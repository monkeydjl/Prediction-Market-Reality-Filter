"""Automatic engine tuning service based on AI optimization feedback."""

import json
from typing import Any
from datetime import datetime

from app.models.world_cup_prediction import (
    MatchFixture, MatchPrediction, AIOptimizedPrediction,
    EngineCalibration, MatchResult
)
from app.utils.prediction_db import get_prediction_session, close_prediction_session
from app.services.world_cup_ai_optimization_service import optimize_prediction_with_ai


async def analyze_and_optimize_all_predictions(
    engine_filter: str | None = None,
    limit: int | None = None
) -> dict[str, Any]:
    """Run AI analysis and optimization on all scheduled matches.

    Args:
        engine_filter: Only process predictions from this engine (e.g., "elo_odds", "hybrid")
        limit: Maximum number of matches to process (None = all)

    Returns:
        Summary of analysis and optimization results
    """
    session = get_prediction_session()
    try:
        # Get all scheduled matches with predictions
        query = session.query(MatchFixture, MatchPrediction).join(
            MatchPrediction,
            MatchFixture.match_id == MatchPrediction.match_id
        ).filter(
            MatchFixture.status == "scheduled"
        )

        if limit:
            query = query.limit(limit)

        matches = query.all()

        results = {
            "total_processed": 0,
            "optimizations_generated": 0,
            "errors": [],
            "optimizations": []
        }

        for fixture, prediction in matches:
            # Filter by engine if specified
            if engine_filter:
                if engine_filter == "elo_odds" and not prediction.prediction_method.startswith("elo"):
                    continue
                elif engine_filter == "hybrid" and prediction.prediction_method.startswith("elo"):
                    continue

            try:
                # Run AI optimization
                optimization_result = await optimize_prediction_with_ai(
                    home_team=fixture.home_team,
                    away_team=fixture.away_team,
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
                    match_context=None
                )

                results["total_processed"] += 1

                if optimization_result.get("status") == "ok":
                    opt = optimization_result.get("optimization", {})
                    opt_pred = opt.get("optimized_prediction")

                    if opt_pred:
                        # Save optimized prediction to database
                        optimized_record = AIOptimizedPrediction(
                            match_id=fixture.match_id,
                            original_engine=prediction.prediction_method,
                            original_home_score=prediction.predicted_home_score,
                            original_away_score=prediction.predicted_away_score,
                            original_home_win_prob=prediction.home_win_prob,
                            original_draw_prob=prediction.draw_prob,
                            original_away_win_prob=prediction.away_win_prob,
                            original_confidence=prediction.confidence,
                            optimized_home_score=opt_pred["predicted_score"]["home"],
                            optimized_away_score=opt_pred["predicted_score"]["away"],
                            optimized_home_win_prob=opt_pred["outcome_probabilities"]["home_win"],
                            optimized_draw_prob=opt_pred["outcome_probabilities"]["draw"],
                            optimized_away_win_prob=opt_pred["outcome_probabilities"]["away_win"],
                            optimized_confidence=opt_pred["confidence"],
                            blind_spots=opt.get("blind_spots", []),
                            calibration_issues=opt.get("calibration_issues", []),
                            optimization_reasoning=opt_pred.get("reasoning", "")
                        )
                        session.add(optimized_record)
                        results["optimizations_generated"] += 1

                        results["optimizations"].append({
                            "match_id": fixture.match_id,
                            "home_team": fixture.home_team,
                            "away_team": fixture.away_team,
                            "engine": prediction.prediction_method,
                            "blind_spots": opt.get("blind_spots", []),
                            "calibration_issues": opt.get("calibration_issues", [])
                        })

            except Exception as e:
                results["errors"].append({
                    "match_id": fixture.match_id,
                    "error": str(e)
                })

        session.commit()
        return results

    finally:
        close_prediction_session(session)


