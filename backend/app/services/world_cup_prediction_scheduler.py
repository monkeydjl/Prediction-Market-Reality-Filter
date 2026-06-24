"""Scheduler for daily World Cup prediction updates.

This module schedules automatic prediction updates:
- Daily pre-match predictions at 06:00 UTC
- Updates all remaining matches
"""

import asyncio
import logging
from datetime import datetime

from app.services.world_cup_match_service import sync_world_cup_fixtures
from app.services.world_cup_prediction_pipeline import batch_predict_matches

logger = logging.getLogger(__name__)


async def run_daily_prediction_update():
    """Daily job to update all match predictions.

    This runs:
    1. Sync fixtures from API-Football (get any new/updated matches)
    2. Run predictions for all remaining matches
    """

    logger.info("World Cup daily update starting at %s", datetime.utcnow().isoformat())

    try:
        # Step 1: Sync fixtures
        logger.info("Syncing fixtures from API-Football...")
        sync_result = sync_world_cup_fixtures()

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

        # Step 2: Run predictions for all remaining matches
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
            "timestamp": datetime.utcnow().isoformat(),
            "fixture_sync": sync_result,
            "predictions": predict_result
        }

    except Exception as e:
        logger.error("World Cup daily update error: %s", e, exc_info=True)
        return {
            "status": "error",
            "error": str(e)
        }
