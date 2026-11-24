"""v0.4.0 (test round 3): the card market, kept duplicates and fusing, collector offers, the shop
deal, the tag rush, more tags per card, tag suggestions, buying as many as you can afford, reset."""

import math
import random
import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.game import economy as eco
from app.game import market as mk
from app.game import packs as pack_rules
from app.game.config import get_config
from app.main import app
from app.models import AppState, CollectionEntry, MarketCard, MediaItem, Pack, Tag, Transaction
from app.services import market, offers, organize
from app.services import packs as pack_svc
from app.services.wallet import credit, local_user
from tests.test_chase import media, own

cfg = get_config()
TICK = cfg.market.tick_hours * 3600
T0 = 1_800_000_000.0  # a fixed "now" for the market clock


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        yield c


def copies(db):
    db.expire_all()
    return {(e.media_id, e.treatment): e.copies for e in db.scalars(select(CollectionEntry)) if e.copies}


# ---------------------------------------------------------------- pure market rules


def test_scarce_and_wanted_cards_are_worth_more_whatever_their_treatment():
    common = mk.CardState(1, print_run=40_000)
    scarce = mk.CardState(2, print_run=50)
    assert mk.price(cfg, scarce, 1.0) > 20 * mk.price(cfg, common, 1.0)
    # a plain Normal copy of a scarce card outprices a Foil of a mass print
    assert mk.price(cfg, scarce, 1.0, "normal") > mk.price(cfg, common, 1.0, "foil")
    wanted = mk.CardState(3, print_run=40_000, popularity=3.0)
    assert mk.price(cfg, wanted, 1.0) > mk.price(cfg, common, 1.0)
    assert mk.price(cfg, common, 1.0, "special") > mk.price(cfg, common, 1.0, "holo") > mk.price(cfg, common, 1.0, "foil")
    assert mk.price(cfg, common, 0.0001) == 1  # never below one coin


def test_print_runs_follow_the_tiers_and_scarce_cards_are_pulled_less():
    rng = random.Random(4)
    runs = [mk.roll_print_run(cfg, rng) for _ in range(4000)]
    chase = cfg.market.print_tiers[-1]
    share = sum(chase.min <= r <= chase.max for r in runs) / len(runs)
    assert 0.004 < share < 0.02  # about 1 card in 100 is a chase print
    assert min(runs) >= chase.min and max(runs) <= cfg.market.print_tiers[0].max
    assert mk.draw_weight(cfg, 40_000) > 20 * mk.draw_weight(cfg, 50)
    assert mk.print_tier_name(cfg, 60) == "Chase" and mk.print_tier_name(cfg, 30_000) == "Mass print"


def test_the_market_is_replayable_and_moves_in_cycles():
    def run(seed):
        cards = [mk.CardState(i, 1000 * (i + 1)) for i in range(30)]
        log_index, path = 0.0, []
        for tick in range(1, 4 * 365):
            log_index, _ = mk.advance(cfg, cards, seed, tick, log_index)
            path.append(math.exp(log_index))
        return path, [c.factor for c in cards]

    a, fa = run(5)
    b, fb = run(5)
    assert a == b and fa == fb  # same seed, same market
    assert run(6)[0] != a
    lo, hi = cfg.market.index_range
    assert lo <= min(a) and max(a) <= hi
    assert max(a) / min(a) > 1.3  # a year holds a real rise and fall


def test_a_reprint_adds_supply_cuts_value_and_spares_the_reserved_list():
    chance = cfg.model_copy(update={"market": cfg.market.model_copy(update={
        "reprint_chance": 1.0, "hype_per_tick": 0.0, "buyout_chance": 0.0, "reprint_share": 0.5})})
    cards = [mk.CardState(i, 60 if i < 5 else 2000 + i) for i in range(40)]
    before = {c.media_id: (c.print_run, mk.price(chance, c, 1.0)) for c in cards}
    news = mk.run_events(chance, cards, random.Random(1))
    assert news and news[0].kind == "reprint" and news[0].media_ids
    for c in cards:
        if c.media_id in news[0].media_ids:
            assert c.print_run > before[c.media_id][0] and mk.price(chance, c, 1.0) < before[c.media_id][1]
        if c.media_id < 5:
            assert c.print_run == 60  # under reserved_below: never reprinted


