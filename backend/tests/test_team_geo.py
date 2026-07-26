"""Tests for team_geo travel / timezone soft signals."""
from app.sports._shared.team_geo import (
    altitude_m_for_team,
    haversine_km,
    resolve_city,
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


# --- P1-F7 football club geo + altitude ---


def test_football_club_resolves_city():
    city = resolve_city("Arsenal", "epl")
    assert city is not None
    lat, lon, tz = city
    assert 51.0 < lat < 52.0
    assert -1.0 < lon < 1.0
    assert tz == 0


def test_football_club_alias_man_city():
    a = resolve_city("Manchester City", "football")
    b = resolve_city("Man City", "ucl")
    assert a is not None and b is not None
    assert a[0] == b[0] and a[1] == b[1]


def test_football_fixture_real_madrid_cf():
    city = resolve_city("Real Madrid CF", "ucl")
    assert city is not None


def test_football_fixture_bayern():
    city = resolve_city("FC Bayern München", "ucl")
    assert city is not None


def test_football_national_still_resolves():
    city = resolve_city("Brazil", "wc")
    assert city is not None
    assert city[0] < 0  # southern hemisphere capital-ish


def test_club_travel_london_to_madrid():
    t = travel_between_teams("Arsenal", "Real Madrid CF", "ucl")
    assert t["travel_known"] is True
    assert t["travel_km_away"] is not None
    assert t["travel_km_away"] > 1000
    assert t["travel_km_home"] == 0.0


def test_unknown_football_club_travel_unknown():
    t = travel_between_teams("NoSuchHome FC", "NoSuchAway FC", "epl")
    assert t["travel_known"] is False


def test_altitude_high_venue_in_band():
    # Toluca / Mexico City area — must be in static altitude table ≥1500
    alt = altitude_m_for_team("Toluca")
    assert alt is not None
    assert 1500.0 <= float(alt) <= 4500.0


def test_altitude_unknown_none():
    assert altitude_m_for_team("NotAFootballClubXYZ") is None


def test_altitude_empty_none():
    assert altitude_m_for_team("") is None
    assert altitude_m_for_team("   ") is None
