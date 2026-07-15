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

COMPETITION_SPORT = {
    "wc": "football", "ucl": "football", "epl": "football",
    "laliga": "football", "bundesliga": "football",
    "seriea": "football", "ligue1": "football",
    "nba": "basketball", "mlb": "baseball", "nhl": "hockey",
}


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
        factor_registry = FactorRegistry()
        engine = EloOddsEngine(factor_registry=factor_registry)
        learning = KernelLearningService(factor_registry=factor_registry)
        reg = EngineRegistry(learning_service=learning)
        reg.register(engine)

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

        # Phase 4: register NBA adapter + engine when enabled
        fb = FootballFeatureBuilder()

        # Start with football-only builders dict; sport flags below extend it
        builders: dict[str, object] = {
            "wc-": fb, "ucl-": fb, "epl-": fb,
            "laliga-": fb, "bundesliga-": fb, "seriea-": fb, "ligue1-": fb,
        }
        feature_builder: object = fb  # default — replaced by MultiFeatureBuilder if any sport enabled

        if config.settings.PHASE4_NBA_ENABLED:
            from app.sports.basketball.nba_adapter import NBAAdapter
            from app.sports.basketball.feature_builder import BasketballFeatureBuilder
            from app.sports.basketball.engines.basketball_engine import BasketballEngine

            adapters["nba-"] = NBAAdapter()
            nba_engine = BasketballEngine(factor_registry=factor_registry)
            reg.register(nba_engine)

            factor_registry.ensure_competition_factors("nba")
            builders["nba-"] = BasketballFeatureBuilder()

        if config.settings.PHASE5_MLB_ENABLED:
            from app.sports.baseball.mlb_adapter import MLBAdapter
            from app.sports.baseball.feature_builder import BaseballFeatureBuilder
            from app.sports.baseball.engines.baseball_engine import BaseballEngine

            adapters["mlb-"] = MLBAdapter()
            mlb_engine = BaseballEngine(factor_registry=factor_registry)
            reg.register(mlb_engine)

            factor_registry.ensure_competition_factors("mlb")
            builders["mlb-"] = BaseballFeatureBuilder()

        if config.settings.PHASE5_NHL_ENABLED:
            from app.sports.hockey.nhl_adapter import NHLAdapter
            from app.sports.hockey.feature_builder import HockeyFeatureBuilder
            from app.sports.hockey.engines.hockey_engine import HockeyEngine

            adapters["nhl-"] = NHLAdapter()
            nhl_engine = HockeyEngine(factor_registry=factor_registry)
            reg.register(nhl_engine)

            factor_registry.ensure_competition_factors("nhl")
            builders["nhl-"] = HockeyFeatureBuilder()

        # If any non-football sport is enabled, wrap builders in MultiFeatureBuilder
        if (config.settings.PHASE4_NBA_ENABLED
                or config.settings.PHASE5_MLB_ENABLED
                or config.settings.PHASE5_NHL_ENABLED):
            from app.kernel.multi_feature_builder import MultiFeatureBuilder
            feature_builder = MultiFeatureBuilder(builders)

        from app.sports.football.adapters.multi_adapter import MultiAdapter
        multi = MultiAdapter(adapters)

        _get_kernel._instance = PredictionKernel(
            adapter=multi,
            feature_builder=feature_builder,
            engine_registry=reg,
            factor_registry=factor_registry,
            feature_registry=FeatureRegistry(),
            learning=learning,
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
            "feature_version": result.feature_version,
            "prediction_timestamp": result.prediction_timestamp.isoformat(),
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


@router.get("/matches")
def list_matches(sport: str | None = None):
    """List today's matches across all sports."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    kernel = _get_kernel()
    from app.kernel.protocols import ScheduleFilter
    raw_matches = kernel._adapter.fetch_schedule(ScheduleFilter())

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    today_matches = []
    for m in raw_matches:
        kickoff = m.match.kickoff_utc
        if kickoff is not None and kickoff.date() == today:
            today_matches.append(m)

    if sport:
        today_matches = [m for m in today_matches
                         if m.match.season.competition.sport.code == sport]

    from app.kernel.kernel_db import get_match_ids_with_predictions
    predicted_ids = get_match_ids_with_predictions([m.match.match_id for m in today_matches])

    return [_match_summary(m, predicted_ids) for m in today_matches]


@router.get("/matches/{match_id}")
def get_match(match_id: str):
    """Get match detail and latest prediction (if any)."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    kernel = _get_kernel()
    match = kernel._adapter.get_match_identity(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    from app.kernel.kernel_db import get_latest_prediction
    latest = get_latest_prediction(match_id)

    return {
        "match": _match_detail(match),
        "prediction": _prediction_to_dict(latest) if latest else None,
    }


def _match_summary(raw, predicted_ids: set[str]) -> dict:
    """Compact match summary for list endpoint."""
    m = raw.match
    return {
        "match_id": m.match_id,
        "sport": m.season.competition.sport.code,
        "competition": m.season.competition.code,
        "home_team": m.home.name,
        "away_team": m.away.name,
        "home_code": m.home.code,
        "away_code": m.away.code,
        "kickoff_utc": m.kickoff_utc.isoformat() if m.kickoff_utc else None,
        "stage": m.stage,
        "has_prediction": m.match_id in predicted_ids,
    }


def _match_detail(match) -> dict:
    """Full match detail for detail endpoint."""
    return {
        "match_id": match.match_id,
        "sport": match.season.competition.sport.code,
        "competition": match.season.competition.code,
        "season_key": match.season.season_key,
        "home_team": match.home.name,
        "away_team": match.away.name,
        "home_code": match.home.code,
        "away_code": match.away.code,
        "kickoff_utc": match.kickoff_utc.isoformat() if match.kickoff_utc else None,
        "stage": match.stage,
        "round": match.round,
    }


def _prediction_to_dict(pred) -> dict:
    """Convert a KernelPrediction (ORM row) to a dict for the API response.

    Note: Only KernelPrediction (ORM) is supported. The PredictionResult
    dataclass uses ``engine_name`` (not ``engine``), so it cannot be passed
    here — callers must persist the prediction first and pass the ORM row.
    """
    import json
    from datetime import datetime

    explanation = pred.explanation
    if isinstance(explanation, str):
        explanation = json.loads(explanation)

    timestamp = pred.created_at
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()

    return {
        "engine": pred.engine,
        "predicted_scores": pred.predicted_scores,
        "outcome_probabilities": pred.outcome_probabilities,
        "confidence": pred.confidence,
        "explanation": explanation,
        "feature_version": pred.feature_version,
        "prediction_timestamp": timestamp,
    }


# --- Learning Dashboard endpoints ---


def _engine_score_to_dict(score) -> dict:
    """Serialize KernelEngineScore to dict."""
    return {
        "engine": score.engine,
        "competition": score.competition,
        "accuracy": score.accuracy,
        "avg_mae": score.avg_mae,
        "brier_score": score.brier_score,
        "sample_count": score.sample_count,
        "confidence_calibration": score.confidence_calibration,
        "last_updated": score.last_updated.isoformat() if score.last_updated else None,
    }


def _calibration_to_dict(cal) -> dict:
    """Serialize KernelCalibration to dict."""
    return {
        "engine": cal.engine,
        "competition": cal.competition,
        "slope": cal.slope,
        "intercept": cal.intercept,
        "sample_count": cal.sample_count,
        "avg_confidence": cal.avg_confidence,
        "avg_accuracy": cal.avg_accuracy,
        "last_updated": cal.last_updated.isoformat() if cal.last_updated else None,
    }


@router.get("/engines/scores")
def list_engine_scores(engine: str | None = None,
                       competition: str | None = None,
                       sport: str | None = None):
    """List engine performance scores with optional filters."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    from app.kernel.kernel_db import get_engine_scores
    scores = get_engine_scores(engine=engine, competition=competition, sport=sport)
    return [_engine_score_to_dict(s) for s in scores]


@router.get("/history")
def list_prediction_history(sport: str | None = None,
                            competition: str | None = None,
                            limit: int = 50,
                            offset: int = 0):
    """List prediction history, paginated."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be 1-200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")
    from app.kernel.kernel_db import get_prediction_history
    items, total = get_prediction_history(sport=sport, competition=competition,
                                           limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/history/{match_id}")
def get_match_history(match_id: str):
    """Get single-match prediction trajectory (all history records)."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    from app.kernel.kernel_db import get_prediction_history_by_match
    return get_prediction_history_by_match(match_id)


@router.get("/calibration")
def list_calibrations(engine: str | None = None,
                      competition: str | None = None):
    """List calibration parameters."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    from app.kernel.kernel_db import get_calibrations
    cals = get_calibrations(engine=engine, competition=competition)
    return [_calibration_to_dict(c) for c in cals]


@router.get("/calibration/reliability")
def get_reliability(engine: str | None = None,
                    competition: str | None = None,
                    bins: int = 10):
    """Get binned reliability data for calibration chart."""
    if not config.settings.KERNEL_PREDICTION_ENABLED:
        raise HTTPException(status_code=503, detail="Kernel prediction is disabled.")
    if bins < 5 or bins > 20:
        raise HTTPException(status_code=422, detail="bins must be 5-20")
    from app.kernel.kernel_db import compute_reliability_bins
    return compute_reliability_bins(engine=engine, competition=competition, bins=bins)
