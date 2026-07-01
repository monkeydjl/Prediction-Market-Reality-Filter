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


class TestRenderText(unittest.TestCase):
    def test_render_text_includes_all_phases(self):
        """Text output contains all 6 phase headers + guardrail + final direction."""
        from diagnose_event_quality import _render_text, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        text = _render_text(data, replay_result=None)
        self.assertIn("Phase 1: Decision Quality", text)
        self.assertIn("Phase 2: Market Quality", text)
        self.assertIn("Phase 3: Prediction Calibration", text)
        self.assertIn("Phase 4: Source Reliability", text)
        self.assertIn("Phase 5: LLM Telemetry", text)
        self.assertIn("Phase 6: Execution Quality", text)
        self.assertIn("Guardrails", text)
        self.assertIn("Final Direction", text)

    def test_render_text_includes_event_header(self):
        """Text output starts with Event: <id> (<title>)."""
        from diagnose_event_quality import _render_text, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        text = _render_text(data, replay_result=None)
        self.assertIn("Event: test-1", text)
        self.assertIn("Will X happen?", text)

    def test_render_text_missing_overlay_shows_skipped(self):
        """Missing overlay shows 'Skipped (overlay not built)'."""
        from diagnose_event_quality import _render_text, _extract_phase_data
        record = _sample_record()
        del record["llm_telemetry"]
        data = _extract_phase_data(record)
        text = _render_text(data, replay_result=None)
        self.assertIn("Skipped (overlay not built)", text)

    def test_render_text_uses_max_safe_size_label(self):
        """Text output uses 'max_safe_size' label, NOT 'max_safe_position_size'."""
        from diagnose_event_quality import _render_text, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        text = _render_text(data, replay_result=None)
        self.assertIn("max_safe_size", text)
        # The banned term should not appear as a CLI-generated label
        self.assertNotIn("max_safe_position_size", text)


class TestRenderJson(unittest.TestCase):
    def test_render_json_valid_structure(self):
        """JSON output parses, has event_id/phases/guardrails/final_direction keys."""
        from diagnose_event_quality import _render_json, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        json_str = _render_json(data, replay_result=None)
        parsed = json.loads(json_str)
        self.assertEqual(parsed["event_id"], "test-1")
        self.assertEqual(parsed["event_title"], "Will X happen?")
        self.assertIn("phases", parsed)
        self.assertIn("decision_quality", parsed["phases"])
        self.assertIn("execution_quality", parsed["phases"])
        self.assertEqual(
            parsed["phases"]["execution_quality"]["max_safe_size"], 1000.0
        )
        self.assertNotIn(
            "max_safe_position_size", parsed["phases"]["execution_quality"]
        )
        self.assertIn("guardrails", parsed)
        self.assertIn("fired_rules", parsed["guardrails"])
        self.assertEqual(parsed["final_direction"], "YES")
        # replay_comparison is null when no replay run
        self.assertIsNone(parsed["replay_comparison"])

    def test_render_json_replay_null_without_flag(self):
        """replay_comparison is null when replay_result is None."""
        from diagnose_event_quality import _render_json, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        json_str = _render_json(data, replay_result=None)
        parsed = json.loads(json_str)
        self.assertIsNone(parsed["replay_comparison"])


