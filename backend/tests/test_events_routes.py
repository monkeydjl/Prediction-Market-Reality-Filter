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
from contextlib import ExitStack
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
from app.memory import review_queue_store as rq
from app.memory import simulated_trade_store as trades
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


class _UrlResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self, _size: int) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


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

    def test_world_cup_data_source_status_requires_key_and_sanitizes_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_db = str(base / "v2_loop.db")
            # ExitStack instead of a 21-item `with`: CPython 3.11 (the version CI
            # pins) caps statically nested blocks at 20 and refuses to compile.
            settings_overrides = {
                "SPORTS_FACT_FILE": str(base / "facts.json"),
                "WORLD_CUP_DATA_FILE": str(base / "world_cup_data.json"),
                "WORLD_CUP_SOURCE_BUNDLE_FILE": str(base / "bundle.json"),
                "WORLD_CUP_SOURCE_BUNDLE_URL": "https://example.com/bundle?token=secret",
                "WORLD_CUP_MATCH_SOURCE_URL": "https://example.com/matches?token=secret",
                "WORLD_CUP_MATCH_EVENTS_SOURCE_URL": "",
                "WORLD_CUP_LINEUPS_SOURCE_URL": "",
                "WORLD_CUP_STANDINGS_SOURCE_URL": "",
                "WORLD_CUP_PLAYER_AWARDS_SOURCE_URL": "",
                "WORLD_CUP_PLAYER_STATUS_SOURCE_URL": "",
                "WORLD_CUP_STATISTICS_SOURCE_URL": "",
                "WORLD_CUP_API_FOOTBALL_API_KEY": "provider-secret",
                "WORLD_CUP_SPORTMONKS_API_TOKEN": "sportmonks-secret",
                "WORLD_CUP_SPORTMONKS_FIXTURES_URL": "https://sportmonks.example/fixtures?api_token=secret",
                "WORLD_CUP_SPORTMONKS_STANDINGS_URL": "",
                "WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL": "",
                "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED": True,
                "WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE": "feeds",
                "API_WRITE_KEY": "secret",
            }
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(sqlite_db, "loop_db_path", return_value=run_db)
                )
                for _name, _value in settings_overrides.items():
                    stack.enter_context(patch.object(settings, _name, _value))
                run_id = loop_run_store.start_run("world_cup_source_bundle_import")
                loop_run_store.finish_run(
                    run_id,
                    "success",
                    result={
                        "mode": "feeds",
                        "converted_fact_count": 1,
                        "source_feeds": [{
                            "kind": "matches",
                            "source_url": "https://example.com/matches",
                        }],
                    },
                )
                client = _events_client()
                unauthorized = client.get("/events/sports/world-cup/data/sources/status")
                resp = client.get(
                    "/events/sports/world-cup/data/sources/status",
                    headers=AUTH_HEADERS,
                )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["configured_sources"]["bundle_url"]["source_url"], "https://example.com/bundle")
        self.assertEqual(body["configured_sources"]["feeds"][0]["source_url"], "https://example.com/matches")
        self.assertTrue(body["configured_sources"]["api_football"]["configured"])
        self.assertEqual(body["configured_sources"]["api_football"]["max_detail_calls"], 100)
        self.assertTrue(body["configured_sources"]["sportmonks"]["configured"])
        self.assertEqual(
            body["configured_sources"]["sportmonks"]["feeds"][0]["source_url"],
            "https://sportmonks.example/fixtures",
        )
        self.assertTrue(body["scheduled_import"]["enabled"])
        self.assertEqual(
            body["runs"]["world_cup_source_bundle_import"]["result"]["mode"],
            "feeds",
        )
        self.assertNotIn("secret", json.dumps(body))

    def test_api_football_validate_records_failed_run(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.api.routes.events.validate_world_cup_api_football_pipeline",
                    return_value={
                        "ok": False,
                        "error": "API-Football returned 0 fixtures for league=1 season=2026",
                        "steps": [
                            {"name": "connection", "ok": True},
                            {"name": "fixture_fetch", "ok": False, "fixture_count": 0},
                        ],
                    },
                ):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/bundle/api-football/validate",
                headers=AUTH_HEADERS,
            )
            run = loop_run_store.last_run("world_cup_api_football_validate")

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(
            run["error"],
            "API-Football returned 0 fixtures for league=1 season=2026",
        )
        self.assertEqual(run["result"]["fixture_count"], 0)
        self.assertEqual(run["result"]["failed_step"], "fixture_fetch")

    def test_api_football_import_requires_successful_pipeline_validation(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.api.routes.events.import_world_cup_api_football_bundle",
                    return_value={"imported": 1},
                ) as mock_import:
            run_id = loop_run_store.start_run("world_cup_api_football_validate")
            loop_run_store.finish_run(
                run_id,
                "failed",
                result={
                    "provider": "api_football",
                    "ok": False,
                    "fixture_count": 0,
                    "failed_step": "fixture_fetch",
                },
                error="API-Football returned 0 fixtures for league=1 season=2026",
            )

            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/bundle/api-football/import?replace=true",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 409)
        self.assertIn("Latest API-Football pipeline validation failed", resp.json()["detail"])
        mock_import.assert_not_called()

    def test_api_football_import_runs_after_successful_pipeline_validation(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.api.routes.events.import_world_cup_api_football_bundle",
                    return_value={"imported": 1, "provider": "api_football"},
                ) as mock_import:
            run_id = loop_run_store.start_run("world_cup_api_football_validate")
            loop_run_store.finish_run(
                run_id,
                "success",
                result={
                    "provider": "api_football",
                    "ok": True,
                    "fixture_count": 48,
                },
            )

            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/bundle/api-football/import?replace=true",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["imported"], 1)
        mock_import.assert_called_once_with(replace=True)

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

    def test_world_cup_official_csv_source_preview_converts_without_writing_facts(self):
        payload = {
            "source": "official_csv",
            "observed_at": "2026-07-20T00:00:00Z",
            "csv": {
                "matches": (
                    "match_id,stage,kickoff_at,venue,referee,home_team,away_team,"
                    "status,home_score,away_score,winner,extra_time,penalty_shootout,"
                    "home_red_cards,away_red_cards,home_yellow_cards,away_yellow_cards\n"
                    "round16-1,round_of_16,2026-07-20T19:00:00+00:00,"
                    "\"Stadium A, City A\",Referee A,Team A,Team B,finished,1,1,"
                    "Team A,true,true,1,0,2,1\n"
                )
            },
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/official-csv/preview",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["profile"], "official_csv_v1")
        self.assertEqual(preview_resp.json()["converted_fact_count"], 1)
        self.assertEqual(preview_resp.json()["facts"][0]["yellow_cards"], 3.0)
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_official_csv_source_import_writes_facts(self):
        payload = {
            "source": "official_csv",
            "observed_at": "2026-07-20T00:00:00Z",
            "csv": {
                "matches": (
                    "match_id,stage,kickoff_at,venue,referee,home_team,away_team,"
                    "status,home_score,away_score,winner,extra_time,penalty_shootout,"
                    "home_red_cards,away_red_cards,home_yellow_cards,away_yellow_cards\n"
                    "round16-1,round_of_16,2026-07-20T19:00:00+00:00,"
                    "\"Stadium A, City A\",Referee A,Team A,Team B,finished,1,1,"
                    "Team A,true,true,1,0,2,1\n"
                )
            },
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/official-csv/import?replace=true",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=match_result")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 1)

    def test_world_cup_source_bundle_preview_converts_without_writing_facts(self):
        payload = {
            "sources": [{
                "kind": "matches",
                "payload": {
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
                },
            }, {
                "kind": "player_status",
                "payload": {
                    "source": "official_injury_feed",
                    "observed_at": "2026-06-25T00:00:00Z",
                    "response": [{
                        "player": {"name": "Player A"},
                        "team": {"name": "Brazil"},
                        "status": "out",
                        "injury": {"type": "hamstring"},
                    }],
                },
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/data/bundle/preview",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["source_count"], 2)
        self.assertEqual(preview_resp.json()["converted_fact_count"], 2)
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_source_bundle_import_writes_facts(self):
        payload = {
            "sources": [{
                "kind": "matches",
                "payload": {
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
                },
            }, {
                "kind": "player_status",
                "payload": {
                    "source": "official_injury_feed",
                    "observed_at": "2026-06-25T00:00:00Z",
                    "response": [{
                        "player": {"name": "Player A"},
                        "team": {"name": "Brazil"},
                        "status": "out",
                        "injury": {"type": "hamstring"},
                    }],
                },
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/data/bundle/import?replace=true",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["source_count"], 2)
        self.assertEqual(import_resp.json()["converted_fact_count"], 2)
        self.assertEqual(facts_resp.json()["count"], 2)

    def test_world_cup_source_bundle_invalid_payload_returns_422(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/bundle/preview",
                headers=AUTH_HEADERS,
                json={"sources": [{"kind": "odds", "payload": {}}]},
            )
        self.assertEqual(resp.status_code, 422)

    def test_world_cup_configured_source_bundle_preview_does_not_write_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bundle_path = base / "world_cup_source_bundle.json"
            facts_path = base / "facts.json"
            bundle_path.write_text(json.dumps({
                "sources": [{
                    "kind": "matches",
                    "payload": {
                        "source": "api_football",
                        "observed_at": _observed_at_now(),
                        "response": [{
                            "fixture": {"id": 1001, "status": {"short": "FT"}},
                            "teams": {
                                "home": {"name": "Team A", "winner": True},
                                "away": {"name": "Team B", "winner": False},
                            },
                            "goals": {"home": 2, "away": 0},
                        }],
                    },
                }]
            }), encoding="utf-8")
            with patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_FILE", str(bundle_path)), \
                    patch.object(settings, "SPORTS_FACT_FILE", str(facts_path)), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                client = _events_client()
                preview_resp = client.post(
                    "/events/sports/world-cup/data/bundle/source/preview",
                    headers=AUTH_HEADERS,
                )
                facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["source_count"], 1)
        self.assertEqual(preview_resp.json()["source_metadata"][0]["source"], "api_football")
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_configured_source_bundle_import_writes_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bundle_path = base / "world_cup_source_bundle.json"
            facts_path = base / "facts.json"
            bundle_path.write_text(json.dumps({
                "sources": [{
                    "kind": "matches",
                    "payload": {
                        "source": "api_football",
                        "observed_at": _observed_at_now(),
                        "response": [{
                            "fixture": {"id": 1001, "status": {"short": "FT"}},
                            "teams": {
                                "home": {"name": "Team A", "winner": True},
                                "away": {"name": "Team B", "winner": False},
                            },
                            "goals": {"home": 2, "away": 0},
                        }],
                    },
                }]
            }), encoding="utf-8")
            with patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_FILE", str(bundle_path)), \
                    patch.object(settings, "SPORTS_FACT_FILE", str(facts_path)), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                client = _events_client()
                import_resp = client.post(
                    "/events/sports/world-cup/data/bundle/source/import?replace=true",
                    headers=AUTH_HEADERS,
                )
                facts_resp = client.get("/events/sports/world-cup/facts?kind=match_result")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(import_resp.json()["source_metadata"][0]["source"], "api_football")
        self.assertEqual(facts_resp.json()["count"], 1)

    def test_world_cup_configured_source_bundle_missing_returns_404(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_FILE", str(Path(tmp) / "missing.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/bundle/source/preview",
                headers=AUTH_HEADERS,
            )
        self.assertEqual(resp.status_code, 404)

    def test_world_cup_configured_source_bundle_missing_metadata_returns_422(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "world_cup_source_bundle.json"
            bundle_path.write_text(json.dumps({
                "sources": [{
                    "kind": "matches",
                    "payload": {
                        "response": [{
                            "fixture": {"id": 1001, "status": {"short": "FT"}},
                            "teams": {
                                "home": {"name": "Team A", "winner": True},
                                "away": {"name": "Team B", "winner": False},
                            },
                        }],
                    },
                }]
            }), encoding="utf-8")
            with patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_FILE", str(bundle_path)), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                client = _events_client()
                resp = client.post(
                    "/events/sports/world-cup/data/bundle/source/preview",
                    headers=AUTH_HEADERS,
                )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("source", resp.json()["detail"])

    def test_world_cup_remote_source_bundle_preview_does_not_write_facts(self):
        body = json.dumps({
            "sources": [{
                "kind": "matches",
                "payload": {
                    "source": "api_football",
                    "observed_at": _observed_at_now(),
                    "response": [{
                        "fixture": {"id": 1001, "status": {"short": "FT"}},
                        "teams": {
                            "home": {"name": "Team A", "winner": True},
                            "away": {"name": "Team B", "winner": False},
                        },
                        "goals": {"home": 2, "away": 0},
                    }],
                },
            }]
        }).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_URL", "https://example.com/bundle?token=secret"), \
                patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch("app.services.world_cup_source_bundle.urlopen", return_value=_UrlResponse(body)):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/data/bundle/url/preview",
                headers=AUTH_HEADERS,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["source_url"], "https://example.com/bundle")
        self.assertEqual(preview_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 0)
        self.assertNotIn("secret", json.dumps(preview_resp.json()))

    def test_world_cup_remote_source_bundle_import_writes_facts(self):
        body = json.dumps({
            "sources": [{
                "kind": "matches",
                "payload": {
                    "source": "api_football",
                    "observed_at": _observed_at_now(),
                    "response": [{
                        "fixture": {"id": 1001, "status": {"short": "FT"}},
                        "teams": {
                            "home": {"name": "Team A", "winner": True},
                            "away": {"name": "Team B", "winner": False},
                        },
                        "goals": {"home": 2, "away": 0},
                    }],
                },
            }]
        }).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_URL", "https://example.com/bundle"), \
                patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch("app.services.world_cup_source_bundle.urlopen", return_value=_UrlResponse(body)):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/data/bundle/url/import?replace=true",
                headers=AUTH_HEADERS,
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=match_result")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 1)

    def test_world_cup_configured_source_feeds_preview_does_not_write_facts(self):
        body = json.dumps({
            "response": [{
                "fixture": {"id": 1001, "status": {"short": "FT"}},
                "teams": {
                    "home": {"name": "Team A", "winner": True},
                    "away": {"name": "Team B", "winner": False},
                },
                "goals": {"home": 2, "away": 0},
            }]
        }).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "WORLD_CUP_MATCH_SOURCE_URL", "https://example.com/matches?token=secret"), \
                patch.object(settings, "WORLD_CUP_MATCH_EVENTS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_LINEUPS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_PLAYER_AWARDS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_PLAYER_STATUS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_STATISTICS_SOURCE_URL", ""), \
                patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch("app.services.world_cup_source_bundle.urlopen", return_value=_UrlResponse(body)):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/data/bundle/feeds/preview",
                headers=AUTH_HEADERS,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["source_count"], 1)
        self.assertEqual(preview_resp.json()["source_feeds"][0]["source_url"], "https://example.com/matches")
        self.assertEqual(preview_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 0)
        self.assertNotIn("secret", json.dumps(preview_resp.json()))

    def test_world_cup_configured_source_feeds_import_writes_facts(self):
        body = json.dumps({
            "response": [{
                "fixture": {"id": 1001, "status": {"short": "FT"}},
                "teams": {
                    "home": {"name": "Team A", "winner": True},
                    "away": {"name": "Team B", "winner": False},
                },
                "goals": {"home": 2, "away": 0},
            }]
        }).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "WORLD_CUP_MATCH_SOURCE_URL", "https://example.com/matches"), \
                patch.object(settings, "WORLD_CUP_MATCH_EVENTS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_LINEUPS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_PLAYER_AWARDS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_PLAYER_STATUS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_STATISTICS_SOURCE_URL", ""), \
                patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch("app.services.world_cup_source_bundle.urlopen", return_value=_UrlResponse(body)):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/data/bundle/feeds/import?replace=true",
                headers=AUTH_HEADERS,
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=match_result")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 1)

    def test_world_cup_configured_source_feeds_missing_urls_returns_422(self):
        with patch.object(settings, "WORLD_CUP_MATCH_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_MATCH_EVENTS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_LINEUPS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_PLAYER_AWARDS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_PLAYER_STATUS_SOURCE_URL", ""), \
                patch.object(settings, "WORLD_CUP_STATISTICS_SOURCE_URL", ""), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/bundle/feeds/preview",
                headers=AUTH_HEADERS,
            )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("No World Cup source feed URLs", resp.json()["detail"])

    def test_world_cup_api_football_bundle_preview_does_not_write_facts(self):
        fixture_body = json.dumps({
            "errors": [],
            "response": [{
                "fixture": {"id": 1001, "status": {"short": "FT"}},
                "teams": {
                    "home": {"name": "Team A", "winner": True},
                    "away": {"name": "Team B", "winner": False},
                },
                "goals": {"home": 2, "away": 0},
            }],
        }).encode("utf-8")
        empty_body = json.dumps({"errors": [], "response": []}).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", "provider-secret"), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_FETCH_EVENTS", False), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_FETCH_LINEUPS", False), \
                patch.object(settings, "WORLD_CUP_API_FOOTBALL_FETCH_STATISTICS", False), \
                patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.services.world_cup_api_football_source.urlopen",
                    side_effect=[
                        _UrlResponse(fixture_body),
                        _UrlResponse(empty_body),
                        _UrlResponse(empty_body),
                        _UrlResponse(empty_body),
                    ],
                ):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/data/bundle/api-football/preview",
                headers=AUTH_HEADERS,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["provider"], "api_football")
        self.assertEqual(preview_resp.json()["source_count"], 1)
        self.assertEqual(preview_resp.json()["skipped_source_count"], 3)
        self.assertEqual(preview_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 0)
        self.assertNotIn("provider-secret", json.dumps(preview_resp.json()))

    def test_world_cup_football_data_preview_uses_configured_provider(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.api.routes.events.preview_world_cup_football_data_standings",
                    return_value={
                        "provider": "football_data",
                        "normalized_qualification_count": 48,
                        "source_url": "https://api.football-data.org/v4/competitions/WC/standings",
                    },
                ) as mock_preview:
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/bundle/football-data/preview",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["provider"], "football_data")
        self.assertEqual(resp.json()["normalized_qualification_count"], 48)
        mock_preview.assert_called_once_with()

    def test_world_cup_football_data_import_uses_configured_provider(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.api.routes.events.import_world_cup_football_data_standings",
                    return_value={
                        "provider": "football_data",
                        "normalized_qualification_count": 48,
                        "imported": 48,
                    },
                ) as mock_import:
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/bundle/football-data/import?replace=true",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["provider"], "football_data")
        self.assertEqual(resp.json()["imported"], 48)
        mock_import.assert_called_once_with(replace=True)

    def test_world_cup_sportmonks_bundle_preview_does_not_write_facts(self):
        fixture_body = json.dumps({
            "data": [{
                "id": 1001,
                "state": {"short_name": "FT"},
                "participants": [
                    {"id": 1, "name": "Team A", "meta": {"location": "home"}},
                    {"id": 2, "name": "Team B", "meta": {"location": "away"}},
                ],
                "scores": [
                    {"participant_id": 1, "score": {"goals": 2}},
                    {"participant_id": 2, "score": {"goals": 0}},
                ],
            }]
        }).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", "provider-secret"), \
                patch.object(settings, "WORLD_CUP_SPORTMONKS_FIXTURES_URL", "https://sportmonks.example/fixtures?include=participants"), \
                patch.object(settings, "WORLD_CUP_SPORTMONKS_STANDINGS_URL", ""), \
                patch.object(settings, "WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL", ""), \
                patch.object(settings, "API_WRITE_KEY", "secret"), \
                patch(
                    "app.services.world_cup_sportmonks_source.urlopen",
                    return_value=_UrlResponse(fixture_body),
                ):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/data/bundle/sportmonks/preview",
                headers=AUTH_HEADERS,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["provider"], "sportmonks")
        self.assertEqual(preview_resp.json()["source_count"], 1)
        self.assertEqual(preview_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 0)
        self.assertNotIn("provider-secret", json.dumps(preview_resp.json()))

    def test_world_cup_remote_source_bundle_missing_url_returns_422(self):
        with patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_URL", ""), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/data/bundle/url/preview",
                headers=AUTH_HEADERS,
            )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("not configured", resp.json()["detail"])

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

    def test_world_cup_match_events_source_preview_converts_without_writing_facts(self):
        payload = {
            "source": "api_football_events",
            "observed_at": "2026-07-20T00:00:00Z",
            "fixture": {"id": 1001},
            "response": [{
                "time": {"elapsed": 72},
                "team": {"name": "Team A"},
                "player": {"name": "Player A"},
                "type": "Card",
                "detail": "Red Card",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/match-events/preview",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["normalized_event_count"], 1)
        self.assertEqual(preview_resp.json()["facts"][0]["kind"], "discipline")
        self.assertEqual(preview_resp.json()["facts"][0]["red_cards"], 1.0)
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_match_events_source_import_writes_facts(self):
        payload = {
            "source": "api_football_events",
            "observed_at": "2026-07-20T00:00:00Z",
            "fixture": {"id": 1001},
            "response": [{
                "time": {"elapsed": 72},
                "team": {"name": "Team A"},
                "player": {"name": "Player A"},
                "type": "Card",
                "detail": "Red Card",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/match-events/import?replace=true",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=discipline")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 1)
        self.assertEqual(facts_resp.json()["facts"][0]["red_cards"], 1.0)

    def test_world_cup_lineups_source_preview_converts_without_writing_facts(self):
        payload = {
            "source": "api_football_lineups",
            "observed_at": "2026-07-20T00:00:00Z",
            "fixture": {"id": 1001},
            "response": [{
                "team": {"name": "Team A"},
                "formation": "4-3-3",
                "startXI": [{
                    "player": {"name": "Player A", "number": 10, "pos": "F"}
                }],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/lineups/preview",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["normalized_lineup_count"], 1)
        self.assertEqual(preview_resp.json()["facts"][0]["kind"], "lineup")
        self.assertEqual(preview_resp.json()["facts"][0]["formation"], "4-3-3")
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_lineups_source_import_writes_facts(self):
        payload = {
            "source": "api_football_lineups",
            "observed_at": "2026-07-20T00:00:00Z",
            "fixture": {"id": 1001},
            "response": [{
                "team": {"name": "Team A"},
                "formation": "4-3-3",
                "startXI": [{
                    "player": {"name": "Player A", "number": 10, "pos": "F"}
                }],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/lineups/import?replace=true",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=lineup")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 1)
        self.assertEqual(facts_resp.json()["facts"][0]["player"], "Player A")

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
            "source_url": "https://example.com/standings",
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

    def test_world_cup_standings_source_import_requires_source_metadata(self):
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

        self.assertEqual(import_resp.status_code, 422)
        self.assertIn("source_url", import_resp.json()["detail"])
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_standings_source_invalid_payload_returns_422(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/standings/preview",
                headers=AUTH_HEADERS,
                json={"source": "empty_feed", "response": []},
            )
        self.assertEqual(resp.status_code, 422)

    def test_world_cup_player_awards_source_preview_converts_without_writing_facts(self):
        payload = {
            "source": "api_football",
            "observed_at": "2026-07-20T00:00:00Z",
            "award": "golden_boot",
            "response": [{
                "rank": 1,
                "player": {"name": "Player A"},
                "statistics": [{
                    "team": {"name": "Team A"},
                    "goals": {"total": 7},
                }],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/player-awards/preview",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["normalized_award_count"], 1)
        self.assertEqual(preview_resp.json()["facts"][0]["goals"], 7)
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_player_awards_source_import_writes_facts(self):
        payload = {
            "source": "api_football",
            "observed_at": "2026-07-20T00:00:00Z",
            "response": [{
                "rank": 1,
                "player": {"name": "Player A"},
                "statistics": [{
                    "team": {"name": "Team A"},
                    "goals": {"total": 7},
                }],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/player-awards/import?replace=true",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=player_award")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 1)
        self.assertEqual(facts_resp.json()["facts"][0]["player"], "Player A")

    def test_world_cup_player_awards_source_invalid_payload_returns_422(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/player-awards/preview",
                headers=AUTH_HEADERS,
                json={"source": "empty_feed", "response": []},
            )
        self.assertEqual(resp.status_code, 422)

    def test_world_cup_player_status_source_preview_converts_without_writing_facts(self):
        payload = {
            "source": "official_injury_feed",
            "observed_at": "2026-06-25T00:00:00Z",
            "response": [{
                "player": {"name": "Player A"},
                "team": {"name": "Brazil"},
                "status": "out",
                "injury": {"type": "hamstring"},
                "severity": "high",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/player-status/preview",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["normalized_status_count"], 1)
        self.assertEqual(preview_resp.json()["facts"][0]["kind"], "injury")
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_player_status_source_import_writes_facts(self):
        payload = {
            "source": "official_injury_feed",
            "observed_at": "2026-06-25T00:00:00Z",
            "response": [{
                "player": {"name": "Player A"},
                "team": {"name": "Brazil"},
                "status": "out",
                "injury": {"type": "hamstring"},
                "severity": "high",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/player-status/import?replace=true",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=injury&team=Brazil")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 1)
        self.assertEqual(facts_resp.json()["facts"][0]["player"], "Player A")

    def test_world_cup_player_status_source_invalid_payload_returns_422(self):
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            resp = client.post(
                "/events/sports/world-cup/player-status/preview",
                headers=AUTH_HEADERS,
                json={"source": "empty_feed", "response": []},
            )
        self.assertEqual(resp.status_code, 422)

    def test_world_cup_statistics_source_preview_converts_without_writing_facts(self):
        payload = {
            "source": "api_football_statistics",
            "observed_at": "2026-07-20T00:00:00Z",
            "fixture": {"id": 1001},
            "response": [{
                "team": {"name": "Team A"},
                "statistics": [{"type": "Shots on Goal", "value": 5}],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            preview_resp = client.post(
                "/events/sports/world-cup/statistics/preview",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts")

        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(preview_resp.json()["normalized_team_stat_count"], 1)
        self.assertEqual(preview_resp.json()["facts"][0]["kind"], "team_stat")
        self.assertEqual(preview_resp.json()["facts"][0]["stat_value"], 5.0)
        self.assertEqual(facts_resp.json()["count"], 0)

    def test_world_cup_statistics_source_import_writes_facts(self):
        payload = {
            "source": "api_football_statistics",
            "observed_at": "2026-07-20T00:00:00Z",
            "team_stats": [{
                "team": "Team A",
                "stat_name": "shots on goal",
                "stat_value": 5,
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")), \
                patch.object(settings, "API_WRITE_KEY", "secret"):
            client = _events_client()
            import_resp = client.post(
                "/events/sports/world-cup/statistics/import?replace=true",
                headers=AUTH_HEADERS,
                json=payload,
            )
            facts_resp = client.get("/events/sports/world-cup/facts?kind=team_stat")

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["converted_fact_count"], 1)
        self.assertEqual(facts_resp.json()["count"], 1)

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
                client.get("/events/discover?limit=51", headers=AUTH_HEADERS).status_code,
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


class ResolveExpiredRouteTests(unittest.TestCase):
    def test_expired_unsettled_market_is_archived_without_outcome_or_trade_close(self):
        """Past close_time is not a market resolution."""
        from app.memory import simulated_trade_store as trades
        from app.services.event_resolve_service import reconcile_predictions

        rec = _make_record("evtExpired", estimated=70.0, value_score=50)
        rec["event_title"] = "Will X happen by July 1, 2026?"
        rec["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "source_id": "poly-expired",
            "close_time": "2026-07-01T00:00:00Z",
            "baseline_probability": 40.0,
            "liquidity": 100.0,
            "volume": 200.0,
        }
        rec["final_displayed_direction"] = "YES"
        rec["final_recommendation"] = "YES"

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(store, "_store_path", return_value=str(base / "event_store.json")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(base / "v2_loop.db")), \
                    patch.object(trades, "loop_db_path", return_value=str(base / "v2_loop.db")), \
                    patch.object(settings, "API_WRITE_KEY", "secret"):
                store.save_event(rec)
                preds.freeze_prediction(rec)
                trades.open_trade(
                    "evtExpired",
                    "Will X happen by July 1, 2026?",
                    direction="YES",
                    entry_prob=70.0,
                    market_prob=40.0,
                    decision="provisional_act",
                )

                client = _events_client()
                resp = client.post("/events/resolve-expired", headers=AUTH_HEADERS)
                self.assertEqual(resp.status_code, 200)

                saved = store.get_event("evtExpired")
                saved_record = saved["record"]
                self.assertEqual((saved_record.get("tracking") or {}).get("status"), "archived")
                self.assertNotIn("outcome", saved_record)
                self.assertNotIn("calibration", saved_record)

                healed = reconcile_predictions()
                self.assertEqual(healed, 0)
                self.assertEqual(preds.get_prediction("evtExpired")["status"], "open")
                self.assertEqual(len(trades.list_open_trades()), 1)
                self.assertEqual(trades.list_closed_trades(), [])


class WholeFilePassRouteTests(unittest.TestCase):
    """E1 (scale debt): how many whole-file event_store passes per request.

    The store file is rewritten in full by every mutating call — measured at
    237 ms for the live 3.455 MB store, on top of a 64 ms read — and the
    cross-process lock is held for the whole of it. Two endpoints amplified
    that: ``GET /events/`` read the store twice for one answer, and
    ``POST /events/resolve-expired`` did a full read-modify-write per expired
    event.

    Passes are counted at the event_store module's own ``read_json`` /
    ``read_json_strict`` / ``write_json_atomic`` bindings — the chokepoint every
    store path shares. Counting in ``file_store`` would also tally the other
    JSON stores a request touches.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.path = str(base / "event_store.json")
        self.patches = [
            patch.object(store, "_store_path", return_value=self.path),
            patch.object(sqlite_db, "loop_db_path", return_value=str(base / "v2_loop.db")),
            patch.object(settings, "API_WRITE_KEY", "secret"),
        ]
        for p in self.patches:
            p.start()
        self.client = _events_client()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmpdir.cleanup()

    def _counting(self):
        from contextlib import contextmanager

        tally = {"reads": 0, "writes": 0}
        real_read = store.read_json
        real_strict = store.read_json_strict
        real_write = store.write_json_atomic

        @contextmanager
        def _cm():
            with patch.object(store, "read_json",
                             lambda p, fb: (tally.__setitem__("reads", tally["reads"] + 1),
                                            real_read(p, fb))[1]), \
                    patch.object(store, "read_json_strict",
                                 lambda p, fb: (tally.__setitem__("reads", tally["reads"] + 1),
                                                real_strict(p, fb))[1]), \
                    patch.object(store, "write_json_atomic",
                                 lambda p, d, **kw: (tally.__setitem__("writes", tally["writes"] + 1),
                                                     real_write(p, d, **kw))[1]):
                yield tally

        return _cm()

    def _seed_expired(self, n):
        records = []
        for index in range(n):
            record = _make_record(f"exp-{index:02d}", value_score=100 - index)
            record["event_title"] = f"Will thing {index} happen by July 1, 2026?"
            record["source"] = {
                "type": "prediction_market",
                "platform": "Polymarket",
                "source_id": f"poly-{index}",
                "close_time": "2020-01-01T00:00:00Z",
            }
            records.append(record)
        store.save_events(records)

    def test_list_events_route_serves_page_and_total_from_one_read(self):
        self._seed_expired(6)
        with self._counting() as tally:
            resp = self.client.get("/events/?limit=2&exclude_expired=false")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["total"], 6)
        self.assertEqual(
            tally["reads"], 1,
            f"GET /events/ read the whole store {tally['reads']} times for one page",
        )
        self.assertEqual(tally["writes"], 0)

    def test_list_events_total_still_describes_the_filtered_scope(self):
        """One read must not be bought by widening the total to the whole store:
        the pager the dashboard draws is sized off this number."""
        self._seed_expired(6)
        store.set_tracking_bulk(["exp-00", "exp-01", "exp-02"], status="archived")

        resp = self.client.get("/events/?limit=1&status=archived&exclude_expired=false")
        body = resp.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["count"], 1)
        self.assertIn(body["events"][0]["event_id"], {"exp-00", "exp-01", "exp-02"})

    def test_resolve_expired_archives_the_whole_batch_in_one_write(self):
        self._seed_expired(7)
        with self._counting() as tally:
            resp = self.client.post("/events/resolve-expired", headers=AUTH_HEADERS)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["archived"], 7)
        self.assertEqual(
            tally["writes"], 1,
            f"resolve-expired rewrote the store {tally['writes']} times for 7 events",
        )
        archived = store.list_events(status="archived", exclude_expired=False)
        self.assertEqual(len(archived), 7)

    def test_resolve_expired_reports_only_what_the_store_wrote(self):
        """An id the store skips must not be counted as archived — the operator
        reads this number as "this many events left the active dashboard"."""
        self._seed_expired(3)
        real_bulk = store.set_tracking_bulk

        def partial(event_ids, **kwargs):
            # The store archives only the first id (e.g. the others vanished
            # between the route's read and the store's locked write).
            return real_bulk(event_ids[:1], **kwargs)

        with patch.object(events_routes, "set_tracking_bulk", partial):
            resp = self.client.post("/events/resolve-expired", headers=AUTH_HEADERS)

        body = resp.json()
        self.assertEqual(body["archived"], 1)
        self.assertIn("Archived 1 expired events", body["message"])

    def test_resolve_expired_still_writes_no_outcome(self):
        """Expiry is not settlement — the batched path must not have quietly
        gained the outcome write the per-event path was careful never to do."""
        self._seed_expired(2)
        self.client.post("/events/resolve-expired", headers=AUTH_HEADERS)
        for event_id in ("exp-00", "exp-01"):
            record = store.get_event(event_id)["record"]
            self.assertNotIn("outcome", record)
            self.assertNotIn("calibration", record)

    def test_resolve_expired_writes_nothing_when_nothing_expired(self):
        record = _make_record("future", value_score=10)
        record["event_title"] = "Will thing happen by July 1, 2099?"
        record["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "close_time": "2099-01-01T00:00:00Z",
        }
        store.save_event(record)

        with self._counting() as tally:
            resp = self.client.post("/events/resolve-expired", headers=AUTH_HEADERS)

        self.assertEqual(resp.json()["archived"], 0)
        self.assertEqual(tally["writes"], 0)


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
        self.assertEqual(body["storage"]["loop_db_schema_versions"]["predictions"], 4)
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


    def test_recent_predictions_support_offset_and_total(self):
        records = []
        timestamps = []
        for idx in range(1, 12):
            rec = _make_record(f"evtRecent{idx:02d}", estimated=70.0, value_score=30 + idx)
            rec["event_title"] = f"Recent page event {idx}"
            rec["source"] = {
                "type": "prediction_market",
                "platform": "Polymarket",
                "source_id": f"poly-recent-{idx:02d}",
                "liquidity": 1000.0,
                "volume": 5000.0,
            }
            records.append(rec)
            timestamps.append(f"2026-07-05T00:00:{idx:02d}Z")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")), \
                    patch.object(preds, "utc_now", side_effect=timestamps):
                for rec in records:
                    store.save_event(rec)
                    preds.freeze_prediction(rec)
                client = _events_client()
                resp = client.get("/events/predictions/recent?limit=10&offset=10")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 11)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["limit"], 10)
        self.assertEqual(body["offset"], 10)
        self.assertEqual([p["event_title"] for p in body["predictions"]], ["Recent page event 1"])


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
                               source, volume, liquidity, sentiment_profile=None,
                               market_quote=None, filtered_articles=None):
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
                               source, volume, liquidity, sentiment_profile=None,
                               market_quote=None, filtered_articles=None):
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
                               source, volume, liquidity, sentiment_profile=None,
                               market_quote=None, filtered_articles=None):
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
                               source, volume, liquidity, sentiment_profile=None,
                               market_quote=None, filtered_articles=None):
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

    def test_timeout_saves_partial_results_and_cancels_pending(self):
        """On timeout, already-completed candidates are saved as partial results;
        still-running tasks are cancelled. status.timeout=True marks the run.
        """
        persisted = []

        def capture_persist(records):
            persisted.extend(records)

        async def fake_filtered(question, shared_articles=None):
            if question == "qSlow":
                await asyncio.sleep(5)
            return {"context": "ctx", "summary": {"selected_count": 1}}

        async def fake_analyze(event_question, **kwargs):
            return {"event_id": event_question, "value_score": 50}

        candidates = [
            {"question": "qFast", "baseline_probability": 50, "source": {"type": "open_web"}},
            {"question": "qSlow", "baseline_probability": 50, "source": {"type": "open_web"}},
        ]

        async def run():
            with patch.object(eis, "_collect_candidate_events",
                              new=AsyncMock(return_value=candidates)), \
                    patch("app.services.event_collection_service.collect_shared_articles",
                          new=AsyncMock(return_value=[])), \
                    patch.object(eis, "_build_filtered_news", new=AsyncMock(side_effect=fake_filtered)), \
                    patch.object(eis, "analyze_event", new=AsyncMock(side_effect=fake_analyze)), \
                    patch.object(eis, "_persist_events", new=capture_persist), \
                    patch.object(eis.settings, "EVENT_DISCOVER_TIMEOUT_SECONDS", 0.5):
                return await eis.discover_events(limit=10, use_cache=False)

        result = asyncio.run(run())
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["event_id"], "qFast")
        self.assertTrue(result["status"].get("timeout"))
        self.assertEqual([r["event_id"] for r in persisted], ["qFast"])


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
        entry = {"event_id": "evt1", "record": {"event_title_zh": "X 会发生吗？"}}
        with patch.object(events_routes, "histories_by_event", return_value=histories), \
                patch.object(events_routes, "list_all_events", return_value=[entry]):
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

    def _collect(self, poly, kalshi, limit=10):
        async def run():
            with patch("app.services.polymarket_event_source.fetch_candidate_events",
                       new=AsyncMock(return_value=poly)), \
                    patch("app.services.kalshi_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=kalshi)), \
                    patch("app.services.world_cup_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])):
                return await eis._collect_candidate_events(limit)
        return asyncio.run(run())

    def test_pool_capped_and_active_sources_represented(self):
        from collections import Counter
        out = self._collect(self._cands("Polymarket", 30),
                            self._cands("Kalshi", 10, offset=40), limit=10)
        self.assertEqual(len(out), 30)  # limit(10) * _CANDIDATE_POOL_FACTOR(3)
        counts = Counter(c["source"]["platform"] for c in out)
        # Round-robin keeps each source represented rather than letting
        # Polymarket's 30 fill the whole budget. Polymarket is intentionally
        # weighted higher, while Manifold is no longer an active source.
        self.assertEqual(counts["Polymarket"], 20)
        self.assertEqual(counts["Kalshi"], 10)
        self.assertNotIn("Manifold", counts)

    def test_small_pools_returned_whole(self):
        out = self._collect(self._cands("Polymarket", 2),
                            self._cands("Kalshi", 2, offset=20), limit=10)
        self.assertEqual(len(out), 4)

    def test_failing_source_is_isolated(self):
        from collections import Counter

        async def run():
            with patch("app.services.polymarket_event_source.fetch_candidate_events",
                       new=AsyncMock(return_value=self._cands("Polymarket", 5))), \
                    patch("app.services.kalshi_event_source.fetch_candidate_events",
                          new=AsyncMock(side_effect=RuntimeError("down"))), \
                    patch("app.services.world_cup_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])):
                return await eis._collect_candidate_events(10)

        out = asyncio.run(run())
        counts = Counter(c["source"]["platform"] for c in out)
        self.assertNotIn("Manifold", counts)
        self.assertEqual(counts["Polymarket"], 5)
        self.assertNotIn("Kalshi", counts)

    def test_cancelled_source_is_isolated(self):
        """A cancelled source must be dropped like any other failure.

        asyncio.CancelledError is a BaseException, not an Exception, so
        gather(return_exceptions=True) hands it back as a result value that an
        `isinstance(result, Exception)` guard lets through. Appending it to the
        per-source lists made zip_longest try to iterate an exception object,
        which lost the whole scan rather than just the cancelled source.
        """
        from collections import Counter

        async def run():
            with patch("app.services.polymarket_event_source.fetch_candidate_events",
                       new=AsyncMock(return_value=self._cands("Polymarket", 5))), \
                    patch("app.services.kalshi_event_source.fetch_candidate_events",
                          new=AsyncMock(side_effect=asyncio.CancelledError())), \
                    patch("app.services.world_cup_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])):
                return await eis._collect_candidate_events(10)

        out = asyncio.run(run())
        counts = Counter(c["source"]["platform"] for c in out)
        self.assertEqual(counts["Polymarket"], 5)
        self.assertNotIn("Kalshi", counts)

    def test_open_web_source_is_interleaved(self):
        from collections import Counter

        async def run():
            with patch("app.services.polymarket_event_source.fetch_candidate_events",
                       new=AsyncMock(return_value=self._cands("Polymarket", 4))), \
                    patch("app.services.kalshi_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])), \
                    patch("app.services.world_cup_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])), \
                    patch("app.services.event_extraction_service.extract_candidate_events",
                          new=AsyncMock(return_value=self._cands(
                              "Open Web", 4, offset=10, source_type="open_web"))), \
                    patch.object(eis.settings, "OPEN_WEB_ENABLED", True):
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
                    patch("app.services.kalshi_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=[])), \
                    patch("app.services.world_cup_event_source.fetch_candidate_events",
                          new=AsyncMock(return_value=self._cands(
                              "2026 FIFA World Cup", 3, offset=10,
                              source_type="sports_event"))), \
                    patch("app.services.event_extraction_service.extract_candidate_events",
                          new=AsyncMock(return_value=[])), \
                    patch.object(eis.settings, "WORLD_CUP_SOURCE_ENABLED", True), \
                    patch.object(eis, "_cross_match_world_cup", side_effect=lambda c: c):
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


class BlockingSourceRouteOffloadTests(unittest.TestCase):
    """Network-bound World Cup source routes must not block the event loop.

    These handlers are ``async def`` but called synchronous helpers that do a
    blocking ``urlopen`` against API-Football / Sportmonks / Football-Data.
    A blocking call inside a coroutine runs ON the event loop thread, so one
    unresponsive upstream freezes every other in-flight request until its
    timeout expires (up to 30s), not just the caller's.

    The probe below is the real invariant: while the handler is running, the
    loop must still be able to schedule other work.
    """

    ROUTES = (
        (
            "/events/sports/world-cup/data/bundle/api-football/test",
            "test_world_cup_api_football_connection",
        ),
        (
            "/events/sports/world-cup/data/bundle/sportmonks/test",
            "test_world_cup_sportmonks_connection",
        ),
        (
            "/events/sports/world-cup/data/bundle/url/preview",
            "preview_world_cup_source_bundle_url",
        ),
        (
            "/events/sports/world-cup/data/bundle/feeds/preview",
            "preview_world_cup_source_bundle_feeds",
        ),
        (
            "/events/sports/world-cup/data/bundle/football-data/preview",
            "preview_world_cup_football_data_standings",
        ),
        (
            "/events/sports/world-cup/data/bundle/sportmonks/preview",
            "preview_world_cup_sportmonks_bundle",
        ),
    )

    def test_blocking_upstream_does_not_starve_the_event_loop(self):
        import time

        from httpx import ASGITransport, AsyncClient

        for route, helper in self.ROUTES:
            with self.subTest(route=route):
                app = FastAPI()
                app.include_router(events_routes.router, prefix="/events")

                async def _exercise():
                    ticks = 0

                    async def _heartbeat():
                        nonlocal ticks
                        # Yields constantly; only a blocked loop stops it.
                        while True:
                            await asyncio.sleep(0.005)
                            ticks += 1

                    beat = asyncio.create_task(_heartbeat())
                    transport = ASGITransport(app=app)
                    async with AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as client:
                        resp = await client.post(route, headers=AUTH_HEADERS)
                    beat.cancel()
                    return resp, ticks

                with patch.object(settings, "API_WRITE_KEY", "secret"), \
                        patch(
                            f"app.api.routes.events.{helper}",
                            side_effect=lambda *a, **k: (
                                time.sleep(0.25) or {"ok": True}
                            ),
                        ):
                    resp, ticks = asyncio.run(_exercise())

                self.assertEqual(resp.status_code, 200)
                self.assertGreater(
                    ticks,
                    5,
                    f"{helper} blocked the event loop: the heartbeat only got "
                    f"{ticks} ticks during a 0.25s upstream call",
                )


class DeleteEventStrandedRefsTests(unittest.TestCase):
    """E2: ``DELETE /events/{event_id}`` removes the JSON record and nothing else.

    No foreign key can span a JSON file and a SQLite database, and nothing prunes
    the loop-DB tables afterwards (``loop_db_maintenance`` is WAL truncation plus
    an integrity check), so every row keyed on the deleted event_id survives with
    nothing to resolve to: an open simulated trade that can never be closed, a
    pending review item that sends a human to a 404, a scored prediction that
    still enters the Brier aggregate.

    The rows are deliberately kept — a scored prediction is a calibration sample
    and cascading the delete would silently shrink the only measurement of
    whether the engine works. So the route reports the consequence instead of
    hiding it. Until this class existed the route had no automated coverage at
    all, while being reachable from the events page delete button.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.db = str(base / "v2_loop.db")
        self.patches = [
            patch.object(store, "_store_path", return_value=str(base / "event_store.json")),
            patch.object(sqlite_db, "loop_db_path", return_value=self.db),
            # simulated_trade_store holds its own `loop_db_path` binding, so the
            # patch above does not reach it and its writes would land in the real
            # backend/v2_loop.db.
            patch.object(trades, "loop_db_path", return_value=self.db),
            patch.object(settings, "API_WRITE_KEY", "secret"),
        ]
        for p in self.patches:
            p.start()
        self.client = _events_client()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmpdir.cleanup()

    def _seed(self, event_id, *, with_rows=True):
        """Save an event, and optionally the loop-DB rows a live event accrues."""
        record = _make_record(event_id, estimated=70.0)
        record["probability"]["baseline"] = 50.0
        record["source"] = {
            "type": "prediction_market",
            "platform": "Polymarket",
            "source_id": f"contract-{event_id}",
        }
        store.save_event(record)
        if not with_rows:
            return record
        preds.freeze_prediction(record)  # also seeds the verified market link
        trades.open_trade(
            event_id, "t", direction="YES", entry_prob=70.0, market_prob=50.0,
        )
        rq.enqueue_item(
            event_id=event_id, trigger="high_value_downgraded",
            severity="WARN", reason="r", context={},
        )
        return record

    def test_the_response_names_every_table_left_pointing_at_the_event(self):
        self._seed("evt-del")
        resp = self.client.delete("/events/evt-del", headers=AUTH_HEADERS)

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            body["stranded_refs"],
            {
                "predictions": 1,
                "event_market_links": 1,
                "simulated_trades": 1,
                "review_queue_items": 1,
            },
        )
        self.assertEqual(body["stranded_total"], 4)
        self.assertIsNone(store.get_event("evt-del"))

    def test_the_stranded_rows_are_deliberately_left_in_place(self):
        """The route reports; it must not cascade. A scored prediction is a
        calibration sample, so purging is an operator's decision."""
        self._seed("evt-del")
        self.client.delete("/events/evt-del", headers=AUTH_HEADERS)

        self.assertIsNotNone(preds.get_prediction("evt-del"))
        self.assertEqual(len(trades.list_open_trades()), 1)
        self.assertEqual(len(links.get_links("evt-del")), 1)

    def test_an_event_with_no_loop_rows_strands_nothing(self):
        """The guard against a count that is non-zero for every delete."""
        self._seed("evt-clean", with_rows=False)
        body = self.client.delete(
            "/events/evt-clean", headers=AUTH_HEADERS
        ).json()

        self.assertEqual(body["stranded_refs"], {})
        self.assertEqual(body["stranded_total"], 0)

    def test_the_count_covers_only_the_event_being_deleted(self):
        """Rows stranded by an earlier delete must not be re-attributed.

        This is why the count is per-event rather than a store-wide total: after
        the first delete, its rows are indistinguishable from the second's.
        """
        self._seed("evt-first")
        self._seed("evt-second")
        self.client.delete("/events/evt-first", headers=AUTH_HEADERS)

        body = self.client.delete("/events/evt-second", headers=AUTH_HEADERS).json()
        self.assertEqual(body["stranded_total"], 4)

    def test_a_missing_event_still_404s(self):
        """The census runs before the existence check; it must not swallow it."""
        resp = self.client.delete("/events/nope", headers=AUTH_HEADERS)
        self.assertEqual(resp.status_code, 404)

    def test_the_delete_requires_the_write_key(self):
        self._seed("evt-del", with_rows=False)
        resp = self.client.delete("/events/evt-del")

        self.assertEqual(resp.status_code, 401)
        self.assertIsNotNone(store.get_event("evt-del"))

    def test_an_unreadable_loop_db_does_not_block_the_delete(self):
        """The JSON record must still go. Reporting the consequence is a
        courtesy; failing the delete because the census could not run would make
        an unreadable loop DB block event maintenance entirely."""
        self._seed("evt-del", with_rows=False)
        with patch.object(sqlite_db, "loop_db_path", return_value="/nope/x.db"):
            resp = self.client.delete("/events/evt-del", headers=AUTH_HEADERS)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["stranded_total"], 0)
        self.assertIsNone(store.get_event("evt-del"))
