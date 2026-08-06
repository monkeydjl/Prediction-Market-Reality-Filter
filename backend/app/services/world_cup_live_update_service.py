"""Live match prediction updates during active matches.

This module handles prediction updates for matches that are currently
in play or near kickoff. Instead of blindly re-running the full prediction
pipeline every 2 minutes (which wastes CPU when inputs haven't changed),
it:

1. For in-play matches: Only detects status changes (e.g., match finished,
   score updated). Does NOT re-run predictions during play unless the
   match status changed.

2. For pre-match (within 15 min of kickoff): Re-runs predictions to capture
   last-minute odds changes and team news.

3. For newly finished matches: Triggers post-match scoring via the
   scoring service, closing the feedback loop.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from app.models.world_cup_prediction import MatchFixture
from app.services.world_cup_prediction_pipeline import run_prediction_pipeline
from app.utils.prediction_db import get_prediction_session, close_prediction_session

logger = logging.getLogger(__name__)


def get_live_matches() -> list[str]:
    """Get list of matches currently in play."""
    session = get_prediction_session()
    try:
        matches = session.query(MatchFixture).filter(
            MatchFixture.status == "in_play"
        ).all()
        return [m.match_id for m in matches]
    finally:
        close_prediction_session(session)


def get_matches_near_kickoff(window_minutes: int = 15) -> list[str]:
    """Get matches starting within the next N minutes."""
    session = get_prediction_session()
    try:
        now = datetime.now(timezone.utc)
        window_end = now + timedelta(minutes=window_minutes)

        matches = session.query(MatchFixture).filter(
            MatchFixture.status == "scheduled",
            MatchFixture.kickoff_utc >= now,
            MatchFixture.kickoff_utc <= window_end
        ).all()

        return [m.match_id for m in matches]
    finally:
        close_prediction_session(session)


def get_newly_finished_matches() -> list[str]:
    """Get matches that just finished (status changed to 'finished').

    These need post-match scoring to close the feedback loop.
    """
    session = get_prediction_session()
    try:
        # Find finished matches that don't have a MatchResult yet
        from app.models.world_cup_prediction import MatchResult

        finished_match_ids = session.query(MatchFixture.match_id).filter(
            MatchFixture.status == "finished",
            MatchFixture.home_score.isnot(None),
            MatchFixture.away_score.isnot(None),
        ).all()
        finished_ids = [r[0] for r in finished_match_ids]

        if not finished_ids:
            return []

        scored_ids = session.query(MatchResult.match_id).filter(
            MatchResult.match_id.in_(finished_ids)
        ).all()
        scored_set = {r[0] for r in scored_ids}

        return [mid for mid in finished_ids if mid not in scored_set]
    finally:
        close_prediction_session(session)


async def update_live_predictions() -> dict[str, Any]:
    """Main entry point called by the scheduler every 2 minutes.

    Strategy:
    - In-play matches: Only detect status changes, do NOT re-run predictions
      (odds don't refresh during play, and inputs haven't changed)
    - Pre-match (15 min before kickoff): Re-run predictions for last-minute
      odds/team news updates
    - Newly finished: Trigger post-match scoring (feedback loop)

    Returns:
        Summary of actions taken
    """
    logger.info("[Live Update] Checking match statuses at %s", datetime.now(timezone.utc).isoformat())

    # 1. Check for newly finished matches → trigger scoring
    newly_finished = get_newly_finished_matches()
    scored_count = 0
    if newly_finished:
        try:
            from app.services.world_cup_scoring_service import score_finished_match
            for match_id in newly_finished:
                result = score_finished_match(match_id)
                if result:
                    scored_count += 1
                    logger.info("[Live Update] Scored finished match %s: %s", match_id, result.get("status"))
        except Exception as e:
            logger.error("[Live Update] Scoring failed: %s", e, exc_info=True)

    # 2. Pre-match updates (within 15 min of kickoff)
    pre_match_ids = get_matches_near_kickoff(window_minutes=15)
    updated_count = 0

    for match_id in pre_match_ids:
        try:
            result = await run_prediction_pipeline(
                match_id,
                trigger="live_update"
            )
            if result.get("status") == "ok":
                updated_count += 1
                logger.info("[Live Update] Pre-match update %s", match_id)
        except Exception as e:
            logger.error("[Live Update] Pre-match update failed %s: %s", match_id, e)

    # 3. In-play matches: detect status changes only (no re-prediction)
    live_match_ids = get_live_matches()

    # Log in-play matches but don't re-run predictions
    if live_match_ids:
        logger.info(
            "[Live Update] %d matches in play (monitoring status only, no re-prediction)",
            len(live_match_ids),
        )

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "in_play_count": len(live_match_ids),
        "pre_match_updated": updated_count,
        "newly_finished_scored": scored_count,
        "actions": {
            "pre_match_predictions": updated_count,
            "post_match_scoring": scored_count,
            "in_play_monitoring": len(live_match_ids),
        }
    }
