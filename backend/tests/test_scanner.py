"""Full scan, folder watcher and scan status."""

import hashlib
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.game.config import get_config
from app.models import AppState, MediaItem
from app.services import scanner
from app.services.scanner import MediaWatcher, full_scan, get_status, wait_until_stable
from app.services.wallet import local_user

cfg = get_config()


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def tmp_media(tmp_path, monkeypatch):
    d = tmp_path / "media"
    d.mkdir()
    monkeypatch.setattr(settings, "media_dir", d)
    return d


def make_png(p: Path, color=(255, 0, 0), *, age: float | None = 60.0):
    """A small PNG. By default its mtime is backdated so scans treat it as a settled library file."""
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 20), color).save(p)
    if age is not None:
        t = time.time() - age
        os.utime(p, (t, t))
    return p


def make_pngs(root: Path, n: int, start: int = 0):
    # distinct colors -> distinct hashes
    return [make_png(root / f"sub{i % 3}" / f"img{i}.png", (i % 256, (i * 7) % 256, (i * 13) % 256)) for i in range(start, start + n)]


def coins(db):
    db.expire_all()
    return local_user(db).coins


def poll(fn, timeout=10.0, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(step)
    return False


def end_first_import(db=None):
    """Let the free first import's quiet period lapse, so the next new files pay."""
    with SessionLocal() as s:
        s.merge(AppState(key=scanner.INITIAL_UNTIL_KEY, value=str(time.time() - 1)))
        s.commit()


def count_rows(missing=None):
    with SessionLocal() as s:
        q = select(MediaItem)
        if missing is not None:
            q = q.where(MediaItem.missing.is_(missing))
        return len(s.scalars(q).all())


def test_full_scan_initial_pays_nothing(tmp_media, db):
    make_pngs(tmp_media, 50)
    (tmp_media / "notes.txt").write_text("ignored")
    report = full_scan(tmp_media)
    assert report["new"] == 50
    assert set(report) == {"new", "moved", "restored", "unchanged", "duplicate", "skipped", "missing"}
    assert coins(db) == cfg.starting_coins
    assert db.get(AppState, "initial_scan_done").value == "1"


def test_empty_first_scan_keeps_initial_import_free(tmp_media, db):
    full_scan(tmp_media)  # app started with an empty folder
    assert db.get(AppState, "initial_scan_done") is None
    make_pngs(tmp_media, 5)  # library copied in while the app was off
    assert full_scan(tmp_media)["new"] == 5
    assert coins(db) == cfg.starting_coins
    db.expire_all()
    assert db.get(AppState, "initial_scan_done").value == "1"


def test_second_scan_unchanged_and_pays_new(tmp_media, db):
    make_pngs(tmp_media, 50)
    full_scan(tmp_media)
    end_first_import()
    report = full_scan(tmp_media)
    assert report["unchanged"] == 50 and report["new"] == 0
    make_png(tmp_media / "fresh.png", (1, 2, 3))
    report = full_scan(tmp_media)
    assert report["new"] == 1 and report["unchanged"] == 50
    assert coins(db) == cfg.starting_coins + cfg.ingest.image


def test_full_scan_marks_deleted_missing(tmp_media, db):
    files = make_pngs(tmp_media, 5)
    full_scan(tmp_media)
    files[0].unlink()
    report = full_scan(tmp_media)
    assert report["missing"] == 1 and report["unchanged"] == 4
    assert count_rows(missing=True) == 1


def test_full_scan_survives_per_file_exception(tmp_media, monkeypatch):
    files = make_pngs(tmp_media, 5)
    bad = files[2]
    real = scanner.ingest_path

    def flaky(db, user, path, *, initial_scan):
        if Path(path) == bad:
            raise EOFError("truncated")
        return real(db, user, path, initial_scan=initial_scan)

    monkeypatch.setattr(scanner, "ingest_path", flaky)
    report = full_scan(tmp_media)
    assert report["new"] == 4 and report["skipped"] == 1
    last = get_status().last_report  # v0.8.0: the counts plus new_media, new_slots and slots
    assert get_status().state == "idle" and {k: last[k] for k in report} == report


def test_interrupted_first_scan_stays_initial(tmp_media, db, monkeypatch):
    make_pngs(tmp_media, 10)
    stop = threading.Event()
    real = scanner.ingest_path
    calls = []

    def stopping(db, user, path, *, initial_scan):
        calls.append(initial_scan)
        r = real(db, user, path, initial_scan=initial_scan)
        if len(calls) == 3:
            stop.set()
        return r

    monkeypatch.setattr(scanner, "ingest_path", stopping)
    full_scan(tmp_media, stop=stop)
    assert len(calls) == 3
    assert db.get(AppState, "initial_scan_done") is None  # next scan is still the free initial import
    monkeypatch.setattr(scanner, "ingest_path", real)
    report = full_scan(tmp_media)
    assert report["new"] == 7 and coins(db) == cfg.starting_coins


def test_wait_until_stable_waits_for_growth():
    reads = []
    sizes = iter([10, 20, 20, 20])

    def stat(_path):
        s = next(sizes)
        reads.append(s)
        return SimpleNamespace(st_size=s, st_mtime=1.0)

    assert wait_until_stable(Path("x.png"), interval=0, checks=2, stat=stat) is True
    assert reads == [10, 20, 20]  # returned as soon as two consecutive reads matched


def test_wait_until_stable_false_when_deleted():
    def stat(_path):
        raise FileNotFoundError

    assert wait_until_stable(Path("gone.png"), interval=0, stat=stat) is False


def test_wait_until_stable_stops_on_stop_event():
    stop = threading.Event()
    stop.set()
    growing = iter(range(1000))
    stat = lambda _p: SimpleNamespace(st_size=next(growing), st_mtime=1.0)
    assert wait_until_stable(Path("x.png"), interval=10, stat=stat, stop=stop) is False


def test_watcher_ingests_new_file(tmp_media):
    w = MediaWatcher(tmp_media)
    w.start()
    try:
        time.sleep(0.2)
        make_png(tmp_media / "new" / "w.png", (9, 9, 9))
        assert poll(lambda: count_rows() == 1, timeout=10)
    finally:
        t = time.monotonic()
        w.stop()
        assert time.monotonic() - t < 3


def test_watcher_marks_deleted_missing_and_survives_errors(tmp_media, monkeypatch):
    keep = make_png(tmp_media / "keep.png", (1, 1, 1))
    gone = make_png(tmp_media / "gone.png", (2, 2, 2))
    full_scan(tmp_media)
    end_first_import()
    real = scanner.ingest_path

    def flaky(db, user, path, *, initial_scan):
        assert initial_scan is False
        if Path(path).name == "bad.png":
            raise TypeError("pillow oddity")
        return real(db, user, path, initial_scan=initial_scan)

    monkeypatch.setattr(scanner, "ingest_path", flaky)
    w = MediaWatcher(tmp_media, debounce=0.3, stable_interval=0.1)
    w.start()
    try:
        time.sleep(0.2)
        make_png(tmp_media / "bad.png", (3, 3, 3))  # raises inside ingest; the batch must carry on
        gone.unlink()
        make_png(tmp_media / "later.png", (4, 4, 4))
        assert poll(lambda: count_rows(missing=True) == 1 and count_rows() == 3, timeout=8)
        assert w.worker_alive()
    finally:
        w.stop()
    with SessionLocal() as s:
        paths = {r.path: r.missing for r in s.scalars(select(MediaItem))}
    assert paths == {"keep.png": False, "gone.png": True, "later.png": False}
    assert keep.exists()


def test_status_reports_progress(tmp_media, monkeypatch):
    make_pngs(tmp_media, 5)
    seen = []
    real = scanner.ingest_path

    def spy(db, user, path, *, initial_scan):
        seen.append(get_status())
        return real(db, user, path, initial_scan=initial_scan)

    monkeypatch.setattr(scanner, "ingest_path", spy)
    t = threading.Thread(target=full_scan, args=(tmp_media,))
    t.start()
    t.join(10)
    assert any(s.state == "scanning" and s.total == 5 for s in seen)
    assert [s.done for s in seen] == [0, 1, 2, 3, 4]
    st = get_status()
    assert st.state == "idle" and st.done == 5 and st.last_report["new"] == 5


def test_stop_background_is_prompt_mid_scan(tmp_media, monkeypatch):
    make_pngs(tmp_media, 30)
    real = scanner.ingest_path

    def slow(db, user, path, *, initial_scan):
        time.sleep(0.2)
        return real(db, user, path, initial_scan=initial_scan)

    monkeypatch.setattr(scanner, "ingest_path", slow)
    scanner.start_background(tmp_media)
    assert poll(lambda: get_status().state == "scanning" and get_status().done >= 1, timeout=5)
    t = time.monotonic()
    scanner.stop_background()
    assert time.monotonic() - t < 2
    assert get_status().state == "idle"
    assert count_rows() < 30


# ---------------------------------------------------------------- review fix round 1


def test_full_scan_settles_file_changed_during_scan(tmp_media, db, monkeypatch):
    make_pngs(tmp_media, 3)
    late = tmp_media / "sub2" / "img2.png"  # last in scan order
    monkeypatch.setattr(scanner, "SETTLE_INTERVAL", 0.05)
    real = scanner.ingest_path
    calls = []

    def spy(db, user, path, *, initial_scan):
        calls.append((Path(path).name, initial_scan))
        if len(calls) == 1:  # while the first file ingests, the copy of `late` is still being written
            make_png(late, (250, 250, 250), age=30)
        return real(db, user, path, initial_scan=initial_scan)

    monkeypatch.setattr(scanner, "ingest_path", spy)
    report = full_scan(tmp_media)
    assert [n for n, _ in calls].count("img2.png") == 1 and calls[-1][0] == "img2.png"
    assert all(flag for _, flag in calls)  # settled files still belong to the free initial import
    assert report["new"] == 3 and report["skipped"] == 0
    with SessionLocal() as s:
        row = s.scalars(select(MediaItem).where(MediaItem.path == "sub2/img2.png")).one()
        assert row.sha256 == hashlib.sha256(late.read_bytes()).hexdigest()  # the final content
        assert len(s.scalars(select(MediaItem)).all()) == 3
    assert count_rows(missing=True) == 0
    assert coins(db) == cfg.starting_coins
    assert full_scan(tmp_media)["unchanged"] == 3


def test_full_scan_defers_recently_modified_file(tmp_media, db, monkeypatch):
    make_png(tmp_media / "old.png", (1, 1, 1))
    make_png(tmp_media / "a_fresh.png", (2, 2, 2), age=None)  # mtime is "now": may still be copying
    monkeypatch.setattr(scanner, "SETTLE_INTERVAL", 0.05)
    real = scanner.ingest_path
    order = []

    def spy(db, user, path, *, initial_scan):
        order.append(Path(path).name)
        return real(db, user, path, initial_scan=initial_scan)

    monkeypatch.setattr(scanner, "ingest_path", spy)
    report = full_scan(tmp_media)
    assert order == ["old.png", "a_fresh.png"]  # sorted first, but ingested only after settling
    assert report["new"] == 2 and count_rows() == 2
    assert coins(db) == cfg.starting_coins


def test_full_scan_never_ingests_file_that_keeps_changing(tmp_media, monkeypatch):
    make_png(tmp_media / "ok.png", (1, 1, 1))
    growing = make_png(tmp_media / "growing.png", (2, 2, 2))
    monkeypatch.setattr(scanner, "SETTLE_INTERVAL", 0.01)
    monkeypatch.setattr(scanner, "MAX_SETTLE_ROUNDS", 3)
    real_sig = scanner._signature
    n = iter(range(10_000))

    def sig(path, stat=os.stat):
        return (next(n), 1.0) if Path(path) == growing else real_sig(path, stat)

    monkeypatch.setattr(scanner, "_signature", sig)
    report = full_scan(tmp_media)
    assert report["new"] == 1 and report["skipped"] == 1 and report["missing"] == 0
    with SessionLocal() as s:
        assert [r.path for r in s.scalars(select(MediaItem))] == ["ok.png"]


def test_watcher_requeues_file_that_appears_between_reads(tmp_media, monkeypatch):
    p = make_png(tmp_media / "late.png", (5, 5, 5))
    real_sig = scanner._signature
    seen = []

    def sig(path, stat=os.stat):
        seen.append(path)
        return None if len(seen) == 1 else real_sig(path, stat)  # first read: not there yet

    monkeypatch.setattr(scanner, "_signature", sig)
    w = MediaWatcher(tmp_media, stable_interval=0)
    w._process({p}, False)
    assert w._pending == {p} and count_rows() == 0


def test_start_background_twice_keeps_one_watcher(tmp_media, monkeypatch):
    monkeypatch.setattr(scanner.settings, "watch_media", True)  # the watcher is opt-in since v0.3.0
    scanner.start_background(tmp_media)
    try:
        first = scanner._bg_watcher
        assert poll(lambda: not scanner._bg_thread.is_alive(), timeout=5)
        scanner.start_background(tmp_media)
        assert scanner._bg_watcher is first
    finally:
        scanner.stop_background()
    assert not first.worker_alive() and scanner._bg_watcher is None


# ---------------------------------------------------------------- media_version + first import via the watcher


def test_watcher_bumps_media_version(tmp_media):
    w = MediaWatcher(tmp_media, stable_interval=0)
    v0 = get_status().media_version
    p = make_png(tmp_media / "w.png", (9, 9, 9))
    w._process({p}, False)
    v1 = get_status().media_version
    assert v1 > v0
    w._process({p}, False)  # unchanged file: nothing to reload
    assert get_status().media_version == v1
    p.unlink()
    w._process(set(), True)  # marked missing
    assert get_status().media_version > v1


def test_full_scan_bumps_media_version_only_on_changes(tmp_media):
    make_pngs(tmp_media, 2)
    v0 = get_status().media_version
    full_scan(tmp_media)
    v1 = get_status().media_version
    assert v1 > v0
    full_scan(tmp_media)
    assert get_status().media_version == v1


def test_watcher_first_batch_on_never_scanned_db_is_free(tmp_media, db):
    full_scan(tmp_media)  # the app started with an empty folder
    assert db.get(AppState, "initial_scan_done") is None
    w = MediaWatcher(tmp_media, stable_interval=0)
    w._process(set(make_pngs(tmp_media, 5)), False)  # the user drops their library in
    assert count_rows() == 5
    assert coins(db) == cfg.starting_coins  # the first import is free
    db.expire_all()
    assert db.get(AppState, "initial_scan_done").value == "1"
    end_first_import()  # quiet period over
    later = make_png(tmp_media / "later.png", (200, 100, 50))
    w._process({later}, False)
    assert coins(db) > cfg.starting_coins  # files added after that pay normally


# ---------------------------------------------------------------- case-only rename (case_demo.py)


def test_case_only_rename_moves_the_card(tmp_media, db):
    """On NTFS the old spelling still 'exists' after a.png -> A.png; the card must move, not hide."""
    make_png(tmp_media / "a.png")
    full_scan(tmp_media)
    os.rename(tmp_media / "a.png", tmp_media / "A.png")
    for _ in range(2):
        report = full_scan(tmp_media)
        assert report["duplicate"] == 0 and report["missing"] == 0
        with SessionLocal() as s:
            row = s.scalars(select(MediaItem)).one()
            assert (row.path, row.missing) == ("A.png", False)
