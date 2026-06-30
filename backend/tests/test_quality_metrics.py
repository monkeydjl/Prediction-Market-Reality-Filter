"""Unit + HTTP tests for the Prometheus /metrics + /api/quality-metrics endpoints (P0-6).

Covers:
- ``app.utils.metrics`` module exports all spec-required metric names.
- ``/metrics`` endpoint returns Prometheus text exposition format with the
  expected metric families present.
- ``/api/quality-metrics/summary`` returns the documented JSON shape and
  correct aggregates from a seeded event_store + loop_db.
- ``/api/quality-metrics/timeseries`` returns the scheduler run slice.
- ``/api/quality-metrics/anomalies`` surfaces the wide_spread + scheduler
  anomaly codes when those conditions are present.
- Aggregate gauge refresh recomputes direction/consensus counts on each scrape.
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
from app.memory import event_store as store
from app.memory import loop_run_store
from app.utils import sqlite_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed_event(
    event_id: str,
    *,
    final_direction: str | None = None,
    consensus_level: str | None = None,
    market_quality: dict | None = None,
    source_reliability: dict | None = None,
    llm_telemetry: dict | None = None,
    decision_quality_error: bool = False,
    final_downgrade_reason: str | None = None,
) -> dict:
    record = {
        "event_id": event_id,
        "event_title": f"event {event_id}",
        "event_summary": "summary",
        "event_title_zh": "事件",
        "probability": {
            "baseline": 50.0,
            "estimated": 60.0,
            "change": 10.0,
            "direction": "rising",
        },
        "credibility": {
            "score": 60,
            "level": "MEDIUM",
            "confidence": 0.6,
            "news_quality": 0.5,
            "evidence_strength": 0.4,
            "source_count": 3,
        },
        "impact": {"score": 55, "level": "MEDIUM", "drivers": ["strong_evidence"]},
        "risk": {"level": "LOW", "flags": []},
        "evidence": {
            "direction": "supports",
            "strength": 0.4,
            "conflict": 0.1,
            "freshness": 0.7,
            "resolution_relevance": 0.5,
        },
        "source": {"type": "manual"},
        "value_score": 50,
        "intelligence_report": {
            "headline": "h",
            "why_it_matters": "w",
            "probability_assessment": "p",
            "recommended_action": "a",
        },
        "schema_version": "v2.1",
    }
    if final_direction is not None:
        record["final_displayed_direction"] = final_direction
    if final_downgrade_reason is not None:
        record["final_downgrade_reason"] = final_downgrade_reason
    if consensus_level is not None or decision_quality_error:
        record["decision_quality"] = {
            "consensus_level": consensus_level or "high",
            "raw_direction": "YES",
            "displayed_direction": final_direction or "YES",
            "downgraded": False,
            "downgrade_reason": None,
            "supporting_evidence": [],
            "opposing_evidence": [],
            "conflict_score": 0.0,
            "decision_rationale_zh": "rationale",
            "reversal_triggers": [],
        }
        if decision_quality_error:
            record["decision_quality"]["error"] = "build_failed"
    if market_quality is not None:
        record["market_quality"] = market_quality
    if source_reliability is not None:
        record["source_reliability"] = source_reliability
    if llm_telemetry is not None:
        record["llm_telemetry"] = llm_telemetry
    return record


def _client_with_stores(tmp: Path) -> TestClient:
    """Build a FastAPI test client backed by a temp event_store + loop_db."""
    app = FastAPI()
    app.include_router(quality_metrics_routes.router)
    # Mount the /metrics endpoint as in main.py
    from fastapi import Response

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics():
        from app.utils.metrics import render_metrics
        body, content_type = render_metrics()
        return Response(content=body, media_type=content_type)

    with patch.object(store, "_store_path", return_value=str(tmp / "event_store.json")), \
            patch.object(sqlite_db, "loop_db_path", return_value=str(tmp / "v2_loop.db")):
        # Touch the store file so list_all_events returns [] instead of raising
        store._load_unlocked(str(tmp / "event_store.json"))  # ensures file exists
        yield app


class _StoreContext:
    """Context manager that patches event_store + loop_db paths and seeds events."""

    def __init__(self, tmp: Path, events: list[dict] | None = None):
        self.tmp = tmp
        self.events = events or []

    def __enter__(self):
        self._store_patch = patch.object(
            store, "_store_path", return_value=str(self.tmp / "event_store.json")
        )
        self._loop_patch = patch.object(
            sqlite_db, "loop_db_path", return_value=str(self.tmp / "v2_loop.db")
        )
        self._store_patch.start()
        self._loop_patch.start()
        # Initialize empty store + loop_db schema
        from app.utils.file_store import write_json_atomic
        write_json_atomic(str(self.tmp / "event_store.json"), {}, indent=2)
        loop_run_store._ensure_schema(str(self.tmp / "v2_loop.db"))
        if self.events:
            store.save_events(self.events, skip_invalid=False)
        return self

    def __exit__(self, *args):
        self._store_patch.stop()
        self._loop_patch.stop()


# ---------------------------------------------------------------------------
# Module-level tests
# ---------------------------------------------------------------------------

class TestMetricsModuleDefinitions(unittest.TestCase):
    """Spec §1.1 — all required metric names must be exported."""

    def test_all_core_metrics_defined(self):
        from app.utils import metrics

        # The five core spec metrics
        self.assertTrue(hasattr(metrics, "DECISION_QUALITY_DOWNGRADE"))
        self.assertTrue(hasattr(metrics, "CONSENSUS_DISTRIBUTION"))
        self.assertTrue(hasattr(metrics, "RULE_FIRE"))
        self.assertTrue(hasattr(metrics, "OVERLAY_BUILD_FAILURE"))
        self.assertTrue(hasattr(metrics, "OVERLAY_LATENCY"))

        # Production metrics
        self.assertTrue(hasattr(metrics, "SCHEDULER_LAST_SUCCESS"))
        self.assertTrue(hasattr(metrics, "SCHEDULER_FAILED_RUNS"))
        self.assertTrue(hasattr(metrics, "LLM_TOKEN_COST"))
        self.assertTrue(hasattr(metrics, "CALIBRATION_BRIER"))
        self.assertTrue(hasattr(metrics, "CALIBRATION_DRIFT"))
        self.assertTrue(hasattr(metrics, "FINAL_DIRECTION"))
        self.assertTrue(hasattr(metrics, "FINAL_DIRECTION_CHANGE"))

    def test_render_metrics_returns_bytes_and_content_type(self):
        from app.utils.metrics import render_metrics, CONTENT_TYPE_LATEST

        body, content_type = render_metrics()
        self.assertIsInstance(body, (bytes, bytearray))
        self.assertEqual(content_type, CONTENT_TYPE_LATEST)
        # Body must contain at least one pmrf_ metric
        self.assertIn(b"pmrf_", body)

    def test_counter_inc_and_observe_are_noop_safe_after_clear(self):
        """Increment + observe should not raise even after registry manipulation."""
        from app.utils import metrics
        metrics.DECISION_QUALITY_DOWNGRADE.labels(reason="test").inc()
        metrics.OVERLAY_LATENCY.labels(phase="test").observe(0.001)
        metrics.FINAL_DIRECTION.labels(direction="YES").set(3)
        # The Prometheus registry is global; we can't easily reset between
        # tests, so just assert no exception fired.


# ---------------------------------------------------------------------------
# /metrics HTTP endpoint tests
# ---------------------------------------------------------------------------

class TestMetricsEndpoint(unittest.TestCase):
    """The /metrics endpoint must serve prometheus exposition format."""

    def _app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(quality_metrics_routes.router)
        from fastapi import Response

        @app.get("/metrics", include_in_schema=False)
        async def prometheus_metrics():
            from app.utils.metrics import render_metrics
            body, content_type = render_metrics()
            return Response(content=body, media_type=content_type)

        return app

    def test_metrics_endpoint_returns_prometheus_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp)):
                client = TestClient(self._app())
                response = client.get("/metrics")
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/plain", response.headers.get("content-type", ""))
                body = response.text
                # Spec §1.1 — required metric names exposed
                self.assertIn("pmrf_decision_quality_downgrade_total", body)
                self.assertIn("pmrf_consensus_distribution", body)
                self.assertIn("pmrf_rule_fire_total", body)
                self.assertIn("pmrf_overlay_build_failure_total", body)
                self.assertIn("pmrf_overlay_latency_ms", body)
                self.assertIn("pmrf_scheduler_last_success_timestamp", body)
                self.assertIn("pmrf_scheduler_failed_runs_total", body)
                self.assertIn("pmrf_llm_token_cost_total", body)
                self.assertIn("pmrf_calibration_brier_score", body)
                self.assertIn("pmrf_final_direction_count", body)

    def test_metrics_endpoint_reflects_event_store_direction_counts(self):
        events = [
            _seed_event("e1", final_direction="YES"),
            _seed_event("e2", final_direction="YES"),
            _seed_event("e3", final_direction="WAIT"),
            _seed_event("e4", final_direction="NO"),
            _seed_event("e5", final_direction="AVOID"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp), events):
                client = TestClient(self._app())
                response = client.get("/metrics")
                body = response.text
                # Look for the YES label with value 2
                self.assertIn(
                    'pmrf_final_direction_count{direction="YES"} 2.0',
                    body,
                )
                self.assertIn(
                    'pmrf_final_direction_count{direction="WAIT"} 1.0',
                    body,
                )
                self.assertIn(
                    'pmrf_final_direction_count{direction="NO"} 1.0',
                    body,
                )
                self.assertIn(
                    'pmrf_final_direction_count{direction="AVOID"} 1.0',
                    body,
                )

    def test_metrics_endpoint_reflects_consensus_distribution(self):
        events = [
            _seed_event("e1", consensus_level="high"),
            _seed_event("e2", consensus_level="high"),
            _seed_event("e3", consensus_level="low"),
            _seed_event("e4", consensus_level="medium"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp), events):
                client = TestClient(self._app())
                response = client.get("/metrics")
                body = response.text
                self.assertIn(
                    'pmrf_consensus_distribution{level="high"} 2.0',
                    body,
                )
                self.assertIn(
                    'pmrf_consensus_distribution{level="medium"} 1.0',
                    body,
                )


# ---------------------------------------------------------------------------
# /api/quality-metrics/summary
# ---------------------------------------------------------------------------

class TestQualityMetricsSummary(unittest.TestCase):

    def _app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(quality_metrics_routes.router)
        return app

    def test_summary_returns_expected_shape_for_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp)):
                client = TestClient(self._app())
                response = client.get("/quality-metrics/summary")
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["counts"]["events"], 0)
                self.assertEqual(data["counts"]["resolved_events"], 0)
                self.assertEqual(data["final_direction"], {"YES": 0, "NO": 0, "WAIT": 0, "AVOID": 0})
                self.assertEqual(data["consensus"], {"none": 0, "low": 0, "medium": 0, "high": 0})
                self.assertEqual(data["downgrade"]["final_downgrade_reason_present"], 0)
                self.assertIn("calibration", data)
                self.assertIn("calibration_buckets", data)
                self.assertIn("scheduler", data)

    def test_summary_aggregates_direction_counts(self):
        events = [
            _seed_event("e1", final_direction="YES"),
            _seed_event("e2", final_direction="NO"),
            _seed_event("e3", final_direction="WAIT"),
            _seed_event("e4", final_direction="AVOID"),
            _seed_event("e5", final_direction="YES"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp), events):
                client = TestClient(self._app())
                response = client.get("/quality-metrics/summary")
                data = response.json()
                self.assertEqual(data["final_direction"], {"YES": 2, "NO": 1, "WAIT": 1, "AVOID": 1})

    def test_summary_counts_market_quality_flags(self):
        events = [
            _seed_event(
                "e1",
                market_quality={
                    "score": 0.8,
                    "wide_spread_flag": True,
                    "thin_market_flag": False,
                },
            ),
            _seed_event(
                "e2",
                market_quality={
                    "score": 0.6,
                    "wide_spread_flag": False,
                    "thin_market_flag": True,
                },
            ),
            _seed_event("e3"),  # no market_quality
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp), events):
                client = TestClient(self._app())
                response = client.get("/quality-metrics/summary")
                data = response.json()
                self.assertEqual(data["market_quality"]["count"], 2)
                self.assertEqual(data["market_quality"]["wide_spread_flag_count"], 1)
                self.assertEqual(data["market_quality"]["thin_market_flag_count"], 1)
                self.assertEqual(data["market_quality"]["score_avg"], 0.7)
                self.assertEqual(data["market_quality"]["score_min"], 0.6)
                self.assertEqual(data["market_quality"]["score_max"], 0.8)

    def test_summary_aggregates_source_reliability(self):
        events = [
            _seed_event(
                "e1",
                source_reliability={
                    "overall_score": 0.7,
                    "source_count": 4,
                    "domain_diversity": 3,
                },
            ),
            _seed_event(
                "e2",
                source_reliability={
                    "overall_score": 0.9,
                    "source_count": 6,
                    "domain_diversity": 4,
                },
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp), events):
                client = TestClient(self._app())
                data = client.get("/quality-metrics/summary").json()
                self.assertEqual(data["source_reliability"]["count"], 2)
                self.assertEqual(data["source_reliability"]["overall_score_avg"], 0.8)
                self.assertEqual(data["source_reliability"]["source_count_avg"], 5.0)
                self.assertEqual(data["source_reliability"]["domain_diversity_avg"], 3.5)

    def test_summary_aggregates_llm_telemetry(self):
        events = [
            _seed_event(
                "e1",
                llm_telemetry={"degraded_mode": False, "estimated_token_cost": 0.001},
            ),
            _seed_event(
                "e2",
                llm_telemetry={"degraded_mode": True, "estimated_token_cost": 0.002},
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp), events):
                client = TestClient(self._app())
                data = client.get("/quality-metrics/summary").json()
                self.assertEqual(data["llm_telemetry"]["count"], 2)
                self.assertEqual(data["llm_telemetry"]["degraded_mode_count"], 1)
                self.assertEqual(data["llm_telemetry"]["estimated_token_cost_total"], 0.003)

    def test_summary_counts_overlay_build_errors(self):
        events = [
            _seed_event("e1", decision_quality_error=True),
            _seed_event("e2"),
            _seed_event(
                "e3",
                market_quality={"error": "build_failed", "score": 0.0},
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp), events):
                client = TestClient(self._app())
                data = client.get("/quality-metrics/summary").json()
                self.assertEqual(data["downgrade"]["build_errors"]["decision_quality"], 1)
                self.assertEqual(data["downgrade"]["build_errors"]["market_quality"], 1)
                self.assertEqual(data["downgrade"]["build_errors"]["source_reliability"], 0)

    def test_summary_counts_final_downgrade_reason_present(self):
        events = [
            _seed_event("e1", final_downgrade_reason="证据冲突较高，强方向建议降级为 WAIT。"),
            _seed_event("e2"),
            _seed_event("e3", final_downgrade_reason="市场流动性不足。"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp), events):
                client = TestClient(self._app())
                data = client.get("/quality-metrics/summary").json()
                self.assertEqual(data["downgrade"]["final_downgrade_reason_present"], 2)

    def test_summary_timeframe_param_clamped_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp)):
                client = TestClient(self._app())
                # Invalid timeframe should fall back to 24h
                response = client.get("/quality-metrics/summary", params={"timeframe": "garbage"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["timeframe"], "24h")

    def test_summary_includes_scheduler_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp)):
                # Seed one successful run
                run_id = loop_run_store.start_run("event_discover")
                loop_run_store.finish_run(run_id, "success", result={"n": 1})

                client = TestClient(self._app())
                data = client.get("/quality-metrics/summary").json()
                sched = data["scheduler"]
                self.assertIn("last_runs", sched)
                self.assertIsNotNone(sched["last_runs"]["event_discover"])
                self.assertEqual(sched["last_runs"]["event_discover"]["status"], "success")
                self.assertEqual(sched["recent_failed_count"], 0)


# ---------------------------------------------------------------------------
# /api/quality-metrics/timeseries
# ---------------------------------------------------------------------------

class TestQualityMetricsTimeseries(unittest.TestCase):

    def _app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(quality_metrics_routes.router)
        return app

    def test_timeseries_returns_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp)):
                run_id = loop_run_store.start_run("event_discover")
                loop_run_store.finish_run(run_id, "success", result={"n": 1})

                client = TestClient(self._app())
                response = client.get("/quality-metrics/timeseries", params={"window": "24h"})
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["window"], "24h")
                self.assertGreaterEqual(len(data["points"]), 1)
                point = data["points"][0]
                self.assertEqual(point["job_name"], "event_discover")
                self.assertEqual(point["status"], "success")
                self.assertIn("duration_ms", point)

    def test_timeseries_window_invalid_falls_back_to_7d(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp)):
                client = TestClient(self._app())
                response = client.get("/quality-metrics/timeseries", params={"window": "garbage"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["window"], "7d")


# ---------------------------------------------------------------------------
# /api/quality-metrics/anomalies
# ---------------------------------------------------------------------------

class TestQualityMetricsAnomalies(unittest.TestCase):

    def _app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(quality_metrics_routes.router)
        return app

    def test_anomalies_empty_for_clean_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp)):
                # Scheduler anomaly check requires the scheduler module;
                # patch SCHEDULER_ENABLED to False to skip that branch
                with patch.object(settings, "SCHEDULER_ENABLED", False):
                    client = TestClient(self._app())
                    response = client.get("/quality-metrics/anomalies")
                    self.assertEqual(response.status_code, 200)
                    data = response.json()
                    # No anomalies when scheduler disabled + clean store
                    self.assertEqual(data["count"], 0)

    def test_anomalies_flags_wide_spread_not_downgraded(self):
        events = [
            _seed_event(
                "e1",
                final_direction="YES",
                market_quality={
                    "score": 0.3,
                    "wide_spread_flag": True,
                    "thin_market_flag": False,
                },
            ),
            _seed_event(
                "e2",
                final_direction="WAIT",
                market_quality={
                    "score": 0.4,
                    "wide_spread_flag": True,
                    "thin_market_flag": False,
                },
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp), events):
                with patch.object(settings, "SCHEDULER_ENABLED", False):
                    client = TestClient(self._app())
                    data = client.get("/quality-metrics/anomalies").json()
                    codes = [a["code"] for a in data["anomalies"]]
                    self.assertIn("wide_spread_not_downgraded", codes)
                    anomaly = next(a for a in data["anomalies"] if a["code"] == "wide_spread_not_downgraded")
                    self.assertEqual(anomaly["detail"]["count"], 1)

    def test_anomalies_flags_llm_degraded_events(self):
        events = [
            _seed_event("e1", llm_telemetry={"degraded_mode": True, "estimated_token_cost": 0.0}),
            _seed_event("e2", llm_telemetry={"degraded_mode": False, "estimated_token_cost": 0.0}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp), events):
                with patch.object(settings, "SCHEDULER_ENABLED", False):
                    client = TestClient(self._app())
                    data = client.get("/quality-metrics/anomalies").json()
                    codes = [a["code"] for a in data["anomalies"]]
                    self.assertIn("llm_degraded_mode_events", codes)

    def test_anomalies_flags_failed_scheduler_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _StoreContext(Path(tmp)):
                run_id = loop_run_store.start_run("event_discover")
                loop_run_store.finish_run(run_id, "failed", error="boom")

                with patch.object(settings, "SCHEDULER_ENABLED", False):
                    client = TestClient(self._app())
                    data = client.get("/quality-metrics/anomalies").json()
                    codes = [a["code"] for a in data["anomalies"]]
                    self.assertIn("scheduler_job_failed", codes)


class TestQualityMetricsDriftRoute(unittest.TestCase):
    """Tests for GET /api/quality-metrics/drift (Plan 2 Task 2)."""

    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(quality_metrics_routes.router)
        self.client = TestClient(self.app)

    def test_drift_returns_report_shape(self):
        with patch("app.api.routes.quality_metrics.list_scored_samples_for_drift") as ls, \
             patch("app.api.routes.quality_metrics.list_all_events", return_value=[]):
            ls.return_value = {
                "recent": [
                    {"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
                     "edge_bucket": "5-10", "confidence_bucket": "high",
                     "direction_correct": 1, "degraded": False, "event_id": "e1",
                     "resolved_at": "2026-06-29T00:00:00+00:00"},
                ],
                "baseline": [
                    {"predicted_prob": 0.7, "actual_outcome": 1, "brier_score": 0.09,
                     "edge_bucket": "5-10", "confidence_bucket": "high",
                     "direction_correct": 1, "degraded": False, "event_id": "e0",
                     "resolved_at": "2026-06-01T00:00:00+00:00"},
                ],
            }
            response = self.client.get("/quality-metrics/drift")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("drift", body)
        self.assertIn("ece", body)
        self.assertIn("alerts", body)
        self.assertIn("buckets", body)

    def test_drift_gauge_wired_uses_store_not_placeholder(self):
        """_refresh_calibration_gauges reads prediction_store for drift
        (real computation), not the old CALIBRATION_DRIFT.set(0.0) placeholder.

        The import inside _refresh_calibration_gauges is local
        (``from app.memory.prediction_store import ...``), so we patch the
        source module ``app.memory.prediction_store`` — NOT
        ``app.utils.metrics.prediction_store`` (that attribute does not
        exist on the metrics module).
        """
        from app.utils import metrics
        with patch("app.memory.prediction_store.calibration_summary",
                   return_value={"brier_score": 0.2}), \
             patch("app.memory.prediction_store.list_scored_samples_for_drift",
                   return_value={
                       "recent": [{"brier_score": 0.3}],
                       "baseline": [{"brier_score": 0.1}],
                   }) as mock_list:
            # Must not raise — best-effort refresh.
            metrics._refresh_calibration_gauges()
            # The store was consulted (real path), confirming the 0.0
            # placeholder is gone.
            mock_list.assert_called_once_with(recent_n=50)

    def test_drift_route_does_not_dispatch_without_write_key(self):
        """Unauthenticated GET must NOT trigger dispatch side effects.

        The route is read-only for unauthenticated callers (no X-API-Key
        required, same as /api/health). Only callers with a valid write
        key can trigger Sentry/webhook dispatch — otherwise any visitor
        could turn the read endpoint into a side-effect surface.
        """
        from app.api.security import optional_write_key
        # Override the dependency to simulate "no write key" (False).
        self.app.dependency_overrides[optional_write_key] = lambda: False
        try:
            with patch("app.api.routes.quality_metrics.list_scored_samples_for_drift") as ls, \
                 patch("app.api.routes.quality_metrics.list_all_events", return_value=[]), \
                 patch("app.api.routes.quality_metrics.dispatch_drift_alerts") as dispatch, \
                 patch("app.api.routes.quality_metrics.evaluate_scheduler_alerts", return_value=[]):
                ls.return_value = {"recent": [], "baseline": []}
                response = self.client.get("/quality-metrics/drift")
        finally:
            self.app.dependency_overrides.pop(optional_write_key, None)
        self.assertEqual(response.status_code, 200)
        # Detection result is still returned...
        self.assertIn("alerts", response.json())
        # ...but dispatch was not called (no write key).
        dispatch.assert_not_called()

    def test_drift_route_dispatches_with_valid_write_key(self):
        """Authenticated GET (valid X-API-Key) triggers dispatch side effects.

        Operators with the write key can use the route as the alert
        heartbeat — the dashboard's unauthenticated poll cannot.
        """
        from app.api.security import optional_write_key
        # Override the dependency to simulate "valid write key" (True).
        self.app.dependency_overrides[optional_write_key] = lambda: True
        try:
            with patch("app.api.routes.quality_metrics.list_scored_samples_for_drift") as ls, \
                 patch("app.api.routes.quality_metrics.list_all_events", return_value=[]), \
                 patch("app.api.routes.quality_metrics.dispatch_drift_alerts") as dispatch, \
                 patch("app.api.routes.quality_metrics.evaluate_scheduler_alerts", return_value=[]):
                ls.return_value = {"recent": [], "baseline": []}
                response = self.client.get("/quality-metrics/drift")
        finally:
            self.app.dependency_overrides.pop(optional_write_key, None)
        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
