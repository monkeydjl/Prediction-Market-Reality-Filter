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
    test_world_cup_sportmonks_connection as check_sportmonks_connection,
    validate_world_cup_sportmonks_pipeline,
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

    @staticmethod
    def lineups() -> bytes:
        return _body({
            "data": [{
                "fixture_id": 1001,
                "participant": {"name": "Team A"},
                "lineup": [
                    {"player": {"name": "Goalkeeper A"}, "position": {"name": "GK"}, "starting": True},
                    {"player": {"name": "Striker A"}, "position": {"name": "FW"}, "starting": False},
                ],
            }],
        })

    @staticmethod
    def cards() -> bytes:
        return _body({
            "data": [
                {
                    "fixture_id": 1001,
                    "player": {"name": "Player A"},
                    "participant": {"name": "Team A"},
                    "type": "yellowcard",
                    "minute": 35,
                },
                {
                    "fixture_id": 1002,
                    "player": {"name": "Player B"},
                    "participant": {"name": "Team B"},
                    "type": "redcard",
                    "minute": 78,
                },
            ],
        })


class WorldCupSportmonksConnectionTests(unittest.TestCase):

    @patch("app.services.world_cup_sportmonks_source.urlopen")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://api.sportmonks.com/v3/football/fixtures")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "test-token-123")
    def test_success(self, mock_urlopen):
        mock_urlopen.return_value = _UrlResponse(_body({
            "data": [{"id": 1}, {"id": 2}],
            "rate_limit": {"remaining": 99, "limit": 100, "resets_at_timestamp": "2026-06-24T00:00:00Z"},
        }))
        result = check_sportmonks_connection()
        self.assertTrue(result["ok"])
        self.assertEqual(result["feed_tested"], "matches")
        self.assertEqual(result["item_count"], 2)
        self.assertEqual(result["rate_limit"]["remaining"], 99)
        self.assertIsNone(result["error"])

    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "")
    def test_no_token(self):
        result = check_sportmonks_connection()
        self.assertFalse(result["ok"])
        self.assertIn("not configured", result["error"])

    @patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", "")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL", "")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "test-token-123")
    def test_no_feeds_configured(self):
        result = check_sportmonks_connection()
        self.assertFalse(result["ok"])
        self.assertIn("No Sportmonks feed URLs configured", result["error"])

    @patch("app.services.world_cup_sportmonks_source.urlopen")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://api.sportmonks.com/v3/football/fixtures")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "test-token-123")
    def test_http_error(self, mock_urlopen):
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError("https://example.com", 403, "Forbidden", {}, None)
        result = check_sportmonks_connection()
        self.assertFalse(result["ok"])
        self.assertIn("HTTP 403", result["error"])

    @patch("app.services.world_cup_sportmonks_source.urlopen")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://api.sportmonks.com/v3/football/fixtures")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "test-token-123")
    def test_provider_errors(self, mock_urlopen):
        mock_urlopen.return_value = _UrlResponse(_body({
            "data": [],
            "errors": [{"message": "Invalid token"}],
        }))
        result = check_sportmonks_connection()
        self.assertFalse(result["ok"])
        self.assertIn("Provider errors", result["error"])


