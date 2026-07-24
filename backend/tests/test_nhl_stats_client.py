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
    fetch_nhl_club_stats,
    pick_primary_goalie,
    summarize_club_rates,
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
        roster = fetch_nhl_team_roster("NYR")
        assert len(roster["goalies"]) == 1
        assert roster["goalies"][0]["lastName"] == "Shesterkin"
        assert "roster/NYR/current" in mock_get.call_args.args[0]


class TestFetchNhlClubStats:
    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_uses_now_path_by_default(self, mock_rl, mock_get):
        mock_get.return_value = _ok_response({"goalies": [], "skaters": []})
        payload = fetch_nhl_club_stats("uta")
        assert payload["goalies"] == []
        assert "club-stats/UTA/now" in mock_get.call_args.args[0]

    @patch("app.sports.hockey.nhl_stats_client.httpx.get")
    @patch("app.sports.hockey.nhl_stats_client._enforce_rate_limit")
    def test_season_path_uses_regular_season_game_type(self, mock_rl, mock_get):
        mock_get.return_value = _ok_response({"goalies": []})
        fetch_nhl_club_stats("COL", season="20252026")
        assert "club-stats/COL/20252026/2" in mock_get.call_args.args[0]


class TestPickPrimaryGoalie:
    def test_picks_most_games_started(self):
        stats = {
            "goalies": [
                {
                    "playerId": 1,
                    "firstName": {"default": "Backup"},
                    "lastName": {"default": "One"},
                    "gamesStarted": 10,
                    "gamesPlayed": 15,
                    "savePercentage": 0.920,
                },
                {
                    "playerId": 2,
                    "firstName": {"default": "Karel"},
                    "lastName": {"default": "Vejmelka"},
                    "gamesStarted": 63,
                    "gamesPlayed": 64,
                    "savePercentage": 0.896679,
                },
            ]
        }
        picked = pick_primary_goalie(stats)
        assert picked is not None
        assert picked["name"] == "Karel Vejmelka"
        assert abs(picked["save_pct"] - 0.896679) < 1e-6
        assert picked["player_id"] == 2

    def test_normalizes_percent_scale(self):
        stats = {
            "goalies": [
                {
                    "firstName": "A",
                    "lastName": "B",
                    "gamesStarted": 5,
                    "savePercentage": 91.2,
                }
            ]
        }
        picked = pick_primary_goalie(stats)
        assert picked is not None
        assert abs(picked["save_pct"] - 0.912) < 1e-6

    def test_empty_returns_none(self):
        assert pick_primary_goalie(None) is None
        assert pick_primary_goalie({"goalies": []}) is None


class TestSummarizeClubRates:
    def test_aggregates_per_game_rates_and_shot_share(self):
        stats = {
            "skaters": [
                {"goals": 20, "shots": 200, "gamesPlayed": 80},
                {"goals": 10, "shots": 100, "gamesPlayed": 60},
            ],
            "goalies": [
                {
                    "goalsAgainst": 120,
                    "shotsAgainst": 1600,
                    "gamesPlayed": 70,
                    "gamesStarted": 65,
                },
                {
                    "goalsAgainst": 40,
                    "shotsAgainst": 400,
                    "gamesPlayed": 20,
                    "gamesStarted": 15,
                },
            ],
        }
        rates = summarize_club_rates(stats)
        assert rates is not None
        assert rates["games"] == 80
        assert abs(rates["gf_per_game"] - 30 / 80) < 1e-6
        assert abs(rates["ga_per_game"] - 160 / 80) < 1e-6
        assert abs(rates["sf_per_game"] - 300 / 80) < 1e-6
        assert abs(rates["sa_per_game"] - 2000 / 80) < 1e-6
        expected_share = (300 / 80) / ((300 / 80) + (2000 / 80))
        assert abs(rates["shot_share"] - expected_share) < 1e-6

    def test_empty_returns_none(self):
        assert summarize_club_rates(None) is None
        assert summarize_club_rates({"skaters": [], "goalies": []}) is None


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
