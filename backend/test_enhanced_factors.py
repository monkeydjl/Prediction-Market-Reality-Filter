"""Test enhanced factor calculation system."""

import sys
import json

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from app.services.world_cup_enhanced_factors import (
    calculate_comprehensive_factors,
    calculate_elo_rating,
    calculate_betting_market_factors,
    derive_model_features,
)


def test_comprehensive_factors():
    """Test comprehensive factor calculation with realistic data."""

    print("=== Enhanced Factor System Test ===\n")

    # Scenario: Brazil vs Argentina (high-profile match)
    home_team_stats = {
        "elo_rating": 2100,
        "fifa_ranking": 3,
        "goals_per_game": 2.1,
        "goals_conceded_per_game": 0.8,
        "xg_per_game": 2.0,
        "xga_per_game": 0.9,
        "wins": 7,
        "draws": 2,
        "losses": 1,
        "injured_players": 1,
        "suspended_players": 0,
        "key_injured": 0,
        "squad_size": 23,
        "last_match_date": "2026-06-18T20:00:00Z",
        "matches_last_14_days": 2,
        "squad_value_m": 850.0,
        "vs_top_teams": {"win_rate": 0.6},
        "vs_mid_teams": {"win_rate": 0.75},
        "vs_low_teams": {"win_rate": 0.85},
    }

    away_team_stats = {
        "elo_rating": 2050,
        "fifa_ranking": 5,
        "goals_per_game": 1.9,
        "goals_conceded_per_game": 0.9,
        "xg_per_game": 1.85,
        "xga_per_game": 1.0,
        "wins": 6,
        "draws": 3,
        "losses": 1,
        "injured_players": 2,
        "suspended_players": 1,
        "key_injured": 1,
        "squad_size": 23,
        "last_match_date": "2026-06-19T20:00:00Z",
        "matches_last_14_days": 2,
        "squad_value_m": 900.0,
        "vs_top_teams": {"win_rate": 0.55},
        "vs_mid_teams": {"win_rate": 0.70},
        "vs_low_teams": {"win_rate": 0.80},
    }

    h2h_data = {
        "matches_played": 12,
        "home_wins": 5,
        "draws": 4,
        "away_wins": 3,
        "avg_goals_home": 1.8,
        "avg_goals_away": 1.5,
    }

    venue_data = {
        "venue": "MetLife Stadium",
        "home_travel_km": 2000,
        "away_travel_km": 3500,
        "altitude_m": 15,
        "climate": "temperate",
    }

    betting_odds = {
        "home": 2.10,  # Implied ~45%
        "draw": 3.20,  # Implied ~28%
        "away": 3.50,  # Implied ~27%
    }

    context = {
        "stage": "SEMI_FINAL",
        "tournament": "2026 FIFA World Cup",
    }

    # Calculate comprehensive factors
    factors = calculate_comprehensive_factors(
        home_team_name="Brazil",
        away_team_name="Argentina",
        home_team_stats=home_team_stats,
        away_team_stats=away_team_stats,
        h2h_data=h2h_data,
        venue_data=venue_data,
        betting_odds=betting_odds,
        context=context,
    )

    # Display results by category
    print("📊 CATEGORY 1: TEAM STRENGTH")
    print("-" * 60)
    print(f"Brazil Elo: {factors['home_strength']['elo_rating']}")
    print(f"Argentina Elo: {factors['away_strength']['elo_rating']}")
    print(f"Brazil Attack: {factors['home_strength']['attack_strength']:.2f}")
    print(f"Argentina Attack: {factors['away_strength']['attack_strength']:.2f}")
    print(f"Brazil Defense: {factors['home_strength']['defense_strength']:.2f}")
    print(f"Argentina Defense: {factors['away_strength']['defense_strength']:.2f}")
    print(f"Brazil Form: {factors['home_strength']['form_rating']:.1%}")
    print(f"Argentina Form: {factors['away_strength']['form_rating']:.1%}")
    print()

    print("📈 CATEGORY 2: HISTORICAL PERFORMANCE")
    print("-" * 60)
    h2h = factors['head_to_head']
    print(f"H2H Matches: {h2h['matches_played']}")
    print(f"Brazil wins: {h2h['home_win_rate']:.1%}")
    print(f"Draws: {h2h['draw_rate']:.1%}")
    print(f"Argentina wins: {h2h['away_win_rate']:.1%}")
    print(f"Historical dominance: {h2h['home_dominance']:+.2f} (Brazil favor)")
    print()

    print("⚡ CATEGORY 3: SITUATIONAL CONTEXT")
    print("-" * 60)
    print(f"Brazil fatigue: {factors['home_fatigue']['fatigue_level']} "
          f"({factors['home_fatigue']['days_since_last_match']} days rest)")
    print(f"Argentina fatigue: {factors['away_fatigue']['fatigue_level']} "
          f"({factors['away_fatigue']['days_since_last_match']} days rest)")
    print(f"Brazil venue modifier: {factors['home_venue']['total_venue_modifier']:+.3f}")
    print(f"Argentina venue modifier: {factors['away_venue']['total_venue_modifier']:+.3f}")
    print(f"Argentina squad issues: {factors['away_strength']['squad_availability']['injured_count']} injured, "
          f"{factors['away_strength']['squad_availability']['suspended_count']} suspended")
    print()

    print("💰 CATEGORY 4: MARKET SIGNALS")
    print("-" * 60)
    market = factors['market_signals']
    print(f"Betting odds: Brazil {market['odds_home']:.2f} | "
          f"Draw {market['odds_draw']:.2f} | Argentina {market['odds_away']:.2f}")
    print(f"Implied probabilities:")
    print(f"  Brazil: {market['implied_home_win']:.1%}")
    print(f"  Draw: {market['implied_draw']:.1%}")
    print(f"  Argentina: {market['implied_away_win']:.1%}")
    print(f"Market confidence: {market['market_confidence']:.1%}")
    print(f"Favorite: {market['favorite']}")
    print()
    value = factors['squad_value']
    print(f"Squad values: Brazil €{value['home_value_m_eur']:.0f}M | "
          f"Argentina €{value['away_value_m_eur']:.0f}M")
    print(f"Value ratio: {value['value_ratio']:.2f}")
    print()

    print("🤖 CATEGORY 5: MODEL FEATURES")
    print("-" * 60)
    model = factors['model_features']
    print(f"Elo difference: {model['elo_difference']:+.0f} (Brazil favor)")
    print(f"Elo win probability: {model['elo_win_probability']:.1%}")
    print(f"Form differential: {model['form_differential']:+.3f}")
    print(f"Brazil quality score: {model['home_quality_score']:.2f}")
    print(f"Argentina quality score: {model['away_quality_score']:.2f}")
    print(f"Quality gap: {model['quality_gap']:.3f}")
    print(f"Expected total goals: {model['expected_total_goals']:.1f}")
    print()

    print("=" * 60)
    print("✅ All factor categories calculated successfully!")
    print()
    print("📋 Key Insights:")
    print(f"  • Brazil slight favorite (Elo +50, market +{(market['implied_home_win'] - market['implied_away_win'])*100:.0f}%)")
    print(f"  • Argentina dealing with injuries ({factors['away_strength']['squad_availability']['impact_modifier']:.2f} impact)")
    print(f"  • Close matchup: historical H2H relatively balanced")
    print(f"  • High-stakes semifinal environment")


if __name__ == "__main__":
    test_comprehensive_factors()
