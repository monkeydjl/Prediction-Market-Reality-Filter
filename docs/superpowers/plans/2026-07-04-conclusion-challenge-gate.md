# Conclusion Challenge Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared conclusion challenge gate that blocks or downgrades weak strong conclusions in the World Cup system and the main event intelligence module.

**Architecture:** Build one pure challenge module with a small `challenge_conclusion(input, critic_adapter=None)` interface. Domain-specific adapters convert World Cup predictions and event records into the shared input and apply the returned result. Orchestrators handle flags, persistence, retry, and review queue I/O.

**Tech Stack:** Python 3.11+, FastAPI backend, pytest, existing dict-shaped service interfaces, existing review queue store, existing config pattern in `backend/app/core/config.py`.

## Global Constraints

- Version 1 is a single challenge gate, not a multi-API debate system.
- The shared service must not import SQLAlchemy models, event stores, review queue stores, or settings.
- Feature flags default to disabled and must preserve current behavior when off.
- LLM critic is optional and must not own the final verdict.
- Deterministic checks must be testable without network access or an LLM.
- Automatic recalculation is capped at one attempt.
- Main event hard failures downgrade strong directions to `WAIT`.
- World Cup hard failures preserve prediction display but remove high-confidence semantics and cap confidence.
- Downgrade and challenge reasons must be readable Chinese text.

---

## File Structure

- Create `backend/app/services/conclusion_challenge_service.py`
  - Pure deterministic checks, verdict aggregation, optional critic adapter call.
  - Public interface: `challenge_conclusion(payload, critic_adapter=None) -> dict[str, Any]`.

- Create `backend/app/services/conclusion_challenge_event_adapter.py`
  - Converts event intelligence records to `ChallengeInput`.
  - Applies challenge results to event records.

- Create `backend/app/services/conclusion_challenge_world_cup_adapter.py`
  - Converts World Cup match/prediction/factor data to `ChallengeInput`.
  - Applies challenge results to `prediction_result`.

- Modify `backend/app/core/config.py`
  - Add challenge gate flags and numeric defaults.

- Modify `backend/.env.example`
  - Document disabled-by-default challenge settings.

- Modify `backend/app/services/event_intelligence_service.py`
  - Add gated challenge integration after guardrails and before review queue detectors.
  - Add one-attempt recompute support through internal-only parameters.

- Modify `backend/app/services/world_cup_prediction_pipeline.py`
  - Add gated challenge integration after confidence calibration/betting/tactical output and before persistence.
  - Add one-attempt retry or conservative fallback behavior.

- Modify `backend/app/services/review_queue_detectors.py`
  - Register `conclusion_challenge_failed` in trigger documentation only.

- Test files:
  - Create `backend/tests/test_conclusion_challenge_service.py`
  - Create `backend/tests/test_conclusion_challenge_event_adapter.py`
  - Create `backend/tests/test_conclusion_challenge_world_cup_adapter.py`
  - Extend `backend/tests/test_event_intelligence_service.py`
  - Extend `backend/tests/test_world_cup_prediction_pipeline.py`
  - Extend config tests if present; otherwise create `backend/tests/test_conclusion_challenge_config.py`

---

### Task 1: Pure Conclusion Challenge Service

**Files:**
- Create: `backend/app/services/conclusion_challenge_service.py`
- Test: `backend/tests/test_conclusion_challenge_service.py`

**Interfaces:**
- Produces:
  - `challenge_conclusion(payload: dict[str, Any], critic_adapter: Any | None = None) -> dict[str, Any]`
  - Result keys: `verdict`, `required_action`, `failed_checks`, `warnings`, `confidence_adjustment`, `challenge_summary`, `critic_notes`, `attempt_count`
- Consumes: no project I/O, no settings, no stores.

- [ ] **Step 1: Write failing tests for verdict aggregation**

Create `backend/tests/test_conclusion_challenge_service.py` with these tests:

