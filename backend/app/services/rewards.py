"""Rewards, server-side and idempotent.

v0.9.0 (decision D1): coins are earned only by scanning (`reward_ingest`) and tagging
(`reward_tag`, and `reward_review`: looking at a tagged card again after pulling it in a rarer
treatment is tagging work). Every other reward is sealed packs (`grant_packs`): milestones here,
goals, collector levels and sets in services/chase.py, the checklist and the tours in
services/onboarding.py. The daily check-in pays nothing; it unlocks the free daily pack.
Every reward uses a dedupe key, so repeating an action never pays twice.
"""

import json
import math
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..game import economy as eco
from ..game.config import get_config
from ..models import AppState, CollectionEntry, MediaItem, Pack, TagSlot, Transaction, User, media_tags
from .wallet import already_paid, credit, mark_claimed, take_back


# ---------------------------------------------------------------- pack rewards (v0.9.0)


def inventory(db: Session, user: User) -> dict[str, int]:
    """Sealed packs the player owns, per pack id (every pack id is listed, 0 when none)."""
    counts = dict.fromkeys(get_config().packs, 0)
    rows = db.execute(
        select(Pack.pack_type, func.count())
        .where(Pack.user_id == user.id, Pack.pack_type.is_not(None), Pack.opened_at.is_(None))
        .group_by(Pack.pack_type)
    ).all()
    for pack_type, n in rows:
        counts[pack_type] = n
    return counts


def grant_packs(db: Session, user: User, packs: list[dict], source: str) -> list[dict]:
    """Put reward packs, sealed, into the inventory. `packs` is `[{pack, count}]`; `source` says
    what they are for (stored on the pack, at most 16 characters). A reward pack costs nothing and
    does not raise pack prices, like the free daily pack. The caller commits. Returns what was
    given (unknown pack ids are left out)."""
    cfg = get_config()
    given = []
    for p in packs:
        if p["pack"] not in cfg.packs or p["count"] <= 0:
            continue
        for _ in range(p["count"]):
            db.add(Pack(user_id=user.id, pack_type=p["pack"], source=source[:16], cost=0))
        given.append({"pack": p["pack"], "count": p["count"]})
    db.flush()
    return given


def claim_reply(db: Session, user: User, packs: list[dict], **more) -> dict:
    """What every claim answers (v0.9.0): the packs given, the inventory after it, and the coins
    (unchanged: no claim pays coins)."""
    return {"packs": packs, "inventory": inventory(db, user), "coins": user.coins, **more}

# The highest number of present files the library ever had (app_state). Milestones are measured
# against it, so taking files out of the folder can't raise the share that pays (v0.7.1).
LIBRARY_PEAK_KEY = "library_peak"


def tagged_share(db: Session) -> float:
    total = db.scalar(select(func.count(MediaItem.id)).where(MediaItem.missing.is_(False))) or 0
    if not total:
        return 0.0
    tagged = (
        db.scalar(
            select(func.count(func.distinct(media_tags.c.media_id)))
            .join(MediaItem, MediaItem.id == media_tags.c.media_id)
            .where(MediaItem.missing.is_(False))
        )
        or 0
    )
    return tagged / total


def slots(db: Session, user: User) -> tuple[int, int]:
    """(filled, total) completeness slots: one per present card and treatment above Normal.

    A slot is filled while the player holds at least one copy of that treatment. Missing files
    have no slots, and their copies don't count."""
    cfg = get_config()
    kinds = eco.slot_treatments(cfg)
    media = db.scalar(select(func.count(MediaItem.id)).where(MediaItem.missing.is_(False))) or 0
    filled = db.scalar(
        select(func.count(CollectionEntry.id))
        .join(MediaItem, MediaItem.id == CollectionEntry.media_id)
        .where(CollectionEntry.user_id == user.id, CollectionEntry.copies > 0,
               CollectionEntry.treatment.in_(kinds), MediaItem.missing.is_(False))
    ) or 0
    return filled, eco.slots_total(cfg, media)


