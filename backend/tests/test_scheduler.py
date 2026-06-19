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
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core import scheduler


class JobDefaultsTests(unittest.TestCase):
    """job_defaults on the scheduler prevent silent misfire drops."""

    def test_scheduler_has_coalesce_and_misfire_grace(self):
        defaults = scheduler.scheduler._job_defaults
        self.assertTrue(defaults.get("coalesce"))
        self.assertGreater(defaults.get("misfire_grace_time", 0), 60)


class EventDiscoverJobTests(unittest.TestCase):
    """_job_event_discover runs the event-layer discovery (which freezes
    predictions), passing the configured limit and forcing a fresh re-scan."""

    def test_job_calls_discover_events_with_config(self):
        captured = {}

        async def fake_discover(**kwargs):
            captured.update(kwargs)
            return {"count": 3}

        with patch("app.services.event_intelligence_service.discover_events",
                   new=AsyncMock(side_effect=fake_discover)), \
                patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", True), \
                patch.object(scheduler.settings, "EVENT_DISCOVER_LIMIT", 7):
            asyncio.run(scheduler._job_event_discover())
        self.assertEqual(captured.get("limit"), 7)
        self.assertEqual(captured.get("use_cache"), False)  # fresh re-scan each run

    def test_job_failure_is_isolated(self):
        with patch("app.services.event_intelligence_service.discover_events",
                   new=AsyncMock(side_effect=RuntimeError("boom"))), \
                patch.object(scheduler.settings, "EVENT_DISCOVER_ENABLED", True):
            # Must not raise: a discovery failure cannot crash the scheduler.
            asyncio.run(scheduler._job_event_discover())

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


if __name__ == "__main__":
    unittest.main()
