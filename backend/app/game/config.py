"""Economy config: config/economy.toml, validated, re-read automatically when it changes."""

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..config import ROOT

CONFIG_PATH = ROOT / "config" / "economy.toml"


class Ingest(BaseModel):
    image: int
    video: int
    reward_initial_scan: bool = False
    initial_import_quiet_seconds: int = Field(300, ge=0)
    full_files_per_day: int = 10**9
    half_files_per_day: int = 0
    daily_cap: int


class PackReward(BaseModel):
    """A reward since v0.9.0: `count` sealed packs of one kind, put into the inventory. Rewards are
    never coins (coins come only from scanning and tagging, and from trades on the market)."""

    pack: str
    count: int = Field(1, ge=1)


class Tagging(BaseModel):
    first_tag: int
    extra_tags: list[int]
    floor: int = Field(0, ge=0)  # what a tag past the list pays (v0.4.0: 1, so more tags always pay a little)
    max_paid_tags: int = Field(10**6, ge=1)  # tags on one card that can pay at all (stops endless 1-coin tags)
    rush_multiplier: float = Field(1.0, ge=1)  # tag coins × this during a tag rush
    rush_minutes: int = Field(0, ge=0)  # length of the daily tag rush (0 = no rush)
    # v0.9.0: the next rush can start this many hours after the last one STARTED at the earliest
    # (and only on another local day). Without it a rush started just before midnight could be
    # followed by a second one right after midnight.
    rush_gap_hours: float = Field(0, ge=0)
    # v0.9.0, the tagging pause (services/tagpace.py): more than `burst_tags` paid tags within
    # `burst_seconds` start a pause of `cooldown_seconds` in which a tag is applied but pays nothing.
    burst_tags: int = Field(0, ge=0)  # 0 = no pause
    burst_seconds: float = Field(8, gt=0)
    cooldown_seconds: float = Field(4, ge=0)
    # v0.9.0: a tag placed by bulk tagging pays this share of the normal rate (at least 1 coin for
    # a tag that pays at all). One call can tag 200 cards, so it must not pay like 200 single tags.
    bulk_multiplier: float = Field(1.0, ge=0, le=1)
    unowned_factor: float = Field(1.0, ge=0, le=1)
    upgrade_review: int


class Milestone(BaseModel):
    at: float = Field(gt=0, le=1)
    reward: PackReward


class Treatment(BaseModel):
    id: str = ""
    name: str
    tier: int
    multiplier: float = Field(ge=1)
    weight: int = Field(gt=0)
    value: int = Field(gt=0)
    completeness_weight: float = Field(1.0, gt=0)


class Completeness(BaseModel):
    mode: Literal["slots", "weighted", "tagged"] = "slots"


class Pack(BaseModel):
    id: str = ""
    name: str
    blurb: str = ""
    size: int = Field(gt=0)
    base_cost: int = Field(gt=0)
    hue: int = 230
    weights: dict[str, int] = {}


class Inflation(BaseModel):
    mode: Literal["packs", "completeness"]
    per_pack: float
    per_10pct_completeness: float
    round_to: int = 5
    max_multiplier: float = Field(0, ge=0)  # prices stop rising here (1.3 = +30%); 0 = no cap


class Selling(BaseModel):
    base_rate: float
    decay_per_inflation_pct: float
    floor: float
    copy_value_decay: float


class Duplicates(BaseModel):
    early_chance: float = Field(ge=0, le=1)
    late_chance: float = Field(ge=0, le=1)
    curve: float = 1.0
    convert_rate: float
    upgrade_chance: float = Field(ge=0, le=1)
    # v0.4.0: a duplicate is kept as an extra copy. Nothing is paid or upgraded by itself: the
    # player sells copies on the market or fuses them. False restores the old automatic payout.
    keep_copies: bool = False


class Fuse(BaseModel):
    """Trade in copies of one card for one copy a treatment higher."""

    copies: int = Field(3, ge=2)
    # v0.7.0: a card worth more takes more copies. Each step the card's Normal market price has
    # reached adds one copy to `copies` (and raises the price of an upgrade attempt by the same share).
    price_steps: list[int] = []

    @field_validator("price_steps")
    @classmethod
    def _ascending(cls, v: list[int]) -> list[int]:
        if any(s <= 0 for s in v) or sorted(set(v)) != v:
            raise ValueError("fuse.price_steps must be positive and ascending")
        return v


