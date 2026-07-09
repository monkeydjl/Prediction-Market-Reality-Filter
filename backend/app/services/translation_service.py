"""translation_service.py
=========================
Reusable English -> Simplified Chinese batch translation via the LLM.

Shared by the discovery flow (translating collected news evidence into Chinese)
and the one-off backfill script (translate_events_zh.py). Best-effort by design:
any failure (no API key, API error, malformed JSON) returns the originals so the
caller keeps the English text rather than breaking the pipeline.
"""

import json
import logging
from typing import Any

from app.services.llm_gateway_service import complete_json

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a precise English->Simplified Chinese translator. "
    "Return only valid JSON."
)
_INSTRUCTION = (
    "把下面 JSON 中每个字段的英文文本翻译成简洁、自然的简体中文，保留原意；"
    "专有名词（人名、机构、资产）保留常用形式。"
    "只返回键名完全相同的 JSON，不要添加额外说明。"
)


def looks_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in (text or ""))


async def translate_fields(
    payload: dict[str, str],
) -> dict[str, str]:
    """Translate each English value in ``payload`` to Simplified Chinese.

    ``payload`` maps caller-chosen keys -> English text; the keys let a caller
    batch many strings into one LLM call and re-associate the results. Returns a
    dict with the SAME keys mapped to translated text, dropping any blank/non-str
    values. On any failure (no client + no API key, API error, bad JSON) returns
    ``{}`` so the caller keeps the originals.
    """
    if not payload:
        return {}
    try:
        result = await complete_json(
            task="translation",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": _INSTRUCTION
                    + "\n\n"
                    + json.dumps(payload, ensure_ascii=False),
                },
            ],
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001 - best effort, keep originals on failure
        logger.warning("Translation failed: %s", exc)
        return {}
    data = result.json_data if result.ok else None
    if not isinstance(data, dict):
        return {}
    return {
        key: value.strip()
        for key, value in data.items()
        if isinstance(value, str) and value.strip()
    }


async def translate_articles(
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add Chinese ``title_zh`` / ``summary_zh`` to news/official articles.

    One batched LLM call for the whole list; indexed keys (t{i}/s{i}) keep each
    translation associated with its article. English text already in Chinese is
    skipped. Mutates the article dicts in place and returns the same list. On any
    translation failure the articles are returned unchanged (no zh fields) and
    callers fall back to the English text.
    """
    if not articles:
        return articles
    payload: dict[str, str] = {}
    for i, article in enumerate(articles):
        title = str(article.get("title") or "").strip()
        summary = str(
            article.get("summary") or article.get("description") or ""
        ).strip()
        if title and not looks_chinese(title):
            payload[f"t{i}"] = title
        if summary and not looks_chinese(summary):
            payload[f"s{i}"] = summary
    if not payload:
        return articles

    translated = await translate_fields(payload)
    for i, article in enumerate(articles):
        title_zh = translated.get(f"t{i}")
        if title_zh:
            article["title_zh"] = title_zh[:300]
        summary_zh = translated.get(f"s{i}")
        if summary_zh:
            article["summary_zh"] = summary_zh[:500]
    return articles
