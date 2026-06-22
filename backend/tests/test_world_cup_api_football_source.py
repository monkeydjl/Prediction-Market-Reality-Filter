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
        )

    def test_preview_fetches_provider_feeds_without_writing_facts(self):
        bodies = [
            _Body.fixtures(),
            _Body.standings(),
            _Body.top_scorers(),
            _Body.injuries(),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sports_file, base_url, api_key, league_id, season = self._settings(tmp)
            with sports_file, base_url, api_key, league_id, season, \
                    patch(
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
        self.assertEqual(
            {fact["kind"] for fact in result["facts"]},
            {"match_result", "qualification", "player_award", "injury"},
        )
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
            sports_file, base_url, api_key, league_id, season = self._settings(tmp)
            with sports_file, base_url, api_key, league_id, season, \
                    patch(
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
                "fixture": {"id": 1001, "status": {"short": "FT"}},
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


if __name__ == "__main__":
    unittest.main()
