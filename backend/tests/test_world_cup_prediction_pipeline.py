import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import (
    Base,
    MatchFixture,
    MatchPrediction,
    PredictionHistory,
)
from app.services import world_cup_prediction_pipeline as pipeline


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorldCupPredictionPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine)()

    def tearDown(self):
        self.session.close()

    def _add_match(self, match_id: str, kickoff_utc: datetime, status: str = "scheduled"):
        self.session.add(
            MatchFixture(
                match_id=match_id,
                fixture_id=match_id,
                home_team="Team A",
                away_team="Team B",
                kickoff_utc=kickoff_utc,
                venue="Test Stadium",
                stage="GROUP_STAGE",
                group="A",
                status=status,
            )
        )
        self.session.commit()

    async def test_run_prediction_pipeline_skips_scheduled_match_after_kickoff(self):
        self._add_match(
            "past_scheduled",
            kickoff_utc=utc_now_naive() - timedelta(minutes=1),
            status="scheduled",
        )

        with (
            patch.object(pipeline, "get_elo_rating", new_callable=AsyncMock) as get_elo_rating,
            patch.object(pipeline, "get_cached_odds", new_callable=AsyncMock) as get_cached_odds,
        ):
            result = await pipeline.run_prediction_pipeline(
                "past_scheduled",
                session=self.session,
            )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "Match already started")
        get_elo_rating.assert_not_called()
        get_cached_odds.assert_not_called()
        self.assertEqual(self.session.query(MatchPrediction).count(), 0)
        self.assertEqual(self.session.query(PredictionHistory).count(), 0)

    async def test_batch_predict_matches_skips_explicit_started_matches_without_writes(self):
        self._add_match(
            "past_scheduled",
            kickoff_utc=utc_now_naive() - timedelta(minutes=1),
            status="scheduled",
        )
        self._add_match(
            "in_play",
            kickoff_utc=utc_now_naive() - timedelta(minutes=30),
            status="in_play",
        )

        with (
            patch.object(pipeline, "get_prediction_session", return_value=self.session),
            patch.object(pipeline, "close_prediction_session"),
            patch.object(pipeline, "get_elo_rating", new_callable=AsyncMock) as get_elo_rating,
            patch.object(pipeline, "get_cached_odds", new_callable=AsyncMock) as get_cached_odds,
        ):
            result = await pipeline.batch_predict_matches(
                match_ids=["past_scheduled", "in_play"],
                trigger="batch_manual",
            )

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["succeeded"], 0)
        self.assertEqual(result["skipped"], 2)
        get_elo_rating.assert_not_called()
        get_cached_odds.assert_not_called()
        self.assertEqual(self.session.query(MatchPrediction).count(), 0)
        self.assertEqual(self.session.query(PredictionHistory).count(), 0)

    async def test_batch_predict_matches_excludes_stale_scheduled_matches(self):
        self._add_match(
            "past_scheduled",
            kickoff_utc=utc_now_naive() - timedelta(minutes=1),
            status="scheduled",
        )
        self._add_match(
            "future_scheduled",
            kickoff_utc=utc_now_naive() + timedelta(hours=1),
            status="scheduled",
        )

        with (
            patch.object(pipeline, "get_prediction_session", return_value=self.session),
            patch.object(pipeline, "close_prediction_session"),
            patch.object(
                pipeline,
                "run_prediction_pipeline",
                new_callable=AsyncMock,
                return_value={"status": "ok", "engine_used": "hybrid"},
            ) as run_prediction_pipeline,
        ):
            result = await pipeline.batch_predict_matches()

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["succeeded"], 1)
        run_prediction_pipeline.assert_awaited_once()
        self.assertEqual(run_prediction_pipeline.await_args.args[0], "future_scheduled")


if __name__ == "__main__":
    unittest.main()
