"""sec_edgar_service.py
====================
Collector adapter for SEC EDGAR current filings (default: latest 8-K
material-event filings). Thin by design: fetch + normalize only. No filtering,
scoring, or event logic. Wired into evidence collection via
`event_intelligence_service._build_filtered_news`.

Unlike the other RSS adapters, SEC's fair-access policy requires a declared
User-Agent on every request, so this adapter sends `settings.SEC_USER_AGENT`.

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
    """Synchronous SEC EDGAR fetch + normalize. Runs in a thread pool."""
    try:
        feed = feedparser.parse(url, agent=user_agent)
    except Exception as exc:
        return fail_closed_empty_list(
            logger,
            "sec_edgar_rss",
            exc,
            context={"url": url},
        )
    articles = []
    for entry in feed.entries[:limit]:
        articles.append({
            "title": entry.get("title", ""),
            "description": entry.get("summary", "") or entry.get("description", ""),
            "source": source_name,
            "published": entry.get("updated", "") or entry.get("published", ""),
            "url": entry.get("link", ""),
        })
    return articles


async def fetch_sec_filings(limit: int = 10) -> list[dict]:
    """Fetch normalized articles from the configured SEC EDGAR filings feed.

    Returns an empty list if no URL is configured or the feed cannot be read,
    so a missing/broken feed never breaks evidence collection.
    """
    url = settings.SEC_EDGAR_RSS_URL
    if not url:
        return []
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(_fetch_sync, url, settings.SEC_SOURCE_NAME, settings.SEC_USER_AGENT, limit),
    )
