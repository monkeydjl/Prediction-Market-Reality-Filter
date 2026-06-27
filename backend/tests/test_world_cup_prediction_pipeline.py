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


def selection_info(raw_confidence, engine_name=None, reliability_cache=None, calibrated=None):
    raw = round(float(raw_confidence), 3)
    return {
        "raw": raw,
        "calibrated": raw if calibrated is None else calibrated,
        "method": "bucketed_reliability_curve",
        "engine_filter": engine_name,
        "total_samples": 8,
        "is_reliable": True,
        "bucket": {"label": "80-100%", "count": 4},
        "applied_bucket": {"label": "80-100%", "count": 4},
        "reason": "bucket_reliability_curve",
    }


class WorldCupPredictionPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_data_quality_score_counts_context_without_odds(self):
        score = pipeline.calculate_data_quality_score({
            "quality": "mock",
            "has_elo": True,
            "has_odds": False,
            "has_h2h": False,
            "has_stats": False,
            "has_weather": True,
            "has_schedule_context": True,
            "has_group_context": True,
            "elo_source": "cached_manual_import/cached_manual_import",
            "elo_age_days": 2,
            "odds_age_minutes": None,
            "stats_age_hours": None,
        })

        self.assertGreaterEqual(score, 45)
        self.assertLess(score, 60)

    def test_fetch_h2h_data_marks_real_source(self):
        with (
            patch.object(pipeline, "get_team_id_from_name", side_effect=[1, 2]),
            patch.object(
                pipeline,
                "fetch_head_to_head",
                return_value={
                    "matches_played": 3,
                    "team1_wins": 1,
                    "draws": 1,
                    "team2_wins": 1,
                    "avg_goals_team1": 1.33,
                    "avg_goals_team2": 1.0,
                },
            ),
        ):
            h2h = pipeline.fetch_h2h_data("Team A", "Team B")

        self.assertEqual(h2h["data_source"], "real")
        self.assertEqual(h2h["matches_played"], 3)

    def test_fetch_team_stats_uses_historical_fallback(self):
        historical_stats = {
            "goals_per_game": 1.4,
            "goals_conceded_per_game": 0.9,
            "wins": 6,
            "draws": 2,
            "losses": 2,
            "played": 10,
            "data_source": "github_martj42_international_results",
        }
        with (
            patch.object(pipeline, "get_team_id_from_name", return_value=None),
            patch.object(pipeline, "get_historical_team_stats", return_value=historical_stats),
        ):
            stats = pipeline.fetch_team_stats("Team A")

        self.assertEqual(stats["data_source"], "github_martj42_international_results")
        self.assertEqual(stats["played"], 10)

    def test_fetch_h2h_data_uses_historical_fallback(self):
        historical_h2h = {
            "matches_played": 2,
            "home_wins": 1,
            "draws": 1,
            "away_wins": 0,
            "avg_goals_home": 1.0,
            "avg_goals_away": 0.5,
            "data_source": "github_martj42_international_results",
        }
        with (
            patch.object(pipeline, "get_team_id_from_name", return_value=None),
            patch.object(pipeline, "get_historical_h2h", return_value=historical_h2h),
        ):
            h2h = pipeline.fetch_h2h_data("Team A", "Team B")

        self.assertEqual(h2h["data_source"], "github_martj42_international_results")
        self.assertEqual(h2h["matches_played"], 2)

    def test_openfootball_context_enriches_stats_and_factors(self):
        stats = {}
        team_context = {
            "metadata": {
                "fifa_code": "FRA",
                "confed": "UEFA",
                "continent": "Europe",
                "group": "I",
            },
            "squad": {
                "player_count": 26,
                "average_age": 27.4,
                "position_counts": {"GK": 3, "DF": 8, "MF": 8, "FW": 7},
            },
        }

        pipeline._apply_openfootball_team_context(stats, team_context)

        self.assertEqual(stats["squad_size"], 26)
        self.assertEqual(stats["injured_players"], 0)
        self.assertEqual(stats["world_cup_group"], "I")

        factors = {"home_team": {}, "away_team": {}}
        pipeline._apply_openfootball_factor_context(
            factors,
            {"home_team": team_context, "away_team": team_context, "data_source": "test"},
        )

        self.assertEqual(factors["home_team"]["squad_size"], 26)
        self.assertEqual(factors["away_team"]["fifa_code"], "FRA")
        self.assertEqual(factors["openfootball_context"]["data_source"], "test")

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

    async def test_compare_only_bypasses_kickoff_freeze_and_skips_persistence(self):
        # Match has already started (status scheduled but kickoff in the past),
        # which the freeze guard normally rejects. With compare_only=True the
        # pipeline should still run the engine and return a result without
        # writing MatchPrediction or PredictionHistory rows.
        self._add_match(
            "past_scheduled_compare",
            kickoff_utc=utc_now_naive() - timedelta(minutes=1),
            status="scheduled",
        )

        with (
            patch.object(pipeline, "get_elo_rating", new_callable=AsyncMock) as get_elo_rating,
            patch.object(pipeline, "get_cached_odds", new_callable=AsyncMock) as get_cached_odds,
        ):
            get_elo_rating.return_value = {"elo_rating": 1500.0}
            get_cached_odds.return_value = None

            result = await pipeline.run_prediction_pipeline(
                "past_scheduled_compare",
                engine="elo_odds",
                compare_only=True,
                session=self.session,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["action"], "compared")
        self.assertIn("predicted_score", result)
        self.assertIn("outcome_probabilities", result)
        self.assertIn("confidence", result)
        self.assertIn("explanation_contributions", result)
        self.assertEqual(
            {item["key"] for item in result["explanation_contributions"]["items"]},
            {"elo", "odds", "schedule", "injury", "motivation", "market_signal"},
        )
        # compare_only must NOT bypass the data fetch (proves the freeze was skipped)
        get_elo_rating.assert_awaited()
        # No persistence should happen
        self.assertEqual(self.session.query(MatchPrediction).count(), 0)
        self.assertEqual(self.session.query(PredictionHistory).count(), 0)

    async def test_confidence_calibration_metadata_is_persisted(self):
        self._add_match(
            "future_calibrated",
            kickoff_utc=utc_now_naive() + timedelta(hours=1),
            status="scheduled",
        )

        def fake_elo_engine(**_kwargs):
            return {
                "predicted_score": {"home": 1.0, "away": 1.0},
                "outcome_probabilities": {"home_win": 0.35, "draw": 0.35, "away_win": 0.30},
                "confidence": 0.80,
                "prediction_method": "elo_only",
                "elo_ratings": {"home": 1500, "away": 1500},
                "has_betting_odds": False,
            }

        def fake_apply_confidence_calibration(prediction, engine_name=None):
            prediction["raw_confidence"] = 0.80
            prediction["confidence"] = 0.65
            prediction["calibration_info"] = {
                "raw": 0.80,
                "calibrated": 0.65,
                "method": "bucketed_reliability_curve",
                "engine_filter": engine_name,
                "total_samples": 8,
                "is_reliable": True,
                "bucket": {"label": "80-100%", "count": 4},
                "applied_bucket": {"label": "80-100%", "count": 4},
                "reason": "bucket_reliability_curve",
            }
            return prediction

        with (
            patch.object(pipeline, "get_elo_rating", new_callable=AsyncMock) as get_elo_rating,
            patch.object(pipeline, "get_cached_odds", new_callable=AsyncMock) as get_cached_odds,
            patch.object(pipeline, "get_engine", return_value=fake_elo_engine),
            patch.object(pipeline, "apply_confidence_calibration", side_effect=fake_apply_confidence_calibration),
            patch.object(pipeline, "format_tactical_summary", return_value="test tactical"),
        ):
            get_elo_rating.return_value = {"elo_rating": 1500.0, "source": "test"}
            get_cached_odds.return_value = None

            result = await pipeline.run_prediction_pipeline(
                "future_calibrated",
                engine="elo_odds",
                session=self.session,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["confidence"], 0.65)
        self.assertEqual(result["raw_confidence"], 0.80)
        self.assertEqual(result["confidence_calibration"]["total_samples"], 8)

        prediction = self.session.query(MatchPrediction).one()
        self.assertEqual(prediction.confidence, 0.65)
        self.assertEqual(prediction.factors["confidence_calibration"]["raw"], 0.80)
        self.assertEqual(
            prediction.factors["explanation_contributions"]["engine"],
            "elo_odds",
        )
        self.assertEqual(
            prediction.factors["explanation_contributions"]["items"][0]["key"],
            "elo",
        )

    async def test_high_confidence_selects_real_engine(self):
        self._add_match(
            "future_high_confidence",
            kickoff_utc=utc_now_naive() + timedelta(hours=1),
            status="scheduled",
        )

        def fake_elo_engine(**_kwargs):
            return {
                "predicted_score": {"home": 1.0, "away": 0.8},
                "outcome_probabilities": {"home_win": 0.45, "draw": 0.30, "away_win": 0.25},
                "confidence": 0.40,
                "prediction_method": "elo_odds",
                "elo_ratings": {"home": 1500, "away": 1450},
                "has_betting_odds": True,
            }

        async def fake_hybrid_engine(**_kwargs):
            return {
                "predicted_score": {"home": 2.0, "away": 1.0},
                "outcome_probabilities": {"home_win": 0.60, "draw": 0.25, "away_win": 0.15},
                "confidence": 0.82,
                "prediction_method": "hybrid",
                "rule_score": {"home": 2.0, "away": 1.0},
                "ai_score": {"home": 2.0, "away": 1.0},
                "ai_reasoning": "test",
                "key_factors": ["test"],
                "factors": {"data_quality": "real"},
            }

        def fake_get_engine(name):
            if name == "elo_odds":
                return fake_elo_engine
            if name == "hybrid":
                return fake_hybrid_engine
            raise AssertionError(f"unexpected engine {name}")

        with (
            patch.object(pipeline, "get_elo_rating", new_callable=AsyncMock) as get_elo_rating,
            patch.object(pipeline, "get_cached_odds", new_callable=AsyncMock) as get_cached_odds,
            patch.object(pipeline, "fetch_team_stats", return_value={"data_source": "real"}),
            patch.object(pipeline, "fetch_h2h_data", return_value={"data_source": "real"}),
            patch.object(pipeline, "get_match_weather", return_value=None),
            patch.object(pipeline, "calculate_comprehensive_factors", return_value={}),
            patch.object(pipeline, "build_prediction_factors", return_value={"data_quality": "real"}),
            patch.object(pipeline, "get_engine", side_effect=fake_get_engine),
            patch.object(pipeline, "build_confidence_calibration_info", side_effect=selection_info),
            patch.object(pipeline, "apply_confidence_calibration", side_effect=lambda prediction, engine_name: prediction),
            patch.object(pipeline, "format_tactical_summary", return_value="test tactical"),
        ):
            get_elo_rating.return_value = {"elo_rating": 1500.0, "source": "test"}
            get_cached_odds.return_value = {
                "home": 2.0,
                "draw": 3.0,
                "away": 4.0,
                "source": "test",
            }

            result = await pipeline.run_prediction_pipeline(
                "future_high_confidence",
                engine="high_confidence",
                session=self.session,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["engine_used"], "hybrid")
        self.assertEqual(result["prediction_method"], "hybrid")
        self.assertEqual(
            result["high_confidence_selection"]["candidate_confidences"]["hybrid"]["total_samples"],
            8,
        )
        prediction = self.session.query(MatchPrediction).one()
        history = self.session.query(PredictionHistory).one()
        self.assertEqual(prediction.prediction_method, "hybrid")
        self.assertEqual(
            prediction.factors["high_confidence_selection"]["selected_engine"],
            "hybrid",
        )
        self.assertEqual(history.prediction_method, "hybrid")

    async def test_high_confidence_ranks_by_calibrated_confidence(self):
        self._add_match(
            "future_high_confidence_calibrated",
            kickoff_utc=utc_now_naive() + timedelta(hours=1),
            status="scheduled",
        )

        def fake_elo_engine(**_kwargs):
            return {
                "predicted_score": {"home": 1.0, "away": 1.0},
                "outcome_probabilities": {"home_win": 0.30, "draw": 0.40, "away_win": 0.30},
                "confidence": 0.30,
                "prediction_method": "elo_odds",
                "elo_ratings": {"home": 1500, "away": 1500},
                "has_betting_odds": True,
            }

        async def fake_hybrid_engine(**_kwargs):
            return {
                "predicted_score": {"home": 2.0, "away": 0.0},
                "outcome_probabilities": {"home_win": 0.75, "draw": 0.15, "away_win": 0.10},
                "confidence": 0.95,
                "prediction_method": "hybrid",
                "rule_score": {"home": 2.0, "away": 0.0},
                "ai_score": {"home": 2.0, "away": 0.0},
                "ai_reasoning": "test",
                "key_factors": ["test"],
                "factors": {"data_quality": "real"},
            }

        def fake_get_engine(name):
            if name == "elo_odds":
                return fake_elo_engine
            if name == "hybrid":
                return fake_hybrid_engine
            raise AssertionError(f"unexpected engine {name}")

        def fake_selection_info(raw_confidence, engine_name=None, reliability_cache=None):
            if engine_name == "integrated":
                return selection_info(raw_confidence, engine_name, calibrated=0.88)
            if engine_name == "hybrid":
                return selection_info(raw_confidence, engine_name, calibrated=0.50)
            return selection_info(raw_confidence, engine_name)

        with (
            patch.object(pipeline, "get_elo_rating", new_callable=AsyncMock) as get_elo_rating,
            patch.object(pipeline, "get_cached_odds", new_callable=AsyncMock) as get_cached_odds,
            patch.object(pipeline, "fetch_team_stats", return_value={"data_source": "real"}),
            patch.object(pipeline, "fetch_h2h_data", return_value={"data_source": "real"}),
            patch.object(pipeline, "get_match_weather", return_value=None),
            patch.object(pipeline, "calculate_comprehensive_factors", return_value={}),
            patch.object(pipeline, "build_prediction_factors", return_value={"data_quality": "real"}),
            patch.object(pipeline, "get_engine", side_effect=fake_get_engine),
            patch.object(pipeline, "build_confidence_calibration_info", side_effect=fake_selection_info),
            patch.object(pipeline, "apply_confidence_calibration", side_effect=lambda prediction, engine_name: prediction),
            patch.object(pipeline, "format_tactical_summary", return_value="test tactical"),
        ):
            get_elo_rating.return_value = {"elo_rating": 1500.0, "source": "test"}
            get_cached_odds.return_value = {
                "home": 2.0,
                "draw": 3.0,
                "away": 4.0,
                "source": "test",
            }

            result = await pipeline.run_prediction_pipeline(
                "future_high_confidence_calibrated",
                engine="high_confidence",
                session=self.session,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["engine_used"], "integrated")
        self.assertTrue(result["prediction_method"].startswith("integrated"))
        self.assertEqual(
            result["high_confidence_selection"]["candidate_confidences"]["integrated"]["calibrated"],
            0.88,
        )


if __name__ == "__main__":
    unittest.main()
