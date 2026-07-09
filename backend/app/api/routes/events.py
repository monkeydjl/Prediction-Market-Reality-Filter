from typing import Annotated, Any
import logging

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query

# ---------------------------------------------------------------------------
# Request body types
#
# Previously these endpoints used ``payload: Any = Body(...)`` which accepts
# any JSON value (string, number, null, bool, array, object).  Switching to
# ``dict[str, Any]`` ensures the payload is a JSON object and returns a 422
# error for malformed requests at the API boundary instead of deep in the
# service layer.  The facts-import endpoint accepts either a list of fact
# objects or a ``{"facts": [...]}`` wrapper, so it uses a union type.
# ---------------------------------------------------------------------------

#: Body type for endpoints that accept a single JSON object payload.
DictPayload = dict[str, Any]

#: Body type for the facts-import endpoint (list of facts or {"facts": [...]}).
FactsPayload = list[dict[str, Any]] | dict[str, Any]

from app.api.security import is_write_key_valid, require_write_key
from app.memory import loop_run_store
from app.memory.event_market_link_store import list_pending, set_verified
from app.memory.prediction_store import (
    calibration_summary,
    count_open_opportunities,
    count_predictions,
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
from app.services.world_cup_api_football_source import (
    import_world_cup_api_football_bundle,
    preview_world_cup_api_football_bundle,
    test_world_cup_api_football_connection,
    validate_world_cup_api_football_pipeline,
)
from app.services.football_data_source import (
    FootballDataAPIError,
    import_world_cup_football_data_standings,
    preview_world_cup_football_data_standings,
)
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    import_world_cup_data_file,
    preview_world_cup_data_file,
    world_cup_data_to_facts,
)
from app.services.world_cup_data_source_status_service import (
    world_cup_data_source_status,
)
from app.services.world_cup_match_source import (
    import_world_cup_match_source,
    preview_world_cup_match_source,
)
from app.services.world_cup_match_events_source import (
    import_world_cup_match_events_source,
    preview_world_cup_match_events_source,
)
from app.services.world_cup_lineups_source import (
    import_world_cup_lineups_source,
    preview_world_cup_lineups_source,
)
from app.services.world_cup_official_csv_source import (
    import_world_cup_official_csv_source,
    preview_world_cup_official_csv_source,
)
from app.services.world_cup_player_awards_source import (
    import_world_cup_player_awards_source,
    preview_world_cup_player_awards_source,
)
from app.services.world_cup_player_status_source import (
    import_world_cup_player_status_source,
    preview_world_cup_player_status_source,
)
from app.services.world_cup_source_bundle import (
    import_world_cup_source_bundle,
    import_world_cup_source_bundle_feeds,
    import_world_cup_source_bundle_file,
    import_world_cup_source_bundle_url,
    preview_world_cup_source_bundle,
    preview_world_cup_source_bundle_feeds,
    preview_world_cup_source_bundle_file,
    preview_world_cup_source_bundle_url,
)
from app.services.world_cup_sportmonks_source import (
    import_world_cup_sportmonks_bundle,
    preview_world_cup_sportmonks_bundle,
    test_world_cup_sportmonks_connection,
    validate_world_cup_sportmonks_pipeline,
)
from app.services.world_cup_standings_source import (
    import_world_cup_standings_source,
    preview_world_cup_standings_source,
)
from app.services.world_cup_statistics_source import (
    import_world_cup_statistics_source,
    preview_world_cup_statistics_source,
)
from app.services.trend_analysis_service import (
    analyze_edge_trajectory,
    analyze_trend,
    count_edge_trajectories,
    edge_class_counts,
    list_edge_trajectories,
    rank_fresh_edges,
    rank_movers,
)
from app.models.event import (
    AutoResolveResponse,
    DecisionTimelineDiff,
    DecisionTimelineResponse,
    DecisionTimelineSnapshot,
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


logger = logging.getLogger(__name__)

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
    limit: int = Query(default=10, ge=1, le=50),
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


@router.post("/reset", response_model=FlexibleResponse)
async def reset_all_event_data(_auth: None = Depends(require_write_key)):
    """Delete all event data: store, predictions, audit log, cache.

    Returns a summary of what was cleared.  Requires write-key auth."""
    import json
    import os
    import sqlite3

    from app.core.config import settings
    from app.utils.sqlite_db import loop_db_path

    cleared: dict[str, int | str] = {}

    # 1. event_store.json
    store_path = os.path.abspath(settings.EVENT_STORE_FILE)
    with open(store_path, "w", encoding="utf-8") as f:
        f.write("{}")
    cleared["event_store"] = str(store_path)

    # 2. event_audit.jsonl
    audit_path = os.path.abspath(settings.EVENT_AUDIT_FILE)
    with open(audit_path, "w", encoding="utf-8") as f:
        f.write("")
    cleared["event_audit"] = str(audit_path)

    # 3. event_cache.json
    cache_path = os.path.abspath(settings.EVENT_CACHE_FILE)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write("{}")
    cleared["event_cache"] = str(cache_path)

    # 4. v2_loop.db tables
    db_path = loop_db_path()
    conn = sqlite3.connect(db_path)
    tables = ("predictions", "loop_runs", "event_market_links", "decision_timeline")
    for table in tables:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.execute(f"DELETE FROM {table}")
            cleared[f"sqlite.{table}"] = count
        except sqlite3.OperationalError:
            # Table doesn't exist yet (e.g. decision_timeline is only created
            # when DECISION_TIMELINE_ENABLED is first used). Nothing to clear.
            cleared[f"sqlite.{table}"] = 0
    conn.commit()
    conn.close()

    # 5. Invalidate in-memory history cache so the next movers / edge
    #    request rebuilds from the (now empty) audit file instead of
    #    serving stale cached snapshots.
    try:
        from app.services.event_audit_service import invalidate_history_cache
        invalidate_history_cache()
        cleared["history_cache"] = "invalidated"
    except ImportError:
        cleared["history_cache"] = "skipped"

    logger.info(
        "Event data reset: store=%s audit=%s cache=%s predictions=%d runs=%d links=%d",
        cleared.get("event_store"),
        cleared.get("event_audit"),
        cleared.get("event_cache"),
        cleared.get("sqlite.predictions", 0),
        cleared.get("sqlite.loop_runs", 0),
        cleared.get("sqlite.event_market_links", 0),
    )
    return {"message": "All event data cleared.", "cleared": cleared}



@router.get("/", response_model=EventListResponse)
async def list_event_intelligence(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default="", max_length=200),
    status: str = Query(default="all", pattern="^(active|tracking|watching|archived|all)$"),
    category: str = Query(default="all", max_length=80),
    sort: str = Query(default="value", pattern="^(value|delta|probability|support)$"),
    exclude_expired: bool = Query(default=True),
    resolved_only: bool = Query(default=False),
):
    """List stored event intelligence records for the dashboard table."""
    entries = list_events(
        limit=limit,
        offset=offset,
        query=q,
        status=status,
        category=category,
        sort=sort,
        exclude_expired=exclude_expired,
        resolved_only=resolved_only,
    )
    total = count_events(
        query=q,
        status=status,
        category=category,
        sort=sort,
        exclude_expired=exclude_expired,
        resolved_only=resolved_only,
    )
    return {"count": len(entries), "total": total, "limit": limit, "offset": offset, "events": entries}


