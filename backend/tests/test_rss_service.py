import asyncio
import unittest
from unittest.mock import patch

import httpx

from app.services import rss_service as rss
from app.utils import rss_fetch


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

    def test_rss_failure_log_does_not_render_url_or_exception_details(self):
        secret = "rss-query-secret"
        upstream_url = f"https://feeds.example/private.xml?token={secret}"
        exc = httpx.ConnectError(f"connection refused for {upstream_url}")

        with patch.object(rss_fetch.httpx, "get", side_effect=exc), \
                self.assertLogs(rss_fetch.logger.name, level="WARNING") as logs:
            feed = rss_fetch.parse_feed(upstream_url)

        text = "\n".join(logs.output)
        self.assertEqual(feed.entries, [])
        self.assertIn("ConnectError", text)
        self.assertNotIn(upstream_url, text)
        self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
