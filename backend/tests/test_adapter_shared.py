# backend/tests/test_adapter_shared.py
"""Tests for _shared.py — shared adapter utility functions."""
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
import asyncio
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
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
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=kernel_h2h,
        ) as mock_kh:
            enrich_situational_features(raw, match)

        mock_kh.assert_called()
        assert raw["team"]["h2h_home_win_rate"] == pytest.approx(0.5)
        assert raw["team"]["h2h_draw_rate"] == pytest.approx(0.5)

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
            create=True,
        ):
            enrich_situational_features(raw, match)

        assert raw["player"]["injury_impact_home"] == pytest.approx(0.35)
        assert raw["player"]["injury_impact_away"] == pytest.approx(0.26)
        assert raw["custom"]["injury_impact_home"] == pytest.approx(0.35)
        assert raw["custom"]["injury_impact_away"] == pytest.approx(0.26)

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
            create=True,
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
            create=True,
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
            create=True,
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


class TestStaticStyleOverwrite:
    def test_both_static_hits_overwrite_proxy(self):
        match = _make_match("ucl-style-static")
        raw = {
            "team": {"form_home": 0.4, "form_away": 0.6},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {
                # Simulate form_share proxy already applied
                "possession_home": 40.0,
                "possession_away": 60.0,
                "possession_proxy": "form_share",
            },
        }
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
        # Must not remain form proxy values
        assert raw["custom"]["possession_home"] != pytest.approx(40.0)

    def test_one_side_unknown_keeps_proxy(self):
        match = MatchIdentity(
            match_id="ucl-style-partial",
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
            "custom": {
                "possession_home": 55.0,
                "possession_away": 45.0,
                "possession_proxy": "form_share",
            },
        }
        enrich_style_features(raw, match)

        assert raw["custom"].get("possession_home") == pytest.approx(55.0)
        assert raw["custom"].get("possession_away") == pytest.approx(45.0)
        assert raw["custom"].get("possession_proxy") == "form_share"
        assert "style_source" not in raw["custom"]
        assert "shots_home" not in raw["custom"]
        assert "ppda_home" not in raw["custom"]

    def test_both_unknown_no_static_source(self):
        match = MatchIdentity(
            match_id="ucl-style-none",
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
            "custom": {
                "possession_home": 50.0,
                "possession_away": 50.0,
                "possession_proxy": "form_share",
            },
        }
        enrich_style_features(raw, match)

        assert raw["custom"].get("possession_home") == pytest.approx(50.0)
        assert raw["custom"].get("possession_proxy") == "form_share"
        assert "style_source" not in raw["custom"]
        assert "shots_home" not in raw["custom"]
        assert "ppda_home" not in raw["custom"]


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

