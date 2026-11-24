"""Getting-started checklist and tour rewards."""

import hashlib
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.game import economy as eco
from app.game.config import get_config
from app.main import app
from app.models import CollectionEntry, MediaItem, Pack, Tag, Transaction
from app.services import onboarding as ob
from app.services.chase import NotClaimable
from app.services.wallet import debit, local_user

cfg = get_config()


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def media(db, n, missing=False):
    items = [MediaItem(path=f"o{i}.jpg", sha256=hashlib.sha256(f"o{i}-{missing}".encode()).hexdigest(),
                       kind="image", missing=missing) for i in range(n)]
    db.add_all(items)
    db.commit()
    return items


def tag(db, items, name="t"):
    t = db.query(Tag).filter_by(name=name).one_or_none() or Tag(name=name)
    db.add(t)
    for m in items:
        m.tags.append(t)
    db.commit()


def own(db, user, m, copies=1):
    db.add(CollectionEntry(user_id=user.id, media_id=m.id, treatment="normal", copies=copies))
    db.commit()


def step(name):
    return next(s for s in cfg.onboarding.steps if s.id == name)


def test_counters_media_excludes_missing(db):
    u = local_user(db)
    media(db, 2)
    media(db, 3, missing=True)
    assert ob.counters(db, u).media == 2


def test_counters_tagged_excludes_missing_and_untagged(db):
    u = local_user(db)
    live = media(db, 3)
    gone = media(db, 2, missing=True)
    tag(db, live[:2] + gone)
    assert ob.counters(db, u).tagged == 2


def test_counters_packs(db):
    u = local_user(db)
    u.packs_opened = 3  # three bought...
    db.add_all([Pack(user_id=u.id, pack_type="standard"),  # ...one still sealed
                Pack(user_id=u.id, pack_type="standard", contents_json='{"pulls": []}'),
                Pack(user_id=u.id, pack_type="standard", contents_json='{"pulls": []}')])
    db.commit()
    assert ob.counters(db, u).packs == 2  # only opened packs count


def test_counters_tagged_owned(db):
    u = local_user(db)
    a, b, c, d = media(db, 3) + media(db, 1, missing=True)
    tag(db, [a, b, d])
    own(db, u, a)
    own(db, u, b, copies=0)  # sold
    own(db, u, c)  # owned but untagged
    own(db, u, d)  # tagged and owned but missing
    assert ob.counters(db, u).tagged_owned == 1


def test_counters_upgrades(db):
    u = local_user(db)
    u.coins = 1000
    db.commit()
    debit(db, u, 10, "upgrade_attempt", ref="media:1:fail")
    debit(db, u, 10, "upgrade_attempt", ref="media:1:ok")
    db.add(Pack(user_id=u.id, contents_json=json.dumps({"pulls": [{"outcome": "upgraded"}, {"outcome": "new"}]})))
    db.add(Pack(user_id=u.id, contents_json="[]"))
    db.commit()
    assert ob.counters(db, u).upgrades == 3


def test_checklist_claim_once(db):
    """v0.9.0: a step pays sealed packs, never coins, and only some steps pay."""
    u = local_user(db)
    tag(db, media(db, 5))
    items = ob.checklist(db, u)["steps"]
    first = next(s for s in items if s["id"] == "tag-5")
    reward = eco.pack_list(cfg, [step("tag-5").reward])
    assert first["done"] and not first["claimed"] and first["reward"] == reward and reward
    start = u.coins
    out = ob.claim(db, u, "tag-5")
    assert out["packs"] == reward and out["inventory"][reward[0]["pack"]] == reward[0]["count"]
    assert out["coins"] == start and u.coins == start
    with pytest.raises(NotClaimable):
        ob.claim(db, u, "tag-5")
    assert u.coins == start
    again = next(s for s in ob.checklist(db, u)["steps"] if s["id"] == "tag-5")
    assert again["claimed"]


def test_a_step_without_a_reward_is_done_not_claimed(db):
    """v0.9.0: "add-media" pays nothing: it counts as claimed once done, and there is nothing to claim."""
    u = local_user(db)
    media(db, 1)
    first = next(s for s in ob.checklist(db, u)["steps"] if s["id"] == "add-media")
    assert step("add-media").reward is None
    assert first["done"] and first["claimed"] and first["reward"] == []
    with pytest.raises(NotClaimable):
        ob.claim(db, u, "add-media")
    assert u.coins == cfg.starting_coins


