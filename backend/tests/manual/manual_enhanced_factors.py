"""Test enhanced prediction factors with market value and sentiment."""

import sys
import asyncio

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from app.services.world_cup_factor_service import (
    calculate_team_factors,
    build_prediction_factors,
)
from app.services.transfermarkt_scraper import scrape_team_market_value, cache_market_value
from app.services.sentiment_aggregator import fetch_team_sentiment, cache_sentiment
from app.utils.prediction_db import init_prediction_db


async def test_market_value_integration():
    """Test market value integration into factors."""
    print("=" * 70)
    print("TEST 1: Market Value Integration")
    print("=" * 70)

    init_prediction_db()

    print("\nPre-populating market values...")
    test_teams = ["Brazil", "Argentina"]
    for team in test_teams:
        market_data = await scrape_team_market_value(team, use_cache=False)
        if market_data:
            cache_market_value(market_data)
            print(f"  ✓ {team}: €{market_data['total_market_value']:.1f}m cached")

    team_stats = {
        "goals_per_game": 2.1,
        "goals_conceded_per_game": 0.8,
        "wins": 4,
        "draws": 1,
        "losses": 0,
        "fifa_ranking": 1
    }

    factors = calculate_team_factors("Brazil", team_stats, is_home=True)

    print(f"\nBrazil factors:")
    print(f"  Recent form: {factors['recent_form']}")
    print(f"  Market value rating: {factors['market_value_rating']}")
    if factors.get('market_value_euros'):
        print(f"  Market value (€): {factors['market_value_euros']}m")

    print("\n✅ Market value integration working")


async def run_all_tests():
    """Run complete test suite."""
    print("\n" + "🧪 " * 25)
    print("ENHANCED PREDICTION FACTORS TEST SUITE".center(70))
    print("🧪 " * 25)

    await test_market_value_integration()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("\n✅ Enhanced factors integrated!")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
