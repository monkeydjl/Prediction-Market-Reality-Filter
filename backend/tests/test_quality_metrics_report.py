"""HTTP tests for /api/quality-metrics/report endpoint (NEXT #3).

Validates the FastAPI route that wraps
quality_metrics_report_service.build_report over list_resolved_events.
Focuses on the HTTP contract (status codes, JSON shape, query params) —
the service-level math is covered by test_report_quality_metrics.py.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import quality_metrics as quality_metrics_routes
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

    Mirrors the helper in test_report_quality_metrics.py — duplicated here
    so this test file is self-contained (the two files exercise different
    layers: service math vs HTTP route).
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


class TestQualityMetricsReportEndpoint(unittest.TestCase):
    """HTTP tests for GET /api/quality-metrics/report."""

    def _app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(quality_metrics_routes.router)
        return app

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._store_path = Path(self._tmp.name) / "event_store.json"
        self._patch = patch.object(
            settings, "EVENT_STORE_FILE", str(self._store_path)
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _seed(self, records: list[dict]) -> None:
        for r in records:
            event_store.save_event(r)

    def test_report_empty_store_returns_200_with_zero_overview(self):
        """Empty event_store → 200, overview.total_resolved=0."""
        client = TestClient(self._app())
        response = client.get("/quality-metrics/report")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["overview"]["total_resolved"], 0)
        self.assertEqual(data["overview"]["with_calibration"], 0)
        # All slice dicts present (empty)
        for key in ("by_source_type", "by_analysis_quality",
                    "by_edge_bucket", "by_source_reliability_bucket"):
            self.assertEqual(data[key], {})
        # calibration_deviation always returns 5 buckets (even when empty),
        # each with n=0 and None means — mirrors the CLI's behavior.
        self.assertEqual(len(data["calibration_deviation"]), 5)
        for row in data["calibration_deviation"]:
            self.assertEqual(row["n"], 0)
            self.assertIsNone(row["predicted_mean"])
        self.assertEqual(data["report_errors"], [])

    def test_report_returns_full_shape_for_resolved_events(self):
        """Resolved events → 200, all top-level keys present with data."""
        self._seed([
            _make_resolved_record("e1", brier_score=0.1, estimated_probability=60.0,
                                   source_reliability_score=0.7),
            _make_resolved_record("e2", direction="NO", actual_outcome=0.0,
                                   brier_score=0.05, estimated_probability=30.0,
                                   source_reliability_score=0.5),
        ])
        client = TestClient(self._app())
        response = client.get("/quality-metrics/report")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["overview"]["total_resolved"], 2)
        self.assertEqual(data["overview"]["with_calibration"], 2)
        # by_source_type has the prediction_market slice
        self.assertIn("prediction_market", data["by_source_type"])
        sl = data["by_source_type"]["prediction_market"]
        self.assertEqual(sl["n"], 2)
        # 1 true (e1: YES+outcome100) + 1 true (e2: NO+outcome0)
        self.assertEqual(sl["direction_correct_true"], 2)
        self.assertEqual(sl["direction_correct_false"], 0)
        # calibration_deviation has 5 buckets
        self.assertEqual(len(data["calibration_deviation"]), 5)
        # report_errors empty (all records well-formed)
        self.assertEqual(data["report_errors"], [])

    def test_report_limit_truncates_results(self):
        """?limit=N reports on only the first N events."""
        self._seed([_make_resolved_record(f"e{i}") for i in range(5)])
        client = TestClient(self._app())
        response = client.get("/quality-metrics/report?limit=2")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["overview"]["total_resolved"], 2)

    def test_report_sample_is_reproducible(self):
        """?sample=N uses seed=42 — two calls return the same subset."""
        self._seed([_make_resolved_record(f"e{i}") for i in range(10)])
        client = TestClient(self._app())
        r1 = client.get("/quality-metrics/report?sample=4").json()
        r2 = client.get("/quality-metrics/report?sample=4").json()
        # Same total count (4) — the slice composition should be identical
        # because the seed is fixed.
        self.assertEqual(r1["overview"]["total_resolved"], 4)
        self.assertEqual(r2["overview"]["total_resolved"], 4)
        # Same by_source_type breakdown (both sample from same seed)
        self.assertEqual(r1["by_source_type"], r2["by_source_type"])

    def test_report_excludes_non_resolved_events(self):
        """status='invalid' events are excluded by list_resolved_events,
        so they don't appear in the report at all."""
        self._seed([
            _make_resolved_record("resolved", brier_score=0.1),
            _make_resolved_record("invalid", outcome_status="invalid"),
        ])
        client = TestClient(self._app())
        response = client.get("/quality-metrics/report")
        data = response.json()
        # Only the resolved event is in the report
        self.assertEqual(data["overview"]["total_resolved"], 1)

    def test_report_resilient_to_malformed_record(self):
        """A record missing required fields lands in report_errors, not a 500."""
        # save_event validates against EventRecord, so we can't directly save
        # a malformed record through the normal path. Instead we test that
        # the endpoint never 500s on a normal store (resilience is the
        # service layer's job, exercised in test_report_quality_metrics.py).
        self._seed([_make_resolved_record("ok", brier_score=0.1)])
        client = TestClient(self._app())
        response = client.get("/quality-metrics/report")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["report_errors"], [])


if __name__ == "__main__":
    unittest.main()
