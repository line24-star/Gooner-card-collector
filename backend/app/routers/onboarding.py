"""Getting-started checklist API."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import onboarding as svc
from ..services.chase import NotClaimable
from ..services.wallet import local_user

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


@router.get("")
def get_checklist(db: Session = Depends(get_db)) -> dict:
    user = local_user(db)
    # v0.9.0: `tours` is the tour reward (packs once every tour is completed)
    return {**svc.checklist(db, user), "tours": svc.tours_status(db, user)}


@router.post("/dismiss")
def dismiss(db: Session = Depends(get_db)) -> dict:
    svc.dismiss(db)
    return {"dismissed": True}


@router.post("/restore")
def restore(db: Session = Depends(get_db)) -> dict:
    """Bring the hidden checklist back (the Player page has the button)."""
    svc.restore(db)
    return {"dismissed": False}


@router.post("/{step_id}/claim")
def claim(step_id: str, db: Session = Depends(get_db)) -> dict:
    user = local_user(db)
    try:
        return svc.claim(db, user, step_id)  # {packs, inventory, coins}
    except NotClaimable:
        raise HTTPException(409, "Step not finished yet, already claimed, or without a reward")
