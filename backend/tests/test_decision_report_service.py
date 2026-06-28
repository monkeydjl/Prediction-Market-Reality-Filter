"""Tests for decision_report_service + the M5 decision endpoints.

build_decision_report is a pure join of a prediction dict + an event record dict;
the endpoints (GET /events/decisions/open, GET /events/{id}/decision) wire it to
the stores. Loop DB is isolated via sqlite_db.loop_db_path; the event store via
event_store._store_path.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import events as events_routes
from app.memory import event_store as store
from app.memory import prediction_store as preds
from app.services.decision_report_service import build_decision_report
from app.utils import sqlite_db

# Reuse the canonical full EventRecord builder.
from tests.test_event_store import _make_record


def _prediction(**overrides):
    base = {
        "event_id": "e1", "market_probability": 50.0, "platform": "Polymarket",
        "liquidity": 1000.0, "volume": 2000.0, "raw_edge": 30.0,
        "adjusted_edge": 15.0, "trust": 0.5, "decision": "watch",
        "base_rate_category": "cpi", "status": "open",
    }
    base.update(overrides)
    return base


def _record():
    return {
        "event_title": "Will CPI be under 3%?",
        "event_summary": "Macro inflation print.",
        "probability": {"estimated": 80.0, "baseline": 50.0, "change": 30.0, "direction": "up"},
        "credibility": {"level": "MEDIUM", "score": 60, "confidence": 0.6},
        "risk": {"level": "LOW", "flags": ["thin_evidence"]},
        "intelligence_report": {"recommended_action": "Track"},
    }


class BuildDecisionReportTests(unittest.TestCase):
    def test_full_report_maps_all_sections(self):
        report = build_decision_report(_prediction(), _record())
        self.assertEqual(report["event_id"], "e1")
        self.assertEqual(report["event"]["title"], "Will CPI be under 3%?")
        self.assertEqual(report["probability"]["estimated"], 80.0)
        self.assertEqual(report["market_view"]["market_probability"], 50.0)
        self.assertEqual(report["edge"], {"raw": 30.0, "adjusted": 15.0, "trust": 0.5})
        self.assertEqual(report["confidence"]["level"], "MEDIUM")
        self.assertEqual(report["recommendation"], {"decision": "watch", "action": "Track", "calibration_status": "uncalibrated_provisional"})
        self.assertEqual(report["risk"], {"level": "LOW", "flags": ["thin_evidence"]})
        self.assertEqual(report["category"], "cpi")
        self.assertEqual(report["status"], "open")

    def test_missing_record_yields_minimal_report(self):
        report = build_decision_report(_prediction(), None)
        self.assertEqual(report["event"]["title"], "")        # no record
        self.assertIsNone(report["probability"]["estimated"])  # no record
        self.assertEqual(report["edge"]["adjusted"], 15.0)     # still from prediction
        self.assertEqual(report["recommendation"]["decision"], "watch")
        self.assertEqual(report["recommendation"]["action"], "")

    def test_report_uses_event_vocabulary_only(self):
        # The decision report must not introduce trading terms (event-conventions).
        report = build_decision_report(_prediction(), _record())
        blob = str(report).lower()
        for banned in ("long", "short", "buy", "sell", "position", "kelly", "order"):
            self.assertNotIn(banned, blob)

    def test_diagnosis_block_surfaces_frozen_inputs_and_reason(self):
        # The frozen diagnosis inputs explain the verdict. A dormant (not
        # qualified) watch row reports the sample-shortfall reason.
        pred = _prediction(qualified=False, segment_n=3, segment_min_samples=8, segment_skill=None,
                           liquidity_factor=0.2, decision="watch")
        report = build_decision_report(pred, _record())
        diag = report["diagnosis"]
        self.assertEqual(diag["qualified"], False)
        self.assertEqual(diag["segment_n"], 3)
        self.assertEqual(diag["segment_min_samples"], 8)
        self.assertEqual(diag["liquidity_factor"], 0.2)
        self.assertIn("3/8", diag["reason"])
        self.assertIn("样本不足", diag["reason"])  # dormancy named before liquidity

    def test_diagnosis_reason_act_vs_liquidity(self):
        act = build_decision_report(
            _prediction(decision="act", qualified=True, segment_n=10,
                        liquidity_factor=1.0), _record())
        self.assertIn("行动阈值", act["diagnosis"]["reason"])
        # Qualified but liquidity-discounted watch -> liquidity reason.
        liq = build_decision_report(
            _prediction(decision="watch", qualified=True, segment_n=10,
                        liquidity_factor=0.2), _record())
        self.assertIn("流动性", liq["diagnosis"]["reason"])

    def test_provisional_act_diagnosis_reason(self):
        # provisional_act: dormant but edge large -> uncalibrated provisional
        pred = _prediction(
            decision="provisional_act", qualified=False, segment_n=0,
            segment_min_samples=8, segment_skill=None, liquidity_factor=1.0,
        )
        report = build_decision_report(pred, _record())
        self.assertIn("未经校准", report["diagnosis"]["reason"])
        self.assertEqual(report["recommendation"]["calibration_status"], "uncalibrated_provisional")

    def test_act_decision_has_calibrated_status(self):
        pred = _prediction(
            decision="act", qualified=True, segment_n=10,
            segment_min_samples=8, liquidity_factor=1.0,
        )
        report = build_decision_report(pred, _record())
        self.assertEqual(report["recommendation"]["calibration_status"], "calibrated")

    def test_watch_decision_has_uncalibrated_status(self):
        pred = _prediction(decision="watch", qualified=False, segment_n=2)
        report = build_decision_report(pred, _record())
        self.assertEqual(report["recommendation"]["calibration_status"], "uncalibrated_provisional")

    def test_actionable_recommendation_passthrough_from_record(self):
        record = _record()
        record["actionable_recommendation"] = {
            "direction": "YES",
            "confidence": "high",
            "suggested_allocation_pct": 10.0,
            "edge": 15.0,
            "risk_level": "medium",
            "rationale": "Strong evidence.",
            "calibration_status": "uncalibrated_provisional",
        }
        report = build_decision_report(_prediction(), record)
        self.assertEqual(report["actionable_recommendation"]["direction"], "YES")
        self.assertEqual(report["actionable_recommendation"]["suggested_allocation_pct"], 10.0)

    def test_actionable_recommendation_calibration_status_overridden_by_qualification(self):
        # The helper in event_intelligence_service hardcodes the inner
        # calibration_status to uncalibrated_provisional (it lacks segment
        # stats). build_decision_report must override it to match the actual
        # prediction.qualified flag, so a calibrated act decision does NOT
        # show the "未经校准" tag in the frontend.
        record = _record()
        record["actionable_recommendation"] = {
            "direction": "YES", "confidence": "high",
            "suggested_allocation_pct": 10.0, "edge": 15.0,
            "risk_level": "medium", "rationale": "Strong evidence.",
            "calibration_status": "uncalibrated_provisional",  # helper default
        }
        # Calibrated act prediction: qualified=True -> inner should become "calibrated"
        pred = _prediction(decision="act", qualified=True, segment_n=10,
                           segment_min_samples=8, liquidity_factor=1.0)
        report = build_decision_report(pred, record)
        self.assertEqual(report["recommendation"]["calibration_status"], "calibrated")
        self.assertEqual(report["actionable_recommendation"]["calibration_status"], "calibrated")

    def test_actionable_recommendation_none_when_record_missing_it(self):
        report = build_decision_report(_prediction(), _record())  # _record() has no actionable_recommendation
        self.assertIsNone(report["actionable_recommendation"])

    def test_provisional_act_does_not_introduce_banned_words(self):
        # The banned-words test must still pass with provisional_act + actionable_recommendation
        record = _record()
        record["actionable_recommendation"] = {
            "direction": "YES",
            "confidence": "high",
            "suggested_allocation_pct": 10.0,
            "edge": 15.0,
            "risk_level": "medium",
            "rationale": "Strong evidence for YES.",
            "calibration_status": "uncalibrated_provisional",
        }
        pred = _prediction(
            decision="provisional_act", qualified=False, segment_n=0,
            segment_min_samples=8, liquidity_factor=1.0,
        )
        report = build_decision_report(pred, record)
        blob = str(report).lower()
        for banned in ("long", "short", "buy", "sell", "position", "kelly", "order"):
            self.assertNotIn(banned, blob, f"banned word '{banned}' found in report")

    def test_real_long_signal_rationale_does_not_introduce_banned_words(self):
        # Production path: a real event record with actionable_recommendation
        # built from a LONG signal. The rationale must not contain "long"/"short".
        # Build a record the way build_event_record() would (Task 4 path).
        from app.services.event_intelligence_service import build_event_record
        analysis = {
            "event_question": "Will X happen?",
            "market_probability": 40.0,
            "ai_probability": 55.0,
            "title_zh": "X",
            "narrative_summary": "Evidence.",
            "confidence_score": 0.7,
            "news_quality_score": 0.6,
            "evidence_strength": 0.5,
            "evidence_conflict_score": 0.2,
            "freshness_score": 0.8,
            "resolution_relevance_score": 0.5,
            "source_count": 5,
            "risk_level": "MEDIUM",
            "risk_flags": [],
            "signal": "LONG",
            "signal_direction": "LONG",
            "signal_strength": "HIGH",
            "position_size": 0.10,
            "expected_edge": 0.15,
            "divergence": 15.0,
            "base_rate_category": "test",
        }
        record = build_event_record(analysis)
        pred = _prediction(decision="provisional_act", qualified=False, segment_n=0,
                           segment_min_samples=8, liquidity_factor=1.0)
        report = build_decision_report(pred, record)
        blob = str(report).lower()
        for banned in ("long", "short", "buy", "sell", "position", "kelly", "order"):
            self.assertNotIn(banned, blob, f"banned word '{banned}' found in report with real LONG signal rationale")


class DecisionEndpointTests(unittest.TestCase):
    def _client(self):
        app = FastAPI()
        app.include_router(events_routes.router, prefix="/events")
        return TestClient(app)

    def _market_record(self, event_id, estimated):
        rec = _make_record(event_id, value_score=30, estimated=estimated)
        rec["source"] = {
            "type": "prediction_market", "platform": "Polymarket",
            "source_id": event_id, "liquidity": 10000.0, "volume": 1.0,
        }
        return rec

    def test_open_decisions_lists_ranked_reports(self):
        client = self._client()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                rec = self._market_record("op1", estimated=90.0)  # raw 40 -> dormant adj 20 -> provisional_act (cold-start bypass)
                store.save_event(rec)
                preds.freeze_prediction(rec)
                resp = client.get("/events/decisions/open")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        report = body["decisions"][0]
        self.assertEqual(report["event_id"], "op1")
        self.assertEqual(report["recommendation"]["decision"], "provisional_act")
        self.assertEqual(report["event"]["title"], rec["event_title"])  # joined from record

    def test_open_decisions_loads_event_store_once(self):
        client = self._client()
        predictions = [
            _prediction(event_id="op1", adjusted_edge=20.0),
            _prediction(event_id="op2", adjusted_edge=10.0),
        ]
        entries = [
            {"event_id": "op1", "record": _record()},
            {"event_id": "op2", "record": _record()},
        ]
        with patch.object(events_routes, "list_open_opportunities", return_value=predictions), \
                patch.object(events_routes, "list_all_events", return_value=entries) as list_all, \
                patch.object(events_routes, "get_event", side_effect=AssertionError("N+1 lookup")):
            resp = client.get("/events/decisions/open")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 2)
        self.assertEqual(list_all.call_count, 1)

    def test_event_decision_returns_report_or_404(self):
        client = self._client()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "_store_path", return_value=str(Path(tmp) / "event_store.json")), \
                    patch.object(sqlite_db, "loop_db_path", return_value=str(Path(tmp) / "v2_loop.db")):
                rec = self._market_record("ev1", estimated=90.0)
                store.save_event(rec)
                preds.freeze_prediction(rec)
                found = client.get("/events/ev1/decision")
                missing = client.get("/events/nope/decision")
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["event_id"], "ev1")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
