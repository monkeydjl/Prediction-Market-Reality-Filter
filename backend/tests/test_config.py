import importlib
import os
import unittest
from unittest.mock import patch

from app.core import config
from app.core.config import settings


class ConfigHelperTests(unittest.TestCase):
    def test_env_bool_accepts_explicit_truthy_values(self):
        truthy = {"BOOL_UNDER_TEST": "yes"}
        falsy = {"BOOL_UNDER_TEST": "no"}
        with patch.dict(os.environ, truthy, clear=False):
            self.assertTrue(config._env_bool("BOOL_UNDER_TEST"))
        with patch.dict(os.environ, falsy, clear=False):
            self.assertFalse(config._env_bool("BOOL_UNDER_TEST"))

    def test_env_csv_strips_empty_items(self):
        with patch.dict(os.environ, {"CSV_UNDER_TEST": "GET, POST,, OPTIONS "}, clear=False):
            self.assertEqual(
                config._env_csv("CSV_UNDER_TEST", ""),
                ["GET", "POST", "OPTIONS"],
            )


class OddsApiConfigTests(unittest.TestCase):
    def test_odds_api_base_url_can_be_configured_from_env(self):
        from app.core import config as config_module
        from app.services import odds_api_service

        custom_base_url = "https://odds-proxy.example/v4"
        with patch.dict(
            os.environ,
            {"ODDS_API_BASE_URL": custom_base_url},
            clear=False,
        ):
            reloaded_config = importlib.reload(config_module)
            try:
                reloaded_odds_service = importlib.reload(odds_api_service)

                self.assertEqual(
                    reloaded_config.settings.ODDS_API_BASE_URL,
                    custom_base_url,
                )
                self.assertEqual(reloaded_odds_service.ODDS_API_BASE, custom_base_url)
            finally:
                importlib.reload(config_module)
                importlib.reload(odds_api_service)


class ConfigDefaultTests(unittest.TestCase):
    def test_scheduler_misfire_default_is_operationally_useful(self):
        self.assertGreaterEqual(settings.SCHEDULER_MISFIRE_GRACE_SECONDS, 60 * 60)

    def test_sec_user_agent_is_declared(self):
        self.assertTrue(settings.SEC_USER_AGENT.strip())

    def test_review_queue_auto_resolve_confidence_default(self):
        self.assertEqual(settings.REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE, 0.95)


if __name__ == "__main__":
    unittest.main()
