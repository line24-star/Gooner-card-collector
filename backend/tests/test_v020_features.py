"""v0.2.0: rarity-ladder packs, soft pity, the daily free pack, the price cap and pull history."""

import random
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.game import economy as eco
from app.game import packs as pack_rules
from app.game.config import get_config
from app.main import app
from app.models import Pack, Transaction
from app.services import packs as pack_svc
from app.services import rewards
from app.services.wallet import credit, local_user
from tests.test_chase import media

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


# ---------------------------------------------------------------- rarity ladder


def test_packs_form_a_rarity_ladder_better_odds_cost_more_per_card():
    order = sorted(cfg.packs.values(), key=lambda p: p.base_cost / p.size)
    assert [p.id for p in order] == ["standard", "deluxe", "premium"]
    rare = []
    for p in order:
        w = dict(eco.slot_weights(cfg, p, 0))
        rare.append((w["holo"] + w["special"]) / sum(w.values()))
    assert rare == sorted(rare) and rare[0] < rare[1] < rare[2]
    assert "rediscover" not in cfg.packs


def test_every_card_in_the_group_is_equally_likely_tagged_or_not():
    """No pack leans toward untagged cards any more."""
    pool = [pack_rules.PoolItem(i, tagged=i < 50) for i in range(100)]
    rng = random.Random(5)
    tagged = total = 0
    for _ in range(600):
        for p in pack_rules.roll_pack(cfg, cfg.packs["standard"], pool, 0, rng):
            total += 1
            tagged += p.media_id < 50
    assert 0.45 < tagged / total < 0.55


def test_config_shows_each_packs_own_odds(client):
    packs = {p["id"]: p for p in client.get("/api/economy/config").json()["packs"]}
    assert packs["premium"]["odds"]["special"] > packs["deluxe"]["odds"]["special"] > packs["standard"]["odds"]["special"]
    assert sum(packs["premium"]["odds"].values()) == pytest.approx(1)


# ---------------------------------------------------------------- price cap


def test_pack_prices_stop_rising_at_the_cap(monkeypatch):
    std = cfg.packs["standard"]
    assert eco.pack_cost(cfg, std, 10_000, 0) == std.base_cost  # v0.9.0: the live price does not rise at all
    monkeypatch.setattr(cfg.inflation, "per_pack", 0.002)  # the rise and its cap are still there behind the number
    cap = cfg.inflation.max_multiplier
    assert cap > 1
    assert eco.inflation_multiplier(cfg, 10_000, 0) == cap
    assert eco.pack_cost(cfg, std, 10_000, 0) == eco.pack_cost(cfg, std, 100_000, 0) <= round(std.base_cost * cap) + cfg.inflation.round_to


# ---------------------------------------------------------------- soft pity


@pytest.fixture
def pity_on(monkeypatch):
    """The soft pity is switched off in the live config (v0.9.0, decision D4: every pack has the
    same odds). Its tests run with the switch on."""
    assert cfg.pity.enabled is False
    assert eco.pity_multiplier(cfg, 10_000) == 1.0  # live: no dry streak changes the odds
    monkeypatch.setattr(cfg.pity, "enabled", True)


def test_pity_multiplier_starts_after_the_dry_run_and_is_capped(pity_on):
    p = cfg.pity
    assert eco.pity_multiplier(cfg, 0) == 1
    assert eco.pity_multiplier(cfg, p.after_packs - 1) == 1
    assert eco.pity_multiplier(cfg, p.after_packs) == pytest.approx(1 + p.boost_per_pack)
    assert eco.pity_multiplier(cfg, p.after_packs + 1) > eco.pity_multiplier(cfg, p.after_packs)
    assert eco.pity_multiplier(cfg, 10_000) == p.max_boost


def test_pity_raises_only_the_pity_treatments(monkeypatch):
    std = cfg.packs["standard"]
    base = dict(eco.slot_weights(cfg, std, 0))
    assert dict(eco.slot_weights(cfg, std, 0, pity=2.0)) == base  # switched off: a multiplier changes nothing
    monkeypatch.setattr(cfg.pity, "enabled", True)
    boosted = dict(eco.slot_weights(cfg, std, 0, pity=2.0))
    assert boosted["holo"] == base["holo"] * 2 and boosted["special"] == base["special"] * 2
    assert boosted["normal"] == base["normal"] and boosted["foil"] == base["foil"]


def test_dry_packs_count_up_and_reset_on_a_holo(db, monkeypatch):
    u = local_user(db)
    media(db, 30)
    credit(db, u, 50_000, "test")
    db.commit()
    rolls = iter(["normal", "foil", "holo", "normal"])

    def fake_roll(cfg_, pack, pool, completeness, rng, pity=1.0):
        return [pack_rules.Pull(pool[0].media_id, next(rolls), "new")]

    monkeypatch.setattr(pack_svc, "roll_pack", fake_roll)
    seen = []
    for _ in range(4):
        seen.append(pack_svc.open_pack(db, u, "standard")["dry_streak"])  # v0.9.0: was pity.dry_packs
    assert seen == [1, 2, 0, 1]  # the streak is still counted with the pity off: it is for looks only


