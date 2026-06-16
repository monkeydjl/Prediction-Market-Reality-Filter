"""
Unit tests for translation_service (the shared English->Chinese batch translator).

Network-free: the LLM client is mocked. Covers translate_fields JSON parsing /
strip / failure-to-empty, the no-key short-circuit, and translate_articles
batching (indexed keys, skipping text already Chinese, best-effort fallback).
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import translation_service as ts


def _client(content: str):
    """A fake OpenAI-style client whose completion returns `content`."""
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return client, create


class TranslateFieldsTests(unittest.TestCase):
    def test_empty_payload_short_circuits(self):
        self.assertEqual(asyncio.run(ts.translate_fields({})), {})

    def test_parses_json_and_strips_blanks(self):
        client, create = _client('{"t0": "  你好  ", "s0": "世界", "x": "  "}')
        out = asyncio.run(ts.translate_fields({"t0": "Hi", "s0": "World"}, client=client))
        self.assertEqual(out, {"t0": "你好", "s0": "世界"})
        create.assert_awaited_once()

    def test_bad_json_returns_empty(self):
        client, _ = _client("not json at all")
        self.assertEqual(
            asyncio.run(ts.translate_fields({"t0": "Hi"}, client=client)), {}
        )

    def test_no_client_and_no_key_returns_empty(self):
        with patch.object(ts.settings, "OPENAI_API_KEY", ""):
            self.assertEqual(asyncio.run(ts.translate_fields({"t0": "Hi"})), {})


class TranslateArticlesTests(unittest.TestCase):
    def test_empty_list_is_noop(self):
        self.assertEqual(asyncio.run(ts.translate_articles([])), [])

    def test_assigns_zh_and_skips_already_chinese(self):
        articles = [
            {"title": "Fed holds rates", "description": "The central bank held."},
            {"title": "央行维持利率", "description": "已是中文"},
        ]

        async def fake(payload, client=None):
            # Only the English article (index 0) is sent for translation.
            self.assertEqual(set(payload), {"t0", "s0"})
            return {"t0": "美联储维持利率", "s0": "央行维持了利率。"}

        with patch.object(ts, "translate_fields", new=fake):
            out = asyncio.run(ts.translate_articles(articles))

        self.assertEqual(out[0]["title_zh"], "美联储维持利率")
        self.assertEqual(out[0]["summary_zh"], "央行维持了利率。")
        self.assertNotIn("title_zh", out[1])
        self.assertNotIn("summary_zh", out[1])

    def test_failure_leaves_articles_unchanged(self):
        articles = [{"title": "Fed holds rates", "description": "x"}]

        async def fake(payload, client=None):
            return {}

        with patch.object(ts, "translate_fields", new=fake):
            out = asyncio.run(ts.translate_articles(articles))

        self.assertNotIn("title_zh", out[0])
        self.assertNotIn("summary_zh", out[0])


if __name__ == "__main__":
    unittest.main()
