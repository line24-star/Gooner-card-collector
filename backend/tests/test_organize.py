"""Tagging, categories, review, bulk, rate limiting and audit."""

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.game.config import get_config
from app.game.media import normalize_tag
from app.main import app
from app.models import AuditLog, CollectionEntry, MediaItem, Tag, Transaction
from app.services import organize, ratelimit
from app.services.ratelimit import TokenBucket
from app.services.wallet import local_user

cfg = get_config()
client = TestClient(app, headers={"X-MCO-Client": "1"})


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _fresh_bucket():
    ratelimit.reset_organize_bucket()
    yield
    ratelimit.reset_organize_bucket()


def add(db, name, pulled=True, **kw):
    """A card. Pulled (owned as Normal) by default: only cards you own can be organized."""
    m = MediaItem(path=name, sha256=hashlib.sha256(name.encode()).hexdigest(), kind="image", **kw)
    db.add(m)
    db.commit()
    if pulled:
        own(db, m, "normal")
    return m


def own(db, media, treatment):
    u = local_user(db)
    row = db.scalar(select(CollectionEntry).where(
        CollectionEntry.user_id == u.id, CollectionEntry.media_id == media.id, CollectionEntry.treatment == treatment))
    if row is None:
        db.add(CollectionEntry(user_id=u.id, media_id=media.id, treatment=treatment, copies=1))
    db.commit()


def test_normalize_tag():
    assert normalize_tag("  Summer   Trip ") == "summer trip"
    with pytest.raises(ValueError):
        normalize_tag("   ")
    with pytest.raises(ValueError):
        normalize_tag("x" * 41)
    assert normalize_tag("x" * 40) == "x" * 40


def test_add_tag_pays_and_is_idempotent(db):
    m = add(db, "a.jpg")
    user = local_user(db)
    r = organize.add_tag(db, user, m.id, "Beach")
    assert r["coins_awarded"] == cfg.tagging.first_tag  # a pulled Normal card pays the full first tag
    assert r["tag"]["name"] == "beach"
    assert [t["name"] for t in r["card"]["tags"]] == ["beach"]
    again = organize.add_tag(db, user, m.id, "beach")
    assert again["coins_awarded"] == 0


def test_remove_and_readd_never_gains_a_coin(db):
    """v0.9.0: removing a tag takes back what it paid; adding it again pays again, never more."""
    m = add(db, "a.jpg")
    user = local_user(db)
    start = user.coins
    r = organize.add_tag(db, user, m.id, "beach")
    assert r["coins_awarded"] > 0
    out = organize.remove_tag(db, user, m.id, r["tag"]["id"])
    assert out["refunded"] == r["coins_awarded"] and out["coins"] == start
    again = organize.add_tag(db, user, m.id, "beach")
    assert again["coins_awarded"] == r["coins_awarded"]
    assert user.coins == start + r["coins_awarded"]  # one tag on the card, paid once


def test_add_tag_with_parent_creates_hierarchy(db):
    m = add(db, "a.jpg")
    r = organize.add_tag(db, local_user(db), m.id, "beach", parent="Travel")
    travel = db.query(Tag).filter_by(name="travel").one()
    assert r["tag"]["parent_id"] == travel.id


def test_add_tag_unknown_or_missing_media(db):
    m = add(db, "gone.jpg", missing=True)
    with pytest.raises(organize.MediaNotFound):
        organize.add_tag(db, local_user(db), m.id, "x")
    assert client.post("/api/media/9999/tags", json={"name": "x"}).status_code == 404
    assert client.post(f"/api/media/{m.id}/tags", json={"name": "x"}).status_code == 404
    assert client.post(f"/api/media/{m.id}/review").status_code == 404


def test_add_tag_claims_milestones(db, monkeypatch):
    c = cfg.model_copy(deep=True)
    c.completeness.mode = "tagged"
    monkeypatch.setattr("app.services.rewards.get_config", lambda: c)
    items = [add(db, f"{i}.jpg") for i in range(10)]
    from app.services import rewards

    user = local_user(db)
    r = organize.add_tag(db, user, items[0].id, "beach")
    # v0.9.0: a tag pays its coins and nothing else. Milestones never pay by themselves: in this older
    # completeness mode (1 of 10 cards tagged = 10%) every milestone up to 10% becomes claimable,
    # and the reply lists them, as after a pack.
    assert [m["at"] for m in r["milestones"]] == [m.at for m in c.milestones if m.at <= 0.10]
    assert organize.add_tag(db, user, items[0].id, "sea")["milestones"] == []  # the same card: nothing new is crossed
    r = organize.add_tag(db, user, items[1].id, "beach")
    assert [m["at"] for m in r["milestones"]] == [m.at for m in c.milestones if 0.10 < m.at <= 0.20]
    assert rewards.note_milestones(db, user) == []
    before = user.coins
    claimed = rewards.claim_milestones(db, user)
    db.commit()
    assert claimed["milestones"] == [m.at for m in c.milestones if m.at <= 0.20] and claimed["packs"]
    assert user.coins == before  # packs, not coins


