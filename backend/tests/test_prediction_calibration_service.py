"""Tests for prediction_calibration_service (Phase 3 pure helpers).

Locks the spec invariants:
- Edge buckets use half-open intervals [low, high) with boundary values
  belonging to the UPPER bucket (edge=5.0 -> "5-10", not "0-5").
- Edge bucketing uses abs(raw_edge); sign is preserved on the prediction row.
- direction_correct checks YES/NO recommendation vs outcome, returns None for
  WAIT/AVOID/empty (non-directional).
- Brier score convention: outcome >= 50 = YES, < 50 = NO (per spec
  clarification that direction_correct is separate from Brier).
- confidence_bucket validates {high, medium, low} and returns "unknown" for
  anything else (including pre-Phase-3 predictions that lack the field).
- build_prediction_snapshot extracts from event record without raising on
  missing/malformed fields (best-effort).
- build_resolution_buckets combines all three bucket computations.
"""
import math
import unittest

from app.services.prediction_calibration_service import (
    build_prediction_snapshot,
    build_resolution_buckets,
    compute_confidence_bucket,
    compute_direction_correct,
    compute_edge_bucket,
)


class ComputeEdgeBucketTests(unittest.TestCase):
    """Edge bucket boundaries — half-open intervals [low, high).

    Per spec § Edge Bucket Boundaries: boundary values belong to the UPPER
    bucket. Negative edges use absolute value for bucketing.
    """

    def test_zero_edge_lands_in_0_5(self):
        self.assertEqual(compute_edge_bucket(0.0), "0-5")

    def test_just_below_5_lands_in_0_5(self):
        self.assertEqual(compute_edge_bucket(4.99), "0-5")

    def test_boundary_5_lands_in_upper_bucket_5_10(self):
        """Spec: edge=5.0 belongs to '5-10', not '0-5'."""
        self.assertEqual(compute_edge_bucket(5.0), "5-10")

    def test_just_below_10_lands_in_5_10(self):
        self.assertEqual(compute_edge_bucket(9.99), "5-10")

    def test_boundary_10_lands_in_upper_bucket_10_20(self):
        self.assertEqual(compute_edge_bucket(10.0), "10-20")

    def test_just_below_20_lands_in_10_20(self):
        self.assertEqual(compute_edge_bucket(19.99), "10-20")

    def test_boundary_20_lands_in_upper_bucket_20_plus(self):
        self.assertEqual(compute_edge_bucket(20.0), "20+")

    def test_large_edge_lands_in_20_plus(self):
        self.assertEqual(compute_edge_bucket(50.0), "20+")

    def test_negative_edge_uses_absolute_value(self):
        """Spec: negative edges use absolute value for bucketing."""
        self.assertEqual(compute_edge_bucket(-5.0), "5-10")
        self.assertEqual(compute_edge_bucket(-13.0), "10-20")
        self.assertEqual(compute_edge_bucket(-25.0), "20+")

    def test_positive_and_negative_same_magnitude_same_bucket(self):
        """+13 and -13 land in the same bucket (sign-agnostic bucketing)."""
        self.assertEqual(compute_edge_bucket(13.0), compute_edge_bucket(-13.0))

    def test_none_returns_empty_string(self):
        self.assertEqual(compute_edge_bucket(None), "")

    def test_non_numeric_returns_empty_string(self):
        self.assertEqual(compute_edge_bucket("not-a-number"), "")
        self.assertEqual(compute_edge_bucket(float("nan")), "")
        self.assertEqual(compute_edge_bucket(float("inf")), "")

    def test_string_numeric_returns_bucket(self):
        """String that parses as float still produces a bucket (defensive)."""
        self.assertEqual(compute_edge_bucket("7.5"), "5-10")


class ComputeConfidenceBucketTests(unittest.TestCase):
    """Confidence bucket validates {high, medium, low} and returns 'unknown'
    for anything else."""

    def test_high_returns_high(self):
        self.assertEqual(compute_confidence_bucket("high"), "high")

    def test_medium_returns_medium(self):
        self.assertEqual(compute_confidence_bucket("medium"), "medium")

    def test_low_returns_low(self):
        self.assertEqual(compute_confidence_bucket("low"), "low")

    def test_case_insensitive(self):
        self.assertEqual(compute_confidence_bucket("HIGH"), "high")
        self.assertEqual(compute_confidence_bucket("Medium"), "medium")

    def test_whitespace_stripped(self):
        self.assertEqual(compute_confidence_bucket("  high  "), "high")

    def test_empty_string_returns_unknown(self):
        self.assertEqual(compute_confidence_bucket(""), "unknown")

    def test_none_returns_unknown(self):
        self.assertEqual(compute_confidence_bucket(None), "unknown")

    def test_unrecognized_returns_unknown(self):
        self.assertEqual(compute_confidence_bucket("very-high"), "unknown")
        self.assertEqual(compute_confidence_bucket("extreme"), "unknown")

    def test_non_string_returns_unknown(self):
        self.assertEqual(compute_confidence_bucket(42), "unknown")
        self.assertEqual(compute_confidence_bucket(["high"]), "unknown")


