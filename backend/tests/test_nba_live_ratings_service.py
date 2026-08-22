"""Tests for the optional NBA dynamic-season efficiency provider (P1-B4)."""
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.nba_live_ratings_service import (
    clear_live_ratings_cache,
    get_live_team_ratings,
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
        "NBA_LIVE_RATINGS_ENABLED": True,
        "NBA_LIVE_RATINGS_URL": "https://nba.example/efficiency",
        "NBA_LIVE_RATINGS_API_KEY": "secret-key",
        "NBA_LIVE_RATINGS_SEASON_PARAM": "season",
        "NBA_LIVE_RATINGS_TIMEOUT_S": 5.0,
        "NBA_LIVE_RATINGS_CACHE_TTL_HOURS": 6.0,
        "NBA_LIVE_RATINGS_MAX_BYTES": 262144,
        "NBA_LIVE_RATINGS_MIN_POSSESSIONS": 500.0,
    }
    values.update(overrides)
    return tuple(patch.object(settings, name, value) for name, value in values.items())


def _lookup(team, response=None, *, season="2024-25", side_effect=None, **overrides):
    """Look a team up with urlopen stubbed; returns (result, urlopen mock)."""
    opener = (
        patch("app.services.nba_live_ratings_service.urlopen", side_effect=side_effect)
        if side_effect is not None
        else patch("app.services.nba_live_ratings_service.urlopen", return_value=response)
    )
    with ExitStack() as stack:
        mock_open = stack.enter_context(opener)
        for ctx in _settings(**overrides):
            stack.enter_context(ctx)
        return get_live_team_ratings(season, team), mock_open


# Boston: 9600 / 8000 possessions = 120.0 ORtg, 8720 / 8000 = 109.0 DRtg.
# Miami's sample is below the configured 500-possession minimum.
_SNAPSHOT = {
    "teams": [
        {
            "team": "Boston Celtics",
            "possessions": 8000.0,
            "points": 9600.0,
            "points_allowed": 8720.0,
        },
        {
            "team": "Miami Heat",
            "possessions": 300.0,
            "points": 340.0,
            "points_allowed": 330.0,
        },
    ]
}


class TestConfigurationGate:
    def setup_method(self):
        clear_live_ratings_cache()

    def test_disabled_makes_no_request(self):
        result, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT), NBA_LIVE_RATINGS_ENABLED=False,
        )
        assert result.available is False
        opener.assert_not_called()

    def test_missing_url_makes_no_request(self):
        result, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT), NBA_LIVE_RATINGS_URL="",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_missing_api_key_makes_no_request(self):
        result, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT), NBA_LIVE_RATINGS_API_KEY="   ",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_blank_season_param_makes_no_request(self):
        result, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT), NBA_LIVE_RATINGS_SEASON_PARAM="",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_non_http_scheme_makes_no_request(self):
        result, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT),
            NBA_LIVE_RATINGS_URL="file:///etc/passwd",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_blank_team_makes_no_request(self):
        result, opener = _lookup("   ", _Response(_SNAPSHOT))
        assert result.available is False
        opener.assert_not_called()

    @pytest.mark.parametrize("season", ["", None, "not-a-season", "24-25"])
    def test_unusable_season_makes_no_request(self, season):
        result, opener = _lookup("Boston Celtics", _Response(_SNAPSHOT), season=season)
        assert result.available is False
        opener.assert_not_called()


class TestRequestUrl:
    def setup_method(self):
        clear_live_ratings_cache()

    def test_season_start_year_is_appended(self):
        _, opener = _lookup("Boston Celtics", _Response(_SNAPSHOT))
        assert opener.call_args.args[0].full_url.endswith("?season=2024")

    def test_existing_season_param_is_replaced(self):
        _, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT),
            NBA_LIVE_RATINGS_URL="https://nba.example/efficiency?season=1999&league=nba",
        )
        url = opener.call_args.args[0].full_url
        assert "league=nba" in url
        assert "season=2024" in url
        assert "season=1999" not in url

    def test_custom_season_param_name_is_used(self):
        _, opener = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT),
            NBA_LIVE_RATINGS_SEASON_PARAM="year",
        )
        assert "year=2024" in opener.call_args.args[0].full_url


