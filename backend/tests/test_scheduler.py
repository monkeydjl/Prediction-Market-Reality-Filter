"""Tests for scheduler job logic.

The scheduler jobs are thin glue (fetch -> process -> log). These lock the
event-layer jobs: event_discover passes the configured limit and forces a
fresh re-scan, a discovery failure cannot crash the scheduler, the job is
gated on EVENT_DISCOVER_ENABLED, and the misfire-grace defaults are set so a
missed run is not silently dropped.

All external dependencies are mocked (discover_events), so no network or LLM
is hit.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.core import scheduler
from app.memory import loop_run_store
from app.utils import sqlite_db


class JobDefaultsTests(unittest.TestCase):
    """job_defaults on the scheduler prevent silent misfire drops."""

    def test_scheduler_has_coalesce_and_misfire_grace(self):
        defaults = scheduler.scheduler._job_defaults
        self.assertTrue(defaults.get("coalesce"))
        self.assertGreater(defaults.get("misfire_grace_time", 0), 60)


class SchedulerLockTests(unittest.TestCase):
    """The scheduler process lock prevents same-host worker duplication."""

    def tearDown(self):
        scheduler._release_scheduler_lock()

    def test_process_lock_acquires_and_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = str(Path(tmp) / "scheduler.lock")
            with patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", True), \
                    patch.object(scheduler.settings, "SCHEDULER_LOCK_FILE", lock_path):
                self.assertTrue(scheduler._try_acquire_scheduler_lock())
                self.assertFalse(scheduler.scheduler_start_skipped_due_to_lock())
                scheduler._release_scheduler_lock()
                self.assertTrue(scheduler._try_acquire_scheduler_lock())
                scheduler._release_scheduler_lock()

    def test_process_lock_detects_external_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = str(Path(tmp) / "scheduler.lock")
            with open(lock_path, "a+", encoding="utf-8") as owner:
                scheduler._acquire_process_lock(owner)
                try:
                    with patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", True), \
                            patch.object(scheduler.settings, "SCHEDULER_LOCK_FILE", lock_path):
                        self.assertFalse(scheduler._try_acquire_scheduler_lock())
                        self.assertTrue(scheduler.scheduler_start_skipped_due_to_lock())
                finally:
                    scheduler._release_process_lock(owner)


class EventDiscoverJobTests(unittest.TestCase):
    """_job_event_discover runs the event-layer discovery (which freezes
    predictions), passing the configured limit and forcing a fresh re-scan."""

    def test_job_calls_discover_events_with_config(self):
        captured = {}

        async def fake_discover(**kwargs):
            captured.update(kwargs)
            return {"count": 3}

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.event_intelligence_service.discover_events",
                          new=AsyncMock(side_effect=fake_discover)), \
                    patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", True), \
                    patch.object(scheduler.settings, "EVENT_DISCOVER_LIMIT", 7):
                asyncio.run(scheduler._job_event_discover())
                run = loop_run_store.last_run("event_discover")
        self.assertEqual(captured.get("limit"), 7)
        self.assertEqual(captured.get("use_cache"), False)  # fresh re-scan each run
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["result"]["count"], 3)

    def test_job_failure_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch("app.services.event_intelligence_service.discover_events",
                          new=AsyncMock(side_effect=RuntimeError("boom"))), \
                    patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", True):
                # Must not raise: a discovery failure cannot crash the scheduler.
                asyncio.run(scheduler._job_event_discover())
                run = loop_run_store.last_run("event_discover")
        self.assertEqual(run["status"], "failed")
        self.assertIn("boom", run["error"])

    def test_job_skips_when_disabled(self):
        mock_discover = AsyncMock(return_value={"count": 1})
        with patch("app.services.event_intelligence_service.discover_events",
                   new=mock_discover), \
                patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", False):
            asyncio.run(scheduler._job_event_discover())
        mock_discover.assert_not_called()


class EventDiscoverRegistrationTests(unittest.TestCase):
    """The event_discover job is registered only when EVENT_DISCOVER_ENABLED;
    event_auto_resolve is always registered."""

    def _registered_ids(self, enabled):
        fake = MagicMock()
        with patch.object(scheduler, "scheduler", fake), \
                patch.object(scheduler.settings, "SCHEDULER_LOCK_ENABLED", False), \
                patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", enabled):
            scheduler.start_scheduler()
        return [call.kwargs.get("id") for call in fake.add_job.call_args_list]

    def test_registered_when_enabled(self):
        ids = self._registered_ids(True)
        self.assertIn("event_discover", ids)
        self.assertIn("event_auto_resolve", ids)

    def test_not_registered_when_disabled(self):
        ids = self._registered_ids(False)
        self.assertNotIn("event_discover", ids)
        self.assertIn("event_auto_resolve", ids)  # always registered

    def test_start_is_noop_when_already_running(self):
        fake = MagicMock()
        fake.running = True
        with patch.object(scheduler, "scheduler", fake):
            scheduler.start_scheduler()
        fake.add_job.assert_not_called()
        fake.start.assert_not_called()

    def test_start_skips_when_process_lock_is_held(self):
        fake = MagicMock()
        fake.running = False
        with patch.object(scheduler, "scheduler", fake), \
                patch.object(scheduler, "_try_acquire_scheduler_lock", return_value=False):
            started = scheduler.start_scheduler()
        self.assertFalse(started)
        fake.add_job.assert_not_called()
        fake.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
