"""API routes for World Cup dynamic score predictions."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel

from app.models.world_cup_prediction import MatchFixture, MatchPrediction, PredictionHistory, AIAnalysisHistory
from app.services.world_cup_match_service import sync_world_cup_fixtures, get_remaining_matches
from app.services.world_cup_prediction_engine import predict_match_score
from app.services.world_cup_factor_service import build_prediction_factors
from app.utils.prediction_db import get_prediction_session, close_prediction_session, init_prediction_db


router = APIRouter(prefix="/world-cup/predictions", tags=["world-cup-predictions"])


class FlexibleResponse(BaseModel):
    """Flexible response model that accepts any fields."""
    class Config:
        extra = "allow"


class PredictionRequest(BaseModel):
    """Request body for prediction trigger."""
    engine: str = "auto"


def _engine_used_from_method(method: str | None) -> str:
    if method and method.startswith("elo"):
        return "elo_odds"
    return "hybrid"


def _serialize_prediction(prediction: MatchPrediction) -> dict[str, Any]:
    method = prediction.prediction_method
    return {
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
        "prediction_method": method,
        "engine_used": _engine_used_from_method(method),
        "ai_reasoning": prediction.ai_reasoning,
        "key_factors": prediction.key_factors,
        "last_updated": prediction.last_updated.isoformat() if prediction.last_updated else None,
        "has_betting_odds": bool(method and "elo_odds" in method),
    }


@router.post("/init-db", response_model=FlexibleResponse)
async def initialize_prediction_db():
    """Initialize the prediction database schema."""
    try:
        init_prediction_db()
        return {"status": "ok", "message": "Database initialized"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-fixtures", response_model=FlexibleResponse)
async def sync_fixtures(source: str = Query("football-data", description="Data source: 'football-data' or 'api-football'")):
    """Sync World Cup fixtures to database.

    Args:
        source: Data source to use (default: football-data for real-time 2026 data)
    """
    result = sync_world_cup_fixtures(source=source)
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
        # Expire all objects to force refresh from database
        session.expire_all()

        query = session.query(MatchFixture)

        if stage:
            query = query.filter(MatchFixture.stage == stage)
        if status:
            query = query.filter(MatchFixture.status == status)

        # Get all matches first (without limit), then sort and slice
        matches = query.order_by(MatchFixture.kickoff_utc).all()

        match_list = []
        for m in matches:
            # Get prediction for this match
            prediction = session.query(MatchPrediction).filter_by(match_id=m.match_id).first()

            match_with_prediction = {
                "match": {
                    "match_id": m.match_id,
                    "fixture_id": m.fixture_id,
                    "home_team": m.home_team,
                    "away_team": m.away_team,
                    "kickoff_utc": m.kickoff_utc.isoformat() if m.kickoff_utc else None,
                    "venue": m.venue,
                    "stage": m.stage,
                    "group": m.group,
                    "status": m.status,
                    "home_score": m.home_score,
                    "away_score": m.away_score
                }
            }

            if prediction:
                match_with_prediction["prediction"] = _serialize_prediction(prediction)

            match_list.append(match_with_prediction)

        # Sort: in_play first, then scheduled, then finished
        status_order = {"in_play": 0, "scheduled": 1, "finished": 2}
        match_list.sort(key=lambda m: (
            status_order.get(m["match"]["status"], 3),
            m["match"]["kickoff_utc"]
        ))

        # Apply limit after sorting
        match_list = match_list[:limit]

        return {
            "status": "ok",
            "count": len(match_list),
            "matches": match_list
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
            result["prediction"] = _serialize_prediction(prediction)

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
                    "trigger": h.trigger,
                    "prediction_method": h.prediction_method
                }
                for h in history
            ]
        }

    finally:
        close_prediction_session(session)


@router.post("/matches/{match_id}/predict", response_model=FlexibleResponse)
async def trigger_prediction(match_id: str, request: PredictionRequest = Body(default=PredictionRequest())):
    """Manually trigger prediction generation for a match.

    Args:
        match_id: Match ID to predict
        request: Prediction request with optional engine selection
            - engine: "auto" (default), "elo_odds", or "hybrid"
    """
    from app.services.world_cup_prediction_pipeline import run_prediction_pipeline

    result = await run_prediction_pipeline(match_id, trigger="manual", engine=request.engine)

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result


@router.post("/matches/{match_id}/analyze", response_model=FlexibleResponse)
async def analyze_match_prediction(match_id: str):
    """Get AI analysis of a match prediction.

    Args:
        match_id: Match ID to analyze
    """
    session = get_prediction_session()
    try:
        # Get match fixture
        match = session.query(MatchFixture).filter_by(match_id=match_id).first()
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")

        # Get current prediction
        prediction = session.query(MatchPrediction).filter_by(match_id=match_id).first()
        if not prediction:
            raise HTTPException(status_code=404, detail="No prediction found for this match")

        # Check if we have a recent analysis for this prediction
        latest_analysis = session.query(AIAnalysisHistory).filter_by(
            match_id=match_id
        ).order_by(AIAnalysisHistory.created_at.desc()).first()

        # Reuse analysis if prediction hasn't changed (including engine method)
        if latest_analysis and (
            abs(latest_analysis.predicted_home_score - prediction.predicted_home_score) < 0.01 and
            abs(latest_analysis.predicted_away_score - prediction.predicted_away_score) < 0.01 and
            abs(latest_analysis.confidence - prediction.confidence) < 0.01 and
            latest_analysis.prediction_method == prediction.prediction_method
        ):
            return {
                "status": "ok",
                "match_id": match_id,
                "analysis": latest_analysis.analysis_text,
                "cached": True,
                "created_at": latest_analysis.created_at.isoformat()
            }

        # Call AI analysis service
        from app.services.world_cup_ai_analysis_service import analyze_prediction_with_ai

        analysis = await analyze_prediction_with_ai(
            home_team=match.home_team,
            away_team=match.away_team,
            predicted_score={
                "home": prediction.predicted_home_score,
                "away": prediction.predicted_away_score
            },
            outcome_probabilities={
                "home_win": prediction.home_win_prob,
                "draw": prediction.draw_prob,
                "away_win": prediction.away_win_prob
            },
            confidence=prediction.confidence,
            prediction_method=prediction.prediction_method,
            key_factors=prediction.key_factors or []
        )

        # Save analysis to history
        analysis_record = AIAnalysisHistory(
            match_id=match_id,
            analysis_text=analysis,
            predicted_home_score=prediction.predicted_home_score,
            predicted_away_score=prediction.predicted_away_score,
            confidence=prediction.confidence,
            prediction_method=prediction.prediction_method
        )
        session.add(analysis_record)
        session.commit()

        return {
            "status": "ok",
            "match_id": match_id,
            "analysis": analysis,
            "cached": False
        }

    finally:
        close_prediction_session(session)


@router.get("/matches/{match_id}/analysis-history", response_model=FlexibleResponse)
async def get_analysis_history(match_id: str):
    """Get AI analysis history for a match.

    Args:
        match_id: Match ID
    """
    session = get_prediction_session()
    try:
        history = session.query(AIAnalysisHistory).filter_by(
            match_id=match_id
        ).order_by(AIAnalysisHistory.created_at.desc()).all()

        return {
            "status": "ok",
            "match_id": match_id,
            "count": len(history),
            "history": [
                {
                    "id": h.id,
                    "analysis": h.analysis_text,
                    "predicted_score": {
                        "home": h.predicted_home_score,
                        "away": h.predicted_away_score
                    },
                    "confidence": h.confidence,
                    "prediction_method": h.prediction_method,
                    "created_at": h.created_at.isoformat() if h.created_at else None
                }
                for h in history
            ]
        }

    finally:
        close_prediction_session(session)


@router.post("/batch-predict", response_model=FlexibleResponse)
async def batch_predict(match_ids: list[str] | None = None, engine: str = "auto"):
    """Run predictions for multiple matches.

    Args:
        match_ids: Optional list of match IDs (None = all remaining matches)
        engine: Prediction engine to use ("auto", "elo_odds", "hybrid")
    """
    from app.services.world_cup_prediction_pipeline import batch_predict_matches

    result = await batch_predict_matches(match_ids, trigger="batch_manual", engine=engine)
    return result


@router.get("/today", response_model=FlexibleResponse)
async def get_today_matches():
    """Get today's matches with predictions."""
    session = get_prediction_session()
    try:
        # Expire all objects to force refresh from database
        session.expire_all()

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

            match_with_prediction = {
                "match": {
                    "match_id": match.match_id,
                    "fixture_id": match.fixture_id,
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "kickoff_utc": match.kickoff_utc.isoformat() if match.kickoff_utc else None,
                    "venue": match.venue,
                    "stage": match.stage,
                    "group": match.group,
                    "status": match.status,
                    "home_score": match.home_score,
                    "away_score": match.away_score
                }
            }

            if prediction:
                match_with_prediction["prediction"] = _serialize_prediction(prediction)

            results.append(match_with_prediction)

        # Sort: in_play first, then scheduled, then finished
        status_order = {"in_play": 0, "scheduled": 1, "finished": 2}
        results.sort(key=lambda m: (
            status_order.get(m["match"]["status"], 3),
            m["match"]["kickoff_utc"]
        ))

        return {
            "status": "ok",
            "date": now.date().isoformat(),
            "count": len(results),
            "matches": results
        }

    finally:
        close_prediction_session(session)


