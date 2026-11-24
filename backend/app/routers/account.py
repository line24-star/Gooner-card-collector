"""The player's account page.

GET  /api/account          -> name, since, balance, lifetime earned/spent (with a per-reason split),
                              packs opened, streak, card counts, the media folder and the log folder
PUT  /api/account {name}   -> rename the player (1-32 characters)
POST /api/account/logs/open -> show the log folder in the file manager (204 | 500)
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import logs
from ..db import get_db
from ..models import (
    AppState, AuditLog, CollectionEntry, MediaItem, Pack, SetCredit, Tag, TagSlot, Transaction, TutorialProgress, User,
    media_tags,
)
from ..services import desktop, folders, market
from ..services.rewards import LIBRARY_PEAK_KEY
from ..services.wallet import local_user

router = APIRouter(prefix="/api/account", tags=["account"])

NAME_KEY = "player_name"
DEFAULT_NAME = "Player"


class NameIn(BaseModel):
    name: str


def clean_name(raw: str) -> str:
    """Trim and collapse whitespace; 1-32 characters. Raises ValueError otherwise."""
    name = " ".join(raw.split())
    if not 1 <= len(name) <= 32:
        raise ValueError("A name needs 1 to 32 characters.")
    return name


def _payload(db: Session) -> dict:
    user = local_user(db)
    row = db.get(AppState, NAME_KEY)
    by_reason = db.execute(
        select(Transaction.reason, func.sum(Transaction.amount), func.count())
        .where(Transaction.user_id == user.id)
        .group_by(Transaction.reason)
    ).all()
    present = select(MediaItem.id).where(MediaItem.missing.is_(False))
    owned = db.scalar(
        select(func.count(func.distinct(CollectionEntry.media_id))).where(
            CollectionEntry.user_id == user.id, CollectionEntry.copies > 0, CollectionEntry.media_id.in_(present)
        )
    )
    return {
        "name": row.value if row and row.value else DEFAULT_NAME,
        "since": user.created_at.isoformat() if user.created_at else None,
        "coins": user.coins,
        "packs_opened": user.packs_opened,
        "streak_days": user.streak_days,
        "earned": sum(total for _, total, _ in by_reason if total > 0),
        "spent": -sum(total for _, total, _ in by_reason if total < 0),
        "by_reason": sorted(
            ({"reason": reason, "total": total, "count": n} for reason, total, n in by_reason),
            key=lambda r: -abs(r["total"]),
        ),
        "cards_total": folders.media_count(db),
        "cards_owned": owned or 0,
        "media_folder": str(folders.current_media_dir()),
        "log_folder": str(logs.log_dir()),
    }


@router.get("")
def get_account(db: Session = Depends(get_db)) -> dict:
    return _payload(db)


@router.put("")
def rename(body: NameIn, db: Session = Depends(get_db)) -> dict:
    try:
        name = clean_name(body.name)
    except ValueError as e:
        raise HTTPException(422, detail={"message": str(e)}) from None
    db.merge(AppState(key=NAME_KEY, value=name))
    db.commit()
    return _payload(db)


class ResetIn(BaseModel):
    confirm: str = ""


@router.post("/reset")
def reset(body: ResetIn, db: Session = Depends(get_db)) -> dict:
    """Wipe the player's progress for a clean start. The body must carry confirm = "RESET".

    Gone: coins and their history, sealed and opened packs, pulled cards, tags, streak, checklist,
    tours, set rewards, the goals picked for today and this week, mastery (its points come from the
    cards and fuses, its paid levels from the ledger, so the levels pay again like every other
    one-time reward), the market's history. Kept: the imported library (with the names the player
    gave cards: a name describes the file, it is not progress), the media folder and the player's name. The player starts again with the welcome coins and a new market.
    """
    if body.confirm != "RESET":
        raise HTTPException(422, detail={"message": 'Type RESET to confirm.'})
    for table in (media_tags, SetCredit.__table__, TagSlot.__table__, CollectionEntry.__table__, Pack.__table__, Transaction.__table__,
                  TutorialProgress.__table__, Tag.__table__, AuditLog.__table__, User.__table__):
        db.execute(table.delete())
    keep = {"media_dir", "initial_scan_done", "initial_import_until", NAME_KEY, LIBRARY_PEAK_KEY}
    for row in db.scalars(select(AppState)).all():
        if row.key not in keep:
            db.delete(row)
    market.reset(db)
    db.commit()
    db.expire_all()
    user = local_user(db)  # a new player with the welcome coins
    market.catch_up(db)
    db.add(AuditLog(user_id=user.id, event="reset", detail="progress wiped by the player"))
    db.commit()
    return _payload(db)


@router.post("/logs/open", status_code=204)
def open_logs() -> Response:
    try:
        logs.log_dir().mkdir(parents=True, exist_ok=True)
        desktop.open_folder(logs.log_dir())
    except (OSError, RuntimeError) as e:
        raise HTTPException(500, detail={"message": str(e) or "Couldn't open the log folder."}) from None
    return Response(status_code=204)
