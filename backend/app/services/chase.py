"""Late-game chase: upgrade attempts, weekly and daily goals, mastery, the next goal, category sets
(decision 2026-11-10b; daily goals, mastery and the next goal: v0.6.0)."""

import json
import random
from datetime import date, datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..game import economy as eco
from ..game import goals as gl
from ..game import mastery as ms
from ..game.config import get_config
from ..models import AppState, AuditLog, CollectionEntry, MediaItem, Pack, SetCredit, Tag, Transaction, User, media_tags
from .rewards import (
    best_owned,
    claim_reply,
    grant_packs,
    milestone_progress,
    milestones_status,
    next_tag_slots,
    note_milestones,
    paid_milestones,
    reached_milestones,
    review_eligible_treatment,
    slot_holdings,
    slots_change,
)
from .wallet import already_paid, debit, mark_claimed


class CannotUpgrade(Exception):
    pass


class NotClaimable(Exception):
    pass


# ---------------------------------------------------------------- upgrade attempts


def attempt_upgrade(db: Session, user: User, media_id: int, rng: random.Random | None = None) -> dict:
    """Spend coins for a chance to move a card's best copy up one treatment.

    v0.7.1: refused while `upgrades.enabled` is off (the live config): fusing is the one way up."""
    cfg = get_config()
    if not cfg.upgrades.enabled:
        raise CannotUpgrade("Upgrade attempts are switched off. Fuse copies to get a rarer one.")
    current = best_owned(db, user, media_id)
    if current is None:
        raise CannotUpgrade("You don't own that card")
    missing = db.scalar(select(MediaItem.missing).where(MediaItem.id == media_id))
    if missing is None or missing:
        raise CannotUpgrade("That card's file is missing")
    from . import market  # late: market imports packs, which imports this package's rewards

    need = market.Prices(db, [media_id]).fuse_need(media_id)
    cost = eco.upgrade_cost(cfg, current, need)
    if cost is None:
        raise CannotUpgrade("This card can't be upgraded further")
    before = slot_holdings(db, user)
    success = (rng or random.SystemRandom()).random() < cfg.upgrades.chance
    debit(db, user, cost, "upgrade_attempt", ref=f"media:{media_id}:{'ok' if success else 'fail'}")
    new = current
    milestones: list[dict] = []
    if success:
        from .packs import move_copy_up  # late: packs imports this package's rewards too

        new = eco.next_tier(cfg, current)
        if not move_copy_up(db, user, media_id, current, new):  # a parallel request used the copy
            db.rollback()
            raise CannotUpgrade("You don't own that card")
        db.flush()
        milestones = note_milestones(db, user)  # a rarer copy raises completeness
    change = slots_change(db, user, before)
    db.commit()
    return {"success": success, "cost": cost, "treatment": new, "coins": user.coins, "milestones": milestones,
            "slots": change}


# ---------------------------------------------------------------- weekly and daily goals


def counters(db: Session, user: User, since: datetime) -> gl.Counters:
    """What the player did since `since` (a UTC moment: the start of the week or of the local day)."""
    cfg = get_config()
    c = gl.Counters()
    # A pack counts for the period it was OPENED in (it may have been bought, sealed, a week earlier).
    opened_at = func.coalesce(Pack.opened_at, Pack.created_at)
    for raw in db.scalars(select(Pack.contents_json).where(Pack.user_id == user.id, opened_at >= since)):
        data = json.loads(raw or "{}")
        if "pulls" not in data:
            continue
        c.opens += 1
        for p in data["pulls"]:
            if p["treatment"] in cfg.treatments:
                c.add_pull(cfg.treatments[p["treatment"]].tier)
            if p["outcome"] == "upgraded":
                c.upgrades += 1
    rows = db.execute(
        select(Transaction.reason, Transaction.ref, Transaction.amount).where(
            Transaction.user_id == user.id, Transaction.created_at >= since
        )
    ).all()
    for reason, ref, amount in rows:
        if reason == "tag":
            c.tags += 1
        elif reason == "review":
            c.reviews += 1
        elif reason == "upgrade_attempt" and ref.endswith(":ok"):
            c.upgrades += 1
        elif reason in ("sell", "offer"):  # one row per copy sold; an accepted offer is a sale too
            c.sells += 1
            c.market_coins += amount
        elif reason == "market_buy":
            c.buys += 1
    c.fuses = db.scalar(select(func.count(AuditLog.id)).where(
        AuditLog.user_id == user.id, AuditLog.event == "fuse", AuditLog.created_at >= since)) or 0
    c.upgrades += c.fuses  # fusing copies into a rarer one is an upgrade too (v0.4.0)
    return c


