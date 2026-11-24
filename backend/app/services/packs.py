"""Buying/opening packs and selling cards against the database (server-authoritative)."""

import json
import random
from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..game import economy as eco
from ..game.config import get_config
from ..game.packs import PoolItem, ends_dry_run, reveal_order, roll_hint, roll_pack, shown_treatment
from ..models import AppState, AuditLog, CollectionEntry, MediaItem, Pack, User, media_tags
from . import market
from .cards import card_out_many
from .rewards import completeness, inventory, note_milestones, slot_holdings, slots, slots_change  # noqa: F401  (inventory: used by the routers through this module)
from .wallet import debit


class NoMedia(Exception):
    pass


class UnknownPack(Exception):
    pass


class IdempotencyKeyInUse(Exception):
    pass


class NotOwned(Exception):
    pass


class NoSealedPack(Exception):
    """The inventory holds no sealed pack of that kind."""


class NoDeal(Exception):
    """The deal is over, is for another pack, or has been used up."""


class NoFreePack(Exception):
    """No free pack to claim right now (already claimed today, or the daily check-in is missing)."""


# ---------------------------------------------------------------- the dry streak and the daily free pack


def _state(db: Session, key: str) -> str | None:
    row = db.get(AppState, key)
    return row.value if row is not None else None


def dry_packs(db: Session, user: User) -> int:
    """The dry streak: packs opened in a row without one of the `pity.treatments` (Holo, Special).

    v0.9.0: it changes no odds (the soft pity is switched off, `pity.enabled`). The page gets it as
    `dry_streak` and only lets the face-down cards glow more with it."""
    try:
        return max(0, int(_state(db, f"pity_dry:{user.id}") or 0))
    except ValueError:
        return 0


def free_pack_status(db: Session, user: User, today: date | None = None) -> dict:
    """The daily free pack: which pack it is and whether it can be claimed now."""
    cfg = get_config()
    today = today or date.today()
    pack_id = cfg.free_pack.pack
    claimed = _state(db, f"free_pack_day:{user.id}") == today.isoformat()
    checked_in = user.last_active_day == today.isoformat() and user.streak_days >= cfg.free_pack.min_streak
    has_media = _has_media(db)
    return {
        "pack": pack_id or None,
        "claimed_today": claimed,
        "available": bool(pack_id) and checked_in and not claimed and has_media,
        "needs_checkin": bool(pack_id) and not checked_in,
    }


def current_prices(db: Session, user: User) -> dict[str, int]:
    cfg = get_config()
    comp = completeness(db, user)
    return {pid: eco.pack_cost(cfg, p, user.packs_opened, comp) for pid, p in cfg.packs.items()}


def _pool(db: Session, user: User) -> list[PoolItem]:
    tagged = set(db.scalars(select(media_tags.c.media_id).distinct()).all())
    pool = {m: PoolItem(m, tagged=m in tagged) for m in db.scalars(select(MediaItem.id).where(MediaItem.missing.is_(False))).all()}
    for e in db.scalars(select(CollectionEntry).where(CollectionEntry.user_id == user.id)).all():
        if e.media_id in pool and e.copies > 0:
            pool[e.media_id].owned[e.treatment] = e.copies
    # A card's print run decides how often a pack pulls it, and its market price what a copy is worth.
    for media_id, (weight, value, premium) in market.weights_and_values(db).items():
        if media_id in pool:
            pool[media_id].weight, pool[media_id].value, pool[media_id].premium = weight, value, premium
    return list(pool.values())


def _add_copy(db: Session, user: User, media_id: int, treatment: str) -> None:
    entry = db.scalars(
        select(CollectionEntry).where(
            CollectionEntry.user_id == user.id,
            CollectionEntry.media_id == media_id,
            CollectionEntry.treatment == treatment,
        )
    ).one_or_none()
    if entry is None:
        entry = CollectionEntry(user_id=user.id, media_id=media_id, treatment=treatment, copies=0)
        db.add(entry)
        db.flush()  # a pack in a tiny library can hold the same card twice: the next copy must find this row
    if entry.copies <= 0:
        entry.first_pulled_at = datetime.now(timezone.utc)  # regained after a sell: counts as newly obtained
    entry.copies += 1


