"""Tests for the optional measured MLB park-factor provider (P1-M2)."""
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.mlb_live_park_service import (
    clear_live_park_cache,
    get_live_park_factor,
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
        "MLB_LIVE_PARK_ENABLED": True,
        "MLB_LIVE_PARK_URL": "https://mlb.example/parks",
        "MLB_LIVE_PARK_API_KEY": "secret-key",
        "MLB_LIVE_PARK_SEASON_PARAM": "season",
        "MLB_LIVE_PARK_TIMEOUT_S": 5.0,
        "MLB_LIVE_PARK_CACHE_TTL_HOURS": 12.0,
        "MLB_LIVE_PARK_MAX_BYTES": 262144,
        "MLB_LIVE_PARK_MIN_GAMES": 81.0,
    }
    values.update(overrides)
    return tuple(patch.object(settings, name, value) for name, value in values.items())


def _lookup(team, response=None, *, season="2026", side_effect=None, **overrides):
    """Look a park up with urlopen stubbed; returns (result, urlopen mock)."""
    opener = (
        patch("app.services.mlb_live_park_service.urlopen", side_effect=side_effect)
        if side_effect is not None
        else patch("app.services.mlb_live_park_service.urlopen", return_value=response)
    )
    with ExitStack() as stack:
        mock_open = stack.enter_context(opener)
        for ctx in _settings(**overrides):
            stack.enter_context(ctx)
        return get_live_park_factor(season, team), mock_open


# Colorado: 990/90 = 11.0 runs at home, 1000/100 = 10.0 on the road, so 1.10.
# The unequal game counts mean a raw runs ratio would read 0.99 instead.
# Miami's home sample is below the configured 81-game minimum.
_SNAPSHOT = {
    "parks": [
        {
            "team": "Colorado Rockies",
            "home_games": 90,
            "home_runs": 990,
            "road_games": 100,
            "road_runs": 1000,
        },
        {
            "team": "Miami Marlins",
            "home_games": 40,
            "home_runs": 340,
            "road_games": 100,
            "road_runs": 900,
        },
    ]
}


_OMIT = object()


def _one(**fields):
    """Single-park payload with the valid Colorado row's fields overridden."""
    row = {"team": "Colorado Rockies", "home_games": 90, "home_runs": 990,
           "road_games": 100, "road_runs": 1000}
    row.update(fields)
    return {"parks": [{k: v for k, v in row.items() if v is not _OMIT}]}


