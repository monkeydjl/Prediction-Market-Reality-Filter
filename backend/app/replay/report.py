"""Render ReplayMetrics to Markdown + JSON + HTML + cases.jsonl.

Pure rendering: no IO except ``write_report`` which writes the four files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape as _stdlib_escape
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


def _html_escape(text: str) -> str:
    """Escape HTML special characters. Defensive XSS protection for
    event_id / phase strings that flow into the report from metrics."""
    return _stdlib_escape(str(text), quote=True)


def _format_pct(num: int, denom: int) -> str:
    """Format num/denom as a percentage string. Returns 'N/A' when
    denom is 0 (avoids ZeroDivisionError in summary cards)."""
    if denom == 0:
        return "N/A"
    return f"{num / denom * 100:.1f}%"


def _heatmap_color(count: int, max_count: int, is_diagonal: bool) -> str:
    """Return inline ``background-color: rgba(...)`` style for a direction
    matrix cell. Diagonal (unchanged) = green; off-diagonal (changed) =
    crimson. Intensity scales with count/max_count. Returns empty string
    when max_count=0 or count=0 (no background)."""
    if max_count == 0 or count == 0:
        return ""
    intensity = count / max_count
    if is_diagonal:
        alpha = 0.15 + intensity * 0.35
        return f"background-color: rgba(34, 139, 34, {alpha:.2f})"
    alpha = 0.15 + intensity * 0.5
    return f"background-color: rgba(220, 20, 60, {alpha:.2f})"


def _delta_badge(delta: float | None) -> str:
    """Render direction_correct_delta as a colored badge. Positive =
    green (improved), negative = red (regressed), zero/None = grey."""
    if delta is None:
        return '<span class="badge badge-neutral">N/A</span>'
    if delta > 0:
        return f'<span class="badge badge-improved">+{delta*100:.1f}pp (improved)</span>'
    if delta < 0:
        return f'<span class="badge badge-regressed">{delta*100:.1f}pp (regressed)</span>'
    return '<span class="badge badge-neutral">0.0pp (unchanged)</span>'


_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; color: #222; max-width: 1100px; }
h1 { border-bottom: 2px solid #444; padding-bottom: 8px; }
h2 { margin-top: 32px; border-bottom: 1px solid #ccc; padding-bottom: 4px; }
.generated { color: #666; font-style: italic; font-size: 0.9em; }
section { margin-bottom: 24px; }
.cards { display: flex; gap: 16px; flex-wrap: wrap; }
.card { background: #f5f5f5; border-radius: 8px; padding: 16px 20px; min-width: 160px; border-left: 4px solid #444; }
.card .label { color: #666; font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.5px; }
.card .value { font-size: 1.8em; font-weight: bold; margin-top: 4px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
th { background: #f0f0f0; cursor: pointer; user-select: none; }
th:hover { background: #e0e0e0; }
tbody tr:nth-child(even) { background: #fafafa; }
.callout { background: #fffbe6; border-left: 4px solid #d4a017; padding: 12px 16px; margin: 8px 0; font-size: 0.9em; }
.bar-container { background: #eee; border-radius: 4px; height: 24px; margin: 4px 0; position: relative; }
.bar { height: 100%; border-radius: 4px; }
.bar-original { background: #888; }
.bar-replayed { background: #2563eb; }
.bar-label { position: absolute; left: 8px; top: 4px; color: white; font-size: 0.8em; font-weight: bold; }
.badge { padding: 2px 8px; border-radius: 12px; font-size: 0.85em; font-weight: bold; }
.badge-improved { background: #dcfce7; color: #166534; }
.badge-regressed { background: #fee2e2; color: #991b1b; }
.badge-neutral { background: #e5e7eb; color: #374151; }
.filter-bar { margin: 8px 0; display: flex; align-items: center; gap: 8px; }
.filter-bar label { font-size: 0.9em; color: #555; }
select { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; }
"""


