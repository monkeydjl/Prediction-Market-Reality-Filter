"""evidence_scoring_service.py
=========================
Evidence scoring: aggregate per-article evidence items (produced by
evidence_extraction_service) into an evidence profile - net direction, strength,
conflict, freshness, resolution relevance, and source coverage. Split out of
news_filter_service in Phase 3.

Phase 4: LLM sentiment fusion — when a sentiment_profile (from
news_sentiment_service) is available, its model-based direction and strength are
blended with the keyword-based evidence signal.  The LLM sentiment is more
robust (handles negation, context, nuance) while the keyword signal is
deterministic and cheap.  Weighted fusion gives the best of both: LLM leads when
confident, keywords lead when LLM is uncertain or unavailable.
"""

from typing import Any

from app.services.evidence_extraction_service import classify_evidence
from app.services.market_semantics_service import parse_market_semantics

# Fusion weight: how much the LLM sentiment contributes vs the keyword signal.
# 0.6 = LLM leads when both agree/disagree; keywords still provide 40% anchor.
_SENTIMENT_WEIGHT = 0.6


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

    # Bloomberg family
    if "bloomberg" in source_lower:
        return "bloomberg"

    return source_lower


def fuse_sentiment_direction(
    keyword_direction: str,
    keyword_strength: float,
    sentiment_profile: dict[str, Any],
) -> tuple[str, float, float]:
    """Blend LLM sentiment signal with keyword-based evidence direction.

    Returns (fused_direction, fused_strength, fused_conflict).
    The LLM sentiment uses support_yes/oppose_yes/neutral naming from
    news_sentiment_service; this maps to support/oppose/neutral.

    Fusion formula:
      keyword_signal = direction_sign * strength  (-1..+1)
      sentiment_signal = sentiment_sign * sentiment_strength  (-1..+1)
      fused_signal = w * sentiment + (1-w) * keyword
    Where w = _SENTIMENT_WEIGHT when sentiment is non-fallback, else 0.
    """
    overall_dir = sentiment_profile.get("overall_direction", "neutral")
    overall_str = float(sentiment_profile.get("overall_strength", 0) or 0)
    sentiment_conflict = float(sentiment_profile.get("conflict_level", 0) or 0)
    is_fallback = sentiment_profile.get("fallback", False)

    # Skip fusion when sentiment is unavailable or neutral fallback
    if is_fallback or overall_str < 0.05:
        return keyword_direction, keyword_strength, keyword_strength * 0  # no conflict change

    # Map sentiment direction to sign
    if "support" in overall_dir:
        sentiment_sign = 1.0
    elif "oppose" in overall_dir:
        sentiment_sign = -1.0
    else:
        sentiment_sign = 0.0

    # Map keyword direction to sign
    if keyword_direction == "support":
        keyword_sign = 1.0
    elif keyword_direction == "oppose":
        keyword_sign = -1.0
    else:
        keyword_sign = 0.0

    # Weighted fusion
    w = _SENTIMENT_WEIGHT
    fused_signal = w * (sentiment_sign * overall_str) + (1 - w) * (keyword_sign * keyword_strength)

    # Convert back to direction + strength
    fused_strength = abs(fused_signal)
    if fused_strength < 0.15:
        fused_direction = "neutral"
    elif fused_signal > 0:
        fused_direction = "support"
    else:
        fused_direction = "oppose"

    # Blend conflict: average keyword conflict with LLM conflict level
    # (LLM conflict_level is 0-1, keyword conflict is ratio-based)
    # Not returned here — caller blends separately.
    return fused_direction, round(fused_strength, 3), sentiment_conflict


def build_evidence_profile(
    market_question: str,
    articles: list[dict[str, Any]],
    sentiment_profile: dict[str, Any] | None = None,
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

    # ── Phase 4: LLM sentiment fusion ─────────────────────────────────────
    # Blend the keyword-based direction with the LLM sentiment signal when
    # available.  The LLM handles negation, context, and nuance that keyword
    # counting misses; keywords provide a deterministic anchor.
    if sentiment_profile and not sentiment_profile.get("fallback", False):
        fused_dir, fused_str, sent_conflict = fuse_sentiment_direction(
            direction, strength, sentiment_profile,
        )
        # Only apply fusion when it produces a meaningful signal
        if fused_str >= 0.05:
            direction = fused_dir
            strength = fused_str
            # Blend conflict: 50% keyword conflict + 50% LLM conflict_level
            if sent_conflict > 0:
                conflict = round(conflict * 0.5 + sent_conflict * 0.5, 3)

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


def apply_sentiment_fusion(
    evidence_profile: dict[str, Any],
    sentiment_profile: dict[str, Any],
) -> dict[str, Any]:
    """Apply sentiment fusion to an already-built evidence profile.

    Convenience function for callers that compute sentiment AFTER the evidence
    profile (e.g. _build_filtered_news). Mutates and returns the same dict.
    """
    if not sentiment_profile or sentiment_profile.get("fallback", False):
        return evidence_profile

    fused_dir, fused_str, sent_conflict = fuse_sentiment_direction(
        evidence_profile["evidence_direction"],
        evidence_profile["evidence_strength"],
        sentiment_profile,
    )
    if fused_str >= 0.05:
        evidence_profile["evidence_direction"] = fused_dir
        evidence_profile["evidence_strength"] = fused_str
        if sent_conflict > 0:
            evidence_profile["conflict_score"] = round(
                evidence_profile["conflict_score"] * 0.5 + sent_conflict * 0.5, 3,
            )
    return evidence_profile