def take_copy(db: Session, user: User, media_id: int, treatment: str, keep: int = 0) -> bool:
    """Remove one copy, atomically: False (and nothing changes) when there is none left.

    A single conditional UPDATE, so two requests racing for the last copy can't both take it.
    keep: leave at least this many copies (selling duplicates keeps one).
    """
    taken = db.execute(
        update(CollectionEntry)
        .where(
            CollectionEntry.user_id == user.id,
            CollectionEntry.media_id == media_id,
            CollectionEntry.treatment == treatment,
            CollectionEntry.copies > max(0, keep),
        )
        .values(copies=CollectionEntry.copies - 1),
        execution_options={"synchronize_session": False},
    ).rowcount
    db.expire_all()
    return bool(taken)


def move_copy_up(db: Session, user: User, media_id: int, current: str, new: str) -> bool:
    """Turn one copy at `current` into one copy at `new` (an upgrade moves the copy, it doesn't add one)."""
    if not take_copy(db, user, media_id, current):
        return False
    _add_copy(db, user, media_id, new)
    return True


def _is_missing(db: Session, media_id: int) -> bool:
    missing = db.scalar(select(MediaItem.missing).where(MediaItem.id == media_id))
    return missing is None or bool(missing)


def _with_cards(db: Session, user: User, pulls: list[dict]) -> list[dict]:
    """Attach a CardOut (and is_new) to each pull using one batched card_out_many call."""
    ids = list(dict.fromkeys(p["media_id"] for p in pulls))
    items = db.scalars(select(MediaItem).where(MediaItem.id.in_(ids))).all() if ids else []
    cards = {c["id"]: c for c in card_out_many(db, user, items)}
    prices = market.Prices(db, ids)

    def shown(p: dict) -> str:
        return p["upgraded_to"] if p.get("outcome") == "upgraded" and p.get("upgraded_to") else p["treatment"]

    cfg = get_config()

    def name(t: str) -> str:
        return cfg.treatments[t].name if t in cfg.treatments else t

    # `value`: what the copy just pulled is worth on the market today.
    # fills_slot / already_owned: stored with the pack since v0.8.0 (older packs: false).
    # v0.9.0: `hint` (does the face-down card glow: a hint that can be wrong; stored with the pack,
    # for an older pack it is fills_slot), `shown` (the treatment the revealed card shows) and
    # `treatment_name` (its name, for "Fills Foil slot").
    return [{"fills_slot": False, "already_owned": False, "hint": bool(p.get("fills_slot")), **p,
             "shown": shown(p), "treatment_name": name(shown(p)),
             "card": cards.get(p["media_id"]), "is_new": p["outcome"] == "new",
             "value": prices.price(p["media_id"], shown(p))} for p in pulls]


def _replay(db: Session, user: User, row: Pack) -> dict:
    stored = json.loads(row.contents_json)
    db.add(AuditLog(user_id=user.id, event="pack_replay", detail=f"pack:{row.id} key:{row.idem_key}"))
    filled, total = slots(db, user)
    result = {
        "pack_id": stored["pack"],
        "cost": stored["cost"],
        "pulls": _with_cards(db, user, stored["pulls"]),
        "coins": user.coins,
        "replayed": True,
        "milestones": [],
        # a replay changes nothing: the pulls keep their flags, `slots` shows the count as it is now
        "slots": {"filled": filled, "total": total, "delta": 0, "filled_now": [], "emptied": [], "total_delta": 0},
        "free": bool(stored.get("free")),
        "source": stored.get("source") or ("free" if stored.get("free") else "bought"),
        "dry_streak": dry_packs(db, user),
        "inventory": inventory(db, user),
    }
    db.commit()
    return result


def _find_by_key(db: Session, user: User, idem_key: str) -> Pack | None:
    return db.scalars(select(Pack).where(Pack.user_id == user.id, Pack.idem_key == idem_key)).one_or_none()


MAX_BUY = 50  # packs per purchase


def _has_media(db: Session) -> bool:
    return db.scalar(select(MediaItem.id).where(MediaItem.missing.is_(False)).limit(1)) is not None


