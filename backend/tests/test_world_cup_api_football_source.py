import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_api_football_source import (
    import_world_cup_api_football_bundle,
    preview_world_cup_api_football_bundle,
    test_world_cup_api_football_connection as check_api_football_connection,
    validate_world_cup_api_football_pipeline,
)


class _UrlResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self, _size: int) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _body(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


class WorldCupApiFootballSourceTests(unittest.TestCase):
    def _settings(self, tmp: str):
        return (
            patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")),
            patch.object(
                settings,
                "WORLD_CUP_API_FOOTBALL_BASE_URL",
                "https://api-football.example/v3",
            ),
            patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "secret-key"),
            patch.object(settings, "WORLD_CUP_API_FOOTBALL_LEAGUE_ID", "1"),
            patch.object(settings, "WORLD_CUP_API_FOOTBALL_SEASON", "2026"),
            patch.object(settings, "WORLD_CUP_API_FOOTBALL_FETCH_EVENTS", False),
            patch.object(settings, "WORLD_CUP_API_FOOTBALL_FETCH_LINEUPS", False),
            patch.object(settings, "WORLD_CUP_API_FOOTBALL_FETCH_STATISTICS", False),
        )

    def test_preview_fetches_provider_feeds_without_writing_facts(self):
        bodies = [
            _Body.fixtures(),
            _Body.standings(),
            _Body.top_scorers(),
            _Body.injuries(),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics = self._settings(tmp)
            with sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics, \
                    patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0), \
                    patch(  # disable staleness gate (fixture uses fixed observed_at; covered by test_world_cup_data_source_service.py)
                        "app.services.world_cup_api_football_source.urlopen",
                        side_effect=[_UrlResponse(body) for body in bodies],
                    ) as open_mock:
                result = preview_world_cup_api_football_bundle(
                    now=datetime(2026, 6, 25, 12, tzinfo=timezone.utc)
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        urls = [call.args[0].full_url for call in open_mock.call_args_list]
        headers = {
            key.lower(): value
            for key, value in open_mock.call_args_list[0].args[0].headers.items()
        }
        self.assertEqual(urls, [
            "https://api-football.example/v3/fixtures?league=1&season=2026",
            "https://api-football.example/v3/standings?league=1&season=2026",
            "https://api-football.example/v3/players/topscorers"
            "?league=1&season=2026",
            "https://api-football.example/v3/injuries?league=1&season=2026",
        ])
        self.assertEqual(headers["x-apisports-key"], "secret-key")
        self.assertEqual(result["provider"], "api_football")
        self.assertEqual(result["source_count"], 4)
        self.assertEqual(result["converted_fact_count"], 4)
        self.assertEqual(result["source_fetch_count"], 4)
        self.assertEqual(result["run"]["source_fetch_count"], 4)
        self.assertEqual(result["source_fetches"][0]["status"], "success")
        self.assertGreaterEqual(result["source_fetches"][0]["duration_ms"], 0)
        self.assertEqual(result["call_budget"]["max_detail_calls"], 100)
        self.assertEqual(
            {fact["kind"] for fact in result["facts"]},
            {"match_result", "qualification", "player_award", "injury"},
        )
        match_fact = next(
            fact for fact in result["facts"] if fact["kind"] == "match_result"
        )
        self.assertEqual(match_fact["kickoff_at"], "2026-07-20T19:00:00+00:00")
        self.assertEqual(match_fact["venue"], "Stadium A, City A")
        self.assertEqual(match_fact["referee"], "Referee A")
        self.assertEqual(facts, [])
        self.assertNotIn("secret-key", json.dumps(result))

    def test_import_skips_empty_provider_responses(self):
        bodies = [
            _Body.fixtures(),
            _body({"errors": [], "response": []}),
            _body({"errors": [], "response": []}),
            _body({"errors": [], "response": []}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics = self._settings(tmp)
            with sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics, \
                    patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0), \
                    patch(  # disable staleness gate (fixture uses fixed observed_at; covered by test_world_cup_data_source_service.py)
                        "app.services.world_cup_api_football_source.urlopen",
                        side_effect=[_UrlResponse(body) for body in bodies],
                    ):
                result = import_world_cup_api_football_bundle(
                    replace=True,
                    now=datetime(2026, 6, 25, 12, tzinfo=timezone.utc),
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(result["skipped_source_count"], 3)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["kickoff_at"], "2026-07-20T19:00:00+00:00")
        self.assertEqual(facts[0]["venue"], "Stadium A, City A")
        self.assertEqual(facts[0]["referee"], "Referee A")

    def test_preview_optionally_fetches_fixture_events_as_discipline_facts(self):
        bodies = [
            _Body.fixtures(),
            _Body.standings(),
            _Body.top_scorers(),
            _Body.injuries(),
            _Body.fixture_events(),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics = self._settings(tmp)
            with sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics, \
                    patch.object(settings, "WORLD_CUP_API_FOOTBALL_FETCH_EVENTS", True), \
                    patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0), \
                    patch(  # disable staleness gate (fixture uses fixed observed_at; covered by test_world_cup_data_source_service.py)
                        "app.services.world_cup_api_football_source.urlopen",
                        side_effect=[_UrlResponse(body) for body in bodies],
                    ) as open_mock:
                result = preview_world_cup_api_football_bundle(
                    now=datetime(2026, 6, 25, 12, tzinfo=timezone.utc)
                )

        urls = [call.args[0].full_url for call in open_mock.call_args_list]
        self.assertEqual(
            urls[-1],
            "https://api-football.example/v3/fixtures/events?fixture=1001",
        )
        self.assertEqual(result["source_count"], 5)
        self.assertIn("match_events", {source["kind"] for source in result["sources"]})
        discipline = next(fact for fact in result["facts"] if fact["kind"] == "discipline")
        self.assertEqual(discipline["match_id"], "1001")
        self.assertEqual(discipline["red_cards"], 1.0)
        self.assertEqual(discipline["minute"], "72")

    def test_preview_optionally_fetches_fixture_lineups_as_lineup_facts(self):
        bodies = [
            _Body.fixtures(),
            _Body.standings(),
            _Body.top_scorers(),
            _Body.injuries(),
            _Body.fixture_lineups(),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics = self._settings(tmp)
            with sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics, \
                    patch.object(settings, "WORLD_CUP_API_FOOTBALL_FETCH_LINEUPS", True), \
                    patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0), \
                    patch(  # disable staleness gate (fixture uses fixed observed_at; covered by test_world_cup_data_source_service.py)
                        "app.services.world_cup_api_football_source.urlopen",
                        side_effect=[_UrlResponse(body) for body in bodies],
                    ) as open_mock:
                result = preview_world_cup_api_football_bundle(
                    now=datetime(2026, 6, 25, 12, tzinfo=timezone.utc)
                )

        urls = [call.args[0].full_url for call in open_mock.call_args_list]
        self.assertEqual(
            urls[-1],
            "https://api-football.example/v3/fixtures/lineups?fixture=1001",
        )
        self.assertEqual(result["source_count"], 5)
        self.assertIn("lineups", {source["kind"] for source in result["sources"]})
        lineup = next(fact for fact in result["facts"] if fact["kind"] == "lineup")
        self.assertEqual(lineup["match_id"], "1001")
        self.assertEqual(lineup["player"], "Player A")
        self.assertEqual(lineup["status"], "starting")
        self.assertEqual(lineup["position"], "F")
        self.assertEqual(lineup["formation"], "4-3-3")

    def test_preview_optionally_fetches_fixture_statistics_as_stat_facts(self):
        bodies = [
            _Body.fixtures(),
            _Body.standings(),
            _Body.top_scorers(),
            _Body.injuries(),
            _Body.fixture_statistics(),
            _Body.fixture_players(),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics = self._settings(tmp)
            with sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics, \
                    patch.object(settings, "WORLD_CUP_API_FOOTBALL_FETCH_STATISTICS", True), \
                    patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0), \
                    patch(  # disable staleness gate (fixture uses fixed observed_at; covered by test_world_cup_data_source_service.py)
                        "app.services.world_cup_api_football_source.urlopen",
                        side_effect=[_UrlResponse(body) for body in bodies],
                    ) as open_mock:
                result = preview_world_cup_api_football_bundle(
                    now=datetime(2026, 6, 25, 12, tzinfo=timezone.utc)
                )

        urls = [call.args[0].full_url for call in open_mock.call_args_list]
        self.assertEqual(
            urls[-2:],
            [
                "https://api-football.example/v3/fixtures/statistics?fixture=1001",
                "https://api-football.example/v3/fixtures/players?fixture=1001",
            ],
        )
        self.assertEqual(result["source_count"], 5)
        self.assertEqual(result["source_fetch_count"], 6)
        self.assertEqual(result["call_budget"]["detail_calls_used"], 2)
        self.assertEqual(result["call_budget"]["detail_calls_remaining"], 98)
        self.assertIn("statistics", {source["kind"] for source in result["sources"]})
        team_stat = next(fact for fact in result["facts"] if fact["kind"] == "team_stat")
        player_stat = next(fact for fact in result["facts"] if fact["kind"] == "player_stat")
        self.assertEqual(team_stat["stat_name"], "shots on goal")
        self.assertEqual(team_stat["stat_value"], 5.0)
        self.assertEqual(player_stat["player"], "Player A")
        self.assertEqual(player_stat["stat_name"], "shots.total")

    def test_fixture_detail_fetches_respect_call_budget(self):
        bodies = [
            _Body.fixtures(),
            _Body.standings(),
            _Body.top_scorers(),
            _Body.injuries(),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics = self._settings(tmp)
            with sports_file, base_url, api_key, league_id, season, fetch_events, fetch_lineups, fetch_statistics, \
                    patch.object(settings, "WORLD_CUP_API_FOOTBALL_FETCH_EVENTS", True), \
                    patch.object(settings, "WORLD_CUP_API_FOOTBALL_MAX_DETAIL_CALLS", 0), \
                    patch.object(settings, "WORLD_CUP_DATA_MAX_AGE_HOURS", 0), \
                    patch(  # disable staleness gate (fixture uses fixed observed_at; covered by test_world_cup_data_source_service.py)
                        "app.services.world_cup_api_football_source.urlopen",
                        side_effect=[_UrlResponse(body) for body in bodies],
                    ) as open_mock:
                result = preview_world_cup_api_football_bundle(
                    now=datetime(2026, 6, 25, 12, tzinfo=timezone.utc)
                )

        self.assertEqual(open_mock.call_count, 4)
        self.assertEqual(result["source_count"], 4)
        self.assertEqual(result["source_fetch_count"], 4)
        self.assertEqual(result["skipped_source_count"], 1)
        self.assertEqual(result["skipped_sources"][0]["kind"], "match_events")
        self.assertEqual(result["skipped_sources"][0]["reason"], "call budget exceeded")
        self.assertEqual(result["call_budget"]["detail_calls_used"], 0)
        self.assertEqual(result["call_budget"]["detail_calls_skipped"], 1)

    def test_missing_api_key_fails_closed(self):
        with patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", ""):
            with self.assertRaisesRegex(ValueError, "API_KEY is not configured"):
                preview_world_cup_api_football_bundle()


class _Body:
    @staticmethod
    def fixtures() -> bytes:
        return _body({
            "errors": [],
            "response": [{
                "fixture": {
                    "id": 1001,
                    "date": "2026-07-20T19:00:00+00:00",
                    "referee": "Referee A",
                    "venue": {"name": "Stadium A", "city": "City A"},
                    "status": {"short": "FT"},
                },
                "league": {"round": "Group A"},
                "teams": {
                    "home": {"name": "Team A", "winner": True},
                    "away": {"name": "Team B", "winner": False},
                },
                "goals": {"home": 2, "away": 0},
            }],
        })

    @staticmethod
    def standings() -> bytes:
        return _body({
            "errors": [],
            "response": [{
                "league": {
                    "standings": [[{
                        "team": {"name": "Team A"},
                        "group": "Group A",
                        "description": "Qualified for knockout stage",
                    }]]
                }
            }],
        })

    @staticmethod
    def top_scorers() -> bytes:
        return _body({
            "errors": [],
            "response": [{
                "rank": 1,
                "player": {"name": "Player A"},
                "statistics": [{
                    "team": {"name": "Team A"},
                    "goals": {"total": 5},
                }],
            }],
        })

    @staticmethod
    def injuries() -> bytes:
        return _body({
            "errors": [],
            "response": [{
                "player": {
                    "name": "Player C",
                    "type": "Missing Fixture",
                    "reason": "Hamstring injury",
                },
                "team": {"name": "Brazil"},
                "fixture": {"id": 1002},
            }],
        })

    @staticmethod
    def fixture_events() -> bytes:
        return _body({
            "errors": [],
            "response": [{
                "time": {"elapsed": 72},
                "team": {"name": "Team A"},
                "player": {"name": "Player A"},
                "type": "Card",
                "detail": "Red Card",
            }],
        })

    @staticmethod
    def fixture_lineups() -> bytes:
        return _body({
            "errors": [],
            "response": [{
                "team": {"name": "Team A"},
                "formation": "4-3-3",
                "startXI": [{
                    "player": {"name": "Player A", "number": 10, "pos": "F"},
                }],
            }],
        })

    @staticmethod
    def fixture_statistics() -> bytes:
        return _body({
            "errors": [],
            "response": [{
                "team": {"name": "Team A"},
                "statistics": [{"type": "Shots on Goal", "value": 5}],
            }],
        })

    @staticmethod
    def fixture_players() -> bytes:
        return _body({
            "errors": [],
            "response": [{
                "team": {"name": "Team A"},
                "players": [{
                    "player": {"name": "Player A"},
                    "statistics": [{
                        "games": {"position": "F", "number": 10},
                        "shots": {"total": 3},
                    }],
                }],
            }],
        })


class WorldCupApiFootballConnectionTests(unittest.TestCase):
    def test_connection_success(self):
        status_body = _body({
            "response": {
                "account": {
                    "firstname": "John",
                    "lastname": "Doe",
                    "email": "john@example.com",
                },
                "subscription": {
                    "plan": "Pro",
                    "active": True,
                    "end": "2026-12-31",
                },
                "requests": {
                    "current": 42,
                    "limit_day": 100,
                },
            }
        })
        with patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "test-key"), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_BASE_URL", "https://api.example/v3"), \
                patch(
                    "app.services.world_cup_api_football_source.urlopen",
                    return_value=_UrlResponse(status_body),
                ) as open_mock:
            result = check_api_football_connection()

        self.assertTrue(result["ok"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["account"]["firstname"], "John")
        self.assertEqual(result["subscription"]["plan"], "Pro")
        self.assertTrue(result["subscription"]["active"])
        self.assertEqual(result["requests_today"], 42)
        self.assertEqual(result["requests_limit"], 100)
        req = open_mock.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.example/v3/status")
        self.assertEqual(req.headers["X-apisports-key"], "test-key")

    def test_connection_no_api_key(self):
        with patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", ""):
            result = check_api_football_connection()

        self.assertFalse(result["ok"])
        self.assertIn("not configured", result["error"])

    def test_connection_no_base_url(self):
        with patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "key"), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_BASE_URL", ""):
            result = check_api_football_connection()

        self.assertFalse(result["ok"])
        self.assertIn("base URL not configured", result["error"])

    def test_connection_http_error(self):
        from urllib.error import HTTPError

        with patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "key"), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_BASE_URL", "https://api.example/v3"), \
                patch(
                    "app.services.world_cup_api_football_source.urlopen",
                    side_effect=HTTPError("url", 401, "Unauthorized", {}, None),
                ):
            result = check_api_football_connection()

        self.assertFalse(result["ok"])
        self.assertIn("401", result["error"])

    def test_connection_timeout(self):
        with patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "key"), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_BASE_URL", "https://api.example/v3"), \
                patch(
                    "app.services.world_cup_api_football_source.urlopen",
                    side_effect=TimeoutError("timed out"),
                ):
            result = check_api_football_connection()

        self.assertFalse(result["ok"])
        self.assertIn("Connection failed", result["error"])


