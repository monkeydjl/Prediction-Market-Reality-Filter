"""Tests for the optional NHL true 5v5 shot-quality provider (P1-H1)."""
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.nhl_live_xg_service import (
    clear_live_5v5_cache,
    get_live_5v5_metrics,
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
        "NHL_LIVE_XG_ENABLED": True,
        "NHL_LIVE_XG_URL": "https://nhl.example/5v5",
        "NHL_LIVE_XG_API_KEY": "secret-key",
        "NHL_LIVE_XG_SEASON_PARAM": "season",
        "NHL_LIVE_XG_TIMEOUT_S": 5.0,
        "NHL_LIVE_XG_CACHE_TTL_HOURS": 6.0,
        "NHL_LIVE_XG_MAX_BYTES": 262144,
        "NHL_LIVE_XG_MIN_TOI_MINUTES": 500.0,
    }
    values.update(overrides)
    return tuple(patch.object(settings, name, value) for name, value in values.items())


def _lookup(team, response=None, *, season="20262027", side_effect=None, **overrides):
    """Look a team up with urlopen stubbed; returns (result, urlopen mock)."""
    opener = (
        patch("app.services.nhl_live_xg_service.urlopen", side_effect=side_effect)
        if side_effect is not None
        else patch("app.services.nhl_live_xg_service.urlopen", return_value=response)
    )
    with ExitStack() as stack:
        mock_open = stack.enter_context(opener)
        for ctx in _settings(**overrides):
            stack.enter_context(ctx)
        return get_live_5v5_metrics(season, team), mock_open


# Boston: 60 x 40 / 1000 minutes = 2.4 xGF/60, 1100 / (1100+900) = 0.55 CF%.
# Montreal's 5v5 sample is below the configured 500-minute minimum.
_SNAPSHOT = {
    "teams": [
        {
            "team": "Boston Bruins",
            "toi_minutes": 1000.0,
            "xgf": 40.0,
            "cf": 1100.0,
            "ca": 900.0,
        },
        {
            "team": "Montreal Canadiens",
            "toi_minutes": 300.0,
            "xgf": 12.0,
            "cf": 300.0,
            "ca": 320.0,
        },
    ]
}


_OMIT = object()


def _one(**fields):
    """Single-team payload with the valid Boston row's fields overridden."""
    row = {"team": "Boston Bruins", "toi_minutes": 1000.0, "xgf": 40.0,
           "cf": 1100.0, "ca": 900.0}
    row.update(fields)
    return {"teams": [{k: v for k, v in row.items() if v is not _OMIT}]}


