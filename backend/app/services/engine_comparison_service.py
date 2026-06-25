"""Service for comparing prediction engine performance."""

from sqlalchemy import func
from app.models.world_cup_prediction import MatchFixture, PredictionHistory
from app.utils.prediction_db import get_prediction_session, close_prediction_session


def _bucket_engine(method: str) -> str:
    """Map a prediction_method string to an engine bucket."""
    if "elo_odds" in method or ("elo" in method and "odds" in method):
        return "elo_odds"
    return "hybrid"


def _credit_engine_prediction(engines_stats, engine, match, last_prediction):
    """Accumulate one engine's prediction for a finished match into engines_stats."""
    # Initialize engine stats
    if engine not in engines_stats:
        engines_stats[engine] = {
            "total_matches": 0,
            "exact_score": 0,
            "correct_outcome": 0,
            "goal_diff_correct": 0,
            "total_score_error": 0,
            "predictions": []
        }

    stats = engines_stats[engine]
    stats["total_matches"] += 1

    # Predicted values
    pred_home = round(last_prediction.predicted_home_score)
    pred_away = round(last_prediction.predicted_away_score)
    pred_diff = pred_home - pred_away

    # Actual values
    actual_home = match.home_score
    actual_away = match.away_score
    actual_diff = actual_home - actual_away

    # Determine outcomes
    if actual_home > actual_away:
        actual_outcome = "home_win"
        pred_outcome_prob = last_prediction.home_win_prob
    elif actual_away > actual_home:
        actual_outcome = "away_win"
        pred_outcome_prob = last_prediction.away_win_prob
    else:
        actual_outcome = "draw"
        pred_outcome_prob = last_prediction.draw_prob

    if pred_home > pred_away:
        pred_outcome = "home_win"
    elif pred_away > pred_home:
        pred_outcome = "away_win"
    else:
        pred_outcome = "draw"

    # Calculate metrics
    if pred_home == actual_home and pred_away == actual_away:
        stats["exact_score"] += 1

    if pred_outcome == actual_outcome:
        stats["correct_outcome"] += 1

    if pred_diff == actual_diff:
        stats["goal_diff_correct"] += 1

    score_error = abs(pred_home - actual_home) + abs(pred_away - actual_away)
    stats["total_score_error"] += score_error

    # Store individual prediction for detail view
    stats["predictions"].append({
        "match_id": match.match_id,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "predicted_score": {"home": pred_home, "away": pred_away},
        "actual_score": {"home": actual_home, "away": actual_away},
        "score_error": score_error,
        "outcome_correct": pred_outcome == actual_outcome,
        "confidence": last_prediction.confidence,
        "outcome_probability": pred_outcome_prob
    })


def calculate_engine_accuracy():
    """Calculate accuracy metrics for each prediction engine.

    Returns:
        dict: Engine comparison statistics
    """
    session = get_prediction_session()
    try:
        # Get all finished matches
        finished_matches = session.query(MatchFixture).filter(
            MatchFixture.status == "finished",
            MatchFixture.home_score.isnot(None),
            MatchFixture.away_score.isnot(None)
        ).all()

        if not finished_matches:
            return {
                "status": "ok",
                "message": "No finished matches yet",
                "engines": {}
            }

        engines_stats = {}

        for match in finished_matches:
            # Get every prediction recorded for this match (newest first).
            # Dual-engine recording stores both the selected engine and the
            # alternative engine per match, so a single match can have rows for
            # both elo_odds and hybrid.
            predictions = session.query(PredictionHistory).filter(
                PredictionHistory.match_id == match.match_id
            ).order_by(PredictionHistory.timestamp.desc()).all()

            if not predictions:
                continue

            # Keep the latest prediction per engine bucket so each engine is
            # credited independently. Previously only the single latest row was
            # counted, so for matches predicted by both engines one engine was
            # silently dropped from the comparison.
            latest_by_engine: dict[str, PredictionHistory] = {}
            for p in predictions:
                if not p.prediction_method:
                    continue
                bucket = _bucket_engine(p.prediction_method)
                # predictions are newest-first, so the first row seen per bucket
                # is the most recent one for that engine.
                if bucket not in latest_by_engine:
                    latest_by_engine[bucket] = p

            for engine, last_prediction in latest_by_engine.items():
                _credit_engine_prediction(engines_stats, engine, match, last_prediction)

        # Calculate percentages and averages
        result = {}
        for engine, stats in engines_stats.items():
            total = stats["total_matches"]
            result[engine] = {
                "total_matches": total,
                "exact_score_rate": stats["exact_score"] / total if total > 0 else 0,
                "outcome_accuracy": stats["correct_outcome"] / total if total > 0 else 0,
                "goal_diff_accuracy": stats["goal_diff_correct"] / total if total > 0 else 0,
                "avg_score_error": stats["total_score_error"] / total if total > 0 else 0,
                "predictions": stats["predictions"]
            }

        return {
            "status": "ok",
            "engines": result
        }

    finally:
        close_prediction_session(session)
