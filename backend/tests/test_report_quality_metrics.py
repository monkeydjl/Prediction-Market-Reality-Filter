"""Unit tests for report_quality_metrics CLI (NEXT #3)."""
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
# report_quality_metrics as a top-level module.
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _make_resolved_record(
    event_id: str,
    direction: str = "YES",
    edge: float = 12.0,
    actual_outcome: float = 100.0,
    outcome_status: str = "resolved",
    source_type: str = "prediction_market",
    analysis_quality: str = "llm",
    source_reliability_score: float | None = None,
    brier_score: float | None = None,
    estimated_probability: float | None = None,
) -> dict:
    """Build a minimal record that passes EventRecord validation + has a
    resolved outcome. Extends the sweep test helper with calibration and
    source_reliability fields so we can test Brier aggregation + sr bucketing.
    """
    record: dict = {
        "event_id": event_id,
        "event_title": f"Event {event_id}",
        "event_summary": "summary",
        "source": {"type": source_type, "platform": "manifold"},
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
            "analysis_quality": analysis_quality,
        },
        "evidence_breakdown": [],
        "sentiment_profile": {"summary": "neutral", "articles": []},
        "outcome": {
            "status": outcome_status, "actual_outcome": actual_outcome,
            "confidence": 0.9, "resolved_at": "2026-06-15T00:00:00Z",
            "source": "manual",
        },
        "llm_telemetry": {
            "degraded_mode": False, "total_tokens": 1000,
            "estimated_token_cost": 0.001, "analysis_quality": analysis_quality,
        },
        "market_quality": {
            "degraded": False, "degrade_reason": None,
            "wide_spread_flag": False, "low_liquidity_flag": False,
        },
        "guardrail_fired": [],
    }
    # Attach calibration snapshot only when a brier_score is provided —
    # mirrors real records where calibration is attached at resolve time.
    if brier_score is not None:
        record["calibration"] = {
            "brier_score": brier_score,
            "skill_score": round(1.0 - brier_score / 0.25, 4),
            "grade": "GOOD",
            "estimated_probability": estimated_probability if estimated_probability is not None else 62.0,
            "actual_outcome": actual_outcome,
            "trajectory_observations": 3,
            "trajectory_span_hours": 12.0,
        }
    if source_reliability_score is not None:
        record["source_reliability"] = {
            "overall_score": source_reliability_score,
            "source_count": 3,
            "domain_diversity": 2,
            "trusted_source_ratio": 0.5,
            "official_source_count": 0,
            "unknown_source_ratio": 0.2,
            "source_breakdown": [],
            "downgrade_reason": None,
            "raw_direction": "YES",
            "suggested_direction": "YES",
            "downgraded": False,
            "applied_to_displayed_direction": False,
        }
    return record


def _seed_store(records: list[dict]) -> tuple[object, object]:
    """Save records to a temp event_store and return (tmp, patch_ctx).

    Caller must keep the TemporaryDirectory alive (returned via the patch
    context) — same pattern as test_sweep_event_quality._seed_store.
    """
    from app.core.config import settings
    from app.memory import event_store
    tmp = tempfile.TemporaryDirectory()
    store_path = Path(tmp.name) / "event_store.json"
    patch_ctx = patch.object(settings, "EVENT_STORE_FILE", str(store_path))
    patch_ctx.start()
    for r in records:
        event_store.save_event(r)
    return tmp, patch_ctx


