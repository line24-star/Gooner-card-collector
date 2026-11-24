"""Server-side pack rolling (pure, no DB) and RTP estimation. Used by the API and the simulator.

Per slot:
  1. Roll a treatment by weight (rarest band at the top of [0,1); near-miss = just below the next band).
  2. With probability duplicate_chance(owned share) pick a media item you already own,
     otherwise one you don't. A pack holds a card once; only a library with fewer cards than the
     pack has slots repeats cards, so the pack is always full (v0.7.1).
  3. Classify: not owned → "new"; owned below the rolled tier → "better"; a treatment you hold no
     copy of, below your best → "version" (v0.7.1: it is not a duplicate, it fills a version);
     otherwise "duplicate": an extra copy (older configs: a chance to upgrade, else coins).
"""

import random
from dataclasses import dataclass, field

from . import economy as eco
from .config import EconomyConfig, Pack

NEAR_MISS = 0.02


@dataclass
class PoolItem:
    media_id: int
    tagged: bool
    tag_count: int = 0
    owned: dict[str, int] = field(default_factory=dict)  # treatment → copies
    weight: float = 1.0  # how likely a slot pulls this card, relative to the others (from its print run)
    value: float = 0.0  # market value of a Normal copy (used for RTP and by the simulator)
    premium: float = 1.0  # the card's premium exponent (game/market.premium): a rarer copy is worth value × treatment_value ** premium

    def best_tier(self, cfg: EconomyConfig) -> int:
        return max((cfg.treatments[t].tier for t, n in self.owned.items() if n > 0), default=-1)

    def best_treatment(self, cfg: EconomyConfig) -> str | None:
        owned = [t for t, n in self.owned.items() if n > 0]
        return max(owned, key=lambda t: cfg.treatments[t].tier) if owned else None


@dataclass
class Pull:
    media_id: int
    treatment: str
    outcome: str  # "new" | "better" | "version" | "duplicate" | "upgraded"
    near_miss: bool = False
    coins: int = 0  # duplicate conversion
    upgraded_to: str | None = None


def roll_treatment(weights: list[tuple[str, float]], r: float) -> tuple[str, bool]:
    total = sum(w for _, w in weights)
    edge = 0.0
    for i, (tid, w) in enumerate(weights):
        edge += w / total
        if r < edge or i == len(weights) - 1:
            return tid, i < len(weights) - 1 and edge - r < NEAR_MISS
    raise AssertionError("unreachable")


def roll_pack(
    cfg: EconomyConfig,
    pack: Pack,
    pool: list[PoolItem],
    completeness: float,
    rng: random.Random,
    pity: float = 1.0,
) -> list[Pull]:
    """Rolls a pack and returns the pulls. Does NOT mutate the pool (callers apply results).

    `pity` multiplies the odds of the pity treatments (economy.pity_multiplier)."""
    if not pool:
        return []
    weights = eco.slot_weights(cfg, pack, completeness, pity)
    owned = [p for p in pool if p.owned and any(p.owned.values())]
    unowned = [p for p in pool if not (p.owned and any(p.owned.values()))]
    dup_p = eco.duplicate_chance(cfg, len(owned) / len(pool))
    used: set[int] = set()
    pulls: list[Pull] = []
    # What this pack itself already added per card (only a pack that repeats a card needs it).
    gained: dict[int, set[str]] = {}

    for _ in range(pack.size):
        treatment, near = roll_treatment(weights, rng.random())
        want_owned = rng.random() < dup_p
        primary, fallback = (owned, unowned) if want_owned else (unowned, owned)
        candidates = [p for p in primary if p.media_id not in used] or [
            p for p in fallback if p.media_id not in used
        ]
        if not candidates:
            # Fewer cards than slots: every card was used once, so they may come again. The pack
            # always holds its full number of cards.
            used.clear()
            candidates = primary or fallback
        # A card is pulled in proportion to its weight: cards with a big print run turn up more often.
        item = rng.choices(candidates, weights=[c.weight for c in candidates])[0]
        used.add(item.media_id)

        tier = cfg.treatments[treatment].tier
        held = {t for t, n in item.owned.items() if n > 0} | gained.get(item.media_id, set())
        best = max((cfg.treatments[t].tier for t in held), default=-1)
        gained.setdefault(item.media_id, set()).add(treatment)
        if best < 0:
            pulls.append(Pull(item.media_id, treatment, "new", near))
        elif tier > best:
            pulls.append(Pull(item.media_id, treatment, "better", near))
        elif cfg.duplicates.keep_copies:
            # A treatment you hold no copy of is a new version of the card, not a duplicate. A real
            # duplicate is simply another copy: the player decides whether to sell it or fuse it.
            pulls.append(Pull(item.media_id, treatment, "duplicate" if treatment in held else "version", near))
        else:
            best_t = item.best_treatment(cfg)
            up = eco.next_tier(cfg, best_t) if best_t else None
            if up and rng.random() < cfg.duplicates.upgrade_chance:
                pulls.append(Pull(item.media_id, treatment, "upgraded", near, upgraded_to=up))
            else:
                pulls.append(
                    Pull(item.media_id, treatment, "duplicate", near, coins=eco.duplicate_coins(cfg, treatment))
                )

    tier_of = {t.id: t.tier for t in cfg.tiers()}
    # Reveal rarest last, by what each card SHOWS: an upgraded duplicate reveals as its upgraded_to.
    pulls.sort(key=lambda p: tier_of[p.upgraded_to if p.outcome == "upgraded" and p.upgraded_to else p.treatment])
    return pulls


