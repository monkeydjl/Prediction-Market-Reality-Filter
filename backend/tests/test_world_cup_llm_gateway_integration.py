"""Regression tests for World Cup services using the unified LLM Gateway."""

import unittest
from unittest.mock import AsyncMock, patch

from app.services.llm_gateway_service import LLMAttempt, LLMResult
from app.services.world_cup_ai_analysis_service import analyze_prediction_with_ai
from app.services.world_cup_ai_optimization_service import optimize_prediction_with_ai


def _chat_result(content: str) -> LLMResult:
    return LLMResult(
        ok=True,
        content=content,
        provider="openai_1",
        model="model-a",
        attempts=[LLMAttempt("openai_1", "model-a", "success")],
    )


def _json_result(data: dict) -> LLMResult:
    return LLMResult(
        ok=True,
        content="{}",
        json_data=data,
        provider="openai_1",
        model="model-a",
        attempts=[LLMAttempt("openai_1", "model-a", "success")],
    )


class WorldCupLLMGatewayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_uses_world_cup_gateway_route_without_legacy_openai_key(self):
        with patch(
            "app.services.world_cup_ai_analysis_service.has_configured_llm_route",
            return_value=True,
            create=True,
        ), patch(
            "app.services.world_cup_ai_analysis_service.complete_chat",
            new=AsyncMock(return_value=_chat_result("analysis ok")),
            create=True,
        ) as mock_complete:
            result = await analyze_prediction_with_ai(
                home_team="Brazil",
                away_team="Argentina",
                predicted_score={"home": 2.0, "away": 1.0},
                outcome_probabilities={"home_win": 0.5, "draw": 0.25, "away_win": 0.25},
                confidence=0.7,
                prediction_method="hybrid",
            )

        self.assertEqual(result, "analysis ok")
        self.assertEqual(mock_complete.await_args.kwargs["task"], "world_cup")

    async def test_optimization_uses_world_cup_gateway_route_without_legacy_openai_key(self):
        optimization = {
            "blind_spots": ["injuries"],
            "calibration_issues": ["overconfident"],
            "optimized_prediction": None,
        }
        current_prediction = {
            "predicted_score": {"home": 2.0, "away": 1.0},
            "outcome_probabilities": {"home_win": 0.5, "draw": 0.25, "away_win": 0.25},
            "confidence": 0.7,
        }

        with patch(
            "app.services.world_cup_ai_optimization_service.has_configured_llm_route",
            return_value=True,
            create=True,
        ), patch(
            "app.services.world_cup_ai_optimization_service.complete_json",
            new=AsyncMock(return_value=_json_result(optimization)),
            create=True,
        ) as mock_complete:
            result = await optimize_prediction_with_ai(
                home_team="Brazil",
                away_team="Argentina",
                current_prediction=current_prediction,
                prediction_method="hybrid",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["optimization"], optimization)
        self.assertEqual(mock_complete.await_args.kwargs["task"], "world_cup")


if __name__ == "__main__":
    unittest.main()
