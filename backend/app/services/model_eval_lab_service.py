"""Model evaluation lab — pure functions for slicing resolved events by
model / analysis_quality / degraded_mode (Plan §4.6).

Read-only: no LLM calls, no writes, no network. Reuses extract_metrics /
slice_metrics / calibration_deviation from quality_metrics_report_service
(calls them, does not copy logic) to preserve direction / Brier / edge
semantics. Appends model / cost / guardrail / ECE on top.
"""
from __future__ import annotations

import math
from typing import Any

from app.services.quality_metrics_report_service import extract_metrics

# Probability buckets for ECE (0-100 scale). Last upper bound 101.0 so
# estimated_probability == 100.0 is included with `< hi`.
_PROB_BUCKETS = [(0.0, 20.0), (20.0, 40.0), (40.0, 60.0), (60.0, 80.0), (80.0, 101.0)]


def extract_model_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Extract model-eval metrics from a record.

    Calls existing extract_metrics (preserves direction/Brier/edge
    semantics), then appends model / degraded_mode / degraded_mode_label
    / estimated_token_cost / guardrail_fired.

    model source: llm_telemetry.model, missing -> "unknown".
    Never infer model from current settings (would pollute historical
    attribution).

    cost: _is_real_number rejects None/NaN/inf/bool/strings -> None.
    """
    item = extract_metrics(record)
    llm = record.get("llm_telemetry") or {}
    if not isinstance(llm, dict):
        llm = {}
    item["model"] = llm.get("model") or "unknown"
    item["degraded_mode"] = bool(llm.get("degraded_mode", False))
    item["degraded_mode_label"] = "degraded" if item["degraded_mode"] else "normal"
    cost_raw = llm.get("estimated_token_cost")
    item["estimated_token_cost"] = float(cost_raw) if _is_real_number(cost_raw) else None
    guardrails = record.get("guardrail_fired")
    item["guardrail_fired"] = guardrails if isinstance(guardrails, list) else []
    return item


def compute_ece(items: list[dict[str, Any]]) -> float | None:
    """Expected Calibration Error (0-100 scale).

    Formula: sum(bucket_n / total_n * abs(predicted_mean - actual_mean))
    Only counts records with both estimated_probability and actual_outcome
    as real numbers (bool excluded — bool is int subclass in Python).
    Returns None when no eligible records.

    Scale: 0-100 probability points (consistent with calibration_deviation).
    """
    eligible = [
        it for it in items
        if _is_real_number(it.get("estimated_probability"))
        and _is_real_number(it.get("actual_outcome"))
    ]
    total = len(eligible)
    if total == 0:
        return None
    ece = 0.0
    for lo, hi in _PROB_BUCKETS:
        bucket = [
            it for it in eligible
            if lo <= it["estimated_probability"] < hi
        ]
        if not bucket:
            continue
        bucket_n = len(bucket)
        predicted_mean = sum(it["estimated_probability"] for it in bucket) / bucket_n
        actual_mean = sum(it["actual_outcome"] for it in bucket) / bucket_n
        ece += (bucket_n / total) * abs(predicted_mean - actual_mean)
    return ece


def _is_real_number(value: Any) -> bool:
    """True only for int/float that is not bool and is finite."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


from app.services.quality_metrics_report_service import (
    calibration_deviation,
    slice_metrics,
)


def slice_model_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Extended slice_metrics with ECE, cost, and guardrail aggregations.

    Inherits all fields from slice_metrics (n, direction_correct_*,
    brier, missing_calibration_rate, direction_accuracy), then adds:
        ece                  — float | None
        cost_total           — float (0.0 when no cost data)
        cost_avg             — float | None (None when cost_n == 0)
        cost_n               — int (count of non-None costs)
        guardrail_count      — int
        guardrail_rate       — float (0.0-1.0)
        degraded_count       — int
        degraded_rate        — float (0.0-1.0)
    """
    base = slice_metrics(items)  # from quality_metrics_report_service
    cost_values = [
        it["estimated_token_cost"]
        for it in items
        if it.get("estimated_token_cost") is not None
    ]
    cost_total = sum(cost_values) if cost_values else 0.0
    cost_n = len(cost_values)
    cost_avg = cost_total / cost_n if cost_n else None
    guardrail_count = sum(1 for it in items if it.get("guardrail_fired"))
    guardrail_rate = guardrail_count / len(items) if items else 0.0
    degraded_count = sum(1 for it in items if it.get("degraded_mode"))
    degraded_rate = degraded_count / len(items) if items else 0.0
    return {
        **base,
        "ece": compute_ece(items),
        "cost_total": cost_total,
        "cost_avg": cost_avg,
        "cost_n": cost_n,
        "guardrail_count": guardrail_count,
        "guardrail_rate": guardrail_rate,
        "degraded_count": degraded_count,
        "degraded_rate": degraded_rate,
    }


def _group_by(
    items: list[dict[str, Any]],
    key: str,
) -> dict[str, list[dict[str, Any]]]:
    """Group items by a flat key on the item dict. Local helper — does
    not touch quality_metrics_report_service.group_by (which hardcodes
    slice_metrics and would drop cost/guardrail/ECE)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        k = str(it.get(key, "unknown"))
        groups.setdefault(k, []).append(it)
    return groups


def group_model_slices(
    items: list[dict[str, Any]],
    key: str,
    *,
    min_samples: int = 0,
) -> dict[str, dict[str, Any]]:
    """Group items by key, slice each group with slice_model_metrics.

    Groups with fewer than min_samples are still computed but flagged
    ``insufficient_samples: True`` (not dropped — caller decides).
    """
    groups = _group_by(items, key)
    result: dict[str, dict[str, Any]] = {}
    for k, group_items in groups.items():
        slice_data = slice_model_metrics(group_items)
        slice_data["insufficient_samples"] = len(group_items) < min_samples
        result[k] = slice_data
    return result


def build_model_eval_report(
    items: list[dict[str, Any]],
    report_errors: list[dict[str, Any]],
    *,
    min_samples: int = 0,
) -> dict[str, Any]:
    """Build the full model evaluation report.

    overview always computed from ALL items (min_samples does NOT filter
    overview). by_model / by_analysis_quality / by_degraded_mode use
    group_model_slices with min_samples flagging (not filtering).
    """
    overview = slice_model_metrics(items)
    return {
        "overview": overview,
        "by_model": group_model_slices(items, "model", min_samples=min_samples),
        "by_analysis_quality": group_model_slices(
            items, "analysis_quality", min_samples=min_samples,
        ),
        "by_degraded_mode": group_model_slices(
            items, "degraded_mode_label", min_samples=min_samples,
        ),
        "calibration_deviation": calibration_deviation(items),
        "report_errors": report_errors,
        "min_samples": min_samples,
    }
