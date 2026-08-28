import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import llm as llm_routes
from app.services import llm_gateway_diagnostics_service as diag
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


class CostCapDiagnosticsTests(unittest.TestCase):
    """``get_spend_today()`` is the number the cap is enforced against and it had
    no reader outside the gateway — no route, no CLI, no dashboard. An operator
    running with a cap could not tell how close they were to having every LLM
    call refused until it happened.
    """

    def _report(self):
        # Route resolution is irrelevant here; keep it cheap and key-free.
        with patch.object(gateway.settings, "LLM_ROUTE_DEFAULT", ""), \
                patch.object(gateway.settings, "OPENAI_API_KEY", ""), \
                patch.object(gateway.settings, "OPENAI_MODEL", ""):
            return build_llm_diagnostics()

    def test_the_endpoint_reports_spend_against_the_cap(self):
        from app.memory import llm_daily_spend_store

        with patch.object(diag.settings, "LLM_DAILY_COST_CAP_USD", 25.0), \
                patch.object(llm_daily_spend_store, "get_spend_today", return_value=5.0):
            block = self._report()["cost_cap"]

        self.assertTrue(block["enabled"])
        self.assertEqual(block["cap_usd"], 25.0)
        self.assertEqual(block["spend_today_usd"], 5.0)
        self.assertEqual(block["remaining_usd"], 20.0)
        self.assertEqual(block["used_ratio"], 0.2)
        self.assertEqual(block["status"], "ok")
        self.assertIsNone(block["error"])

    def test_status_escalates_with_the_ratio_not_the_absolute_spend(self):
        """A $4 spend is healthy against a $25 cap and exceeded against a $4 one,
        so the verdict must come from the ratio."""
        from app.memory import llm_daily_spend_store

        cases = [
            (25.0, 5.0, "ok"),
            (25.0, 20.0, "warning"),
            (25.0, 25.0, "exceeded"),
            (25.0, 40.0, "exceeded"),
            (4.0, 4.0, "exceeded"),
            (100.0, 4.0, "ok"),
        ]
        for cap, spend, expected in cases:
            with self.subTest(cap=cap, spend=spend):
                with patch.object(diag.settings, "LLM_DAILY_COST_CAP_USD", cap), \
                        patch.object(llm_daily_spend_store, "get_spend_today",
                                     return_value=spend):
                    block = self._report()["cost_cap"]
                self.assertEqual(block["status"], expected)
                # Remaining never goes negative, so a UI bar cannot invert.
                self.assertGreaterEqual(block["remaining_usd"], 0.0)

    def test_a_disabled_cap_reports_absence_and_never_touches_storage(self):
        """The gateway's contract is that the disabled default (0) does not touch
        SQLite; reading the store would CREATE TABLE on first use. And with no
        cap there is nothing to be close to, so the spend figure is ``None``
        rather than ``0.0`` — "not measured" must not read as "nothing spent".
        """
        from app.memory import llm_daily_spend_store

        with patch.object(diag.settings, "LLM_DAILY_COST_CAP_USD", 0.0), \
                patch.object(llm_daily_spend_store, "get_spend_today",
                             side_effect=AssertionError("storage must not be touched")):
            block = self._report()["cost_cap"]

        self.assertFalse(block["enabled"])
        self.assertIsNone(block["spend_today_usd"])
        self.assertIsNone(block["remaining_usd"])
        self.assertIsNone(block["used_ratio"])
        self.assertEqual(block["status"], "disabled")

    def test_a_broken_spend_store_degrades_instead_of_raising(self):
        """Same posture as the gateway's fail-OPEN cap check: a broken counter
        must not turn a read-only diagnostics call into a 500. The status is
        ``unknown``, not ``ok`` — a missing measurement is not a pass."""
        from app.memory import llm_daily_spend_store

        with patch.object(diag.settings, "LLM_DAILY_COST_CAP_USD", 25.0), \
                patch.object(llm_daily_spend_store, "get_spend_today",
                             side_effect=RuntimeError("disk gone")):
            block = self._report()["cost_cap"]

        self.assertTrue(block["enabled"])
        self.assertEqual(block["cap_usd"], 25.0)
        self.assertIsNone(block["spend_today_usd"])
        self.assertEqual(block["status"], "unknown")
        self.assertEqual(block["error"], "spend_lookup_failed")

    def test_the_block_never_carries_an_api_key(self):
        from app.memory import llm_daily_spend_store

        with patch.dict("os.environ", {"OPENAI_API_KEY_1": "indexed-secret",
                                       "OPENAI_MODEL_1_1": "m"}, clear=True), \
                patch.object(gateway.settings, "LLM_PROVIDER_DEEPSEEK_API_KEY",
                             "deepseek-secret"), \
                patch.object(diag.settings, "LLM_DAILY_COST_CAP_USD", 25.0), \
                patch.object(llm_daily_spend_store, "get_spend_today", return_value=1.0):
            report = build_llm_diagnostics()

        text = repr(report)
        self.assertNotIn("deepseek-secret", text)
        self.assertNotIn("indexed-secret", text)


class CostCapRouteWiringTests(unittest.TestCase):
    """A reader that no route returns is the defect this closes; pin the wiring."""

    def test_the_cost_cap_block_is_served_by_the_diagnostics_route(self):
        from app.memory import llm_daily_spend_store

        app = FastAPI()
        app.include_router(llm_routes.router, prefix="/llm")
        with patch.object(diag.settings, "LLM_DAILY_COST_CAP_USD", 25.0), \
                patch.object(llm_daily_spend_store, "get_spend_today", return_value=5.0):
            response = TestClient(app).get("/llm/diagnostics")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("cost_cap", payload)
        self.assertEqual(payload["cost_cap"]["spend_today_usd"], 5.0)
        self.assertEqual(payload["cost_cap"]["cap_usd"], 25.0)


if __name__ == "__main__":
    unittest.main()
