"""v0.9.0, the server side of test round 6: a scan after the first import pays (P3), one tag rush a
day and not two in a row (P15), the price change per range (P10), numbered card names (P12), the
coin rule: coins only from scanning and tagging, every other reward is sealed packs (D1, P18),
milestones are claimed, not paid during a pack, the audit's numbers (P7), the same odds for every
pack (D4), the hint that can be wrong and the reveal order (D6), tag refunds (P9), the tagging
pause (P21) and a calmer market (P17)."""

import json
import random
import re
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.game import economy as eco
from app.game import market as mk
from app.game import mastery as ms
from app.game import packs as gp
from app.game import titles
from app.game.config import get_config
from app.main import app
from app.models import AppState, MediaItem, Pack, TagSlot, Transaction
from app.services import chase, offers, organize, rewards, scanner, tagpace
from app.services import packs as pack_svc
from app.services.wallet import credit, local_user
from tests.test_chase import media, own
from tests.test_scanner import make_png, make_pngs

cfg = get_config()
ROOT = Path(__file__).resolve().parents[1]


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


def coins(db) -> int:
    db.expire_all()
    return local_user(db).coins


def ledger(db, reason: str) -> list[Transaction]:
    db.expire_all()
    return list(db.scalars(select(Transaction).where(Transaction.reason == reason).order_by(Transaction.id)))


def sealed(db) -> int:
    return sum(rewards.inventory(db, local_user(db)).values())


# ---------------------------------------------------------------- P3: a scan pays for files added later


def test_files_added_minutes_after_the_first_import_are_paid_by_the_next_scan(tmp_media, db):
    """The bug: every scan inside the first import's five-minute window was free AND opened the
    window again, so a player who added files and scanned soon after choosing the folder got nothing."""
    make_pngs(tmp_media, 12)
    scanner.full_scan(tmp_media)  # the first import: free
    assert coins(db) == cfg.starting_coins
    window = float(db.get(AppState, scanner.INITIAL_UNTIL_KEY).value)
    assert window > time.time()  # the watcher's window is open: this is exactly when the scan paid nothing
    make_pngs(tmp_media, 3, start=100)
    report = scanner.full_scan(tmp_media)
    assert report["new"] == 3
    assert coins(db) == cfg.starting_coins + 3 * cfg.ingest.image
    assert [t.amount for t in ledger(db, "ingest")] == [cfg.ingest.image] * 3
    # a scan no longer opens the window again
    db.expire_all()
    assert float(db.get(AppState, scanner.INITIAL_UNTIL_KEY).value) == window


def test_the_scan_report_says_what_was_paid_and_why_it_was_less(tmp_media, db):
    ing = cfg.ingest
    make_pngs(tmp_media, 4)
    scanner.full_scan(tmp_media)
    first = scanner.get_status().last_report
    assert (first["coins"], first["coins_full"], first["coins_reason"]) == (0, 4 * ing.image, "first_import")

    make_pngs(tmp_media, 2, start=50)
    scanner.full_scan(tmp_media)
    paid = scanner.get_status().last_report
    assert (paid["coins"], paid["coins_full"], paid["coins_reason"]) == (2 * ing.image, 2 * ing.image, None)
    assert paid["ingest_day"] == {"paid": 2 * ing.image, "cap": ing.daily_cap, "files": 2,
                                  "full_files": ing.full_files_per_day, "half_files": ing.half_files_per_day}

    many = ing.full_files_per_day + ing.half_files_per_day + 5
    make_pngs(tmp_media, many, start=100)
    scanner.full_scan(tmp_media)
    capped = scanner.get_status().last_report
    assert capped["coins_full"] == many * ing.image
    assert capped["coins"] < capped["coins_full"] and capped["coins_reason"] == "daily_limit"
    assert capped["ingest_day"]["paid"] <= ing.daily_cap
    assert coins(db) == cfg.starting_coins + capped["ingest_day"]["paid"]

    scanner.full_scan(tmp_media)  # nothing new: nothing to explain
    nothing = scanner.get_status().last_report
    assert (nothing["coins"], nothing["coins_full"], nothing["coins_reason"]) == (0, 0, None)


def test_a_day_of_new_files_pays_for_one_to_two_packs():
    """Audit target 3: five new images are one Standard pack, fifteen are two."""
    pack = cfg.packs["standard"].base_cost
    paid, total = 0, []
    for n in range(20):
        paid += eco.ingest_reward(cfg, "image", False, paid, n)
        total.append(paid)
    assert total[4] == pack and total[14] == 2 * pack == cfg.ingest.daily_cap and total[19] == total[14]


def test_the_preview_does_not_promise_identical_copies_as_new_media(tmp_media, db):
    """A second copy of a file that is a card adds no card and pays nothing. The preview counted
    it as new media before every scan."""
    files = make_pngs(tmp_media, 5)
    scanner.full_scan(tmp_media)
    shutil.copy2(files[0], tmp_media / "the-same-again.png")
    make_png(tmp_media / "really-new.png", (1, 2, 3))
    p = scanner.preview(tmp_media)
    assert (p["new_media"], p["duplicate_media"], p["new_slots"]) == (1, 1, 3)
    assert (p["coins"], p["coins_full"], p["coins_reason"]) == (cfg.ingest.image, cfg.ingest.image, None)
    report = scanner.full_scan(tmp_media)
    assert (report["new"], report["duplicate"]) == (1, 1)
    assert scanner.get_status().last_report["coins"] == p["coins"]


def test_the_preview_says_the_first_import_is_free(tmp_media, db):
    make_pngs(tmp_media, 3)
    p = scanner.preview(tmp_media)
    assert (p["new_media"], p["coins"], p["coins_full"], p["coins_reason"]) == (3, 0, 3 * cfg.ingest.image, "first_import")


# ---------------------------------------------------------------- P15: one tag rush, not two in a row


