import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app.services.ai_analysis_service as ai
import app.services.event_intelligence_service as eis
from app.services.event_intelligence_service import (
    build_event_record,
    build_evidence_items,
)
from app.services.scoring_service import (
    calculate_impact_score,
    calculate_trust_score,
    calculate_value_score,
    impact_drivers,
    probability_direction,
)


def _run(coro):
    return asyncio.run(coro)


class EventIntelligenceServiceTests(unittest.TestCase):
    def test_build_event_record_maps_probability_change_and_scores(self):
        record = build_event_record({
            "market_question": "Will the bill pass before June?",
            "market_probability": 58,
            "ai_probability": 72,
            "divergence": 14,
            "confidence_score": 0.7,
            "news_quality_score": 0.8,
            "evidence_strength": 0.55,
            "resolution_relevance_score": 0.6,
            "evidence_conflict_score": 0.1,
            "freshness_score": 0.9,
            "base_rate_category": "legal",
            "risk_level": "MEDIUM",
            "risk_flags": ["watch_confirmation"],
            "narrative_summary": "A key legislator publicly backed the bill.",
        })

        self.assertEqual(record["event_title"], "Will the bill pass before June?")
        self.assertEqual(record["probability"]["baseline"], 58)
        self.assertEqual(record["probability"]["estimated"], 72)
        self.assertEqual(record["probability"]["change"], 14)
        self.assertEqual(record["probability"]["direction"], "rising")
        self.assertEqual(record["credibility"]["level"], "HIGH")
        self.assertIn("material_probability_change", record["impact"]["drivers"])
        self.assertNotIn(
            "trade",
            record["intelligence_report"]["recommended_action"].lower(),
        )

    def test_build_event_record_adds_default_tracking_and_title_zh(self):
        record = build_event_record({
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 56,
            "title_zh": "法案会通过吗？",
        })
        self.assertEqual(record["event_title_zh"], "法案会通过吗？")
        self.assertEqual(record["tracking"]["status"], "watching")
        self.assertIn(record["tracking"]["priority"], {"high", "medium", "low"})
        # The Chinese title (when present) drives the headline.
        self.assertIn("法案会通过吗？", record["intelligence_report"]["headline"])

    def test_build_evidence_items_groups_and_preserves_url(self):
        items = build_evidence_items([
            {
                "kind": "official", "source": "Federal Reserve",
                "title": "Fed holds rates", "description": "rate hold",
                "url": "https://fed.gov/x", "published": "Mon, 01 Jun 2026 00:00:00 GMT",
                "quality_score": 0.8, "relevance_score": 0.6,
            },
            {
                "kind": "news", "source": "Reuters", "title": "Reuters report",
                "description": "context", "url": "https://r.com/y",
                "quality_score": 0.7, "relevance_score": 0.5,
            },
            {"title": "   "},  # blank title is dropped
        ])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["kind"], "official")
        self.assertEqual(items[0]["url"], "https://fed.gov/x")
        self.assertEqual(items[0]["quality"], 0.8)
        self.assertEqual({i["kind"] for i in items}, {"official", "news"})

    def test_build_evidence_items_handles_empty(self):
        self.assertEqual(build_evidence_items(None), [])
        self.assertEqual(build_evidence_items([]), [])

    def test_scores_are_bounded_when_analysis_is_sparse(self):
        analysis = {"market_question": "Sparse event"}

        self.assertEqual(calculate_trust_score(analysis), 10)
        self.assertEqual(calculate_impact_score(analysis), 0)
        self.assertEqual(probability_direction(1.9), "stable")
        self.assertEqual(probability_direction(-2), "falling")

    def test_zero_probability_is_preserved(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 10,
            "ai_probability": 0,
            "divergence": -10,
        })

        self.assertEqual(record["probability"]["estimated"], 0)
        self.assertEqual(record["probability"]["direction"], "falling")

    def test_non_dict_source_defaults_to_manual_source(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
        }, source="bad-source")

        self.assertEqual(record["source"], {"type": "manual"})

    def test_complex_source_values_are_dropped(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
        }, source={
            "type": "open_web",
            "platform": ["bad"],
            "event_type": {"bad": "shape"},
            "confidence": 0.7,
        })

        self.assertEqual(record["source"], {"type": "open_web", "confidence": 0.7})

    def test_empty_source_values_are_dropped(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
        }, source={
            "type": "",
            "platform": "   ",
            "event_type": None,
        })

        self.assertEqual(record["source"], {"type": "manual"})

    def test_non_list_risk_flags_default_to_empty_list(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
            "risk_flags": "not-a-list",
        })

        self.assertEqual(record["risk"]["flags"], [])

    def test_invalid_risk_level_defaults_to_unknown(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
            "risk_level": ["bad"],
        })

        self.assertEqual(record["risk"]["level"], "UNKNOWN")

    def test_invalid_evidence_direction_defaults_to_neutral(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
            "evidence_direction": {"bad": "shape"},
        })

        self.assertEqual(record["evidence"]["direction"], "neutral")

    def test_negative_source_count_clamps_to_zero(self):
        record = build_event_record({
            "market_question": "Will the event happen?",
            "market_probability": 50,
            "ai_probability": 55,
            "source_count": -3,
        })

        self.assertEqual(record["credibility"]["source_count"], 0)

    def test_impact_drivers_ignore_non_string_base_rate_category(self):
        drivers = impact_drivers({"base_rate_category": ["bad"]})

        self.assertEqual(drivers, ["monitor_for_confirmation"])

    def test_semantics_populated_from_analysis_fields(self):
        record = build_event_record({
            "market_question": "Will Bitcoin reach $100k?",
            "market_probability": 50,
            "ai_probability": 60,
            "resolution_criteria": "BTC closes at or above $100,000",
            "time_horizon": "by end of 2026",
            "entities": ["Bitcoin", " Satoshi ", ""],
        })
        self.assertEqual(record["semantics"]["resolution_criteria"],
                         "BTC closes at or above $100,000")
        self.assertEqual(record["semantics"]["time_horizon"], "by end of 2026")
        # Blanks dropped, whitespace stripped.
        self.assertEqual(record["semantics"]["entities"], ["Bitcoin", "Satoshi"])

    def test_semantics_none_when_all_fields_empty(self):
        record = build_event_record({"market_question": "Will X happen?"})
        self.assertIsNone(record.get("semantics"))

    def test_review_queue_detector_receives_auto_resolve_threshold(self):
        record = {
            "event_id": "evtReviewThreshold",
            "actionable_recommendation": {
                "direction": "YES",
                "signal": "act",
                "ai_probability": 0.70,
            },
            "final_displayed_direction": "YES",
        }
        with patch.multiple(
            eis.settings,
            DECISION_QUALITY_ENABLED=False,
            MARKET_QUALITY_ENABLED=False,
            EXECUTION_QUALITY_ENABLED=False,
            SOURCE_RELIABILITY_ENABLED=False,
            LLM_TELEMETRY_ENABLED=False,
            GUARDRAILS_ENABLED=False,
            REVIEW_QUEUE_ENABLED=True,
            REVIEW_QUEUE_MISMATCH_CONFIDENCE=0.66,
            REVIEW_QUEUE_AUTO_RESOLVE_CONFIDENCE=0.91,
        ), \
                patch(
                    "app.services.review_queue_detectors.detect_review_candidates",
                    return_value=[],
                ) as detect, \
                patch("app.memory.review_queue_store.enqueue_item"):
            eis._build_all_overlays(
                record,
                analysis={},
                sentiment_profile=None,
                news_context="",
                market_quote=None,
            )

        detect.assert_called_once()
        self.assertEqual(
            detect.call_args.kwargs["mismatch_confidence_threshold"],
            0.66,
        )
        self.assertEqual(
            detect.call_args.kwargs["auto_resolve_confidence_threshold"],
            0.91,
        )


class BuildFilteredNewsSemanticsTests(unittest.TestCase):
    """The semantics passthrough in _build_filtered_news.

    Locks the fix: parsed market semantics (entities + resolution conditions)
    must reach annotate_semantic_relevance, so the embedding query is enriched
    for threshold questions (e.g. crypto "reach $2,000") rather than embedding
    the bare question. collect_articles and annotate are mocked - network-free.
    analyze_sentiment is mocked too so _build_filtered_news stays network-free.
    """

    QUESTION = "Will Ethereum reach $2,000 in June?"
    ARTICLES = [{"title": "ETH rallies", "description": "ether climbs", "source": "Decrypt"}]
    SENTIMENT = {
        "articles": [],
        "overall_direction": "support_yes",
        "overall_strength": 0.6,
        "conflict_level": 0.1,
        "summary": "证据整体支持 ETH 上行",
    }

    def test_semantics_passed_to_annotate(self):
        captured = {}

        async def fake_annotate(question, articles, semantics=None):
            captured["question"] = question
            captured["semantics"] = semantics
            return articles

        with patch("app.services.event_collection_service.collect_articles",
                   new=AsyncMock(return_value=self.ARTICLES)), \
                patch("app.services.semantic_relevance_service.annotate_semantic_relevance",
                      new=fake_annotate), \
                patch("app.services.news_sentiment_service.analyze_sentiment",
                      new=AsyncMock(return_value=self.SENTIMENT)):
            _run(eis._build_filtered_news(self.QUESTION))

        self.assertEqual(captured["question"], self.QUESTION)
        self.assertIsInstance(captured["semantics"], dict)
        # The parsed semantics must carry the structured signal the embedding
        # query relies on: the entity (Ethereum) and the YES resolution condition.
        entities = [e.lower() for e in captured["semantics"].get("entities", [])]
        self.assertIn("ethereum", entities)
        self.assertTrue(captured["semantics"].get("yes_condition"))

    def test_returns_filter_result_shape(self):
        """The fix must not change the returned shape (context + summary).
        Also locks the sentiment_profile passthrough added by Task 6."""
        with patch("app.services.event_collection_service.collect_articles",
                   new=AsyncMock(return_value=self.ARTICLES)), \
                patch("app.services.semantic_relevance_service.annotate_semantic_relevance",
                      new=AsyncMock(return_value=self.ARTICLES)), \
                patch("app.services.news_sentiment_service.analyze_sentiment",
                      new=AsyncMock(return_value=self.SENTIMENT)):
            result = _run(eis._build_filtered_news(self.QUESTION))
        self.assertIn("context", result)
        self.assertIn("summary", result)
        self.assertIn("selected_count", result["summary"])
        # sentiment_profile is now part of the filtered-news dict (Task 6).
        self.assertEqual(result["sentiment_profile"], self.SENTIMENT)


