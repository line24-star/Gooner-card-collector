"""v0.6.0: the goal layer that keeps going in a finished library: goal kinds that need no untagged
cards, daily goals, stored picks, mastery, the next goal, and the endpoints for all of it."""

import json
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.game import economy as eco
from app.game import goals as gl
from app.game import mastery as ms
from app.game.config import EconomyConfig, GoalTemplate, get_config
from app.main import app
from app.models import AppState, AuditLog, CollectionEntry, Pack, Tag, Transaction
from app.services import chase
from app.services import packs as pack_svc
from app.services.wallet import credit, debit, local_user
from tests.test_chase import media, own
from tools import simulate as sim

cfg = get_config()
TODAY = date(2026, 11, 14)  # a Saturday
DAILY = [g for g in cfg.goals.templates if g.period == "day"]
WEEKLY = [g for g in cfg.goals.templates if g.period == "week"]


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client():
    with TestClient(app, headers={"X-MCO-Client": "1"}) as c:
        yield c


def template(goal_id):
    return next(g for g in cfg.goals.templates if g.id == goal_id)


def packs_of(goal_id):
    """A goal's reward as the payloads carry it (v0.9.0: packs)."""
    return eco.pack_list(cfg, [template(goal_id).reward])


def unlock(db, u, packs=None):
    u.packs_opened = max(cfg.goals.unlock_after_packs, cfg.goals.daily_unlock_after_packs) if packs is None else packs
    db.commit()


def only(monkeypatch, *goal_ids):
    """Make the picker hand out exactly these templates (each in its own period)."""
    chosen = [template(i) for i in goal_ids]
    monkeypatch.setattr(gl, "pick_goals", lambda c, key, uid, period="week", blocked=frozenset(), limits=None:
                        [g for g in chosen if g.period == period])


# ---------------------------------------------------------------- pure rules: goals


def test_the_config_has_daily_and_weekly_goals_of_the_new_kinds():
    # v0.9.0: goals pay sealed packs: a daily goal one Standard pack, a weekly goal a dearer one
    assert len(DAILY) >= 8 and all((g.reward.pack, g.reward.count) == ("standard", 1) for g in DAILY)
    assert all(cfg.packs[g.reward.pack].base_cost > cfg.packs["standard"].base_cost for g in WEEKLY)
    for kind in ("fuse", "sell", "market-coins"):
        assert any(g.kind == kind for g in DAILY) and any(g.kind == kind for g in WEEKLY)
    assert any(g.kind == "buy" for g in cfg.goals.templates)
    assert cfg.goals.per_day >= 2 and cfg.goals.daily_unlock_after_packs < cfg.goals.unlock_after_packs
    assert all(g.period == "week" for g in cfg.goals.templates[:6])  # the v0.2.0 goals are still weekly
    with pytest.raises(ValueError):
        GoalTemplate(id="x", title="x", kind="dance", target=1, reward={"pack": "standard"})
    with pytest.raises(ValueError):
        GoalTemplate(id="x", title="x", kind="fuse", period="month", target=1, reward={"pack": "standard"})
    with pytest.raises(ValueError):
        GoalTemplate(id="x", title="x", kind="fuse", target=1, reward=20)  # coins are no reward any more


def test_picks_are_stable_per_period_and_leave_out_blocked_kinds():
    day = gl.pick_goals(cfg, "2026-11-14", 1, period="day")
    assert len(day) == cfg.goals.per_day and all(g.period == "day" for g in day)
    assert day == gl.pick_goals(cfg, "2026-11-14", 1, period="day")
    week = gl.pick_goals(cfg, "2026-W46", 1)
    assert len(week) == cfg.goals.per_week and all(g.period == "week" for g in week)
    mixes = {tuple(g.id for g in gl.pick_goals(cfg, f"2026-11-{d:02d}", 1, period="day")) for d in range(1, 29)}
    assert len(mixes) > 5  # different days, different goals
    blocked = {"tag", "review", "upgrade", "buy"}
    for d in range(1, 29):
        picks = gl.pick_goals(cfg, f"2026-11-{d:02d}", 1, period="day", blocked=blocked)
        assert len(picks) == cfg.goals.per_day and not {g.kind for g in picks} & blocked
    # fewer templates than wanted: the player gets what there is
    all_but_fuse = {g.kind for g in DAILY} - {"fuse"}
    assert [g.kind for g in gl.pick_goals(cfg, "k", 1, period="day", blocked=all_but_fuse)] == ["fuse"]
    assert gl.pick_goals(cfg, "k", 1, period="day", blocked={g.kind for g in DAILY}) == []


