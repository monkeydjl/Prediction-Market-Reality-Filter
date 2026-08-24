"""Unit tests for review_queue_store (Plan 4 §6.2)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_enqueue_is_idempotent_for_pending_same_event_trigger(self):
        """Re-enqueueing the same (event_id, trigger) while pending returns
        the existing item_id — no duplicate pending rows pile up during
        periodic orchestrator refresh.
        """
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            id1 = rq.enqueue_item(
                event_id="evt-001", trigger="high_value_downgraded",
                severity="WARN", reason="r1", context={"v": 1},
            )
            id2 = rq.enqueue_item(
                event_id="evt-001", trigger="high_value_downgraded",
                severity="WARN", reason="r1-updated", context={"v": 2},
            )
            self.assertEqual(id1, id2)
            self.assertEqual(len(rq.list_pending()), 1)
            # Context is refreshed on the existing row (latest detector run).
            item = rq.get_item(id1)
            self.assertEqual(item["context"], {"v": 2})
            self.assertEqual(item["reason"], "r1-updated")

    def test_enqueue_allows_re_enqueue_after_resolved(self):
        """After an item is resolved, a new enqueue for the same
        (event_id, trigger) creates a NEW pending row (re-review allowed).
        """
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            id1 = rq.enqueue_item(
                event_id="evt-001", trigger="source_market_conflict",
                severity="WARN", reason="r1", context={},
            )
            rq.take_action(item_id=id1, reviewer="alice",
                           action="confirm", note="")
            # Resolved — now re-enqueue should create a new row.
            id2 = rq.enqueue_item(
                event_id="evt-001", trigger="source_market_conflict",
                severity="WARN", reason="r2", context={},
            )
            self.assertNotEqual(id1, id2)
            pending = rq.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["item_id"], id2)
            self.assertEqual(len(rq.list_resolved()), 1)

    def test_enqueue_different_triggers_for_same_event_both_pending(self):
        """Same event_id but different triggers are independent items —
        both can be pending simultaneously.
        """
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            id1 = rq.enqueue_item(
                event_id="evt-001", trigger="high_value_downgraded",
                severity="WARN", reason="r1", context={},
            )
            id2 = rq.enqueue_item(
                event_id="evt-001", trigger="source_market_conflict",
                severity="WARN", reason="r2", context={},
            )
            self.assertNotEqual(id1, id2)
            self.assertEqual(len(rq.list_pending()), 2)


class TestReviewQueueSla(unittest.TestCase):
    """Q7: ordering, age, and the SLA aggregate.

    Ages are set by back-dating ``created_at`` with SQL rather than by sleeping,
    so the tests assert real hour arithmetic instead of sub-second deltas that
    round to zero.
    """

    @staticmethod
    def _backdate(item_id: str, hours: float, base: datetime | None = None) -> None:
        """Rewrite ``created_at`` to ``base - hours``, truncated to the second.

        ``base`` exists so a whole batch can share one instant. Deriving ``now``
        per call meant two rows given the *same* age still landed on different
        timestamps whenever the enqueue loop straddled a second boundary, which
        left the ordering tests asserting a tie that had silently not been set
        up -- a real 1-in-7 failure rate, and the assertion was never about the
        tie-break at all on those runs.
        """
        moment = (base if base is not None else datetime.now(timezone.utc))
        stamp = (moment - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
            conn.execute(
                "UPDATE review_queue_items SET created_at = ? WHERE item_id = ?",
                (stamp, item_id),
            )

    @staticmethod
    def _created_at_values() -> list[str]:
        """The raw stored timestamps, for tests that need to prove a tie exists."""
        with sqlite_db.reading(sqlite_db.loop_db_path()) as conn:
            return [
                str(row[0]) for row in conn.execute(
                    "SELECT created_at FROM review_queue_items"
                ).fetchall()
            ]

    def _queue(self, *specs):
        """Enqueue ``(event_id, severity, age_hours)`` triples; return the ids.

        One ``base`` for the batch: two specs with the same age must produce
        byte-identical ``created_at`` values, or the ties the ordering tests
        exercise depend on where the wall clock happens to fall.
        """
        base = datetime.now(timezone.utc)
        ids = []
        for index, (event_id, severity, age) in enumerate(specs):
            item_id = rq.enqueue_item(
                event_id=event_id, trigger=f"t{index}", severity=severity,
                reason=f"r{index}", context={},
            )
            self._backdate(item_id, age, base)
            ids.append(item_id)
        return ids

    def test_list_pending_is_oldest_first(self):
        """Pre-Q7 this was ``ORDER BY created_at DESC`` while the HTTP route
        truncated with ``items[:limit]``, so the longest-waiting item was both
        the last one displayed and the first one dropped."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            newest, middle, oldest = self._queue(
                ("evt-new", "WARN", 1.0),
                ("evt-mid", "ERROR", 10.0),
                ("evt-old", "WARN", 100.0),
            )
            self.assertEqual(
                [item["item_id"] for item in rq.list_pending()],
                [oldest, middle, newest],
            )

    def test_truncation_keeps_the_oldest(self):
        """The property the ordering exists for: whatever a caller drops must be
        the freshest items, never the one closest to breaching."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            ids = self._queue(*[
                (f"evt-{i}", "WARN", float(i)) for i in range(1, 6)
            ])
            first_two = [item["item_id"] for item in rq.list_pending()][:2]
            self.assertEqual(first_two, [ids[4], ids[3]])

    def test_ties_are_broken_deterministically(self):
        """``datetime('now')`` has one-second granularity, so a batch enqueue
        writes several rows with an identical timestamp."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            ids = self._queue(*[(f"evt-{i}", "WARN", 5.0) for i in range(6)])
            # Prove the premise before asserting on it. `created_at` is the
            # first ORDER BY key, so if the six rows do not genuinely share one
            # timestamp then nothing below exercises the item_id tie-break --
            # it just re-checks timestamp ordering under a misleading name.
            self.assertEqual(len(set(self._created_at_values())), 1)
            first = [item["item_id"] for item in rq.list_pending()]
            second = [item["item_id"] for item in rq.list_pending()]
            self.assertEqual(first, second)
            self.assertEqual(first, sorted(ids))

    def test_list_pending_reports_age_hours(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self._queue(("evt-1", "ERROR", 30.0))
            item = rq.list_pending()[0]
            self.assertAlmostEqual(item["age_hours"], 30.0, delta=0.05)
            self.assertEqual(item["severity_rank"], rq.SEVERITY_RANK["ERROR"])

    def test_created_at_survives_a_pending_refresh(self):
        """The load-bearing SLA invariant. The orchestrator re-runs detectors on
        every overlay build; if a refresh reset ``created_at`` no item could ever
        age and every breach count would read zero."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-1", trigger="high_value_downgraded",
                severity="WARN", reason="r1", context={"v": 1},
            )
            self._backdate(item_id, 200.0)
            before = rq.get_item(item_id)["created_at"]
            again = rq.enqueue_item(
                event_id="evt-1", trigger="high_value_downgraded",
                severity="ERROR", reason="r2", context={"v": 2},
            )
            self.assertEqual(again, item_id)
            after = rq.get_item(item_id)
            self.assertEqual(after["created_at"], before)
            self.assertEqual(after["severity"], "ERROR")
            self.assertAlmostEqual(
                rq.list_pending()[0]["age_hours"], 200.0, delta=0.05,
            )

    def test_resolved_items_carry_no_age(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(
                event_id="evt-1", trigger="t", severity="WARN",
                reason="r", context={},
            )
            rq.take_action(item_id=item_id, reviewer="alice", action="confirm")
            resolved = rq.list_resolved()[0]
            self.assertNotIn("age_hours", resolved)
            self.assertEqual(resolved["severity_rank"], 0)

    def test_enqueue_rejects_an_unknown_severity(self):
        """A detector typo used to persist silently: ``status`` has a column
        CHECK, ``severity`` does not."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            for severity in ("warn", "CRITICAL", "", "INFO", None):
                with self.subTest(severity=severity):
                    with self.assertRaises(ValueError):
                        rq.enqueue_item(
                            event_id="evt-1", trigger="t", severity=severity,
                            reason="r", context={},
                        )
            self.assertEqual(rq.list_pending(), [])

    def test_severity_rank_orders_error_above_warn(self):
        self.assertGreater(rq.SEVERITY_RANK["ERROR"], rq.SEVERITY_RANK["WARN"])
        self.assertEqual(rq.VALID_SEVERITIES, frozenset({"WARN", "ERROR"}))

    def test_sla_summary_counts_depth_oldest_and_breaches(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            ids = self._queue(
                ("evt-1", "ERROR", 40.0),   # breaches a 24h budget
                ("evt-2", "ERROR", 5.0),
                ("evt-3", "WARN", 100.0),   # breaches a 72h budget
                ("evt-4", "WARN", 10.0),
            )
            summary = rq.queue_sla_summary()
            self.assertEqual(summary["pending_total"], 4)
            self.assertAlmostEqual(summary["oldest_age_hours"], 100.0, delta=0.05)
            self.assertEqual(summary["oldest_item_id"], ids[2])
            self.assertEqual(summary["breached_total"], 2)
            self.assertEqual(summary["unknown_severity"], 0)
            self.assertEqual(summary["by_severity"]["ERROR"]["count"], 2)
            self.assertEqual(summary["by_severity"]["ERROR"]["breached"], 1)
            self.assertEqual(summary["by_severity"]["ERROR"]["sla_hours"], 24.0)
            self.assertAlmostEqual(
                summary["by_severity"]["ERROR"]["oldest_age_hours"], 40.0,
                delta=0.05,
            )
            self.assertEqual(summary["by_severity"]["WARN"]["breached"], 1)
            self.assertEqual(summary["by_trigger"], {"t0": 1, "t1": 1,
                                                     "t2": 1, "t3": 1})

    def test_sla_budgets_are_overridable(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self._queue(("evt-1", "ERROR", 40.0))
            self.assertEqual(rq.queue_sla_summary()["breached_total"], 1)
            self.assertEqual(
                rq.queue_sla_summary(sla_hours={"ERROR": 100.0, "WARN": 100.0})[
                    "breached_total"], 0,
            )

    def test_sla_summary_is_empty_on_an_empty_queue(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            summary = rq.queue_sla_summary()
            self.assertEqual(summary["pending_total"], 0)
            self.assertIsNone(summary["oldest_age_hours"])
            self.assertIsNone(summary["oldest_item_id"])
            self.assertEqual(summary["breached_total"], 0)
            self.assertEqual(summary["by_severity"], {})

    def test_sla_summary_ignores_resolved_items(self):
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            ids = self._queue(("evt-1", "ERROR", 40.0), ("evt-2", "ERROR", 40.0))
            rq.take_action(item_id=ids[0], reviewer="alice", action="confirm")
            summary = rq.queue_sla_summary()
            self.assertEqual(summary["pending_total"], 1)
            self.assertEqual(summary["breached_total"], 1)

    def test_a_severity_without_a_budget_is_reported_not_ignored(self):
        """A row written before the writer-side gate (or by hand) must not read
        as never-breaching."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            item_id = rq.enqueue_item(event_id="evt-1", trigger="t",
                                      severity="WARN", reason="r", context={})
            self._backdate(item_id, 500.0)
            with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
                conn.execute(
                    "UPDATE review_queue_items SET severity='NOTICE' "
                    "WHERE item_id = ?", (item_id,),
                )
            summary = rq.queue_sla_summary()
            self.assertEqual(summary["unknown_severity"], 1)
            self.assertEqual(summary["breached_total"], 0)
            self.assertIsNone(summary["by_severity"]["NOTICE"]["sla_hours"])
            self.assertEqual(rq.list_pending()[0]["severity_rank"], -1)

    def test_breach_needs_the_age_past_the_budget(self):
        """A budget bracketing the item's age either side, with a margin: the
        back-dated timestamp is truncated to the second and time passes before
        the read, so an exact-equality boundary is not observable here. The
        comparison itself is pinned in ``TestAgeParsing``."""
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self._queue(("evt-1", "ERROR", 24.0))
            self.assertEqual(
                rq.queue_sla_summary(sla_hours={"ERROR": 24.5})["breached_total"], 0,
            )
            self.assertEqual(
                rq.queue_sla_summary(sla_hours={"ERROR": 23.5})["breached_total"], 1,
            )

    def test_an_age_exactly_at_the_budget_is_not_a_breach(self):
        """The one exactly-representable equality case.

        ``_age_hours`` clamps a future ``created_at`` to 0.0, so a future-stamped
        item against a zero budget is the only way to compare an age with a
        budget it exactly equals — every back-dated age carries a sub-second
        remainder. A zero budget is a real setting ("breach immediately"), and at
        equality the item has not yet waited *past* what it was given.
        """
        with tempfile.TemporaryDirectory() as tmp, _db(tmp):
            self._queue(("evt-1", "WARN", -1.0))
            summary = rq.queue_sla_summary(sla_hours={"WARN": 0.0})
            self.assertEqual(summary["oldest_age_hours"], 0.0)
            self.assertEqual(summary["breached_total"], 0)
            self.assertEqual(
                rq.queue_sla_summary(sla_hours={"WARN": -0.5})["breached_total"], 1,
                "a budget below the age must still breach — the guard is the "
                "comparison, not a special case for zero",
            )


class TestAgeParsing(unittest.TestCase):
    def test_sqlite_timestamps_are_read_as_utc(self):
        """``datetime('now')`` writes a naive UTC string. Subtracting a naive
        datetime from an aware one raises TypeError, so the zone must be attached
        rather than assumed — the same trap that bit ``/today``."""
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(
            rq._age_hours("2026-08-24 06:00:00", now), 6.0, places=4,
        )
        self.assertAlmostEqual(
            rq._age_hours("2026-08-24T06:00:00+00:00", now), 6.0, places=4,
        )
        self.assertAlmostEqual(
            rq._age_hours("2026-08-24T09:00:00+03:00", now), 6.0, places=4,
        )

    def test_unparseable_timestamps_yield_none(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        for value in (None, "", "   ", "not-a-date", 17568, "2026-13-45 00:00:00"):
            with self.subTest(value=value):
                self.assertIsNone(rq._age_hours(value, now))

    def test_a_future_timestamp_clamps_to_zero(self):
        """Clock skew between writer and reader must not produce a negative age
        that sorts ahead of every real item."""
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(rq._age_hours("2026-08-25 12:00:00", now), 0.0)


if __name__ == "__main__":
    unittest.main()