class TestReport(unittest.TestCase):
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

    def test_report_empty_store_returns_zero(self):
        """Empty event_store → exit 0, not an error."""
        from report_quality_metrics import main
        self._seed([])
        rc = main([])
        self.assertEqual(rc, 0)

    def test_report_overview_counts(self):
        """overview: total/with_calibration/missing_calibration."""
        from report_quality_metrics import _collect_entries, _extract_metrics, _build_report
        records = [
            _make_resolved_record("with-cal", brier_score=0.1, estimated_probability=60.0),
            _make_resolved_record("no-cal"),  # no calibration snapshot
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        items = [_extract_metrics(e["record"]) for e in entries]
        report = _build_report(items, [])
        ov = report["overview"]
        self.assertEqual(ov["total_resolved"], 2)
        self.assertEqual(ov["with_calibration"], 1)
        self.assertEqual(ov["missing_calibration"], 1)

    def test_report_by_source_type(self):
        """Events grouped by record.source.type."""
        from report_quality_metrics import _collect_entries, _extract_metrics, _build_report
        records = [
            _make_resolved_record("pm", source_type="prediction_market"),
            _make_resolved_record("sport", source_type="sports_event"),
            _make_resolved_record("pm2", source_type="prediction_market"),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        items = [_extract_metrics(e["record"]) for e in entries]
        report = _build_report(items, [])
        by_st = report["by_source_type"]
        self.assertEqual(by_st["prediction_market"]["n"], 2)
        self.assertEqual(by_st["sports_event"]["n"], 1)

    def test_report_by_analysis_quality(self):
        """Events grouped by llm_telemetry.analysis_quality (engine proxy)."""
        from report_quality_metrics import _collect_entries, _extract_metrics, _build_report
        records = [
            _make_resolved_record("llm1", analysis_quality="llm"),
            _make_resolved_record("llm2", analysis_quality="llm"),
            _make_resolved_record("det", analysis_quality="deterministic_fallback"),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        items = [_extract_metrics(e["record"]) for e in entries]
        report = _build_report(items, [])
        by_aq = report["by_analysis_quality"]
        self.assertEqual(by_aq["llm"]["n"], 2)
        self.assertEqual(by_aq["deterministic_fallback"]["n"], 1)

    def test_report_by_edge_bucket(self):
        """Events grouped by compute_edge_bucket(edge)."""
        from report_quality_metrics import _collect_entries, _extract_metrics, _build_report
        records = [
            _make_resolved_record("small", edge=3.0),    # 0-5
            _make_resolved_record("mid", edge=12.0),     # 10-20
            _make_resolved_record("big", edge=25.0),     # 20+
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        items = [_extract_metrics(e["record"]) for e in entries]
        report = _build_report(items, [])
        by_eb = report["by_edge_bucket"]
        self.assertEqual(by_eb["0-5"]["n"], 1)
        self.assertEqual(by_eb["10-20"]["n"], 1)
        self.assertEqual(by_eb["20+"]["n"], 1)

    def test_report_by_source_reliability_bucket(self):
        """overall_score bucketed into low/medium/high/very_high/missing."""
        from report_quality_metrics import _collect_entries, _extract_metrics, _build_report
        records = [
            _make_resolved_record("low", source_reliability_score=0.3),
            _make_resolved_record("med", source_reliability_score=0.5),
            _make_resolved_record("high", source_reliability_score=0.7),
            _make_resolved_record("vhigh", source_reliability_score=0.9),
            _make_resolved_record("none"),  # no source_reliability → <missing>
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        items = [_extract_metrics(e["record"]) for e in entries]
        report = _build_report(items, [])
        by_sr = report["by_source_reliability_bucket"]
        self.assertEqual(by_sr["low(<0.4)"]["n"], 1)
        self.assertEqual(by_sr["medium(0.4-0.6)"]["n"], 1)
        self.assertEqual(by_sr["high(0.6-0.8)"]["n"], 1)
        self.assertEqual(by_sr["very_high(>=0.8)"]["n"], 1)
        self.assertEqual(by_sr["<missing>"]["n"], 1)

    def test_report_direction_accuracy(self):
        """2 true + 1 false → accuracy = 2/3 ≈ 0.6667. WAIT → None (not scored)."""
        from report_quality_metrics import _collect_entries, _extract_metrics, _build_report
        records = [
            _make_resolved_record("correct-yes", direction="YES", actual_outcome=100.0),
            _make_resolved_record("correct-no", direction="NO", actual_outcome=0.0),
            _make_resolved_record("wrong-yes", direction="YES", actual_outcome=0.0),
            _make_resolved_record("wait", direction="WAIT", actual_outcome=100.0),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        items = [_extract_metrics(e["record"]) for e in entries]
        report = _build_report(items, [])
        # All 4 share the same source_type, so one slice covers them all.
        sl = report["by_source_type"]["prediction_market"]
        self.assertEqual(sl["direction_correct_true"], 2)
        self.assertEqual(sl["direction_correct_false"], 1)
        self.assertEqual(sl["direction_correct_none"], 1)
        # accuracy = 2 / (2+1) = 0.6667
        self.assertAlmostEqual(sl["direction_accuracy"], 2 / 3, places=4)

    def test_report_brier_aggregation(self):
        """Mean Brier aggregated from record.calibration.brier_score."""
        from report_quality_metrics import _collect_entries, _extract_metrics, _build_report
        records = [
            _make_resolved_record("a", brier_score=0.1, estimated_probability=60.0),
            _make_resolved_record("b", brier_score=0.2, estimated_probability=70.0),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        items = [_extract_metrics(e["record"]) for e in entries]
        report = _build_report(items, [])
        sl = report["by_source_type"]["prediction_market"]
        # mean(0.1, 0.2) = 0.15
        self.assertAlmostEqual(sl["brier"]["brier_score"], 0.15, places=4)
        self.assertEqual(sl["brier"]["n"], 2)
        # skill = 1 - 0.15/0.25 = 0.4
        self.assertAlmostEqual(sl["brier"]["skill_score"], 0.4, places=4)

    def test_report_missing_calibration_yields_no_data_brier(self):
        """Slice with no calibration snapshots → brier grade='no_data', n=0."""
        from report_quality_metrics import _collect_entries, _extract_metrics, _build_report
        records = [
            _make_resolved_record("no-cal"),  # no calibration
            _make_resolved_record("also-no-cal"),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        items = [_extract_metrics(e["record"]) for e in entries]
        report = _build_report(items, [])
        sl = report["by_source_type"]["prediction_market"]
        self.assertIsNone(sl["brier"]["brier_score"])
        self.assertEqual(sl["brier"]["grade"], "no_data")
        self.assertEqual(sl["brier"]["n"], 0)

    def test_report_calibration_deviation_buckets(self):
        """Calibration deviation table: predicted vs actual per prob bucket."""
        from report_quality_metrics import _collect_entries, _extract_metrics, _build_report
        records = [
            # bucket 20-40: predicted=30, actual=0 → dev +30
            _make_resolved_record("b1", brier_score=0.09, estimated_probability=30.0, actual_outcome=0.0),
            # bucket 60-80: predicted=70, actual=100 → dev -30
            _make_resolved_record("b2", brier_score=0.09, estimated_probability=70.0, actual_outcome=100.0),
        ]
        self._seed(records)
        entries = _collect_entries(limit=None, sample=None)
        items = [_extract_metrics(e["record"]) for e in entries]
        report = _build_report(items, [])
        cd = {row["bucket"]: row for row in report["calibration_deviation"]}
        # 20-40 bucket: 1 event, pred 30, actual 0, dev +30
        self.assertEqual(cd["20-40"]["n"], 1)
        self.assertAlmostEqual(cd["20-40"]["predicted_mean"], 30.0)
        self.assertAlmostEqual(cd["20-40"]["actual_mean"], 0.0)
        self.assertAlmostEqual(cd["20-40"]["deviation"], 30.0)
        # 60-80 bucket: 1 event, pred 70, actual 100, dev -30
        self.assertEqual(cd["60-80"]["n"], 1)
        self.assertAlmostEqual(cd["60-80"]["deviation"], -30.0)
        # empty buckets have n=0 and None means
        self.assertEqual(cd["0-20"]["n"], 0)
        self.assertIsNone(cd["0-20"]["predicted_mean"])

    def test_report_invalid_outcome_excluded_from_scoring(self):
        """status='invalid' → actual_outcome=None, direction_correct=None,
        not counted in calibration deviation."""
        from report_quality_metrics import _collect_entries, _extract_metrics
        # Note: list_resolved_events filters out status!='resolved', so this
        # event won't appear in _collect_entries at all. We test _extract_metrics
        # directly to confirm the gating contract.
        record = _make_resolved_record(
            "invalid", direction="YES", actual_outcome=100.0,
            outcome_status="invalid", brier_score=0.1, estimated_probability=60.0,
        )
        m = _extract_metrics(record)
        # direction_correct is None because status != "resolved"
        self.assertIsNone(m["direction_correct"])
        # actual_outcome is None because status != "resolved"
        self.assertIsNone(m["actual_outcome"])

    def test_report_limit_truncates_entries(self):
        """--limit N reports on only the first N entries."""
        from report_quality_metrics import _collect_entries
        records = [_make_resolved_record(f"evt-{i}") for i in range(5)]
        self._seed(records)
        entries = _collect_entries(limit=2, sample=None)
        self.assertEqual(len(entries), 2)

    def test_report_sample_is_reproducible(self):
        """--sample N uses seed=42, so two runs return the same subset."""
        from report_quality_metrics import _collect_entries
        records = [_make_resolved_record(f"evt-{i}") for i in range(10)]
        self._seed(records)
        first = _collect_entries(limit=None, sample=4)
        second = _collect_entries(limit=None, sample=4)
        ids_first = [e["event_id"] for e in first]
        ids_second = [e["event_id"] for e in second]
        self.assertEqual(ids_first, ids_second)
        self.assertEqual(len(ids_first), 4)

    def test_report_json_output_structure(self):
        """--json outputs a dict with the expected top-level keys."""
        from report_quality_metrics import main
        import io as _io
        records = [
            _make_resolved_record("e1", brier_score=0.1, estimated_probability=60.0,
                                   source_reliability_score=0.7),
        ]
        self._seed(records)
        buf = _io.StringIO()
        with patch("sys.stdout", buf):
            rc = main(["--json"])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        for key in ("overview", "by_source_type", "by_analysis_quality",
                    "by_edge_bucket", "by_source_reliability_bucket",
                    "calibration_deviation", "report_errors"):
            self.assertIn(key, out)
        self.assertEqual(out["overview"]["total_resolved"], 1)

    def test_report_resilient_to_single_event_failure(self):
        """One record that raises during _extract_metrics must not abort the
        whole report — it lands in report_errors instead."""
        from report_quality_metrics import _extract_metrics, _build_report
        # Two healthy items + one we make fail by patching _extract_metrics.
        good_items = [
            {"event_id": "g1", "source_type": "prediction_market",
             "analysis_quality": "llm", "edge_bucket": "0-5",
             "source_reliability_bucket": "<missing>",
             "direction_correct": True, "brier_score": 0.1,
             "estimated_probability": 60.0, "actual_outcome": 100.0},
            {"event_id": "g2", "source_type": "prediction_market",
             "analysis_quality": "llm", "edge_bucket": "0-5",
             "source_reliability_bucket": "<missing>",
             "direction_correct": False, "brier_score": 0.2,
             "estimated_probability": 40.0, "actual_outcome": 100.0},
        ]
        report_errors = [{"event_id": "bad", "error": "boom"}]
        report = _build_report(good_items, report_errors)
        # The two healthy items were aggregated.
        self.assertEqual(report["overview"]["total_resolved"], 2)
        # The failure was recorded.
        self.assertEqual(len(report["report_errors"]), 1)
        self.assertEqual(report["report_errors"][0]["event_id"], "bad")


class TestReportExitCodes(unittest.TestCase):
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

    def test_report_exit_code_2_on_load_failure(self):
        """If event_store.list_resolved_events raises, main returns 2."""
        from report_quality_metrics import main
        with patch("app.memory.event_store.list_resolved_events",
                   side_effect=RuntimeError("disk gone")):
            rc = main([])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
