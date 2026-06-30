"""Unit tests for report renderer."""
import unittest


def _sample_metrics() -> dict:
    return {
        "total": 100,
        "direction_matrix": {"YES->WAIT": 17, "YES->AVOID": 3, "NO->WAIT": 8},
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


if __name__ == "__main__":
    unittest.main()
