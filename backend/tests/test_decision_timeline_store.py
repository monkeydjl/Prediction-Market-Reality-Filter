"""Unit tests for decision_timeline_store (Plan 5 §5.4)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import decision_timeline_store as dt
from app.utils import sqlite_db


def _db(tmp):
    return patch.object(sqlite_db, "loop_db_path",
                        return_value=str(Path(tmp) / "v2_loop.db"))


def _sample_record(event_id="evt-001", **overrides):
    rec = {
        "event_id": event_id,
        "probability": {"baseline": 50.0, "estimated": 55.0,
                        "change": 5.0, "direction": "YES"},
        "final_displayed_direction": "YES",
        "final_downgrade_reason": None,
        "decision_quality": {"downgraded": False, "raw_direction": "YES",
                             "displayed_direction": "YES"},
        "market_quality": None,
        "source_reliability": None,
        "execution_quality": None,
        "llm_telemetry": {"degraded_mode": False},
        "guardrail_fired": None,
        "outcome": None,
    }
    rec.update(overrides)
    return rec


class TestDecisionTimelineStore(unittest.TestCase):
    def test_table_autocreates_on_fresh_db(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self.assertEqual(dt.count_snapshots("evt-001"), 0)
            self.assertEqual(dt.list_snapshots("evt-001"), [])

    def test_record_snapshot_returns_id_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            sid = dt.record_snapshot(_sample_record())
            self.assertIsNotNone(sid)
            self.assertEqual(dt.count_snapshots("evt-001"), 1)
            snaps = dt.list_snapshots("evt-001")
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["snapshot_id"], sid)
            self.assertEqual(snaps[0]["event_id"], "evt-001")
            self.assertEqual(snaps[0]["final_displayed_direction"], "YES")
            self.assertEqual(snaps[0]["probability"], {"baseline": 50.0,
                                                       "estimated": 55.0,
                                                       "change": 5.0,
                                                       "direction": "YES"})
            self.assertEqual(snaps[0]["decision_quality"]["downgraded"], False)
            self.assertFalse(snaps[0]["llm_degraded_mode"])

    def test_list_snapshots_ordered_ascending_by_recorded_at(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            dt.record_snapshot(_sample_record(final_displayed_direction="YES"))
            dt.record_snapshot(_sample_record(final_displayed_direction="WAIT"))
            dt.record_snapshot(_sample_record(final_displayed_direction="AVOID"))
            snaps = dt.list_snapshots("evt-001")
            self.assertEqual([s["final_displayed_direction"] for s in snaps],
                             ["YES", "WAIT", "AVOID"])

    def test_list_snapshots_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for i in range(5):
                dt.record_snapshot(_sample_record(final_displayed_direction=f"D{i}"))
            snaps = dt.list_snapshots("evt-001", limit=3)
            self.assertEqual(len(snaps), 3)
            # Most recent 3 (last inserted 3) returned in ASC order.
            self.assertEqual([s["final_displayed_direction"] for s in snaps],
                             ["D2", "D3", "D4"])

    def test_list_snapshots_filtered_by_event_id(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            dt.record_snapshot(_sample_record("evt-A"))
            dt.record_snapshot(_sample_record("evt-B"))
            dt.record_snapshot(_sample_record("evt-A"))
            self.assertEqual(dt.count_snapshots("evt-A"), 2)
            self.assertEqual(dt.count_snapshots("evt-B"), 1)
            self.assertEqual(len(dt.list_snapshots("evt-A")), 2)
            self.assertEqual(len(dt.list_snapshots("evt-B")), 1)

    def test_record_snapshot_handles_missing_overlay_blocks(self):
        """A record with no overlays (e.g. a freshly discovered event) must
        still snapshot without crashing — overlays_json stores nulls."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            rec = {"event_id": "evt-min", "final_displayed_direction": None}
            sid = dt.record_snapshot(rec)
            self.assertIsNotNone(sid)
            snaps = dt.list_snapshots("evt-min")
            self.assertEqual(len(snaps), 1)
            self.assertIsNone(snaps[0]["final_displayed_direction"])
            self.assertIsNone(snaps[0]["decision_quality"])
            self.assertIsNone(snaps[0]["market_quality"])
            self.assertIsNone(snaps[0]["probability"])

    def test_record_snapshot_captures_outcome_when_present(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            dt.record_snapshot(_sample_record(outcome="YES"))
            snaps = dt.list_snapshots("evt-001")
            self.assertEqual(snaps[0]["outcome"], "YES")

    def test_record_snapshot_captures_guardrail_fired_list(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            dt.record_snapshot(_sample_record(guardrail_fired=["rule_a", "rule_b"]))
            snaps = dt.list_snapshots("evt-001")
            self.assertEqual(snaps[0]["guardrail_fired"], ["rule_a", "rule_b"])

    def test_record_snapshot_captures_llm_degraded_mode_true(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            dt.record_snapshot(_sample_record(
                llm_telemetry={"degraded_mode": True}))
            snaps = dt.list_snapshots("evt-001")
            self.assertTrue(snaps[0]["llm_degraded_mode"])

    def test_record_snapshot_is_append_only(self):
        """Each call creates a new row — no upsert, no dedup. The store is a
        timeline, not a latest-state cache."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for _ in range(3):
                dt.record_snapshot(_sample_record())
            self.assertEqual(dt.count_snapshots("evt-001"), 3)


if __name__ == "__main__":
    unittest.main()
