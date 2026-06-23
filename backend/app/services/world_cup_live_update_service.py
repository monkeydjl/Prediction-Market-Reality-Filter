"""Live match prediction updates during active matches.

This module handles per-minute prediction updates for matches that are currently
in play. It monitors match status and re-runs predictions based on live match state.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.models.world_cup_prediction import MatchFixture
from app.services.world_cup_prediction_pipeline import run_prediction_pipeline
from app.utils.prediction_db import get_prediction_session, close_prediction_session


def get_live_matches() -> list[str]:
    """Get list of matches currently in play.

    Returns:
        List of match IDs that are currently in progress
    """
    session = get_prediction_session()
    try:
        matches = session.query(MatchFixture).filter(
            MatchFixture.status == "in_play"
        ).all()
        return [m.match_id for m in matches]
    finally:
        close_prediction_session(session)


def get_matches_near_kickoff(window_minutes: int = 15) -> list[str]:
    """Get matches starting within the next N minutes.

    This allows predictions to be updated just before kickoff with the latest
    team news (lineups, injuries, etc.)

    Args:
        window_minutes: How many minutes before kickoff to consider

    Returns:
        List of match IDs starting soon
    """
    session = get_prediction_session()
    try:
        now = datetime.now(timezone.utc)
        from datetime import timedelta

        window_start = now
        window_end = now + timedelta(minutes=window_minutes)

        matches = session.query(MatchFixture).filter(
            MatchFixture.status == "scheduled",
            MatchFixture.kickoff_utc >= window_start,
            MatchFixture.kickoff_utc <= window_end
        ).all()

        return [m.match_id for m in matches]
    finally:
        close_prediction_session(session)


async def update_live_predictions() -> dict[str, Any]:
    """Update predictions for all live matches.

    This is the main entry point called by the scheduler every 1-2 minutes.

    Returns:
        Summary of updates performed
    """
    print(f"[Live Update] Checking for live matches at {datetime.utcnow().isoformat()}")

    # Get matches that need updates
    live_match_ids = get_live_matches()
    pre_match_ids = get_matches_near_kickoff(window_minutes=15)

    all_match_ids = list(set(live_match_ids + pre_match_ids))

    if not all_match_ids:
        print("[Live Update] No live or upcoming matches, skipping")
        return {
            "status": "ok",
            "matches_checked": 0,
            "live_count": 0,
            "pre_match_count": 0,
            "updated": 0,
        }

    print(f"[Live Update] Found {len(live_match_ids)} live + {len(pre_match_ids)} pre-match")

    # Update predictions for each match
    results = []
    for match_id in all_match_ids:
        try:
            result = await run_prediction_pipeline(
                match_id,
                trigger="live_update"
            )
            results.append(result)

            if result.get("status") == "ok":
                print(f"[Live Update] Updated {match_id}: {result.get('predicted_score')}")
        except Exception as e:
            print(f"[Live Update] Failed to update {match_id}: {e}")
            results.append({"status": "error", "match_id": match_id, "error": str(e)})

    # Summarize results
    updated_count = sum(1 for r in results if r.get("status") == "ok")

    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "matches_checked": len(all_match_ids),
        "live_count": len(live_match_ids),
        "pre_match_count": len(pre_match_ids),
        "updated": updated_count,
        "failed": len(results) - updated_count,
    }


def register_live_update_job(scheduler):
    """Register live match update job with the scheduler.

    This job runs every 2 minutes during match windows.

    Args:
        scheduler: APScheduler instance
    """
    # Run every 2 minutes
    # More frequent than daily updates but not too aggressive on API quota
    scheduler.add_job(
        update_live_predictions,
        trigger="interval",
        minutes=2,
        id="world_cup_live_prediction_update",
        name="World Cup Live Prediction Update",
        replace_existing=True,
        max_instances=1,  # Don't overlap runs
    )

    print("[Scheduler] Registered World Cup live prediction update job (every 2 minutes)")
