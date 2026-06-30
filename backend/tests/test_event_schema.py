"""Unit tests for event_schema.normalize_event_record.

Verifies:
- v1.0 records (no schema_version) get all v2.0 + v2.1 overlay fields backfilled.
- v2.0 records get only v2.1 fields backfilled.
- v2.1 records are no-op.
- Explicit field values are preserved (setdefault semantics).
- Idempotency: normalizing twice produces identical output.
- needs_upgrade() correctly identifies stale records.
"""
from __future__ import annotations

import unittest

from app.services.event_schema import (
    CURRENT_SCHEMA_VERSION,
    needs_upgrade,
    normalize_event_record,
)


def _v1_record() -> dict:
    """Minimal v1.0 record (no schema_version, no overlay fields)."""
    return {
        "event_id": "evt1",
        "event_title": "Will X happen?",
        "event_summary": "summary",
        "probability": {"baseline": 50.0, "estimated": 60.0, "change": 10.0, "direction": "rising"},
        "credibility": {"score": 60, "level": "MEDIUM", "confidence": 0.6, "news_quality": 0.5, "evidence_strength": 0.4, "source_count": 3},
        "impact": {"score": 55, "level": "MEDIUM", "drivers": ["strong_evidence"]},
        "risk": {"level": "LOW", "flags": []},
        "evidence": {"direction": "supports", "strength": 0.4, "conflict": 0.1, "freshness": 0.7, "resolution_relevance": 0.5},
        "source": {"type": "manual"},
        "value_score": 50,
        "intelligence_report": {"headline": "h", "why_it_matters": "w", "probability_assessment": "p", "recommended_action": "a"},
    }


def _v20_record() -> dict:
    """v2.0 record with Phase 1-3 fields but missing Phase 4-5."""
    rec = _v1_record()
    rec["schema_version"] = "v2.0"
    rec["decision_quality"] = {"raw_direction": "YES"}
    rec["market_quality"] = {"score": 0.85}
    rec["final_displayed_direction"] = "YES"
    rec["final_downgrade_reason"] = None
    return rec


def _v21_record() -> dict:
    """v2.1 record with all overlay fields."""
    rec = _v20_record()
    rec["schema_version"] = "v2.1"
    rec["source_reliability"] = {"overall_score": 0.78}
    rec["llm_telemetry"] = {"degraded_mode": False}
    return rec