class PrintTier(BaseModel):
    name: str
    share: float = Field(gt=0)  # share of all cards that get this tier
    min: int = Field(gt=0)
    max: int = Field(gt=0)
    # The cheapest pack that holds cards of this tier ("" = every pack). Dearer packs hold them too.
    min_pack: str = ""


class Cycle(BaseModel):
    days: float = Field(gt=0)
    amplitude: float  # in log points: 0.2 ≈ ±22%
    phase: float = 0.0


class Market(BaseModel):
    """The card market (game/market.py). Every number of the simulation."""

    tick_hours: float = Field(6, gt=0)
    max_catch_up_ticks: int = Field(480, ge=1)
    warmup_ticks: int = Field(28, ge=0)  # ticks simulated when the market is created, so charts aren't empty
    # value of a Normal copy = base_value × (reference_print / print_run) ** elasticity × popularity ** leverage
    base_value: float = Field(6, gt=0)
    reference_print: int = Field(20000, gt=0)
    elasticity: float = Field(0.55, ge=0)
    # v0.8.0: what a Foil, Holo or Special copy of the most sought-after card costs, as a multiple
    # of its Normal copy. Every card has its own premium exponent k inside `premium_range`
    # (game/market.premium): a copy at treatment t costs the Normal price × treatment_value[t] ** k.
    treatment_value: dict[str, float] = {}
    premium_range: tuple[float, float] = (1.0, 1.0)
    premium_demand_share: float = Field(0.6, ge=0, le=1)  # how much of k follows the card's popularity
    drop_exponent: float = Field(0.5, ge=0)  # pull weight = print_run ** this (0 = every card equally likely)
    print_tiers: list[PrintTier] = [PrintTier(name="Common", share=1, min=20000, max=20000)]
    popularity_sigma: float = Field(0.45, ge=0)
    # Demand counts for more on scarce cards: value × popularity ** (1 + demand_leverage × log10(reference_print / print_run)).
    # 0 = demand counts the same for every card (the v0.4.0 rule).
    demand_leverage: float = Field(0.0, ge=0)
    popularity_range: tuple[float, float] = (0.4, 4.0)
    # trading
    sell_rate: float = Field(0.85, gt=0, le=1)
    buy_markup: float = Field(1.15, ge=1)
    listings_per_treatment: int = Field(10, ge=1)  # v0.8.0: cards for sale per treatment (was listing_count)
    # What each copy SOLD lowers the card's price by. 0 since v0.8.0: the player is one seller among
    # millions. Above 0 a player can dump copies, buy them back cheaper and wait for the price to
    # recover, so the config is refused while cards are listed (see `dump_return`).
    trade_impact: float = Field(0.0, ge=0, lt=1)
    # v0.8.0: what each copy BOUGHT raises the card's price by. 0: a purchase leaves the price alone.
    # (It was `trade_impact` before. A purchase that raises the price also raises what the copies
    # fused from the purchases sell for, which made buying many copies and fusing them pay.)
    buy_impact: float = Field(0.0, ge=0, lt=1)
    wanted_count: int = Field(3, ge=0)
    wanted_bonus: float = Field(1.3, ge=1)
    wanted_min_value: int = Field(0, ge=0)  # v0.8.0: only a card whose held copy is worth this much is wanted
    # A collector's offer for one of your most valuable cards: at most one per tick.
    offer_chance: float = Field(0.0, ge=0, le=1)  # per tick
    offer_range: tuple[float, float] = (0.9, 1.25)  # the offer as a share of the market price
    offer_top: int = Field(5, ge=1)  # it is for one of your this-many most valuable copies
    offer_min_value: int = Field(0, ge=0)  # no offers for copies worth less than this
    # the index (the market as a whole)
    cycles: list[Cycle] = []
    index_reversion: float = Field(0.08, gt=0, le=1)
    index_volatility: float = Field(0.012, ge=0)
    index_range: tuple[float, float] = (0.45, 2.2)
    # single cards
    card_reversion: float = Field(0.06, gt=0, le=1)
    card_volatility: float = Field(0.04, ge=0)
    factor_range: tuple[float, float] = (0.25, 6.0)
    # events
    reprint_chance: float = Field(0.02, ge=0, le=1)  # per tick
    reprint_share: float = Field(0.03, ge=0, le=1)  # of all cards, taken from the most valuable fifth
    reprint_growth: tuple[float, float] = (1.5, 3.0)
    reserved_below: int = Field(0, ge=0)  # print runs under this are never reprinted (a reserved list)
    hype_per_tick: float = Field(0.5, ge=0)
    hype_boost: tuple[float, float] = (1.3, 2.2)
    buyout_chance: float = Field(0.03, ge=0, le=1)
    buyout_boost: tuple[float, float] = (2.0, 3.5)
    # v0.8.0, momentum (game/market.step_run): a hype or a buyout starts a run. Its first step is
    # `momentum_share` of the spike (in log points). At every later tick the price rises by the
    # step with chance `momentum_chance` and the step shrinks to `momentum_decay` of itself, or the
    # run breaks and the price drops by as much as makes the two cancel out on average.
    momentum_share: float = Field(0.0, ge=0, le=1)  # 0 = no momentum
    momentum_chance: float = Field(0.6, ge=0, lt=1)
    momentum_decay: float = Field(0.6, ge=0, lt=1)


