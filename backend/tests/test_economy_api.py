import hashlib
import random
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.game.config import get_config
from app.main import app
from app.models import AuditLog, CollectionEntry, MediaItem, Tag, Transaction
from app.services import packs as pack_svc
from app.services import rewards
from app.services.wallet import InsufficientFunds, credit, debit, local_user

cfg = get_config()


def seed_media(db, n=50):
    items = [
        MediaItem(path=f"m{i}.jpg", sha256=hashlib.sha256(str(i).encode()).hexdigest(), kind="image")
        for i in range(n)
    ]
    db.add_all(items)
    db.commit()
    return items


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def test_missing_media_excluded_from_pool_and_completeness(db):
    a, b = seed_media(db, 2)
    t = Tag(name="x")
    a.tags.append(t)
    b.tags.append(t)
    db.commit()
    assert rewards.tagged_share(db) == 1.0
    b.missing = True
    db.commit()
    assert rewards.tagged_share(db) == 1.0  # 1 tagged of 1 present
    u = local_user(db)
    with_missing = rewards.completeness(db, u)
    c = seed_media_extra(db)
    assert rewards.tagged_share(db) == 0.5  # untagged present item counts, missing tagged one doesn't
    assert {p.media_id for p in pack_svc._pool(db, u)} == {a.id, c.id}
    # completeness over {a} + missing b equals completeness over {a} alone
    db.delete(c)
    b.tags.clear()
    db.delete(b)
    db.commit()
    assert rewards.completeness(db, u) == with_missing


def seed_media_extra(db):
    m = MediaItem(path="extra.jpg", sha256=hashlib.sha256(b"extra").hexdigest(), kind="image")
    db.add(m)
    db.commit()
    return m


def test_local_user_gets_starting_coins_once(db):
    u = local_user(db)
    assert u.coins == cfg.starting_coins
    assert local_user(db).id == u.id
    grants = db.scalars(select(Transaction).where(Transaction.reason == "starting_grant")).all()
    assert len(grants) == 1


def test_ledger_dedupe_and_insufficient_funds(db):
    u = local_user(db)
    assert credit(db, u, 10, "test", dedupe_key="k1") is not None
    assert credit(db, u, 10, "test", dedupe_key="k1") is None  # can't pay twice
    assert credit(db, u, 0, "test") is None
    tx = debit(db, u, 5, "test")
    assert tx.balance_after == u.coins == cfg.starting_coins + 5
    with pytest.raises(InsufficientFunds):
        debit(db, u, 10**9, "test")


def test_tag_rewards_idempotent_and_diminishing(db):
    u = local_user(db)
    m = seed_media(db, 1)[0]
    tags = [Tag(name=f"t{i}") for i in range(6)]
    db.add_all(tags)
    db.commit()
    paid = [rewards.reward_tag(db, u, m, t.id) for t in tags]
    assert paid[0] == round(cfg.tagging.first_tag * cfg.tagging.unowned_factor)
    assert paid[-1] == 0
    assert rewards.reward_tag(db, u, m, tags[0].id) == 0  # re-tagging doesn't pay again


def test_owned_card_pays_multiplier_and_review_once(db):
    u = local_user(db)
    m = seed_media(db, 1)[0]
    db.add(CollectionEntry(user_id=u.id, media_id=m.id, treatment="holo", copies=1))
    t = Tag(name="x")
    db.add(t)
    db.commit()
    assert rewards.reward_review(db, u, m.id, "holo") == 0  # untagged: nothing to review yet
    m.tags.append(t)
    db.commit()
    assert rewards.reward_tag(db, u, m, t.id) == round(cfg.tagging.first_tag * cfg.treatments["holo"].multiplier)
    # A10b: the holo copy predates the tag, so it earns no review bonus...
    assert rewards.reward_review(db, u, m.id, "holo") == 0
    # ...only a shinier copy obtained after tagging does (first_pulled_at set explicitly: no sleeps).
    tag_tx = db.scalars(select(Transaction).where(Transaction.reason == "tag")).one()
    db.add(CollectionEntry(
        user_id=u.id, media_id=m.id, treatment="special", copies=1,
        first_pulled_at=tag_tx.created_at + timedelta(seconds=5),
    ))
    db.commit()
    assert rewards.reward_review(db, u, m.id, "special") > 0
    assert rewards.reward_review(db, u, m.id, "special") == 0