def test_selling_and_buying_prices_and_your_own_trades_leave_the_card_alone(monkeypatch):
    c = mk.CardState(1, 2000)
    p = mk.price(cfg, c, 1.0)
    assert mk.sell_price(cfg, p) == int(p * cfg.market.sell_rate) < p < mk.buy_price(cfg, p)
    assert mk.sell_price(cfg, p, wanted=True) > mk.sell_price(cfg, p)
    # v0.8.0: the player is one collector among millions. Neither a sale nor a purchase moves the price.
    assert cfg.market.trade_impact == 0 and cfg.market.buy_impact == 0
    mk.nudge(cfg, c, sold=True)
    mk.nudge(cfg, c, sold=False)
    assert c.factor == 1.0
    # the rule behind the number (v0.4.0 to v0.7.1: 3% down per copy sold) still works
    monkeypatch.setattr(cfg.market, "trade_impact", 0.03)
    mk.nudge(cfg, c, sold=True)
    assert c.factor == pytest.approx(0.97)
    mk.nudge(cfg, c, sold=False)
    assert c.factor == pytest.approx(0.97)  # a purchase leaves the price alone (buy_impact = 0)


def test_an_offer_is_for_a_top_card_and_near_the_market_price():
    holdings = [(i, "normal", 10 * (i + 1)) for i in range(12)]
    always = cfg.model_copy(update={"market": cfg.market.model_copy(update={"offer_chance": 1.0})})
    lo, hi = cfg.market.offer_range
    for seed in range(40):
        media_id, treatment, offered = mk.roll_offer(always, holdings, random.Random(seed))
        market_price = dict((h[0], h[2]) for h in holdings)[media_id]
        assert media_id >= 12 - cfg.market.offer_top  # one of the most valuable
        assert round(market_price * lo) - 1 <= offered <= round(market_price * hi) + 1
    never = cfg.model_copy(update={"market": cfg.market.model_copy(update={"offer_chance": 0.0})})
    assert mk.roll_offer(never, holdings, random.Random(1)) is None
    assert mk.roll_offer(always, [(1, "normal", 3)], random.Random(1)) is None  # nothing worth an offer


# ---------------------------------------------------------------- the market against the database


def test_cards_are_issued_once_and_the_market_catches_up_with_the_clock(db):
    items = media(db, 40)
    state = market.catch_up(db, T0)
    db.expire_all()
    runs = {m.id: m.print_run for m in db.scalars(select(MediaItem))}
    assert all(r > 0 for r in runs.values())
    assert len(db.scalars(select(MarketCard)).all()) == 40
    assert len(state["index_history"]) == cfg.market.warmup_ticks + 1  # a week of history from the start
    assert market.catch_up(db, T0 + 60)["tick"] == state["tick"]  # nothing is due within the tick
    later = market.catch_up(db, T0 + 3 * TICK)
    assert later["tick"] == state["tick"] + 3
    db.expire_all()
    assert {m.id: m.print_run for m in db.scalars(select(MediaItem))}.keys() == runs.keys()
    hist = db.scalars(select(MarketCard.history)).first()
    assert len(eval(hist)) <= mk.HISTORY
    assert items


def test_a_long_break_replays_a_bounded_number_of_ticks(db):
    media(db, 10)
    first = market.catch_up(db, T0)
    after = market.catch_up(db, T0 + 5000 * TICK)
    assert after["tick"] == first["tick"] + 5000
    assert len(after["index_history"]) <= 125


def test_new_cards_join_the_market_between_ticks(db):
    media(db, 5)
    market.catch_up(db, T0)
    extra = MediaItem(path="late.jpg", sha256="f" * 64, kind="image")
    db.add(extra)
    db.commit()
    market.catch_up(db, T0 + 30)
    db.expire_all()
    assert db.get(MediaItem, extra.id).print_run > 0 and db.get(MarketCard, extra.id) is not None