class TestConfigurationGate:
    def setup_method(self):
        clear_live_park_cache()

    def test_disabled_makes_no_request(self):
        result, opener = _lookup(
            "Colorado Rockies", _Response(_SNAPSHOT), MLB_LIVE_PARK_ENABLED=False,
        )
        assert result.available is False
        opener.assert_not_called()

    def test_missing_url_makes_no_request(self):
        result, opener = _lookup(
            "Colorado Rockies", _Response(_SNAPSHOT), MLB_LIVE_PARK_URL="",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_missing_api_key_makes_no_request(self):
        result, opener = _lookup(
            "Colorado Rockies", _Response(_SNAPSHOT), MLB_LIVE_PARK_API_KEY="   ",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_blank_season_param_makes_no_request(self):
        result, opener = _lookup(
            "Colorado Rockies", _Response(_SNAPSHOT), MLB_LIVE_PARK_SEASON_PARAM="",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_non_http_scheme_makes_no_request(self):
        result, opener = _lookup(
            "Colorado Rockies", _Response(_SNAPSHOT),
            MLB_LIVE_PARK_URL="file:///etc/passwd",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_blank_team_makes_no_request(self):
        result, opener = _lookup("   ", _Response(_SNAPSHOT))
        assert result.available is False
        opener.assert_not_called()

    @pytest.mark.parametrize("season", ["", None, "not-a-season", "202"])
    def test_unusable_season_makes_no_request(self, season):
        result, opener = _lookup("Colorado Rockies", _Response(_SNAPSHOT), season=season)
        assert result.available is False
        opener.assert_not_called()


class TestRequestUrl:
    def setup_method(self):
        clear_live_park_cache()

    def test_season_year_is_appended(self):
        _, opener = _lookup("Colorado Rockies", _Response(_SNAPSHOT))
        assert opener.call_args.args[0].full_url.endswith("?season=2026")

    def test_integer_season_is_accepted(self):
        # The adapter derives the season as an int, not a string.
        result, opener = _lookup("Colorado Rockies", _Response(_SNAPSHOT), season=2026)
        assert result.available is True
        assert opener.call_args.args[0].full_url.endswith("?season=2026")

    def test_existing_season_param_is_replaced(self):
        _, opener = _lookup(
            "Colorado Rockies", _Response(_SNAPSHOT),
            MLB_LIVE_PARK_URL="https://mlb.example/parks?season=1999&split=runs",
        )
        url = opener.call_args.args[0].full_url
        assert "split=runs" in url
        assert "season=2026" in url
        assert "season=1999" not in url

    def test_custom_season_param_name_is_used(self):
        _, opener = _lookup(
            "Colorado Rockies", _Response(_SNAPSHOT), MLB_LIVE_PARK_SEASON_PARAM="year",
        )
        assert "year=2026" in opener.call_args.args[0].full_url


class TestSnapshotReading:
    def setup_method(self):
        clear_live_park_cache()

    def test_factor_is_computed_from_per_game_run_rates(self):
        result, _ = _lookup("Colorado Rockies", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.park is not None
        # 11.0 home runs per game over 10.0 on the road. A raw runs ratio would
        # be 0.99, so this also proves the game counts are divided out.
        assert result.park["park_factor"] == pytest.approx(1.1)
        assert result.park["home_games"] == pytest.approx(90.0)
        assert result.park["road_games"] == pytest.approx(100.0)

    def test_precomputed_factor_without_counts_is_rejected(self):
        # The point of the row is that the factor is measured; a payload carrying
        # only park_factor cannot prove that, so it is not trusted.
        payload = {"parks": [{"team": "Colorado Rockies", "park_factor": 1.15}]}
        result, _ = _lookup("Colorado Rockies", _Response(payload))
        assert result.available is False

    def test_small_home_sample_is_available_but_unmeasured(self):
        result, _ = _lookup("Miami Marlins", _Response(_SNAPSHOT))
        assert result.available is True
        # Reached, but 40 home games is below the configured minimum.
        assert result.park is None

    def test_small_road_sample_is_also_unmeasured(self):
        # The road rate is the baseline, so a thin road sample is just as noisy.
        result, _ = _lookup(
            "Colorado Rockies", _Response(_one(road_games=30, road_runs=300)),
        )
        assert result.available is True
        assert result.park is None

    def test_lowering_the_minimum_admits_the_small_sample(self):
        result, _ = _lookup(
            "Miami Marlins", _Response(_SNAPSHOT), MLB_LIVE_PARK_MIN_GAMES=20.0,
        )
        assert result.park is not None
        # 340/40 = 8.5 at home, 900/100 = 9.0 on the road.
        assert result.park["park_factor"] == pytest.approx(0.9444, abs=1e-4)

    def test_sample_exactly_at_the_minimum_is_admitted(self):
        result, _ = _lookup(
            "Colorado Rockies",
            _Response(_one(home_games=81, home_runs=891, road_games=81, road_runs=810)),
        )
        assert result.park is not None
        assert result.park["park_factor"] == pytest.approx(1.1)

    def test_neutral_park_is_reported_as_one(self):
        result, _ = _lookup("Colorado Rockies", _Response(_one(home_runs=900)))
        assert result.park is not None
        assert result.park["park_factor"] == pytest.approx(1.0)

    def test_team_absent_from_snapshot_is_available_but_unmeasured(self):
        result, _ = _lookup("Boston Red Sox", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.park is None

    def test_team_name_matching_ignores_case_and_punctuation(self):
        result, _ = _lookup("  colorado   rockies!  ", _Response(_SNAPSHOT))
        assert result.park is not None
        assert result.park["park_factor"] == pytest.approx(1.1)

    def test_numeric_strings_are_accepted(self):
        result, _ = _lookup(
            "Colorado Rockies",
            _Response(_one(home_games="90", home_runs="990",
                           road_games="100", road_runs="1000")),
        )
        assert result.park is not None
        assert result.park["park_factor"] == pytest.approx(1.1)

    def test_returned_park_is_a_copy(self):
        with ExitStack() as stack:
            stack.enter_context(patch(
                "app.services.mlb_live_park_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_park_factor("2026", "Colorado Rockies")
            assert first.park is not None
            first.park["park_factor"] = 0.0
            # Served from cache: a caller mutating its copy must not poison it.
            second = get_live_park_factor("2026", "Colorado Rockies")

        assert second.park is not None
        assert second.park["park_factor"] == pytest.approx(1.1)


class TestFailClosed:
    def setup_method(self):
        clear_live_park_cache()

    @pytest.mark.parametrize("payload", [
        {"errors": ["quota exceeded"], "parks": []},
        {"parks": "not-a-list"},
        {"no_parks_key": True},
        ["not", "an", "object"],
        {"parks": ["not-an-object"]},
        _one(team=_OMIT),
        _one(team=""),
        _one(home_games=_OMIT),
        _one(road_games=_OMIT),
        _one(home_runs=_OMIT),
        _one(road_runs=_OMIT),
        _one(home_games=0),
        _one(road_games=0),
        _one(home_games=-90),
        _one(home_runs=-990),
        # Zero road runs is the denominator; no ratio exists.
        _one(road_runs=0),
        _one(home_games="a few"),
        _one(road_games=True),
        _one(home_runs=None),
        _one(road_runs="lots"),
    ])
    def test_malformed_payloads_report_unavailable(self, payload):
        result, _ = _lookup("Colorado Rockies", _Response(payload))
        assert result.available is False

    def test_duplicate_team_blocks_report_unavailable(self):
        payload = {
            "parks": [
                _one()["parks"][0],
                _one(team="colorado rockies", home_runs=900)["parks"][0],
            ]
        }
        result, _ = _lookup("Colorado Rockies", _Response(payload))
        assert result.available is False

    @pytest.mark.parametrize("home_runs", [600, 1400])
    def test_out_of_band_factor_reports_unavailable(self, home_runs):
        # 0.67 and 1.56 are not park run factors; the payload is not a
        # league-average-relative ratio, so the whole feed is rejected.
        result, _ = _lookup("Colorado Rockies", _Response(_one(home_runs=home_runs)))
        assert result.available is False

    def test_one_bad_row_rejects_the_whole_snapshot(self):
        payload = {
            "parks": [
                _one()["parks"][0],
                {"team": "Miami Marlins", "home_games": 90},
            ]
        }
        result, _ = _lookup("Colorado Rockies", _Response(payload))
        assert result.available is False

    def test_invalid_json_reports_unavailable(self):
        result, _ = _lookup("Colorado Rockies", _BytesResponse(b"{not json"))
        assert result.available is False

    def test_non_utf8_body_reports_unavailable(self):
        result, _ = _lookup("Colorado Rockies", _BytesResponse(b"\xff\xfe\x00"))
        assert result.available is False

    def test_oversize_body_reports_unavailable(self):
        body = json.dumps(_SNAPSHOT).encode("utf-8")
        result, _ = _lookup(
            "Colorado Rockies", _BytesResponse(body),
            MLB_LIVE_PARK_MAX_BYTES=len(body) - 1,
        )
        assert result.available is False

    def test_body_exactly_at_cap_is_accepted(self):
        body = json.dumps(_SNAPSHOT).encode("utf-8")
        result, _ = _lookup(
            "Colorado Rockies", _BytesResponse(body), MLB_LIVE_PARK_MAX_BYTES=len(body),
        )
        assert result.available is True

    def test_zero_byte_cap_reports_unavailable(self):
        result, _ = _lookup(
            "Colorado Rockies", _Response(_SNAPSHOT), MLB_LIVE_PARK_MAX_BYTES=0,
        )
        assert result.available is False

    def test_transport_error_reports_unavailable(self):
        result, _ = _lookup("Colorado Rockies", None, side_effect=OSError("boom"))
        assert result.available is False

    def test_timeout_reports_unavailable(self):
        result, _ = _lookup("Colorado Rockies", None, side_effect=TimeoutError("slow"))
        assert result.available is False

    def test_unreadable_ttl_reports_unavailable(self):
        result, _ = _lookup(
            "Colorado Rockies", _Response(_SNAPSHOT),
            MLB_LIVE_PARK_CACHE_TTL_HOURS="not-a-number",
        )
        assert result.available is False

    def test_unreadable_minimum_reports_unavailable(self):
        result, _ = _lookup(
            "Colorado Rockies", _Response(_SNAPSHOT), MLB_LIVE_PARK_MIN_GAMES="many",
        )
        assert result.available is False


class TestCaching:
    def setup_method(self):
        clear_live_park_cache()

    def test_second_lookup_reuses_snapshot(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.mlb_live_park_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_park_factor("2026", "Colorado Rockies")
            second = get_live_park_factor("2026", "Miami Marlins")

        assert first.available and second.available
        assert opener.call_count == 1

    def test_different_season_fetches_separately(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.mlb_live_park_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            get_live_park_factor("2026", "Colorado Rockies")
            get_live_park_factor("2025", "Colorado Rockies")

        assert opener.call_count == 2

    def test_failure_is_not_cached(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.mlb_live_park_service.urlopen",
                side_effect=[OSError("boom"), _Response(_SNAPSHOT)],
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_park_factor("2026", "Colorado Rockies")
            second = get_live_park_factor("2026", "Colorado Rockies")

        assert first.available is False
        assert second.available is True
        assert opener.call_count == 2

    def test_zero_ttl_refetches(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.mlb_live_park_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings(MLB_LIVE_PARK_CACHE_TTL_HOURS=0.0):
                stack.enter_context(ctx)
            get_live_park_factor("2026", "Colorado Rockies")
            get_live_park_factor("2026", "Colorado Rockies")

        assert opener.call_count == 2

    def test_cache_can_be_cleared(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.mlb_live_park_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            get_live_park_factor("2026", "Colorado Rockies")
            clear_live_park_cache()
            get_live_park_factor("2026", "Colorado Rockies")

        assert opener.call_count == 2


class TestCredentialHandling:
    def setup_method(self):
        clear_live_park_cache()

    def test_key_is_sent_only_as_bearer_header(self):
        _, opener = _lookup("Colorado Rockies", _Response(_SNAPSHOT))
        request = opener.call_args.args[0]
        assert request.get_header("Authorization") == "Bearer secret-key"
        assert "secret-key" not in request.full_url

    def test_result_carries_no_credential(self):
        result, _ = _lookup("Colorado Rockies", _Response(_SNAPSHOT))
        assert "secret-key" not in repr(result)
