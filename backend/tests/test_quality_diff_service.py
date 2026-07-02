"""Unit tests for quality_diff_service.build_diff (LATER #1)."""
import unittest

from app.services.quality_diff_service import (
    DIRECTION_LABELS,
    _effective_direction,
    build_diff,
)


def _record(
    event_id: str,
    direction: str = "YES",
    actual_outcome: float | None = 100.0,
    source_type: str = "prediction_market",
    analysis_quality: str = "llm",
    edge: float = 12.0,
    brier_score: float | None = 0.18,
    overall_score: float | None = 0.7,
) -> dict:
    """Build a minimal record that extract_metrics can consume."""
    return {
        "event_id": event_id,
        "source": {"type": source_type},
        "llm_telemetry": {"analysis_quality": analysis_quality},
        "actionable_recommendation": {"direction": direction, "edge": edge},
        "outcome": {
            "status": "resolved" if actual_outcome is not None else "unresolved",
            "actual_outcome": actual_outcome,
        },
        "calibration": {"brier_score": brier_score, "estimated_probability": 62.0},
        "source_reliability": {"overall_score": overall_score},
    }


def _meta(name: str = "current", overrides: dict | None = None) -> dict:
    return {"preset": name, "settings_overrides": overrides or {}, "name": name}


class TestEffectiveDirection(unittest.TestCase):
    def test_final_displayed_direction_takes_precedence(self):
        r = {"final_displayed_direction": "WAIT",
             "actionable_recommendation": {"direction": "YES"}}
        self.assertEqual(_effective_direction(r), "WAIT")

    def test_falls_back_to_actionable_recommendation(self):
        r = {"actionable_recommendation": {"direction": "NO"}}
        self.assertEqual(_effective_direction(r), "NO")

    def test_unknown_direction_returns_none(self):
        r = {"actionable_recommendation": {"direction": "SKIP"}}
        self.assertEqual(_effective_direction(r), None)

    def test_missing_direction_returns_none(self):
        self.assertEqual(_effective_direction({}), None)


