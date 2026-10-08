"""Regression tests for EventRecord overlay field typing + CrossValidation.

Verifies:
- CrossValidation Pydantic model accepts the dict shape written by
  event_intelligence_service (model, probability, primary_probability,
  divergence, agreement).
- CrossValidation *ignores* keys it does not declare. Audit section 53.51
  removed the ``ConfigDict(extra="allow")`` these overlay models used to set, so
  extras are back to Pydantic's default ``ignore`` -- the producers write
  declared fields only.
- EventRecord.model_validate() accepts a record with full overlay dicts, in the
  shapes the producers actually emit.
- EventRecord.model_validate() accepts a record with overlay build-failure
  blocks (``{"error": "build_failed", ...}``) -- again the real block, all
  declared keys.
- EventRecord.model_validate() accepts a record missing overlay fields
  (they default to None).
- EventRecord.model_validate() accepts a record with cross_validation dict.
- EventRecord.model_validate() accepts a record without cross_validation.
"""
from __future__ import annotations

import unittest

from app.models.event import ConfidenceBreakdown, CrossValidation, EventRecord


def _base_record() -> dict:
    """Minimal valid record dict (no overlays, no cross_validation)."""
    return {
        "event_id": "evt1",
        "event_title": "Will X happen?",
        "event_summary": "summary",
        "probability": {"baseline": 50.0, "estimated": 60.0, "change": 10.0, "direction": "rising"},
        "credibility": {"score": 60, "level": "MEDIUM", "confidence": 0.6, "news_quality": 0.5, "evidence_strength": 0.4, "source_count": 3},
        "impact": {"score": 55, "level": "MEDIUM", "drivers": ["strong_evidence"]},
        "risk": {"level": "MEDIUM", "flags": []},
        "evidence": {"direction": "supports", "strength": 0.7, "conflict": 0.2, "freshness": 0.8, "resolution_relevance": 0.9},
        "source": {"type": "prediction_market", "platform": "polymarket"},
        "value_score": 7,
        "intelligence_report": {"headline": "h", "why_it_matters": "w", "probability_assessment": "p", "recommended_action": "r"},
    }


class TestCrossValidationModel(unittest.TestCase):
    def test_accepts_full_dict_from_intelligence_service(self):
        """cross_validate() returns dict with 5 keys; CrossValidation must accept it."""
        cv_dict = {
            "model": "qwen2.5-72b",
            "probability": 65.0,
            "primary_probability": 60.0,
            "divergence": 5.0,
            "agreement": "high",
        }
        cv = CrossValidation.model_validate(cv_dict)
        self.assertEqual(cv.model, "qwen2.5-72b")
        self.assertEqual(cv.probability, 65.0)
        self.assertEqual(cv.primary_probability, 60.0)
        self.assertEqual(cv.divergence, 5.0)
        self.assertEqual(cv.agreement, "high")

    def test_ignores_keys_it_does_not_declare(self):
        """An undeclared key is dropped, not preserved.

        These models used to declare ``ConfigDict(extra="allow")``; audit
        section 53.51 removed it, because every producer writes declared fields
        only (see ``_frontend_export``). The previous version of this test was
        named ``test_accepts_extra_fields_via_extra_allow`` but asserted only
        ``cv.model`` -- which holds under either setting, so it pinned nothing.
        Both halves are asserted now: the declared value survives *and* the
        undeclared one does not, which is what makes re-adding ``allow`` fail.
        """
        cv = CrossValidation.model_validate({"model": "x", "extra_field": "ok"})
        self.assertEqual(cv.model, "x")
        self.assertNotIn("extra_field", cv.model_dump())

    def test_missing_fields_default_to_none(self):
        cv = CrossValidation.model_validate({})
        self.assertIsNone(cv.model)
        self.assertIsNone(cv.probability)
        self.assertIsNone(cv.primary_probability)
        self.assertIsNone(cv.divergence)
        self.assertIsNone(cv.agreement)


