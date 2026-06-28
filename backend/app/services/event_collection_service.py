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

from app.core.config import settings
from app.utils.failure_policy import fail_closed_empty_list, log_service_failure
from app.utils.full_text_fetcher import fetch_full_text

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
            log_service_failure(
                logger,
                "shared_source",
                result,
                policy="fail_closed_empty_list",
                context={"label": label},
            )
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

    When `settings.NEWS_FULL_TEXT_FETCH_ENABLED` is true (default), the top
    `settings.NEWS_FULL_TEXT_MAX_ARTICLES` articles are enriched with a
    `full_text` field (extracted main text from the article URL) so downstream
    LLM sentiment analysis has more signal than title+description alone. All
    other articles get `full_text=None`. When the flag is false, full-text
    fetching is skipped entirely and every article is returned with
    `full_text=None`. Failures during fetch surface as `None` (never raise) -
    full-text is an enhancement, not a blocker.
    """
    from app.services.gnews_service import fetch_google_news

    if shared_articles is None:
        shared_articles = await collect_shared_articles()
    try:
        google_news = await fetch_google_news(event_question)
    except Exception as exc:
        google_news = fail_closed_empty_list(
            logger,
            "query_source",
            exc,
            context={"label": "gnews"},
        )
    google_news = [{**article, "kind": "news"} for article in google_news]
    articles = shared_articles + google_news

    # Read the cap at call time so tests/monkeypatches on settings take effect.
    full_text_cap = settings.NEWS_FULL_TEXT_MAX_ARTICLES

    if not settings.NEWS_FULL_TEXT_FETCH_ENABLED:
        # Feature disabled: skip the HTTP cost entirely; every article still
        # carries the full_text key (None) so downstream consumers can rely on
        # the key's presence regardless of the flag.
        for article in articles:
            article["full_text"] = None
        return articles

    # Enrich top articles with full text (capped to limit cost). gather with
    # return_exceptions=True so one slow/failing URL never breaks the batch;
    # fetch_full_text also returns None on internal failure, but the
    # isinstance(str) guard below safely absorbs both None and exception objects.
    top_articles = articles[:full_text_cap]
    full_text_tasks = [fetch_full_text(a.get("url", "")) for a in top_articles]
    full_texts = await asyncio.gather(*full_text_tasks, return_exceptions=True)
    for article, full_text in zip(top_articles, full_texts):
        if isinstance(full_text, str) and full_text:
            article["full_text"] = full_text
        else:
            article["full_text"] = None
    # Remaining articles: full_text not fetched, marked None so every article
    # in the returned list carries the key for downstream consumers.
    for article in articles[full_text_cap:]:
        article["full_text"] = None
    return articles
