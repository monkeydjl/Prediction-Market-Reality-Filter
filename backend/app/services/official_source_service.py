"""official_source_service.py
==========================
Collector adapter for an official / government RSS feed (default: U.S. Federal
Reserve press releases). Thin by design: fetch + normalize only. No filtering,
scoring, or event logic. Wired into evidence collection via
`event_intelligence_service._build_filtered_news`.

Returns the normalized article shape the news filter expects:
    {"title", "description", "source", "published"}
"""

import asyncio
import logging
from functools import partial

from app.core.config import settings
from app.utils.failure_policy import fail_closed_empty_list
from app.utils.rss_fetch import parse_feed

logger = logging.getLogger(__name__)


def _fetch_sync(url: str, source_name: str, limit: int) -> list[dict]:
    """Synchronous RSS fetch + normalize. Runs in a thread pool."""
    try:
        feed = parse_feed(url)
    except Exception as exc:
        return fail_closed_empty_list(
            logger,
            "official_rss",
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


async def fetch_official_news(limit: int = 5) -> list[dict]:
    """Fetch normalized articles from the configured official RSS feed.

    Returns an empty list if no URL is configured or the feed cannot be read,
    so a missing/broken feed never breaks evidence collection.
    """
    url = settings.OFFICIAL_RSS_URL
    if not url:
        return []
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, partial(_fetch_sync, url, settings.OFFICIAL_SOURCE_NAME, limit)
    )
