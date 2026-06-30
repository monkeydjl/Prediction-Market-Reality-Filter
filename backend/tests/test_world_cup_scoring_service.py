import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.memory import loop_run_store
from app.models.world_cup_prediction import Base, MatchFixture, MatchResult
from app.services.world_cup_scoring_service import (
    SCORING_RECONCILE_AUDIT_JOB_NAME,
    score_all_finished_matches,
    score_finished_match,
)
from app.utils import sqlite_db


def naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorldCupScoringServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.tmp = tempfile.TemporaryDirectory()
        self.loop_db_patch = patch.object(
            sqlite_db,
            "loop_db_path",
            return_value=str(Path(self.tmp.name) / "loop.db"),
        )
        self.loop_db_patch.start()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.loop_db_patch.stop()
        self.tmp.cleanup()

    def _add_finished_match_with_unscorable_result(self):
        kickoff = naive_now() - timedelta(hours=3)
        self.session.add(
            MatchFixture(
                match_id="m1",
                fixture_id="m1",
                home_team="Team A",
                away_team="Team B",
                kickoff_utc=kickoff,
                venue="Test Stadium",
                stage="group_stage",
                status="finished",
                home_score=2,
                away_score=0,
            )
        )
        self.session.add(
            MatchResult(
                match_id="m1",
                final_home_score=2,
                final_away_score=0,
                outcome="home_win",
                finished_at=kickoff + timedelta(hours=2),
                predicted_home_score=None,
                predicted_away_score=None,
                predicted_outcome_prob=None,
                score_mae=None,
                outcome_correct=None,
                brier_score=None,
                home_error=None,
                away_error=None,
                confidence_calibrated=None,
            )
        )
        self.session.commit()

    def test_existing_no_prediction_result_is_skipped_not_error(self):
        self._add_finished_match_with_unscorable_result()

        single = score_finished_match("m1", session=self.session)

        self.assertEqual(single["status"], "skipped_no_prediction")
        self.assertEqual(self.session.query(MatchResult).count(), 1)

        with (
            patch(
                "app.services.world_cup_scoring_service.get_prediction_session",
                return_value=self.session,
            ),
            patch("app.services.world_cup_scoring_service.close_prediction_session"),
        ):
            summary = score_all_finished_matches()

        self.assertEqual(summary["total_finished"], 1)
        self.assertEqual(summary["scored"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["errors"], 0)

    def test_scoring_reconcile_creates_audit_run_with_metadata(self):
        self._add_finished_match_with_unscorable_result()

        with (
            patch(
                "app.services.world_cup_scoring_service.get_prediction_session",
                return_value=self.session,
            ),
            patch("app.services.world_cup_scoring_service.close_prediction_session"),
        ):
            summary = score_all_finished_matches(
                audit_metadata={
                    "trigger_source": "test-runner",
                    "operator": "bob",
                    "request_path": "/analytics/reconcile-scoring",
                },
            )

        self.assertEqual(summary["status"], "ok")
        self.assertIn("run_id", summary)
        self.assertIn("duration_ms", summary)

        run = loop_run_store.get_run(summary["run_id"])
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["job_name"], SCORING_RECONCILE_AUDIT_JOB_NAME)
        self.assertEqual(run["result"]["audit_metadata"]["trigger_source"], "test-runner")
        self.assertEqual(run["result"]["audit_metadata"]["operator"], "bob")
        self.assertEqual(
            run["result"]["audit_metadata"]["request_path"],
            "/analytics/reconcile-scoring",
        )


if __name__ == "__main__":
    unittest.main()
