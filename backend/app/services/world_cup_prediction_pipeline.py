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

from datetime import datetime, timezone
from typing import Any, Literal
import logging
import time

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.world_cup_prediction import (
    MatchFixture,
    MatchPrediction,
    PredictionHistory
)
from app.services.world_cup_factor_service import build_prediction_factors
from app.services.world_cup_engines import get_engine
from app.services.elo_ratings_service import get_elo_rating
from app.services.odds_cache_service import get_cached_odds
from app.services.world_cup_team_stats_service import (
    fetch_team_statistics,
    fetch_head_to_head,
    get_team_id_from_name,
)
from app.services.world_cup_enhanced_factors import calculate_comprehensive_factors
from app.services.engine_auto_tuning_service import apply_calibration_to_prediction
from app.services.world_cup_confidence_calibration import apply_confidence_calibration
from app.services.world_cup_betting_analysis import analyze_betting_markets
from app.services.world_cup_tactical_profiles import format_tactical_summary
from app.services.world_cup_weather_service import get_match_weather
from app.utils.prediction_db import get_prediction_session, close_prediction_session

logger = logging.getLogger(__name__)


# Prediction engine selection
PredictionEngine = Literal["elo_odds", "hybrid", "auto"]
DEFAULT_ENGINE: PredictionEngine = "auto"  # Auto-select based on data availability


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _has_match_started(match: MatchFixture, now: datetime | None = None) -> bool:
    kickoff = getattr(match, "kickoff_utc", None)
    if kickoff is None:
        return False
    current = _utc_naive(now or datetime.now(timezone.utc))
    return _utc_naive(kickoff) <= current


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
    stats = generate_mock_team_stats(team_name)
    stats["data_source"] = "mock"  # Mark as mock for transparency
    return stats


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
    h2h = generate_mock_h2h_data(home_team, away_team)
    h2h["data_source"] = "mock"  # Mark as mock for transparency
    return h2h


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


