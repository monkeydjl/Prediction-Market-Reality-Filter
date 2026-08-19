"""Tests for the optional NBA availability provider (P1-B1)."""
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.nba_live_injury_service import (
    clear_live_injury_cache,
    get_live_injury_impact,
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


def _settings(**overrides):
    """Patch contexts enabling a configured provider."""
    values = {
        "NBA_LIVE_INJURIES_ENABLED": True,
        "NBA_LIVE_INJURIES_URL": "https://nba.example/availability",
        "NBA_LIVE_INJURIES_API_KEY": "secret-key",
        "NBA_LIVE_INJURIES_TIMEOUT_S": 5.0,
        "NBA_LIVE_INJURIES_CACHE_TTL_HOURS": 1.0,
        "NBA_LIVE_INJURIES_MAX_BYTES": 262144,
    }
    values.update(overrides)
    return tuple(patch.object(settings, name, value) for name, value in values.items())


def _lookup(team, response=None, *, side_effect=None, **overrides):
    """Look a team up with urlopen stubbed; returns (result, urlopen mock)."""
    opener = (
        patch("app.services.nba_live_injury_service.urlopen", side_effect=side_effect)
        if side_effect is not None
        else patch("app.services.nba_live_injury_service.urlopen", return_value=response)
    )
    with ExitStack() as stack:
        mock_open = stack.enter_context(opener)
        for ctx in _settings(**overrides):
            stack.enter_context(ctx)
        return get_live_injury_impact(team), mock_open


_SNAPSHOT = {
    "teams": [
        {
            "team": "Boston Celtics",
            "absences": [
                {"player": "A Star", "role": "star", "status": "out"},
                {"player": "A Starter", "role": "starter", "status": "out"},
            ],
        },
        {"team": "Miami Heat", "absences": []},
    ]
}


class TestConfigurationGate:
    def setup_method(self):
        clear_live_injury_cache()

    def test_disabled_makes_no_request(self):
        result, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT), NBA_LIVE_INJURIES_ENABLED=False,
        )
        assert result.available is False
        opener.assert_not_called()

    def test_missing_url_makes_no_request(self):
        result, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT), NBA_LIVE_INJURIES_URL="",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_missing_api_key_makes_no_request(self):
        result, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT), NBA_LIVE_INJURIES_API_KEY="   ",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_non_http_scheme_makes_no_request(self):
        result, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT),
            NBA_LIVE_INJURIES_URL="file:///etc/passwd",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_blank_team_makes_no_request(self):
        result, opener = _lookup("   ", _Response(_SNAPSHOT))
        assert result.available is False
        opener.assert_not_called()


