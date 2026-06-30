import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services import sports_fact_service as facts


class SportsFactServiceTests(unittest.TestCase):
    def test_import_upserts_and_loads_world_cup_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                result = facts.import_sports_facts({
                    "facts": [{
                        "kind": "injury",
                        "team": "Brazil",
                        "player": "Player A",
                        "status": "out",
                        "severity": "high",
                        "source": "manual",
                        "confidence": 1.2,
                        "applies_to": ["world-cup-2026:brazil-semifinal"],
                    }]
                })
                stored = facts.load_sports_facts(
                    tournament=facts.WORLD_CUP_TOURNAMENT,
                    kind="injury",
                )

                self.assertEqual(result["imported"], 1)
                self.assertEqual(result["total"], 1)
                self.assertEqual(len(stored), 1)
                self.assertTrue(stored[0]["fact_id"].startswith("sports:"))
                self.assertEqual(stored[0]["confidence"], 1.0)
                self.assertEqual(stored[0]["status"], "out")

                result = facts.import_sports_facts([{
                    **stored[0],
                    "status": "questionable",
                }])
                stored = facts.load_sports_facts(
                    tournament=facts.WORLD_CUP_TOURNAMENT,
                    kind="injury",
                )

        self.assertEqual(result["total"], 1)
        self.assertEqual(stored[0]["status"], "questionable")

    def test_all_invalid_import_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                facts.import_sports_facts([{
                    "kind": "qualification",
                    "team": "Mexico",
                    "status": "qualified",
                }])
                result = facts.import_sports_facts(
                    [{"team": "Mexico"}],
                    replace=True,
                )
                stored = facts.load_sports_facts(tournament=facts.WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["team"], "Mexico")

    def test_status_counts_by_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                facts.import_sports_facts([
                    {"kind": "discipline", "red_cards": 2, "source": "manual"},
                    {"kind": "qualification", "team": "Canada", "status": "eliminated"},
                ])
                status = facts.sports_fact_status()

        self.assertEqual(status["count"], 2)
        self.assertEqual(status["by_kind"]["discipline"], 1)
        self.assertEqual(status["by_kind"]["qualification"], 1)

    def test_player_award_facts_keep_goal_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                result = facts.import_sports_facts([{
                    "kind": "player_award",
                    "award": "Golden Boot",
                    "player": "Player A",
                    "goals": 7,
                    "rank": 1,
                    "status": "official",
                }])
                stored = facts.load_sports_facts(
                    tournament=facts.WORLD_CUP_TOURNAMENT,
                    kind="player_award",
                )

        self.assertEqual(result["imported"], 1)
        self.assertEqual(stored[0]["award"], "golden boot")
        self.assertEqual(stored[0]["goals"], 7.0)
        self.assertEqual(stored[0]["rank"], 1.0)

    def test_stat_facts_keep_stat_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                result = facts.import_sports_facts([{
                    "kind": "team_stat",
                    "team": "Team A",
                    "match_id": "1001",
                    "stat_name": "ball possession",
                    "stat_value": "60",
                    "stat_unit": "%",
                }])
                stored = facts.load_sports_facts(
                    tournament=facts.WORLD_CUP_TOURNAMENT,
                    kind="team_stat",
                )

        self.assertEqual(result["imported"], 1)
        self.assertEqual(stored[0]["stat_name"], "ball possession")
        self.assertEqual(stored[0]["stat_value"], 60.0)
        self.assertEqual(stored[0]["stat_unit"], "%")


if __name__ == "__main__":
    unittest.main()