def week_counters(db: Session, user: User, today: date) -> gl.Counters:
    return counters(db, user, gl.week_start(today))


def day_counters(db: Session, user: User, today: date) -> gl.Counters:
    """Since local midnight of the player's calendar day."""
    return counters(db, user, gl.day_start(today))


def blocked_kinds(db: Session, user: User, period: str = "week") -> set[str]:
    """Goal kinds the player cannot make progress on right now. Asked once, when a period's goals are picked.

    tag: no owned card has a paying tag left. review: every owned card is already in the top
    treatment (also: nothing owned), so no shinier copy can come, and no review is waiting.
    buy: the balance doesn't cover the cheapest listed card.
    Pulls, opens, sales and fuses are always possible in principle (they only need packs). So is
    "upgrade", which counts fuses (v0.7.1: it was blocked with every card at Special, although a
    card's lower versions can still be fused).
    """
    cfg = get_config()
    present = set(db.scalars(select(MediaItem.id).where(MediaItem.missing.is_(False))).all())
    best = {m: t for m, t in _best_by_media(db, user).items() if m in present}
    blocked: set[str] = set()
    top = cfg.tiers()[-1].id
    if all(t == top for t in best.values()) and not any(_review_waits(db, user, m) for m in best):
        blocked.add("review")
    if paying_tags_left(db, user) == 0:
        blocked.add("tag")
    if any(g.kind == "buy" and g.period == period for g in cfg.goals.templates):
        from . import market  # late: market imports packs, which imports this module

        cheapest = market.cheapest_listing(db)
        if cheapest is None or user.coins < cheapest:
            blocked.add("buy")
    return blocked


def _review_waits(db: Session, user: User, media_id: int) -> bool:
    t = review_eligible_treatment(db, user, media_id)
    return t is not None and not already_paid(db, f"review:{media_id}:{t}")


def paying_tags_left(db: Session, user: User) -> int:
    """How many more tags would still pay on the cards the player owns: the most a tag goal can ask for."""
    cfg = get_config()
    present = set(db.scalars(select(MediaItem.id).where(MediaItem.missing.is_(False))).all())
    best = {m: t for m, t in _best_by_media(db, user).items() if m in present}
    slots = next_tag_slots(db, user, list(best))
    left = 0
    for m, t in best.items():
        mult = cfg.treatments[t].multiplier
        n, cap = slots.get(m, (0, None))
        if cap is not None and cap <= 0:
            continue  # the next tag refills a slot that nothing was taken back for: it pays nothing
        while n < cfg.tagging.max_paid_tags and eco.tag_reward(cfg, n, mult) > 0:
            left += 1
            n += 1
    return left


def _picks_key(user: User, period: str, key: str) -> str:
    return f"goals:{user.id}:{period}:{key}"


def picked_goals(db: Session, user: User, period: str, key: str) -> list:
    """The goal templates of this period. Picked on first sight and stored in app_state, so they stay
    the same until the period ends, whatever the player does (or tags, or sells) meanwhile."""
    cfg = get_config()
    by_id = {g.id: g for g in cfg.goals.templates if g.period == period}
    k = _picks_key(user, period, key)
    row = db.get(AppState, k)
    if row is None:
        # A tag goal that asks for more tags than still pay in the whole library can't be finished.
        ids = [g.id for g in gl.pick_goals(cfg, key, user.id, period=period, blocked=blocked_kinds(db, user, period),
                                           limits={"tag": paying_tags_left(db, user)})]
        try:
            # the picks of earlier periods are no longer needed
            db.execute(delete(AppState).where(AppState.key.like(f"goals:{user.id}:{period}:%"), AppState.key != k))
            db.add(AppState(key=k, value=json.dumps(ids)))
            db.commit()
        except IntegrityError:  # two first requests at once: the other one stored its picks. Use those.
            db.rollback()
            row = db.get(AppState, k)
            ids = json.loads(row.value) if row else ids
    else:
        ids = json.loads(row.value or "[]")
    return [by_id[i] for i in ids if i in by_id]  # a template taken out of economy.toml drops out


