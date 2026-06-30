import asyncio
import unittest
from unittest.mock import patch

from app.services import world_cup_event_source as source


class WorldCupEventSourceTests(unittest.TestCase):
    def test_returns_curated_sports_candidates(self):
        with patch.object(source.settings, "WORLD_CUP_SOURCE_ENABLED", True), \
                patch.object(source.settings, "WORLD_CUP_SOURCE_NAME", "World Cup"):
            candidates = asyncio.run(source.fetch_candidate_events(limit=3))

        self.assertEqual(len(candidates), 3)
        first = candidates[0]
        self.assertIn("2026 FIFA World Cup", first["question"])
        self.assertEqual(first["baseline_probability"], 70.0)
        self.assertEqual(first["source"]["type"], "sports_event")
        self.assertEqual(first["source"]["platform"], "World Cup")
        self.assertEqual(first["source"]["source_id"], "world-cup-2026:usa-knockout-stage")
        self.assertEqual(first["source"]["tournament"], "2026 FIFA World Cup")
        self.assertIn("resolution_criteria", first["source"])
        self.assertIn("United States", first["source"]["entities"])

    def test_disabled_source_returns_empty(self):
        with patch.object(source.settings, "WORLD_CUP_SOURCE_ENABLED", False):
            self.assertEqual(asyncio.run(source.fetch_candidate_events(limit=5)), [])

    def test_non_positive_limit_returns_empty(self):
        with patch.object(source.settings, "WORLD_CUP_SOURCE_ENABLED", True):
            self.assertEqual(asyncio.run(source.fetch_candidate_events(limit=0)), [])


if __name__ == "__main__":
    unittest.main()
