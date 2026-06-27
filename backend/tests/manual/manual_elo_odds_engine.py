"""Test Elo + Odds fusion prediction engine."""

import sys

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from app.services.world_cup_elo_odds_engine import (
    predict_match_elo_odds,
    calculate_elo_win_probability,
    odds_to_probabilities,
    fuse_elo_and_odds,
    predict_matches_batch,
)


def test_elo_only_prediction():
    """Test prediction using only Elo ratings (no odds)."""
    print("=" * 60)
    print("TEST 1: Elo-Only Prediction")
    print("=" * 60)

    prediction = predict_match_elo_odds(
        home_team="Brazil",
        away_team="Argentina",
        elo_home=2100,
        elo_away=2050,
        # No odds provided
    )

    print(f"\n{prediction['home_team']} vs {prediction['away_team']}")
    print(f"Elo ratings: {prediction['elo_ratings']['home']} vs {prediction['elo_ratings']['away']}")
    print(f"Elo difference: {prediction['elo_ratings']['difference']:+.0f}")
    print(f"\nPredicted score: {prediction['predicted_score']['home']:.2f} - {prediction['predicted_score']['away']:.2f}")
    print(f"\nOutcome probabilities:")
    print(f"  {prediction['home_team']} win: {prediction['outcome_probabilities']['home_win']:.1%}")
    print(f"  Draw: {prediction['outcome_probabilities']['draw']:.1%}")
    print(f"  {prediction['away_team']} win: {prediction['outcome_probabilities']['away_win']:.1%}")
    print(f"\nConfidence: {prediction['confidence']:.1%}")
    print(f"Method: {prediction['prediction_method']}")
    print(f"Has betting odds: {prediction['has_betting_odds']}")

    assert prediction['elo_ratings']['difference'] == 50.0
    assert prediction['confidence'] > 0.5
    assert not prediction['has_betting_odds']
    print("\n✅ Elo-only prediction passed")


def test_elo_odds_fusion():
    """Test full Elo + Odds fusion prediction."""
    print("\n" + "=" * 60)
    print("TEST 2: Elo + Odds Fusion")
    print("=" * 60)

    prediction = predict_match_elo_odds(
        home_team="Spain",
        away_team="Germany",
        elo_home=2080,
        elo_away=2070,
        odds_home=2.10,  # Implied ~45%
        odds_draw=3.20,  # Implied ~28%
        odds_away=3.50,  # Implied ~27%
    )

    print(f"\n{prediction['home_team']} vs {prediction['away_team']}")
    print(f"Elo ratings: {prediction['elo_ratings']['home']} vs {prediction['elo_ratings']['away']}")
    print(f"Elo difference: {prediction['elo_ratings']['difference']:+.0f}")

    print(f"\n📊 Elo-based probabilities:")
    elo_probs = prediction['elo_probabilities']
    print(f"  {prediction['home_team']} win: {elo_probs['home_win']:.1%}")
    print(f"  Draw: {elo_probs['draw']:.1%}")
    print(f"  {prediction['away_team']} win: {elo_probs['away_win']:.1%}")

    print(f"\n💰 Market-based probabilities (from odds):")
    market_probs = prediction['market_probabilities']
    print(f"  Odds: {prediction['home_team']} {market_probs} | Draw 3.20 | {prediction['away_team']} 3.50")
    print(f"  {prediction['home_team']} win: {market_probs['home_win']:.1%}")
    print(f"  Draw: {market_probs['draw']:.1%}")
    print(f"  {prediction['away_team']} win: {market_probs['away_win']:.1%}")
    print(f"  Market favorite: {prediction['market_favorite']}")

    print(f"\n🔮 Final fused prediction (30% Elo + 70% Odds):")
    print(f"  {prediction['home_team']} win: {prediction['outcome_probabilities']['home_win']:.1%}")
    print(f"  Draw: {prediction['outcome_probabilities']['draw']:.1%}")
    print(f"  {prediction['away_team']} win: {prediction['outcome_probabilities']['away_win']:.1%}")

    print(f"\nPredicted score: {prediction['predicted_score']['home']:.2f} - {prediction['predicted_score']['away']:.2f}")
    print(f"Confidence: {prediction['confidence']:.1%}")
    print(f"Method: {prediction['prediction_method']}")

    assert prediction['has_betting_odds']
    assert prediction['market_favorite'] in ['home', 'away']
    assert 0.0 <= prediction['confidence'] <= 1.0
    print("\n✅ Elo + Odds fusion passed")


