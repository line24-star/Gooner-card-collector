"""The CardOut payload: one media item as the UI sees it (with the user's ownership and prices).

card_out_many batches every query (tags, ownership, paid reviews) and computes completeness and
inflation once, so listing hundreds of cards costs a fixed number of queries.
"""

from collections import defaultdict
from collections.abc import Sequence
from itertools import accumulate
from pathlib import PurePosixPath, PureWindowsPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..game import economy as eco
from ..game import titles
from ..game.config import get_config
from ..models import CollectionEntry, MediaItem, Tag, Transaction, User, media_tags
from .rewards import first_tag_times, is_shinier, next_tag_slots, obtained_after_tag


SALE_QUOTES = 50  # the sale preview covers selling up to this many copies of one treatment


def filename(path: str) -> str:
    return PureWindowsPath(path).name or PurePosixPath(path).name


def file_stem(path: str) -> str:
    """The file name without folders and extension, exactly as it is on disk."""
    return PureWindowsPath(filename(path)).stem


def card_number(media) -> int:
    """The card's permanent number (v0.8.0). `media` is a MediaItem or any row with `number` and
    `id`. Every stored card has a number; the id only stands in for a row that was never stored."""
    return media.number if media.number is not None else media.id


def card_code(media) -> str:
    """The number as shown: "#000123"."""
    return titles.card_code(card_number(media))


def card_title(media) -> str:
    """The one place a card's name is decided: every payload, news item, listing, offer, search and
    sort goes through here. `media` is a MediaItem or any row with `path`, `title`, `number`, `id`.

    The player's own name; else "Card #000123" (v0.9.0: file names no longer make names; the
    file name is on the payload as `file_name`)."""
    return titles.display_title(file_stem(media.path), media.title, card_number(media))


