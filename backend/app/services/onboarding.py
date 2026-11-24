"""Getting-started checklist and tour rewards: small one-time rewards, paid as sealed packs since
v0.9.0 (each deduped). Only some steps pay; the tours pay once, when all of them are finished."""

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..game import onboarding as ob
from ..game.config import get_config
from ..game import economy as eco
from ..models import AppState, AuditLog, CollectionEntry, MediaItem, Pack, Transaction, TutorialProgress, User, media_tags
from .chase import NotClaimable
from .rewards import claim_reply, grant_packs
from .wallet import already_paid, mark_claimed

DISMISSED_KEY = "onboarding_dismissed"


def counters(db: Session, user: User) -> ob.Counters:
    live = MediaItem.missing.is_(False)
    c = ob.Counters()
    c.media = db.scalar(select(func.count(MediaItem.id)).where(live)) or 0
    tagged_ids = select(media_tags.c.media_id).distinct()
    c.tagged = db.scalar(select(func.count(MediaItem.id)).where(live, MediaItem.id.in_(tagged_ids))) or 0
    owned_ids = select(CollectionEntry.media_id).where(CollectionEntry.user_id == user.id, CollectionEntry.copies > 0)
    c.tagged_owned = db.scalar(
        select(func.count(MediaItem.id)).where(live, MediaItem.id.in_(tagged_ids), MediaItem.id.in_(owned_ids))
    ) or 0
    c.packs = 0  # packs OPENED, the free pack included. A bought pack that is still sealed doesn't count.
    for raw in db.scalars(select(Pack.contents_json).where(Pack.user_id == user.id)):
        data = json.loads(raw or "{}")
        if isinstance(data, dict):
            c.packs += "pulls" in data
            c.upgrades += sum(
                1 for p in data.get("pulls", []) if isinstance(p, dict) and p.get("outcome") == "upgraded"
            )
    c.upgrades += db.scalar(
        select(func.count(Transaction.id)).where(Transaction.user_id == user.id, Transaction.reason == "upgrade_attempt")
    ) or 0
    # Fusing copies into a rarer one counts too (v0.4.0).
    c.upgrades += db.scalar(
        select(func.count(AuditLog.id)).where(AuditLog.user_id == user.id, AuditLog.event == "fuse")
    ) or 0
    return c


def restore(db: Session) -> None:
    """Show the checklist again after it was hidden."""
    row = db.get(AppState, DISMISSED_KEY)
    if row is not None:
        db.delete(row)
        db.commit()


def _key(user: User, step_id: str) -> str:
    return f"onboarding:{user.id}:{step_id}"


def is_dismissed(db: Session) -> bool:
    row = db.get(AppState, DISMISSED_KEY)
    return row is not None and row.value == "1"


def checklist(db: Session, user: User) -> dict:
    """The steps with their progress. `reward` is packs, as [{pack, count}] (empty for a step that
    pays nothing). `claimed`: the step's packs were taken; a step without a reward counts as
    claimed once it is done, so `complete` means every step is done and nothing waits."""
    cfg = get_config()
    c = counters(db, user)
    steps = []
    for s in cfg.onboarding.steps:
        # A library with fewer cards than a tagging step asks for can still finish it (v0.7.1):
        # the target is the number of cards, and the title says so.
        target, title = s.target, s.title
        if s.kind in ("tagged", "tagged_owned") and 0 < c.media < s.target:
            target = c.media
            title = "Tag your card" if target == 1 else f"Tag all {target} of your cards"
        progress = min(ob.step_progress(s, c), target)
        done = progress >= target
        reward = eco.pack_list(cfg, [s.reward])
        steps.append({
            "id": s.id, "title": title, "kind": s.kind, "target": target, "reward": reward,
            "progress": progress, "done": done,
            "claimed": already_paid(db, _key(user, s.id)) or (done and not reward),
        })
    return {"steps": steps, "complete": bool(steps) and all(s["claimed"] for s in steps),
            "dismissed": is_dismissed(db)}


def claim(db: Session, user: User, step_id: str) -> dict:
    """Give a finished step its packs, once. Returns the claim reply (`packs`, `inventory`, `coins`)."""
    step = next((s for s in checklist(db, user)["steps"] if s["id"] == step_id), None)
    if step is None or step["claimed"] or not step["done"] or not step["reward"]:
        raise NotClaimable(step_id)
    if mark_claimed(db, user, "onboarding", dedupe_key=_key(user, step_id), ref=step_id) is None:
        raise NotClaimable(step_id)
    packs = grant_packs(db, user, step["reward"], "onboarding")
    db.commit()
    return claim_reply(db, user, packs)


def dismiss(db: Session) -> None:
    db.merge(AppState(key=DISMISSED_KEY, value="1"))
    db.commit()


def tours_status(db: Session, user: User) -> dict:
    """The tour reward: the tours that count, how many are completed, the packs, and whether they were given."""
    cfg = get_config()
    ob_cfg = cfg.onboarding
    done = set(db.scalars(select(TutorialProgress.tutorial_id).where(TutorialProgress.status == "completed")).all())
    return {
        "tours": list(ob_cfg.tours),
        "completed": sum(1 for t in ob_cfg.tours if t in done),
        "reward": eco.pack_list(cfg, [ob_cfg.tours_reward]),
        # a player paid per tour before v0.9.0 has had the tour reward
        "claimed": already_paid(db, f"tours:{user.id}") or db.scalar(
            select(Transaction.id).where(Transaction.dedupe_key.like(f"tour:{user.id}:%")).limit(1)) is not None,
    }


def pay_tour(db: Session, user: User, tour_id: str) -> list[dict]:
    """Call when a tour was completed (never for a skipped one). v0.9.0: once every tour in
    `onboarding.tours` is completed, the tours' packs are given, once. Returns the packs given
    by this call (`[{pack, count}]`, empty most of the time)."""
    status = tours_status(db, user)
    if (tour_id not in status["tours"] or not status["reward"] or status["claimed"]
            or status["completed"] < len(status["tours"])):
        return []
    if mark_claimed(db, user, "tour", dedupe_key=f"tours:{user.id}", ref="tours") is None:
        return []
    packs = grant_packs(db, user, status["reward"], "tour")
    db.commit()
    return packs
