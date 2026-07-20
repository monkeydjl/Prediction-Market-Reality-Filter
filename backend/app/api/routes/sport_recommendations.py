"""Sport recommendation API routes.

When PHASE7_SPORT_RECOMMENDATION_ENABLED is false, all routes return 503.
All endpoints are GET (read-only) — no require_write_key auth (consistent
with Subproject B's GET endpoints).

Stateless: each request computes the recommendation on-demand from B's
persisted edges. No caching, no scheduler.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core import config

router = APIRouter(prefix="/sport-recommendations", tags=["Sport Recommendations"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_SPORT_RECOMMENDATION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Sport recommendations are disabled. Set PHASE7_SPORT_RECOMMENDATION_ENABLED=true to enable.",
        )


def _service():
    from app.kernel.sport_recommendation_service import SportRecommendationService
    return SportRecommendationService()


def _rec_to_dict(rec) -> dict[str, Any]:
    """Serialize a SportActionableRecommendation to a JSON-friendly dict."""
    return {
        "match_id": rec.match_id,
        "mapped_outcome": rec.mapped_outcome,
        "direction": rec.direction,
        "decision": rec.decision,
        "confidence": rec.confidence,
        "risk_level": rec.risk_level,
        "edge_pct": rec.edge_pct,
        "raw_edge_pct": rec.raw_edge_pct,
        "trust": rec.trust,
        "liquidity_factor": rec.liquidity_factor,
        "stale": rec.stale,
        "suggested_allocation_pct": rec.suggested_allocation_pct,
        "calibration_status": rec.calibration_status,
        "rationale": rec.rationale,
        "engine_name": rec.engine_name,
        "competition": rec.competition,
        "prediction_timestamp": rec.prediction_timestamp.isoformat() if rec.prediction_timestamp else None,
        "model_prob": rec.model_prob,
        "market_prob": rec.market_prob,
        "sources_count": rec.sources_count,
        "captured_at": rec.captured_at.isoformat() if rec.captured_at else None,
        "review_priority": getattr(rec, "review_priority", "normal"),
        "guardrail_flags": getattr(rec, "guardrail_flags", None),
        "policy_notes": getattr(rec, "policy_notes", None),
    }


@router.get("/open")
def get_open_decisions(
    limit: int = Query(20, ge=1, le=100),
    decision: str | None = Query(None, pattern="^(act|provisional_act|watch)$"),
) -> dict[str, Any]:
    """Open decisions list (excludes skip). Filterable by decision type."""
    _ensure_enabled()
    svc = _service()
    recs = svc.get_open_decisions(limit=limit, decision=decision)
    return {"items": [_rec_to_dict(r) for r in recs], "total": len(recs)}


@router.get("/discrepancies")
def get_top_picks(
    limit: int = Query(20, ge=1, le=100),
    min_abs_edge: float = Query(0.0, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Top edge picks (all decisions). min_abs_edge is on 0-1 scale (B's convention)."""
    _ensure_enabled()
    svc = _service()
    recs = svc.get_top_picks(limit=limit, min_abs_edge_pct=min_abs_edge * 100)
    return {"items": [_rec_to_dict(r) for r in recs], "total": len(recs)}


@router.get("/{match_id}")
def get_recommendation(match_id: str) -> dict[str, Any]:
    """Single match recommendation. Returns 404 when no edges exist."""
    _ensure_enabled()
    svc = _service()
    rec = svc.get_recommendation(match_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="No edges found for match.")
    return _rec_to_dict(rec)
