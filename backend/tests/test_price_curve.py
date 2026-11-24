"""The price ladder (v0.5.0, test round 4 point 5): a few cards far above the rest, like a real set.

Real sets (docs/economy/technical-reference.md, "Real-world price research"): the top card costs
hundreds to thousands of times a common, the 20th card a few percent of the top one, and half of
all cards are bulk.
"""

import math
import random
import statistics

import pytest

from app.game import market as mk
from app.game.config import get_config

cfg = get_config()


def values(n: int, seed: int) -> list[float]:
    """Fair values of n freshly issued cards, dearest first."""
    rng = random.Random(seed)
    return sorted((mk.fair_value(cfg, mk.roll_print_run(cfg, rng), mk.roll_popularity(cfg, rng)) for _ in range(n)), reverse=True)


def test_the_rank_curve_is_steep_and_the_median_card_is_bulk():
    tops, twentieths = [], []
    for seed in range(1, 8):
        v = values(2000, seed)
        median = statistics.median(v)
        assert 6 <= median <= 13  # the typical card stays a bulk card of about one base value
        assert v[0] >= 100 * median  # every market has a card far above the rest
        tops.append(v[0] / median)
        twentieths.append(v[19] / v[0])
    assert statistics.median(tops) >= 250  # a typical top card of 2,000: hundreds of times a bulk card
    assert statistics.median(twentieths) <= 0.20  # the 20th card is a small fraction of the top one
    v = values(2000, 1)
    assert sum(v[:200]) / sum(v) > 0.5  # a tenth of the cards holds more than half of all value


def test_demand_counts_for_more_on_scarce_cards():
    m = cfg.market
    assert m.demand_leverage > 0
    # a mass print at the reference print run: demand counts once, as before
    assert mk.fair_value(cfg, m.reference_print, 2.0) == pytest.approx(2 * mk.fair_value(cfg, m.reference_print, 1.0))
    # a wanted Chase card takes off
    assert mk.fair_value(cfg, 50, 2.0) > 5 * mk.fair_value(cfg, 50, 1.0)
    # an unloved one keeps its scarcity value: it loses no more than its popularity says
    assert mk.fair_value(cfg, 50, 0.5) == pytest.approx(0.5 * mk.fair_value(cfg, 50, 1.0))
    # above the reference print run demand counts for less, never negatively
    assert mk.fair_value(cfg, 60_000, 1.0) < mk.fair_value(cfg, 60_000, 3.0) < 3 * mk.fair_value(cfg, 60_000, 1.0)
    # value never falls when popularity rises, whatever the print run
    for run in (25, 400, 2000, 20_000, 60_000, 10**9):
        ladder = [mk.fair_value(cfg, run, p) for p in (0.4, 0.7, 1.0, 1.5, 4.0)]
        assert ladder == sorted(ladder)


def test_without_leverage_the_old_formula_holds():
    flat = cfg.model_copy(deep=True)
    flat.market.demand_leverage = 0.0
    m = flat.market
    for run, pop in ((50, 3.0), (2000, 0.5), (40_000, 1.7)):
        assert mk.fair_value(flat, run, pop) == pytest.approx(m.base_value * (m.reference_print / run) ** m.elasticity * pop)


def test_a_typical_duplicate_still_sells_for_a_few_coins():
    # a mass print at average demand: the same 4 to 8 coins as before the ladder
    for run in (20_000, 40_000, 60_000):
        card = mk.CardState(1, print_run=run)
        assert 4 <= mk.sell_price(cfg, mk.price(cfg, card, 1.0)) <= 8
    assert math.isclose(mk.fair_value(cfg, cfg.market.reference_print), cfg.market.base_value)
