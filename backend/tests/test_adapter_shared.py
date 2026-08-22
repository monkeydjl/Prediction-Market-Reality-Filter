# backend/tests/test_adapter_shared.py
"""Tests for _shared.py — shared adapter utility functions."""
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import date, datetime, timezone, timedelta
import asyncio
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.services.football_live_referee_service import LiveRefereeResult
from app.services.football_live_style_service import LiveStyleResult
from app.services.football_live_xg_service import LiveXgResult
from app.sports.football.h2h import H2HMeeting
from app.sports.football.adapters._shared import (
    fetch_team_elo,
    fetch_elo_and_odds,
    query_fixture,
    query_result,
    build_match_identity,
    build_match_outcome,
    save_fixture,
    enrich_situational_features,
    enrich_style_features,
)


_FOOTBALL = SportIdentity(code="football", name="Football")
_UCL = CompetitionIdentity(code="ucl", name="UEFA Champions League", sport=_FOOTBALL)


def _make_match(match_id="ucl-123") -> MatchIdentity:
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
        stage="group_stage",
        round=None,
        home=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
        away=TeamIdentity(code="FCB", name="FC Bayern München", competition=_UCL),
        kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
    )


class TestFetchTeamElo:
    @pytest.mark.asyncio
    @patch("app.sports.football.adapters._shared.get_club_elo")
    async def test_club_scope(self, mock_club):
        mock_club.return_value = {"elo_rating": 1955.12, "source": "clubelo"}
        result = await fetch_team_elo("Real Madrid", scope="club")
        assert result is not None
        assert result["elo_rating"] == 1955.12
        mock_club.assert_called_once_with("Real Madrid")

    @pytest.mark.asyncio
    @patch("app.sports.football.adapters._shared.get_club_elo")
    async def test_club_scope_with_alias(self, mock_club):
        mock_club.return_value = {"elo_rating": 1955.12, "source": "clubelo"}
        result = await fetch_team_elo("Real Madrid CF", scope="club", alias="RealMadrid")
        assert result is not None
        mock_club.assert_called_once_with("RealMadrid")


class TestFetchEloAndOdds:
    @patch("app.sports.football.adapters._shared.get_club_elo")
    @patch("app.services.odds_cache_service.get_cached_odds", new_callable=AsyncMock)
    def test_fetch_all_success(self, mock_odds, mock_club):
        mock_club.return_value = {"elo_rating": 1955.12, "source": "clubelo"}
        mock_odds.return_value = {"home": 1.5, "draw": 4.0, "away": 5.5, "source": "test"}

        match = _make_match()
        raw = fetch_elo_and_odds(match, elo_scope="club")

        assert raw["team"]["elo_home"] == 1955.12
        assert raw["team"]["elo_away"] == 1955.12
        assert raw["market"]["odds_home"] == 1.5
        assert raw["market"]["odds_away"] == 5.5
        assert raw["market"]["odds_fresh"] is False  # stale defaults to True
        # Situational enrichment may leave player empty; environment always sets home adv.
        assert isinstance(raw["player"], dict)
        assert "is_home_advantage" in raw["environment"]
        assert raw["environment"]["is_home_advantage"] is True  # ucl club match

    @patch("app.sports.football.adapters._shared.get_club_elo")
    @patch("app.services.odds_cache_service.get_cached_odds", new_callable=AsyncMock)
    def test_fetch_with_team_aliases(self, mock_odds, mock_club):
        mock_club.return_value = {"elo_rating": 1900.0, "source": "clubelo"}
        mock_odds.return_value = None

        match = _make_match()
        aliases = {"Real Madrid CF": "RealMadrid", "FC Bayern München": "BayernMunich"}
        raw = fetch_elo_and_odds(match, elo_scope="club", team_aliases=aliases)

        # get_club_elo called with alias names
        calls = [c.args[0] for c in mock_club.call_args_list]
        assert "RealMadrid" in calls
        assert "BayernMunich" in calls

    @patch("app.sports.football.adapters._shared.get_club_elo")
    @patch("app.services.odds_cache_service.get_cached_odds", new_callable=AsyncMock)
    @patch("app.services.world_cup_historical_results.get_historical_team_stats")
    @patch("app.services.world_cup_historical_results.get_historical_h2h")
    def test_enrich_form_and_h2h(
        self, mock_h2h, mock_stats, mock_odds, mock_club,
    ):
        mock_club.return_value = {"elo_rating": 1800.0, "source": "clubelo"}
        mock_odds.return_value = None
        mock_stats.return_value = {
            "wins": 6, "draws": 2, "losses": 2, "played": 10,
            "goals_per_game": 1.8,
            "last_match_date": "2025-09-01",
        }
        mock_h2h.return_value = {
            "matches_played": 4, "home_wins": 2, "draws": 1, "away_wins": 1,
        }

        match = _make_match()
        raw = fetch_elo_and_odds(match, elo_scope="club")
        # points rate: (3*6 + 2) / (3*10) = 0.6667  (old win rate was 0.6)
        assert raw["team"]["form_home"] == pytest.approx(0.6667)
        assert raw["team"]["form_away"] == pytest.approx(0.6667)
        assert raw["team"]["h2h_home_win_rate"] == 0.5
        assert raw["team"]["h2h_draw_rate"] == 0.25
        assert raw["general"]["rest_days_home"] == 15.0
        from app.sports.football.football_xg import xg_for_team

        assert raw["custom"]["xg_home"] == pytest.approx(
            float(xg_for_team("Real Madrid CF")),
        )
        assert raw["custom"]["xg_away"] == pytest.approx(
            float(xg_for_team("FC Bayern München")),
        )
        assert raw["custom"]["xg_source"] == "static_table"

    @patch("app.sports.football.adapters._shared.get_club_elo")
    @patch("app.services.odds_cache_service.get_cached_odds", new_callable=AsyncMock)
    @patch("app.services.world_cup_historical_results.get_historical_team_stats")
    @patch("app.services.world_cup_historical_results.get_historical_h2h")
    def test_weighted_form_preferred_over_flat(
        self, mock_h2h, mock_stats, mock_odds, mock_club,
    ):
        """Kernel stats carry a per-match sequence, so the weighted rate wins."""
        mock_club.return_value = {"elo_rating": 1800.0, "source": "clubelo"}
        mock_odds.return_value = None
        mock_stats.return_value = {
            "wins": 6, "draws": 2, "losses": 2, "played": 10,
            "goals_per_game": 1.8,
            "last_match_date": "2025-09-01",
            "form_rate_weighted": 0.42,
        }
        mock_h2h.return_value = None

        raw = fetch_elo_and_odds(_make_match(), elo_scope="club")
        assert raw["team"]["form_home"] == pytest.approx(0.42)
        assert raw["team"]["form_away"] == pytest.approx(0.42)


class TestBuildMatchIdentity:
    def test_build_from_fixture(self):
        fixture = MagicMock()
        fixture.match_id = "ucl-537327"
        fixture.home_team = "Real Madrid CF"
        fixture.away_team = "FC Bayern München"
        fixture.stage = "group_stage"
        fixture.kickoff_utc = datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc)

        identity = build_match_identity(fixture, _UCL, "2025-26", "group_stage")
        assert identity.match_id == "ucl-537327"
        assert identity.home.name == "Real Madrid CF"
        assert identity.away.name == "FC Bayern München"
        assert identity.stage == "group_stage"
        assert identity.home.competition == _UCL

    def test_build_with_none_stage(self):
        fixture = MagicMock()
        fixture.match_id = "epl-1"
        fixture.home_team = "Arsenal FC"
        fixture.away_team = "Chelsea FC"
        fixture.stage = None
        fixture.kickoff_utc = None

        identity = build_match_identity(fixture, _UCL, "2025-26", "regular_season")
        assert identity.stage == "regular_season"


class TestBuildMatchOutcome:
    def test_build_from_result(self):
        result = MagicMock()
        result.match_id = "ucl-537327"
        result.home_score = 3
        result.away_score = 1
        result.outcome = "home_win"
        result.finished_at = datetime(2025, 9, 16, 22, 0, tzinfo=timezone.utc)

        outcome = build_match_outcome(result)
        assert outcome.match_id == "ucl-537327"
        assert outcome.home_score == 3
        assert outcome.away_score == 1
        assert outcome.outcome == "home_win"

    def test_build_from_none_returns_none(self):
        assert build_match_outcome(None) is None


