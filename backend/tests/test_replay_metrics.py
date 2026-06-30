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
            original={},  # no final_displayed_direction, no actionable_recommendation
            replayed={"final_displayed_direction": "WAIT"},
        )
        d = m.to_dict()
        self.assertEqual(d["total"], 0)

    def test_falls_back_to_actionable_recommendation_direction(self):
        """P1-2 regression: when final_displayed_direction is absent (all_off
        baseline), fall back to actionable_recommendation.direction so the
        default A/B comparison reports total>0 and the matrix reads
        raw->with_overlays (YES->WAIT = overlays downgraded YES to WAIT)."""
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={
                # all_off baseline: no final_displayed_direction, but raw rec direction is YES
                "actionable_recommendation": {"direction": "YES"},
            },
            replayed={
                # current config: overlays downgraded to WAIT
                "final_displayed_direction": "WAIT",
                "actionable_recommendation": {"direction": "YES"},
            },
        )
        d = m.to_dict()
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["direction_matrix"]["YES->WAIT"], 1)


class TestBrierAndDirectionCorrect(unittest.TestCase):
    def test_brier_frozen_and_direction_correct_delta(self):
        """Brier is frozen (single mean, no delta); direction_correct_delta
        is the real improvement signal. original=YES/outcome=100 -> correct;
        replayed=NO/outcome=100 -> incorrect. Delta = 0/1 - 1/1 = -1.0."""
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={
                "final_displayed_direction": "YES",
                "brier_score": 0.25,
                "actual_outcome": 100.0,
                "direction_correct": 1,
            },
            replayed={
                "final_displayed_direction": "NO",
                "brier_score": 0.25,  # frozen — same as original
                "actual_outcome": 100.0,
            },
        )
        d = m.to_dict()
        self.assertEqual(d["resolved_count"], 1)
        # Single frozen Brier mean (no original/replayed split, no delta).
        self.assertAlmostEqual(d["brier_mean"], 0.25)
        self.assertNotIn("brier_original_mean", d)
        self.assertNotIn("brier_replayed_mean", d)
        self.assertNotIn("brier_delta", d)
        self.assertTrue(d["brier_frozen"])
        # direction_correct: original 1/1=100%, replayed 0/1=0% -> delta -1.0
        self.assertEqual(d["direction_correct_original"], 1)
        self.assertEqual(d["direction_correct_replayed"], 0)
        self.assertEqual(d["direction_correct_resolved_count"], 1)
        self.assertAlmostEqual(d["direction_correct_delta"], -1.0)

    def test_direction_correct_ignores_frozen_field_uses_replay_dir(self):
        """P1 regression: orig_dc must be re-derived from orig_dir + actual,
        NOT read from the frozen ``direction_correct`` field. The frozen
        field reflects the freeze-time snapshot direction and would pair
        incorrectly with the A/B replay's left-side direction. Here the
        frozen field says 0 (incorrect) but orig_dir=YES + outcome=100
        means the replayed YES is actually correct — the frozen value
        must be ignored."""
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={
                "final_displayed_direction": "YES",
                "brier_score": 0.25,
                "actual_outcome": 100.0,
                "direction_correct": 0,  # frozen field says WRONG — must be ignored
            },
            replayed={
                "final_displayed_direction": "YES",
                "brier_score": 0.25,
                "actual_outcome": 100.0,
            },
        )
        d = m.to_dict()
        # orig_dc re-derived from YES+100=True, not from frozen field=0.
        self.assertEqual(d["direction_correct_original"], 1)
        self.assertEqual(d["direction_correct_replayed"], 1)
        self.assertEqual(d["direction_correct_resolved_count"], 1)
        # delta = 1/1 - 1/1 = 0.0 (both sides correct)
        self.assertAlmostEqual(d["direction_correct_delta"], 0.0)

    def test_abstention_excluded_from_direction_correct_delta(self):
        """P1 regression: WAIT/AVOID abstentions return None from
        _derive_direction_correct and are excluded from delta. Both sides
        abstaining (WAIT->WAIT) must NOT produce delta=-1.0 (the old
        bug read frozen direction_correct=1 and treated replay WAIT as 0).
        direction_correct_delta should be None (no eligible samples)."""
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={
                "final_displayed_direction": "WAIT",
                "brier_score": 0.25,
                "actual_outcome": 100.0,
                "direction_correct": 1,  # frozen — would have caused -1.0 under old code
            },
            replayed={
                "final_displayed_direction": "WAIT",
                "brier_score": 0.25,
                "actual_outcome": 100.0,
            },
        )
        d = m.to_dict()
        # Both sides abstain -> no eligible samples for delta.
        self.assertEqual(d["direction_correct_resolved_count"], 0)
        self.assertIsNone(d["direction_correct_delta"])
        # Brier still counted (frozen reference).
        self.assertEqual(d["resolved_count"], 1)

    def test_one_side_abstention_excluded_from_delta(self):
        """P1 regression: when one side has YES/NO and the other abstains
        (WAIT/AVOID), the pair is excluded from direction_correct_delta
        (asymmetric — cannot compare a prediction to an abstention).
        The YES->WAIT signal still flows through direction_matrix."""
        from app.replay.metrics import ReplayMetrics
        m = ReplayMetrics()
        m.add_pair(
            original={
                "final_displayed_direction": "YES",
                "brier_score": 0.25,
                "actual_outcome": 100.0,
                "direction_correct": 1,
            },
            replayed={
                "final_displayed_direction": "WAIT",  # abstention
                "brier_score": 0.25,
                "actual_outcome": 100.0,
            },
        )
        d = m.to_dict()
        # One side abstains -> not eligible for delta.
        self.assertEqual(d["direction_correct_resolved_count"], 0)
        self.assertIsNone(d["direction_correct_delta"])
        # But direction_matrix still records the YES->WAIT change.
        self.assertEqual(d["direction_matrix"]["YES->WAIT"], 1)

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