def test_progress_of_the_new_kinds():
    c = gl.Counters(fuses=2, sells=7, market_coins=95, buys=1, upgrades=3)
    assert gl.goal_progress(cfg, template("fuse"), c) == 2
    assert gl.goal_progress(cfg, template("sell"), c) == 7
    assert gl.goal_progress(cfg, template("market-coins"), c) == 95
    assert gl.goal_progress(cfg, template("day-buy"), c) == 1
    assert gl.goal_progress(cfg, template("upgrade"), c) == 3
    assert gl.WeekCounters is gl.Counters


def test_a_day_is_the_local_calendar_day_and_a_week_ends_on_monday():
    start, end = gl.day_start(TODAY), gl.day_end(TODAY)
    assert start.tzinfo is not None and start.utcoffset() == timedelta(0)  # UTC, like the stored timestamps
    assert start.astimezone().replace(tzinfo=None) == datetime.combine(TODAY, time.min)  # local midnight
    assert end == gl.day_start(TODAY + timedelta(days=1)) and timedelta(hours=23) <= end - start <= timedelta(hours=25)
    assert gl.day_key(TODAY) == "2026-11-14" and gl.week_key(TODAY) == "2026-W46"
    # v0.7.1: the week ends at local midnight on Monday, like the day (it was 00:00 UTC)
    monday = datetime.combine(TODAY + timedelta(days=2), time.min).astimezone(timezone.utc)
    assert gl.week_end(TODAY) == monday and gl.week_start(TODAY) == gl.day_start(TODAY - timedelta(days=5))


def test_the_next_goal_is_a_claimable_one_first_then_the_closest():
    far = gl.NextGoal("weekly", "far", 1, 10, 200, "#/packs")
    near = gl.NextGoal("daily", "near", 4, 5, 20, "#/market")
    done = gl.NextGoal("set", "done", 5, 5, 60, "#/stats")
    step = gl.NextGoal("mastery", "step", 143, 160, 100, "#/stats", share=0.5)  # 143 of 160, but half of this level
    assert gl.closest([far, near, step]) is near
    assert gl.closest([far, near, done, step]) is done and done.done and not near.done
    assert gl.closest([far, step]) is step and step.closeness == 0.5
    assert gl.closest([]) is None
    assert gl.closest([near, gl.NextGoal("weekly", "tie", 8, 10, 1, "#/")]) is near  # a tie: the earlier one


# ---------------------------------------------------------------- pure rules: mastery


def test_mastery_points_levels_and_rewards():
    m = cfg.mastery
    assert ms.points(cfg, {"normal": 90, "foil": 12, "holo": 3, "special": 1}, 4) == (
        90 * m.points["normal"] + 12 * m.points["foil"] + 3 * m.points["holo"] + m.points["special"] + 4 * m.fuse_points)
    assert ms.points(cfg, {}, 0) == 0
    assert m.points["normal"] < m.points["foil"] < m.points["holo"] < m.points["special"]
    assert ms.level_at(cfg, 0) == 0 and ms.level_at(cfg, 1) == round(m.level_base)
    assert ms.level_at(cfg, 7) == round(m.level_base * 7 ** m.level_exponent)
    gaps = [ms.level_at(cfg, n + 1) - ms.level_at(cfg, n) for n in range(1, 120)]
    assert all(b >= a for a, b in zip(gaps, gaps[1:]))  # the levels get further apart
    for pts in list(range(0, 400)) + [5_000, 77_777, 1_000_000]:
        level = ms.level_for(cfg, pts)
        assert ms.level_at(cfg, level) <= pts < ms.level_at(cfg, level + 1)
    # v0.9.0: a level pays packs: `mastery.reward`, and at a multiple of a `reward_every` step that
    # step's pack instead (the biggest step that fits)
    assert ms.level_reward(cfg, 0) is None and ms.level_packs(cfg, [0]) == []
    assert ms.level_reward(cfg, 1) == m.reward
    for n in range(1, 101):
        steps = [s for s in m.reward_every if n % s.every == 0]
        want = max(steps, key=lambda s: s.every) if steps else m.reward
        assert ms.level_reward(cfg, n) == want
        assert ms.level_packs(cfg, [n]) == [{"pack": want.pack, "count": want.count}]
    top = max(m.reward_every, key=lambda s: s.every)
    assert cfg.packs[top.pack].base_cost > cfg.packs[m.reward.pack].base_cost  # the rarer step is the dearer pack
    assert sum(p["count"] for p in ms.level_packs(cfg, range(1, 11))) == 10  # one reward a level


