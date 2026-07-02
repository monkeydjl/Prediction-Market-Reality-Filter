"""Batch quality sweep over resolved events (NEXT #1).

Sweeps all resolved events in event_store through diagnose_event_quality's
_extract_phase_data, then reports:

  1. Aggregate metrics across the swept set:
     - direction_correct distribution (True / False / None counts)
     - edge_bucket distribution (0-5 / 5-10 / 10-20 / 20+ / "")
     - llm degraded_mode count
     - market degraded count
     - guardrail fired frequency (top rules)
     - missing-overlay counts (which phases are absent)

  2. Anomaly list (events that warrant review):
     - direction_correct == False (misjudgments)
     - direction_correct == None but recommendation is YES/NO (unsettled
       directional — not an anomaly, but surfaced for completeness)
     - llm_telemetry.degraded_mode == True
     - market_quality.degraded == True
     - guardrail_fired non-empty

Complements audit_quality_consistency.py:
  - audit_quality_consistency: detects field-level invariant violations
    (overlay fields contradicting final_displayed_direction)
  - sweep_event_quality: aggregates diagnosis-level metrics across events
    and lists anomalies by category

Pure read-only: no writes, no LLM calls, no network.

Usage:
    python -m scripts.sweep_event_quality
    python -m scripts.sweep_event_quality --limit 50
    python -m scripts.sweep_event_quality --sample 50
    python -m scripts.sweep_event_quality --json
    python -m scripts.sweep_event_quality --anomalies-only
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from collections import Counter
from typing import Any

# UTF-8 stdout for Windows GBK console safety (same convention as
# diagnose_event_quality.py / source_trust_registry_cli.py).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass

# Make backend importable when run as a script.
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

# scripts/ is not a package — add it to sys.path so we can import
# diagnose_event_quality as a top-level module (same pattern as
# test_diagnose_event_quality.py).
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _print(text: str) -> None:
    """Print with UTF-8 stdout (Windows GBK safety)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _collect_entries(limit: int | None, sample: int | None) -> list[dict[str, Any]]:
    """Load resolved events from event_store, optionally limited/sampled.

    list_resolved_events already filters to outcome.status == "resolved"
    (or missing status, which defaults to resolved). Non-resolved statuses
    (e.g. "invalid") are excluded — they record the marker but are not
    scored, so sweeping them would pollute the direction_correct aggregate.
    """
    from app.memory import event_store
    entries = event_store.list_resolved_events()
    if sample is not None and sample < len(entries):
        # Reproducible sample for auditability — seed fixed.
        rng = random.Random(42)
        entries = rng.sample(entries, sample)
    if limit is not None:
        entries = entries[:limit]
    return entries


