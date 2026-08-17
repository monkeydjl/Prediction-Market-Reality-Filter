import os
import unittest
from unittest.mock import patch

from scripts import verify_local_stack


class ChecksCoverageTests(unittest.TestCase):
    def test_covers_event_loop_and_discovery_status(self):
        paths = [path for path, _method, _flag in verify_local_stack.CHECKS]

        self.assertIn("/api/events/loop/status", paths)
        self.assertIn("/api/events/discover/status", paths)

    def test_every_check_is_read_only(self):
        methods = {method for _path, method, _flag in verify_local_stack.CHECKS}

        self.assertEqual(methods, {"GET"})


class WriteAuthSummaryTests(unittest.TestCase):
    def test_reports_configured_key_without_printing_it(self):
        with patch.dict(
            os.environ,
            {"API_WRITE_KEY": "super-secret-value", "ALLOW_OPEN_WRITES": "false"},
            clear=False,
        ):
            summary = verify_local_stack.write_auth_summary()

        self.assertNotIn("super-secret-value", summary)
        self.assertIn("configured=True", summary)
        self.assertIn("ALLOW_OPEN_WRITES=False", summary)

    def test_reports_missing_key_and_open_writes(self):
        with patch.dict(
            os.environ,
            {"API_WRITE_KEY": "   ", "ALLOW_OPEN_WRITES": "true"},
            clear=False,
        ):
            summary = verify_local_stack.write_auth_summary()

        self.assertIn("configured=False", summary)
        self.assertIn("ALLOW_OPEN_WRITES=True", summary)


if __name__ == "__main__":
    unittest.main()