def test_selling_pays_the_market_price_and_leaves_it_alone(db):
    u = local_user(db)
    m = media(db, 3)[0]
    m.print_run = 300  # a scarce card: a price move of a few percent would show after rounding
    db.commit()
    own(db, u, m, "foil", copies=3)
    prices = market.Prices(db, [m.id])
    expect = prices.sell(m.id, "foil")
    start = u.coins
    r = pack_svc.sell_card(db, u, m.id, "foil")
    slots = r.pop("slots")  # v0.8.0
    assert slots["delta"] == 0 and slots["emptied"] == []
    assert r == {"price": expect, "sold": 1, "coins": start + expect, "copies_left": 2}
    # v0.8.0: one seller among millions does not move the price (before: 3% down per copy sold)
    assert market.Prices(db, [m.id]).price(m.id, "foil") == prices.price(m.id, "foil")
    two = pack_svc.sell_card(db, u, m.id, "foil", 5)  # asks for 5, has 2
    assert two["sold"] == 2 and two["copies_left"] == 0 and two["price"] == 2 * expect
    assert two["slots"]["delta"] == -1 and two["slots"]["emptied"] == [{"media_id": m.id, "treatment": "foil"}]
    with pytest.raises(pack_svc.NotOwned):
        pack_svc.sell_card(db, u, m.id, "foil")


@pytest.mark.price_steps  # v0.8.0: only cards past the first fuse price step are listed
def test_only_listed_cards_can_be_bought_and_buying_reveals_them(db):
    u = local_user(db)
    for m in media(db, 60)[:15]:
        m.print_run, m.popularity = 140, 1.0  # worth trading: about 140 coins each, too scarce to be reprinted
    credit(db, u, 100_000, "test")
    db.commit()
    offers_ = market.listings(db)["normal"]  # v0.8.0: one list per treatment
    assert len(offers_) == cfg.market.listings_per_treatment
    assert [o["price"] for o in offers_] == sorted((o["price"] for o in offers_), reverse=True)
    top = offers_[0]
    start = u.coins
    r = market.buy(db, u, top["media_id"])
    assert r["price"] == top["price"] and u.coins == start - top["price"]
    assert copies(db) == {(top["media_id"], "normal"): 1}
    listed = {o["media_id"] for o in offers_}
    cheap = next(m for m in db.scalars(select(MediaItem.id)) if m not in listed)
    with pytest.raises(market.NotListed):
        market.buy(db, u, cheap)


def test_a_card_has_a_month_of_price_history_for_the_chart(client):
    with SessionLocal() as db:
        m = media(db, 3)[0].id
        market.catch_up(db, T0)
        market.catch_up(db, T0 + 200 * TICK)
    h = client.get(f"/api/market/cards/{m}/history").json()
    assert len(h["points"]) == mk.HISTORY == 124  # 31 days, one point every 6 hours
    assert h["points"][-1]["price"] == h["value"] or abs(h["points"][-1]["price"] - h["value"]) <= max(2, h["value"] // 10)
    assert h["points"][0]["at"] < h["points"][-1]["at"] and h["treatment_value"]["foil"] > 1
    assert h["treatment_value"]["normal"] == 1
    assert client.get("/api/market/cards/999999/history").status_code == 404


def test_collection_value_adds_up_every_copy(db):
    u = local_user(db)
    a, b = media(db, 2)
    own(db, u, a, "normal", copies=2)
    own(db, u, b, "holo")
    p = market.Prices(db)
    assert market.collection_value(db, u) == 2 * p.price(a.id, "normal") + p.price(b.id, "holo")


def test_selling_all_duplicates_keeps_one_copy_of_each(db):
    u = local_user(db)
    a, b = media(db, 2)
    own(db, u, a, "normal", copies=4)
    own(db, u, b, "foil", copies=1)
    start = u.coins
    r = market.sell_duplicates(db, u)
    assert r["sold"] == 3 and u.coins == start + r["price"] > start
    assert copies(db) == {(a.id, "normal"): 1, (b.id, "foil"): 1}


def test_a_collectors_offer_can_be_taken_once_or_turned_down(db, monkeypatch):
    # wanted_count 0: no offer is made for a wanted card (v0.7.1), and the only card owned would be wanted
    always = cfg.model_copy(update={"market": cfg.market.model_copy(
        update={"offer_chance": 1.0, "offer_min_value": 0, "wanted_count": 0})})
    monkeypatch.setattr(market, "get_config", lambda: always)
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "holo", copies=2)
    market.catch_up(db, T0)
    monkeypatch.setattr(market.time, "time", lambda: T0 + 10)
    offer = market.current_offer(db, u)
    assert offer and offer["media_id"] == m.id and offer["treatment"] == "holo"
    lo, hi = always.market.offer_range
    assert lo - 0.05 <= offer["price"] / offer["market_price"] <= hi + 0.05
    start = u.coins
    r = market.answer_offer(db, u, True)
    assert r["accepted"] and u.coins == start + offer["price"]
    assert copies(db) == {(m.id, "holo"): 1}
    with pytest.raises(market.NoOffer):
        market.answer_offer(db, u, True)  # one offer, one answer
    assert market.current_offer(db, u) is None


