"""Golden baseline tests for world_cup_prediction_engine.

Lock hybrid engine fusion behavior and prediction_method strings.
"""

import unittest
from unittest.mock import AsyncMock, patch
from app.services.world_cup_prediction_engine import fuse_predictions, predict_match_score


class PredictionEngineGoldenTests(unittest.IsolatedAsyncioTestCase):
    """Lock hybrid engine fusion behavior."""

    def setUp(self):
        """Common fixtures."""
        self.rule_pred = {
            "predicted_score": {"home": 1.8, "away": 1.3},
            "outcome_probabilities": {"home_win": 0.45, "draw": 0.27, "away_win": 0.28},
            "confidence": 0.75,
        }
        self.ai_pred = {
            "predicted_score": {"home": 2.0, "away": 1.2},
            "outcome_probabilities": {"home_win": 0.50, "draw": 0.25, "away_win": 0.25},
            "confidence": 0.80,
            "confidence_in_adjustment": 0.7,
            "reasoning": "AI调整理由",
            "key_factors": ["factor1"],
        }

    def test_fuse_predictions_ai_is_none(self):
        """Lock behavior when AI prediction fails."""
        result = fuse_predictions(self.rule_pred, None)

        # Should return rule-only
        self.assertEqual(result["prediction_method"], "rule_only")
        self.assertEqual(result["predicted_score"], self.rule_pred["predicted_score"])
        self.assertEqual(result["outcome_probabilities"], self.rule_pred["outcome_probabilities"])
        # Confidence slightly reduced: 0.75 * 0.9 = 0.675
        self.assertAlmostEqual(result["confidence"], 0.675, places=3)
        self.assertIsNone(result["ai_score"])
        self.assertIsNone(result["ai_reasoning"])

    def test_fuse_predictions_low_ai_confidence(self):
        """Lock behavior when AI confidence_in_adjustment < 0.4."""
        ai_pred_low_conf = self.ai_pred.copy()
        ai_pred_low_conf["confidence_in_adjustment"] = 0.3

        result = fuse_predictions(self.rule_pred, ai_pred_low_conf)

        # Should use rule-dominant (trust rule, ignore AI delta)
        self.assertEqual(result["prediction_method"], "rule_dominant")
        self.assertEqual(result["predicted_score"], self.rule_pred["predicted_score"])
        self.assertEqual(result["outcome_probabilities"], self.rule_pred["outcome_probabilities"])
        self.assertEqual(result["confidence"], self.rule_pred["confidence"])
        self.assertIsNone(result["ai_score"])
        self.assertIsNotNone(result["ai_reasoning"])

    def test_fuse_predictions_hybrid_fusion(self):
        """Lock normal hybrid fusion with 80/20 rule/AI weights."""
        result = fuse_predictions(self.rule_pred, self.ai_pred, rule_weight=0.80, ai_weight=0.20)

        # Fused scores: home = 1.8*0.8 + 2.0*0.2 = 1.44 + 0.40 = 1.84
        #               away = 1.3*0.8 + 1.2*0.2 = 1.04 + 0.24 = 1.28
        self.assertAlmostEqual(result["predicted_score"]["home"], 1.84, places=2)
        self.assertAlmostEqual(result["predicted_score"]["away"], 1.28, places=2)

        # Agreement factor based on score diff
        # score_diff_home = |1.8 - 2.0| = 0.2, score_diff_away = |1.3 - 1.2| = 0.1
        # avg_diff = 0.15, agreement_factor = max(0.5, 1.0 - 0.15/3.0) = 0.95
        # base_confidence = (0.75 + 0.80)/2 = 0.775
        # final = 0.775 * 0.95 = 0.736
        self.assertAlmostEqual(result["confidence"], 0.736, places=3)

        self.assertEqual(result["prediction_method"], "hybrid")
        self.assertEqual(result["rule_score"], self.rule_pred["predicted_score"])
        self.assertEqual(result["ai_score"], self.ai_pred["predicted_score"])
        self.assertEqual(result["ai_reasoning"], "AI调整理由")
        self.assertEqual(result["key_factors"], ["factor1"])

    def test_fuse_predictions_low_agreement(self):
        """Lock confidence penalty when rule and AI disagree strongly."""
        ai_pred_disagree = {
            "predicted_score": {"home": 3.0, "away": 0.5},  # Very different from rule
            "outcome_probabilities": {"home_win": 0.70, "draw": 0.15, "away_win": 0.15},
            "confidence": 0.85,
            "confidence_in_adjustment": 0.8,
            "reasoning": "Strong disagreement",
            "key_factors": [],
        }

        result = fuse_predictions(self.rule_pred, ai_pred_disagree, rule_weight=0.80, ai_weight=0.20)

        # score_diff_home = |1.8 - 3.0| = 1.2, score_diff_away = |1.3 - 0.5| = 0.8
        # avg_diff = 1.0, agreement_factor = max(0.5, 1.0 - 1.0/3.0) = 0.667
        # base_confidence = (0.75 + 0.85)/2 = 0.80
        # final = 0.80 * 0.667 = 0.534
        self.assertAlmostEqual(result["confidence"], 0.533, places=3)

    async def test_predict_match_score_calls_rule_and_ai(self):
        """Lock that predict_match_score orchestrates rule + AI."""
        factors = {
            "home_team": {"goals_per_game": 2.0, "goals_conceded_per_game": 1.0, "recent_form": 0.7},
            "away_team": {"goals_per_game": 1.5, "goals_conceded_per_game": 1.2, "recent_form": 0.5},
            "head_to_head": {"matches_played": 0},
            "context": {"tournament_stage": "group_stage", "stakes": "medium"},
        }

        # Mock AI to return valid prediction
        with patch("app.services.world_cup_prediction_engine.predict_score_ai") as mock_ai:
            mock_ai.return_value = {
                "predicted_score": {"home": 2.0, "away": 1.2},
                "outcome_probabilities": {"home_win": 0.50, "draw": 0.25, "away_win": 0.25},
                "confidence": 0.80,
                "confidence_in_adjustment": 0.7,
                "reasoning": "Mock AI",
                "key_factors": [],
            }

            result = await predict_match_score(
                "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage", factors
            )

        # Should have called AI and fused
        mock_ai.assert_called_once()
        self.assertIn(result["prediction_method"], ["hybrid", "rule_dominant", "rule_only"])
        self.assertIn("predicted_score", result)
        self.assertIn("outcome_probabilities", result)
        self.assertIn("confidence", result)
        self.assertIn("factors", result)
        self.assertIn("timestamp", result)

    async def test_predict_match_score_ai_fails_gracefully(self):
        """Lock that predict_match_score falls back to rule-only when AI fails."""
        factors = {
            "home_team": {"goals_per_game": 2.0, "goals_conceded_per_game": 1.0, "recent_form": 0.7},
            "away_team": {"goals_per_game": 1.5, "goals_conceded_per_game": 1.2, "recent_form": 0.5},
            "head_to_head": {"matches_played": 0},
            "context": {"tournament_stage": "group_stage", "stakes": "medium"},
        }

        # Mock AI to return None (failure)
        with patch("app.services.world_cup_prediction_engine.predict_score_ai", return_value=None):
            result = await predict_match_score(
                "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage", factors
            )

        # Should fall back to rule-only
        self.assertEqual(result["prediction_method"], "rule_only")
        self.assertIsNone(result["ai_score"])
        self.assertIsNone(result["ai_reasoning"])


if __name__ == "__main__":
    unittest.main()