@router.get("/engine-comparison", response_model=FlexibleResponse)
async def compare_engine_accuracy():
    """Compare accuracy of different prediction engines on finished matches."""
    from app.services.engine_comparison_service import calculate_engine_accuracy

    result = calculate_engine_accuracy()
    return result


@router.post("/auto-tune/{engine_name}", response_model=FlexibleResponse)
async def auto_tune_engine(engine_name: str):
    """Run automatic tuning cycle for an engine: analyze, optimize, learn, calibrate.

    Args:
        engine_name: Engine to tune ("elo_odds" or "hybrid")
    """
    from app.services.engine_auto_tuning_service import run_full_auto_tuning_cycle

    result = await run_full_auto_tuning_cycle(engine_name)
    return result


@router.post("/batch-optimize", response_model=FlexibleResponse)
async def batch_optimize_predictions(
    engine: str | None = Query(None, description="Filter by engine (elo_odds, hybrid)"),
    limit: int = Query(10, ge=1, le=100, description="Max matches to process")
):
    """Run AI optimization on multiple scheduled matches.

    Args:
        engine: Optional engine filter
        limit: Maximum number of matches to process
    """
    from app.services.engine_auto_tuning_service import analyze_and_optimize_all_predictions

    result = await analyze_and_optimize_all_predictions(
        engine_filter=engine,
        limit=limit
    )
    return result