class Deal(BaseModel):
    """A rotating discount in the shop: one pack, cheaper, for a few hours, a few times."""

    hours: float = Field(0, ge=0)  # length of one deal window (0 = no deals)
    discount: tuple[float, float] = (0.2, 0.4)
    per_window: int = Field(2, ge=1)


class RarityDecay(BaseModel):
    max: float = Field(ge=0, lt=1)


class Upgrades(BaseModel):
    # v0.7.1: the paid attempt can be switched off. Off: no card has an upgrade cost, an attempt is
    # refused and fusing is the one way up. The code stays; tests for it switch it on.
    enabled: bool = True
    chance: float = Field(ge=0, le=1)
    cost: dict[str, int]  # current best treatment → coins per attempt


class GoalTemplate(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    title: str
    # v0.6.0: fuse, sell, market-coins and buy need no untagged cards, so they work in a finished library.
    kind: Literal["pull", "upgrade", "tag", "review", "open", "fuse", "sell", "market-coins", "buy"]
    period: Literal["week", "day"] = "week"
    treatment: str | None = None  # for kind = "pull"
    target: int = Field(gt=0)
    reward: PackReward


class Goals(BaseModel):
    per_week: int = 3
    unlock_after_packs: int = 0  # weekly goals are a late-game feature: hidden until this many packs
    per_day: int = 2
    daily_unlock_after_packs: int = 0  # daily goals: hidden until this many packs
    templates: list[GoalTemplate] = []


class Mastery(BaseModel):
    """A second progress number (game/mastery.py): points for rarer treatments and for fusing."""

    points: dict[str, int] = {}  # per owned card, by its best treatment
    fuse_points: int = Field(0, ge=0)  # per fuse ever done
    level_base: float = Field(10, gt=0)  # level L is reached at round(level_base × L ^ level_exponent) points
    level_exponent: float = Field(1.6, ge=1)
    # v0.9.0: a level pays sealed packs, once. `reward` for every level; a level that is a multiple
    # of a `reward_every` step pays that step's packs instead (the biggest step that fits wins).
    reward: PackReward | None = None
    reward_every: list["MasteryStep"] = []


class MasteryStep(BaseModel):
    every: int = Field(gt=1)
    pack: str
    count: int = Field(1, ge=1)


class Pity(BaseModel):
    """The dry streak, and (behind `enabled`, off since v0.9.0) the soft pity: after a dry run of
    packs the odds of the named treatments rise until one drops."""

    enabled: bool = True  # false: every pack has the same odds; the dry streak is only counted
    treatments: list[str] = []  # a pull of any of these ends the dry run
    after_packs: int = Field(0, ge=0)  # dry packs before the boost starts (0 = pity off)
    boost_per_pack: float = Field(0, ge=0)  # each further dry pack adds this to the multiplier
    max_boost: float = Field(1, ge=1)  # the multiplier never exceeds this


class FreePack(BaseModel):
    """One free pack per local day, after the daily check-in."""

    pack: str = ""  # pack id; empty = no free pack
    min_streak: int = Field(1, ge=1)  # check-in streak needed (1 = just check in today)


class Sets(BaseModel):
    min_size: int = 5
    treatments: list[str] = []
    # v0.9.0: what completing a set at a treatment pays, as packs. A set of `large_size` cards or
    # more pays twice the count.
    rewards: dict[str, PackReward] = {}
    large_size: int = Field(0, ge=0)  # 0 = no doubling


class Reveal(BaseModel):
    """What the pack reveal shows before a card is turned (v0.9.0, decision D6)."""

    hint_true: float = Field(1.0, ge=0, le=1)  # a pull that fills a version slot glows with this chance
    hint_false: float = Field(0.0, ge=0, le=1)  # a pull that fills nothing glows with this chance (a near miss)
    rarest_last: float = Field(1.0, ge=0, le=1)  # the rarest card is revealed last with this chance


class OnboardingStep(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    title: str
    kind: Literal["media", "tagged", "packs", "tagged_owned", "upgrades"]
    target: int = Field(gt=0)
    reward: PackReward | None = None  # v0.9.0: packs, and only some steps pay


class Onboarding(BaseModel):
    # v0.9.0: paid once, when every tour in `tours` has been completed (not skipped)
    tours_reward: PackReward | None = None
    tours: list[str] = []  # the tours that count (allowlist: arbitrary ids never count)
    steps: list[OnboardingStep] = []


class Limits(BaseModel):
    organize_per_second: float = Field(5, gt=0)
    organize_burst: int = Field(30, gt=0)
    bulk_max_items: int = Field(200, gt=0)


class Rtp(BaseModel):
    assumed_tags_per_card: int
    early_target: tuple[float, float]  # checked by tests/test_packs_roll.py


class EconomyConfig(BaseModel):
    starting_coins: int
    ingest: Ingest
    tagging: Tagging
    milestones: list[Milestone]
    completeness: Completeness = Completeness()
    treatments: dict[str, Treatment]
    packs: dict[str, Pack]
    inflation: Inflation
    selling: Selling
    duplicates: Duplicates
    rarity_decay: RarityDecay
    upgrades: Upgrades = Upgrades(chance=0, cost={})
    goals: Goals = Goals()
    sets: Sets = Sets()
    mastery: Mastery = Mastery()
    fuse: Fuse = Fuse()
    market: Market = Market()
    deal: Deal = Deal()
    pity: Pity = Pity()
    reveal: Reveal = Reveal()
    free_pack: FreePack = FreePack()
    limits: Limits = Limits()
    onboarding: Onboarding = Onboarding()
    rtp: Rtp

    @model_validator(mode="after")
    def _check(self) -> "EconomyConfig":
        for k, t in self.treatments.items():
            t.id = k
        known = set(self.treatments)
        for name, ids in (
            ("upgrades.cost", set(self.upgrades.cost)),
            ("sets.treatments", set(self.sets.treatments)),
            ("pity.treatments", set(self.pity.treatments)),
            ("market.treatment_value", set(self.market.treatment_value)),
            ("goals.templates.treatment", {g.treatment for g in self.goals.templates if g.treatment}),
            ("mastery.points", set(self.mastery.points)),
        ):
            if ids - known:
                raise ValueError(f"{name} uses unknown treatments {sorted(ids - known)}")
        gids = [g.id for g in self.goals.templates]
        if len(set(gids)) != len(gids):
            raise ValueError("goal template ids must be unique")
        oids = [o.id for o in self.onboarding.steps]
        if len(set(oids)) != len(oids):
            raise ValueError("onboarding step ids must be unique")
        if len(set(self.onboarding.tours)) != len(self.onboarding.tours):
            raise ValueError("onboarding tour ids must be unique")
        for t in self.onboarding.tours:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", t):
                raise ValueError(f"bad onboarding tour id {t!r}")
        if self.free_pack.pack and self.free_pack.pack not in self.packs:
            raise ValueError(f"free_pack.pack {self.free_pack.pack!r} is not a pack")
        # Every reward is packs (v0.9.0): each must name a pack that exists.
        named = [("milestones", m.reward.pack) for m in self.milestones]
        named += [(f"goal {g.id!r}", g.reward.pack) for g in self.goals.templates]
        named += [(f"onboarding step {o.id!r}", o.reward.pack) for o in self.onboarding.steps if o.reward]
        named += [(f"sets.rewards.{t}", r.pack) for t, r in self.sets.rewards.items()]
        named += [("mastery.reward_every", r.pack) for r in self.mastery.reward_every]
        if self.mastery.reward:
            named.append(("mastery.reward", self.mastery.reward.pack))
        if self.onboarding.tours_reward:
            named.append(("onboarding.tours_reward", self.onboarding.tours_reward.pack))
        for where, pack in named:
            if pack not in self.packs:
                raise ValueError(f"{where}: reward names an unknown pack {pack!r}")
        if set(self.sets.rewards) - known:
            raise ValueError(f"sets.rewards uses unknown treatments {sorted(set(self.sets.rewards) - known)}")
        if any(g.kind == "pull" and not g.treatment for g in self.goals.templates):
            raise ValueError("pull goals need a treatment")
        for k, p in self.packs.items():
            p.id = k
            unknown = set(p.weights) - set(self.treatments)
            if unknown:
                raise ValueError(f"pack {k!r} overrides unknown treatments {sorted(unknown)}")
        tiers = [t.tier for t in self.treatments.values()]
        if len(set(tiers)) != len(tiers):
            raise ValueError("treatment tiers must be unique")
        if "normal" not in self.treatments:
            raise ValueError("a 'normal' treatment is required")
        # No way of buying on the market and selling again may pay (v0.7.1, widened in v0.8.0).
        m = self.market
        lo, hi = m.premium_range
        if not 0 < lo <= hi:
            raise ValueError("market.premium_range must be positive and ascending")
        values = [m.treatment_value.get(t.id, 1.0) for t in self.tiers()]
        if any(v <= 0 for v in values) or values != sorted(values):
            raise ValueError("market.treatment_value must be positive and rise with the treatment's tier")
        # 1. Buy a listed copy and sell it straight back, as a wanted card.
        if m.sell_rate * m.wanted_bonus * (1 + m.buy_impact) >= m.buy_markup:
            raise ValueError(
                "market.sell_rate × market.wanted_bonus (× 1 + buy_impact) must stay below market.buy_markup: "
                f"{m.sell_rate} × {m.wanted_bonus} × {1 + m.buy_impact} = "
                f"{m.sell_rate * m.wanted_bonus * (1 + m.buy_impact):.4f}, buy_markup = {m.buy_markup}"
            )
        # 2. Buy a listed copy and wait for a collector's offer on it.
        if m.offer_range[0] > m.offer_range[1] or m.offer_range[1] * (1 + m.buy_impact) >= m.buy_markup:
            raise ValueError(
                "market.offer_range must be ascending and its upper end (× 1 + buy_impact) must stay below "
                f"market.buy_markup ({m.buy_markup}): a listed copy could be bought and handed to a collector for more"
            )
        # 3. Buy copies, fuse them and sell the result. Only a card whose fuse takes more than the
        # base count is listed or wanted (game/market.tradeable), so the loop has `fuse.copies + 1`
        # copies per fuse at the least. Two regimes: a plain sale, and a sale as a wanted card.
        for wanted in (False, True):
            worst = loop_return(self, wanted=wanted)
            if worst >= 1:
                raise ValueError(
                    f"buying copies, fusing them and selling the result{' as a wanted card' if wanted else ''} would pay: "
                    f"the sale returns {worst:.3f} of the cost. Lower market.treatment_value, market.premium_range, "
                    "market.wanted_bonus or market.buy_impact, or raise fuse.copies or market.buy_markup"
                )
        # 4. Sell copies to push the price down, buy them back cheaper, wait for it to recover.
        dump = dump_return(self)
        if dump >= 1:
            raise ValueError(
                f"market.trade_impact = {m.trade_impact} lets a player move a price: dumping copies and buying "
                f"them back at the lowered price returns {dump:.3f} of what the buy-back costs. Set it to 0 "
                "(the player is one seller among millions)"
            )
        # A run must stay a fair gamble: the chance of a rise times the rise may not reach 1,
        # or no drop could balance it (game/market.run_drop).
        biggest = max(m.hype_boost[1], m.buyout_boost[1], 1.0)
        if m.momentum_share > 0 and m.momentum_chance * biggest**m.momentum_share >= 0.95:
            raise ValueError(
                "market.momentum_chance × (the biggest hype or buyout boost) ^ market.momentum_share must stay "
                f"below 0.95: {m.momentum_chance} × {biggest} ^ {m.momentum_share} = "
                f"{m.momentum_chance * biggest**m.momentum_share:.3f}. Lower momentum_share or momentum_chance"
            )
        self.milestones.sort(key=lambda m: m.at)
        return self

    def tiers(self) -> list[Treatment]:
        """Treatments ordered common → rare."""
        return sorted(self.treatments.values(), key=lambda t: t.tier)


def loop_return(cfg: EconomyConfig, premium: float | None = None, wanted: bool = True) -> float:
    """The best a buy, fuse and sell loop can return, as a share of what it costs (below 1 = a loss).

    For every pair of treatments a below b: buy `(fuse.copies + 1) ** steps` copies at a (each at
    the buy markup, each purchase raising the price by `buy_impact`), fuse them up to one copy at
    b, and sell that one at the price the purchases left, plain or with the wanted bonus.

    `fuse.copies + 1` is the fewest copies a fuse of a card that can be BOUGHT takes: only cards
    whose Normal price has reached the first of `fuse.price_steps` are listed (and wanted), and from
    there a fuse takes one copy more than the base. Without price steps nothing is listed and no
    loop exists (0). `premium` is the card's exponent (default: the top of `market.premium_range`,
    the worst case because the treatment values rise with the tier).
    Rounding to whole coins is left out here; the tests check it on whole prices."""
    if not cfg.fuse.price_steps:
        return 0.0
    m = cfg.market
    k = m.premium_range[1] if premium is None else premium
    values = [m.treatment_value.get(t.id, 1.0) for t in cfg.tiers()]
    up = 1 + m.buy_impact
    pays = m.sell_rate * (m.wanted_bonus if wanted else 1.0)
    need = cfg.fuse.copies + 1
    worst = 0.0
    for a in range(len(values)):
        for b in range(a + 1, len(values)):
            n = need ** (b - a)
            cost = m.buy_markup * (sum(up**i for i in range(n)) if up > 1 else n)
            worst = max(worst, pays * (values[b] / values[a]) ** k * up**n / cost)
    return worst


def dump_return(cfg: EconomyConfig) -> float:
    """The best a dump-and-buy-back round can return, as a share of what the buy-back costs
    (below 1 = a loss).

    The round: sell n copies of a listed card one after another (each sale lowers the price by
    `trade_impact`, the best case being a wanted card), buy n copies back at the lowered price,
    and wait for the price to recover. The player then holds what they held and keeps the
    difference, so the round can be repeated. n is bounded by `factor_range`: the price cannot be
    pushed further down than from its top to its floor.

    With `trade_impact = 0` the price does not move and this is the plain sell-and-buy-back
    return, `sell_rate × wanted_bonus ÷ buy_markup` (0.96 at the live numbers). With any impact
    worth the name it is far above 1 (about 7 at 0.03). Without price steps nothing is listed,
    nothing can be bought back and no round exists (0)."""
    if not cfg.fuse.price_steps:
        return 0.0
    m = cfg.market
    pays = m.sell_rate * m.wanted_bonus
    best = pays / m.buy_markup
    if m.trade_impact <= 0:
        return best
    keep = 1 - m.trade_impact
    floor = m.factor_range[0] / m.factor_range[1]
    level, sold = 1.0, 0.0  # the price after n sales, and what the n sales paid (in start prices)
    for n in range(1, 100_001):
        sold += pays * level
        level *= keep
        if level < floor:
            break
        best = max(best, sold / (m.buy_markup * n * level))
    return best


def load(path: Path = CONFIG_PATH) -> EconomyConfig:
    with path.open("rb") as f:
        return EconomyConfig.model_validate(tomllib.load(f))


_cache: tuple[float, EconomyConfig] | None = None


def get_config() -> EconomyConfig:
    """Current config, reloaded when the file's mtime changes. A broken edit keeps the last good one."""
    global _cache
    mtime = CONFIG_PATH.stat().st_mtime
    if _cache is None or _cache[0] != mtime:
        try:
            _cache = (mtime, load())
        except Exception:
            if _cache is None:
                raise
            import logging

            logging.getLogger("mco.economy").exception("economy.toml invalid; keeping previous config")
    return _cache[1]
