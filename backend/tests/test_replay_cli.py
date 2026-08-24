"""CLI-level regression tests for replay_decision_pipeline.

Covers the 3 P1 bug fixes:
- P1-1: _enrich_with_outcome filters status in ("scored", "observed"),
  NOT "resolved" (prediction_store writes scored/observed at resolve time).
- P1-2: default compare is ("all_off", "current") so direction_matrix
  reads raw->with_overlays (YES->WAIT = overlays downgraded).
- P1-3: when compare includes "llm_degraded", run_replay calls
  simulate_llm_degraded after replay_record so degraded_mode=True.

Q2 additions: ``_load_records`` and ``main`` had no tests at all, which is how
the positional sampler and the missing argument validation survived. See
TestLoadRecords / TestMainArgumentValidation / TestMainThreadsProvenance.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _synthetic_record(event_id: str = "cli-1") -> dict:
    """Minimal record with a support evidence item so build_decision_quality
    produces consensus_level="high" and keeps displayed_direction="YES"
    (otherwise guardrail short-circuits on non-strong directions)."""
    return {
        "event_id": event_id,
        "legacy_analysis": {
            "ai_probability": 62.0,
            "market_probability": 50.0,
            "signal": "WATCHLIST",
            "signal_direction": "LONG",
            "signal_strength": "MEDIUM",
            "evidence_strength": 0.7,
            "evidence_conflict_score": 0.2,
            "risk_flags": [],
            "analysis_quality": "llm",
        },
        "actionable_recommendation": {
            "direction": "YES",
            "confidence": "medium",
            "suggested_allocation_pct": 2.0,
            "edge": 12.0,
            "risk_level": "medium",
            "rationale": "...",
            "calibration_status": "uncalibrated_provisional",
        },
        "evidence_breakdown": [
            {
                "direction": "support",
                "source": "test",
                "title": "test evidence",
                "strength": 0.8,
                "credibility": 0.8,
                "rationale_zh": "",
            }
        ],
        "source": {"type": "prediction_market", "platform": "polymarket"},
        "market_quote": {"spread_pct": 1.0, "liquidity": 5000.0, "volume": 1000.0},
        "sentiment_profile": {"summary": "neutral", "articles": []},
        "probability": {"baseline": 50.0, "estimated": 62.0, "change": 12.0},
    }


class TestEnrichWithOutcomeStatusFilter(unittest.TestCase):
    """P1-1 regression: status filter must accept scored + observed."""

    def test_loads_scored_predictions(self):
        from scripts.replay_decision_pipeline import _enrich_with_outcome
        records = [{"event_id": "e1"}]
        fake_preds = [
            {"event_id": "e1", "status": "scored", "brier_score": 0.16,
             "actual_outcome": 100.0, "direction_correct": 1},
            {"event_id": "e2", "status": "open", "brier_score": None,
             "actual_outcome": None, "direction_correct": None},
        ]
        with patch("app.memory.prediction_store.list_recent", return_value=fake_preds):
            result = _enrich_with_outcome(records)
        self.assertEqual(result[0]["brier_score"], 0.16)
        self.assertEqual(result[0]["actual_outcome"], 100.0)

    def test_loads_observed_predictions(self):
        from scripts.replay_decision_pipeline import _enrich_with_outcome
        records = [{"event_id": "e1"}]
        fake_preds = [
            {"event_id": "e1", "status": "observed", "brier_score": 0.20,
             "actual_outcome": 0.0, "direction_correct": 0},
        ]
        with patch("app.memory.prediction_store.list_recent", return_value=fake_preds):
            result = _enrich_with_outcome(records)
        self.assertEqual(result[0]["brier_score"], 0.20)

    def test_skips_resolved_status(self):
        """prediction_store never writes status='resolved'; if a row somehow
        had it, it should be skipped (only scored/observed are resolved)."""
        from scripts.replay_decision_pipeline import _enrich_with_outcome
        records = [{"event_id": "e1"}]
        fake_preds = [
            {"event_id": "e1", "status": "resolved", "brier_score": 0.99,
             "actual_outcome": 99.0, "direction_correct": 1},
        ]
        with patch("app.memory.prediction_store.list_recent", return_value=fake_preds):
            result = _enrich_with_outcome(records)
        # No enrichment — "resolved" is not a valid post-resolution status.
        self.assertNotIn("brier_score", result[0])


class TestDefaultCompareDirection(unittest.TestCase):
    """P1-2 regression: default compare is all_off -> current (raw -> overlays)."""

    def test_default_compare_reads_all_off_then_current(self):
        """When compare=None, direction_matrix should populate as
        raw->with_overlays. With a YES record whose overlays downgrade to
        WAIT (via decision_quality Rule 4 on empty evidence — but our
        fixture has evidence so it stays YES unless an overlay downgrades).
        We verify the orientation by checking that the all_off side never
        has final_displayed_direction set (it only has the raw rec)."""
        from scripts.replay_decision_pipeline import run_replay
        from pathlib import Path
        import tempfile
        record = _synthetic_record()
        tmp = Path(tempfile.mkdtemp())
        with patch("scripts.replay_decision_pipeline._enrich_with_outcome",
                   side_effect=lambda recs: recs):
            run_replay([record], skip_marginal=True, output_dir=tmp)
        import json
        metrics = json.loads((tmp / "metrics.json").read_text(encoding="utf-8"))
        # total > 0 proves the all_off baseline contributed a direction
        # (via the actionable_recommendation fallback). Without the fix,
        # total would be 0 because all_off strips final_displayed_direction.
        self.assertGreater(metrics["total"], 0)
        # Every direction_matrix key should be "X->Y" where X is the raw
        # direction (YES, since our record's actionable_recommendation is YES)
        # and Y is the overlay-applied direction.
        for key in metrics["direction_matrix"]:
            orig, replay = key.split("->")
            self.assertEqual(orig, "YES",
                             f"all_off baseline should be raw YES, got {orig}")

    def test_cases_jsonl_uses_effective_direction_not_null(self):
        """P3 regression: cases.jsonl must write effective direction (with
        actionable_recommendation fallback) for direction_a, not the raw
        final_displayed_direction which is null under the all_off baseline.
        Without this fix, per-case traceability breaks even though aggregate
        metrics report the correct direction_matrix."""
        from scripts.replay_decision_pipeline import run_replay
        from pathlib import Path
        import json
        import tempfile
        record = _synthetic_record()
        tmp = Path(tempfile.mkdtemp())
        with patch("scripts.replay_decision_pipeline._enrich_with_outcome",
                   side_effect=lambda recs: recs):
            run_replay([record], skip_marginal=True, output_dir=tmp)
        cases = []
        with (tmp / "cases.jsonl").open(encoding="utf-8") as f:
            for line in f:
                cases.append(json.loads(line))
        self.assertEqual(len(cases), 1)
        # direction_a is the all_off side — final_displayed_direction is None
        # there, but _effective_direction falls back to
        # actionable_recommendation.direction (YES). Must not be null.
        self.assertEqual(cases[0]["direction_a"], "YES",
                         "cases.jsonl direction_a must use _effective_direction, "
                         "not the null final_displayed_direction under all_off")


class TestLlmDegradedTriggersSimulate(unittest.TestCase):
    """P1-3 regression: --compare current llm_degraded must call
    simulate_llm_degraded so degraded_mode=True and the guardrail fires."""

    def test_simulate_llm_degraded_called_for_llm_degraded_side(self):
        """When cfg_b is llm_degraded, run_replay must call
        simulate_llm_degraded on replayed_b. We spy on the imported
        reference inside the CLI module."""
        from scripts.replay_decision_pipeline import run_replay
        from pathlib import Path
        import tempfile
        record = _synthetic_record()
        tmp = Path(tempfile.mkdtemp())
        with patch("scripts.replay_decision_pipeline._enrich_with_outcome",
                   side_effect=lambda recs: recs):
            with patch("scripts.replay_decision_pipeline.simulate_llm_degraded") as mock_sim:
                run_replay([record], compare=("current", "llm_degraded"),
                           skip_marginal=True, output_dir=tmp)
        self.assertEqual(mock_sim.call_count, 1)
        # The argument should be the replayed_b dict (has llm_telemetry).
        args, kwargs = mock_sim.call_args
        replayed_b = args[0]
        self.assertIsInstance(replayed_b, dict)
        self.assertIn("llm_telemetry", replayed_b)

    def test_simulate_llm_degraded_not_called_for_non_degraded(self):
        """When neither config is llm_degraded, simulate_llm_degraded
        must not be called."""
        from scripts.replay_decision_pipeline import run_replay
        from pathlib import Path
        import tempfile
        record = _synthetic_record()
        tmp = Path(tempfile.mkdtemp())
        with patch("scripts.replay_decision_pipeline._enrich_with_outcome",
                   side_effect=lambda recs: recs):
            with patch("scripts.replay_decision_pipeline.simulate_llm_degraded") as mock_sim:
                run_replay([record], compare=("all_off", "current"),
                           skip_marginal=True, output_dir=tmp)
        self.assertEqual(mock_sim.call_count, 0)

    def test_llm_degraded_end_to_end_sets_degraded_mode(self):
        """Full end-to-end: --compare current llm_degraded produces a
        replayed_b with llm_telemetry.degraded_mode=True and the
        llm_degraded_blocks_act guardrail fired."""
        from scripts.replay_decision_pipeline import run_replay
        from pathlib import Path
        import tempfile
        record = _synthetic_record()
        tmp = Path(tempfile.mkdtemp())
        with patch("scripts.replay_decision_pipeline._enrich_with_outcome",
                   side_effect=lambda recs: recs):
            run_replay([record], compare=("current", "llm_degraded"),
                       skip_marginal=True, output_dir=tmp)
        import json
        cases = []
        with (tmp / "cases.jsonl").open(encoding="utf-8") as f:
            for line in f:
                cases.append(json.loads(line))
        self.assertEqual(len(cases), 1)
        # direction_b is from the llm_degraded side — guardrail should have
        # downgraded it to WAIT (llm_degraded_blocks_act fires).
        self.assertEqual(cases[0]["direction_b"], "WAIT")


class TestGuardrailMarginalAttribution(unittest.TestCase):
    """P2 regression: guardrails phase must attribute contribution using
    all_on minus guardrails as base (not all_off). The all_off baseline
    leaves final_displayed_direction=None, so the guardrail no-ops
    (guardrail_service returns early on None final_direction) and the
    phase reports 0 downgrades_caused even when it truly fires under
    all_on. Fix: base = all_on_minus_guardrails, phase = all_on."""

    def test_guardrail_marginal_records_downgrade_when_fired(self):
        """When the guardrail fires under all_on (degrades YES->WAIT),
        the guardrails phase should record downgrades_caused=1. Under
        the old all_off+guardrails_on baseline, this was always 0
        because the guardrail had no direction to act on.

        Uses Rule 2 (uncalibrated_category_blocks_act), armed by stubbing a
        calibration store where some *other* category has qualified — the
        record's own category ("general") then trips the fail-closed check.
        An empty store no longer fires: the call site passes None on cold
        start so a fresh install is not blocked wholesale. Rule 1
        (llm_degraded) would require simulate_llm_degraded which
        _run_marginal_loop does not call."""
        from unittest.mock import patch
        from app.core.config import settings
        from scripts.replay_decision_pipeline import _run_marginal_loop
        from app.replay.metrics import ReplayMetrics

        record = _synthetic_record("guard-marginal-1")
        # Flags: decision_quality produces a strong YES direction for the
        # guardrail to gate; Rule 2 fires fail-closed on the stubbed store.
        flags = {
            "DECISION_QUALITY_ENABLED": True,
            "GUARDRAILS_ENABLED": True,
            "GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT": False,  # not triggered (no simulate)
            "GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT": True,  # fires fail-closed
            "GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT": False,
        }
        calibrated = {
            "n": 20,
            "segments": {"politics": {"n": 20, "qualified": True}},
        }
        with patch.multiple(settings, **flags), patch(
            "app.memory.prediction_store.calibration_summary",
            return_value=calibrated,
        ):
            m = ReplayMetrics()
            _run_marginal_loop([record], m)
        d = m.to_dict()
        # Guardrail should have downgraded YES -> WAIT under all_on.
        guard = d["phase_contributions"].get("guardrails", {})
        self.assertGreaterEqual(
            guard.get("downgrades_caused", 0), 1,
            "guardrail marginal must attribute downgrades when it fires",
        )

    def test_guardrail_marginal_not_zero_when_other_phases_active(self):
        """Sanity: guardrails phase appears in phase_contributions (not
        silently dropped) and has a non-None entry."""
        from unittest.mock import patch
        from app.core.config import settings
        from scripts.replay_decision_pipeline import _run_marginal_loop
        from app.replay.metrics import ReplayMetrics

        record = _synthetic_record("guard-marginal-2")
        flags = {
            "DECISION_QUALITY_ENABLED": True,
            "GUARDRAILS_ENABLED": True,
            "GUARDRAIL_UNCALIBRATED_CATEGORY_BLOCKS_ACT": True,
            "GUARDRAIL_LLM_DEGRADED_BLOCKS_ACT": False,
            "GUARDRAIL_HIGH_CONFLICT_BLOCKS_ACT": False,
        }
        with patch.multiple(settings, **flags):
            m = ReplayMetrics()
            _run_marginal_loop([record], m)
        d = m.to_dict()
        self.assertIn("guardrails", d["phase_contributions"])


def _entries(*event_ids: str) -> list[dict]:
    """What list_all_events returns: an {event_id, record} envelope per row."""
    return [
        {"event_id": eid, "record": _synthetic_record(eid)} for eid in event_ids
    ]


class TestLoadRecords(unittest.TestCase):
    """Q2: _load_records had zero coverage, which is how a positional sampler
    and a silent duplicate-counting path both survived in it."""

    def _load(self, entries, event_ids=None, sample_size=None, **kw):
        from scripts.replay_decision_pipeline import _load_records
        with patch("app.memory.event_store.list_all_events", return_value=entries):
            return _load_records(event_ids, sample_size, **kw)

    def test_unwraps_the_envelope(self):
        records, notes = self._load(_entries("a", "b"))
        self.assertEqual({r["event_id"] for r in records}, {"a", "b"})
        self.assertEqual(notes.population, 2)

    def test_skips_rows_whose_record_is_not_a_dict(self):
        entries = _entries("a") + [{"event_id": "b", "record": None}]
        records, _ = self._load(entries)
        self.assertEqual([r["event_id"] for r in records], ["a"])

    def test_event_ids_filter_restricts_the_load(self):
        records, notes = self._load(_entries("a", "b", "c"), event_ids=["a", "c"])
        self.assertEqual({r["event_id"] for r in records}, {"a", "c"})
        self.assertEqual(notes.missing_event_ids, [])

    def test_requested_but_absent_id_is_reported_not_swallowed(self):
        """Asking for 3 events and silently replaying 2 is a report that
        describes a different population than the operator asked for."""
        records, notes = self._load(_entries("a", "b"), event_ids=["a", "b", "ghost"])
        self.assertEqual(len(records), 2)
        self.assertEqual(notes.missing_event_ids, ["ghost"])

    def test_missing_ids_are_sorted_and_deduped(self):
        _, notes = self._load(_entries("a"), event_ids=["z", "ghost", "z"])
        self.assertEqual(notes.missing_event_ids, ["ghost", "z"])

    def test_blank_requested_ids_are_ignored(self):
        records, notes = self._load(_entries("a"), event_ids=["a", ""])
        self.assertEqual(len(records), 1)
        self.assertEqual(notes.missing_event_ids, [])

    def test_duplicate_event_id_is_kept_once_and_reported(self):
        """A duplicated id made add_pair count one event twice, inflating
        total and every rate derived from it (see count-each-occurrence-once)."""
        records, notes = self._load(_entries("a", "a", "b"))
        self.assertEqual(len(records), 2)
        self.assertEqual(notes.duplicate_event_ids, ["a"])
        self.assertEqual(notes.population, 2)

    def test_duplicate_report_lists_each_id_once(self):
        _, notes = self._load(_entries("a", "a", "a"))
        self.assertEqual(notes.duplicate_event_ids, ["a"])

    def test_first_occurrence_of_a_duplicate_wins(self):
        from scripts.replay_decision_pipeline import _load_records
        first = _synthetic_record("a")
        first["marker"] = "first"
        second = _synthetic_record("a")
        second["marker"] = "second"
        entries = [
            {"event_id": "a", "record": first},
            {"event_id": "a", "record": second},
        ]
        with patch("app.memory.event_store.list_all_events", return_value=entries):
            records, _ = _load_records(None, None)
        self.assertEqual(records[0]["marker"], "first")

    def test_record_without_event_id_is_skipped_and_counted(self):
        entries = _entries("a") + [{"event_id": "", "record": {"no_id": True}}]
        records, notes = self._load(entries)
        self.assertEqual(len(records), 1)
        self.assertEqual(notes.skipped_no_event_id, 1)

    def test_sample_size_above_population_replays_everything(self):
        records, notes = self._load(_entries("a", "b"), sample_size=10)
        self.assertEqual(len(records), 2)
        self.assertEqual(notes.population, 2)

    def test_sample_is_stable_across_calls(self):
        entries = _entries(*[f"e{i:03d}" for i in range(40)])
        first, _ = self._load(entries, sample_size=6)
        second, _ = self._load(entries, sample_size=6)
        self.assertEqual(
            [r["event_id"] for r in first], [r["event_id"] for r in second],
        )

    def test_sample_ignores_store_order(self):
        """The property the old random.sample did not have. event_store.json is
        rewritten whole, so a reordering is a routine event here."""
        ids = [f"e{i:03d}" for i in range(40)]
        forward, _ = self._load(_entries(*ids), sample_size=6)
        backward, _ = self._load(_entries(*reversed(ids)), sample_size=6)
        self.assertEqual(
            sorted(r["event_id"] for r in forward),
            sorted(r["event_id"] for r in backward),
        )

    def test_sample_seed_changes_the_subset(self):
        entries = _entries(*[f"e{i:03d}" for i in range(40)])
        a, _ = self._load(entries, sample_size=6, sample_seed="replay")
        b, _ = self._load(entries, sample_size=6, sample_seed="other")
        self.assertNotEqual(
            {r["event_id"] for r in a}, {r["event_id"] for r in b},
        )

    def test_population_counts_the_whole_store_not_the_sample(self):
        """A report that says n=6 with no population reads as "the store has 6"."""
        entries = _entries(*[f"e{i:03d}" for i in range(40)])
        records, notes = self._load(entries, sample_size=6)
        self.assertEqual(len(records), 6)
        self.assertEqual(notes.population, 40)

    def test_does_not_touch_the_process_global_rng(self):
        """The old code called random.seed(42) from a read-only diagnostic,
        changing every later random draw in the same process."""
        import random
        entries = _entries(*[f"e{i:03d}" for i in range(40)])
        random.seed(1234)
        expected = [random.random() for _ in range(3)]
        random.seed(1234)
        self._load(entries, sample_size=6)
        self.assertEqual([random.random() for _ in range(3)], expected)


class TestMainArgumentValidation(unittest.TestCase):
    """Q2: a mistyped --compare used to raise ValueError out of run_replay --
    a traceback reads as a crashed tool, and it exited 1, the same code as
    "no records to replay"."""

    def _main(self, argv: list[str]) -> int:
        import sys
        from scripts import replay_decision_pipeline as cli
        with patch.object(sys, "argv", ["replay_decision_pipeline", *argv]):
            with patch.object(cli, "_load_records") as loader:
                loader.side_effect = AssertionError(
                    "validation must reject before the store is read"
                )
                return cli.main()

    def test_unknown_compare_name_exits_2(self):
        self.assertEqual(self._main(["--compare", "all_off", "currnet"]), 2)

    def test_unknown_compare_name_in_first_position_exits_2(self):
        self.assertEqual(self._main(["--compare", "nope", "current"]), 2)

    def test_valid_compare_names_pass_validation(self):
        """Guards the guard: if _validate_args rejected everything, every test
        above would pass for the wrong reason."""
        from scripts.replay_decision_pipeline import _CONFIG_NAMES, _validate_args
        import argparse
        for name in _CONFIG_NAMES:
            args = argparse.Namespace(
                compare=[name, name], sample_size=None, sample_seed="replay",
            )
            self.assertIsNone(_validate_args(args), f"{name} should be accepted")

    def test_zero_sample_size_exits_2(self):
        self.assertEqual(self._main(["--sample-size", "0"]), 2)

    def test_negative_sample_size_exits_2(self):
        self.assertEqual(self._main(["--sample-size", "-5"]), 2)

    def test_blank_sample_seed_exits_2(self):
        self.assertEqual(self._main(["--sample-seed", "   "]), 2)

    def test_empty_store_exits_1_not_2(self):
        """1 and 2 must stay distinguishable: a wrapper script cannot tell a
        typo from an empty store if both exit the same way."""
        import sys
        from scripts import replay_decision_pipeline as cli
        from scripts.replay_decision_pipeline import LoadNotes
        with patch.object(sys, "argv", ["replay_decision_pipeline"]):
            with patch.object(cli, "_load_records", return_value=([], LoadNotes())):
                self.assertEqual(cli.main(), 1)


class TestMainThreadsProvenance(unittest.TestCase):
    """Q2: main() must hand the seed and the load notes to the report.

    A flag main() accepts but does not pass through is a seam held open by a
    test (see seam-held-open-by-a-mock): the CLI would look configurable while
    every real run used the default.
    """

    def _run_main(self, argv: list[str], entries: list[dict]):
        import sys
        from scripts import replay_decision_pipeline as cli
        tmp = Path(tempfile.mkdtemp())
        full = ["replay_decision_pipeline", "--skip-marginal",
                "--output-dir", str(tmp), *argv]
        with patch.object(sys, "argv", full):
            with patch("app.memory.event_store.list_all_events", return_value=entries):
                with patch.object(cli, "_enrich_with_outcome",
                                  side_effect=lambda recs: recs):
                    code = cli.main()
        payload = json.loads((tmp / "metrics.json").read_text(encoding="utf-8"))
        self._last_dir = tmp
        return code, payload

    def _replayed_ids(self) -> set[str]:
        """Which events the last run actually graded, from cases.jsonl."""
        with (self._last_dir / "cases.jsonl").open(encoding="utf-8") as f:
            return {json.loads(line)["event_id"] for line in f if line.strip()}

    def test_sample_seed_reaches_the_report(self):
        entries = _entries(*[f"e{i:03d}" for i in range(20)])
        code, payload = self._run_main(
            ["--sample-size", "5", "--sample-seed", "2026-w34"], entries,
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["run"]["sample"], {
            "size": 5, "seed": "2026-w34", "strategy": "sha256-rank",
        })

    def test_sample_seed_actually_selects(self):
        """Not merely echoed into the report: two seeds must grade different
        events. Without this, main() could write the seed and ignore it -- the
        report would look configurable while every run used the default.
        """
        entries = _entries(*[f"e{i:03d}" for i in range(20)])
        self._run_main(["--sample-size", "5", "--sample-seed", "aaa"], entries)
        graded_a = self._replayed_ids()
        self._run_main(["--sample-size", "5", "--sample-seed", "bbb"], entries)
        graded_b = self._replayed_ids()
        self.assertEqual(len(graded_a), 5)
        self.assertEqual(len(graded_b), 5)
        self.assertNotEqual(graded_a, graded_b)

    def test_same_seed_grades_the_same_events(self):
        entries = _entries(*[f"e{i:03d}" for i in range(20)])
        self._run_main(["--sample-size", "5", "--sample-seed", "aaa"], entries)
        first = self._replayed_ids()
        self._run_main(["--sample-size", "5", "--sample-seed", "aaa"], entries)
        self.assertEqual(first, self._replayed_ids())

    def test_sample_size_bounds_what_is_graded(self):
        entries = _entries(*[f"e{i:03d}" for i in range(20)])
        _, payload = self._run_main(["--sample-size", "5"], entries)
        self.assertEqual(len(self._replayed_ids()), 5)
        self.assertEqual(payload["run"]["records_replayed"], 5)
        self.assertEqual(payload["run"]["population"], 20)

    def test_no_sample_flag_records_no_sample(self):
        """The key is always present, so its absence never has to be guessed at."""
        code, payload = self._run_main([], _entries("a", "b"))
        self.assertEqual(code, 0)
        self.assertIn("sample", payload["run"])
        self.assertIsNone(payload["run"]["sample"])
        self.assertEqual(payload["run"]["population"], 2)

    def test_compare_pair_is_recorded(self):
        code, payload = self._run_main(
            ["--compare", "current", "all_off"], _entries("a", "b"),
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["run"]["compare"], {"a": "current", "b": "all_off"})

    def test_default_compare_pair_is_recorded(self):
        _, payload = self._run_main([], _entries("a"))
        self.assertEqual(payload["run"]["compare"], {"a": "all_off", "b": "current"})

    def test_skip_marginal_is_recorded(self):
        _, payload = self._run_main([], _entries("a"))
        self.assertFalse(payload["run"]["marginal"])

    def test_missing_and_duplicate_ids_reach_the_report(self):
        entries = _entries("a", "a", "b")
        _, payload = self._run_main(["--event-ids", "a", "b", "ghost"], entries)
        run = payload["run"]
        self.assertEqual(run["missing_event_ids"], ["ghost"])
        self.assertEqual(run["duplicate_event_ids"], ["a"])
        self.assertEqual(run["records_replayed"], 2)

    def test_run_block_carries_a_schema_version(self):
        _, payload = self._run_main([], _entries("a"))
        from app.replay.report import REPLAY_REPORT_SCHEMA_VERSION
        self.assertEqual(payload["run"]["schema_version"],
                         REPLAY_REPORT_SCHEMA_VERSION)

    def test_metrics_top_level_keys_survive_the_run_block(self):
        """run is added beside the metrics, not wrapped around them -- an
        archived report from before Q2 must stay readable the same way."""
        _, payload = self._run_main([], _entries("a", "b"))
        self.assertEqual(payload["total"], 2)
        self.assertIn("direction_matrix", payload)
        self.assertIn("brier_by_quality", payload)


if __name__ == "__main__":
    unittest.main()
