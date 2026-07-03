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