class TestEnrichRefereeFeatures:
    def test_passthrough_rate(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        raw = {"custom": {"referee_home_win_rate": 0.62}, "environment": {}}
        enrich_referee_features(raw, _make_match())
        assert raw["custom"]["referee_home_win_rate"] == 0.62
        assert raw["custom"].get("referee_source") != "static_map"

    def test_passthrough_bias_not_overwritten(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        raw = {
            "custom": {"referee_home_bias": 0.11, "referee_name": "Michael Oliver"},
            "environment": {"referee": "Michael Oliver"},
        }
        enrich_referee_features(raw, _make_match())
        assert raw["custom"]["referee_home_bias"] == pytest.approx(0.11)
        assert raw["custom"].get("referee_source") != "static_map"

    def test_environment_unknown_name_only(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        raw = {"custom": {}, "environment": {"referee": "John Smith UnknownXYZ"}}
        enrich_referee_features(raw, _make_match())
        assert raw["custom"]["referee_name"] == "John Smith UnknownXYZ"
        assert raw["custom"].get("referee_home_win_rate") is None
        assert raw["custom"].get("referee_home_bias") is None
        assert raw["custom"].get("referee_source") is None

    def test_static_map_bias_known_name(self):
        from app.sports.football.adapters._shared import enrich_referee_features
        from app.sports.football.football_referee import bias_for_referee

        raw = {"custom": {}, "environment": {"referee": "Michael Oliver"}}
        enrich_referee_features(raw, _make_match())
        expected = bias_for_referee("Michael Oliver")
        assert expected is not None
        assert raw["custom"]["referee_name"] == "Michael Oliver"
        assert raw["custom"]["referee_home_bias"] == pytest.approx(float(expected))
        assert raw["custom"]["referee_source"] == "static_map"

    def test_live_provider_overrides_static_map(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        raw = {"custom": {}, "environment": {"referee": "Michael Oliver"}}
        with patch(
            "app.services.football_live_referee_service.get_live_referee",
            return_value=LiveRefereeResult(True, home_win_rate=0.58, matches=24),
        ):
            enrich_referee_features(raw, _make_match())

        assert raw["custom"]["referee_home_win_rate"] == pytest.approx(0.58)
        assert raw["custom"]["referee_home_bias"] == pytest.approx(0.16)
        assert raw["custom"]["referee_source"] == "live_provider"

    def test_live_no_row_falls_back_to_static_map(self):
        from app.sports.football.adapters._shared import enrich_referee_features
        from app.sports.football.football_referee import bias_for_referee

        raw = {"custom": {}, "environment": {"referee": "Michael Oliver"}}
        with patch(
            "app.services.football_live_referee_service.get_live_referee",
            return_value=LiveRefereeResult(True),
        ):
            enrich_referee_features(raw, _make_match())

        assert raw["custom"]["referee_home_bias"] == pytest.approx(
            float(bias_for_referee("Michael Oliver")),
        )
        assert raw["custom"]["referee_source"] == "static_map"

    def test_world_cup_does_not_call_live_referee_provider(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        world_cup = CompetitionIdentity(
            code="wc", name="FIFA World Cup", sport=_FOOTBALL,
        )
        match = MatchIdentity(
            match_id="wc-referee-no-live",
            season=SeasonIdentity(competition=world_cup, season_key="2026"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(
                code="H", name="Home", competition=world_cup,
            ),
            away=TeamIdentity(
                code="A", name="Away", competition=world_cup,
            ),
            kickoff_utc=datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc),
        )
        raw = {"custom": {}, "environment": {"referee": "Michael Oliver"}}
        with patch(
            "app.services.football_live_referee_service.get_live_referee",
        ) as live_referee:
            enrich_referee_features(raw, match)

        live_referee.assert_not_called()
        assert raw["custom"]["referee_source"] == "static_map"


class TestScheduleDensityEnrich:
    def test_matches_last_7d_and_congest_from_count(self):
        """count>=2 sets congest True even when rest_days > 2."""
        match = _make_match("ucl-dense")
        raw = {
            "team": {},
            "general": {"rest_days_home": 4.0, "rest_days_away": 4.0},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        history = [
            {
                "match_id": "ucl-1",
                "home_team": "Real Madrid CF",
                "away_team": "X",
                "kickoff_utc": datetime(2025, 9, 10, 20, 0, tzinfo=timezone.utc),
            },
            {
                "match_id": "ucl-2",
                "home_team": "Y",
                "away_team": "Real Madrid CF",
                "kickoff_utc": datetime(2025, 9, 13, 20, 0, tzinfo=timezone.utc),
            },
            {
                "match_id": "ucl-dense",
                "home_team": "Real Madrid CF",
                "away_team": "FC Bayern München",
                "kickoff_utc": match.kickoff_utc,
            },
        ]
        with patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=history,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ):
            # Avoid club_form DB; rest already set on raw
            with patch(
                "app.sports.football.club_form.team_form_from_kernel",
                return_value=None,
            ):
                enrich_situational_features(raw, match)

        assert raw["custom"]["matches_last_7d_home"] == 2
        assert raw["custom"]["schedule_congested_home"] is True
        assert raw["custom"]["b2b_home"] is False  # rest 4

    def test_count_one_overrides_rest_proxy_congest(self):
        """Known count < 2 → congest False even if rest_days <= 2."""
        match = _make_match("ucl-sparse")
        raw = {
            "team": {},
            "general": {"rest_days_home": 2.0, "rest_days_away": 5.0},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        history = [
            {
                "match_id": "ucl-1",
                "home_team": "Real Madrid CF",
                "away_team": "X",
                "kickoff_utc": datetime(2025, 9, 14, 20, 0, tzinfo=timezone.utc),
            },
            {
                "match_id": "ucl-sparse",
                "home_team": "Real Madrid CF",
                "away_team": "FC Bayern München",
                "kickoff_utc": match.kickoff_utc,
            },
        ]
        with patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=history,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert raw["custom"]["matches_last_7d_home"] == 1
        assert raw["custom"]["schedule_congested_home"] is False
        assert raw["custom"]["b2b_home"] is False

    def test_no_history_falls_back_to_rest_congest(self):
        match = _make_match("ucl-fallback")
        raw = {
            "team": {},
            "general": {"rest_days_home": 1.0, "rest_days_away": 5.0},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert "matches_last_7d_home" not in raw["custom"]
        assert raw["custom"]["schedule_congested_home"] is True
        assert raw["custom"]["b2b_home"] is True


def _raw_for_density() -> dict:
    return {
        "team": {},
        "general": {"rest_days_home": 4.0, "rest_days_away": 4.0},
        "market": {},
        "player": {},
        "environment": {},
        "custom": {},
    }


def _row(match_id, home, away, kickoff, competition):
    return {
        "match_id": match_id,
        "home_team": home,
        "away_team": away,
        "kickoff_utc": kickoff,
        "competition": competition,
    }


def _enrich_with_merged(raw, match, single, merged):
    """Run enrich with both density histories stubbed."""
    with patch(
        "app.sports.football.adapters._shared._fixture_history_for_density",
        return_value=single,
    ), patch(
        "app.sports.football.adapters._shared._merged_fixture_rows",
        return_value=merged,
    ), patch(
        "app.services.world_cup_historical_results.get_historical_team_stats",
        return_value=None,
    ), patch(
        "app.services.world_cup_historical_results.get_historical_h2h",
        return_value=None,
    ), patch(
        "app.sports.football.club_form.team_form_from_kernel",
        return_value=None,
    ):
        enrich_situational_features(raw, match)


class TestMergedScheduleDensity:
    """P1-F2 residual: cross-competition merge + 3-day window."""

    def test_live_history_is_used_when_merged_kernel_rows_are_empty(self):
        from app.services.football_live_schedule_service import LiveScheduleResult
        from app.sports.football.adapters._shared import _merged_fixture_history

        fixture = _row(
            "live-1",
            "Real Madrid CF",
            "FC Bayern München",
            datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc),
            "ucl",
        )
        with patch(
            "app.sports.football.adapters._shared._merged_fixture_rows",
            return_value=[],
        ), patch(
            "app.services.football_live_schedule_service.get_live_schedule",
            return_value=LiveScheduleResult(available=True, fixtures=[fixture]),
        ) as live_schedule:
            result = _merged_fixture_history(
                "2025-26",
                datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
            )

        assert result is not None
        assert result[0]["match_id"] == "live-1"
        assert live_schedule.call_count > 0

    def test_kernel_merged_rows_take_precedence_over_live_schedule(self):
        from app.services.football_live_schedule_service import LiveScheduleResult
        from app.sports.football.adapters._shared import _merged_fixture_history

        kernel = _row(
            "kernel-1",
            "Real Madrid CF",
            "FC Bayern München",
            datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc),
            "ucl",
        )
        with patch(
            "app.sports.football.adapters._shared._merged_fixture_rows",
            return_value=[kernel],
        ), patch(
            "app.services.football_live_schedule_service.get_live_schedule",
            return_value=LiveScheduleResult(available=True, fixtures=[]),
        ) as live_schedule:
            result = _merged_fixture_history(
                "2025-26",
                datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
            )

        assert result is not None
        assert result[0]["match_id"] == "kernel-1"
        live_schedule.assert_not_called()

    def test_live_single_competition_fallback_when_kernel_history_missing(self):
        from app.sports.football.adapters._shared import _fixture_history_for_density

        fixture = {
            "match_id": "live-epl-1",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "kickoff_utc": datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc),
        }
        with patch(
            "app.kernel.kernel_db.get_kernel_session",
            side_effect=RuntimeError("kernel unavailable"),
        ), patch(
            "app.sports.football.adapters._shared._live_fixture_history_for_density",
            return_value=[fixture],
        ) as live_history:
            result = _fixture_history_for_density(
                "epl",
                "2025-26",
                datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
            )

        assert result == [fixture]
        live_history.assert_called_once()

    def test_merged_counts_other_competition(self):
        match = _make_match("ucl-merge")
        raw = _raw_for_density()
        single = [
            _row("epl-1", "Real Madrid CF", "X",
                 datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc), "epl"),
        ]
        merged = single + [
            _row("ucl-1", "Y", "Real Madrid CF",
                 datetime(2025, 9, 10, 20, 0, tzinfo=timezone.utc), "ucl"),
        ]
        _enrich_with_merged(raw, match, single, merged)

        assert raw["custom"]["matches_merged_7d_home"] == 2
        assert raw["custom"]["matches_last_7d_home"] == 1

    def test_merged_matches_across_name_spellings(self):
        match = _make_match("ucl-spelling")
        raw = _raw_for_density()
        merged = [
            _row("epl-1", "Manchester City", "X",
                 datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc), "epl"),
            _row("ucl-1", "Y", "Man City",
                 datetime(2025, 9, 10, 20, 0, tzinfo=timezone.utc), "ucl"),
        ]
        city = MatchIdentity(
            match_id="ucl-spelling",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="MCI", name="Man City", competition=_UCL),
            away=TeamIdentity(code="FCB", name="FC Bayern München", competition=_UCL),
            kickoff_utc=match.kickoff_utc,
        )
        _enrich_with_merged(raw, city, [], merged)

        assert raw["custom"]["matches_merged_7d_home"] == 2

    def test_colliding_alias_across_competitions_not_merged(self):
        """CEL is celta_vigo in laliga but celtic in ucl — must not merge."""
        match = MatchIdentity(
            match_id="ucl-cel",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="CEL", name="CEL", competition=_UCL),
            away=TeamIdentity(code="FCB", name="FC Bayern München", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = _raw_for_density()
        merged = [
            _row("laliga-1", "CEL", "X",
                 datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc), "laliga"),
        ]
        _enrich_with_merged(raw, match, [], merged)

        assert raw["custom"]["matches_merged_7d_home"] == 0

    def test_unknown_team_falls_back_to_string_match(self):
        match = MatchIdentity(
            match_id="ucl-obscure",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="OBS", name="Obscure Town FC", competition=_UCL),
            away=TeamIdentity(code="FCB", name="FC Bayern München", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = _raw_for_density()
        merged = [
            _row("epl-1", "obscure town fc", "X",
                 datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc), "epl"),
        ]
        _enrich_with_merged(raw, match, [], merged)

        assert raw["custom"]["matches_merged_7d_home"] == 1

    def test_three_day_window_is_narrower(self):
        match = _make_match("ucl-3d")
        raw = _raw_for_density()
        merged = [
            _row("epl-1", "Real Madrid CF", "X",
                 datetime(2025, 9, 11, 15, 0, tzinfo=timezone.utc), "epl"),
            _row("ucl-1", "Y", "Real Madrid CF",
                 datetime(2025, 9, 14, 20, 0, tzinfo=timezone.utc), "ucl"),
        ]
        _enrich_with_merged(raw, match, [], merged)

        assert raw["custom"]["matches_merged_7d_home"] == 2
        assert raw["custom"]["matches_merged_3d_home"] == 1

    def test_current_match_excluded(self):
        match = _make_match("ucl-self")
        raw = _raw_for_density()
        merged = [
            _row("ucl-self", "Real Madrid CF", "FC Bayern München",
                 match.kickoff_utc, "ucl"),
        ]
        _enrich_with_merged(raw, match, [], merged)

        assert raw["custom"]["matches_merged_7d_home"] == 0

    def test_away_side_counted_separately(self):
        match = _make_match("ucl-away")
        raw = _raw_for_density()
        merged = [
            _row("epl-1", "FC Bayern München", "X",
                 datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc), "epl"),
        ]
        _enrich_with_merged(raw, match, [], merged)

        assert raw["custom"]["matches_merged_7d_away"] == 1
        assert raw["custom"]["matches_merged_7d_home"] == 0


