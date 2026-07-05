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


    def test_build_route_uses_indexed_openai_env_when_no_explicit_route_exists(self):
        env = {
            "OPENAI_API_KEY_1": "key-1",
            "OPENAI_MODEL_1_1": "provider1-model1",
            "OPENAI_MODEL_1_2": "provider1-model2",
            "OPENAI_BASE_URL_1": "https://provider1.example/v1",
            "OPENAI_API_KEY_2": "key-2",
            "OPENAI_MODEL_2_1": "provider2-model1",
            "OPENAI_BASE_URL_2": "https://provider2.example/v1",
        }
        with patch.dict("os.environ", env, clear=True), \
             patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", ""), \
             patch.object(gateway.settings, "LLM_ROUTE_PROBABILITY_ANALYSIS", ""), \
             patch.object(gateway.settings, "OPENAI_MODEL", "legacy-model"):
            routes = gateway.build_route("default")
            configs = gateway._provider_configs()

        self.assertEqual(
            [(route.provider, route.models) for route in routes],
            [
                ("openai_1", ["provider1-model1", "provider1-model2"]),
                ("openai_2", ["provider2-model1"]),
            ],
        )
        self.assertEqual(configs["openai_1"].api_key, "key-1")
        self.assertEqual(configs["openai_1"].base_url, "https://provider1.example/v1")
        self.assertEqual(configs["openai_2"].api_key, "key-2")
        self.assertEqual(configs["openai_2"].base_url, "https://provider2.example/v1")

    def test_build_route_prefers_explicit_task_route_over_indexed_openai_env(self):
        env = {
            "OPENAI_API_KEY_1": "key-1",
            "OPENAI_MODEL_1_1": "indexed-model",
            "OPENAI_BASE_URL_1": "https://indexed.example/v1",
        }
        with patch.dict("os.environ", env, clear=True), \
             patch.object(gateway.settings, "LLM_ROUTE_PROBABILITY_ANALYSIS", "deepseek:reasoner"), \
             patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", "openai:gpt-4o-mini"):
            routes = gateway.build_route("probability_analysis")

        self.assertEqual([(route.provider, route.models) for route in routes], [("deepseek", ["reasoner"])])

    def test_build_route_uses_legacy_openai_when_no_new_route_exists(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", ""), \
             patch.object(gateway.settings, "LLM_ROUTE_PROBABILITY_ANALYSIS", ""), \
             patch.object(gateway.settings, "OPENAI_MODEL", "deepseek-chat"):
            routes = gateway.build_route("default")

        self.assertEqual(routes[0].provider, "legacy_openai")
        self.assertEqual(routes[0].models, ["deepseek-chat"])


    def test_has_configured_llm_route_detects_indexed_openai_env(self):
        env = {
            "OPENAI_API_KEY_1": "key-1",
            "OPENAI_MODEL_1_1": "provider1-model1",
            "OPENAI_BASE_URL_1": "https://provider1.example/v1",
        }
        with patch.dict("os.environ", env, clear=True), \
             patch.object(gateway.settings, "LLM_ROUTE_WORLD_CUP", ""), \
             patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", ""), \
             patch.object(gateway.settings, "OPENAI_MODEL", ""):
            self.assertTrue(gateway.has_configured_llm_route("world_cup"))

    def test_has_configured_llm_route_rejects_route_without_api_key(self):
        with patch.object(gateway.settings, "LLM_ROUTE_WORLD_CUP", "openai:gpt-4o-mini"), \
             patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", ""), \
             patch.object(gateway.settings, "LLM_PROVIDER_OPENAI_API_KEY", ""), \
             patch.object(gateway.settings, "OPENAI_API_KEY", ""):
            self.assertFalse(gateway.has_configured_llm_route("world_cup"))



if __name__ == "__main__":
    unittest.main()
from types import SimpleNamespace
from unittest.mock import AsyncMock


class _FakeCompletions:
    def __init__(self, create):
        self.create = AsyncMock(side_effect=create)


class _FakeChat:
    def __init__(self, create):
        self.completions = _FakeCompletions(create)


class _FakeClient:
    def __init__(self, create):
        self.chat = _FakeChat(create)


def _fake_client(create):
    return _FakeClient(create)


def _fake_response(content, prompt_tokens=3, completion_tokens=5):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


class LLMGatewayExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_json_falls_back_to_next_model_same_provider(self):
        calls = []

        async def fake_create(**kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "bad-model":
                raise RuntimeError("rate limit")
            return _fake_response('{"ok": true}')

        result = await gateway.complete_json(
            task="default",
            messages=[{"role": "user", "content": "x"}],
            route=[gateway.LLMModelRoute("p1", ["bad-model", "good-model"])],
            provider_configs={"p1": gateway.LLMProviderConfig("p1", "key", "http://example")},
            client_factory=lambda provider: _fake_client(fake_create),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.model, "good-model")
        self.assertEqual(result.json_data, {"ok": True})
        self.assertEqual(calls, ["bad-model", "good-model"])
        self.assertEqual([a.status for a in result.attempts], ["failed", "success"])
        self.assertEqual(result.usage, {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8})

    async def test_complete_json_falls_back_to_next_provider_after_provider_models_fail(self):
        calls = []

        async def fake_create(**kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] in {"p1-a", "p1-b"}:
                raise TimeoutError("timeout")
            return _fake_response('{"provider": "p2"}')

        result = await gateway.complete_json(
            messages=[{"role": "user", "content": "x"}],
            route=[
                gateway.LLMModelRoute("p1", ["p1-a", "p1-b"]),
                gateway.LLMModelRoute("p2", ["p2-a"]),
            ],
            provider_configs={
                "p1": gateway.LLMProviderConfig("p1", "key1", "http://p1"),
                "p2": gateway.LLMProviderConfig("p2", "key2", "http://p2"),
            },
            client_factory=lambda provider: _fake_client(fake_create),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "p2")
        self.assertEqual(result.model, "p2-a")
        self.assertEqual(calls, ["p1-a", "p1-b", "p2-a"])

    async def test_complete_json_uses_indexed_env_route_and_provider_order(self):
        calls = []
        client_configs = []
        env = {
            "OPENAI_API_KEY_1": "key-1",
            "OPENAI_MODEL_1_1": "provider1-model1",
            "OPENAI_MODEL_1_2": "provider1-model2",
            "OPENAI_BASE_URL_1": "https://provider1.example/v1",
            "OPENAI_API_KEY_2": "key-2",
            "OPENAI_MODEL_2_1": "provider2-model1",
            "OPENAI_BASE_URL_2": "https://provider2.example/v1",
        }

        async def fake_create(**kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"].startswith("provider1-"):
                raise TimeoutError("timeout")
            return _fake_response('{"provider": "openai_2"}')

        def fake_factory(config):
            client_configs.append((config.provider, config.api_key, config.base_url))
            return _fake_client(fake_create)

        with patch.dict("os.environ", env, clear=True), \
             patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", ""), \
             patch.object(gateway.settings, "LLM_ROUTE_PROBABILITY_ANALYSIS", ""), \
             patch.object(gateway.settings, "OPENAI_MODEL", "legacy-model"):
            result = await gateway.complete_json(
                messages=[{"role": "user", "content": "x"}],
                client_factory=fake_factory,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "openai_2")
        self.assertEqual(result.model, "provider2-model1")
        self.assertEqual(calls, ["provider1-model1", "provider1-model2", "provider2-model1"])
        self.assertEqual(
            client_configs,
            [
                ("openai_1", "key-1", "https://provider1.example/v1"),
                ("openai_2", "key-2", "https://provider2.example/v1"),
            ],
        )

    async def test_complete_json_returns_failed_result_when_all_models_fail(self):
        async def fake_create(**kwargs):
            raise RuntimeError("provider down")

        result = await gateway.complete_json(
            messages=[{"role": "user", "content": "x"}],
            route=[gateway.LLMModelRoute("p1", ["m1", "m2"])],
            provider_configs={"p1": gateway.LLMProviderConfig("p1", "key", "http://p1")},
            client_factory=lambda provider: _fake_client(fake_create),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.degraded_reason, "all_routes_failed")
        self.assertEqual([a.model for a in result.attempts], ["m1", "m2"])
        self.assertTrue(all(a.status == "failed" for a in result.attempts))

    async def test_complete_json_falls_back_when_content_is_invalid_json(self):
        calls = []

        async def fake_create(**kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "bad-json":
                return _fake_response("not json")
            return _fake_response('{"ok": true}')

        result = await gateway.complete_json(
            messages=[{"role": "user", "content": "x"}],
            route=[gateway.LLMModelRoute("p1", ["bad-json", "good-json"])],
            provider_configs={"p1": gateway.LLMProviderConfig("p1", "key", "http://p1")},
            client_factory=lambda provider: _fake_client(fake_create),
        )

        self.assertTrue(result.ok)
        self.assertEqual(calls, ["bad-json", "good-json"])
        self.assertEqual(result.attempts[0].error_type, "invalid_json")

    async def test_complete_chat_skips_provider_with_missing_api_key(self):
        async def fake_create(**kwargs):
            return _fake_response("ok")

        result = await gateway.complete_chat(
            messages=[{"role": "user", "content": "x"}],
            route=[
                gateway.LLMModelRoute("missing", ["m1"]),
                gateway.LLMModelRoute("valid", ["m2"]),
            ],
            provider_configs={
                "missing": gateway.LLMProviderConfig("missing", "", "http://missing"),
                "valid": gateway.LLMProviderConfig("valid", "key", "http://valid"),
            },
            client_factory=lambda provider: _fake_client(fake_create),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "valid")
        self.assertEqual(result.attempts[0].status, "skipped")
        self.assertEqual(result.attempts[0].error_type, "missing_api_key")

    async def test_complete_chat_falls_back_when_provider_error_leaks_in_content(self):
        async def fake_create(**kwargs):
            if kwargs["model"] == "busy":
                return _fake_response("模型负载过高，请稍后再试")
            return _fake_response("正常结果")

        result = await gateway.complete_chat(
            messages=[{"role": "user", "content": "x"}],
            route=[gateway.LLMModelRoute("p1", ["busy", "ok-model"])],
            provider_configs={"p1": gateway.LLMProviderConfig("p1", "key", "http://p1")},
            client_factory=lambda provider: _fake_client(fake_create),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.model, "ok-model")
        self.assertEqual(result.content, "正常结果")
        self.assertEqual(result.attempts[0].error_type, "provider_error_in_content")
