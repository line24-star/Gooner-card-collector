"""v0.8.0, the server side: card numbers (PT4), listings per treatment with a per-card premium
(PT16; only cards worth trading are listed, and no way of buying and selling again pays), the
wanted minimum (PT9), one source for version changes (0.1), the scan preview and
result (6.1, 6.2), the offer's treatment (7.2) and the 30-day price table (PT15)."""

import hashlib
import itertools
import json
import math
import os
import random
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select, text

from app.config import settings
from app.db import SCHEMA_VERSION, SessionLocal, migrate
from app.game import economy as eco
from app.game import market as mk
from app.game import titles
from app.game.config import EconomyConfig, get_config, loop_return
from app.main import app
from app.models import CollectionEntry, MarketCard, MarketNews, MediaItem, Pack
from app.services import cards, market, rewards, scanner
from app.services import packs as pack_svc
from app.services.wallet import credit, local_user
from tests.test_chase import media, own
from tools import simulate as sim

cfg = get_config()
TIERS = [t.id for t in cfg.tiers()]


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        yield c


@pytest.fixture
def tmp_media(tmp_path, monkeypatch):
    d = tmp_path / "media"
    d.mkdir()
    monkeypatch.setattr(settings, "media_dir", d)
    return d


def make_png(p: Path, shade: int, *, age: float = 60.0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 20), (shade % 256, (shade * 7) % 256, (shade * 13) % 256)).save(p)
    t = time.time() - age - shade  # every file its own time, like real files
    os.utime(p, (t, t))
    return p


def copies(db):
    return {(e.media_id, e.treatment): e.copies for e in db.scalars(select(CollectionEntry)) if e.copies}


def worth_trading(db, items, print_run=140):
    """Give these cards a price past the first fuse price step (about 140 coins; too scarce to be reprinted), before the market opens."""
    for m in items:
        m.print_run, m.popularity = print_run, 1.0
    db.commit()


def flat_market(db):
    """Open the market and make it flat (index 1, every card at its fair price, now and at the
    last update), so prices are the same in every run."""
    state = market.catch_up(db)
    state["log_index"] = 0.0
    for row in db.scalars(select(MarketCard)):
        row.factor = 1.0
    db.flush()
    market._save_state(db, state)
    prices = market.Prices(db, state=state)
    for row in db.scalars(select(MarketCard)):
        row.history = json.dumps([prices.price(row.media_id)] * 2)
    db.commit()
    return state


def dear(db, m, print_run=40, popularity=1.0):
    """A scarce card: its prices are high enough for a 3% step to show after rounding."""
    m.print_run, m.popularity = print_run, popularity
    db.commit()
    market.catch_up(db)
    return market.Prices(db, [m.id]).price(m.id)


def changed(raw: dict, **market_values) -> dict:
    out = json.loads(json.dumps(raw))
    out["market"].update(market_values)
    return out


def raw_config() -> dict:
    return json.loads(cfg.model_dump_json())


# ---------------------------------------------------------------- PT4: a permanent number per card


V6_MEDIA = (
    "CREATE TABLE media_items (id INTEGER PRIMARY KEY, path TEXT, sha256 VARCHAR(64) UNIQUE, "
    "kind VARCHAR(8), treatment VARCHAR(16), ingested_at DATETIME, "
    "size_bytes INTEGER NOT NULL DEFAULT 0, width INTEGER, height INTEGER, format VARCHAR(16), "
    "duration_s REAL, mtime REAL NOT NULL DEFAULT 0, missing INTEGER NOT NULL DEFAULT 0, "
    "print_run INTEGER NOT NULL DEFAULT 0, popularity REAL NOT NULL DEFAULT 1.0, title VARCHAR(80))"
)


def test_the_migration_numbers_existing_cards_in_scan_order(tmp_path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'v6.db').as_posix()}")
    rows = [  # (id, path, ingested_at, missing): the ids are NOT in scan order
        (1, "late.jpg", "2026-03-01 10:00:00", 0),
        (2, "first.jpg", "2026-01-01 10:00:00", 0),
        (3, "gone.jpg", "2026-02-01 10:00:00", 1),
        (4, "same-time-a.jpg", "2026-02-15 10:00:00", 0),
        (5, "same-time-b.jpg", "2026-02-15 10:00:00", 0),
    ]
    with eng.begin() as c:
        c.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE, "
                       "password_hash VARCHAR(255), coins INTEGER, packs_opened INTEGER NOT NULL DEFAULT 0, "
                       "streak_days INTEGER NOT NULL DEFAULT 0, last_active_day VARCHAR(10), created_at DATETIME)"))
        c.execute(text(V6_MEDIA))
        for i, path, at, missing in rows:
            c.execute(text("INSERT INTO media_items (id, path, sha256, kind, treatment, ingested_at, missing) "
                           "VALUES (:i, :p, :s, 'image', 'normal', :at, :m)"),
                      {"i": i, "p": path, "s": f"sha{i}", "at": at, "m": missing})
        c.execute(text("CREATE TABLE app_state (key VARCHAR(64) PRIMARY KEY, value TEXT)"))
        c.execute(text("INSERT INTO app_state VALUES ('market', :v)"), {"v": json.dumps(
            {"seed": 7, "tick": 99, "wanted": [2], "offer": {"media_id": 2, "treatment": "holo", "price": 900, "tick": 99}})})
        c.execute(text("INSERT INTO app_state VALUES ('library_peak', '5')"))
        c.execute(text("PRAGMA user_version = 6"))
    assert migrate(eng) == SCHEMA_VERSION == 7
    with eng.begin() as c:
        got = dict(c.execute(text("SELECT path, number FROM media_items")).all())
        # an offer made at the old treatment prices is withdrawn; the rest of the market stays
        state = json.loads(c.execute(text("SELECT value FROM app_state WHERE key = 'market'")).scalar())
        assert state == {"seed": 7, "tick": 99, "wanted": [2]}
        assert c.execute(text("SELECT value FROM app_state WHERE key = 'library_peak'")).scalar() == "5"
    # ingested time first, then id; a missing file is numbered like any other
    assert got == {"first.jpg": 1, "gone.jpg": 2, "same-time-a.jpg": 3, "same-time-b.jpg": 4, "late.jpg": 5}
    assert any(ix["unique"] and ix["column_names"] == ["number"] for ix in inspect(eng).get_indexes("media_items"))
    assert migrate(eng) == SCHEMA_VERSION  # running it again changes nothing
    with eng.begin() as c:
        assert dict(c.execute(text("SELECT path, number FROM media_items")).all()) == got


def test_new_cards_get_the_next_number_and_keep_it(db):
    a, b, c = media(db, 3)  # stored in one go
    assert [a.number, b.number, c.number] == [1, 2, 3]
    b.missing = True  # a missing file keeps its number, and the number is not given out again
    db.commit()
    d = media_one(db, "later.jpg")
    assert d.number == 4 and db.get(MediaItem, b.id).number == 2
    e = MediaItem(path="own-number.jpg", sha256="x" * 64, kind="image", number=40)
    f = MediaItem(path="after.jpg", sha256="y" * 64, kind="image")
    db.add_all([e, f])
    db.commit()
    assert e.number == 40 and f.number == 41


def media_one(db, path):
    m = MediaItem(path=path, sha256=hashlib.sha256(path.encode()).hexdigest(), kind="image")
    db.add(m)
    db.commit()
    return m


