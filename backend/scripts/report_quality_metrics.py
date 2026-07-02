"""Sliced quality metrics report over resolved events (NEXT #3).

Distinct from sweep_event_quality.py:
  - sweep_event_quality: anomaly hunting (finds bad events one-by-one)
  - report_quality_metrics: aggregate analytics (slices the resolved set by
    dimension and computes direction accuracy, mean Brier, and calibration
    deviation per slice)

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

Pure read-only: no writes, no LLM calls, no network.

Usage:
    python -m scripts.report_quality_metrics
    python -m scripts.report_quality_metrics --limit 50
    python -m scripts.report_quality_metrics --sample 50
    python -m scripts.report_quality_metrics --json
"""
from __future__ import annotations

import argparse
import io
import json
import math
import random
import sys
from collections import defaultdict
from typing import Any

# UTF-8 stdout for Windows GBK console safety (same convention as
# sweep_event_quality.py / diagnose_event_quality.py).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass

# Make backend importable when run as a script.
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def _print(text: str) -> None:
    """Print with UTF-8 stdout (Windows GBK safety)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


# ─── Collection ────────────────────────────────────────────────────────────

def _collect_entries(limit: int | None, sample: int | None) -> list[dict[str, Any]]:
    """Load resolved events from event_store, optionally limited/sampled.

    list_resolved_events already filters to outcome.status == "resolved"
    (or missing status, which defaults to resolved). Non-resolved statuses
    (e.g. "invalid") are excluded — they record the marker but are not
    scored, so including them would pollute direction_accuracy / Brier.
    """
    from app.memory import event_store
    entries = event_store.list_resolved_events()
    if sample is not None and sample < len(entries):
        # Reproducible sample for auditability — seed fixed (same convention
        # as sweep_event_quality.py).
        rng = random.Random(42)
        entries = rng.sample(entries, sample)
    if limit is not None:
        entries = entries[:limit]
    return entries


# ─── Per-event extraction ──────────────────────────────────────────────────

def _safe_float(value: Any) -> float | None:
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


def _sr_bucket(score: Any) -> str:
    """Bucket source_reliability.overall_score (0.0-1.0 typical range).

    Buckets: low(<0.4) / medium(0.4-0.6) / high(0.6-0.8) / very_high(>=0.8).
    Half-open intervals [0.4, 0.6) etc. None / non-numeric → <missing>.
    """
    s = _safe_float(score)
    if s is None:
        return "<missing>"
    if s < 0.4:
        return "low(<0.4)"
    if s < 0.6:
        return "medium(0.4-0.6)"
    if s < 0.8:
        return "high(0.6-0.8)"
    return "very_high(>=0.8)"


# Probability buckets for calibration deviation table. Half-open [lo, hi).
_PROB_BUCKETS: list[tuple[float, float, str]] = [
    (0.0, 20.0, "0-20"),
    (20.0, 40.0, "20-40"),
    (40.0, 60.0, "40-60"),
    (60.0, 80.0, "60-80"),
    (80.0, 101.0, "80-100"),
]


def _extract_metrics(record: dict[str, Any]) -> dict[str, Any]:
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
        "source_reliability_bucket": _sr_bucket(sr.get("overall_score") if isinstance(sr, dict) else None),
        "direction_correct": direction_correct,
        "brier_score": _safe_float(cal.get("brier_score")) if isinstance(cal, dict) else None,
        "estimated_probability": _safe_float(cal.get("estimated_probability")) if isinstance(cal, dict) else None,
        "actual_outcome": _safe_float(actual) if is_resolved else None,
    }


# ─── Aggregation ───────────────────────────────────────────────────────────

def _slice_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
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

    briers = [
        i["brier_score"] for i in items
        if i["brier_score"] is not None
    ]
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


def _group_by(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    """Group items by item[key], compute _slice_metrics per group."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[str(item.get(key, "<missing>"))].append(item)
    return {k: _slice_metrics(v) for k, v in groups.items()}


