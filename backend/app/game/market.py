"""The card market: pure rules, no database (the simulation is driven by services/market.py).

What is simulated, and why (see docs/economy/technical-reference.md, "The market"):

* **Print run.** Every card gets a number of copies "issued" when it is scanned in. A few cards have
  tiny print runs, most have huge ones. Scarcity sets the value (a card printed 50 times is worth
  far more than one printed 40,000 times) AND the drop rate (a pack pulls a card in proportion to
  a power of its print run), so the valuable cards are also the ones that are hard to find.
* **Demand.** Each card has a popularity that has nothing to do with its treatment: a plain Normal
  copy of a scarce, wanted card outprices a Foil of a mass-printed one.
* **Treatment.** A Foil, Holo or Special copy multiplies the card's value. By how much differs per
  card (v0.8.0): every card has its own premium exponent, higher for popular cards, as in real
  singles markets, where a foil of a sought-after card costs several times the plain one and a foil
  of a bulk card hardly more.
* **The market as a whole** moves in cycles: a long wave over about two years, a medium one over
  months and a short one over weeks, plus noise, like the boom of 2020-21 and the correction after.
* **Each card** drifts around its fair value (mean reversion plus noise).
* **Events.** Now and then the issuer reprints a batch of sought-after cards (their print runs
  grow, so their value AND their scarcity in packs drop), a card gets hyped (a spike that fades),
  or a buyout squeezes one card. The very scarcest prints are a reserved list: never reprinted.
* **Momentum** (v0.8.0). A hype or a buyout starts a *run*: at each of the next updates the card
  either rises again (a smaller step each time) or the run breaks and the price drops back a good
  part. Which of the two is a toss weighted toward "rises", and the drop is sized so that the two
  cancel out exactly on average (`step_run`): a player who buys into a spike may win or lose, but
  gains nothing on average, and after the buy and sell margins loses.
* **Your own trades do not move a price** (v0.8.0). The player is one collector among millions:
  `trade_impact` and `buy_impact` are both 0. The rule is still here behind the numbers (`nudge`),
  but a sale that lowers the price lets a player dump copies, buy them back cheaper and wait, so
  the config refuses it while cards are listed (`config.dump_return`).
* **Only cards worth trading are traded** (v0.8.0): a card is for sale, and can be wanted, only
  when its Normal price has reached the first fuse price step (`tradeable`). Bulk is not sold as
  singles, as in real card shops. It is also what keeps buying, fusing and selling from paying:
  a card that can be bought takes at least one copy more than the base count to fuse.

Time moves in ticks (`market.tick_hours`, 6 by default). Every function here is deterministic given
its random generator, so tests and the simulator can replay a market.
"""

import math
import random
from dataclasses import dataclass, field

from .config import EconomyConfig

HISTORY = 124  # ticks of price history kept per card (31 days at 6 hours)
RUN_END = 0.01  # a run (momentum) whose next step is under 1% is over


@dataclass
class CardState:
    """One card on the market."""

    media_id: int
    print_run: int
    popularity: float = 1.0
    factor: float = 1.0  # the card's own price level around its fair value (1 = fair)
    history: list[int] = field(default_factory=list)  # Normal price at the last ticks, oldest first
    k: float | None = None  # the premium exponent, worked out on first use (see `premium`)
    run: float = 0.0  # momentum: the next step of a running spike, in log points (0 = no run)


@dataclass
class News:
    kind: str  # "reprint" | "hype" | "buyout" | "boom" | "bust"
    text: str
    media_ids: list[int] = field(default_factory=list)


