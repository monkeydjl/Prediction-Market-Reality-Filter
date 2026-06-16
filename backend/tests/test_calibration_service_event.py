"""Tests for event-layer calibration (calibration_service_event).

Pure-math unit tests for brier/skill/grade/score_event/summarize. The store
and route integration (calibration attached to a resolved record, the
/calibration aggregate endpoint) is covered in tests/test_event_store.py.
"""

import math
import unittest

from app.services.calibration_service_event import (
    brier_score,
    grade,
    score_event,
    skill_score,
    summarize,
)


class BrierScoreTests(unittest.TestCase):
    def test_perfect_prediction_is_zero(self):
        self.assertEqual(brier_score(80, 80), 0.0)
        self.assertEqual(brier_score(0, 0), 0.0)
        self.assertEqual(brier_score(100, 100), 0.0)

    def test_random_guess_is_quarter(self):
        # A 50% prediction against a binary outcome (0 or 100) = 0.25.
        self.assertAlmostEqual(brier_score(50, 0), 0.25)
        self.assertAlmostEqual(brier_score(50, 100), 0.25)

    def test_fully_wrong_is_one(self):
        self.assertEqual(brier_score(100, 0), 1.0)
        self.assertEqual(brier_score(0, 100), 1.0)


class SkillScoreTests(unittest.TestCase):
    def test_perfect_is_one(self):
        self.assertEqual(skill_score(0.0), 1.0)

    def test_random_is_zero(self):
        self.assertAlmostEqual(skill_score(0.25), 0.0)

    def test_worse_than_random_is_negative(self):
        self.assertLess(skill_score(0.5), 0.0)


class GradeTests(unittest.TestCase):
    def test_grade_bands(self):
        self.assertEqual(grade(0.00), "EXCELLENT")
        self.assertEqual(grade(0.05), "EXCELLENT")
        self.assertEqual(grade(0.06), "GOOD")
        self.assertEqual(grade(0.10), "GOOD")
        self.assertEqual(grade(0.11), "ACCEPTABLE")
        self.assertEqual(grade(0.15), "ACCEPTABLE")
        self.assertEqual(grade(0.16), "POOR")
        self.assertEqual(grade(0.20), "POOR")
        self.assertEqual(grade(0.21), "RANDOM_LEVEL")


class ScoreEventTests(unittest.TestCase):
    def test_output_shape_and_values(self):
        result = score_event(
            estimated=70.0,
            actual_outcome=100.0,
            trajectory_observations=4,
            trajectory_span_hours=12.5,
        )
        # Brier = ((70-100)/100)^2 = 0.09 -> GOOD
        self.assertEqual(result["brier_score"], 0.09)
        self.assertEqual(result["grade"], "GOOD")
        self.assertEqual(result["estimated_probability"], 70.0)
        self.assertEqual(result["actual_outcome"], 100.0)
        self.assertEqual(result["trajectory_observations"], 4)
        self.assertEqual(result["trajectory_span_hours"], 12.5)

    def test_trajectory_context_does_not_affect_score(self):
        """The trajectory_* fields are context, not score inputs."""
        a = score_event(60.0, 60.0, 1, None)
        b = score_event(60.0, 60.0, 50, 1000.0)
        self.assertEqual(a["brier_score"], b["brier_score"])
        self.assertEqual(a["grade"], b["grade"])

    def test_none_span_hours_passes_through(self):
        result = score_event(50.0, 50.0, 0, None)
        self.assertIsNone(result["trajectory_span_hours"])
        self.assertEqual(result["trajectory_observations"], 0)

    def test_clamps_out_of_range_estimated_and_actual(self):
        """Regression for defensive clamp (#1): out-of-range inputs are clamped
        to 0-100 so the Brier stays finite and the grade stays meaningful."""
        # estimated 150 -> clamped to 100; actual -20 -> clamped to 0.
        result = score_event(estimated=150.0, actual_outcome=-20.0,
                             trajectory_observations=1, trajectory_span_hours=1.0)
        self.assertEqual(result["estimated_probability"], 100.0)
        self.assertEqual(result["actual_outcome"], 0.0)
        # ((100-0)/100)^2 = 1.0 -> fully wrong -> RANDOM_LEVEL
        self.assertEqual(result["brier_score"], 1.0)
        self.assertEqual(result["grade"], "RANDOM_LEVEL")

    def test_non_finite_input_falls_back_to_neutral(self):
        """NaN/inf inputs fall back to the neutral 50% prior rather than
        producing a non-finite Brier."""
        result = score_event(estimated=float("nan"), actual_outcome=float("inf"),
                             trajectory_observations=0, trajectory_span_hours=None)
        # Both clamp to 50 -> Brier 0.0 (perfect, since both equal).
        self.assertEqual(result["brier_score"], 0.0)
        self.assertTrue(math.isfinite(result["brier_score"]))


