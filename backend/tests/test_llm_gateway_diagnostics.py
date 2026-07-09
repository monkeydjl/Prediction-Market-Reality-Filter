import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import llm as llm_routes
from app.services import llm_gateway_service as gateway
from app.services.llm_gateway_diagnostics_service import build_llm_diagnostics


class LLMGatewayDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_reports_routes_without_exposing_api_keys(self):
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY_1": "indexed-secret",
                "OPENAI_MODEL_1_1": "indexed-a",
                "OPENAI_MODEL_1_2": "indexed-b",
                "OPENAI_BASE_URL_1": "https://indexed.example/v1",
            },
            clear=True,
        ), patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", "deepseek:reasoner|openai:gpt-4o-mini"), \
            patch.object(gateway.settings, "LLM_ROUTE_TRANSLATION", ""), \
            patch.object(gateway.settings, "LLM_PROVIDER_DEEPSEEK_API_KEY", "deepseek-secret"), \
            patch.object(gateway.settings, "LLM_PROVIDER_OPENAI_API_KEY", ""), \
            patch.object(gateway.settings, "OPENAI_API_KEY", ""), \
            patch.object(gateway.settings, "OPENAI_MODEL", ""):
            report = build_llm_diagnostics()

        default = next(task for task in report["tasks"] if task["task"] == "default")
        self.assertTrue(default["configured"])
        self.assertEqual(default["route_source"], "task")
        self.assertEqual(default["routes"][0]["provider"], "deepseek")
        self.assertTrue(default["routes"][0]["api_key_configured"])
        self.assertFalse(default["routes"][1]["api_key_configured"])

        translation = next(task for task in report["tasks"] if task["task"] == "translation")
        self.assertEqual(translation["route_source"], "default")
        self.assertTrue(translation["configured"])

        text = repr(report)
        self.assertNotIn("deepseek-secret", text)
        self.assertNotIn("indexed-secret", text)

    def test_diagnostics_route_is_read_only_and_public(self):
        app = FastAPI()
        app.include_router(llm_routes.router, prefix="/llm")
        with patch("app.api.routes.llm.build_llm_diagnostics", return_value={"tasks": []}):
            response = TestClient(app).get("/llm/diagnostics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"tasks": []})


if __name__ == "__main__":
    unittest.main()
