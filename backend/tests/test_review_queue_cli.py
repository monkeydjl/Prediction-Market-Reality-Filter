"""Tests for the review queue admin CLI (Q7).

The CLI was the only way to see the queue before the HTTP routes existed, and it
printed items newest-first with no age. These tests pin the two things an SLA
needs from it: ``list`` shows how long each item has waited, and ``sla`` reports
depth / oldest / breaches and **exits 1** when anything has breached, so it can
be wired into a check instead of read by eye.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from app.core.config import settings
from app.memory import review_queue_store as rq
from app.utils import sqlite_db


class TestReviewQueueCli(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            sqlite_db, "loop_db_path",
            return_value=str(Path(self.tmpdir.name) / "v2_loop.db"),
        )
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.tmpdir.cleanup()

    def _run(self, argv):
        import review_queue_cli as cli
        orig_stdout, orig_stderr = sys.stdout, sys.stderr
        try:
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            rc = cli.main(argv)
            return rc, sys.stdout.getvalue()
        finally:
            sys.stdout, sys.stderr = orig_stdout, orig_stderr

    def _enqueue_aged(self, event_id, severity, age_hours, *, trigger="t"):
        item_id = rq.enqueue_item(
            event_id=event_id, trigger=trigger, severity=severity,
            reason="需要人工复核", context={},
        )
        stamp = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
            conn.execute(
                "UPDATE review_queue_items SET created_at = ? WHERE item_id = ?",
                (stamp, item_id),
            )
        return item_id

    def test_list_empty_exits_zero(self):
        rc, out = self._run(["list"])
        self.assertEqual(rc, 0)
        self.assertIn("no items found", out)

    def test_list_prints_how_long_each_item_has_waited(self):
        self._enqueue_aged("evt-a", "ERROR", 30.0)
        rc, out = self._run(["list"])
        self.assertEqual(rc, 0)
        self.assertIn("age=", out)
        self.assertIn("30.0", out)

    def test_list_is_oldest_first(self):
        self._enqueue_aged("evt-new", "WARN", 2.0, trigger="fresh")
        self._enqueue_aged("evt-old", "WARN", 99.0, trigger="stale")
        _, out = self._run(["list"])
        self.assertLess(out.index("stale"), out.index("fresh"))

    def test_resolved_items_show_no_age(self):
        """A resolved item is not waiting, so it carries no age at all — the
        label has to survive that rather than crash or print 0.0."""
        item_id = self._enqueue_aged("evt-a", "WARN", 5.0)
        rq.take_action(item_id=item_id, reviewer="alice", action="confirm")
        rc, out = self._run(["list", "--status", "resolved"])
        self.assertEqual(rc, 0)
        self.assertIn("age=   ?  h", out)

    def test_sla_reports_depth_and_exits_zero_without_breaches(self):
        self._enqueue_aged("evt-a", "WARN", 1.0)
        rc, out = self._run(["sla", "--error-hours", "24", "--warn-hours", "72"])
        self.assertEqual(rc, 0)
        self.assertIn("pending=1", out)
        self.assertIn("breached=0", out)
        self.assertNotIn("[FAIL]", out)

    def test_sla_exits_one_when_something_has_breached(self):
        """The reason the command exists: a breach has to be detectable by a
        check, not only visible to a human reading the table."""
        self._enqueue_aged("evt-a", "ERROR", 30.0)
        rc, out = self._run(["sla", "--error-hours", "24", "--warn-hours", "72"])
        self.assertEqual(rc, 1)
        self.assertIn("breached=1", out)
        self.assertIn("past SLA", out)

    def test_sla_budgets_default_to_the_settings(self):
        self._enqueue_aged("evt-a", "ERROR", 10.0)
        with patch.object(settings, "REVIEW_QUEUE_SLA_ERROR_HOURS", 1.0), \
                patch.object(settings, "REVIEW_QUEUE_SLA_WARN_HOURS", 2.0):
            tight_rc, _ = self._run(["sla"])
        with patch.object(settings, "REVIEW_QUEUE_SLA_ERROR_HOURS", 100.0), \
                patch.object(settings, "REVIEW_QUEUE_SLA_WARN_HOURS", 200.0):
            loose_rc, _ = self._run(["sla"])
        self.assertEqual((tight_rc, loose_rc), (1, 0))

    def test_sla_lists_severities_most_urgent_first(self):
        self._enqueue_aged("evt-a", "WARN", 1.0, trigger="t-warn")
        self._enqueue_aged("evt-b", "ERROR", 2.0, trigger="t-error")
        _, out = self._run(["sla"])
        self.assertLess(out.index("ERROR"), out.index("WARN "))

    def test_sla_flags_a_row_whose_severity_has_no_budget(self):
        """Rows written before the severity gate existed can carry anything. A
        severity with no budget can never breach, so it has to be reported as
        unbudgeted rather than counted as healthy."""
        self._enqueue_aged("evt-a", "WARN", 1.0)
        with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
            conn.execute(
                "UPDATE review_queue_items SET severity = 'CRITICAL' "
                "WHERE event_id = 'evt-a'"
            )
        rc, out = self._run(["sla"])
        self.assertEqual(rc, 0)
        self.assertIn("[WARN]", out)
        self.assertIn("1 item(s) carry a severity", out)

    def test_sla_on_an_empty_queue_exits_zero(self):
        rc, out = self._run(["sla"])
        self.assertEqual(rc, 0)
        self.assertIn("pending=0", out)
        self.assertIn("oldest=n/a", out)

    def test_action_then_audit_round_trip(self):
        item_id = self._enqueue_aged("evt-a", "WARN", 1.0)
        rc, _ = self._run(["action", "--item-id", item_id,
                           "--reviewer", "alice", "--action", "confirm",
                           "--note", "已核对来源"])
        self.assertEqual(rc, 0)
        rc, out = self._run(["audit", "--item-id", item_id])
        self.assertEqual(rc, 0)
        self.assertIn("confirm", out)
        self.assertIn("alice", out)

    def test_action_on_unknown_item_exits_one(self):
        rc, out = self._run(["action", "--item-id", "nope",
                             "--reviewer", "alice", "--action", "confirm"])
        self.assertEqual(rc, 1)
        self.assertIn("[FAIL]", out)


if __name__ == "__main__":
    unittest.main()
