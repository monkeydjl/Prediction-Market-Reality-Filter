import asyncio
import unittest
from unittest.mock import patch

from app.services import official_source_service as official


class _FakeFeed:
    def __init__(self, entries):
        self.entries = entries


class OfficialSourceServiceTests(unittest.TestCase):
    def test_fetch_normalizes_entries(self):
        feed = _FakeFeed([
            {"title": "Fed holds rates", "summary": "summary text", "published": "2026-06-12"},
            {"title": "Statement", "description": "desc only", "updated": "2026-06-11"},
        ])
        with patch.object(official, "parse_feed", return_value=feed), \
             patch.object(official.settings, "OFFICIAL_RSS_URL", "http://example/feed"), \
             patch.object(official.settings, "OFFICIAL_SOURCE_NAME", "Federal Reserve"):
            articles = asyncio.run(official.fetch_official_news(limit=5))
        self.assertEqual(len(articles), 2)
        self.assertEqual(
            set(articles[0].keys()),
            {"title", "description", "source", "published", "url"},
        )
        self.assertEqual(articles[0]["title"], "Fed holds rates")
        self.assertEqual(articles[0]["description"], "summary text")
        self.assertEqual(articles[0]["source"], "Federal Reserve")
        self.assertEqual(articles[1]["description"], "desc only")
        self.assertEqual(articles[1]["published"], "2026-06-11")

    def test_fetch_returns_empty_when_no_url(self):
        with patch.object(official.settings, "OFFICIAL_RSS_URL", ""):
            articles = asyncio.run(official.fetch_official_news())
        self.assertEqual(articles, [])

    def test_fetch_handles_parse_errors_gracefully(self):
        empty_feed = _FakeFeed([])
        with patch.object(official, "parse_feed", return_value=empty_feed), \
             patch.object(official.settings, "OFFICIAL_RSS_URL", "http://example/feed"):
            articles = asyncio.run(official.fetch_official_news())
        self.assertEqual(articles, [])


if __name__ == "__main__":
    unittest.main()
