"""A census is only worth running if its numbers can move.

`scripts/verify_store_counts.py` exists because `restore_stores.py` proves the
bytes it wrote match the bytes it read, which cannot tell you the archive was the
right one or that the store you care about was in it. An archive written before
2026-08-28 holds four of the eight stores and restores cleanly.

The trap this file is shaped around, measured on the live install: `sports_facts.json`
is `{"updated_at": ..., "facts": [186 items]}`. A census that counts top-level keys
reports **2** for it, and reports 2 both before and after a restore that dropped
every one of those 186 facts. So the per-store counter is asserted to read the
payload, not the wrapper, and `COUNTERS` is asserted to be an exact partition of
the declared state stores rather than a hand-kept list that falls behind config —
the defect shape from #62 and #66.

The second trap: `compare()` must be *asymmetric*. Losing records is a failure,
gaining them is not, and a test that only ever feeds it identical censuses proves
neither.
"""
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import runtime_stores
from scripts import verify_store_counts as vsc


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def _make_sqlite(path: Path, *, rows: int = 5, tables: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        for t in range(tables):
            conn.execute(f"CREATE TABLE t{t} (id INTEGER PRIMARY KEY, v TEXT)")
            conn.executemany(
                f"INSERT INTO t{t} (v) VALUES (?)", [(f"r{i}",) for i in range(rows)]
            )
        conn.commit()
    finally:
        conn.close()


class CounterShapeTests(unittest.TestCase):
    """Each counter has to read the records, not the container."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_facts_counter_reads_the_list_not_the_wrapper(self):
        """The whole reason this file exists.

        Two top-level keys, 186 facts. A top-level count answers 2 and cannot
        distinguish a healthy store from one that lost every fact.
        """
        path = self.root / "sports_facts.json"
        _write_json(path, {"updated_at": "2026-08-29T00:00:00Z", "facts": [{"i": i} for i in range(186)]})
        self.assertEqual(vsc._count_json_facts(path), {"facts": 186})

        gutted = self.root / "gutted.json"
        _write_json(gutted, {"updated_at": "2026-08-29T00:00:00Z", "facts": []})
        self.assertEqual(vsc._count_json_facts(gutted), {"facts": 0})

    def test_a_top_level_count_would_not_have_moved(self):
        """Names the defect the facts counter avoids, so a 'simplification' fails.

        Both files below have exactly two top-level keys. Anything counting those
        reports the same number for a full store and an empty one.
        """
        full = self.root / "full.json"
        empty = self.root / "empty.json"
        _write_json(full, {"updated_at": "x", "facts": [{"i": i} for i in range(186)]})
        _write_json(empty, {"updated_at": "x", "facts": []})
        with io.open(full, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)), 2)
        with io.open(empty, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)), 2)
        self.assertNotEqual(
            vsc._count_json_facts(full), vsc._count_json_facts(empty),
            "the counter must distinguish these two; a top-level count cannot",
        )

    def test_json_mapping_counts_records(self):
        path = self.root / "event_store.json"
        _write_json(path, {f"id{i}": {"title": i} for i in range(7)})
        self.assertEqual(vsc._count_json_mapping(path), {"records": 7})

    def test_jsonl_ignores_blank_lines(self):
        path = self.root / "audit.jsonl"
        path.write_text('{"a":1}\n\n{"a":2}\n   \n{"a":3}\n', encoding="utf-8")
        self.assertEqual(vsc._count_jsonl(path), {"records": 3})

    def test_sqlite_counts_per_table_and_totals(self):
        path = self.root / "x.db"
        _make_sqlite(path, rows=5, tables=2)
        counts = vsc._count_sqlite(path)
        self.assertEqual(counts["t0"], 5)
        self.assertEqual(counts["t1"], 5)
        self.assertEqual(counts["TOTAL"], 10)

    def test_sqlite_skips_internal_tables(self):
        """`sqlite_sequence` is SQLite's bookkeeping, not restored records."""
        path = self.root / "auto.db"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
            conn.execute("INSERT INTO a (v) VALUES ('x')")
            conn.commit()
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        self.assertIn("sqlite_sequence", names, "fixture did not produce the internal table")
        self.assertEqual(vsc._count_sqlite(path), {"a": 1, "TOTAL": 1})

    def test_a_mapping_counter_rejects_a_list(self):
        path = self.root / "wrong.json"
        _write_json(path, [1, 2, 3])
        with self.assertRaises(ValueError):
            vsc._count_json_mapping(path)

    def test_a_facts_counter_rejects_a_missing_list(self):
        path = self.root / "nofacts.json"
        _write_json(path, {"updated_at": "x"})
        with self.assertRaises(ValueError):
            vsc._count_json_facts(path)


class ReadOnlyTests(unittest.TestCase):
    """An operator runs this against production. It must not touch anything."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_counting_changes_none_of_the_stores_own_bytes(self):
        """The guarantee that matters, on a real WAL database.

        Measured while writing this: reading a WAL database through `mode=ro`
        **does** materialise a 32 KB `-shm` and a 0-byte `-wal`. That is SQLite
        needing the shared-memory index to coordinate with possible writers, not
        the census opening the store for writing — the paired test below shows a
        write through the same URI is refused. It is deliberately *not* cleaned
        up: a `-wal` sitting next to a live database can hold committed frames,
        and deleting one would destroy data. So the assertion is on the store's
        own bytes, which is the property a census must not disturb.

        (An earlier version of this test asserted no sidecars appear and passed
        only because its fixture was not actually WAL: `PRAGMA journal_mode=WAL`
        issued inside an open transaction returns `delete` and persists `delete`.
        Hence the explicit journal-mode assertion below.)
        """
        import hashlib

        path = self.root / "live.db"
        _make_sqlite(path, rows=4, tables=1)
        conn = sqlite3.connect(str(path))
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(mode, "wal", "fixture is not a WAL database, so it proves nothing")
        for sidecar in ("-wal", "-shm"):
            Path(str(path) + sidecar).unlink(missing_ok=True)

        before = hashlib.md5(path.read_bytes()).hexdigest()
        counts = vsc._count_sqlite(path)
        after = hashlib.md5(path.read_bytes()).hexdigest()

        self.assertEqual(counts["TOTAL"], 4, "the fixture was not read at all")
        self.assertEqual(before, after, "the census rewrote the database file")

    def test_the_census_connection_cannot_write(self):
        """Pins `mode=ro` itself, which is what makes the tool safe on production.

        Without this, dropping `?mode=ro` from the URI would break no test: the
        byte-comparison above passes for a read-write connection too, because
        counting rows happens not to write anything.
        """
        path = self.root / "ro.db"
        _make_sqlite(path, rows=2, tables=1)
        # Through the script's own opener, not a URI rebuilt here: an earlier
        # version constructed `file:...?mode=ro` in the test, so removing
        # `mode=ro` from production broke no write assertion at all.
        conn = vsc.connect_readonly(path)
        try:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                conn.execute("INSERT INTO t0 (v) VALUES ('written')")
                conn.commit()
            self.assertIn("readonly", str(caught.exception).lower())
        finally:
            conn.close()

    def test_counting_a_missing_database_does_not_create_one(self):
        path = self.root / "absent.db"
        with self.assertRaises(sqlite3.OperationalError):
            vsc._count_sqlite(path)
        self.assertFalse(path.exists(), "read-only mode must not create the file")


class DeclaredPartitionTests(unittest.TestCase):
    """`COUNTERS` must cover the declared stores exactly, not approximately.

    Same shape as the backup list in #62 and the maintenance list in #66: a
    hand-kept list is complete the day it is written and nothing says when it
    stopped being complete. Asserting an exact partition against the source of
    truth is what makes a new store impossible to forget.
    """

    def test_counters_is_an_exact_partition_of_the_declared_state_stores(self):
        declared = set(runtime_stores.state_setting_names())
        self.assertGreaterEqual(len(declared), 8, "the scan found fewer stores than exist")
        self.assertEqual(
            set(vsc.COUNTERS),
            declared,
            "COUNTERS and STATE_STORES disagree: a store is either uncounted or "
            "counted but no longer declared",
        )

    def test_every_sqlite_state_store_uses_the_sqlite_counter(self):
        for name in runtime_stores.sqlite_state_settings():
            with self.subTest(store=name):
                self.assertIs(vsc.COUNTERS[name], vsc._count_sqlite)

    def test_a_store_with_no_counter_is_reported_not_skipped(self):
        """An uncounted store must be loud, because a census that silently omits
        one reports a smaller world and still says `ok`."""
        with patch.object(
            runtime_stores, "state_setting_names",
            return_value=tuple(runtime_stores.state_setting_names()) + ("BRAND_NEW_STORE_FILE",),
        ):
            result = vsc.census()
        self.assertIn("BRAND_NEW_STORE_FILE", result["stores"])
        self.assertIn("error", result["stores"]["BRAND_NEW_STORE_FILE"])


def _census(**stores: dict[str, int]) -> dict[str, object]:
    return {"stores": {name: {"path": name, "counts": c} for name, c in stores.items()}}


class CompareTests(unittest.TestCase):
    """Losing records fails; gaining them does not. Both arms are needed.

    A test that only feeds `compare()` identical censuses proves nothing: a
    function hardwired to `ok: True` and one hardwired to `ok: False` would each
    pass half of it. Every case below is paired with its opposite.
    """

    def test_identical_censuses_are_ok(self):
        a = _census(KERNEL_DB_FILE={"t": 10, "TOTAL": 10})
        diff = vsc.compare(a, a)
        self.assertTrue(diff["ok"], diff)
        self.assertEqual(diff["losses"], [])
        self.assertEqual(diff["changed"], [])

    def test_a_decrease_is_a_loss(self):
        before = _census(KERNEL_DB_FILE={"t": 33882, "TOTAL": 33882})
        after = _census(KERNEL_DB_FILE={"t": 12, "TOTAL": 12})
        diff = vsc.compare(before, after)
        self.assertFalse(diff["ok"])
        self.assertTrue(any("33882 -> 12" in loss for loss in diff["losses"]), diff["losses"])

    def test_an_increase_is_not_a_loss(self):
        """The archive can legitimately be newer than a table nobody touched."""
        before = _census(LOOP_DB_FILE={"t": 10, "TOTAL": 10})
        after = _census(LOOP_DB_FILE={"t": 40, "TOTAL": 40})
        diff = vsc.compare(before, after)
        self.assertTrue(diff["ok"], diff)
        self.assertEqual(diff["losses"], [])
        self.assertTrue(diff["changed"], "an increase must still be reported as changed")

    def test_a_store_present_before_and_missing_after_is_a_loss(self):
        before = _census(SPORTS_FACT_FILE={"facts": 186})
        after = {"stores": {"SPORTS_FACT_FILE": {"path": "x", "missing": True}}}
        diff = vsc.compare(before, after)
        self.assertFalse(diff["ok"])
        self.assertTrue(any("missing after" in loss for loss in diff["losses"]), diff["losses"])

    def test_a_store_missing_before_and_present_after_is_ok(self):
        """This is the normal shape of a successful restore."""
        before = {"stores": {"SPORTS_FACT_FILE": {"path": "x", "missing": True}}}
        after = _census(SPORTS_FACT_FILE={"facts": 186})
        diff = vsc.compare(before, after)
        self.assertTrue(diff["ok"], diff)

    def test_a_vanished_table_is_a_loss_even_when_others_grow(self):
        """A total that went up can hide a table that went away."""
        before = _census(KERNEL_DB_FILE={"kept": 10, "dropped": 5, "TOTAL": 15})
        after = _census(KERNEL_DB_FILE={"kept": 999, "TOTAL": 999})
        diff = vsc.compare(before, after)
        self.assertFalse(diff["ok"], "the dropped table was not reported")
        self.assertTrue(any("dropped" in loss for loss in diff["losses"]), diff["losses"])

    def test_the_partial_archive_scenario_this_tool_exists_for(self):
        """Restoring a pre-2026-08-28 archive: four stores land, four vanish."""
        before = _census(
            EVENT_STORE_FILE={"records": 235},
            LOOP_DB_FILE={"TOTAL": 2157},
            KERNEL_DB_FILE={"TOTAL": 33882},
            SPORTS_FACT_FILE={"facts": 186},
        )
        after = {
            "stores": {
                "EVENT_STORE_FILE": {"path": "x", "counts": {"records": 235}},
                "LOOP_DB_FILE": {"path": "x", "counts": {"TOTAL": 2157}},
                "KERNEL_DB_FILE": {"path": "x", "missing": True},
                "SPORTS_FACT_FILE": {"path": "x", "missing": True},
            }
        }
        diff = vsc.compare(before, after)
        self.assertFalse(diff["ok"])
        self.assertEqual(len(diff["losses"]), 4, diff["losses"])