class TestNormalizeEventRecord(unittest.TestCase):

    def test_v1_record_gets_all_overlay_fields(self):
        rec = _v1_record()
        self.assertNotIn("schema_version", rec)

        normalize_event_record(rec)

        self.assertEqual(rec["schema_version"], CURRENT_SCHEMA_VERSION)
        # v2.0 fields
        self.assertIn("decision_quality", rec)
        self.assertIn("market_quality", rec)
        self.assertIn("final_displayed_direction", rec)
        self.assertIn("final_downgrade_reason", rec)
        # v2.1 fields
        self.assertIn("source_reliability", rec)
        self.assertIn("llm_telemetry", rec)
        # Defaults are None (no spurious data)
        self.assertIsNone(rec["decision_quality"])
        self.assertIsNone(rec["source_reliability"])

    def test_v20_record_gets_only_v21_fields(self):
        rec = _v20_record()
        original_dq = rec["decision_quality"]

        normalize_event_record(rec)

        self.assertEqual(rec["schema_version"], CURRENT_SCHEMA_VERSION)
        # v2.0 fields preserved (not overwritten)
        self.assertEqual(rec["decision_quality"], original_dq)
        self.assertEqual(rec["market_quality"], {"score": 0.85})
        self.assertEqual(rec["final_displayed_direction"], "YES")
        # v2.1 fields backfilled
        self.assertIn("source_reliability", rec)
        self.assertIn("llm_telemetry", rec)
        self.assertIsNone(rec["source_reliability"])
        self.assertIsNone(rec["llm_telemetry"])

    def test_v21_record_is_noop(self):
        rec = _v21_record()
        original = rec.copy()
        original_sr = rec["source_reliability"]
        original_lt = rec["llm_telemetry"]

        normalize_event_record(rec)

        # All values preserved
        self.assertEqual(rec["schema_version"], CURRENT_SCHEMA_VERSION)
        self.assertEqual(rec["source_reliability"], original_sr)
        self.assertEqual(rec["llm_telemetry"], original_lt)
        self.assertEqual(rec["decision_quality"], original["decision_quality"])

    def test_explicit_values_preserved(self):
        """If a v1.0 record somehow has source_reliability set, normalize must NOT overwrite."""
        rec = _v1_record()
        rec["source_reliability"] = {"overall_score": 0.99}  # explicit value
        rec["llm_telemetry"] = {"degraded_mode": True}

        normalize_event_record(rec)

        # setdefault preserves explicit values
        self.assertEqual(rec["source_reliability"], {"overall_score": 0.99})
        self.assertEqual(rec["llm_telemetry"], {"degraded_mode": True})

    def test_idempotent(self):
        """Normalizing twice produces identical output."""
        rec = _v1_record()
        normalize_event_record(rec)
        first_pass = dict(rec)

        normalize_event_record(rec)
        second_pass = dict(rec)

        self.assertEqual(first_pass, second_pass)

    def test_malformed_schema_version_treated_as_v1(self):
        rec = _v1_record()
        rec["schema_version"] = "garbage"  # not v-prefixed

        normalize_event_record(rec)

        self.assertEqual(rec["schema_version"], CURRENT_SCHEMA_VERSION)
        self.assertIn("source_reliability", rec)  # backfilled

    def test_returns_same_dict_object(self):
        """normalize mutates in place and returns the same dict (for chaining)."""
        rec = _v1_record()
        result = normalize_event_record(rec)
        self.assertIs(result, rec)

    def test_non_dict_input_returned_unchanged(self):
        self.assertIsNone(normalize_event_record(None))  # type: ignore[arg-type]
        self.assertEqual(normalize_event_record("not a dict"), "not a dict")  # type: ignore[arg-type]

    def test_v20_to_v21_upgrade_preserves_v20_fields(self):
        """Regression: v2.0 → v2.1 must not lose decision_quality etc."""
        rec = _v20_record()
        normalize_event_record(rec)

        self.assertEqual(rec["decision_quality"], {"raw_direction": "YES"})
        self.assertEqual(rec["market_quality"], {"score": 0.85})
        self.assertEqual(rec["final_displayed_direction"], "YES")


class TestNeedsUpgrade(unittest.TestCase):

    def test_v1_record_needs_upgrade(self):
        self.assertTrue(needs_upgrade(_v1_record()))

    def test_v20_record_needs_upgrade(self):
        self.assertTrue(needs_upgrade(_v20_record()))

    def test_v21_record_does_not_need_upgrade(self):
        self.assertFalse(needs_upgrade(_v21_record()))

    def test_future_version_does_not_need_upgrade(self):
        rec = _v21_record()
        rec["schema_version"] = "v3.0"
        self.assertFalse(needs_upgrade(rec))

    def test_malformed_version_needs_upgrade(self):
        rec = _v1_record()
        rec["schema_version"] = "garbage"
        self.assertTrue(needs_upgrade(rec))

    def test_non_dict_returns_false(self):
        self.assertFalse(needs_upgrade(None))  # type: ignore[arg-type]
        self.assertFalse(needs_upgrade("not a dict"))  # type: ignore[arg-type]


class TestVersionParsing(unittest.TestCase):

    def test_v1_parses_to_1_0(self):
        from app.services.event_schema import _parse_version
        self.assertEqual(_parse_version("v1.0"), (1, 0))

    def test_v21_parses_to_2_1(self):
        from app.services.event_schema import _parse_version
        self.assertEqual(_parse_version("v2.1"), (2, 1))

    def test_no_prefix_parses_to_0_0(self):
        from app.services.event_schema import _parse_version
        self.assertEqual(_parse_version("2.1"), (0, 0))

    def test_malformed_parses_to_0_0(self):
        from app.services.event_schema import _parse_version
        self.assertEqual(_parse_version("garbage"), (0, 0))

    def test_missing_minor_parses_to_major_0(self):
        from app.services.event_schema import _parse_version
        self.assertEqual(_parse_version("v2"), (2, 0))


if __name__ == "__main__":
    unittest.main()
