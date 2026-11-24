"""v0.7.1: the fixes for the bug hunt of v0.7.0 (docs/research/bug-hunt-v0.7.0.md).

One test (or a few) per finding that touches the server. The ids in the headings are the report's."""

import json
import random
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.game import economy as eco
from app.game import goals as gl
from app.game import market as mk
from app.game.config import EconomyConfig, get_config
from app.game.packs import PoolItem, Pull, roll_pack
from app.main import app
from app.models import AppState, CollectionEntry, MarketCard, MediaItem, Tag, Transaction
from app.services import cards, chase, market, onboarding, rewards
from app.services import packs as pack_svc
from app.services.wallet import credit, local_user
from tests.test_chase import FixedRng, media, own
from tools import simulate as sim

cfg = get_config()
STD = cfg.packs["standard"]


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
    return {(e.media_id, e.treatment): e.copies for e in db.scalars(select(CollectionEntry)) if e.copies}


def dear(db, m, print_run=300):
    """A scarce card: its price is high enough for a step of a few percent to show after rounding."""
    m.print_run, m.popularity = print_run, 1.0
    db.commit()
    market.catch_up(db)
    return market.Prices(db, [m.id]).price(m.id, "normal")


# ---------------------------------------------------------------- B4: the quote is what the sale pays


def test_a_quote_for_several_copies_is_what_the_sale_pays(db):
    """v0.8.0: a sale does not move the price, so n copies pay n × the price of one."""
    u = local_user(db)
    m = media(db, 3)[0]
    dear(db, m)
    own(db, u, m, "normal", copies=16)
    prices = market.Prices(db, [m.id])
    one = prices.sell(m.id, "normal")
    assert prices.sale_prices(m.id, "normal", 15) == [one] * 15
    quote = cards.card_out(db, u, m)["sell_extras"]["normal"]
    assert quote == one * 15
    paid = pack_svc.sell_card(db, u, m.id, "normal", 15)
    assert paid["sold"] == 15 and paid["price"] == quote
    assert market.Prices(db, [m.id]).sell(m.id, "normal") == one  # the price after is the price before


def test_with_a_trade_impact_the_quote_counts_the_falling_price(db, monkeypatch):
    """The rule behind `trade_impact` (3% until v0.8.0; now 0, and refused by the config when
    cards are listed): each copy sold lowers the price for the next, and the quote says so."""
    monkeypatch.setattr(cfg.market, "trade_impact", 0.03)
    u = local_user(db)
    m = media(db, 3)[0]
    dear(db, m)
    own(db, u, m, "normal", copies=16)
    prices = market.Prices(db, [m.id])
    one = prices.sell(m.id, "normal")
    steps = prices.sale_prices(m.id, "normal", 15)
    assert steps[0] == one and steps[-1] < one and steps == sorted(steps, reverse=True)
    quote = cards.card_out(db, u, m)["sell_extras"]["normal"]
    assert quote == sum(steps) < one * 15
    paid = pack_svc.sell_card(db, u, m.id, "normal", 15)
    assert paid["sold"] == 15 and paid["price"] == quote
    assert market.Prices(db, [m.id]).sell(m.id, "normal") < one


def test_the_duplicates_button_shows_what_it_pays(db, client):
    u = local_user(db)
    a, b, c = media(db, 3)
    for m in (a, b, c):
        dear(db, m)
    own(db, u, a, "normal", copies=9)
    own(db, u, b, "foil", copies=6)
    own(db, u, c, "holo")  # no duplicate: not part of it
    shown = client.get("/api/market").json()["duplicates"]
    assert shown["copies"] == 8 + 5
    r = client.post("/api/market/sell-duplicates").json()
    assert r["sold"] == shown["copies"] and r["price"] == shown["value"]
    assert "sell_extras" in client.get(f"/api/media/{c.id}").json()


# ---------------------------------------------------------------- B6: selling duplicates never sells the kept copy


