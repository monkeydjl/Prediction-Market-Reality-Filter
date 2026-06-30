import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import world_cup_analytics
from app.core.config import settings
from app.memory import loop_run_store
from app.models.world_cup_prediction import Base, MatchFixture, MatchPrediction
from app.utils import sqlite_db
from app.utils.prediction_db import get_prediction_session_dep


AUTH_HEADERS = {"X-API-Key": "secret"}


class WorldCupAnalyticsRouteAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fact_patch = patch.object(
            settings,
            "SPORTS_FACT_FILE",
            str(Path(self.tmp.name) / "sports_facts.json"),
        )
        self.loop_db_patch = patch.object(
            sqlite_db,
            "loop_db_path",
            return_value=str(Path(self.tmp.name) / "loop.db"),
        )
        self.fact_patch.start()
        self.loop_db_patch.start()

        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

        app = FastAPI()

        def override_session():
            yield self.session

        app.dependency_overrides[get_prediction_session_dep] = override_session
        app.include_router(world_cup_analytics.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.session.close()
        self.engine.dispose()
        self.loop_db_patch.stop()
        self.fact_patch.stop()
        self.tmp.cleanup()

    def _add_finished_fixture(self):
        self.session.add(
            MatchFixture(
                match_id="m1",
                fixture_id="m1",
                home_team="Team A",
                away_team="Team B",
                kickoff_utc=datetime(2026, 6, 20, 18, 0, 0),
                venue="Test Stadium",
                stage="group_stage",
                status="finished",
                home_score=2,
                away_score=1,
            )
        )
        self.session.commit()

    def test_analytics_post_routes_require_write_key(self):
        paths = [
            "/analytics/result-fact-backfill?dry_run=true",
            "/analytics/consistency-repair?history_ids=1&dry_run=true",
            "/analytics/reconcile-scoring",
            "/analytics/post-match-backfill?dry_run=true",
        ]

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            for path in paths:
                with self.subTest(path=path):
                    resp = self.client.post(path)
                    self.assertEqual(resp.status_code, 401)

    def test_result_fact_backfill_accepts_valid_write_key_for_dry_run(self):
        self._add_finished_fixture()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/result-fact-backfill?dry_run=true&limit=5",
                headers=AUTH_HEADERS,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["candidate_count"], 1)
        self.assertEqual(body["imported"], 0)

    def test_confirmed_result_fact_backfill_records_audit_metadata(self):
        self._add_finished_fixture()
        headers = {
            **AUTH_HEADERS,
            "X-Client-Source": "world-cup-dashboard",
            "X-Operator": "alice",
        }

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/result-fact-backfill?dry_run=false&confirm=true&limit=5",
                headers=headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["imported"], 1)
        run = loop_run_store.get_run(body["run_id"])
        self.assertEqual(run["result"]["audit_metadata"]["trigger_source"], "world-cup-dashboard")
        self.assertEqual(run["result"]["audit_metadata"]["operator"], "alice")
        self.assertEqual(
            run["result"]["audit_metadata"]["request_path"],
            "/analytics/result-fact-backfill",
        )

    def test_reconcile_scoring_records_audit_metadata(self):
        self._add_finished_fixture()
        headers = {
            **AUTH_HEADERS,
            "X-Client-Source": "world-cup-dashboard",
            "X-Operator": "charlie",
        }

        with (
            patch.object(settings, "API_WRITE_KEY", "secret"),
            patch(
                "app.api.routes.world_cup_analytics.score_all_finished_matches",
                return_value={
                    "status": "ok",
                    "total_finished": 1,
                    "scored": 0,
                    "skipped": 1,
                    "errors": 0,
                    "duration_ms": 5,
                    "run_id": "fake-run-id",
                    "audit_metadata": {
                        "trigger_source": "world-cup-dashboard",
                        "operator": "charlie",
                    },
                },
            ) as mock_score,
        ):
            resp = self.client.post(
                "/analytics/reconcile-scoring",
                headers=headers,
            )

        self.assertEqual(resp.status_code, 200)
        mock_score.assert_called_once()
        passed_meta = mock_score.call_args.kwargs.get("audit_metadata") or mock_score.call_args[1].get("audit_metadata")
        self.assertIsNotNone(passed_meta)
        self.assertEqual(passed_meta.get("trigger_source"), "world-cup-dashboard")
        self.assertEqual(passed_meta.get("operator"), "charlie")

    def test_engine_stats_includes_gbm_bucket(self):
        self.session.add(
            MatchPrediction(
                match_id="m-gbm",
                predicted_home_score=1.4,
                predicted_away_score=0.9,
                home_win_prob=0.55,
                draw_prob=0.25,
                away_win_prob=0.20,
                confidence=0.72,
                prediction_method="gbm_lightgbm",
            )
        )
        self.session.commit()

        resp = self.client.get("/analytics/engine-stats")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total_predictions"], 1)
        self.assertEqual(body["by_engine"]["gbm"]["count"], 1)
        self.assertEqual(body["by_engine"]["gbm"]["avg_confidence"], 0.72)

    def test_tournament_simulation_cache_is_keyed_by_num_simulations(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="a1",
                    fixture_id="a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="A",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="b1",
                    fixture_id="b1",
                    home_team="Team B1",
                    away_team="Team B2",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="B",
                    status="scheduled",
                ),
            ]
        )
        self.session.commit()

        def fake_simulate(*, groups, elo_cache, num_simulations):
            return {"status": "ok", "num_simulations": num_simulations}

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ) as get_elo,
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ) as simulate,
        ):
            first = self.client.get("/analytics/tournament-simulation?num_simulations=5000")
            second = self.client.get("/analytics/tournament-simulation?num_simulations=20000")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["num_simulations"], 5000)
        self.assertEqual(second.json()["num_simulations"], 20000)
        self.assertEqual(simulate.call_count, 2)
        self.assertGreaterEqual(get_elo.await_count, 4)


if __name__ == "__main__":
    unittest.main()
