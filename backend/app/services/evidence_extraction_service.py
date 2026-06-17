"""evidence_extraction_service.py
============================
Evidence extraction: turn a single filtered article into a classified evidence
item - its direction relative to the market question (support / oppose /
neutral), how directly it speaks to resolution, and a weighted score. Split out
of news_filter_service in Phase 3.

Aggregating these items into an evidence profile lives in
evidence_scoring_service; article filtering and ranking live in
news_filter_service. Semantics are passed in (parsed once by the caller).
"""

import re
from typing import Any

from app.utils.text_match import word_in_text


def classify_evidence(
    market_question: str,
    article: dict[str, Any],
    semantics: dict[str, Any],
) -> dict[str, Any]:
    text = f"{article['title']} {article['description']}".lower()
    question = market_question.lower()
    # Pass semantics to infer_direction for threshold polarity logic
    direction = infer_direction(question, text, semantics)
    resolution_relevance = score_resolution_relevance(text, semantics)
    
    # Window alignment factor (Trap L3: must be OUTSIDE the 0.35 floor)
    window_alignment = compute_window_alignment(article, semantics)
    
    weighted_score = (
        article["quality_score"]
        * article["relevance_score"]
        * article["source_quality"]
        * max(0.35, resolution_relevance)  # floor
        * window_alignment  # OUTSIDE floor - ensures expired articles are penalized in direction voting
    )

    return {
        "title": article["title"],
        "source": article["source"],
        "direction": direction,
        "weighted_score": round(weighted_score, 3),
        "quality_score": article["quality_score"],
        "relevance_score": article["relevance_score"],
        "resolution_relevance_score": resolution_relevance,
    }


def infer_direction(question: str, text: str, semantics: dict[str, Any] | None = None) -> str:
    """Infer evidence direction relative to market question.
    
    For threshold markets (above/below), uses polarity-aware logic:
    - above market + article says "rises" -> support
    - above market + article says "falls" -> oppose
    - below market + article says "rises" -> oppose
    - below market + article says "falls" -> support
    
    For non-threshold markets, uses legacy yes_positive flip logic.
    """
    # Keep both base form and inflections - word_in_text uses boundary matching,
    # so "win" does NOT match "wins" (\bwin\b stops at the 's').
    # We need explicit forms to catch common inflections without over-matching.
    positive_terms = (
        "win", "wins", "pass", "passes", "passed", "approve", "approved", "approval",
        "launch", "launches", "confirm", "confirms", "confirmed",
        "raise", "raises", "rise", "rises", "surge", "surges",
        "lead", "leads", "ahead", "beat", "beats", "grow", "growth", "record high",
    )
    negative_terms = (
        "lose", "loses", "fail", "fails", "failed", "reject", "rejected", "denied",
        "cancel", "cancelled", "canceled", "delay", "delayed",
        "fall", "falls", "drop", "drops", "behind", "miss", "misses",
        "lawsuit", "investigation",
    )

    # Check if this is a threshold market
    if semantics and semantics.get("condition_type") == "threshold":
        # New logic: threshold polarity × article movement
        threshold_direction = semantics.get("threshold_direction", "unknown")
        
        positive_hits = sum(1 for term in positive_terms if word_in_text(term, text))
        negative_hits = sum(1 for term in negative_terms if word_in_text(term, text))
        
        # Determine article movement
        if positive_hits > negative_hits:
            article_movement = "up"
        elif negative_hits > positive_hits:
            article_movement = "down"
        else:
            return "neutral"
        
        # Combine threshold direction with article movement
        if threshold_direction == "above":
            # Above market: up=support, down=oppose
            return "support" if article_movement == "up" else "oppose"
        elif threshold_direction == "below":
            # Below market: down=support, up=oppose
            return "support" if article_movement == "down" else "oppose"
        else:
            # Unknown threshold direction, fall back to neutral
            return "neutral"
    
    # Legacy logic for non-threshold markets
    yes_positive = not any(term in question for term in (" not ", " fail", " lose", " below"))
    positive_hits = sum(1 for term in positive_terms if word_in_text(term, text))
    negative_hits = sum(1 for term in negative_terms if word_in_text(term, text))

    if positive_hits == negative_hits:
        return "neutral"

    supports_yes = positive_hits > negative_hits
    if not yes_positive:
        supports_yes = not supports_yes

    return "support" if supports_yes else "oppose"


