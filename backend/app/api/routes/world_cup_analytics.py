"""API endpoints for prediction analytics and monitoring."""

import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Any

from app.api.audit_metadata import get_audit_metadata
from app.api.security import require_write_key
from app.memory import loop_run_store
from app.utils.prediction_db import get_prediction_session_dep
from app.models.world_cup_prediction import (
    MatchPrediction,
    MatchResult,
    PredictionHistory,
)
from app.services.odds_cache_service import OddsCache
from app.services.world_cup_quality_service import (
    apply_consistency_history_repair,
    build_consistency_repair_plan,
    build_quality_loop_report,
    bucket_engine,
    preview_consistency_history_repair,
)
from app.services.world_cup_result_consistency_service import (
    audit_world_cup_result_consistency,
)
from app.services.world_cup_result_fact_backfill_service import (
    list_world_cup_result_fact_backfill_runs,
    run_world_cup_result_fact_backfill,
)
from app.services.world_cup_scoring_service import (
    SCORING_RECONCILE_AUDIT_JOB_NAME,
    score_all_finished_matches,
)
from app.services.world_cup_post_match_backfill_service import (
    list_post_match_backfill_runs,
    run_post_match_backfill,
)


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/engine-stats")
async def get_engine_stats(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get prediction engine usage statistics."""

    # Count predictions by engine
    predictions = session.query(MatchPrediction).all()

    total = len(predictions)
    engine_keys = ("elo_odds", "hybrid", "integrated", "gbm")
    grouped: dict[str, list[MatchPrediction]] = {engine: [] for engine in engine_keys}
    for prediction in predictions:
        grouped.setdefault(bucket_engine(prediction.prediction_method), []).append(prediction)

    def engine_summary(engine: str) -> dict[str, Any]:
        engine_predictions = grouped[engine]
        count = len(engine_predictions)
        avg_confidence = (
            sum(float(prediction.confidence or 0.0) for prediction in engine_predictions) / count
            if count else 0.0
        )
        return {
            "count": count,
            "percentage": (count / total * 100) if total > 0 else 0,
            "avg_confidence": round(avg_confidence, 3),
        }

    return {
        "total_predictions": total,
        "by_engine": {
            engine: engine_summary(engine)
            for engine in engine_keys
        }
    }


@router.get("/quality-loop")
async def get_quality_loop(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get prediction quality metrics and confidence calibration buckets."""
    return build_quality_loop_report(session=session)


@router.get("/consistency-repair-plan")
async def get_consistency_repair_plan(
    limit: int = Query(25, ge=1, le=100, description="Maximum consistency issues to inspect"),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Get a dry-run repair plan for prediction history consistency issues."""
    return build_consistency_repair_plan(session=session, limit=limit)


@router.get("/result-consistency")
async def get_result_consistency(
    limit: int = Query(100, ge=1, le=500, description="Maximum result consistency issues to return"),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Audit match-result facts against prediction DB fixtures."""
    return audit_world_cup_result_consistency(session=session, limit=limit)


@router.post("/result-fact-backfill")
async def post_result_fact_backfill(
    limit: int = Query(100, ge=1, le=500, description="Maximum missing result facts to backfill"),
    dry_run: bool = Query(True, description="When true, only report result facts that would be imported"),
    confirm: bool = Query(False, description="Must be true with dry_run=false to write facts"),
    _auth: None = Depends(require_write_key),
    audit_metadata: dict[str, str] = Depends(get_audit_metadata),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Backfill missing match-result facts from finished prediction fixtures."""
    return run_world_cup_result_fact_backfill(
        session=session,
        dry_run=dry_run,
        confirm=confirm,
        limit=limit,
        audit_metadata=audit_metadata,
    )


@router.get("/result-fact-backfill/runs")
async def get_result_fact_backfill_runs(
    limit: int = Query(10, ge=1, le=50, description="Maximum result fact backfill audit runs to return"),
) -> dict[str, Any]:
    """Get recent audit runs for confirmed result fact backfills."""
    return list_world_cup_result_fact_backfill_runs(limit=limit)


@router.get("/consistency-repair-preview")
async def get_consistency_repair_preview(
    history_ids: list[int] = Query(..., description="Prediction history row IDs to preview"),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Preview method-fill repairs for selected prediction history rows."""
    return preview_consistency_history_repair(history_ids=history_ids, session=session)


@router.post("/consistency-repair")
async def post_consistency_repair(
    history_ids: list[int] = Query(..., description="Prediction history row IDs to repair"),
    dry_run: bool = Query(True, description="When true, only report repair actions"),
    confirm: bool = Query(False, description="Must be true with dry_run=false to write changes"),
    _auth: None = Depends(require_write_key),
    audit_metadata: dict[str, str] = Depends(get_audit_metadata),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Apply selected method-fill repairs with dry-run and confirmation guards."""
    return apply_consistency_history_repair(
        history_ids=history_ids,
        session=session,
        dry_run=dry_run,
        confirm=confirm,
        audit_metadata=audit_metadata,
    )


@router.post("/reconcile-scoring")
async def reconcile_scoring(
    _auth: None = Depends(require_write_key),
    audit_metadata: dict[str, str] = Depends(get_audit_metadata),
) -> dict[str, Any]:
    """Score finished matches against stored predictions."""
    return score_all_finished_matches(audit_metadata=audit_metadata)


@router.get("/reconcile-scoring/runs")
async def get_scoring_reconcile_runs(
    limit: int = Query(10, ge=1, le=50, description="Maximum audit runs to return"),
) -> dict[str, Any]:
    """Get recent audit runs for scoring reconciliation."""
    runs = loop_run_store.recent_runs(
        limit=limit, job_name=SCORING_RECONCILE_AUDIT_JOB_NAME
    )
    return {
        "status": "ok",
        "job_name": SCORING_RECONCILE_AUDIT_JOB_NAME,
        "count": len(runs),
        "runs": [
            {
                "id": r["id"],
                "status": r["status"],
                "started_at": r.get("started_at"),
                "finished_at": r.get("finished_at"),
                "duration_ms": (r.get("result") or {}).get("duration_ms"),
                "scored": (r.get("result") or {}).get("scored"),
                "skipped": (r.get("result") or {}).get("skipped"),
                "errors": (r.get("result") or {}).get("errors"),
                "trigger_source": (r.get("result") or {}).get("audit_metadata", {}).get("trigger_source"),
                "operator": (r.get("result") or {}).get("audit_metadata", {}).get("operator"),
                "error": r.get("error"),
            }
            for r in runs
        ],
    }


@router.post("/post-match-backfill")
async def post_match_backfill(
    source: str = Query("football-data", description="Fixture/result source to sync before scoring"),
    dry_run: bool = Query(True, description="When true, report candidates without syncing or writing"),
    _auth: None = Depends(require_write_key),
    audit_metadata: dict[str, str] = Depends(get_audit_metadata),
) -> dict[str, Any]:
    """Run the World Cup post-match backfill loop."""
    return run_post_match_backfill(
        source=source,
        dry_run=dry_run,
        sync_first=True,
        audit_metadata=audit_metadata,
    )


@router.get("/post-match-backfill/runs")
async def get_post_match_backfill_runs(
    limit: int = Query(10, ge=1, le=50, description="Maximum audit runs to return"),
) -> dict[str, Any]:
    """Get recent audit runs for the World Cup post-match backfill loop."""
    return list_post_match_backfill_runs(limit=limit)


@router.get("/accuracy-stats")
async def get_accuracy_stats(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get prediction accuracy statistics."""
    # Refresh PredictionAccuracy table from live MatchResult data
    from app.services.world_cup_quality_service import refresh_prediction_accuracy
    try:
        refresh_prediction_accuracy(session)
    except Exception as e:
        logger.warning("Prediction accuracy refresh failed (using stale data): %s", e)

    results = session.query(MatchResult).all()

    if not results:
        return {
            "total_matches": 0,
            "outcome_accuracy": 0.0,
            "avg_score_mae": 0.0,
            "avg_brier_score": 0.0,
            "by_engine": {},
            "by_stage": [],
        }

    # Only count matches that actually have predictions (outcome_correct is not None).
    # Matches without predictions have outcome_correct=None and would dilute accuracy.
    predicted = [r for r in results if r.outcome_correct is not None]
    total = len(results)
    predicted_count = len(predicted)
    outcome_correct = sum(1 for r in predicted if r.outcome_correct == 1)

    # Calculate averages — only over rows that have the metric
    scored_mae = [r.score_mae for r in results if r.score_mae is not None]
    scored_brier = [r.brier_score for r in results if r.brier_score is not None]
    avg_mae = sum(scored_mae) / len(scored_mae) if scored_mae else 0.0
    avg_brier = sum(scored_brier) / len(scored_brier) if scored_brier else 0.0

    # By-stage breakdown from PredictionAccuracy table
    from app.models.world_cup_prediction import PredictionAccuracy
    stage_rows = session.query(PredictionAccuracy).all()
    by_stage = [
        {
            "stage": r.stage,
            "matches_evaluated": r.matches_evaluated,
            "exact_score_pct": round(r.exact_score_correct / r.matches_evaluated, 3) if r.matches_evaluated and r.exact_score_correct else 0,
            "goal_diff_pct": round(r.goal_diff_correct / r.matches_evaluated, 3) if r.matches_evaluated and r.goal_diff_correct else 0,
            "outcome_accuracy": round(r.outcome_accuracy, 3) if r.outcome_accuracy else 0,
            "score_mae": round(r.score_mae, 2) if r.score_mae else None,
        }
        for r in stage_rows
    ]

    return {
        "total_matches": total,
        "predicted_matches": predicted_count,
        "outcome_accuracy": round(outcome_correct / predicted_count, 3) if predicted_count > 0 else 0,
        "avg_score_mae": round(avg_mae, 2),
        "avg_brier_score": round(avg_brier, 3),
        "exact_score_correct": sum(1 for r in predicted if abs(r.home_error or 0) < 0.5 and abs(r.away_error or 0) < 0.5),
        "by_stage": by_stage,
    }


# ---------------------------------------------------------------------------
# Tournament simulation (Monte Carlo)
# ---------------------------------------------------------------------------

_TOURNAMENT_CACHE: dict[int, dict[str, Any]] = {}
_TOURNAMENT_CACHE_TIME: dict[int, float] = {}
_TOURNAMENT_CACHE_TTL = 3600.0  # 1 hour
_TOURNAMENT_CACHE_LOCK = threading.Lock()


@router.get("/tournament-simulation")
async def get_tournament_simulation(
    num_simulations: int = Query(default=5000, ge=1000, le=20000),
    session: Session = Depends(get_prediction_session_dep),
) -> dict[str, Any]:
    """Run Monte Carlo tournament simulation and return win/progression probabilities."""
    import time as _time

    global _TOURNAMENT_CACHE, _TOURNAMENT_CACHE_TIME

    # Return cached result if fresh (fast path under lock)
    with _TOURNAMENT_CACHE_LOCK:
        now = _time.monotonic()
        cached_result = _TOURNAMENT_CACHE.get(num_simulations)
        cached_at = _TOURNAMENT_CACHE_TIME.get(num_simulations, 0.0)
        if cached_result is not None and (now - cached_at) < _TOURNAMENT_CACHE_TTL:
            return {**cached_result, "cached": True}

    # Load groups from fixtures
    from app.models.world_cup_prediction import MatchFixture
    fixtures = session.query(MatchFixture).filter(
        MatchFixture.group.isnot(None),
        func.lower(MatchFixture.stage) == "group_stage",
    ).all()

    groups: dict[str, list[str]] = {}
    for f in fixtures:
        g = (f.group or "").strip()
        if not g:
            continue
        groups.setdefault(g, [])
        if f.home_team and f.home_team not in groups[g]:
            groups[g].append(f.home_team)
        if f.away_team and f.away_team not in groups[g]:
            groups[g].append(f.away_team)

    if len(groups) < 2:
        return {
            "error": "insufficient_group_data",
            "message": "需要至少 2 个小组的赛程数据才能模拟",
            "groups_found": len(groups),
        }

    # Pre-fetch Elo ratings (avoids asyncio.run RuntimeError in sync context)
    from app.services.elo_ratings_service import get_elo_rating
    all_teams = set()
    for teams in groups.values():
        all_teams.update(teams)

    elo_cache: dict[str, float] = {}
    for team in all_teams:
        try:
            data = await get_elo_rating(team)
            elo_cache[team] = data.get("elo_rating", 1500.0)
        except Exception as e:
            logger.warning("Elo fetch failed for %s, using default 1500: %s", team, e)
            elo_cache[team] = 1500.0

    # Run simulation
    from app.services.world_cup_tournament_simulator import simulate_tournament
    result = simulate_tournament(
        groups=groups,
        elo_cache=elo_cache,
        num_simulations=num_simulations,
    )

    result["cached_at"] = datetime.now(timezone.utc).isoformat()
    result["groups"] = {g: teams for g, teams in groups.items()}

    # Cache for subsequent requests (under lock)
    with _TOURNAMENT_CACHE_LOCK:
        _TOURNAMENT_CACHE[num_simulations] = result
        _TOURNAMENT_CACHE_TIME[num_simulations] = _time.monotonic()

    return result


@router.get("/odds-cache-stats")
async def get_odds_cache_stats(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get odds API cache statistics."""

    from datetime import datetime, timedelta

    cache_entries = session.query(OddsCache).all()

    if not cache_entries:
        return {
            "total_entries": 0,
            "fresh_count": 0,
            "stale_count": 0,
            "estimated_api_calls_saved": 0
        }

    now = datetime.now(timezone.utc)
    # OddsCache has no expires_at column; derive freshness from cached_at + 1h TTL
    ttl = timedelta(hours=1)
    fresh = sum(1 for e in cache_entries if e.cached_at and (now - e.cached_at) < ttl)
    stale = len(cache_entries) - fresh

    # Estimate API calls saved (each cache hit = 1 API call saved)
    # Assume each entry was used at least once
    api_calls_saved = len(cache_entries)

    return {
        "total_entries": len(cache_entries),
        "fresh_count": fresh,
        "stale_count": stale,
        "estimated_api_calls_saved": api_calls_saved,
        "cache_hit_rate": round(fresh / len(cache_entries), 3) if cache_entries else 0
    }


@router.get("/prediction-timeline")
async def get_prediction_timeline(
    match_id: str,
    session: Session = Depends(get_prediction_session_dep)
) -> dict[str, Any]:
    """Get prediction evolution timeline for a match."""

    history = (
        session.query(PredictionHistory)
        .filter_by(match_id=match_id)
        .order_by(PredictionHistory.timestamp)
        .all()
    )

    if not history:
        return {
            "match_id": match_id,
            "snapshots": []
        }

    snapshots = []
    for entry in history:
        snapshots.append({
            "timestamp": entry.timestamp.isoformat(),
            "predicted_score": {
                "home": entry.predicted_home_score,
                "away": entry.predicted_away_score
            },
            "outcome_probabilities": {
                "home_win": entry.home_win_prob,
                "draw": entry.draw_prob,
                "away_win": entry.away_win_prob
            },
            "confidence": entry.confidence,
            "trigger": entry.trigger,
            "match_minute": entry.match_minute,
            "actual_score": {
                "home": entry.actual_home_score,
                "away": entry.actual_away_score
            } if entry.actual_home_score is not None else None
        })

    return {
        "match_id": match_id,
        "snapshots": snapshots,
        "total_updates": len(snapshots)
    }


@router.get("/system-health")
async def get_system_health(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get overall system health metrics."""

    from datetime import datetime, timedelta

    # Recent predictions (last 24 hours)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    recent_predictions = (
        session.query(MatchPrediction)
        .filter(MatchPrediction.last_updated >= yesterday)
        .count()
    )

    # Cache health
    cache_entries = session.query(OddsCache).count()

    # Data freshness
    latest_prediction = (
        session.query(MatchPrediction)
        .order_by(MatchPrediction.last_updated.desc())
        .first()
    )

    data_age_hours = 0
    if latest_prediction and latest_prediction.last_updated:
        # SQLite stores naive datetimes; attach UTC tzinfo before subtracting.
        last_updated = latest_prediction.last_updated.replace(tzinfo=timezone.utc)
        data_age = datetime.now(timezone.utc) - last_updated
        data_age_hours = data_age.total_seconds() / 3600

    return {
        "status": "healthy" if recent_predictions > 0 else "stale",
        "recent_predictions_24h": recent_predictions,
        "cache_entries": cache_entries,
        "data_freshness_hours": round(data_age_hours, 1),
        "last_update": latest_prediction.last_updated.isoformat() if latest_prediction else None
    }
