import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_sportmonks_source import (
    import_world_cup_sportmonks_bundle,
    preview_world_cup_sportmonks_bundle,
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


class WorldCupSportmonksSourceTests(unittest.TestCase):
    def _settings(self, tmp: str):
        return (
            patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")),
            patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "secret-token"),
            patch.object(
                settings,
                "WORLD_CUP_SPORTMONKS_FIXTURES_URL",
                "https://sportmonks.example/fixtures?include=participants,scores",
            ),
            patch.object(
                settings,
                "WORLD_CUP_SPORTMONKS_STANDINGS_URL",
                "https://sportmonks.example/standings",
            ),
            patch.object(
                settings,
                "WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL",
                "https://sportmonks.example/topscorers",
            ),
        )

    def test_preview_fetches_provider_feeds_without_writing_facts(self):
        bodies = [
            _Body.fixtures(),
            _Body.standings(),
            _Body.top_scorers(),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sports_file, token, fixtures_url, standings_url, scorers_url = self._settings(tmp)
            with sports_file, token, fixtures_url, standings_url, scorers_url, \
                    patch(
                        "app.services.world_cup_sportmonks_source.urlopen",
                        side_effect=[_UrlResponse(body) for body in bodies],
                    ) as open_mock:
                result = preview_world_cup_sportmonks_bundle(
                    now=datetime(2026, 6, 25, 12, tzinfo=timezone.utc)
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        urls = [call.args[0].full_url for call in open_mock.call_args_list]
        self.assertEqual(
            urls[0],
            "https://sportmonks.example/fixtures"
            "?include=participants%2Cscores&api_token=secret-token",
        )
        self.assertEqual(
            urls[1],
            "https://sportmonks.example/standings?api_token=secret-token",
        )
        self.assertEqual(result["provider"], "sportmonks")
        self.assertEqual(result["source_count"], 3)
        self.assertEqual(result["converted_fact_count"], 3)
        self.assertEqual(result["source_fetch_count"], 3)
        self.assertEqual(result["run"]["source_fetch_count"], 3)
        self.assertEqual(result["source_fetches"][0]["status"], "success")
        self.assertGreaterEqual(result["source_fetches"][0]["duration_ms"], 0)
        self.assertEqual(
            {fact["kind"] for fact in result["facts"]},
            {"match_result", "qualification", "player_award"},
        )
        match_fact = next(
            fact for fact in result["facts"] if fact["kind"] == "match_result"
        )
        self.assertEqual(match_fact["match_id"], "1001")
        self.assertEqual(match_fact["home_team"], "Team A")
        self.assertEqual(match_fact["score"], {"home": 2, "away": 0})
        self.assertEqual(match_fact["winner"], "Team A")
        self.assertEqual(result["source_feeds"][0]["source_url"], "https://sportmonks.example/fixtures")
        self.assertEqual(facts, [])
        self.assertNotIn("secret-token", json.dumps(result))

    def test_import_skips_empty_provider_responses(self):
        bodies = [
            _Body.fixtures(),
            _body({"data": []}),
            _body({"data": []}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sports_file, token, fixtures_url, standings_url, scorers_url = self._settings(tmp)
            with sports_file, token, fixtures_url, standings_url, scorers_url, \
                    patch(
                        "app.services.world_cup_sportmonks_source.urlopen",
                        side_effect=[_UrlResponse(body) for body in bodies],
                    ):
                result = import_world_cup_sportmonks_bundle(
                    replace=True,
                    now=datetime(2026, 6, 25, 12, tzinfo=timezone.utc),
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(result["skipped_source_count"], 2)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(facts), 1)

    def test_missing_api_token_fails_closed(self):
        with patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""), \
                patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://sportmonks.example/fixtures"):
            with self.assertRaisesRegex(ValueError, "API_TOKEN is not configured"):
                preview_world_cup_sportmonks_bundle()

    def test_missing_feed_urls_fail_closed(self):
        with patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "secret-token"), \
                patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", ""), \
                patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""), \
                patch.object(settings, "WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL", ""):
            with self.assertRaisesRegex(ValueError, "No Sportmonks World Cup feed URLs"):
                preview_world_cup_sportmonks_bundle()


class _Body:
    @staticmethod
    def fixtures() -> bytes:
        return _body({
            "data": [{
                "id": 1001,
                "starting_at": "2026-07-20T19:00:00+00:00",
                "state": {"short_name": "FT"},
                "round": {"name": "Group A"},
                "venue": {"name": "Stadium A"},
                "referees": [{"name": "Referee A"}],
                "participants": [
                    {
                        "id": 1,
                        "name": "Team A",
                        "meta": {"location": "home", "winner": True},
                    },
                    {
                        "id": 2,
                        "name": "Team B",
                        "meta": {"location": "away", "winner": False},
                    },
                ],
                "scores": [
                    {"participant_id": 1, "score": {"goals": 2}},
                    {"participant_id": 2, "score": {"goals": 0}},
                ],
            }],
        })

    @staticmethod
    def standings() -> bytes:
        return _body({
            "data": [{
                "participant": {"name": "Team A"},
                "group": {"name": "Group A"},
                "description": "Qualified for knockout stage",
            }],
        })

    @staticmethod
    def top_scorers() -> bytes:
        return _body({
            "data": [{
                "position": 1,
                "total": 5,
                "player": {"name": "Player A"},
                "participant": {"name": "Team A"},
            }],
        })


if __name__ == "__main__":
    unittest.main()
