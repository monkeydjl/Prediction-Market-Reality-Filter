"""Football multi-factor engine — 3-way fusion beyond Elo + odds.

Consumes the full FeatureSet produced by FootballFeatureBuilder:
Elo (BTD), market odds, form, rest, injury, h2h, travel, xG proxy, and
market value. Missing factors have their weight redistributed across
available ones (same pattern as BasketballEngine / BaseballEngine).

Default weights (sum 1.0):
    elo            0.24  — BTD three-way from Elo ratings
    odds           0.36  — market-implied 1x2 (overround removed)
    form           0.10  — recent form differential
    rest           0.05  — rest-days differential
    injury         0.05  — injury impact differential
    h2h            0.05  — head-to-head historical rates
    travel         0.04  — distance / timezone soft home edge
    xg             0.07  — attack-rate / xG proxy soft share
    market_value   0.04  — squad valuation differential
    possession     0.04  — possession / shots soft share
    referee        0.02  — referee home-bias soft (custom-gated)
    altitude       0.02  — high-altitude venue soft home edge

Registered only when FOOTBALL_MULTI_FACTOR_ENGINE_ENABLED is true so the
legacy EloOddsEngine remains the default path.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.kernel.domain import (
    ContributionItem,
    FeatureSet,
    MatchIdentity,
    PredictionResult,
)
from app.kernel.engines.btd_model import calculate_btd_probabilities
from app.kernel.engines.confidence import compute_confidence, confidence_breakdown
from app.kernel.engines.elo_odds_engine import (
    _KNOCKOUT_STAGES,
    _odds_to_probabilities,
    _probabilities_to_scores,
    soft_totals_btts_analysis,
)
from app.kernel.engines.odds_quality import (
    describe_odds_quality,
    odds_weight_multiplier,
)

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry

_DEFAULT_WEIGHTS: dict[str, float] = {
    "elo": 0.22,
    "odds": 0.34,
    "form": 0.09,
    "rest": 0.05,
    "injury": 0.05,
    "h2h": 0.05,
    "travel": 0.03,
    "xg": 0.06,
    "market_value": 0.04,
    "possession": 0.03,
    "referee": 0.02,
    "altitude": 0.02,
}

# P1-E3: competition-specific multi-factor base profiles (sum ≈ 1.0).
# Only used when FactorRegistry has no competition-specific override for a factor.
# Does not touch EloOddsEngine global 0.30/0.70.
_COMPETITION_WEIGHT_PROFILES: dict[str, dict[str, float]] = {
    "epl": {
        "elo": 0.20, "odds": 0.33, "form": 0.09, "rest": 0.05, "injury": 0.05,
        "h2h": 0.05, "travel": 0.03, "xg": 0.07, "market_value": 0.05,
        "possession": 0.04, "referee": 0.02, "altitude": 0.02,
    },
    "laliga": {
        "elo": 0.21, "odds": 0.32, "form": 0.09, "rest": 0.05, "injury": 0.05,
        "h2h": 0.05, "travel": 0.03, "xg": 0.07, "market_value": 0.05,
        "possession": 0.04, "referee": 0.02, "altitude": 0.02,
    },
    "serie_a": {
        "elo": 0.21, "odds": 0.31, "form": 0.10, "rest": 0.05, "injury": 0.05,
        "h2h": 0.05, "travel": 0.03, "xg": 0.07, "market_value": 0.05,
        "possession": 0.04, "referee": 0.02, "altitude": 0.02,
    },
    "bundesliga": {
        "elo": 0.22, "odds": 0.30, "form": 0.10, "rest": 0.05, "injury": 0.05,
        "h2h": 0.05, "travel": 0.03, "xg": 0.07, "market_value": 0.05,
        "possession": 0.04, "referee": 0.02, "altitude": 0.02,
    },
    "ligue_1": {
        "elo": 0.22, "odds": 0.29, "form": 0.11, "rest": 0.05, "injury": 0.05,
        "h2h": 0.05, "travel": 0.03, "xg": 0.07, "market_value": 0.05,
        "possession": 0.04, "referee": 0.02, "altitude": 0.02,
    },
    "ucl": {
        "elo": 0.25, "odds": 0.27, "form": 0.11, "rest": 0.05, "injury": 0.05,
        "h2h": 0.05, "travel": 0.04, "xg": 0.06, "market_value": 0.04,
        "possession": 0.04, "referee": 0.02, "altitude": 0.02,
    },
    "wc": {
        "elo": 0.26, "odds": 0.25, "form": 0.09, "rest": 0.05, "injury": 0.05,
        "h2h": 0.06, "travel": 0.05, "xg": 0.05, "market_value": 0.05,
        "possession": 0.05, "referee": 0.02, "altitude": 0.02,
    },
    "world_cup": {
        "elo": 0.26, "odds": 0.25, "form": 0.09, "rest": 0.05, "injury": 0.05,
        "h2h": 0.06, "travel": 0.05, "xg": 0.05, "market_value": 0.05,
        "possession": 0.05, "referee": 0.02, "altitude": 0.02,
    },
}

_NEUTRAL_3WAY = {"home_win": 0.40, "draw": 0.30, "away_win": 0.30}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_3way(probs: dict[str, float]) -> dict[str, float]:
    total = probs["home_win"] + probs["draw"] + probs["away_win"]
    if total <= 0:
        return dict(_NEUTRAL_3WAY)
    return {
        "home_win": probs["home_win"] / total,
        "draw": probs["draw"] / total,
        "away_win": probs["away_win"] / total,
    }


_MIN_VENUE_SAMPLES = 4.0


def _blend_h2h_venue(
    h2h_home: float,
    h2h_draw: float,
    custom: dict,
) -> tuple[float, float]:
    """Blend overall H2H rates toward the current home team's own venue (P1-F4).

    Club pairings meet rarely, so the same-venue subset is often 0-2 matches -
    too thin to trust on its own. alpha ramps with the sample size and caps at
    1.0, so a missing or empty subset returns the overall rates unchanged.

    Gated by FOOTBALL_H2H_VENUE_SPLIT_ENABLED (default OFF) because it moves
    the output of an already-registered engine.
    """
    from app.core import config

    if not config.settings.FOOTBALL_H2H_VENUE_SPLIT_ENABLED:
        return h2h_home, h2h_draw

    matches = custom.get("h2h_home_venue_matches")
    venue_home = custom.get("h2h_home_venue_win_rate")
    venue_draw = custom.get("h2h_home_venue_draw_rate")
    if matches is None or venue_home is None or venue_draw is None:
        return h2h_home, h2h_draw
    try:
        n = float(matches)
        v_home = float(venue_home)
        v_draw = float(venue_draw)
    except (TypeError, ValueError):
        return h2h_home, h2h_draw
    if n <= 0:
        return h2h_home, h2h_draw

    alpha = _clamp(n / _MIN_VENUE_SAMPLES, 0.0, 1.0)
    return (
        (1.0 - alpha) * h2h_home + alpha * v_home,
        (1.0 - alpha) * h2h_draw + alpha * v_draw,
    )


def _adjust_home_edge(
    base: dict[str, float],
    home_delta: float,
) -> dict[str, float]:
    """Shift probability mass from away toward home (or reverse).

    ``home_delta`` positive → more home_win. Draw is damped slightly when
    the matchup becomes more decisive.
    """
    home = base["home_win"] + home_delta
    away = base["away_win"] - home_delta * 0.7
    draw = base["draw"] - abs(home_delta) * 0.3
    return _normalize_3way({
        "home_win": max(home, 0.01),
        "draw": max(draw, 0.05),
        "away_win": max(away, 0.01),
    })


def _predicted_outcome(probs: dict[str, float]) -> str:
    return max(probs, key=probs.get)  # type: ignore[arg-type]


class FootballMultiFactorEngine:
    """Multi-factor 3-way football engine. Implements PredictionEngine Protocol."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def name(self) -> str:
        return "football_multi_factor"

    def supported_sports(self) -> list[str]:
        return ["football"]

    def _weight(self, factor_id: str, competition: str) -> float:
        """Resolve weight: registry competition override → profile → default.

        Ignores global FactorRegistry elo/odds (0.30/0.70) so this engine
        keeps its multi-factor balance unless a competition-specific weight
        was seeded or learned.
        """
        code = competition.lower().strip()
        code = {
            "seriea": "serie_a",
            "ligue1": "ligue_1",
            "worldcup": "world_cup",
        }.get(code, code)
        profile = _COMPETITION_WEIGHT_PROFILES.get(code)
        default = (
            profile[factor_id]
            if profile and factor_id in profile
            else _DEFAULT_WEIGHTS[factor_id]
        )
        if self._factor_registry is None:
            return default
        specific = self._factor_registry.get_competition_weight(
            factor_id, code,
        )
        return default if specific is None else specific

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        competition = match.season.competition.code
        is_knockout = (match.stage or "").lower().strip() in _KNOCKOUT_STAGES

        weights = {
            fid: self._weight(fid, competition) for fid in _DEFAULT_WEIGHTS
        }

        # P1-E4: thin / stale / high-overround market → lower odds weight
        odds_mult = odds_weight_multiplier(
            features.market.odds_home,
            features.market.odds_draw,
            features.market.odds_away,
            odds_fresh=bool(features.market.odds_fresh),
            custom=features.custom if isinstance(features.custom, dict) else None,
        )
        if odds_mult < 1.0:
            weights["odds"] = weights["odds"] * odds_mult

        # (factor_id, probs_3way, weight, available)
        factors: list[tuple[str, dict[str, float], float, bool]] = []
        custom = features.custom if isinstance(features.custom, dict) else {}

        # 1. Elo via BTD
        elo_home = features.team.elo_rating_home
        elo_away = features.team.elo_rating_away
        if elo_home is not None and elo_away is not None:
            elo_probs = calculate_btd_probabilities(
                elo_home, elo_away, is_neutral=True, is_knockout=is_knockout,
            )
            factors.append(("elo", elo_probs, weights["elo"], True))
        else:
            factors.append(("elo", dict(_NEUTRAL_3WAY), weights["elo"], False))

        # 2. Market odds
        odds_h = features.market.odds_home
        odds_d = features.market.odds_draw
        odds_a = features.market.odds_away
        odds_quality_note = describe_odds_quality(
            odds_h,
            odds_d,
            odds_a,
            odds_fresh=bool(features.market.odds_fresh),
            custom=features.custom if isinstance(features.custom, dict) else None,
        )
        if (
            odds_h and odds_d and odds_a
            and odds_h > 1.0 and odds_d > 1.0 and odds_a > 1.0
        ):
            market_probs = _odds_to_probabilities(odds_h, odds_d, odds_a)
            factors.append(("odds", market_probs, weights["odds"], True))
        else:
            factors.append(("odds", dict(_NEUTRAL_3WAY), weights["odds"], False))

        # 3. Form — form fields are win-rate-like in [0, 1]
        form_home = features.team.form_home
        form_away = features.team.form_away
        if form_home is not None and form_away is not None:
            form_diff = _clamp(form_home - form_away, -0.5, 0.5)
            form_probs = _adjust_home_edge(_NEUTRAL_3WAY, form_diff * 0.25)
            factors.append(("form", form_probs, weights["form"], True))
        else:
            factors.append(("form", dict(_NEUTRAL_3WAY), weights["form"], False))

        # 4. Rest days (+ P1-F2 congestion / b2b soft penalty)
        rest_home = features.general.rest_days_home
        rest_away = features.general.rest_days_away
        b2b_home = bool(custom.get("b2b_home")) or (
            rest_home is not None and float(rest_home) <= 1.0
        )
        b2b_away = bool(custom.get("b2b_away")) or (
            rest_away is not None and float(rest_away) <= 1.0
        )
        if "schedule_congested_home" in custom:
            congest_home = bool(custom["schedule_congested_home"])
        else:
            congest_home = rest_home is not None and float(rest_home) <= 2.0
        if "schedule_congested_away" in custom:
            congest_away = bool(custom["schedule_congested_away"])
        else:
            congest_away = rest_away is not None and float(rest_away) <= 2.0
        if rest_home is not None and rest_away is not None:
            rest_diff = _clamp(float(rest_home) - float(rest_away), -4.0, 4.0)
            edge = rest_diff * 0.02
            # Back-to-back (rest <= 1): stronger than midweek congestion (rest <= 2)
            if b2b_home and not b2b_away:
                edge -= 0.03
            elif b2b_away and not b2b_home:
                edge += 0.03
            elif congest_home and not congest_away:
                edge -= 0.015
            elif congest_away and not congest_home:
                edge += 0.015
            rest_probs = _adjust_home_edge(_NEUTRAL_3WAY, edge)
            factors.append(("rest", rest_probs, weights["rest"], True))
        else:
            factors.append(("rest", dict(_NEUTRAL_3WAY), weights["rest"], False))

        # 5. Injury — higher impact means more damaged side (P1-F3: custom fallback)
        inj_home = features.player.injury_impact_home
        inj_away = features.player.injury_impact_away
        if inj_home is None:
            inj_home = custom.get("injury_impact_home")
        if inj_away is None:
            inj_away = custom.get("injury_impact_away")
        if inj_home is not None and inj_away is not None:
            # Positive (away more injured) → home advantage
            inj_diff = _clamp(inj_away - inj_home, -1.0, 1.0)
            injury_probs = _adjust_home_edge(_NEUTRAL_3WAY, inj_diff * 0.12)
            factors.append(("injury", injury_probs, weights["injury"], True))
        else:
            factors.append(
                ("injury", dict(_NEUTRAL_3WAY), weights["injury"], False),
            )

        # 6. H2H historical rates
        h2h_home = features.team.h2h_home_win_rate
        h2h_draw = features.team.h2h_draw_rate
        if h2h_home is not None and h2h_draw is not None:
            h2h_home, h2h_draw = _blend_h2h_venue(
                float(h2h_home), float(h2h_draw), custom,
            )
            h2h_away = max(0.0, 1.0 - h2h_home - h2h_draw)
            h2h_probs = _normalize_3way({
                "home_win": float(h2h_home),
                "draw": float(h2h_draw),
                "away_win": float(h2h_away),
            })
            factors.append(("h2h", h2h_probs, weights["h2h"], True))
        else:
            factors.append(("h2h", dict(_NEUTRAL_3WAY), weights["h2h"], False))

        # 7. Travel / timezone (P1-F7) — soft home edge when away traveled far
        travel_km = custom.get("travel_km_away")
        if travel_km is None:
            travel_km = features.general.travel_distance_km
        tz_off = custom.get("timezone_offset_hours_away")
        if travel_km is not None:
            try:
                from app.sports._shared.team_geo import travel_prob_home

                p_home_travel, travel_ok = travel_prob_home(
                    float(travel_km),
                    float(tz_off) if tz_off is not None else None,
                )
                if travel_ok:
                    # Convert binary-ish home bias into 3-way soft shift
                    travel_delta = _clamp((p_home_travel - 0.5) * 0.8, -0.04, 0.04)
                    travel_probs = _adjust_home_edge(_NEUTRAL_3WAY, travel_delta)
                    factors.append(("travel", travel_probs, weights.get("travel", 0.04), True))
                else:
                    factors.append(
                        ("travel", dict(_NEUTRAL_3WAY), weights.get("travel", 0.04), False),
                    )
            except Exception:  # noqa: BLE001
                factors.append(
                    ("travel", dict(_NEUTRAL_3WAY), weights.get("travel", 0.04), False),
                )
        else:
            factors.append(
                ("travel", dict(_NEUTRAL_3WAY), weights.get("travel", 0.04), False),
            )

        # 8a. Altitude soft (P1-F7) — high venue helps acclimated home side
        alt_ok = False
        try:
            alt = custom.get("venue_altitude_m")
            if alt is None:
                alt = custom.get("altitude_m")
            if alt is not None:
                alt_f = float(alt)
                # >1500m soft home edge; home assumed more acclimated
                if alt_f >= 1500:
                    edge = min(0.04, (alt_f - 1500) / 1500 * 0.04)
                    alt_probs = _adjust_home_edge(_NEUTRAL_3WAY, edge)
                    factors.append(
                        ("altitude", alt_probs, weights.get("altitude", 0.02), True),
                    )
                    alt_ok = True
        except (TypeError, ValueError):
            alt_ok = False

        # 8. xG soft (P1-F5) — custom.xg_* until true xG feed lands
        xg_h = custom.get("xg_home")
        xg_a = custom.get("xg_away")
        if xg_h is not None and xg_a is not None:
            try:
                xh, xa = float(xg_h), float(xg_a)
                total_xg = xh + xa
                if total_xg > 0:
                    share_h = xh / total_xg
                    xg_probs = _normalize_3way({
                        "home_win": 0.25 + share_h * 0.50,
                        "draw": 0.28,
                        "away_win": 0.25 + (1.0 - share_h) * 0.50,
                    })
                    factors.append(("xg", xg_probs, weights.get("xg", 0.07), True))
                else:
                    factors.append(
                        ("xg", dict(_NEUTRAL_3WAY), weights.get("xg", 0.07), False),
                    )
            except (TypeError, ValueError):
                factors.append(
                    ("xg", dict(_NEUTRAL_3WAY), weights.get("xg", 0.07), False),
                )
        else:
            factors.append(
                ("xg", dict(_NEUTRAL_3WAY), weights.get("xg", 0.07), False),
            )

        # 9. Market value soft (squad valuation differential)
        mv_h = getattr(features.team, "market_value_home", None)
        mv_a = getattr(features.team, "market_value_away", None)
        if mv_h is None:
            mv_h = custom.get("market_value_home")
        if mv_a is None:
            mv_a = custom.get("market_value_away")
        if mv_h is not None and mv_a is not None:
            try:
                vh, va = float(mv_h), float(mv_a)
                if vh > 0 and va > 0:
                    ratio = math.log(vh / va)
                    delta = _clamp(ratio * 0.04, -0.06, 0.06)
                    mv_probs = _adjust_home_edge(_NEUTRAL_3WAY, delta)
                    factors.append(
                        (
                            "market_value",
                            mv_probs,
                            weights.get("market_value", 0.04),
                            True,
                        ),
                    )
                else:
                    factors.append(
                        (
                            "market_value",
                            dict(_NEUTRAL_3WAY),
                            weights.get("market_value", 0.04),
                            False,
                        ),
                    )
            except (TypeError, ValueError):
                factors.append(
                    (
                        "market_value",
                        dict(_NEUTRAL_3WAY),
                        weights.get("market_value", 0.04),
                        False,
                    ),
                )
        else:
            factors.append(
                (
                    "market_value",
                    dict(_NEUTRAL_3WAY),
                    weights.get("market_value", 0.04),
                    False,
                ),
            )

        # 10. Possession / shots soft (P1-F6)
        poss_h = custom.get("possession_home")
        poss_a = custom.get("possession_away")
        shots_h = custom.get("shots_home")
        if shots_h is None:
            shots_h = custom.get("shots_on_target_home")
        shots_a = custom.get("shots_away")
        if shots_a is None:
            shots_a = custom.get("shots_on_target_away")
        poss_ok = False
        try:
            share = None
            if poss_h is not None and poss_a is not None:
                ph, pa = float(poss_h), float(poss_a)
                if ph > 1.5 or pa > 1.5:
                    ph, pa = ph / 100.0, pa / 100.0
                total_p = ph + pa
                if total_p > 0:
                    share = ph / total_p
            if share is None and shots_h is not None and shots_a is not None:
                sh, sa = float(shots_h), float(shots_a)
                total_s = sh + sa
                if total_s > 0:
                    share = sh / total_s
            # PPDA: lower is stronger press → invert to attack share proxy (P1-F6)
            if share is None:
                ppda_h = custom.get("ppda_home")
                ppda_a = custom.get("ppda_away")
                if ppda_h is not None and ppda_a is not None:
                    ph, pa = max(0.5, float(ppda_h)), max(0.5, float(ppda_a))
                    inv_h, inv_a = 1.0 / ph, 1.0 / pa
                    tot = inv_h + inv_a
                    if tot > 0:
                        share = inv_h / tot
            if share is not None:
                poss_probs = _normalize_3way({
                    "home_win": 0.28 + share * 0.40,
                    "draw": 0.30,
                    "away_win": 0.28 + (1.0 - share) * 0.40,
                })
                factors.append(
                    ("possession", poss_probs, weights.get("possession", 0.04), True),
                )
                poss_ok = True
        except (TypeError, ValueError):
            poss_ok = False
        if not poss_ok:
            factors.append(
                (
                    "possession",
                    dict(_NEUTRAL_3WAY),
                    weights.get("possession", 0.04),
                    False,
                ),
            )

        # 11. Referee soft (P1-F8) — custom-gated home bias
        ref_ok = False
        try:
            home_rate = custom.get("referee_home_win_rate")
            home_bias = custom.get("referee_home_bias")
            if home_rate is None and home_bias is not None:
                # bias in [-1, 1] → home win share around 0.5
                home_rate = 0.5 + 0.5 * float(home_bias)
            if home_rate is not None:
                hr = float(home_rate)
                if hr > 1.5:
                    hr = hr / 100.0
                hr = max(0.20, min(0.80, hr))
                # Soft 3-way: draw residual fixed; home/away share remaining
                draw_mass = 0.28
                remain = 1.0 - draw_mass
                ref_probs = _normalize_3way({
                    "home_win": remain * hr,
                    "draw": draw_mass,
                    "away_win": remain * (1.0 - hr),
                })
                factors.append(
                    ("referee", ref_probs, weights.get("referee", 0.02), True),
                )
                ref_ok = True
        except (TypeError, ValueError):
            ref_ok = False
        if not ref_ok:
            factors.append(
                (
                    "referee",
                    dict(_NEUTRAL_3WAY),
                    weights.get("referee", 0.02),
                    False,
                ),
            )

        # Weighted fusion with redistribution
        # Altitude is excluded from fusion and applied post-fusion as an
        # additive edge so it never dilutes other home-favoring factors.
        available = [(fid, p, w) for fid, p, w, ok in factors if ok and fid != "altitude"]
        total_w = sum(w for _, _, w in available)
        if total_w > 0:
            fused = {
                "home_win": 0.0,
                "draw": 0.0,
                "away_win": 0.0,
            }
            for _, probs, w in available:
                nw = w / total_w
                fused["home_win"] += probs["home_win"] * nw
                fused["draw"] += probs["draw"] * nw
                fused["away_win"] += probs["away_win"] * nw
            fused = {
                k: round(v, 4) for k, v in _normalize_3way(fused).items()
            }
        else:
            fused = {k: round(v, 4) for k, v in _NEUTRAL_3WAY.items()}

        # Post-fusion altitude additive edge (P1-F7)
        if alt_ok:
            for _fid, _probs, _w, _ok in factors:
                if _fid == "altitude" and _ok:
                    alt_edge = _probs["home_win"] - _NEUTRAL_3WAY["home_win"]
                    fused = _normalize_3way(_adjust_home_edge(fused, alt_edge))
                    fused = {k: round(v, 4) for k, v in fused.items()}
                    break

        scores = _probabilities_to_scores(fused)

        explanation: list[ContributionItem] = []
        for fid, probs, w, ok in factors:
            if fid == "odds" and ok:
                detail = (
                    f"H={probs['home_win']:.3f} D={probs['draw']:.3f} "
                    f"A={probs['away_win']:.3f}; {odds_quality_note}"
                )
            elif ok:
                detail = (
                    f"H={probs['home_win']:.3f} D={probs['draw']:.3f} "
                    f"A={probs['away_win']:.3f}"
                )
            else:
                detail = f"{fid} unavailable"
            explanation.append(ContributionItem(
                factor=fid,
                direction="support" if ok else "neutral",
                weight=w,
                available=ok,
                detail=detail,
                predicted_outcome=_predicted_outcome(probs) if ok else None,
            ))

        confidence = compute_confidence(
            fused,
            available_flags=[ok for _, _, _, ok in factors],
            predicted_outcomes=[
                _predicted_outcome(p) if ok else None for _, p, _, ok in factors
            ],
            data_quality=features.data_quality,
            odds_fresh=bool(features.market.odds_fresh) if odds_h else None,
            custom=features.custom if isinstance(features.custom, dict) else None,
        )

        conf_break = confidence_breakdown(
            fused,
            available_flags=[ok for _, _, _, ok in factors],
            predicted_outcomes=[_predicted_outcome(p) if ok else None for _, p, _, ok in factors],
            data_quality=features.data_quality,
            odds_fresh=bool(features.market.odds_fresh) if odds_h else None,
            custom=features.custom if isinstance(features.custom, dict) else None,
        )

        return PredictionResult(
            predicted_scores=scores,
            outcome_probabilities=fused,
            confidence=confidence,
            engine_name="football_multi_factor",
            explanation=explanation,
            betting_analysis={
                "confidence_breakdown": conf_break,
                "soft_totals_btts": soft_totals_btts_analysis(scores),
            },
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
