"""The card market against the database. The rules live in game/market.py; this module stores
the state, catches the simulation up to the clock, and does the trades.

State:
* ``media_items.print_run`` / ``popularity``: issued once, when a card is scanned in (reprints grow print_run).
* ``market_cards``: one row per card with its own price level and a short price history.
* ``app_state['market']``: JSON with the market's seed, the last tick simulated, the index and its
  history, the cards collectors currently want, and the runs under way (``runs``: media id → the
  next step of a spike that is still going, see game/market.step_run).
* ``market_news``: the latest events, for the Market page.

Time: the market moves once per tick (6 hours). Nothing runs in the background: every read calls
``catch_up``, which replays the ticks that passed since the last one (bounded), so the market has
"moved while you were away" and two computers with the same seed would see the same market.
"""

import json
import math
import random
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..game import economy as eco
from ..game import market as mk
from ..game.config import EconomyConfig, get_config
from ..models import AppState, CollectionEntry, MarketCard, MarketNews, MediaItem, User
from .rewards import note_milestones, slot_holdings, slots_change
from .wallet import InsufficientFunds, credit, debit  # noqa: F401  (InsufficientFunds re-exported for the router)

# One market update at a time in this process: two requests arriving together must not both
# replay the same ticks or create the same rows.
_lock = threading.RLock()

STATE_KEY = "market"
NEWS_KEPT = 40
INDEX_HISTORY = 125  # ticks of index history kept (31 days)


class NotListed(Exception):
    """The card is not among the cards for sale right now."""


class NotOwned(Exception):
    pass


# ---------------------------------------------------------------- state


def _load_state(db: Session) -> dict | None:
    row = db.get(AppState, STATE_KEY)
    if row is None or not row.value:
        return None
    try:
        return json.loads(row.value)
    except ValueError:
        return None


def _save_state(db: Session, state: dict) -> None:
    db.merge(AppState(key=STATE_KEY, value=json.dumps(state)))


def issue(db: Session, media: MediaItem, rng: random.Random | None = None) -> None:
    """Give a newly scanned card its print run and popularity (once)."""
    if media.print_run:
        return
    cfg = get_config()
    rng = rng or random.SystemRandom()
    media.print_run = mk.roll_print_run(cfg, rng)
    media.popularity = mk.roll_popularity(cfg, rng)


def _cards(db: Session) -> tuple[list[mk.CardState], dict[int, MediaItem], dict[int, MarketCard]]:
    """Every present card as a CardState, issuing print runs and market rows that are missing."""
    items = {m.id: m for m in db.scalars(select(MediaItem).where(MediaItem.missing.is_(False)))}
    rows = {r.media_id: r for r in db.scalars(select(MarketCard))}
    out = []
    for media_id, m in items.items():
        if not m.print_run:
            issue(db, m)
        row = rows.get(media_id)
        if row is None:
            row = MarketCard(media_id=media_id, factor=1.0, history="[]")
            db.add(row)
            rows[media_id] = row
        out.append(mk.CardState(media_id, m.print_run, m.popularity or 1.0, row.factor or 1.0, json.loads(row.history or "[]")))
    return out, items, rows


def _store(cards: list[mk.CardState], items: dict[int, MediaItem], rows: dict[int, MarketCard]) -> None:
    for c in cards:
        items[c.media_id].print_run = c.print_run
        rows[c.media_id].factor = c.factor
        rows[c.media_id].history = json.dumps(c.history)


def catch_up(db: Session, now: float | None = None) -> dict:
    """Bring the market up to the current tick and return its state. Cheap when nothing is due."""
    with _lock:
        return _catch_up(db, now)


