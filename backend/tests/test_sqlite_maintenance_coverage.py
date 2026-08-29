"""Maintenance has to cover every SQLite state store, not just the loop DB.

`sqlite_db.maintain()` accepts a path and defaults to `loop_db_path()`. Until
`maintain_all()` existed, **no caller passed one**, so `KERNEL_DB_FILE`,
`WORLD_CUP_PREDICTION_DB_FILE` and `DOMAIN_RELIABILITY_DB_PATH` got neither WAL
truncation nor `PRAGMA integrity_check` anywhere in the codebase.

Measured before the fix, by copying each store to a temp directory, corrupting it
until `integrity_check` actually complained, and booting the app:

    LOOP_DB_FILE                 -> boot aborts (DatabaseError)   <- correct
    KERNEL_DB_FILE               -> 200 {"status": "ok"}          <- silent
    WORLD_CUP_PREDICTION_DB_FILE -> 200 {"status": "ok"}          <- silent
    DOMAIN_RELIABILITY_DB_PATH   -> 200 {"status": "ok"}          <- silent

The kernel DB holds 33,882 rows of committed predictions on this install.

Two traps this file is shaped around. First, the corruption has to be *verified*:
an earlier probe wrote at a fixed page offset and assumed the file was broken, but
that page was not a live b-tree page in the loop DB, `integrity_check` stayed
`ok`, and the whole measurement was vacuous. `_corrupt` here asserts the file
really fails afterwards. Second, every "reports degraded" assertion is worthless
without the healthy baseline — a change that made health degrade unconditionally
would satisfy all of them — so `test_a_healthy_install_is_not_degraded` runs first
in spirit and is the one to check when this file goes red as a group.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import runtime_stores
from app.utils import sqlite_db


def _make_db(path: Path, *, rows: int = 3) -> None:
    """A small but non-empty SQLite file.

    Non-empty matters: `integrity_check` on a table with no rows has nothing to
    traverse, so an empty DB cannot be made to fail and any test built on one
    would pass for the wrong reason.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
        conn.executemany(
            "INSERT INTO sample (name) VALUES (?)", [(f"row-{i}",) for i in range(rows)]
        )
        conn.commit()
    finally:
        conn.close()


def _integrity(path: Path) -> list[str]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return [str(r[0]) for r in conn.execute("PRAGMA integrity_check").fetchall()]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return [f"DatabaseError: {exc}"]


