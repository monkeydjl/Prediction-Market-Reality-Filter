"""Unit tests for review_queue_detectors (Plan 4 §6.2)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.services.review_queue_detectors import (
    detect_auto_resolve_low_confidence,
    detect_review_candidates,
)


def _base_record(**overrides):
    """Minimal record that triggers no detectors by default."""
    rec = {
        "event_id": "evt-001",
        "actionable_recommendation": {"direction": "YES", "signal": "act",
                                       "ai_probability": 0.65},
        "final_displayed_direction": "YES",
        "final_downgrade_reason": None,
        "source_reliability": None,
        "market_quality": None,
        "outcome": None,
    }
    rec.update(overrides)
    return rec


class TestReviewQueueDetectors(unittest.TestCase):
    def test_no_candidates_for_clean_record(self):
        candidates = detect_review_candidates(_base_record())
        self.assertEqual(candidates, [])

    def test_high_value_downgraded_when_act_becomes_wait(self):
        rec = _base_record(
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.72},
            final_displayed_direction="WAIT",
            final_downgrade_reason="guardrail fired",
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "high_value_downgraded")
        self.assertEqual(candidates[0]["severity"], "WARN")

    def test_high_value_downgraded_skips_watchlist_signal(self):
        """WATCHLIST signal should not trigger even if direction is WAIT."""
        rec = _base_record(
            actionable_recommendation={"direction": "YES", "signal": "WATCHLIST",
                                        "ai_probability": 0.55},
            final_displayed_direction="WAIT",
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(candidates, [])

    def test_source_market_conflict(self):
        rec = _base_record(
            source_reliability={
                "suggested_direction": "WAIT",
                "downgraded": True,
                "downgrade_reason": "来源可靠性不足",
            },
            market_quality={
                "suggested_direction": "YES",
                "downgraded": False,
            },
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "source_market_conflict")

    def test_outcome_prediction_mismatch(self):
        rec = _base_record(
            outcome="NO",
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.82},
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "outcome_prediction_mismatch")
        self.assertEqual(candidates[0]["severity"], "ERROR")

    def test_outcome_mismatch_skips_low_confidence(self):
        rec = _base_record(
            outcome="NO",
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.60},
        )
        candidates = detect_review_candidates(rec)
        self.assertEqual(candidates, [])

    def test_multiple_detectors_can_fire(self):
        rec = _base_record(
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.80},
            final_displayed_direction="WAIT",
            final_downgrade_reason="guardrail",
            outcome="NO",
        )
        candidates = detect_review_candidates(rec)
        triggers = [c["trigger"] for c in candidates]
        self.assertIn("high_value_downgraded", triggers)
        self.assertIn("outcome_prediction_mismatch", triggers)

    def test_reasons_exclude_banned_terms(self):
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        rec = _base_record(
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.80},
            final_displayed_direction="WAIT",
            final_downgrade_reason="guardrail",
        )
        candidates = detect_review_candidates(rec)
        for c in candidates:
            for term in banned:
                self.assertNotIn(term, c["reason"].lower(),
                                 f"banned term '{term}' in reason: {c['reason']}")

    def test_handles_missing_fields_gracefully(self):
        """Detectors must not crash on records missing optional fields."""
        candidates = detect_review_candidates({"event_id": "x"})
        self.assertEqual(candidates, [])

    def test_mismatch_confidence_threshold_param_controls_firing(self):
        """``mismatch_confidence_threshold`` parameter overrides the default
        0.75 — a 0.70-confidence prediction that contradicts the outcome
        fires only when the threshold is lowered to 0.70.
        """
        rec = _base_record(
            outcome="NO",
            actionable_recommendation={"direction": "YES", "signal": "act",
                                        "ai_probability": 0.70},
        )
        # Default threshold 0.75 → 0.70 < 0.75 → no fire.
        self.assertEqual(detect_review_candidates(rec), [])
        # Lowered threshold 0.70 → 0.70 >= 0.70 → fire.
        candidates = detect_review_candidates(rec, mismatch_confidence_threshold=0.70)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "outcome_prediction_mismatch")

    def test_auto_resolve_low_confidence_fires_for_auto_market_below_threshold(self):
        rec = _base_record(
            outcome={
                "status": "resolved",
                "source": "auto_market",
                "confidence": 0.92,
                "actual_outcome": 100.0,
            },
        )
        candidates = detect_auto_resolve_low_confidence(
            rec, confidence_threshold=0.95,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "auto_resolve_low_confidence")
        self.assertEqual(candidates[0]["severity"], "WARN")
        self.assertEqual(candidates[0]["context"]["outcome_confidence"], 0.92)
        self.assertEqual(candidates[0]["context"]["confidence_threshold"], 0.95)

    def test_auto_resolve_low_confidence_is_strictly_below_threshold(self):
        rec = _base_record(
            outcome={
                "status": "resolved",
                "source": "auto_market",
                "confidence": 0.95,
                "actual_outcome": 100.0,
            },
        )
        candidates = detect_auto_resolve_low_confidence(
            rec, confidence_threshold=0.95,
        )
        self.assertEqual(candidates, [])

    def test_auto_resolve_low_confidence_skips_manual_and_unresolved_records(self):
        manual = _base_record(
            outcome={"status": "resolved", "source": "manual", "confidence": 0.1},
        )
        pending = _base_record(
            outcome={"status": "pending", "source": "auto_market", "confidence": 0.1},
        )
        self.assertEqual(detect_auto_resolve_low_confidence(manual), [])
        self.assertEqual(detect_auto_resolve_low_confidence(pending), [])

    def test_auto_resolve_low_confidence_skips_non_numeric_confidence(self):
        rec = _base_record(
            outcome={
                "status": "resolved",
                "source": "auto_market",
                "confidence": "0.92",
            },
        )
        self.assertEqual(detect_auto_resolve_low_confidence(rec), [])

    def test_detect_review_candidates_includes_auto_resolve_detector(self):
        rec = _base_record(
            outcome={
                "status": "resolved",
                "source": "auto_market",
                "confidence": 0.92,
                "actual_outcome": 0.0,
            },
        )
        candidates = detect_review_candidates(
            rec, auto_resolve_confidence_threshold=0.93,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger"], "auto_resolve_low_confidence")

    def test_reasons_for_auto_resolve_detector_exclude_banned_terms(self):
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        rec = _base_record(
            outcome={
                "status": "resolved",
                "source": "auto_market",
                "confidence": 0.92,
                "actual_outcome": 100.0,
            },
        )
        candidates = detect_auto_resolve_low_confidence(rec)
        self.assertEqual(len(candidates), 1)
        for term in banned:
            self.assertNotIn(term, candidates[0]["reason"].lower())


if __name__ == "__main__":
    unittest.main()
