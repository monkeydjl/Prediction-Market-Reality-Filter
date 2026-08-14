# backend/app/sports/lol/engines/market_only_engine.py
"""LolMarketOnlyEngine — binary series winner from market probabilities.

v1 LoL engine uses only series moneyline market probs (mkt_home / mkt_away).
No draw; no invented map scores.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.kernel.domain import (
    ContributionItem,
    FeatureSet,
    MatchIdentity,
    PredictionResult,
)


def _as_prob(value: Any) -> float | None:
    if value is None:
        return None
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if p < 0.0 or p > 1.0:
        return None
    return p


class LolMarketOnlyEngine:
    """Market-only binary series winner. Implements PredictionEngine Protocol."""

    def name(self) -> str:
        return "lol_market_only"

    def supported_sports(self) -> list[str]:
        return ["lol"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        custom = features.custom if isinstance(features.custom, dict) else {}
        mkt_home = _as_prob(custom.get("mkt_home"))
        mkt_away = _as_prob(custom.get("mkt_away"))

        # Branch on the probabilities themselves: a bool flag assigned from
        # `is not None` reads like a guard but narrows neither Optional.
        if mkt_home is not None and mkt_away is not None:
            market_available = True
            total = mkt_home + mkt_away
            if total <= 0.0:
                p_h, p_a = 0.5, 0.5
                market_available = False
            else:
                p_h = mkt_home / total
                p_a = mkt_away / total
            confidence = min(0.85, max(0.25, abs(p_h - p_a) + 0.35))
        else:
            market_available = False
            p_h, p_a = 0.5, 0.5
            confidence = 0.2

        predicted_outcome = "home_win" if p_h >= p_a else "away_win"
        explanation = [
            ContributionItem(
                factor="market",
                direction="support" if market_available else "neutral",
                weight=1.0,
                available=market_available,
                detail="series moneyline only",
                predicted_outcome=predicted_outcome if market_available else None,
            )
        ]

        return PredictionResult(
            predicted_scores={},
            outcome_probabilities={
                "home_win": p_h,
                "away_win": p_a,
            },
            confidence=confidence,
            engine_name=self.name(),
            explanation=explanation,
            betting_analysis=None,
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
