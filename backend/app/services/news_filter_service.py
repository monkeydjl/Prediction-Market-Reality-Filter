import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from app.services.evidence_scoring_service import average_field, build_evidence_profile
from app.services.market_semantics_service import (
    build_semantics_context,
    parse_market_semantics,
)
from app.utils.text_match import word_in_text


TRUSTED_SOURCES = (
    "reuters",
    "associated press",
    "ap news",
    "bloomberg",
    "financial times",
    "wall street journal",
    "wsj",
    "cnbc",
    "the guardian",
    "bbc",
    "politico",
    "axios",
    "the verge",
)

LOW_QUALITY_TERMS = (
    "rumor",
    "unconfirmed",
    "allegedly",
    "speculation",
    "shocking",
    "you won't believe",
    "bombshell",
    "insane",
    "meme",
    "satire",
    "parody",
    "the onion",
    "babylon bee",
    "conspiracy",
    "hoax",
)

STOPWORDS = {
    "will", "the", "this", "that", "with", "from", "into", "about",
    "after", "before", "over", "under", "market", "polymarket", "yes",
    "no", "and", "or", "for", "to", "of", "in", "on", "by", "a", "an",
}


def filter_news_for_market(
    market_question: str,
    articles: list[dict[str, Any]],
    max_items: int = 6,
) -> dict[str, Any]:
    scored = []
    rejected = []
    semantics = parse_market_semantics(market_question)

    for article in articles:
        normalized = normalize_article(article)
        score, reasons = score_article(market_question, normalized, semantics)
        normalized["quality_score"] = score
        normalized["quality_reasons"] = reasons

        if score < 0.35:
            rejected.append({
                "title": normalized["title"],
                "score": score,
                "reasons": reasons,
            })
            continue

        scored.append(normalized)

    scored.sort(
        key=lambda item: (
            item["quality_score"],
            item["source_quality"],
            item["relevance_score"],
        ),
        reverse=True,
    )
    selected = dedupe_articles(scored)[:max_items]
    evidence_profile = build_evidence_profile(market_question, selected)

    return {
        "articles": selected,
        "context": build_news_context(selected, evidence_profile, semantics),
        "evidence_profile": evidence_profile,
        "market_semantics": semantics,
        "summary": {
            "input_count": len(articles),
            "selected_count": len(selected),
            "rejected_count": len(rejected),
            "average_quality": average_quality(selected),
            "evidence_strength": evidence_profile["evidence_strength"],
            "evidence_direction": evidence_profile["evidence_direction"],
            "conflict_score": evidence_profile["conflict_score"],
            "freshness_score": evidence_profile["freshness_score"],
            "rejected": rejected[:10],
        },
    }


def normalize_article(article: dict[str, Any]) -> dict[str, Any]:
    title = str(article.get("title", "") or "").strip()
    description = str(
        article.get("description")
        or article.get("summary")
        or article.get("desc")
        or ""
    ).strip()
    source = str(article.get("source", "") or article.get("publisher", "") or "").strip()
    published = str(
        article.get("published")
        or article.get("published_date")
        or article.get("published date")
        or ""
    ).strip()

    if not source:
        source = infer_source(title)

    url = str(article.get("url") or article.get("link") or "").strip()
    kind = str(article.get("kind") or "").strip().lower()
    if kind not in {"news", "official", "market"}:
        kind = "news"

    return {
        "title": title,
        "description": description,
        "source": source,
        "published": published,
        "url": url,
        "kind": kind,
        "source_quality": score_source_quality(source, title),
        "age_score": score_age(published),
        # Carried through from semantic_relevance_service (when embeddings are
        # enabled) so score_article can blend it with keyword relevance.
        "semantic_relevance": article.get("semantic_relevance"),
    }


def score_article(
    market_question: str,
    article: dict[str, Any],
    semantics: dict[str, Any] | None = None,
) -> tuple[float, list[str]]:
    text = f"{article['title']} {article['description']}".lower()
    reasons = []

    relevance = relevance_score(market_question, text, semantics)
    # When semantic relevance is available (embeddings enabled), take the
    # stronger of the two signals: semantic rescues relevant articles that share
    # little surface vocabulary; keyword rescues semantic noise.
    semantic = article.get("semantic_relevance")
    if isinstance(semantic, (int, float)):
        relevance = max(relevance, float(semantic))
    article["relevance_score"] = relevance
    if relevance < 0.2:
        reasons.append("low_relevance")

    low_quality_hits = [term for term in LOW_QUALITY_TERMS if term in text]
    penalty = min(0.45, len(low_quality_hits) * 0.09)
    if low_quality_hits:
        reasons.append("low_quality_terms:" + ",".join(low_quality_hits[:3]))

    if len(article["title"]) < 12:
        penalty += 0.1
        reasons.append("title_too_short")

    score = (
        relevance * 0.45
        + article["source_quality"] * 0.3
        + article["age_score"] * 0.15
        + 0.1
        - penalty
    )
    return round(max(0.0, min(1.0, score)), 3), reasons


