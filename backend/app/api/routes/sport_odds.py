"""Sport odds API routes — traditional sportsbook odds snapshots.

All endpoints gated by PHASE7_SPORT_MARKET_BRIDGE_ENABLED (503 when false).
Both are GET (read-only) — no require_write_key auth.
Route order: static paths (/history) before dynamic /{match_id}/latest.

``skip_reason`` carries one of two words: ``no_odds`` (the store answered, there
is nothing) or ``read_failed`` (the store did not answer). A disabled bridge
never reaches a body — it is a 503 from the gate — so the three meanings of an
empty body stay separate (audit 53.180).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

from app.core import config
from app.utils.failure_policy import READ_FAILED
from app.utils.helpers import utc_isoformat

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Import-time only for the checker; the runtime import stays inside _store()
    # so the store (and its DB engine) is not constructed at module import.
    from app.kernel.traditional_odds_store import TraditionalOddsStore

router = APIRouter(prefix="/sport-odds", tags=["Sport Odds"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_SPORT_MARKET_BRIDGE_ENABLED:
        raise HTTPException(
            status_code=503, detail="Sport market bridge is disabled."
        )


def _store() -> "TraditionalOddsStore":
    from app.kernel.traditional_odds_store import TraditionalOddsStore
    return TraditionalOddsStore()


@router.get("/{match_id}/history")
def get_history(
    match_id: str,
    mapped_outcome: str | None = Query(None),
) -> dict:
    """Historical traditional odds time-series for a match.

    Returns one series per mapped_outcome, each with all snapshots ordered
    oldest-first (chart x-axis order).
    """
    _ensure_enabled()
    store = _store()
    # An empty list here means the store answered and this match has no
    # snapshots. A failed read is a different fact and gets a different word
    # (audit 53.180); the store propagates it, and this is the only layer that
    # both knows it happened and can say so in the body.
    try:
        snapshots = store.get_snapshots(match_id=match_id, mapped_outcome=mapped_outcome)
    except Exception as exc:
        logger.warning(
            "sport_odds history read failed [match_id=%s]: %s",
            match_id,
            type(exc).__name__,
            exc_info=True,
        )
        return {
            "match_id": match_id,
            "series": [],
            "skipped": True,
            "skip_reason": READ_FAILED,
        }
    if not snapshots:
        return {"match_id": match_id, "series": [], "skipped": True, "skip_reason": "no_odds"}

    # Group by mapped_outcome
    by_outcome: dict[str, list[dict]] = {}
    for snap in snapshots:
        outcome = snap["mapped_outcome"]
        by_outcome.setdefault(outcome, []).append({
            "implied_prob": snap["implied_prob"],
            "decimal_odds": snap["decimal_odds"],
            "bookmaker": snap["bookmaker"],
            "bookmakers_count": snap["bookmakers_count"],
            "captured_at": utc_isoformat(snap["captured_at"]),
        })

    series = [
        {"mapped_outcome": outcome, "snapshots": snaps}
        for outcome, snaps in by_outcome.items()
    ]
    return {"match_id": match_id, "series": series, "skipped": False, "skip_reason": None}


@router.get("/{match_id}/latest")
def get_latest(match_id: str) -> dict:
    """Latest traditional odds snapshot for each outcome of a match."""
    _ensure_enabled()
    store = _store()
    try:
        snapshots = store.get_snapshots(match_id=match_id)
    except Exception as exc:
        logger.warning(
            "sport_odds latest read failed [match_id=%s]: %s",
            match_id,
            type(exc).__name__,
            exc_info=True,
        )
        return {
            "match_id": match_id,
            "outcomes": [],
            "skipped": True,
            "skip_reason": READ_FAILED,
        }
    if not snapshots:
        return {
            "match_id": match_id,
            "outcomes": [],
            "skipped": True,
            "skip_reason": "no_odds",
        }

    # Get the latest snapshot per outcome
    latest_by_outcome: dict[str, dict] = {}
    for snap in snapshots:
        outcome = snap["mapped_outcome"]
        # snapshots are ordered oldest-first, so last one wins
        latest_by_outcome[outcome] = {
            "mapped_outcome": outcome,
            "implied_prob": snap["implied_prob"],
            "decimal_odds": snap["decimal_odds"],
            "bookmaker": snap["bookmaker"],
            "bookmakers_count": snap["bookmakers_count"],
            "captured_at": utc_isoformat(snap["captured_at"]),
        }

    return {
        "match_id": match_id,
        "outcomes": list(latest_by_outcome.values()),
        "skipped": False,
        "skip_reason": None,
    }
