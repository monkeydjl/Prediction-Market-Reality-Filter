# backend/app/api/routes/sport_optimization.py
"""Sport optimization API routes (Phase 9).

All endpoints gated by PHASE9_ACCURACY_SPRINT_ENABLED (503 when false).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.security import require_write_key
from app.core.config import settings
from app.services.historical_data_ingestor import HistoricalDataIngestor

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sport-optimization", tags=["Sport Optimization"])


class IngestRequest(BaseModel):
    sport: str  # "nba" / "mlb" / "nhl" / "all"
    seasons: list[str]  # e.g., ["2023-24", "2024-25"]


class OptimizationRequest(BaseModel):
    sport: str  # "nba" / "mlb" / "nhl" / "all"
    n_trials: int = 150


def _check_enabled() -> None:
    if not settings.PHASE9_ACCURACY_SPRINT_ENABLED:
        raise HTTPException(status_code=503, detail="Phase 9 accuracy sprint disabled")


@router.post("/ingest")
async def ingest_historical_data(request: IngestRequest):
    """Trigger historical data ingestion."""
    _check_enabled()
    ingestor = HistoricalDataIngestor()
    sports = ["nba", "mlb", "nhl"] if request.sport == "all" else [request.sport]
    results = {}
    for sport in sports:
        for season in request.seasons:
            result = await ingestor.ingest_season(sport, season)
            results[f"{sport}-{season}"] = result
    return results


@router.post("/run")
async def run_optimization(request: OptimizationRequest):
    """Trigger parameter optimization (async)."""
    _check_enabled()
    from app.services.optimization_task_manager import get_task_manager

    task_manager = get_task_manager()
    task = await task_manager.create_task(engine_name=request.sport)
    return {"task_id": task.task_id, "status": "pending"}


@router.get("/status/{task_id}")
async def get_optimization_status(task_id: str):
    """Query optimization task status."""
    _check_enabled()
    from app.services.optimization_task_manager import get_task_manager

    task_manager = get_task_manager()
    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@router.get("/params/{sport}")
async def get_params(sport: str):
    """Get current optimized params for a sport."""
    _check_enabled()
    from app.kernel.optimized_params_store import OptimizedParamsStore

    store = OptimizedParamsStore()
    params = store.get_applied(sport, sport)
    if params is None:
        raise HTTPException(status_code=404, detail=f"No applied params for {sport}")
    return params


@router.get("/params")
async def list_params():
    """List all sports' optimized params."""
    _check_enabled()
    from app.kernel.optimized_params_store import OptimizedParamsStore

    store = OptimizedParamsStore()
    return store.get_candidates()


@router.post("/apply/{params_id}")
async def apply_params(params_id: int, _auth: None = Depends(require_write_key)):
    """Apply optimized params to FactorRegistry."""
    _check_enabled()
    from app.kernel.optimized_params_store import OptimizedParamsStore

    store = OptimizedParamsStore()
    try:
        result = store.apply(params_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
