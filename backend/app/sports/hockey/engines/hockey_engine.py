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
from app.kernel.engines.confidence import compute_confidence, confidence_breakdown
from app.kernel.engines.elo_odds_engine import (
    resolve_totals_line,
    soft_totals_from_scores,
)
from app.kernel.domain import (
    FeatureSet, MatchIdentity, PredictionResult, ContributionItem,
)
from app.sports._shared.elo_calculator import compute_expected_score

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry

# Default factor weights (sum to 1.0)
_DEFAULT_WEIGHTS = {
    "elo": 0.30,
    "home_court": 0.13,
    "rest": 0.12,
    "form": 0.17,
    "goalie": 0.14,
    "travel": 0.07,
    "attack_share": 0.07,
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
        from app.kernel.elo_params_resolve import resolve_elo_params

        hfa = resolve_elo_params("nhl")["hfa"]
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

        # 3. Rest factor (+ P1-H2 back-to-back)
        rest_home = features.general.rest_days_home
        rest_away = features.general.rest_days_away
        custom = features.custom if isinstance(features.custom, dict) else {}
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
                p_rest -= 0.03
            elif b2b_away and not b2b_home:
                p_rest += 0.03
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

        # 6. Travel / timezone (P1-H3) — Canadian cross-zone hops included
        from app.sports._shared.team_geo import travel_prob_home

        travel_km = custom.get("travel_km_away")
        if travel_km is None and features.general.travel_distance_km is not None:
            travel_km = features.general.travel_distance_km
        tz_off = custom.get("timezone_offset_hours_away")
        p_travel, travel_available = travel_prob_home(
            float(travel_km) if travel_km is not None else None,
            float(tz_off) if tz_off is not None else None,
        )
        factors.append(("travel", p_travel, weights.get("travel", 0.08), travel_available))

        # 7. Attack share (P1-H1) — corsi% preferred, else GF share soft proxy
        corsi_h = custom.get("corsi_pct_home")
        corsi_a = custom.get("corsi_pct_away")
        xg_h = custom.get("xg_for_home")
        xg_a = custom.get("xg_for_away")
        gf_h = custom.get("team_gf_home")
        gf_a = custom.get("team_gf_away")
        p_attack = 0.5
        attack_available = False
        attack_detail_src = "unavailable"
        try:
            if corsi_h is not None and corsi_a is not None:
                # corsi as 0-100 or 0-1
                ch, ca = float(corsi_h), float(corsi_a)
                if ch > 1.5 or ca > 1.5:
                    ch, ca = ch / 100.0, ca / 100.0
                share = ch / (ch + ca) if (ch + ca) > 0 else 0.5
                p_attack = _clamp(0.5 + (share - 0.5) * 0.6, 0.38, 0.62)
                attack_available = True
                attack_detail_src = "corsi"
            elif xg_h is not None and xg_a is not None:
                xh, xa = float(xg_h), float(xg_a)
                total = xh + xa
                if total > 0:
                    share = xh / total
                    p_attack = _clamp(0.5 + (share - 0.5) * 0.55, 0.38, 0.62)
                    attack_available = True
                    attack_detail_src = "xg_for"
            elif gf_h is not None and gf_a is not None:
                gh, ga = float(gf_h), float(gf_a)
                total = gh + ga
                if total > 0:
                    share = gh / total
                    # Weaker than true corsi/xG — soft GF proxy only
                    p_attack = _clamp(0.5 + (share - 0.5) * 0.35, 0.42, 0.58)
                    attack_available = True
                    attack_detail_src = "gf_proxy"
        except (TypeError, ValueError):
            p_attack = 0.5
            attack_available = False
        factors.append(
            (
                "attack_share",
                p_attack,
                weights.get("attack_share", 0.07),
                attack_available,
            ),
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

        # Score conversion (NHL: league_avg=5.5, low-scoring)
        if elo_home is not None and elo_away is not None:
            margin = _clamp((elo_home - elo_away + hfa) * 0.015, -2.5, 2.5)
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
            if fid == "attack_share" and available:
                detail_extra = f"; src={attack_detail_src}"
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
            engine_name="hockey",
            explanation=explanation,
            betting_analysis={
                "confidence_breakdown": conf_break,
                "soft_totals_btts": soft_totals_from_scores(
                    predicted_scores, line=totals_line, sport="hockey",
                    line_source=totals_source, market_p_over=market_p_over,
                ),
            },
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