```python
from app.services.conclusion_challenge_service import challenge_conclusion


def _base_payload(**overrides):
    payload = {
        "domain": "event_intelligence",
        "subject": {"id": "evt-1", "title": "Will X happen?", "type": "event_recommendation"},
        "conclusion": {
            "direction": "YES",
            "predicted_score": None,
            "probabilities": None,
            "confidence": 0.72,
            "recommended_action": "YES",
        },
        "calculation_trace": {
            "method": "event_intelligence",
            "engine_used": None,
            "weights": {},
            "key_scores": {"baseline": 40.0, "estimated": 58.0, "change": 18.0},
            "calibration": {"is_reliable": True, "sample_count": 20},
        },
        "evidence": {
            "supporting": [{"source": "Reuters", "credibility": 0.9, "direction": "supports"}],
            "opposing": [],
            "neutral": [],
            "data_quality": {"quality": "real", "score": 0.9},
            "source_reliability": {"suggested_direction": "YES", "downgraded": False},
        },
        "risk": {"level": "medium", "flags": [], "execution_constraints": {}},
        "options": {
            "max_recompute_attempts": 1,
            "strictness": "normal",
            "allow_llm_critic": False,
        },
        "attempt_count": 0,
    }
    payload.update(overrides)
    return payload


def test_all_checks_pass_returns_pass():
    result = challenge_conclusion(_base_payload())
    assert result["verdict"] == "pass"
    assert result["required_action"] == "allow_output"
    assert result["failed_checks"] == []


def test_strong_conclusion_without_support_is_insufficient_evidence():
    payload = _base_payload()
    payload["evidence"]["supporting"] = []
    payload["evidence"]["neutral"] = [{"source": "blog", "credibility": 0.4}]
    result = challenge_conclusion(payload)
    assert result["verdict"] == "insufficient_evidence"
    assert result["required_action"] == "downgrade_to_wait"
    assert result["failed_checks"][0]["check"] == "evidence_support"


def test_hard_counterevidence_rejects():
    payload = _base_payload()
    payload["evidence"]["opposing"] = [
        {"source": "official", "credibility": 0.95, "direction": "refutes"}
    ]
    result = challenge_conclusion(payload)
    assert result["verdict"] == "reject"
    assert result["required_action"] == "downgrade_to_wait"
    assert any(item["check"] == "counterevidence" for item in result["failed_checks"])


def test_two_soft_failures_request_single_recalculation():
    payload = _base_payload()
    payload["conclusion"]["confidence"] = 0.86
    payload["calculation_trace"]["calibration"] = {"is_reliable": False, "sample_count": 2}
    payload["risk"]["execution_constraints"] = {"liquidity_ok": False}
    result = challenge_conclusion(payload)
    assert result["verdict"] == "revise"
    assert result["required_action"] == "recalculate_once"


def test_revise_after_one_attempt_downgrades():
    payload = _base_payload(attempt_count=1)
    payload["conclusion"]["confidence"] = 0.86
    payload["calculation_trace"]["calibration"] = {"is_reliable": False, "sample_count": 2}
    payload["risk"]["execution_constraints"] = {"liquidity_ok": False}
    result = challenge_conclusion(payload)
    assert result["verdict"] == "revise"
    assert result["required_action"] == "downgrade_to_wait"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd backend
python -m pytest tests/test_conclusion_challenge_service.py -q
```

Expected: import failure because `app.services.conclusion_challenge_service` does not exist.

- [ ] **Step 3: Implement the pure service**

Create `backend/app/services/conclusion_challenge_service.py`:

```python
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


def _failure(check: str, severity: str, reason: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "check": check,
        "severity": severity,
        "reason": reason,
        "details": details or {},
    }


def _check_calculation(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    conclusion = _dict(payload.get("conclusion"))
    trace = _dict(payload.get("calculation_trace"))
    scores = _dict(trace.get("key_scores"))

    if payload.get("domain") == "event_intelligence":
        direction = conclusion.get("direction")
        change = _num(scores.get("change"))
        if direction in {"YES", "NO"} and change is not None and abs(change) < 3.0:
            failures.append(_failure(
                CHECK_CALCULATION,
                "soft_fail",
                "概率变化太小，不足以支撑强方向结论。",
                {"direction": direction, "change": change},
            ))

    if payload.get("domain") == "world_cup":
        probabilities = _dict(conclusion.get("probabilities"))
        confidence = _num(conclusion.get("confidence"), 0.0) or 0.0
        max_prob = max(
            [
                _num(probabilities.get("home_win"), 0.0) or 0.0,
                _num(probabilities.get("draw"), 0.0) or 0.0,
                _num(probabilities.get("away_win"), 0.0) or 0.0,
            ]
        ) if probabilities else 0.0
        if confidence >= 0.75 and max_prob < 0.45:
            failures.append(_failure(
                CHECK_CALCULATION,
                "soft_fail",
                "最高赛果概率偏低，但预测置信度较高。",
                {"confidence": confidence, "max_probability": max_prob},
            ))

    if not trace.get("method"):
        warnings.append(_failure(CHECK_CALCULATION, "warning", "缺少计算方法说明。"))
    return failures, warnings


def _check_evidence(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence = _dict(payload.get("evidence"))
    supporting = _list(evidence.get("supporting"))
    if _strong_conclusion(payload) and not supporting:
        return [
            _failure(CHECK_EVIDENCE, "hard_fail", "强结论缺少支持证据。")
        ], []

    credible_support = [
        item for item in supporting
        if (_num(_dict(item).get("credibility"), 0.0) or 0.0) >= 0.55
    ]
    if _strong_conclusion(payload) and supporting and not credible_support:
        return [
            _failure(CHECK_EVIDENCE, "soft_fail", "支持证据可信度不足。")
        ], []
    return [], []


def _check_counterevidence(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence = _dict(payload.get("evidence"))
    opposing = _list(evidence.get("opposing"))
    strong_opposing = [
        item for item in opposing
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
    if source_reliability.get("suggested_direction") in {"WAIT", "AVOID"} and source_reliability.get("downgraded") is True:
        return [
            _failure(
                CHECK_COUNTEREVIDENCE,
                "hard_fail",
                "来源可靠性层已建议降级，当前强结论未通过反证检查。",
                {"source_reliability": source_reliability},
            )
        ], []
    return [], []


def _check_confidence(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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


def _check_actionability(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    risk = _dict(payload.get("risk"))
    constraints = _dict(risk.get("execution_constraints"))
    flags = _list(risk.get("flags"))
    failures: list[dict[str, Any]] = []
    if payload.get("domain") == "event_intelligence" and _strong_conclusion(payload):
        if constraints.get("liquidity_ok") is False or constraints.get("executable") is False:
            failures.append(_failure(
                CHECK_ACTIONABILITY,
                "soft_fail",
                "结论方向可能成立，但当前市场不可执行或流动性不足。",
                {"execution_constraints": constraints},
            ))
        if len(flags) >= 3:
            failures.append(_failure(
                CHECK_ACTIONABILITY,
                "soft_fail",
                "风险标记过多，不适合输出强行动结论。",
                {"risk_flags": flags},
            ))
    return failures, []


def _run_checks(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
    max_attempts = int(_num(_dict(payload.get("options")).get("max_recompute_attempts"), 1.0) or 1)
    if verdict == PASS:
        return "allow_output"
    if verdict == PASS_WITH_WARNINGS:
        return "cap_confidence"
    if verdict == REVISE:
        return "recalculate_once" if attempt_count < max_attempts else "downgrade_to_wait"
    if verdict in {REJECT, INSUFFICIENT_EVIDENCE}:
        return "downgrade_to_wait"
    return "enqueue_review"


def _aggregate(payload: dict[str, Any], failures: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> str:
    hard = [item for item in failures if item.get("severity") == "hard_fail"]
    soft = [item for item in failures if item.get("severity") == "soft_fail"]
    if any(item.get("check") == CHECK_EVIDENCE and item.get("severity") == "hard_fail" for item in failures):
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


def _summary(verdict: str, failures: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> str:
    if verdict == PASS:
        return "结论通过否定门检查。"
    items = failures or warnings
    first = items[0]["reason"] if items else "否定门发现结论风险。"
    return f"结论否定门结果：{verdict}。主要原因：{first}"


def _critic_notes(payload: dict[str, Any], critic_adapter: CriticAdapter | None) -> tuple[dict[str, Any], bool]:
    if critic_adapter is None or not _dict(payload.get("options")).get("allow_llm_critic"):
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
        warnings.append(_failure("critic", "warning", "LLM critic 不可用，已仅使用规则检查。"))
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
cd backend
python -m pytest tests/test_conclusion_challenge_service.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/services/conclusion_challenge_service.py backend/tests/test_conclusion_challenge_service.py
git commit -m "feat: add conclusion challenge service"
```