class ComputeDirectionCorrectTests(unittest.TestCase):
    """direction_correct checks YES/NO recommendation vs settled outcome.

    Returns None for WAIT/AVOID/empty (non-directional) or unresolved
    (actual_outcome is None).
    """

    def test_yes_recommendation_yes_outcome_correct(self):
        self.assertTrue(compute_direction_correct("YES", 100.0))
        self.assertTrue(compute_direction_correct("YES", 60.0))

    def test_yes_recommendation_no_outcome_incorrect(self):
        self.assertFalse(compute_direction_correct("YES", 0.0))
        self.assertFalse(compute_direction_correct("YES", 30.0))

    def test_no_recommendation_no_outcome_correct(self):
        self.assertTrue(compute_direction_correct("NO", 0.0))
        self.assertTrue(compute_direction_correct("NO", 30.0))

    def test_no_recommendation_yes_outcome_incorrect(self):
        self.assertFalse(compute_direction_correct("NO", 100.0))
        self.assertFalse(compute_direction_correct("NO", 70.0))

    def test_boundary_50_treated_as_yes(self):
        """Outcomes >= 50.0 are YES per the threshold convention."""
        self.assertTrue(compute_direction_correct("YES", 50.0))
        self.assertFalse(compute_direction_correct("NO", 50.0))

    def test_wait_returns_none(self):
        """WAIT is non-directional — no direction to check."""
        self.assertIsNone(compute_direction_correct("WAIT", 100.0))

    def test_avoid_returns_none(self):
        """AVOID is non-directional — no direction to check."""
        self.assertIsNone(compute_direction_correct("AVOID", 0.0))

    def test_empty_string_returns_none(self):
        self.assertIsNone(compute_direction_correct("", 100.0))

    def test_none_recommendation_returns_none(self):
        self.assertIsNone(compute_direction_correct(None, 100.0))

    def test_none_outcome_returns_none(self):
        """Unresolved prediction (no outcome yet) — direction_correct is None."""
        self.assertIsNone(compute_direction_correct("YES", None))

    def test_case_insensitive(self):
        self.assertTrue(compute_direction_correct("yes", 100.0))
        self.assertTrue(compute_direction_correct("no", 0.0))

    def test_non_numeric_outcome_returns_none(self):
        self.assertIsNone(compute_direction_correct("YES", "not-a-number"))
        self.assertIsNone(compute_direction_correct("YES", float("nan")))
        self.assertIsNone(compute_direction_correct("YES", float("inf")))

    def test_non_string_recommendation_returns_none(self):
        self.assertIsNone(compute_direction_correct(42, 100.0))
        self.assertIsNone(compute_direction_correct(["YES"], 100.0))


class BuildPredictionSnapshotTests(unittest.TestCase):
    """build_prediction_snapshot extracts context fields from an event record.

    Best-effort: never raises on missing/malformed fields. Returns defaults
    (empty string / None) when fields are absent.
    """

    def test_full_record_extracts_all_fields(self):
        record = {
            "event_title": "Will CPI be under 3%?",
            "source": {"type": "prediction_market", "platform": "Polymarket"},
            "actionable_recommendation": {
                "direction": "YES",
                "confidence": "high",
            },
            "evidence": {"strength": 0.72, "conflict": 0.18},
            "market_quality": {"score": 0.81},
        }
        snapshot = build_prediction_snapshot(record)
        self.assertEqual(snapshot["snapshot_question"], "Will CPI be under 3%?")
        self.assertEqual(snapshot["snapshot_recommendation"], "YES")
        self.assertEqual(snapshot["snapshot_confidence"], "high")
        self.assertAlmostEqual(snapshot["snapshot_evidence_strength"], 0.72)
        self.assertAlmostEqual(snapshot["snapshot_conflict_score"], 0.18)
        self.assertAlmostEqual(snapshot["snapshot_market_quality_score"], 0.81)
        self.assertEqual(snapshot["snapshot_source_platform"], "Polymarket")

    def test_missing_actionable_recommendation_yields_empty(self):
        record = {
            "event_title": "Question?",
            "source": {"platform": "Kalshi"},
        }
        snapshot = build_prediction_snapshot(record)
        self.assertEqual(snapshot["snapshot_recommendation"], "")
        self.assertEqual(snapshot["snapshot_confidence"], "")

    def test_missing_evidence_yields_none(self):
        record = {"event_title": "Q?", "source": {"platform": "P"}}
        snapshot = build_prediction_snapshot(record)
        self.assertIsNone(snapshot["snapshot_evidence_strength"])
        self.assertIsNone(snapshot["snapshot_conflict_score"])

    def test_missing_market_quality_yields_none(self):
        """When Phase 2 is off or source is non-prediction-market,
        market_quality is absent — snapshot_market_quality_score is None."""
        record = {"event_title": "Q?", "source": {"platform": "P"}}
        snapshot = build_prediction_snapshot(record)
        self.assertIsNone(snapshot["snapshot_market_quality_score"])

    def test_none_record_returns_empty_snapshot(self):
        snapshot = build_prediction_snapshot(None)
        self.assertEqual(snapshot["snapshot_question"], "")
        self.assertEqual(snapshot["snapshot_recommendation"], "")
        self.assertIsNone(snapshot["snapshot_evidence_strength"])

    def test_non_dict_record_returns_empty_snapshot(self):
        snapshot = build_prediction_snapshot("not-a-dict")
        self.assertEqual(snapshot["snapshot_question"], "")

    def test_non_dict_source_yields_empty_platform(self):
        record = {"event_title": "Q?", "source": "bad"}
        snapshot = build_prediction_snapshot(record)
        self.assertEqual(snapshot["snapshot_source_platform"], "")

    def test_non_dict_actionable_recommendation_yields_empty(self):
        record = {"actionable_recommendation": "bad"}
        snapshot = build_prediction_snapshot(record)
        self.assertEqual(snapshot["snapshot_recommendation"], "")

    def test_non_dict_evidence_yields_none(self):
        record = {"evidence": "bad"}
        snapshot = build_prediction_snapshot(record)
        self.assertIsNone(snapshot["snapshot_evidence_strength"])

    def test_non_dict_market_quality_yields_none(self):
        record = {"market_quality": "bad"}
        snapshot = build_prediction_snapshot(record)
        self.assertIsNone(snapshot["snapshot_market_quality_score"])

    def test_non_numeric_evidence_strength_yields_none(self):
        record = {"evidence": {"strength": "not-a-number"}}
        snapshot = build_prediction_snapshot(record)
        self.assertIsNone(snapshot["snapshot_evidence_strength"])

    def test_nan_evidence_strength_yields_none(self):
        record = {"evidence": {"strength": float("nan")}}
        snapshot = build_prediction_snapshot(record)
        self.assertIsNone(snapshot["snapshot_evidence_strength"])

    def test_never_raises_on_adversarial_input(self):
        """Adversarial / deeply malformed input must not raise."""
        snapshot = build_prediction_snapshot({
            "event_title": None,
            "source": 42,
            "actionable_recommendation": [],
            "evidence": "bad",
            "market_quality": object(),
        })
        # Should produce a valid (empty) snapshot, not raise
        self.assertIn("snapshot_question", snapshot)


