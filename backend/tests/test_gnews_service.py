# backend/tests/test_gnews_service.py
"""fetch_google_news fan-out isolation.

fetch_google_news runs build_news_queries' variants concurrently through
asyncio.gather(return_exceptions=True). A guard of isinstance(result, Exception)
lets a cancelled variant's CancelledError (a BaseException) through to
articles.extend(...), which raises TypeError and costs the caller every
variant's articles instead of just the cancelled one.
"""
import asyncio
from unittest.mock import patch

import pytest

from app.services.gnews_service import build_news_queries, fetch_google_news

_QUESTION = "Will the Federal Reserve cut interest rates before December 2026?"


def _article(title: str) -> dict:
    return {
        "title": title,
        "description": "",
        "published_date": "",
        "source": "Example",
        "url": f"https://example.com/{title.replace(' ', '-')}",
        "query": "",
    }


@pytest.mark.asyncio
async def test_multiple_query_variants_are_fanned_out():
    assert len(build_news_queries(_QUESTION)) > 1, "test needs >1 variant to isolate"


@pytest.mark.asyncio
async def test_cancelled_variant_keeps_the_other_variants():
    queries = build_news_queries(_QUESTION)
    first = queries[0]

    def _fetch(query: str) -> list[dict]:
        if query == first:
            raise asyncio.CancelledError()
        return [_article(f"kept {query}")]

    with patch("app.services.gnews_service._sync_fetch", side_effect=_fetch):
        articles = await fetch_google_news(_QUESTION)

    assert articles, "a cancelled variant must not empty the whole feed"
    assert all(article["title"].startswith("kept ") for article in articles)


@pytest.mark.asyncio
async def test_failing_variant_keeps_the_other_variants():
    queries = build_news_queries(_QUESTION)
    first = queries[0]

    def _fetch(query: str) -> list[dict]:
        if query == first:
            raise RuntimeError("gnews down")
        return [_article(f"kept {query}")]

    with patch("app.services.gnews_service._sync_fetch", side_effect=_fetch):
        articles = await fetch_google_news(_QUESTION)

    assert articles
    assert all(article["title"].startswith("kept ") for article in articles)
