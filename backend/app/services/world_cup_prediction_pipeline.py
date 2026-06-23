"""Complete prediction pipeline orchestration.

This module ties together all prediction components:
1. Fetch team statistics from API
2. Calculate prediction factors
3. Run hybrid prediction engine
4. Save prediction to database
5. Record prediction history
"""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.world_cup_prediction import (
    MatchFixture,
    MatchPrediction,
    PredictionHistory
)
from app.services.world_cup_factor_service import build_prediction_factors
from app.services.world_cup_prediction_engine import predict_match_score
from app.utils.prediction_db import get_prediction_session, close_prediction_session


def generate_mock_team_stats(team_name: str, is_home: bool = False) -> dict[str, Any]:
    """Generate mock team statistics for testing.

    TODO: Replace with real API-Football team stats fetching.

    Args:
        team_name: Team name
        is_home: Whether team is playing at home

    Returns:
        Mock team statistics
    """

    # Mock data - in production, fetch from API-Football
    # GET /teams/statistics?team={team_id}&league={league_id}&season={season}

    base_stats = {
        "goals_per_game": 1.8,
        "goals_conceded_per_game": 1.1,
        "wins": 3,
        "draws": 1,
        "losses": 1,
        "fifa_ranking": 15,
        "injury_impact": -0.05,
        "last_match_date": "2026-06-20T20:00:00Z"
    }

    # Vary stats slightly by team (very simplified)
    if team_name in {"Brazil", "Argentina", "France", "Germany", "Spain", "England"}:
        base_stats["goals_per_game"] = 2.1
        base_stats["goals_conceded_per_game"] = 0.9
        base_stats["fifa_ranking"] = 8
        base_stats["wins"] = 4
        base_stats["losses"] = 0
    elif team_name in {"USA", "Mexico", "Netherlands", "Portugal"}:
        base_stats["goals_per_game"] = 1.9
        base_stats["goals_conceded_per_game"] = 1.0
        base_stats["fifa_ranking"] = 12
        base_stats["wins"] = 3

    return base_stats


def generate_mock_h2h_data(home_team: str, away_team: str) -> dict[str, Any]:
    """Generate mock head-to-head data.

    TODO: Replace with real API-Football h2h data.

    Args:
        home_team: Home team name
        away_team: Away team name

    Returns:
        Mock h2h statistics
    """

    # Mock data - in production, fetch from API-Football
    # GET /fixtures/headtohead?h2h={team1_id}-{team2_id}

    return {
        "matches_played": 10,
        "home_wins": 4,
        "draws": 3,
        "away_wins": 3,
        "avg_goals_home": 1.7,
        "avg_goals_away": 1.4
    }


