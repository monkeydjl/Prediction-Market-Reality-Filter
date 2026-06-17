import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import app.services.ai_analysis_service as ai
import app.services.event_intelligence_service as eis
from app.services.event_intelligence_service import (
    build_event_record,
    build_evidence_items,
    calculate_impact_score,
    calculate_trust_score,
    impact_drivers,
    probability_direction,
)


def _run(coro):
    return asyncio.run(coro)


class EventIntelligenceServiceTests(unittest.TestCase):
    def test_build_event_record_maps_probability_change_and_scores(self):
        record = build_event_record({
            "market_question": "Will the bill pass before June?",
            "market_probability": 58,
            "ai_probability": 72,
            "divergence": 14,
            "confidence_score": 0.7,
            "news_quality_score": 0.8,
            "evidence_strength": 0.55,
            "resolution_relevance_score": 0.6,
            "evidence_conflict_score": 0.1,
            "freshness_score": 0.9,
            "base_rate_category": "legal",
            "risk_level": "MEDIUM",
            "risk_flags": ["watch_confirmation"],
            "narrative_summary": "A key legislator publicly backed the bill.",
        })

        self.assertEqual(record["event_title"], "Will the bill pass before June?")
        self.assertEqual(record["probability"]["baseline"], 58)
        self.assertEqual(record["probability"]["estimated"], 72)
        self.assertEqual(record["probability"]["change"], 14)
        self.assertEqual(record["probability"]["direction"], "rising")
        self.assertEqual(record["credibility"]["level"], "HIGH")
        self.assertIn("material_probability_change", record["impact"]["drivers"])
        self.assertNotIn(
            "trade",
            record["intelligence_report"]["recommended_action"].lower(),
        )

    def test_build_event_record_adds_default_tracking_and_title_zh(self):
        record = build_event_record({
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 56,
            "title_zh": "法案会通过吗？",
        })
        self.assertEqual(record["event_title_zh"], "法案会通过吗？")
        self.assertEqual(record["tracking"]["status"], "watching")
        self.assertIn(record["tracking"]["priority"], {"high", "medium", "low"})
        # The Chinese title (when present) drives the headline.
        self.assertIn("法案会通过吗？", record["intelligence_report"]["headline"])

    def test_build_evidence_items_groups_and_preserves_url(self):
        items = build_evidence_items([
            {
                "kind": "official", "source": "Federal Reserve",
                "title": "Fed holds rates", "description": "rate hold",
                "url": "https://fed.gov/x", "published": "Mon, 01 Jun 2026 00:00:00 GMT",
                "quality_score": 0.8, "relevance_score": 0.6,
            },
            {
                "kind": "news", "source": "Reuters", "title": "Reuters report",
                "description": "context", "url": "https://r.com/y",
                "quality_score": 0.7, "relevance_score": 0.5,
            },
            {"title": "   "},  # blank title is dropped
        ])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["kind"], "official")
        self.assertEqual(items[0]["url"], "https://fed.gov/x")
        self.assertEqual(items[0]["quality"], 0.8)
        self.assertEqual({i["kind"] for i in items}, {"official", "news"})

    def test_build_evidence_items_handles_empty(self):
        self.assertEqual(build_evidence_items(None), [])
        self.assertEqual(build_evidence_items([]), [])

    def test_scores_are_bounded_when_analysis_is_sparse(self):
        analysis = {"market_question": "Sparse event"}

        self.assertEqual(calculate_trust_score(analysis), 10)
        self.assertEqual(calculate_impact_score(analysis), 0)
        self.assertEqual(probability_direction(1.9), "stable")
        self.assertEqual(probability_direction(-2), "falling")

    def test_zero_probability_is_preserved(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 10,
            "ai_probability": 0,
            "divergence": -10,
        })

        self.assertEqual(record["probability"]["estimated"], 0)
        self.assertEqual(record["probability"]["direction"], "falling")

    def test_non_dict_source_defaults_to_manual_source(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
        }, source="bad-source")

        self.assertEqual(record["source"], {"type": "manual"})

    def test_complex_source_values_are_dropped(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
        }, source={
            "type": "open_web",
            "platform": ["bad"],
            "event_type": {"bad": "shape"},
            "confidence": 0.7,
        })

        self.assertEqual(record["source"], {"type": "open_web", "confidence": 0.7})

    def test_empty_source_values_are_dropped(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
        }, source={
            "type": "",
            "platform": "   ",
            "event_type": None,
        })

        self.assertEqual(record["source"], {"type": "manual"})

    def test_non_list_risk_flags_default_to_empty_list(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
            "risk_flags": "not-a-list",
        })

        self.assertEqual(record["risk"]["flags"], [])

    def test_invalid_risk_level_defaults_to_unknown(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
            "risk_level": ["bad"],
        })

        self.assertEqual(record["risk"]["level"], "UNKNOWN")

    def test_invalid_evidence_direction_defaults_to_neutral(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
            "evidence_direction": {"bad": "shape"},
        })

        self.assertEqual(record["evidence"]["direction"], "neutral")

    def test_negative_source_count_clamps_to_zero(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
            "source_count": -3,
        })

        self.assertEqual(record["credibility"]["source_count"], 0)

    def test_impact_drivers_ignore_non_string_base_rate_category(self):
        drivers = impact_drivers({"base_rate_category": ["bad"]})

        self.assertEqual(drivers, ["monitor_for_confirmation"])

    def test_semantics_populated_from_analysis_fields(self):
        record = build_event_record({
            "market_question": "Will Bitcoin reach $100k?",
            "market_probability": 50,
            "ai_probability": 60,
            "resolution_criteria": "BTC closes at or above $100,000",
            "time_horizon": "by end of 2026",
            "entities": ["Bitcoin", " Satoshi ", ""],
        })
        self.assertEqual(record["semantics"]["resolution_criteria"],
                         "BTC closes at or above $100,000")
        self.assertEqual(record["semantics"]["time_horizon"], "by end of 2026")
        # Blanks dropped, whitespace stripped.
        self.assertEqual(record["semantics"]["entities"], ["Bitcoin", "Satoshi"])

    def test_semantics_none_when_all_fields_empty(self):
        record = build_event_record({"market_question": "Will X happen?"})
        self.assertIsNone(record.get("semantics"))


