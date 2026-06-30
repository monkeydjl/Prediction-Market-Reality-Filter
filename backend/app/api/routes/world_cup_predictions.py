"""API routes for World Cup dynamic score predictions."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_

from app.api.security import optional_write_key, require_write_key
from app.models.world_cup_prediction import MatchFixture, MatchPrediction, PredictionHistory, AIAnalysisHistory
from app.services.world_cup_match_service import sync_world_cup_fixtures, get_remaining_matches
from app.services.world_cup_factor_service import build_prediction_factors
from app.services.world_cup_data_quality import enrich_data_quality_metrics
from app.utils.prediction_db import get_prediction_session, close_prediction_session, init_prediction_db


router = APIRouter(prefix="/world-cup/predictions", tags=["world-cup-predictions"])

logger = logging.getLogger(__name__)


class FlexibleResponse(BaseModel):
    """Flexible response model that accepts any fields."""
    model_config = ConfigDict(extra="allow")


class PredictionRequest(BaseModel):
    """Request body for prediction trigger."""
    engine: str = "auto"


def _engine_used_from_method(method: str | None) -> str:
    if method and method.startswith("integrated"):
        return "integrated"
    if method and method.startswith("elo"):
        return "elo_odds"
    if method and method.startswith("gbm"):
        return "gbm"
    return "hybrid"


def _serialize_prediction(prediction: MatchPrediction) -> dict[str, Any]:
    method = prediction.prediction_method
    factors = prediction.factors or {}
    raw_quality_metrics = factors.get("data_quality_metrics") or {}
    quality_metrics = (
        enrich_data_quality_metrics(raw_quality_metrics, factors)
        if raw_quality_metrics
        else {}
    )
    confidence_calibration = factors.get("confidence_calibration") or None
    high_confidence_selection = factors.get("high_confidence_selection") or None
    explanation_contributions = factors.get("explanation_contributions") or None
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
        "has_betting_odds": bool(quality_metrics.get("has_odds") or (method and "elo_odds" in method)),
        "data_quality": factors.get("data_quality"),
        "data_quality_score": quality_metrics.get("quality_score"),
        "raw_confidence": (
            confidence_calibration.get("raw")
            if isinstance(confidence_calibration, dict)
            else None
        ),
        "confidence_calibration": confidence_calibration,
        "high_confidence_selection": high_confidence_selection,
        "explanation_contributions": explanation_contributions,
    }


def _history_matches_current_prediction(
    history: PredictionHistory,
    prediction: MatchPrediction,
) -> bool:
    """Return true when a history row is the same snapshot as current prediction."""
    if history.prediction_method != prediction.prediction_method:
        return False
    return (
        abs(float(history.predicted_home_score) - float(prediction.predicted_home_score)) < 0.001
        and abs(float(history.predicted_away_score) - float(prediction.predicted_away_score)) < 0.001
        and abs(float(history.confidence) - float(prediction.confidence)) < 0.01
    )


def _serialize_history_entry(
    history: PredictionHistory,
    current_prediction: MatchPrediction | None = None,
    current_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": history.timestamp.isoformat() if history.timestamp else None,
        "predicted_score": {
            "home": history.predicted_home_score,
            "away": history.predicted_away_score,
        },
        "outcome_probabilities": {
            "home_win": history.home_win_prob,
            "draw": history.draw_prob,
            "away_win": history.away_win_prob,
        },
        "confidence": history.confidence,
        "trigger": history.trigger,
        "prediction_method": history.prediction_method,
        "engine_used": _engine_used_from_method(history.prediction_method),
    }

    if (
        current_prediction is not None
        and current_payload is not None
        and _history_matches_current_prediction(history, current_prediction)
    ):
        for key in (
            "raw_confidence",
            "confidence_calibration",
            "high_confidence_selection",
            "explanation_contributions",
        ):
            if current_payload.get(key) is not None:
                payload[key] = current_payload[key]

    return payload


@router.post("/init-db", response_model=FlexibleResponse)
async def initialize_prediction_db(_auth: None = Depends(require_write_key)):
    """Initialize the prediction database schema."""
    try:
        init_prediction_db()
        return {"status": "ok", "message": "Database initialized"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-fixtures", response_model=FlexibleResponse)
async def sync_fixtures(
    source: str = Query("football-data", description="Data source: 'football-data' or 'api-football'"),
    _auth: None = Depends(require_write_key),
):
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

        # Batch-fetch predictions for all matches (avoids N+1 query)
        match_ids = [m.match_id for m in matches]
        predictions = (
            session.query(MatchPrediction)
            .filter(MatchPrediction.match_id.in_(match_ids))
            .all()
        ) if match_ids else []
        prediction_map = {p.match_id: p for p in predictions}

        match_list = []
        for m in matches:
            prediction = prediction_map.get(m.match_id)

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
        ).filter(
            or_(
                PredictionHistory.trigger.is_(None),
                ~PredictionHistory.trigger.like("%_comparison"),
            )
        ).order_by(PredictionHistory.timestamp, PredictionHistory.id).all()
        current_prediction = session.query(MatchPrediction).filter_by(match_id=match_id).first()
        current_payload = _serialize_prediction(current_prediction) if current_prediction else None

        return {
            "status": "ok",
            "match_id": match_id,
            "count": len(history),
            "history": [
                _serialize_history_entry(h, current_prediction, current_payload)
                for h in history
            ]
        }

    except Exception as hist_err:
        # Degrade to empty history instead of returning HTTP 500, so the
        # frontend can fall back to the "no history yet" empty state.
        logger.error("Failed to load prediction history for %s: %s", match_id, hist_err, exc_info=True)
        return {
            "status": "ok",
            "match_id": match_id,
            "count": 0,
            "history": [],
        }

    finally:
        close_prediction_session(session)


@router.post("/matches/{match_id}/predict", response_model=FlexibleResponse)
async def trigger_prediction(
    match_id: str,
    request: PredictionRequest = Body(default=PredictionRequest()),
    compare_only: bool = Query(False, description="Read-only mode: run the engine without persisting (bypasses kickoff freeze, skips MatchPrediction/PredictionHistory writes)"),
    _auth: bool = Depends(optional_write_key),
):
    """Manually trigger prediction generation for a match.

    Args:
        match_id: Match ID to predict
        request: Prediction request with optional engine selection
            - engine: "auto" (default), "elo_odds", "hybrid", or "integrated"
        compare_only: When true, runs the chosen engine in read-only mode
            so the engine-comparison card can render even after kickoff.
            Skips persistence and bypasses the kickoff freeze.
    """
    # compare_only is a read-only operation — no auth required.
    if not compare_only and not _auth:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key",
        )
    from app.services.world_cup_prediction_pipeline import run_prediction_pipeline

    result = await run_prediction_pipeline(
        match_id,
        trigger="manual",
        engine=request.engine,
        compare_only=compare_only,
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result


@router.post("/matches/{match_id}/analyze", response_model=FlexibleResponse)
async def analyze_match_prediction(
    match_id: str,
    _auth: None = Depends(require_write_key),
):
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
async def batch_predict(
    match_ids: list[str] | None = None,
    engine: str = "auto",
    _auth: None = Depends(require_write_key),
):
    """Run predictions for multiple matches.

    Args:
        match_ids: Optional list of match IDs (None = all remaining matches)
        engine: Prediction engine to use ("auto", "elo_odds", "hybrid")
    """
    from app.services.world_cup_prediction_pipeline import batch_predict_matches

    result = await batch_predict_matches(match_ids, trigger="batch_manual", engine=engine)
    return result


@router.post("/batch-switch-engine", response_model=FlexibleResponse)
async def batch_switch_engine(
    engine: str = Query(..., description='Target engine: "elo_odds", "hybrid", "integrated", or "high_confidence"'),
    status_filter: str = Query("scheduled", description='Match status filter (default: "scheduled")'),
    _auth: None = Depends(require_write_key),
):
    """Batch switch all matches to a specific prediction engine.

    Args:
        engine: Target engine to use
            - "elo_odds": Fast ELO + odds fusion engine
            - "hybrid": Full hybrid engine (rule + AI)
            - "integrated": Fuse elo_odds and hybrid engine results
            - "high_confidence": Auto-select best engine based on confidence
        status_filter: Only process matches with this status (default: "scheduled")

    Returns:
        Batch prediction results
    """
    from app.services.world_cup_prediction_pipeline import batch_predict_matches

    logger.info(f"batch_switch_engine called: engine={engine}, status_filter={status_filter}")

    session = get_prediction_session()
    try:
        # Get all matches matching the status filter
        matches = session.query(MatchFixture).filter(
            MatchFixture.status == status_filter
        ).all()

        match_ids = [m.match_id for m in matches]
        logger.info(f"Found {len(match_ids)} matches with status={status_filter}")

        if not match_ids:
            return {
                "status": "ok",
                "message": f"没有找到状态为 {status_filter} 的比赛",
                "total": 0,
                "succeeded": 0,
                "failed": 0,
                "skipped": 0
            }

        # Run batch prediction with specified engine
        logger.info(f"Starting batch_predict_matches for {len(match_ids)} matches")
        result = await batch_predict_matches(
            match_ids,
            trigger="batch_engine_switch",
            engine=engine
        )

        logger.info(f"batch_predict_matches completed: {result.get('status')}, succeeded={result.get('succeeded')}")
        return result
    except Exception as e:
        logger.error(f"Error in batch_switch_engine: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"批量切换失败: {str(e)}")
    finally:
        close_prediction_session(session)


@router.get("/batch-switch-engine-stream")
async def batch_switch_engine_stream(
    engine: str = Query(..., description='Target engine: "elo_odds", "hybrid", "integrated", or "high_confidence"'),
    status_filter: str = Query("scheduled", description="Match status filter (default: scheduled)"),
    _auth: None = Depends(require_write_key),
):
    """Stream batch engine switch progress as Server-Sent Events.

    Emits one ``progress`` event per match processed and a final ``complete``
    event with the aggregate summary. Replaces the long-poll
    ``POST /batch-switch-engine`` for clients that want real-time feedback.
    """

    async def event_stream() -> AsyncGenerator[str, None]:
        from app.services.world_cup_prediction_pipeline import run_prediction_pipeline

        logger.info(
            "batch_switch_engine_stream start: engine=%s status_filter=%s",
            engine,
            status_filter,
        )

        def sse(event: str, payload: dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        session = get_prediction_session()
        succeeded = 0
        failed = 0
        skipped = 0
        elo_odds_count = 0
        hybrid_count = 0
        integrated_count = 0
        total = 0
        try:
            matches = session.query(MatchFixture).filter(
                MatchFixture.status == status_filter
            ).all()
            total = len(matches)
            match_ids = [m.match_id for m in matches]

            if not match_ids:
                yield sse("complete", {
                    "status": "ok",
                    "message": f"没有找到状态为 {status_filter} 的比赛",
                    "total": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "skipped": 0,
                })
                return

            yield sse("start", {"total": total, "engine": engine})

            for idx, match_id in enumerate(match_ids, start=1):
                try:
                    result = await run_prediction_pipeline(
                        match_id,
                        trigger="batch_engine_switch",
                        engine=engine,
                        session=session,
                    )
                    status = result.get("status")
                    if status == "ok":
                        succeeded += 1
                        engine_used = result.get("engine_used")
                        if engine_used == "elo_odds":
                            elo_odds_count += 1
                        elif engine_used == "hybrid":
                            hybrid_count += 1
                        elif engine_used == "integrated":
                            integrated_count += 1
                    elif status == "skipped":
                        skipped += 1
                    else:
                        failed += 1

                    yield sse("progress", {
                        "current": idx,
                        "total": total,
                        "match_id": match_id,
                        "status": status,
                        "succeeded": succeeded,
                        "failed": failed,
                        "skipped": skipped,
                    })
                except Exception as exc:  # noqa: BLE001 - per-match isolation
                    failed += 1
                    logger.error(
                        "batch_switch_engine_stream match %s failed: %s",
                        match_id,
                        exc,
                    )
                    yield sse("progress", {
                        "current": idx,
                        "total": total,
                        "match_id": match_id,
                        "status": "error",
                        "error": str(exc),
                        "succeeded": succeeded,
                        "failed": failed,
                        "skipped": skipped,
                    })

            yield sse("complete", {
                "status": "ok",
                "total": total,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "elo_odds_count": elo_odds_count,
                "hybrid_count": hybrid_count,
                "integrated_count": integrated_count,
            })
        except Exception as exc:  # noqa: BLE001 - surface fatal errors
            logger.error("batch_switch_engine_stream fatal: %s", exc, exc_info=True)
            yield sse("error", {"message": str(exc)})
        finally:
            close_prediction_session(session)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/today", response_model=FlexibleResponse)
async def get_today_matches():
    """Get today's matches with predictions."""
    session = get_prediction_session()
    try:
        # Expire all objects to force refresh from database
        session.expire_all()

        now = datetime.now(timezone.utc)
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
async def auto_tune_engine(
    engine_name: str,
    background: bool = Query(True, description="Run in background"),
    _auth: None = Depends(require_write_key),
):
    """Run automatic tuning cycle for an engine: analyze, optimize, learn, calibrate.

    Args:
        engine_name: Engine to tune ("elo_odds", "hybrid", or "integrated")
        background: If True, run in background and return task_id immediately
    """
    if background:
        # Create background task
        from app.services.optimization_task_manager import get_task_manager
        from app.services.engine_auto_tuning_async import run_async_optimization
        import asyncio

        task_manager = get_task_manager()
        task = await task_manager.create_task(engine_name)

        # Start background task
        asyncio.create_task(run_async_optimization(engine_name, task.task_id))

        return {
            "status": "accepted",
            "task_id": task.task_id,
            "message": f"后台优化任务已启动，任务ID: {task.task_id}",
            "poll_url": f"/api/world-cup/predictions/auto-tune/status/{task.task_id}"
        }
    else:
        # Run synchronously (legacy behavior, not recommended)
        from app.services.engine_auto_tuning_service import run_full_auto_tuning_cycle

        result = await run_full_auto_tuning_cycle(engine_name)
        return result


