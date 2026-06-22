from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query

from app.api.security import is_write_key_valid, require_write_key
from app.memory.event_market_link_store import list_pending, set_verified
from app.memory.prediction_store import (
    calibration_summary,
    get_prediction,
    list_open_opportunities,
    list_recent,
)
from app.memory.event_store import (
    count_events,
    get_event,
    list_all_events,
    list_events,
    list_resolved_events,
    set_tracking,
)
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
from app.services.sports_fact_service import (
    WORLD_CUP_TOURNAMENT,
    import_sports_facts,
    load_sports_facts,
    sports_fact_status,
)
from app.services.sports_resolution_service import resolve_world_cup_events
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    world_cup_data_to_facts,
)
from app.services.trend_analysis_service import (
    analyze_edge_trajectory,
    analyze_trend,
    list_edge_trajectories,
    rank_fresh_edges,
    rank_movers,
)
from app.models.event import (
    AutoResolveResponse,
    EventAnalysisRequest,
    EventDiscoveryResponse,
    EventHistoryResponse,
    EventListResponse,
    EventMoversResponse,
    EventStoreEntry,
    FlexibleResponse,
    FreshEdgesResponse,
    OpenDecisionsResponse,
    PendingLinksResponse,
    RecentPredictionsResponse,
    SimilarEventsResponse,
)


router = APIRouter()

EVENT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
EventId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=EVENT_ID_PATTERN,
        description="Stored event id.",
    ),
]


@router.get("/discover", response_model=EventDiscoveryResponse)
async def discover_event_intelligence(
    limit: int = Query(default=10, ge=1, le=20),
    use_cache: bool = Query(default=True),
    _auth: None = Depends(require_write_key),
):
    """Discover high-value events and return intelligence records."""
    return await discover_events(limit=limit, use_cache=use_cache)


@router.post("/analyze", response_model=FlexibleResponse)
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


@router.get("/", response_model=EventListResponse)
async def list_event_intelligence(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default="", max_length=200),
    status: str = Query(default="all", pattern="^(active|tracking|watching|archived|all)$"),
    category: str = Query(default="all", max_length=80),
    sort: str = Query(default="value", pattern="^(value|delta|probability|support)$"),
):
    """List stored event intelligence records for the dashboard table."""
    entries = list_events(
        limit=limit,
        offset=offset,
        query=q,
        status=status,
        category=category,
        sort=sort,
    )
    total = count_events(query=q, status=status, category=category, sort=sort)
    return {"count": len(entries), "total": total, "limit": limit, "offset": offset, "events": entries}


@router.get("/movers", response_model=EventMoversResponse)
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


@router.get("/calibration", response_model=FlexibleResponse)
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


@router.get("/loop/status", response_model=FlexibleResponse)
async def get_loop_status(x_api_key: str | None = Header(default=None)):
    """Operational status for the unattended reality feedback loop."""
    from app.core.scheduler import scheduler

    return loop_status(
        scheduler_running=scheduler.running,
        include_run_details=is_write_key_valid(x_api_key),
    )


@router.get("/sports/world-cup/status", response_model=FlexibleResponse)
async def get_world_cup_status():
    """Return World Cup fact-store status for the sports vertical."""
    return sports_fact_status(tournament=WORLD_CUP_TOURNAMENT)


@router.get("/sports/world-cup/facts", response_model=FlexibleResponse)
async def list_world_cup_facts(
    kind: str | None = Query(default=None, max_length=40),
    team: str | None = Query(default=None, max_length=80),
):
    """List imported structured World Cup facts."""
    facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind=kind)
    if team:
        needle = team.strip().lower()
        facts = [
            fact for fact in facts
            if needle in str(fact.get("team", "")).lower()
            or needle in str(fact.get("home_team", "")).lower()
            or needle in str(fact.get("away_team", "")).lower()
        ]
    return {"count": len(facts), "facts": facts}


