"""Complete prediction pipeline orchestration.

This module ties together all prediction components:
1. Fetch team statistics from API
2. Fetch Elo ratings and betting odds
3. Choose prediction engine (Elo+Odds or Hybrid Rule+AI)
4. Calculate prediction factors
5. Run prediction engine
6. Save prediction to database
7. Record prediction history
"""

from datetime import datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.world_cup_prediction import (
    MatchFixture,
    MatchPrediction,
    PredictionHistory
)
from app.services.world_cup_factor_service import build_prediction_factors
from app.services.world_cup_prediction_engine import predict_match_score
from app.services.world_cup_elo_odds_engine import predict_match_elo_odds
from app.services.elo_ratings_service import get_elo_rating
from app.services.odds_cache_service import get_cached_odds
from app.services.world_cup_team_stats_service import (
    fetch_team_statistics,
    fetch_head_to_head,
    get_team_id_from_name,
)
from app.utils.prediction_db import get_prediction_session, close_prediction_session


# Prediction engine selection
PredictionEngine = Literal["elo_odds", "hybrid", "auto"]
DEFAULT_ENGINE: PredictionEngine = "auto"  # Auto-select based on data availability


def fetch_team_stats(team_name: str, team_id: int | None = None) -> dict[str, Any]:
    """Fetch team statistics from API-Football or fallback to mock data.

    Args:
        team_name: Team name
        team_id: Optional API-Football team ID

    Returns:
        Team statistics dictionary
    """
    # Try to get team ID if not provided
    if team_id is None:
        team_id = get_team_id_from_name(team_name)

    # Try to fetch real data if we have team ID and API configured
    if team_id is not None:
        league_id = int(settings.WORLD_CUP_API_FOOTBALL_LEAGUE_ID)
        season = settings.WORLD_CUP_API_FOOTBALL_SEASON

        stats = fetch_team_statistics(team_id, league_id, season)
        if stats:
            return stats

    # Fallback to mock data
    return generate_mock_team_stats(team_name)


def fetch_h2h_data(home_team: str, away_team: str, home_team_id: int | None = None, away_team_id: int | None = None) -> dict[str, Any]:
    """Fetch head-to-head data from API-Football or fallback to mock data.

    Args:
        home_team: Home team name
        away_team: Away team name
        home_team_id: Optional home team API-Football ID
        away_team_id: Optional away team API-Football ID

    Returns:
        Head-to-head statistics dictionary
    """
    # Try to get team IDs if not provided
    if home_team_id is None:
        home_team_id = get_team_id_from_name(home_team)
    if away_team_id is None:
        away_team_id = get_team_id_from_name(away_team)

    # Try to fetch real data if we have both team IDs
    if home_team_id is not None and away_team_id is not None:
        h2h = fetch_head_to_head(home_team_id, away_team_id)
        if h2h:
            # Convert API format to expected format
            return {
                "matches_played": h2h["matches_played"],
                "home_wins": h2h["team1_wins"],  # team1 = home in our call
                "draws": h2h["draws"],
                "away_wins": h2h["team2_wins"],
                "avg_goals_home": h2h["avg_goals_team1"],
                "avg_goals_away": h2h["avg_goals_team2"],
            }

    # Fallback to mock data
    return generate_mock_h2h_data(home_team, away_team)


def generate_mock_team_stats(team_name: str) -> dict[str, Any]:
    """Generate mock team statistics for testing.

    Used as fallback when API-Football data is unavailable.

    Args:
        team_name: Team name

    Returns:
        Mock team statistics
    """

    base_stats = {
        "goals_per_game": 1.8,
        "goals_conceded_per_game": 1.1,
        "wins": 3,
        "draws": 1,
        "losses": 1,
        "played": 5,
        "form": 0.6,
    }

    # Vary stats slightly by team (very simplified)
    if team_name in {"Brazil", "Argentina", "France", "Germany", "Spain", "England"}:
        base_stats["goals_per_game"] = 2.1
        base_stats["goals_conceded_per_game"] = 0.9
        base_stats["wins"] = 4
        base_stats["losses"] = 0
        base_stats["form"] = 0.9
    elif team_name in {"USA", "Mexico", "Netherlands", "Portugal"}:
        base_stats["goals_per_game"] = 1.9
        base_stats["goals_conceded_per_game"] = 1.0
        base_stats["wins"] = 3
        base_stats["form"] = 0.7

    return base_stats


