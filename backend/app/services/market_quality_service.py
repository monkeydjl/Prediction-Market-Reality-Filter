"""Market quality service (Phase 2: Market Quality Layer).

Pure-function layer that scores market feasibility (spread, liquidity,
volume, staleness) and produces a ``market_quality`` overlay block.

Only computed for events whose ``source.type == "prediction_market"``
(Polymarket, Kalshi). For other source types (``prediction_question``,
``open_web``, ``sports_event``, ``manual``), the block is omitted entirely
— mirroring the ``freeze_prediction`` market-gated convention.

This is an audit/overlay layer. It MUST NOT feed back into
``ai_probability``, ``evidence_profile``, ``regression_to_market``,
``actionable_recommendation``, or ``decision_quality``. The data flow is
one-way:

    actionable_recommendation + market fields
      -> build_market_quality
      -> market_quality (overlay only, no writeback)

The function is synchronous and deterministic — no LLM calls, no I/O.
``settings`` is intentionally not passed; the orchestrator extracts concrete
scalar config values and passes them explicitly.

Field availability varies per adapter (see
``docs/superpowers/audits/market-quality-field-audit.md``):
- ``market_quote`` (bid/ask/spread): only Kalshi, often 0,0,0 when
  last_price is available. Polymarket/Metaculus/manual: ``None``.
- ``volume`` / ``liquidity``: Polymarket/Kalshi have real values.
  Metaculus has ``liquidity=0.0`` but is excluded from this service anyway.
- ``last_updated``: unavailable across all adapters. ``stale_price_flag``
  is always ``None`` (unknown) in Phase 2.

When a sub-score's input is missing, the sub-score is recorded as ``None``
(unknown) and excluded from the overall score average. The service never
raises — missing fields degrade per-sub-score, not as a whole-response
failure.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_STRONG_DIRECTIONS = ("YES", "NO")


def build_market_quality(
    *,
    recommendation: dict[str, Any] | None,
    source: dict[str, Any] | None,
    market_quote: dict[str, Any] | None,
    volume: float | None,
    liquidity: float | None,
    max_spread_pct: float,
    min_liquidity: float,
    min_volume: float,
    score_threshold: float,
) -> dict[str, Any] | None:
    """Build the market_quality overlay block.

    Returns ``None`` when the event is not a ``prediction_market`` source
    (Metaculus / manual / sports_event / open_web). Caller attaches the
    block to the record only when non-None.

    Pure function: does not mutate inputs. Never raises — missing fields
    produce unknown sub-scores, not exceptions.
    """
    if not _is_prediction_market(source):
        return None

    raw_direction = _extract_direction(recommendation)

    spread_penalty = _compute_spread_penalty(market_quote, max_spread_pct)
    wide_spread_flag = _is_wide_spread(market_quote, max_spread_pct)
    liquidity_score = _compute_liquidity_score(liquidity, min_liquidity)
    volume_score = _compute_volume_score(volume, min_volume)
    # stale_price_flag: always None (unknown) in Phase 2 — no adapter
    # exposes last_updated. See market-quality-field-audit.md.
    stale_price_flag: bool | None = None

    thin_market_flag = _is_thin_market(liquidity, volume, min_liquidity, min_volume)

    score = _aggregate_score(spread_penalty, liquidity_score, volume_score)

    suggested_direction, downgrade_reason = _apply_market_downgrade(
        raw_direction, score, score_threshold, wide_spread_flag
    )

    return {
        "score": round(score, 4),
        "liquidity_score": _round_or_none(liquidity_score),
        "volume_score": _round_or_none(volume_score),
        "spread_penalty": _round_or_none(spread_penalty),
        "wide_spread_flag": wide_spread_flag,
        "thin_market_flag": thin_market_flag,
        "stale_price_flag": stale_price_flag,
        "downgrade_reason": downgrade_reason,
        "raw_direction": raw_direction,
        "suggested_direction": suggested_direction,
        "downgraded": suggested_direction != raw_direction,
        "applied_to_displayed_direction": False,  # set by merge step
    }


def _is_prediction_market(source: dict[str, Any] | None) -> bool:
    if not isinstance(source, dict):
        return False
    return source.get("type") == "prediction_market"


def _extract_direction(recommendation: dict[str, Any] | None) -> str:
    if not isinstance(recommendation, dict):
        return "WAIT"
    direction = recommendation.get("direction")
    if direction in ("YES", "NO", "WAIT", "AVOID"):
        return direction
    return "WAIT"


def _compute_spread_penalty(
    market_quote: dict[str, Any] | None,
    max_spread_pct: float,
) -> float | None:
    """Spread penalty in [0, 1]. 0 = no penalty (tight spread), 1 = max
    penalty. Returns None when market_quote is missing or spread is 0
    (placeholder). Penalty = spread / 100, capped at 1.0.

    Note: ``max_spread_pct`` is NOT used here — the penalty is a continuous
    signal fed into the aggregate score. The hard cutoff (spread >
    max_spread_pct -> wide_spread_flag) is handled by ``_is_wide_spread``
    and forces a downgrade independently of the aggregate score. This
    prevents healthy liquidity/volume from masking an untradeable spread.
    """
    if not isinstance(market_quote, dict):
        return None
    spread = market_quote.get("spread")
    if not isinstance(spread, (int, float)) or spread <= 0:
        return None
    # spread is in 0-100 scale (same as probability). Penalty proportional.
    penalty = min(float(spread) / 100.0, 1.0)
    return penalty


def _is_wide_spread(
    market_quote: dict[str, Any] | None,
    max_spread_pct: float,
) -> bool:
    """Hard cutoff: True when spread exceeds ``max_spread_pct``. A wide
    spread means the quoted price is untradeable (bid-ask gap too large),
    so the market quality MUST downgrade regardless of the aggregate score.
    Returns False when market_quote is missing or spread is 0 (unknown)."""
    if not isinstance(market_quote, dict):
        return False
    spread = market_quote.get("spread")
    if not isinstance(spread, (int, float)) or spread <= 0:
        return False
    return float(spread) > max_spread_pct


def _compute_liquidity_score(
    liquidity: float | None,
    min_liquidity: float,
) -> float | None:
    """Liquidity score in [0, 1]. 1.0 = at or above min_liquidity; scales
    down to 0 as liquidity approaches 0. Returns None when liquidity is
    None (unknown)."""
    if liquidity is None:
        return None
    try:
        liq = float(liquidity)
    except (TypeError, ValueError):
        return None
    if liq < 0:
        return None
    if min_liquidity <= 0:
        return 1.0
    if liq >= min_liquidity:
        return 1.0
    return liq / min_liquidity


def _compute_volume_score(
    volume: float | None,
    min_volume: float,
) -> float | None:
    """Volume score in [0, 1]. Same scaling as liquidity. None when unknown."""
    if volume is None:
        return None
    try:
        vol = float(volume)
    except (TypeError, ValueError):
        return None
    if vol < 0:
        return None
    if min_volume <= 0:
        return 1.0
    if vol >= min_volume:
        return 1.0
    return vol / min_volume


def _is_thin_market(
    liquidity: float | None,
    volume: float | None,
    min_liquidity: float,
    min_volume: float,
) -> bool:
    """Thin market when EITHER liquidity or volume is below its minimum
    (and the value is known — None does not trigger)."""
    if liquidity is not None:
        try:
            if float(liquidity) < min_liquidity:
                return True
        except (TypeError, ValueError):
            pass
    if volume is not None:
        try:
            if float(volume) < min_volume:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _aggregate_score(
    spread_penalty: float | None,
    liquidity_score: float | None,
    volume_score: float | None,
) -> float:
    """Average of available sub-scores. spread_penalty reduces the score
    (higher penalty = lower quality); liquidity/volume scores raise it
    (higher = better). Missing sub-scores are excluded from the average."""
    components: list[float] = []
    if spread_penalty is not None:
        components.append(1.0 - spread_penalty)
    if liquidity_score is not None:
        components.append(liquidity_score)
    if volume_score is not None:
        components.append(volume_score)
    if not components:
        # No market data at all — neutral score (neither good nor bad).
        # Does not trigger downgrade by itself.
        return 1.0
    return sum(components) / len(components)


def _apply_market_downgrade(
    raw_direction: str,
    score: float,
    score_threshold: float,
    wide_spread_flag: bool = False,
) -> tuple[str, str | None]:
    """Downgrade strong directions (YES/NO) to WAIT when market quality is
    below threshold OR when the spread is untradeable (wide_spread_flag).
    WAIT/AVOID are never downgraded by market quality.

    The wide_spread_flag is a hard cutoff: even when liquidity and volume are
    healthy (keeping the aggregate score above threshold), an untradeable
    spread makes the market effectively unusable and MUST trigger a downgrade.
    """
    if raw_direction not in _STRONG_DIRECTIONS:
        return raw_direction, None
    if wide_spread_flag:
        return "WAIT", "价差过大（无法交易），降级为 WAIT。"
    if score < score_threshold:
        return "WAIT", "市场质量不足（流动性低或价差过大），降级为 WAIT。"
    return raw_direction, None


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)


def merge_quality_overlays(
    decision_quality: dict[str, Any] | None,
    market_quality: dict[str, Any] | None,
    source_reliability: dict[str, Any] | None = None,
) -> tuple[str | None, str | None, bool, bool]:
    """Merge decision_quality, market_quality, and source_reliability overlays
    using most-strict direction wins. Returns (final_displayed_direction,
    final_downgrade_reason, market_applied, source_applied).

    Parallel + most-strict semantics per spec § Downgrade Chain:
    - All three computed independently; none reads the others' output.
    - severity_rank: YES/NO=0, WAIT=1, AVOID=2
    - The direction with HIGHER severity wins.
    - When multiple overlays share the max severity (>0), their reasons are
      concatenated with " | " in order: dq | mq | sr.

    Returns ``(None, None, False, False)`` when no overlay is present
    (all features off). ``source_reliability`` defaults to None for backward
    compatibility with Phase 2 callers that pass only 2 overlays — in that
    case ``source_applied`` is always False.

    ``market_applied`` / ``source_applied`` are True when that overlay's
    suggested_direction is the strictest (unique max) OR when it contributed
    a reason at a tied max severity. Used by the caller to set
    ``applied_to_displayed_direction`` on each overlay block. decision_quality
    never gets an ``applied`` flag because it is the base layer (its
    ``displayed_direction`` is the starting point, not a downgrade suggestion).
    """
    severity = {"YES": 0, "NO": 0, "WAIT": 1, "AVOID": 2}

    # Collect (direction, reason) for each present overlay, in fixed order:
    # decision_quality, market_quality, source_reliability.
    overlays: list[tuple[str | None, str | None]] = []
    if decision_quality is not None:
        overlays.append(
            (decision_quality.get("displayed_direction"),
             decision_quality.get("downgrade_reason"))
        )
    if market_quality is not None:
        overlays.append(
            (market_quality.get("suggested_direction"),
             market_quality.get("downgrade_reason"))
        )
    if source_reliability is not None:
        overlays.append(
            (source_reliability.get("suggested_direction"),
             source_reliability.get("downgrade_reason"))
        )

    # Filter to overlays that actually have a direction.
    present = [(d, r) for d, r in overlays if d is not None]
    if not present:
        return None, None, False, False

    # Compute severities; max_sev drives the final direction.
    sev_list = [severity.get(d, 0) for d, _ in present]
    max_sev = max(sev_list)

    if max_sev == 0:
        # No overlay downgraded — pick the first present direction (no reason).
        final_direction = present[0][0]
        final_reason = None
    else:
        # Downgrade — pick the direction at max_sev; concatenate reasons of
        # all overlays at max_sev, in submission order.
        max_pairs = [(d, r) for (d, r), s in zip(present, sev_list) if s == max_sev]
        final_direction = max_pairs[0][0]
        reasons = [r for _, r in max_pairs if r]
        final_reason = " | ".join(reasons) if reasons else None

    # Determine which overlay "applied" (changed the final direction).
    # An overlay "applied" when it made the final direction stricter than
    # the decision_quality base layer. Requires decision_quality to be
    # present (no base = nothing to override). This mirrors the Phase 2
    # 2-way semantics: when only market_quality is present, market_applied
    # is False because there is no base to apply on top of.
    unique_max = sum(1 for s in sev_list if s == max_sev) == 1

    market_applied = False
    source_applied = False
    if max_sev > 0 and decision_quality is not None:
        # Re-walk the original (ordered) overlays to flag market/source.
        # decision_quality is overlays[0] when present; mq/sr follow.
        idx = 1  # skip decision_quality (the base, never "applied")
        if market_quality is not None:
            d, r = overlays[idx]
            mq_sev = severity.get(d, 0) if d is not None else 0
            if mq_sev == max_sev:
                market_applied = True if unique_max else bool(r)
            idx += 1
        if source_reliability is not None:
            d, r = overlays[idx]
            sr_sev = severity.get(d, 0) if d is not None else 0
            if sr_sev == max_sev:
                source_applied = True if unique_max else bool(r)

    return final_direction, final_reason, market_applied, source_applied