def slot_holdings(db: Session, user: User) -> set[tuple[int, str]]:
    """The version slots filled right now, as (media id, treatment) pairs: the same slots `slots`
    counts. Taken before an action, it is what `slots_change` compares against afterwards."""
    kinds = eco.slot_treatments(get_config())
    return {
        (media_id, treatment)
        for media_id, treatment in db.execute(
            select(CollectionEntry.media_id, CollectionEntry.treatment)
            .join(MediaItem, MediaItem.id == CollectionEntry.media_id)
            .where(CollectionEntry.user_id == user.id, CollectionEntry.copies > 0,
                   CollectionEntry.treatment.in_(kinds), MediaItem.missing.is_(False))
        )
    }


def slots_change(db: Session, user: User, before: set[tuple[int, str]], total_before: int | None = None) -> dict:
    """What an action did to the versions held: the one source for every response that can change
    ownership (v0.8.0). `before` is `slot_holdings` from before the action; call this after the
    action's changes are flushed.

    filled / total: the slots now (as in the wallet). delta: slots filled minus slots emptied by
    the action. filled_now / emptied: which ones, as {media_id, treatment}. total_delta: how much
    the total moved (only a scan changes it; 0 unless `total_before` is given)."""
    cfg = get_config()
    tier = {t.id: t.tier for t in cfg.tiers()}
    after = slot_holdings(db, user)
    filled, total = slots(db, user)

    def rows(pairs: set[tuple[int, str]]) -> list[dict]:
        return [{"media_id": m, "treatment": t} for m, t in sorted(pairs, key=lambda p: (p[0], tier.get(p[1], 0)))]

    gained, lost = after - before, before - after
    return {
        "filled": filled,
        "total": total,
        "delta": len(gained) - len(lost),
        "filled_now": rows(gained),
        "emptied": rows(lost),
        "total_delta": total - total_before if total_before is not None else 0,
    }


def library_peak(db: Session) -> int:
    """The largest library seen: the highest count of present files, remembered for good.

    Raised (and flushed, the caller commits) whenever the present count is above what is stored.
    It never goes down: not when files go missing, not when a smaller folder is chosen."""
    present = db.scalar(select(func.count(MediaItem.id)).where(MediaItem.missing.is_(False))) or 0
    row = db.get(AppState, LIBRARY_PEAK_KEY)
    try:
        stored = int(row.value) if row is not None and row.value else 0
    except ValueError:
        stored = 0
    if present > stored:
        if row is None:
            db.add(AppState(key=LIBRARY_PEAK_KEY, value=str(present)))
        else:
            row.value = str(present)
        db.flush()
        return present
    return stored


def milestone_progress(db: Session, user: User) -> tuple[float, int, int]:
    """(share, filled, total) as the milestones count it: the versions held now against three per
    card of the largest library seen. Equal to completeness while no file ever went missing.

    Other completeness modes ("weighted", "tagged") have no slots: they use plain completeness."""
    cfg = get_config()
    if cfg.completeness.mode != "slots":
        return completeness(db, user), 0, 0
    filled, _ = slots(db, user)
    total = eco.slots_total(cfg, library_peak(db))
    return eco.slots_completeness(filled, total), filled, total


def next_milestone(db: Session, user: User) -> dict | None:
    """The next milestone still to reach, for the wallet: where it is, what it pays (`reward`:
    packs, as `[{pack, count}]`), and how far
    away it is on the milestones' own measure (`progress`, and `versions_needed` more versions
    against `cards` cards). The UI words the distance from these, never from the shown
    completeness, so it can't promise a milestone that would not pay."""
    cfg = get_config()
    share, filled, total = milestone_progress(db, user)
    nm = eco.next_milestone(cfg, share, paid_milestones(db, user) | reached_milestones(db, user))
    if nm is None:
        return None
    out = {"at": nm[0], "reward": nm[1], "progress": share}
    if total:
        kinds = max(1, len(eco.slot_treatments(cfg)))
        out["versions_needed"] = max(1, math.ceil(nm[0] * total - 1e-9) - filled)
        out["cards"] = total // kinds
    return out


