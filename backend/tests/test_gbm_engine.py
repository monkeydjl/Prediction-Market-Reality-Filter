"""Tests for the GBM (LightGBM) World Cup prediction engine.

Covers:
1. Engine registration in ENGINES dict
2. predict_match_gbm output schema completeness
3. Probability sanity (sum to 1, all positive)
4. bucket_engine recognizes "gbm"
5. Feature derivation matches FEATURE_NAMES length
6. Fallback behavior when model files are missing
"""

import unittest
from unittest.mock import patch


class GbmEngineRegistrationTests(unittest.TestCase):
    """Verify GBM engine is properly registered."""

    def test_gbm_registered_in_engines_dict(self):
        """'gbm' key should be in ENGINES registry."""
        from app.services.world_cup_engines import ENGINES
        self.assertIn("gbm", ENGINES)

    def test_get_engine_returns_gbm_function(self):
        """get_engine('gbm') should return the predict_match_gbm callable."""
        from app.services.world_cup_engines import get_engine
        from app.services.world_cup_engines.world_cup_gbm_engine import predict_match_gbm
        engine_fn = get_engine("gbm")
        self.assertEqual(engine_fn, predict_match_gbm)

    def test_bucket_engine_recognizes_gbm(self):
        """bucket_engine should map gbm methods to 'gbm' bucket."""
        from app.services.world_cup_quality_service import bucket_engine
        self.assertEqual(bucket_engine("gbm_lightgbm"), "gbm")
        self.assertEqual(bucket_engine("gbm_fallback_elo"), "gbm")

    def test_engine_names_includes_gbm(self):
        """ENGINE_NAMES tuple should include 'gbm'."""
        from app.services.world_cup_quality_service import ENGINE_NAMES
        self.assertIn("gbm", ENGINE_NAMES)


class GbmEnginePredictionTests(unittest.TestCase):
    """Validate prediction output schema and numerical sanity."""

    def test_prediction_has_required_fields(self):
        """predict_match_gbm output must contain all required fields."""
        from app.services.world_cup_engines.world_cup_gbm_engine import predict_match_gbm
        pred = predict_match_gbm("Brazil", "Argentina", 2100, 2050)

        # Required fields for pipeline compatibility
        required_fields = [
            "home_team", "away_team",
            "predicted_score", "outcome_probabilities",
            "confidence", "prediction_method",
            "expected_goals", "elo_ratings",
            "has_betting_odds", "market_probabilities",
            "rule_score", "ai_score", "ai_reasoning", "key_factors",
            "model_loaded",
        ]
        for field in required_fields:
            with self.subTest(field=field):
                self.assertIn(field, pred, f"Missing required field: {field}")

    def test_predicted_score_structure(self):
        """predicted_score must be a dict with home and away floats."""
        from app.services.world_cup_engines.world_cup_gbm_engine import predict_match_gbm
        pred = predict_match_gbm("France", "Germany", 2080, 2070)
        score = pred["predicted_score"]
        self.assertIsInstance(score, dict)
        self.assertIn("home", score)
        self.assertIn("away", score)
        self.assertIsInstance(score["home"], (int, float))
        self.assertIsInstance(score["away"], (int, float))
        # xG should be in reasonable range
        self.assertGreater(score["home"], 0.0)
        self.assertGreater(score["away"], 0.0)
        self.assertLess(score["home"], 6.0)
        self.assertLess(score["away"], 6.0)

    def test_outcome_probabilities_sum_to_one(self):
        """outcome_probabilities must sum to ~1.0."""
        from app.services.world_cup_engines.world_cup_gbm_engine import predict_match_gbm
        pred = predict_match_gbm("Spain", "Portugal", 2050, 2000)
        probs = pred["outcome_probabilities"]
        total = probs["home_win"] + probs["draw"] + probs["away_win"]
        # Rounded to 4 decimals, allow small delta
        self.assertAlmostEqual(total, 1.0, places=3)

    def test_outcome_probabilities_all_positive(self):
        """All outcome probabilities must be positive."""
        from app.services.world_cup_engines.world_cup_gbm_engine import predict_match_gbm
        pred = predict_match_gbm("England", "Italy", 2050, 2040)
        probs = pred["outcome_probabilities"]
        self.assertGreater(probs["home_win"], 0.0)
        self.assertGreater(probs["draw"], 0.0)
        self.assertGreater(probs["away_win"], 0.0)

    def test_prediction_method_starts_with_gbm(self):
        """prediction_method should start with 'gbm'."""
        from app.services.world_cup_engines.world_cup_gbm_engine import predict_match_gbm
        pred = predict_match_gbm("Brazil", "Mexico", 2100, 1800)
        self.assertTrue(pred["prediction_method"].startswith("gbm"))

    def test_higher_elo_gives_higher_home_win_prob(self):
        """Higher Elo should give higher home_win probability."""
        from app.services.world_cup_engines.world_cup_gbm_engine import predict_match_gbm
        # Use same teams to avoid confounding from historical stats
        pred_low = predict_match_gbm("Brazil", "Argentina", 2000, 2000)
        pred_high = predict_match_gbm("Brazil", "Argentina", 2200, 1800)
        self.assertGreater(
            pred_high["outcome_probabilities"]["home_win"],
            pred_low["outcome_probabilities"]["home_win"],
        )

    def test_confidence_in_valid_range(self):
        """Confidence should be in [0.0, 1.0]."""
        from app.services.world_cup_engines.world_cup_gbm_engine import predict_match_gbm
        pred = predict_match_gbm("France", "Germany", 2080, 2070)
        self.assertGreaterEqual(pred["confidence"], 0.0)
        self.assertLessEqual(pred["confidence"], 1.0)

    def test_elo_ratings_preserved(self):
        """elo_ratings should reflect input Elo."""
        from app.services.world_cup_engines.world_cup_gbm_engine import predict_match_gbm
        pred = predict_match_gbm("Spain", "Germany", 2080, 2070)
        self.assertEqual(pred["elo_ratings"]["home"], 2080)
        self.assertEqual(pred["elo_ratings"]["away"], 2070)
        self.assertAlmostEqual(pred["elo_ratings"]["difference"], 10.0, places=1)


