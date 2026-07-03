"""Model evaluation lab CLI (Plan §4.6).

Pure read-only slicing of resolved events by model / analysis_quality /
degraded_mode. Reports Brier / ECE / direction accuracy / cost / guardrail
rate per group. Distinct from report_quality_metrics.py (which slices by
source_type / analysis_quality / edge_bucket / source_reliability).

Usage:
    python -m scripts.model_eval_lab
    python -m scripts.model_eval_lab --sample 50
    python -m scripts.model_eval_lab --event-ids evt-001,evt-002
    python -m scripts.model_eval_lab --min-samples 10 --json
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from typing import Any

# UTF-8 stdout for Windows GBK console safety.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass

from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.services.model_eval_lab_service import (  # noqa: E402
    build_model_eval_report,
    extract_model_metrics,
)


def _print(text: str) -> None:
    """Print with UTF-8 stdout (Windows GBK safety)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


# ─── Collection ────────────────────────────────────────────────────────────

def _collect_entries(
    sample: int | None,
    event_ids: list[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load resolved events from event_store, extract model metrics.

    Returns (items, report_errors).
    report_errors only records:
      - record is not a dict
      - extract_model_metrics raised
    Does NOT validate field types (degraded_mode not bool etc.).

    When event_ids is given, filters first; then applies sample within
    the filtered set.
    """
    from app.memory import event_store
    entries = event_store.list_resolved_events()
    if event_ids:
        id_set = set(event_ids)
        entries = [e for e in entries if e.get("event_id") in id_set]
    if sample is not None and sample < len(entries):
        rng = random.Random(42)
        entries = rng.sample(entries, sample)

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for entry in entries:
        record = entry.get("record")
        if not isinstance(record, dict):
            errors.append({
                "event_id": entry.get("event_id", "?"),
                "error": "record missing or not a dict",
            })
            continue
        try:
            items.append(extract_model_metrics(record))
        except Exception as exc:
            errors.append({
                "event_id": record.get("event_id", "?"),
                "error": str(exc),
            })
    return items, errors


# ─── Rendering ─────────────────────────────────────────────────────────────

def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "  -  "
    return f"{v * 100:.2f}%"


def _fmt_cost(v: float | None, n: int) -> str:
    if n == 0:
        return "[n/a]"
    return f"${v:.4f}" if v is not None else "[n/a]"


def _fmt_brier(block: dict[str, Any]) -> str:
    b = block.get("brier_score")
    return "  -  " if b is None else f"{b:.4f}"


def _render_slice_table(
    title: str,
    slices: dict[str, dict[str, Any]],
    key_label: str,
) -> list[str]:
    """Render one slice dimension as an ASCII table."""
    lines: list[str] = [title, "=="]
    lines.append(
        f"  {key_label:<24} {'n':>4} {'brier':>7} {'ece':>6} "
        f"{'dir_acc':>10} {'cost_avg':>10} {'guard%':>8} {'degr%':>7}"
    )
    for k in sorted(slices.keys(), key=lambda x: -slices[x]["n"]):
        s = slices[k]
        brier_str = _fmt_brier(s["brier"])
        ece = s["ece"]
        ece_str = "  -  " if ece is None else f"{ece:.2f}"
        acc = s["direction_accuracy"]
        dc_true = s["direction_correct_true"]
        dc_false = s["direction_correct_false"]
        acc_str = "  -  " if acc is None else f"{acc:.4f} ({dc_true}/{dc_true + dc_false})"
        cost_str = _fmt_cost(s["cost_avg"], s["cost_n"])
        guard_str = _fmt_pct(s["guardrail_rate"])
        degr_str = _fmt_pct(s["degraded_rate"])
        suffix = "  [INSUFFICIENT]" if s.get("insufficient_samples") else ""
        lines.append(
            f"  {k:<24} {s['n']:>4} {brier_str:>7} {ece_str:>6} "
            f"{acc_str:>10} {cost_str:>10} {guard_str:>8} {degr_str:>7}{suffix}"
        )
    lines.append("")
    return lines


def _render_text(report: dict[str, Any]) -> str:
    """Render human-readable ASCII report."""
    lines: list[str] = []
    ov = report["overview"]
    lines.append(
        f"[INFO] Loaded {ov['n']} resolved events "
        f"({len(report['report_errors'])} report errors)"
    )
    lines.append(f"[INFO] Min samples for table display: {report['min_samples']}")
    lines.append("")

    # Overview
    lines.append(f"== Overview (all {ov['n']} events) ==")
    brier_str = _fmt_brier(ov["brier"])
    ece = ov["ece"]
    ece_str = "  -  " if ece is None else f"{ece:.2f}"
    acc = ov["direction_accuracy"]
    dc_true = ov["direction_correct_true"]
    dc_false = ov["direction_correct_false"]
    acc_str = "  -  " if acc is None else f"{acc:.4f} ({dc_true}/{dc_true + dc_false})"
    cost_str = _fmt_cost(ov["cost_avg"], ov["cost_n"])
    lines.append(
        f"  n={ov['n']}  brier={brier_str}  ece={ece_str}  "
        f"direction_acc={acc_str}"
    )
    lines.append(
        f"  cost_total=${ov['cost_total']:.4f}  cost_avg={cost_str} (n={ov['cost_n']})  "
        f"guardrail_rate={_fmt_pct(ov['guardrail_rate'])}  "
        f"degraded_rate={_fmt_pct(ov['degraded_rate'])}"
    )
    lines.append("")

    # Slices
    lines.extend(_render_slice_table("== By Model ==", report["by_model"], "model"))
    lines.extend(_render_slice_table(
        "== By Analysis Quality ==", report["by_analysis_quality"], "analysis_quality",
    ))
    lines.extend(_render_slice_table(
        "== By Degraded Mode ==", report["by_degraded_mode"], "mode",
    ))

    # Calibration deviation
    lines.append("== Calibration Deviation ==")
    lines.append(f"  {'bucket':<10} {'n':>4} {'pred_mean':>10} {'act_mean':>10} {'dev':>7}")
    for row in report["calibration_deviation"]:
        pred = "  -  " if row["predicted_mean"] is None else f"{row['predicted_mean']:.2f}"
        act = "  -  " if row["actual_mean"] is None else f"{row['actual_mean']:.2f}"
        dev = "  -  " if row["deviation"] is None else f"{row['deviation']:+.2f}"
        lines.append(f"  {row['bucket']:<10} {row['n']:>4} {pred:>10} {act:>10} {dev:>7}")
    lines.append("")

    # Report errors
    if report["report_errors"]:
        lines.append(f"== Report Errors ({len(report['report_errors'])}) ==")
        for err in report["report_errors"]:
            lines.append(f"  [WARN] {err.get('event_id', '?')}: {err['error']}")
        lines.append("")

    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code.

    Exit codes: 0 success (report_errors present still 0); 2 param errors.
    """
    parser = argparse.ArgumentParser(
        prog="model_eval_lab",
        description=(
            "Model evaluation lab. Slices resolved events by model / "
            "analysis_quality / degraded_mode. Reports Brier / ECE / "
            "direction accuracy / cost / guardrail rate per group."
        ),
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Randomly sample N resolved events (reproducible seed=42)",
    )
    parser.add_argument(
        "--event-ids", type=str, default=None,
        help="Comma-separated event IDs to restrict analysis",
    )
    parser.add_argument(
        "--min-samples", type=int, default=5,
        help="Min samples for table display (insufficient groups flagged, "
             "not dropped). Default 5. Does NOT affect overview.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON instead of human-readable text",
    )
    args = parser.parse_args(argv)

    # Param validation
    if args.sample is not None and args.sample < 0:
        print("Error: --sample must be >= 0", file=sys.stderr)
        return 2
    if args.min_samples < 0:
        print("Error: --min-samples must be >= 0", file=sys.stderr)
        return 2

    event_ids: list[str] | None = None
    if args.event_ids is not None:
        event_ids = [s.strip() for s in args.event_ids.split(",") if s.strip()]
        if not event_ids:
            print("Error: --event-ids parsed to empty list", file=sys.stderr)
            return 2

    try:
        items, report_errors = _collect_entries(args.sample, event_ids)
    except Exception as exc:
        print(f"Error: failed to load events: {exc}", file=sys.stderr)
        return 2

    try:
        report = build_model_eval_report(items, report_errors, min_samples=args.min_samples)
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
