"""Tests for the optional real market over/under totals provider (P1-O1)."""
import json
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.market_totals_service import (
    clear_market_totals_cache,
    get_market_total,
    inject_market_total_into_custom,
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
        "MARKET_TOTALS_ENABLED": True,
        "MARKET_TOTALS_URL": "https://books.example/totals",
        "MARKET_TOTALS_API_KEY": "secret-key",
        "MARKET_TOTALS_SPORT_PARAM": "sport",
        "MARKET_TOTALS_DATE_PARAM": "date",
        "MARKET_TOTALS_TIMEOUT_S": 5.0,
        "MARKET_TOTALS_CACHE_TTL_MINUTES": 15.0,
        "MARKET_TOTALS_MAX_BYTES": 262144,
        "MARKET_TOTALS_MAX_PRICE_SKEW": 0.15,
        # Pinned so the unit band is deterministic regardless of deployment.
        "NBA_LEAGUE_AVG_TOTAL": 220.0,
        "MLB_LEAGUE_AVG_TOTAL": 8.5,
        "NHL_LEAGUE_AVG_TOTAL": 5.5,
    }
    values.update(overrides)
    return tuple(patch.object(settings, name, value) for name, value in values.items())


_DATE = "2026-08-19"


def _lookup(
    home="Boston Celtics",
    away="Miami Heat",
    response=None,
    *,
    sport="basketball",
    match_date=_DATE,
    side_effect=None,
    **overrides,
):
    """Look one fixture up with urlopen stubbed; returns (result, urlopen mock)."""
    opener = (
        patch("app.services.market_totals_service.urlopen", side_effect=side_effect)
        if side_effect is not None
        else patch("app.services.market_totals_service.urlopen", return_value=response)
    )
    with ExitStack() as stack:
        mock_open = stack.enter_context(opener)
        for ctx in _settings(**overrides):
            stack.enter_context(ctx)
        return get_market_total(sport, match_date, home, away), mock_open


# 1/1.91 and 1/1.95 de-vig to an over probability just above even, which is what
# a genuinely balanced posted total looks like. The Denver row is well formed but
# explicitly unpriced — a real fixture whose market is suspended or not yet open.
_SNAPSHOT = {
    "games": [
        {
            "home": "Boston Celtics",
            "away": "Miami Heat",
            "total_line": 228.5,
            "over_odds": 1.91,
            "under_odds": 1.95,
        },
        {
            "home": "Denver Nuggets",
            "away": "Phoenix Suns",
            "total_line": 231.0,
            "over_odds": None,
            "under_odds": None,
        },
    ]
}

_OMIT = object()


def _one(**fields):
    """Single-game payload with the valid Boston row's fields overridden."""
    row = {"home": "Boston Celtics", "away": "Miami Heat", "total_line": 228.5,
           "over_odds": 1.91, "under_odds": 1.95}
    row.update(fields)
    return {"games": [{k: v for k, v in row.items() if v is not _OMIT}]}


