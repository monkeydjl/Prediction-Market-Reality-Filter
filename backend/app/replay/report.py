"""Render ReplayMetrics to Markdown + JSON + HTML + cases.jsonl.

Pure rendering: no IO except ``write_report`` which writes the four files.

Every renderer takes an optional ``run`` block — the provenance an archived
report needs to be interpretable later: which two configs were compared, how
many records were replayed out of what population, whether the per-phase loop
ran, and which sample seed drew the subset. Without it two ``metrics.json``
files are two bags of numbers that cannot be told apart, so "the replay says
downgrades doubled" was not a statement anyone could check. ``run`` is absent
on a report written before this existed, which is why every reader treats it as
optional rather than defaulting it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape as _stdlib_escape
from pathlib import Path
from typing import Any

# Bumped when the *layout* of the written files changes, which is a different
# question from "were these two reports built from the same events" — that one
# is answered by the run block's compare/sample/population fields.
REPLAY_REPORT_SCHEMA_VERSION = 1


def _generated_at(run: dict[str, Any] | None) -> str:
    """The one timestamp for this run.

    ``render_markdown`` and ``render_html`` each used to call
    ``datetime.now()``, so the two human-readable files a single run wrote
    disagreed about when that run happened -- and ``metrics.json``, the one a
    script reads, carried no timestamp at all. When a run block is supplied,
    ``write_report`` stamps it once and all three print or store that.
    """
    if run is not None:
        stamped = run.get("generated_at")
        if isinstance(stamped, str) and stamped:
            return stamped
    return datetime.now(timezone.utc).isoformat()


def _run_rows(run: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, value) pairs for the Run section, shared by both renderers.

    One source for the rows so the Markdown and HTML reports cannot describe
    the same run differently — they already computed the Summary numbers twice
    each, which is one copy too many to add a third to.
    """
    compare = run.get("compare") or {}
    rows: list[tuple[str, str]] = [
        ("Compared", f"{compare.get('a', '?')} -> {compare.get('b', '?')}"),
        ("Records replayed", str(run.get("records_replayed", 0))),
    ]
    population = run.get("population")
    if population is not None:
        rows.append(("Population", str(population)))
    sample = run.get("sample")
    if isinstance(sample, dict):
        rows.append((
            "Sample",
            f"size={sample.get('size')} seed={sample.get('seed')!r} "
            f"strategy={sample.get('strategy')}",
        ))
    else:
        rows.append(("Sample", "none (whole population)"))
    rows.append((
        "Per-phase marginal",
        "yes" if run.get("marginal") else "no (--skip-marginal)",
    ))
    missing = run.get("missing_event_ids") or []
    if missing:
        shown = ", ".join(str(m) for m in missing[:10])
        suffix = f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""
        rows.append((
            "Requested but not found", f"{len(missing)}: {shown}{suffix}",
        ))
    dupes = run.get("duplicate_event_ids") or []
    if dupes:
        rows.append((
            "Duplicate event_id (kept once each)",
            f"{len(dupes)}: {', '.join(str(d) for d in dupes[:10])}",
        ))
    skipped = run.get("skipped_no_event_id") or 0
    if skipped:
        rows.append(("Skipped (no event_id)", str(skipped)))
    rows.append(("Report schema", str(run.get("schema_version", "?"))))
    return rows


def _no_direction_samples(metrics: dict[str, Any]) -> str:
    """Why Direction Accuracy is empty, naming the denominator it actually uses.

    Both renderers used to print "No resolved samples." here while the Summary
    two sections up said "Resolved (with outcome): 2" — a flat contradiction to
    anyone reading top to bottom. Nothing was miscounted: Brier's denominator is
    ``resolved_count`` and this section's is
    ``direction_correct_resolved_count``, which excludes WAIT/AVOID because an
    abstention has no direction to be right about. The message named the wrong
    one.
    """
    resolved = metrics.get("resolved_count") or 0
    if resolved:
        return (
            f"_No direction-callable samples: {resolved} event(s) resolved, but "
            "none carried an explicit YES/NO on both sides (WAIT/AVOID abstain, "
            "so they score in the Direction Matrix instead)._"
        )
    return "_No resolved samples._"


def render_markdown(metrics: dict[str, Any], run: dict[str, Any] | None = None) -> str:
    """Render metrics dict to a Markdown report string."""
    lines: list[str] = []
    lines.append("# Replay Report")
    lines.append("")
    lines.append(f"_Generated: {_generated_at(run)}_")
    lines.append("")

    # Section 0: Run provenance. Only rendered when supplied, so a caller that
    # predates the run block still produces the report it always produced.
    if run is not None:
        lines.append("## Run")
        lines.append("")
        for label, value in _run_rows(run):
            lines.append(f"- {label}: {value}")
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
        lines.append(f"- Direction-callable samples: {rc}")
        lines.append(f"- Original correct: {orig_correct} ({orig_correct/rc*100:.1f}%)")
        lines.append(f"- Replayed correct: {replay_correct} ({replay_correct/rc*100:.1f}%)")
        if delta is not None:
            verdict = "improved" if delta > 0 else ("regressed" if delta < 0 else "unchanged")
            lines.append(f"- Delta: {delta*100:+.1f}pp ({verdict})")
    else:
        lines.append(_no_direction_samples(metrics))
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
        # There is no --marginal flag; the loop runs by default and
        # --skip-marginal turns it off. The old text told an operator staring
        # at an empty section to pass a flag argparse would reject, so the
        # obvious next step produced "unrecognized arguments" and read as a
        # broken tool.
        lines.append(
            "_No per-phase replay run (the N+1 loop runs by default; "
            "--skip-marginal disables it)._"
        )
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


