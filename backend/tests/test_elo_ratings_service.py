"""The cache branch of ``elo_ratings_service.get_elo_rating`` (audit 53.172).

53.172 converted five serializers onto ``utc_isoformat`` and recorded one of
them -- this service's cache-hit branch -- as an **expected survivor**: reverting
the change turned nothing red, because no pytest case had ever executed that
branch. Only ``tests/manual/manual_elo_ratings_service.py`` reached it, and only
by hand. This file is the harness that closes that gap.

What makes the branch awkward, and therefore worth a dedicated fixture: the
value it emits comes from a column SQLite hands back naive, and the *fallback*
paths emit ``datetime.now(timezone.utc)`` -- already offset-carrying. So a test
that merely asserts "the string has an offset" passes whether or not the cache
was hit: the empty case it is supposed to guard is indistinguishable from the
unrelated cases that happen to be correct. Every test here therefore asserts
``source`` as well, which is the only key that says *which* branch answered.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services import elo_ratings_service
from app.services.elo_ratings_service import EloRating

# Not in ``WORLD_CUP_2026_ELO_ESTIMATES`` and not an alias of anything in it, so
# a cache miss falls through to the "default" branch instead of the hardcoded
# one -- two different fallbacks, and only one of them can be asserted cheaply.
UNKNOWN_TEAM = "Testlandia"


class EloRatingCacheBranchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        EloRating.__table__.create(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _store(self, age: timedelta) -> datetime:
        """Insert a row the way the producer does, and return what SQLite gives back.

        The insert value is **aware** (``datetime.now(timezone.utc)``, which is
        what ``_save_to_cache`` writes). The assertion below is the premise of
        every test in this file: reading it back loses the offset. If a future
        driver returns it aware, this fails here -- at the premise -- rather than
        leaving the offset assertions to pass for the wrong reason.
        """
        written = datetime.now(timezone.utc) - age
        self.session.add(
            EloRating(
                team_name=UNKNOWN_TEAM,
                elo_rating=1700.0,
                fifa_rank=42,
                confederation="UEFA",
                last_updated=written,
                source="wikipedia",
            )
        )
        self.session.commit()

        reloaded = self.session.query(EloRating).filter_by(team_name=UNKNOWN_TEAM).one()
        self.assertIsNone(
            reloaded.last_updated.tzinfo,
            "premise broken: the column now round-trips aware, so this file is "
            "no longer measuring the shape it was written for",
        )
        return reloaded.last_updated

    async def _get(self, **kwargs):
        with patch.object(
            elo_ratings_service, "get_prediction_session", return_value=self.session
        ):
            return await elo_ratings_service.get_elo_rating(UNKNOWN_TEAM, **kwargs)

    async def test_cached_rating_carries_an_offset(self):
        """The branch 53.172 changed, and the only one that can be naive."""
        stored = self._store(timedelta(hours=1))

        rating = await self._get()

        self.assertEqual(rating["source"], "cached_wikipedia")
        self.assertEqual(rating["elo_rating"], 1700.0)
        # Built from the naive value SQLite returned, not from `written`: this
        # pins what the API says (wall clock + explicit UTC) rather than
        # re-asserting the input.
        self.assertEqual(rating["last_updated"], f"{stored.isoformat()}+00:00")

    async def test_a_stale_row_is_not_served_from_cache(self):
        """The 7-day boundary: without it, an eight-day-old row answers the call."""
        self._store(timedelta(days=8))

        rating = await self._get()

        self.assertEqual(rating["source"], "default")
        self.assertEqual(rating["elo_rating"], 1500.0)

    async def test_force_refresh_bypasses_the_cache(self):
        self._store(timedelta(hours=1))

        rating = await self._get(force_refresh=True)

        self.assertEqual(rating["source"], "default")


if __name__ == "__main__":
    unittest.main()
