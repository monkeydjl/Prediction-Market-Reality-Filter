"""GBM football engine adapter for the Prediction Kernel.

Wraps the legacy world_cup_gbm_engine.predict_match_gbm path so Kernel
callers get a Protocol-compatible PredictionResult. When LightGBM models
are missing, legacy falls back to Elo→xG baseline (still valid 3-way).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.kernel.domain import (
    ContributionItem,
    FeatureSet,
    MatchIdentity,
    PredictionResult,
)
from app.kernel.engines.elo_odds_engine import _probabilities_to_scores

_NEUTRAL = {"home_win": 0.40, "draw": 0.30, "away_win": 0.30}


class GbmEngine:
    """Kernel PredictionEngine backed by LightGBM xG models."""

    def name(self) -> str:
        return "gbm"

    def supported_sports(self) -> list[str]:
        return ["football"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        elo_home = features.team.elo_rating_home
        elo_away = features.team.elo_rating_away
        if elo_home is None or elo_away is None:
            return self._neutral(features)

        home_name = match.home.name or match.home.code
        away_name = match.away.name or match.away.code
        is_knockout = (match.stage or "").lower() in {
            "round_of_16", "quarter_final", "semi_final", "final",
            "knockout", "playoff",
        }
        competition = (match.season.competition.code or "").lower()
        is_world_cup = competition in {"wc", "world_cup"}
        is_neutral = is_world_cup or not features.environment.is_home_advantage

        try:
            from app.services.world_cup_engines.world_cup_gbm_engine import (
                predict_match_gbm,
            )
            raw = predict_match_gbm(
                home_name,
                away_name,
                float(elo_home),
                float(elo_away),
                is_knockout=is_knockout,
                is_world_cup=is_world_cup,
                is_neutral=is_neutral,
            )
        except Exception:
            return self._neutral(features)

        probs_raw = raw.get("outcome_probabilities") or {}
        probs = {
            "home_win": float(probs_raw.get("home_win", _NEUTRAL["home_win"])),
            "draw": float(probs_raw.get("draw", _NEUTRAL["draw"])),
            "away_win": float(probs_raw.get("away_win", _NEUTRAL["away_win"])),
        }
        total = sum(probs.values())
        if total > 0:
            probs = {k: round(v / total, 4) for k, v in probs.items()}

        scores = raw.get("predicted_score") or _probabilities_to_scores(probs)
        if "home" not in scores:
            scores = _probabilities_to_scores(probs)

        confidence = float(raw.get("confidence") or max(probs.values()) * 0.9)
        confidence = round(min(0.95, max(0.3, confidence)), 4)
        model_loaded = bool(raw.get("model_loaded"))
        method = str(raw.get("prediction_method") or "gbm")
        pred = max(probs, key=probs.get)  # type: ignore[arg-type]

        explanation = [
            ContributionItem(
                factor="gbm",
                direction="support" if model_loaded else "neutral",
                weight=0.80,
                available=True,
                detail=method,
                predicted_outcome=pred,
            ),
            ContributionItem(
                factor="elo",
                direction="support",
                weight=0.20,
                available=True,
                detail=f"Elo {elo_home} vs {elo_away}",
                predicted_outcome=None,
            ),
        ]

        return PredictionResult(
            predicted_scores={
                "home": float(scores.get("home", 1.2)),
                "away": float(scores.get("away", 1.0)),
            },
            outcome_probabilities=probs,
            confidence=confidence,
            engine_name="gbm",
            explanation=explanation,
            betting_analysis={
                "model_loaded": model_loaded,
                "method": method,
                "expected_goals": raw.get("expected_goals"),
            },
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )

    def _neutral(self, features: FeatureSet) -> PredictionResult:
        scores = _probabilities_to_scores(_NEUTRAL)
        return PredictionResult(
            predicted_scores=scores,
            outcome_probabilities=dict(_NEUTRAL),
            confidence=0.35,
            engine_name="gbm",
            explanation=[
                ContributionItem(
                    factor="gbm",
                    direction="neutral",
                    weight=1.0,
                    available=False,
                    detail="Elo unavailable",
                    predicted_outcome=None,
                ),
            ],
            betting_analysis={"model_loaded": False},
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
