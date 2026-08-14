# backend/tests/test_sentiment_aggregator_gather.py
"""fetch_rss_news fan-out isolation.

Every feed in NEWS_FEEDS is fetched concurrently through
asyncio.gather(return_exceptions=True). A guard of isinstance(result, Exception)
lets a cancelled feed's CancelledError (a BaseException) through to
articles.extend(...), which raises TypeError and costs the caller every feed's
articles instead of just the cancelled one.
"""
import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from app.services.sentiment_aggregator import NEWS_FEEDS, fetch_rss_news


def _article(name: str) -> dict[str, Any]:
    return {
        "title": f"kept {name}",
        "source": name,
        "published_at": "2026-06-25T00:00:00Z",
        "sentiment_score": 0.0,
    }


@pytest.mark.asyncio
async def test_multiple_feeds_are_fanned_out():
    assert len(NEWS_FEEDS) > 1, "test needs >1 feed to isolate"


@pytest.mark.asyncio
async def test_cancelled_feed_keeps_the_other_feeds():
    first = NEWS_FEEDS[0]["name"]

    async def _fetch(feed, team_name, timeout):
        if feed["name"] == first:
            raise asyncio.CancelledError()
        return [_article(feed["name"])]

    with patch(
        "app.services.sentiment_aggregator._fetch_single_feed",
        side_effect=_fetch,
    ):
        articles = await fetch_rss_news()

    assert articles, "a cancelled feed must not empty the whole batch"
    assert all(article["title"].startswith("kept ") for article in articles)
    assert first not in {article["source"] for article in articles}


@pytest.mark.asyncio
async def test_failing_feed_keeps_the_other_feeds():
    first = NEWS_FEEDS[0]["name"]

    async def _fetch(feed, team_name, timeout):
        if feed["name"] == first:
            raise RuntimeError("feed down")
        return [_article(feed["name"])]

    with patch(
        "app.services.sentiment_aggregator._fetch_single_feed",
        side_effect=_fetch,
    ):
        articles = await fetch_rss_news()

    assert articles
    assert all(article["title"].startswith("kept ") for article in articles)
