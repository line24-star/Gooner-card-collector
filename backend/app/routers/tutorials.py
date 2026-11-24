"""Onboarding / tutorial progress.

Tour and article *content* lives in the frontend (frontend/src/help/). The backend only
records which tutorials the user has finished, so progress survives browser resets and can
later be tied to accounts and reward coins (design doc §7: milestones award coins).
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import TutorialProgress
from ..services import onboarding as onboarding_svc
from ..services.rewards import inventory
from ..services.wallet import local_user

router = APIRouter(prefix="/api/tutorials", tags=["tutorials"])

TutorialId = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")


class ProgressEntry(BaseModel):
    tutorial_id: str
    status: Literal["completed", "skipped"]
    updated_at: datetime
    # v0.9.0: tours pay packs, once, when the last tour that counts is completed. `packs` is what
    # this call gave ([{pack, count}], empty most of the time); `inventory` the sealed packs after it.
    packs: list[dict] = []
    inventory: dict[str, int] | None = None


class ProgressUpdate(BaseModel):
    status: Literal["completed", "skipped"] = "completed"


def _current_user_id() -> int | None:
    # TODO(auth): return the logged-in user's id once accounts exist.
    return None


def _query(user_id: int | None):
    q = select(TutorialProgress)
    if user_id is None:
        return q.where(TutorialProgress.user_id.is_(None))
    return q.where(TutorialProgress.user_id == user_id)


@router.get("/progress", response_model=list[ProgressEntry])
def list_progress(db: Session = Depends(get_db)):
    return db.scalars(_query(_current_user_id())).all()


@router.put("/progress/{tutorial_id}", response_model=ProgressEntry)
def set_progress(
    body: ProgressUpdate, tutorial_id: str = TutorialId, db: Session = Depends(get_db)
):
    user_id = _current_user_id()
    row = db.scalars(
        _query(user_id).where(TutorialProgress.tutorial_id == tutorial_id)
    ).one_or_none()
    if row is None:
        row = TutorialProgress(user_id=user_id, tutorial_id=tutorial_id, status=body.status)
        db.add(row)
    else:
        row.status = body.status
        row.updated_at = datetime.now(timezone.utc)
    db.commit()
    packs: list[dict] = []
    user = local_user(db)
    if body.status == "completed":  # skipping pays nothing and doesn't burn the one-time key
        packs = onboarding_svc.pay_tour(db, user, tutorial_id)
    db.refresh(row)
    return ProgressEntry(tutorial_id=row.tutorial_id, status=row.status, updated_at=row.updated_at,
                         packs=packs, inventory=inventory(db, user) if packs else None)


@router.delete("/progress", status_code=204)
def reset_progress(db: Session = Depends(get_db)):
    user_id = _current_user_id()
    cond = (
        TutorialProgress.user_id.is_(None)
        if user_id is None
        else TutorialProgress.user_id == user_id
    )
    db.execute(delete(TutorialProgress).where(cond))
    db.commit()