def test_a_scan_numbers_the_cards_and_a_rescan_never_renumbers(tmp_media, db):
    for i in range(4):
        make_png(tmp_media / f"img_{i}.png", i)
    scanner.full_scan(tmp_media)
    first = {m.path: m.number for m in db.scalars(select(MediaItem))}
    assert sorted(first.values()) == [1, 2, 3, 4]
    (tmp_media / "img_1.png").unlink()
    make_png(tmp_media / "new.png", 9)
    scanner.full_scan(tmp_media)
    db.expire_all()
    after = {m.path: (m.number, m.missing) for m in db.scalars(select(MediaItem))}
    assert {p: n for p, (n, _) in after.items() if p in first} == first  # nobody was renumbered
    assert after["img_1.png"][1] is True and after["new.png"] == (5, False)


def test_the_payload_carries_number_and_code_and_names_no_longer_come_from_tags(db):
    u = local_user(db)
    readable = media_one(db, "sub/my-cat (1).jpg")
    machine = media_one(db, "GKtwUBnXcAAQeLp.jpg")
    from app.services import organize

    own(db, u, machine, "normal")
    organize.add_tag(db, u, machine.id, "Lake")
    out = {c["id"]: c for c in cards.card_out_many(db, u, [readable, machine])}
    assert (out[readable.id]["number"], out[readable.id]["code"], out[readable.id]["title"]) == (1, "#000001", "Card #000001")  # v0.9.0: not "My cat"
    assert (out[machine.id]["number"], out[machine.id]["code"]) == (2, "#000002")
    assert out[machine.id]["title"] == "Card #000002" and out[machine.id]["file_name"] == "GKtwUBnXcAAQeLp"
    machine.title = "Mine"
    db.commit()
    assert cards.card_out(db, u, machine)["title"] == "Mine" and cards.card_out(db, u, machine)["title_custom"] is True
    assert titles.display_title("IMG_0001", None, 123) == "Card #000123"


@pytest.mark.price_steps
def test_news_listings_and_offers_name_a_card_like_its_payload(client):
    with SessionLocal() as db:
        u = local_user(db)
        items = media(db, 3)  # c0.jpg ... : machine-like names? "c0" reads as a word, so rename one
        items[0].path = "GT4m8LPWIAAkddx.jpg"
        worth_trading(db, items[:1], 40)
        own(db, u, items[0], "normal")
        market.catch_up(db)
        db.add(MarketNews(tick=1, kind="hype", text="A card is in demand: its price jumped.",
                          media_ids=json.dumps([items[0].id])))
        db.commit()
        mid, number = items[0].id, items[0].number
    m = client.get("/api/market").json()
    named = m["news"][0]["cards"][0]
    assert named == {"id": mid, "title": f"Card #{number:06d}", "number": number, "code": f"#{number:06d}", "owned": True}
    row = next(r for r in m["listings"]["normal"] if r["media_id"] == mid)
    assert row["title"] == row["card"]["title"] == named["title"] and row["code"] == named["code"]


# ---------------------------------------------------------------- PT16: the premium of a rarer copy


def test_the_premium_is_fixed_per_card_inside_the_range_and_follows_demand():
    lo, hi = cfg.market.premium_range
    assert (lo, hi) == (0.8, 1.0)
    rng = random.Random(3)
    seen = []
    for _ in range(3000):
        pop, i = mk.roll_popularity(cfg, rng), rng.randrange(1, 10**6)
        k = mk.premium(cfg, pop, i)
        assert lo <= k <= hi and k == mk.premium(cfg, pop, i)
        seen.append(k)
    assert min(seen) < lo + 0.1 * (hi - lo) and max(seen) > hi - 0.1 * (hi - lo)  # the whole range is used
    # the same card id: a more popular card has the higher premium
    assert mk.premium(cfg, 0.5, 7) < mk.premium(cfg, 1.0, 7) < mk.premium(cfg, 2.5, 7)
    # the same popularity: the cards still differ
    assert len({round(mk.premium(cfg, 1.0, i), 6) for i in range(1, 50)}) > 40


def test_a_rarer_copy_costs_the_cards_own_multiple_of_its_normal_copy():
    assert cfg.market.treatment_value == {"normal": 1, "foil": 4, "holo": 14, "special": 50}
    bulk, loved = mk.CardState(11, 40, 0.4), mk.CardState(12, 40, 4.0)
    for card in (bulk, loved):
        normal = mk.price(cfg, card, 1.0)
        k = mk.premium(cfg, card.popularity, card.media_id)
        assert mk.treatment_factor(cfg, card, "normal") == 1.0
        for t in TIERS[1:]:
            f = mk.treatment_factor(cfg, card, t)
            assert f == pytest.approx(cfg.market.treatment_value[t] ** k)
            assert 1 < f <= cfg.market.treatment_value[t]  # never above the base multiple
            assert mk.price(cfg, card, 1.0, t) == pytest.approx(normal * f, rel=0.01)
    # a sought-after card carries more of the premium than a bulk card
    assert mk.treatment_factor(cfg, loved, "special") > 1.3 * mk.treatment_factor(cfg, bulk, "special")


# ---------------------------------------------------------------- PT16: only cards worth trading are traded


@pytest.mark.price_steps
def test_a_card_is_tradeable_from_the_first_fuse_price_step():
    first = cfg.fuse.price_steps[0]
    assert first == 20
    assert not mk.tradeable(cfg, first - 1) and mk.tradeable(cfg, first) and mk.tradeable(cfg, 5000)
    # exactly the cards whose fuse takes more than the base count
    for p in range(1, 700):
        assert mk.tradeable(cfg, p) == (eco.fuse_need(cfg, p) > cfg.fuse.copies)
    # the price right now counts too: your own sales can push it under the step
    assert not mk.tradeable(cfg, first + 5, first - 1) and mk.tradeable(cfg, first + 5, first)
    assert not mk.tradeable(cfg, first - 1, first + 5)


def test_without_price_steps_nothing_is_tradeable():
    assert cfg.fuse.price_steps == []  # the test default (conftest): every fuse takes the base count
    assert not mk.tradeable(cfg, 10**6)
    assert loop_return(cfg) == 0.0


@pytest.mark.price_steps
def test_cheap_cards_are_not_for_sale_and_a_list_can_be_short(db):
    u = local_user(db)
    items = media(db, 12)
    for m in items:
        m.print_run, m.popularity = 60000, 1.0  # bulk: about 5 coins
    db.commit()
    credit(db, u, 1_000_000, "test")
    db.commit()
    flat_market(db)
    lists = market.listings(db)
    assert lists == {t: [] for t in TIERS} and market.cheapest_listing(db) is None
    for t in TIERS:  # not even a Special of a bulk card: the card is not traded as a single
        with pytest.raises(market.NotListed):
            market.buy(db, u, items[0].id, t)
    # three cards worth trading: three rows per list
    worth_trading(db, items[:3])
    flat_market(db)
    lists = market.listings(db)
    assert all({r["media_id"] for r in rows} == {m.id for m in items[:3]} for rows in lists.values())
    prices = market.Prices(db)
    assert all(prices.tick_price(r["media_id"]) >= 20 for rows in lists.values() for r in rows)
    # your own sales push the live price under the step: the card leaves the lists until it recovers
    row = db.get(MarketCard, items[0].id)
    row.factor = row.factor * 19 / prices.price(items[0].id) * 0.99
    db.commit()
    assert market.Prices(db).price(items[0].id) < 20 <= market.Prices(db).tick_price(items[0].id)
    assert items[0].id not in {r["media_id"] for r in market.listings(db)["normal"]}
    with pytest.raises(market.NotListed):
        market.buy(db, u, items[0].id, "foil")


# ---------------------------------------------------------------- PT16: no loop pays


def random_card(rng: random.Random) -> tuple[mk.CardState, float]:
    card = mk.CardState(rng.randrange(1, 10**6), rng.choice([25, 60, 300, 2000, 9000, 40000, 60000]),
                        rng.uniform(*cfg.market.popularity_range), rng.uniform(*cfg.market.factor_range))
    return card, rng.uniform(*cfg.market.index_range)


