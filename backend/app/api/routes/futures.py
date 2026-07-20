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
from app.services.futures_market_source import (
    list_known_futures_series,
    multi_leg_integrity,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/futures", tags=["Futures Markets"])


def _check_enabled() -> None:
    if not settings.PHASE12_FUTURES_MARKETS_ENABLED:
        raise HTTPException(status_code=503, detail="Phase 12 futures markets disabled")


def _pair_coverage(
    store: FuturesLinkStore,
    competition: str,
    season: str,
) -> dict[str, Any]:
    links = store.get_links(competition, season)
    verified = [lnk for lnk in links if lnk.get("verified")]
    contracts = [
        {
            "team": lnk.get("team"),
            "price": lnk.get("implied_prob"),
            "ticker": lnk.get("contract_id"),
        }
        for lnk in verified
    ]
    integrity = multi_leg_integrity(contracts)
    return {
        "competition": competition,
        "season": season,
        "link_count": len(links),
        "verified_count": len(verified),
        "integrity": integrity,
    }


@router.get("/meta/series")
async def list_futures_series_registry() -> dict[str, Any]:
    """Registered Kalshi series prefixes → competition / championship_type."""
    _check_enabled()
    series = list_known_futures_series()
    competitions = sorted({s["competition"] for s in series})
    return {
        "series": series,
        "competition_count": len(competitions),
        "series_count": len(series),
        "competitions": competitions,
    }


@router.get("/meta/coverage")
async def list_futures_coverage() -> dict[str, Any]:
    """Coverage report: known series registry + stored multi-leg integrity per pair."""
    _check_enabled()
    store = FuturesLinkStore()
    links = store.get_verified_links()
    pairs_map: dict[tuple[str, str], None] = {}
    for link in links:
        pairs_map[(link["competition"], link["season"])] = None
    pairs = [
        _pair_coverage(store, comp, season)
        for (comp, season) in sorted(pairs_map.keys())
    ]
    status_counts: dict[str, int] = {}
    for p in pairs:
        st = (p.get("integrity") or {}).get("status") or "unknown"
        status_counts[st] = status_counts.get(st, 0) + 1
    series = list_known_futures_series()
    covered_comps = {p["competition"] for p in pairs}
    registered_comps = {s["competition"] for s in series}
    return {
        "series_registry": series,
        "pairs": pairs,
        "pair_count": len(pairs),
        "status_counts": status_counts,
        "registered_competitions": sorted(registered_comps),
        "linked_competitions": sorted(covered_comps),
        "missing_linked_competitions": sorted(registered_comps - covered_comps),
    }


@router.get("/{competition}/{season}")
async def get_futures(competition: str, season: str) -> dict[str, Any]:
    """Get all futures market links for a competition+season pair."""
    _check_enabled()
    store = FuturesLinkStore()
    links = store.get_links(competition, season)
    verified = [lnk for lnk in links if lnk.get("verified")]
    integrity = multi_leg_integrity(
        [
            {
                "team": lnk.get("team"),
                "price": lnk.get("implied_prob"),
                "ticker": lnk.get("contract_id"),
            }
            for lnk in verified
        ]
    )
    return {
        "competition": competition,
        "season": season,
        "links": links,
        "integrity": integrity,
    }


@router.get("/{competition}/{season}/latest")
async def get_latest_snapshots(competition: str, season: str) -> dict[str, Any]:
    """Get the latest price snapshot per team for a competition+season."""
    _check_enabled()
    store = FuturesLinkStore()
    snapshots = store.get_latest_snapshots(competition, season)
    integrity = multi_leg_integrity(
        [
            {
                "team": s.get("team"),
                "price": s.get("implied_prob"),
            }
            for s in snapshots
        ]
    )
    return {
        "competition": competition,
        "season": season,
        "snapshots": snapshots,
        "integrity": integrity,
    }


@router.get("")
async def list_available_futures() -> dict[str, Any]:
    """List all available (competition, season) pairs that have verified links."""
    _check_enabled()
    store = FuturesLinkStore()
    links = store.get_verified_links()
    # Deduplicate (competition, season) pairs
    seen: set[tuple[str, str]] = set()
    pairs: list[dict[str, Any]] = []
    for link in links:
        key = (link["competition"], link["season"])
        if key in seen:
            continue
        seen.add(key)
        cov = _pair_coverage(store, link["competition"], link["season"])
        pairs.append(
            {
                "competition": link["competition"],
                "season": link["season"],
                "verified_count": cov["verified_count"],
                "integrity": cov["integrity"],
            }
        )
    return {"pairs": pairs}