def _catch_up(db: Session, now: float | None) -> dict:
    cfg = get_config()
    now = time.time() if now is None else now
    current = mk.tick_of(cfg, now)
    state = _load_state(db)
    has_cards = db.scalar(select(MediaItem.id).where(MediaItem.missing.is_(False)).limit(1)) is not None
    if state is not None and state["tick"] >= current:
        _issue_new_cards(db, state)
        return state
    if not has_cards:
        return state or {"seed": 0, "tick": current, "log_index": 0.0, "previous": 0.0, "index_history": [], "wanted": []}

    cards, items, rows = _cards(db)
    if state is None:
        # A new market: simulate a month of history (warmup_ticks) so there is something to look at.
        state = {"seed": random.SystemRandom().randrange(1, 2**31), "tick": current - cfg.market.warmup_ticks - 1,
                 "log_index": mk.index_target(cfg, current - cfg.market.warmup_ticks - 1), "previous": 0.0,
                 "index_history": [], "wanted": []}
    first = max(state["tick"] + 1, current - cfg.market.max_catch_up_ticks + 1)
    log_index = state["log_index"]
    # The runs under way (momentum after a hype or a buyout): media id → next step, kept in the state.
    runs = state.get("runs") or {}
    for c in cards:
        c.run = float(runs.get(str(c.media_id), 0.0))
    for tick in range(first, current + 1):
        state["previous"] = log_index
        log_index, news = mk.advance(cfg, cards, state["seed"], tick, log_index)
        state["index_history"].append(round(math.exp(log_index), 4))
        for n in news:
            db.add(MarketNews(tick=tick, kind=n.kind, text=n.text, media_ids=json.dumps(n.media_ids),
                              at=datetime.fromtimestamp(mk.tick_start(cfg, tick), tz=timezone.utc)))
    state["index_history"] = state["index_history"][-INDEX_HISTORY:]
    state["tick"] = current
    state["log_index"] = log_index
    state["runs"] = {str(c.media_id): c.run for c in cards if c.run > 0}
    state["wanted"] = _pick_wanted(db, cfg, state, cards)
    _store(cards, items, rows)
    db.flush()
    state["offer"] = _roll_offer(db, cfg, state, cards)
    _save_state(db, state)
    old = db.scalars(select(MarketNews.id).order_by(MarketNews.id.desc()).offset(NEWS_KEPT)).all()
    if old:
        db.execute(delete(MarketNews).where(MarketNews.id.in_(old)))
    db.commit()
    return state


def _issue_new_cards(db: Session, state: dict) -> None:
    """Cards scanned in since the last tick join the market at their fair price."""
    missing = db.scalars(
        select(MediaItem).where(MediaItem.missing.is_(False), MediaItem.id.not_in(select(MarketCard.media_id)))
    ).all()
    if not missing:
        return
    cfg = get_config()
    # Their past prices follow the market as a whole, so the price chart is never empty. (A first scan
    # of a big folder takes a while: the market opens with the first cards, the rest join here.)
    past = (state.get("index_history") or [math.exp(state["log_index"])])[-mk.HISTORY:]
    for m in missing:
        issue(db, m)
        card = mk.CardState(m.id, m.print_run, m.popularity or 1.0)
        db.add(MarketCard(media_id=m.id, factor=1.0, history=json.dumps([mk.price(cfg, card, i) for i in past])))
    db.commit()


def _pick_wanted(db: Session, cfg: EconomyConfig, state: dict, cards: list[mk.CardState]) -> list[int]:
    """Collectors want a few of the cards the player owns: fixed for one tick.

    v0.8.0: only a card whose most valuable held copy is worth `market.wanted_min_value` or more
    (the same value an offer looks at)."""
    by_id = {c.media_id: c for c in cards}
    index = math.exp(state["log_index"])
    held: dict[int, int] = {}
    for media_id, treatment in db.execute(
        select(CollectionEntry.media_id, CollectionEntry.treatment)
        .join(MediaItem, MediaItem.id == CollectionEntry.media_id)
        .where(CollectionEntry.copies > 0, MediaItem.missing.is_(False))
    ):
        if media_id in by_id and treatment in cfg.treatments:
            held[media_id] = max(held.get(media_id, 0), mk.price(cfg, by_id[media_id], index, treatment))
    normal = {i: mk.price(cfg, by_id[i], index) for i in held}  # this tick's price: what the fuse count reads
    return mk.pick_wanted(cfg, held, mk.tick_rng(state["seed"] + 17, state["tick"]), normal)


