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
# First token of a column-definition line inside a CREATE TABLE body.
_COLUMN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\b", re.M)
# Table-level clauses that start a line but declare no column.
_NON_COLUMN_LEADERS = {"CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN"}
# The column a live predicate filters on, e.g. "status = 'open'" -> "status".
_PREDICATE_COLUMN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|IN\b|IS\b|LIKE\b)", re.I
)


def _declared_columns() -> dict[str, set[str]]:
    """Column names each table's CREATE TABLE constant declares, from source.

    Same reason as ``_tables_declaring_event_id``: a live database only holds
    the tables some code path has already touched, so a schema scan of it would
    quietly narrow as the deployment shrinks.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(APP_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, body in _CREATE.findall(text):
            for line in body.splitlines():
                match = _COLUMN.match(line)
                if not match:
                    continue
                token = match.group(1)
                if token.upper() in _NON_COLUMN_LEADERS:
                    continue
                found.setdefault(name, set()).add(token)
    return found


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

    def test_every_referencing_table_declares_a_live_predicate(self):
        """The actionable reading and the census must cover the same tables.

        Both directions: a watched table with no predicate would silently read
        zero there (a live stranded row hidden), and a predicate for a table
        the census no longer watches would be dead weight nobody notices.
        """
        declared = set(census.LIVE_PREDICATES)
        watched = set(census.REFERENCING_TABLES)

        self.assertFalse(
            watched - declared,
            "watched table(s) with no live predicate, so their actionable "
            f"count would always read zero: {sorted(watched - declared)}. Add "
            "each to LIVE_PREDICATES, using None if nothing can act on it.",
        )
        self.assertFalse(
            declared - watched,
            f"LIVE_PREDICATES names table(s) the census does not watch: "
            f"{sorted(declared - watched)}",
        )

    def test_every_live_predicate_names_a_column_its_table_declares(self):
        """A predicate on a missing column turns into a silent full count.

        ``actionable_dangling_counts`` falls back to counting every row when the
        column is absent, so a renamed column would not fail loudly -- it would
        quietly report more stranded references than there are. Pin the names
        against the CREATE TABLE constants instead.
        """
        declared = _declared_columns()
        for table, predicate in census.LIVE_PREDICATES.items():
            if predicate is None:
                continue
            match = _PREDICATE_COLUMN.match(predicate)
            self.assertIsNotNone(
                match, f"{table}: cannot read a column name out of {predicate!r}"
            )
            column = match.group(1)
            self.assertIn(
                column,
                declared.get(table, set()),
                f"{table}: live predicate filters on {column!r}, which the "
                f"table's CREATE TABLE does not declare (has "
                f"{sorted(declared.get(table, set()))})",
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


def _seed_typed(
    path: str, schema: dict[str, str], rows: dict[str, list[tuple]]
) -> None:
    """Create each table with the given column list, then insert the rows."""
    conn = sqlite3.connect(path)
    try:
        for table, columns in schema.items():
            conn.execute(f"CREATE TABLE {table} ({columns})")
        for table, values in rows.items():
            width = len(values[0])
            conn.executemany(
                f"INSERT INTO {table} VALUES ({', '.join('?' * width)})", values
            )
        conn.commit()
    finally:
        conn.close()


class ActionableDanglingCountsTests(unittest.TestCase):
    """Only rows whose next step is blocked by the missing event are counted.

    ``dangling_counts`` answers "how many rows point at a deleted event" -- a
    number that can never return to zero once anything is purged. This reading
    answers "how much work is still blocked", which is what an operator can act
    on and what the dashboard badge shows (audit 53.95).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = str(Path(self._tmp.name) / "v2_loop.db")
        patcher = patch.object(sqlite_db, "loop_db_path", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_terminal_simulated_trades_are_not_actionable(self):
        """The live case: a voided trade whose event was purged.

        Distinct event ids per status on purpose: the census counts distinct
        ids, so three rows sharing one id would be 1 either way and the
        predicate would carry no weight (audit 54.2 -- an assertion has to be
        sensitive to the mutation it is supposed to catch).
        """
        _seed_typed(
            self.db,
            {"simulated_trades": "event_id TEXT, status TEXT"},
            {"simulated_trades": [
                ("gone-open", "open"),
                ("gone-closed", "closed"),
                ("gone-voided", "voided"),
            ]},
        )
        total = census.dangling_counts({"evt-live"})["simulated_trades"]
        actionable = census.actionable_dangling_counts({"evt-live"})["simulated_trades"]
        self.assertEqual(total, 3)
        self.assertEqual(actionable, 1)

    def test_only_open_predictions_are_actionable(self):
        _seed_typed(
            self.db,
            {"predictions": "event_id TEXT, status TEXT"},
            {"predictions": [
                ("gone-open", "open"),
                ("gone-scored", "scored"),
                ("gone-voided", "voided"),
            ]},
        )
        total = census.dangling_counts({"evt-live"})["predictions"]
        actionable = census.actionable_dangling_counts({"evt-live"})["predictions"]
        self.assertEqual(total, 3)
        self.assertEqual(actionable, 1)

    def test_only_pending_review_items_are_actionable(self):
        _seed_typed(
            self.db,
            {"review_queue_items": "event_id TEXT, status TEXT"},
            {"review_queue_items": [
                ("gone-pending", "pending"),
                ("gone-resolved", "resolved"),
            ]},
        )
        total = census.dangling_counts({"evt-live"})["review_queue_items"]
        actionable = census.actionable_dangling_counts({"evt-live"})[
            "review_queue_items"
        ]
        self.assertEqual(total, 2)
        self.assertEqual(actionable, 1)

    def test_only_unverified_links_are_actionable(self):
        """A verified link is a settled binding; an unverified one still needs
        the human review that the missing event just made impossible."""
        _seed_typed(
            self.db,
            {"event_market_links": "event_id TEXT, verified INTEGER"},
            {"event_market_links": [("gone-unverified", 0), ("gone-verified", 1)]},
        )
        total = census.dangling_counts({"evt-live"})["event_market_links"]
        actionable = census.actionable_dangling_counts({"evt-live"})[
            "event_market_links"
        ]
        self.assertEqual(total, 2)
        self.assertEqual(actionable, 1)

    def test_the_timeline_is_never_actionable(self):
        """Snapshots record decisions already taken; nothing waits on them.

        Pinned explicitly because this is the table that would otherwise keep
        the actionable reading permanently non-zero after any purge.
        """
        _seed_typed(
            self.db,
            {"decision_timeline": "event_id TEXT, snapshot_id TEXT"},
            {"decision_timeline": [("gone", str(i)) for i in range(40)]},
        )
        actionable = census.actionable_dangling_counts({"evt-live"})
        self.assertEqual(actionable["decision_timeline"], 0)
        self.assertEqual(census.dangling_counts({"evt-live"})["decision_timeline"], 1)

    def test_rows_pointing_at_a_live_event_are_not_counted(self):
        _seed_typed(
            self.db,
            {"simulated_trades": "event_id TEXT, status TEXT"},
            {"simulated_trades": [("evt-1", "open"), ("evt-2", "open")]},
        )
        self.assertEqual(
            census.actionable_dangling_counts({"evt-1", "evt-2"})["simulated_trades"],
            0,
        )

    def test_a_missing_predicate_column_falls_back_to_counting_every_row(self):
        """Over-report rather than hide: an unclassifiable row is still a row.

        The table has no ``status`` column, so the predicate cannot run. Reading
        zero here would silently drop a live stranded reference.
        """
        _seed_typed(
            self.db,
            {"simulated_trades": "event_id TEXT"},
            {"simulated_trades": [("gone",), ("gone",)]},
        )
        with self.assertLogs("app.memory.event_ref_census", level="INFO") as logs:
            counts = census.actionable_dangling_counts({"evt-live"})
        self.assertEqual(counts["simulated_trades"], 1)  # distinct ids, not rows
        self.assertTrue(
            any("does not match its schema" in line for line in logs.output),
            logs.output,
        )

    def test_the_actionable_reading_never_exceeds_the_total(self):
        """Per table, actionable <= dangling: the filter only removes rows."""
        _seed_typed(
            self.db,
            {
                "simulated_trades": "event_id TEXT, status TEXT",
                "event_market_links": "event_id TEXT, verified INTEGER",
            },
            {
                "simulated_trades": [("gone-open", "open"), ("gone-voided", "voided")],
                "event_market_links": [("gone-unverified", 0), ("gone-verified", 1)],
            },
        )
        total = census.dangling_counts({"evt-live"})
        actionable = census.actionable_dangling_counts({"evt-live"})
        self.assertEqual(set(actionable), set(census.REFERENCING_TABLES))
        for table, count in actionable.items():
            self.assertLessEqual(count, total[table], table)

    def test_an_unreadable_database_reports_zeros_rather_than_raising(self):
        with patch.object(sqlite_db, "loop_db_path", return_value="/nope/x.db"):
            counts = census.actionable_dangling_counts({"evt-1"})
        self.assertEqual(set(counts), set(census.REFERENCING_TABLES))
        self.assertEqual(sum(counts.values()), 0)

    def test_the_whole_census_uses_one_connection(self):
        _seed_typed(
            self.db,
            {t: "event_id TEXT" for t in census.REFERENCING_TABLES},
            {t: [("gone",)] for t in census.REFERENCING_TABLES},
        )
        real_connect = sqlite_db.connect
        calls = []

        def counting(path, *args, **kwargs):
            calls.append(path)
            return real_connect(path, *args, **kwargs)

        with patch.object(sqlite_db, "connect", side_effect=counting):
            census.actionable_dangling_counts({"evt-live"})
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
