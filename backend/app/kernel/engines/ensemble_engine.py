"""Inverse-Brier ensemble over registered football engines.

Fuses outcome probabilities from child engines. When LearningService scores
are available (sample_count >= min_samples), weights ∝ 1 / max(brier, ε);
otherwise equal weights among successful children.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.kernel.domain import (
    ContributionItem,
    FeatureSet,
    MatchIdentity,
    PredictionResult,
)
from app.kernel.engines.confidence import factor_vote
from app.kernel.engines.elo_odds_engine import _probabilities_to_scores
from app.kernel.protocols import PredictionEngine

if TYPE_CHECKING:
    from app.kernel.protocols import LearningService

_EPS = 1e-3
_NEUTRAL = {"home_win": 0.40, "draw": 0.30, "away_win": 0.30}


class EnsembleEngine:
    """Weighted average ensemble implementing PredictionEngine Protocol."""

    def __init__(
        self,
        engines: list[PredictionEngine],
        *,
        learning_service: LearningService | None = None,
        min_samples: int = 5,
        name: str = "ensemble",
    ) -> None:
        if not engines:
            raise ValueError("EnsembleEngine requires at least one child engine")
        self._engines = list(engines)
        self._learning = learning_service
        self._min_samples = min_samples
        self._name = name

    def name(self) -> str:
        return self._name

    def supported_sports(self) -> list[str]:
        sports: set[str] = set()
        for eng in self._engines:
            for s in eng.supported_sports():
                sports.add(s)
        return sorted(sports) if sports else ["football"]

    def _weight_for(self, engine_name: str, competition: str) -> float:
        if self._learning is None:
            return 1.0
        score = self._learning.engine_score(engine_name, competition)
        if score is None or score.sample_count < self._min_samples:
            return 1.0
        brier = max(float(score.brier_score), _EPS)
        return 1.0 / brier

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        competition = match.season.competition.code
        child_results: list[tuple[str, PredictionResult, float]] = []

        for eng in self._engines:
            try:
                result = eng.predict(features, match)
            except Exception:
                continue
            w = self._weight_for(eng.name(), competition)
            child_results.append((eng.name(), result, w))

        if not child_results:
            return PredictionResult(
                predicted_scores=_probabilities_to_scores(_NEUTRAL),
                outcome_probabilities=dict(_NEUTRAL),
                confidence=0.3,
                engine_name=self._name,
                explanation=[],
                betting_analysis=None,
                feature_version=features.feature_version,
                prediction_timestamp=datetime.now(timezone.utc),
            )

        total_w = sum(w for _, _, w in child_results)
        fused = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
        score_h = score_a = 0.0
        conf = 0.0

        for _, result, w in child_results:
            nw = w / total_w
            probs = result.outcome_probabilities
            for key in fused:
                fused[key] += float(probs.get(key, 0.0)) * nw
            score_h += float(result.predicted_scores.get("home", 1.2)) * nw
            score_a += float(result.predicted_scores.get("away", 1.0)) * nw
            conf += float(result.confidence) * nw

        total_p = sum(fused.values())
        if total_p > 0:
            fused = {k: round(v / total_p, 4) for k, v in fused.items()}
        else:
            fused = dict(_NEUTRAL)

        explanation = [
            ContributionItem(
                factor=name,
                direction="support",
                weight=round(w / total_w, 4),
                available=True,
                detail=(
                    f"H={result.outcome_probabilities.get('home_win', 0):.3f} "
                    f"D={result.outcome_probabilities.get('draw', 0):.3f} "
                    f"A={result.outcome_probabilities.get('away_win', 0):.3f}"
                ),
                predicted_outcome=factor_vote(result.outcome_probabilities),
            )
            for name, result, w in child_results
        ]

        return PredictionResult(
            predicted_scores={
                "home": round(score_h, 3),
                "away": round(score_a, 3),
            },
            outcome_probabilities=fused,
            confidence=round(min(0.95, max(0.3, conf)), 4),
            engine_name=self._name,
            explanation=explanation,
            betting_analysis={
                "members": [n for n, _, _ in child_results],
                "weights": {
                    n: round(w / total_w, 4) for n, _, w in child_results
                },
            },
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