@router.get("/auto-tune/status/{task_id}", response_model=FlexibleResponse)
async def get_auto_tune_status(task_id: str):
    """Get status of a background auto-tune task.

    Args:
        task_id: Task ID returned from auto-tune endpoint
    """
    from app.services.optimization_task_manager import get_task_manager

    task_manager = get_task_manager()
    task = await task_manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "status": "ok",
        "task": task.to_dict()
    }



@router.post("/batch-optimize", response_model=FlexibleResponse)
async def batch_optimize_predictions(
    engine: str | None = Query(None, description="Filter by engine (elo_odds, hybrid)"),
    limit: int = Query(10, ge=1, le=100, description="Max matches to process"),
    _auth: None = Depends(require_write_key),
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
async def optimize_match_prediction(
    match_id: str,
    _auth: None = Depends(require_write_key),
):
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

        # Don't optimize matches that have already started or finished
        if match.status == "finished":
            raise HTTPException(status_code=400, detail="Match already finished, prediction is frozen")
        if match.status == "in_play":
            raise HTTPException(status_code=400, detail="Match already started, prediction is frozen")

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
            match_context={
                "stage": match.stage if match else None,
                "group": match.group if match else None,
                "venue": match.venue if match else None,
                "data_quality": prediction.factors.get("data_quality") if prediction.factors else None,
                "key_factors": prediction.key_factors[:5] if prediction.key_factors else []
            }
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
