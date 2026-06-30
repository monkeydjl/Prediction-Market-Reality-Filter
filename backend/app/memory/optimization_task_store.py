"""Durable persistence for optimization task state.

The :class:`OptimizationTaskManager` (see
``app.services.optimization_task_manager``) keeps every in-flight auto-tune /
batch-optimize task in process memory so the API can poll progress cheaply.
That state is lost on process restart, which leaves the frontend polling
``/auto-tune/status/{task_id}`` against a 404 even though the task was
submitted. This module mirrors the in-memory state into the same SQLite file
the rest of the V2 loop store uses (``settings.LOOP_DB_FILE``), so a restart
can re-hydrate the manager and completed tasks remain queryable until the
daily cleanup job prunes them.

The store deliberately stays a thin CRUD layer over a single table; the task
manager remains the single source of truth for in-flight mutations and calls
into :func:`upsert_task` after every transition. SQLite writes go through the
shared :data:`sqlite_db._WRITE_LOCK`, so concurrent writers from the API and
the background optimization task serialize safely.
"""

import json
from typing import Any

from app.utils import sqlite_db
from app.utils.helpers import utc_now
from app.utils.sqlite_db import reading, writing

_SCHEMA_VERSION = 1


def _ensure_schema(path: str) -> None:
    with writing(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS optimization_tasks (
                task_id TEXT PRIMARY KEY,
                engine_name TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                current_match TEXT,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                logs_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_optimization_tasks_status "
            "ON optimization_tasks(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_optimization_tasks_created "
            "ON optimization_tasks(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_optimization_tasks_completed "
            "ON optimization_tasks(completed_at)"
        )
        sqlite_db.record_schema_version(conn, "optimization_tasks", _SCHEMA_VERSION)


def _row_to_task(row: Any) -> dict[str, Any]:
    """Convert a DB row into the dict shape OptimizationTask.to_dict emits."""
    out = {
        "task_id": row["task_id"],
        "engine_name": row["engine_name"],
        "status": row["status"],
        "progress": int(row["progress"] or 0),
        "total": int(row["total"] or 0),
        "current_match": row["current_match"],
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "updated_at": row["updated_at"],
    }
    result_raw = row["result_json"]
    if result_raw:
        try:
            out["result"] = json.loads(result_raw)
        except json.JSONDecodeError:
            out["result"] = None
    else:
        out["result"] = None
    logs_raw = row["logs_json"] or "[]"
    try:
        out["logs"] = json.loads(logs_raw)
    except json.JSONDecodeError:
        out["logs"] = []
    return out


def upsert_task(task: dict[str, Any]) -> None:
    """Insert or replace a task row.

    Called by the task manager after every mutation so the on-disk row always
    reflects the latest in-memory state. ``logs`` may be the full list or a
    trimmed tail — the store persists whatever it is given; the manager is
    responsible for the trimming policy.
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    logs = task.get("logs") or []
    if isinstance(logs, list):
        logs_json = json.dumps(logs, ensure_ascii=False)
    else:
        # Defensive: tolerate a caller passing pre-serialized JSON.
        logs_json = str(logs)
    result = task.get("result")
    result_json = (
        json.dumps(result, ensure_ascii=False, sort_keys=True)
        if result is not None
        else None
    )
    with writing(path) as conn:
        conn.execute(
            """
            INSERT INTO optimization_tasks (
                task_id, engine_name, status, progress, total,
                current_match, result_json, error,
                created_at, started_at, completed_at,
                logs_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                engine_name=excluded.engine_name,
                status=excluded.status,
                progress=excluded.progress,
                total=excluded.total,
                current_match=excluded.current_match,
                result_json=excluded.result_json,
                error=excluded.error,
                started_at=excluded.started_at,
                completed_at=excluded.completed_at,
                logs_json=excluded.logs_json,
                updated_at=excluded.updated_at
            """,
            (
                task["task_id"],
                task.get("engine_name", ""),
                task.get("status", "pending"),
                int(task.get("progress", 0) or 0),
                int(task.get("total", 0) or 0),
                task.get("current_match"),
                result_json,
                task.get("error"),
                task.get("created_at") or utc_now(),
                task.get("started_at"),
                task.get("completed_at"),
                logs_json,
                utc_now(),
            ),
        )


def get_task(task_id: str) -> dict[str, Any] | None:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        row = conn.execute(
            "SELECT * FROM optimization_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
    return _row_to_task(row) if row else None


def delete_task(task_id: str) -> None:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with writing(path) as conn:
        conn.execute(
            "DELETE FROM optimization_tasks WHERE task_id=?",
            (task_id,),
        )


def list_recent_tasks(limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        if status:
            rows = conn.execute(
                """
                SELECT * FROM optimization_tasks
                WHERE status=?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM optimization_tasks
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [_row_to_task(row) for row in rows]


def delete_older_than(completed_at_cutoff_iso: str, statuses: list[str]) -> int:
    """Delete rows whose status is in ``statuses`` AND completed_at is older
    than (strictly less than) ``completed_at_cutoff_iso``.

    Returns the number of deleted rows. Rows with NULL ``completed_at`` are
    never deleted here — a terminal task with no ``completed_at`` indicates a
    crash mid-flight and should be surfaced (e.g. as ``failed``) rather than
    silently dropped.
    """
    if not statuses:
        return 0
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    placeholders = ",".join("?" for _ in statuses)
    with writing(path) as conn:
        cur = conn.execute(
            f"""
            DELETE FROM optimization_tasks
            WHERE status IN ({placeholders})
              AND completed_at IS NOT NULL
              AND completed_at < ?
            """,
            (*statuses, completed_at_cutoff_iso),
        )
        return int(cur.rowcount or 0)
