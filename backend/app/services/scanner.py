"""Full media-folder scan, live folder watcher, and a thread-safe scan-status snapshot.

Threads: the startup scan runs in its own thread; the watcher has a watchdog Observer thread plus one
worker thread. Each opens its own SessionLocal() and closes it. All ingest work (scan or watcher)
runs under the module-level ``_lock`` so the two never write concurrently. Per-file exceptions are
logged and counted as skipped: one bad file never aborts a scan or kills the watcher (ruling R6).
Never writes inside the media folder.
"""

import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..config import settings
from ..db import SessionLocal
from ..game import economy as eco
from ..game.config import get_config
from ..game.media import classify
from ..models import AppState, CollectionEntry, MediaItem
from .folders import current_media_dir
from .ingest import ingest_path, mark_missing
from .ingest import hash_file
from .rewards import ingest_day_status, ingest_today, slot_holdings, slots, slots_change
from .wallet import local_user

log = logging.getLogger("mco.scanner")

INITIAL_SCAN_KEY = "initial_scan_done"
INITIAL_UNTIL_KEY = "initial_import_until"  # epoch seconds; the free first import stays open until then
REPORT_KEYS = ("new", "moved", "restored", "unchanged", "duplicate", "skipped", "missing")
PREVIEW_HASH_BYTES = 64 * 1024 * 1024  # the preview hashes a possible copy only up to this size
RECENT_SECONDS = 3.0  # a file modified this recently may still be copying
SETTLE_INTERVAL = 1.0  # seconds between stability re-checks of unsettled files during a scan
MAX_SETTLE_ROUNDS = 600  # ~10 min; after that the watcher takes over

_lock = threading.Lock()  # serializes all scanner/watcher ingest work


# ---------------------------------------------------------------- the free first import


def first_import_pending(db: Session) -> bool:
    """True until the first import of this folder's library has completed: that scan is free.

    v0.9.0 (test round 6, point 3): a scan the player asks for is the free first import only while
    no import has completed. Before, every scan inside the quiet window of
    ``ingest.initial_import_quiet_seconds`` was free too AND opened the window again, so files
    added a few minutes after choosing the folder paid nothing, scan after scan, for as long as
    the scans came less than five minutes apart. The window is for the folder watcher only
    (``initial_import_open``), where one big copy arrives as many batches. A later scan is paid
    like any other: the daily limits bound it (150 coins a day), which is what the window was for."""
    return db.get(AppState, INITIAL_SCAN_KEY) is None


def initial_import_open(db: Session) -> bool:
    """For the folder watcher: True while new files belong to the free first import: it hasn't
    happened yet, or its last free batch came less than ``ingest.initial_import_quiet_seconds``
    before (a big copy arrives in many batches)."""
    if db.get(AppState, INITIAL_SCAN_KEY) is None:
        return True
    until = db.get(AppState, INITIAL_UNTIL_KEY)
    try:
        return until is not None and time.time() < float(until.value)
    except ValueError:
        return False


def _mark_initial_import(db: Session) -> None:
    """Record that free files were imported just now, keeping the window open a while longer."""
    until = time.time() + get_config().ingest.initial_import_quiet_seconds
    db.merge(AppState(key=INITIAL_SCAN_KEY, value="1"))
    db.merge(AppState(key=INITIAL_UNTIL_KEY, value=f"{until:.0f}"))
    db.commit()


def reset_initial_import(db: Session) -> None:
    """A newly chosen media folder gets its own free first import (the caller commits)."""
    for key in (INITIAL_SCAN_KEY, INITIAL_UNTIL_KEY):
        row = db.get(AppState, key)
        if row is not None:
            db.delete(row)


# ---------------------------------------------------------------- status


