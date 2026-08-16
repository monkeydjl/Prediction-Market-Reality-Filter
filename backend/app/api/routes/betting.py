"""Betting / 竞猜 module catalog API.

Flag-free, read-only. Does not enable Kernel prediction or invent markets.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.kernel.betting_catalog import (
    build_catalog_payload,
    build_status_payload,
    get_competition,
)

router = APIRouter(prefix="/betting", tags=["Betting"])


@router.get("/catalog")
def get_betting_catalog() -> dict[str, Any]:
    """Return the static 竞猜 competition catalog (competitions + tools)."""
    return build_catalog_payload()


@router.get("/catalog/{competition_id}")
def get_betting_competition(competition_id: str) -> dict[str, Any]:
    """Return a single catalog entry by id (e.g. epl, nba, esports)."""
    row = get_competition(competition_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown competition: {competition_id}")
    return row


@router.get("/status")
def get_betting_status() -> dict[str, Any]:
    """Operator diagnostic: flags + registered MultiAdapter prefixes.

    Always 200 when the API is up. Does not require write key. Does not sync
    or invent fixtures — use POST /predictions/schedule/sync for ingest.
    """
    return build_status_payload()
