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

from datetime import datetime, timezone
from typing import TYPE_CHECKING, TypedDict

from app.kernel.domain import (
    FeatureSet, MatchIdentity, PredictionResult, ContributionItem,
)
from app.kernel.engines.btd_model import calculate_btd_probabilities
from app.kernel.engines.confidence import compute_confidence, confidence_breakdown
from app.kernel.engines.odds_quality import (
    describe_odds_quality,
    odds_weight_multiplier,
)

if TYPE_CHECKING:
    from app.kernel.factor_registry import FactorRegistry

# Whitelist of known knockout stage names. Matches the legacy pipeline's
# ``_KNOCKOUT_STAGES`` set. Unknown/empty stages default to False
# (non-knockout), which is the safe default for group-stage-heavy tournaments.
_KNOCKOUT_STAGES = frozenset({
    "round_of_16", "quarterfinal", "quarter_final",
    "semifinal", "semi_final", "final",
})


class _ConfidenceKwargs(TypedDict):
    """Shared keyword arguments for ``compute_confidence`` /
    ``confidence_breakdown``.

    Both take the same keywords, so ``predict`` builds them once and unpacks
    them twice. A plain ``dict(...)`` here infers ``dict[str, object]``, which
    makes every unpacked argument unverifiable at both call sites; a TypedDict
    keeps the single-build convenience and still gets checked.
    """

    available_flags: list[bool]
    predicted_outcomes: list[str | None]
    data_quality: str
    odds_fresh: bool | None
    custom: dict[str, float] | None


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
        # Return a fresh dict with rounded values rather than aliasing the
        # caller's ``elo_probs``. ``round(v, 4)`` mirrors the with-odds path
        # so the no-odds output is consistently rounded to 4 decimals even if
        # the upstream BTD result ever changes its rounding behaviour.
        return {k: round(v, 4) for k, v in elo_probs.items()}
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



def soft_totals_btts_analysis(
    scores: dict[str, float],
    *,
    line: float = 2.5,
) -> dict:
    """Soft O/U + BTTS from independent Poisson goals (P1-O1 scaffolding).

    Not a full multi-market engine — exposes diagnostic probs for FE/API until
    dedicated totals/BTTS markets and odds feeds land.
    """
    import math

    try:
        lh = max(0.05, float(scores.get("home", 1.2)))
        la = max(0.05, float(scores.get("away", 1.2)))
    except (TypeError, ValueError):
        return {"available": False}

    # P(total > line) via discrete Poisson sum 0..10 each side
    def pois_pmf(k: int, lam: float) -> float:
        return math.exp(-lam) * lam ** k / math.factorial(k)

    max_g = 10
    p_over = 0.0
    p_btts = 0.0
    p_home0 = 0.0
    p_away0 = 0.0
    for h in range(0, max_g + 1):
        ph = pois_pmf(h, lh)
        if h == 0:
            p_home0 = ph
        for a in range(0, max_g + 1):
            pa = pois_pmf(a, la)
            if a == 0 and h == 0:
                p_away0 = pa  # will fix below
            joint = ph * pa
            if h + a > line:
                p_over += joint
            if h >= 1 and a >= 1:
                p_btts += joint
    # recompute zero goals properly
    p_home0 = pois_pmf(0, lh)
    p_away0 = pois_pmf(0, la)
    p_btts = 1.0 - p_home0 - p_away0 + p_home0 * p_away0  # inclusion

    # normalize residual mass from truncation
    # (sums are slightly < 1 due to max_g cutoff — fine for soft)
    p_over = max(0.0, min(1.0, p_over))
    p_under = max(0.0, min(1.0, 1.0 - p_over))
    p_btts = max(0.0, min(1.0, p_btts))
    return {
        "available": True,
        "line": line,
        "expected_home_goals": round(lh, 3),
        "expected_away_goals": round(la, 3),
        "expected_total": round(lh + la, 3),
        "p_over": round(p_over, 4),
        "p_under": round(p_under, 4),
        "p_btts_yes": round(p_btts, 4),
        "p_btts_no": round(1.0 - p_btts, 4),
        "note": "soft independent Poisson; not calibrated multi-market prices",
    }



