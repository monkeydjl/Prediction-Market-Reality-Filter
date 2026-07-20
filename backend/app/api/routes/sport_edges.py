"""Sport edge detector API routes.

When PHASE7_EDGE_DETECTOR_ENABLED is false, all routes return 503.
GET endpoints are read-only. POST /{match_id}/detect requires write key.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.security import require_write_key
from app.core import config

router = APIRouter(prefix="/sport-edges", tags=["Sport Edges"])


def _ensure_enabled() -> None:
    if not config.settings.PHASE7_EDGE_DETECTOR_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Edge detector is disabled. Set PHASE7_EDGE_DETECTOR_ENABLED=true to enable.",
        )


def _service():
    from app.kernel.edge_detector_service import EdgeDetectorService
    return EdgeDetectorService()


def _edge_to_dict(edge) -> dict[str, Any]:
    """Serialize an EdgeResult to a JSON-friendly dict."""
    return {
        "mapped_outcome": edge.mapped_outcome,
        "model_prob": edge.model_prob,
        "market_prob": edge.market_prob,
        "raw_edge": edge.raw_edge,
        "trust": edge.trust,
        "liquidity_factor": edge.liquidity_factor,
        "adjusted_edge": edge.adjusted_edge,
        "spread": edge.spread,
        "sources_count": edge.sources_count,
        "stale": edge.stale,
        "review_priority": getattr(edge, "review_priority", "normal"),
        "factor_drivers": getattr(edge, "factor_drivers", None),
        "factor_attribution": getattr(edge, "factor_attribution", None),
        "captured_at": edge.captured_at.isoformat() if edge.captured_at else None,
        "sources": [
            {
                "link_id": s.link_id,
                "source": s.source,
                "contract_id": s.contract_id,
                "implied_prob": s.implied_prob,
                "liquidity": s.liquidity,
                "volume": s.volume,
                "weight": s.weight,
                "link_confidence": s.link_confidence,
            }
            for s in edge.sources
        ],
    }


@router.post("/{match_id}/detect")
def detect_edges_now(
    match_id: str,
    _auth: None = Depends(require_write_key),
) -> dict[str, Any]:
    """Compute and persist edges for a match on demand.

    Requires write key. Returns the same shape as GET .../latest after
    running EdgeDetectorService.detect_edges.
    """
    _ensure_enabled()
    svc = _service()
    summary = svc.detect_edges(match_id)
    return {
        "match_id": summary.match_id,
        "outcomes": [_edge_to_dict(e) for e in summary.outcomes],
        "engine_name": summary.engine_name,
        "competition": summary.competition,
        "prediction_timestamp": (
            summary.prediction_timestamp.isoformat()
            if summary.prediction_timestamp
            else None
        ),
        "skipped": summary.skipped,
        "skip_reason": summary.skip_reason,
    }


@router.get("/{match_id}/latest")
def get_latest_edges(match_id: str) -> dict[str, Any]:
    """Latest edge snapshot per outcome for a match.

    If the match has no prediction or no verified links, returns skipped=true.
    Use POST /{match_id}/detect to compute edges on demand.
    """
    _ensure_enabled()
    svc = _service()
    edges = svc.get_latest_edges(match_id)
    if not edges:
        from app.kernel.kernel_db import get_latest_prediction
        pred = get_latest_prediction(match_id)
        if pred is None:
            return {
                "match_id": match_id, "outcomes": [],
                "engine_name": None, "competition": None,
                "prediction_timestamp": None,
                "skipped": True, "skip_reason": "no_prediction",
            }
        return {
            "match_id": match_id, "outcomes": [],
            "engine_name": pred.engine, "competition": pred.competition,
            "prediction_timestamp": pred.created_at.isoformat() if pred.created_at else None,
            "skipped": True, "skip_reason": "no_verified_links",
        }
    from app.kernel.kernel_db import get_latest_prediction
    pred = get_latest_prediction(match_id)
    return {
        "match_id": match_id,
        "outcomes": [_edge_to_dict(e) for e in edges],
        "engine_name": pred.engine if pred else None,
        "competition": pred.competition if pred else None,
        "prediction_timestamp": pred.created_at.isoformat() if pred and pred.created_at else None,
        "skipped": False,
        "skip_reason": None,
    }


@router.get("/{match_id}/history")
def get_edge_history(
    match_id: str,
    mapped_outcome: str | None = Query(None),
) -> dict[str, Any]:
    """Full edge time-series for a match, optionally filtered by outcome."""
    _ensure_enabled()
    svc = _service()
    edges = svc.get_edge_history(match_id, mapped_outcome=mapped_outcome)
    by_outcome: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        by_outcome.setdefault(edge.mapped_outcome, []).append({
            "captured_at": edge.captured_at.isoformat() if edge.captured_at else None,
            "model_prob": edge.model_prob,
            "market_prob": edge.market_prob,
            "raw_edge": edge.raw_edge,
            "adjusted_edge": edge.adjusted_edge,
            "stale": edge.stale,
            "review_priority": getattr(edge, "review_priority", "normal"),
            "trust": getattr(edge, "trust", None),
            "liquidity_factor": getattr(edge, "liquidity_factor", None),
        })
    series = [
        {"mapped_outcome": outcome, "snapshots": snaps}
        for outcome, snaps in by_outcome.items()
    ]
    return {"match_id": match_id, "series": series}


@router.get("/discrepancies")
def get_discrepancies(
    limit: int = Query(20, ge=1, le=100),
    min_abs_edge: float = Query(0.0, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Top matches by |adjusted_edge| across all matches with edge data."""
    _ensure_enabled()
    svc = _service()
    edges = svc.get_top_discrepancies(limit=limit, min_abs_edge=min_abs_edge)
    _PRIORITY_RANK = {"critical": 0, "high": 1, "normal": 2, "low": 3}

    items = [
        {
            "match_id": e.match_id,
            "mapped_outcome": e.mapped_outcome,
            "model_prob": e.model_prob,
            "market_prob": e.market_prob,
            "raw_edge": e.raw_edge,
            "adjusted_edge": e.adjusted_edge,
            "stale": e.stale,
            "trust": getattr(e, "trust", None),
            "liquidity_factor": getattr(e, "liquidity_factor", None),
            "sources_count": getattr(e, "sources_count", None),
            "review_priority": getattr(e, "review_priority", None) or "normal",
            "captured_at": e.captured_at.isoformat() if e.captured_at else None,
        }
        for e in edges
    ]
    items.sort(
        key=lambda row: (
            _PRIORITY_RANK.get(str(row.get("review_priority") or "normal"), 2),
            -abs(float(row.get("adjusted_edge") or 0.0)),
        )
    )
    return {"items": items, "total": len(items)}