def loop_result(c, card: mk.CardState, index: float, bought: dict[str, int], target: str) -> tuple[int, int]:
    """(cost, sale) in whole coins of one loop with the real price functions: buy `bought` copies
    (dearest first; a purchase moves the price by `buy_impact`), fuse up to one copy at `target`,
    sell it as a wanted card."""
    step = mk.CardState(card.media_id, card.print_run, card.popularity, card.factor, k=card.k)
    cost = 0
    for t in sorted(bought, key=lambda t: -c.treatments[t].tier):
        for _ in range(bought[t]):
            cost += mk.buy_price(c, mk.price(c, step, index, t))
            mk.nudge(c, step, sold=False)
    return cost, mk.sell_price(c, mk.price(c, step, index, target), wanted=True)


def loop_plans(c, target: str, need: int):
    """Every way to get one copy at `target` from bought copies of lower treatments, with `need`
    copies per fuse."""
    tiers = [t.id for t in c.tiers()]
    top = tiers.index(target)

    def plans(tier: int, wanted: int):
        if tier == 0:
            yield {tiers[0]: wanted}
            return
        for buy in range(wanted + 1):  # buy `buy` of them, fuse the rest from the tier below
            if buy == wanted:
                yield {tiers[tier]: buy}
                continue
            for below in plans(tier - 1, (wanted - buy) * need):
                yield {**below, **({tiers[tier]: buy} if buy else {})}

    yield from plans(top - 1, need)


@pytest.mark.price_steps
def test_no_loop_of_buying_fusing_and_selling_pays():
    """The hard rule of PT16, with the real price functions and whole coins. For random cards that
    can be bought (their Normal price has reached the first step), over the whole premium range and
    every market level: every plan of buying copies, fusing them with the card's own fuse count and
    selling the result as a wanted card loses coins."""
    lo, hi = cfg.market.premium_range
    rng = random.Random(8)
    checked = cards_seen = 0
    while cards_seen < 300:
        card, index = random_card(rng)
        normal = mk.price(cfg, card, index)
        if not mk.tradeable(cfg, normal, normal):
            continue  # not listed, not wanted: nothing to buy
        cards_seen += 1
        need = eco.fuse_need(cfg, normal)
        assert need >= cfg.fuse.copies + 1
        card.k = (lo, hi)[cards_seen % 4] if cards_seen % 4 < 2 else None  # the ends of the range, and the card's own
        for target in TIERS[1:]:
            for plan in loop_plans(cfg, target, need):
                cost, sale = loop_result(cfg, card, index, plan, target)
                assert sale < cost, (card, index, plan, target, cost, sale)
                checked += 1
    assert checked > 10_000


def whole(x: float, factor: float) -> int:
    """A price in whole coins, as mk.price rounds it."""
    return max(1, round(x * factor))


@pytest.mark.price_steps
def test_the_cheapest_card_that_can_be_bought_still_loses_in_whole_coins():
    """Right at the floor of 20 coins the margin is thinnest (4 × 23 = 92 for a Foil that sells for
    90 at most). Every Normal price from the step up, the ends of the premium range, and the
    fractions of a coin that round worst."""
    tv = cfg.market.treatment_value
    checked = 0
    for p in range(cfg.fuse.price_steps[0], 700):
        for d in (-0.5, -0.25, 0.0, 0.25, 0.49):
            x = p + d  # the price before rounding
            normal = whole(x, 1)
            if not mk.tradeable(cfg, normal, normal):
                continue
            n = eco.fuse_need(cfg, normal)
            for k in cfg.market.premium_range:
                for t, up in zip(TIERS, TIERS[1:]):
                    cost = n * mk.buy_price(cfg, whole(x, tv[t] ** k))
                    assert mk.sell_price(cfg, whole(x, tv[up] ** k), wanted=True) < cost, (x, k, t)
                    checked += 1
    assert checked > 15_000
    assert cfg.fuse.copies + 1 == 4


@pytest.mark.price_steps
def test_the_two_regimes_hold_over_the_whole_premium_range():
    """With `fuse.copies + 1` = 4 copies per fuse: a plain sale may reach 4 × 1.15 ÷ 0.85 = 5.41
    Normal-step ratios, a wanted sale 4 × 1.15 ÷ (0.85 × 1.3) = 4.16; the step ratio is 4 at most."""
    m = cfg.market
    need = cfg.fuse.copies + 1
    lo, hi = m.premium_range
    assert need * m.buy_markup / m.sell_rate == pytest.approx(5.41, abs=0.01)
    assert need * m.buy_markup / (m.sell_rate * m.wanted_bonus) == pytest.approx(4.16, abs=0.01)
    for step in range(101):
        k = lo + (hi - lo) * step / 100
        for a in range(len(TIERS)):
            for b in range(a + 1, len(TIERS)):
                ratio = (m.treatment_value[TIERS[b]] / m.treatment_value[TIERS[a]]) ** k
                copies_needed = need ** (b - a)
                assert m.sell_rate * ratio < copies_needed * m.buy_markup * 0.75  # plain: a quarter to spare
                assert m.sell_rate * m.wanted_bonus * ratio < copies_needed * m.buy_markup  # wanted
    assert loop_return(cfg, wanted=False) == pytest.approx(0.85 * 4 / 4.6)  # 0.74
    assert loop_return(cfg, wanted=True) == pytest.approx(0.85 * 1.3 * 4 / 4.6)  # 0.96
    assert loop_return(cfg, premium=lo) < loop_return(cfg) < 1


@pytest.mark.price_steps
def test_the_config_refuses_values_that_would_let_a_loop_pay():
    raw = raw_config()
    EconomyConfig.model_validate(raw)  # the live numbers pass

    def refused(match, fuse=None, **market_values):
        bad = changed(raw, **market_values)
        if fuse is not None:
            bad["fuse"].update(fuse)
        with pytest.raises(ValidationError, match=match):
            EconomyConfig.model_validate(bad)

    refused("as a wanted card would pay", premium_range=[0.8, 1.1])  # a Foil above 4 Normal copies
    refused("as a wanted card would pay", treatment_value={"normal": 1, "foil": 4.5, "holo": 14, "special": 50})
    refused("would pay", treatment_value={"normal": 1, "foil": 4, "holo": 14, "special": 300})  # even a plain sale
    refused("would pay", fuse={"copies": 2})  # a listed card would fuse with 3 copies
    # a purchase that raises the price lifts what the fused copy sells for: the old +3% pays
    refused("would pay", buy_impact=0.03, offer_range=[0.9, 1.1])
    refused("offer_range", buy_impact=0.03)  # and it lifts an offer above what the copy cost
    refused("buy_markup", wanted_bonus=1.4)
    # a collector's offer must stay under what a listed copy costs
    refused("offer_range", offer_range=[0.9, 1.25])
    refused("offer_range", offer_range=[0.9, 1.15])
    refused("offer_range", offer_range=[1.1, 1.0])
    EconomyConfig.model_validate(changed(raw, offer_range=[0.9, 1.14]))
    refused("rise with", treatment_value={"normal": 1, "foil": 15, "holo": 14, "special": 50})
    refused("premium_range", premium_range=[0.9, 0.5])
    # without price steps no card is listed, so there is no loop to refuse
    open_ = changed(raw, premium_range=[1.0, 1.0])
    open_["fuse"]["price_steps"] = []
    EconomyConfig.model_validate(open_)


def test_buying_and_selling_the_same_copy_always_loses():
    """Whole coins, any price: buy at the markup, sell with the wanted bonus. A purchase no longer
    moves the price (v0.8.0), so the rounding that let a copy under 47 coins break even is gone."""
    assert cfg.market.buy_impact == 0
    for p in range(1, 20_000):
        assert mk.sell_price(cfg, p, wanted=True) < mk.buy_price(cfg, p)
    card = mk.CardState(1, 300)
    before = card.factor
    mk.nudge(cfg, card, sold=False)
    assert card.factor == before


