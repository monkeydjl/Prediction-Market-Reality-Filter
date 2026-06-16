"""Tests for the event-flow LLM compute cache (app/memory/event_cache.py).

Locks the TTL cache contract: get/set round-trip, TTL expiry, expired-entry
purge on write, and independence from the legacy market_cache file.
"""

import tempfile
import time
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from app.memory import event_cache as cache


class EventCacheTests(unittest.TestCase):
    def test_set_and_get_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_cache.json")
            with patch.object(cache, "_cache_file", return_value=path):
                cache.set_cached_event("Will Bitcoin reach $100k?", {"event_id": "abc"})
                result = cache.get_cached_event("Will Bitcoin reach $100k?")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_id"], "abc")

    def test_miss_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_cache.json")
            with patch.object(cache, "_cache_file", return_value=path):
                self.assertIsNone(cache.get_cached_event("never cached"))

    def test_expired_entry_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_cache.json")
            with patch.object(cache, "_cache_file", return_value=path):
                cache.set_cached_event("q", {"event_id": "x"})
                # Force the cached_at into the past beyond the TTL.
                with patch.object(cache, "_cache_file", return_value=path):
                    data = json.loads(Path(path).read_text(encoding="utf-8"))
                    data[cache._cache_key("q")]["cached_at"] = time.time() - 4000
                    Path(path).write_text(json.dumps(data), encoding="utf-8")
                self.assertIsNone(cache.get_cached_event("q"))

    def test_set_purges_expired_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_cache.json")
            with patch.object(cache, "_cache_file", return_value=path):
                cache.set_cached_event("old", {"event_id": "old"})
                # Manually age the "old" entry past the TTL.
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                data[cache._cache_key("old")]["cached_at"] = time.time() - 4000
                Path(path).write_text(json.dumps(data), encoding="utf-8")
                # Writing a new entry purges the expired one.
                cache.set_cached_event("new", {"event_id": "new"})
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                keys = set(data.keys())
        self.assertIn(cache._cache_key("new"), keys)
        self.assertNotIn(cache._cache_key("old"), keys)

    def test_set_purges_malformed_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event_cache.json"
            path.write_text(
                json.dumps({
                    cache._cache_key("bad"): "not-an-entry",
                    cache._cache_key("also bad"): ["not", "an", "entry"],
                }),
                encoding="utf-8",
            )
            with patch.object(cache, "_cache_file", return_value=str(path)):
                cache.set_cached_event("new", {"event_id": "new"})
                data = json.loads(path.read_text(encoding="utf-8"))
                keys = set(data.keys())
        self.assertEqual(keys, {cache._cache_key("new")})

    def test_key_normalizes_whitespace_and_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_cache.json")
            with patch.object(cache, "_cache_file", return_value=path):
                cache.set_cached_event("  Will BTC Rise?  ", {"event_id": "n"})
                # Same question different case/whitespace -> hit.
                result = cache.get_cached_event("will btc rise?")
        self.assertIsNotNone(result)
        self.assertEqual(result["event_id"], "n")


if __name__ == "__main__":
    unittest.main()