def test_mastery_is_off_without_a_mastery_section():
    raw = cfg.model_dump()
    del raw["mastery"]
    plain = EconomyConfig.model_validate(raw)
    assert ms.points(plain, {"normal": 50}, 9) == 0 and ms.level_for(plain, 0) == 0 and ms.level_reward(plain, 3) is None
    raw["mastery"] = {"points": {"shiny": 1}}
    with pytest.raises(ValueError):
        EconomyConfig.model_validate(raw)


# ---------------------------------------------------------------- counters and the daily reset


def test_counters_count_fuses_sales_offers_and_purchases(db):
    u = local_user(db)
    credit(db, u, 12, "sell", ref="media:1:normal")
    credit(db, u, 30, "sell", ref="media:1:normal")
    credit(db, u, 50, "offer", ref="media:2:foil")
    debit(db, u, 40, "market_buy", ref="media:3")
    db.add(AuditLog(user_id=u.id, event="fuse", detail="media:1 3xnormal->foil"))
    db.commit()
    for c in (chase.day_counters(db, u, date.today()), chase.week_counters(db, u, date.today())):
        assert (c.sells, c.market_coins, c.buys, c.fuses, c.upgrades) == (3, 92, 1, 1, 1)


def test_fusing_and_selling_for_real_move_the_goals(db):
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal", copies=cfg.fuse.copies + 2)
    pack_svc.fuse(db, u, m.id, "normal")
    sold = pack_svc.sell_card(db, u, m.id, "normal", 2)
    c = chase.day_counters(db, u, date.today())
    assert c.fuses == 1 and c.sells == 2 and c.market_coins == sold["price"] > 0


def test_daily_goals_reset_at_local_midnight(db, monkeypatch):
    u = local_user(db)
    unlock(db, u)
    only(monkeypatch, "day-sell")
    today = date.today()
    midnight = gl.day_start(today)
    for at in (midnight - timedelta(seconds=1), midnight + timedelta(seconds=1), midnight + timedelta(seconds=2)):
        db.add(Transaction(user_id=u.id, amount=5, reason="sell", created_at=at))
    db.add(AuditLog(user_id=u.id, event="fuse", created_at=midnight - timedelta(minutes=5)))
    db.add(Pack(user_id=u.id, opened_at=midnight - timedelta(hours=1), contents_json=json.dumps(
        {"pulls": [{"treatment": "foil", "outcome": "new"}]})))
    db.commit()
    now = chase.day_counters(db, u, today)
    assert (now.sells, now.market_coins, now.fuses, now.opens) == (2, 10, 0, 0)  # the minute before midnight is yesterday
    before = chase.day_counters(db, u, today - timedelta(days=1))
    assert (before.sells, before.fuses, before.opens) == (3, 1, 1)
    d = chase.daily_goals(db, u, today)
    assert d["key"] == today.isoformat() and d["goals"][0]["progress"] == 2
    assert d["ends_at"] == gl.day_end(today).isoformat()
    tomorrow = chase.daily_goals(db, u, today + timedelta(days=1))
    assert tomorrow["key"] != d["key"] and tomorrow["goals"][0]["progress"] == 0  # a new day starts at zero


# ---------------------------------------------------------------- picking: locked, doable, stored


def test_daily_goals_unlock_before_weekly_goals(db):
    u = local_user(db)
    d, w = chase.daily_goals(db, u, TODAY), chase.period_goals(db, u, "week", TODAY)
    assert d["locked"] and w["locked"] and d["goals"] == [] and d["unlock_after_packs"] == cfg.goals.daily_unlock_after_packs
    assert db.scalars(select(AppState).where(AppState.key.like("goals:%"))).all() == []  # nothing is picked while locked
    unlock(db, u, cfg.goals.daily_unlock_after_packs)
    d, w = chase.daily_goals(db, u, TODAY), chase.period_goals(db, u, "week", TODAY)
    assert not d["locked"] and w["locked"] and d["packs_opened"] == cfg.goals.daily_unlock_after_packs
    assert len(d["goals"]) == cfg.goals.per_day
    assert set(d["goals"][0]) == {"id", "title", "kind", "target", "reward", "progress", "claimed"}


