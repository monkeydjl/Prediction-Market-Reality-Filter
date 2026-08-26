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
     Opinion > Predict.fun > curated sports and Metaculus > Open Web), preserving
     the caller's round-robin order for non-duplicates. The market platforms are
     recognised by the same ``settings.*_SOURCE_NAME`` fields their adapters
     stamp onto a candidate, so renaming a source through the environment moves
     its rank with it.
  2. Walk the candidate list, keeping a candidate unless it duplicates one already
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

from app.core.config import settings
from app.utils.text_match import normalize, tokenize, token_overlap


# Source priority (lower = kept first). Market sources carry a market baseline
# and volume/liquidity, so they win on duplicates. Curated events are structured
# and human-authored, so they outrank open-web LLM extractions while still
# staying below markets.
#
# These are the ``settings`` attributes the adapters read for the platform name
# they stamp on each candidate (`kalshi_event_source.py:146` and its siblings) --
# not the names themselves. Repeating the default spellings here is what the
# ranking used to do, and it meant an operator who set KALSHI_SOURCE_NAME got
# that spelling on every Kalshi record while this table still looked for
# "Kalshi": the source stopped being recognised at all and fell to
# _UNKNOWN_PRIORITY, i.e. *below* Open Web, so an LLM-reworded news item evicted
# a real market with a real price. Order in this tuple is the ranking.
_MARKET_PRIORITY_SETTINGS: tuple[str, ...] = (
    "KALSHI_SOURCE_NAME",
    "LIMITLESS_SOURCE_NAME",
    "OPINION_SOURCE_NAME",
    "PREDICT_FUN_SOURCE_NAME",
)
# polymarket_event_source hardcodes its platform name -- it has no setting -- and
# it is the primary market source, so it ranks ahead of the tuple above.
_LITERAL_MARKET_NAMES: tuple[str, ...] = ("Polymarket",)

# Curated, human-authored sources: no traded price, but structured rather than
# LLM-reworded, so they rank below every market and above Open Web. Metaculus
# publishes a community forecast rather than a market price, which is why it sits
# in this tier and not in the market tier above.
_CURATED_PLATFORM_SETTINGS: tuple[str, ...] = ("METACULUS_SOURCE_NAME",)

_SPORTS_EVENT_TYPE = "sports_event"
_OPEN_WEB_TYPE = "open_web"

# Market ranks occupy 0 .. _CURATED_PRIORITY - 1.
_CURATED_PRIORITY = len(_LITERAL_MARKET_NAMES) + len(_MARKET_PRIORITY_SETTINGS)
_OPEN_WEB_PRIORITY = _CURATED_PRIORITY + 1

# Market-vs-market duplicate threshold (strict; market questions are close).
MARKET_THRESHOLD = 0.82
# Any-side-open-web duplicate threshold (loose; LLM-reworded questions diverge).
CROSS_THRESHOLD = 0.6
# Priority assigned to a platform none of the tables above name (e.g. the retired
# Manifold source, or a record from a platform this build has no adapter for).
# Below Open Web so known sources always win.
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


def _platform_token(value: str) -> str:
    """Normalize a platform name for comparison.

    ``casefold``, not ``lower``: these names come from the environment, so an
    operator writing ``PREDICT_FUN_SOURCE_NAME=predict.fun`` is naming the same
    platform. An exact match would have dropped it to _UNKNOWN_PRIORITY over the
    capital letter.
    """
    return value.strip().casefold()


def _platform_ranks() -> dict[str, int]:
    """Platform token -> priority, resolved from the settings in force now.

    Built per call rather than at import: the settings object is patched in tests
    and read from the environment at startup, and a module-level dict would pin
    whichever spelling happened to be live when this module was first imported.
    """
    names = list(_LITERAL_MARKET_NAMES) + [
        str(getattr(settings, attr, "") or "")
        for attr in _MARKET_PRIORITY_SETTINGS
    ]
    # enumerate over the full list before filtering, so an unset source name
    # leaves a gap instead of promoting every source below it.
    ranks = {
        _platform_token(name): index
        for index, name in enumerate(names)
        if name.strip()
    }
    for attr in _CURATED_PLATFORM_SETTINGS:
        token = _platform_token(str(getattr(settings, attr, "") or ""))
        if token:
            ranks.setdefault(token, _CURATED_PRIORITY)
    return ranks


def _priority(candidate: dict[str, Any]) -> int:
    """Source priority: markets, curated sports and Metaculus, Open Web, unknown."""
    source = candidate.get("source") or {}
    source_type = source.get("type")
    if source_type == _SPORTS_EVENT_TYPE:
        return _CURATED_PRIORITY
    if source_type == _OPEN_WEB_TYPE:
        return _OPEN_WEB_PRIORITY
    platform = source.get("platform")
    if isinstance(platform, str):
        rank = _platform_ranks().get(_platform_token(platform))
        if rank is not None:
            return rank
    return _UNKNOWN_PRIORITY


def _is_open_web(candidate: dict[str, Any]) -> bool:
    return (candidate.get("source") or {}).get("type") == _OPEN_WEB_TYPE
