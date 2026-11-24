"""Pure economy formulas (no DB). Every number comes from EconomyConfig.

Section references (§) point at the Economy Design doc; see docs/economy/technical-reference.md.
"""

from datetime import date

from .config import EconomyConfig, Pack

# ---------------------------------------------------------------- coin sources (§2)


def ingest_reward(
    cfg: EconomyConfig, kind: str, initial_scan: bool, paid_today: int, files_today: int = 0
) -> int:
    """files_today: new files already paid today. First N pay full, next M pay half, then 0;
    the total is also capped at daily_cap coins."""
    if initial_scan and not cfg.ingest.reward_initial_scan:
        return 0
    ing = cfg.ingest
    if files_today < ing.full_files_per_day:
        factor = 1.0
    elif files_today < ing.full_files_per_day + ing.half_files_per_day:
        factor = 0.5
    else:
        return 0
    base = round((ing.video if kind == "video" else ing.image) * factor)
    return max(0, min(base, ing.daily_cap - paid_today))


def fuse_need(cfg: EconomyConfig, normal_price: int) -> int:
    """Copies one fuse of this card takes: the base, plus one for every price step the card's
    Normal market price has reached. Callers pass the price of the last price update (the live
    one is the same since v0.8.0: the player's own trades no longer move it), so the count changes
    only when the market does."""
    return cfg.fuse.copies + sum(1 for step in cfg.fuse.price_steps if normal_price >= step)


def fuse_net_versions(cfg: EconomyConfig, owned: dict[str, int], treatment: str, need: int) -> int:
    """The real change in versions held (filled Foil, Holo and Special slots) when `need` copies at
    `treatment` are fused into one copy a treatment higher: +1, 0 or -1.

    owned: copies per treatment before the fuse. The cases (feature spec, amendment 2):
    * the last copies of a slot treatment go into a treatment already held: -1
    * the last copies go into a treatment not yet held: 0
    * a copy of the fused treatment stays: +1, or 0 when the higher treatment is already held
    * from Normal (not a slot): +1, or 0 when the Foil is already held
    """
    up = next_tier(cfg, treatment)
    if up is None:
        return 0
    emptied = cfg.treatments[treatment].tier > 0 and owned.get(treatment, 0) - need <= 0
    filled = owned.get(up, 0) <= 0
    return int(filled) - int(emptied)


def pull_flags(cfg: EconomyConfig, held: dict[str, int], treatment: str) -> dict[str, bool]:
    """What a pulled copy means for the card's versions, and the copy is added to `held`.

    held: the card's copies per treatment before this pull (changed in place).
    fills_slot: the pull fills an empty Foil, Holo or Special slot.
    already_owned: its treatment was already held and at least one higher slot of the card is
    still empty (the pull did not help the versions, and there is something left to chase).

    How they go with the pull's `outcome`: fills_slot can only be true for "new", "better" and
    "version" (it is false for those when the copy is a Normal one); already_owned can only be true
    for "duplicate". Both are never true together."""
    tier = cfg.treatments[treatment].tier
    had = held.get(treatment, 0) > 0
    held[treatment] = held.get(treatment, 0) + 1
    higher_empty = any(t.tier > tier and held.get(t.id, 0) <= 0 for t in cfg.tiers())
    return {"fills_slot": tier > 0 and not had, "already_owned": had and higher_empty}


def sale_empties_slot(cfg: EconomyConfig, treatment: str, have: int, count: int = 1) -> bool:
    """Does selling `count` of `have` copies empty a version slot? Only the last copy of a
    treatment above Normal does."""
    return treatment in cfg.treatments and cfg.treatments[treatment].tier > 0 and 0 < have <= count


def upgrade_cost(cfg: EconomyConfig, current: str, need: int | None = None) -> int | None:
    """Coins for one upgrade attempt on a card whose best treatment is `current` (None = can't).

    `need` is the card's fuse_need: a card that takes more copies to fuse costs that much more to
    upgrade (4 copies instead of 3 = a third more), rounded to 5 coins.
    None for every card while `upgrades.enabled` is off (v0.7.1)."""
    if not cfg.upgrades.enabled or next_tier(cfg, current) is None:
        return None
    base = cfg.upgrades.cost.get(current)
    if base is None or need is None or need <= cfg.fuse.copies:
        return base
    return int(round(base * need / cfg.fuse.copies / 5) * 5)