@pytest.mark.price_steps
def test_a_price_that_falls_under_a_step_between_buying_and_fusing_does_not_pay():
    """The edge across a price update. Copies are bought while the fuse takes `n` copies; at the
    next update the Normal price is under that step, so the fuse takes `n - 1`. The copies were
    bought at or above the step and the fused copy is priced under it, so it still loses: under
    the first step the card is not wanted (plain sale, 0.85 × 4 < 3 × 1.15), above it a wanted
    sale pays 1.105 × 4 = 4.42 against 4 × 1.15 = 4.6 or more. Every pair of prices where the
    second is not above the first, whole coins, rounding at its worst.

    (A price that ROSE between the updates is no loop: the copies were bought cheaper than they
    are worth now, which is what any holder of the card gained. It cannot be made to happen:
    a purchase does not move the price.)"""
    tv = cfg.market.treatment_value
    steps = cfg.fuse.price_steps
    checked = 0
    for p in range(steps[0], steps[-1] + 60, 3):  # the Normal price the copies were bought at
        for q in range(1, p + 1):  # the Normal price at the next update: not higher
            xp, xq = p - 0.5, min(q + 0.49, p - 0.5)  # bought as cheap, fused as dear as rounding allows
            bought, now = whole(xp, 1), whole(xq, 1)
            if not mk.tradeable(cfg, bought, bought):
                continue
            need_now = eco.fuse_need(cfg, now)
            assert need_now <= eco.fuse_need(cfg, bought)
            wanted = mk.tradeable(cfg, now)  # under the first step nobody wants the card
            for k in cfg.market.premium_range:
                for t, up in zip(TIERS, TIERS[1:]):
                    cost = need_now * mk.buy_price(cfg, whole(xp, tv[t] ** k))
                    assert mk.sell_price(cfg, whole(xq, tv[up] ** k), wanted=wanted) < cost, (p, q, k, t)
                    checked += 1
    assert checked > 100_000


@pytest.mark.price_steps
def test_the_same_edge_through_the_services(db):
    """Buy four Normal copies of a card just above the first step, let the next update put its
    price under the step, fuse three and sell everything: fewer coins than before."""
    u = local_user(db)
    items = media(db, 400)  # 1,200 slots: no milestone from one version
    card = items[0]
    for m in items:
        m.print_run, m.popularity = 60000, 1.0
    card.print_run = 5000  # about 19 coins at a flat market
    credit(db, u, 1_000_000, "test")
    db.commit()
    state = market.catch_up(db)
    row = db.get(MarketCard, card.id)
    live = market.Prices(db, [card.id], state).price(card.id)
    row.factor = row.factor * 24 / live  # 24 coins now and at the last update: the fuse takes 4
    db.flush()
    price = market.Prices(db, [card.id], state).price(card.id)
    row.history = json.dumps([price, price])
    db.commit()
    assert 20 <= price < 30 and market.Prices(db, [card.id]).fuse_need(card.id) == 4
    start = u.coins
    for _ in range(4):
        market.buy(db, u, card.id)
    assert market.Prices(db, [card.id]).price(card.id) == price  # buying did not move it
    # the next update: the price is under the step (the highest it can be there)
    row = db.get(MarketCard, card.id)
    row.factor = row.factor * 19 / price
    db.flush()
    under = market.Prices(db, [card.id]).price(card.id)
    row.history = json.dumps([price, under])
    state = market.catch_up(db)
    state["wanted"] = market._pick_wanted(db, cfg, state, market._cards(db)[0])
    market._save_state(db, state)
    db.commit()
    assert under < 20 and market.Prices(db, [card.id]).fuse_need(card.id) == 3
    assert card.id not in state["wanted"]  # not wanted under the step
    with pytest.raises(market.NotListed):  # and no longer for sale: the edge cannot be fed
        market.buy(db, u, card.id)
    assert pack_svc.fuse(db, u, card.id, "normal")["used"] == 3
    market.sell(db, u, card.id, "foil")
    market.sell(db, u, card.id, "normal")
    assert copies(db) == {} and u.coins < start


@pytest.mark.price_steps
def test_buying_and_selling_a_listing_loses_coins_at_every_treatment_even_when_wanted(db):
    u = local_user(db)
    items = media(db, 400)  # 1,200 slots: one version stays under the first milestone
    top = items[0]
    dear(db, top, 25, 4.0)  # the dearest card: listed in every treatment
    own(db, u, top, "normal")
    credit(db, u, 100_000_000, "test")
    db.commit()
    state = market.catch_up(db)
    state["wanted"] = [top.id]
    market._save_state(db, state)
    db.commit()
    for t in TIERS:
        for _ in range(5):
            before = u.coins
            bought = market.buy(db, u, top.id, t)
            sold = market.sell(db, u, top.id, t)
            assert bought["treatment"] == t and sold["price"] < bought["price"] and u.coins < before


@pytest.mark.price_steps
def test_bought_copies_fused_and_sold_lose_coins_in_the_running_market(db):
    """The loop through the services: buy the copies a fuse needs, fuse, sell the result wanted."""
    u = local_user(db)
    items = media(db, 3)
    card = items[0]
    dear(db, card, 140)  # about 140 coins: for sale, and its fuse takes 5 or 6 copies
    credit(db, u, 10_000_000, "test")
    db.commit()
    state = market.catch_up(db)
    state["wanted"] = [card.id]
    market._save_state(db, state)
    db.commit()
    for t, up in zip(TIERS, TIERS[1:]):
        start = u.coins
        need = market.Prices(db, [card.id]).fuse_need(card.id)
        assert need > cfg.fuse.copies
        # v0.9.0: milestones pay no coins (they become claimable, as packs), so the coins are the whole story
        for _ in range(need):
            market.buy(db, u, card.id, t)
        fused = pack_svc.fuse(db, u, card.id, t)
        assert fused["treatment"] == up and fused["used"] == need
        assert market.Prices(db, [card.id]).info(card.id)["wanted"] is True
        market.sell(db, u, card.id, up)
        assert u.coins < start, (t, up)
    assert copies(db) == {}


def test_an_offer_never_reaches_what_a_listed_copy_costs():
    m = cfg.market
    assert m.offer_range == (0.9, 1.12) and m.offer_range[1] < m.buy_markup
    rng = random.Random(2)
    always = cfg.model_copy(update={"market": m.model_copy(update={"offer_chance": 1.0})})
    for _ in range(3000):
        price = rng.randrange(20, 5000)
        hit = mk.roll_offer(always, [(1, "normal", price)], rng)
        if hit is not None:
            assert hit[2] < mk.buy_price(cfg, price)


# ---------------------------------------------------------------- PT16: the listings


@pytest.mark.price_steps
def test_the_market_lists_the_ten_most_valuable_cards_of_each_treatment(db):
    worth_trading(db, media(db, 80)[:20])
    market.catch_up(db)
    prices = market.Prices(db)
    lists = market.listings(db, prices)
    assert list(lists) == TIERS
    listable = [i for i in prices.cards if mk.tradeable(cfg, prices.tick_price(i), prices.price(i))]
    assert 20 <= len(listable) < 80
    for t, rows in lists.items():
        assert len(rows) == cfg.market.listings_per_treatment == 10
        worth = sorted((prices.price(i, t) for i in listable), reverse=True)
        assert [r["value"] for r in rows] == worth[:10]  # the most valuable at THIS treatment
        assert all(r["treatment"] == t and r["price"] == mk.buy_price(cfg, r["value"]) for r in rows)
        assert all(prices.fuse_need(r["media_id"]) > cfg.fuse.copies for r in rows)  # a listed card takes 4 or more
    # a rarer copy costs more, and by a different factor per card
    by_card = {r["media_id"]: r["value"] for r in lists["normal"]}
    ratios = {round(r["value"] / by_card[r["media_id"]], 2) for r in lists["special"] if r["media_id"] in by_card}
    assert len(ratios) > 1 and all(20 < x <= cfg.market.treatment_value["special"] + 0.5 for x in ratios)
    assert market.cheapest_listing(db) == min(r["price"] for rows in lists.values() for r in rows)


