import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services import sports_resolution_service as resolver
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_player_awards_source import (
    import_world_cup_player_awards_source,
    preview_world_cup_player_awards_source,
    world_cup_player_awards_source_to_data,
)
from tests.test_sports_resolution_service import _sports_record


def _raw_scorer_payload() -> dict:
    return {
        "source": "api_football",
        "source_url": "https://example.com/topscorers",
        "observed_at": "2026-07-20T00:00:00Z",
        "award": "golden_boot",
        "response": [{
            "rank": 1,
            "player": {"name": "Player A"},
            "statistics": [{
                "team": {"name": "Team A"},
                "goals": {"total": 7},
            }],
        }],
    }


class WorldCupPlayerAwardsSourceTests(unittest.TestCase):
    def test_normalizes_nested_top_scorers_to_player_awards(self):
        data = world_cup_player_awards_source_to_data(_raw_scorer_payload())

        self.assertEqual(data["source"], "api_football")
        self.assertEqual(data["observed_at"], "2026-07-20T00:00:00Z")
        self.assertEqual(len(data["player_awards"]), 1)
        award = data["player_awards"][0]
        self.assertEqual(award["award"], "golden_boot")
        self.assertEqual(award["player"], "Player A")
        self.assertEqual(award["team"], "Team A")
        self.assertEqual(award["goals"], 7)
        self.assertEqual(award["rank"], 1)
        self.assertEqual(award["status"], "current")

    def test_player_name_field_does_not_override_default_award(self):
        data = world_cup_player_awards_source_to_data({
            "source": "manual_scorers",
            "name": "Top scorers feed",
            "scorers": [{
                "name": "Player B",
                "team": "Team B",
                "goals": 6,
            }],
        })

        self.assertEqual(data["player_awards"][0]["award"], "golden_boot")
        self.assertEqual(data["player_awards"][0]["player"], "Player B")

    def test_accepts_direct_goals_total_object(self):
        data = world_cup_player_awards_source_to_data({
            "top_scorers": [{
                "player": "Player D",
                "team": "Team D",
                "goals": {"total": 5},
            }],
        })

        self.assertEqual(data["player_awards"][0]["goals"], 5)

    def test_final_payload_marks_award_as_official(self):
        data = world_cup_player_awards_source_to_data({
            "source": "official_awards",
            "final": True,
            "awards": [{
                "player": "Player C",
                "team": "Team C",
                "goals": 6,
            }],
        })

        self.assertEqual(data["player_awards"][0]["status"], "official")

    def test_preview_converts_awards_to_player_award_facts(self):
        result = preview_world_cup_player_awards_source(_raw_scorer_payload())

        self.assertEqual(result["normalized_award_count"], 1)
        self.assertEqual(result["converted_fact_count"], 1)
        fact = result["facts"][0]
        self.assertEqual(fact["kind"], "player_award")
        self.assertEqual(fact["award"], "golden_boot")
        self.assertEqual(fact["player"], "Player A")
        self.assertEqual(fact["goals"], 7)

    def test_imported_awards_feed_existing_goal_threshold_resolution(self):
        record = _sports_record(
            "golden-boot",
            "Will the top scorer at the 2026 FIFA World Cup finish with at least 7 goals?",
            "world-cup-2026:top-scorer-seven-goals",
            "player_awards",
            [WORLD_CUP_TOURNAMENT, "top scorer", "Golden Boot"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sports_facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", path):
                result = import_world_cup_player_awards_source(
                    _raw_scorer_payload(),
                    replace=True,
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(decision["actual_outcome"], 100.0)

    def test_rejects_payload_without_awards(self):
        with self.assertRaisesRegex(ValueError, "did not contain awards"):
            world_cup_player_awards_source_to_data({"source": "empty_feed", "response": []})

    def test_rejects_award_without_goals(self):
        with self.assertRaisesRegex(ValueError, "did not contain awards"):
            world_cup_player_awards_source_to_data({
                "awards": [{
                    "player": "Player A",
                    "team": "Team A",
                }]
            })


if __name__ == "__main__":
    unittest.main()
