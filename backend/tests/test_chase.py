"""Late-game chase: upgrade attempts, weekly goals, category sets."""

import hashlib
import json
import random
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.game import economy as eco
from app.game import goals as gl
from app.game.config import get_config
from app.main import app
from app.models import CollectionEntry, MediaItem, Pack, Tag
from app.services import chase
from app.services.wallet import InsufficientFunds, credit, debit, local_user

cfg = get_config()
TODAY = date(2026, 11, 10)


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def media(db, n):
    items = [MediaItem(path=f"c{i}.jpg", sha256=hashlib.sha256(f"c{i}".encode()).hexdigest(), kind="image")
             for i in range(n)]
    db.add_all(items)
    db.commit()
    return items


def own(db, user, m, treatment, copies=1):
    db.add(CollectionEntry(user_id=user.id, media_id=m.id, treatment=treatment, copies=copies))
    db.commit()


class FixedRng:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


# ---------------------------------------------------------------- upgrades


@pytest.mark.upgrades_on
def test_upgrade_success_moves_copy_up(db):
    u = local_user(db)
    m = media(db, 200)[0]  # one Foil of 600 slots stays under the first milestone
    own(db, u, m, "normal")
    start = u.coins
    res = chase.attempt_upgrade(db, u, m.id, FixedRng(0.0))
    slots = res.pop("slots")  # v0.8.0: what the attempt did to the versions held
    assert slots["delta"] == 1 and slots["filled_now"] == [{"media_id": m.id, "treatment": "foil"}]
    assert res == {"success": True, "cost": cfg.upgrades.cost["normal"], "treatment": "foil", "coins": u.coins,
                   "milestones": []}
    assert u.coins == start - cfg.upgrades.cost["normal"]
    rows = {e.treatment: e.copies for e in db.scalars(select(CollectionEntry)).all()}
    assert rows == {"normal": 0, "foil": 1}


@pytest.mark.upgrades_on
def test_upgrade_failure_keeps_card_and_costs_coins(db):
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal")
    res = chase.attempt_upgrade(db, u, m.id, FixedRng(0.99))
    assert res["success"] is False and res["treatment"] == "normal"
    assert {e.treatment: e.copies for e in db.scalars(select(CollectionEntry)).all()} == {"normal": 1}


@pytest.mark.upgrades_on
def test_upgrade_rejects_unowned_top_tier_and_broke(db):
    u = local_user(db)
    a, b, c = media(db, 3)
    with pytest.raises(chase.CannotUpgrade):
        chase.attempt_upgrade(db, u, a.id)
    own(db, u, b, "special")
    with pytest.raises(chase.CannotUpgrade):
        chase.attempt_upgrade(db, u, b.id)
    own(db, u, c, "holo")  # costs 400
    debit(db, u, u.coins - 399, "test")  # one coin short
    with pytest.raises(InsufficientFunds):
        chase.attempt_upgrade(db, u, c.id)


# ---------------------------------------------------------------- weekly goals


def test_pick_goals_stable_per_week_and_varies():
    a = [g.id for g in gl.pick_goals(cfg, "2026-W46", 1)]
    assert a == [g.id for g in gl.pick_goals(cfg, "2026-W46", 1)]
    assert len(a) == min(cfg.goals.per_week, len(cfg.goals.templates))
    weeks = {tuple(g.id for g in gl.pick_goals(cfg, f"2026-W{w:02d}", 1)) for w in range(1, 20)}
    assert len(weeks) > 1


def test_goal_progress_kinds():
    c = gl.WeekCounters(upgrades=2, tags=7, reviews=1, opens=3)
    c.add_pull(cfg.treatments["foil"].tier)
    c.add_pull(cfg.treatments["holo"].tier)
    c.add_pull(cfg.treatments["normal"].tier)
    def goal(kind, treatment=None):
        return next(g for g in cfg.goals.templates if g.kind == kind and g.treatment == treatment)

    assert gl.goal_progress(cfg, goal("pull", "foil"), c) == 2  # foil + holo count as "foil or better"
    assert gl.goal_progress(cfg, goal("pull", "holo"), c) == 1
    assert gl.goal_progress(cfg, goal("upgrade"), c) == 2
    assert gl.goal_progress(cfg, goal("open"), c) == 3


def test_goals_locked_until_enough_packs(db):
    u = local_user(db)
    data = chase.weekly_goals(db, u)
    assert data["locked"] is (cfg.goals.unlock_after_packs > 0)
    if data["locked"]:
        assert data["goals"] == []
        with pytest.raises(chase.NotClaimable):
            chase.claim_goal(db, u, cfg.goals.templates[0].id)


