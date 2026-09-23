"""Durable run ledger for the reality feedback loop."""

import json
import threading
import uuid
from datetime import datetime
from typing import Any

from app.utils import sqlite_db
from app.utils.helpers import utc_now
from app.utils.sqlite_db import reading, writing

_SCHEMA_VERSION = 1
_MIGRATIONS: dict[str, str] = {}

STALE_RUN_ERROR = "run abandoned: owning process exited before finishing"

_INITIALIZED: set[str] = set()
_INIT_GUARD = threading.Lock()


def _ensure_schema(path: str) -> None:
    """Build the table on first use of a given DB path, then migrate (idempotent).

    Memoized per path, like every other store in this package. Without it the
    four read functions below each opened a write transaction before their
    SELECT: `record_schema_version` rewrites `updated_at` unconditionally, so the
    transaction always had a dirty page, and closing the last handle to a WAL
    database checkpoints it -- fsync the whole main file at `synchronous=FULL`.
    Over the live 17.5 MB ledger that was 141 ms per call against a 3 ms query,
    on the process-wide write lock the scheduler's own `_start_run` needs.
    """
    if path in _INITIALIZED:
        return
    with _INIT_GUARD:
        if path in _INITIALIZED:
            return
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
            sqlite_db.apply_migrations(conn, "loop_runs", _SCHEMA_VERSION, _MIGRATIONS)
            sqlite_db.record_schema_version(conn, "loop_runs", _SCHEMA_VERSION)
        _INITIALIZED.add(path)


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