def card_out_many(db: Session, user: User, items: Sequence[MediaItem]) -> list[dict]:
    if not items:
        return []
    cfg = get_config()

    ids = [m.id for m in items]
    from .market import Prices  # late: market imports wallet, which this package's users import too

    prices = Prices(db, ids)

    tags: dict[int, list[dict]] = defaultdict(list)
    for media_id, tag in db.execute(
        select(media_tags.c.media_id, Tag)
        .join(Tag, Tag.id == media_tags.c.tag_id)
        .where(media_tags.c.media_id.in_(ids))
        .order_by(Tag.name)
    ):
        tags[media_id].append({"id": tag.id, "name": tag.name, "parent_id": tag.parent_id})

    owned: dict[int, dict[str, int]] = defaultdict(dict)
    pulled_at: dict[tuple[int, str], object] = {}
    for e in db.scalars(
        select(CollectionEntry).where(
            CollectionEntry.user_id == user.id, CollectionEntry.media_id.in_(ids), CollectionEntry.copies > 0
        )
    ):
        if e.treatment in cfg.treatments:
            owned[e.media_id][e.treatment] = e.copies
            pulled_at[(e.media_id, e.treatment)] = e.first_pulled_at

    paid = set(
        db.scalars(
            select(Transaction.dedupe_key).where(
                Transaction.dedupe_key.in_([f"review:{i}:{t}" for i in ids for t in owned[i]])
            )
        ).all()
    ) if owned else set()

    tagged_at = first_tag_times(db, user, [i for i in ids if tags.get(i)])

    # the slot the next tag takes per card, and the most it can be paid (after a removed tag): it
    # sets what the next tag pays
    tag_slots = next_tag_slots(db, user, ids)
    from .offers import rush_active  # late: offers imports rewards, which imports this package

    rush = rush_active(db, user)

    out = []
    for m in items:
        mine = owned.get(m.id, {})
        best = max(mine, key=lambda t: cfg.treatments[t].tier) if mine else None
        review_t = (
            best
            if best
            and is_shinier(best)  # a Normal copy is never "a shinier card"
            and tags.get(m.id)
            and obtained_after_tag(pulled_at[(m.id, best)], tagged_at.get(m.id))
            and f"review:{m.id}:{best}" not in paid
            else None
        )
        need = prices.fuse_need(m.id)
        by_tier = sorted(mine, key=lambda t: cfg.treatments[t].tier)
        # every fuse this card's copies allow or lack copies for, lowest treatment first (v0.8.0)
        fuse_options = []
        for t in by_tier:
            up = eco.next_tier(cfg, t)
            if up is None:
                continue
            fuse_options.append({
                "from": t,
                "to": up,
                "copies": need,  # copies one fuse consumes
                "have": mine[t],
                "can": mine[t] >= need,
                # the real change in versions held: +1, 0 or -1
                "net_versions": eco.fuse_net_versions(cfg, mine, t, need),
                "empties": cfg.treatments[t].tier > 0 and mine[t] - need <= 0,  # the fuse uses the last copies of a slot
                "fills": mine.get(up, 0) <= 0,  # the copy gained fills an empty slot
                "value": prices.price(m.id, t) * need,  # market value of the copies consumed
            })
        out.append({
            "id": m.id,
            # the card's permanent number (v0.8.0) and how it is shown
            "number": card_number(m),
            "code": card_code(m),
            # the name shown on the card; file_name is the real file name (never changed on disk)
            "title": card_title(m),
            "title_custom": bool(m.title and m.title.strip()),
            "file_name": file_stem(m.path),
            "kind": m.kind,
            "thumb_url": f"/api/media/{m.id}/thumb" if m.kind == "image" else None,
            "file_url": f"/api/media/{m.id}/file",
            "treatment": best or "normal",
            "owned": {t: True for t in mine},
            "copies": sum(mine.values()),
            "tags": tags.get(m.id, []),
            "width": m.width,
            "height": m.height,
            "added_at": m.ingested_at.isoformat() if m.ingested_at else None,
            "review_available": review_t is not None,
            "review_reward": eco.review_reward(cfg, cfg.treatments[review_t].multiplier) if review_t else None,
            "upgrade_cost": eco.upgrade_cost(cfg, best, need) if best else None,
            # what one more tag on this card pays right now (0 = this card's tags are paid out)
            "next_tag_reward": _next_tag_reward(cfg, tag_slots.get(m.id, (0, None)), best, rush),
            # what the market pays for one copy of each treatment you own, today
            "sell_prices": {t: prices.sell(m.id, t) for t in mine},
            # the same without the wanted bonus (v0.8.0). Equal to sell_prices unless the card is
            # wanted; the bonus in coins is sell_prices[t] - plain_prices[t]
            "plain_prices": {t: prices.plain_sell(m.id, t) for t in mine},
            # what selling every copy but one pays in all: (copies - 1) × sell_prices[t], since a
            # sale no longer moves the price (v0.8.0)
            "sell_extras": {t: prices.quote(m.id, t, n - 1) for t, n in mine.items() if n > 1},
            # the sale preview (v0.8.0): sale_quotes[t][n - 1] is what selling n copies of t pays
            # in all, for n = 1 .. copies held (at most SALE_QUOTES): n × sell_prices[t]. Selling
            # all of them empties the slot when t is above Normal.
            "sale_quotes": {t: list(accumulate(prices.sale_prices(m.id, t, min(n, SALE_QUOTES)))) for t, n in mine.items()},
            # the market price of one copy at every treatment, held or not (v0.8.0)
            "prices": {t.id: prices.price(m.id, t.id) for t in cfg.tiers()},
            "fuse_options": fuse_options,
            "copies_by_treatment": dict(mine),
            # the market: what a copy of your best treatment is worth, and the card's scarcity
            "value": prices.price(m.id, best or "normal"),
            "fuse_from": next((t for t in by_tier if mine[t] >= need and eco.next_tier(cfg, t)), None),
            # copies one fuse of this card takes today: more for a card that is worth more
            "fuse_copies": need,
            **{k: v for k, v in prices.info(m.id).items() if k != "value"},
        })
    return out


def _next_tag_reward(cfg, slot: tuple[int, int | None], best: str | None, rush: bool) -> int:
    index, cap = slot
    pays = eco.tag_reward(cfg, index, cfg.treatments[best].multiplier if best else 1.0, owned=best is not None, rush=rush)
    return pays if cap is None else min(pays, cap)


def card_out(db: Session, user: User, media: MediaItem) -> dict:
    return card_out_many(db, user, [media])[0]
