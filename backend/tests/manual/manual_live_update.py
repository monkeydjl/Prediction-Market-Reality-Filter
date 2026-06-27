"""Test live match prediction updates."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from app.utils.prediction_db import init_prediction_db, get_prediction_session, close_prediction_session
from app.models.world_cup_prediction import MatchFixture
from app.services.world_cup_live_update_service import (
    get_live_matches,
    get_matches_near_kickoff,
    update_live_predictions,
)


def setup_test_scenarios():
    """Create test fixtures with different statuses."""
    session = get_prediction_session()
    try:
        # Clear existing data
        session.query(MatchFixture).delete()

        now = datetime.now(timezone.utc)

        # Scenario 1: Live match (in play)
        live_match = MatchFixture(
            match_id="live_001",
            fixture_id=20001,
            home_team="Brazil",
            away_team="Argentina",
            kickoff_utc=now - timedelta(minutes=30),  # Started 30 mins ago
            venue="Estadio Azteca",
            stage="SEMI_FINAL",
            status="in_play"
        )
        session.add(live_match)

        # Scenario 2: Match starting in 10 minutes
        upcoming_match = MatchFixture(
            match_id="upcoming_001",
            fixture_id=20002,
            home_team="France",
            away_team="Germany",
            kickoff_utc=now + timedelta(minutes=10),
            venue="MetLife Stadium",
            stage="SEMI_FINAL",
            status="scheduled"
        )
        session.add(upcoming_match)

        # Scenario 3: Match starting in 1 hour (should not trigger)
        future_match = MatchFixture(
            match_id="future_001",
            fixture_id=20003,
            home_team="Spain",
            away_team="England",
            kickoff_utc=now + timedelta(hours=1),
            venue="SoFi Stadium",
            stage="GROUP_STAGE",
            group="A",
            status="scheduled"
        )
        session.add(future_match)

        # Scenario 4: Finished match (should not trigger)
        finished_match = MatchFixture(
            match_id="finished_001",
            fixture_id=20004,
            home_team="Netherlands",
            away_team="Portugal",
            kickoff_utc=now - timedelta(hours=2),
            venue="AT&T Stadium",
            stage="GROUP_STAGE",
            group="B",
            status="finished"
        )
        session.add(finished_match)

        session.commit()
        print("✓ Created 4 test fixtures:")
        print("  - 1 live match (in_play)")
        print("  - 1 upcoming match (10 min)")
        print("  - 1 future match (1 hour)")
        print("  - 1 finished match\n")

    finally:
        close_prediction_session(session)


async def test_live_update():
    """Test live match update functionality."""
    print("\n=== Live Match Update Test ===\n")

    # Setup
    print("Setup: Initialize database and create test fixtures")
    init_prediction_db()
    setup_test_scenarios()

    # Test 1: Get live matches
    print("Test 1: Get live matches")
    live_ids = get_live_matches()
    print(f"  Found {len(live_ids)} live matches: {live_ids}")
    assert len(live_ids) == 1, "Should find exactly 1 live match"
    assert "live_001" in live_ids
    print("  ✓ Correct\n")

    # Test 2: Get matches near kickoff
    print("Test 2: Get matches near kickoff (15 min window)")
    near_kickoff_ids = get_matches_near_kickoff(window_minutes=15)
    print(f"  Found {len(near_kickoff_ids)} upcoming matches: {near_kickoff_ids}")
    assert len(near_kickoff_ids) == 1, "Should find exactly 1 upcoming match"
    assert "upcoming_001" in near_kickoff_ids
    print("  ✓ Correct\n")

    # Test 3: Run live update
    print("Test 3: Run live prediction update")
    result = await update_live_predictions()
    print(f"\nUpdate Result:")
    print(f"  Status: {result.get('status')}")
    print(f"  Matches checked: {result.get('matches_checked')}")
    print(f"  Live count: {result.get('live_count')}")
    print(f"  Pre-match count: {result.get('pre_match_count')}")
    print(f"  Updated: {result.get('updated')}")
    print(f"  Failed: {result.get('failed')}")

    assert result.get("status") == "ok"
    assert result.get("matches_checked") == 2  # 1 live + 1 upcoming
    assert result.get("updated") == 2
    print("\n  ✓ Live update completed successfully\n")

    # Test 4: Verify no updates when no live matches
    print("Test 4: Update all matches to finished")
    session = get_prediction_session()
    try:
        session.query(MatchFixture).update({"status": "finished"})
        session.commit()
    finally:
        close_prediction_session(session)

    result = await update_live_predictions()
    print(f"  Matches checked: {result.get('matches_checked')}")
    assert result.get("matches_checked") == 0
    print("  ✓ Correctly skipped when no live matches\n")

    print("=== All Tests Passed ===")


if __name__ == "__main__":
    asyncio.run(test_live_update())
