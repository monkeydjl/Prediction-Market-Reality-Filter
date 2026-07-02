"""Quality metrics report service (NEXT #3).

Pure functions that build the sliced quality metrics report over resolved
events. Extracted from ``scripts/report_quality_metrics.py`` so both the
CLI script and the ``/api/quality-metrics/report`` route share one
implementation.

Slices computed:
  1. by_source_type          — record.source.type (prediction_market /
     prediction_question / sports_event / open_web / manual / unknown)
  2. by_analysis_quality     — record.llm_telemetry.analysis_quality
     (llm / deterministic_fallback / unknown). This is the closest available
     proxy for "which engine produced the record" — there is no true engine
     field on EventRecord.
  3. by_edge_bucket          — compute_edge_bucket(actionable_recommendation.edge)
     (0-5 / 5-10 / 10-20 / 20+ / <missing>)
  4. by_source_reliability   — source_reliability.overall_score bucketed into
     low(<0.4) / medium(0.4-0.6) / high(0.6-0.8) / very_high(>=0.8) / <missing>

Per-slice metrics:
  - n, direction_correct_true/false/none counts
  - direction_accuracy = true / (true + false)  (None when no directional)
  - brier block (mean brier / skill / grade / n) aggregated from
    record.calibration.brier_score (already computed at resolve time)

Plus a calibration_deviation table: events grouped by estimated_probability
bucket [0,20)/[20,40)/[40,60)/[60,80)/[80,100], showing mean predicted vs
mean actual and their deviation (predicted - actual). Positive deviation =
overconfident; negative = underconfident.

Pure and deterministic: no I/O, no network, no LLM calls. The caller
(event_store / API route / CLI) supplies the data; this module only does
the math. Mirrors the contract of ``calibration_service_event`` — same
vocabulary, same reuse of canonical pure functions
(``compute_edge_bucket`` / ``compute_direction_correct`` /
``calibration_service_event._aggregate``).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

# Probability buckets for calibration deviation table. Half-open [lo, hi).
_PROB_BUCKETS: list[tuple[float, float, str]] = [
    (0.0, 20.0, "0-20"),
    (20.0, 40.0, "20-40"),
    (40.0, 60.0, "40-60"),
    (60.0, 80.0, "60-80"),
    (80.0, 101.0, "80-100"),
]


def safe_float(value: Any) -> float | None:
    """Parse value to float, returning None for None/NaN/inf/non-numeric."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def bucket_source_reliability(score: Any) -> str:
    """Bucket source_reliability.overall_score (0.0-1.0 typical range).

    Buckets: low(<0.4) / medium(0.4-0.6) / high(0.6-0.8) / very_high(>=0.8).
    Half-open intervals [0.4, 0.6) etc. None / non-numeric → <missing>.
    """
    s = safe_float(score)
    if s is None:
        return "<missing>"
    if s < 0.4:
        return "low(<0.4)"
    if s < 0.6:
        return "medium(0.4-0.6)"
    if s < 0.8:
        return "high(0.6-0.8)"
    return "very_high(>=0.8)"


