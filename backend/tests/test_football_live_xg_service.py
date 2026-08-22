import json
from unittest.mock import patch

from app.core.config import settings
from app.services.football_live_xg_service import clear_live_xg_cache, get_live_xg


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
        patch.object(settings, "FOOTBALL_LIVE_XG_ENABLED", True),
        patch.object(settings, "FOOTBALL_LIVE_XG_URL", "https://xg.example/snapshot?scope=clubs"),
        patch.object(settings, "FOOTBALL_LIVE_XG_API_KEY", "secret-key"),
        patch.object(settings, "FOOTBALL_LIVE_XG_SEASON_PARAM", "season"),
        patch.object(settings, "FOOTBALL_LIVE_XG_CACHE_TTL_HOURS", 1.0),
    )


def test_disabled_xg_provider_makes_no_request():
    clear_live_xg_cache()
    with patch.object(settings, "FOOTBALL_LIVE_XG_ENABLED", False), patch(
        "app.services.football_live_xg_service.urlopen"
    ) as open_mock:
        result = get_live_xg("epl", "2026-27", "Arsenal")

    assert not result.available
    open_mock.assert_not_called()


def test_empty_api_key_makes_no_request():
    clear_live_xg_cache()
    with patch.object(settings, "FOOTBALL_LIVE_XG_ENABLED", True), patch.object(
        settings, "FOOTBALL_LIVE_XG_URL", "https://xg.example/snapshot"
    ), patch.object(settings, "FOOTBALL_LIVE_XG_API_KEY", ""), patch(
        "app.services.football_live_xg_service.urlopen"
    ) as open_mock:
        result = get_live_xg("epl", "2026-27", "Arsenal")

    assert not result.available
    open_mock.assert_not_called()


    clear_live_xg_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_xg_service.urlopen",
        return_value=_Response({"teams": [{"team": "Arsenal FC", "xg_per90": 1.72}]}),
    ) as open_mock:
        result = get_live_xg("epl", "2026-27", "Arsenal")

    assert result.available
    assert result.xg_per90 == 1.72
    request = open_mock.call_args.args[0]
    assert request.full_url == "https://xg.example/snapshot?scope=clubs&competition=epl&season=2026"
    assert request.headers["Authorization"] == "Bearer secret-key"


def test_live_xg_replaces_preconfigured_scope_parameters():
    clear_live_xg_cache()
    contexts = _settings()
    with contexts[0], patch.object(
        settings,
        "FOOTBALL_LIVE_XG_URL",
        "https://xg.example/snapshot?competition=old&season=2025&scope=clubs",
    ), contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_xg_service.urlopen",
        return_value=_Response({"teams": []}),
    ) as open_mock:
        get_live_xg("epl", "2026-27", "Arsenal")

    request = open_mock.call_args.args[0]
    assert request.full_url == "https://xg.example/snapshot?scope=clubs&competition=epl&season=2026"


def test_successful_snapshot_without_team_row_is_available_but_empty():
    clear_live_xg_cache()
    contexts = _settings()
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_xg_service.urlopen", return_value=_Response({"teams": []})
    ):
        result = get_live_xg("epl", "2026-27", "Arsenal")

    assert result.available
    assert result.xg_per90 is None


def test_malformed_duplicate_or_invalid_xg_snapshot_is_unavailable():
    invalid_payloads = [
        {"teams": [{"team": "Arsenal", "xg_per90": 1.2}, {"team": "Arsenal FC", "xg_per90": 1.3}]},
        {"teams": [{"team": "Arsenal", "xg_per90": 9.0}]},
        {"errors": {"detail": "bad request"}},
    ]
    for payload in invalid_payloads:
        clear_live_xg_cache()
        contexts = _settings()
        with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
            "app.services.football_live_xg_service.urlopen", return_value=_Response(payload)
        ):
            assert not get_live_xg("epl", "2026-27", "Arsenal").available


def test_xg_snapshot_cache_reuses_provider_call():
    clear_live_xg_cache()
    contexts = _settings()
    payload = {"teams": [{"team": "Arsenal", "xg_per90": 1.72}]}
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], patch(
        "app.services.football_live_xg_service.urlopen", return_value=_Response(payload)
    ) as open_mock:
        get_live_xg("epl", "2026-27", "Arsenal")
        get_live_xg("epl", "2026-27", "Chelsea")

    assert open_mock.call_count == 1
