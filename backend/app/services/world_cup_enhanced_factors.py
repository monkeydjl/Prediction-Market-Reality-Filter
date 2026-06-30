"""Enhanced factor calculation with comprehensive prediction signals.

This module implements a rich set of prediction factors across 5 categories:
1. Team Strength: Elo, FIFA ranking, attack/defense ratings
2. Historical Performance: H2H records, form against similar opponents
3. Situational Context: Fatigue, travel, venue, climate
4. Market Signals: Betting odds, squad market value
5. Model Features: Derived signals for ML models
"""

from datetime import datetime, timedelta, timezone
from typing import Any
import math


# ============================================================================
# 1. TEAM STRENGTH FACTORS
# ============================================================================

def calculate_elo_rating(team_stats: dict[str, Any]) -> float:
    """Calculate or retrieve Elo rating for a team.

    Returns:
        Elo rating (typically 1000-2200, avg ~1500)
    """
    # If provided directly
    if "elo_rating" in team_stats:
        return float(team_stats["elo_rating"])

    # Estimate from FIFA ranking if available
    fifa_rank = team_stats.get("fifa_ranking")
    if fifa_rank:
        # Inverse relationship: lower rank = higher Elo
        # Rank 1 ≈ 2100, Rank 50 ≈ 1500, Rank 200 ≈ 1000
        return max(1000, 2200 - (fifa_rank * 6))

    # Default neutral rating
    return 1500


def calculate_attack_strength(team_stats: dict[str, Any]) -> float:
    """Calculate normalized attack strength (0.0 - 2.0).

    Based on:
    - Goals per game
    - Expected goals (xG) if available
    - Shot conversion rate
    """
    goals_per_game = team_stats.get("goals_per_game", 1.5)
    xg_per_game = team_stats.get("xg_per_game", goals_per_game)

    # Average both actual and expected
    attack = (goals_per_game + xg_per_game) / 2

    # Normalize: 1.5 goals/game = 1.0 strength
    return round(attack / 1.5, 3)


def calculate_defense_strength(team_stats: dict[str, Any]) -> float:
    """Calculate normalized defense strength (0.0 - 2.0).

    Based on:
    - Goals conceded per game
    - Expected goals against (xGA) if available
    - Clean sheet rate
    """
    goals_conceded = team_stats.get("goals_conceded_per_game", 1.2)
    xga_per_game = team_stats.get("xga_per_game", goals_conceded)

    # Average both actual and expected
    defense_weakness = (goals_conceded + xga_per_game) / 2

    # Invert: lower conceded = higher strength
    # 1.2 goals conceded = 1.0 strength
    defense = 1.2 / max(0.5, defense_weakness)

    return round(min(2.0, defense), 3)


def calculate_form_rating(team_stats: dict[str, Any], lookback: int = 5) -> float:
    """Calculate recent form rating with recency weighting.

    Args:
        team_stats: Team statistics
        lookback: Number of recent matches to consider

    Returns:
        Form rating (0.0 - 1.0)
    """
    wins = team_stats.get("wins", 0)
    draws = team_stats.get("draws", 0)
    losses = team_stats.get("losses", 0)
    total = wins + draws + losses

    if total == 0:
        return 0.5

    # Points-based rating with 3-1-0 system
    points = (wins * 3) + draws
    max_points = total * 3

    return round(points / max_points, 3)


def calculate_squad_availability(team_stats: dict[str, Any]) -> dict[str, Any]:
    """Calculate squad availability metrics.

    Returns:
        Dictionary with injury/suspension impact
    """
    injured = team_stats.get("injured_players", 0)
    suspended = team_stats.get("suspended_players", 0)
    squad_size = team_stats.get("squad_size", 23)

    # Key player injuries have more impact
    key_injured = team_stats.get("key_injured", 0)

    # Impact: 0 = no impact, -0.5 = severe impact
    base_impact = -(injured + suspended) / squad_size * 0.3
    key_impact = -key_injured * 0.1

    total_impact = max(-0.5, base_impact + key_impact)

    return {
        "injured_count": injured,
        "suspended_count": suspended,
        "key_injured": key_injured,
        "availability_rate": round(1.0 - (injured + suspended) / squad_size, 3),
        "impact_modifier": round(total_impact, 3)
    }


# ============================================================================
# 2. HISTORICAL PERFORMANCE FACTORS
# ============================================================================

