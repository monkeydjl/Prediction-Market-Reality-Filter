"""Unit tests for ReplayMetrics accumulator."""
import unittest


class TestDirectionMatrix(unittest.TestCase):
    def test_accumulates_yes_to_wait(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={"final_displayed_direction": "YES"},
            replayed={"final_displayed_direction": "WAIT"},
        )
        m.add_pair(
            original={"final_displayed_direction": "YES"},
            replayed={"final_displayed_direction": "WAIT"},
        )
        m.add_pair(
            original={"final_displayed_direction": "NO"},
            replayed={"final_displayed_direction": "AVOID"},
        )
        d = m.to_dict()
        # to_dict() serializes direction_matrix to JSON-friendly string keys
        # ("YES->WAIT" format, per module docstring). The original brief used
        # tuple keys here, which contradicts the to_dict() contract — fixed
        # by controller adjudication (Option A: test is the spec bug).
        self.assertEqual(d["direction_matrix"]["YES->WAIT"], 2)
        self.assertEqual(d["direction_matrix"]["NO->AVOID"], 1)
        self.assertEqual(d["total"], 3)

    def test_missing_direction_skipped(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={},  # no final_displayed_direction
            replayed={"final_displayed_direction": "WAIT"},
        )
        d = m.to_dict()
        self.assertEqual(d["total"], 0)


class TestBrierAndDirectionCorrect(unittest.TestCase):
    def test_brier_delta_signed(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        # original brier 0.25, replayed brier 0.15 — replayed is better.
        m.add_pair(
            original={
                "final_displayed_direction": "YES",
                "brier_score": 0.25,
                "actual_outcome": 100.0,
                "direction_correct": 1,
            },
            replayed={
                "final_displayed_direction": "NO",
                "brier_score": 0.15,
                "actual_outcome": 100.0,
            },
        )
        d = m.to_dict()
        self.assertEqual(d["resolved_count"], 1)
        self.assertAlmostEqual(d["brier_original_mean"], 0.25)
        self.assertAlmostEqual(d["brier_replayed_mean"], 0.15)
        self.assertAlmostEqual(d["brier_delta"], -0.10)  # negative = improved
        # original direction_correct: YES vs outcome 100 -> correct (1)
        self.assertEqual(d["direction_correct_original"], 1)
        # replayed direction_correct: NO vs outcome 100 -> incorrect (0)
        self.assertEqual(d["direction_correct_replayed"], 0)

    def test_llm_vs_fallback_split(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={
                "final_displayed_direction": "YES",
                "brier_score": 0.2,
                "actual_outcome": 100.0,
                "direction_correct": 1,
            },
            replayed={
                "final_displayed_direction": "YES",
                "brier_score": 0.2,
                "actual_outcome": 100.0,
                "llm_telemetry": {"analysis_quality": "llm"},
            },
        )
        m.add_pair(
            original={
                "final_displayed_direction": "YES",
                "brier_score": 0.4,
                "actual_outcome": 0.0,
                "direction_correct": 0,
            },
            replayed={
                "final_displayed_direction": "YES",
                "brier_score": 0.4,
                "actual_outcome": 0.0,
                "llm_telemetry": {"analysis_quality": "deterministic_fallback"},
            },
        )
        d = m.to_dict()
        self.assertIn("llm", d["brier_by_quality"])
        self.assertIn("deterministic_fallback", d["brier_by_quality"])
        self.assertEqual(d["brier_by_quality"]["llm"]["n"], 1)
        self.assertEqual(d["brier_by_quality"]["deterministic_fallback"]["n"], 1)


class TestPhaseContributions(unittest.TestCase):
    def test_downgrades_caused_counted(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        # base=YES, phase_only=WAIT — this phase downgraded.
        m.add_phase_result("e1", "decision_quality", "YES", "WAIT", "WAIT")
        # base=YES, phase_only=YES — this phase didn't downgrade.
        m.add_phase_result("e2", "market_quality", "YES", "YES", "YES")
        d = m.to_dict()
        self.assertEqual(
            d["phase_contributions"]["decision_quality"]["downgrades_caused"], 1
        )
        self.assertEqual(
            d["phase_contributions"]["market_quality"]["downgrades_caused"], 0
        )

    def test_conflict_case_collected_when_phase_overridden(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        # phase says YES, final says WAIT — phase was overridden by another.
        m.add_phase_result("e1", "source_reliability", "YES", "YES", "WAIT")
        d = m.to_dict()
        self.assertEqual(d["conflict_cases_total"], 1)
        self.assertEqual(d["conflict_cases"][0]["phase"], "source_reliability")
        self.assertEqual(d["conflict_cases"][0]["phase_dir"], "YES")
        self.assertEqual(d["conflict_cases"][0]["final_dir"], "WAIT")

    def test_no_conflict_when_phase_agrees_with_final(self):
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_phase_result("e1", "decision_quality", "YES", "YES", "YES")
        d = m.to_dict()
        self.assertEqual(d["conflict_cases_total"], 0)


if __name__ == "__main__":
    unittest.main()