class GbmFeatureDerivationTests(unittest.TestCase):
    """Verify feature derivation produces correctly-sized vectors."""

    def test_feature_names_length(self):
        """FEATURE_NAMES should have the expected count."""
        from app.services.world_cup_engines.world_cup_gbm_features import FEATURE_NAMES
        self.assertEqual(len(FEATURE_NAMES), 17)

    def test_derive_features_returns_correct_length(self):
        """derive_gbm_features should return a vector matching FEATURE_NAMES length."""
        from app.services.world_cup_engines.world_cup_gbm_features import (
            derive_gbm_features, FEATURE_NAMES,
        )
        features = derive_gbm_features(
            elo_home=2100, elo_away=2000,
            home_stats={"goals_per_game": 2.0, "goals_conceded_per_game": 1.0,
                       "wins": 6, "draws": 2, "losses": 2, "played": 10,
                       "last_match_date": "2026-06-01"},
            away_stats={"goals_per_game": 1.5, "goals_conceded_per_game": 1.2,
                        "wins": 4, "draws": 3, "losses": 3, "played": 10,
                        "last_match_date": "2026-06-01"},
            h2h={"matches_played": 5, "home_wins": 3, "draws": 1, "away_wins": 1,
                 "avg_goals_home": 2.0, "avg_goals_away": 1.0},
            is_neutral=True, is_world_cup=True,
        )
        self.assertEqual(len(features), len(FEATURE_NAMES))

    def test_derive_features_handles_none_stats(self):
        """derive_gbm_features should handle None stats/h2h gracefully."""
        from app.services.world_cup_engines.world_cup_gbm_features import (
            derive_gbm_features, FEATURE_NAMES,
        )
        features = derive_gbm_features(
            elo_home=2000, elo_away=2000,
            home_stats=None, away_stats=None, h2h=None,
            is_neutral=True, is_world_cup=True,
        )
        self.assertEqual(len(features), len(FEATURE_NAMES))
        # All values should be finite numbers
        for v in features:
            self.assertIsInstance(v, (int, float))
            self.assertFalse(isinstance(v, bool))


class GbmEngineFallbackTests(unittest.TestCase):
    """Verify fallback behavior when models are unavailable."""

    def test_fallback_when_models_missing(self):
        """When models are missing, should fall back to Elo baseline."""
        from app.services.world_cup_engines.world_cup_gbm_engine import predict_match_gbm, _load_models
        # Clear cache
        _load_models.cache_clear()

        with patch(
            "app.services.world_cup_engines.world_cup_gbm_engine._HOME_MODEL_PATH"
        ) as mock_home, patch(
            "app.services.world_cup_engines.world_cup_gbm_engine._AWAY_MODEL_PATH"
        ) as mock_away:
            # Simulate missing files
            from pathlib import Path
            mock_home.exists.return_value = False
            mock_away.exists.return_value = False
            _load_models.cache_clear()

            pred = predict_match_gbm("Brazil", "Argentina", 2100, 2050)

        self.assertEqual(pred["prediction_method"], "gbm_fallback_elo")
        self.assertFalse(pred["model_loaded"])
        # Fallback still produces valid predictions
        self.assertIn("predicted_score", pred)
        self.assertIn("outcome_probabilities", pred)
        # Restore cache for other tests
        _load_models.cache_clear()


if __name__ == "__main__":
    unittest.main()
