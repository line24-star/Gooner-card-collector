"""Economy simulator (v0.4.0; daily goals and mastery: v0.6.0): plays the economy forward, day by day, with simple player models.

    cd backend
    .venv\\Scripts\\python -m tools.simulate                          # one player: regular seller, 2,000 cards, 30 days
    .venv\\Scripts\\python -m tools.simulate --player heavy --policy fuser --media 200 --days 90
    .venv\\Scripts\\python -m tools.simulate --audit                  # the whole grid (sizes × players × horizons)
    .venv\\Scripts\\python -m tools.simulate --audit --set market.base_value=9 --set tagging.floor=2
    .venv\\Scripts\\python -m tools.simulate --report                 # the v0.9.0 table: packs a day by period, coins by source
    .venv\\Scripts\\python -m tools.simulate --loop                   # does one tag a card pay for the next pack?

Uses the live config/economy.toml and the pure rules in app/game/ (economy, packs, market, goals),
so a tuning change shows up here immediately. `--set section.key=value` tries a change without
editing the file. Everything is seeded: the same arguments always print the same numbers.

What is modelled
  - Cards: every card gets a print run and a popularity (market.roll_print_run / roll_popularity).
    A pack slot pulls a card in proportion to market.draw_weight (print_run ^ drop_exponent). Packs
    are rolled by `roll_fast`, which gives exactly the pulls of packs.roll_pack for the same random
    numbers (tests/test_simulate.py checks that) but in O(log n) per slot, so 20,000 cards stay fast.
  - Duplicates are kept as extra copies (duplicates.keep_copies). What happens to them is the policy:
      seller   presses "sell duplicates" (keeps 1 copy per card and treatment) at market sell price,
               with the wanted bonus when it applies (v0.8.0: only cards whose best copy is worth
               market.wanted_min_value and whose Normal price has reached the first fuse price step
               are wanted), and takes a collector's offer when it is at
               least the market price and a copy of the card stays in the collection
      hoarder  never sells, never fuses
      fuser    fuses fuse.copies copies into the next treatment whenever possible, never sells
  - The market: the index and every card's price level move each tick with the real rules
    (market.step_index / step_card / run_events, reprints and the runs after a hype or a buyout
    included); the player's own sales go through market.nudge, which leaves the price alone since
    v0.8.0 (trade_impact = 0). Every price comes from market.price, so a Foil, Holo or Special copy
    is worth the card's own premium (v0.8.0: market.premium, from its popularity and its id).
  - Tagging: `tags_per_card` tags on every pulled card, paid by economy.tag_reward (list, then the
    floor up to max_paid_tags). The daily tag rush starts with the first tag of the day and lasts
    tagging.rush_minutes of session time.
  - Reviews: a tagged card that gets a new best treatment (pack, fuse or upgrade) pays once.
  - Shop deal: each session falls into one deal window (services/offers.deal: one pack type,
    20-40% off, deal.per_window times). The player takes it whenever the coins are there.
  - The daily free pack (after the check-in, which pays nothing since v0.9.0), ingest rewards for
    `--new-media` new files a day, and the dry streak (it changes no odds while `pity.enabled` is off).
  - v0.9.0, the coin rule: coins come only from tags, reviews, ingest and market sales. Every other
    reward is sealed packs: completeness milestones, weekly and daily goals, mastery levels, the
    checklist steps and the tours. They are claimed the moment they are reached, go into the
    inventory and are opened at the start of the next loop of the session (30 s each, no coins, no
    price rise). `reward_packs` counts them, `--report` prints the v0.9.0 audit table.
  - v0.6.0: daily goals (picked each day, stable for the day, kinds that cannot move left out, like
    services/chase.py) and mastery levels (game/mastery.py).
    The player works on open goals where the policy alone would not: a seller with a fuse or upgrade
    goal fuses a card that has enough copies before selling the rest, and a player with a tag goal and
    nothing else to do adds tags to tagged cards (they pay the floor) until the goal is done.
  - Upgrade attempts: when no pack is affordable but an attempt is, and every second purchase once
    every card has been pulled. v0.7.1: only while `upgrades.enabled` is on (it is off in the live
    config, so no attempt is made; try them with --set upgrades.enabled=true).

Assumptions (not rules of the game; change them with the flags or in ARCHETYPES / Times)
  - The player shows up every day. casual: 10 min, 1 session, 2 tags a card. regular: 30 min,
    1 session, 3 tags. heavy: 120 min in 2 sessions, 5 tags. grinder: a heavy player who, with
    nothing else to do, keeps tagging tagged cards up to 12 tags each (for the floor coin).
  - Times: open a pack 30 s, tag a card 15 s + 8 s per tag, review 8 s, sell duplicates 20 s,
    fuse 8 s, upgrade attempt 12 s, answer an offer 10 s.
  - Order of play: reviews, deal packs, packs while coins last, fuse / sell, tag, upgrade attempts.
    When none of these is possible the session ends early ("idle" minutes).
  - Not modelled: buying listed cards on the market, at any treatment (so "buy" goals are never picked), category
    rewards and sets, sealed-pack hoarding, video ingest, missed days.
"""

import argparse
import math
import random
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.game import config as config_mod
from app.game import economy as eco
from app.game import goals as gl
from app.game import market as mk
from app.game import mastery as ms
from app.game.config import EconomyConfig, Pack, get_config
from app.game.packs import PoolItem, Pull, ends_dry_run, pull_value, roll_treatment

SOURCES = ("tag", "review", "sell", "offer", "ingest")  # v0.9.0: the only things that pay coins
REWARDS = ("milestone", "goal", "daily", "mastery", "onboarding")  # these pay sealed packs
POLICIES = ("seller", "hoarder", "fuser")
WEIGHT_SCALE = 1024  # pull weights are kept as whole numbers so the weight trees stay exact
DEFAULT_START = "2026-11-16"  # the market's cycles depend on the date: a fixed one keeps runs repeatable


@dataclass(frozen=True)
class Archetype:
    name: str
    minutes: float  # play time per day
    sessions: int  # visits per day (each has its own deal window and market tick)
    tags_per_card: int
    deep_tags: int = 0  # with nothing else to do, keeps adding tags to tagged cards up to this many (0 = doesn't)


ARCHETYPES = {
    "casual": Archetype("casual", 10, 1, 2),
    "regular": Archetype("regular", 30, 1, 3),
    "heavy": Archetype("heavy", 120, 2, 5),
    # the heavy player who, out of coins and cards, goes back and tags deeper for the 1-coin floor
    "grinder": Archetype("grinder", 120, 2, 5, deep_tags=12),
}


@dataclass(frozen=True)
class Times:
    """Seconds one action takes (assumptions)."""

    pack: float = 30
    card: float = 15
    tag: float = 8
    review: float = 8
    sell: float = 20
    fuse: float = 8
    upgrade: float = 12
    offer: float = 10


class WeightTree:
    """A Fenwick tree over whole-number weights: add, remove and pick by weight in O(log n)."""

    def __init__(self, size: int):
        self.n = size
        self.tree = [0] * (size + 1)
        self.total = 0
        self.count = 0
        self.top = 1 << max(0, size.bit_length() - 1)

    def add(self, i: int, w: int) -> None:
        self.total += w
        self.count += 1 if w > 0 else -1
        i += 1
        while i <= self.n:
            self.tree[i] += w
            i += i & -i

    def find(self, target: float) -> int:
        """The first index whose running total is above `target` (what random.choices picks)."""
        pos, step, tree, n = 0, self.top, self.tree, self.n
        while step:
            nxt = pos + step
            if nxt <= n and tree[nxt] <= target:
                pos = nxt
                target -= tree[nxt]
            step >>= 1
        return min(pos, n - 1)