_WORLD_CUP = CompetitionIdentity(code="wc", name="FIFA World Cup", sport=_FOOTBALL)
_WC_KICKOFF = datetime(2026, 6, 20, 18, 0, tzinfo=timezone.utc)


def _wc_match(match_id="wc-density") -> MatchIdentity:
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_WORLD_CUP, season_key="2026"),
        stage="group_stage",
        round=None,
        home=TeamIdentity(code="BRA", name="Brazil", competition=_WORLD_CUP),
        away=TeamIdentity(code="CRO", name="Croatia", competition=_WORLD_CUP),
        kickoff_utc=_WC_KICKOFF,
    )


def _enrich_with_international(raw, match, merged, intl_dates, *, history_none=False):
    """Run enrich with the kernel merge and the international CSV both stubbed.

    ``intl_dates`` maps a team name to the dates its CSV lookup returns; anything
    callable is used as the patch side effect directly (for failure cases).
    """
    side_effect = (
        intl_dates
        if callable(intl_dates)
        else lambda team, **_kw: tuple(intl_dates.get(team, ()))
    )
    merged_patch = (
        patch(
            "app.sports.football.adapters._shared._merged_fixture_history",
            return_value=None,
        )
        if history_none
        else patch(
            "app.sports.football.adapters._shared._merged_fixture_rows",
            return_value=merged,
        )
    )
    with merged_patch, patch(
        "app.sports.football.adapters._shared._fixture_history_for_density",
        return_value=[],
    ), patch(
        "app.services.world_cup_historical_results.international_match_dates",
        side_effect=side_effect,
    ) as intl, patch(
        "app.services.world_cup_historical_results.get_historical_team_stats",
        return_value=None,
    ), patch(
        "app.services.world_cup_historical_results.get_historical_h2h",
        return_value=None,
    ), patch(
        "app.services.world_cup_historical_results.historical_h2h_meetings",
        return_value=[],
    ):
        enrich_situational_features(raw, match)
    return intl


class TestInternationalScheduleDensity:
    """P1-F2 residual: real international match days for national teams."""

    def test_international_days_lift_national_team_counts(self):
        raw = _raw_for_density()
        merged = [
            _row("wc-earlier", "Brazil", "X",
                 datetime(2026, 6, 14, 18, 0, tzinfo=timezone.utc), "wc"),
        ]
        _enrich_with_international(
            raw, _wc_match(), merged,
            {"Brazil": [date(2026, 6, 16), date(2026, 6, 18)]},
        )

        # One kernel tournament fixture plus two qualifier/friendly match days.
        assert raw["custom"]["matches_merged_7d_home"] == 3
        assert raw["custom"]["matches_intl_7d_home"] == 2
        assert raw["custom"]["schedule_intl_source"] == "international_results"

    def test_kernel_and_csv_same_date_counted_once(self):
        raw = _raw_for_density()
        merged = [
            _row("wc-earlier", "Brazil", "X",
                 datetime(2026, 6, 16, 18, 0, tzinfo=timezone.utc), "wc"),
        ]
        _enrich_with_international(
            raw, _wc_match(), merged,
            # Brazil's CSV row is the same match already in the kernel; Croatia's
            # is genuinely absent from it.
            {"Brazil": [date(2026, 6, 16)], "Croatia": [date(2026, 6, 17)]},
        )

        assert raw["custom"]["matches_merged_7d_home"] == 1
        assert raw["custom"]["matches_intl_7d_home"] == 0
        assert raw["custom"]["matches_merged_7d_away"] == 1
        assert raw["custom"]["matches_intl_7d_away"] == 1

    def test_three_day_window_sees_international_day(self):
        raw = _raw_for_density()
        _enrich_with_international(
            raw, _wc_match(), [],
            {"Brazil": [date(2026, 6, 14), date(2026, 6, 18)]},
        )

        assert raw["custom"]["matches_merged_7d_home"] == 2
        assert raw["custom"]["matches_merged_3d_home"] == 1

    def test_counts_written_when_kernel_history_unavailable(self):
        raw = _raw_for_density()
        _enrich_with_international(
            raw, _wc_match(), None,
            {"Brazil": [date(2026, 6, 18)]},
            history_none=True,
        )

        assert raw["custom"]["matches_merged_7d_home"] == 1
        assert raw["custom"]["matches_intl_7d_home"] == 1

    def test_club_fixture_never_consults_international_csv(self):
        raw = _raw_for_density()
        merged = [
            _row("epl-1", "Real Madrid CF", "X",
                 datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc), "epl"),
        ]
        intl = _enrich_with_international(
            raw, _make_match("ucl-club"), merged, {"Real Madrid CF": [date(2025, 9, 11)]},
        )

        intl.assert_not_called()
        assert raw["custom"]["matches_merged_7d_home"] == 1
        assert "schedule_intl_source" not in raw["custom"]

    def test_lookup_failure_preserves_kernel_counts(self):
        raw = _raw_for_density()
        merged = [
            _row("wc-earlier", "Brazil", "X",
                 datetime(2026, 6, 16, 18, 0, tzinfo=timezone.utc), "wc"),
        ]

        def _boom(*_args, **_kwargs):
            raise RuntimeError("CSV unreadable")

        _enrich_with_international(raw, _wc_match(), merged, _boom)

        assert raw["custom"]["matches_merged_7d_home"] == 1
        assert "matches_intl_7d_home" not in raw["custom"]
        assert "schedule_intl_source" not in raw["custom"]


class TestMergedFixtureHistory:
    def test_non_football_rows_dropped(self):
        from app.sports.football.adapters._shared import _merged_history_rows

        kickoff = datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc)
        rows = [
            _row("epl-1", "Manchester City", "X", kickoff, "epl"),
            _row("nba-1", "Manchester City", "X", kickoff, "nba"),
        ]
        out = _merged_history_rows(rows)

        assert len(out) == 1
        assert out[0]["match_id"] == "epl-1"

    def test_rows_resolved_against_own_competition(self):
        from app.sports._shared.team_aliases import comparison_key
        from app.sports.football.adapters._shared import _merged_history_rows

        kickoff = datetime(2025, 9, 13, 15, 0, tzinfo=timezone.utc)
        rows = [_row("ucl-1", "Man City", "CEL", kickoff, "ucl")]
        out = _merged_history_rows(rows)

        assert out[0]["home_team"] == comparison_key("Manchester City", "epl")
        assert out[0]["away_team"] == comparison_key("Celtic", "ucl")