# ---------------------------------------------------------------- duplicates and fusing


def test_a_duplicate_from_a_pack_is_another_copy_and_pays_nothing(db, monkeypatch):
    u = local_user(db)
    m = media(db, 5)[0]
    own(db, u, m, "normal")
    credit(db, u, 1000, "test")
    db.commit()
    monkeypatch.setattr(pack_svc, "roll_pack", lambda *a, **k: [pack_rules.Pull(m.id, "normal", "duplicate")])
    coins_after_buy = None
    r = pack_svc.open_pack(db, u, "standard")
    coins_after_buy = r["coins"]
    assert copies(db) == {(m.id, "normal"): 2}
    assert not db.scalars(select(Transaction).where(Transaction.reason == "duplicate")).all()
    assert r["pulls"][0]["value"] == market.Prices(db, [m.id]).price(m.id, "normal")  # the reveal shows the value
    assert coins_after_buy == u.coins


def test_three_copies_fuse_into_one_of_the_next_treatment(db):
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal", copies=cfg.fuse.copies + 1)
    r = pack_svc.fuse(db, u, m.id, "normal")
    assert r["treatment"] == "foil" and r["used"] == cfg.fuse.copies
    assert copies(db) == {(m.id, "normal"): 1, (m.id, "foil"): 1}
    with pytest.raises(pack_svc.CannotFuse):
        pack_svc.fuse(db, u, m.id, "normal")  # one copy left
    with pytest.raises(pack_svc.CannotFuse):
        pack_svc.fuse(db, u, m.id, "foil")


def test_special_cannot_be_fused_further(db):
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "special", copies=5)
    with pytest.raises(pack_svc.CannotFuse):
        pack_svc.fuse(db, u, m.id, "special")


def test_card_payload_carries_market_data(db):
    from app.services.cards import card_out

    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal", copies=3)
    c = card_out(db, u, m)
    assert c["value"] >= 1 and c["print_run"] > 0 and c["print_tier"]
    assert c["copies_by_treatment"] == {"normal": 3} and c["fuse_from"] == "normal"
    assert c["sell_prices"]["normal"] == market.Prices(db, [m.id]).sell(m.id, "normal")


# ---------------------------------------------------------------- shop: deal, buy as many as you can


def test_the_deal_is_fixed_for_its_window_and_limited(db):
    u = local_user(db)
    media(db, 20)
    credit(db, u, 10_000, "test")
    db.commit()
    d = offers.deal(db, u, T0)
    assert d == offers.deal(db, u, T0 + 60)  # asking again can't re-roll it
    lo, hi = cfg.deal.discount
    assert lo <= d["discount"] <= hi and d["price"] < d["full_price"] and d["left"] == cfg.deal.per_window
    assert offers.deal(db, u, T0 + cfg.deal.hours * 3600)["window"] == d["window"] + 1


def test_buying_at_the_deal_price_uses_it_up(db, monkeypatch):
    u = local_user(db)
    media(db, 20)
    credit(db, u, 10_000, "test")
    db.commit()
    d = offers.deal(db, u)
    start = u.coins
    r = pack_svc.buy_packs(db, u, d["pack"], 1, use_deal=True)
    assert r["cost"] == d["price"] and u.coins == start - d["price"]
    assert offers.deal(db, u)["left"] == cfg.deal.per_window - 1
    other = next(p for p in cfg.packs if p != d["pack"])
    with pytest.raises(pack_svc.NoDeal):
        pack_svc.buy_packs(db, u, other, 1, use_deal=True)
    with pytest.raises(pack_svc.NoDeal):
        pack_svc.buy_packs(db, u, d["pack"], cfg.deal.per_window, use_deal=True)  # more than are left


def test_max_affordable_counts_the_rising_price(db):
    u = local_user(db)
    media(db, 5)
    n = pack_svc.max_affordable(db, u)["standard"]
    assert n == 5 or n == 4  # 500 welcome coins at 100 and rising
    pack_svc.buy_packs(db, u, "standard", n)
    assert pack_svc.max_affordable(db, u)["standard"] == 0
    assert u.coins >= 0


# ---------------------------------------------------------------- tags


