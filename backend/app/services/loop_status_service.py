"""Operational status summary for the reality feedback loop."""

import logging
from typing import Any

from app.memory import event_ref_census, loop_run_store, review_queue_store
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
    # Calibration first, counts second: both read the `predictions` table, and
    # only the calibration path creates it. The other order made the first poll
    # of a cold database run a query against a table that did not exist yet and
    # report `{}` -- the same first-call artefact as the schema snapshot below.
    # (53.116 then made that `{}` two-valued: `predictions_available` says which
    # zero it is, and the warning names the exception type rather than logging
    # the reason and a traceback on every poll.)
    calibration = calibration_summary()
    prediction_counts, counts_readable = _prediction_counts()
    dangling_refs = _dangling_event_refs(events)
    actionable_refs = _actionable_dangling_event_refs(events)
    orphan_count, orphans_readable = _orphan_prediction_count(events)
    review_queue = _review_queue_counts()
    # One switch per table (53.117): both numbers below are counted from the
    # `predictions` table, so a failed read of either makes the single switch
    # false. Deliberately conservative -- when only the orphan query breaks,
    # `predictions` is still accurate and gets flagged with it; a consumer that
    # reads false as "do not trust these numbers" loses nothing, while the other
    # direction (true over a failed read) would be the 53.116 defect again.
    predictions_available = counts_readable and orphans_readable
    storage: dict[str, Any] = {
        # E1 (scale debt): every mutating event_store call rewrites this
        # whole file, so one write costs roughly these many bytes of
        # serialize + replace. Reported next to the record count so an
        # operator can see the two grow together and judge when the JSON
        # store has to become a real database -- instead of first noticing
        # it as a slow dashboard. len(events) reuses the load above rather
        # than re-reading the file for a number already in hand.
        "event_store_bytes": store_bytes(),
        "event_store_records": len(events),
    }
    status: dict[str, Any] = {
        "scheduler": {"running": scheduler_running},
        "storage": storage,
        # Every job name the ledger holds, not a list typed out here. Three were
        # named, the live ledger holds fifteen, and the only one whose last run
        # had failed was among the twelve that nothing watched -- so /api/health
        # answered 200 "ok" over it. `recent_runs_by_job` caps the window per
        # job for the same reason in reverse: one job holds 1193 of 1706 rows
        # and its newest 20 crowded every other name out of the timeline.
        "runs": {
            str(run["job_name"]): _visible_run(
                run, include_run_details=include_run_details
            )
            for run in loop_run_store.latest_run_per_job()
        },
        "recent_runs": [
            run for run in (
                _visible_run(item, include_run_details=include_run_details)
                for item in loop_run_store.recent_runs_by_job(limit=20)
            )
            if run is not None
        ],
        "counts": {
            "events": len(events),
            "resolved_events": len(resolved),
            "open_opportunities": len(list_open_opportunities(limit=1000)),
            "predictions": prediction_counts,
            # §5: `{}` above means "no predictions" only when this is true. On a
            # failed read the map is unknown, not empty, and the dashboard must
            # be able to tell those apart instead of showing a confident zero.
            "predictions_available": predictions_available,
            "pending_links": len(list_pending()),
            "pending_reviews": review_queue["pending_total"],
            "breached_reviews": review_queue["breached_total"],
            "orphan_predictions": orphan_count,
            # E2: the two published keys are kept because the dashboard badge
            # reads them by name, but they were never the whole picture --
            # `simulated_trades`, `review_queue_items` and `decision_timeline`
            # also carry an event_id and were unwatched, and the one genuinely
            # stranded row in the live database sat in the first of those, so
            # the badge read 0. `dangling_refs` is the honest total and
            # `dangling_by_table` says where they are, since "3 broken
            # references" is not actionable without knowing which store.
            "dangling_predictions": dangling_refs.get("predictions", 0),
            "dangling_links": dangling_refs.get("event_market_links", 0),
            "dangling_refs": sum(dangling_refs.values()),
            "dangling_by_table": dangling_refs,
            # Audit 53.95: `dangling_refs` above counts history -- a voided
            # trade whose event was purged is a fact, not a defect, so the
            # badge reading it could never return to zero after the first
            # purge (measured: stuck at 1 on the live database). These two
            # keys count only the rows that still have a next step blocked,
            # per LIVE_PREDICATES; that is the number an operator can act on,
            # and the one the dashboard badge reads.
            "actionable_dangling_refs": sum(actionable_refs.values()),
            "actionable_dangling_by_table": actionable_refs,
            "calibration_n": calibration.get("n", 0),
        },
        "calibration": calibration,
        "review_queue": review_queue,
    }
    # Snapshot the schema versions LAST, after every reader above has run
    # (audit 53.114). Read first, this key reported what the database looked
    # like *before* this very request created the tables: on a cold loop
    # database the first poll answered `{"event_market_links": null,
    # "loop_runs": null, ...}` and the second answered `1, 1`, because
    # `list_pending()` and `latest_run_per_job()` run while the dict below is
    # being built. Measured on a fresh sandbox: 1 256 bytes then 1 293, equal
    # from the second poll on. A health payload polled by monitors must not
    # depend on how many times it has been polled -- that is the same shape as
    # a reading that is only correct after a warm-up.
    storage["loop_db_schema_versions"] = sqlite_db.schema_versions()
    return status


