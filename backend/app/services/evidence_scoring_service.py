"""evidence_scoring_service.py
=========================
Evidence scoring: aggregate per-article evidence items (produced by
evidence_extraction_service) into an evidence profile - net direction, strength,
conflict, freshness, resolution relevance, and source coverage. Split out of
news_filter_service in Phase 3.
"""

from typing import Any

from app.services.evidence_extraction_service import classify_evidence
from app.services.market_semantics_service import parse_market_semantics


def build_evidence_profile(
    market_question: str,
    articles: list[dict[str, Any]],
) -> dict[str, Any]:
    semantics = parse_market_semantics(market_question)
    evidence_items = [
        classify_evidence(market_question, article, semantics)
        for article in articles
    ]
    support = sum(item["weighted_score"] for item in evidence_items if item["direction"] == "support")
    oppose = sum(item["weighted_score"] for item in evidence_items if item["direction"] == "oppose")
    neutral = sum(item["weighted_score"] for item in evidence_items if item["direction"] == "neutral")
    total = support + oppose + neutral

    if total <= 0:
        direction = "neutral"
        strength = 0.0
        conflict = 0.0
    else:
        net = support - oppose
        strength = abs(net) / total
        conflict = min(support, oppose) / max(support, oppose, 0.001)
        if strength < 0.15:
            direction = "neutral"
        elif net > 0:
            direction = "support"
        else:
            direction = "oppose"

    sources = sorted({
        item["source"]
        for item in evidence_items
        if item["source"]
    })
    freshness = average_field(articles, "age_score")
    resolution_relevance = average_evidence_field(
        evidence_items,
        "resolution_relevance_score",
    )

    return {
        "evidence_direction": direction,
        "evidence_strength": round(strength, 3),
        "support_score": round(support, 3),
        "oppose_score": round(oppose, 3),
        "neutral_score": round(neutral, 3),
        "conflict_score": round(conflict, 3),
        "freshness_score": round(freshness, 3),
        "resolution_relevance_score": round(resolution_relevance, 3),
        "source_count": len(sources),
        "sources": sources[:10],
        "items": evidence_items[:10],
    }


def average_field(articles: list[dict[str, Any]], field: str) -> float:
    if not articles:
        return 0.0
    return round(
        sum(article[field] for article in articles) / len(articles),
        3,
    )


def average_evidence_field(items: list[dict[str, Any]], field: str) -> float:
    if not items:
        return 0.0
    return round(
        sum(item[field] for item in items) / len(items),
        3,
    )