class BuildResolutionBucketsTests(unittest.TestCase):
    """build_resolution_buckets combines direction_correct + edge_bucket +
    confidence_bucket at resolve time."""

    def test_full_resolution_yes_correct(self):
        buckets = build_resolution_buckets(
            snapshot_recommendation="YES",
            snapshot_confidence="high",
            raw_edge=15.0,
            actual_outcome=100.0,
        )
        self.assertTrue(buckets["direction_correct"])
        self.assertEqual(buckets["edge_bucket"], "10-20")
        self.assertEqual(buckets["confidence_bucket"], "high")

    def test_full_resolution_no_correct(self):
        buckets = build_resolution_buckets(
            snapshot_recommendation="NO",
            snapshot_confidence="medium",
            raw_edge=-8.0,
            actual_outcome=20.0,
        )
        self.assertTrue(buckets["direction_correct"])
        self.assertEqual(buckets["edge_bucket"], "5-10")
        self.assertEqual(buckets["confidence_bucket"], "medium")

    def test_yes_incorrect_when_outcome_no(self):
        buckets = build_resolution_buckets(
            snapshot_recommendation="YES",
            snapshot_confidence="high",
            raw_edge=15.0,
            actual_outcome=0.0,
        )
        self.assertFalse(buckets["direction_correct"])

    def test_wait_recommendation_direction_none(self):
        buckets = build_resolution_buckets(
            snapshot_recommendation="WAIT",
            snapshot_confidence="low",
            raw_edge=3.0,
            actual_outcome=100.0,
        )
        self.assertIsNone(buckets["direction_correct"])
        self.assertEqual(buckets["edge_bucket"], "0-5")
        self.assertEqual(buckets["confidence_bucket"], "low")

    def test_empty_recommendation_direction_none(self):
        """Pre-Phase-3 predictions (or flag-off freeze) have empty
        snapshot_recommendation — direction_correct is None."""
        buckets = build_resolution_buckets(
            snapshot_recommendation="",
            snapshot_confidence="",
            raw_edge=12.0,
            actual_outcome=80.0,
        )
        self.assertIsNone(buckets["direction_correct"])
        self.assertEqual(buckets["edge_bucket"], "10-20")
        self.assertEqual(buckets["confidence_bucket"], "unknown")

    def test_unresolved_outcome_direction_none(self):
        """When called before resolution (actual_outcome=None),
        direction_correct is None but buckets still compute."""
        buckets = build_resolution_buckets(
            snapshot_recommendation="YES",
            snapshot_confidence="high",
            raw_edge=15.0,
            actual_outcome=None,
        )
        self.assertIsNone(buckets["direction_correct"])
        self.assertEqual(buckets["edge_bucket"], "10-20")
        self.assertEqual(buckets["confidence_bucket"], "high")


if __name__ == "__main__":
    unittest.main()