---

### Task 2: Event Intelligence Adapter

**Files:**
- Create: `backend/app/services/conclusion_challenge_event_adapter.py`
- Test: `backend/tests/test_conclusion_challenge_event_adapter.py`

**Interfaces:**
- Consumes: `challenge_conclusion()` result shape from Task 1.
- Produces:
  - `build_event_challenge_input(record: dict[str, Any], *, attempt_count: int = 0, allow_llm_critic: bool = False, strictness: str = "normal") -> dict[str, Any]`
  - `apply_event_challenge_result(record: dict[str, Any], result: dict[str, Any]) -> None`

- [ ] **Step 1: Write failing adapter tests**

Create `backend/tests/test_conclusion_challenge_event_adapter.py`:

```python
from app.services.conclusion_challenge_event_adapter import (
    apply_event_challenge_result,
    build_event_challenge_input,
)


def _record():
    return {
        "event_id": "evt-1",
        "event_title": "Will X happen?",
        "probability": {"baseline": 40.0, "estimated": 61.0, "change": 21.0},
        "actionable_recommendation": {
            "direction": "YES",
            "confidence": "high",
            "suggested_allocation_pct": 2.5,
            "edge": 21.0,
            "risk_level": "medium",
        },
        "final_displayed_direction": "YES",
        "final_downgrade_reason": None,
        "evidence_breakdown": [
            {"source": "Reuters", "direction": "supports", "credibility": 0.9, "url": "https://reuters.com/a"},
            {"source": "Blog", "direction": "neutral", "credibility": 0.4, "url": "https://example.com/b"},
        ],
        "decision_quality": {"conflict_score": 0.1, "downgraded": False},
        "market_quality": {"score": 0.8, "downgraded": False, "liquidity_score": 0.9},
        "source_reliability": {"suggested_direction": "YES", "downgraded": False},
        "risk": {"level": "medium", "flags": []},
    }


def test_build_event_challenge_input_maps_core_fields():
    payload = build_event_challenge_input(_record(), attempt_count=0)
    assert payload["domain"] == "event_intelligence"
    assert payload["subject"]["id"] == "evt-1"
    assert payload["conclusion"]["direction"] == "YES"
    assert payload["calculation_trace"]["key_scores"]["change"] == 21.0
    assert len(payload["evidence"]["supporting"]) == 1
    assert len(payload["evidence"]["neutral"]) == 1


def test_apply_pass_writes_challenge_without_downgrade():
    record = _record()
    result = {
        "verdict": "pass",
        "required_action": "allow_output",
        "failed_checks": [],
        "warnings": [],
        "challenge_summary": "结论通过否定门检查。",
    }
    apply_event_challenge_result(record, result)
    assert record["final_displayed_direction"] == "YES"
    assert record["conclusion_challenge"]["verdict"] == "pass"


def test_apply_reject_downgrades_to_wait():
    record = _record()
    result = {
        "verdict": "reject",
        "required_action": "downgrade_to_wait",
        "failed_checks": [{"check": "counterevidence", "reason": "存在高可信反证"}],
        "warnings": [],
        "challenge_summary": "结论否定门结果：reject。主要原因：存在高可信反证",
    }
    apply_event_challenge_result(record, result)
    assert record["final_displayed_direction"] == "WAIT"
    assert record["final_downgrade_reason"].startswith("conclusion_challenge_rejected")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd backend
python -m pytest tests/test_conclusion_challenge_event_adapter.py -q
```