class SummarizeTests(unittest.TestCase):
    def test_empty_is_no_data(self):
        result = summarize([])
        self.assertEqual(result["overall"]["n"], 0)
        self.assertEqual(result["overall"]["grade"], "no_data")
        self.assertIsNone(result["overall"]["brier_score"])
        self.assertEqual(result["by_source"], {})

    def test_overall_aggregates_brier(self):
        events = [
            {"calibration": {"brier_score": 0.0}, "source": {"platform": "Polymarket"}},
            {"calibration": {"brier_score": 0.2}, "source": {"platform": "Polymarket"}},
        ]
        result = summarize(events)
        self.assertEqual(result["overall"]["n"], 2)
        self.assertAlmostEqual(result["overall"]["brier_score"], 0.1)

    def test_by_source_breakdown(self):
        events = [
            {"calibration": {"brier_score": 0.0}, "source": {"platform": "Polymarket"}},
            {"calibration": {"brier_score": 0.4}, "source": {"platform": "Manifold"}},
            {"calibration": {"brier_score": 0.2}, "source": {"platform": "Polymarket"}},
        ]
        result = summarize(events)
        # Polymarket: avg(0.0, 0.2) = 0.1; Manifold: 0.4
        self.assertEqual(set(result["by_source"].keys()), {"Polymarket", "Manifold"})
        self.assertAlmostEqual(result["by_source"]["Polymarket"]["brier_score"], 0.1)
        self.assertEqual(result["by_source"]["Polymarket"]["n"], 2)
        self.assertEqual(result["by_source"]["Manifold"]["brier_score"], 0.4)

    def test_by_base_rate_category_breakdown(self):
        events = [
            {"calibration": {"brier_score": 0.0}, "base_rate_category": "fed_hike"},
            {"calibration": {"brier_score": 0.2}, "base_rate_category": "fed_hike"},
            {"calibration": {"brier_score": 0.4}, "base_rate_category": "crypto_price_btc"},
        ]
        result = summarize(events)
        self.assertEqual(
            set(result["by_base_rate_category"].keys()),
            {"fed_hike", "crypto_price_btc"},
        )
        self.assertAlmostEqual(
            result["by_base_rate_category"]["fed_hike"]["brier_score"], 0.1
        )
        self.assertEqual(result["by_base_rate_category"]["fed_hike"]["n"], 2)
        self.assertEqual(
            result["by_base_rate_category"]["crypto_price_btc"]["brier_score"], 0.4
        )

    def test_missing_base_rate_category_falls_back_to_unknown(self):
        events = [
            {"calibration": {"brier_score": 0.1}},  # no base_rate_category
            {"calibration": {"brier_score": 0.1}, "base_rate_category": None},
        ]
        result = summarize(events)
        self.assertEqual(set(result["by_base_rate_category"].keys()), {"unknown"})
        self.assertEqual(result["by_base_rate_category"]["unknown"]["n"], 2)

    def test_empty_returns_empty_category_breakdown(self):
        result = summarize([])
        self.assertEqual(result["by_base_rate_category"], {})

    def test_skips_events_without_calibration(self):
        events = [
            {"calibration": {"brier_score": 0.1}, "source": {"platform": "Polymarket"}},
            {"calibration": None, "source": {"platform": "Manifold"}},
            {"source": {"platform": "Kalshi"}},  # no calibration key at all
        ]
        result = summarize(events)
        self.assertEqual(result["overall"]["n"], 1)

    def test_source_falls_back_to_type_then_unknown(self):
        events = [
            {"calibration": {"brier_score": 0.1}, "source": {"type": "open_web"}},
            {"calibration": {"brier_score": 0.1}, "source": {}},
        ]
        result = summarize(events)
        self.assertIn("open_web", result["by_source"])
        self.assertIn("unknown", result["by_source"])

    def test_skips_non_finite_brier_scores(self):
        """Regression for summarize finite-filter (#1): NaN/inf Brier values
        in stored records are skipped so they do not poison the aggregate."""
        events = [
            {"calibration": {"brier_score": 0.1}, "source": {"platform": "Polymarket"}},
            {"calibration": {"brier_score": float("nan")}, "source": {"platform": "Manifold"}},
            {"calibration": {"brier_score": float("inf")}, "source": {"platform": "Kalshi"}},
            {"calibration": {"brier_score": "not-a-number"}, "source": {"platform": "Kalshi"}},
        ]
        result = summarize(events)
        # Only the finite 0.1 survives.
        self.assertEqual(result["overall"]["n"], 1)
        self.assertAlmostEqual(result["overall"]["brier_score"], 0.1)


if __name__ == "__main__":
    unittest.main()