class TestEventRecordOverlayTyping(unittest.TestCase):
    def test_record_with_full_overlay_dicts_validates(self):
        """A record with all four overlays validates to typed models.

        Each dict is the shape its producer actually writes -- audit section
        53.51 read all of them: ``build_market_quality``'s return, the
        ``source_reliability`` fallback block and the ``llm_telemetry`` one. The
        previous version used invented keys (``wide_spread``, ``thin_market``,
        ``level``/``factors``) and asserted only ``assertIsNotNone``, so it
        documented a shape no producer emits *and* would have stayed green even
        if every one of those keys were silently dropped.
        """
        record = _base_record()
        record["decision_quality"] = {
            "conflict_score": 0.0,
            "consensus_level": "none",
            "raw_direction": "YES",
            "displayed_direction": "YES",
            "downgraded": False,
        }
        record["market_quality"] = {
            "score": 0.82,
            "wide_spread_flag": False,
            "thin_market_flag": False,
            "stale_price_flag": None,
            "raw_direction": "YES",
            "suggested_direction": "YES",
            "downgraded": False,
            "applied_to_displayed_direction": False,
        }
        record["source_reliability"] = {
            "overall_score": 0.85,
            "source_count": 3,
            "domain_diversity": 2,
            "raw_direction": "YES",
            "suggested_direction": "YES",
            "downgraded": False,
            "applied_to_displayed_direction": False,
        }
        record["llm_telemetry"] = {
            "model": "gpt-4o",
            "degraded_mode": False,
            "analysis_quality": "llm",
            "llm_call_count": 2,
            "estimated_token_cost": 0.01,
        }
        ev = EventRecord.model_validate(record)

        self.assertEqual(ev.decision_quality.conflict_score, 0.0)
        self.assertEqual(ev.market_quality.score, 0.82)
        self.assertEqual(ev.market_quality.suggested_direction, "YES")
        self.assertEqual(ev.source_reliability.overall_score, 0.85)
        self.assertEqual(ev.source_reliability.domain_diversity, 2)
        self.assertEqual(ev.llm_telemetry.model, "gpt-4o")
        self.assertEqual(ev.llm_telemetry.llm_call_count, 2)

    def test_record_with_overlay_build_failure_block_validates(self):
        """The real build-failure block validates to a typed DecisionQuality.

        Copied from ``event_intelligence_service``'s ``except`` arm around
        ``build_decision_quality`` rather than invented. The previous version
        passed ``{"error": ..., "reason": ...}`` -- ``reason`` is written by no
        producer -- and then asserted only ``assertIsNotNone``.
        """
        record = _base_record()
        record["decision_quality"] = {
            "error": "build_failed",
            "raw_direction": "WAIT",
            "displayed_direction": "WAIT",
            "downgraded": False,
            "downgrade_reason": None,
            "decision_rationale_zh": "决策质量构建失败，使用原始方向。",
            "supporting_evidence": [],
            "opposing_evidence": [],
            "conflict_score": 0.0,
            "consensus_level": "none",
            "reversal_triggers": [],
        }
        ev = EventRecord.model_validate(record)

        self.assertEqual(ev.decision_quality.error, "build_failed")
        self.assertEqual(ev.decision_quality.consensus_level, "none")
        self.assertEqual(ev.decision_quality.displayed_direction, "WAIT")

    def test_record_missing_overlay_fields_validates(self):
        """Old records (pre-Phase) don't have overlay fields — default to None."""
        record = _base_record()
        ev = EventRecord.model_validate(record)
        self.assertIsNone(ev.decision_quality)
        self.assertIsNone(ev.market_quality)
        self.assertIsNone(ev.source_reliability)
        self.assertIsNone(ev.llm_telemetry)
        self.assertIsNone(ev.cross_validation)


    def test_record_with_confidence_breakdown_validates_to_typed_model(self):
        """Confidence diagnostics emitted by analysis are part of EventRecord schema."""
        record = _base_record()
        record["confidence_breakdown"] = {
            "source_count": 4,
            "independent_source_count": 3,
            "official_source_count": 1,
            "counterevidence_considered": True,
            "news_quantity_score": 0.8,
            "source_structure_score": 0.9,
            "effective_source_score": 0.9,
            "source_structure_used": True,
            "source_quality_reasons": ["independent_source_support"],
        }

        ev = EventRecord.model_validate(record)

        self.assertIsInstance(ev.confidence_breakdown, ConfidenceBreakdown)
        self.assertEqual(ev.confidence_breakdown.independent_source_count, 3)
        self.assertTrue(ev.confidence_breakdown.source_structure_used)

    def test_record_with_cross_validation_validates(self):
        """Record with cross_validation dict validates to CrossValidation model."""
        record = _base_record()
        record["cross_validation"] = {
            "model": "qwen2.5-72b",
            "probability": 65.0,
            "primary_probability": 60.0,
            "divergence": 5.0,
            "agreement": "high",
        }
        ev = EventRecord.model_validate(record)
        self.assertIsNotNone(ev.cross_validation)
        self.assertEqual(ev.cross_validation.model, "qwen2.5-72b")
        self.assertEqual(ev.cross_validation.primary_probability, 60.0)

    def test_record_without_cross_validation_validates(self):
        """Record without cross_validation defaults to None."""
        record = _base_record()
        ev = EventRecord.model_validate(record)
        self.assertIsNone(ev.cross_validation)


if __name__ == "__main__":
    unittest.main()
