import unittest
from unittest.mock import patch

from app.services import llm_gateway_service as gateway


class LLMGatewayRouteTests(unittest.TestCase):
    def test_parse_route_string_keeps_provider_and_model_order(self):
        routes = gateway.parse_route_string(
            "deepseek:deepseek-chat,deepseek-reasoner|dashscope:qwen-plus"
        )

        self.assertEqual(
            [(route.provider, route.models) for route in routes],
            [
                ("deepseek", ["deepseek-chat", "deepseek-reasoner"]),
                ("dashscope", ["qwen-plus"]),
            ],
        )

    def test_parse_route_string_ignores_empty_and_invalid_segments(self):
        routes = gateway.parse_route_string(
            " deepseek: deepseek-chat , | missing-models: | :missing-provider | openai:gpt-4o-mini "
        )

        self.assertEqual(
            [(route.provider, route.models) for route in routes],
            [
                ("deepseek", ["deepseek-chat"]),
                ("openai", ["gpt-4o-mini"]),
            ],
        )

    def test_build_route_prefers_task_route_then_default_route(self):
        with patch.object(gateway.settings, "LLM_ROUTE_PROBABILITY_ANALYSIS", "deepseek:reasoner"), \
             patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", "openai:gpt-4o-mini"):
            task_routes = gateway.build_route("probability_analysis")
            default_routes = gateway.build_route("translation")

        self.assertEqual([(r.provider, r.models) for r in task_routes], [("deepseek", ["reasoner"])])
        self.assertEqual([(r.provider, r.models) for r in default_routes], [("openai", ["gpt-4o-mini"])])

    def test_build_route_uses_legacy_openai_when_no_new_route_exists(self):
        with patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", ""), \
             patch.object(gateway.settings, "LLM_ROUTE_PROBABILITY_ANALYSIS", ""), \
             patch.object(gateway.settings, "OPENAI_MODEL", "deepseek-chat"):
            routes = gateway.build_route("default")

        self.assertEqual(routes[0].provider, "legacy_openai")
        self.assertEqual(routes[0].models, ["deepseek-chat"])


if __name__ == "__main__":
    unittest.main()