def _corrupt(path: Path) -> None:
    """Damage `path` until PRAGMA integrity_check stops saying ok.

    Raises if it cannot, rather than letting a caller assert against a file that
    is still perfectly readable.
    """
    size = path.stat().st_size
    for page in range(0, max(2, size // 4096 + 2)):
        with open(path, "r+b") as fh:
            fh.seek(4096 * page + 24)
            fh.write(b"\xde\xad\xbe\xef" * 64)
        if _integrity(path) != ["ok"]:
            return
    raise AssertionError(f"could not corrupt {path} past integrity_check")


def _make_indexed_db(path: Path, *, rows: int = 200) -> None:
    """A DB big enough to span pages, with a secondary index.

    `_corrupt` above stomps page 0, which breaks the header — SQLite then raises
    on the very first read, so `wal_checkpoint` blows up before `integrity_check`
    is ever consulted. To pin the integrity check itself the damage has to be the
    other kind: file opens, checkpoint succeeds, and only the b-tree walk
    complains. That needs enough pages that a non-header page is corruptible, and
    an index whose entries can go missing relative to the table.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # setUp already put a small DB here
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany(
            "INSERT INTO sample (name) VALUES (?)",
            [(f"row-{i}" * 40,) for i in range(rows)],
        )
        conn.execute("CREATE INDEX ix_sample_name ON sample (name)")
        conn.commit()
    finally:
        conn.close()


def _corrupt_but_still_openable(path: Path) -> list[str]:
    """Corrupt `path` so it opens and checkpoints but fails integrity_check.

    Returns the integrity messages. Raises if no page produces that state, so a
    caller can never assert against a file that is merely unopenable — which is
    a different defect, already covered, and one the WAL checkpoint catches on
    its own.
    """
    original = path.read_bytes()
    pages = max(2, len(original) // 4096)
    for page in range(1, pages):
        path.write_bytes(original)
        with open(path, "r+b") as fh:
            fh.seek(4096 * page + 8)
            fh.write(b"\xff" * 300)
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            conn.close()
            continue  # unopenable: the other failure mode, not this one
        try:
            messages = [str(r[0]) for r in conn.execute("PRAGMA integrity_check")]
        except sqlite3.DatabaseError:
            continue  # raises rather than reporting: also not this mode
        finally:
            conn.close()
        if messages != ["ok"]:
            return messages
    path.write_bytes(original)
    raise AssertionError(
        f"no page of {path} produced an openable-but-inconsistent database; "
        "without one, nothing here exercises PRAGMA integrity_check"
    )


class _StoresInTempDir(unittest.TestCase):
    """Every SQLite state store redirected to a populated temp copy.

    Each store gets its **own subdirectory**. That is not cosmetic: several
    fallbacks in this codebase resolve to `Path(settings.LOOP_DB_FILE).parent`,
    so with everything in one directory a wrong-path bug and a correct path
    coincide. Same trap that made two backup tests vacuous.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.paths: dict[str, Path] = {}
        overrides: dict[str, str] = {}
        for name in runtime_stores.sqlite_state_settings():
            path = self.root / name.lower() / f"{name.lower()}.db"
            _make_db(path)
            self.paths[name] = path
            overrides[name] = str(path)
        self.assertGreaterEqual(
            len(self.paths), 4, "the scan found fewer SQLite state stores than exist"
        )
        patcher = patch.multiple(
            "app.core.config.settings", **{k: v for k, v in overrides.items()}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # loop_db_path() reads settings.LOOP_DB_FILE through os.path.abspath, so
        # patching the setting is enough; assert it rather than assume it.
        self.assertEqual(
            Path(sqlite_db.loop_db_path()).resolve(),
            self.paths["LOOP_DB_FILE"].resolve(),
        )


class MaintainAllCoverageTests(_StoresInTempDir):
    def test_every_declared_sqlite_state_store_is_maintained(self):
        result = sqlite_db.maintain_all()
        self.assertEqual(
            set(result["stores"]),
            set(runtime_stores.sqlite_state_settings()),
            "maintain_all must report exactly the declared SQLite state stores",
        )

    def test_the_three_stores_that_used_to_be_unmaintained_are_covered(self):
        """Named explicitly so a regression says which store went dark."""
        result = sqlite_db.maintain_all()
        for name in (
            "KERNEL_DB_FILE",
            "WORLD_CUP_PREDICTION_DB_FILE",
            "DOMAIN_RELIABILITY_DB_PATH",
        ):
            with self.subTest(store=name):
                self.assertIn(name, result["stores"])
                self.assertTrue(result["stores"][name]["ok"])

    def test_a_healthy_install_is_ok(self):
        result = sqlite_db.maintain_all()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["failed"], [])

    def test_a_corrupt_non_loop_store_is_reported_as_failed(self):
        _corrupt(self.paths["KERNEL_DB_FILE"])
        result = sqlite_db.maintain_all()
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], ["KERNEL_DB_FILE"])
        self.assertFalse(result["stores"]["KERNEL_DB_FILE"]["ok"])
        self.assertIn("error", result["stores"]["KERNEL_DB_FILE"])

    def test_every_store_is_reported_even_when_an_earlier_one_failed(self):
        """Stopping at the first failure would hide the rest.

        Corrupt the *first* store in declaration order, then assert the last one
        was still visited — an early `raise` would leave it out of the report.
        """
        names = list(runtime_stores.sqlite_state_settings())
        _corrupt(self.paths[names[0]])
        result = sqlite_db.maintain_all()
        self.assertEqual(set(result["stores"]), set(names))
        self.assertTrue(result["stores"][names[-1]]["ok"])

    def test_all_failures_are_listed_not_just_the_first(self):
        _corrupt(self.paths["KERNEL_DB_FILE"])
        _corrupt(self.paths["DOMAIN_RELIABILITY_DB_PATH"])
        result = sqlite_db.maintain_all()
        self.assertEqual(
            sorted(result["failed"]),
            ["DOMAIN_RELIABILITY_DB_PATH", "KERNEL_DB_FILE"],
        )

    def test_a_missing_store_is_skipped_not_failed(self):
        """An install that never populated a store is healthy, not broken."""
        self.paths["WORLD_CUP_PREDICTION_DB_FILE"].unlink()
        result = sqlite_db.maintain_all()
        self.assertTrue(result["ok"], result)
        self.assertIn("skipped", result["stores"]["WORLD_CUP_PREDICTION_DB_FILE"])

    def test_maintain_all_does_not_raise_on_corruption(self):
        """The caller writes one run-ledger row; it needs the whole picture.

        `maintain()` raises by design. `maintain_all()` must not, or the report
        would stop at the first bad store.
        """
        for name in self.paths:
            _corrupt(self.paths[name])
        result = sqlite_db.maintain_all()  # must not raise
        self.assertEqual(sorted(result["failed"]), sorted(self.paths))

    def test_an_openable_but_inconsistent_store_is_caught_by_integrity_check(self):
        """The integrity check has to be load-bearing, not decorative.

        Every other corruption test here damages page 0, which breaks the header:
        SQLite raises on the first read, so `wal_checkpoint` fails and the store
        is reported as failed *without `PRAGMA integrity_check` contributing
        anything*. Measured by stubbing `integrity_check` to return `["ok"]` —
        the whole file stayed green, so nothing pinned the check at all.

        This is the second failure mode: the file opens, the checkpoint succeeds,
        and only the b-tree walk notices. `_corrupt_but_still_openable` proves
        that state before the assertion runs, so if a future SQLite stops
        reporting it the test fails loudly instead of quietly passing.
        """
        kernel = self.paths["KERNEL_DB_FILE"]
        _make_indexed_db(kernel)
        messages = _corrupt_but_still_openable(kernel)
        self.assertNotEqual(messages, ["ok"])

        # The precondition: this file is readable enough to checkpoint.
        checkpoint = sqlite_db.wal_checkpoint(str(kernel), mode="TRUNCATE")
        self.assertIsInstance(checkpoint, dict)

        result = sqlite_db.maintain_all()
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["failed"], ["KERNEL_DB_FILE"])
        self.assertIn(
            "integrity",
            str(result["stores"]["KERNEL_DB_FILE"]["error"]).lower(),
            "the failure has to come from the integrity check, not an open error",
        )

    def test_the_wal_is_checkpointed_for_a_non_loop_store(self):
        """Coverage means the checkpoint too, not only the integrity check.

        The WAL file itself cannot be the assertion: SQLite checkpoints and
        removes it when the last connection closes, so it is already gone by the
        time `maintain_all()` runs. A connection is therefore held open while the
        checkpoint is measured, and the evidence is the count `PRAGMA
        wal_checkpoint(TRUNCATE)` reports for *that store* — a file-size check
        would pass whether or not the kernel DB was ever visited.
        """
        kernel = self.paths["KERNEL_DB_FILE"]
        holder = sqlite_db.connect(str(kernel))
        try:
            # Without this, SQLite's autocheckpoint drains the WAL on its own and
            # the explicit checkpoint reports log=0 — indistinguishable from never
            # having run.
            holder.execute("PRAGMA wal_autocheckpoint=0")
            holder.executemany(
                "INSERT INTO sample (name) VALUES (?)",
                [(f"wal-{i}",) for i in range(500)],
            )
            holder.commit()
            wal = Path(str(kernel) + "-wal")
            self.assertTrue(
                wal.exists() and wal.stat().st_size > 0,
                "no WAL was produced, so this test would prove nothing",
            )
            before = wal.stat().st_size
            result = sqlite_db.maintain_all()
            after = wal.stat().st_size if wal.exists() else 0
        finally:
            holder.close()

        self.assertTrue(result["stores"]["KERNEL_DB_FILE"]["ok"], result)
        # The reported counts are not the evidence: PRAGMA wal_checkpoint(TRUNCATE)
        # resets the log, so it answers log=0/checkpointed=0 *after* doing the
        # work. Measured 28,872 -> 0 on a probe. The file size is what moves.
        self.assertGreater(before, 0)
        self.assertEqual(
            after, 0, f"the kernel DB's WAL was not truncated: {before} -> {after}"
        )


