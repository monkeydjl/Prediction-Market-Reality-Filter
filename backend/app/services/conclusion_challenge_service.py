from __future__ import annotations

from typing import Any, Protocol

CHECK_CALCULATION = "calculation_rationale"
CHECK_EVIDENCE = "evidence_support"
CHECK_COUNTEREVIDENCE = "counterevidence"
CHECK_CONFIDENCE = "confidence_calibration"
CHECK_ACTIONABILITY = "actionability"

PASS = "pass"
PASS_WITH_WARNINGS = "pass_with_warnings"
REVISE = "revise"
REJECT = "reject"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CriticAdapter(Protocol):
    def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


def _num(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_strong_event_direction(payload: dict[str, Any]) -> bool:
    conclusion = _dict(payload.get("conclusion"))
    return conclusion.get("direction") in {"YES", "NO"}


def _is_strong_world_cup_prediction(payload: dict[str, Any]) -> bool:
    conclusion = _dict(payload.get("conclusion"))
    confidence = _num(conclusion.get("confidence"), 0.0) or 0.0
    return confidence >= 0.70


def _strong_conclusion(payload: dict[str, Any]) -> bool:
    if payload.get("domain") == "world_cup":
        return _is_strong_world_cup_prediction(payload)
    return _is_strong_event_direction(payload)


def _failure(
    check: str,
    severity: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "check": check,
        "severity": severity,
        "reason": reason,
        "details": details or {},
    }


def _check_calculation(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    conclusion = _dict(payload.get("conclusion"))
    trace = _dict(payload.get("calculation_trace"))
    scores = _dict(trace.get("key_scores"))

    if payload.get("domain") == "event_intelligence":
        direction = conclusion.get("direction")
        change = _num(scores.get("change"))
        if direction in {"YES", "NO"} and change is not None and abs(change) < 3.0:
            failures.append(
                _failure(
                    CHECK_CALCULATION,
                    "soft_fail",
                    "概率变化太小，不足以支撑强方向结论。",
                    {"direction": direction, "change": change},
                )
            )

    if payload.get("domain") == "world_cup":
        probabilities = _dict(conclusion.get("probabilities"))
        confidence = _num(conclusion.get("confidence"), 0.0) or 0.0
        max_prob = (
            max(
                [
                    _num(probabilities.get("home_win"), 0.0) or 0.0,
                    _num(probabilities.get("draw"), 0.0) or 0.0,
                    _num(probabilities.get("away_win"), 0.0) or 0.0,
                ]
            )
            if probabilities
            else 0.0
        )
        if confidence >= 0.75 and max_prob < 0.45:
            failures.append(
                _failure(
                    CHECK_CALCULATION,
                    "soft_fail",
                    "最高赛果概率偏低，但预测置信度较高。",
                    {"confidence": confidence, "max_probability": max_prob},
                )
            )

    if not trace.get("method"):
        warnings.append(_failure(CHECK_CALCULATION, "warning", "缺少计算方法说明。"))
    return failures, warnings


def _check_evidence(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence = _dict(payload.get("evidence"))
    supporting = _list(evidence.get("supporting"))
    if _strong_conclusion(payload) and not supporting:
        return [_failure(CHECK_EVIDENCE, "hard_fail", "强结论缺少支持证据。")], []

    credible_support = [
        item
        for item in supporting
        if (_num(_dict(item).get("credibility"), 0.0) or 0.0) >= 0.55
    ]
    if _strong_conclusion(payload) and supporting and not credible_support:
        return [_failure(CHECK_EVIDENCE, "soft_fail", "支持证据可信度不足。")], []
    return [], []


def _check_counterevidence(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence = _dict(payload.get("evidence"))
    opposing = _list(evidence.get("opposing"))
    strong_opposing = [
        item
        for item in opposing
        if (_num(_dict(item).get("credibility"), 0.0) or 0.0) >= 0.80
    ]
    if _strong_conclusion(payload) and strong_opposing:
        return [
            _failure(
                CHECK_COUNTEREVIDENCE,
                "hard_fail",
                "存在高可信反证，不能直接输出强结论。",
                {"opposing_count": len(strong_opposing)},
            )
        ], []

    source_reliability = _dict(evidence.get("source_reliability"))
    if (
        source_reliability.get("suggested_direction") in {"WAIT", "AVOID"}
        and source_reliability.get("downgraded") is True
    ):
        return [
            _failure(
                CHECK_COUNTEREVIDENCE,
                "hard_fail",
                "来源可靠性层已建议降级，当前强结论未通过反证检查。",
                {"source_reliability": source_reliability},
            )
        ], []
    return [], []


def _check_confidence(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conclusion = _dict(payload.get("conclusion"))
    trace = _dict(payload.get("calculation_trace"))
    calibration = _dict(trace.get("calibration"))
    confidence = _num(conclusion.get("confidence"), 0.0) or 0.0
    sample_count = int(_num(calibration.get("sample_count"), 0.0) or 0)
    reliable = calibration.get("is_reliable")
    if confidence >= 0.75 and reliable is False:
        return [
            _failure(
                CHECK_CONFIDENCE,
                "soft_fail",
                "置信度较高，但校准数据不可靠。",
                {"confidence": confidence, "sample_count": sample_count},
            )
        ], []
    if confidence >= 0.80 and sample_count and sample_count < 5:
        return [
            _failure(
                CHECK_CONFIDENCE,
                "soft_fail",
                "高置信结论的历史样本不足。",
                {"confidence": confidence, "sample_count": sample_count},
            )
        ], []
    return [], []


def _check_actionability(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    risk = _dict(payload.get("risk"))
    constraints = _dict(risk.get("execution_constraints"))
    flags = _list(risk.get("flags"))
    failures: list[dict[str, Any]] = []
    if payload.get("domain") == "event_intelligence" and _strong_conclusion(payload):
        if constraints.get("liquidity_ok") is False or constraints.get("executable") is False:
            failures.append(
                _failure(
                    CHECK_ACTIONABILITY,
                    "soft_fail",
                    "结论方向可能成立，但当前市场不可执行或流动性不足。",
                    {"execution_constraints": constraints},
                )
            )
        if len(flags) >= 3:
            failures.append(
                _failure(
                    CHECK_ACTIONABILITY,
                    "soft_fail",
                    "风险标记过多，不适合输出强行动结论。",
                    {"risk_flags": flags},
                )
            )
    return failures, []


def _run_checks(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for check in (
        _check_calculation,
        _check_evidence,
        _check_counterevidence,
        _check_confidence,
        _check_actionability,
    ):
        check_failures, check_warnings = check(payload)
        failures.extend(check_failures)
        warnings.extend(check_warnings)
    return failures, warnings


def _required_action(payload: dict[str, Any], verdict: str) -> str:
    attempt_count = int(_num(payload.get("attempt_count"), 0.0) or 0)
    options = _dict(payload.get("options"))
    max_attempts = int(_num(options.get("max_recompute_attempts"), 1.0) or 1)
    if verdict == PASS:
        return "allow_output"
    if verdict == PASS_WITH_WARNINGS:
        return "cap_confidence"
    if verdict == REVISE:
        return "recalculate_once" if attempt_count < max_attempts else "downgrade_to_wait"
    if verdict in {REJECT, INSUFFICIENT_EVIDENCE}:
        return "downgrade_to_wait"
    return "enqueue_review"


def _aggregate(
    payload: dict[str, Any],
    failures: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> str:
    _ = payload
    hard = [item for item in failures if item.get("severity") == "hard_fail"]
    soft = [item for item in failures if item.get("severity") == "soft_fail"]
    if any(
        item.get("check") == CHECK_EVIDENCE and item.get("severity") == "hard_fail"
        for item in failures
    ):
        return INSUFFICIENT_EVIDENCE
    if hard:
        return REJECT
    if len(soft) >= 2:
        return REVISE
    if soft or warnings:
        return PASS_WITH_WARNINGS
    return PASS


def _confidence_adjustment(verdict: str, payload: dict[str, Any]) -> dict[str, Any]:
    if verdict == PASS:
        return {"cap": None, "reason": None}
    if payload.get("domain") == "world_cup":
        if verdict in {REJECT, INSUFFICIENT_EVIDENCE}:
            return {"cap": 0.60, "reason": "否定门未通过，限制世界杯预测置信度。"}
        if verdict in {PASS_WITH_WARNINGS, REVISE}:
            return {"cap": 0.70, "reason": "否定门发现警告，限制世界杯预测置信度。"}
    if verdict in {PASS_WITH_WARNINGS, REVISE}:
        return {"cap": 0.70, "reason": "否定门发现警告，限制结论置信度。"}
    return {"cap": None, "reason": None}


def _summary(
    verdict: str,
    failures: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> str:
    if verdict == PASS:
        return "结论通过否定门检查。"
    items = failures or warnings
    first = items[0]["reason"] if items else "否定门发现结论风险。"
    return f"结论否定门结果：{verdict}。主要原因：{first}"


def _critic_notes(
    payload: dict[str, Any],
    critic_adapter: CriticAdapter | None,
) -> tuple[dict[str, Any], bool]:
    if critic_adapter is None or not _dict(payload.get("options")).get(
        "allow_llm_critic"
    ):
        return {
            "missing_counterarguments": [],
            "weak_assumptions": [],
            "evidence_gaps": [],
        }, False
    try:
        notes = critic_adapter.review(payload)
        if isinstance(notes, dict):
            return {
                "missing_counterarguments": _list(notes.get("missing_counterarguments")),
                "weak_assumptions": _list(notes.get("weak_assumptions")),
                "evidence_gaps": _list(notes.get("evidence_gaps")),
            }, False
    except Exception:
        pass
    return {
        "missing_counterarguments": [],
        "weak_assumptions": [],
        "evidence_gaps": ["critic_unavailable"],
    }, True


def challenge_conclusion(
    payload: dict[str, Any],
    critic_adapter: CriticAdapter | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    failures, warnings = _run_checks(payload)
    notes, critic_failed = _critic_notes(payload, critic_adapter)
    if critic_failed and not failures:
        warnings.append(
            _failure("critic", "warning", "LLM critic 不可用，已仅使用规则检查。")
        )
    verdict = _aggregate(payload, failures, warnings)
    return {
        "verdict": verdict,
        "required_action": _required_action(payload, verdict),
        "failed_checks": failures,
        "warnings": warnings,
        "confidence_adjustment": _confidence_adjustment(verdict, payload),
        "challenge_summary": _summary(verdict, failures, warnings),
        "critic_notes": notes,
        "attempt_count": int(_num(payload.get("attempt_count"), 0.0) or 0),
    }
