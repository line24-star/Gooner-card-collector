"""Scoped chase goals (pure, no DB): weekly and daily goals, the "next goal", category sets.

A 1000-card rarity chase moves ~0.02% per upgrade, which is invisible. These give late-game
progress in finishable chunks instead (decision 2026-11-10b). Used by the API and the simulator.
"""

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from . import economy as eco
from .config import EconomyConfig, GoalTemplate

# ---------------------------------------------------------------- weekly and daily goals


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_start(d: date) -> datetime:
    """Local midnight on the Monday of d's ISO week, as UTC (timestamps are stored in UTC).

    v0.7.1: the same clock as the day. The week key changes at local midnight, so counting from
    Monday 00:00 UTC lost the first hours of Monday east of Greenwich."""
    monday = d - timedelta(days=d.weekday())
    return datetime.combine(monday, time.min).astimezone(timezone.utc)


def week_end(d: date) -> datetime:
    """The next Monday's local midnight, as UTC: when this week's goals are replaced."""
    return week_start(d + timedelta(days=7))


def day_key(d: date) -> str:
    return d.isoformat()


def day_start(d: date) -> datetime:
    """Local midnight at the start of the player's calendar day d, as UTC (timestamps are stored in UTC)."""
    return datetime.combine(d, time.min).astimezone(timezone.utc)


def day_end(d: date) -> datetime:
    """The next local midnight, as UTC: when today's goals are replaced."""
    return day_start(d + timedelta(days=1))


def pick_goals(cfg: EconomyConfig, key: str, user_id: int, period: str = "week",
               blocked: frozenset[str] | set[str] = frozenset(),
               limits: dict[str, int] | None = None) -> list[GoalTemplate]:
    """The goals of one period (`key` is its week or day key). Stable for a given player and period;
    different players and periods get different mixes.

    blocked: kinds the player cannot make progress on right now; their templates are left out. When
    fewer templates remain than the period wants, the player gets what there is.
    limits: per kind, the most that can still be done (e.g. the paying tags left in the library);
    a template with a higher target is left out (v0.7.1).
    """
    limits = limits or {}
    templates = [g for g in cfg.goals.templates
                 if g.period == period and g.kind not in blocked and g.target <= limits.get(g.kind, g.target)]
    want = cfg.goals.per_day if period == "day" else cfg.goals.per_week
    n = max(0, min(want, len(templates)))
    return random.Random(f"{user_id}:{key}").sample(templates, n)


@dataclass
class Counters:
    """What the player did in a period (a week or a day). Pulls are counted by treatment tier."""

    pulls_by_tier: dict[int, int] = field(default_factory=dict)
    upgrades: int = 0  # successful upgrade attempts, upgrades out of a pack, and fuses
    tags: int = 0
    reviews: int = 0
    opens: int = 0
    fuses: int = 0
    sells: int = 0  # copies sold on the market, accepted offers included
    market_coins: int = 0  # coins those sales paid
    buys: int = 0  # cards bought on the market

    def add_pull(self, tier: int) -> None:
        self.pulls_by_tier[tier] = self.pulls_by_tier.get(tier, 0) + 1


WeekCounters = Counters  # the name before v0.6.0


def goal_progress(cfg: EconomyConfig, goal: GoalTemplate, c: Counters) -> int:
    if goal.kind == "pull":
        min_tier = cfg.treatments[goal.treatment].tier
        return sum(n for tier, n in c.pulls_by_tier.items() if tier >= min_tier)
    return {
        "upgrade": c.upgrades, "tag": c.tags, "review": c.reviews, "open": c.opens,
        "fuse": c.fuses, "sell": c.sells, "market-coins": c.market_coins, "buy": c.buys,
    }[goal.kind]


@dataclass(frozen=True)
class NextGoal:
    """One candidate for "the next goal" (a daily or weekly goal, a mastery level, a milestone, a set)."""

    kind: str  # daily | weekly | mastery | milestone | set
    title: str
    progress: float
    target: float
    reward: list  # v0.9.0: packs, as [{pack, count}]
    href: str
    share: float | None = None  # how far along, 0..1, when that isn't progress / target (steps of a ladder)

    @property
    def done(self) -> bool:
        return self.progress >= self.target

    @property
    def closeness(self) -> float:
        if self.share is not None:
            return max(0.0, min(1.0, self.share))
        return max(0.0, min(1.0, self.progress / self.target)) if self.target > 0 else 0.0


def closest(candidates: list[NextGoal]) -> NextGoal | None:
    """The single next goal: one that only needs claiming first, else the one closest to done.
    Ties go to the earlier candidate, so callers list them in the order daily, weekly, mastery,
    milestone, set."""
    best = None
    for c in candidates:
        if best is None or (c.done, c.closeness) > (best.done, best.closeness):
            best = c
    return best


GOAL_HREF = {
    "pull": "#/packs", "open": "#/packs", "tag": "#/organize", "review": "#/organize",
    "upgrade": "#/collection", "fuse": "#/collection", "sell": "#/market", "market-coins": "#/market",
    "buy": "#/market",
}


# ---------------------------------------------------------------- category sets


def set_complete(cfg: EconomyConfig, best_treatments: list[str | None], treatment: str) -> bool:
    """best_treatments: the best owned treatment (or None) for each card in the set."""
    if not best_treatments:
        return False
    need = cfg.treatments[treatment].tier
    return all(b is not None and cfg.treatments[b].tier >= need for b in best_treatments)


def set_progress(cfg: EconomyConfig, best_treatments: list[str | None], treatment: str) -> int:
    need = cfg.treatments[treatment].tier
    return sum(1 for b in best_treatments if b is not None and cfg.treatments[b].tier >= need)


def set_reward(cfg: EconomyConfig, size: int, treatment: str) -> list[dict]:
    """The packs a set of `size` cards pays at a treatment (v0.9.0): `sets.rewards[treatment]`,
    twice the count from `sets.large_size` cards on. Empty when that treatment has no reward."""
    r = cfg.sets.rewards.get(treatment)
    if r is None:
        return []
    double = cfg.sets.large_size > 0 and size >= cfg.sets.large_size
    return [{"pack": r.pack, "count": r.count * (2 if double else 1)}]


def set_pays(size: int, fresh: int) -> bool:
    """A set pays its packs only while more than half of its cards have not counted for a set at
    that treatment before (`fresh`): a new tag on cards that already counted pays nothing."""
    return size > 0 and fresh * 2 > size