def _roll_offer(db: Session, cfg: EconomyConfig, state: dict, cards: list[mk.CardState]) -> dict | None:
    """This tick's offer from a collector, for one of the player's most valuable copies (or none)."""
    by_id = {c.media_id: c for c in cards}
    index = math.exp(state["log_index"])
    holdings = [
        (media_id, treatment, mk.price(cfg, by_id[media_id], index, treatment))
        for media_id, treatment in db.execute(
            select(CollectionEntry.media_id, CollectionEntry.treatment).where(CollectionEntry.copies > 0)
        ) if media_id in by_id and treatment in cfg.treatments
    ]
    hit = mk.roll_offer(cfg, holdings, mk.tick_rng(state["seed"] + 31, state["tick"]), set(state.get("wanted", [])))
    if hit is None:
        return None
    return {"media_id": hit[0], "treatment": hit[1], "price": hit[2], "tick": state["tick"]}


def current_offer(db: Session, user: User, state: dict | None = None) -> dict | None:
    """The open offer with today's market price next to it, or None (none made, taken, or the copy is gone).

    v0.7.1: an offer is only shown while it is worth taking. None for a wanted card, and none while
    the offer is below what selling the copy on the market pays right now."""
    cfg = get_config()
    state = state or catch_up(db)
    offer = state.get("offer")
    if not offer or offer.get("tick") != state["tick"]:
        return None
    have = db.scalar(select(CollectionEntry.copies).where(
        CollectionEntry.user_id == user.id, CollectionEntry.media_id == offer["media_id"],
        CollectionEntry.treatment == offer["treatment"])) or 0
    if have < 1:
        return None
    prices = Prices(db, [offer["media_id"]], state)
    if offer["media_id"] in prices.wanted or offer["price"] < prices.sell(offer["media_id"], offer["treatment"]):
        return None
    return {
        "media_id": offer["media_id"],
        "treatment": offer["treatment"],
        "price": offer["price"],
        "market_price": prices.price(offer["media_id"], offer["treatment"]),
        "sell_price": prices.sell(offer["media_id"], offer["treatment"]),
        # v0.8.0: how many copies of that treatment the player holds, and whether taking the
        # offer empties its version slot (the last copy of a Foil, Holo or Special)
        "copies": have,
        "empties_slot": eco.sale_empties_slot(cfg, offer["treatment"], have),
        "expires_at": datetime.fromtimestamp(mk.tick_start(cfg, state["tick"] + 1), tz=timezone.utc).isoformat(),
    }


class NoOffer(Exception):
    pass


def answer_offer(db: Session, user: User, accept: bool) -> dict:
    """Take the collector's offer (the copy goes, the coins arrive) or turn it down. Either way it is gone."""
    from .packs import take_copy

    with _lock:
        state = catch_up(db)
        offer = current_offer(db, user, state)
        if offer is None:
            raise NoOffer()
        before = slot_holdings(db, user)
        if accept:
            media_id, treatment = offer["media_id"], offer["treatment"]
            if not take_copy(db, user, media_id, treatment):
                db.rollback()
                raise NoOffer()
            credit(db, user, offer["price"], "offer", ref=f"media:{media_id}:{treatment}")
            _nudge(db, media_id, sold=True)
        state["offer"] = None
        _save_state(db, state)
        db.flush()
        change = slots_change(db, user, before)
        db.commit()
    return {"accepted": accept, "price": offer["price"] if accept else 0, "coins": user.coins,
            "media_id": offer["media_id"], "treatment": offer["treatment"], "slots": change}


def reset(db: Session) -> None:
    """A fresh market: new seed, every card back at its fair price, print runs issued again."""
    db.execute(delete(MarketCard))
    db.execute(delete(MarketNews))
    row = db.get(AppState, STATE_KEY)
    if row is not None:
        db.delete(row)
    for m in db.scalars(select(MediaItem)):
        m.print_run = 0
        m.popularity = 1.0
    db.flush()


# ---------------------------------------------------------------- prices