class TestSnapshotReading:
    def setup_method(self):
        clear_live_ratings_cache()

    def test_ratings_are_computed_from_true_possessions(self):
        result, _ = _lookup("Boston Celtics", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.ratings is not None
        assert result.ratings["ortg"] == pytest.approx(120.0)
        assert result.ratings["drtg"] == pytest.approx(109.0)
        assert result.ratings["possessions"] == pytest.approx(8000.0)

    def test_precomputed_ratings_without_possessions_are_rejected(self):
        # The whole point of the row is that the value is possession-derived; a
        # payload carrying only ortg/drtg cannot prove that, so it is not trusted.
        payload = {"teams": [{"team": "Boston Celtics", "ortg": 118.2, "drtg": 109.4}]}
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.available is False

    def test_small_sample_is_available_but_unrated(self):
        result, _ = _lookup("Miami Heat", _Response(_SNAPSHOT))
        assert result.available is True
        # Reached, but 300 possessions is below the configured minimum.
        assert result.ratings is None

    def test_lowering_the_minimum_admits_the_small_sample(self):
        result, _ = _lookup(
            "Miami Heat", _Response(_SNAPSHOT), NBA_LIVE_RATINGS_MIN_POSSESSIONS=100.0,
        )
        assert result.ratings is not None
        assert result.ratings["ortg"] == pytest.approx(113.33, abs=0.01)

    def test_sample_exactly_at_the_minimum_is_admitted(self):
        payload = {
            "teams": [{
                "team": "Boston Celtics",
                "possessions": 500.0,
                "points": 600.0,
                "points_allowed": 545.0,
            }]
        }
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.ratings is not None
        assert result.ratings["ortg"] == pytest.approx(120.0)

    def test_team_absent_from_snapshot_is_available_but_unrated(self):
        result, _ = _lookup("Denver Nuggets", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.ratings is None

    def test_team_name_matching_ignores_case_and_punctuation(self):
        result, _ = _lookup("  boston   celtics!  ", _Response(_SNAPSHOT))
        assert result.ratings is not None
        assert result.ratings["ortg"] == pytest.approx(120.0)

    def test_numeric_strings_are_accepted(self):
        payload = {
            "teams": [{
                "team": "Boston Celtics",
                "possessions": "8000",
                "points": "9600",
                "points_allowed": "8720",
            }]
        }
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.ratings is not None
        assert result.ratings["drtg"] == pytest.approx(109.0)

    def test_returned_ratings_are_a_copy(self):
        with ExitStack() as stack:
            stack.enter_context(patch(
                "app.services.nba_live_ratings_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_team_ratings("2024-25", "Boston Celtics")
            assert first.ratings is not None
            first.ratings["ortg"] = 0.0
            # Served from cache: a caller mutating its copy must not poison it.
            second = get_live_team_ratings("2024-25", "Boston Celtics")

        assert second.ratings is not None
        assert second.ratings["ortg"] == pytest.approx(120.0)


class TestFailClosed:
    def setup_method(self):
        clear_live_ratings_cache()

    @pytest.mark.parametrize("payload", [
        {"errors": ["quota exceeded"], "teams": []},
        {"teams": "not-a-list"},
        {"no_teams_key": True},
        ["not", "an", "object"],
        {"teams": ["not-an-object"]},
        {"teams": [{"possessions": 8000, "points": 9600, "points_allowed": 8720}]},
        {"teams": [{"team": "", "possessions": 8000, "points": 9600, "points_allowed": 8720}]},
        {"teams": [{"team": "Boston Celtics", "points": 9600, "points_allowed": 8720}]},
        {"teams": [{"team": "Boston Celtics", "possessions": 8000, "points_allowed": 8720}]},
        {"teams": [{"team": "Boston Celtics", "possessions": 8000, "points": 9600}]},
        {"teams": [{"team": "Boston Celtics", "possessions": 0, "points": 9600, "points_allowed": 8720}]},
        {"teams": [{"team": "Boston Celtics", "possessions": -8000, "points": 9600, "points_allowed": 8720}]},
        {"teams": [{"team": "Boston Celtics", "possessions": 8000, "points": -1, "points_allowed": 8720}]},
        {"teams": [{"team": "Boston Celtics", "possessions": 8000, "points": 9600, "points_allowed": -1}]},
        {"teams": [{"team": "Boston Celtics", "possessions": "many", "points": 9600, "points_allowed": 8720}]},
        {"teams": [{"team": "Boston Celtics", "possessions": True, "points": 9600, "points_allowed": 8720}]},
        {"teams": [{"team": "Boston Celtics", "possessions": None, "points": 9600, "points_allowed": 8720}]},
    ])
    def test_malformed_payloads_report_unavailable(self, payload):
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.available is False

    def test_duplicate_team_blocks_report_unavailable(self):
        payload = {
            "teams": [
                {"team": "Boston Celtics", "possessions": 8000, "points": 9600, "points_allowed": 8720},
                {"team": "boston celtics", "possessions": 8000, "points": 9000, "points_allowed": 8720},
            ]
        }
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.available is False

    @pytest.mark.parametrize("points", [4000.0, 16000.0])
    def test_out_of_band_rating_reports_unavailable(self, points):
        # 50.0 and 200.0 points per 100 are not NBA efficiencies; the payload is
        # not in points-per-100 at all, so the whole feed is rejected.
        payload = {
            "teams": [{
                "team": "Boston Celtics",
                "possessions": 8000.0,
                "points": points,
                "points_allowed": 8720.0,
            }]
        }
        result, _ = _lookup("Boston Celtics", _Response(payload))
        assert result.available is False

    def test_one_bad_row_rejects_the_whole_snapshot(self):
        payload = {
            "teams": [
                {"team": "Boston Celtics", "possessions": 8000, "points": 9600, "points_allowed": 8720},
                {"team": "Miami Heat", "possessions": 8000},
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
            NBA_LIVE_RATINGS_MAX_BYTES=len(body) - 1,
        )
        assert result.available is False

    def test_body_exactly_at_cap_is_accepted(self):
        body = json.dumps(_SNAPSHOT).encode("utf-8")
        result, _ = _lookup(
            "Boston Celtics", _BytesResponse(body),
            NBA_LIVE_RATINGS_MAX_BYTES=len(body),
        )
        assert result.available is True

    def test_zero_byte_cap_reports_unavailable(self):
        result, _ = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT), NBA_LIVE_RATINGS_MAX_BYTES=0,
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
            NBA_LIVE_RATINGS_CACHE_TTL_HOURS="not-a-number",
        )
        assert result.available is False

    def test_unreadable_minimum_reports_unavailable(self):
        result, _ = _lookup(
            "Boston Celtics", _Response(_SNAPSHOT),
            NBA_LIVE_RATINGS_MIN_POSSESSIONS="lots",
        )
        assert result.available is False


class TestCaching:
    def setup_method(self):
        clear_live_ratings_cache()

    def test_second_lookup_reuses_snapshot(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nba_live_ratings_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_team_ratings("2024-25", "Boston Celtics")
            second = get_live_team_ratings("2024-25", "Miami Heat")

        assert first.available and second.available
        assert opener.call_count == 1

    def test_different_season_fetches_separately(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nba_live_ratings_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            get_live_team_ratings("2024-25", "Boston Celtics")
            get_live_team_ratings("2023-24", "Boston Celtics")

        assert opener.call_count == 2

    def test_failure_is_not_cached(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nba_live_ratings_service.urlopen",
                side_effect=[OSError("boom"), _Response(_SNAPSHOT)],
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_team_ratings("2024-25", "Boston Celtics")
            second = get_live_team_ratings("2024-25", "Boston Celtics")

        assert first.available is False
        assert second.available is True
        assert opener.call_count == 2

    def test_zero_ttl_refetches(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nba_live_ratings_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings(NBA_LIVE_RATINGS_CACHE_TTL_HOURS=0.0):
                stack.enter_context(ctx)
            get_live_team_ratings("2024-25", "Boston Celtics")
            get_live_team_ratings("2024-25", "Boston Celtics")

        assert opener.call_count == 2

    def test_cache_can_be_cleared(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nba_live_ratings_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            get_live_team_ratings("2024-25", "Boston Celtics")
            clear_live_ratings_cache()
            get_live_team_ratings("2024-25", "Boston Celtics")

        assert opener.call_count == 2


class TestCredentialHandling:
    def setup_method(self):
        clear_live_ratings_cache()

    def test_key_is_sent_only_as_bearer_header(self):
        _, opener = _lookup("Boston Celtics", _Response(_SNAPSHOT))
        request = opener.call_args.args[0]
        assert request.get_header("Authorization") == "Bearer secret-key"
        assert "secret-key" not in request.full_url

    def test_result_carries_no_credential(self):
        result, _ = _lookup("Boston Celtics", _Response(_SNAPSHOT))
        assert "secret-key" not in repr(result)