def period_goals(db: Session, user: User, period: str, today: date | None = None) -> dict:
    """The goals of the running week ("week") or local day ("day"), with progress."""
    cfg = get_config()
    today = today or date.today()
    if period == "day":
        key, since, ends = gl.day_key(today), gl.day_start(today), gl.day_end(today)
        unlock = cfg.goals.daily_unlock_after_packs
    else:
        key, since, ends = gl.week_key(today), gl.week_start(today), gl.week_end(today)
        unlock = cfg.goals.unlock_after_packs
    out = {"key": key, "locked": user.packs_opened < unlock, "unlock_after_packs": unlock,
           "packs_opened": user.packs_opened, "ends_at": ends.isoformat(), "goals": []}
    if out["locked"]:
        return out
    picks = picked_goals(db, user, period, key)
    c = counters(db, user, since)
    for g in picks:
        out["goals"].append({
            "id": g.id, "title": g.title, "kind": g.kind, "target": g.target,
            "reward": eco.pack_list(cfg, [g.reward]),  # v0.9.0: packs, as [{pack, count}]
            "progress": min(gl.goal_progress(cfg, g, c), g.target),
            "claimed": already_paid(db, f"goal:{user.id}:{key}:{g.id}"),
        })
    return out


def weekly_goals(db: Session, user: User, today: date | None = None) -> dict:
    data = period_goals(db, user, "week", today)
    return {"week": data["key"], **data}


def daily_goals(db: Session, user: User, today: date | None = None) -> dict:
    return period_goals(db, user, "day", today)


def claim_goal(db: Session, user: User, goal_id: str, today: date | None = None, period: str = "week") -> dict:
    """Give a finished goal of the running week (or day) its packs, once. Returns the claim reply
    (`packs`, `inventory`, `coins`)."""
    data = period_goals(db, user, period, today)
    goal = next((g for g in data["goals"] if g["id"] == goal_id), None)
    if goal is None or goal["claimed"] or goal["progress"] < goal["target"]:
        raise NotClaimable(goal_id)
    reason = "daily_goal" if period == "day" else "goal"
    try:
        if mark_claimed(db, user, reason, dedupe_key=f"goal:{user.id}:{data['key']}:{goal_id}", ref=goal_id) is None:
            raise NotClaimable(goal_id)
        packs = grant_packs(db, user, goal["reward"], reason)
        db.commit()
    except IntegrityError:  # a second click arrived at the same moment
        db.rollback()
        raise NotClaimable(goal_id) from None
    return claim_reply(db, user, packs)


# ---------------------------------------------------------------- mastery


def mastery_status(db: Session, user: User) -> dict:
    """Mastery points, level and the levels waiting to be claimed (game/mastery.py)."""
    cfg = get_config()
    present = set(db.scalars(select(MediaItem.id).where(MediaItem.missing.is_(False))).all())
    cards = {t.id: 0 for t in cfg.tiers()}
    for media_id, t in _best_by_media(db, user).items():
        if media_id in present:
            cards[t] += 1
    fuses = db.scalar(select(func.count(AuditLog.id)).where(
        AuditLog.user_id == user.id, AuditLog.event == "fuse")) or 0
    pts = ms.points(cfg, cards, fuses)
    level = ms.level_for(cfg, pts)
    paid = set(db.scalars(select(Transaction.dedupe_key).where(
        Transaction.user_id == user.id, Transaction.reason == "mastery")).all())
    claimable = [{"level": n, "reward": ms.level_packs(cfg, [n])} for n in range(1, level + 1)
                 if f"mastery:{user.id}:{n}" not in paid and ms.level_reward(cfg, n) is not None]
    # v0.9.0: every reward is packs, as [{pack, count}]. `claimable_packs`: the waiting levels' packs in all.
    return {
        "level": level, "points": pts, "level_at": ms.level_at(cfg, level), "next_at": ms.level_at(cfg, level + 1),
        "next_reward": ms.level_packs(cfg, [level + 1]), "claimable": claimable,
        "claimable_packs": ms.level_packs(cfg, [c["level"] for c in claimable]), "cards": cards, "fuses": fuses,
    }


