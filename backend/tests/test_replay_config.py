"""Unit tests for ReplayConfig + apply_replay_config."""
import unittest
from unittest.mock import patch


class TestReplayConfigPresets(unittest.TestCase):
    def test_preset_all_off_disables_everything(self):
        from app.replay.config import ReplayConfig
        cfg = ReplayConfig.preset_all_off()
        self.assertFalse(cfg.decision_quality_enabled)
        self.assertFalse(cfg.market_quality_enabled)
        self.assertFalse(cfg.source_reliability_enabled)
        self.assertFalse(cfg.prediction_calibration_enabled)
        self.assertFalse(cfg.llm_telemetry_enabled)
        self.assertFalse(cfg.guardrails_enabled)

    def test_preset_all_on_returns_all_none(self):
        from app.replay.config import ReplayConfig
        cfg = ReplayConfig.preset_all_on()
        self.assertIsNone(cfg.decision_quality_enabled)
        self.assertIsNone(cfg.market_quality_enabled)
        self.assertIsNone(cfg.guardrails_enabled)

    def test_preset_llm_degraded_enables_telemetry_and_guardrail(self):
        from app.replay.config import ReplayConfig
        cfg = ReplayConfig.preset_llm_degraded()
        self.assertTrue(cfg.llm_telemetry_enabled)
        self.assertTrue(cfg.guardrails_enabled)


class TestApplyReplayConfig(unittest.TestCase):
    def setUp(self):
        from app.core.config import settings
        self._settings = settings
        self._orig = {
            "DECISION_QUALITY_ENABLED": settings.DECISION_QUALITY_ENABLED,
            "GUARDRAILS_ENABLED": settings.GUARDRAILS_ENABLED,
        }

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(self._settings, k, v)

    def test_applies_and_restores_on_normal_exit(self):
        from app.core.config import settings
        from app.replay.config import ReplayConfig, apply_replay_config
        original = settings.DECISION_QUALITY_ENABLED
        with apply_replay_config(ReplayConfig(decision_quality_enabled=True)):
            self.assertTrue(settings.DECISION_QUALITY_ENABLED)
        self.assertEqual(settings.DECISION_QUALITY_ENABLED, original)

    def test_restores_on_exception(self):
        from app.core.config import settings
        from app.replay.config import ReplayConfig, apply_replay_config
        original = settings.GUARDRAILS_ENABLED
        try:
            with apply_replay_config(ReplayConfig(guardrails_enabled=True)):
                self.assertTrue(settings.GUARDRAILS_ENABLED)
                raise RuntimeError("simulated failure")
        except RuntimeError:
            pass
        self.assertEqual(settings.GUARDRAILS_ENABLED, original)

    def test_none_fields_leave_settings_untouched(self):
        from app.core.config import settings
        from app.replay.config import ReplayConfig, apply_replay_config
        before = settings.MARKET_QUALITY_ENABLED
        with apply_replay_config(ReplayConfig()):  # all None
            self.assertEqual(settings.MARKET_QUALITY_ENABLED, before)
        self.assertEqual(settings.MARKET_QUALITY_ENABLED, before)


if __name__ == "__main__":
    unittest.main()
