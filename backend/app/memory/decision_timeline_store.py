"""Decision timeline snapshot store (Plan 5 §5.4).

Append-only SQLite store of overlay-bearing record snapshots. On every
``save_events`` call (when ``DECISION_TIMELINE_ENABLED`` is on) the
orchestrator calls ``record_snapshot(record)`` which extracts the
direction/overlay/probability fields and inserts a new row. The
``/api/events/{event_id}/decision-timeline`` route reads these back in
ASC order so the frontend can render a Diff Viewer timeline.

Schema follows the ``event_market_link_store.py`` /
``source_trust_registry_store.py`` pattern: module-level functions, lazy
schema init via ``_ensure_schema`` (double-checked locking), shared
``sqlite_db.py`` plumbing.

The store is byte-identical-inert when the flag is off:
``record_snapshot`` still works if called directly (tests use it), but
``event_store.save_events`` only calls it when
``settings.DECISION_TIMELINE_ENABLED`` is true.
"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from app.utils import sqlite_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_timeline (
    snapshot_id              TEXT PRIMARY KEY,
    event_id                 TEXT NOT NULL,
    recorded_at              TEXT NOT NULL DEFAULT (datetime('now')),
    final_displayed_direction TEXT,
    final_downgrade_reason   TEXT,
    probability_json         TEXT,
    decision_quality_json    TEXT,
    market_quality_json      TEXT,
    source_reliability_json  TEXT,
    execution_quality_json   TEXT,
    llm_degraded_mode        INTEGER,
    guardrail_fired_json     TEXT,
    outcome                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_dt_event_id ON decision_timeline(event_id);
CREATE INDEX IF NOT EXISTS idx_dt_recorded_at ON decision_timeline(recorded_at);
"""

_SCHEMA_VERSION = 1
_MIGRATIONS: dict[str, str] = {}

_INITIALIZED: set[str] = set()
_INIT_GUARD = threading.Lock()


def _ensure_schema(path: str) -> None:
    if path in _INITIALIZED:
        return
    with _INIT_GUARD:
        if path in _INITIALIZED:
            return
        with sqlite_db.writing(path) as conn:
            conn.executescript(_SCHEMA)
            sqlite_db.apply_migrations(conn, "decision_timeline",
                                       _SCHEMA_VERSION, _MIGRATIONS)
            sqlite_db.record_schema_version(conn, "decision_timeline",
                                            _SCHEMA_VERSION)
        _INITIALIZED.add(path)


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _row_to_snapshot(row: Any) -> dict[str, Any]:
    return {
        "snapshot_id": row["snapshot_id"],
        "event_id": row["event_id"],
        "recorded_at": row["recorded_at"],
        "final_displayed_direction": row["final_displayed_direction"],
        "final_downgrade_reason": row["final_downgrade_reason"],
        "probability": _json_loads(row["probability_json"]),
        "decision_quality": _json_loads(row["decision_quality_json"]),
        "market_quality": _json_loads(row["market_quality_json"]),
        "source_reliability": _json_loads(row["source_reliability_json"]),
        "execution_quality": _json_loads(row["execution_quality_json"]),
        "llm_degraded_mode": bool(row["llm_degraded_mode"])
                             if row["llm_degraded_mode"] is not None else None,
        "guardrail_fired": _json_loads(row["guardrail_fired_json"]),
        "outcome": row["outcome"],
    }


def record_snapshot(record: dict[str, Any]) -> str | None:
    """Append one overlay-bearing snapshot of ``record`` to the timeline.

    Extracts the direction / overlay / probability / outcome fields and
    inserts a new row. Returns the new ``snapshot_id``. Does not crash on
    missing fields — overlays default to None.

    The caller (``event_store.save_events``) gates this call behind
    ``settings.DECISION_TIMELINE_ENABLED`` so the store stays empty
    (and the schema is never created) when the flag is off.
    """
    if not isinstance(record, dict):
        return None
    event_id = record.get("event_id")
    if not event_id:
        return None
    probability = record.get("probability")
    if not isinstance(probability, dict):
        probability = None
    llm_tel = record.get("llm_telemetry")
    llm_degraded = None
    if isinstance(llm_tel, dict):
        llm_degraded = bool(llm_tel.get("degraded_mode", False))
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    snapshot_id = str(uuid.uuid4())
    with sqlite_db.writing(path) as conn:
        conn.execute(
            """
            INSERT INTO decision_timeline (
                snapshot_id, event_id,
                final_displayed_direction, final_downgrade_reason,
                probability_json, decision_quality_json, market_quality_json,
                source_reliability_json, execution_quality_json,
                llm_degraded_mode, guardrail_fired_json, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                event_id,
                record.get("final_displayed_direction"),
                record.get("final_downgrade_reason"),
                _json_dumps(probability),
                _json_dumps(record.get("decision_quality")),
                _json_dumps(record.get("market_quality")),
                _json_dumps(record.get("source_reliability")),
                _json_dumps(record.get("execution_quality")),
                llm_degraded,
                _json_dumps(record.get("guardrail_fired")),
                record.get("outcome"),
            ),
        )
    return snapshot_id


def list_snapshots(event_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return snapshots for ``event_id`` ordered ASC by ``recorded_at``.

    Returns the most recent ``limit`` rows (default 100). Empty list when
    the event has no snapshots (e.g. flag was off when it was saved).
    """
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT *, rowid FROM decision_timeline
                WHERE event_id = ?
                ORDER BY rowid DESC
                LIMIT ?
            ) AS recent
            ORDER BY rowid ASC
            """,
            (event_id, limit),
        ).fetchall()
    return [_row_to_snapshot(row) for row in rows]


def count_snapshots(event_id: str) -> int:
    """Return the number of snapshots stored for ``event_id``."""
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    with sqlite_db.reading(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM decision_timeline WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    return int(row["n"]) if row else 0