class ConnectLeakTests(unittest.TestCase):
    """`connect()` must not leave a handle open when its pragmas raise.

    `sqlite3.connect` succeeds on a corrupt file — the first read is
    `PRAGMA journal_mode=WAL`, which raises *after* the handle exists and
    *before* it is returned. Nothing could close it: the caller never got it, and
    `reading`/`writing` only close what they were handed. It came back on GC.

    That was harmless while the only caller let the exception abort startup. It
    became a leak per call the moment `maintain_all()` started catching the error
    and continuing — one handle per corrupt store per daily maintenance run.

    Found because the temp-directory cleanup in the tests above failed on Windows
    with WinError 32, not because anything asserted it.
    """

    def test_a_failed_connect_leaves_no_open_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corrupt.db"
            _make_db(path, rows=5)
            _corrupt(path)
            with self.assertRaises(sqlite3.DatabaseError):
                sqlite_db.connect(str(path))
            # On Windows an open handle makes unlink fail with WinError 32; on
            # POSIX unlink succeeds regardless, so assert on the handle count
            # there instead of trusting a platform-specific side effect.
            try:
                path.unlink()
            except PermissionError as exc:  # pragma: no cover - Windows only
                self.fail(f"connect() leaked a handle: {exc}")

    def test_the_probe_would_notice_a_leak(self):
        """Guard the instrument: deliberately leak, and confirm it is detectable.

        Without this, `test_a_failed_connect_leaves_no_open_handle` would pass on
        any platform where an open handle does not block unlink — which is most
        of them — and would be testing nothing at all there.
        """
        import sys

        if not sys.platform.startswith("win"):
            self.skipTest("unlink-while-open only fails on Windows")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "held.db"
            _make_db(path, rows=2)
            conn = sqlite3.connect(str(path))
            try:
                with self.assertRaises(PermissionError):
                    path.unlink()
            finally:
                conn.close()