def test_ingest_reward_once_per_file(db):
    u = local_user(db)
    m = seed_media(db, 1)[0]
    if not cfg.ingest.reward_initial_scan:
        assert rewards.reward_ingest(db, u, m, initial_scan=True) == 0
    assert rewards.reward_ingest(db, u, m, initial_scan=False) == cfg.ingest.image
    assert rewards.reward_ingest(db, u, m, initial_scan=False) == 0


def test_daily_checkin_once_per_day(db):
    u = local_user(db)
    # v0.9.0: the check-in pays no coins (it unlocks the free daily pack); it says whether it counted
    before = u.coins
    assert rewards.daily_checkin(db, u, date(2026, 11, 10)) is True
    assert rewards.daily_checkin(db, u, date(2026, 11, 10)) is False
    assert rewards.daily_checkin(db, u, date(2026, 11, 11)) is True
    assert u.streak_days == 2 and u.coins == before


def test_open_pack_and_sell(db):
    u = local_user(db)
    seed_media(db, 50)
    credit(db, u, 1000, "test")
    start = u.coins
    res = pack_svc.open_pack(db, u, "standard", random.Random(4))
    assert res["cost"] == cfg.packs["standard"].base_cost
    assert len(res["pulls"]) == cfg.packs["standard"].size
    dup_coins = sum(p["coins"] for p in res["pulls"])
    assert u.coins == start - res["cost"] + dup_coins
    assert u.packs_opened == 1
    e = db.scalars(select(CollectionEntry).where(CollectionEntry.copies > 0)).first()
    copies = e.copies
    sold = pack_svc.sell_card(db, u, e.media_id, e.treatment)
    assert sold["price"] > 0 and sold["copies_left"] == copies - 1
    with pytest.raises(pack_svc.NotOwned):
        pack_svc.sell_card(db, u, 999999, "normal")


def test_open_pack_returns_cards(db):
    u = local_user(db)
    seed_media(db, 50)
    credit(db, u, 1000, "test")
    res = pack_svc.open_pack(db, u, "standard", random.Random(4))
    assert res["replayed"] is False
    for p in res["pulls"]:
        assert p["card"]["id"] == p["media_id"]
        assert p["is_new"] == (p["outcome"] == "new")
        got = p["upgraded_to"] if p["outcome"] == "upgraded" and p["upgraded_to"] else p["treatment"]
        if p["outcome"] in ("new", "better", "upgraded"):
            assert p["card"]["owned"].get(got) is True


def test_open_pack_idempotency_key_charges_once():
    from app.models import Pack

    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        with SessionLocal() as s:
            seed_media(s, 30)
            credit(s, local_user(s), 1000, "test")
            s.commit()
        h = {"Idempotency-Key": "abc-123"}
        with SessionLocal() as s:
            start = local_user(s).coins
        a = c.post("/api/packs/standard/open?buy=true", headers=h).json()
        b = c.post("/api/packs/standard/open?buy=true", headers=h).json()
        assert a["replayed"] is False and b["replayed"] is True
        # v0.9.0: opening a pack pays nothing. A Foil in a small library can reach a completeness
        # milestone: it is listed as claimable and the balance is the start minus the price
        assert a["coins"] == start - a["cost"]
        assert all(set(m) == {"at", "reward"} for m in a["milestones"])
        with SessionLocal() as s:
            assert len(s.scalars(select(AuditLog).where(AuditLog.event == "pack_replay")).all()) == 1
        assert [p["media_id"] for p in a["pulls"]] == [p["media_id"] for p in b["pulls"]]
        assert a["coins"] == b["coins"]
        with SessionLocal() as s:
            assert len(s.scalars(select(Pack)).all()) == 1
            assert local_user(s).packs_opened == 1
            assert len(s.scalars(select(Pack).where(Pack.idem_key == "abc-123")).all()) == 1


