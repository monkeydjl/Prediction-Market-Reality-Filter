"""historical_matching_service.py
==============================
Historical event matching: given an event, find prior events in the store that
resemble it, as precedent context for a human reviewer.

Similarity is the max of two deterministic Jaccard signals:
  - title-token overlap (lowercase alphanumeric tokens, min 3 chars, stopwords
    dropped) - the original signal
  - entity overlap (LLM-extracted key subjects, raw form, case-insensitive set
    membership) - the entity-aware signal

No LLM, no network at match time - pure ranking over the stored event entries
passed in. The entities come from the analysis engine (record.semantics.entities).

Honest scope: events that are resolved carry an outcome, but a match surfaces a
similar past event and how its probability moved - a precedent signal. Canonical
entity resolution (alias merging, e.g. BTC = Bitcoin) is not applied; entities
are matched by raw surface form.

Event vocabulary only - no trading terms.
"""

import re
from typing import Any

from app.utils.text_match import token_overlap

_STOPWORDS = frozenset({
    "will", "the", "be", "by", "of", "in", "to", "an", "and", "or", "for", "on",
    "at", "is", "are", "was", "were", "this", "that", "with", "from", "as", "it",
    "its", "his", "her", "their", "than", "then", "before", "after", "over",
    "under", "into", "new", "not",
})

# Common CJK function/particle characters that carry little semantic signal.
# Single-char CJK tokenization would otherwise let these dominate overlap
# (e.g. two unrelated titles sharing 会/吗/的 get a spurious ~0.15 Jaccard).
# Tuned small on purpose; full Chinese stopword lists are a later refinement.
_CJK_STOPWORDS = frozenset(
    "的吗了呢吧啊呀哦么是的不在有和就也都这那一个上下月底年月日"
    "会能可以要还又再已已经把被让给向着跟同与及或并"
)


def find_similar(
    query_text: str,
    stored_entries: list[dict[str, Any]],
    limit: int = 5,
    exclude_event_id: str | None = None,
    query_entities: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank stored event entries by similarity to the query event.

    Similarity is the max of two Jaccard signals:
      - title-token overlap (the original signal; ASCII tokens, min 3 chars)
      - entity overlap (entities are LLM-extracted key subjects; raw form,
        case-insensitive set membership)

    Taking the max means a strong entity match rescues a weak title match
    (same event, different phrasing) and vice versa. When neither side has
    entities, the entity signal is 0 and the result is pure title matching
    (backward compatible). `stored_entries` are event-store entries
    ({event_id, record, last_updated}). Excludes `exclude_event_id` and
    zero-overlap matches; returns the top `limit` as compact precedent dicts.
    """
    query_tokens = _tokenize(query_text)
    query_entity_set = {
        str(entity).strip().lower()
        for entity in (query_entities or [])
        if str(entity or "").strip()
    }
    if not query_tokens and not query_entity_set:
        return []

    scored: list[dict[str, Any]] = []
    for entry in stored_entries:
        event_id = entry.get("event_id")
        if not event_id or event_id == exclude_event_id:
            continue
        record = entry.get("record") or {}
        title = str(record.get("event_title", "") or "")
        title_sim = _title_similarity(query_tokens, _tokenize(title))
        stored_semantics = record.get("semantics") or {}
        stored_entities = {
            str(entity).strip().lower()
            for entity in (stored_semantics.get("entities") or [])
            if str(entity or "").strip()
        }
        entity_sim = token_overlap(query_entity_set, stored_entities)
        similarity = max(title_sim, entity_sim)
        if similarity <= 0:
            continue
        probability = record.get("probability") or {}
        scored.append({
            "event_id": event_id,
            "event_title": title,
            "similarity": round(similarity, 3),
            "estimated_probability": probability.get("estimated"),
            "change": probability.get("change"),
            "direction": probability.get("direction"),
            "last_updated": entry.get("last_updated"),
        })

    scored.sort(key=lambda match: match["similarity"], reverse=True)
    return scored[:limit]


def _tokenize(text: Any) -> set[str]:
    """Tokenize for similarity matching.

    ASCII/Latin runs are whole words (min 3 chars, stopwords dropped). Non-ASCII
    word characters (CJK, etc.) become single characters, because Chinese-like
    languages are not space-delimited. ASCII-only input is unchanged from the
    previous ``[a-z0-9]+`` regex, so the locked Jaccard values in tests hold.
    """
    tokens: set[str] = set()
    for word in re.findall(r"\w+", str(text or "").lower(), re.UNICODE):
        if re.fullmatch(r"[a-z0-9]+", word):
            if len(word) >= 3 and word not in _STOPWORDS:
                tokens.add(word)
        else:
            for ch in word:
                if not ch.isascii() and not ch.isspace() and ch not in _CJK_STOPWORDS:
                    tokens.add(ch)
    return tokens


def _title_similarity(a: set[str], b: set[str]) -> float:
    intersection = a & b
    if _has_cjk_token(a) and _has_cjk_token(b) and len(intersection) < 2:
        return 0.0
    return token_overlap(a, b)


def _has_cjk_token(tokens: set[str]) -> bool:
    return any(_is_cjk_char(token) for token in tokens)


def _is_cjk_char(token: str) -> bool:
    if len(token) != 1:
        return False
    codepoint = ord(token)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )
