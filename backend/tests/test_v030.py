"""v0.3.0 (test round 2): sealed packs in an inventory, real-booster odds, cards only from packs,
scanning only when asked, a faster import, and the checklist can be brought back."""

import random
import threading

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.game import economy as eco
from app.game.config import get_config
from app.main import app
from app.models import CollectionEntry, Pack, Transaction
from app.services import onboarding, scanner
from app.services import packs as pack_svc
from app.services.thumbs import thumb_path
from app.services.wallet import InsufficientFunds, credit, local_user
from tests.test_chase import media

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


# ---------------------------------------------------------------- odds like real boosters


def per_pack(pack, treatment: str) -> float:
    w = dict(eco.slot_weights(cfg, pack, 0))
    return 1 - (1 - w[treatment] / sum(w.values())) ** pack.size


def test_standard_pack_odds_match_real_boosters():
    assert STD.size == 5 and STD.base_cost == 100
    assert 1 / 3.5 < per_pack(STD, "foil") < 1 / 2.5      # a "hit" in about 1 pack of 3
    assert 1 / 16 < per_pack(STD, "holo") < 1 / 11        # about 1 pack of 13
    assert 1 / 65 < per_pack(STD, "special") < 1 / 45     # about 1 pack of 54


def test_deluxe_and_premium_roughly_double_and_triple_the_per_card_odds():
    def per_card(pack, t):
        w = dict(eco.slot_weights(cfg, pack, 0))
        return w[t] / sum(w.values())

    for t in ("foil", "holo", "special"):
        base = per_card(STD, t)
        assert 1.7 < per_card(cfg.packs["deluxe"], t) / base < 2.1
        assert 2.3 < per_card(cfg.packs["premium"], t) / base < 3.1
    assert cfg.inflation.max_multiplier == 1.2


# ---------------------------------------------------------------- the pack inventory


def test_buying_puts_sealed_packs_in_the_inventory_and_charges_each(db):
    u = local_user(db)
    media(db, 30)
    start = u.coins
    r = pack_svc.buy_packs(db, u, "standard", 3)
    assert r["bought"] == 3 and r["inventory"] == {"standard": 3, "deluxe": 0, "premium": 0}
    assert u.coins == start - r["cost"] and r["cost"] >= 3 * STD.base_cost
    assert u.packs_opened == 3  # bought packs raise the next price
    assert db.scalars(select(CollectionEntry)).all() == []  # nothing is rolled until a pack is opened
    rows = db.scalars(select(Pack)).all()
    assert all(p.opened_at is None and p.pack_type == "standard" and p.source == "bought" for p in rows)


def test_buying_is_all_or_nothing(db):
    u = local_user(db)
    media(db, 30)
    with pytest.raises(InsufficientFunds):
        pack_svc.buy_packs(db, u, "standard", 10)  # 500 welcome coins don't cover ten
    db.rollback()
    db.expire_all()
    assert local_user(db).coins == cfg.starting_coins
    assert pack_svc.inventory(db, u)["standard"] == 0 and local_user(db).packs_opened == 0
    with pytest.raises(ValueError):
        pack_svc.buy_packs(db, u, "standard", 0)
    with pytest.raises(ValueError):
        pack_svc.buy_packs(db, u, "standard", pack_svc.MAX_BUY + 1)


def test_a_repeated_purchase_with_the_same_key_charges_once(db):
    u = local_user(db)
    media(db, 30)
    first = pack_svc.buy_packs(db, u, "standard", 2, idem_key="K")
    again = pack_svc.buy_packs(db, u, "standard", 2, idem_key="K")
    assert first["bought"] == 2 and again["bought"] == 0 and again["replayed"] is True
    assert pack_svc.inventory(db, u)["standard"] == 2
    assert len(db.scalars(select(Transaction).where(Transaction.reason == "pack")).all()) == 2


def test_opening_takes_a_sealed_pack_and_charges_nothing_more(db):
    u = local_user(db)
    media(db, 30)
    pack_svc.buy_packs(db, u, "standard", 2)
    coins = u.coins
    r = pack_svc.open_pack(db, u, "standard", random.Random(1), buy=False)
    assert len(r["pulls"]) == STD.size and r["inventory"]["standard"] == 1
    assert u.coins >= coins and u.packs_opened == 2  # opening never charges or raises prices
    pack_svc.open_pack(db, u, "standard", random.Random(2), buy=False)
    with pytest.raises(pack_svc.NoSealedPack):
        pack_svc.open_pack(db, u, "standard", random.Random(3), buy=False)
    with pytest.raises(pack_svc.NoSealedPack):
        pack_svc.open_pack(db, u, "premium", random.Random(3), buy=False)  # a Standard can't open as Premium