def completeness(db: Session, user: User) -> float:
    """Collection completeness per config: by default the share of filled slots (see `slots`)."""
    cfg = get_config()
    if cfg.completeness.mode == "slots":
        return eco.slots_completeness(*slots(db, user))
    tagged = set(db.scalars(select(media_tags.c.media_id).distinct()).all())
    best: dict[int, str] = {}
    rows = db.execute(
        select(CollectionEntry.media_id, CollectionEntry.treatment).where(
            CollectionEntry.user_id == user.id, CollectionEntry.copies > 0
        )
    ).all()
    for media_id, t in rows:
        if t not in cfg.treatments:
            continue
        if media_id not in best or cfg.treatments[t].tier > cfg.treatments[best[media_id]].tier:
            best[media_id] = t
    ids = db.scalars(select(MediaItem.id).where(MediaItem.missing.is_(False))).all()
    return eco.collection_completeness(cfg, [(m in tagged, best.get(m)) for m in ids])


def is_shinier(treatment: str) -> bool:
    """Above the lowest treatment: only such a copy can be reviewed."""
    cfg = get_config()
    return treatment in cfg.treatments and cfg.treatments[treatment].tier > cfg.tiers()[0].tier


def best_owned(db: Session, user: User, media_id: int) -> str | None:
    cfg = get_config()
    rows = db.scalars(
        select(CollectionEntry.treatment).where(
            CollectionEntry.user_id == user.id, CollectionEntry.media_id == media_id, CollectionEntry.copies > 0
        )
    ).all()
    known = [t for t in rows if t in cfg.treatments]
    return max(known, key=lambda t: cfg.treatments[t].tier) if known else None


def ingest_today(db: Session, user: User) -> tuple[int, int]:
    """(coins, files) the scans of the player's local day have paid so far.

    "Today" is the player's local day. Timestamps are stored in UTC, so local midnight is converted
    (stamping the local date as UTC skipped the cap for the hours the two dates differ)."""
    start = datetime.combine(date.today(), time.min).astimezone(timezone.utc)
    paid, files = db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0), func.count(Transaction.id)).where(
            Transaction.user_id == user.id, Transaction.reason == "ingest", Transaction.created_at >= start
        )
    ).one()
    return paid or 0, files or 0


def ingest_day_status(db: Session, user: User) -> dict:
    """Today's scan payouts against the day's limits, for the scan report."""
    ing = get_config().ingest
    paid, files = ingest_today(db, user)
    return {"paid": paid, "cap": ing.daily_cap, "files": files,
            "full_files": ing.full_files_per_day, "half_files": ing.half_files_per_day}


def reward_ingest(db: Session, user: User, media: MediaItem, *, initial_scan: bool) -> int:
    cfg = get_config()
    if initial_scan and not cfg.ingest.reward_initial_scan:
        return 0  # the free first import: nothing to look up, so a big library imports fast
    paid_today, files_today = ingest_today(db, user)
    amount = eco.ingest_reward(cfg, media.kind, initial_scan, paid_today or 0, files_today or 0)
    tx = credit(db, user, amount, "ingest", dedupe_key=f"ingest:{media.sha256}", ref=f"media:{media.id}")
    return tx.amount if tx else 0