class WorldCupApiFootballValidateTests(unittest.TestCase):
    def _status_body(self):
        return _body({
            "response": {
                "account": {"firstname": "A", "lastname": "B", "email": "a@b.com"},
                "subscription": {"plan": "Pro", "active": True, "end": "2026-12-31"},
                "requests": {"current": 10, "limit_day": 100},
            }
        })

    def _fixtures_body(self, fixture_ids):
        return _body({
            "errors": [],
            "response": [
                {"fixture": {"id": fid, "date": "2026-07-01T18:00:00+00:00", "status": {"short": "NS"}},
                 "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
                 "goals": {"home": None, "away": None}}
                for fid in fixture_ids
            ],
        })

    def test_validate_success_with_stored_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            fact_file = str(Path(tmp) / "facts.json")
            facts_data = {"tournament": "2026 FIFA World Cup", "facts": [
                {"fact_id": "m_1001", "kind": "match_result", "match_id": "1001",
                 "tournament": "2026 FIFA World Cup", "home_team": "A", "away_team": "B"},
            ]}
            Path(fact_file).write_text(json.dumps(facts_data))

            with patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "key"), \
                    patch.object(settings, "WORLD_CUP_API_FOOTBALL_BASE_URL", "https://api.example/v3"), \
                    patch.object(settings, "WORLD_CUP_API_FOOTBALL_LEAGUE_ID", "1"), \
                    patch.object(settings, "WORLD_CUP_API_FOOTBALL_SEASON", "2026"), \
                    patch.object(settings, "SPORTS_FACT_FILE", fact_file), \
                    patch(
                        "app.services.world_cup_api_football_source.urlopen",
                        side_effect=[
                            _UrlResponse(self._status_body()),
                            _UrlResponse(self._fixtures_body([1001, 1002])),
                        ],
                    ):
                result = validate_world_cup_api_football_pipeline()

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["steps"]), 3)
        self.assertTrue(result["steps"][0]["ok"])
        self.assertTrue(result["steps"][1]["ok"])
        self.assertEqual(result["steps"][1]["fixture_count"], 2)
        self.assertEqual(result["coverage"]["covered"], 1)
        self.assertEqual(result["coverage"]["missing_from_store"], 1)
        self.assertIn("1002", result["coverage"]["missing_ids_sample"])

    def test_validate_fails_on_bad_connection(self):
        with patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", ""):
            result = validate_world_cup_api_football_pipeline()

        self.assertFalse(result["ok"])
        self.assertEqual(result["steps"][0]["name"], "connection")
        self.assertFalse(result["steps"][0]["ok"])

    def test_validate_fails_on_fixture_fetch_error(self):
        from urllib.error import HTTPError

        with patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "key"), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_BASE_URL", "https://api.example/v3"), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_LEAGUE_ID", "1"), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_SEASON", "2026"), \
                patch(
                    "app.services.world_cup_api_football_source.urlopen",
                    side_effect=[
                        _UrlResponse(self._status_body()),
                        HTTPError("url", 500, "Server Error", {}, None),
                    ],
                ):
            result = validate_world_cup_api_football_pipeline()

        self.assertFalse(result["ok"])
        self.assertEqual(len(result["steps"]), 2)
        self.assertFalse(result["steps"][1]["ok"])


if __name__ == "__main__":
    unittest.main()