@pytest.mark.price_steps
def test_a_cards_own_premium_changes_who_is_listed(db):
    """Two cards with the same Normal price: the one with the higher premium is the dearer Special."""
    items = media(db, 400)
    ks = {m.id: mk.premium(cfg, 1.0, m.id) for m in items}
    low, high = min(ks, key=ks.get), max(ks, key=ks.get)
    for m in items:
        m.print_run, m.popularity = (40, 1.0) if m.id in (low, high) else (60000, 1.0)
    db.commit()
    market.catch_up(db)
    for row in db.scalars(select(MarketCard)):
        row.factor = 1.0
    db.commit()
    prices = market.Prices(db)
    assert prices.price(low) == prices.price(high)
    assert prices.price(high, "special") > 1.3 * prices.price(low, "special")
    lists = market.listings(db, prices)
    assert [r["media_id"] for r in lists["special"]][:2] == [high, low]


@pytest.mark.price_steps
def test_buying_a_listing_gives_a_copy_of_that_treatment_and_counts_like_a_pull(db):
    u = local_user(db)
    items = media(db, 30)  # 90 slots: one version is 1.1%, past the first milestone
    worth_trading(db, items[:12])
    credit(db, u, 10_000_000, "test")
    db.commit()
    lists = market.listings(db)
    foil = lists["foil"][0]
    own(db, u, db.get(MediaItem, foil["media_id"]), "normal")
    start = u.coins
    r = market.buy(db, u, foil["media_id"], "foil")
    assert r["price"] == foil["price"] and u.coins == start - foil["price"]  # v0.9.0: a milestone adds no coins
    assert r["treatment"] == "foil" and r["copies"] == 1
    assert copies(db) == {(foil["media_id"], "normal"): 1, (foil["media_id"], "foil"): 1}
    # it filled the version slot, and the milestone that follows became claimable, as for a pulled copy
    assert r["slots"]["delta"] == 1 and r["slots"]["filled_now"] == [{"media_id": foil["media_id"], "treatment": "foil"}]
    assert (r["slots"]["filled"], r["slots"]["total"]) == rewards.slots(db, u) == (1, 90)
    assert r["milestones"] == [{"at": cfg.milestones[0].at, "reward": eco.pack_list(cfg, [cfg.milestones[0].reward])}]
    # a second copy of the same treatment: still listed, nothing new filled
    again = market.buy(db, u, foil["media_id"], "foil")
    assert again["copies"] == 2 and again["slots"]["delta"] == 0 and again["milestones"] == []
    assert again["price"] == r["price"]  # a purchase leaves the price alone
    # a Normal copy fills no slot
    n = market.buy(db, u, lists["normal"][0]["media_id"])
    assert n["treatment"] == "normal" and n["slots"]["delta"] == 0
    # listed means: listed at that treatment
    listed = {row["media_id"] for row in market.listings(db)["special"]}
    other = next(m.id for m in items if m.id not in listed)
    with pytest.raises(market.NotListed):
        market.buy(db, u, other, "special")
    with pytest.raises(market.NotListed):
        market.buy(db, u, foil["media_id"], "gold")


@pytest.mark.price_steps
def test_the_market_endpoint_groups_the_listings_by_treatment(client):
    with SessionLocal() as db:
        u = local_user(db)
        worth_trading(db, media(db, 40)[:15])
        credit(db, u, 10_000_000, "test")
        db.commit()
        top = market.listings(db)["holo"][0]["media_id"]
        own(db, u, db.get(MediaItem, top), "holo", copies=2)
    m = client.get("/api/market").json()
    assert list(m["listings"]) == TIERS and m["listings_per_treatment"] == 10
    for t in TIERS:
        rows = m["listings"][t]
        assert len(rows) == 10
        for r in rows:
            assert {"media_id", "treatment", "price", "value", "number", "code", "title", "thumb_url", "copies",
                    "fills_slot", "card", "print_run", "print_tier", "trend", "wanted"} <= set(r)
            assert r["treatment"] == t and r["card"]["id"] == r["media_id"] and r["code"] == f"#{r['number']:06d}"
            assert r["value"] == r["card"]["prices"][t]
    held = next(r for r in m["listings"]["holo"] if r["media_id"] == top)
    assert held["copies"] == 2 and held["fills_slot"] is False  # held already, and still listed
    assert next(r for r in m["listings"]["foil"] if r["media_id"] == top)["fills_slot"] is True
    assert all(r["fills_slot"] is False for r in m["listings"]["normal"])  # Normal is not a slot
    special = m["listings"]["special"][0]
    r = client.post("/api/market/buy", json={"media_id": special["media_id"], "treatment": "special"})
    assert r.status_code == 200 and r.json()["price"] == special["price"] and r.json()["slots"]["delta"] == 1
    assert client.post("/api/market/buy", json={"media_id": 10**9, "treatment": "foil"}).status_code == 409
    assert client.post("/api/market/buy", json={"media_id": special["media_id"], "treatment": "nope"}).status_code == 409
    c = client.get("/api/economy/config").json()["market"]
    assert c["listings_per_treatment"] == 10 and c["wanted_min_value"] == cfg.market.wanted_min_value


@pytest.mark.price_steps
def test_the_simulator_prices_treatments_like_the_app():
    s = sim.Sim(cfg, 120, sim.ARCHETYPES["regular"], "seller", seed=2, new_media=0, days=1)
    for i in (0, 17, 119):
        state = s.book.states[i]
        for tier, t in enumerate(TIERS):
            assert s.price(i, tier) == mk.price(cfg, state, s.index, t)
        k = mk.premium(cfg, state.popularity, state.media_id)
        assert s.price(i, 3) == pytest.approx(s.price(i, 0) * cfg.market.treatment_value["special"] ** k, rel=0.05, abs=2)
    s.run(3)
    held = {i: max(s.price(i, t) for t, n in enumerate(s.book.cards[i].owned) if n) for i in s.owned_ids}
    assert all(held[i] >= cfg.market.wanted_min_value and mk.tradeable(cfg, s.price(i, 0)) for i in s.wanted)


# ---------------------------------------------------------------- PT9: wanted only when it is worth something


def test_only_cards_worth_the_minimum_are_wanted():
    m = cfg.market
    assert m.wanted_min_value == 20
    held = {1: 5, 2: 19, 3: 20, 4: 300}
    for seed in range(20):
        assert set(mk.pick_wanted(cfg, held, random.Random(seed))) == {3, 4}  # fewer qualify: fewer are wanted
    # and only a card that is traded as a single: a dear copy of a bulk card is not wanted
    live = cfg.model_copy(update={"fuse": cfg.fuse.model_copy(update={"price_steps": [20, 60, 180, 540]})})
    normal = {1: 5, 2: 19, 3: 20, 4: 6}
    for seed in range(20):
        assert mk.pick_wanted(live, held, random.Random(seed), normal) == [3]
    many = {i: 100 + i for i in range(50)}
    picked = mk.pick_wanted(cfg, many, random.Random(1))
    assert len(picked) == m.wanted_count == len(set(picked))
    assert mk.pick_wanted(cfg, {1: 5}, random.Random(1)) == []


