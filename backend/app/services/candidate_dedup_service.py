"""candidate_dedup_service.py
============================
Cross-source candidate-event deduplication.

The same real-world event can surface as a candidate from multiple sources -
e.g. a Polymarket question and an Open-Web extraction of the same story. Left
alone, discover analyzes both, producing two records that rank side by side
and double-count the event. This module drops cross-source duplicates at the
candidate-pool stage, before any LLM analysis runs, so it saves LLM calls and
keeps the top-N list clean.

Strategy: priority-aware incremental build.

  1. Compare candidates by source priority (Polymarket > Kalshi > Limitless >
     Opinion > Predict.fun > Open Web), preserving the caller's round-robin
     order for non-duplicates.
  2. Walk the sorted list, keeping a candidate unless it duplicates one already
     accepted. Because higher-priority candidates are seen first, "keep the
     higher-priority one" falls out for free - no replacement logic needed.

Similarity is token-set Jaccard (reusing app.utils.text_match's normalize /
tokenize / token_overlap). The threshold is tiered:

  - structured-vs-structured: MARKET_THRESHOLD (0.82). Market and curated
    structured questions can share repeated domain terms, so a strict threshold
    avoids merging genuinely different events. This matches auto-resolve's
    threshold.
  - any side open-web: CROSS_THRESHOLD (0.6). Open-Web questions are LLM-reworded
    from article text, so they diverge more from structured phrasing; a looser
    threshold is needed to catch the same event across that gap.

Pure and deterministic: a candidate list in, a smaller candidate list out.
Reuses text_match's token utilities but owns its threshold logic (find_match's
0.82 is single-tier and does not fit the market/open-web split).
"""

from typing import Any

from app.utils.text_match import normalize, tokenize, token_overlap


# Source priority (lower = kept first). Market sources carry a market baseline
# and volume/liquidity, so they win on duplicates. Curated sports events are
# structured and human-authored, so they outrank open-web LLM extractions while
# still staying below markets.
_SOURCE_PRIORITY: dict[str, int] = {
    "Polymarket": 0,
    "Kalshi": 1,
    "Limitless": 2,
    "Opinion": 3,
    "Predict.fun": 4,
}
_SPORTS_EVENT_TYPE = "sports_event"
_OPEN_WEB_TYPE = "open_web"

# Market-vs-market duplicate threshold (strict; market questions are close).
MARKET_THRESHOLD = 0.82
# Any-side-open-web duplicate threshold (loose; LLM-reworded questions diverge).
CROSS_THRESHOLD = 0.6
# Priority assigned to any source not in _SOURCE_PRIORITY (e.g. an unknown
# platform or a future source). Below Open Web so known sources always win.
_UNKNOWN_PRIORITY = 99


def dedupe_candidates(
    candidates: list[dict[str, Any]],
    market_threshold: float = MARKET_THRESHOLD,
    cross_threshold: float = CROSS_THRESHOLD,
) -> list[dict[str, Any]]:
    """Drop cross-source duplicate candidates, keeping the higher-priority one.

    Walks candidates in input order (so the caller's round-robin ordering is
    preserved for non-duplicates). When a candidate duplicates one already
    accepted, the higher-priority one wins: if the new candidate outranks the
    accepted one, it replaces it (in-place, keeping the position); otherwise
    the new candidate is dropped. Pure; the caller still applies its own cap.
    """
    accepted: list[dict[str, Any]] = []
    accepted_tokens: list[set[str]] = []
    for candidate in candidates:
        question = candidate.get("question") or ""
        tokens = set(tokenize(normalize(question)))
        candidate_is_open_web = _is_open_web(candidate)
        candidate_priority = _priority(candidate)
        duplicate_index = None
        for index, (prior_tokens, prior_is_open_web) in enumerate(
            zip(accepted_tokens, (_is_open_web(a) for a in accepted))
        ):
            threshold = (
                cross_threshold
                if candidate_is_open_web or prior_is_open_web
                else market_threshold
            )
            if token_overlap(tokens, prior_tokens) >= threshold:
                duplicate_index = index
                break
        if duplicate_index is None:
            accepted.append(candidate)
            accepted_tokens.append(tokens)
        elif candidate_priority < _priority(accepted[duplicate_index]):
            # New candidate outranks the accepted duplicate: replace in place
            # so the round-robin position is preserved but the better source
            # is kept.
            accepted[duplicate_index] = candidate
            accepted_tokens[duplicate_index] = tokens
        # else: new candidate is a lower-priority duplicate -> drop it.
    return accepted


def _priority(candidate: dict[str, Any]) -> int:
    """Source priority: markets, curated sports, Open Web, then unknown."""
    source = candidate.get("source") or {}
    source_type = source.get("type")
    if source_type == _SPORTS_EVENT_TYPE:
        return len(_SOURCE_PRIORITY)
    if source_type == _OPEN_WEB_TYPE:
        return len(_SOURCE_PRIORITY) + 1
    platform = source.get("platform")
    if isinstance(platform, str) and platform in _SOURCE_PRIORITY:
        return _SOURCE_PRIORITY[platform]
    return _UNKNOWN_PRIORITY


def _is_open_web(candidate: dict[str, Any]) -> bool:
    return (candidate.get("source") or {}).get("type") == _OPEN_WEB_TYPE
