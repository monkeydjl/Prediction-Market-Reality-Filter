"""Render ReplayMetrics to Markdown + JSON + cases.jsonl.

Pure rendering: no IO except ``write_report`` which writes the three files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def render_markdown(metrics: dict[str, Any]) -> str:
    """Render metrics dict to a Markdown report string."""
    lines: list[str] = []
    lines.append("# Replay Report")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")

    # Section 1: Summary
    total = metrics.get("total", 0)
    matrix = metrics.get("direction_matrix", {})
    changed = sum(v for k, v in matrix.items() if k.split("->")[0] != k.split("->")[1]) if matrix else 0
    change_rate = (changed / total * 100) if total else 0.0
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total events: {total}")
    lines.append(f"- Direction changed: {changed} ({change_rate:.1f}%)")
    lines.append(f"- Resolved (with outcome): {metrics.get('resolved_count', 0)}")
    lines.append("")

    # Section 2: Direction Matrix
    lines.append("## Direction Matrix")
    lines.append("")
    if matrix:
        lines.append("| Original | Replayed | Count |")
        lines.append("|---|---|---|")
        for key, count in sorted(matrix.items(), key=lambda x: -x[1]):
            orig, replay = key.split("->")
            lines.append(f"| {orig} | {replay} | {count} |")
    else:
        lines.append("_No direction changes recorded._")
    lines.append("")

    # Section 3: Brier
    lines.append("## Brier")
    lines.append("")
    brier_mean = metrics.get("brier_mean")
    brier_frozen = metrics.get("brier_frozen", False)
    if brier_mean is not None:
        lines.append(f"- Mean Brier (resolved): {brier_mean:.4f}")
        if brier_frozen:
            lines.append(
                "- _Note: Brier is frozen at freeze time. Overlays do not "
                "recompute ai_probability, so original and replayed share "
                "the same Brier. See Direction Accuracy below for the real "
                "improvement signal._"
            )
    else:
        lines.append("_No resolved samples to compute Brier._")
    lines.append("")

    # Section 4: Direction Accuracy
    lines.append("## Direction Accuracy")
    lines.append("")
    rc = metrics.get("direction_correct_resolved_count", 0)
    orig_correct = metrics.get("direction_correct_original", 0)
    replay_correct = metrics.get("direction_correct_replayed", 0)
    delta = metrics.get("direction_correct_delta")
    if rc:
        lines.append(f"- Resolved samples: {rc}")
        lines.append(f"- Original correct: {orig_correct} ({orig_correct/rc*100:.1f}%)")
        lines.append(f"- Replayed correct: {replay_correct} ({replay_correct/rc*100:.1f}%)")
        if delta is not None:
            verdict = "improved" if delta > 0 else ("regressed" if delta < 0 else "unchanged")
            lines.append(f"- Delta: {delta*100:+.1f}pp ({verdict})")
    else:
        lines.append("_No resolved samples._")
    lines.append("")

    # Section 5: LLM vs Fallback
    lines.append("## LLM vs Fallback")
    lines.append("")
    bq = metrics.get("brier_by_quality", {})
    if bq:
        lines.append("| Quality | N | Brier mean |")
        lines.append("|---|---|---|")
        for q, bucket in bq.items():
            mean = bucket.get("brier_mean")
            mean_str = f"{mean:.4f}" if mean is not None else "N/A"
            lines.append(f"| {q} | {bucket.get('n', 0)} | {mean_str} |")
    else:
        lines.append("_No analysis_quality data._")
    lines.append("")

    # Section 6: Per-Phase Marginal Contribution
    lines.append("## Per-Phase Marginal Contribution")
    lines.append("")
    pc = metrics.get("phase_contributions", {})
    if pc:
        lines.append("| Phase | Downgrades caused | Directions changed | Conflicts |")
        lines.append("|---|---|---|---|")
        for phase, contrib in pc.items():
            lines.append(
                f"| {phase} | {contrib.get('downgrades_caused', 0)} | "
                f"{contrib.get('directions_changed', 0)} | "
                f"{contrib.get('conflicts_with_final', 0)} |"
            )
    else:
        lines.append("_No per-phase replay run (use --marginal to enable)._")
    lines.append("")

    # Section 7: Conflict Cases
    lines.append("## Conflict Cases")
    lines.append("")
    cases = metrics.get("conflict_cases", [])
    total_cases = metrics.get("conflict_cases_total", 0)
    lines.append(f"_Total conflicts: {total_cases} (showing first {len(cases)})._")
    lines.append("")
    if cases:
        lines.append("| Event | Phase | Phase dir | Final dir | Base dir |")
        lines.append("|---|---|---|---|---|")
        for c in cases:
            lines.append(
                f"| {c.get('event_id')} | {c.get('phase')} | "
                f"{c.get('phase_dir')} | {c.get('final_dir')} | "
                f"{c.get('base_dir')} |"
            )
    lines.append("")
    return "\n".join(lines)


def render_json(metrics: dict[str, Any]) -> str:
    """Render metrics dict to a JSON string."""
    return json.dumps(metrics, indent=2, default=str)


def write_report(
    metrics: dict[str, Any],
    output_dir: Path,
    cases: list[dict[str, Any]] | None = None,
) -> Path:
    """Write report.md + metrics.json + cases.jsonl to ``output_dir``.

    Returns the path to ``report.md``. Creates ``output_dir`` if missing.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "report.md"
    md_path.write_text(render_markdown(metrics), encoding="utf-8")
    (output_dir / "metrics.json").write_text(
        render_json(metrics), encoding="utf-8"
    )
    if cases is not None:
        cases_path = output_dir / "cases.jsonl"
        with cases_path.open("w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, default=str) + "\n")
    return md_path