@pytest.mark.price_steps
def test_the_market_wants_no_cheap_card_and_the_payload_says_what_the_bonus_is(db):
    u = local_user(db)
    items = media(db, 6)
    cheap, good, foiled = items[0], items[1], items[2]
    for m in items:
        m.print_run, m.popularity = 60000, 1.0  # a few coins each
    good.print_run = 40  # worth hundreds
    db.commit()
    own(db, u, cheap, "normal", copies=2)
    own(db, u, good, "normal")
    own(db, u, foiled, "special")  # a cheap card whose copy is worth the minimum: still not traded as a single
    state = market.catch_up(db)
    # a flat market, so the prices are the same in every run: 5 coins for a mass print
    state["log_index"] = 0.0
    for row in db.scalars(select(MarketCard)):
        row.factor = 1.0
    db.flush()
    state["wanted"] = market._pick_wanted(db, cfg, state, market._cards(db)[0])
    market._save_state(db, state)
    db.commit()
    prices = market.Prices(db, state=state)
    assert prices.price(cheap.id) < cfg.market.wanted_min_value <= prices.price(foiled.id, "special")
    assert set(state["wanted"]) == {good.id}
    out = {c["id"]: c for c in cards.card_out_many(db, u, [cheap, good, foiled])}
    assert out[cheap.id]["wanted"] is False and out[cheap.id]["plain_prices"] == out[cheap.id]["sell_prices"]
    w = out[good.id]
    assert w["wanted"] is True
    plain, now = w["plain_prices"]["normal"], w["sell_prices"]["normal"]
    assert plain == mk.sell_price(cfg, w["prices"]["normal"]) and now == mk.sell_price(cfg, w["prices"]["normal"], True)
    assert now > plain and now - plain == pytest.approx(plain * (cfg.market.wanted_bonus - 1), abs=2)
    assert out[foiled.id]["wanted"] is False and set(out[foiled.id]["plain_prices"]) == {"special"}


# ---------------------------------------------------------------- 0.1: the net change of a fuse


def test_fuse_net_versions_last_copies_into_a_treatment_already_held():
    assert eco.fuse_net_versions(cfg, {"foil": 3, "holo": 1}, "foil", 3) == -1


def test_fuse_net_versions_last_copies_into_a_treatment_not_yet_held():
    assert eco.fuse_net_versions(cfg, {"foil": 3}, "foil", 3) == 0
    assert eco.fuse_net_versions(cfg, {"holo": 4, "normal": 2}, "holo", 4) == 0


def test_fuse_net_versions_a_copy_stays_and_the_higher_one_is_new():
    assert eco.fuse_net_versions(cfg, {"foil": 4}, "foil", 3) == 1


def test_fuse_net_versions_a_copy_stays_and_the_higher_one_is_held():
    assert eco.fuse_net_versions(cfg, {"foil": 5, "holo": 2}, "foil", 3) == 0


def test_fuse_net_versions_from_normal():
    assert eco.fuse_net_versions(cfg, {"normal": 3}, "normal", 3) == 1  # Normal is not a slot
    assert eco.fuse_net_versions(cfg, {"normal": 7, "foil": 1}, "normal", 3) == 0  # the Foil is already held
    assert eco.fuse_net_versions(cfg, {"special": 9}, "special", 3) == 0  # nothing above Special


def test_a_sale_empties_a_slot_only_with_the_last_copy_above_normal():
    assert eco.sale_empties_slot(cfg, "foil", 1) is True
    assert eco.sale_empties_slot(cfg, "foil", 2) is False and eco.sale_empties_slot(cfg, "foil", 2, 2) is True
    assert eco.sale_empties_slot(cfg, "normal", 1) is False
    assert eco.sale_empties_slot(cfg, "holo", 0) is False


# ---------------------------------------------------------------- 0.1: slots in every reply


def test_the_fuse_reply_carries_the_real_change_also_a_loss(db):
    u = local_user(db)
    a, b = media(db, 2)
    own(db, u, a, "foil", copies=3)
    own(db, u, a, "holo")
    own(db, u, b, "normal", copies=4)
    card = cards.card_out(db, u, a)
    assert card["fuse_options"] == [{
        "from": "foil", "to": "holo", "copies": 3, "have": 3, "can": True, "net_versions": -1,
        "empties": True, "fills": False, "value": card["prices"]["foil"] * 3,
    }, {
        "from": "holo", "to": "special", "copies": 3, "have": 1, "can": False, "net_versions": 0,
        "empties": True, "fills": True, "value": card["prices"]["holo"] * 3,
    }]
    r = pack_svc.fuse(db, u, a.id, "foil")
    assert (r["from"], r["used"], r["treatment"], r["net_versions"]) == ("foil", 3, "holo", -1)
    assert r["slots"] == {"filled": 1, "total": 6, "delta": -1, "filled_now": [],
                          "emptied": [{"media_id": a.id, "treatment": "foil"}], "total_delta": 0}
    other = cards.card_out(db, u, b)["fuse_options"]
    assert [(o["from"], o["to"], o["have"], o["can"], o["net_versions"]) for o in other] == [("normal", "foil", 4, True, 1)]
    r = pack_svc.fuse(db, u, b.id, "normal")
    assert r["net_versions"] == 1 and r["slots"]["filled_now"] == [{"media_id": b.id, "treatment": "foil"}]
    assert (r["slots"]["filled"], r["slots"]["total"]) == rewards.slots(db, u) == (2, 6)


def test_a_sale_says_which_slot_it_emptied(db):
    u = local_user(db)
    a, b = media(db, 2)
    own(db, u, a, "foil", copies=2)
    own(db, u, a, "normal", copies=3)
    own(db, u, b, "holo")
    assert market.sell(db, u, a.id, "foil")["slots"]["delta"] == 0  # a copy stays
    r = market.sell(db, u, a.id, "foil")
    assert r["slots"] == {"filled": 1, "total": 6, "delta": -1, "filled_now": [],
                          "emptied": [{"media_id": a.id, "treatment": "foil"}], "total_delta": 0}
    assert market.sell(db, u, a.id, "normal", 3)["slots"]["delta"] == 0  # Normal is not a slot
    d = market.sell_duplicates(db, u)
    assert d["slots"] == {"filled": 1, "total": 6, "delta": 0, "filled_now": [], "emptied": [], "total_delta": 0}


def test_the_card_previews_a_sale_of_any_number_of_copies(db):
    u = local_user(db)
    m = media(db, 3)[0]
    dear(db, m, 40)
    own(db, u, m, "foil", copies=4)
    own(db, u, m, "normal")
    card = cards.card_out(db, u, m)
    quotes = card["sale_quotes"]
    assert set(quotes) == {"foil", "normal"} and len(quotes["foil"]) == 4 and len(quotes["normal"]) == 1
    assert quotes["foil"][0] == card["sell_prices"]["foil"] and quotes["foil"][2] == card["sell_extras"]["foil"]
    # every copy pays the same: n copies pay n × the price of one (a sale does not move the price)
    assert quotes["foil"] == [n * card["sell_prices"]["foil"] for n in range(1, 5)]
    start = u.coins
    assert market.sell(db, u, m.id, "foil", 3)["price"] == quotes["foil"][2] == u.coins - start  # the preview is the sale
    assert set(card["prices"]) == set(TIERS) and card["prices"]["foil"] > card["prices"]["normal"]


