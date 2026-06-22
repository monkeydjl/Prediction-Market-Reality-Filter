import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services import sports_resolution_service as resolver
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    world_cup_data_to_facts,
)
from tests.test_sports_resolution_service import _sports_record


class WorldCupDataSourceServiceTests(unittest.TestCase):
    def test_converts_match_snapshot_to_structured_facts(self):
        facts = world_cup_data_to_facts({
            "source": "official_feed",
            "observed_at": "2026-07-20T00:00:00Z",
            "matches": [{
                "match_id": "round16-1",
                "stage": "round_of_16",
                "home_team": "Team A",
                "away_team": "Team B",
                "status": "finished",
                "home_score": 1,
                "away_score": 1,
                "winner": "Team A",
                "extra_time": True,
                "penalty_shootout": True,
                "home_red_cards": 1,
                "away_red_cards": 0,
            }],
            "qualifications": [{
                "team": "Mexico",
                "status": "qualified",
                "stage": "round_of_32",
                "already_qualified": True,
            }],
            "player_awards": [{
                "award": "golden_boot",
                "player": "Player A",
                "goals": 7,
                "rank": 1,
                "status": "current",
            }],
            "tournament_status": {
                "status": "complete",
                "tournament_complete": True,
            },
        })

        by_kind = {fact["kind"]: fact for fact in facts}
        self.assertEqual(by_kind["match_result"]["fact_id"], "wc2026:match:round16-1")
        self.assertEqual(by_kind["match_result"]["score"], {"home": 1, "away": 1})
        self.assertEqual(by_kind["match_result"]["red_cards"], 1.0)
        self.assertTrue(by_kind["match_result"]["penalty_shootout"])
        self.assertEqual(by_kind["qualification"]["team"], "Mexico")
        self.assertEqual(by_kind["player_award"]["goals"], 7)
        self.assertTrue(by_kind["tournament_status"]["tournament_complete"])

    def test_imported_match_data_feeds_existing_resolution(self):
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
                result = import_world_cup_data({
                    "source": "official_feed",
                    "matches": [{
                        "match_id": "round16-1",
                        "stage": "round_of_16",
                        "home_team": "Team A",
                        "away_team": "Team B",
                        "status": "finished",
                        "penalty_shootout": True,
                    }],
                })
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        decision = resolver.evaluate_world_cup_resolution(record, facts)
        self.assertEqual(result["converted_fact_count"], 1)
        self.assertEqual(decision["actual_outcome"], 100.0)


if __name__ == "__main__":
    unittest.main()