def calculate_optimization_patterns(engine_name: str) -> dict[str, Any]:
    """Analyze patterns in AI optimizations to derive calibration adjustments.

    Args:
        engine_name: Engine to analyze (e.g., "elo_odds", "hybrid")

    Returns:
        Suggested calibration parameters
    """
    session = get_prediction_session()
    try:
        # Get all optimizations for this engine
        optimizations = session.query(AIOptimizedPrediction).filter(
            AIOptimizedPrediction.original_engine.like(f"%{engine_name}%")
        ).all()

        if not optimizations:
            return {"status": "no_data", "message": f"No optimizations found for engine '{engine_name}'"}

        # Analyze systematic biases
        home_score_diffs = []
        away_score_diffs = []
        home_win_prob_diffs = []
        draw_prob_diffs = []
        away_win_prob_diffs = []
        confidence_diffs = []

        blind_spot_counts = {}
        calibration_issue_counts = {}

        for opt in optimizations:
            home_score_diffs.append(opt.optimized_home_score - opt.original_home_score)
            away_score_diffs.append(opt.optimized_away_score - opt.original_away_score)
            home_win_prob_diffs.append(opt.optimized_home_win_prob - opt.original_home_win_prob)
            draw_prob_diffs.append(opt.optimized_draw_prob - opt.original_draw_prob)
            away_win_prob_diffs.append(opt.optimized_away_win_prob - opt.original_away_win_prob)
            confidence_diffs.append(opt.optimized_confidence - opt.original_confidence)

            # Count blind spots
            for spot in opt.blind_spots or []:
                blind_spot_counts[spot] = blind_spot_counts.get(spot, 0) + 1

            # Count calibration issues
            for issue in opt.calibration_issues or []:
                calibration_issue_counts[issue] = calibration_issue_counts.get(issue, 0) + 1

        # Calculate average adjustments
        avg_home_score_adj = sum(home_score_diffs) / len(home_score_diffs)
        avg_away_score_adj = sum(away_score_diffs) / len(away_score_diffs)
        avg_home_win_prob_adj = sum(home_win_prob_diffs) / len(home_win_prob_diffs)
        avg_draw_prob_adj = sum(draw_prob_diffs) / len(draw_prob_diffs)
        avg_away_win_prob_adj = sum(away_win_prob_diffs) / len(away_win_prob_diffs)
        avg_confidence_adj = sum(confidence_diffs) / len(confidence_diffs)

        # Build calibration parameters
        calibration_params = {
            "home_score_bias": round(avg_home_score_adj, 3),
            "away_score_bias": round(avg_away_score_adj, 3),
            "home_win_prob_shift": round(avg_home_win_prob_adj, 3),
            "draw_prob_shift": round(avg_draw_prob_adj, 3),
            "away_win_prob_shift": round(avg_away_win_prob_adj, 3),
            "confidence_multiplier": round(1.0 + avg_confidence_adj, 3),
        }

        return {
            "status": "ok",
            "engine": engine_name,
            "samples": len(optimizations),
            "calibration_params": calibration_params,
            "top_blind_spots": sorted(blind_spot_counts.items(), key=lambda x: x[1], reverse=True)[:5],
            "top_calibration_issues": sorted(calibration_issue_counts.items(), key=lambda x: x[1], reverse=True)[:5],
            "analysis": {
                "avg_home_score_adjustment": round(avg_home_score_adj, 3),
                "avg_away_score_adjustment": round(avg_away_score_adj, 3),
                "avg_home_win_prob_adjustment": round(avg_home_win_prob_adj, 3),
                "avg_draw_prob_adjustment": round(avg_draw_prob_adj, 3),
                "avg_away_win_prob_adjustment": round(avg_away_win_prob_adj, 3),
                "avg_confidence_adjustment": round(avg_confidence_adj, 3)
            }
        }

    finally:
        close_prediction_session(session)


def save_engine_calibration(
    engine_name: str,
    calibration_params: dict[str, Any],
    based_on_matches: int,
    description: str = ""
) -> dict[str, Any]:
    """Save a new calibration version for an engine.

    Args:
        engine_name: Engine to calibrate
        calibration_params: Calibration parameters to apply
        based_on_matches: Number of matches this calibration is based on
        description: Optional description

    Returns:
        Created calibration record info
    """
    session = get_prediction_session()
    try:
        # Deactivate previous calibrations
        session.query(EngineCalibration).filter(
            EngineCalibration.engine_name == engine_name,
            EngineCalibration.is_active == 1
        ).update({"is_active": 0})

        # Get next version number
        latest = session.query(EngineCalibration).filter(
            EngineCalibration.engine_name == engine_name
        ).order_by(EngineCalibration.version.desc()).first()

        next_version = (latest.version + 1) if latest else 1

        # Create new calibration
        calibration = EngineCalibration(
            engine_name=engine_name,
            calibration_params=calibration_params,
            based_on_matches=based_on_matches,
            version=next_version,
            is_active=1
        )

        session.add(calibration)
        session.commit()

        return {
            "status": "ok",
            "calibration_id": calibration.id,
            "engine": engine_name,
            "version": next_version,
            "params": calibration_params
        }

    finally:
        close_prediction_session(session)


