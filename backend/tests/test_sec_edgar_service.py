import asyncio
import unittest
from unittest.mock import patch

from app.services import sec_edgar_service as sec


class _FakeFeed:
    def __init__(self, entries):
        self.entries = entries


class SecEdgarServiceTests(unittest.TestCase):
    def test_fetch_normalizes_entries(self):
        feed = _FakeFeed([
            {"title": "8-K - EXAMPLE CORP (0001) (Filer)",
             "summary": "Item 2.02 Results", "updated": "2026-06-12"},
            {"title": "10-Q - OTHER INC (0002) (Filer)",
             "description": "desc only", "published": "2026-06-11"},
        ])
        with patch.object(sec, "parse_feed", return_value=feed) as parse, \
             patch.object(sec.settings, "SEC_EDGAR_RSS_URL", "http://example/edgar"), \
             patch.object(sec.settings, "SEC_SOURCE_NAME", "SEC EDGAR"), \
             patch.object(sec.settings, "SEC_USER_AGENT", "UA contact@example.com"):
            articles = asyncio.run(sec.fetch_sec_filings(limit=5))
        self.assertEqual(len(articles), 2)
        self.assertEqual(
            set(articles[0].keys()),
            {"title", "description", "source", "published", "url"},
        )
        self.assertEqual(articles[0]["title"], "8-K - EXAMPLE CORP (0001) (Filer)")
        self.assertEqual(articles[0]["description"], "Item 2.02 Results")
        self.assertEqual(articles[0]["source"], "SEC EDGAR")
        self.assertEqual(articles[0]["published"], "2026-06-12")
        self.assertEqual(articles[1]["description"], "desc only")
        self.assertEqual(articles[1]["published"], "2026-06-11")
        # SEC requires a declared User-Agent on every request.
        self.assertEqual(parse.call_args.kwargs.get("user_agent"), "UA contact@example.com")

    def test_fetch_returns_empty_when_no_url(self):
        with patch.object(sec.settings, "SEC_EDGAR_RSS_URL", ""):
            articles = asyncio.run(sec.fetch_sec_filings())
        self.assertEqual(articles, [])

    def test_fetch_handles_parse_errors_gracefully(self):
        empty_feed = _FakeFeed([])
        with patch.object(sec, "parse_feed", return_value=empty_feed), \
             patch.object(sec.settings, "SEC_EDGAR_RSS_URL", "http://example/edgar"):
            articles = asyncio.run(sec.fetch_sec_filings())
        self.assertEqual(articles, [])


if __name__ == "__main__":
    unittest.main()
