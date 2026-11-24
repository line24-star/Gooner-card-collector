"""Economy API: config, wallet, ledger, daily check-in, packs, selling, upgrades, goals, sets.

All coin math happens here on the server; the client only displays results (anti-cheat).
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..game import economy as eco
from ..game.config import get_config
from ..models import CollectionEntry, MediaItem, Transaction, media_tags
from ..services import chase, market, offers, portfolio
from ..services import packs as pack_svc
from ..services.rewards import (
    claim_milestones, claim_reply, completeness, daily_checkin, milestones_status, note_milestones, slots, tagged_share,
)
from ..services.wallet import InsufficientFunds, local_user

router = APIRouter(prefix="/api", tags=["economy"])


def card_counts(db: Session, user) -> dict:
    """How many cards exist, how many the player has pulled, and how many of those are tagged."""
    present = select(MediaItem.id).where(MediaItem.missing.is_(False))
    owned = select(CollectionEntry.media_id).where(
        CollectionEntry.user_id == user.id, CollectionEntry.copies > 0, CollectionEntry.media_id.in_(present)
    ).distinct()
    owned_n = db.scalar(select(func.count()).select_from(owned.subquery())) or 0
    tagged_n = db.scalar(
        select(func.count(func.distinct(media_tags.c.media_id))).where(media_tags.c.media_id.in_(owned))
    ) or 0
    by_treatment = dict(db.execute(
        select(CollectionEntry.treatment, func.coalesce(func.sum(CollectionEntry.copies), 0))
        .where(CollectionEntry.user_id == user.id, CollectionEntry.copies > 0, CollectionEntry.media_id.in_(present))
        .group_by(CollectionEntry.treatment)
    ).all())
    return {
        "cards_total": db.scalar(select(func.count()).select_from(present.subquery())) or 0,
        "cards_owned": owned_n,
        "cards_tagged": tagged_n,
        "copies": by_treatment,
    }


def pack_odds(pack, completeness: float = 0.0) -> dict[str, float]:
    """This pack's real chance per card slot for each treatment (the pack's own weights applied)."""
    weights = eco.slot_weights(get_config(), pack, completeness)
    total = sum(w for _, w in weights) or 1
    return {tid: w / total for tid, w in weights}


@router.get("/economy/config")
def economy_config() -> dict:
    """The parts of the tuning the UI needs to display (odds, multipliers, base pack info)."""
    cfg = get_config()
    total = sum(t.weight for t in cfg.treatments.values())
    return {
        "starting_coins": cfg.starting_coins,
        "treatments": [
            {"id": t.id, "name": t.name, "tier": t.tier, "multiplier": t.multiplier,
             "pack_weight": t.weight, "pack_odds": t.weight / total, "value": t.value,
             "completeness_weight": t.completeness_weight}
            for t in cfg.tiers()
        ],
        "completeness_mode": cfg.completeness.mode,
        "limits": {"bulk_max_items": cfg.limits.bulk_max_items},
        # v0.9.0: the tours pay packs once, when all of `tours` are completed
        "onboarding": {"tours": list(cfg.onboarding.tours),
                       "tours_reward": eco.pack_list(cfg, [cfg.onboarding.tours_reward])},
        # v0.9.0: the tagging pause and the reveal numbers a page may want to name
        "tagging": {"burst_tags": cfg.tagging.burst_tags, "burst_seconds": cfg.tagging.burst_seconds,
                    "cooldown_seconds": cfg.tagging.cooldown_seconds,
                    "bulk_multiplier": cfg.tagging.bulk_multiplier},
        "reveal": {"hint_true": cfg.reveal.hint_true, "hint_false": cfg.reveal.hint_false,
                   "rarest_last": cfg.reveal.rarest_last},
        "pity_enabled": cfg.pity.enabled,  # false: every pack has the same odds
        # enabled false (v0.7.1): no upgrade attempts; cards carry upgrade_cost null and fusing is the way up
        "upgrades": {"enabled": cfg.upgrades.enabled, "chance": cfg.upgrades.chance, "cost": cfg.upgrades.cost},
        # v0.8.0: the market numbers a page may want to name
        "market": {
            "listings_per_treatment": cfg.market.listings_per_treatment,
            "sell_rate": cfg.market.sell_rate,
            "buy_markup": cfg.market.buy_markup,
            "wanted_bonus": cfg.market.wanted_bonus,
            "wanted_min_value": cfg.market.wanted_min_value,
        },
        "packs": [
            {"id": p.id, "name": p.name, "blurb": p.blurb, "size": p.size, "base_cost": p.base_cost,
             "hue": p.hue, "odds": pack_odds(p)}
            for p in cfg.packs.values()
        ],
    }


@router.get("/economy/wallet")
def wallet(db: Session = Depends(get_db)) -> dict:
    cfg = get_config()
    user = local_user(db)
    comp = completeness(db, user)
    infl = eco.inflation_multiplier(cfg, user.packs_opened, comp)
    ms_status = milestones_status(db, user)
    db.commit()  # the largest library seen and the milestones reached are remembered
    filled, total = slots(db, user)
    return {
        "coins": user.coins,
        "packs_opened": user.packs_opened,
        "streak_days": user.streak_days,
        "last_active_day": user.last_active_day,
        "completeness": comp,
        # the versions above Normal held now, of three per card (Foil, Holo, Special)
        "slots_filled": filled,
        "slots_total": total,
        "tagged_share": tagged_share(db),
        # the next milestone that still pays, measured against the largest library seen: `progress`
        # is that share, `versions_needed` how many more versions reach it
        # v0.9.0: its `reward` is packs, as [{pack, count}]
        "next_milestone": ms_status["next"],
        # v0.9.0: milestones reached and waiting to be claimed, [{at, reward}], and their packs in all
        "milestones_claimable": ms_status["claimable"],
        "milestones_claimable_packs": ms_status["claimable_packs"],
        "inflation": infl,
        "sell_rate": eco.sell_rate(cfg, infl),
        "pack_prices": pack_svc.current_prices(db, user),
        "inventory": pack_svc.inventory(db, user),
        "pack_max": pack_svc.max_affordable(db, user),
        "pack_exclusives": market.pack_exclusives(db, user),
        "collection_value": market.collection_value(db, user),
        # the cards held now, priced at the last tick against the tick before it
        "collection_change": portfolio.change(db, user),
        "market": market.summary(db),
        "deal": offers.deal(db, user),
        "rush": offers.rush(db, user),
        "offer": market.current_offer(db, user),
        "fuse_copies": cfg.fuse.copies,
        **card_counts(db, user),
        # v0.9.0: packs opened in a row without a Holo or Special. For looks only (the face-down
        # glow grows with it): it changes no odds. Replaces `pity`.
        "dry_streak": pack_svc.dry_packs(db, user),
        "free_pack": pack_svc.free_pack_status(db, user),
        # the check-in pays no coins since v0.9.0: it unlocks the free daily pack
        "can_check_in": user.last_active_day != date.today().isoformat(),
    }


@router.get("/economy/history")
def history(db: Session = Depends(get_db)) -> dict:
    """Packs opened today and this week, the latest packs and the latest near misses."""
    return pack_svc.history(db, local_user(db))


@router.get("/packs/inventory")
def pack_inventory(db: Session = Depends(get_db)) -> dict:
    """Sealed packs the player owns, per pack id."""
    return {"inventory": pack_svc.inventory(db, local_user(db))}


@router.post("/packs/free/claim")
def claim_free_pack(db: Session = Depends(get_db)) -> dict:
    """Put today's free pack, sealed, into the inventory (after the daily check-in). 409 when there is none."""
    user = local_user(db)
    try:
        return pack_svc.claim_free_pack(db, user)
    except pack_svc.NoFreePack:
        raise HTTPException(409, "No free pack right now: it is one per day, after the daily check-in")


