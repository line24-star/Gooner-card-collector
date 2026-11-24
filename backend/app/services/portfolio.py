"""The player's collection as a whole: what it is worth and how that moved (v0.5.0).

A price belongs to a card. There is no "price of the market": only the player's own collection has
a knowable value, and only until the next 6-hour tick or the next pack. So every number here is
about the copies the player holds NOW, priced at earlier ticks.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..game import market as mk
from ..models import CollectionEntry, MediaItem, User
from . import market


def _holdings(db: Session, user: User) -> list[tuple[int, str, int]]:
    return [
        (media_id, treatment, copies)
        for media_id, treatment, copies in db.execute(
            select(CollectionEntry.media_id, CollectionEntry.treatment, CollectionEntry.copies)
            .join(MediaItem, MediaItem.id == CollectionEntry.media_id)
            .where(CollectionEntry.user_id == user.id, CollectionEntry.copies > 0, MediaItem.missing.is_(False))
        )
    ]


def value_history(db: Session, user: User, prices: market.Prices | None = None) -> list[int]:
    """What today's holdings were worth at each kept tick, oldest first. Empty without cards.

    A card with a shorter history counts at its oldest known price before that."""
    prices = prices or market.Prices(db)
    cfg = prices.cfg
    rows = []
    for media_id, treatment, copies in _holdings(db, user):
        hist = prices.history(media_id)
        if hist and treatment in cfg.market.treatment_value:
            # the history is the Normal price; a rarer copy is worth this card's own multiple of it
            rows.append((hist, prices.factor(media_id, treatment) * copies))
    if not rows:
        return []
    n = max(len(h) for h, _ in rows)
    out = []
    for i in range(n):
        total = 0.0
        for hist, k in rows:
            j = i - (n - len(hist))
            total += hist[max(j, 0)] * k
        out.append(round(total))
    return out


def change(db: Session, user: User, prices: market.Prices | None = None) -> dict:
    """The collection's value at the last tick against the tick before it.

    `change` is a fraction (0.04 = up 4%); 0 with fewer than two ticks or without cards."""
    hist = value_history(db, user, prices)
    if len(hist) < 2 or hist[-2] <= 0:
        return {"previous": hist[-1] if hist else 0, "change": 0.0, "coins": 0}
    return {"previous": hist[-2], "change": round(hist[-1] / hist[-2] - 1, 4), "coins": hist[-1] - hist[-2]}


def dated_history(db: Session, user: User, state: dict, prices: market.Prices | None = None) -> list[dict]:
    """`value_history` with the time of each tick, for the chart."""
    prices = prices or market.Prices(db, state=state)
    hist = value_history(db, user, prices)
    last = state["tick"]
    return [
        {"at": datetime.fromtimestamp(mk.tick_start(prices.cfg, last - (len(hist) - 1 - i)), tz=timezone.utc).isoformat(),
         "price": v}
        for i, v in enumerate(hist)
    ]