class WorldCupSportmonksValidateTests(unittest.TestCase):

    @patch("app.services.world_cup_sportmonks_source.urlopen")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://api.sportmonks.com/v3/football/fixtures")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "test-token-123")
    def test_success_with_stored_facts(self, mock_urlopen):
        mock_urlopen.return_value = _UrlResponse(_body({
            "data": [
                {"id": 101, "starting_at": "2026-06-11T18:00:00Z",
                 "participants": [
                     {"name": "USA", "meta": {"location": "home"}},
                     {"name": "Mexico", "meta": {"location": "away"}},
                 ]},
                {"id": 102, "starting_at": "2026-06-12T18:00:00Z",
                 "participants": [
                     {"name": "Brazil", "meta": {"location": "home"}},
                     {"name": "Japan", "meta": {"location": "away"}},
                 ]},
            ],
        }))
        with tempfile.TemporaryDirectory() as tmp:
            facts_file = Path(tmp) / "sports_facts.json"
            facts_file.write_text(json.dumps([{
                "fact_id": "f1",
                "kind": "match_result",
                "tournament": WORLD_CUP_TOURNAMENT,
                "match_id": "101",
                "confidence": 1.0,
            }]), encoding="utf-8")
            with patch.object(settings, "SPORTS_FACT_FILE", str(facts_file)):
                result = validate_world_cup_sportmonks_pipeline()
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["steps"]), 3)
        self.assertTrue(result["steps"][0]["ok"])
        self.assertTrue(result["steps"][1]["ok"])
        self.assertEqual(result["coverage"]["api_fixture_count"], 2)
        self.assertEqual(result["coverage"]["covered"], 1)
        self.assertEqual(result["coverage"]["missing_from_store"], 1)

    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "")
    def test_fails_on_bad_connection(self):
        result = validate_world_cup_sportmonks_pipeline()
        self.assertFalse(result["ok"])
        self.assertIn("connection", result["steps"][0]["name"])
        self.assertFalse(result["steps"][0]["ok"])

    @patch("app.services.world_cup_sportmonks_source.urlopen")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://api.sportmonks.com/v3/football/fixtures")
    @patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "test-token-123")
    def test_fails_on_fixture_fetch_error(self, mock_urlopen):
        call_count = [0]

        def side_effect(*_args, **_kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _UrlResponse(_body({"data": [{"id": 1}]}))
            from urllib.error import HTTPError
            raise HTTPError("https://example.com", 500, "Server Error", {}, None)

        mock_urlopen.side_effect = side_effect
        result = validate_world_cup_sportmonks_pipeline()
        self.assertFalse(result["ok"])
        self.assertIn("Fixture fetch failed", result.get("error", ""))


class LineupsCardsAdapterTests(unittest.TestCase):
    """Unit tests for the thin lineups/cards adapters."""

    def test_lineup_row_normalizes_players(self):
        from app.services.world_cup_sportmonks_source import _lineup_row

        raw = {
            "fixture_id": 1001,
            "participant": {"name": "Team A"},
            "lineup": [
                {"player": {"name": "Goalkeeper A"}, "position": {"name": "GK"}, "starting": True},
                {"player": {"name": "Striker A"}, "position": "FW", "starting": False},
            ],
        }
        result = _lineup_row(raw)
        self.assertEqual(result["match_id"], "1001")
        self.assertEqual(result["team"], "Team A")
        self.assertEqual(len(result["players"]), 2)
        self.assertEqual(result["players"][0]["player"], "Goalkeeper A")
        self.assertEqual(result["players"][0]["position"], "GK")
        self.assertTrue(result["players"][0]["starting"])
        self.assertFalse(result["players"][1]["starting"])

    def test_lineup_row_raises_on_no_players(self):
        from app.services.world_cup_sportmonks_source import _lineup_row

        with self.assertRaisesRegex(ValueError, "no players"):
            _lineup_row({"fixture_id": 1001, "participant": {"name": "Team A"}})

    def test_card_row_normalizes_yellow_card(self):
        from app.services.world_cup_sportmonks_source import _card_row

        raw = {
            "fixture_id": 1001,
            "player": {"name": "Player A"},
            "participant": {"name": "Team A"},
            "type": "yellowcard",
            "minute": 35,
        }
        result = _card_row(raw)
        self.assertEqual(result["match_id"], "1001")
        self.assertEqual(result["player"], "Player A")
        self.assertEqual(result["card_type"], "yellow")
        self.assertEqual(result["minute"], "35")

    def test_card_row_normalizes_red_card(self):
        from app.services.world_cup_sportmonks_source import _card_row

        raw = {
            "fixture_id": 1002,
            "player": {"name": "Player B"},
            "participant": {"name": "Team B"},
            "type": "redcard",
            "minute": 78,
        }
        result = _card_row(raw)
        self.assertEqual(result["card_type"], "red")

    def test_card_row_raises_on_missing_player(self):
        from app.services.world_cup_sportmonks_source import _card_row

        with self.assertRaisesRegex(ValueError, "missing player"):
            _card_row({"fixture_id": 1001, "type": "yellow"})


if __name__ == "__main__":
    unittest.main()
