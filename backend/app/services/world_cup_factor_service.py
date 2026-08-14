"""Service to calculate prediction factors from team data and context.

This module extracts and transforms raw team statistics into normalized
factors used by the prediction engines.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.transfermarkt_scraper import get_cached_market_value
from app.services.sentiment_aggregator import get_cached_sentiment
from app.services.sports_signal_service import build_sports_signals

logger = logging.getLogger(__name__)


def calculate_team_factors(
    team_name: str,
    team_stats: dict[str, Any],
    is_home: bool = False
) -> dict[str, Any]:
    """Calculate normalized factors for a team.

    Args:
        team_name: Team name
        team_stats: Raw statistics from API
        is_home: Whether team is playing at home

    Returns:
        Dictionary of normalized factors
    """

    # Extract basic stats
    goals_per_game = team_stats.get("goals_per_game", 1.5)
    goals_conceded_per_game = team_stats.get("goals_conceded_per_game", 1.2)
    wins = team_stats.get("wins", 0)
    draws = team_stats.get("draws", 0)
    losses = team_stats.get("losses", 0)
    matches_played = wins + draws + losses

    # Calculate recent form with time-decay weighting
    # Recent matches weigh more than older ones
    recent_form = 0.5  # Default neutral
    if matches_played > 0:
        # Check if match-level results are available for time-decay
        match_results = team_stats.get("recent_results", [])
        if match_results and len(match_results) > 0:
            # Apply exponential decay: most recent match has weight 1.0,
            # each older match decays by factor 0.75
            decay = 0.75
            weighted_score = 0.0
            total_weight = 0.0
            for i, result in enumerate(match_results):
                w = decay ** i
                if result == "W":
                    weighted_score += w * 1.0
                elif result == "D":
                    weighted_score += w * 0.5
                # Loss = 0 points, no contribution
                total_weight += w
            recent_form = weighted_score / total_weight if total_weight > 0 else 0.5
        else:
            # Fallback: simple win rate (no time decay data available)
            recent_form = (wins + 0.5 * draws) / matches_played

    # Defense rating (inverse of goals conceded, normalized)
    # Assume avg team concedes ~1.2 goals per game
    defense_rating = max(0.0, min(1.0, 1.0 - (goals_conceded_per_game - 0.8) / 2.0))

    # Injury impact (negative modifier)
    injury_impact = team_stats.get("injury_impact", 0.0)

    # Fatigue level based on days since last match
    last_match_date = team_stats.get("last_match_date")
    days_since_last_match = 7  # Default
    if last_match_date:
        try:
            last_date = datetime.fromisoformat(last_match_date.replace('Z', '+00:00'))
            days_since_last_match = (datetime.now(timezone.utc) - last_date).days
        except (ValueError, TypeError):
            pass

    # Home advantage modifier
    home_advantage = 0.10 if is_home else 0.0

    # NEW: Market value factor (team quality proxy)
    market_value_rating = 0.5  # Default neutral
    market_value_cached = get_cached_market_value(team_name, ttl_days=7)  # 7 days
    if market_value_cached:
        total_value = market_value_cached["total_market_value"]
        # Normalize: top teams ~1000m, mid ~400m, low ~150m
        # Map to 0-1 scale: 1000m+ = 1.0, 150m = 0.0
        market_value_rating = min(1.0, max(0.0, (total_value - 150) / (1000 - 150)))

    # NEW: Sentiment factor (momentum/morale signal)
    sentiment_rating = 0.0  # Default neutral
    sentiment_confidence = 0.0
    sentiment_cached = get_cached_sentiment(team_name, ttl_hours=6)
    if sentiment_cached:
        # Sentiment is -1 to 1, convert to 0 to 1
        sentiment_rating = (sentiment_cached["overall_sentiment"] + 1) / 2
        sentiment_confidence = sentiment_cached["confidence"]

    return {
        "team_name": team_name,
        "fifa_ranking": team_stats.get("fifa_ranking"),
        "recent_form": round(recent_form, 3),
        "goals_per_game": round(goals_per_game, 2),
        "goals_conceded_per_game": round(goals_conceded_per_game, 2),
        "defense_rating": round(defense_rating, 3),
        "injury_impact": round(injury_impact, 3),
        "days_since_last_match": days_since_last_match,
        "home_advantage": home_advantage,
        "matches_played": matches_played,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "squad_size": team_stats.get("squad_size"),
        "average_squad_age": team_stats.get("average_squad_age"),
        "squad_positions": team_stats.get("squad_positions"),
        # New factors
        "market_value_rating": round(market_value_rating, 3),
        "market_value_euros": round(market_value_cached["total_market_value"], 1) if market_value_cached else None,
        "sentiment_rating": round(sentiment_rating, 3),
        "sentiment_confidence": round(sentiment_confidence, 3)
    }


def calculate_head_to_head_factors(h2h_data: dict[str, Any] | None) -> dict[str, Any]:
    """Calculate head-to-head factors from historical matchups.

    Args:
        h2h_data: Historical head-to-head data

    Returns:
        Normalized h2h factors
    """

    if not h2h_data:
        return {
            "matches_played": 0,
            "home_wins": 0,
            "draws": 0,
            "away_wins": 0,
            "avg_goals_home": 1.5,
            "avg_goals_away": 1.5
        }

    return {
        "matches_played": h2h_data.get("matches_played", 0),
        "home_wins": h2h_data.get("home_wins", 0),
        "draws": h2h_data.get("draws", 0),
        "away_wins": h2h_data.get("away_wins", 0),
        "avg_goals_home": round(h2h_data.get("avg_goals_home", 1.5), 2),
        "avg_goals_away": round(h2h_data.get("avg_goals_away", 1.5), 2)
    }


def calculate_context_factors(
    stage: str,
    home_team_standing: dict[str, Any] | None = None,
    away_team_standing: dict[str, Any] | None = None,
    weather: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Calculate contextual factors for the match.

    Args:
        stage: Tournament stage
        home_team_standing: Current group standing for home team
        away_team_standing: Current group standing for away team
        weather: Weather conditions

    Returns:
        Context factors including stakes level
    """

    # Determine stakes level based on stage and standings
    stakes = "medium"

    if stage in {"final", "semifinal"}:
        stakes = "high"
    elif stage in {"quarterfinal", "round_of_16"}:
        stakes = "medium"
    elif stage == "group_stage":
        # Check if must-win situation based on standings
        if home_team_standing or away_team_standing:
            # Simplified logic: if team is bottom of group in last match, it's must-win
            stakes = "medium"
            # Could add more sophisticated logic here

    return {
        "tournament_stage": stage,
        "stakes": stakes,
        "weather": weather.get("condition", "clear") if weather else "clear",
        "temperature_c": weather.get("temperature", 25) if weather else 25
    }