def _calibration_deviation(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _build_report(items: list[dict[str, Any]], report_errors: list[dict[str, str]]) -> dict[str, Any]:
    """Assemble the full report dict from extracted per-event metrics."""
    total = len(items)
    with_cal = sum(1 for i in items if i["brier_score"] is not None)

    return {
        "overview": {
            "total_resolved": total,
            "with_calibration": with_cal,
            "missing_calibration": total - with_cal,
        },
        "by_source_type": _group_by(items, "source_type"),
        "by_analysis_quality": _group_by(items, "analysis_quality"),
        "by_edge_bucket": _group_by(items, "edge_bucket"),
        "by_source_reliability_bucket": _group_by(items, "source_reliability_bucket"),
        "calibration_deviation": _calibration_deviation(items),
        "report_errors": report_errors,
    }


# ─── Rendering ─────────────────────────────────────────────────────────────

def _render_slice_table(title: str, slices: dict[str, dict[str, Any]]) -> list[str]:
    """Render one slice dimension as a table. Returns lines for _render_text."""
    lines: list[str] = [title]
    lines.append("─" * 70)
    # Fixed columns: slice | n | acc | true/false/none | brier | grade
    lines.append(f"  {'slice':<24} {'n':>4} {'acc':>6} {'T/F/N':>10} {'brier':>7} {'grade':<14}")
    # Sort by n desc so the biggest slices surface first.
    for key in sorted(slices.keys(), key=lambda k: -slices[k]["n"]):
        s = slices[key]
        acc = "  -  " if s["direction_accuracy"] is None else f"{s['direction_accuracy']:.2f}"
        tfn = f"{s['direction_correct_true']}/{s['direction_correct_false']}/{s['direction_correct_none']}"
        brier = s["brier"]["brier_score"]
        brier_str = "  -  " if brier is None else f"{brier:.4f}"
        grade = s["brier"]["grade"]
        lines.append(f"  {key:<24} {s['n']:>4} {acc:>6} {tfn:>10} {brier_str:>7} {grade:<14}")
    lines.append("")
    return lines


def _render_text(report: dict[str, Any]) -> str:
    """Render human-readable report."""
    lines: list[str] = []
    ov = report["overview"]
    lines.append(f"Quality Metrics Report — {ov['total_resolved']} resolved events")
    lines.append("═" * 70)
    lines.append("")
    lines.append("📊 Overview")
    lines.append("─" * 40)
    lines.append(f"  total_resolved:     {ov['total_resolved']}")
    lines.append(f"  with_calibration:   {ov['with_calibration']}")
    lines.append(f"  missing_calibration: {ov['missing_calibration']}")
    lines.append("")

    lines.extend(_render_slice_table(
        "📊 By source_type (record.source.type)", report["by_source_type"]))
    lines.extend(_render_slice_table(
        "📊 By analysis_quality (engine proxy — llm vs deterministic_fallback)",
        report["by_analysis_quality"]))
    lines.extend(_render_slice_table(
        "📊 By edge_bucket", report["by_edge_bucket"]))
    lines.extend(_render_slice_table(
        "📊 By source_reliability_bucket (overall_score)", report["by_source_reliability_bucket"]))

    # Calibration deviation table
    lines.append("📊 Calibration deviation (by estimated_probability bucket)")
    lines.append("─" * 70)
    lines.append(f"  {'bucket':<10} {'n':>4} {'pred%':>7} {'actual%':>8} {'dev':>7}")
    for row in report["calibration_deviation"]:
        pred = "  -  " if row["predicted_mean"] is None else f"{row['predicted_mean']:.2f}"
        act = "  -  " if row["actual_mean"] is None else f"{row['actual_mean']:.2f}"
        dev = "  -  " if row["deviation"] is None else f"{row['deviation']:+.2f}"
        lines.append(f"  {row['bucket']:<10} {row['n']:>4} {pred:>7} {act:>8} {dev:>7}")
    lines.append("")
    lines.append("  (deviation = predicted - actual; positive = overconfident)")
    lines.append("")

    if report["report_errors"]:
        lines.append(f"⚠️  report_errors: {len(report['report_errors'])}")
        for err in report["report_errors"][:5]:
            lines.append(f"  {err['event_id']}: {err['error']}")
        lines.append("")

    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code.

    Exit codes: 0 success, 2 other errors (load/extract failure).
    Missing calibration / non-directional events are NOT errors — they're
    reported as None metrics.
    """
    parser = argparse.ArgumentParser(
        prog="report_quality_metrics",
        description=(
            "Sliced quality metrics over resolved events. Reports direction "
            "accuracy, mean Brier, and calibration deviation per slice "
            "(source_type, analysis_quality, edge_bucket, source_reliability)."
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Report on only the first N resolved events (default: all)",
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Randomly sample N resolved events (reproducible seed=42)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON instead of human-readable text",
    )
    args = parser.parse_args(argv)

    try:
        entries = _collect_entries(args.limit, args.sample)
    except Exception as exc:
        print(f"Error: failed to load events: {exc}", file=sys.stderr)
        return 2

    if not entries:
        if args.json:
            _print(json.dumps({
                "overview": {"total_resolved": 0, "with_calibration": 0, "missing_calibration": 0},
                "by_source_type": {},
                "by_analysis_quality": {},
                "by_edge_bucket": {},
                "by_source_reliability_bucket": {},
                "calibration_deviation": [],
                "report_errors": [],
                "message": "no resolved events found in event_store",
            }, indent=2, ensure_ascii=False))
        else:
            _print("[INFO] No resolved events found in event_store.")
        return 0

    # Extract per-event metrics. Single-event failure must not abort the
    # whole report (same resilience contract as sweep_event_quality).
    items: list[dict[str, Any]] = []
    report_errors: list[dict[str, str]] = []
    for entry in entries:
        record = entry.get("record")
        if not isinstance(record, dict):
            report_errors.append({
                "event_id": entry.get("event_id", "?"),
                "error": "record missing or not a dict",
            })
            continue
        try:
            items.append(_extract_metrics(record))
        except Exception as exc:
            report_errors.append({
                "event_id": record.get("event_id", "?"),
                "error": str(exc),
            })

    try:
        report = _build_report(items, report_errors)
    except Exception as exc:
        print(f"Error: report build failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        _print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    else:
        _print(_render_text(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