def test_review_pays_once(db):
    m = add(db, "a.jpg")
    user = local_user(db)
    own(db, m, "normal")
    organize.add_tag(db, user, m.id, "beach")
    assert organize.review(db, user, m.id)["coins_awarded"] == 0  # A10b: normal predates the tag
    tag_tx = db.scalars(select(Transaction).where(Transaction.reason == "tag")).one()
    db.add(CollectionEntry(
        user_id=user.id, media_id=m.id, treatment="foil", copies=1,
        first_pulled_at=tag_tx.created_at + timedelta(seconds=5),
    ))
    db.commit()  # (the Normal copy from add() predates the tag, as the comment above says)
    r = organize.review(db, user, m.id)
    assert r["coins_awarded"] == round(cfg.tagging.upgrade_review * cfg.treatments["foil"].multiplier)
    assert organize.review(db, user, m.id)["coins_awarded"] == 0


def test_bulk_tag_applies_and_caps(db):
    items = [add(db, f"{i}.jpg") for i in range(3)]
    r = organize.bulk_tag(db, local_user(db), [m.id for m in items] + [9999], "Trip")
    assert r["applied"] == 3
    # tag rewards, plus any completeness milestones the bulk crossed
    # v0.9.0: a bulk tag pays `bulk_multiplier` of the rate, at least 1 coin
    assert r["coins_awarded"] == 3 * max(1, int(cfg.tagging.first_tag * cfg.tagging.bulk_multiplier))
    too_many = list(range(1, cfg.limits.bulk_max_items + 2))
    with pytest.raises(organize.TooManyItems):
        organize.bulk_tag(db, local_user(db), too_many, "trip")
    resp = client.post("/api/tags/bulk", json={"media_ids": too_many, "name": "trip"})
    assert resp.status_code == 413


def test_bad_name_is_422(db):
    m = add(db, "a.jpg")
    assert client.post(f"/api/media/{m.id}/tags", json={"name": "   "}).status_code == 422
    assert client.post("/api/tags/bulk", json={"media_ids": [m.id], "name": "x" * 41}).status_code == 422


def test_routes_roundtrip(db):
    m = add(db, "a.jpg")
    r = client.post(f"/api/media/{m.id}/tags", json={"name": "beach"})
    assert r.status_code == 200
    tag_id = r.json()["tag"]["id"]
    gone = client.delete(f"/api/media/{m.id}/tags/{tag_id}")
    # v0.9.0: 200 with what was taken back (before: 204, nothing taken)
    assert gone.status_code == 200 and gone.json()["refunded"] == r.json()["coins_awarded"] > 0
    assert gone.json()["coins"] == r.json()["coins"] - gone.json()["refunded"] and gone.json()["card"]["tags"] == []
    assert client.get("/api/tags").json()[0]["count"] == 0


def test_economy_config_exposes_limits():
    assert client.get("/api/economy/config").json()["limits"] == {"bulk_max_items": cfg.limits.bulk_max_items}


def test_token_bucket():
    now = [0.0]
    b = TokenBucket(5, 2, clock=lambda: now[0])
    assert b.allow("k") and b.allow("k")
    assert not b.allow("k")
    now[0] += 0.2
    assert b.allow("k")
    assert not b.allow("k")


def test_rate_limited_returns_429_and_audits(db, monkeypatch):
    m = add(db, "a.jpg")
    monkeypatch.setattr(ratelimit, "organize_bucket", TokenBucket(0.001, 1))
    assert client.post(f"/api/media/{m.id}/tags", json={"name": "a"}).status_code == 200
    assert client.post(f"/api/media/{m.id}/tags", json={"name": "b"}).status_code == 429
    db.expire_all()
    row = db.query(AuditLog).filter_by(event="rate_limited").one()
    assert "tags" in row.detail


def test_tags_list_counts(db):
    a, b = add(db, "a.jpg"), add(db, "b.jpg")
    user = local_user(db)
    organize.add_tag(db, user, a.id, "beach")
    organize.add_tag(db, user, b.id, "beach")
    b.missing = True
    db.commit()
    rows = client.get("/api/tags").json()
    assert [(t["name"], t["count"]) for t in rows] == [("beach", 1)]


# ---------------------------------------------------------------- A10b: review only for copies gained after tagging
from datetime import datetime, timedelta, timezone  # noqa: E402

from app.services import cards, chase, rewards  # noqa: E402


def _set_tag_time(db, m, when):
    from sqlalchemy import select

    for tx in db.scalars(select(Transaction).where(Transaction.reason == "tag", Transaction.ref == f"media:{m.id}")):
        tx.created_at = when
    db.commit()