class Prices:
    """Today's prices for a set of cards, loaded once."""

    def __init__(self, db: Session, media_ids: list[int] | None = None, state: dict | None = None):
        self.cfg = get_config()
        self.state = state or catch_up(db)
        self.index = math.exp(self.state["log_index"])
        self.wanted = set(self.state.get("wanted", []))
        q = select(MediaItem.id, MediaItem.print_run, MediaItem.popularity, MarketCard.factor, MarketCard.history).join(
            MarketCard, MarketCard.media_id == MediaItem.id, isouter=True)
        if media_ids is not None:
            q = q.where(MediaItem.id.in_(media_ids))
        else:
            q = q.where(MediaItem.missing.is_(False))
        self.cards: dict[int, mk.CardState] = {}
        self._raw: dict[int, str] = {}  # histories stay JSON text until one is asked for (there are a lot of them)
        for media_id, print_run, popularity, factor, history in db.execute(q):
            self.cards[media_id] = mk.CardState(media_id, print_run or self.cfg.market.reference_print, popularity or 1.0,
                                                factor or 1.0)
            self._raw[media_id] = history or "[]"

    def price(self, media_id: int, treatment: str = "normal") -> int:
        card = self.cards.get(media_id)
        return mk.price(self.cfg, card, self.index, treatment) if card else 1

    def factor(self, media_id: int, treatment: str) -> float:
        """What a copy at this treatment costs as a multiple of the card's Normal copy (the card's
        own premium, v0.8.0). 1 for Normal and for a card that is not on the market."""
        card = self.cards.get(media_id)
        return mk.treatment_factor(self.cfg, card, treatment) if card else 1.0

    def factors(self, media_id: int) -> dict[str, float]:
        return {t.id: round(self.factor(media_id, t.id), 4) for t in self.cfg.tiers()}

    def sell(self, media_id: int, treatment: str) -> int:
        return mk.sell_price(self.cfg, self.price(media_id, treatment), media_id in self.wanted)

    def plain_sell(self, media_id: int, treatment: str) -> int:
        """What a copy sells for without the wanted bonus."""
        return mk.sell_price(self.cfg, self.price(media_id, treatment))

    def sale_prices(self, media_id: int, treatment: str, count: int) -> list[int]:
        """What selling `count` copies pays, copy by copy (every copy the same since v0.8.0: the
        player's own sale does not move the price)."""
        card = self.cards.get(media_id)
        if card is None:
            return [1] * max(0, count)
        return mk.sale_prices(self.cfg, card, self.index, treatment, count, media_id in self.wanted)

    def quote(self, media_id: int, treatment: str, count: int) -> int:
        """What selling `count` copies pays in all: the number to show before the sale."""
        return sum(self.sale_prices(media_id, treatment, count))

    def tick_price(self, media_id: int) -> int:
        """The Normal price at the last price update: the last point of the card's history.
        The player's own trades never change it. Falls back to the live price for a
        card without history. Only the last number of the stored history is read."""
        if media_id not in self.cards:
            return 1
        card = self.cards[media_id]
        if card.history:
            return card.history[-1]
        tail = self._raw.get(media_id, "[]").rstrip().rstrip("]").rsplit(",", 1)[-1].lstrip("[").strip()
        try:
            return int(tail) if tail else self.price(media_id)
        except ValueError:
            return self.price(media_id)

    def fuse_need(self, media_id: int) -> int:
        """Copies one fuse of this card takes: set by its price at the last update (v0.7.1), so
        buying the missing copy can't raise the count under the player's hands."""
        return eco.fuse_need(self.cfg, self.tick_price(media_id))

    def history(self, media_id: int) -> list[int]:
        """Normal price at the last ticks, oldest first (parsed on first use)."""
        card = self.cards.get(media_id)
        if card is None:
            return []
        if not card.history and media_id in self._raw:
            card.history = json.loads(self._raw.pop(media_id))
        return card.history

    def trend(self, media_id: int) -> float:
        return round(mk.trend(self.history(media_id)), 4) if media_id in self.cards else 0.0

    def info(self, media_id: int) -> dict:
        card = self.cards.get(media_id)
        if card is None:
            return {"value": 1, "print_run": 0, "print_tier": "", "trend": 0.0, "wanted": False, "min_pack": None,
                    "changes": {"update": 0.0, "day": 0.0, "week": 0.0, "month": 0.0}}
        return {
            "value": self.price(media_id),
            "print_run": card.print_run,
            "print_tier": mk.print_tier_name(self.cfg, card.print_run),
            # the price change over the last 24 hours (four 6-hour updates), as a fraction
            "trend": self.trend(media_id),
            # v0.9.0: the change per range, so a label can say what it measures and agree with the
            # chart next to it: update (the last price update), day (= trend), week, month
            "changes": mk.changes(self.cfg, self.history(media_id)),
            "wanted": media_id in self.wanted,
            # the cheapest pack that holds this card (None = every pack)
            "min_pack": mk.min_pack(self.cfg, card.print_run),
        }


