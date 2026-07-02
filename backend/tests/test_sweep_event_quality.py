"""Unit tests for sweep_event_quality CLI."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure backend/ is on sys.path (same pattern as other test files)
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# scripts/ is not a package — add it to sys.path so we can import
# sweep_event_quality as a top-level module.
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _make_resolved_record(
    event_id: str,
    direction: str = "YES",
    edge: float = 12.0,
    actual_outcome: float = 100.0,
    llm_degraded: bool = False,
    market_degraded: bool = False,
    guardrail_fired: list | None = None,
) -> dict:
    """Build a minimal record that passes EventRecord validation + has
    a resolved outcome. Mirrors the shape used by test_diagnose_event_quality
    + adds outcome."""
    return {
        "event_id": event_id,
        "event_title": f"Event {event_id}",
        "event_summary": "summary",
        "source": {"type": "prediction_market", "platform": "manifold"},
        "market_quote": {"spread_pct": 1.0, "liquidity": 5000.0, "volume": 1000.0},
        "probability": {
            "baseline": 50.0, "estimated": 62.0, "change": 12.0, "direction": "rising",
        },
        "credibility": {
            "score": 60, "level": "MEDIUM", "confidence": 0.6,
            "news_quality": 0.5, "evidence_strength": 0.4, "source_count": 3,
        },
        "impact": {"score": 55, "level": "MEDIUM", "drivers": ["strong_evidence"]},
        "risk": {"level": "LOW", "flags": []},
        "evidence": {
            "direction": "supports", "strength": 0.4, "conflict": 0.1,
            "freshness": 0.7, "resolution_relevance": 0.5,
        },
        "value_score": 50,
        "intelligence_report": {
            "headline": "h", "why_it_matters": "w",
            "probability_assessment": "p", "recommended_action": "a",
        },
        "actionable_recommendation": {
            "direction": direction, "confidence": "medium",
            "suggested_allocation_pct": 2.0, "edge": edge, "risk_level": "medium",
            "rationale": "test", "calibration_status": "uncalibrated_provisional",
        },
        "legacy_analysis": {
            "ai_probability": 62.0, "market_probability": 50.0,
            "signal": "WATCHLIST", "signal_direction": "LONG",
            "signal_strength": "MEDIUM", "evidence_strength": 0.7,
            "evidence_conflict_score": 0.2, "risk_flags": [],
            "analysis_quality": "llm",
        },
        "evidence_breakdown": [],
        "sentiment_profile": {"summary": "neutral", "articles": []},
        "outcome": {
            "status": "resolved", "actual_outcome": actual_outcome,
            "confidence": 0.9, "resolved_at": "2026-06-15T00:00:00Z",
            "source": "manual",
        },
        "llm_telemetry": {
            "degraded_mode": llm_degraded, "total_tokens": 1000,
            "estimated_token_cost": 0.001, "analysis_quality": "degraded" if llm_degraded else "llm",
        },
        "market_quality": {
            "degraded": market_degraded,
            "degrade_reason": "wide_spread" if market_degraded else None,
            "wide_spread_flag": market_degraded, "low_liquidity_flag": False,
        },
        "guardrail_fired": guardrail_fired or [],
    }


def _seed_store(records: list[dict]) -> Path:
    """Save records to a temp event_store and return the path. Caller
    must keep the TemporaryDirectory alive (returned via the patch context)."""
    from app.core.config import settings
    from app.memory import event_store
    tmp = tempfile.TemporaryDirectory()
    store_path = Path(tmp.name) / "event_store.json"
    patch_ctx = patch.object(settings, "EVENT_STORE_FILE", str(store_path))
    patch_ctx.start()
    for r in records:
        event_store.save_event(r)
    return tmp, patch_ctx


class TestSweep(unittest.TestCase):
    def setUp(self):
        self._tmps: list = []
        self._patches: list = []

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for t in self._tmps:
            t.cleanup()

    def _seed(self, records: list[dict]) -> None:
        tmp, patch_ctx = _seed_store(records)
        self._tmps.append(tmp)
        self._patches.append(patch_ctx)

    def test_sweep_empty_store_returns_zero(self):
        """Empty event_store → total_swept=0, exit 0, not an error."""
        from sweep_event_quality import main
        self._seed([])  # no records
        rc = main([])
        self.assertEqual(rc, 0)

    def test_sweep_aggregate_counts_direction_correct(self):
        """3 events: YES+YES(True), YES+NO(False), WAIT(None).
        Aggregate must show True=1, False=1, None=1."""
        from sweep_event_quality import _collect_entries, _sweep, _aggregate
        records = [
            _make_resolved_record("evt-1", direction="YES", actual_outcome=100.0),
            _make_resolved_record("evt-2", direction="YES", actual_outcome=0.0),
            _make_resolved_record("evt-3", direction="WAIT", actual_outcome=100.0),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        results = _sweep(entries)
        agg = _aggregate(results)

        self.assertEqual(agg["total_swept"], 3)
        dc = agg["direction_correct"]
        self.assertEqual(dc.get("True"), 1)
        self.assertEqual(dc.get("False"), 1)
        self.assertEqual(dc.get("None"), 1)

    def test_sweep_anomalies_lists_misjudgments(self):
        """direction_correct=False events appear in misjudgments list."""
        from sweep_event_quality import _collect_entries, _sweep, _anomalies
        records = [
            _make_resolved_record("correct-yes", direction="YES", actual_outcome=100.0),
            _make_resolved_record("wrong-yes", direction="YES", actual_outcome=0.0),
            _make_resolved_record("wrong-no", direction="NO", actual_outcome=100.0),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        results = _sweep(entries)
        anomalies = _anomalies(results)

        self.assertEqual(len(anomalies["misjudgments"]), 2)
        misjudgment_ids = {a["event_id"] for a in anomalies["misjudgments"]}
        self.assertEqual(misjudgment_ids, {"wrong-yes", "wrong-no"})

    def test_sweep_anomalies_lists_llm_degraded(self):
        """llm_telemetry.degraded_mode=True events appear in llm_degraded list."""
        from sweep_event_quality import _collect_entries, _sweep, _anomalies
        records = [
            _make_resolved_record("healthy", llm_degraded=False),
            _make_resolved_record("degraded", llm_degraded=True),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        results = _sweep(entries)
        anomalies = _anomalies(results)

        self.assertEqual(len(anomalies["llm_degraded"]), 1)
        self.assertEqual(anomalies["llm_degraded"][0]["event_id"], "degraded")

    def test_sweep_anomalies_lists_market_degraded(self):
        """market_quality.degraded=True events appear in market_degraded list."""
        from sweep_event_quality import _collect_entries, _sweep, _anomalies
        records = [
            _make_resolved_record("healthy-market", market_degraded=False),
            _make_resolved_record("bad-market", market_degraded=True),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        results = _sweep(entries)
        anomalies = _anomalies(results)

        self.assertEqual(len(anomalies["market_degraded"]), 1)
        self.assertEqual(anomalies["market_degraded"][0]["event_id"], "bad-market")
        self.assertEqual(anomalies["market_degraded"][0]["degrade_reason"], "wide_spread")

    def test_sweep_anomalies_lists_guardrail_fired(self):
        """Non-empty guardrail_fired events appear in guardrail_fired list."""
        from sweep_event_quality import _collect_entries, _sweep, _anomalies
        records = [
            _make_resolved_record("clean", guardrail_fired=[]),
            _make_resolved_record("guarded", guardrail_fired=["rule_a", "rule_b"]),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        results = _sweep(entries)
        anomalies = _anomalies(results)

        self.assertEqual(len(anomalies["guardrail_fired"]), 1)
        self.assertEqual(anomalies["guardrail_fired"][0]["event_id"], "guarded")
        self.assertEqual(
            anomalies["guardrail_fired"][0]["fired_rules"],
            ["rule_a", "rule_b"],
        )

    def test_sweep_limit_truncates_entries(self):
        """--limit N sweeps only the first N entries."""
        from sweep_event_quality import _collect_entries
        records = [_make_resolved_record(f"evt-{i}") for i in range(5)]
        self._seed(records)
        entries = _collect_entries(limit=2, sample=None)
        self.assertEqual(len(entries), 2)

    def test_sweep_sample_is_reproducible(self):
        """--sample N uses seed=42, so two runs return the same set."""
        from sweep_event_quality import _collect_entries
        records = [_make_resolved_record(f"evt-{i}") for i in range(10)]
        self._seed(records)
        set1 = {e["event_id"] for e in _collect_entries(limit=None, sample=3)}
        set2 = {e["event_id"] for e in _collect_entries(limit=None, sample=3)}
        self.assertEqual(set1, set2)
        self.assertEqual(len(set1), 3)

    def test_sweep_json_output_is_valid(self):
        """--json produces valid JSON with aggregate + anomalies keys."""
        from sweep_event_quality import main
        records = [
            _make_resolved_record("correct", direction="YES", actual_outcome=100.0),
            _make_resolved_record("wrong", direction="YES", actual_outcome=0.0),
        ]
        self._seed(records)

        orig_stdout = sys.stdout
        try:
            sys.stdout = io.StringIO()
            rc = main(["--json"])
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = orig_stdout

        self.assertEqual(rc, 0)
        parsed = json.loads(output)
        self.assertIn("aggregate", parsed)
        self.assertIn("anomalies", parsed)
        self.assertEqual(parsed["aggregate"]["total_swept"], 2)
        self.assertEqual(len(parsed["anomalies"]["misjudgments"]), 1)

    def test_sweep_anomalies_only_json_excludes_aggregate(self):
        """--anomalies-only --json outputs anomalies without aggregate."""
        from sweep_event_quality import main
        records = [_make_resolved_record("evt-1")]
        self._seed(records)

        orig_stdout = sys.stdout
        try:
            sys.stdout = io.StringIO()
            rc = main(["--json", "--anomalies-only"])
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = orig_stdout

        self.assertEqual(rc, 0)
        parsed = json.loads(output)
        self.assertNotIn("aggregate", parsed)
        self.assertIn("anomalies", parsed)

    def test_sweep_excludes_invalid_outcomes(self):
        """list_resolved_events filters status=invalid → sweep never sees them.

        This is a contract test: sweep relies on event_store.list_resolved_events
        to exclude non-resolved outcomes. If that filter breaks, sweep's
        direction_correct aggregate would be polluted.
        """
        from sweep_event_quality import _collect_entries
        # Two resolved + one invalid. The invalid one has actual_outcome
        # but status="invalid" — it must NOT appear in the sweep.
        records = [
            _make_resolved_record("resolved-1", direction="YES", actual_outcome=100.0),
            _make_resolved_record("resolved-2", direction="NO", actual_outcome=0.0),
        ]
        # Add an invalid-outcome record by direct manipulation
        invalid = _make_resolved_record("invalid-1", direction="YES", actual_outcome=100.0)
        invalid["outcome"]["status"] = "invalid"
        records.append(invalid)
        self._seed(records)

        entries = _collect_entries(limit=None, sample=None)
        swept_ids = {e["event_id"] for e in entries}
        self.assertIn("resolved-1", swept_ids)
        self.assertIn("resolved-2", swept_ids)
        self.assertNotIn("invalid-1", swept_ids)

    def test_sweep_resilient_to_single_event_failure(self):
        """If _extract_phase_data raises on one event, the sweep continues
        and the failed event appears in sweep_errors, not as a crash."""
        from sweep_event_quality import _sweep
        good_record = _make_resolved_record("good")

        # Patch _extract_phase_data to raise only for the "bad" event.
        from diagnose_event_quality import _extract_phase_data
        original_extract = _extract_phase_data

        def selective_extract(record):
            if record.get("event_id") == "bad":
                raise RuntimeError("simulated extract failure")
            return original_extract(record)

        entries = [
            {"event_id": "good", "record": good_record},
            {"event_id": "bad", "record": _make_resolved_record("bad")},
        ]
        with patch("diagnose_event_quality._extract_phase_data",
                   side_effect=selective_extract):
            # The import inside _sweep is `from diagnose_event_quality import
            # _extract_phase_data`, so patch at the source module.
            results = _sweep(entries)
        # Both processed, no crash
        self.assertEqual(len(results), 2)
        # The bad one has _sweep_error
        bad_result = next(r for r in results if r.get("event_id") == "bad")
        self.assertIn("_sweep_error", bad_result)
        self.assertIn("simulated extract failure", bad_result["_sweep_error"])


class TestSweepExitCodes(unittest.TestCase):
    def test_exit_code_2_on_load_exception(self):
        """When _collect_entries raises, main returns 2 + stderr message."""
        import sweep_event_quality as seq
        orig_stderr = sys.stderr
        try:
            sys.stderr = io.StringIO()
            with patch.object(seq, "_collect_entries", side_effect=RuntimeError("db locked")):
                rc = seq.main([])
            self.assertEqual(rc, 2)
            self.assertIn("failed to load events", sys.stderr.getvalue())
            self.assertIn("db locked", sys.stderr.getvalue())
        finally:
            sys.stderr = orig_stderr

    def test_exit_code_2_on_sweep_exception(self):
        """When _sweep raises (not single-event, but whole-batch), main returns 2."""
        import sweep_event_quality as seq
        orig_stderr = sys.stderr
        try:
            with patch.object(seq, "_collect_entries", return_value=[{"event_id": "x"}]):
                with patch.object(seq, "_sweep", side_effect=RuntimeError("sweep blew up")):
                    sys.stderr = io.StringIO()
                    rc = seq.main([])
            self.assertEqual(rc, 2)
            self.assertIn("sweep failed", sys.stderr.getvalue())
        finally:
            sys.stderr = orig_stderr


if __name__ == "__main__":
    unittest.main()
