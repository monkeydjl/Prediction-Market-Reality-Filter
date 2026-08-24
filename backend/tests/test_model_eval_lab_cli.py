"""CLI tests for model_eval_lab."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from scripts.model_eval_lab import main


def _record(event_id="evt-001", model="gpt-4o-mini", **overrides):
    rec = {
        "event_id": event_id,
        "source": {"type": "prediction_market"},
        "llm_telemetry": {
            "model": model,
            "analysis_quality": "llm",
            "degraded_mode": False,
            "estimated_token_cost": 0.02,
        },
        "actionable_recommendation": {"direction": "YES", "edge": 12.0},
        "outcome": {"status": "resolved", "actual_outcome": 100.0},
        "calibration": {"brier_score": 0.16, "estimated_probability": 72.0},
        "source_reliability": {"overall_score": 0.65},
        "guardrail_fired": [],
    }
    rec.update(overrides)
    return rec


def _entry(event_id="evt-001", **overrides):
    return {"event_id": event_id, "record": _record(event_id, **overrides)}


def _extract_items(*entries):
    """Extract model metrics from entry dicts for mocking _collect_entries."""
    from app.services.model_eval_lab_service import extract_model_metrics
    return [extract_model_metrics(e["record"]) for e in entries]


class TestCliExitCodes(unittest.TestCase):
    def test_no_args_returns_0(self):
        with patch("scripts.model_eval_lab._collect_entries", return_value=([], [])):
            rc = main([])
        self.assertEqual(rc, 0)

    def test_sample_negative_returns_2(self):
        rc = main(["--sample", "-1"])
        self.assertEqual(rc, 2)

    def test_min_samples_negative_returns_2(self):
        with patch("scripts.model_eval_lab._collect_entries", return_value=([], [])):
            rc = main(["--min-samples", "-1"])
        self.assertEqual(rc, 2)

    def test_event_ids_empty_after_parse_returns_2(self):
        rc = main(["--event-ids", ","])
        self.assertEqual(rc, 2)

    def test_sample_zero_legal_empty_report(self):
        with patch("scripts.model_eval_lab._collect_entries", return_value=([], [])):
            rc = main(["--sample", "0"])
        self.assertEqual(rc, 0)


class TestCliOutput(unittest.TestCase):
    def test_json_mode_outputs_pure_json(self):
        items = _extract_items(_entry())
        with patch("scripts.model_eval_lab._collect_entries", return_value=(items, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = main(["--json"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        # Must be pure JSON, no [INFO] prefix
        self.assertFalse(out.startswith("[INFO]"))
        data = json.loads(out)
        self.assertIn("overview", data)
        self.assertIn("by_model", data)

    def test_text_mode_has_overview_header(self):
        items = _extract_items(_entry())
        with patch("scripts.model_eval_lab._collect_entries", return_value=(items, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = main([])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("== Overview", out)
        self.assertIn("[INFO]", out)

    def test_ascii_only_no_emoji_or_box_chars(self):
        items = _extract_items(_entry())
        with patch("scripts.model_eval_lab._collect_entries", return_value=(items, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                main([])
        out = buf.getvalue()
        # No box drawing chars or emoji
        for bad in ("─", "═", "│", "📊", "⚠️"):
            self.assertNotIn(bad, out, f"found forbidden char {bad!r}")

    def test_insufficient_flag_in_output(self):
        items = _extract_items(_entry(model="rare"))
        with patch("scripts.model_eval_lab._collect_entries", return_value=(items, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                main(["--min-samples", "5"])
        out = buf.getvalue()
        self.assertIn("[INSUFFICIENT]", out)

    def test_cost_na_when_no_cost_data(self):
        items = _extract_items(_entry(llm_telemetry={
            "model": "x", "analysis_quality": "llm",
            "degraded_mode": False, "estimated_token_cost": None,
        }))
        with patch("scripts.model_eval_lab._collect_entries", return_value=(items, [])):
            from io import StringIO
            buf = StringIO()
            with patch("sys.stdout", buf):
                main(["--json"])
        out = buf.getvalue()
        data = json.loads(out)
        self.assertIsNone(data["overview"]["cost_avg"])


class TestCliCollectEntries(unittest.TestCase):
    def test_event_ids_filter_first_then_sample(self):
        from scripts.model_eval_lab import _collect_entries
        entries = [
            _entry("a"), _entry("b"), _entry("c"),
        ]
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            items, errors = _collect_entries(sample=1, event_ids=["a", "b"])
        # Filtered to a+b, then sampled 1
        self.assertEqual(len(items), 1)
        self.assertIn(items[0]["event_id"], {"a", "b"})

    def test_event_ids_none_returns_all(self):
        from scripts.model_eval_lab import _collect_entries
        entries = [_entry("a"), _entry("b")]
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            items, errors = _collect_entries(sample=None, event_ids=None)
        self.assertEqual(len(items), 2)

    def test_report_errors_for_non_dict_record(self):
        from scripts.model_eval_lab import _collect_entries
        # An entry whose record is not a dict
        entries = [{"event_id": "bad", "record": "not a dict"}]
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            items, errors = _collect_entries(sample=None, event_ids=None)
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("not a dict", errors[0]["error"])


class TestCliSampleStability(unittest.TestCase):
    """--sample used to call random.Random(42).sample, which picks positions.

    A store rewrite reorders event_store.json, so the same --sample N graded a
    different set of events under one metric name. These pin the fix at the
    CLI seam, not just in the service.
    """

    def _sample(self, entries, n=10):
        from scripts.model_eval_lab import _collect_entries
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            items, _ = _collect_entries(sample=n, event_ids=None)
        return [i["event_id"] for i in items]

    def test_reordering_the_store_does_not_change_the_sample(self):
        entries = [_entry(f"evt-{i:03d}") for i in range(40)]
        forward = self._sample(entries)
        backward = self._sample(list(reversed(entries)))
        self.assertEqual(len(forward), 10)
        self.assertEqual(set(forward), set(backward))

    def test_a_grown_store_displaces_at_most_one_per_new_event(self):
        entries = [_entry(f"evt-{i:03d}") for i in range(40)]
        before = set(self._sample(entries))
        grown = entries + [_entry(f"new-{i:03d}") for i in range(5)]
        after = set(self._sample(grown))
        self.assertLessEqual(len(before - after), 5)
        self.assertEqual(len(after), 10)

    def test_sample_at_or_above_population_keeps_everything(self):
        entries = [_entry(f"evt-{i:03d}") for i in range(4)]
        self.assertEqual(len(self._sample(entries, n=4)), 4)
        self.assertEqual(len(self._sample(entries, n=99)), 4)

    def test_seed_selects_a_different_sample(self):
        from scripts.model_eval_lab import _collect_entries
        entries = [_entry(f"evt-{i:03d}") for i in range(40)]
        with patch("app.memory.event_store.list_resolved_events", return_value=entries):
            a, _ = _collect_entries(sample=10, event_ids=None, sample_seed="one")
            b, _ = _collect_entries(sample=10, event_ids=None, sample_seed="two")
        self.assertNotEqual(
            {i["event_id"] for i in a}, {i["event_id"] for i in b},
        )

    def test_help_no_longer_claims_reproducible_seed_42(self):
        """The string that made the false promise."""
        from scripts.model_eval_lab import _build_parser
        help_text = _build_parser().format_help()
        self.assertNotIn("seed=42", help_text)
        self.assertIn("order-independent", help_text)

    def test_main_threads_sample_seed_to_the_sampler(self):
        """A seed only a test can reach is not a feature.

        ``_collect_entries`` takes ``sample_seed``, so without ``--sample-seed``
        the only caller able to vary it would be the test right above -- the
        seam would be held open from ``tests/`` while production was stuck on
        one seed. This asserts main() actually passes the flag through.
        """
        entries = [_entry(f"evt-{i:03d}") for i in range(40)]
        seen: list[str] = []
        real = None

        from scripts import model_eval_lab as mod
        real = mod._collect_entries

        def spy(sample, event_ids, *, sample_seed):
            seen.append(sample_seed)
            return real(sample, event_ids, sample_seed=sample_seed)

        with patch("app.memory.event_store.list_resolved_events", return_value=entries), \
                patch.object(mod, "_collect_entries", spy), \
                patch("sys.stdout", StringIO()):
            self.assertEqual(mod.main(["--sample", "10"]), 0)
            self.assertEqual(mod.main(["--sample", "10", "--sample-seed", "other"]), 0)

        self.assertEqual(seen, ["model-eval", "other"])

    def test_empty_sample_seed_is_rejected(self):
        rc = main(["--sample", "5", "--sample-seed", "  "])
        self.assertEqual(rc, 2)


class TestCliEvalSet(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.entries = [_entry(f"evt-{i:03d}") for i in range(12)]
        self.items = _extract_items(*self.entries)

    def _run(self, argv, items=None, errors=None):
        buf = StringIO()
        with patch(
            "scripts.model_eval_lab._collect_entries",
            return_value=(self.items if items is None else items, errors or []),
        ), patch("sys.stdout", buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_write_then_read_round_trip(self):
        path = self.tmp / "baseline.json"
        rc, out = self._run(["--write-eval-set", str(path), "--size", "5"])
        self.assertEqual(rc, 0)
        self.assertIn("[OK] wrote", out)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "baseline")
        self.assertEqual(len(manifest["event_ids"]), 5)

        rc, out = self._run(["--eval-set", str(path)])
        self.assertEqual(rc, 0)
        self.assertIn("== Eval Set ==", out)
        self.assertIn("matched=5/5", out)
        self.assertIn("complete=True", out)

    def test_write_does_not_print_a_report(self):
        path = self.tmp / "baseline.json"
        _, out = self._run(["--write-eval-set", str(path), "--size", "3"])
        self.assertNotIn("== Overview", out)

    def test_write_reports_unreadable_records_it_skipped(self):
        """The population a set was minted from is not the store size when some
        records could not be extracted."""
        path = self.tmp / "baseline.json"
        errors = [{"event_id": "bad", "error": "boom"}]
        _, out = self._run(
            ["--write-eval-set", str(path), "--size", "3"], errors=errors,
        )
        self.assertIn("unreadable_skipped=1", out)

    def test_refuses_to_overwrite_without_force(self):
        path = self.tmp / "baseline.json"
        self.assertEqual(self._run(["--write-eval-set", str(path)])[0], 0)
        first = path.read_text(encoding="utf-8")
        rc, _ = self._run(["--write-eval-set", str(path)])
        self.assertEqual(rc, 2)
        self.assertEqual(path.read_text(encoding="utf-8"), first)

    def test_force_replaces_and_a_revision_bump_is_visible(self):
        path = self.tmp / "baseline.json"
        self._run(["--write-eval-set", str(path), "--size", "5"])
        before = json.loads(path.read_text(encoding="utf-8"))
        rc, _ = self._run([
            "--write-eval-set", str(path), "--size", "5",
            "--set-revision", "2", "--force",
        ])
        self.assertEqual(rc, 0)
        after = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(after["revision"], "2")
        self.assertEqual(after["event_ids"], before["event_ids"])
        self.assertNotEqual(after["digest"], before["digest"])

    def test_set_name_and_seed_default_to_the_path_and_name(self):
        path = self.tmp / "nested" / "my-set.json"
        self._run(["--write-eval-set", str(path), "--size", "3"])
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "my-set")
        self.assertEqual(manifest["selection"]["seed"], "my-set")

    def test_explicit_seed_and_name_are_recorded(self):
        path = self.tmp / "baseline.json"
        self._run([
            "--write-eval-set", str(path), "--size", "3",
            "--set-name", "golden", "--seed", "s-2026",
        ])
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "golden")
        self.assertEqual(manifest["selection"]["seed"], "s-2026")

    def test_pinned_run_grades_only_the_pinned_events(self):
        path = self.tmp / "baseline.json"
        self._run(["--write-eval-set", str(path), "--size", "4"])
        rc, out = self._run(["--eval-set", str(path), "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["overview"]["n"], 4)
        self.assertEqual(data["eval_set"]["matched"], 4)
        self.assertEqual(data["eval_set"]["ignored"], 8)

    def test_missing_pinned_event_is_named_not_hidden(self):
        path = self.tmp / "baseline.json"
        self._run(["--write-eval-set", str(path), "--size", "4"])
        pinned = json.loads(path.read_text(encoding="utf-8"))["event_ids"]
        thinner = [i for i in self.items if i["event_id"] != pinned[0]]
        rc, out = self._run(["--eval-set", str(path)], items=thinner)
        self.assertEqual(rc, 0)
        self.assertIn("[WARN] missing from store (1)", out)
        self.assertIn(pinned[0], out)
        self.assertIn("complete=False", out)

    def test_regraded_event_is_named_and_still_graded(self):
        path = self.tmp / "baseline.json"
        self._run(["--write-eval-set", str(path), "--size", "4"])
        pinned = json.loads(path.read_text(encoding="utf-8"))["event_ids"]
        regraded = [
            {**i, "actual_outcome": 0.0} if i["event_id"] == pinned[0] else i
            for i in self.items
        ]
        rc, out = self._run(["--eval-set", str(path), "--json"], items=regraded)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["eval_set"]["drifted_event_ids"], [pinned[0]])
        self.assertEqual(data["eval_set"]["matched"], 4)   # kept, not dropped
        self.assertEqual(data["overview"]["n"], 4)

    def test_unreadable_file_returns_2(self):
        rc, _ = self._run(["--eval-set", str(self.tmp / "nope.json")])
        self.assertEqual(rc, 2)

    def test_malformed_json_returns_2(self):
        path = self.tmp / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        rc, _ = self._run(["--eval-set", str(path)])
        self.assertEqual(rc, 2)

    def test_hand_edited_manifest_is_rejected_with_every_problem(self):
        path = self.tmp / "baseline.json"
        self._run(["--write-eval-set", str(path), "--size", "5"])
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["event_ids"] = manifest["event_ids"][:2]
        path.write_text(json.dumps(manifest), encoding="utf-8")

        buf = StringIO()
        with patch(
            "scripts.model_eval_lab._collect_entries", return_value=(self.items, []),
        ), patch("sys.stderr", buf):
            rc = main(["--eval-set", str(path)])
        self.assertEqual(rc, 2)
        err = buf.getvalue()
        self.assertIn("digest mismatch", err)
        self.assertIn("fingerprints do not cover", err)

    def test_rejected_flag_combinations(self):
        path = self.tmp / "baseline.json"
        self._run(["--write-eval-set", str(path), "--size", "3"])
        for argv in (
            ["--eval-set", str(path), "--write-eval-set", str(path)],
            ["--eval-set", str(path), "--sample", "2"],
            ["--eval-set", str(path), "--event-ids", "evt-000"],
        ):
            with self.subTest(argv=argv):
                rc, _ = self._run(argv)
                self.assertEqual(rc, 2)

    def test_size_zero_returns_2(self):
        rc, _ = self._run(["--write-eval-set", str(self.tmp / "x.json"), "--size", "0"])
        self.assertEqual(rc, 2)

    def test_write_over_an_empty_store_returns_2(self):
        rc, _ = self._run(
            ["--write-eval-set", str(self.tmp / "x.json")], items=[],
        )
        self.assertEqual(rc, 2)
        self.assertFalse((self.tmp / "x.json").exists())


class TestCliReleaseGate(unittest.TestCase):
    """--gate turns thresholds into an exit code. Off unless the flag is given."""

    def _items(self, n=25, **overrides):
        entries = [_entry(f"evt-{i:03d}", **overrides) for i in range(n)]
        return _extract_items(*entries)

    def _run(self, argv, items):
        buf = StringIO()
        with patch(
            "scripts.model_eval_lab._collect_entries", return_value=(items, []),
        ), patch("sys.stdout", buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def _passing_items(self, n=24):
        """Half confident-YES resolved YES, half confident-NO resolved NO:
        ECE ~5, direction accuracy 1.0, Brier 0.05."""
        entries = []
        for i in range(n):
            if i % 2 == 0:
                entries.append(_entry(
                    f"evt-{i:03d}",
                    outcome={"status": "resolved", "actual_outcome": 100.0},
                    calibration={"brier_score": 0.05, "estimated_probability": 95.0},
                    actionable_recommendation={"direction": "YES", "edge": 12.0},
                ))
            else:
                entries.append(_entry(
                    f"evt-{i:03d}",
                    outcome={"status": "resolved", "actual_outcome": 0.0},
                    calibration={"brier_score": 0.05, "estimated_probability": 5.0},
                    actionable_recommendation={"direction": "NO", "edge": 12.0},
                ))
        return _extract_items(*entries)

    def test_no_gate_flag_means_no_gate(self):
        rc, out = self._run(["--json"], self._items(n=2))
        self.assertEqual(rc, 0)
        self.assertNotIn("release_gate", json.loads(out))

    def test_a_healthy_report_passes_and_exits_0(self):
        rc, out = self._run(["--gate"], self._passing_items())
        self.assertEqual(rc, 0, out)
        self.assertIn("== Release Gate: PASS ==", out)

    def test_a_failing_gate_exits_1_and_names_what_blocked(self):
        rc, out = self._run(["--gate"], self._items(n=25))
        self.assertEqual(rc, 1)
        self.assertIn("== Release Gate: FAIL ==", out)
        self.assertIn("Blocking:", out)
        self.assertIn("ece_max", out)

    def test_an_empty_store_cannot_pass(self):
        """The whole point: no evidence is not a pass."""
        rc, out = self._run(["--gate"], [])
        self.assertEqual(rc, 1)
        self.assertIn("no measurement", out)

    def test_a_thin_metric_fails_on_its_own_denominator(self):
        """25 events, only 3 of them gradeable: overview.n passes and the
        metrics that only 3 events carry do not."""
        items = self._passing_items(n=3) + _extract_items(*[
            _entry(
                f"blank-{i:03d}",
                outcome={"status": "resolved", "actual_outcome": None},
                calibration={},
                actionable_recommendation={"direction": "NONE", "edge": 0.0},
            )
            for i in range(22)
        ])
        rc, out = self._run(["--gate", "--json"], items)
        self.assertEqual(rc, 1)
        gate = json.loads(out)["release_gate"]
        by_name = {c["name"]: c for c in gate["checks"]}
        self.assertTrue(by_name["min_samples"]["passed"])
        self.assertEqual(by_name["min_samples"]["value"], 25)
        self.assertEqual(by_name["ece_max"]["sample_count"], 3)
        self.assertFalse(by_name["ece_max"]["passed"])
        self.assertIn("carry this metric", by_name["ece_max"]["reason"])

    def test_gate_block_is_stored_on_the_json_report(self):
        rc, out = self._run(["--gate", "--json"], self._passing_items())
        self.assertEqual(rc, 0)
        gate = json.loads(out)["release_gate"]
        self.assertTrue(gate["passed"])
        self.assertIn("thresholds", gate)
        self.assertEqual(gate["failed"], [])

    def test_gate_table_shows_each_metrics_own_count(self):
        _, out = self._run(["--gate"], self._passing_items())
        self.assertIn("metric_n", out)

    def test_gate_with_a_pinned_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            items = self._passing_items()
            buf = StringIO()
            with patch(
                "scripts.model_eval_lab._collect_entries", return_value=(items, []),
            ), patch("sys.stdout", buf):
                self.assertEqual(
                    main(["--write-eval-set", str(path), "--size", "24"]), 0,
                )
            rc, out = self._run(["--eval-set", str(path), "--gate", "--json"], items)
            self.assertEqual(rc, 0, out)
            gate = json.loads(out)["release_gate"]
            names = [c["name"] for c in gate["checks"]]
            self.assertIn("eval_set_complete", names)
            self.assertTrue(gate["passed"])

    def test_report_errors_block_the_gate(self):
        buf = StringIO()
        with patch(
            "scripts.model_eval_lab._collect_entries",
            return_value=(self._passing_items(), [{"event_id": "x", "error": "boom"}]),
        ), patch("sys.stdout", buf):
            rc = main(["--gate"])
        self.assertEqual(rc, 1)
        self.assertIn("report_errors_max", buf.getvalue())

    def test_ascii_only_in_gate_output(self):
        _, out = self._run(["--gate"], self._items(n=25))
        for bad in ("─", "═", "│", "📊", "⚠️", "✅", "❌"):
            self.assertNotIn(bad, out, f"found forbidden char {bad!r}")


if __name__ == "__main__":
    unittest.main()