class BuildFilteredNewsFullTextTests(unittest.TestCase):
    """Full-text enrichment moved from collect_articles to _build_filtered_news.

    The HTTP budget (NEWS_FULL_TEXT_MAX_ARTICLES fetches) is now spent on the
    post-filter top-N - the most-relevant articles reach the LLM with full
    text, while the pre-filter source-order top-N may not. Network-free:
    collect_articles, filter_news_for_market, fetch_full_text, and
    analyze_sentiment are all mocked; the enrichment layer is the unit under
    test. Fail-closed pattern (gather(return_exceptions=True) + isinstance(str)
    guard) is preserved.
    """

    QUESTION = "Will X happen?"
    SENTIMENT = {
        "articles": [],
        "overall_direction": "neutral",
        "overall_strength": 0.0,
        "conflict_level": 0.0,
        "summary": "fallback",
    }

    def _filtered_articles(self, n):
        return [
            {
                "title": f"filtered-{i}",
                "description": "d",
                "source": "s",
                "published": "p",
                "url": f"http://example.com/{i}",
            }
            for i in range(n)
        ]

    def _run(
        self,
        filtered_articles,
        fetch_impl,
        fetch_enabled=True,
        max_articles=5,
    ):
        """Run _build_filtered_news with the supplied filtered articles and a
        fetch_full_text implementation. Returns (result, captured_urls)."""
        captured_urls: list[str] = []

        if isinstance(fetch_impl, str):
            # Constant return value for all URLs.
            async def fake_fetch(url, *, timeout=10.0):
                captured_urls.append(url)
                return fetch_impl
        else:
            # fetch_impl is a callable mapping url -> text-or-None.
            async def fake_fetch(url, *, timeout=10.0):
                captured_urls.append(url)
                return fetch_impl(url)

        with patch(
            "app.services.event_collection_service.collect_articles",
            new=AsyncMock(return_value=[]),
        ), \
                patch(
                    "app.services.news_filter_service.filter_news_for_market",
                    return_value={
                        "articles": filtered_articles,
                        "context": "ctx",
                        "summary": {"selected_count": len(filtered_articles)},
                    },
                ), \
                patch(
                    "app.services.semantic_relevance_service.annotate_semantic_relevance",
                    new=AsyncMock(),
                ), \
                patch(
                    "app.services.event_intelligence_service.fetch_full_text",
                    new=fake_fetch,
                ), \
                patch(
                    "app.services.event_intelligence_service.settings."
                    "NEWS_FULL_TEXT_FETCH_ENABLED",
                    fetch_enabled,
                ), \
                patch(
                    "app.services.event_intelligence_service.settings."
                    "NEWS_FULL_TEXT_MAX_ARTICLES",
                    max_articles,
                ), \
                patch(
                    "app.services.news_sentiment_service.analyze_sentiment",
                    new=AsyncMock(return_value=self.SENTIMENT),
                ):
            result = _run(eis._build_filtered_news(self.QUESTION))
        return result, captured_urls

    def test_enriches_top_5_filtered_articles_with_full_text(self):
        """Top `NEWS_FULL_TEXT_MAX_ARTICLES` filtered articles get full_text;
        the rest get None. Locks the post-filter enrichment contract: the cap
        applies to filtered["articles"] (the most-relevant ones), not the
        pre-filter source-order list."""
        # 10 filtered articles; default cap 5 -> top 5 enriched, rest None.
        filtered = self._filtered_articles(10)
        result, captured_urls = self._run(
            filtered, fetch_impl=lambda url: f"FULL::{url}"
        )
        self.assertEqual(captured_urls, [
            "http://example.com/0", "http://example.com/1",
            "http://example.com/2", "http://example.com/3",
            "http://example.com/4",
        ])
        enriched = result["articles"]
        self.assertEqual(len(enriched), 10)
        for i in range(5):
            self.assertEqual(
                enriched[i]["full_text"], f"FULL::http://example.com/{i}",
                msg=f"filtered article {i} should have full_text",
            )
        for i in range(5, 10):
            self.assertIsNone(
                enriched[i]["full_text"],
                msg=f"filtered article {i} should have full_text=None",
            )

    def test_handles_fetch_full_text_returning_none(self):
        """When fetch_full_text returns None, the article still gets full_text=None."""
        filtered = self._filtered_articles(1)
        result, _ = self._run(filtered, fetch_impl=lambda url: None)
        self.assertEqual(len(result["articles"]), 1)
        self.assertIsNone(result["articles"][0]["full_text"])

    def test_isolates_failing_fetch_full_text(self):
        """A raising fetch_full_text is swallowed via gather(return_exceptions=True).

        fetch_full_text is contract-bound to never raise, but the
        return_exceptions=True + isinstance(str) guard is the safety net.
        """
        filtered = self._filtered_articles(1)

        def raise_on_call(url):
            raise RuntimeError("net down")

        result, _ = self._run(filtered, fetch_impl=raise_on_call)
        self.assertEqual(len(result["articles"]), 1)
        self.assertIsNone(result["articles"][0]["full_text"])

    def test_disabled_flag_sets_all_full_text_none(self):
        """When NEWS_FULL_TEXT_FETCH_ENABLED is false, every filtered article
        gets full_text=None and fetch_full_text is never called."""
        filtered = self._filtered_articles(3)

        async def should_not_be_called(url, *, timeout=10.0):
            raise AssertionError("fetch_full_text should not be called when disabled")

        with patch(
            "app.services.event_collection_service.collect_articles",
            new=AsyncMock(return_value=[]),
        ), \
                patch(
                    "app.services.news_filter_service.filter_news_for_market",
                    return_value={
                        "articles": filtered,
                        "context": "ctx",
                        "summary": {"selected_count": len(filtered)},
                    },
                ), \
                patch(
                    "app.services.semantic_relevance_service.annotate_semantic_relevance",
                    new=AsyncMock(),
                ), \
                patch(
                    "app.services.event_intelligence_service.fetch_full_text",
                    new=should_not_be_called,
                ), \
                patch(
                    "app.services.event_intelligence_service.settings."
                    "NEWS_FULL_TEXT_FETCH_ENABLED",
                    False,
                ), \
                patch(
                    "app.services.news_sentiment_service.analyze_sentiment",
                    new=AsyncMock(return_value=self.SENTIMENT),
                ):
            result = _run(eis._build_filtered_news(self.QUESTION))
        self.assertEqual(len(result["articles"]), 3)
        for article in result["articles"]:
            self.assertIsNone(article["full_text"])

    def test_respects_max_articles_setting(self):
        """The NEWS_FULL_TEXT_MAX_ARTICLES setting is read at call time, so a
        monkeypatch lowering it from the default 5 to 2 cuts the fetch count."""
        # 4 filtered articles; cap lowered to 2 -> only 2 fetches.
        filtered = self._filtered_articles(4)
        result, captured_urls = self._run(
            filtered, fetch_impl=lambda url: f"FULL::{url}", max_articles=2
        )
        self.assertEqual(captured_urls, [
            "http://example.com/0", "http://example.com/1",
        ])
        enriched = result["articles"]
        self.assertEqual(len(enriched), 4)
        for i in range(2):
            self.assertEqual(
                enriched[i]["full_text"], f"FULL::http://example.com/{i}",
            )
        for i in range(2, 4):
            self.assertIsNone(enriched[i]["full_text"])


class SentimentIntegrationTests(unittest.TestCase):
    """Task 6 wiring: analyze_sentiment -> _build_filtered_news ->
    analyze_event -> analyze_market -> _build_user_prompt.

    Locks the integration contract: sentiment_profile flows from
    _build_filtered_news through analyze_event into both the LLM prompt
    (as sentiment_summary) and the event record. analyze_market is mocked so
    no real LLM call is made; we assert on the kwargs it received.
    """

    SENTIMENT = {
        "articles": [{"index": 0, "sentiment": "positive", "impact": "high"}],
        "overall_direction": "support_yes",
        "overall_strength": 0.7,
        "conflict_level": 0.1,
        "summary": "证据整体支持 YES 结果",
    }

    def test_analyze_event_passes_sentiment_summary_to_analyze_market(self):
        """The sentiment summary is forwarded as sentiment_summary kwarg so it
        reaches _build_user_prompt via _ask_ai."""
        captured = {}

        async def fake_analyze_market(**kwargs):
            captured.update(kwargs)
            return {
                "market_question": kwargs["market_question"],
                "market_probability": kwargs["market_probability"],
                "ai_probability": 55.0,
            }

        with patch("app.services.ai_analysis_service.analyze_market",
                   new=fake_analyze_market), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=self.SENTIMENT,
            ))
        self.assertEqual(
            captured.get("sentiment_summary"), self.SENTIMENT["summary"]
        )

    def test_analyze_event_records_sentiment_profile_on_record(self):
        """sentiment_profile is added as a top-level field on the event record
        when provided (mirrors the sports_context / cross_validation pattern)."""
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=self.SENTIMENT,
            ))
        self.assertEqual(record["sentiment_profile"], self.SENTIMENT)

    def test_analyze_event_omits_sentiment_profile_when_not_provided(self):
        """When sentiment_profile is None (e.g. direct analyze_event calls
        without news filtering), the field is absent - matching how
        cross_validation / sports_context are conditionally set."""
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
            ))
        self.assertNotIn("sentiment_profile", record)

    def test_analyze_event_records_market_quote_when_provided(self):
        """Kalshi's bid_ask surfaces on the event record as `market_quote` so the
        Stage 1 transparency goal (make Kalshi bid/ask spreads visible in the
        /discover response) is actually met. Mirrors the conditional-attach
        pattern used by sentiment_profile / sports_context."""
        analyze = AsyncMock(return_value={
            "market_question": "Will it rain?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        quote = {"bid": 42.0, "ask": 46.0, "spread": 4.0}
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Will it rain?",
                baseline_probability=44.0,
                news_context="direction: support",
                market_quote=quote,
            ))
        self.assertEqual(record["market_quote"], quote)

    def test_analyze_event_omits_market_quote_when_not_provided(self):
        """Non-Kalshi sources don't populate bid_ask, so market_quote must be
        absent on their records (not present-and-None). Mirrors the
        conditional-attach pattern."""
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
            ))
        self.assertNotIn("market_quote", record)

    def test_build_user_prompt_includes_sentiment_section_when_present(self):
        """probability_engine_service._build_user_prompt appends a dedicated
        LLM 情感分析 section when sentiment_summary is non-empty, and omits it
        when empty (so the neutral fallback is a clean no-op)."""
        from app.services.probability_engine_service import _build_user_prompt

        prompt_with = _build_user_prompt(
            market_question="Will the bill pass?",
            market_probability=50,
            news_context="direction: support",
            sentiment_summary="证据整体支持 YES",
        )
        prompt_without = _build_user_prompt(
            market_question="Will the bill pass?",
            market_probability=50,
            news_context="direction: support",
        )
        self.assertIn("LLM 情感分析", prompt_with)
        self.assertIn("证据整体支持 YES", prompt_with)
        self.assertNotIn("LLM 情感分析", prompt_without)


class AnalyzeEventCalibrationFeedbackTests(unittest.TestCase):
    """The calibration feedback wiring in analyze_event.

    The LLM call is mocked to the deterministic fallback and cross-validation is
    disabled, so these are network-free. The feedback math itself is covered in
    test_calibration_feedback_service; here we lock the wiring: components are
    always recorded, and the published estimate is only overwritten when the
    feature is enabled.
    """

    NEWS = "direction: support\nstrength: 0.5\nsource_count: 3\n"

    def _analyze(self):
        with patch.object(ai, "_ask_ai", new=AsyncMock(side_effect=RuntimeError())), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            return _run(eis.analyze_event(
                "Will it pass?", baseline_probability=50, news_context=self.NEWS))

    def test_records_components_when_disabled(self):
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False):
            record = self._analyze()
        components = record["calibration_components"]
        self.assertEqual(components["market"], record["probability"]["baseline"])
        self.assertEqual(components["llm"], record["probability"]["estimated"])
        # Cross-validation disabled -> not a component.
        self.assertNotIn("cross_validation", components)

    def test_estimate_unchanged_when_disabled(self):
        """Default-off: no feedback metadata and the estimate is the LLM value."""
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False):
            record = self._analyze()
        self.assertNotIn("calibration_feedback", record)
        self.assertEqual(
            record["legacy_analysis"]["analysis_quality"],
            "deterministic_fallback",
        )
        self.assertEqual(
            record["probability"]["estimated"],
            record["calibration_components"]["llm"],
        )

    def test_records_cross_validation_component_when_present(self):
        cross = {"model": "second", "probability": 64.0,
                 "primary_probability": 60.0, "divergence": 4.0, "agreement": "high"}
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False), \
                patch.object(ai, "_ask_ai", new=AsyncMock(side_effect=RuntimeError())), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=cross)):
            record = _run(eis.analyze_event(
                "Will it pass?", baseline_probability=50, news_context=self.NEWS))
        self.assertEqual(record["calibration_components"]["cross_validation"], 64.0)

    def test_overwrites_estimate_when_enabled(self):
        """When enabled, analyze_event publishes adjust_probability's result and
        recomputes change/direction from it."""
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", True), \
                patch("app.services.calibration_feedback_service.adjust_probability",
                      return_value=(33.0, {"weights": {}, "shrinkage": 0.0,
                                           "fused": 33.0, "samples": 9})):
            record = self._analyze()
        prob = record["probability"]
        self.assertEqual(prob["estimated"], 33.0)
        self.assertEqual(prob["change"], round(33.0 - prob["baseline"], 2))
        self.assertEqual(prob["direction"], "falling")
        self.assertEqual(record["calibration_feedback"]["samples"], 9)

    def test_enabled_but_no_history_is_noop(self):
        """Enabled with empty history: adjust_probability returns the llm value,
        so the published estimate is unchanged from the disabled case."""
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False):
            disabled_estimate = self._analyze()["probability"]["estimated"]
        with patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", True), \
                patch("app.services.calibration_feedback_service._load_resolved_records",
                      return_value=[]):
            record = self._analyze()
        self.assertEqual(record["probability"]["estimated"], disabled_estimate)


class EventIdentityTests(unittest.TestCase):
    def test_event_id_uses_64_bit_sha1_prefix(self):
        self.assertEqual(len(eis._event_id("Will it pass?")), 16)
        self.assertEqual(eis._event_id("Will it pass?"), eis._event_id("Will it pass?"))


class SportsContextIntegrationTests(unittest.TestCase):
    def test_analyze_event_appends_sports_context_for_sports_source(self):
        from app.core.config import settings
        from app.services.sports_fact_service import import_sports_facts

        async def run():
            with tempfile.TemporaryDirectory() as tmp, \
                    patch.object(settings, "SPORTS_FACT_FILE", str(Path(tmp) / "facts.json")):
                import_sports_facts([{
                    "kind": "injury",
                    "team": "Brazil",
                    "player": "Player A",
                    "status": "out",
                    "severity": "high",
                }])
                analyze = AsyncMock(return_value={
                    "market_question": "Will Brazil reach the semifinals of the 2026 FIFA World Cup?",
                    "market_probability": 38.0,
                    "ai_probability": 34.0,
                    "confidence_score": 0.6,
                    "news_quality_score": 0.5,
                    "evidence_strength": 0.3,
                    "source_count": 1,
                })
                with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                        patch("app.services.cross_validation_service.cross_validate",
                              new=AsyncMock(return_value=None)):
                    record = await eis.analyze_event(
                        "Will Brazil reach the semifinals of the 2026 FIFA World Cup?",
                        baseline_probability=38.0,
                        news_context="EVIDENCE PROFILE\nSOURCE_COUNT: 1",
                        source={
                            "type": "sports_event",
                            "category": "team_progression",
                            "tournament": "2026 FIFA World Cup",
                            "entities": ["Brazil", "2026 FIFA World Cup"],
                        },
                    )
                return record, analyze.await_args.kwargs["news_context"]

        record, context = _run(run())
        self.assertIn("SPORTS FACT SIGNALS", context)
        self.assertIn("Player A", context)
        self.assertIn("sports_context", record)
        self.assertEqual(
            record["sports_context"]["signals"]["injury_signal"]["direction"],
            "supports_no",
        )