@dataclass
class ScanStatus:
    state: Literal["idle", "scanning"]
    done: int
    total: int
    # The last finished scan: how many files were new, moved, restored, unchanged, duplicate,
    # skipped and missing. v0.8.0 adds `new_media` (= new), `new_slots` (version slots the new
    # media added: 3 each) and `slots` (the versions held after the scan, as in every action's reply;
    # `slots.total_delta` is how much the total moved, files gone missing included).
    # v0.9.0 adds what the scan paid: `coins` (paid for the new files), `coins_full` (what they
    # are worth at the full rate: 15 an image, 25 a video), `coins_reason` (why it paid less than
    # that: "first_import" = the free first import of a library, "daily_limit" = the day's steps
    # and cap; null when it paid in full or nothing was new) and `ingest_day` (today's scan
    # payouts: paid, cap, files, full_files, half_files).
    last_report: dict | None
    # Bumped whenever a scan or the watcher creates, moves, restores or marks missing any media.
    # Monotonic within one server run; the UI reloads its cards when it changes between polls.
    media_version: int = 0


_status = ScanStatus("idle", 0, 0, None)
CHANGE_STATUSES = frozenset({"new", "moved", "restored"})
_status_lock = threading.Lock()


def _set_status(**changes) -> None:
    global _status
    with _status_lock:
        _status = replace(_status, **changes)


def _bump_media_version() -> None:
    global _status
    with _status_lock:
        _status = replace(_status, media_version=_status.media_version + 1)


def _record(status: str) -> None:
    if status in CHANGE_STATUSES:
        _bump_media_version()


def get_status() -> ScanStatus:
    """A copy of the current status, safe to read from any thread."""
    with _status_lock:
        report = dict(_status.last_report) if _status.last_report is not None else None
        return replace(_status, last_report=report)


# ---------------------------------------------------------------- full scan


def _iter_media(media_dir: Path) -> Iterator[Path]:
    """Supported files under media_dir, in a stable order. Unreadable folders are logged and skipped."""

    def onerror(e: OSError) -> None:
        log.warning("Cannot read folder %s: %s", e.filename, e)

    for dirpath, dirnames, filenames in os.walk(media_dir, onerror=onerror):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if classify(p) is not None:
                yield p


def _present_paths(media_dir: Path, files: Iterable[Path] | None = None) -> set[str]:
    return {p.relative_to(media_dir).as_posix() for p in (files if files is not None else _iter_media(media_dir))}


