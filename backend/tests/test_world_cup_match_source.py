import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services import sports_resolution_service as resolver
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_match_source import (
    import_world_cup_match_source,
    preview_world_cup_match_source,
    world_cup_match_source_to_data,
)
from tests.test_sports_resolution_service import _sports_record


def _raw_match_payload() -> dict:
    return {
        "source": "api_football",
        "source_url": "https://example.com/fixtures",
        "observed_at": "2026-07-20T00:00:00Z",
        "response": [{
            "fixture": {
                "id": 1001,
                "status": {"short": "PEN"},
            },
            "league": {"round": "Round of 16"},
            "teams": {
                "home": {"name": "Team A", "winner": True},
                "away": {"name": "Team B", "winner": False},
            },
            "goals": {"home": 1, "away": 1},
            "score": {"penalty": {"home": 5, "away": 4}},
            "cards": {
                "home": {"red": 1, "yellow": 2},
                "away": {"red": 0, "yellow": 3},
            },
        }],
    }


class WorldCupMatchSourceTests(unittest.TestCase):
    def test_normalizes_nested_fixture_payload_to_data_source_shape(self):
        data = world_cup_match_source_to_data(_raw_match_payload())

        self.assertEqual(data["source"], "api_football")
        self.assertEqual(data["observed_at"], "2026-07-20T00:00:00Z")
        match = data["matches"][0]
        self.assertEqual(match["match_id"], "1001")
        self.assertEqual(match["stage"], "Round of 16")
        self.assertEqual(match["home_team"], "Team A")
        self.assertEqual(match["away_team"], "Team B")
        self.assertEqual(match["winner"], "Team A")
        self.assertEqual(match["status"], "finished")
        self.assertEqual(match["home_score"], 1)
        self.assertTrue(match["extra_time"])
        self.assertTrue(match["penalty_shootout"])
        self.assertEqual(match["home_red_cards"], 1)
        self.assertEqual(match["away_yellow_cards"], 3)

    def test_preview_converts_match_source_to_facts(self):
        result = preview_world_cup_match_source(_raw_match_payload())

        self.assertEqual(result["normalized_match_count"], 1)
        self.assertEqual(result["converted_fact_count"], 1)
        fact = result["facts"][0]
        self.assertEqual(fact["kind"], "match_result")
        self.assertEqual(fact["match_id"], "1001")
        self.assertEqual(fact["score"], {"home": 1, "away": 1})
        self.assertEqual(fact["red_cards"], 1.0)
        self.assertTrue(fact["penalty_shootout"])

    def test_imported_match_source_feeds_existing_resolution(self):
        record = _sports_record(
            "penalties",
            "Will any 2026 FIFA World Cup knockout match be decided by a penalty shootout?",
            "world-cup-2026:penalty-shootout",
            "match_format",
            [WORLD_CUP_TOURNAMENT, "penalty shootout"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                result = import_world_cup_match_source(_raw_match_payload(), replace=True)
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_accepts_flat_single_match_payload(self):
        data = world_cup_match_source_to_data({
            "source": "manual_fixture_export",
            "match_id": "group-a-1",
            "home_team": "United States",
            "away_team": "Mexico",
            "status": "scheduled",
        })

        self.assertEqual(data["matches"][0]["match_id"], "group-a-1")
        self.assertEqual(data["matches"][0]["status"], "scheduled")

    def test_ignores_boolean_winner_as_team_name(self):
        data = world_cup_match_source_to_data({
            "match_id": "group-a-2",
            "home_team": "United States",
            "away_team": "Mexico",
            "status": "finished",
            "winner": True,
            "home_score": 1,
            "away_score": 0,
        })

        self.assertNotIn("winner", data["matches"][0])

    def test_rejects_payload_without_matches(self):
        with self.assertRaisesRegex(ValueError, "did not contain matches"):
            world_cup_match_source_to_data({"source": "empty_feed", "response": []})

    def test_rejects_match_without_teams(self):
        with self.assertRaisesRegex(ValueError, "missing home or away team"):
            world_cup_match_source_to_data({"matches": [{"id": "m1"}]})


if __name__ == "__main__":
    unittest.main()
