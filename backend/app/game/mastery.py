"""Mastery (pure, no DB): a second progress number that keeps rising after everything is tagged.

Completeness stands still once a library is pulled and tagged, except for the odd rarer copy.
Mastery counts only what the late game is made of: the best treatment of every card you own, and
every fuse you have ever done. Its levels get further apart, each pays once, and fusing never runs
out, so there is always a next level (v0.6.0, work order A2).

    points = Σ over owned cards of mastery.points[best treatment] + mastery.fuse_points × fuses
    level L is reached at round(level_base × L ^ level_exponent) points
    level L pays packs, once: 1 Standard, a Deluxe every 5th level, a Premium every 10th (v0.9.0)

Used by services/chase.py and by the simulator.
"""

from .config import EconomyConfig


def points(cfg: EconomyConfig, cards_by_best: dict[str, int], fuses: int) -> int:
    """cards_by_best: how many owned cards have each treatment as their best one."""
    m = cfg.mastery
    return sum(m.points.get(t, 0) * n for t, n in cards_by_best.items()) + m.fuse_points * max(0, fuses)


def level_at(cfg: EconomyConfig, level: int) -> int:
    """Points needed for a level (level 0 needs none)."""
    if level <= 0:
        return 0
    return round(cfg.mastery.level_base * level ** cfg.mastery.level_exponent)


def level_for(cfg: EconomyConfig, pts: int) -> int:
    """The highest level reached with this many points."""
    m = cfg.mastery
    if pts <= 0:
        return 0
    level = max(0, int((pts / m.level_base) ** (1 / m.level_exponent)))  # close; rounding is settled below
    while level_at(cfg, level + 1) <= pts:
        level += 1
    while level > 0 and level_at(cfg, level) > pts:
        level -= 1
    return level


def level_reward(cfg: EconomyConfig, level: int):
    """What a level pays, once (v0.9.0: packs): the `reward_every` step with the biggest `every`
    that divides the level, else `mastery.reward`. A PackReward-like object, or None for no reward."""
    m = cfg.mastery
    if level <= 0:
        return None
    steps = [s for s in m.reward_every if level % s.every == 0]
    return max(steps, key=lambda s: s.every) if steps else m.reward


def level_packs(cfg: EconomyConfig, levels) -> list[dict]:
    """The packs a list of levels pays in all, as `[{pack, count}]`."""
    from . import economy as eco

    return eco.pack_list(cfg, [level_reward(cfg, n) for n in levels])