def test_claim_unfinished_409(db):
    u = local_user(db)
    with pytest.raises(NotClaimable):
        ob.claim(db, u, "tag-5")
    with pytest.raises(NotClaimable):
        ob.claim(db, u, "nope")
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        assert c.post("/api/onboarding/tag-5/claim").status_code == 409
        r = c.get("/api/onboarding")
        assert r.status_code == 200 and r.json()["complete"] is False


def test_dismiss_persists(db):
    assert ob.checklist(db, local_user(db))["dismissed"] is False
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        assert c.post("/api/onboarding/dismiss").status_code == 200
        assert c.get("/api/onboarding").json()["dismissed"] is True


TOURS_PACKS = eco.pack_list(cfg, [cfg.onboarding.tours_reward])


def test_tour_reward_only_when_every_tour_is_completed():
    """v0.9.0: no coins per tour. The tours' packs come once, with the last tour that counts."""
    tours = cfg.onboarding.tours
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        r = c.put(f"/api/tutorials/progress/{tours[0]}", json={"status": "skipped"})
        assert r.json()["packs"] == [] and r.json()["status"] == "skipped"
        for tid in tours[:-1]:
            r = c.put(f"/api/tutorials/progress/{tid}", json={"status": "completed"})
            assert r.json()["packs"] == [] and r.json()["inventory"] is None
        status = c.get("/api/onboarding").json()["tours"]
        assert status["completed"] == len(tours) - 1 and not status["claimed"] and status["reward"] == TOURS_PACKS
        r = c.put(f"/api/tutorials/progress/{tours[-1]}", json={"status": "completed"})
        assert r.json()["packs"] == TOURS_PACKS and TOURS_PACKS
        assert r.json()["inventory"][TOURS_PACKS[0]["pack"]] == TOURS_PACKS[0]["count"]
        r = c.put(f"/api/tutorials/progress/{tours[-1]}", json={"status": "completed"})
        assert r.json()["packs"] == []  # once
        assert c.get("/api/onboarding").json()["tours"]["claimed"] is True
        assert c.get("/api/economy/wallet").json()["coins"] == cfg.starting_coins  # never coins


def test_tour_reward_completed_skipped_completed_pays_once():
    tours = cfg.onboarding.tours
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        for tid in tours[:-1]:
            c.put(f"/api/tutorials/progress/{tid}", json={"status": "completed"})
        got = [c.put(f"/api/tutorials/progress/{tours[-1]}", json={"status": s}).json()["packs"]
               for s in ("completed", "skipped", "completed")]
        assert got == [TOURS_PACKS, [], []]


def test_tour_reward_only_for_listed_tours(db):
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        for tid in ("a1", "a2", "setup"):
            r = c.put(f"/api/tutorials/progress/{tid}", json={"status": "completed"})
            assert r.json()["packs"] == []
        assert db.query(Transaction).filter(Transaction.reason == "tour").count() == 0
        assert c.get("/api/onboarding").json()["tours"]["completed"] == 0  # unlisted tours do not count


def test_claim_http_happy_path(db):
    tag(db, media(db, 5))
    reward = eco.pack_list(cfg, [step("tag-5").reward])
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        r = c.post("/api/onboarding/tag-5/claim")
        assert r.status_code == 200
        assert r.json()["packs"] == reward  # v0.9.0: packs
        assert r.json()["inventory"][reward[0]["pack"]] == reward[0]["count"]
        assert r.json()["coins"] == cfg.starting_coins
        assert c.post("/api/onboarding/tag-5/claim").status_code == 409
        assert c.post("/api/onboarding/add-media/claim").status_code == 409  # a step without a reward


def test_economy_config_exposes_tour_reward():
    with TestClient(app) as c:
        assert c.get("/api/economy/config").json()["onboarding"] == {
            "tours": list(cfg.onboarding.tours), "tours_reward": TOURS_PACKS}


def test_shipped_onboarding_config_valid():
    ids = [s.id for s in cfg.onboarding.steps]
    assert len(ids) == len(set(ids)) >= 5
    assert all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", i) for i in ids)
    assert {s.kind for s in cfg.onboarding.steps} <= {"media", "tagged", "packs", "tagged_owned", "upgrades"}
    assert cfg.onboarding.tours_reward is not None and cfg.onboarding.tours_reward.pack in cfg.packs
    assert sum(1 for s in cfg.onboarding.steps if s.reward) == 2  # v0.9.0: only some steps pay, in packs
    assert len(set(cfg.onboarding.tours)) == len(cfg.onboarding.tours)
    assert "setup" not in cfg.onboarding.tours
