import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import Base
from app.services import odds_cache_service
from app.services.odds_cache_service import OddsCache


class OddsCacheServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    async def test_stale_cache_is_returned_when_fresh_fetch_fails(self):
        self.session.add(
            OddsCache(
                match_key="wc_norway_vs_france",  # Phase 7: namespaced by competition
                home_odds=2.5,
                draw_odds=3.1,
                away_odds=2.8,
                source="pinnacle",
                bookmakers_count=12,
                cached_at=datetime.now(timezone.utc) - timedelta(days=2),
                last_updated_api=datetime.now(timezone.utc) - timedelta(days=2, minutes=5),
            )
        )
        self.session.commit()

        with (
            patch.object(odds_cache_service, "get_prediction_session", return_value=self.session),
            patch.object(odds_cache_service, "fetch_match_odds", new_callable=AsyncMock, return_value=None),
        ):
            odds = await odds_cache_service.get_cached_odds(
                "Norway",
                "France",
                ttl_seconds=3600,
                allow_stale=True,
                max_stale_hours=168,
            )

        self.assertIsNotNone(odds)
        assert odds is not None
        self.assertTrue(odds["stale"])
        self.assertEqual(odds["source"], "stale_cached_pinnacle")
        self.assertEqual(odds["home"], 2.5)


if __name__ == "__main__":
    unittest.main()
