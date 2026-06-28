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
