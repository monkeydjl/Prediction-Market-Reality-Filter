# Replay HTML Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a self-contained HTML report renderer (`render_html`) to `backend/app/replay/report.py`, mirroring the existing 7 Markdown sections, with an inline-CSS+JS sortable/filterable conflict cases table and direction-matrix heatmap. Extend `write_report` to emit `report.html`. Closes spec §4.5 (the last P1 gap).

**Architecture:** Single new pure function `render_html(metrics: dict) -> str` in the existing `report.py`, alongside `render_markdown` / `render_json`. It consumes the exact same `metrics` dict produced by `ReplayMetrics.to_dict()`. Output is one self-contained HTML document (inline `<style>` + inline `<script>`, no external `src`/`href`/CDN). `write_report` gains one extra `write_text` call; its return value stays `report.md`'s path for backward compat. No new files; no new dependencies.

**Tech Stack:** Python 3.14 stdlib only (`html.escape`, `datetime`, `json` already imported). Vanilla JS (no library). Inline CSS. UTF-8 output.

## Global Constraints

Copied verbatim from spec `2026-07-02-replay-html-report-design.md`:

- **Vocabulary lock:** HTML report must NOT contain `long` / `short` / `buy` / `sell` / `position` / `kelly` / `order` (case-insensitive). Direction vocabulary is locked to `YES` / `NO` / `WAIT` / `AVOID`. JS identifiers avoid the word `order` (use `direction` / `sortDir`).
- **Self-contained:** No `src=` pointing to external resources; no `href=` pointing to external resources (no `<link rel="stylesheet">`); no `@import`; no `url(` referencing external URLs; no CDN / http / https / protocol-relative URLs. All CSS inside `<style>`, all JS inside `<script>`. Output opens directly in a browser with no network.
- **Pure function:** `render_html(metrics) -> str` has no IO, no side effects, is deterministic. Shares the same `metrics` input as `render_markdown`.
- **UTF-8 encoding:** `<meta charset="UTF-8">` + `write_text(encoding="utf-8")`.
- **Backward compat:** `render_markdown` / `render_json` signatures and output unchanged. `write_report` return value unchanged (still returns `report.md` path). `cases` parameter behavior unchanged.
- **Empty-state text parity:** Empty-state strings must match the Markdown report text with `_italic_` markers stripped (see spec table).
- **No new files:** Only `backend/app/replay/report.py` and `backend/tests/test_replay_report.py` are modified.
- **No new dependencies:** stdlib only; `requirements.txt` unchanged.

---

## File Structure

- **Modify** `backend/app/replay/report.py` — add `render_html` + 5 private helpers (`_html_escape`, `_format_pct`, `_heatmap_color`, `_delta_badge`, `_direction_accuracy_bar`); extend `write_report` to also write `report.html`.
- **Modify** `backend/tests/test_replay_report.py` — add `TestRenderHtml` class with 11 test methods + reuse existing `_sample_metrics()` fixture.

No new files. `render_markdown` / `render_json` untouched.

---

## Task 1: Helpers + `render_html` skeleton with Summary + Brier

**Files:**
- Modify: `backend/app/replay/report.py` (add imports, helpers, `render_html` with Summary + Brier sections)
- Modify: `backend/tests/test_replay_report.py` (add `TestRenderHtml` with skeleton tests)

**Interfaces:**
- Produces: `render_html(metrics: dict[str, Any]) -> str` (partial — Summary + Brier only; later tasks extend it). Private helpers `_html_escape`, `_format_pct` available module-level.

- [ ] **Step 1: Write failing tests for helpers + skeleton**

Append to `backend/tests/test_replay_report.py` (after existing `TestRenderJson` class, before `if __name__ == "__main__":`):