def _tag_slots(db: Session, user: User, media_id: int) -> list[TagSlot]:
    """The card's paid-tag slots, lowest first. A card tagged before v0.9.0 has none stored yet:
    they are made from its ledger rows (one "tag" payout per slot, in the order they were paid)."""
    rows = db.scalars(
        select(TagSlot).where(TagSlot.user_id == user.id, TagSlot.media_id == media_id).order_by(TagSlot.slot)
    ).all()
    if rows:
        return list(rows)
    paid = db.scalars(
        select(Transaction).where(
            Transaction.user_id == user.id, Transaction.reason == "tag", Transaction.ref == f"media:{media_id}"
        ).order_by(Transaction.id)
    ).all()
    out = []
    for i, tx in enumerate(paid):
        try:
            tag_id = int((tx.dedupe_key or "").rsplit(":", 1)[1])
        except (IndexError, ValueError):
            tag_id = -tx.id  # a payout without a readable key keeps its slot, held by no real tag
        row = TagSlot(user_id=user.id, media_id=media_id, slot=i, tag_id=tag_id, amount=tx.amount, paid_at=tx.created_at)
        db.add(row)
        out.append(row)
    if out:
        db.flush()
    return out


def next_tag_slots(db: Session, user: User, media_ids: list[int]) -> dict[int, tuple[int, int | None]]:
    """For each card: (the slot its next tag takes, the most that tag can be paid or None for no
    limit). The slot is the lowest free one (a removed tag left it, with what was taken back as
    the limit), else the next new one. Read-only: cards without stored slots count their ledger rows."""
    out: dict[int, tuple[int, int | None]] = {}
    if not media_ids:
        return out
    stored: dict[int, list[TagSlot]] = {}
    for row in db.scalars(select(TagSlot).where(TagSlot.user_id == user.id, TagSlot.media_id.in_(media_ids))):
        stored.setdefault(row.media_id, []).append(row)
    for media_id, rows in stored.items():
        free = [r for r in rows if r.tag_id is None]
        if free:
            first = min(free, key=lambda r: r.slot)
            out[media_id] = (first.slot, first.amount)
        else:
            out[media_id] = (len(rows), None)
    rest = [i for i in media_ids if i not in out]
    if rest:
        refs = {f"media:{i}": i for i in rest}
        counts = dict(db.execute(
            select(Transaction.ref, func.count(Transaction.id))
            .where(Transaction.user_id == user.id, Transaction.reason == "tag", Transaction.ref.in_(list(refs)))
            .group_by(Transaction.ref)
        ).all())
        for ref, i in refs.items():
            out[i] = (counts.get(ref, 0), None)
    return out


def reward_tag(db: Session, user: User, media: MediaItem, tag_id: int, *, pay: bool = True,
               factor: float = 1.0) -> int:
    """Call after a tag is applied. Pays once per (media, tag), with diminishing returns per card.

    The tag takes the card's lowest free slot (v0.9.0, see TagSlot): after a removal that is the
    removed tag's slot, paid at today's rate for that slot but never more than was taken back.
    `pay=False` (the tagging pause): the tag pays nothing and takes no slot.
    `factor` (bulk tagging: `tagging.bulk_multiplier`): the share of the rate that is paid, rounded
    down, at least 1 coin for a tag that pays at all."""
    cfg = get_config()
    key = f"tag:{media.id}:{tag_id}"
    if not pay or already_paid(db, key):
        return 0
    slots = _tag_slots(db, user, media.id)
    if any(s.tag_id == tag_id for s in slots):
        return 0
    free = min((s for s in slots if s.tag_id is None), key=lambda s: s.slot, default=None)
    index = free.slot if free is not None else len(slots)
    best = best_owned(db, user, media.id)
    mult = cfg.treatments[best].multiplier if best else 1.0
    from .offers import rush_active  # late: offers imports this module

    amount = eco.tag_reward(cfg, index, mult, owned=best is not None, rush=rush_active(db, user))
    if factor < 1 and amount > 0:
        amount = max(1, int(amount * factor))
    if free is not None:
        amount = min(amount, free.amount)
    elif amount <= 0:
        return 0  # past the paid tags: no slot, nothing to take back later
    tx = credit(db, user, amount, "tag", dedupe_key=key, ref=f"media:{media.id}")
    paid = tx.amount if tx else 0
    if free is not None:
        if tx is not None:
            tx.created_at = free.paid_at  # the slot was earned then: it counts for that day's goals
        free.tag_id, free.amount = tag_id, paid
    else:
        db.add(TagSlot(user_id=user.id, media_id=media.id, slot=index, tag_id=tag_id, amount=paid))
    db.flush()
    return paid


