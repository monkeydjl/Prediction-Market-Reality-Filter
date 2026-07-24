# backend/tests/test_nhl_adapter.py
"""Tests for NHLAdapter — DataAdapter Protocol implementation."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter
from app.sports.hockey.nhl_adapter import NHLAdapter, parse_nhl_game


_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)


def _make_match(match_id="nhl-2023020001") -> MatchIdentity:
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=_NHL, season_key="20232024"),
        stage="regular_season", round=None,
        home=TeamIdentity(code="NJD", name="New Jersey Devils", competition=_NHL),
        away=TeamIdentity(code="NYR", name="New York Rangers", competition=_NHL),
        kickoff_utc=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )


def _make_fixture(match_id="nhl-2023020001", home="New Jersey Devils", away="New York Rangers"):
    fixture = MagicMock()
    fixture.match_id = match_id
    fixture.competition = "nhl"
    fixture.season = "20232024"
    fixture.home_team = home
    fixture.away_team = away
    fixture.kickoff_utc = datetime(2024, 1, 15, tzinfo=timezone.utc)
    fixture.stage = "regular_season"
    fixture.status = "scheduled"
    fixture.venue = "Prudential Center"
    fixture.home_score = None
    fixture.away_score = None
    return fixture


class TestNHLAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        adapter = NHLAdapter()
        assert isinstance(adapter, DataAdapter)


class TestParseNhlGame:
    def test_parses_regular_season_final_game(self):
        """parse_nhl_game maps API fields to internal fixture format."""
        raw = {
            "id": 2023020001,
            "season": 20232024,
            "gameDate": "2024-01-15T00:00:00Z",
            "homeTeam": {"id": 1, "name": "New Jersey Devils", "abbrev": "NJD"},
            "awayTeam": {"id": 2, "name": "New York Rangers", "abbrev": "NYR"},
            "gameState": "OFF FINAL",
            "homeTeamScore": 3,
            "awayTeamScore": 2,
            "gameType": 2,  # 2 = regular season
        }
        parsed = parse_nhl_game(raw)
        assert parsed["match_id"] == "nhl-2023020001"
        assert parsed["home_team"] == "New Jersey Devils"
        assert parsed["away_team"] == "New York Rangers"
        assert parsed["stage"] == "regular_season"
        assert parsed["status"] == "finished"
        assert parsed["home_score"] == 3
        assert parsed["away_score"] == 2

    def test_parses_playoff_game_with_overtime(self):
        """Playoff game maps to 'playoff'; overtime/shootout flags captured."""
        raw = {
            "id": 2023030111,
            "season": 20232024,
            "gameDate": "2024-04-20T00:00:00Z",
            "homeTeam": {"id": 1, "name": "New Jersey Devils", "abbrev": "NJD"},
            "awayTeam": {"id": 2, "name": "New York Rangers", "abbrev": "NYR"},
            "gameState": "OFF FINAL",
            "homeTeamScore": 4,
            "awayTeamScore": 3,
            "gameType": 3,  # 3 = playoffs
            "period": 4,  # OT
        }
        parsed = parse_nhl_game(raw)
        assert parsed["match_id"] == "nhl-2023030111"
        assert parsed["stage"] == "playoff"
        assert parsed["status"] == "finished"
        assert parsed["went_to_overtime"] is True
        assert parsed["went_to_shootout"] is False

    def test_parses_official_nested_place_common_name(self):
        """api-web.nhle.com uses placeName + commonName + team.score."""
        raw = {
            "id": 2025020004,
            "season": 20252026,
            "startTimeUTC": "2025-10-08T23:00:00Z",
            "gameType": 2,
            "gameState": "OFF",
            "venue": {"default": "Scotiabank Arena"},
            "homeTeam": {
                "id": 10,
                "commonName": {"default": "Maple Leafs"},
                "placeName": {"default": "Toronto"},
                "abbrev": "TOR",
                "score": 5,
            },
            "awayTeam": {
                "id": 8,
                "commonName": {"default": "Canadiens"},
                "placeName": {"default": "Montréal"},
                "abbrev": "MTL",
                "score": 2,
            },
            "periodDescriptor": {
                "number": 3,
                "periodType": "REG",
                "maxRegulationPeriods": 3,
            },
            "gameOutcome": {"lastPeriodType": "REG"},
        }
        parsed = parse_nhl_game(raw)
        assert parsed is not None
        assert parsed["match_id"] == "nhl-2025020004"
        assert parsed["home_team"] == "Toronto Maple Leafs"
        assert parsed["away_team"] == "Montreal Canadiens"
        assert parsed["home_score"] == 5
        assert parsed["away_score"] == 2
        assert parsed["status"] == "finished"
        assert parsed["stage"] == "regular_season"
        assert parsed["venue"] == "Scotiabank Arena"
        assert parsed["went_to_overtime"] is False

    def test_parses_ot_from_period_descriptor(self):
        raw = {
            "id": 2025020999,
            "startTimeUTC": "2025-11-01T00:00:00Z",
            "gameType": 2,
            "gameState": "FINAL",
            "homeTeam": {
                "placeName": {"default": "Boston"},
                "commonName": {"default": "Bruins"},
                "score": 3,
            },
            "awayTeam": {
                "placeName": {"default": "Buffalo"},
                "commonName": {"default": "Sabres"},
                "score": 2,
            },
            "periodDescriptor": {"number": 4, "periodType": "OT"},
            "gameOutcome": {"lastPeriodType": "OT"},
        }
        parsed = parse_nhl_game(raw)
        assert parsed["went_to_overtime"] is True
        assert parsed["went_to_shootout"] is False
        assert parsed["status"] == "finished"


class TestNHLAdapterGetMatchIdentity:
    @patch("app.sports.hockey.nhl_adapter.query_fixture")
    def test_returns_identity_when_fixture_found(self, mock_query):
        mock_query.return_value = _make_fixture()
        adapter = NHLAdapter()
        identity = adapter.get_match_identity("nhl-2023020001")
        assert identity.match_id == "nhl-2023020001"
        assert identity.home.name == "New Jersey Devils"
        assert identity.away.name == "New York Rangers"
        assert identity.season.competition.code == "nhl"

    @patch("app.sports.hockey.nhl_adapter.query_fixture")
    def test_returns_stub_when_not_found(self, mock_query):
        mock_query.return_value = None
        adapter = NHLAdapter()
        identity = adapter.get_match_identity("nhl-nonexistent")
        assert identity.match_id == "nhl-nonexistent"
        assert identity.home.name == "Home"


class TestNHLAdapterFetchAllData:
    @patch("app.sports.hockey.nhl_adapter.query_fixture")
    def test_fetch_all_data_includes_goalie_save_pct(self, mock_query):
        """fetch_all_data writes goalie save% into raw['custom']."""
        mock_query.return_value = _make_fixture()

        adapter = NHLAdapter()
        with patch.object(adapter, "_fetch_elo_ratings",
                          return_value={"New Jersey Devils": 1510.0, "New York Rangers": 1495.0}), \
             patch.object(adapter, "_fetch_starting_goalies",
                          return_value={
                              "home": {"name": "Igor Shesterkin", "save_pct": 0.912},
                              "away": {"name": "Juuse Saros", "save_pct": 0.920},
                          }):
            match = _make_match()
            raw = adapter.fetch_all_data(match)
            assert raw["team"]["elo_home"] == 1510.0
            assert raw["team"]["elo_away"] == 1495.0
            assert raw["environment"]["is_home_advantage"] is True
            # Goalie stats in custom dict
            assert raw["custom"]["goalie_save_pct_home"] == 0.912
            assert raw["custom"]["goalie_save_pct_away"] == 0.920
            # Overtime defaults (False for fresh game)
            assert raw["custom"]["went_to_overtime"] is False
            assert raw["custom"]["went_to_shootout"] is False


class TestNHLAdapterFetchOutcome:
    @patch("app.sports.hockey.nhl_adapter.build_match_outcome")
    @patch("app.sports.hockey.nhl_adapter.query_result")
    def test_fetch_outcome_returns_binary_outcome(self, mock_query, mock_build):
        """fetch_outcome returns binary outcome even for OT/shootout games."""
        mock_query.return_value = MagicMock()
        mock_build.return_value = MatchOutcome(
            match_id="nhl-2023020001",
            home_score=3, away_score=2,
            outcome="home_win",  # binary — no "overtime_win"
            finished_at=datetime(2024, 1, 15, 22, 0, tzinfo=timezone.utc),
        )
        adapter = NHLAdapter()
        result = adapter.fetch_outcome("nhl-2023020001")
        assert result is not None
        assert result.home_score == 3
        assert result.outcome == "home_win"


class TestNHLAdapterSyncSchedule:
    @patch("app.sports.hockey.nhl_adapter.save_fixture")
    @patch("app.sports.hockey.nhl_adapter.fetch_nhl_schedule")
    @patch("app.sports.hockey.nhl_adapter.config.settings")
    def test_sync_uses_preferred_then_fallback_season(
        self, mock_settings, mock_fetch, mock_save
    ):
        mock_settings.PHASE5_NHL_ENABLED = True
        mock_fetch.side_effect = [
            [],
            [
                {
                    "id": 2025020001,
                    "season": 20252026,
                    "startTimeUTC": "2025-10-08T23:00:00Z",
                    "gameType": 2,
                    "gameState": "FUT",
                    "homeTeam": {
                        "placeName": {"default": "Toronto"},
                        "commonName": {"default": "Maple Leafs"},
                    },
                    "awayTeam": {
                        "placeName": {"default": "Montreal"},
                        "commonName": {"default": "Canadiens"},
                    },
                }
            ],
        ]
        adapter = NHLAdapter()
        with patch.object(
            adapter,
            "_season_candidates",
            return_value=["20262027", "20252026"],
        ):
            count = adapter.sync_schedule()
        assert count == 1
        assert mock_fetch.call_args_list[0].args[0] == "20262027"
        assert mock_fetch.call_args_list[1].args[0] == "20252026"
        mock_save.assert_called_once()
        assert mock_save.call_args.args[1] == "nhl"
        assert mock_save.call_args.args[2] == "20252026"

    @patch("app.sports.hockey.nhl_adapter.config.settings")
    def test_sync_disabled_returns_zero(self, mock_settings):
        mock_settings.PHASE5_NHL_ENABLED = False
        assert NHLAdapter().sync_schedule() == 0
