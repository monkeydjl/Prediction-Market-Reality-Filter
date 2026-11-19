"""Scheduler for daily World Cup prediction updates.

This module schedules automatic prediction updates:
- Daily pre-match predictions at 06:00 UTC
- Updates all remaining matches
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.services.world_cup_match_service import (
    FIXTURE_SYNC_REASON_CODES,
    FIXTURE_SYNC_UNKNOWN,
    sync_world_cup_fixtures,
)
from app.services.world_cup_post_match_backfill_service import run_post_match_backfill
from app.services.world_cup_prediction_pipeline import batch_predict_matches

logger = logging.getLogger(__name__)

# Why fixture sync failed, as a code -- and only a code.
#
# The classification itself lives in `world_cup_match_service.fixture_sync_reason`,
# next to the messages it reads, because two classifiers over the same text are
# two places to drift apart. This side only **validates**: accept a code if it is
# in the producer's declared vocabulary, otherwise give up to `sync_failed`.
#
# Why validation is not paranoia: everything below is durable. `result_json` is
# returned by `GET /quality-metrics/anomalies`, which carries no auth dependency,
# and is what `/api/health` derives its 503 from; Sentry and the operator webhook
# receive the same dict. `tests/test_scheduler_ledger_alarm_gate.py` pins that no
# upstream error *text* reaches any of them. §53.106 answered "why did the run
# fail" by transcribing that text -- four red tests said so, and §53.107/§53.108
# replaced the sentence with this code path.
def _fixture_sync_reason(result: dict[str, Any]) -> str:
    """Take the producer's code if it is one we know; otherwise give up."""
    reported = result.get("reason")
    if isinstance(reported, str) and reported in FIXTURE_SYNC_REASON_CODES:
        return reported
    return FIXTURE_SYNC_UNKNOWN


async def run_daily_prediction_update() -> dict[str, Any]:
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
            # The sentence says *what* broke, `reason` says *why* -- one is
            # prose this module owns, the other is a code from a closed set.
            reason = _fixture_sync_reason(sync_result)
            logger.error("Fixture sync failed: %s", reason)
            error_result: dict[str, Any] = {
                "status": "error",
                "step": "fixture_sync",
                "error": "World Cup fixture sync failed",
                "reason": reason,
            }
            # Omitted rather than set to None: `source: null` in the ledger reads
            # like a source was reported and went missing.
            if sync_result.get("source"):
                error_result["source"] = sync_result["source"]
            return error_result

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

    except Exception as exc:
        # The **type**, not the message. Whatever raised was free text: an httpx
        # `HTTPStatusError` stringifies with its URL, `sqlite3` errors carry the
        # statement. The type narrows "something broke" to a layer without
        # dragging any of that into durable storage -- same reason the branch
        # above stores a code instead of the sync layer's sentence.
        logger.error("World Cup daily update failed: %s", type(exc).__name__)
        return {
            "status": "error",
            "error": f"World Cup daily update failed: {type(exc).__name__}",
        }