def test_kinds_the_player_cannot_progress_are_left_out(db):
    u = local_user(db)
    unlock(db, u)
    # nothing owned: no tag pays, nothing to review; no card listed: nothing to buy.
    # v0.7.1: "upgrade" counts fuses, which only need packs, so it is never left out.
    assert chase.blocked_kinds(db, u, "day") == {"tag", "review", "buy"}
    assert chase.blocked_kinds(db, u, "week") == {"tag", "review"}  # no weekly "buy" goal to ask about
    for n in range(40):
        day = TODAY + timedelta(days=n)
        kinds = {g["kind"] for g in chase.daily_goals(db, u, day)["goals"]}
        assert len(kinds) and not kinds & {"tag", "review", "buy"}

    a, b = media(db, 2)
    own(db, u, a, "normal")
    assert chase.blocked_kinds(db, u, "week") == set()  # a card with paying tags, below Special
    for i in range(cfg.tagging.max_paid_tags):  # every paying tag of the card is used up
        credit(db, u, 1, "tag", dedupe_key=f"tag:{a.id}:{i}", ref=f"media:{a.id}")
    db.commit()
    assert chase.blocked_kinds(db, u, "week") == {"tag"}
    db.query(CollectionEntry).delete()
    own(db, u, b, "special")
    assert chase.blocked_kinds(db, u, "week") == {"review"}  # all Special; b still has paying tags


@pytest.mark.price_steps  # v0.8.0: only cards past the first fuse price step are listed
def test_buy_goals_need_the_coins_for_the_cheapest_listed_card(db):
    u = local_user(db)
    for m in media(db, 30)[:3]:
        m.print_run, m.popularity = 140, 1.0  # worth trading (and too scarce to be reprinted)
    db.commit()
    debit(db, u, u.coins, "test")
    db.commit()
    assert "buy" in chase.blocked_kinds(db, u, "day")  # no coins: not even the cheapest listed card
    credit(db, u, 10_000_000, "test")
    db.commit()
    assert "buy" not in chase.blocked_kinds(db, u, "day")


def test_the_picks_of_a_period_are_stored_and_never_change(db, monkeypatch):
    u = local_user(db)
    unlock(db, u)
    a = media(db, 1)[0]
    own(db, u, a, "normal")
    first = [g["id"] for g in chase.daily_goals(db, u, TODAY)["goals"]]
    week = [g["id"] for g in chase.weekly_goals(db, u, TODAY)["goals"]]
    stored = {r.key: json.loads(r.value) for r in db.scalars(select(AppState).where(AppState.key.like("goals:%")))}
    assert stored == {f"goals:{u.id}:day:2026-11-14": first, f"goals:{u.id}:week:2026-W46": week}
    # whatever changes meanwhile (even the picker itself), the day's goals stay
    monkeypatch.setattr(gl, "pick_goals", lambda *a, **k: [])
    monkeypatch.setattr(chase, "blocked_kinds", lambda *a, **k: {g.kind for g in cfg.goals.templates})
    assert [g["id"] for g in chase.daily_goals(db, u, TODAY)["goals"]] == first
    assert [g["id"] for g in chase.weekly_goals(db, u, TODAY)["goals"]] == week
    assert [g["id"] for g in chase.weekly_goals(db, u, TODAY + timedelta(days=1))["goals"]] == week  # Sunday: same week
    monkeypatch.undo()
    # the next day picks again and drops yesterday's row; the week's row stays
    chase.daily_goals(db, u, TODAY + timedelta(days=1))
    keys = set(db.scalars(select(AppState.key).where(AppState.key.like("goals:%"))))
    assert keys == {f"goals:{u.id}:day:2026-11-15", f"goals:{u.id}:week:2026-W46"}
    # a stored goal whose template is gone from the config is dropped, not an error
    db.merge(AppState(key=f"goals:{u.id}:day:2026-11-15", value=json.dumps(["no-such-goal", "day-sell"])))
    db.commit()
    assert [g["id"] for g in chase.daily_goals(db, u, TODAY + timedelta(days=1))["goals"]] == ["day-sell"]


# ---------------------------------------------------------------- claiming goals


