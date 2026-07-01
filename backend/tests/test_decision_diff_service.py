"""Unit tests for decision_diff_service (Plan 5 §5.4)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.services.decision_diff_service import build_decision_diff


def _snapshot(**overrides):
    snap = {
        "snapshot_id": "s1",
        "event_id": "evt-001",
        "recorded_at": "2026-07-01T00:00:00",
        "final_displayed_direction": "YES",
        "final_downgrade_reason": None,
        "probability": {"baseline": 50.0, "estimated": 55.0,
                        "change": 5.0, "direction": "YES"},
        "decision_quality": {"downgraded": False, "raw_direction": "YES",
                             "displayed_direction": "YES"},
        "market_quality": None,
        "source_reliability": None,
        "execution_quality": None,
        "llm_degraded_mode": False,
        "guardrail_fired": None,
        "outcome": None,
    }
    snap.update(overrides)
    return snap


class TestDecisionDiffService(unittest.TestCase):
    def test_no_change_when_snapshots_identical(self):
        prev = _snapshot()
        cur = _snapshot()
        diff = build_decision_diff(prev, cur)
        self.assertFalse(diff["direction_changed"])
        self.assertEqual(diff["primary_change_driver"], "none")
        self.assertEqual(diff["overlay_deltas"], [])

    def test_prev_none_treats_as_initial_snapshot(self):
        """First snapshot in a timeline has no prev — diff should report
        'initial' with no deltas."""
        diff = build_decision_diff(None, _snapshot())
        self.assertFalse(diff["direction_changed"])
        self.assertEqual(diff["primary_change_driver"], "initial")

    def test_manual_resolution_driver_when_outcome_appears(self):
        prev = _snapshot(outcome=None)
        cur = _snapshot(outcome="YES")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "manual_resolution")

    def test_llm_degraded_driver_when_degraded_mode_flips_true(self):
        prev = _snapshot(llm_degraded_mode=False)
        cur = _snapshot(llm_degraded_mode=True,
                        final_displayed_direction="WAIT",
                        final_downgrade_reason="LLM 降级")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "llm_degraded")
        self.assertTrue(diff["direction_changed"])

    def test_guardrail_driver_when_guardrail_fired_appears(self):
        prev = _snapshot(guardrail_fired=None)
        cur = _snapshot(guardrail_fired=["llm_degraded_blocks_act"],
                        final_displayed_direction="WAIT")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "guardrail")

    def test_market_quality_driver_when_downgraded_flips_true(self):
        prev = _snapshot(market_quality={"downgraded": False})
        cur = _snapshot(market_quality={"downgraded": True,
                                        "downgrade_reason": "价差过大"},
                        final_displayed_direction="WAIT")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "market_quality")

    def test_source_conflict_driver_when_source_reliability_downgrades(self):
        prev = _snapshot(source_reliability={"downgraded": False})
        cur = _snapshot(source_reliability={"downgraded": True,
                                            "downgrade_reason": "来源可靠性不足"},
                        final_displayed_direction="WAIT")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "source_conflict")

    def test_calibration_driver_when_decision_quality_downgrades(self):
        prev = _snapshot(decision_quality={"downgraded": False})
        cur = _snapshot(decision_quality={"downgraded": True,
                                          "downgrade_reason": "证据冲突"},
                        final_displayed_direction="WAIT")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "calibration")

    def test_market_move_driver_when_estimated_probability_changes(self):
        prev = _snapshot(probability={"baseline": 50.0, "estimated": 55.0,
                                      "change": 5.0, "direction": "YES"})
        cur = _snapshot(probability={"baseline": 50.0, "estimated": 45.0,
                                     "change": -5.0, "direction": "NO"},
                        final_displayed_direction="NO")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "market_move")
        self.assertTrue(diff["direction_changed"])
        self.assertEqual(diff["probability_delta"]["estimated"], -10.0)

    def test_direction_change_ranked_after_overlay_drivers(self):
        """When direction changed AND multiple overlays flipped, the overlay
        driver takes precedence over 'market_move' (a probability move alone
        is weaker evidence of *why* the direction changed than an overlay
        explicitly downgrading)."""
        prev = _snapshot(
            probability={"estimated": 55.0},
            market_quality={"downgraded": False},
        )
        cur = _snapshot(
            probability={"estimated": 50.0},
            market_quality={"downgraded": True},
            final_displayed_direction="WAIT",
        )
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "market_quality")

    def test_overlay_deltas_record_per_overlay_changes(self):
        prev = _snapshot(
            market_quality={"downgraded": False, "downgrade_reason": None},
            source_reliability={"downgraded": False, "downgrade_reason": None},
        )
        cur = _snapshot(
            market_quality={"downgraded": True, "downgrade_reason": "价差过大"},
            source_reliability={"downgraded": False, "downgrade_reason": None},
        )
        diff = build_decision_diff(prev, cur)
        deltas = {d["overlay"]: d for d in diff["overlay_deltas"]}
        self.assertIn("market_quality", deltas)
        self.assertTrue(deltas["market_quality"]["changed"])
        self.assertNotIn("source_reliability", deltas)

    def test_reasons_exclude_banned_terms(self):
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        prev = _snapshot()
        cur = _snapshot(final_displayed_direction="WAIT",
                        final_downgrade_reason="证据冲突",
                        market_quality={"downgraded": True,
                                        "downgrade_reason": "价差过大"})
        diff = build_decision_diff(prev, cur)
        # Serialize the whole diff and check no banned term appears.
        import json
        blob = json.dumps(diff, ensure_ascii=False).lower()
        for term in banned:
            self.assertNotIn(term, blob,
                             f"banned term '{term}' in diff: {blob}")

    def test_handles_missing_overlay_blocks_gracefully(self):
        """A snapshot with None overlays must not crash the diff."""
        prev = _snapshot(decision_quality=None, market_quality=None)
        cur = _snapshot(decision_quality={"downgraded": True},
                        final_displayed_direction="WAIT")
        diff = build_decision_diff(prev, cur)
        self.assertEqual(diff["primary_change_driver"], "calibration")

    def test_handles_non_dict_input(self):
        """build_decision_diff must not crash on non-dict prev/current."""
        diff = build_decision_diff(None, {"final_displayed_direction": "YES"})
        self.assertEqual(diff["primary_change_driver"], "initial")
        diff2 = build_decision_diff("garbage", {"final_displayed_direction": "YES"})
        self.assertFalse(diff2["direction_changed"])


if __name__ == "__main__":
    unittest.main()
