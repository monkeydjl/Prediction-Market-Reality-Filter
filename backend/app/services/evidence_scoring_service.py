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


def normalize_source_name(source: str) -> str:
    """Normalize feed/publisher name to media root.
    
    Current source field is a feed display name (e.g., "Reuters Politics"),
    NOT a domain. This function only handles known same-outlet multi-feed
    merging, NOT URL/domain parsing.
    
    Known mappings:
    - "Reuters Politics" -> "reuters"
    - "Reuters Business" -> "reuters"
    - Future GNews publishers like "Reuters", "Reuters.com", etc.
      will be added case-by-case.
    
    Residual limitation: Cross-outlet转载 of the same wire article
    will still be overcounted. This is a conscious trade-off, not an
    oversight.
    """
    if not source:
        return ""
    
    source_lower = source.lower().strip()
    
    # Reuters family feeds
    if "reuters" in source_lower:
        return "reuters"
    
    # Future: add more media mappings
    # if "bloomberg" in source_lower:
    #     return "bloomberg"
    
    return source_lower


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
        # Two-stage evidence strength:
        # 1) direction_signal: normalized net direction among directional
        #    evidence only (-1..+1). Neutral articles do not dilute this.
        # 2) evidence_volume: how many directional articles (capped at 5).
        #    A lone article carries less weight than several agreeing ones.
        # strength = |signal| * volume.
        #
        # Golden values (verified by hand):
        #   2 support, 0 oppose -> strength=0.4, direction=support
        #   2 support, 2 oppose -> strength=0.0, direction=neutral
        directional_count = sum(
            1 for it in evidence_items
            if it["direction"] in ("support", "oppose")
        )
        direction_signal = (support - oppose) / max(support + oppose, 0.001)
        
        # Use deduplicated source count instead of article count
        # This prevents single-feed重复 from inflating evidence volume
        directional_sources = {
            normalize_source_name(it["source"])
            for it in evidence_items
            if it["direction"] in ("support", "oppose") and it["source"]
        }
        evidence_volume = min(1.0, len(directional_sources) / 5.0)
        strength = abs(direction_signal) * evidence_volume
        conflict = min(support, oppose) / max(support, oppose, 0.001)
        if strength < 0.15:
            direction = "neutral"
        elif direction_signal > 0:
            direction = "support"
        else:
            direction = "oppose"

    sources = sorted({
        normalize_source_name(item["source"])
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
