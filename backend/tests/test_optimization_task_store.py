"""Tests for the durable optimization task store and the manager that backs it.

These isolate the loop DB to a temp file via ``sqlite_db.loop_db_path`` so the
real ``v2_loop.db`` is never touched. The persistence contract being asserted:
- create_task persists; restart-simulation (fresh manager) still resolves.
- update_progress / mark_running / mark_completed / mark_failed all sync to disk.
- cleanup_old_tasks prunes both memory and the store, but keeps NULL-completed
  rows (crash mid-flight) for diagnosis.
"""

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.memory import optimization_task_store as store
from app.services.optimization_task_manager import (
    OptimizationTaskManager,
    TaskStatus,
)
from app.utils import sqlite_db


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class OptimizationTaskStoreTests(unittest.TestCase):
    def test_upsert_and_get_round_trips_full_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                task = {
                    "task_id": "t1",
                    "engine_name": "elo_odds",
                    "status": "running",
                    "progress": 3,
                    "total": 10,
                    "current_match": "A vs B",
                    "result": None,
                    "error": None,
                    "created_at": "2026-06-27T00:00:00+00:00",
                    "started_at": "2026-06-27T00:00:01+00:00",
                    "completed_at": None,
                    "logs": [{"timestamp": "2026-06-27T00:00:02+00:00", "message": "hi"}],
                }
                store.upsert_task(task)
                got = store.get_task("t1")
                self.assertIsNotNone(got)
                self.assertEqual(got["task_id"], "t1")
                self.assertEqual(got["status"], "running")
                self.assertEqual(got["progress"], 3)
                self.assertEqual(got["total"], 10)
                self.assertEqual(got["current_match"], "A vs B")
                self.assertEqual(got["logs"], [{"timestamp": "2026-06-27T00:00:02+00:00", "message": "hi"}])
                self.assertEqual(got["result"], None)
                self.assertEqual(got["error"], None)

    def test_upsert_replaces_existing_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.upsert_task({"task_id": "t2", "engine_name": "hybrid", "status": "pending", "progress": 0, "total": 0, "logs": []})
                store.upsert_task({"task_id": "t2", "engine_name": "hybrid", "status": "completed", "progress": 5, "total": 5, "logs": [], "result": {"k": "v"}, "completed_at": "2026-06-27T01:00:00+00:00"})
                got = store.get_task("t2")
                self.assertEqual(got["status"], "completed")
                self.assertEqual(got["progress"], 5)
                self.assertEqual(got["result"], {"k": "v"})

    def test_get_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                self.assertIsNone(store.get_task("nope"))

    def test_list_recent_tasks_orders_by_created_desc(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.upsert_task({"task_id": "a", "engine_name": "e", "status": "completed", "progress": 0, "total": 0, "logs": [], "created_at": "2026-06-27T00:00:00+00:00"})
                store.upsert_task({"task_id": "b", "engine_name": "e", "status": "failed", "progress": 0, "total": 0, "logs": [], "created_at": "2026-06-27T01:00:00+00:00"})
                store.upsert_task({"task_id": "c", "engine_name": "e", "status": "running", "progress": 0, "total": 0, "logs": [], "created_at": "2026-06-27T02:00:00+00:00"})
                recent = store.list_recent_tasks(limit=10)
                self.assertEqual([t["task_id"] for t in recent], ["c", "b", "a"])
                only_failed = store.list_recent_tasks(limit=10, status="failed")
                self.assertEqual([t["task_id"] for t in only_failed], ["b"])

    def test_delete_older_than_keeps_null_completed_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                # Old + completed -> pruned
                store.upsert_task({"task_id": "old", "engine_name": "e", "status": "completed", "progress": 0, "total": 0, "logs": [], "completed_at": "2026-06-01T00:00:00+00:00"})
                # Recent + completed -> kept
                store.upsert_task({"task_id": "recent", "engine_name": "e", "status": "completed", "progress": 0, "total": 0, "logs": [], "completed_at": "2026-06-27T00:00:00+00:00"})
                # Terminal but NULL completed_at (crash mid-flight) -> KEPT
                store.upsert_task({"task_id": "crashed", "engine_name": "e", "status": "failed", "progress": 0, "total": 0, "logs": [], "completed_at": None})
                deleted = store.delete_older_than("2026-06-26T00:00:00+00:00", ["completed", "failed"])
                self.assertEqual(deleted, 1)
                self.assertIsNone(store.get_task("old"))
                self.assertIsNotNone(store.get_task("recent"))
                self.assertIsNotNone(store.get_task("crashed"))

    def test_delete_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.upsert_task({"task_id": "x", "engine_name": "e", "status": "pending", "progress": 0, "total": 0, "logs": []})
                store.delete_task("x")
                self.assertIsNone(store.get_task("x"))

    def test_schema_version_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.upsert_task({"task_id": "v", "engine_name": "e", "status": "pending", "progress": 0, "total": 0, "logs": []})
                self.assertEqual(sqlite_db.schema_versions()["optimization_tasks"], 1)


class OptimizationTaskManagerPersistenceTests(unittest.TestCase):
    def test_create_persists_and_get_resolves_after_memory_loss(self):
        """Simulate a restart: a fresh manager with an empty memory cache
        should still resolve a task_id that the prior manager persisted."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                m1 = OptimizationTaskManager()
                task = _run(m1.create_task("elo_odds"))
                _run(m1.mark_running(task.task_id))
                _run(m1.update_progress(task.task_id, 2, 5, current_match="A vs B", log_message="halfway"))

                # Fresh manager simulates a process restart — empty memory.
                m2 = OptimizationTaskManager()
                restored = _run(m2.get_task(task.task_id))
                self.assertIsNotNone(restored)
                self.assertEqual(restored.status, TaskStatus.RUNNING)
                self.assertEqual(restored.progress, 2)
                self.assertEqual(restored.total, 5)
                self.assertEqual(restored.current_match, "A vs B")
                self.assertEqual(restored.engine_name, "elo_odds")
                self.assertTrue(any("halfway" in (log.get("message") or "") for log in restored.logs))

    def test_mark_completed_persists_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                m1 = OptimizationTaskManager()
                task = _run(m1.create_task("hybrid"))
                _run(m1.mark_completed(task.task_id, {"optimizations_generated": 7}))

                m2 = OptimizationTaskManager()
                restored = _run(m2.get_task(task.task_id))
                self.assertEqual(restored.status, TaskStatus.COMPLETED)
                self.assertEqual(restored.result, {"optimizations_generated": 7})
                self.assertIsNotNone(restored.completed_at)

    def test_mark_failed_persists_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                m1 = OptimizationTaskManager()
                task = _run(m1.create_task("integrated"))
                _run(m1.mark_failed(task.task_id, "boom"))

                m2 = OptimizationTaskManager()
                restored = _run(m2.get_task(task.task_id))
                self.assertEqual(restored.status, TaskStatus.FAILED)
                self.assertEqual(restored.error, "boom")

    def test_cleanup_prunes_old_terminal_tasks_in_memory_and_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                m = OptimizationTaskManager()
                old = _run(m.create_task("elo_odds"))
                _run(m.mark_completed(old.task_id, {"x": 1}))
                # Backdate the completed_at past the cleanup window.
                _run(m._lock.acquire())
                try:
                    t = m._tasks[old.task_id]
                    t.completed_at = datetime.now(timezone.utc) - timedelta(hours=48)
                    m._persist(t)
                finally:
                    m._lock.release()

                _run(m.cleanup_old_tasks(max_age_hours=24))

                self.assertNotIn(old.task_id, m._tasks)
                self.assertIsNone(store.get_task(old.task_id))

    def test_cleanup_keeps_recent_terminal_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                m = OptimizationTaskManager()
                recent = _run(m.create_task("hybrid"))
                _run(m.mark_completed(recent.task_id, {"x": 1}))
                _run(m.cleanup_old_tasks(max_age_hours=24))
                self.assertIn(recent.task_id, m._tasks)
                self.assertIsNotNone(store.get_task(recent.task_id))

    def test_cleanup_keeps_running_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                m = OptimizationTaskManager()
                running = _run(m.create_task("integrated"))
                _run(m.mark_running(running.task_id))
                _run(m.cleanup_old_tasks(max_age_hours=0))
                self.assertIn(running.task_id, m._tasks)
                self.assertIsNotNone(store.get_task(running.task_id))


class InterruptedTaskReconciliationTests(unittest.TestCase):
    """A restart leaves 'pending'/'running' rows with no asyncio.Task behind
    them. Nothing re-attaches one, so /auto-tune/status reported them running
    forever, and cleanup_old_tasks only prunes terminal statuses — so the row
    was never removed either. Startup reconciliation makes them terminal.
    """

    def test_non_terminal_rows_become_failed_and_prunable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.upsert_task({"task_id": "run", "engine_name": "e", "status": "running", "progress": 2, "total": 9, "logs": [], "created_at": "2026-06-01T00:00:00+00:00"})
                store.upsert_task({"task_id": "pend", "engine_name": "e", "status": "pending", "progress": 0, "total": 0, "logs": [], "created_at": "2026-06-01T00:00:00+00:00"})

                changed = store.fail_interrupted_tasks("interrupted")

                self.assertEqual(changed, 2)
                for task_id in ("run", "pend"):
                    got = store.get_task(task_id)
                    self.assertEqual(got["status"], "failed")
                    self.assertEqual(got["error"], "interrupted")
                    # completed_at is set, so the daily cleanup can now prune it.
                    self.assertIsNotNone(got["completed_at"])
                pruned = store.delete_older_than(
                    "2999-01-01T00:00:00+00:00", ["completed", "failed"]
                )
                self.assertEqual(pruned, 2)

    def test_terminal_rows_are_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.upsert_task({"task_id": "ok", "engine_name": "e", "status": "completed", "progress": 5, "total": 5, "logs": [], "result": {"k": "v"}, "completed_at": "2026-06-01T00:00:00+00:00"})
                store.upsert_task({"task_id": "bad", "engine_name": "e", "status": "failed", "progress": 1, "total": 5, "logs": [], "error": "real failure", "completed_at": "2026-06-01T00:00:00+00:00"})

                self.assertEqual(store.fail_interrupted_tasks("interrupted"), 0)

                self.assertEqual(store.get_task("ok")["status"], "completed")
                self.assertEqual(store.get_task("ok")["result"], {"k": "v"})
                # An existing error message is not overwritten.
                self.assertEqual(store.get_task("bad")["error"], "real failure")

    def test_existing_completed_at_is_preserved(self):
        """A 'running' row that somehow carries completed_at keeps it, so the
        reconciliation cannot move a timestamp forward and delay pruning."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.upsert_task({"task_id": "odd", "engine_name": "e", "status": "running", "progress": 1, "total": 2, "logs": [], "completed_at": "2026-06-01T00:00:00+00:00"})
                store.fail_interrupted_tasks("interrupted")
                self.assertEqual(
                    store.get_task("odd")["completed_at"], "2026-06-01T00:00:00+00:00"
                )

    def test_manager_reconcile_drops_stale_memory_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                m = OptimizationTaskManager()
                running = _run(m.create_task("integrated"))
                _run(m.mark_running(running.task_id))
                done = _run(m.create_task("hybrid"))
                _run(m.mark_completed(done.task_id, {"x": 1}))

                reconciled = _run(m.reconcile_interrupted_tasks())

                self.assertEqual(reconciled, 1)
                # The stale in-memory copy is gone, so a poll re-hydrates the
                # reconciled 'failed' row rather than reading 'running'.
                self.assertNotIn(running.task_id, m._tasks)
                rehydrated = _run(m.get_task(running.task_id))
                self.assertEqual(rehydrated.status, TaskStatus.FAILED)
                # The completed task is untouched in memory and on disk.
                self.assertIn(done.task_id, m._tasks)
                self.assertEqual(store.get_task(done.task_id)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
