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


def _run(coro):
    return asyncio.run(coro)


class CrossValidateTests(unittest.TestCase):
    def test_disabled_when_no_model_configured(self):
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", ""):
            self.assertIsNone(_run(cv.cross_validate("Q?", "ctx", 60.0)))

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

    def test_invalid_probability_falls_back_to_primary(self):
        with patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "m"), \
                patch.object(cv, "_ask_second_model",
                             new=AsyncMock(return_value={"ai_probability": None})):
            result = _run(cv.cross_validate("Q?", "ctx", 55.0))
        self.assertEqual(result["probability"], 55.0)
        self.assertEqual(result["divergence"], 0.0)
        self.assertEqual(result["agreement"], "high")

    def test_non_finite_probability_falls_back_to_primary(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), \
                    patch.object(cv.settings, "CROSS_VALIDATION_MODEL", "m"), \
                    patch.object(cv, "_ask_second_model",
                                 new=AsyncMock(return_value={"ai_probability": value})):
                result = _run(cv.cross_validate("Q?", "ctx", 55.0))
            self.assertEqual(result["probability"], 55.0)
            self.assertEqual(result["divergence"], 0.0)
            self.assertEqual(result["agreement"], "high")

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
