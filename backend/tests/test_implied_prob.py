"""Tests for implied probability conversion utilities."""
import pytest


def test_polymarket_to_implied_basic():
    from app.utils.implied_prob import polymarket_to_implied
    yes_implied, no_implied, spread = polymarket_to_implied(0.60, 0.45)
    assert yes_implied == 0.60
    assert no_implied == 0.45
    assert spread == pytest.approx(0.05)


def test_polymarket_to_implied_no_spread_when_sum_below_one():
    from app.utils.implied_prob import polymarket_to_implied
    yes_implied, no_implied, spread = polymarket_to_implied(0.40, 0.40)
    assert yes_implied == 0.40
    assert no_implied == 0.40
    assert spread == pytest.approx(-0.20)


def test_polymarket_to_implied_exact_one():
    from app.utils.implied_prob import polymarket_to_implied
    _, _, spread = polymarket_to_implied(0.55, 0.45)
    assert spread == pytest.approx(0.0)


def test_odds_api_to_implied_basic():
    from app.utils.implied_prob import odds_api_to_implied
    # 2.0 / 2.0 -> 0.5 / 0.5 (no vigorish)
    result = odds_api_to_implied([2.0, 2.0])
    assert result == [pytest.approx(0.5), pytest.approx(0.5)]


def test_odds_api_to_implied_normalizes_vigorish():
    from app.utils.implied_prob import odds_api_to_implied
    # 1.5 / 2.5 -> raw 0.667 / 0.4 = 1.067; normalized -> 0.625 / 0.375
    result = odds_api_to_implied([1.5, 2.5])
    assert sum(result) == pytest.approx(1.0)
    assert result[0] > result[1]


def test_odds_api_to_implied_empty_list():
    from app.utils.implied_prob import odds_api_to_implied
    assert odds_api_to_implied([]) == []


def test_odds_api_to_implied_skips_zero_odds():
    from app.utils.implied_prob import odds_api_to_implied
    # zero odds are skipped (guarded against division by zero)
    result = odds_api_to_implied([0.0, 2.0])
    assert result == [pytest.approx(1.0)]