class TestConfigurationGate:
    def setup_method(self):
        clear_market_totals_cache()

    def test_disabled_makes_no_request(self):
        result, opener = _lookup(
            response=_Response(_SNAPSHOT), MARKET_TOTALS_ENABLED=False,
        )
        assert result.available is False
        opener.assert_not_called()

    def test_missing_url_makes_no_request(self):
        result, opener = _lookup(response=_Response(_SNAPSHOT), MARKET_TOTALS_URL="")
        assert result.available is False
        opener.assert_not_called()

    def test_missing_api_key_makes_no_request(self):
        result, opener = _lookup(
            response=_Response(_SNAPSHOT), MARKET_TOTALS_API_KEY="   ",
        )
        assert result.available is False
        opener.assert_not_called()

    @pytest.mark.parametrize("param", ["MARKET_TOTALS_SPORT_PARAM",
                                       "MARKET_TOTALS_DATE_PARAM"])
    def test_blank_query_param_name_makes_no_request(self, param):
        result, opener = _lookup(response=_Response(_SNAPSHOT), **{param: ""})
        assert result.available is False
        opener.assert_not_called()

    def test_identical_param_names_make_no_request(self):
        # One query parameter cannot carry both the sport and the date; whichever
        # was written last would silently win.
        result, opener = _lookup(
            response=_Response(_SNAPSHOT),
            MARKET_TOTALS_SPORT_PARAM="q",
            MARKET_TOTALS_DATE_PARAM="q",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_non_http_scheme_makes_no_request(self):
        result, opener = _lookup(
            response=_Response(_SNAPSHOT), MARKET_TOTALS_URL="file:///etc/passwd",
        )
        assert result.available is False
        opener.assert_not_called()

    def test_unknown_sport_makes_no_request(self):
        # Without a baseline there is nothing to band the line against, so the
        # line cannot be checked for units and is not trusted.
        result, opener = _lookup(response=_Response(_SNAPSHOT), sport="cricket")
        assert result.available is False
        opener.assert_not_called()

    @pytest.mark.parametrize("match_date", [
        "", "   ", None,
        # A provider handed a timestamp or a partial date may ignore it and
        # return another day's board, which would look valid but quote the wrong
        # fixtures. The shape is required exactly.
        "2026-08-19T23:30:00+00:00", "2026-08-19 23:30", "20260819",
        "19/08/2026", "not-a-date", "2026-02-30",
    ])
    def test_unusable_date_makes_no_request(self, match_date):
        result, opener = _lookup(response=_Response(_SNAPSHOT), match_date=match_date)
        assert result.available is False
        opener.assert_not_called()

    @pytest.mark.parametrize("home,away", [
        ("", "Miami Heat"),
        ("Boston Celtics", "  "),
        ("Boston Celtics", "boston  celtics!"),
    ])
    def test_unusable_team_pair_makes_no_request(self, home, away):
        result, opener = _lookup(home, away, _Response(_SNAPSHOT))
        assert result.available is False
        opener.assert_not_called()


class TestRequestUrl:
    def setup_method(self):
        clear_market_totals_cache()

    def test_sport_and_date_are_appended(self):
        _, opener = _lookup(response=_Response(_SNAPSHOT))
        url = opener.call_args.args[0].full_url
        assert "sport=basketball" in url
        assert f"date={_DATE}" in url

    def test_sport_is_lowercased(self):
        _, opener = _lookup(response=_Response(_SNAPSHOT), sport="BasketBall")
        assert "sport=basketball" in opener.call_args.args[0].full_url

    @pytest.mark.parametrize("match_date", ["2026-8-19", "  2026-08-19  "])
    def test_date_is_sent_canonically(self, match_date):
        # Unambiguous but non-canonical input is normalized rather than rejected,
        # and rather than forwarded as-is for the provider to interpret.
        _, opener = _lookup(response=_Response(_SNAPSHOT), match_date=match_date)
        assert f"date={_DATE}" in opener.call_args.args[0].full_url

    def test_existing_params_are_replaced_and_others_kept(self):
        _, opener = _lookup(
            response=_Response(_SNAPSHOT),
            MARKET_TOTALS_URL=(
                "https://books.example/totals?sport=nfl&date=1999-01-01&market=totals"
            ),
        )
        url = opener.call_args.args[0].full_url
        assert "market=totals" in url
        assert "sport=basketball" in url
        assert "sport=nfl" not in url
        assert "date=1999-01-01" not in url

    def test_custom_param_names_are_used(self):
        _, opener = _lookup(
            response=_Response(_SNAPSHOT),
            MARKET_TOTALS_SPORT_PARAM="league",
            MARKET_TOTALS_DATE_PARAM="on",
        )
        url = opener.call_args.args[0].full_url
        assert "league=basketball" in url
        assert f"on={_DATE}" in url


class TestSnapshotReading:
    def setup_method(self):
        clear_market_totals_cache()

    def test_line_and_devigged_over_probability_are_returned(self):
        result, _ = _lookup(response=_Response(_SNAPSHOT))
        assert result.available is True
        assert result.total is not None
        assert result.total["total_line"] == pytest.approx(228.5)
        # 1/1.91 / (1/1.91 + 1/1.95) — the vig is removed, not passed through.
        assert result.total["market_p_over"] == pytest.approx(0.5052, abs=1e-4)

    def test_devigged_probability_is_not_forced_to_even(self):
        # Within the skew tolerance the book's own lean is preserved, otherwise
        # the value would carry no information beyond "a line exists".
        result, _ = _lookup(
            response=_Response(_one(over_odds=1.75, under_odds=2.20)),
        )
        assert result.total is not None
        assert result.total["market_p_over"] == pytest.approx(0.557, abs=1e-4)

    def test_team_name_matching_ignores_case_and_punctuation(self):
        result, _ = _lookup("  boston   celtics!  ", "MIAMI HEAT", _Response(_SNAPSHOT))
        assert result.total is not None
        assert result.total["total_line"] == pytest.approx(228.5)

    def test_swapped_home_and_away_is_a_different_fixture(self):
        # Orientation is part of the fixture identity; a reversed pair is not
        # assumed to be the same listing.
        result, _ = _lookup("Miami Heat", "Boston Celtics", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.total is None

    def test_fixture_absent_from_snapshot_is_available_without_a_total(self):
        result, _ = _lookup("Chicago Bulls", "New York Knicks", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.total is None

    def test_explicitly_unpriced_market_is_available_without_a_total(self):
        # Both prices null is a market that exists but is not priced. That is a
        # per-fixture gap, not a broken contract, so other rows stay usable.
        result, _ = _lookup("Denver Nuggets", "Phoenix Suns", _Response(_SNAPSHOT))
        assert result.available is True
        assert result.total is None

    def test_an_unpriced_row_does_not_invalidate_its_neighbours(self):
        result, _ = _lookup(response=_Response(_SNAPSHOT))
        assert result.total is not None

    def test_numeric_strings_are_accepted(self):
        result, _ = _lookup(
            response=_Response(
                _one(total_line="228.5", over_odds="1.91", under_odds="1.95"),
            ),
        )
        assert result.total is not None
        assert result.total["total_line"] == pytest.approx(228.5)

    @pytest.mark.parametrize("sport,line", [
        ("baseball", 9.5),
        ("hockey", 6.5),
        ("football", 3.5),
        ("soccer", 2.5),
    ])
    def test_other_sports_use_their_own_baseline_band(self, sport, line):
        result, _ = _lookup(
            response=_Response(_one(total_line=line)), sport=sport,
        )
        assert result.total is not None
        assert result.total["total_line"] == pytest.approx(line)

    def test_returned_total_is_a_copy(self):
        with ExitStack() as stack:
            stack.enter_context(patch(
                "app.services.market_totals_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            first = get_market_total("basketball", _DATE, "Boston Celtics", "Miami Heat")
            assert first.total is not None
            first.total["total_line"] = 0.0
            # Served from cache: a caller mutating its copy must not poison it.
            second = get_market_total(
                "basketball", _DATE, "Boston Celtics", "Miami Heat",
            )

        assert second.total is not None
        assert second.total["total_line"] == pytest.approx(228.5)


class TestPriceIntegrity:
    """The line alone is a number; the two-sided quote is what makes it a market."""

    def setup_method(self):
        clear_market_totals_cache()

    @pytest.mark.parametrize("payload", [
        # A line with no prices behind it cannot be verified as a posted level.
        _one(over_odds=_OMIT, under_odds=_OMIT),
        _one(over_odds=_OMIT),
        _one(under_odds=_OMIT),
        # Half-supplied is a broken contract, unlike both-null.
        _one(over_odds=None),
        _one(under_odds=None),
        _one(total_line=_OMIT),
        _one(total_line=None),
    ])
    def test_incomplete_quote_rejects_the_snapshot(self, payload):
        result, _ = _lookup(response=_Response(payload))
        assert result.available is False

    @pytest.mark.parametrize("odds", [1.0, 0.5, 0.0, -1.91])
    def test_decimal_odds_at_or_below_one_are_rejected(self, odds):
        result, _ = _lookup(response=_Response(_one(over_odds=odds)))
        assert result.available is False

    def test_zero_overround_is_rejected(self):
        # A perfectly fair pair is not a book's prices.
        result, _ = _lookup(
            response=_Response(_one(over_odds=2.0, under_odds=2.0)),
        )
        assert result.available is False

    def test_implausible_overround_is_rejected(self):
        # 1/1.5 + 1/1.5 = 1.33 — beyond any real totals market.
        result, _ = _lookup(
            response=_Response(_one(over_odds=1.5, under_odds=1.5)),
        )
        assert result.available is False

    def test_wide_but_plausible_overround_is_accepted(self):
        # 1/1.55 + 1/1.55 = 1.29, just inside the ceiling.
        result, _ = _lookup(
            response=_Response(_one(over_odds=1.55, under_odds=1.55)),
        )
        assert result.total is not None
        assert result.total["market_p_over"] == pytest.approx(0.5)

    def test_heavily_skewed_price_is_rejected(self):
        # De-vigs to p_over 0.68. A book's posted total is the level it balanced,
        # so a two-to-one lean means this number is not that level.
        result, _ = _lookup(
            response=_Response(_one(over_odds=1.40, under_odds=3.00)),
        )
        assert result.available is False

    def test_raising_the_tolerance_admits_the_skewed_price(self):
        result, _ = _lookup(
            response=_Response(_one(over_odds=1.40, under_odds=3.00)),
            MARKET_TOTALS_MAX_PRICE_SKEW=0.20,
        )
        assert result.total is not None
        assert result.total["market_p_over"] == pytest.approx(0.6818, abs=1e-4)

    @pytest.mark.parametrize("sport,line", [
        # A basketball total in football units, and vice versa.
        ("basketball", 5.5),
        ("basketball", 500.0),
        ("football", 220.0),
        ("baseball", 228.5),
        ("hockey", 1.5),
    ])
    def test_line_outside_the_sport_band_is_rejected(self, sport, line):
        result, _ = _lookup(response=_Response(_one(total_line=line)), sport=sport)
        assert result.available is False

    @pytest.mark.parametrize("line", [110.0, 440.0])
    def test_line_on_the_band_edge_is_accepted(self, line):
        # Half and double the 220.0 baseline are wide enough for any real line.
        result, _ = _lookup(response=_Response(_one(total_line=line)))
        assert result.total is not None


class TestFailClosed:
    def setup_method(self):
        clear_market_totals_cache()

    @pytest.mark.parametrize("payload", [
        {"errors": ["quota exceeded"], "games": []},
        {"games": "not-a-list"},
        {"no_games_key": True},
        ["not", "an", "object"],
        {"games": ["not-an-object"]},
        _one(home=_OMIT),
        _one(away=_OMIT),
        _one(home=""),
        _one(away="   "),
        # A team cannot host itself; the row is unidentifiable.
        _one(away="Boston Celtics"),
        _one(total_line="a lot"),
        _one(total_line=True),
        _one(over_odds="evens"),
        _one(under_odds=True),
        _one(total_line=float("inf")),
        _one(over_odds=float("nan")),
    ])
    def test_malformed_payloads_report_unavailable(self, payload):
        result, _ = _lookup(response=_Response(payload))
        assert result.available is False

    def test_duplicate_fixture_reports_unavailable(self):
        payload = {"games": [
            _one()["games"][0],
            _one(home="boston celtics", total_line=231.0)["games"][0],
        ]}
        result, _ = _lookup(response=_Response(payload))
        assert result.available is False

    def test_one_bad_row_rejects_the_whole_snapshot(self):
        payload = {"games": [
            _one()["games"][0],
            {"home": "Denver Nuggets", "away": "Phoenix Suns", "total_line": 231.0},
        ]}
        result, _ = _lookup(response=_Response(payload))
        assert result.available is False

    def test_invalid_json_reports_unavailable(self):
        result, _ = _lookup(response=_BytesResponse(b"{not json"))
        assert result.available is False

    def test_non_utf8_body_reports_unavailable(self):
        result, _ = _lookup(response=_BytesResponse(b"\xff\xfe\x00"))
        assert result.available is False

    def test_oversize_body_reports_unavailable(self):
        body = json.dumps(_SNAPSHOT).encode("utf-8")
        result, _ = _lookup(
            response=_BytesResponse(body), MARKET_TOTALS_MAX_BYTES=len(body) - 1,
        )
        assert result.available is False

    def test_body_exactly_at_cap_is_accepted(self):
        body = json.dumps(_SNAPSHOT).encode("utf-8")
        result, _ = _lookup(
            response=_BytesResponse(body), MARKET_TOTALS_MAX_BYTES=len(body),
        )
        assert result.available is True

    def test_zero_byte_cap_reports_unavailable(self):
        result, _ = _lookup(response=_Response(_SNAPSHOT), MARKET_TOTALS_MAX_BYTES=0)
        assert result.available is False

    def test_transport_error_reports_unavailable(self):
        result, _ = _lookup(side_effect=OSError("boom"))
        assert result.available is False

    def test_timeout_reports_unavailable(self):
        result, _ = _lookup(side_effect=TimeoutError("slow"))
        assert result.available is False

    @pytest.mark.parametrize("name", [
        "MARKET_TOTALS_CACHE_TTL_MINUTES",
        "MARKET_TOTALS_TIMEOUT_S",
        "MARKET_TOTALS_MAX_BYTES",
        "MARKET_TOTALS_MAX_PRICE_SKEW",
    ])
    def test_unreadable_setting_reports_unavailable(self, name):
        result, _ = _lookup(
            response=_Response(_SNAPSHOT), **{name: "not-a-number"},
        )
        assert result.available is False

    @pytest.mark.parametrize("baseline", [0.0, -220.0, float("nan")])
    def test_unusable_baseline_makes_no_request(self, baseline):
        result, opener = _lookup(
            response=_Response(_SNAPSHOT), NBA_LEAGUE_AVG_TOTAL=baseline,
        )
        assert result.available is False
        opener.assert_not_called()


class TestCaching:
    def setup_method(self):
        clear_market_totals_cache()

    def _run(self, calls, *, side_effect=None, **overrides):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.market_totals_service.urlopen",
                side_effect=side_effect,
            ) if side_effect is not None else patch(
                "app.services.market_totals_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings(**overrides):
                stack.enter_context(ctx)
            results = [get_market_total(*call) for call in calls]
        return results, opener

    def test_second_fixture_reuses_the_snapshot(self):
        results, opener = self._run([
            ("basketball", _DATE, "Boston Celtics", "Miami Heat"),
            ("basketball", _DATE, "Denver Nuggets", "Phoenix Suns"),
        ])
        assert all(r.available for r in results)
        assert opener.call_count == 1

    def test_different_date_fetches_separately(self):
        _, opener = self._run([
            ("basketball", _DATE, "Boston Celtics", "Miami Heat"),
            ("basketball", "2026-08-20", "Boston Celtics", "Miami Heat"),
        ])
        assert opener.call_count == 2

    def test_different_sport_fetches_separately(self):
        _, opener = self._run([
            ("basketball", _DATE, "Boston Celtics", "Miami Heat"),
            ("hockey", _DATE, "Boston Celtics", "Miami Heat"),
        ])
        assert opener.call_count == 2

    def test_failure_is_not_cached(self):
        results, opener = self._run(
            [("basketball", _DATE, "Boston Celtics", "Miami Heat")] * 2,
            side_effect=[OSError("boom"), _Response(_SNAPSHOT)],
        )
        assert results[0].available is False
        assert results[1].available is True
        assert opener.call_count == 2

    def test_zero_ttl_refetches(self):
        _, opener = self._run(
            [("basketball", _DATE, "Boston Celtics", "Miami Heat")] * 2,
            MARKET_TOTALS_CACHE_TTL_MINUTES=0.0,
        )
        assert opener.call_count == 2

    def test_cache_can_be_cleared(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.market_totals_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            get_market_total("basketball", _DATE, "Boston Celtics", "Miami Heat")
            clear_market_totals_cache()
            get_market_total("basketball", _DATE, "Boston Celtics", "Miami Heat")

        assert opener.call_count == 2


class TestCredentialHandling:
    def setup_method(self):
        clear_market_totals_cache()

    def test_key_is_sent_only_as_bearer_header(self):
        _, opener = _lookup(response=_Response(_SNAPSHOT))
        request = opener.call_args.args[0]
        assert request.get_header("Authorization") == "Bearer secret-key"
        assert "secret-key" not in request.full_url

    def test_result_carries_no_credential(self):
        result, _ = _lookup(response=_Response(_SNAPSHOT))
        assert "secret-key" not in repr(result)


class TestInjectIntoCustom:
    """The adapter-facing helper the four sports share."""

    def setup_method(self):
        clear_market_totals_cache()

    _KICKOFF = datetime(2026, 8, 19, 23, 30, tzinfo=timezone.utc)

    def _inject(self, custom=None, *, response=None, side_effect=None, **overrides):
        opener = (
            patch("app.services.market_totals_service.urlopen",
                  side_effect=side_effect)
            if side_effect is not None
            else patch("app.services.market_totals_service.urlopen",
                       return_value=response)
        )
        with ExitStack() as stack:
            stack.enter_context(opener)
            for ctx in _settings(**overrides):
                stack.enter_context(ctx)
            return inject_market_total_into_custom(
                custom,
                sport="basketball",
                kickoff_utc=self._KICKOFF,
                home_name="Boston Celtics",
                away_name="Miami Heat",
            )

    def test_available_total_is_written(self):
        out = self._inject({"b2b_home": True}, response=_Response(_SNAPSHOT))
        assert out["market_total_line"] == pytest.approx(228.5)
        assert out["market_total_p_over"] == pytest.approx(0.5052, abs=1e-4)
        # Existing enrichment is preserved, not replaced.
        assert out["b2b_home"] is True

    def test_kickoff_date_drives_the_request(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.market_totals_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            inject_market_total_into_custom(
                {},
                sport="basketball",
                kickoff_utc=self._KICKOFF,
                home_name="Boston Celtics",
                away_name="Miami Heat",
            )
        assert f"date={_DATE}" in opener.call_args.args[0].full_url

    def test_disabled_provider_writes_nothing(self):
        out = self._inject(
            {"b2b_home": True},
            response=_Response(_SNAPSHOT),
            MARKET_TOTALS_ENABLED=False,
        )
        assert out == {"b2b_home": True}

    def test_unpriced_fixture_writes_nothing(self):
        with ExitStack() as stack:
            stack.enter_context(patch(
                "app.services.market_totals_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            out = inject_market_total_into_custom(
                {},
                sport="basketball",
                kickoff_utc=self._KICKOFF,
                home_name="Denver Nuggets",
                away_name="Phoenix Suns",
            )
        assert out == {}

    def test_transport_failure_writes_nothing(self):
        out = self._inject({}, side_effect=OSError("boom"))
        assert out == {}

    def test_provider_exception_is_contained(self):
        with ExitStack() as stack:
            stack.enter_context(patch(
                "app.services.market_totals_service.get_market_total",
                side_effect=RuntimeError("provider down"),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            out = inject_market_total_into_custom(
                {"b2b_home": True},
                sport="basketball",
                kickoff_utc=self._KICKOFF,
                home_name="Boston Celtics",
                away_name="Miami Heat",
            )
        assert out == {"b2b_home": True}

    @pytest.mark.parametrize("kickoff", [None, "2026-08-19", 0])
    def test_unusable_kickoff_makes_no_request(self, kickoff):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.market_totals_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            out = inject_market_total_into_custom(
                {},
                sport="basketball",
                kickoff_utc=kickoff,
                home_name="Boston Celtics",
                away_name="Miami Heat",
            )
        assert out == {}
        opener.assert_not_called()

    def test_none_custom_yields_a_fresh_dict(self):
        out = self._inject(None, response=_Response(_SNAPSHOT))
        assert out["market_total_line"] == pytest.approx(228.5)

    def test_existing_line_is_not_overwritten(self):
        with ExitStack() as stack:
            opener = stack.enter_context(patch(
                "app.services.market_totals_service.urlopen",
                return_value=_Response(_SNAPSHOT),
            ))
            for ctx in _settings():
                stack.enter_context(ctx)
            out = inject_market_total_into_custom(
                {"market_total_line": 210.0},
                sport="basketball",
                kickoff_utc=self._KICKOFF,
                home_name="Boston Celtics",
                away_name="Miami Heat",
            )
        assert out["market_total_line"] == pytest.approx(210.0)
        opener.assert_not_called()

    def test_input_dict_is_not_mutated(self):
        source: dict = {}
        out = self._inject(source, response=_Response(_SNAPSHOT))
        assert source == {}
        assert "market_total_line" in out
