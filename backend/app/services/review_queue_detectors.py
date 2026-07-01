"""Review queue trigger detectors (Plan 4 §6.2).

Pure functions that scan a single event record and return review-queue
candidate dicts. No I/O, no LLM, no settings reads — the orchestrator
calls ``detect_review_candidates`` and decides whether to enqueue.

Each candidate is a dict with keys:
    trigger   — one of the locked trigger type strings
    severity  — "WARN" or "ERROR"
    reason    — Chinese reason string (vocabulary-locked)
    context   — dict of relevant field values for the reviewer

Trigger types (locked):
    high_value_downgraded     — act signal but final direction is WAIT/AVOID
    source_market_conflict    — source_reliability says WAIT but market_quality
                                does not (strong cross-overlay conflict)
    outcome_prediction_mismatch — resolved outcome contradicts a high-confidence
                                  prediction
"""
from __future__ import annotations

from typing import Any

_ACT_SIGNALS = frozenset({"act", "provisional_act"})
_WAIT_LIKE = frozenset({"WAIT", "AVOID"})


def detect_review_candidates(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Scan a record and return review-queue candidate dicts.

    Pure, synchronous, deterministic. Returns an empty list when no
    detector fires. Does not crash on missing fields.
    """
    if not isinstance(record, dict):
        return []
    candidates: list[dict[str, Any]] = []
    candidates.extend(_detect_high_value_downgraded(record))
    candidates.extend(_detect_source_market_conflict(record))
    candidates.extend(_detect_outcome_prediction_mismatch(record))
    return candidates


def _detect_high_value_downgraded(record: dict[str, Any]) -> list[dict[str, Any]]:
    rec = record.get("actionable_recommendation") or {}
    if not isinstance(rec, dict):
        return []
    signal = rec.get("signal")
    if signal not in _ACT_SIGNALS:
        return []
    final_dir = record.get("final_displayed_direction")
    if final_dir not in _WAIT_LIKE:
        return []
    reason = record.get("final_downgrade_reason") or ""
    return [{
        "trigger": "high_value_downgraded",
        "severity": "WARN",
        "reason": f"高价值信号 {signal} 被降级为 {final_dir}"
                  + (f"：{reason}" if reason else ""),
        "context": {
            "signal": signal,
            "raw_direction": rec.get("direction"),
            "final_direction": final_dir,
            "downgrade_reason": reason,
            "ai_probability": rec.get("ai_probability"),
        },
    }]


def _detect_source_market_conflict(record: dict[str, Any]) -> list[dict[str, Any]]:
    sr = record.get("source_reliability")
    mq = record.get("market_quality")
    if not isinstance(sr, dict) or not isinstance(mq, dict):
        return []
    sr_says_wait = sr.get("suggested_direction") in _WAIT_LIKE and sr.get("downgraded") is True
    mq_says_ok = mq.get("downgraded") is not True
    if not (sr_says_wait and mq_says_ok):
        return []
    return [{
        "trigger": "source_market_conflict",
        "severity": "WARN",
        "reason": "来源可靠性与市场质量强冲突：来源建议 WAIT 但市场质量未降级",
        "context": {
            "sr_suggested_direction": sr.get("suggested_direction"),
            "sr_downgrade_reason": sr.get("downgrade_reason"),
            "mq_suggested_direction": mq.get("suggested_direction"),
        },
    }]


def _detect_outcome_prediction_mismatch(
    record: dict[str, Any],
    *,
    confidence_threshold: float = 0.75,
) -> list[dict[str, Any]]:
    outcome = record.get("outcome")
    if not outcome:
        return []
    rec = record.get("actionable_recommendation") or {}
    if not isinstance(rec, dict):
        return []
    prob = rec.get("ai_probability")
    if not isinstance(prob, (int, float)):
        return []
    if prob < confidence_threshold:
        return []
    direction = rec.get("direction")
    # Outcome YES contradicts a NO prediction and vice versa.
    contradicts = (
        (outcome == "YES" and direction == "NO")
        or (outcome == "NO" and direction == "YES")
    )
    if not contradicts:
        return []
    return [{
        "trigger": "outcome_prediction_mismatch",
        "severity": "ERROR",
        "reason": f"结算结果 {outcome} 与高置信预测 {direction}（置信度 {prob:.2f}）相反",
        "context": {
            "outcome": outcome,
            "predicted_direction": direction,
            "ai_probability": prob,
        },
    }]