def card_history(db: Session, media_id: int) -> dict | None:
    """One card's price history for the chart and the 30-day table: a Normal copy's price at each
    tick, oldest first, and the same history as one row per local day.

    Every price is for a Normal copy. `treatment_value` is THIS card's factor per treatment (its
    own premium, v0.8.0): a Foil's price on a day is the Normal price × treatment_value.foil."""
    cfg = get_config()
    prices = Prices(db, [media_id])
    if media_id not in prices.cards:
        return None
    points = prices.history(media_id)
    last = prices.state["tick"]
    starts = [mk.tick_start(cfg, last - (len(points) - 1 - i)) for i in range(len(points))]
    return {
        "media_id": media_id,
        "tick_hours": cfg.market.tick_hours,
        "points": [
            {"at": datetime.fromtimestamp(at, tz=timezone.utc).isoformat(), "price": p}
            for at, p in zip(starts, points)
        ],
        # one row per day of the player's clock, the last 30, oldest first: the price at the end
        # of the day and the change against the day before
        "days": mk.daily_table([(datetime.fromtimestamp(at).date().isoformat(), p) for at, p in zip(starts, points)]),
        "treatment_value": prices.factors(media_id),
        "next_tick_at": datetime.fromtimestamp(mk.tick_start(cfg, last + 1), tz=timezone.utc).isoformat(),
        **prices.info(media_id),
    }


def weights_and_values(db: Session) -> dict[int, tuple[float, float, float]]:
    """media id → (pull weight, value of a Normal copy, premium exponent), for rolling packs."""
    p = Prices(db)
    return {i: (mk.draw_weight(p.cfg, c.print_run), float(p.price(i)), mk.premium(p.cfg, c.popularity, i))
            for i, c in p.cards.items()}


def not_in_pack(db: Session, pack_id: str) -> set[int]:
    """The cards this pack can't pull: they are only in dearer packs."""
    p = Prices(db)
    return {i for i, c in p.cards.items() if not mk.pack_holds(p.cfg, pack_id, c.print_run)}


def pack_exclusives(db: Session, user: User) -> dict[str, dict[str, int]]:
    """Per pack: how many cards no cheaper pack holds, and how many of those the player still misses."""
    p = Prices(db)
    present = set(db.scalars(select(MediaItem.id).where(MediaItem.missing.is_(False))).all())
    owned = set(db.scalars(
        select(CollectionEntry.media_id).where(CollectionEntry.user_id == user.id, CollectionEntry.copies > 0)
    ).all())
    out = {pid: {"cards": 0, "missing": 0} for pid in p.cfg.packs}
    for i, c in p.cards.items():
        need = mk.min_pack(p.cfg, c.print_run)
        if need is None or i not in present:
            continue
        out[need]["cards"] += 1
        out[need]["missing"] += i not in owned
    return out


def collection_value(db: Session, user: User, prices: Prices | None = None) -> int:
    """What every copy the player owns is worth at today's prices."""
    prices = prices or Prices(db)
    total = 0
    for media_id, treatment, copies in db.execute(
        select(CollectionEntry.media_id, CollectionEntry.treatment, CollectionEntry.copies)
        .join(MediaItem, MediaItem.id == CollectionEntry.media_id)
        .where(CollectionEntry.user_id == user.id, CollectionEntry.copies > 0, MediaItem.missing.is_(False))
    ):
        total += prices.price(media_id, treatment) * copies
    return total


def summary(db: Session, state: dict | None = None) -> dict:
    """The market in one glance: index, mood, and when it moves next."""
    cfg = get_config()
    state = state or catch_up(db)
    hist = state.get("index_history", [])
    return {
        "tick": state["tick"],
        "index": round(math.exp(state["log_index"]), 4),
        "mood": mk.mood(state["log_index"], state.get("previous", state["log_index"])),
        "change": round(hist[-1] / hist[-5] - 1, 4) if len(hist) >= 5 and hist[-5] else 0.0,  # over the last day
        "next_tick_at": datetime.fromtimestamp(mk.tick_start(cfg, state["tick"] + 1), tz=timezone.utc).isoformat(),
        "tick_hours": cfg.market.tick_hours,
    }


