"""Richer confidence scores for Kernel prediction engines (P1-X1).

Replaces pure ``max(p) * 0.95`` with a blend of:
1. Decision strength — how peaky the outcome distribution is
2. Data completeness — share of expected factor inputs present
3. Factor agreement — whether major factors point the same way
4. Optional market quality — stale / thin odds damp confidence

Output is always in [0.20, 0.95].
"""
from __future__ import annotations

from typing import Any, Sequence


def _clamp(x: float, lo: float = 0.20, hi: float = 0.95) -> float:
    return max(lo, min(hi, x))


def decision_strength(probs: dict[str, float]) -> float:
    """Flat → 0.0 useful signal; certain → 1.0. Baseline is read off the arity.

    "Flat" is ``1/n`` for an ``n``-outcome distribution: 1/3 for football's
    3-way, **1/2 for the binary home/away sports**. The baseline used to be the
    literal ``1/3``, so a coin-flip binary call scored 0.25 instead of 0.0 --
    the bottom quarter of the scale was unreachable for
    ``basketball``/``baseball``/``hockey`` (three of the five engines that call
    this), which inflated their confidence by up to 0.0813 and never by less
    than ~0.042 on any real fixture, because the factor clamps cap a binary
    fused peak at ~0.74/0.65/0.72.

    The divisor is written ``(n - 1) / n`` rather than ``1 - 1/n`` so that
    ``n == 3`` reproduces the previous ``2.0 / 3.0`` bit-for-bit: football's
    values are unchanged, verified over the whole 5,151-point simplex grid at
    a 0.01 step (see ``tests/test_confidence_decision_arity.py``).
    """
    if not probs:
        return 0.0
    vals = [max(0.0, float(v)) for v in probs.values()]
    total = sum(vals) or 1.0
    norm = [v / total for v in vals]
    peak = max(norm)
    n = len(norm)
    if n < 2:
        # A single-outcome distribution carries no alternative to be unsure
        # about, and 1 - 1/1 == 0 would divide by zero.
        return 1.0
    baseline = 1.0 / n
    return max(0.0, min(1.0, (peak - baseline) / ((n - 1.0) / n)))


def data_completeness(
    available_flags: Sequence[bool],
    *,
    quality: str | None = None,
) -> float:
    """Fraction of factors available, nudged by FeatureSet.data_quality."""
    if not available_flags:
        base = 0.5
    else:
        base = sum(1 for a in available_flags if a) / len(available_flags)
    q = (quality or "").lower()
    if q == "real":
        base = min(1.0, base + 0.05)
    elif q in {"partial", "degraded"}:
        base = max(0.0, base - 0.10)
    elif q in {"stub", "synthetic", "mock"}:
        base = max(0.0, base - 0.20)
    return base


def factor_agreement(
    predicted_outcomes: Sequence[str | None],
    *,
    final_outcome: str | None = None,
) -> float:
    """Share of available factor heads that match the plurality / final pick."""
    votes = [p for p in predicted_outcomes if p]
    if not votes:
        return 0.5
    if final_outcome is None:
        # plurality
        counts: dict[str, int] = {}
        for v in votes:
            counts[v] = counts.get(v, 0) + 1
        final_outcome = max(counts, key=counts.get)  # type: ignore[arg-type]
    agree = sum(1 for v in votes if v == final_outcome)
    return agree / len(votes)


def market_quality_damp(
    *,
    odds_fresh: bool | None = None,
    liquidity_factor: float | None = None,
    odds_dispersion: float | None = None,
) -> float:
    """Multiplicative damp in (0.75, 1.0] from market hygiene signals."""
    damp = 1.0
    if odds_fresh is False:
        damp *= 0.92
    if liquidity_factor is not None:
        try:
            liq = float(liquidity_factor)
            if 0.0 <= liq < 0.5:
                damp *= 0.90 + 0.10 * (liq / 0.5)
        except (TypeError, ValueError):
            pass
    # odds_dispersion: std of implied probs across books; higher → less trust
    if odds_dispersion is not None:
        try:
            d = float(odds_dispersion)
            if d >= 0.08:
                damp *= 0.88
            elif d >= 0.04:
                damp *= 0.94
        except (TypeError, ValueError):
            pass
    return max(0.75, min(1.0, damp))


def compute_confidence(
    probs: dict[str, float],
    *,
    available_flags: Sequence[bool] | None = None,
    predicted_outcomes: Sequence[str | None] | None = None,
    data_quality: str | None = None,
    odds_fresh: bool | None = None,
    custom: dict[str, Any] | None = None,
) -> float:
    """Blend decision strength, completeness, agreement, market damp."""
    strength = decision_strength(probs)
    completeness = data_completeness(available_flags or [], quality=data_quality)
    final = max(probs, key=probs.get) if probs else None  # type: ignore[arg-type]
    agreement = factor_agreement(predicted_outcomes or [], final_outcome=final)

    custom = custom or {}
    liq = custom.get("liquidity_factor")
    try:
        liq_f = float(liq) if liq is not None else None
    except (TypeError, ValueError):
        liq_f = None
    disp = custom.get("odds_dispersion")
    try:
        disp_f = float(disp) if disp is not None else None
    except (TypeError, ValueError):
        disp_f = None

    damp = market_quality_damp(
        odds_fresh=odds_fresh,
        liquidity_factor=liq_f,
        odds_dispersion=disp_f,
    )

    # Weights: strength dominates, completeness & agreement refine
    raw = (
        0.50 * strength
        + 0.25 * completeness
        + 0.25 * agreement
    ) * damp
    # Map 0..1 into a readable confidence band starting ~0.35 for flat cases
    scaled = 0.30 + 0.65 * raw
    return round(_clamp(scaled), 4)



def confidence_breakdown(
    probs: dict[str, float],
    *,
    available_flags: Sequence[bool] | None = None,
    predicted_outcomes: Sequence[str | None] | None = None,
    data_quality: str | None = None,
    odds_fresh: bool | None = None,
    custom: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured diagnostics for FE SportConfidencePanel (P1-X3)."""
    flags = list(available_flags or [])
    outcomes = list(predicted_outcomes or [])
    strength = decision_strength(probs)
    completeness = data_completeness(flags, quality=data_quality)
    final = max(probs, key=probs.get) if probs else None  # type: ignore[arg-type]
    agreement = factor_agreement(outcomes, final_outcome=final)

    custom = custom or {}
    liq = custom.get("liquidity_factor")
    try:
        liq_f = float(liq) if liq is not None else None
    except (TypeError, ValueError):
        liq_f = None
    disp = custom.get("odds_dispersion")
    try:
        disp_f = float(disp) if disp is not None else None
    except (TypeError, ValueError):
        disp_f = None

    damp = market_quality_damp(
        odds_fresh=odds_fresh,
        liquidity_factor=liq_f,
        odds_dispersion=disp_f,
    )
    total = compute_confidence(
        probs,
        available_flags=flags,
        predicted_outcomes=outcomes,
        data_quality=data_quality,
        odds_fresh=odds_fresh,
        custom=custom,
    )
    available_n = sum(1 for a in flags if a)
    return {
        "total": total,
        "decision_strength": round(strength, 4),
        "data_completeness": round(completeness, 4),
        "factor_agreement": round(agreement, 4),
        "market_damp": round(damp, 4),
        "factors_available": available_n,
        "factors_total": len(flags),
        "final_outcome": final,
        "data_quality": data_quality,
        "odds_fresh": odds_fresh,
    }
