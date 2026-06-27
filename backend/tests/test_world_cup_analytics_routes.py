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
from app.models.world_cup_prediction import Base, MatchFixture
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
                stage="GROUP_STAGE",
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


if __name__ == "__main__":
    unittest.main()