def test_a_daily_goal_pays_once_and_only_today(db, monkeypatch):
    u = local_user(db)
    unlock(db, u)
    only(monkeypatch, "day-sell", "sell")
    goal = template("day-sell")
    today = date.today()
    with pytest.raises(chase.NotClaimable):
        chase.claim_goal(db, u, goal.id, today, period="day")  # not done yet
    for _ in range(goal.target):
        credit(db, u, 4, "sell")
    db.commit()
    before = u.coins
    out = chase.claim_goal(db, u, goal.id, today, period="day")
    # v0.9.0: packs into the inventory, no coins; the claim is a ledger row with amount 0
    assert out["packs"] == packs_of(goal.id) and out["inventory"][goal.reward.pack] == goal.reward.count
    assert out["coins"] == before and u.coins == before
    tx = db.scalars(select(Transaction).order_by(Transaction.id.desc())).first()
    assert tx.reason == "daily_goal" and tx.dedupe_key == f"goal:{u.id}:{today.isoformat()}:{goal.id}" and tx.amount == 0
    sealed = db.scalars(select(Pack).where(Pack.opened_at.is_(None), Pack.pack_type.is_not(None))).all()
    assert [(p.pack_type, p.source, p.cost) for p in sealed] == [(goal.reward.pack, "daily_goal", 0)] * goal.reward.count
    with pytest.raises(chase.NotClaimable):
        chase.claim_goal(db, u, goal.id, today, period="day")  # once
    with pytest.raises(chase.NotClaimable):
        chase.claim_goal(db, u, goal.id, today)  # not a weekly goal
    assert chase.daily_goals(db, u, today)["goals"][0]["claimed"] is True
    # the weekly goal of the same kind is its own goal, with its own progress and reward
    weekly = chase.weekly_goals(db, u, today)["goals"][0]
    assert weekly["id"] == "sell" and not weekly["claimed"]
    # tomorrow the same goal can be earned again
    assert chase.daily_goals(db, u, today + timedelta(days=1))["goals"][0]["claimed"] is False


# ---------------------------------------------------------------- mastery


def test_mastery_counts_best_treatments_and_fuses(db):
    u = local_user(db)
    a, b, c, gone = media(db, 4)
    own(db, u, a, "normal", copies=4)  # copies don't count, the best treatment does
    own(db, u, a, "holo")
    own(db, u, b, "foil")
    own(db, u, c, "special", copies=0)  # sold: not owned
    own(db, u, gone, "special")
    gone.missing = True  # a card whose file is gone doesn't count
    db.add(AuditLog(user_id=u.id, event="fuse"))
    db.add(AuditLog(user_id=u.id, event="fuse"))
    db.commit()
    s = chase.mastery_status(db, u)
    m = cfg.mastery
    assert s["cards"] == {"normal": 0, "foil": 1, "holo": 1, "special": 0} and s["fuses"] == 2
    assert s["points"] == m.points["foil"] + m.points["holo"] + 2 * m.fuse_points
    assert s["level"] == ms.level_for(cfg, s["points"])
    assert s["level_at"] <= s["points"] < s["next_at"] and s["next_reward"] == ms.level_packs(cfg, [s["level"] + 1])
    assert [x["level"] for x in s["claimable"]] == list(range(1, s["level"] + 1))
    assert [x["reward"] for x in s["claimable"]] == [ms.level_packs(cfg, [n]) for n in range(1, s["level"] + 1)]
    assert s["claimable_packs"] == ms.level_packs(cfg, range(1, s["level"] + 1))  # v0.9.0: was claimable_total, in coins


def test_mastery_levels_pay_once_even_when_the_points_drop_and_return(db):
    u = local_user(db)
    items = media(db, 6)
    with pytest.raises(chase.NotClaimable):
        chase.claim_mastery(db, u)  # level 0
    for m in items[:3]:
        own(db, u, m, "special")
    level = chase.mastery_status(db, u)["level"]
    assert level >= 2
    before = u.coins
    paid = chase.claim_mastery(db, u)
    assert paid["levels"] == list(range(1, level + 1))
    assert paid["packs"] == ms.level_packs(cfg, paid["levels"]) and u.coins == before == paid["coins"]  # packs, no coins
    assert sum(paid["inventory"].values()) == sum(p["count"] for p in paid["packs"]) == len(paid["levels"])
    assert {t.dedupe_key for t in db.scalars(select(Transaction).where(Transaction.reason == "mastery"))} == {
        f"mastery:{u.id}:{n}" for n in paid["levels"]}
    with pytest.raises(chase.NotClaimable):
        chase.claim_mastery(db, u)
    # the cards are sold: the level drops, nothing is taken back, nothing is claimable
    for e in db.scalars(select(CollectionEntry)):
        e.copies = 0
    db.commit()
    s = chase.mastery_status(db, u)
    assert s["level"] == 0 and s["points"] == 0 and s["claimable"] == []
    # back at the old level: already paid. One level further: only the new level pays
    for e in db.scalars(select(CollectionEntry)):
        e.copies = 1
    db.commit()
    assert chase.mastery_status(db, u)["claimable"] == []
    for m in items[3:]:
        own(db, u, m, "special")
    s = chase.mastery_status(db, u)
    assert s["level"] > level and [c["level"] for c in s["claimable"]] == list(range(level + 1, s["level"] + 1))
    coins = u.coins
    assert chase.claim_mastery(db, u)["packs"] == s["claimable_packs"] and u.coins == coins


