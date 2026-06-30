"""file_store.py unit tests.

Covers read_json (lenient), read_json_strict (durable-store), quarantine
behavior, and the core regression: a corrupt persistent store must not be
silently overwritten by the next write.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utils import file_store as fs


class ReadJsonLenientTests(unittest.TestCase):
    """read_json is for caches / read-only paths – never raises."""

    def test_missing_file_returns_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nope.json")
            self.assertEqual(fs.read_json(path, {"fallback": True}), {"fallback": True})

    def test_valid_json_returns_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ok.json")
            Path(path).write_text('{"a": 1}', encoding="utf-8")
            self.assertEqual(fs.read_json(path, {}), {"a": 1})

    def test_corrupt_json_returns_fallback_and_quarantines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            Path(path).write_text("{not valid json", encoding="utf-8")
            result = fs.read_json(path, [])
            self.assertEqual(result, [])
            self.assertTrue(os.path.exists(path + ".corrupt"))

    def test_os_error_returns_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "io.json")
            Path(path).write_text("{}", encoding="utf-8")
            with patch("builtins.open", side_effect=PermissionError("locked")):
                result = fs.read_json(path, {"fb": 1})
            self.assertEqual(result, {"fb": 1})


class ReadJsonStrictTests(unittest.TestCase):
    """read_json_strict is for durable stores' read-modify-write paths."""

    def test_missing_file_returns_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nope.json")
            self.assertEqual(fs.read_json_strict(path, []), [])

    def test_valid_json_returns_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ok.json")
            Path(path).write_text('{"a": 1}', encoding="utf-8")
            self.assertEqual(fs.read_json_strict(path, {}), {"a": 1})

    def test_corrupt_json_raises_and_quarantines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            Path(path).write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                fs.read_json_strict(path, {})
            self.assertTrue(os.path.exists(path + ".corrupt"))

    def test_os_error_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "io.json")
            Path(path).write_text("{}", encoding="utf-8")
            with patch("builtins.open", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    fs.read_json_strict(path, {})


class QuarantineTests(unittest.TestCase):
    """.corrupt backup behavior."""

    def test_corrupt_keeps_only_latest(self):
        """Two successive corrupt reads should not accumulate .corrupt files."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rot.json")
            # First corruption
            Path(path).write_text("{bad1", encoding="utf-8")
            fs.read_json(path, {})
            self.assertTrue(os.path.exists(path + ".corrupt"))
            first_content = Path(path + ".corrupt").read_text(encoding="utf-8")
            self.assertEqual(first_content, "{bad1")

            # Second corruption (overwrites .corrupt)
            Path(path).write_text("{bad2", encoding="utf-8")
            fs.read_json(path, {})
            second_content = Path(path + ".corrupt").read_text(encoding="utf-8")
            self.assertEqual(second_content, "{bad2")


class CorruptStoreOverwriteRegressionTests(unittest.TestCase):
    """Core regression: corrupt persistent store must NOT be silently cleared."""

    def test_event_store_corrupt_save_aborts(self):
        """Corrupt event_store.json -> save_events raises, old bytes preserved."""
        from app.memory import event_store as store

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "event_store.json")
            original_bytes = b"{corrupt but was here"
            Path(path).write_bytes(original_bytes)

            with patch.object(store, "_store_path", return_value=path):
                with self.assertRaises(json.JSONDecodeError):
                    store.save_events([{
                        "event_id": "new",
                        "event_title": "t",
                        "event_summary": "s",
                        "probability": {"market": 50, "estimated": 50, "change": 0, "direction": "stable"},
                        "credibility": {"score": 50, "drivers": []},
                        "impact": {"score": 50, "drivers": []},
                        "risk": {"level": "low", "factors": []},
                        "evidence": {"evidence_direction": "neutral", "evidence_strength": 0,
                                     "support_score": 0, "oppose_score": 0, "neutral_score": 0,
                                     "conflict_score": 0, "freshness_score": 0,
                                     "resolution_relevance_score": 0, "source_count": 0,
                                     "sources": [], "items": []},
                        "source": {"type": "manual"},
                        "value_score": 50,
                        "intelligence_report": {"headline": "h", "why_it_matters": "w",
                                                "probability_assessment": "p",
                                                "recommended_action": "a"},
                    }])

            # Original corrupt bytes must still be there (not overwritten)
            self.assertEqual(Path(path).read_bytes(), original_bytes)
            # .corrupt backup should exist
            self.assertTrue(os.path.exists(path + ".corrupt"))


class NestedLockedFileRegressionTests(unittest.TestCase):
    """Regression: nested locked_file() calls on the same path must not deadlock.

    The cross-process lock (POSIX flock / Windows msvcrt.locking) is NOT
    reentrant at the OS level — a second acquire on a different fd from the
    same process blocks against itself. locked_file() must track per-process
    ownership so a nested call (e.g. save_events -> read_json_strict ->
    locked_file, then write_json_atomic -> locked_file on the same path)
    skips re-acquisition and only the outermost call releases.
    """

    def test_nested_locked_file_does_not_deadlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested.json")
            # If the cross-process lock isn't reentrant-safe, this hangs.
            with fs.locked_file(path):
                with fs.locked_file(path):
                    Path(path).write_text("{}", encoding="utf-8")
            # If we got here, no deadlock. Verify the file was written and the
            # cross-process lock tracking entry was cleaned up on exit.
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "{}")
            self.assertNotIn(os.path.abspath(path), fs._HELD_CROSS_PROCESS)

    def test_cross_process_lock_file_is_created(self):
        # The sidecar .crosslock file is created next to the target on first
        # acquisition and is NOT removed on release (unlinking would race
        # with another process trying to acquire on the same path).
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sidecar.json")
            self.assertFalse(os.path.exists(path + ".crosslock"))
            with fs.locked_file(path):
                self.assertTrue(os.path.exists(path + ".crosslock"))
            # Still there after release — operators may need to clean up
            # stale .crosslock files only when no process is running.
            self.assertTrue(os.path.exists(path + ".crosslock"))


if __name__ == "__main__":
    unittest.main()
