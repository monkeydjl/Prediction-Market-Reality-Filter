"""HTTP tests for /quality-metrics/alerts endpoint (LATER #3)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.memory import event_store


def _make_resolved_record(
    event_id: str,
    *,
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
    """Build a minimal resolved record that passes EventRecord validation.

    Mirrors the helper in test_quality_metrics_report.py — duplicated here
    so this test file is self-contained (the two files exercise different
    layers: alert evaluation vs report shape).
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


class TestQualityAlertsEndpoint(unittest.TestCase):
    """HTTP tests for GET /api/quality-metrics/alerts."""

    def setUp(self) -> None:
        # Isolate the event_store per test so seeding never leaks across
        # tests or into the on-disk store. Mirrors the pattern in
        # test_quality_metrics_report.py (tempfile + patch EVENT_STORE_FILE).
        self._tmp = tempfile.TemporaryDirectory()
        self._store_path = Path(self._tmp.name) / "event_store.json"
        self._patch = patch.object(
            settings, "EVENT_STORE_FILE", str(self._store_path)
        )
        self._patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def _seed(self, records: list[dict]) -> None:
        for r in records:
            event_store.save_event(r)

    def test_alerts_empty_store_returns_200(self):
        """Empty store → 200, no alerts."""
        response = self.client.get("/api/quality-metrics/alerts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("alerts", data)
        self.assertIn("alert_count", data)
        self.assertEqual(data["alert_count"], len(data["alerts"]))

    def test_alerts_returns_alerts_for_degraded_quality(self):
        """Inject low-direction-accuracy records → alerts non-empty.

        Seeds 15 events with direction=YES, actual_outcome=0.0 (all wrong)
        → direction_accuracy=0.0 < 0.50 → high direction_accuracy_low alert.
        min_samples default is 10, so the overview (n=15) always participates.
        """
        self._seed([
            _make_resolved_record(
                f"e{i}", direction="YES", actual_outcome=0.0,
                brier_score=0.15, estimated_probability=60.0,
                source_reliability_score=0.7,
            )
            for i in range(15)
        ])
        response = self.client.get("/api/quality-metrics/alerts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreater(data["alert_count"], 0)
        codes = [a["code"] for a in data["alerts"]]
        self.assertIn("direction_accuracy_low", codes)

    def test_alerts_limit_truncates(self):
        """?limit=2 truncates results (same semantics as report endpoint)."""
        self._seed([_make_resolved_record(f"e{i}", brier_score=0.1) for i in range(5)])
        response = self.client.get("/api/quality-metrics/alerts?limit=2")
        self.assertEqual(response.status_code, 200)

    def test_alerts_sample_reproducible(self):
        """?sample=4 twice → identical alerts (seed=42)."""
        self._seed([_make_resolved_record(f"e{i}", brier_score=0.1) for i in range(10)])
        r1 = self.client.get("/api/quality-metrics/alerts?sample=4")
        r2 = self.client.get("/api/quality-metrics/alerts?sample=4")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json(), r2.json())

    def test_alerts_include_insufficient_samples(self):
        """?include_insufficient_samples=true → diagnostics field present."""
        response = self.client.get("/api/quality-metrics/alerts?include_insufficient_samples=true")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("diagnostics", data)
        self.assertIn("insufficient_samples", data["diagnostics"])

    def test_alerts_invalid_limit_rejected(self):
        """?limit=0 → 422 (FastAPI ge=1 validation)."""
        response = self.client.get("/api/quality-metrics/alerts?limit=0")
        self.assertEqual(response.status_code, 422)

    def test_alerts_resilient_to_malformed_record(self):
        """Malformed record → 200, no 500, report_errors counted.

        save_event validates against EventRecord, so we can't write a bad
        record through the normal path. Instead we patch list_resolved_events
        to return an entry whose "record" is not a dict — this triggers the
        endpoint's not-isinstance(record, dict) branch, producing a
        report_error. With one error, report_errors_high (>=1) should fire.
        """
        with patch("app.api.routes.quality_metrics.list_resolved_events",
                   return_value=[{"event_id": "bad", "record": "not-a-dict"}]):
            response = self.client.get("/api/quality-metrics/alerts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        codes = [a["code"] for a in data["alerts"]]
        self.assertIn("report_errors_high", codes)

    def test_alerts_default_no_diagnostics(self):
        """Without include_insufficient_samples → no diagnostics field."""
        response = self.client.get("/api/quality-metrics/alerts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertNotIn("diagnostics", data)


if __name__ == "__main__":
    unittest.main()
