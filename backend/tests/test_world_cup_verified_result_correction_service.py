# backend/tests/test_world_cup_verified_result_correction_service.py
"""Coverage for the protected verified-result correction workflow.

The service had no tests at all. These pin the two things a caller depends on:
the operator's confirmed scores are what land in the persisted match_result
fact (every downstream reader parses that "score" dict as numbers), and an
unconfirmed call writes nothing.
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.world_cup_prediction import Base, MatchFixture
from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT, load_sports_facts
from app.services.world_cup_verified_result_correction_service import (
    apply_verified_result_correction,
)
from app.utils import sqlite_db


class VerifiedResultCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.tmp = tempfile.TemporaryDirectory()
        self.fact_file = str(Path(self.tmp.name) / "sports_facts.json")
        self.loop_db_patch = patch.object(
            sqlite_db,
            "loop_db_path",
            return_value=str(Path(self.tmp.name) / "loop.db"),
        )
        self.loop_db_patch.start()
        self.fact_file_patch = patch.object(
            settings, "SPORTS_FACT_FILE", self.fact_file
        )
        self.fact_file_patch.start()

    def tearDown(self):
        self.fact_file_patch.stop()
        self.loop_db_patch.stop()
        self.session.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def _add_unscored_fixture(self):
        kickoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
        self.session.add(
            MatchFixture(
                match_id="m1",
                fixture_id="m1",
                home_team="Brazil",
                away_team="Croatia",
                kickoff_utc=kickoff,
                venue="Test Stadium",
                stage="group_stage",
                status="scheduled",
                home_score=None,
                away_score=None,
            )
        )
        self.session.commit()

    def test_confirmed_correction_writes_the_operator_scores_into_the_fact(self):
        self._add_unscored_fixture()

        result = apply_verified_result_correction(
            match_id="m1",
            home_score=3,
            away_score=1,
            source="official_fifa_result",
            source_url="https://example.com/result",
            confirmed=True,
            session=self.session,
        )

        self.assertEqual(result["status"], "ok")
        # The scores the operator confirmed, not a re-read of a nullable column.
        self.assertEqual(result["fact"]["score"], {"home": 3, "away": 1})
        self.assertEqual(result["fixture"]["score"], {"home": 3, "away": 1})

        facts = load_sports_facts(tournament=WORLD_CUP_TOURNAMENT)
        stored = [fact for fact in facts if fact.get("match_id") == "m1"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["kind"], "match_result")
        self.assertEqual(stored[0]["score"], {"home": 3, "away": 1})

        fixture = self.session.query(MatchFixture).filter_by(match_id="m1").first()
        self.assertEqual(fixture.status, "finished")
        self.assertEqual(fixture.home_score, 3)
        self.assertEqual(fixture.away_score, 1)

    def test_unconfirmed_call_writes_nothing(self):
        self._add_unscored_fixture()

        result = apply_verified_result_correction(
            match_id="m1",
            home_score=3,
            away_score=1,
            source="official_fifa_result",
            session=self.session,
        )

        self.assertEqual(result["status"], "protected")
        self.assertTrue(result["protected"])
        fixture = self.session.query(MatchFixture).filter_by(match_id="m1").first()
        self.assertEqual(fixture.status, "scheduled")
        self.assertIsNone(fixture.home_score)
        self.assertFalse(Path(self.fact_file).exists())

    def test_negative_score_is_rejected(self):
        self._add_unscored_fixture()

        result = apply_verified_result_correction(
            match_id="m1",
            home_score=-1,
            away_score=1,
            source="official_fifa_result",
            confirmed=True,
            session=self.session,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "negative_score")


if __name__ == "__main__":
    unittest.main()
