"""The economy simulator (tools/simulate.py): it must follow the real rules and stay repeatable."""

import random

import pytest

from app.game import economy as eco
from app.game import market as mk
from app.game.config import get_config
from app.game.packs import roll_pack
from tools import simulate as sim


def _book(cfg, n: int, seed: int = 3) -> sim.Book:
    rng = random.Random(seed)
    book = sim.Book(cfg, n)
    for _ in range(n):
        book.add(mk.roll_print_run(cfg, rng), mk.roll_popularity(cfg, rng))
    return book


def _own(book: sim.Book, i: int, tier: int) -> None:
    c = book.cards[i]
    if c.best < 0:
        book.mark_owned(i)
    c.owned[tier] += 1
    c.best = max(c.best, tier)


@pytest.mark.parametrize("owned_share", [0.0, 0.3, 0.97, 1.0])
def test_roll_fast_gives_the_pulls_of_roll_pack(owned_share):
    cfg = get_config()
    book = _book(cfg, 80)
    setup = random.Random(11)
    for i in range(len(book.cards)):
        if setup.random() < owned_share:
            _own(book, i, setup.choice([0, 0, 0, 1, 2, 3]))
    for seed in range(40):
        for pack in cfg.packs.values():
            pity = 1.0 + (seed % 3)
            fast = sim.roll_fast(cfg, pack, book, 0.4, random.Random(seed), pity)
            real = roll_pack(cfg, pack, book.pool_items(), 0.4, random.Random(seed), pity)
            assert [(p.media_id, p.treatment, p.outcome, p.near_miss) for p in fast] == [
                (p.media_id, p.treatment, p.outcome, p.near_miss) for p in real
            ]
            assert len({p.media_id for p in fast}) == len(fast)
        # the roll leaves the book as it was, then the collection grows a little
        assert book.owned.count + book.unowned.count == len(book.cards)
        if book.unowned.count:
            _own(book, book.unowned.find(0), 0)


def test_market_tick_moves_the_market_like_advance():
    cfg = get_config()
    a, b = _book(cfg, 150), _book(cfg, 150)
    log_a = log_b = 0.1
    for tick in range(1000, 1200):  # long enough for reprints, hype and buyouts
        log_a = sim.market_tick(cfg, a, 42, tick, log_a)
        log_b, _ = mk.advance(cfg, b.states, 42, tick, log_b)
    assert log_a == log_b
    assert [(c.print_run, c.factor) for c in a.states] == [(c.print_run, c.factor) for c in b.states]
    # a reprinted card's pull weight follows its new print run
    for card, state in zip(a.cards, a.states):
        assert card.weight == max(1, round(mk.draw_weight(cfg, state.print_run) * sim.WEIGHT_SCALE))
    assert a.unowned.total == sum(c.weight for c in a.cards)


def test_runs_are_repeatable_and_books_balance():
    cfg = get_config()
    one = sim.simulate(cfg, 150, "regular", "seller", days=12, seed=5)
    two = sim.simulate(cfg, 150, "regular", "seller", days=12, seed=5)
    assert one.days == two.days and one.left == two.left
    assert sim.simulate(cfg, 150, "regular", "seller", days=12, seed=6).days != one.days
    last = one.days[-1]
    # every coin is accounted for: welcome coins + income - spending = balance
    assert cfg.starting_coins + sum(last.earned.values()) - last.spent_packs - last.spent_upgrades == last.coins
    assert all(d.coins >= 0 and d.used_s + d.idle_s <= 30 * 60 + 60 for d in one.days)
    assert set(last.earned) == set(sim.SOURCES)


def test_policies_differ_only_in_what_happens_to_duplicates():
    cfg = get_config()
    runs = {p: sim.simulate(cfg, 120, "heavy", p, days=20, seed=2) for p in sim.POLICIES}
    seller, hoarder, fuser = (runs[p].days[-1] for p in sim.POLICIES)
    # duplicates pay nothing by themselves (duplicates.keep_copies): only the seller turns them into coins
    assert seller.earned["sell"] > 0 and seller.spare_copies == 0
    assert hoarder.earned["sell"] == hoarder.earned["offer"] == 0 and hoarder.fused == 0 and hoarder.spare_copies > 0
    assert fuser.earned["sell"] == 0 and fuser.fused > 0
    assert runs["fuser"].left["fusable"] == 0  # everything that could be fused was fused
    # all three see the same library and the same market index
    assert seller.index == hoarder.index == fuser.index


def test_tags_pay_the_list_then_the_floor():
    cfg = get_config()
    arch = sim.Archetype("deep", 600, 1, cfg.tagging.max_paid_tags + 3)
    s = sim.Sim(cfg, 50, arch, "hoarder", seed=1, new_media=0, days=1)
    s._add_copy(0, 0)
    assert s.tag_card(rush=False)
    assert s.earned["tag"] == eco.card_tagging_value(cfg, arch.tags_per_card, 1.0)
    t = cfg.tagging
    assert s.earned["tag"] == t.first_tag + sum(t.extra_tags) + t.floor * (t.max_paid_tags - 1 - len(t.extra_tags))
    s._add_copy(1, 1)  # a Foil during a rush: the list is multiplied by the treatment, everything by the rush
    before = s.earned["tag"]
    assert s.tag_card(rush=True)
    assert s.earned["tag"] - before == sum(
        eco.tag_reward(cfg, k, cfg.treatments["foil"].multiplier, True, True) for k in range(t.max_paid_tags))
    assert not s.tag_card(rush=False)  # nothing pulled, nothing to tag


def test_deal_price_and_config_overrides():
    cfg = get_config()
    assert sim.deal_price(cfg, 100, 0.33) == 65  # rounded to inflation.round_to like the shop
    assert sim.deal_price(cfg, 100, 0.999) == max(1, cfg.inflation.round_to)
    changed = sim.config_with(["market.sell_rate=0.5", "tagging.extra_tags=[4, 4]", "milestones.0.reward.count=7"])
    assert changed.market.sell_rate == 0.5 and changed.tagging.extra_tags == [4, 4]
    assert changed.milestones[0].reward.count == 7  # v0.9.0: a milestone's reward is packs
    assert get_config().market.sell_rate == cfg.market.sell_rate  # the live config is untouched
    with pytest.raises(SystemExit):
        sim.config_with(["market.no_such_key=1"])


def test_summary_adds_up():
    cfg = get_config()
    res = sim.simulate(cfg, 200, "casual", "seller", days=14, seed=1)
    s = sim.summarize(res, 14)
    assert s["income_per_day"] == pytest.approx(sum(s["per_day"].values()))
    assert 0 <= s["market_share"] <= 1 and 0 <= s["pulled"] <= 1
    assert s["used_min"] + s["idle_min"] <= 10 + 1
    week = sim.summarize(res, 7)
    assert week["days"] == 7 and week["coins"] == res.days[6].coins
