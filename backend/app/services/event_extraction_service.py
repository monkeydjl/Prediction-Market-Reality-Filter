"""event_extraction_service.py
===========================
Structured event extraction: turn collected open-web articles into native
candidate events, not just evidence.

Where the market event sources (polymarket / manifold / kalshi) produce candidate
events from market prices, this produces them from the news the collector already
fetches: an LLM reads a batch of articles and extracts forward-looking, resolvable
yes/no event questions (with entities and a coarse event_type). Each becomes a
candidate event in the same shape the market sources emit, so it flows through the
identical evidence -> analysis -> trend pipeline. The baseline probability is 50
(no market prior); the source descriptor carries type "open_web".

Opt-in: disabled unless `settings.OPEN_WEB_EXTRACTION_MODEL` is set. No articles,
no config, or any failure yields an empty list, so it never breaks discovery. The
live LLM call lives behind `_ask_extractor` so tests stay network-free. It runs on
the primary provider/client with the configured extraction model.

Event vocabulary only - no trading terms.
"""

import json
import logging
from typing import Any

from app.core.config import settings
from app.utils.failure_policy import fail_closed_empty_list

logger = logging.getLogger(__name__)

# Cap how many articles are sent to the extractor in one prompt.
_MAX_ARTICLES = 40

_EXTRACTION_SYSTEM = (
    "You are an event extraction analyst. You turn news into forward-looking, "
    "resolvable yes/no questions about future outcomes whose answer is not yet "
    "known. Use neutral event language, never trading or betting language. "
    "Return only valid JSON, no markdown."
)


async def extract_candidate_events(
    articles: list[dict[str, Any]],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Extract up to `limit` candidate events from collected articles.

    Returns an empty list when extraction is disabled (no model configured),
    there are no articles, or the extractor is unavailable.
    """
    if not settings.OPEN_WEB_EXTRACTION_MODEL:
        return []
    if not articles:
        return []
    try:
        extracted = await _ask_extractor(articles[:_MAX_ARTICLES], limit)
    except Exception as exc:
        return fail_closed_empty_list(
            logger,
            "open_web_extraction",
            exc,
            context={"limit": limit},
        )

    candidates: list[dict[str, Any]] = []
    for item in extracted:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "") or "").strip()
        if not question:
            continue
        candidates.append(_to_candidate_event(item, question, articles))
    return candidates[:limit]


async def _ask_extractor(articles: list[dict[str, Any]], limit: int) -> list[Any]:
    from app.services.probability_engine_service import get_client

    client = get_client()
    response = await client.chat.completions.create(
        model=settings.OPEN_WEB_EXTRACTION_MODEL,
        messages=[
            {"role": "system", "content": _EXTRACTION_SYSTEM},
            {"role": "user", "content": _build_extraction_prompt(articles, limit)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    events = parsed.get("events") if isinstance(parsed, dict) else None
    return events if isinstance(events, list) else []


def _build_extraction_prompt(articles: list[dict[str, Any]], limit: int) -> str:
    listing = "\n".join(_format_article(i, a) for i, a in enumerate(articles))
    return f"""
From the news items below, extract up to {limit} forward-looking, resolvable
yes/no event questions whose outcome is NOT yet known. Skip items that only
report something already settled, and skip opinion or analysis pieces.

News items:
{listing}

Return exactly this JSON shape:
{{
  "events": [
    {{
      "question": "Will <entity> <do something> by <timeframe>?",
      "entities": ["..."],
      "event_type": "policy|election|economic|legal|company|geopolitical|technology|other",
      "article_index": 0
    }}
  ]
}}

Rules:
- Each question must be answerable yes or no and resolvable by a future fact.
- article_index is the index of the item the question came from.
- Prefer specific, consequential questions over vague ones.
""".strip()


def _format_article(index: int, article: dict[str, Any]) -> str:
    source = str(article.get("source", "") or "")
    title = str(article.get("title", "") or "").strip()
    description = str(article.get("description", "") or "").strip()[:240]
    return f"[{index}] ({source}) {title} - {description}"


def _to_candidate_event(
    item: dict[str, Any],
    question: str,
    articles: list[dict[str, Any]],
) -> dict[str, Any]:
    article = _article_for(item.get("article_index"), articles)
    entities = [
        str(entity).strip()
        for entity in (item.get("entities") or [])
        if str(entity).strip()
    ][:10]
    event_type = str(item.get("event_type", "") or "").strip() or "unknown"
    return {
        "question": question,
        "baseline_probability": 50.0,
        "volume": 0.0,
        "liquidity": 0.0,
        "source": {
            "type": "open_web",
            "platform": settings.OPEN_WEB_SOURCE_NAME,
            "source_id": str(article.get("source", "") or "open_web"),
            "question": question,
            "entities": entities,
            "event_type": event_type,
            "article_title": str(article.get("title", "") or ""),
        },
    }


def _article_for(index: Any, articles: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(index, int) and 0 <= index < len(articles):
        return articles[index]
    return {}
