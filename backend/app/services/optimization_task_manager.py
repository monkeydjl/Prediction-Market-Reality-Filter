"""Background task manager for AI optimization jobs."""

import asyncio
import uuid
from datetime import datetime
from typing import Any
from enum import Enum


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
        self.logs = []

    def to_dict(self) -> dict[str, Any]:
        """Convert task to dictionary for API response."""
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
            "logs": self.logs[-10:]  # Last 10 log entries
        }


class OptimizationTaskManager:
    """Manages background optimization tasks."""

    def __init__(self):
        self._tasks: dict[str, OptimizationTask] = {}
        self._lock = asyncio.Lock()

    async def create_task(self, engine_name: str) -> OptimizationTask:
        """Create a new optimization task."""
        async with self._lock:
            task = OptimizationTask(engine_name)
            self._tasks[task.task_id] = task
            return task

    async def get_task(self, task_id: str) -> OptimizationTask | None:
        """Get task by ID."""
        async with self._lock:
            return self._tasks.get(task_id)

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

    async def mark_running(self, task_id: str):
        """Mark task as running."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now(timezone.utc)

    async def mark_completed(self, task_id: str, result: dict[str, Any]):
        """Mark task as completed with result."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(timezone.utc)
                task.result = result

    async def mark_failed(self, task_id: str, error: str):
        """Mark task as failed with error."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now(timezone.utc)
                task.error = error

    async def cleanup_old_tasks(self, max_age_hours: int = 24):
        """Remove completed/failed tasks older than max_age_hours."""
        async with self._lock:
            now = datetime.now(timezone.utc)
            to_remove = []
            for task_id, task in self._tasks.items():
                if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                    if task.completed_at:
                        age = (now - task.completed_at).total_seconds() / 3600
                        if age > max_age_hours:
                            to_remove.append(task_id)

            for task_id in to_remove:
                del self._tasks[task_id]


# Global task manager instance
_task_manager = OptimizationTaskManager()


def get_task_manager() -> OptimizationTaskManager:
    """Get global task manager instance."""
    return _task_manager
