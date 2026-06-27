"""economic_data_service.py
=======================
Collector adapter for an economic-data news-release RSS feed (default: U.S.
Bureau of Labor Statistics "Latest Numbers" major economic indicators - jobs,
CPI, PPI, import/export prices). Thin by design: fetch + normalize only. No
filtering, scoring, or event logic. Wired into evidence collection via the
collector module (`event_collection_service.collect_shared_articles`).

Like SEC EDGAR, BLS returns 403 to requests without a declared User-Agent, so
this adapter sends `settings.ECONOMIC_USER_AGENT`.

Returns the normalized article shape the news filter expects:
    {"title", "description", "source", "published"}
"""

import asyncio
import logging
from functools import partial

import feedparser

from app.core.config import settings
from app.utils.failure_policy import fail_closed_empty_list

logger = logging.getLogger(__name__)


def _fetch_sync(url: str, source_name: str, user_agent: str, limit: int) -> list[dict]:
    """Synchronous economic-feed fetch + normalize. Runs in a thread pool."""
    try:
        feed = feedparser.parse(url, agent=user_agent)
    except Exception as exc:
        return fail_closed_empty_list(
            logger,
            "economic_rss",
            exc,
            context={"url": url},
        )
    articles = []
    for entry in feed.entries[:limit]:
        articles.append({
            "title": entry.get("title", ""),
            "description": entry.get("summary", "") or entry.get("description", ""),
            "source": source_name,
            "published": entry.get("published", "") or entry.get("updated", ""),
            "url": entry.get("link", ""),
        })
    return articles


async def fetch_economic_data(limit: int = 8) -> list[dict]:
    """Fetch normalized articles from the configured economic-data RSS feed.

    Returns an empty list if no URL is configured or the feed cannot be read,
    so a missing/broken feed never breaks evidence collection.
    """
    url = settings.ECONOMIC_RSS_URL
    if not url:
        return []
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(
            _fetch_sync,
            url,
            settings.ECONOMIC_SOURCE_NAME,
            settings.ECONOMIC_USER_AGENT,
            limit,
        ),
    )
