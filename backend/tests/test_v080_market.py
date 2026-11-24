"""v0.8.0, the market as a real one: the player is one collector among millions, so their own
trades do not move a price (`trade_impact = 0`; dumping copies to push a price down is gone), and
a hype or a buyout has momentum (a run that keeps rising for a few updates, then corrects) that is
a fair gamble: knowing about it is worth nothing."""

import json
import math
import random

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.game import market as mk
from app.game.config import EconomyConfig, dump_return, get_config
from app.models import MarketCard, MediaItem
from app.services import cards, market
from app.services import packs as pack_svc
from app.services.wallet import credit, local_user
from tests.test_chase import media, own
from tools import simulate as sim

cfg = get_config()
TICK = cfg.market.tick_hours * 3600
T0 = 1_800_000_000.0


@pytest.fixture
def db():
    from app.db import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


def raw_config() -> dict:
    return json.loads(cfg.model_dump_json())


def market_rows(db) -> dict:
    """Everything stored about every card on the market: price level, history, print run, popularity."""
    db.expire_all()
    rows = {r.media_id: r for r in db.scalars(select(MarketCard))}
    return {m.id: (rows[m.id].factor, rows[m.id].history, m.print_run, m.popularity)
            for m in db.scalars(select(MediaItem)) if m.id in rows}


# ---------------------------------------------------------------- the player's own trades


def dump_round(c, card: mk.CardState, index: float, n: int, wanted: bool = False) -> int:
    """Coins gained by one dump-and-buy-back round, in whole coins with the real price functions:
    sell n Normal copies one after another, then buy n back at the price the sales left. The
    player holds the same copies after it, and the price recovers at the next updates."""
    step = mk.CardState(card.media_id, card.print_run, card.popularity, card.factor)
    paid = 0
    for _ in range(n):
        paid += mk.sell_price(c, mk.price(c, step, index), wanted)
        mk.nudge(c, step, sold=True)
    return paid - n * mk.buy_price(c, mk.price(c, step, index))


def test_a_sale_does_not_move_the_price_and_n_copies_pay_n_times_one():
    assert cfg.market.trade_impact == 0 and cfg.market.buy_impact == 0
    rng = random.Random(3)
    for _ in range(300):
        card = mk.CardState(rng.randrange(1, 10**6), rng.choice([25, 60, 300, 2000, 9000, 40000]),
                            rng.uniform(*cfg.market.popularity_range), rng.uniform(*cfg.market.factor_range))
        index = rng.uniform(*cfg.market.index_range)
        before = card.factor
        for t in cfg.treatments:
            for wanted in (False, True):
                one = mk.sell_price(cfg, mk.price(cfg, card, index, t), wanted)
                assert mk.sale_prices(cfg, card, index, t, 40, wanted) == [one] * 40
        mk.nudge(cfg, card, sold=True)
        mk.nudge(cfg, card, sold=False)
        assert card.factor == before


@pytest.mark.price_steps
def test_dumping_copies_to_buy_them_back_cheaper_paid_with_a_trade_impact_and_loses_without(monkeypatch):
    """The last way a player could move a price (until v0.8.0): sell copies, each lowering the
    price 3%, buy them back at the lowered price, wait for the price to recover, repeat."""
    card = mk.CardState(7, 60)  # a Chase card of about 220 coins: listed, so it can be bought back
    assert mk.tradeable(cfg, mk.price(cfg, card, 1.0))
    # now: the price stays where it is, so every round loses the margin, plain or wanted
    for n in range(1, 201):
        assert dump_round(cfg, card, 1.0, n) < 0 and dump_round(cfg, card, 1.0, n, wanted=True) < 0
    assert dump_return(cfg) == pytest.approx(0.85 * 1.3 / 1.15)  # 0.96: selling and buying back, as a wanted card
    # before: with 3% per copy sold the round gained coins from 19 copies on, even on a plain sale
    monkeypatch.setattr(cfg.market, "trade_impact", 0.03)
    monkeypatch.setattr(cfg.market, "factor_range", (0.25, 6.0))  # as it was then (v0.9.0: 0.4 to 4, a higher floor)
    assert dump_round(cfg, card, 1.0, 10) < 0  # a few copies: the margin is still bigger than the drop
    assert dump_round(cfg, card, 1.0, 30) > 0 and dump_round(cfg, card, 1.0, 60) > dump_round(cfg, card, 1.0, 30)
    assert dump_round(cfg, card, 1.0, 12, wanted=True) > 0  # and sooner with the wanted bonus
    assert dump_return(cfg) > 5  # the best round returns several times what the buy-back costs


