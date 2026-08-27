"""Unit tests for llm_telemetry_service (Phase 5).

Verifies the pure-function contract: reads analysis/sentiment signals,
produces a structured telemetry block, never raises, no writeback.
"""
from __future__ import annotations

import unittest

from app.services.llm_telemetry_service import (
    _DEFAULT_PRICE_PER_1K,
    _MODEL_PRICING_PER_1K,
    _estimate_cost,
    _lookup_price,
    build_llm_telemetry,
)


def _analysis(*, quality="llm", usage=None, llm_model=None):
    """Build a minimal analysis dict for testing."""
    d = {"analysis_quality": quality}
    if usage is not None:
        d["llm_usage"] = usage
    if llm_model is not None:
        d["llm_model"] = llm_model
    return d


def _usage(prompt=1000, completion=300, total=1300):
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": total}


class BuildLlmTelemetryTests(unittest.TestCase):
    """Tests for build_llm_telemetry (the public entry point)."""

    def test_returns_none_when_disabled(self):
        result = build_llm_telemetry(
            analysis=_analysis(),
            sentiment_profile=None,
            news_context="some context",
            model="gpt-4o-mini",
            enabled=False,
        )
        self.assertIsNone(result)

    def test_returns_block_when_enabled(self):
        result = build_llm_telemetry(
            analysis=_analysis(quality="llm", usage=_usage()),
            sentiment_profile=None,
            news_context="ctx",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertIsNotNone(result)
        self.assertNotIn("error", result)
        self.assertEqual(result["analysis_quality"], "llm")
        self.assertFalse(result["degraded_mode"])
        self.assertEqual(result["total_tokens"], 1300)

    def test_never_raises_on_malformed_analysis(self):
        """Malformed analysis dict should not raise — best-effort block."""
        result = build_llm_telemetry(
            analysis="not a dict",  # wrong type
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["analysis_quality"], "unknown")

    def test_never_raises_on_none_analysis(self):
        result = build_llm_telemetry(
            analysis=None,
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["analysis_quality"], "unknown")
        self.assertFalse(result["degraded_mode"])


class DegradedModeTests(unittest.TestCase):
    """Tests for degraded_mode / degraded_reason / analysis_quality."""

    def test_degraded_when_fallback(self):
        result = build_llm_telemetry(
            analysis=_analysis(quality="deterministic_fallback"),
            sentiment_profile=None,
            news_context="ctx",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertTrue(result["degraded_mode"])
        self.assertEqual(result["degraded_reason"], "llm_call_failed")
        self.assertEqual(result["analysis_quality"], "deterministic_fallback")
        # No real tokens when degraded
        self.assertIsNone(result["prompt_tokens"])
        self.assertIsNone(result["completion_tokens"])
        self.assertIsNone(result["total_tokens"])
        # llm_call_count is 0 (the main _ask_ai did not run)
        self.assertEqual(result["llm_call_count"], 0)

    def test_not_degraded_when_llm(self):
        result = build_llm_telemetry(
            analysis=_analysis(quality="llm", usage=_usage()),
            sentiment_profile=None,
            news_context="ctx",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertFalse(result["degraded_mode"])
        self.assertIsNone(result["degraded_reason"])
        self.assertEqual(result["analysis_quality"], "llm")
        self.assertEqual(result["llm_call_count"], 1)

    def test_unknown_quality_treated_as_not_degraded(self):
        result = build_llm_telemetry(
            analysis={"analysis_quality": "weird_value"},
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertFalse(result["degraded_mode"])
        self.assertEqual(result["analysis_quality"], "unknown")

    def test_degraded_reason_no_forbidden_words(self):
        """degraded_reason must not contain banned trading vocabulary."""
        result = build_llm_telemetry(
            analysis=_analysis(quality="deterministic_fallback"),
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        reason = result["degraded_reason"]
        self.assertIsNotNone(reason)
        forbidden = ("long", "short", "buy", "sell", "position", "kelly", "order")
        for word in forbidden:
            self.assertNotIn(word, reason.lower())


class SentimentDegradedTests(unittest.TestCase):
    """Tests for sentiment_degraded flag."""

    def test_sentiment_degraded_true_when_fallback(self):
        result = build_llm_telemetry(
            analysis=_analysis(),
            sentiment_profile={"fallback": True, "summary": "unavailable"},
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertTrue(result["sentiment_degraded"])

    def test_sentiment_degraded_false_when_no_fallback(self):
        result = build_llm_telemetry(
            analysis=_analysis(),
            sentiment_profile={"fallback": False, "summary": "real"},
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertFalse(result["sentiment_degraded"])

    def test_sentiment_degraded_false_when_missing(self):
        result = build_llm_telemetry(
            analysis=_analysis(),
            sentiment_profile={"summary": "real but no fallback key"},
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertFalse(result["sentiment_degraded"])

    def test_sentiment_degraded_false_when_profile_none(self):
        result = build_llm_telemetry(
            analysis=_analysis(),
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertFalse(result["sentiment_degraded"])


class TokenUsageTests(unittest.TestCase):
    """Tests for prompt/completion/total_tokens extraction."""

    def test_tokens_populated_from_usage(self):
        usage = _usage(prompt=1200, completion=350, total=1550)
        result = build_llm_telemetry(
            analysis=_analysis(quality="llm", usage=usage),
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertEqual(result["prompt_tokens"], 1200)
        self.assertEqual(result["completion_tokens"], 350)
        self.assertEqual(result["total_tokens"], 1550)

    def test_tokens_none_when_no_usage(self):
        result = build_llm_telemetry(
            analysis=_analysis(quality="llm"),  # no llm_usage key
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertIsNone(result["prompt_tokens"])
        self.assertIsNone(result["completion_tokens"])
        self.assertIsNone(result["total_tokens"])

    def test_tokens_none_when_degraded(self):
        """When degraded_mode=True, llm_usage should be None (fallback path
        doesn't attach it). Verify the telemetry handles this."""
        result = build_llm_telemetry(
            analysis=_analysis(quality="deterministic_fallback"),  # no usage
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertIsNone(result["prompt_tokens"])
        self.assertIsNone(result["total_tokens"])

    def test_tokens_none_when_usage_malformed(self):
        """Malformed usage (non-int values) -> treated as None."""
        result = build_llm_telemetry(
            analysis={"analysis_quality": "llm",
                      "llm_usage": {"prompt_tokens": "abc"}},
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertIsNone(result["prompt_tokens"])
        self.assertIsNone(result["total_tokens"])

    def test_tokens_none_when_usage_negative(self):
        result = build_llm_telemetry(
            analysis={"analysis_quality": "llm",
                      "llm_usage": {"prompt_tokens": -100, "total_tokens": -100}},
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertIsNone(result["prompt_tokens"])
        self.assertIsNone(result["total_tokens"])


class EstimatedCostTests(unittest.TestCase):
    """Tests for estimated_token_cost computation."""

    def test_cost_from_real_tokens(self):
        """When total_tokens is available, cost = total * price / 1000."""
        usage = _usage(total=1000)
        result = build_llm_telemetry(
            analysis=_analysis(quality="llm", usage=usage),
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",  # price = 0.00015/1K
            enabled=True,
        )
        # 1000 tokens * 0.00015 / 1000 = 0.00015
        self.assertAlmostEqual(result["estimated_token_cost"], 0.00015, places=6)

    def test_cost_estimated_from_context_when_degraded(self):
        """When total_tokens is None (degraded), estimate from news_context
        length using chars/4 heuristic."""
        # 400 chars / 4 = 100 tokens. gpt-4o-mini: 0.00015/1K
        # 100 * 0.00015 / 1000 = 0.000015
        result = build_llm_telemetry(
            analysis=_analysis(quality="deterministic_fallback"),
            sentiment_profile=None,
            news_context="x" * 400,
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertAlmostEqual(result["estimated_token_cost"], 0.000015, places=6)

    def test_cost_zero_when_no_context_and_no_tokens(self):
        result = build_llm_telemetry(
            analysis=_analysis(quality="deterministic_fallback"),
            sentiment_profile=None,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertEqual(result["estimated_token_cost"], 0.0)

    def test_cost_for_gpt4o(self):
        usage = _usage(total=1000)
        result = build_llm_telemetry(
            analysis=_analysis(quality="llm", usage=usage),
            sentiment_profile=None,
            news_context="",
            model="gpt-4o",  # price = 0.005/1K
            enabled=True,
        )
        # 1000 * 0.005 / 1000 = 0.005
        self.assertAlmostEqual(result["estimated_token_cost"], 0.005, places=6)


class ModelFieldTests(unittest.TestCase):
    """Tests for the model field."""

    def test_model_mirrors_input(self):
        result = build_llm_telemetry(
            analysis=_analysis(),
            sentiment_profile=None,
            news_context="",
            model="deepseek-chat",
            enabled=True,
        )
        self.assertEqual(result["model"], "deepseek-chat")

    def test_empty_model_string(self):
        result = build_llm_telemetry(
            analysis=_analysis(),
            sentiment_profile=None,
            news_context="",
            model="",
            enabled=True,
        )
        self.assertEqual(result["model"], "")


class ServedModelPricingTests(unittest.TestCase):
    """The block must price and label by the model that actually ran.

    The gateway walks a route list and falls back between providers, so the
    ``model`` argument -- ``settings.OPENAI_MODEL``, which ``.env.example``
    calls the legacy last-resort name and tells operators to comment out --
    is not the model that served the call. Pricing by it understated a
    gpt-4-served call by 214x while the cap counter, which prices by the real
    model, charged the full amount.
    """

    def test_cost_is_priced_by_the_served_model_not_the_configured_one(self):
        result = build_llm_telemetry(
            analysis=_analysis(usage=_usage(total=100_000), llm_model="gpt-4"),
            sentiment_profile=None,
            news_context="",
            model="deepseek-chat",
            enabled=True,
        )
        # 100_000 * 0.03 / 1000 = 3.0, not 100_000 * 0.00014 / 1000 = 0.014.
        self.assertAlmostEqual(result["estimated_token_cost"], 3.0, places=6)
        self.assertEqual(result["model"], "gpt-4")

    def test_the_configured_model_is_still_used_when_none_was_recorded(self):
        """Non-vacuous baseline: without a served model the old behaviour
        stands, so "price by the served model" cannot be satisfied by ignoring
        the argument."""
        result = build_llm_telemetry(
            analysis=_analysis(usage=_usage(total=100_000)),
            sentiment_profile=None,
            news_context="",
            model="deepseek-chat",
            enabled=True,
        )
        self.assertAlmostEqual(result["estimated_token_cost"], 0.014, places=6)
        self.assertEqual(result["model"], "deepseek-chat")

    def test_a_malformed_served_model_falls_back_to_the_configured_one(self):
        for bad in ("", "   ", 123, None, ["gpt-4"]):
            with self.subTest(llm_model=bad):
                analysis = _analysis(usage=_usage(total=1000))
                analysis["llm_model"] = bad
                result = build_llm_telemetry(
                    analysis=analysis,
                    sentiment_profile=None,
                    news_context="",
                    model="gpt-4o",
                    enabled=True,
                )
                self.assertEqual(result["model"], "gpt-4o")
                self.assertAlmostEqual(
                    result["estimated_token_cost"], 0.005, places=6
                )

    def test_prometheus_series_is_labelled_with_the_served_model(self):
        """The counter is documented as cost "across all calls", so an operator
        reading it by model needs the model that ran. It used to carry one
        label -- the configured model -- for every call regardless."""
        from app.utils.metrics import LLM_TOKEN_COST, LLM_TOKEN_USAGE

        def _cost_for(model: str) -> float:
            return sum(
                sample.value
                for metric in LLM_TOKEN_COST.collect()
                for sample in metric.samples
                if sample.labels.get("model") == model
                and sample.name.endswith("_total")
            )

        def _tokens_for(model: str, kind: str) -> float:
            return sum(
                sample.value
                for metric in LLM_TOKEN_USAGE.collect()
                for sample in metric.samples
                if sample.labels.get("model") == model
                and sample.labels.get("kind") == kind
                and sample.name.endswith("_total")
            )

        served, configured = "gpt-4-turbo", "gpt-3.5-turbo"
        before_served = _cost_for(served)
        before_configured = _cost_for(configured)
        before_input = _tokens_for(served, "input")
        build_llm_telemetry(
            analysis=_analysis(
                usage=_usage(prompt=800, completion=200, total=1000),
                llm_model=served,
            ),
            sentiment_profile=None,
            news_context="",
            model=configured,
            enabled=True,
        )
        # 1000 * 0.01 / 1000 = 0.01 charged under the served model...
        self.assertAlmostEqual(_cost_for(served) - before_served, 0.01, places=6)
        # ...and nothing at all under the configured one.
        self.assertAlmostEqual(_cost_for(configured) - before_configured, 0.0, places=9)
        self.assertAlmostEqual(_tokens_for(served, "input") - before_input, 800, places=6)


class LookupPriceTests(unittest.TestCase):
    """Tests for _lookup_price (pricing table lookup)."""

    def test_exact_match(self):
        self.assertEqual(_lookup_price("gpt-4o-mini"), _MODEL_PRICING_PER_1K["gpt-4o-mini"])

    def test_case_insensitive_match(self):
        self.assertEqual(_lookup_price("GPT-4O-MINI"), _MODEL_PRICING_PER_1K["gpt-4o-mini"])

    def test_prefix_match_for_versioned_model(self):
        """Versioned model names like 'gpt-4o-mini-2024-07-18' should match
        the base name via prefix lookup."""
        self.assertEqual(
            _lookup_price("gpt-4o-mini-2024-07-18"),
            _MODEL_PRICING_PER_1K["gpt-4o-mini"],
        )

    def test_unknown_model_uses_default(self):
        self.assertEqual(_lookup_price("some-unknown-model"), _DEFAULT_PRICE_PER_1K)

    def test_empty_string_uses_default(self):
        self.assertEqual(_lookup_price(""), _DEFAULT_PRICE_PER_1K)

    def test_none_uses_default(self):
        self.assertEqual(_lookup_price(None), _DEFAULT_PRICE_PER_1K)


class EstimateCostTests(unittest.TestCase):
    """Tests for _estimate_cost helper."""

    def test_real_tokens_exact_cost(self):
        cost = _estimate_cost(2000, "", 0.005)
        # 2000 * 0.005 / 1000 = 0.01
        self.assertAlmostEqual(cost, 0.01, places=6)

    def test_zero_tokens_uses_context(self):
        """total_tokens=0 is treated as 'no usage' -> estimate from context."""
        cost = _estimate_cost(0, "x" * 400, 0.00015)
        # 400/4 = 100 tokens, 100 * 0.00015 / 1000 = 0.000015
        self.assertAlmostEqual(cost, 0.000015, places=6)

    def test_none_tokens_uses_context(self):
        cost = _estimate_cost(None, "x" * 800, 0.005)
        # 800/4 = 200 tokens, 200 * 0.005 / 1000 = 0.001
        self.assertAlmostEqual(cost, 0.001, places=6)

    def test_none_tokens_empty_context_zero_cost(self):
        cost = _estimate_cost(None, "", 0.005)
        self.assertEqual(cost, 0.0)


class NoWritebackTests(unittest.TestCase):
    """Verify build_llm_telemetry does not mutate its inputs."""

    def test_analysis_not_mutated(self):
        analysis = _analysis(quality="llm", usage=_usage())
        original = dict(analysis)
        build_llm_telemetry(
            analysis=analysis,
            sentiment_profile=None,
            news_context="ctx",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertEqual(analysis, original)

    def test_sentiment_profile_not_mutated(self):
        sp = {"fallback": True, "summary": "test"}
        original = dict(sp)
        build_llm_telemetry(
            analysis=_analysis(),
            sentiment_profile=sp,
            news_context="",
            model="gpt-4o-mini",
            enabled=True,
        )
        self.assertEqual(sp, original)


if __name__ == "__main__":
    unittest.main()
