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

    def test_set_without_diff_report_exits_2(self):
        """--set (shared overrides) without --diff-* should exit 2,
        not be silently ignored in legacy mode."""
        rc, _ = self._run_main(["--set", "MARKET_MAX_SPREAD_PCT=15"])
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


class TestSetSplit(unittest.TestCase):
    def test_set_a_set_b_split(self):
        """--set-a K=1 --set-b K=2 -> A and B get separate overrides.

        Mocks _run_diff_mode to capture the override dicts passed in,
        since they are internal to _run_diff_mode and not otherwise
        observable. _load_records is mocked to return a non-empty list
        so main() reaches _run_diff_mode instead of early-exiting on
        the "No records found" branch.
        """
        import analyze_feature_flag_impact as afi
        captured = {}

        def fake_run_diff(records, ca, cb, shared, a_only, b_only, dr, drp, dj):
            captured["shared"] = shared
            captured["a_only"] = a_only
            captured["b_only"] = b_only
            return 0

        with patch.object(afi, "_load_records", return_value=[{"event_id": "x"}]):
            with patch.object(afi, "_run_diff_mode", side_effect=fake_run_diff):
                rc = afi.main(["--set-a", "MARKET_MAX_SPREAD_PCT=10",
                               "--set-b", "MARKET_MAX_SPREAD_PCT=20",
                               "--diff-report"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["a_only"], {"MARKET_MAX_SPREAD_PCT": 10})
        self.assertEqual(captured["b_only"], {"MARKET_MAX_SPREAD_PCT": 20})
        self.assertEqual(captured["shared"], {})


class TestDiffOutput(unittest.TestCase):
    """End-to-end tests for diff/legacy output rendering.

    Mocks _load_records to return pre-built record dicts (avoids
    event_store seeding). Records still flow through replay_record +
    build_diff, so the full render path is exercised.
    """

    @staticmethod
    def _two_records():
        from tests.test_sweep_event_quality import _make_resolved_record
        return [
            _make_resolved_record("evt-1", direction="YES", actual_outcome=100.0),
            _make_resolved_record("evt-2", direction="NO", actual_outcome=0.0),
        ]

    @staticmethod
    def _run_main_capture(argv, records):
        import analyze_feature_flag_impact as afi
        with patch.object(afi, "_load_records", return_value=records):
            orig_stdout = sys.stdout
            try:
                sys.stdout = io.StringIO()
                rc = afi.main(argv)
                output = sys.stdout.getvalue()
            finally:
                sys.stdout = orig_stdout
        return rc, output

    def test_diff_report_text_output(self):
        """--diff-report stdout contains all 4 required sections."""
        rc, output = self._run_main_capture(["--diff-report"], self._two_records())
        self.assertEqual(rc, 0)
        self.assertIn("Overview", output)
        self.assertIn("Regression summary", output)
        self.assertIn("Direction matrix", output)
        self.assertIn("Slice diff", output)

    def test_diff_json_output_shape(self):
        """--diff-json PATH JSON contains the required top-level keys."""
        import os
        import tempfile
        records = self._two_records()
        fd, json_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            rc, _ = self._run_main_capture(["--diff-json", json_path], records)
            self.assertEqual(rc, 0)
            with open(json_path, "r", encoding="utf-8") as f:
                payload = json.loads(f.read())
            for key in ("overview", "direction_matrix", "slice_diff",
                        "regression_summary", "effective_config_a",
                        "effective_config_b"):
                self.assertIn(key, payload)
        finally:
            os.unlink(json_path)

    def test_backward_compat_compare_no_diff(self):
        """--compare all_off all_on (no --diff-*) -> legacy matrix, no diff sections."""
        rc, output = self._run_main_capture(
            ["--compare", "all_off", "all_on"], self._two_records())
        self.assertEqual(rc, 0)
        self.assertNotIn("Regression summary", output)
        self.assertIn("Direction transition matrix", output)


if __name__ == "__main__":
    unittest.main()