@pytest.mark.price_steps
def test_the_config_refuses_a_trade_impact_while_cards_are_listed():
    raw = raw_config()
    EconomyConfig.model_validate(raw)
    for impact in (0.03, 0.005, 0.0005):
        bad = json.loads(json.dumps(raw))
        bad["market"]["trade_impact"] = impact
        with pytest.raises(ValidationError, match="trade_impact"):
            EconomyConfig.model_validate(bad)
    # without price steps nothing is listed, so nothing can be bought back: no loop to refuse
    open_ = json.loads(json.dumps(raw))
    open_["market"]["trade_impact"] = 0.03
    open_["fuse"]["price_steps"] = []
    assert dump_return(EconomyConfig.model_validate(open_)) == 0


@pytest.mark.price_steps
def test_no_trade_of_the_player_changes_anything_stored_about_the_market(db, monkeypatch):
    """Sales (one, many, all duplicates), purchases, a fuse and an accepted offer: the price level,
    the history, the print run and the popularity of every card stay exactly as they were."""
    always = cfg.model_copy(update={"market": cfg.market.model_copy(
        update={"offer_chance": 1.0, "wanted_count": 0})})
    monkeypatch.setattr(market, "get_config", lambda: always)
    u = local_user(db)
    items = media(db, 14)
    for m in items[:8]:
        m.print_run, m.popularity = 140, 1.0  # about 140 coins: listed
    credit(db, u, 500_000, "test")
    db.commit()
    a, b, c = items[0], items[1], items[9]
    own(db, u, a, "normal", copies=30)
    own(db, u, a, "foil", copies=6)
    own(db, u, b, "normal", copies=9)
    own(db, u, c, "normal", copies=5)
    market.catch_up(db, T0)
    monkeypatch.setattr(market.time, "time", lambda: T0 + 10)
    before = market_rows(db)
    assert len(before) == 14
    price = market.Prices(db).sell(a.id, "normal")

    start = u.coins
    sold = market.sell(db, u, a.id, "normal", 20)
    assert sold["price"] == 20 * price == u.coins - start  # 20 copies pay 20 × the price of one
    assert market.Prices(db).sell(a.id, "normal") == price  # and the next copy pays the same
    quote = market.duplicates_quote(db, u)
    assert market.sell_duplicates(db, u)["price"] == quote["value"]
    listed = market.listings(db)["normal"]
    for row in listed[:3]:
        for _ in range(8):
            assert market.buy(db, u, row["media_id"])["price"] == row["price"]  # the 8th costs what the 1st did
    assert [(r["media_id"], r["price"]) for r in market.listings(db)["normal"]] == [(r["media_id"], r["price"]) for r in listed]
    pack_svc.fuse(db, u, listed[0]["media_id"], "normal")
    offer = market.current_offer(db, u)
    assert offer is not None
    assert market.answer_offer(db, u, True)["accepted"]
    assert market.Prices(db).price(offer["media_id"], offer["treatment"]) == offer["market_price"]

    assert market_rows(db) == before


@pytest.mark.price_steps
def test_selling_a_listed_card_and_buying_it_back_loses_through_the_services(db):
    u = local_user(db)
    # as many cards as a treatment lists, so the card is for sale whatever the month of warm-up did
    # to the prices (with 12 cards it was the 11th or 12th now and then, and the purchase was refused)
    items = media(db, cfg.market.listings_per_treatment)
    for m in items:
        m.print_run, m.popularity = 140, 1.0
    credit(db, u, 100_000, "test")
    db.commit()
    own(db, u, items[0], "normal", copies=41)
    market.catch_up(db)
    start = u.coins
    market.sell(db, u, items[0].id, "normal", 40)
    for _ in range(40):
        market.buy(db, u, items[0].id)
    assert u.coins < start  # the same 41 copies, fewer coins
    card = cards.card_out(db, u, items[0])
    assert card["sale_quotes"]["normal"][:3] == [n * card["sell_prices"]["normal"] for n in (1, 2, 3)]


