"""Tests for engine auto-tuning bucket filters."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.world_cup_prediction import AIOptimizedPrediction, Base
from app.services import engine_auto_tuning_service as svc


class EngineAutoTuningServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine)()

    def tearDown(self):
        self.session.close()

    def _run_patterns(self, engine_name: str):
        with (
            patch.object(svc, "get_prediction_session", return_value=self.session),
            patch.object(svc, "close_prediction_session", lambda s: None),
        ):
            return svc.calculate_optimization_patterns(engine_name)

    def _add_optimization(self, match_id: str, original_engine: str):
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
            )
        )

    def test_integrated_optimizations_are_not_counted_as_elo_odds(self):
        self._add_optimization("elo", "elo_odds_fusion (Elo 30% + Odds 70%)")
        self._add_optimization("integrated", "integrated (elo_odds 40% + hybrid 60%)")
        self.session.commit()

        elo_patterns = self._run_patterns("elo_odds")
        integrated_patterns = self._run_patterns("integrated")

        self.assertEqual(elo_patterns["samples"], 1)
        self.assertEqual(integrated_patterns["samples"], 1)


if __name__ == "__main__":
    unittest.main()