def generate_mock_h2h_data(home_team: str, away_team: str) -> dict[str, Any]:
    """Generate mock head-to-head data.

    Used as fallback when API-Football data is unavailable.

    Args:
        home_team: Home team name
        away_team: Away team name

    Returns:
        Mock h2h statistics
    """

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
    engine: PredictionEngine = "auto",
    session: Session | None = None
) -> dict[str, Any]:
    """Run complete prediction pipeline for a match.

    Args:
        match_id: Match ID to predict
        trigger: What triggered this prediction (manual, daily_update, live_update, etc.)
        engine: Prediction engine to use ("elo_odds", "hybrid", "auto")
               - "elo_odds": Fast Elo+Odds engine (70-75% accuracy, <100ms)
               - "hybrid": Current Rule+AI engine (interpretable, 2-3s)
               - "auto": Auto-select based on data availability (default)
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

        # Step 2: Fetch Elo ratings and betting odds
        home_elo_data = await get_elo_rating(match.home_team)
        away_elo_data = await get_elo_rating(match.away_team)

        odds = await get_cached_odds(
            match.home_team,
            match.away_team,
            ttl_seconds=3600,  # 1 hour cache
            commence_time=match.kickoff_utc
        )

        # Step 3: Choose prediction engine
        selected_engine = engine
        if engine == "auto":
            # Auto-select: prefer Elo+Odds if odds available
            if odds and odds.get("source") != "fallback":
                selected_engine = "elo_odds"
            else:
                selected_engine = "hybrid"

        # Step 4: Run prediction based on selected engine
        if selected_engine == "elo_odds":
            # Use fast Elo+Odds engine
            prediction = predict_match_elo_odds(
                home_team=match.home_team,
                away_team=match.away_team,
                elo_home=home_elo_data["elo_rating"],
                elo_away=away_elo_data["elo_rating"],
                odds_home=odds["home"] if odds else None,
                odds_draw=odds["draw"] if odds else None,
                odds_away=odds["away"] if odds else None,
            )

            # Convert to standard format
            prediction_result = {
                "predicted_score": prediction["predicted_score"],
                "outcome_probabilities": prediction["outcome_probabilities"],
                "confidence": prediction["confidence"],
                "prediction_method": prediction["prediction_method"],
                "elo_ratings": prediction["elo_ratings"],
                "has_betting_odds": prediction["has_betting_odds"],
                "rule_score": None,
                "ai_score": None,
                "ai_reasoning": None,
                "key_factors": []
            }

        else:  # selected_engine == "hybrid"
            # Use comprehensive hybrid engine
            # Step 4a: Fetch team statistics (try API-Football, fallback to mock)
            home_team_id = getattr(match, 'home_team_id', None)
            away_team_id = getattr(match, 'away_team_id', None)

            home_stats = fetch_team_stats(match.home_team, home_team_id)
            away_stats = fetch_team_stats(match.away_team, away_team_id)
            h2h_data = fetch_h2h_data(match.home_team, match.away_team, home_team_id, away_team_id)

            # Step 4b: Calculate prediction factors
            factors = build_prediction_factors(
                home_team_name=match.home_team,
                away_team_name=match.away_team,
                home_team_stats=home_stats,
                away_team_stats=away_stats,
                stage=match.stage,
                h2h_data=h2h_data
            )

            # Step 4c: Run prediction engine
            prediction_result = await predict_match_score(
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
            existing.predicted_home_score = prediction_result["predicted_score"]["home"]
            existing.predicted_away_score = prediction_result["predicted_score"]["away"]
            existing.home_win_prob = prediction_result["outcome_probabilities"]["home_win"]
            existing.draw_prob = prediction_result["outcome_probabilities"]["draw"]
            existing.away_win_prob = prediction_result["outcome_probabilities"]["away_win"]
            existing.confidence = prediction_result["confidence"]
            existing.prediction_method = prediction_result["prediction_method"]
            existing.rule_home_score = prediction_result.get("rule_score", {}).get("home") if prediction_result.get("rule_score") else None
            existing.rule_away_score = prediction_result.get("rule_score", {}).get("away") if prediction_result.get("rule_score") else None
            existing.ai_home_score = prediction_result.get("ai_score", {}).get("home") if prediction_result.get("ai_score") else None
            existing.ai_away_score = prediction_result.get("ai_score", {}).get("away") if prediction_result.get("ai_score") else None
            existing.factors = getattr(prediction_result, "factors", {})
            existing.ai_reasoning = prediction_result.get("ai_reasoning")
            existing.key_factors = prediction_result.get("key_factors", [])
            existing.last_updated = datetime.utcnow()
            action = "updated"
        else:
            # Create new prediction
            new_pred = MatchPrediction(
                match_id=match_id,
                predicted_home_score=prediction_result["predicted_score"]["home"],
                predicted_away_score=prediction_result["predicted_score"]["away"],
                home_win_prob=prediction_result["outcome_probabilities"]["home_win"],
                draw_prob=prediction_result["outcome_probabilities"]["draw"],
                away_win_prob=prediction_result["outcome_probabilities"]["away_win"],
                confidence=prediction_result["confidence"],
                prediction_method=prediction_result["prediction_method"],
                rule_home_score=prediction_result.get("rule_score", {}).get("home") if prediction_result.get("rule_score") else None,
                rule_away_score=prediction_result.get("rule_score", {}).get("away") if prediction_result.get("rule_score") else None,
                ai_home_score=prediction_result.get("ai_score", {}).get("home") if prediction_result.get("ai_score") else None,
                ai_away_score=prediction_result.get("ai_score", {}).get("away") if prediction_result.get("ai_score") else None,
                factors=getattr(prediction_result, "factors", {}),
                ai_reasoning=prediction_result.get("ai_reasoning"),
                key_factors=prediction_result.get("key_factors", [])
            )
            session.add(new_pred)
            action = "created"

        # Step 6: Record prediction history snapshot
        history_entry = PredictionHistory(
            match_id=match_id,
            timestamp=datetime.utcnow(),
            predicted_home_score=prediction_result["predicted_score"]["home"],
            predicted_away_score=prediction_result["predicted_score"]["away"],
            home_win_prob=prediction_result["outcome_probabilities"]["home_win"],
            draw_prob=prediction_result["outcome_probabilities"]["draw"],
            away_win_prob=prediction_result["outcome_probabilities"]["away_win"],
            confidence=prediction_result["confidence"],
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
            "predicted_score": prediction_result["predicted_score"],
            "confidence": prediction_result["confidence"],
            "prediction_method": prediction_result["prediction_method"],
            "engine_used": selected_engine,
            "has_betting_odds": prediction_result.get("has_betting_odds", False)
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
    trigger: str = "batch",
    engine: PredictionEngine = "auto"
) -> dict[str, Any]:
    """Run prediction pipeline for multiple matches.

    Args:
        match_ids: List of match IDs to predict (None = all remaining matches)
        trigger: What triggered this batch
        engine: Prediction engine to use ("elo_odds", "hybrid", "auto")

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
            "elo_odds_count": 0,
            "hybrid_count": 0,
            "predictions": []
        }

        for match in matches:
            result = await run_prediction_pipeline(
                match.match_id,
                trigger=trigger,
                engine=engine,
                session=session
            )

            if result["status"] == "ok":
                results["succeeded"] += 1
                # Track which engine was used
                if result.get("engine_used") == "elo_odds":
                    results["elo_odds_count"] += 1
                elif result.get("engine_used") == "hybrid":
                    results["hybrid_count"] += 1
            elif result["status"] == "skipped":
                results["skipped"] += 1
            else:
                results["failed"] += 1

            results["predictions"].append(result)

        return results

    finally:
        close_prediction_session(session)
