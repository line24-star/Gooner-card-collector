"""Pure economy formulas against the shipped config/economy.toml."""

from datetime import date

import pytest

from app.game import economy as eco
from app.game.config import CONFIG_PATH, EconomyConfig, get_config, load

cfg = get_config()


def test_shipped_config_is_valid_and_matches_design_ranges():
    c = load(CONFIG_PATH)
    assert c.starting_coins >= c.packs["standard"].base_cost  # v0.1.1: welcome coins buy the first pack
    std = c.packs["standard"]
    # v0.9.0 (§2 said 8–12): coins come only from scanning and tagging, so one tag on every new card
    # of a Standard pack in a fresh library pays for the next pack
    assert std.size * (1 - c.duplicates.early_chance) * c.tagging.first_tag >= std.base_cost
    extra = c.tagging.extra_tags
    assert extra == sorted(extra, reverse=True) and c.tagging.floor <= extra[-1] and extra[0] < c.tagging.first_tag
    assert 80 <= std.base_cost / std.size * 5 <= 180  # §3 said 120–180 per 5 cards; v0.3.0 (test round 2): cheaper, 100
    assert 0.35 <= c.selling.base_rate <= 0.45  # §4
    assert 0.15 <= c.duplicates.early_chance <= 0.25  # §5
    assert c.duplicates.convert_rate < c.selling.base_rate  # duplicate coins < selling (§5)


def test_ingest_initial_scan_and_daily_cap():
    first_scan = eco.ingest_reward(cfg, "image", initial_scan=True, paid_today=0)
    assert first_scan == (cfg.ingest.image if cfg.ingest.reward_initial_scan else 0)
    assert eco.ingest_reward(cfg, "video", initial_scan=False, paid_today=0) == cfg.ingest.video
    assert eco.ingest_reward(cfg, "image", initial_scan=False, paid_today=cfg.ingest.daily_cap - 4) == 4
    assert eco.ingest_reward(cfg, "image", initial_scan=False, paid_today=cfg.ingest.daily_cap) == 0


def test_ingest_diminishing_returns_per_day():
    ing = cfg.ingest
    full, half = ing.full_files_per_day, ing.half_files_per_day
    assert eco.ingest_reward(cfg, "image", False, 0, files_today=full - 1) == ing.image
    assert eco.ingest_reward(cfg, "image", False, 0, files_today=full) == round(ing.image / 2)
    assert eco.ingest_reward(cfg, "image", False, 0, files_today=full + half) == 0


@pytest.mark.upgrades_on
def test_upgrade_cost():
    assert eco.upgrade_cost(cfg, "normal") == cfg.upgrades.cost["normal"]
    assert eco.upgrade_cost(cfg, "special") is None  # top tier


def test_tag_rewards_diminish_and_stop():
    t = cfg.tagging
    rewards = [eco.tag_reward(cfg, i, 1.0) for i in range(t.max_paid_tags + 2)]
    assert rewards[0] == t.first_tag
    assert rewards == sorted(rewards, reverse=True)
    assert rewards[len(t.extra_tags) + 1] == t.floor == 1  # past the list every tag still pays 1 coin…
    assert rewards[t.max_paid_tags - 1] == 1 and rewards[t.max_paid_tags] == 0  # …until the cap per card
    assert eco.tag_reward(cfg, len(t.extra_tags) + 1, 3.5) == 1  # the floor ignores the treatment
    assert eco.tag_reward(cfg, 0, 1.0, rush=True) == round(t.first_tag * t.rush_multiplier)


def test_tag_reward_multiplier_and_unowned_factor():
    assert eco.tag_reward(cfg, 0, 1.5) == round(cfg.tagging.first_tag * 1.5)
    expected = round(cfg.tagging.first_tag * cfg.tagging.unowned_factor)
    assert eco.tag_reward(cfg, 0, 1.0, owned=False) == expected


def test_streak():
    d = date(2026, 11, 10)
    assert eco.next_streak(0, None, d) == (1, True)
    assert eco.next_streak(3, date(2026, 11, 9), d) == (4, True)
    assert eco.next_streak(3, d, d) == (3, False)  # already claimed today
    assert eco.next_streak(9, date(2026, 11, 1), d) == (1, True)  # missed a day: reset
    assert not hasattr(eco, "streak_bonus")  # v0.9.0: the check-in pays no coins (it unlocks the free pack)


