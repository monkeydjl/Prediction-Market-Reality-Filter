"""Tests for ReplayConfig.settings_overrides (LATER #1)."""
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.replay.config import ReplayConfig, apply_replay_config


class TestSettingsOverrides(unittest.TestCase):
    def test_settings_overrides_applied_and_restored(self):
        """Overrides applied inside context, restored after exit."""
        original = settings.MARKET_MAX_SPREAD_PCT
        cfg = ReplayConfig(settings_overrides={"MARKET_MAX_SPREAD_PCT": 99})
        with apply_replay_config(cfg):
            self.assertEqual(settings.MARKET_MAX_SPREAD_PCT, 99)
        self.assertEqual(settings.MARKET_MAX_SPREAD_PCT, original)

    def test_settings_overrides_exception_still_restores(self):
        """Exception inside context → finally still restores."""
        original = settings.DECISION_ACT_EDGE
        cfg = ReplayConfig(settings_overrides={"DECISION_ACT_EDGE": 99.0})
        with self.assertRaises(RuntimeError):
            with apply_replay_config(cfg):
                self.assertEqual(settings.DECISION_ACT_EDGE, 99.0)
                raise RuntimeError("boom")
        self.assertEqual(settings.DECISION_ACT_EDGE, original)

    def test_settings_overrides_none_is_noop(self):
        """settings_overrides=None does not touch settings."""
        original = settings.MARKET_MAX_SPREAD_PCT
        cfg = ReplayConfig()  # settings_overrides defaults to None
        with apply_replay_config(cfg):
            self.assertEqual(settings.MARKET_MAX_SPREAD_PCT, original)
        self.assertEqual(settings.MARKET_MAX_SPREAD_PCT, original)

    def test_settings_overrides_takes_precedence_over_bool_fields(self):
        """Same key in bool field + overrides → override wins (applied last)."""
        # GUARDRAILS_ENABLED is a bool field; also put it in overrides.
        # Bool field False, override True → final True.
        original = settings.GUARDRAILS_ENABLED
        cfg = ReplayConfig(
            guardrails_enabled=False,
            settings_overrides={"GUARDRAILS_ENABLED": True},
        )
        with apply_replay_config(cfg):
            self.assertTrue(settings.GUARDRAILS_ENABLED)
        self.assertEqual(settings.GUARDRAILS_ENABLED, original)


if __name__ == "__main__":
    unittest.main()
