import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import (
    Base,
    MatchFixture,
    MatchPrediction,
    MatchResult,
    PredictionHistory,
)
from app.services.world_cup_post_match_backfill_service import (
    list_post_match_backfill_runs,
    run_post_match_backfill,
)
from app.core.config import settings
from app.memory import loop_run_store
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.utils import sqlite_db


def naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorldCupPostMatchBackfillServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.loop_db_patch = patch.object(
            sqlite_db,
            "loop_db_path",
            return_value=str(Path(self.tmp.name) / "loop.db"),
        )
        self.fact_patch = patch.object(
            settings,
            "SPORTS_FACT_FILE",
            str(Path(self.tmp.name) / "sports_facts.json"),
        )
        self.loop_db_patch.start()
        self.fact_patch.start()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.fact_patch.stop()
        self.loop_db_patch.stop()
        self.tmp.cleanup()

    def _add_finished_match_with_prediction(self, match_id: str = "m1"):
        kickoff = naive_now() - timedelta(hours=3)
        self.session.add(
            MatchFixture(
                match_id=match_id,
                fixture_id=match_id,
                home_team="Team A",
                away_team="Team B",
                kickoff_utc=kickoff,
                venue="Test Stadium",
                stage="group_stage",
                status="finished",
                home_score=2,
                away_score=1,
            )
        )
        self.session.add(
            MatchPrediction(
                match_id=match_id,
                predicted_home_score=2.0,
                predicted_away_score=1.0,
                home_win_prob=0.70,
                draw_prob=0.20,
                away_win_prob=0.10,
                confidence=0.80,
                prediction_method="hybrid",
            )
        )
        self.session.add(
            PredictionHistory(
                match_id=match_id,
                timestamp=kickoff - timedelta(hours=1),
                predicted_home_score=2.0,
                predicted_away_score=1.0,
                home_win_prob=0.70,
                draw_prob=0.20,
                away_win_prob=0.10,
                confidence=0.80,
                trigger="manual",
                prediction_method="hybrid",
            )
        )
        self.session.commit()

    def _add_prediction_for_match(self, match_id: str, kickoff: datetime):
        self.session.add(
            MatchPrediction(
                match_id=match_id,
                predicted_home_score=2.0,
                predicted_away_score=1.0,
                home_win_prob=0.70,
                draw_prob=0.20,
                away_win_prob=0.10,
                confidence=0.80,
                prediction_method="hybrid",
            )
        )
        self.session.add(
            PredictionHistory(
                match_id=match_id,
                timestamp=kickoff - timedelta(hours=1),
                predicted_home_score=2.0,
                predicted_away_score=1.0,
                home_win_prob=0.70,
                draw_prob=0.20,
                away_win_prob=0.10,
                confidence=0.80,
                trigger="manual",
                prediction_method="hybrid",
            )
        )
        self.session.commit()

    def test_dry_run_reports_candidates_without_writing(self):
        self._add_finished_match_with_prediction()

        result = run_post_match_backfill(
            session=self.session,
            dry_run=True,
            audit_metadata={"trigger_source": "unit-test", "operator": "alice"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["match_id"], "m1")
        self.assertEqual(result["scoring"]["scored"], 0)
        self.assertEqual(self.session.query(MatchResult).count(), 0)
        self.assertIn("run_id", result)
        run = loop_run_store.get_run(result["run_id"])
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["job_name"], "world_cup_post_match_backfill")
        self.assertTrue(run["result"]["dry_run"])
        self.assertEqual(run["result"]["candidate_count"], 1)
        self.assertEqual(run["result"]["scored"], 0)
        self.assertEqual(run["result"]["audit_metadata"]["trigger_source"], "unit-test")
        self.assertEqual(run["result"]["audit_metadata"]["operator"], "alice")

        history = list_post_match_backfill_runs()
        self.assertEqual(history["status"], "ok")
        self.assertEqual(history["job_name"], "world_cup_post_match_backfill")
        self.assertEqual(history["count"], 1)
        self.assertEqual(history["runs"][0]["id"], result["run_id"])
        self.assertEqual(history["runs"][0]["status"], "success")
        self.assertTrue(history["runs"][0]["dry_run"])
        self.assertEqual(history["runs"][0]["candidate_count"], 1)
        self.assertEqual(history["runs"][0]["scored"], 0)
        self.assertEqual(history["runs"][0]["audit_metadata"]["trigger_source"], "unit-test")
        self.assertEqual(history["runs"][0]["audit_metadata"]["operator"], "alice")

    def test_backfill_scores_candidates_and_returns_quality_snapshot(self):
        self._add_finished_match_with_prediction()

        result = run_post_match_backfill(
            session=self.session,
            dry_run=False,
            sync_first=False,
        )

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["scoring"]["scored"], 1)
        self.assertEqual(result["result_fact_backfill"]["imported"], 1)
        self.assertEqual(result["quality"]["samples"], 1)
        self.assertEqual(result["quality"]["avg_brier_score"], 0.14)
        self.assertEqual(self.session.query(MatchResult).count(), 1)
        facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="match_result")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["match_id"], "m1")
        self.assertEqual(facts[0]["home_team"], "Team A")
        self.assertEqual(facts[0]["away_team"], "Team B")
        self.assertIn("run_id", result)
        run = loop_run_store.get_run(result["run_id"])
        self.assertEqual(run["status"], "success")
        self.assertFalse(run["result"]["dry_run"])
        self.assertEqual(run["result"]["candidate_count"], 1)
        self.assertEqual(run["result"]["scored"], 1)
        self.assertEqual(run["result"]["quality_samples"], 1)

    def test_sync_error_short_circuits_without_scoring(self):
        self._add_finished_match_with_prediction()

        with patch(
            "app.services.world_cup_post_match_backfill_service.sync_world_cup_fixtures",
            return_value={"status": "error", "error": "source unavailable"},
        ):
            result = run_post_match_backfill(
                session=self.session,
                dry_run=False,
                sync_first=True,
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["step"], "fixture_sync")
        self.assertEqual(self.session.query(MatchResult).count(), 0)
        self.assertIn("run_id", result)
        run = loop_run_store.get_run(result["run_id"])
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "source unavailable")
        self.assertEqual(run["result"]["sync_status"], "error")

        history = list_post_match_backfill_runs()
        self.assertEqual(history["count"], 1)
        self.assertEqual(history["runs"][0]["id"], result["run_id"])
        self.assertEqual(history["runs"][0]["status"], "failed")
        self.assertEqual(history["runs"][0]["sync_status"], "error")
        self.assertEqual(history["runs"][0]["error"], "source unavailable")

    def test_sync_ok_but_stale_started_fixture_marks_run_degraded(self):
        kickoff = naive_now() - timedelta(hours=16)
        self.session.add(
            MatchFixture(
                match_id="stale-r16",
                fixture_id="stale-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=kickoff,
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            )
        )
        self.session.commit()

        with patch(
            "app.services.world_cup_post_match_backfill_service.sync_world_cup_fixtures",
            return_value={"status": "ok", "source": "football-data", "updated": 0},
        ):
            result = run_post_match_backfill(
                session=self.session,
                dry_run=False,
                sync_first=True,
            )

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["stale_unfinished_count"], 1)
        self.assertEqual(result["stale_unfinished_fixtures"][0]["match_id"], "stale-r16")
        self.assertEqual(result["stale_unfinished_fixtures"][0]["status"], "scheduled")
        self.assertIn("run_id", result)

        run = loop_run_store.get_run(result["run_id"])
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["result"]["stale_unfinished_count"], 1)

        history = list_post_match_backfill_runs()
        self.assertEqual(history["runs"][0]["status"], "failed")
        self.assertEqual(history["runs"][0]["stale_unfinished_count"], 1)

    def test_sync_first_can_turn_stale_scheduled_fixture_into_result_fact(self):
        kickoff = naive_now() - timedelta(hours=16)
        self.session.add(
            MatchFixture(
                match_id="stale-r16",
                fixture_id="stale-r16",
                home_team="Switzerland",
                away_team="Colombia",
                kickoff_utc=kickoff,
                venue="Test Stadium",
                stage="ROUND_OF_16",
                status="scheduled",
            )
        )
        self.session.commit()
        self._add_prediction_for_match("stale-r16", kickoff)

        def finish_stale_fixture(source: str):
            match = self.session.query(MatchFixture).filter_by(match_id="stale-r16").one()
            match.status = "finished"
            match.home_score = 1
            match.away_score = 2
            self.session.commit()
            return {
                "status": "ok",
                "source": source,
                "fixtures_synced": 1,
                "fixtures_fetched": 1,
                "fixtures_parsed": 1,
                "created": 0,
                "updated": 1,
                "skipped": 0,
                "remaining_matches": 0,
                "season": 2026,
            }

        with patch(
            "app.services.world_cup_post_match_backfill_service.sync_world_cup_fixtures",
            side_effect=finish_stale_fixture,
        ):
            result = run_post_match_backfill(
                session=self.session,
                dry_run=False,
                sync_first=True,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sync"]["updated"], 1)
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["result_fact_backfill"]["imported"], 1)
        facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT, kind="match_result")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["match_id"], "stale-r16")
        self.assertEqual(facts[0]["home_team"], "Switzerland")
        self.assertEqual(facts[0]["away_team"], "Colombia")
        self.assertEqual(facts[0]["score"], {"home": 1, "away": 2})


if __name__ == "__main__":
    unittest.main()