def listings(db: Session, prices: Prices | None = None) -> dict[str, list[dict]]:
    """The cards for sale (v0.8.0): per treatment, common → rare, the `listings_per_treatment` most
    valuable cards at that treatment, each as one copy of that treatment at its buy price.

    Only `mk.tradeable` cards are listed: those whose Normal price, at the last price update and
    now, has reached the first fuse price step. So a list can be shorter than that, or empty, in a
    small or cheap library. A card's premium for the rarer treatments is its own, so the four lists
    differ. A card the player already holds is listed all the same. Each row: media_id, treatment, `value` (the market
    price of that copy), `price` (what buying it costs: value × buy_markup, rounded up), and the
    card's market info (print run, trend, wanted, ...)."""
    prices = prices or Prices(db)
    cfg = prices.cfg
    out: dict[str, list[dict]] = {}
    listed = [i for i in prices.cards if mk.tradeable(cfg, prices.tick_price(i), prices.price(i))]
    for t in cfg.tiers():
        worth = {i: prices.price(i, t.id) for i in listed}
        ranked = sorted(worth, key=lambda i: (-worth[i], i))[: cfg.market.listings_per_treatment]
        out[t.id] = [{**prices.info(i), "media_id": i, "treatment": t.id, "value": worth[i],
                      "price": mk.buy_price(cfg, worth[i])} for i in ranked]
    return out


def cheapest_listing(db: Session) -> int | None:
    """The lowest buy price among everything for sale, or None when nothing is."""
    return min((row["price"] for rows in listings(db).values() for row in rows), default=None)


def news(db: Session, limit: int = 12) -> list[dict]:
    """The latest news, newest first. `cards` names the cards an item is about and says which the player owns:
    news about "a card" is no use without knowing which one."""
    from .cards import card_code, card_number, card_title  # late: cards imports this module for prices

    rows = db.scalars(select(MarketNews).order_by(MarketNews.id.desc()).limit(limit)).all()
    ids_of = {r.id: json.loads(r.media_ids or "[]") for r in rows}
    ids = sorted({i for v in ids_of.values() for i in v})
    named: dict[int, dict] = {}
    if ids:
        owned = set(db.scalars(
            select(CollectionEntry.media_id).where(CollectionEntry.media_id.in_(ids), CollectionEntry.copies > 0)
        ).all())
        for m in db.scalars(select(MediaItem).where(MediaItem.id.in_(ids), MediaItem.missing.is_(False))):
            named[m.id] = {"id": m.id, "title": card_title(m), "number": card_number(m), "code": card_code(m),
                           "owned": m.id in owned}
    return [{"kind": r.kind, "text": r.text, "media_ids": ids_of[r.id],
             "cards": [named[i] for i in ids_of[r.id] if i in named],
             "at": (r.at if r.at.tzinfo else r.at.replace(tzinfo=timezone.utc)).isoformat()} for r in rows]


# ---------------------------------------------------------------- trades


def _nudge(db: Session, media_id: int, sold: bool) -> None:
    """What the player's own trade does to the card's price: nothing since v0.8.0 (`trade_impact`
    and `buy_impact` are 0; the player is one collector among millions). See `mk.nudge`."""
    cfg = get_config()
    if not (cfg.market.trade_impact if sold else cfg.market.buy_impact):
        return
    row = db.get(MarketCard, media_id)
    if row is None:
        return
    card = mk.CardState(media_id, 1, 1.0, row.factor or 1.0)
    mk.nudge(cfg, card, sold)
    row.factor = card.factor


def sell(db: Session, user: User, media_id: int, treatment: str, count: int = 1, keep: int = 0) -> dict:
    """Sell `count` copies at today's price: `count` times what one copy pays. The sale does not
    move the card's price (v0.8.0; before, each copy sold lowered it 3% for the next one).

    keep: never go below this many copies (the "sell duplicates" button keeps one). The check is
    part of the UPDATE that takes the copy, so requests arriving together can't sell past it.
    The reply carries `slots`: what the sale did to the versions held (v0.8.0)."""
    before = slot_holdings(db, user)
    out = _sell(db, user, media_id, treatment, count, keep)
    return {**out, "slots": slots_change(db, user, before)}


