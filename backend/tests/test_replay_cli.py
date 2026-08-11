"""CLI-level regression tests for replay_decision_pipeline.

Covers the 3 P1 bug fixes:
- P1-1: _enrich_with_outcome filters status in ("scored", "observed"),
  NOT "resolved" (prediction_store writes scored/observed at resolve time).
- P1-2: default compare is ("all_off", "current") so direction_matrix
  reads raw->with_overlays (YES->WAIT = overlays downgraded).
- P1-3: when compare includes "llm_degraded", run_replay calls
  simulate_llm_degraded after replay_record so degraded_mode=True.
"""
import unittest
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


if __name__ == "__main__":
    unittest.main()
