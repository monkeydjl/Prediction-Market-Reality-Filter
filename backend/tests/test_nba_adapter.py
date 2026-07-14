# backend/tests/test_nba_adapter.py
"""Tests for NBAAdapter — DataAdapter Protocol implementation."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter
from app.sports.basketball.nba_adapter import NBAAdapter, parse_nba_game


_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)


def _make_match(match_id="nba-123") -> MatchIdentity:
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_NBA, season_key="2024-25"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="BOS", name="Boston Celtics", competition=_NBA),
        away=TeamIdentity(code="LAL", name="Los Angeles Lakers", competition=_NBA),
        kickoff_utc=datetime(2024, 12, 25, tzinfo=timezone.utc),
    )


def _make_fixture(match_id="nba-123", home="Boston Celtics", away="Los Angeles Lakers"):
    """Create a mock KernelMatchFixture row."""
    fixture = MagicMock()
    fixture.match_id = match_id
    fixture.competition = "nba"
    fixture.season = "2024-25"
    fixture.home_team = home
    fixture.away_team = away
    fixture.kickoff_utc = datetime(2024, 12, 25, tzinfo=timezone.utc)
    fixture.stage = "regular_season"
    fixture.status = "scheduled"
    fixture.venue = "TD Garden"
    fixture.home_score = None
    fixture.away_score = None
    return fixture


class TestNBAAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = NBAAdapter()
        assert isinstance(adapter, DataAdapter)


class TestParseNbaGame:
    def test_parses_regular_season_game(self):
        """parse_nba_game maps API fields to internal fixture format."""
        raw = {
            "id": 123,
            "season": 2023,
            "postseason": False,
            "home_team": {"id": 1, "full_name": "Boston Celtics"},
            "visitor_team": {"id": 2, "full_name": "Los Angeles Lakers"},
            "date": "2023-12-25T00:00:00Z",
            "home_team_score": 114,
            "visitor_team_score": 108,
            "status": "Final",
        }
        parsed = parse_nba_game(raw)
        assert parsed["match_id"] == "nba-123"
        assert parsed["home_team"] == "Boston Celtics"
        assert parsed["away_team"] == "Los Angeles Lakers"
        assert parsed["stage"] == "regular_season"
        assert parsed["status"] == "finished"

    def test_parses_playoff_game(self):
        """postseason=True maps to 'playoff' stage."""
        raw = {
            "id": 456,
            "season": 2023,
            "postseason": True,
            "home_team": {"id": 1, "full_name": "Boston Celtics"},
            "visitor_team": {"id": 2, "full_name": "Los Angeles Lakers"},
            "date": "2024-04-15T00:00:00Z",
            "home_team_score": 0,
            "visitor_team_score": 0,
            "status": "Scheduled",
        }
        parsed = parse_nba_game(raw)
        assert parsed["match_id"] == "nba-456"
        assert parsed["stage"] == "playoff"
        assert parsed["status"] == "scheduled"


class TestNBAAdapterGetMatchIdentity:
    @patch("app.sports.basketball.nba_adapter.query_fixture")
    def test_returns_identity_when_fixture_found(self, mock_query):
        mock_query.return_value = _make_fixture()
        adapter = NBAAdapter()
        identity = adapter.get_match_identity("nba-123")
        assert identity.match_id == "nba-123"
        assert identity.home.name == "Boston Celtics"
        assert identity.away.name == "Los Angeles Lakers"
        assert identity.season.competition.code == "nba"

    @patch("app.sports.basketball.nba_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = NBAAdapter()
        identity = adapter.get_match_identity("nba-nonexistent")
        assert identity.match_id == "nba-nonexistent"
        assert identity.home.name == "Home"


class TestNBAAdapterSyncSchedule:
    @patch("app.sports.basketball.nba_adapter.save_fixture")
    @patch("app.sports.basketball.nba_adapter.parse_nba_game")
    @patch("app.sports.basketball.nba_adapter.fetch_nba_games")
    @patch("app.sports.basketball.nba_adapter.config")
    def test_sync_returns_count_when_api_key_present(
        self, mock_config, mock_fetch, mock_parse, mock_save
    ):
        mock_config.settings.BALLDONTLIE_API_KEY = "test-key"
        mock_fetch.return_value = [{"id": 1}, {"id": 2}]
        mock_parse.return_value = {"match_id": "nba-1"}
        adapter = NBAAdapter()
        count = adapter.sync_schedule()
        assert count == 2

    @patch("app.sports.basketball.nba_adapter.config")
    def test_sync_returns_zero_when_api_key_empty(self, mock_config):
        mock_config.settings.BALLDONTLIE_API_KEY = ""
        adapter = NBAAdapter()
        count = adapter.sync_schedule()
        assert count == 0


class TestNBAAdapterFetchAllData:
    @patch("app.sports.basketball.nba_adapter.query_fixture")
    def test_fetch_all_data_returns_elo_from_db(self, mock_query):
        """fetch_all_data reads Elo from kernel_elo_ratings table."""
        # Setup: fixture exists in DB with team names
        mock_query.return_value = _make_fixture()

        adapter = NBAAdapter()
        # Mock the internal DB-touching methods to isolate from real DB
        # (avoids creating backend/kernel_predictions.db as a side effect)
        with patch.object(adapter, "_fetch_elo_ratings", return_value={"Boston Celtics": 1650.0, "Los Angeles Lakers": 1520.0}), \
             patch.object(adapter, "_compute_form", return_value=0.6), \
             patch.object(adapter, "_compute_rest_days", return_value=2):
            match = _make_match()
            raw = adapter.fetch_all_data(match)
            assert raw["team"]["elo_home"] == 1650.0
            assert raw["team"]["elo_away"] == 1520.0
            assert raw["team"]["form_home"] == 0.6
            assert raw["general"]["rest_days_home"] == 2
            assert raw["environment"]["is_home_advantage"] is True


class TestNBAAdapterFetchOutcome:
    @patch("app.sports.basketball.nba_adapter.build_match_outcome")
    @patch("app.sports.basketball.nba_adapter.query_result")
    def test_fetch_outcome_returns_outcome(self, mock_query, mock_build):
        mock_query.return_value = MagicMock()
        mock_build.return_value = MatchOutcome(
            match_id="nba-123",
            home_score=114, away_score=108,
            outcome="home_win",
            finished_at=datetime(2024, 12, 25, tzinfo=timezone.utc),
        )
        adapter = NBAAdapter()
        result = adapter.fetch_outcome("nba-123")
        assert result is not None
        assert result.home_score == 114
        assert result.outcome == "home_win"