class BuyRequest(BaseModel):
    count: int = 1
    deal: bool = False  # buy at the shop deal's price


@router.post("/packs/{pack_id}/buy")
def buy_packs(
    pack_id: str,
    body: BuyRequest | None = None,
    idempotency_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Buy sealed packs into the inventory. All or nothing: 402 when the coins don't cover every pack."""
    if idempotency_key is not None and len(idempotency_key) > 58:
        raise HTTPException(400, "Idempotency-Key must be at most 58 characters")
    user = local_user(db)
    try:
        body = body or BuyRequest()
        return pack_svc.buy_packs(db, user, pack_id, body.count, idem_key=idempotency_key or None, use_deal=body.deal)
    except pack_svc.NoDeal:
        raise HTTPException(409, "That deal is over or used up")
    except pack_svc.UnknownPack:
        raise HTTPException(404, f"Unknown pack {pack_id!r}")
    except ValueError as e:
        raise HTTPException(422, str(e))
    except pack_svc.NoMedia:
        raise HTTPException(409, "No media in the collection yet")
    except InsufficientFunds as e:
        db.rollback()
        raise HTTPException(402, {"message": "Not enough coins", "needed": e.needed, "balance": e.balance})


@router.get("/economy/transactions")
def transactions(limit: int = 30, db: Session = Depends(get_db)) -> list[dict]:
    user = local_user(db)
    rows = db.scalars(
        # coin changes only: a pack reward is recorded with amount 0 (v0.9.0) and is not listed
        select(Transaction).where(Transaction.user_id == user.id, Transaction.amount != 0)
        .order_by(Transaction.id.desc()).limit(max(1, min(limit, 200)))
    ).all()
    return [{"id": t.id, "amount": t.amount, "reason": t.reason, "ref": t.ref,
             "balance_after": t.balance_after, "created_at": t.created_at.isoformat()} for t in rows]


@router.post("/economy/checkin")
def checkin(db: Session = Depends(get_db)) -> dict:
    """The daily check-in. v0.9.0: it pays no coins. It moves the streak on and unlocks today's
    free pack (`free_pack`, as in the wallet). `checked_in`: this call was today's check-in."""
    user = local_user(db)
    done = daily_checkin(db, user)
    milestones = note_milestones(db, user)
    db.commit()
    return {"checked_in": done, "streak_days": user.streak_days, "milestones": milestones, "coins": user.coins,
            "free_pack": pack_svc.free_pack_status(db, user)}


@router.post("/economy/milestones/claim")
def claim_milestones_now(db: Session = Depends(get_db)) -> dict:
    """Take the packs of every completeness milestone that is reached and not yet claimed (v0.9.0).
    409 when none waits. Answers the claim reply plus `milestones` (the shares claimed)."""
    user = local_user(db)
    out = claim_milestones(db, user)
    if not out["milestones"]:
        db.rollback()
        raise HTTPException(409, "No milestone to claim")
    db.commit()
    return claim_reply(db, user, out["packs"], milestones=out["milestones"])


@router.post("/packs/{pack_id}/open")
def open_pack(
    pack_id: str,
    buy: bool = False,
    idempotency_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Open one sealed pack of this kind from the inventory. `?buy=true`: with none sealed, buy one
    at today's price and open it. Without it, an empty inventory answers 409."""
    if idempotency_key is not None and len(idempotency_key) > 64:
        raise HTTPException(400, "Idempotency-Key must be at most 64 characters")
    user = local_user(db)
    try:
        return pack_svc.open_pack(db, user, pack_id, idem_key=idempotency_key or None, buy=buy)
    except pack_svc.UnknownPack:
        raise HTTPException(404, f"Unknown pack {pack_id!r}")
    except pack_svc.NoSealedPack:
        raise HTTPException(409, {"message": "You have no sealed pack of that kind", "no_sealed": True})
    except pack_svc.IdempotencyKeyInUse:
        raise HTTPException(409, "Idempotency-Key already used")
    except pack_svc.NoMedia:
        raise HTTPException(409, "No media in the collection yet")
    except InsufficientFunds as e:
        db.rollback()
        raise HTTPException(402, {"message": "Not enough coins", "needed": e.needed, "balance": e.balance})


class SellRequest(BaseModel):
    treatment: str
    count: int = 1


class FuseRequest(BaseModel):
    treatment: str


@router.post("/cards/{media_id}/fuse")
def fuse(media_id: int, body: FuseRequest, db: Session = Depends(get_db)) -> dict:
    """Give up copies of this card at one treatment for one copy a treatment higher. How many: the
    card's `fuse_copies` (3, more for a card that is worth more). 409 with the reason when it can't be done."""
    try:
        return pack_svc.fuse(db, local_user(db), media_id, body.treatment)
    except pack_svc.CannotFuse as e:
        raise HTTPException(409, str(e))


@router.post("/economy/rush")
def start_rush(db: Session = Depends(get_db)) -> dict:
    """Start today's tag rush: tags pay more for a few minutes. 409 when it was already used today,
    is running, or the last one started less than `tagging.rush_gap_hours` ago."""
    try:
        return offers.start_rush(db, local_user(db))
    except offers.RushUnavailable:
        raise HTTPException(409, "The tag rush is not ready yet")


@router.post("/cards/{media_id}/sell")
def sell(media_id: int, body: SellRequest, db: Session = Depends(get_db)) -> dict:
    user = local_user(db)
    try:
        return pack_svc.sell_card(db, user, media_id, body.treatment, max(1, min(body.count, 500)))
    except pack_svc.NotOwned:
        raise HTTPException(404, "You don't own that card")


@router.post("/cards/{media_id}/upgrade")
def upgrade(media_id: int, db: Session = Depends(get_db)) -> dict:
    """One upgrade attempt: pays the cost; on success the card moves up one treatment."""
    user = local_user(db)
    try:
        return chase.attempt_upgrade(db, user, media_id)
    except chase.CannotUpgrade as e:
        raise HTTPException(409, str(e))
    except InsufficientFunds as e:
        db.rollback()
        raise HTTPException(402, {"message": "Not enough coins", "needed": e.needed, "balance": e.balance})


@router.get("/economy/goals")
def goals(db: Session = Depends(get_db)) -> dict:
    return chase.weekly_goals(db, local_user(db))


@router.post("/economy/goals/{goal_id}/claim")
def claim_goal(goal_id: str, db: Session = Depends(get_db)) -> dict:
    user = local_user(db)
    try:
        return chase.claim_goal(db, user, goal_id)  # {packs, inventory, coins}
    except chase.NotClaimable:
        raise HTTPException(409, "Goal not complete, already claimed, or not active this week")


@router.get("/economy/chase")
def chase_overview(db: Session = Depends(get_db)) -> dict:
    """Everything to chase in one reply: today's goals, this week's goals, mastery and the next goal."""
    return chase.overview(db, local_user(db))


class GoalClaim(BaseModel):
    scope: Literal["daily", "weekly"]
    id: str


@router.post("/economy/chase/claim")
def claim_chase_goal(body: GoalClaim, db: Session = Depends(get_db)) -> dict:
    """Claim a finished goal of today ("daily") or of this week ("weekly")."""
    user = local_user(db)
    try:
        return chase.claim_goal(db, user, body.id, period="day" if body.scope == "daily" else "week")
    except chase.NotClaimable:
        raise HTTPException(409, "Goal not complete, already claimed, or not one of the current goals")


@router.post("/economy/mastery/claim")
def claim_mastery(db: Session = Depends(get_db)) -> dict:
    """Take the packs of every mastery level reached and not yet claimed. 409 when there is none.
    Answers the claim reply plus `levels`."""
    user = local_user(db)
    try:
        return chase.claim_mastery(db, user)
    except chase.NotClaimable:
        raise HTTPException(409, "No mastery level to claim")


@router.get("/economy/sets")
def sets(db: Session = Depends(get_db)) -> list[dict]:
    return chase.category_sets(db, local_user(db))


class SetClaim(BaseModel):
    treatment: str


@router.post("/economy/sets/{tag_id}/claim")
def claim_set(tag_id: int, body: SetClaim, db: Session = Depends(get_db)) -> dict:
    user = local_user(db)
    try:
        return chase.claim_set(db, user, tag_id, body.treatment)  # {packs, inventory, coins}
    except chase.NotClaimable:
        raise HTTPException(409, "Set not complete or already claimed")
