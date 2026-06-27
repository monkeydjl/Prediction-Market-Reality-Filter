"""Test batch prediction with multiple fixtures."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from app.utils.prediction_db import init_prediction_db, get_prediction_session, close_prediction_session
from app.models.world_cup_prediction import MatchFixture
from app.services.world_cup_prediction_pipeline import batch_predict_matches


def setup_test_fixtures():
    """Create multiple test fixtures."""
    session = get_prediction_session()
    try:
        fixtures = [
            ("test_001", "Brazil", "Argentina", "GROUP_STAGE", "A"),
            ("test_002", "Germany", "Spain", "GROUP_STAGE", "B"),
            ("test_003", "France", "England", "GROUP_STAGE", "C"),
            ("test_004", "Netherlands", "Portugal", "GROUP_STAGE", "D"),
            ("test_005", "USA", "Mexico", "ROUND_OF_16", None),
        ]

        for i, (match_id, home, away, stage, group) in enumerate(fixtures):
            fixture = MatchFixture(
                match_id=match_id,
                fixture_id=10000 + i,
                home_team=home,
                away_team=away,
                kickoff_utc=datetime.now(timezone.utc) + timedelta(days=i+1),
                venue=f"Stadium {i+1}",
                stage=stage,
                group=group,
                status="scheduled"
            )
            session.add(fixture)

        session.commit()
        print(f"✓ Created {len(fixtures)} test fixtures\n")
        return [f[0] for f in fixtures]

    finally:
        close_prediction_session(session)


async def test_batch_prediction():
    """Test batch prediction pipeline."""
    print("\n=== Batch Prediction Test ===\n")

    # Setup
    print("Setup: Initialize database and create fixtures")
    init_prediction_db()
    match_ids = setup_test_fixtures()

    # Run batch prediction
    print("Running batch prediction...\n")
    result = await batch_predict_matches(match_ids=match_ids, trigger="batch_test")

    # Display results
    print(f"Batch Result:")
    print(f"  Total: {result.get('total')}")
    print(f"  Succeeded: {result.get('succeeded')}")
    print(f"  Failed: {result.get('failed')}")
    print(f"  Skipped: {result.get('skipped')}\n")

    # Show individual predictions
    print("Individual Predictions:")
    for pred in result.get('predictions', []):
        if pred.get('status') == 'ok':
            home = pred.get('home_team')
            away = pred.get('away_team')
            score = pred.get('predicted_score', {})
            conf = pred.get('confidence', 0)
            print(f"  {home} vs {away}: {score.get('home'):.2f} - {score.get('away'):.2f} (conf: {conf:.0%})")
        else:
            print(f"  {pred.get('match_id')}: ERROR - {pred.get('error')}")

    print("\n=== Test Complete ===")


if __name__ == "__main__":
    asyncio.run(test_batch_prediction())
