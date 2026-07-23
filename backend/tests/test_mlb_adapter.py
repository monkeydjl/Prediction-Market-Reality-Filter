# backend/tests/test_mlb_adapter.py
"""Tests for MLBAdapter — DataAdapter Protocol implementation."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter
from app.sports.baseball.mlb_adapter import MLBAdapter, parse_mlb_game


_BASEBALL = SportIdentity(code="baseball", name="Baseball")
_MLB = CompetitionIdentity(code="mlb", name="MLB", sport=_BASEBALL)


def _make_match(match_id="mlb-778812") -> MatchIdentity:
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_MLB, season_key="2024"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="NYY", name="New York Yankees", competition=_MLB),
        away=TeamIdentity(code="BOS", name="Boston Red Sox", competition=_MLB),
        kickoff_utc=datetime(2024, 7, 4, tzinfo=timezone.utc),
    )


def _make_fixture(match_id="mlb-778812", home="New York Yankees", away="Boston Red Sox"):
    """Create a mock KernelMatchFixture row."""
    fixture = MagicMock()
    fixture.match_id = match_id
    fixture.competition = "mlb"
    fixture.season = "2024"
    fixture.home_team = home
    fixture.away_team = away
    fixture.kickoff_utc = datetime(2024, 7, 4, tzinfo=timezone.utc)
    fixture.stage = "regular_season"
    fixture.status = "scheduled"
    fixture.venue = "Yankee Stadium"
    fixture.home_score = None
    fixture.away_score = None
    return fixture


class TestMLBAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = MLBAdapter()
        assert isinstance(adapter, DataAdapter)


class TestParseMlbGame:
    def test_parses_regular_season_final_game(self):
        """parse_mlb_game maps API fields to internal fixture format."""
        raw = {
            "gamePk": 778812,
            "season": "2024",
            "gameDate": "2024-07-04T00:00:00Z",
            "teams": {
                "home": {"name": "New York Yankees"},
                "away": {"name": "Boston Red Sox"},
            },
            "status": {"abstractGameState": "Final"},
            "linescore": {"home": {"runs": 5}, "away": {"runs": 3}},
        }
        parsed = parse_mlb_game(raw)
        assert parsed["match_id"] == "mlb-778812"
        assert parsed["home_team"] == "New York Yankees"
        assert parsed["away_team"] == "Boston Red Sox"
        assert parsed["stage"] == "regular_season"
        assert parsed["status"] == "finished"

    def test_parses_postseason_scheduled_game(self):
        """Postseason game maps to 'playoff' stage; non-Final maps to 'scheduled'."""
        raw = {
            "gamePk": 781234,
            "season": "2024",
            "gameDate": "2024-10-05T00:00:00Z",
            "teams": {
                "home": {"name": "Houston Astros"},
                "away": {"name": "Texas Rangers"},
            },
            "status": {"abstractGameState": "Preview"},
            "linescore": {"home": {"runs": 0}, "away": {"runs": 0}},
            "seriesDescription": "American League Championship Series",
            "gameType": "L",
        }
        parsed = parse_mlb_game(raw)
        assert parsed["match_id"] == "mlb-781234"
        assert parsed["stage"] == "playoff"
        assert parsed["status"] == "scheduled"

    def test_parses_official_schedule_nested_team_name(self):
        """statsapi.mlb.com nests name under teams.home.team.name."""
        raw = {
            "gamePk": 824410,
            "season": "2026",
            "gameDate": "2026-07-20T22:40:00Z",
            "gameType": "R",
            "teams": {
                "away": {
                    "team": {"id": 142, "name": "Minnesota Twins"},
                    "score": 4,
                },
                "home": {
                    "team": {"id": 114, "name": "Cleveland Guardians"},
                    "score": 13,
                },
            },
            "status": {"abstractGameState": "Final"},
            "venue": {"name": "Progressive Field"},
        }
        parsed = parse_mlb_game(raw)
        assert parsed is not None
        assert parsed["match_id"] == "mlb-824410"
        assert parsed["home_team"] == "Cleveland Guardians"
        assert parsed["away_team"] == "Minnesota Twins"
        assert parsed["home_score"] == 13
        assert parsed["away_score"] == 4
        assert parsed["stage"] == "regular_season"
        assert parsed["status"] == "finished"


class TestMLBAdapterGetMatchIdentity:
    @patch("app.sports.baseball.mlb_adapter.query_fixture")
    def test_returns_identity_when_fixture_found(self, mock_query):
        mock_query.return_value = _make_fixture()
        adapter = MLBAdapter()
        identity = adapter.get_match_identity("mlb-778812")
        assert identity.match_id == "mlb-778812"
        assert identity.home.name == "New York Yankees"
        assert identity.away.name == "Boston Red Sox"
        assert identity.season.competition.code == "mlb"

    @patch("app.sports.baseball.mlb_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = MLBAdapter()
        identity = adapter.get_match_identity("mlb-nonexistent")
        assert identity.match_id == "mlb-nonexistent"
        assert identity.home.name == "Home"


class TestMLBAdapterFetchAllData:
    @patch("app.sports.baseball.mlb_adapter.query_fixture")
    def test_fetch_all_data_includes_pitcher_era_whip(self, mock_query):
        """fetch_all_data writes pitcher ERA/WHIP into raw['custom']."""
        mock_query.return_value = _make_fixture()

        adapter = MLBAdapter()
        # Mock internal helpers to avoid real DB / API calls
        with patch.object(adapter, "_fetch_elo_ratings",
                          return_value={"New York Yankees": 1520.0, "Boston Red Sox": 1490.0}), \
             patch.object(adapter, "_fetch_starting_pitchers",
                          return_value={
                              "home": {"name": "Gerrit Cole", "era": 3.15, "whip": 1.02},
                              "away": {"name": "Brayan Bello", "era": 4.10, "whip": 1.30},
                          }):
            match = _make_match()
            raw = adapter.fetch_all_data(match)
            assert raw["team"]["elo_home"] == 1520.0
            assert raw["team"]["elo_away"] == 1490.0
            assert raw["environment"]["is_home_advantage"] is True
            # Pitcher stats in custom dict
            assert raw["custom"]["pitcher_era_home"] == 3.15
            assert raw["custom"]["pitcher_era_away"] == 4.10
            assert raw["custom"]["pitcher_whip_home"] == 1.02
            assert raw["custom"]["pitcher_whip_away"] == 1.30


class TestMLBAdapterFetchOutcome:
    @patch("app.sports.baseball.mlb_adapter.build_match_outcome")
    @patch("app.sports.baseball.mlb_adapter.query_result")
    def test_fetch_outcome_returns_outcome(self, mock_query, mock_build):
        mock_query.return_value = MagicMock()
        mock_build.return_value = MatchOutcome(
            match_id="mlb-778812",
            home_score=5, away_score=3,
            outcome="home_win",
            finished_at=datetime(2024, 7, 4, 22, 0, tzinfo=timezone.utc),
        )
        adapter = MLBAdapter()
        result = adapter.fetch_outcome("mlb-778812")
        assert result is not None
        assert result.home_score == 5
        assert result.outcome == "home_win"
