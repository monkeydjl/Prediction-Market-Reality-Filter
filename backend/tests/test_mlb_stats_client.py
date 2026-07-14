# backend/tests/test_mlb_stats_client.py
"""Tests for MLB Stats API client — httpx-based HTTP client."""
from unittest.mock import patch, MagicMock
import httpx
import pytest

from app.sports.baseball.mlb_stats_client import (
    fetch_mlb_schedule,
    fetch_mlb_game_feed,
    fetch_mlb_pitcher,
    MLBStatsClientError,
)


def _ok_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


class TestFetchMlbSchedule:
    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_returns_games_list(self, mock_rl, mock_get):
        """fetch_mlb_schedule returns the games array from the API response."""
        mock_get.return_value = _ok_response({
            "dates": [
                {"date": "2024-07-04", "games": [
                    {"gamePk": 778812, "status": {"abstractGameState": "Final"}},
                    {"gamePk": 778813, "status": {"abstractGameState": "Final"}},
                ]},
            ],
        })
        games = fetch_mlb_schedule("2024-07-04", "2024-07-04")
        assert len(games) == 2
        assert games[0]["gamePk"] == 778812


class TestFetchMlbGameFeed:
    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_returns_game_feed_dict(self, mock_rl, mock_get):
        """fetch_mlb_game_feed returns the full feed payload."""
        mock_get.return_value = _ok_response({
            "gamePk": 778812,
            "gameData": {"teams": {"home": {"name": "Yankees"}}},
            "liveData": {"plays": {"allPlays": []}},
        })
        feed = fetch_mlb_game_feed(778812)
        assert feed["gamePk"] == 778812
        assert feed["gameData"]["teams"]["home"]["name"] == "Yankees"


class TestFetchMlbPitcher:
    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_returns_pitcher_stats(self, mock_rl, mock_get):
        """fetch_mlb_pitcher returns pitcher info with stats."""
        mock_get.return_value = _ok_response({
            "people": [{
                "id": 543037,
                "fullName": "Gerrit Cole",
                "stats": [{"group": {"displayName": "pitching"},
                            "splits": [{"stat": {"era": 3.15, "whip": 1.02}}]}],
            }],
        })
        pitcher = fetch_mlb_pitcher(543037)
        assert pitcher["people"][0]["fullName"] == "Gerrit Cole"
        assert pitcher["people"][0]["stats"][0]["splits"][0]["stat"]["era"] == 3.15


class TestMLBStatsClientError:
    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_raises_on_non_200(self, mock_rl, mock_get):
        """Non-200 response raises MLBStatsClientError."""
        bad = MagicMock()
        bad.status_code = 500
        bad.text = "Internal Server Error"
        mock_get.return_value = bad
        with pytest.raises(MLBStatsClientError):
            fetch_mlb_schedule("2024-07-04", "2024-07-04")

    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_raises_on_network_error(self, mock_rl, mock_get):
        """httpx.RequestError surfaces as MLBStatsClientError."""
        mock_get.side_effect = httpx.RequestError("DNS failure")
        with pytest.raises(MLBStatsClientError):
            fetch_mlb_game_feed(778812)