def calculate_h2h_factors(h2h_data: dict[str, Any] | None) -> dict[str, Any]:
    """Calculate head-to-head historical factors.

    Returns:
        H2H win rates, goal averages, dominance metrics
    """
    if not h2h_data:
        return {
            "matches_played": 0,
            "home_win_rate": 0.33,
            "draw_rate": 0.33,
            "away_win_rate": 0.33,
            "home_dominance": 0.0,
            "avg_goals_home": 1.5,
            "avg_goals_away": 1.5,
        }

    matches = h2h_data.get("matches_played", 0)
    if matches == 0:
        return calculate_h2h_factors(None)

    home_wins = h2h_data.get("home_wins", 0)
    draws = h2h_data.get("draws", 0)
    away_wins = h2h_data.get("away_wins", 0)

    # Dominance: how much one team outperforms
    home_dominance = (home_wins - away_wins) / matches

    return {
        "matches_played": matches,
        "home_win_rate": round(home_wins / matches, 3),
        "draw_rate": round(draws / matches, 3),
        "away_win_rate": round(away_wins / matches, 3),
        "home_dominance": round(home_dominance, 3),  # -1 to +1
        "avg_goals_home": round(h2h_data.get("avg_goals_home", 1.5), 2),
        "avg_goals_away": round(h2h_data.get("avg_goals_away", 1.5), 2),
    }


def calculate_opponent_quality_performance(team_stats: dict[str, Any]) -> dict[str, Any]:
    """Calculate performance against different opponent tiers.

    Returns:
        Win rates vs top/mid/low tier opponents
    """
    # If detailed stats available
    if "vs_top_teams" in team_stats:
        return {
            "vs_top_win_rate": team_stats.get("vs_top_teams", {}).get("win_rate", 0.3),
            "vs_mid_win_rate": team_stats.get("vs_mid_teams", {}).get("win_rate", 0.5),
            "vs_low_win_rate": team_stats.get("vs_low_teams", {}).get("win_rate", 0.7),
        }

    # Default distribution
    return {
        "vs_top_win_rate": 0.4,
        "vs_mid_win_rate": 0.5,
        "vs_low_win_rate": 0.6,
    }


# ============================================================================
# 3. SITUATIONAL CONTEXT FACTORS
# ============================================================================

def calculate_fatigue_factor(team_stats: dict[str, Any]) -> dict[str, Any]:
    """Calculate fatigue based on rest days and schedule density.

    Returns:
        Fatigue metrics and modifiers
    """
    last_match = team_stats.get("last_match_date")
    days_rest = 7  # Default

    if last_match:
        try:
            last_date = datetime.fromisoformat(last_match.replace('Z', '+00:00'))
            days_rest = (datetime.now(timezone.utc) - last_date).days
        except (ValueError, TypeError):
            pass

    # Fatigue curve: 0-2 days = high fatigue, 3-4 = medium, 5+ = rested
    if days_rest <= 2:
        fatigue_level = "high"
        performance_modifier = -0.10
    elif days_rest <= 4:
        fatigue_level = "medium"
        performance_modifier = -0.05
    else:
        fatigue_level = "low"
        performance_modifier = 0.0

    # Schedule density: matches in last 14 days
    matches_last_14d = team_stats.get("matches_last_14_days", 2)
    density = "high" if matches_last_14d >= 4 else "normal"

    return {
        "days_since_last_match": days_rest,
        "fatigue_level": fatigue_level,
        "performance_modifier": round(performance_modifier, 3),
        "matches_last_14_days": matches_last_14d,
        "schedule_density": density,
    }


def calculate_venue_factors(
    is_home: bool,
    venue: str | None = None,
    travel_distance_km: float | None = None,
    altitude_m: float | None = None,
    climate: str | None = None
) -> dict[str, Any]:
    """Calculate venue and travel impact factors.

    Args:
        is_home: Whether team is home (for World Cup, usually neutral)
        venue: Venue name
        travel_distance_km: Distance traveled
        altitude_m: Venue altitude
        climate: Climate type (tropical, temperate, cold)

    Returns:
        Venue impact factors
    """
    # Home advantage (minimal for World Cup - all neutral venues)
    home_modifier = 0.05 if is_home else 0.0

    # Travel impact: >3000km = jet lag risk
    travel_modifier = 0.0
    if travel_distance_km:
        if travel_distance_km > 5000:
            travel_modifier = -0.08
        elif travel_distance_km > 3000:
            travel_modifier = -0.05

    # Altitude impact: >2000m = performance drop for non-adapted teams
    altitude_modifier = 0.0
    if altitude_m and altitude_m > 2000:
        # Check if team is from high-altitude region
        is_altitude_adapted = False  # Would check team origin
        if not is_altitude_adapted:
            altitude_modifier = -0.10

    # Climate impact
    climate_modifier = 0.0
    if climate in ["tropical", "extreme_heat"]:
        climate_modifier = -0.05  # Unless team is adapted

    return {
        "is_neutral_venue": not is_home,
        "home_advantage": round(home_modifier, 3),
        "travel_impact": round(travel_modifier, 3),
        "altitude_m": altitude_m or 0,
        "altitude_impact": round(altitude_modifier, 3),
        "climate": climate or "temperate",
        "climate_impact": round(climate_modifier, 3),
        "total_venue_modifier": round(
            home_modifier + travel_modifier + altitude_modifier + climate_modifier, 3
        ),
    }