def test_every_pull_says_what_it_did_to_the_versions_and_agrees_with_its_outcome(db):
    u = local_user(db)
    items = media(db, 12)
    credit(db, u, 10_000_000, "test")
    for m in items[:4]:  # some cards are held already, in different treatments
        own(db, u, m, "foil")
    own(db, u, items[0], "normal")
    db.commit()
    rng = random.Random(21)
    seen = {"fills": 0, "already": 0}
    for _ in range(60):
        held = copies(db)
        filled_before = rewards.slots(db, u)[0]
        r = pack_svc.open_pack(db, u, "premium", rng)
        slots = r["slots"]
        for p in r["pulls"]:
            t, mid = p["treatment"], p["media_id"]
            had = held.get((mid, t), 0) > 0
            assert p["fills_slot"] == (t != "normal" and not had)
            higher_empty = any(held.get((mid, x), 0) == 0 for x in TIERS[TIERS.index(t) + 1:])
            assert p["already_owned"] == (had and higher_empty)
            # how the two go with `outcome`
            assert not (p["fills_slot"] and p["already_owned"])
            if p["fills_slot"]:
                assert p["outcome"] in ("new", "better", "version")
            if p["already_owned"]:
                assert p["outcome"] == "duplicate"
            if p["outcome"] == "duplicate":
                assert not p["fills_slot"]
            if p["outcome"] in ("better",) or (p["outcome"] in ("new", "version") and t != "normal"):
                assert p["fills_slot"]
            held[(mid, t)] = held.get((mid, t), 0) + 1
            seen["fills"] += p["fills_slot"]
            seen["already"] += p["already_owned"]
        fills = [{"media_id": p["media_id"], "treatment": p["treatment"]} for p in r["pulls"] if p["fills_slot"]]
        key = lambda x: (x["media_id"], TIERS.index(x["treatment"]))  # noqa: E731
        assert sorted(slots["filled_now"], key=key) == sorted(fills, key=key)
        assert slots["delta"] == len(fills) and slots["emptied"] == []
        assert slots["filled"] == filled_before + len(fills) == rewards.slots(db, u)[0]
        assert slots["total"] == 36
    assert seen["fills"] > 5 and seen["already"] > 20


def test_a_replayed_pack_keeps_its_flags_and_changes_nothing(db):
    u = local_user(db)
    media(db, 8)
    credit(db, u, 100_000, "test")
    db.commit()
    first = pack_svc.open_pack(db, u, "premium", random.Random(5), idem_key="k1")
    again = pack_svc.open_pack(db, u, "premium", random.Random(6), idem_key="k1")
    assert again["replayed"] is True
    assert [(p["media_id"], p["treatment"], p["fills_slot"], p["already_owned"]) for p in again["pulls"]] == \
           [(p["media_id"], p["treatment"], p["fills_slot"], p["already_owned"]) for p in first["pulls"]]
    assert again["slots"]["delta"] == 0 and again["slots"]["filled_now"] == []
    assert again["slots"]["filled"] == first["slots"]["filled"]
    # a pack stored before v0.8.0 has no flags: they read as false
    old = db.scalars(select(Pack).where(Pack.idem_key == "k1")).one()
    stored = json.loads(old.contents_json)
    for p in stored["pulls"]:
        p.pop("fills_slot"), p.pop("already_owned")
    old.contents_json = json.dumps(stored)
    db.commit()
    replay = pack_svc.open_pack(db, u, "premium", idem_key="k1")
    assert all(p["fills_slot"] is False and p["already_owned"] is False for p in replay["pulls"])


def test_the_endpoints_carry_slots(client):
    with SessionLocal() as db:
        u = local_user(db)
        items = media(db, 5)
        own(db, u, items[0], "foil")
        own(db, u, items[1], "normal", copies=3)
        credit(db, u, 100_000, "test")
        db.commit()
        a, b = items[0].id, items[1].id
    r = client.post("/api/packs/standard/open?buy=true").json()
    assert {"filled", "total", "delta", "filled_now", "emptied", "total_delta"} == set(r["slots"])
    assert all({"fills_slot", "already_owned", "outcome"} <= set(p) for p in r["pulls"])
    before = client.get("/api/economy/wallet").json()
    card = client.get(f"/api/media/{b}").json()
    if card["fuse_options"] and card["fuse_options"][0]["can"]:
        f = client.post(f"/api/cards/{b}/fuse", json={"treatment": "normal"}).json()
        assert f["net_versions"] == f["slots"]["delta"] == card["fuse_options"][0]["net_versions"]
        assert f["slots"]["filled"] == before["slots_filled"] + f["slots"]["delta"]
    s = client.post(f"/api/cards/{a}/sell", json={"treatment": "foil", "count": 9}).json()
    assert s["slots"]["emptied"] == [{"media_id": a, "treatment": "foil"}] and s["slots"]["delta"] == -1
    w = client.get("/api/economy/wallet").json()
    assert (w["slots_filled"], w["slots_total"]) == (s["slots"]["filled"], s["slots"]["total"])
    assert set(client.post("/api/market/sell-duplicates").json()["slots"]) == set(s["slots"])


# ---------------------------------------------------------------- 7.2: the offer names its treatment


def offer_config(monkeypatch, **changes):
    always = cfg.model_copy(update={"market": cfg.market.model_copy(
        update={"offer_chance": 1.0, "offer_min_value": 0, "wanted_count": 0, **changes})})
    monkeypatch.setattr(market, "get_config", lambda: always)
    return always


def test_the_offer_says_its_treatment_the_copies_held_and_whether_a_slot_empties(db, client, monkeypatch):
    always = offer_config(monkeypatch)
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "holo", copies=2)
    state = market.catch_up(db)
    offer = market.current_offer(db, u, state)
    assert (offer["treatment"], offer["copies"], offer["empties_slot"]) == ("holo", 2, False)
    r = market.answer_offer(db, u, True)
    assert r["accepted"] and (r["media_id"], r["treatment"]) == (m.id, "holo") and r["slots"]["delta"] == 0
    # one copy left: a new offer for it would empty the slot
    state = market.catch_up(db)
    state["offer"] = market._roll_offer(db, always, state, market._cards(db)[0])
    market._save_state(db, state)
    db.commit()
    w = client.get("/api/economy/wallet").json()["offer"]
    assert (w["treatment"], w["copies"], w["empties_slot"]) == ("holo", 1, True)
    page = client.get("/api/market/offer").json()["offer"]
    assert page["empties_slot"] is True and page["card"]["id"] == m.id and page["card"]["code"] == "#000001"
    r = client.post("/api/market/offer", json={"accept": True}).json()
    assert r["slots"]["delta"] == -1 and r["slots"]["emptied"] == [{"media_id": m.id, "treatment": "holo"}]


def test_an_offer_for_a_normal_copy_empties_no_slot(db, monkeypatch):
    offer_config(monkeypatch)
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal")
    offer = market.current_offer(db, u, market.catch_up(db))
    assert (offer["treatment"], offer["copies"], offer["empties_slot"]) == ("normal", 1, False)
    assert market.answer_offer(db, u, False)["slots"]["delta"] == 0


# ---------------------------------------------------------------- 6.1, 6.2: the scan preview and result


