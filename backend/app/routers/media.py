"""Media API: card list/detail, thumbnails, original files, and folder-scan control."""

import logging
import threading
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..game.config import get_config
from ..models import CollectionEntry, MediaItem, Tag, media_tags
from ..services import cards, folders, organize, scanner
from ..services.thumbs import ensure_thumb
from ..services.wallet import local_user

router = APIRouter(prefix="/api/media", tags=["media"])

log = logging.getLogger("mco.media")
MAX_LIMIT = 500
_scan_thread: threading.Thread | None = None
_scan_guard = threading.Lock()


def _get_present(db: Session, media_id: int) -> MediaItem:
    m = db.get(MediaItem, media_id)
    if m is None or m.missing:
        raise HTTPException(404, "Media not found")
    return m


def _safe_path(m: MediaItem) -> Path:
    """The original file, only if it really lives inside the media folder."""
    base = folders.current_media_dir()
    root = base.resolve()
    p = (base / m.path).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise HTTPException(404, "File not found")
    return p


def _run_scan(media_dir: Path) -> None:
    try:
        scanner.full_scan(media_dir, expect_current=True)
    except Exception:
        log.exception("background scan failed")


@router.get("/scan")
def scan_status() -> dict:
    return asdict(scanner.get_status())


@router.post("/scan", status_code=202)
def start_scan(response: Response, preview: bool = False) -> dict:
    """Kick off a full scan in the background (it can wait minutes on files still copying).

    `?preview=1` (v0.8.0) starts nothing: it answers 200 with what a scan would add
    (`scanner.preview`: new_media, new_slots, the slots and completeness now and after)."""
    global _scan_thread
    if preview:
        response.status_code = 200
        return scanner.preview(folders.current_media_dir())
    with _scan_guard:
        if scanner.get_status().state == "scanning" or (_scan_thread and _scan_thread.is_alive()):
            return {"started": False}
        media_dir = folders.current_media_dir()
        _scan_thread = threading.Thread(target=_run_scan, args=(media_dir,), name="mco-manual-scan", daemon=True)
        _scan_thread.start()
    return {"started": True}


@router.get("")
def list_media(
    filter: str = "all",
    tag: int | None = None,
    q: str | None = None,
    sort: str = Query("name", pattern="^(name|added)$"),
    offset: int = Query(0, ge=0),
    limit: int = 200,
    db: Session = Depends(get_db),
) -> dict:
    limit = max(1, min(limit, MAX_LIMIT))
    user = local_user(db)
    stmt = select(MediaItem.id, MediaItem.path, MediaItem.title, MediaItem.number).where(MediaItem.missing.is_(False))

    if filter == "untagged":
        stmt = stmt.where(MediaItem.id.not_in(select(media_tags.c.media_id)))
    elif filter != "all":
        if filter != "pulled" and filter not in get_config().treatments:
            raise HTTPException(422, f"Unknown filter: {filter}")
        owned = select(CollectionEntry.media_id).where(
            CollectionEntry.user_id == user.id, CollectionEntry.copies > 0
        )
        if filter != "pulled":
            owned = owned.where(CollectionEntry.treatment == filter)
        stmt = stmt.where(MediaItem.id.in_(owned))
    if tag is not None:
        family = [tag, *db.scalars(select(Tag.id).where(Tag.parent_id == tag)).all()]
        stmt = stmt.where(MediaItem.id.in_(select(media_tags.c.media_id).where(media_tags.c.tag_id.in_(family))))

    if sort == "added":
        stmt = stmt.order_by(MediaItem.ingested_at.desc(), MediaItem.id.desc())
    rows = db.execute(stmt).all()
    # Searching and name ordering use the name shown on the card (the search also the real file
    # name, without folders, and the card's number as "#000123"), so they run on the rows here
    # rather than in SQL.
    if q or sort == "name":
        shown = {r.id: cards.card_title(r).casefold() for r in rows}
    if q:
        needle = q.casefold()
        rows = [r for r in rows if needle in shown[r.id] or needle in cards.filename(r.path).casefold()
                or needle in cards.card_code(r)]
    if sort == "name":
        rows.sort(key=lambda r: (shown[r.id], r.id))
    page_ids = [r.id for r in rows[offset : offset + limit]]
    by_id = {m.id: m for m in db.scalars(select(MediaItem).where(MediaItem.id.in_(page_ids)))}
    items = [by_id[i] for i in page_ids]
    return {"total": len(rows), "items": cards.card_out_many(db, user, items)}


@router.get("/{media_id}")
def get_media(media_id: int, db: Session = Depends(get_db)) -> dict:
    return cards.card_out(db, local_user(db), _get_present(db, media_id))


class TitleIn(BaseModel):
    title: str | None  # required; null or an empty text clears the custom name


@router.patch("/{media_id}")
def rename_media(media_id: int, body: TitleIn, db: Session = Depends(get_db)) -> dict:
    """Set or clear the player's own name for a card they own. The file on disk is not touched."""
    try:
        return organize.set_title(db, local_user(db), media_id, body.title)
    except organize.MediaNotFound:
        raise HTTPException(404, "Media not found") from None
    except organize.NotPulled:
        raise HTTPException(409, "Pull this card from a pack first: only cards you own can be renamed") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/{media_id}/thumb")
def get_thumb(media_id: int, db: Session = Depends(get_db)) -> FileResponse:
    m = _get_present(db, media_id)
    if m.kind != "image":
        raise HTTPException(404, "No thumbnail for this media")
    thumb = ensure_thumb(_safe_path(m), m.sha256)
    if thumb is None:
        raise HTTPException(404, "Thumbnail unavailable")
    return FileResponse(thumb, media_type="image/webp")


@router.get("/{media_id}/file")
def get_file(media_id: int, db: Session = Depends(get_db)) -> FileResponse:
    return FileResponse(_safe_path(_get_present(db, media_id)))