def buy_packs(db: Session, user: User, pack_id: str, count: int = 1, *, idem_key: str | None = None,
              use_deal: bool = False) -> dict:
    """Buy `count` sealed packs. All or nothing: InsufficientFunds leaves nothing bought (the caller
    rolls back). Each pack is charged at the price of that moment, so the price rises within one
    purchase. `idem_key`: a repeat of the same purchase charges once."""
    cfg = get_config()
    pack = cfg.packs.get(pack_id)
    if pack is None:
        raise UnknownPack(pack_id)
    if not 1 <= count <= MAX_BUY:
        raise ValueError(f"Buy 1 to {MAX_BUY} packs at a time")
    key = f"buy:{idem_key}" if idem_key else None
    if key and _find_by_key(db, user, key) is not None:
        return {"pack_id": pack_id, "bought": 0, "cost": 0, "coins": user.coins, "inventory": inventory(db, user),
                "replayed": True}
    if not _has_media(db):
        raise NoMedia()
    comp = completeness(db, user)
    total = 0
    offer = None
    if use_deal:
        from . import offers

        offer = offers.deal(db, user)
        if offer is None or offer["pack"] != pack_id or offer["left"] < count:
            raise NoDeal()
    try:
        for n in range(count):
            cost = offer["price"] if offer else eco.pack_cost(cfg, pack, user.packs_opened, comp)
            if offer:
                offers.use_deal(db, user, offer["window"])
            row = Pack(user_id=user.id, pack_type=pack_id, source="bought", cost=cost, idem_key=key if n == 0 else None)
            db.add(row)
            db.flush()  # the unique idem_key index rejects a concurrent duplicate here
            debit(db, user, cost, "pack", ref=f"pack:{row.id}")
            user.packs_opened += 1  # bought packs raise the price of the next one
            total += cost
    except IntegrityError:
        db.rollback()
        return {"pack_id": pack_id, "bought": 0, "cost": 0, "coins": user.coins, "inventory": inventory(db, user),
                "replayed": True}
    db.commit()
    return {"pack_id": pack_id, "bought": count, "cost": total, "coins": user.coins, "inventory": inventory(db, user),
            "replayed": False}


def claim_free_pack(db: Session, user: User, today: date | None = None) -> dict:
    """Put today's free pack, sealed, into the inventory. Raises NoFreePack when there is none to claim."""
    today = today or date.today()
    status = free_pack_status(db, user, today)
    if not status["available"]:
        raise NoFreePack()
    row = Pack(user_id=user.id, pack_type=status["pack"], source="free", cost=0)
    db.add(row)
    db.flush()
    db.merge(AppState(key=f"free_pack_day:{user.id}", value=today.isoformat()))
    db.add(AuditLog(user_id=user.id, event="free_pack", detail=f"pack:{row.id} day:{today.isoformat()}"))
    db.commit()
    return {"pack_id": status["pack"], "inventory": inventory(db, user)}


def open_free_pack(db: Session, user: User, rng: random.Random | None = None, today: date | None = None) -> dict:
    """Claim today's free pack and open it straight away."""
    claimed = claim_free_pack(db, user, today)
    return open_pack(db, user, claimed["pack_id"], rng, buy=False)


def _take_sealed(db: Session, user: User, pack_id: str) -> Pack | None:
    """Take the oldest sealed pack of this kind out of the inventory, atomically (two requests
    can't open the same pack). Returns None when there is none."""
    while True:
        row = db.scalars(
            select(Pack)
            .where(Pack.user_id == user.id, Pack.pack_type == pack_id, Pack.opened_at.is_(None))
            .order_by(Pack.id)
            .limit(1)
        ).first()
        if row is None:
            return None
        taken = db.execute(
            update(Pack).where(Pack.id == row.id, Pack.opened_at.is_(None)).values(opened_at=datetime.now(timezone.utc)),
            execution_options={"synchronize_session": False},
        ).rowcount
        if taken:
            db.refresh(row)
            return row