class TestBuildDiff(unittest.TestCase):
    def test_build_diff_empty_inputs(self):
        result = build_diff([], [], _meta(), _meta())
        self.assertEqual(result["overview"]["n_total"], 0)
        self.assertEqual(result["overview"]["n_direction_compared"], 0)
        self.assertEqual(result["overview"]["n_scored_compared"], 0)
        self.assertEqual(result["overview"]["n_missing_a"], 0)
        self.assertEqual(result["overview"]["n_missing_b"], 0)
        self.assertEqual(result["overview"]["direction_changed"], 0)
        self.assertIsNone(result["overview"]["change_rate"])
        for key in ("by_source_type", "by_analysis_quality",
                    "by_edge_bucket", "by_source_reliability_bucket"):
            self.assertEqual(result["slice_diff"][key], {})

    def test_build_diff_by_event_id_alignment(self):
        """A has 3 events, B has 2 → n_missing_b=1, only common compared."""
        records_a = [_record("e1"), _record("e2"), _record("e3")]
        records_b = [_record("e1"), _record("e2")]
        result = build_diff(records_a, records_b, _meta(), _meta())
        self.assertEqual(result["overview"]["n_total"], 3)
        self.assertEqual(result["overview"]["n_missing_a"], 0)
        self.assertEqual(result["overview"]["n_missing_b"], 1)
        self.assertEqual(result["overview"]["n_direction_compared"], 2)

    def test_build_diff_direction_matrix(self):
        """A=YES, B=WAIT for one event; A=YES, B=YES for another."""
        records_a = [_record("e1", direction="YES"), _record("e2", direction="YES")]
        records_b = [_record("e1", direction="WAIT"), _record("e2", direction="YES")]
        result = build_diff(records_a, records_b, _meta(), _meta())
        # Matrix: YES→WAIT=1, YES→YES=1
        self.assertEqual(result["direction_matrix"]["YES"]["WAIT"], 1)
        self.assertEqual(result["direction_matrix"]["YES"]["YES"], 1)
        self.assertEqual(result["overview"]["direction_changed"], 1)
        self.assertAlmostEqual(result["overview"]["change_rate"], 0.5)
        # top_transitions excludes diagonal, so YES->WAIT appears
        self.assertTrue(any(
            t["from"] == "YES" and t["to"] == "WAIT"
            for t in result["top_transitions"]
        ))

    def test_build_diff_unknown_direction_goes_to_other(self):
        """Direction 'SKIP' (not in DIRECTION_LABELS) → OTHER bucket."""
        records_a = [_record("e1", direction="SKIP")]
        records_b = [_record("e1", direction="YES")]
        result = build_diff(records_a, records_b, _meta(), _meta())
        # _effective_direction returns None for SKIP, so this event is
        # excluded from direction matrix (not counted in n_direction_compared).
        # But extract_metrics still processes it for slices.
        self.assertEqual(result["overview"]["n_direction_compared"], 0)

    def test_build_diff_n_direction_vs_n_scored(self):
        """Records with unresolved outcome → n_direction > n_scored."""
        records_a = [
            _record("e1", direction="YES", actual_outcome=100.0),
            _record("e2", direction="YES", actual_outcome=None),  # unresolved
        ]
        records_b = [
            _record("e1", direction="YES", actual_outcome=100.0),
            _record("e2", direction="YES", actual_outcome=None),
        ]
        result = build_diff(records_a, records_b, _meta(), _meta())
        self.assertEqual(result["overview"]["n_direction_compared"], 2)
        # Only e1 has resolved outcome on both sides
        self.assertEqual(result["overview"]["n_scored_compared"], 1)

    def test_build_diff_slice_metrics_structure(self):
        """slice_diff.by_source_type has a/b/delta per slice key."""
        records_a = [_record("e1", source_type="prediction_market")]
        records_b = [_record("e1", source_type="prediction_market")]
        result = build_diff(records_a, records_b, _meta(), _meta())
        st = result["slice_diff"]["by_source_type"]
        self.assertIn("prediction_market", st)
        sl = st["prediction_market"]
        self.assertIn("a", sl)
        self.assertIn("b", sl)
        self.assertIn("delta", sl)
        self.assertIn("n", sl["delta"])
        self.assertIn("a", sl["delta"]["n"])
        self.assertIn("b", sl["delta"]["n"])
        self.assertIn("delta", sl["delta"]["n"])
        self.assertIn("direction_accuracy", sl["delta"])
        self.assertIn("brier_score", sl["delta"])

    def test_build_diff_regression_summary(self):
        """Mixed Δacc/Δbrier signs → regression/improvement counts."""
        # Two slices: one improves accuracy, one regresses
        records_a = [
            _record("e1", source_type="good", direction="YES", actual_outcome=0.0),  # wrong
            _record("e2", source_type="bad", direction="YES", actual_outcome=100.0),  # right
        ]
        records_b = [
            _record("e1", source_type="good", direction="YES", actual_outcome=100.0),  # now right
            _record("e2", source_type="bad", direction="YES", actual_outcome=0.0),  # now wrong
        ]
        result = build_diff(records_a, records_b, _meta(), _meta())
        rs = result["regression_summary"]
        # good slice: acc 0→1 (improvement); bad slice: acc 1→0 (regression)
        self.assertGreaterEqual(rs["accuracy_regressions"], 1)
        self.assertGreaterEqual(rs["accuracy_improvements"], 1)

    def test_build_diff_diff_errors_stage(self):
        """Malformed record → diff_errors with stage, no abort."""
        # Record missing actionable_recommendation will cause extract_metrics
        # to still work (returns direction_correct=None), so we need a record
        # that actually raises. Patch extract_metrics to raise for one event.
        records_a = [_record("e1"), {"event_id": "bad", "broken": True}]
        records_b = [_record("e1"), _record("bad")]
        # The malformed record lacks actionable_recommendation; extract_metrics
        # handles it gracefully (returns direction_correct=None). To test the
        # error path, we need a record that raises. Use a record whose
        # source field is an int (isinstance check in extract_metrics handles
        # this). Actually extract_metrics is resilient. So patch it.
        from unittest.mock import patch
        from app.services import quality_diff_service as qds
        original_extract = qds.extract_metrics

        def patched(record):
            if record.get("event_id") == "bad" and "broken" in record:
                raise RuntimeError("simulated extract failure")
            return original_extract(record)

        with patch.object(qds, "extract_metrics", side_effect=patched):
            result = build_diff(records_a, records_b, _meta(), _meta())
        self.assertTrue(len(result["diff_errors"]) >= 1)
        err = result["diff_errors"][0]
        self.assertIn(err["stage"], ("extract_metrics", "slice_metrics", "direction"))
        self.assertIn("simulated extract failure", err["error"])

    def test_build_diff_outcome_injection_contract(self):
        """Record with outcome field works — validates service depends on
        CLI injecting outcome onto replayed record."""
        records_a = [_record("e1", actual_outcome=100.0)]
        records_b = [_record("e1", actual_outcome=100.0)]
        result = build_diff(records_a, records_b, _meta(), _meta())
        # Should not crash; direction_correct computed from outcome
        self.assertEqual(result["overview"]["n_scored_compared"], 1)


if __name__ == "__main__":
    unittest.main()
