"""
Route + dashboard tests for the Event Intelligence surface (Phase 4 items 1-3).

- POST /events/analyze: exercised end-to-end through the real service with the
  LLM forced onto its deterministic fallback (_ask_ai raises) and persistence
  stubbed, so the test is network-free and deterministic. Asserts the response
  is a full event record and that the recommended action carries no trading
  vocabulary (event-conventions).
- GET /events/discover: route wiring + Query bound validation, plus a
  service-level test of discover_events orchestration (value_score sorting,
  skipping events with no selected evidence, and per-event failure isolation).
- GET /dashboard: smoke render of the real static dashboard.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import events as events_routes
import app.services.ai_analysis_service as ai
import app.services.event_intelligence_service as eis


def _events_client() -> TestClient:
    app = FastAPI()
    app.include_router(events_routes.router, prefix="/events")
    return TestClient(app)


NEWS_CONTEXT = (
    "EVIDENCE PROFILE\n"
    "direction: support\nstrength: 0.5\nconflict: 0.2\nfreshness: 0.8\n"
    "resolution_relevance: 0.5\nsource_count: 4\n"
    "news item: Reuters reports the agency confirmed the plan. quality: 0.7 relevance: 0.8\n"
    "news item: Associated Press covers the official filing. quality: 0.6 relevance: 0.75\n"
)


class AnalyzeRouteTests(unittest.TestCase):
    def test_analyze_returns_event_record_without_trading_vocab(self):
        with patch.object(ai, "_ask_ai", new=AsyncMock(side_effect=RuntimeError("no llm"))), \
                patch.object(eis, "_persist_events", new=lambda records: None), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            client = _events_client()
            resp = client.post(
                "/events/analyze",
                json={
                    "event_question": "Will the agency approve the policy before the deadline?",
                    "baseline_probability": 50,
                    "news_context": NEWS_CONTEXT,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            body["event_title"], "Will the agency approve the policy before the deadline?"
        )
        for key in (
            "event_id", "event_summary", "probability", "credibility", "impact",
            "risk", "evidence", "source", "value_score", "intelligence_report",
        ):
            self.assertIn(key, body)
        for key in ("baseline", "estimated", "change", "direction"):
            self.assertIn(key, body["probability"])

        action = body["intelligence_report"]["recommended_action"].lower()
        for banned in ("trade", "buy", "sell", "order", "position", "long", "short"):
            self.assertNotIn(banned, action)

    def test_analyze_rejects_missing_event_question(self):
        # Pydantic validation fails before the service is called - no patching needed.
        client = _events_client()
        resp = client.post("/events/analyze", json={"baseline_probability": 50})
        self.assertEqual(resp.status_code, 422)


class DiscoverRouteTests(unittest.TestCase):
    def test_discover_passes_query_params_and_returns_payload(self):
        canned = {
            "platform": "Event Intelligence Platform",
            "source": "Multi-source event discovery",
            "count": 0,
            "events": [],
        }
        with patch.object(
            events_routes, "discover_events", new=AsyncMock(return_value=canned)
        ) as mock_discover:
            client = _events_client()
            resp = client.get("/events/discover?limit=5&use_cache=false")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), canned)
        mock_discover.assert_awaited_once_with(limit=5, use_cache=False)

    def test_discover_validates_limit_bounds(self):
        client = _events_client()
        self.assertEqual(client.get("/events/discover?limit=0").status_code, 422)
        self.assertEqual(client.get("/events/discover?limit=21").status_code, 422)


class DiscoverServiceTests(unittest.TestCase):
    """discover_events orchestration, with external boundaries mocked."""

    @staticmethod
    def _candidates():
        return [
            {"question": q, "baseline_probability": 50, "volume": 0,
             "liquidity": 0, "source": {"type": "polymarket"}}
            for q in ("qA", "qB", "qC")
        ]

    def test_sorts_by_value_score_and_skips_events_without_evidence(self):
        async def fake_filtered(question, shared_articles=None):
            selected = 0 if question == "qB" else 2
            return {"context": "ctx", "summary": {"selected_count": selected}}

        async def fake_analyze(event_question, baseline_probability, news_context,
                               source, volume, liquidity):
            return {"event_id": event_question, "value_score": {"qA": 30, "qC": 70}[event_question]}

        async def run():
            with patch.object(eis, "_collect_candidate_events",
                              new=AsyncMock(return_value=self._candidates())), \
                    patch("app.services.event_collection_service.collect_shared_articles",
                          new=AsyncMock(return_value=[])), \
                    patch.object(eis, "_build_filtered_news", new=AsyncMock(side_effect=fake_filtered)), \
                    patch.object(eis, "analyze_event", new=AsyncMock(side_effect=fake_analyze)), \
                    patch.object(eis, "_persist_events", new=lambda records: None):
                return await eis.discover_events(limit=10, use_cache=False)

        result = asyncio.run(run())
        self.assertEqual(result["platform"], "Event Intelligence Platform")
        self.assertEqual(result["source"], "Multi-source event discovery")
        self.assertEqual(result["count"], 2)
        self.assertEqual([e["event_id"] for e in result["events"]], ["qC", "qA"])

    def test_one_failing_event_does_not_break_the_scan(self):
        async def fake_filtered(question, shared_articles=None):
            return {"context": "ctx", "summary": {"selected_count": 2}}

        async def fake_analyze(event_question, **kwargs):
            if event_question == "qC":
                raise RuntimeError("analysis blew up")
            return {"event_id": event_question, "value_score": 10}

        async def run():
            with patch.object(eis, "_collect_candidate_events",
                              new=AsyncMock(return_value=self._candidates())), \
                    patch("app.services.event_collection_service.collect_shared_articles",
                          new=AsyncMock(return_value=[])), \
                    patch.object(eis, "_build_filtered_news", new=AsyncMock(side_effect=fake_filtered)), \
                    patch.object(eis, "analyze_event", new=AsyncMock(side_effect=fake_analyze)), \
                    patch.object(eis, "_persist_events", new=lambda records: None):
                return await eis.discover_events(limit=10, use_cache=False)

        result = asyncio.run(run())
        self.assertEqual(result["count"], 2)
        self.assertNotIn("qC", [e["event_id"] for e in result["events"]])

    def test_candidate_missing_optional_market_fields_is_still_analyzed(self):
        candidate = {
            "question": "qOptional",
            "baseline_probability": 50,
            "source": {"type": "open_web"},
        }

        async def fake_filtered(question, shared_articles=None):
            return {"context": "ctx", "summary": {"selected_count": 1}}

        async def fake_analyze(event_question, baseline_probability, news_context,
                               source, volume, liquidity):
            self.assertIsNone(volume)
            self.assertIsNone(liquidity)
            return {"event_id": event_question, "value_score": 20}

        async def run():
            with patch.object(eis, "_collect_candidate_events",
                              new=AsyncMock(return_value=[candidate])), \
                    patch("app.services.event_collection_service.collect_shared_articles",
                          new=AsyncMock(return_value=[])), \
                    patch.object(eis, "_build_filtered_news", new=AsyncMock(side_effect=fake_filtered)), \
                    patch.object(eis, "analyze_event", new=AsyncMock(side_effect=fake_analyze)), \
                    patch.object(eis, "_persist_events", new=lambda records: None):
                return await eis.discover_events(limit=10, use_cache=False)

        result = asyncio.run(run())
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["event_id"], "qOptional")

    def test_candidate_missing_baseline_defaults_to_neutral_probability(self):
        candidate = {
            "question": "qNoBaseline",
            "source": {"type": "open_web"},
        }

        async def fake_filtered(question, shared_articles=None):
            return {"context": "ctx", "summary": {"selected_count": 1}}

        async def fake_analyze(event_question, baseline_probability, news_context,
                               source, volume, liquidity):
            self.assertEqual(baseline_probability, 50.0)
            return {"event_id": event_question, "value_score": 20}

        async def run():
            with patch.object(eis, "_collect_candidate_events",
                              new=AsyncMock(return_value=[candidate])), \
                    patch("app.services.event_collection_service.collect_shared_articles",
                          new=AsyncMock(return_value=[])), \
                    patch.object(eis, "_build_filtered_news", new=AsyncMock(side_effect=fake_filtered)), \
                    patch.object(eis, "analyze_event", new=AsyncMock(side_effect=fake_analyze)), \
                    patch.object(eis, "_persist_events", new=lambda records: None):
                return await eis.discover_events(limit=10, use_cache=False)

        result = asyncio.run(run())
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["event_id"], "qNoBaseline")

    def test_candidate_invalid_baseline_defaults_to_neutral_probability(self):
        candidates = [
            {"question": "qNoneBaseline", "baseline_probability": None},
            {"question": "qBadBaseline", "baseline_probability": "not-a-number"},
        ]
        seen = []

        async def fake_filtered(question, shared_articles=None):
            return {"context": "ctx", "summary": {"selected_count": 1}}

        async def fake_analyze(event_question, baseline_probability, news_context,
                               source, volume, liquidity):
            seen.append((event_question, baseline_probability))
            return {"event_id": event_question, "value_score": 20}

        async def run():
            with patch.object(eis, "_collect_candidate_events",
                              new=AsyncMock(return_value=candidates)), \
                    patch("app.services.event_collection_service.collect_shared_articles",
                          new=AsyncMock(return_value=[])), \
                    patch.object(eis, "_build_filtered_news", new=AsyncMock(side_effect=fake_filtered)), \
                    patch.object(eis, "analyze_event", new=AsyncMock(side_effect=fake_analyze)), \
                    patch.object(eis, "_persist_events", new=lambda records: None):
                return await eis.discover_events(limit=10, use_cache=False)

        result = asyncio.run(run())
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            seen,
            [("qNoneBaseline", 50.0), ("qBadBaseline", 50.0)],
        )

    def test_candidate_missing_or_blank_question_is_skipped(self):
        candidates = [
            {"source": {"type": "open_web"}},
            {"question": "   ", "source": {"type": "open_web"}},
        ]

        async def run():
            with patch.object(eis, "_collect_candidate_events",
                              new=AsyncMock(return_value=candidates)), \
                    patch("app.services.event_collection_service.collect_shared_articles",
                          new=AsyncMock(return_value=[])), \
                    patch.object(eis, "_build_filtered_news", new=AsyncMock()) as filtered, \
                    patch.object(eis, "analyze_event", new=AsyncMock()) as analyze, \
                    patch.object(eis, "_persist_events", new=lambda records: None):
                result = await eis.discover_events(limit=10, use_cache=False)
                return result, filtered, analyze

        result, filtered, analyze = asyncio.run(run())
        self.assertEqual(result["count"], 0)
        filtered.assert_not_awaited()
        analyze.assert_not_awaited()

    def test_cache_hits_are_returned_but_not_re_persisted(self):
        """Cached records must stay in the response but must NOT be re-audited.

        Re-auditing a cache hit appends a duplicate probability snapshot for an
        event whose probability did not change, polluting trend analysis and
        growing event_audit.jsonl without bound. Only freshly-analyzed records
        are passed to _persist_events.
        """
        from unittest.mock import Mock

        cached_record = {"event_id": "qCached", "value_score": 99}

        async def fake_filtered(question, shared_articles=None):
            return {"context": "ctx", "summary": {"selected_count": 1}}

        async def fake_analyze(event_question, **kwargs):
            return {"event_id": event_question, "value_score": 10}

        async def run():
            persisted = []

            def capture_persist(records):
                persisted.extend(records)

            with patch.object(eis, "_collect_candidate_events",
                              new=AsyncMock(return_value=self._candidates())), \
                    patch("app.services.event_collection_service.collect_shared_articles",
                          new=AsyncMock(return_value=[])), \
                    patch.object(eis, "_build_filtered_news", new=AsyncMock(side_effect=fake_filtered)), \
                    patch.object(eis, "analyze_event", new=AsyncMock(side_effect=fake_analyze)), \
                    patch.object(eis, "_persist_events", new=capture_persist), \
                    patch("app.memory.event_cache.get_cached_event") as get_cache:
                # qA is a cache hit; qB / qC are misses (qB later filtered for
                # zero evidence by fake_filtered? no - all return selected=1
                # here, so qB and qC are freshly analyzed). The mock receives
                # the raw question (the real get_cached_event normalizes it).
                def cache_lookup(question):
                    return cached_record if question == "qA" else None
                get_cache.side_effect = cache_lookup
                result = await eis.discover_events(limit=10, use_cache=True)
                return result, persisted

        result, persisted = asyncio.run(run())
        # All three appear in the response (cached + fresh).
        ids_in_response = {e["event_id"] for e in result["events"]}
        self.assertEqual(ids_in_response, {"qCached", "qB", "qC"})
        # Only the freshly-analyzed records reach persistence / audit.
        self.assertEqual(
            [r["event_id"] for r in persisted],
            ["qB", "qC"],
        )


class MoversRouteTests(unittest.TestCase):
    """GET /events/movers enriches each mover with the stored Chinese title."""

    def test_movers_include_chinese_title(self):
        histories = {
            "evt1": [
                {"timestamp": "2026-06-10T00:00:00+00:00", "estimated": 40.0,
                 "event_title": "Will X happen?"},
                {"timestamp": "2026-06-12T00:00:00+00:00", "estimated": 55.0,
                 "event_title": "Will X happen?"},
            ],
        }
        entry = {"record": {"event_title_zh": "X 会发生吗？"}}
        with patch.object(events_routes, "histories_by_event", return_value=histories), \
                patch.object(events_routes, "get_event", return_value=entry):
            client = _events_client()
            resp = client.get("/events/movers")

        self.assertEqual(resp.status_code, 200)
        movers = resp.json()["movers"]
        self.assertEqual(len(movers), 1)
        self.assertEqual(movers[0]["event_title"], "Will X happen?")
        self.assertEqual(movers[0]["event_title_zh"], "X 会发生吗？")


class CollectCandidateEventsTests(unittest.TestCase):
    """The multi-source candidate composition: round-robin interleave + pool cap."""

    # Single-token questions drawn from a wide pool, so each candidate tokenizes
    # to a disjoint token set and the cross-source dedup (which runs after
    # round-robin) never collapses them. These tests cover round-robin / cap,
    # not dedup.
    _QUESTION_POOL = [
        "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
        "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
        "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
        "victor", "whiskey", "xray", "yankee", "zulu", "avenue", "butterfly",
        "canyon", "dolphin", "ember", "falcon", "granite", "harbor", "igloo",
        "jungle", "kettle", "lantern", "marble", "nugget", "orchid", "prairie",
        "quartz", "ribbon", "saddle", "thunder", "umber", "violet", "willow",
        "xenon", "yacht",
    ]

    @staticmethod
    def _cands(platform, n, offset=0, source_type="prediction_market"):
        """n candidates for `platform`. `offset` picks a disjoint slice of the
        question pool so two calls with different offsets produce no token
        overlap (letting dedup stay out of the way)."""
        pool = CollectCandidateEventsTests._QUESTION_POOL
        return [
            {"question": pool[offset + i],
             "source": {"type": source_type, "platform": platform}}
            for i in range(n)
        ]

    def _collect(self, poly, manifold, kalshi, limit=10):
        async def run():
            with patch("app.services.polymarket_event_source.fetch_candidate_events",
                       new=AsyncMock(return_value=poly)), \
                    patch("app.services.manifold_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=manifold)), \
                    patch("app.services.kalshi_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=kalshi)):
                return await eis._collect_candidate_events(limit)
        return asyncio.run(run())

    def test_pool_capped_and_all_sources_represented(self):
        from collections import Counter
        out = self._collect(self._cands("Polymarket", 30),
                            self._cands("Manifold", 10, offset=30),
                            self._cands("Kalshi", 10, offset=40), limit=10)
        self.assertEqual(len(out), 30)  # limit(10) * _CANDIDATE_POOL_FACTOR(3)
        counts = Counter(c["source"]["platform"] for c in out)
        # Round-robin keeps each source represented rather than letting
        # Polymarket's 30 fill the whole budget.
        self.assertEqual(counts["Polymarket"], 10)
        self.assertEqual(counts["Manifold"], 10)
        self.assertEqual(counts["Kalshi"], 10)

    def test_small_pools_returned_whole(self):
        out = self._collect(self._cands("Polymarket", 2),
                            self._cands("Manifold", 2, offset=10),
                            self._cands("Kalshi", 2, offset=20), limit=10)
        self.assertEqual(len(out), 6)

    def test_failing_source_is_isolated(self):
        from collections import Counter

        async def run():
            with patch("app.services.polymarket_event_source.fetch_candidate_events",
                       new=AsyncMock(return_value=self._cands("Polymarket", 5))), \
                    patch("app.services.manifold_event_source.fetch_candidate_events",
                          new=AsyncMock(side_effect=RuntimeError("down"))), \
                    patch("app.services.kalshi_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=self._cands("Kalshi", 5, offset=10))):
                return await eis._collect_candidate_events(10)

        out = asyncio.run(run())
        counts = Counter(c["source"]["platform"] for c in out)
        self.assertNotIn("Manifold", counts)
        self.assertEqual(counts["Polymarket"], 5)
        self.assertEqual(counts["Kalshi"], 5)

    def test_open_web_source_is_interleaved(self):
        from collections import Counter

        async def run():
            with patch("app.services.polymarket_event_source.fetch_candidate_events",
                       new=AsyncMock(return_value=self._cands("Polymarket", 4))), \
                    patch("app.services.manifold_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])), \
                    patch("app.services.kalshi_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])), \
                    patch("app.services.event_extraction_service.extract_candidate_events",
                          new=AsyncMock(return_value=self._cands(
                              "Open Web", 4, offset=10, source_type="open_web"))):
                return await eis._collect_candidate_events(10, shared_articles=[{"title": "x"}])

        out = asyncio.run(run())
        counts = Counter(c["source"]["platform"] for c in out)
        self.assertEqual(counts["Polymarket"], 4)
        self.assertEqual(counts["Open Web"], 4)


class DashboardSmokeTests(unittest.TestCase):
    @staticmethod
    def _render(path):
        from app.main import app as main_app

        # Patch the scheduler so the lifespan startup is side-effect free.
        with patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None):
            with TestClient(main_app) as client:
                return client.get(path)

    def test_dashboard_serves_html(self):
        resp = self._render("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        body = resp.text
        for marker in (
            "Event Intelligence Platform",
            "Discovered Events",
            "Multi-source discovery",
            "Manual Event Analysis",
            "Tracked Events",
            "Probability History",
        ):
            self.assertIn(marker, body)
        for marker in (
            "fetch(API + path",
            "/events/discover?limit=10&use_cache=false",
            "/events/?limit=50",
            "/events/movers?limit=10",
        ):
            self.assertIn(marker, body)
        self.assertIn('href="/dashboard_zh"', body)
        self.assertIn('const API = window.location.origin + "/api";', body)
        self.assertNotIn("http://localhost:8000", body)

    def test_chinese_dashboard_serves_html(self):
        resp = self._render("/dashboard/zh")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        body = resp.text
        for marker in ("事件情报平台", "发现的事件", "多源发现", "手动事件分析", "跟踪的事件", "概率历史"):
            self.assertIn(marker, body)
        for marker in (
            "fetch(API + path",
            "/events/discover?limit=10&use_cache=false",
            "/events/?limit=50",
            "/events/movers?limit=10",
        ):
            self.assertIn(marker, body)
        self.assertIn('href="/dashboard"', body)
        self.assertIn('const API = window.location.origin + "/api";', body)
        self.assertNotIn("http://localhost:8000", body)

    def test_chinese_dashboard_underscore_alias_serves_html(self):
        resp = self._render("/dashboard_zh")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("事件情报平台", resp.text)

    def test_api_overview_lists_current_event_surface(self):
        # The machine-readable overview moved from / to /api (root now serves the
        # SPA when built; in tests there is no build so / has no route).
        resp = self._render("/api")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["system"], "Event Intelligence Platform")
        self.assertEqual(body["dashboard"], "/dashboard")
        self.assertEqual(body["dashboard_zh"], "/dashboard_zh")
        self.assertEqual(body["docs"], "/docs")
        endpoints = body["endpoints"]
        for key in (
            "event_discovery",
            "event_analysis",
            "event_list",
            "event_detail",
            "event_history",
            "event_movers",
            "event_similar",
        ):
            self.assertIn(key, endpoints)

    def test_startup_log_uses_deployment_neutral_dashboard_path(self):
        from app.main import app as main_app

        with patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None), \
                self.assertLogs("app.main", level="INFO") as logs:
            with TestClient(main_app):
                pass

        startup_logs = "\n".join(logs.output)
        self.assertIn("dashboard: /dashboard", startup_logs)
        self.assertNotIn("http://localhost:8000", startup_logs)


if __name__ == "__main__":
    unittest.main()
