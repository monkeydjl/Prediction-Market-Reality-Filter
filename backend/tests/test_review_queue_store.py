"""Unit tests for review_queue_store (Plan 4 §6.2)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.memory import review_queue_store as rq
from app.utils import sqlite_db


def _db(tmp):
    return patch.object(sqlite_db, "loop_db_path",
                        return_value=str(Path(tmp) / "v2_loop.db"))


class TestReviewQueueStore(unittest.TestCase):
    def test_table_autocreates_on_fresh_db(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self.assertEqual(rq.list_pending(), [])

    def test_enqueue_and_get_item(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-001",
                trigger="high_value_downgraded",
                severity="WARN",
                reason="高价值事件被降级为 WAIT",
                context={"final_direction": "WAIT", "raw_direction": "YES"},
            )
            self.assertIsNotNone(item_id)
            item = rq.get_item(item_id)
            self.assertIsNotNone(item)
            self.assertEqual(item["event_id"], "evt-001")
            self.assertEqual(item["trigger"], "high_value_downgraded")
            self.assertEqual(item["severity"], "WARN")
            self.assertEqual(item["status"], "pending")

    def test_list_pending_filtered_by_trigger(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            rq.enqueue_item(event_id="a", trigger="t1", severity="WARN",
                            reason="r1", context={})
            rq.enqueue_item(event_id="b", trigger="t2", severity="ERROR",
                            reason="r2", context={})
            t1_items = rq.list_pending(trigger="t1")
            self.assertEqual(len(t1_items), 1)
            self.assertEqual(t1_items[0]["event_id"], "a")

    def test_take_action_validates_vocabulary(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-001", trigger="t", severity="WARN",
                reason="r", context={},
            )
            # Valid action
            rq.take_action(item_id=item_id, reviewer="alice",
                           action="confirm", note="已确认")
            item = rq.get_item(item_id)
            self.assertEqual(item["status"], "resolved")
            self.assertEqual(item["reviewer_decision"], "confirm")
            self.assertEqual(item["reviewer"], "alice")

            # Invalid action
            item_id2 = rq.enqueue_item(
                event_id="evt-002", trigger="t", severity="WARN",
                reason="r", context={},
            )
            with self.assertRaises(ValueError):
                rq.take_action(item_id=item_id2, reviewer="bob",
                               action="random_action", note="")

    def test_audit_log_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-001", trigger="t", severity="WARN",
                reason="r", context={},
            )
            rq.take_action(item_id=item_id, reviewer="alice",
                           action="request_more_evidence", note="需要更多证据")
            rq.take_action(item_id=item_id, reviewer="bob",
                           action="confirm", note="证据已补充，确认")
            log = rq.get_audit_log(item_id=item_id)
            self.assertEqual(len(log), 2)
            self.assertEqual(log[0]["action"], "request_more_evidence")
            self.assertEqual(log[0]["reviewer"], "alice")
            self.assertEqual(log[1]["action"], "confirm")
            self.assertEqual(log[1]["reviewer"], "bob")

    def test_resolved_items_not_in_pending(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-001", trigger="t", severity="WARN",
                reason="r", context={},
            )
            self.assertEqual(len(rq.list_pending()), 1)
            rq.take_action(item_id=item_id, reviewer="alice",
                           action="confirm", note="")
            self.assertEqual(len(rq.list_pending()), 0)
            self.assertEqual(len(rq.list_resolved()), 1)

    def test_reason_excludes_banned_terms(self):
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for term in banned:
                with self.assertRaises(ValueError):
                    rq.enqueue_item(
                        event_id=f"evt-{term}", trigger="t", severity="WARN",
                        reason=f"this source is {term}", context={},
                    )

    def test_take_action_rejects_banned_notes(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-001", trigger="t", severity="WARN",
                reason="正常原因", context={},
            )
            with self.assertRaises(ValueError):
                rq.take_action(item_id=item_id, reviewer="alice",
                               action="confirm", note="contains long term")

    def test_take_action_on_nonexistent_item_raises(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            with self.assertRaises(KeyError):
                rq.take_action(item_id="nonexistent", reviewer="alice",
                               action="confirm", note="")

    def test_audit_log_global_when_no_item_id(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            id1 = rq.enqueue_item(event_id="a", trigger="t", severity="WARN",
                                  reason="r1", context={})
            id2 = rq.enqueue_item(event_id="b", trigger="t", severity="WARN",
                                  reason="r2", context={})
            rq.take_action(item_id=id1, reviewer="alice", action="confirm")
            rq.take_action(item_id=id2, reviewer="bob", action="override")
            log = rq.get_audit_log()
            self.assertEqual(len(log), 2)


if __name__ == "__main__":
    unittest.main()
