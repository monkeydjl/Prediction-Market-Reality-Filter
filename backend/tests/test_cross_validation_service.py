"""
Tests for multi-model cross-validation (cross_validation_service) and its
attachment to the event record in event_intelligence_service.analyze_event.

The live second-model call (_ask_second_model) is mocked, so these are
network-free. The agreement thresholds, the disabled/error paths, and the
additive record attachment are all locked here.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import app.services.ai_analysis_service as ai
import app.services.cross_validation_service as cv
import app.services.event_intelligence_service as eis
from app.services.llm_gateway_service import LLMResult


def _run(coro):
    return asyncio.run(coro)


class CrossValidateTests(unittest.TestCase):
    def test_disabled_when_no_model_configured(self):
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", ""), \
                patch.object(cv.settings, "LLM_ROUTE_CROSS_VALIDATION", ""):
            self.assertIsNone(_run(cv.cross_validate("Q?", "ctx", 60.0)))

    def test_default_gateway_route_does_not_enable_cross_validation(self):
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", ""), \
                patch.object(cv.settings, "LLM_ROUTE_CROSS_VALIDATION", ""), \
                patch.object(cv, "_ask_second_model", new=AsyncMock()) as ask:
            self.assertIsNone(_run(cv.cross_validate("Q?", "ctx", 60.0)))

        ask.assert_not_awaited()

    def test_explicit_gateway_route_enables_cross_validation_without_legacy_model(self):
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", ""), \
                patch.object(cv.settings, "LLM_ROUTE_CROSS_VALIDATION", "openai:validator"), \
                patch.object(cv, "_ask_second_model", new=AsyncMock(return_value={"ai_probability": 64})):
            result = _run(cv.cross_validate("Q?", "ctx", 60.0))

        self.assertEqual(result["model"], "")
        self.assertEqual(result["probability"], 64.0)

    def test_high_agreement(self):
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "second-model"), \
                patch.object(cv, "_ask_second_model",
                             new=AsyncMock(return_value={"ai_probability": 64})):
            result = _run(cv.cross_validate("Q?", "ctx", 60.0))
        self.assertEqual(result, {
            "model": "second-model",
            "probability": 64.0,
            "primary_probability": 60.0,
            "divergence": 4.0,
            "agreement": "high",
        })

    def test_medium_agreement(self):
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "m"), \
                patch.object(cv, "_ask_second_model",
                             new=AsyncMock(return_value={"ai_probability": 40})):
            result = _run(cv.cross_validate("Q?", "ctx", 60.0))
        self.assertEqual(result["divergence"], 20.0)
        self.assertEqual(result["agreement"], "medium")

    def test_low_agreement(self):
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "m"), \
                patch.object(cv, "_ask_second_model",
                             new=AsyncMock(return_value={"ai_probability": 20})):
            result = _run(cv.cross_validate("Q?", "ctx", 60.0))
        self.assertEqual(result["divergence"], 40.0)
        self.assertEqual(result["agreement"], "low")

    def test_invalid_probability_returns_none(self):
        """A missing ai_probability is a non-answer, not agreement.

        Falling back to the primary estimate would make divergence 0 ->
        agreement "high" -> credibility_delta +5, i.e. the system would
        reward itself for a second model that said nothing.
        """
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "m"), \
                patch.object(cv, "_ask_second_model",
                             new=AsyncMock(return_value={"ai_probability": None})):
            self.assertIsNone(_run(cv.cross_validate("Q?", "ctx", 55.0)))

    def test_non_finite_probability_returns_none(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), \
                    patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "m"), \
                    patch.object(cv, "_ask_second_model",
                                 new=AsyncMock(return_value={"ai_probability": value})):
                self.assertIsNone(_run(cv.cross_validate("Q?", "ctx", 55.0)))

    def test_zero_probability_is_a_real_answer(self):
        """0% is a genuine estimate and must not be treated as "no answer"."""
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "m"), \
                patch.object(cv, "_ask_second_model",
                             new=AsyncMock(return_value={"ai_probability": 0})):
            result = _run(cv.cross_validate("Q?", "ctx", 55.0))
        self.assertIsNotNone(result)
        self.assertEqual(result["probability"], 0.0)
        self.assertEqual(result["divergence"], 55.0)
        self.assertEqual(result["agreement"], "low")

    def test_out_of_range_probability_is_clamped(self):
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "m"), \
                patch.object(cv, "_ask_second_model",
                             new=AsyncMock(return_value={"ai_probability": 150})):
            result = _run(cv.cross_validate("Q?", "ctx", 60.0))
        self.assertEqual(result["probability"], 100.0)

    def test_error_returns_none(self):
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "m"), \
                patch.object(cv, "_ask_second_model",
                             new=AsyncMock(side_effect=RuntimeError("down"))), \
                self.assertLogs("app.services.cross_validation_service",
                                level="WARNING") as logs:
            self.assertIsNone(_run(cv.cross_validate("Q?", "ctx", 60.0)))
        text = "\n".join(logs.output)
        self.assertIn("source=cross_validation", text)
        self.assertIn("policy=fail_closed_none", text)

    def test_credibility_delta_mapping(self):
        self.assertEqual(cv.credibility_delta("high"), 5)
        self.assertEqual(cv.credibility_delta("medium"), 0)
        self.assertEqual(cv.credibility_delta("low"), -15)
        self.assertEqual(cv.credibility_delta("unknown"), 0)

    def test_ask_second_model_uses_gateway(self):
        gateway = AsyncMock(return_value=LLMResult(
            ok=True,
            json_data={"ai_probability": 64},
        ))
        with patch.object(cv.settings, "OPENAI_API_KEY", ""), \
                patch.object(cv, "complete_json", gateway, create=True):
            result = _run(cv._ask_second_model("Q?", 50.0, "ctx"))

        self.assertEqual(result, {"ai_probability": 64})
        gateway.assert_awaited_once()
        self.assertEqual(gateway.await_args.kwargs["task"], "cross_validation")

    def test_legacy_cross_validation_model_supplies_gateway_route(self):
        gateway = AsyncMock(return_value=LLMResult(
            ok=True,
            json_data={"ai_probability": 64},
        ))
        with patch.object(cv.settings, "LLM_ROUTE_CROSS_VALIDATION", ""), \
                patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "legacy-validator"), \
                patch.object(cv.settings, "CROSS_VALIDATION_API_KEY", "cv-key"), \
                patch.object(cv.settings, "CROSS_VALIDATION_BASE_URL", "https://cv.example/v1"), \
                patch.object(cv, "complete_json", gateway, create=True):
            _run(cv._ask_second_model("Q?", 50.0, "ctx"))

        route = gateway.await_args.kwargs["route"]
        configs = gateway.await_args.kwargs["provider_configs"]
        self.assertEqual([(item.provider, item.models) for item in route], [
            ("legacy_cross_validation", ["legacy-validator"]),
        ])
        self.assertEqual(configs["legacy_cross_validation"].api_key, "cv-key")
        self.assertEqual(configs["legacy_cross_validation"].base_url, "https://cv.example/v1")


class AnalyzeEventCrossValidationTests(unittest.TestCase):
    NEWS = "direction: support\nstrength: 0.5\nsource_count: 3\n"

    def _analyze(self, cross_result):
        with patch.object(ai, "_ask_ai", new=AsyncMock(side_effect=RuntimeError())), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=cross_result)):
            return _run(eis.analyze_event(
                "Will it pass?", baseline_probability=50, news_context=self.NEWS))

    @staticmethod
    def _cross(agreement):
        return {
            "model": "second", "probability": 70.0, "primary_probability": 50.0,
            "divergence": 20.0, "agreement": agreement,
        }

    def test_attached_when_present(self):
        canned = self._cross("medium")
        self.assertEqual(self._analyze(canned)["cross_validation"], canned)

    def test_absent_when_disabled(self):
        self.assertNotIn("cross_validation", self._analyze(None))

    def test_low_agreement_reduces_credibility(self):
        baseline = self._analyze(None)["credibility"]["score"]
        low = self._analyze(self._cross("low"))["credibility"]["score"]
        self.assertEqual(low, max(0, baseline - 15))

    def test_high_agreement_raises_credibility(self):
        baseline = self._analyze(None)["credibility"]["score"]
        high = self._analyze(self._cross("high"))["credibility"]["score"]
        self.assertEqual(high, min(100, baseline + 5))

    def test_medium_agreement_leaves_credibility_unchanged(self):
        baseline = self._analyze(None)["credibility"]["score"]
        medium = self._analyze(self._cross("medium"))["credibility"]["score"]
        self.assertEqual(medium, baseline)


if __name__ == "__main__":
    unittest.main()
