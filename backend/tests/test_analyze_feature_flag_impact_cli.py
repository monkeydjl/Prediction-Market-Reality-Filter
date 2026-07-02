"""Tests for analyze_feature_flag_impact CLI --set parsing + diff mode (LATER #1)."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


class TestParseKv(unittest.TestCase):
    def test_bool_literal_true(self):
        import analyze_feature_flag_impact as afi
        key, val = afi.parse_kv("GUARDRAILS_ENABLED=true")
        self.assertEqual(key, "GUARDRAILS_ENABLED")
        self.assertIsInstance(val, bool)
        self.assertTrue(val)

    def test_bool_literal_false(self):
        import analyze_feature_flag_impact as afi
        _, val = afi.parse_kv("GUARDRAILS_ENABLED=false")
        self.assertFalse(val)

    def test_int_coercion(self):
        import analyze_feature_flag_impact as afi
        _, val = afi.parse_kv("MARKET_MAX_SPREAD_PCT=15")
        self.assertEqual(val, 15)
        self.assertIsInstance(val, int)

    def test_float_coercion(self):
        import analyze_feature_flag_impact as afi
        _, val = afi.parse_kv("DECISION_ACT_EDGE=8.0")
        self.assertEqual(val, 8.0)
        self.assertIsInstance(val, float)

    def test_str_fallback(self):
        import analyze_feature_flag_impact as afi
        _, val = afi.parse_kv("OFFICIAL_SOURCE_NAME=UTC")
        self.assertEqual(val, "UTC")

    def test_rejects_unknown_setting(self):
        import analyze_feature_flag_impact as afi
        with self.assertRaises(SystemExit) as cm:
            afi.parse_kv("NONEXISTENT_FIELD=1")
        self.assertEqual(cm.exception.code, 2)

    def test_rejects_sensitive_field(self):
        import analyze_feature_flag_impact as afi
        with self.assertRaises(SystemExit) as cm:
            afi.parse_kv("OPENAI_API_KEY=sk-xxx")
        self.assertEqual(cm.exception.code, 2)
        # Error message printed to stderr should mention the policy
        # (parse_kv prints to stderr before exiting)


class TestIllegalCombos(unittest.TestCase):
    def _run_main(self, argv):
        import analyze_feature_flag_impact as afi
        orig_stderr = sys.stderr
        try:
            sys.stderr = io.StringIO()
            rc = afi.main(argv)
            return rc, sys.stderr.getvalue()
        finally:
            sys.stderr = orig_stderr

    def test_per_phase_plus_diff_report_exits_2(self):
        rc, _ = self._run_main(["--per-phase", "--diff-report"])
        self.assertEqual(rc, 2)

    def test_json_plus_diff_json_exits_2(self):
        rc, _ = self._run_main(["--json", "a.json", "--diff-json", "b.json"])
        self.assertEqual(rc, 2)

    def test_set_a_without_diff_report_exits_2(self):
        rc, _ = self._run_main(["--set-a", "MARKET_MAX_SPREAD_PCT=15"])
        self.assertEqual(rc, 2)

    def test_diff_json_plus_diff_report_path_exits_2(self):
        rc, _ = self._run_main(["--diff-json", "a.json", "--diff-report-path", "b.txt"])
        self.assertEqual(rc, 2)


class TestDefaultCompare(unittest.TestCase):
    def test_no_compare_defaults_to_current_current(self):
        """Without --compare, A and B both default to 'current' preset."""
        import analyze_feature_flag_impact as afi
        # Mock _load_records to return empty so main exits early
        with patch.object(afi, "_load_records", return_value=[]):
            orig_stdout = sys.stdout
            try:
                sys.stdout = io.StringIO()
                rc = afi.main(["--diff-report"])
            finally:
                sys.stdout = orig_stdout
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
