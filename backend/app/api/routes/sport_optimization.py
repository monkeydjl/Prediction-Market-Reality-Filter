# backend/app/api/routes/sport_optimization.py
"""Sport optimization API routes (Phase 9).

All endpoints gated by PHASE9_ACCURACY_SPRINT_ENABLED (503 when false).
"""
from __future__ import annotations

import logging
from typing import Any

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
async def ingest_historical_data(
    request: IngestRequest, _auth: None = Depends(require_write_key)
) -> dict[str, Any]:
    """Trigger historical data ingestion."""
    _check_enabled()
    ingestor = HistoricalDataIngestor()
    sports = ["nba", "mlb", "nhl"] if request.sport == "all" else [request.sport]
    results: dict[str, Any] = {}
    for sport in sports:
        for season in request.seasons:
            result = await ingestor.ingest_season(sport, season)
            results[f"{sport}-{season}"] = result
    return results


class BackfillSeedRequest(BaseModel):
    sport: str = "all"  # "nba" / "mlb" / "nhl" / "all"
    backfill: bool = True
    seed_elo: bool = True


@router.post("/backfill-seed")
async def backfill_and_seed(
    request: BackfillSeedRequest,
    _auth: None = Depends(require_write_key),
) -> dict[str, Any]:
    """Backfill KernelMatchResult from fixtures and/or seed KernelEloRating.

    Use after schedule sync when fixtures have scores but results/Elo tables
    are empty. Idempotent.
    """
    _check_enabled()
    sport = None if request.sport == "all" else request.sport
    if sport is not None and sport not in {"nba", "mlb", "nhl"}:
        raise HTTPException(status_code=400, detail=f"Unsupported sport: {sport}")
    ingestor = HistoricalDataIngestor()
    out: dict = {}
    if request.backfill:
        out["backfill"] = ingestor.backfill_results_from_fixtures(sport=sport)
    if request.seed_elo:
        out["seed"] = ingestor.seed_elo_ratings(sport=sport)
    return out


@router.post("/run")
async def run_optimization(
    request: OptimizationRequest, _auth: None = Depends(require_write_key)
) -> dict[str, Any]:
    """Trigger parameter optimization (async).

    Creates a durable task row, then schedules ParameterOptimizer.optimize_sync
    in a worker thread so the HTTP response returns immediately with task_id.
    """
    _check_enabled()
    import asyncio

    from app.services.optimization_task_manager import get_task_manager
    from app.utils.background_tasks import spawn

    sports = ["nba", "mlb", "nhl"] if request.sport == "all" else [request.sport]
    for sport in sports:
        if sport not in {"nba", "mlb", "nhl"}:
            raise HTTPException(status_code=400, detail=f"Unsupported sport: {sport}")

    task_manager = get_task_manager()
    # One task for the whole request; engine_name records the primary sport key.
    task = await task_manager.create_task(engine_name=request.sport)
    n_trials = max(1, min(int(request.n_trials), 500))

    async def _run() -> None:
        await task_manager.mark_running(task.task_id)
        results: dict = {}
        try:
            from app.kernel.backtest.match_loader import (
                load_sport_matches_for_backtest,
                time_series_split,
            )
            from app.kernel.parameter_optimizer import ParameterOptimizer

            optimizer = ParameterOptimizer()
            total = len(sports)
            for idx, sport in enumerate(sports):
                await task_manager.update_progress(
                    task.task_id,
                    progress=idx,
                    total=total,
                    current_match=sport,
                    log_message=f"Loading matches for {sport}",
                )
                matches = await asyncio.to_thread(load_sport_matches_for_backtest, sport)
                if len(matches) < 5:
                    results[sport] = {
                        "error": f"Not enough matches ({len(matches)}); ingest history first",
                        "match_count": len(matches),
                    }
                    continue
                train, test = time_series_split(matches, test_ratio=0.2)
                await task_manager.update_progress(
                    task.task_id,
                    progress=idx,
                    total=total,
                    current_match=sport,
                    log_message=(
                        f"Optimizing {sport}: train={len(train)} test={len(test)} "
                        f"trials={n_trials}"
                    ),
                )
                opt_result = await asyncio.to_thread(
                    optimizer.optimize_sync,
                    sport,
                    n_trials=n_trials,
                    train_matches=train,
                    test_matches=test,
                )
                results[sport] = opt_result
            await task_manager.update_progress(
                task.task_id, progress=total, total=total, log_message="done",
            )
            await task_manager.mark_completed(task.task_id, {"sports": results})
        except Exception as exc:
            logger.exception("Optimization task %s failed", task.task_id)
            await task_manager.mark_failed(task.task_id, str(exc))

    spawn(_run(), name=f"sport_optimization:{task.task_id}")
    return {"task_id": task.task_id, "status": "pending", "sports": sports, "n_trials": n_trials}


@router.get("/status/{task_id}")
async def get_optimization_status(task_id: str) -> dict[str, Any]:
    """Query optimization task status."""
    _check_enabled()
    from app.services.optimization_task_manager import get_task_manager

    task_manager = get_task_manager()
    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@router.get("/params/{sport}")
async def get_params(sport: str) -> dict:
    """Get current optimized params for a sport."""
    _check_enabled()
    from app.kernel.optimized_params_store import OptimizedParamsStore

    store = OptimizedParamsStore()
    params = store.get_applied(sport, sport)
    if params is None:
        raise HTTPException(status_code=404, detail=f"No applied params for {sport}")
    return params


@router.get("/params")
async def list_params() -> list[dict]:
    """List all sports' optimized params."""
    _check_enabled()
    from app.kernel.optimized_params_store import OptimizedParamsStore

    store = OptimizedParamsStore()
    return store.get_candidates()


@router.post("/apply/{params_id}")
async def apply_params(params_id: int, _auth: None = Depends(require_write_key)) -> dict:
    """Apply optimized params to FactorRegistry."""
    _check_enabled()
    from app.kernel.optimized_params_store import OptimizedParamsStore

    store = OptimizedParamsStore()
    try:
        result = store.apply(params_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