def test_a_sale_never_goes_below_the_copies_to_keep(db):
    u = local_user(db)
    m = media(db, 2)[0]
    own(db, u, m, "normal", copies=3)
    # A second request read "3 copies" too and asks for 2 more after the first one sold 2.
    assert market.sell(db, u, m.id, "normal", 2, keep=1)["sold"] == 2
    with pytest.raises(market.NotOwned):
        market.sell(db, u, m.id, "normal", 2, keep=1)
    assert copies(db) == {(m.id, "normal"): 1}
    assert pack_svc.take_copy(db, u, m.id, "normal", keep=1) is False
    assert pack_svc.take_copy(db, u, m.id, "normal") is True  # a plain sale may take the last one


def test_selling_duplicates_survives_copies_that_are_already_gone(db, monkeypatch):
    u = local_user(db)
    a, b = media(db, 2)
    own(db, u, a, "normal", copies=2)
    own(db, u, b, "normal", copies=3)
    real = market._sell

    def racing(db_, user, media_id, treatment, count=1, keep=0):
        if media_id == a.id:  # another request sold this card's duplicates a moment ago
            raise market.NotOwned()
        return real(db_, user, media_id, treatment, count, keep=keep)

    monkeypatch.setattr(market, "_sell", racing)
    r = market.sell_duplicates(db, u)  # v0.7.0: NotOwned escaped and the request answered 500
    assert r["sold"] == 2 and copies(db) == {(a.id, "normal"): 2, (b.id, "normal"): 1}


# ---------------------------------------------------------------- B7: a pack is always full


def test_a_library_smaller_than_the_pack_still_fills_it():
    pool = [PoolItem(i, tagged=False) for i in range(3)]
    for seed in range(30):
        pulls = roll_pack(cfg, STD, pool, 0, random.Random(seed))
        assert len(pulls) == STD.size
        assert {p.media_id for p in pulls} == {0, 1, 2}  # every card once before any comes again
        # a card that came twice is new once; its second copy is a duplicate, a rarer copy or a new version
        first = {}
        for p in pulls:
            first.setdefault(p.media_id, []).append(p.outcome)
        assert all(v.count("new") == 1 for v in first.values())
    big = [PoolItem(i, tagged=False) for i in range(40)]
    assert len({p.media_id for p in roll_pack(cfg, STD, big, 0, random.Random(1))}) == STD.size  # no repeats when there is room


def test_opening_a_pack_in_a_tiny_library_gives_the_full_number_of_cards(db):
    u = local_user(db)
    media(db, 3)
    credit(db, u, 5000, "test")
    db.commit()
    for pack_id in ("standard", "premium"):
        before = sum(copies(db).values())
        r = pack_svc.open_pack(db, u, pack_id)
        assert len(r["pulls"]) == cfg.packs[pack_id].size
        assert sum(copies(db).values()) == before + cfg.packs[pack_id].size


def test_the_simulator_rolls_small_libraries_like_the_game():
    book = sim.Book(cfg, 3)
    for run in (40000, 9000, 300):
        book.add(run)
    for seed in range(20):
        fast = sim.roll_fast(cfg, STD, book, 0.0, random.Random(seed))
        real = roll_pack(cfg, STD, book.pool_items(), 0.0, random.Random(seed))
        assert [(p.media_id, p.treatment, p.outcome) for p in fast] == [(p.media_id, p.treatment, p.outcome) for p in real]
        assert len(fast) == STD.size


# ---------------------------------------------------------------- B8: the week starts at local midnight


def test_the_week_starts_and_ends_at_local_midnight(db):
    monday = date(2026, 11, 16)
    sunday = monday - timedelta(days=1)
    local_midnight = datetime.combine(monday, time.min).astimezone(timezone.utc)
    assert gl.week_start(monday) == local_midnight == gl.day_start(monday)
    assert gl.week_end(sunday) == local_midnight == gl.day_end(sunday)
    assert gl.week_end(monday) == gl.week_start(monday + timedelta(days=7))
    # Three sales half an hour into Monday (local time) count for Monday's week and for Monday.
    u = local_user(db)
    at = local_midnight + timedelta(minutes=30)
    for i in range(3):
        db.add(Transaction(user_id=u.id, amount=5, reason="sell", ref=f"media:{i}:normal", balance_after=u.coins,
                           created_at=at))
    db.commit()
    assert chase.week_counters(db, u, monday).sells == 3 == chase.day_counters(db, u, monday).sells
    assert chase.week_counters(db, u, monday + timedelta(days=7)).sells == 0
    assert chase.period_goals(db, u, "week", sunday)["ends_at"] == local_midnight.isoformat()