class TestH2hKernelFallback:
    def test_kernel_fills_when_historical_none(self):
        match = _make_match("ucl-h2h-kernel")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        kernel_h2h = {
            "matches_played": 2,
            "home_wins": 1,
            "draws": 1,
            "away_wins": 0,
            "data_source": "kernel_match_results",
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.historical_h2h_meetings",
            return_value=[],
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_meetings_from_kernel",
            return_value=[
                H2HMeeting(datetime(2025, 9, 1).date(), 1, 0, True),
                H2HMeeting(datetime(2025, 8, 1).date(), 0, 0, False),
            ],
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=kernel_h2h,
        ) as mock_kh:
            enrich_situational_features(raw, match)

        mock_kh.assert_not_called()
        assert raw["team"]["h2h_home_win_rate"] == pytest.approx(0.5)
        assert raw["team"]["h2h_draw_rate"] == pytest.approx(0.5)

    def test_combines_sources_and_deduplicates_overlap(self):
        match = _make_match("ucl-h2h-combined")
        raw = {
            "team": {}, "general": {}, "market": {}, "player": {},
            "environment": {}, "custom": {},
        }
        duplicate = H2HMeeting(datetime(2025, 9, 1).date(), 2, 0, True)
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.historical_h2h_meetings",
            return_value=[duplicate],
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_meetings_from_kernel",
            return_value=[
                duplicate,
                H2HMeeting(datetime(2025, 8, 1).date(), 1, 1, False),
            ],
        ):
            enrich_situational_features(raw, match)

        assert raw["team"]["h2h_home_win_rate"] == pytest.approx(0.5)
        assert raw["team"]["h2h_draw_rate"] == pytest.approx(0.5)
        assert raw["custom"]["h2h_home_venue_matches"] == 1.0
        assert raw["custom"]["h2h_home_venue_win_rate"] == pytest.approx(1.0)
        assert raw["custom"]["h2h_home_venue_draw_rate"] == pytest.approx(0.0)

    def test_historical_not_overwritten_by_kernel(self):
        match = _make_match("ucl-h2h-hist")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        hist = {
            "matches_played": 4,
            "home_wins": 2,
            "draws": 1,
            "away_wins": 1,
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=hist,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value={
                "matches_played": 2,
                "home_wins": 2,
                "draws": 0,
                "away_wins": 0,
                "data_source": "kernel_match_results",
            },
        ) as mock_kh:
            enrich_situational_features(raw, match)

        mock_kh.assert_not_called()
        assert raw["team"]["h2h_home_win_rate"] == pytest.approx(0.5)
        assert raw["team"]["h2h_draw_rate"] == pytest.approx(0.25)

    def test_both_empty_omits_h2h(self):
        match = _make_match("ucl-h2h-empty")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert "h2h_home_win_rate" not in raw["team"]
        assert "h2h_draw_rate" not in raw["team"]


class TestInjuryImpactEnrich:
    def test_static_dual_writes_sample_teams(self):
        """Real Madrid / Bayern sample Outs inject player + custom impacts."""
        match = _make_match("ucl-injury")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.world_cup_player_status_source.get_team_injury_impact",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert raw["player"]["injury_impact_home"] == pytest.approx(0.35)
        assert raw["player"]["injury_impact_away"] == pytest.approx(0.26)
        assert raw["custom"]["injury_impact_home"] == pytest.approx(0.35)
        assert raw["custom"]["injury_impact_away"] == pytest.approx(0.26)
        assert raw["custom"]["injury_source_home"] == "static_table"
        assert raw["custom"]["injury_source_away"] == "static_table"

    def test_contextual_availability_overrides_other_injury_sources(self):
        from app.services.football_live_availability_service import LiveAvailabilityImpact

        match = _make_match("ucl-contextual-availability")
        raw = {
            "team": {}, "general": {}, "market": {}, "player": {},
            "environment": {}, "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.football_live_availability_service.get_live_availability_impact",
            side_effect=[
                LiveAvailabilityImpact(available=True, impact=0.35),
                LiveAvailabilityImpact(available=True, impact=0.29),
            ],
        ), patch(
            "app.services.football_live_injury_service.get_live_injury_impact",
        ) as injury_provider, patch(
            "app.sports.football.football_injury.injury_impact_for_team",
        ) as static_mock:
            enrich_situational_features(raw, match)

        injury_provider.assert_not_called()
        static_mock.assert_not_called()
        assert raw["player"]["injury_impact_home"] == pytest.approx(0.35)
        assert raw["player"]["injury_impact_away"] == pytest.approx(0.29)
        assert raw["custom"]["injury_source_home"] == "live_availability_provider"
        assert raw["custom"]["injury_source_away"] == "live_availability_provider"

    def test_missing_contextual_availability_falls_back_to_api_football(self):
        from app.services.football_live_availability_service import LiveAvailabilityImpact
        from app.services.football_live_injury_service import LiveInjuryImpact

        match = _make_match("ucl-contextual-fallback")
        raw = {
            "team": {}, "general": {}, "market": {}, "player": {},
            "environment": {}, "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.football_live_availability_service.get_live_availability_impact",
            side_effect=[
                LiveAvailabilityImpact(available=True, impact=None),
                LiveAvailabilityImpact(available=False),
            ],
        ), patch(
            "app.services.football_live_injury_service.get_live_injury_impact",
            side_effect=[
                LiveInjuryImpact(available=True, impact=0.12),
                LiveInjuryImpact(available=True, impact=0.27),
            ],
        ) as injury_provider:
            enrich_situational_features(raw, match)

        assert injury_provider.call_count == 2
        assert raw["custom"]["injury_source_home"] == "api_football"
        assert raw["custom"]["injury_source_away"] == "api_football"

        from app.services.football_live_injury_service import LiveInjuryImpact

        match = _make_match("ucl-live-injuries")
        raw = {
            "team": {}, "general": {}, "market": {}, "player": {},
            "environment": {}, "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.football_live_injury_service.get_live_injury_impact",
            side_effect=[
                LiveInjuryImpact(available=True, impact=0.12),
                LiveInjuryImpact(available=True, impact=0.27),
            ],
        ), patch(
            "app.sports.football.football_injury.injury_impact_for_team",
        ) as static_mock:
            enrich_situational_features(raw, match)

        static_mock.assert_not_called()
        assert raw["player"]["injury_impact_home"] == pytest.approx(0.12)
        assert raw["player"]["injury_impact_away"] == pytest.approx(0.27)
        assert raw["custom"]["injury_source_home"] == "api_football"
        assert raw["custom"]["injury_source_away"] == "api_football"

    def test_successful_live_no_absence_does_not_fall_back_to_static(self):
        from app.services.football_live_injury_service import LiveInjuryImpact

        match = _make_match("ucl-live-no-absence")
        raw = {
            "team": {}, "general": {}, "market": {}, "player": {},
            "environment": {}, "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.football_live_injury_service.get_live_injury_impact",
            return_value=LiveInjuryImpact(available=True, impact=None),
        ), patch(
            "app.sports.football.football_injury.injury_impact_for_team",
        ) as static_mock:
            enrich_situational_features(raw, match)

        static_mock.assert_not_called()
        assert "injury_impact_home" not in raw["player"]
        assert "injury_impact_away" not in raw["player"]
        assert "injury_source_home" not in raw["custom"]
        assert "injury_source_away" not in raw["custom"]

    def test_unknown_teams_omit_injury_keys(self):
        football = SportIdentity(code="football", name="Football")
        ucl = CompetitionIdentity(code="ucl", name="UCL", sport=football)
        match = MatchIdentity(
            match_id="ucl-unknown-inj",
            season=SeasonIdentity(competition=ucl, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="XXX", name="Fake Home FC", competition=ucl),
            away=TeamIdentity(code="YYY", name="Fake Away FC", competition=ucl),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.world_cup_player_status_source.get_team_injury_impact",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert "injury_impact_home" not in raw["player"]
        assert "injury_impact_away" not in raw["player"]
        assert "injury_impact_home" not in raw["custom"]
        assert "injury_impact_away" not in raw["custom"]

    def test_wc_fallback_when_static_none(self):
        """WC source fills a side only when static returns None."""
        football = SportIdentity(code="football", name="Football")
        ucl = CompetitionIdentity(code="ucl", name="UCL", sport=football)
        match = MatchIdentity(
            match_id="ucl-wc-fallback",
            season=SeasonIdentity(competition=ucl, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="XXX", name="Fake Home FC", competition=ucl),
            away=TeamIdentity(code="YYY", name="Fake Away FC", competition=ucl),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }

        def _wc_side(name: str):
            if name == "Fake Home FC":
                return 0.22
            return None

        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.world_cup_player_status_source.get_team_injury_impact",
            side_effect=_wc_side,
        ):
            enrich_situational_features(raw, match)

        assert raw["player"]["injury_impact_home"] == pytest.approx(0.22)
        assert raw["custom"]["injury_impact_home"] == pytest.approx(0.22)
        assert "injury_impact_away" not in raw["player"]
        assert "injury_impact_away" not in raw["custom"]

    def test_static_not_overwritten_by_wc(self):
        match = _make_match("ucl-static-wins")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.world_cup_player_status_source.get_team_injury_impact",
            return_value=0.99,
        ):
            enrich_situational_features(raw, match)

        assert raw["player"]["injury_impact_home"] == pytest.approx(0.35)
        assert raw["player"]["injury_impact_away"] == pytest.approx(0.26)


