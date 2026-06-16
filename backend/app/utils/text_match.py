"""text_match.py
================
Shared text matching utilities for question-similarity matching.

Extracted verbatim from auto_resolve_service (which itself was the only
consumer). Used to match a local question against a pool of candidate
questions - e.g. matching a stored event/prediction against resolved
Polymarket markets by question text.

The matching strategy is: normalize whitespace + casing, tokenize on
alphanumeric runs (min 2 chars), drop a small stopword set, and compare token
sets by Jaccard overlap. An exact normalized-key match wins; otherwise the
best fuzzy overlap above FUZZY_THRESHOLD wins; otherwise no match.

Note: historical_matching_service has its OWN tokenize rules (min 3 chars, a
different stopword set, no threshold - it ranks by similarity rather than
matching). Those rules are deliberately NOT shared here: the two features have
different semantics (find-similar vs exact-match) and historical_matching has
tests locking its exact Jaccard values.
"""

import re
from typing import Any


STOPWORDS: set[str] = {
    "will", "the", "this", "that", "with", "from", "about", "after",
    "before", "over", "under", "yes", "no", "and", "or", "for", "to",
    "of", "in", "on", "by", "a", "an", "be", "is", "are", "was",
}

# Minimum Jaccard overlap for a fuzzy (non-exact) match. Below this, no match.
FUZZY_THRESHOLD = 0.82


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace."""
    return " ".join((text or "").lower().split()).strip()


def tokenize(text: str) -> list[str]:
    """Lowercase tokens, stopwords removed.

    ASCII/Latin runs are tokenized as whole words (min 2 chars). Non-ASCII
    word characters (CJK, etc.) are tokenized as single characters, because
    languages like Chinese are not space-delimited - a single character is the
    smallest comparable unit, and two Chinese event titles sharing characters
    (e.g. 比特币) should produce overlap. This keeps ASCII-only input behavior
    identical to the previous ``[a-z0-9]{2,}`` regex, so English matching is
    unchanged.
    """
    tokens: list[str] = []
    for word in re.findall(r"\w+", (text or "").lower(), re.UNICODE):
        if re.fullmatch(r"[a-z0-9]+", word):
            if len(word) >= 2 and word not in STOPWORDS:
                tokens.append(word)
        else:
            for ch in word:
                if not ch.isascii() and not ch.isspace():
                    tokens.append(ch)
    return tokens


def token_overlap(a: set[str], b: set[str]) -> float:
    """Jaccard overlap of two token sets; 0.0 if either is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def build_index(
    items: list[dict[str, Any]],
    question_key: str = "question",
    value_key: str = "actual_outcome",
) -> dict[str, tuple[str, float, set[str]]]:
    """Index candidate items by their normalized question.

    Returns {normalized_question: (original_question, value, token_set)}. The
    token_set is pre-computed once at build time so find_match does not re-tokenize
    every index key on every query - that matters on the auto-resolve path where
    one index (~200 resolved markets) is queried once per unresolved event.

    Items with an empty question or a non-numeric value are skipped. When two
    items normalize to the same key, the later one wins.
    """
    index: dict[str, tuple[str, float, set[str]]] = {}
    for item in items:
        question = item.get(question_key, "")
        if not question:
            continue
        try:
            value = float(item.get(value_key, 0.0))
        except (TypeError, ValueError):
            continue
        key = normalize(question)
        if key:
            index[key] = (question, value, set(tokenize(key)))
    return index


def find_match(
    question: str,
    index: dict[str, tuple[str, float, set[str]]],
) -> tuple[str, float, float] | None:
    """Find the best candidate match for `question` in `index`.

    Returns (matched_question, value, score) or None. An exact normalized-key
    match returns score 1.0; otherwise the best fuzzy Jaccard overlap at or
    above FUZZY_THRESHOLD wins; otherwise None. Empty/whitespace question
    returns None.

    The index values carry a pre-tokenized set (see build_index), so this is
    O(index size) per call rather than O(index size * tokenization cost).
    """
    if not question:
        return None
    norm_q = normalize(question)
    tokens_q = set(tokenize(norm_q))

    if norm_q in index:
        original, value, _ = index[norm_q]
        return original, value, 1.0

    best_score = 0.0
    best_entry: tuple[str, float] | None = None
    for original, value, index_tokens in index.values():
        score = token_overlap(tokens_q, index_tokens)
        if score > best_score:
            best_score = score
            best_entry = (original, value)

    if best_score >= FUZZY_THRESHOLD and best_entry is not None:
        return best_entry[0], best_entry[1], best_score
    return None