# ---------------------------------------------------------------- L1, L7: upgrade attempts are switched off


def test_upgrade_attempts_are_off_in_the_live_config(db, client):
    assert cfg.upgrades.enabled is False
    assert EconomyConfig.model_fields["upgrades"].default.enabled is True  # the model's default stays on
    u = local_user(db)
    m = media(db, 3)[0]
    own(db, u, m, "normal", copies=2)
    credit(db, u, 5000, "test")
    db.commit()
    assert eco.upgrade_cost(cfg, "normal") is None and eco.upgrade_cost(cfg, "holo", 5) is None
    assert cards.card_out(db, u, m)["upgrade_cost"] is None  # the card panel hides its button on null
    start = u.coins
    with pytest.raises(chase.CannotUpgrade, match="switched off"):
        chase.attempt_upgrade(db, u, m.id, FixedRng(0.0))
    r = client.post(f"/api/cards/{m.id}/upgrade")
    assert r.status_code == 409 and "switched off" in r.json()["detail"]
    db.refresh(u)
    assert u.coins == start and copies(db) == {(m.id, "normal"): 2}
    assert client.get("/api/economy/config").json()["upgrades"]["enabled"] is False
    assert client.get(f"/api/media/{m.id}").json()["upgrade_cost"] is None


@pytest.mark.upgrades_on
def test_the_upgrade_code_still_works_behind_the_switch(db, client):
    u = local_user(db)
    m = media(db, 200)[0]
    own(db, u, m, "normal")
    assert cards.card_out(db, u, m)["upgrade_cost"] == cfg.upgrades.cost["normal"]
    assert chase.attempt_upgrade(db, u, m.id, FixedRng(0.0))["treatment"] == "foil"
    assert client.get("/api/economy/config").json()["upgrades"]["enabled"] is True


def test_no_goal_or_checklist_step_asks_for_an_upgrade(db):
    texts = [g.title for g in cfg.goals.templates] + [s.title for s in cfg.onboarding.steps]
    assert not [t for t in texts if "upgrade" in t.lower()]
    # the "upgrade" goals and the checklist step still count fuses
    u = local_user(db)
    m = media(db, 3)[0]
    own(db, u, m, "normal", copies=cfg.fuse.copies)
    pack_svc.fuse(db, u, m.id, "normal")
    today = date.today()
    c = chase.day_counters(db, u, today)
    assert c.fuses == 1 and c.upgrades == 1
    step = next(s for s in onboarding.checklist(db, u)["steps"] if s["kind"] == "upgrades")
    assert step["done"] and "fuse" in step["title"].lower()


def test_goals_that_count_fuses_are_never_left_out(db):
    """L7: with every owned card at Special the picker dropped "upgrade" and "review", although
    lower versions can still be fused and a review was waiting."""
    u = local_user(db)
    items = media(db, 6)
    for m in items:
        m.tags.append(Tag(name=f"t{m.id}"))
        own(db, u, m, "normal", copies=9)
        own(db, u, m, "special")  # obtained now; the tags carry no payout, so they count as old
    db.commit()
    blocked = chase.blocked_kinds(db, u, "week")
    assert "upgrade" not in blocked and "fuse" not in blocked
    assert cards.card_out(db, u, items[0])["review_available"] is True
    assert "review" not in blocked  # a review is waiting
    for m in items:
        assert rewards.reward_review(db, u, m.id, "special") > 0
    db.commit()
    assert chase.blocked_kinds(db, u, "week") == {"review"}  # none waiting, none can come: every card is Special


def test_the_simulator_makes_no_attempt_while_they_are_off():
    off = sim.simulate(cfg, 60, "regular", "fuser", days=25, seed=3)
    assert off.days[-1].spent_upgrades == 0 and off.days[-1].upgrades == 0
    on = cfg.model_copy(update={"upgrades": cfg.upgrades.model_copy(update={"enabled": True})})
    assert sim.simulate(on, 60, "regular", "fuser", days=25, seed=3).days[-1].spent_upgrades > 0


