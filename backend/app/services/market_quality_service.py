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
    liquidity_score = _compute_liquidity_score(liquidity, min_liquidity)
    volume_score = _compute_volume_score(volume, min_volume)
    # stale_price_flag: always None (unknown) in Phase 2 — no adapter
    # exposes last_updated. See market-quality-field-audit.md.
    stale_price_flag: bool | None = None

    thin_market_flag = _is_thin_market(liquidity, volume, min_liquidity, min_volume)

    score = _aggregate_score(spread_penalty, liquidity_score, volume_score)

    suggested_direction, downgrade_reason = _apply_market_downgrade(
        raw_direction, score, score_threshold
    )

    return {
        "score": round(score, 4),
        "liquidity_score": _round_or_none(liquidity_score),
        "volume_score": _round_or_none(volume_score),
        "spread_penalty": _round_or_none(spread_penalty),
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
    (placeholder). Penalty = spread / 100, capped at 1.0; downgrades only
    when spread > max_spread_pct."""
    if not isinstance(market_quote, dict):
        return None
    spread = market_quote.get("spread")
    if not isinstance(spread, (int, float)) or spread <= 0:
        return None
    # spread is in 0-100 scale (same as probability). Penalty proportional.
    penalty = min(float(spread) / 100.0, 1.0)
    return penalty


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
) -> tuple[str, str | None]:
    """Downgrade strong directions (YES/NO) to WAIT when market quality is
    below threshold. WAIT/AVOID are never downgraded by market quality."""
    if raw_direction not in _STRONG_DIRECTIONS:
        return raw_direction, None
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
) -> tuple[str | None, str | None, bool]:
    """Merge decision_quality and market_quality overlays using most-strict
    direction wins. Returns (final_displayed_direction, final_downgrade_reason,
    market_applied).

    Parallel + most-strict semantics per spec § Downgrade Chain:
    - Both computed independently; neither reads the other's output.
    - severity_rank: YES/NO=0, WAIT=1, AVOID=2
    - The direction with HIGHER severity wins.
    - If both downgraded to WAIT, reasons are concatenated with " | ".

    Returns ``(None, None, False)`` when neither overlay is present
    (both features off).
    """
    severity = {"YES": 0, "NO": 0, "WAIT": 1, "AVOID": 2}

    dq_direction: str | None = None
    dq_reason: str | None = None
    if decision_quality is not None:
        dq_direction = decision_quality.get("displayed_direction")
        dq_reason = decision_quality.get("downgrade_reason")

    mq_direction: str | None = None
    mq_reason: str | None = None
    if market_quality is not None:
        mq_direction = market_quality.get("suggested_direction")
        mq_reason = market_quality.get("downgrade_reason")

    if dq_direction is None and mq_direction is None:
        return None, None, False

    if dq_direction is None:
        # Only market_quality present
        return mq_direction, mq_reason, False
    if mq_direction is None:
        # Only decision_quality present
        return dq_direction, dq_reason, False

    # Both present — most-strict wins
    dq_sev = severity.get(dq_direction, 0)
    mq_sev = severity.get(mq_direction, 0)

    if mq_sev > dq_sev:
        # Market quality is stricter — it changes the final direction
        final_direction = mq_direction
        final_reason = mq_reason
        market_applied = True
    elif mq_sev == dq_sev and dq_sev > 0:
        # Same severity (both WAIT or both AVOID) — concatenate reasons
        final_direction = dq_direction
        if dq_reason and mq_reason:
            final_reason = f"{dq_reason} | {mq_reason}"
        else:
            final_reason = dq_reason or mq_reason
        market_applied = bool(mq_reason)  # market contributed a reason
    else:
        # decision_quality is stricter or equal without market contribution
        final_direction = dq_direction
        final_reason = dq_reason
        market_applied = False

    return final_direction, final_reason, market_applied
