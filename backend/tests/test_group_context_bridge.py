"""Tests for group_context → FeatureSet.custom mapping."""
from app.kernel.engines.group_context_bridge import (
    group_context_to_custom,
    merge_custom,
)
from app.kernel.engines.situational_adjust import (
    apply_situational_adjustment,
    extract_situational_context,
)


def test_empty_context():
    assert group_context_to_custom(None) == {}
    assert group_context_to_custom({}) == {}


def test_maps_must_win_and_pressure():
    gc = {
        "group": "A",
        "has_must_win_team": True,
        "home": {
            "must_win": True,
            "pressure": "must_win",
            "status": "contending",
            "rank": 3,
            "points": 3,
        },
        "away": {
            "must_win": False,
            "pressure": "rotation_risk",
            "status": "qualified",
            "rank": 1,
            "points": 6,
        },
    }
    custom = group_context_to_custom(gc)
    assert custom["must_win_home"] is True
    assert custom["must_win_away"] is False
    assert custom["home_pressure"] == "must_win"
    assert custom["away_pressure"] == "rotation_risk"
    assert custom["away_group_status"] == "qualified"
    assert custom["stakes"] == "high"
    assert custom["group"] == "A"


def test_feeds_situational_adjust():
    custom = group_context_to_custom({
        "home": {"must_win": True, "pressure": "must_win", "status": "contending"},
        "away": {"must_win": False, "pressure": "normal", "status": "contending"},
        "has_must_win_team": True,
    })
    ctx = extract_situational_context("group_stage", custom)
    adj, applied = apply_situational_adjustment(
        {"home_win": 0.40, "draw": 0.30, "away_win": 0.30},
        ctx,
    )
    assert applied
    assert adj["home_win"] > 0.40


def test_merge_preserves_base():
    merged = merge_custom(
        {"must_win_home": False, "liquidity_factor": 0.9},
        {"must_win_home": True, "home_pressure": "must_win"},
    )
    assert merged["must_win_home"] is False
    assert merged["home_pressure"] == "must_win"
    assert merged["liquidity_factor"] == 0.9