# ---------------------------------------------------------------- momentum


def run_value(c, heat: float, ticks: int) -> float:
    """The exact average of what a run with next step `heat` multiplies the price by over `ticks` ticks."""
    m = c.market
    if ticks == 0 or heat < mk.RUN_END:
        return 1.0
    return (m.momentum_chance * math.exp(heat) * run_value(c, heat * m.momentum_decay, ticks - 1)
            + (1 - m.momentum_chance) * mk.run_drop(c, heat))


def test_a_run_rises_more_often_than_not_and_is_fair_on_average():
    m = cfg.market
    assert m.momentum_share > 0 and m.momentum_chance > 0.5
    biggest = mk.run_start(cfg, max(m.hype_boost[1], m.buyout_boost[1]))
    for i in range(1, 101):
        heat = biggest * i / 100
        drop = mk.run_drop(cfg, heat)
        assert 0 < drop < 1 < math.exp(heat)
        assert m.momentum_chance * math.exp(heat) + (1 - m.momentum_chance) * drop == pytest.approx(1.0)
        for ticks in range(1, 12):
            assert run_value(cfg, heat, ticks) == pytest.approx(1.0)  # at every horizon
    # the numbers in economy.toml: a ×1.75 hype and a ×2.75 buyout
    hype, buyout = mk.run_start(cfg, 1.75), mk.run_start(cfg, 2.75)
    assert math.exp(hype) == pytest.approx(1.15, abs=0.005) and mk.run_drop(cfg, hype) == pytest.approx(0.775, abs=0.005)
    assert math.exp(buyout) == pytest.approx(1.288, abs=0.005) and mk.run_drop(cfg, buyout) == pytest.approx(0.568, abs=0.005)


def test_a_run_steps_up_with_smaller_steps_or_breaks_once():
    m = cfg.market
    rng = random.Random(11)
    ups = breaks = 0
    for _ in range(4000):
        card = mk.CardState(1, 300, factor=1.5, run=0.2)
        last, steps = card.factor, []
        while card.run > 0:
            heat = card.run
            mk.step_run(cfg, card, rng)
            if card.factor > last:
                steps.append(card.factor / last)
                assert card.factor / last == pytest.approx(math.exp(heat))
                assert card.run == 0 or card.run == pytest.approx(heat * m.momentum_decay)
                ups += 1
            else:
                assert card.factor / last == pytest.approx(mk.run_drop(cfg, heat)) and card.run == 0
                breaks += 1
            last = card.factor
        assert steps == sorted(steps, reverse=True)  # every further step is smaller
        assert len(steps) <= 7  # 0.2 × 0.6 ^ 6 is under 1%: the run fades out
    assert ups / (ups + breaks) == pytest.approx(m.momentum_chance, abs=0.03)
    still = mk.CardState(1, 300, factor=1.2)
    mk.step_run(cfg, still, rng)
    assert still.factor == 1.2  # no run, no step


def spike(c, rng: random.Random) -> mk.CardState:
    """A card right after a hype or a buyout, as `run_events` leaves it."""
    card = mk.CardState(1, 300, factor=math.exp(rng.gauss(0.0, 0.12)))
    boost = rng.uniform(*(c.market.buyout_boost if rng.random() < 0.2 else c.market.hype_boost))
    card.factor *= boost
    card.run = mk.run_start(c, boost)
    return card


