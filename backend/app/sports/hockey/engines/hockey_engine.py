# backend/app/sports/hockey/engines/hockey_engine.py
"""HockeyEngine — 5-factor Bradley-Terry binary prediction engine.

5 factors that each compute P(home_win), then weighted-average fusion.
NHL has binary outcomes (home_win/away_win, no draws). Overtime and
shootout games still resolve to a binary winner; the OT/SO info is
preserved separately in FeatureSet.custom (Constraint 22).

Factors:
    elo (0.35)        — Elo-based win probability with HFA=55
    home_court (0.15) — NHL historical home win rate (constant 0.55)
    rest (0.15)       — Rest days differential
    form (0.20)       — Recent form (last-10 win rate)
    goalie (0.15)     — Starting goalie save% differential

Goalie formula:
    sv_pct_diff = sv_pct_home - sv_pct_away
    p = 0.5 + clamp(sv_pct_diff, -0.1, 0.1) * 2.0
    (Higher home save% → p > 0.5)

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
    "elo": 0.35,
    "home_court": 0.15,
    "rest": 0.15,
    "form": 0.20,
    "goalie": 0.15,
}

# NHL historical home win rate (constant — slightly higher than MLB's 0.54)
_HOME_COURT_PROB = 0.55


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class HockeyEngine:
    """5-factor Bradley-Terry binary outcome engine. Implements PredictionEngine Protocol."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def name(self) -> str:
        return "hockey"

    def supported_sports(self) -> list[str]:
        return ["hockey"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        competition = match.season.competition.code
        hfa = config.settings.NHL_ELO_HFA
        league_avg = config.settings.NHL_LEAGUE_AVG_TOTAL

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

        # 2. Home court factor (constant — NHL home advantage)
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

        # 5. Goalie factor
        # sv_pct_diff = sv_pct_home - sv_pct_away; home goalie better → diff > 0 → p > 0.5
        sv_pct_home = features.custom.get("goalie_save_pct_home")
        sv_pct_away = features.custom.get("goalie_save_pct_away")
        if sv_pct_home is not None and sv_pct_away is not None:
            sv_pct_diff = _clamp(sv_pct_home - sv_pct_away, -0.1, 0.1)
            p_goalie = 0.5 + sv_pct_diff * 2.0
            goalie_available = True
        else:
            p_goalie = 0.5
            goalie_available = False
        factors.append(("goalie", p_goalie, weights["goalie"], goalie_available))

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

        # Score conversion (NHL: league_avg=5.5, low-scoring)
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

        # Confidence (same formula as BaseballEngine / BasketballEngine)
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
            engine_name="hockey",
            explanation=explanation,
            betting_analysis=None,
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
