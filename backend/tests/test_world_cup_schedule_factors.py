import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import Base, MatchFixture
from app.services.world_cup_schedule_factors import _team_schedule, build_schedule_factors

#: The per-team shape. Pinned as a set because 53.189 deleted a dead key from
#: both returns -- ``last_match_utc``, which nothing in ``backend/`` or
#: ``frontend/`` read. "No reader" was established by grep; this is what makes
#: the *absence* stay true, since a key with no reader can come back without
#: breaking anything -- which is how it survived in the first place.
TEAM_SCHEDULE_KEYS = {
    "days_since_last_match",
    "matches_last_14_days",
    "schedule_density",
    "last_match_id",
}

FACTORS_KEYS = {"home", "away", "rest_days_difference", "rest_advantage"}


def naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorldCupScheduleFactorsTests(unittest.TestCase):
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
        kickoff: datetime,
        status: str = "finished",
    ) -> MatchFixture:
        match = MatchFixture(
            match_id=match_id,
            fixture_id=match_id,
            home_team=home,
            away_team=away,
            kickoff_utc=kickoff,
            venue="Test Stadium",
            stage="group_stage",
            status=status,
        )
        self.session.add(match)
        return match

    def test_builds_rest_advantage_and_density(self):
        kickoff = naive() + timedelta(days=1)
        target = self._add_match("target", "Home", "Away", kickoff, status="scheduled")
        self._add_match("h1", "Home", "Other A", kickoff - timedelta(days=6))
        self._add_match("a1", "Away", "Other B", kickoff - timedelta(days=2))
        self._add_match("a2", "Other C", "Away", kickoff - timedelta(days=7))
        self._add_match("a3", "Away", "Other D", kickoff - timedelta(days=12))
        self.session.commit()

        factors = build_schedule_factors(target, self.session)

        self.assertEqual(factors["rest_advantage"], "home")
        self.assertEqual(factors["home"]["days_since_last_match"], 6.0)
        self.assertEqual(factors["away"]["days_since_last_match"], 2.0)
        self.assertEqual(factors["away"]["matches_last_14_days"], 3)
        self.assertEqual(factors["away"]["schedule_density"], "high")

    def test_defaults_when_no_previous_matches(self):
        target = self._add_match("target", "Home", "Away", naive(), status="scheduled")
        self.session.commit()

        factors = build_schedule_factors(target, self.session)

        self.assertEqual(factors["rest_advantage"], "balanced")
        self.assertEqual(factors["home"]["days_since_last_match"], 7)
        self.assertEqual(factors["away"]["matches_last_14_days"], 0)

    def test_the_shape_is_pinned_on_every_return(self):
        """Three carriers, because ``_team_schedule`` has two returns.

        53.188's lesson applied here: a rule that holds at one exit is not a rule
        about the function. The early return is unreachable from a persisted row
        (``kickoff_utc`` is NOT NULL), so it is exercised with a transient
        fixture -- the branch is defensive, but the *shape* promise is the same
        one, and dropping a key from only that branch would otherwise be silent.
        """
        kickoff = naive() + timedelta(days=1)
        target = self._add_match("target", "Home", "Away", kickoff, status="scheduled")
        self._add_match("h1", "Home", "Other A", kickoff - timedelta(days=6))
        self.session.commit()

        factors = build_schedule_factors(target, self.session)
        self.assertEqual(set(factors), FACTORS_KEYS)
        for side in ("home", "away"):
            with self.subTest(side=side):
                self.assertEqual(set(factors[side]), TEAM_SCHEDULE_KEYS)

        transient = MatchFixture(
            match_id="no-kickoff",
            fixture_id="no-kickoff",
            home_team="Home",
            away_team="Away",
            kickoff_utc=None,
            stage="group_stage",
        )
        self.assertEqual(set(_team_schedule("Home", transient, self.session)), TEAM_SCHEDULE_KEYS)


if __name__ == "__main__":
    unittest.main()