def test_buying_into_a_spike_gains_nothing_on_average_whenever_the_copy_is_sold():
    """What the player can see: the news names the card and the chart shows the spike, so they
    know a run is on. The hard rule: that knowledge must not pay. Buying costs 115% and a sale pays
    85% (110.5% when wanted), so the price would have to rise 4.07% on average for a wanted sale
    to break even. It does not rise at all on average: 0.739 plain, 0.961 wanted, at best."""
    m = cfg.market
    margin = m.buy_markup / (m.sell_rate * m.wanted_bonus)
    assert margin == pytest.approx(1.0407, abs=0.0001)
    horizons = 16
    rng = random.Random(8)
    trials = 40_000
    total = [0.0] * (horizons + 1)
    first_drop = after_rise = 0.0
    for _ in range(trials):
        card = spike(cfg, rng)
        bought, last = card.factor, card.factor
        sold_on_drop = sold_after_rise = None
        for t in range(1, horizons + 1):
            mk.step_card(cfg, card, rng)
            mk.step_run(cfg, card, rng)
            total[t] += card.factor / bought
            if sold_on_drop is None and card.factor < last:
                sold_on_drop = card.factor / bought  # rule: hold while it rises, sell at the first fall
            if sold_after_rise is None and card.factor > last:
                sold_after_rise = card.factor / bought  # rule: sell at the first rise
            last = card.factor
        first_drop += sold_on_drop if sold_on_drop is not None else card.factor / bought
        after_rise += sold_after_rise if sold_after_rise is not None else card.factor / bought
    means = [x / trials for x in total[1:]]
    # the spiked price also drifts back toward the fair value, so the average falls with time
    assert max(means) < 1.0 and means[-1] < means[0]
    assert first_drop / trials < 1.0 and after_rise / trials < 1.0
    best = max(max(means), first_drop / trials, after_rise / trials)
    assert best * m.sell_rate / m.buy_markup < 0.74 and best * m.sell_rate * m.wanted_bonus / m.buy_markup < 0.961


def test_the_run_alone_is_a_fair_gamble_in_a_simulation_too():
    """Without the drift back and the noise (both switched off here) the average stays at 1."""
    still = cfg.model_copy(update={"market": cfg.market.model_copy(
        update={"card_reversion": 1e-12, "card_volatility": 0.0})})
    rng = random.Random(21)
    trials, total = 60_000, [0.0] * 9
    for _ in range(trials):
        card = spike(still, rng)
        bought = card.factor
        if bought * math.exp(card.run) / (1 - still.market.momentum_decay) > still.market.factor_range[1]:
            continue  # leave out the few that could hit the price ceiling, which only lowers them
        for t in range(1, 9):
            mk.step_card(still, card, rng)
            mk.step_run(still, card, rng)
            total[t] += card.factor / bought - 1
    assert all(abs(x / trials) < 0.01 for x in total[1:])


def test_events_start_runs_and_the_runs_step_at_the_next_tick():
    only_hype = cfg.model_copy(update={"market": cfg.market.model_copy(
        update={"reprint_chance": 0.0, "buyout_chance": 0.0, "hype_per_tick": 3.0})})
    deck = [mk.CardState(i, 2000 + i) for i in range(40)]
    news = mk.run_events(only_hype, deck, random.Random(5))
    hit = {i for n in news for i in n.media_ids}
    assert hit and {c.media_id for c in deck if c.run > 0} == hit
    for c in deck:
        if c.media_id in hit:  # the spike itself; the run has not stepped yet
            assert c.run == pytest.approx(cfg.market.momentum_share * math.log(c.factor)) or c.factor > 2.2
    quiet = cfg.model_copy(update={"market": cfg.market.model_copy(
        update={"reprint_chance": 0.0, "buyout_chance": 0.0, "hype_per_tick": 0.0})})
    before = {c.media_id: c.factor for c in deck}
    mk.run_events(quiet, deck, random.Random(6))
    moved = {c.media_id for c in deck if c.factor != before[c.media_id]}
    assert moved == hit  # every running card stepped (up or down), no other card moved