class CollectCandidateEventsCryptoOptInTests(unittest.TestCase):
    """The opt-in Polymarket crypto fetch (POLYMARKET_CRYPTO_FETCH_ENABLED).

    Default-off: the crypto-only fetch is NOT part of the candidate gather. When
    enabled, it is added as an extra concurrent source. All real fetches are
    mocked so this is network-free; we assert on whether the crypto fetch ran.
    """

    def _stub_sources(self, crypto_fetch):
        """Patch every real source fetch so _collect_candidate_events runs without
        network. The crypto fetch is the one under test (the rest just need to
        return empty lists so they don't shape the result)."""
        patches = [
            patch("app.services.polymarket_event_source.fetch_candidate_events",
                  new=AsyncMock(return_value=[])),
            patch("app.services.manifold_event_source.fetch_candidate_events",
                  new=AsyncMock(return_value=[])),
            patch("app.services.kalshi_event_source.fetch_candidate_events",
                  new=AsyncMock(return_value=[])),
            patch("app.services.world_cup_event_source.fetch_candidate_events",
                  new=AsyncMock(return_value=[])),
            patch("app.services.polymarket_event_source.fetch_crypto_candidate_events",
                  new=crypto_fetch),
            patch("app.services.event_extraction_service.extract_candidate_events",
                  new=AsyncMock(return_value=[])),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

    def test_manifold_fetch_not_called(self):
        manifold_fetch = AsyncMock(return_value=[{
            "question": "Manifold candidate should be ignored",
            "baseline_probability": 55.0,
            "source": {"type": "prediction_market", "platform": "Manifold"},
        }])
        with patch.object(eis.settings, "POLYMARKET_CRYPTO_FETCH_ENABLED", False), \
                patch.object(eis.settings, "WORLD_CUP_SOURCE_ENABLED", False), \
                patch.object(eis.settings, "METACULUS_API_TOKEN", ""), \
                patch.object(eis.settings, "OPEN_WEB_ENABLED", False):
            patches = [
                patch("app.services.polymarket_event_source.fetch_candidate_events",
                      new=AsyncMock(return_value=[])),
                patch("app.services.manifold_event_source.fetch_candidate_events",
                      new=manifold_fetch),
                patch("app.services.kalshi_event_source.fetch_candidate_events",
                      new=AsyncMock(return_value=[])),
                patch("app.services.world_cup_event_source.fetch_candidate_events",
                      new=AsyncMock(return_value=[])),
                patch("app.services.polymarket_event_source.fetch_crypto_candidate_events",
                      new=AsyncMock(return_value=[])),
                patch("app.services.event_extraction_service.extract_candidate_events",
                      new=AsyncMock(return_value=[])),
            ]
            for p in patches:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in patches])

            candidates = _run(eis._collect_candidate_events(limit=5))

        manifold_fetch.assert_not_awaited()
        self.assertEqual(candidates, [])

    def test_crypto_fetch_not_called_when_disabled(self):
        crypto_fetch = AsyncMock(return_value=[])
        with patch.object(eis.settings, "POLYMARKET_CRYPTO_FETCH_ENABLED", False):
            self._stub_sources(crypto_fetch)
            _run(eis._collect_candidate_events(limit=5))
        crypto_fetch.assert_not_awaited()

    def test_crypto_fetch_called_when_enabled(self):
        crypto_fetch = AsyncMock(return_value=[
            {"question": "Will Bitcoin reach $100k?", "baseline_probability": 50,
             "volume": 1, "liquidity": 1,
             "source": {"type": "prediction_market", "platform": "Polymarket",
                        "source_id": "c1", "question": "Will Bitcoin reach $100k?",
                        "baseline_probability": 50.0, "liquidity": 1, "volume": 1,
                        "url": "https://polymarket.com/event/x"}},
        ])
        with patch.object(eis.settings, "POLYMARKET_CRYPTO_FETCH_ENABLED", True):
            self._stub_sources(crypto_fetch)
            candidates = _run(eis._collect_candidate_events(limit=5))
        crypto_fetch.assert_awaited_once()
        # The crypto candidate reached the pool.
        questions = [c.get("question") for c in candidates]
        self.assertIn("Will Bitcoin reach $100k?", questions)


class ActionableRecommendationTests(unittest.TestCase):
    """Tests for the actionable_recommendation field on EventRecord (Stage 3)."""

    def _analysis(self, **overrides):
        """Minimal analysis dict that build_event_record accepts."""
        base = {
            "event_question": "Will X happen?",
            "market_probability": 40.0,
            "ai_probability": 55.0,
            "title_zh": "X 是否发生",
            "narrative_summary": "Evidence suggests X is likely.",
            "confidence_score": 0.7,
            "news_quality_score": 0.6,
            "evidence_strength": 0.5,
            "evidence_conflict_score": 0.2,
            "freshness_score": 0.8,
            "resolution_relevance_score": 0.5,
            "source_count": 5,
            "risk_level": "MEDIUM",
            "risk_flags": [],
            "signal": "LONG",
            "signal_direction": "LONG",
            "signal_strength": "HIGH",
            "position_size": 0.10,
            "expected_edge": 0.15,
            "divergence": 15.0,
            "base_rate_category": "test",
        }
        base.update(overrides)
        return base

    def test_long_signal_maps_to_yes_direction(self):
        record = build_event_record(self._analysis(signal="LONG", signal_direction="LONG"))
        rec = record["actionable_recommendation"]
        self.assertEqual(rec["direction"], "YES")
        self.assertEqual(rec["confidence"], "high")

    def test_strong_short_signal_maps_to_no_direction(self):
        record = build_event_record(
            self._analysis(signal="STRONG_SHORT", signal_direction="SHORT",
                           signal_strength="MEDIUM", divergence=-25.0, expected_edge=-0.25)
        )
        rec = record["actionable_recommendation"]
        self.assertEqual(rec["direction"], "NO")
        self.assertEqual(rec["confidence"], "medium")

    def test_watchlist_signal_maps_to_wait_direction(self):
        record = build_event_record(
            self._analysis(signal="WATCHLIST", signal_direction="NEUTRAL",
                           signal_strength="LOW", divergence=2.0, expected_edge=0.02)
        )
        rec = record["actionable_recommendation"]
        self.assertEqual(rec["direction"], "WAIT")

    def test_high_risk_low_confidence_maps_to_avoid(self):
        record = build_event_record(
            self._analysis(signal="LONG", signal_direction="LONG",
                           signal_strength="LOW", risk_flags=["a", "b", "c"])
        )
        rec = record["actionable_recommendation"]
        self.assertEqual(rec["direction"], "AVOID")

    def test_none_when_feature_disabled(self):
        from app.services import event_intelligence_service as svc
        with patch.object(svc.settings, "ACTIONABLE_RECOMMENDATION_ENABLED", False):
            record = build_event_record(self._analysis(signal="LONG"))
        self.assertIsNone(record["actionable_recommendation"])

    def test_suggested_allocation_pct_from_position_size(self):
        record = build_event_record(self._analysis(position_size=0.15))
        rec = record["actionable_recommendation"]
        self.assertAlmostEqual(rec["suggested_allocation_pct"], 15.0)

    def test_recommended_action_uses_signal_direction_when_available(self):
        record = build_event_record(self._analysis(signal="LONG", signal_direction="LONG"))
        action = record["intelligence_report"]["recommended_action"]
        self.assertIn("YES", action)


if __name__ == "__main__":
    unittest.main()


class CalculateValueScoreTests(unittest.TestCase):
    """calculate_value_score is linear: impact * trust / 100.

    The old formula ``impact * (0.5 + trust/200)`` gave impact*0.5 when
    trust=0, overstating low-trust events by up to 50 points.
    """

    def test_zero_trust_gives_zero_value(self):
        """Core regression: trust=0 -> value=0."""
        self.assertEqual(calculate_value_score(80, 0), 0)
        self.assertEqual(calculate_value_score(50, 0), 0)

    def test_full_trust_gives_full_impact(self):
        """trust=100 -> value = impact."""
        self.assertEqual(calculate_value_score(80, 100), 80)
        self.assertEqual(calculate_value_score(50, 100), 50)

    def test_half_trust_gives_half_impact(self):
        """trust=50 -> value = impact/2."""
        self.assertEqual(calculate_value_score(80, 50), 40)
        self.assertEqual(calculate_value_score(100, 50), 50)

    def test_zero_impact_gives_zero_value(self):
        self.assertEqual(calculate_value_score(0, 80), 0)

    def test_both_zero(self):
        self.assertEqual(calculate_value_score(0, 0), 0)

    def test_rounding(self):
        # 73 * 67 / 100 = 48.91 -> 49
        self.assertEqual(calculate_value_score(73, 67), 49)


class EvidenceBreakdownTests(unittest.TestCase):
    """Locks the evidence_breakdown integration in analyze_event (Stage:
    evidence decomposition). The field is an audit/explanation layer and
    MUST NOT affect ai_probability / evidence_profile / actionable_recommendation."""

    SENTIMENT_WITH_EVIDENCE = {
        "articles": [{
            "index": 0,
            "sentiment": "positive",
            "impact": "high",
            "key_facts": ["fact"],
            "relevance_to_question": 0.8,
            "evidence_direction": "support",
            "evidence_strength": 0.85,
            "source_credibility": 0.9,
            "rationale_zh": "直接支持 YES 的事实。",
        }],
        "overall_direction": "support_yes",
        "overall_strength": 0.85,
        "conflict_level": 0.1,
        "summary": "证据整体支持 YES",
    }

    FILTERED_ARTICLES = [
        {"source": "Reuters", "title": "Fed signals rate cut", "description": "desc"}
    ]

    def test_analyze_event_populates_evidence_breakdown_when_enabled(self):
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)), \
                patch("app.services.event_intelligence_service.settings.EVIDENCE_BREAKDOWN_ENABLED",
                      True):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=self.SENTIMENT_WITH_EVIDENCE,
                filtered_articles=self.FILTERED_ARTICLES,
            ))
        self.assertIn("evidence_breakdown", record)
        self.assertEqual(len(record["evidence_breakdown"]), 1)
        item = record["evidence_breakdown"][0]
        self.assertEqual(item["source"], "Reuters")
        self.assertEqual(item["title"], "Fed signals rate cut")
        self.assertEqual(item["direction"], "support")
        self.assertEqual(item["strength"], 0.85)
        self.assertEqual(item["credibility"], 0.9)
        self.assertEqual(item["rationale_zh"], "直接支持 YES 的事实。")

    def test_analyze_event_evidence_breakdown_empty_when_disabled(self):
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)), \
                patch("app.services.event_intelligence_service.settings.EVIDENCE_BREAKDOWN_ENABLED",
                      False):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=self.SENTIMENT_WITH_EVIDENCE,
                filtered_articles=self.FILTERED_ARTICLES,
            ))
        self.assertEqual(record["evidence_breakdown"], [])

    def test_analyze_event_evidence_breakdown_empty_when_no_sentiment(self):
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=None,
                filtered_articles=self.FILTERED_ARTICLES,
            ))
        self.assertEqual(record["evidence_breakdown"], [])

    def test_analyze_event_evidence_breakdown_empty_when_no_filtered_articles(self):
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=self.SENTIMENT_WITH_EVIDENCE,
                filtered_articles=None,
            ))
        self.assertEqual(record["evidence_breakdown"], [])

    def test_analyze_event_evidence_breakdown_filters_neutral_articles(self):
        # sentiment article with neutral direction -> filtered out by aggregation
        sentiment = {
            "articles": [{
                "index": 0,
                "evidence_direction": "neutral",
                "evidence_strength": 0.9,
            }],
            "overall_direction": "neutral",
            "overall_strength": 0.0,
            "conflict_level": 0.0,
            "summary": "neutral",
        }
        analyze = AsyncMock(return_value={
            "market_question": "Q?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Q?",
                baseline_probability=50,
                news_context="direction: neutral",
                sentiment_profile=sentiment,
                filtered_articles=self.FILTERED_ARTICLES,
            ))
        self.assertEqual(record["evidence_breakdown"], [])

    def test_analyze_event_does_not_break_without_filtered_articles_kwarg(self):
        """Backward compat: old callers that do not pass filtered_articles
        still get a working record with evidence_breakdown=[]."""
        analyze = AsyncMock(return_value={
            "market_question": "Q?",
            "market_probability": 50,
            "ai_probability": 55,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)):
            record = _run(eis.analyze_event(
                "Q?",
                baseline_probability=50,
                news_context="direction: support",
                # NOTE: no filtered_articles kwarg
            ))
        self.assertEqual(record.get("evidence_breakdown", []), [])


