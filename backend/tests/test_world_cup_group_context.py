import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import Base, MatchFixture
from app.services.world_cup_group_context import build_group_context


def naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorldCupGroupContextTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _add_match(
        self,
        match_id: str,
        home: str,
        away: str,
        *,
        home_score: int | None = None,
        away_score: int | None = None,
        status: str = "finished",
    ) -> MatchFixture:
        match = MatchFixture(
            match_id=match_id,
            fixture_id=match_id,
            home_team=home,
            away_team=away,
            kickoff_utc=naive() + timedelta(hours=len(match_id)),
            venue="Test Stadium",
            stage="group_stage",
            group="A",
            status=status,
            home_score=home_score,
            away_score=away_score,
        )
        self.session.add(match)
        return match

    def test_marks_final_round_must_win_and_qualified(self):
        # A has 6 points and is already safe. B has 3 points after two matches,
        # so B needs a result in the final group match.
        self._add_match("m1", "A", "C", home_score=1, away_score=0)
        self._add_match("m2", "B", "D", home_score=1, away_score=0)
        self._add_match("m3", "A", "D", home_score=2, away_score=0)
        self._add_match("m4", "C", "B", home_score=1, away_score=0)
        target = self._add_match("m5", "A", "B", status="scheduled")
        self._add_match("m6", "C", "D", status="scheduled")
        self.session.commit()

        context = build_group_context(target, self.session)

        self.assertIsNotNone(context)
        self.assertEqual(context["home"]["status"], "qualified")
        self.assertEqual(context["home"]["pressure"], "rotation_risk")
        self.assertFalse(context["home"]["must_win"])
        self.assertEqual(context["away"]["pressure"], "must_win")
        self.assertTrue(context["away"]["must_win"])
        self.assertTrue(context["has_must_win_team"])

    def test_marks_zero_point_team_eliminated_after_two_matches(self):
        self._add_match("m1", "A", "C", home_score=1, away_score=0)
        self._add_match("m2", "B", "D", home_score=1, away_score=0)
        self._add_match("m3", "A", "D", home_score=2, away_score=0)
        self._add_match("m4", "B", "C", home_score=1, away_score=0)
        self._add_match("m5", "A", "B", status="scheduled")
        target = self._add_match("m6", "C", "D", status="scheduled")
        self.session.commit()

        context = build_group_context(target, self.session)

        self.assertEqual(context["table"][0]["team"], "A")
        self.assertEqual(context["away"]["status"], "eliminated")
        self.assertEqual(context["away"]["pressure"], "low_motivation")

    def test_returns_none_outside_group_stage(self):
        target = MatchFixture(
            match_id="ko",
            fixture_id="ko",
            home_team="A",
            away_team="B",
            kickoff_utc=naive(),
            venue="Test Stadium",
            stage="round_of_16",
            group=None,
            status="scheduled",
        )
        self.session.add(target)
        self.session.commit()

        self.assertIsNone(build_group_context(target, self.session))


if __name__ == "__main__":
    unittest.main()
