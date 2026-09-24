"""Every store's schema setup must run once per path, not once per call.

`GET /metrics` measured 209.8 ms on the live stores, and 146.5 ms of that was
`_refresh_scheduler_gauges` -> `loop_run_store.latest_run_per_job()`. The query
is 3.02 ms. The other 141 ms is `_ensure_schema(path)`, which opens a *write*
transaction on every call:

    _ensure_schema(path)                     median  141.32 ms
      writing() open + empty commit          median    1.40 ms
      + CREATE TABLE/INDEX IF NOT EXISTS x3  median    1.52 ms
      + record_schema_version alone          median  133.54 ms

The mechanism is not the DDL and not the INSERT. `record_schema_version` does an
unconditional `INSERT ... ON CONFLICT DO UPDATE SET updated_at=...`, so every
call dirties a page; `writing()` then commits and closes, and closing the last
handle to a WAL database checkpoints it -- copy the dirty pages into the 17.5 MB
main file, fsync it at `synchronous=FULL`, delete the WAL. Measured with a second
handle held open so close() is not the last: 47.98 ms instead of 141.57 ms. An
empty commit stays at 1.40 ms because nothing is dirty to checkpoint.

So four read functions in `loop_run_store` and every read in
`optimization_task_store` each take the process-wide `_WRITE_LOCK` and force a
durable checkpoint. Measured with `PRAGMA data_version` on a held connection --
it bumps when another connection commits -- every one of them wrote:

    last_run('event_discover')   data_version 2 -> 3   WROTE
    recent_runs(limit=5)                      3 -> 4   WROTE
    latest_run_per_job()                      4 -> 5   WROTE
    recent_runs_by_job()                      5 -> 6   WROTE
    optimization_task_store.get_task('nope')  2 -> 3   WROTE

That is `/metrics` (unauthenticated, scraped every 15s = 5,760/day),
`/api/health`, the quality-metrics scheduler block and the frontend's
auto-tune status polling, each contending with the scheduler's own
`_start_run`/`_finish_run` on the same lock.

Eight of the ten stores in `app/memory/` already memoize this behind a
double-checked lock, and `test_llm_daily_cost_cap.SchemaMemoizationTests` records
the ninth being fixed for exactly this reason. The list of stores that got it was
hand-maintained, so `loop_run_store` and `optimization_task_store` were simply
missed -- which is why the partition test below scans the filesystem instead of
naming stores.

The cost is invisible to the suite because it scales with database size: every
test builds a few-KB temp database, where the checkpoint is free.
`record_schema_version`'s `updated_at` column has no reader anywhere in `app/` --
`schema_versions()` selects `component, version` -- so not rewriting it every
call changes no observable value.
"""
from __future__ import annotations

import importlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory import loop_run_store as runs
from app.memory import optimization_task_store as tasks
from app.utils import sqlite_db

_MEMORY_DIR = Path(sqlite_db.__file__).resolve().parents[1] / "memory"


class _ConnectCounter:
    """Count connections opened, patched at the chokepoint.

    `sqlite_db.connect` and not `sqlite_db.writing`: every store does
    `from app.utils.sqlite_db import reading, writing`, so the name a test
    patches on the `sqlite_db` module is not the name the store calls. Both
    context managers go through `connect`, which is inside `sqlite_db` and
    therefore looked up there at call time.
    """

    def __init__(self) -> None:
        self.n = 0
        self._real = sqlite_db.connect

    def __call__(self, path: str) -> sqlite3.Connection:
        self.n += 1
        return self._real(path)


class _LockCounter:
    """A lock that counts acquisitions and otherwise behaves like one."""

    def __init__(self) -> None:
        self.n = 0
        self._real = threading.Lock()

    def __enter__(self) -> None:
        self.n += 1
        self._real.acquire()

    def __exit__(self, *exc: object) -> None:
        self._real.release()


class _DataVersion:
    """Watch commits from a held connection.

    `PRAGMA data_version` on one connection changes when *another* connection
    commits a change to the database, and does not change for a transaction that
    wrote nothing. No sleeps, no timing.
    """

    def __init__(self, path: str) -> None:
        self._conn = sqlite_db.connect(path)
        self._mark = self.read()

    def read(self) -> int:
        return int(self._conn.execute("PRAGMA data_version").fetchone()[0])

    def wrote(self) -> bool:
        now = self.read()
        changed = now != self._mark
        self._mark = now
        return changed

    def close(self) -> None:
        self._conn.close()


