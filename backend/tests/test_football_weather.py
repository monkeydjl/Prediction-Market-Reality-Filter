"""Tests for football_weather.climate_for_home (P1-F7 residual)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.sports.football.football_weather import (
    climate_for_home,
    live_weather_for_match,
    _clear_live_weather_cache,
)

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


class _StubHome:
    def __init__(self, name):
        self.name = name


class _StubMatch:
    def __init__(self, home_name, kickoff_utc):
        self.home = _StubHome(home_name)
        self.kickoff_utc = kickoff_utc


_NOW = datetime(2025, 9, 16, 12, 0, tzinfo=timezone.utc)


class TestLiveWeatherForMatch:
    def setup_method(self):
        _clear_live_weather_cache()

    def test_configured_within_horizon_returns_normalized(self):
        match = _StubMatch("Arsenal", _NOW)
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"current_weather": {"temperature": 17.4, "weathercode": 61}}
        with (
            patch("app.sports.football.football_weather.httpx.get", return_value=resp) as mock_get,
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
        ):
            out = live_weather_for_match(match)
        assert mock_get.call_count == 1
        assert out is not None
        assert out["weather_temp_c"] == pytest.approx(17.4)
        assert out["weather_condition"] == "rain"

    def test_missing_config_returns_none_without_http(self):
        match = _StubMatch("Arsenal", _NOW)
        with (
            patch("app.sports.football.football_weather.httpx.get") as mock_get,
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", ""),
        ):
            out = live_weather_for_match(match)
        assert out is None
        mock_get.assert_not_called()

    def test_beyond_horizon_returns_none_without_http(self):
        far = datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc)  # 14 days out
        match = _StubMatch("Arsenal", far)
        with (
            patch("app.sports.football.football_weather.httpx.get") as mock_get,
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
        ):
            out = live_weather_for_match(match)
        assert out is None
        mock_get.assert_not_called()

    def test_network_error_returns_none(self):
        import httpx as _httpx

        match = _StubMatch("Arsenal", _NOW)
        with (
            patch(
                "app.sports.football.football_weather.httpx.get",
                side_effect=_httpx.ConnectError("boom"),
            ),
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
        ):
            out = live_weather_for_match(match)
        assert out is None

    def test_malformed_payload_returns_none(self):
        match = _StubMatch("Arsenal", _NOW)
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("bad json")
        with (
            patch("app.sports.football.football_weather.httpx.get", return_value=resp),
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
        ):
            out = live_weather_for_match(match)
        assert out is None
