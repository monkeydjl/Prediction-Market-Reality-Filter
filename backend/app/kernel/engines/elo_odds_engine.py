"""Elo + Betting Odds fusion prediction engine.

Migrated from app/services/world_cup_engines/world_cup_elo_odds_engine.py.
This engine is sport-agnostic: it consumes FeatureSet and produces
PredictionResult, with no dependency on any world_cup_* module.

Combines:
1. Elo ratings (stable, long-term team strength) via BTD model
2. Betting market odds (sharp, incorporates everything)

Research shows ~70-75% accuracy with 30% Elo + 70% Odds weighting.

The numerical probability pipeline (BTD -> odds normalization -> 30/70
fusion) is intentionally identical to the legacy engine so that the two
produce matching outcome probabilities during the migration. See the
equivalence tests in tests/test_kernel_elo_odds_engine.py
(``TestEloOddsEquivalence``). The output *envelope* differs: this engine
returns a ``PredictionResult`` dataclass and uses a simpler confidence
model, whereas the legacy engine returns a plain dict with extra fields
(score matrix, prediction interval, ...).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.kernel.domain import (
    FeatureSet, MatchIdentity, PredictionResult, ContributionItem,
)
from app.kernel.engines.btd_model import calculate_btd_probabilities


def _odds_to_probabilities(
    odds_home: float, odds_draw: float, odds_away: float,
) -> dict[str, float]:
    """Convert decimal odds to normalized probabilities (remove overround)."""
    implied_home = 1.0 / odds_home
    implied_draw = 1.0 / odds_draw
    implied_away = 1.0 / odds_away
    total = implied_home + implied_draw + implied_away
    return {
        "home_win": round(implied_home / total, 4),
        "draw": round(implied_draw / total, 4),
        "away_win": round(implied_away / total, 4),
    }


def _fuse_elo_and_odds(
    elo_probs: dict[str, float],
    market_probs: dict[str, float] | None,
    elo_weight: float = 0.30,
    odds_weight: float = 0.70,
) -> dict[str, float]:
    """Fuse Elo and market probabilities. Falls back to Elo-only if no market."""
    if market_probs is None:
        return elo_probs
    total_w = elo_weight + odds_weight
    ew = elo_weight / total_w
    ow = odds_weight / total_w
    return {
        "home_win": round(elo_probs["home_win"] * ew + market_probs["home_win"] * ow, 4),
        "draw": round(elo_probs["draw"] * ew + market_probs["draw"] * ow, 4),
        "away_win": round(elo_probs["away_win"] * ew + market_probs["away_win"] * ow, 4),
    }


def _probabilities_to_scores(
    probs: dict[str, float], league_avg_goals: float = 2.7,
) -> dict[str, float]:
    """Convert win probabilities to expected scores via Poisson."""
    home_advantage = (probs["home_win"] - probs["away_win"]) / 2
    home_share = 0.5 + home_advantage
    home_goals = league_avg_goals * home_share
    away_goals = league_avg_goals * (1 - home_share)
    draw_factor = 1.0 - (probs["draw"] - 0.20) * 0.5
    home_goals *= draw_factor
    away_goals *= draw_factor
    return {"home": round(home_goals, 2), "away": round(away_goals, 2)}


def _calculate_confidence(probs: dict[str, float]) -> float:
    """Confidence = max probability, slightly deflated."""
    max_prob = max(probs.values())
    return round(min(max_prob * 0.95, 0.95), 4)


class EloOddsEngine:
    """Elo + Odds fusion engine. Implements PredictionEngine Protocol."""

    def name(self) -> str:
        return "elo_odds"

    def supported_sports(self) -> list[str]:
        return ["*"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        elo_home = features.team.elo_rating_home
        elo_away = features.team.elo_rating_away
        is_knockout = match.stage not in ("group_stage", "regular_season")

        # Elo probabilities via BTD
        if elo_home is not None and elo_away is not None:
            elo_probs = calculate_btd_probabilities(
                elo_home, elo_away, is_neutral=True, is_knockout=is_knockout,
            )
            elo_available = True
        else:
            elo_probs = {"home_win": 0.4, "draw": 0.3, "away_win": 0.3}
            elo_available = False

        # Market probabilities
        odds_h = features.market.odds_home
        odds_d = features.market.odds_draw
        odds_a = features.market.odds_away
        if odds_h and odds_d and odds_a and odds_h > 1.0 and odds_d > 1.0 and odds_a > 1.0:
            market_probs = _odds_to_probabilities(odds_h, odds_d, odds_a)
            odds_available = True
        else:
            market_probs = None
            odds_available = False

        # Fuse
        fused = _fuse_elo_and_odds(elo_probs, market_probs)
        scores = _probabilities_to_scores(fused)
        confidence = _calculate_confidence(fused)

        # Explanation
        explanation = [
            ContributionItem(
                factor="elo", direction="support" if elo_available else "neutral",
                weight=0.30, available=elo_available,
                detail=f"Elo {elo_home} vs {elo_away}" if elo_available else "Elo unavailable",
            ),
            ContributionItem(
                factor="odds", direction="support" if odds_available else "neutral",
                weight=0.70, available=odds_available,
                detail=f"Odds {odds_h}/{odds_d}/{odds_a}" if odds_available else "Odds unavailable",
            ),
        ]

        return PredictionResult(
            predicted_scores=scores,
            outcome_probabilities=fused,
            confidence=confidence,
            engine_name="elo_odds",
            explanation=explanation,
            betting_analysis=None,
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
