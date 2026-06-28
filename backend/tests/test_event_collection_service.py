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
                   AsyncMock(return_value=gnews)):
            articles = asyncio.run(
                collection.collect_articles("will X happen?", shared_articles=shared)
            )
        # Shared reused as-is; gnews appended, tagged "news". Every article
        # carries a full_text=None key (enrichment moved to _build_filtered_news,
        # so collect_articles just seeds the key with None).
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
             self.assertLogs("app.services.event_collection_service",
                             level="WARNING") as logs:
            articles = asyncio.run(
                collection.collect_articles("will X happen?", shared_articles=shared)
            )

        # gnews failed -> [] (fail_closed_empty_list); shared article survives
        # with full_text=None (enrichment moved to _build_filtered_news).
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "shared")
        self.assertIsNone(articles[0]["full_text"])
        text = "\n".join(logs.output)
        self.assertIn("source=query_source", text)
        self.assertIn("policy=fail_closed_empty_list", text)

    def test_collect_articles_sets_full_text_none_for_every_article(self):
        """Full-text enrichment moved to _build_filtered_news; collect_articles
        just seeds full_text=None on every article so downstream consumers can
        rely on the key's presence."""
        shared = [
            {"title": f"shared-{i}", "description": "d", "source": "s",
             "published": "p", "url": f"http://example.com/{i}"}
            for i in range(3)
        ]
        with patch("app.services.gnews_service.fetch_google_news",
                   AsyncMock(return_value=[])):
            articles = asyncio.run(
                collection.collect_articles("will X happen?", shared_articles=shared)
            )
        self.assertEqual(len(articles), 3)
        for article in articles:
            self.assertIsNone(article["full_text"])


if __name__ == "__main__":
    unittest.main()