def relevance_score(
    market_question: str,
    news_text: str,
    semantics: dict[str, Any] | None = None,
) -> float:
    semantic_tokens = []
    if semantics:
        # Only the event's concrete entities add real signal here. The yes/no
        # conditions are generic boilerplate templates ("The referenced metric
        # reaches or exceeds ... does not ...") whose filler words (referenced,
        # metric, reaches, exceeds, occurs, does, not) match unrelated news and
        # inflate relevance - e.g. a politics article scoring 0.5 on a Bitcoin
        # question. The threshold / deadline already surface as entity tokens, so
        # dropping the templates removes noise without losing event-specific signal.
        semantic_tokens = list(semantics.get("entities", []))

    question_tokens = list(dict.fromkeys(
        extract_keywords(market_question) + semantic_tokens
    ))
    if not question_tokens:
        return 0.0

    # Word-boundary match instead of a bare substring, so a short token like
    # "eth" no longer matches inside "hegseth" / "whether", and "end" no longer
    # matches inside "transgender". Tokenizing the article already isolated
    # whole words; this stops a question token from bleeding across word
    # boundaries in the article text. (Stemming is intentionally NOT supported
    # here: a "rate" token should not match "rates" any more than "eth" should
    # match "hegseth" - both are the same substring-matching failure mode.)
    hits = sum(1 for token in question_tokens if word_in_text(token, news_text))
    return max(0.0, min(1.0, hits / min(len(question_tokens), 6)))


def _word_in(token: str, text: str) -> bool:
    """Deprecated alias - use word_in_text from app.utils.text_match directly."""
    return word_in_text(token, text)


def extract_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9]{3,}", (text or "").lower())
    return [
        token
        for token in tokens
        if token not in STOPWORDS and not token.isdigit()
    ][:12]


def score_source_quality(source: str, title: str) -> float:
    haystack = f"{source} {title}".lower()
    if any(source_name in haystack for source_name in TRUSTED_SOURCES):
        return 0.9
    if source:
        return 0.55
    return 0.35


def score_age(published: str) -> float:
    if not published:
        return 0.5

    try:
        parsed = parsedate_to_datetime(published)
    except (TypeError, ValueError):
        return 0.5

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    age_hours = (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
    if age_hours <= 24:
        return 1.0
    if age_hours <= 72:
        return 0.8
    if age_hours <= 168:
        return 0.6
    return 0.35


def dedupe_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    unique = []

    for article in articles:
        key = normalize_key(article["title"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(article)

    return unique


def build_news_context(
    articles: list[dict[str, Any]],
    evidence_profile: dict[str, Any],
    semantics: dict[str, Any],
) -> str:
    semantics_header = build_semantics_context(semantics)
    evidence_header = (
        "EVIDENCE PROFILE\n"
        f"DIRECTION: {evidence_profile['evidence_direction']}\n"
        f"STRENGTH: {evidence_profile['evidence_strength']}\n"
        f"CONFLICT: {evidence_profile['conflict_score']}\n"
        f"FRESHNESS: {evidence_profile['freshness_score']}\n"
        f"RESOLUTION_RELEVANCE: {evidence_profile['resolution_relevance_score']}\n"
        f"SOURCE_COUNT: {evidence_profile['source_count']}"
    )
    news_items = "\n\n".join(
        "NEWS ITEM\n"
        f"SOURCE: {article['source']}\n"
        f"QUALITY: {article['quality_score']}\n"
        f"RELEVANCE: {article['relevance_score']}\n"
        f"TITLE: {article['title']}\n"
        f"DESCRIPTION: {article['description']}"
        for article in articles
    )
    return f"{semantics_header}\n\n{evidence_header}\n\n{news_items}".strip()


def average_quality(articles: list[dict[str, Any]]) -> float:
    return average_field(articles, "quality_score")


def normalize_key(text: str) -> str:
    return " ".join(extract_keywords(text)[:8])


def infer_source(title: str) -> str:
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return ""
