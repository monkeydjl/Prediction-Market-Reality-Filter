"""Tests for engine auto-tuning bucket filters."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import AIOptimizedPrediction, Base, EngineCalibration
from app.services import engine_auto_tuning_service as svc


class EngineAutoTuningServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _run_patterns(self, engine_name: str):
        with (
            patch.object(svc, "get_prediction_session", return_value=self.session),
            patch.object(svc, "close_prediction_session", lambda s: None),
        ):
            return svc.calculate_optimization_patterns(engine_name)

    def _add_optimization(
        self,
        match_id: str,
        original_engine: str,
        *,
        verified: bool = False,
        improved: bool = True,
    ):
        actual_fields = {}
        if verified:
            actual_fields = {
                "actual_home_score": 1,
                "actual_away_score": 0,
                "original_error": 0.50,
                "optimized_error": 0.25 if improved else 0.75,
                "optimization_improved": 1 if improved else 0,
            }
        self.session.add(
            AIOptimizedPrediction(
                match_id=match_id,
                original_engine=original_engine,
                original_home_score=1.0,
                original_away_score=1.0,
                original_home_win_prob=0.35,
                original_draw_prob=0.30,
                original_away_win_prob=0.35,
                original_confidence=0.70,
                optimized_home_score=1.1,
                optimized_away_score=0.9,
                optimized_home_win_prob=0.40,
                optimized_draw_prob=0.28,
                optimized_away_win_prob=0.32,
                optimized_confidence=0.74,
                blind_spots=[],
                calibration_issues=[],
                optimization_reasoning="test",
                **actual_fields,
            )
        )

    def test_integrated_optimizations_are_not_counted_as_elo_odds(self):
        self._add_optimization("elo", "elo_odds_fusion (Elo 30% + Odds 70%)", verified=True)
        self._add_optimization("integrated", "integrated (elo_odds 40% + hybrid 60%)", verified=True)
        self.session.commit()

        elo_patterns = self._run_patterns("elo_odds")
        integrated_patterns = self._run_patterns("integrated")

        self.assertEqual(elo_patterns["samples"], 1)
        self.assertEqual(integrated_patterns["samples"], 1)

    def test_unverified_ai_optimizations_do_not_create_calibration_patterns(self):
        self._add_optimization("unverified", "elo_odds_fusion (Elo 30% + Odds 70%)")
        self.session.commit()

        result = self._run_patterns("elo_odds")

        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["samples"], 0)
        self.assertEqual(result["unverified_samples"], 1)
        self.assertIn("verified", result["message"])

    def test_verified_ai_optimization_patterns_include_improvement_rate(self):
        self._add_optimization("winner", "elo_odds_fusion (Elo 30% + Odds 70%)", verified=True, improved=True)
        self._add_optimization("loser", "elo_odds_fusion (Elo 30% + Odds 70%)", verified=True, improved=False)
        self.session.commit()

        result = self._run_patterns("elo_odds")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["samples"], 2)
        self.assertEqual(result["unverified_samples"], 0)
        self.assertEqual(result["avg_improvement"], 0.5)

    def test_unverified_active_calibration_is_not_applied_to_predictions(self):
        self.session.add(
            EngineCalibration(
                engine_name="elo_odds",
                calibration_params={"confidence_multiplier": 0.5},
                based_on_matches=20,
                version=1,
                is_active=1,
                avg_improvement=None,
                confidence_score=0.9,
            )
        )
        self.session.commit()
        prediction = {
            "predicted_score": {"home": 1.0, "away": 1.0},
            "outcome_probabilities": {"home_win": 0.4, "draw": 0.3, "away_win": 0.3},
            "confidence": 0.8,
        }

        with (
            patch.object(svc, "get_prediction_session", return_value=self.session),
            patch.object(svc, "close_prediction_session", lambda s: None),
        ):
            result = svc.apply_calibration_to_prediction(prediction, "elo_odds")

        self.assertEqual(result["confidence"], 0.8)

    def test_verified_active_calibration_can_be_applied_to_predictions(self):
        self.session.add(
            EngineCalibration(
                engine_name="elo_odds",
                calibration_params={"confidence_multiplier": 0.5},
                based_on_matches=20,
                version=1,
                is_active=1,
                avg_improvement=0.1,
                confidence_score=0.9,
            )
        )
        self.session.commit()
        prediction = {
            "predicted_score": {"home": 1.0, "away": 1.0},
            "outcome_probabilities": {"home_win": 0.4, "draw": 0.3, "away_win": 0.3},
            "confidence": 0.8,
        }

        with (
            patch.object(svc, "get_prediction_session", return_value=self.session),
            patch.object(svc, "close_prediction_session", lambda s: None),
        ):
            result = svc.apply_calibration_to_prediction(prediction, "elo_odds")

        self.assertEqual(result["confidence"], 0.4)


if __name__ == "__main__":
    unittest.main()