def _review_queue_counts() -> dict[str, Any]:
    """Review-queue depth / oldest wait / SLA breaches for the status payload.

    ``pending_links`` above counts ``event_market_link_store`` — a different
    store — and used to be the only "pending" number here, so a human review
    backlog of any size was invisible from the status endpoint.

    Failures degrade to zeros: a status endpoint that raises because the review
    queue is unreadable is worse than one reporting an empty queue. Since audit
    53.118 the block carries ``available`` (the §5 switch) and the failure logs
    the exception *type* instead of a traceback -- this endpoint is polled and
    ``logs/`` is a persistent surface (§53.107).

    The switch leads the block because every number below derives from it -- and
    ``counts.pending_reviews`` / ``counts.breached_reviews`` are copied straight
    out of here, so one failed read used to turn four numbers into a confident
    "nothing pending". A reader that sees ``available: false`` must treat those
    four as unknown, whichever block it is reading.

    ``enabled`` is the other non-numeric fact (audit 53.119): whether the
    feature was ever on. ``event_intelligence_service`` only enqueues when it is
    true, so a disabled queue answers zero forever -- and the 53.118 probe
    measured that it serializes *byte for byte* like a drained one. It is not a
    switch: ``enabled=false`` is a legitimate reason for those zeros, a failed
    read is not.
    """
    # A config fact, read once so both paths report the same thing.
    enabled = settings.REVIEW_QUEUE_ENABLED
    try:
        summary = review_queue_store.queue_sla_summary(sla_hours={
            "ERROR": settings.REVIEW_QUEUE_SLA_ERROR_HOURS,
            "WARN": settings.REVIEW_QUEUE_SLA_WARN_HOURS,
        })
        available = True
    except Exception as exc:
        logger.warning("review queue sla summary unavailable: %s", type(exc).__name__)
        summary = {
            "pending_total": 0,
            "oldest_age_hours": None,
            "oldest_item_id": None,
            "breached_total": 0,
            "unknown_severity": 0,
            "sla_hours": {},
            "by_severity": {},
            "by_trigger": {},
        }
        available = False
    return {"available": available, "enabled": enabled, **summary}


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


def _prediction_counts() -> tuple[dict[str, int], bool]:
    """Status-grouped prediction counts, and whether that map could be read.

    The second element is the §5 producer switch. ``{}`` came back both when the
    table was empty and when the query failed, and the two need opposite
    reactions: nothing to do, versus something is broken. A plain bool, not an
    error string -- the payload is polled every few seconds and the reason
    belongs in the log, where the *type* of the exception is the safe half of it
    (§53.107: the message can carry the database path or a SQL fragment).

    The failure path deliberately logs no traceback: ``/api/health`` and
    ``/loop/status`` are polled, so ``exc_info=True`` would append five lines of
    traceback to ``logs/`` on every poll that finds the database broken.
    """
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
    except Exception as exc:
        logger.warning("prediction status counts unavailable: %s", type(exc).__name__)
        return {}, False
    return {str(row["status"]): int(row["n"] or 0) for row in rows}, True


def _dangling_event_refs(events: list[dict[str, Any]]) -> dict[str, int]:
    event_ids = {
        str(entry.get("event_id") or "")
        for entry in events
        if entry.get("event_id")
    }
    return event_ref_census.dangling_counts(event_ids)


def _actionable_dangling_event_refs(events: list[dict[str, Any]]) -> dict[str, int]:
    """Same id set, filtered to references that still block a next step.

    Built from the same ``events`` load rather than a second store read: the
    two readings must agree about which ids exist, and /api/health is polled
    constantly (E1's lesson, applied to the pair instead of to one query).
    """
    event_ids = {
        str(entry.get("event_id") or "")
        for entry in events
        if entry.get("event_id")
    }
    return event_ref_census.actionable_dangling_counts(event_ids)


def _orphan_prediction_count(events: list[dict[str, Any]]) -> tuple[int, bool]:
    """Resolved events whose prediction is still marked open, and whether that
    count could be read.

    Reads every open prediction id in one query rather than calling
    get_prediction() per event: that opened a fresh connection and ran a
    lookup for each resolved event, so /api/health scaled linearly with the
    event store and measured over a second on a modest store.

    The second element is the §5 switch (53.117), for the same reason
    ``_prediction_counts`` has one: ``0`` meant both "no orphans" and "the
    query failed", and the failure path wrote a traceback to ``logs/`` on a
    payload that is polled every few seconds. No resolved events means no
    query ran at all -- nothing can have failed, so that is readable.
    """
    resolved_ids = {
        str(entry["event_id"])
        for entry in events
        if entry.get("event_id") and (entry.get("record") or {}).get("outcome") is not None
    }
    if not resolved_ids:
        return 0, True

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
    except Exception as exc:
        logger.warning("orphan prediction count unavailable: %s", type(exc).__name__)
        return 0, False

    return sum(1 for row in rows if str(row["event_id"]) in resolved_ids), True
