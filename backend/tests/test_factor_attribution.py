"""P1-V3 factor attribution unit tests."""
from app.kernel.factor_attribution import (
    extract_factor_drivers,
    format_factor_attribution,
)


def test_extract_top_drivers_3way():
    explanation = [
        {"factor": "elo", "weight": 0.3, "home_win": 0.70, "draw": 0.18, "away_win": 0.12, "available": True},
        {"factor": "odds", "weight": 0.4, "home_win": 0.45, "draw": 0.28, "away_win": 0.27, "available": True},
        {"factor": "form", "weight": 0.1, "home_win": 0.55, "draw": 0.25, "away_win": 0.20, "available": True},
        {"factor": "injury", "weight": 0.1, "home_win": 0.40, "draw": 0.30, "away_win": 0.30, "available": False},
    ]
    drivers = extract_factor_drivers(explanation, "home_win", top_n=2)
    assert len(drivers) == 2
    assert drivers[0]["factor"] == "elo"  # high weight * high home edge
    assert drivers[0]["impact"] > 0


def test_format_attribution():
    drivers = [{"factor": "elo", "impact": 0.05, "weight": 0.3}]
    s = format_factor_attribution(drivers, model_higher=True)
    assert s and "主导因子" in s and "elo" in s


def test_empty_explanation():
    assert extract_factor_drivers(None, "home_win") == []
    assert extract_factor_drivers([], "home_win") == []
