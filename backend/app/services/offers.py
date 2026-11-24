"""Timed offers that give the player a reason to act now: the shop deal and the tag rush.

* **Deal.** In every window of ``deal.hours`` one pack is cheaper, at most ``deal.per_window``
  times. Which pack and how much follows from the window number and the market seed, so it is the
  same every time the page asks, and it can't be re-rolled.
* **Tag rush.** Once per local day the player can start a rush: for ``tagging.rush_minutes`` every
  tag pays ``tagging.rush_multiplier`` times as much. v0.9.0: and not within
  ``tagging.rush_gap_hours`` of the last start. "Once a local day" alone let a rush started at
  shortly before midnight be followed by a second one right after it.
"""

import json
import random
import threading
import time
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from ..game import economy as eco
from ..game.config import get_config
from ..models import AppState, User
from .rewards import completeness


def _get(db: Session, key: str) -> str | None:
    row = db.get(AppState, key)
    return row.value if row is not None else None


def _seed(db: Session) -> int:
    raw = _get(db, "market")
    try:
        return int(json.loads(raw)["seed"]) if raw else 0
    except (ValueError, KeyError, TypeError):
        return 0


# ---------------------------------------------------------------- the deal


def deal(db: Session, user: User, now: float | None = None) -> dict | None:
    """The current deal, or None when deals are off."""
    cfg = get_config()
    if cfg.deal.hours <= 0 or not cfg.packs:
        return None
    now = time.time() if now is None else now
    length = cfg.deal.hours * 3600
    window = int(now // length)
    rng = random.Random(_seed(db) * 7919 + window)
    pack_id = rng.choice(sorted(cfg.packs))
    discount = round(rng.uniform(*cfg.deal.discount), 2)
    used = int(_get(db, f"deal:{user.id}:{window}") or 0)
    full = eco.pack_cost(cfg, cfg.packs[pack_id], user.packs_opened, completeness(db, user))
    step = max(1, cfg.inflation.round_to)
    return {
        "window": window,
        "pack": pack_id,
        "discount": discount,
        "full_price": full,
        "price": max(step, int(round(full * (1 - discount) / step) * step)),
        "left": max(0, cfg.deal.per_window - used),
        "ends_at": datetime.fromtimestamp((window + 1) * length, tz=timezone.utc).isoformat(),
    }


def use_deal(db: Session, user: User, window: int) -> None:
    key = f"deal:{user.id}:{window}"
    db.merge(AppState(key=key, value=str(int(_get(db, key) or 0) + 1)))


# ---------------------------------------------------------------- the tag rush


def rush(db: Session, user: User, now: float | None = None, today: date | None = None) -> dict:
    cfg = get_config()
    now = time.time() if now is None else now
    today = today or date.today()
    until = float(_get(db, f"rush_until:{user.id}") or 0)
    used_today = _get(db, f"rush_day:{user.id}") == today.isoformat()
    on = cfg.tagging.rush_minutes > 0
    # When the last rush started: stored since v0.9.0; for an older one, its end minus its length.
    started = float(_get(db, f"rush_started:{user.id}") or 0) or (until - cfg.tagging.rush_minutes * 60 if until else 0.0)
    ready_at = max(until, started + cfg.tagging.rush_gap_hours * 3600) if started else 0.0
    available = on and not used_today and now >= ready_at
    # `available_at`: when the next rush can start, when that is known and still ahead (the gap
    # after the last start, or the end of the running rush); None when it is available now or
    # only "tomorrow" (the next local day) is the answer.
    return {
        "active": on and now < until,
        "ends_at": datetime.fromtimestamp(until, tz=timezone.utc).isoformat() if now < until else None,
        "available": available,
        "available_at": (datetime.fromtimestamp(ready_at, tz=timezone.utc).isoformat()
                         if on and not available and not used_today and now < ready_at else None),
        "multiplier": cfg.tagging.rush_multiplier,
        "minutes": cfg.tagging.rush_minutes,
    }


def rush_active(db: Session, user: User, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return get_config().tagging.rush_minutes > 0 and now < float(_get(db, f"rush_until:{user.id}") or 0)


class RushUnavailable(Exception):
    pass


_rush_lock = threading.Lock()


def start_rush(db: Session, user: User, now: float | None = None, today: date | None = None) -> dict:
    """Start today's tag rush. Raises RushUnavailable when it was already used today, is running,
    or the last one started less than `tagging.rush_gap_hours` ago.

    One start at a time in this process (`_rush_lock`), so two requests arriving together cannot
    both find it available."""
    now = time.time() if now is None else now
    today = today or date.today()
    with _rush_lock:
        db.expire_all()
        if not rush(db, user, now, today)["available"]:
            raise RushUnavailable()
        cfg = get_config()
        db.merge(AppState(key=f"rush_until:{user.id}", value=str(now + cfg.tagging.rush_minutes * 60)))
        db.merge(AppState(key=f"rush_day:{user.id}", value=today.isoformat()))
        db.merge(AppState(key=f"rush_started:{user.id}", value=str(now)))
        db.commit()
    return rush(db, user, now, today)
