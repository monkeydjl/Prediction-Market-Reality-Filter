import json
from unittest.mock import patch

from app.core.config import settings
from app.services.football_live_injury_service import (
    clear_live_injury_cache,
    get_live_injury_impact,
)


class _Response:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, _size: int) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _settings():
    return (
        patch.object(settings, "FOOTBALL_LIVE_INJURIES_ENABLED", True),
        patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "secret-key"),
        patch.object(settings, "WORLD_CUP_API_FOOTBALL_BASE_URL", "https://api.example/v3"),
        patch.object(settings, "FOOTBALL_LIVE_INJURIES_LEAGUE_IDS", "epl:39"),
        patch.object(settings, "FOOTBALL_LIVE_INJURIES_CACHE_TTL_HOURS", 1.0),
    )


def test_disabled_source_makes_no_request():
    clear_live_injury_cache()
    with patch.object(settings, "FOOTBALL_LIVE_INJURIES_ENABLED", False), patch(
        "app.services.football_live_injury_service.urlopen"
    ) as open_mock:
        result = get_live_injury_impact("epl", "2026-27", "Arsenal FC")

    assert not result.available
    open_mock.assert_not_called()


def test_live_snapshot_normalizes_names_and_weights_absences():
    clear_live_injury_cache()
    payload = {
        "errors": [],
        "response": [
            {
                "team": {"name": "Arsenal"},
                "player": {"name": "A", "reason": "Hamstring", "role": "starter"},
            },
            {
                "team": {"name": "Arsenal"},
                "player": {"name": "B", "reason": "Knee"},
            },
            {
                "team": {"name": "Arsenal"},
                "player": {"name": "C", "status": "fit"},
            },
        ],
    }
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_injury_service.urlopen", return_value=_Response(payload)
    ) as open_mock:
        result = get_live_injury_impact("epl", "2026-27", "Arsenal FC")

    assert result.available
    assert result.impact == 0.21
    request = open_mock.call_args.args[0]
    assert request.full_url == "https://api.example/v3/injuries?league=39&season=2026"
    assert request.headers["X-apisports-key"] == "secret-key"


def test_successful_snapshot_without_team_absence_is_not_fallback_failure():
    clear_live_injury_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_injury_service.urlopen",
        return_value=_Response({"errors": [], "response": []}),
    ):
        result = get_live_injury_impact("epl", "2026-27", "Arsenal")

    assert result.available
    assert result.impact is None


def test_snapshot_cache_reuses_one_provider_request():
    clear_live_injury_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_injury_service.urlopen",
        return_value=_Response({"errors": [], "response": []}),
    ) as open_mock:
        get_live_injury_impact("epl", "2026-27", "Arsenal")
        get_live_injury_impact("epl", "2026-27", "Chelsea")

    assert open_mock.call_count == 1


def test_bad_mapping_or_provider_error_is_unavailable_without_leaking_key():
    clear_live_injury_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[4], patch.object(
        settings, "FOOTBALL_LIVE_INJURIES_LEAGUE_IDS", "epl:bad"
    ):
        assert not get_live_injury_impact("epl", "2026-27", "Arsenal").available

    clear_live_injury_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_injury_service.urlopen", side_effect=OSError("secret-key")
    ):
        assert not get_live_injury_impact("epl", "2026-27", "Arsenal").available
