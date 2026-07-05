"""
Characterization tests for ai_analysis_service.

These lock the deterministic behavior of the analysis engine BEFORE the Phase 3
item 2 split (probability engine / report generation / legacy adapter) so the
relocation can be proven behavior-preserving. Every symbol is imported from
`app.services.ai_analysis_service`, which must keep re-exporting them after the
split, so this same file passes unchanged before and after.

The expected values were captured from the pre-split implementation. They are
intentionally exact: any drift is a behavior change, not a refactor.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import app.services.ai_analysis_service as ai
from app.services.ai_analysis_service import (
    build_deterministic_fallback_analysis,
    apply_confidence_caps,
    apply_longshot_guardrail,
    build_risk_flags,
    calculate_confidence_score,
    calculate_evidence_quality,
    calculate_narrative_risk_score,
    calculate_position_size,
    calculate_priced_in_risk_score,
    calculate_risk_level,
    calculate_signal,
    calculate_signal_direction,
    calculate_signal_strength,
    clamp_probability,
    constrain_probability,
    extract_evidence_profile,
    extract_semantics_profile,
    passes_analysis_quality_gate,
    score_news_quality,
    _normalize_ai_analysis,
)

NEWS_CONTEXT = (
    "EVIDENCE PROFILE\n"
    "direction: support\n"
    "strength: 0.6\n"
    "conflict: 0.2\n"
    "freshness: 0.8\n"
    "resolution_relevance: 0.5\n"
    "source_count: 4\n"
    "independent_source_count: 3\n"
    "official_source_count: 1\n"
    "counterevidence_considered: true\n"
    "MARKET SEMANTICS\n"
    "condition_type: threshold\n"
    "ambiguity_score: 30\n"
    "news item: Reuters reports official filing confirms the plan. quality: 0.7 relevance: 0.8\n"
    "news item: Associated Press covers the court decision. quality: 0.6 relevance: 0.75\n"
    "google news: Bloomberg analysis of the policy. quality: 0.65 relevance: 0.7\n"
    "rss title: Policy update from the agency today. quality: 0.6 relevance: 0.72\n"
    "title: Additional confirmation reported widely. quality: 0.58 relevance: 0.69\n"
)

EVIDENCE = {
    "evidence_direction": "support",
    "evidence_strength": 0.6,
    "conflict_score": 0.2,
    "freshness_score": 0.8,
    "resolution_relevance_score": 0.5,
    "source_count": 4,
    "independent_source_count": 3,
    "official_source_count": 1,
    "counterevidence_considered": True,
}
SEMANTICS = {"condition_type": "threshold", "ambiguity_score": 30}
REASONING = (
    "This is a sufficiently long reasoning string with many words to exceed "
    "the twelve word floor."
)


class ParsingTests(unittest.TestCase):
    def test_extract_evidence_profile(self):
        self.assertEqual(extract_evidence_profile(NEWS_CONTEXT), EVIDENCE)

    def test_extract_semantics_profile(self):
        self.assertEqual(extract_semantics_profile(NEWS_CONTEXT), SEMANTICS)

    def test_score_news_quality(self):
        self.assertEqual(score_news_quality(NEWS_CONTEXT), 0.867)


class ProbabilityMathTests(unittest.TestCase):
    def test_calculate_priced_in_risk_score(self):
        self.assertEqual(
            calculate_priced_in_risk_score(
                market_probability=65,
                evidence_profile=EVIDENCE,
                volume=200000,
                liquidity=60000,
            ),
            54,
        )

    def test_calculate_confidence_score(self):
        self.assertEqual(
            calculate_confidence_score(
                news_context=NEWS_CONTEXT,
                news_quality_score=0.867,
                narrative_type="factual",
                reasoning=REASONING,
                reasoning_consistency=0.7,
                evidence_profile=EVIDENCE,
                priced_in_risk_score=30,
                semantics_profile=SEMANTICS,
            ),
            0.701,
        )

    def test_calculate_evidence_quality_weak(self):
        quality = calculate_evidence_quality(
            evidence_profile={
                "evidence_direction": "support",
                "evidence_strength": 0.12,
                "conflict_score": 0.65,
                "freshness_score": 0.25,
                "resolution_relevance_score": 0.15,
                "source_count": 1,
            },
            news_quality_score=0.25,
            semantics_profile={"condition_type": "unknown", "ambiguity_score": 75},
            priced_in_risk_score=80,
        )
        self.assertEqual(quality["bucket"], "weak")
        self.assertLessEqual(quality["factor"], 0.35)
        self.assertIn("thin_or_indirect_evidence", quality["reasons"])
        self.assertIn("high_conflict", quality["reasons"])

    def test_calculate_evidence_quality_strong(self):
        quality = calculate_evidence_quality(
            evidence_profile={
                "evidence_direction": "support",
                "evidence_strength": 0.85,
                "conflict_score": 0.05,
                "freshness_score": 0.92,
                "resolution_relevance_score": 0.88,
                "source_count": 6,
                "independent_source_count": 5,
                "official_source_count": 1,
                "counterevidence_considered": True,
            },
            news_quality_score=0.86,
            semantics_profile={"condition_type": "threshold", "ambiguity_score": 18},
            priced_in_risk_score=20,
        )
        self.assertEqual(quality["bucket"], "strong")
        self.assertGreaterEqual(quality["factor"], 0.75)
        self.assertIn("direct_relevant_evidence", quality["reasons"])
        self.assertIn("multi_source_support", quality["reasons"])
        self.assertIn("official_source_support", quality["reasons"])
        self.assertIn("counterevidence_considered", quality["reasons"])

    def test_calculate_evidence_quality_rewards_independent_sources(self):
        shared = {
            "evidence_direction": "support",
            "evidence_strength": 0.72,
            "conflict_score": 0.10,
            "freshness_score": 0.80,
            "resolution_relevance_score": 0.74,
            "source_count": 6,
            "official_source_count": 0,
            "counterevidence_considered": True,
        }
        same_wire_story = calculate_evidence_quality(
            evidence_profile={**shared, "independent_source_count": 1},
            news_quality_score=0.72,
            semantics_profile={"condition_type": "threshold", "ambiguity_score": 20},
            priced_in_risk_score=20,
        )
        independent_sources = calculate_evidence_quality(
            evidence_profile={**shared, "independent_source_count": 5},
            news_quality_score=0.72,
            semantics_profile={"condition_type": "threshold", "ambiguity_score": 20},
            priced_in_risk_score=20,
        )

        self.assertGreater(independent_sources["factor"], same_wire_story["factor"])
        self.assertIn("low_source_diversity", same_wire_story["reasons"])
        self.assertIn("independent_source_support", independent_sources["reasons"])

    def test_calculate_evidence_quality_requires_counterevidence_for_strong(self):
        quality = calculate_evidence_quality(
            evidence_profile={
                "evidence_direction": "support",
                "evidence_strength": 0.90,
                "conflict_score": 0.02,
                "freshness_score": 0.92,
                "resolution_relevance_score": 0.90,
                "source_count": 7,
                "independent_source_count": 6,
                "official_source_count": 1,
                "counterevidence_considered": False,
            },
            news_quality_score=0.88,
            semantics_profile={"condition_type": "threshold", "ambiguity_score": 15},
            priced_in_risk_score=10,
        )

        self.assertEqual(quality["bucket"], "solid")
        self.assertIn("counterevidence_not_considered", quality["reasons"])

    def test_apply_confidence_caps_limits_one_sided_evidence(self):
        result = apply_confidence_caps(
            confidence=0.84,
            market_probability=42.0,
            base_rate_category="known_policy",
            evidence_quality={
                "factor": 0.74,
                "bucket": "solid",
                "reasons": ["counterevidence_not_considered"],
            },
        )

        self.assertEqual(result["confidence"], 0.70)
        self.assertIn("counterevidence_not_considered_cap", result["reasons"])

    def test_apply_longshot_guardrail_caps_weak_low_probability_lift(self):
        result = apply_longshot_guardrail(
            market_probability=3.8,
            ai_probability=30.7,
            evidence_quality={"factor": 0.24, "bucket": "weak", "reasons": []},
            has_strong_evidence=False,
            base_rate_category="unknown",
        )
        self.assertTrue(result["triggered"])
        self.assertEqual(result["reason"], "low_probability_weak_evidence_cap")
        self.assertLessEqual(result["probability"], 15.8)

    def test_apply_longshot_guardrail_allows_strong_evidence_more_room(self):
        result = apply_longshot_guardrail(
            market_probability=4.0,
            ai_probability=31.0,
            evidence_quality={"factor": 0.82, "bucket": "strong", "reasons": []},
            has_strong_evidence=True,
            base_rate_category="unknown",
        )
        self.assertFalse(result["triggered"])
        self.assertEqual(result["probability"], 31.0)

    def test_constrain_probability_returns_diagnostics(self):
        result = constrain_probability(
            market_probability=3.8,
            ai_probability=30.7,
            confidence=0.58,
            narrative_type="factual",
            has_strong_evidence=False,
            evidence_profile={
                "evidence_direction": "support",
                "evidence_strength": 0.12,
                "conflict_score": 0.65,
                "freshness_score": 0.25,
                "resolution_relevance_score": 0.15,
                "source_count": 1,
            },
            priced_in_risk_score=80,
            semantics_profile={"condition_type": "unknown", "ambiguity_score": 75},
            news_quality_score=0.25,
            base_rate_category="unknown",
        )
        self.assertEqual(result["evidence_quality_bucket"], "weak")
        self.assertTrue(result["guardrail_triggered"])
        self.assertEqual(result["guardrail_reason"], "low_probability_weak_evidence_cap")
        self.assertLessEqual(result["probability"], 15.8)

    def test_apply_confidence_caps_weak_unknown_longshot(self):
        result = apply_confidence_caps(
            confidence=0.82,
            market_probability=4.0,
            base_rate_category="unknown",
            evidence_quality={"factor": 0.24, "bucket": "weak", "reasons": []},
        )
        self.assertEqual(result["confidence"], 0.55)
        self.assertIn("weak_evidence_cap", result["reasons"])
        self.assertIn("unknown_category_cap", result["reasons"])
        self.assertIn("low_probability_evidence_cap", result["reasons"])

    def test_apply_confidence_caps_solid_known_category_keeps_confidence(self):
        result = apply_confidence_caps(
            confidence=0.68,
            market_probability=42.0,
            base_rate_category="crypto_price",
            evidence_quality={"factor": 0.68, "bucket": "solid", "reasons": []},
        )
        self.assertEqual(result["confidence"], 0.68)
        self.assertEqual(result["reasons"], [])

    def test_clamp_probability(self):
        self.assertEqual(
            clamp_probability(
                market_probability=50,
                ai_probability=72,
                confidence=0.7,
                narrative_type="factual",
                has_strong_evidence=True,
                evidence_profile=EVIDENCE,
                priced_in_risk_score=30,
                semantics_profile=SEMANTICS,
            ),
            70.86,
        )

    def test_build_deterministic_fallback_analysis(self):
        self.assertEqual(
            build_deterministic_fallback_analysis(
                market_probability=50,
                evidence_profile=EVIDENCE,
                news_quality_score=0.867,
                priced_in_risk_score=30,
                semantics_profile=SEMANTICS,
            ),
            {
                "ai_probability": 51.79,
                "reasoning_steps": [],
                "title_zh": "",
                "narrative_type": "evidence_fallback",
                "narrative_summary": "基于结构化新闻证据的确定性回退分析。",
                "reasoning": (
                    "LLM 不可用或返回无效；概率根据证据方向、强度、结算相关性、新鲜度、"
                    "冲突度、新闻质量、已定价风险与结算歧义度综合估算得出。"
                ),
                "has_strong_evidence": False,
                "reasoning_consistency": 0.3,
                "resolution_criteria": "",
                "time_horizon": "",
                "entities": [],
            },
        )

    def test_normalize_ai_analysis_trims_and_clamps(self):
        self.assertEqual(
            _normalize_ai_analysis(
                {
                    "ai_probability": 80,
                    "narrative_type": "  Factual  ",
                    "narrative_summary": "  A summary.  ",
                    "reasoning": "  Some reasoning.  ",
                    "has_strong_evidence": True,
                    "reasoning_consistency": 0.9,
                },
                50,
            ),
            {
                "ai_probability": 80.0,
                "reasoning_steps": [],
                "title_zh": "",
                "narrative_type": "Factual",
                "narrative_summary": "A summary.",
                "reasoning": "Some reasoning.",
                "has_strong_evidence": True,
                "reasoning_consistency": 0.9,
                "resolution_criteria": "",
                "time_horizon": "",
                "entities": [],
            },
        )

    def test_normalize_ai_analysis_extracts_semantics_fields(self):
        result = _normalize_ai_analysis(
            {
                "ai_probability": 70,
                "resolution_criteria": "  BTC closes at or above $100k  ",
                "time_horizon": "  by end of 2026  ",
                "entities": ["Bitcoin", "  bitcoin  ", "SAT", "", "  ", "ETF"],
            },
            50,
        )
        self.assertEqual(result["resolution_criteria"], "BTC closes at or above $100k")
        self.assertEqual(result["time_horizon"], "by end of 2026")
        # Deduped case-insensitively (Bitcoin/bitcoin collapse), empties
        # dropped, order preserved on first occurrence.
        self.assertEqual(result["entities"], ["Bitcoin", "SAT", "ETF"])

    def test_normalize_ai_analysis_caps_entities_at_ten(self):
        result = _normalize_ai_analysis(
            {"ai_probability": 60, "entities": [f"entity{i}" for i in range(15)]},
            50,
        )
        self.assertEqual(len(result["entities"]), 10)
        self.assertEqual(result["entities"][0], "entity0")

    def test_normalize_ai_analysis_rejects_non_list_entities(self):
        result = _normalize_ai_analysis(
            {"ai_probability": 60, "entities": "Bitcoin, not a list"},
            50,
        )
        self.assertEqual(result["entities"], [])

    def test_normalize_ai_analysis_rejects_non_finite_numbers(self):
        self.assertEqual(
            _normalize_ai_analysis(
                {
                    "ai_probability": "NaN",
                    "reasoning_consistency": "Infinity",
                },
                50,
            )["ai_probability"],
            0,
        )
        self.assertEqual(
            _normalize_ai_analysis(
                {
                    "ai_probability": "NaN",
                    "reasoning_consistency": "Infinity",
                },
                50,
            )["reasoning_consistency"],
            0,
        )


class ReportGenerationTests(unittest.TestCase):
    def test_calculate_narrative_risk_score(self):
        self.assertEqual(
            calculate_narrative_risk_score(news_context=NEWS_CONTEXT, narrative_type="meme"),
            45,
        )

    def test_passes_analysis_quality_gate(self):
        self.assertTrue(
            passes_analysis_quality_gate(
                confidence=0.6,
                evidence_profile=EVIDENCE,
                priced_in_risk_score=30,
                news_quality_score=0.6,
            )
        )

    def test_calculate_signal(self):
        self.assertEqual(
            calculate_signal(
                divergence=15,
                confidence=0.6,
                evidence_profile=EVIDENCE,
                priced_in_risk_score=30,
                news_quality_score=0.6,
            ),
            "LONG",
        )

    def test_calculate_signal_strength(self):
        self.assertEqual(
            calculate_signal_strength(
                divergence=15,
                confidence=0.6,
                news_quality_score=0.6,
                narrative_risk=20,
                evidence_profile=EVIDENCE,
                priced_in_risk_score=30,
            ),
            "LOW",
        )

    def test_calculate_signal_direction(self):
        self.assertEqual(calculate_signal_direction("LONG"), "LONG")

    def test_calculate_position_size(self):
        self.assertEqual(
            calculate_position_size(divergence=15, confidence=0.6, narrative_risk=20),
            0.05,
        )

    def test_calculate_risk_level(self):
        self.assertEqual(
            calculate_risk_level(narrative_risk_score=20, news_quality_score=0.6),
            "LOW",
        )

    def test_build_risk_flags(self):
        self.assertEqual(
            build_risk_flags(
                news_context=NEWS_CONTEXT, narrative_type="meme", news_quality_score=0.6
            ),
            ["meme"],
        )


class AnalyzeMarketContractTests(unittest.TestCase):
    """Lock the full analyze_market output via the deterministic fallback path.

    _ask_ai is forced to raise so analyze_market takes its evidence-only fallback
    branch, making the whole record deterministic. This locks both orchestration
    and the complete output shape (the legacy contract callers depend on).
    """

    def test_analyze_market_caps_confidence_for_weak_unknown_longshot(self):
        weak_context = (
            "EVIDENCE PROFILE\n"
            "direction: support\n"
            "strength: 0.12\n"
            "conflict: 0.65\n"
            "freshness: 0.25\n"
            "resolution_relevance: 0.15\n"
            "source_count: 1\n"
            "MARKET SEMANTICS\n"
            "condition_type: unknown\n"
            "ambiguity_score: 75\n"
            "news item: unconfirmed rumor. quality: 0.25 relevance: 0.20\n"
        )

        async def run():
            with (
                patch.object(ai, "_ask_ai", new=AsyncMock(return_value={
                    "ai_probability": 34,
                    "narrative_type": "factual",
                    "narrative_summary": "Weak rumor points upward.",
                    "reasoning": REASONING,
                    "has_strong_evidence": False,
                    "reasoning_consistency": 0.9,
                })),
                patch.object(ai, "translate_title", new=AsyncMock(return_value="")),
            ):
                return await ai.analyze_market(
                    market_question="Will an obscure unclassified event happen this week?",
                    market_probability=4,
                    news_context=weak_context,
                    volume=1000,
                    liquidity=500,
                )

        result = asyncio.run(run())
        self.assertLessEqual(result["confidence_score"], 0.55)
        self.assertIn("weak_evidence_cap", result["confidence_cap_reasons"])
        self.assertIn("low_probability_evidence_cap", result["confidence_cap_reasons"])

    def test_analyze_market_unknown_category_anchors_to_market_not_static_fifty(self):
        weak_context = (
            "EVIDENCE PROFILE\n"
            "direction: support\n"
            "strength: 0.12\n"
            "conflict: 0.65\n"
            "freshness: 0.25\n"
            "resolution_relevance: 0.15\n"
            "source_count: 1\n"
            "MARKET SEMANTICS\n"
            "condition_type: unknown\n"
            "ambiguity_score: 75\n"
            "news item: unconfirmed rumor. quality: 0.25 relevance: 0.20\n"
        )

        async def run():
            with (
                patch.object(ai, "_ask_ai", new=AsyncMock(return_value={
                    "ai_probability": 34,
                    "narrative_type": "factual",
                    "narrative_summary": "Weak rumor points upward.",
                    "reasoning": REASONING,
                    "has_strong_evidence": False,
                    "reasoning_consistency": 0.9,
                })),
                patch.object(ai, "translate_title", new=AsyncMock(return_value="")),
            ):
                return await ai.analyze_market(
                    market_question="Will an obscure unclassified event happen this week?",
                    market_probability=4,
                    news_context=weak_context,
                    volume=1000,
                    liquidity=500,
                )

        result = asyncio.run(run())
        self.assertEqual(result["base_rate_category"], "unknown")
        self.assertEqual(result["base_rate_prior"], 50)
        self.assertEqual(result["base_rate_effective_prior"], 4)
        self.assertLessEqual(result["evidence_constrained_probability"], 14.0)
        self.assertLessEqual(result["ai_probability"], 14.0)

    def test_analyze_market_fallback_contract(self):
        async def run():
            with (
                patch.object(ai, "_ask_ai", new=AsyncMock(side_effect=RuntimeError("no llm"))),
                patch.object(ai, "translate_title", new=AsyncMock(return_value="")),
            ):
                return await ai.analyze_market(
                    market_question="Will the agency approve the policy before the deadline?",
                    market_probability=50,
                    news_context=NEWS_CONTEXT,
                    volume=200000,
                    liquidity=60000,
                )

        result = asyncio.run(run())
        self.assertEqual(
            result,
            {
                "market_question": "Will the agency approve the policy before the deadline?",
                "market_probability": 50.0,
                "ai_probability": 50.74,
                "true_probability": 50.74,
                "final_probability": 50.74,
                "divergence": 0.74,
                "signal_strength": "LOW",
                "signal_direction": "NEUTRAL",
                "overreaction_score": 0.74,
                "confidence_score": 0.584,
                "confidence_cap_reasons": [],
                "narrative_type": "evidence_fallback",
                "title_zh": "",
                "narrative_summary": "基于结构化新闻证据的确定性回退分析。",
                "reasoning": (
                    "LLM 不可用或返回无效；概率根据证据方向、强度、结算相关性、新鲜度、"
                    "冲突度、新闻质量、已定价风险与结算歧义度综合估算得出。"
                ),
                "risk_flags": [],
                "signal": "WATCHLIST",
                "position_size": 0.02,
                "narrative_risk_score": 20,
                "news_quality_score": 0.867,
                "evidence_direction": "support",
                "evidence_strength": 0.6,
                "evidence_conflict_score": 0.2,
                "freshness_score": 0.8,
                "resolution_relevance_score": 0.5,
                "priced_in_risk_score": 29,
                "market_ambiguity_score": 30,
                "condition_type": "threshold",
                "base_rate_category": "unknown",
                "base_rate_prior": 50,
                "base_rate_effective_prior": 50.0,
                "base_rate_range": [20, 80],
                "evidence_constrained_probability": 51.66,
                "evidence_quality_factor": 0.683,
                "evidence_quality_bucket": "solid",
                "evidence_quality_reasons": [
                    "independent_source_support",
                    "official_source_support",
                    "counterevidence_considered",
                ],
                "probability_guardrail_triggered": False,
                "probability_guardrail_reason": "",
                "base_rate_probability": 50.74,
                "expected_edge": 0.0074,
                "risk_level": "LOW",
                "volume": 200000,
                "liquidity": 60000,
                "resolution_criteria": "",
                "time_horizon": "",
                "entities": [],
                "reasoning_steps": [],
                "analysis_quality": "deterministic_fallback",
                # Phase 5: llm_usage is None when the LLM call failed (fallback)
                "llm_usage": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
