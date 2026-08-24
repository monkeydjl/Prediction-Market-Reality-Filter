"""Tests for loop_status_service — the /api/health and /events/loop/status body.

Focus is the orphan-prediction count, which used to issue one SELECT per
resolved event. /api/health is polled by container healthchecks and uptime
monitors, so a per-event query made the endpoint scale linearly with the event
store (measured at ~1.2s) while holding the event loop the whole time.

``ReviewQueueCountsTests`` covers the Q7 addition: review-queue depth, oldest
wait and SLA breach counts, which the payload previously reported nowhere.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.memory import event_store as store
from app.memory import prediction_store as preds
from app.memory import review_queue_store as rq
from app.services import loop_status_service
from app.utils import sqlite_db

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
        count = loop_status_service._orphan_prediction_count(events)

        # The 3 resolved events kept their predictions open; the 2 unresolved
        # ones are not orphans no matter what their prediction says.
        self.assertEqual(count, 3)

    def test_no_resolved_events_short_circuits_without_querying(self):
        self._seed(n_resolved=0, n_unresolved=2)

        events = store.list_all_events()
        with patch.object(
            sqlite_db, "connect", side_effect=AssertionError("queried")
        ):
            count = loop_status_service._orphan_prediction_count(events)

        self.assertEqual(count, 0)

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
            count = loop_status_service._orphan_prediction_count(events)

        self.assertEqual(count, 12)
        self.assertLessEqual(
            len(selects),
            1,
            f"orphan count issued {len(selects)} SELECTs for 12 events; it must "
            "not scale with the event store",
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

    def test_the_fallback_shape_matches_the_real_summary(self):
        """A key the fallback omits becomes a KeyError only on the failure path,
        i.e. exactly when nothing is left to catch it."""
        real = rq.queue_sla_summary()
        with patch.object(rq, "queue_sla_summary",
                          side_effect=RuntimeError("db gone")):
            fallback = loop_status_service._review_queue_counts()
        self.assertEqual(set(fallback), set(real))


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
            side_effect=lambda **kw: time.sleep(0.25) or {"runs": {}},
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