def get_active_calibration(engine_name: str) -> dict[str, Any] | None:
    """Get the currently active calibration for an engine.

    Args:
        engine_name: Engine name

    Returns:
        Calibration parameters or None if no active calibration
    """
    session = get_prediction_session()
    try:
        calibration = session.query(EngineCalibration).filter(
            EngineCalibration.engine_name == engine_name,
            EngineCalibration.is_active == 1
        ).first()

        if not calibration:
            return None

        return {
            "engine": engine_name,
            "version": calibration.version,
            "params": calibration.calibration_params,
            "based_on_matches": calibration.based_on_matches,
            "created_at": calibration.created_at.isoformat()
        }

    finally:
        close_prediction_session(session)


def apply_calibration_to_prediction(
    prediction: dict[str, Any],
    engine_name: str
) -> dict[str, Any]:
    """Apply active calibration adjustments to a prediction.

    Args:
        prediction: Prediction dict with predicted_score, outcome_probabilities, confidence
        engine_name: Engine that generated this prediction

    Returns:
        Calibrated prediction
    """
    calibration = get_active_calibration(engine_name)
    if not calibration:
        return prediction  # No calibration, return original

    params = calibration["params"]
    calibrated = prediction.copy()

    # Apply score biases
    if "home_score_bias" in params:
        calibrated["predicted_score"]["home"] += params["home_score_bias"]
    if "away_score_bias" in params:
        calibrated["predicted_score"]["away"] += params["away_score_bias"]

    # Clamp scores to valid range
    calibrated["predicted_score"]["home"] = max(0.0, calibrated["predicted_score"]["home"])
    calibrated["predicted_score"]["away"] = max(0.0, calibrated["predicted_score"]["away"])

    # Apply probability shifts
    probs = calibrated["outcome_probabilities"]
    probs["home_win"] += params.get("home_win_prob_shift", 0.0)
    probs["draw"] += params.get("draw_prob_shift", 0.0)
    probs["away_win"] += params.get("away_win_prob_shift", 0.0)

    # Normalize probabilities to sum to 1.0
    total = probs["home_win"] + probs["draw"] + probs["away_win"]
    if total > 0:
        probs["home_win"] /= total
        probs["draw"] /= total
        probs["away_win"] /= total

    # Clamp to valid range
    probs["home_win"] = max(0.0, min(1.0, probs["home_win"]))
    probs["draw"] = max(0.0, min(1.0, probs["draw"]))
    probs["away_win"] = max(0.0, min(1.0, probs["away_win"]))

    # Apply confidence multiplier
    calibrated["confidence"] *= params.get("confidence_multiplier", 1.0)
    calibrated["confidence"] = max(0.0, min(1.0, calibrated["confidence"]))

    return calibrated


async def run_full_auto_tuning_cycle(engine_name: str) -> dict[str, Any]:
    """Run complete auto-tuning cycle: analyze, optimize, learn, calibrate.

    Args:
        engine_name: Engine to tune (e.g., "elo_odds", "hybrid")

    Returns:
        Summary of tuning cycle results
    """
    # Step 1: Run AI optimization on all predictions
    optimization_results = await analyze_and_optimize_all_predictions(
        engine_filter=engine_name
    )

    if optimization_results["optimizations_generated"] == 0:
        return {
            "status": "no_data",
            "message": f"No optimizations generated for engine '{engine_name}'"
        }

    # Step 2: Analyze patterns to derive calibration
    pattern_analysis = calculate_optimization_patterns(engine_name)

    if pattern_analysis.get("status") != "ok":
        return pattern_analysis

    # Step 3: Save new calibration
    calibration_result = save_engine_calibration(
        engine_name=engine_name,
        calibration_params=pattern_analysis["calibration_params"],
        based_on_matches=pattern_analysis["samples"],
        description=f"Auto-tuned from {pattern_analysis['samples']} AI optimizations"
    )

    return {
        "status": "ok",
        "engine": engine_name,
        "optimization_summary": {
            "matches_processed": optimization_results["total_processed"],
            "optimizations_generated": optimization_results["optimizations_generated"],
            "errors": len(optimization_results["errors"])
        },
        "pattern_analysis": pattern_analysis["analysis"],
        "calibration": calibration_result,
        "top_blind_spots": pattern_analysis["top_blind_spots"],
        "top_calibration_issues": pattern_analysis["top_calibration_issues"]
    }
