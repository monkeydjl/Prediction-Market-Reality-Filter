"""API endpoints for prediction analytics and monitoring."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Any

from app.api.audit_metadata import get_audit_metadata
from app.api.security import require_write_key
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
from app.services.world_cup_scoring_service import score_all_finished_matches
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
    grouped: dict[str, list[MatchPrediction]] = {
        "elo_odds": [],
        "hybrid": [],
        "integrated": [],
    }
    for prediction in predictions:
        grouped[bucket_engine(prediction.prediction_method)].append(prediction)

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
            "elo_odds": engine_summary("elo_odds"),
            "hybrid": engine_summary("hybrid"),
            "integrated": engine_summary("integrated"),
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
) -> dict[str, Any]:
    """Score finished matches against stored predictions."""
    return score_all_finished_matches()


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

    results = session.query(MatchResult).all()

    if not results:
        return {
            "total_matches": 0,
            "outcome_accuracy": 0.0,
            "avg_score_mae": 0.0,
            "avg_brier_score": 0.0,
            "by_engine": {}
        }

    total = len(results)
    outcome_correct = sum(1 for r in results if r.outcome_correct == 1)

    # Calculate averages
    avg_mae = sum(r.score_mae for r in results if r.score_mae) / total
    avg_brier = sum(r.brier_score for r in results if r.brier_score) / total

    return {
        "total_matches": total,
        "outcome_accuracy": round(outcome_correct / total, 3) if total > 0 else 0,
        "avg_score_mae": round(avg_mae, 2),
        "avg_brier_score": round(avg_brier, 3),
        "exact_score_correct": sum(1 for r in results if abs(r.home_error or 0) < 0.5 and abs(r.away_error or 0) < 0.5)
    }


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
