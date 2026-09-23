import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory import loop_run_store as runs
from app.utils import sqlite_db


class LoopRunStoreTests(unittest.TestCase):
    def test_start_finish_and_last_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                run_id = runs.start_run("event_discover")
                running = runs.get_run(run_id)
                self.assertEqual(running["status"], "running")
                self.assertEqual(running["job_name"], "event_discover")

                finished = runs.finish_run(
                    run_id,
                    "success",
                    result={"count": 3},
                )

                self.assertEqual(finished["status"], "success")
                self.assertEqual(finished["result"]["count"], 3)
                self.assertIsNotNone(finished["finished_at"])
                self.assertGreaterEqual(finished["duration_ms"], 0)
                self.assertEqual(runs.last_run("event_discover")["id"], run_id)
                self.assertEqual(sqlite_db.schema_versions()["loop_runs"], 1)

    def test_failed_run_records_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                run_id = runs.start_run("event_auto_resolve")
                finished = runs.finish_run(run_id, "failed", error="boom")

                self.assertEqual(finished["status"], "failed")
                self.assertEqual(finished["error"], "boom")
                self.assertEqual(finished["result"], {})

    def test_recent_runs_can_filter_by_job_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                first = runs.start_run("world_cup_post_match_backfill")
                runs.finish_run(first, "success", result={"candidate_count": 2})
                other = runs.start_run("event_auto_resolve")
                runs.finish_run(other, "success", result={"count": 1})
                second = runs.start_run("world_cup_post_match_backfill")
                runs.finish_run(second, "failed", error="source unavailable")

                filtered = runs.recent_runs(limit=5, job_name="world_cup_post_match_backfill")

                self.assertEqual([run["id"] for run in filtered], [second, first])
                self.assertTrue(all(run["job_name"] == "world_cup_post_match_backfill" for run in filtered))


