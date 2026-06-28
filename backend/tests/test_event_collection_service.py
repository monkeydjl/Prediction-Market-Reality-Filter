import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import event_collection_service as collection


def _rss_item(title, summary, source, published):
    return SimpleNamespace(
        title=title, summary=summary, source=source, published=published
    )


class EventCollectionServiceTests(unittest.TestCase):
    def test_collect_shared_combines_and_normalizes(self):
        rss = [_rss_item("RSS title", "RSS body", "Reuters", "2026-06-12")]
        official = [{"title": "Fed", "description": "d",
                     "source": "Federal Reserve", "published": "2026-06-12"}]
        sec = [{"title": "8-K", "description": "d",
                "source": "SEC EDGAR", "published": "2026-06-11"}]
        econ = [{"title": "CPI up 0.2%", "description": "d",
                 "source": "U.S. Bureau of Labor Statistics", "published": "2026-06-12"}]
        with patch("app.services.rss_service.fetch_news",
                   AsyncMock(return_value=rss)), \
             patch("app.services.official_source_service.fetch_official_news",
                   AsyncMock(return_value=official)), \
             patch("app.services.sec_edgar_service.fetch_sec_filings",
                   AsyncMock(return_value=sec)), \
             patch("app.services.economic_data_service.fetch_economic_data",
                   AsyncMock(return_value=econ)):
            articles = asyncio.run(collection.collect_shared_articles())
        self.assertEqual(len(articles), 4)
        # RSS NewsModel normalized to the common dict shape (tagged news + url).
        self.assertEqual(
            articles[0],
            {"title": "RSS title", "description": "RSS body",
             "source": "Reuters", "published": "2026-06-12", "url": "", "kind": "news"},
        )
        # Official, SEC, and economic dicts passed through, tagged "official".
        self.assertIn({**official[0], "kind": "official"}, articles)
        self.assertIn({**sec[0], "kind": "official"}, articles)
        self.assertIn({**econ[0], "kind": "official"}, articles)

    def test_collect_articles_appends_gnews_and_reuses_shared(self):
        shared = [{"title": "shared", "description": "d",
                   "source": "s", "published": "p"}]
        # Google News carries its own richer shape (published_date/url/query).
        gnews = [{"title": "GN", "description": "d", "source": "Pub",
                  "published_date": "2026-06-12", "url": "u", "query": "q"}]
        rss_mock = AsyncMock()
        with patch("app.services.rss_service.fetch_news", rss_mock), \
             patch("app.services.gnews_service.fetch_google_news",
                   AsyncMock(return_value=gnews)), \
             patch("app.services.event_collection_service.fetch_full_text",
                   AsyncMock(return_value=None)):
            articles = asyncio.run(
                collection.collect_articles("will X happen?", shared_articles=shared)
            )
        # Shared reused as-is; gnews appended, tagged "news". Every article
        # carries a full_text key (None here because fetch_full_text is mocked
        # to return None).
        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0]["title"], "shared")
        self.assertIsNone(articles[0]["full_text"])
        self.assertEqual(articles[1], {**gnews[0], "kind": "news",
                                       "full_text": None})
        # Shared sources were not re-fetched because shared_articles was provided.
        rss_mock.assert_not_called()

    def test_collect_shared_isolates_failing_source(self):
        official = [{"title": "Fed", "description": "d",
                     "source": "Federal Reserve", "published": "2026-06-12"}]
        with patch("app.services.rss_service.fetch_news",
                   AsyncMock(side_effect=Exception("boom"))), \
             patch("app.services.official_source_service.fetch_official_news",
                   AsyncMock(return_value=official)), \
             patch("app.services.sec_edgar_service.fetch_sec_filings",
                   AsyncMock(return_value=[])), \
             patch("app.services.economic_data_service.fetch_economic_data",
                   AsyncMock(return_value=[])), \
             self.assertLogs("app.services.event_collection_service",
                             level="WARNING") as logs:
            articles = asyncio.run(collection.collect_shared_articles())
        # The failing RSS source contributes nothing; the others still collected.
        self.assertEqual(articles, [{**official[0], "kind": "official"}])
        self.assertIn("policy=fail_closed_empty_list", "\n".join(logs.output))

    def test_collect_articles_isolates_failing_gnews(self):
        shared = [{"title": "shared", "description": "d",
                   "source": "s", "published": "p"}]
        with patch("app.services.gnews_service.fetch_google_news",
                   AsyncMock(side_effect=Exception("gnews down"))), \
             patch("app.services.event_collection_service.fetch_full_text",
                   AsyncMock(return_value=None)), \
             self.assertLogs("app.services.event_collection_service",
                             level="WARNING") as logs:
            articles = asyncio.run(
                collection.collect_articles("will X happen?", shared_articles=shared)
            )

        # gnews failed -> [] (fail_closed_empty_list); shared article survives
        # with full_text=None (fetch_full_text mocked to None).
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "shared")
        self.assertIsNone(articles[0]["full_text"])
        text = "\n".join(logs.output)
        self.assertIn("source=query_source", text)
        self.assertIn("policy=fail_closed_empty_list", text)

    def test_collect_articles_enriches_top_5_with_full_text(self):
        """Top `_MAX_FULL_TEXT_ARTICLES` get full_text; the rest get None.

        Verifies the integration of fetch_full_text into collect_articles:
        - Only the first 5 articles are passed to fetch_full_text.
        - Every returned article carries a `full_text` key (string or None).
        - Articles ranked 6+ get full_text=None without any fetch call.
        """
        # 8 shared articles + 2 gnews = 10 total. Top 5 enriched, rest None.
        shared = [
            {"title": f"shared-{i}", "description": "d", "source": "s",
             "published": "p", "url": f"http://example.com/{i}"}
            for i in range(8)
        ]
        gnews = [
            {"title": "GN-1", "description": "d", "source": "Pub",
             "published_date": "2026-06-12", "url": "http://example.com/g1",
             "query": "q"},
            {"title": "GN-2", "description": "d", "source": "Pub",
             "published_date": "2026-06-12", "url": "http://example.com/g2",
             "query": "q"},
        ]

        captured_urls: list[str] = []

        async def fake_fetch(url, *, timeout=10.0):
            captured_urls.append(url)
            return f"FULL::{url}"

        with patch("app.services.rss_service.fetch_news", AsyncMock()), \
             patch("app.services.gnews_service.fetch_google_news",
                   AsyncMock(return_value=gnews)), \
             patch("app.services.event_collection_service.fetch_full_text",
                   new=fake_fetch):
            articles = asyncio.run(
                collection.collect_articles("will X happen?", shared_articles=shared)
            )

        # Exactly 5 fetches (top 5); URLs are the first 5 shared articles.
        self.assertEqual(captured_urls, [
            "http://example.com/0", "http://example.com/1",
            "http://example.com/2", "http://example.com/3",
            "http://example.com/4",
        ])
        # All 10 articles present; top 5 enriched, rest None.
        self.assertEqual(len(articles), 10)
        for i in range(5):
            self.assertEqual(
                articles[i]["full_text"], f"FULL::http://example.com/{i}",
                msg=f"article {i} should have full_text",
            )
        for i in range(5, 10):
            self.assertIsNone(
                articles[i]["full_text"],
                msg=f"article {i} should have full_text=None",
            )

    def test_collect_articles_handles_fetch_full_text_returning_none(self):
        """When fetch_full_text returns None, the article still gets full_text=None."""
        shared = [
            {"title": "shared", "description": "d", "source": "s",
             "published": "p", "url": "http://example.com/x"}
        ]
        with patch("app.services.gnews_service.fetch_google_news",
                   AsyncMock(return_value=[])), \
             patch("app.services.event_collection_service.fetch_full_text",
                   AsyncMock(return_value=None)):
            articles = asyncio.run(
                collection.collect_articles("will X happen?", shared_articles=shared)
            )
        self.assertEqual(len(articles), 1)
        self.assertIsNone(articles[0]["full_text"])

    def test_collect_articles_isolates_failing_fetch_full_text(self):
        """A raising fetch_full_text is swallowed via gather(return_exceptions=True).

        fetch_full_text is contract-bound to never raise, but the
        return_exceptions=True + isinstance(str) guard is the safety net.
        """
        shared = [
            {"title": "shared", "description": "d", "source": "s",
             "published": "p", "url": "http://example.com/x"}
        ]
        with patch("app.services.gnews_service.fetch_google_news",
                   AsyncMock(return_value=[])), \
             patch("app.services.event_collection_service.fetch_full_text",
                   AsyncMock(side_effect=RuntimeError("net down"))):
            articles = asyncio.run(
                collection.collect_articles("will X happen?", shared_articles=shared)
            )
        self.assertEqual(len(articles), 1)
        self.assertIsNone(articles[0]["full_text"])


if __name__ == "__main__":
    unittest.main()