def refund_tag(db: Session, user: User, media: MediaItem, tag_id: int) -> int:
    """Call when a tag is removed (v0.9.0): takes back what that tag paid on that card, as far as
    the balance goes (never below zero), and returns what was taken.

    The tag's slot is left free, and the next tag on the card is paid at most what was taken back
    here. The payout's ledger row is kept as "tag_removed" and its key is released, so it no longer
    counts as a paid tag (not for the card, not for a goal) and the same tag can be paid again."""
    slot = next((s for s in _tag_slots(db, user, media.id) if s.tag_id == tag_id), None)
    if slot is None:
        return 0  # this tag was never paid
    taken = take_back(db, user, slot.amount, "tag_refund", ref=f"media:{media.id}")
    tx = db.scalar(select(Transaction).where(Transaction.dedupe_key == f"tag:{media.id}:{tag_id}"))
    if tx is not None:
        tx.reason, tx.dedupe_key = "tag_removed", None
    slot.tag_id, slot.amount = None, taken
    db.flush()
    return taken


def _naive(d: datetime) -> datetime:
    """SQLite hands datetimes back naive (UTC); normalize so every comparison is like for like."""
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def first_tag_times(db: Session, user: User, media_ids: list[int]) -> dict[int, datetime]:
    """When each media was first tagged by this user (the earliest "tag" payout), in one query.

    Media with no tag transaction (tagged before rewards existed, or tags that paid 0) are absent:
    callers treat them as tagged at the beginning of time.
    """
    if not media_ids:
        return {}
    rows = db.execute(
        select(Transaction.ref, func.min(Transaction.created_at))
        .where(
            Transaction.user_id == user.id,
            Transaction.reason == "tag",
            Transaction.ref.in_([f"media:{i}" for i in media_ids]),
        )
        .group_by(Transaction.ref)
    ).all()
    return {int(ref.split(":", 1)[1]): _naive(at) for ref, at in rows}


def obtained_after_tag(first_pulled_at: datetime, tagged_at: datetime | None) -> bool:
    return tagged_at is None or _naive(first_pulled_at) > tagged_at


def review_eligible_treatment(db: Session, user: User, media_id: int) -> str | None:
    """The treatment that may be reviewed now, or None. Ignores whether it was already paid.

    Rule: the best owned treatment of a tagged card, and only if that copy was obtained
    after the card was first tagged. The lowest treatment is never "shinier": a Normal copy that
    was sold and came back pays no review (v0.7.1).
    """
    best = best_owned(db, user, media_id)
    if best is None or not is_shinier(best):
        return None
    if not db.scalar(select(media_tags.c.media_id).where(media_tags.c.media_id == media_id).limit(1)):
        return None  # nothing to re-organize yet: tag it first (that pays the multiplier anyway)
    pulled = db.scalar(
        select(CollectionEntry.first_pulled_at).where(
            CollectionEntry.user_id == user.id,
            CollectionEntry.media_id == media_id,
            CollectionEntry.treatment == best,
        )
    )
    tagged_at = first_tag_times(db, user, [media_id]).get(media_id)
    return best if pulled is not None and obtained_after_tag(pulled, tagged_at) else None


def reward_review(db: Session, user: User, media_id: int, treatment: str) -> int:
    """Re-organize a tagged card after getting it in a shinier treatment: once per (media, treatment)."""
    cfg = get_config()
    if review_eligible_treatment(db, user, media_id) != treatment:
        return 0
    amount = eco.review_reward(cfg, cfg.treatments[treatment].multiplier)
    tx = credit(db, user, amount, "review", dedupe_key=f"review:{media_id}:{treatment}", ref=f"media:{media_id}")
    return tx.amount if tx else 0


