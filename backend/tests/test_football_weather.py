"""Tests for football_weather.climate_for_home (P1-F7 residual)."""
from contextlib import ExitStack
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

    def test_single_source_reports_source_count_one(self):
        match = _StubMatch("Arsenal", _NOW)
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"current_weather": {"temperature": 17.4, "weathercode": 61}}
        with (
            patch("app.sports.football.football_weather.httpx.get", return_value=resp),
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED", False),
        ):
            out = live_weather_for_match(match)
        assert out is not None
        assert out["weather_source_count"] == pytest.approx(1.0)
        assert out["weather_agreement"] == "single"


def _secondary_on():
    """Patch context tuple enabling the optional second provider."""
    return (
        patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED", True),
        patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_SECONDARY_URL", "https://wx2.example/point"),
        patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_SECONDARY_API_KEY", "secret-key"),
    )


class TestMultiSourceConsensus:
    """Two configured live sources: agreement averaging and bounded divergence."""

    def setup_method(self):
        _clear_live_weather_cache()

    @staticmethod
    def _primary(temp, code=61):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"current_weather": {"temperature": temp, "weathercode": code}}
        return resp

    def _run(self, match, primary_resp, secondary_result, *, primary_url="https://wx.example/forecast"):
        """Run the consensus path with both sources stubbed.

        ``primary_resp`` None means the primary provider is never reached (used
        with ``primary_url=""`` for a secondary-only configuration).
        """
        http_patch = (
            patch("app.sports.football.football_weather.httpx.get", return_value=primary_resp)
            if primary_resp is not None
            else patch("app.sports.football.football_weather.httpx.get")
        )
        contexts = (
            http_patch,
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", primary_url),
            patch(
                "app.services.football_live_weather_service.get_secondary_weather",
                return_value=secondary_result,
            ),
            *_secondary_on(),
        )
        with ExitStack() as stack:
            for ctx in contexts:
                stack.enter_context(ctx)
            return live_weather_for_match(match)

    def test_agreeing_sources_average_temperature(self):
        from app.services.football_live_weather_service import LiveWeatherResult

        match = _StubMatch("Arsenal", _NOW)
        out = self._run(
            match,
            self._primary(17.0),
            LiveWeatherResult(available=True, temp_c=19.0, condition="rain"),
        )
        assert out is not None
        assert out["weather_temp_c"] == pytest.approx(18.0)
        assert out["weather_condition"] == "rain"
        assert out["weather_source_count"] == pytest.approx(2.0)
        assert out["weather_agreement"] == "agree"

    def test_condition_disagreement_keeps_primary_label(self):
        from app.services.football_live_weather_service import LiveWeatherResult

        match = _StubMatch("Arsenal", _NOW)
        out = self._run(
            match,
            self._primary(17.0),
            LiveWeatherResult(available=True, temp_c=18.0, condition="mild"),
        )
        assert out is not None
        assert out["weather_condition"] == "rain"
        assert out["weather_agreement"] == "temp_only"

    def test_diverging_temperature_falls_back_to_primary(self):
        from app.services.football_live_weather_service import LiveWeatherResult

        match = _StubMatch("Arsenal", _NOW)
        out = self._run(
            match,
            self._primary(17.0),
            LiveWeatherResult(available=True, temp_c=30.0, condition="hot"),
        )
        assert out is not None
        assert out["weather_temp_c"] == pytest.approx(17.0)
        assert out["weather_condition"] == "rain"
        assert out["weather_agreement"] == "diverged"

    def test_secondary_carries_when_primary_unavailable(self):
        from app.services.football_live_weather_service import LiveWeatherResult

        match = _StubMatch("Arsenal", _NOW)
        out = self._run(
            match,
            self._primary_error(),
            LiveWeatherResult(available=True, temp_c=12.5, condition="cold"),
        )
        assert out is not None
        assert out["weather_temp_c"] == pytest.approx(12.5)
        assert out["weather_condition"] == "cold"
        assert out["weather_agreement"] == "single"

    @staticmethod
    def _primary_error():
        resp = MagicMock(status_code=500)
        resp.json.return_value = {}
        return resp

    def test_secondary_only_configuration_makes_no_primary_http(self):
        from app.services.football_live_weather_service import LiveWeatherResult

        match = _StubMatch("Arsenal", _NOW)
        contexts = (
            patch("app.sports.football.football_weather.httpx.get"),
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", ""),
            patch(
                "app.services.football_live_weather_service.get_secondary_weather",
                return_value=LiveWeatherResult(available=True, temp_c=14.0, condition="mild"),
            ),
            *_secondary_on(),
        )
        with ExitStack() as stack:
            mock_get = stack.enter_context(contexts[0])
            for ctx in contexts[1:]:
                stack.enter_context(ctx)
            out = live_weather_for_match(match)
        mock_get.assert_not_called()
        assert out is not None
        assert out["weather_temp_c"] == pytest.approx(14.0)
        assert out["weather_agreement"] == "single"

    def test_unavailable_secondary_leaves_primary_untouched(self):
        from app.services.football_live_weather_service import LiveWeatherResult

        match = _StubMatch("Arsenal", _NOW)
        out = self._run(
            match,
            self._primary(17.4),
            LiveWeatherResult(available=False),
        )
        assert out is not None
        assert out["weather_temp_c"] == pytest.approx(17.4)
        assert out["weather_source_count"] == pytest.approx(1.0)
        assert out["weather_agreement"] == "single"

    def test_secondary_exception_does_not_break_primary(self):
        match = _StubMatch("Arsenal", _NOW)
        contexts = (
            patch("app.sports.football.football_weather.httpx.get", return_value=self._primary(17.4)),
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
            patch(
                "app.services.football_live_weather_service.get_secondary_weather",
                side_effect=RuntimeError("provider blew up"),
            ),
            *_secondary_on(),
        )
        with ExitStack() as stack:
            for ctx in contexts:
                stack.enter_context(ctx)
            out = live_weather_for_match(match)
        assert out is not None
        assert out["weather_temp_c"] == pytest.approx(17.4)
        assert out["weather_agreement"] == "single"

    def test_both_sources_unavailable_returns_none(self):
        from app.services.football_live_weather_service import LiveWeatherResult

        match = _StubMatch("Arsenal", _NOW)
        out = self._run(
            match,
            self._primary_error(),
            LiveWeatherResult(available=False),
        )
        assert out is None

    def test_secondary_reading_is_clamped_to_feature_band(self):
        from app.services.football_live_weather_service import LiveWeatherResult

        match = _StubMatch("Arsenal", _NOW)
        out = self._run(
            match,
            None,
            LiveWeatherResult(available=True, temp_c=55.0, condition="hot"),
            primary_url="",
        )
        assert out is not None
        assert out["weather_temp_c"] == pytest.approx(45.0)
