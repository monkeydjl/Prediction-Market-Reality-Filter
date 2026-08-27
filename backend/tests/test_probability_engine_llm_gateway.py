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

    async def test_ask_ai_forwards_the_model_that_actually_served_the_call(self):
        """The gateway falls back between providers, so the served model is only
        knowable from its result. Cost telemetry prices by this; without it the
        block falls back to ``settings.OPENAI_MODEL``, the legacy last-resort
        name, which understated a gpt-4-served call by 214x."""
        gateway_result = LLMResult(
            ok=True,
            content='{"ai_probability": 61}',
            json_data={"ai_probability": 61},
            provider="openai",
            model="gpt-4-turbo",
            attempts=[LLMAttempt("openai", "gpt-4-turbo", "success")],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

        with patch("app.services.probability_engine_service.complete_json", new=AsyncMock(return_value=gateway_result)):
            result = await pe._ask_ai(
                market_question="Will X happen?",
                market_probability=42,
                news_context="Evidence: official update supports X.",
            )

        self.assertEqual(result["_llm_model"], "gpt-4-turbo")

    async def test_ask_ai_omits_the_served_model_when_the_gateway_recorded_none(self):
        """Non-vacuous baseline: the key is absent rather than empty, so the
        telemetry fallback to the configured model stays reachable."""
        gateway_result = LLMResult(
            ok=True,
            content='{"ai_probability": 61}',
            json_data={"ai_probability": 61},
            provider="p1",
            model="",
            attempts=[LLMAttempt("p1", "", "success")],
        )

        with patch("app.services.probability_engine_service.complete_json", new=AsyncMock(return_value=gateway_result)):
            result = await pe._ask_ai(
                market_question="Will X happen?",
                market_probability=42,
                news_context="Evidence: official update supports X.",
            )

        self.assertNotIn("_llm_model", result)

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