def test_more_tags_keep_paying_a_coin_until_the_cap(db, monkeypatch):
    monkeypatch.setattr(cfg.tagging, "burst_tags", 0)  # 14 tags in a blink would start the tagging pause (v0.9.0)
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "holo")
    paid = [organize.add_tag(db, u, m.id, f"tag {i}")["coins_awarded"] for i in range(cfg.tagging.max_paid_tags + 2)]
    assert paid[0] == round(cfg.tagging.first_tag * cfg.treatments["holo"].multiplier)
    assert paid[len(cfg.tagging.extra_tags) + 1] == 1 and paid[cfg.tagging.max_paid_tags - 1] == 1
    assert paid[cfg.tagging.max_paid_tags:] == [0, 0]
    assert paid == sorted(paid, reverse=True)


def test_the_tag_rush_doubles_tags_once_a_day(db):
    u = local_user(db)
    a, b = media(db, 2)
    own(db, u, a, "normal")
    own(db, u, b, "normal")
    today = date(2026, 11, 14)
    assert offers.rush(db, u, T0, today)["available"] is True
    r = offers.start_rush(db, u, T0, today)
    assert r["active"] and not r["available"]
    with pytest.raises(offers.RushUnavailable):
        offers.start_rush(db, u, T0 + 5, today)
    assert offers.rush_active(db, u, T0 + 60) and not offers.rush_active(db, u, T0 + cfg.tagging.rush_minutes * 60 + 1)


def test_tag_suggestions_match_and_relate(client):
    with SessionLocal() as db:
        u = local_user(db)
        a, b, c = media(db, 3)
        for m in (a, b, c):
            own(db, u, m, "normal")
        organize.add_tag(db, u, a.id, "lake")
        organize.add_tag(db, u, a.id, "bay")
        organize.add_tag(db, u, b.id, "lake")
        organize.add_tag(db, u, b.id, "boat")
        organize.add_tag(db, u, c.id, "beach")
        ids = (a.id, b.id, c.id)
    r = client.get("/api/tags/suggest", params={"q": "b", "media_id": ids[2]}).json()
    assert [t["name"] for t in r["matches"]] == ["bay", "boat"]  # starts with "b", and not the card's own "beach"
    r = client.get("/api/tags/suggest", params={"media_id": ids[0]}).json()
    assert r["matches"] == [] and r["related"][0]["name"] == "boat"  # sits on a card that shares "lake"
    assert "lake" not in [t["name"] for t in r["related"]] and "bay" not in [t["name"] for t in r["related"]]
    r = client.get("/api/tags/suggest", params={"q": "LAK"}).json()
    assert r["matches"][0] == {"id": r["matches"][0]["id"], "name": "lake", "count": 2}


# ---------------------------------------------------------------- endpoints and reset


@pytest.mark.price_steps  # v0.8.0: only cards past the first fuse price step are listed
def test_market_endpoints(client):
    with SessionLocal() as db:
        u = local_user(db)
        items = media(db, 40)
        items[0].print_run, items[0].popularity = 60000, 1.0  # a cheap card: its fuse takes the base 3 copies
        for m in items[1:16]:
            m.print_run, m.popularity = 140, 1.0  # worth trading (and too scarce to be reprinted)
        own(db, u, items[0], "normal", copies=3)
        credit(db, u, 50_000, "test")
        db.commit()
        first = items[0].id
    m = client.get("/api/market").json()
    normal = m["listings"]["normal"]  # v0.8.0: one list per treatment
    assert len(normal) == cfg.market.listings_per_treatment and normal[0]["card"]["id"] == normal[0]["media_id"]
    assert m["index"] > 0 and m["next_tick_at"] and len(m["index_history"]) >= 2 and m["mood"]
    assert m["duplicates"]["copies"] == 2 and m["duplicates"]["value"] > 0
    assert m["collection_value"] > 0
    w = client.get("/api/economy/wallet").json()
    assert w["collection_value"] == m["collection_value"] and w["market"]["tick"] == m["tick"]
    assert w["pack_max"]["standard"] == 50 and w["deal"]["pack"] in cfg.packs and w["rush"]["available"] is True
    assert client.post("/api/market/buy", json={"media_id": normal[0]["media_id"]}).status_code == 200
    assert client.post("/api/market/buy", json={"media_id": 10**9}).status_code == 409
    assert client.post(f"/api/cards/{first}/fuse", json={"treatment": "normal"}).json()["treatment"] == "foil"
    assert client.post(f"/api/cards/{first}/fuse", json={"treatment": "normal"}).status_code == 409
    assert client.post("/api/economy/rush").json()["active"] is True
    assert client.post("/api/economy/rush").status_code == 409
    assert client.post("/api/market/sell-duplicates").json()["sold"] == 0
    assert client.get("/api/market/offer").status_code == 200
    d = w["deal"]
    r = client.post(f"/api/packs/{d['pack']}/buy", json={"count": 1, "deal": True})
    assert r.status_code == 200 and r.json()["cost"] == d["price"]