def test_without_momentum_the_events_fall_exactly_as_with_it_and_no_run_starts():
    off = cfg.model_copy(update={"market": cfg.market.model_copy(update={"momentum_share": 0.0})})
    a = [mk.CardState(i, 300 + 500 * i) for i in range(30)]
    b = [mk.CardState(i, 300 + 500 * i) for i in range(30)]
    for tick in range(1, 400):
        news_a = mk.run_events(cfg, a, mk.tick_rng(9, tick))
        news_b = mk.run_events(off, b, mk.tick_rng(9, tick))
        # the same events hit the same cards (reprints pick by value, so only compare the others)
        pick = lambda news: [(n.kind, n.media_ids) for n in news if n.kind != "reprint"]
        assert pick(news_a) == pick(news_b)
        assert all(c.run == 0 for c in b)
    assert any(x.factor != y.factor for x, y in zip(a, b))


@pytest.mark.price_steps
def test_the_config_refuses_a_momentum_that_no_drop_can_balance():
    raw = raw_config()
    for change in ({"momentum_share": 0.5}, {"momentum_chance": 0.75}, {"buyout_boost": [2.0, 7.0]}):
        bad = json.loads(json.dumps(raw))
        bad["market"].update(change)
        with pytest.raises(ValidationError, match="momentum"):
            EconomyConfig.model_validate(bad)
    off = json.loads(json.dumps(raw))
    off["market"].update(momentum_share=0.0, momentum_chance=0.9)
    EconomyConfig.model_validate(off)  # switched off: nothing to balance


def test_runs_are_kept_between_price_updates(db, monkeypatch):
    """The market replays the same whether it is caught up tick by tick or in one go: the runs
    under way are stored in the market state."""
    monkeypatch.setattr(cfg.market, "hype_per_tick", 0.5)  # v0.9.0: hypes are rare now (0.05); this needs runs to store
    media(db, 12)
    state = market.catch_up(db, T0)
    db.expire_all()
    rows = {r.media_id: r.factor for r in db.scalars(select(MarketCard))}
    pure = [mk.CardState(m.id, m.print_run, m.popularity, rows[m.id], run=state.get("runs", {}).get(str(m.id), 0.0))
            for m in db.scalars(select(MediaItem).order_by(MediaItem.id))]
    log_index, seen = state["log_index"], 0
    for i in range(1, 61):
        log_index, _ = mk.advance(cfg, pure, state["seed"], state["tick"] + i, log_index)
        later = market.catch_up(db, T0 + i * TICK)
        seen += bool(later["runs"])
        assert later["runs"] == {str(c.media_id): c.run for c in pure if c.run > 0}
    assert seen > 5  # runs were under way at several updates
    db.expire_all()
    assert later["log_index"] == log_index
    assert {r.media_id: r.factor for r in db.scalars(select(MarketCard))} == {c.media_id: c.factor for c in pure}
    assert {m.id: m.print_run for m in db.scalars(select(MediaItem))} == {c.media_id: c.print_run for c in pure}


def test_the_simulator_uses_the_same_rules():
    """One market tick of the simulator is `mk.advance` without the history: runs included."""
    book = sim.Book(cfg, 40)
    rng = random.Random(2)
    for _ in range(40):
        book.add(mk.roll_print_run(cfg, rng), mk.roll_popularity(cfg, rng))
    twin = [mk.CardState(c.media_id, c.print_run, c.popularity) for c in book.states]
    a = b = 0.0
    runs = 0
    for tick in range(1, 200):
        a = sim.market_tick(cfg, book, 4, tick, a)
        b, _ = mk.advance(cfg, twin, 4, tick, b)
        runs += sum(c.run > 0 for c in book.states)
    assert a == b and runs > 0
    assert [(c.factor, c.print_run, c.run) for c in book.states] == [(c.factor, c.print_run, c.run) for c in twin]
    # and a sale in the simulator leaves the price alone
    s = sim.Sim(cfg, 60, sim.ARCHETYPES["regular"], "seller", seed=3, days=5)
    s.run(5)
    i = next(i for i in s.owned_ids)
    s.book.cards[i].owned[0] += 2
    before = s.book.states[i].factor
    s.sell_copy(i, 0)
    assert s.book.states[i].factor == before