class TestReplayComparison(unittest.TestCase):
    def test_replay_comparison_no_change(self):
        """When all_on and all_off produce same direction, delta=no_change.
        Uses a record with no overlays — both configs fall back to
        actionable_recommendation.direction = "YES"."""
        from app.core.config import settings
        from diagnose_event_quality import _run_replay_comparison
        record = _sample_record()
        # Strip all overlays so all_off and all_on both start fresh.
        # With DECISION_QUALITY_ENABLED=False and GUARDRAILS_ENABLED=False
        # (test env default), all_on inherits these → no overlay runs →
        # direction falls back to actionable_recommendation.direction.
        for key in ("decision_quality", "market_quality", "source_reliability",
                    "llm_telemetry", "execution_quality",
                    "final_displayed_direction", "final_downgrade_reason",
                    "guardrail_fired"):
            record.pop(key, None)
        flags = {
            "DECISION_QUALITY_ENABLED": False,
            "MARKET_QUALITY_ENABLED": False,
            "SOURCE_RELIABILITY_ENABLED": False,
            "LLM_TELEMETRY_ENABLED": False,
            "GUARDRAILS_ENABLED": False,
            "EXECUTION_QUALITY_ENABLED": False,
        }
        with patch.multiple(settings, **flags):
            result = _run_replay_comparison(record)
        self.assertEqual(result["all_off_direction"], "YES")
        self.assertEqual(result["all_on_direction"], "YES")
        self.assertEqual(result["delta"], "no_change")

    def test_replay_comparison_changed(self):
        """When all_on and all_off produce different directions, delta=changed.
        Enables DQ + guardrails (uncalibrated_category rule) so all_on
        downgrades YES → WAIT, while all_off leaves direction as YES."""
        from app.core.config import settings
        from diagnose_event_quality import _run_replay_comparison
        record = _sample_record()
        # Give the record support evidence so DQ keeps YES (not downgraded
        # to WAIT for empty evidence) — same pattern as test_replay_runner.py
        record["evidence_breakdown"] = [
            {
                "direction": "support",
                "source": "test",
                "title": "test evidence",
                "strength": 0.8,
                "credibility": 0.8,
                "rationale_zh": "",
            }
        ]
        # Also need legacy_analysis for DQ to compute consensus_level
        record["legacy_analysis"] = {
            "ai_probability": 62.0,
            "market_probability": 50.0,
            "signal": "WATCHLIST",
            "signal_direction": "LONG",
            "signal_strength": "MEDIUM",
            "evidence_strength": 0.7,
            "evidence_conflict_score": 0.2,
            "risk_flags": [],
            "analysis_quality": "llm",
        }
        record["market_quote"] = {
            "spread_pct": 1.0, "liquidity": 5000.0, "volume": 1000.0
        }
        record["sentiment_profile"] = {"summary": "neutral", "articles": []}
        record["source"] = {"type": "prediction_market", "platform": "manifold"}
        # Enable guardrails + DQ + uncalibrated_category rule so all_on
        # downgrades YES → WAIT (empty calibration store in test env)
        flags = {
            "DECISION_QUALITY_ENABLED": True,
            "GUARDRAILS_ENABLED": True,
            "GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT": True,
            "GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT": False,
            "GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT": False,
            "GUARDRAIL_MARKET_NOT_EXECUTABLE_BLOCKS_ACT": False,
            "EXECUTION_QUALITY_ENABLED": False,
        }
        with patch.multiple(settings, **flags):
            result = _run_replay_comparison(record)
        # all_off leaves direction as YES (from actionable_recommendation)
        self.assertEqual(result["all_off_direction"], "YES")
        # all_on downgrades YES → WAIT (uncalibrated category fires)
        self.assertEqual(result["all_on_direction"], "WAIT")
        self.assertEqual(result["delta"], "changed")

    def test_replay_no_mutation_of_input(self):
        """_run_replay_comparison must not mutate the input record."""
        from app.core.config import settings
        from diagnose_event_quality import _run_replay_comparison
        record = _sample_record()
        snapshot = copy.deepcopy(record)
        flags = {"DECISION_QUALITY_ENABLED": False, "GUARDRAILS_ENABLED": False}
        with patch.multiple(settings, **flags):
            _run_replay_comparison(record)
        self.assertEqual(record, snapshot)


class TestExitCodes(unittest.TestCase):
    def test_exit_code_not_found(self):
        """Missing event → exit 1, error to stderr."""
        from app.core.config import settings
        from diagnose_event_quality import main
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "event_store.json"
            import io as _io
            orig_stderr = sys.stderr
            try:
                with patch.object(settings, "EVENT_STORE_FILE", str(store_path)):
                    sys.stderr = _io.StringIO()
                    rc = main(["nonexistent"])
                self.assertEqual(rc, 1)
                self.assertIn("not found", sys.stderr.getvalue())
            finally:
                sys.stderr = orig_stderr

    def test_exit_code_success(self):
        """Found event → exit 0."""
        from app.core.config import settings
        from app.memory import event_store
        from diagnose_event_quality import main
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
            import io as _io
            orig_stdout = sys.stdout
            orig_stderr = sys.stderr
            try:
                with patch.object(settings, "EVENT_STORE_FILE", str(store_path)):
                    event_store.save_event(record)
                    sys.stdout = _io.StringIO()
                    sys.stderr = _io.StringIO()
                    rc = main(["test-1", "--json"])
                self.assertEqual(rc, 0)
            finally:
                sys.stdout = orig_stdout
                sys.stderr = orig_stderr


class TestVocabularyLock(unittest.TestCase):
    def test_vocabulary_lock_cli_labels(self):
        """CLI-generated labels/field names (fixed strings in render
        functions) contain no banned terms. Raw external data values
        (event_title, downgrade_reason, fired_rules) are NOT scanned —
        they are user-authored and may contain trading terms (§8.1).
        _sample_record uses clean values (no trading terms), so a full
        output scan is equivalent to scanning only CLI labels here."""
        import re
        from diagnose_event_quality import _render_text, _render_json, _extract_phase_data
        record = _sample_record()
        data = _extract_phase_data(record)
        text = _render_text(data, replay_result=None)
        json_str = _render_json(data, replay_result=None)

        # Banned terms as whole words (case-insensitive)
        banned = ("long", "short", "buy", "sell", "position", "kelly", "order")
        for term in banned:
            pattern = r"\b" + re.escape(term) + r"\b"
            self.assertFalse(
                re.search(pattern, text, re.IGNORECASE),
                f"banned term '{term}' found in text output",
            )
            self.assertFalse(
                re.search(pattern, json_str, re.IGNORECASE),
                f"banned term '{term}' found in JSON output",
            )


if __name__ == "__main__":
    unittest.main()