```python
class TestRenderHtml(unittest.TestCase):
    def test_render_html_returns_non_empty_string(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        self.assertIsInstance(html, str)
        self.assertTrue(len(html) > 0)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))

    def test_render_html_includes_summary_section(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        self.assertIn('id="summary"', html)
        self.assertIn("Summary", html)
        # Summary cards: Total events, Direction changed, Resolved
        self.assertIn("Total events", html)
        self.assertIn("100", html)  # total value from _sample_metrics

    def test_render_html_includes_brier_section(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        self.assertIn('id="brier"', html)
        self.assertIn("Brier", html)
        self.assertIn("0.25", html)  # brier_mean from _sample_metrics
        # brier_frozen callout text
        self.assertIn("frozen", html.lower())

    def test_html_escape_helper(self):
        from app.replay.report import _html_escape
        self.assertEqual(_html_escape("<script>"), "&lt;script&gt;")
        self.assertEqual(_html_escape("a&b"), "a&amp;b")
        self.assertEqual(_html_escape('"quoted"'), "&quot;quoted&quot;")

    def test_format_pct_helper(self):
        from app.replay.report import _format_pct
        self.assertEqual(_format_pct(17, 100), "17.0%")
        self.assertEqual(_format_pct(0, 0), "N/A")
        self.assertEqual(_format_pct(3, 10), "30.0%")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_replay_report.py::TestRenderHtml -v`
Expected: ImportError / AttributeError — `render_html` and helpers not defined.

- [ ] **Step 3: Add helpers and `render_html` skeleton to `report.py`**

In `backend/app/replay/report.py`, first update the module docstring (line 1-3) to mention HTML:

Replace:
```python
"""Render ReplayMetrics to Markdown + JSON + cases.jsonl.

Pure rendering: no IO except ``write_report`` which writes the three files.
"""
```

With:
```python
"""Render ReplayMetrics to Markdown + JSON + HTML + cases.jsonl.

Pure rendering: no IO except ``write_report`` which writes the four files.
"""
```

Then, after the existing imports (after line 10 `from typing import Any`), add:

```python
from html import escape as _stdlib_escape
```

Then, after the `render_json` function (before `write_report`), insert the helpers and the start of `render_html`:

```python
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

    # Sections 2, 4, 5, 6, 7 added in later tasks.
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_replay_report.py::TestRenderHtml -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Run full report test file to verify no regression**

Run: `cd backend && python -m pytest tests/test_replay_report.py -v`
Expected: all tests PASS (existing 3 + new 5).

- [ ] **Step 6: Commit**

Write commit message to `backend/.commit_msg.tmp`:
```
feat(replay-html): add render_html skeleton with Summary + Brier sections

