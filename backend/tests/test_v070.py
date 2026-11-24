"""v0.7.0: completeness counts versions held (docs/specs/maximal-collection-completeness-spec.md).

Every card has three slots (Foil, Holo, Special). A slot is filled while the player holds at least
one copy of that treatment; Normal copies and tags don't count."""

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.game import economy as eco
from app.game.config import get_config
from app.main import app
from app.models import Tag
from app.services import chase, rewards
from app.services import packs as pack_svc
from app.services.wallet import credit, local_user
from tests.test_chase import FixedRng, media, own
from tools import simulate as sim

cfg = get_config()


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        yield c


def test_the_default_mode_counts_three_slots_per_card():
    assert cfg.completeness.mode == "slots"
    assert eco.slot_treatments(cfg) == ["foil", "holo", "special"]  # Normal is not a slot
    assert eco.slots_total(cfg, 1000) == 3000
    assert eco.slots_completeness(0, 0) == 0.0
    assert eco.slots_completeness(1000, 3000) == pytest.approx(1 / 3)
    assert eco.slots_completeness(3000, 3000) == 1.0


def test_the_examples_of_the_spec(db):
    u = local_user(db)
    items = media(db, 10)
    assert rewards.slots(db, u) == (0, 30) and rewards.completeness(db, u) == 0.0  # nothing pulled
    for m in items:
        own(db, u, m, "normal", copies=4)
    assert rewards.slots(db, u) == (0, 30)  # every card owned only as Normal
    for m in items:
        own(db, u, m, "foil", copies=5)  # several copies fill the slot once
    assert rewards.slots(db, u) == (10, 30) and rewards.completeness(db, u) == pytest.approx(1 / 3)
    for m in items:
        own(db, u, m, "holo")
    assert rewards.completeness(db, u) == pytest.approx(2 / 3)
    for m in items:
        own(db, u, m, "special")
    assert rewards.completeness(db, u) == 1.0
    for m in items:  # sells all Specials
        pack_svc.sell_card(db, u, m.id, "special")
    assert rewards.slots(db, u) == (20, 30)


def test_tags_do_not_count_and_an_untagged_foil_does(db):
    u = local_user(db)
    a, b = media(db, 2)
    tag = Tag(name="trip")
    db.add(tag)
    a.tags.append(tag)
    own(db, u, a, "normal")
    own(db, u, b, "foil")  # never tagged
    db.commit()
    assert rewards.slots(db, u) == (1, 6)


def test_fusing_the_last_copies_empties_the_lower_slot_and_fills_the_higher(db):
    u = local_user(db)
    m = media(db, 100)[0]
    own(db, u, m, "foil", copies=3)
    assert rewards.slots(db, u) == (1, 300)
    pack_svc.fuse(db, u, m.id, "foil")
    assert rewards.slots(db, u) == (1, 300)  # Foil empties, Holo fills
    own(db, u, m, "normal", copies=3)
    pack_svc.fuse(db, u, m.id, "normal")  # Normal is not a slot: this one only adds
    assert rewards.slots(db, u) == (2, 300)


@pytest.mark.upgrades_on
def test_an_upgrade_follows_the_same_ownership_rules(db):
    u = local_user(db)
    m = media(db, 100)[0]
    own(db, u, m, "foil")
    assert chase.attempt_upgrade(db, u, m.id, FixedRng(0.0))["treatment"] == "holo"
    assert rewards.slots(db, u) == (1, 300)  # the only Foil became the Holo


def test_new_files_add_three_slots_and_missing_files_take_theirs_away(db):
    u = local_user(db)
    a, b = media(db, 2)
    own(db, u, a, "foil")
    own(db, u, b, "holo")
    assert rewards.slots(db, u) == (2, 6)
    b.missing = True
    db.commit()
    assert rewards.slots(db, u) == (1, 3)  # out of the numerator and the denominator
    from tests.test_chase import MediaItem  # a new file
    db.add(MediaItem(path="new.jpg", sha256="f" * 64, kind="image"))
    db.commit()
    assert rewards.slots(db, u) == (1, 6)


def test_a_milestone_pays_once_even_when_completeness_falls_and_rises_again(db):
    u = local_user(db)
    items = media(db, 10)  # 30 slots: one version is 3.3%
    own(db, u, items[0], "foil")
    coins = u.coins
    first = rewards.claim_milestones(db, u)
    db.commit()
    reached = [m for m in cfg.milestones if m.at <= 1 / 30]
    # v0.9.0: a claimed milestone gives sealed packs, never coins
    assert first == {"milestones": [m.at for m in reached], "packs": eco.pack_list(cfg, [m.reward for m in reached])}
    assert rewards.inventory(db, u)["standard"] == sum(m.reward.count for m in reached if m.reward.pack == "standard") > 0
    assert u.coins == coins
    paid = rewards.paid_milestones(db, u)
    assert paid == {m.at for m in cfg.milestones if m.at <= 1 / 30}
    pack_svc.sell_card(db, u, items[0].id, "foil")  # back to 0%
    assert rewards.completeness(db, u) == 0.0
    # the next milestone is the first one that still pays, not one that paid before
    nxt = eco.next_milestone(cfg, 0.0, paid)
    assert nxt is not None and nxt[0] not in paid and nxt[0] == min(m.at for m in cfg.milestones if m.at not in paid)
    own(db, u, items[1], "foil")
    assert rewards.note_milestones(db, u) == []  # the same step does not become claimable again
    assert rewards.claim_milestones(db, u) == {"milestones": [], "packs": []}  # no second payment for the same step


