"""event_resolve_service.py
=========================
Event-layer resolution: the shared resolve-with-calibration helper plus the
auto-resolve workflow.

`resolve_with_calibration` is the single resolution path used by both the
manual resolve endpoint and auto-resolve. It computes the calibration snapshot
from the event's probability trajectory, persists outcome + calibration onto
the record, and appends an outcome snapshot to the audit log. Centralizing it
here keeps the calibration computation in one place (no duplication between
manual and auto paths).

`auto_resolve_events` fetches resolved Polymarket markets, matches each
unresolved local event by question similarity (shared text_match utilities),
and resolves each match with a calibration snapshot. It mirrors the
market-layer auto_resolve_service but writes to the event store / event audit
instead of agent_memory / analysis_audit.

Event vocabulary only - no trading terms.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.memory.event_store import get_event, list_all_events, resolve_event
from app.services.calibration_service_event import score_event
from app.services.event_audit_service import histories_by_event, history_for_event, record_outcome
from app.services.trend_analysis_service import analyze_trend
from app.utils.text_match import build_index, find_match

logger = logging.getLogger(__name__)


async def resolve_with_calibration(
    event_id: str,
    actual_outcome: float,
    confidence: float = 1.0,
    source: str = "manual",
    notes: str = "",
    snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Resolve an event with a settled outcome and a calibration snapshot.

    Scores the event's latest probability estimate against the outcome
    (trajectory context included), persists outcome + calibration onto the
    record, and appends an outcome snapshot to the audit log. Returns the
    updated entry, or None when event_id is not stored (callers raise 404).

    This is the single resolution path: the manual resolve endpoint and
    auto-resolve both call it, so the calibration logic lives in one place.

    `snapshots` optionally supplies this event's audit snapshots (any kind); the
    caller may pass them when resolving many events to avoid an N-times full
    audit-log read (auto-resolve passes one slice per event from a single
    histories_by_event() read). When None, the snapshots are read here.
    """
    entry = get_event(event_id)
    if entry is None:
        return None

    record = entry.get("record") or {}

    # Outcome snapshots are filtered out so "latest" is the latest probability
    # estimate, not a settlement marker. analyze_trend already skips
    # non-numeric estimates, but filtering here keeps the trajectory_observations
    # count honest (it counts probability snapshots, not outcome markers).
    raw_snapshots = snapshots if snapshots is not None else history_for_event(event_id)
    probability_snapshots = [
        snap for snap in raw_snapshots if snap.get("kind") != "outcome"
    ]
    trend = analyze_trend(probability_snapshots)
    if trend["latest_probability"] is not None:
        estimated = trend["latest_probability"]
    else:
        # No probability history yet: fall back to the record's baseline so
        # the event still gets a calibration score rather than None.
        estimated = (record.get("probability") or {}).get("baseline", 50.0)

    calibration = score_event(
        estimated=estimated,
        actual_outcome=actual_outcome,
        trajectory_observations=trend["observations"],
        trajectory_span_hours=trend["span_hours"],
    )

    outcome = {
        "status": "resolved",
        "actual_outcome": actual_outcome,
        "confidence": confidence,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "notes": notes,
    }
    updated = resolve_event(event_id, outcome, calibration=calibration)
    record = (updated or {}).get("record") or {}
    record_outcome(event_id, record.get("event_title", ""), outcome)
    return updated


async def auto_resolve_events(resolved_limit: int = 200) -> dict[str, Any]:
    """Auto-resolve events whose questions match resolved Polymarket markets.

    Fetches resolved markets, builds a question index, and for each unresolved
    local event whose question matches (exact normalized key or fuzzy overlap
    >= FUZZY_THRESHOLD), resolves it with a calibration snapshot
    (source = "auto_polymarket"). Already-resolved events are skipped. Returns
    a summary; never raises (network / match failures degrade to fewer
    resolutions).
    """
    from app.services.polymarket_history_service import fetch_resolved_markets

    try:
        resolved_markets = await fetch_resolved_markets(limit=resolved_limit)
    except Exception as exc:
        logger.warning("auto_resolve: fetch_resolved_markets failed: %s", exc)
        return {"status": "fetch_failed", "resolved_count": 0, "checked_count": 0,
                "unresolved_events": 0, "matches": []}

    if not resolved_markets:
        return {"status": "no_resolved_markets", "resolved_count": 0,
                "checked_count": 0, "unresolved_events": _count_unresolved(),
                "matches": []}

    index = build_index(resolved_markets)
    resolved_count = 0
    match_log: list[dict[str, Any]] = []

    # Scan EVERY stored event (unranked, unbounded), not just the top-200 by
    # value_score - otherwise low-value events would never be resolved and the
    # calibration aggregate (which reads all resolved events) would be silently
    # biased toward high-value events.
    entries = list_all_events()
    # Read the audit log once and group by event_id, instead of calling
    # history_for_event per event (which would re-read the whole file N times).
    histories = histories_by_event()
    for entry in entries:
        record = entry.get("record") or {}
        if record.get("outcome") is not None:
            continue  # already resolved
        event_id = entry.get("event_id")
        if not event_id:
            continue
        question = record.get("event_title") or ""
        if not question:
            # No title means nothing to match on; do NOT fall back to
            # event_summary (narrative text would produce garbage matches).
            continue
        match = find_match(question, index)
        if match is None:
            continue
        matched_question, actual_outcome, score = match
        try:
            await resolve_with_calibration(
                event_id=event_id,
                actual_outcome=actual_outcome,
                confidence=1.0,
                source="auto_polymarket",
                notes=f"matched: {matched_question[:100]}",
                snapshots=histories.get(event_id, []),
            )
        except Exception as exc:
            logger.warning(
                "auto_resolve: failed to resolve event %s: %s", event_id, exc
            )
            continue
        resolved_count += 1
        match_log.append({
            "event_id": event_id,
            "event_title": question[:80],
            "matched_to": matched_question[:80],
            "actual_outcome": actual_outcome,
            "match_score": round(score, 3),
        })

    unresolved_events = sum(
        1 for entry in entries
        if (entry.get("record") or {}).get("outcome") is None
    )
    return {
        "status": "ok",
        "resolved_count": resolved_count,
        "checked_count": len(resolved_markets),
        "unresolved_events": unresolved_events,
        "matches": match_log,
    }


def _count_unresolved() -> int:
    """Count stored events without an outcome (best-effort, for reporting).

    Unranked and unbounded so the count is accurate, not capped at the top-200
    by value_score like list_events.
    """
    return sum(
        1 for entry in list_all_events()
        if (entry.get("record") or {}).get("outcome") is None
    )