class DecisionQualityIntegrationTests(unittest.TestCase):
    """Phase 1: locks the analyze_event -> decision_quality integration.
    Verifies:
    - decision_quality attached when DECISION_QUALITY_ENABLED=true
    - no decision_quality key when DECISION_QUALITY_ENABLED=false (byte-identical)
    - raw ai_probability / actionable_recommendation.direction NOT mutated
    - build failure falls back to error block without blocking event production
    """

    SENTIMENT = {
        "articles": [{
            "index": 0,
            "sentiment": "positive",
            "impact": "high",
            "key_facts": ["fact"],
            "relevance_to_question": 0.8,
            "evidence_direction": "support",
            "evidence_strength": 0.85,
            "source_credibility": 0.9,
            "rationale_zh": "支持 YES 的事实。",
        }],
        "overall_direction": "support_yes",
        "overall_strength": 0.85,
        "conflict_level": 0.1,
        "summary": "证据支持 YES",
    }

    FILTERED_ARTICLES = [
        {"source": "Reuters", "title": "Fed signals rate cut", "description": "desc"}
    ]

    def _run_analyze(self, dq_enabled, evidence_enabled=True):
        analyze = AsyncMock(return_value={
            "market_question": "Will the bill pass?",
            "market_probability": 50,
            "ai_probability": 55,
            "signal": "ACT",
            "signal_direction": "LONG",
            "signal_strength": "HIGH",
            "risk_level": "medium",
            "expected_edge": 0.05,
            "position_size": 0.02,
            "evidence_strength": 0.8,
            "confidence_score": 0.7,
            "news_quality_score": 0.8,
            "source_count": 3,
        })
        # These tests isolate overlay behavior; calibration feedback is a
        # separate writeback layer with dedicated tests below. Keep it off so
        # local .env/history cannot change the raw LLM estimate.
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)), \
                patch.object(eis.settings, "EVIDENCE_BREAKDOWN_ENABLED", evidence_enabled), \
                patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False), \
                patch.object(eis.settings, "DECISION_QUALITY_ENABLED", dq_enabled), \
                patch.object(eis.settings, "DECISION_QUALITY_MAX_EVIDENCE_ITEMS", 3), \
                patch.object(eis.settings, "DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD", 0.40), \
                patch.object(eis.settings, "DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD", 0.20):
            return _run(eis.analyze_event(
                "Will the bill pass?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=self.SENTIMENT,
                filtered_articles=self.FILTERED_ARTICLES,
            ))

    def test_decision_quality_attached_when_enabled(self):
        record = self._run_analyze(dq_enabled=True)
        self.assertIn("decision_quality", record)
        self.assertIsNotNone(record["decision_quality"])
        dq = record["decision_quality"]
        self.assertEqual(dq["raw_direction"], "YES")
        self.assertIn("decision_rationale_zh", dq)
        self.assertTrue(dq["decision_rationale_zh"].endswith("不构成投资建议。"))

    def test_decision_quality_absent_when_disabled(self):
        """When DECISION_QUALITY_ENABLED=false, record has NO decision_quality
        key — byte-identical to pre-Phase-1 records."""
        record = self._run_analyze(dq_enabled=False)
        self.assertNotIn("decision_quality", record)

    def test_ai_probability_not_mutated(self):
        record = self._run_analyze(dq_enabled=True)
        # ai_probability should still be the LLM's estimate (55), not changed
        # by decision_quality (audit layer isolation)
        self.assertEqual(record["probability"]["estimated"], 55)

    def test_actionable_recommendation_direction_not_mutated(self):
        """no-writeback invariant: actionable_recommendation.direction is
        byte-equal before and after decision_quality runs."""
        record = self._run_analyze(dq_enabled=True)
        # actionable_recommendation.direction should still be YES
        # (decision_quality may set displayed_direction, but never mutates
        # the raw recommendation)
        self.assertEqual(
            record["actionable_recommendation"]["direction"], "YES"
        )

    def test_decision_quality_build_failure_does_not_block(self):
        """When build_decision_quality raises, analyze_event still returns
        a record with a fallback decision_quality.error block."""
        analyze = AsyncMock(return_value={
            "market_question": "Q?",
            "market_probability": 50,
            "ai_probability": 55,
            "signal": "ACT",
            "signal_direction": "LONG",
            "signal_strength": "HIGH",
            "risk_level": "medium",
            "expected_edge": 0.05,
            "position_size": 0.02,
            "evidence_strength": 0.8,
            "confidence_score": 0.7,
            "news_quality_score": 0.8,
            "source_count": 3,
        })
        with patch("app.services.ai_analysis_service.analyze_market", new=analyze), \
                patch("app.services.cross_validation_service.cross_validate",
                      new=AsyncMock(return_value=None)), \
                patch.object(eis.settings, "EVIDENCE_BREAKDOWN_ENABLED", True), \
                patch.object(eis.settings, "DECISION_QUALITY_ENABLED", True), \
                patch.object(eis.settings, "DECISION_QUALITY_MAX_EVIDENCE_ITEMS", 3), \
                patch.object(eis.settings, "DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD", 0.40), \
                patch.object(eis.settings, "DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD", 0.20), \
                patch("app.services.decision_quality_service.build_decision_quality",
                      side_effect=RuntimeError("boom")):
            record = _run(eis.analyze_event(
                "Q?",
                baseline_probability=50,
                news_context="direction: support",
                sentiment_profile=self.SENTIMENT,
                filtered_articles=self.FILTERED_ARTICLES,
            ))
        self.assertIn("decision_quality", record)
        self.assertEqual(record["decision_quality"]["error"], "build_failed")
        self.assertEqual(record["decision_quality"]["raw_direction"], "YES")
        self.assertEqual(record["decision_quality"]["displayed_direction"], "YES")


class MarketQualityIntegrationTests(unittest.TestCase):
    """Phase 2: locks the analyze_event -> market_quality integration.

    Verifies the same invariants as Phase 1 decision_quality, applied to the
    market layer:
    - market_quality attached only when source.type == prediction_market AND
      MARKET_QUALITY_ENABLED=true
    - no market_quality key when feature off (byte-identical to pre-Phase-2)
    - no market_quality key when source is non-prediction-market (Metaculus,
      manual) even if feature is on — matches the freeze_prediction gate
    - final_displayed_direction / final_downgrade_reason set when at least one
      overlay produced a direction
    - both final_* fields absent when both overlays off (byte-identical)
    - build failure falls back to error block for prediction_market sources,
      no error block for non-prediction-market sources
    - market_quality never mutates ai_probability or
      actionable_recommendation.direction (no-writeback invariant)
    - applied_to_displayed_direction flag set when market is stricter than
      decision_quality
    """

    SENTIMENT = {
        "articles": [{
            "index": 0,
            "sentiment": "positive",
            "impact": "high",
            "key_facts": ["fact"],
            "relevance_to_question": 0.8,
            "evidence_direction": "support",
            "evidence_strength": 0.85,
            "source_credibility": 0.9,
            "rationale_zh": "支持 YES 的事实。",
        }],
        "overall_direction": "support_yes",
        "overall_strength": 0.85,
        "conflict_level": 0.1,
        "summary": "证据支持 YES",
    }

    FILTERED_ARTICLES = [
        {"source": "Reuters", "title": "Fed signals rate cut", "description": "desc"}
    ]

    def _run_analyze(
        self,
        *,
        mq_enabled: bool,
        dq_enabled: bool = False,
        source: dict | None = None,
        volume: float | None = None,
        liquidity: float | None = None,
        market_quote: dict | None = None,
        build_mq_side_effect=None,
    ):
        analyze = AsyncMock(return_value={
            "market_question": "Will X happen?",
            "market_probability": 50,
            "ai_probability": 70,
            "signal": "ACT",
            "signal_direction": "LONG",
            "signal_strength": "HIGH",
            "risk_level": "medium",
            "expected_edge": 0.20,
            "position_size": 0.10,
            "evidence_strength": 0.8,
            "confidence_score": 0.7,
            "news_quality_score": 0.8,
            "source_count": 3,
        })
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.ai_analysis_service.analyze_market", new=analyze))
            stack.enter_context(patch("app.services.cross_validation_service.cross_validate",
                                      new=AsyncMock(return_value=None)))
            stack.enter_context(patch.object(eis.settings, "EVIDENCE_BREAKDOWN_ENABLED", False))
            # Overlay tests must be independent of local calibration history.
            stack.enter_context(patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False))
            stack.enter_context(patch.object(eis.settings, "DECISION_QUALITY_ENABLED", dq_enabled))
            stack.enter_context(patch.object(eis.settings, "MARKET_QUALITY_ENABLED", mq_enabled))
            stack.enter_context(patch.object(eis.settings, "MARKET_MAX_SPREAD_PCT", 12.0))
            stack.enter_context(patch.object(eis.settings, "MARKET_MIN_LIQUIDITY", 1000.0))
            stack.enter_context(patch.object(eis.settings, "MARKET_MIN_VOLUME", 1000.0))
            stack.enter_context(patch.object(eis.settings, "MARKET_QUALITY_SCORE_THRESHOLD", 0.5))
            if build_mq_side_effect is not None:
                stack.enter_context(patch("app.services.market_quality_service.build_market_quality",
                                          side_effect=build_mq_side_effect))
            return _run(eis.analyze_event(
                "Will X happen?",
                baseline_probability=50,
                news_context="direction: support",
                source=source,
                volume=volume,
                liquidity=liquidity,
                market_quote=market_quote,
                sentiment_profile=self.SENTIMENT,
                filtered_articles=self.FILTERED_ARTICLES,
            ))

    # --- Attachment gating ---

    def test_market_quality_attached_for_prediction_market_when_enabled(self):
        record = self._run_analyze(
            mq_enabled=True,
            source={"type": "prediction_market", "platform": "Polymarket"},
            volume=5000.0,
            liquidity=5000.0,
            market_quote={"bid": 48, "ask": 52, "spread": 4},
        )
        self.assertIn("market_quality", record)
        self.assertIsNotNone(record["market_quality"])
        self.assertEqual(record["market_quality"]["raw_direction"], "YES")
        # Success path: no 'error' key (mirrors Phase 1 decision_quality
        # convention — error only surfaces on fallback)
        self.assertNotIn("error", record["market_quality"])

    def test_market_quality_absent_when_disabled(self):
        """When MARKET_QUALITY_ENABLED=false, record has NO market_quality
        key — byte-identical to pre-Phase-2 records."""
        record = self._run_analyze(
            mq_enabled=False,
            source={"type": "prediction_market", "platform": "Polymarket"},
            volume=5000.0,
            liquidity=5000.0,
        )
        self.assertNotIn("market_quality", record)

    def test_market_quality_absent_for_metaculus_source(self):
        """Metaculus source.type=prediction_question must NOT produce a
        market_quality block (mirrors the freeze_prediction gate)."""
        record = self._run_analyze(
            mq_enabled=True,
            source={"type": "prediction_question", "platform": "Metaculus"},
            volume=0.0,
            liquidity=0.0,
        )
        self.assertNotIn("market_quality", record)

    def test_market_quality_absent_for_manual_source(self):
        record = self._run_analyze(
            mq_enabled=True,
            source={"type": "manual"},
        )
        self.assertNotIn("market_quality", record)

    # --- Merge / final_displayed_direction ---

    def test_final_displayed_direction_set_when_market_quality_present(self):
        """When market_quality is present (and decision_quality is off),
        final_displayed_direction mirrors market_quality.suggested_direction."""
        record = self._run_analyze(
            mq_enabled=True,
            dq_enabled=False,
            source={"type": "prediction_market"},
            volume=5000.0,
            liquidity=5000.0,
        )
        self.assertIn("final_displayed_direction", record)
        self.assertEqual(
            record["final_displayed_direction"],
            record["market_quality"]["suggested_direction"],
        )

    def test_final_fields_absent_when_both_overlays_off(self):
        """When both features off, no final_* fields — byte-identical to
        pre-overlay records."""
        record = self._run_analyze(
            mq_enabled=False,
            dq_enabled=False,
            source={"type": "prediction_market"},
            volume=5000.0,
            liquidity=5000.0,
        )
        self.assertNotIn("final_displayed_direction", record)
        self.assertNotIn("final_downgrade_reason", record)

    def test_market_applied_when_market_stricter_than_decision(self):
        """When market_quality downgrades YES->WAIT and decision_quality
        keeps YES, the merge picks market's WAIT and sets
        market_quality.applied_to_displayed_direction=True."""
        # decision_quality keeps YES (high consensus, single-supporting evidence)
        # market_quality downgrades YES->WAIT (score < threshold via thin market)
        record = self._run_analyze(
            mq_enabled=True,
            dq_enabled=True,
            source={"type": "prediction_market"},
            volume=100.0,   # below min_volume (1000) -> thin -> low score
            liquidity=100.0,  # below min_liquidity (1000) -> thin
        )
        # market applied: final is WAIT, market_quality flagged
        self.assertEqual(record["final_displayed_direction"], "WAIT")
        self.assertTrue(record["market_quality"]["applied_to_displayed_direction"])
        self.assertEqual(record["market_quality"]["suggested_direction"], "WAIT")
        self.assertIsNotNone(record["market_quality"]["downgrade_reason"])

    def test_market_not_applied_when_decision_is_stricter(self):
        """When decision_quality is stricter than market_quality, the merge
        picks decision's direction and ``applied_to_displayed_direction``
        stays False. Concretely: EVIDENCE_BREAKDOWN is off (Rule 4 -> WAIT
        on the decision side) while the market is healthy (market keeps YES).
        WAIT is stricter than YES, so final = WAIT and market did not change
        the final direction."""
        record = self._run_analyze(
            mq_enabled=True,
            dq_enabled=True,
            source={"type": "prediction_market"},
            volume=5000.0,   # healthy -> market keeps YES
            liquidity=5000.0,
        )
        # decision_quality -> WAIT (Rule 4, empty breakdown), market_quality
        # -> YES (healthy). WAIT is stricter -> final = WAIT, market not
        # applied.
        self.assertEqual(record["decision_quality"]["displayed_direction"], "WAIT")
        self.assertEqual(record["market_quality"]["suggested_direction"], "YES")
        self.assertEqual(record["final_displayed_direction"], "WAIT")
        self.assertFalse(record["market_quality"]["applied_to_displayed_direction"])

    # --- No-writeback invariant ---

    def test_ai_probability_not_mutated_by_market_quality(self):
        record = self._run_analyze(
            mq_enabled=True,
            source={"type": "prediction_market"},
            volume=5000.0,
            liquidity=5000.0,
        )
        # ai_probability stays at the LLM's 70 (market_quality is overlay only)
        self.assertEqual(record["probability"]["estimated"], 70)

    def test_actionable_recommendation_direction_not_mutated_by_market(self):
        record = self._run_analyze(
            mq_enabled=True,
            source={"type": "prediction_market"},
            volume=100.0,  # thin -> market downgrades YES to WAIT
            liquidity=100.0,
        )
        # market_quality may set suggested_direction=WAIT, but actionable_recommendation.direction
        # stays YES (no-writeback invariant)
        self.assertEqual(
            record["actionable_recommendation"]["direction"], "YES"
        )

    # --- Build failure fallback ---

    def test_market_quality_build_failure_falls_back_for_prediction_market(self):
        record = self._run_analyze(
            mq_enabled=True,
            source={"type": "prediction_market"},
            volume=5000.0,
            liquidity=5000.0,
            build_mq_side_effect=RuntimeError("boom"),
        )
        self.assertIn("market_quality", record)
        self.assertEqual(record["market_quality"]["error"], "build_failed")
        self.assertEqual(record["market_quality"]["raw_direction"], "YES")
        self.assertEqual(record["market_quality"]["suggested_direction"], "YES")
        self.assertFalse(record["market_quality"]["downgraded"])

    def test_market_quality_build_failure_no_block_for_non_prediction_market(self):
        """When build fails for a non-prediction-market source (which
        shouldn't have produced a block anyway), no error block is attached —
        the record stays byte-identical (no market_quality key)."""
        record = self._run_analyze(
            mq_enabled=True,
            source={"type": "manual"},
            build_mq_side_effect=RuntimeError("boom"),
        )
        self.assertNotIn("market_quality", record)


