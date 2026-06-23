"""API routes for World Cup dynamic score predictions."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.models.world_cup_prediction import MatchFixture, MatchPrediction, PredictionHistory
from app.services.world_cup_match_service import sync_world_cup_fixtures, get_remaining_matches
from app.services.world_cup_prediction_engine import predict_match_score
from app.services.world_cup_factor_service import build_prediction_factors
from app.utils.prediction_db import get_prediction_session, close_prediction_session, init_prediction_db


router = APIRouter(prefix="/world-cup/predictions", tags=["world-cup-predictions"])


class FlexibleResponse(BaseModel):
    """Flexible response model that accepts any fields."""
    class Config:
        extra = "allow"


@router.post("/init-db", response_model=FlexibleResponse)
async def initialize_prediction_db():
    """Initialize the prediction database schema."""
    try:
        init_prediction_db()
        return {"status": "ok", "message": "Database initialized"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-fixtures", response_model=FlexibleResponse)
async def sync_fixtures():
    """Sync World Cup fixtures from API-Football to database."""
    result = sync_world_cup_fixtures()
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


@router.get("/matches", response_model=FlexibleResponse)
async def list_matches(
    stage: str | None = Query(None, description="Filter by stage"),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(100, ge=1, le=200)
):
    """Get all matches with optional filters."""
    session = get_prediction_session()
    try:
        query = session.query(MatchFixture)

        if stage:
            query = query.filter(MatchFixture.stage == stage)
        if status:
            query = query.filter(MatchFixture.status == status)

        matches = query.order_by(MatchFixture.kickoff_utc).limit(limit).all()

        return {
            "status": "ok",
            "count": len(matches),
            "matches": [
                {
                    "match_id": m.match_id,
                    "fixture_id": m.fixture_id,
                    "home_team": m.home_team,
                    "away_team": m.away_team,
                    "kickoff_utc": m.kickoff_utc.isoformat() if m.kickoff_utc else None,
                    "venue": m.venue,
                    "stage": m.stage,
                    "group": m.group,
                    "status": m.status
                }
                for m in matches
            ]
        }
    finally:
        close_prediction_session(session)


@router.get("/matches/{match_id}", response_model=FlexibleResponse)
async def get_match(match_id: str):
    """Get single match details with prediction."""
    session = get_prediction_session()
    try:
        # Get match fixture
        match = session.query(MatchFixture).filter_by(match_id=match_id).first()
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")

        # Get current prediction
        prediction = session.query(MatchPrediction).filter_by(match_id=match_id).first()

        result: dict[str, Any] = {
            "status": "ok",
            "match": {
                "match_id": match.match_id,
                "fixture_id": match.fixture_id,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "kickoff_utc": match.kickoff_utc.isoformat() if match.kickoff_utc else None,
                "venue": match.venue,
                "stage": match.stage,
                "group": match.group,
                "status": match.status
            }
        }

        if prediction:
            result["prediction"] = {
                "predicted_score": {
                    "home": prediction.predicted_home_score,
                    "away": prediction.predicted_away_score
                },
                "outcome_probabilities": {
                    "home_win": prediction.home_win_prob,
                    "draw": prediction.draw_prob,
                    "away_win": prediction.away_win_prob
                },
                "confidence": prediction.confidence,
                "prediction_method": prediction.prediction_method,
                "ai_reasoning": prediction.ai_reasoning,
                "key_factors": prediction.key_factors,
                "last_updated": prediction.last_updated.isoformat() if prediction.last_updated else None
            }

        return result

    finally:
        close_prediction_session(session)


@router.get("/matches/{match_id}/prediction-history", response_model=FlexibleResponse)
async def get_prediction_history(match_id: str):
    """Get prediction history (time-series) for a match."""
    session = get_prediction_session()
    try:
        history = session.query(PredictionHistory).filter_by(
            match_id=match_id
        ).order_by(PredictionHistory.timestamp).all()

        return {
            "status": "ok",
            "match_id": match_id,
            "count": len(history),
            "history": [
                {
                    "timestamp": h.timestamp.isoformat() if h.timestamp else None,
                    "predicted_score": {
                        "home": h.predicted_home_score,
                        "away": h.predicted_away_score
                    },
                    "outcome_probabilities": {
                        "home_win": h.home_win_prob,
                        "draw": h.draw_prob,
                        "away_win": h.away_win_prob
                    },
                    "confidence": h.confidence,
                    "trigger": h.trigger
                }
                for h in history
            ]
        }

    finally:
        close_prediction_session(session)


@router.post("/matches/{match_id}/predict", response_model=FlexibleResponse)
async def trigger_prediction(match_id: str):
    """Manually trigger prediction generation for a match."""
    from app.services.world_cup_prediction_pipeline import run_prediction_pipeline

    result = await run_prediction_pipeline(match_id, trigger="manual")

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result


@router.post("/batch-predict", response_model=FlexibleResponse)
async def batch_predict(match_ids: list[str] | None = None):
    """Run predictions for multiple matches."""
    from app.services.world_cup_prediction_pipeline import batch_predict_matches

    result = await batch_predict_matches(match_ids, trigger="batch_manual")
    return result


@router.get("/today", response_model=FlexibleResponse)
async def get_today_matches():
    """Get today's matches with predictions."""
    session = get_prediction_session()
    try:
        now = datetime.utcnow()
        today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
        today_end = datetime(now.year, now.month, now.day, 23, 59, 59)

        matches = session.query(MatchFixture).filter(
            MatchFixture.kickoff_utc >= today_start,
            MatchFixture.kickoff_utc <= today_end
        ).order_by(MatchFixture.kickoff_utc).all()

        results = []
        for match in matches:
            prediction = session.query(MatchPrediction).filter_by(match_id=match.match_id).first()

            match_dict = {
                "match_id": match.match_id,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "kickoff_utc": match.kickoff_utc.isoformat() if match.kickoff_utc else None,
                "venue": match.venue,
                "stage": match.stage,
                "group": match.group,
                "status": match.status
            }

            if prediction:
                match_dict["prediction"] = {
                    "predicted_score": {
                        "home": prediction.predicted_home_score,
                        "away": prediction.predicted_away_score
                    },
                    "outcome_probabilities": {
                        "home_win": prediction.home_win_prob,
                        "draw": prediction.draw_prob,
                        "away_win": prediction.away_win_prob
                    },
                    "confidence": prediction.confidence
                }

            results.append(match_dict)

        return {
            "status": "ok",
            "date": now.date().isoformat(),
            "count": len(results),
            "matches": results
        }

    finally:
        close_prediction_session(session)
