from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _confidence_to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if value == "high":
        return 0.80
    if value == "medium":
        return 0.60
    if value == "low":
        return 0.40
    return None


def _evidence_groups(record: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {
        "supporting": [],
        "opposing": [],
        "neutral": [],
    }
    for item in _list(record.get("evidence_breakdown")):
        if not isinstance(item, dict):
            continue
        direction = item.get("direction")
        if direction == "supports":
            groups["supporting"].append(item)
        elif direction == "refutes":
            groups["opposing"].append(item)
        else:
            groups["neutral"].append(item)
    return groups


def build_event_challenge_input(
    record: dict[str, Any],
    *,
    attempt_count: int = 0,
    allow_llm_critic: bool = False,
    strictness: str = "normal",
) -> dict[str, Any]:
    rec = record if isinstance(record, dict) else {}
    action = _dict(rec.get("actionable_recommendation"))
    probability = _dict(rec.get("probability"))
    evidence = _evidence_groups(rec)
    risk = _dict(rec.get("risk"))
    market_quality = _dict(rec.get("market_quality"))
    execution_quality = _dict(rec.get("execution_quality"))

    return {
        "domain": "event_intelligence",
        "subject": {
            "id": str(rec.get("event_id") or ""),
            "title": str(rec.get("event_title") or rec.get("event_title_zh") or ""),
            "type": "event_recommendation",
        },
        "conclusion": {
            "direction": rec.get("final_displayed_direction") or action.get("direction"),
            "predicted_score": None,
            "probabilities": None,
            "confidence": _confidence_to_float(action.get("confidence")),
            "recommended_action": action.get("direction"),
        },
        "calculation_trace": {
            "method": "event_intelligence",
            "engine_used": None,
            "weights": {},
            "key_scores": {
                "baseline": probability.get("baseline"),
                "estimated": probability.get("estimated"),
                "change": probability.get("change"),
                "edge": action.get("edge"),
                "suggested_allocation_pct": action.get("suggested_allocation_pct"),
            },
            "calibration": rec.get("calibration_feedback") or {},
        },
        "evidence": {
            "supporting": evidence["supporting"],
            "opposing": evidence["opposing"],
            "neutral": evidence["neutral"],
            "data_quality": {
                "decision_quality": rec.get("decision_quality"),
                "market_quality": rec.get("market_quality"),
                "llm_telemetry": rec.get("llm_telemetry"),
            },
            "source_reliability": rec.get("source_reliability"),
        },
        "risk": {
            "level": action.get("risk_level") or risk.get("level"),
            "flags": _list(risk.get("flags")),
            "execution_constraints": {
                "liquidity_ok": market_quality.get("liquidity_score") is None
                or market_quality.get("liquidity_score", 1) > 0,
                "executable": execution_quality.get("executable", True),
            },
        },
        "options": {
            "max_recompute_attempts": 1,
            "strictness": strictness,
            "allow_llm_critic": allow_llm_critic,
        },
        "attempt_count": attempt_count,
    }


def apply_event_challenge_result(
    record: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if not isinstance(record, dict):
        return
    res = result if isinstance(result, dict) else {}
    record["conclusion_challenge"] = {
        "verdict": res.get("verdict"),
        "required_action": res.get("required_action"),
        "failed_checks": res.get("failed_checks", []),
        "warnings": res.get("warnings", []),
        "challenge_summary": res.get("challenge_summary", ""),
        "critic_notes": res.get("critic_notes", {}),
        "attempt_count": res.get("attempt_count", 0),
    }
    if res.get("required_action") == "downgrade_to_wait":
        verdict = res.get("verdict")
        reason = res.get("challenge_summary") or "结论未通过否定门检查。"
        if verdict == "insufficient_evidence":
            record["final_downgrade_reason"] = (
                "insufficient_evidence_for_strong_conclusion"
            )
        else:
            record["final_downgrade_reason"] = (
                f"conclusion_challenge_rejected: {reason}"
            )
        record["final_displayed_direction"] = "WAIT"
