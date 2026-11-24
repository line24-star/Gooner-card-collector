"""v0.2.0: the coin and data errors found by the pre-release audit (work order A7, B1-B6, C10)."""

import random
import threading
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.game import economy as eco
from app.game import packs as pack_rules
from app.game.config import get_config
from app.models import AppState, CollectionEntry, MediaItem, Tag, Transaction
from app.services import chase, folders, rewards
from app.services import packs as pack_svc
from app.services.wallet import credit, local_user
from tests.test_chase import FixedRng, media, own

cfg = get_config()


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def copies(db, media_id=None):
    db.expire_all()
    q = select(CollectionEntry)
    if media_id is not None:
        q = q.where(CollectionEntry.media_id == media_id)
    return {e.treatment: e.copies for e in db.scalars(q).all() if e.copies}


def tag_all(db, name, items):
    t = Tag(name=name)
    t.media = list(items)
    db.add(t)
    db.commit()
    return t


# ---------------------------------------------------------------- A7: a pack upgrade moves the copy up


def test_pack_upgrade_moves_the_best_copy_up_instead_of_adding_one(db, monkeypatch):
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal")
    credit(db, u, 1000, "test")
    db.commit()

    def fake_roll(cfg_, pack, pool, completeness, rng, pity=1.0):
        return [pack_rules.Pull(m.id, "normal", "upgraded", upgraded_to="foil")]

    monkeypatch.setattr(pack_svc, "roll_pack", fake_roll)
    pack_svc.open_pack(db, u, "standard", random.Random(1))
    assert copies(db) == {"foil": 1}  # not Normal ×1 and Foil ×1


# ---------------------------------------------------------------- B1: racing requests


