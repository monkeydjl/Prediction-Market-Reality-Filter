"""Tests for scripts/report_probability_scale_outliers.py.

Why this file exists
--------------------
The script is the **only** thing that would notice a new 0-1-scale value reaching
``predictions.market_probability``. The 2026-09-26 audit decided against both
alternatives -- it did not rewrite the one bad stored row (the "true" value is
only inferable, and rewriting history would stop the record reflecting what the
system actually computed) and it did not add a write-time guard (a value-only
threshold cannot tell 0.2% from a 0.2 fraction, and the analyze request carries
no second probability to compare against). That leaves this census as the entire
safeguard.

An unguarded safeguard is the defect class this repo keeps re-finding, and an
earlier version of this script was only ever run against the live DB, so its
bucket SQL was never checked against inputs whose answer is known. Both halves
are pinned here: the arithmetic, and the promise that running it changes nothing.

The ``market_probability NULL`` bucket
--------------------------------------
``market_probability`` is declared ``REAL NOT NULL`` in
``prediction_store._SCHEMA``, so that bucket reads 0 on every DB this repo can
create. It is kept as a defensive line. Asserted here as 0 rather than presented
as a live signal, and paired with the INSERT that proves why it cannot be
anything else.
"""
from __future__ import annotations

import contextlib
import io
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_SCRIPTS = _BACKEND / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from app.memory import event_market_link_store as link_store  # noqa: E402
from app.memory import prediction_store as preds  # noqa: E402
from app.memory import simulated_trade_store as trade_store  # noqa: E402
from app.utils import sqlite_db  # noqa: E402

import report_probability_scale_outliers as probe  # noqa: E402

#: The row the audit found, exactly as stored. 30.17 - 0.2*100 = 10.17.
AUDITED = {
    "event_id": "0779bde4dcd63e08",
    "market_probability": 0.2,
    "ai_probability": 30.17,
    "raw_edge": 29.97,
    "adjusted_edge": 14.98,
    "decision": "provisional_act",
    "platform": "Kalshi",
    "contract_id": "KXBRUVSEAT-35",
    "created_at": "2026-07-12 03:00:00",
}


class ReportProbabilityScaleOutliersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "v2_loop.db")
        self._patch = patch.object(
            sqlite_db, "loop_db_path", return_value=self.db_path
        )
        self._patch.start()
        # The real schemas, not hand-rolled copies: the point is to exercise the
        # script against the tables it will actually meet.
        preds._ensure_schema(self.db_path)
        trade_store._ensure_schema(self.db_path)
        link_store._ensure_schema(self.db_path)

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    # --- helpers ---------------------------------------------------------

    def _insert_prediction(self, event_id, market_probability, **overrides):
        row = {
            "id": f"pred-{event_id}",
            "event_id": event_id,
            "ai_probability": 50.0,
            "market_probability": market_probability,
            "raw_edge": 50.0 - (market_probability or 0.0),
            "adjusted_edge": None,
            "decision": "tracked",
            "created_at": "",
        }
        row.update(overrides)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with sqlite_db.writing(self.db_path) as conn:
            conn.execute(
                f"INSERT INTO predictions ({cols}) VALUES ({marks})",
                tuple(row.values()),
            )

    def _insert_trade(self, event_id, trade_id="t1"):
        with sqlite_db.writing(self.db_path) as conn:
            conn.execute(
                "INSERT INTO simulated_trades (trade_id, event_id, direction,"
                " entry_prob, market_prob, entry_edge, entry_time)"
                " VALUES (?, ?, 'YES', 30.17, 0.2, 29.97, '2026-07-12 03:00:00')",
                (trade_id, event_id),
            )

    def _insert_link(self, event_id, link_id="l1"):
        with sqlite_db.writing(self.db_path) as conn:
            conn.execute(
                "INSERT INTO event_market_links (id, event_id, contract_id)"
                " VALUES (?, ?, 'KXBRUVSEAT-35')",
                (link_id, event_id),
            )

    def _run(self, argv=()):
        """Run ``main()`` and return (returncode, stdout)."""
        argv = ["report_probability_scale_outliers.py", *argv]
        buf = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
            rc = probe.main()
        return rc, buf.getvalue()

    def _bucket(self, out, label):
        """Read one ``<label> <number>`` line out of the distribution block.

        Matched by label instead of by column position so re-wording a label or
        re-widening its padding does not silently turn this into an assertion
        about the wrong row.
        """
        match = re.search(
            rf"^\s+{re.escape(label)}\s+(-?\d+(?:\.\d+)?)\s*$", out, re.MULTILINE
        )
        self.assertIsNotNone(match, f"no bucket labelled {label!r} in:\n{out}")
        return float(match.group(1))

    # --- bucket arithmetic ----------------------------------------------

    def test_distribution_buckets_partition_the_rows(self):
        """Every row lands in exactly one bucket, and min/max is over all rows.

        Five rows chosen so each bucket is non-empty except the NULL one, whose
        emptiness is asserted separately (it cannot be non-empty on this schema).
        """
        self._insert_prediction("evt-zero", 0.0)
        self._insert_prediction("evt-suspect", 0.2)
        self._insert_prediction("evt-low", 20.0)
        self._insert_prediction("evt-high", 99.45)
        self._insert_prediction("evt-over", 105.0)

        rc, out = self._run()

        self.assertEqual(rc, 0)
        self.assertEqual(self._bucket(out, "total rows"), 5)
        self.assertEqual(self._bucket(out, "0 < mp < 1   (0-1 scale suspects)"), 1)
        self.assertEqual(self._bucket(out, "mp = 0"), 1)
        self.assertEqual(self._bucket(out, "1 <= mp <= 100"), 2)
        self.assertEqual(self._bucket(out, "mp > 100"), 1)
        self.assertIn("min / max", out)

    def test_out_of_range_high_rows_are_not_lumped_into_the_normal_bucket(self):
        """``mp > 100`` has to be its own bucket.

        ``1 <= mp <= 100`` is the bucket a reader takes as "fine"; an upper bound
        of 100 is what keeps a 105 in a bucket of its own instead of hiding an
        out-of-range value inside the healthy count.
        """
        self._insert_prediction("evt-over", 105.0)
        self._insert_prediction("evt-high", 99.45)

        _, out = self._run()

        self.assertEqual(self._bucket(out, "mp > 100"), 1)
        self.assertEqual(self._bucket(out, "1 <= mp <= 100"), 1)

    def test_min_max_span_every_row_including_the_suspect(self):
        self._insert_prediction("evt-suspect", 0.2)
        self._insert_prediction("evt-over", 105.0)

        _, out = self._run()

        self.assertRegex(out, r"min / max\s+0\.2 / 105\.0")

    def test_the_null_bucket_is_zero_because_the_column_is_not_null(self):
        """Stated as a consequence of the schema, not as a live signal.

        ``market_probability REAL NOT NULL`` means the count is 0 by
        construction. The INSERT below is the proof; if a future migration drops
        NOT NULL, that INSERT starts succeeding and this test goes red -- which
        is the point, because the bucket would then be able to say something.
        """
        self._insert_prediction("evt-ok", 20.0)

        _, out = self._run()

        self.assertEqual(self._bucket(out, "market_probability NULL"), 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_prediction("evt-null", None)

    # --- the suspect row -------------------------------------------------

    def test_the_audited_row_is_reported_with_its_corrected_edge(self):
        """The one live example, with its real stored numbers.

        The printed correction is 30.17 - 0.2*100 = 10.17. (The audit's ACT
        comparison uses ``adjusted_edge``, which the script does not recompute:
        (30.17-20)*0.5 = 5.09, below the 6.0 threshold, which is why this row
        should have been a ``watch`` rather than a ``provisional_act``.)
        """
        self._insert_prediction(**AUDITED)

        rc, out = self._run()

        self.assertEqual(rc, 0)
        self.assertIn("=== rows with 0 < market_probability < 1 (1) ===", out)
        self.assertIn("0779bde4dcd63e08", out)
        self.assertIn("Kalshi/KXBRUVSEAT-35", out)
        self.assertIn("created=2026-07-12 03:00:00", out)
        self.assertIn(
            "ai=30.17 market=0.2 raw_edge=29.97"
            " adjusted_edge=14.98 decision=provisional_act",
            out,
        )
        self.assertIn("if the market value were 20.0: raw_edge=10.17", out)

    def test_a_suspect_with_a_non_numeric_ai_probability_skips_the_correction(self):
        """The correction is guarded by ``isinstance(ai, (int, float))``, and the
        guard is reachable even though ``ai_probability`` is ``REAL NOT NULL``.

        SQLite applies type *affinity*, not enforcement: a text value that cannot
        be losslessly converted is stored as TEXT in a REAL column, so ``'abc'``
        arrives here as a ``str`` and the guard fires before the arithmetic.
        Without it the f-string would put "unsupported operand type(s)" in the
        script's headline output. The row still has to be listed either way.
        """
        self._insert_prediction("evt-noai", 0.5, ai_probability="abc")

        rc, out = self._run()

        self.assertEqual(rc, 0)
        self.assertIn("evt-noai", out)
        self.assertNotIn("if the market value were", out)

    def test_an_empty_suspect_set_says_none(self):
        self._insert_prediction("evt-ok", 20.0)

        rc, out = self._run()

        self.assertEqual(rc, 0)
        self.assertIn("=== rows with 0 < market_probability < 1 (0) ===", out)
        self.assertRegex(out, r"\(0\)\s*===.*\n\s+none")

    def test_a_zero_market_probability_is_not_a_suspect(self):
        """``mp = 0`` is its own bucket: the filter is strictly ``> 0``.

        0 is indistinguishable from "no market quote" and is a separate
        condition, so it must not inflate the suspect count.
        """
        self._insert_prediction("evt-zero", 0.0)

        _, out = self._run()

        self.assertIn("=== rows with 0 < market_probability < 1 (0) ===", out)

    def test_a_market_probability_of_exactly_one_is_not_a_suspect(self):
        """The boundary is exclusive: 1.0 is a legal 1%, not a 0-1 fraction."""
        self._insert_prediction("evt-one", 1.0)

        _, out = self._run()

        self.assertIn("=== rows with 0 < market_probability < 1 (0) ===", out)
        self.assertEqual(self._bucket(out, "1 <= mp <= 100"), 1)

    # --- read-only promise -----------------------------------------------

    def test_the_connection_refuses_to_write(self):
        """``mode=ro`` is the script's whole safety story: it is a census."""
        conn = probe._connect()
        try:
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.execute(
                    "INSERT INTO predictions"
                    " (id, event_id, ai_probability, market_probability, raw_edge)"
                    " VALUES ('x', 'y', 1.0, 1.0, 0.0)"
                )
            self.assertIn("readonly", str(ctx.exception).lower())
        finally:
            conn.close()

    def test_running_the_report_leaves_every_stored_value_alone(self):
        """The end-to-end version of the promise above: byte-identical DB.

        Compared on the row contents as well as the file, because the file check
        alone would not notice a write that happened to be rolled back.
        """
        self._insert_prediction(**AUDITED)
        before_bytes = Path(self.db_path).read_bytes()
        before_row = preds.get_prediction("0779bde4dcd63e08")

        self._run()
        self._run(["--event-id", "0779bde4dcd63e08"])

        self.assertEqual(Path(self.db_path).read_bytes(), before_bytes)
        self.assertEqual(preds.get_prediction("0779bde4dcd63e08"), before_row)

    def test_a_missing_db_exits_naming_the_path(self):
        """A crash log should say which file was missing, not a traceback."""
        missing = Path(self.tmp.name) / "nope" / "v2_loop.db"
        with patch.object(sqlite_db, "loop_db_path", return_value=str(missing)):
            with self.assertRaises(SystemExit) as ctx:
                probe._connect()

        self.assertIn("loop DB not found", str(ctx.exception))
        self.assertIn("v2_loop.db", str(ctx.exception))

    # --- --event-id dump -------------------------------------------------

    def test_event_dump_covers_the_three_tables(self):
        self._insert_prediction("evtDump", 20.0)
        self._insert_trade("evtDump")
        self._insert_link("evtDump")

        rc, out = self._run(["--event-id", "evtDump"])

        self.assertEqual(rc, 0)
        for table in ("predictions", "simulated_trades", "event_market_links"):
            self.assertIn(f"=== {table} WHERE event_id = evtDump ===", out)
        self.assertNotIn("(no row)", out)

    def test_event_dump_marks_a_table_that_has_no_rows(self):
        """A missing table section would read as "nothing to see"; it has to say
        so explicitly, which is what distinguishes it from a truncated dump."""
        self._insert_prediction("evtOnlyPred", 20.0)

        rc, out = self._run(["--event-id", "evtOnlyPred"])

        self.assertEqual(rc, 0)
        self.assertIn(
            "=== simulated_trades WHERE event_id = evtOnlyPred ===", out
        )
        self.assertIn("(no row)", out)

    def test_event_dump_truncates_long_values(self):
        long_text = "Z" * 300
        self._insert_prediction("evtLong", 20.0, snapshot_question=long_text)

        rc, out = self._run(["--event-id", "evtLong"])

        self.assertEqual(rc, 0)
        self.assertIn("Z" * 200 + "...", out)
        self.assertNotIn(long_text, out)

    def test_event_dump_is_skipped_without_the_flag(self):
        self._insert_prediction("evtDump", 20.0)

        _, out = self._run()

        self.assertNotIn("WHERE event_id =", out)


if __name__ == "__main__":
    unittest.main()