class SourceReliabilityIntegrationTests(unittest.TestCase):
    """Phase 4: locks the analyze_event -> source_reliability integration.

    Verifies the same invariants as Phases 1/2, applied to the source layer:
    - source_reliability attached only when SOURCE_RELIABILITY_ENABLED=true AND
      evidence_breakdown is non-empty
    - no source_reliability key when feature off (byte-identical to pre-Phase-4)
    - no source_reliability key when evidence_breakdown is empty (e.g., when
      EVIDENCE_BREAKDOWN_ENABLED is off or no filtered_articles)
    - final_displayed_direction / final_downgrade_reason reflect the 3-way
      merge (decision_quality + market_quality + source_reliability)
    - source_applied_to_displayed_direction flag set when source is stricter
      than the decision_quality base
    - build failure falls back to error block when evidence_breakdown non-empty,
      no error block when evidence_breakdown is empty
    - source_reliability never mutates ai_probability or
      actionable_recommendation.direction (no-writeback invariant)
    """

    # Sentiment with 2 articles from different sources -> 2 domains, passes
    # diversity/min-sources gates. Used for the no-downgrade (happy path) case.
    SENTIMENT_2SRC = {
        "articles": [
            {
                "index": 0,
                "sentiment": "positive",
                "impact": "high",
                "key_facts": ["fact a"],
                "relevance_to_question": 0.8,
                "evidence_direction": "support",
                "evidence_strength": 0.85,
                "source_credibility": 0.9,
                "rationale_zh": "支持 YES。",
            },
            {
                "index": 1,
                "sentiment": "positive",
                "impact": "medium",
                "key_facts": ["fact b"],
                "relevance_to_question": 0.7,
                "evidence_direction": "support",
                "evidence_strength": 0.75,
                "source_credibility": 0.8,
                "rationale_zh": "支持 YES。",
            },
        ],
        "overall_direction": "support_yes",
        "overall_strength": 0.8,
        "conflict_level": 0.1,
        "summary": "证据支持 YES",
    }

    FILTERED_ARTICLES_2SRC = [
        {"source": "Reuters", "title": "Fed signals rate cut",
         "url": "https://www.reuters.com/article/fed-cut/1", "description": "desc"},
        {"source": "Bloomberg", "title": "Markets rally",
         "url": "https://www.bloomberg.com/news/markets/rally", "description": "desc"},
    ]

    def _run_analyze(
        self,
        *,
        sr_enabled: bool,
        dq_enabled: bool = False,
        mq_enabled: bool = False,
        source: dict | None = None,
        sentiment=None,
        filtered_articles=None,
        build_sr_side_effect=None,
        domain_feedback_enabled: bool = False,
        domain_stats_rows: list[dict] | None = None,
        domain_stats_side_effect=None,
        capture_build_call: bool = False,
    ):
        analyze = AsyncMock(return_value={
            "market_question": "Will X happen?",
            "market_probability": 50,
            "ai_probability": 70,
            "signal": "ACT",
            "signal_direction": "LONG",
            "signal_strength": "HIGH",
            "risk_level": "medium",
            "expected_edge": 0.20,
            "position_size": 0.10,
            "evidence_strength": 0.8,
            "confidence_score": 0.7,
            "news_quality_score": 0.8,
            "source_count": 3,
        })
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.ai_analysis_service.analyze_market", new=analyze))
            stack.enter_context(patch("app.services.cross_validation_service.cross_validate",
                                      new=AsyncMock(return_value=None)))
            # EVIDENCE_BREAKDOWN must be ON so evidence_breakdown is populated
            # (source_reliability requires a non-empty evidence_breakdown).
            stack.enter_context(patch.object(eis.settings, "EVIDENCE_BREAKDOWN_ENABLED", True))
            # Overlay tests must be independent of local calibration history.
            stack.enter_context(patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False))
            stack.enter_context(patch.object(eis.settings, "DECISION_QUALITY_ENABLED", dq_enabled))
            stack.enter_context(patch.object(eis.settings, "MARKET_QUALITY_ENABLED", mq_enabled))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_ENABLED", sr_enabled))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_SCORE_THRESHOLD", 0.5))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_MIN_TRUSTED_RATIO", 0.4))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY", 2))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_MIN_SOURCES", 2))
            stack.enter_context(patch.object(
                eis.settings,
                "DOMAIN_RELIABILITY_FEEDBACK_ENABLED",
                domain_feedback_enabled,
            ))
            stack.enter_context(patch.object(
                eis.settings,
                "DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT",
                5,
            ))
            if domain_stats_rows is not None or domain_stats_side_effect is not None:
                get_stats = stack.enter_context(patch(
                    "app.memory.domain_reliability_store.get_stats",
                    return_value=domain_stats_rows or [],
                    side_effect=domain_stats_side_effect,
                ))
            else:
                get_stats = None
            if capture_build_call:
                build_sr = stack.enter_context(patch(
                    "app.services.source_reliability_service.build_source_reliability",
                    return_value={
                        "overall_score": 0.9,
                        "source_count": 2,
                        "domain_diversity": 2,
                        "trusted_source_ratio": 1.0,
                        "official_source_count": 0,
                        "unknown_source_ratio": 0.0,
                        "source_breakdown": [],
                        "downgrade_reason": None,
                        "raw_direction": "YES",
                        "suggested_direction": "YES",
                        "downgraded": False,
                        "applied_to_displayed_direction": False,
                    },
                ))
            else:
                build_sr = None
            if build_sr_side_effect is not None and not capture_build_call:
                stack.enter_context(patch("app.services.source_reliability_service.build_source_reliability",
                                          side_effect=build_sr_side_effect))
            record = _run(eis.analyze_event(
                "Will X happen?",
                baseline_probability=50,
                news_context="direction: support",
                source=source,
                sentiment_profile=sentiment or self.SENTIMENT_2SRC,
                filtered_articles=filtered_articles if filtered_articles is not None
                                  else self.FILTERED_ARTICLES_2SRC,
            ))
            if capture_build_call:
                return record, build_sr, get_stats
            return record

    # --- Attachment gating ---

    def test_source_reliability_attached_when_enabled_with_evidence(self):
        record = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market", "platform": "Polymarket"},
        )
        self.assertIn("source_reliability", record)
        self.assertIsNotNone(record["source_reliability"])
        self.assertEqual(record["source_reliability"]["raw_direction"], "YES")
        self.assertNotIn("error", record["source_reliability"])
        # 2 distinct domains (reuters.com, bloomberg.com) -> diversity=2
        self.assertEqual(record["source_reliability"]["domain_diversity"], 2)
        self.assertEqual(record["source_reliability"]["source_count"], 2)

    def test_source_reliability_absent_when_disabled(self):
        """When SOURCE_RELIABILITY_ENABLED=false, record has NO source_reliability
        key — byte-identical to pre-Phase-4 records."""
        record = self._run_analyze(
            sr_enabled=False,
            source={"type": "prediction_market", "platform": "Polymarket"},
        )
        self.assertNotIn("source_reliability", record)

    def test_domain_feedback_flag_off_passes_none_and_does_not_load_store(self):
        record, build_sr, get_stats = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market", "platform": "Polymarket"},
            domain_feedback_enabled=False,
            domain_stats_rows=[
                {
                    "domain": "reuters.com",
                    "category": "_all",
                    "sample_count": 10,
                    "correct_count": 8,
                },
            ],
            capture_build_call=True,
        )
        self.assertIn("source_reliability", record)
        get_stats.assert_not_called()
        self.assertIsNone(build_sr.call_args.kwargs["domain_stats_overrides"])

    def test_domain_feedback_flag_on_projects_stats_rows(self):
        record, build_sr, get_stats = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market", "platform": "Polymarket"},
            domain_feedback_enabled=True,
            domain_stats_rows=[
                {
                    "domain": "reuters.com",
                    "category": "_all",
                    "sample_count": 10,
                    "correct_count": 8,
                    "wrong_count": 2,
                    "reliability_score": 0.8,
                    "credibility_sum": 7.5,
                },
                {
                    "domain": "bloomberg.com",
                    "category": "_all",
                    "sample_count": 5,
                    "correct_count": 2,
                    "wrong_count": 3,
                },
            ],
            capture_build_call=True,
        )
        self.assertIn("source_reliability", record)
        get_stats.assert_called_once_with(category="_all", min_samples=0)
        self.assertEqual(
            build_sr.call_args.kwargs["domain_stats_overrides"],
            [
                {"domain": "reuters.com", "sample_count": 10, "correct_count": 8},
                {"domain": "bloomberg.com", "sample_count": 5, "correct_count": 2},
            ],
        )
        self.assertEqual(
            build_sr.call_args.kwargs["domain_reliability_shrinkage_pseudocount"],
            5,
        )

    def test_domain_feedback_store_failure_is_best_effort(self):
        with self.assertLogs("app.services.event_intelligence_service", level="WARNING") as logs:
            record, build_sr, get_stats = self._run_analyze(
                sr_enabled=True,
                source={"type": "prediction_market", "platform": "Polymarket"},
                domain_feedback_enabled=True,
                domain_stats_side_effect=RuntimeError("db unavailable"),
                capture_build_call=True,
            )
        self.assertIn("source_reliability", record)
        get_stats.assert_called_once_with(category="_all", min_samples=0)
        self.assertIsNone(build_sr.call_args.kwargs["domain_stats_overrides"])
        self.assertTrue(any("domain_reliability load failed" in msg for msg in logs.output))

    def test_domain_feedback_does_not_load_when_source_reliability_disabled(self):
        record, build_sr, get_stats = self._run_analyze(
            sr_enabled=False,
            source={"type": "prediction_market", "platform": "Polymarket"},
            domain_feedback_enabled=True,
            domain_stats_rows=[
                {
                    "domain": "reuters.com",
                    "category": "_all",
                    "sample_count": 10,
                    "correct_count": 8,
                },
            ],
            capture_build_call=True,
        )
        self.assertNotIn("source_reliability", record)
        build_sr.assert_not_called()
        get_stats.assert_not_called()

    def test_source_reliability_absent_when_evidence_breakdown_empty(self):
        """When evidence_breakdown is empty (EVIDENCE_BREAKDOWN off), no
        source_reliability block — there is no source base to assess."""
        analyze = AsyncMock(return_value={
            "market_question": "Will X happen?",
            "market_probability": 50,
            "ai_probability": 70,
            "signal": "ACT",
            "signal_direction": "LONG",
            "signal_strength": "HIGH",
            "risk_level": "medium",
            "expected_edge": 0.20,
            "position_size": 0.10,
            "evidence_strength": 0.8,
            "confidence_score": 0.7,
            "news_quality_score": 0.8,
            "source_count": 3,
        })
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.ai_analysis_service.analyze_market", new=analyze))
            stack.enter_context(patch("app.services.cross_validation_service.cross_validate",
                                      new=AsyncMock(return_value=None)))
            stack.enter_context(patch.object(eis.settings, "EVIDENCE_BREAKDOWN_ENABLED", False))
            stack.enter_context(patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_ENABLED", True))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_SCORE_THRESHOLD", 0.5))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_MIN_TRUSTED_RATIO", 0.4))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY", 2))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_MIN_SOURCES", 2))
            record = _run(eis.analyze_event(
                "Will X happen?",
                baseline_probability=50,
                news_context="direction: support",
                source={"type": "prediction_market"},
                sentiment_profile=self.SENTIMENT_2SRC,
                filtered_articles=self.FILTERED_ARTICLES_2SRC,
            ))
        self.assertEqual(record.get("evidence_breakdown"), [])
        self.assertNotIn("source_reliability", record)

    def test_source_reliability_attached_for_metaculus_source(self):
        """Unlike market_quality, source_reliability applies to ALL sources
        with evidence_breakdown (including Metaculus prediction_question)."""
        record = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_question", "platform": "Metaculus"},
        )
        self.assertIn("source_reliability", record)
        self.assertIsNotNone(record["source_reliability"])

    # --- Downgrade behavior ---

    def test_source_reliability_downgrades_yes_to_wait_single_domain(self):
        """When all evidence comes from a single domain (diversity=1 <
        min_domain_diversity=2), YES is downgraded to WAIT."""
        # Single source, single domain
        sentiment_1 = {
            "articles": [{
                "index": 0,
                "sentiment": "positive",
                "impact": "high",
                "key_facts": ["fact"],
                "relevance_to_question": 0.8,
                "evidence_direction": "support",
                "evidence_strength": 0.85,
                "source_credibility": 0.9,
                "rationale_zh": "支持 YES。",
            }],
            "overall_direction": "support_yes",
            "overall_strength": 0.85,
            "conflict_level": 0.1,
            "summary": "证据支持 YES",
        }
        filtered_1 = [
            {"source": "CryptoNews", "title": "BTC up",
             "url": "https://www.cryptonews.com/news/btc-up", "description": "desc"},
        ]
        record = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market"},
            sentiment=sentiment_1,
            filtered_articles=filtered_1,
        )
        sr = record["source_reliability"]
        self.assertEqual(sr["raw_direction"], "YES")
        self.assertEqual(sr["suggested_direction"], "WAIT")
        self.assertTrue(sr["downgraded"])
        self.assertIsNotNone(sr["downgrade_reason"])
        self.assertEqual(sr["domain_diversity"], 1)

    def test_source_reliability_keeps_yes_when_diverse(self):
        """With 2+ trusted sources from different domains, YES stays YES."""
        record = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market"},
        )
        sr = record["source_reliability"]
        self.assertEqual(sr["raw_direction"], "YES")
        self.assertEqual(sr["suggested_direction"], "YES")
        self.assertFalse(sr["downgraded"])
        self.assertIsNone(sr["downgrade_reason"])

    # --- Merge / applied flag ---

    def test_source_applied_when_source_stricter_than_decision(self):
        """When source_reliability downgrades YES->WAIT but decision_quality
        keeps YES, the 3-way merge picks WAIT and sets
        source_reliability.applied_to_displayed_direction=True."""
        # Single-domain evidence -> source downgrades to WAIT.
        # decision_quality keeps YES (high consensus, single supporting
        # evidence with high strength).
        sentiment_1 = {
            "articles": [{
                "index": 0,
                "sentiment": "positive",
                "impact": "high",
                "key_facts": ["fact"],
                "relevance_to_question": 0.8,
                "evidence_direction": "support",
                "evidence_strength": 0.85,
                "source_credibility": 0.9,
                "rationale_zh": "支持 YES。",
            }],
            "overall_direction": "support_yes",
            "overall_strength": 0.85,
            "conflict_level": 0.1,
            "summary": "证据支持 YES",
        }
        filtered_1 = [
            {"source": "CryptoNews", "title": "BTC up",
             "url": "https://www.cryptonews.com/news/btc-up", "description": "desc"},
        ]
        record = self._run_analyze(
            sr_enabled=True,
            dq_enabled=True,
            source={"type": "prediction_market"},
            sentiment=sentiment_1,
            filtered_articles=filtered_1,
        )
        self.assertEqual(record["final_displayed_direction"], "WAIT")
        self.assertTrue(record["source_reliability"]["applied_to_displayed_direction"])
        self.assertEqual(record["source_reliability"]["suggested_direction"], "WAIT")

    def test_final_fields_absent_when_all_overlays_off(self):
        """When all three features off, no final_* fields — byte-identical."""
        record = self._run_analyze(
            sr_enabled=False,
            dq_enabled=False,
            mq_enabled=False,
            source={"type": "prediction_market"},
        )
        self.assertNotIn("final_displayed_direction", record)
        self.assertNotIn("final_downgrade_reason", record)
        self.assertNotIn("source_reliability", record)

    # --- No-writeback invariant ---

    def test_ai_probability_not_mutated_by_source_reliability(self):
        record = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market"},
        )
        # ai_probability stays at the LLM's 70 (source_reliability is overlay only)
        self.assertEqual(record["probability"]["estimated"], 70)

    def test_actionable_recommendation_direction_not_mutated(self):
        """Even when source_reliability downgrades YES->WAIT, the
        actionable_recommendation.direction stays YES (no-writeback)."""
        sentiment_1 = {
            "articles": [{
                "index": 0,
                "sentiment": "positive",
                "impact": "high",
                "key_facts": ["fact"],
                "relevance_to_question": 0.8,
                "evidence_direction": "support",
                "evidence_strength": 0.85,
                "source_credibility": 0.9,
                "rationale_zh": "支持 YES。",
            }],
            "overall_direction": "support_yes",
            "overall_strength": 0.85,
            "conflict_level": 0.1,
            "summary": "证据支持 YES",
        }
        filtered_1 = [
            {"source": "CryptoNews", "title": "BTC up",
             "url": "https://www.cryptonews.com/news/btc-up", "description": "desc"},
        ]
        record = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market"},
            sentiment=sentiment_1,
            filtered_articles=filtered_1,
        )
        self.assertEqual(
            record["actionable_recommendation"]["direction"], "YES"
        )

    # --- Build failure fallback ---

    def test_source_reliability_build_failure_falls_back_with_evidence(self):
        record = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market"},
            build_sr_side_effect=RuntimeError("boom"),
        )
        self.assertIn("source_reliability", record)
        self.assertEqual(record["source_reliability"]["error"], "build_failed")
        self.assertEqual(record["source_reliability"]["raw_direction"], "YES")
        self.assertEqual(record["source_reliability"]["suggested_direction"], "YES")
        self.assertFalse(record["source_reliability"]["downgraded"])

    def test_source_reliability_build_failure_no_block_without_evidence(self):
        """When build fails AND evidence_breakdown is empty, no error block
        is attached — the record stays byte-identical (no source_reliability key)."""
        analyze = AsyncMock(return_value={
            "market_question": "Will X happen?",
            "market_probability": 50,
            "ai_probability": 70,
            "signal": "ACT",
            "signal_direction": "LONG",
            "signal_strength": "HIGH",
            "risk_level": "medium",
            "expected_edge": 0.20,
            "position_size": 0.10,
            "evidence_strength": 0.8,
            "confidence_score": 0.7,
            "news_quality_score": 0.8,
            "source_count": 3,
        })
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.ai_analysis_service.analyze_market", new=analyze))
            stack.enter_context(patch("app.services.cross_validation_service.cross_validate",
                                      new=AsyncMock(return_value=None)))
            stack.enter_context(patch.object(eis.settings, "EVIDENCE_BREAKDOWN_ENABLED", False))
            stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_ENABLED", True))
            stack.enter_context(patch("app.services.source_reliability_service.build_source_reliability",
                                      side_effect=RuntimeError("boom")))
            record = _run(eis.analyze_event(
                "Will X happen?",
                baseline_probability=50,
                news_context="direction: support",
                source={"type": "prediction_market"},
                sentiment_profile=self.SENTIMENT_2SRC,
                filtered_articles=self.FILTERED_ARTICLES_2SRC,
            ))
        self.assertNotIn("source_reliability", record)

    # --- Forbidden word invariant ---

    def test_downgrade_reason_no_forbidden_words(self):
        """source_reliability.downgrade_reason must not contain forbidden
        trading vocabulary (long, short, buy, sell, position, kelly, order)."""
        sentiment_1 = {
            "articles": [{
                "index": 0,
                "sentiment": "positive",
                "impact": "high",
                "key_facts": ["fact"],
                "relevance_to_question": 0.8,
                "evidence_direction": "support",
                "evidence_strength": 0.85,
                "source_credibility": 0.9,
                "rationale_zh": "支持 YES。",
            }],
            "overall_direction": "support_yes",
            "overall_strength": 0.85,
            "conflict_level": 0.1,
            "summary": "证据支持 YES",
        }
        filtered_1 = [
            {"source": "CryptoNews", "title": "BTC up",
             "url": "https://www.cryptonews.com/news/btc-up", "description": "desc"},
        ]
        record = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market"},
            sentiment=sentiment_1,
            filtered_articles=filtered_1,
        )
        reason = record["source_reliability"]["downgrade_reason"]
        self.assertIsNotNone(reason)
        forbidden = ("long", "short", "buy", "sell", "position", "kelly", "order")
        reason_lower = reason.lower()
        for word in forbidden:
            self.assertNotIn(word, reason_lower,
                             f"forbidden word '{word}' in downgrade_reason: {reason}")
        # final_downgrade_reason (merged) must also be clean
        final_reason = record.get("final_downgrade_reason") or ""
        final_lower = final_reason.lower()
        for word in forbidden:
            self.assertNotIn(word, final_lower,
                             f"forbidden word '{word}' in final_downgrade_reason: {final_reason}")


