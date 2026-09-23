"""`GET /metrics` must not run its store walk on the event-loop thread.

`render_metrics()` reads the whole event store to recompute the direction and
consensus gauges. Measured on the live stores after the schema memo landed (see
`test_store_schema_memoization`):

    render_metrics()                      median  78.58 ms
      _refresh_event_store_gauges()       median  68.03 ms   (87% of the scrape)
        list_all_events()                 median  58.40 ms   (3,622,538 bytes)
      _refresh_scheduler_gauges()         median   4.24 ms
      _refresh_calibration_gauges()       median   3.91 ms

The handler is `async def` and called it directly, so every scrape stalled the
event loop for the whole 78 ms -- Prometheus scrapes at 15s, so 5,760 times a
day, and nothing else this process serves could progress meanwhile.
``api_health`` twenty lines above already hands its equivalent store read to
``asyncio.to_thread``; this one did not.

The walk itself is left in place. It is a read with no lock, it is what keeps the
gauges honest on each scrape, and off the loop it costs 0.45% of one core per day
-- a cache would buy that back at the price of staleness nobody asked for.

Both tests below are behavioural rather than a source scan, because "runs off the
loop" is the property that matters and a scan for the string `to_thread` would
pass on a call that was never awaited.
"""
from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch

from app import main


class ScrapeOffloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_render_does_not_run_on_the_loop_thread(self):
        """Inside a worker thread there is no running loop, so this raises."""
        seen: dict[str, object] = {}

        def _probe() -> tuple[bytes, str]:
            try:
                asyncio.get_running_loop()
                seen["off_loop"] = False
            except RuntimeError:
                seen["off_loop"] = True
            seen["thread"] = threading.current_thread().ident
            return b"# probe\n", "text/plain; version=0.0.4"

        with patch("app.utils.metrics.render_metrics", _probe):
            response = await main.prometheus_metrics()

        self.assertTrue(
            seen.get("off_loop"),
            "render_metrics() ran on the event-loop thread; a 78 ms store walk "
            "there blocks every other request for the duration",
        )
        self.assertNotEqual(seen.get("thread"), threading.current_thread().ident)
        self.assertEqual(response.body, b"# probe\n")
        self.assertIn("text/plain", response.media_type)

    async def test_the_loop_still_runs_while_the_scrape_is_in_flight(self):
        """A coroutine must be able to release the render. On the loop it cannot.

        The render blocks on an event that only a coroutine sets. If the handler
        holds the loop thread, that coroutine never gets to run and the wait times
        out -- so the assertion is on `released`, not on elapsed time.
        """
        gate = threading.Event()
        observed: dict[str, bool] = {}

        def _blocking_render() -> tuple[bytes, str]:
            observed["released"] = gate.wait(timeout=5.0)
            return b"# blocked\n", "text/plain; version=0.0.4"

        with patch("app.utils.metrics.render_metrics", _blocking_render):
            task = asyncio.create_task(main.prometheus_metrics())
            await asyncio.sleep(0)  # hand the loop to the handler
            gate.set()  # a loop-thread statement, reachable only if the loop is free
            await asyncio.wait_for(task, timeout=10.0)

        self.assertTrue(
            observed.get("released"),
            "the event loop could not reach a statement while /metrics was in "
            "flight, so the scrape is still blocking it",
        )


class ScrapeStillServesTests(unittest.IsolatedAsyncioTestCase):
    """The offload must not change what the endpoint returns."""

    async def test_the_real_renderer_still_answers_prometheus_text(self):
        response = await main.prometheus_metrics()
        self.assertIn("text/plain", response.media_type)
        body = response.body.decode("utf-8")
        self.assertIn("pmrf_", body)
        self.assertTrue(body.endswith("\n"), "exposition format is line-oriented")


if __name__ == "__main__":
    unittest.main()