def build_prediction_factors(
    home_team_name: str,
    away_team_name: str,
    home_team_stats: dict[str, Any],
    away_team_stats: dict[str, Any],
    stage: str,
    h2h_data: dict[str, Any] | None = None,
    home_standing: dict[str, Any] | None = None,
    away_standing: dict[str, Any] | None = None,
    weather: dict[str, Any] | None = None,
    match_date: str | None = None,
    match_id: str | None = None
) -> dict[str, Any]:
    """Build complete factor dictionary for prediction engines.

    Args:
        home_team_name: Home team name
        away_team_name: Away team name
        home_team_stats: Home team statistics
        away_team_stats: Away team statistics
        stage: Tournament stage
        h2h_data: Head-to-head historical data
        home_standing: Home team's current group standing
        away_standing: Away team's current group standing
        weather: Weather conditions
        match_date: Match date (ISO format)
        match_id: Match ID for signal lookup

    Returns:
        Complete factors dictionary ready for prediction
    """

    # Build event question for signal service
    event_question = f"Will {home_team_name} beat {away_team_name}?"

    # Build source context
    source = {
        "tournament": "2026 FIFA World Cup",
        "stage": stage,
        "home_team": home_team_name,
        "away_team": away_team_name,
        "match_date": match_date,
        "match_id": match_id
    }

    # Fetch sports facts and build signals (optional enhancement)
    sports_signals = {}
    try:
        from app.services.sports_fact_service import load_sports_facts

        # Load facts from storage
        facts = load_sports_facts()

        # Build signals from facts
        sports_signals = build_sports_signals(event_question, source, facts)
    except Exception as e:
        # Sports signals are optional - don't fail prediction if unavailable
        logger.warning("[Factor Service] Sports signals unavailable: %s", e)
        sports_signals = {}

    # Annotated: the literal's values are all dicts, so it infers
    # dict[str, dict[str, Any]] and rejects the key_factors list added below.
    factors: dict[str, Any] = {
        "home_team": calculate_team_factors(home_team_name, home_team_stats, is_home=True),
        "away_team": calculate_team_factors(away_team_name, away_team_stats, is_home=False),
        "head_to_head": calculate_head_to_head_factors(h2h_data),
        "context": calculate_context_factors(stage, home_standing, away_standing, weather)
    }

    # Add sports signals if available and non-empty
    if sports_signals and sports_signals.get("fact_count", 0) > 0:
        factors["sports_signals"] = sports_signals

        # ---- Wire signals into team factors --------------------------------
        # The rule engine reads injury_impact from home_team/away_team dicts
        # (see world_cup_rule_engine.predict_score_rule_based, line ~294).
        # API-Football never populates this field, so it stays at 0.0.
        # Extract it from sports signals (injury + lineup + suspension) here.
        _apply_signal_injury_impact(factors, sports_signals)

        # Populate key_factors from sports signals so they flow through to
        # the prediction result and the frontend.
        kf = _extract_key_factors(sports_signals)
        if kf:
            factors["key_factors"] = kf

    return factors