def open_pack(
    db: Session, user: User, pack_id: str, rng: random.Random | None = None, *, idem_key: str | None = None,
    buy: bool = True,
) -> dict:
    """Open one sealed pack of this kind from the inventory. With none there: `buy=True` buys one
    at today's price and opens it, `buy=False` raises NoSealedPack.

    The cards are rolled now, not when the pack was bought, so they come from today's collection
    and today's luck."""
    cfg = get_config()
    pack = cfg.packs.get(pack_id)
    if pack is None:
        raise UnknownPack(pack_id)
    if idem_key:
        existing = _find_by_key(db, user, idem_key)
        if existing is not None:
            return _replay(db, user, existing)
    pool = _pool(db, user)
    if not pool:
        raise NoMedia()
    # The scarcest cards are only in the dearer packs. (A library with nothing else keeps them all.)
    owned_before = {item.media_id: dict(item.owned) for item in pool}  # every card, also those this pack cannot pull
    barred = market.not_in_pack(db, pack_id)
    pool = [p for p in pool if p.media_id not in barred] or pool
    comp = completeness(db, user)
    dry = dry_packs(db, user)

    row = _take_sealed(db, user, pack_id)
    try:
        if row is None:
            if not buy:
                db.rollback()
                raise NoSealedPack(pack_id)
            cost = eco.pack_cost(cfg, pack, user.packs_opened, comp)
            row = Pack(user_id=user.id, pack_type=pack_id, source="bought", cost=cost,
                       opened_at=datetime.now(timezone.utc))
            db.add(row)
            db.flush()
            debit(db, user, cost, "pack", ref=f"pack:{row.id}")  # raises InsufficientFunds before any changes stick
            user.packs_opened += 1
        if idem_key:
            row.idem_key = idem_key
            db.flush()  # the unique idem_key index rejects a concurrent duplicate here
    except IntegrityError:
        db.rollback()
        winner = _find_by_key(db, user, idem_key) if idem_key else None
        if winner is None:
            if idem_key:
                raise IdempotencyKeyInUse(idem_key)
            raise
        return _replay(db, user, winner)
    free = row.source != "bought"  # the daily free pack or a reward pack: it cost nothing
    cost = row.cost
    before = slot_holdings(db, user)
    rng = rng or random.SystemRandom()
    pulls = roll_pack(cfg, pack, pool, comp, rng, eco.pity_multiplier(cfg, dry))
    db.merge(AppState(key=f"pity_dry:{user.id}", value="0" if ends_dry_run(cfg, pulls) else str(dry + 1)))
    # v0.9.0 (D6): the rarest card is not always last. The order is fixed here and stored with the pack.
    pulls = reveal_order(cfg, pulls, rng)
    by_id = {item.media_id: item for item in pool}
    # Per pull: did it fill an empty version slot, or was its treatment already held while a higher
    # slot of the card is still empty? Worked out card by card, in the order the pulls are applied.
    held = {m: dict(owned_before.get(m, {})) for m in {p.media_id for p in pulls}}
    flags = [eco.pull_flags(cfg, held[p.media_id], shown_treatment(p)) for p in pulls]
    for p in pulls:
        if p.outcome in ("new", "better", "version"):
            _add_copy(db, user, p.media_id, p.treatment)
        elif p.outcome == "upgraded" and p.upgraded_to:
            # The double upgrades your best copy: that copy moves up one treatment.
            best = by_id[p.media_id].best_treatment(cfg)
            if not (best and move_copy_up(db, user, p.media_id, best, p.upgraded_to)):
                _add_copy(db, user, p.media_id, p.upgraded_to)
            by_id[p.media_id].owned[p.upgraded_to] = by_id[p.media_id].owned.get(p.upgraded_to, 0) + 1
        else:
            # A duplicate is another copy: yours to sell or fuse. v0.9.0 (D1): opening a pack never
            # pays coins, so the old `duplicates.keep_copies = false` payout is gone from here too.
            _add_copy(db, user, p.media_id, p.treatment)
    # v0.9.0 (D6): the face-down glow, a hint that can be wrong, decided now and stored with the pack
    raw = [{**p.__dict__, **f, "hint": roll_hint(cfg, f["fills_slot"], rng)} for p, f in zip(pulls, flags)]
    row.contents_json = json.dumps({"pack": pack_id, "cost": cost, "free": free, "source": row.source, "pulls": raw})
    db.flush()  # ownership is now visible to the card queries below
    # v0.9.0 (D1): nothing is paid while a pack is opened. Milestones this pack reached are noted
    # and listed here; the player claims them afterwards (POST /api/economy/milestones/claim).
    milestones = note_milestones(db, user)
    change = slots_change(db, user, before)  # what this pack did to the versions held
    result = _with_cards(db, user, raw)
    db.commit()
    return {"pack_id": pack_id, "cost": cost, "pulls": result, "coins": user.coins, "replayed": False,
            "milestones": milestones, "slots": change, "free": free, "source": row.source,
            "dry_streak": dry_packs(db, user), "inventory": inventory(db, user)}


def sell_card(db: Session, user: User, media_id: int, treatment: str, count: int = 1) -> dict:
    """Sell copies on the market, at today's price. The card returns to the pack pool when the last copy goes."""
    try:
        return market.sell(db, user, media_id, treatment, count)
    except market.NotOwned:
        raise NotOwned() from None


class CannotFuse(Exception):
    pass


