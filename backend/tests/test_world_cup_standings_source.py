import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services import sports_resolution_service as resolver
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_standings_source import (
    import_world_cup_standings_source,
    preview_world_cup_standings_source,
    world_cup_standings_source_to_data,
)
from tests.test_sports_resolution_service import _sports_record


def _raw_standings_payload() -> dict:
    return {
        "source": "api_football",
        "source_url": "https://example.com/standings",
        "observed_at": "2026-06-28T00:00:00Z",
        "response": [{
            "league": {
                "standings": [[
                    {
                        "rank": 1,
                        "team": {"name": "Mexico"},
                        "group": "Group A",
                        "description": "Qualified for knockout stage",
                    },
                    {
                        "rank": 4,
                        "team": {"name": "Team B"},
                        "group": "Group A",
                        "description": "Eliminated",
                    },
                ]]
            }
        }],
    }


class WorldCupStandingsSourceTests(unittest.TestCase):
    def test_normalizes_nested_standings_to_qualification_data(self):
        data = world_cup_standings_source_to_data(_raw_standings_payload())

        self.assertEqual(data["source"], "api_football")
        self.assertEqual(data["observed_at"], "2026-06-28T00:00:00Z")
        self.assertEqual(len(data["qualifications"]), 2)
        mexico = data["qualifications"][0]
        self.assertEqual(mexico["team"], "Mexico")
        self.assertEqual(mexico["stage"], "Group A")
        self.assertEqual(mexico["status"], "qualified")
        self.assertTrue(mexico["already_qualified"])
        team_b = data["qualifications"][1]
        self.assertEqual(team_b["status"], "eliminated")
        self.assertTrue(team_b["already_eliminated"])

    def test_group_map_applies_group_name_as_stage(self):
        data = world_cup_standings_source_to_data({
            "source": "manual_table",
            "groups": {
                "Group C": [{
                    "team": "Team C",
                    "already_qualified": True,
                }]
            },
        })

        self.assertEqual(data["qualifications"][0]["stage"], "Group C")
        self.assertEqual(data["qualifications"][0]["status"], "qualified")

    def test_does_not_infer_from_negative_status_phrases(self):
        data = world_cup_standings_source_to_data({
            "standings": [{
                "team": "Team C",
                "description": "not yet qualified",
            }, {
                "team": "Team D",
                "description": "not eliminated",
            }],
        })

        self.assertEqual(data["qualifications"][0]["status"], "not yet qualified")
        self.assertNotIn("already_qualified", data["qualifications"][0])
        self.assertEqual(data["qualifications"][1]["status"], "not eliminated")
        self.assertNotIn("already_eliminated", data["qualifications"][1])

    def test_preview_converts_standings_to_qualification_facts(self):
        result = preview_world_cup_standings_source(_raw_standings_payload())

        self.assertEqual(result["normalized_qualification_count"], 2)
        self.assertEqual(result["converted_fact_count"], 2)
        fact = result["facts"][0]
        self.assertEqual(fact["kind"], "qualification")
        self.assertEqual(fact["team"], "Mexico")
        self.assertTrue(fact["already_qualified"])

    def test_imported_standings_feed_existing_progression_resolution(self):
        record = _sports_record(
            "mexico",
            "Will Mexico reach the knockout stage of the 2026 FIFA World Cup?",
            "world-cup-2026:mexico-knockout-stage",
            "team_progression",
            ["Mexico", WORLD_CUP_TOURNAMENT],
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                result = import_world_cup_standings_source(
                    _raw_standings_payload(),
                    replace=True,
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertEqual(result["converted_fact_count"], 2)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_rejects_payload_without_standings(self):
        with self.assertRaisesRegex(ValueError, "did not contain standings"):
            world_cup_standings_source_to_data({"source": "empty_feed", "response": []})

    def test_rejects_non_object_rows(self):
        with self.assertRaisesRegex(ValueError, "must be an object or list"):
            world_cup_standings_source_to_data({"standings": ["Mexico"]})

    def test_extracts_group_table_metrics(self):
        data = world_cup_standings_source_to_data({
            "source": "manual_table",
            "standings": [{
                "team": "Brazil",
                "group": "Group G",
                "rank": 1,
                "played": 3,
                "won": 3,
                "drawn": 0,
                "lost": 0,
                "points": 9,
                "goals_for": 8,
                "goals_against": 1,
                "goal_diff": 7,
                "description": "Qualified for knockout stage",
            }],
        })
        q = data["qualifications"][0]
        self.assertEqual(q["team"], "Brazil")
        self.assertEqual(q["rank"], 1)
        self.assertEqual(q["played"], 3)
        self.assertEqual(q["won"], 3)
        self.assertEqual(q["drawn"], 0)
        self.assertEqual(q["lost"], 0)
        self.assertEqual(q["points"], 9)
        self.assertEqual(q["goals_for"], 8)
        self.assertEqual(q["goals_against"], 1)
        self.assertEqual(q["goal_diff"], 7)
        self.assertEqual(q["group"], "Group G")

    def test_group_table_short_field_aliases(self):
        data = world_cup_standings_source_to_data({
            "source": "manual_table",
            "standings": [{
                "team": "France",
                "group": "Group H",
                "pos": 2,
                "P": 2,
                "W": 1,
                "D": 1,
                "L": 0,
                "Pts": 4,
                "GF": 3,
                "GA": 2,
                "GD": 1,
                "description": "not yet qualified",
            }],
        })
        q = data["qualifications"][0]
        self.assertEqual(q["rank"], 2)
        self.assertEqual(q["played"], 2)
        self.assertEqual(q["won"], 1)
        self.assertEqual(q["drawn"], 1)
        self.assertEqual(q["lost"], 0)
        self.assertEqual(q["points"], 4)
        self.assertEqual(q["goals_for"], 3)
        self.assertEqual(q["goals_against"], 2)
        self.assertEqual(q["goal_diff"], 1)

    def test_group_table_metrics_flow_through_to_facts(self):
        result = preview_world_cup_standings_source({
            "source": "manual_table",
            "standings": [{
                "team": "Spain",
                "group": "Group F",
                "rank": 1,
                "points": 7,
                "won": 2,
                "drawn": 1,
                "lost": 0,
                "description": "Qualified for knockout stage",
            }],
        })
        self.assertEqual(result["converted_fact_count"], 1)
        fact = result["facts"][0]
        self.assertEqual(fact["points"], 7)
        self.assertEqual(fact["rank"], 1)
        self.assertEqual(fact["won"], 2)
        self.assertEqual(fact["drawn"], 1)
        self.assertEqual(fact["lost"], 0)
        self.assertEqual(fact["group"], "Group F")


if __name__ == "__main__":
    unittest.main()