def test_extreme_elo_difference():
    """Test prediction with large Elo gap (heavy favorite)."""
    print("\n" + "=" * 60)
    print("TEST 3: Extreme Elo Difference (Heavy Favorite)")
    print("=" * 60)

    prediction = predict_match_elo_odds(
        home_team="France",
        away_team="Panama",
        elo_home=2150,
        elo_away=1450,
        odds_home=1.20,  # Heavy favorite
        odds_draw=6.00,
        odds_away=15.0,
    )

    print(f"\n{prediction['home_team']} vs {prediction['away_team']}")
    print(f"Elo difference: {prediction['elo_ratings']['difference']:+.0f} (MASSIVE)")

    print(f"\nElo says: {prediction['elo_probabilities']['home_win']:.1%} {prediction['home_team']} win")
    print(f"Market says: {prediction['market_probabilities']['home_win']:.1%} {prediction['home_team']} win")
    print(f"Fused: {prediction['outcome_probabilities']['home_win']:.1%} {prediction['home_team']} win")

    print(f"\nPredicted score: {prediction['predicted_score']['home']:.2f} - {prediction['predicted_score']['away']:.2f}")
    print(f"Confidence: {prediction['confidence']:.1%}")

    # Heavy favorite should have >70% win probability
    assert prediction['outcome_probabilities']['home_win'] > 0.70
    assert prediction['predicted_score']['home'] > prediction['predicted_score']['away'] + 1.0
    print("\n✅ Heavy favorite test passed")


def test_even_matchup():
    """Test prediction for very evenly matched teams."""
    print("\n" + "=" * 60)
    print("TEST 4: Even Matchup (Coin Flip)")
    print("=" * 60)

    prediction = predict_match_elo_odds(
        home_team="Netherlands",
        away_team="Portugal",
        elo_home=2000,
        elo_away=2000,
        odds_home=2.80,  # ~35%
        odds_draw=3.00,  # ~33%
        odds_away=2.80,  # ~35%
    )

    print(f"\n{prediction['home_team']} vs {prediction['away_team']}")
    print(f"Elo difference: {prediction['elo_ratings']['difference']:+.0f} (EVEN)")

    print(f"\nOutcome probabilities:")
    print(f"  {prediction['home_team']} win: {prediction['outcome_probabilities']['home_win']:.1%}")
    print(f"  Draw: {prediction['outcome_probabilities']['draw']:.1%}")
    print(f"  {prediction['away_team']} win: {prediction['outcome_probabilities']['away_win']:.1%}")

    print(f"\nPredicted score: {prediction['predicted_score']['home']:.2f} - {prediction['predicted_score']['away']:.2f}")
    print(f"Confidence: {prediction['confidence']:.1%} (lower for coin-flip matches)")

    # Even matchup should have balanced probabilities
    assert 0.30 <= prediction['outcome_probabilities']['home_win'] <= 0.40
    assert 0.30 <= prediction['outcome_probabilities']['away_win'] <= 0.40
    # Confidence should be moderate for even matches
    assert prediction['confidence'] < 0.75
    print("\n✅ Even matchup test passed")


def test_batch_predictions():
    """Test batch prediction of multiple matches."""
    print("\n" + "=" * 60)
    print("TEST 5: Batch Predictions")
    print("=" * 60)

    matches = [
        {
            "home_team": "Brazil",
            "away_team": "Mexico",
            "elo_home": 2100,
            "elo_away": 1900,
            "odds_home": 1.60,
            "odds_draw": 3.80,
            "odds_away": 5.50,
        },
        {
            "home_team": "England",
            "away_team": "Belgium",
            "elo_home": 2050,
            "elo_away": 2040,
            "odds_home": 2.20,
            "odds_draw": 3.10,
            "odds_away": 3.40,
        },
        {
            "home_team": "Japan",
            "away_team": "South Korea",
            "elo_home": 1850,
            "elo_away": 1880,
            # No odds for this match
        },
    ]

    predictions = predict_matches_batch(matches)

    print(f"\nPredicted {len(predictions)} matches:\n")

    for i, pred in enumerate(predictions, 1):
        print(f"{i}. {pred['home_team']} vs {pred['away_team']}")
        print(f"   Score: {pred['predicted_score']['home']:.2f} - {pred['predicted_score']['away']:.2f}")
        print(f"   Winner: {pred['home_team'] if pred['outcome_probabilities']['home_win'] > pred['outcome_probabilities']['away_win'] else pred['away_team']}")
        print(f"   Confidence: {pred['confidence']:.1%}")
        print(f"   Method: {pred['prediction_method']}")
        print()

    assert len(predictions) == 3
    assert predictions[0]['has_betting_odds']
    assert predictions[1]['has_betting_odds']
    assert not predictions[2]['has_betting_odds']  # No odds for Japan vs South Korea
    print("✅ Batch predictions passed")


