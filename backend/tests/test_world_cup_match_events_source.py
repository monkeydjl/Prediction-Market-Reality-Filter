import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_match_events_source import (
    import_world_cup_match_events_source,
    preview_world_cup_match_events_source,
    world_cup_match_events_source_to_data,
)


def _raw_events_payload() -> dict:
    return {
        "source": "api_football",
        "observed_at": "2026-07-20T00:00:00Z",
        "fixture": {"id": 1001},
        "response": [
            {
                "time": {"elapsed": 72, "extra": 1},
                "team": {"name": "Team A"},
                "player": {"name": "Player A"},
                "type": "Card",
                "detail": "Red Card",
                "comments": "Serious foul",
            },
            {
                "time": {"elapsed": 80},
                "team": {"name": "Team B"},
                "player": {"name": "Player B"},
                "type": "Goal",
                "detail": "Normal Goal",
            },
            {
                "time": {"elapsed": 85},
                "team": {"name": "Team B"},
                "player": {"name": "Player C"},
                "type": "Card",
                "detail": "Yellow Card",
            },
        ],
    }


class WorldCupMatchEventsSourceTests(unittest.TestCase):
    def test_normalizes_api_football_card_rows_to_discipline_data(self):
        data = world_cup_match_events_source_to_data(_raw_events_payload())

        self.assertEqual(data["source"], "api_football")
        self.assertEqual(len(data["discipline"]), 2)
        red = data["discipline"][0]
        yellow = data["discipline"][1]
        self.assertEqual(red["match_id"], "1001")
        self.assertEqual(red["team"], "Team A")
        self.assertEqual(red["player"], "Player A")
        self.assertEqual(red["minute"], "72+1")
        self.assertEqual(red["status"], "red_card")
        self.assertEqual(red["red_cards"], 1)
        self.assertEqual(yellow["yellow_cards"], 1)

    def test_preview_converts_card_events_without_writing_facts(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")):
            result = preview_world_cup_match_events_source(_raw_events_payload())
            facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["normalized_event_count"], 2)
        self.assertEqual(result["converted_fact_count"], 2)
        self.assertEqual(result["facts"][0]["kind"], "discipline")
        self.assertEqual(result["facts"][0]["red_cards"], 1.0)
        self.assertEqual(result["facts"][0]["minute"], "72+1")
        self.assertEqual(facts, [])

    def test_import_writes_discipline_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")):
                result = import_world_cup_match_events_source(
                    _raw_events_payload(),
                    replace=True,
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["imported"], 2)
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0]["kind"], "discipline")

    def test_rejects_payload_without_card_events(self):
        with self.assertRaisesRegex(ValueError, "card events"):
            world_cup_match_events_source_to_data({
                "fixture": {"id": 1001},
                "response": [{"type": "Goal", "detail": "Normal Goal"}],
            })

    def test_requires_match_id_for_card_events(self):
        with self.assertRaisesRegex(ValueError, "missing match id"):
            world_cup_match_events_source_to_data({
                "response": [{"type": "Card", "detail": "Red Card"}],
            })


if __name__ == "__main__":
    unittest.main()
