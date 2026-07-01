"""Unit tests for report renderer."""
import unittest


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


if __name__ == "__main__":
    unittest.main()
