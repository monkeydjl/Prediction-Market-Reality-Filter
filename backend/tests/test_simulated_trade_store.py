import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory import simulated_trade_store as store


class SimulatedTradeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "loop.db")
        self.patch = patch.object(store, "loop_db_path", return_value=self.db_path)
        self.patch.start()

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main()
