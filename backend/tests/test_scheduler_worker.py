import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from scripts import run_scheduler


async def _return_immediately():
    return None


class SchedulerWorkerTests(unittest.TestCase):
    def test_worker_starts_scheduler_and_stops_on_shutdown(self):
        with patch.object(run_scheduler, "setup_logging"), \
                patch.object(run_scheduler.settings, "SCHEDULER_ENABLED", True), \
                patch.object(run_scheduler.settings, "LLM_STARTUP_CHECK_ENABLED", False), \
                patch.object(run_scheduler.sqlite_db, "maintain", return_value={"ok": True}), \
                patch.object(run_scheduler, "start_scheduler", return_value=True) as start, \
                patch.object(run_scheduler, "stop_scheduler") as stop:
            code = asyncio.run(
                run_scheduler.run_scheduler_worker(wait_for_shutdown=_return_immediately)
            )

        self.assertEqual(code, 0)
        start.assert_called_once_with()
        stop.assert_called_once_with()

    def test_worker_returns_failure_when_lock_owner_exists(self):
        with patch.object(run_scheduler, "setup_logging"), \
                patch.object(run_scheduler.settings, "SCHEDULER_ENABLED", True), \
                patch.object(run_scheduler.settings, "LLM_STARTUP_CHECK_ENABLED", False), \
                patch.object(run_scheduler.sqlite_db, "maintain", return_value={"ok": True}), \
                patch.object(run_scheduler, "start_scheduler", return_value=False), \
                patch.object(run_scheduler, "stop_scheduler") as stop:
            code = asyncio.run(
                run_scheduler.run_scheduler_worker(wait_for_shutdown=_return_immediately)
            )

        self.assertEqual(code, 1)
        stop.assert_not_called()

    def test_worker_respects_scheduler_disabled(self):
        with patch.object(run_scheduler, "setup_logging"), \
                patch.object(run_scheduler.settings, "SCHEDULER_ENABLED", False), \
                patch.object(run_scheduler.sqlite_db, "maintain") as maintain, \
                patch.object(run_scheduler, "start_scheduler") as start:
            code = asyncio.run(
                run_scheduler.run_scheduler_worker(wait_for_shutdown=_return_immediately)
            )

        self.assertEqual(code, 0)
        maintain.assert_not_called()
        start.assert_not_called()

    def test_worker_runs_llm_startup_check_when_enabled(self):
        with patch.object(run_scheduler, "setup_logging"), \
                patch.object(run_scheduler.settings, "SCHEDULER_ENABLED", True), \
                patch.object(run_scheduler.settings, "LLM_STARTUP_CHECK_ENABLED", True), \
                patch.object(run_scheduler, "validate_primary_llm_startup",
                             new=AsyncMock()) as validate, \
                patch.object(run_scheduler.sqlite_db, "maintain", return_value={"ok": True}), \
                patch.object(run_scheduler, "start_scheduler", return_value=True), \
                patch.object(run_scheduler, "stop_scheduler"):
            code = asyncio.run(
                run_scheduler.run_scheduler_worker(wait_for_shutdown=_return_immediately)
            )

        self.assertEqual(code, 0)
        validate.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
