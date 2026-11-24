"""Organizing endpoints: tags, review, bulk tagging. All draw from one rate-limit bucket."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, MediaItem, Tag, media_tags
from ..services import organize, ratelimit
from ..services.wallet import local_user

router = APIRouter(prefix="/api", tags=["organize"])


class TagIn(BaseModel):
    name: str
    parent: str | None = None
    typed: bool = False  # v0.9.0: the tag was typed (not a quick key): it ends the tagging pause


class BulkIn(BaseModel):
    media_ids: list[int]
    name: str


def _limit(db: Session, route: str) -> None:
    if not ratelimit.organize_bucket.allow("organize"):
        db.add(AuditLog(event="rate_limited", detail=route))
        db.commit()
        raise HTTPException(429, "Too many requests, slow down a little")


def _run(fn, *args):
    try:
        return fn(*args)
    except organize.MediaNotFound:
        raise HTTPException(404, "Media not found")
    except organize.NotPulled:
        raise HTTPException(409, "Pull this card from a pack first: only cards you own can be tagged") from None
    except organize.TooManyItems:
        raise HTTPException(413, "Too many items in one bulk request")
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/tags")
def list_tags(db: Session = Depends(get_db)) -> list[dict]:
    counts = dict(
        db.execute(
            select(media_tags.c.tag_id, func.count())
            .join(MediaItem, MediaItem.id == media_tags.c.media_id)
            .where(MediaItem.missing.is_(False))
            .group_by(media_tags.c.tag_id)
        ).all()
    )
    return [
        {"id": t.id, "name": t.name, "parent_id": t.parent_id, "count": counts.get(t.id, 0)}
        for t in db.scalars(select(Tag).order_by(Tag.name))
    ]


@router.get("/tags/suggest")
def suggest_tags(q: str = "", media_id: int | None = None, limit: int = 8, db: Session = Depends(get_db)) -> dict:
    """Tags to offer while typing.

    ``matches``: existing tags containing ``q`` (those starting with it first, then the most used).
    ``related``: tags that often sit on the same cards as this card's tags (or simply the most used
    tags when the card has none yet). Tags the card already has are left out of both.
    """
    limit = max(1, min(limit, 20))
    needle = " ".join(q.lower().split())
    counts = dict(db.execute(
        select(media_tags.c.tag_id, func.count())
        .join(MediaItem, MediaItem.id == media_tags.c.media_id)
        .where(MediaItem.missing.is_(False))
        .group_by(media_tags.c.tag_id)
    ).all())
    tags = {t.id: t for t in db.scalars(select(Tag))}
    mine = set(db.scalars(select(media_tags.c.tag_id).where(media_tags.c.media_id == media_id)).all()) if media_id else set()

    def out(ids):
        return [{"id": i, "name": tags[i].name, "count": counts.get(i, 0)} for i in ids if i in tags]

    matches = []
    if needle:
        hits = [i for i, t in tags.items() if needle in t.name and i not in mine]
        hits.sort(key=lambda i: (not tags[i].name.startswith(needle), -counts.get(i, 0), tags[i].name))
        matches = hits[:limit]

    if mine:
        siblings = select(media_tags.c.media_id).where(media_tags.c.tag_id.in_(mine))
        together = db.execute(
            select(media_tags.c.tag_id, func.count())
            .where(media_tags.c.media_id.in_(siblings), media_tags.c.tag_id.not_in(mine))
            .group_by(media_tags.c.tag_id)
        ).all()
        related = [i for i, _ in sorted(together, key=lambda r: (-r[1], tags[r[0]].name if r[0] in tags else ""))]
    else:
        related = []
    # Fill up with the most used tags, so there is always something to pick.
    popular = sorted((i for i in tags if i not in mine and i not in related), key=lambda i: (-counts.get(i, 0), tags[i].name))
    related = (related + popular)[:limit]
    return {"matches": out(matches), "related": out([i for i in related if i not in matches])}


@router.post("/media/{media_id}/tags")
def add_tag(media_id: int, body: TagIn, db: Session = Depends(get_db)) -> dict:
    _limit(db, "POST /api/media/{id}/tags")
    return _run(organize.add_tag, db, local_user(db), media_id, body.name, body.parent, body.typed)


@router.delete("/media/{media_id}/tags/{tag_id}")
def remove_tag(media_id: int, tag_id: int, db: Session = Depends(get_db)) -> dict:
    """Remove a tag. v0.9.0: answers 200 with `refunded` (the coins that tag had paid on this card,
    taken back as far as the balance goes), `coins` and the `card` (before: 204, nothing taken)."""
    _limit(db, "DELETE /api/media/{id}/tags/{tag_id}")
    return _run(organize.remove_tag, db, local_user(db), media_id, tag_id)


@router.post("/media/{media_id}/review")
def review(media_id: int, db: Session = Depends(get_db)) -> dict:
    _limit(db, "POST /api/media/{id}/review")
    return _run(organize.review, db, local_user(db), media_id)


@router.post("/tags/bulk")
def bulk(body: BulkIn, db: Session = Depends(get_db)) -> dict:
    _limit(db, "POST /api/tags/bulk")
    return _run(organize.bulk_tag, db, local_user(db), body.media_ids, body.name)
