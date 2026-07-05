import unittest
from unittest.mock import AsyncMock, patch

import app.services.probability_engine_service as pe
from app.services.llm_gateway_service import LLMResult, LLMAttempt


class ProbabilityEngineLLMGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_ai_uses_llm_gateway_json_result_and_usage(self):
        gateway_result = LLMResult(
            ok=True,
            content='{"ai_probability": 61}',
            json_data={
                "ai_probability": 61,
                "narrative_type": "factual",
                "reasoning": "结构化证据支持。",
            },
            provider="p1",
            model="m1",
            attempts=[LLMAttempt("p1", "m1", "success")],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

        with patch("app.services.probability_engine_service.complete_json", new=AsyncMock(return_value=gateway_result)) as mock_complete:
            result = await pe._ask_ai(
                market_question="Will X happen?",
                market_probability=42,
                news_context="Evidence: official update supports X.",
            )

        self.assertEqual(result["ai_probability"], 61)
        self.assertEqual(result["_llm_usage"], {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self.assertEqual(mock_complete.await_args.kwargs["task"], "probability_analysis")
        self.assertEqual(mock_complete.await_args.kwargs["temperature"], 0)

    async def test_ask_ai_raises_when_gateway_returns_failed_result(self):
        gateway_result = LLMResult(
            ok=False,
            attempts=[LLMAttempt("p1", "m1", "failed", "rate_limit")],
            degraded_reason="all_routes_failed",
        )

        with patch("app.services.probability_engine_service.complete_json", new=AsyncMock(return_value=gateway_result)):
            with self.assertRaisesRegex(RuntimeError, "all_routes_failed"):
                await pe._ask_ai(
                    market_question="Will X happen?",
                    market_probability=42,
                    news_context="Evidence: official update supports X.",
                )

    async def test_translate_title_uses_gateway_and_keeps_english_on_failure(self):
        gateway_result = LLMResult(
            ok=False,
            attempts=[LLMAttempt("p1", "m1", "failed", "timeout")],
            degraded_reason="all_routes_failed",
        )

        with patch("app.services.probability_engine_service.complete_chat", new=AsyncMock(return_value=gateway_result)) as mock_complete:
            result = await pe.translate_title("Will a new product launch before July?")

        self.assertEqual(result, "Will a new product launch before July?")
        self.assertEqual(mock_complete.await_args.kwargs["task"], "translation")

    async def test_translate_title_returns_gateway_content_on_success(self):
        gateway_result = LLMResult(
            ok=True,
            content="新产品会在七月前发布吗？",
            provider="p1",
            model="m1",
            attempts=[LLMAttempt("p1", "m1", "success")],
        )

        with patch("app.services.probability_engine_service.complete_chat", new=AsyncMock(return_value=gateway_result)):
            result = await pe.translate_title("Will a new product launch before July?")

        self.assertEqual(result, "新产品会在七月前发布吗？")


if __name__ == "__main__":
    unittest.main()