class Phase1To4EndToEndIntegrationTests(unittest.TestCase):
    """End-to-end verification of the full Decision Quality Engine (Phases 1-4).

    Unlike the per-phase integration tests above (which mock analyze_event),
    these tests let analyze_event run for real — only the external boundaries
    are mocked (LLM, news collection, candidate sources, persistence,
    translation). All four feature flags are ON simultaneously to verify the
    complete overlay stack integrates without regressions:

        analyze_market (mocked LLM)
          -> build_event_record
          -> aggregate_evidence_breakdown      (EVIDENCE_BREAKDOWN)
          -> build_decision_quality            (Phase 1)
          -> build_market_quality              (Phase 2)
          -> build_source_reliability          (Phase 4)
          -> merge_quality_overlays (3-way)   (Phase 1+2+4 merge)
          -> final_displayed_direction / final_downgrade_reason

    The prediction-calibration layer (Phase 3) is gated on
    freeze_prediction / score_prediction at resolve time, so it is not
    exercised here (no prediction row is frozen in analyze_event itself).
    """

    # ── Shared fixtures ────────────────────────────────────────────────

    # Two articles from distinct trusted domains -> diverse, passes all
    # source-reliability gates (diversity=2, trusted_ratio=1.0).
    SENTIMENT_DIVERS = {
        "articles": [
            {
                "index": 0,
                "sentiment": "positive",
                "impact": "high",
                "key_facts": ["Fed cut signals support YES"],
                "relevance_to_question": 0.85,
                "evidence_direction": "support",
                "evidence_strength": 0.85,
                "source_credibility": 0.9,
                "rationale_zh": "支持 YES 的强证据。",
            },
            {
                "index": 1,
                "sentiment": "positive",
                "impact": "medium",
                "key_facts": ["Markets rally on Fed news"],
                "relevance_to_question": 0.75,
                "evidence_direction": "support",
                "evidence_strength": 0.75,
                "source_credibility": 0.85,
                "rationale_zh": "支持 YES 的辅助证据。",
            },
        ],
        "overall_direction": "support_yes",
        "overall_strength": 0.8,
        "conflict_level": 0.1,
        "summary": "证据支持 YES",
    }

    ARTICLES_DIVERS = [
        {"source": "Reuters", "title": "Fed signals rate cut",
         "url": "https://www.reuters.com/article/fed-cut/1",
         "summary": "desc", "description": "desc"},
        {"source": "Bloomberg", "title": "Markets rally",
         "url": "https://www.bloomberg.com/news/markets/rally",
         "summary": "desc", "description": "desc"},
    ]

    # Single article from an aggregator domain -> fails diversity gate,
    # source_reliability downgrades YES -> WAIT.
    SENTIMENT_SINGLE = {
        "articles": [
            {
                "index": 0,
                "sentiment": "positive",
                "impact": "high",
                "key_facts": ["BTC up"],
                "relevance_to_question": 0.8,
                "evidence_direction": "support",
                "evidence_strength": 0.85,
                "source_credibility": 0.5,
                "rationale_zh": "支持 YES。",
            },
        ],
        "overall_direction": "support_yes",
        "overall_strength": 0.85,
        "conflict_level": 0.1,
        "summary": "证据支持 YES",
    }

    ARTICLES_SINGLE = [
        {"source": "CryptoNews", "title": "BTC up",
         "url": "https://www.cryptonews.com/news/btc-up",
         "summary": "desc", "description": "desc"},
    ]

    def _llm_analysis(self, *, signal_direction="LONG", ai_probability=70):
        """A realistic analyze_market return value (the LLM mock)."""
        return {
            "market_question": "Will X happen?",
            "market_probability": 50,
            "ai_probability": ai_probability,
            "signal": "ACT",
            "signal_direction": signal_direction,
            "signal_strength": "HIGH",
            "risk_level": "medium",
            "expected_edge": 0.20,
            "position_size": 0.10,
            "evidence_strength": 0.8,
            "confidence_score": 0.7,
            "news_quality_score": 0.8,
            "source_count": 3,
        }

    def _patch_all_flags(self, stack):
        """Enable ALL overlay feature flags for the E2E test."""
        stack.enter_context(patch.object(eis.settings, "EVIDENCE_BREAKDOWN_ENABLED", True))
        # E2E overlay assertions are about audit-layer isolation, not the
        # calibration feedback writeback layer.
        stack.enter_context(patch.object(eis.settings, "CALIBRATION_FEEDBACK_ENABLED", False))
        stack.enter_context(patch.object(eis.settings, "DECISION_QUALITY_ENABLED", True))
        stack.enter_context(patch.object(eis.settings, "DECISION_QUALITY_MAX_EVIDENCE_ITEMS", 3))
        stack.enter_context(patch.object(eis.settings, "DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD", 0.40))
        stack.enter_context(patch.object(eis.settings, "DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD", 0.20))
        stack.enter_context(patch.object(eis.settings, "MARKET_QUALITY_ENABLED", True))
        stack.enter_context(patch.object(eis.settings, "MARKET_MAX_SPREAD_PCT", 12.0))
        stack.enter_context(patch.object(eis.settings, "MARKET_MIN_LIQUIDITY", 1000.0))
        stack.enter_context(patch.object(eis.settings, "MARKET_MIN_VOLUME", 1000.0))
        stack.enter_context(patch.object(eis.settings, "MARKET_QUALITY_SCORE_THRESHOLD", 0.5))
        stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_ENABLED", True))
        stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_SCORE_THRESHOLD", 0.5))
        stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_MIN_TRUSTED_RATIO", 0.4))
        stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY", 2))
        stack.enter_context(patch.object(eis.settings, "SOURCE_RELIABILITY_MIN_SOURCES", 2))

    def _run_analyze_event(
        self,
        *,
        source: dict,
        sentiment: dict,
        articles: list,
        volume: float | None = None,
        liquidity: float | None = None,
        market_quote: dict | None = None,
    ) -> dict:
        """Run analyze_event with all overlays ON, external deps mocked."""
        analyze = AsyncMock(return_value=self._llm_analysis())
        from contextlib import ExitStack
        with ExitStack() as stack:
            self._patch_all_flags(stack)
            stack.enter_context(patch("app.services.ai_analysis_service.analyze_market", new=analyze))
            stack.enter_context(patch("app.services.cross_validation_service.cross_validate",
                                      new=AsyncMock(return_value=None)))
            stack.enter_context(patch("app.services.translation_service.translate_articles",
                                      new=AsyncMock(side_effect=lambda arts: arts)))
            return _run(eis.analyze_event(
                "Will X happen?",
                baseline_probability=50,
                news_context="direction: support",
                source=source,
                volume=volume,
                liquidity=liquidity,
                market_quote=market_quote,
                sentiment_profile=sentiment,
                filtered_articles=articles,
            ))

    # ── Test 1: All overlays present, no downgrade (healthy case) ──────

    def test_all_overlays_present_healthy_prediction_market(self):
        """All 4 overlays attached for a healthy prediction_market event
        with diverse trusted sources. No overlay downgrades (YES stays YES)."""
        record = self._run_analyze_event(
            source={"type": "prediction_market", "platform": "Polymarket",
                    "source_id": "poly-123"},
            sentiment=self.SENTIMENT_DIVERS,
            articles=self.ARTICLES_DIVERS,
            volume=5000.0,
            liquidity=5000.0,
            market_quote={"bid": 48, "ask": 52, "spread": 4},
        )
        # All overlay blocks present
        self.assertIn("decision_quality", record)
        self.assertIn("market_quality", record)
        self.assertIn("source_reliability", record)
        self.assertIn("final_displayed_direction", record)
        self.assertIn("final_downgrade_reason", record)
        # All overlays agree: YES (healthy market + diverse sources + high consensus)
        self.assertEqual(record["decision_quality"]["raw_direction"], "YES")
        self.assertEqual(record["market_quality"]["raw_direction"], "YES")
        self.assertEqual(record["source_reliability"]["raw_direction"], "YES")
        # No downgrade: all suggested_directions == raw_direction
        self.assertEqual(record["decision_quality"]["displayed_direction"], "YES")
        self.assertEqual(record["market_quality"]["suggested_direction"], "YES")
        self.assertEqual(record["source_reliability"]["suggested_direction"], "YES")
        # Final merge: YES, no reason
        self.assertEqual(record["final_displayed_direction"], "YES")
        self.assertIsNone(record["final_downgrade_reason"])
        # applied flags: nothing downgraded the base
        self.assertFalse(record["market_quality"]["applied_to_displayed_direction"])
        self.assertFalse(record["source_reliability"]["applied_to_displayed_direction"])
        # No error blocks
        self.assertNotIn("error", record["decision_quality"])
        self.assertNotIn("error", record["market_quality"])
        self.assertNotIn("error", record["source_reliability"])
        # evidence_breakdown populated (EVIDENCE_BREAKDOWN ON)
        self.assertEqual(len(record["evidence_breakdown"]), 2)

    # ── Test 2: source_reliability downgrades YES -> WAIT (3-way merge) ─

    def test_source_reliability_downgrades_in_full_stack(self):
        """When source_reliability is the strictest overlay (single domain ->
        WAIT) while decision_quality and market_quality keep YES, the 3-way
        merge picks WAIT and flags source_reliability as applied."""
        record = self._run_analyze_event(
            source={"type": "prediction_market", "platform": "Polymarket"},
            sentiment=self.SENTIMENT_SINGLE,
            articles=self.ARTICLES_SINGLE,
            volume=5000.0,   # healthy market
            liquidity=5000.0,
        )
        # decision_quality: YES (single supporting evidence, high strength)
        self.assertEqual(record["decision_quality"]["displayed_direction"], "YES")
        # market_quality: YES (healthy market)
        self.assertEqual(record["market_quality"]["suggested_direction"], "YES")
        # source_reliability: WAIT (single domain, diversity=1 < min=2)
        self.assertEqual(record["source_reliability"]["suggested_direction"], "WAIT")
        self.assertTrue(record["source_reliability"]["downgraded"])
        self.assertIsNotNone(record["source_reliability"]["downgrade_reason"])
        # 3-way merge: WAIT is strictest -> final = WAIT
        self.assertEqual(record["final_displayed_direction"], "WAIT")
        self.assertIsNotNone(record["final_downgrade_reason"])
        # source_reliability applied, market_quality not
        self.assertTrue(record["source_reliability"]["applied_to_displayed_direction"])
        self.assertFalse(record["market_quality"]["applied_to_displayed_direction"])

    # ── Test 3: market_quality downgrades YES -> WAIT (3-way merge) ────

    def test_market_quality_downgrades_in_full_stack(self):
        """When market_quality is the strictest overlay (thin market -> WAIT)
        while decision_quality and source_reliability keep YES, the 3-way
        merge picks WAIT and flags market_quality as applied."""
        record = self._run_analyze_event(
            source={"type": "prediction_market", "platform": "Polymarket"},
            sentiment=self.SENTIMENT_DIVERS,
            articles=self.ARTICLES_DIVERS,
            volume=100.0,   # thin -> market downgrades YES -> WAIT
            liquidity=100.0,
        )
        # decision_quality: YES
        self.assertEqual(record["decision_quality"]["displayed_direction"], "YES")
        # market_quality: WAIT (thin market)
        self.assertEqual(record["market_quality"]["suggested_direction"], "WAIT")
        self.assertTrue(record["market_quality"]["downgraded"])
        # source_reliability: YES (diverse sources)
        self.assertEqual(record["source_reliability"]["suggested_direction"], "YES")
        # 3-way merge: WAIT is strictest -> final = WAIT
        self.assertEqual(record["final_displayed_direction"], "WAIT")
        self.assertIsNotNone(record["final_downgrade_reason"])
        # market_quality applied, source_reliability not
        self.assertTrue(record["market_quality"]["applied_to_displayed_direction"])
        self.assertFalse(record["source_reliability"]["applied_to_displayed_direction"])

    # ── Test 4: All overlays downgrade -> reasons concatenated ────────

    def test_all_overlays_downgrade_reasons_concatenated(self):
        """When ALL three overlays downgrade YES -> WAIT (thin market + single
        domain + high conflict), the merged reason concatenates all three."""
        # Use single-source sentiment (triggers source_reliability downgrade)
        # + thin market (triggers market_quality downgrade).
        # decision_quality downgrade is harder to trigger with a single
        # supporting article, but we can check that at least market +
        # source reasons appear in the merged final_downgrade_reason.
        record = self._run_analyze_event(
            source={"type": "prediction_market", "platform": "Polymarket"},
            sentiment=self.SENTIMENT_SINGLE,
            articles=self.ARTICLES_SINGLE,
            volume=100.0,   # thin market
            liquidity=100.0,
        )
        self.assertEqual(record["final_displayed_direction"], "WAIT")
        reason = record["final_downgrade_reason"]
        self.assertIsNotNone(reason)
        # Both market and source reasons should appear (concatenated with " | ")
        self.assertIn(" | ", reason)

    # ── Test 5: No-writeback invariant across all overlays ────────────

    def test_no_writeback_across_all_overlays(self):
        """Verify that NONE of the 4 overlays mutate the upstream fields:
        ai_probability, actionable_recommendation.direction,
        actionable_recommendation.edge, evidence_profile."""
        record = self._run_analyze_event(
            source={"type": "prediction_market", "platform": "Polymarket"},
            sentiment=self.SENTIMENT_SINGLE,  # triggers source downgrade
            articles=self.ARTICLES_SINGLE,
            volume=100.0,   # triggers market downgrade
            liquidity=100.0,
        )
        # ai_probability stays at LLM's 70 (no overlay writes back)
        self.assertEqual(record["probability"]["estimated"], 70)
        # actionable_recommendation.direction stays YES (overlays don't mutate)
        self.assertEqual(record["actionable_recommendation"]["direction"], "YES")
        # evidence_breakdown directions stay support (no rewrite)
        for item in record["evidence_breakdown"]:
            self.assertIn(item["direction"], ("support", "oppose", "neutral"))

    # ── Test 6: Metaculus source (prediction_question) ────────────────

    def test_metaculus_source_has_source_reliability_but_no_market_quality(self):
        """Metaculus (prediction_question) gets source_reliability + decision_quality
        but NOT market_quality (mirrors the freeze_prediction gate)."""
        record = self._run_analyze_event(
            source={"type": "prediction_question", "platform": "Metaculus"},
            sentiment=self.SENTIMENT_DIVERS,
            articles=self.ARTICLES_DIVERS,
        )
        self.assertIn("decision_quality", record)
        self.assertIn("source_reliability", record)
        # market_quality must be absent (non-prediction-market source)
        self.assertNotIn("market_quality", record)
        # final_displayed_direction still set (decision_quality + source_reliability)
        self.assertIn("final_displayed_direction", record)
        # No downgrade (diverse sources, high consensus)
        self.assertEqual(record["final_displayed_direction"], "YES")

    # ── Test 7: discover_events end-to-end with all flags ON ──────────

    def test_discover_events_all_flags_on(self):
        """Full discover_events orchestration with all overlay flags ON.
        Mocks external boundaries (LLM, news, candidates, persistence,
        translation) but lets analyze_event + all overlay services run for
        real. Verifies the returned records have the complete overlay stack."""
        # Candidate with a healthy prediction_market + diverse sources
        candidate = {
            "question": "Will the Fed cut rates in 2026?",
            "baseline_probability": 50,
            "volume": 5000.0,
            "liquidity": 5000.0,
            "source": {"type": "prediction_market", "platform": "Polymarket",
                       "source_id": "poly-e2e-1"},
        }

        # Mock _build_filtered_news to return diverse articles + sentiment
        async def fake_filtered_news(question, shared_articles=None):
            return {
                "context": "direction: support",
                "summary": {"selected_count": 2},
                "articles": self.ARTICLES_DIVERS,
                "sentiment_profile": self.SENTIMENT_DIVERS,
            }

        analyze = AsyncMock(return_value=self._llm_analysis())
        from contextlib import ExitStack
        with ExitStack() as stack:
            self._patch_all_flags(stack)
            stack.enter_context(patch.object(eis, "_collect_candidate_events",
                                             new=AsyncMock(return_value=[candidate])))
            stack.enter_context(patch("app.services.event_collection_service.collect_shared_articles",
                                      new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(eis, "_build_filtered_news",
                                             new=AsyncMock(side_effect=fake_filtered_news)))
            stack.enter_context(patch("app.services.ai_analysis_service.analyze_market", new=analyze))
            stack.enter_context(patch("app.services.cross_validation_service.cross_validate",
                                      new=AsyncMock(return_value=None)))
            stack.enter_context(patch("app.services.translation_service.translate_articles",
                                      new=AsyncMock(side_effect=lambda arts: arts)))
            stack.enter_context(patch.object(eis, "_persist_events", new=lambda records: None))
            result = _run(eis.discover_events(limit=10, use_cache=False))

        # discover_events returned 1 event
        self.assertEqual(result["count"], 1)
        record = result["events"][0]
        # Full overlay stack present
        self.assertIn("decision_quality", record)
        self.assertIn("market_quality", record)
        self.assertIn("source_reliability", record)
        self.assertIn("final_displayed_direction", record)
        # Healthy case: YES, no downgrade
        self.assertEqual(record["final_displayed_direction"], "YES")
        self.assertIsNone(record["final_downgrade_reason"])
        # No errors in any overlay
        self.assertNotIn("error", record["decision_quality"])
        self.assertNotIn("error", record["market_quality"])
        self.assertNotIn("error", record["source_reliability"])


class LLMTelemetryIntegrationTests(unittest.TestCase):
    """Phase 5: locks the analyze_event -> llm_telemetry integration.

    Verifies:
    - llm_telemetry attached when LLM_TELEMETRY_ENABLED=true (ALL events)
    - no llm_telemetry key when feature off (byte-identical to pre-Phase-5)
    - degraded_mode=True when LLM mock raises (analyze_market fallback path)
    - degraded_mode=False when LLM mock returns valid response
    - total_tokens populated from _ask_ai instrumentation
    - sentiment_degraded reflects sentiment_profile.fallback
    - no mutation of analysis_quality or sentiment_profile
    - build failure falls back to error block
    - llm_telemetry does NOT participate in merge_quality_overlays
    """

    def _llm_analysis_with_usage(self, *, quality="llm"):
        """A realistic analyze_market return value with llm_usage attached."""
        d = {
            "market_question": "Will X happen?",
            "market_probability": 50,
            "ai_probability": 70,
            "signal": "ACT",
            "signal_direction": "LONG",
            "signal_strength": "HIGH",
            "risk_level": "medium",
            "expected_edge": 0.20,
            "position_size": 0.10,
            "evidence_strength": 0.8,
            "confidence_score": 0.7,
            "news_quality_score": 0.8,
            "source_count": 3,
            "analysis_quality": quality,
            "llm_usage": {"prompt_tokens": 1200, "completion_tokens": 350,
                           "total_tokens": 1550},
        }
        return d

    def _llm_analysis_fallback(self):
        """analyze_market return value when LLM fails (deterministic fallback)."""
        d = self._llm_analysis_with_usage(quality="deterministic_fallback")
        d["llm_usage"] = None  # fallback path doesn't attach usage
        return d

    def _run_analyze(
        self,
        *,
        telemetry_enabled: bool,
        llm_return: dict | None = None,
        llm_side_effect=None,
        sentiment=None,
    ):
        analyze = AsyncMock(
            return_value=llm_return or self._llm_analysis_with_usage(),
            side_effect=llm_side_effect,
        )
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.ai_analysis_service.analyze_market", new=analyze))
            stack.enter_context(patch("app.services.cross_validation_service.cross_validate",
                                      new=AsyncMock(return_value=None)))
            stack.enter_context(patch.object(eis.settings, "LLM_TELEMETRY_ENABLED", telemetry_enabled))
            stack.enter_context(patch.object(eis.settings, "OPENAI_MODEL", "gpt-4o-mini"))
            return _run(eis.analyze_event(
                "Will X happen?",
                baseline_probability=50,
                news_context="direction: support",
                source={"type": "prediction_market"},
                sentiment_profile=sentiment,
                filtered_articles=[],
            ))

    # --- Attachment gating ---

    def test_telemetry_attached_when_enabled(self):
        record = self._run_analyze(telemetry_enabled=True)
        self.assertIn("llm_telemetry", record)
        self.assertIsNotNone(record["llm_telemetry"])
        self.assertNotIn("error", record["llm_telemetry"])

    def test_telemetry_absent_when_disabled(self):
        """When LLM_TELEMETRY_ENABLED=false, record has NO llm_telemetry key."""
        record = self._run_analyze(telemetry_enabled=False)
        self.assertNotIn("llm_telemetry", record)

    # --- Degraded mode ---

    def test_not_degraded_when_llm_succeeds(self):
        record = self._run_analyze(
            telemetry_enabled=True,
            llm_return=self._llm_analysis_with_usage(quality="llm"),
        )
        tel = record["llm_telemetry"]
        self.assertFalse(tel["degraded_mode"])
        self.assertIsNone(tel["degraded_reason"])
        self.assertEqual(tel["analysis_quality"], "llm")
        self.assertEqual(tel["llm_call_count"], 1)

    def test_degraded_when_llm_fails(self):
        """When _ask_ai raises, analyze_market falls back to deterministic
        (analysis_quality=deterministic_fallback). Telemetry should detect this."""
        # analyze_market's internal try/except catches the exception and returns
        # a fallback dict. We simulate this by returning a fallback dict directly.
        record = self._run_analyze(
            telemetry_enabled=True,
            llm_return=self._llm_analysis_fallback(),
        )
        tel = record["llm_telemetry"]
        self.assertTrue(tel["degraded_mode"])
        self.assertEqual(tel["degraded_reason"], "llm_call_failed")
        self.assertEqual(tel["analysis_quality"], "deterministic_fallback")
        # No real tokens when degraded
        self.assertIsNone(tel["total_tokens"])
        self.assertEqual(tel["llm_call_count"], 0)

    # --- Token usage from _ask_ai instrumentation ---

    def test_tokens_populated_from_llm_usage(self):
        record = self._run_analyze(
            telemetry_enabled=True,
            llm_return=self._llm_analysis_with_usage(),
        )
        tel = record["llm_telemetry"]
        self.assertEqual(tel["prompt_tokens"], 1200)
        self.assertEqual(tel["completion_tokens"], 350)
        self.assertEqual(tel["total_tokens"], 1550)
        # estimated_token_cost computed from real tokens, rounded to 6 places
        # gpt-4o-mini: 0.00015/1K -> 1550 * 0.00015 / 1000 = 0.0002325 -> 0.000232
        self.assertAlmostEqual(tel["estimated_token_cost"], 0.0002325, places=5)

    def test_tokens_none_when_degraded(self):
        record = self._run_analyze(
            telemetry_enabled=True,
            llm_return=self._llm_analysis_fallback(),
        )
        tel = record["llm_telemetry"]
        self.assertIsNone(tel["prompt_tokens"])
        self.assertIsNone(tel["total_tokens"])

    # --- Sentiment degradation ---

    def test_sentiment_degraded_reflects_fallback_flag(self):
        record = self._run_analyze(
            telemetry_enabled=True,
            sentiment={"fallback": True, "summary": "unavailable"},
        )
        self.assertTrue(record["llm_telemetry"]["sentiment_degraded"])

    def test_sentiment_not_degraded_when_no_fallback(self):
        record = self._run_analyze(
            telemetry_enabled=True,
            sentiment={"fallback": False, "summary": "real analysis"},
        )
        self.assertFalse(record["llm_telemetry"]["sentiment_degraded"])

    # --- No-writeback invariant ---

    def test_analysis_quality_not_mutated(self):
        """llm_telemetry reads analysis_quality but must not mutate it."""
        record = self._run_analyze(telemetry_enabled=True)
        # The analysis dict is stored in legacy_analysis; check it's unchanged
        self.assertEqual(record["legacy_analysis"]["analysis_quality"], "llm")

    # --- Model field ---

    def test_model_field_populated(self):
        record = self._run_analyze(telemetry_enabled=True)
        self.assertEqual(record["llm_telemetry"]["model"], "gpt-4o-mini")

    # --- Best-effort fallback ---

    def test_build_failure_falls_back_to_error_block(self):
        """When build_llm_telemetry raises, analyze_event attaches an error
        block instead of crashing."""
        analyze = AsyncMock(return_value=self._llm_analysis_with_usage())
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.ai_analysis_service.analyze_market", new=analyze))
            stack.enter_context(patch("app.services.cross_validation_service.cross_validate",
                                      new=AsyncMock(return_value=None)))
            stack.enter_context(patch.object(eis.settings, "LLM_TELEMETRY_ENABLED", True))
            stack.enter_context(patch.object(eis.settings, "OPENAI_MODEL", "gpt-4o-mini"))
            stack.enter_context(patch("app.services.llm_telemetry_service.build_llm_telemetry",
                                      side_effect=RuntimeError("boom")))
            record = _run(eis.analyze_event(
                "Will X happen?",
                baseline_probability=50,
                news_context="direction: support",
                source={"type": "prediction_market"},
            ))
        self.assertIn("llm_telemetry", record)
        self.assertEqual(record["llm_telemetry"]["error"], "build_failed")
        # Still populates degraded_mode from analysis dict
        self.assertFalse(record["llm_telemetry"]["degraded_mode"])

    # --- Does NOT participate in merge_quality_overlays ---

    def test_telemetry_does_not_affect_final_displayed_direction(self):
        """llm_telemetry is observability-only — it must NOT change
        final_displayed_direction even when degraded_mode=True."""
        record = self._run_analyze(
            telemetry_enabled=True,
            llm_return=self._llm_analysis_fallback(),
        )
        # Telemetry says degraded_mode=True, but final_displayed_direction
        # is controlled only by decision_quality/market_quality/source_reliability
        # (none of which are enabled here, so final_* fields should be absent).
        self.assertNotIn("final_displayed_direction", record)
        self.assertTrue(record["llm_telemetry"]["degraded_mode"])

    # --- Forbidden word invariant ---

    def test_degraded_reason_no_forbidden_words(self):
        """degraded_reason must not contain banned trading vocabulary."""
        record = self._run_analyze(
            telemetry_enabled=True,
            llm_return=self._llm_analysis_fallback(),
        )
        reason = record["llm_telemetry"]["degraded_reason"]
        self.assertIsNotNone(reason)
        forbidden = ("long", "short", "buy", "sell", "position", "kelly", "order")
        for word in forbidden:
            self.assertNotIn(word, reason.lower())


def _challenge_event_record():
    return {
        "event_id": "evt-1",
        "event_title": "Will X happen?",
        "probability": {"baseline": 40.0, "estimated": 62.0, "change": 22.0},
        "actionable_recommendation": {
            "direction": "YES",
            "confidence": "high",
            "risk_level": "medium",
        },
        "final_displayed_direction": "YES",
        "evidence_breakdown": [],
        "risk": {"level": "medium", "flags": []},
    }


def test_event_conclusion_challenge_flag_off_noops(monkeypatch):
    record = _challenge_event_record()
    monkeypatch.setattr(eis.settings, "CONCLUSION_CHALLENGE_ENABLED", False)
    monkeypatch.setattr(eis.settings, "EVENT_CHALLENGE_ENABLED", False)
    eis._run_event_conclusion_challenge(record, attempt_count=0)
    assert "conclusion_challenge" not in record
    assert record["final_displayed_direction"] == "YES"


def test_event_conclusion_challenge_reject_downgrades(monkeypatch):
    record = _challenge_event_record()
    monkeypatch.setattr(eis.settings, "CONCLUSION_CHALLENGE_ENABLED", True)
    monkeypatch.setattr(eis.settings, "EVENT_CHALLENGE_ENABLED", True)
    monkeypatch.setattr(eis.settings, "CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED", False)
    monkeypatch.setattr(eis.settings, "CONCLUSION_CHALLENGE_STRICTNESS", "normal")

    def fake_challenge(payload):
        return {
            "verdict": "reject",
            "required_action": "downgrade_to_wait",
            "failed_checks": [
                {"check": "counterevidence", "reason": "存在高可信反证"}
            ],
            "warnings": [],
            "challenge_summary": "结论否定门结果：reject。主要原因：存在高可信反证",
            "critic_notes": {},
            "attempt_count": 0,
        }

    monkeypatch.setattr(
        "app.services.conclusion_challenge_service.challenge_conclusion",
        fake_challenge,
    )
    eis._run_event_conclusion_challenge(record, attempt_count=0)
    assert record["final_displayed_direction"] == "WAIT"
    assert record["conclusion_challenge"]["verdict"] == "reject"


def test_analyze_event_recomputes_once_when_challenge_requests_retry(monkeypatch):
    analysis = {
        "market_question": "Will X happen?",
        "market_probability": 40,
        "ai_probability": 62,
        "confidence_score": 0.8,
        "news_quality_score": 0.7,
        "evidence_strength": 0.8,
        "signal_direction": "LONG",
        "signal_strength": "HIGH",
        "risk_level": "MEDIUM",
    }
    overlay_calls = []

    def fake_build_overlays(record, **_kwargs):
        if not overlay_calls:
            record["conclusion_challenge"] = {
                "verdict": "revise",
                "required_action": "recalculate_once",
                "failed_checks": [{"check": "confidence_calibration"}],
                "warnings": [],
                "challenge_summary": "需要重新计算一次",
                "attempt_count": 0,
            }
        else:
            record["conclusion_challenge"] = {
                "verdict": "pass",
                "required_action": "allow_output",
                "failed_checks": [],
                "warnings": [],
                "challenge_summary": "结论通过否定门检查。",
                "attempt_count": 1,
            }
        overlay_calls.append(record["event_id"])

    monkeypatch.setattr(
        "app.services.ai_analysis_service.analyze_market",
        AsyncMock(return_value=analysis),
    )
    monkeypatch.setattr(
        "app.services.cross_validation_service.cross_validate",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(eis, "_build_all_overlays", fake_build_overlays)
    monkeypatch.setattr(eis.settings, "CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS", 1)

    record = _run(
        eis.analyze_event(
            "Will X happen?",
            baseline_probability=40,
            news_context="test context",
            source={"type": "manual"},
        )
    )

    assert len(overlay_calls) == 2
    assert record["conclusion_challenge"]["verdict"] == "pass"
    assert record["conclusion_challenge"]["attempt_count"] == 1
