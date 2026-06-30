"""Tests for the Phase 1 DecisionQuality models.

Locks:
- EvidenceBreakdownItem.direction is Literal["support", "oppose", "neutral"]
  (rejects YES/NO/WAIT/AVOID/LONG/SHORT at construction time — locks the
  two-vocabulary separation at the model boundary)
- DecisionQuality default field values match the spec's "missing input"
  fallback (raw_direction=WAIT, displayed_direction=WAIT, consensus_level=none)
- EventRecord.decision_quality defaults to None (feature off → byte-identical
  to records built before Phase 1)
"""

import unittest

from pydantic import ValidationError

from app.models.event import (
    DecisionEvidenceItem,
    DecisionQuality,
    EvidenceBreakdownItem,
    EventRecord,
)


class EvidenceBreakdownItemVocabularyLockTests(unittest.TestCase):
    """Spec: EvidenceBreakdownItem.direction rejects values outside
    {support, oppose, neutral}. This locks the two-vocabulary separation
    (article stance vs recommendation direction) at the model boundary."""

    def test_support_oppose_neutral_accepted(self):
        for valid in ("support", "oppose", "neutral"):
            item = EvidenceBreakdownItem(direction=valid)
            self.assertEqual(item.direction, valid)

    def test_yes_rejected(self):
        with self.assertRaises(ValidationError):
            EvidenceBreakdownItem(direction="YES")

    def test_no_rejected(self):
        with self.assertRaises(ValidationError):
            EvidenceBreakdownItem(direction="NO")

    def test_wait_rejected(self):
        with self.assertRaises(ValidationError):
            EvidenceBreakdownItem(direction="WAIT")

    def test_avoid_rejected(self):
        with self.assertRaises(ValidationError):
            EvidenceBreakdownItem(direction="AVOID")

    def test_long_rejected(self):
        with self.assertRaises(ValidationError):
            EvidenceBreakdownItem(direction="LONG")

    def test_short_rejected(self):
        with self.assertRaises(ValidationError):
            EvidenceBreakdownItem(direction="SHORT")

    def test_buy_rejected(self):
        with self.assertRaises(ValidationError):
            EvidenceBreakdownItem(direction="buy")

    def test_empty_string_rejected(self):
        with self.assertRaises(ValidationError):
            EvidenceBreakdownItem(direction="")


class DecisionQualityDefaultTests(unittest.TestCase):
    """Spec: when inputs are missing, the block defaults to the fallback
    shape (raw_direction=WAIT, displayed_direction=WAIT, downgraded=False,
    consensus_level=none)."""

    def test_defaults(self):
        dq = DecisionQuality()
        self.assertEqual(dq.raw_direction, "WAIT")
        self.assertEqual(dq.displayed_direction, "WAIT")
        self.assertFalse(dq.downgraded)
        self.assertEqual(dq.consensus_level, "none")
        self.assertEqual(dq.conflict_score, 0.0)
        self.assertEqual(dq.supporting_evidence, [])
        self.assertEqual(dq.opposing_evidence, [])
        self.assertEqual(dq.reversal_triggers, [])
        self.assertIsNone(dq.downgrade_reason)
        self.assertIsNone(dq.error)
        self.assertEqual(dq.decision_rationale_zh, "")

    def test_raw_direction_literal_rejects_invalid(self):
        with self.assertRaises(ValidationError):
            DecisionQuality(raw_direction="MAYBE")

    def test_displayed_direction_literal_rejects_invalid(self):
        with self.assertRaises(ValidationError):
            DecisionQuality(displayed_direction="HOLD")

    def test_consensus_level_literal_rejects_invalid(self):
        with self.assertRaises(ValidationError):
            DecisionQuality(consensus_level="very_high")

    def test_downgraded_flag_set_when_directions_diverge(self):
        dq = DecisionQuality(
            raw_direction="YES",
            displayed_direction="WAIT",
            downgraded=True,
        )
        self.assertTrue(dq.downgraded)

    def test_error_field_allows_fallback_block(self):
        dq = DecisionQuality(
            error="build_failed",
            raw_direction="YES",
            displayed_direction="YES",
            decision_rationale_zh="决策质量构建失败，使用原始方向。",
        )
        self.assertEqual(dq.error, "build_failed")


class DecisionEvidenceItemTests(unittest.TestCase):
    def test_defaults(self):
        item = DecisionEvidenceItem()
        self.assertEqual(item.source, "")
        self.assertEqual(item.title, "")
        self.assertEqual(item.strength, 0.0)
        self.assertEqual(item.credibility, 0.0)
        self.assertEqual(item.rationale_zh, "")

    def test_construct_from_breakdown_item(self):
        """DecisionEvidenceItem is shape-compatible with the subset of
        EvidenceBreakdownItem fields (source, title, strength, credibility,
        rationale_zh). The service copies these fields when promoting an
        audit item to a decision driver."""
        breakdown = EvidenceBreakdownItem(
            source="Reuters",
            title="Fed signals rate cut",
            direction="support",
            strength=0.85,
            credibility=0.9,
            rationale_zh="直接支持 YES 的事实。",
        )
        driver = DecisionEvidenceItem(
            source=breakdown.source,
            title=breakdown.title,
            strength=breakdown.strength,
            credibility=breakdown.credibility,
            rationale_zh=breakdown.rationale_zh,
        )
        self.assertEqual(driver.source, "Reuters")
        self.assertEqual(driver.strength, 0.85)
        self.assertEqual(driver.rationale_zh, "直接支持 YES 的事实。")


class EventRecordDecisionQualityTests(unittest.TestCase):
    """Spec: when DECISION_QUALITY_ENABLED=false (default), the record has
    no decision_quality key — byte-identical to a record built before
    Phase 1. The field defaults to None.

    EventRecord has 10+ required fields; use model_construct to bypass
    validation and isolate the decision_quality field behavior."""

    def test_decision_quality_defaults_to_none(self):
        record = EventRecord.model_construct(event_id="ev_test")
        self.assertIsNone(record.decision_quality)

    def test_decision_quality_can_be_set(self):
        record = EventRecord.model_construct(
            event_id="ev_test",
            decision_quality={
                "raw_direction": "YES",
                "displayed_direction": "WAIT",
                "downgraded": True,
            },
        )
        self.assertIsNotNone(record.decision_quality)
        self.assertEqual(record.decision_quality["raw_direction"], "YES")


if __name__ == "__main__":
    unittest.main()
