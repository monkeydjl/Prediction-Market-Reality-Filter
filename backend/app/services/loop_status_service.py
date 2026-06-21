"""Operational status summary for the reality feedback loop."""

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


def loop_status(
    *,
    scheduler_running: bool | None = None,
    include_run_details: bool = False,
) -> dict[str, Any]:
    events = list_all_events()
    resolved = list_resolved_events()
    prediction_counts = _prediction_counts()
    orphan_count = _orphan_prediction_count(events)
    calibration = calibration_summary()
    return {
        "scheduler": {"running": scheduler_running},
        "runs": {
            "event_discover": _visible_run(
                loop_run_store.last_run("event_discover"),
                include_run_details=include_run_details,
            ),
            "event_auto_resolve": _visible_run(
                loop_run_store.last_run("event_auto_resolve"),
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
        return {}
    return {str(row["status"]): int(row["n"] or 0) for row in rows}


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
