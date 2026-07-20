"""Dixon-Coles football engine for the Prediction Kernel.

Converts Elo ratings (and optional form/injury adjustments) into expected
goals, then applies Poisson + Dixon-Coles rho correction for 3-way
probabilities. Rho is loaded from data/dixon_coles_params.json when present
(same file as the legacy world-cup rule engine).
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from app.kernel.domain import (
    ContributionItem,
    FeatureSet,
    MatchIdentity,
    PredictionResult,
)
from app.kernel.engines.elo_odds_engine import _probabilities_to_scores

logger = logging.getLogger(__name__)

_PARAMS_PATH = Path(os.getenv(
    "DIXON_COLES_PARAMS_FILE",
    str(Path(__file__).resolve().parents[3] / "data" / "dixon_coles_params.json"),
))
_FALLBACK_RHO = 0.0
_NEUTRAL_3WAY = {"home_win": 0.40, "draw": 0.30, "away_win": 0.30}


@lru_cache(maxsize=1)
def _load_rho() -> float:
    try:
        if not _PARAMS_PATH.exists():
            return _FALLBACK_RHO
        data = json.loads(_PARAMS_PATH.read_text(encoding="utf-8"))
        rho = float(data.get("rho", _FALLBACK_RHO))
        return max(-0.5, min(0.5, rho))
    except Exception:
        logger.exception("Failed to load Dixon-Coles rho from %s", _PARAMS_PATH)
        return _FALLBACK_RHO


def _poisson_pmf(lam: float, k: int) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def elo_to_xg(
    elo_home: float,
    elo_away: float,
    *,
    form_home: float | None = None,
    form_away: float | None = None,
    injury_home: float | None = None,
    injury_away: float | None = None,
) -> tuple[float, float]:
    """Map Elo (+ light adjustments) to expected goals."""
    elo_diff = elo_home - elo_away
    p_home = 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))
    home_xg = 1.35 + 0.9 * (p_home - 0.5)
    away_xg = 1.15 - 0.9 * (p_home - 0.5)

    if form_home is not None and form_away is not None:
        home_xg *= 0.9 + 0.2 * form_home
        away_xg *= 0.9 + 0.2 * form_away

    if injury_home is not None:
        home_xg *= max(0.7, 1.0 - 0.15 * injury_home)
    if injury_away is not None:
        away_xg *= max(0.7, 1.0 - 0.15 * injury_away)

    return max(0.2, min(4.5, home_xg)), max(0.2, min(4.5, away_xg))


def dixon_coles_probabilities(
    home_xg: float,
    away_xg: float,
    *,
    rho: float | None = None,
    max_goals: int = 8,
) -> dict[str, float]:
    """3-way probabilities from independent Poisson + DC tau correction."""
    if rho is None:
        rho = _load_rho()

    home_win = draw = away_win = 0.0
    for hg in range(max_goals + 1):
        ph = _poisson_pmf(home_xg, hg)
        for ag in range(max_goals + 1):
            pa = _poisson_pmf(away_xg, ag)
            joint = ph * pa
            if hg <= 1 and ag <= 1:
                if hg == 0 and ag == 0:
                    joint *= 1.0 - home_xg * away_xg * rho
                elif hg == 1 and ag == 0:
                    joint *= 1.0 + away_xg * rho
                elif hg == 0 and ag == 1:
                    joint *= 1.0 + home_xg * rho
                elif hg == 1 and ag == 1:
                    joint *= 1.0 - rho
            if joint < 0:
                joint = 0.0
            if hg > ag:
                home_win += joint
            elif hg < ag:
                away_win += joint
            else:
                draw += joint

    total = home_win + draw + away_win
    if total <= 0:
        return dict(_NEUTRAL_3WAY)
    return {
        "home_win": round(home_win / total, 4),
        "draw": round(draw / total, 4),
        "away_win": round(away_win / total, 4),
    }


class DixonColesEngine:
    """Kernel PredictionEngine: Elo → xG → Dixon-Coles 3-way."""

    def name(self) -> str:
        return "dixon_coles"

    def supported_sports(self) -> list[str]:
        return ["football"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        elo_home = features.team.elo_rating_home
        elo_away = features.team.elo_rating_away
        if elo_home is None or elo_away is None:
            probs = dict(_NEUTRAL_3WAY)
            home_xg = away_xg = 1.2
            elo_ok = False
        else:
            home_xg, away_xg = elo_to_xg(
                elo_home,
                elo_away,
                form_home=features.team.form_home,
                form_away=features.team.form_away,
                injury_home=features.player.injury_impact_home,
                injury_away=features.player.injury_impact_away,
            )
            probs = dixon_coles_probabilities(home_xg, away_xg)
            elo_ok = True

        rho = _load_rho()
        confidence = round(
            min(0.92, max(0.35, max(probs.values()) * 0.9 + (0.05 if elo_ok else 0.0))),
            4,
        )
        scores = _probabilities_to_scores(probs)
        pred = max(probs, key=probs.get)  # type: ignore[arg-type]

        explanation = [
            ContributionItem(
                factor="elo",
                direction="support" if elo_ok else "neutral",
                weight=0.70,
                available=elo_ok,
                detail=(
                    f"Elo {elo_home} vs {elo_away} → xG {home_xg:.2f}-{away_xg:.2f}"
                    if elo_ok else "Elo unavailable"
                ),
                predicted_outcome=pred if elo_ok else None,
            ),
            ContributionItem(
                factor="dixon_coles_rho",
                direction="support",
                weight=0.30,
                available=True,
                detail=f"rho={rho:.4f}",
                predicted_outcome=None,
            ),
        ]

        return PredictionResult(
            predicted_scores=scores,
            outcome_probabilities=probs,
            confidence=confidence,
            engine_name="dixon_coles",
            explanation=explanation,
            betting_analysis={
                "expected_goals": {"home": round(home_xg, 3), "away": round(away_xg, 3)},
                "rho": rho,
            },
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
