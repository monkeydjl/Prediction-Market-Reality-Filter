# backend/tests/test_nhl_stats_client.py
"""Tests for NHL Stats API client — httpx-based HTTP client."""
from unittest.mock import patch, MagicMock, call
import httpx
import pytest

from app.sports.hockey.nhl_stats_client import (
    fetch_nhl_schedule,
    fetch_nhl_game_feed,
    fetch_nhl_team_roster,
    fetch_nhl_team_abbrevs,
    fetch_nhl_club_schedule,
    NHLStatsClientError,
)


def _ok_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


class TestFetchNhlSchedule:
    @patch("app.sports.hockey.nhl_stats_client.fetch_nhl_club_schedule")
    @patch("app.sports.hockey.nhl_stats_client.fetch_nhl_team_abbrevs")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_returns_deduped_club_games(self, mock_rl, mock_abbrevs, mock_club):
        """fetch_nhl_schedule merges club schedules and dedupes by id."""
        mock_abbrevs.return_value = ["TOR", "MTL"]
        shared = {
            "id": 2025020004,
            "gameState": "OFF",
            "homeTeam": {"abbrev": "TOR"},
            "awayTeam": {"abbrev": "MTL"},
        }
        mock_club.side_effect = [
            [shared, {"id": 2025020005, "gameState": "FUT"}],
            [shared, {"id": 2025020006, "gameState": "FUT"}],
        ]
        games = fetch_nhl_schedule("20252026")
        assert len(games) == 3
        assert {g["id"] for g in games} == {2025020004, 2025020005, 2025020006}
        mock_club.assert_has_calls([
            call("TOR", "20252026"),
            call("MTL", "20252026"),
        ])

    @patch("app.sports.hockey.nhl_stats_client._request")
    @patch("app.sports.hockey.nhl_stats_client.fetch_nhl_team_abbrevs")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_falls_back_to_week_walk_when_clubs_empty(
        self, mock_rl, mock_abbrevs, mock_request
    ):
        mock_abbrevs.return_value = []
        mock_request.side_effect = [
            {
                "regularSeasonStartDate": "2025-10-07",
                "gameWeek": [],
            },
            {
                "nextStartDate": None,
                "gameWeek": [
                    {
                        "date": "2025-10-07",
                        "games": [{"id": 1, "gameState": "FUT"}],
                    }
                ],
            },
        ]
        games = fetch_nhl_schedule("20252026")
        assert len(games) == 1
        assert games[0]["id"] == 1


class TestFetchNhlTeamAbbrevs:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_reads_localized_team_abbrev(self, mock_rl, mock_get):
        mock_get.return_value = _ok_response({
            "standings": [
                {"teamAbbrev": {"default": "TOR"}},
                {"teamAbbrev": {"default": "MTL"}},
                {"teamAbbrev": {"default": "TOR"}},
            ],
        })
        assert fetch_nhl_team_abbrevs() == ["TOR", "MTL"]


class TestFetchNhlClubSchedule:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_returns_games_array(self, mock_rl, mock_get):
        mock_get.return_value = _ok_response({
            "currentSeason": 20252026,
            "games": [{"id": 1}, {"id": 2}],
        })
        games = fetch_nhl_club_schedule("TOR", "20252026")
        assert len(games) == 2
        mock_get.assert_called_once()
        assert "club-schedule-season/TOR/20252026" in mock_get.call_args.args[0]


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
            fetch_nhl_team_abbrevs()

    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_raises_on_network_error(self, mock_rl, mock_get):
        """httpx.RequestError surfaces as NHLStatsClientError."""
        mock_get.side_effect = httpx.RequestError("DNS failure")
        with pytest.raises(NHLStatsClientError):
            fetch_nhl_game_feed(2023020001)

    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_follows_redirects(self, mock_rl, mock_get):
        mock_get.return_value = _ok_response({"standings": []})
        fetch_nhl_team_abbrevs()
        assert mock_get.call_args.kwargs.get("follow_redirects") is True

    @patch("app.sports.hockey.nhl_stats_client.time.sleep")
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_retries_transient_request_error(self, mock_rl, mock_get, mock_sleep):
        mock_get.side_effect = [
            httpx.RequestError("SSL EOF"),
            _ok_response({"standings": [{"teamAbbrev": {"default": "TOR"}}]}),
        ]
        assert fetch_nhl_team_abbrevs() == ["TOR"]
        assert mock_get.call_count == 2
        mock_sleep.assert_called()
