"""Evidence aggregation service (Stage: evidence decomposition).

Pure-function layer that transforms the per-article fields emitted by
``analyze_sentiment`` into the ``EventRecord.evidence_breakdown`` audit list.

This is an EXPLANATION layer only. It MUST NOT feed back into
``evidence_profile``, ``ai_probability``, ``regression_to_market``, or
``actionable_recommendation`` calculations.

Filtering rules:
- direction missing/unknown/neutral -> skip
- strength < 0.2 -> skip
- index missing/non-integer/bool/negative/out-of-range -> skip
- original article missing/empty title -> skip

Normalization:
- strength/credibility clamped to [0, 1]
- credibility missing -> 0.5
- source missing -> "unknown"
- title truncated to 200 chars
- rationale_zh: stringified, stripped, banned-word replaced, truncated to 300 chars

Output order preserves the sentiment_articles order (no re-sorting).
"""
import re
from typing import Any

# Banned trading terms and their event-vocabulary replacements. Case-insensitive
# on the keys. Replacements use event vocabulary (YES/NO) per the decision-report
# invariant locked by test_report_uses_event_vocabulary_only.
_BANNED_WORD_REPLACEMENTS: dict[str, str] = {
    "long": "支持 YES",
    "buy": "支持 YES",
    "short": "支持 NO",
    "sell": "支持 NO",
    "position": "配置",
    "kelly": "风险预算",
    "order": "决策",
}

_TITLE_MAX = 200
_RATIONALE_MAX = 300
_STRENGTH_THRESHOLD = 0.2
_DEFAULT_CREDIBILITY = 0.5
_VALID_DIRECTIONS = {"support", "oppose"}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _is_valid_index(index: Any, length: int) -> bool:
    """True only for genuine non-negative ints in range. bool is rejected
    explicitly because bool is a subclass of int in Python."""
    if isinstance(index, bool):
        return False
    if not isinstance(index, int):
        return False
    if index < 0 or index >= length:
        return False
    return True


def _filter_banned_words(text: str) -> str:
    """Replace banned trading terms with event-vocabulary equivalents.

    Case-insensitive matching on the banned word; replacement preserves the
    rest of the string. Word-boundary safe is NOT enforced because the banned
    terms rarely appear as substrings of legitimate Chinese text; the simple
    lower() + replace is sufficient for the禁词 invariant.
    """
    if not text:
        return ""
    result = text
    lowered = result.lower()
    for banned, replacement in _BANNED_WORD_REPLACEMENTS.items():
        if banned in lowered:
            # Case-insensitive replace: rebuild the string segment by segment.
            # Simple approach: lower the whole string, replace, but that loses
            # original case. Better: find all occurrences case-insensitively
            # and replace each with the replacement string.
            pattern = re.compile(re.escape(banned), re.IGNORECASE)
            result = pattern.sub(replacement, result)
            lowered = result.lower()
    return result


def aggregate_evidence_breakdown(
    sentiment_articles: list[dict[str, Any]] | None,
    original_articles: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Transform LLM sentiment article output into EventRecord.evidence_breakdown.

    Args:
        sentiment_articles: per-article output from ``analyze_sentiment`` LLM
            call. Each item may carry ``index``, ``evidence_direction``,
            ``evidence_strength``, ``source_credibility``, ``rationale_zh``.
        original_articles: the filtered news articles (from
            ``filter_news_for_market``) that were fed to the LLM. Used as the
            source of truth for ``source`` and ``title`` (LLM self-reported
            source/title is not trusted).

    Returns:
        List of breakdown items, each with keys ``source``, ``title``,
        ``direction``, ``strength``, ``credibility``, ``rationale_zh``.
        Order preserves the input ``sentiment_articles`` order. Items that
        fail validation are skipped (per-item fail-closed).
    """
    if not sentiment_articles or not original_articles:
        return []

    breakdown: list[dict[str, Any]] = []
    original_len = len(original_articles)

    for item in sentiment_articles:
        if not isinstance(item, dict):
            continue

        index = item.get("index")
        if not _is_valid_index(index, original_len):
            continue

        direction = item.get("evidence_direction")
        if not isinstance(direction, str) or direction not in _VALID_DIRECTIONS:
            continue

        strength_raw = item.get("evidence_strength", 0)
        try:
            strength = _clamp(float(strength_raw), 0.0, 1.0)
        except (TypeError, ValueError):
            continue
        if strength < _STRENGTH_THRESHOLD:
            continue

        original = original_articles[index]
        title = str(original.get("title") or "").strip()
        if not title:
            continue

        credibility_raw = item.get("source_credibility", _DEFAULT_CREDIBILITY)
        try:
            credibility = _clamp(float(credibility_raw), 0.0, 1.0)
        except (TypeError, ValueError):
            credibility = _DEFAULT_CREDIBILITY

        source = str(original.get("source") or "unknown").strip() or "unknown"

        rationale_raw = item.get("rationale_zh") or ""
        rationale = _filter_banned_words(str(rationale_raw).strip())[:_RATIONALE_MAX]

        breakdown.append({
            "source": source,
            "title": title[:_TITLE_MAX],
            "direction": direction,
            "strength": strength,
            "credibility": credibility,
            "rationale_zh": rationale,
        })

    return breakdown