def test_milestones():
    crossed = eco.milestones_crossed(cfg, 0.0, 0.3)
    assert [a for a, _ in crossed] == [m.at for m in cfg.milestones if m.at <= 0.3]
    assert all(packs and all(p["count"] >= 1 for p in packs) for _, packs in crossed)  # v0.9.0: packs
    assert eco.milestones_crossed(cfg, 0.3, 0.3) == []
    assert eco.next_milestone(cfg, 1.0) is None


def test_pack_cost_inflates_and_rounds(monkeypatch):
    pack = cfg.packs["standard"]
    assert eco.pack_cost(cfg, pack, 0, 0) == pack.base_cost
    assert eco.pack_cost(cfg, pack, 40, 0) == pack.base_cost  # v0.9.0: the live price does not rise
    monkeypatch.setattr(cfg.inflation, "per_pack", 0.002)  # the rule is still there behind the number
    c40 = eco.pack_cost(cfg, pack, 40, 0)
    assert c40 > pack.base_cost and c40 % cfg.inflation.round_to == 0


def test_completeness_inflation_mode():
    c = cfg.model_copy(deep=True)
    c.inflation.mode = "completeness"
    assert eco.inflation_multiplier(c, 999, 0.05) == 1
    assert eco.inflation_multiplier(c, 0, 0.2) == pytest.approx((1 + c.inflation.per_10pct_completeness) ** 2)


def test_selling_decays_with_inflation_and_copies():
    assert eco.sell_rate(cfg, 1.0) == cfg.selling.base_rate
    assert eco.sell_rate(cfg, 3.0) == cfg.selling.floor
    assert eco.card_value(cfg, "foil", 3) < eco.card_value(cfg, "foil", 1)
    assert 40 <= eco.sell_price(cfg, "foil", 1, 1.0) <= 60  # §8: foil sells for ~40–60


def test_duplicate_curve_and_conversion():
    assert eco.duplicate_chance(cfg, 0) == cfg.duplicates.early_chance
    assert eco.duplicate_chance(cfg, 1) == cfg.duplicates.late_chance
    assert eco.duplicate_coins(cfg, "foil") < eco.sell_price(cfg, "foil", 1, 1.0)
    assert eco.next_tier(cfg, "normal") == "foil"
    assert eco.next_tier(cfg, "special") is None


def test_rarity_decay_and_pack_overrides(monkeypatch):
    std = dict(eco.slot_weights(cfg, cfg.packs["standard"], 0))
    # v0.9.0: the decay is off in the live config, the odds are the same for every pack
    assert dict(eco.slot_weights(cfg, cfg.packs["standard"], 1)) == std
    monkeypatch.setattr(cfg.rarity_decay, "max", 0.30)
    late = dict(eco.slot_weights(cfg, cfg.packs["standard"], 1))
    assert late["normal"] == std["normal"] and late["special"] < std["special"]
    prem = dict(eco.slot_weights(cfg, cfg.packs["premium"], 0))
    assert prem["special"] > std["special"]


def test_weighted_completeness():
    assert eco.card_completeness(cfg, False, "special") == 0
    top = max(t.completeness_weight for t in cfg.treatments.values())
    assert eco.card_completeness(cfg, True, None) == cfg.treatments["normal"].completeness_weight / top
    assert eco.card_completeness(cfg, True, "special") == 1.0
    # fully tagged but all-Normal is well short of 100% (the long tail)
    assert eco.collection_completeness(cfg, [(True, None)] * 10) < 0.5
    assert eco.collection_completeness(cfg, [(True, "special")] * 10) == 1.0
    tagged_mode = cfg.model_copy(deep=True)
    tagged_mode.completeness.mode = "tagged"
    assert eco.collection_completeness(tagged_mode, [(True, None), (False, None)]) == 0.5


def test_config_rejects_unknown_treatment_override():
    raw = cfg.model_dump()
    raw["packs"]["standard"]["weights"] = {"mythic": 5}
    with pytest.raises(ValueError):
        EconomyConfig.model_validate(raw)