def test_two_requests_cannot_open_the_same_sealed_pack(db):
    u = local_user(db)
    media(db, 40)
    pack_svc.buy_packs(db, u, "standard", 1)
    barrier = threading.Barrier(2)
    results = []

    def go():
        with SessionLocal() as s:
            barrier.wait()
            try:
                pack_svc.open_pack(s, local_user(s), "standard", random.Random(1), buy=False)
                results.append("opened")
            except pack_svc.NoSealedPack:
                results.append("none")

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["none", "opened"]
    db.expire_all()
    assert sum(e.copies for e in db.scalars(select(CollectionEntry)).all()) <= STD.size


def test_inventory_endpoints(client):
    with SessionLocal() as db:
        media(db, 30)
    assert client.get("/api/packs/inventory").json() == {"inventory": {"standard": 0, "deluxe": 0, "premium": 0}}
    assert client.post("/api/packs/standard/open").status_code == 409  # nothing sealed, and no ?buy=true
    r = client.post("/api/packs/standard/buy", json={"count": 2}, headers={"Idempotency-Key": "abc"})
    assert r.status_code == 200 and r.json()["inventory"]["standard"] == 2
    assert client.post("/api/packs/standard/buy", json={"count": 2}, headers={"Idempotency-Key": "abc"}).json()["bought"] == 0
    w = client.get("/api/economy/wallet").json()
    assert w["inventory"]["standard"] == 2 and w["cards_total"] == 30 and w["cards_owned"] == 0
    opened = client.post("/api/packs/standard/open").json()
    assert len(opened["pulls"]) == STD.size and opened["inventory"]["standard"] == 1
    w = client.get("/api/economy/wallet").json()
    assert 1 <= w["cards_owned"] <= STD.size and w["cards_tagged"] == 0
    assert sum(w["copies"].values()) == STD.size
    assert client.post("/api/packs/standard/buy", json={"count": 40}).status_code == 402
    assert client.post("/api/packs/standard/buy", json={"count": 0}).status_code == 422
    assert client.post("/api/packs/nope/buy").status_code == 404


def test_the_collection_lists_only_pulled_cards(client):
    with SessionLocal() as db:
        u = local_user(db)
        items = media(db, 20)
        credit(db, u, 1000, "test")
        db.commit()
        pack_svc.open_pack(db, u, "standard", random.Random(4))
    everything = client.get("/api/media?limit=500").json()["total"]
    pulled = client.get("/api/media?filter=pulled&limit=500").json()
    assert everything == len(items) == 20
    assert 1 <= pulled["total"] <= STD.size and all(c["copies"] > 0 for c in pulled["items"])


# ---------------------------------------------------------------- scanning only when asked


def test_nothing_scans_or_watches_by_default(tmp_path, monkeypatch):
    assert settings.watch_media is False
    d = tmp_path / "media"
    d.mkdir()
    monkeypatch.setattr(settings, "media_dir", d)
    monkeypatch.setattr(settings, "watch_media", False)
    Image.new("RGB", (10, 10), "red").save(d / "a.png")
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        assert scanner._bg_watcher is None and scanner._bg_thread is None  # startup started nothing
        assert c.get("/api/media?limit=1").json()["total"] == 0
        assert c.post("/api/media/scan").json() == {"started": True}  # the Scan button
        for _ in range(100):
            if c.get("/api/media?limit=1").json()["total"] == 1:
                break
            threading.Event().wait(0.05)
        assert c.get("/api/media?limit=1").json()["total"] == 1
        assert scanner._bg_watcher is None


def test_the_import_makes_no_thumbnails(tmp_path, monkeypatch):
    d = tmp_path / "media"
    d.mkdir()
    monkeypatch.setattr(settings, "media_dir", d)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    for i in range(3):
        Image.new("RGB", (64, 64), (i * 40, 10, 10)).save(d / f"{i}.png")
    scanner.full_scan(d)
    assert not list((tmp_path / "data").rglob("*.webp"))
    assert thumb_path  # thumbnails are made on first request (covered in test_thumbs)


# ---------------------------------------------------------------- the checklist


def test_a_hidden_checklist_can_be_brought_back(client):
    assert client.post("/api/onboarding/dismiss").json() == {"dismissed": True}
    assert client.get("/api/onboarding").json()["dismissed"] is True
    assert client.post("/api/onboarding/restore").json() == {"dismissed": False}
    assert client.get("/api/onboarding").json()["dismissed"] is False


def test_the_first_pack_comes_before_tagging_in_the_checklist(db):
    ids = [s["id"] for s in onboarding.checklist(db, local_user(db))["steps"]]
    assert ids.index("first-pack") < ids.index("tag-5")


def test_two_first_requests_at_once_create_one_player():
    barrier = threading.Barrier(4)
    seen, errors = [], []

    def go():
        with SessionLocal() as s:
            barrier.wait()
            try:
                seen.append(local_user(s).id)
            except Exception as e:  # noqa: BLE001 - the test reports any failure
                errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(set(seen)) == 1
    with SessionLocal() as s:
        assert local_user(s).coins == cfg.starting_coins  # the welcome coins were granted once