def claim_mastery(db: Session, user: User) -> dict:
    """Give the packs of every reached level that hasn't paid yet. A level pays once, whatever
    happens to the points later. Returns the claim reply plus `levels`."""
    cfg = get_config()
    status = mastery_status(db, user)
    if not status["claimable"]:
        raise NotClaimable("mastery")
    levels = []
    try:
        for c in status["claimable"]:
            if mark_claimed(db, user, "mastery", dedupe_key=f"mastery:{user.id}:{c['level']}", ref=f"level:{c['level']}"):
                levels.append(c["level"])
        if not levels:
            raise NotClaimable("mastery")
        packs = grant_packs(db, user, ms.level_packs(cfg, levels), "mastery")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise NotClaimable("mastery") from None
    return claim_reply(db, user, packs, levels=levels)


# ---------------------------------------------------------------- the whole chase, and the next goal


def _goal_candidates(scope: str, data: dict) -> list[gl.NextGoal]:
    return [
        gl.NextGoal(scope, g["title"], g["progress"], g["target"], g["reward"],
                    "#/stats" if g["progress"] >= g["target"] else gl.GOAL_HREF.get(g["kind"], "#/stats"))
        for g in data["goals"] if not g["claimed"]
    ]


def next_goal(db: Session, user: User, daily: dict, weekly: dict, mastery: dict) -> dict | None:
    """The single goal to show as "next": one that only needs claiming, else the one closest to done.

    Candidates: today's and this week's unclaimed goals, the next mastery level (or the levels waiting
    to be claimed), the next completeness milestone and the category set tier closest to its reward.
    A mastery level and a milestone are steps of a ladder: how close they are is measured from the
    step before, not from zero (otherwise "33% of 35%" would always win).
    """
    cfg = get_config()
    cands = _goal_candidates("daily", daily) + _goal_candidates("weekly", weekly)
    if mastery["claimable"]:
        levels = [c["level"] for c in mastery["claimable"]]
        title = (f"Claim collector level {levels[0]}" if len(levels) == 1
                 else f"Claim collector levels {levels[0]} to {levels[-1]}")
        cands.append(gl.NextGoal("mastery", title, mastery["points"], ms.level_at(cfg, levels[-1]),
                                 mastery["claimable_packs"], "#/stats"))
    elif mastery["next_reward"]:
        span = mastery["next_at"] - mastery["level_at"]
        cands.append(gl.NextGoal(
            "mastery", f"Reach collector level {mastery['level'] + 1}", mastery["points"], mastery["next_at"],
            mastery["next_reward"], "#/stats",
            share=(mastery["points"] - mastery["level_at"]) / span if span > 0 else 0.0))
    comp = milestone_progress(db, user)[0]  # the milestones' own measure (the largest library seen)
    waiting = milestones_status(db, user)
    if waiting["claimable"]:  # reached, and waiting to be claimed (v0.9.0)
        ats = [m["at"] for m in waiting["claimable"]]
        title = (f"Claim the {ats[0] * 100:g}% milestone" if len(ats) == 1 else f"Claim {len(ats)} milestones")
        cands.append(gl.NextGoal("milestone", title, 1, 1, waiting["claimable_packs"], "#/stats"))
    nm = eco.next_milestone(cfg, comp, paid_milestones(db, user) | reached_milestones(db, user))
    if nm and not waiting["claimable"]:
        before = max((m.at for m in cfg.milestones if m.at <= comp), default=0.0)
        cands.append(gl.NextGoal(
            "milestone", f"Reach {nm[0] * 100:g}% completeness", min(round(comp * 100, 1), nm[0] * 100),
            round(nm[0] * 100, 1), nm[1], "#/stats", share=(comp - before) / (nm[0] - before)))
    tiers = [
        gl.NextGoal("set", f"Complete the set \"{s['name']}\" in {cfg.treatments[t['treatment']].name}",
                    t["progress"], s["size"], t["reward"], "#/stats")
        for s in category_sets(db, user) for t in s["tiers"] if not t["claimed"] and t["reward"]
    ]
    best_set = gl.closest(tiers)
    if best_set:
        cands.append(best_set)
    pick = gl.closest(cands)
    if pick is None:
        return None
    return {"kind": pick.kind, "title": pick.title, "progress": pick.progress, "target": pick.target,
            "reward": pick.reward, "href": pick.href, "claimable": pick.done}