# ---------------------------------------------------------------- L2: buying and selling a wanted card loses coins


def test_the_config_refuses_a_wanted_bonus_that_beats_the_buy_price():
    m = cfg.market
    assert m.wanted_bonus == 1.3
    assert m.sell_rate * m.wanted_bonus * (1 + m.buy_impact) < m.buy_markup
    raw = cfg.model_dump()
    raw["market"]["wanted_bonus"] = 1.5  # the value of v0.7.0: 85% × 1.5 = 127.5% against 115%
    with pytest.raises(ValueError, match="buy_markup"):
        EconomyConfig.model_validate(raw)
    # under the buy price alone, above it when a purchase adds 3% (v0.8.0: a purchase adds nothing,
    # `buy_impact = 0`, so 1.32 passes; with the old 3% it is refused)
    raw["market"]["wanted_bonus"] = 1.32
    EconomyConfig.model_validate(raw)
    raw["market"]["buy_impact"] = 0.03
    with pytest.raises(ValueError, match="buy_markup"):
        EconomyConfig.model_validate(raw)
    raw["market"].update(wanted_bonus=1.5, buy_markup=1.4, buy_impact=0.0)
    EconomyConfig.model_validate(raw)


@pytest.mark.price_steps  # v0.8.0: only cards past the first fuse price step are listed
def test_a_round_of_buying_and_selling_a_wanted_card_loses_coins(db):
    u = local_user(db)
    items = media(db, 3)  # a small library: every card is listed
    top = items[0]
    dear(db, top, 40)
    own(db, u, top, "normal")
    credit(db, u, 10_000_000, "test")
    db.commit()
    state = market.catch_up(db)
    state["wanted"] = [top.id]
    market._save_state(db, state)
    db.commit()
    assert market.Prices(db, [top.id]).info(top.id)["wanted"] is True
    for _ in range(20):
        before = u.coins
        bought = market.buy(db, u, top.id)
        sold = market.sell(db, u, top.id, "normal")
        assert sold["price"] < bought["price"] and u.coins < before


# ---------------------------------------------------------------- L3: milestones count against the largest library seen


def test_files_that_go_missing_never_pay_a_milestone(db, client):
    u = local_user(db)
    items = media(db, 300)
    one = items[0]
    for t in ("foil", "holo", "special"):
        own(db, u, one, t)
    nothing = {"milestones": [], "packs": []}  # v0.9.0: what a claim answers when no milestone waits
    assert rewards.claim_milestones(db, u) == nothing  # 3 of 900 versions = 0.33%: under the first milestone (1%)
    db.commit()
    paid_before = rewards.paid_milestones(db, u)
    for m in items[1:]:
        m.missing = True
    db.commit()
    # The shown completeness is the real one: the one card left is complete.
    assert rewards.completeness(db, u) == 1.0
    # The milestones still count against 300 cards: nothing new is reached.
    assert rewards.library_peak(db) == 300
    share, filled, total = rewards.milestone_progress(db, u)
    assert (filled, total) == (3, 900) and share == pytest.approx(3 / 900)
    assert rewards.note_milestones(db, u) == [] and rewards.claim_milestones(db, u) == nothing
    assert rewards.paid_milestones(db, u) == paid_before
    # The wallet names a milestone that will pay, and how far away it is on that measure.
    w = client.get("/api/economy/wallet").json()
    assert w["completeness"] == 1.0 and (w["slots_filled"], w["slots_total"]) == (3, 3)
    nm = w["next_milestone"]
    assert nm["at"] == 0.01 and nm["cards"] == 300
    assert nm["progress"] == pytest.approx(3 / 900) and nm["versions_needed"] == 9 - 3
    # The next goal uses the same measure.
    with SessionLocal() as s:
        nxt = [c for c in [chase.overview(s, local_user(s))["next"]] if c and c["kind"] == "milestone"]
        assert all(c["progress"] < c["target"] for c in nxt)
    # Putting the files back changes nothing either, and the peak only ever rises.
    for m in items[1:]:
        m.missing = False
    db.commit()
    assert rewards.claim_milestones(db, u) == nothing
    media_more = [MediaItem(path=f"x{i}.jpg", sha256=f"x{i}" * 8, kind="image") for i in range(5)]
    db.add_all(media_more)
    db.commit()
    assert rewards.library_peak(db) == 305
    assert int(db.get(AppState, rewards.LIBRARY_PEAK_KEY).value) == 305


