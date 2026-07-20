"""Tests for odds quality multiplier (P1-E4)."""
from app.kernel.engines.odds_quality import (
    decimal_overround,
    describe_odds_quality,
    odds_weight_multiplier,
)


def test_decimal_overround_typical_book():
    ov = decimal_overround(1.80, 3.50, 4.50)
    assert ov is not None
    assert 1.05 < ov < 1.12


def test_full_multiplier_for_fresh_sharp_book():
    mult = odds_weight_multiplier(1.90, 3.40, 4.20, odds_fresh=True)
    assert mult == 1.0


def test_stale_odds_reduces_multiplier():
    mult = odds_weight_multiplier(1.90, 3.40, 4.20, odds_fresh=False)
    assert mult < 0.75
    assert mult >= 0.15


def test_high_overround_reduces_multiplier():
    # Extreme juice: ~1.30 overround
    mult = odds_weight_multiplier(1.50, 3.0, 3.0, odds_fresh=True)
    assert mult < 1.0
    assert mult >= 0.15


def test_low_liquidity_custom_reduces_multiplier():
    mult = odds_weight_multiplier(
        1.90,
        3.40,
        4.20,
        odds_fresh=True,
        custom={"liquidity_factor": 0.0},
    )
    assert mult < 0.70


def test_describe_includes_mult():
    note = describe_odds_quality(1.90, 3.40, 4.20, odds_fresh=False)
    assert "odds_mult=" in note
    assert "stale" in note


def test_odds_dispersion_from_books():
    from app.kernel.engines.odds_quality import odds_dispersion_from_books

    books = [
        {"odds_home": 1.80},
        {"odds_home": 2.00},
        {"odds_home": 1.90},
    ]
    disp = odds_dispersion_from_books(books)
    assert disp is not None
    assert disp > 0
    assert odds_dispersion_from_books([{"odds_home": 1.9}]) is None
