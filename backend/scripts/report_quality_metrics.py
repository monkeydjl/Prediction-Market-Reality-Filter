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
import random
import sys
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

# Pure functions live in the service module so the CLI script and the
# /api/quality-metrics/report route share one implementation. Re-exported
# here under the script's original private names so existing tests
# (`from report_quality_metrics import _extract_metrics`) keep working.
from app.services.quality_metrics_report_service import (  # noqa: E402
    build_report as _build_report,
    extract_metrics as _extract_metrics,
)


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