def overview(db: Session, user: User, today: date | None = None) -> dict:
    """Everything there is to chase, in one reply (GET /api/economy/chase)."""
    daily = period_goals(db, user, "day", today)
    weekly = period_goals(db, user, "week", today)
    mastery = mastery_status(db, user)
    out = {"daily": daily, "weekly": weekly, "mastery": mastery,
           # v0.9.0: the milestones reached and waiting to be claimed, and the next one
           "milestones": milestones_status(db, user),
           "next": next_goal(db, user, daily, weekly, mastery)}
    db.commit()  # a milestone noted as reached is kept
    return out


# ---------------------------------------------------------------- category sets
# Tags act as categories until groups/categories land in Phase 1.


def _best_by_media(db: Session, user: User) -> dict[int, str]:
    cfg = get_config()
    best: dict[int, str] = {}
    for media_id, t in db.execute(
        select(CollectionEntry.media_id, CollectionEntry.treatment).where(
            CollectionEntry.user_id == user.id, CollectionEntry.copies > 0
        )
    ).all():
        if t in cfg.treatments and (media_id not in best or cfg.treatments[t].tier > cfg.treatments[best[media_id]].tier):
            best[media_id] = t
    return best


def _set_credits(db: Session, user: User) -> set[tuple[int, str]]:
    return set(db.execute(select(SetCredit.media_id, SetCredit.treatment).where(SetCredit.user_id == user.id)).all())


def category_sets(db: Session, user: User) -> list[dict]:
    """Every tag with enough cards is a set. Owning all its cards at a treatment completes it there.

    A card counts for a set reward once per treatment (SetCredit), whichever tag the set is built
    on. v0.9.0: the reward is packs (`reward`, as [{pack, count}]), and a set pays only while more
    than half of its cards have not counted yet (`gl.set_pays`); otherwise its reward is empty and
    it shows as claimed. So a new tag on cards that already counted is worth nothing, and cards
    whose file is missing don't count.
    """
    cfg = get_config()
    best = _best_by_media(db, user)
    present = set(db.scalars(select(MediaItem.id).where(MediaItem.missing.is_(False))).all())
    members: dict[int, list[int]] = {}
    for tag_id, media_id in db.execute(select(media_tags.c.tag_id, media_tags.c.media_id)).all():
        if media_id in present:
            members.setdefault(tag_id, []).append(media_id)
    names = dict(db.execute(select(Tag.id, Tag.name)).all())
    credited = _set_credits(db, user)
    out = []
    for tag_id, media in sorted(members.items(), key=lambda kv: names.get(kv[0], "")):
        if len(media) < cfg.sets.min_size:
            continue
        bests = [best.get(m) for m in media]
        tiers = []
        for t in cfg.sets.treatments:
            fresh = sum(1 for m in media if (m, t) not in credited)
            claimed = already_paid(db, f"set:{user.id}:{tag_id}:{t}")
            pays = gl.set_pays(len(media), fresh)
            tiers.append({
                "treatment": t,
                "progress": gl.set_progress(cfg, bests, t),
                "complete": gl.set_complete(cfg, bests, t),
                "reward": gl.set_reward(cfg, len(media), t) if pays and not claimed else [],
                "claimed": claimed or not pays,
            })
        out.append({"tag_id": tag_id, "name": names.get(tag_id, ""), "size": len(media), "tiers": tiers})
    return out


def claim_set(db: Session, user: User, tag_id: int, treatment: str) -> dict:
    """Give a completed set its packs at a treatment, once. Returns the claim reply."""
    s = next((x for x in category_sets(db, user) if x["tag_id"] == tag_id), None)
    tier = next((t for t in (s or {}).get("tiers", []) if t["treatment"] == treatment), None)
    if tier is None or not tier["complete"] or tier["claimed"] or not tier["reward"]:
        raise NotClaimable(f"{tag_id}:{treatment}")
    tx = mark_claimed(db, user, "set", dedupe_key=f"set:{user.id}:{tag_id}:{treatment}", ref=f"tag:{tag_id}")
    if tx is None:
        raise NotClaimable(f"{tag_id}:{treatment}")
    packs = grant_packs(db, user, tier["reward"], "set")
    if tx:
        credited = _set_credits(db, user)
        present = select(MediaItem.id).where(MediaItem.missing.is_(False))
        for (media_id,) in db.execute(
            select(media_tags.c.media_id).where(media_tags.c.tag_id == tag_id, media_tags.c.media_id.in_(present))
        ).all():
            if (media_id, treatment) not in credited:
                db.add(SetCredit(user_id=user.id, media_id=media_id, treatment=treatment))
    db.commit()
    return claim_reply(db, user, packs)