def score_resolution_relevance(
    text: str,
    semantics: dict[str, Any],
) -> float:
    score = 0.25
    entities = semantics.get("entities", [])
    threshold = semantics.get("threshold")
    deadline = semantics.get("deadline")
    condition_type = semantics.get("condition_type", "binary_event")
    threshold_direction = semantics.get("threshold_direction", "unknown")

    if entities:
        entity_hits = sum(1 for entity in entities if entity in text)
        score += min(0.35, entity_hits * 0.12)
    if threshold and threshold.lower() in text:
        score += 0.25
    if deadline and deadline.lower() in text:
        score += 0.15
    
    # Threshold relevance: split keywords by polarity (Trap L2-2)
    # Above markets: hit/reach/above/record high/rises
    # Below markets: below/under/falls/drops/misses/declines
    if condition_type == "threshold":
        if threshold_direction == "above":
            if any(term in text for term in ("hit", "reach", "above", "record", "high", "price",
                                              "rises", "increases", "surges", "grows")):
                score += 0.15
        elif threshold_direction == "below":
            if any(term in text for term in ("below", "under", "falls", "drops", "misses",
                                              "declines", "decreases", "falls below", "drops below")):
                score += 0.15
        else:
            # Unknown direction: use old generic keywords (fallback)
            if any(term in text for term in ("hit", "reach", "above", "record", "high", "price")):
                score += 0.15
    elif condition_type == "election" and any(
        term in text for term in ("poll", "vote", "election", "lead", "wins")
    ):
        score += 0.15
    elif condition_type == "announcement_or_approval" and any(
        term in text for term in ("approve", "approval", "announce", "launch", "release")
    ):
        score += 0.15

    return round(max(0.0, min(1.0, score)), 3)


def compute_window_alignment(article: dict[str, Any], semantics: dict[str, Any]) -> float:
    """Compute window alignment score.
    
    Returns 1.0 (neutral) if article is within market deadline.
    Returns 0.5 (penalty) if article is clearly after market deadline.
    Returns 1.0 (neutral) if no explicit time window can be parsed.
    
    Initial version only handles explicit years/months, NOT fuzzy expressions
    like 'soon', 'later this year', etc.
    
    This factor is applied OUTSIDE the 0.35 floor in weighted_score calculation
    (Trap L3 fix), ensuring expired articles are penalized in direction voting.
    """
    deadline = semantics.get("deadline")
    if not deadline:
        return 1.0  # No deadline, neutral
    
    article_published = article.get("published", "")
    if not article_published:
        return 1.0  # No article date, neutral
    
    # Extract years
    article_year = _extract_year(article_published)
    deadline_year = _extract_year(deadline)
    
    if not article_year or not deadline_year:
        return 1.0  # Can't parse years, neutral
    
    # Article year > deadline year -> clearly expired
    if article_year > deadline_year:
        return 0.5
    
    # Same year: check months if available
    if article_year == deadline_year:
        article_month = _extract_month(article_published)
        deadline_month = _extract_month(deadline)
        
        if article_month and deadline_month and article_month > deadline_month:
            return 0.5
    
    return 1.0  # Within window or same time, neutral


def _extract_year(text: str) -> int | None:
    """Extract 4-digit year from text."""
    match = re.search(r'\b(20\d{2})\b', text)
    if match:
        return int(match.group(1))
    return None


def _extract_month(text: str) -> int | None:
    """Extract month number (1-12) from text.
    
    Handles: 'January', 'Jan', '01', '1', etc.
    """
    month_names = {
        'january': 1, 'jan': 1,
        'february': 2, 'feb': 2,
        'march': 3, 'mar': 3,
        'april': 4, 'apr': 4,
        'may': 5,
        'june': 6, 'jun': 6,
        'july': 7, 'jul': 7,
        'august': 8, 'aug': 8,
        'september': 9, 'sep': 9, 'sept': 9,
        'october': 10, 'oct': 10,
        'november': 11, 'nov': 11,
        'december': 12, 'dec': 12,
    }
    
    text_lower = text.lower()
    
    # Try month names first
    for name, num in month_names.items():
        if name in text_lower:
            return num
    
    # Try numeric month (MM or M)
    match = re.search(r'\b(0?[1-9]|1[0-2])\b', text)
    if match:
        return int(match.group(1))
    
    return None
