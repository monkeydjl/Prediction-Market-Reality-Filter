"""GBM (LightGBM) prediction engine for World Cup matches.

Loads two LightGBM regressors (home_xg, away_xg) trained by
scripts/train_gbm_model.py, derives features via the shared
world_cup_gbm_features module, and produces a complete prediction dict
compatible with the pipeline's expected output schema.

The predicted xG values are fed into rule_engine.calculate_outcome_probabilities
(with Dixon-Coles rho correction) to produce win/draw/loss probabilities.

If the model files are missing, falls back to a pure-Poisson baseline using
Elo-derived expected goals (no GBM correction).
"""

from __future__ import annotations

import json
import logging
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.world_cup_engines.world_cup_rule_engine import (
    calculate_outcome_probabilities,
)
from app.services.world_cup_engines.world_cup_gbm_features import (
    derive_gbm_features,
)
from app.services.world_cup_historical_results import (
    get_historical_team_stats,
    get_historical_h2h,
)


logger = logging.getLogger(__name__)


_DATA_DIR = Path(os.getenv(
    "GBM_DATA_DIR",
    str(Path(__file__).resolve().parents[3] / "data"),
))
_HOME_MODEL_PATH = _DATA_DIR / "gbm_home_model.txt"
_AWAY_MODEL_PATH = _DATA_DIR / "gbm_away_model.txt"
_META_PATH = _DATA_DIR / "gbm_features.json"

# Fallback expected goals when GBM models are unavailable (pure Elo-based).
_FALLBACK_HOME_XG = 1.4
_FALLBACK_AWAY_XG = 1.1


@lru_cache(maxsize=1)
def _load_models() -> tuple[Any, Any, dict]:
    """Load LightGBM models and metadata.

    Returns:
        (home_model, away_model, meta_dict). Falls back to (None, None, {})
        if model files are missing, logging a warning.
    """
    try:
        if not _HOME_MODEL_PATH.exists() or not _AWAY_MODEL_PATH.exists():
            logger.warning(
                "GBM model files not found at %s / %s; falling back to Elo baseline. "
                "Run `python scripts/train_gbm_model.py` to train models.",
                _HOME_MODEL_PATH,
                _AWAY_MODEL_PATH,
            )
            return None, None, {}

        import lightgbm as lgb
        home_model = lgb.Booster(model_file=str(_HOME_MODEL_PATH))
        away_model = lgb.Booster(model_file=str(_AWAY_MODEL_PATH))

        meta = {}
        if _META_PATH.exists():
            meta = json.loads(_META_PATH.read_text(encoding="utf-8"))

        logger.info("Loaded GBM models (home: %s, away: %s)",
                    _HOME_MODEL_PATH.name, _AWAY_MODEL_PATH.name)
        return home_model, away_model, meta
    except Exception:
        logger.exception(
            "Failed to load GBM models from %s; falling back to Elo baseline",
            _DATA_DIR,
        )
        return None, None, {}


def _elo_to_xg_baseline(elo_home: float, elo_away: float) -> tuple[float, float]:
    """Convert Elo ratings to baseline expected goals (no GBM).

    Uses the same formula as elo_odds_engine.probabilities_to_expected_scores
    but without odds dependency.
    """
    elo_diff = elo_home - elo_away
    # Home win probability (BT-style)
    p_home = 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))
    # Translate to xG: 1.4 baseline ± adjustment from elo gap
    home_xg = 1.4 + 0.4 * (p_home - 0.5)
    away_xg = 1.1 - 0.4 * (p_home - 0.5)
    # Clamp
    home_xg = max(0.3, min(4.0, home_xg))
    away_xg = max(0.3, min(4.0, away_xg))
    return home_xg, away_xg


