"""Integration tests for the /decision-timeline route (Plan 5 §5.4)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient

from app.main import app
from app.memory import decision_timeline_store, event_store
from app.utils import sqlite_db


def _db(tmp):
    return patch.object(sqlite_db, "loop_db_path",
                        return_value=str(Path(tmp) / "v2_loop.db"))


def _sample_record(event_id="evt-tl", direction="YES", **overrides):
    rec = {
        "event_id": event_id,
        "event_title": "Test event",
        "probability": {"baseline": 50.0, "estimated": 55.0,
                        "change": 5.0, "direction": direction},
        "final_displayed_direction": direction,
        "final_downgrade_reason": None,
        "decision_quality": {"downgraded": False, "raw_direction": direction,
                             "displayed_direction": direction},
        "market_quality": None,
        "source_reliability": None,
        "execution_quality": None,
        "llm_telemetry": {"degraded_mode": False},
        "guardrail_fired": None,
        "outcome": None,
    }
    rec.update(overrides)
    return rec


class TestDecisionTimelineRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_404_for_unknown_event_id(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            # Patch event_store.get_event to return None for this id.
            with patch.object(event_store, "get_event", return_value=None):
                resp = self.client.get("/api/events/evt-unknown/decision-timeline")
            self.assertEqual(resp.status_code, 404)

    def test_returns_empty_list_for_event_with_no_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            # event exists (get_event returns a stub) but no snapshots stored.
            entry = {"event_id": "evt-empty", "first_seen": "t",
                     "last_updated": "t", "record": _sample_record("evt-empty")}
            with patch.object(event_store, "get_event", return_value=entry):
                resp = self.client.get("/api/events/evt-empty/decision-timeline")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["event_id"], "evt-empty")
            self.assertEqual(body["count"], 0)
            self.assertEqual(body["snapshots"], [])
            self.assertEqual(body["diffs"], [])

    def test_returns_snapshots_and_diffs_in_order(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            # Insert 3 snapshots with direction changes.
            decision_timeline_store.record_snapshot(
                _sample_record("evt-tl", direction="YES"))
            decision_timeline_store.record_snapshot(
                _sample_record("evt-tl", direction="WAIT",
                               final_downgrade_reason="证据冲突",
                               decision_quality={"downgraded": True,
                                                 "downgrade_reason": "证据冲突"}))
            decision_timeline_store.record_snapshot(
                _sample_record("evt-tl", direction="AVOID",
                               final_downgrade_reason="LLM 降级",
                               llm_telemetry={"degraded_mode": True}))
            entry = {"event_id": "evt-tl", "first_seen": "t",
                     "last_updated": "t", "record": _sample_record("evt-tl")}
            with patch.object(event_store, "get_event", return_value=entry):
                resp = self.client.get("/api/events/evt-tl/decision-timeline")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["count"], 3)
            self.assertEqual(len(body["snapshots"]), 3)
            self.assertEqual([s["final_displayed_direction"] for s in body["snapshots"]],
                             ["YES", "WAIT", "AVOID"])
            # diffs has len(snapshots) - 1 = 2 entries.
            self.assertEqual(len(body["diffs"]), 2)
            self.assertEqual(body["diffs"][0]["primary_change_driver"], "calibration")
            self.assertEqual(body["diffs"][1]["primary_change_driver"], "llm_degraded")
            self.assertTrue(body["diffs"][0]["direction_changed"])

    def test_respects_limit_query_param(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for i in range(5):
                decision_timeline_store.record_snapshot(
                    _sample_record("evt-tl", direction=f"D{i}"))
            entry = {"event_id": "evt-tl", "first_seen": "t",
                     "last_updated": "t", "record": _sample_record("evt-tl")}
            with patch.object(event_store, "get_event", return_value=entry):
                resp = self.client.get("/api/events/evt-tl/decision-timeline?limit=3")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["count"], 5)  # count is total, not limited
            self.assertEqual(len(body["snapshots"]), 3)  # snapshots limited
            self.assertEqual([s["final_displayed_direction"] for s in body["snapshots"]],
                             ["D2", "D3", "D4"])


if __name__ == "__main__":
    unittest.main()
