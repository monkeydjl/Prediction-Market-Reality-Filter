"""Unit tests for source_trust_registry_store (Plan 4 §6.1)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Bootstrap importability (canonical pattern from test_event_market_link_store.py)
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import source_trust_registry_store as registry
from app.utils import sqlite_db


def _db(tmp):
    return patch.object(sqlite_db, "loop_db_path",
                        return_value=str(Path(tmp) / "v2_loop.db"))


class TestSourceTrustRegistryStore(unittest.TestCase):
    def test_table_autocreates_on_fresh_db(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            entries = registry.list_entries()
            self.assertEqual(entries, [])

    def test_upsert_and_get_entry(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(
                pattern="reuters.com",
                pattern_type="domain",
                tier="trusted",
                base_trust=0.90,
                list_category="official",
                notes="路透社官方源",
            )
            entry = registry.get_entry("reuters.com")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["pattern"], "reuters.com")
            self.assertEqual(entry["pattern_type"], "domain")
            self.assertEqual(entry["tier"], "trusted")
            self.assertAlmostEqual(entry["base_trust"], 0.90)
            self.assertEqual(entry["list_category"], "official")
            self.assertEqual(entry["notes"], "路透社官方源")

    def test_upsert_is_idempotent_on_same_pattern(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(
                pattern="reuters.com", pattern_type="domain",
                tier="trusted", base_trust=0.90, list_category="official",
            )
            registry.upsert_entry(
                pattern="reuters.com", pattern_type="domain",
                tier="official", base_trust=0.95, list_category="official",
                notes="updated",
            )
            entry = registry.get_entry("reuters.com")
            self.assertEqual(entry["tier"], "official")  # overwritten
            self.assertAlmostEqual(entry["base_trust"], 0.95)
            self.assertEqual(entry["notes"], "updated")

    def test_list_entries_filtered_by_category(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(pattern="a.com", pattern_type="domain",
                                  tier="trusted", base_trust=0.85, list_category="official")
            registry.upsert_entry(pattern="b.com", pattern_type="domain",
                                  tier="unknown", base_trust=0.20, list_category="denylist")
            official = registry.list_entries(list_category="official")
            self.assertEqual(len(official), 1)
            self.assertEqual(official[0]["pattern"], "a.com")

    def test_delete_entry(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(pattern="reuters.com", pattern_type="domain",
                                  tier="trusted", base_trust=0.90, list_category="official")
            self.assertTrue(registry.delete_entry("reuters.com"))
            self.assertIsNone(registry.get_entry("reuters.com"))
            self.assertFalse(registry.delete_entry("reuters.com"))  # already gone

    def test_match_domain_longest_prefix_wins(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(pattern="reuters.com", pattern_type="domain",
                                  tier="trusted", base_trust=0.85, list_category="official")
            registry.upsert_entry(pattern="politics.reuters.com", pattern_type="domain",
                                  tier="official", base_trust=0.95, list_category="official")
            # Longer match wins
            entry = registry.match_domain("politics.reuters.com")
            self.assertEqual(entry["tier"], "official")
            # Shorter match when no longer one
            entry = registry.match_domain("business.reuters.com")
            self.assertEqual(entry["tier"], "trusted")
            # No match
            entry = registry.match_domain("example.com")
            self.assertIsNone(entry)

    def test_match_source_name_substring(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            registry.upsert_entry(pattern="Reuters Politics", pattern_type="source_name",
                                  tier="trusted", base_trust=0.85, list_category="official")
            entry = registry.match_source_name("Reuters Politics")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["tier"], "trusted")
            # Substring match (case-insensitive)
            entry = registry.match_source_name("reuters politics daily")
            self.assertIsNotNone(entry)

    def test_match_returns_none_on_empty_registry(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self.assertIsNone(registry.match_domain("reuters.com"))
            self.assertIsNone(registry.match_source_name("Reuters"))

    def test_notes_exclude_banned_terms(self):
        """Registry notes must not contain banned trading terms."""
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for term in banned:
                with self.assertRaises(ValueError):
                    registry.upsert_entry(
                        pattern=f"test-{term}.com", pattern_type="domain",
                        tier="trusted", base_trust=0.85, list_category="official",
                        notes=f"this source is {term}",
                    )


if __name__ == "__main__":
    unittest.main()
