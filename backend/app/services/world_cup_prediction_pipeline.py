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
import asyncio
import logging
import time

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.world_cup_prediction import (
    MatchFixture,
    MatchPrediction,
    PredictionHistory
)
from app.services.world_cup_factor_service import build_prediction_factors
from app.services.world_cup_engines import get_engine
from app.services.world_cup_engines.world_cup_elo_odds_engine import (
    calculate_elo_win_probability,
    odds_to_probabilities,
)
from app.services.elo_ratings_service import get_elo_rating
from app.services.odds_cache_service import get_cached_odds
from app.services.world_cup_team_stats_service import (
    fetch_team_statistics,
    fetch_head_to_head,
    get_team_id_from_name,
)
from app.services.world_cup_historical_results import (
    get_historical_h2h,
    get_historical_team_stats,
)
from app.services.world_cup_enhanced_factors import calculate_comprehensive_factors
from app.services.engine_auto_tuning_service import apply_calibration_to_prediction
from app.services.world_cup_confidence_calibration import apply_confidence_calibration, build_confidence_calibration_info
from app.services.world_cup_betting_analysis import analyze_betting_markets
from app.services.world_cup_tactical_profiles import format_tactical_summary
from app.services.world_cup_weather_service import get_match_weather
from app.services.world_cup_quality_service import suggest_integrated_engine_weights
from app.services.world_cup_data_quality import (
    SCORE_VERSION,
    age_days_from_source,
    age_minutes_from_source,
    calculate_data_quality_score,
    source_looks_real,
)
from app.services.world_cup_group_context import build_group_context
from app.services.world_cup_openfootball_data import build_openfootball_match_context
from app.services.world_cup_schedule_factors import build_schedule_factors
from app.utils.prediction_db import get_prediction_session, close_prediction_session

logger = logging.getLogger(__name__)


# Prediction engine selection
PredictionEngine = Literal["elo_odds", "hybrid", "integrated", "high_confidence", "auto"]
DEFAULT_ENGINE: PredictionEngine = "auto"  # Auto-select based on data availability


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _impact_item(
    key: str,
    label: str,
    unit: str,
    home_impact: float,
    away_impact: float,
    description: str,
    *,
    available: bool = True,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "home_impact": round(home_impact, 3),
        "away_impact": round(away_impact, 3),
        "description": description,
        "available": available,
    }