class TestStaticXgOverwrite:
    def test_both_static_hits_overwrite_proxy(self):
        match = _make_match("ucl-xg-static")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        # Proxy would write 1.1 / 1.1; static for Real Madrid CF / Bayern must win
        hist = {
            "wins": 5,
            "draws": 2,
            "losses": 3,
            "played": 10,
            "goals_per_game": 1.1,
            "last_match_date": "2025-09-01",
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=hist,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        from app.sports.football.football_xg import xg_for_team

        assert raw["custom"]["xg_home"] == pytest.approx(
            float(xg_for_team("Real Madrid CF")),
        )
        assert raw["custom"]["xg_away"] == pytest.approx(
            float(xg_for_team("FC Bayern München")),
        )
        assert raw["custom"]["xg_source"] == "static_table"
        # Must not remain goals proxy
        assert raw["custom"]["xg_home"] != pytest.approx(1.1)

    def test_one_side_unknown_keeps_proxy(self):
        match = MatchIdentity(
            match_id="ucl-xg-partial",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            away=TeamIdentity(code="ZZZ", name="Unknown Club XYZ", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        hist = {
            "wins": 4,
            "draws": 3,
            "losses": 3,
            "played": 10,
            "goals_per_game": 1.25,
            "last_match_date": "2025-09-01",
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=hist,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert raw["custom"].get("xg_home") == pytest.approx(1.25)
        assert raw["custom"].get("xg_away") == pytest.approx(1.25)
        assert "xg_source" not in raw["custom"]

    def test_both_unknown_no_static_source(self):
        match = MatchIdentity(
            match_id="ucl-xg-none",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="AAA", name="NoSuchHome FC", competition=_UCL),
            away=TeamIdentity(code="BBB", name="NoSuchAway FC", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert "xg_home" not in raw["custom"]
        assert "xg_away" not in raw["custom"]
        assert "xg_source" not in raw["custom"]


class TestLiveXgOverwrite:
    _HIST = {
        "wins": 5,
        "draws": 2,
        "losses": 3,
        "played": 10,
        "goals_per_game": 1.1,
        "last_match_date": "2025-09-01",
    }

    @staticmethod
    def _raw() -> dict:
        return {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }

    def _enrich(
        self,
        raw: dict,
        match: MatchIdentity,
        live_results: list[LiveXgResult],
        *,
        hist: dict | None = None,
    ):
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=self._HIST if hist is None else hist,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=None,
        ), patch(
            "app.services.football_live_xg_service.get_live_xg",
            side_effect=live_results,
        ) as live_xg:
            enrich_situational_features(raw, match)
        return live_xg

    def test_complete_live_pair_overrides_static_pair(self):
        raw = self._raw()
        self._enrich(
            raw,
            _make_match("ucl-xg-live"),
            [
                LiveXgResult(available=True, xg_per90=2.01),
                LiveXgResult(available=True, xg_per90=1.34),
            ],
        )

        assert raw["custom"]["xg_home"] == pytest.approx(2.01)
        assert raw["custom"]["xg_away"] == pytest.approx(1.34)
        assert raw["custom"]["xg_source"] == "live_provider"

    def test_incomplete_live_pair_falls_back_to_static_pair(self):
        raw = self._raw()
        self._enrich(
            raw,
            _make_match("ucl-xg-live-partial"),
            [
                LiveXgResult(available=True, xg_per90=2.01),
                LiveXgResult(available=True),
            ],
        )

        from app.sports.football.football_xg import xg_for_team

        assert raw["custom"]["xg_home"] == pytest.approx(
            float(xg_for_team("Real Madrid CF")),
        )
        assert raw["custom"]["xg_away"] == pytest.approx(
            float(xg_for_team("FC Bayern München")),
        )
        assert raw["custom"]["xg_source"] == "static_table"

    def test_unavailable_live_provider_falls_back_to_static_pair(self):
        raw = self._raw()
        self._enrich(
            raw,
            _make_match("ucl-xg-live-unavailable"),
            [LiveXgResult(available=False), LiveXgResult(available=False)],
        )

        assert raw["custom"]["xg_source"] == "static_table"

    def test_incomplete_live_and_static_data_preserve_gpg_proxy(self):
        match = MatchIdentity(
            match_id="ucl-xg-live-proxy",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            away=TeamIdentity(code="ZZZ", name="Unknown Club XYZ", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = self._raw()
        hist = {**self._HIST, "goals_per_game": 1.25}
        self._enrich(
            raw,
            match,
            [
                LiveXgResult(available=True, xg_per90=2.01),
                LiveXgResult(available=True),
            ],
            hist=hist,
        )

        assert raw["custom"]["xg_home"] == pytest.approx(1.25)
        assert raw["custom"]["xg_away"] == pytest.approx(1.25)
        assert "xg_source" not in raw["custom"]

    def test_world_cup_does_not_call_live_xg_provider(self):
        world_cup = CompetitionIdentity(
            code="wc", name="FIFA World Cup", sport=_FOOTBALL,
        )
        match = MatchIdentity(
            match_id="wc-xg-no-live",
            season=SeasonIdentity(competition=world_cup, season_key="2026"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="RMA", name="Real Madrid CF", competition=world_cup),
            away=TeamIdentity(code="FCB", name="FC Bayern München", competition=world_cup),
            kickoff_utc=datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc),
        )
        raw = self._raw()
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=self._HIST,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.services.football_live_xg_service.get_live_xg",
        ) as live_xg:
            enrich_situational_features(raw, match)

        live_xg.assert_not_called()
        assert raw["custom"]["xg_source"] == "static_table"


class TestLiveStyleOverwrite:
    @staticmethod
    def _raw() -> dict:
        """The shape ``fetch_elo_and_odds`` actually hands the enricher.

        ``custom`` carries no possession keys. This fixture used to seed the
        form-share proxy and a ``possession_proxy`` marker, but that producer
        was removed in P1-F6 and nothing in ``app/`` writes either key before
        ``enrich_style_features`` runs, so the seeded state was unreachable in
        production and the assertions against it could not fail for a
        production reason.
        """
        return {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }

    def test_complete_live_pair_overrides_static_pair(self):
        raw = self._raw()
        with patch(
            "app.services.football_live_style_service.get_live_style",
            side_effect=[
                LiveStyleResult(
                    True,
                    {"possession_pct": 61.2, "shots_per90": 16.4, "ppda": 8.1},
                ),
                LiveStyleResult(
                    True,
                    {"possession_pct": 48.8, "shots_per90": 11.2, "ppda": 13.6},
                ),
            ],
        ):
            enrich_style_features(raw, _make_match("ucl-style-live"))

        assert raw["custom"]["possession_home"] == pytest.approx(61.2)
        assert raw["custom"]["possession_away"] == pytest.approx(48.8)
        assert raw["custom"]["shots_home"] == pytest.approx(16.4)
        assert raw["custom"]["ppda_away"] == pytest.approx(13.6)
        assert raw["custom"]["style_source"] == "live_provider"
        assert "possession_proxy" not in raw["custom"]

    def test_live_pair_wins_over_a_static_pair_that_would_also_resolve(self):
        """Both sources can answer; the live one must be the one that lands.

        ``_make_match`` uses Real Madrid CF / FC Bayern München, which the static
        table carries, so a green result here means live took precedence rather
        than static merely being absent.
        """
        from app.sports.football.football_style import stats_for_team

        static_home = stats_for_team("Real Madrid CF")
        static_away = stats_for_team("FC Bayern München")
        assert static_home is not None and static_away is not None  # premise

        raw = self._raw()
        with patch(
            "app.services.football_live_style_service.get_live_style",
            side_effect=[
                LiveStyleResult(
                    True,
                    {"possession_pct": 61.2, "shots_per90": 16.4, "ppda": 8.1},
                ),
                LiveStyleResult(
                    True,
                    {"possession_pct": 48.8, "shots_per90": 11.2, "ppda": 13.6},
                ),
            ],
        ):
            enrich_style_features(raw, _make_match("ucl-style-live-wins"))

        assert raw["custom"]["style_source"] == "live_provider"
        assert raw["custom"]["possession_home"] == pytest.approx(61.2)
        assert raw["custom"]["possession_home"] != pytest.approx(
            static_home["possession_pct"],
        )

    def test_incomplete_live_pair_falls_back_to_static_pair(self):
        raw = self._raw()
        with patch(
            "app.services.football_live_style_service.get_live_style",
            side_effect=[
                LiveStyleResult(
                    True,
                    {"possession_pct": 61.2, "shots_per90": 16.4, "ppda": 8.1},
                ),
                LiveStyleResult(True),
            ],
        ):
            enrich_style_features(raw, _make_match("ucl-style-live-partial"))

        from app.sports.football.football_style import stats_for_team

        home = stats_for_team("Real Madrid CF")
        away = stats_for_team("FC Bayern München")
        assert home is not None and away is not None
        assert raw["custom"]["possession_home"] == pytest.approx(home["possession_pct"])
        assert raw["custom"]["possession_away"] == pytest.approx(away["possession_pct"])
        assert raw["custom"]["style_source"] == "static_table"

    def test_live_and_static_incomplete_write_nothing(self):
        """Neither source resolves a full pair, so ``custom`` stays empty.

        Renamed from ``..._preserve_form_proxy``: the old name and its
        assertions described the removed form-share producer (P1-F6) as
        intended behaviour, and read as though possession arriving from
        somewhere else were a supported input.
        """
        raw = self._raw()
        match = MatchIdentity(
            match_id="ucl-style-half-pair",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            away=TeamIdentity(code="ZZZ", name="Unknown Club XYZ", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        with patch(
            "app.services.football_live_style_service.get_live_style",
            side_effect=[LiveStyleResult(False), LiveStyleResult(False)],
        ):
            enrich_style_features(raw, match)

        # Half a pair is not a share. One side alone must not be completed with
        # a guess; the engine marks the factor unavailable instead.
        assert "possession_home" not in raw["custom"]
        assert "possession_away" not in raw["custom"]
        assert "possession_proxy" not in raw["custom"]
        assert "style_source" not in raw["custom"]
        assert "shots_home" not in raw["custom"]

    def test_national_teams_get_no_style_and_no_live_call(self):
        """The World Cup branch, driven with the names it exists for.

        The previous version of this test used Real Madrid CF / FC Bayern
        München under a ``wc`` competition code, so the static table answered
        and it asserted ``style_source == "static_table"`` — it could not
        observe the national-team path it was named for. National teams have no
        club style row, which is the whole reason the guard skips the provider.

        Note this branch is defensive rather than live: ``WorldCupAdapter``
        builds its own raw dict and calls no enricher, so no production fixture
        reaches here with a World Cup code.
        """
        from app.sports.football.football_style import stats_for_team

        assert stats_for_team("Brazil") is None  # premise
        assert stats_for_team("Argentina") is None

        world_cup = CompetitionIdentity(
            code="wc", name="FIFA World Cup", sport=_FOOTBALL,
        )
        match = MatchIdentity(
            match_id="wc-style-no-live",
            season=SeasonIdentity(competition=world_cup, season_key="2026"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="BRA", name="Brazil", competition=world_cup),
            away=TeamIdentity(code="ARG", name="Argentina", competition=world_cup),
            kickoff_utc=datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc),
        )
        raw = self._raw()
        with patch("app.services.football_live_style_service.get_live_style") as live_style:
            enrich_style_features(raw, match)

        live_style.assert_not_called()
        assert raw["custom"] == {}

    def test_world_cup_code_skips_the_provider_even_when_style_resolves(self):
        """Isolate the skip from the miss: club names, World Cup code.

        Keeps the coverage the old test actually had — the provider is not
        called because of the competition code, not because the lookup failed.
        """
        world_cup = CompetitionIdentity(
            code="wc", name="FIFA World Cup", sport=_FOOTBALL,
        )
        match = MatchIdentity(
            match_id="wc-style-club-names",
            season=SeasonIdentity(competition=world_cup, season_key="2026"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(
                code="RMA", name="Real Madrid CF", competition=world_cup,
            ),
            away=TeamIdentity(
                code="FCB", name="FC Bayern München", competition=world_cup,
            ),
            kickoff_utc=datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc),
        )
        raw = self._raw()
        with patch("app.services.football_live_style_service.get_live_style") as live_style:
            enrich_style_features(raw, match)

        live_style.assert_not_called()
        assert raw["custom"]["style_source"] == "static_table"


class TestStaticStyleFill:
    """Static-table branch, driven from the ``custom`` shape production has.

    Renamed from ``TestStaticStyleOverwrite``: every test here used to seed the
    removed form-share proxy into ``custom`` and assert what happened to it.
    Nothing in ``app/`` writes those keys before ``enrich_style_features`` runs,
    so the "overwrite" and "keeps proxy" framings described a state no
    production call can produce.
    """

    @staticmethod
    def _raw(**sections) -> dict:
        base = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        base.update(sections)
        return base

    def test_both_static_hits_fill_all_six_keys(self):
        match = _make_match("ucl-style-static")
        raw = self._raw(team={"form_home": 0.4, "form_away": 0.6})
        enrich_style_features(raw, match)

        from app.sports.football.football_style import stats_for_team

        home = stats_for_team("Real Madrid CF")
        away = stats_for_team("FC Bayern München")
        assert home is not None and away is not None
        assert raw["custom"]["possession_home"] == pytest.approx(home["possession_pct"])
        assert raw["custom"]["possession_away"] == pytest.approx(away["possession_pct"])
        assert raw["custom"]["shots_home"] == pytest.approx(home["shots_per90"])
        assert raw["custom"]["shots_away"] == pytest.approx(away["shots_per90"])
        assert raw["custom"]["ppda_home"] == pytest.approx(home["ppda"])
        assert raw["custom"]["ppda_away"] == pytest.approx(away["ppda"])
        assert raw["custom"]["style_source"] == "static_table"
        assert "possession_proxy" not in raw["custom"]
        # Form is present and differs from the static values, so a green result
        # means the numbers came from the table rather than from form.
        assert raw["custom"]["possession_home"] != pytest.approx(40.0)

    def test_one_side_unknown_writes_nothing(self):
        match = MatchIdentity(
            match_id="ucl-style-partial",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            away=TeamIdentity(code="ZZZ", name="Unknown Club XYZ", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        from app.sports.football.football_style import stats_for_team

        assert stats_for_team("Real Madrid CF") is not None  # premise: home hits
        assert stats_for_team("Unknown Club XYZ") is None    # premise: away misses

        raw = self._raw()
        enrich_style_features(raw, match)

        # A resolved home side must not be published alone: the factor is a
        # share, and a lone side would need the other one invented.
        assert raw["custom"] == {}

    def test_both_unknown_writes_nothing(self):
        match = MatchIdentity(
            match_id="ucl-style-none",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="AAA", name="NoSuchHome FC", competition=_UCL),
            away=TeamIdentity(code="BBB", name="NoSuchAway FC", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = self._raw()
        enrich_style_features(raw, match)

        assert raw["custom"] == {}


class TestStaticAltitudeFill:
    def test_static_fill_when_missing(self):
        # Home side must be in altitude table (Toluca)
        match = MatchIdentity(
            match_id="ucl-alt-fill",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="TOL", name="Toluca", competition=_UCL),
            away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        from app.sports.football.adapters._shared import enrich_altitude_features

        enrich_altitude_features(raw, match)

        from app.sports._shared.team_geo import altitude_m_for_team

        expected = altitude_m_for_team("Toluca")
        assert expected is not None
        assert raw["custom"]["venue_altitude_m"] == pytest.approx(float(expected))
        assert raw["custom"]["altitude_source"] == "static_table"

    def test_does_not_overwrite_existing(self):
        match = MatchIdentity(
            match_id="ucl-alt-keep",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="TOL", name="Toluca", competition=_UCL),
            away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {"venue_altitude_m": 1234.0},
        }
        from app.sports.football.adapters._shared import enrich_altitude_features

        enrich_altitude_features(raw, match)

        assert raw["custom"]["venue_altitude_m"] == pytest.approx(1234.0)
        assert raw["custom"].get("altitude_source") != "static_table"

    def test_unknown_home_no_static_altitude(self):
        match = MatchIdentity(
            match_id="ucl-alt-none",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="XXX", name="NoSuchHome FC", competition=_UCL),
            away=TeamIdentity(code="YYY", name="NoSuchAway FC", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        from app.sports.football.adapters._shared import enrich_altitude_features

        enrich_altitude_features(raw, match)

        assert "venue_altitude_m" not in raw["custom"]
        assert "altitude_source" not in raw["custom"]


class TestStaticWeatherFill:
    def test_static_fill_when_missing(self):
        match = MatchIdentity(
            match_id="ucl-wx-fill",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="ARS", name="Arsenal", competition=_UCL),
            away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        from app.sports.football.adapters._shared import enrich_weather_features
        from app.sports.football.football_weather import climate_for_home

        enrich_weather_features(raw, match)
        expected = climate_for_home("Arsenal", 9)
        assert expected is not None
        assert raw["environment"]["weather_temp_c"] == pytest.approx(float(expected["temp_c"]))
        assert raw["environment"]["weather_condition"] == expected["condition"]
        assert raw["custom"]["weather_source"] == "static_climate"
        assert raw["custom"]["weather_temp_c"] == pytest.approx(float(expected["temp_c"]))
        assert raw["custom"]["weather_condition"] == expected["condition"]

    def test_does_not_overwrite_existing_temp(self):
        match = MatchIdentity(
            match_id="ucl-wx-keep",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="ARS", name="Arsenal", competition=_UCL),
            away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {"weather_temp_c": 21.5, "weather_condition": "clear"},
            "custom": {},
        }
        from app.sports.football.adapters._shared import enrich_weather_features

        enrich_weather_features(raw, match)
        assert raw["environment"]["weather_temp_c"] == pytest.approx(21.5)
        assert raw["environment"]["weather_condition"] == "clear"
        assert raw["custom"].get("weather_source") != "static_climate"

    def test_unknown_home_no_static_weather(self):
        match = MatchIdentity(
            match_id="ucl-wx-none",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="XXX", name="NoSuchHome FC", competition=_UCL),
            away=TeamIdentity(code="YYY", name="NoSuchAway FC", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        from app.sports.football.adapters._shared import enrich_weather_features

        enrich_weather_features(raw, match)
        assert raw["environment"].get("weather_temp_c") is None
        assert raw["environment"].get("weather_condition") is None
        assert "weather_source" not in raw["custom"]


_NOW = datetime(2025, 9, 16, 12, 0, tzinfo=timezone.utc)


def _wx_match(match_id, home_name="Arsenal", kickoff=None):
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
        stage="group_stage",
        round=None,
        home=TeamIdentity(code="ARS", name=home_name, competition=_UCL),
        away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
        kickoff_utc=kickoff if kickoff is not None else (_NOW + timedelta(hours=24)),
    )


def _wx_raw(environment=None):
    return {
        "team": {},
        "general": {},
        "market": {},
        "player": {},
        "environment": environment if environment is not None else {},
        "custom": {},
    }


class TestLiveWeatherFill:
    def setup_method(self):
        from app.sports.football.football_weather import _clear_live_weather_cache

        _clear_live_weather_cache()

    def test_live_forecast_used_when_configured_and_env_absent(self):
        match = _wx_match("ucl-wx-live")
        raw = _wx_raw()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"current_weather": {"temperature": 17.4, "weathercode": 61}}
        from app.sports.football.adapters._shared import enrich_weather_features

        with (
            patch("app.sports.football.football_weather.httpx.get", return_value=resp) as mock_get,
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
        ):
            enrich_weather_features(raw, match)
        assert mock_get.call_count == 1
        assert raw["environment"]["weather_temp_c"] == pytest.approx(17.4)
        assert raw["environment"]["weather_condition"] == "rain"
        assert raw["custom"]["weather_source"] == "live_forecast"
        assert raw["custom"]["weather_temp_c"] == pytest.approx(17.4)

    def test_env_zero_temp_beats_live_forecast(self):
        match = _wx_match("ucl-wx-zero")
        raw = _wx_raw(environment={"weather_temp_c": 0.0})
        from app.sports.football.adapters._shared import enrich_weather_features

        with (
            patch("app.sports.football.football_weather.httpx.get") as mock_get,
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
        ):
            enrich_weather_features(raw, match)
        assert raw["environment"]["weather_temp_c"] == pytest.approx(0.0)
        assert raw["custom"].get("weather_source") != "live_forecast"
        mock_get.assert_not_called()

    def test_live_failure_falls_back_to_static_climate(self):
        import httpx as _httpx

        match = _wx_match("ucl-wx-fail")
        raw = _wx_raw()
        from app.sports.football.adapters._shared import enrich_weather_features
        from app.sports.football.football_weather import climate_for_home

        with (
            patch(
                "app.sports.football.football_weather.httpx.get",
                side_effect=_httpx.ConnectError("boom"),
            ),
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
        ):
            enrich_weather_features(raw, match)
        expected = climate_for_home("Arsenal", 9)
        assert expected is not None
        assert raw["environment"]["weather_temp_c"] == pytest.approx(float(expected["temp_c"]))
        assert raw["custom"]["weather_source"] == "static_climate"

    def test_beyond_horizon_skips_live_call(self):
        match = _wx_match("ucl-wx-far", kickoff=_NOW + timedelta(days=14))
        raw = _wx_raw()
        from app.sports.football.adapters._shared import enrich_weather_features
        from app.sports.football.football_weather import climate_for_home

        with (
            patch("app.sports.football.football_weather.httpx.get") as mock_get,
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
        ):
            enrich_weather_features(raw, match)
        mock_get.assert_not_called()
        expected = climate_for_home("Arsenal", 9)
        assert raw["custom"]["weather_source"] == "static_climate"
        assert raw["environment"]["weather_temp_c"] == pytest.approx(float(expected["temp_c"]))

    def test_missing_config_no_http_static_climate(self):
        match = _wx_match("ucl-wx-nocfg")
        raw = _wx_raw()
        from app.sports.football.adapters._shared import enrich_weather_features
        from app.sports.football.football_weather import climate_for_home

        with (
            patch("app.sports.football.football_weather.httpx.get") as mock_get,
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", ""),
        ):
            enrich_weather_features(raw, match)
        mock_get.assert_not_called()
        expected = climate_for_home("Arsenal", 9)
        assert raw["custom"]["weather_source"] == "static_climate"
        assert raw["environment"]["weather_temp_c"] == pytest.approx(float(expected["temp_c"]))

    def test_single_live_source_reports_provenance(self):
        match = _wx_match("ucl-wx-one")
        raw = _wx_raw()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"current_weather": {"temperature": 17.4, "weathercode": 61}}
        from app.sports.football.adapters._shared import enrich_weather_features

        with (
            patch("app.sports.football.football_weather.httpx.get", return_value=resp),
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED", False),
        ):
            enrich_weather_features(raw, match)
        assert raw["custom"]["weather_source"] == "live_forecast"
        assert raw["custom"]["weather_source_count"] == pytest.approx(1.0)
        assert raw["custom"]["weather_agreement"] == "single"

    def test_two_live_sources_propagate_consensus_provenance(self):
        from app.services.football_live_weather_service import LiveWeatherResult

        match = _wx_match("ucl-wx-two")
        raw = _wx_raw()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"current_weather": {"temperature": 17.0, "weathercode": 61}}
        from app.sports.football.adapters._shared import enrich_weather_features

        contexts = (
            patch("app.sports.football.football_weather.httpx.get", return_value=resp),
            patch("app.sports.football.football_weather._utcnow", return_value=_NOW),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_URL", "https://wx.example/forecast"),
            patch(
                "app.services.football_live_weather_service.get_secondary_weather",
                return_value=LiveWeatherResult(available=True, temp_c=19.0, condition="rain"),
            ),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED", True),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_SECONDARY_URL", "https://wx2.example/point"),
            patch("app.sports.football.football_weather.settings.FOOTBALL_LIVE_WEATHER_SECONDARY_API_KEY", "secret-key"),
        )
        with ExitStack() as stack:
            for ctx in contexts:
                stack.enter_context(ctx)
            enrich_weather_features(raw, match)
        assert raw["environment"]["weather_temp_c"] == pytest.approx(18.0)
        assert raw["custom"]["weather_source"] == "live_forecast"
        assert raw["custom"]["weather_source_count"] == pytest.approx(2.0)
        assert raw["custom"]["weather_agreement"] == "agree"


class TestZeroAltitudePreserved:
    """venue_altitude_m=0.0 is a valid sea-level value, not missing."""

    def test_zero_altitude_not_treated_as_missing(self):
        match = MatchIdentity(
            match_id="ucl-alt-zero",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="TOL", name="Toluca", competition=_UCL),
            away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {"venue_altitude_m": 0.0},
        }
        from app.sports.football.adapters._shared import enrich_altitude_features

        enrich_altitude_features(raw, match)

        # Must keep 0.0, NOT overwrite with Toluca static altitude (~2667)
        assert raw["custom"]["venue_altitude_m"] == pytest.approx(0.0)
        assert raw["custom"].get("altitude_source") != "static_table"

    def test_zero_altitude_from_env_not_treated_as_missing(self):
        match = MatchIdentity(
            match_id="ucl-alt-zero-env",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="TOL", name="Toluca", competition=_UCL),
            away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {"altitude_m": 0.0},
            "custom": {},
        }
        from app.sports.football.adapters._shared import enrich_altitude_features

        enrich_altitude_features(raw, match)

        assert raw["custom"]["venue_altitude_m"] == pytest.approx(0.0)
        assert raw["custom"].get("altitude_source") != "static_table"


class TestZeroWeatherTempPreserved:
    """weather_temp_c=0.0 is a valid temperature, not missing."""

    def test_zero_temp_no_condition_not_overwritten(self):
        match = MatchIdentity(
            match_id="ucl-wx-zero",
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="ARS", name="Arsenal", competition=_UCL),
            away=TeamIdentity(code="RMA", name="Real Madrid CF", competition=_UCL),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {"weather_temp_c": 0.0},
            "custom": {},
        }
        from app.sports.football.adapters._shared import enrich_weather_features

        enrich_weather_features(raw, match)

        # Must keep 0.0, NOT overwrite with static climate
        assert raw["environment"]["weather_temp_c"] == pytest.approx(0.0)
        assert raw["custom"].get("weather_source") != "static_climate"


