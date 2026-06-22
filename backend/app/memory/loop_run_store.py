"""Durable run ledger for the reality feedback loop."""

import json
import uuid
from datetime import datetime
from typing import Any

from app.utils import sqlite_db
from app.utils.helpers import utc_now
from app.utils.sqlite_db import reading, writing

_SCHEMA_VERSION = 1


def _ensure_schema(path: str) -> None:
    with writing(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS loop_runs (
                id TEXT PRIMARY KEY,
                job_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER,
                result_json TEXT,
                error TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_loop_runs_job_started "
            "ON loop_runs(job_name, started_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_loop_runs_status "
            "ON loop_runs(status)"
        )
        sqlite_db.record_schema_version(conn, "loop_runs", _SCHEMA_VERSION)


def start_run(job_name: str) -> str:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    run_id = str(uuid.uuid4())
    with writing(path) as conn:
        conn.execute(
            """
            INSERT INTO loop_runs (id, job_name, status, started_at)
            VALUES (?, ?, 'running', ?)
            """,
            (run_id, job_name, utc_now()),
        )
    return run_id


def finish_run(
    run_id: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    if status not in {"success", "failed"}:
        raise ValueError("status must be 'success' or 'failed'")
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    finished_at = utc_now()
    result_json = json.dumps(result or {}, ensure_ascii=False, sort_keys=True)
    with writing(path) as conn:
        row = conn.execute(
            "SELECT started_at FROM loop_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        duration_ms = _duration_ms(row["started_at"], finished_at)
        conn.execute(
            """
            UPDATE loop_runs
            SET status=?, finished_at=?, duration_ms=?, result_json=?, error=?
            WHERE id=?
            """,
            (status, finished_at, duration_ms, result_json, error, run_id),
        )
    return get_run(run_id)


def get_run(run_id: str) -> dict[str, Any] | None:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        row = conn.execute(
            "SELECT * FROM loop_runs WHERE id=?",
            (run_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def last_run(job_name: str) -> dict[str, Any] | None:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM loop_runs
            WHERE job_name=?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (job_name,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM loop_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _duration_ms(started_at: str, finished_at: str) -> int:
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
        return int((finish - start).total_seconds() * 1000)
    except Exception:
        return 0


def _row_to_dict(row: Any) -> dict[str, Any]:
    out = dict(row)
    raw = out.get("result_json")
    if raw:
        try:
            out["result"] = json.loads(raw)
        except json.JSONDecodeError:
            out["result"] = {}
    else:
        out["result"] = {}
    out.pop("result_json", None)
    return out
