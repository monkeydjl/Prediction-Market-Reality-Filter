# backend/tests/test_nhl_stats_client.py
"""Tests for NHL Stats API client — httpx-based HTTP client."""
from unittest.mock import patch, MagicMock
import httpx
import pytest

from app.sports.hockey.nhl_stats_client import (
    fetch_nhl_schedule,
    fetch_nhl_game_feed,
    fetch_nhl_team_roster,
    NHLStatsClientError,
)


def _ok_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


class TestFetchNhlSchedule:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_returns_games_list(self, mock_rl, mock_get):
        """fetch_nhl_schedule returns the games array from the API response."""
        mock_get.return_value = _ok_response({
            "gameWeek": [
                {"date": "2024-01-15", "games": [
                    {"id": 2023020001, "gameState": "OFF FINAL"},
                    {"id": 2023020002, "gameState": "OFF FINAL"},
                ]},
            ],
        })
        games = fetch_nhl_schedule("20232024")
        assert len(games) == 2
        assert games[0]["id"] == 2023020001


class TestFetchNhlGameFeed:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_returns_game_feed_dict(self, mock_rl, mock_get):
        """fetch_nhl_game_feed returns the full feed payload."""
        mock_get.return_value = _ok_response({
            "id": 2023020001,
            "homeTeam": {"id": 1, "name": "New Jersey Devils"},
            "awayTeam": {"id": 2, "name": "New York Rangers"},
            "scoringPlays": [],
        })
        feed = fetch_nhl_game_feed(2023020001)
        assert feed["id"] == 2023020001
        assert feed["homeTeam"]["name"] == "New Jersey Devils"


class TestFetchNhlTeamRoster:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_returns_roster_with_goalies(self, mock_rl, mock_get):
        """fetch_nhl_team_roster returns roster containing goalies."""
        mock_get.return_value = _ok_response({
            "forwards": [],
            "defensemen": [],
            "goalies": [
                {"id": 8478401, "firstName": "Igor", "lastName": "Shesterkin",
                 "svPct": 0.912},
            ],
        })
        roster = fetch_nhl_team_roster(1)
        assert len(roster["goalies"]) == 1
        assert roster["goalies"][0]["lastName"] == "Shesterkin"
        assert roster["goalies"][0]["svPct"] == 0.912


class TestNHLStatsClientError:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_raises_on_non_200(self, mock_rl, mock_get):
        """Non-200 response raises NHLStatsClientError."""
        bad = MagicMock()
        bad.status_code = 500
        bad.text = "Internal Server Error"
        mock_get.return_value = bad
        with pytest.raises(NHLStatsClientError):
            fetch_nhl_schedule("20232024")

    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_raises_on_network_error(self, mock_rl, mock_get):
        """httpx.RequestError surfaces as NHLStatsClientError."""
        mock_get.side_effect = httpx.RequestError("DNS failure")
        with pytest.raises(NHLStatsClientError):
            fetch_nhl_game_feed(2023020001)