def recent_runs(limit: int = 20, job_name: str | None = None) -> list[dict[str, Any]]:
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        if job_name:
            rows = conn.execute(
                """
                SELECT * FROM loop_runs
                WHERE job_name=?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (job_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM loop_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [_row_to_dict(row) for row in rows]


def latest_run_per_job() -> list[dict[str, Any]]:
    """Newest run of every job name in the ledger, newest first.

    The watched set derived instead of typed out. Four callers -- the
    ``/api/health`` status payload, the `scheduler_job_failed` anomaly, the
    quality-metrics scheduler block and the `SCHEDULER_LAST_SUCCESS` gauge --
    each named ``event_discover``, ``event_auto_resolve`` and
    ``loop_db_maintenance``. The live ledger holds 15 job names over 1706 rows,
    so twelve were unwatched, and the only one whose last run had *failed*
    (``world_cup_api_football_validate``, 2026-07-07, "API-Football returned 0
    fixtures") was among them: ``/api/health`` answered 200 "ok" and
    ``scripts/healthcheck.py`` went on feeding the dead-man switch.

    Two names in the ledger have no scheduler job id at all -- they are written
    by request-triggered services -- so deriving from the ledger rather than
    from ``scheduler.py``'s ``add_job`` ids is deliberate: what matters to an
    operator is which recorded work failed, whoever started it.

    One row per job even when two share a ``started_at``: the query orders by
    ``id`` as well and the first row per name wins, so the choice is the
    query's rather than storage order. Grouped-max JOIN rather than
    ``ROW_NUMBER()`` -- `prediction_store` avoids window functions on purpose --
    and it stays on ``idx_loop_runs_job_started`` (1.03 ms over the live 1706
    rows; a correlated per-row count was 246 ms).
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        rows = conn.execute(
            """
            SELECT r.* FROM loop_runs r
            JOIN (
                SELECT job_name, MAX(started_at) AS started_at
                FROM loop_runs
                GROUP BY job_name
            ) newest
              ON newest.job_name = r.job_name
             AND newest.started_at = r.started_at
            ORDER BY r.started_at DESC, r.id DESC
            """
        ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest.setdefault(str(row["job_name"]), _row_to_dict(row))
    return list(latest.values())


def recent_runs_by_job(limit: int = 20, per_job: int = 2) -> list[dict[str, Any]]:
    """Recent runs with at most ``per_job`` rows from any one job, newest first.

    ``recent_runs(limit=20)`` is a plain global window, and one chatty job
    empties it: ``world_cup_scoring_reconcile`` holds 1193 of the live ledger's
    1706 rows and its newest 20 postdate every other job, so the window
    returned **one** distinct job name out of 15 and the dashboard's run
    timeline showed that job alone.

    Capping per job puts 11 names in the same 20 rows. It is still a truncated
    window -- the oldest names fall off the end -- so it is the timeline, not
    the census; ``latest_run_per_job`` is the complete one and is what health
    reads.

    One indexed query per distinct name (16 queries, 1.90 ms over the live
    ledger) rather than one clever query: the single-query forms that rank
    within a group cost two orders of magnitude more here, and the count of
    distinct names is bounded by the job names written in code, not by rows.
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with reading(path) as conn:
        names = [
            str(row["job_name"])
            for row in conn.execute(
                "SELECT DISTINCT job_name FROM loop_runs"
            ).fetchall()
        ]
        rows: list[Any] = []
        for name in names:
            rows.extend(
                conn.execute(
                    """
                    SELECT * FROM loop_runs
                    WHERE job_name=?
                    ORDER BY started_at DESC, id DESC
                    LIMIT ?
                    """,
                    (name, per_job),
                ).fetchall()
            )
    rows.sort(key=lambda row: (str(row["started_at"]), str(row["id"])), reverse=True)
    return [_row_to_dict(row) for row in rows[:limit]]


def fail_stale_running_rows(cutoff_started_at: str) -> int:
    """Mark every ``running`` row whose ``started_at`` is older than the cutoff
    as failed. Returns the number of rows changed.

    Threshold-based, not startup-based, unlike ``optimization_task_store.
    fail_interrupted_tasks``: the API process and the scheduler are two
    processes on this ledger (the systemd units split them deliberately), so
    "a second process just started" cannot mean "nobody owns that row" -- the
    scheduler can be mid-run in a job while the API restarts.

    The row keeps its original ``started_at`` and gains ``finished_at`` (the
    moment it was failed) and ``duration_ms``, so a reconciled row is
    distinguishable from a completed one and the timeline stays honest.
    ``error`` names the mechanism, because "the process died" is the actual
    outcome of every row this catches.
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    now = utc_now()
    with writing(path) as conn:
        cur = conn.execute(
            """
            UPDATE loop_runs
               SET status='failed',
                   error=COALESCE(NULLIF(error, ''), ?),
                   finished_at=COALESCE(finished_at, ?),
                   duration_ms=?
             WHERE status='running' AND started_at < ?
            """,
            (STALE_RUN_ERROR, now, now, cutoff_started_at),
        )
        return int(cur.rowcount or 0)


def delete_terminal_runs_before(cutoff_finished_at: str) -> int:
    """Delete terminal rows older than the cutoff. Returns rows deleted.

    The newest row of every job name is exempt regardless of age: ``/api/health``
    reads it through :func:`latest_run_per_job`, and a retention rule that
    removes it would blind the health probe exactly when a job has been quiet
    long enough to look dead. ``running`` rows are never deleted here -- an
    abandoned-looking run is a fact to surface via
    :func:`fail_stale_running_rows`, not one to quietly remove.
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with writing(path) as conn:
        cur = conn.execute(
            """
            DELETE FROM loop_runs
             WHERE status IN ('success', 'failed')
               AND finished_at < ?
               AND id NOT IN (
                   SELECT r.id FROM loop_runs r
                   JOIN (
                       SELECT job_name, MAX(started_at) AS started_at
                       FROM loop_runs
                       GROUP BY job_name
                   ) newest
                     ON newest.job_name = r.job_name
                    AND newest.started_at = r.started_at
               )
            """,
            (cutoff_finished_at,),
        )
        return int(cur.rowcount or 0)


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
