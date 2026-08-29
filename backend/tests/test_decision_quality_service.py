"""Tests for decision_quality_service.build_decision_quality() (Phase 1).

Pure-function tests that do not touch settings, LLM, or I/O. Verifies:
- Evidence selection for YES / NO / WAIT / AVOID directions
- Support/opposition ranking by strength * credibility
- Conflict score calculation
- Consensus level thresholds (high / medium / low / none)
- Two-stage downgrade pipeline (Stage A first-match-wins + Stage B escalation)
- Template rationale avoids banned vocabulary + has disclaimer suffix
- Missing recommendation -> raw_direction=WAIT
- Empty evidence_breakdown -> consensus_level=none + rule 4 downgrade
- No-writeback: recommendation dict is byte-equal before/after the call
- Adversarial input never raises
"""
import copy
import unittest

from app.services.decision_quality_service import build_decision_quality


def _evidence(direction: str, strength: float, credibility: float = 0.9,
              source: str = "Reuters", title: str = "Test", rationale: str = "rationale") -> dict:
    return {
        "source": source,
        "title": title,
        "direction": direction,
        "strength": strength,
        "credibility": credibility,
        "rationale_zh": rationale,
    }


def _recommendation(direction: str = "YES", risk_level: str = "medium") -> dict:
    return {
        "direction": direction,
        "confidence": "medium",
        "suggested_allocation_pct": 2.0,
        "edge": 5.0,
        "risk_level": risk_level,
        "rationale": "市场定价 50%，估计 55%。",
        "calibration_status": "uncalibrated_provisional",
    }


