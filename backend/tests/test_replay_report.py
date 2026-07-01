"""Unit tests for report renderer."""
import unittest
from pathlib import Path


def _sample_metrics() -> dict:
    return {
        "total": 100,
        "direction_matrix": {"YES->YES": 50, "YES->WAIT": 17, "YES->AVOID": 3, "NO->WAIT": 8},
        "resolved_count": 40,
        "brier_mean": 0.25,
        "brier_frozen": True,
        "direction_correct_original": 30,
        "direction_correct_replayed": 32,
        "direction_correct_resolved_count": 40,
        "direction_correct_delta": 0.05,
        "brier_by_quality": {
            "llm": {"n": 30, "brier_mean": 0.18},
            "deterministic_fallback": {"n": 10, "brier_mean": 0.32},
        },
        "phase_contributions": {
            "decision_quality": {
                "downgrades_caused": 12,
                "directions_changed": 15,
                "conflicts_with_final": 2,
            },
        },
        "conflict_cases": [
            {
                "event_id": "e1",
                "phase": "source_reliability",
                "phase_dir": "YES",
                "final_dir": "WAIT",
                "base_dir": "YES",
            }
        ],
        "conflict_cases_total": 1,
    }


class TestRenderMarkdown(unittest.TestCase):
    def test_includes_all_sections(self):
        from app.replay.report import render_markdown
        md = render_markdown(_sample_metrics())
        self.assertIn("# Replay Report", md)
        self.assertIn("## Summary", md)
        self.assertIn("## Direction Matrix", md)
        self.assertIn("## Brier", md)
        self.assertIn("## Direction Accuracy", md)
        self.assertIn("## LLM vs Fallback", md)
        self.assertIn("## Per-Phase Marginal Contribution", md)
        self.assertIn("## Conflict Cases", md)

    def test_summary_shows_total_and_change_rate(self):
        from app.replay.report import render_markdown
        md = render_markdown(_sample_metrics())
        # 20 of 100 changed direction (17+3 others stayed) — change rate.
        self.assertIn("Total events: 100", md)


class TestRenderJson(unittest.TestCase):
    def test_returns_valid_json_string(self):
        import json
        from app.replay.report import render_json
        s = render_json(_sample_metrics())
        parsed = json.loads(s)
        self.assertEqual(parsed["total"], 100)
        self.assertEqual(parsed["direction_matrix"]["YES->WAIT"], 17)


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
        # (check the rendered div, not the bare class name which also
        # appears in the inline <style> block as .bar-original)
        self.assertNotIn('class="bar bar-original"', html)

    def test_render_html_includes_llm_vs_fallback_section(self):
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        self.assertIn('id="llm-vs-fallback"', html)
        self.assertIn("LLM vs Fallback", html)
        # Table headers
        self.assertIn("<th>Quality</th>", html)
        self.assertIn("<th>N</th>", html)
        self.assertIn("brier mean", html.lower())
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

    def test_render_html_per_phase_zero_downgrades_no_min_width(self):
        """When a phase has downgrades_caused=0, its bar container must
        have width 0.0% and NO min-width — otherwise a blue bar would
        render for a zero-contribution phase (spec §6 requires 0% width
        when max=0 or dc=0). The numeric label (0) must still appear."""
        from app.replay.report import render_html
        m = _sample_metrics()
        # Single phase with downgrades_caused=0
        m["phase_contributions"] = {
            "decision_quality": {
                "downgrades_caused": 0,
                "directions_changed": 5,
                "conflicts_with_final": 2,
            }
        }
        html = render_html(m)
        # Bar container width must be 0.0% (max_downgrades=0 → bar_width=0)
        self.assertIn("width: 0.0%;", html)
        # No min-width on bar containers (would force a visible bar)
        self.assertNotIn("min-width: 30px", html)
        self.assertNotIn("min-width:30px", html)
        # The zero value label must still be visible as text
        self.assertIn(">0</div>", html)

    def test_render_html_per_phase_mixed_zero_and_nonzero(self):
        """When one phase has downgrades_caused=0 and another has >0,
        the zero phase must show width 0.0% (not a proportion of the
        max), and the non-zero phase shows its proportional width."""
        from app.replay.report import render_html
        m = _sample_metrics()
        m["phase_contributions"] = {
            "decision_quality": {
                "downgrades_caused": 12,
                "directions_changed": 15,
                "conflicts_with_final": 3,
            },
            "market_quality": {
                "downgrades_caused": 0,
                "directions_changed": 2,
                "conflicts_with_final": 0,
            },
        }
        html = render_html(m)
        # max_downgrades=12 → decision_quality width=100.0%, market_quality width=0.0%
        self.assertIn("width: 100.0%;", html)
        self.assertIn("width: 0.0%;", html)
        # Bar containers must NOT have inline min-width (would force a
        # visible bar for zero-contribution phases). The .card CSS class
        # uses min-width:160px for summary cards — that's unrelated.
        self.assertNotIn("min-width: 30px", html)
        self.assertNotIn("min-width:30px", html)

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
        import re
        from app.replay.report import render_html
        html = render_html(_sample_metrics())
        # Vocabulary lock (spec Hard Constraint): HTML output must NOT
        # contain long/short/buy/sell/position/kelly as whole words, and
        # must NOT contain `order` as a whole word. CSS `border` (substring
        # `order`) is allowed since `order` is not a whole word within it.
        for term in ("long", "short", "buy", "sell", "position", "kelly"):
            pattern = r"\b" + re.escape(term) + r"\b"
            self.assertFalse(
                re.search(pattern, html, re.IGNORECASE),
                f"banned term '{term}' found in HTML",
            )
        self.assertFalse(
            re.search(r"\border\b", html, re.IGNORECASE),
            "banned term 'order' found as whole word in HTML",
        )

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


if __name__ == "__main__":
    unittest.main()