def reveal_order(cfg: EconomyConfig, pulls: list[Pull], rng: random.Random) -> list[Pull]:
    """The order the cards are revealed in (v0.9.0, decision D6). `pulls` come rarest last
    (`roll_pack`). With chance `reveal.rarest_last` they stay so; otherwise the rarest card (the
    last one) moves to a random earlier position and the others keep their order. A new list."""
    out = list(pulls)
    if len(out) > 1 and rng.random() >= cfg.reveal.rarest_last:
        out.insert(rng.randrange(len(out) - 1), out.pop())
    return out


def roll_hint(cfg: EconomyConfig, fills_slot: bool, rng: random.Random) -> bool:
    """Does the face-down card glow? A hint that can be wrong: a card that fills a version slot
    glows with chance `reveal.hint_true`, one that fills nothing with `reveal.hint_false`."""
    return rng.random() < (cfg.reveal.hint_true if fills_slot else cfg.reveal.hint_false)


def shown_treatment(pull: Pull) -> str:
    """The treatment the player sees on the revealed card (an upgraded double shows what it became)."""
    return pull.upgraded_to if pull.outcome == "upgraded" and pull.upgraded_to else pull.treatment


def ends_dry_run(cfg: EconomyConfig, pulls: list[Pull]) -> bool:
    """True when the pack rolled one of the pity treatments (the roll, not an upgrade result)."""
    return any(p.treatment in cfg.pity.treatments for p in pulls)


# ---------------------------------------------------------------- RTP (§6)


def pull_value(cfg: EconomyConfig, pull: Pull, item: PoolItem) -> float:
    """Organizing value + coins actually returned for one pull (the doc's RTP definition).

    Hypothetical sale value is NOT counted: you can't both keep a card and sell it.
    """
    t = cfg.treatments[pull.treatment]
    if pull.outcome in ("new", "better"):
        if not item.tagged:
            return eco.card_tagging_value(cfg, cfg.rtp.assumed_tags_per_card, t.multiplier)
        return eco.review_reward(cfg, t.multiplier)
    if pull.outcome == "upgraded" and pull.upgraded_to:
        return eco.review_reward(cfg, cfg.treatments[pull.upgraded_to].multiplier)
    if cfg.duplicates.keep_copies:
        # A kept duplicate is worth what the market pays for it.
        return item.value * cfg.market.treatment_value.get(pull.treatment, 1.0) ** item.premium * cfg.market.sell_rate
    return pull.coins


def estimate_rtp(
    cfg: EconomyConfig,
    pack: Pack,
    pool: list[PoolItem],
    packs_opened: int,
    completeness: float,
    trials: int = 400,
    seed: int = 0,
) -> float:
    rng = random.Random(seed)
    cost = eco.pack_cost(cfg, pack, packs_opened, completeness)
    by_id = {p.media_id: p for p in pool}
    total = 0.0
    for _ in range(trials):
        for pull in roll_pack(cfg, pack, pool, completeness, rng):
            total += pull_value(cfg, pull, by_id[pull.media_id])
    return total / trials / cost