def test_week_counters_and_claim(db, monkeypatch):
    u = local_user(db)
    u.packs_opened = cfg.goals.unlock_after_packs
    target = next(g for g in cfg.goals.templates if g.kind == "open")
    monkeypatch.setattr(gl, "pick_goals", lambda c, w, uid, **kw: [target])
    for _ in range(target.target):
        db.add(Pack(user_id=u.id, contents_json=json.dumps(
            {"pack": "standard", "cost": 0, "pulls": [{"treatment": "foil", "outcome": "new"}]})))
    db.commit()
    data = chase.weekly_goals(db, u, date.today())
    assert data["goals"][0]["progress"] == target.target and not data["goals"][0]["claimed"]
    before = u.coins
    # v0.9.0: a goal pays sealed packs, never coins
    out = chase.claim_goal(db, u, target.id)
    assert out["packs"] == eco.pack_list(cfg, [target.reward])
    assert out["inventory"][target.reward.pack] == target.reward.count
    assert out["coins"] == before and u.coins == before
    with pytest.raises(chase.NotClaimable):
        chase.claim_goal(db, u, target.id)  # once per week


def test_unfinished_goal_not_claimable(db):
    u = local_user(db)
    u.packs_opened = cfg.goals.unlock_after_packs
    g = gl.pick_goals(cfg, gl.week_key(date.today()), u.id)[0]
    with pytest.raises(chase.NotClaimable):
        chase.claim_goal(db, u, g.id)


# ---------------------------------------------------------------- category sets


def test_category_set_completes_and_pays_once(db):
    u = local_user(db)
    tag = Tag(name="travel")
    db.add(tag)
    items = media(db, cfg.sets.min_size)
    for m in items:
        m.tags.append(tag)
    db.commit()
    for m in items[:-1]:
        own(db, u, m, "foil")
    s = chase.category_sets(db, u)[0]
    foil = next(t for t in s["tiers"] if t["treatment"] == "foil")
    assert s["size"] == cfg.sets.min_size and foil["progress"] == cfg.sets.min_size - 1 and not foil["complete"]
    with pytest.raises(chase.NotClaimable):
        chase.claim_set(db, u, tag.id, "foil")
    own(db, u, items[-1], "holo")  # holo counts toward a foil set
    before = u.coins
    out = chase.claim_set(db, u, tag.id, "foil")  # v0.9.0: packs, no coins
    assert out["packs"] == gl.set_reward(cfg, cfg.sets.min_size, "foil") and out["packs"]
    assert out["inventory"][out["packs"][0]["pack"]] == out["packs"][0]["count"]
    assert out["coins"] == before
    with pytest.raises(chase.NotClaimable):
        chase.claim_set(db, u, tag.id, "foil")
    with pytest.raises(chase.NotClaimable):
        chase.claim_set(db, u, tag.id, "holo")  # not all holo yet


def test_small_tags_are_not_sets(db):
    u = local_user(db)
    tag = Tag(name="tiny")
    db.add(tag)
    for m in media(db, cfg.sets.min_size - 1):
        m.tags.append(tag)
    db.commit()
    assert chase.category_sets(db, u) == []


# ---------------------------------------------------------------- API


@pytest.mark.upgrades_on
def test_chase_api_endpoints():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        with SessionLocal() as s:
            u = local_user(s)
            m = media(s, 1)[0]
            own(s, u, m, "normal")
            credit(s, u, 1000, "test")
            s.commit()
            media_id = m.id
        r = c.post(f"/api/cards/{media_id}/upgrade")
        assert r.status_code == 200 and r.json()["cost"] == cfg.upgrades.cost["normal"]
        assert c.post("/api/cards/999999/upgrade").status_code == 409
        g = c.get("/api/economy/goals").json()
        assert g["locked"] is (cfg.goals.unlock_after_packs > 0)  # fresh player
        with SessionLocal() as s:
            local_user(s).packs_opened = cfg.goals.unlock_after_packs
            s.commit()
        g = c.get("/api/economy/goals").json()
        assert not g["locked"] and len(g["goals"]) == min(cfg.goals.per_week, len(cfg.goals.templates))
        assert c.post(f"/api/economy/goals/{g['goals'][0]['id']}/claim").status_code == 409  # not done
        assert c.get("/api/economy/sets").json() == []
        assert c.post("/api/economy/sets/1/claim", json={"treatment": "foil"}).status_code == 409
        assert c.get("/api/economy/config").json()["upgrades"]["chance"] == cfg.upgrades.chance


def test_seeded_random_upgrade_rate_matches_config():
    rng = random.Random(5)
    hits = sum(rng.random() < cfg.upgrades.chance for _ in range(10_000))
    assert abs(hits / 10_000 - cfg.upgrades.chance) < 0.02
