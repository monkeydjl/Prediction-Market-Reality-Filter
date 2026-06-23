"""Scheduler for daily World Cup prediction updates.

This module schedules automatic prediction updates:
- Daily pre-match predictions at 06:00 UTC
- Updates all remaining matches
"""

import asyncio
from datetime import datetime

from app.services.world_cup_match_service import sync_world_cup_fixtures
from app.services.world_cup_prediction_pipeline import batch_predict_matches


async def run_daily_prediction_update():
    """Daily job to update all match predictions.

    This runs:
    1. Sync fixtures from API-Football (get any new/updated matches)
    2. Run predictions for all remaining matches
    """

    print(f"[World Cup Daily Update] Starting at {datetime.utcnow().isoformat()}")

    try:
        # Step 1: Sync fixtures
        print("[World Cup Daily Update] Syncing fixtures from API-Football...")
        sync_result = sync_world_cup_fixtures()

        if sync_result.get("status") == "error":
            print(f"[World Cup Daily Update] Fixture sync failed: {sync_result.get('error')}")
            return {
                "status": "error",
                "step": "fixture_sync",
                "error": sync_result.get("error")
            }

        print(f"[World Cup Daily Update] Synced {sync_result.get('fixtures_parsed', 0)} fixtures")
        print(f"[World Cup Daily Update] Remaining matches: {sync_result.get('remaining_matches', 0)}")

        # Step 2: Run predictions for all remaining matches
        print("[World Cup Daily Update] Running predictions...")
        predict_result = await batch_predict_matches(
            match_ids=None,  # All remaining matches
            trigger="daily_update"
        )

        print(f"[World Cup Daily Update] Predictions completed:")
        print(f"  - Total: {predict_result.get('total', 0)}")
        print(f"  - Succeeded: {predict_result.get('succeeded', 0)}")
        print(f"  - Failed: {predict_result.get('failed', 0)}")
        print(f"  - Skipped: {predict_result.get('skipped', 0)}")

        return {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "fixture_sync": sync_result,
            "predictions": predict_result
        }

    except Exception as e:
        print(f"[World Cup Daily Update] Error: {e}")
        return {
            "status": "error",
            "error": str(e)
        }


def register_world_cup_prediction_jobs(scheduler):
    """Register World Cup prediction jobs with the scheduler.

    Args:
        scheduler: APScheduler instance
    """

    # Daily prediction update at 06:00 UTC
    scheduler.add_job(
        run_daily_prediction_update,
        trigger="cron",
        hour=6,
        minute=0,
        id="world_cup_daily_prediction_update",
        name="World Cup Daily Prediction Update",
        replace_existing=True
    )

    print("[Scheduler] Registered World Cup daily prediction update job (06:00 UTC)")
