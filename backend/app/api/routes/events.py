from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.security import require_write_key
from app.memory.event_market_link_store import list_pending, set_verified
from app.memory.prediction_store import (
    calibration_summary,
    get_prediction,
    list_open_opportunities,
    list_recent,
)
from app.memory.event_store import (
    get_event,
    list_all_events,
    list_events,
    list_resolved_events,
    set_tracking,
)
from app.models.event import EventAnalysisRequest
from app.services.calibration_service_event import summarize
from app.services.decision_report_service import build_decision_report
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
from app.services.loop_status_service import loop_status
from app.services.trend_analysis_service import (
    analyze_edge_trajectory,
    analyze_trend,
    rank_fresh_edges,
    rank_movers,
)


router = APIRouter()


@router.get("/discover")
async def discover_event_intelligence(
    limit: int = Query(default=10, ge=1, le=20),
    use_cache: bool = Query(default=True),
    _auth: None = Depends(require_write_key),
):
    """Discover high-value events and return intelligence records."""
    return await discover_events(limit=limit, use_cache=use_cache)


@router.post("/analyze")
async def analyze_event_intelligence(
    payload: EventAnalysisRequest,
    _auth: None = Depends(require_write_key),
):
    """Analyze one event question and estimate probability change."""
    return await analyze_event_question(
        event_question=payload.event_question,
        baseline_probability=payload.baseline_probability,
        news_context=payload.news_context,
        volume=payload.volume,
        liquidity=payload.liquidity,
    )


@router.get("/")
async def list_event_intelligence(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List stored event intelligence records, ranked by value_score."""
    entries = list_events(limit=limit, offset=offset)
    total = len(list_all_events())
    return {"count": len(entries), "total": total, "limit": limit, "offset": offset, "events": entries}


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


@router.get("/loop/status")
async def get_loop_status():
    """Operational status for the unattended reality feedback loop."""
    from app.core.scheduler import scheduler

    return loop_status(scheduler_running=scheduler.running)


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
    _auth: None = Depends(require_write_key),
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
        "edge": analyze_edge_trajectory(probability_snapshots),
        "history": probability_snapshots,
    }


@router.post("/{event_id}/resolve")
async def resolve_event_intelligence(
    event_id: str,
    actual_outcome: float = Body(..., ge=0, le=100, embed=True),
    confidence: float = Body(default=1.0, ge=0, le=1, embed=True),
    notes: str = Body(default="", embed=True),
    _auth: None = Depends(require_write_key),
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
    _auth: None = Depends(require_write_key),
):
    """Auto-resolve events whose questions match resolved prediction markets
    (Polymarket, Manifold, Kalshi).

    Fetches resolved markets from all sources, matches each unresolved local
    event by question similarity, and resolves each match with a calibration
    snapshot (source = "auto_market"). Returns a summary
    {status, resolved_count, checked_count, unresolved_events, matches}.
    """
    return await auto_resolve_events(resolved_limit=limit)


@router.get("/links/pending")
async def list_pending_links():
    """List event->market links awaiting human verification.

    These are fuzzy auto-matches recorded below the auto-verify threshold: they
    are NOT scored (fail-closed) until a human verifies them via
    POST /events/{event_id}/link/verify. Returns the review queue.
    """
    return {"pending": list_pending()}


@router.post("/{event_id}/link/verify")
async def verify_event_link(
    event_id: str,
    contract_id: str = Body(default="", embed=True),
    _auth: None = Depends(require_write_key),
):
    """Verify (promote) a pending event->market link so it becomes eligible to
    be scored. `contract_id` identifies which of the event's links to verify
    (the value shown in the pending queue). 404 if no such link exists.

    Verifying only marks the link trustworthy; the event is scored on the next
    auto-resolve (or a manual resolve), not here.
    """
    updated = set_verified(event_id, contract_id, True)
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail=f"No link for event '{event_id}' with contract '{contract_id}'",
        )
    return updated


@router.get("/predictions/calibration")
async def get_prediction_calibration():
    """Calibration scorecard over committed, point-in-time predictions: an overall
    block (mean Brier, grade, count, mean raw edge) plus `realized_edge` /
    `directional_hit_rate` (did our divergences from the market move toward reality)
    and a per-category breakdown (`by_category`, the conditional calibration the
    Disagreement Diagnosis trust-weights with). Empty (no_data) until predictions resolve.
    """
    return calibration_summary()


@router.get("/predictions/recent")
async def get_recent_predictions(limit: int = Query(default=50, ge=1, le=200)):
    """Recent frozen predictions (AI vs market price, raw edge, and - once
    resolved - the scored Brier). Visibility into what the loop has committed."""
    return {"predictions": list_recent(limit=limit)}


@router.get("/decisions/open")
async def get_open_decisions(
    limit: int = Query(default=50, ge=1, le=200),
    decision: str | None = Query(default=None, pattern="^(act|watch)$"),
):
    """Open opportunities: unresolved committed predictions worth a human's
    attention, ranked by absolute adjusted edge, each rendered as a decision
    report (event / probability / market view / edge / confidence / recommendation
    / risk). Defaults to act + watch; pass `decision=act` to narrow (an invalid
    value is rejected with 422 rather than silently returning nothing). While
    every category is dormant this surfaces watch-grade items (or is empty).
    """
    decisions = (decision,) if decision else ("act", "watch")
    reports = []
    for prediction in list_open_opportunities(decisions=decisions, limit=limit):
        entry = get_event(prediction["event_id"])
        record = entry.get("record") if entry else None
        reports.append(build_decision_report(prediction, record))
    return {"count": len(reports), "decisions": reports}


@router.get("/edges/fresh")
async def get_fresh_edges(limit: int = Query(default=10, ge=1, le=50)):
    """Events with a live (fresh) edge: a material AI-vs-market divergence that is
    recent and holding near its peak, ranked by edge size. The 'catch edges when
    real' surface - it deliberately excludes decayed or stale edges.
    """
    edges = rank_fresh_edges(histories_by_event(), limit=limit)
    return {"count": len(edges), "edges": edges}


@router.get("/{event_id}/decision")
async def get_event_decision(event_id: str):
    """The decision report for one event: its committed prediction joined with the
    event intelligence record (event / probability / market view / edge+trust /
    confidence / recommendation / risk). 404 when the event has no committed
    prediction (e.g. a non-market event, which has no edge to act on).
    """
    prediction = get_prediction(event_id)
    if prediction is None:
        raise HTTPException(
            status_code=404,
            detail=f"No committed prediction for event '{event_id}'",
        )
    entry = get_event(event_id)
    record = entry.get("record") if entry else None
    return build_decision_report(prediction, record)


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
