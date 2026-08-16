"""Tests for the fire-and-forget task launcher.

Two failures motivated it: an unreferenced `asyncio.create_task` can be garbage
collected mid-run, and its exception is only ever reported as a GC-time
"Task exception was never retrieved" line on stderr — outside logging, so
neither the log file nor Sentry sees it.
"""

import asyncio
import unittest

from app.utils import background_tasks


class SpawnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        background_tasks._PENDING.clear()

    async def test_reference_is_held_while_running_and_released_after(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def work():
            started.set()
            await release.wait()

        task = background_tasks.spawn(work(), name="held")
        await started.wait()
        self.assertEqual(background_tasks.pending_count(), 1)
        self.assertIn(task, background_tasks._PENDING)

        release.set()
        await task
        self.assertEqual(background_tasks.pending_count(), 0)

    async def test_exception_is_logged_with_the_task_name(self):
        async def boom():
            raise ValueError("kaboom")

        with self.assertLogs("app.utils.background_tasks", level="ERROR") as logs:
            task = background_tasks.spawn(boom(), name="engine_auto_tune:hybrid:t-42")
            with self.assertRaises(ValueError):
                await task
            # The done callback runs via call_soon, so let the loop drain.
            await asyncio.sleep(0)

        text = "\n".join(logs.output)
        self.assertIn("engine_auto_tune:hybrid:t-42", text)
        self.assertIn("kaboom", text)
        self.assertEqual(background_tasks.pending_count(), 0)

    async def test_cancellation_is_logged_as_a_warning(self):
        started = asyncio.Event()

        async def work():
            started.set()
            await asyncio.sleep(3600)

        with self.assertLogs("app.utils.background_tasks", level="WARNING") as logs:
            task = background_tasks.spawn(work(), name="cancelled-one")
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)

        text = "\n".join(logs.output)
        self.assertIn("cancelled", text.lower())
        self.assertIn("cancelled-one", text)
        self.assertEqual(background_tasks.pending_count(), 0)

    async def test_successful_task_logs_nothing(self):
        async def work():
            return 7

        task = background_tasks.spawn(work(), name="quiet")
        self.assertEqual(await task, 7)
        await asyncio.sleep(0)
        self.assertEqual(background_tasks.pending_count(), 0)


if __name__ == "__main__":
    unittest.main()