def render_json(metrics: dict[str, Any], run: dict[str, Any] | None = None) -> str:
    """Render metrics dict to a JSON string.

    The run block is merged in under ``"run"`` rather than nesting the metrics
    one level deeper: a consumer that reads ``total`` off the top level keeps
    working, and a file with no ``"run"`` key is one written before provenance
    was recorded.
    """
    body = dict(metrics)
    if run is not None:
        body["run"] = run
    return json.dumps(body, indent=2, default=str)


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
.bar-container { background: #eee; border-radius: 4px; height: 24px; margin: 4px 0; display: flex; align-items: center; }
.bar { height: 100%; border-radius: 4px; display: flex; align-items: center; }
.bar-original { background: #888; }
.bar-replayed { background: #2563eb; }
.bar-label { padding-left: 8px; color: white; font-size: 0.8em; font-weight: bold; }
.badge { padding: 2px 8px; border-radius: 12px; font-size: 0.85em; font-weight: bold; }
.badge-improved { background: #dcfce7; color: #166534; }
.badge-regressed { background: #fee2e2; color: #991b1b; }
.badge-neutral { background: #e5e7eb; color: #374151; }
.filter-bar { margin: 8px 0; display: flex; align-items: center; gap: 8px; }
.filter-bar label { font-size: 0.9em; color: #555; }
select { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; }
"""


def render_html(metrics: dict[str, Any], run: dict[str, Any] | None = None) -> str:
    """Render metrics dict to a self-contained HTML report string.

    Pure function: no IO, no side effects. Output is a single HTML
    document with inline CSS + JS, no external resources — can be
    opened directly in a browser without a network connection.

    Mirrors the 7 sections of render_markdown: Summary / Direction
    Matrix / Brier / Direction Accuracy / LLM vs Fallback /
    Per-Phase Marginal / Conflict Cases, preceded by the Run block when
    provenance was supplied. Conflict cases table is sortable (click headers)
    and filterable (by phase dropdown).
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
        f"{_generated_at(run)}_</p>"
    )

    # Section 0: Run provenance (same rows as the Markdown report).
    if run is not None:
        parts.append('<section id="run">')
        parts.append("<h2>Run</h2>")
        parts.append("<table>")
        parts.append("<tbody>")
        for label, value in _run_rows(run):
            parts.append(
                f"<tr><th>{_html_escape(label)}</th>"
                f"<td>{_html_escape(value)}</td></tr>"
            )
        parts.append("</tbody></table>")
        parts.append("</section>")

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

    # Section 3: Brier (rendered after Direction Matrix, matching Markdown order)
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
        parts.append(f"<p>Direction-callable samples: {rc}</p>")
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
        parts.append(f"<p>{_html_escape(_no_direction_samples(metrics).strip('_'))}</p>")
    parts.append("</section>")

    # Section 5: LLM vs Fallback
    bq = metrics.get("brier_by_quality", {})
    parts.append('<section id="llm-vs-fallback">')
    parts.append("<h2>LLM vs Fallback</h2>")
    if bq:
        parts.append("<table>")
        parts.append("<thead><tr><th>Quality</th><th>N</th><th>Brier mean</th></tr></thead>")
        parts.append("<tbody>")
        for q, bucket in bq.items():
            mean = bucket.get("brier_mean")
            mean_str = f"{mean:.4f}" if mean is not None else "N/A"
            parts.append(
                f"<tr><td>{_html_escape(q)}</td>"
                f'<td data-numeric="true">{bucket.get("n", 0)}</td>'
                f"<td>{mean_str}</td></tr>"
            )
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No analysis_quality data.</p>")
    parts.append("</section>")

    # Section 6: Per-Phase Marginal Contribution
    pc = metrics.get("phase_contributions", {})
    parts.append('<section id="per-phase-marginal">')
    parts.append("<h2>Per-Phase Marginal Contribution</h2>")
    if pc:
        max_downgrades = max(
            (c.get("downgrades_caused", 0) for c in pc.values()),
            default=0,
        )
        parts.append("<table>")
        parts.append(
            "<thead><tr><th>Phase</th><th>Downgrades caused</th>"
            "<th>Directions changed</th><th>Conflicts</th></tr></thead>"
        )
        parts.append("<tbody>")
        for phase, contrib in pc.items():
            dc = contrib.get("downgrades_caused", 0)
            bar_width = (dc / max_downgrades * 100) if max_downgrades else 0
            # Bar container width = bar_width% (0% when dc=0 or max=0).
            # No min-width on the container — a zero-contribution phase
            # must show zero bar width per spec. The numeric label is
            # always visible because it sits inside the bar div (which
            # has padding) even when width is 0% (text overflows).
            parts.append(
                f"<tr><td>{_html_escape(phase)}</td>"
                f'<td><div class="bar-container" style="height: 16px; width: {bar_width:.1f}%;">'
                f'<div class="bar bar-replayed" style="width: 100%;">'
                f"{dc}</div></div></td>"
                f'<td data-numeric="true">{contrib.get("directions_changed", 0)}</td>'
                f'<td data-numeric="true">{contrib.get("conflicts_with_final", 0)}</td></tr>'
            )
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No per-phase replay run.</p>")
    parts.append("</section>")

    # Section 7: Conflict Cases (sortable + filterable)
    cases = metrics.get("conflict_cases", [])
    total_cases = metrics.get("conflict_cases_total", 0)
    parts.append('<section id="conflict-cases">')
    parts.append("<h2>Conflict Cases</h2>")
    parts.append(f"<p>Total conflicts: {total_cases} (showing first {len(cases)}).</p>")
    if cases:
        # Phase filter dropdown
        phases = sorted({c.get("phase", "") for c in cases if c.get("phase")})
        parts.append('<div class="filter-bar">')
        parts.append('<label for="phase-filter">Filter by phase:</label>')
        parts.append('<select id="phase-filter" onchange="filterTable(\'conflict-table\', \'phase-filter\')">')
        parts.append('<option value="">All</option>')
        for p in phases:
            parts.append(f'<option value="{_html_escape(p)}">{_html_escape(p)}</option>')
        parts.append("</select>")
        parts.append("</div>")
        # Sortable table
        parts.append('<table id="conflict-table">')
        parts.append("<thead><tr>")
        for i, header in enumerate(("Event", "Phase", "Phase dir", "Final dir", "Base dir")):
            parts.append(
                f'<th onclick="sortTable(\'conflict-table\', {i})">'
                f"{header} <span class=\"sort-icon\">&#9650;&#9660;</span></th>"
            )
        parts.append("</tr></thead>")
        parts.append("<tbody>")
        for c in cases:
            evt = _html_escape(c.get("event_id", ""))
            ph = _html_escape(c.get("phase", ""))
            parts.append(
                f'<tr data-phase="{ph}"><td>{evt}</td><td>{ph}</td>'
                f'<td>{_html_escape(c.get("phase_dir", ""))}</td>'
                f'<td>{_html_escape(c.get("final_dir", ""))}</td>'
                f'<td>{_html_escape(c.get("base_dir", ""))}</td></tr>'
            )
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No conflict cases.</p>")
    parts.append("</section>")

    # Inline JS (vanilla, no external library)
    parts.append("<script>")
    parts.append("""
function sortTable(tableId, colIdx) {
  var table = document.getElementById(tableId);
  var tbody = table.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));
  if (rows.length === 0) return;
  var isNumeric = rows[0].cells[colIdx] && rows[0].cells[colIdx].dataset.numeric === 'true';
  var direction = table.dataset.sortDir === 'asc' ? 'desc' : 'asc';
  table.dataset.sortDir = direction;
  rows.sort(function(a, b) {
    var av = a.cells[colIdx].textContent.trim();
    var bv = b.cells[colIdx].textContent.trim();
    var cmp = isNumeric ? (parseFloat(av) - parseFloat(bv)) : av.localeCompare(bv);
    return direction === 'asc' ? cmp : -cmp;
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
}
function filterTable(tableId, selectId) {
  var filter = document.getElementById(selectId).value;
  var rows = document.querySelectorAll('#' + tableId + ' tbody tr');
  rows.forEach(function(r) {
    r.style.display = (!filter || r.dataset.phase === filter) ? '' : 'none';
  });
}
""")
    parts.append("</script>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


def write_report(
    metrics: dict[str, Any],
    output_dir: Path,
    cases: list[dict[str, Any]] | None = None,
    run: dict[str, Any] | None = None,
) -> Path:
    """Write report.md + metrics.json + cases.jsonl + report.html to
    ``output_dir``.

    Returns the path to ``report.md`` (unchanged for backward compat).
    Creates ``output_dir`` if missing.

    When ``run`` is supplied it is stamped with ``generated_at`` and
    ``schema_version`` here — once, so the three files describe the same
    instant instead of each calling the clock on its way out.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if run is not None:
        run = dict(run)
        run.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
        run.setdefault("schema_version", REPLAY_REPORT_SCHEMA_VERSION)
    md_path = output_dir / "report.md"
    md_path.write_text(render_markdown(metrics, run), encoding="utf-8")
    (output_dir / "metrics.json").write_text(
        render_json(metrics, run), encoding="utf-8"
    )
    # HTML report (spec §4.5: HTML/Markdown/JSON triple format)
    (output_dir / "report.html").write_text(
        render_html(metrics, run), encoding="utf-8"
    )
    if cases is not None:
        cases_path = output_dir / "cases.jsonl"
        with cases_path.open("w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, default=str) + "\n")
    return md_path