def test_a_rush_just_before_midnight_is_not_followed_by_one_just_after(db):
    """A rush started shortly before midnight and a second one right after it, less than a minute
    after the first had ended. "Once a local day" alone allowed it."""
    u = local_user(db)
    day = date(2026, 11, 15)
    start = datetime(2026, 11, 15, 23, 49, 34).timestamp()
    offers.start_rush(db, u, now=start, today=day)
    after_midnight = datetime(2026, 11, 16, 0, 0, 22).timestamp()
    status = offers.rush(db, u, now=after_midnight, today=day + timedelta(days=1))
    assert not status["active"] and not status["available"]
    ready = datetime.fromisoformat(status["available_at"]).timestamp()
    assert ready == pytest.approx(start + cfg.tagging.rush_gap_hours * 3600)
    with pytest.raises(offers.RushUnavailable):
        offers.start_rush(db, u, now=after_midnight, today=day + timedelta(days=1))
    # the next day's rush is there once the gap has passed
    assert offers.start_rush(db, u, now=ready + 1, today=day + timedelta(days=1))["active"]


def test_the_rush_is_still_once_a_local_day(db):
    u = local_user(db)
    day = date(2026, 11, 15)
    start = datetime(2026, 11, 15, 9, 0).timestamp()
    offers.start_rush(db, u, now=start, today=day)
    late = datetime(2026, 11, 15, 22, 0).timestamp()  # 13 hours later, the same day
    status = offers.rush(db, u, now=late, today=day)
    assert not status["available"] and status["available_at"] is None
    with pytest.raises(offers.RushUnavailable):
        offers.start_rush(db, u, now=late, today=day)


def test_a_rush_started_before_this_version_counts_for_the_gap(db):
    """Only `rush_until` and `rush_day` were stored before: the start is the end minus the length."""
    u = local_user(db)
    start = datetime(2026, 11, 15, 23, 49, 34).timestamp()
    db.merge(AppState(key=f"rush_until:{u.id}", value=str(start + cfg.tagging.rush_minutes * 60)))
    db.merge(AppState(key=f"rush_day:{u.id}", value="2026-11-15"))
    db.commit()
    assert not offers.rush(db, u, now=start + 660, today=date(2026, 11, 16))["available"]


# ---------------------------------------------------------------- P10: the change per range


def test_changes_per_range_agree_with_the_history():
    per_day = round(24 / cfg.market.tick_hours)
    hist = [100] * (30 * per_day + 1)
    hist[0] = 80          # a month ago
    hist[-7 * per_day - 1] = 125   # a week ago
    hist[-per_day - 1] = 110       # a day ago
    hist[-2] = 103                 # the update before
    c = mk.changes(cfg, hist)
    assert c == {"update": round(100 / 103 - 1, 4), "day": round(100 / 110 - 1, 4),
                 "week": round(100 / 125 - 1, 4), "month": 0.25}
    assert c["day"] == round(mk.trend(hist), 4)  # `trend` is the 24-hour change, not the last update
    assert c["day"] < 0 < c["month"]  # "down today" and "up this month" are both true: the label must say which
    assert mk.changes(cfg, [50]) == {"update": 0.0, "day": 0.0, "week": 0.0, "month": 0.0}
    assert mk.changes(cfg, [50, 60])["month"] == 0.2  # a short history is measured from its oldest price


def test_cards_and_the_history_endpoint_carry_the_changes(client, db):
    items = media(db, 3)
    own(db, local_user(db), items[0], "normal")
    card = client.get(f"/api/media/{items[0].id}").json()
    assert set(card["changes"]) == {"update", "day", "week", "month"} and card["trend"] == card["changes"]["day"]
    hist = client.get(f"/api/market/cards/{items[0].id}/history").json()
    assert hist["changes"] == card["changes"]
    points = [p["price"] for p in hist["points"]]
    assert hist["changes"]["month"] == pytest.approx(points[-1] / points[max(0, len(points) - 121)] - 1, abs=1e-4)


# ---------------------------------------------------------------- P12 / D5: every card is "Card #000123"


def test_file_names_no_longer_make_names(client, db):
    assert titles.display_title("Summer holiday in Rome", None, 7) == "Card #000007"
    assert titles.display_title("IMG_1234", "", 12) == "Card #000012"
    assert titles.display_title("whatever", "  My cat  ", 12) == "My cat"
    db.add(MediaItem(path="trips/Summer holiday in Rome.jpg", sha256="a" * 64, kind="image"))
    db.commit()
    m = db.scalars(select(MediaItem)).one()
    own(db, local_user(db), m, "normal")
    card = client.get(f"/api/media/{m.id}").json()
    assert card["title"] == f"Card #{m.number:06d}" == f"Card {card['code']}" and card["title_custom"] is False
    assert card["file_name"] == "Summer holiday in Rome"
    # the file name still finds the card
    assert client.get("/api/media", params={"q": "holiday"}).json()["total"] == 1
    named = client.patch(f"/api/media/{m.id}", json={"title": "Rome"}).json()
    assert named["title"] == "Rome" and named["title_custom"] is True


# ---------------------------------------------------------------- D1: coins only from scanning and tagging


def test_only_the_named_reasons_pay_coins_in_the_code():
    """Every `credit(` call in the app, by its ledger reason. Scanning, tagging (a review counts as
    tagging work) and trades on the market pay coins; the welcome coins and the test credit are
    not rewards. Nothing else may pay."""
    allowed = {"ingest", "tag", "review", "sell", "offer", "starting_grant"}
    found = set()
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for call in re.finditer(r"(?<![\w.])credit\(", text):
            if text[max(0, call.start() - 4):call.start()] == "def ":
                continue
            args = text[call.end():call.end() + 400]
            reason = re.search(r'"([a-z_]+)"\s*[,)]', args)  # the reason: the first literal that is a whole argument
            assert reason, f"{path.name}: a credit() call without a literal reason"
            found.add(reason.group(1))
    assert found == allowed, found


