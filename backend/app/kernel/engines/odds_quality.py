"""Market odds quality → odds weight multiplier (P1-E4).

When the book is thin, stale, or heavily overrounded, prediction engines
should lean less on the market leg and more on model factors (Elo, form…).

Inputs available without expanding MarketFeatures:
- decimal 1x2 odds → overround
- ``odds_fresh`` flag on MarketFeatures
- optional ``custom["liquidity_factor"]`` or ``custom["market_liquidity"]``
  in [0, 1] (1 = deep/trustworthy)

Multiplier is multiplicative on the odds factor weight (0.15 … 1.0).
"""
from __future__ import annotations

from typing import Any


def decimal_overround(
    odds_home: float,
    odds_draw: float,
    odds_away: float,
) -> float | None:
    """Sum of implied probs before normalization. Fair ~1.02–1.08."""
    if min(odds_home, odds_draw, odds_away) <= 1.0:
        return None
    try:
        return (1.0 / odds_home) + (1.0 / odds_draw) + (1.0 / odds_away)
    except (TypeError, ZeroDivisionError, ValueError):
        return None


def _liquidity_from_custom(custom: dict[str, Any] | None) -> float | None:
    if not custom:
        return None
    for key in ("liquidity_factor", "market_liquidity", "liquidity"):
        raw = custom.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if 0.0 <= val <= 1.0:
            return val
        # Allow dollar-like liquidity: map log-ish into (0,1]
        if val > 1.0:
            # $1k → ~0.5, $50k → ~0.9, $500k+ → ~1.0
            import math

            return max(0.15, min(1.0, math.log10(val + 10) / 5.5))
    return None


def odds_weight_multiplier(
    odds_home: float | None,
    odds_draw: float | None,
    odds_away: float | None,
    *,
    odds_fresh: bool = True,
    custom: dict[str, Any] | None = None,
    min_mult: float = 0.15,
) -> float:
    """Return factor in [min_mult, 1.0] to scale the odds fusion weight."""
    if (
        odds_home is None
        or odds_draw is None
        or odds_away is None
        or odds_home <= 1.0
        or odds_draw <= 1.0
        or odds_away <= 1.0
    ):
        return min_mult

    mult = 1.0
    reasons_penalty = 0.0

    if not odds_fresh:
        reasons_penalty += 0.35

    overround = decimal_overround(float(odds_home), float(odds_draw), float(odds_away))
    if overround is not None:
        # Soft books often sit 1.05–1.12; junk/thin can be 1.20+
        if overround >= 1.25:
            reasons_penalty += 0.45
        elif overround >= 1.15:
            reasons_penalty += 0.28
        elif overround >= 1.10:
            reasons_penalty += 0.12
        elif overround < 1.0:
            # Impossible book — treat as broken feed
            reasons_penalty += 0.50

    liq = _liquidity_from_custom(custom)
    if liq is not None:
        # Full trust at 1.0; at 0.0 subtract another 0.40
        reasons_penalty += (1.0 - liq) * 0.40

    mult = 1.0 - reasons_penalty
    return max(min_mult, min(1.0, round(mult, 4)))


def odds_dispersion_from_books(
    books: list[dict[str, Any]] | None,
) -> float | None:
    """Std-dev of home implied probs across books (for confidence damp).

    Each book: {odds_home, odds_draw?, odds_away?} or {home, draw, away}.
    Returns None if fewer than 2 valid books.
    """
    if not books or len(books) < 2:
        return None
    implied: list[float] = []
    for b in books:
        if not isinstance(b, dict):
            continue
        h = b.get("odds_home", b.get("home"))
        try:
            hf = float(h)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if hf <= 1.0:
            continue
        implied.append(1.0 / hf)
    if len(implied) < 2:
        return None
    mean = sum(implied) / len(implied)
    var = sum((x - mean) ** 2 for x in implied) / len(implied)
    return round(var ** 0.5, 4)


def inject_odds_dispersion(custom: dict[str, Any] | None, books: list[dict[str, Any]] | None) -> dict[str, Any]:
    out = dict(custom or {})
    if out.get("odds_dispersion") is not None:
        return out
    disp = odds_dispersion_from_books(books)
    if disp is not None:
        out["odds_dispersion"] = disp
    return out


def describe_odds_quality(
    odds_home: float | None,
    odds_draw: float | None,
    odds_away: float | None,
    *,
    odds_fresh: bool = True,
    custom: dict[str, Any] | None = None,
) -> str:
    """Short human-readable quality note for ContributionItem.detail."""
    if odds_home is None or odds_draw is None or odds_away is None:
        return "odds missing"
    mult = odds_weight_multiplier(
        odds_home, odds_draw, odds_away, odds_fresh=odds_fresh, custom=custom,
    )
    parts = [f"odds_mult={mult:.2f}"]
    if not odds_fresh:
        parts.append("stale")
    ov = decimal_overround(float(odds_home), float(odds_draw), float(odds_away))
    if ov is not None:
        parts.append(f"overround={ov:.3f}")
    liq = _liquidity_from_custom(custom)
    if liq is not None:
        parts.append(f"liq={liq:.2f}")
    return " ".join(parts)
