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
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import events as events_routes
from app.core.config import settings
from app.memory import event_market_link_store as links
from app.memory import event_store as store
from app.memory import loop_run_store
from app.memory import prediction_store as preds
from app.utils import sqlite_db
import app.services.ai_analysis_service as ai
from app.services import event_audit_service as audit
import app.services.event_intelligence_service as eis
from tests.test_event_store import _make_record


def _events_client() -> TestClient:
    app = FastAPI()
    app.include_router(events_routes.router, prefix="/events")
    return TestClient(app)


AUTH_HEADERS = {"X-API-Key": "secret"}


def _observed_at_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
                      new=AsyncMock(return_value=None)), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/analyze",
                headers=AUTH_HEADERS,
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
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/analyze",
                headers=AUTH_HEADERS,
                json={"baseline_probability": 50},
            )
        self.assertEqual(resp.status_code, 422)


class WorldCupFactRouteTests(unittest.TestCase):
    def test_import_requires_write_key(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/facts/import",
                json={"facts": [{"kind": "discipline", "red_cards": 1}]},
            )
        self.assertEqual(resp.status_code, 401)

    def test_import_status_and_list_facts(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/facts/import?replace=true",
                headers=AUTH_HEADERS,
                json={
                    "facts": [{
                        "kind": "injury",
                        "team": "Brazil",
                        "player": "Player A",
                        "status": "out",
                    }]
                },
            )
            status_resp = client.get("/events/sports/world-cup/status")
            facts_resp = client.get("/events/sports/world-cup/facts?kind=injury&team=Brazil")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["imported"], 1)
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.json()["count"], 1)
        self.assertEqual(facts_resp.status_code, 200)
        self.assertEqual(facts_resp.json()["count"], 1)
        self.assertEqual(facts_resp.json()["facts"][0]["player"], "Player A")

    def test_world_cup_data_import_converts_match_payload(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/data/import?replace=true",
                headers=AUTH_HEADERS,
                json={
                    "source": "official_feed",
                    "matches": [{
                        "match_id": "round16-1",
                        "stage": "round_of_16",
                        "home_team": "Team A",
                        "away_team": "Team B",
                        "status": "finished",
                        "penalty_shootout": True,
                    }]
                },
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=match_result")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.status_code, 200)
        self.assertEqual(facts_resp.json()["count"], 1)
        self.assertEqual(facts_resp.json()["facts"][0]["match_id"], "round16-1")

    def test_world_cup_data_import_accepts_csv_payload(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/data/import?replace=true",
                headers=AUTH_HEADERS,
                json={
                    "source": "official_csv",
                    "csv": {
                        "matches": (
                            "match_id,stage,home_team,away_team,status,penalty_shootout\n"
                            "round16-1,round_of_16,Team A,Team B,finished,true\n"
                        )
                    },
                },
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=match_result")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertTrue(facts_resp.json()["facts"][0]["penalty_shootout"])

    def test_world_cup_data_preview_converts_without_writing_facts(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/data/preview",
                headers=AUTH_HEADERS,
                json={
                    "source": "official_csv",
                    "csv": {
                        "matches": (
                            "match_id,stage,home_team,away_team,status,penalty_shootout\n"
                            "round16-1,round_of_16,Team A,Team B,finished,true\n"
                        )
                    },
                },
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["converted_fact_count"], 1)
        self.assertEqual(preview_resp.json()["facts"][0]["match_id"], "round16-1")
        self.assertEqual(facts_resp.status_code, 200)
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_data_preview_requires_write_key(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/preview",
                json={
                    "matches": [{
                        "match_id": "round16-1",
                        "home_team": "Team A",
                        "away_team": "Team B",
                    }]
                },
            )
        self.assertEqual(resp.status_code, 401)

    def test_world_cup_configured_data_preview_does_not_write_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data_path = base / "world_cup_data.json"
            facts_path = base / "facts.json"
            data_path.write_text(json.dumps({
                "source": "official_file",
                "source_url": "https://example.com/world-cup-feed",
                "observed_at": _observed_at_now(),
                "matches": [{
                    "match_id": "round16-1",
                    "home_team": "Team A",
                    "away_team": "Team B",
                    "status": "finished",
                }],
            }), encoding="utf-8")
            with patch.object(settings, "WORLD_CUP_DATA_FILE", str(data_path)), \
                    patch.object(settings, "SPORTS_FACT_FILE", str(facts_path)), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                client = _events_client()
                preview_resp = client.post(
                    "/events/sports/world-cup/data/source/preview",
                    headers=AUTH_HEADERS,
                )
                facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["converted_fact_count"], 1)
        self.assertEqual(preview_resp.json()["source_metadata"]["source"], "official_file")
        self.assertEqual(preview_resp.json()["facts"][0]["match_id"], "round16-1")
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_configured_data_import_writes_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data_path = base / "world_cup_data.json"
            facts_path = base / "facts.json"
            data_path.write_text(json.dumps({
                "source": "official_file",
                "source_url": "https://example.com/world-cup-feed",
                "observed_at": _observed_at_now(),
                "matches": [{
                    "match_id": "round16-1",
                    "home_team": "Team A",
                    "away_team": "Team B",
                    "status": "finished",
                }],
            }), encoding="utf-8")
            with patch.object(settings, "WORLD_CUP_DATA_FILE", str(data_path)), \
                    patch.object(settings, "SPORTS_FACT_FILE", str(facts_path)), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                client = _events_client()
                import_resp = client.post(
                    "/events/sports/world-cup/data/source/import?replace=true",
                    headers=AUTH_HEADERS,
                )
                facts_resp = client.get("/events/sports/world-cup/facts?kind=match_result")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(import_resp.json()["source_metadata"]["source"], "official_file")
        self.assertEqual(facts_resp.json()["count"], 1)
        self.assertEqual(facts_resp.json()["facts"][0]["match_id"], "round16-1")

    def test_world_cup_configured_data_source_missing_returns_404(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "WORLD_CUP_DATA_FILE", str(Path(tmp) / "missing.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/source/preview",
                headers=AUTH_HEADERS,
            )
        self.assertEqual(resp.status_code, 404)

    def test_world_cup_configured_data_source_missing_metadata_returns_422(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "world_cup_data.json"
            data_path.write_text(json.dumps({
                "source": "official_file",
                "matches": [{
                    "match_id": "round16-1",
                    "home_team": "Team A",
                    "away_team": "Team B",
                }],
            }), encoding="utf-8")
            with patch.object(settings, "WORLD_CUP_DATA_FILE", str(data_path)), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                client = _events_client()
                resp = client.post(
                    "/events/sports/world-cup/data/source/preview",
                    headers=AUTH_HEADERS,
                )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("observed_at", resp.json()["detail"])

    def test_world_cup_match_source_preview_converts_without_writing_facts(self):
        payload = {
            "source": "api_football",
            "observed_at": "2026-07-20T00:00:00Z",
            "response": [{
                "fixture": {"id": 1001, "status": {"short": "PEN"}},
                "league": {"round": "Round of 16"},
                "teams": {
                    "home": {"name": "Team A", "winner": True},
                    "away": {"name": "Team B", "winner": False},
                },
                "goals": {"home": 1, "away": 1},
                "score": {"penalty": {"home": 5, "away": 4}},
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/matches/preview",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["normalized_match_count"], 1)
        self.assertTrue(preview_resp.json()["facts"][0]["penalty_shootout"])
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_match_source_import_writes_facts(self):
        payload = {
            "source": "api_football",
            "observed_at": "2026-07-20T00:00:00Z",
            "response": [{
                "fixture": {"id": 1001, "status": {"short": "FT"}},
                "teams": {
                    "home": {"name": "Team A", "winner": True},
                    "away": {"name": "Team B", "winner": False},
                },
                "goals": {"home": 2, "away": 0},
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/matches/import?replace=true",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=match_result")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 1)
        self.assertEqual(facts_resp.json()["facts"][0]["winner"], "Team A")

    def test_world_cup_match_source_invalid_payload_returns_422(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/matches/preview",
                headers=AUTH_HEADERS,
                json={"source": "empty_feed", "response": []},
            )
        self.assertEqual(resp.status_code, 422)

    def test_world_cup_standings_source_preview_converts_without_writing_facts(self):
        payload = {
            "source": "api_football",
            "observed_at": "2026-06-28T00:00:00Z",
            "response": [{
                "league": {
                    "standings": [[{
                        "team": {"name": "Mexico"},
                        "group": "Group A",
                        "description": "Qualified for knockout stage",
                    }]]
                }
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/standings/preview",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["normalized_qualification_count"], 1)
        self.assertTrue(preview_resp.json()["facts"][0]["already_qualified"])
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_standings_source_import_writes_facts(self):
        payload = {
            "source": "api_football",
            "observed_at": "2026-06-28T00:00:00Z",
            "standings": [{
                "team": {"name": "Mexico"},
                "group": "Group A",
                "description": "Qualified for knockout stage",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/standings/import?replace=true",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=qualification")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 1)
        self.assertEqual(facts_resp.json()["facts"][0]["team"], "Mexico")

    def test_world_cup_standings_source_invalid_payload_returns_422(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/standings/preview",
                headers=AUTH_HEADERS,
                json={"source": "empty_feed", "response": []},
            )
        self.assertEqual(resp.status_code, 422)

    def test_world_cup_resolve_requires_write_key(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post("/events/sports/world-cup/resolve")
        self.assertEqual(resp.status_code, 401)

    def test_world_cup_resolve_passes_dry_run_and_limit(self):
        payload = {
            "status": "ok",
            "dry_run": True,
            "resolved_count": 0,
            "pending_count": 0,
            "checked_count": 0,
            "unresolved_events": 0,
            "matches": [],
        }
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(events_routes, "resolve_world_cup_events",
                             new=AsyncMock(return_value=payload)) as mock_resolve:
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/resolve?dry_run=true&limit=25",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), payload)
        mock_resolve.assert_awaited_once_with(dry_run=True, limit=25)


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
        ) as mock_discover, patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.get(
                "/events/discover?limit=5&use_cache=false",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), canned)
        mock_discover.assert_awaited_once_with(limit=5, use_cache=False)

    def test_discover_validates_limit_bounds(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            self.assertEqual(
                client.get("/events/discover?limit=0", headers=AUTH_HEADERS).status_code,
                422,
            )
            self.assertEqual(
                client.get("/events/discover?limit=21", headers=AUTH_HEADERS).status_code,
                422,
            )


class ListRouteTests(unittest.TestCase):
    def test_list_events_returns_page_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([
                    _make_record("low", value_score=10),
                    _make_record("high", value_score=90),
                    _make_record("mid", value_score=50),
                ])
                client = _events_client()
                resp = client.get("/events/?limit=1&offset=1")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["limit"], 1)
        self.assertEqual(body["offset"], 1)
        self.assertEqual(body["events"][0]["event_id"], "mid")

    def test_list_events_filters_before_paginating(self):
        fed = _make_record("fed", value_score=70, estimated=65)
        fed["event_title"] = "Federal Reserve rate cut"
        fed["legacy_analysis"] = {"base_rate_category": "monetary"}
        fed["tracking"] = {"status": "tracking", "priority": "high"}
        eth = _make_record("eth", value_score=40, estimated=85)
        eth["event_title"] = "Ethereum ETF approval"
        eth["legacy_analysis"] = {"base_rate_category": "crypto"}
        eth["tracking"] = {"status": "watching", "priority": "medium"}
        archived = _make_record("old", value_score=95, estimated=90)
        archived["event_title"] = "Archived crypto item"
        archived["legacy_analysis"] = {"base_rate_category": "crypto"}
        archived["tracking"] = {"status": "archived", "priority": "low"}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "event_store.json")
            with patch.object(store, "_store_path", return_value=path):
                store.save_events([fed, eth, archived])
                client = _events_client()
                resp = client.get(
                    "/events/?limit=1&offset=0&q=ethereum&status=watching"
                    "&category=crypto&sort=probability"
                )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["events"][0]["event_id"], "eth")

    def test_list_events_validates_filter_vocab(self):
        client = _events_client()
        self.assertEqual(client.get("/events/?status=bogus").status_code, 422)
        self.assertEqual(client.get("/events/?sort=bogus").status_code, 422)


class EventIdValidationTests(unittest.TestCase):
    def test_dynamic_event_routes_reject_invalid_event_id(self):
        client = _events_client()
        self.assertEqual(client.get("/events/%20bad").status_code, 422)
        self.assertEqual(client.get(f"/events/{'a' * 129}").status_code, 422)


class AutoResolveRouteTests(unittest.TestCase):
    def test_auto_resolve_passes_limit_and_dry_run(self):
        payload = {
            "status": "ok",
            "dry_run": True,
            "resolved_count": 1,
            "pending_count": 0,
            "checked_count": 1,
            "matches": [],
            "by_source": {},
        }
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch.object(events_routes, "auto_resolve_events",
                             new=AsyncMock(return_value=payload)) as mock_resolve:
            client = _events_client()
            resp = client.post(
                "/events/resolve/auto?limit=50&dry_run=true",
                headers={"X-API-Key": "secret"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), payload)
        mock_resolve.assert_awaited_once_with(resolved_limit=50, dry_run=True)


class PendingLinksRouteTests(unittest.TestCase):
    def test_pending_links_are_enriched_with_event_context(self):
        record = _make_record("evtLinkCtx", value_score=30)
        record["event_title"] = "Will the Fed cut rates in July 2026?"
        record["event_title_zh"] = "美联储会在 2026 年 7 月降息吗？"
        record["event_summary"] = "A policy-rate resolution event."
        record["semantics"] = {
            "resolution_criteria": "YES if FOMC lowers the target range in July 2026",
            "time_horizon": "July 2026",
            "entities": ["Fed"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(record)
                links.upsert_link(
                    "evtLinkCtx",
                    market_name="Polymarket",
                    contract_id="poly-ctx",
                    market_question="Will the Fed cut rates by July?",
                    resolution_criteria="YES if rates are lower after the July meeting",
                    link_confidence=0.83,
                    verified=False,
                )
                client = _events_client()
                resp = client.get("/events/links/pending")

        self.assertEqual(resp.status_code, 200)
        pending = resp.json()["pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["event_title_zh"], "美联储会在 2026 年 7 月降息吗？")
        self.assertEqual(
            pending[0]["event_resolution_criteria"],
            "YES if FOMC lowers the target range in July 2026",
        )
        self.assertEqual(
            pending[0]["resolution_criteria"],
            "YES if rates are lower after the July meeting",
        )


class LoopStatusRouteTests(unittest.TestCase):
    def test_loop_status_returns_run_and_core_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(store, "_store_path", return_value=str(base / "event_store.json")), \
                    patch.object(audit, "_audit_path", return_value=str(base / "event_audit.jsonl")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(base / "v2_loop.db")):
                store.save_event(_make_record("evtLoop", estimated=70.0, value_score=30))
                rec = _make_record("evtPred", estimated=80.0, value_score=40)
                rec["source"] = {
                    "type": "prediction_market",
                    "source_id": "cLoop",
                    "platform": "Polymarket",
                    "liquidity": 20000.0,
                }
                store.save_event(rec)
                preds.freeze_prediction(rec)
                run_id = loop_run_store.start_run("event_auto_resolve")
                loop_run_store.finish_run(
                    run_id,
                    "success",
                    result={"resolved_count": 1, "checked_count": 2},
                )

                client = _events_client()
                resp = client.get("/events/loop/status")
                with patch.object(settings, "API_WRITE_KEY", "secret"):
                    authed_resp = client.get(
                        "/events/loop/status",
                        headers={"X-API-Key": "secret"},
                    )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("scheduler", body)
        self.assertEqual(body["runs"]["event_auto_resolve"]["status"], "success")
        self.assertNotIn("result", body["runs"]["event_auto_resolve"])
        self.assertNotIn("error", body["runs"]["event_auto_resolve"])
        self.assertEqual(body["recent_runs"][0]["status"], "success")
        self.assertNotIn("result", body["recent_runs"][0])
        self.assertEqual(body["counts"]["events"], 2)
        self.assertEqual(body["counts"]["predictions"]["open"], 1)
        self.assertIn("calibration_n", body["counts"])
        self.assertEqual(authed_resp.status_code, 200)
        authed_body = authed_resp.json()
        self.assertEqual(
            authed_body["runs"]["event_auto_resolve"]["result"]["resolved_count"],
            1,
        )
        self.assertEqual(authed_body["recent_runs"][0]["result"]["checked_count"], 2)

    def test_loop_status_reports_dangling_cross_store_refs(self):
        rec = _make_record("ghostPred", estimated=70.0, value_score=30)
        rec["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "source_id": "poly-ghost",
            "liquidity": 1000.0,
            "volume": 5000.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                # Freeze a prediction/link without writing the JSON event. The
                # loop status should surface this cross-store dangling ref.
                preds.freeze_prediction(rec)
                client = _events_client()
                resp = client.get("/events/loop/status")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["counts"]["dangling_predictions"], 1)
        self.assertEqual(body["counts"]["dangling_links"], 1)
        self.assertEqual(body["storage"]["loop_db_schema_versions"]["predictions"], 3)
        self.assertEqual(
            body["storage"]["loop_db_schema_versions"]["event_market_links"],
            1,
        )


class RecentPredictionsRouteTests(unittest.TestCase):
    def test_recent_predictions_include_event_titles(self):
        rec = _make_record("evtRecent", estimated=70.0, value_score=30)
        rec["event_title"] = "Will the recent event resolve yes?"
        rec["event_title_zh"] = "最近事件会以 YES 结算吗？"
        rec["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "source_id": "poly-recent",
            "liquidity": 1000.0,
            "volume": 5000.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                store.save_event(rec)
                preds.freeze_prediction(rec)
                client = _events_client()
                resp = client.get("/events/predictions/recent")

        self.assertEqual(resp.status_code, 200)
        predictions = resp.json()["predictions"]
        self.assertEqual(len(predictions), 1)
        self.assertEqual(predictions[0]["event_title"], "Will the recent event resolve yes?")
        self.assertEqual(predictions[0]["event_title_zh"], "最近事件会以 YES 结算吗？")


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
                          new=AsyncMock(return_value=kalshi)), \
                    patch("app.services.world_cup_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])):
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
                          new=AsyncMock(return_value=self._cands("Kalshi", 5, offset=10))), \
                    patch("app.services.world_cup_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])):
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
                    patch("app.services.world_cup_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])), \
                    patch("app.services.event_extraction_service.extract_candidate_events",
                          new=AsyncMock(return_value=self._cands(
                              "Open Web", 4, offset=10, source_type="open_web"))):
                return await eis._collect_candidate_events(10, shared_articles=[{"title": "x"}])

        out = asyncio.run(run())
        counts = Counter(c["source"]["platform"] for c in out)
        self.assertEqual(counts["Polymarket"], 4)
        self.assertEqual(counts["Open Web"], 4)

    def test_world_cup_source_is_interleaved(self):
        from collections import Counter

        async def run():
            with patch("app.services.polymarket_event_source.fetch_candidate_events",
                       new=AsyncMock(return_value=self._cands("Polymarket", 3))), \
                    patch("app.services.manifold_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])), \
                    patch("app.services.kalshi_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])), \
                    patch("app.services.world_cup_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=self._cands(
                              "2026 FIFA World Cup", 3, offset=10,
                              source_type="sports_event"))), \
                    patch("app.services.event_extraction_service.extract_candidate_events",
                          new=AsyncMock(return_value=[])):
                return await eis._collect_candidate_events(10)

        out = asyncio.run(run())
        counts = Counter(c["source"]["platform"] for c in out)
        self.assertEqual(counts["Polymarket"], 3)
        self.assertEqual(counts["2026 FIFA World Cup"], 3)


class DashboardSmokeTests(unittest.TestCase):
    @staticmethod
    def _render(path):
        from app.main import app as main_app

        # Patch the scheduler so the lifespan startup is side-effect free, and
        # give the lifespan a write key so its fail-closed guard (empty key +
        # no ALLOW_OPEN_WRITES => refuse boot) does not trip during the test.
        with patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch.object(settings, "API_WRITE_KEY", "test-key"):
            with TestClient(main_app) as client:
                return client.get(path)

    def test_api_overview_lists_current_event_surface(self):
        # The machine-readable overview lives at /api (root serves the Next.js
        # SPA when built; in tests there is no build so / has no route). After
        # the legacy trading layer was removed, the overview is V2-only.
        resp = self._render("/api")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["system"], "Event Intelligence Platform")
        self.assertEqual(body["docs"], "/docs")
        # No legacy dashboard pointers anymore.
        self.assertNotIn("dashboard", body)
        endpoints = body["endpoints"]
        for key in (
            "event_discovery",
            "event_analysis",
            "event_list",
            "event_detail",
            "event_history",
            "event_movers",
            "event_similar",
            "event_calibration",
            "prediction_calibration",
        ):
            self.assertIn(key, endpoints)
        # No legacy endpoints leak into the overview.
        for legacy_key in ("full_scan", "manual_analysis", "open_trade", "markets"):
            self.assertNotIn(legacy_key, endpoints)

    def test_startup_log_is_deployment_neutral(self):
        from app.main import app as main_app

        with patch("app.main.start_scheduler", lambda: None), \
                patch("app.main.stop_scheduler", lambda: None), \
                patch("app.main.sqlite_db.maintain", return_value={"ok": True}), \
                patch.object(settings, "API_WRITE_KEY", "test-key"), \
                self.assertLogs("app.main", level="INFO") as logs:
            with TestClient(main_app):
                pass

        startup_logs = "\n".join(logs.output)
        self.assertIn("EIP v0.3.0 starting", startup_logs)
        self.assertNotIn("http://localhost:8000", startup_logs)


if __name__ == "__main__":
    unittest.main()