Expected: import failure because the adapter file does not exist.

- [ ] **Step 3: Implement event adapter**

Create `backend/app/services/conclusion_challenge_event_adapter.py`:

```python
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
    groups = {"supporting": [], "opposing": [], "neutral": []}
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
                "liquidity_ok": market_quality.get("liquidity_score") is None or market_quality.get("liquidity_score", 1) > 0,
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
            record["final_downgrade_reason"] = "insufficient_evidence_for_strong_conclusion"
        else:
            record["final_downgrade_reason"] = f"conclusion_challenge_rejected: {reason}"
        record["final_displayed_direction"] = "WAIT"
```

- [ ] **Step 4: Run adapter tests**

Run:

```powershell
cd backend
python -m pytest tests/test_conclusion_challenge_event_adapter.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/services/conclusion_challenge_event_adapter.py backend/tests/test_conclusion_challenge_event_adapter.py
git commit -m "feat: add event conclusion challenge adapter"
```

---

### Task 3: World Cup Adapter

**Files:**
- Create: `backend/app/services/conclusion_challenge_world_cup_adapter.py`
- Test: `backend/tests/test_conclusion_challenge_world_cup_adapter.py`

**Interfaces:**
- Consumes: `ChallengeResult` from Task 1.
- Produces:
  - `build_world_cup_challenge_input(match: Any, prediction_result: dict[str, Any], factors: dict[str, Any] | None, *, attempt_count: int = 0, allow_llm_critic: bool = False, strictness: str = "normal") -> dict[str, Any]`
  - `apply_world_cup_challenge_result(prediction_result: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]`

- [ ] **Step 1: Write failing adapter tests**

Create `backend/tests/test_conclusion_challenge_world_cup_adapter.py`:

```python
from types import SimpleNamespace

from app.services.conclusion_challenge_world_cup_adapter import (
    apply_world_cup_challenge_result,
    build_world_cup_challenge_input,
)


def _match():
    return SimpleNamespace(
        match_id="wc-1",
        home_team="A",
        away_team="B",
        stage="group_stage",
    )


def _prediction():
    return {
        "predicted_score": {"home": 2.0, "away": 1.0},
        "outcome_probabilities": {"home_win": 0.52, "draw": 0.24, "away_win": 0.24},
        "confidence": 0.78,
        "prediction_method": "integrated",
        "elo_ratings": {"home": 1700, "away": 1600},
        "has_betting_odds": True,
        "high_confidence_selection": {"selected_engine": "integrated"},
    }


def test_build_world_cup_input_maps_prediction_fields():
    factors = {
        "data_quality": "real",
        "confidence_calibration": {"is_reliable": True, "sample_count": 12},
        "explanation_contributions": {"items": [{"key": "elo", "home_impact": 0.2}]},
    }
    payload = build_world_cup_challenge_input(_match(), _prediction(), factors)
    assert payload["domain"] == "world_cup"
    assert payload["subject"]["id"] == "wc-1"
    assert payload["conclusion"]["predicted_score"]["home"] == 2.0
    assert payload["conclusion"]["probabilities"]["home_win"] == 0.52
    assert payload["evidence"]["data_quality"]["quality"] == "real"


def test_apply_pass_writes_challenge_result_only():
    prediction = _prediction()
    result = {
        "verdict": "pass",
        "required_action": "allow_output",
        "failed_checks": [],
        "warnings": [],
        "challenge_summary": "结论通过否定门检查。",
    }
    updated = apply_world_cup_challenge_result(prediction, result)
    assert updated["confidence"] == 0.78
    assert updated["factors"]["challenge_result"]["verdict"] == "pass"


def test_apply_reject_caps_confidence_and_removes_high_confidence_semantics():
    prediction = _prediction()
    result = {
        "verdict": "reject",
        "required_action": "downgrade_to_wait",
        "failed_checks": [{"check": "counterevidence", "reason": "赔率反向"}],
        "warnings": [],
        "confidence_adjustment": {"cap": 0.60, "reason": "否定门未通过"},
        "challenge_summary": "结论否定门结果：reject。主要原因：赔率反向",
    }
    updated = apply_world_cup_challenge_result(prediction, result)
    assert updated["confidence"] == 0.60
    assert updated["high_confidence_selection"] is None
    assert updated["factors"]["requires_review"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd backend
python -m pytest tests/test_conclusion_challenge_world_cup_adapter.py -q
```

Expected: import failure because the adapter file does not exist.

- [ ] **Step 3: Implement World Cup adapter**

Create `backend/app/services/conclusion_challenge_world_cup_adapter.py`:

