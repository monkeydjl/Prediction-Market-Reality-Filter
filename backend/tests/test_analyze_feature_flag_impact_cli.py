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

        def fake_run_diff(records, ca, cb, shared, a_only, b_only, dr, drp, dj,
                          sample=None):
            captured["shared"] = shared
            captured["a_only"] = a_only
            captured["b_only"] = b_only
            captured["sample"] = sample
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


class TestStableSampling(unittest.TestCase):
    """Q2: this script held a byte-identical copy of the replay CLI's defective
    sampler -- ``random.seed(42)`` + ``random.sample``, which picks positions
    and reseeds the process-global RNG from inside a read-only diagnostic.

    Both copies had to move together: two sibling tools that sample differently
    cannot have their reports compared, which is the whole point of running
    them on the same store.
    """

    @staticmethod
    def _entries(ids):
        return [{"event_id": eid, "record": {"event_id": eid}} for eid in ids]

    def _load(self, ids, **kw):
        import analyze_feature_flag_impact as afi
        with patch("app.memory.event_store.list_all_events",
                   return_value=self._entries(ids)):
            return [r["event_id"] for r in afi._load_records(None, **kw)]

    def test_sample_is_stable_across_calls(self):
        ids = [f"e{i:03d}" for i in range(40)]
        self.assertEqual(
            self._load(ids, sample_size=6), self._load(ids, sample_size=6),
        )

    def test_sample_ignores_store_order(self):
        ids = [f"e{i:03d}" for i in range(40)]
        self.assertEqual(
            sorted(self._load(ids, sample_size=6)),
            sorted(self._load(list(reversed(ids)), sample_size=6)),
        )

    def test_sample_seed_changes_the_subset(self):
        ids = [f"e{i:03d}" for i in range(40)]
        a = set(self._load(ids, sample_size=6, sample_seed="one"))
        b = set(self._load(ids, sample_size=6, sample_seed="two"))
        self.assertNotEqual(a, b)

    def test_sample_size_is_respected(self):
        ids = [f"e{i:03d}" for i in range(40)]
        self.assertEqual(len(self._load(ids, sample_size=6)), 6)

    def test_sample_size_above_population_loads_everything(self):
        ids = [f"e{i:03d}" for i in range(4)]
        self.assertEqual(len(self._load(ids, sample_size=99)), 4)

    def test_duplicate_id_cannot_take_two_slots(self):
        ids = ["a"] * 10 + [f"e{i}" for i in range(10)]
        picked = self._load(ids, sample_size=5)
        self.assertEqual(len(picked), len(set(picked)))

    def test_does_not_touch_the_process_global_rng(self):
        import random
        ids = [f"e{i:03d}" for i in range(40)]
        random.seed(999)
        expected = [random.random() for _ in range(3)]
        random.seed(999)
        self._load(ids, sample_size=6)
        self.assertEqual([random.random() for _ in range(3)], expected)

    def test_event_ids_filter_still_works(self):
        import analyze_feature_flag_impact as afi
        with patch("app.memory.event_store.list_all_events",
                   return_value=self._entries(["a", "b", "c"])):
            got = afi._load_records(["a", "c"], None)
        self.assertEqual({r["event_id"] for r in got}, {"a", "c"})


class TestSampleArgumentValidation(unittest.TestCase):
    def _run_main(self, argv):
        import analyze_feature_flag_impact as afi
        orig_stderr = sys.stderr
        try:
            sys.stderr = io.StringIO()
            return afi.main(argv), sys.stderr.getvalue()
        finally:
            sys.stderr = orig_stderr

    def test_zero_sample_size_exits_2(self):
        rc, err = self._run_main(["--sample-size", "0"])
        self.assertEqual(rc, 2)
        self.assertIn("--sample-size", err)

    def test_negative_sample_size_exits_2(self):
        rc, _ = self._run_main(["--sample-size", "-3"])
        self.assertEqual(rc, 2)

    def test_blank_sample_seed_exits_2(self):
        rc, err = self._run_main(["--sample-seed", "  "])
        self.assertEqual(rc, 2)
        self.assertIn("--sample-seed", err)


class TestSampleProvenanceInJson(unittest.TestCase):
    """Every JSON this script writes carries the key, so its absence is never
    something an operator has to guess about."""

    @staticmethod
    def _records():
        from tests.test_sweep_event_quality import _make_resolved_record
        return [
            _make_resolved_record("evt-1", direction="YES", actual_outcome=100.0),
            _make_resolved_record("evt-2", direction="NO", actual_outcome=0.0),
        ]

    def _write(self, argv):
        import os
        import analyze_feature_flag_impact as afi
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with patch.object(afi, "_load_records", return_value=self._records()):
                orig_stdout = sys.stdout
                try:
                    sys.stdout = io.StringIO()
                    rc = afi.main([*argv, path])
                finally:
                    sys.stdout = orig_stdout
            with open(path, encoding="utf-8") as f:
                return rc, json.loads(f.read())
        finally:
            os.unlink(path)

    def test_legacy_json_records_the_sample(self):
        rc, payload = self._write(
            ["--sample-size", "2", "--sample-seed", "w34", "--json"],
        )
        self.assertEqual(rc, 0)
        self.assertEqual(payload["sample"], {
            "size": 2, "seed": "w34", "strategy": "sha256-rank",
        })

    def test_legacy_json_records_none_without_the_flag(self):
        rc, payload = self._write(["--json"])
        self.assertEqual(rc, 0)
        self.assertIn("sample", payload)
        self.assertIsNone(payload["sample"])

    def test_per_phase_json_records_the_sample(self):
        rc, payload = self._write(
            ["--per-phase", "--sample-size", "2", "--sample-seed", "w34", "--json"],
        )
        self.assertEqual(rc, 0)
        self.assertEqual(payload["sample"]["seed"], "w34")

    def test_diff_json_records_the_sample(self):
        rc, payload = self._write(
            ["--sample-size", "2", "--sample-seed", "w34", "--diff-json"],
        )
        self.assertEqual(rc, 0)
        self.assertEqual(payload["sample"]["seed"], "w34")

    def test_diff_json_records_none_without_the_flag(self):
        rc, payload = self._write(["--diff-json"])
        self.assertEqual(rc, 0)
        self.assertIn("sample", payload)
        self.assertIsNone(payload["sample"])


if __name__ == "__main__":
    unittest.main()
