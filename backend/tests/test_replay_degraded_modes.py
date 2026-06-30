"""Spec §4.2 degraded-mode integration tests.

Validates that partial failures still produce safe recommendations:
- all phases degraded still produces a recommendation
- market_quality disabled for non-prediction-market sources
- source_reliability disabled when no evidence_breakdown
- partial degradation does not block the pipeline
- llm_degraded triggers guardrail block
"""
import unittest
from unittest.mock import patch


def _record(source_type: str = "prediction_market", with_evidence: bool = True) -> dict:
    rec = {
        "event_id": "degraded-1",
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
        "actionable_recommendation": {
            "direction": "YES",
            "confidence": "medium",
            "suggested_allocation_pct": 2.0,
            "edge": 12.0,
            "risk_level": "medium",
            "rationale": "...",
            "calibration_status": "uncalibrated_provisional",
        },
        "evidence_breakdown": [
            {"direction": "support", "strength": 0.7, "credibility": 0.8, "rationale": "x", "url": "https://a.com", "domain": "a.com"}
        ] if with_evidence else [],
        "source": {"type": source_type, "platform": "test"},
        "market_quote": {"spread_pct": 1.0, "liquidity": 5000.0, "volume": 1000.0},
        "sentiment_profile": {"summary": "neutral", "articles": []},
        "probability": {"baseline": 50.0, "estimated": 62.0, "change": 12.0},
    }
    return rec


class TestDegradedModes(unittest.TestCase):
    def test_all_phases_degraded_still_produces_recommendation(self):
        """When all overlays fail, the record still has
        actionable_recommendation (set before overlays run)."""
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        record = _record()
        replayed = replay_record(record, ReplayConfig.preset_all_off())
        self.assertIn("actionable_recommendation", replayed)
        self.assertEqual(replayed["actionable_recommendation"]["direction"], "YES")

    def test_market_quality_disabled_when_source_not_prediction_market(self):
        """open_web / sports_event sources must not get market_quality."""
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        from unittest.mock import patch
        from app.core.config import settings
        record = _record(source_type="open_web")
        with patch.multiple(settings, MARKET_QUALITY_ENABLED=True):
            replayed = replay_record(record, ReplayConfig.preset_all_on())
        # market_quality should be absent for open_web source.
        self.assertNotIn("market_quality", replayed)

    def test_source_reliability_disabled_when_no_evidence_breakdown(self):
        """Events with empty evidence_breakdown must not get source_reliability."""
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        from unittest.mock import patch
        from app.core.config import settings
        record = _record(with_evidence=False)
        with patch.multiple(settings, SOURCE_RELIABILITY_ENABLED=True):
            replayed = replay_record(record, ReplayConfig.preset_all_on())
        self.assertNotIn("source_reliability", replayed)

    def test_partial_degradation_does_not_block_pipeline(self):
        """If one overlay throws, the others still run and the record is
        returned (not crashed). We simulate by mocking build_decision_quality
        to raise — the except handler in _build_all_overlays should emit an
        error block and the pipeline should continue (market_quality still runs)."""
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        from unittest.mock import patch
        from app.core.config import settings
        record = _record()
        flags = {"DECISION_QUALITY_ENABLED": True, "MARKET_QUALITY_ENABLED": True}
        with patch.multiple(settings, **flags):
            # Mock build_decision_quality at its source module so the local
            # import inside _build_all_overlays picks up the raising version.
            with patch("app.services.decision_quality_service.build_decision_quality",
                       side_effect=RuntimeError("simulated failure")):
                replayed = replay_record(record, ReplayConfig.preset_all_on())
        # Should not raise; decision_quality should have an error block.
        self.assertEqual(replayed["decision_quality"].get("error"), "build_failed")
        # Other overlays still ran (market_quality is present for prediction_market source).
        self.assertIn("market_quality", replayed)

    def test_llm_degraded_triggers_guardrail_block(self):
        """When llm_telemetry.degraded_mode=True and guardrails are on with
        llm_degraded_blocks_act=True, final direction should downgrade to WAIT."""
        from app.replay.runner import replay_record, simulate_llm_degraded
        from app.replay.config import ReplayConfig
        from unittest.mock import patch
        from app.core.config import settings
        record = _record()
        flags = {
            "DECISION_QUALITY_ENABLED": True,  # produce a non-None final_displayed_direction for guardrail to act on
            "LLM_TELEMETRY_ENABLED": True,
            "GUARDRAILS_ENABLED": True,
            "GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT": True,
            # Disable Rule 2 (uncalibrated_category) and Rule 3 (high_conflict)
            # so the only guardrail that can fire is llm_degraded_blocks_act.
            # Rule 2 otherwise fires fail-closed when calibration_summary
            # returns empty segments (the test-environment default), which
            # would downgrade direction to WAIT during replay and cause
            # simulate_llm_degraded to short-circuit on a non-strong direction.
            "GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT": False,
            "GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT": False,
        }
        with patch.multiple(settings, **flags):
            replayed = replay_record(record, ReplayConfig.preset_all_on())
            simulate_llm_degraded(replayed)
        self.assertIn("llm_degraded_blocks_act", replayed.get("guardrail_fired", []))
        self.assertEqual(replayed["final_displayed_direction"], "WAIT")


if __name__ == "__main__":
    unittest.main()
