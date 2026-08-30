"""Situational engine — base engine + soft tournament context (P1-E8).

Wraps another PredictionEngine (default EloOdds). When FeatureSet.custom
or match.stage carry must-win / knockout / group-status signals, applies
small renormalized probability adjustments and explains them as a
ContributionItem. Without context, output matches the base engine
(plus an extra neutral explanation row).
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
from app.kernel.engines.elo_odds_engine import (
    EloOddsEngine,
    _probabilities_to_scores,
)
from app.kernel.engines.situational_adjust import (
    apply_situational_adjustment,
    extract_situational_context,
    situational_summary,
)

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry
    from app.kernel.protocols import PredictionEngine


class SituationalEngine:
    """PredictionEngine: base probs + soft situational post-adjustment."""

    def __init__(
        self,
        base_engine: PredictionEngine | None = None,
        *,
        factor_registry: FactorRegistry | None = None,
        name: str = "situational",
    ) -> None:
        self._base = base_engine or EloOddsEngine(factor_registry=factor_registry)
        self._name = name

    def name(self) -> str:
        return self._name

    def supported_sports(self) -> list[str]:
        # Tournament context is football-first; still works if base is multi-sport
        base_sports = list(self._base.supported_sports())
        if "football" not in base_sports and "*" not in base_sports:
            return base_sports + ["football"]
        return base_sports

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        base_result = self._base.predict(features, match)
        custom = features.custom if isinstance(features.custom, dict) else {}
        ctx = extract_situational_context(match.stage, custom)
        adjusted, applied = apply_situational_adjustment(
            base_result.outcome_probabilities,
            ctx,
        )
        scores = (
            _probabilities_to_scores(adjusted)
            if applied
            else base_result.predicted_scores
        )
        confidence = base_result.confidence
        if applied:
            # Slight confidence trim when we moved mass (uncertainty of context)
            confidence = round(max(0.25, min(0.95, confidence * 0.98)), 4)

        explanation = list(base_result.explanation)
        pred = factor_vote(adjusted)
        explanation.append(
            ContributionItem(
                factor="situational",
                direction="support" if applied else "neutral",
                weight=0.08 if applied else 0.0,
                available=applied,
                detail=situational_summary(ctx, applied),
                predicted_outcome=pred if applied else None,
            )
        )

        betting = dict(base_result.betting_analysis or {})
        betting["base_engine"] = self._base.name()
        betting["situational_applied"] = applied
        if applied:
            betting["situational_notes"] = list(ctx.notes)
            betting["base_probs"] = dict(base_result.outcome_probabilities)
            betting["adjusted_probs"] = dict(adjusted)

        return PredictionResult(
            predicted_scores=scores,
            outcome_probabilities=adjusted if applied else dict(
                base_result.outcome_probabilities
            ),
            confidence=confidence,
            engine_name=self._name,
            explanation=explanation,
            betting_analysis=betting,
            feature_version=base_result.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