class TestWhitespaceRefereeCreatesNothing:
    """Whitespace-only referee input must not create empty referee_name."""

    def test_whitespace_referee_no_fields_created(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        raw = {"custom": {}, "environment": {"referee": "   "}}
        enrich_referee_features(raw, _make_match())

        assert "referee_name" not in raw["custom"]
        assert "referee_home_bias" not in raw["custom"]
        assert "referee_home_win_rate" not in raw["custom"]
        assert "referee_source" not in raw["custom"]

    def test_tab_newline_referee_no_fields_created(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        raw = {"custom": {}, "environment": {"referee": "\t\n  "}}
        enrich_referee_features(raw, _make_match())

        assert "referee_name" not in raw["custom"]
        assert "referee_home_bias" not in raw["custom"]
        assert "referee_source" not in raw["custom"]


class TestMarketTotalsWiring:
    """P1-O1 真盘口: the football adapters must actually reach the provider.

    The engine reads the line out of ``custom``, so a provider nobody calls is a
    capability that exists and is unreachable. ``fetch_elo_and_odds`` is the
    composition root for the EPL/UCL/league adapters; the World Cup adapter
    builds ``custom`` itself and is covered separately.
    """

    @patch("app.sports.football.adapters._shared.get_club_elo")
    @patch("app.services.odds_cache_service.get_cached_odds", new_callable=AsyncMock)
    def _fetch(self, mock_odds, mock_club, result=None, **kwargs):
        mock_club.return_value = {"elo_rating": 1900.0, "source": "clubelo"}
        mock_odds.return_value = None
        with patch(
            "app.services.market_totals_service.get_market_total",
            return_value=result,
            **kwargs,
        ) as provider:
            raw = fetch_elo_and_odds(_make_match(), elo_scope="club")
        return raw, provider

    def test_available_line_reaches_custom(self):
        from app.services.market_totals_service import MarketTotal

        raw, provider = self._fetch(
            result=MarketTotal(
                available=True,
                total={"total_line": 3.5, "market_p_over": 0.51},
            ),
        )
        assert raw["custom"]["market_total_line"] == pytest.approx(3.5)
        assert raw["custom"]["market_total_p_over"] == pytest.approx(0.51)
        # The kickoff date and the fixture's own team names identify the row; no
        # provider fixture ID is assumed to be compatible.
        assert provider.call_args.args == (
            "football", "2025-09-16", "Real Madrid CF", "FC Bayern München",
        )

    def test_unavailable_provider_writes_nothing(self):
        from app.services.market_totals_service import MarketTotal

        raw, _ = self._fetch(result=MarketTotal(available=False))
        assert "market_total_line" not in raw["custom"]
        # The rest of the enrichment still ran.
        assert raw["team"]["elo_home"] == pytest.approx(1900.0)

    def test_provider_exception_does_not_break_enrichment(self):
        raw, _ = self._fetch(side_effect=RuntimeError("provider down"))
        assert "market_total_line" not in raw["custom"]
        assert raw["team"]["elo_home"] == pytest.approx(1900.0)

    def test_world_cup_adapter_is_wired_too(self):
        from app.services.market_totals_service import MarketTotal
        from app.sports.football.adapters.world_cup_adapter import WorldCupAdapter

        match = _make_match(match_id="wc-1")
        with patch(
            "app.services.market_totals_service.get_market_total",
            return_value=MarketTotal(
                available=True, total={"total_line": 2.75, "market_p_over": 0.49},
            ),
        ):
            custom = WorldCupAdapter()._build_custom(match)

        assert custom["market_total_line"] == pytest.approx(2.75)
        assert custom["market_total_p_over"] == pytest.approx(0.49)


class TestFormIsNotPossession:
    """P1-F6: form must not be published under the possession keys.

    ``feature_builder`` hands ``team_raw["form_home"]`` straight to the engine's
    form factor, so a possession value derived from form is the same evidence
    voting a second time under a different name -- in the fused weight, in
    ``data_completeness``, and in ``factor_agreement``. These tests drive the
    real entry points rather than the removed helper, because the defect was a
    write nobody read the marker of, not a helper anybody called.
    """

    @staticmethod
    def _fetch(match, *, form=(0.9, 0.1), **odds_kwargs):
        """Drive the real composition root with form resolved and style absent.

        Form has to actually be present for this to be a test of anything: the
        removed proxy read ``raw["team"]["form_home"]`` and skipped silently when
        it was None, so a fixture whose form never resolves cannot observe the
        defect at all. The situational enrichment is stubbed to seed form rather
        than seeded through the database, because form's provenance is not what
        is under test here.
        """
        def _seed_form(raw, _match):
            if form is not None:
                raw.setdefault("team", {})
                raw["team"]["form_home"], raw["team"]["form_away"] = form

        with patch(
            "app.sports.football.adapters._shared.get_club_elo",
            return_value={"elo_rating": 1900.0, "source": "clubelo"},
        ), patch(
            "app.services.odds_cache_service.get_cached_odds",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.services.football_live_style_service.get_live_style",
            return_value=LiveStyleResult(False),
        ), patch(
            "app.sports.football.adapters._shared.enrich_situational_features",
            side_effect=_seed_form,
        ):
            return fetch_elo_and_odds(match, elo_scope="club", **odds_kwargs)

    @staticmethod
    def _unknown_club_match(match_id: str) -> MatchIdentity:
        """A fixture both style sources miss, so nothing overwrites possession.

        The default ``_make_match`` uses Real Madrid and Bayern, which the static
        table does carry -- their possession is a real (if coarse) static reading
        and ``enrich_style_features`` pops the proxy marker on the way past, so a
        table-hit fixture destroys the evidence these tests need.
        """
        from app.sports.football.football_style import stats_for_team

        assert stats_for_team("Unknown Club XYZ") is None  # premise
        assert stats_for_team("Another Unknown FC") is None
        return MatchIdentity(
            match_id=match_id,
            season=SeasonIdentity(competition=_UCL, season_key="2025-26"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(
                code="UNK", name="Unknown Club XYZ", competition=_UCL,
            ),
            away=TeamIdentity(
                code="ANO", name="Another Unknown FC", competition=_UCL,
            ),
            kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
        )

    def test_form_alone_never_produces_a_possession_key(self):
        """No live style and no static row must leave possession unset."""
        raw = self._fetch(self._unknown_club_match("ucl-form-only"))
        assert raw["custom"].get("style_source") is None  # premise: no style data
        assert "possession_home" not in raw["custom"]
        assert "possession_away" not in raw["custom"]
        assert "possession_proxy" not in raw["custom"]

    def test_enrich_style_features_is_the_only_writer_of_possession(self):
        """Source-level guard: no second producer may reappear.

        The removed proxy was a *write* whose marker nobody read, so no
        behavioural test could have caught it being added -- the engine happily
        consumes whatever is under the key. Pinning the set of writers is what
        makes the next such addition visible.
        """
        import ast
        import pathlib

        source = pathlib.Path("app/sports/football/adapters/_shared.py").read_text(
            encoding="utf-8",
        )
        tree = ast.parse(source)
        writers: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Assign):
                    continue
                for target in inner.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value
                        in {"possession_home", "possession_away"}
                    ):
                        writers.add(node.name)
        assert writers == {"enrich_style_features"}, (
            f"possession is written by {sorted(writers)}; only "
            "enrich_style_features may produce it, from live or static style "
            "stats. Deriving it from another factor's input double counts."
        )

    def test_world_cup_adapter_never_reaches_the_style_enricher(self):
        """Pins the corrected blast radius of the removed proxy.

        The P1-F6 write-up first claimed the World Cup was the permanently
        affected track, on the reasoning that ``is_world_cup`` skips the live
        provider and national teams miss the static table. Both halves are true
        of ``enrich_style_features``, and irrelevant: ``WorldCupAdapter`` builds
        its own raw dict in ``fetch_all_data`` and calls no enricher at all, so
        no World Cup fixture ever reached the proxy. The affected tracks are the
        callers of ``fetch_elo_and_odds`` -- epl, ucl, laliga, bundesliga,
        seriea, ligue1.

        Asserted against the real adapter rather than by reading the source, so
        wiring the World Cup through the shared path later would fail here
        instead of silently re-widening the radius.
        """
        from app.sports.football.adapters.world_cup_adapter import WorldCupAdapter

        world_cup = CompetitionIdentity(
            code="world_cup", name="FIFA World Cup", sport=_FOOTBALL,
        )
        match = MatchIdentity(
            match_id="wc-reachability",
            season=SeasonIdentity(competition=world_cup, season_key="2026"),
            stage="group_stage",
            round=None,
            home=TeamIdentity(code="BRA", name="Brazil", competition=world_cup),
            away=TeamIdentity(code="ARG", name="Argentina", competition=world_cup),
            kickoff_utc=datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc),
        )

        async def _elo(_team):
            return {"elo_rating": 1850.0}

        async def _odds(_home, _away):
            return None

        with patch(
            "app.services.elo_ratings_service.get_elo_rating", side_effect=_elo,
        ), patch(
            "app.services.odds_cache_service.get_cached_odds", side_effect=_odds,
        ), patch(
            "app.sports.football.adapters._shared.enrich_style_features",
        ) as style, patch(
            "app.sports.football.adapters._shared.enrich_situational_features",
        ) as situational:
            raw = WorldCupAdapter().fetch_all_data(match)

        style.assert_not_called()
        situational.assert_not_called()
        assert "possession_home" not in raw.get("custom", {})

    def test_form_evidence_is_not_counted_twice(self):
        """Confidence must not rise just because form was copied to possession.

        Pins the structural claim: a possession value derived from form adds an
        available factor and an agreeing vote without adding information. The
        assertion is an inequality rather than a fixed number so it survives any
        future re-weighting of the confidence blend.
        """
        from app.kernel.engines.confidence import compute_confidence

        probs = {"home_win": 0.43, "draw": 0.27, "away_win": 0.30}
        base_flags = [True, True, True, False, False]
        base_votes = ["home_win", "home_win", "home_win", None, None]

        honest = compute_confidence(
            probs,
            available_flags=[*base_flags, False],
            predicted_outcomes=[*base_votes, None],
            data_quality="real",
        )
        with_proxy = compute_confidence(
            probs,
            available_flags=[*base_flags, True],
            predicted_outcomes=[*base_votes, "home_win"],
            data_quality="real",
        )
        # The proxy inflated confidence; the honest answer is the lower one, and
        # that is what the adapter now produces for a form-only fixture.
        assert with_proxy > honest

        # An unknown-club fixture is required here. On a table-hit fixture the
        # style enrichment pops the marker and overwrites possession, so the
        # proxy would leave no trace and this assertion could not fail.
        raw = self._fetch(self._unknown_club_match("ucl-no-double-count"))
        assert "possession_proxy" not in raw["custom"]
        assert "possession_home" not in raw["custom"]