def build_explanation_contributions(
    *,
    selected_engine: str,
    match: MatchFixture,
    prediction_result: dict[str, Any],
    factors: dict[str, Any] | None,
    home_elo_data: dict[str, Any],
    away_elo_data: dict[str, Any],
    odds: dict[str, Any] | None,
    is_knockout: bool,
) -> dict[str, Any]:
    """Build a stable, UI-friendly contribution breakdown for a prediction."""
    factor_payload = factors if isinstance(factors, dict) else {}
    home_factor = factor_payload.get("home_team") or {}
    away_factor = factor_payload.get("away_team") or {}
    context_factor = factor_payload.get("context") or {}
    group_status = factor_payload.get("group_status") or {}

    home_elo = _safe_float(home_elo_data.get("elo_rating"), 1500.0)
    away_elo = _safe_float(away_elo_data.get("elo_rating"), 1500.0)
    elo_probs = calculate_elo_win_probability(home_elo, away_elo, is_knockout=is_knockout)
    elo_home_impact = (elo_probs["home_win"] - (1.0 / 3.0)) * 100
    elo_away_impact = (elo_probs["away_win"] - (1.0 / 3.0)) * 100

    items = [
        _impact_item(
            "elo",
            "Elo",
            "pp",
            elo_home_impact,
            elo_away_impact,
            f"Elo差 {home_elo - away_elo:+.0f} 点，反映长期球队强度。",
        )
    ]

    has_real_odds = bool(
        odds
        and odds.get("home")
        and odds.get("draw")
        and odds.get("away")
        and "fallback" not in str(odds.get("source", ""))
        and "default" not in str(odds.get("source", ""))
    )
    if has_real_odds:
        market_probs = odds_to_probabilities(
            _safe_float(odds.get("home"), 1.0),
            _safe_float(odds.get("draw"), 1.0),
            _safe_float(odds.get("away"), 1.0),
        )
        items.append(_impact_item(
            "odds",
            "赔率",
            "pp",
            (market_probs["home_win"] - elo_probs["home_win"]) * 100,
            (market_probs["away_win"] - elo_probs["away_win"]) * 100,
            "赔率隐含概率相对 Elo 的修正，代表盘口吸收的临场信息。",
        ))
    else:
        items.append(_impact_item(
            "odds",
            "赔率",
            "pp",
            0.0,
            0.0,
            "没有可用真实赔率，赔率贡献未参与。",
            available=False,
        ))

    home_density = home_factor.get("schedule_density", "normal")
    away_density = away_factor.get("schedule_density", "normal")
    home_schedule = 0.96 if home_density == "high" else (0.98 if home_density == "medium" else 1.0)
    away_schedule = 0.96 if away_density == "high" else (0.98 if away_density == "medium" else 1.0)
    items.append(_impact_item(
        "schedule",
        "赛程",
        "%xg",
        (home_schedule - 1.0) * 100,
        (away_schedule - 1.0) * 100,
        "赛程密度对预期进球的疲劳修正。",
        available=bool(home_factor or away_factor),
    ))

    home_injury = _safe_float(home_factor.get("injury_impact"), 0.0)
    away_injury = _safe_float(away_factor.get("injury_impact"), 0.0)
    items.append(_impact_item(
        "injury",
        "伤停",
        "xg",
        home_injury,
        away_injury,
        "伤停对本队预期进球的直接修正。",
        available=bool(home_factor or away_factor),
    ))

    home_must = bool((context_factor.get("home_team_standing") or {}).get("must_win"))
    away_must = bool((context_factor.get("away_team_standing") or {}).get("must_win"))
    home_motivation = 12.0 if home_must else 0.0
    away_motivation = 12.0 if away_must else 0.0
    if group_status.get("home") == "qualified":
        home_motivation -= 15.0
    elif group_status.get("home") == "eliminated":
        home_motivation -= 20.0
    if group_status.get("away") == "qualified":
        away_motivation -= 15.0
    elif group_status.get("away") == "eliminated":
        away_motivation -= 20.0
    items.append(_impact_item(
        "motivation",
        "动机",
        "%xg",
        home_motivation,
        away_motivation,
        "出线形势、必须取胜或已出线/出局状态带来的动机修正。",
        available=bool(context_factor or group_status),
    ))

    home_market_value = 0.85 + _safe_float(home_factor.get("market_value_rating"), 0.5) * 0.30
    away_market_value = 0.85 + _safe_float(away_factor.get("market_value_rating"), 0.5) * 0.30
    home_sentiment = 0.90 + _safe_float(home_factor.get("sentiment_rating"), 0.5) * 0.20
    away_sentiment = 0.90 + _safe_float(away_factor.get("sentiment_rating"), 0.5) * 0.20
    items.append(_impact_item(
        "market_signal",
        "市场信号",
        "%xg",
        ((home_market_value - 1.0) + (home_sentiment - 1.0)) * 100,
        ((away_market_value - 1.0) + (away_sentiment - 1.0)) * 100,
        "身价代理和情绪/舆情信号合并后的预期进球修正。",
        available=bool(home_factor or away_factor),
    ))

    return {
        "engine": selected_engine,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "items": items,
        "engine_weights": prediction_result.get("integrated_weights"),
        "prediction_method": prediction_result.get("prediction_method"),
    }


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


