import json
from unittest.mock import patch

from app.core.config import settings
from app.services.football_live_referee_service import (
    clear_live_referee_cache,
    get_live_referee,
)


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
        patch.object(settings, "FOOTBALL_LIVE_REFEREE_ENABLED", True),
        patch.object(
            settings,
            "FOOTBALL_LIVE_REFEREE_URL",
            "https://ref.example/snapshot?scope=refs",
        ),
        patch.object(settings, "FOOTBALL_LIVE_REFEREE_API_KEY", "secret-key"),
        patch.object(settings, "FOOTBALL_LIVE_REFEREE_SEASON_PARAM", "season"),
        patch.object(settings, "FOOTBALL_LIVE_REFEREE_CACHE_TTL_HOURS", 1.0),
    )


def test_disabled_referee_provider_makes_no_request():
    clear_live_referee_cache()
    with patch.object(settings, "FOOTBALL_LIVE_REFEREE_ENABLED", False), patch(
        "app.services.football_live_referee_service.urlopen"
    ) as open_mock:
        result = get_live_referee("epl", "2026-27", "Michael Oliver")

    assert not result.available
    open_mock.assert_not_called()


def test_empty_api_key_makes_no_request():
    clear_live_referee_cache()
    with patch.object(settings, "FOOTBALL_LIVE_REFEREE_ENABLED", True), patch.object(
        settings, "FOOTBALL_LIVE_REFEREE_URL", "https://ref.example/snapshot"
    ), patch.object(settings, "FOOTBALL_LIVE_REFEREE_API_KEY", ""), patch(
        "app.services.football_live_referee_service.urlopen"
    ) as open_mock:
        result = get_live_referee("epl", "2026-27", "Michael Oliver")

    assert not result.available
    open_mock.assert_not_called()


def test_live_referee_reads_normalized_data_and_authenticates():
    clear_live_referee_cache()
    contexts = _settings()
    payload = {
        "referees": [
            {"referee": "Michael Oliver", "home_win_rate": 0.54, "matches": 24}
        ]
    }
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_referee_service.urlopen",
        return_value=_Response(payload),
    ) as open_mock:
        result = get_live_referee("epl", "2026-27", "  MICHAEL OLIVER ")

    assert result.available
    assert result.home_win_rate == 0.54
    assert result.matches == 24
    request = open_mock.call_args.args[0]
    assert request.full_url == (
        "https://ref.example/snapshot?scope=refs&competition=epl&season=2026"
    )
    assert request.headers["Authorization"] == "Bearer secret-key"


def test_live_referee_replaces_preconfigured_scope_parameters():
    clear_live_referee_cache()
    contexts = _settings()
    with contexts[0], patch.object(
        settings,
        "FOOTBALL_LIVE_REFEREE_URL",
        "https://ref.example/snapshot?competition=old&season=2025&scope=refs",
    ), contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_referee_service.urlopen",
        return_value=_Response({"referees": []}),
    ) as open_mock:
        get_live_referee("epl", "2026-27", "Michael Oliver")

    request = open_mock.call_args.args[0]
    assert request.full_url == (
        "https://ref.example/snapshot?scope=refs&competition=epl&season=2026"
    )


def test_successful_snapshot_without_referee_row_is_available_but_empty():
    clear_live_referee_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_referee_service.urlopen",
        return_value=_Response({"referees": []}),
    ):
        result = get_live_referee("epl", "2026-27", "Michael Oliver")

    assert result.available
    assert result.home_win_rate is None
    assert result.matches is None


def test_malformed_duplicate_or_invalid_snapshot_is_unavailable():
    invalid_payloads = [
        {
            "referees": [
                {"referee": "Michael Oliver", "home_win_rate": 0.54, "matches": 24},
                {"referee": "Michael-Oliver", "home_win_rate": 0.55, "matches": 25},
            ]
        },
        {"referees": [{"referee": "Michael Oliver", "home_win_rate": 1.2, "matches": 24}]},
        {"referees": [{"referee": "Michael Oliver", "home_win_rate": 0.54, "matches": 0}]},
        {"referees": [{"referee": "Michael Oliver", "home_win_rate": 0.54}]},
        {"errors": {"detail": "bad request"}},
    ]
    for payload in invalid_payloads:
        clear_live_referee_cache()
        contexts = _settings()
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
            "app.services.football_live_referee_service.urlopen",
            return_value=_Response(payload),
        ):
            assert not get_live_referee("epl", "2026-27", "Michael Oliver").available


def test_oversized_or_failed_response_is_unavailable():
    clear_live_referee_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch.object(
        settings, "FOOTBALL_LIVE_REFEREE_MAX_BYTES", 1
    ), patch(
        "app.services.football_live_referee_service.urlopen",
        return_value=_BytesResponse(b"{}"),
    ):
        assert not get_live_referee("epl", "2026-27", "Michael Oliver").available

    clear_live_referee_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_referee_service.urlopen",
        side_effect=OSError("network unavailable"),
    ):
        assert not get_live_referee("epl", "2026-27", "Michael Oliver").available


def test_referee_snapshot_cache_reuses_provider_call():
    clear_live_referee_cache()
    contexts = _settings()
    payload = {
        "referees": [
            {"referee": "Michael Oliver", "home_win_rate": 0.54, "matches": 24}
        ]
    }
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_referee_service.urlopen",
        return_value=_Response(payload),
    ) as open_mock:
        get_live_referee("epl", "2026-27", "Michael Oliver")
        get_live_referee("epl", "2026-27", "Anthony Taylor")

    assert open_mock.call_count == 1
