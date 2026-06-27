import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import Base, MatchFixture, MatchResult
from app.services.world_cup_scoring_service import (
    score_all_finished_matches,
    score_finished_match,
)


def naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorldCupScoringServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

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
                stage="GROUP_STAGE",
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


if __name__ == "__main__":
    unittest.main()
