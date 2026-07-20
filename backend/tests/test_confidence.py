"""Tests for richer Kernel confidence (P1-X1)."""
from app.kernel.engines.confidence import (
    compute_confidence,
    data_completeness,
    decision_strength,
    factor_agreement,
    market_quality_damp,
)


def test_decision_strength_peaky_vs_flat():
    flat = decision_strength({"home_win": 0.34, "draw": 0.33, "away_win": 0.33})
    peaky = decision_strength({"home_win": 0.80, "draw": 0.12, "away_win": 0.08})
    assert peaky > flat
    assert peaky > 0.6


def test_data_completeness_quality_nudge():
    full = data_completeness([True, True, True], quality="real")
    partial = data_completeness([True, False, False], quality="partial")
    assert full > partial


def test_factor_agreement():
    assert factor_agreement(["home_win", "home_win", "draw"], final_outcome="home_win") == 2 / 3


def test_stale_and_thin_damp():
    assert market_quality_damp(odds_fresh=False) < 1.0
    assert market_quality_damp(liquidity_factor=0.1) < 1.0
    assert market_quality_damp(odds_dispersion=0.10) < 1.0


def test_compute_confidence_range():
    c = compute_confidence(
        {"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        available_flags=[True, True],
        predicted_outcomes=["home_win", "home_win"],
        data_quality="real",
        odds_fresh=True,
        custom={"liquidity_factor": 1.0},
    )
    assert 0.20 <= c <= 0.95


def test_incomplete_data_lowers_confidence():
    full = compute_confidence(
        {"home_win": 0.60, "draw": 0.22, "away_win": 0.18},
        available_flags=[True, True, True, True],
        predicted_outcomes=["home_win"] * 4,
        data_quality="real",
        odds_fresh=True,
    )
    thin = compute_confidence(
        {"home_win": 0.60, "draw": 0.22, "away_win": 0.18},
        available_flags=[True, False, False, False],
        predicted_outcomes=["home_win", None, None, None],
        data_quality="partial",
        odds_fresh=False,
        custom={"liquidity_factor": 0.1},
    )
    assert full > thin


def test_confidence_breakdown_matches_total():
    from app.kernel.engines.confidence import confidence_breakdown

    probs = {"home_win": 0.55, "draw": 0.25, "away_win": 0.20}
    kwargs = dict(
        available_flags=[True, True, False],
        predicted_outcomes=["home_win", "home_win", None],
        data_quality="real",
        odds_fresh=True,
        custom={"liquidity_factor": 0.8, "odds_dispersion": 0.05},
    )
    total = compute_confidence(probs, **kwargs)
    bd = confidence_breakdown(probs, **kwargs)
    assert bd["total"] == total
    assert 0.0 <= bd["decision_strength"] <= 1.0
    assert 0.0 <= bd["data_completeness"] <= 1.0
    assert 0.0 <= bd["factor_agreement"] <= 1.0
    assert 0.75 <= bd["market_damp"] <= 1.0
    assert bd["factors_available"] == 2
    assert bd["factors_total"] == 3
    assert bd["final_outcome"] == "home_win"