class AnalyzeEventCalibrationFeedbackTests(unittest.TestCase):
    """The calibration feedback wiring in analyze_event.

    The LLM call is mocked to the deterministic fallback and cross-validation is
    disabled, so these are network-free. The feedback math itself is covered in
    test_calibration_feedback_service; here we lock the wiring: components are
    always recorded, and the published estimate is only overwritten when the
    feature is enabled.
    """

    NEWS = "direction: support\nstrength: 0.5\nsource_count: 3\n"

    def _analyze(self):
        with patch.object(ai, "_ask_ai", new=AsyncMock(side_effect=RuntimeError())), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            return _run(eis.analyze_event(
                "Will it pass?", baseline_probability=50, news_context=self.NEWS))

    def test_records_components_when_disabled(self):
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False):
            record = self._analyze()
        components = record["calibration_components"]
        self.assertEqual(components["market"], record["probability"]["baseline"])
        self.assertEqual(components["llm"], record["probability"]["estimated"])
        # Cross-validation disabled -> not a component.
        self.assertNotIn("cross_validation", components)

    def test_estimate_unchanged_when_disabled(self):
        """Default-off: no feedback metadata and the estimate is the LLM value."""
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False):
            record = self._analyze()
        self.assertNotIn("calibration_feedback", record)
        self.assertEqual(
            record["probability"]["estimated"],
            record["calibration_components"]["llm"],
        )

    def test_records_cross_validation_component_when_present(self):
        cross = {"model": "second", "probability": 64.0,
                 "primary_probability": 60.0, "divergence": 4.0, "agreement": "high"}
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False), \
                patch.object(ai, "_ask_ai", new=AsyncMock(side_effect=RuntimeError())), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=cross)):
            record = _run(eis.analyze_event(
                "Will it pass?", baseline_probability=50, news_context=self.NEWS))
        self.assertEqual(record["calibration_components"]["cross_validation"], 64.0)

    def test_overwrites_estimate_when_enabled(self):
        """When enabled, analyze_event publishes adjust_probability's result and
        recomputes change/direction from it."""
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", True), \
                patch("app.services.calibration_feedback_service.adjust_probability",
                      return_value=(33.0, {"weights": {}, "shrinkage": 0.0,
                                           "fused": 33.0, "samples": 9})):
            record = self._analyze()
        prob = record["probability"]
        self.assertEqual(prob["estimated"], 33.0)
        self.assertEqual(prob["change"], round(33.0 - prob["baseline"], 2))
        self.assertEqual(prob["direction"], "falling")
        self.assertEqual(record["calibration_feedback"]["samples"], 9)

    def test_enabled_but_no_history_is_noop(self):
        """Enabled with empty history: adjust_probability returns the llm value,
        so the published estimate is unchanged from the disabled case."""
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False):
            disabled_estimate = self._analyze()["probability"]["estimated"]
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", True), \
                patch("app.services.calibration_feedback_service._load_resolved_records",
                      return_value=[]):
            record = self._analyze()
        self.assertEqual(record["probability"]["estimated"], disabled_estimate)


if __name__ == "__main__":
    unittest.main()
