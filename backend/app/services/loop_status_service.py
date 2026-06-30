"""Operational status summary for the reality feedback loop."""

import logging
from typing import Any

from app.memory import loop_run_store
from app.memory.event_market_link_store import list_pending
from app.memory.event_store import list_all_events, list_resolved_events
from app.memory.prediction_store import (
    calibration_summary,
    get_prediction,
    list_open_opportunities,
)
from app.utils import sqlite_db
from app.utils.sqlite_db import reading

logger = logging.getLogger(__name__)


def loop_status(
    *,
    scheduler_running: bool | None = None,
    include_run_details: bool = False,
) -> dict[str, Any]:
    events = list_all_events()
    resolved = list_resolved_events()
    prediction_counts = _prediction_counts()
    dangling_refs = _dangling_event_refs(events)
    orphan_count = _orphan_prediction_count(events)
    calibration = calibration_summary()
    return {
        "scheduler": {"running": scheduler_running},
        "storage": {
            "loop_db_schema_versions": sqlite_db.schema_versions(),
        },
        "runs": {
            "event_discover": _visible_run(
                loop_run_store.last_run("event_discover"),
                include_run_details=include_run_details,
            ),
            "event_auto_resolve": _visible_run(
                loop_run_store.last_run("event_auto_resolve"),
                include_run_details=include_run_details,
            ),
            "loop_db_maintenance": _visible_run(
                loop_run_store.last_run("loop_db_maintenance"),
                include_run_details=include_run_details,
            ),
        },
        "recent_runs": [
            run for run in (
                _visible_run(item, include_run_details=include_run_details)
                for item in loop_run_store.recent_runs(limit=20)
            )
            if run is not None
        ],
        "counts": {
            "events": len(events),
            "resolved_events": len(resolved),
            "open_opportunities": len(list_open_opportunities(limit=1000)),
            "predictions": prediction_counts,
            "pending_links": len(list_pending()),
            "orphan_predictions": orphan_count,
            "dangling_predictions": dangling_refs["predictions"],
            "dangling_links": dangling_refs["links"],
            "calibration_n": calibration.get("n", 0),
        },
        "calibration": calibration,
    }


def _visible_run(
    run: dict[str, Any] | None,
    *,
    include_run_details: bool,
) -> dict[str, Any] | None:
    if run is None:
        return None
    if include_run_details:
        return run
    return {
        key: run.get(key)
        for key in ("job_name", "status", "started_at", "finished_at", "duration_ms")
        if key in run
    }


def _prediction_counts() -> dict[str, int]:
    path = sqlite_db.loop_db_path()
    try:
        with reading(path) as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS n
                FROM predictions
                GROUP BY status
                """
            ).fetchall()
    except Exception:
        logger.warning("prediction status counts query failed", exc_info=True)
        return {}
    return {str(row["status"]): int(row["n"] or 0) for row in rows}


def _dangling_event_refs(events: list[dict[str, Any]]) -> dict[str, int]:
    event_ids = {
        str(entry.get("event_id") or "")
        for entry in events
        if entry.get("event_id")
    }
    return {
        "predictions": _dangling_count("predictions", event_ids),
        "links": _dangling_count("event_market_links", event_ids),
    }


def _dangling_count(table: str, event_ids: set[str]) -> int:
    path = sqlite_db.loop_db_path()
    try:
        with reading(path) as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT event_id
                FROM {table}
                WHERE event_id IS NOT NULL AND event_id != ''
                """
            ).fetchall()
    except Exception:
        logger.warning("dangling count query failed for table=%s", table, exc_info=True)
        return 0
    return sum(1 for row in rows if str(row["event_id"]) not in event_ids)


def _orphan_prediction_count(events: list[dict[str, Any]]) -> int:
    count = 0
    for entry in events:
        record = entry.get("record") or {}
        if record.get("outcome") is None:
            continue
        event_id = entry.get("event_id")
        if not event_id:
            continue
        pred = get_prediction(event_id)
        if pred is not None and pred.get("status") == "open":
            count += 1
    return count
