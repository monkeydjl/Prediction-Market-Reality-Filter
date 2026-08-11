"""Tests for loop_status_service — the /api/health and /events/loop/status body.

Focus is the orphan-prediction count, which used to issue one SELECT per
resolved event. /api/health is polled by container healthchecks and uptime
monitors, so a per-event query made the endpoint scale linearly with the event
store (measured at ~1.2s) while holding the event loop the whole time.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory import event_store as store
from app.memory import prediction_store as preds
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
