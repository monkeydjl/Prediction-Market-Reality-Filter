import asyncio
import unittest
from unittest.mock import patch

from app.services import economic_data_service as economic


class _FakeFeed:
    def __init__(self, entries):
        self.entries = entries


class EconomicDataServiceTests(unittest.TestCase):
    def test_fetch_normalizes_entries(self):
        feed = _FakeFeed([
            {"title": "CPI up 0.2% in May",
             "summary": "Consumer Price Index summary", "published": "2026-06-12"},
            {"title": "Employment Situation",
             "description": "desc only", "updated": "2026-06-11"},
        ])
        with patch.object(economic.feedparser, "parse", return_value=feed) as parse, \
             patch.object(economic.settings, "ECONOMIC_RSS_URL", "http://example/bls"), \
             patch.object(economic.settings, "ECONOMIC_SOURCE_NAME",
                          "U.S. Bureau of Labor Statistics"), \
             patch.object(economic.settings, "ECONOMIC_USER_AGENT",
                          "UA contact@example.com"):
            articles = asyncio.run(economic.fetch_economic_data(limit=5))
        self.assertEqual(len(articles), 2)
        self.assertEqual(
            set(articles[0].keys()), {"title", "description", "source", "published"}
        )
        self.assertEqual(articles[0]["title"], "CPI up 0.2% in May")
        self.assertEqual(articles[0]["description"], "Consumer Price Index summary")
        self.assertEqual(articles[0]["source"], "U.S. Bureau of Labor Statistics")
        self.assertEqual(articles[0]["published"], "2026-06-12")
        self.assertEqual(articles[1]["description"], "desc only")
        self.assertEqual(articles[1]["published"], "2026-06-11")
        # BLS requires a declared User-Agent on every request.
        self.assertEqual(parse.call_args.kwargs.get("agent"), "UA contact@example.com")

    def test_fetch_returns_empty_when_no_url(self):
        with patch.object(economic.settings, "ECONOMIC_RSS_URL", ""):
            articles = asyncio.run(economic.fetch_economic_data())
        self.assertEqual(articles, [])

    def test_fetch_swallows_parse_errors(self):
        with patch.object(economic.feedparser, "parse", side_effect=Exception("boom")), \
             patch.object(economic.settings, "ECONOMIC_RSS_URL", "http://example/bls"):
            articles = asyncio.run(economic.fetch_economic_data())
        self.assertEqual(articles, [])


if __name__ == "__main__":
    unittest.main()