@router.get("/calibration/{engine_name}", response_model=FlexibleResponse)
async def get_engine_calibration(engine_name: str):
    """Get active calibration parameters for an engine.

    Args:
        engine_name: Engine name
    """
    from app.services.engine_auto_tuning_service import get_active_calibration

    calibration = get_active_calibration(engine_name)
    if not calibration:
        return {
            "status": "no_calibration",
            "engine": engine_name,
            "message": "No active calibration found"
        }

    return {
        "status": "ok",
        "calibration": calibration
    }


@router.get("/calibration-patterns/{engine_name}", response_model=FlexibleResponse)
async def analyze_calibration_patterns(engine_name: str):
    """Analyze AI optimization patterns for an engine to derive calibration suggestions.

    Args:
        engine_name: Engine name
    """
    from app.services.engine_auto_tuning_service import calculate_optimization_patterns

    result = calculate_optimization_patterns(engine_name)
    return result


@router.post("/matches/{match_id}/optimize", response_model=FlexibleResponse)
async def optimize_match_prediction(match_id: str):
    """Get AI optimization suggestions for a match prediction.

    Args:
        match_id: Match ID to optimize
    """
    session = get_prediction_session()
    try:
        # Get match fixture
        match = session.query(MatchFixture).filter_by(match_id=match_id).first()
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")

        # Get current prediction
        prediction = session.query(MatchPrediction).filter_by(match_id=match_id).first()
        if not prediction:
            raise HTTPException(status_code=404, detail="No prediction found for this match")

        # Call AI optimization service
        from app.services.world_cup_ai_optimization_service import optimize_prediction_with_ai

        optimization_result = await optimize_prediction_with_ai(
            home_team=match.home_team,
            away_team=match.away_team,
            current_prediction={
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
                "elo_ratings": prediction.factors.get("elo_ratings") if prediction.factors else None
            },
            prediction_method=prediction.prediction_method,
            match_context=None  # TODO: Add context from factors if available
        )

        if optimization_result.get("status") == "error":
            raise HTTPException(status_code=500, detail=optimization_result.get("message"))

        if optimization_result.get("status") == "unavailable":
            raise HTTPException(status_code=503, detail=optimization_result.get("message"))

        return {
            "status": "ok",
            "match_id": match_id,
            "original_prediction": {
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
            },
            "optimization": optimization_result.get("optimization")
        }

    finally:
        close_prediction_session(session)