def full_scan(media_dir: Path, *, stop: threading.Event | None = None, expect_current: bool = False) -> dict:
    """Ingest every supported file under media_dir, then mark vanished ones missing.

    The first scan that completes with at least one file is the free initial import
    (``initial_scan=True``). Every later scan pays for the files it adds (v0.9.0: before, a scan
    inside the first import's quiet window was free as well, see ``first_import_pending``).
    If ``stop`` is set the scan ends after the current file, without marking
    missing files or recording that the initial import finished (the next start simply scans again).

    A file that may still be being written (modified less than ``RECENT_SECONDS`` ago, or whose
    size/mtime changed since the walk) is not ingested in the main pass. It is settled at the end of the
    scan instead: re-checked every ``SETTLE_INTERVAL`` seconds and ingested (with this scan's
    ``initial_scan``) once two reads match. A file still changing after ``MAX_SETTLE_ROUNDS`` is
    skipped; the watcher picks it up when it finally settles.

    ``expect_current``: the scan belongs to the app's live media folder. If the folder is switched
    while it runs it stops like a stopped scan (no more ingest, no marking missing), so a stale scan
    of the old folder can never touch cards after a switch.
    """
    report = dict.fromkeys(REPORT_KEYS, 0)

    def stopped() -> bool:
        if stop is not None and stop.is_set():
            return True
        return expect_current and current_media_dir() != media_dir

    with _lock:
        if stopped():
            return report
        files = [(p, _signature(p)) for p in _iter_media(media_dir)]
        _set_status(state="scanning", done=0, total=len(files))
        completed = False
        db = SessionLocal()
        result: dict | None = None
        try:
            initial = first_import_pending(db)
            user = local_user(db)
            held_before = slot_holdings(db, user)
            total_before = slots(db, user)[1]
            ing = get_config().ingest
            paid = {"coins": 0, "full": 0}  # what the new files paid, and what they are worth at the full rate

            def ingest(path: Path) -> None:
                try:
                    r = ingest_path(db, user, path, initial_scan=initial)
                    status = r.status
                    report[status] += 1
                    if status == "new":
                        paid["coins"] += r.coins
                        paid["full"] += ing.video if r.kind == "video" else ing.image
                    _record(status)
                except Exception:
                    log.warning("Skipped %s: ingest failed", path, exc_info=True)
                    db.rollback()
                    report["skipped"] += 1

            unsettled: dict[Path, tuple[int, float] | None] = {}
            for i, (path, walked) in enumerate(files):
                if stopped():
                    log.info("Scan stopped after %d of %d files", i, len(files))
                    break
                sig = _signature(path)
                if sig is not None and (sig != walked or time.time() - sig[1] < RECENT_SECONDS):
                    unsettled[path] = sig  # may still be copying: settle it after the main pass
                else:
                    ingest(path)
                _set_status(done=i + 1)
            else:
                rounds = 0
                while unsettled and rounds < MAX_SETTLE_ROUNDS and not stopped():
                    rounds += 1
                    if stop is not None:
                        stop.wait(SETTLE_INTERVAL)
                    else:
                        time.sleep(SETTLE_INTERVAL)
                    if stopped():
                        break
                    for path, prev in list(unsettled.items()):
                        sig = _signature(path)
                        if sig is None:
                            del unsettled[path]  # vanished; it simply isn't present
                        elif sig == prev:
                            del unsettled[path]
                            ingest(path)
                        else:
                            unsettled[path] = sig
                completed = not stopped()
                for path in unsettled if completed else ():
                    log.warning("Skipped %s: still changing after %d checks", path, rounds)
                    report["skipped"] += 1
            if completed:
                report["missing"] = mark_missing(db, _present_paths(media_dir, [p for p, _ in files]))
                if report["missing"]:
                    _bump_media_version()
                if initial and files:  # an empty folder doesn't use up the free initial import
                    _mark_initial_import(db)
                kinds = len(eco.slot_treatments(get_config()))
                reason = None
                if paid["coins"] < paid["full"]:
                    reason = "first_import" if initial and not ing.reward_initial_scan else "daily_limit"
                result = {**report, "new_media": report["new"], "new_slots": report["new"] * kinds,
                          "slots": slots_change(db, user, held_before, total_before),
                          "coins": paid["coins"], "coins_full": paid["full"], "coins_reason": reason,
                          "ingest_day": ingest_day_status(db, user)}
                log.info("Scan paid %d of %d coins (%s)", paid["coins"], paid["full"], reason or "in full")
        finally:
            db.close()
            if completed:
                _set_status(state="idle", last_report=result if result is not None else dict(report))
            else:
                _set_status(state="idle")
    if completed:
        log.info("Scan finished: %s", report)
    return report


# ---------------------------------------------------------------- preview (v0.8.0)


