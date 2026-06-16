"""event_collection_service.py
==========================
Collector module: owns orchestration of evidence collection across information
sources. Source adapters stay thin (fetch + normalize); this module decides
which sources to run, runs them concurrently, and combines their output into a
single list for the news filter.

Query-independent sources (general RSS, official announcements, SEC filings) are
separated from the query-specific source (Google News). A discovery scan can
fetch the shared, query-independent feeds once via `collect_shared_articles` and
reuse them across every candidate event instead of re-fetching per event.

Article shape consumed downstream: {"title", "description", "source",
"published"}. Google News carries a richer dict (published_date/url/query); it
is passed through unchanged because the news filter already tolerates it.
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def collect_shared_articles() -> list[dict[str, Any]]:
    """Fetch and normalize the query-independent sources concurrently.

    A failing source contributes nothing rather than breaking the whole
    collection (mirrors the per-source resilience in rss_service/gnews_service).
    """
    from app.services.economic_data_service import fetch_economic_data
    from app.services.official_source_service import fetch_official_news
    from app.services.rss_service import fetch_news
    from app.services.sec_edgar_service import fetch_sec_filings

    results = await asyncio.gather(
        fetch_news(limit=8),
        fetch_official_news(limit=8),
        fetch_sec_filings(limit=8),
        fetch_economic_data(limit=8),
        return_exceptions=True,
    )
    cleaned = []
    for label, result in zip(("rss", "official", "sec", "economic"), results):
        if isinstance(result, Exception):
            logger.warning("Source collection failed [%s]: %s", label, result)
            result = []
        cleaned.append(result)
    rss_news, official_news, sec_filings, economic_data = cleaned

    rss_articles = [
        {
            "title": item.title,
            "description": item.summary,
            "source": item.source,
            "published": item.published,
            "url": getattr(item, "link", "") or "",
            "kind": "news",
        }
        for item in rss_news
    ]
    # Official / regulatory / economic feeds are evidence of record; tag them
    # "official" so the UI can separate official information from public news.
    official_articles = [
        {**article, "kind": "official"}
        for article in official_news + sec_filings + economic_data
    ]
    return rss_articles + official_articles


async def collect_articles(
    event_question: str,
    shared_articles: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """All evidence articles for an event question.

    Pass `shared_articles` from `collect_shared_articles()` to avoid re-fetching
    the query-independent feeds; otherwise they are fetched here. Google News is
    query-specific and always fetched.
    """
    from app.services.gnews_service import fetch_google_news

    if shared_articles is None:
        shared_articles = await collect_shared_articles()
    try:
        google_news = await fetch_google_news(event_question)
    except Exception as exc:
        logger.warning("Source collection failed [gnews]: %s", exc)
        google_news = []
    google_news = [{**article, "kind": "news"} for article in google_news]
    return shared_articles + google_news
