"""Unit tests for ReplayRunner.replay_record."""
import copy
import unittest


def _make_synthetic_record() -> dict:
    """A minimal record with the LLM-era fields replay needs."""
    return {
        "event_id": "test-1",
        "event_title": "Will X happen?",
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
            "rationale": "市场定价 50.0%，估计 62.0%。",
            "calibration_status": "uncalibrated_provisional",
        },
        "evidence_breakdown": [],
        "source": {"type": "prediction_market", "platform": "polymarket"},
        "market_quote": {"spread_pct": 1.0, "liquidity": 5000.0, "volume": 1000.0},
        "sentiment_profile": {"summary": "neutral", "articles": []},
        "probability": {"baseline": 50.0, "estimated": 62.0, "change": 12.0},
    }


class TestReplayRecordBasic(unittest.TestCase):
    def test_does_not_mutate_input(self):
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        record = _make_synthetic_record()
        snapshot = copy.deepcopy(record)
        replay_record(record, ReplayConfig.preset_all_off())
        self.assertEqual(record, snapshot, "input record must not be mutated")

    def test_preserves_legacy_analysis(self):
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        record = _make_synthetic_record()
        replayed = replay_record(record, ReplayConfig.preset_all_off())
        self.assertEqual(
            replayed["legacy_analysis"]["ai_probability"],
            record["legacy_analysis"]["ai_probability"],
        )

    def test_all_off_strips_overlays(self):
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig
        record = _make_synthetic_record()
        # Pre-populate with overlay fields; all_off should strip them.
        record["decision_quality"] = {"stale": True}
        record["execution_quality"] = {"stale": True}
        record["final_displayed_direction"] = "YES"
        replayed = replay_record(record, ReplayConfig.preset_all_off())
        self.assertNotIn("decision_quality", replayed)
        self.assertNotIn("market_quality", replayed)
        self.assertNotIn("source_reliability", replayed)
        self.assertNotIn("llm_telemetry", replayed)
        self.assertNotIn("execution_quality", replayed)
        self.assertNotIn("final_displayed_direction", replayed)
        self.assertNotIn("final_downgrade_reason", replayed)
        self.assertNotIn("guardrail_fired", replayed)


class TestReplayRecordAllOn(unittest.TestCase):
    def test_all_on_attaches_overlays(self):
        """When all feature flags are on, replay should attach overlay
        fields. We monkeypatch settings to enable each flag because
        preset_all_on() inherits current settings (which default off
        in pytest)."""
        from unittest.mock import patch
        from app.core.config import settings
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig

        record = _make_synthetic_record()
        # Enable the overlays that the synthetic record can satisfy.
        flags = {
            "DECISION_QUALITY_ENABLED": True,
            "MARKET_QUALITY_ENABLED": True,
            "LLM_TELEMETRY_ENABLED": True,
            "GUARDRAILS_ENABLED": False,  # avoid calibration_summary IO
        }
        with patch.multiple(settings, **flags):
            replayed = replay_record(record, ReplayConfig.preset_all_on())
        self.assertIn("decision_quality", replayed)
        self.assertIn("market_quality", replayed)
        self.assertIn("llm_telemetry", replayed)
        # final_displayed_direction is set by merge_quality_overlays when at
        # least one overlay produced a direction.
        self.assertIn("final_displayed_direction", replayed)

    def test_replay_idempotent(self):
        """Calling replay_record twice with the same cfg produces equal output."""
        from unittest.mock import patch
        from app.core.config import settings
        from app.replay.runner import replay_record
        from app.replay.config import ReplayConfig

        record = _make_synthetic_record()
        flags = {"DECISION_QUALITY_ENABLED": True, "MARKET_QUALITY_ENABLED": True}
        with patch.multiple(settings, **flags):
            first = replay_record(record, ReplayConfig.preset_all_on())
            second = replay_record(record, ReplayConfig.preset_all_on())
        self.assertEqual(first, second)