def render_html(metrics: dict[str, Any]) -> str:
    """Render metrics dict to a self-contained HTML report string.

    Pure function: no IO, no side effects. Output is a single HTML
    document with inline CSS + JS, no external resources — can be
    opened directly in a browser without a network connection.

    Mirrors the 7 sections of render_markdown: Summary / Direction
    Matrix / Brier / Direction Accuracy / LLM vs Fallback /
    Per-Phase Marginal / Conflict Cases. Conflict cases table is
    sortable (click headers) and filterable (by phase dropdown).
    """
    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="zh-CN">')
    parts.append("<head>")
    parts.append('<meta charset="UTF-8">')
    parts.append("<title>Replay Report</title>")
    parts.append(f"<style>{_CSS}</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append("<h1>Replay Report</h1>")
    parts.append(
        f'<p class="generated">_Generated: '
        f"{datetime.now(timezone.utc).isoformat()}_</p>"
    )

    # Section 1: Summary
    total = metrics.get("total", 0)
    matrix = metrics.get("direction_matrix", {})
    changed = sum(
        v for k, v in matrix.items()
        if k.split("->")[0] != k.split("->")[1]
    ) if matrix else 0
    change_rate = _format_pct(changed, total) if total else "0.0%"
    resolved = metrics.get("resolved_count", 0)
    parts.append('<section id="summary">')
    parts.append("<h2>Summary</h2>")
    parts.append('<div class="cards">')
    parts.append(
        f'<div class="card"><div class="label">Total events</div>'
        f'<div class="value">{total}</div></div>'
    )
    parts.append(
        f'<div class="card"><div class="label">Direction changed</div>'
        f'<div class="value">{changed} ({change_rate})</div></div>'
    )
    parts.append(
        f'<div class="card"><div class="label">Resolved (with outcome)</div>'
        f'<div class="value">{resolved}</div></div>'
    )
    parts.append("</div>")
    parts.append("</section>")

    # Section 2: Direction Matrix (4x4 heatmap)
    parts.append('<section id="direction-matrix">')
    parts.append("<h2>Direction Matrix</h2>")
    dirs = ("YES", "NO", "WAIT", "AVOID")
    # Build matrix dict: (orig, replay) -> count
    cell_counts: dict[tuple[str, str], int] = {}
    for key, count in (matrix or {}).items():
        try:
            orig, replay = key.split("->")
            cell_counts[(orig, replay)] = count
        except ValueError:
            continue
    max_count = max(cell_counts.values()) if cell_counts else 0
    if cell_counts:
        parts.append('<table id="matrix-table">')
        parts.append("<thead><tr><th>Original \\ Replayed</th>")
        for d in dirs:
            parts.append(f"<th>{d}</th>")
        parts.append("</tr></thead>")
        parts.append("<tbody>")
        for orig in dirs:
            parts.append("<tr>")
            parts.append(f"<th>{orig}</th>")
            for replay in dirs:
                count = cell_counts.get((orig, replay), 0)
                is_diag = orig == replay
                color = _heatmap_color(count, max_count, is_diag)
                style = f' style="{color}"' if color else ""
                parts.append(f"<td{style}>{count}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No direction changes recorded.</p>")
    parts.append("</section>")

    # Section 3: Brier (rendered before Direction Matrix per Markdown order)
    brier_mean = metrics.get("brier_mean")
    brier_frozen = metrics.get("brier_frozen", False)
    parts.append('<section id="brier">')
    parts.append("<h2>Brier</h2>")
    if brier_mean is not None:
        parts.append(
            f'<div class="card"><div class="label">Mean Brier (resolved)</div>'
            f'<div class="value">{brier_mean:.4f}</div></div>'
        )
        if brier_frozen:
            parts.append(
                '<div class="callout">Brier is frozen at freeze time. '
                "Overlays do not recompute ai_probability, so original and "
                "replayed share the same Brier. See Direction Accuracy "
                "below for the real improvement signal.</div>"
            )
    else:
        parts.append("<p>No resolved samples to compute Brier.</p>")
    parts.append("</section>")

    # Section 4: Direction Accuracy
    rc = metrics.get("direction_correct_resolved_count", 0)
    orig_correct = metrics.get("direction_correct_original", 0)
    replay_correct = metrics.get("direction_correct_replayed", 0)
    delta = metrics.get("direction_correct_delta")
    parts.append('<section id="direction-accuracy">')
    parts.append("<h2>Direction Accuracy</h2>")
    if rc:
        orig_pct = orig_correct / rc * 100
        replay_pct = replay_correct / rc * 100
        parts.append(f"<p>Resolved samples: {rc}</p>")
        # Original bar
        parts.append(
            f'<div class="bar-container" style="width: {orig_pct:.1f}%;">'
            f'<div class="bar bar-original" style="width: 100%;">'
            f'<span class="bar-label">Original: {orig_pct:.1f}% ({orig_correct}/{rc})</span>'
            f"</div></div>"
        )
        # Replayed bar
        parts.append(
            f'<div class="bar-container" style="width: {replay_pct:.1f}%;">'
            f'<div class="bar bar-replayed" style="width: 100%;">'
            f'<span class="bar-label">Replayed: {replay_pct:.1f}% ({replay_correct}/{rc})</span>'
            f"</div></div>"
        )
        parts.append(f"<p>Delta: {_delta_badge(delta)}</p>")
    else:
        parts.append("<p>No resolved samples.</p>")
    parts.append("</section>")

    # Sections 2, 4, 5, 6, 7 added in later tasks.
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


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
