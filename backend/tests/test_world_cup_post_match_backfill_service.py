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
from app.memory import loop_run_store
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
        self.loop_db_patch.start()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
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
                stage="GROUP_STAGE",
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
        self.assertEqual(result["quality"]["samples"], 1)
        self.assertEqual(result["quality"]["avg_brier_score"], 0.14)
        self.assertEqual(self.session.query(MatchResult).count(), 1)
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


if __name__ == "__main__":
    unittest.main()
