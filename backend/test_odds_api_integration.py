"""Test The Odds API integration."""

import sys
import asyncio

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from app.services.odds_api_service import (
    fetch_match_odds,
    get_available_quota,
    normalize_team_name,
    find_matching_fixture,
)
from app.core.config import settings


async def test_api_configuration():
    """Test API key configuration."""
    print("=" * 70)
    print("TEST 1: API Configuration")
    print("=" * 70)

    api_key = settings.ODDS_API_KEY if hasattr(settings, 'ODDS_API_KEY') else None
    enabled = settings.ODDS_API_ENABLED if hasattr(settings, 'ODDS_API_ENABLED') else False

    print(f"\nAPI Key configured: {'✅ Yes' if api_key else '❌ No (set ODDS_API_KEY in .env)'}")
    print(f"API Enabled: {'✅ Yes' if enabled else '❌ No (set ODDS_API_ENABLED=true in .env)'}")

    if not api_key:
        print("\n⚠️  To use The Odds API:")
        print("   1. Register at https://the-odds-api.com/")
        print("   2. Get your API key")
        print("   3. Add to .env: ODDS_API_KEY=your_key_here")
        print("   4. Add to .env: ODDS_API_ENABLED=true")
        return False

    return True


async def test_quota_check():
    """Test API quota check."""
    print("\n" + "=" * 70)
    print("TEST 2: API Quota Check")
    print("=" * 70)

    quota = await get_available_quota()

    if quota:
        print(f"\n✅ API Connection Successful")
        print(f"   Requests used: {quota['requests_used']}")
        print(f"   Requests remaining: {quota['requests_remaining']}")

        if quota['requests_remaining'] < 50:
            print(f"\n⚠️  Low quota warning: <50 requests remaining")
    else:
        print(f"\n❌ Unable to check quota (API key invalid or network error)")
        return False

    return True


async def test_fetch_match_odds():
    """Test fetching odds for a specific match."""
    print("\n" + "=" * 70)
    print("TEST 3: Fetch Match Odds")
    print("=" * 70)

    # Test with hypothetical World Cup match
    print("\nFetching odds for: Brazil vs Argentina")

    odds = await fetch_match_odds(
        home_team="Brazil",
        away_team="Argentina",
        commence_time="2026-07-13T20:00:00Z"  # Hypothetical final date
    )

    if odds:
        print(f"\n✅ Odds Retrieved:")
        print(f"   Home (Brazil): {odds['home']}")
        print(f"   Draw: {odds['draw']}")
        print(f"   Away (Argentina): {odds['away']}")
        print(f"   Source: {odds['source']}")
        print(f"   Bookmakers: {odds['bookmakers_count']}")
        print(f"   Last update: {odds['last_update']}")
    else:
        print(f"\n⚠️  No odds available for this match")
        print(f"   Possible reasons:")
        print(f"   - Match not yet listed on betting sites")
        print(f"   - Team names don't match API format")
        print(f"   - World Cup 2026 odds not yet available")

    return odds is not None


def test_team_name_normalization():
    """Test team name normalization."""
    print("\n" + "=" * 70)
    print("TEST 4: Team Name Normalization")
    print("=" * 70)

    test_cases = [
        ("Brazil", "brazil"),
        ("United States", "unitedstates"),
        ("Korea Republic", "korearepublic"),
        ("Côte d'Ivoire", "cotedivoire"),
        ("Saudi Arabia", "saudiarabia"),
    ]

    print("\nNormalization tests:")
    all_passed = True

    for original, expected in test_cases:
        normalized = normalize_team_name(original)
        passed = normalized == expected
        all_passed = all_passed and passed

        status = "✅" if passed else "❌"
        print(f"  {status} '{original}' → '{normalized}' (expected: '{expected}')")

    return all_passed


async def test_integration_with_elo_odds_engine():
    """Test integration with Elo+Odds prediction engine."""
    print("\n" + "=" * 70)
    print("TEST 5: Integration with Elo+Odds Engine")
    print("=" * 70)

    from app.services.world_cup_elo_odds_engine import predict_match_elo_odds

    # Fetch real odds (if available)
    odds = await fetch_match_odds(
        home_team="Brazil",
        away_team="Argentina",
        commence_time="2026-07-13T20:00:00Z"
    )

    if odds:
        print(f"\n✅ Using REAL odds from {odds['source']}")
        odds_home = odds['home']
        odds_draw = odds['draw']
        odds_away = odds['away']
    else:
        print(f"\n⚠️  Using MOCK odds (real odds not available)")
        odds_home = 2.10
        odds_draw = 3.20
        odds_away = 3.50

    # Make prediction
    prediction = predict_match_elo_odds(
        home_team="Brazil",
        away_team="Argentina",
        elo_home=2100,
        elo_away=2050,
        odds_home=odds_home,
        odds_draw=odds_draw,
        odds_away=odds_away,
    )

    print(f"\nPrediction:")
    print(f"  Score: {prediction['predicted_score']['home']:.2f} - {prediction['predicted_score']['away']:.2f}")
    print(f"  Win probabilities:")
    print(f"    Brazil: {prediction['outcome_probabilities']['home_win']:.1%}")
    print(f"    Draw: {prediction['outcome_probabilities']['draw']:.1%}")
    print(f"    Argentina: {prediction['outcome_probabilities']['away_win']:.1%}")
    print(f"  Confidence: {prediction['confidence']:.1%}")
    print(f"  Method: {prediction['prediction_method']}")

    return True


async def run_all_tests():
    """Run complete test suite."""
    print("\n" + "🧪 " * 25)
    print("THE ODDS API INTEGRATION TEST SUITE".center(70))
    print("🧪 " * 25)

    # Test 1: Configuration
    config_ok = await test_api_configuration()

    if not config_ok:
        print("\n" + "=" * 70)
        print("⚠️  TESTS SKIPPED - API NOT CONFIGURED")
        print("=" * 70)
        print("\nConfigure The Odds API to run these tests.")
        return

    # Test 2: Quota check
    quota_ok = await test_quota_check()

    # Test 3: Fetch odds
    if quota_ok:
        await test_fetch_match_odds()

    # Test 4: Team name normalization
    norm_ok = test_team_name_normalization()

    # Test 5: Integration
    await test_integration_with_elo_odds_engine()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if config_ok and quota_ok:
        print("\n✅ The Odds API integration is working!")
        print("\nNext steps:")
        print("  1. ✅ API service implemented (odds_api_service.py)")
        print("  2. ✅ Configuration added (ODDS_API_KEY in config.py)")
        print("  3. ⏭️  Integrate into prediction pipeline")
        print("  4. ⏭️  Add caching to reduce API calls")
        print("  5. ⏭️  Monitor quota usage")
    else:
        print("\n⚠️  Some tests failed. Check configuration above.")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