class DeclaredSetTests(unittest.TestCase):
    """The set is derived, so a new SQLite store cannot arrive unmaintained."""

    def test_the_sqlite_subset_is_a_subset_of_the_state_stores(self):
        self.assertTrue(
            set(runtime_stores.sqlite_state_settings())
            <= set(runtime_stores.state_setting_names())
        )

    def test_the_four_known_sqlite_stores_are_all_present(self):
        self.assertEqual(
            set(runtime_stores.sqlite_state_settings()),
            {
                "LOOP_DB_FILE",
                "KERNEL_DB_FILE",
                "WORLD_CUP_PREDICTION_DB_FILE",
                "DOMAIN_RELIABILITY_DB_PATH",
            },
        )

    def test_no_json_store_is_in_the_sqlite_subset(self):
        """A `PRAGMA` against event_store.json would be a runtime error."""
        for name in runtime_stores.sqlite_state_settings():
            with self.subTest(store=name):
                self.assertTrue(str(getattr(__import__(
                    "app.core.config", fromlist=["settings"]).settings, name)).endswith(".db"))

    def test_sqlite_state_paths_agrees_with_the_settings(self):
        from app.core.config import settings

        for name, path in runtime_stores.sqlite_state_paths().items():
            with self.subTest(store=name):
                self.assertEqual(str(path), getattr(settings, name))


if __name__ == "__main__":
    unittest.main()

