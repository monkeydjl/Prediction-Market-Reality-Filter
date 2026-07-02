"""Tests for check_quality_alerts CLI (LATER #3)."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


class TestCheckQualityAlertsCli(unittest.TestCase):
    def _run_main(self, argv: list[str]) -> tuple[int, str, str]:
        """Run main() with captured stdout/stderr. Returns (rc, stdout, stderr)."""
        import check_quality_alerts as cqa
        orig_stdout, orig_stderr = sys.stdout, sys.stderr
        try:
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            rc = cqa.main(argv)
            return rc, sys.stdout.getvalue(), sys.stderr.getvalue()
        finally:
            sys.stdout, sys.stderr = orig_stdout, orig_stderr

    def test_cli_empty_store_exit_0(self):
        """Empty store → exit 0, output includes '0 alerts'."""
        with patch.object(__import__("check_quality_alerts", fromlist=["_collect_entries"]),
                          "_collect_entries", return_value=[]):
            rc, stdout, _ = self._run_main([])
        self.assertEqual(rc, 0)
        self.assertIn("0 alerts", stdout)

    def test_cli_json_output_shape(self):
        """--json → JSON with alerts + alert_count."""
        with patch.object(__import__("check_quality_alerts", fromlist=["_collect_entries"]),
                          "_collect_entries", return_value=[]):
            rc, stdout, _ = self._run_main(["--json"])
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertIn("alerts", data)
        self.assertIn("alert_count", data)

    def test_cli_no_emoji_in_output(self):
        """Text output must not contain emoji characters."""
        with patch.object(__import__("check_quality_alerts", fromlist=["_collect_entries"]),
                          "_collect_entries", return_value=[]):
            rc, stdout, _ = self._run_main([])
        # Check for common emoji ranges (simplified: no chars in U+1F000-U+1FAFF, U+2600-U+27BF)
        for ch in stdout:
            cp = ord(ch)
            self.assertFalse(
                0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF,
                f"Output contains emoji-like char U+{cp:04X}: {ch!r}",
            )

    def test_cli_include_insufficient_samples(self):
        """--include-insufficient-samples → output includes [INSUFFICIENT]."""
        fake_insufficient = [
            {"dimension": "by_source_type", "slice": "sports_event",
             "n": 2, "min_samples": 10},
        ]
        with patch.object(__import__("check_quality_alerts", fromlist=["_collect_entries"]),
                          "_collect_entries", return_value=[]), \
             patch("app.services.quality_alert_service.collect_insufficient_samples",
                   return_value=fake_insufficient):
            rc, stdout, _ = self._run_main(["--include-insufficient-samples"])
        self.assertEqual(rc, 0)
        self.assertIn("[INSUFFICIENT]", stdout)
        self.assertIn("by_source_type", stdout)
        self.assertIn("sports_event", stdout)

    def test_cli_text_output_contains_alerts_section(self):
        """Text output has a Config line and Summary line."""
        with patch.object(__import__("check_quality_alerts", fromlist=["_collect_entries"]),
                          "_collect_entries", return_value=[]):
            rc, stdout, _ = self._run_main([])
        self.assertIn("Config:", stdout)
        self.assertIn("Summary:", stdout)

    def test_cli_text_output_renders_non_empty_alerts(self):
        """When alerts are non-empty, text output contains [HIGH] and alert codes."""
        fake_alert = {
            "code": "direction_accuracy_low",
            "severity": "high",
            "scope": "overview",
            "dimension": None,
            "slice": None,
            "metric": "direction_accuracy",
            "value": 0.45,
            "threshold": 0.50,
            "n": 42,
        }
        with patch.object(__import__("check_quality_alerts", fromlist=["_collect_entries"]),
                          "_collect_entries", return_value=[]), \
             patch("app.services.quality_alert_service.evaluate_quality_alerts",
                   return_value=[fake_alert]):
            rc, stdout, _ = self._run_main([])
        self.assertEqual(rc, 0)
        self.assertIn("[HIGH]", stdout)
        self.assertIn("direction_accuracy_low", stdout)
        self.assertIn("Summary:", stdout)


if __name__ == "__main__":
    unittest.main()