def tag_reward(cfg: EconomyConfig, tag_index: int, multiplier: float, owned: bool = True, rush: bool = False) -> int:
    """tag_index 0 = first tag on the card. The reward shrinks with every tag on the same card:
    the configured list, then `tagging.floor` (not multiplied) until `tagging.max_paid_tags`, then 0.

    multiplier: the best treatment owned for this media (1.0 if none).
    owned: False for media never pulled from a pack → pays cfg.tagging.unowned_factor.
    rush: a tag rush is running → × cfg.tagging.rush_multiplier.
    """
    t = cfg.tagging
    if tag_index >= t.max_paid_tags:
        return 0
    boost = t.rush_multiplier if rush else 1.0
    if tag_index == 0:
        base = t.first_tag
    elif tag_index - 1 < len(t.extra_tags):
        base = t.extra_tags[tag_index - 1]
    else:
        return round(t.floor * boost)  # the floor is a flat coin or so, whatever the card's treatment
    factor = 1.0 if owned else t.unowned_factor
    return round(base * multiplier * factor * boost)


def card_tagging_value(cfg: EconomyConfig, tags: int, multiplier: float, owned: bool = True) -> int:
    return sum(tag_reward(cfg, i, multiplier, owned) for i in range(tags))


def review_reward(cfg: EconomyConfig, multiplier: float) -> int:
    return round(cfg.tagging.upgrade_review * multiplier)


def pack_list(cfg: EconomyConfig, rewards) -> list[dict]:
    """Pack rewards as every payload carries them (v0.9.0): `[{"pack": id, "count": n}]`, one row
    per pack kind, in the order of the packs in the config. `rewards` is any mix of PackReward-like
    objects (with `pack` and `count`) and None; an empty list means "no reward"."""
    total: dict[str, int] = {}
    for r in rewards:
        if r is not None and r.count > 0:
            total[r.pack] = total.get(r.pack, 0) + r.count
    order = list(cfg.packs)
    return [{"pack": p, "count": n} for p, n in sorted(total.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order))]


def packs_worth(cfg: EconomyConfig, packs: list[dict]) -> int:
    """What a list of pack rewards would cost at base prices: used only to compare rewards (the
    "next goal" pick and the simulator), never paid."""
    return sum(cfg.packs[p["pack"]].base_cost * p["count"] for p in packs if p["pack"] in cfg.packs)


def next_streak(current: int, last_day: date | None, today: date) -> tuple[int, bool]:
    """Returns (new streak length, whether today's bonus is still claimable)."""
    if last_day == today:
        return current, False
    if last_day is not None and (today - last_day).days == 1:
        return current + 1, True
    return 1, True


def milestones_crossed(cfg: EconomyConfig, before: float, after: float) -> list[tuple[float, list[dict]]]:
    return [(m.at, pack_list(cfg, [m.reward])) for m in cfg.milestones if before < m.at <= after]


def next_milestone(cfg: EconomyConfig, completeness: float, paid: frozenset[float] | set[float] = frozenset()) -> tuple[float, list[dict]] | None:
    """The next milestone still to reach: (share, its packs). Completeness can fall (a sale, new
    files), so a milestone above it may have been reached or claimed before: those (`paid`) are skipped."""
    return next(((m.at, pack_list(cfg, [m.reward])) for m in cfg.milestones if m.at > completeness and m.at not in paid), None)


# ---------------------------------------------------------------- completeness (§6.3)


def slot_treatments(cfg: EconomyConfig) -> list[str]:
    """The treatments that count for completeness: every one above the base (Foil, Holo, Special).

    Normal is the base for fusing and upgrading and is not a slot."""
    return [t.id for t in cfg.tiers() if t.tier > 0]


def slots_total(cfg: EconomyConfig, media: int) -> int:
    return media * len(slot_treatments(cfg))


def slots_completeness(filled: int, total: int) -> float:
    """Slots mode (v0.7.0): the share of (card, treatment) slots the player holds right now."""
    return min(1.0, filled / total) if total > 0 else 0.0