class LoopRunStoreReadsTests(unittest.TestCase):
    """The run ledger's readers must not write."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "v2_loop.db")
        self._patch = patch.object(sqlite_db, "loop_db_path", return_value=self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        runs._INITIALIZED.discard(self.path)
        run_id = runs.start_run("event_discover")
        runs.finish_run(run_id, "success", result={"count": 1})

    def test_no_reader_writes_to_the_ledger(self):
        watcher = _DataVersion(self.path)
        self.addCleanup(watcher.close)
        watcher.wrote()  # clear anything the seeding left pending
        for label, call in (
            ("last_run", lambda: runs.last_run("event_discover")),
            ("recent_runs", lambda: runs.recent_runs(limit=5)),
            ("latest_run_per_job", runs.latest_run_per_job),
            ("recent_runs_by_job", runs.recent_runs_by_job),
        ):
            with self.subTest(reader=label):
                call()
                self.assertFalse(
                    watcher.wrote(),
                    f"{label}() committed a write; a read path must not take the "
                    "write lock or force a WAL checkpoint",
                )

    def test_the_watcher_can_see_a_write(self):
        """Guard the instrument: without this the test above proves nothing."""
        watcher = _DataVersion(self.path)
        self.addCleanup(watcher.close)
        watcher.wrote()
        runs.start_run("event_auto_resolve")
        self.assertTrue(
            watcher.wrote(),
            "PRAGMA data_version did not move for a real write, so it cannot "
            "detect one on a read path either",
        )

    def test_a_read_opens_one_connection(self):
        counter = _ConnectCounter()
        with patch.object(sqlite_db, "connect", counter):
            runs.latest_run_per_job()
        self.assertEqual(
            counter.n,
            1,
            f"latest_run_per_job() opened {counter.n} connections; the schema "
            "write transaction is still running on every call",
        )

    def test_the_first_call_still_builds_the_schema(self):
        """Memoizing must not skip setup on a path this process has not seen."""
        fresh = str(Path(self._tmp.name) / "fresh.db")
        with patch.object(sqlite_db, "loop_db_path", return_value=fresh):
            run_id = runs.start_run("world_cup_scoring_reconcile")
            self.assertEqual(runs.get_run(run_id)["status"], "running")
            self.assertEqual(sqlite_db.schema_versions(fresh)["loop_runs"], 1)


class OptimizationTaskStoreReadsTests(unittest.TestCase):
    """The auto-tune status the frontend polls must not write either."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "v2_loop.db")
        self._patch = patch.object(sqlite_db, "loop_db_path", return_value=self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        tasks._INITIALIZED.discard(self.path)
        tasks._ensure_schema(self.path)

    def test_no_reader_writes(self):
        watcher = _DataVersion(self.path)
        self.addCleanup(watcher.close)
        watcher.wrote()
        for label, call in (
            ("get_task", lambda: tasks.get_task("no-such-task")),
            ("list_recent_tasks", lambda: tasks.list_recent_tasks(limit=50)),
        ):
            with self.subTest(reader=label):
                call()
                self.assertFalse(
                    watcher.wrote(), f"{label}() committed a write on a read path"
                )

    def test_a_task_still_round_trips(self):
        tasks.upsert_task({
            "task_id": "t1",
            "engine_name": "elo",
            "status": "completed",
            "progress": 3,
            "total": 3,
            "current_match": None,
            "result_json": None,
            "error": None,
            "created_at": "2026-09-06T00:00:00+00:00",
            "started_at": "2026-09-06T00:00:01+00:00",
            "completed_at": "2026-09-06T00:00:02+00:00",
            "logs_json": "[]",
            "updated_at": "2026-09-06T00:00:02+00:00",
        })
        self.assertEqual(tasks.get_task("t1")["status"], "completed")


class SchemaSetupPartitionTests(unittest.TestCase):
    """*Every* store in app/memory/ memoizes, scanned rather than listed.

    The two stores this module fixes were missed because the set of stores that
    memoize was only ever a set of stores that happened to. Discovering them from
    the filesystem means a new store cannot join without being covered.
    """

    def _stores(self) -> list[str]:
        found = []
        for py in sorted(_MEMORY_DIR.glob("*.py")):
            if py.name == "__init__.py":
                continue
            if "def _ensure_schema(path: str)" in py.read_text(encoding="utf-8"):
                found.append(py.stem)
        return found

    def test_the_scan_finds_the_stores(self):
        stores = self._stores()
        self.assertGreaterEqual(len(stores), 10, stores)
        for expected in ("loop_run_store", "optimization_task_store",
                         "prediction_store", "llm_daily_spend_store"):
            self.assertIn(expected, stores)

    def test_every_store_builds_its_schema_once_per_path(self):
        for name in self._stores():
            with self.subTest(store=name):
                module = importlib.import_module(f"app.memory.{name}")
                with tempfile.TemporaryDirectory() as tmp:
                    path = str(Path(tmp) / f"{name}.db")
                    module._ensure_schema(path)
                    counter = _ConnectCounter()
                    with patch.object(sqlite_db, "connect", counter):
                        module._ensure_schema(path)
                    self.assertEqual(
                        counter.n,
                        0,
                        f"app/memory/{name}.py opened {counter.n} connections on a "
                        "repeat _ensure_schema; memoize it behind the same "
                        "double-checked lock the other stores use",
                    )

    def test_the_repeat_call_does_not_touch_the_lock(self):
        """The fast path exists to keep readers off `_INIT_GUARD`.

        Checking the set only *inside* the lock still opens no connection, so the
        connection counter above cannot see the difference -- but it serializes
        every reader in the process behind one mutex for the life of the run.
        Reading a set is atomic under the GIL, which is why the check is doubled.
        """
        for name in self._stores():
            with self.subTest(store=name):
                module = importlib.import_module(f"app.memory.{name}")
                with tempfile.TemporaryDirectory() as tmp:
                    path = str(Path(tmp) / f"{name}.db")
                    module._ensure_schema(path)
                    counter = _LockCounter()
                    with patch.object(module, "_INIT_GUARD", counter):
                        module._ensure_schema(path)
                    self.assertEqual(
                        counter.n,
                        0,
                        f"app/memory/{name}.py acquired _INIT_GUARD {counter.n} "
                        "times on an already-built path; check the memo before "
                        "taking the lock, not only inside it",
                    )

    def test_a_failed_build_does_not_leave_the_path_memoized(self):
        """Memoize after the build, never before.

        Recording the path first makes a failure permanent for the process: the
        table was never created, every later call takes the fast path out, and
        each read fails with `no such table` until a restart.
        """
        for name in self._stores():
            with self.subTest(store=name):
                module = importlib.import_module(f"app.memory.{name}")
                with tempfile.TemporaryDirectory() as tmp:
                    path = str(Path(tmp) / f"{name}.db")

                    def _boom(_path: str) -> sqlite3.Connection:
                        raise sqlite3.OperationalError("disk I/O error")

                    with patch.object(sqlite_db, "connect", _boom):
                        with self.assertRaises(sqlite3.OperationalError):
                            module._ensure_schema(path)
                    self.assertNotIn(
                        path,
                        module._INITIALIZED,
                        f"app/memory/{name}.py memoized a path whose schema build "
                        "raised; the table does not exist and no later call will "
                        "try again",
                    )
                    # And the recovery actually works once the DB is reachable.
                    module._ensure_schema(path)
                    self.assertIn(path, module._INITIALIZED)

    def test_the_partition_would_notice_a_store_that_does_not_memoize(self):
        """Guard the instrument by bypassing the memo for one store."""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "v2_loop.db")
            runs._ensure_schema(path)
            runs._INITIALIZED.discard(path)
            counter = _ConnectCounter()
            with patch.object(sqlite_db, "connect", counter):
                runs._ensure_schema(path)
            self.assertGreater(
                counter.n,
                0,
                "the connection counter saw nothing for a store whose memo was "
                "cleared, so it cannot detect an unmemoized store either",
            )


if __name__ == "__main__":
    unittest.main()
