"""Test World Cup prediction flow with mock data."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from app.utils.prediction_db import init_prediction_db, get_prediction_session, close_prediction_session
from app.models.world_cup_prediction import MatchFixture, MatchPrediction
from app.services.world_cup_prediction_pipeline import run_prediction_pipeline


def setup_test_fixture():
    """Create a test fixture in the database."""
    session = get_prediction_session()
    try:
        # Clear existing data
        session.query(MatchPrediction).delete()
        session.query(MatchFixture).delete()

        # Create test fixture
        fixture = MatchFixture(
            match_id="test_match_001",
            fixture_id=12345,
            home_team="Brazil",
            away_team="Argentina",
            kickoff_utc=datetime.now(timezone.utc) + timedelta(days=1),
            venue="Estadio Azteca",
            stage="GROUP_STAGE",
            group="A",
            status="scheduled"
        )
        session.add(fixture)
        session.commit()

        print(f"✓ Created test fixture: {fixture.home_team} vs {fixture.away_team}")
        return fixture.match_id

    finally:
        close_prediction_session(session)


async def test_prediction():
    """Run prediction pipeline on test fixture."""
    print("\n=== World Cup Prediction Pipeline Test ===\n")

    # Step 1: Initialize database
    print("Step 1: Initialize database")
    init_prediction_db()
    print("✓ Database initialized\n")

    # Step 2: Create test fixture
    print("Step 2: Create test fixture")
    match_id = setup_test_fixture()
    print()

    # Step 3: Run prediction
    print("Step 3: Run prediction pipeline")
    result = await run_prediction_pipeline(match_id, trigger="test")

    print(f"\nPrediction Result:")
    print(f"  Status: {result.get('status')}")
    print(f"  Action: {result.get('action')}")
    print(f"  Match: {result.get('home_team')} vs {result.get('away_team')}")

    if result.get('status') == 'ok':
        pred_score = result.get('predicted_score', {})
        print(f"  Predicted Score: {pred_score.get('home')} - {pred_score.get('away')}")
        print(f"  Confidence: {result.get('confidence'):.2%}")
        print(f"  Method: {result.get('prediction_method')}")
    else:
        print(f"  Error: {result.get('error')}")

    # Step 4: Verify database state
    print("\nStep 4: Verify database state")
    session = get_prediction_session()
    try:
        prediction = session.query(MatchPrediction).filter_by(match_id=match_id).first()
        if prediction:
            print(f"✓ Prediction saved to database")
            print(f"  Score: {prediction.predicted_home_score} - {prediction.predicted_away_score}")
            print(f"  Win probabilities: Home {prediction.home_win_prob:.1%}, Draw {prediction.draw_prob:.1%}, Away {prediction.away_win_prob:.1%}")
            print(f"  Rule-based score: {prediction.rule_home_score} - {prediction.rule_away_score}")
            if prediction.ai_home_score is not None:
                print(f"  AI score: {prediction.ai_home_score} - {prediction.ai_away_score}")
            if prediction.ai_reasoning:
                print(f"  AI reasoning: {prediction.ai_reasoning[:100]}...")
        else:
            print("✗ Prediction not found in database")
    finally:
        close_prediction_session(session)

    print("\n=== Test Complete ===")


if __name__ == "__main__":
    asyncio.run(test_prediction())