def _detect_group_stage_status(match: MatchFixture, session: Session) -> dict[str, Any] | None:
    """Detect group stage final round status for both teams.

    In the final round of group stage matches, some teams may have already
    qualified (>= 6 points) or been effectively eliminated (0 points with
    only 1 match remaining). This affects their motivation and lineup choices.

    Args:
        match: The match fixture being predicted
        session: Database session

    Returns:
        Dict with "home" and "away" status values, or None if not applicable.
        Status values: "qualified", "eliminated", or None.
    """
    stage = match.stage
    if stage not in ("group_stage", "Group Stage"):
        return None

    group = match.group
    if not group:
        return None

    # Query all matches in this group
    group_matches = session.query(MatchFixture).filter(
        MatchFixture.group == group
    ).all()

    finished_matches = [m for m in group_matches if m.status == "finished"]
    finished_count = len(finished_matches)

    # Final round: >= 4 finished matches in the group (of 6 total).
    # At this point the current (unfinished) match is one of the last 2.
    if finished_count < 4:
        return None

    # Calculate points and matches played for each team from finished matches
    team_points: dict[str, int] = {}
    team_matches_played: dict[str, int] = {}

    for fm in finished_matches:
        home_team = fm.home_team
        away_team = fm.away_team
        hs = fm.home_score
        as_ = fm.away_score

        if hs is None or as_ is None:
            continue

        team_matches_played[home_team] = team_matches_played.get(home_team, 0) + 1
        team_matches_played[away_team] = team_matches_played.get(away_team, 0) + 1

        if hs > as_:
            team_points[home_team] = team_points.get(home_team, 0) + 3
            team_points[away_team] = team_points.get(away_team, 0)
        elif hs < as_:
            team_points[away_team] = team_points.get(away_team, 0) + 3
            team_points[home_team] = team_points.get(home_team, 0)
        else:
            team_points[home_team] = team_points.get(home_team, 0) + 1
            team_points[away_team] = team_points.get(away_team, 0) + 1

    # Determine status for home and away teams
    home_status = None
    away_status = None

    home_points = team_points.get(match.home_team, 0)
    away_points = team_points.get(match.away_team, 0)
    home_played = team_matches_played.get(match.home_team, 0)
    away_played = team_matches_played.get(match.away_team, 0)

    # Qualified: >= 6 points (2 wins guarantees advancement)
    if home_points >= 6:
        home_status = "qualified"
    if away_points >= 6:
        away_status = "qualified"

    # Eliminated: 0 points and this is their last match (already played 2)
    if home_points == 0 and home_played == 2:
        home_status = "eliminated"
    if away_points == 0 and away_played == 2:
        away_status = "eliminated"

    if home_status is None and away_status is None:
        return None

    return {"home": home_status, "away": away_status}


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

    # Performance monitoring
    pipeline_start_time = time.perf_counter()
    data_fetch_start = None
    data_fetch_time = None
    engine_exec_time = None
    
    should_close = session is None
    if session is None:
        session = get_prediction_session()

    try:
        # Step 1: Get match fixture
        match = session.query(MatchFixture).filter_by(match_id=match_id).first()
        if not match:
            return {"status": "error", "error": "Match not found"}

        # Don't predict matches that have already started or finished.
        # Status can lag behind kickoff when an upstream fixture sync is late,
        # so kickoff time is the durable freeze boundary for pre-match scores.
        if match.status == "finished":
            return {"status": "skipped", "reason": "Match already finished"}
        if match.status == "in_play" or _has_match_started(match):
            return {"status": "skipped", "reason": "Match already started"}

        # Step 2: Fetch Elo ratings and betting odds
        home_elo_data = await get_elo_rating(match.home_team)
        away_elo_data = await get_elo_rating(match.away_team)

        odds = await get_cached_odds(
            match.home_team,
            match.away_team,
            ttl_seconds=3600,  # 1 hour cache
            commence_time=match.kickoff_utc
        )

        # End data fetch timing
        if data_fetch_start:
            data_fetch_time = (time.perf_counter() - data_fetch_start) * 1000  # Convert to ms
        
        # Step 3: Choose prediction engine
        selected_engine = engine
        if engine == "auto":
            # Auto-select: use integrated mode if odds available, else hybrid
            if odds and odds.get("source") != "fallback":
                selected_engine = "integrated"
            else:
                selected_engine = "hybrid"

        # Step 3b: Calculate prediction factors (needed for hybrid and integrated)
        factors = None
        if selected_engine in ("hybrid", "integrated"):
            home_team_id = getattr(match, 'home_team_id', None)
            away_team_id = getattr(match, 'away_team_id', None)

            home_stats = fetch_team_stats(match.home_team, home_team_id)
            away_stats = fetch_team_stats(match.away_team, away_team_id)
            h2h_data = fetch_h2h_data(match.home_team, match.away_team, home_team_id, away_team_id)

            # Track data quality with enhanced metrics
            data_quality = "real"
            if home_stats.get("data_source") == "mock" or away_stats.get("data_source") == "mock":
                data_quality = "mock"
            if h2h_data.get("data_source") == "mock":
                data_quality = "mock" if data_quality != "real" else "partial"
            
            # Enhanced data quality metrics
            data_quality_metrics = {
                "quality": data_quality,
                "has_elo": home_elo_data is not None and away_elo_data is not None,
                "has_odds": odds is not None and odds.get("source") != "fallback",
                "has_h2h": h2h_data and h2h_data.get("data_source") == "real",
                "has_weather": False,  # Will be set after weather fetch
                "elo_source": f"{home_elo_data.get('source', 'unknown')}/{away_elo_data.get('source', 'unknown')}",
                "odds_source": odds.get("source") if odds else "none",
                "stats_source": f"{home_stats.get('data_source', 'unknown')}/{away_stats.get('data_source', 'unknown')}",
            }

            # Fetch weather data for the match venue
            weather = get_match_weather(
                venue=getattr(match, 'venue', None),
                city=getattr(match, 'city', None),
                match_date=match.kickoff_utc.isoformat() if match.kickoff_utc else None,
            )
            if weather:
                data_quality_metrics["has_weather"] = True

            # Calculate data freshness (age in appropriate units)
            now = datetime.now(timezone.utc)
            
            # Elo ratings age (days)
            elo_age_days = None
            if home_elo_data and 'updated_at' in home_elo_data:
                elo_updated = datetime.fromisoformat(home_elo_data['updated_at'].replace('Z', '+00:00'))
                elo_age_days = (now - elo_updated).total_seconds() / 86400
            
            # Odds age (minutes)
            odds_age_minutes = None
            if odds and 'fetched_at' in odds:
                odds_fetched = datetime.fromisoformat(odds['fetched_at'].replace('Z', '+00:00'))
                odds_age_minutes = (now - odds_fetched).total_seconds() / 60
            
            # Stats age (hours) - use home_stats as proxy
            stats_age_hours = None
            if home_stats and 'updated_at' in home_stats:
                stats_updated = datetime.fromisoformat(home_stats['updated_at'].replace('Z', '+00:00'))
                stats_age_hours = (now - stats_updated).total_seconds() / 3600
            
            # Add freshness to metrics
            data_quality_metrics['elo_age_days'] = round(elo_age_days, 2) if elo_age_days is not None else None
            data_quality_metrics['odds_age_minutes'] = round(odds_age_minutes, 2) if odds_age_minutes is not None else None
            data_quality_metrics['stats_age_hours'] = round(stats_age_hours, 2) if stats_age_hours is not None else None
            
            # Calculate composite quality score (0-100)
            quality_score = 0.0
            
            # Coverage component (40 points max)
            coverage_score = 0
            if data_quality_metrics['has_elo']:
                coverage_score += 10
            if data_quality_metrics['has_odds']:
                coverage_score += 15
            if data_quality_metrics['has_h2h']:
                coverage_score += 10
            if data_quality_metrics['has_weather']:
                coverage_score += 5
            
            # Freshness component (40 points max)
            freshness_score = 0
            if elo_age_days is not None:
                # Elo: fresh if < 7 days, stale if > 30 days
                elo_freshness = max(0, min(1, 1 - (elo_age_days - 7) / 23))
                freshness_score += elo_freshness * 10
            if odds_age_minutes is not None:
                # Odds: fresh if < 30 min, stale if > 120 min
                odds_freshness = max(0, min(1, 1 - (odds_age_minutes - 30) / 90))
                freshness_score += odds_freshness * 20
            if stats_age_hours is not None:
                # Stats: fresh if < 24h, stale if > 72h
                stats_freshness = max(0, min(1, 1 - (stats_age_hours - 24) / 48))
                freshness_score += stats_freshness * 10
            
            # Quality level component (20 points max)
            if data_quality == 'real':
                quality_level_score = 20
            elif data_quality == 'partial':
                quality_level_score = 12
            else:  # mock
                quality_level_score = 5
            
            quality_score = coverage_score + freshness_score + quality_level_score
            data_quality_metrics['quality_score'] = round(quality_score, 1)

            enhanced_factors = calculate_comprehensive_factors(
                home_team_name=match.home_team,
                away_team_name=match.away_team,
                home_team_stats=home_stats,
                away_team_stats=away_stats,
                h2h_data=h2h_data,
                betting_odds=odds if odds and odds.get("source") != "fallback" else None,
                context={"stage": match.stage, "match_id": match.match_id, "weather": weather},
            )

            factors = build_prediction_factors(
                home_team_name=match.home_team,
                away_team_name=match.away_team,
                home_team_stats=home_stats,
                away_team_stats=away_stats,
                stage=match.stage,
                h2h_data=h2h_data,
                match_date=match.kickoff_utc.isoformat() if match.kickoff_utc else None,
                match_id=match.match_id
            )
            factors["enhanced"] = enhanced_factors
            factors["data_quality"] = data_quality
            factors["data_quality_metrics"] = data_quality_metrics

            # Detect group stage final round status (qualified/eliminated teams)
            group_status = _detect_group_stage_status(match, session)
            if group_status:
                factors["group_status"] = group_status
                logger.info(
                    "Group stage final round detected for match %s (group %s): home=%s, away=%s",
                    match.match_id,
                    match.group,
                    group_status.get("home"),
                    group_status.get("away"),
                )

        # Determine if this is a knockout match
        is_knockout = match.stage in {
            "round_of_16", "quarterfinal", "semifinal", "final",
            "Round of 16", "Quarterfinal", "Semifinal", "Final",
        }

        # Step 4: Run prediction based on selected engine
        engine_start_time = time.perf_counter()
        if selected_engine == "elo_odds":
            # Use fast Elo+Odds engine
            prediction = get_engine("elo_odds")(
                home_team=match.home_team,
                away_team=match.away_team,
                elo_home=home_elo_data["elo_rating"],
                elo_away=away_elo_data["elo_rating"],
                odds_home=odds["home"] if odds else None,
                odds_draw=odds["draw"] if odds else None,
                odds_away=odds["away"] if odds else None,
                is_knockout=is_knockout,
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
                "key_factors": [],
                "score_probability_matrix": prediction.get("score_probability_matrix"),
                "top_5_scores": prediction.get("top_5_scores"),
                "prediction_interval": prediction.get("prediction_interval"),
            }

        elif selected_engine == "integrated":
            # Integrated engine: run both engines and fuse results
            # This combines market signals (elo_odds) with contextual factors (hybrid)
            elo_prediction = get_engine("elo_odds")(
                home_team=match.home_team,
                away_team=match.away_team,
                elo_home=home_elo_data["elo_rating"],
                elo_away=away_elo_data["elo_rating"],
                odds_home=odds["home"] if odds else None,
                odds_draw=odds["draw"] if odds else None,
                odds_away=odds["away"] if odds else None,
                is_knockout=is_knockout,
            )

            # Also run hybrid engine with factors
            hybrid_prediction = await get_engine("hybrid")(
                home_team=match.home_team,
                away_team=match.away_team,
                kickoff_utc=match.kickoff_utc,
                stage=match.stage,
                factors=factors,
            )

            # Fuse: elo_odds + hybrid with dynamic weights based on data quality
            # Default: trust market signals (elo_odds) more when all data is real
            elo_weight = 0.70
            if data_quality == "mock":
                # Team stats are fake - reduce reliance on elo_odds
                elo_weight = min(elo_weight, 0.40)
            if odds and odds.get("source") and ("fallback" in odds.get("source") or "default" in odds.get("source")):
                # Odds are fake - reduce reliance on elo_odds
                elo_weight = min(elo_weight, 0.35)
            home_elo_source = home_elo_data.get("source", "")
            away_elo_source = away_elo_data.get("source", "")
            if ("estimated" in home_elo_source or "default" in home_elo_source or
                "estimated" in away_elo_source or "default" in away_elo_source):
                # Elo ratings are estimated/default - reduce reliance on elo_odds
                elo_weight = min(elo_weight, 0.45)
            hybrid_weight = 1.0 - elo_weight

            logger.info("Integrated engine weights: elo=%.2f hybrid=%.2f (data_quality=%s, odds_source=%s, elo_source=%s/%s)",
                        elo_weight, hybrid_weight, data_quality,
                        odds.get("source") if odds else "none",
                        home_elo_data.get("source", "unknown"),
                        away_elo_data.get("source", "unknown"))

            fused_home = elo_prediction["predicted_score"]["home"] * elo_weight + \
                         hybrid_prediction["predicted_score"]["home"] * hybrid_weight
            fused_away = elo_prediction["predicted_score"]["away"] * elo_weight + \
                         hybrid_prediction["predicted_score"]["away"] * hybrid_weight

            # Fuse outcome probabilities
            elo_probs = elo_prediction["outcome_probabilities"]
            hybrid_probs = hybrid_prediction["outcome_probabilities"]
            fused_probs = {
                "home_win": elo_probs["home_win"] * elo_weight + hybrid_probs["home_win"] * hybrid_weight,
                "draw": elo_probs["draw"] * elo_weight + hybrid_probs["draw"] * hybrid_weight,
                "away_win": elo_probs["away_win"] * elo_weight + hybrid_probs["away_win"] * hybrid_weight,
            }

            # Confidence: average of both, boosted if they agree
            elo_conf = elo_prediction["confidence"]
            hybrid_conf = hybrid_prediction["confidence"]
            agreement = 1.0 - abs(elo_probs["home_win"] - hybrid_probs["home_win"])
            base_conf = (elo_conf + hybrid_conf) / 2
            confidence = min(0.95, base_conf + agreement * 0.10)

            prediction_result = {
                "predicted_score": {"home": round(fused_home, 2), "away": round(fused_away, 2)},
                "outcome_probabilities": {
                    k: round(v, 4) for k, v in fused_probs.items()
                },
                "confidence": round(confidence, 3),
                "prediction_method": f"integrated (elo_odds {int(elo_weight*100)}% + hybrid {int(hybrid_weight*100)}%)",
                "elo_ratings": elo_prediction["elo_ratings"],
                "has_betting_odds": elo_prediction["has_betting_odds"],
                "rule_score": hybrid_prediction.get("rule_score"),
                "ai_score": hybrid_prediction.get("ai_score"),
                "ai_reasoning": hybrid_prediction.get("ai_reasoning"),
                "key_factors": hybrid_prediction.get("key_factors", []),
                "factors": factors,
                "score_probability_matrix": elo_prediction.get("score_probability_matrix"),
                "top_5_scores": elo_prediction.get("top_5_scores"),
                "prediction_interval": elo_prediction.get("prediction_interval"),
            }

        else:  # selected_engine == "hybrid"
            # Use comprehensive hybrid engine (factors already calculated in Step 3b)
            prediction_result = await get_engine("hybrid")(
                home_team=match.home_team,
                away_team=match.away_team,
                kickoff_utc=match.kickoff_utc,
                stage=match.stage,
                factors=factors
            )

        # Step 4d: Apply engine calibration if available
        try:
            from app.models.world_cup_prediction import EngineCalibration
            active_cal = session.query(EngineCalibration).filter_by(
                engine_name=selected_engine, is_active=True
            ).first()
            if active_cal and active_cal.calibration_params:
                # Pass engine name string; function will fetch calibration internally
                prediction_result = apply_calibration_to_prediction(
                    prediction_result, selected_engine
                )
                prediction_result["calibration_applied"] = {
                    "version": active_cal.version,
                    "params": active_cal.calibration_params,
                }
        except Exception as cal_err:
            logger.warning("Calibration application skipped: %s", cal_err)

        # Add data quality flag to prediction result
        if "factors" in prediction_result and isinstance(prediction_result["factors"], dict):
            prediction_result["data_quality"] = prediction_result["factors"].get("data_quality", "real")
        else:
            prediction_result["data_quality"] = "real"

        # Step 4e: Apply confidence calibration (bucketed reliability curve)
        try:
            prediction_result = apply_confidence_calibration(
                prediction_result, engine_name=selected_engine
            )
        except Exception as cal_err:
            logger.warning("Confidence calibration skipped: %s", cal_err)

        # Step 4f: Add betting market analysis from score probability matrix
        score_matrix = prediction_result.get("score_probability_matrix")
        if score_matrix:
            try:
                prediction_result["betting_analysis"] = analyze_betting_markets(
                    score_matrix,
                    prediction_result.get("outcome_probabilities"),
                )
            except Exception as ba_err:
                logger.warning("Betting analysis skipped: %s", ba_err)

        # End engine execution timing
        if engine_start_time:
            engine_exec_time = (time.perf_counter() - engine_start_time) * 1000  # Convert to ms
        
        # Step 4g: Add tactical matchup analysis
        try:
            prediction_result["tactical_analysis"] = format_tactical_summary(
                match.home_team, match.away_team
            )
        except Exception as ta_err:
            logger.warning("Tactical analysis skipped: %s", ta_err)

        # Step 4h: Run alternative engine for comparison (always record both engines)
        alternative_predictions = []
        
        # Determine which engines to run for comparison
        if selected_engine in ('elo_odds', 'integrated'):
            # Primary used elo_odds, also run hybrid for comparison
            if factors:  # Only if we have factors
                try:
                    hybrid_comparison = await get_engine('hybrid')(
                        home_team=match.home_team,
                        away_team=match.away_team,
                        kickoff_utc=match.kickoff_utc,
                        stage=match.stage,
                        factors=factors,
                    )
                    alternative_predictions.append({
                        'engine': 'hybrid',
                        'predicted_score': hybrid_comparison['predicted_score'],
                        'outcome_probabilities': hybrid_comparison['outcome_probabilities'],
                        'confidence': hybrid_comparison['confidence'],
                        'prediction_method': hybrid_comparison['prediction_method'],
                    })
                    logger.info('Ran hybrid engine for comparison: %s vs %s', 
                               hybrid_comparison['predicted_score'], prediction_result['predicted_score'])
                except Exception as alt_err:
                    logger.warning('Alternative hybrid engine failed: %s', alt_err)
        
        if selected_engine in ('hybrid', 'integrated'):
            # Primary used hybrid or integrated, also run pure elo_odds for comparison
            try:
                elo_comparison = get_engine('elo_odds')(
                    home_team=match.home_team,
                    away_team=match.away_team,
                    elo_home=home_elo_data['elo_rating'],
                    elo_away=away_elo_data['elo_rating'],
                    odds_home=odds['home'] if odds else None,
                    odds_draw=odds['draw'] if odds else None,
                    odds_away=odds['away'] if odds else None,
                    is_knockout=is_knockout,
                )
                alternative_predictions.append({
                    'engine': 'elo_odds',
                    'predicted_score': elo_comparison['predicted_score'],
                    'outcome_probabilities': elo_comparison['outcome_probabilities'],
                    'confidence': elo_comparison['confidence'],
                    'prediction_method': elo_comparison['prediction_method'],
                })
                logger.info('Ran elo_odds engine for comparison: %s vs %s', 
                           elo_comparison['predicted_score'], prediction_result['predicted_score'])
            except Exception as alt_err:
                logger.warning('Alternative elo_odds engine failed: %s', alt_err)

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
            existing.factors = prediction_result.get("factors", {})
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
                factors=prediction_result.get("factors", {}),
                ai_reasoning=prediction_result.get("ai_reasoning"),
                key_factors=prediction_result.get("key_factors", [])
            )
            session.add(new_pred)
            action = "created"

        # Step 6: Record prediction history snapshot (only if changed)
        # Check if last history entry has same prediction
        should_record_history = True
        last_history = session.query(PredictionHistory).filter_by(
            match_id=match_id
        ).order_by(PredictionHistory.timestamp.desc()).first()

        if last_history:
            # Check if prediction actually changed
            score_same = (
                abs(last_history.predicted_home_score - prediction_result["predicted_score"]["home"]) < 0.01 and
                abs(last_history.predicted_away_score - prediction_result["predicted_score"]["away"]) < 0.01
            )
            engine_same = last_history.prediction_method == prediction_result.get("prediction_method")
            confidence_similar = abs(last_history.confidence - prediction_result["confidence"]) < 0.01  # 1% threshold

            # Only skip if score, engine, and confidence are all unchanged
            if score_same and engine_same and confidence_similar:
                should_record_history = False

        if should_record_history:
            # Calculate total pipeline time
            total_pipeline_time = (time.perf_counter() - pipeline_start_time) * 1000 if pipeline_start_time else None
            
            # Record primary engine prediction
            history_entry = PredictionHistory(
                match_id=match_id,
                timestamp=datetime.utcnow(),
                predicted_home_score=prediction_result["predicted_score"]["home"],
                predicted_away_score=prediction_result["predicted_score"]["away"],
                home_win_prob=prediction_result["outcome_probabilities"]["home_win"],
                draw_prob=prediction_result["outcome_probabilities"]["draw"],
                away_win_prob=prediction_result["outcome_probabilities"]["away_win"],
                confidence=prediction_result["confidence"],
                trigger=trigger,
                prediction_method=prediction_result.get("prediction_method"),
                execution_time_ms=engine_exec_time,
                data_fetch_time_ms=data_fetch_time,
                total_pipeline_time_ms=total_pipeline_time
            )
            session.add(history_entry)
            
            # Also record alternative engine predictions for comparison
            for alt_pred in alternative_predictions:
                alt_history = PredictionHistory(
                    match_id=match_id,
                    timestamp=datetime.utcnow(),
                    predicted_home_score=alt_pred["predicted_score"]["home"],
                    predicted_away_score=alt_pred["predicted_score"]["away"],
                    home_win_prob=alt_pred["outcome_probabilities"]["home_win"],
                    draw_prob=alt_pred["outcome_probabilities"]["draw"],
                    away_win_prob=alt_pred["outcome_probabilities"]["away_win"],
                    confidence=alt_pred["confidence"],
                    trigger=trigger + "_comparison",
                    prediction_method=alt_pred["prediction_method"]
                )
                session.add(alt_history)
                logger.info("Recorded comparison history for %s engine: method=%s", 
                           alt_pred["engine"], alt_pred["prediction_method"])

        session.commit()

        return {
            "status": "ok",
            "action": action,
            "match_id": match_id,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "predicted_score": prediction_result["predicted_score"],
            "outcome_probabilities": prediction_result["outcome_probabilities"],
            "confidence": prediction_result["confidence"],
            "prediction_method": prediction_result["prediction_method"],
            "engine_used": selected_engine,
            "has_betting_odds": prediction_result.get("has_betting_odds", False),
            "elo_ratings": prediction_result.get("elo_ratings")
        }

    except Exception as e:
        session.rollback()
        logger.error("Prediction pipeline failed for %s: %s", match_id, e, exc_info=True)
        return {
            "status": "error",
            "match_id": match_id,
            "error": "internal_error"
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
            # Predict all remaining matches (only scheduled, not in_play/finished)
            matches = session.query(MatchFixture).filter(
                MatchFixture.status == "scheduled",
                MatchFixture.kickoff_utc > _utc_naive(datetime.now(timezone.utc)),
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