def card_completeness(cfg: EconomyConfig, tagged: bool, best_treatment: str | None) -> float:
    """0..1 score for one card in the older modes ("weighted", "tagged"); "slots" doesn't use it.

    Weighted mode: weight of the best owned treatment / top weight.

    Untagged → 0. Tagged media you don't own counts like Normal.
    """
    if not tagged:
        return 0.0
    if cfg.completeness.mode == "tagged":
        return 1.0
    top = max(t.completeness_weight for t in cfg.treatments.values())
    t = cfg.treatments.get(best_treatment or "normal", cfg.treatments["normal"])
    return t.completeness_weight / top


def collection_completeness(cfg: EconomyConfig, cards: list[tuple[bool, str | None]]) -> float:
    """cards: (tagged, best owned treatment or None) for every media item."""
    if not cards:
        return 0.0
    return sum(card_completeness(cfg, tagged, best) for tagged, best in cards) / len(cards)


# ---------------------------------------------------------------- sinks / packs (§3)


def inflation_multiplier(cfg: EconomyConfig, packs_opened: int, completeness: float) -> float:
    inf = cfg.inflation
    if inf.mode == "completeness":
        raw = (1 + inf.per_10pct_completeness) ** int(completeness * 10 + 1e-9)
    else:
        raw = 1 + inf.per_pack * packs_opened
    # Late-game pacing: prices stop rising at the cap, so packs stay within reach once
    # everything is tagged and income has thinned out.
    return min(raw, inf.max_multiplier) if inf.max_multiplier >= 1 else raw


def pack_cost(cfg: EconomyConfig, pack: Pack, packs_opened: int, completeness: float) -> int:
    raw = pack.base_cost * inflation_multiplier(cfg, packs_opened, completeness)
    step = max(1, cfg.inflation.round_to)
    return int(round(raw / step) * step)


def pity_multiplier(cfg: EconomyConfig, dry_packs: int) -> float:
    """Odds multiplier for the pity treatments after `dry_packs` packs in a row without one."""
    p = cfg.pity
    if not p.enabled:  # v0.9.0: switched off in the live config, every pack has the same odds
        return 1.0
    if not p.treatments or p.after_packs <= 0 or dry_packs < p.after_packs:
        return 1.0
    return min(p.max_boost, 1 + p.boost_per_pack * (dry_packs - p.after_packs + 1))


def slot_weights(cfg: EconomyConfig, pack: Pack, completeness: float, pity: float = 1.0) -> list[tuple[str, float]]:
    """(treatment id, weight) common → rare, with pack overrides, rarity decay and pity applied."""
    decay = 1 - cfg.rarity_decay.max * max(0.0, min(1.0, completeness))
    out = []
    for t in cfg.tiers():
        w = pack.weights.get(t.id, t.weight)
        if t.tier > 0:
            w *= decay
        if t.id in cfg.pity.treatments and cfg.pity.enabled:
            w *= pity
        out.append((t.id, w))
    return out


# ---------------------------------------------------------------- value & selling (§4)


def card_value(cfg: EconomyConfig, treatment: str, copies_owned: int) -> int:
    base = cfg.treatments[treatment].value
    extra = max(0, copies_owned - 1)
    return round(base / (1 + cfg.selling.copy_value_decay * extra))


def sell_rate(cfg: EconomyConfig, inflation_mult: float) -> float:
    s = cfg.selling
    pct = (inflation_mult - 1) * 100
    return max(s.floor, s.base_rate - s.decay_per_inflation_pct * pct)


def sell_price(cfg: EconomyConfig, treatment: str, copies_owned: int, inflation_mult: float) -> int:
    return round(card_value(cfg, treatment, copies_owned) * sell_rate(cfg, inflation_mult))


# ---------------------------------------------------------------- duplicates (§5)


def duplicate_chance(cfg: EconomyConfig, owned_ratio: float) -> float:
    d = cfg.duplicates
    r = max(0.0, min(1.0, owned_ratio))
    return d.early_chance + (d.late_chance - d.early_chance) * r**d.curve


def duplicate_coins(cfg: EconomyConfig, treatment: str) -> int:
    return round(cfg.treatments[treatment].value * cfg.duplicates.convert_rate)


def next_tier(cfg: EconomyConfig, treatment: str) -> str | None:
    tiers = cfg.tiers()
    i = [t.id for t in tiers].index(treatment)
    return tiers[i + 1].id if i + 1 < len(tiers) else None