```python
from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _getattr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def build_world_cup_challenge_input(
    match: Any,
    prediction_result: dict[str, Any],
    factors: dict[str, Any] | None,
    *,
    attempt_count: int = 0,
    allow_llm_critic: bool = False,
    strictness: str = "normal",
) -> dict[str, Any]:
    prediction = prediction_result if isinstance(prediction_result, dict) else {}
    factor_payload = factors if isinstance(factors, dict) else {}
    data_quality = prediction.get("data_quality") or factor_payload.get("data_quality") or "unknown"
    calibration = (
        prediction.get("calibration_info")
        or factor_payload.get("confidence_calibration")
        or {}
    )
    explanation = (
        prediction.get("explanation_contributions")
        or factor_payload.get("explanation_contributions")
        or {}
    )
    supporting = []
    for item in explanation.get("items", []) if isinstance(explanation, dict) else []:
        if isinstance(item, dict):
            supporting.append({
                "source": item.get("label") or item.get("key"),
                "credibility": 0.7 if item.get("available", True) else 0.3,
                "direction": "supports",
                "details": item,
            })

    return {
        "domain": "world_cup",
        "subject": {
            "id": str(_getattr(match, "match_id", "")),
            "title": f"{_getattr(match, 'home_team', '')} vs {_getattr(match, 'away_team', '')}".strip(),
            "type": "match_prediction",
        },
        "conclusion": {
            "direction": None,
            "predicted_score": prediction.get("predicted_score"),
            "probabilities": prediction.get("outcome_probabilities"),
            "confidence": prediction.get("confidence"),
            "recommended_action": None,
        },
        "calculation_trace": {
            "method": prediction.get("prediction_method") or "unknown",
            "engine_used": prediction.get("engine_used"),
            "weights": factor_payload.get("integrated_weights") or {},
            "key_scores": {
                "elo_ratings": prediction.get("elo_ratings"),
                "has_betting_odds": prediction.get("has_betting_odds"),
            },
            "calibration": calibration,
        },
        "evidence": {
            "supporting": supporting,
            "opposing": [],
            "neutral": [],
            "data_quality": {
                "quality": data_quality,
                "score": _dict(factor_payload.get("data_quality_metrics")).get("quality_score"),
                "metrics": factor_payload.get("data_quality_metrics") or {},
            },
            "source_reliability": None,
        },
        "risk": {
            "level": "high" if _getattr(match, "stage", "") in {"round_of_16", "quarter_final", "semi_final", "final"} else "medium",
            "flags": [],
            "execution_constraints": {},
        },
        "options": {
            "max_recompute_attempts": 1,
            "strictness": strictness,
            "allow_llm_critic": allow_llm_critic,
        },
        "attempt_count": attempt_count,
    }


def apply_world_cup_challenge_result(
    prediction_result: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    prediction = dict(prediction_result)
    res = result if isinstance(result, dict) else {}
    factors = dict(prediction.get("factors") or {})
    factors["challenge_result"] = {
        "verdict": res.get("verdict"),
        "required_action": res.get("required_action"),
        "failed_checks": res.get("failed_checks", []),
        "warnings": res.get("warnings", []),
        "challenge_summary": res.get("challenge_summary", ""),
        "critic_notes": res.get("critic_notes", {}),
        "attempt_count": res.get("attempt_count", 0),
    }
    if res.get("required_action") in {"downgrade_to_wait", "enqueue_review"}:
        factors["requires_review"] = True
        prediction["high_confidence_selection"] = None
    cap = _dict(res.get("confidence_adjustment")).get("cap")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool):
        current = prediction.get("confidence")
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            prediction["confidence"] = min(float(current), float(cap))
    prediction["factors"] = factors
    return prediction
```

- [ ] **Step 4: Run adapter tests**

Run:

```powershell
cd backend
python -m pytest tests/test_conclusion_challenge_world_cup_adapter.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/services/conclusion_challenge_world_cup_adapter.py backend/tests/test_conclusion_challenge_world_cup_adapter.py
git commit -m "feat: add world cup conclusion challenge adapter"
```

---

### Task 4: Configuration And Trigger Vocabulary

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Modify: `backend/app/services/review_queue_detectors.py`
- Test: `backend/tests/test_conclusion_challenge_config.py`

**Interfaces:**
- Produces settings:
  - `CONCLUSION_CHALLENGE_ENABLED: bool`
  - `CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED: bool`
  - `CONCLUSION_CHALLENGE_STRICTNESS: str`
  - `CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS: int`
  - `WORLD_CUP_CHALLENGE_ENABLED: bool`
  - `EVENT_CHALLENGE_ENABLED: bool`

- [ ] **Step 1: Write config test**

Create `backend/tests/test_conclusion_challenge_config.py`:

```python
from app.core.config import settings


def test_conclusion_challenge_settings_exist_with_safe_defaults():
    assert settings.CONCLUSION_CHALLENGE_ENABLED is False
    assert settings.CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED is False
    assert settings.CONCLUSION_CHALLENGE_STRICTNESS == "normal"
    assert settings.CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS == 1
    assert settings.WORLD_CUP_CHALLENGE_ENABLED is False
    assert settings.EVENT_CHALLENGE_ENABLED is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd backend
python -m pytest tests/test_conclusion_challenge_config.py -q
```

Expected: `AttributeError` for missing settings.

- [ ] **Step 3: Add settings**

In `backend/app/core/config.py`, add near other feature flags:

```python
CONCLUSION_CHALLENGE_ENABLED: bool = _env_bool(
    "CONCLUSION_CHALLENGE_ENABLED", "false"
)
CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED: bool = _env_bool(
    "CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED", "false"
)
CONCLUSION_CHALLENGE_STRICTNESS: str = os.getenv(
    "CONCLUSION_CHALLENGE_STRICTNESS", "normal"
)
CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS: int = int(
    os.getenv("CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS", "1")
)
WORLD_CUP_CHALLENGE_ENABLED: bool = _env_bool(
    "WORLD_CUP_CHALLENGE_ENABLED", "false"
)
EVENT_CHALLENGE_ENABLED: bool = _env_bool(
    "EVENT_CHALLENGE_ENABLED", "false"
)
```