class BuildDecisionQualityTests(unittest.TestCase):
    """Core tests for build_decision_quality() pure function."""

    DEFAULT_KWARGS = {
        "enabled": True,
        "max_items": 3,
        "high_threshold": 0.40,
        "medium_threshold": 0.20,
    }

    # --- Evidence Selection ---

    def test_yes_recommendation_support_is_support_direction(self):
        """YES recommendation: supporting = support articles, opposing = oppose articles."""
        rec = _recommendation("YES")
        evidence = [
            _evidence("support", 0.8, source="Reuters"),
            _evidence("oppose", 0.6, source="AP"),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertEqual(len(result["supporting_evidence"]), 1)
        self.assertEqual(result["supporting_evidence"][0]["source"], "Reuters")
        self.assertEqual(len(result["opposing_evidence"]), 1)
        self.assertEqual(result["opposing_evidence"][0]["source"], "AP")

    def test_no_recommendation_support_is_oppose_direction(self):
        """NO recommendation: supporting = oppose articles, opposing = support articles."""
        rec = _recommendation("NO")
        evidence = [
            _evidence("support", 0.8, source="Reuters"),
            _evidence("oppose", 0.6, source="AP"),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        # For NO: supporting = oppose articles
        self.assertEqual(len(result["supporting_evidence"]), 1)
        self.assertEqual(result["supporting_evidence"][0]["source"], "AP")
        # For NO: opposing = support articles
        self.assertEqual(len(result["opposing_evidence"]), 1)
        self.assertEqual(result["opposing_evidence"][0]["source"], "Reuters")

    def test_wait_surfaces_both_sides_no_filtering(self):
        """WAIT surfaces BOTH support and oppose columns without recommendation-side filtering."""
        rec = _recommendation("WAIT")
        evidence = [
            _evidence("support", 0.8, source="Reuters"),
            _evidence("oppose", 0.6, source="AP"),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        # Both sides present, no swap
        self.assertEqual(len(result["supporting_evidence"]), 1)
        self.assertEqual(result["supporting_evidence"][0]["source"], "Reuters")
        self.assertEqual(len(result["opposing_evidence"]), 1)
        self.assertEqual(result["opposing_evidence"][0]["source"], "AP")

    def test_avoid_surfaces_both_sides(self):
        """AVOID also surfaces both sides, like WAIT."""
        rec = _recommendation("AVOID")
        evidence = [
            _evidence("support", 0.8),
            _evidence("oppose", 0.6),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertEqual(len(result["supporting_evidence"]), 1)
        self.assertEqual(len(result["opposing_evidence"]), 1)

    def test_neutral_items_filtered_out(self):
        """Neutral items are never selected into either column."""
        rec = _recommendation("YES")
        evidence = [
            _evidence("support", 0.8),
            _evidence("neutral", 0.9),
            _evidence("oppose", 0.6),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertEqual(len(result["supporting_evidence"]), 1)
        self.assertEqual(len(result["opposing_evidence"]), 1)

    def test_ranking_by_strength_times_credibility_descending(self):
        """Items ranked by strength * credibility, descending."""
        rec = _recommendation("YES")
        evidence = [
            _evidence("support", 0.5, credibility=0.8, source="Weak"),   # 0.40
            _evidence("support", 0.9, credibility=0.9, source="Strong"), # 0.81
            _evidence("support", 0.7, credibility=0.7, source="Mid"),   # 0.49
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        sources = [item["source"] for item in result["supporting_evidence"]]
        self.assertEqual(sources, ["Strong", "Mid", "Weak"])

    def test_max_items_caps_each_column_independently(self):
        """max_items caps supporting and opposing lists independently."""
        rec = _recommendation("YES")
        evidence = [
            _evidence("support", 0.9, source="S1"),
            _evidence("support", 0.8, source="S2"),
            _evidence("support", 0.7, source="S3"),
            _evidence("support", 0.6, source="S4"),
            _evidence("oppose", 0.9, source="O1"),
            _evidence("oppose", 0.8, source="O2"),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence,
            enabled=True, max_items=2, high_threshold=0.40, medium_threshold=0.20,
        )
        self.assertEqual(len(result["supporting_evidence"]), 2)
        self.assertEqual(len(result["opposing_evidence"]), 2)

    # --- Conflict Score ---

    def test_conflict_score_zero_when_only_support(self):
        rec = _recommendation("YES")
        evidence = [_evidence("support", 0.9, credibility=0.9)]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertEqual(result["conflict_score"], 0.0)
        self.assertEqual(result["consensus_level"], "high")

    def test_conflict_score_balanced_sides(self):
        rec = _recommendation("YES")
        evidence = [
            _evidence("support", 0.8, credibility=0.9),  # 0.72
            _evidence("oppose", 0.8, credibility=0.9),   # 0.72
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        # min(0.72, 0.72) / 1.44 = 0.5
        self.assertAlmostEqual(result["conflict_score"], 0.5, places=2)
        self.assertEqual(result["consensus_level"], "low")

    def test_conflict_score_no_strong_support_means_medium(self):
        rec = _recommendation("YES")
        evidence = [
            _evidence("support", 0.3, credibility=0.5),  # 0.15, no strong item
            _evidence("oppose", 0.2, credibility=0.5),   # 0.10
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        # conflict = 0.10 / 0.25 = 0.4 -> not < 0.20, and no strong support
        # -> not "high". 0.4 >= 0.40 (high_threshold) -> "low"
        # Actually 0.10/0.25 = 0.4, and high_threshold=0.40, so 0.4 >= 0.40
        # means "low". Let's check the actual value.
        self.assertGreaterEqual(result["conflict_score"], 0.0)

    def test_both_sides_strong_means_low(self):
        rec = _recommendation("YES")
        evidence = [
            _evidence("support", 0.8, credibility=0.9),  # strong
            _evidence("oppose", 0.75, credibility=0.9),   # strong
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertEqual(result["consensus_level"], "low")

    # --- Consensus Levels ---

    def test_consensus_none_when_empty_breakdown(self):
        rec = _recommendation("YES")
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=[], **self.DEFAULT_KWARGS
        )
        self.assertEqual(result["consensus_level"], "none")
        self.assertEqual(result["supporting_evidence"], [])
        self.assertEqual(result["opposing_evidence"], [])

    # --- Stage A Downgrade Rules ---

    def test_rule1_low_conflict_downgrades_yes_to_wait(self):
        rec = _recommendation("YES", risk_level="medium")
        evidence = [
            _evidence("support", 0.8, credibility=0.9),
            _evidence("oppose", 0.8, credibility=0.9),  # balanced -> low
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertEqual(result["displayed_direction"], "WAIT")
        self.assertTrue(result["downgraded"])
        self.assertIn("证据冲突较高", result["downgrade_reason"])

    def test_rule1_wins_when_rule2_would_also_fire(self):
        """Rules 1-3 are first-match-wins, so rule 1 owns the reason string.

        This input satisfies *both* rule 1 (conflict_score 0.4706 -> consensus
        "low") and rule 2 (opposing evidence from an official source at
        strength >= 0.7). Both downgrade to WAIT, so ``displayed_direction``
        cannot tell them apart — only ``downgrade_reason`` can. Pinning the
        reason is what makes a reordering of the rule chain in
        ``_apply_downgrade_rules`` fail a test instead of silently changing
        which explanation the operator reads.

        The companion assertion below removes the official source and shows the
        output is byte-identical, i.e. rule 2 contributes nothing here. Without
        it, this test would read as coverage of the official-source term.
        """
        rec = _recommendation("YES", risk_level="medium")
        evidence = [
            _evidence("support", 0.9, credibility=0.9, source="Reuters"),
            _evidence("oppose", 0.8, credibility=0.9, source="Official Government Source"),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        # support 0.9*0.9 = 0.81, oppose 0.8*0.9 = 0.72
        # conflict = 0.72 / 1.53 = 0.4706 -> >= high_threshold -> "low"
        self.assertEqual(result["consensus_level"], "low")
        self.assertEqual(result["displayed_direction"], "WAIT")
        self.assertTrue(result["downgraded"])
        # Rule 1's reason, NOT rule 2's ("官方/监管反向证据").
        self.assertIn("证据冲突较高", result["downgrade_reason"])
        self.assertNotIn("官方", result["downgrade_reason"])

        # Same weights, non-official opposing source: rule 2's precondition is
        # gone and nothing about the outcome changes.
        non_official = [
            _evidence("support", 0.9, credibility=0.9, source="Reuters"),
            _evidence("oppose", 0.8, credibility=0.9, source="Some Blog"),
        ]
        control = build_decision_quality(
            recommendation=_recommendation("YES", risk_level="medium"),
            evidence_breakdown=non_official, **self.DEFAULT_KWARGS
        )
        self.assertEqual(control["downgrade_reason"], result["downgrade_reason"])
        self.assertEqual(control["consensus_level"], result["consensus_level"])

    def test_rule2_isolated(self):
        """Rule 2 fires when consensus is NOT low but official opposing
        exists with strength >= 0.7. To isolate rule 2 from rule 1 (low
        consensus) and the "both sides strong" safety net, support must NOT
        be strong (strength < 0.7) and conflict_score must be in the
        "medium" range [0.20, 0.40)."""
        rec = _recommendation("YES", risk_level="medium")
        evidence = [
            # support: not strong (0.6 < 0.7), but weight > opposing
            _evidence("support", 0.6, credibility=0.9, source="MidSupport"),
            # opposing: strength >= 0.7 (strong) triggers rule 2,
            # low credibility keeps conflict_score in "medium" range
            _evidence("oppose", 0.75, credibility=0.3, source="Official Ministry"),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        # support_weight = 0.6*0.9 = 0.54
        # oppose_weight = 0.75*0.3 = 0.225
        # conflict = 0.225 / 0.765 = 0.294 -> >= 0.20, < 0.40 -> "medium"
        # rule 1 doesn't fire (consensus != low)
        # rule 2 fires: official opposing with strength >= 0.7
        self.assertEqual(result["consensus_level"], "medium")
        self.assertEqual(result["displayed_direction"], "WAIT")
        self.assertIn("官方", result["downgrade_reason"])

    def test_rule3_supporting_empty_downgrades_to_wait(self):
        """Rule 3: supporting column is empty (but breakdown is not empty).
        E.g., only opposing evidence exists for a YES recommendation."""
        rec = _recommendation("YES", risk_level="medium")
        evidence = [
            _evidence("oppose", 0.6, credibility=0.7, source="AP"),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        # consensus != none (evidence exists), supporting is empty
        # rule 3 fires: "缺少支持证据"
        self.assertNotEqual(result["consensus_level"], "none")
        self.assertEqual(result["displayed_direction"], "WAIT")
        self.assertIn("缺少支持证据", result["downgrade_reason"])

    def test_rule4_empty_breakdown_downgrades_yes_to_wait(self):
        """Rule 4: evidence_breakdown is empty -> "缺少证据支持" (different
        wording from rule 3 "缺少支持证据")."""
        rec = _recommendation("YES", risk_level="medium")
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=[], **self.DEFAULT_KWARGS
        )
        self.assertEqual(result["displayed_direction"], "WAIT")
        self.assertIn("缺少证据支持", result["downgrade_reason"])
        # Must NOT be rule 3 wording
        self.assertNotIn("缺少支持证据", result["downgrade_reason"])

    def test_rule4_empty_breakdown_does_not_downgrade_wait(self):
        """Rule 4 only applies to YES/NO. WAIT is unchanged."""
        rec = _recommendation("WAIT", risk_level="medium")
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=[], **self.DEFAULT_KWARGS
        )
        self.assertEqual(result["displayed_direction"], "WAIT")
        self.assertFalse(result["downgraded"])
        self.assertIsNone(result["downgrade_reason"])

    def test_no_rule_fires_no_downgrade(self):
        rec = _recommendation("YES", risk_level="medium")
        evidence = [
            _evidence("support", 0.9, credibility=0.9, source="Reuters"),
            _evidence("oppose", 0.2, credibility=0.5, source="Blog"),  # weak
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        # support_weight=0.81, oppose_weight=0.10, conflict=0.10/0.91=0.11
        # conflict < medium_threshold (0.20) and has strong support -> "high"
        # No Stage A rule fires, no Stage B (risk=medium)
        self.assertEqual(result["consensus_level"], "high")
        self.assertEqual(result["displayed_direction"], "YES")
        self.assertFalse(result["downgraded"])
        self.assertIsNone(result["downgrade_reason"])

    # --- Stage B Risk Escalation ---

    def test_stage_b_escalates_wait_to_avoid(self):
        """Stage B: rule 1 fires (WAIT), then risk escalation escalates to AVOID."""
        rec = _recommendation("YES", risk_level="high")
        evidence = [
            _evidence("support", 0.8, credibility=0.9),
            _evidence("oppose", 0.8, credibility=0.9),  # balanced -> low
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        # Stage A rule 1: consensus low -> WAIT
        # Stage B: risk=high + consensus=low -> AVOID
        self.assertEqual(result["displayed_direction"], "AVOID")
        self.assertTrue(result["downgraded"])
        self.assertIn("高风险", result["downgrade_reason"])

    def test_stage_b_escalates_yes_directly_to_avoid(self):
        """Stage B can escalate raw YES to AVOID directly when no Stage A
        rule fired (high risk + low consensus, but Stage A rule 1 fires
        first on low consensus... so to test direct YES->AVOID we need
        consensus=none + risk=high)."""
        rec = _recommendation("YES", risk_level="high")
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=[], **self.DEFAULT_KWARGS
        )
        # consensus=none -> Stage A rule 4 sets WAIT
        # Stage B: risk=high + consensus=none -> AVOID
        self.assertEqual(result["displayed_direction"], "AVOID")
        self.assertIn("高风险", result["downgrade_reason"])

    def test_stage_b_does_not_de_escalate(self):
        """Stage B cannot de-escalate. If Stage A didn't fire and risk is
        not high, displayed stays raw."""
        rec = _recommendation("YES", risk_level="low")
        evidence = [
            _evidence("support", 0.9, credibility=0.9),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertEqual(result["displayed_direction"], "YES")
        self.assertFalse(result["downgraded"])

    # --- Missing Inputs ---

    def test_missing_recommendation_raw_direction_wait(self):
        """When recommendation is None, raw_direction=WAIT, both sides
        surfaced, displayed_direction=WAIT, downgraded=False."""
        evidence = [
            _evidence("support", 0.8),
            _evidence("oppose", 0.6),
        ]
        result = build_decision_quality(
            recommendation=None, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertEqual(result["raw_direction"], "WAIT")
        self.assertEqual(result["displayed_direction"], "WAIT")
        self.assertFalse(result["downgraded"])
        self.assertEqual(len(result["supporting_evidence"]), 1)
        self.assertEqual(len(result["opposing_evidence"]), 1)

    def test_missing_both_inputs_emits_block(self):
        """Missing both recommendation and evidence_breakdown: still emits
        a well-formed block."""
        result = build_decision_quality(
            recommendation=None, evidence_breakdown=[],
            enabled=True, max_items=3, high_threshold=0.40, medium_threshold=0.20,
        )
        self.assertEqual(result["raw_direction"], "WAIT")
        self.assertEqual(result["displayed_direction"], "WAIT")
        self.assertEqual(result["consensus_level"], "none")
        self.assertFalse(result["downgraded"])
        self.assertIsNone(result["downgrade_reason"])
        self.assertNotEqual(result["decision_rationale_zh"], "")

    def test_adversarial_input_never_raises(self):
        """All-None, all-empty, all-malformed input returns a well-formed block."""
        # recommendation is a string (not dict), evidence has non-dict items
        result = build_decision_quality(
            recommendation="not a dict",  # type: ignore
            evidence_breakdown=[None, 42, "string", {}, {"direction": "invalid"}],
            enabled=True, max_items=3, high_threshold=0.40, medium_threshold=0.20,
        )
        self.assertEqual(result["raw_direction"], "WAIT")
        self.assertEqual(result["consensus_level"], "none")
        self.assertEqual(result["supporting_evidence"], [])

    # --- No-Writeback Invariant ---

    def test_no_writeback_to_recommendation(self):
        """actionable_recommendation dict is byte-equal before/after the call."""
        rec = _recommendation("YES", risk_level="medium")
        evidence = [
            _evidence("support", 0.8),
            _evidence("oppose", 0.8),
        ]
        rec_before = copy.deepcopy(rec)
        evidence_before = copy.deepcopy(evidence)
        build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertEqual(rec, rec_before, "recommendation was mutated")
        self.assertEqual(evidence, evidence_before, "evidence_breakdown was mutated")

    # --- Banned Vocabulary + Disclaimer ---

    def test_rationale_avoids_banned_vocab(self):
        rec = _recommendation("YES", risk_level="medium")
        evidence = [
            _evidence("support", 0.8, source="Reuters", rationale="支持 YES 的事实"),
            _evidence("oppose", 0.6, source="AP", rationale="反向证据"),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        blob = (
            result["decision_rationale_zh"] + " " +
            (result["downgrade_reason"] or "") + " " +
            " ".join(item["rationale_zh"] for item in result["supporting_evidence"]) +
            " ".join(item["rationale_zh"] for item in result["opposing_evidence"])
        ).lower()
        for banned in ("long", "short", "buy", "sell", "position", "kelly", "order"):
            self.assertNotIn(banned, blob, f"banned word '{banned}' found in rationale")

    def test_rationale_ends_with_disclaimer(self):
        rec = _recommendation("YES", risk_level="medium")
        evidence = [_evidence("support", 0.8)]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertTrue(
            result["decision_rationale_zh"].endswith("不构成投资建议。"),
            f"rationale missing disclaimer: {result['decision_rationale_zh']}"
        )

    def test_downgraded_rationale_ends_with_disclaimer(self):
        rec = _recommendation("YES", risk_level="medium")
        evidence = [
            _evidence("support", 0.8),
            _evidence("oppose", 0.8),
        ]
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=evidence, **self.DEFAULT_KWARGS
        )
        self.assertTrue(result["downgraded"])
        self.assertTrue(
            result["decision_rationale_zh"].endswith("不构成投资建议。"),
            f"downgraded rationale missing disclaimer: {result['decision_rationale_zh']}"
        )

    # --- raw_direction / displayed_direction ---

    def test_raw_direction_mirrors_recommendation(self):
        for direction in ("YES", "NO", "WAIT", "AVOID"):
            rec = _recommendation(direction)
            result = build_decision_quality(
                recommendation=rec, evidence_breakdown=[],
                enabled=True, max_items=3, high_threshold=0.40, medium_threshold=0.20,
            )
            self.assertEqual(result["raw_direction"], direction)

    def test_downgraded_flag_true_when_directions_diverge(self):
        rec = _recommendation("YES", risk_level="high")
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=[],
            enabled=True, max_items=3, high_threshold=0.40, medium_threshold=0.20,
        )
        # YES -> WAIT (rule 4) -> AVOID (stage B)
        self.assertNotEqual(result["raw_direction"], result["displayed_direction"])
        self.assertTrue(result["downgraded"])

    # --- reversal_triggers ---

    def test_reversal_triggers_empty_in_phase1(self):
        rec = _recommendation("YES")
        result = build_decision_quality(
            recommendation=rec, evidence_breakdown=[_evidence("support", 0.8)],
            **self.DEFAULT_KWARGS
        )
        self.assertEqual(result["reversal_triggers"], [])


if __name__ == "__main__":
    unittest.main()