@router.post("/sports/world-cup/facts/import", response_model=FlexibleResponse)
async def import_world_cup_facts(
    payload: Any = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import structured World Cup facts from a list or {"facts": [...]} body."""
    try:
        result = import_sports_facts(
            payload,
            replace=replace,
            default_tournament=WORLD_CUP_TOURNAMENT,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result["imported"] == 0 and result["error_count"] > 0:
        raise HTTPException(status_code=422, detail=result["errors"])
    return result


@router.post("/sports/world-cup/data/import", response_model=FlexibleResponse)
async def import_world_cup_data_source(
    payload: Any = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Convert trusted World Cup data-source payloads into facts and import them."""
    try:
        result = import_world_cup_data(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result["imported"] == 0 and result["error_count"] > 0:
        raise HTTPException(status_code=422, detail=result["errors"])
    return result


@router.post("/sports/world-cup/data/preview", response_model=FlexibleResponse)
async def preview_world_cup_data_source(
    payload: Any = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview facts that would be produced from a trusted World Cup payload."""
    try:
        facts = world_cup_data_to_facts(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"converted_fact_count": len(facts), "facts": facts}


@router.post("/sports/world-cup/resolve", response_model=FlexibleResponse)
async def resolve_world_cup_sports_events(
    dry_run: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=1000),
    _auth: None = Depends(require_write_key),
):
    """Resolve World Cup sports events from structured facts.

    Defaults to dry-run so operators can inspect deterministic matches before
    writing outcomes.
    """
    return await resolve_world_cup_events(dry_run=dry_run, limit=limit)


# Dynamic routes declared after the static /discover, /analyze, / and /movers
# routes so the path parameter does not shadow them.
@router.get("/{event_id}", response_model=EventStoreEntry)
async def get_event_intelligence(event_id: EventId):
    """Return a stored event intelligence record by event_id (404 if unknown)."""
    entry = get_event(event_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return entry


@router.patch("/{event_id}/tracking", response_model=EventStoreEntry)
async def update_event_tracking(
    event_id: EventId,
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


@router.get("/{event_id}/history", response_model=EventHistoryResponse)
async def get_event_probability_history(event_id: EventId):
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


@router.post("/{event_id}/resolve", response_model=EventStoreEntry)
async def resolve_event_intelligence(
    event_id: EventId,
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


@router.post(
    "/resolve/auto",
    response_model=AutoResolveResponse,
    response_model_exclude_unset=True,
)
async def auto_resolve_event_intelligence(
    limit: int = Query(default=200, ge=1, le=1000),
    dry_run: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Auto-resolve events whose questions match resolved prediction markets
    (Polymarket, Manifold, Kalshi).

    Fetches resolved markets from all sources, matches each unresolved local
    event by question similarity, and resolves each match with a calibration
    snapshot (source = "auto_market"). With dry_run=true it only returns the
    candidate matches and writes nothing. Returns a summary
    {status, dry_run, resolved_count, checked_count, unresolved_events, matches}.
    """
    return await auto_resolve_events(resolved_limit=limit, dry_run=dry_run)


@router.get("/links/pending", response_model=PendingLinksResponse)
async def list_pending_links():
    """List event->market links awaiting human verification.

    These are fuzzy auto-matches recorded below the auto-verify threshold: they
    are NOT scored (fail-closed) until a human verifies them via
    POST /events/{event_id}/link/verify. Returns the review queue.
    """
    pending = []
    for link in list_pending():
        entry = get_event(link.get("event_id", ""))
        record = (entry or {}).get("record") or {}
        semantics = record.get("semantics") or {}
        pending.append({
            **link,
            "event_title": record.get("event_title", ""),
            "event_title_zh": record.get("event_title_zh", ""),
            "event_summary": record.get("event_summary", ""),
            "event_resolution_criteria": semantics.get("resolution_criteria", ""),
        })
    return {"pending": pending}


@router.post("/{event_id}/link/verify", response_model=FlexibleResponse)
async def verify_event_link(
    event_id: EventId,
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


@router.get("/predictions/calibration", response_model=FlexibleResponse)
async def get_prediction_calibration():
    """Calibration scorecard over committed, point-in-time predictions: an overall
    block (mean Brier, grade, count, mean raw edge) plus `realized_edge` /
    `directional_hit_rate` (did our divergences from the market move toward reality)
    and a per-category breakdown (`by_category`, the conditional calibration the
    Disagreement Diagnosis trust-weights with). Empty (no_data) until predictions resolve.
    """
    return calibration_summary()


@router.get("/predictions/recent", response_model=RecentPredictionsResponse)
async def get_recent_predictions(limit: int = Query(default=50, ge=1, le=200)):
    """Recent frozen predictions (AI vs market price, raw edge, and - once
    resolved - the scored Brier). Visibility into what the loop has committed."""
    events_by_id = {entry.get("event_id"): entry for entry in list_all_events()}
    predictions = []
    for prediction in list_recent(limit=limit):
        entry = events_by_id.get(prediction.get("event_id"))
        record = entry.get("record") if entry else None
        predictions.append({
            **prediction,
            "event_title": (record or {}).get("event_title", ""),
            "event_title_zh": (record or {}).get("event_title_zh", ""),
        })
    return {"predictions": predictions}


@router.get("/decisions/open", response_model=OpenDecisionsResponse)
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
    events_by_id = {entry.get("event_id"): entry for entry in list_all_events()}
    for prediction in list_open_opportunities(decisions=decisions, limit=limit):
        entry = events_by_id.get(prediction["event_id"])
        record = entry.get("record") if entry else None
        reports.append(build_decision_report(prediction, record))
    return {"count": len(reports), "decisions": reports}


@router.get(
    "/edges/fresh",
    response_model=FreshEdgesResponse,
    response_model_exclude_unset=True,
)
async def get_fresh_edges(
    limit: int = Query(default=10, ge=1, le=50),
    classification: str = Query(
        default="fresh",
        pattern="^(all|fresh|decaying|stale|closed|no_data)$",
    ),
    include_series: bool = Query(default=False),
):
    """Events with a live (fresh) edge: a material AI-vs-market divergence that is
    recent and holding near its peak, ranked by edge size. Pass
    classification=all for the monitoring view that groups fresh/decaying/stale/
    closed edges; default behavior deliberately excludes decayed or stale edges.
    """
    histories = histories_by_event()
    if classification == "fresh" and not include_series:
        edges = rank_fresh_edges(histories, limit=limit)
    else:
        edges = list_edge_trajectories(
            histories,
            limit=limit,
            classification=classification,
            include_series=include_series,
        )
    body = {"count": len(edges), "edges": edges}
    if classification != "fresh" or include_series:
        body["classification"] = classification
    return body


@router.get("/{event_id}/decision", response_model=FlexibleResponse)
async def get_event_decision(event_id: EventId):
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


@router.get("/{event_id}/similar", response_model=SimilarEventsResponse)
async def get_similar_events(
    event_id: EventId,
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
