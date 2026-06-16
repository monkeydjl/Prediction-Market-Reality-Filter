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

from typing import Any


def classify_evidence(
    market_question: str,
    article: dict[str, Any],
    semantics: dict[str, Any],
) -> dict[str, Any]:
    text = f"{article['title']} {article['description']}".lower()
    question = market_question.lower()
    direction = infer_direction(question, text)
    resolution_relevance = score_resolution_relevance(text, semantics)
    weighted_score = (
        article["quality_score"]
        * article["relevance_score"]
        * article["source_quality"]
        * max(0.35, resolution_relevance)
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


def infer_direction(question: str, text: str) -> str:
    positive_terms = (
        "wins", "win", "passes", "passed", "approved", "approval",
        "launches", "confirmed", "confirms", "raises", "rise", "rises",
        "surges", "leads", "ahead", "beats", "growth", "record high",
    )
    negative_terms = (
        "loses", "lose", "fails", "failed", "rejected", "denied",
        "cancelled", "canceled", "delay", "delayed", "falls", "fall",
        "drops", "behind", "misses", "lawsuit", "investigation",
    )

    yes_positive = not any(term in question for term in (" not ", " fail", " lose", " below"))
    positive_hits = sum(1 for term in positive_terms if term in text)
    negative_hits = sum(1 for term in negative_terms if term in text)

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

    if entities:
        entity_hits = sum(1 for entity in entities if entity in text)
        score += min(0.35, entity_hits * 0.12)
    if threshold and threshold.lower() in text:
        score += 0.25
    if deadline and deadline.lower() in text:
        score += 0.15
    if condition_type == "threshold" and any(
        term in text for term in ("hit", "reach", "above", "record", "high", "price")
    ):
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
