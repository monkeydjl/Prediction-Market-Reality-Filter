"""``last_update`` leaves the odds cache zoned -- both exits, pinned.

Audit 53.186. ``get_cached_odds`` builds its dict in **two** ``return``
statements -- the fresh cache hit, and the stale fallback taken when a re-fetch
fails -- and each writes ``last_update`` once. That is two carriers, not one.
The mutation run for 53.186 proved it the hard way: reverting only the hit
branch left every test green, because both assertions written for this field
walked the stale path.

Why these live in their own file instead of ``test_odds_cache_service.py``: that
file owns the cache's *behaviour* (TTL boundaries, staleness reporting, quota
counting). This one owns the *shape* of the one value that leaves the producer,
and it sits next to the other wire-level guard from the same defect family,
``test_service_timestamp_wire.py``. Three tests, one question -- does the string
parse to an aware datetime -- none of them about caching.

Note the shape of the thing under test: this dict never becomes a response body.
Its only reader is ``age_minutes_from_source``, which returns before parsing when
``cache_age_seconds`` is present. That is why the naive string had no visible
symptom, and why it is worth pinning anyway: a value nobody reads is a value
nobody notices going wrong the day someone does start reading it.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import Base
from app.services import odds_cache_service
from app.services.odds_cache_service import OddsCache


class OddsCacheTimestampWireTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _cache_row(self, *, cached_at: datetime, api_stamp: datetime | None) -> None:
        self.session.add(
            OddsCache(
                match_key="wc_norway_vs_france",  # Phase 7: namespaced by competition
                home_odds=2.5,
                draw_odds=3.1,
                away_odds=2.8,
                source="pinnacle",
                bookmakers_count=12,
                cached_at=cached_at,
                last_updated_api=api_stamp,
            )
        )
        self.session.commit()

    def _patched(self):
        return (
            patch.object(odds_cache_service, "get_prediction_session", return_value=self.session),
            patch.object(odds_cache_service, "fetch_match_odds", new_callable=AsyncMock, return_value=None),
        )

    async def _get(self, **kwargs):
        """Call the producer with both seams patched; ``None`` means no live API."""
        session_patch, fetch_patch = self._patched()
        with session_patch, fetch_patch:
            return await odds_cache_service.get_cached_odds("Norway", "France", **kwargs)

    async def test_cache_hit_last_update_carries_an_offset(self):
        """The first exit: a *fresh* cache hit returns before any fetch."""
        cached_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        api_stamp = cached_at - timedelta(minutes=1)
        self._cache_row(cached_at=cached_at, api_stamp=api_stamp)

        odds = await self._get(ttl_seconds=3600)

        assert odds is not None
        self.assertFalse(odds["stale"])
        self.assertEqual(odds["source"], "cached_pinnacle")
        parsed = datetime.fromisoformat(odds["last_update"].replace("Z", "+00:00"))
        self.assertIsNotNone(parsed.tzinfo, odds["last_update"])
        self.assertEqual(parsed, api_stamp)

    async def test_stale_last_update_carries_an_offset(self):
        """The second exit: re-fetch failed, the expired row is served anyway."""
        cached_at = datetime.now(timezone.utc) - timedelta(days=2)
        api_stamp = cached_at - timedelta(minutes=5)
        self._cache_row(cached_at=cached_at, api_stamp=api_stamp)

        odds = await self._get(
            ttl_seconds=3600, allow_stale=True, max_stale_hours=168
        )

        assert odds is not None
        self.assertTrue(odds["stale"])
        stamp = odds["last_update"]
        self.assertIsInstance(stamp, str)
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        self.assertIsNotNone(
            parsed.tzinfo,
            f"{stamp!r} has no offset; `new Date(...)` would read it as local time",
        )
        self.assertEqual(parsed, api_stamp)

    async def test_last_update_falls_back_to_cached_at(self):
        """``last_updated_api`` is nullable; the fallback must be zoned too."""
        cached_at = datetime.now(timezone.utc) - timedelta(days=2)
        self._cache_row(cached_at=cached_at, api_stamp=None)

        odds = await self._get(ttl_seconds=3600, allow_stale=True, max_stale_hours=168)

        assert odds is not None
        parsed = datetime.fromisoformat(odds["last_update"].replace("Z", "+00:00"))
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed, cached_at)


if __name__ == "__main__":
    unittest.main()