def _sweep(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run _extract_phase_data on each entry's record.

    Returns list of diagnosis dicts (same shape as diagnose CLI output).
    Entries whose record is missing/invalid are skipped with a note in
    the output (phase data = None) rather than crashing the sweep.
    """
    from diagnose_event_quality import _extract_phase_data
    results: list[dict[str, Any]] = []
    for entry in entries:
        record = entry.get("record")
        if not isinstance(record, dict):
            results.append({
                "event_id": entry.get("event_id", "?"),
                "event_title": None,
                "phases": {},
                "guardrails": {"fired_rules": []},
                "final_direction": None,
                "_sweep_error": "record missing or not a dict",
            })
            continue
        try:
            data = _extract_phase_data(record)
            results.append(data)
        except Exception as exc:
            # Single-event failure must not abort the whole sweep.
            results.append({
                "event_id": record.get("event_id", "?"),
                "event_title": record.get("event_title"),
                "phases": {},
                "guardrails": {"fired_rules": []},
                "final_direction": None,
                "_sweep_error": str(exc),
            })
    return results


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate metrics across swept results."""
    direction_correct_counts = Counter()
    edge_bucket_counts = Counter()
    recommendation_counts = Counter()
    llm_degraded_count = 0
    market_degraded_count = 0
    guardrail_counter: Counter[str] = Counter()
    missing_overlay_counts: Counter[str] = Counter()
    sweep_errors: list[dict[str, str]] = []

    for r in results:
        if r.get("_sweep_error"):
            sweep_errors.append({
                "event_id": r.get("event_id", "?"),
                "error": r["_sweep_error"],
            })
            continue

        phases = r.get("phases", {})

        # Phase 3: prediction calibration
        pc = phases.get("prediction_calibration")
        if pc is not None:
            dc = pc.get("direction_correct")
            # None → "unsettled_or_non_directional"; True/False as-is
            direction_correct_counts[str(dc)] += 1
            edge_bucket_counts[pc.get("edge_bucket") or "<missing>"] += 1
            recommendation_counts[pc.get("snapshot_recommendation") or "<missing>"] += 1
        else:
            missing_overlay_counts["prediction_calibration"] += 1

        # Phase 5: LLM telemetry
        lt = phases.get("llm_telemetry")
        if lt is None:
            missing_overlay_counts["llm_telemetry"] += 1
        elif lt.get("degraded_mode"):
            llm_degraded_count += 1

        # Phase 2: market quality
        mq = phases.get("market_quality")
        if mq is None:
            missing_overlay_counts["market_quality"] += 1
        elif mq.get("degraded"):
            market_degraded_count += 1

        # Guardrails
        fired = r.get("guardrails", {}).get("fired_rules", [])
        for rule in fired:
            guardrail_counter[str(rule)] += 1

    return {
        "total_swept": len(results),
        "direction_correct": dict(direction_correct_counts),
        "edge_bucket": dict(edge_bucket_counts),
        "recommendation": dict(recommendation_counts),
        "llm_degraded_count": llm_degraded_count,
        "market_degraded_count": market_degraded_count,
        "guardrail_top": guardrail_counter.most_common(10),
        "missing_overlay_counts": dict(missing_overlay_counts),
        "sweep_errors": sweep_errors,
    }


def _anomalies(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group anomalies by category for the review list."""
    misjudgments: list[dict[str, Any]] = []      # direction_correct == False
    llm_degraded: list[dict[str, Any]] = []
    market_degraded: list[dict[str, Any]] = []
    guardrail_fired: list[dict[str, Any]] = []

    for r in results:
        if r.get("_sweep_error"):
            continue
        event_id = r.get("event_id", "?")
        title = r.get("event_title") or ""
        phases = r.get("phases", {})

        pc = phases.get("prediction_calibration") or {}
        if pc.get("direction_correct") is False:
            misjudgments.append({
                "event_id": event_id,
                "event_title": title,
                "recommendation": pc.get("snapshot_recommendation"),
                "edge_bucket": pc.get("edge_bucket"),
            })

        lt = phases.get("llm_telemetry") or {}
        if lt.get("degraded_mode"):
            llm_degraded.append({
                "event_id": event_id,
                "event_title": title,
                "analysis_quality": lt.get("analysis_quality"),
            })

        mq = phases.get("market_quality") or {}
        if mq.get("degraded"):
            market_degraded.append({
                "event_id": event_id,
                "event_title": title,
                "degrade_reason": mq.get("degrade_reason"),
            })

        fired = r.get("guardrails", {}).get("fired_rules", [])
        if fired:
            guardrail_fired.append({
                "event_id": event_id,
                "event_title": title,
                "fired_rules": fired,
            })

    return {
        "misjudgments": misjudgments,
        "llm_degraded": llm_degraded,
        "market_degraded": market_degraded,
        "guardrail_fired": guardrail_fired,
    }


def _render_text(agg: dict[str, Any], anomalies: dict[str, list]) -> str:
    """Render human-readable sweep report."""
    lines: list[str] = []
    total = agg["total_swept"]
    lines.append(f"Quality Sweep Report — {total} events swept")
    lines.append("═" * 60)
    lines.append("")

    # Aggregate
    lines.append("📊 Aggregate Metrics")
    lines.append("─" * 40)
    dc = agg["direction_correct"]
    lines.append(
        f"  direction_correct: True={dc.get('True', 0)}  "
        f"False={dc.get('False', 0)}  "
        f"None={dc.get('None', 0)}"
    )
    if dc.get("False", 0) > 0 and total > 0:
        rate = dc["False"] / total * 100
        lines.append(f"  misjudgment rate: {rate:.1f}%")
    lines.append(f"  edge_bucket: {agg['edge_bucket']}")
    lines.append(f"  recommendation: {agg['recommendation']}")
    lines.append(f"  llm_degraded: {agg['llm_degraded_count']}")
    lines.append(f"  market_degraded: {agg['market_degraded_count']}")
    if agg["guardrail_top"]:
        lines.append("  guardrail_top:")
        for rule, count in agg["guardrail_top"]:
            lines.append(f"    {rule}: {count}")
    if agg["missing_overlay_counts"]:
        lines.append(f"  missing_overlays: {agg['missing_overlay_counts']}")
    if agg["sweep_errors"]:
        lines.append(f"  sweep_errors: {len(agg['sweep_errors'])}")
        for err in agg["sweep_errors"][:5]:
            lines.append(f"    {err['event_id']}: {err['error']}")
    lines.append("")

    # Anomalies
    lines.append("🚨 Anomalies")
    lines.append("─" * 40)
    lines.append(f"  misjudgments (direction_correct=False): {len(anomalies['misjudgments'])}")
    for a in anomalies["misjudgments"][:10]:
        lines.append(
            f"    {a['event_id']}  rec={a['recommendation']}  "
            f"edge={a['edge_bucket']}  {a['event_title'][:40]}"
        )
    lines.append(f"  llm_degraded: {len(anomalies['llm_degraded'])}")
    for a in anomalies["llm_degraded"][:5]:
        lines.append(f"    {a['event_id']}  {a['event_title'][:40]}")
    lines.append(f"  market_degraded: {len(anomalies['market_degraded'])}")
    for a in anomalies["market_degraded"][:5]:
        lines.append(f"    {a['event_id']}  ({a['degrade_reason']})  {a['event_title'][:40]}")
    lines.append(f"  guardrail_fired: {len(anomalies['guardrail_fired'])}")
    for a in anomalies["guardrail_fired"][:5]:
        rules = ", ".join(a["fired_rules"][:3])
        lines.append(f"    {a['event_id']}  [{rules}]  {a['event_title'][:30]}")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code.

    Exit codes: 0 success, 2 other errors (load/sweep failure).
    Anomalies are NOT failures — they're the report's content.
    """
    parser = argparse.ArgumentParser(
        prog="sweep_event_quality",
        description=(
            "Batch quality sweep over resolved events. Aggregates diagnosis "
            "metrics and lists anomalies (misjudgments, degraded modes, "
            "guardrail firings)."
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Sweep only the first N resolved events (default: all)",
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Randomly sample N resolved events (reproducible seed=42)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON instead of human-readable text",
    )
    parser.add_argument(
        "--anomalies-only", action="store_true",
        help="Output only the anomaly list (skip aggregate metrics)",
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
                "total_swept": 0,
                "message": "no resolved events found in event_store",
            }, indent=2))
        else:
            _print("[INFO] No resolved events found in event_store.")
        return 0

    try:
        results = _sweep(entries)
    except Exception as exc:
        print(f"Error: sweep failed: {exc}", file=sys.stderr)
        return 2

    agg = _aggregate(results)
    anomalies = _anomalies(results)

    if args.json:
        output = {"anomalies": anomalies} if args.anomalies_only else {
            "aggregate": agg, "anomalies": anomalies,
        }
        _print(json.dumps(output, indent=2, default=str, ensure_ascii=False))
    else:
        if args.anomalies_only:
            _print(_render_text({"total_swept": agg["total_swept"]}, anomalies))
        else:
            _print(_render_text(agg, anomalies))

    return 0


if __name__ == "__main__":
    sys.exit(main())