# ============================================================================
# 4. MARKET & EXTERNAL SIGNALS
# ============================================================================

def calculate_betting_market_factors(betting_odds: dict[str, Any] | None) -> dict[str, Any]:
    """Extract probability signals from betting odds.

    Betting odds are often the strongest single predictor because they
    aggregate wisdom of crowds + sharp money.

    Args:
        betting_odds: Dictionary with home/draw/away odds (decimal format)

    Returns:
        Implied probabilities and value indicators
    """
    if not betting_odds:
        return {
            "has_odds": False,
            "implied_home_win": 0.45,
            "implied_draw": 0.27,
            "implied_away_win": 0.28,
            "market_confidence": 0.5,
        }

    home_odds = betting_odds.get("home", 2.2)
    draw_odds = betting_odds.get("draw", 3.4)
    away_odds = betting_odds.get("away", 3.0)

    # Convert decimal odds to implied probability
    # Remove bookmaker margin (overround)
    implied_home = 1 / home_odds
    implied_draw = 1 / draw_odds
    implied_away = 1 / away_odds

    total = implied_home + implied_draw + implied_away

    # Normalize to remove margin
    implied_home_norm = implied_home / total
    implied_draw_norm = implied_draw / total
    implied_away_norm = implied_away / total

    # Market confidence: how decisive are the odds?
    # High confidence = large gap between favorite and underdog
    max_prob = max(implied_home_norm, implied_draw_norm, implied_away_norm)
    market_confidence = (max_prob - 0.33) / 0.67  # 0-1 scale

    return {
        "has_odds": True,
        "implied_home_win": round(implied_home_norm, 3),
        "implied_draw": round(implied_draw_norm, 3),
        "implied_away_win": round(implied_away_norm, 3),
        "market_confidence": round(market_confidence, 3),
        "favorite": "home" if implied_home_norm > implied_away_norm else "away",
        "odds_home": home_odds,
        "odds_draw": draw_odds,
        "odds_away": away_odds,
    }


def calculate_squad_value_factors(
    home_value_m: float | None,
    away_value_m: float | None
) -> dict[str, Any]:
    """Calculate factors from squad market value (Transfermarkt data).

    Args:
        home_value_m: Home team squad value in millions EUR
        away_value_m: Away team squad value in millions EUR

    Returns:
        Value ratios and quality indicators
    """
    if not home_value_m or not away_value_m:
        return {
            "has_value_data": False,
            "value_ratio": 1.0,
            "home_quality_tier": "mid",
            "away_quality_tier": "mid",
        }

    value_ratio = home_value_m / away_value_m

    # Tier classification
    def get_tier(value: float) -> str:
        if value > 800:
            return "elite"
        elif value > 500:
            return "top"
        elif value > 300:
            return "mid"
        else:
            return "low"

    return {
        "has_value_data": True,
        "home_value_m_eur": home_value_m,
        "away_value_m_eur": away_value_m,
        "value_ratio": round(value_ratio, 2),
        "home_quality_tier": get_tier(home_value_m),
        "away_quality_tier": get_tier(away_value_m),
        "value_advantage": "home" if value_ratio > 1.2 else ("away" if value_ratio < 0.8 else "balanced"),
    }


def calculate_sentiment_factors(sentiment_data: dict[str, Any] | None) -> dict[str, Any]:
    """Calculate sentiment from media and social signals (optional).

    Args:
        sentiment_data: Media mentions, social sentiment scores

    Returns:
        Sentiment indicators
    """
    if not sentiment_data:
        return {
            "has_sentiment": False,
            "home_sentiment": 0.5,
            "away_sentiment": 0.5,
        }

    return {
        "has_sentiment": True,
        "home_sentiment": sentiment_data.get("home_score", 0.5),
        "away_sentiment": sentiment_data.get("away_score", 0.5),
        "media_buzz": sentiment_data.get("media_mentions", 0),
    }


# ============================================================================
# 5. MODEL FEATURES (DERIVED SIGNALS)
# ============================================================================