def fuse(db: Session, user: User, media_id: int, treatment: str) -> dict:
    """Give up copies of a card at one treatment for one copy a treatment higher.

    How many: `fuse.copies`, plus one per price step the card's Normal market price had reached at
    the last price update (the player's own trades since then don't change the count)."""
    cfg = get_config()
    need = market.Prices(db, [media_id]).fuse_need(media_id)
    if treatment not in cfg.treatments or _is_missing(db, media_id):
        raise CannotFuse("You don't own that card")
    up = eco.next_tier(cfg, treatment)
    if up is None:
        raise CannotFuse("Special is the highest treatment")
    have = db.scalar(select(CollectionEntry.copies).where(
        CollectionEntry.user_id == user.id, CollectionEntry.media_id == media_id, CollectionEntry.treatment == treatment)) or 0
    if have < need:
        raise CannotFuse(f"Fusing needs {need} copies, you have {have}")
    before = slot_holdings(db, user)
    for _ in range(need):
        if not take_copy(db, user, media_id, treatment):  # another request used a copy meanwhile
            db.rollback()
            raise CannotFuse(f"Fusing needs {need} copies")
    _add_copy(db, user, media_id, up)
    db.add(AuditLog(user_id=user.id, event="fuse", detail=f"media:{media_id} {need}x{treatment}->{up}"))
    db.flush()
    milestones = note_milestones(db, user)  # v0.9.0: noted as claimable, not paid
    change = slots_change(db, user, before)
    db.commit()
    # `from` and `used`: the treatment and count consumed; `treatment`: the one gained;
    # `net_versions` (= slots.delta): the real change in versions held, +1, 0 or -1
    return {"treatment": up, "from": treatment, "used": need, "coins": user.coins, "milestones": milestones,
            "net_versions": change["delta"], "slots": change}


def max_affordable(db: Session, user: User, limit: int = MAX_BUY) -> dict[str, int]:
    """How many of each pack the balance buys right now, with the price rising per pack (at most `limit`)."""
    cfg = get_config()
    comp = completeness(db, user)
    out = {}
    for pid, pack in cfg.packs.items():
        coins, n = user.coins, 0
        while n < limit:
            cost = eco.pack_cost(cfg, pack, user.packs_opened + n, comp)
            if coins < cost:
                break
            coins -= cost
            n += 1
        out[pid] = n
    return out


# ---------------------------------------------------------------- history


def history(db: Session, user: User, today: date | None = None, *, packs: int = 8, near: int = 8) -> dict:
    """Recent packs and near misses, plus how many packs were opened today and this week."""
    from ..game import goals as gl

    cfg = get_config()
    today = today or date.today()
    week_start = gl.week_start(today)
    day_start = datetime.combine(today, datetime.min.time()).astimezone(timezone.utc)
    rows = db.scalars(
        select(Pack).where(Pack.user_id == user.id).order_by(Pack.id.desc()).limit(200)
    ).all()
    tier_of = {t.id: t.tier for t in cfg.tiers()}
    recent, misses = [], []
    today_n = week_n = week_misses = 0
    for row in rows:
        data = json.loads(row.contents_json or "{}")
        if not isinstance(data, dict) or "pulls" not in data:
            continue  # still sealed
        when = row.opened_at or row.created_at  # a pack counts for the day it was opened
        at = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        in_week = at >= week_start if week_start.tzinfo else at.replace(tzinfo=None) >= week_start
        shown = [p.get("upgraded_to") if p.get("outcome") == "upgraded" and p.get("upgraded_to") else p["treatment"]
                 for p in data["pulls"]]
        n_miss = sum(1 for p in data["pulls"] if p.get("near_miss"))
        if in_week:
            week_n += 1
            week_misses += n_miss
        if at >= day_start:
            today_n += 1
        if len(recent) < packs:
            counts = {"new": 0, "better": 0, "version": 0, "upgraded": 0, "duplicate": 0}
            for p in data["pulls"]:
                counts[p.get("outcome", "duplicate")] = counts.get(p.get("outcome", "duplicate"), 0) + 1
            best = max((t for t in shown if t in tier_of), key=lambda t: tier_of[t], default=None)
            recent.append({
                "id": row.id, "pack": data.get("pack"), "at": at.isoformat(), "cost": data.get("cost", 0),
                "free": bool(data.get("free")), "cards": len(data["pulls"]), "best": best,
                "near_misses": n_miss, **counts,
            })
        for p, t in zip(data["pulls"], shown):
            if p.get("near_miss") and len(misses) < near:
                misses.append({"media_id": p["media_id"], "treatment": t, "at": at.isoformat()})
    items = db.scalars(select(MediaItem).where(MediaItem.id.in_({m["media_id"] for m in misses}))).all() if misses else []
    cards = {c["id"]: c for c in card_out_many(db, user, items)}
    return {
        "packs_today": today_n,
        "packs_this_week": week_n,
        "near_misses_this_week": week_misses,
        "recent_packs": recent,
        "near_misses": [{**m, "card": cards.get(m["media_id"])} for m in misses if m["media_id"] in cards],
    }
