"""Scheduler for daily World Cup prediction updates.

This module schedules automatic prediction updates:
- Daily pre-match predictions at 06:00 UTC
- Updates all remaining matches
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.services.world_cup_match_service import sync_world_cup_fixtures
from app.services.world_cup_post_match_backfill_service import run_post_match_backfill
from app.services.world_cup_prediction_pipeline import batch_predict_matches

logger = logging.getLogger(__name__)


async def run_daily_prediction_update():
    """Daily job to update all match predictions.

    This runs:
    1. Sync fixtures from API-Football (get any new/updated matches)
    2. Run predictions for all remaining matches
    """

    logger.info("World Cup daily update starting at %s", datetime.now(timezone.utc).isoformat())

    try:
        # Step 1: Sync fixtures (run sync I/O in a worker thread to avoid
        # blocking the event loop; sync_world_cup_fixtures does HTTP + DB).
        logger.info("Syncing fixtures from API-Football...")
        sync_result = await asyncio.to_thread(sync_world_cup_fixtures)

        if sync_result.get("status") == "error":
            logger.error("Fixture sync failed: %s", sync_result.get("error"))
            return {
                "status": "error",
                "step": "fixture_sync",
                "error": sync_result.get("error")
            }

        logger.info("Synced %s fixtures, remaining matches: %s",
                     sync_result.get("fixtures_parsed", 0),
                     sync_result.get("remaining_matches", 0))

        # Step 2: Score any matches that became finished during fixture sync
        # (run_post_match_backfill does HTTP + DB — offload to thread).
        logger.info("Running post-match backfill scoring...")
        post_match_result = await asyncio.to_thread(
            run_post_match_backfill,
            dry_run=False,
            sync_first=False,
        )

        logger.info(
            "Post-match backfill completed — candidates: %s, scored: %s, errors: %s",
            post_match_result.get("candidate_count", 0),
            post_match_result.get("scoring", {}).get("scored", 0),
            post_match_result.get("scoring", {}).get("errors", 0),
        )

        # Step 3: Run predictions for all remaining matches
        logger.info("Running predictions...")
        predict_result = await batch_predict_matches(
            match_ids=None,  # All remaining matches
            trigger="daily_update"
        )

        logger.info("Predictions completed — total: %s, succeeded: %s, failed: %s, skipped: %s",
                     predict_result.get("total", 0),
                     predict_result.get("succeeded", 0),
                     predict_result.get("failed", 0),
                     predict_result.get("skipped", 0))

        return {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fixture_sync": sync_result,
            "post_match_backfill": post_match_result,
            "predictions": predict_result
        }

    except Exception as e:
        logger.error("World Cup daily update error: %s", e, exc_info=True)
        return {
            "status": "error",
            "error": str(e)
        }