def tick_of(cfg: EconomyConfig, epoch_seconds: float) -> int:
    """The market tick a moment falls in (ticks are aligned to the clock, e.g. 00:00, 06:00 UTC)."""
    return int(epoch_seconds // (cfg.market.tick_hours * 3600))


def tick_start(cfg: EconomyConfig, tick: int) -> float:
    return tick * cfg.market.tick_hours * 3600.0


# ---------------------------------------------------------------- issuing a card


def roll_print_run(cfg: EconomyConfig, rng: random.Random) -> int:
    """How many copies of a new card are 'issued': a tier by its share, then a number inside it."""
    tiers = cfg.market.print_tiers
    r = rng.random() * sum(t.share for t in tiers)
    for t in tiers:
        r -= t.share
        if r < 0:
            return rng.randint(t.min, t.max)
    return rng.randint(tiers[-1].min, tiers[-1].max)


def roll_popularity(cfg: EconomyConfig, rng: random.Random) -> float:
    """Demand for the card itself, independent of scarcity and treatment (log-normal around 1)."""
    lo, hi = cfg.market.popularity_range
    return max(lo, min(hi, math.exp(rng.gauss(0.0, cfg.market.popularity_sigma))))


def print_tier_name(cfg: EconomyConfig, print_run: int) -> str:
    """The name of the print tier a print run falls in (reprints can move a card to a bigger one)."""
    tiers = sorted(cfg.market.print_tiers, key=lambda t: t.max)
    for t in tiers:
        if print_run <= t.max:
            return t.name
    return tiers[-1].name


def min_pack(cfg: EconomyConfig, print_run: int) -> str | None:
    """The cheapest pack that holds a card with this print run, or None when every pack does.

    The scarcest cards are only in the dearer packs: a reason to buy those."""
    tiers = sorted(cfg.market.print_tiers, key=lambda t: t.max)
    tier = next((t for t in tiers if print_run <= t.max), tiers[-1])
    return tier.min_pack if tier.min_pack in cfg.packs else None


def pack_holds(cfg: EconomyConfig, pack_id: str, print_run: int) -> bool:
    """Can this pack pull a card with this print run? A pack holds what every cheaper pack holds."""
    need = min_pack(cfg, print_run)
    if need is None or pack_id not in cfg.packs:
        return True
    return cfg.packs[pack_id].base_cost >= cfg.packs[need].base_cost


# ---------------------------------------------------------------- value


def fair_value(cfg: EconomyConfig, print_run: int, popularity: float = 1.0) -> float:
    """Coins a Normal copy is worth when the market is flat: scarcer and more wanted = more.

    Demand counts for more the scarcer the card is (`market.demand_leverage`). In a real set the
    commons all cost about the same however loved the character is (there are far more copies than
    buyers), while the cards of one and the same top rarity differ 20-40 times by demand alone. So
    the popularity is raised to a power: 1 at the reference print run, plus `demand_leverage` for
    every ten times fewer copies. Only a wanted card (popularity above 1) takes off like that; an
    unloved scarce card keeps its plain scarcity value, as the cheapest top-rarity cards of a real
    set still cost many times a common. With 0 demand counts the same for every card.
    """
    m = cfg.market
    ratio = m.reference_print / max(1, print_run)
    leverage = max(0.0, 1.0 + m.demand_leverage * math.log10(ratio))
    if popularity < 1.0:
        leverage = min(1.0, leverage)
    return m.base_value * ratio**m.elasticity * max(1e-9, popularity) ** leverage


def premium(cfg: EconomyConfig, popularity: float, media_id: int) -> float:
    """A card's premium exponent k: how much of the full treatment premium its rarer copies carry.

    Fixed per card and inside `market.premium_range`. `premium_demand_share` of it follows the
    card's popularity (0 at two sigma below the typical card, 1 at two sigma above), the rest is the
    card's own, spread evenly over the cards by their id. So a sought-after card has a high premium,
    a bulk card a low one, and two equally popular cards still differ."""
    m = cfg.market
    lo, hi = m.premium_range
    if m.popularity_sigma > 0:
        demand = max(0.0, min(1.0, 0.5 + math.log(max(1e-9, popularity)) / (4 * m.popularity_sigma)))
    else:
        demand = 0.5
    own = ((media_id * 2654435761) & 0xFFFFFFFF) / 2**32  # the same for a card for good
    share = m.premium_demand_share
    return lo + (hi - lo) * (share * demand + (1 - share) * own)


def treatment_factor(cfg: EconomyConfig, card: CardState, treatment: str = "normal") -> float:
    """What a copy at this treatment costs as a multiple of the card's Normal copy:
    `treatment_value[treatment] ** k`, with the card's own premium exponent k. 1 for Normal."""
    base = cfg.market.treatment_value.get(treatment, 1.0)
    if base == 1.0:
        return 1.0
    if card.k is None:
        card.k = premium(cfg, card.popularity, card.media_id)
    return base**card.k


def price(cfg: EconomyConfig, card: CardState, index: float, treatment: str = "normal") -> int:
    """Today's market price of one copy, in whole coins (never below 1)."""
    mult = treatment_factor(cfg, card, treatment)
    return max(1, round(fair_value(cfg, card.print_run, card.popularity) * card.factor * index * mult))


def sell_price(cfg: EconomyConfig, market_price: int, wanted: bool = False) -> int:
    """What the market pays you for a copy: the price minus its cut, plus the bonus when a collector wants it."""
    bonus = cfg.market.wanted_bonus if wanted else 1.0
    return max(1, int(market_price * cfg.market.sell_rate * bonus))


def sale_prices(cfg: EconomyConfig, card: CardState, index: float, treatment: str, count: int,
                wanted: bool = False) -> list[int]:
    """What selling `count` copies one after another pays, copy by copy. Leaves `card` as it is.
    Used for the sale itself and for every quote shown before it, so the two can't differ.

    Since v0.8.0 every copy pays the same (`trade_impact = 0`: the player's own sale does not move
    the price). With a `trade_impact` above 0 every sale lowers the price by it for the next one."""
    step = CardState(card.media_id, card.print_run, card.popularity, card.factor, k=card.k)
    out = []
    for _ in range(max(0, count)):
        out.append(sell_price(cfg, price(cfg, step, index, treatment), wanted))
        nudge(cfg, step, sold=True)
    return out


def buy_price(cfg: EconomyConfig, market_price: int) -> int:
    """What a listed card costs you: the price plus the market's markup."""
    return max(1, math.ceil(market_price * cfg.market.buy_markup))


def draw_weight(cfg: EconomyConfig, print_run: int) -> float:
    """How likely a pack slot is to pull this card, relative to the others: more copies issued = more often."""
    return max(1, print_run) ** cfg.market.drop_exponent


# ---------------------------------------------------------------- one tick


def index_target(cfg: EconomyConfig, tick: int) -> float:
    """Where the cycles alone would put the (log) market index at this tick."""
    m = cfg.market
    per_day = 24 / m.tick_hours
    total = 0.0
    for c in m.cycles:
        total += c.amplitude * math.sin(2 * math.pi * tick / (c.days * per_day) + c.phase)
    return total


def step_index(cfg: EconomyConfig, tick: int, log_index: float, rng: random.Random) -> float:
    """The market index one tick later: pulled toward the cycles, plus noise. Returned as a log."""
    m = cfg.market
    target = index_target(cfg, tick)
    nxt = log_index + m.index_reversion * (target - log_index) + rng.gauss(0.0, m.index_volatility)
    lo, hi = math.log(m.index_range[0]), math.log(m.index_range[1])
    return max(lo, min(hi, nxt))


def step_card(cfg: EconomyConfig, card: CardState, rng: random.Random) -> None:
    """A card's own price level one tick later: it drifts back toward fair value, with noise."""
    m = cfg.market
    log_f = math.log(max(1e-6, card.factor))
    log_f += -m.card_reversion * log_f + rng.gauss(0.0, m.card_volatility)
    lo, hi = m.factor_range
    card.factor = max(lo, min(hi, math.exp(log_f)))


def run_events(cfg: EconomyConfig, cards: list[CardState], rng: random.Random) -> list[News]:
    """The rare things that shake single cards: reprints, hype and buyouts, and the runs that an
    earlier hype or buyout started (`step_run`). Changes `cards` in place.

    The runs are stepped last, with the last random numbers of the tick, so the events themselves
    fall exactly as they would without momentum. A run that starts in this tick takes its first
    step at the next one."""
    running = [c for c in cards if c.run > 0]
    started: dict[int, float] = {}
    news = _events(cfg, cards, rng, started)
    for c in running:
        step_run(cfg, c, rng)
    if started:
        by_id = {c.media_id: c for c in cards}
        for media_id, heat in started.items():
            by_id[media_id].run = max(by_id[media_id].run, heat)
    return news


def run_start(cfg: EconomyConfig, boost: float) -> float:
    """The first step of the run that a spike of `boost` (a factor, e.g. 1.8) starts, in log
    points: `momentum_share` of the spike. 0 = no run (momentum off, or a step under 1%)."""
    heat = cfg.market.momentum_share * math.log(max(1.0, boost))
    return heat if heat >= RUN_END else 0.0


def run_drop(cfg: EconomyConfig, heat: float) -> float:
    """What the price is multiplied by when a run whose next step is `heat` breaks.

    Chosen so that the two outcomes cancel out: with chance p the price is multiplied by
    `exp(heat)`, else by this, and `p × exp(heat) + (1 − p) × drop = 1`. So whatever the player
    knows about a run (that it is on, how big its next step is), the price one tick later is on
    average the price now. The config check keeps `p × exp(heat)` under 1 for the biggest spike."""
    p = cfg.market.momentum_chance
    return max(1e-6, (1.0 - p * math.exp(heat)) / (1.0 - p)) if p < 1 else 1.0


def step_run(cfg: EconomyConfig, card: CardState, rng: random.Random) -> None:
    """One tick of a card's run (momentum after a hype or a buyout).

    With chance `momentum_chance` the price rises by the run's step, and the next step is
    `momentum_decay` of it (the run is over once a step is under 1%). Otherwise the run breaks:
    the price is multiplied by `run_drop` and the run is over. Fair on average, see `run_drop`."""
    m = cfg.market
    heat = card.run
    if heat <= 0:
        return
    lo, hi = m.factor_range
    if rng.random() < m.momentum_chance:
        card.factor = max(lo, min(hi, card.factor * math.exp(heat)))
        heat *= m.momentum_decay
        card.run = heat if heat >= RUN_END else 0.0
    else:
        card.factor = max(lo, min(hi, card.factor * run_drop(cfg, heat)))
        card.run = 0.0


def _events(cfg: EconomyConfig, cards: list[CardState], rng: random.Random, started: dict[int, float]) -> list[News]:
    """Reprints, hype and buyouts. `started` collects the runs they start: media id → first step."""
    m = cfg.market
    news: list[News] = []
    if not cards:
        return news

    # The issuer reprints sought-after cards: more supply, lower value, and they show up in packs more.
    if rng.random() < m.reprint_chance:
        ranked = sorted(cards, key=lambda c: fair_value(cfg, c.print_run, c.popularity) * c.factor, reverse=True)
        # Reprints land in the most valuable fifth, but never on the reserved list (the scarcest prints).
        pool = [c for c in ranked[: max(1, len(ranked) // 5)] if c.print_run >= m.reserved_below]
        n = max(1, round(len(cards) * m.reprint_share))
        hit = rng.sample(pool, min(n, len(pool)))
        if not hit:
            return news
        for c in hit:
            c.print_run = round(c.print_run * rng.uniform(*m.reprint_growth))
            c.factor = max(m.factor_range[0], c.factor * rng.uniform(0.75, 0.95))  # the announcement alone scares buyers
        news.append(News("reprint", f"The issuer reprinted {len(hit)} sought-after {'card' if len(hit) == 1 else 'cards'}: "
                                    "more copies exist now, so they are worth less and easier to pull.",
                         [c.media_id for c in hit]))

    # Hype: a card is suddenly wanted. The spike fades as the factor reverts.
    for _ in range(_poisson(m.hype_per_tick, rng)):
        c = rng.choice(cards)
        boost = rng.uniform(*m.hype_boost)
        c.factor = min(m.factor_range[1], c.factor * boost)
        started[c.media_id] = max(started.get(c.media_id, 0.0), run_start(cfg, boost))
        news.append(News("hype", "A card is in demand: its price jumped.", [c.media_id]))

    # A buyout: someone corners a scarce card.
    if rng.random() < m.buyout_chance:
        scarce = sorted(cards, key=lambda c: c.print_run)[: max(1, len(cards) // 20)]
        c = rng.choice(scarce)
        boost = rng.uniform(*m.buyout_boost)
        c.factor = min(m.factor_range[1], c.factor * boost)
        started[c.media_id] = max(started.get(c.media_id, 0.0), run_start(cfg, boost))
        news.append(News("buyout", "A buyout: one of the scarcest cards was bought up, and its price shot up.", [c.media_id]))
    return news


def _poisson(mean: float, rng: random.Random) -> int:
    """A small Poisson draw (Knuth), enough for event counts around or below 1."""
    if mean <= 0:
        return 0
    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def mood(log_index: float, previous: float) -> str:
    """One word for the whole market, for the page: boom, rising, flat, falling or bust."""
    level, move = math.exp(log_index), log_index - previous
    if level >= 1.25:
        return "boom"
    if level <= 0.8:
        return "bust"
    if move > 0.004:
        return "rising"
    if move < -0.004:
        return "falling"
    return "flat"


def tradeable(cfg: EconomyConfig, tick_price: int, live_price: int | None = None) -> bool:
    """Is this card traded as a single: listed for sale, and able to be wanted? (v0.8.0)

    Only when a fuse of it takes more than the base count, i.e. its Normal price at the last price
    update (`tick_price`, what the fuse count is read from) has reached the first of
    `fuse.price_steps`. Bulk cards are not traded as singles. With `live_price` (the Normal price
    right now; it differs from `tick_price` only when `trade_impact` is switched on and the
    player's own sales lowered it) that must have reached the step too, so a copy is never bought
    under it. Without price steps no card is tradeable.

    Why: the fuse count, the listing and the wanted mark all come from the same price update, so
    within one update a card that can be bought takes `fuse.copies + 1` copies or more, and the
    config check (`config.loop_return`) covers exactly that. Across an update the price may fall
    under the step: the fuse then takes the base count, but the card is no longer listed or wanted,
    its Normal price is under the step every copy was bought at or above, and a plain sale of what
    the base count makes pays less than those copies cost (0.85 × 4 < 3 × 1.15 at the live
    numbers). It cannot be repeated either: nothing more of the card can be bought until its price
    is back at the step, where the fuse takes the extra copy again."""
    steps = cfg.fuse.price_steps
    if not steps:
        return False
    return tick_price >= steps[0] and (live_price is None or live_price >= steps[0])


def pick_wanted(cfg: EconomyConfig, held_value: dict[int, int], rng: random.Random,
                normal_price: dict[int, int] | None = None) -> list[int]:
    """The cards collectors want this tick: up to `wanted_count` of the cards the player owns.

    held_value: media id → market price of the player's most valuable copy of that card.
    normal_price: media id → the card's Normal price at this tick. Given, only `tradeable` cards
    can be wanted (the same cards that can be listed).
    v0.8.0: only cards whose copy is worth at least `wanted_min_value` can be wanted (a bonus on an
    8-coin card is not worth a message), so fewer than `wanted_count` may come back."""
    m = cfg.market
    pool = sorted(i for i, v in held_value.items() if v >= m.wanted_min_value
                  and (normal_price is None or tradeable(cfg, normal_price.get(i, 0))))
    return rng.sample(pool, min(m.wanted_count, len(pool)))


def roll_offer(cfg: EconomyConfig, holdings: list[tuple[int, str, int]], rng: random.Random,
               wanted: frozenset[int] | set[int] = frozenset()) -> tuple[int, str, int] | None:
    """A collector's offer for this tick, or None.

    holdings: (media id, treatment, market price) for every copy kind the player owns.
    wanted: the cards collectors want this tick. No offer is made for one of them: selling it
    pays the wanted bonus, which beats any offer.
    Returns (media id, treatment, coins offered). The offer is for one of the most valuable ones
    and lands just below or above the market price, so taking it is a real decision. An offer
    below what a plain sale pays is not made (v0.7.1).
    """
    m = cfg.market
    best = sorted((h for h in holdings if h[2] >= m.offer_min_value and h[0] not in wanted),
                  key=lambda h: -h[2])[: m.offer_top]
    if not best or rng.random() >= m.offer_chance:
        return None
    media_id, treatment, market_price = rng.choice(best)
    coins = max(1, round(market_price * rng.uniform(*m.offer_range)))
    if coins < sell_price(cfg, market_price):
        return None
    return media_id, treatment, coins


def tick_rng(seed: int, tick: int) -> random.Random:
    """The generator for one tick of one market: the same seed and tick always replay the same tick."""
    return random.Random(seed * 1_000_003 + tick)


def advance(cfg: EconomyConfig, cards: list[CardState], seed: int, tick: int, log_index: float) -> tuple[float, list[News]]:
    """Move the whole market from `tick - 1` to `tick`. Returns the new log index and the news."""
    rng = tick_rng(seed, tick)
    new_index = step_index(cfg, tick, log_index, rng)
    for c in cards:
        step_card(cfg, c, rng)
    news = run_events(cfg, cards, rng)
    index = math.exp(new_index)
    for c in cards:
        c.history.append(price(cfg, c, index))
        if len(c.history) > HISTORY:
            del c.history[: len(c.history) - HISTORY]
    return new_index, news


def trend(history: list[int], ticks: int = 4) -> float:
    """Price change over the last `ticks` ticks as a fraction (0.12 = up 12%); 0 without enough history."""
    if len(history) < 2:
        return 0.0
    then = history[max(0, len(history) - 1 - ticks)]
    return (history[-1] - then) / then if then else 0.0


def changes(cfg: EconomyConfig, history: list[int]) -> dict[str, float]:
    """A card's price change over each range the page can show, as fractions (0.12 = up 12%):

    update: the last price update against the one before (one tick, 6 hours)
    day:    against 24 hours ago (the same number as `trend`)
    week:   against 7 days ago
    month:  against 30 days ago

    A history shorter than a range is measured from its oldest price. All 0 without two prices.
    They are worked out on the Normal price and hold for every treatment of the card, because a
    rarer copy costs a fixed multiple of the card's Normal copy."""
    per_day = max(1, round(24 / cfg.market.tick_hours))
    return {name: round(trend(history, ticks), 4)
            for name, ticks in (("update", 1), ("day", per_day), ("week", 7 * per_day), ("month", 30 * per_day))}


def daily_table(dated: list[tuple[str, int]], days: int = 30) -> list[dict]:
    """A price history as one row per day, for the 30-day table in the card panel.

    dated: (day, price) for every tick, oldest first; `day` is the ISO date the tick falls on.
    Returns the last `days` days, oldest first: the price at the end of the day (its last tick; for
    today the latest one) and the change against the day before, as a fraction (`change`) and in
    coins (`change_coins`). Both are None for a day without a day before it in the history."""
    closes: dict[str, int] = {}
    for day, value in dated:
        closes[day] = value  # later ticks of the same day overwrite: the last one is the close
    out = []
    prev: int | None = None
    for day, value in closes.items():
        out.append({
            "date": day,
            "price": value,
            "change": round(value / prev - 1, 4) if prev else None,
            "change_coins": value - prev if prev is not None else None,
        })
        prev = value
    return out[-max(1, days):]


def nudge(cfg: EconomyConfig, card: CardState, sold: bool) -> None:
    """What the player's own trade does to the card's price: a sale lowers it by `trade_impact`, a
    purchase raises it by `buy_impact`. Both are 0 since v0.8.0, so this leaves the card as it is:
    the player is one collector among millions and does not move a price."""
    step = 1 - cfg.market.trade_impact if sold else 1 + cfg.market.buy_impact
    lo, hi = cfg.market.factor_range
    card.factor = max(lo, min(hi, card.factor * step))
