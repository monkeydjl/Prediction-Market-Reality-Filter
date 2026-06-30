"""Decision quality service (Phase 1: Decision Explanation + Conflict Layer).

Pure-function layer that converts ``actionable_recommendation`` +
``evidence_breakdown`` into a structured ``decision_quality`` overlay block.

This is an EXPLANATION/AUDIT layer only. It MUST NOT feed back into
``ai_probability``, ``evidence_profile``, ``regression_to_market``, or
``actionable_recommendation``. The data flow is one-way:

    actionable_recommendation + evidence_breakdown
      -> build_decision_quality
      -> decision_quality (overlay only, no writeback)

The function is synchronous and deterministic — no LLM calls, no I/O.
``settings`` is intentionally not passed; the orchestrator extracts concrete
scalar config values and passes them explicitly, keeping the function pure
and easy to unit test.

Two direction vocabularies (spec § Evidence Selection):
- ``EvidenceBreakdownItem.direction`` ∈ {support, oppose, neutral} — article
  stance relative to the YES outcome. ``support`` = supports YES occurring;
  ``oppose`` = supports NO.
- ``actionable_recommendation.direction`` ∈ {YES, NO, WAIT, AVOID} — the
  recommended side to act on.

This service translates between the two when selecting evidence, but never
rewrites ``EvidenceBreakdownItem.direction``.
"""
from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Fixed legal disclaimer appended to every decision_rationale_zh body.
# Spec § Legal Disclaimer. The disclaimer itself contains no banned words.
_DISCLAIMER_SUFFIX = " 本分析仅供参考，不构成投资建议。"

# Banned vocabulary (case-insensitive). Spec § Rationale Generation.
# Mirrors evidence_aggregation_service._BANNED_WORD_REPLACEMENTS keys.
_BANNED_WORDS = ("long", "short", "buy", "sell", "position", "kelly", "order")

# Direction vocabularies. _SUPPORT_DIRECTIONS maps a recommendation
# direction to the article stance that SUPPORTS it.
# - YES recommendation is supported by `support` articles (YES-stance)
# - NO recommendation is supported by `oppose` articles (NO-stance)
# - WAIT / AVOID surface BOTH sides; no recommendation-side filtering.
_SUPPORT_DIRECTION_FOR_YES = "support"
_SUPPORT_DIRECTION_FOR_NO = "oppose"

_STRONG_STRENGTH_THRESHOLD = 0.7  # spec § Conflict Score safety net


def build_decision_quality(
    *,
    recommendation: dict[str, Any] | None,
    evidence_breakdown: list[dict[str, Any]],
    enabled: bool,
    max_items: int,
    high_threshold: float,
    medium_threshold: float,
) -> dict[str, Any]:
    """Build the decision_quality overlay block.

    Pure function: does not mutate ``recommendation`` or ``evidence_breakdown``.
    Returns a dict with keys: supporting_evidence, opposing_evidence,
    conflict_score, consensus_level, decision_rationale_zh, reversal_triggers,
    downgrade_reason, raw_direction, displayed_direction, downgraded.

    Args:
        recommendation: ``actionable_recommendation`` dict, or None when
            the feature is disabled or signal is WATCHLIST. When None,
            raw_direction defaults to WAIT and both evidence columns are
            surfaced without recommendation-side filtering.
        evidence_breakdown: list of EvidenceBreakdownItem-shaped dicts.
            May be empty; empty triggers consensus_level="none" and a
            downgrade for raw YES/NO directions.
        enabled: when False, the block is still returned (caller decides
            whether to attach). Kept as a parameter so the pure function
            has no hidden dependency on settings.
        max_items: cap for supporting_evidence and opposing_evidence lists
            (applied independently to each column).
        high_threshold: conflict_score >= this means "low" consensus.
        medium_threshold: conflict_score >= this (but < high_threshold)
            means "medium" consensus; < medium_threshold means "high"
            (also requires at least one strong supporting item).

    Returns:
        A dict shaped like DecisionQuality. Never raises — on adversarial
        input, degrades to a well-formed fallback block.
    """
    # Defensive deep-copy so we can prove no-writeback even if a caller
    # later mutates the returned block's nested structures.
    evidence = _safe_list(evidence_breakdown)

    raw_direction = _extract_raw_direction(recommendation)
    risk_level = _extract_risk_level(recommendation)

    supporting, opposing = _select_evidence(raw_direction, evidence, max_items)
    conflict_score = _compute_conflict_score(supporting, opposing)
    consensus_level = _classify_consensus(
        conflict_score, supporting, opposing, high_threshold, medium_threshold
    )

    displayed_direction, downgrade_reason = _apply_downgrade_rules(
        raw_direction, consensus_level, supporting, opposing, risk_level, evidence
    )

    rationale_body = _build_rationale_body(
        raw_direction, displayed_direction, consensus_level,
        supporting, opposing, downgrade_reason,
    )
    decision_rationale_zh = _filter_banned_words(rationale_body) + _DISCLAIMER_SUFFIX

    return {
        "supporting_evidence": supporting,
        "opposing_evidence": opposing,
        "conflict_score": round(conflict_score, 4),
        "consensus_level": consensus_level,
        "decision_rationale_zh": decision_rationale_zh,
        "reversal_triggers": [],  # Phase 1: empty per spec § reversal_triggers
        "downgrade_reason": downgrade_reason,
        "raw_direction": raw_direction,
        "displayed_direction": displayed_direction,
        "downgraded": displayed_direction != raw_direction,
    }


