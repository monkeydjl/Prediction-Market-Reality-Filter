"""Tests for team_geo travel / timezone soft signals."""
from app.sports._shared.team_geo import (
    haversine_km,
    travel_between_teams,
    travel_prob_home,
)


def test_haversine_same_point_zero():
    assert haversine_km(40.0, -74.0, 40.0, -74.0) < 0.01


def test_nba_cross_country_distance():
    t = travel_between_teams("Boston Celtics", "Los Angeles Lakers", "nba")
    assert t["travel_known"] is True
    assert t["travel_km_away"] is not None
    assert t["travel_km_away"] > 3000
    assert abs(t["timezone_offset_hours_away"]) >= 2


def test_nhl_canadian_cross_zone():
    t = travel_between_teams("Toronto Maple Leafs", "Vancouver Canucks", "nhl")
    assert t["travel_known"] is True
    assert t["travel_km_away"] > 2000
    assert abs(t["timezone_offset_hours_away"]) >= 2


def test_travel_prob_long_haul_favors_home():
    p_short, ok1 = travel_prob_home(100.0, 0)
    p_long, ok2 = travel_prob_home(4000.0, 3)
    assert ok1 and ok2
    assert p_long > p_short
    assert 0.4 <= p_long <= 0.6


def test_unknown_team_unavailable():
    t = travel_between_teams("Unknown FC", "Also Unknown", "nba")
    assert t["travel_known"] is False
    p, ok = travel_prob_home(None, None)
    assert ok is False
    assert p == 0.5