If `config.py` uses direct `os.getenv(...).lower() == "true"` instead of `_env_bool` in the target section, use the local helper already established in that file.

- [ ] **Step 4: Document env vars**

Add to `backend/.env.example`:

```env
# Conclusion challenge gate (default off)
CONCLUSION_CHALLENGE_ENABLED=false
CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED=false
CONCLUSION_CHALLENGE_STRICTNESS=normal
CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS=1
WORLD_CUP_CHALLENGE_ENABLED=false
EVENT_CHALLENGE_ENABLED=false
```

- [ ] **Step 5: Register trigger documentation**

In `backend/app/services/review_queue_detectors.py` top trigger comment, add:

```python
    conclusion_challenge_failed   conclusion challenge gate rejected or could
                                   not validate a strong conclusion. Enqueued
                                   by event/world-cup orchestrators; no
                                   _detect_* function here.
```

- [ ] **Step 6: Run config test**

Run:

```powershell
cd backend
python -m pytest tests/test_conclusion_challenge_config.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/core/config.py backend/.env.example backend/app/services/review_queue_detectors.py backend/tests/test_conclusion_challenge_config.py
git commit -m "feat: add conclusion challenge settings"
```

---

### Task 5: Main Event Intelligence Integration

**Files:**
- Modify: `backend/app/services/event_intelligence_service.py`
- Test: `backend/tests/test_event_intelligence_service.py`

**Interfaces:**
- Consumes:
  - `challenge_conclusion(payload)`
  - `build_event_challenge_input(record, attempt_count=..., allow_llm_critic=..., strictness=...)`
  - `apply_event_challenge_result(record, result)`
- Produces:
  - record field `conclusion_challenge`
  - review queue item trigger `conclusion_challenge_failed` when enabled and failure requires review

- [ ] **Step 1: Add focused integration tests**

Append tests in `backend/tests/test_event_intelligence_service.py` using monkeypatches already used in the file. If the file does not expose easy fixtures, create minimal tests for a helper introduced in Step 3.

Add tests for helper `_run_event_conclusion_challenge`:

```python
import pytest

from app.services import event_intelligence_service as svc


def _event_record():
    return {
        "event_id": "evt-1",
        "event_title": "Will X happen?",
        "probability": {"baseline": 40.0, "estimated": 62.0, "change": 22.0},
        "actionable_recommendation": {"direction": "YES", "confidence": "high", "risk_level": "medium"},
        "final_displayed_direction": "YES",
        "evidence_breakdown": [],
        "risk": {"level": "medium", "flags": []},
    }


def test_event_conclusion_challenge_flag_off_noops(monkeypatch):
    record = _event_record()
    monkeypatch.setattr(svc.settings, "CONCLUSION_CHALLENGE_ENABLED", False)
    monkeypatch.setattr(svc.settings, "EVENT_CHALLENGE_ENABLED", False)
    svc._run_event_conclusion_challenge(record, attempt_count=0)
    assert "conclusion_challenge" not in record
    assert record["final_displayed_direction"] == "YES"


def test_event_conclusion_challenge_reject_downgrades(monkeypatch):
    record = _event_record()
    monkeypatch.setattr(svc.settings, "CONCLUSION_CHALLENGE_ENABLED", True)
    monkeypatch.setattr(svc.settings, "EVENT_CHALLENGE_ENABLED", True)
    monkeypatch.setattr(svc.settings, "CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED", False)
    monkeypatch.setattr(svc.settings, "CONCLUSION_CHALLENGE_STRICTNESS", "normal")

    def fake_challenge(payload):
        return {
            "verdict": "reject",
            "required_action": "downgrade_to_wait",
            "failed_checks": [{"check": "counterevidence", "reason": "存在高可信反证"}],
            "warnings": [],
            "challenge_summary": "结论否定门结果：reject。主要原因：存在高可信反证",
            "critic_notes": {},
            "attempt_count": 0,
        }

    monkeypatch.setattr("app.services.conclusion_challenge_service.challenge_conclusion", fake_challenge)
    svc._run_event_conclusion_challenge(record, attempt_count=0)
    assert record["final_displayed_direction"] == "WAIT"
    assert record["conclusion_challenge"]["verdict"] == "reject"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd backend
python -m pytest tests/test_event_intelligence_service.py -q
```

Expected: failure because `_run_event_conclusion_challenge` does not exist.

- [ ] **Step 3: Add best-effort helper**

In `backend/app/services/event_intelligence_service.py`, add helper near `_build_all_overlays`:

```python
def _run_event_conclusion_challenge(
    record: dict[str, Any],
    *,
    attempt_count: int = 0,
) -> None:
    if not (
        settings.CONCLUSION_CHALLENGE_ENABLED
        and settings.EVENT_CHALLENGE_ENABLED
    ):
        return
    try:
        from app.services.conclusion_challenge_event_adapter import (
            apply_event_challenge_result,
            build_event_challenge_input,
        )
        from app.services.conclusion_challenge_service import challenge_conclusion

        payload = build_event_challenge_input(
            record,
            attempt_count=attempt_count,
            allow_llm_critic=settings.CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED,
            strictness=settings.CONCLUSION_CHALLENGE_STRICTNESS,
        )
        result = challenge_conclusion(payload)
        apply_event_challenge_result(record, result)
    except Exception as exc:
        logger.warning("conclusion challenge failed: %s", exc, exc_info=True)
        record["conclusion_challenge"] = {
            "verdict": "pass_with_warnings",
            "required_action": "allow_output",
            "failed_checks": [],
            "warnings": [{
                "check": "challenge_error",
                "severity": "warning",
                "reason": "结论否定门执行失败，已保留原结论。",
                "details": {"error": str(exc)},
            }],
            "challenge_summary": "结论否定门执行失败，已保留原结论。",
            "critic_notes": {},
            "attempt_count": attempt_count,
        }
```

- [ ] **Step 4: Call helper before review queue detectors**

In `_build_all_overlays`, after guardrail evaluation and before the review queue detector block, add:

```python
    _run_event_conclusion_challenge(record, attempt_count=0)
```

Do not run it before guardrails; it must inspect the post-guardrail direction.

- [ ] **Step 5: Enqueue conclusion challenge failures**

Inside the existing review queue block, after existing `detect_review_candidates(...)` assignment and before iterating, append a candidate when challenge failed:

```python
                challenge = record.get("conclusion_challenge")
                if isinstance(challenge, dict) and challenge.get("verdict") in {
                    "reject",
                    "insufficient_evidence",
                    "revise",
                }:
                    candidates.append({
                        "trigger": "conclusion_challenge_failed",
                        "severity": "ERROR" if challenge.get("verdict") in {"reject", "insufficient_evidence"} else "WARN",
                        "reason": challenge.get("challenge_summary") or "结论未通过否定门检查",
                        "context": {
                            "verdict": challenge.get("verdict"),
                            "required_action": challenge.get("required_action"),
                            "failed_checks": challenge.get("failed_checks", []),
                        },
                    })
```

The existing best-effort enqueue loop handles store errors.

- [ ] **Step 6: Run focused event tests**

Run:

```powershell
cd backend
python -m pytest tests/test_event_intelligence_service.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/services/event_intelligence_service.py backend/tests/test_event_intelligence_service.py
git commit -m "feat: integrate conclusion challenge for events"
```

---

### Task 6: World Cup Pipeline Integration

**Files:**
- Modify: `backend/app/services/world_cup_prediction_pipeline.py`
- Test: `backend/tests/test_world_cup_prediction_pipeline.py`

**Interfaces:**
- Consumes:
  - `build_world_cup_challenge_input(...)`
  - `challenge_conclusion(...)`
  - `apply_world_cup_challenge_result(...)`
- Produces:
  - `prediction_result["factors"]["challenge_result"]`
  - conservative confidence cap on failure
  - no high-confidence semantics on rejection

- [ ] **Step 1: Add focused helper tests**

In `backend/tests/test_world_cup_prediction_pipeline.py`, add tests for helper `_run_world_cup_conclusion_challenge`:

```python
from types import SimpleNamespace

from app.services import world_cup_prediction_pipeline as pipeline


def _match():
    return SimpleNamespace(match_id="wc-1", home_team="A", away_team="B", stage="group_stage")


def _prediction():
    return {
        "predicted_score": {"home": 2.0, "away": 1.0},
        "outcome_probabilities": {"home_win": 0.52, "draw": 0.24, "away_win": 0.24},
        "confidence": 0.82,
        "prediction_method": "integrated",
        "high_confidence_selection": {"selected_engine": "integrated"},
        "factors": {"data_quality": "real"},
    }


def test_world_cup_challenge_flag_off_noops(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "CONCLUSION_CHALLENGE_ENABLED", False)
    monkeypatch.setattr(pipeline.settings, "WORLD_CUP_CHALLENGE_ENABLED", False)
    result = pipeline._run_world_cup_conclusion_challenge(
        _match(), _prediction(), attempt_count=0
    )
    assert "challenge_result" not in result["factors"]
    assert result["confidence"] == 0.82


def test_world_cup_challenge_reject_caps_confidence(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "CONCLUSION_CHALLENGE_ENABLED", True)
    monkeypatch.setattr(pipeline.settings, "WORLD_CUP_CHALLENGE_ENABLED", True)
    monkeypatch.setattr(pipeline.settings, "CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED", False)
    monkeypatch.setattr(pipeline.settings, "CONCLUSION_CHALLENGE_STRICTNESS", "normal")

    def fake_challenge(payload):
        return {
            "verdict": "reject",
            "required_action": "downgrade_to_wait",
            "failed_checks": [{"check": "counterevidence", "reason": "赔率反向"}],
            "warnings": [],
            "confidence_adjustment": {"cap": 0.60, "reason": "否定门未通过"},
            "challenge_summary": "结论否定门结果：reject。主要原因：赔率反向",
            "critic_notes": {},
            "attempt_count": 0,
        }

    monkeypatch.setattr("app.services.conclusion_challenge_service.challenge_conclusion", fake_challenge)
    result = pipeline._run_world_cup_conclusion_challenge(
        _match(), _prediction(), attempt_count=0
    )
    assert result["confidence"] == 0.60
    assert result["high_confidence_selection"] is None
    assert result["factors"]["challenge_result"]["verdict"] == "reject"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd backend
python -m pytest tests/test_world_cup_prediction_pipeline.py -q
```

