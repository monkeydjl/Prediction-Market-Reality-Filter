import asyncio
import unittest
from unittest.mock import patch

from app.services import rss_service as rss


class _FakeFeed:
    def __init__(self, entries):
        self.entries = entries


class RssServiceTests(unittest.TestCase):
    def test_fetch_news_normalizes_entries(self):
        feed = _FakeFeed([
            {
                "title": "Policy update",
                "summary": "Agency statement",
                "link": "https://example.test/policy",
                "published": "2026-06-12",
            },
        ])
        with patch.object(rss, "RSS_FEEDS", [("Example", "https://example.test/rss", "policy")]), \
                patch.object(rss, "parse_feed", return_value=feed):
            articles = asyncio.run(rss.fetch_news(limit=5))

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "Policy update")
        self.assertEqual(articles[0].source, "Example")

    def test_fetch_news_handles_parse_errors_gracefully(self):
        # parse_feed swallows errors internally and returns an empty feed.
        empty_feed = _FakeFeed([])
        with patch.object(rss, "RSS_FEEDS", [("Broken", "https://example.test/rss", "policy")]), \
                patch.object(rss, "parse_feed", return_value=empty_feed):
            articles = asyncio.run(rss.fetch_news(limit=5))

        self.assertEqual(articles, [])


if __name__ == "__main__":
    unittest.main()
