# backend/tests/test_optimization_cleanup_job.py
"""The cleanup job must report what it pruned, and must not call a degraded store clean.

``_job_optimization_task_cleanup`` had no test at all. Its docstring claimed a store
failure was "logged and re-raised into the run ledger"; ``cleanup_old_tasks`` caught it,
logged it and returned ``None``, so the job recorded the literal ``{"cleaned": True}``.
The table growing without bound -- the one condition this job exists to prevent -- was
reported as a clean success.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.core.scheduler import _job_optimization_task_cleanup


@pytest.fixture
def captured():
    calls = {"finish": []}

    def fake_finish(run_id, status, *, result=None, error=None, exc=None):
        calls["finish"].append({"status": status, "result": result, "error": error})

    with patch("app.core.scheduler._start_run", return_value="run-cleanup"), \
         patch("app.core.scheduler._finish_run", side_effect=fake_finish):
        yield calls


def _manager(outcome):
    mgr = AsyncMock()
    mgr.cleanup_old_tasks = AsyncMock(return_value=outcome)
    return mgr


@pytest.mark.asyncio
async def test_job_reports_the_counts_it_actually_pruned(captured):
    mgr = _manager({"memory_removed": 3, "store_deleted": 7, "store_error": None})
    with patch(
        "app.services.optimization_task_manager.get_task_manager", return_value=mgr,
    ):
        await _job_optimization_task_cleanup()

    mgr.cleanup_old_tasks.assert_awaited_once_with(max_age_hours=24)
    final = captured["finish"][-1]
    assert final["status"] == "success"
    # The defect reported {"cleaned": True} -- a literal that is identical whether
    # 0 rows or 7000 rows were removed.
    assert final["result"] == {
        "memory_removed": 3, "store_deleted": 7, "store_error": None,
    }


@pytest.mark.asyncio
async def test_a_degraded_store_is_a_failed_run_not_a_clean_one(captured):
    """The condition the job exists to prevent must not read as success."""
    mgr = _manager({
        "memory_removed": 2, "store_deleted": 0,
        "store_error": "database is locked",
    })
    with patch(
        "app.services.optimization_task_manager.get_task_manager", return_value=mgr,
    ):
        await _job_optimization_task_cleanup()

    final = captured["finish"][-1]
    assert final["status"] == "failed"
    assert "database is locked" in final["error"]
    # The in-memory pruning did happen, and saying so is the reason the manager
    # swallows the exception instead of re-raising it.
    assert final["result"]["memory_removed"] == 2
    assert final["result"]["store_error"] == "database is locked"


@pytest.mark.asyncio
async def test_pruning_nothing_is_distinguishable_from_pruning_rows(captured):
    """Zero is a real answer here, and must not look like the busy case."""
    mgr = _manager({"memory_removed": 0, "store_deleted": 0, "store_error": None})
    with patch(
        "app.services.optimization_task_manager.get_task_manager", return_value=mgr,
    ):
        await _job_optimization_task_cleanup()

    final = captured["finish"][-1]
    assert final["status"] == "success"
    assert final["result"]["memory_removed"] == 0
    assert final["result"]["store_deleted"] == 0


class TestCleanupOldTasksReturnsItsCounts:
    """The manager side: the counts must exist before the job can report them."""

    def test_store_failure_is_returned_rather_than_only_logged(self):
        import asyncio
        from app.services.optimization_task_manager import OptimizationTaskManager

        mgr = OptimizationTaskManager()
        with patch(
            "app.memory.optimization_task_store.delete_older_than",
            side_effect=RuntimeError("disk I/O error"),
        ):
            outcome = asyncio.run(mgr.cleanup_old_tasks(max_age_hours=24))

        assert outcome["store_error"] == "disk I/O error"
        assert outcome["store_deleted"] == 0
        # In-memory pruning is independent and still reported.
        assert outcome["memory_removed"] == 0

    def test_a_healthy_prune_reports_the_row_count(self):
        """The rival configuration: without it, always-report-an-error would pass."""
        import asyncio
        from app.services.optimization_task_manager import OptimizationTaskManager

        mgr = OptimizationTaskManager()
        with patch(
            "app.memory.optimization_task_store.delete_older_than",
            return_value=5,
        ):
            outcome = asyncio.run(mgr.cleanup_old_tasks(max_age_hours=24))

        assert outcome["store_error"] is None
        assert outcome["store_deleted"] == 5

    def test_memory_removed_counts_the_entries_actually_dropped(self, tmp_path):
        """``memory_removed`` must be non-zero somewhere, or 0 pins nothing.

        The other tests in this class prune an empty manager, so they assert
        ``memory_removed == 0`` -- which a hardcoded ``0`` satisfies. Injection
        confirmed that: replacing ``len(to_remove)`` with ``0`` was caught by
        nobody until this test existed.
        """
        import asyncio
        from datetime import datetime, timedelta, timezone

        from app.services.optimization_task_manager import OptimizationTaskManager
        from app.utils import sqlite_db

        with patch.object(
            sqlite_db, "loop_db_path", return_value=str(tmp_path / "v2_loop.db"),
        ):
            mgr = OptimizationTaskManager()

            async def _drive():
                stale = await mgr.create_task("elo_odds")
                await mgr.mark_completed(stale.task_id, {"x": 1})
                fresh = await mgr.create_task("hybrid")
                await mgr.mark_completed(fresh.task_id, {"x": 2})
                # Backdate only the first one past the window.
                async with mgr._lock:
                    mgr._tasks[stale.task_id].completed_at = (
                        datetime.now(timezone.utc) - timedelta(hours=48)
                    )
                return await mgr.cleanup_old_tasks(max_age_hours=24), fresh.task_id

            outcome, fresh_id = asyncio.run(_drive())

        assert outcome["memory_removed"] == 1, outcome
        # And the recent one survived, so the count is a real partition rather
        # than "everything" or a constant.
        assert fresh_id in mgr._tasks
