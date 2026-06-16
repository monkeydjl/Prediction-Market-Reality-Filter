import unittest

from app.services.event_intelligence_service import (
    build_event_record,
    calculate_impact_score,
    calculate_trust_score,
    impact_drivers,
    probability_direction,
)


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


if __name__ == "__main__":
    unittest.main()