def _pack_debits(db):
    return len(db.scalars(select(Transaction).where(Transaction.reason == "pack")).all())


def test_idempotency_race_falls_back_to_replay(db, monkeypatch):
    from app.models import Pack

    u = local_user(db)
    seed_media(db, 30)
    credit(db, u, 1000, "test")
    first = pack_svc.open_pack(db, u, "standard", random.Random(1), idem_key="K")
    coins_after_first = u.coins
    real = pack_svc._find_by_key
    calls = []

    def skip_precheck(*a, **kw):
        calls.append(1)
        return None if len(calls) == 1 else real(*a, **kw)

    monkeypatch.setattr(pack_svc, "_find_by_key", skip_precheck)
    second = pack_svc.open_pack(db, u, "standard", random.Random(2), idem_key="K")
    assert second["replayed"] is True
    assert [p["media_id"] for p in second["pulls"]] == [p["media_id"] for p in first["pulls"]]
    assert len(db.scalars(select(Pack)).all()) == 1
    assert _pack_debits(db) == 1
    assert local_user(db).coins == coins_after_first


def test_idempotency_key_of_another_user_is_409_no_charge(db):
    from app.models import Pack, User

    u = local_user(db)
    seed_media(db, 30)
    credit(db, u, 1000, "test")
    pack_svc.open_pack(db, u, "standard", random.Random(1), idem_key="K")
    other = User(username="other", password_hash="x", coins=1000)
    db.add(other)
    db.commit()
    with pytest.raises(pack_svc.IdempotencyKeyInUse):
        pack_svc.open_pack(db, other, "standard", random.Random(1), idem_key="K")
    assert db.get(User, other.id).coins == 1000
    assert len(db.scalars(select(Pack)).all()) == 1


def test_open_without_header_charges_twice():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        with SessionLocal() as s:
            seed_media(s, 30)
            credit(s, local_user(s), 1000, "test")
            s.commit()
        a = c.post("/api/packs/standard/open?buy=true").json()
        b = c.post("/api/packs/standard/open?buy=true").json()
        assert a["replayed"] is False and b["replayed"] is False
        with SessionLocal() as s:
            assert _pack_debits(s) == 2


def test_idempotency_key_too_long_400():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        r = c.post("/api/packs/standard/open?buy=true", headers={"Idempotency-Key": "k" * 65})
        assert r.status_code == 400


def test_api_wallet_packs_and_ledger():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        w = c.get("/api/economy/wallet").json()
        assert w["coins"] == cfg.starting_coins
        assert w["pack_prices"]["standard"] == cfg.packs["standard"].base_cost
        assert c.post("/api/packs/standard/open?buy=true").status_code == 409  # no media yet
        assert c.post("/api/packs/nope/open").status_code == 404
        with SessionLocal() as db:
            seed_media(db, 30)
            credit(db, local_user(db), 1000, "test")
            db.commit()
        assert c.post("/api/packs/standard/open?buy=true").status_code == 200
        while c.post("/api/packs/standard/open?buy=true").status_code == 200:
            pass
        r = c.post("/api/packs/standard/open?buy=true")
        assert r.status_code == 402
        assert r.json()["detail"]["needed"] > r.json()["detail"]["balance"]
        tx = c.get("/api/economy/transactions?limit=5").json()
        assert tx and all("balance_after" in t for t in tx)
        assert c.post("/api/economy/checkin").json()["streak_days"] == 1
        assert {p["id"] for p in c.get("/api/economy/config").json()["packs"]} == set(cfg.packs)