def test_milestones_pay_as_before_in_a_library_that_never_shrank(db, client):
    u = local_user(db)
    items = media(db, 10)
    own(db, u, items[0], "foil")  # 1 of 30 = 3.3%: the 1% and 2% steps
    # v0.9.0: both become claimable and pay packs when claimed (before: 50 coins each, by themselves)
    assert [m["at"] for m in rewards.note_milestones(db, u)] == [0.01, 0.02]
    db.commit()
    w = client.get("/api/economy/wallet").json()
    assert [m["at"] for m in w["milestones_claimable"]] == [0.01, 0.02]
    assert w["milestones_claimable_packs"] == eco.pack_list(cfg, [m.reward for m in cfg.milestones[:2]])
    assert w["next_milestone"]["at"] == 0.05  # the next one to reach, not one that only waits to be claimed
    r = client.post("/api/economy/milestones/claim")
    assert r.status_code == 200 and r.json()["milestones"] == [0.01, 0.02] and r.json()["coins"] == w["coins"]
    assert r.json()["packs"] == w["milestones_claimable_packs"]
    assert client.post("/api/economy/milestones/claim").status_code == 409
    db.expire_all()
    nm = client.get("/api/economy/wallet").json()["next_milestone"]
    assert nm["at"] == 0.05 and nm["versions_needed"] == 1 and nm["cards"] == 10
    assert nm["progress"] == pytest.approx(rewards.completeness(db, u))


def test_a_progress_reset_keeps_the_largest_library(db, client):
    items = media(db, 8)
    assert client.get("/api/economy/wallet").status_code == 200  # the wallet counts the library
    with SessionLocal() as s:
        assert int(s.get(AppState, rewards.LIBRARY_PEAK_KEY).value) == 8
    assert client.post("/api/account/reset", json={"confirm": "RESET"}).status_code == 200
    with SessionLocal() as s:
        assert int(s.get(AppState, rewards.LIBRARY_PEAK_KEY).value) == len(items)


# ---------------------------------------------------------------- L4: an offer is always worth taking


def test_no_offer_is_made_for_a_wanted_card_or_below_a_plain_sale():
    always = cfg.model_copy(update={"market": cfg.market.model_copy(update={"offer_chance": 1.0, "offer_min_value": 0})})
    holdings = [(1, "holo", 900), (2, "foil", 500), (3, "normal", 100)]
    for seed in range(200):
        hit = mk.roll_offer(always, holdings, random.Random(seed), wanted={1, 2})
        assert hit is not None and hit[0] == 3
        assert hit[2] >= mk.sell_price(always, 100)
    assert mk.roll_offer(always, holdings, random.Random(1), wanted={1, 2, 3}) is None
    # An offer range that reaches below the market's own price: such an offer is not made.
    low = always.model_copy(update={"market": always.market.model_copy(update={"offer_range": (0.5, 0.6)})})
    assert all(mk.roll_offer(low, holdings, random.Random(s)) is None for s in range(50))


def test_an_open_offer_is_hidden_while_selling_pays_more(db, monkeypatch):
    always = cfg.model_copy(update={"market": cfg.market.model_copy(
        update={"offer_chance": 1.0, "offer_min_value": 0, "wanted_count": 0, "offer_range": (0.9, 0.9)})})
    monkeypatch.setattr(market, "get_config", lambda: always)
    u = local_user(db)
    m = media(db, 3)[0]
    dear(db, m, 40)
    own(db, u, m, "normal", copies=2)
    state = market.catch_up(db)
    state["offer"] = market._roll_offer(db, always, state, market._cards(db)[0])
    market._save_state(db, state)
    db.commit()
    offer = market.current_offer(db, u)
    assert offer and offer["price"] >= offer["sell_price"]
    # The card becomes wanted in the stored state: selling pays more than the offer, so it is not shown.
    state["wanted"] = [m.id]
    market._save_state(db, state)
    db.commit()
    assert market.current_offer(db, u) is None
    with pytest.raises(market.NoOffer):
        market.answer_offer(db, u, True)
    # And the tick's own roll leaves a wanted card out.
    assert market._roll_offer(db, always, state, market._cards(db)[0]) is None


