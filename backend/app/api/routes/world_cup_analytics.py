"""API endpoints for prediction analytics and monitoring."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Any

from app.utils.prediction_db import get_prediction_session_dep
from app.models.world_cup_prediction import (
    MatchPrediction,
    MatchResult,
    PredictionHistory,
)
from app.services.odds_cache_service import OddsCache


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/engine-stats")
async def get_engine_stats(session: Session = Depends(get_prediction_session_dep)) -> dict[str, Any]:
    """Get prediction engine usage statistics."""

    # Count predictions by engine
    predictions = session.query(MatchPrediction).all()

    total = len(predictions)
    elo_odds_count = sum(1 for p in predictions if p.prediction_method == "elo_odds")
    hybrid_count = sum(1 for p in predictions if p.prediction_method == "hybrid")

    # Calculate average confidence by engine
    elo_odds_confidence = 0.0
    hybrid_confidence = 0.0

    if elo_odds_count > 0:
        elo_odds_predictions = [p for p in predictions if p.prediction_method == "elo_odds"]
        elo_odds_confidence = sum(p.confidence for p in elo_odds_predictions) / elo_odds_count

    if hybrid_count > 0:
        hybrid_predictions = [p for p in predictions if p.prediction_method == "hybrid"]
        hybrid_confidence = sum(p.confidence for p in hybrid_predictions) / hybrid_count

    return {
        "total_predictions": total,
        "by_engine": {
            "elo_odds": {
                "count": elo_odds_count,
                "percentage": (elo_odds_count / total * 100) if total > 0 else 0,
                "avg_confidence": round(elo_odds_confidence, 3)
            },
            "hybrid": {
                "count": hybrid_count,
                "percentage": (hybrid_count / total * 100) if total > 0 else 0,
                "avg_confidence": round(hybrid_confidence, 3)
            }
        }
    }


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
    if latest_prediction:
        data_age = datetime.now(timezone.utc) - latest_prediction.last_updated
        data_age_hours = data_age.total_seconds() / 3600

    return {
        "status": "healthy" if recent_predictions > 0 else "stale",
        "recent_predictions_24h": recent_predictions,
        "cache_entries": cache_entries,
        "data_freshness_hours": round(data_age_hours, 1),
        "last_update": latest_prediction.last_updated.isoformat() if latest_prediction else None
    }