class TestSnapshotReading:
    def setup_method(self):
        clear_live_injury_cache()

    def test_absences_sum_role_weights(self):
        result, _ = _lookup("Boston Celtics", _Response(_SNAPSHOT))
        assert result.available is True
        # star 0.35 + starter 0.18, via the shared summarizer.
        assert result.impact == pytest.approx(0.53)

    def test_team_without_absence_is_available_but_unscored(self):
        result, _ = _lookup("Miami Heat", _Response(_SNAPSHOT))
        assert result.available is True
        # Explicitly not a known-healthy 0.0 — the static table still applies.
        assert result.impact is None

    def test_team_absent_from_snapshot_is_available_but_unscored(self):
        result, _ = _lookup("Denver Nuggets", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.impact is None

    def test_team_name_matching_ignores_case_and_punctuation(self):
        result, _ = _lookup("  boston   celtics!  ", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.impact == pytest.approx(0.53)

    def test_unknown_role_falls_back_to_bench_weight(self):
        payload = {
            "teams": [{
                "team": "Boston Celtics",
                "absences": [{"role": "sixth-man", "status": "out"}],
            }]
        }
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.impact == pytest.approx(0.03)

    def test_non_out_statuses_are_not_counted(self):
        payload = {
            "teams": [{
                "team": "Boston Celtics",
                "absences": [
                    {"role": "star", "status": "questionable"},
                    {"role": "star", "status": "probable"},
                    {"role": "star", "status": "day-to-day"},
                ],
            }]
        }
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.available is True
        assert result.impact is None

    def test_inactive_and_suspended_count_as_out(self):
        payload = {
            "teams": [{
                "team": "Boston Celtics",
                "absences": [
                    {"role": "starter", "status": "inactive"},
                    {"role": "rotation", "status": "suspended"},
                ],
            }]
        }
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.impact == pytest.approx(0.26)

    def test_impact_is_clamped_to_one(self):
        payload = {
            "teams": [{
                "team": "Boston Celtics",
                "absences": [{"role": "star", "status": "out"} for _ in range(5)],
            }]
        }
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.impact == pytest.approx(1.0)

    def test_missing_absences_key_is_treated_as_no_absence(self):
        payload = {"teams": [{"team": "Boston Celtics"}]}
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.available is True
        assert result.impact is None


class TestFailClosed:
    def setup_method(self):
        clear_live_injury_cache()

    @pytest.mark.parametrize("payload", [
        {"errors": ["quota exceeded"], "teams": []},
        {"teams": "not-a-list"},
        {"teams": [{"team": "", "absences": []}]},
        {"teams": [{"absences": []}]},
        {"teams": ["not-an-object"]},
        {"teams": [{"team": "Boston Celtics", "absences": "not-a-list"}]},
        {"no_teams_key": True},
        ["not", "an", "object"],
    ])
    def test_malformed_payloads_report_unavailable(self, payload):
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.available is False

    def test_duplicate_team_blocks_report_unavailable(self):
        payload = {
            "teams": [
                {"team": "Boston Celtics", "absences": [{"role": "star", "status": "out"}]},
                {"team": "boston celtics", "absences": []},
            ]
        }
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.available is False

    def test_invalid_json_reports_unavailable(self):
        result, _ = _lookup("Boston Celtics", _BytesResponse(b"{not json"))
        assert result.available is False

    def test_non_utf8_body_reports_unavailable(self):
        result, _ = _lookup("Boston Celtics", _BytesResponse(b"\xff\xfe\x00"))
        assert result.available is False

    def test_oversize_body_reports_unavailable(self):
        body = json.dumps(_SNAPSHOT).encode("utf-8")
        result, _ = _lookup(
            "Boston Celtics", _BytesResponse(body),
            NBA_LIVE_INJURIES_MAX_BYTES=len(body) - 1,
        )
        assert result.available is False

    def test_body_exactly_at_cap_is_accepted(self):
        body = json.dumps(_SNAPSHOT).encode("utf-8")
        result, _ = _lookup(
            "Boston Celtics", _BytesResponse(body),
            NBA_LIVE_INJURIES_MAX_BYTES=len(body),
        )
        assert result.available is True

    def test_zero_byte_cap_reports_unavailable(self):
        result, _ = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT), NBA_LIVE_INJURIES_MAX_BYTES=0,
        )
        assert result.available is False

    def test_transport_error_reports_unavailable(self):
        result, _ = _lookup("Boston Celtics", None, side_effect=OSError("boom"))
        assert result.available is False

    def test_timeout_reports_unavailable(self):
        result, _ = _lookup("Boston Celtics", None, side_effect=TimeoutError("slow"))
        assert result.available is False

    def test_unreadable_ttl_reports_unavailable(self):
        result, _ = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT),
            NBA_LIVE_INJURIES_CACHE_TTL_HOURS="not-a-number",
        )
        assert result.available is False


class TestCaching:
    def setup_method(self):
        clear_live_injury_cache()

    def test_second_lookup_reuses_snapshot(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nba_live_injury_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_injury_impact("Boston Celtics")
            second = get_live_injury_impact("Miami Heat")

        assert first.available and second.available
        assert opener.call_count == 1

    def test_failure_is_not_cached(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nba_live_injury_service.urlopen",
                side_effect=[OSError("boom"), _Response(_SNAPSHOT)],
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_injury_impact("Boston Celtics")
            second = get_live_injury_impact("Boston Celtics")

        assert first.available is False
        assert second.available is True
        assert opener.call_count == 2

    def test_zero_ttl_refetches(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nba_live_injury_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings(NBA_LIVE_INJURIES_CACHE_TTL_HOURS=0.0):
                stack.enter_context(ctx)
            get_live_injury_impact("Boston Celtics")
            get_live_injury_impact("Boston Celtics")

        assert opener.call_count == 2

    def test_cache_can_be_cleared(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nba_live_injury_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            get_live_injury_impact("Boston Celtics")
            clear_live_injury_cache()
            get_live_injury_impact("Boston Celtics")

        assert opener.call_count == 2


class TestCredentialHandling:
    def setup_method(self):
        clear_live_injury_cache()

    def test_key_is_sent_only_as_bearer_header(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nba_live_injury_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            get_live_injury_impact("Boston Celtics")

        request = opener.call_args.args[0]
        assert request.get_header("Authorization") == "Bearer secret-key"
        assert "secret-key" not in request.full_url

    def test_result_carries_no_credential(self):
        result, _ = _lookup("Boston Celtics", _Response(_SNAPSHOT))
        assert "secret-key" not in repr(result)