# ---------------------------------------------------------------- L6: a new version is not a duplicate


def test_a_treatment_not_held_is_a_new_version_not_a_duplicate(db, monkeypatch):
    one = STD.model_copy(update={"size": 1})
    pool = [PoolItem(0, tagged=True, owned={"holo": 1})]
    seen = {}
    for seed in range(400):
        p = roll_pack(cfg, one, pool, 0, random.Random(seed))[0]
        seen.setdefault(p.treatment, set()).add(p.outcome)
    assert seen["normal"] == {"version"} and seen["foil"] == {"version"}
    assert seen["holo"] == {"duplicate"} and seen.get("special", {"better"}) == {"better"}
    # Through the service: the Foil fills a version, is kept, and the reply names it.
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "holo")
    credit(db, u, 1000, "test")
    db.commit()
    monkeypatch.setattr(pack_svc, "roll_pack", lambda *a, **k: [Pull(m.id, "foil", "version")])
    before = rewards.slots(db, u)[0]
    r = pack_svc.open_pack(db, u, "standard")
    assert r["pulls"][0]["outcome"] == "version" and r["pulls"][0]["is_new"] is False
    assert rewards.slots(db, u)[0] == before + 1 and copies(db) == {(m.id, "holo"): 1, (m.id, "foil"): 1}
    assert pack_svc.history(db, u)["recent_packs"][0]["version"] == 1


# ---------------------------------------------------------------- L8: your own trades don't change the fuse count


@pytest.mark.price_steps
def test_buying_the_missing_copy_does_not_raise_the_fuse_count(db):
    u = local_user(db)
    items = media(db, 3)  # a small library: every card is listed
    m = items[0]
    credit(db, u, 1_000_000, "test")
    db.commit()
    market.catch_up(db)
    step = cfg.fuse.price_steps[2]
    lo, hi = 1, 10_000_000
    while lo < hi:  # the largest print run whose live price is just under the step
        mid = (lo + hi) // 2
        m.print_run, m.popularity = mid, 1.0
        db.get(MarketCard, m.id).factor = 1.0
        db.commit()
        if market.Prices(db, [m.id]).price(m.id, "normal") < step:
            hi = mid
        else:
            lo = mid + 1
    m.print_run = lo
    db.commit()
    price = market.Prices(db, [m.id]).price(m.id, "normal")
    assert step - 2 <= price < step  # until v0.8.0 one purchase (+3%) lifted the live price past the step
    db.get(MarketCard, m.id).history = json.dumps([price - 1, price])  # the price at the last update
    db.commit()
    need = eco.fuse_need(cfg, price)
    own(db, u, m, "normal", copies=need - 1)
    assert cards.card_out(db, u, m)["fuse_copies"] == need and cards.card_out(db, u, m)["fuse_from"] is None
    market.buy(db, u, m.id)  # the one missing copy
    p = market.Prices(db, [m.id])
    # v0.8.0: a purchase no longer moves the price at all, so the count cannot move either
    assert p.price(m.id, "normal") == price == p.tick_price(m.id)
    card = cards.card_out(db, u, m)
    assert card["fuse_copies"] == need and card["fuse_from"] == "normal"
    assert pack_svc.fuse(db, u, m.id, "normal")["used"] == need
    # the next price update is what moves the count
    db.get(MarketCard, m.id).history = json.dumps([price, step + 5])
    db.commit()
    assert cards.card_out(db, u, m)["fuse_copies"] == need + 1


def test_the_last_price_point_is_read_without_the_whole_history(db):
    u = local_user(db)
    m = media(db, 1)[0]
    market.catch_up(db)
    row = db.get(MarketCard, m.id)
    for raw, want in (("[3, 7, 12]", 12), ("[40]", 40), ("[1,2, 3 ]", 3)):
        row.history = raw
        db.commit()
        assert market.Prices(db, [m.id]).tick_price(m.id) == want
    row.history = "[]"
    db.commit()
    p = market.Prices(db, [m.id])
    assert p.tick_price(m.id) == p.price(m.id)  # no history yet: the live price
    assert u.id and market.Prices(db, [999]).tick_price(999) == 1


