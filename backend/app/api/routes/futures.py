# backend/app/api/routes/futures.py
"""Futures/championship market API routes (Phase 12).

All endpoints gated by PHASE12_FUTURES_MARKETS_ENABLED (503 when false).
Read-only — no writes via API. Writes happen only via scheduler jobs.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.kernel.futures_link_store import FuturesLinkStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/futures", tags=["Futures Markets"])


def _check_enabled() -> None:
    if not settings.PHASE12_FUTURES_MARKETS_ENABLED:
        raise HTTPException(status_code=503, detail="Phase 12 futures markets disabled")


@router.get("/{competition}/{season}")
async def get_futures(competition: str, season: str) -> dict[str, Any]:
    """Get all futures market links for a competition+season pair."""
    _check_enabled()
    store = FuturesLinkStore()
    links = store.get_links(competition, season)
    return {
        "competition": competition,
        "season": season,
        "links": links,
    }


@router.get("/{competition}/{season}/latest")
async def get_latest_snapshots(competition: str, season: str) -> dict[str, Any]:
    """Get the latest price snapshot per team for a competition+season."""
    _check_enabled()
    store = FuturesLinkStore()
    snapshots = store.get_latest_snapshots(competition, season)
    return {
        "competition": competition,
        "season": season,
        "snapshots": snapshots,
    }


@router.get("")
async def list_available_futures() -> dict[str, Any]:
    """List all available (competition, season) pairs that have verified links."""
    _check_enabled()
    store = FuturesLinkStore()
    links = store.get_verified_links()
    # Deduplicate (competition, season) pairs
    seen: set[tuple[str, str]] = set()
    pairs: list[dict[str, str]] = []
    for link in links:
        key = (link["competition"], link["season"])
        if key in seen:
            continue
        seen.add(key)
        pairs.append({"competition": link["competition"], "season": link["season"]})
    return {"pairs": pairs}