async def run_prediction_pipeline(
    match_id: str,
    trigger: str = "manual",
    session: Session | None = None
) -> dict[str, Any]:
    """Run complete prediction pipeline for a match.

    Args:
        match_id: Match ID to predict
        trigger: What triggered this prediction (manual, daily_update, live_update, etc.)
        session: Database session (creates one if None)

    Returns:
        Result summary with prediction details
    """

    should_close = session is None
    if session is None:
        session = get_prediction_session()

    try:
        # Step 1: Get match fixture
        match = session.query(MatchFixture).filter_by(match_id=match_id).first()
        if not match:
            return {"status": "error", "error": "Match not found"}

        # Don't predict finished matches
        if match.status == "finished":
            return {"status": "skipped", "reason": "Match already finished"}

        # Step 2: Fetch team statistics
        # TODO: Replace mock data with real API-Football calls
        home_stats = generate_mock_team_stats(match.home_team, is_home=True)
        away_stats = generate_mock_team_stats(match.away_team, is_home=False)
        h2h_data = generate_mock_h2h_data(match.home_team, match.away_team)

        # Step 3: Calculate prediction factors
        factors = build_prediction_factors(
            home_team_name=match.home_team,
            away_team_name=match.away_team,
            home_team_stats=home_stats,
            away_team_stats=away_stats,
            stage=match.stage,
            h2h_data=h2h_data
        )

        # Step 4: Run prediction engine
        prediction = await predict_match_score(
            home_team=match.home_team,
            away_team=match.away_team,
            kickoff_utc=match.kickoff_utc,
            stage=match.stage,
            factors=factors
        )

        # Step 5: Save or update prediction in database
        existing = session.query(MatchPrediction).filter_by(match_id=match_id).first()

        if existing:
            # Update existing prediction
            existing.predicted_home_score = prediction["predicted_score"]["home"]
            existing.predicted_away_score = prediction["predicted_score"]["away"]
            existing.home_win_prob = prediction["outcome_probabilities"]["home_win"]
            existing.draw_prob = prediction["outcome_probabilities"]["draw"]
            existing.away_win_prob = prediction["outcome_probabilities"]["away_win"]
            existing.confidence = prediction["confidence"]
            existing.prediction_method = prediction["prediction_method"]
            existing.rule_home_score = prediction.get("rule_score", {}).get("home")
            existing.rule_away_score = prediction.get("rule_score", {}).get("away")
            existing.ai_home_score = prediction.get("ai_score", {}).get("home") if prediction.get("ai_score") else None
            existing.ai_away_score = prediction.get("ai_score", {}).get("away") if prediction.get("ai_score") else None
            existing.factors = factors
            existing.ai_reasoning = prediction.get("ai_reasoning")
            existing.key_factors = prediction.get("key_factors", [])
            existing.last_updated = datetime.utcnow()
            action = "updated"
        else:
            # Create new prediction
            new_pred = MatchPrediction(
                match_id=match_id,
                predicted_home_score=prediction["predicted_score"]["home"],
                predicted_away_score=prediction["predicted_score"]["away"],
                home_win_prob=prediction["outcome_probabilities"]["home_win"],
                draw_prob=prediction["outcome_probabilities"]["draw"],
                away_win_prob=prediction["outcome_probabilities"]["away_win"],
                confidence=prediction["confidence"],
                prediction_method=prediction["prediction_method"],
                rule_home_score=prediction.get("rule_score", {}).get("home"),
                rule_away_score=prediction.get("rule_score", {}).get("away"),
                ai_home_score=prediction.get("ai_score", {}).get("home") if prediction.get("ai_score") else None,
                ai_away_score=prediction.get("ai_score", {}).get("away") if prediction.get("ai_score") else None,
                factors=factors,
                ai_reasoning=prediction.get("ai_reasoning"),
                key_factors=prediction.get("key_factors", [])
            )
            session.add(new_pred)
            action = "created"

        # Step 6: Record prediction history snapshot
        history_entry = PredictionHistory(
            match_id=match_id,
            timestamp=datetime.utcnow(),
            predicted_home_score=prediction["predicted_score"]["home"],
            predicted_away_score=prediction["predicted_score"]["away"],
            home_win_prob=prediction["outcome_probabilities"]["home_win"],
            draw_prob=prediction["outcome_probabilities"]["draw"],
            away_win_prob=prediction["outcome_probabilities"]["away_win"],
            confidence=prediction["confidence"],
            trigger=trigger
        )
        session.add(history_entry)

        session.commit()

        return {
            "status": "ok",
            "action": action,
            "match_id": match_id,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "predicted_score": prediction["predicted_score"],
            "confidence": prediction["confidence"],
            "prediction_method": prediction["prediction_method"]
        }

    except Exception as e:
        session.rollback()
        return {
            "status": "error",
            "match_id": match_id,
            "error": str(e)
        }

    finally:
        if should_close:
            close_prediction_session(session)


async def batch_predict_matches(
    match_ids: list[str] | None = None,
    trigger: str = "batch"
) -> dict[str, Any]:
    """Run prediction pipeline for multiple matches.

    Args:
        match_ids: List of match IDs to predict (None = all remaining matches)
        trigger: What triggered this batch

    Returns:
        Batch result summary
    """

    session = get_prediction_session()

    try:
        # Get matches to predict
        if match_ids:
            matches = session.query(MatchFixture).filter(
                MatchFixture.match_id.in_(match_ids)
            ).all()
        else:
            # Predict all remaining matches
            matches = session.query(MatchFixture).filter(
                MatchFixture.status.in_(["scheduled", "in_play"])
            ).order_by(MatchFixture.kickoff_utc).all()

        results = {
            "status": "ok",
            "total": len(matches),
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "predictions": []
        }

        for match in matches:
            result = await run_prediction_pipeline(
                match.match_id,
                trigger=trigger,
                session=session
            )

            if result["status"] == "ok":
                results["succeeded"] += 1
            elif result["status"] == "skipped":
                results["skipped"] += 1
            else:
                results["failed"] += 1

            results["predictions"].append(result)

        return results

    finally:
        close_prediction_session(session)