Expected: failure because `_run_world_cup_conclusion_challenge` does not exist.

- [ ] **Step 3: Add helper**

In `backend/app/services/world_cup_prediction_pipeline.py`, add helper near other local pipeline helpers:

```python
def _run_world_cup_conclusion_challenge(
    match: MatchFixture,
    prediction_result: dict[str, Any],
    *,
    attempt_count: int = 0,
) -> dict[str, Any]:
    if not (
        settings.CONCLUSION_CHALLENGE_ENABLED
        and settings.WORLD_CUP_CHALLENGE_ENABLED
    ):
        return prediction_result
    try:
        from app.services.conclusion_challenge_service import challenge_conclusion
        from app.services.conclusion_challenge_world_cup_adapter import (
            apply_world_cup_challenge_result,
            build_world_cup_challenge_input,
        )

        factors = prediction_result.get("factors")
        payload = build_world_cup_challenge_input(
            match,
            prediction_result,
            factors if isinstance(factors, dict) else {},
            attempt_count=attempt_count,
            allow_llm_critic=settings.CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED,
            strictness=settings.CONCLUSION_CHALLENGE_STRICTNESS,
        )
        result = challenge_conclusion(payload)
        return apply_world_cup_challenge_result(prediction_result, result)
    except Exception as exc:
        logger.warning("world cup conclusion challenge failed: %s", exc, exc_info=True)
        updated = dict(prediction_result)
        factors = dict(updated.get("factors") or {})
        factors["challenge_result"] = {
            "verdict": "pass_with_warnings",
            "required_action": "allow_output",
            "failed_checks": [],
            "warnings": [{
                "check": "challenge_error",
                "severity": "warning",
                "reason": "结论否定门执行失败，已保留原预测。",
                "details": {"error": str(exc)},
            }],
            "challenge_summary": "结论否定门执行失败，已保留原预测。",
            "critic_notes": {},
            "attempt_count": attempt_count,
        }
        updated["factors"] = factors
        return updated
```

- [ ] **Step 4: Call helper before compare-only/persistence returns**

In `run_prediction_pipeline`, after Step 4g tactical analysis and before compare-only handling, add:

```python
        prediction_result = _run_world_cup_conclusion_challenge(
            match,
            prediction_result,
            attempt_count=0,
        )
```

This ensures compare-only responses also expose challenge metadata when the feature is enabled, while persistence uses the same challenged result.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
cd backend
python -m pytest tests/test_world_cup_prediction_pipeline.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/services/world_cup_prediction_pipeline.py backend/tests/test_world_cup_prediction_pipeline.py
git commit -m "feat: integrate conclusion challenge for world cup"
```

---

### Task 7: Regression And Smoke Verification

**Files:**
- No new source files unless prior tasks reveal small test fixes.

**Interfaces:**
- Consumes all previous tasks.
- Produces verified branch state.

- [ ] **Step 1: Run pure challenge tests**

Run:

```powershell
cd backend
python -m pytest `
  tests/test_conclusion_challenge_service.py `
  tests/test_conclusion_challenge_event_adapter.py `
  tests/test_conclusion_challenge_world_cup_adapter.py `
  tests/test_conclusion_challenge_config.py `
  -q
```

Expected: pass.

- [ ] **Step 2: Run affected backend tests**

Run:

```powershell
cd backend
python -m pytest `
  tests/test_event_intelligence_service.py `
  tests/test_world_cup_prediction_pipeline.py `
  tests/test_review_queue_detectors.py `
  tests/test_guardrail_service.py `
  tests/test_decision_quality_service.py `
  -q
```

Expected: pass.

- [ ] **Step 3: Run full backend regression**

Run:

```powershell
cd backend
python -m pytest -q
```

Expected: pass. If unrelated failures appear, capture the failing test names and error summaries before fixing anything.

- [ ] **Step 4: Run frontend typecheck**

Run:

```powershell
cd frontend
npm.cmd run typecheck
```

Expected: pass. This should be no-op for frontend behavior unless generated/shared types changed.

- [ ] **Step 5: Check git state**

Run:

```powershell
git status --short
git log --oneline -5
```

Expected: only intentional files are modified/staged, and recent commits correspond to Tasks 1-6.

- [ ] **Step 6: Final verification commit if needed**

If Task 7 required small fixes, commit them:

```powershell
git add backend/app backend/tests backend/.env.example
git commit -m "test: verify conclusion challenge integration"
```

If no fixes were required, do not create an empty commit.

---

## Self-Review

- Spec coverage:
  - Shared pure gate: Task 1.
  - Five fixed checks: Task 1.
  - Deterministic verdict ownership: Task 1.
  - Event adapter and downgrade behavior: Task 2 and Task 5.
  - World Cup adapter and confidence cap behavior: Task 3 and Task 6.
  - Disabled-by-default flags: Task 4.
  - Review queue trigger: Task 4 and Task 5.
  - LLM critic optional seam: Task 1, Task 5, Task 6.
  - One-attempt loop prevention: Task 1 via `attempt_count`; orchestrator helpers pass attempt count.

- Placeholder scan:
  - The plan contains no unresolved markers or unspecified implementation steps.

- Type consistency:
  - `challenge_conclusion(payload, critic_adapter=None)` is consumed consistently by adapters and integrations.
  - Adapter result keys match the shared `ChallengeResult` shape.
  - Config names match the spec exactly.
