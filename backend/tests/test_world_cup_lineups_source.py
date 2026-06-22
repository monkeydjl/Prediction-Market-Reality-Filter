import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_lineups_source import (
    import_world_cup_lineups_source,
    preview_world_cup_lineups_source,
    world_cup_lineups_source_to_data,
)


def _raw_lineups_payload() -> dict:
    return {
        "source": "api_football_lineups",
        "observed_at": "2026-07-20T00:00:00Z",
        "fixture": {"id": 1001},
        "response": [{
            "team": {"name": "Team A"},
            "formation": "4-3-3",
            "startXI": [{
                "player": {
                    "name": "Player A",
                    "number": 10,
                    "pos": "F",
                    "grid": "4:2",
                }
            }],
            "substitutes": [{
                "player": {
                    "name": "Player B",
                    "number": 12,
                    "pos": "M",
                }
            }],
        }],
    }


class WorldCupLineupsSourceTests(unittest.TestCase):
    def test_normalizes_api_football_lineups_to_player_statuses(self):
        data = world_cup_lineups_source_to_data(_raw_lineups_payload())

        self.assertEqual(data["source"], "api_football_lineups")
        self.assertEqual(len(data["player_statuses"]), 2)
        starter = data["player_statuses"][0]
        bench = data["player_statuses"][1]
        self.assertEqual(starter["kind"], "lineup")
        self.assertEqual(starter["team"], "Team A")
        self.assertEqual(starter["player"], "Player A")
        self.assertEqual(starter["status"], "starting")
        self.assertEqual(starter["match_id"], "1001")
        self.assertEqual(starter["formation"], "4-3-3")
        self.assertEqual(starter["position"], "F")
        self.assertEqual(starter["jersey_number"], "10")
        self.assertEqual(bench["status"], "bench")

    def test_preview_converts_lineups_without_writing_facts(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")):
            result = preview_world_cup_lineups_source(_raw_lineups_payload())
            facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["normalized_lineup_count"], 2)
        self.assertEqual(result["converted_fact_count"], 2)
        self.assertEqual(result["facts"][0]["kind"], "lineup")
        self.assertEqual(result["facts"][0]["position"], "F")
        self.assertEqual(result["facts"][0]["formation"], "4-3-3")
        self.assertEqual(facts, [])

    def test_import_writes_lineup_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")):
                result = import_world_cup_lineups_source(
                    _raw_lineups_payload(),
                    replace=True,
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["imported"], 2)
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0]["kind"], "lineup")

    def test_accepts_flat_player_rows_with_envelope_team(self):
        data = world_cup_lineups_source_to_data({
            "source": "manual_lineup",
            "team": "Team A",
            "fixture": {"id": 1001},
            "lineups": [{
                "player": "Player C",
                "status": "starter",
                "position": "GK",
            }],
        })

        lineup = data["player_statuses"][0]
        self.assertEqual(lineup["team"], "Team A")
        self.assertEqual(lineup["status"], "starting")
        self.assertEqual(lineup["position"], "GK")

    def test_rejects_payload_without_lineups(self):
        with self.assertRaisesRegex(ValueError, "did not contain player lineups"):
            world_cup_lineups_source_to_data({"source": "empty_feed", "response": []})


if __name__ == "__main__":
    unittest.main()
