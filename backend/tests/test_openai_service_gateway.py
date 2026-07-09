import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services import openai_service
from app.services.llm_gateway_service import LLMResult


class OpenAIServiceGatewayTests(unittest.TestCase):
    def test_ask_llm_uses_gateway_without_legacy_client(self):
        gateway = AsyncMock(return_value=LLMResult(ok=True, json_data={
            "true_probability": 72,
            "confidence": 0.8,
            "narrative_type": "factual",
            "reasoning": "Evidence supports the outcome.",
        }))

        with patch.object(openai_service, "complete_json", gateway, create=True):
            result = asyncio.run(openai_service.ask_llm("Will X happen?"))

        self.assertEqual(result, {
            "probability": 72.0,
            "confidence": 0.8,
            "narrative_type": "factual",
            "reasoning": "Evidence supports the outcome.",
        })
        gateway.assert_awaited_once()
        self.assertEqual(gateway.await_args.kwargs["task"], "probability_analysis")


if __name__ == "__main__":
    unittest.main()