def test_opening_packs_pays_nothing_and_milestones_wait_to_be_claimed(db):
    """P18: nothing lands in the wallet or the inventory while cards are revealed."""
    items = media(db, 4)
    u = local_user(db)
    credit(db, u, 20_000, "dev_test")
    db.commit()
    reached: list[dict] = []
    for n in range(30):
        before, packs_before = coins(db), sealed(db)
        r = pack_svc.open_pack(db, u, "premium", random.Random(n))
        assert coins(db) == before - r["cost"]            # the price, and nothing back
        assert sealed(db) == packs_before                 # no pack arrives by itself either
        assert r["coins"] == before - r["cost"]
        reached += r["milestones"]
    assert reached, "30 Premium packs over 4 cards reach milestones"
    assert all(set(m) == {"at", "reward"} and m["reward"][0]["pack"] in cfg.packs for m in reached)
    assert len({m["at"] for m in reached}) == len(reached)  # each is announced once
    assert not ledger(db, "milestone")
    status = rewards.milestones_status(db, u)
    assert [m["at"] for m in status["claimable"]] == sorted(m["at"] for m in reached)
    assert len(items) == 4


def test_milestones_are_claimed_once_and_pay_packs(client, db):
    items = media(db, 2)
    u = local_user(db)
    for m in items:
        for t in ("foil", "holo"):
            own(db, u, m, t)  # 4 of 6 slots: every milestone up to 60%
    wallet = client.get("/api/economy/wallet").json()
    waiting = wallet["milestones_claimable"]
    assert [m["at"] for m in waiting] == [m.at for m in cfg.milestones if m.at <= 4 / 6]
    assert wallet["next_milestone"]["at"] == 0.75 and wallet["next_milestone"]["reward"] == [{"pack": "premium", "count": 3}]
    expected = eco.pack_list(cfg, [m.reward for m in cfg.milestones if m.at <= 4 / 6])
    assert wallet["milestones_claimable_packs"] == expected
    assert client.get("/api/economy/chase").json()["milestones"]["claimable_packs"] == expected

    before = coins(db)
    r = client.post("/api/economy/milestones/claim")
    assert r.status_code == 200
    body = r.json()
    assert body["packs"] == expected and body["coins"] == before == coins(db)
    assert body["inventory"] == {p["pack"]: p["count"] for p in expected} | {
        k: 0 for k in cfg.packs if k not in {p["pack"] for p in expected}}
    assert body["milestones"] == [m["at"] for m in waiting]
    assert client.post("/api/economy/milestones/claim").status_code == 409
    assert client.get("/api/economy/wallet").json()["milestones_claimable"] == []
    assert all(t.amount == 0 for t in ledger(db, "milestone"))


def test_a_reached_milestone_stays_claimable_when_completeness_falls(db):
    items = media(db, 10)
    u = local_user(db)
    own(db, u, items[0], "foil")  # 1 of 30 slots: 3.3%, past 1% and 2%
    assert [m["at"] for m in rewards.note_milestones(db, u)] == [0.01, 0.02]
    db.commit()
    pack_svc.take_copy(db, u, items[0].id, "foil")  # sold: back to 0%
    db.commit()
    assert rewards.note_milestones(db, u) == []  # nothing new
    out = rewards.claim_milestones(db, u)
    db.commit()
    assert out == {"milestones": [0.01, 0.02], "packs": [{"pack": "standard", "count": 2}]}
    # and reaching them again is worth nothing
    own_again = db.scalars(select(MediaItem)).all()[1]
    own(db, u, own_again, "foil")
    assert rewards.note_milestones(db, u) == []
    assert rewards.claim_milestones(db, u) == {"milestones": [], "packs": []}


def test_a_milestone_paid_in_coins_before_this_version_stays_paid(db):
    items = media(db, 10)
    u = local_user(db)
    credit(db, u, 50, "milestone", dedupe_key=f"milestone:{u.id}:0.01")  # a v0.8.0 payout
    db.commit()
    own(db, u, items[0], "foil")
    assert [m["at"] for m in rewards.note_milestones(db, u)] == [0.02]
    assert rewards.claim_milestones(db, u)["milestones"] == [0.02]


def test_reward_packs_cost_nothing_and_do_not_raise_prices(db, monkeypatch):
    monkeypatch.setattr(cfg.inflation, "per_pack", 0.002)  # the price rise of v0.8.0, to see that a reward pack leaves it alone
    media(db, 6)
    u = local_user(db)
    prices = pack_svc.current_prices(db, u)
    rewards.grant_packs(db, u, [{"pack": "deluxe", "count": 2}, {"pack": "nope", "count": 1}], "milestone")
    db.commit()
    assert rewards.inventory(db, u) == {"standard": 0, "deluxe": 2, "premium": 0}
    before = coins(db)
    r = pack_svc.open_pack(db, u, "deluxe", random.Random(1), buy=False)
    assert (r["cost"], r["free"], r["source"]) == (0, True, "milestone")
    assert coins(db) == before and u.packs_opened == 0
    assert pack_svc.current_prices(db, u) == prices
    stored = db.scalars(select(Pack).where(Pack.opened_at.is_not(None))).one()
    assert stored.source == "milestone" and stored.cost == 0


