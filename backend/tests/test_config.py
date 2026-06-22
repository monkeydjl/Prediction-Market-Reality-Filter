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


class ConfigDefaultTests(unittest.TestCase):
    def test_scheduler_misfire_default_is_operationally_useful(self):
        self.assertGreaterEqual(settings.SCHEDULER_MISFIRE_GRACE_SECONDS, 60 * 60)

    def test_sec_user_agent_is_declared(self):
        self.assertTrue(settings.SEC_USER_AGENT.strip())


if __name__ == "__main__":
    unittest.main()
