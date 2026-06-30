"""Golden baseline tests for world_cup_ai_engine.

Lock AI engine behavior with mocked LLM responses.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.world_cup_engines.world_cup_ai_engine import predict_score_ai, build_ai_prediction_prompt


class AIEngineGoldenTests(unittest.IsolatedAsyncioTestCase):
    """Lock AI engine behavior with mocked LLM."""

    def setUp(self):
        """Common fixtures."""
        self.factors = {
            "home_team": {
                "goals_per_game": 2.0,
                "goals_conceded_per_game": 1.0,
                "recent_form": 0.7,
            },
            "away_team": {
                "goals_per_game": 1.5,
                "goals_conceded_per_game": 1.2,
                "recent_form": 0.5,
            },
            "head_to_head": {
                "matches_played": 5,
                "home_wins": 2,
                "draws": 2,
                "away_wins": 1,
                "avg_goals_home": 1.8,
                "avg_goals_away": 1.4,
            },
        }
        self.rule_prediction = {
            "predicted_score": {"home": 1.8, "away": 1.3},
            "outcome_probabilities": {"home_win": 0.45, "draw": 0.27, "away_win": 0.28},
            "confidence": 0.75,
        }

    async def test_predict_score_ai_no_api_key(self):
        """Lock behavior when OPENAI_API_KEY is not set."""
        with patch("app.services.world_cup_engines.world_cup_ai_engine.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = None

            result = await predict_score_ai(
                "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                self.factors, self.rule_prediction
            )

            self.assertIsNone(result)

    async def test_predict_score_ai_valid_json(self):
        """Lock AI adjustment when LLM returns valid JSON."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"home_adjustment": 0.2, "away_adjustment": -0.1, "reasoning": "巴西状态更好", "confidence_in_adjustment": 0.8}'
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            mock_settings.OPENAI_MODEL = "gpt-4"
            with patch("app.services.openai_service.get_client", return_value=mock_client):
                result = await predict_score_ai(
                    "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                    self.factors, self.rule_prediction
                )

        self.assertIsNotNone(result)
        # Adjusted scores: rule + adjustment
        # home = 1.8 + 0.2 = 2.0, away = 1.3 - 0.1 = 1.2
        self.assertAlmostEqual(result["predicted_score"]["home"], 2.0, places=2)
        self.assertAlmostEqual(result["predicted_score"]["away"], 1.2, places=2)
        # confidence_in_adjustment = 0.8 > 0.7, adjustment_magnitude = 0.2 > 0.3 is False
        # So final_confidence = rule_confidence - 0.05 = 0.75 - 0.05 = 0.70
        self.assertAlmostEqual(result["confidence"], 0.70, places=2)
        self.assertEqual(result["reasoning"], "巴西状态更好")
        self.assertAlmostEqual(result["confidence_in_adjustment"], 0.8, places=2)

    async def test_predict_score_ai_adjustment_clamped(self):
        """Lock that AI adjustments are clamped to [-1.0, +1.0]."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        # LLM tries to adjust by +2.0 and -1.5, should be clamped
        mock_response.choices[0].message.content = '{"home_adjustment": 2.0, "away_adjustment": -1.5, "reasoning": "极端调整", "confidence_in_adjustment": 0.9}'
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            mock_settings.OPENAI_MODEL = "gpt-4"
            with patch("app.services.openai_service.get_client", return_value=mock_client):
                result = await predict_score_ai(
                    "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                    self.factors, self.rule_prediction
                )

        # Adjustments clamped to +1.0 and -1.0
        # home = 1.8 + 1.0 = 2.8, away = max(0, 1.3 - 1.0) = 0.3
        self.assertAlmostEqual(result["predicted_score"]["home"], 2.8, places=2)
        self.assertAlmostEqual(result["predicted_score"]["away"], 0.3, places=2)

    async def test_predict_score_ai_high_confidence_boost(self):
        """Lock confidence boost when AI is confident and adjustment is meaningful."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        # confidence_in_adjustment > 0.7 AND adjustment_magnitude > 0.3 -> +0.05
        mock_response.choices[0].message.content = '{"home_adjustment": 0.4, "away_adjustment": 0.0, "reasoning": "重要调整", "confidence_in_adjustment": 0.85}'
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            mock_settings.OPENAI_MODEL = "gpt-4"
            with patch("app.services.openai_service.get_client", return_value=mock_client):
                result = await predict_score_ai(
                    "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                    self.factors, self.rule_prediction
                )

        # rule_confidence = 0.75, adjustment_magnitude = 0.4 > 0.3, confidence_in_adjustment = 0.85 > 0.7
        # final_confidence = min(0.95, 0.75 + 0.05) = 0.80
        self.assertAlmostEqual(result["confidence"], 0.80, places=2)

    async def test_predict_score_ai_no_json_in_response(self):
        """Lock behavior when LLM response contains no JSON."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Sorry, I cannot provide a prediction."
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            mock_settings.OPENAI_MODEL = "gpt-4"
            with patch("app.services.openai_service.get_client", return_value=mock_client):
                result = await predict_score_ai(
                    "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                    self.factors, self.rule_prediction
                )

        self.assertIsNone(result)

    async def test_predict_score_ai_llm_exception(self):
        """Lock behavior when LLM call raises exception."""
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API error"))

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            mock_settings.OPENAI_MODEL = "gpt-4"
            with patch("app.services.openai_service.get_client", return_value=mock_client):
                result = await predict_score_ai(
                    "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                    self.factors, self.rule_prediction
                )

        # Exception caught, returns None
        self.assertIsNone(result)

    def test_build_ai_prediction_prompt_structure(self):
        """Lock that prompt includes key context."""
        prompt = build_ai_prediction_prompt(
            "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
            self.factors, self.rule_prediction
        )

        # Check prompt contains team names
        self.assertIn("Brazil", prompt)
        self.assertIn("Argentina", prompt)
        # Check it includes rule prediction
        self.assertIn("1.8", prompt)
        self.assertIn("1.3", prompt)
        # Check it asks for JSON
        self.assertIn("JSON", prompt)
        self.assertIn("home_adjustment", prompt)


if __name__ == "__main__":
    unittest.main()