def test_no_credit_for_normals_and_the_older_modes_still_work(db):
    u = local_user(db)
    items = media(db, 4)
    tag = Tag(name="trip")
    db.add(tag)
    for m in items:
        own(db, u, m, "normal")
        m.tags.append(tag)
    db.commit()
    assert rewards.completeness(db, u) == 0.0  # fully tagged, all Normal: 0% (it was about 33%)
    assert rewards.claim_milestones(db, u) == {"milestones": [], "packs": []}
    weighted = cfg.model_copy(deep=True)
    weighted.completeness.mode = "weighted"
    assert eco.collection_completeness(weighted, [(True, "normal")] * 4) == pytest.approx(1 / 3)


def test_the_wallet_reports_the_slots(client, db):
    u = local_user(db)
    a, _ = media(db, 2)
    own(db, u, a, "holo")
    w = client.get("/api/economy/wallet").json()
    assert (w["slots_filled"], w["slots_total"]) == (1, 6)
    assert w["completeness"] == pytest.approx(1 / 6)
    assert w["next_milestone"]["at"] > w["completeness"]


def test_the_simulator_counts_slots_like_the_game():
    s = sim.Sim(cfg, 50, sim.ARCHETYPES["regular"], "fuser", days=20)
    s.run(20)
    held = sum(1 for c in s.book.cards for t, n in enumerate(c.owned) if t > 0 and n > 0)
    assert s.slots == held > 0
    assert s.completeness == pytest.approx(held / (3 * len(s.book.cards)))
    seller = sim.Sim(cfg, 50, sim.ARCHETYPES["regular"], "seller", days=20)
    seller.run(20)
    assert seller.slots == sum(1 for c in seller.book.cards for t, n in enumerate(c.owned) if t > 0 and n > 0)


# ---------------------------------------------------------------- fusing follows the market price


def priced(db, m, coins):
    """Give a card a Normal market price of about `coins` (by its print run) and return the real price."""
    from app.services import market

    market.catch_up(db)
    lo, hi = 1, 10_000_000
    while lo < hi:  # the scarcer, the dearer: find the largest print run that still reaches the price
        mid = (lo + hi + 1) // 2
        m.print_run, m.popularity = mid, 1.0
        db.commit()
        if market.Prices(db, [m.id]).price(m.id, "normal") >= coins:
            lo = mid
        else:
            hi = mid - 1
    m.print_run = lo
    db.commit()
    price = market.Prices(db, [m.id]).price(m.id, "normal")
    # v0.7.1: the fuse count follows the price of the last update, so that is set as well
    import json

    from app.models import MarketCard

    db.get(MarketCard, m.id).history = json.dumps([price])
    db.commit()
    return price


@pytest.mark.price_steps
@pytest.mark.upgrades_on
def test_a_card_that_is_worth_more_takes_more_copies():
    assert cfg.fuse.price_steps == sorted(cfg.fuse.price_steps) and cfg.fuse.price_steps
    steps = cfg.fuse.price_steps
    assert eco.fuse_need(cfg, 1) == cfg.fuse.copies
    assert eco.fuse_need(cfg, steps[0] - 1) == cfg.fuse.copies
    assert eco.fuse_need(cfg, steps[0]) == cfg.fuse.copies + 1
    assert eco.fuse_need(cfg, steps[-1] * 100) == cfg.fuse.copies + len(steps)
    # an upgrade attempt costs more by the same share, in steps of 5 coins
    base = cfg.upgrades.cost["normal"]
    assert eco.upgrade_cost(cfg, "normal", cfg.fuse.copies) == base == eco.upgrade_cost(cfg, "normal")
    dear = eco.upgrade_cost(cfg, "normal", cfg.fuse.copies + 1)
    assert dear == round(base * (cfg.fuse.copies + 1) / cfg.fuse.copies / 5) * 5 > base
    assert eco.upgrade_cost(cfg, "special", 7) is None


@pytest.mark.price_steps
@pytest.mark.upgrades_on
def test_the_fuse_asks_for_the_copies_of_todays_price(db, client):
    u = local_user(db)
    cheap, dear = media(db, 2)
    assert priced(db, cheap, 1) < cfg.fuse.price_steps[0]
    price = priced(db, dear, cfg.fuse.price_steps[1])
    need = eco.fuse_need(cfg, price)
    assert need == cfg.fuse.copies + 2
    own(db, u, cheap, "normal", copies=cfg.fuse.copies)
    own(db, u, dear, "normal", copies=need - 1)
    assert pack_svc.fuse(db, u, cheap.id, "normal")["used"] == cfg.fuse.copies
    with pytest.raises(pack_svc.CannotFuse, match=f"needs {need} copies"):
        pack_svc.fuse(db, u, dear.id, "normal")
    cards = {c["id"]: c for c in client.get("/api/media?filter=pulled").json()["items"]}
    assert cards[dear.id]["fuse_copies"] == need and cards[dear.id]["fuse_from"] is None
    assert cards[dear.id]["upgrade_cost"] == eco.upgrade_cost(cfg, "normal", need) > cfg.upgrades.cost["normal"]
    assert cards[cheap.id]["fuse_copies"] == cfg.fuse.copies
    from app.models import CollectionEntry
    from sqlalchemy import select

    row = db.scalar(select(CollectionEntry).where(CollectionEntry.media_id == dear.id, CollectionEntry.treatment == "normal"))
    row.copies = need
    db.commit()
    assert pack_svc.fuse(db, u, dear.id, "normal")["used"] == need
    # the upgrade attempt charges the raised price
    credit(db, u, 5_000, "test")  # v0.9.0: no milestone coins arrive by themselves any more
    db.commit()
    start = u.coins
    own(db, u, dear, "holo")
    res = chase.attempt_upgrade(db, u, dear.id, FixedRng(0.99))
    assert res["cost"] == eco.upgrade_cost(cfg, "holo", need) and u.coins == start - res["cost"]
