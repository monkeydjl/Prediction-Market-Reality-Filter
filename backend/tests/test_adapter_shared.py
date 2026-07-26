# backend/tests/test_adapter_shared.py
"""Tests for _shared.py — shared adapter utility functions."""
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone
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

    def test_environment_name_only(self):
        from app.sports.football.adapters._shared import enrich_referee_features

        raw = {"custom": {}, "environment": {"referee": "John Smith"}}
        enrich_referee_features(raw, _make_match())
        assert raw["custom"]["referee_name"] == "John Smith"
        assert raw["custom"].get("referee_home_win_rate") is None

    def test_static_map_bias(self, monkeypatch):
        import app.sports.football.adapters._shared as sh
        monkeypatch.setitem(sh._REFEREE_HOME_BIAS, "jane doe", 0.08)
        raw = {"custom": {}, "environment": {"referee": "Jane Doe"}}
        sh.enrich_referee_features(raw, _make_match())
        assert raw["custom"]["referee_home_bias"] == 0.08
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

