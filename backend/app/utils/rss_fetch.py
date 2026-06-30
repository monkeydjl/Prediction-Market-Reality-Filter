"""rss_fetch.py
==============
Shared RSS content fetcher with timeout and retry.

Every feedparser-based service in the codebase needs the same plumbing: fetch
the feed content with a timeout (feedparser has no built-in one), handle common
HTTP errors, and pass the raw content to feedparser.parse(). Centralizing it
here means a fix to timeout / retry / User-Agent logic propagates to all five
services (rss_service, official_source_service, sec_edgar_service,
economic_data_service, sentiment_aggregator) in one change.

feedparser.parse() accepts a string (raw XML/HTML) as its first argument, so we
pre-fetch the content with httpx (which supports proper connect + read
timeouts) and hand the decoded text to feedparser.
"""

import logging
from typing import Any

import feedparser
import httpx

logger = logging.getLogger(__name__)

# Default timeout in seconds. A hung server is worse than a missing feed.
DEFAULT_TIMEOUT: float = 15.0

DEFAULT_USER_AGENT = (
    "PMRF/1.0 (+https://github.com/prediction-market-reality-filter)"
)


def parse_feed(
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Fetch an RSS/Atom feed with a timeout and return a parsed feedparser object.

    Equivalent to ``feedparser.parse(url)`` but with:
    - A proper connect + read timeout (feedparser's internal urllib has none).
    - A declared User-Agent so sites that reject blank UAs (SEC, BLS) work.
    - Graceful degradation: any fetch/parse error returns an empty FeedParserDict
      (feed.entries == []) so callers can iterate without checking for None.

    Returns a feedparser.FeedParserDict. On error, returns an empty result
    (feed.entries == []) and logs the failure — callers should treat this the
    same as a feed with zero entries.
    """
    headers = {"User-Agent": user_agent or DEFAULT_USER_AGENT}
    try:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        return feedparser.parse(response.content)
    except httpx.TimeoutException:
        logger.warning("RSS feed timed out: %s (timeout=%.1fs)", url, timeout)
        return feedparser.parse("")
    except httpx.HTTPStatusError as exc:
        logger.warning("RSS feed HTTP error %s: %s", exc.response.status_code, url)
        return feedparser.parse("")
    except httpx.HTTPError as exc:
        logger.warning("RSS fetch failed for %s: %s", url, exc)
        return feedparser.parse("")
    except Exception as exc:
        logger.error("Unexpected error parsing RSS feed %s: %s", url, exc)
        return feedparser.parse("")
