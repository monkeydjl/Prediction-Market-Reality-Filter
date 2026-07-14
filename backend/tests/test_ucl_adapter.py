# backend/tests/test_ucl_adapter.py
"""Tests for UCLAdapter — DataAdapter Protocol implementation for UCL."""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter, ScheduleFilter, RawMatchData
from app.sports.football.adapters.ucl_adapter import UCLAdapter


def _make_match(match_id="ucl-537327") -> MatchIdentity:
    football = SportIdentity(code="football", name="Football")
    ucl = CompetitionIdentity(code="ucl", name="UEFA Champions League", sport=football)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=ucl, season_key="2025-26"),
        stage="group_stage",
        round=None,
        home=TeamIdentity(code="RMA", name="Real Madrid CF", competition=ucl),
        away=TeamIdentity(code="FCB", name="FC Bayern München", competition=ucl),
        kickoff_utc=datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc),
    )


class TestUCLAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = UCLAdapter()
        assert isinstance(adapter, DataAdapter)


class TestGetMatchIdentity:
    @patch("app.sports.football.adapters.ucl_adapter.query_fixture")
    def test_returns_identity_when_fixture_found(self, mock_query):
        fixture = MagicMock()
        fixture.match_id = "ucl-537327"
        fixture.home_team = "Real Madrid CF"
        fixture.away_team = "FC Bayern München"
        fixture.stage = "group_stage"
        fixture.kickoff_utc = datetime(2025, 9, 16, 20, 0, tzinfo=timezone.utc)
        mock_query.return_value = fixture

        adapter = UCLAdapter()
        identity = adapter.get_match_identity("ucl-537327")
        assert identity.match_id == "ucl-537327"
        assert identity.home.name == "Real Madrid CF"
        assert identity.away.name == "FC Bayern München"
        assert identity.season.competition.code == "ucl"
        assert identity.stage == "group_stage"

    @patch("app.sports.football.adapters.ucl_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = UCLAdapter()
        identity = adapter.get_match_identity("ucl-nonexistent")
        assert identity.match_id == "ucl-nonexistent"
        assert identity.home.name == "Home"
        assert identity.season.competition.code == "ucl"


class TestFetchAllData:
    @patch("app.sports.football.adapters.ucl_adapter.fetch_elo_and_odds")
    def test_fetch_all_data_uses_club_elo(self, mock_fetch):
        mock_fetch.return_value = {
            "team": {"elo_home": 1955.12, "elo_away": 1940.33},
            "market": {"odds_home": 1.5},
            "player": {}, "environment": {}, "general": {},
        }
        adapter = UCLAdapter()
        match = _make_match()
        raw = adapter.fetch_all_data(match)

        assert raw["team"]["elo_home"] == 1955.12
        # Verify fetch_elo_and_odds was called with club scope
        mock_fetch.assert_called_once()
        call_kwargs = mock_fetch.call_args.kwargs
        assert call_kwargs["elo_scope"] == "club"
        assert call_kwargs["team_aliases"] is not None  # UCL has aliases


class TestFetchOutcome:
    @patch("app.sports.football.adapters.ucl_adapter.query_result")
    @patch("app.sports.football.adapters.ucl_adapter.build_match_outcome")
    def test_fetch_outcome_returns_outcome(self, mock_build, mock_query):
        mock_query.return_value = MagicMock()
        expected = MatchOutcome(
            match_id="ucl-537327", home_score=3, away_score=1,
            outcome="home_win",
            finished_at=datetime(2025, 9, 16, 22, 0, tzinfo=timezone.utc),
        )
        mock_build.return_value = expected

        adapter = UCLAdapter()
        result = adapter.fetch_outcome("ucl-537327")
        assert result == expected

    @patch("app.sports.football.adapters.ucl_adapter.query_result")
    @patch("app.sports.football.adapters.ucl_adapter.build_match_outcome")
    def test_fetch_outcome_returns_none(self, mock_build, mock_query):
        mock_query.return_value = None
        mock_build.return_value = None
        adapter = UCLAdapter()
        assert adapter.fetch_outcome("ucl-nonexistent") is None


class TestSyncSchedule:
    @patch("app.sports.football.adapters.ucl_adapter.save_fixture")
    @patch("app.sports.football.adapters.ucl_adapter.parse_fixture")
    @patch("app.sports.football.adapters.ucl_adapter.fetch_competition_fixtures")
    def test_sync_saves_fixtures(self, mock_fetch, mock_parse, mock_save):
        mock_fetch.return_value = [{"id": 1}, {"id": 2}]
        mock_parse.side_effect = [
            {"match_id": "ucl-1", "home_team": "A", "away_team": "B",
             "kickoff_utc": datetime(2025, 9, 16), "stage": "group_stage",
             "status": "scheduled", "venue": "X"},
            {"match_id": "ucl-2", "home_team": "C", "away_team": "D",
             "kickoff_utc": datetime(2025, 9, 17), "stage": "group_stage",
             "status": "scheduled", "venue": "Y"},
        ]
        adapter = UCLAdapter()
        count = adapter.sync_schedule()
        assert count == 2
        assert mock_save.call_count == 2

    @patch("app.sports.football.adapters.ucl_adapter.fetch_competition_fixtures")
    def test_sync_failure_returns_zero(self, mock_fetch):
        mock_fetch.side_effect = Exception("API error")
        adapter = UCLAdapter()
        assert adapter.sync_schedule() == 0


class TestStubMethods:
    def test_fetch_team_data_returns_empty(self):
        assert UCLAdapter().fetch_team_data(MagicMock()) == {}

    def test_fetch_player_data_returns_empty(self):
        assert UCLAdapter().fetch_player_data(MagicMock()) == {}

    def test_fetch_market_data_returns_empty(self):
        assert UCLAdapter().fetch_market_data(MagicMock()) == {}