def fetch_team_stats(
    team_name: str,
    team_id: int | None = None,
    before_date: datetime | None = None,
) -> dict[str, Any]:
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
            stats["data_source"] = "real"
            stats.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
            return stats

    historical_stats = get_historical_team_stats(team_name, before_date=before_date)
    if historical_stats:
        return historical_stats

    # Fallback to mock data
    stats = generate_mock_team_stats(team_name)
    stats["data_source"] = "mock"  # Mark as mock for transparency
    return stats


def fetch_h2h_data(
    home_team: str,
    away_team: str,
    home_team_id: int | None = None,
    away_team_id: int | None = None,
    before_date: datetime | None = None,
) -> dict[str, Any]:
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
                "data_source": "real",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

    historical_h2h = get_historical_h2h(home_team, away_team, before_date=before_date)
    if historical_h2h:
        return historical_h2h

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


def _apply_openfootball_team_context(
    team_stats: dict[str, Any],
    team_context: dict[str, Any] | None,
) -> None:
    if not team_context:
        return

    metadata = team_context.get("metadata") or {}
    squad = team_context.get("squad") or {}
    if metadata:
        team_stats.setdefault("fifa_code", metadata.get("fifa_code"))
        team_stats.setdefault("confed", metadata.get("confed"))
        team_stats.setdefault("continent", metadata.get("continent"))
        team_stats.setdefault("world_cup_group", metadata.get("group"))
    if squad:
        player_count = squad.get("player_count")
        if player_count:
            team_stats.setdefault("squad_size", player_count)
            team_stats.setdefault("injured_players", 0)
            team_stats.setdefault("suspended_players", 0)
            team_stats.setdefault("key_injured", 0)
        if squad.get("average_age") is not None:
            team_stats.setdefault("average_squad_age", squad.get("average_age"))
        if squad.get("position_counts"):
            team_stats.setdefault("squad_positions", squad.get("position_counts"))