def test_every_claim_answers_with_packs_and_leaves_the_coins(client, db, monkeypatch):
    """Goals, collector levels, sets and the checklist: `packs`, the new `inventory`, `coins` unchanged."""
    monkeypatch.setattr(cfg.goals, "daily_unlock_after_packs", 0)
    monkeypatch.setattr(cfg.goals, "unlock_after_packs", 0)
    items = media(db, 6)
    u = local_user(db)
    for m in items:
        own(db, u, m, "foil")
    for m in items[:5]:
        organize.add_tag(db, u, m.id, "cats")
        tagpace.reset()
    start = coins(db)

    def check(r, packs):
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["packs"] == packs and body["coins"] == start == coins(db)
        assert body["inventory"] == rewards.inventory(db, local_user(db))
        return body

    inv0 = sealed(db)
    step = next(s for s in client.get("/api/onboarding").json()["steps"] if s["id"] == "tag-5")
    assert step["reward"] == [{"pack": "standard", "count": 1}] and step["done"] and not step["claimed"]
    check(client.post("/api/onboarding/tag-5/claim"), step["reward"])

    tag_id = client.get("/api/tags").json()[0]["id"]
    tier = client.get("/api/economy/sets").json()[0]["tiers"][0]
    assert tier["treatment"] == "foil" and tier["reward"] == [{"pack": "standard", "count": 1}] and tier["complete"]
    check(client.post(f"/api/economy/sets/{tag_id}/claim", json={"treatment": "foil"}), tier["reward"])
    assert client.get("/api/economy/sets").json()[0]["tiers"][0]["reward"] == []

    mastery = client.get("/api/economy/chase").json()["mastery"]
    assert mastery["level"] == 1 and mastery["claimable"] == [{"level": 1, "reward": [{"pack": "standard", "count": 1}]}]
    assert mastery["claimable_packs"] == mastery["claimable"][0]["reward"] and mastery["next_reward"] == mastery["claimable_packs"]
    assert check(client.post("/api/economy/mastery/claim"), mastery["claimable_packs"])["levels"] == [1]

    daily = client.get("/api/economy/chase").json()["daily"]["goals"]
    done = [g for g in daily if g["progress"] >= g["target"]]
    assert all(g["reward"] == [{"pack": "standard", "count": 1}] for g in daily)
    for g in done:
        check(client.post("/api/economy/chase/claim", json={"scope": "daily", "id": g["id"]}), g["reward"])
        assert client.post("/api/economy/chase/claim", json={"scope": "daily", "id": g["id"]}).status_code == 409
    weekly = client.get("/api/economy/goals").json()["goals"]
    assert all(g["reward"] == [{"pack": "deluxe", "count": 1}] for g in weekly)
    assert sealed(db) == inv0 + 3 + len(done)
    # no coin was paid by any of it, and the ledger shows coin changes only
    assert all(t["amount"] != 0 for t in client.get("/api/economy/transactions").json())
    assert {t.reason for t in db.scalars(select(Transaction).where(Transaction.amount == 0))} >= {"onboarding", "set", "mastery"}


def test_the_check_in_pays_no_coins_and_unlocks_the_free_pack(client, db):
    media(db, 3)
    start = coins(db)
    first = client.post("/api/economy/checkin").json()
    assert first["checked_in"] is True and first["streak_days"] == 1 and first["coins"] == start
    assert first["free_pack"]["available"] is True
    assert "streak_bonus" not in first
    again = client.post("/api/economy/checkin").json()
    assert again["checked_in"] is False and again["coins"] == start
    assert not ledger(db, "streak")
    wallet = client.get("/api/economy/wallet").json()
    assert "next_streak_bonus" not in wallet and "pity" not in wallet and wallet["can_check_in"] is False
    claimed = client.post("/api/packs/free/claim").json()
    assert claimed["inventory"]["standard"] == 1 and coins(db) == start


def test_collector_levels_pay_standard_deluxe_and_premium_packs():
    assert ms.level_packs(cfg, [1, 2, 3, 4]) == [{"pack": "standard", "count": 4}]
    assert ms.level_packs(cfg, [5]) == [{"pack": "deluxe", "count": 1}]
    assert ms.level_packs(cfg, [10]) == [{"pack": "premium", "count": 1}]  # the bigger step wins
    assert ms.level_packs(cfg, range(1, 11)) == [
        {"pack": "standard", "count": 8}, {"pack": "deluxe", "count": 1}, {"pack": "premium", "count": 1}]
    assert ms.level_reward(cfg, 0) is None


def test_the_tours_pay_one_pack_when_all_of_them_are_finished(client, db):
    media(db, 1)
    tours = cfg.onboarding.tours
    start = coins(db)
    for t in tours[:-1]:
        assert client.put(f"/api/tutorials/progress/{t}", json={"status": "completed"}).json()["packs"] == []
    assert client.put(f"/api/tutorials/progress/{tours[-1]}", json={"status": "skipped"}).json()["packs"] == []
    status = client.get("/api/onboarding").json()["tours"]
    assert (status["completed"], status["claimed"], status["reward"]) == (len(tours) - 1, False, [{"pack": "standard", "count": 1}])
    last = client.put(f"/api/tutorials/progress/{tours[-1]}", json={"status": "completed"}).json()
    assert last["packs"] == [{"pack": "standard", "count": 1}] and last["inventory"]["standard"] == 1
    assert client.put(f"/api/tutorials/progress/{tours[0]}", json={"status": "completed"}).json()["packs"] == []
    assert coins(db) == start and sealed(db) == 1
    assert client.get("/api/onboarding").json()["tours"]["claimed"] is True


def test_a_second_tag_on_the_same_cards_does_not_pay_a_set_again(db):
    """Target 5, sets: a set pays only while more than half of its cards have not counted."""
    items = media(db, 12)
    u = local_user(db)
    for m in items:
        own(db, u, m, "foil")
    for m in items[:6]:
        organize.add_tag(db, u, m.id, "first")
        tagpace.reset()
    first = db.scalars(select(MediaItem)).all()
    tag_first = chase.category_sets(db, u)[0]["tag_id"]
    assert chase.claim_set(db, u, tag_first, "foil")["packs"] == [{"pack": "standard", "count": 1}]
    for m in first[:6]:
        organize.add_tag(db, u, m.id, "again")  # the same six cards under a new tag
        tagpace.reset()
    again = next(s for s in chase.category_sets(db, u) if s["name"] == "again")
    assert again["tiers"][0]["reward"] == [] and again["tiers"][0]["claimed"]
    with pytest.raises(chase.NotClaimable):
        chase.claim_set(db, u, again["tag_id"], "foil")
    for m in first[3:12]:
        organize.add_tag(db, u, m.id, "mixed")  # nine cards, six of them new to a set: more than half
        tagpace.reset()
    mixed = next(s for s in chase.category_sets(db, u) if s["name"] == "mixed")
    assert mixed["tiers"][0]["reward"] == [{"pack": "standard", "count": 1}]


# ---------------------------------------------------------------- P7: the audit's numbers


def test_one_tag_a_card_pays_for_the_next_standard_pack_through_half_the_library():
    """Target 1: the new cards of a Standard pack, tagged once at the plain Normal rate, pay its
    price while less than half of the library is pulled (rarer pulls and reviews come on top)."""
    pack = cfg.packs["standard"]
    assert eco.pack_cost(cfg, pack, 0, 0.0) == eco.pack_cost(cfg, pack, 500, 0.5) == pack.base_cost  # prices no longer rise
    for owned_share in (0.0, 0.25, 0.5):
        new_cards = pack.size * (1 - eco.duplicate_chance(cfg, owned_share))
        assert new_cards * eco.tag_reward(cfg, 0, 1.0) >= pack.base_cost, owned_share
    # target 2: two and three tags a card are clearly ahead
    one, two, three = (eco.card_tagging_value(cfg, n, 1.0) for n in (1, 2, 3))
    assert two >= one * 1.3 and three >= one * 1.45


