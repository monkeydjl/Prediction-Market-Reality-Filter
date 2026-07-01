"""Unit tests for diagnose_event_quality CLI."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure backend/ is on sys.path (same pattern as other test files)
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# scripts/ is not a package — add it to sys.path so we can import
# diagnose_event_quality as a top-level module (same pattern as
# test_analyze_feature_flag_impact.py importing analyze_feature_flag_impact).
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _sample_record() -> dict:
    """A minimal record with all 6 overlays populated for testing.
    Bypasses EventRecord validation — used for extract/render/replay tests
    that don't touch event_store."""
    return {
        "event_id": "test-1",
        "event_title": "Will X happen?",
        "actionable_recommendation": {
            "direction": "YES",
            "confidence": "medium",
            "calibration_status": "uncalibrated_provisional",
            "edge": 12.0,
        },
        "probability": {
            "baseline": 50.0,
            "estimated": 62.0,
            "change": 12.0,
        },
        "decision_quality": {
            "evidence_strength": 0.82,
            "conflict_score": 0.15,
            "downgrade_reason": None,
            "displayed_direction": "YES",
        },
        "market_quality": {
            "degraded": False,
            "degrade_reason": None,
            "wide_spread_flag": False,
            "low_liquidity_flag": False,
        },
        "source_reliability": {
            "overall_score": 0.78,
            "source_count": 4,
            "domain_diversity": 3,
        },
        "llm_telemetry": {
            "degraded_mode": False,
            "total_tokens": 1247,
            "estimated_token_cost": 0.0018,
            "analysis_quality": "llm",
        },
        "execution_quality": {
            "executable": True,
            "estimated_slippage_pct": 0.3,
            "stale_price_flag": False,
            "max_safe_position_size": 1000.0,
        },
        "guardrail_fired": [],
        "final_displayed_direction": "YES",
    }


class TestLoadEvent(unittest.TestCase):
    def test_load_event_found(self):
        """When event exists in store, _load_event returns the store entry."""
        from app.memory import event_store
        from app.core.config import settings
        from diagnose_event_quality import _load_event
        # Use a minimal valid record that passes EventRecord validation.
        # event_store.save_event runs normalize_event_record + model_validate,
        # so the record must have required fields. Use the same shape as
        # test_operational_readiness.py: patch EVENT_STORE_FILE + save_event.
        # Minimal valid record that passes EventRecord validation.
        # Required fields mirror tests/test_event_store.py::_make_record; the
        # brief's note that this record "must have required fields" is
        # satisfied by including event_summary, probability.direction,
        # credibility, impact, risk, evidence, value_score, intelligence_report.
        # Extra fields (market_quote, actionable_recommendation, etc.) are
        # preserved by EventRecord's extra="allow" config.
        record = {
            "event_id": "test-1",
            "event_title": "Will X happen?",
            "event_summary": "summary",
            "source": {"type": "prediction_market", "platform": "manifold"},
            "market_quote": {"spread_pct": 1.0, "liquidity": 5000.0, "volume": 1000.0},
            "probability": {
                "baseline": 50.0,
                "estimated": 62.0,
                "change": 12.0,
                "direction": "rising",
            },
            "credibility": {
                "score": 60,
                "level": "MEDIUM",
                "confidence": 0.6,
                "news_quality": 0.5,
                "evidence_strength": 0.4,
                "source_count": 3,
            },
            "impact": {"score": 55, "level": "MEDIUM", "drivers": ["strong_evidence"]},
            "risk": {"level": "LOW", "flags": []},
            "evidence": {
                "direction": "supports",
                "strength": 0.4,
                "conflict": 0.1,
                "freshness": 0.7,
                "resolution_relevance": 0.5,
            },
            "value_score": 50,
            "intelligence_report": {
                "headline": "h",
                "why_it_matters": "w",
                "probability_assessment": "p",
                "recommended_action": "a",
            },
            "actionable_recommendation": {
                "direction": "YES",
                "confidence": "medium",
                "suggested_allocation_pct": 2.0,
                "edge": 12.0,
                "risk_level": "medium",
                "rationale": "test",
                "calibration_status": "uncalibrated_provisional",
            },
            "legacy_analysis": {
                "ai_probability": 62.0,
                "market_probability": 50.0,
                "signal": "WATCHLIST",
                "signal_direction": "LONG",
                "signal_strength": "MEDIUM",
                "evidence_strength": 0.7,
                "evidence_conflict_score": 0.2,
                "risk_flags": [],
                "analysis_quality": "llm",
            },
            "evidence_breakdown": [],
            "sentiment_profile": {"summary": "neutral", "articles": []},
        }
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "event_store.json"
            with patch.object(settings, "EVENT_STORE_FILE", str(store_path)):
                event_store.save_event(record)
                entry = _load_event("test-1")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["event_id"], "test-1")
            self.assertEqual(entry["record"]["event_title"], "Will X happen?")

    def test_load_event_not_found(self):
        """When event does not exist, _load_event returns None."""
        from app.core.config import settings
        from diagnose_event_quality import _load_event
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "event_store.json"
            with patch.object(settings, "EVENT_STORE_FILE", str(store_path)):
                # Empty store (file doesn't exist yet — get_event returns None)
                entry = _load_event("nonexistent")
            self.assertIsNone(entry)


class TestExtractPhaseData(unittest.TestCase):
    def test_extract_phase_data_full(self):
        """All 6 overlays present → all phases populated."""
        from diagnose_event_quality import _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        self.assertEqual(data["event_id"], "test-1")
        self.assertEqual(data["event_title"], "Will X happen?")
        self.assertIn("decision_quality", data["phases"])
        self.assertEqual(data["phases"]["decision_quality"]["evidence_strength"], 0.82)
        self.assertIn("market_quality", data["phases"])
        self.assertIn("source_reliability", data["phases"])
        self.assertIn("llm_telemetry", data["phases"])
        self.assertIn("execution_quality", data["phases"])
        # max_safe_position_size renamed to max_safe_size per §8.1
        self.assertEqual(data["phases"]["execution_quality"]["max_safe_size"], 1000.0)
        self.assertNotIn("max_safe_position_size", data["phases"]["execution_quality"])
        # guardrail + final direction
        self.assertEqual(data["guardrails"]["fired_rules"], [])
        self.assertEqual(data["final_direction"], "YES")

    def test_extract_phase_data_missing_overlays(self):
        """When some overlays are absent, they show as None."""
        from diagnose_event_quality import _extract_phase_data
        record = _sample_record()
        del record["llm_telemetry"]
        del record["execution_quality"]
        data = _extract_phase_data(record)
        self.assertIsNone(data["phases"]["llm_telemetry"])
        self.assertIsNone(data["phases"]["execution_quality"])
        # Other phases still populated
        self.assertIsNotNone(data["phases"]["decision_quality"])

    def test_extract_phase_data_no_mutation(self):
        """_extract_phase_data must not mutate the input record."""
        from diagnose_event_quality import _extract_phase_data
        record = _sample_record()
        snapshot = copy.deepcopy(record)
        _extract_phase_data(record)
        self.assertEqual(record, snapshot)


if __name__ == "__main__":
    unittest.main()
