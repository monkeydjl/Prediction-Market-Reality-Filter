import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_statistics_source import (
    import_world_cup_statistics_source,
    preview_world_cup_statistics_source,
    world_cup_statistics_source_to_data,
)


def _raw_statistics_payload() -> dict:
    return {
        "source": "api_football_statistics",
        "observed_at": "2026-07-20T00:00:00Z",
        "fixture": {"id": 1001},
        "response": [
            {
                "team": {"name": "Team A"},
                "statistics": [
                    {"type": "Shots on Goal", "value": 5},
                    {"type": "Ball Possession", "value": "60%"},
                ],
            },
            {
                "team": {"name": "Team A"},
                "players": [{
                    "player": {"name": "Player A"},
                    "statistics": [{
                        "games": {"position": "F", "number": 10, "rating": "7.2"},
                        "shots": {"total": 3, "on": 2},
                        "passes": {"key": 1},
                    }],
                }],
            },
        ],
    }


class WorldCupStatisticsSourceTests(unittest.TestCase):
    def test_api_football_statistics_normalize_to_stat_rows(self):
        data = world_cup_statistics_source_to_data(_raw_statistics_payload())

        self.assertEqual(len(data["team_stats"]), 2)
        self.assertEqual(len(data["player_stats"]), 4)
        possession = next(
            row for row in data["team_stats"] if row["stat_name"] == "Ball Possession"
        )
        self.assertEqual(possession["stat_value"], 60.0)
        self.assertEqual(possession["stat_unit"], "%")
        shot_total = next(
            row for row in data["player_stats"] if row["stat_name"] == "shots.total"
        )
        self.assertEqual(shot_total["player"], "Player A")
        self.assertEqual(shot_total["position"], "F")
        self.assertEqual(shot_total["jersey_number"], "10")

    def test_preview_converts_without_writing_facts(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")):
            result = preview_world_cup_statistics_source(_raw_statistics_payload())
            facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["normalized_team_stat_count"], 2)
        self.assertEqual(result["normalized_player_stat_count"], 4)
        self.assertEqual(result["converted_fact_count"], 6)
        self.assertEqual(
            {fact["kind"] for fact in result["facts"]},
            {"team_stat", "player_stat"},
        )
        rating = next(
            fact for fact in result["facts"] if fact["stat_name"] == "games.rating"
        )
        self.assertEqual(rating["stat_value"], 7.2)
        self.assertEqual(facts, [])

    def test_import_writes_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            fact_path = str(Path(tmp) / "facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", fact_path):
                result = import_world_cup_statistics_source(
                    _raw_statistics_payload(),
                    replace=True,
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["converted_fact_count"], 6)
        self.assertEqual(result["imported"], 6)
        self.assertEqual(len(facts), 6)

    def test_direct_rows_are_accepted(self):
        data = world_cup_statistics_source_to_data({
            "source": "official_stats",
            "team_stats": [{
                "team": "Team A",
                "stat_name": "expected_goals",
                "stat_value": 1.7,
            }],
            "player_stats": [{
                "team": "Team A",
                "player": "Player A",
                "stat_name": "shots.total",
                "stat_value": 3,
            }],
        })

        self.assertEqual(data["team_stats"][0]["stat_name"], "expected_goals")
        self.assertEqual(data["player_stats"][0]["player"], "Player A")

    def test_rejects_empty_payload(self):
        with self.assertRaisesRegex(ValueError, "did not contain team or player statistics"):
            world_cup_statistics_source_to_data({"source": "empty_feed", "response": []})


if __name__ == "__main__":
    unittest.main()