def test_the_simulated_one_tag_player_no_longer_runs_dry():
    from tools import simulate as sim

    arch = sim.Archetype("marathon", 60 * 24 * 60, 1, 1)  # one tag a card, no time limit
    day = sim.Sim(cfg, 300, arch, "hoarder", 1, 0, 1).run(1).days[0]
    assert day.pulled == 1.0  # v0.8.0: out of coins with about half of a small library pulled


# ---------------------------------------------------------------- D4: the same odds for every pack


def test_a_dry_streak_changes_no_odds(db):
    assert cfg.pity.enabled is False and cfg.rarity_decay.max == 0
    pack = cfg.packs["standard"]
    assert eco.pity_multiplier(cfg, 500) == 1.0
    base = eco.slot_weights(cfg, pack, 0.0)
    assert eco.slot_weights(cfg, pack, 0.0, pity=4.0) == base  # even a caller that passes a boost
    assert eco.slot_weights(cfg, pack, 1.0) == base            # and completeness changes nothing either

    media(db, 40)
    u = local_user(db)
    credit(db, u, 100_000, "dev_test")
    db.merge(AppState(key=f"pity_dry:{u.id}", value="60"))
    db.commit()
    seen = 0
    for n in range(40):
        r = pack_svc.open_pack(db, u, "standard", random.Random(n))
        seen += sum(p["treatment"] in cfg.pity.treatments for p in r["pulls"])
        assert "pity" not in r and isinstance(r["dry_streak"], int)
    assert seen <= 12  # 200 cards at 2% each: about 4. With the old boost (×4 after 60 dry packs) about 15


def test_the_dry_streak_is_still_counted_for_the_glow(client, db):
    media(db, 10)
    u = local_user(db)
    credit(db, u, 5000, "dev_test")
    db.commit()
    streak = 0
    for n in range(12):
        r = pack_svc.open_pack(db, u, "standard", random.Random(n))
        streak = 0 if any(p["treatment"] in cfg.pity.treatments for p in r["pulls"]) else streak + 1
        assert r["dry_streak"] == streak
    assert client.get("/api/economy/wallet").json()["dry_streak"] == streak
    assert client.get("/api/economy/config").json()["pity_enabled"] is False


# ---------------------------------------------------------------- D6: the hint and the reveal order


def test_the_hint_is_right_most_of_the_time_and_wrong_sometimes():
    rng = random.Random(7)
    n = 20_000
    true_hits = sum(gp.roll_hint(cfg, True, rng) for _ in range(n)) / n
    false_hits = sum(gp.roll_hint(cfg, False, rng) for _ in range(n)) / n
    assert true_hits == pytest.approx(cfg.reveal.hint_true, abs=0.015)
    assert false_hits == pytest.approx(cfg.reveal.hint_false, abs=0.015)
    assert 0 < cfg.reveal.hint_false < cfg.reveal.hint_true < 1


def test_the_rarest_card_is_last_six_times_in_ten():
    pulls = [gp.Pull(i, t, "new") for i, t in enumerate(["normal", "normal", "normal", "foil", "holo"])]
    rng = random.Random(3)
    n = 10_000
    last = 0
    positions = set()
    for _ in range(n):
        order = gp.reveal_order(cfg, pulls, rng)
        assert sorted(p.media_id for p in order) == [0, 1, 2, 3, 4]
        at = [p.treatment for p in order].index("holo")
        positions.add(at)
        last += at == 4
        assert [p.media_id for p in order if p.treatment != "holo"] == [0, 1, 2, 3]  # the others keep their order
    assert last / n == pytest.approx(cfg.reveal.rarest_last, abs=0.02)
    assert positions == {0, 1, 2, 3, 4}
    assert gp.reveal_order(cfg, pulls[:1], rng) == pulls[:1]


def test_the_hint_and_the_order_are_stored_with_the_pack_and_replayed(client, db):
    media(db, 30)
    u = local_user(db)
    credit(db, u, 50_000, "dev_test")
    db.commit()
    wrong = moved = 0
    tier = {t.id: t.tier for t in cfg.tiers()}
    for n in range(40):
        r = client.post("/api/packs/premium/open", params={"buy": "true"}, headers={"Idempotency-Key": f"k{n}"}).json()
        again = client.post("/api/packs/premium/open", params={"buy": "true"}, headers={"Idempotency-Key": f"k{n}"}).json()
        assert again["replayed"] is True
        key = [(p["media_id"], p["treatment"], p["hint"], p["fills_slot"]) for p in r["pulls"]]
        assert key == [(p["media_id"], p["treatment"], p["hint"], p["fills_slot"]) for p in again["pulls"]]
        for p in r["pulls"]:
            assert isinstance(p["hint"], bool) and p["shown"] == p["treatment"]
            assert p["treatment_name"] == cfg.treatments[p["treatment"]].name
            assert p["card"]["print_tier"]
            wrong += p["hint"] != p["fills_slot"]
        tiers = [tier[p["treatment"]] for p in r["pulls"]]
        moved += tiers[-1] != max(tiers)
    assert wrong > 0 and moved > 0
    stored = json.loads(db.scalars(select(Pack).order_by(Pack.id)).first().contents_json)
    assert all("hint" in p for p in stored["pulls"])


def test_a_pack_stored_before_this_version_replays_with_the_old_glow(db):
    items = media(db, 2)
    u = local_user(db)
    row = Pack(user_id=u.id, pack_type="standard", source="bought", cost=100, idem_key="old",
               opened_at=datetime.now(timezone.utc),
               contents_json=json.dumps({"pack": "standard", "cost": 100, "free": False, "pulls": [
                   {"media_id": items[0].id, "treatment": "foil", "outcome": "new", "fills_slot": True},
                   {"media_id": items[1].id, "treatment": "normal", "outcome": "new", "fills_slot": False}]}))
    db.add(row)
    db.commit()
    r = pack_svc.open_pack(db, u, "standard", idem_key="old")
    assert [p["hint"] for p in r["pulls"]] == [True, False] and r["source"] == "bought"


