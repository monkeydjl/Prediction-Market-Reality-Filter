"""Tests for loop_status_service — the /api/health and /events/loop/status body.

Focus is the orphan-prediction count, which used to issue one SELECT per
resolved event. /api/health is polled by container healthchecks and uptime
monitors, so a per-event query made the endpoint scale linearly with the event
store (measured at ~1.2s) while holding the event loop the whole time.

``ReviewQueueCountsTests`` covers the Q7 addition: review-queue depth, oldest
wait and SLA breach counts, which the payload previously reported nowhere.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.memory import decision_timeline_store as timeline
from app.memory import event_market_link_store as links
from app.memory import event_ref_census
from app.memory import event_store as store
from app.memory import loop_run_store
from app.memory import prediction_store as preds
from app.memory import review_queue_store as rq
from app.memory import simulated_trade_store as trades
from app.services import loop_status_service
from app.utils import sqlite_db

from tests.conftest import minimal_loop_status_payload
from tests.test_event_store import _make_record


def _market_record(event_id, *, estimated=70.0, baseline=50.0):
    """An event record that freeze_prediction will accept (market-gated)."""
    record = _make_record(event_id, estimated=estimated)
    record["probability"]["baseline"] = baseline
    record["source"] = {
        "type": "prediction_market",
        "platform": "Polymarket",
        "source_id": f"contract-{event_id}",
    }
    return record


class OrphanPredictionCountTests(unittest.TestCase):
    """Resolved events whose prediction is still open."""

    UNREADABLE = sqlite3.OperationalError("no such table: predictions")

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.db_path = str(base / "v2_loop.db")
        self.store_path = str(base / "event_store.json")

        self.patches = [
            patch.object(sqlite_db, "loop_db_path", return_value=self.db_path),
            patch.object(store, "_store_path", return_value=self.store_path),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmpdir.cleanup()

    def _seed(self, n_resolved, n_unresolved=0):
        """Store n_resolved resolved events with open predictions."""
        for i in range(n_resolved):
            record = _market_record(f"res-{i}")
            store.save_event(record)
            preds.freeze_prediction(record)
            store.resolve_event(
                f"res-{i}",
                {
                    "status": "resolved",
                    "actual_outcome": 100.0,
                    "confidence": 0.9,
                    "resolved_at": "2026-01-01T00:00:00+00:00",
                    "source": "manual",
                },
            )
        for i in range(n_unresolved):
            record = _market_record(f"open-{i}")
            store.save_event(record)
            preds.freeze_prediction(record)

    def test_counts_resolved_events_whose_prediction_is_still_open(self):
        self._seed(n_resolved=3, n_unresolved=2)

        events = store.list_all_events()
        count, readable = loop_status_service._orphan_prediction_count(events)

        # The 3 resolved events kept their predictions open; the 2 unresolved
        # ones are not orphans no matter what their prediction says.
        self.assertEqual(count, 3)
        self.assertTrue(readable, "a successful read reported itself as unreadable")

    def test_no_resolved_events_short_circuits_without_querying(self):
        self._seed(n_resolved=0, n_unresolved=2)

        events = store.list_all_events()
        with patch.object(
            sqlite_db, "connect", side_effect=AssertionError("queried")
        ):
            count, readable = loop_status_service._orphan_prediction_count(events)

        self.assertEqual(count, 0)
        self.assertTrue(readable, "no query ran, so the answer cannot be unreadable")

    def test_query_count_does_not_grow_with_the_event_store(self):
        """The N+1 regression guard.

        The old implementation called get_prediction() per resolved event, so
        the query count tracked the store size exactly. Counting is done at
        ``sqlite_db.connect`` - the chokepoint both the old and new code paths
        share - because each module imports its own ``reading`` binding, so
        patching one module's ``reading`` would silently miss the other's.
        Counting statements (rather than wall-clock) keeps this deterministic
        on a loaded machine.
        """
        self._seed(n_resolved=12)
        events = store.list_all_events()

        selects = []
        real_connect = sqlite_db.connect

        class _CountingConn:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args, **kwargs):
                if sql.strip().upper().startswith("SELECT"):
                    selects.append(sql)
                return self._conn.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        with patch.object(
            sqlite_db, "connect", lambda path: _CountingConn(real_connect(path))
        ):
            count, readable = loop_status_service._orphan_prediction_count(events)

        self.assertEqual(count, 12)
        self.assertTrue(readable)
        self.assertLessEqual(
            len(selects),
            1,
            f"orphan count issued {len(selects)} SELECTs for 12 events; it must "
            "not scale with the event store",
        )

    def test_the_switch_does_not_say_which_query_broke(self):
        """One switch per table (53.117) buys an honest boolean and loses a name.

        Both queries read the same table, so ``predictions_available`` goes false
        whichever one failed, and the operator cannot tell from it which query to
        look at. **The log sentence is the only carrier of that fact**, so each
        query owns its own sentence and a test has to hold them apart: before this
        one existed, replacing the orphan sentence with the counts sentence left
        the whole file green (measured: 10 passed, junit failures 0), so a
        collapsed pair would name the wrong query (53.124: a local fact lost is an
        incident, not a simplification).

        A resolved event has to be seeded first -- ``_orphan_prediction_count``
        short-circuits before querying when there is nothing resolved, and a test
        that skips the seeding passes on an orphan warning that was never written
        (measured: the first version of this test read only one message).
        """
        self._seed(n_resolved=1)

        with self.assertLogs("app.services.loop_status_service", level="WARNING") as logs:
            with patch.object(loop_status_service, "reading", side_effect=self.UNREADABLE):
                payload = loop_status_service.loop_status()

        messages = [record.getMessage() for record in logs.records]
        orphans = [m for m in messages if "orphan prediction" in m]
        counts = [m for m in messages if "prediction status counts" in m]

        self.assertEqual(len(orphans), 1, f"the orphan query's failure is unnamed: {messages}")
        self.assertEqual(len(counts), 1, f"the counts query's failure is unnamed: {messages}")
        self.assertNotEqual(orphans[0], counts[0], "one sentence for two different queries")
        self.assertFalse(
            payload["counts"]["predictions_available"],
            "a real failed read did not reach the switch",
        )


class ReviewQueueCountsTests(unittest.TestCase):
    """Q7: the review backlog had no number anywhere in the status payload.

    ``counts.pending_links`` reads ``event_market_link_store`` — a different
    store — and was the only "pending" figure here, so a human review queue of
    any depth was invisible from /api/health.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.patches = [
            patch.object(sqlite_db, "loop_db_path",
                         return_value=str(base / "v2_loop.db")),
            patch.object(store, "_store_path",
                         return_value=str(base / "event_store.json")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmpdir.cleanup()

    @staticmethod
    def _enqueue_aged(event_id, severity, age_hours):
        item_id = rq.enqueue_item(
            event_id=event_id, trigger="high_value_downgraded",
            severity=severity, reason="需要人工复核", context={},
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

    def test_counts_report_review_depth_and_breaches(self):
        with patch.object(settings, "REVIEW_QUEUE_SLA_ERROR_HOURS", 24.0), \
                patch.object(settings, "REVIEW_QUEUE_SLA_WARN_HOURS", 72.0):
            overdue = self._enqueue_aged("evt-a", "ERROR", 30.0)
            self._enqueue_aged("evt-b", "WARN", 5.0)
            payload = loop_status_service.loop_status()

        self.assertEqual(payload["counts"]["pending_reviews"], 2)
        self.assertEqual(payload["counts"]["breached_reviews"], 1)
        self.assertEqual(payload["review_queue"]["oldest_item_id"], overdue)
        self.assertEqual(payload["review_queue"]["by_severity"]["ERROR"]["breached"], 1)

    def test_pending_reviews_is_not_pending_links(self):
        """The two numbers count different stores; conflating them is how the
        review backlog stayed invisible while a "pending" figure was already
        on screen."""
        self._enqueue_aged("evt-a", "WARN", 1.0)
        self._enqueue_aged("evt-b", "WARN", 2.0)
        counts = loop_status_service.loop_status()["counts"]
        self.assertEqual(counts["pending_reviews"], 2)
        self.assertEqual(counts["pending_links"], 0)

    def test_configured_budgets_are_forwarded_to_the_store(self):
        with patch.object(settings, "REVIEW_QUEUE_SLA_ERROR_HOURS", 1.5), \
                patch.object(settings, "REVIEW_QUEUE_SLA_WARN_HOURS", 2.5), \
                patch.object(rq, "queue_sla_summary",
                             return_value={"pending_total": 0,
                                           "breached_total": 0}) as spy:
            loop_status_service._review_queue_counts()
        self.assertEqual(spy.call_args.kwargs["sla_hours"],
                         {"ERROR": 1.5, "WARN": 2.5})

    def test_an_unreadable_queue_degrades_instead_of_raising(self):
        """/api/health answering 500 because the review queue is unreadable is
        worse than it reporting an empty queue — same posture as
        ``_prediction_counts``."""
        with patch.object(rq, "queue_sla_summary",
                          side_effect=RuntimeError("db gone")):
            fallback = loop_status_service._review_queue_counts()
            payload = loop_status_service.loop_status()

        self.assertEqual(fallback["pending_total"], 0)
        self.assertEqual(fallback["breached_total"], 0)
        self.assertEqual(payload["counts"]["pending_reviews"], 0)
        self.assertEqual(payload["counts"]["breached_reviews"], 0)
        self.assertFalse(
            fallback["available"],
            "a failed read reported itself as an empty queue (53.118)",
        )
        self.assertFalse(
            payload["review_queue"]["available"],
            "the payload copy of the switch says those four zeros are real",
        )

    def test_the_fallback_shape_matches_the_real_summary(self):
        """A key the fallback omits becomes a KeyError only on the failure path,
        i.e. exactly when nothing is left to catch it.

        Compared against the *wrapper*, not against ``queue_sla_summary``:
        since 53.118 the wrapper adds ``available``, so comparing to the store
        would flag that key as drift on both paths.
        """
        live = loop_status_service._review_queue_counts()
        with patch.object(rq, "queue_sla_summary",
                          side_effect=RuntimeError("db gone")):
            fallback = loop_status_service._review_queue_counts()
        self.assertEqual(set(fallback), set(live))

    def test_a_readable_queue_reports_itself_as_read(self):
        payload = loop_status_service.loop_status()

        self.assertTrue(payload["review_queue"]["available"])

    def test_the_queue_failure_logs_the_type_and_no_traceback(self):
        """Same rule as the predictions reads (53.107/53.118): the type is the
        safe half, and a traceback in ``logs/`` on every poll is not free."""
        with self.assertLogs("app.services.loop_status_service", level="WARNING") as logs:
            with patch.object(rq, "queue_sla_summary",
                              side_effect=RuntimeError("db gone")):
                loop_status_service._review_queue_counts()

        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertIsNone(record.exc_info, "the failure put a traceback in the log")
        self.assertIn("RuntimeError", record.getMessage(), "the safe half is missing")
        self.assertNotIn("db gone", record.getMessage(), "the message leaked the reason")

    def test_the_block_echoes_the_feature_flag(self):
        """`enabled` separates a disabled queue from a drained one.

        The 53.118 probe measured the two states as byte-identical, which is
        why `GET /review-queue/sla` echoes the flag and this block did not.
        """
        with patch.object(settings, "REVIEW_QUEUE_ENABLED", False):
            off = loop_status_service.loop_status()["review_queue"]
        with patch.object(settings, "REVIEW_QUEUE_ENABLED", True):
            on = loop_status_service.loop_status()["review_queue"]

        self.assertFalse(off["enabled"])
        self.assertTrue(on["enabled"])
        self.assertEqual(
            {k: v for k, v in off.items() if k != "enabled"},
            {k: v for k, v in on.items() if k != "enabled"},
            "the flag changed more than itself; only `enabled` may differ",
        )

    def test_available_and_enabled_answer_different_questions(self):
        """A failed read is not a disabled feature, and vice versa."""
        with patch.object(rq, "queue_sla_summary",
                          side_effect=RuntimeError("db gone")), \
                patch.object(settings, "REVIEW_QUEUE_ENABLED", True):
            block = loop_status_service.loop_status()["review_queue"]

        self.assertFalse(block["available"], "a failed read is not 'disabled'")
        self.assertTrue(
            block["enabled"],
            "the flag was reported as off because the read failed",
        )


class PredictionCountsTests(unittest.TestCase):
    """§5 at its narrowest: `{}` from an empty table vs `{}` from a broken query.

    Measured before 53.116: both paths returned the same empty map, so anything
    reading ``counts.predictions`` -- the dashboard badge included -- showed a
    confident zero while the loop database was unreadable. The same handler also
    logged its failure with ``exc_info=True``, and this endpoint is polled every
    few seconds, so a broken database meant a five-line traceback per poll in
    ``logs/`` (§53.107: a persistent surface).
    """

    UNREADABLE = sqlite3.OperationalError("no such table: predictions")

    def test_a_readable_table_reports_itself_as_read(self):
        """The consumer-visible value, not the helper in isolation.

        ``loop_status()`` reads the calibration summary first, and that is what
        creates the ``predictions`` table (53.114), so "readable" is the normal
        case by the time the counts query runs. A bare ``_prediction_counts()``
        against a database nothing has touched yet reports ``available: False``
        -- honest ("unknown"), but not something a caller of ``loop_status()``
        ever sees, so asserting on it here would pin the wrong observation point.
        """
        counts = loop_status_service.loop_status()["counts"]

        self.assertTrue(counts["predictions_available"])

    def test_a_broken_query_is_not_an_empty_table(self):
        with patch.object(loop_status_service, "reading", side_effect=self.UNREADABLE):
            counts, available = loop_status_service._prediction_counts()

        self.assertEqual(counts, {}, "the fallback shape changed; callers expect a dict")
        self.assertFalse(available, "a failed read reported itself as an empty table")

    def test_the_failure_logs_the_type_and_no_traceback(self):
        """The type name is the safe half of the exception; the message is not.

        ``OperationalError``'s text here is ``no such table: predictions``, and
        with a different failure it would carry the database path. ``exc_info``
        would also print the traceback, which points at ``sqlite_db.reading``
        and the absolute path of the store.
        """
        with self.assertLogs("app.services.loop_status_service", level="WARNING") as logs:
            with patch.object(loop_status_service, "reading", side_effect=self.UNREADABLE):
                loop_status_service._prediction_counts()

        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertIsNone(record.exc_info, "the failure put a traceback in the log")
        self.assertIn("OperationalError", record.getMessage(), "the safe half is missing")
        self.assertNotIn("no such table", record.getMessage(), "the message leaked the reason text")

    def test_the_counts_block_has_the_same_keys_on_both_paths(self):
        """A key written only on the success path is a KeyError waiting for the
        failure path -- the same lesson as the review-queue fallback above."""
        whole = loop_status_service.loop_status()["counts"]
        with patch.object(loop_status_service, "reading", side_effect=self.UNREADABLE):
            broken = loop_status_service.loop_status()["counts"]

        self.assertEqual(set(broken), set(whole))
        self.assertFalse(broken["predictions_available"])
        self.assertTrue(whole["predictions_available"])

    def test_a_failed_orphan_read_makes_the_switch_false(self):
        """One switch per table (53.117): the orphan count rides it too.

        Patched at the seam rather than through ``reading``: both queries go
        through ``reading``, so breaking it cannot tell the two cases apart --
        and what this test is about is exactly the combination ``loop_status``
        performs.
        """
        with patch.object(
            loop_status_service, "_orphan_prediction_count", return_value=(0, False)
        ):
            counts = loop_status_service.loop_status()["counts"]

        self.assertEqual(counts["orphan_predictions"], 0)
        self.assertFalse(
            counts["predictions_available"],
            "a failed orphan read left the switch true, so its 0 reads as 'no orphans'",
        )

    def test_the_orphan_count_reports_itself_unreadable(self):
        events = [{"event_id": "evt-1", "record": {"outcome": "yes"}}]
        with self.assertLogs("app.services.loop_status_service", level="WARNING") as logs:
            with patch.object(loop_status_service, "reading", side_effect=self.UNREADABLE):
                count, readable = loop_status_service._orphan_prediction_count(events)

        self.assertEqual(count, 0, "the fallback shape changed")
        self.assertFalse(readable, "a failed query reported itself as a clean zero")
        record = logs.records[0]
        self.assertIsNone(record.exc_info, "the failure put a traceback in the log")
        self.assertIn("OperationalError", record.getMessage(), "the safe half is missing")
        self.assertNotIn("no such table", record.getMessage(), "the message leaked the reason")

    def test_nothing_resolved_means_nothing_to_read(self):
        """No resolved events => no query ran => the answer cannot be unreadable."""
        count, readable = loop_status_service._orphan_prediction_count([])

        self.assertEqual(count, 0)
        self.assertTrue(readable, "reported a failure for a query that never ran")


class WatchedJobSetTests(unittest.TestCase):
    """The `runs` block named three jobs; the ledger holds fifteen.

    Measured on the live ledger: 15 distinct job names over 1706 rows, and the
    only one whose last run had failed -- ``world_cup_api_football_validate``,
    2026-07-07, "API-Football returned 0 fixtures" -- was not one of the three,
    so ``/api/health`` answered 200 "ok" and ``scripts/healthcheck.py`` kept
    feeding the dead-man switch. ``recent_runs`` had the mirror problem: one
    chatty job holds 1193 of those rows and its newest 20 postdate every other
    job, so the 20-row window carried one distinct name.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.patches = [
            patch.object(sqlite_db, "loop_db_path", return_value=str(base / "v2_loop.db")),
            patch.object(store, "_store_path", return_value=str(base / "event_store.json")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmpdir.cleanup()

    def _run(self, job_name, status="success", **kw):
        run_id = loop_run_store.start_run(job_name)
        loop_run_store.finish_run(run_id, status, **kw)
        return run_id

    def _backdate_failed_run(self, job_name, started_at, error="boom"):
        """A failed run stamped in the past -- what a previous boot leaves.

        The store has no API for backdating on purpose, so the test writes the
        column directly; seeding through start_run would make every row look
        like it came from this process, which is exactly the distinction under
        test.
        """
        run_id = self._run(job_name, "failed", error=error)
        with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
            conn.execute(
                "UPDATE loop_runs SET started_at=?, finished_at=? WHERE id=?",
                (started_at, started_at, run_id),
            )
        return run_id

    def test_a_failed_job_outside_the_old_three_is_reported(self):
        self._run("event_discover")
        self._run("world_cup_api_football_validate", "failed", error="0 fixtures")

        runs = loop_status_service.loop_status()["runs"]

        self.assertEqual(runs["world_cup_api_football_validate"]["status"], "failed")
        self.assertEqual(runs["event_discover"]["status"], "success")

    def test_health_answers_degraded_for_a_job_outside_the_old_three(self):
        """The whole point of the derived set: this is the live ledger's one
        failing job, and the endpoint backing the container healthcheck and the
        dead-man switch used to answer 200 "ok" while it sat there failed."""
        from fastapi.testclient import TestClient

        from app.main import app

        self._run("event_discover")
        self._run("world_cup_api_football_validate", "failed", error="0 fixtures")

        with patch.object(settings, "SCHEDULER_ENABLED", False):
            resp = TestClient(app).get("/api/health")

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["failed_runs"], ["world_cup_api_football_validate"])

    def test_a_failed_run_from_a_previous_boot_does_not_degrade_health(self):
        """53.99: the live defect, reproduced.

        ``event_discover_startup`` is a one-shot job that failed on 2026-09-10
        and nothing will ever supersede that row, so /api/health answered 503
        for eight months. A run nothing will retry is a record, not a state.
        """
        from fastapi.testclient import TestClient

        from app.main import app

        self._run("event_discover")
        self._backdate_failed_run(
            "event_discover_startup", "2026-09-10T19:51:22+00:00"
        )

        with patch.object(settings, "SCHEDULER_ENABLED", False):
            resp = TestClient(app).get("/api/health")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["failed_runs"], [])
        self.assertEqual(body["stale_failed_runs"], ["event_discover_startup"])
        # Still visible: the row is history, not something to hide.
        self.assertEqual(
            body["loop"]["runs"]["event_discover_startup"]["status"], "failed"
        )

    def test_a_failure_from_this_boot_still_degrades_health(self):
        """The half that must not be lost: a startup job failing on *this* boot
        is a condition of this instance, and no restart clears it -- only a
        restart does, which is the point."""
        from fastapi.testclient import TestClient

        from app.main import app

        self._run("startup_sqlite_integrity", "failed", error="volume went away")

        with patch.object(settings, "SCHEDULER_ENABLED", False):
            resp = TestClient(app).get("/api/health")

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["failed_runs"], ["startup_sqlite_integrity"])
        self.assertEqual(body["stale_failed_runs"], [])

    def test_a_scheduled_job_still_degrades_after_a_restart(self):
        """An interval/cron job will run again, so its last run describes the
        pipeline now even when it happened before this process started."""
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from app.core.scheduler import scheduler as real_scheduler
        from app.main import app

        self._backdate_failed_run("loop_db_maintenance", "2026-07-24T22:45:00+00:00")

        with patch.object(settings, "SCHEDULER_ENABLED", False), patch.object(
            real_scheduler,
            "get_jobs",
            return_value=[SimpleNamespace(id="loop_db_maintenance")],
        ):
            resp = TestClient(app).get("/api/health")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["failed_runs"], ["loop_db_maintenance"])
        self.assertEqual(resp.json()["stale_failed_runs"], [])

    def test_every_ledger_job_name_appears_in_the_runs_block(self):
        """A hand-maintained list is what let twelve names go unwatched, so the
        assertion is an exact partition against the ledger's own names."""
        seeded = {
            "event_discover", "event_auto_resolve", "loop_db_maintenance",
            "backup_stores", "world_cup_scoring_reconcile", "translate_titles",
            "sport_market_discover", "world_cup_api_football_validate",
        }
        for name in sorted(seeded):
            self._run(name)

        runs = loop_status_service.loop_status()["runs"]

        self.assertEqual(set(runs), seeded)

    def test_a_chatty_job_no_longer_empties_the_recent_window(self):
        for name in ("event_discover", "event_auto_resolve", "loop_db_maintenance"):
            self._run(name)
        for _ in range(25):
            self._run("world_cup_scoring_reconcile")

        recent = loop_status_service.loop_status()["recent_runs"]

        self.assertGreater(
            len({run["job_name"] for run in recent}),
            1,
            f"the recent window carried one job name: {recent}",
        )
        self.assertEqual(
            {run["job_name"] for run in recent},
            {
                "event_discover", "event_auto_resolve",
                "loop_db_maintenance", "world_cup_scoring_reconcile",
            },
        )

    def test_run_details_stay_behind_the_write_key(self):
        """The sanitising wrapper applies to every derived name, not just the
        three that used to be listed."""
        self._run("world_cup_api_football_validate", "failed", error="0 fixtures")

        sanitized = loop_status_service.loop_status()
        detailed = loop_status_service.loop_status(include_run_details=True)

        run = sanitized["runs"]["world_cup_api_football_validate"]
        self.assertEqual(set(run), {"job_name", "status", "started_at", "finished_at", "duration_ms"})
        self.assertNotIn("error", run)
        self.assertNotIn("error", sanitized["recent_runs"][0])
        self.assertEqual(
            detailed["runs"]["world_cup_api_football_validate"]["error"], "0 fixtures"
        )

    def test_an_empty_ledger_reports_no_runs_rather_than_raising(self):
        payload = loop_status_service.loop_status()

        self.assertEqual(payload["runs"], {})
        self.assertEqual(payload["recent_runs"], [])

    def test_the_run_queries_do_not_open_one_connection_per_job_name(self):
        """A per-name loop over `last_run` is the shape this replaced: 15 store
        calls of 533 ms against 4 of 101 ms on the live ledger. Counted at
        ``sqlite_db.connect``, the chokepoint every module's ``reading`` binding
        shares. Measured on the two store calls rather than the whole payload,
        because the payload's other stores create their schemas on first use and
        that noise is larger than the signal.

        ``recent_runs_by_job`` does issue one indexed query per distinct name --
        deliberately, and bounded by the job names written in code -- but inside
        a single connection scope, so neither call scales its connections.
        """
        real_connect = sqlite_db.connect

        def _count() -> int:
            opens: list[str] = []
            with patch.object(
                sqlite_db,
                "connect",
                lambda path: opens.append(path) or real_connect(path),
            ):
                loop_run_store.latest_run_per_job()
                loop_run_store.recent_runs_by_job(limit=20)
            return len(opens)

        self._run("probe_job_0")
        few = _count()
        for i in range(1, 13):
            self._run(f"probe_job_{i}")
        many = _count()

        self.assertEqual(
            few,
            many,
            f"the run queries opened {few} connections for 1 job name and {many} "
            "for 13; they must not scale with the job count",
        )


class EventStoreSizeTests(unittest.TestCase):
    """E1: the JSON store's size had no reading anywhere.

    Every mutating ``event_store`` call rewrites the whole file — 237 ms for the
    live 3.455 MB store, with the cross-process lock held throughout — so the
    day the format stops being viable would first have been felt as a slow
    dashboard. ``storage`` now carries the file size and the record count so an
    operator can watch them grow together.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.store_path = str(base / "event_store.json")
        self.patches = [
            patch.object(sqlite_db, "loop_db_path", return_value=str(base / "v2_loop.db")),
            patch.object(store, "_store_path", return_value=self.store_path),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_status_reports_the_event_store_size_on_disk(self):
        store.save_events([_make_record(f"size-{i}") for i in range(4)])
        storage = loop_status_service.loop_status()["storage"]
        self.assertEqual(storage["event_store_bytes"], Path(self.store_path).stat().st_size)
        self.assertEqual(storage["event_store_records"], 4)

    def test_a_fresh_deploy_with_no_store_file_reports_zero(self):
        """The status endpoint backs /api/health; raising here would take the
        container healthcheck down on first boot."""
        self.assertFalse(Path(self.store_path).exists())
        storage = loop_status_service.loop_status()["storage"]
        self.assertEqual(storage["event_store_bytes"], 0)
        self.assertEqual(storage["event_store_records"], 0)

    def test_the_store_size_reading_adds_no_extra_whole_file_pass(self):
        """loop_status already loads the store for its counts. A size reading
        that re-read and re-parsed the file would double the cost of the
        endpoint container healthchecks poll — the amplification it exists to
        expose. The same pass now also serves ``resolved``, which used to be a
        second full read of its own."""
        store.save_events([_make_record(f"size-{i}") for i in range(3)])

        reads = []
        real_read = store.read_json

        def counting(path, fallback):
            reads.append(path)
            return real_read(path, fallback)

        with patch.object(store, "read_json", counting):
            payload = loop_status_service.loop_status()

        self.assertEqual(payload["storage"]["event_store_records"], 3)
        self.assertEqual(
            len(reads), 1,
            f"loop_status parsed the event store {len(reads)} times; both the "
            "size reading and the resolved count must reuse the one load",
        )

    def test_resolved_count_is_the_same_whether_the_list_is_passed_or_read(self):
        """The pre-loaded path must not have its own resolved predicate: an
        "invalid" outcome is settled but uncalibrated, and a copy of the rule
        that forgot that would quietly change what enters the Brier aggregate."""
        outcome = {
            "status": "resolved", "actual_outcome": 100.0, "confidence": 0.9,
            "resolved_at": "2026-01-01T00:00:00+00:00", "source": "manual",
        }
        invalid = dict(outcome, status="invalid")
        store.save_events([_make_record(f"res-{i}") for i in range(4)])
        store.resolve_event("res-0", outcome)
        store.resolve_event("res-1", outcome)
        store.resolve_event("res-2", invalid)

        read_itself = store.list_resolved_events()
        passed_in = store.list_resolved_events(store.list_all_events())

        self.assertEqual(
            [e["event_id"] for e in read_itself],
            [e["event_id"] for e in passed_in],
        )
        self.assertEqual({e["event_id"] for e in passed_in}, {"res-0", "res-1"})
        self.assertEqual(
            loop_status_service.loop_status()["counts"]["resolved_events"], 2,
        )


class DanglingRefCountTests(unittest.TestCase):
    """E2: the ``引用异常`` badge summed two tables while six carried an event_id.

    ``loop_status`` reports how many SQLite rows point at an event that no longer
    exists in the JSON store — the only thing standing in for a foreign key that
    cannot span a JSON file and a database. It watched ``predictions`` and
    ``event_market_links`` only, so the one genuinely stranded row in the live
    database (in ``simulated_trades``) was invisible and the badge read 0.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.db = str(base / "v2_loop.db")
        self.patches = [
            patch.object(sqlite_db, "loop_db_path", return_value=self.db),
            # simulated_trade_store does `from ...sqlite_db import loop_db_path`,
            # so it holds its own binding and the patch above does not reach it.
            # Without this second patch its writes land in the real backend/
            # v2_loop.db -- the per-module-binding trap, and here it would also
            # make the assertions read zero against an untouched tmp database.
            patch.object(trades, "loop_db_path", return_value=self.db),
            patch.object(store, "_store_path", return_value=str(base / "event_store.json")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmpdir.cleanup()

    def _strand(self, table, event_id):
        """Write a real row referencing ``event_id`` through the owning store.

        Deliberately through each store's public API rather than a hand-made
        ``CREATE TABLE x (event_id TEXT)``: a stub table shadows the real schema
        (``CREATE TABLE IF NOT EXISTS`` means the stub wins, and ``loop_status``
        then cannot read ``predictions.status``), and a test built on stubs would
        keep passing if a column were renamed under it.

        The event is never saved to the JSON store, which is what makes the row
        stranded — the same end state ``DELETE /events/{event_id}`` leaves.

        Note ``predictions`` is not isolated: ``freeze_prediction`` seeds a
        verified ``event_market_links`` row on first freeze, so stranding a
        prediction strands a link too. That is production behaviour, and the
        assertions below account for it rather than working around it.
        """
        record = _market_record(event_id)
        if table == "predictions":
            preds.freeze_prediction(record)
        elif table == "event_market_links":
            links.upsert_link(
                event_id, contract_id=f"c-{event_id}", market_name="m",
            )
        elif table == "simulated_trades":
            trades.open_trade(
                event_id, "t", direction="YES", entry_prob=70.0, market_prob=50.0,
            )
        elif table == "review_queue_items":
            rq.enqueue_item(
                event_id=event_id, trigger="high_value_downgraded",
                severity="WARN", reason="r", context={},
            )
        elif table == "decision_timeline":
            timeline.record_snapshot(record)
        else:  # pragma: no cover - a new table needs a seeding arm here
            raise AssertionError(f"no seeding arm for {table}")

    def test_a_stranded_trade_is_now_visible_in_the_total(self):
        """The live defect, reproduced: the only broken reference in the real
        database was an open simulated trade, and the badge read 0."""
        store.save_events([_make_record("evt-live")])
        self._strand("simulated_trades", "evt-gone")
        counts = loop_status_service.loop_status()["counts"]
        # The two published keys still describe their own tables ...
        self.assertEqual(counts["dangling_predictions"], 0)
        self.assertEqual(counts["dangling_links"], 0)
        # ... and the total no longer agrees with them, which is the point.
        self.assertEqual(counts["dangling_refs"], 1)
        self.assertEqual(counts["dangling_by_table"]["simulated_trades"], 1)

    def test_an_open_stranded_trade_is_actionable(self):
        """53.95: the row still has a settlement step blocked by the event."""
        store.save_events([_make_record("evt-live")])
        trades.open_trade(
            "evt-gone", "t", direction="YES", entry_prob=70.0, market_prob=50.0,
        )
        counts = loop_status_service.loop_status()["counts"]
        self.assertEqual(counts["dangling_refs"], 1)
        self.assertEqual(counts["actionable_dangling_refs"], 1)
        self.assertEqual(
            counts["actionable_dangling_by_table"]["simulated_trades"], 1
        )

    def test_a_voided_stranded_trade_is_history_not_work(self):
        """The live case after the 53.95 purge: the total stays 1 forever, and
        the actionable reading is the one that returns to zero."""
        store.save_events([_make_record("evt-live")])
        trades.open_trade(
            "evt-gone", "t", direction="YES", entry_prob=70.0, market_prob=50.0,
        )
        voided = trades.void_trade("evt-gone")
        self.assertEqual(voided["status"], "voided")
        counts = loop_status_service.loop_status()["counts"]
        self.assertEqual(counts["dangling_refs"], 1)
        self.assertEqual(counts["dangling_by_table"]["simulated_trades"], 1)
        self.assertEqual(counts["actionable_dangling_refs"], 0)
        self.assertEqual(
            counts["actionable_dangling_by_table"]["simulated_trades"], 0
        )

    def test_every_watched_table_contributes_to_the_total(self):
        """One stranded reference per table, each pinned individually so a
        dropped table name cannot be masked by another table's count."""
        store.save_events([_make_record("evt-live")])
        for table in event_ref_census.REFERENCING_TABLES:
            if table == "event_market_links":
                continue  # the predictions arm already strands one, see _strand
            self._strand(table, f"gone-{table}")
        counts = loop_status_service.loop_status()["counts"]
        for table in event_ref_census.REFERENCING_TABLES:
            self.assertEqual(counts["dangling_by_table"][table], 1, table)
        self.assertEqual(
            counts["dangling_refs"], len(event_ref_census.REFERENCING_TABLES),
        )

    def test_the_breakdown_names_every_watched_table(self):
        """A bare total is not actionable — an operator needs the store."""
        store.save_events([_make_record("evt-live")])
        counts = loop_status_service.loop_status()["counts"]
        self.assertEqual(
            set(counts["dangling_by_table"]),
            set(event_ref_census.REFERENCING_TABLES),
        )

    def test_the_two_published_keys_keep_their_meaning(self):
        """The dashboard reads these by name; keying the census by table name
        internally must not change what they report."""
        store.save_events([_make_record("evt-live")])
        self._strand("predictions", "p-gone")  # strands its own link as well
        self._strand("event_market_links", "l-gone")
        counts = loop_status_service.loop_status()["counts"]
        self.assertEqual(counts["dangling_predictions"], 1)
        self.assertEqual(counts["dangling_links"], 2)
        self.assertEqual(counts["dangling_refs"], 3)

    def test_a_store_whose_rows_all_resolve_reports_zero(self):
        """The guard against a census that counts every row as broken."""
        record = _market_record("evt-live")
        store.save_event(record)
        preds.freeze_prediction(record)
        timeline.record_snapshot(record)
        trades.open_trade(
            "evt-live", "t", direction="YES", entry_prob=70.0, market_prob=50.0,
        )
        counts = loop_status_service.loop_status()["counts"]
        self.assertEqual(counts["dangling_refs"], 0)
        self.assertEqual(sum(counts["dangling_by_table"].values()), 0)


class LoopStatusRouteOffloadTests(unittest.TestCase):
    """/api/health must not run its status scan on the event loop.

    loop_status() is fully synchronous - a dozen SQLite reads plus the event
    store JSON - and both callers are ``async def``. Container healthchecks and
    uptime monitors poll /api/health constantly, so running the scan inline
    froze the loop for its whole duration on every poll.
    """

    def test_health_scan_does_not_starve_the_event_loop(self):
        import asyncio
        import time

        from httpx import ASGITransport, AsyncClient

        from app.main import app

        async def _exercise():
            ticks = 0

            async def _heartbeat():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.005)
                    ticks += 1

            beat = asyncio.create_task(_heartbeat())
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.get("/api/health")
            beat.cancel()
            return resp, ticks

        with patch(
            "app.services.loop_status_service.loop_status",
            side_effect=lambda **kw: time.sleep(0.25) or minimal_loop_status_payload(),
        ):
            resp, ticks = asyncio.run(_exercise())

        self.assertIn(resp.status_code, (200, 503))
        # Offloaded, the heartbeat gets ~22 ticks across the 0.25s scan; run
        # inline it gets 5 (only the ticks either side of the frozen window).
        # 15 sits clear of both.
        self.assertGreater(
            ticks,
            15,
            f"/api/health blocked the event loop: the heartbeat only got {ticks} "
            "ticks during a 0.25s status scan",
        )


if __name__ == "__main__":
    unittest.main()