def test_a_fuse_raises_mastery(db):
    u = local_user(db)
    m = media(db, 1)[0]
    own(db, u, m, "normal", copies=cfg.fuse.copies)
    before = chase.mastery_status(db, u)["points"]
    pack_svc.fuse(db, u, m.id, "normal")
    p = cfg.mastery.points
    assert chase.mastery_status(db, u)["points"] == before + p["foil"] - p["normal"] + cfg.mastery.fuse_points


# ---------------------------------------------------------------- the next goal


def test_there_is_a_next_goal_from_the_first_minute_to_the_finished_library(db, monkeypatch):
    u = local_user(db)
    items = media(db, 5)  # five Specials are just past a mastery level, so the next level is far away
    nxt = chase.overview(db, u, TODAY)["next"]  # nothing pulled, goals locked: a mastery level or a milestone
    assert nxt["kind"] in ("mastery", "milestone") and nxt["progress"] == 0 and nxt["reward"] and not nxt["claimable"]
    assert all(set(p) == {"pack", "count"} for p in nxt["reward"])  # v0.9.0: the reward is packs
    assert set(nxt) == {"kind", "title", "progress", "target", "reward", "href", "claimable"}

    # a finished, fully tagged library (every card in Foil, Holo and Special) with every milestone and
    # level paid: goals are what is left
    tag = Tag(name="trip")
    db.add(tag)
    for m in items:
        for t in ("foil", "holo", "special"):
            own(db, u, m, t)
        m.tags.append(tag)
    unlock(db, u)
    from app.services.rewards import claim_milestones

    assert chase.overview(db, u, TODAY)["milestones"]["claimable"]  # v0.9.0: reached milestones wait to be claimed
    claim_milestones(db, u)
    db.commit()
    chase.claim_mastery(db, u)
    for t in cfg.sets.treatments:
        chase.claim_set(db, u, tag.id, t)
    only(monkeypatch, "day-sell", "sell")
    o = chase.overview(db, u, date.today())
    span = o["mastery"]["next_at"] - o["mastery"]["level_at"]
    assert (o["mastery"]["points"] - o["mastery"]["level_at"]) / span < 0.5
    assert o["next"]["kind"] in ("daily", "weekly", "mastery")  # 0 of 3, 0 of 15, or the next level
    credit(db, u, 9, "sell")
    credit(db, u, 9, "sell")
    db.commit()
    nxt = chase.overview(db, u, date.today())["next"]
    assert (nxt["kind"], nxt["title"], nxt["progress"], nxt["target"]) == ("daily", template("day-sell").title, 2, 3)
    assert nxt["href"] == "#/market" and nxt["reward"] == packs_of("day-sell") and not nxt["claimable"]
    credit(db, u, 9, "sell")
    db.commit()
    nxt = chase.overview(db, u, date.today())["next"]
    assert nxt["kind"] == "daily" and nxt["claimable"] and nxt["href"] == "#/stats"
    chase.claim_goal(db, u, "day-sell", period="day")
    nxt = chase.overview(db, u, date.today())["next"]
    assert nxt["kind"] in ("weekly", "mastery") and not nxt["claimable"]  # the claimed goal is no longer "next"


def test_the_next_goal_can_be_a_set_a_milestone_or_a_waiting_mastery_level(db):
    u = local_user(db)
    items = media(db, 40)
    tag = Tag(name="trip")
    db.add(tag)
    for m in items[:5]:
        m.tags.append(tag)
    for m in items[:4]:
        own(db, u, m, "foil")
    db.commit()
    o = chase.overview(db, u, TODAY)
    assert o["mastery"]["claimable"] and o["next"]["kind"] == "mastery" and o["next"]["claimable"]  # claim first
    assert o["next"]["reward"] == o["mastery"]["claimable_packs"] and o["next"]["title"].startswith("Claim collector level")
    chase.claim_mastery(db, u)
    from app.services.rewards import claim_milestones

    claim_milestones(db, u)
    db.commit()
    kinds = set()
    nxt = chase.overview(db, u, TODAY)["next"]
    kinds.add(nxt["kind"])
    if nxt["kind"] == "set":
        assert nxt["title"] == 'Complete the set "trip" in Foil' and (nxt["progress"], nxt["target"]) == (4, 5)
    own(db, u, items[4], "foil")  # the set is complete: claiming it comes first
    claim_milestones(db, u)
    chase.claim_mastery(db, u) if chase.mastery_status(db, u)["claimable"] else None
    db.commit()
    nxt = chase.overview(db, u, TODAY)["next"]
    assert nxt["kind"] == "set" and nxt["claimable"] and nxt["reward"] == gl.set_reward(cfg, 5, "foil")
    chase.claim_set(db, u, tag.id, "foil")
    nxt = chase.overview(db, u, TODAY)["next"]
    assert nxt["kind"] in ("set", "milestone", "mastery") and not nxt["claimable"]
    if nxt["kind"] == "milestone":
        assert nxt["title"].endswith("% completeness") and 0 <= nxt["progress"] < nxt["target"] <= 100