def test_odds_conversion():
    """Test direct odds-to-probability conversion."""
    print("\n" + "=" * 60)
    print("TEST 6: Odds Conversion Mechanics")
    print("=" * 60)

    # Example: Premier League typical odds
    odds_home = 2.10
    odds_draw = 3.20
    odds_away = 3.50

    probs = odds_to_probabilities(odds_home, odds_draw, odds_away)

    print(f"\nDecimal odds:")
    print(f"  Home: {odds_home}")
    print(f"  Draw: {odds_draw}")
    print(f"  Away: {odds_away}")

    # Calculate raw implied probabilities
    implied_home = 1 / odds_home
    implied_draw = 1 / odds_draw
    implied_away = 1 / odds_away
    total = implied_home + implied_draw + implied_away
    overround = (total - 1.0) * 100

    print(f"\nRaw implied probabilities (with margin):")
    print(f"  Home: {implied_home:.1%}")
    print(f"  Draw: {implied_draw:.1%}")
    print(f"  Away: {implied_away:.1%}")
    print(f"  Total: {total:.1%} (overround: {overround:.1f}%)")

    print(f"\nNormalized probabilities (margin removed):")
    print(f"  Home: {probs['home_win']:.1%}")
    print(f"  Draw: {probs['draw']:.1%}")
    print(f"  Away: {probs['away_win']:.1%}")
    print(f"  Total: {sum(probs.values()):.1%}")

    # Total should be exactly 1.0 after normalization
    assert abs(sum(probs.values()) - 1.0) < 0.001
    print("\n✅ Odds conversion passed")


def test_elo_formula():
    """Test Elo win probability formula."""
    print("\n" + "=" * 60)
    print("TEST 7: Elo Formula Verification")
    print("=" * 60)

    test_cases = [
        (2000, 2000, 0.50),   # Even → 50%
        (2100, 2000, 0.64),   # +100 → ~64%
        (2200, 2000, 0.76),   # +200 → ~76%
        (2000, 2100, 0.36),   # -100 → ~36%
        (1500, 2000, 0.05),   # -500 → ~5%
    ]

    print("\nElo difference → Win probability:")
    print(f"{'Home Elo':<12} {'Away Elo':<12} {'Diff':<8} {'Win %':<10} {'Expected':<10}")
    print("-" * 60)

    for home, away, expected in test_cases:
        probs = calculate_elo_win_probability(home, away)
        diff = home - away
        actual = probs['home_win']

        print(f"{home:<12} {away:<12} {diff:+8} {actual:<10.1%} {expected:<10.1%}")

        # Allow 2% tolerance
        assert abs(actual - expected) < 0.02, f"Expected {expected:.1%}, got {actual:.1%}"

    print("\n✅ Elo formula verification passed")


def run_all_tests():
    """Run complete test suite."""
    print("\n" + "🧪 " * 20)
    print("ELO + ODDS FUSION ENGINE TEST SUITE")
    print("🧪 " * 20 + "\n")

    test_elo_only_prediction()
    test_elo_odds_fusion()
    test_extreme_elo_difference()
    test_even_matchup()
    test_batch_predictions()
    test_odds_conversion()
    test_elo_formula()

    print("\n" + "=" * 60)
    print("🎉 ALL TESTS PASSED!")
    print("=" * 60)
    print("\n📊 Summary:")
    print("  ✅ Elo-only predictions work")
    print("  ✅ Elo + Odds fusion works")
    print("  ✅ Heavy favorite detection works")
    print("  ✅ Even matchup handling works")
    print("  ✅ Batch processing works")
    print("  ✅ Odds conversion is accurate")
    print("  ✅ Elo formula verified")
    print("\n🚀 Engine ready for production!")


if __name__ == "__main__":
    run_all_tests()
