# backend/app/sports/baseball/engines/baseball_engine.py
"""BaseballEngine — 5-factor Bradley-Terry binary prediction engine.

5 factors that each compute P(home_win), then weighted-average fusion.
MLB has binary outcomes (home_win/away_win, no draws).

Factors:
    elo (0.30)             — Elo-based win probability with HFA=50
    home_court (0.10)      — MLB historical home win rate (constant 0.54)
    rest (0.15)            — Rest days differential
    form (0.20)            — Recent form (last-10 win rate)
    starting_pitcher (0.25)— Starting pitcher ERA differential

Starting pitcher formula:
    era_diff = era_away - era_home   (home pitcher better → lower ERA → era_diff > 0)
    p = 0.5 + clamp(era_diff, -2.0, 2.0) * 0.1

Weights are read from FactorRegistry at call time, falling back to
defaults if FactorRegistry is None. When a factor is unavailable, its
weight is redistributed proportionally to available factors.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.core import config
from app.kernel.domain import (
    FeatureSet, MatchIdentity, PredictionResult, ContributionItem,
)
from app.sports._shared.elo_calculator import compute_expected_score

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry

# Default factor weights (sum to 1.0)
_DEFAULT_WEIGHTS = {
    "elo": 0.30,
    "home_court": 0.10,
    "rest": 0.15,
    "form": 0.20,
    "starting_pitcher": 0.25,
}

# MLB historical home win rate (constant — lower than NBA's 0.58)
_HOME_COURT_PROB = 0.54


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class BaseballEngine:
    """5-factor Bradley-Terry binary outcome engine. Implements PredictionEngine Protocol."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def name(self) -> str:
        return "baseball"

    def supported_sports(self) -> list[str]:
        return ["baseball"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        competition = match.season.competition.code
        hfa = config.settings.MLB_ELO_HFA
        league_avg = config.settings.MLB_LEAGUE_AVG_TOTAL

        # Get weights from FactorRegistry or fall back to defaults
        if self._factor_registry:
            weights = {
                fid: self._factor_registry.get_weight(fid, competition)
                for fid in _DEFAULT_WEIGHTS
            }
        else:
            weights = dict(_DEFAULT_WEIGHTS)

        factors: list[tuple[str, float, float, bool]] = []

        # 1. Elo factor
        elo_home = features.team.elo_rating_home
        elo_away = features.team.elo_rating_away
        if elo_home is not None and elo_away is not None:
            p_elo = compute_expected_score(elo_home, elo_away, hfa)
            elo_available = True
        else:
            p_elo = 0.5
            elo_available = False
        factors.append(("elo", p_elo, weights["elo"], elo_available))

        # 2. Home court factor (constant — MLB lower home advantage than NBA)
        p_home_court = _HOME_COURT_PROB
        factors.append(("home_court", p_home_court, weights["home_court"], True))

        # 3. Rest factor
        rest_home = features.general.rest_days_home
        rest_away = features.general.rest_days_away
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(rest_home - rest_away, -3, 3)
            p_rest = 0.5 + rest_diff * 0.03
            rest_available = True
        else:
            p_rest = 0.5
            rest_available = False
        factors.append(("rest", p_rest, weights["rest"], rest_available))

        # 4. Form factor
        form_home = features.team.form_home
        form_away = features.team.form_away
        if form_home is not None and form_away is not None:
            form_diff = _clamp(form_home - form_away, -0.3, 0.3)
            p_form = 0.5 + form_diff * 0.5
            form_available = True
        else:
            p_form = 0.5
            form_available = False
        factors.append(("form", p_form, weights["form"], form_available))

        # 5. Starting pitcher factor
        # era_diff = era_away - era_home; home pitcher better (lower ERA) → era_diff > 0 → p > 0.5
        era_home = features.custom.get("pitcher_era_home")
        era_away = features.custom.get("pitcher_era_away")
        if era_home is not None and era_away is not None:
            era_diff = _clamp(era_away - era_home, -2.0, 2.0)
            p_pitcher = 0.5 + era_diff * 0.1
            pitcher_available = True
        else:
            p_pitcher = 0.5
            pitcher_available = False
        factors.append(("starting_pitcher", p_pitcher, weights["starting_pitcher"], pitcher_available))

        # Weighted fusion — redistribute unavailable factor weights
        available_factors = [(f, p, w) for f, p, w, a in factors if a]
        total_w = sum(w for _, _, w in available_factors)
        if total_w > 0:
            p_home = sum(p * (w / total_w) for _, p, w in available_factors)
        else:
            p_home = 0.5  # All factors unavailable → neutral
        p_away = 1.0 - p_home

        outcome_probabilities = {
            "home_win": round(p_home, 4),
            "away_win": round(p_away, 4),
        }

        # Score conversion (MLB: league_avg=8.5, low-scoring)
        if elo_home is not None and elo_away is not None:
            margin = (elo_home - elo_away + hfa) * 0.03
        else:
            margin = 0.0
        home_score = league_avg / 2 + margin / 2
        away_score = league_avg / 2 - margin / 2
        predicted_scores = {
            "home": round(home_score, 1),
            "away": round(away_score, 1),
        }

        # Confidence (same formula as BasketballEngine)
        confidence = round(min(max(p_home, p_away) * 0.95, 0.95), 4)

        # Build explanation with ContributionItems
        explanation: list[ContributionItem] = []
        for fid, p, w, available in factors:
            predicted_outcome = "home_win" if p >= 0.5 else "away_win"
            explanation.append(ContributionItem(
                factor=fid,
                direction="support" if available else "neutral",
                weight=w,
                available=available,
                detail=f"P(home_win)={round(p, 4)}" if available else f"{fid} unavailable",
                predicted_outcome=predicted_outcome if available else None,
            ))

        return PredictionResult(
            predicted_scores=predicted_scores,
            outcome_probabilities=outcome_probabilities,
            confidence=confidence,
            engine_name="baseball",
            explanation=explanation,
            betting_analysis=None,
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