@router.get("/movers", response_model=EventMoversResponse)
async def get_event_movers(limit: int = Query(default=10, ge=1, le=50)):
    """Rank tracked events by how much their probability has moved over time.

    Movers carry the English event_title from the audit snapshots; enrich each
    with the stored event_title_zh so the dashboard can show Chinese titles.
    """
    movers = rank_movers(histories_by_event(), limit=limit)
    # Batch-load events once to avoid N+1 reads
    events_by_id = {e.get("event_id"): e for e in list_all_events()}
    kept = []
    for mover in movers:
        entry = events_by_id.get(mover.get("event_id", ""))
        if not entry:
            # Event was deleted; drop stale mover history.
            continue
        title_zh = ((entry or {}).get("record") or {}).get("event_title_zh") or ""
        if title_zh:
            mover["event_title_zh"] = title_zh
        kept.append(mover)
    return {"count": len(kept), "movers": kept}


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


@router.get("/sports/world-cup/data/sources/status", response_model=FlexibleResponse)
async def get_world_cup_data_source_status(
    _auth: None = Depends(require_write_key),
):
    """Return configured World Cup data-source and import-run status."""
    return world_cup_data_source_status()


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
    payload: FactsPayload = Body(...),
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
    payload: DictPayload = Body(...),
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
    payload: DictPayload = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview facts that would be produced from a trusted World Cup payload."""
    try:
        facts = world_cup_data_to_facts(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"converted_fact_count": len(facts), "facts": facts}


@router.post("/sports/world-cup/data/bundle/preview", response_model=FlexibleResponse)
async def preview_world_cup_source_bundle_route(
    payload: DictPayload = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview facts from a bundle of World Cup data-source payloads."""
    try:
        return preview_world_cup_source_bundle(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/import", response_model=FlexibleResponse)
async def import_world_cup_source_bundle_route(
    payload: DictPayload = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import facts from a bundle of World Cup data-source payloads."""
    try:
        return import_world_cup_source_bundle(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/source/preview", response_model=FlexibleResponse)
async def preview_configured_world_cup_source_bundle_route(
    _auth: None = Depends(require_write_key),
):
    """Preview facts from the configured WORLD_CUP_SOURCE_BUNDLE_FILE."""
    try:
        return preview_world_cup_source_bundle_file()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"World Cup source bundle file not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/source/import", response_model=FlexibleResponse)
async def import_configured_world_cup_source_bundle_route(
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import facts from the configured WORLD_CUP_SOURCE_BUNDLE_FILE."""
    try:
        return import_world_cup_source_bundle_file(replace=replace)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"World Cup source bundle file not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/url/preview", response_model=FlexibleResponse)
async def preview_remote_world_cup_source_bundle_route(
    _auth: None = Depends(require_write_key),
):
    """Preview facts from the configured WORLD_CUP_SOURCE_BUNDLE_URL."""
    try:
        return preview_world_cup_source_bundle_url()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/url/import", response_model=FlexibleResponse)
async def import_remote_world_cup_source_bundle_route(
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import facts from the configured WORLD_CUP_SOURCE_BUNDLE_URL."""
    try:
        return import_world_cup_source_bundle_url(replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/feeds/preview", response_model=FlexibleResponse)
async def preview_configured_world_cup_source_feeds_route(
    _auth: None = Depends(require_write_key),
):
    """Preview facts from configured raw World Cup source feed URLs."""
    try:
        return preview_world_cup_source_bundle_feeds()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/feeds/import", response_model=FlexibleResponse)
async def import_configured_world_cup_source_feeds_route(
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import facts from configured raw World Cup source feed URLs."""
    try:
        return import_world_cup_source_bundle_feeds(replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/api-football/preview", response_model=FlexibleResponse)
async def preview_api_football_world_cup_source_bundle_route(
    _auth: None = Depends(require_write_key),
):
    """Preview facts from configured API-Football World Cup feeds."""
    try:
        return preview_world_cup_api_football_bundle()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/api-football/import", response_model=FlexibleResponse)
async def import_api_football_world_cup_source_bundle_route(
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import facts from configured API-Football World Cup feeds."""
    _require_successful_api_football_validation_before_import()
    try:
        return import_world_cup_api_football_bundle(replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _require_successful_api_football_validation_before_import() -> None:
    run = loop_run_store.last_run("world_cup_api_football_validate")
    if not isinstance(run, dict):
        raise HTTPException(
            status_code=409,
            detail="Run API-Football pipeline validation before import.",
        )
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    if run.get("status") != "success" or result.get("ok") is not True:
        error = str(run.get("error") or result.get("error") or "").strip()
        suffix = f": {error}" if error else "."
        raise HTTPException(
            status_code=409,
            detail=f"Latest API-Football pipeline validation failed{suffix}",
        )


@router.post("/sports/world-cup/data/bundle/api-football/test", response_model=FlexibleResponse)
async def test_api_football_connection_route(
    _auth: None = Depends(require_write_key),
):
    """Test API-Football connectivity and return account status."""
    return test_world_cup_api_football_connection()


@router.post("/sports/world-cup/data/bundle/api-football/validate", response_model=FlexibleResponse)
async def validate_api_football_pipeline_route(
    _auth: None = Depends(require_write_key),
):
    """Validate pipeline: connection + sample fetch + compare with stored facts."""
    run_id = loop_run_store.start_run("world_cup_api_football_validate")
    try:
        result = validate_world_cup_api_football_pipeline()
    except Exception as exc:
        loop_run_store.finish_run(run_id, "failed", error=str(exc))
        raise

    summary = _api_football_validation_run_summary(result)
    status = "success" if result.get("ok") else "failed"
    loop_run_store.finish_run(
        run_id,
        status,
        result=summary,
        error=str(result.get("error") or "") if status == "failed" else None,
    )
    return result


def _api_football_validation_run_summary(result: dict[str, Any]) -> dict[str, Any]:
    steps = result.get("steps") if isinstance(result.get("steps"), list) else []
    failed_step = next(
        (
            step.get("name")
            for step in steps
            if isinstance(step, dict) and step.get("ok") is False
        ),
        None,
    )
    fixture_step = next(
        (
            step
            for step in steps
            if isinstance(step, dict) and step.get("name") == "fixture_fetch"
        ),
        {},
    )
    coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else {}
    return {
        "provider": "api_football",
        "ok": bool(result.get("ok")),
        "error": str(result.get("error") or "")[:500],
        "step_count": len(steps),
        "failed_step": failed_step,
        "fixture_count": fixture_step.get("fixture_count"),
        "covered": coverage.get("covered"),
        "missing_from_store": coverage.get("missing_from_store"),
    }


@router.post("/sports/world-cup/data/bundle/football-data/preview", response_model=FlexibleResponse)
async def preview_football_data_world_cup_standings_route(
    _auth: None = Depends(require_write_key),
):
    """Preview qualification facts from configured Football-Data.org standings."""
    try:
        return preview_world_cup_football_data_standings()
    except FootballDataAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/football-data/import", response_model=FlexibleResponse)
async def import_football_data_world_cup_standings_route(
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import qualification facts from configured Football-Data.org standings."""
    try:
        return import_world_cup_football_data_standings(replace=replace)
    except FootballDataAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/sportmonks/preview", response_model=FlexibleResponse)
async def preview_sportmonks_world_cup_source_bundle_route(
    _auth: None = Depends(require_write_key),
):
    """Preview facts from configured Sportmonks-style World Cup feeds."""
    try:
        return preview_world_cup_sportmonks_bundle()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/sportmonks/import", response_model=FlexibleResponse)
async def import_sportmonks_world_cup_source_bundle_route(
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import facts from configured Sportmonks-style World Cup feeds."""
    try:
        return import_world_cup_sportmonks_bundle(replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/bundle/sportmonks/test", response_model=FlexibleResponse)
async def test_sportmonks_connection_route(
    _auth: None = Depends(require_write_key),
):
    """Test Sportmonks connectivity and return connection status."""
    return test_world_cup_sportmonks_connection()


@router.post("/sports/world-cup/data/bundle/sportmonks/validate", response_model=FlexibleResponse)
async def validate_sportmonks_pipeline_route(
    _auth: None = Depends(require_write_key),
):
    """Run Sportmonks pipeline diagnostic: connection + feed fetch + fact coverage."""
    return validate_world_cup_sportmonks_pipeline()


@router.post("/sports/world-cup/data/source/preview", response_model=FlexibleResponse)
async def preview_configured_world_cup_data_source(
    _auth: None = Depends(require_write_key),
):
    """Preview facts from the configured WORLD_CUP_DATA_FILE without writing."""
    try:
        return preview_world_cup_data_file()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"World Cup data file not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/data/source/import", response_model=FlexibleResponse)
async def import_configured_world_cup_data_source(
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import facts from the configured WORLD_CUP_DATA_FILE."""
    try:
        return import_world_cup_data_file(replace=replace)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"World Cup data file not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/official-csv/preview", response_model=FlexibleResponse)
async def preview_world_cup_official_csv_source_route(
    payload: DictPayload = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview facts from the strict official World Cup CSV profile."""
    try:
        return preview_world_cup_official_csv_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/official-csv/import", response_model=FlexibleResponse)
async def import_world_cup_official_csv_source_route(
    payload: DictPayload = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import facts from the strict official World Cup CSV profile."""
    try:
        return import_world_cup_official_csv_source(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/matches/preview", response_model=FlexibleResponse)
async def preview_world_cup_match_source_route(
    payload: DictPayload = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview facts from a raw World Cup fixture/result payload."""
    try:
        return preview_world_cup_match_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/matches/import", response_model=FlexibleResponse)
async def import_world_cup_match_source_route(
    payload: DictPayload = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import facts from a raw World Cup fixture/result payload."""
    try:
        return import_world_cup_match_source(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/match-events/preview", response_model=FlexibleResponse)
async def preview_world_cup_match_events_source_route(
    payload: DictPayload = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview discipline facts from raw World Cup match event/card data."""
    try:
        return preview_world_cup_match_events_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/match-events/import", response_model=FlexibleResponse)
async def import_world_cup_match_events_source_route(
    payload: DictPayload = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import discipline facts from raw World Cup match event/card data."""
    try:
        return import_world_cup_match_events_source(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/lineups/preview", response_model=FlexibleResponse)
async def preview_world_cup_lineups_source_route(
    payload: DictPayload = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview lineup facts from raw World Cup lineup data."""
    try:
        return preview_world_cup_lineups_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/lineups/import", response_model=FlexibleResponse)
async def import_world_cup_lineups_source_route(
    payload: DictPayload = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import lineup facts from raw World Cup lineup data."""
    try:
        return import_world_cup_lineups_source(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/standings/preview", response_model=FlexibleResponse)
async def preview_world_cup_standings_source_route(
    payload: DictPayload = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview qualification facts from raw World Cup standings data."""
    try:
        return preview_world_cup_standings_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/standings/import", response_model=FlexibleResponse)
async def import_world_cup_standings_source_route(
    payload: DictPayload = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import qualification facts from raw World Cup standings data."""
    try:
        return import_world_cup_standings_source(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/player-awards/preview", response_model=FlexibleResponse)
async def preview_world_cup_player_awards_source_route(
    payload: DictPayload = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview player-award facts from raw World Cup award/scorer data."""
    try:
        return preview_world_cup_player_awards_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/player-awards/import", response_model=FlexibleResponse)
async def import_world_cup_player_awards_source_route(
    payload: DictPayload = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import player-award facts from raw World Cup award/scorer data."""
    try:
        return import_world_cup_player_awards_source(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/player-status/preview", response_model=FlexibleResponse)
async def preview_world_cup_player_status_source_route(
    payload: DictPayload = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview injury/availability/suspension/lineup facts from raw player-status data."""
    try:
        return preview_world_cup_player_status_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/player-status/import", response_model=FlexibleResponse)
async def import_world_cup_player_status_source_route(
    payload: DictPayload = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import injury/availability/suspension/lineup facts from raw player-status data."""
    try:
        return import_world_cup_player_status_source(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/statistics/preview", response_model=FlexibleResponse)
async def preview_world_cup_statistics_source_route(
    payload: DictPayload = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Preview team/player statistic facts from raw World Cup statistics data."""
    try:
        return preview_world_cup_statistics_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sports/world-cup/statistics/import", response_model=FlexibleResponse)
async def import_world_cup_statistics_source_route(
    payload: DictPayload = Body(...),
    replace: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Import team/player statistic facts from raw World Cup statistics data."""
    try:
        return import_world_cup_statistics_source(payload, replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
@router.post("/batch-sparklines", response_model=FlexibleResponse)
async def batch_sparklines(
    body: dict = Body(...),
    _auth: None = Depends(require_write_key),
):
    """Return compact sparkline series for multiple events in one request.

    Accepts ``{"event_ids": ["abc", "def", ...]}`` and returns
    ``{"sparklines": {"abc": [45.2, 47.1, ...], "def": []}}``.
    Each series is just the estimated probability values (no metadata),
    minimising payload for the frontend sparkline chart.  Reads the audit
    log once via ``histories_by_event()`` instead of N individual reads.
    Capped at 200 event IDs.
    """
    event_ids = body.get("event_ids") or []
    if not isinstance(event_ids, list):
        raise HTTPException(status_code=422, detail="event_ids must be a list")
    event_ids = [str(eid) for eid in event_ids[:200]]

    all_histories = histories_by_event()
    sparklines: dict[str, list[float]] = {}
    for eid in event_ids:
        snaps = all_histories.get(eid, [])
        series = [
            float(snap["estimated"])
            for snap in snaps
            if snap.get("kind") != "outcome"
            and snap.get("estimated") is not None
            and isinstance(snap.get("estimated"), (int, float))
        ]
        sparklines[eid] = series
    return {"sparklines": sparklines}


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


@router.get("/{event_id}/decision-timeline", response_model=DecisionTimelineResponse)
async def get_event_decision_timeline(
    event_id: EventId,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Return the decision timeline snapshots + consecutive diffs for an event.

    Each snapshot captures the overlay-bearing record at one save_events
    call. The diffs array has len(snapshots) - 1 entries; diffs[i] is the
    diff between snapshots[i] and snapshots[i+1].

    Returns count=0 / empty lists when the event exists but has no
    snapshots (e.g. DECISION_TIMELINE_ENABLED was off when it was saved).
    404 when the event_id is unknown.
    """
    from app.memory import event_store
    entry = event_store.get_event(event_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    from app.memory import decision_timeline_store
    from app.services.decision_diff_service import build_decision_diff
    total = decision_timeline_store.count_snapshots(event_id)
    snapshots = decision_timeline_store.list_snapshots(event_id, limit=limit)
    diffs: list[dict[str, Any]] = []
    for i in range(max(len(snapshots) - 1, 0)):
        diffs.append(build_decision_diff(snapshots[i], snapshots[i + 1]))
    return {
        "event_id": event_id,
        "count": total,
        "snapshots": snapshots,
        "diffs": diffs,
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
    updated = resolve_with_calibration(
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
    (Polymarket, Kalshi).

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
    # Batch-load events once to avoid N+1 reads
    events_by_id = {e.get("event_id"): e for e in list_all_events()}
    pending = []
    for link in list_pending():
        entry = events_by_id.get(link.get("event_id", ""))
        record = ((entry or {}).get("record") or {})
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
async def get_recent_predictions(
    limit: int = Query(default=10, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Recent frozen predictions (AI vs market price, raw edge, and - once
    resolved - the scored Brier). Visibility into what the loop has committed."""
    events_by_id = {entry.get("event_id"): entry for entry in list_all_events()}
    predictions = []
    for prediction in list_recent(limit=limit, offset=offset):
        entry = events_by_id.get(prediction.get("event_id"))
        record = entry.get("record") if entry else None
        predictions.append({
            **prediction,
            "event_title": (record or {}).get("event_title", ""),
            "event_title_zh": (record or {}).get("event_title_zh", ""),
        })
    return {
        "predictions": predictions,
        "count": len(predictions),
        "total": count_predictions(),
        "limit": limit,
        "offset": offset,
    }


@router.get("/decisions/open", response_model=OpenDecisionsResponse)
async def get_open_decisions(
    limit: int = Query(default=10, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    decision: str | None = Query(default=None, pattern="^(act|watch|provisional_act)$"),
):
    """Open opportunities: unresolved committed predictions worth a human's
    attention, ranked by absolute adjusted edge, each rendered as a decision
    report (event / probability / market view / edge / confidence / recommendation
    / risk). Defaults to act + watch + provisional_act; pass `decision=act` to
    narrow (an invalid value is rejected with 422 rather than silently returning
    nothing). While every category is dormant this surfaces watch-grade items
    and cold-start provisional_act items (or is empty).
    """
    decisions = (decision,) if decision else ("act", "watch", "provisional_act")
    reports = []
    events_by_id = {entry.get("event_id"): entry for entry in list_all_events()}
    for prediction in list_open_opportunities(decisions=decisions, limit=limit, offset=offset):
        entry = events_by_id.get(prediction["event_id"])
        record = entry.get("record") if entry else None
        reports.append(build_decision_report(prediction, record))
    decision_totals = {
        "act": count_open_opportunities(decisions=("act",)),
        "provisional_act": count_open_opportunities(decisions=("provisional_act",)),
        "watch": count_open_opportunities(decisions=("watch",)),
    }
    return {
        "count": len(reports),
        "total": count_open_opportunities(decisions=decisions),
        "limit": limit,
        "offset": offset,
        "decision_totals": decision_totals,
        "decisions": reports,
    }


@router.get(
    "/edges/fresh",
    response_model=FreshEdgesResponse,
    response_model_exclude_unset=True,
)
async def get_fresh_edges(
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
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
    if classification == "fresh" and not include_series and offset == 0:
        edges = rank_fresh_edges(histories, limit=limit)
    else:
        edges = list_edge_trajectories(
            histories,
            limit=limit,
            offset=offset,
            classification=classification,
            include_series=include_series,
        )
    # Enrich each edge with the stored Chinese title (history snapshots only
    # carry event_title, not event_title_zh).
    events_by_id = {e.get("event_id"): e for e in list_all_events()}
    for edge in edges:
        entry = events_by_id.get(edge.get("event_id", ""))
        title_zh = ((entry or {}).get("record") or {}).get("event_title_zh") or ""
        if title_zh:
            edge["event_title_zh"] = title_zh
    body = {
        "count": len(edges),
        "total": count_edge_trajectories(histories, classification=classification),
        "limit": limit,
        "offset": offset,
        "classification": classification,
        "classification_totals": edge_class_counts(histories),
        "edges": edges,
    }
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


# ── Simulated trades (paper trading) ─────────────────────────────────


@router.get("/trades/stats", response_model=FlexibleResponse)
async def get_trade_stats():
    """Return aggregate statistics for closed simulated trades."""
    from app.memory.simulated_trade_store import trade_stats
    return trade_stats()


@router.get("/trades/open", response_model=FlexibleResponse)
async def get_open_trades(
    limit: int = Query(default=10, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Return paginated open simulated trades."""
    from app.memory.simulated_trade_store import count_open_trades, list_open_trades
    trades = list_open_trades(limit=limit, offset=offset)
    return {
        "count": len(trades),
        "total": count_open_trades(),
        "limit": limit,
        "offset": offset,
        "trades": trades,
    }


@router.get("/trades/closed", response_model=FlexibleResponse)
async def get_closed_trades(
    limit: int = Query(default=10, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Return paginated recently closed simulated trades."""
    from app.memory.simulated_trade_store import count_closed_trades, list_closed_trades
    trades = list_closed_trades(limit=limit, offset=offset)
    return {
        "count": len(trades),
        "total": count_closed_trades(),
        "limit": limit,
        "offset": offset,
        "trades": trades,
    }


@router.post("/trades/{event_id}/close", response_model=FlexibleResponse)
async def manual_close_trade(
    event_id: str,
    exit_prob: float = Body(default=0.0, embed=True),
    exit_reason: str = Body(default="manual", embed=True),
    _auth: None = Depends(require_write_key),
):
    """Manually close a simulated trade (中途离场模拟)."""
    from app.memory.simulated_trade_store import close_trade
    result = close_trade(
        event_id,
        actual_outcome=exit_prob,
        exit_prob=exit_prob,
        exit_reason=exit_reason or "manual",
    )
    if result is None:
        raise HTTPException(status_code=404, detail="No open trade for this event")
    return result


# ── Discovery progress ─────────────────────────────────────────────


@router.get("/discover/status", response_model=FlexibleResponse)
async def get_discovery_status():
    """Real-time progress of the current (or most recent) discovery run."""
    from app.services.discovery_status import snapshot
    return snapshot()


@router.delete("/{event_id}", response_model=FlexibleResponse)
async def delete_event(
    event_id: str,
    _auth: None = Depends(require_write_key),
):
    """Delete a single event from the store by its event_id."""
    from app.memory.event_store import _store_path, _load_for_write
    from app.utils.file_store import locked_file, write_json_atomic

    path = _store_path()
    with locked_file(path):
        store = _load_for_write(path)
        if event_id not in store:
            raise HTTPException(status_code=404, detail="Event not found")
        store.pop(event_id)
        write_json_atomic(path, store, indent=2)
    return {"event_id": event_id, "message": "Deleted"}


# ── Event title translation ────────────────────────────────────────


@router.post("/{event_id}/translate", response_model=FlexibleResponse)
async def translate_event_title(
    event_id: str,
    force: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Translate a single event's title to Chinese.

    Reads the English title from the event store, calls the LLM for a concise
    Chinese translation, and writes it back.  Use ``force=true`` to re-translate
    even when a Chinese title already exists.
    """
    from app.memory.event_store import get_event, save_events
    from app.services.probability_engine_service import translate_title

    event = get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    record = event.get("record") or event
    en_title = str(record.get("event_title") or "").strip()
    if not en_title:
        raise HTTPException(status_code=400, detail="Event has no English title")

    current_zh = str(record.get("event_title_zh") or "").strip()
    if current_zh and not force:
        return {
            "event_id": event_id,
            "event_title_zh": current_zh,
            "message": "Already translated",
        }

    zh_title = await translate_title(en_title)
    if not zh_title or zh_title == en_title:
        # Translation failed — if forcing, clear the stale title so UI
        # shows English instead of a misleading copy.
        if force and current_zh == en_title:
            record["event_title_zh"] = ""
            save_events([record])
        return {
            "event_id": event_id,
            "event_title_zh": en_title[:120],
            "message": "Translation unavailable, kept original",
        }

    record["event_title_zh"] = zh_title[:300]
    save_events([record])
    return {"event_id": event_id, "event_title_zh": zh_title[:300], "message": "Translated"}


@router.post("/translate-all", response_model=FlexibleResponse)
async def translate_all_events(
    force: bool = Query(default=False),
    _auth: None = Depends(require_write_key),
):
    """Translate all events whose Chinese title is empty.

    Set ``force=true`` to re-translate every event even when a Chinese title
    already exists (useful for recovering from failed LLM translations)."""
    from app.memory.event_store import list_all_events, save_events
    from app.services.probability_engine_service import translate_title
    import asyncio

    events = list_all_events()
    translated = 0
    modified: list[dict[str, Any]] = []
    for event in events:
        record = event.get("record") or event
        zh = str(record.get("event_title_zh") or "").strip()
        if zh and not force:
            continue
        en = str(record.get("event_title") or "").strip()
        if not en:
            continue
        result = await translate_title(en)
        if result and result != en:
            record["event_title_zh"] = result[:300]
            translated += 1
        elif force and zh == en:
            # Translation failed — clear stale English copy so UI can fall back.
            record["event_title_zh"] = ""
        modified.append(record)
        await asyncio.sleep(0.2)  # Respect API rate limits

    if modified:
        save_events(modified)

    return {
        "total": len(events),
        "translated": translated,
        "message": f"Translated {translated} event titles",
    }


@router.post("/resolve-expired", response_model=FlexibleResponse)
async def resolve_expired_events(_auth: None = Depends(require_write_key)):
    """Archive unresolved events whose deadline is in the past.

    Uses source end_date/close_time when available; otherwise parses the most
    recent date from the event title. Expiry is not settlement: this endpoint
    must not write outcome/calibration or invent actual_outcome=50. It only
    marks unresolved expired events as archived so they leave the active
    dashboard while remaining unscored until the market actually resolves.
    """
    import re
    from datetime import datetime, timezone
    from app.memory.event_store import list_all_events, set_tracking

    now = datetime.now(timezone.utc)
    events = list_all_events()
    archived = 0
    parsed = 0

    # Chinese date patterns: 6月30日, 7月1日, 6月, 2026年6月30日
    # English date patterns: July 3, 2026, July 3, 2026-07-03, 2026/07/03
    title_date_patterns = [
        re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"),  # 2026年6月30日
        re.compile(r"(\d{1,2})月(\d{1,2})日"),            # 6月30日
        re.compile(r"(\d{1,2})月"),                       # 6月
        re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})"),
        re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
        re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})"),
    ]

    for event in events:
        record = event.get("record") or event
        outcome = record.get("outcome") or {}
        if outcome.get("status"):
            continue
        event_id = event.get("event_id") or record.get("event_id")
        if not event_id:
            continue
        tracking = record.get("tracking") or {}
        if tracking.get("status") == "archived":
            continue

        source = record.get("source") or {}
        close_str = str(source.get("close_time") or source.get("end_date") or "").strip()

        deadline: datetime | None = None
        if close_str:
            try:
                deadline = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                deadline = None
        else:
            # Parse dates from title
            titles = [
                record.get("event_title_zh", ""),
                record.get("event_title", ""),
            ]
            latest = None
            for text in titles:
                if not text:
                    continue
                text = str(text)
                for pat in title_date_patterns:
                    for m in pat.finditer(text):
                        groups = m.groups()
                        try:
                            if len(groups) == 3:
                                y, mo, d = int(groups[0]), int(groups[1]), int(groups[2])
                                if y < 100:
                                    y += 2000
                                dt = datetime(y, mo, d, tzinfo=timezone.utc)
                            elif len(groups) == 2:
                                mo, d = int(groups[0]), int(groups[1])
                                dt = datetime(2026, mo, d, tzinfo=timezone.utc)
                            elif len(groups) == 1:
                                mo = int(groups[0])
                                dt = datetime(2026, mo, 1, tzinfo=timezone.utc)
                            else:
                                continue
                            if latest is None or dt > latest:
                                latest = dt
                        except (ValueError, TypeError):
                            continue
            if latest is not None:
                deadline = latest
                parsed += 1

        if deadline is not None and deadline < now:
            if set_tracking(str(event_id), status="archived") is not None:
                archived += 1

    return {
        "total": len(events),
        # Backwards compatibility for older clients that only read `resolved`.
        "resolved": 0,
        "archived": archived,
        "parsed_dates": parsed,
        "message": f"Archived {archived} expired events without resolving outcomes",
    }
