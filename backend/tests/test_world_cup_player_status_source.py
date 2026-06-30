import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.sports_signal_service import build_sports_signals
from app.services.world_cup_player_status_source import (
    import_world_cup_player_status_source,
    preview_world_cup_player_status_source,
    world_cup_player_status_source_to_data,
)


def _raw_status_payload() -> dict:
    return {
        "source": "official_injury_feed",
        "source_url": "https://example.com/injuries",
        "observed_at": "2026-06-25T00:00:00Z",
        "response": [{
            "player": {"name": "Player A"},
            "team": {"name": "Brazil"},
            "status": "out",
            "injury": {"type": "hamstring"},
            "severity": "high",
            "fixture": {"id": "group-a-1"},
        }, {
            "player": {"name": "Player B"},
            "team": {"name": "Brazil"},
            "status": "suspended",
            "reason": "red card ban",
        }],
    }


class WorldCupPlayerStatusSourceTests(unittest.TestCase):
    def test_normalizes_raw_player_statuses_to_data_shape(self):
        data = world_cup_player_status_source_to_data(_raw_status_payload())

        self.assertEqual(data["source"], "official_injury_feed")
        self.assertEqual(data["observed_at"], "2026-06-25T00:00:00Z")
        self.assertEqual(len(data["player_statuses"]), 2)
        injury = data["player_statuses"][0]
        self.assertEqual(injury["kind"], "injury")
        self.assertEqual(injury["team"], "Brazil")
        self.assertEqual(injury["player"], "Player A")
        self.assertEqual(injury["status"], "out")
        self.assertEqual(injury["reason"], "hamstring")
        self.assertEqual(injury["match_id"], "group-a-1")
        suspension = data["player_statuses"][1]
        self.assertEqual(suspension["kind"], "suspension")
        self.assertEqual(suspension["status"], "suspended")

    def test_envelope_team_applies_to_player_rows(self):
        data = world_cup_player_status_source_to_data({
            "source": "manual_status",
            "team": "France",
            "injuries": [{
                "player": "Player C",
                "status": "doubtful",
            }],
        })

        self.assertEqual(data["player_statuses"][0]["team"], "France")
        self.assertEqual(data["player_statuses"][0]["kind"], "injury")

    def test_normalizes_api_football_injury_rows(self):
        data = world_cup_player_status_source_to_data({
            "provider": "api_football",
            "observed_at": "2026-06-25T00:00:00Z",
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

        status = data["player_statuses"][0]
        self.assertEqual(status["kind"], "injury")
        self.assertEqual(status["status"], "injured")
        self.assertEqual(status["reason"], "Hamstring injury")
        self.assertEqual(status["match_id"], "1002")

    def test_preview_converts_statuses_to_facts(self):
        result = preview_world_cup_player_status_source(_raw_status_payload())

        self.assertEqual(result["normalized_status_count"], 2)
        self.assertEqual(result["converted_fact_count"], 2)
        facts_by_kind = {fact["kind"]: fact for fact in result["facts"]}
        self.assertEqual(facts_by_kind["injury"]["player"], "Player A")
        self.assertEqual(facts_by_kind["suspension"]["player"], "Player B")

    def test_imported_statuses_feed_existing_injury_signal(self):
        source = {
            "type": "sports_event",
            "category": "team_progression",
            "tournament": WORLD_CUP_TOURNAMENT,
            "source_id": "world-cup-2026:brazil-semifinal",
            "entities": ["Brazil", WORLD_CUP_TOURNAMENT],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                result = import_world_cup_player_status_source(
                    _raw_status_payload(),
                    replace=True,
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        bundle = build_sports_signals(
            "Will Brazil reach the semifinals of the 2026 FIFA World Cup?",
            source,
            facts,
        )
        self.assertEqual(result["converted_fact_count"], 2)
        self.assertEqual(bundle["signals"]["injury_signal"]["level"], "high")

    def test_rejects_payload_without_statuses(self):
        with self.assertRaisesRegex(ValueError, "did not contain player statuses"):
            world_cup_player_status_source_to_data({"source": "empty_feed", "response": []})

    def test_rejects_status_without_team(self):
        with self.assertRaisesRegex(ValueError, "missing team"):
            world_cup_player_status_source_to_data({
                "injuries": [{
                    "player": "Player A",
                    "status": "out",
                }]
            })


if __name__ == "__main__":
    unittest.main()
