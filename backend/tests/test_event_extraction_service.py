"""
Tests for structured open-web event extraction (event_extraction_service).

The LLM call (_ask_extractor) is mocked, so these are network-free. They lock the
disabled / no-articles / error paths and the normalization of extracted items into
the shared candidate-event shape (open_web source, baseline 50, entities/event_type,
article linkage, blank-question skip, limit cap).
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import app.services.event_extraction_service as ext
from app.services.llm_gateway_service import LLMResult

ARTICLES = [
    {"title": "Senate to vote on bill", "description": "A vote is scheduled.",
     "source": "Reuters", "published": "x"},
    {"title": "Company plans launch", "description": "New product coming.",
     "source": "BBC", "published": "y"},
]


def _run(coro):
    return asyncio.run(coro)


class ExtractCandidateEventsTests(unittest.TestCase):
    def test_disabled_when_no_model(self):
        with patch.object(ext.settings, "OPEN_WEB_EXTRACTION_MODEL", ""), \
                patch.object(ext.settings, "LLM_ROUTE_OPEN_WEB_EXTRACTION", ""):
            self.assertEqual(_run(ext.extract_candidate_events(ARTICLES, 10)), [])

    def test_no_articles_returns_empty(self):
        with patch.object(ext.settings, "OPEN_WEB_EXTRACTION_MODEL", "m"):
            self.assertEqual(_run(ext.extract_candidate_events([], 10)), [])

    def test_enabled_by_gateway_route_when_legacy_model_is_empty(self):
        extracted = [{"question": "Will the bill pass by July?", "article_index": 0}]
        with patch.object(ext.settings, "OPEN_WEB_EXTRACTION_MODEL", ""), \
                patch.object(ext.settings, "LLM_ROUTE_OPEN_WEB_EXTRACTION", "openai:extractor"), \
                patch.object(ext, "_ask_extractor", new=AsyncMock(return_value=extracted)):
            events = _run(ext.extract_candidate_events(ARTICLES, 10))

        self.assertEqual([event["question"] for event in events], ["Will the bill pass by July?"])

    def test_normalizes_extracted_events(self):
        extracted = [{
            "question": "Will the bill pass by July?",
            "entities": ["Senate", "bill"],
            "event_type": "policy",
            "article_index": 0,
        }]
        with patch.object(ext.settings, "OPEN_WEB_EXTRACTION_MODEL", "m"), \
                patch.object(ext.settings, "OPEN_WEB_SOURCE_NAME", "Open Web"), \
                patch.object(ext, "_ask_extractor", new=AsyncMock(return_value=extracted)):
            events = _run(ext.extract_candidate_events(ARTICLES, 10))
        self.assertEqual(events, [{
            "question": "Will the bill pass by July?",
            "baseline_probability": 50.0,
            "volume": 0.0,
            "liquidity": 0.0,
            "source": {
                "type": "open_web",
                "platform": "Open Web",
                "source_id": "Reuters",
                "question": "Will the bill pass by July?",
                "entities": ["Senate", "bill"],
                "event_type": "policy",
                "article_title": "Senate to vote on bill",
            },
        }])

    def test_skips_blank_questions_and_caps_limit(self):
        extracted = [
            {"question": "Q1?", "article_index": 0},
            {"question": "   ", "article_index": 1},   # blank -> skipped
            {"question": "Q2?", "article_index": 1},
            {"question": "Q3?", "article_index": 0},
        ]
        with patch.object(ext.settings, "OPEN_WEB_EXTRACTION_MODEL", "m"), \
                patch.object(ext, "_ask_extractor", new=AsyncMock(return_value=extracted)):
            events = _run(ext.extract_candidate_events(ARTICLES, 2))
        self.assertEqual([e["question"] for e in events], ["Q1?", "Q2?"])

    def test_bad_article_index_and_missing_fields_default_safely(self):
        extracted = [{"question": "Q?", "article_index": 99}]
        with patch.object(ext.settings, "OPEN_WEB_EXTRACTION_MODEL", "m"), \
                patch.object(ext, "_ask_extractor", new=AsyncMock(return_value=extracted)):
            events = _run(ext.extract_candidate_events(ARTICLES, 10))
        src = events[0]["source"]
        self.assertEqual(src["source_id"], "open_web")  # out-of-range index -> no article
        self.assertEqual(src["article_title"], "")
        self.assertEqual(src["event_type"], "unknown")  # missing -> unknown
        self.assertEqual(src["entities"], [])

    def test_error_returns_empty(self):
        with patch.object(ext.settings, "OPEN_WEB_EXTRACTION_MODEL", "m"), \
                patch.object(ext, "_ask_extractor",
                             new=AsyncMock(side_effect=RuntimeError("boom"))), \
                self.assertLogs("app.services.event_extraction_service",
                                level="WARNING") as logs:
            self.assertEqual(_run(ext.extract_candidate_events(ARTICLES, 10)), [])
        text = "\n".join(logs.output)
        self.assertIn("source=open_web_extraction", text)
        self.assertIn("policy=fail_closed_empty_list", text)

    def test_ask_extractor_uses_gateway(self):
        gateway = AsyncMock(return_value=LLMResult(ok=True, json_data={
            "events": [{
                "question": "Will the bill pass by July?",
                "entities": ["Senate"],
                "event_type": "policy",
                "article_index": 0,
            }]
        }))
        with patch.object(ext.settings, "OPEN_WEB_EXTRACTION_MODEL", "m"), \
                patch.object(ext, "complete_json", gateway, create=True):
            events = _run(ext._ask_extractor(ARTICLES, 5))

        self.assertEqual(events[0]["question"], "Will the bill pass by July?")
        gateway.assert_awaited_once()
        self.assertEqual(gateway.await_args.kwargs["task"], "open_web_extraction")

    def test_legacy_open_web_model_supplies_gateway_route(self):
        gateway = AsyncMock(return_value=LLMResult(ok=True, json_data={"events": []}))
        with patch.object(ext.settings, "LLM_ROUTE_OPEN_WEB_EXTRACTION", ""), \
                patch.object(ext.settings, "OPEN_WEB_EXTRACTION_MODEL", "legacy-extractor"), \
                patch.object(ext.settings, "OPENAI_API_KEY", "open-web-key"), \
                patch.object(ext.settings, "DASHSCOPE_BASE_URL", "https://open-web.example/v1"), \
                patch.object(ext, "complete_json", gateway, create=True):
            events = _run(ext._ask_extractor(ARTICLES, 5))

        self.assertEqual(events, [])
        route = gateway.await_args.kwargs["route"]
        configs = gateway.await_args.kwargs["provider_configs"]
        self.assertEqual([(item.provider, item.models) for item in route], [
            ("legacy_open_web_extraction", ["legacy-extractor"]),
        ])
        self.assertEqual(configs["legacy_open_web_extraction"].api_key, "open-web-key")
        self.assertEqual(configs["legacy_open_web_extraction"].base_url, "https://open-web.example/v1")


if __name__ == "__main__":
    unittest.main()