def predict_match_gbm(
    home_team: str,
    away_team: str,
    elo_home: float,
    elo_away: float,
    *,
    is_knockout: bool = False,
    is_world_cup: bool = True,
    is_neutral: bool = True,
) -> dict[str, Any]:
    """Predict a match using the GBM engine.

    Args:
        home_team: Home team name (used to look up historical stats)
        away_team: Away team name
        elo_home: Home team Elo rating
        elo_away: Away team Elo rating
        is_knockout: If True, apply knockout-stage draw reduction (passed to
                     rule_engine.calculate_outcome_probabilities via the
                     Dixon-Coles rho knockout factor)
        is_world_cup: If True, set is_world_cup feature to 1.0
        is_neutral: If True, set is_neutral feature to 1.0 (World Cup default)

    Returns:
        Prediction dict with the standard schema:
        - predicted_score: {home: float, away: float}
        - outcome_probabilities: {home_win, draw, away_win}
        - confidence: float
        - prediction_method: str (starts with "gbm")
        - expected_goals: {home, away}
        - model_loaded: bool
        - elo_ratings: {home, away, difference}
    """
    home_model, away_model, meta = _load_models()

    # Derive features (uses historical stats from CSV)
    home_stats = get_historical_team_stats(home_team)
    away_stats = get_historical_team_stats(away_team)
    h2h = get_historical_h2h(home_team, away_team)

    if home_model is not None and away_model is not None:
        features = derive_gbm_features(
            elo_home=elo_home,
            elo_away=elo_away,
            home_stats=home_stats,
            away_stats=away_stats,
            h2h=h2h,
            is_neutral=is_neutral,
            is_world_cup=is_world_cup,
        )
        # LightGBM predict expects 2D array
        home_xg = float(home_model.predict([features])[0])
        away_xg = float(away_model.predict([features])[0])
        # Clamp to reasonable xG range
        home_xg = max(0.1, min(5.0, home_xg))
        away_xg = max(0.1, min(5.0, away_xg))
        method = "gbm_lightgbm"
        model_loaded = True
    else:
        # Fallback: pure Elo baseline
        home_xg, away_xg = _elo_to_xg_baseline(elo_home, elo_away)
        method = "gbm_fallback_elo"
        model_loaded = False

    # Convert xG to outcome probabilities via rule_engine (with DC rho)
    outcome_probs = calculate_outcome_probabilities(home_xg, away_xg)

    # Confidence: based on Elo gap (larger gap -> higher confidence)
    elo_diff = abs(elo_home - elo_away)
    confidence = max(0.40, min(0.90, 0.50 + elo_diff / 800.0))

    return {
        "home_team": home_team,
        "away_team": away_team,
        "predicted_score": {
            "home": round(home_xg, 2),
            "away": round(away_xg, 2),
        },
        "outcome_probabilities": outcome_probs,
        "confidence": round(confidence, 3),
        "prediction_method": method,
        "expected_goals": {
            "home": round(home_xg, 2),
            "away": round(away_xg, 2),
        },
        "elo_ratings": {
            "home": elo_home,
            "away": elo_away,
            "difference": round(elo_home - elo_away, 1),
        },
        "has_betting_odds": False,
        "market_probabilities": None,
        "market_favorite": None,
        "rule_score": None,
        "ai_score": None,
        "ai_reasoning": None,
        "key_factors": [],
        "model_loaded": model_loaded,
        "score_probability_matrix": {},  # Populated below if needed
        "top_5_scores": [],
        "prediction_interval": {
            "p10_total_goals": 1,
            "p90_total_goals": 4,
            "total_goals_distribution": {},
        },
    }


def _build_score_matrix(home_xg: float, away_xg: float, max_goals: int = 8) -> dict[str, float]:
    """Build a Poisson score probability matrix (used for diagnostics)."""
    matrix: dict[str, float] = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p_h = math.exp(-home_xg) * (home_xg ** h) / math.factorial(h)
            p_a = math.exp(-away_xg) * (away_xg ** a) / math.factorial(a)
            matrix[f"{h}-{a}"] = round(p_h * p_a, 6)
    return matrix