def _apply_signal_injury_impact(
    factors: dict[str, Any],
    signals_bundle: dict[str, Any],
) -> None:
    """Derive injury_impact from sports signals and write into team factors.

    The rule engine reads ``home.get("injury_impact", 0.0)`` and adds it
    directly to xG (range −0.3 to 0).  API-Football never returns this
    field, so we derive it from the structured signals:

    * injury_signal:  high → −0.20,  medium → −0.10,  low → −0.05
    * lineup_signal:  −0.10 per unavailable starter (cap −0.30)
    * suspension_signal: −0.08 per suspended player (cap −0.24)

    Multiple signals for the same team accumulate, hard-capped at −0.30.
    """
    signals = signals_bundle.get("signals") or {}
    home_team = factors.get("home_team", {})
    away_team = factors.get("away_team", {})

    home_impact = float(home_team.get("injury_impact") or 0.0)
    away_impact = float(away_team.get("injury_impact") or 0.0)

    # --- Injury signal (no team attribution → split evenly) -----------------
    injury = signals.get("injury_signal")
    if injury:
        level_map = {"high": -0.20, "medium": -0.10, "low": -0.05}
        delta = level_map.get(injury.get("level", ""), 0.0)
        if delta:
            # Injury signal doesn't specify which team; apply to both.
            # In practice this is conservative — real injuries rarely hit
            # both squads equally.
            home_impact += delta * 0.5
            away_impact += delta * 0.5

    # --- Lineup signal (team-specific) --------------------------------------
    lineup = signals.get("lineup_signal")
    if lineup:
        unavailable = lineup.get("unavailable_starters", 0)
        importance = lineup.get("importance", 1.0)
        if unavailable > 0:
            # −0.10 per unavailable starter, scaled by importance weight.
            delta = max(-0.10 * unavailable * importance, -0.30)
            lineup_team = _norm(lineup.get("team", ""))
            if lineup_team and lineup_team == _norm(home_team.get("team_name", "")):
                home_impact += delta
            elif lineup_team and lineup_team == _norm(away_team.get("team_name", "")):
                away_impact += delta
            else:
                # Can't determine which team — apply to both (halved).
                home_impact += delta * 0.5
                away_impact += delta * 0.5

    # --- Suspension signal (team-specific) ----------------------------------
    suspension = signals.get("suspension_signal")
    if suspension:
        suspended = suspension.get("suspended_count", 0)
        if suspended > 0:
            delta = max(-0.08 * suspended, -0.24)
            susp_team = _norm(suspension.get("team", ""))
            if susp_team and susp_team == _norm(home_team.get("team_name", "")):
                home_impact += delta
            elif susp_team and susp_team == _norm(away_team.get("team_name", "")):
                away_impact += delta
            else:
                home_impact += delta * 0.5
                away_impact += delta * 0.5

    # Hard cap and write back
    home_team["injury_impact"] = round(max(-0.30, min(0.0, home_impact)), 3)
    away_team["injury_impact"] = round(max(-0.30, min(0.0, away_impact)), 3)


def _extract_key_factors(signals_bundle: dict[str, Any]) -> list[str]:
    """Build human-readable key_factors strings from sports signals.

    These flow through ``prediction_result["key_factors"]`` → DB → frontend.
    """
    signals = signals_bundle.get("signals") or {}
    factors: list[str] = []

    _SIGNAL_LABELS = {
        "injury_signal": "伤停",
        "lineup_signal": "首发缺阵",
        "suspension_signal": "停赛",
        "qualification_signal": "出线形势",
        "schedule_fatigue_signal": "赛程疲劳",
        "discipline_signal": "纪律",
        "group_strength_signal": "小组实力",
        "match_format_signal": "赛制",
        "player_award_signal": "射手榜",
    }

    for signal_name, label in _SIGNAL_LABELS.items():
        signal = signals.get(signal_name)
        if signal and signal.get("summary"):
            level = signal.get("level", "")
            tag = f"[{level}]" if level else ""
            factors.append(f"{label}{tag}: {signal['summary']}")

    return factors[:8]  # Cap at 8 factors


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())
