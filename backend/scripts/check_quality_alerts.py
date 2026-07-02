"""Quality alerts CLI (LATER #3).

Evaluates quality alerts on resolved events and prints a text or JSON report.
First version does not dispatch (no webhook/Sentry/log). Mirrors the structure
of report_quality_metrics.py: thin adapter that builds the report, constructs
thresholds from settings, and calls quality_alert_service.

Usage:
    python -m scripts.check_quality_alerts
    python -m scripts.check_quality_alerts --limit 50
    python -m scripts.check_quality_alerts --sample 50
    python -m scripts.check_quality_alerts --json
    python -m scripts.check_quality_alerts --include-insufficient-samples
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from typing import Any
from pathlib import Path

# UTF-8 stdout for Windows GBK console safety (same convention as
# report_quality_metrics.py / sweep_event_quality.py).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):  # pragma: no cover
    pass

# Make backend importable when run as a script.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def _print(text: str) -> None:
    """Print with UTF-8 stdout (Windows GBK safety)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _collect_entries(limit: int | None, sample: int | None) -> list[dict[str, Any]]:
    """Load resolved events from event_store, optionally limited/sampled.

    Duplicated from report_quality_metrics.py: short I/O adapter, not worth
    extracting to service (would pull event_store into pure module).
    list_resolved_events already filters to outcome.status == "resolved".
    """
    from app.memory import event_store
    entries = event_store.list_resolved_events()
    if sample is not None and sample < len(entries):
        rng = random.Random(42)
        entries = rng.sample(entries, sample)
    if limit is not None:
        entries = entries[:limit]
    return entries


def _render_text(
    alerts: list[dict[str, Any]],
    report: dict[str, Any],
    thresholds: dict[str, Any],
    include_insufficient: bool,
) -> str:
    """Render ASCII-only text report."""
    from app.services.quality_alert_service import collect_insufficient_samples

    lines: list[str] = []
    lines.append(f"Quality Alerts Report - {len(alerts)} alerts found")
    lines.append("=" * 60)
    lines.append("")
    lines.append(
        f"Config: min_samples={thresholds['min_samples']}, "
        f"dir_acc={thresholds['direction_accuracy_medium']}/{thresholds['direction_accuracy_high']}, "
        f"brier={thresholds['brier_medium']}/{thresholds['brier_high']}, "
        f"miss_cal={thresholds['missing_calibration_rate_medium']}/{thresholds['missing_calibration_rate_high']}, "
        f"report_errors>={thresholds['report_errors_high']}"
    )
    lines.append("")

    if not alerts:
        lines.append("No alerts. System is healthy.")
        lines.append("")
    else:
        high = [a for a in alerts if a["severity"] == "high"]
        medium = [a for a in alerts if a["severity"] == "medium"]
        if high:
            lines.append(f"[HIGH] {len(high)}")
            for a in high:
                lines.extend(_render_alert(a))
            lines.append("")
        if medium:
            lines.append(f"[MEDIUM] {len(medium)}")
            for a in medium:
                lines.extend(_render_alert(a))
            lines.append("")

    if include_insufficient:
        insuff = collect_insufficient_samples(report, thresholds)
        if insuff:
            lines.append(f"[INSUFFICIENT] {len(insuff)} slices skipped")
            for item in insuff:
                lines.append(
                    f"  {item['dimension']}[{item['slice']}]: n={item['n']} (< {item['min_samples']})"
                )
            lines.append("")

    n_high = sum(1 for a in alerts if a["severity"] == "high")
    n_med = sum(1 for a in alerts if a["severity"] == "medium")
    lines.append(f"Summary: {len(alerts)} alerts ({n_high} high, {n_med} medium). 0 alerts = healthy.")

    return "\n".join(lines)


def _render_alert(alert: dict[str, Any]) -> list[str]:
    """Render one alert as ASCII lines."""
    scope = alert["scope"]
    if scope == "overview":
        header = f"  [overview] {alert['code']}"
    else:
        header = f"  [slice:{alert['dimension']}/{alert['slice']}] {alert['code']}"
    return [
        header,
        f"    value={alert['value']}, threshold={alert['threshold']}, n={alert['n']}",
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code 0 (always, even with alerts)."""
    parser = argparse.ArgumentParser(
        prog="check_quality_alerts",
        description=(
            "Evaluate quality alerts on resolved events. Reports direction "
            "accuracy, Brier score, and missing calibration alerts at overview "
            "and slice level. First version does not dispatch."
        ),
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Report on only the first N resolved events (default: all)")
    parser.add_argument("--sample", type=int, default=None,
                        help="Randomly sample N resolved events (reproducible seed=42)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON instead of human-readable text")
    parser.add_argument("--include-insufficient-samples", action="store_true",
                        help="Include diagnostics for slices with n < min_samples")
    args = parser.parse_args(argv)

    from app.core.config import settings
    from app.services.quality_alert_service import (
        collect_insufficient_samples,
        evaluate_quality_alerts,
        thresholds_from_settings,
    )
    from app.services.quality_metrics_report_service import (
        build_report,
        extract_metrics,
    )

    try:
        entries = _collect_entries(args.limit, args.sample)
    except Exception as exc:
        print(f"Error: failed to load events: {exc}", file=sys.stderr)
        return 2

    items: list[dict[str, Any]] = []
    report_errors: list[dict[str, str]] = []
    for entry in entries:
        record = entry.get("record")
        if not isinstance(record, dict):
            report_errors.append({"event_id": entry.get("event_id", "?"),
                                  "error": "record missing or not a dict"})
            continue
        try:
            items.append(extract_metrics(record))
        except Exception as exc:
            report_errors.append({"event_id": record.get("event_id", "?"),
                                  "error": str(exc)})

    report = build_report(items, report_errors)
    thresholds = thresholds_from_settings(settings)
    alerts = evaluate_quality_alerts(report, thresholds)

    if args.json:
        payload: dict[str, Any] = {"alerts": alerts, "alert_count": len(alerts)}
        if args.include_insufficient_samples:
            payload["diagnostics"] = {
                "insufficient_samples": collect_insufficient_samples(report, thresholds),
            }
        _print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
    else:
        _print(_render_text(alerts, report, thresholds, args.include_insufficient_samples))

    return 0


if __name__ == "__main__":
    sys.exit(main())