def test_reset_wipes_progress_and_keeps_the_library(client):
    with SessionLocal() as db:
        u = local_user(db)
        items = media(db, 12)
        own(db, u, items[0], "foil", copies=2)
        organize.add_tag(db, u, items[0].id, "keep me not")
        pack_svc.buy_packs(db, u, "standard", 2)
        db.merge(AppState(key="media_dir", value="D:/somewhere"))
        db.merge(AppState(key="onboarding_dismissed", value="1"))
        db.commit()
    client.put("/api/account", json={"name": "Jo"})
    assert client.post("/api/account/reset", json={"confirm": "nope"}).status_code == 422
    r = client.post("/api/account/reset", json={"confirm": "RESET"})
    assert r.status_code == 200 and r.json()["coins"] == cfg.starting_coins and r.json()["name"] == "Jo"
    with SessionLocal() as db:
        assert db.scalars(select(CollectionEntry)).all() == [] and db.scalars(select(Tag)).all() == []
        assert db.scalars(select(Pack)).all() == []
        assert len(db.scalars(select(MediaItem)).all()) == 12  # the library stays
        assert db.get(AppState, "media_dir").value == "D:/somewhere" and db.get(AppState, "onboarding_dismissed") is None
        assert [t.reason for t in db.scalars(select(Transaction))] == ["starting_grant"]
        assert all(m.print_run > 0 for m in db.scalars(select(MediaItem)))  # a fresh market was issued
    w = client.get("/api/economy/wallet").json()
    assert w["cards_owned"] == 0 and w["cards_total"] == 12 and w["inventory"]["standard"] == 0


def test_the_market_survives_two_requests_at_once(db):
    media(db, 30)
    errors = []

    def go():
        with SessionLocal() as s:
            try:
                market.catch_up(s, T0)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(db.scalars(select(MarketCard)).all()) == 30


def test_free_and_kept_duplicates_do_not_break_the_tag_reward_for_real_value():
    assert eco.tag_reward(cfg, 0, 1.0) == cfg.tagging.first_tag


def test_card_says_what_the_next_tag_pays(db, monkeypatch):
    """The Organize page shows the reward before the player types: the first tag, the list, then 1, then nothing."""
    from app.services.cards import card_out

    monkeypatch.setattr(cfg.tagging, "burst_tags", 0)  # 13 tags in a blink would start the tagging pause (v0.9.0)

    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal")
    for i in range(cfg.tagging.max_paid_tags + 1):
        promised = card_out(db, u, m)["next_tag_reward"]
        assert organize.add_tag(db, u, m.id, f"next {i}")["coins_awarded"] == promised
        assert promised == eco.tag_reward(cfg, i, 1.0)
    assert card_out(db, u, m)["next_tag_reward"] == 0


