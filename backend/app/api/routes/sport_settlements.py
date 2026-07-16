"""Sport settlement API routes.

All endpoints gated by PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED (503 when false).
3 GET endpoints are read-only. 1 POST endpoint requires require_write_key.
Route order: static paths (/calibrations, /history) before dynamic /{match_id}
to avoid FastAPI catch-all routing (lesson from Subproject C).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.security import require_write_key
from app.core import config

router = APIRouter(prefix="/sport-settlements", tags=["Sport Settlements"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED:
        raise HTTPException(
            status_code=503, detail="Market settlement feedback is disabled."
        )


def _service():
    from app.kernel.market_settlement_service import MarketSettlementService
    return MarketSettlementService()


@router.get("/calibrations")
def get_calibrations(
    engine: str | None = Query(None),
    competition: str | None = Query(None),
) -> dict:
    """Market calibration list. Static path before /{match_id}."""
    _ensure_enabled()
    svc = _service()
    items = svc.get_calibrations(engine=engine, competition=competition)
    return {"items": items, "total": len(items)}


@router.get("/history")
def get_history(
    limit: int = Query(20, ge=1, le=100),
    engine: str | None = Query(None),
) -> dict:
    """Settlement history. Static path before /{match_id}."""
    _ensure_enabled()
    svc = _service()
    items = svc.get_history(limit=limit, engine=engine)
    return {"items": items, "total": len(items)}


@router.get("/{match_id}")
def get_settlement(match_id: str) -> dict:
    """Single match settlement. Returns 404 when no settlements exist."""
    _ensure_enabled()
    svc = _service()
    items = svc.get_settlement(match_id)
    if not items:
        raise HTTPException(
            status_code=404, detail="No settlements found for match."
        )
    return {"match_id": match_id, "items": items, "total": len(items)}


@router.post("/process/{match_id}")
def process_settlement(
    match_id: str, _auth: None = Depends(require_write_key)
) -> dict:
    """Manually trigger settlement processing for a match."""
    _ensure_enabled()
    svc = _service()
    result = svc.process_settlement(match_id)
    return {
        "match_id": match_id,
        "status": result.status,
        "settlements_count": result.settlements_count,
    }