def test_the_boost_reaches_the_roll(db, monkeypatch, pity_on):
    u = local_user(db)
    media(db, 10)
    credit(db, u, 50_000, "test")
    db.commit()
    got = []

    def fake_roll(cfg_, pack, pool, completeness, rng, pity=1.0):
        got.append(pity)
        return [pack_rules.Pull(pool[0].media_id, "normal", "new")]

    monkeypatch.setattr(pack_svc, "roll_pack", fake_roll)
    for _ in range(cfg.pity.after_packs + 2):
        pack_svc.open_pack(db, u, "standard")
    assert got[: cfg.pity.after_packs] == [1.0] * cfg.pity.after_packs
    assert got[-1] > got[-2] > 1.0


# ---------------------------------------------------------------- daily free pack


def test_free_pack_needs_todays_check_in_and_is_one_per_day(db):
    u = local_user(db)
    media(db, 20)
    today = date(2026, 11, 12)
    assert pack_svc.free_pack_status(db, u, today) == {
        "pack": cfg.free_pack.pack, "claimed_today": False, "available": False, "needs_checkin": True}
    with pytest.raises(pack_svc.NoFreePack):
        pack_svc.open_free_pack(db, u, today=today)
    rewards.daily_checkin(db, u, today)
    db.commit()
    coins, opened = u.coins, u.packs_opened
    res = pack_svc.open_free_pack(db, u, random.Random(1), today=today)
    assert res["free"] is True and res["cost"] == 0 and len(res["pulls"]) == cfg.packs[cfg.free_pack.pack].size
    assert u.packs_opened == opened  # a free pack never raises prices
    assert u.coins >= coins  # nothing was charged (doubles may have paid)
    assert not db.scalars(select(Transaction).where(Transaction.reason == "pack")).all()
    assert pack_svc.free_pack_status(db, u, today)["claimed_today"] is True
    with pytest.raises(pack_svc.NoFreePack):
        pack_svc.open_free_pack(db, u, today=today)
    tomorrow = today + timedelta(days=1)
    assert pack_svc.free_pack_status(db, u, tomorrow)["needs_checkin"] is True  # a new day: check in again


def test_free_pack_endpoint(client):
    with SessionLocal() as db:
        media(db, 12)
    assert client.post("/api/packs/free/claim").status_code == 409  # not checked in yet
    assert client.post("/api/economy/checkin").json()["streak_days"] == 1
    w = client.get("/api/economy/wallet").json()
    assert w["free_pack"]["available"] is True and w["can_check_in"] is False
    r = client.post("/api/packs/free/claim")
    assert r.status_code == 200 and r.json()["inventory"]["standard"] == 1  # sealed, in the inventory
    assert client.post("/api/packs/free/claim").status_code == 409
    assert client.get("/api/economy/wallet").json()["free_pack"]["claimed_today"] is True
    opened = client.post("/api/packs/standard/open").json()
    assert opened["free"] is True and opened["cost"] == 0 and opened["inventory"]["standard"] == 0


def test_wallet_tells_the_check_in_and_the_dry_streak(client):
    """v0.9.0: the check-in pays no coins (no `next_streak_bonus`), and `dry_streak` replaces `pity`."""
    w = client.get("/api/economy/wallet").json()
    assert w["can_check_in"] is True and "next_streak_bonus" not in w
    assert w["dry_streak"] == 0 and "pity" not in w
    r = client.post("/api/economy/checkin").json()
    assert r["checked_in"] is True and r["streak_days"] == 1 and r["coins"] == w["coins"]
    assert client.post("/api/economy/checkin").json()["checked_in"] is False


# ---------------------------------------------------------------- history


def test_history_counts_packs_and_lists_near_misses(db, monkeypatch):
    u = local_user(db)
    items = media(db, 10)
    credit(db, u, 50_000, "test")
    db.commit()

    def fake_roll(cfg_, pack, pool, completeness, rng, pity=1.0):
        return [pack_rules.Pull(items[0].id, "foil", "new", near_miss=True),
                pack_rules.Pull(items[1].id, "normal", "duplicate", coins=8)]

    monkeypatch.setattr(pack_svc, "roll_pack", fake_roll)
    pack_svc.open_pack(db, u, "standard")
    pack_svc.open_pack(db, u, "premium")
    h = pack_svc.history(db, u)
    assert h["packs_today"] == 2 and h["packs_this_week"] == 2 and h["near_misses_this_week"] == 2
    latest = h["recent_packs"][0]
    assert latest["pack"] == "premium" and latest["cards"] == 2 and latest["best"] == "foil"
    assert latest["new"] == 1 and latest["duplicate"] == 1 and latest["near_misses"] == 1
    assert [m["treatment"] for m in h["near_misses"]] == ["foil", "foil"]
    assert h["near_misses"][0]["card"]["id"] == items[0].id
    assert db.scalars(select(Pack)).first().opened_at is not None


def test_history_of_a_new_player_is_empty(client):
    h = client.get("/api/economy/history").json()
    assert h == {"packs_today": 0, "packs_this_week": 0, "near_misses_this_week": 0, "recent_packs": [], "near_misses": []}


def test_the_free_pack_counts_for_the_first_pack_checklist_step(db):
    from app.services import onboarding

    u = local_user(db)
    media(db, 12)
    today = date(2026, 11, 12)
    rewards.daily_checkin(db, u, today)
    db.commit()
    assert onboarding.counters(db, u).packs == 0
    pack_svc.open_free_pack(db, u, random.Random(3), today=today)
    assert u.packs_opened == 0 and onboarding.counters(db, u).packs == 1
