# backend/app/sports/basketball/engines/basketball_engine.py
"""BasketballEngine — Bradley-Terry binary prediction engine.

Uses 4 independent factors that each compute P(home_win), then
weighted-average fusion. Unlike football (3-way home/draw/away),
basketball has binary outcomes (home_win/away_win, no draws).

Factors:
    elo (0.45)        — Elo-based win probability with HFA
    home_court (0.15) — NBA historical home win rate (constant 0.58)
    rest (0.15)       — Rest days advantage
    form (0.25)       — Recent form (last-10 win rate)

Weights are read from FactorRegistry at call time, falling back to
defaults if FactorRegistry is None. When a factor is unavailable,
its weight is redistributed proportionally to available factors.
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
from app.sports.basketball.elo_calculator import compute_expected_score

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry

# Default factor weights (sum to 1.0)
_DEFAULT_WEIGHTS = {
    "elo": 0.38,
    "home_court": 0.11,
    "rest": 0.11,
    "form": 0.16,
    "net_rating": 0.13,
    "travel": 0.05,
    "injury": 0.06,
}

# NBA historical home win rate (constant)
_HOME_COURT_PROB = 0.58


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _is_playoff_stage(stage: str | None) -> bool:
    s = (stage or "").lower().strip()
    return s in {"playoff", "playoffs", "postseason", "post_season"}


class BasketballEngine:
    """Bradley-Terry binary outcome engine. Implements PredictionEngine Protocol."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def name(self) -> str:
        return "basketball"

    def supported_sports(self) -> list[str]:
        return ["basketball"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        competition = match.season.competition.code
        playoff = _is_playoff_stage(match.stage)
        from app.kernel.elo_params_resolve import resolve_nba_hfa

        hfa = resolve_nba_hfa(playoff=playoff)
        league_avg = config.settings.NBA_LEAGUE_AVG_TOTAL
        home_court_prob = (
            float(config.settings.NBA_HOME_COURT_PLAYOFF)
            if playoff
            else _HOME_COURT_PROB
        )

        # Get weights from FactorRegistry or fall back to defaults
        if self._factor_registry:
            weights = {
                fid: self._factor_registry.get_weight(fid, competition)
                for fid in _DEFAULT_WEIGHTS
            }
        else:
            weights = dict(_DEFAULT_WEIGHTS)

        # Compute each factor's P(home_win) and availability
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

        # 2. Home court factor (regular 0.58; playoff slightly lower — P1-B5)
        p_home_court = home_court_prob
        factors.append(("home_court", p_home_court, weights["home_court"], True))

        custom = features.custom if isinstance(features.custom, dict) else {}

        # 3. Rest factor (+ P1-B2 back-to-back: rest_days <= 1)
        rest_home = features.general.rest_days_home
        rest_away = features.general.rest_days_away
        b2b_home = bool(custom.get("b2b_home")) or (
            rest_home is not None and float(rest_home) <= 1.0
        )
        b2b_away = bool(custom.get("b2b_away")) or (
            rest_away is not None and float(rest_away) <= 1.0
        )
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(rest_home - rest_away, -3, 3)
            p_rest = 0.5 + rest_diff * 0.03
            if b2b_home and not b2b_away:
                p_rest -= 0.025
            elif b2b_away and not b2b_home:
                p_rest += 0.025
            p_rest = _clamp(p_rest, 0.35, 0.65)
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

        # 5. Net rating (P1-B3): ORtg - DRtg differential → soft P(home)
        ortg_h = custom.get("ortg_home")
        ortg_a = custom.get("ortg_away")
        drtg_h = custom.get("drtg_home")
        drtg_a = custom.get("drtg_away")
        net_available = all(
            v is not None for v in (ortg_h, ortg_a, drtg_h, drtg_a)
        )
        if net_available:
            try:
                net_home = float(ortg_h) - float(drtg_h)  # type: ignore[arg-type]
                net_away = float(ortg_a) - float(drtg_a)  # type: ignore[arg-type]
                net_diff = net_home - net_away
                p_net = 0.5 + _clamp(net_diff, -15.0, 15.0) * 0.012
                p_net = _clamp(p_net, 0.30, 0.70)
            except (TypeError, ValueError):
                p_net = 0.5
                net_available = False
        else:
            p_net = 0.5
        factors.append(("net_rating", p_net, weights.get("net_rating", 0.13), net_available))

        # 6. Travel / timezone (P1-B3): away distance → soft home boost
        from app.sports._shared.team_geo import travel_prob_home

        travel_km = custom.get("travel_km_away")
        if travel_km is None and features.general.travel_distance_km is not None:
            travel_km = features.general.travel_distance_km
        tz_off = custom.get("timezone_offset_hours_away")
        p_travel, travel_available = travel_prob_home(
            float(travel_km) if travel_km is not None else None,
            float(tz_off) if tz_off is not None else None,
        )
        factors.append(("travel", p_travel, weights.get("travel", 0.05), travel_available))

        # 7. Injury soft (P1-B1): higher impact = worse for that side
        inj_h = features.player.injury_impact_home
        inj_a = features.player.injury_impact_away
        if inj_h is None:
            inj_h = custom.get("injury_impact_home")
        if inj_a is None:
            inj_a = custom.get("injury_impact_away")
        if inj_h is not None and inj_a is not None:
            try:
                delta = _clamp(float(inj_a) - float(inj_h), -0.4, 0.4)
                p_inj = _clamp(0.5 + delta * 0.12, 0.35, 0.65)
                injury_available = True
            except (TypeError, ValueError):
                p_inj = 0.5
                injury_available = False
        else:
            p_inj = 0.5
            injury_available = False
        factors.append(("injury", p_inj, weights.get("injury", 0.06), injury_available))

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

        # Score conversion
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
            detail_extra = ""
            if fid == "rest" and available and (b2b_home or b2b_away):
                detail_extra = f"; b2b_home={b2b_home} b2b_away={b2b_away}"
            if available:
                detail = f"P(home_win)={round(p, 4)}{detail_extra}"
            else:
                detail = f"{fid} unavailable"
            explanation.append(ContributionItem(
                factor=fid,
                direction="support" if available else "neutral",
                weight=w,
                available=available,
                detail=detail,
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
            engine_name="basketball",
            explanation=explanation,
            betting_analysis={
                "confidence_breakdown": conf_break,
                "soft_totals_btts": soft_totals_from_scores(
                    predicted_scores, line=totals_line, sport="basketball",
                    line_source=totals_source, market_p_over=market_p_over,
                ),
            },
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