def test_selling_the_last_copy_twice_at_once_pays_once(db):
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "foil")
    start = u.coins
    mid = m.id  # read here: `m` belongs to this thread's session, and two threads loading it at once crashed SQLite
    barrier = threading.Barrier(2)
    results = []

    def sell():
        with SessionLocal() as s:
            barrier.wait()
            try:
                results.append(pack_svc.sell_card(s, local_user(s), mid, "foil")["price"])
            except pack_svc.NotOwned:
                results.append(None)

    threads = [threading.Thread(target=sell) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    paid = [r for r in results if r is not None]
    assert len(paid) == 1
    db.expire_all()
    assert local_user(db).coins == start + paid[0]
    assert copies(db) == {}


def test_take_copy_never_goes_below_zero(db):
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal")
    assert pack_svc.take_copy(db, u, m.id, "normal") is True
    assert pack_svc.take_copy(db, u, m.id, "normal") is False
    db.commit()
    assert db.scalars(select(CollectionEntry.copies)).one() == 0


# ---------------------------------------------------------------- B2: a card pays into set rewards once


def test_a_new_tag_on_the_same_cards_cannot_claim_the_set_reward_again(db):
    u = local_user(db)
    items = media(db, cfg.sets.min_size)
    for m in items:
        own(db, u, m, "foil")
    first = tag_all(db, "pets", items)
    paid = chase.claim_set(db, u, first.id, "foil")
    assert paid["packs"] and paid["coins"] == u.coins  # v0.9.0: packs, no coins
    again = tag_all(db, "animals", items)  # the same five cards under another name
    tier = next(t for s in chase.category_sets(db, u) if s["tag_id"] == again.id for t in s["tiers"] if t["treatment"] == "foil")
    assert tier["reward"] == [] and tier["claimed"] is True
    with pytest.raises(chase.NotClaimable):
        chase.claim_set(db, u, again.id, "foil")


def test_a_bigger_set_pays_only_while_most_of_its_cards_have_not_counted_yet(db):
    """v0.9.0: packs cannot be paid by the share (no part of a pack), so a set pays in full while
    more than half of its cards are new to set rewards, and nothing otherwise."""
    from app.game import goals as gl

    u = local_user(db)
    n = cfg.sets.min_size
    items = media(db, n * 2 + 1)
    for m in items:
        own(db, u, m, "foil")
    small = tag_all(db, "small", items[:n])
    chase.claim_set(db, u, small.id, "foil")

    def foil_tier(tag):
        return next(t for s in chase.category_sets(db, u) if s["tag_id"] == tag.id for t in s["tiers"] if t["treatment"] == "foil")

    half = tag_all(db, "half", items[: n * 2])  # exactly half of its cards are new: not more than half
    assert foil_tier(half)["reward"] == [] and foil_tier(half)["claimed"] is True
    with pytest.raises(chase.NotClaimable):
        chase.claim_set(db, u, half.id, "foil")
    big = tag_all(db, "big", items)  # n + 1 of 2n + 1 are new
    tier = foil_tier(big)
    assert tier["reward"] == gl.set_reward(cfg, n * 2 + 1, "foil") and tier["reward"]
    assert chase.claim_set(db, u, big.id, "foil")["packs"] == tier["reward"]
    assert foil_tier(big)["claimed"] is True


# ---------------------------------------------------------------- B3 + B4: new-file rewards


def test_the_daily_cap_counts_the_local_day(db, monkeypatch):
    u = local_user(db)
    # A payout earlier today, local time. Stored in UTC like every timestamp.
    local_midnight = datetime.combine(date.today(), time.min).astimezone(timezone.utc)
    db.add(Transaction(user_id=u.id, amount=cfg.ingest.daily_cap, reason="ingest", ref="media:0",
                       created_at=local_midnight + timedelta(minutes=5)))
    db.commit()
    m = media(db, 1)[0]
    assert rewards.reward_ingest(db, u, m, initial_scan=False) == 0  # the cap is already used up


def test_both_new_file_tiers_can_pay_before_the_cap(db):
    ing = cfg.ingest
    paid = 0
    amounts = []
    for i in range(ing.full_files_per_day + ing.half_files_per_day + 3):
        a = eco.ingest_reward(cfg, "image", False, paid, i)
        amounts.append(a)
        paid += a
    assert amounts[0] == ing.image
    assert 0 < amounts[ing.full_files_per_day] < ing.image  # the half tier really pays
    assert amounts[-1] == 0 and paid <= ing.daily_cap


# ---------------------------------------------------------------- B5: milestones after packs and upgrades


@pytest.mark.upgrades_on
def test_an_upgrade_that_crosses_a_milestone_makes_it_claimable(db):
    u = local_user(db)
    m = media(db, 1)[0]  # a one-card collection: every step is a big share of completeness
    tag_all(db, "solo", [m])
    own(db, u, m, "holo")
    credit(db, u, 5000, "test")
    db.commit()
    before = {t.dedupe_key for t in db.scalars(select(Transaction)).all()}
    res = chase.attempt_upgrade(db, u, m.id, FixedRng(0.0))  # holo -> special = 100% complete
    assert res["success"] and res["milestones"] and all(m["reward"] for m in res["milestones"])
    after = {t.dedupe_key for t in db.scalars(select(Transaction)).all()}
    # v0.9.0: nothing is paid by itself. The milestones wait until the player claims them, as packs.
    assert not any(k and k.startswith("milestone:") for k in after - before)
    coins = u.coins
    claimed = rewards.claim_milestones(db, u)
    db.commit()
    assert claimed["milestones"] == [m["at"] for m in res["milestones"]] and claimed["packs"]
    assert u.coins == coins
    assert rewards.claim_milestones(db, u) == {"milestones": [], "packs": []}  # once


# ---------------------------------------------------------------- B6: missing cards


def test_sets_ignore_cards_whose_file_is_missing(db):
    u = local_user(db)
    items = media(db, cfg.sets.min_size + 1)
    for m in items[:-1]:
        own(db, u, m, "foil")
    items[-1].missing = True  # never owned, and its file is gone
    t = tag_all(db, "trip", items)
    s = next(x for x in chase.category_sets(db, u) if x["tag_id"] == t.id)
    assert s["size"] == cfg.sets.min_size
    assert next(x for x in s["tiers"] if x["treatment"] == "foil")["complete"] is True


@pytest.mark.upgrades_on
def test_missing_cards_cannot_be_sold_or_upgraded(db):
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal")
    m.missing = True
    db.commit()
    with pytest.raises(pack_svc.NotOwned):
        pack_svc.sell_card(db, u, m.id, "normal")
    with pytest.raises(chase.CannotUpgrade):
        chase.attempt_upgrade(db, u, m.id, FixedRng(0.0))
    assert copies(db) == {"normal": 1}


# ---------------------------------------------------------------- C10: forget an unavailable saved folder


def test_use_the_default_folder_clears_a_saved_folder_that_is_unavailable(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    db.add(AppState(key=folders.KEY, value=str(tmp_path / "unplugged-drive")))
    db.commit()
    folders.load_override(db)  # startup: the saved folder doesn't exist, so the default is used
    assert folders.saved_unavailable(db)
    folders.reset_media_dir(db, confirm=True)
    assert folders.saved_unavailable(db) is None and db.get(AppState, folders.KEY) is None
