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
                patch.object(rss.feedparser, "parse", return_value=feed):
            articles = asyncio.run(rss.fetch_news(limit=5))

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "Policy update")
        self.assertEqual(articles[0].source, "Example")

    def test_fetch_news_logs_parse_errors(self):
        with patch.object(rss, "RSS_FEEDS", [("Broken", "https://example.test/rss", "policy")]), \
                patch.object(rss.feedparser, "parse", side_effect=Exception("boom")), \
                self.assertLogs("app.services.rss_service", level="WARNING") as logs:
            articles = asyncio.run(rss.fetch_news(limit=5))

        self.assertEqual(articles, [])
        self.assertIn("RSS feed fetch failed [source=Broken]", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
