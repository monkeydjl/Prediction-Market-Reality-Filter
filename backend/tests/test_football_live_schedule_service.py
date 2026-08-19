import json
from datetime import datetime, timezone
from unittest.mock import patch

from app.core.config import settings
from app.services.football_live_schedule_service import (
    clear_live_schedule_cache,
    get_live_schedule,
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
        patch.object(settings, "FOOTBALL_LIVE_SCHEDULE_ENABLED", True),
        patch.object(
            settings,
            "FOOTBALL_LIVE_SCHEDULE_URL",
            "https://schedule.example/snapshot?scope=clubs",
        ),
        patch.object(settings, "FOOTBALL_LIVE_SCHEDULE_API_KEY", "secret-key"),
        patch.object(settings, "FOOTBALL_LIVE_SCHEDULE_SEASON_PARAM", "season"),
        patch.object(settings, "FOOTBALL_LIVE_SCHEDULE_CACHE_TTL_HOURS", 1.0),
        patch.object(settings, "FOOTBALL_LIVE_SCHEDULE_HISTORY_DAYS", 14),
    )


def _fixture(match_id: str, kickoff_utc: str) -> dict:
    return {
        "match_id": match_id,
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "kickoff_utc": kickoff_utc,
        "status": "scheduled",
    }


def test_disabled_schedule_provider_makes_no_request():
    clear_live_schedule_cache()
    with patch.object(settings, "FOOTBALL_LIVE_SCHEDULE_ENABLED", False), patch(
        "app.services.football_live_schedule_service.urlopen"
    ) as open_mock:
        result = get_live_schedule("epl", "2026-27")

    assert not result.available
    open_mock.assert_not_called()


def test_empty_api_key_makes_no_request():
    clear_live_schedule_cache()
    with patch.object(settings, "FOOTBALL_LIVE_SCHEDULE_ENABLED", True), patch.object(
        settings, "FOOTBALL_LIVE_SCHEDULE_URL", "https://schedule.example/snapshot"
    ), patch.object(settings, "FOOTBALL_LIVE_SCHEDULE_API_KEY", ""), patch(
        "app.services.football_live_schedule_service.urlopen"
    ) as open_mock:
        result = get_live_schedule("epl", "2026-27")

    assert not result.available
    open_mock.assert_not_called()


def test_live_schedule_authenticates_and_filters_history_window():
    clear_live_schedule_cache()
    contexts = _settings()
    payload = {
        "fixtures": [
            _fixture("recent", "2026-08-15T15:00:00Z"),
            _fixture("old", "2026-07-20T15:00:00Z"),
            _fixture("future", "2026-08-20T15:00:00Z"),
        ]
    }
    before = datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc)
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], patch(
        "app.services.football_live_schedule_service.urlopen",
        return_value=_Response(payload),
    ) as open_mock:
        result = get_live_schedule("epl", "2026-27", before)

    assert result.available
    assert result.fixtures is not None
    assert [fixture["match_id"] for fixture in result.fixtures] == ["recent"]
    request = open_mock.call_args.args[0]
    assert request.full_url == (
        "https://schedule.example/snapshot?scope=clubs&competition=epl&season=2026"
    )
    assert request.headers["Authorization"] == "Bearer secret-key"


def test_live_schedule_replaces_preconfigured_scope_parameters():
    clear_live_schedule_cache()
    contexts = _settings()
    with contexts[0], patch.object(
        settings,
        "FOOTBALL_LIVE_SCHEDULE_URL",
        "https://schedule.example/snapshot?competition=old&season=2025&scope=clubs",
    ), contexts[2], contexts[3], contexts[4], contexts[5], patch(
        "app.services.football_live_schedule_service.urlopen",
        return_value=_Response({"fixtures": []}),
    ) as open_mock:
        get_live_schedule("epl", "2026-27")

    request = open_mock.call_args.args[0]
    assert request.full_url == (
        "https://schedule.example/snapshot?scope=clubs&competition=epl&season=2026"
    )


def test_successful_empty_snapshot_is_available():
    clear_live_schedule_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], patch(
        "app.services.football_live_schedule_service.urlopen",
        return_value=_Response({"fixtures": []}),
    ):
        result = get_live_schedule("epl", "2026-27")

    assert result.available
    assert result.fixtures == []


def test_malformed_duplicate_or_invalid_fixture_snapshot_is_unavailable():
    invalid_payloads = [
        {"fixtures": [_fixture("same", "2026-08-15T15:00:00Z"), _fixture("same", "2026-08-16T15:00:00Z")]},
        {"fixtures": [{**_fixture("bad-status", "2026-08-15T15:00:00Z"), "status": "unknown"}]},
        {"fixtures": [{**_fixture("naive", "2026-08-15T15:00:00"), "status": "scheduled"}]},
        {"fixtures": [{**_fixture("same-teams", "2026-08-15T15:00:00Z"), "away_team": "arsenal"}]},
        {"errors": {"detail": "bad request"}},
    ]
    for payload in invalid_payloads:
        clear_live_schedule_cache()
        contexts = _settings()
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], patch(
            "app.services.football_live_schedule_service.urlopen",
            return_value=_Response(payload),
        ):
            assert not get_live_schedule("epl", "2026-27").available


def test_oversized_or_failed_response_is_unavailable():
    clear_live_schedule_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], patch.object(
        settings, "FOOTBALL_LIVE_SCHEDULE_MAX_BYTES", 1
    ), patch(
        "app.services.football_live_schedule_service.urlopen",
        return_value=_BytesResponse(b"{}"),
    ):
        assert not get_live_schedule("epl", "2026-27").available

    clear_live_schedule_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], patch(
        "app.services.football_live_schedule_service.urlopen",
        side_effect=OSError("network unavailable"),
    ):
        assert not get_live_schedule("epl", "2026-27").available


def test_schedule_snapshot_cache_reuses_provider_call():
    clear_live_schedule_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], patch(
        "app.services.football_live_schedule_service.urlopen",
        return_value=_Response({"fixtures": [_fixture("one", "2026-08-15T15:00:00Z")]}),
    ) as open_mock:
        get_live_schedule("epl", "2026-27")
        get_live_schedule("epl", "2026-27")

    assert open_mock.call_count == 1