def test_the_scarcest_cards_are_only_in_the_dearer_packs(db):
    """Limited cards need a Deluxe pack or better, Chase cards a Premium pack."""
    tiers = {t.name: t for t in cfg.market.print_tiers}
    chase, limited, mass = tiers["Chase"].min, tiers["Limited"].min, tiers["Mass print"].min
    assert mk.min_pack(cfg, mass) is None and mk.min_pack(cfg, limited) == "deluxe" and mk.min_pack(cfg, chase) == "premium"
    assert [mk.pack_holds(cfg, p, chase) for p in ("standard", "deluxe", "premium")] == [False, False, True]
    assert [mk.pack_holds(cfg, p, limited) for p in ("standard", "deluxe", "premium")] == [False, True, True]
    assert all(mk.pack_holds(cfg, p, mass) for p in cfg.packs)

    u = local_user(db)
    items = media(db, 12)
    market.catch_up(db)
    for m in items:
        m.print_run = mass
    items[0].print_run = chase
    items[1].print_run = limited
    db.commit()
    assert market.not_in_pack(db, "standard") == {items[0].id, items[1].id}
    assert market.not_in_pack(db, "deluxe") == {items[0].id}
    assert market.not_in_pack(db, "premium") == set()
    ex = market.pack_exclusives(db, u)
    assert ex["deluxe"] == {"cards": 1, "missing": 1} and ex["premium"] == {"cards": 1, "missing": 1} and ex["standard"]["cards"] == 0

    credit(db, u, 100_000, "test")
    db.commit()
    rng = random.Random(4)
    pulled = set()
    for _ in range(40):
        pulled |= {p["media_id"] for p in pack_svc.open_pack(db, u, "standard", rng)["pulls"]}
    assert pulled and not pulled & {items[0].id, items[1].id}
    for _ in range(60):
        pulled |= {p["media_id"] for p in pack_svc.open_pack(db, u, "deluxe", rng)["pulls"]}
    assert items[0].id not in pulled
    from app.services.cards import card_out
    assert card_out(db, u, items[0])["min_pack"] == "premium" and card_out(db, u, items[2])["min_pack"] is None


def test_a_card_scanned_after_the_market_opened_has_a_price_history_too(db):
    """A first scan takes a while: the market opens with the first cards and the rest join later."""
    first = media(db, 3)
    market.catch_up(db)
    late = MediaItem(path="late.jpg", kind="image", sha256="late" * 16)
    db.add(late)
    db.commit()
    market.catch_up(db)
    h = market.card_history(db, late.id)
    assert len(h["points"]) == len(market.card_history(db, first[0].id)["points"]) == mk.HISTORY
    assert h["points"][-1]["price"] == h["value"]


def test_fusing_counts_for_the_weekly_upgrade_goal(db):
    from app.services import chase

    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal", copies=cfg.fuse.copies)
    today = date.today()
    before = chase.week_counters(db, u, today).upgrades
    pack_svc.fuse(db, u, m.id, "normal")
    assert chase.week_counters(db, u, today).upgrades == before + 1


# ---------------------------------------------------------------- v0.5.0: the collection, not "the market"


def test_the_collections_change_is_the_held_cards_at_the_last_two_ticks(db):
    import json as _json

    from app.services import portfolio

    u = local_user(db)
    a, b, c = media(db, 3)
    assert portfolio.change(db, u) == {"previous": 0, "change": 0.0, "coins": 0}  # no cards: nothing to say
    market.catch_up(db)
    rows = {r.media_id: r for r in db.scalars(select(MarketCard))}
    rows[a.id].history = _json.dumps([10, 10, 20])
    rows[b.id].history = _json.dumps([5, 5])          # a shorter history counts at its oldest price before that
    rows[c.id].history = _json.dumps([999, 999, 999])  # not owned: never counted
    db.commit()
    own(db, u, a, "normal", copies=2)
    own(db, u, b, "foil")
    foil = market.Prices(db, [b.id]).factor(b.id, "foil")  # v0.8.0: this card's own Foil premium
    assert 1 < foil <= cfg.market.treatment_value["foil"]
    hist = [round(20 + 5 * foil), round(20 + 5 * foil), round(40 + 5 * foil)]
    assert portfolio.value_history(db, u) == hist
    ch = portfolio.change(db, u)
    assert ch["previous"] == hist[1] and ch["coins"] == 20
    assert ch["change"] == round(hist[2] / hist[1] - 1, 4)


def test_wallet_and_market_carry_the_collections_change(client):
    w = client.get("/api/economy/wallet").json()
    assert set(w["collection_change"]) == {"previous", "change", "coins"}
    m = client.get("/api/market").json()
    assert "collection_history" in m and "collection_change" in m


def test_news_names_the_cards_it_is_about(db):
    from app.models import MarketNews
    from datetime import datetime, timezone

    u = local_user(db)
    a, b = media(db, 2)
    own(db, u, a, "normal")
    db.add(MarketNews(tick=1, kind="hype", text="A card is in demand: its price jumped.", media_ids=f"[{a.id}, {b.id}, 999]",
                      at=datetime(2026, 11, 14, tzinfo=timezone.utc)))
    db.commit()
    item = market.news(db)[0]
    assert [(c["id"], c["owned"]) for c in item["cards"]] == [(a.id, True), (b.id, False)]  # 999 is gone: left out
    assert all(c["title"] for c in item["cards"])
