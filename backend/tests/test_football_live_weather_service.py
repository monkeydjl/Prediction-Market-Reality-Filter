"""Tests for the optional secondary weather provider (P1-F7 multi-source)."""
import json
from unittest.mock import patch

from app.core.config import settings
from app.services.football_live_weather_service import (
    clear_secondary_weather_cache,
    get_secondary_weather,
)

_LAT = 51.51
_LON = -0.13
_DAY = "2026-09-16"


class _Response:
    def __init__(self, payload: object):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, _size: int) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _BytesResponse(_Response):
    def __init__(self, body: bytes):
        self._body = body


def _settings():
    return (
        patch.object(settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED", True),
        patch.object(
            settings,
            "FOOTBALL_LIVE_WEATHER_SECONDARY_URL",
            "https://wx2.example/point?units=metric",
        ),
        patch.object(settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_API_KEY", "secret-key"),
        patch.object(settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_TIMEOUT_S", 5.0),
        patch.object(settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_CACHE_TTL_HOURS", 1.0),
        patch.object(settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_MAX_BYTES", 262144),
    )


def _run(payload: object, *, lat=_LAT, lon=_LON, day=_DAY):
    """Call the service with the provider configured, returning (result, mock)."""
    clear_secondary_weather_cache()
    contexts = _settings()
    for context in contexts:
        context.start()
    try:
        with patch(
            "app.services.football_live_weather_service.urlopen",
            return_value=_Response(payload),
        ) as open_mock:
            return get_secondary_weather(lat, lon, day), open_mock
    finally:
        for context in contexts:
            context.stop()


class TestConfigurationGates:
    def test_disabled_provider_makes_no_request(self):
        clear_secondary_weather_cache()
        with patch.object(
            settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED", False
        ), patch.object(
            settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_URL", "https://wx2.example/point"
        ), patch.object(
            settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_API_KEY", "secret-key"
        ), patch(
            "app.services.football_live_weather_service.urlopen"
        ) as open_mock:
            result = get_secondary_weather(_LAT, _LON, _DAY)

        assert not result.available
        open_mock.assert_not_called()

    def test_missing_url_makes_no_request(self):
        clear_secondary_weather_cache()
        with patch.object(
            settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED", True
        ), patch.object(
            settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_URL", ""
        ), patch.object(
            settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_API_KEY", "secret-key"
        ), patch(
            "app.services.football_live_weather_service.urlopen"
        ) as open_mock:
            result = get_secondary_weather(_LAT, _LON, _DAY)

        assert not result.available
        open_mock.assert_not_called()

    def test_missing_api_key_makes_no_request(self):
        clear_secondary_weather_cache()
        with patch.object(
            settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED", True
        ), patch.object(
            settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_URL", "https://wx2.example/point"
        ), patch.object(
            settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_API_KEY", ""
        ), patch(
            "app.services.football_live_weather_service.urlopen"
        ) as open_mock:
            result = get_secondary_weather(_LAT, _LON, _DAY)

        assert not result.available
        open_mock.assert_not_called()

    def test_out_of_range_coordinates_make_no_request(self):
        result, open_mock = _run({"weather": {"temp_c": 12.0, "condition": "mild"}}, lat=99.0)
        assert not result.available
        open_mock.assert_not_called()

    def test_malformed_date_makes_no_request(self):
        result, open_mock = _run(
            {"weather": {"temp_c": 12.0, "condition": "mild"}}, day="16/09/2026"
        )
        assert not result.available
        open_mock.assert_not_called()


class TestRequestShape:
    def test_reads_normalized_reading_and_authenticates(self):
        result, open_mock = _run({"weather": {"temp_c": 17.44, "condition": "Rain"}})

        assert result.available
        assert result.temp_c == 17.4
        assert result.condition == "rain"

        request = open_mock.call_args.args[0]
        assert "latitude=51.51" in request.full_url
        assert "longitude=-0.13" in request.full_url
        assert "date=2026-09-16" in request.full_url
        assert "units=metric" in request.full_url  # pre-existing query preserved
        assert request.get_header("Authorization") == "Bearer secret-key"
        # The key must never travel in the query string.
        assert "secret-key" not in request.full_url

    def test_existing_reserved_params_are_replaced(self):
        clear_secondary_weather_cache()
        contexts = _settings()
        for context in contexts:
            context.start()
        try:
            with patch.object(
                settings,
                "FOOTBALL_LIVE_WEATHER_SECONDARY_URL",
                "https://wx2.example/point?latitude=0&date=1999-01-01",
            ), patch(
                "app.services.football_live_weather_service.urlopen",
                return_value=_Response({"weather": {"temp_c": 5.0, "condition": "cold"}}),
            ) as open_mock:
                result = get_secondary_weather(_LAT, _LON, _DAY)
        finally:
            for context in contexts:
                context.stop()

        assert result.available
        full_url = open_mock.call_args.args[0].full_url
        assert "latitude=0" not in full_url
        assert "1999-01-01" not in full_url

    def test_non_http_scheme_makes_no_request(self):
        clear_secondary_weather_cache()
        contexts = _settings()
        for context in contexts:
            context.start()
        try:
            with patch.object(
                settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_URL", "file:///etc/passwd"
            ), patch("app.services.football_live_weather_service.urlopen") as open_mock:
                result = get_secondary_weather(_LAT, _LON, _DAY)
        finally:
            for context in contexts:
                context.stop()

        assert not result.available
        open_mock.assert_not_called()


class TestPayloadValidation:
    def test_error_envelope_is_rejected(self):
        result, _ = _run({"errors": ["quota"], "weather": {"temp_c": 12.0, "condition": "mild"}})
        assert not result.available

    def test_missing_weather_block_is_rejected(self):
        result, _ = _run({"data": {"temp_c": 12.0}})
        assert not result.available

    def test_unknown_condition_is_rejected(self):
        result, _ = _run({"weather": {"temp_c": 12.0, "condition": "sandstorm"}})
        assert not result.available

    def test_boolean_temperature_is_rejected(self):
        result, _ = _run({"weather": {"temp_c": True, "condition": "mild"}})
        assert not result.available

    def test_implausible_temperature_is_rejected(self):
        result, _ = _run({"weather": {"temp_c": 500.0, "condition": "hot"}})
        assert not result.available

    def test_missing_condition_is_rejected(self):
        result, _ = _run({"weather": {"temp_c": 12.0}})
        assert not result.available

    def test_numeric_string_temperature_is_accepted(self):
        result, _ = _run({"weather": {"temp_c": "12.5", "condition": "mild"}})
        assert result.available
        assert result.temp_c == 12.5

    def test_oversized_body_is_rejected(self):
        clear_secondary_weather_cache()
        contexts = _settings()
        for context in contexts:
            context.start()
        try:
            with patch.object(
                settings, "FOOTBALL_LIVE_WEATHER_SECONDARY_MAX_BYTES", 16
            ), patch(
                "app.services.football_live_weather_service.urlopen",
                return_value=_BytesResponse(b"x" * 64),
            ):
                result = get_secondary_weather(_LAT, _LON, _DAY)
        finally:
            for context in contexts:
                context.stop()

        assert not result.available

    def test_undecodable_body_is_rejected(self):
        clear_secondary_weather_cache()
        contexts = _settings()
        for context in contexts:
            context.start()
        try:
            with patch(
                "app.services.football_live_weather_service.urlopen",
                return_value=_BytesResponse(b"\xff\xfe not json"),
            ):
                result = get_secondary_weather(_LAT, _LON, _DAY)
        finally:
            for context in contexts:
                context.stop()

        assert not result.available


class TestTransportAndCache:
    def test_transport_error_is_unavailable(self):
        clear_secondary_weather_cache()
        contexts = _settings()
        for context in contexts:
            context.start()
        try:
            with patch(
                "app.services.football_live_weather_service.urlopen",
                side_effect=OSError("boom"),
            ):
                result = get_secondary_weather(_LAT, _LON, _DAY)
        finally:
            for context in contexts:
                context.stop()

        assert not result.available

    def test_valid_reading_is_cached_for_repeat_lookups(self):
        clear_secondary_weather_cache()
        contexts = _settings()
        for context in contexts:
            context.start()
        try:
            with patch(
                "app.services.football_live_weather_service.urlopen",
                return_value=_Response({"weather": {"temp_c": 9.0, "condition": "rain"}}),
            ) as open_mock:
                first = get_secondary_weather(_LAT, _LON, _DAY)
                second = get_secondary_weather(_LAT, _LON, _DAY)
        finally:
            for context in contexts:
                context.stop()

        assert first.available and second.available
        assert second.temp_c == 9.0
        assert open_mock.call_count == 1

    def test_failure_is_not_cached(self):
        clear_secondary_weather_cache()
        contexts = _settings()
        for context in contexts:
            context.start()
        try:
            with patch(
                "app.services.football_live_weather_service.urlopen",
                side_effect=OSError("boom"),
            ) as open_mock:
                get_secondary_weather(_LAT, _LON, _DAY)
                get_secondary_weather(_LAT, _LON, _DAY)
        finally:
            for context in contexts:
                context.stop()

        assert open_mock.call_count == 2

    def test_distinct_days_are_cached_separately(self):
        clear_secondary_weather_cache()
        contexts = _settings()
        for context in contexts:
            context.start()
        try:
            with patch(
                "app.services.football_live_weather_service.urlopen",
                return_value=_Response({"weather": {"temp_c": 9.0, "condition": "rain"}}),
            ) as open_mock:
                get_secondary_weather(_LAT, _LON, "2026-09-16")
                get_secondary_weather(_LAT, _LON, "2026-09-17")
        finally:
            for context in contexts:
                context.stop()

        assert open_mock.call_count == 2