# ---------------------------------------------------------------- L9: targets a small library can reach


def test_a_tag_goal_above_the_paying_tags_left_is_not_picked(db):
    u = local_user(db)
    u.packs_opened = max(cfg.goals.unlock_after_packs, cfg.goals.daily_unlock_after_packs)
    for m in media(db, 5):
        own(db, u, m, "normal")
    assert chase.paying_tags_left(db, u) == 5 * cfg.tagging.max_paid_tags == 60
    weekly_tag = next(g for g in cfg.goals.templates if g.kind == "tag" and g.period == "week")
    assert weekly_tag.target > 60
    for n in range(30):
        day = date(2027, 1, 4) + timedelta(days=7 * n)
        assert weekly_tag.id not in {g["id"] for g in chase.weekly_goals(db, u, day)["goals"]}
    # the pure picker: the limit leaves out only what is above it
    key = next(k for k in (f"2027-W{w:02d}" for w in range(1, 53))
               if weekly_tag in gl.pick_goals(cfg, k, u.id, period="week"))
    assert weekly_tag not in gl.pick_goals(cfg, key, u.id, period="week", limits={"tag": 60})
    assert weekly_tag in gl.pick_goals(cfg, key, u.id, period="week", limits={"tag": weekly_tag.target})
    daily = {g.id for k in range(40) for g in gl.pick_goals(cfg, f"d{k}", u.id, period="day", limits={"tag": 60})}
    assert "day-tag" in daily  # 5 tags are within reach


def test_a_tagging_step_never_asks_for_more_cards_than_there_are(db):
    u = local_user(db)
    items = media(db, 12)
    for m in items[:11]:
        m.tags.append(Tag(name=f"t{m.id}"))
    db.commit()
    step = next(s for s in onboarding.checklist(db, u)["steps"] if s["id"] == "tag-pulled")
    assert (step["progress"], step["target"], step["done"]) == (11, 12, False)
    assert step["title"] == "Tag all 12 of your cards"
    items[11].tags.append(Tag(name="last"))
    db.commit()
    step = next(s for s in onboarding.checklist(db, u)["steps"] if s["id"] == "tag-pulled")
    # v0.9.0: this step pays nothing, so being done is all there is to it (nothing to claim)
    assert step["done"] and step["claimed"] and step["reward"] == []
    with pytest.raises(chase.NotClaimable):
        onboarding.claim(db, u, "tag-pulled")
    # a library that is big enough keeps the configured target and title
    media_more = [MediaItem(path=f"y{i}.jpg", sha256=f"y{i}" * 8, kind="image") for i in range(10)]
    db.add_all(media_more)
    db.commit()
    step = next(s for s in onboarding.checklist(db, u)["steps"] if s["id"] == "tag-pulled")
    assert step["target"] == 15 and step["title"] == "Tag 15 of your cards"


# ---------------------------------------------------------------- L10: a Normal copy is never "a shinier card"


def test_a_normal_copy_that_came_back_pays_no_review(db):
    u = local_user(db)
    m = media(db, 3)[0]
    m.tags.append(Tag(name="beach"))  # tagged, without a payout: it counts as tagged long ago
    own(db, u, m, "normal")
    db.commit()
    assert cards.card_out(db, u, m)["review_available"] is False
    assert rewards.review_eligible_treatment(db, u, m.id) is None
    assert rewards.reward_review(db, u, m.id, "normal") == 0
    own(db, u, m, "foil")
    assert cards.card_out(db, u, m)["review_available"] is True
    assert rewards.reward_review(db, u, m.id, "foil") > 0


# ---------------------------------------------------------------- L11, L12: texts and config keys


def test_config_keys_nobody_reads_are_gone():
    assert set(type(cfg.rtp).model_fields) == {"assumed_tags_per_card", "early_target"}
    from app.game.config import CONFIG_PATH

    text = CONFIG_PATH.read_text(encoding="utf-8")
    assert "early_packs" not in text and "late_target" not in text
    assert "at 100% tagged" not in text  # rarity decay follows completeness