class LatestRunPerJobTests(unittest.TestCase):
    """The watched job set, derived from the ledger instead of typed out.

    Four callers each named the same three jobs, so the other twelve names in
    the live ledger were unwatched -- including the one that had actually
    failed. See ``latest_run_per_job``'s docstring.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        patcher = patch.object(
            sqlite_db,
            "loop_db_path",
            return_value=str(Path(self.tmpdir.name) / "v2_loop.db"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, job_name, status, **kw):
        run_id = runs.start_run(job_name)
        runs.finish_run(run_id, status, **kw)
        return run_id

    def test_every_job_name_in_the_ledger_is_reported(self):
        self._run("event_discover", "success")
        self._run("world_cup_api_football_validate", "failed", error="0 fixtures")

        latest = runs.latest_run_per_job()

        self.assertEqual(
            {run["job_name"] for run in latest},
            {"event_discover", "world_cup_api_football_validate"},
        )
        by_name = {run["job_name"]: run for run in latest}
        self.assertEqual(by_name["world_cup_api_football_validate"]["status"], "failed")
        self.assertEqual(by_name["world_cup_api_football_validate"]["error"], "0 fixtures")

    def test_one_row_per_job_and_it_is_the_newest(self):
        self._run("event_discover", "failed", error="old failure")
        newest = self._run("event_discover", "success")
        chatty = [self._run("world_cup_scoring_reconcile", "success") for _ in range(25)]

        latest = runs.latest_run_per_job()

        self.assertEqual(len(latest), 2, f"expected one row per job, got {latest}")
        by_name = {run["job_name"]: run for run in latest}
        self.assertEqual(by_name["event_discover"]["id"], newest)
        self.assertEqual(by_name["world_cup_scoring_reconcile"]["id"], chatty[-1])

    def test_ordered_newest_first(self):
        self._run("loop_db_maintenance", "success")
        self._run("event_auto_resolve", "success")
        self._run("backup_stores", "success")

        names = [run["job_name"] for run in runs.latest_run_per_job()]

        self.assertEqual(names, ["backup_stores", "event_auto_resolve", "loop_db_maintenance"])

    def test_a_readable_empty_ledger_reports_no_jobs(self):
        """Distinct from an unreadable one: the store raises for that, and the
        callers must not read "nothing failed" out of a failed read."""
        self.assertEqual(runs.latest_run_per_job(), [])

    def test_a_shared_started_at_still_yields_one_deterministic_row(self):
        """`started_at` is microsecond-resolution `utc_now()` and jobs run with
        `max_instances=1`, so this does not occur in practice (0 collisions in
        1706 live rows). Asserted so the row is chosen by the query's own
        tie-break rather than by storage order."""
        stamp = "2026-09-05T13:23:56.000000+00:00"
        runs.recent_runs(limit=1)  # creates the schema through the store
        with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
            for run_id, status in (("aaa-tie", "success"), ("zzz-tie", "failed")):
                conn.execute(
                    "INSERT INTO loop_runs (id, job_name, status, started_at) "
                    "VALUES (?, 'translate_titles', ?, ?)",
                    (run_id, status, stamp),
                )

        latest = runs.latest_run_per_job()

        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["id"], "zzz-tie")


class RecentRunsByJobTests(unittest.TestCase):
    """One chatty job used to empty the recent-runs window.

    ``world_cup_scoring_reconcile`` holds 1193 of the live ledger's 1706 rows
    and its newest 20 postdate every other job, so ``recent_runs(limit=20)``
    returned one distinct job name out of 15.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        patcher = patch.object(
            sqlite_db,
            "loop_db_path",
            return_value=str(Path(self.tmpdir.name) / "v2_loop.db"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, job_name, status="success"):
        run_id = runs.start_run(job_name)
        runs.finish_run(run_id, status)
        return run_id

    def test_a_chatty_job_cannot_fill_the_window(self):
        for name in ("event_discover", "event_auto_resolve", "loop_db_maintenance"):
            self._run(name)
        for _ in range(25):
            self._run("world_cup_scoring_reconcile")

        window = runs.recent_runs_by_job(limit=20, per_job=2)

        # 3 jobs with one run each + the chatty job's cap of 2.
        self.assertEqual(len(window), 5)
        self.assertEqual(
            {run["job_name"] for run in window},
            {
                "event_discover",
                "event_auto_resolve",
                "loop_db_maintenance",
                "world_cup_scoring_reconcile",
            },
        )
        # The chatty job keeps its cap, not its 25 rows.
        chatty = [r for r in window if r["job_name"] == "world_cup_scoring_reconcile"]
        self.assertEqual(len(chatty), 2)

    def test_the_window_is_newest_first_and_capped_at_limit(self):
        for _ in range(6):
            self._run("event_discover")
            self._run("translate_titles")

        window = runs.recent_runs_by_job(limit=3, per_job=6)

        self.assertEqual(len(window), 3)
        stamps = [run["started_at"] for run in window]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_the_per_job_rows_are_that_job_newest_first(self):
        older = self._run("event_discover")
        newer = self._run("event_discover")
        self._run("translate_titles")

        window = runs.recent_runs_by_job(limit=20, per_job=1)

        ids = {run["job_name"]: run["id"] for run in window}
        self.assertEqual(ids["event_discover"], newer)
        self.assertNotIn(older, [run["id"] for run in window])

    def test_a_readable_empty_ledger_reports_an_empty_window(self):
        self.assertEqual(runs.recent_runs_by_job(), [])


class _Ledger(unittest.TestCase):
    """A temp DB patched in for the whole test, plus a raw-row inserter.

    ``_insert`` writes ``started_at`` / ``finished_at`` directly because the
    functions under test are *about* old rows, and manufacturing them through
    ``start_run``/``finish_run`` would stamp them "now".
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        patcher = patch.object(
            sqlite_db,
            "loop_db_path",
            return_value=str(Path(self.tmpdir.name) / "v2_loop.db"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        runs.recent_runs(limit=1)  # create the schema through the store

    def _insert(
        self,
        run_id: str,
        job_name: str,
        status: str,
        started_at: str,
        finished_at: str | None = None,
    ) -> None:
        with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
            conn.execute(
                "INSERT INTO loop_runs (id, job_name, status, started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, job_name, status, started_at, finished_at),
            )

    def _row(self, run_id: str) -> dict:
        got = runs.get_run(run_id)
        self.assertIsNotNone(got, f"row {run_id} vanished; the test lost its fixture")
        return got  # type: ignore[return-value]


class FailStaleRunningRowsTests(_Ledger):
    """123 rows in the live ledger have been `running` since 2026-07-23 or
    earlier. Nothing re-attaches a job to a stored row, so their only exit was
    the dead process's `_finish_run`. Threshold-based rather than
    startup-based, because the API and the scheduler are two processes sharing
    this ledger."""

    def test_a_stale_running_row_is_failed_and_counted(self):
        self._insert("stale", "event_discover", "running", "2026-07-23T00:00:00+00:00")
        changed = runs.fail_stale_running_rows("2026-08-01T00:00:00+00:00")
        self.assertEqual(changed, 1)
        row = self._row("stale")
        self.assertEqual(row["status"], "failed")
        self.assertIsNotNone(row["finished_at"])
        self.assertIn("abandoned", row["error"])

    def test_a_fresh_running_row_is_left_alone(self):
        """The scheduler can be mid-run right now; only the stale ones fail."""
        self._insert("fresh", "event_discover", "running", "2026-08-31T00:00:00+00:00")
        changed = runs.fail_stale_running_rows("2026-08-01T00:00:00+00:00")
        self.assertEqual(changed, 0)
        self.assertEqual(self._row("fresh")["status"], "running")

    def test_an_existing_error_is_not_overwritten(self):
        self._insert(
            "half-failed", "translate_titles", "running",
            "2026-07-01T00:00:00+00:00",
        )
        with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
            conn.execute(
                "UPDATE loop_runs SET error='mid-write crash' WHERE id='half-failed'"
            )
        runs.fail_stale_running_rows("2026-08-01T00:00:00+00:00")
        self.assertEqual(self._row("half-failed")["error"], "mid-write crash")

    def test_a_terminal_row_is_never_touched(self):
        self._insert(
            "old-success", "event_discover", "success",
            "2026-07-01T00:00:00+00:00", "2026-07-01T00:01:00+00:00",
        )
        changed = runs.fail_stale_running_rows("2026-08-01T00:00:00+00:00")
        self.assertEqual(changed, 0)
        self.assertEqual(self._row("old-success")["status"], "success")

    def test_a_readable_empty_ledger_changes_nothing(self):
        self.assertEqual(runs.fail_stale_running_rows("2026-08-01T00:00:00+00:00"), 0)


class DeleteTerminalRunsBeforeTests(_Ledger):
    """The live table is 1706 rows / 16.73 MB with no retention of any kind.
    The newest row per job name is exempt whatever its age, because
    latest_run_per_job is /api/health's data source."""

    OLD = "2026-06-01T00:00:00+00:00"
    NEW = "2026-09-01T00:00:00+00:00"
    CUTOFF = "2026-08-01T00:00:00+00:00"

    def test_old_terminal_rows_are_deleted_and_counted(self):
        for i, job in enumerate(("event_discover", "translate_titles")):
            self._insert(f"old-{i}", job, "success", self.OLD, self.OLD)
            # A fresh row per job, so the deletion is about age, not the exemption.
            self._insert(f"new-{i}", job, "success", self.NEW, self.NEW)
        deleted = runs.delete_terminal_runs_before(self.CUTOFF)
        self.assertEqual(deleted, 2)
        self.assertIsNone(runs.get_run("old-0"))
        self.assertIsNotNone(runs.get_run("new-0"))

    def test_a_running_row_is_never_deleted(self):
        """An abandoned-looking run is a fact to surface via
        fail_stale_running_rows, not one to quietly remove.

        The row carries ``finished_at`` on purpose: a NULL there stops the
        deletion by accident, and this test exists to pin the *status* guard --
        the newer sibling row keeps the exemption from doing the protecting."""
        self._insert(
            "newer", "event_discover", "success", self.NEW, self.NEW,
        )
        self._insert("ghost", "event_discover", "running", self.OLD, self.OLD)
        self.assertEqual(runs.delete_terminal_runs_before(self.CUTOFF), 0)
        self.assertIsNotNone(runs.get_run("ghost"))

    def test_the_newest_row_of_a_job_survives_any_age(self):
        """Without the exemption, pruning a job that has been quiet for the
        retention window deletes /api/health's only record of it."""
        self._insert("last-breath", "event_discover", "success", self.OLD, self.OLD)
        runs.delete_terminal_runs_before(self.CUTOFF)
        self.assertIsNotNone(runs.get_run("last-breath"))

    def test_a_null_finished_at_row_is_kept(self):
        """A terminal row with no finished_at is a mid-flight crash, not
        retention fodder; leave it for the reconcile path or an operator.

        The newer sibling keeps this row out of newest-row territory, so the
        NULL -- not the exemption -- is what must save it."""
        self._insert("newer", "event_discover", "success", self.NEW, self.NEW)
        self._insert("no-finish", "event_discover", "success", self.OLD)
        self.assertEqual(runs.delete_terminal_runs_before(self.CUTOFF), 0)
        self.assertIsNotNone(runs.get_run("no-finish"))

    def test_a_readable_empty_ledger_deletes_nothing(self):
        self.assertEqual(runs.delete_terminal_runs_before(self.CUTOFF), 0)


if __name__ == "__main__":
    unittest.main()
