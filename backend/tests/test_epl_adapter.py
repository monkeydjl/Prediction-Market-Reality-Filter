# backend/tests/test_epl_adapter.py
"""Tests for EPLAdapter — DataAdapter Protocol implementation for EPL."""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter
from app.sports.football.adapters.epl_adapter import EPLAdapter


def _make_match(match_id="epl-123456") -> MatchIdentity:
    football = SportIdentity(code="football", name="Football")
    epl = CompetitionIdentity(code="epl", name="English Premier League", sport=football)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=epl, season_key="2025-26"),
        stage="regular_season",
        round=None,
        home=TeamIdentity(code="ARS", name="Arsenal FC", competition=epl),
        away=TeamIdentity(code="CHE", name="Chelsea FC", competition=epl),
        kickoff_utc=datetime(2025, 8, 16, 15, 0, tzinfo=timezone.utc),
    )


class TestEPLAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = EPLAdapter()
        assert isinstance(adapter, DataAdapter)


class TestGetMatchIdentity:
    @patch("app.sports.football.adapters.epl_adapter.query_fixture")
    def test_returns_identity_with_regular_season(self, mock_query):
        fixture = MagicMock()
        fixture.match_id = "epl-123456"
        fixture.home_team = "Arsenal FC"
        fixture.away_team = "Chelsea FC"
        fixture.stage = "regular_season"
        fixture.kickoff_utc = datetime(2025, 8, 16, 15, 0, tzinfo=timezone.utc)
        mock_query.return_value = fixture

        adapter = EPLAdapter()
        identity = adapter.get_match_identity("epl-123456")
        assert identity.match_id == "epl-123456"
        assert identity.home.name == "Arsenal FC"
        assert identity.stage == "regular_season"
        assert identity.season.competition.code == "epl"

    @patch("app.sports.football.adapters.epl_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = EPLAdapter()
        identity = adapter.get_match_identity("epl-nonexistent")
        assert identity.match_id == "epl-nonexistent"
        assert identity.stage == "regular_season"
        assert identity.season.competition.code == "epl"


class TestFetchAllData:
    @patch("app.sports.football.adapters.epl_adapter.fetch_elo_and_odds")
    def test_fetch_all_data_uses_club_elo(self, mock_fetch):
        mock_fetch.return_value = {
            "team": {"elo_home": 2063.76, "elo_away": 1680.0},
            "market": {},
            "player": {}, "environment": {}, "general": {},
        }
        adapter = EPLAdapter()
        match = _make_match()
        raw = adapter.fetch_all_data(match)

        assert raw["team"]["elo_home"] == 2063.76
        mock_fetch.assert_called_once()
        call_kwargs = mock_fetch.call_args.kwargs
        assert call_kwargs["elo_scope"] == "club"
        assert "Arsenal FC" in call_kwargs["team_aliases"]


class TestSyncSchedule:
    @patch("app.sports.football.adapters.epl_adapter.save_fixture")
    @patch("app.sports.football.adapters.epl_adapter.parse_fixture")
    @patch("app.sports.football.adapters.epl_adapter.fetch_competition_fixtures")
    def test_sync_uses_pl_code(self, mock_fetch, mock_parse, mock_save):
        mock_fetch.return_value = [{"id": 1}]
        mock_parse.return_value = {
            "match_id": "epl-1", "home_team": "A", "away_team": "B",
            "kickoff_utc": datetime(2025, 8, 16), "stage": "regular_season",
            "status": "scheduled", "venue": "X",
        }
        adapter = EPLAdapter()
        count = adapter.sync_schedule()
        assert count == 1
        # Verify PL competition code was used
        mock_fetch.assert_called_once_with("PL", season=2025)

    @patch("app.sports.football.adapters.epl_adapter.fetch_competition_fixtures")
    def test_sync_failure_returns_zero(self, mock_fetch):
        mock_fetch.side_effect = Exception("API error")
        adapter = EPLAdapter()
        assert adapter.sync_schedule() == 0


class TestStubMethods:
    def test_fetch_team_data_returns_empty(self):
        assert EPLAdapter().fetch_team_data(MagicMock()) == {}

    def test_fetch_player_data_returns_empty(self):
        assert EPLAdapter().fetch_player_data(MagicMock()) == {}

    def test_fetch_market_data_returns_empty(self):
        assert EPLAdapter().fetch_market_data(MagicMock()) == {}
