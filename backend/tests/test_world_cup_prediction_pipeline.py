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
from app.services.world_cup_data_quality import enrich_data_quality_metrics


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def selection_info(raw_confidence, engine_name=None, reliability_cache=None, calibrated=None):
    raw = round(float(raw_confidence), 3)
    return {
        "raw": raw,
        "calibrated": raw if calibrated is None else calibrated,
        "method": "piecewise_linear_reliability",
        "engine_filter": engine_name,
        "total_samples": 8,
        "is_reliable": True,
        "bucket": {"label": "80-100%", "count": 4},
        "applied_bucket": {"label": "80-100%", "count": 4},
        "reason": "piecewise_linear_calibration",
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

    def test_unavailable_source_is_not_treated_as_real(self):
        self.assertFalse(pipeline.source_looks_real("unavailable"))
        self.assertFalse(pipeline.source_looks_real("unavailable/unavailable"))

    def test_stats_quality_requires_both_team_sources_to_be_real(self):
        self.assertFalse(pipeline.all_sources_look_real("github_martj42_international_results/unavailable"))
        self.assertTrue(pipeline.all_sources_look_real("github_martj42_international_results/api_football"))

    def test_data_quality_score_rejects_mixed_non_real_elo_source(self):
        base_metrics = {
            "quality": "partial",
            "has_elo": True,
            "has_odds": False,
            "has_h2h": False,
            "has_stats": False,
            "has_weather": False,
            "has_schedule_context": False,
            "has_group_context": False,
            "elo_age_days": None,
            "odds_age_minutes": None,
            "stats_age_hours": None,
        }

        clean_score = pipeline.calculate_data_quality_score({
            **base_metrics,
            "elo_source": "cached_manual_import/api_football",
        })
        mixed_score = pipeline.calculate_data_quality_score({
            **base_metrics,
            "elo_source": "cached_manual_import/estimated",
        })

        self.assertEqual(clean_score - mixed_score, 8)

    def test_enriched_quality_rejects_mixed_non_real_stats_source(self):
        enriched = enrich_data_quality_metrics({
            "quality": "partial",
            "stats_source": "github_martj42_international_results/fallback",
        })

        self.assertFalse(enriched["has_stats"])

    def test_degraded_quality_never_emits_new_mock_quality(self):
        self.assertEqual(pipeline._degrade_quality("real"), "partial")
        self.assertEqual(pipeline._degrade_quality("partial"), "partial")
        self.assertEqual(pipeline._degrade_quality("mock"), "partial")

    def test_new_prediction_quality_only_trusts_explicit_real_or_partial(self):
        quality, notes = pipeline._normalize_new_prediction_quality("official", [])

        self.assertEqual(quality, "partial")
        self.assertIn("non_real_quality_normalized", notes)

        self.assertEqual(pipeline._normalize_new_prediction_quality("real", [])[0], "real")
        self.assertEqual(pipeline._normalize_new_prediction_quality("partial", [])[0], "partial")

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

    def test_fetch_team_stats_returns_unavailable_instead_of_mock_when_real_sources_missing(self):
        with (
            patch.object(pipeline, "get_team_id_from_name", return_value=None),
            patch.object(pipeline, "get_historical_team_stats", return_value=None),
        ):
            stats = pipeline.fetch_team_stats("Team A")

        self.assertEqual(stats["data_source"], "unavailable")
        self.assertFalse(stats["available"])
        self.assertNotEqual(stats["data_source"], "mock")

    def test_fetch_h2h_data_returns_unavailable_instead_of_mock_when_real_sources_missing(self):
        with (
            patch.object(pipeline, "get_team_id_from_name", return_value=None),
            patch.object(pipeline, "get_historical_h2h", return_value=None),
        ):
            h2h = pipeline.fetch_h2h_data("Team A", "Team B")

        self.assertEqual(h2h["data_source"], "unavailable")
        self.assertFalse(h2h["available"])
        self.assertNotEqual(h2h["data_source"], "mock")

    def _challenge_match(self):
        return type(
            "Match",
            (),
            {
                "match_id": "wc-1",
                "home_team": "A",
                "away_team": "B",
                "stage": "group_stage",
            },
        )()

    def _challenge_prediction(self):
        return {
            "predicted_score": {"home": 2.0, "away": 1.0},
            "outcome_probabilities": {
                "home_win": 0.52,
                "draw": 0.24,
                "away_win": 0.24,
            },
            "confidence": 0.82,
            "prediction_method": "integrated",
            "high_confidence_selection": {"selected_engine": "integrated"},
            "factors": {"data_quality": "real"},
        }

    def test_world_cup_challenge_flag_off_noops(self):
        with (
            patch.object(pipeline.settings, "CONCLUSION_CHALLENGE_ENABLED", False),
            patch.object(pipeline.settings, "WORLD_CUP_CHALLENGE_ENABLED", False),
        ):
            result = pipeline._run_world_cup_conclusion_challenge(
                self._challenge_match(),
                self._challenge_prediction(),
                attempt_count=0,
            )
        self.assertNotIn("challenge_result", result["factors"])
        self.assertEqual(result["confidence"], 0.82)

    def test_world_cup_challenge_reject_caps_confidence(self):
        def fake_challenge(payload):
            return {
                "verdict": "reject",
                "required_action": "downgrade_to_wait",
                "failed_checks": [{"check": "counterevidence", "reason": "odds reversal"}],
                "warnings": [],
                "confidence_adjustment": {"cap": 0.60, "reason": "challenge gate failed"},
                "challenge_summary": "challenge result: reject; reason: odds reversal",
                "critic_notes": {},
                "attempt_count": 0,
            }

        with (
            patch.object(pipeline.settings, "CONCLUSION_CHALLENGE_ENABLED", True),
            patch.object(pipeline.settings, "WORLD_CUP_CHALLENGE_ENABLED", True),
            patch.object(pipeline.settings, "CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED", False),
            patch.object(pipeline.settings, "CONCLUSION_CHALLENGE_STRICTNESS", "normal"),
            patch(
                "app.services.conclusion_challenge_service.challenge_conclusion",
                new=fake_challenge,
            ),
        ):
            result = pipeline._run_world_cup_conclusion_challenge(
                self._challenge_match(),
                self._challenge_prediction(),
                attempt_count=0,
            )
        self.assertEqual(result["confidence"], 0.60)
        self.assertIsNone(result["high_confidence_selection"])
        self.assertEqual(result["factors"]["challenge_result"]["verdict"], "reject")

    def test_world_cup_challenge_retry_requested_once(self):
        prediction = self._challenge_prediction()
        prediction["factors"]["challenge_result"] = {
            "required_action": "recalculate_once",
            "verdict": "revise",
        }
        with patch.object(
            pipeline.settings,
            "CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS",
            1,
        ):
            self.assertTrue(
                pipeline._world_cup_challenge_retry_requested(
                    prediction,
                    attempt_count=0,
                )
            )
            self.assertFalse(
                pipeline._world_cup_challenge_retry_requested(
                    prediction,
                    attempt_count=1,
                )
            )

    def test_world_cup_challenge_retry_engine_is_conservative(self):
        self.assertEqual(
            pipeline._select_world_cup_challenge_retry_engine("high_confidence"),
            "integrated",
        )
        self.assertEqual(
            pipeline._select_world_cup_challenge_retry_engine("integrated"),
            "hybrid",
        )

    async def test_world_cup_challenge_retry_reruns_before_persistence(self):
        self._add_match(
            "future_challenge_retry",
            kickoff_utc=utc_now_naive() + timedelta(hours=1),
            status="scheduled",
        )
        challenge_calls = []

        def fake_elo_engine(**_kwargs):
            return {
                "predicted_score": {"home": 1.0, "away": 1.0},
                "outcome_probabilities": {
                    "home_win": 0.35,
                    "draw": 0.35,
                    "away_win": 0.30,
                },
                "confidence": 0.60,
                "prediction_method": "elo_odds",
                "elo_ratings": {"home": 1500, "away": 1500},
                "has_betting_odds": True,
            }

        async def fake_hybrid_engine(**_kwargs):
            return {
                "predicted_score": {"home": 2.0, "away": 1.0},
                "outcome_probabilities": {
                    "home_win": 0.60,
                    "draw": 0.25,
                    "away_win": 0.15,
                },
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

        def fake_challenge(_match, prediction_result, *, attempt_count=0):
            challenge_calls.append((
                attempt_count,
                prediction_result["prediction_method"],
            ))
            updated = dict(prediction_result)
            factors = dict(updated.get("factors") or {})
            if attempt_count == 0:
                factors["challenge_result"] = {
                    "verdict": "revise",
                    "required_action": "recalculate_once",
                }
            else:
                factors["challenge_result"] = {
                    "verdict": "pass",
                    "required_action": "allow_output",
                    "attempt_count": attempt_count,
                }
            updated["factors"] = factors
            return updated

        with (
            patch.object(pipeline, "get_elo_rating", new_callable=AsyncMock) as get_elo_rating,
            patch.object(pipeline, "get_cached_odds", new_callable=AsyncMock) as get_cached_odds,
            patch.object(pipeline, "fetch_team_stats", return_value={"data_source": "real"}),
            patch.object(pipeline, "fetch_h2h_data", return_value={"data_source": "real"}),
            patch.object(pipeline, "get_match_weather", return_value=None),
            patch.object(pipeline, "calculate_comprehensive_factors", return_value={}),
            patch.object(pipeline, "build_prediction_factors", return_value={"data_quality": "real"}),
            patch.object(pipeline, "get_engine", side_effect=fake_get_engine),
            patch.object(pipeline, "apply_confidence_calibration", side_effect=lambda prediction, engine_name: prediction),
            patch.object(pipeline, "format_tactical_summary", return_value="test tactical"),
            patch.object(pipeline, "_run_world_cup_conclusion_challenge", side_effect=fake_challenge),
            patch.object(pipeline.settings, "CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS", 1),
        ):
            get_elo_rating.return_value = {"elo_rating": 1500.0, "source": "test"}
            get_cached_odds.return_value = {
                "home": 2.0,
                "draw": 3.0,
                "away": 4.0,
                "source": "test",
            }

            result = await pipeline.run_prediction_pipeline(
                "future_challenge_retry",
                engine="integrated",
                session=self.session,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["engine_used"], "hybrid")
        self.assertEqual(
            challenge_calls,
            [(0, "integrated (elo_odds 70% + hybrid 30%)"), (1, "hybrid")],
        )
        self.assertEqual(self.session.query(MatchPrediction).count(), 1)
        self.assertEqual(self.session.query(PredictionHistory).count(), 1)
        prediction = self.session.query(MatchPrediction).one()
        self.assertEqual(prediction.prediction_method, "hybrid")
        self.assertEqual(prediction.factors["challenge_result"]["verdict"], "pass")

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
                stage="group_stage",
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
            patch.object(pipeline, "apply_confidence_calibration", side_effect=lambda prediction, engine_name: prediction),
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

    async def test_auto_engine_defaults_to_elo_odds_not_integrated_slow_path(self):
        self._add_match(
            "future_auto",
            kickoff_utc=utc_now_naive() + timedelta(hours=1),
            status="scheduled",
        )

        def fake_elo_engine(**_kwargs):
            return {
                "predicted_score": {"home": 1.5, "away": 0.9},
                "outcome_probabilities": {
                    "home_win": 0.55,
                    "draw": 0.25,
                    "away_win": 0.20,
                },
                "confidence": 0.70,
                "prediction_method": "elo_odds_fusion (Elo 30% + Odds 70%)",
                "elo_ratings": {"home": 1600, "away": 1500},
                "has_betting_odds": True,
            }

        with (
            patch.object(pipeline, "get_elo_rating", new_callable=AsyncMock) as get_elo_rating,
            patch.object(pipeline, "get_cached_odds", new_callable=AsyncMock) as get_cached_odds,
            patch.object(pipeline, "fetch_team_stats", side_effect=AssertionError("auto should not fetch hybrid team stats")),
            patch.object(pipeline, "get_engine", return_value=fake_elo_engine),
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
                "future_auto",
                engine="auto",
                session=self.session,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["engine_used"], "elo_odds")
        self.assertIn("elo_odds", result["prediction_method"])
        prediction = self.session.query(MatchPrediction).one()
        history = self.session.query(PredictionHistory).one()
        self.assertEqual(prediction.prediction_method, result["prediction_method"])
        self.assertEqual(history.prediction_method, result["prediction_method"])

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

        def fake_elo_engine(**_kwargs):
            return {
                "predicted_score": {"home": 1.0, "away": 1.0},
                "outcome_probabilities": {"home_win": 0.35, "draw": 0.35, "away_win": 0.30},
                "confidence": 0.60,
                "prediction_method": "elo_only",
                "elo_ratings": {"home": 1500, "away": 1500},
                "has_betting_odds": False,
            }

        with (
            patch.object(pipeline, "get_elo_rating", new_callable=AsyncMock) as get_elo_rating,
            patch.object(pipeline, "get_cached_odds", new_callable=AsyncMock) as get_cached_odds,
            patch.object(pipeline, "get_engine", return_value=fake_elo_engine),
            patch.object(pipeline, "apply_confidence_calibration", side_effect=lambda prediction, engine_name: prediction),
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
                "method": "piecewise_linear_reliability",
                "engine_filter": engine_name,
                "total_samples": 8,
                "is_reliable": True,
                "bucket": {"label": "80-100%", "count": 4},
                "applied_bucket": {"label": "80-100%", "count": 4},
                "reason": "piecewise_linear_calibration",
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
        self.assertEqual(result["data_quality"], "partial")
        self.assertIn("betting_odds_unavailable", result["data_quality_notes"])
        self.assertEqual(result["confidence"], 0.65)
        self.assertEqual(result["raw_confidence"], 0.80)
        self.assertEqual(result["confidence_calibration"]["total_samples"], 8)

        prediction = self.session.query(MatchPrediction).one()
        self.assertEqual(prediction.confidence, 0.65)
        self.assertEqual(prediction.factors["data_quality"], "partial")
        self.assertIn("betting_odds_unavailable", prediction.factors["data_quality_notes"])
        self.assertEqual(prediction.factors["confidence_calibration"]["raw"], 0.80)
        self.assertEqual(
            prediction.factors["explanation_contributions"]["engine"],
            "elo_odds",
        )
        self.assertEqual(
            prediction.factors["explanation_contributions"]["items"][0]["key"],
            "elo",
        )

    async def test_run_prediction_pipeline_does_not_persist_new_mock_quality(self):
        self._add_match(
            "future_mock_quality_guard",
            kickoff_utc=utc_now_naive() + timedelta(hours=1),
            status="scheduled",
        )

        def fake_elo_engine(**_kwargs):
            return {
                "predicted_score": {"home": 1.0, "away": 1.0},
                "outcome_probabilities": {"home_win": 0.35, "draw": 0.35, "away_win": 0.30},
                "confidence": 0.60,
                "prediction_method": "elo_odds",
                "elo_ratings": {"home": 1500, "away": 1500},
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
                "factors": {"data_quality": "mock"},
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
            patch.object(pipeline, "build_prediction_factors", return_value={"data_quality": "mock"}),
            patch.object(pipeline, "get_engine", side_effect=fake_get_engine),
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
                "future_mock_quality_guard",
                engine="hybrid",
                session=self.session,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data_quality"], "partial")
        self.assertIn("non_real_quality_normalized", result["data_quality_notes"])
        prediction = self.session.query(MatchPrediction).one()
        history = self.session.query(PredictionHistory).one()
        self.assertEqual(prediction.factors["data_quality"], "partial")
        self.assertIn("non_real_quality_normalized", prediction.factors["data_quality_notes"])
        self.assertNotEqual(prediction.factors["data_quality"], "mock")
        self.assertEqual(history.prediction_method, "hybrid")

    async def test_run_prediction_pipeline_does_not_default_missing_quality_to_real(self):
        self._add_match(
            "future_missing_quality_guard",
            kickoff_utc=utc_now_naive() + timedelta(hours=1),
            status="scheduled",
        )

        def fake_elo_engine(**_kwargs):
            return {
                "predicted_score": {"home": 1.0, "away": 1.0},
                "outcome_probabilities": {"home_win": 0.35, "draw": 0.35, "away_win": 0.30},
                "confidence": 0.60,
                "prediction_method": "elo_odds",
                "elo_ratings": {"home": 1500, "away": 1500},
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
            patch.object(pipeline, "build_prediction_factors", return_value={}),
            patch.object(pipeline, "get_engine", side_effect=fake_get_engine),
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
                "future_missing_quality_guard",
                engine="hybrid",
                session=self.session,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data_quality"], "partial")
        self.assertIn("data_quality_missing", result["data_quality_notes"])
        prediction = self.session.query(MatchPrediction).one()
        self.assertEqual(prediction.factors["data_quality"], "partial")
        self.assertIn("data_quality_missing", prediction.factors["data_quality_notes"])

    async def test_run_prediction_pipeline_degrades_mixed_non_real_elo_sources(self):
        self._add_match(
            "future_mixed_elo_guard",
            kickoff_utc=utc_now_naive() + timedelta(hours=1),
            status="scheduled",
        )

        def fake_elo_engine(**_kwargs):
            return {
                "predicted_score": {"home": 1.0, "away": 1.0},
                "outcome_probabilities": {"home_win": 0.35, "draw": 0.35, "away_win": 0.30},
                "confidence": 0.60,
                "prediction_method": "elo_odds",
                "elo_ratings": {"home": 1500, "away": 1500},
                "has_betting_odds": True,
            }

        async def fake_hybrid_engine(**kwargs):
            return {
                "predicted_score": {"home": 2.0, "away": 1.0},
                "outcome_probabilities": {"home_win": 0.60, "draw": 0.25, "away_win": 0.15},
                "confidence": 0.82,
                "prediction_method": "hybrid",
                "rule_score": {"home": 2.0, "away": 1.0},
                "ai_score": {"home": 2.0, "away": 1.0},
                "ai_reasoning": "test",
                "key_factors": ["test"],
                "factors": kwargs["factors"],
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
            patch.object(pipeline, "build_prediction_factors", return_value={}),
            patch.object(pipeline, "get_engine", side_effect=fake_get_engine),
            patch.object(pipeline, "apply_confidence_calibration", side_effect=lambda prediction, engine_name: prediction),
            patch.object(pipeline, "format_tactical_summary", return_value="test tactical"),
        ):
            get_elo_rating.side_effect = [
                {"elo_rating": 1500.0, "source": "cached_manual_import"},
                {"elo_rating": 1500.0, "source": "estimated"},
            ]
            get_cached_odds.return_value = {
                "home": 2.0,
                "draw": 3.0,
                "away": 4.0,
                "source": "api_football",
            }

            result = await pipeline.run_prediction_pipeline(
                "future_mixed_elo_guard",
                engine="integrated",
                session=self.session,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data_quality"], "partial")
        self.assertIn("elo_unavailable", result["data_quality_notes"])
        prediction = self.session.query(MatchPrediction).one()
        self.assertFalse(prediction.factors["data_quality_metrics"]["has_elo"])

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
