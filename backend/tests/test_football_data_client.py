"""Tests for football_data_client — parameterized Football-Data.org client."""
from unittest.mock import patch, MagicMock
import pytest

from app.services.football_data_client import (
    fetch_competition_fixtures,
    parse_fixture,
    FootballDataClientError,
)


class TestFetchCompetitionFixtures:
    @patch("app.services.football_data_client.httpx.get")
    @patch("app.services.football_data_client.settings")
    def test_fetch_ucl_fixtures(self, mock_settings, mock_get):
        mock_settings.FOOTBALL_DATA_API_KEY = "test-key"
        mock_settings.FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"matches": [{"id": 123}]}
        mock_get.return_value = mock_response

        result = fetch_competition_fixtures("CL", season=2025)
        assert len(result) == 1
        assert result[0]["id"] == 123
        # Verify URL contains CL competition code
        call_args = mock_get.call_args
        assert "CL" in str(call_args[0][0])

    @patch("app.services.football_data_client.settings")
    def test_no_api_key_raises(self, mock_settings):
        mock_settings.FOOTBALL_DATA_API_KEY = ""
        with pytest.raises(FootballDataClientError, match="not configured"):
            fetch_competition_fixtures("CL")

    @patch("app.services.football_data_client.httpx.get")
    @patch("app.services.football_data_client.settings")
    def test_429_rate_limit_raises(self, mock_settings, mock_get):
        mock_settings.FOOTBALL_DATA_API_KEY = "test-key"
        mock_settings.FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_get.return_value = mock_response
        with pytest.raises(FootballDataClientError, match="Rate limit"):
            fetch_competition_fixtures("PL")


class TestParseFixture:
    def test_parse_ucl_group_stage(self):
        raw = {
            "id": 537327,
            "homeTeam": {"name": "Real Madrid CF"},
            "awayTeam": {"name": "FC Bayern München"},
            "utcDate": "2025-09-16T20:00:00Z",
            "stage": "GROUP_STAGE",
            "status": "SCHEDULED",
            "venue": "Santiago Bernabéu",
            "score": {"fullTime": {"home": None, "away": None}},
        }
        stage_map = {
            "GROUP_STAGE": "group_stage",
            "ROUND_OF_16": "round_of_16",
            "QUARTER_FINALS": "quarterfinal",
            "SEMI_FINALS": "semifinal",
            "FINAL": "final",
        }
        result = parse_fixture(raw, stage_mapping=stage_map, match_id_prefix="ucl-")
        assert result is not None
        assert result["match_id"] == "ucl-537327"
        assert result["home_team"] == "Real Madrid CF"
        assert result["away_team"] == "FC Bayern München"
        assert result["stage"] == "group_stage"
        assert result["status"] == "scheduled"
        assert result["venue"] == "Santiago Bernabéu"

    def test_parse_epl_no_stage_mapping(self):
        raw = {
            "id": 123456,
            "homeTeam": {"name": "Arsenal FC"},
            "awayTeam": {"name": "Chelsea FC"},
            "utcDate": "2025-08-16T15:00:00Z",
            "stage": "",
            "status": "FINISHED",
            "venue": "Emirates Stadium",
            "score": {"fullTime": {"home": 2, "away": 1}},
        }
        result = parse_fixture(raw, stage_mapping=None, match_id_prefix="epl-")
        assert result is not None
        assert result["match_id"] == "epl-123456"
        assert result["stage"] == "regular_season"
        assert result["status"] == "finished"
        assert result["home_score"] == 2
        assert result["away_score"] == 1

    def test_parse_missing_id_returns_none(self):
        raw = {"homeTeam": {"name": "Team A"}}
        result = parse_fixture(raw, stage_mapping=None)
        assert result is None

    def test_parse_missing_teams_returns_none(self):
        raw = {"id": 1, "homeTeam": {}, "awayTeam": {}}
        result = parse_fixture(raw, stage_mapping=None)
        assert result is None
