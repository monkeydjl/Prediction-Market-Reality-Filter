"""Tests for football_weather.climate_for_home (P1-F7 residual)."""
from app.sports.football.football_weather import climate_for_home

_CONDITIONS = {"clear", "mild", "rain", "cold", "hot"}


class TestClimateForHome:
    def test_known_club_month_has_keys_in_band(self):
        c = climate_for_home("Arsenal", 9)
        assert c is not None
        assert set(c.keys()) >= {"temp_c", "condition"}
        assert -15.0 <= float(c["temp_c"]) <= 45.0
        assert c["condition"] in _CONDITIONS

    def test_unknown_returns_none(self):
        assert climate_for_home("NotAFootballClubXYZ", 6) is None

    def test_empty_returns_none(self):
        assert climate_for_home("", 6) is None
        assert climate_for_home("   ", 6) is None

    def test_bad_month_returns_none(self):
        assert climate_for_home("Arsenal", 0) is None
        assert climate_for_home("Arsenal", 13) is None

    def test_normalize_case_and_spaces(self):
        a = climate_for_home("Arsenal", 6)
        b = climate_for_home("  arsenal  ", 6)
        c = climate_for_home("ARSENAL", 6)
        assert a is not None
        assert a == b == c

    def test_northern_winter_colder_than_summer(self):
        winter = climate_for_home("Manchester United", 1)
        summer = climate_for_home("Manchester United", 7)
        assert winter is not None and summer is not None
        assert float(winter["temp_c"]) < float(summer["temp_c"])

    def test_mediterranean_warmer_winter_than_scotland(self):
        seville = climate_for_home("Sevilla", 1)
        celtic = climate_for_home("Celtic", 1)
        assert seville is not None and celtic is not None
        assert float(seville["temp_c"]) > float(celtic["temp_c"])
