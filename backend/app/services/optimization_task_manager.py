"""Background task manager for AI optimization jobs.

State is mirrored to SQLite via :mod:`app.memory.optimization_task_store` so
that a process restart does not lose in-flight or recently-completed task
state — the frontend polls ``/auto-tune/status/{task_id}`` and would otherwise
get a 404 after a redeploy. Memory remains the fast-path read; the store is
consulted on memory miss (e.g. after a restart) and mutated alongside every
state transition. The daily scheduler job ``optimization_task_cleanup``
(eventually registered in ``app.core.scheduler``) prunes terminal tasks older
than 24h via :meth:`OptimizationTaskManager.cleanup_old_tasks`.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any
from enum import Enum

from app.memory import optimization_task_store

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task status enumeration."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class OptimizationTask:
    """Represents a background optimization task."""

    def __init__(self, engine_name: str, task_id: str | None = None):
        self.task_id = task_id or str(uuid.uuid4())
        self.engine_name = engine_name
        self.status = TaskStatus.PENDING
        self.progress = 0
        self.total = 0
        self.current_match = None
        self.result = None
        self.error = None
        self.created_at = datetime.now(timezone.utc)
        self.started_at = None
        self.completed_at = None
        self.logs: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        """Convert task to dictionary for API response and persistence."""
        return {
            "task_id": self.task_id,
            "engine_name": self.engine_name,
            "status": self.status.value,
            "progress": self.progress,
            "total": self.total,
            "current_match": self.current_match,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "logs": self.logs[-10:],  # Last 10 log entries
        }

    def _full_logs(self) -> list[dict[str, Any]]:
        """Full log list for persistence (not trimmed)."""
        return self.logs

    def _persist_dict(self) -> dict[str, Any]:
        """Dict shape for the store: full logs so we can rebuild later, not
        the API-trimmed tail."""
        d = self.to_dict()
        d["logs"] = self._full_logs()
        return d


class OptimizationTaskManager:
    """Manages background optimization tasks.

    Memory is the fast-path read; SQLite is the durable fallback consulted on a
    memory miss (e.g. after a restart) and written on every transition. The
    store is best-effort — a write failure is logged but never raised, so a
    degraded SQLite file never blocks the optimization pipeline itself.
    """

    def __init__(self):
        self._tasks: dict[str, OptimizationTask] = {}
        self._lock = asyncio.Lock()

    def _persist(self, task: OptimizationTask) -> None:
        try:
            optimization_task_store.upsert_task(task._persist_dict())
        except Exception:
            logger.exception(
                "[OptimizationTaskManager] Failed to persist task %s; "
                "in-memory state is still authoritative.",
                task.task_id,
            )

    async def create_task(self, engine_name: str) -> OptimizationTask:
        """Create a new optimization task."""
        async with self._lock:
            task = OptimizationTask(engine_name)
            self._tasks[task.task_id] = task
            self._persist(task)
            return task

    async def get_task(self, task_id: str) -> OptimizationTask | None:
        """Get task by ID.

        Memory is checked first; on miss the durable store is consulted and
        the task is re-hydrated into memory so subsequent reads stay fast.
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                return task
        # Fall back to SQLite outside the lock so a slow disk read does not
        # block other in-flight callers. Best-effort: any error returns None
        # (matches the prior contract where a miss simply returned None).
        try:
            stored = optimization_task_store.get_task(task_id)
        except Exception:
            logger.exception(
                "[OptimizationTaskManager] Failed to load task %s from store.",
                task_id,
            )
            return None
        if stored is None:
            return None
        rehydrated = self._task_from_stored(stored)
        async with self._lock:
            # Another coroutine may have re-hydrated the same id while we held
            # the lock — prefer the in-memory copy if it exists.
            existing = self._tasks.get(task_id)
            if existing is not None:
                return existing
            self._tasks[task_id] = rehydrated
            return rehydrated

    def _task_from_stored(self, stored: dict[str, Any]) -> OptimizationTask:
        """Rebuild an :class:`OptimizationTask` from a stored dict."""
        task = OptimizationTask(
            engine_name=stored.get("engine_name", ""),
            task_id=stored.get("task_id"),
        )
        try:
            task.status = TaskStatus(stored.get("status", "pending"))
        except ValueError:
            task.status = TaskStatus.PENDING
        task.progress = int(stored.get("progress", 0) or 0)
        task.total = int(stored.get("total", 0) or 0)
        task.current_match = stored.get("current_match")
        task.result = stored.get("result")
        task.error = stored.get("error")
        task.created_at = _parse_dt(stored.get("created_at")) or task.created_at
        task.started_at = _parse_dt(stored.get("started_at"))
        task.completed_at = _parse_dt(stored.get("completed_at"))
        logs = stored.get("logs") or []
        if isinstance(logs, list):
            task.logs = list(logs)
        else:
            task.logs = []
        return task

    async def update_progress(
        self,
        task_id: str,
        progress: int,
        total: int,
        current_match: str | None = None,
        log_message: str | None = None
    ):
        """Update task progress."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.progress = progress
                task.total = total
                if current_match:
                    task.current_match = current_match
                if log_message:
                    task.logs.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "message": log_message
                    })
                self._persist(task)

    async def mark_running(self, task_id: str):
        """Mark task as running."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now(timezone.utc)
                self._persist(task)

    async def mark_completed(self, task_id: str, result: dict[str, Any]):
        """Mark task as completed with result."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(timezone.utc)
                task.result = result
                self._persist(task)

    async def mark_failed(self, task_id: str, error: str):
        """Mark task as failed with error."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now(timezone.utc)
                task.error = error
                self._persist(task)

    async def cleanup_old_tasks(self, max_age_hours: int = 24):
        """Remove completed/failed tasks older than max_age_hours.

        Prunes both the in-memory cache and the durable store. Memory removal
        keeps the dict bounded across long-lived processes; store removal keeps
        the SQLite table from growing without limit. Rows with NULL
        ``completed_at`` are kept (a terminal task with no completion timestamp
        indicates a crash mid-flight and is preserved for diagnosis).
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=max_age_hours)
            cutoff_iso = cutoff.isoformat()
            to_remove = []
            for task_id, task in self._tasks.items():
                if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                    if task.completed_at and task.completed_at < cutoff:
                        to_remove.append(task_id)
            for task_id in to_remove:
                del self._tasks[task_id]
        # Store prune outside the lock; best-effort.
        try:
            deleted = optimization_task_store.delete_older_than(
                cutoff_iso,
                [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value],
            )
            if deleted:
                logger.info(
                    "[OptimizationTaskManager] Pruned %d old task(s) from store.",
                    deleted,
                )
        except Exception:
            logger.exception(
                "[OptimizationTaskManager] Store cleanup failed; in-memory "
                "pruning still completed."
            )


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# Global task manager instance
_task_manager = OptimizationTaskManager()


def get_task_manager() -> OptimizationTaskManager:
    """Get global task manager instance."""
    return _task_manager
