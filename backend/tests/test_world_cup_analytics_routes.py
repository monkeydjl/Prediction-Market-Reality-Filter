import tempfile
import unittest
from datetime import datetime, timezone
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
from app.models.world_cup_prediction import Base, MatchFixture, MatchPrediction, MatchResult
from app.services.sports_fact_service import (
    WORLD_CUP_TOURNAMENT,
    import_sports_facts,
    load_sports_facts,
)
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
            "/analytics/verified-result-correction",
        ]

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            for path in paths:
                with self.subTest(path=path):
                    resp = self.client.post(path, json={})
                    self.assertEqual(resp.status_code, 401)

    def test_verified_result_correction_requires_confirmation_and_source(self):
        self.session.add_all([
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            ),
            MatchFixture(
                match_id="verify-group-a",
                fixture_id="verify-group-a",
                home_team="Switzerland",
                away_team="Team A2",
                kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_A",
                status="finished",
                home_score=1,
                away_score=0,
            ),
            MatchFixture(
                match_id="verify-group-b",
                fixture_id="verify-group-b",
                home_team="Colombia",
                away_team="Team B2",
                kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_B",
                status="finished",
                home_score=1,
                away_score=0,
            ),
        ])
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            unconfirmed = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 1,
                    "away_score": 0,
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": False,
                },
            )
            missing_source = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 1,
                    "away_score": 0,
                    "confirmed": True,
                },
            )

        self.assertEqual(unconfirmed.status_code, 200)
        self.assertEqual(unconfirmed.json()["status"], "protected")
        self.assertEqual(missing_source.status_code, 422)
        fixture = self.session.query(MatchFixture).filter_by(match_id="verify-r16").first()
        self.assertEqual(fixture.status, "scheduled")
        self.assertIsNone(fixture.home_score)
        self.assertIsNone(fixture.away_score)

    def test_verified_result_correction_updates_fixture_scores_result_and_fact(self):
        self.session.add_all([
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            ),
            MatchPrediction(
                match_id="verify-r16",
                predicted_home_score=1.2,
                predicted_away_score=0.8,
                home_win_prob=0.45,
                draw_prob=0.30,
                away_win_prob=0.25,
                confidence=0.62,
                prediction_method="elo_odds_fusion",
            ),
        ])
        self.session.commit()

        headers = {
            **AUTH_HEADERS,
            "X-Client-Source": "world-cup-dashboard",
            "X-Operator": "alice",
        }
        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/verified-result-correction",
                headers=headers,
                json={
                    "match_id": "verify-r16",
                    "home_score": 1,
                    "away_score": 0,
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "notes": "Final score verified from official match centre.",
                    "confirmed": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["fixture"]["status"], "finished")
        self.assertEqual(body["fixture"]["score"], {"home": 1, "away": 0})
        self.assertEqual(body["scoring"]["status"], "scored")
        self.assertEqual(body["fact_import"]["imported"], 1)

        fixture = self.session.query(MatchFixture).filter_by(match_id="verify-r16").first()
        self.assertEqual(fixture.status, "finished")
        self.assertEqual(fixture.home_score, 1)
        self.assertEqual(fixture.away_score, 0)

        result = self.session.query(MatchResult).filter_by(match_id="verify-r16").first()
        self.assertIsNotNone(result)
        self.assertEqual(result.final_home_score, 1)
        self.assertEqual(result.final_away_score, 0)
        self.assertEqual(result.outcome, "home_win")

        facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="match_result")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["fact_id"], "wc2026:verified-result:verify-r16")
        self.assertEqual(facts[0]["match_id"], "verify-r16")
        self.assertEqual(facts[0]["score"], {"home": 1, "away": 0})
        self.assertEqual(facts[0]["source"], "FIFA match centre")
        self.assertEqual(facts[0]["source_url"], "https://www.fifa.com/en/match-centre/test")
        self.assertIn("operator=alice", facts[0]["notes"])

    def test_verified_result_correction_records_penalty_winner_fact(self):
        self.session.add(
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            )
        )
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 0,
                    "away_score": 0,
                    "winner": "Switzerland",
                    "penalty_score": {"home": 4, "away": 3},
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "notes": "0-0 after extra time; Switzerland won 4-3 on penalties.",
                    "confirmed": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["fixture"]["score"], {"home": 0, "away": 0})
        self.assertEqual(body["fact"]["winner"], "Switzerland")
        self.assertEqual(body["fact"]["penalty_score"], {"home": 4, "away": 3})

        facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="match_result")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["score"], {"home": 0, "away": 0})
        self.assertEqual(facts[0]["winner"], "Switzerland")
        self.assertEqual(facts[0]["penalty_score"], {"home": 4, "away": 3})

    def test_verified_result_correction_removes_stale_fixture_readiness_blocker(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all([
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            ),
            MatchFixture(
                match_id="verify-group-a",
                fixture_id="verify-group-a",
                home_team="Switzerland",
                away_team="Team A2",
                kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_A",
                status="finished",
                home_score=1,
                away_score=0,
            ),
            MatchFixture(
                match_id="verify-group-b",
                fixture_id="verify-group-b",
                home_team="Colombia",
                away_team="Team B2",
                kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_B",
                status="finished",
                home_score=1,
                away_score=0,
            ),
        ])
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            correction = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 1,
                    "away_score": 0,
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": True,
                },
            )

        self.assertEqual(correction.status_code, 200)

        def fake_knockout(*, fixtures, elo_cache, num_simulations, odds_cache=None):
            return {
                "status": "ok",
                "simulation_basis": "knockout_fixtures",
                "win_probability": {"Switzerland": 1.0},
                "reach_final": {"Switzerland": 1.0},
                "reach_semifinal": {"Switzerland": 1.0},
                "most_likely_winner": "Switzerland",
                "most_likely_winner_prob": 1.0,
            }

        with (
            patch(
                "app.api.routes.world_cup_analytics._utcnow",
                return_value=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            ),
            patch(
                "app.services.world_cup_data_source_status_service.world_cup_data_source_status",
                return_value={
                    "real_data_readiness": {
                        "ok": True,
                        "issues": [],
                        "issue_details": [],
                    }
                },
            ),
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_remaining_knockout",
                side_effect=fake_knockout,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        readiness = resp.json()["real_data_readiness"]
        self.assertTrue(readiness["ok"])
        self.assertNotIn("stale_unfinished_knockout_fixture", readiness["issues"])

    def test_tournament_simulation_uses_verified_penalty_winner_fact(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all([
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            ),
            MatchFixture(
                match_id="verify-group-a",
                fixture_id="verify-group-a",
                home_team="Switzerland",
                away_team="Team A2",
                kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_A",
                status="finished",
                home_score=1,
                away_score=0,
            ),
            MatchFixture(
                match_id="verify-group-b",
                fixture_id="verify-group-b",
                home_team="Colombia",
                away_team="Team B2",
                kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="GROUP_B",
                status="finished",
                home_score=1,
                away_score=0,
            ),
        ])
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            correction = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 0,
                    "away_score": 0,
                    "winner": "Switzerland",
                    "penalty_score": {"home": 4, "away": 3},
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": True,
                },
            )
        self.assertEqual(correction.status_code, 200)

        seen_fixtures = []

        def fake_knockout(*, fixtures, elo_cache, num_simulations, odds_cache=None):
            seen_fixtures.extend(fixtures)
            return {
                "status": "ok",
                "simulation_basis": "knockout_fixtures",
                "win_probability": {"Switzerland": 1.0},
                "reach_final": {"Switzerland": 1.0},
                "reach_semifinal": {"Switzerland": 1.0},
                "most_likely_winner": "Switzerland",
                "most_likely_winner_prob": 1.0,
            }

        with (
            patch(
                "app.api.routes.world_cup_analytics._utcnow",
                return_value=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            ),
            patch(
                "app.services.world_cup_data_source_status_service.world_cup_data_source_status",
                return_value={
                    "real_data_readiness": {
                        "ok": True,
                        "issues": [],
                        "issue_details": [],
                    }
                },
            ),
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_remaining_knockout",
                side_effect=fake_knockout,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["real_data_readiness"]["ok"])
        self.assertEqual(seen_fixtures[0]["winner"], "Switzerland")
        self.assertEqual(seen_fixtures[0]["penalty_score"], {"home": 4, "away": 3})

    def test_tied_finished_knockout_without_winner_blocks_trusted_simulation(self):
        with patch(
            "app.api.routes.world_cup_analytics._current_world_cup_real_data_readiness",
            return_value={"ok": True, "issues": [], "issue_details": []},
        ):
            readiness = world_cup_analytics._simulation_real_data_readiness(
                [
                    {
                        "match_id": "verify-r16",
                        "stage": "ROUND_OF_16",
                        "status": "finished",
                        "home_team": "Switzerland",
                        "away_team": "Colombia",
                        "home_score": 0,
                        "away_score": 0,
                    }
                ],
                now=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(readiness["ok"])
        self.assertIn("ambiguous_finished_knockout_fixture", readiness["issues"])
        self.assertEqual(
            readiness["issue_details"][0]["match_id"],
            "verify-r16",
        )

    def test_tied_finished_knockout_without_penalty_score_blocks_trusted_simulation(self):
        with patch(
            "app.api.routes.world_cup_analytics._current_world_cup_real_data_readiness",
            return_value={"ok": True, "issues": [], "issue_details": []},
        ):
            readiness = world_cup_analytics._simulation_real_data_readiness(
                [
                    {
                        "match_id": "verify-r16",
                        "stage": "ROUND_OF_16",
                        "status": "finished",
                        "home_team": "Switzerland",
                        "away_team": "Colombia",
                        "home_score": 0,
                        "away_score": 0,
                        "winner": "Switzerland",
                    }
                ],
                now=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(readiness["ok"])
        self.assertIn("ambiguous_finished_knockout_fixture", readiness["issues"])

    def test_verified_result_correction_rejects_tied_knockout_without_penalty_score(self):
        self.session.add(
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            )
        )
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 0,
                    "away_score": 0,
                    "winner": "Switzerland",
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["reason"], "knockout_draw_requires_penalty_score")

    def test_tied_finished_knockout_with_mismatched_penalty_winner_blocks_trusted_simulation(self):
        with patch(
            "app.api.routes.world_cup_analytics._current_world_cup_real_data_readiness",
            return_value={"ok": True, "issues": [], "issue_details": []},
        ):
            readiness = world_cup_analytics._simulation_real_data_readiness(
                [
                    {
                        "match_id": "verify-r16",
                        "stage": "ROUND_OF_16",
                        "status": "finished",
                        "home_team": "Switzerland",
                        "away_team": "Colombia",
                        "home_score": 0,
                        "away_score": 0,
                        "winner": "Switzerland",
                        "penalty_score": {"home": 3, "away": 4},
                    }
                ],
                now=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(readiness["ok"])
        self.assertIn("ambiguous_finished_knockout_fixture", readiness["issues"])

    def test_verified_result_correction_rejects_mismatched_penalty_winner(self):
        self.session.add(
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            )
        )
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 0,
                    "away_score": 0,
                    "winner": "Switzerland",
                    "penalty_score": {"home": 3, "away": 4},
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["reason"], "winner_must_match_penalty_score")

    def test_finished_knockout_with_winner_mismatching_score_blocks_trusted_simulation(self):
        with patch(
            "app.api.routes.world_cup_analytics._current_world_cup_real_data_readiness",
            return_value={"ok": True, "issues": [], "issue_details": []},
        ):
            readiness = world_cup_analytics._simulation_real_data_readiness(
                [
                    {
                        "match_id": "verify-r16",
                        "stage": "ROUND_OF_16",
                        "status": "finished",
                        "home_team": "Switzerland",
                        "away_team": "Colombia",
                        "home_score": 2,
                        "away_score": 0,
                        "winner": "Colombia",
                    }
                ],
                now=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(readiness["ok"])
        self.assertIn("inconsistent_finished_knockout_fixture", readiness["issues"])

    def test_verified_result_correction_rejects_winner_mismatching_score(self):
        self.session.add(
            MatchFixture(
                match_id="verify-r16",
                fixture_id="verify-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            )
        )
        self.session.commit()

        with patch.object(settings, "API_WRITE_KEY", "secret"):
            resp = self.client.post(
                "/analytics/verified-result-correction",
                headers=AUTH_HEADERS,
                json={
                    "match_id": "verify-r16",
                    "home_score": 2,
                    "away_score": 0,
                    "winner": "Colombia",
                    "source": "FIFA match centre",
                    "source_url": "https://www.fifa.com/en/match-centre/test",
                    "confirmed": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["reason"], "winner_must_match_score")

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

    def test_post_match_backfill_response_includes_result_fact_backfill(self):
        headers = {
            **AUTH_HEADERS,
            "X-Client-Source": "world-cup-dashboard",
            "X-Operator": "dana",
        }
        service_result = {
            "status": "ok",
            "dry_run": False,
            "source": "football-data",
            "candidate_count": 1,
            "candidates": [],
            "scoring": {"scored": 1, "skipped": 0, "errors": 0},
            "quality": {"samples": 1},
            "result_fact_backfill": {
                "status": "ok",
                "dry_run": False,
                "candidate_count": 1,
                "imported": 1,
            },
        }

        with (
            patch.object(settings, "API_WRITE_KEY", "secret"),
            patch(
                "app.api.routes.world_cup_analytics.run_post_match_backfill",
                return_value=service_result,
            ) as mock_backfill,
        ):
            resp = self.client.post(
                "/analytics/post-match-backfill?dry_run=false",
                headers=headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["result_fact_backfill"]["imported"], 1)
        mock_backfill.assert_called_once()
        self.assertFalse(mock_backfill.call_args.kwargs["dry_run"])
        self.assertTrue(mock_backfill.call_args.kwargs["sync_first"])
        self.assertEqual(mock_backfill.call_args.kwargs["audit_metadata"]["operator"], "dana")

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

    def test_prediction_coverage_reports_scheduled_matches_missing_predictions(self):
        self.session.add_all([
            MatchFixture(
                match_id="m-covered",
                fixture_id="m-covered",
                home_team="Covered A",
                away_team="Covered B",
                kickoff_utc=datetime(2026, 7, 10, 18, 0, 0),
                venue="Test Stadium",
                stage="quarterfinal",
                status="scheduled",
            ),
            MatchFixture(
                match_id="m-missing",
                fixture_id="m-missing",
                home_team="Missing A",
                away_team="Missing B",
                kickoff_utc=datetime(2026, 7, 11, 18, 0, 0),
                venue="Test Stadium",
                stage="quarterfinal",
                status="scheduled",
            ),
            MatchPrediction(
                match_id="m-covered",
                predicted_home_score=1.4,
                predicted_away_score=0.9,
                home_win_prob=0.55,
                draw_prob=0.25,
                away_win_prob=0.20,
                confidence=0.72,
                prediction_method="elo_only",
            ),
        ])
        self.session.commit()

        resp = self.client.get("/analytics/prediction-coverage")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["coverage_ok"])
        self.assertEqual(body["scheduled_count"], 2)
        self.assertEqual(body["predicted_count"], 1)
        self.assertEqual(body["missing_count"], 1)
        self.assertEqual(body["missing_predictions"][0]["match_id"], "m-missing")
        self.assertEqual(body["missing_predictions"][0]["home_team"], "Missing A")

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

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
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
            second = self.client.get("/analytics/tournament-simulation?num_simulations=8000")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["num_simulations"], 5000)
        self.assertEqual(second.json()["num_simulations"], 8000)
        self.assertEqual(simulate.call_count, 2)
        self.assertGreaterEqual(get_elo.await_count, 4)

    def test_tournament_simulation_force_refresh_bypasses_cached_result(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="force-a1",
                    fixture_id="force-a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="A",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="force-b1",
                    fixture_id="force-b1",
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

        calls = []

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            calls.append(num_simulations)
            return {"status": "ok", "call_index": len(calls)}

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ) as simulate,
        ):
            first = self.client.get("/analytics/tournament-simulation?num_simulations=5000")
            second = self.client.get("/analytics/tournament-simulation?num_simulations=5000")
            refreshed = self.client.get(
                "/analytics/tournament-simulation?num_simulations=5000&force_refresh=true"
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(first.json()["call_index"], 1)
        self.assertEqual(second.json()["call_index"], 1)
        self.assertTrue(second.json()["cached"])
        self.assertEqual(refreshed.json()["call_index"], 2)
        self.assertFalse(refreshed.json()["cached"])
        self.assertEqual(simulate.call_count, 2)

    def test_tournament_simulation_cached_result_refreshes_real_data_readiness(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="cache-ready-a1",
                    fixture_id="cache-ready-a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="A",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="cache-ready-b1",
                    fixture_id="cache-ready-b1",
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

        readiness_values = [
            {
                "real_data_readiness": {
                    "ok": False,
                    "issues": ["last_import_failed"],
                    "issue_details": [{"code": "last_import_failed"}],
                }
            },
            {
                "real_data_readiness": {
                    "ok": True,
                    "issues": [],
                    "issue_details": [],
                }
            },
        ]

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            return {"status": "ok", "call_index": 1}

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ) as simulate,
            patch(
                "app.services.world_cup_data_source_status_service.world_cup_data_source_status",
                side_effect=readiness_values,
            ),
        ):
            first = self.client.get("/analytics/tournament-simulation?num_simulations=5000")
            second = self.client.get("/analytics/tournament-simulation?num_simulations=5000")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.json()["real_data_readiness"]["ok"])
        self.assertTrue(second.json()["cached"])
        self.assertTrue(second.json()["real_data_readiness"]["ok"])
        self.assertEqual(simulate.call_count, 1)

    def test_tournament_simulation_accepts_uppercase_group_stage_fixtures(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="upper-a1",
                    fixture_id="upper-a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_A",
                    status="finished",
                    home_score=1,
                    away_score=0,
                ),
                MatchFixture(
                    match_id="upper-b1",
                    fixture_id="upper-b1",
                    home_team="Team B1",
                    away_team="Team B2",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_B",
                    status="finished",
                    home_score=2,
                    away_score=1,
                ),
            ]
        )
        self.session.commit()

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            return {
                "status": "ok",
                "groups_seen": groups,
                "teams_seen": sorted(elo_cache),
                "num_simulations": num_simulations,
            }

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertNotIn("error", body)
        self.assertEqual(set(body["groups_seen"]), {"GROUP_A", "GROUP_B"})
        self.assertEqual(body["groups_seen"]["GROUP_A"], ["Team A1", "Team A2"])
        self.assertEqual(body["groups_seen"]["GROUP_B"], ["Team B1", "Team B2"])

    def test_tournament_simulation_includes_real_data_readiness(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="ready-a1",
                    fixture_id="ready-a1",
                    home_team="Team A1",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="group_stage",
                    group="A",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="ready-b1",
                    fixture_id="ready-b1",
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

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            return {"status": "ok", "win_probability": {"Team A1": 0.5}}

        with (
            patch.object(settings, "WORLD_CUP_STANDINGS_SOURCE_URL", ""),
            patch.object(settings, "WORLD_CUP_API_FOOTBALL_API_KEY", ""),
            patch.object(settings, "FOOTBALL_DATA_API_KEY", ""),
            patch.object(settings, "WORLD_CUP_SPORTMONKS_API_TOKEN", ""),
            patch.object(settings, "WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED", False),
            patch(
                "app.services.world_cup_data_source_status_service.loop_run_store.last_run",
                return_value={"status": "failed", "error": "not configured"},
            ),
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000")

        self.assertEqual(resp.status_code, 200)
        readiness = resp.json()["real_data_readiness"]
        self.assertFalse(readiness["ok"])
        self.assertIn("qualification_facts_missing", readiness["issues"])
        self.assertEqual(
            readiness["issue_details"][0]["message"],
            "尚未配置真实积分榜/出线数据源",
        )

    def test_tournament_simulation_excludes_knockout_match_result_losers(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="real-a1",
                    fixture_id="real-a1",
                    home_team="Germany",
                    away_team="Paraguay",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_A",
                    status="finished",
                    home_score=0,
                    away_score=1,
                ),
                MatchFixture(
                    match_id="real-b1",
                    fixture_id="real-b1",
                    home_team="Canada",
                    away_team="Morocco",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_B",
                    status="finished",
                    home_score=0,
                    away_score=1,
                ),
            ]
        )
        self.session.commit()
        import_sports_facts(
            [
                {
                    "kind": "match_result",
                    "stage": "ROUND_OF_32",
                    "home_team": "Germany",
                    "away_team": "Paraguay",
                    "score": {"home": 4, "away": 5},
                    "observed_at": "2026-06-30T02:26:08Z",
                },
                {
                    "kind": "match_result",
                    "stage": "ROUND_OF_16",
                    "home_team": "Canada",
                    "away_team": "Morocco",
                    "score": {"home": 0, "away": 3},
                    "observed_at": "2026-07-04T23:33:28Z",
                },
            ],
            replace=True,
        )

        def fake_simulate(*, groups, elo_cache, num_simulations, eliminated_teams=None):
            return {
                "status": "ok",
                "eliminated_seen": sorted(eliminated_teams or []),
                "teams_seen": sorted(elo_cache),
            }

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                side_effect=fake_simulate,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["eliminated_seen"], ["Canada", "Germany"])
        self.assertEqual(body["qualification_state"]["eliminated_teams"], ["Canada", "Germany"])
        self.assertEqual(body["qualification_state"]["knockout_result_fact_count"], 2)
        self.assertNotIn("Canada", body["teams_seen"])
        self.assertNotIn("Germany", body["teams_seen"])

    def test_tournament_simulation_uses_knockout_fixture_bracket_after_round_of_16_exists(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="r16-1",
                    fixture_id="r16-1",
                    home_team="Canada",
                    away_team="Morocco",
                    kickoff_utc=datetime(2026, 7, 4, 17, 0, 0),
                    venue="Test Stadium",
                    stage="ROUND_OF_16",
                    status="finished",
                    home_score=0,
                    away_score=3,
                ),
                MatchFixture(
                    match_id="r16-2",
                    fixture_id="r16-2",
                    home_team="Portugal",
                    away_team="Spain",
                    kickoff_utc=datetime(2026, 7, 6, 19, 0, 0),
                    venue="Test Stadium",
                    stage="ROUND_OF_16",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="group-a",
                    fixture_id="group-a",
                    home_team="Canada",
                    away_team="Spain",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_A",
                    status="finished",
                    home_score=0,
                    away_score=1,
                ),
                MatchFixture(
                    match_id="group-b",
                    fixture_id="group-b",
                    home_team="Portugal",
                    away_team="Morocco",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_B",
                    status="finished",
                    home_score=0,
                    away_score=1,
                ),
            ]
        )
        self.session.commit()

        def fake_knockout(*, fixtures, elo_cache, num_simulations, odds_cache=None):
            return {
                "status": "ok",
                "simulation_basis": "knockout_fixtures",
                "fixture_teams": [
                    (fixture["home_team"], fixture["away_team"], fixture["status"])
                    for fixture in fixtures
                ],
                "num_simulations": num_simulations,
                "win_probability": {"Morocco": 1.0},
                "reach_final": {"Morocco": 1.0},
                "reach_semifinal": {"Morocco": 1.0},
                "most_likely_winner": "Morocco",
                "most_likely_winner_prob": 1.0,
                "excluded_teams": ["Canada"],
            }

        with (
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_remaining_knockout",
                side_effect=fake_knockout,
            ) as knockout_simulate,
            patch(
                "app.services.world_cup_tournament_simulator.simulate_tournament",
                return_value={"status": "wrong_path"},
            ) as group_simulate,
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["simulation_basis"], "knockout_fixtures")
        self.assertEqual(body["fixture_teams"], [
            ["Canada", "Morocco", "finished"],
            ["Portugal", "Spain", "scheduled"],
        ])
        knockout_simulate.assert_called_once()
        group_simulate.assert_not_called()

    def test_tournament_simulation_marks_past_unfinished_knockout_fixture_not_ready(self):
        world_cup_analytics._TOURNAMENT_CACHE = {}
        world_cup_analytics._TOURNAMENT_CACHE_TIME = {}
        self.session.add_all(
            [
                MatchFixture(
                    match_id="stale-r16",
                    fixture_id="stale-r16",
                    home_team="Switzerland",
                    away_team="Colombia",
                    kickoff_utc=datetime(2026, 7, 7, 20, 0, 0),
                    venue="Test Stadium",
                    stage="ROUND_OF_16",
                    status="scheduled",
                ),
                MatchFixture(
                    match_id="stale-group-a",
                    fixture_id="stale-group-a",
                    home_team="Switzerland",
                    away_team="Team A2",
                    kickoff_utc=datetime(2026, 6, 12, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_A",
                    status="finished",
                    home_score=1,
                    away_score=0,
                ),
                MatchFixture(
                    match_id="stale-group-b",
                    fixture_id="stale-group-b",
                    home_team="Colombia",
                    away_team="Team B2",
                    kickoff_utc=datetime(2026, 6, 13, 12, 0, 0),
                    venue="Test Stadium",
                    stage="GROUP_STAGE",
                    group="GROUP_B",
                    status="finished",
                    home_score=1,
                    away_score=0,
                ),
            ]
        )
        self.session.commit()

        def fake_knockout(*, fixtures, elo_cache, num_simulations, odds_cache=None):
            return {
                "status": "ok",
                "simulation_basis": "knockout_fixtures",
                "win_probability": {"Switzerland": 0.5, "Colombia": 0.5},
                "reach_final": {"Switzerland": 0.5, "Colombia": 0.5},
                "reach_semifinal": {"Switzerland": 0.5, "Colombia": 0.5},
                "most_likely_winner": "Switzerland",
                "most_likely_winner_prob": 0.5,
                "simulated_fixtures": fixtures,
            }

        with (
            patch(
                "app.api.routes.world_cup_analytics._utcnow",
                return_value=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
            ),
            patch(
                "app.services.world_cup_data_source_status_service.world_cup_data_source_status",
                return_value={
                    "real_data_readiness": {
                        "ok": True,
                        "issues": [],
                        "issue_details": [],
                    }
                },
            ),
            patch(
                "app.services.elo_ratings_service.get_elo_rating",
                return_value={"elo_rating": 1500.0},
            ),
            patch(
                "app.services.world_cup_tournament_simulator.simulate_remaining_knockout",
                side_effect=fake_knockout,
            ),
        ):
            resp = self.client.get("/analytics/tournament-simulation?num_simulations=5000&force_refresh=true")

        self.assertEqual(resp.status_code, 200)
        readiness = resp.json()["real_data_readiness"]
        self.assertFalse(readiness["ok"])
        self.assertIn("stale_unfinished_knockout_fixture", readiness["issues"])
        self.assertEqual(readiness["issue_details"][0]["match_id"], "stale-r16")
        self.assertIn("Switzerland", readiness["issue_details"][0]["message"])


if __name__ == "__main__":
    unittest.main()
