import json
from unittest.mock import patch

from app.core.config import settings
from app.services.football_live_availability_service import (
    clear_live_availability_cache,
    get_live_availability_impact,
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


def _settings():
    return (
        patch.object(settings, "FOOTBALL_LIVE_AVAILABILITY_ENABLED", True),
        patch.object(
            settings,
            "FOOTBALL_LIVE_AVAILABILITY_URL",
            "https://availability.example/snapshot?scope=clubs",
        ),
        patch.object(settings, "FOOTBALL_LIVE_AVAILABILITY_API_KEY", "secret-key"),
        patch.object(settings, "FOOTBALL_LIVE_AVAILABILITY_SEASON_PARAM", "season"),
        patch.object(settings, "FOOTBALL_LIVE_AVAILABILITY_CACHE_TTL_HOURS", 1.0),
    )


def _absence(
    player: str = "Example Player",
    minutes_share: float = 0.11,
    market_value_share: float = 0.14,
) -> dict:
    return {
        "player": player,
        "status": "out",
        "role": "starter",
        "minutes_share": minutes_share,
        "market_value_share": market_value_share,
    }


def test_disabled_availability_provider_makes_no_request():
    clear_live_availability_cache()
    with patch.object(settings, "FOOTBALL_LIVE_AVAILABILITY_ENABLED", False), patch(
        "app.services.football_live_availability_service.urlopen"
    ) as open_mock:
        result = get_live_availability_impact("epl", "2026-27", "Arsenal")

    assert not result.available
    open_mock.assert_not_called()


def test_empty_api_key_makes_no_request():
    clear_live_availability_cache()
    with patch.object(settings, "FOOTBALL_LIVE_AVAILABILITY_ENABLED", True), patch.object(
        settings,
        "FOOTBALL_LIVE_AVAILABILITY_URL",
        "https://availability.example/snapshot",
    ), patch.object(settings, "FOOTBALL_LIVE_AVAILABILITY_API_KEY", ""), patch(
        "app.services.football_live_availability_service.urlopen"
    ) as open_mock:
        result = get_live_availability_impact("epl", "2026-27", "Arsenal")

    assert not result.available
    open_mock.assert_not_called()


def test_live_availability_authenticates_and_uses_contextual_impact():
    clear_live_availability_cache()
    contexts = _settings()
    payload = {
        "teams": [
            {
                "team": "Arsenal FC",
                "absences": [_absence(minutes_share=0.11, market_value_share=0.14)],
            }
        ]
    }
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_availability_service.urlopen",
        return_value=_Response(payload),
    ) as open_mock:
        result = get_live_availability_impact("epl", "2026-27", "Arsenal")

    assert result.available
    assert result.impact == 0.35
    request = open_mock.call_args.args[0]
    assert request.full_url == (
        "https://availability.example/snapshot?scope=clubs&competition=epl&season=2026"
    )
    assert request.headers["Authorization"] == "Bearer secret-key"


def test_live_availability_replaces_preconfigured_scope_parameters():
    clear_live_availability_cache()
    contexts = _settings()
    with contexts[0], patch.object(
        settings,
        "FOOTBALL_LIVE_AVAILABILITY_URL",
        "https://availability.example/snapshot?competition=old&season=2025&scope=clubs",
    ), contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_availability_service.urlopen",
        return_value=_Response({"teams": []}),
    ) as open_mock:
        get_live_availability_impact("epl", "2026-27", "Arsenal")

    request = open_mock.call_args.args[0]
    assert request.full_url == (
        "https://availability.example/snapshot?scope=clubs&competition=epl&season=2026"
    )


def test_successful_snapshot_without_team_row_is_available_but_empty():
    clear_live_availability_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_availability_service.urlopen",
        return_value=_Response({"teams": []}),
    ):
        result = get_live_availability_impact("epl", "2026-27", "Arsenal")

    assert result.available
    assert result.impact is None


def test_malformed_duplicate_or_invalid_availability_snapshot_is_unavailable():
    invalid_payloads = [
        {
            "teams": [
                {"team": "Arsenal", "absences": []},
                {"team": "Arsenal FC", "absences": []},
            ]
        },
        {
            "teams": [
                {
                    "team": "Arsenal",
                    "absences": [_absence(), _absence()],
                }
            ]
        },
        {
            "teams": [
                {
                    "team": "Arsenal",
                    "absences": [_absence(minutes_share=1.1)],
                }
            ]
        },
        {"errors": {"detail": "bad request"}},
    ]
    for payload in invalid_payloads:
        clear_live_availability_cache()
        contexts = _settings()
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
            "app.services.football_live_availability_service.urlopen",
            return_value=_Response(payload),
        ):
            assert not get_live_availability_impact("epl", "2026-27", "Arsenal").available


def test_availability_snapshot_cache_reuses_provider_call():
    clear_live_availability_cache()
    contexts = _settings()
    payload = {
        "teams": [
            {"team": "Arsenal", "absences": [_absence()]},
            {"team": "Chelsea", "absences": []},
        ]
    }
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_availability_service.urlopen",
        return_value=_Response(payload),
    ) as open_mock:
        get_live_availability_impact("epl", "2026-27", "Arsenal")
        get_live_availability_impact("epl", "2026-27", "Chelsea")

    assert open_mock.call_count == 1