# ---------------------------------------------------------------- P9: removing a tag takes its coins back


def tagged_card(db, treatment="normal"):
    m = media(db, 1)[0]
    u = local_user(db)
    own(db, u, m, treatment)
    return u, m


def add(db, u, m, name, **kw):
    r = organize.add_tag(db, u, m.id, name, **kw)
    tagpace.reset()
    return r


def test_removing_a_tag_takes_back_what_it_paid_and_the_right_tag_pays_again(client, db):
    u, m = tagged_card(db)
    start = coins(db)
    first = add(db, u, m, "ctas")  # a typing error
    assert first["coins_awarded"] == cfg.tagging.first_tag
    r = client.delete(f"/api/media/{m.id}/tags/{first['tag']['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["refunded"] == cfg.tagging.first_tag and body["coins"] == start == coins(db)
    assert body["card"]["tags"] == [] and body["card"]["next_tag_reward"] == cfg.tagging.first_tag
    right = add(db, u, m, "cats")
    assert right["coins_awarded"] == cfg.tagging.first_tag and coins(db) == start + cfg.tagging.first_tag
    assert [t.amount for t in ledger(db, "tag_refund")] == [-cfg.tagging.first_tag]
    assert [t.dedupe_key for t in ledger(db, "tag_removed")] == [None]  # the key is free: the tag can be paid again
    assert len(ledger(db, "tag")) == 1


def test_the_slot_of_a_removed_tag_is_refilled_at_its_own_rate(db):
    """Three tags paid 30, 10 and 5. Removing the FIRST frees the 30-coin slot: the next tag is
    paid 30 again, not 2 (the fourth tag's rate), and not more."""
    u, m = tagged_card(db)
    rates = [cfg.tagging.first_tag, *cfg.tagging.extra_tags]
    paid = [add(db, u, m, n) for n in ("a", "b", "c")]
    assert [p["coins_awarded"] for p in paid] == rates[:3]
    start = coins(db)
    assert organize.remove_tag(db, u, m.id, paid[0]["tag"]["id"])["refunded"] == rates[0]
    card = organize.remove_tag(db, u, m.id, paid[2]["tag"]["id"])
    assert card["refunded"] == rates[2] and card["card"]["next_tag_reward"] == rates[0]
    assert add(db, u, m, "d")["coins_awarded"] == rates[0]   # the lowest free slot first
    assert add(db, u, m, "e")["coins_awarded"] == rates[2]
    assert add(db, u, m, "f")["coins_awarded"] == rates[3]   # then the next new slot
    assert coins(db) == start + rates[3]
    slots = db.scalars(select(TagSlot).where(TagSlot.media_id == m.id).order_by(TagSlot.slot)).all()
    assert [(s.slot, s.amount) for s in slots] == list(enumerate(rates))


def test_no_tag_remove_tag_loop_gains_a_coin(db, monkeypatch):
    """Target 5: plain, with a rarer copy pulled in between, with the tag rush started in between,
    and with the coins spent before the tag is removed."""
    u, m = tagged_card(db)
    start = coins(db)
    tag = add(db, u, m, "x")
    high = coins(db)
    assert high == start + cfg.tagging.first_tag
    for _ in range(5):
        organize.remove_tag(db, u, m.id, tag["tag"]["id"])
        tag = add(db, u, m, "x")
        assert coins(db) <= high

    # a rarer copy and the rush would make the same tag worth 30 × 3.5 × 2 = 210 as a first tag
    own(db, u, m, "special")
    offers.start_rush(db, u)
    assert offers.rush_active(db, u)
    for name in ("x", "y", "x"):
        organize.remove_tag(db, u, m.id, tag["tag"]["id"])
        tag = add(db, u, m, name)
        assert tag["coins_awarded"] <= cfg.tagging.first_tag and coins(db) <= high
    assert coins(db) == high

    # spend everything, remove the tag (nothing can be taken back), add it again: it pays nothing
    from app.services.wallet import debit

    debit(db, u, coins(db), "pack")
    db.commit()
    gone = organize.remove_tag(db, u, m.id, tag["tag"]["id"])
    assert gone["refunded"] == 0 and gone["coins"] == 0 and gone["card"]["next_tag_reward"] == 0
    for name in ("x", "z"):
        again = add(db, u, m, name)
        assert again["coins_awarded"] == 0 and coins(db) == 0
        organize.remove_tag(db, u, m.id, again["tag"]["id"])
    # the card is not dead: a tag in the next NEW slot is paid as usual
    add(db, u, m, "x")
    second = add(db, u, m, "more")
    assert second["coins_awarded"] > 0


def test_a_partial_refund_caps_what_the_slot_pays_next(db):
    u, m = tagged_card(db)
    tag = add(db, u, m, "x")
    from app.services.wallet import debit

    debit(db, u, coins(db) - 7, "pack")  # 7 coins left
    db.commit()
    assert organize.remove_tag(db, u, m.id, tag["tag"]["id"])["refunded"] == 7
    assert coins(db) == 0
    assert add(db, u, m, "x")["coins_awarded"] == 7


def test_a_card_tagged_before_this_version_refunds_from_the_ledger(db):
    """No stored slots yet: they are made from the card's "tag" payouts, in the order they were paid."""
    u, m = tagged_card(db)
    names = {}
    for i, (name, amount) in enumerate((("a", 10), ("b", 5), ("c", 3))):  # the v0.8.0 rates
        tag = organize._get_or_create(db, name)
        m.tags.append(tag)
        db.flush()
        credit(db, u, amount, "tag", dedupe_key=f"tag:{m.id}:{tag.id}", ref=f"media:{m.id}")
        names[name] = tag.id
    db.commit()
    assert db.scalars(select(TagSlot)).all() == []
    start = coins(db)
    assert organize.remove_tag(db, u, m.id, names["b"])["refunded"] == 5
    assert add(db, u, m, "b2")["coins_awarded"] == 5  # at most what was taken back, not today's 10
    assert coins(db) == start
    assert add(db, u, m, "d")["coins_awarded"] == eco.tag_reward(cfg, 3, 1.0)


def test_remove_and_add_again_cannot_farm_a_goal(db):
    """A tag removed in a goal period leaves no progress behind, and a slot paid on an earlier day
    counts for that day when it is refilled, not for today."""
    u, m = tagged_card(db)
    today = date.today()
    tag = add(db, u, m, "x")
    assert chase.day_counters(db, u, today).tags == 1
    organize.remove_tag(db, u, m.id, tag["tag"]["id"])
    assert chase.day_counters(db, u, today).tags == 0
    tag = add(db, u, m, "x")
    assert chase.day_counters(db, u, today).tags == 1  # one tag stands, one tag counts
    for _ in range(4):
        organize.remove_tag(db, u, m.id, tag["tag"]["id"])
        tag = add(db, u, m, "x")
    assert chase.day_counters(db, u, today).tags == 1

    # the same card tomorrow: yesterday's slot is refilled, today's goal does not move
    tomorrow = today + timedelta(days=1)
    for row in ledger(db, "tag"):
        row.created_at = row.created_at - timedelta(days=1)
    for slot in db.scalars(select(TagSlot)):
        slot.paid_at = slot.paid_at - timedelta(days=1)
    db.commit()
    assert chase.day_counters(db, u, today).tags == 0
    organize.remove_tag(db, u, m.id, tag["tag"]["id"])
    add(db, u, m, "y")
    assert chase.day_counters(db, u, today).tags == 0 and len(ledger(db, "tag")) == 1
    assert tomorrow > today


def test_removing_a_tag_that_was_never_paid_takes_nothing(db, monkeypatch):
    monkeypatch.setattr(cfg.tagging, "max_paid_tags", 1)
    u, m = tagged_card(db)
    add(db, u, m, "paid")
    free = add(db, u, m, "unpaid")
    assert free["coins_awarded"] == 0
    start = coins(db)
    assert organize.remove_tag(db, u, m.id, free["tag"]["id"])["refunded"] == 0
    assert organize.remove_tag(db, u, m.id, 99999)["refunded"] == 0  # not a tag at all
    assert coins(db) == start and not ledger(db, "tag_refund")


# ---------------------------------------------------------------- P21: the tagging pause


def test_seven_paid_tags_inside_eight_seconds_start_a_pause(monkeypatch):
    t = cfg.tagging
    assert (t.burst_tags, t.burst_seconds, t.cooldown_seconds) == (6, 8, 4)
    # one tag every 1.5 seconds, for a long time: never
    for i in range(200):
        assert tagpace.note_paid(now=1000 + i * 1.5) is False
    assert tagpace.status(now=1300)["must_type"] is False
    tagpace.reset()
    # six quick tags in a second are fine too (three quick keys on two cards)
    assert not any(tagpace.note_paid(now=2000 + i * 0.2) for i in range(6))
    # the seventh within eight seconds of the first starts the pause
    assert tagpace.note_paid(now=2007.9) is True
    status = tagpace.status(now=2008)
    assert status["must_type"] is True
    assert datetime.fromisoformat(status["cooldown_until"]).timestamp() == pytest.approx(2007.9 + t.cooldown_seconds)
    assert tagpace.paused(now=2010) is True
    assert tagpace.paused(now=2007.9 + t.cooldown_seconds) is False  # it ends by itself
    assert tagpace.status(now=2012) == {"cooldown_until": None, "must_type": False}


def test_a_typed_tag_ends_the_pause(monkeypatch):
    for i in range(7):
        tagpace.note_paid(now=100 + i)
    assert tagpace.paused(now=107.5) is True
    assert tagpace.paused(typed=True, now=108) is False
    assert tagpace.paused(now=108.1) is False and tagpace.status(now=108.1)["must_type"] is False


def test_a_quick_key_pays_nothing_during_the_pause_and_a_typed_tag_does(client, db):
    items = media(db, 12)
    u = local_user(db)
    for m in items:
        own(db, u, m, "normal")
    replies = [client.post(f"/api/media/{m.id}/tags", json={"name": "x"}).json() for m in items[:8]]
    assert [r["coins_awarded"] for r in replies] == [cfg.tagging.first_tag] * 7 + [0]
    assert [r["must_type"] for r in replies] == [False] * 6 + [True, True]
    assert [r["paused"] for r in replies] == [False] * 7 + [True]
    until = datetime.fromisoformat(replies[6]["cooldown_until"])
    assert 0 < (until - datetime.now(timezone.utc)).total_seconds() <= cfg.tagging.cooldown_seconds
    paused_card = replies[7]["card"]
    assert [t["name"] for t in paused_card["tags"]] == ["x"]          # the tag is applied
    assert paused_card["next_tag_reward"] == cfg.tagging.first_tag    # and its slot is still to be earned
    before = coins(db)
    typed = client.post(f"/api/media/{items[8].id}/tags", json={"name": "x", "typed": True}).json()
    assert typed["coins_awarded"] == cfg.tagging.first_tag and typed["paused"] is False
    assert typed["must_type"] is False and typed["cooldown_until"] is None
    assert coins(db) == before + cfg.tagging.first_tag
    after = client.post(f"/api/media/{items[9].id}/tags", json={"name": "x"}).json()
    assert after["coins_awarded"] == cfg.tagging.first_tag  # quick keys pay again


def test_bulk_tagging_is_not_paused_and_does_not_count(client, db):
    items = media(db, 30)
    u = local_user(db)
    for m in items:
        own(db, u, m, "normal")
    r = client.post("/api/tags/bulk", json={"media_ids": [m.id for m in items[:20]], "name": "trip"}).json()
    one = bulk_pay(cfg.tagging.first_tag)
    assert r["applied"] == 20 and r["coins_awarded"] == 20 * one
    assert r["must_type"] is False and r["cooldown_until"] is None
    for m in items[20:27]:
        last = client.post(f"/api/media/{m.id}/tags", json={"name": "x"}).json()
    assert last["must_type"] is True  # the pause is on
    during = client.post("/api/tags/bulk", json={"media_ids": [m.id for m in items[27:]], "name": "trip"}).json()
    assert during["coins_awarded"] == 3 * one and during["must_type"] is True


# ---------------------------------------------------------------- the ruling on bulk tagging


def bulk_pay(rate: int) -> int:
    """What bulk tagging pays for a tag whose normal rate is `rate`."""
    return max(1, int(rate * cfg.tagging.bulk_multiplier)) if rate > 0 else 0


def test_bulk_tagging_pays_a_quarter_through_the_ledger(client, db):
    assert cfg.tagging.bulk_multiplier == 0.25
    assert client.get("/api/economy/config").json()["tagging"]["bulk_multiplier"] == 0.25
    items = media(db, 200)
    u = local_user(db)
    for m in items:
        own(db, u, m, "normal")
    before = coins(db)
    r = client.post("/api/tags/bulk", json={"media_ids": [m.id for m in items], "name": "trip"}).json()
    # 200 cards x one tag: 1,400 coins, not the 6,000 of the full rate
    assert r["coins_awarded"] == 200 * bulk_pay(cfg.tagging.first_tag) == 200 * 7
    assert r["coins"] == coins(db) == before + 200 * 7
    rows = db.scalars(select(Transaction).where(Transaction.reason == "tag")).all()
    assert len(rows) == 200 and {t.amount for t in rows} == {7}  # one ledger row a tag, each with its key
    assert len({t.dedupe_key for t in rows}) == 200
    # the same call again pays nothing
    again = client.post("/api/tags/bulk", json={"media_ids": [m.id for m in items[:5]], "name": "trip"}).json()
    assert again["coins_awarded"] == 0 and coins(db) == before + 200 * 7


def test_bulk_tagging_floor_and_refund(client, db):
    m = media(db, 1)[0]
    u = local_user(db)
    own(db, u, m, "normal")
    paid = []
    for i in range(1 + len(cfg.tagging.extra_tags) + 1):
        paid.append(client.post("/api/tags/bulk", json={"media_ids": [m.id], "name": f"t{i}"}).json()["coins_awarded"])
    rates = [cfg.tagging.first_tag, *cfg.tagging.extra_tags, cfg.tagging.floor]
    assert paid == [bulk_pay(x) for x in rates]
    assert paid[-2] == 1 and paid[-1] == 1  # a 2-coin tag and the 1-coin floor still pay 1 coin
    # removing a bulk tag takes back what it paid (a quarter), not the full rate
    card = client.get(f"/api/media/{m.id}").json()
    first = next(t for t in card["tags"] if t["name"] == "t0")
    before = coins(db)
    gone = client.delete(f"/api/media/{m.id}/tags/{first['id']}").json()
    assert gone["refunded"] == paid[0] and coins(db) == before - paid[0]
    # the freed slot pays at most what was taken back, also for a single (full-rate) tag
    back = client.post(f"/api/media/{m.id}/tags", json={"name": "right", "typed": True}).json()
    assert back["coins_awarded"] == paid[0]


def test_bulk_multiplier_is_validated():
    from app.game.config import Tagging

    base = cfg.tagging.model_dump()
    for bad in (-0.1, 1.5):
        with pytest.raises(ValueError):
            Tagging(**{**base, "bulk_multiplier": bad})
    assert Tagging(**{**base, "bulk_multiplier": 1}).bulk_multiplier == 1


# ---------------------------------------------------------------- P16, P17: the market


def simulated_days(n_cards: int, days: int, seed: int = 5):
    rng = random.Random(seed)
    cards = [mk.CardState(i, mk.roll_print_run(cfg, rng), mk.roll_popularity(cfg, rng)) for i in range(n_cards)]
    log_index, tick = 0.0, 1_000_000
    for _ in range(cfg.market.warmup_ticks):
        tick += 1
        log_index, _ = mk.advance(cfg, cards, seed, tick, log_index)
    moves, spikes = [], 0
    per_day = round(24 / cfg.market.tick_hours)
    for _ in range(days):
        before = [c.history[-1] for c in cards]
        for _ in range(per_day):
            tick += 1
            log_index, _ = mk.advance(cfg, cards, seed, tick, log_index)
        for c, b in zip(cards, before):
            if b >= 20:  # under 20 coins one coin is already 5%
                moves.append(abs(c.history[-1] / b - 1))
                spikes += c.history[-1] / b - 1 >= 0.5
    return cards, sorted(moves), spikes, math_exp(log_index)


def math_exp(x: float) -> float:
    import math

    return math.exp(x)


def test_quiet_days_are_quiet_and_spikes_are_rare():
    """P17. v0.8.0: a card moved 5.4% on a median day and more than 10% on 23 of 100 card-days."""
    cards, moves, spikes, _ = simulated_days(600, 60)
    assert moves[len(moves) // 2] < 0.03                     # the median day
    assert moves[int(len(moves) * 0.9)] < 0.07               # nine days in ten
    assert sum(1 for x in moves if x > 0.10) / len(moves) < 0.02
    assert spikes <= 20  # 60 days: about 10 at 6 hypes and 1 buyout a month (v0.8.0: about 90)
    assert all(cfg.market.factor_range[0] <= c.factor <= cfg.market.factor_range[1] for c in cards)


def test_the_normal_ladder_stays_in_the_range_of_a_real_set():
    """P16: the top Normal card of 2,000 costs tens of packs, the 10th a few, the median a fraction."""
    pack = cfg.packs["standard"].base_cost
    tops, tenths, medians = [], [], []
    for seed in range(1, 7):
        rng = random.Random(seed * 7919 + 1)
        fair = sorted((mk.fair_value(cfg, mk.roll_print_run(cfg, rng), mk.roll_popularity(cfg, rng))
                       for _ in range(2000)), reverse=True)
        tops.append(fair[0] / pack)
        tenths.append(fair[9] / pack)
        medians.append(fair[1000] / pack)
    assert 30 <= sum(tops) / 6 <= 150
    assert 3 <= sum(tenths) / 6 <= 15
    assert sum(medians) / 6 < 0.2
