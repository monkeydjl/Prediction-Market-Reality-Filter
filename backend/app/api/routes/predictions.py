# backend/app/api/routes/predictions.py
"""Generic prediction API routes.

These routes provide sport-agnostic prediction access through the
Prediction Kernel. When KERNEL_PREDICTION_ENABLED is false, the
routes return 503 Service Unavailable.

Note: settings is accessed via ``config.settings`` (not ``from ... import
settings``) so that the route always sees the current Settings instance
even if ``test_main_frontend_mount`` reloads the config module at test
time.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.security import require_write_key
from app.core import config

router = APIRouter(prefix="/predictions", tags=["Predictions"])
logger = logging.getLogger(__name__)


def _get_kernel():
    """Lazy-initialize the PredictionKernel singleton."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Kernel prediction is disabled. Set KERNEL_PREDICTION_ENABLED=true to enable.",
        )
    from app.kernel.prediction_kernel import PredictionKernel
    from app.kernel.engine_registry import EngineRegistry
    from app.kernel.feature_registry import FeatureRegistry
    from app.kernel.factor_registry import FactorRegistry
    from app.kernel.engines.elo_odds_engine import EloOddsEngine
    from app.kernel.kernel_db import init_kernel_db
    from app.kernel.learning_service import KernelLearningService
    from app.sports.football.adapters.world_cup_adapter import WorldCupAdapter
    from app.sports.football.feature_builder import FootballFeatureBuilder

    if not hasattr(_get_kernel, "_instance"):
        init_kernel_db()
        reg = EngineRegistry()
        reg.register(EloOddsEngine())

        # Build adapter registry — always includes WorldCupAdapter
        adapters: dict[str, object] = {
            "wc-": WorldCupAdapter(),
        }

        # Phase 2: register UCL and EPL adapters when enabled
        if config.settings.PHASE2_LEAGUES_ENABLED:
            from app.sports.football.adapters.ucl_adapter import UCLAdapter
            from app.sports.football.adapters.epl_adapter import EPLAdapter
            adapters["ucl-"] = UCLAdapter()
            adapters["epl-"] = EPLAdapter()

            # Phase 2b: register league-format adapters from LEAGUE_REGISTRY
            from app.sports.football.adapters.league_adapter import LEAGUE_REGISTRY, LeagueAdapter
            for prefix, cfg in LEAGUE_REGISTRY.items():
                adapters[prefix] = LeagueAdapter(cfg)

        from app.sports.football.adapters.multi_adapter import MultiAdapter
        multi = MultiAdapter(adapters)

        _get_kernel._instance = PredictionKernel(
            adapter=multi,
            feature_builder=FootballFeatureBuilder(),
            engine_registry=reg,
            factor_registry=FactorRegistry(),
            feature_registry=FeatureRegistry(),
            learning=KernelLearningService(),
        )
    return _get_kernel._instance


@router.get("/engines")
def list_engines():
    """List available prediction engines."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        return ["elo_odds"]  # static list when disabled
    kernel = _get_kernel()
    return kernel._engine_registry.list_engines()


@router.post("/matches/{match_id}/predict")
def predict_match(match_id: str, engine: str = "auto", _auth: None = Depends(require_write_key)):
    """Run a prediction for a single match."""
    kernel = _get_kernel()
    try:
        result = kernel.predict(match_id, engine=engine)
        return {
            "match_id": match_id,
            "engine": result.engine_name,
            "predicted_scores": result.predicted_scores,
            "outcome_probabilities": result.outcome_probabilities,
            "confidence": result.confidence,
            "explanation": [c.__dict__ for c in result.explanation],
        }
    except Exception as e:
        logger.error("Prediction failed for %s: %s", match_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/outcomes/{match_id}/process")
def process_outcome(match_id: str, _auth: None = Depends(require_write_key)):
    """Process a match outcome — triggers the learning loop."""
    kernel = _get_kernel()
    try:
        kernel.process_outcome(match_id)
        return {"match_id": match_id, "status": "processed"}
    except Exception as e:
        logger.error("Outcome processing failed for %s: %s", match_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/engines/{name}/score")
def engine_score(name: str, competition: str | None = None):
    """Get the performance score for an engine."""
    kernel = _get_kernel()
    score = kernel._learning.engine_score(name, competition)
    if score is None:
        raise HTTPException(status_code=404, detail="No score data for this engine")
    return {
        "engine": score.engine,
        "competition": score.competition,
        "accuracy": score.accuracy,
        "avg_mae": score.avg_mae,
        "brier_score": score.brier_score,
        "sample_count": score.sample_count,
    }