def _safe_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(copy.deepcopy(item))
    return out


def _extract_raw_direction(recommendation: dict[str, Any] | None) -> str:
    if not isinstance(recommendation, dict):
        return "WAIT"
    direction = recommendation.get("direction")
    if direction in ("YES", "NO", "WAIT", "AVOID"):
        return direction
    return "WAIT"


def _extract_risk_level(recommendation: dict[str, Any] | None) -> str:
    if not isinstance(recommendation, dict):
        return "unknown"
    level = recommendation.get("risk_level")
    if isinstance(level, str) and level.lower() in ("low", "medium", "high"):
        return level.lower()
    return "unknown"


def _select_evidence(
    raw_direction: str,
    evidence: list[dict[str, Any]],
    max_items: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split evidence into supporting and opposing columns per the
    recommendation direction. WAIT/AVOID surface both sides without
    recommendation-side filtering.

    Each selected item is shaped as DecisionEvidenceItem (source, title,
    strength, credibility, rationale_zh). Ranked by strength * credibility
    descending, capped at max_items per column.
    """
    if max_items <= 0:
        return [], []

    support_stance = _SUPPORT_DIRECTION_FOR_YES
    oppose_stance = _SUPPORT_DIRECTION_FOR_NO
    if raw_direction == "NO":
        support_stance, oppose_stance = oppose_stance, support_stance
    # WAIT / AVOID: surface both sides; "support" column = support stance,
    # "oppose" column = oppose stance (no recommendation-side swap).

    supporting: list[dict[str, Any]] = []
    opposing: list[dict[str, Any]] = []
    for item in evidence:
        direction = item.get("direction")
        if direction not in ("support", "oppose"):
            continue  # neutral or invalid -> skip
        driver = _to_driver(item)
        if driver is None:
            continue
        if direction == support_stance:
            supporting.append(driver)
        elif direction == oppose_stance:
            opposing.append(driver)

    supporting.sort(key=lambda d: d["strength"] * d["credibility"], reverse=True)
    opposing.sort(key=lambda d: d["strength"] * d["credibility"], reverse=True)
    return supporting[:max_items], opposing[:max_items]


def _to_driver(item: dict[str, Any]) -> dict[str, Any] | None:
    """Convert an EvidenceBreakdownItem-shaped dict to a DecisionEvidenceItem-
    shaped dict. Returns None for malformed items (per-item fail-closed)."""
    try:
        strength = float(item.get("strength", 0.0))
        credibility = float(item.get("credibility", 0.0))
    except (TypeError, ValueError):
        return None
    strength = max(0.0, min(1.0, strength))
    credibility = max(0.0, min(1.0, credibility))
    return {
        "source": str(item.get("source") or "unknown")[:200],
        "title": str(item.get("title") or "")[:200],
        "strength": strength,
        "credibility": credibility,
        "rationale_zh": _filter_banned_words(str(item.get("rationale_zh") or ""))[:300],
    }


def _compute_conflict_score(
    supporting: list[dict[str, Any]],
    opposing: list[dict[str, Any]],
) -> float:
    support_weight = sum(d["strength"] * d["credibility"] for d in supporting)
    oppose_weight = sum(d["strength"] * d["credibility"] for d in opposing)
    total = support_weight + oppose_weight
    if total <= 0:
        return 0.0
    return min(support_weight, oppose_weight) / total


def _classify_consensus(
    conflict_score: float,
    supporting: list[dict[str, Any]],
    opposing: list[dict[str, Any]],
    high_threshold: float,
    medium_threshold: float,
) -> str:
    if not supporting and not opposing:
        return "none"
    has_strong_support = any(d["strength"] >= _STRONG_STRENGTH_THRESHOLD for d in supporting)
    has_strong_oppose = any(d["strength"] >= _STRONG_STRENGTH_THRESHOLD for d in opposing)
    if has_strong_support and has_strong_oppose:
        return "low"
    if conflict_score < medium_threshold and has_strong_support:
        return "high"
    if conflict_score < high_threshold:
        return "medium"
    return "low"


def _apply_downgrade_rules(
    raw_direction: str,
    consensus_level: str,
    supporting: list[dict[str, Any]],
    opposing: list[dict[str, Any]],
    risk_level: str,
    evidence: list[dict[str, Any]],
) -> tuple[str, str | None]:
    """Apply the two-stage downgrade pipeline. Returns
    (displayed_direction, downgrade_reason). Never raises.

    Stage A — rules 1-3 fire only when evidence is available (non-empty
    breakdown); rule 4 covers the empty-breakdown case. They are mutually
    exclusive. Within the evidence-available branch, first match wins
    among rules 1, 2, 3.

    Stage B — risk escalation runs unconditionally after Stage A. Can
    escalate WAIT -> AVOID or YES/NO -> AVOID directly. Cannot de-escalate.
    """
    displayed = raw_direction
    reason: str | None = None

    # Stage A — initial downgrade (only for strong directions YES/NO).
    if raw_direction in ("YES", "NO"):
        if consensus_level == "none":
            # Rule 4: evidence_breakdown is empty/absent. Reason wording
            # is distinct from rule 3 ("缺少证据支持" vs "缺少支持证据").
            displayed, reason = "WAIT", "缺少证据支持，强方向建议降级为 WAIT。"
        else:
            # Rules 1-3: evidence is available, first match wins.
            if consensus_level == "low":
                displayed, reason = "WAIT", "证据冲突较高，强方向建议降级为 WAIT。"
            elif _has_official_opposing(opposing):
                displayed, reason = "WAIT", "存在高强度的官方/监管反向证据，降级为 WAIT。"
            elif not supporting:
                # Rule 3: supporting column is empty (but breakdown is not
                # empty — consensus_level is high/medium, meaning opposing
                # evidence exists but no supporting items were selected).
                displayed, reason = "WAIT", "缺少支持证据，强方向建议降级为 WAIT。"

    # Stage B — risk escalation (evaluated UNCONDITIONALLY after Stage A).
    if risk_level == "high" and consensus_level in ("low", "none"):
        displayed, reason = "AVOID", "高风险且证据不足/冲突，降级为 AVOID。"

    return displayed, reason


def _has_official_opposing(opposing: list[dict[str, Any]]) -> bool:
    """Stage A rule 2: opposing evidence contains an official/regulatory
    source with strength >= 0.7. Phase 1 uses a heuristic: source name
    contains one of the official-source keywords (case-insensitive) AND
    strength >= 0.7. Intentionally conservative — only fires on clearly-
    official sources to avoid false positives."""
    official_keywords = (
        "official", "regulator", "ministry", "agency",
        "政府", "官方", "监管", "部",
    )
    for item in opposing:
        if item.get("strength", 0.0) < _STRONG_STRENGTH_THRESHOLD:
            continue
        source_lower = str(item.get("source") or "").lower()
        for kw in official_keywords:
            if kw.lower() in source_lower:
                return True
    return False


def _build_rationale_body(
    raw_direction: str,
    displayed_direction: str,
    consensus_level: str,
    supporting: list[dict[str, Any]],
    opposing: list[dict[str, Any]],
    downgrade_reason: str | None,
) -> str:
    """Generate the rationale body (WITHOUT the disclaimer suffix, which
    is appended by the caller). Uses deterministic templates per spec
    § Rationale Generation."""
    if consensus_level == "none":
        if raw_direction in ("YES", "NO"):
            return "缺少可解析的证据分解，无法判断证据一致性。"
        return "缺少可解析的证据分解，无法判断证据一致性。"

    if downgrade_reason is not None and displayed_direction != raw_direction:
        # Downgraded path
        return (
            f"虽然存在支持 {raw_direction} 的证据，"
            f"但反向证据强度较高或证据不足，当前结论降级为 {displayed_direction}。"
        )

    # Not downgraded
    top_support = supporting[0] if supporting else None
    top_oppose = opposing[0] if opposing else None
    support_count = len(supporting)
    oppose_count = len(opposing)

    if top_support and not top_oppose:
        return (
            f"主要证据来自 {top_support['source']}，"
            f"支持 {raw_direction} 的强度较高；"
            f"反向证据较弱，因此维持 {raw_direction} 方向。"
        )
    if top_support and top_oppose:
        return (
            f"支持方证据来自 {top_support['source']}（{support_count} 条），"
            f"反对方证据来自 {top_oppose['source']}（{oppose_count} 条），"
            f"冲突水平 {consensus_level}，维持 {raw_direction} 方向。"
        )
    # No supporting but not downgraded (only possible for WAIT/AVOID)
    return (
        f"无明确支持证据，当前方向 {raw_direction} 为保守建议。"
    )


def _filter_banned_words(text: str) -> str:
    """Case-insensitive replace of banned trading terms. Mirrors
    evidence_aggregation_service._filter_banned_words so the禁词 invariant
    holds across both audit layers."""
    if not text:
        return ""
    replacements = {
        "long": "支持 YES",
        "buy": "支持 YES",
        "short": "支持 NO",
        "sell": "支持 NO",
        "position": "配置",
        "kelly": "风险预算",
        "order": "决策",
    }
    import re
    result = text
    for banned, replacement in replacements.items():
        pattern = re.compile(re.escape(banned), re.IGNORECASE)
        result = pattern.sub(replacement, result)
    return result
