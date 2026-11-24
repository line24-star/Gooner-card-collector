"""Read-only game configuration the frontend needs (treatments, pack rates)."""

from fastapi import APIRouter

from ..game import treatments

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/treatments")
def list_treatments() -> list[dict]:
    return treatments.as_dicts()