def _sell(db: Session, user: User, media_id: int, treatment: str, count: int = 1, keep: int = 0) -> dict:
    from .packs import take_copy  # late: packs imports this module for prices

    cfg = get_config()
    media = db.get(MediaItem, media_id)
    if media is None or media.missing or treatment not in cfg.treatments or count < 1:
        raise NotOwned()
    state = catch_up(db)
    total = sold = 0
    # One list of prices for the whole sale, the same one every quote is made from.
    for price in Prices(db, [media_id], state).sale_prices(media_id, treatment, count):
        if not take_copy(db, user, media_id, treatment, keep=keep):
            break
        credit(db, user, price, "sell", ref=f"media:{media_id}:{treatment}")
        _nudge(db, media_id, sold=True)
        db.flush()
        total += price
        sold += 1
    if sold == 0:
        db.rollback()
        raise NotOwned()
    left = db.scalar(select(func.coalesce(func.sum(CollectionEntry.copies), 0)).where(
        CollectionEntry.user_id == user.id, CollectionEntry.media_id == media_id, CollectionEntry.treatment == treatment)) or 0
    db.commit()
    return {"price": total, "sold": sold, "coins": user.coins, "copies_left": left}


def sell_duplicates(db: Session, user: User, keep: int = 1) -> dict:
    """Sell every copy beyond `keep` of each card and treatment. The player presses the button for it."""
    rows = db.execute(
        select(CollectionEntry.media_id, CollectionEntry.treatment, CollectionEntry.copies)
        .join(MediaItem, MediaItem.id == CollectionEntry.media_id)
        .where(CollectionEntry.user_id == user.id, CollectionEntry.copies > keep, MediaItem.missing.is_(False))
    ).all()
    before = slot_holdings(db, user)
    total = sold = 0
    for media_id, treatment, copies in rows:
        try:
            r = _sell(db, user, media_id, treatment, copies - keep, keep=keep)
        except NotOwned:  # another request sold these copies meanwhile: nothing left above `keep`
            continue
        total += r["price"]
        sold += r["sold"]
    # With keep = 1 no slot is emptied; `slots` is there so every sale answers alike.
    return {"price": total, "sold": sold, "coins": user.coins, "slots": slots_change(db, user, before)}


def duplicates_quote(db: Session, user: User, prices: "Prices | None" = None, keep: int = 1) -> dict:
    """What "sell all duplicates" would sell and pay right now: exactly what the sale then pays."""
    prices = prices or Prices(db)
    rows = db.execute(
        select(CollectionEntry.media_id, CollectionEntry.treatment, CollectionEntry.copies)
        .join(MediaItem, MediaItem.id == CollectionEntry.media_id)
        .where(CollectionEntry.user_id == user.id, CollectionEntry.copies > keep, MediaItem.missing.is_(False))
    ).all()
    return {
        "copies": sum(c - keep for _, _, c in rows),
        "value": sum(prices.quote(m, t, c - keep) for m, t, c in rows),
    }


def buy(db: Session, user: User, media_id: int, treatment: str = "normal") -> dict:
    """Buy one copy of a listed card at the treatment it is listed in, at that treatment's price.
    Raises NotListed (not among that treatment's listings), or InsufficientFunds.

    The copy counts like a pulled one: it fills its version slot, and a milestone it reaches
    becomes claimable (`milestones`, as in the open-pack reply)."""
    from .packs import _add_copy

    prices = Prices(db)
    offer = next((x for x in listings(db, prices).get(treatment, []) if x["media_id"] == media_id), None)
    if offer is None:
        raise NotListed()
    before = slot_holdings(db, user)
    debit(db, user, offer["price"], "market_buy", ref=f"media:{media_id}:{treatment}")
    _add_copy(db, user, media_id, treatment)
    _nudge(db, media_id, sold=False)
    db.flush()  # ownership is now visible to the counts below
    milestones = note_milestones(db, user)  # v0.9.0: noted as claimable, not paid
    change = slots_change(db, user, before)
    copies = db.scalar(select(CollectionEntry.copies).where(
        CollectionEntry.user_id == user.id, CollectionEntry.media_id == media_id,
        CollectionEntry.treatment == treatment)) or 0
    db.commit()
    return {"price": offer["price"], "coins": user.coins, "media_id": media_id, "treatment": treatment,
            "copies": copies, "milestones": milestones, "slots": change}