class Card:
    __slots__ = ("owned", "best", "tagged", "tags", "weight")

    def __init__(self, tiers: int, weight: int):
        self.owned = [0] * tiers  # copies per treatment tier
        self.best = -1  # best tier owned, -1 = never pulled
        self.tagged = False
        self.tags = 0
        self.weight = weight


class Book:
    """The cards of one library with the two weight trees packs are rolled from."""

    def __init__(self, cfg: EconomyConfig, capacity: int):
        self.cfg = cfg
        self.tier_ids = [t.id for t in cfg.tiers()]
        self.cards: list[Card] = []
        self.states: list[mk.CardState] = []  # the same cards as the market sees them
        self.owned = WeightTree(capacity)
        self.unowned = WeightTree(capacity)

    def add(self, print_run: int, popularity: float = 1.0) -> int:
        i = len(self.cards)
        w = max(1, round(mk.draw_weight(self.cfg, print_run) * WEIGHT_SCALE))
        self.cards.append(Card(len(self.tier_ids), w))
        self.states.append(mk.CardState(i, print_run, popularity))
        self.unowned.add(i, w)
        return i

    def mark_owned(self, i: int) -> None:
        w = self.cards[i].weight
        self.unowned.add(i, -w)
        self.owned.add(i, w)

    def reweigh(self, i: int) -> None:
        """A reprint changed the print run: the card turns up in packs more often from now on."""
        c = self.cards[i]
        w = max(1, round(mk.draw_weight(self.cfg, self.states[i].print_run) * WEIGHT_SCALE))
        tree = self.owned if c.best >= 0 else self.unowned
        tree.add(i, -c.weight)
        tree.add(i, w)
        c.weight = w

    def pool_items(self) -> list[PoolItem]:
        """The same pool in the shape packs.roll_pack takes (for tests and cross-checks)."""
        return [
            PoolItem(i, c.tagged, c.tags, {self.tier_ids[t]: n for t, n in enumerate(c.owned) if n}, float(c.weight))
            for i, c in enumerate(self.cards)
        ]


def roll_fast(cfg: EconomyConfig, pack: Pack, book: Book, completeness: float, rng: random.Random,
              pity: float = 1.0) -> list[Pull]:
    """packs.roll_pack on a Book: the same rules and the same use of `rng`, without walking the pool.

    Only the keep_copies rules are implemented (a duplicate is another copy)."""
    if not book.cards:
        return []
    weights = eco.slot_weights(cfg, pack, completeness, pity)
    dup_p = eco.duplicate_chance(cfg, book.owned.count / len(book.cards))
    tier_of = {t.id: t.tier for t in cfg.tiers()}
    taken: list[tuple[WeightTree, int]] = []
    pulls: list[Pull] = []
    gained: dict[int, set[int]] = {}  # tiers this pack already added per card (a pack that repeats cards)
    for _ in range(pack.size):
        treatment, near = roll_treatment(weights, rng.random())
        want_owned = rng.random() < dup_p
        primary, fallback = (book.owned, book.unowned) if want_owned else (book.unowned, book.owned)
        tree = primary if primary.count else fallback
        if not tree.count:  # fewer cards than slots: every card was used once, they may come again
            for used_tree, j in taken:
                used_tree.add(j, book.cards[j].weight)
            taken.clear()
            tree = primary if primary.count else fallback
        i = tree.find(rng.random() * tree.total)
        tree.add(i, -book.cards[i].weight)  # a card appears once per pack
        taken.append((tree, i))
        tier = tier_of[treatment]
        held = {t for t, n in enumerate(book.cards[i].owned) if n} | gained.get(i, set())
        best = max(held, default=-1)
        gained.setdefault(i, set()).add(tier)
        outcome = "new" if best < 0 else "better" if tier > best else "duplicate" if tier in held else "version"
        pulls.append(Pull(i, treatment, outcome, near))
    for tree, i in taken:
        tree.add(i, book.cards[i].weight)
    pulls.sort(key=lambda p: tier_of[p.treatment])
    return pulls


def market_tick(cfg: EconomyConfig, book: Book, seed: int, tick: int, log_index: float) -> float:
    """market.advance without the price history: the same steps in the same order."""
    rng = mk.tick_rng(seed, tick)
    log_index = mk.step_index(cfg, tick, log_index, rng)
    for c in book.states:
        mk.step_card(cfg, c, rng)
    for news in mk.run_events(cfg, book.states, rng):
        if news.kind == "reprint":
            for i in news.media_ids:
                book.reweigh(i)
    return log_index


def deal_price(cfg: EconomyConfig, full: int, discount: float) -> int:
    """The deal price of a pack (the rounding of services/offers.deal)."""
    step = max(1, cfg.inflation.round_to)
    return max(step, int(round(full * (1 - discount) / step) * step))


@dataclass
class Day:
    day: int
    coins: int
    earned: dict[str, int]  # cumulative, by source
    spent_packs: int  # cumulative
    spent_upgrades: int  # cumulative
    packs: int  # cumulative packs opened (bought + free)
    cards: int  # library size
    pulled: float  # share of the library owned
    tagged: float
    completeness: float
    used_s: float  # play time used today
    idle_s: float  # play time left over because there was nothing to do
    spare_copies: int  # copies beyond one per card and treatment
    value: int  # market value of every copy owned
    index: float
    fused: int  # cumulative
    upgrades: int  # cumulative successful attempts
    pack_cost: int
    reward_packs: int = 0  # cumulative packs received as rewards (v0.9.0), the free daily pack not counted
    reward_by: dict = field(default_factory=dict)  # the same by source (REWARDS), cumulative
    reward_worth: int = 0  # what those packs cost at base prices, cumulative


@dataclass
class Result:
    cfg: EconomyConfig
    arch: Archetype
    policy: str
    media: int
    seed: int
    days: list[Day] = field(default_factory=list)
    first_pulled: dict[float, int] = field(default_factory=dict)  # 0.5 / 0.9 / 1.0 → day
    left: dict = field(default_factory=dict)  # what is left to do at the end of the last day
    extras: dict = field(default_factory=dict)  # counts over the whole run (deals, goals, offers, copies sold)