def extract_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Extract per-event metrics needed for sliced reporting.

    Reuses the canonical pure functions from prediction_calibration_service
    (NOT hand-rolled) so edge_bucket / direction_correct match the
    calibration system's semantics exactly.

    direction_correct is None when:
      - recommendation is WAIT/AVOID (non-directional)
      - outcome is not resolved (status != "resolved")
    Otherwise True/False vs outcome.actual_outcome.
    """
    from app.services.prediction_calibration_service import (
        compute_direction_correct,
        compute_edge_bucket,
    )

    source = record.get("source") or {}
    lt = record.get("llm_telemetry") or {}
    rec = record.get("actionable_recommendation") or {}
    outcome = record.get("outcome") or {}
    cal = record.get("calibration") or {}
    sr = record.get("source_reliability") or {}

    # Mirror event_store.list_resolved_events / diagnose_event_quality:
    # only status == "resolved" (or missing status) is scored. Non-resolved
    # statuses record the marker but are NOT scored.
    is_resolved = outcome.get("status", "resolved") == "resolved"
    actual = outcome.get("actual_outcome") if is_resolved else None

    rec_dir = rec.get("direction") if isinstance(rec, dict) else None
    direction_correct = compute_direction_correct(rec_dir, actual)

    return {
        "event_id": record.get("event_id", "?"),
        "source_type": source.get("type") or "unknown" if isinstance(source, dict) else "unknown",
        "analysis_quality": lt.get("analysis_quality") or "unknown" if isinstance(lt, dict) else "unknown",
        "edge_bucket": compute_edge_bucket(rec.get("edge")) or "<missing>",
        "source_reliability_bucket": bucket_source_reliability(sr.get("overall_score") if isinstance(sr, dict) else None),
        "direction_correct": direction_correct,
        "brier_score": safe_float(cal.get("brier_score")) if isinstance(cal, dict) else None,
        "estimated_probability": safe_float(cal.get("estimated_probability")) if isinstance(cal, dict) else None,
        "actual_outcome": safe_float(actual) if is_resolved else None,
    }


def slice_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute metrics for one slice (group of events sharing a dimension).

    direction_accuracy = true / (true + false). None when no directional
    events (all WAIT/AVOID or non-resolved).

    Brier block uses calibration_service_event._aggregate — the same pure
    aggregator used by summarize() at resolve time — so the report's mean
    Brier / skill / grade match the calibration system's semantics.
    """
    from app.services.calibration_service_event import _aggregate as aggregate_briers

    n = len(items)
    dc_true = sum(1 for i in items if i["direction_correct"] is True)
    dc_false = sum(1 for i in items if i["direction_correct"] is False)
    dc_none = sum(1 for i in items if i["direction_correct"] is None)
    directional = dc_true + dc_false
    accuracy = (dc_true / directional) if directional > 0 else None

    briers = [i["brier_score"] for i in items if i["brier_score"] is not None]
    brier_block = aggregate_briers(briers) if briers else {
        "brier_score": None, "skill_score": None, "grade": "no_data", "n": 0,
    }

    return {
        "n": n,
        "direction_correct_true": dc_true,
        "direction_correct_false": dc_false,
        "direction_correct_none": dc_none,
        "direction_accuracy": round(accuracy, 4) if accuracy is not None else None,
        "brier": brier_block,
    }


def group_by(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    """Group items by item[key], compute slice_metrics per group."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[str(item.get(key, "<missing>"))].append(item)
    return {k: slice_metrics(v) for k, v in groups.items()}


def calibration_deviation(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per probability bucket: mean(predicted%) vs mean(actual%), deviation.

    Only events with both estimated_probability and actual_outcome present
    contribute. Positive deviation = overconfident (predicted higher than
    realized); negative = underconfident.
    """
    out: list[dict[str, Any]] = []
    for lo, hi, label in _PROB_BUCKETS:
        bucket = [
            i for i in items
            if i["estimated_probability"] is not None
            and i["actual_outcome"] is not None
            and lo <= i["estimated_probability"] < hi
        ]
        if not bucket:
            out.append({
                "bucket": label, "n": 0,
                "predicted_mean": None, "actual_mean": None, "deviation": None,
            })
            continue
        preds = [i["estimated_probability"] for i in bucket]
        acts = [i["actual_outcome"] for i in bucket]
        pred_mean = sum(preds) / len(preds)
        act_mean = sum(acts) / len(acts)
        out.append({
            "bucket": label,
            "n": len(bucket),
            "predicted_mean": round(pred_mean, 2),
            "actual_mean": round(act_mean, 2),
            "deviation": round(pred_mean - act_mean, 2),
        })
    return out


def build_report(items: list[dict[str, Any]], report_errors: list[dict[str, str]]) -> dict[str, Any]:
    """Assemble the full report dict from extracted per-event metrics."""
    total = len(items)
    with_cal = sum(1 for i in items if i["brier_score"] is not None)

    return {
        "overview": {
            "total_resolved": total,
            "with_calibration": with_cal,
            "missing_calibration": total - with_cal,
        },
        "by_source_type": group_by(items, "source_type"),
        "by_analysis_quality": group_by(items, "analysis_quality"),
        "by_edge_bucket": group_by(items, "edge_bucket"),
        "by_source_reliability_bucket": group_by(items, "source_reliability_bucket"),
        "calibration_deviation": calibration_deviation(items),
        "report_errors": report_errors,
    }