class TestConfigurationGate:
    def setup_method(self):
        clear_live_5v5_cache()

    def test_disabled_makes_no_request(self):
        result, opener = _lookup(
            "Boston Bruins", _Response(_SNAPSHOT), NHL_LIVE_XG_ENABLED=False,
        )
        assert result.available is False
        opener.assert_not_called()

    def test_missing_url_makes_no_request(self):
        result, opener = _lookup(
            "Boston Bruins", _Response(_SNAPSHOT), NHL_LIVE_XG_URL="",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_missing_api_key_makes_no_request(self):
        result, opener = _lookup(
            "Boston Bruins", _Response(_SNAPSHOT), NHL_LIVE_XG_API_KEY="   ",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_blank_season_param_makes_no_request(self):
        result, opener = _lookup(
            "Boston Bruins", _Response(_SNAPSHOT), NHL_LIVE_XG_SEASON_PARAM="",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_non_http_scheme_makes_no_request(self):
        result, opener = _lookup(
            "Boston Bruins", _Response(_SNAPSHOT),
            NHL_LIVE_XG_URL="file:///etc/passwd",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_blank_team_makes_no_request(self):
        result, opener = _lookup("   ", _Response(_SNAPSHOT))
        assert result.available is False
        opener.assert_not_called()

    @pytest.mark.parametrize("season", ["", None, "not-a-season", "202"])
    def test_unusable_season_makes_no_request(self, season):
        result, opener = _lookup("Boston Bruins", _Response(_SNAPSHOT), season=season)
        assert result.available is False
        opener.assert_not_called()


class TestRequestUrl:
    def setup_method(self):
        clear_live_5v5_cache()

    def test_season_start_year_is_appended(self):
        _, opener = _lookup("Boston Bruins", _Response(_SNAPSHOT))
        assert opener.call_args.args[0].full_url.endswith("?season=2026")

    def test_existing_season_param_is_replaced(self):
        _, opener = _lookup(
            "Boston Bruins", _Response(_SNAPSHOT),
            NHL_LIVE_XG_URL="https://nhl.example/5v5?season=1999&strength=5v5",
        )
        url = opener.call_args.args[0].full_url
        assert "strength=5v5" in url
        assert "season=2026" in url
        assert "season=1999" not in url

    def test_custom_season_param_name_is_used(self):
        _, opener = _lookup(
            "Boston Bruins", _Response(_SNAPSHOT), NHL_LIVE_XG_SEASON_PARAM="year",
        )
        assert "year=2026" in opener.call_args.args[0].full_url


class TestSnapshotReading:
    def setup_method(self):
        clear_live_5v5_cache()

    def test_metrics_are_computed_from_toi_and_counts(self):
        result, _ = _lookup("Boston Bruins", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.metrics is not None
        assert result.metrics["xgf_per_60"] == pytest.approx(2.4)
        assert result.metrics["corsi_pct"] == pytest.approx(0.55)
        assert result.metrics["toi_minutes"] == pytest.approx(1000.0)

    def test_precomputed_rates_without_inputs_are_rejected(self):
        # The point of the row is that the rate is measured; a payload carrying
        # only xgf_per_60/corsi_pct cannot prove that, so it is not trusted.
        payload = {
            "teams": [{
                "team": "Boston Bruins",
                "toi_minutes": 1000.0,
                "xgf_per_60": 2.4,
                "corsi_pct": 0.55,
            }]
        }
        result, _ = _lookup("Boston Bruins", _Response(payload))
        assert result.available is False

    def test_expected_goals_only_row_omits_corsi(self):
        result, _ = _lookup("Boston Bruins", _Response(_one(cf=_OMIT, ca=_OMIT)))
        assert result.metrics is not None
        assert result.metrics["xgf_per_60"] == pytest.approx(2.4)
        assert "corsi_pct" not in result.metrics

    def test_corsi_only_row_omits_expected_goals(self):
        result, _ = _lookup("Boston Bruins", _Response(_one(xgf=_OMIT)))
        assert result.metrics is not None
        assert result.metrics["corsi_pct"] == pytest.approx(0.55)
        assert "xgf_per_60" not in result.metrics

    def test_small_sample_is_available_but_unmeasured(self):
        result, _ = _lookup("Montreal Canadiens", _Response(_SNAPSHOT))
        assert result.available is True
        # Reached, but 300 minutes of 5v5 is below the configured minimum.
        assert result.metrics is None

    def test_lowering_the_minimum_admits_the_small_sample(self):
        result, _ = _lookup(
            "Montreal Canadiens", _Response(_SNAPSHOT),
            NHL_LIVE_XG_MIN_TOI_MINUTES=100.0,
        )
        assert result.metrics is not None
        assert result.metrics["xgf_per_60"] == pytest.approx(2.4)

    def test_sample_exactly_at_the_minimum_is_admitted(self):
        result, _ = _lookup(
            "Boston Bruins", _Response(_one(toi_minutes=500.0, xgf=20.0)),
        )
        assert result.metrics is not None
        assert result.metrics["xgf_per_60"] == pytest.approx(2.4)

    def test_team_absent_from_snapshot_is_available_but_unmeasured(self):
        result, _ = _lookup("Toronto Maple Leafs", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.metrics is None

    def test_team_name_matching_ignores_case_and_punctuation(self):
        result, _ = _lookup("  boston   bruins!  ", _Response(_SNAPSHOT))
        assert result.metrics is not None
        assert result.metrics["corsi_pct"] == pytest.approx(0.55)

    def test_numeric_strings_are_accepted(self):
        result, _ = _lookup(
            "Boston Bruins",
            _Response(_one(toi_minutes="1000", xgf="40", cf="1100", ca="900")),
        )
        assert result.metrics is not None
        assert result.metrics["xgf_per_60"] == pytest.approx(2.4)
        assert result.metrics["corsi_pct"] == pytest.approx(0.55)

    def test_returned_metrics_are_a_copy(self):
        with ExitStack() as stack:
            stack.enter_context(patch(
                "app.services.nhl_live_xg_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_5v5_metrics("20262027", "Boston Bruins")
            assert first.metrics is not None
            first.metrics["xgf_per_60"] = 0.0
            # Served from cache: a caller mutating its copy must not poison it.
            second = get_live_5v5_metrics("20262027", "Boston Bruins")

        assert second.metrics is not None
        assert second.metrics["xgf_per_60"] == pytest.approx(2.4)


class TestFailClosed:
    def setup_method(self):
        clear_live_5v5_cache()

    @pytest.mark.parametrize("payload", [
        {"errors": ["quota exceeded"], "teams": []},
        {"teams": "not-a-list"},
        {"no_teams_key": True},
        ["not", "an", "object"],
        {"teams": ["not-an-object"]},
        _one(team=_OMIT),
        _one(team=""),
        _one(toi_minutes=_OMIT),
        _one(toi_minutes=0),
        _one(toi_minutes=-1000.0),
        _one(toi_minutes="a while"),
        _one(toi_minutes=True),
        _one(toi_minutes=None),
        # A row with a team and ice time but no measurement in it at all.
        _one(xgf=_OMIT, cf=_OMIT, ca=_OMIT),
        # Half-supplied corsi pair: a share cannot be derived from one side.
        _one(xgf=_OMIT, ca=_OMIT),
        _one(xgf=_OMIT, cf=_OMIT),
        _one(xgf=-1.0),
        _one(xgf="lots"),
        _one(xgf=None),
        _one(cf="many"),
        _one(ca=True),
        _one(cf=0, ca=0),
        _one(ca=-900.0),
    ])
    def test_malformed_payloads_report_unavailable(self, payload):
        result, _ = _lookup("Boston Bruins", _Response(payload))
        assert result.available is False

    def test_duplicate_team_blocks_report_unavailable(self):
        payload = {
            "teams": [
                _one()["teams"][0],
                _one(team="boston bruins", xgf=35.0)["teams"][0],
            ]
        }
        result, _ = _lookup("Boston Bruins", _Response(payload))
        assert result.available is False

    @pytest.mark.parametrize("xgf", [10.0, 100.0])
    def test_out_of_band_expected_goals_reports_unavailable(self, xgf):
        # 0.6 and 6.0 xG per 60 are not NHL 5v5 rates; the payload is not in
        # xG-per-60 at all, so the whole feed is rejected.
        result, _ = _lookup("Boston Bruins", _Response(_one(xgf=xgf)))
        assert result.available is False

    @pytest.mark.parametrize(("cf", "ca"), [(1500.0, 500.0), (500.0, 1500.0)])
    def test_out_of_band_corsi_reports_unavailable(self, cf, ca):
        # 75% and 25% of shot attempts are not NHL 5v5 shares; the feed is
        # counting something other than attempts for and against.
        result, _ = _lookup("Boston Bruins", _Response(_one(cf=cf, ca=ca)))
        assert result.available is False

    def test_one_bad_row_rejects_the_whole_snapshot(self):
        payload = {
            "teams": [
                _one()["teams"][0],
                {"team": "Montreal Canadiens", "toi_minutes": 1000.0},
            ]
        }
        result, _ = _lookup("Boston Bruins", _Response(payload))
        assert result.available is False

    def test_invalid_json_reports_unavailable(self):
        result, _ = _lookup("Boston Bruins", _BytesResponse(b"{not json"))
        assert result.available is False

    def test_non_utf8_body_reports_unavailable(self):
        result, _ = _lookup("Boston Bruins", _BytesResponse(b"\xff\xfe\x00"))
        assert result.available is False

    def test_oversize_body_reports_unavailable(self):
        body = json.dumps(_SNAPSHOT).encode("utf-8")
        result, _ = _lookup(
            "Boston Bruins", _BytesResponse(body),
            NHL_LIVE_XG_MAX_BYTES=len(body) - 1,
        )
        assert result.available is False

    def test_body_exactly_at_cap_is_accepted(self):
        body = json.dumps(_SNAPSHOT).encode("utf-8")
        result, _ = _lookup(
            "Boston Bruins", _BytesResponse(body), NHL_LIVE_XG_MAX_BYTES=len(body),
        )
        assert result.available is True

    def test_zero_byte_cap_reports_unavailable(self):
        result, _ = _lookup(
            "Boston Bruins", _Response(_SNAPSHOT), NHL_LIVE_XG_MAX_BYTES=0,
        )
        assert result.available is False

    def test_transport_error_reports_unavailable(self):
        result, _ = _lookup("Boston Bruins", None, side_effect=OSError("boom"))
        assert result.available is False

    def test_timeout_reports_unavailable(self):
        result, _ = _lookup("Boston Bruins", None, side_effect=TimeoutError("slow"))
        assert result.available is False

    def test_unreadable_ttl_reports_unavailable(self):
        result, _ = _lookup(
            "Boston Bruins", _Response(_SNAPSHOT),
            NHL_LIVE_XG_CACHE_TTL_HOURS="not-a-number",
        )
        assert result.available is False

    def test_unreadable_minimum_reports_unavailable(self):
        result, _ = _lookup(
            "Boston Bruins", _Response(_SNAPSHOT),
            NHL_LIVE_XG_MIN_TOI_MINUTES="lots",
        )
        assert result.available is False


class TestCaching:
    def setup_method(self):
        clear_live_5v5_cache()

    def test_second_lookup_reuses_snapshot(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nhl_live_xg_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_5v5_metrics("20262027", "Boston Bruins")
            second = get_live_5v5_metrics("20262027", "Montreal Canadiens")

        assert first.available and second.available
        assert opener.call_count == 1

    def test_different_season_fetches_separately(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nhl_live_xg_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            get_live_5v5_metrics("20262027", "Boston Bruins")
            get_live_5v5_metrics("20252026", "Boston Bruins")

        assert opener.call_count == 2

    def test_failure_is_not_cached(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nhl_live_xg_service.urlopen",
                side_effect=[OSError("boom"), _Response(_SNAPSHOT)],
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_live_5v5_metrics("20262027", "Boston Bruins")
            second = get_live_5v5_metrics("20262027", "Boston Bruins")

        assert first.available is False
        assert second.available is True
        assert opener.call_count == 2

    def test_zero_ttl_refetches(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nhl_live_xg_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings(NHL_LIVE_XG_CACHE_TTL_HOURS=0.0):
                stack.enter_context(ctx)
            get_live_5v5_metrics("20262027", "Boston Bruins")
            get_live_5v5_metrics("20262027", "Boston Bruins")

        assert opener.call_count == 2

    def test_cache_can_be_cleared(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.nhl_live_xg_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            get_live_5v5_metrics("20262027", "Boston Bruins")
            clear_live_5v5_cache()
            get_live_5v5_metrics("20262027", "Boston Bruins")

        assert opener.call_count == 2


class TestCredentialHandling:
    def setup_method(self):
        clear_live_5v5_cache()

    def test_key_is_sent_only_as_bearer_header(self):
        _, opener = _lookup("Boston Bruins", _Response(_SNAPSHOT))
        request = opener.call_args.args[0]
        assert request.get_header("Authorization") == "Bearer secret-key"
        assert "secret-key" not in request.full_url

    def test_result_carries_no_credential(self):
        result, _ = _lookup("Boston Bruins", _Response(_SNAPSHOT))
        assert "secret-key" not in repr(result)
