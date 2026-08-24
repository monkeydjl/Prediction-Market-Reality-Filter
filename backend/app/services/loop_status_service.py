"""Operational status summary for the reality feedback loop."""

import logging
from typing import Any

from app.memory import loop_run_store, review_queue_store
from app.memory.event_market_link_store import list_pending
from app.memory.event_store import list_all_events, list_resolved_events, store_bytes
from app.memory.prediction_store import (
    calibration_summary,
    list_open_opportunities,
)
from app.core.config import settings
from app.utils import sqlite_db
from app.utils.sqlite_db import reading

logger = logging.getLogger(__name__)


def loop_status(
    *,
    scheduler_running: bool | None = None,
    include_run_details: bool = False,
) -> dict[str, Any]:
    events = list_all_events()
    # Filter the load above rather than re-reading: list_resolved_events()
    # re-read and re-parsed the entire store file for a number derived from
    # `events`, so /api/health -- polled constantly by container healthchecks
    # and uptime monitors -- paid two full whole-file passes per poll (E1).
    resolved = list_resolved_events(events)
    prediction_counts = _prediction_counts()
    dangling_refs = _dangling_event_refs(events)
    orphan_count = _orphan_prediction_count(events)
    calibration = calibration_summary()
    review_queue = _review_queue_counts()
    return {
        "scheduler": {"running": scheduler_running},
        "storage": {
            "loop_db_schema_versions": sqlite_db.schema_versions(),
            # E1 (scale debt): every mutating event_store call rewrites this
            # whole file, so one write costs roughly these many bytes of
            # serialize + replace. Reported next to the record count so an
            # operator can see the two grow together and judge when the JSON
            # store has to become a real database -- instead of first noticing
            # it as a slow dashboard. len(events) reuses the load above rather
            # than re-reading the file for a number already in hand.
            "event_store_bytes": store_bytes(),
            "event_store_records": len(events),
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
            "pending_reviews": review_queue["pending_total"],
            "breached_reviews": review_queue["breached_total"],
            "orphan_predictions": orphan_count,
            "dangling_predictions": dangling_refs["predictions"],
            "dangling_links": dangling_refs["links"],
            "calibration_n": calibration.get("n", 0),
        },
        "calibration": calibration,
        "review_queue": review_queue,
    }


def _review_queue_counts() -> dict[str, Any]:
    """Review-queue depth / oldest wait / SLA breaches for the status payload.

    ``pending_links`` above counts ``event_market_link_store`` — a different
    store — and used to be the only "pending" number here, so a human review
    backlog of any size was invisible from the status endpoint.

    Failures degrade to zeros like ``_prediction_counts`` does: a status endpoint
    that raises because the review queue is unreadable is worse than one
    reporting an empty queue.
    """
    try:
        return review_queue_store.queue_sla_summary(sla_hours={
            "ERROR": settings.REVIEW_QUEUE_SLA_ERROR_HOURS,
            "WARN": settings.REVIEW_QUEUE_SLA_WARN_HOURS,
        })
    except Exception:
        logger.warning("review queue sla summary failed", exc_info=True)
        return {
            "pending_total": 0,
            "oldest_age_hours": None,
            "oldest_item_id": None,
            "breached_total": 0,
            "unknown_severity": 0,
            "sla_hours": {},
            "by_severity": {},
            "by_trigger": {},
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
    """Resolved events whose prediction is still marked open.

    Reads every open prediction id in one query rather than calling
    get_prediction() per event: that opened a fresh connection and ran a
    lookup for each resolved event, so /api/health scaled linearly with the
    event store and measured over a second on a modest store.
    """
    resolved_ids = {
        str(entry["event_id"])
        for entry in events
        if entry.get("event_id") and (entry.get("record") or {}).get("outcome") is not None
    }
    if not resolved_ids:
        return 0

    path = sqlite_db.loop_db_path()
    try:
        with reading(path) as conn:
            rows = conn.execute(
                """
                SELECT event_id
                FROM predictions
                WHERE status='open' AND event_id IS NOT NULL AND event_id != ''
                """
            ).fetchall()
    except Exception:
        logger.warning("orphan prediction count query failed", exc_info=True)
        return 0

    return sum(1 for row in rows if str(row["event_id"]) in resolved_ids)