def _apply_openfootball_factor_context(
    factors: dict[str, Any],
    openfootball_context: dict[str, Any] | None,
) -> None:
    if not openfootball_context:
        return

    factors["openfootball_context"] = openfootball_context
    for side in ("home", "away"):
        side_context = openfootball_context.get(f"{side}_team") or {}
        metadata = side_context.get("metadata") or {}
        squad = side_context.get("squad") or {}
        team_factor = factors.setdefault(f"{side}_team", {})
        if metadata:
            team_factor["fifa_code"] = metadata.get("fifa_code")
            team_factor["confed"] = metadata.get("confed")
            team_factor["continent"] = metadata.get("continent")
            team_factor["world_cup_group"] = metadata.get("group")
        if squad:
            team_factor["squad_size"] = squad.get("player_count")
            team_factor["average_squad_age"] = squad.get("average_age")
            team_factor["squad_positions"] = squad.get("position_counts")


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
    session: Session | None = None,
    compare_only: bool = False,
) -> dict[str, Any]:
    """Run complete prediction pipeline for a match.

    Args:
        match_id: Match ID to predict
        trigger: What triggered this prediction (manual, daily_update, live_update, etc.)
        engine: Prediction engine to use ("elo_odds", "hybrid", "integrated", "high_confidence", "auto")
               - "elo_odds": Fast Elo+Odds engine (70-75% accuracy, <100ms)
               - "hybrid": Current Rule+AI engine (interpretable, 2-3s)
            - "integrated": Fuse elo_odds and hybrid engine results
            - "high_confidence": Run all public engines and use the highest-confidence result
            - "auto": Auto-select based on data availability (default)
        session: Database session (creates one if None)
        compare_only: Read-only mode for engine comparison UI. Bypasses the
            kickoff-freeze guard and skips all persistence (MatchPrediction and
            PredictionHistory writes). Used so the comparison card can render
            even after kickoff without overwriting frozen predictions. Defaults
            to False.

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

        # Don't persist predictions for matches that have already started or
        # finished. Status can lag behind kickoff when an upstream fixture sync
        # is late, so kickoff time is the durable freeze boundary for pre-match
        # scores. ``compare_only`` bypasses this guard so the engine-comparison
        # card can still render (without writing).
        if not compare_only:
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
        elif engine not in {"elo_odds", "hybrid", "integrated", "high_confidence"}:
            return {"status": "error", "match_id": match_id, "error": f"Unsupported engine: {engine}"}

        # Step 3b: Calculate prediction factors (needed for hybrid and integrated)
        factors = None
        data_quality = "real"
        if selected_engine in ("hybrid", "integrated", "high_confidence"):
            home_team_id = getattr(match, 'home_team_id', None)
            away_team_id = getattr(match, 'away_team_id', None)

            home_stats = fetch_team_stats(match.home_team, home_team_id, match.kickoff_utc)
            away_stats = fetch_team_stats(match.away_team, away_team_id, match.kickoff_utc)
            h2h_data = fetch_h2h_data(
                match.home_team,
                match.away_team,
                home_team_id,
                away_team_id,
                match.kickoff_utc,
            )

            # Track data quality with enhanced metrics
            data_quality = "real"
            if home_stats.get("data_source") == "mock" or away_stats.get("data_source") == "mock":
                data_quality = "mock"
            if h2h_data.get("data_source") == "mock":
                data_quality = "mock" if data_quality != "real" else "partial"
            
            # Enhanced data quality metrics
            elo_source = f"{home_elo_data.get('source', 'unknown')}/{away_elo_data.get('source', 'unknown')}"
            odds_source = odds.get("source") if odds else "none"
            stats_source = f"{home_stats.get('data_source', 'unknown')}/{away_stats.get('data_source', 'unknown')}"
            data_quality_metrics = {
                "quality": data_quality,
                "score_version": SCORE_VERSION,
                "has_elo": bool(home_elo_data and away_elo_data and source_looks_real(elo_source)),
                "has_odds": bool(odds and source_looks_real(odds_source)),
                "odds_stale": bool(odds and odds.get("stale")),
                "has_h2h": bool(h2h_data and source_looks_real(h2h_data.get("data_source"))),
                "has_stats": source_looks_real(stats_source),
                "has_weather": False,  # Will be set after weather fetch
                "has_schedule_context": False,
                "has_group_context": False,
                "has_openfootball_context": False,
                "elo_source": elo_source,
                "odds_source": odds_source,
                "stats_source": stats_source,
            }

            # Fetch weather data for the match venue
            weather = get_match_weather(
                venue=getattr(match, 'venue', None),
                city=getattr(match, 'city', None),
                match_date=match.kickoff_utc.isoformat() if match.kickoff_utc else None,
            )
            if weather:
                data_quality_metrics["has_weather"] = True

            openfootball_context = build_openfootball_match_context(
                match.home_team,
                match.away_team,
                venue=getattr(match, "venue", None),
                city=getattr(match, "city", None),
                match_date=match.kickoff_utc,
            )
            if openfootball_context:
                data_quality_metrics["has_openfootball_context"] = True
                data_quality_metrics["openfootball_source"] = openfootball_context.get("data_source")
                _apply_openfootball_team_context(
                    home_stats,
                    openfootball_context.get("home_team"),
                )
                _apply_openfootball_team_context(
                    away_stats,
                    openfootball_context.get("away_team"),
                )

            # Calculate data freshness (age in appropriate units)
            elo_ages = [
                age_days_from_source(home_elo_data, "updated_at", "last_updated"),
                age_days_from_source(away_elo_data, "updated_at", "last_updated"),
            ]
            known_elo_ages = [age for age in elo_ages if age is not None]
            elo_age_days = max(known_elo_ages) if known_elo_ages else None

            odds_age_minutes = age_minutes_from_source(
                odds,
                "fetched_at",
                "last_update",
                "last_updated_api",
                "cached_at",
            )

            stats_ages = [
                age_days_from_source(home_stats, "updated_at"),
                age_days_from_source(away_stats, "updated_at"),
            ]
            known_stats_ages = [age for age in stats_ages if age is not None]
            stats_age_hours = max(known_stats_ages) * 24 if known_stats_ages else None
            
            # Add freshness to metrics
            data_quality_metrics['elo_age_days'] = round(elo_age_days, 2) if elo_age_days is not None else None
            data_quality_metrics['odds_age_minutes'] = round(odds_age_minutes, 2) if odds_age_minutes is not None else None
            data_quality_metrics['stats_age_hours'] = round(stats_age_hours, 2) if stats_age_hours is not None else None

            schedule_factors = build_schedule_factors(match, session)
            data_quality_metrics["has_schedule_context"] = bool(schedule_factors)

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
            _apply_openfootball_factor_context(factors, openfootball_context)
            factors["schedule_context"] = schedule_factors
            home_factor = factors.setdefault("home_team", {})
            away_factor = factors.setdefault("away_team", {})
            home_factor["days_since_last_match"] = schedule_factors["home"]["days_since_last_match"]
            home_factor["matches_last_14_days"] = schedule_factors["home"]["matches_last_14_days"]
            home_factor["schedule_density"] = schedule_factors["home"]["schedule_density"]
            away_factor["days_since_last_match"] = schedule_factors["away"]["days_since_last_match"]
            away_factor["matches_last_14_days"] = schedule_factors["away"]["matches_last_14_days"]
            away_factor["schedule_density"] = schedule_factors["away"]["schedule_density"]

            group_context = build_group_context(match, session)
            group_status = None
            if group_context:
                data_quality_metrics["has_group_context"] = True
                factors["group_context"] = group_context
                context_factor = factors.setdefault("context", {})
                context_factor["home_team_standing"] = group_context["home_team_standing"]
                context_factor["away_team_standing"] = group_context["away_team_standing"]
                if group_context["has_must_win_team"]:
                    context_factor["stakes"] = "high"
                group_status = {
                    side: group_context[side]["status"]
                    for side in ("home", "away")
                    if group_context[side].get("status") in {"qualified", "eliminated"}
                } or None
            else:
                # Fallback for older fixture sets that only expose final-round status.
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
            data_quality_metrics["quality_score"] = calculate_data_quality_score(data_quality_metrics)

        # Determine if this is a knockout match
        is_knockout = match.stage in {
            "round_of_16", "quarterfinal", "semifinal", "final",
            "Round of 16", "Quarterfinal", "Semifinal", "Final",
        }

        # Step 4: Run prediction based on selected engine
        engine_start_time = time.perf_counter()

        def run_elo_prediction() -> dict[str, Any]:
            return get_engine("elo_odds")(
                home_team=match.home_team,
                away_team=match.away_team,
                elo_home=home_elo_data["elo_rating"],
                elo_away=away_elo_data["elo_rating"],
                odds_home=odds["home"] if odds else None,
                odds_draw=odds["draw"] if odds else None,
                odds_away=odds["away"] if odds else None,
                is_knockout=is_knockout,
            )

        def standardize_elo_prediction(prediction: dict[str, Any]) -> dict[str, Any]:
            return {
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

        async def run_hybrid_prediction() -> dict[str, Any]:
            if factors is None:
                raise ValueError("Hybrid factors unavailable")
            return await get_engine("hybrid")(
                home_team=match.home_team,
                away_team=match.away_team,
                kickoff_utc=match.kickoff_utc,
                stage=match.stage,
                factors=factors,
            )

        def build_integrated_prediction(
            elo_prediction: dict[str, Any],
            hybrid_prediction: dict[str, Any],
        ) -> dict[str, Any]:
            # Fuse: elo_odds + hybrid with dynamic weights based on data quality
            # Default: trust market signals (elo_odds) more when all data is real
            elo_weight = 0.70
            if data_quality == "mock":
                # Team stats are fake - reduce reliance on elo_odds
                elo_weight = min(elo_weight, 0.40)
            if odds and odds.get("source") and ("fallback" in odds.get("source") or "default" in odds.get("source")):
                # Odds are fake - reduce reliance on elo_odds
                elo_weight = min(elo_weight, 0.35)
            if odds and odds.get("stale"):
                # Stale market data is still useful, but should not dominate.
                elo_weight = min(elo_weight, 0.55)
            home_elo_source = home_elo_data.get("source", "")
            away_elo_source = away_elo_data.get("source", "")
            if ("estimated" in home_elo_source or "default" in home_elo_source or
                "estimated" in away_elo_source or "default" in away_elo_source):
                # Elo ratings are estimated/default - reduce reliance on elo_odds
                elo_weight = min(elo_weight, 0.45)
            weight_info = suggest_integrated_engine_weights(elo_weight, session=session)
            elo_weight = float(weight_info["elo_weight"])
            hybrid_weight = float(weight_info["hybrid_weight"])

            logger.info("Integrated engine weights: elo=%.2f hybrid=%.2f source=%s (data_quality=%s, odds_source=%s, elo_source=%s/%s)",
                        elo_weight, hybrid_weight, weight_info.get("source"),
                        data_quality,
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
            if isinstance(factors, dict):
                factors["integrated_weights"] = weight_info

            return {
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
                "integrated_weights": weight_info,
                "score_probability_matrix": elo_prediction.get("score_probability_matrix"),
                "top_5_scores": elo_prediction.get("top_5_scores"),
                "prediction_interval": elo_prediction.get("prediction_interval"),
            }

        if selected_engine == "elo_odds":
            # Use fast Elo+Odds engine
            prediction_result = standardize_elo_prediction(run_elo_prediction())

        elif selected_engine == "integrated":
            # Integrated engine: run both engines and fuse results
            # This combines market signals (elo_odds) with contextual factors (hybrid)
            prediction_result = build_integrated_prediction(
                run_elo_prediction(),
                await run_hybrid_prediction(),
            )

        elif selected_engine == "high_confidence":
            # Run all public engines, then persist the highest-confidence real engine.
            elo_prediction = run_elo_prediction()
            hybrid_prediction = await run_hybrid_prediction()
            candidates = [
                ("elo_odds", standardize_elo_prediction(elo_prediction)),
                ("hybrid", hybrid_prediction),
                ("integrated", build_integrated_prediction(elo_prediction, hybrid_prediction)),
            ]
            ranked_candidates = []
            for name, result in candidates:
                selection_calibration = build_confidence_calibration_info(
                    float(result.get("confidence") or 0.0),
                    name,
                )
                ranked_candidates.append((
                    name,
                    result,
                    float(selection_calibration["calibrated"]),
                    selection_calibration,
                ))

            selected_engine, prediction_result, selection_confidence, _selection_calibration = max(
                ranked_candidates,
                key=lambda item: item[2],
            )
            logger.info(
                "High-confidence engine selected %s for match %s from calibrated scores=%s",
                selected_engine,
                match_id,
                {name: confidence for name, _result, confidence, _selection_calibration in ranked_candidates},
            )
            prediction_result["high_confidence_selection"] = {
                "selected_engine": selected_engine,
                "selection_confidence": selection_confidence,
                "candidate_confidences": {
                    name: {
                        "raw": result.get("confidence"),
                        "calibrated": confidence,
                        "is_reliable": selection_calibration.get("is_reliable"),
                        "is_reference_only": selection_calibration.get("is_reference_only"),
                        "total_samples": selection_calibration.get("total_samples"),
                        "min_total_samples": selection_calibration.get("min_total_samples"),
                        "min_bucket_samples": selection_calibration.get("min_bucket_samples"),
                        "bucket_is_reliable": selection_calibration.get("bucket_is_reliable"),
                        "bucket": selection_calibration.get("bucket"),
                        "applied_bucket": selection_calibration.get("applied_bucket"),
                        "reason": selection_calibration.get("reason"),
                    }
                    for name, result, confidence, selection_calibration in ranked_candidates
                },
            }

        else:  # selected_engine == "hybrid"
            # Use comprehensive hybrid engine (factors already calculated in Step 3b)
            prediction_result = await run_hybrid_prediction()

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

        # Step 4d.5: Build explanation contributions for UI/debugging
        try:
            explanation_contributions = build_explanation_contributions(
                selected_engine=selected_engine,
                match=match,
                prediction_result=prediction_result,
                factors=prediction_result.get("factors") if isinstance(prediction_result.get("factors"), dict) else factors,
                home_elo_data=home_elo_data,
                away_elo_data=away_elo_data,
                odds=odds,
                is_knockout=is_knockout,
            )
            prediction_result["explanation_contributions"] = explanation_contributions
            factor_payload = prediction_result.get("factors")
            if not isinstance(factor_payload, dict):
                factor_payload = {}
            factor_payload["explanation_contributions"] = explanation_contributions
            prediction_result["factors"] = factor_payload
        except Exception as contribution_err:
            logger.warning("Explanation contribution breakdown skipped: %s", contribution_err)

        # Step 4e: Apply confidence calibration (bucketed reliability curve)
        try:
            prediction_result = apply_confidence_calibration(
                prediction_result, engine_name=selected_engine
            )
            calibration_info = prediction_result.get("calibration_info")
            high_confidence_selection = prediction_result.get("high_confidence_selection")
            if calibration_info or high_confidence_selection:
                factor_payload = prediction_result.get("factors")
                if not isinstance(factor_payload, dict):
                    factor_payload = {}
                if calibration_info:
                    factor_payload["confidence_calibration"] = calibration_info
                if high_confidence_selection:
                    factor_payload["high_confidence_selection"] = high_confidence_selection
                prediction_result["factors"] = factor_payload
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

        # Step 4h: Run alternative engine for diagnostics only. Engine comparison
        # UI uses compare_only calls; prediction_history tracks applied predictions.

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
                logger.info('Ran elo_odds engine for comparison: %s vs %s', 
                           elo_comparison['predicted_score'], prediction_result['predicted_score'])
            except Exception as alt_err:
                logger.warning('Alternative elo_odds engine failed: %s', alt_err)

        # Step 5: Save or update prediction in database (skipped in compare-only mode)
        if compare_only:
            logger.info(
                "compare_only=True for %s: returning prediction without persisting",
                match_id,
            )
            compare_factors = prediction_result.get("factors")
            compare_quality_metrics = (
                compare_factors.get("data_quality_metrics")
                if isinstance(compare_factors, dict)
                else {}
            ) or {}
            return {
                "status": "ok",
                "action": "compared",
                "match_id": match_id,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "predicted_score": prediction_result["predicted_score"],
                "outcome_probabilities": prediction_result["outcome_probabilities"],
                "confidence": prediction_result["confidence"],
                "prediction_method": prediction_result["prediction_method"],
                "engine_used": selected_engine,
                "has_betting_odds": prediction_result.get("has_betting_odds", False),
                "data_quality": prediction_result.get("data_quality"),
                "data_quality_score": compare_quality_metrics.get("quality_score"),
                "elo_ratings": prediction_result.get("elo_ratings"),
                "raw_confidence": prediction_result.get("raw_confidence"),
                "confidence_calibration": prediction_result.get("calibration_info"),
                "high_confidence_selection": prediction_result.get("high_confidence_selection"),
                "explanation_contributions": prediction_result.get("explanation_contributions"),
            }

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
            existing.last_updated = datetime.now(timezone.utc)
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
        ).filter(
            or_(
                PredictionHistory.trigger.is_(None),
                ~PredictionHistory.trigger.like("%_comparison"),
            )
        ).order_by(PredictionHistory.timestamp.desc(), PredictionHistory.id.desc()).first()

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
                timestamp=datetime.now(timezone.utc),
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
            "data_quality": prediction_result.get("data_quality"),
            "data_quality_score": (
                prediction_result.get("factors", {})
                .get("data_quality_metrics", {})
                .get("quality_score")
                if isinstance(prediction_result.get("factors"), dict)
                else None
            ),
            "elo_ratings": prediction_result.get("elo_ratings"),
            "raw_confidence": prediction_result.get("raw_confidence"),
            "confidence_calibration": prediction_result.get("calibration_info"),
            "high_confidence_selection": prediction_result.get("high_confidence_selection"),
            "explanation_contributions": prediction_result.get("explanation_contributions"),
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

    # Resolve the match list with a short-lived session, then close it before
    # dispatching the per-match pipelines. Each parallel pipeline call passes
    # session=None so it creates (and closes) its OWN session — sharing a single
    # SQLAlchemy session across asyncio.gather tasks is unsafe (sessions are not
    # thread- or task-safe; concurrent commits on one session would corrupt
    # state). The match list is plain ORM rows already loaded into memory by
    # .all(), so closing the session afterwards is fine.
    session = get_prediction_session()
    try:
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
        match_ids_to_run = [m.match_id for m in matches]
    finally:
        close_prediction_session(session)

    results = {
        "status": "ok",
        "total": len(match_ids_to_run),
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "elo_odds_count": 0,
        "hybrid_count": 0,
        "integrated_count": 0,
        "predictions": []
    }

    if not match_ids_to_run:
        return results

    # Bound concurrency: each pipeline call may hit the LLM (hybrid engine),
    # the embeddings API (semantic news), the odds API, and the fixture API.
    # Running them unbounded would (a) blow past LLM provider rate limits and
    # (b) open dozens of SQLAlchemy sessions at once, pressuring the SQLite
    # WAL. LLM_CONCURRENCY is the existing knob (default 4) — reuse it as the
    # cap for in-flight pipeline calls.
    semaphore = asyncio.Semaphore(max(1, settings.LLM_CONCURRENCY))

    async def _run_one(mid: str) -> dict[str, Any]:
        async with semaphore:
            # session=None -> run_prediction_pipeline opens its own session
            # and closes it in a finally block. This is what makes the gather
            # safe: each task owns its session.
            return await run_prediction_pipeline(
                mid,
                trigger=trigger,
                engine=engine,
                session=None,
            )

    # return_exceptions=True so one match failing doesn't cancel the whole
    # batch — we surface it as a "failed" entry instead of raising.
    raw_results = await asyncio.gather(
        *(_run_one(mid) for mid in match_ids_to_run),
        return_exceptions=True,
    )

    for result in raw_results:
        if isinstance(result, Exception):
            # An unexpected exception (not a returned {"status": "error"})
            # means the pipeline blew up before its try/except could shape a
            # response. Log it and count as failed so the summary stays
            # accurate; the caller can inspect the message via the predictions
            # list entry below.
            logger.error("batch_predict_matches task raised: %s", result, exc_info=result)
            results["failed"] += 1
            results["predictions"].append({
                "status": "error",
                "error": f"{type(result).__name__}: {result}",
            })
            continue

        if result.get("status") == "ok":
            results["succeeded"] += 1
            if result.get("engine_used") == "elo_odds":
                results["elo_odds_count"] += 1
            elif result.get("engine_used") == "hybrid":
                results["hybrid_count"] += 1
            elif result.get("engine_used") == "integrated":
                results["integrated_count"] += 1
        elif result.get("status") == "skipped":
            results["skipped"] += 1
        else:
            results["failed"] += 1

        results["predictions"].append(result)

    return results
