"""Golden baseline tests for world_cup_ai_engine.

Lock AI engine behavior with mocked LLM responses.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.services.llm_gateway_service import LLMAttempt, LLMResult
from app.services.world_cup_engines.world_cup_ai_engine import (
    build_ai_prediction_prompt,
    predict_score_ai,
)


def _gateway_json_result(data: dict) -> LLMResult:
    return LLMResult(
        ok=True,
        content="{}",
        json_data=data,
        provider="p1",
        model="m1",
        attempts=[LLMAttempt("p1", "m1", "success")],
    )


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
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.LLM_PROVIDER_DEEPSEEK_API_KEY = ""
            mock_settings.LLM_PROVIDER_DASHSCOPE_API_KEY = ""
            mock_settings.LLM_PROVIDER_OPENAI_API_KEY = ""
            mock_settings.LLM_PROVIDER_OPENROUTER_API_KEY = ""

            result = await predict_score_ai(
                "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                self.factors, self.rule_prediction
            )

            self.assertIsNone(result)

    async def test_predict_score_ai_valid_json(self):
        """Lock AI adjustment when LLM returns valid JSON."""
        gateway_result = _gateway_json_result({
            "home_adjustment": 0.2,
            "away_adjustment": -0.1,
            "reasoning": "Brazil form is better",
            "confidence_in_adjustment": 0.8,
        })

        with patch("app.services.world_cup_engines.world_cup_ai_engine.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            with patch("app.services.world_cup_engines.world_cup_ai_engine.complete_json", new=AsyncMock(return_value=gateway_result)) as mock_complete:
                result = await predict_score_ai(
                    "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                    self.factors, self.rule_prediction
                )

        self.assertIsNotNone(result)
        self.assertEqual(mock_complete.await_args.kwargs["task"], "world_cup")
        self.assertAlmostEqual(result["predicted_score"]["home"], 2.0, places=2)
        self.assertAlmostEqual(result["predicted_score"]["away"], 1.2, places=2)
        self.assertAlmostEqual(result["confidence"], 0.70, places=2)
        self.assertEqual(result["reasoning"], "Brazil form is better")
        self.assertAlmostEqual(result["confidence_in_adjustment"], 0.8, places=2)

    async def test_predict_score_ai_adjustment_clamped(self):
        """Lock that AI adjustments are clamped to [-1.0, +1.0]."""
        gateway_result = _gateway_json_result({
            "home_adjustment": 2.0,
            "away_adjustment": -1.5,
            "reasoning": "Extreme adjustment",
            "confidence_in_adjustment": 0.9,
        })

        with patch("app.services.world_cup_engines.world_cup_ai_engine.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            with patch("app.services.world_cup_engines.world_cup_ai_engine.complete_json", new=AsyncMock(return_value=gateway_result)):
                result = await predict_score_ai(
                    "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                    self.factors, self.rule_prediction
                )

        self.assertAlmostEqual(result["predicted_score"]["home"], 2.8, places=2)
        self.assertAlmostEqual(result["predicted_score"]["away"], 0.3, places=2)

    async def test_predict_score_ai_high_confidence_boost(self):
        """Lock confidence boost when AI is confident and adjustment is meaningful."""
        gateway_result = _gateway_json_result({
            "home_adjustment": 0.4,
            "away_adjustment": 0.0,
            "reasoning": "Important adjustment",
            "confidence_in_adjustment": 0.85,
        })

        with patch("app.services.world_cup_engines.world_cup_ai_engine.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            with patch("app.services.world_cup_engines.world_cup_ai_engine.complete_json", new=AsyncMock(return_value=gateway_result)):
                result = await predict_score_ai(
                    "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                    self.factors, self.rule_prediction
                )

        self.assertAlmostEqual(result["confidence"], 0.80, places=2)

    async def test_predict_score_ai_failed_gateway_result(self):
        """Lock behavior when Gateway cannot produce valid JSON."""
        gateway_result = LLMResult(
            ok=False,
            attempts=[LLMAttempt("p1", "m1", "failed", "invalid_json")],
            degraded_reason="all_routes_failed",
        )

        with patch("app.services.world_cup_engines.world_cup_ai_engine.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            with patch("app.services.world_cup_engines.world_cup_ai_engine.complete_json", new=AsyncMock(return_value=gateway_result)):
                result = await predict_score_ai(
                    "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                    self.factors, self.rule_prediction
                )

        self.assertIsNone(result)

    async def test_predict_score_ai_llm_exception(self):
        """Lock behavior when LLM call raises exception."""
        with patch("app.services.world_cup_engines.world_cup_ai_engine.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "test-key"
            with patch("app.services.world_cup_engines.world_cup_ai_engine.complete_json", new=AsyncMock(side_effect=Exception("API error"))):
                result = await predict_score_ai(
                    "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
                    self.factors, self.rule_prediction
                )

        self.assertIsNone(result)

    def test_build_ai_prediction_prompt_structure(self):
        """Lock that prompt includes key context."""
        prompt = build_ai_prediction_prompt(
            "Brazil", "Argentina", "2026-06-15T18:00:00", "group_stage",
            self.factors, self.rule_prediction
        )

        self.assertIn("Brazil", prompt)
        self.assertIn("Argentina", prompt)
        self.assertIn("1.8", prompt)
        self.assertIn("1.3", prompt)
        self.assertIn("JSON", prompt)
        self.assertIn("home_adjustment", prompt)


if __name__ == "__main__":
    unittest.main()
