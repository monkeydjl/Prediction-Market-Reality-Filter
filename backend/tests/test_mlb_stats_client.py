# backend/tests/test_mlb_stats_client.py
"""Tests for MLB Stats API client — httpx-based HTTP client."""
from unittest.mock import patch, MagicMock
import httpx
import pytest

from app.sports.baseball.mlb_stats_client import (
    extract_probable_pitchers,
    extract_probable_pitchers_from_schedule_game,
    fahrenheit_to_celsius,
    fetch_mlb_schedule,
    fetch_mlb_game_feed,
    fetch_mlb_pitcher,
    fetch_mlb_team_pitcher_stats,
    parse_innings_pitched,
    parse_mlb_weather,
    parse_pitcher_person,
    parse_wind_mph,
    summarize_bullpen_era,
    summarize_team_era,
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
        """fetch_mlb_game_feed returns the full feed payload via v1.1 path."""
        mock_get.return_value = _ok_response({
            "gamePk": 778812,
            "gameData": {"teams": {"home": {"name": "Yankees"}}},
            "liveData": {"plays": {"allPlays": []}},
        })
        feed = fetch_mlb_game_feed(778812)
        assert feed["gamePk"] == 778812
        assert feed["gameData"]["teams"]["home"]["name"] == "Yankees"
        called_url = mock_get.call_args.args[0]
        assert "/api/v1.1/game/778812/feed/live" in called_url


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


class TestFetchMlbTeamPitcherStats:
    @patch("app.sports.baseball.mlb_stats_client.httpx.get")
    @patch("app.sports.baseball.mlb_stats_client._enforce_rate_limit")
    def test_returns_splits_list(self, mock_rl, mock_get):
        mock_get.return_value = _ok_response({
            "stats": [{"splits": [
                {"player": {"fullName": "A"}, "stat": {"era": "2.00", "gamesStarted": 0}},
                {"player": {"fullName": "B"}, "stat": {"era": "3.00", "gamesStarted": 10}},
            ]}],
        })
        splits = fetch_mlb_team_pitcher_stats(144, 2026)
        assert len(splits) == 2
        assert splits[0]["player"]["fullName"] == "A"


class TestParseHelpers:
    def test_parse_innings_pitched_outs(self):
        assert parse_innings_pitched("45.2") == pytest.approx(45 + 2 / 3)
        assert parse_innings_pitched("10.1") == pytest.approx(10 + 1 / 3)
        assert parse_innings_pitched("9.0") == 9.0
        assert parse_innings_pitched(None) == 0.0

    def test_parse_pitcher_person(self):
        payload = {
            "people": [{
                "id": 519242,
                "fullName": "Chris Sale",
                "stats": [{
                    "group": {"displayName": "pitching"},
                    "splits": [{"stat": {"era": "2.19", "whip": "1.05"}}],
                }],
            }],
        }
        parsed = parse_pitcher_person(payload)
        assert parsed["name"] == "Chris Sale"
        assert parsed["era"] == pytest.approx(2.19)
        assert parsed["whip"] == pytest.approx(1.05)
        assert parsed["person_id"] == 519242

    def test_extract_probable_pitchers_from_feed(self):
        feed = {
            "gameData": {
                "probablePitchers": {
                    "home": {"id": 1, "fullName": "Home SP"},
                    "away": {"id": 2, "fullName": "Away SP"},
                }
            }
        }
        out = extract_probable_pitchers(feed)
        assert out["home"] == {"id": 1, "name": "Home SP"}
        assert out["away"] == {"id": 2, "name": "Away SP"}

    def test_extract_probable_from_schedule_game(self):
        game = {
            "teams": {
                "home": {"probablePitcher": {"id": 10, "fullName": "A"}},
                "away": {"probablePitcher": {"id": 20, "fullName": "B"}},
            }
        }
        out = extract_probable_pitchers_from_schedule_game(game)
        assert out["home"]["id"] == 10
        assert out["away"]["name"] == "B"

    def test_summarize_bullpen_era_excludes_starters(self):
        splits = [
            {"stat": {"gamesStarted": 0, "inningsPitched": "9.0", "earnedRuns": 3, "era": "3.00"}},
            {"stat": {"gamesStarted": 0, "inningsPitched": "9.0", "earnedRuns": 1, "era": "1.00"}},
            {"stat": {"gamesStarted": 15, "inningsPitched": "90.0", "earnedRuns": 30, "era": "3.00"}},
        ]
        era = summarize_bullpen_era(splits)
        # (3+1)*9 / 18 = 2.0
        assert era == pytest.approx(2.0)

    def test_summarize_team_era(self):
        assert summarize_team_era({"era": "3.68"}) == pytest.approx(3.68)
        assert summarize_team_era(None) is None


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
