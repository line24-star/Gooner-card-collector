"""Ingest a single media file path into the database. Never writes inside the media folder."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..game.media import classify
from ..models import MediaItem, User
from .folders import current_media_dir
from . import market
from .rewards import reward_ingest

CHUNK = 1024 * 1024


@dataclass
class MediaMeta:
    size_bytes: int
    mtime: float
    format: str | None
    width: int | None
    height: int | None


@dataclass
class IngestResult:
    status: Literal["new", "moved", "restored", "unchanged", "duplicate", "skipped"]
    media_id: int | None
    coins: int
    reason: str = ""
    kind: str = ""  # "image" | "video" for a new file (the scan report sums what new files are worth)


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def probe(path: Path, kind: str) -> MediaMeta:
    st = path.stat()
    if kind == "image":
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            return MediaMeta(st.st_size, st.st_mtime, im.format, im.width, im.height)
    return MediaMeta(st.st_size, st.st_mtime, path.suffix.lower().lstrip("."), None, None)


def _rel(path: Path) -> str:
    try:
        return path.relative_to(current_media_dir()).as_posix()
    except ValueError:
        return path.resolve().relative_to(current_media_dir().resolve()).as_posix()


def _skip(reason: str) -> IngestResult:
    return IngestResult("skipped", None, 0, reason)


def _is_case_rename(old_rel: str, new_rel: str) -> bool:
    """A case-only rename (a.png -> A.png) on a case-insensitive filesystem (NTFS, APFS): the old
    spelling still "exists" because it is the same file. Requiring the spellings to differ only in
    case keeps hard links (really two names) classed as duplicates."""
    if old_rel.casefold() != new_rel.casefold():
        return False
    base = current_media_dir()
    try:
        return os.path.samefile(base / old_rel, base / new_rel)
    except OSError:
        return False


def ingest_path(db: Session, user: User, path: Path, *, initial_scan: bool) -> IngestResult:
    kind = classify(path)
    if kind is None:
        return _skip("unsupported extension")
    try:
        rel = _rel(path)
    except ValueError:
        return _skip("outside media folder")
    try:
        st = path.stat()
    except OSError as e:
        return _skip(f"unreadable: {type(e).__name__}")
    if st.st_size == 0:
        return _skip("empty file")

    at_path = db.scalars(select(MediaItem).where(MediaItem.path == rel, MediaItem.missing.is_(False))).all()
    for row in at_path:
        if row.size_bytes == st.st_size and row.mtime == st.st_mtime:
            return IngestResult("unchanged", row.id, 0)

    try:
        digest = hash_file(path)
        meta = probe(path, kind)
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        return _skip(f"unreadable: {type(e).__name__}")

    existing = db.scalar(select(MediaItem).where(MediaItem.sha256 == digest))
    if existing is not None:
        if existing.missing:
            status = "restored"
        elif existing.path == rel:
            existing.size_bytes, existing.mtime = meta.size_bytes, meta.mtime
            db.commit()
            return IngestResult("unchanged", existing.id, 0)
        elif _is_case_rename(existing.path, rel) or not (current_media_dir() / existing.path).exists():
            status = "moved"
        else:
            return IngestResult("duplicate", existing.id, 0)
        for row in at_path:  # retire other present cards occupying the destination path
            if row.id != existing.id:
                row.missing = True
        existing.path = rel
        existing.missing = False
        existing.size_bytes, existing.mtime = meta.size_bytes, meta.mtime
        db.commit()
        return IngestResult(status, existing.id, 0)

    for row in at_path:  # same path, different content: the file was edited
        row.missing = True

    item = MediaItem(
        path=rel,
        sha256=digest,
        kind=kind,
        size_bytes=meta.size_bytes,
        mtime=meta.mtime,
        format=meta.format,
        width=meta.width,
        height=meta.height,
    )
    try:
        market.issue(db, item)  # its print run and popularity, fixed when the card is scanned in
        db.add(item)
        db.flush()
        coins = reward_ingest(db, user, item, initial_scan=initial_scan)
        db.commit()
    except Exception:
        db.rollback()
        raise
    # No thumbnail here: it is made the first time the card is shown (GET /api/media/{id}/thumb).
    # Making one for every file was most of the import's time.
    return IngestResult("new", item.id, coins, kind=kind)


def mark_missing(db: Session, present_paths: set[str]) -> int:
    n = 0
    for row in db.scalars(select(MediaItem).where(MediaItem.missing.is_(False))).all():
        if row.path not in present_paths:
            row.missing = True
            n += 1
    db.commit()
    return n