class TestSimulateLlmDegraded(unittest.TestCase):
    def test_forces_degraded_mode_and_reruns_guardrail(self):
        """When guardrails are enabled and llm_degraded_blocks_act is on,
        simulate_llm_degraded should fire the rule and downgrade to WAIT."""
        from unittest.mock import patch
        from app.core.config import settings
        from app.replay.runner import replay_record, simulate_llm_degraded
        from app.replay.config import ReplayConfig

        record = _make_synthetic_record()
        # Give the record a support evidence item so build_decision_quality
        # computes consensus_level="high" (not "none") and keeps
        # displayed_direction="YES" — otherwise Rule 4 downgrades to WAIT
        # and evaluate_guardrails short-circuits on non-strong directions.
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
            # Before simulate: direction is whatever the overlays produced.
            simulate_llm_degraded(replayed)
        self.assertTrue(replayed["llm_telemetry"]["degraded_mode"])
        self.assertEqual(
            replayed["llm_telemetry"]["analysis_quality"],
            "deterministic_fallback",
        )
        # llm_degraded_blocks_act rule should fire and downgrade to WAIT.
        self.assertIn("llm_degraded_blocks_act", replayed.get("guardrail_fired", []))
        self.assertEqual(replayed.get("final_displayed_direction"), "WAIT")

    def test_noop_when_llm_telemetry_absent(self):
        """If llm_telemetry wasn't built (flag off), simulate is a no-op."""
        from app.replay.runner import replay_record, simulate_llm_degraded
        from app.replay.config import ReplayConfig
        record = _make_synthetic_record()
        replayed = replay_record(record, ReplayConfig.preset_all_off())
        # Should not raise.
        simulate_llm_degraded(replayed)
        self.assertNotIn("llm_telemetry", replayed)


class TestReplayConfigPresets(unittest.TestCase):
    """Regression tests for preset field values. Locks down the
    guardrails_only / guardrails_baseline / llm_degraded preset shapes so
    future edits can't silently drop a rule or prerequisite."""

    def test_preset_llm_degraded_disables_rule4_and_execution_quality(self):
        """preset_llm_degraded must disable execution_quality_enabled and
        guardrail_market_not_executable_blocks_act so rule 4 can't fire
        before simulate_llm_degraded runs and short-circuit rule 1 on a
        non-strong direction."""
        from app.replay.config import ReplayConfig
        cfg = ReplayConfig.preset_llm_degraded()
        self.assertFalse(cfg.execution_quality_enabled)
        self.assertFalse(cfg.guardrail_market_not_executable_blocks_act)
        # Rule 1 must be on; rules 2/3/4 must be off
        self.assertTrue(cfg.guardrail_llm_degraded_blocks_act)
        self.assertFalse(cfg.guardrail_uncalibrated_category_blocks_act)
        self.assertFalse(cfg.guardrail_high_conflict_blocks_act)
        # Prerequisites for rule 1
        self.assertTrue(cfg.decision_quality_enabled)
        self.assertTrue(cfg.llm_telemetry_enabled)
        self.assertTrue(cfg.guardrails_enabled)

    def test_preset_guardrails_only_enables_all_4_rules_and_prerequisites(self):
        """preset_guardrails_only must enable all 4 guardrail rules AND
        their prerequisites (DQ for final_displayed_direction, llm_telemetry
        for rule 1, execution_quality for rule 4)."""
        from app.replay.config import ReplayConfig
        cfg = ReplayConfig.preset_guardrails_only()
        # All 4 rules on
        self.assertTrue(cfg.guardrail_llm_degraded_blocks_act)
        self.assertTrue(cfg.guardrail_uncalibrated_category_blocks_act)
        self.assertTrue(cfg.guardrail_high_conflict_blocks_act)
        self.assertTrue(cfg.guardrail_market_not_executable_blocks_act)
        # Prerequisites
        self.assertTrue(cfg.guardrails_enabled)
        self.assertTrue(cfg.decision_quality_enabled)
        self.assertTrue(cfg.llm_telemetry_enabled)
        self.assertTrue(cfg.execution_quality_enabled)

    def test_preset_guardrails_baseline_matches_guardrails_only_minus_guardrails(self):
        """guardrails_baseline must have the same prerequisites as
        guardrails_only but with guardrails OFF, so the per-phase CLI's
        marginal comparison isolates guardrails' impact."""
        from app.replay.config import ReplayConfig
        base = ReplayConfig.preset_guardrails_baseline()
        on = ReplayConfig.preset_guardrails_only()
        # Same prerequisites
        self.assertEqual(base.decision_quality_enabled, on.decision_quality_enabled)
        self.assertEqual(base.llm_telemetry_enabled, on.llm_telemetry_enabled)
        self.assertEqual(base.execution_quality_enabled, on.execution_quality_enabled)
        # Guardrails off in baseline, on in guardrails_only
        self.assertFalse(base.guardrails_enabled)
        self.assertTrue(on.guardrails_enabled)
        # Rule flags are None in baseline (irrelevant when guardrails off)


if __name__ == "__main__":
    unittest.main()
