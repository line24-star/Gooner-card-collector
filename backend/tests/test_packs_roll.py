import random

from app.game import economy as eco
from app.game.config import get_config
from app.game.packs import NEAR_MISS, PoolItem, estimate_rtp, roll_pack, roll_treatment

cfg = get_config()
STD = cfg.packs["standard"]


def test_roll_treatment_bands_and_near_miss():
    w = eco.slot_weights(cfg, STD, 0)
    normal_edge = w[0][1] / sum(x for _, x in w)
    assert roll_treatment(w, 0)[0] == "normal"
    assert roll_treatment(w, 0.9999)[0] == "special"
    assert roll_treatment(w, normal_edge - NEAR_MISS / 2) == ("normal", True)
    assert roll_treatment(w, 0.0)[1] is False


def test_pack_size_unique_and_sorted():
    pool = [PoolItem(i, tagged=False) for i in range(100)]
    pulls = roll_pack(cfg, STD, pool, 0, random.Random(1))
    assert len(pulls) == STD.size
    assert len({p.media_id for p in pulls}) == STD.size
    tiers = [cfg.treatments[p.treatment].tier for p in pulls]
    assert tiers == sorted(tiers)
    assert all(p.outcome == "new" for p in pulls)  # nothing owned yet


def test_duplicates_are_kept_as_copies_and_pay_nothing():
    """v0.4.0: a duplicate is another copy. No coins and no automatic upgrade."""
    assert cfg.duplicates.keep_copies
    every = dict.fromkeys(cfg.treatments, 1)  # a copy of every treatment: whatever is rolled is a duplicate
    pool = [PoolItem(i, tagged=True, owned=dict(every)) for i in range(20)]
    pulls = roll_pack(cfg, STD, pool, 0.5, random.Random(2))
    assert all(p.outcome == "duplicate" and p.coins == 0 and p.upgraded_to is None for p in pulls)


def test_cards_with_a_big_print_run_are_pulled_more_often():
    pool = [PoolItem(0, False, weight=1.0), PoolItem(1, False, weight=9.0)]
    rng = random.Random(3)
    first = [roll_pack(cfg, STD.model_copy(update={"size": 1}), pool, 0, rng)[0].media_id for _ in range(2000)]
    share = first.count(1) / len(first)
    assert 0.86 < share < 0.94  # 9 of 10


def test_better_version_and_duplicate_both_occur():
    pool = [PoolItem(0, tagged=True, owned={"normal": 1})]
    one = STD.model_copy(update={"size": 1})  # one slot: a one-card library repeats its card in a bigger pack
    seen = {roll_pack(cfg, one, pool, 0, random.Random(s))[0].outcome for s in range(300)}
    assert {"better", "duplicate"} <= seen


def test_duplicate_rate_tracks_owned_share():
    rng = random.Random(3)
    pool = [PoolItem(i, tagged=False, owned={"normal": 1} if i < 500 else {}) for i in range(1000)]
    pulls = [p for _ in range(400) for p in roll_pack(cfg, STD, pool, 0, rng)]
    owned_share = sum(p.media_id < 500 for p in pulls) / len(pulls)
    assert abs(owned_share - eco.duplicate_chance(cfg, 0.5)) < 0.05


def test_small_or_empty_pool():
    assert roll_pack(cfg, STD, [], 0, random.Random(0)) == []
    # v0.7.1: a pack is always full; a library smaller than the pack repeats its cards
    assert len(roll_pack(cfg, STD, [PoolItem(1, False)], 0, random.Random(0))) == STD.size


def test_early_rtp_hits_design_target():
    """Balance guard (§6): a fresh 1000-card collection should return 70–85% per standard pack."""
    pool = [PoolItem(i, tagged=False) for i in range(1000)]
    rtp = estimate_rtp(cfg, STD, pool, packs_opened=0, completeness=0, trials=600, seed=1)
    lo, hi = cfg.rtp.early_target
    assert lo - 0.03 <= rtp <= hi, f"early RTP {rtp:.0%} outside {lo:.0%}–{hi:.0%}; retune config/economy.toml"


def test_reveal_order_uses_the_shown_treatment_for_upgrades():
    """An upgraded duplicate reveals as its upgraded_to treatment, so it sorts by that (rarest last)."""
    pool = [PoolItem(i, tagged=True, owned={"holo": 1}) for i in range(40)]
    for seed in range(50):
        pulls = roll_pack(cfg, STD, pool, 0, random.Random(seed))
        shown = [cfg.treatments[p.upgraded_to if p.outcome == "upgraded" and p.upgraded_to else p.treatment].tier for p in pulls]
        assert shown == sorted(shown), seed