# ---------------------------------------------------------------- endpoints


def test_the_chase_endpoint_and_its_claims(client, monkeypatch):
    only(monkeypatch, "day-sell", "open")
    with SessionLocal() as db:
        u = local_user(db)
        for m in media(db, 3):
            own(db, u, m, "holo")
        for _ in range(3):
            credit(db, u, 7, "sell")
        db.commit()
    o = client.get("/api/economy/chase").json()
    assert set(o) == {"daily", "weekly", "mastery", "milestones", "next"}
    assert set(o["milestones"]) == {"claimable", "claimable_packs", "next"}  # v0.9.0
    assert o["daily"]["locked"] and o["weekly"]["locked"] and o["daily"]["goals"] == []
    assert set(o["daily"]) == set(o["weekly"]) == {"key", "locked", "unlock_after_packs", "packs_opened", "ends_at", "goals"}
    assert o["daily"]["key"] == date.today().isoformat() and o["weekly"]["key"] == gl.week_key(date.today())
    assert datetime.fromisoformat(o["daily"]["ends_at"]) == gl.day_end(date.today())
    assert datetime.fromisoformat(o["weekly"]["ends_at"]) == gl.week_end(date.today())
    assert set(o["mastery"]) == {"level", "points", "level_at", "next_at", "next_reward", "claimable", "claimable_packs",
                                 "cards", "fuses"}
    assert o["mastery"]["points"] == 3 * cfg.mastery.points["holo"] and o["mastery"]["cards"]["holo"] == 3
    assert o["next"]["kind"] in ("mastery", "milestone") and o["next"]["claimable"]
    assert client.post("/api/economy/chase/claim", json={"scope": "daily", "id": "day-sell"}).status_code == 409  # locked

    with SessionLocal() as db:
        unlock(db, local_user(db))
    o = client.get("/api/economy/chase").json()
    assert [g["id"] for g in o["daily"]["goals"]] == ["day-sell"] and [g["id"] for g in o["weekly"]["goals"]] == ["open"]
    assert o["daily"]["goals"][0]["progress"] == o["daily"]["goals"][0]["target"] == 3
    coins = client.get("/api/economy/wallet").json()["coins"]
    r = client.post("/api/economy/chase/claim", json={"scope": "daily", "id": "day-sell"})
    # v0.9.0: every claim answers {packs, inventory, coins}; the coins do not change
    assert r.status_code == 200 and r.json()["packs"] == packs_of("day-sell") and r.json()["coins"] == coins
    assert r.json()["inventory"]["standard"] == 1
    assert client.post("/api/economy/chase/claim", json={"scope": "daily", "id": "day-sell"}).status_code == 409  # once
    assert client.post("/api/economy/chase/claim", json={"scope": "weekly", "id": "open"}).status_code == 409  # not done
    assert client.post("/api/economy/chase/claim", json={"scope": "weekly", "id": "day-sell"}).status_code == 409
    assert client.post("/api/economy/chase/claim", json={"scope": "daily", "id": "nope"}).status_code == 409
    assert client.post("/api/economy/chase/claim", json={"scope": "monthly", "id": "open"}).status_code == 422
    assert client.get("/api/economy/chase").json()["daily"]["goals"][0]["claimed"] is True

    m = client.post("/api/economy/mastery/claim")
    levels = [c["level"] for c in o["mastery"]["claimable"]]
    assert m.status_code == 200 and m.json()["levels"] == levels and m.json()["packs"] == o["mastery"]["claimable_packs"]
    assert m.json()["coins"] == coins
    assert sum(m.json()["inventory"].values()) == 1 + sum(p["count"] for p in m.json()["packs"])
    assert client.post("/api/economy/mastery/claim").status_code == 409
    assert client.get("/api/economy/chase").json()["mastery"]["claimable"] == []


def test_the_claims_need_the_client_header():
    with TestClient(app) as c:
        assert c.get("/api/economy/chase").status_code == 200
        assert c.post("/api/economy/mastery/claim").status_code == 403
        assert c.post("/api/economy/chase/claim", json={"scope": "daily", "id": "x"}).status_code == 403