def derive_model_features(
    home_factors: dict[str, Any],
    away_factors: dict[str, Any],
    context: dict[str, Any]
) -> dict[str, Any]:
    """Derive additional features for ML models (XGBoost, etc).

    These are combinations and transformations of base factors.
    """
    # Elo difference
    home_elo = home_factors.get("elo_rating", 1500)
    away_elo = away_factors.get("elo_rating", 1500)
    elo_diff = home_elo - away_elo

    # Expected win probability from Elo
    elo_win_prob = 1 / (1 + 10 ** (-elo_diff / 400))

    # Attack vs Defense matchup
    home_attack = home_factors.get("attack_strength", 1.0)
    away_defense = away_factors.get("defense_strength", 1.0)
    home_matchup_advantage = home_attack / away_defense

    away_attack = away_factors.get("attack_strength", 1.0)
    home_defense = home_factors.get("defense_strength", 1.0)
    away_matchup_advantage = away_attack / home_defense

    # Form momentum
    home_form = home_factors.get("form_rating", 0.5)
    away_form = away_factors.get("form_rating", 0.5)
    form_diff = home_form - away_form

    # Combined quality score
    home_quality = (home_elo / 1500 + home_factors.get("form_rating", 0.5)) / 2
    away_quality = (away_elo / 1500 + away_factors.get("form_rating", 0.5)) / 2

    return {
        "elo_difference": round(elo_diff, 1),
        "elo_win_probability": round(elo_win_prob, 3),
        "home_matchup_advantage": round(home_matchup_advantage, 3),
        "away_matchup_advantage": round(away_matchup_advantage, 3),
        "form_differential": round(form_diff, 3),
        "home_quality_score": round(home_quality, 3),
        "away_quality_score": round(away_quality, 3),
        "quality_gap": round(abs(home_quality - away_quality), 3),
        "expected_total_goals": round(
            home_factors.get("goals_per_game", 1.5) * 0.5 +
            away_factors.get("goals_per_game", 1.5) * 0.5,
            2
        ),
    }


# ============================================================================
# MAIN INTEGRATION FUNCTION
# ============================================================================

def calculate_comprehensive_factors(
    home_team_name: str,
    away_team_name: str,
    home_team_stats: dict[str, Any],
    away_team_stats: dict[str, Any],
    h2h_data: dict[str, Any] | None = None,
    venue_data: dict[str, Any] | None = None,
    betting_odds: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate comprehensive prediction factors across all categories.

    This is the main entry point that aggregates all factor calculations.

    Args:
        home_team_name: Home team name
        away_team_name: Away team name
        home_team_stats: Home team statistics
        away_team_stats: Away team statistics
        h2h_data: Head-to-head historical data
        venue_data: Venue information
        betting_odds: Market odds
        context: Additional context (stage, etc.)

    Returns:
        Comprehensive factor dictionary
    """
    context = context or {}
    venue_data = venue_data or {}

    # Category 1: Team Strength
    home_strength = {
        "elo_rating": calculate_elo_rating(home_team_stats),
        "attack_strength": calculate_attack_strength(home_team_stats),
        "defense_strength": calculate_defense_strength(home_team_stats),
        "form_rating": calculate_form_rating(home_team_stats),
        "squad_availability": calculate_squad_availability(home_team_stats),
    }

    away_strength = {
        "elo_rating": calculate_elo_rating(away_team_stats),
        "attack_strength": calculate_attack_strength(away_team_stats),
        "defense_strength": calculate_defense_strength(away_team_stats),
        "form_rating": calculate_form_rating(away_team_stats),
        "squad_availability": calculate_squad_availability(away_team_stats),
    }

    # Category 2: Historical Performance
    h2h_factors = calculate_h2h_factors(h2h_data)
    home_opp_quality = calculate_opponent_quality_performance(home_team_stats)
    away_opp_quality = calculate_opponent_quality_performance(away_team_stats)

    # Category 3: Situational Context
    home_fatigue = calculate_fatigue_factor(home_team_stats)
    away_fatigue = calculate_fatigue_factor(away_team_stats)
    home_venue = calculate_venue_factors(
        is_home=True,
        venue=venue_data.get("venue"),
        travel_distance_km=venue_data.get("home_travel_km"),
        altitude_m=venue_data.get("altitude_m"),
        climate=venue_data.get("climate")
    )
    away_venue = calculate_venue_factors(
        is_home=False,
        venue=venue_data.get("venue"),
        travel_distance_km=venue_data.get("away_travel_km"),
        altitude_m=venue_data.get("altitude_m"),
        climate=venue_data.get("climate")
    )

    # Category 4: Market Signals
    market_factors = calculate_betting_market_factors(betting_odds)
    value_factors = calculate_squad_value_factors(
        home_team_stats.get("squad_value_m"),
        away_team_stats.get("squad_value_m")
    )

    # Category 5: Derived Model Features
    model_features = derive_model_features(home_strength, away_strength, context)

    return {
        "home_team": home_team_name,
        "away_team": away_team_name,
        "home_strength": home_strength,
        "away_strength": away_strength,
        "head_to_head": h2h_factors,
        "home_opponent_quality": home_opp_quality,
        "away_opponent_quality": away_opp_quality,
        "home_fatigue": home_fatigue,
        "away_fatigue": away_fatigue,
        "home_venue": home_venue,
        "away_venue": away_venue,
        "market_signals": market_factors,
        "squad_value": value_factors,
        "model_features": model_features,
        "context": context,
    }