def test_the_scan_preview_counts_new_files_without_adding_them(tmp_media, db, client):
    for i in range(4):
        make_png(tmp_media / f"img_{i}.png", i)
    first = scanner.preview(tmp_media)  # an empty library
    # v0.9.0: the preview also says what the scan would pay: nothing here, it is the free first import
    assert first == {"new_media": 4, "restored_media": 0, "missing_media": 0, "new_slots": 12, "slots_filled": 0,
                     "slots_total": 0, "slots_filled_after": 0, "slots_total_after": 12,
                     "completeness_now": 0.0, "completeness_after": 0.0,
                     "duplicate_media": 0, "coins": 0, "coins_full": 4 * cfg.ingest.image, "coins_reason": "first_import"}
    assert db.scalar(select(MediaItem.id).limit(1)) is None  # nothing was added
    scanner.full_scan(tmp_media)
    u = local_user(db)
    held = db.scalars(select(MediaItem).order_by(MediaItem.id)).all()
    own(db, u, held[0], "foil")
    own(db, u, held[0], "holo")
    own(db, u, held[1], "foil")
    assert scanner.preview(tmp_media)["new_media"] == 0

    for i in range(4, 10):
        make_png(tmp_media / "more" / f"img_{i}.png", i)
    (tmp_media / "notes.txt").write_text("not media")
    (tmp_media / "empty.png").write_bytes(b"")
    p = scanner.preview(tmp_media)
    assert (p["new_media"], p["new_slots"], p["missing_media"], p["restored_media"]) == (6, 18, 0, 0)
    # after the first import new files pay: 5 in full, the 6th at half (the day's steps)
    ing = cfg.ingest
    assert ing.full_files_per_day == 5 and p["coins_full"] == 6 * ing.image and p["duplicate_media"] == 0
    assert p["coins"] == 5 * ing.image + round(ing.image / 2) and p["coins_reason"] == "daily_limit"
    assert (p["slots_filled"], p["slots_total"], p["slots_filled_after"], p["slots_total_after"]) == (3, 12, 3, 30)
    assert p["completeness_now"] == pytest.approx(3 / 12) and p["completeness_after"] == pytest.approx(3 / 30)
    assert len(db.scalars(select(MediaItem)).all()) == 4  # still only the first four

    # the endpoint: the same numbers, 200, and no scan is started
    r = client.post("/api/media/scan?preview=1")
    assert r.status_code == 200 and r.json() == p
    assert scanner.get_status().state == "idle" and len(db.scalars(select(MediaItem)).all()) == 4

    # a moved file is not new; a file that is gone takes its card's slots with it
    os.rename(tmp_media / "img_3.png", tmp_media / "more" / "moved.png")
    (tmp_media / "img_1.png").unlink()  # held[1], with one Foil
    p = scanner.preview(tmp_media)
    assert (p["new_media"], p["missing_media"]) == (6, 1)
    assert (p["slots_filled_after"], p["slots_total_after"]) == (2, 27)


def test_the_preview_reads_no_file(tmp_media, monkeypatch):
    for i in range(3):
        make_png(tmp_media / f"img_{i}.png", i)

    def boom(*a, **k):
        raise AssertionError("the preview must not open or hash a file")

    monkeypatch.setattr(scanner, "ingest_path", boom)
    from app.services import ingest

    monkeypatch.setattr(ingest, "hash_file", boom)
    monkeypatch.setattr(ingest, "probe", boom)
    assert scanner.preview(tmp_media)["new_media"] == 3


def test_a_missing_card_that_comes_back_is_restored_not_new(tmp_media, db):
    files = [make_png(tmp_media / f"img_{i}.png", i) for i in range(3)]
    scanner.full_scan(tmp_media)
    u = local_user(db)
    m = db.scalars(select(MediaItem).where(MediaItem.path == "img_0.png")).one()
    own(db, u, m, "special")
    stat = files[0].stat()
    data = files[0].read_bytes()
    files[0].unlink()
    scanner.full_scan(tmp_media)
    assert scanner.get_status().last_report["slots"]["emptied"] == [{"media_id": m.id, "treatment": "special"}]
    files[0].write_bytes(data)
    os.utime(files[0], (stat.st_atime, stat.st_mtime))
    p = scanner.preview(tmp_media)
    assert (p["new_media"], p["restored_media"], p["new_slots"]) == (0, 1, 0)
    assert (p["slots_filled"], p["slots_total"], p["slots_filled_after"], p["slots_total_after"]) == (0, 6, 1, 9)
    scanner.full_scan(tmp_media)
    last = scanner.get_status().last_report
    assert last["restored"] == 1 and last["new_media"] == 0
    assert last["slots"]["filled_now"] == [{"media_id": m.id, "treatment": "special"}]
    assert (last["slots"]["filled"], last["slots"]["total"], last["slots"]["total_delta"]) == (1, 9, 3)


def test_the_scan_status_reports_what_the_scan_added(tmp_media, db, client):
    for i in range(3):
        make_png(tmp_media / f"img_{i}.png", i)
    scanner.full_scan(tmp_media)
    u = local_user(db)
    own(db, u, db.scalars(select(MediaItem)).first(), "foil")
    for i in range(3, 8):
        make_png(tmp_media / f"img_{i}.png", i)
    before = scanner.preview(tmp_media)
    report = scanner.full_scan(tmp_media)
    assert report["new"] == 5 and "slots" not in report  # the function's own report is unchanged
    st = client.get("/api/media/scan").json()
    last = st["last_report"]
    assert st["state"] == "idle" and last["new"] == last["new_media"] == 5 and last["new_slots"] == 15
    assert last["slots"] == {"filled": 1, "total": 24, "delta": 0, "filled_now": [], "emptied": [], "total_delta": 15}
    # the preview said the same
    assert (before["new_media"], before["new_slots"]) == (last["new_media"], last["new_slots"])
    assert (before["slots_filled_after"], before["slots_total_after"]) == (last["slots"]["filled"], last["slots"]["total"])
    w = client.get("/api/economy/wallet").json()
    assert (w["slots_filled"], w["slots_total"]) == (1, 24)


# ---------------------------------------------------------------- PT15: a price per day


def test_daily_table_takes_the_last_price_of_each_day():
    dated = [("2026-11-01", 10), ("2026-11-01", 12), ("2026-11-02", 12), ("2026-11-02", 15), ("2026-11-03", 9)]
    assert mk.daily_table(dated) == [
        {"date": "2026-11-01", "price": 12, "change": None, "change_coins": None},
        {"date": "2026-11-02", "price": 15, "change": 0.25, "change_coins": 3},
        {"date": "2026-11-03", "price": 9, "change": -0.4, "change_coins": -6},
    ]
    assert [d["date"] for d in mk.daily_table(dated, days=2)] == ["2026-11-02", "2026-11-03"]
    assert mk.daily_table(dated, days=2)[0]["change"] == 0.25  # the day before still counts for the change
    assert mk.daily_table([]) == []


def test_the_card_history_has_a_row_per_day_for_thirty_days(client):
    with SessionLocal() as db:
        m = media(db, 3)[0]
        dear(db, m, 40, 2.0)
        mid = m.id
    h = client.get(f"/api/market/cards/{mid}/history").json()
    days, points = h["days"], h["points"]
    assert len(points) == mk.HISTORY and len(days) == 30
    assert [d["date"] for d in days] == sorted({d["date"] for d in days})  # one row per day, oldest first
    assert days[-1]["price"] == points[-1]["price"]  # today: the latest price
    for prev, d in zip(days, days[1:]):
        assert d["change_coins"] == d["price"] - prev["price"]
        assert d["change"] == round(d["price"] / prev["price"] - 1, 4)
    assert days[0]["change"] is not None  # the history reaches a day further back than the table
    # the prices are for a Normal copy; the card's own factors turn them into the other treatments
    f = h["treatment_value"]
    assert f["normal"] == 1 and 1 < f["foil"] < f["holo"] < f["special"] <= cfg.market.treatment_value["special"]
    with SessionLocal() as db:
        prices = market.Prices(db, [mid])
        assert f == prices.factors(mid)
        assert prices.price(mid, "special") == pytest.approx(prices.price(mid) * f["special"], rel=0.01)


def test_helpers_used_above_cover_every_plan():
    # one Special from lower copies with 4 per fuse: all Normal (64), all Holo (4), and the mixes between
    plans = list(loop_plans(cfg, "special", 4))
    assert {"normal": 64} in plans and {"holo": 4} in plans and {"foil": 16} in plans
    assert {"normal": 16, "foil": 8, "holo": 1} in plans
    assert all(sum(n * 4 ** TIERS.index(t) for t, n in p.items()) == 64 for p in plans)  # in Normal copies
    assert list(loop_plans(cfg, "foil", 4)) == [{"normal": 4}]
    assert len(list(itertools.islice(loop_plans(cfg, "special", 7), 1000))) < 1000
    assert math.isfinite(loop_return(cfg))