First task of spec §4.5 HTML report. Adds _html_escape, _format_pct,
_heatmap_color, _delta_badge helpers and render_html() returning a
self-contained HTML document. Currently renders Summary (3 metric cards)
and Brier (value + frozen callout) sections. Direction Matrix, Direction
Accuracy, LLM vs Fallback, Per-Phase Marginal, Conflict Cases sections
follow in subsequent tasks. 5 tests pass.
```

Run:
```bash
cd "e:\Github\Prediction Market Reality Filter"
git add backend/app/replay/report.py backend/tests/test_replay_report.py
git commit -F backend/.commit_msg.tmp
Remove-Item backend/.commit_msg.tmp
```

---

## Task 2: Direction Matrix heatmap + Direction Accuracy bar

**Files:**
- Modify: `backend/app/replay/report.py` (insert Direction Matrix + Direction Accuracy sections into `render_html` before Brier/after Summary, matching Markdown order)
- Modify: `backend/tests/test_replay_report.py` (add 3 tests)

**Interfaces:**
- Consumes: `_heatmap_color`, `_delta_badge`, `_format_pct` from Task 1.
- Produces: extended `render_html` now covering Summary + Direction Matrix + Brier + Direction Accuracy.

- [ ] **Step 1: Write failing tests for Direction Matrix + Direction Accuracy**

Add to `TestRenderHtml` class in `backend/tests/test_replay_report.py`:

```python
    def test_render_html_includes_direction_matrix_heatmap(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        self.assertIn('id="direction-matrix"', html)
        self.assertIn("Direction Matrix", html)
        # 4x4 table headers (YES/NO/WAIT/AVOID as both row and col)
        self.assertIn("<th>YES</th>", html)
        self.assertIn("<th>AVOID</th>", html)
        # Heatmap: at least one cell has inline background-color
        self.assertIn("background-color: rgba(", html)
        # Diagonal cells (green) and off-diagonal (crimson) both present
        self.assertIn("rgba(34, 139, 34", html)  # green diagonal
        self.assertIn("rgba(220, 20, 60", html)  # crimson off-diagonal

    def test_render_html_includes_direction_accuracy_section(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        self.assertIn('id="direction-accuracy"', html)
        self.assertIn("Direction Accuracy", html)
        # Original and Replayed bars
        self.assertIn("bar-original", html)
        self.assertIn("bar-replayed", html)
        # Delta badge (delta=0.05 in _sample_metrics → improved)
        self.assertIn("badge-improved", html)

    def test_render_html_direction_accuracy_no_resolved_samples(self):
        from app.replay.report import render_html
        m = _sample_metrics()
        m["direction_correct_resolved_count"] = 0
        m["direction_correct_original"] = 0
        m["direction_correct_replayed"] = 0
        m["direction_correct_delta"] = None
        html = render_html(m)
        self.assertIn("No resolved samples.", html)
        # Bars should NOT be rendered when resolved_count=0
        self.assertNotIn("bar-original", html)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_replay_report.py::TestRenderHtml::test_render_html_includes_direction_matrix_heatmap tests/test_replay_report.py::TestRenderHtml::test_render_html_includes_direction_accuracy_section -v`
Expected: FAIL — sections not present.

- [ ] **Step 3: Add Direction Matrix + Direction Accuracy sections to `render_html`**

In `backend/app/replay/report.py`, inside `render_html`, locate the line `# Section 3: Brier` and insert BEFORE it the Direction Matrix section. Then after the Brier `</section>`, insert the Direction Accuracy section.

Insert Direction Matrix (before `# Section 3: Brier`):

```python
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
```

Insert Direction Accuracy (after the Brier `</section>` line, before `# Sections 2, 4, 5, 6, 7 added in later tasks.`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_replay_report.py::TestRenderHtml -v`
Expected: 8 tests PASS (5 from Task 1 + 3 new).

- [ ] **Step 5: Commit**

Write commit message to `backend/.commit_msg.tmp`:
```
feat(replay-html): add Direction Matrix heatmap + Direction Accuracy bars

Direction Matrix renders as a 4x4 table with green diagonal (unchanged)
and crimson off-diagonal (changed) cells, intensity scaled by count/max.
Direction Accuracy shows horizontal bar comparison (Original % vs Replayed %)
with a colored delta badge (improved=green/regressed=red/unchanged=grey).
Empty state (resolved_count=0) shows "No resolved samples." 8 tests pass.
```

Run:
```bash
cd "e:\Github\Prediction Market Reality Filter"
git add backend/app/replay/report.py backend/tests/test_replay_report.py
git commit -F backend/.commit_msg.tmp
Remove-Item backend/.commit_msg.tmp
```

---

## Task 3: LLM vs Fallback + Per-Phase Marginal + Conflict Cases (sortable + filterable)

**Files:**
- Modify: `backend/app/replay/report.py` (add 3 sections + inline JS)
- Modify: `backend/tests/test_replay_report.py` (add 5 tests)

**Interfaces:**
- Consumes: helpers from Task 1, `render_html` structure from Tasks 1-2.
- Produces: complete `render_html` covering all 7 sections + inline `<script>`.

- [ ] **Step 1: Write failing tests for the 3 new sections + empty states**

Add to `TestRenderHtml` class:

```python
    def test_render_html_includes_llm_vs_fallback_section(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        self.assertIn('id="llm-vs-fallback"', html)
        self.assertIn("LLM vs Fallback", html)
        # Table headers
        self.assertIn("<th>Quality</th>", html)
        self.assertIn("<th>N</th>", html)
        self.assertIn("brier_mean", html.lower())
        # Sample data: llm bucket with n=30
        self.assertIn("llm", html)
        self.assertIn("deterministic_fallback", html)

    def test_render_html_includes_per_phase_marginal_section(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        self.assertIn('id="per-phase-marginal"', html)
        self.assertIn("Per-Phase Marginal Contribution", html)
        # Table headers
        self.assertIn("<th>Phase</th>", html)
        self.assertIn("Downgrades caused", html)
        # Sample data: decision_quality with downgrades_caused=12
        self.assertIn("decision_quality", html)
        # Inline bar (width style)
        self.assertIn("width:", html)

    def test_render_html_includes_sortable_conflict_table(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        self.assertIn('id="conflict-cases"', html)
        self.assertIn("Conflict Cases", html)
        # Table id + sortable headers
        self.assertIn('id="conflict-table"', html)
        self.assertIn("onclick=\"sortTable('conflict-table'", html)
        # Sort direction icons
        self.assertIn("&#9650;", html)  # ▲ up arrow
        self.assertIn("&#9660;", html)  # ▼ down arrow

    def test_render_html_includes_phase_filter(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        # Phase filter dropdown
        self.assertIn('id="phase-filter"', html)
        self.assertIn("onchange=\"filterTable('conflict-table', 'phase-filter')\"", html)
        # "All" default option
        self.assertIn("<option value=\"\">All</option>", html)
        # phase values from _sample_metrics conflict_cases
        self.assertIn("source_reliability", html)
        # data-phase attribute on rows
        self.assertIn("data-phase=", html)

    def test_render_html_handles_empty_conflict_cases(self):
        from app.replay.report import render_html
        m = _sample_metrics()
        m["conflict_cases"] = []
        m["conflict_cases_total"] = 0
        html = render_html(m)
        self.assertIn("No conflict cases.", html)
        # Table should NOT be rendered when empty
        self.assertNotIn('id="conflict-table"', html)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_replay_report.py::TestRenderHtml -v`
Expected: 5 new tests FAIL (sections not present), 8 existing PASS.

- [ ] **Step 3: Add 3 sections + inline JS to `render_html`**

In `backend/app/replay/report.py`, inside `render_html`, replace the comment `# Sections 2, 4, 5, 6, 7 added in later tasks.` and `parts.append("</body>")` with the 3 new sections + JS + closing tags.

Replace:
```python
    # Sections 2, 4, 5, 6, 7 added in later tasks.
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)
```

With:
```python
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
            parts.append(
                f"<tr><td>{_html_escape(phase)}</td>"
                f'<td><div class="bar-container" style="height: 16px; width: {bar_width:.1f}%;'
                f' min-width: 30px;"><div class="bar bar-replayed" style="width: 100%;">'
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_replay_report.py::TestRenderHtml -v`
Expected: 13 tests PASS (8 from Tasks 1-2 + 5 new).

- [ ] **Step 5: Commit**

Write commit message to `backend/.commit_msg.tmp`:
```
feat(replay-html): add LLM/Fallback, Per-Phase, Conflict Cases + inline JS

Completes all 7 sections of render_html. LLM vs Fallback table, Per-Phase
Marginal table with inline downgrade bars, Conflict Cases table with
sortable headers (click to toggle asc/desc) and phase filter dropdown
(default "All"). Inline vanilla JS (sortTable + filterTable), no external
library. Empty states match Markdown text. 13 tests pass.
```

Run:
```bash
cd "e:\Github\Prediction Market Reality Filter"
git add backend/app/replay/report.py backend/tests/test_replay_report.py
git commit -F backend/.commit_msg.tmp
Remove-Item backend/.commit_msg.tmp
```

---

## Task 4: Extend `write_report` + global assertions (vocabulary, self-contained, XSS, all-sections)

**Files:**
- Modify: `backend/app/replay/report.py` (extend `write_report` to emit `report.html`)
- Modify: `backend/tests/test_replay_report.py` (add 4 global tests + integration test)

**Interfaces:**
- Consumes: complete `render_html` from Tasks 1-3.
- Produces: `write_report` now writes 4 files (`report.md`, `metrics.json`, `report.html`, `cases.jsonl`). All spec acceptance criteria satisfied.

- [ ] **Step 1: Write failing tests for global assertions + `write_report` integration**

Add to `TestRenderHtml` class:

```python
    def test_render_html_includes_all_sections(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        for section_id in (
            "summary",
            "direction-matrix",
            "brier",
            "direction-accuracy",
            "llm-vs-fallback",
            "per-phase-marginal",
            "conflict-cases",
        ):
            self.assertIn(f'id="{section_id}"', html)

    def test_render_html_contains_no_banned_terms(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        lower = html.lower()
        for term in ("long", "short", "buy", "sell", "position", "kelly", "order"):
            self.assertNotIn(term, lower, f"banned term '{term}' found in HTML")

    def test_render_html_is_self_contained(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        # No external resource references
        self.assertNotIn("src=\"http", html)
        self.assertNotIn("src='http", html)
        self.assertNotIn("href=\"http", html)
        self.assertNotIn("href='http", html)
        self.assertNotIn("@import", html)
        self.assertNotIn("url(http", html)
        self.assertNotIn("url('http", html)
        self.assertNotIn("url(\"http", html)
        # No <link> stylesheet
        self.assertNotIn("<link", html)
        # No external script src
        self.assertNotIn("<script src", html)

    def test_render_html_escapes_event_ids(self):
        from app.replay.report import render_html
        m = _sample_metrics()
        m["conflict_cases"] = [
            {
                "event_id": "<script>alert(1)</script>",
                "phase": "test_phase",
                "phase_dir": "YES",
                "final_dir": "WAIT",
                "base_dir": "YES",
            }
        ]
        m["conflict_cases_total"] = 1
        html = render_html(m)
        # Raw <script> tag from event_id must be escaped
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        # The only <script> tags in the output should be the inline JS block
        # (count of "<script" should be exactly 1 — the inline JS)
        self.assertEqual(html.count("<script>"), 1)

    def test_write_report_creates_html_file(self):
        import tempfile
        from app.replay.report import write_report
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            md_path = write_report(_sample_metrics(), out_dir, cases=None)
            # report.html must exist and be non-empty
            html_path = out_dir / "report.html"
            self.assertTrue(html_path.exists())
            content = html_path.read_text(encoding="utf-8")
            self.assertTrue(len(content) > 0)
            self.assertTrue(content.startswith("<!DOCTYPE html>"))
            # write_report still returns report.md path (backward compat)
            self.assertEqual(md_path, out_dir / "report.md")
            # report.md and metrics.json still produced (unchanged)
            self.assertTrue((out_dir / "report.md").exists())
            self.assertTrue((out_dir / "metrics.json").exists())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_replay_report.py::TestRenderHtml::test_write_report_creates_html_file -v`
Expected: FAIL — `report.html` not created by `write_report`.

Run: `cd backend && python -m pytest tests/test_replay_report.py::TestRenderHtml::test_render_html_contains_no_banned_terms -v`
Expected: May fail if "order" appears in `data-numeric` or `sortDir` — check and fix identifiers if needed. (Note: `sortDir` contains "dir" not "order", so should pass. `data-numeric` is safe.)

- [ ] **Step 3: Extend `write_report` to emit `report.html`**

In `backend/app/replay/report.py`, locate `write_report` and replace the function body to add the HTML write call. Replace the entire `write_report` function:

```python
def write_report(
    metrics: dict[str, Any],
    output_dir: Path,
    cases: list[dict[str, Any]] | None = None,
) -> Path:
    """Write report.md + metrics.json + cases.jsonl + report.html to
    ``output_dir``.

    Returns the path to ``report.md`` (unchanged for backward compat).
    Creates ``output_dir`` if missing.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "report.md"
    md_path.write_text(render_markdown(metrics), encoding="utf-8")
    (output_dir / "metrics.json").write_text(
        render_json(metrics), encoding="utf-8"
    )
    # HTML report (spec §4.5: HTML/Markdown/JSON triple format)
    (output_dir / "report.html").write_text(
        render_html(metrics), encoding="utf-8"
    )
    if cases is not None:
        cases_path = output_dir / "cases.jsonl"
        with cases_path.open("w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, default=str) + "\n")
    return md_path
```

- [ ] **Step 4: Run all TestRenderHtml tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_replay_report.py::TestRenderHtml -v`
Expected: 17 tests PASS (13 from Tasks 1-3 + 4 new).

- [ ] **Step 5: Run full report test file for regression check**

Run: `cd backend && python -m pytest tests/test_replay_report.py -v`
Expected: all PASS (existing 3 + new 17 = 20).

- [ ] **Step 6: Run full backend suite for regression check**

Run: `cd backend && python -m pytest --ignore=tests/test_gbm_engine.py -q`
Expected: all PASS, 0 failures (pre-existing `test_gbm_engine.py` excluded as env issue).

- [ ] **Step 7: Commit**

Write commit message to `backend/.commit_msg.tmp`:
```
feat(replay-html): extend write_report + global assertions, close spec §4.5

write_report now emits report.html alongside report.md/metrics.json/
cases.jsonl (backward-compat: return value unchanged). Adds 4 global
tests: all-sections coverage, vocabulary lock (no long/short/buy/sell/
position/kelly/order), self-contained (no external src/href/@import/url),
XSS escape (event_id with <script> is escaped). Integration test verifies
write_report produces report.html. 17 TestRenderHtml tests pass; full
backend suite green. Spec §4.5 (HTML/Markdown/JSON) now DONE.
```

Run:
```bash
cd "e:\Github\Prediction Market Reality Filter"
git add backend/app/replay/report.py backend/tests/test_replay_report.py
git commit -F backend/.commit_msg.tmp
Remove-Item backend/.commit_msg.tmp
```

---

## Self-Review

### 1. Spec coverage

| Spec requirement | Task |
|---|---|
| `render_html(metrics) -> str` pure function | Task 1 (skeleton) + Tasks 2-3 (sections) |
| 7 sections mirror Markdown | Task 1 (Summary + Brier), Task 2 (Direction Matrix + Direction Accuracy), Task 3 (LLM/Fallback + Per-Phase + Conflict Cases) |
| Direction Matrix 4x4 heatmap (green diagonal, crimson off-diagonal) | Task 2 |
| Direction Accuracy horizontal bars + delta badge | Task 2 |
| Conflict Cases sortable table (click headers) | Task 3 |
| Conflict Cases phase filter dropdown (with "All") | Task 3 |
| Per-Phase downgrades inline bar | Task 3 |
| Vocabulary lock (no banned terms) | Task 4 (test asserts) |
| Self-contained (no external resources) | Task 4 (test asserts) |
| UTF-8 encoding | Task 1 (`<meta charset>`) + Task 4 (`write_text(encoding="utf-8")`) |
| Backward compat (render_markdown/json unchanged, write_report returns md path) | Task 4 (test asserts) |
| Empty-state text parity | Tasks 1-3 (inline strings match spec table) |
| `write_report` emits `report.html` | Task 4 |
| XSS escape | Task 1 (`_html_escape` helper) + Task 4 (test asserts) |
| No new files | All tasks (only modify 2 existing files) |
| No new dependencies | All tasks (stdlib only) |

No gaps.

### 2. Placeholder scan

No TBD/TODO/"implement later"/"add appropriate error handling" found. All code blocks are complete.

### 3. Type consistency

- `render_html(metrics: dict[str, Any]) -> str` — consistent across all tasks.
- `_html_escape(text: str) -> str` — defined Task 1, used Tasks 3.
- `_format_pct(num: int, denom: int) -> str` — defined Task 1, used Task 1.
- `_heatmap_color(count: int, max_count: int, is_diagonal: bool) -> str` — defined Task 1, used Task 2.
- `_delta_badge(delta: float | None) -> str` — defined Task 1, used Task 2.
- `write_report(metrics, output_dir, cases=None) -> Path` — signature unchanged, Task 4 extends body.
- `_sample_metrics()` fixture — defined in existing test file, reused in all tasks.
- JS function names `sortTable` / `filterTable` — consistent between HTML `onclick`/`onchange` attributes and `<script>` definitions in Task 3.

No type inconsistencies.

### 4. Banned-term check on plan code

Scanned all code blocks for `long`/`short`/`buy`/`sell`/`position`/`kelly`/`order`:
- No Python identifiers use these terms.
- JS uses `direction` / `sortDir` (not `order`).
- `data-numeric` attribute (not `data-order`).
- CSS classes: `bar-original` / `bar-replayed` / `badge-improved` / `badge-regressed` / `badge-neutral` (none banned).
- HTML text content: direction values `YES`/`NO`/`WAIT`/`AVOID` only.

Clean.
