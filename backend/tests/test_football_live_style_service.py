import json
from unittest.mock import patch

from app.core.config import settings
from app.services.football_live_style_service import (
    clear_live_style_cache,
    get_live_style,
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
        patch.object(settings, "FOOTBALL_LIVE_STYLE_ENABLED", True),
        patch.object(
            settings,
            "FOOTBALL_LIVE_STYLE_URL",
            "https://style.example/snapshot?scope=clubs",
        ),
        patch.object(settings, "FOOTBALL_LIVE_STYLE_API_KEY", "secret-key"),
        patch.object(settings, "FOOTBALL_LIVE_STYLE_SEASON_PARAM", "season"),
        patch.object(settings, "FOOTBALL_LIVE_STYLE_CACHE_TTL_HOURS", 1.0),
    )


def test_disabled_style_provider_makes_no_request():
    clear_live_style_cache()
    with patch.object(settings, "FOOTBALL_LIVE_STYLE_ENABLED", False), patch(
        "app.services.football_live_style_service.urlopen"
    ) as open_mock:
        result = get_live_style("epl", "2026-27", "Arsenal")

    assert not result.available
    open_mock.assert_not_called()


def test_empty_api_key_makes_no_request():
    clear_live_style_cache()
    with patch.object(settings, "FOOTBALL_LIVE_STYLE_ENABLED", True), patch.object(
        settings, "FOOTBALL_LIVE_STYLE_URL", "https://style.example/snapshot"
    ), patch.object(settings, "FOOTBALL_LIVE_STYLE_API_KEY", ""), patch(
        "app.services.football_live_style_service.urlopen"
    ) as open_mock:
        result = get_live_style("epl", "2026-27", "Arsenal")

    assert not result.available
    open_mock.assert_not_called()


def test_live_style_reads_normalized_data_and_authenticates():
    clear_live_style_cache()
    contexts = _settings()
    payload = {
        "teams": [
            {
                "team": "Arsenal FC",
                "possession_pct": 57.2,
                "shots_per90": 15.1,
                "ppda": 9.3,
            }
        ]
    }
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_style_service.urlopen",
        return_value=_Response(payload),
    ) as open_mock:
        result = get_live_style("epl", "2026-27", "Arsenal")

    assert result.available
    assert result.style == {
        "possession_pct": 57.2,
        "shots_per90": 15.1,
        "ppda": 9.3,
    }
    request = open_mock.call_args.args[0]
    assert request.full_url == (
        "https://style.example/snapshot?scope=clubs&competition=epl&season=2026"
    )
    assert request.headers["Authorization"] == "Bearer secret-key"


def test_live_style_replaces_preconfigured_scope_parameters():
    clear_live_style_cache()
    contexts = _settings()
    with contexts[0], patch.object(
        settings,
        "FOOTBALL_LIVE_STYLE_URL",
        "https://style.example/snapshot?competition=old&season=2025&scope=clubs",
    ), contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_style_service.urlopen",
        return_value=_Response({"teams": []}),
    ) as open_mock:
        get_live_style("epl", "2026-27", "Arsenal")

    request = open_mock.call_args.args[0]
    assert request.full_url == (
        "https://style.example/snapshot?scope=clubs&competition=epl&season=2026"
    )


def test_successful_snapshot_without_team_row_is_available_but_empty():
    clear_live_style_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_style_service.urlopen",
        return_value=_Response({"teams": []}),
    ):
        result = get_live_style("epl", "2026-27", "Arsenal")

    assert result.available
    assert result.style is None


def test_malformed_duplicate_or_invalid_style_snapshot_is_unavailable():
    invalid_payloads = [
        {
            "teams": [
                {
                    "team": "Arsenal",
                    "possession_pct": 57.2,
                    "shots_per90": 15.1,
                    "ppda": 9.3,
                },
                {
                    "team": "Arsenal FC",
                    "possession_pct": 57.0,
                    "shots_per90": 15.0,
                    "ppda": 9.0,
                },
            ]
        },
        {
            "teams": [
                {
                    "team": "Arsenal",
                    "possession_pct": 90.0,
                    "shots_per90": 15.1,
                    "ppda": 9.3,
                }
            ]
        },
        {"teams": [{"team": "Arsenal", "possession_pct": 57.2, "shots_per90": 15.1}]},
        {"errors": {"detail": "bad request"}},
    ]
    for payload in invalid_payloads:
        clear_live_style_cache()
        contexts = _settings()
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
            "app.services.football_live_style_service.urlopen",
            return_value=_Response(payload),
        ):
            assert not get_live_style("epl", "2026-27", "Arsenal").available


def test_oversized_or_failed_response_is_unavailable():
    clear_live_style_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch.object(
        settings, "FOOTBALL_LIVE_STYLE_MAX_BYTES", 1
    ), patch(
        "app.services.football_live_style_service.urlopen",
        return_value=_BytesResponse(b"{}"),
    ):
        assert not get_live_style("epl", "2026-27", "Arsenal").available

    clear_live_style_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_style_service.urlopen",
        side_effect=OSError("network unavailable"),
    ):
        assert not get_live_style("epl", "2026-27", "Arsenal").available


    clear_live_style_cache()
    contexts = _settings()
    payload = {
        "teams": [
            {
                "team": "Arsenal",
                "possession_pct": 57.2,
                "shots_per90": 15.1,
                "ppda": 9.3,
            }
        ]
    }
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_style_service.urlopen",
        return_value=_Response(payload),
    ) as open_mock:
        get_live_style("epl", "2026-27", "Arsenal")
        get_live_style("epl", "2026-27", "Chelsea")

    assert open_mock.call_count == 1
