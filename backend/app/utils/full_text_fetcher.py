"""Fetch full article text from URLs using trafilatura.

Used to enrich news articles beyond title+description for LLM sentiment analysis.
Falls back to None on any failure — never blocks the pipeline.
"""
import asyncio
import logging

import httpx
import trafilatura

logger = logging.getLogger(__name__)

_USER_AGENT = "EventIntelligencePlatform/1.0 (+https://github.com/airdrop2474/prediction-market-reality-filter)"


async def fetch_full_text(url: str, *, timeout: float = 10.0) -> str | None:
    """Fetch and extract main article text from a URL.

    Returns extracted text (may be empty), or None on network/parse failure.
    Never raises — callers can treat None as "no full text available".
    """
    if not url:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
        # trafilatura extraction is CPU-bound — run in thread
        text = await asyncio.to_thread(trafilatura.extract, html)
        if text:
            text = text.strip()[:8000]  # cap at 8000 chars to limit LLM cost
        return text
    except Exception as exc:
        logger.warning("full_text_fetch failed for %s: %s", url, exc)
        return None
