import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_source_bundle import (
    import_world_cup_source_bundle,
    preview_world_cup_source_bundle,
)


def _bundle_payload() -> dict:
    return {
        "sources": [{
            "kind": "matches",
            "payload": {
                "source": "api_football",
                "observed_at": "2026-07-20T00:00:00Z",
                "response": [{
                    "fixture": {"id": 1001, "status": {"short": "PEN"}},
                    "league": {"round": "Round of 16"},
                    "teams": {
                        "home": {"name": "Team A", "winner": True},
                        "away": {"name": "Team B", "winner": False},
                    },
                    "goals": {"home": 1, "away": 1},
                    "score": {"penalty": {"home": 5, "away": 4}},
                }],
            },
        }, {
            "kind": "standings",
            "payload": {
                "source": "api_football",
                "observed_at": "2026-06-28T00:00:00Z",
                "response": [{
                    "league": {
                        "standings": [[{
                            "team": {"name": "Mexico"},
                            "group": "Group A",
                            "description": "Qualified for knockout stage",
                        }]]
                    }
                }],
            },
        }, {
            "kind": "player_awards",
            "payload": {
                "source": "api_football",
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
            },
        }, {
            "kind": "player_status",
            "payload": {
                "source": "official_injury_feed",
                "observed_at": "2026-06-25T00:00:00Z",
                "response": [{
                    "player": {"name": "Player B"},
                    "team": {"name": "Brazil"},
                    "status": "out",
                    "injury": {"type": "hamstring"},
                }],
            },
        }],
    }


class WorldCupSourceBundleTests(unittest.TestCase):
    def test_preview_converts_multiple_sources_without_writing_facts(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")):
            result = preview_world_cup_source_bundle(_bundle_payload())
            facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["source_count"], 4)
        self.assertEqual(result["converted_fact_count"], 4)
        self.assertEqual([source["kind"] for source in result["sources"]], [
            "matches",
            "standings",
            "player_awards",
            "player_status",
        ])
        self.assertEqual(
            {fact["kind"] for fact in result["facts"]},
            {"match_result", "qualification", "player_award", "injury"},
        )
        self.assertEqual(facts, [])

    def test_import_writes_combined_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            fact_path = str(Path(tmp) / "facts.json")
            with patch.object(settings, "SPORTS_FACT_FILE", fact_path):
                result = import_world_cup_source_bundle(
                    _bundle_payload(),
                    replace=True,
                )
                facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)

        self.assertEqual(result["source_count"], 4)
        self.assertEqual(result["converted_fact_count"], 4)
        self.assertEqual(result["imported"], 4)
        self.assertEqual(len(facts), 4)

    def test_entry_metadata_is_applied_when_payload_metadata_is_missing(self):
        result = preview_world_cup_source_bundle({
            "sources": [{
                "kind": "matches",
                "source": "entry_source",
                "source_url": "https://example.com/feed",
                "observed_at": "2026-07-20T00:00:00Z",
                "payload": {
                    "response": [{
                        "fixture": {"id": 1001, "status": {"short": "FT"}},
                        "teams": {
                            "home": {"name": "Team A", "winner": True},
                            "away": {"name": "Team B", "winner": False},
                        },
                        "goals": {"home": 2, "away": 0},
                    }],
                },
            }],
        })

        normalized = result["sources"][0]["normalized_data"]
        self.assertEqual(normalized["source"], "entry_source")
        self.assertEqual(normalized["source_url"], "https://example.com/feed")
        self.assertEqual(normalized["observed_at"], "2026-07-20T00:00:00Z")

    def test_rejects_empty_bundle(self):
        with self.assertRaisesRegex(ValueError, "at least one source"):
            preview_world_cup_source_bundle({"sources": []})

    def test_rejects_unknown_source_kind(self):
        with self.assertRaisesRegex(ValueError, "unsupported source kind"):
            preview_world_cup_source_bundle({
                "sources": [{
                    "kind": "odds",
                    "payload": {},
                }]
            })

    def test_rejects_invalid_nested_source_with_context(self):
        with self.assertRaisesRegex(ValueError, r"sources\[0\] matches"):
            preview_world_cup_source_bundle({
                "sources": [{
                    "kind": "matches",
                    "payload": {"response": [{"fixture": {"id": 1001}}]},
                }]
            })


if __name__ == "__main__":
    unittest.main()
