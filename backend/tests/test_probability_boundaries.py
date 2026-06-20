"""
Probability change boundary tests (Phase 4 item 6).

Three boundaries that govern how much a probability is allowed to move and what
that move is labelled:

  - clamp_probability deviation caps: the maximum distance the constrained
    probability may sit from the market baseline tightens with confidence
    (35 points by default, 20 below 0.65, 12 below 0.50), and weak / low-trust
    inputs regress the estimate back toward the market.
  - calculate_signal thresholds: the divergence + confidence cut-offs between
    STRONG_*, LONG/SHORT and WATCHLIST, and the quality gate that forces
    WATCHLIST.
  - probability_direction: the +-2 point rising / stable / falling boundary.

Exact clamp values were captured from the implementation; they lock the caps.
"""

import unittest

from app.services.analysis_report_service import calculate_signal
from app.services.probability_engine_service import (
    clamp_probability,
    default_evidence_profile,
)
from app.services.scoring_service import probability_direction

STRONG_EVIDENCE = {
    "evidence_direction": "support",
    "evidence_strength": 0.6,
    "conflict_score": 0.1,
    "freshness_score": 0.9,
    "resolution_relevance_score": 0.6,
    "source_count": 5,
}
LOW_AMBIGUITY = {"condition_type": "threshold", "ambiguity_score": 20}

# Passes passes_analysis_quality_gate when paired with news_quality >= 0.40,
# confidence >= 0.50 and priced_in <= 80.
GATE_EVIDENCE = {
    "evidence_direction": "support",
    "evidence_strength": 0.5,
    "conflict_score": 0.2,
    "freshness_score": 0.8,
    "resolution_relevance_score": 0.5,
    "source_count": 4,
}


class ClampProbabilityBoundaryTests(unittest.TestCase):
    def _clamp_strong(self, confidence):
        # market 10, ai 99 -> raw deviation 89, far beyond any cap.
        return clamp_probability(
            market_probability=10,
            ai_probability=99,
            confidence=confidence,
            narrative_type="factual",
            has_strong_evidence=True,
            evidence_profile=STRONG_EVIDENCE,
            priced_in_risk_score=10,
            semantics_profile=LOW_AMBIGUITY,
        )

    def test_high_confidence_caps_deviation_near_35(self):
        # confidence 0.80 -> 35 point cap, lightly regressed.
        self.assertEqual(self._clamp_strong(0.80), 43.25)

    def test_mid_confidence_caps_deviation_near_20(self):
        # confidence 0.60 -> 20 point cap.
        self.assertEqual(self._clamp_strong(0.60), 28.66)

    def test_low_confidence_caps_deviation_near_12(self):
        # confidence 0.45 -> 12 point cap.
        self.assertEqual(self._clamp_strong(0.45), 20.87)

    def test_weak_low_trust_input_regresses_toward_market(self):
        result = clamp_probability(
            market_probability=50,
            ai_probability=90,
            confidence=0.3,
            narrative_type="meme",
            has_strong_evidence=False,
            evidence_profile=default_evidence_profile(),
            priced_in_risk_score=70,
            semantics_profile={"condition_type": "unknown", "ambiguity_score": 70},
        )
        self.assertEqual(result, 57.57)
        # Far closer to the market baseline (50) than to the AI estimate (90).
        self.assertLess(result, 65.0)

    def test_downward_move_is_also_capped(self):
        # Mirror of the high-confidence case: market 90, ai 1 -> capped downward.
        result = clamp_probability(
            market_probability=90,
            ai_probability=1,
            confidence=0.80,
            narrative_type="factual",
            has_strong_evidence=True,
            evidence_profile=STRONG_EVIDENCE,
            priced_in_risk_score=10,
            semantics_profile=LOW_AMBIGUITY,
        )
        self.assertLess(result, 90.0)
        self.assertGreaterEqual(result, 55.0)  # cannot fall more than ~35 points


class SignalBoundaryTests(unittest.TestCase):
    def _signal(self, divergence, confidence, news_quality=0.6):
        return calculate_signal(
            divergence=divergence,
            confidence=confidence,
            evidence_profile=GATE_EVIDENCE,
            priced_in_risk_score=20,
            news_quality_score=news_quality,
        )

    def test_strong_long_requires_divergence_over_20(self):
        self.assertEqual(self._signal(21, 0.69), "STRONG_LONG")
        self.assertEqual(self._signal(20, 0.69), "LONG")  # 20 is not > 20

    def test_strong_short_requires_divergence_under_minus_20(self):
        self.assertEqual(self._signal(-21, 0.69), "STRONG_SHORT")
        self.assertEqual(self._signal(-20, 0.69), "SHORT")

    def test_long_lower_threshold(self):
        self.assertEqual(self._signal(11, 0.51), "LONG")
        self.assertEqual(self._signal(10, 0.51), "WATCHLIST")  # 10 is not > 10

    def test_strong_needs_confidence_over_0_68(self):
        # Big divergence but confidence exactly 0.68 (not > 0.68) -> only LONG.
        self.assertEqual(self._signal(25, 0.68), "LONG")

    def test_quality_gate_forces_watchlist(self):
        # Confidence below the 0.50 gate.
        self.assertEqual(self._signal(30, 0.49), "WATCHLIST")
        # News quality below the 0.40 gate.
        self.assertEqual(self._signal(30, 0.80, news_quality=0.39), "WATCHLIST")


class ProbabilityDirectionBoundaryTests(unittest.TestCase):
    def test_rising_at_plus_two(self):
        self.assertEqual(probability_direction(2), "rising")
        self.assertEqual(probability_direction(1.99), "stable")

    def test_falling_at_minus_two(self):
        self.assertEqual(probability_direction(-2), "falling")
        self.assertEqual(probability_direction(-1.99), "stable")

    def test_zero_is_stable(self):
        self.assertEqual(probability_direction(0), "stable")


if __name__ == "__main__":
    unittest.main()
