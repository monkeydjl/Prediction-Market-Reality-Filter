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

    def test_void_trade_moves_the_open_trade_out_of_the_open_list(self):
        store.open_trade(
            "event-void", direction="YES", entry_prob=60.0, market_prob=50.0
        )

        voided = store.void_trade("event-void")

        self.assertIsNotNone(voided)
        self.assertEqual(voided["status"], "voided")
        self.assertEqual(voided["exit_reason"], "voided")
        # No settlement, so nothing that would enter the win-rate / PnL aggregates.
        self.assertIsNone(voided["pnl_pct"])
        self.assertIsNone(voided["is_win"])
        self.assertIsNone(voided["actual_outcome"])
        self.assertIsNotNone(voided["exit_time"])
        # Gone from both lists: neither a live position nor a settle.
        self.assertEqual(store.list_open_trades(), [])
        self.assertEqual(store.list_closed_trades(), [])
        self.assertEqual(store.count_open_trades(), 0)
        self.assertEqual(store.count_closed_trades(), 0)

    def test_void_trade_is_idempotent_and_noop_without_an_open_trade(self):
        store.open_trade(
            "event-void2", direction="NO", entry_prob=40.0, market_prob=60.0
        )

        self.assertIsNotNone(store.void_trade("event-void2"))
        self.assertIsNone(store.void_trade("event-void2"))    # already voided
        self.assertIsNone(store.void_trade("never-opened"))   # no trade at all

    def test_voided_trade_is_excluded_from_trade_stats(self):
        store.open_trade("settled", direction="YES", entry_prob=60.0, market_prob=50.0)
        store.close_trade("settled", actual_outcome=100.0)
        store.open_trade("void-me", direction="NO", entry_prob=70.0, market_prob=50.0)
        voided = store.void_trade("void-me")

        stats = store.trade_stats()

        self.assertEqual(voided["status"], "voided")
        self.assertEqual(stats["total_closed"], 1)
        self.assertEqual(stats["win_rate"], 1.0)
        self.assertNotIn("NO", stats["by_direction"])
        self.assertEqual(store.count_open_trades(), 0)
        self.assertEqual(store.count_closed_trades(), 1)
        self.assertEqual(store.list_closed_trades()[0]["event_id"], "settled")

    def test_by_direction_reports_both_strong_directions(self):
        """trade_stats() must report BOTH directions, not just YES.

        Positive counterpart to test_voided_trade_is_excluded_from_trade_stats:
        that case only asserts ``assertNotIn("NO", ...)``, which is satisfied
        both by "the voided NO trade was filtered out" AND by "NO was never
        queried at all" -- so it cannot see the loop shrinking to YES-only.
        Deleting "NO" from ``_REPORTED_DIRECTIONS`` left the whole file green
        before this case existed (this is the guard registered as mutation W25).
        """
        store.open_trade("d-yes", direction="YES", entry_prob=60.0, market_prob=50.0)
        store.close_trade("d-yes", actual_outcome=100.0)  # YES called correctly
        store.open_trade("d-no", direction="NO", entry_prob=40.0, market_prob=60.0)
        store.close_trade("d-no", actual_outcome=0.0)  # NO called correctly

        stats = store.trade_stats()
        self.assertEqual(set(stats["by_direction"]), {"YES", "NO"})
        self.assertEqual(stats["by_direction"]["YES"]["total"], 1)
        self.assertEqual(stats["by_direction"]["YES"]["wins"], 1)
        self.assertEqual(stats["by_direction"]["NO"]["total"], 1)
        self.assertEqual(stats["by_direction"]["NO"]["wins"], 1)
        self.assertEqual(stats["by_direction"]["NO"]["win_rate"], 1.0)

    def test_records_the_current_schema_version(self):
        store.open_trade("ver", direction="YES", entry_prob=60.0, market_prob=50.0)
        self.assertEqual(
            sqlite_db.schema_versions(self.db_path)["simulated_trades"], 2
        )

    def test_migrate_widens_the_status_check_and_preserves_row_ids(self):
        """A v1 DB (CHECK without 'voided') must be rebuilt in place: every row
        kept, every id kept, and the third state accepted afterwards."""
        v1_schema = """
        CREATE TABLE simulated_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id        TEXT NOT NULL UNIQUE,
            event_id        TEXT NOT NULL,
            event_title     TEXT NOT NULL DEFAULT '',
            direction       TEXT NOT NULL CHECK (direction IN ('YES','NO')),
            entry_prob      REAL NOT NULL,
            market_prob     REAL NOT NULL,
            entry_edge      REAL NOT NULL,
            entry_time      TEXT NOT NULL,
            position_pct    REAL NOT NULL DEFAULT 2.0,
            confidence      REAL,
            trust_weight    REAL,
            decision        TEXT NOT NULL DEFAULT 'watch',
            exit_prob       REAL,
            exit_market     REAL,
            exit_time       TEXT,
            exit_reason     TEXT,
            actual_outcome  REAL,
            pnl_pct         REAL,
            is_win          INTEGER,
            status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "loop.db")
            conn = sqlite3.connect(db_path)
            conn.executescript(v1_schema)
            # Ids far from the natural 1,2 sequence: a rebuild that failed to
            # copy `id` would renumber them and both assertions below would fail.
            conn.executemany(
                "INSERT INTO simulated_trades "
                "(id, trade_id, event_id, direction, entry_prob, market_prob, "
                " entry_edge, entry_time, status) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (42, "sim-open", "evt-old", "YES", 60.0, 50.0, 10.0, "t0", "open"),
                    (7, "sim-closed", "evt-done", "NO", 40.0, 60.0, -20.0, "t0", "closed"),
                ],
            )
            conn.commit()
            conn.close()

            with patch.object(store, "loop_db_path", return_value=db_path):
                store.void_trade("evt-old")   # _ensure_schema -> _migrate, then void
            store._INITIALIZED.discard(db_path)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                ddl = conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='simulated_trades'"
                ).fetchone()["sql"]
                by_trade = {
                    r["trade_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM simulated_trades")
                }
                # The widened CHECK must now accept the third state.
                conn.execute(
                    "INSERT INTO simulated_trades "
                    "(trade_id, event_id, direction, entry_prob, market_prob, "
                    " entry_edge, entry_time, status) "
                    "VALUES ('sim-new', 'evt-new', 'YES', 60.0, 50.0, 10.0, 't1', 'voided')"
                )
                conn.commit()
            finally:
                conn.close()

        self.assertIn("'voided'", ddl)
        self.assertEqual(len(by_trade), 2)
        self.assertEqual(by_trade["sim-open"]["id"], 42)
        self.assertEqual(by_trade["sim-closed"]["id"], 7)
        self.assertEqual(by_trade["sim-open"]["status"], "voided")
        self.assertEqual(by_trade["sim-closed"]["status"], "closed")


if __name__ == "__main__":
    unittest.main()