def soft_totals_from_scores(
    scores: dict[str, float],
    *,
    line: float,
    sport: str = "generic",
    max_g: int = 30,
) -> dict:
    """Independent Poisson O/U (and BTTS only for football-like low totals)."""
    base = soft_totals_btts_analysis(scores, line=line)
    if not base.get("available"):
        return base
    out = dict(base)
    out["sport"] = sport
    # BTTS only meaningful for football-scale scoring
    if sport not in {"football", "soccer", "hockey"}:
        out.pop("p_btts_yes", None)
        out.pop("p_btts_no", None)
        out["note"] = f"soft independent Poisson O/U for {sport}; not market prices"
    else:
        out["note"] = base.get("note", "soft independent Poisson")
    return out


def _calculate_confidence(probs: dict[str, float]) -> float:
    """Confidence = max probability, slightly deflated."""
    max_prob = max(probs.values())
    return round(min(max_prob * 0.95, 0.95), 4)


class EloOddsEngine:
    """Elo + Odds fusion engine. Implements PredictionEngine Protocol."""

    def __init__(self, factor_registry: FactorRegistry | None = None) -> None:
        self._factor_registry = factor_registry

    def name(self) -> str:
        return "elo_odds"

    def supported_sports(self) -> list[str]:
        return ["*"]

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult:
        elo_home = features.team.elo_rating_home
        elo_away = features.team.elo_rating_away
        is_knockout = (match.stage or "").lower().strip() in _KNOCKOUT_STAGES

        # Get weights from FactorRegistry or fall back to defaults
        if self._factor_registry:
            elo_w = self._factor_registry.get_weight("elo", match.season.competition.code)
            odds_w = self._factor_registry.get_weight("odds", match.season.competition.code)
        else:
            elo_w, odds_w = 0.30, 0.70

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
            # P1-E4: damp odds weight when book is thin/stale/overrounded
            odds_mult = odds_weight_multiplier(
                odds_h,
                odds_d,
                odds_a,
                odds_fresh=bool(features.market.odds_fresh),
                custom=features.custom,
            )
            odds_w = odds_w * odds_mult
        else:
            market_probs = None
            odds_available = False

        # Fuse
        fused = _fuse_elo_and_odds(elo_probs, market_probs, elo_w, odds_w)
        scores = _probabilities_to_scores(fused)

        # Explanation with predicted_outcome. `key=probs.get` is an overloaded
        # bound method a checker cannot match against max()'s key callable;
        # indexing is the same lookup and is checkable.
        elo_predicted = (
            max(elo_probs, key=lambda k: elo_probs[k]) if elo_available else None
        )
        odds_predicted = (
            max(market_probs, key=lambda k: market_probs[k])
            if market_probs is not None
            else None
        )
        odds_detail = (
            f"Odds {odds_h}/{odds_d}/{odds_a}; "
            f"{describe_odds_quality(odds_h, odds_d, odds_a, odds_fresh=bool(features.market.odds_fresh), custom=features.custom)}"
            if odds_available
            else "Odds unavailable"
        )

        explanation = [
            ContributionItem(
                factor="elo", direction="support" if elo_available else "neutral",
                weight=elo_w, available=elo_available,
                detail=f"Elo {elo_home} vs {elo_away}" if elo_available else "Elo unavailable",
                predicted_outcome=elo_predicted,
            ),
            ContributionItem(
                factor="odds", direction="support" if odds_available else "neutral",
                weight=odds_w, available=odds_available,
                detail=odds_detail,
                predicted_outcome=odds_predicted,
            ),
        ]

        conf_kwargs: _ConfidenceKwargs = {
            "available_flags": [elo_available, odds_available],
            "predicted_outcomes": [elo_predicted, odds_predicted],
            "data_quality": features.data_quality,
            "odds_fresh": bool(features.market.odds_fresh) if odds_available else None,
            "custom": features.custom if isinstance(features.custom, dict) else None,
        }
        confidence = compute_confidence(fused, **conf_kwargs)
        conf_break = confidence_breakdown(fused, **conf_kwargs)

        return PredictionResult(
            predicted_scores=scores,
            outcome_probabilities=fused,
            confidence=confidence,
            engine_name="elo_odds",
            explanation=explanation,
            betting_analysis={
                "confidence_breakdown": conf_break,
                "soft_totals_btts": soft_totals_btts_analysis(scores),
            },
            feature_version=features.feature_version,
            prediction_timestamp=datetime.now(timezone.utc),
        )