def daily_checkin(db: Session, user: User, today: date | None = None) -> bool:
    """Check in for today: the streak moves on. Pays nothing since v0.9.0 (it unlocks the free
    daily pack). True when this was today's check-in, False when today was already checked in."""
    today = today or date.today()
    last = date.fromisoformat(user.last_active_day) if user.last_active_day else None
    streak, claimable = eco.next_streak(user.streak_days, last, today)
    if not claimable:
        return False
    user.streak_days = streak
    user.last_active_day = today.isoformat()
    db.flush()
    return True


def paid_milestones(db: Session, user: User) -> set[float]:
    """The milestones that already paid (each pays once, even when completeness falls and rises again)."""
    cfg = get_config()
    keys = {f"milestone:{user.id}:{m.at}": m.at for m in cfg.milestones}
    rows = db.scalars(select(Transaction.dedupe_key).where(Transaction.dedupe_key.in_(list(keys)))).all()
    return {keys[k] for k in rows}


def _reached_key(user: User) -> str:
    return f"milestones_reached:{user.id}"


def reached_milestones(db: Session, user: User) -> set[float]:
    """The milestones noted as reached (see `note_milestones`), claimed or not."""
    row = db.get(AppState, _reached_key(user))
    try:
        return {float(x) for x in json.loads(row.value)} if row is not None and row.value else set()
    except (ValueError, TypeError):
        return set()


def note_milestones(db: Session, user: User) -> list[dict]:
    """Note every completeness milestone reached right now, and return the ones that just became
    claimable: `[{at, reward}]` with `reward` as `[{pack, count}]`.

    v0.9.0: a milestone pays nothing by itself. It is noted here (after anything that can raise
    completeness: a pack, a fuse, a purchase) and stays claimable even when completeness falls
    again; the player claims it with `claim_milestones`. Reached is measured against the largest
    library seen (`milestone_progress`), so files that go missing never reach a milestone.
    The caller commits."""
    cfg = get_config()
    comp = milestone_progress(db, user)[0]
    reached = reached_milestones(db, user)
    paid = paid_milestones(db, user)
    new = [m for m in cfg.milestones if m.at <= comp + 1e-9 and m.at not in reached and m.at not in paid]
    if new:
        db.merge(AppState(key=_reached_key(user), value=json.dumps(sorted(reached | {m.at for m in new}))))
        db.flush()
    return [{"at": m.at, "reward": eco.pack_list(cfg, [m.reward])} for m in new]


def milestones_status(db: Session, user: User) -> dict:
    """The milestones for the wallet and the chase overview: `claimable` (reached, not claimed, as
    `[{at, reward}]`), `claimable_packs` (their packs in all) and `next` (see `next_milestone`)."""
    cfg = get_config()
    note_milestones(db, user)
    reached, paid = reached_milestones(db, user), paid_milestones(db, user)
    waiting = [m for m in cfg.milestones if m.at in reached and m.at not in paid]
    return {
        "claimable": [{"at": m.at, "reward": eco.pack_list(cfg, [m.reward])} for m in waiting],
        "claimable_packs": eco.pack_list(cfg, [m.reward for m in waiting]),
        "next": next_milestone(db, user),
    }


def claim_milestones(db: Session, user: User) -> dict:
    """Give the packs of every milestone that is reached and not yet claimed (idempotent).
    Returns {"milestones": [share, ...], "packs": [{pack, count}]}; both empty when nothing waits.
    The caller commits."""
    cfg = get_config()
    note_milestones(db, user)
    reached = reached_milestones(db, user)
    claimed, rewards = [], []
    for m in cfg.milestones:
        if m.at in reached and mark_claimed(db, user, "milestone", dedupe_key=f"milestone:{user.id}:{m.at}"):
            claimed.append(m.at)
            rewards.append(m.reward)
    packs = grant_packs(db, user, eco.pack_list(cfg, rewards), "milestone")
    return {"milestones": claimed, "packs": packs}