def preview(media_dir: Path) -> dict:
    """What a scan of `media_dir` would add, without adding anything: for the summary shown before
    a scan ("Adding 86 media will add 258 version slots. Versions: 12% -> 9%").

    Fast on a large folder: it lists the folder and compares paths with the cards on record. No
    file is opened or hashed, with one exception (v0.9.0): only files at a path without a card
    are looked at (size and time), to tell a moved or restored file (it has the size and time of a
    card whose file is not where it was) from a new one; and such a file that is exactly as big
    as a card on record is hashed (up to ``PREVIEW_HASH_BYTES``), because it may be a second copy
    of that card's file. A second copy adds no card and pays nothing, so it is counted as
    `duplicate_media`, not as new. (Before, the preview promised such copies as new media on every
    scan: 26 of them in the player's library.) A bigger copy is still counted as new.

    new_media: files that would become new cards. duplicate_media: identical copies of files that
    are cards already (skipped by the scan). coins: what the scan would pay for the new files
    today, coins_full / coins_reason as in the scan report. restored_media: missing cards whose file is
    back. missing_media: cards whose file is gone (the scan marks them missing).
    new_slots: version slots the new media add (3 each). slots_filled / slots_total and
    completeness_now: as they are. slots_filled_after / slots_total_after and completeness_after:
    as they would be after the scan."""
    cfg = get_config()
    kinds = eco.slot_treatments(cfg)
    on_disk = _present_paths(media_dir)
    db = SessionLocal()
    try:
        user = local_user(db)
        rows = db.execute(select(MediaItem.id, MediaItem.path, MediaItem.missing, MediaItem.size_bytes, MediaItem.mtime)).all()
        present = {r.path: r for r in rows if not r.missing}
        gone = {r.id for p, r in present.items() if p not in on_disk}
        # cards whose file is not where it was, by size and time: a file at a new path that matches
        # one is that card (moved, or restored), not a new one
        waiting: dict[tuple[int, float], list[int]] = {}
        for r in rows:
            if r.missing or r.id in gone:
                waiting.setdefault((r.size_bytes, r.mtime), []).append(r.id)
        was_missing = {r.id for r in rows if r.missing}
        sizes = {r.size_bytes for r in rows if not r.missing and r.id not in gone}
        new = duplicates = new_videos = 0
        restored: set[int] = set()
        for rel in sorted(on_disk - set(present)):
            sig = _signature(media_dir / rel)
            if sig is None or sig[0] == 0:
                continue  # vanished meanwhile, or empty: the scan skips it
            match = waiting.get(sig)
            if match:
                media_id = match.pop()
                if media_id in was_missing:
                    restored.add(media_id)
                else:
                    gone.discard(media_id)  # moved: the card stays
            elif sig[0] in sizes and sig[0] <= PREVIEW_HASH_BYTES and _is_known_copy(db, media_dir / rel):
                duplicates += 1  # an identical copy of a card's file: the scan skips it
            else:
                new += 1
                new_videos += classify(media_dir / rel) == "video"
        # what the scan would pay for the new files, by today's limits
        initial = first_import_pending(db) and not cfg.ingest.reward_initial_scan
        day_paid, day_files = ingest_today(db, user)
        coins = 0
        full = new_videos * cfg.ingest.video + (new - new_videos) * cfg.ingest.image
        if not initial:
            for n in range(new):  # videos first or last makes a few coins' difference: an estimate
                coins += eco.ingest_reward(cfg, "video" if n < new_videos else "image", False, day_paid + coins, day_files + n)
        filled, total = slots(db, user)
        changed = gone | restored
        held: dict[int, int] = {}
        if changed:
            for (media_id,) in db.execute(
                select(CollectionEntry.media_id).where(
                    CollectionEntry.user_id == user.id, CollectionEntry.copies > 0,
                    CollectionEntry.treatment.in_(kinds), CollectionEntry.media_id.in_(changed))
            ):
                held[media_id] = held.get(media_id, 0) + 1
        filled_after = filled - sum(held.get(i, 0) for i in gone) + sum(held.get(i, 0) for i in restored)
        total_after = eco.slots_total(cfg, len(present) - len(gone) + len(restored) + new)
        return {
            "new_media": new,
            "duplicate_media": duplicates,
            "coins": coins,
            "coins_full": full,
            "coins_reason": None if coins >= full else ("first_import" if initial else "daily_limit"),
            "restored_media": len(restored),
            "missing_media": len(gone),
            "new_slots": new * len(kinds),
            "slots_filled": filled,
            "slots_total": total,
            "slots_filled_after": filled_after,
            "slots_total_after": total_after,
            "completeness_now": eco.slots_completeness(filled, total),
            "completeness_after": eco.slots_completeness(filled_after, total_after),
        }
    finally:
        db.close()


def _is_known_copy(db: Session, path: Path) -> bool:
    """Is this file's content already a card that is present? (The scan calls it a duplicate.)"""
    try:
        digest = hash_file(path)
    except OSError:
        return False
    return db.scalar(select(MediaItem.id).where(MediaItem.sha256 == digest, MediaItem.missing.is_(False))) is not None


# ---------------------------------------------------------------- stability


def _signature(path: Path, stat: Callable = os.stat) -> tuple[int, float] | None:
    try:
        st = stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime)


def wait_until_stable(
    path: Path,
    *,
    interval: float = 1.0,
    checks: int = 2,
    stat=os.stat,
    stop: threading.Event | None = None,
) -> bool:
    """True once size and mtime match across ``checks`` consecutive reads; False if the file
    disappears (or ``stop`` is set while waiting)."""
    last = _signature(path, stat)
    if last is None:
        return False
    streak = 1
    while streak < checks:
        if stop is not None:
            if stop.wait(interval):
                return False
        elif interval > 0:
            time.sleep(interval)
        cur = _signature(path, stat)
        if cur is None:
            return False
        streak = streak + 1 if cur == last else 1
        last = cur
    return True


