from fastapi import APIRouter, Body, HTTPException, Query

from app.memory.event_store import (
    get_event,
    list_events,
    list_resolved_events,
    set_tracking,
)
from app.models.event import EventAnalysisRequest
from app.services.calibration_service_event import summarize
from app.services.event_audit_service import histories_by_event, history_for_event
from app.services.event_intelligence_service import (
    analyze_event_question,
    discover_events,
)
from app.services.event_resolve_service import (
    auto_resolve_events,
    resolve_with_calibration,
)
from app.services.historical_matching_service import find_similar
from app.services.trend_analysis_service import analyze_trend, rank_movers


router = APIRouter()


@router.get("/discover")
async def discover_event_intelligence(
    limit: int = Query(default=10, ge=1, le=20),
    use_cache: bool = Query(default=True),
):
    """Discover high-value events and return intelligence records."""
    return await discover_events(limit=limit, use_cache=use_cache)


@router.post("/analyze")
async def analyze_event_intelligence(payload: EventAnalysisRequest):
    """Analyze one event question and estimate probability change."""
    return await analyze_event_question(
        event_question=payload.event_question,
        baseline_probability=payload.baseline_probability,
        news_context=payload.news_context,
        volume=payload.volume,
        liquidity=payload.liquidity,
    )


@router.get("/")
async def list_event_intelligence(limit: int = Query(default=50, ge=1, le=200)):
    """List stored event intelligence records, ranked by value_score."""
    entries = list_events(limit=limit)
    return {"count": len(entries), "events": entries}


@router.get("/movers")
async def get_event_movers(limit: int = Query(default=10, ge=1, le=50)):
    """Rank tracked events by how much their probability has moved over time.

    Movers carry the English event_title from the audit snapshots; enrich each
    with the stored event_title_zh so the dashboard can show Chinese titles.
    """
    movers = rank_movers(histories_by_event(), limit=limit)
    for mover in movers:
        entry = get_event(mover.get("event_id", ""))
        title_zh = ((entry or {}).get("record") or {}).get("event_title_zh") or ""
        if title_zh:
            mover["event_title_zh"] = title_zh
    return {"count": len(movers), "movers": movers}


@router.get("/calibration")
async def get_event_calibration():
    """Cross-event calibration report: how accurate resolved events' latest
    probability estimates were vs their settled outcomes.

    Returns an overall block (brier / skill / grade / n) plus two parallel
    breakdowns: by_source (by the event source's platform) and
    by_base_rate_category (by the event's base-rate category, falling back to
    "unknown"). Empty until events are resolved; safe to call before any are.
    """
    resolved = list_resolved_events()
    events = []
    for entry in resolved:
        record = entry.get("record") or {}
        legacy = record.get("legacy_analysis") or {}
        events.append({
            "calibration": record.get("calibration"),
            "source": record.get("source") or {},
            "base_rate_category": legacy.get("base_rate_category", "unknown"),
        })
    return summarize(events)


# Dynamic routes declared after the static /discover, /analyze, / and /movers
# routes so the path parameter does not shadow them.
@router.get("/{event_id}")
async def get_event_intelligence(event_id: str):
    """Return a stored event intelligence record by event_id (404 if unknown)."""
    entry = get_event(event_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return entry


@router.patch("/{event_id}/tracking")
async def update_event_tracking(
    event_id: str,
    status: str | None = Body(default=None, embed=True),
    priority: str | None = Body(default=None, embed=True),
):
    """Update the human tracking decision (status / priority) for an event.

    status must be one of tracking/watching/archived; priority one of
    high/medium/low. At least one must be provided. 404 if event is unknown.
    """
    valid_status = {"tracking", "watching", "archived"}
    valid_priority = {"high", "medium", "low"}
    if status is not None and status not in valid_status:
        raise HTTPException(status_code=422, detail=f"Invalid status '{status}'")
    if priority is not None and priority not in valid_priority:
        raise HTTPException(status_code=422, detail=f"Invalid priority '{priority}'")
    if status is None and priority is None:
        raise HTTPException(
            status_code=422, detail="Provide at least one of status or priority"
        )
    updated = set_tracking(event_id, status=status, priority=priority)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return updated


@router.get("/{event_id}/history")
async def get_event_probability_history(event_id: str):
    """Return the probability snapshots recorded for an event over time.

    Outcome snapshots (kind="outcome") are excluded from this view: they are
    resolution markers, not probability estimates, and the outcome itself is
    already exposed on the record via GET /events/{event_id}. The trend is
    computed over the probability snapshots only.
    """
    probability_snapshots = [
        snap for snap in history_for_event(event_id)
        if snap.get("kind") != "outcome"
    ]
    if not probability_snapshots:
        raise HTTPException(
            status_code=404, detail=f"No history for event '{event_id}'"
        )
    return {
        "event_id": event_id,
        "count": len(probability_snapshots),
        "trend": analyze_trend(probability_snapshots),
        "history": probability_snapshots,
    }


@router.post("/{event_id}/resolve")
async def resolve_event_intelligence(
    event_id: str,
    actual_outcome: float = Body(..., ge=0, le=100, embed=True),
    confidence: float = Body(default=1.0, ge=0, le=1, embed=True),
    notes: str = Body(default="", embed=True),
):
    """Manually resolve an event with a settled outcome.

    `actual_outcome` is 0-100 (0=NO, 100=YES, middle=partial/probabilistic).
    `confidence` records how certain this resolution is (0-1). Computes a
    calibration snapshot from the event's probability trajectory, writes the
    outcome + calibration onto the stored record, and appends an outcome
    snapshot to the audit log. Returns the updated entry. 404 if unknown.

    The resolution + calibration logic lives in
    event_resolve_service.resolve_with_calibration, shared with auto-resolve.
    """
    updated = await resolve_with_calibration(
        event_id=event_id,
        actual_outcome=actual_outcome,
        confidence=confidence,
        source="manual",
        notes=notes,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return updated


@router.post("/resolve/auto")
async def auto_resolve_event_intelligence(
    limit: int = Query(default=200, ge=1, le=1000),
):
    """Auto-resolve events whose questions match resolved Polymarket markets.

    Fetches resolved markets, matches each unresolved local event by question
    similarity, and resolves each match with a calibration snapshot
    (source = "auto_polymarket"). Returns a summary
    {status, resolved_count, checked_count, unresolved_events, matches}.
    """
    return await auto_resolve_events(resolved_limit=limit)


@router.get("/{event_id}/similar")
async def get_similar_events(
    event_id: str,
    limit: int = Query(default=5, ge=1, le=20),
):
    """Find stored events most similar to this one (precedent context).

    Similarity uses both title-token overlap and entity overlap (max of the
    two), so events phrased differently but sharing key entities still match.
    """
    entry = get_event(event_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    record = entry.get("record") or {}
    query_text = record.get("event_title", "")
    query_entities = (record.get("semantics") or {}).get("entities") or []
    similar = find_similar(
        query_text,
        list_events(limit=200),
        limit=limit,
        exclude_event_id=event_id,
        query_entities=query_entities,
    )
    return {"event_id": event_id, "count": len(similar), "similar": similar}