class Sim:
    def __init__(self, cfg: EconomyConfig, media: int, arch: Archetype, policy: str = "seller", seed: int = 1,
                 new_media: int = 2, days: int = 30, start: str = DEFAULT_START, times: Times = Times(),
                 pack: str = "standard"):
        if policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}")
        self.cfg, self.arch, self.policy, self.seed, self.times = cfg, arch, policy, seed, times
        self.new_media = new_media
        self.pack = cfg.packs[pack]
        self.tiers = cfg.tiers()
        self.top = len(self.tiers) - 1
        self.rng = random.Random(seed)  # packs and upgrade attempts
        self.issue_rng = random.Random(seed * 7919 + 1)  # print runs: the same library for every player type
        self.misc_rng = random.Random(seed * 7919 + 2)  # deals, offers, wanted cards
        self.book = Book(cfg, media + new_media * days + 1)
        for _ in range(media):
            self._issue()
        # The market, warmed up like services/market.catch_up does for a new one.
        epoch = datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp()
        self.tick = mk.tick_of(cfg, epoch) - cfg.market.warmup_ticks - 1
        self.log_index = mk.index_target(cfg, self.tick)
        for _ in range(cfg.market.warmup_ticks + 1):
            self._tick()
        self.ticks_per_day = max(1, round(24 / cfg.market.tick_hours))

        self.coins = cfg.starting_coins
        self.earned = dict.fromkeys(SOURCES, 0)
        self.spent_packs = self.spent_upgrades = 0
        self.bought = self.free = 0  # packs
        self.inv: dict[str, int] = dict.fromkeys(cfg.packs, 0)  # sealed reward packs waiting to be opened
        self.reward_by = dict.fromkeys(REWARDS, 0)
        self.reward_worth = 0
        self.dry = 0  # packs in a row without a pity treatment
        self.streak = 0
        self.fused = self.upgrades = 0
        self.week = gl.Counters()
        self.today = gl.Counters()
        self.week_no = self.day_no = -1
        self.week_picks: list | None = None  # this week's goals, fixed once picked (None = not unlocked yet)
        self.day_picks: list | None = None
        self.mastery_paid = 0  # every mastery level up to this one has paid
        self.claimed_goals: set[str] = set()
        self.claimed_milestones: set[float] = set()
        self.claimed_steps: set[str] = set()
        self.comp_sum = 0.0
        self.slots = 0  # (card, treatment above Normal) pairs held now
        self.slots_mode = cfg.completeness.mode == "slots"
        self.n_tagged = 0
        self.untagged: list[list[int]] = [[] for _ in self.tiers]  # owned, untagged cards by best tier
        self.shallow: list[int] = []  # tagged cards that could take more paid tags (for deep_tags players)
        self.payable: list[int] = []  # tagged cards whose next tag still pays (for tag goals)
        self.reviews: dict[int, int] = {}  # card → tier waiting for a review
        self.reviewed: set[tuple[int, int]] = set()
        self.spare: set[int] = set()  # cards with something to sell or fuse (by policy)
        self.by_best: list[set[int]] = [set() for _ in self.tiers]
        self.owned_ids: list[int] = []
        self.wanted: set[int] = set()
        self.flip = False  # endgame: alternate packs and upgrade attempts
        self.extras = dict.fromkeys(("deal_packs", "deal_saved", "goals_offered", "goals_claimed", "offers_taken",
                                     "copies_sold", "wanted_sold", "attempts", "daily_offered", "daily_claimed",
                                     "goal_fuses", "goal_tags"), 0)
        self.result = Result(cfg, arch, policy, media, seed)

    # ------------------------------------------------------------ small helpers

    def _issue(self) -> int:
        return self.book.add(mk.roll_print_run(self.cfg, self.issue_rng), mk.roll_popularity(self.cfg, self.issue_rng))

    def _tick(self) -> None:
        self.tick += 1
        self.log_index = market_tick(self.cfg, self.book, self.seed, self.tick, self.log_index)
        self.index = math.exp(self.log_index)

    def _pay(self, source: str, amount: int) -> None:
        self.coins += amount
        self.earned[source] += amount

    def _grant(self, source: str, packs: list[dict]) -> None:
        """A reward: sealed packs into the inventory (services/rewards.grant_packs)."""
        for p in packs:
            self.inv[p["pack"]] += p["count"]
            self.reward_by[source] += p["count"]
        self.reward_worth += eco.packs_worth(self.cfg, packs)

    def open_reward_pack(self) -> bool:
        """Open one sealed reward pack, the dearest kind first. False when the inventory is empty."""
        for pid in sorted(self.inv, key=lambda p: -self.cfg.packs[p].base_cost):
            if self.inv[pid] > 0:
                self.inv[pid] -= 1
                self.open_pack(self.cfg.packs[pid], 0, free=True)
                return True
        return False

    @property
    def completeness(self) -> float:
        if self.slots_mode:
            return eco.slots_completeness(self.slots, eco.slots_total(self.cfg, len(self.book.cards)))
        return self.comp_sum / len(self.book.cards) if self.book.cards else 0.0

    def _need(self, i: int) -> int:
        """Copies one fuse of this card takes at today's price."""
        return eco.fuse_need(self.cfg, self.price(i, 0))

    def _drop(self, i: int, tier: int, n: int = 1) -> None:
        """Copies leave the collection; the last one of a treatment above Normal empties its slot."""
        owned = self.book.cards[i].owned
        owned[tier] -= n
        if tier > 0 and owned[tier] == 0:
            self.slots -= 1

    def _score(self, c: Card) -> float:
        return eco.card_completeness(self.cfg, c.tagged, self.tiers[c.best].id if c.best >= 0 else None)

    def price(self, i: int, tier: int) -> int:
        return mk.price(self.cfg, self.book.states[i], self.index, self.tiers[tier].id)

    def pack_cost(self, pack: Pack | None = None) -> int:
        return eco.pack_cost(self.cfg, pack or self.pack, self.bought, self.completeness)

    def _spare_check(self, i: int) -> None:
        c = self.book.cards[i]
        if self.policy == "seller":
            has = any(n > 1 for n in c.owned)
        elif self.policy == "fuser":
            has = any(n >= self._need(i) for n in c.owned[: self.top])
        else:
            has = False
        (self.spare.add if has else self.spare.discard)(i)

    def _add_copy(self, i: int, tier: int) -> None:
        c = self.book.cards[i]
        c.owned[tier] += 1
        if tier > 0 and c.owned[tier] == 1:
            self.slots += 1
        if c.best < 0:
            self.book.mark_owned(i)
            self.owned_ids.append(i)
        if tier > c.best:
            before = self._score(c)
            if c.best >= 0:
                self.by_best[c.best].discard(i)
            c.best = tier
            self.by_best[tier].add(i)
            self.comp_sum += self._score(c) - before
            if c.tagged:
                if (i, tier) not in self.reviewed:
                    self.reviews[i] = tier
            else:
                self.untagged[tier].append(i)
        self._spare_check(i)

    def _count(self, what: str, n: int = 1) -> None:
        """Something the goals count happened: it counts for the week and for the day."""
        for c in (self.week, self.today):
            setattr(c, what, getattr(c, what) + n)

    def _next_tag_pays(self, i: int) -> bool:
        c = self.book.cards[i]
        return eco.tag_reward(self.cfg, c.tags, self.tiers[c.best].multiplier) > 0

    def _blocked(self) -> set[str]:
        """Goal kinds that cannot move right now (services/chase.blocked_kinds). Buying isn't modelled."""
        blocked = {"buy"}
        if len(self.by_best[self.top]) == len(self.owned_ids) and not self.reviews:
            blocked.add("review")  # "upgrade" counts fuses, which only need copies: never blocked
        while self.payable and not self._next_tag_pays(self.payable[-1]):
            self.payable.pop()
        if not self.payable and len(self.owned_ids) == self.n_tagged:
            blocked.add("tag")
        return blocked

    def _pick_goals(self) -> None:
        """Pick the goals of the running week and day once they are unlocked; they stay until the period ends."""
        cfg = self.cfg
        if self.week_picks is None and self.bought >= cfg.goals.unlock_after_packs:
            self.week_picks = gl.pick_goals(cfg, f"sim-W{self.week_no}", 1, period="week", blocked=self._blocked())
            for g in self.week_picks:
                self.extras["goals_offered"] += 1
                self.extras[f"offered:{g.id}"] = self.extras.get(f"offered:{g.id}", 0) + 1
        if self.day_picks is None and self.bought >= cfg.goals.daily_unlock_after_packs:
            self.day_picks = gl.pick_goals(cfg, f"sim-D{self.day_no}", 1, period="day", blocked=self._blocked())
            for g in self.day_picks:
                self.extras["daily_offered"] += 1
                self.extras[f"offered:{g.id}"] = self.extras.get(f"offered:{g.id}", 0) + 1

    def _open_goals(self):
        """(goal, its counters, its claim key) for every picked goal that hasn't been claimed."""
        for picks, counters, scope in ((self.day_picks, self.today, f"D{self.day_no}"),
                                       (self.week_picks, self.week, f"W{self.week_no}")):
            for g in picks or ():
                key = f"{scope}:{g.id}"
                if key not in self.claimed_goals:
                    yield g, counters, key

    def _goal_wants(self, *kinds: str) -> bool:
        return any(g.kind in kinds for g, _, _ in self._open_goals())

    def mastery_points(self) -> int:
        cards = {self.tiers[t].id: len(s) for t, s in enumerate(self.by_best)}
        return ms.points(self.cfg, cards, self.fused)

    def _after(self) -> None:
        """Rewards that follow from what just happened: milestones, weekly and daily goals, mastery
        levels, onboarding steps."""
        cfg = self.cfg
        comp = self.completeness
        for m in cfg.milestones:
            if m.at <= comp + 1e-12 and m.at not in self.claimed_milestones:
                self.claimed_milestones.add(m.at)
                self._grant("milestone", eco.pack_list(cfg, [m.reward]))
        self._pick_goals()
        for g, counters, key in list(self._open_goals()):
            if gl.goal_progress(cfg, g, counters) >= g.target:
                self.claimed_goals.add(key)
                daily = g.period == "day"
                self.extras["daily_claimed" if daily else "goals_claimed"] += 1
                self.extras[f"claimed:{g.id}"] = self.extras.get(f"claimed:{g.id}", 0) + 1
                self._grant("daily" if daily else "goal", eco.pack_list(cfg, [g.reward]))
        level = ms.level_for(cfg, self.mastery_points())
        while self.mastery_paid < level:
            self.mastery_paid += 1
            self._grant("mastery", ms.level_packs(cfg, [self.mastery_paid]))
        if len(self.claimed_steps) < len(cfg.onboarding.steps):
            have = {"media": len(self.book.cards), "packs": self.bought + self.free, "tagged": self.n_tagged,
                    "tagged_owned": self.n_tagged, "upgrades": 1 if self.spent_upgrades or self.fused else 0}
            for s in cfg.onboarding.steps:
                if s.id not in self.claimed_steps and have[s.kind] >= s.target:
                    self.claimed_steps.add(s.id)
                    self._grant("onboarding", eco.pack_list(cfg, [s.reward]))

    # ------------------------------------------------------------ actions

    def open_pack(self, pack: Pack, cost: int, free: bool = False) -> None:
        cfg = self.cfg
        self.coins -= cost
        self.spent_packs += cost
        pulls = roll_fast(cfg, pack, self.book, self.completeness, self.rng, eco.pity_multiplier(cfg, self.dry))
        self.dry = 0 if ends_dry_run(cfg, pulls) else self.dry + 1
        if free:
            self.free += 1  # a free pack doesn't raise prices
        else:
            self.bought += 1
        self._count("opens")
        for p in pulls:
            tier = cfg.treatments[p.treatment].tier
            self.week.add_pull(tier)
            self.today.add_pull(tier)
            self._add_copy(p.media_id, tier)
        self._after()

    def tag_card(self, rush: bool) -> bool:
        """Tag the shiniest untagged card the player owns. False when there is none."""
        cfg = self.cfg
        for tier in range(self.top, -1, -1):
            stack = self.untagged[tier]
            while stack:
                i = stack.pop()
                c = self.book.cards[i]
                if c.tagged or c.best != tier:
                    continue  # tagged meanwhile, or listed again under its better treatment
                mult = self.tiers[tier].multiplier
                n = self.arch.tags_per_card
                coins = sum(eco.tag_reward(cfg, k, mult, True, rush) for k in range(n))
                before = self._score(c)
                c.tagged, c.tags = True, n
                self.n_tagged += 1
                self.comp_sum += self._score(c) - before
                self._pay("tag", coins)
                self._count("tags", min(n, cfg.tagging.max_paid_tags))
                if n < self.arch.deep_tags:
                    self.shallow.append(i)
                if n < cfg.tagging.max_paid_tags:
                    self.payable.append(i)
                self._after()
                return True
        return False

    def deepen(self, rush: bool) -> bool:
        """One more tag on a card that is already tagged (deep_tags players). False when none can take one."""
        limit = min(self.arch.deep_tags, self.cfg.tagging.max_paid_tags)
        while self.shallow:
            i = self.shallow[-1]
            c = self.book.cards[i]
            if c.tags >= limit:
                self.shallow.pop()
                continue
            self._pay("tag", eco.tag_reward(self.cfg, c.tags, self.tiers[c.best].multiplier, True, rush))
            c.tags += 1
            self._count("tags")
            self._after()
            return True
        return False

    def tag_for_goal(self, rush: bool) -> bool:
        """One more paid tag on a tagged card, because a tag goal is open. False when no tag pays any more."""
        while self.payable:
            i = self.payable[-1]
            if not self._next_tag_pays(i):
                self.payable.pop()
                continue
            c = self.book.cards[i]
            self._pay("tag", eco.tag_reward(self.cfg, c.tags, self.tiers[c.best].multiplier, True, rush))
            c.tags += 1
            self._count("tags")
            self.extras["goal_tags"] += 1
            self._after()
            return True
        return False

    def review(self) -> None:
        i, tier = self.reviews.popitem()
        if self.book.cards[i].best != tier or (i, tier) in self.reviewed:
            return
        self.reviewed.add((i, tier))
        self._pay("review", eco.review_reward(self.cfg, self.tiers[tier].multiplier))
        self._count("reviews")
        self._after()

    def sell_copy(self, i: int, tier: int, source: str = "sell", coins: int | None = None) -> int:
        if coins is None:
            coins = mk.sell_price(self.cfg, self.price(i, tier), i in self.wanted)
            self.extras["wanted_sold"] += i in self.wanted
        self.extras["copies_sold" if source == "sell" else "offers_taken"] += 1
        self._drop(i, tier)
        mk.nudge(self.cfg, self.book.states[i], sold=True)
        self._pay(source, coins)
        self._count("sells")
        self._count("market_coins", coins)
        if source == "sell":
            self._after()
        return coins

    def sell_duplicates(self) -> None:
        """The "sell duplicates" button: every copy beyond one per card and treatment."""
        for i in sorted(self.spare):
            owned = self.book.cards[i].owned
            for tier in range(len(owned)):
                while owned[tier] > 1:
                    self.sell_copy(i, tier)
        self.spare.clear()

    def fuse_one(self, i: int | None = None) -> None:
        i = min(self.spare) if i is None else i
        c = self.book.cards[i]
        need = self._need(i)
        tier = next((t for t in range(self.top) if c.owned[t] >= need), None)
        if tier is None:  # the price climbed past a step since the copies were counted
            self.spare.discard(i)
            return
        self._drop(i, tier, need)
        self.fused += 1
        self._count("fuses")
        self._count("upgrades")  # a fuse counts as an upgrade too (services/chase.counters)
        self._add_copy(i, tier + 1)
        self._after()

    def fuse_for_goal(self) -> bool:
        """A seller with a fuse (or upgrade) goal fuses one card that has the copies. False when none has."""
        i = next((i for i in sorted(self.spare)
                  if any(n >= self._need(i) for n in self.book.cards[i].owned[: self.top])), None)
        if i is None:
            return False
        self.extras["goal_fuses"] += 1
        self.fuse_one(i)
        return True

    def upgrade_cost(self) -> tuple[int, int] | None:
        """(card, cost) of the cheapest upgrade attempt the player could make, coins aside."""
        for tier in range(self.top):
            if self.by_best[tier]:
                i = min(self.by_best[tier])
                cost = eco.upgrade_cost(self.cfg, self.tiers[tier].id, self._need(i))
                if cost is not None:
                    return i, cost
        return None

    def try_upgrade(self) -> bool:
        hit = self.upgrade_cost()
        if hit is None or hit[1] > self.coins:
            return False
        i, cost = hit
        self.coins -= cost
        self.spent_upgrades += cost
        self.extras["attempts"] += 1
        if self.rng.random() < self.cfg.upgrades.chance:
            c = self.book.cards[i]
            self._drop(i, c.best)
            self.upgrades += 1
            self._count("upgrades")
            self._add_copy(i, c.best + 1)
        self._after()
        return True

    def answer_offer(self) -> bool:
        """A collector's offer for this tick. Taken when it beats the market price and a copy stays."""
        if self.policy != "seller" or not self.owned_ids:
            return False
        cards = self.book.cards
        holdings = [(i, self.tiers[t].id, self.price(i, t))
                    for i in self.owned_ids for t, n in enumerate(cards[i].owned) if n]
        hit = mk.roll_offer(self.cfg, holdings, self.misc_rng, self.wanted)
        if hit is None:
            return False
        i, treatment, coins = hit
        tier = self.cfg.treatments[treatment].tier
        if sum(cards[i].owned) < 2 or coins < self.price(i, tier):
            return False
        self.sell_copy(i, tier, "offer", coins)
        c = cards[i]
        if c.owned[c.best] == 0:  # the best copy went: the card shows its next best one
            before = self._score(c)
            self.by_best[c.best].discard(i)
            c.best = max(t for t, n in enumerate(c.owned) if n)
            self.by_best[c.best].add(i)
            self.comp_sum += self._score(c) - before
        return True

    # ------------------------------------------------------------ a day

    def session(self, budget: float, first: bool) -> tuple[float, float]:
        """One visit. Returns (seconds used, seconds left over with nothing to do)."""
        cfg, tm = self.cfg, self.times
        left = budget
        if first and cfg.free_pack.pack and self.streak >= cfg.free_pack.min_streak:
            self.open_pack(cfg.packs[cfg.free_pack.pack], 0, free=True)
            left -= tm.pack
        # This visit's deal window: one pack type, cheaper, a few times.
        deal_pack = deal_left = None
        if cfg.deal.hours > 0:
            deal_pack = cfg.packs[self.misc_rng.choice(sorted(cfg.packs))]
            discount = round(self.misc_rng.uniform(*cfg.deal.discount), 2)
            deal_left = cfg.deal.per_window
        if self.policy == "seller":
            cards = self.book.cards
            held = {i: max(self.price(i, t) for t, n in enumerate(cards[i].owned) if n)
                    for i in self.owned_ids if any(cards[i].owned)}
            normal = {i: self.price(i, 0) for i in held}
            self.wanted = set(mk.pick_wanted(cfg, held, self.misc_rng, normal))
            if self.answer_offer():
                left -= tm.offer
        rush_until = None  # session seconds left when the rush ends
        idle = 0.0
        while left > 0:
            if self.reviews:
                self.review()
                left -= tm.review
                continue
            if self.open_reward_pack():  # sealed packs from rewards: opened before anything is bought
                left -= tm.pack
                continue
            if deal_left:
                price = deal_price(cfg, self.pack_cost(deal_pack), discount)
                if self.coins >= price:
                    self.extras["deal_packs"] += 1
                    self.extras["deal_saved"] += self.pack_cost(deal_pack) - price
                    self.open_pack(deal_pack, price)
                    deal_left -= 1
                    left -= tm.pack
                    continue
            cost = self.pack_cost()
            endgame = self.book.unowned.count == 0
            if endgame and self.flip and self.try_upgrade():
                self.flip = False
                left -= tm.upgrade
                continue
            if self.coins >= cost:
                self.open_pack(self.pack, cost)
                self.flip = True
                left -= tm.pack
                continue
            if self.spare:
                if self.policy == "fuser":
                    self.fuse_one()
                    left -= tm.fuse
                elif self.policy == "seller" and self._goal_wants("fuse", "upgrade") and self.fuse_for_goal():
                    left -= tm.fuse
                else:
                    self.sell_duplicates()
                    left -= tm.sell
                continue
            if first and rush_until is None and cfg.tagging.rush_minutes > 0 and self._has_untagged():
                rush_until = left - cfg.tagging.rush_minutes * 60  # the first tag of the day starts the rush
            if self.tag_card(rush=rush_until is not None and left > rush_until):
                left -= tm.card + tm.tag * self.arch.tags_per_card
                continue
            if self._goal_wants("tag") and self.tag_for_goal(rush=rush_until is not None and left > rush_until):
                left -= tm.tag
                continue
            if self.try_upgrade():
                left -= tm.upgrade
                continue
            if self.arch.deep_tags and self.deepen(rush=rush_until is not None and left > rush_until):
                left -= tm.tag
                continue
            idle = left
            break
        return budget - (idle or max(0.0, left)), idle

    def _has_untagged(self) -> bool:
        cards = self.book.cards
        return any(not cards[i].tagged and cards[i].best == t for t, s in enumerate(self.untagged) for i in s)

    def day(self, d: int) -> None:
        cfg = self.cfg
        week = (d - 1) // 7
        if week != self.week_no:
            self.week_no, self.week, self.week_picks = week, gl.Counters(), None
        self.day_no, self.today, self.day_picks = d, gl.Counters(), None
        self._pick_goals()
        if d > 1:  # new photos keep arriving
            paid = 0
            for n in range(self.new_media):
                self._issue()
                r = eco.ingest_reward(cfg, "image", initial_scan=False, paid_today=paid, files_today=n)
                paid += r
                self._pay("ingest", r)
        self.streak += 1  # the check-in: no coins since v0.9.0, it unlocks the free pack
        if d == 1:  # the tours are done on the first day: their packs come once
            self._grant("onboarding", eco.pack_list(cfg, [cfg.onboarding.tours_reward]))
        used = idle = 0.0
        visits = max(1, self.arch.sessions)
        for s in range(visits):
            for _ in range(max(1, self.ticks_per_day // visits)):
                self._tick()
            u, i = self.session(self.arch.minutes * 60 / visits, first=s == 0)
            used += u
            idle += i
        self._record(d, used, idle)

    def _record(self, d: int, used: float, idle: float) -> None:
        cards = self.book.cards
        n = len(cards)
        pulled = self.book.owned.count / n
        for at in (0.5, 0.9, 1.0):
            if at not in self.result.first_pulled and pulled >= at - 1e-9:
                self.result.first_pulled[at] = d
        self.result.days.append(Day(
            day=d, coins=self.coins, earned=dict(self.earned), spent_packs=self.spent_packs,
            spent_upgrades=self.spent_upgrades, packs=self.bought + self.free, cards=n, pulled=pulled,
            tagged=self.n_tagged / n, completeness=self.completeness, used_s=used, idle_s=idle,
            spare_copies=sum(k - 1 for i in self.owned_ids for k in cards[i].owned if k > 1),
            value=sum(self.price(i, t) * k for i in self.owned_ids for t, k in enumerate(cards[i].owned) if k),
            index=self.index, fused=self.fused, upgrades=self.upgrades, pack_cost=self.pack_cost(),
            reward_packs=sum(self.reward_by.values()), reward_by=dict(self.reward_by), reward_worth=self.reward_worth,
        ))

    def what_is_left(self) -> dict:
        """What the player could still do right now (asked at the end of a run)."""
        cfg, cards = self.cfg, self.book.cards
        t = cfg.tagging
        more = max(0, t.max_paid_tags - self.arch.tags_per_card)
        paid_list = 1 + len(t.extra_tags)
        tagged = [cards[i] for i in self.owned_ids if cards[i].tagged]
        deep = sum(eco.tag_reward(cfg, k, self.tiers[c.best].multiplier)
                   for c in tagged for k in range(c.tags, min(t.max_paid_tags, max(c.tags, paid_list))))
        floor_tags = sum(max(0, t.max_paid_tags - max(c.tags, paid_list)) for c in tagged)
        spare_value = sum(mk.sell_price(cfg, self.price(i, tier)) * (k - 1)
                          for i in self.owned_ids for tier, k in enumerate(cards[i].owned) if k > 1)
        up = self.upgrade_cost()
        return {
            "coins": self.coins,
            "pack_cost": self.pack_cost(),
            "unpulled": self.book.unowned.count,
            "untagged_owned": len(self.owned_ids) - self.n_tagged,
            "more_tags_per_card": more,
            "list_tag_coins": deep,  # coins still to earn from tags on the paid list (2nd-4th tag)
            "floor_tags": floor_tags,  # tags that pay the floor
            "floor_tag_coins": floor_tags * t.floor,
            "spare_copies": sum(k - 1 for i in self.owned_ids for k in cards[i].owned if k > 1),
            "spare_sell_value": spare_value,
            "fusable": sum(k // self._need(i) for i in self.owned_ids for k in cards[i].owned[: self.top]),
            "upgrade_cost": up[1] if up else None,
            "by_best": {self.tiers[t].id: len(s) for t, s in enumerate(self.by_best)},
            "mastery_points": self.mastery_points(),
            "mastery_level": ms.level_for(cfg, self.mastery_points()),
        }

    def rtp(self, trials: int = 200) -> float:
        """Expected return of a Standard pack right now (the definition of packs.estimate_rtp)."""
        rng = random.Random(self.seed * 31 + len(self.result.days))
        total = 0.0
        for _ in range(trials):
            for p in roll_fast(self.cfg, self.pack, self.book, self.completeness, rng):
                state = self.book.states[p.media_id]
                item = PoolItem(p.media_id, self.book.cards[p.media_id].tagged, value=float(self.price(p.media_id, 0)),
                                premium=mk.premium(self.cfg, state.popularity, state.media_id))
                total += pull_value(self.cfg, p, item)
        return total / trials / self.pack_cost()

    def run(self, days: int) -> Result:
        for d in range(len(self.result.days) + 1, days + 1):
            self.day(d)
        self.result.left = self.what_is_left()
        self.result.extras = dict(self.extras)
        return self.result


def simulate(cfg: EconomyConfig, media: int, player: str = "regular", policy: str = "seller", days: int = 30,
             seed: int = 1, new_media: int = 2, start: str = DEFAULT_START) -> Result:
    arch = ARCHETYPES[player] if isinstance(player, str) else player
    return Sim(cfg, media, arch, policy, seed, new_media, days, start).run(days)


# ---------------------------------------------------------------- summaries


def summarize(res: Result, horizon: int) -> dict:
    """Averages over days 1..horizon, and the state at the end of that day."""
    days = res.days[:horizon]
    last, n = days[-1], len(days)
    earned = last.earned
    total = sum(earned.values())
    tail = days[-7:]  # the most recent week: the trend, without the welcome coins
    before = days[-len(tail) - 1] if len(days) > len(tail) else None
    tail_earned = total - (sum(before.earned.values()) if before else 0)
    tail_spent = (last.spent_packs + last.spent_upgrades) - ((before.spent_packs + before.spent_upgrades) if before else 0)
    return {
        "days": n,
        "per_day": {k: v / n for k, v in earned.items()},
        "income_per_day": total / n,
        "market_share": (earned["sell"] + earned["offer"]) / total if total else 0.0,
        "reward_packs_per_day": last.reward_packs / n,  # v0.9.0: rewards are packs
        "reward_by_per_day": {k: v / n for k, v in last.reward_by.items()},
        "packs_per_day_last_week": (last.packs - (before.packs if before else 0)) / len(tail),
        "packs_per_day": last.packs / n,
        "coins": last.coins,
        "net_per_day_last_week": (tail_earned - tail_spent) / len(tail),
        "income_per_day_last_week": tail_earned / len(tail),
        "pulled": last.pulled,
        "tagged": last.tagged,
        "completeness": last.completeness,
        "used_min": sum(d.used_s for d in days) / n / 60,
        "idle_min": sum(d.idle_s for d in days) / n / 60,
        "used_min_last_week": sum(d.used_s for d in tail) / len(tail) / 60,
        "dry_days": sum(1 for d in days if d.idle_s > 0) / n,
        "spare_copies": last.spare_copies,
        "value": last.value,
        "pack_cost": last.pack_cost,
        "fused": last.fused,
        "upgrades": last.upgrades,
        "spent_upgrades": last.spent_upgrades,
    }


def _day(res: Result, at: float) -> str:
    d = res.first_pulled.get(at)
    return f"{d}" if d else f">{len(res.days)}"


def print_run(res: Result) -> None:
    a = res.arch
    print(f"\n{a.name} {res.policy} · {res.media} cards · {a.minutes:g} min/day · {a.tags_per_card} tags/card · seed {res.seed}\n")
    print(f"{'day':>4} {'coins':>7} {'earned':>7} {'tag':>6} {'review':>6} {'market':>6} {'rw.pk':>6} {'packs':>5} "
          f"{'cost':>5} {'pulled':>6} {'tagged':>6} {'compl':>6} {'used':>5} {'idle':>5} {'value':>7}")
    prev = None
    for d in res.days:
        e = {k: d.earned[k] - (prev.earned[k] if prev else 0) for k in SOURCES}
        market = e["sell"] + e["offer"]
        other = d.reward_packs - (prev.reward_packs if prev else 0)  # packs received as rewards
        print(f"{d.day:>4} {d.coins:>7} {sum(e.values()):>7} {e['tag']:>6} {e['review']:>6} {market:>6} {other:>6} "
              f"{d.packs - (prev.packs if prev else 0):>5} {d.pack_cost:>5} {d.pulled:>6.0%} {d.tagged:>6.0%} "
              f"{d.completeness:>6.1%} {d.used_s / 60:>5.1f} {d.idle_s / 60:>5.1f} {d.value:>7}")
        prev = d
    print(f"\nPulled 50% / 90% / 100% of the library on day {_day(res, 0.5)} / {_day(res, 0.9)} / {_day(res, 1.0)}")
    left = res.left
    print(f"Left to do at the end: {left}")


def print_audit(cfg: EconomyConfig, args) -> None:
    horizons = sorted(h for h in args.horizons if h <= args.days) or [args.days]
    print(f"\nEconomy audit · seed {args.seed} · {args.new_media} new files a day · market starts {args.start}")
    print("Per day, averaged over days 1..H (seller policy). Coins come from tags, reviews, the market and 'other' = ingest.")
    print("rw/d = packs a day received as rewards (milestones, goals, mastery, checklist), rw% = their share of all packs opened.")
    print("net/d, act and p/d7 = last 7 days (coins earned minus spent per day; minutes actually played; packs a day).\n")
    head = (f"{'cards':>6} {'player':>8} {'H':>3} | {'tag':>5} {'review':>6} {'market':>6} {'other':>6} {'rw/d':>5} {'income':>6} {'mkt%':>5} {'rw%':>5} | "
            f"{'packs':>5} {'p/d7':>5} {'coins':>7} {'net/d':>6} | {'pulled':>6} {'tagged':>6} {'compl':>6} | {'act':>5} {'idle':>5} {'dry%':>5} {'RTP':>5}")
    runs: dict[tuple[int, str, str], Result] = {}
    print(head)
    for media in args.sizes:
        for player in args.players:
            sim = Sim(cfg, media, ARCHETYPES[player], "seller", args.seed, args.new_media, args.days, args.start)
            for h in horizons:
                res = sim.run(h)
                s = summarize(res, h)
                p = s["per_day"]
                market = p["sell"] + p["offer"]
                chase = s["reward_packs_per_day"]
                other = s["income_per_day"] - p["tag"] - p["review"] - market
                share = chase / s["packs_per_day"] if s["packs_per_day"] else 0.0
                print(f"{media:>6} {player:>8} {h:>3} | {p['tag']:>5.0f} {p['review']:>6.0f} {market:>6.0f} {other:>6.0f} {chase:>5.1f} "
                      f"{s['income_per_day']:>6.0f} {s['market_share']:>5.0%} {share:>5.0%} | {s['packs_per_day']:>5.1f} "
                      f"{s['packs_per_day_last_week']:>5.1f} {s['coins']:>7} "
                      f"{s['net_per_day_last_week']:>6.0f} | {s['pulled']:>6.0%} {s['tagged']:>6.0%} {s['completeness']:>6.1%} | "
                      f"{s['used_min_last_week']:>5.1f} {s['idle_min']:>5.1f} {s['dry_days']:>5.0%} {sim.rtp():>5.0%}")
            runs[(media, player, "seller")] = sim.result
        print()

    print("Days until 50% / 90% / 100% of the library is pulled, and what is left at the end (seller):")
    for (media, player, _), res in runs.items():
        left = res.left
        print(f"  {media:>6} {player:>8}: {_day(res, 0.5):>4} / {_day(res, 0.9):>4} / {_day(res, 1.0):>4}   "
              f"coins {left['coins']} (pack {left['pack_cost']}), unpulled {left['unpulled']}, untagged {left['untagged_owned']}, "
              f"list-tag coins {left['list_tag_coins']}, floor tags {left['floor_tags']}, best {left['by_best']}")

    print(f"\nThe chase after {args.days} days (seller): goals claimed / offered, reward packs per day over the whole run, mastery:")
    for (media, player, _), res in runs.items():
        x, e, n = res.extras, res.days[-1].reward_by, len(res.days)
        print(f"  {media:>6} {player:>8}: weekly {x['goals_claimed']:>3}/{x['goals_offered']:<3} ({e['goal'] / n:>4.2f}/day)   "
              f"daily {x['daily_claimed']:>3}/{x['daily_offered']:<3} ({e['daily'] / n:>4.2f}/day)   "
              f"mastery level {res.left['mastery_level']:>3}, {res.left['mastery_points']:>5} points ({e['mastery'] / n:>4.2f}/day)   "
              f"milestones {e['milestone'] / n:>4.2f}/day   for a goal: {x['goal_fuses']} fuses, {x['goal_tags']} tags")

    print(f"\nPolicies after {args.days} days (per day over the whole run):")
    print(f"{'cards':>6} {'player':>8} {'policy':>8} | {'income':>6} {'market':>6} {'review':>6} {'packs':>5} {'coins':>7} | "
          f"{'pulled':>6} {'compl':>6} {'spare':>6} {'value':>8} {'fused':>5} {'idle':>5} | 100% on day")
    for media in args.sizes:
        for player in args.players:
            for policy in POLICIES:
                res = runs.get((media, player, policy)) or simulate(
                    cfg, media, player, policy, args.days, args.seed, args.new_media, args.start)
                s = summarize(res, args.days)
                p = s["per_day"]
                print(f"{media:>6} {player:>8} {policy:>8} | {s['income_per_day']:>6.0f} {p['sell'] + p['offer']:>6.0f} "
                      f"{p['review']:>6.0f} {s['packs_per_day']:>5.1f} {s['coins']:>7} | {s['pulled']:>6.0%} "
                      f"{s['completeness']:>6.1%} {s['spare_copies']:>6} {s['value']:>8} {s['fused']:>5} {s['idle_min']:>5.1f} | "
                      f"{_day(res, 1.0)}")
        print()

    print("Marathon: a first day with no time limit (minutes played until nothing is left to do):")
    for media in args.sizes:
        for tags in sorted({ARCHETYPES[p].tags_per_card for p in args.players}):
            arch = Archetype("marathon", 24 * 60, 1, tags)
            res = Sim(cfg, media, arch, "seller", args.seed, 0, 1, args.start).run(1)
            d = res.days[0]
            print(f"  {media:>6} cards, {tags} tags/card: {d.used_s / 60:>6.1f} min, {d.packs} packs, "
                  f"{d.pulled:.0%} pulled, ends with {d.coins} coins (pack {d.pack_cost})")


# ---------------------------------------------------------------- the v0.9.0 report

REPORT_WINDOWS = ((1, 7), (8, 30), (31, 90), (91, 180))


def print_report(cfg: EconomyConfig, args) -> None:
    """Packs a day by period, coins a day by source, reward packs a day, the day the library is
    fully pulled and completeness at the end: seller and fuser, averaged over seeds 1..N."""
    days = args.days
    print(f"\nEconomy report · {days} days · seeds 1..{args.seeds} · {args.new_media} new files a day")
    print("packs/day by period (reward and free packs included) | coins/day by source over the run | rw = reward packs/day |"
          " full = day the library is fully pulled | compl = completeness at the end | idle = days with time left over")
    print(f"{'cards':>6} {'player':>8} {'policy':>7} | {'d1-7':>5} {'8-30':>5} {'31-90':>5} {'91-180':>6} | "
          f"{'tag':>5} {'review':>6} {'ingest':>6} {'market':>6} | {'rw':>4} | {'full':>5} | {'compl':>6} | {'idle':>4}")
    for media in args.sizes:
        for player in args.players:
            for policy in ("seller", "fuser"):
                w = [0.0] * len(REPORT_WINDOWS)
                src = dict.fromkeys(("tag", "review", "ingest", "market"), 0.0)
                full: list[int | None] = []
                comp = rw = idle = 0.0
                for seed in range(1, args.seeds + 1):
                    res = simulate(cfg, media, player, policy, days, seed, args.new_media, args.start)
                    d = res.days
                    for k, (a, b) in enumerate(REPORT_WINDOWS):
                        b = min(b, len(d))
                        if a <= b:
                            w[k] += (d[b - 1].packs - (d[a - 2].packs if a > 1 else 0)) / (b - a + 1)
                    e = d[-1].earned
                    for k in ("tag", "review", "ingest"):
                        src[k] += e[k] / len(d)
                    src["market"] += (e["sell"] + e["offer"]) / len(d)
                    full.append(res.first_pulled.get(1.0))
                    comp += d[-1].completeness
                    rw += d[-1].reward_packs / len(d)
                    idle += sum(1 for x in d if x.idle_s > 0) / len(d)
                n = args.seeds
                done = [f for f in full if f]
                when = f"{sum(done) / len(done):.0f}" if len(done) == n else f">{days}"
                print(f"{media:>6} {player:>8} {policy:>7} | {w[0] / n:>5.1f} {w[1] / n:>5.1f} {w[2] / n:>5.1f} {w[3] / n:>6.1f} | "
                      f"{src['tag'] / n:>5.0f} {src['review'] / n:>6.0f} {src['ingest'] / n:>6.0f} {src['market'] / n:>6.0f} | "
                      f"{rw / n:>4.1f} | {when:>5} | {comp / n:>6.1%} | {idle / n:>4.0%}")
        print()


class _LoopSim(Sim):
    """A Sim that notes, for every Standard pack bought, what tagging its cards is worth."""

    def open_pack(self, pack: Pack, cost: int, free: bool = False) -> None:
        share = self.book.owned.count / len(self.book.cards)
        n0, waiting = len(self.owned_ids), dict(self.reviews)
        super().open_pack(pack, cost, free)
        t = self.arch.tags_per_card
        worth = sum(eco.card_tagging_value(self.cfg, t, self.tiers[self.book.cards[i].best].multiplier)
                    for i in self.owned_ids[n0:])
        worth += sum(eco.review_reward(self.cfg, self.tiers[tier].multiplier)
                     for i, tier in self.reviews.items() if waiting.get(i) != tier)
        if pack.id == self.pack.id and cost > 0:
            self.loop_log.append((share, cost, worth))


def print_loop(cfg: EconomyConfig, args) -> None:
    """Does the core loop pay for itself? A first day without a time limit (no new files, nothing
    sold or fused) for 1, 2 and 3 tags a card, and what tagging the cards of a bought Standard
    pack pays per 100 coins of its price, by the share of the library already pulled."""
    buckets = (0.10, 0.25, 0.50, 0.75, 0.90, 1.01)
    print(f"\nThe core loop · Standard pack {cfg.packs['standard'].base_cost} coins · first tag {cfg.tagging.first_tag}, "
          f"then {cfg.tagging.extra_tags} · seeds 1..{args.seeds}")
    print("A first day without a time limit, and the tag + review coins a bought Standard pack brings per 100 coins of its price:")
    print(f"{'cards':>6} {'tags':>4} | {'pulled':>6} {'packs':>6} {'minutes':>7} | share pulled: "
          + " ".join(f"{'<' + format(min(b, 1.0), '.0%'):>5}" for b in buckets))
    for media in args.sizes:
        for tags in (1, 2, 3):
            pulled = packs = minutes = 0.0
            earn, spent = [0.0] * len(buckets), [0.0] * len(buckets)
            for seed in range(1, args.seeds + 1):
                sim = _LoopSim(cfg, media, Archetype("marathon", 60 * 24 * 60, 1, tags), "hoarder", seed, 0, 1, args.start)
                sim.loop_log = []
                d = sim.run(1).days[0]
                pulled, packs, minutes = pulled + d.pulled, packs + d.packs, minutes + d.used_s / 60
                for share, cost, worth in sim.loop_log:
                    k = next(j for j, b in enumerate(buckets) if share < b)
                    earn[k] += worth
                    spent[k] += cost
            n = args.seeds
            row = " ".join(f"{(e / s * 100 if s else float('nan')):>5.0f}" for e, s in zip(earn, spent))
            print(f"{media:>6} {tags:>4} | {pulled / n:>6.0%} {packs / n:>6.0f} {minutes / n:>7.0f} |               {row}")


# ---------------------------------------------------------------- command line


def config_with(overrides: list[str]) -> EconomyConfig:
    """The live config with `section.key=value` changes applied (values in TOML syntax)."""
    if not overrides:
        return get_config()
    with config_mod.CONFIG_PATH.open("rb") as f:
        raw = tomllib.load(f)
    for item in overrides:
        path, _, value = item.partition("=")
        node = raw
        keys = path.strip().split(".")
        for k in keys[:-1]:
            node = node[int(k)] if isinstance(node, list) else node[k]
        last = int(keys[-1]) if isinstance(node, list) else keys[-1]
        if not isinstance(node, list) and last not in node:
            raise SystemExit(f"--set {path}: no such key in economy.toml")
        node[last] = tomllib.loads(f"v = {value}")["v"]
    return EconomyConfig.model_validate(raw)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--media", type=int, default=2000, help="cards in the library at the start")
    ap.add_argument("--days", type=int, default=None, help="days to play (default 30, audit 90)")
    ap.add_argument("--player", choices=sorted(ARCHETYPES), default="regular")
    ap.add_argument("--policy", choices=POLICIES, default="seller")
    ap.add_argument("--minutes", type=float, help="override the player's minutes per day")
    ap.add_argument("--tags-per-card", type=int, help="override the player's tags per card")
    ap.add_argument("--new-media", type=int, default=2, help="files added per day after the first")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--start", default=DEFAULT_START, help="date the run starts on (sets the market cycle)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="try a config change, e.g. --set market.base_value=9 (repeatable)")
    ap.add_argument("--audit", action="store_true", help="run the whole grid and print the audit tables")
    ap.add_argument("--report", action="store_true", help="the v0.9.0 table: packs a day by period, coins by source (180 days)")
    ap.add_argument("--loop", action="store_true", help="does one tag a card pay for the next pack?")
    ap.add_argument("--seeds", type=int, default=6, help="seeds 1..N to average over (--report, --loop)")
    ap.add_argument("--sizes", type=int, nargs="+", default=[200, 2000, 20000])
    ap.add_argument("--players", nargs="+", choices=sorted(ARCHETYPES), default=["casual", "regular", "heavy"])
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 7, 30, 90])
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = config_with(args.set)
    if args.report or args.loop:
        args.days = args.days or 180
        if args.sizes == [200, 2000, 20000]:
            args.sizes = [200, 2000, 10000]
        if args.report:
            print_report(cfg, args)
        if args.loop:
            print_loop(cfg, args)
        return
    if args.audit:
        args.days = args.days or 90
        print_audit(cfg, args)
        return
    args.days = args.days or 30
    base = ARCHETYPES[args.player]
    arch = Archetype(base.name, args.minutes or base.minutes, base.sessions, args.tags_per_card or base.tags_per_card,
                     base.deep_tags)
    print_run(Sim(cfg, args.media, arch, args.policy, args.seed, args.new_media, args.days, args.start).run(args.days))


if __name__ == "__main__":
    main()
