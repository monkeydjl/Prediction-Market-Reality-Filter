"""The list of event-referencing tables must not be hand-maintained.

E2 (跨 JSON/SQLite 无硬 FK). ``loop_status`` reports how many SQLite rows point
at an event that no longer exists in the JSON store. That list of tables used to
be two names written inline in the query -- ``predictions`` and
``event_market_links`` -- while **six** tables carried an ``event_id`` column.
The one genuinely stranded row in the live database sat in ``simulated_trades``,
one of the four unwatched ones, so the dashboard badge read 0 while a real
broken reference existed.

Same defect shape as the isolation list in ``test_singleton_reset_census.py``: a
list a human has to remember to update, guarding something whose failure is
silent. This module closes it the same way -- rebuild the real set from the
source and assert the two declared tables partition it EXACTLY:

    census == REFERENCING_TABLES union EXEMPT_TABLES

Both directions matter. A subset check one way lets a new table slip through; a
subset check the other way lets a stale name rot in the tuple after the table is
renamed or dropped, which would make the census silently stop covering it.
"""
from __future__ import annotations

import pathlib
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory import event_ref_census as census
from app.utils import sqlite_db

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"

# `CREATE TABLE [IF NOT EXISTS] name ( ... )` up to the closing paren that
# starts a line -- the schema constants in app/memory are all written that way.
_CREATE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\n\s*\)",
    re.S | re.I,
)
_ALTER_ADD = re.compile(
    r"ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s+ADD\s+COLUMN\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.I,
)
_EVENT_ID_COL = re.compile(r"^\s*event_id\b", re.M)


