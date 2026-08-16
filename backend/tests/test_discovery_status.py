import asyncio
import unittest

from app.services import discovery_status


class SnapshotIsolationTests(unittest.TestCase):
    """`snapshot()` must not alias the live nested containers.

    `STATUS.copy()` is shallow, so the returned dict shared `sources` and
    `errors` with the running scan. `GET /discover/status` returns the snapshot
    to FastAPI, which awaits while serializing it — long enough for the scan to
    add a source or append an error, so the serializer could iterate a container
    that changed size mid-iteration.
    """

    def setUp(self):
        asyncio.run(discovery_status.reset(3))

    def test_sources_added_after_snapshot_are_not_visible(self):
        async def run():
            await discovery_status.source_start("Polymarket")
            snap = discovery_status.snapshot()
            await discovery_status.source_start("Kalshi")
            await discovery_status.source_done("Polymarket", 7)
            return snap

        snap = asyncio.run(run())
        self.assertEqual(list(snap["sources"]), ["Polymarket"])
        # The per-source entry is copied too, so the later source_done does not
        # rewrite the snapshotted "fetching" state.
        self.assertEqual(snap["sources"]["Polymarket"]["status"], "fetching")

    def test_errors_appended_after_snapshot_are_not_visible(self):
        async def run():
            await discovery_status.set_candidates(2, 2)
            await discovery_status.event_analyzed("q1", success=False, error="boom")
            snap = discovery_status.snapshot()
            await discovery_status.event_analyzed("q2", success=False, error="bang")
            return snap

        snap = asyncio.run(run())
        self.assertEqual([e["event"] for e in snap["errors"]], ["q1"])


if __name__ == "__main__":
    unittest.main()