# ---------------------------------------------------------------- watcher


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "MediaWatcher"):
        self.watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        try:
            self.watcher._on_event(event)
        except Exception:
            log.warning("Watcher event handling failed for %r", event, exc_info=True)


class MediaWatcher:
    """Watches media_dir recursively and ingests new/changed files after they settle.

    Created/modified/moved-to paths are queued; once events have been quiet for ``debounce`` seconds
    the worker re-checks the whole batch after ``stable_interval`` (a file whose size or mtime still
    changes is re-queued), ingests the stable ones, and, if anything was deleted or moved away, marks
    rows missing against the current on-disk set.

    While the free first import is open (``initial_import_open``: it hasn't happened yet, e.g. the
    app started with an empty folder, or the last free batch was less than
    ``ingest.initial_import_quiet_seconds`` ago), a batch is ingested with ``initial_scan=True`` and
    keeps the window open; files after a quiet period pay normally. Every change bumps media_version.
    """

    def __init__(self, media_dir: Path, *, debounce: float = 2.0, stable_interval: float = 1.0):
        self.media_dir = Path(media_dir)
        self.debounce = debounce
        self.stable_interval = stable_interval
        self._cond = threading.Condition()
        self._pending: set[Path] = set()
        self._check_missing = False
        self._last_event = 0.0
        self._stop = threading.Event()
        self._observer = None
        self._worker: threading.Thread | None = None

    # -- lifecycle
    def start(self) -> None:
        self._stop.clear()
        observer = Observer()
        observer.schedule(_Handler(self), str(self.media_dir), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        self._worker = threading.Thread(target=self._run, name="mco-watcher-worker", daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 1.5) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        deadline = time.monotonic() + timeout
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(max(0.0, deadline - time.monotonic()))
            except Exception:
                log.warning("Stopping the folder observer failed", exc_info=True)
        if self._worker is not None:
            self._worker.join(max(0.0, deadline - time.monotonic()))

    def worker_alive(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    # -- events (observer thread)
    def _queue(self, path: Path | None = None, *, missing: bool = False) -> None:
        with self._cond:
            if path is not None:
                self._pending.add(path)
            self._check_missing = self._check_missing or missing
            self._last_event = time.monotonic()
            self._cond.notify_all()

    def _on_event(self, event: FileSystemEvent) -> None:
        kind = event.event_type
        if kind not in ("created", "modified", "deleted", "moved"):
            return  # opened / closed / closed_no_write
        src = Path(os.fsdecode(event.src_path))
        dest = Path(os.fsdecode(event.dest_path)) if kind == "moved" and event.dest_path else None
        if event.is_directory:
            if kind in ("deleted", "moved"):
                self._queue(missing=True)
            if kind == "created":
                self._queue(src)  # a folder moved in from elsewhere: the worker expands it
            if dest is not None:
                self._queue(dest)
            return
        if kind in ("created", "modified"):
            if classify(src) is not None:
                self._queue(src)
        elif kind == "deleted":
            if classify(src) is not None:
                self._queue(missing=True)
        else:  # moved
            if classify(src) is not None:
                self._queue(missing=True)
            if dest is not None and classify(dest) is not None:
                self._queue(dest)

    # -- worker thread
    def _next_batch(self) -> tuple[set[Path], bool] | None:
        with self._cond:
            while not (self._pending or self._check_missing):
                if self._stop.is_set():
                    return None
                self._cond.wait(0.5)
            while True:  # debounce: wait for a quiet period
                if self._stop.is_set():
                    return None
                remaining = self._last_event + self.debounce - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            batch, missing = self._pending, self._check_missing
            self._pending, self._check_missing = set(), False
            return batch, missing

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                batch = self._next_batch()
                if batch is None:
                    return
                self._process(*batch)
            except Exception:
                log.error("Watcher batch failed", exc_info=True)
                self._stop.wait(1.0)  # don't spin on a persistent failure

    def _expand(self, paths: set[Path]) -> set[Path]:
        out: set[Path] = set()
        for p in paths:
            if p.is_dir():
                out.update(_iter_media(p))
            elif classify(p) is not None:
                out.add(p)
        return out

    def _process(self, paths: set[Path], check_missing: bool) -> None:
        files = self._expand(paths)
        first = {p: _signature(p) for p in files}
        if files and self._stop.wait(self.stable_interval):
            return
        stable, changing = [], set()
        for p in sorted(files):
            sig = _signature(p)
            if sig is None:
                check_missing = check_missing or first[p] is not None
            elif first[p] is None:
                changing.add(p)  # appeared after the first read: give it another quiet period
            elif sig == first[p]:
                stable.append(p)
            else:
                changing.add(p)
        for p in changing:  # still being written: try again after the next quiet period
            self._queue(p)
        if not stable and not check_missing:
            return
        while not _lock.acquire(timeout=0.2):
            if self._stop.is_set():
                return
        try:
            db = SessionLocal()
            try:
                user = local_user(db)
                initial = initial_import_open(db)
                ingested = False
                try:
                    for p in stable:
                        if self._stop.is_set():
                            return
                        try:
                            r = ingest_path(db, user, p, initial_scan=initial)
                            log.info("Watcher: %s -> %s %s", p, r.status, r.reason)
                            _record(r.status)
                            ingested = ingested or r.status != "skipped"
                        except Exception:
                            log.warning("Watcher skipped %s: ingest failed", p, exc_info=True)
                            db.rollback()
                finally:
                    if initial and ingested:  # part of the free first import: keep its window open
                        _mark_initial_import(db)
                        log.info("Watcher: free first-import batch")
                if check_missing and not self._stop.is_set():
                    n = mark_missing(db, _present_paths(self.media_dir))
                    if n:
                        _bump_media_version()
                        log.info("Watcher: marked %d item(s) missing", n)
            finally:
                db.close()
        finally:
            _lock.release()


# ---------------------------------------------------------------- app lifecycle

_bg_stop = threading.Event()  # replaced per run: a stopped run's event is never cleared or reused
_bg_thread: threading.Thread | None = None
_bg_watcher: MediaWatcher | None = None


def start_background(media_dir: Path) -> bool:
    """Run a full scan of the folder in a thread. No-op if already started; call stop_background first.

    v0.3.0: scanning is something the player asks for (choosing a folder, or the Scan button), so
    by default nothing watches the folder afterwards. Only with ``settings.watch_media`` (off by
    default, ``MCO_WATCH_MEDIA=true``) the folder watcher is started too; then this returns False
    if the watcher could not start (the scan still runs)."""
    global _bg_thread, _bg_watcher, _bg_stop
    if (_bg_thread is not None and _bg_thread.is_alive()) or _bg_watcher is not None:
        return True
    stop = _bg_stop = threading.Event()
    watcher_ok = True
    if settings.watch_media:
        watcher = MediaWatcher(media_dir)
        try:
            watcher.start()
            _bg_watcher = watcher
        except Exception:
            log.error("Could not start the folder watcher for %s", media_dir, exc_info=True)
            _bg_watcher = None
            watcher_ok = False

    def run() -> None:
        try:
            full_scan(media_dir, stop=stop, expect_current=True)
        except Exception:
            log.error("Startup scan failed", exc_info=True)

    _bg_thread = threading.Thread(target=run, name="mco-startup-scan", daemon=True)
    _bg_thread.start()
    return watcher_ok


def stop_background(timeout: float = 3.0) -> None:
    """Stop the scan (after its current file) and the watcher. Returns within about ``timeout``."""
    global _bg_thread, _bg_watcher
    deadline = time.monotonic() + timeout
    _bg_stop.set()
    if _bg_watcher is not None:
        _bg_watcher.stop(timeout=min(1.5, timeout / 2))
        _bg_watcher = None
    if _bg_thread is not None:
        _bg_thread.join(max(0.0, deadline - time.monotonic()))
        if _bg_thread.is_alive():
            log.warning("Startup scan still finishing its current file; leaving it to exit with the app")
        _bg_thread = None
