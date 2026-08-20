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
from app.kernel.engines.confidence import compute_confidence, confidence_breakdown
from app.kernel.engines.elo_odds_engine import (
    resolve_totals_line,
    soft_totals_from_scores,
)
from app.sports._shared.elo_calculator import compute_expected_score

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry

# Default factor weights (sum to 1.0)
_DEFAULT_WEIGHTS: dict[str, float] = {
    "elo": 0.26,
    "home_court": 0.10,
    "rest": 0.08,
    "form": 0.11,
    "starting_pitcher": 0.20,
    "park": 0.07,
    "bullpen": 0.07,
    "weather": 0.06,
    "platoon": 0.05,
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
        from app.kernel.elo_params_resolve import resolve_elo_params

        hfa = resolve_elo_params("mlb")["hfa"]
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

        custom = features.custom if isinstance(features.custom, dict) else {}

        # 6. Park factor (P1-M2)
        park = custom.get("park_factor")
        if park is not None:
            try:
                pf = float(park)
                p_park = 0.5 + _clamp((pf - 1.0) * 0.25, -0.04, 0.04)
                park_available = True
            except (TypeError, ValueError):
                p_park = 0.5
                park_available = False
        else:
            p_park = 0.5
            park_available = False
        factors.append(("park", p_park, weights.get("park", 0.07), park_available))

        # 7. Bullpen ERA differential (P1-M1 soft)
        bullpen_home = custom.get("bullpen_era_home")
        bullpen_away = custom.get("bullpen_era_away")
        if bullpen_home is not None and bullpen_away is not None:
            try:
                b_diff = _clamp(float(bullpen_away) - float(bullpen_home), -2.0, 2.0)
                p_bullpen = 0.5 + b_diff * 0.04
                bullpen_available = True
            except (TypeError, ValueError):
                p_bullpen = 0.5
                bullpen_available = False
        else:
            p_bullpen = 0.5
            bullpen_available = False
        factors.append(("bullpen", p_bullpen, weights.get("bullpen", 0.07), bullpen_available))

        # 8. Weather soft (P1-M3 outdoor)
        wind = custom.get("weather_wind_mph")
        temp = custom.get("weather_temp_c")
        if temp is None and features.environment.weather_temp_c is not None:
            temp = features.environment.weather_temp_c
        if wind is not None or temp is not None:
            try:
                p_weather = 0.5
                if temp is not None:
                    t = float(temp)
                    if t < 5:
                        p_weather -= 0.01
                    elif t > 30:
                        p_weather += 0.01
                if wind is not None and float(wind) >= 15:
                    p_weather += 0.01
                p_weather = _clamp(p_weather, 0.45, 0.55)
                weather_available = True
            except (TypeError, ValueError):
                p_weather = 0.5
                weather_available = False
        else:
            p_weather = 0.5
            weather_available = False
        factors.append(("weather", p_weather, weights.get("weather", 0.06), weather_available))

        # 9. Platoon soft (P1-M4) — lineup vs pitcher handedness OPS/wOBA proxy
        # custom keys: platoon_ops_home, platoon_ops_away (or platoon_advantage_home in [-0.1,0.1])
        platoon_ok = False
        try:
            ops_h = custom.get("platoon_ops_home")
            ops_a = custom.get("platoon_ops_away")
            adv = custom.get("platoon_advantage_home")
            if ops_h is not None and ops_a is not None:
                d = _clamp(float(ops_h) - float(ops_a), -0.15, 0.15)
                p_platoon = 0.5 + d * 0.8  # 0.050 OPS ≈ +0.04 win prob
                platoon_ok = True
            elif adv is not None:
                p_platoon = 0.5 + _clamp(float(adv), -0.10, 0.10)
                platoon_ok = True
            else:
                p_platoon = 0.5
        except (TypeError, ValueError):
            p_platoon = 0.5
            platoon_ok = False
        p_platoon = _clamp(p_platoon, 0.40, 0.60)
        factors.append(
            ("platoon", p_platoon, weights.get("platoon", 0.05), platoon_ok),
        )

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

        confidence = compute_confidence(
            outcome_probabilities,
            available_flags=[a for _, _, _, a in factors],
            predicted_outcomes=[
                ("home_win" if p >= 0.5 else "away_win") if a else None
                for _, p, _, a in factors
            ],
            data_quality=features.data_quality,
            odds_fresh=bool(features.market.odds_fresh) if features.market.odds_home else None,
            custom=custom,
        )

        conf_break = confidence_breakdown(
            outcome_probabilities,
            available_flags=[a for _, _, _, a in factors],
            predicted_outcomes=[("home_win" if p >= 0.5 else "away_win") if a else None for _, p, _, a in factors],
            data_quality=features.data_quality,
            odds_fresh=bool(features.market.odds_fresh) if features.market.odds_home else None,
            custom=custom,
        )

        # P1-O1: a real book total outranks the league-average placeholder,
        # which equals the expected total by construction and so makes p_over a
        # per-sport constant. Absent or malformed → the placeholder.
        totals_line, totals_source, market_p_over = resolve_totals_line(
            features.custom, league_avg,
        )

        return PredictionResult(
            predicted_scores=predicted_scores,
            outcome_probabilities=outcome_probabilities,
            confidence=confidence,
            engine_name="baseball",
            explanation=explanation,
            betting_analysis={
                "confidence_breakdown": conf_break,
                "soft_totals_btts": soft_totals_from_scores(
                    predicted_scores, line=totals_line, sport="baseball",
                    line_source=totals_source, market_p_over=market_p_over,
                ),
            },
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
