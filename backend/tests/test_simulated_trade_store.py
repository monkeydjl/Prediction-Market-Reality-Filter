import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory import simulated_trade_store as store
from app.utils import sqlite_db


class SimulatedTradeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "loop.db")
        self.patch = patch.object(store, "loop_db_path", return_value=self.db_path)
        self.patch.start()

    def tearDown(self):
        store._INITIALIZED.discard(self.db_path)
        self.patch.stop()
        self.tmpdir.cleanup()

    def test_partial_mkt_resolution_scores_yes_against_market_price(self):
        store.open_trade(
            "event-yes",
            direction="YES",
            entry_prob=26.71,
            market_prob=1.06,
            position_pct=5.0,
        )

        closed = store.close_trade("event-yes", actual_outcome=50.0)

        self.assertIsNotNone(closed)
        self.assertEqual(closed["is_win"], 1)
        self.assertEqual(closed["pnl_pct"], 230.85)

    def test_partial_mkt_resolution_scores_no_against_market_price(self):
        store.open_trade(
            "event-no",
            direction="NO",
            entry_prob=57.14,
            market_prob=82.32,
            position_pct=5.0,
        )

        closed = store.close_trade("event-no", actual_outcome=50.0)

        self.assertIsNotNone(closed)
        self.assertEqual(closed["is_win"], 1)
        self.assertEqual(closed["pnl_pct"], 9.14)

    def test_binary_resolution_keeps_directional_loss(self):
        store.open_trade(
            "event-loss",
            direction="YES",
            entry_prob=30.7,
            market_prob=3.84,
            position_pct=2.0,
        )

        closed = store.close_trade("event-loss", actual_outcome=0.0)

        self.assertIsNotNone(closed)
        self.assertEqual(closed["is_win"], 0)
        self.assertEqual(closed["pnl_pct"], -2.0)

    def test_partial_resolution_uses_partial_exit_reason_by_default(self):
        store.open_trade(
            "event-partial-reason",
            direction="YES",
            entry_prob=60.0,
            market_prob=40.0,
            position_pct=2.0,
        )

        closed = store.close_trade("event-partial-reason", actual_outcome=50.0)

        self.assertIsNotNone(closed)
        self.assertEqual(closed["exit_reason"], "resolved_partial")

    def test_recompute_closed_trades_repairs_legacy_binary_partial_settlement(self):
        store.open_trade(
            "event-recompute",
            direction="YES",
            entry_prob=26.71,
            market_prob=1.06,
            position_pct=5.0,
        )
        closed = store.close_trade("event-recompute", actual_outcome=50.0)
        self.assertIsNotNone(closed)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE simulated_trades SET pnl_pct=-5.0, is_win=0, exit_reason='resolved_no'"
            )
            conn.commit()
        finally:
            conn.close()

        result = store.recompute_closed_trades()
        repaired = store.list_closed_trades(limit=1)[0]

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["total_pnl_pct"], 230.85)
        self.assertEqual(repaired["is_win"], 1)
        self.assertEqual(repaired["pnl_pct"], 230.85)
        self.assertEqual(repaired["exit_reason"], "resolved_partial")


    def test_lists_trades_with_limit_offset_and_counts(self):
        for i in range(12):
            store.open_trade(
                f"open-{i}",
                direction="YES",
                entry_prob=60.0 + i,
                market_prob=50.0,
            )
        for i in range(11):
            store.open_trade(
                f"closed-{i}",
                direction="NO",
                entry_prob=40.0,
                market_prob=60.0 + i,
            )
            store.close_trade(f"closed-{i}", actual_outcome=0.0)

        self.assertEqual(store.count_open_trades(), 12)
        self.assertEqual(store.count_closed_trades(), 11)
        self.assertEqual(len(store.list_open_trades(limit=10, offset=0)), 10)
        self.assertEqual(len(store.list_open_trades(limit=10, offset=10)), 2)
        self.assertEqual(len(store.list_closed_trades(limit=10, offset=0)), 10)
        self.assertEqual(len(store.list_closed_trades(limit=10, offset=10)), 1)

    def test_row_exposes_raw_and_directional_edge(self):
        store.open_trade(
            "edge-yes",
            direction="YES",
            entry_prob=70.0,
            market_prob=50.0,
            position_pct=2.0,
        )
        store.open_trade(
            "edge-no",
            direction="NO",
            entry_prob=40.0,
            market_prob=60.0,
            position_pct=2.0,
        )
        rows = {r["event_id"]: r for r in store.list_open_trades(limit=10)}
        yes = rows["edge-yes"]
        no = rows["edge-no"]
        # raw_edge = AI − market
        self.assertAlmostEqual(yes["entry_edge"], 20.0, places=2)
        self.assertEqual(yes["raw_edge"], yes["entry_edge"])
        self.assertAlmostEqual(yes["directional_edge"], 20.0, places=2)
        self.assertAlmostEqual(no["entry_edge"], -20.0, places=2)
        self.assertAlmostEqual(no["directional_edge"], 20.0, places=2)
        self.assertIn("raw_edge", yes["edge_definition"])

    def test_stats_edge_definition_and_directional_mean(self):
        store.open_trade(
            "s-yes",
            direction="YES",
            entry_prob=60.0,
            market_prob=50.0,
            position_pct=1.0,
        )
        store.close_trade("s-yes", actual_outcome=100.0)
        store.open_trade(
            "s-no",
            direction="NO",
            entry_prob=40.0,
            market_prob=55.0,
            position_pct=1.0,
        )
        store.close_trade("s-no", actual_outcome=0.0)

        stats = store.trade_stats()
        self.assertEqual(stats["total_closed"], 2)
        self.assertIsNotNone(stats["avg_edge_at_entry"])
        self.assertIsNotNone(stats["avg_directional_edge_at_entry"])
        self.assertIn("raw_edge", stats["edge_definition"])
        self.assertIn("0-100", stats["edge_definition"]["scale"])
        # YES raw=+10, NO raw=-15 → directional +10 and +15 → mean 12.5
        self.assertAlmostEqual(stats["avg_directional_edge_at_entry"], 12.5, places=2)
        # |raw| mean = (10+15)/2 = 12.5
        self.assertAlmostEqual(stats["avg_edge_at_entry"], 12.5, places=2)

    def test_store_opens_the_db_through_the_shared_wrapper(self):
        """Every path must go through sqlite_db.connect(), not raw sqlite3.

        The wrapper is what supplies timeout=30s, check_same_thread=False and
        the WAL/foreign_keys pragmas. Raw sqlite3.connect() gets none of them,
        so a busy writer surfaces as OperationalError after 5s instead of
        waiting. WAL is recorded in the file header, so its presence after a
        store write proves the wrapper opened it.
        """
        store.open_trade(
            "wrapper-check",
            direction="YES",
            entry_prob=60.0,
            market_prob=50.0,
        )

        conn = sqlite3.connect(self.db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(mode.lower(), "wal")

    def test_open_trade_serializes_through_the_shared_write_lock(self):
        """Writes must take sqlite_db._WRITE_LOCK, not just SQLite's file lock.

        open_trade is a SELECT-then-INSERT: without the process-wide lock two
        threads can both miss the existing open trade and insert one each,
        breaking the documented "one open trade per event" guarantee. Holding
        the lock here must block a concurrent open_trade until it is released.
        """
        import threading

        # Warm the schema so the blocking below is on the insert path itself.
        store.open_trade(
            "warmup", direction="YES", entry_prob=60.0, market_prob=50.0
        )

        done = threading.Event()

        def _open():
            store.open_trade(
                "locked-1", direction="YES", entry_prob=60.0, market_prob=50.0
            )
            done.set()

        with sqlite_db._WRITE_LOCK:
            worker = threading.Thread(target=_open)
            worker.start()
            blocked = not done.wait(timeout=0.5)
        worker.join(timeout=5)

        self.assertTrue(
            blocked, "open_trade must serialize through sqlite_db._WRITE_LOCK"
        )
        self.assertTrue(done.is_set(), "open_trade must proceed once released")
        self.assertEqual(len(store.list_open_trades()), 2)


if __name__ == "__main__":
    unittest.main()

