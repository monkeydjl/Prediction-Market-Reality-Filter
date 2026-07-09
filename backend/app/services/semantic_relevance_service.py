"""semantic_relevance_service.py
==============================
Optional semantic news relevance via text embeddings.

Opt-in: a no-op unless ``settings.EMBEDDING_MODEL`` is set. When enabled, it
embeds the event question and each collected article in one batched call and
writes a cosine-similarity score (0-1) onto each article as
``semantic_relevance``. The news filter then blends that with its keyword
relevance (max-merge), so semantic matching rescues relevant articles that share
little surface vocabulary with the question.

Best-effort: any failure (no key, API error, shape mismatch) leaves the articles
unchanged and the filter falls back to keyword-only relevance. The embedding
provider is configured separately from the chat LLM because the default chat
provider (DeepSeek) has no embeddings endpoint.
"""

import logging
import math
from typing import Any

from app.core.config import settings
from app.services.llm_gateway_service import complete_embeddings

logger = logging.getLogger(__name__)


def _article_text(article: dict[str, Any]) -> str:
    title = str(article.get("title") or "").strip()
    description = str(
        article.get("description") or article.get("summary") or ""
    ).strip()
    return f"{title}. {description}".strip()[:1000]


def _query_text(question: str, semantics: dict[str, Any] | None) -> str:
    parts = [str(question or "").strip()]
    if semantics:
        parts.extend(
            str(entity).strip()
            for entity in (semantics.get("entities") or [])
            if str(entity or "").strip()
        )
        for key in ("yes_condition", "no_condition"):
            value = str(semantics.get(key) or "").strip()
            if value:
                parts.append(value)
    return " ".join(part for part in parts if part)[:1000]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def annotate_semantic_relevance(
    question: str,
    articles: list[dict[str, Any]],
    semantics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Annotate each article with ``semantic_relevance`` (0-1) in place.

    No-op (articles returned unchanged) when embeddings are disabled, there are
    no articles, the query is empty, or the embedding call fails. One batched
    embeddings request covers the query plus every article.
    """
    if not (settings.LLM_ROUTE_EMBEDDING or settings.EMBEDDING_MODEL) or not articles:
        return articles
    query = _query_text(question, semantics)
    if not query:
        return articles

    inputs = [query] + [_article_text(article) for article in articles]
    try:
        result = await complete_embeddings(input=inputs)
    except Exception as exc:  # noqa: BLE001 - best effort, keep keyword fallback
        logger.warning("Semantic relevance embedding failed: %s", exc)
        return articles
    if not result.ok or result.vectors is None:
        return articles
    vectors = result.vectors
    if len(vectors) != len(inputs):
        return articles

    query_vec = vectors[0]
    for article, vec in zip(articles, vectors[1:]):
        article["semantic_relevance"] = round(max(0.0, _cosine(query_vec, vec)), 3)
    return articles