def _own_at(db, media, treatment, when, copies=1):
    u = local_user(db)
    e = db.scalar(select(CollectionEntry).where(
        CollectionEntry.user_id == u.id, CollectionEntry.media_id == media.id, CollectionEntry.treatment == treatment))
    if e is None:
        e = CollectionEntry(user_id=u.id, media_id=media.id, treatment=treatment)
        db.add(e)
    e.copies, e.first_pulled_at = copies, when
    db.commit()
    return e


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_no_review_for_treatment_owned_before_first_tag(db):
    m = add(db, "a.jpg")
    user = local_user(db)
    own(db, m, "normal")
    organize.add_tag(db, user, m.id, "beach")
    assert rewards.reward_review(db, user, m.id, "normal") == 0
    assert cards.card_out(db, user, m)["review_available"] is False
    assert cards.card_out(db, user, m)["review_reward"] is None


def test_review_after_shinier_pull_post_tag(db):
    m = add(db, "a.jpg")
    user = local_user(db)
    _own_at(db, m, "normal", T0)
    organize.add_tag(db, user, m.id, "beach")
    _set_tag_time(db, m, T0 + timedelta(days=1))
    _own_at(db, m, "foil", T0 + timedelta(days=2))
    c = cards.card_out(db, user, m)
    assert c["review_available"] is True
    assert c["review_reward"] == round(cfg.tagging.upgrade_review * cfg.treatments["foil"].multiplier)
    assert rewards.reward_review(db, user, m.id, "foil") == round(25 * 1.5)
    assert cards.card_out(db, user, m)["review_available"] is False
    assert cards.card_out(db, user, m)["review_reward"] is None


@pytest.mark.upgrades_on
def test_review_after_upgrade_attempt_post_tag(db):
    from tests.test_chase import FixedRng

    m = add(db, "a.jpg")
    user = local_user(db)
    _own_at(db, m, "normal", T0)
    organize.add_tag(db, user, m.id, "beach")
    _set_tag_time(db, m, T0 + timedelta(days=1))
    assert cards.card_out(db, user, m)["review_available"] is False
    chase.attempt_upgrade(db, user, m.id, FixedRng(0.0))
    assert rewards.review_eligible_treatment(db, user, m.id) == "foil"
    assert cards.card_out(db, user, m)["review_available"] is True


def test_review_resold_and_regained_after_tag_refreshes_pull_time(db):
    from app.services import packs

    m = add(db, "a.jpg")
    user = local_user(db)
    e = _own_at(db, m, "normal", T0)
    organize.add_tag(db, user, m.id, "beach")
    _set_tag_time(db, m, T0 + timedelta(days=1))
    e.copies = 0
    db.commit()
    packs._add_copy(db, user, m.id, "normal")
    db.commit()
    # v0.7.1: a Normal copy that came back is not "a shinier card": nothing to review
    assert rewards.review_eligible_treatment(db, user, m.id) is None
    # a Foil that was sold and came back after the tag is: the pull time was refreshed
    e.treatment = "foil"
    db.commit()
    assert rewards.review_eligible_treatment(db, user, m.id) == "foil"


def test_review_without_tag_transaction_counts_as_old_tag(db):
    m = add(db, "a.jpg")
    user = local_user(db)
    own(db, m, "holo")
    m.tags.append(Tag(name="x"))
    db.commit()
    assert cards.card_out(db, user, m)["review_available"] is True
    assert rewards.reward_review(db, user, m.id, "holo") > 0


def test_card_out_review_available_matches_reward_rule(db):
    user = local_user(db)
    ms = [add(db, f"{i}.jpg", pulled=i < 4) for i in range(5)]
    own(db, ms[0], "normal")                      # untagged
    _own_at(db, ms[1], "normal", T0)              # tagged after owning -> no
    _own_at(db, ms[2], "normal", T0)              # tagged, then shinier -> yes
    _own_at(db, ms[3], "holo", T0)                # tagged w/o tx -> yes
    # ms[4] unowned, tagged (a tag from before v0.3.0: an unowned card can't be tagged any more)
    for m in (ms[1], ms[2]):
        organize.add_tag(db, user, m.id, "beach")
        _set_tag_time(db, m, T0 + timedelta(days=1))
    ms[4].tags.append(db.scalar(select(Tag).where(Tag.name == "beach")))
    ms[3].tags.append(Tag(name="legacy"))
    db.commit()
    _own_at(db, ms[2], "foil", T0 + timedelta(days=2))
    got = {c["id"]: c["review_available"] for c in cards.card_out_many(db, user, ms)}
    want = {m.id: rewards.review_eligible_treatment(db, user, m.id) is not None for m in ms}
    assert got == want
    assert [got[m.id] for m in ms] == [False, False, True, True, False]


def test_a_card_that_was_never_pulled_cannot_be_tagged(db):
    m = add(db, "hidden.jpg", pulled=False)
    user = local_user(db)
    with pytest.raises(organize.NotPulled):
        organize.add_tag(db, user, m.id, "beach")
    r = client.post(f"/api/media/{m.id}/tags", json={"name": "beach"})
    assert r.status_code == 409 and "Pull this card" in r.json()["detail"]
    assert organize.bulk_tag(db, user, [m.id], "beach")["applied"] == 0
    assert db.scalars(select(Transaction).where(Transaction.reason == "tag")).all() == []