def _tables_declaring_event_id() -> dict[str, set[str]]:
    """Scan app/ for every table whose schema declares an ``event_id`` column.

    Reads the source rather than a live database on purpose. A table is created
    lazily on first use, so a freshly-provisioned loop DB is missing most of
    them and a schema scan would report a shrinking census that happens to match
    a shrinking list -- vacuous exactly when it matters. Migrations are picked up
    too (``ALTER TABLE ... ADD COLUMN event_id``), since a column added later is
    the case most likely to be forgotten.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(APP_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, body in _CREATE.findall(text):
            if _EVENT_ID_COL.search(body):
                found.setdefault(name, set()).add(str(path))
        for name, column in _ALTER_ADD.findall(text):
            if column == "event_id":
                found.setdefault(name, set()).add(str(path))
    return found


class EventRefTableCensusTests(unittest.TestCase):
    def test_the_declared_tables_exactly_partition_the_source_census(self):
        census_tables = set(_tables_declaring_event_id())
        declared = set(census.REFERENCING_TABLES) | set(census.EXEMPT_TABLES)

        missing = census_tables - declared
        self.assertFalse(
            missing,
            "Table(s) carry an event_id column but appear in neither "
            "REFERENCING_TABLES nor EXEMPT_TABLES, so rows stranded there are "
            f"invisible to /api/health: {sorted(missing)}. Add each to "
            "REFERENCING_TABLES, or to EXEMPT_TABLES with a written reason.",
        )

        stale = declared - census_tables
        self.assertFalse(
            stale,
            "Declared table(s) no longer declare an event_id column anywhere in "
            f"app/: {sorted(stale)}. A stale name makes the census look wider "
            "than it is.",
        )

    def test_the_two_tables_do_not_overlap(self):
        """A table in both lists would be counted and excused at once."""
        both = set(census.REFERENCING_TABLES) & set(census.EXEMPT_TABLES)
        self.assertFalse(both, f"declared both watched and exempt: {sorted(both)}")

    def test_every_exemption_carries_a_reason(self):
        """An exemption with no written reason is indistinguishable from an
        oversight, which is how the original two-table list looked."""
        for table, reason in census.EXEMPT_TABLES.items():
            self.assertGreater(
                len(reason.strip()), 80,
                f"exemption for {table} needs a reason, not a placeholder",
            )

    def test_the_census_regex_finds_a_known_table(self):
        """Guard against the scan silently matching nothing.

        If the schema constants were reformatted so the regex stopped matching,
        every assertion above would pass against two empty sets.
        """
        found = _tables_declaring_event_id()
        self.assertIn("predictions", found)
        self.assertIn("simulated_trades", found)
        self.assertGreaterEqual(len(found), 6)


def _seed_db(path: str, rows: dict[str, list[str]]) -> None:
    """Create each named table with an event_id column and insert the ids."""
    conn = sqlite3.connect(path)
    try:
        for table, event_ids in rows.items():
            conn.execute(f"CREATE TABLE {table} (event_id TEXT)")
            conn.executemany(
                f"INSERT INTO {table} (event_id) VALUES (?)",
                [(event_id,) for event_id in event_ids],
            )
        conn.commit()
    finally:
        conn.close()


class DanglingCountsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = str(Path(self._tmp.name) / "v2_loop.db")
        patcher = patch.object(sqlite_db, "loop_db_path", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_stranded_row_outside_the_two_original_tables_is_counted(self):
        """The live defect, reproduced: the only broken reference in the real
        database was in simulated_trades, and the reading said zero."""
        _seed_db(self.db, {
            "predictions": ["evt-1"],
            "event_market_links": ["evt-1"],
            "simulated_trades": ["evt-1", "evtExpired"],
        })
        counts = census.dangling_counts({"evt-1"})
        self.assertEqual(counts["simulated_trades"], 1)
        self.assertEqual(counts["predictions"], 0)
        self.assertEqual(counts["event_market_links"], 0)

    def test_every_referencing_table_is_actually_queried(self):
        """One stranded id in each watched table; every one must be counted.

        Pins each table individually rather than the total, so dropping a name
        from REFERENCING_TABLES cannot be masked by another table's count.
        """
        _seed_db(self.db, {
            table: ["gone"] for table in census.REFERENCING_TABLES
        })
        counts = census.dangling_counts({"evt-live"})
        for table in census.REFERENCING_TABLES:
            self.assertEqual(counts[table], 1, f"{table} was not counted")

    def test_the_exempt_table_is_not_counted(self):
        _seed_db(self.db, {
            "predictions": ["evt-1"],
            "domain_reliability_ledger": ["long-gone-event"],
        })
        counts = census.dangling_counts({"evt-1"})
        self.assertNotIn("domain_reliability_ledger", counts)
        self.assertEqual(sum(counts.values()), 0)

    def test_distinct_events_are_counted_not_rows(self):
        """One missing event with forty snapshots is one broken reference."""
        _seed_db(self.db, {"decision_timeline": ["gone"] * 40})
        self.assertEqual(census.dangling_counts(set())["decision_timeline"], 1)

    def test_absent_tables_report_zero_rather_than_raising(self):
        """A fresh deploy has created almost none of these tables yet, and
        /api/health backs the container healthcheck on first boot."""
        _seed_db(self.db, {"predictions": ["gone"]})
        counts = census.dangling_counts(set())
        self.assertEqual(counts["predictions"], 1)
        self.assertEqual(counts["review_queue_items"], 0)
        self.assertEqual(set(counts), set(census.REFERENCING_TABLES))

    def test_blank_and_null_event_ids_are_not_references(self):
        """A row with no event_id points at nothing; it is not stranded."""
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE predictions (event_id TEXT)")
        conn.executemany(
            "INSERT INTO predictions (event_id) VALUES (?)",
            [(None,), ("",), ("gone",)],
        )
        conn.commit()
        conn.close()
        self.assertEqual(census.dangling_counts(set())["predictions"], 1)

    def test_an_unreadable_database_reports_zeros_rather_than_raising(self):
        with patch.object(sqlite_db, "loop_db_path", return_value="/nope/x.db"):
            counts = census.dangling_counts({"evt-1"})
        self.assertEqual(set(counts), set(census.REFERENCING_TABLES))
        self.assertEqual(sum(counts.values()), 0)

    def test_the_whole_census_uses_one_connection(self):
        """Five tables must not mean five connect-and-close cycles on an
        endpoint container healthchecks poll (E1's lesson)."""
        _seed_db(self.db, {t: ["gone"] for t in census.REFERENCING_TABLES})
        real_connect = sqlite_db.connect
        calls = []

        def counting(path, *args, **kwargs):
            calls.append(path)
            return real_connect(path, *args, **kwargs)

        with patch.object(sqlite_db, "connect", side_effect=counting):
            census.dangling_counts({"evt-live"})
        self.assertEqual(len(calls), 1, f"opened {len(calls)} connections")


class RefsForEventTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = str(Path(self._tmp.name) / "v2_loop.db")
        patcher = patch.object(sqlite_db, "loop_db_path", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_reports_rows_per_table_for_the_named_event(self):
        _seed_db(self.db, {
            "predictions": ["evt-1", "evt-2"],
            "simulated_trades": ["evt-1", "evt-1"],
            "review_queue_items": ["evt-2"],
        })
        self.assertEqual(
            census.refs_for_event("evt-1"),
            {"predictions": 1, "simulated_trades": 2},
        )

    def test_rows_are_counted_not_distinct_ids(self):
        """The caller is about to strand these rows and wants the volume; two
        open trades on one event is two things left pointing at nothing."""
        _seed_db(self.db, {"simulated_trades": ["evt-1"] * 3})
        self.assertEqual(census.refs_for_event("evt-1")["simulated_trades"], 3)

    def test_an_event_with_no_rows_reports_nothing(self):
        _seed_db(self.db, {"predictions": ["evt-other"]})
        self.assertEqual(census.refs_for_event("evt-1"), {})

    def test_an_empty_event_id_matches_nothing(self):
        """Rows with a blank event_id must not be attributed to a delete."""
        _seed_db(self.db, {"predictions": ["", "evt-1"]})
        self.assertEqual(census.refs_for_event(""), {})

    def test_the_exempt_table_is_not_reported(self):
        _seed_db(self.db, {
            "predictions": ["evt-1"],
            "domain_reliability_ledger": ["evt-1"],
        })
        self.assertEqual(census.refs_for_event("evt-1"), {"predictions": 1})

    def test_an_unreadable_database_reports_nothing_rather_than_raising(self):
        """The delete must not 500 after the JSON record is already gone."""
        with patch.object(sqlite_db, "loop_db_path", return_value="/nope/x.db"):
            self.assertEqual(census.refs_for_event("evt-1"), {})


if __name__ == "__main__":
    unittest.main()
