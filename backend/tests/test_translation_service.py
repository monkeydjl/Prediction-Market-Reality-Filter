"""
Unit tests for translation_service (the shared English->Chinese batch translator).

Network-free: the LLM Gateway is mocked. Covers translate_fields JSON result
handling / strip / failure-to-empty, and translate_articles batching (indexed
keys, skipping text already Chinese, best-effort fallback).
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services import translation_service as ts
from app.services.llm_gateway_service import LLMResult


class TranslateFieldsTests(unittest.TestCase):
    def test_empty_payload_short_circuits(self):
        self.assertEqual(asyncio.run(ts.translate_fields({})), {})

    def test_parses_json_and_strips_blanks(self):
        gateway = AsyncMock(
            return_value=LLMResult(
                ok=True,
                json_data={"t0": "  Title ZH  ", "s0": "Summary ZH", "x": "  "},
            )
        )

        with patch.object(ts, "complete_json", gateway, create=True):
            out = asyncio.run(ts.translate_fields({"t0": "Hi", "s0": "World"}))

        self.assertEqual(out, {"t0": "Title ZH", "s0": "Summary ZH"})
        gateway.assert_awaited_once()
        self.assertEqual(gateway.await_args.kwargs["task"], "translation")

    def test_bad_json_returns_empty(self):
        gateway = AsyncMock(return_value=LLMResult(ok=True, json_data=None))

        with patch.object(ts, "complete_json", gateway, create=True):
            self.assertEqual(asyncio.run(ts.translate_fields({"t0": "Hi"})), {})

    def test_gateway_failure_returns_empty(self):
        gateway = AsyncMock(return_value=LLMResult(ok=False, degraded_reason="no route"))

        with patch.object(ts, "complete_json", gateway, create=True):
            self.assertEqual(asyncio.run(ts.translate_fields({"t0": "Hi"})), {})

        gateway.assert_awaited_once()

    def test_gateway_is_used_without_legacy_key_gate(self):
        gateway = AsyncMock(return_value=LLMResult(ok=True, json_data={"t0": "Title ZH"}))

        with patch.object(ts, "complete_json", gateway, create=True):
            out = asyncio.run(ts.translate_fields({"t0": "Hi"}))

        self.assertEqual(out, {"t0": "Title ZH"})
        gateway.assert_awaited_once()
        self.assertEqual(gateway.await_args.kwargs["task"], "translation")


class TranslateArticlesTests(unittest.TestCase):
    def test_empty_list_is_noop(self):
        self.assertEqual(asyncio.run(ts.translate_articles([])), [])

    def test_assigns_zh_and_skips_already_chinese(self):
        articles = [
            {"title": "Fed holds rates", "description": "The central bank held."},
            {"title": "\u5df2\u662f\u4e2d\u6587", "description": "\u4e2d\u6587\u6458\u8981"},
        ]

        async def fake(payload):
            # Only the English article (index 0) is sent for translation.
            self.assertEqual(set(payload), {"t0", "s0"})
            return {"t0": "Fed title ZH", "s0": "Fed summary ZH"}

        with patch.object(ts, "translate_fields", new=fake):
            out = asyncio.run(ts.translate_articles(articles))

        self.assertEqual(out[0]["title_zh"], "Fed title ZH")
        self.assertEqual(out[0]["summary_zh"], "Fed summary ZH")
        self.assertNotIn("title_zh", out[1])
        self.assertNotIn("summary_zh", out[1])

    def test_failure_leaves_articles_unchanged(self):
        articles = [{"title": "Fed holds rates", "description": "x"}]

        async def fake(payload):
            return {}

        with patch.object(ts, "translate_fields", new=fake):
            out = asyncio.run(ts.translate_articles(articles))

        self.assertNotIn("title_zh", out[0])
        self.assertNotIn("summary_zh", out[0])


if __name__ == "__main__":
    unittest.main()