def test_the_weekly_goals_endpoint_still_answers_as_before(client, monkeypatch):
    only(monkeypatch, "open")
    g = client.get("/api/economy/goals").json()
    assert g["locked"] and g["goals"] == [] and g["week"] == gl.week_key(date.today())
    assert g["unlock_after_packs"] == cfg.goals.unlock_after_packs and g["packs_opened"] == 0
    with SessionLocal() as db:
        u = local_user(db)
        unlock(db, u)
        for _ in range(template("open").target):
            db.add(Pack(user_id=u.id, contents_json=json.dumps({"pulls": [{"treatment": "normal", "outcome": "new"}]})))
        db.commit()
    g = client.get("/api/economy/goals").json()
    assert not g["locked"] and g["week"] == gl.week_key(date.today())
    assert g["goals"] == [{"id": "open", "title": template("open").title, "kind": "open", "target": template("open").target,
                           "reward": packs_of("open"), "progress": template("open").target, "claimed": False}]
    r = client.post("/api/economy/goals/open/claim")
    assert r.status_code == 200 and r.json()["packs"] == packs_of("open") and r.json()["coins"] == cfg.starting_coins
    assert client.post("/api/economy/goals/open/claim").status_code == 409
    # the same goal, seen through the new endpoint, is claimed there too
    assert client.get("/api/economy/chase").json()["weekly"]["goals"][0]["claimed"] is True
    assert client.post("/api/economy/chase/claim", json={"scope": "weekly", "id": "open"}).status_code == 409


def test_reset_clears_the_goal_picks_and_mastery_starts_over(client):
    with SessionLocal() as db:
        u = local_user(db)
        items = media(db, 6)
        for m in items:
            own(db, u, m, "special")
        unlock(db, u)
        chase.overview(db, u)
        chase.claim_mastery(db, u)
        assert db.scalars(select(AppState).where(AppState.key.like("goals:%"))).all()
    assert client.post("/api/account/reset", json={"confirm": "RESET"}).status_code == 200
    with SessionLocal() as db:
        u = local_user(db)
        assert db.scalars(select(AppState).where(AppState.key.like("goals:%"))).all() == []
        assert db.scalars(select(Transaction).where(Transaction.reason == "mastery")).all() == []
        o = chase.overview(db, u)
        assert o["daily"]["locked"] and o["mastery"]["points"] == 0 and o["mastery"]["level"] == 0
        for m in db.scalars(select(CollectionEntry)).all():
            raise AssertionError("no cards after a reset")
        from app.models import MediaItem

        for m in db.scalars(select(MediaItem)).all():
            own(db, u, m, "special")
        # like every other one-time reward, the levels can be earned again after a reset
        assert chase.claim_mastery(db, u)["levels"][0] == 1


# ---------------------------------------------------------------- the simulator


def test_the_simulator_pays_daily_goals_and_mastery_and_the_books_balance():
    """v0.9.0: daily goals and mastery levels pay sealed packs; only tags, reviews, ingest and sales pay coins."""
    res = sim.simulate(cfg, 150, "regular", "seller", days=20, seed=3)
    last = res.days[-1]
    assert set(last.earned) == set(sim.SOURCES) == {"tag", "review", "sell", "offer", "ingest"}
    assert last.reward_by["daily"] > 0 and last.reward_by["mastery"] > 0 and last.reward_by["goal"] >= 0
    assert last.reward_packs == sum(last.reward_by.values()) and set(last.reward_by) == set(sim.REWARDS)
    assert cfg.starting_coins + sum(last.earned.values()) - last.spent_packs - last.spent_upgrades == last.coins
    x = res.extras
    assert 0 < x["daily_claimed"] <= x["daily_offered"] <= 20 * cfg.goals.per_day
    assert last.reward_by["daily"] == x["daily_claimed"] * 1  # one Standard pack a daily goal
    level = res.left["mastery_level"]
    assert level == ms.level_for(cfg, res.left["mastery_points"]) > 0
    assert last.reward_by["mastery"] >= level  # every level reached paid its pack
    s = sim.summarize(res, 20)
    assert s["reward_packs_per_day"] == last.reward_packs / 20 > 0 and s["packs_per_day_last_week"] > 0
    assert s["reward_packs_per_day"] < s["packs_per_day"]  # reward packs are opened, and are not all the packs


def test_the_simulator_never_picks_goals_it_cannot_play():
    s = sim.Sim(cfg, 60, sim.ARCHETYPES["regular"], "seller", seed=2, new_media=0, days=15)
    s.run(15)
    offered = {k.split(":", 1)[1] for k in s.extras if k.startswith("offered:")}
    assert offered and "day-buy" not in offered  # buying on the market isn't modelled
    assert "buy" in s._blocked()
