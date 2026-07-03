"""Source reliability service (Phase 4: Source Reliability overlay).

Pure-function layer that assesses the quality and diversity of news sources
backing an event's evidence, and produces a ``source_reliability`` overlay
block. Can downgrade YES/NO recommendations to WAIT when the source base is
too thin or untrustworthy.

Applies to ALL event types that have a non-empty ``evidence_breakdown``
(prediction_market, prediction_question, open_web). When
``evidence_breakdown`` is empty (e.g., sports_event with match stats), the
block is ``None`` — there is no source base to assess.

This is an audit/overlay layer. It MUST NOT feed back into
``ai_probability``, ``evidence_profile``, ``regression_to_market``,
``actionable_recommendation``, ``decision_quality``, or ``market_quality``.
The data flow is one-way:

    evidence_breakdown + evidence_items + actionable_recommendation.direction
      -> build_source_reliability
      -> source_reliability (overlay only, no writeback)

The function is synchronous and deterministic — no LLM calls, no I/O.
``settings`` is intentionally not passed; the orchestrator extracts concrete
scalar config values and passes them explicitly.

Source tier classification extends the existing
``news_filter_service.score_source_quality`` logic into a 4-tier system
(official / trusted / established / aggregator / unknown). It uses both the
normalized source display name and the URL-extracted domain — if either
matches a known pattern, the source gets that tier. This is a best-effort
heuristic; it does NOT replace the LLM's per-article ``credibility`` score
(which is preserved in ``source_breakdown[].avg_credibility``).

URL → domain extraction is a new capability introduced by Phase 4. The
existing ``EvidenceBreakdownItem.source`` field is a feed display name
(e.g., "Reuters Politics"), not a domain. Phase 4 extracts the domain from
``evidence_items[].url`` to enable domain-level diversity analysis.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Directions that can be downgraded to WAIT. WAIT/AVOID are non-directional
# (no strong stance to soften) and are never downgraded by this layer.
_STRONG_DIRECTIONS = ("YES", "NO")

# Tier scores for the weighted average.
_TIER_SCORES: dict[str, float] = {
    "official": 0.95,
    "trusted": 0.85,
    "established": 0.65,
    "aggregator": 0.35,
    "unknown": 0.20,
}

# Substring patterns for each tier. Matched against the normalized source
# name AND the extracted domain (lowercased). Order matters: earlier tiers
# take precedence (a source matching both "official" and "trusted" patterns
# gets "official").
_TIER_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "official",
        (
            "sec.gov", "federalreserve.gov", "whitehouse.gov",
            "congress.gov", "treasury.gov", "bjs.gov", "bls.gov",
            "bea.gov", "fbi.gov", "cia.gov", "cbo.gov", "gao.gov",
        ),
    ),
    (
        "trusted",
        (
            "reuters", "ap news", "associated press", "bloomberg",
            "wall street journal", "wsj", "financial times",
            "nikkei", "economist",
        ),
    ),
    (
        "established",
        (
            "coin desk", "coindesk", "decrypt", "the block",
            "cnbc", "market watch", "marketwatch",
            "bbc", "guardian", "ny times", "new york times",
            "washington post", "reuters politics", "reuters world",
            "reuters business", "reuters tech",
        ),
    ),
    (
        "aggregator",
        (
            "crypto news", "crypto-news", "bitcoin.com", "newsbtc",
            "cointelegraph", "bitcoinist", "crypto globe", "cryptoslate",
            "news.bitcoin", "ethereum world news",
        ),
    ),
)


def extract_domain(url: str) -> str:
    """Extract registrable domain from a URL.

    'https://www.reuters.com/article/...' -> 'reuters.com'
    'https://www.bbc.co.uk/news/...'       -> 'bbc.co.uk'
    Empty / invalid / missing URL           -> ''

    Strips the ``www.`` prefix and lowercases. Does NOT attempt
    registrable-domain extraction (no Public Suffix List) — keeps it simple;
    ``bbc.co.uk`` stays as ``bbc.co.uk``, not ``bbc``.
    """
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        parsed = urlparse(url.strip())
    except (ValueError, TypeError):
        return ""
    hostname = parsed.hostname or ""
    if not hostname:
        return ""
    hostname = hostname.lower()
    # Strip common leading www. prefix (not deeper subdomains like
    # api.coindesk.com — those are preserved for auditability).
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def classify_source_tier(source: str, domain: str) -> str:
    """Classify a source into one of 5 tiers.

    Tiers (highest to lowest reliability):
        - ``official`` (0.95): gov / regulator / central bank
        - ``trusted`` (0.85): major wire services + financial press
        - ``established`` (0.65): recognized news outlets
        - ``aggregator`` (0.35): crypto/news aggregators
        - ``unknown`` (0.20): empty / unrecognized

    Classification uses both the normalized source display name and the
    extracted domain — if EITHER matches a known pattern, the source gets
    that tier. Earlier tiers take precedence (first match wins).
    """
    normalized_source = _normalize_source(source)
    normalized_domain = (domain or "").lower()

    for tier, patterns in _TIER_PATTERNS:
        for pattern in patterns:
            if pattern in normalized_source or pattern in normalized_domain:
                return tier
    return "unknown"


def build_source_reliability(
    *,
    evidence_breakdown: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    raw_direction: str | None,
    enabled: bool,
    score_threshold: float,
    min_trusted_ratio: float,
    min_domain_diversity: int,
    min_sources: int,
    registry_overrides: list[dict[str, Any]] | None = None,
    domain_stats_overrides: list[dict[str, Any]] | None = None,
    domain_reliability_shrinkage_pseudocount: int = 5,
) -> dict[str, Any] | None:
    """Build the ``source_reliability`` overlay block.

    Pure function: no LLM, no I/O, no settings reads, no mutation of inputs.
    Returns ``None`` when ``evidence_breakdown`` is empty (no source base to
    assess). Returns a dict with keys: ``overall_score``, ``source_count``,
    ``domain_diversity``, ``trusted_source_ratio``, ``official_source_count``,
    ``unknown_source_ratio``, ``source_breakdown``, ``downgrade_reason``,
    ``raw_direction``, ``suggested_direction``, ``downgraded``,
    ``applied_to_displayed_direction``. When ``registry_overrides`` is not
    None, an additional key ``source_prior_affected`` is included.

    ``registry_overrides`` is an optional list of source-trust-registry rows
    (each a dict with ``pattern_type`` in {domain, source_name}, ``pattern``,
    ``tier``, ``base_trust``). When provided, the longest-prefix domain match
    or first source_name substring match overrides the source's tier and/or
    base-trust score. The registry is an OPTIONAL prior — it only adjusts the
    tier score used in the weighted average; it does NOT override event-level
    evidence conflicts. ``source_prior_affected`` is True when any override
    was applied to any source in this record (False otherwise); the key is
    omitted entirely when ``registry_overrides`` is None to preserve
    byte-identical shape to pre-Plan-4 when the registry is disabled.

    ``domain_stats_overrides`` is an optional list of projected
    domain-reliability rows (each a dict with ``domain``, ``sample_count``,
    ``correct_count``). When provided, sources not covered by the registry use
    a shrunk historical reliability score as the tier-score prior. The key
    ``domain_stats_prior_affected`` is emitted only when the parameter is not
    None; its value is True only when at least one source used a valid shrunk
    score.

    The function never raises — malformed items are skipped (best-effort),
    and missing fields default to empty/zero rather than raising.
    """
    if not isinstance(evidence_breakdown, list) or not evidence_breakdown:
        return None

    # Build a source -> url lookup from evidence_items for domain extraction.
    # Match by source display name (case-insensitive) since evidence_breakdown
    # and evidence_items may have different orderings.
    url_by_source: dict[str, str] = {}
    if isinstance(evidence_items, list):
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            source_name = str(item.get("source") or "")
            url = str(item.get("url") or "")
            if source_name and url:
                key = _normalize_source(source_name)
                if key and key not in url_by_source:
                    url_by_source[key] = url

    # Aggregate per source: count, avg credibility, avg strength, domain, tier.
    # Group by normalized source name to merge "Reuters Politics" and "Reuters".
    source_agg: dict[str, dict[str, Any]] = {}
    total_credibility: list[float] = []
    source_prior_affected = False
    domain_stats_prior_affected = False
    for item in evidence_breakdown:
        if not isinstance(item, dict):
            continue
        source_name = str(item.get("source") or "")
        if not source_name:
            source_name = "unknown"
        key = _normalize_source(source_name)
        if not key:
            key = "unknown"

        url = url_by_source.get(key, "")
        domain = extract_domain(url)
        tier = classify_source_tier(source_name, domain)

        # Apply source-trust-registry override (optional prior). The registry
        # only adjusts the tier score used as a prior weight; it does NOT
        # override event-level evidence conflicts.
        registry_matched = False
        if registry_overrides:
            override = _match_registry_override(source_name, domain, registry_overrides)
            if override is not None:
                registry_matched = True
                tier = override.get("tier") or tier
                if override.get("base_trust") is not None:
                    base_trust_override = override["base_trust"]
                else:
                    base_trust_override = None
                source_prior_affected = True
            else:
                base_trust_override = None
        else:
            base_trust_override = None

        domain_stats_score = None
        if domain_stats_overrides is not None and not registry_matched:
            stats_override = _match_domain_stats_override(domain, domain_stats_overrides)
            if stats_override is not None:
                domain_stats_score = _shrunk_reliability(
                    correct=stats_override.get("correct_count"),
                    sample=stats_override.get("sample_count"),
                    K=domain_reliability_shrinkage_pseudocount,
                )
                if domain_stats_score is not None:
                    domain_stats_prior_affected = True

        try:
            credibility = float(item.get("credibility") or 0.0)
        except (TypeError, ValueError):
            credibility = 0.0
        try:
            strength = float(item.get("strength") or 0.0)
        except (TypeError, ValueError):
            strength = 0.0

        total_credibility.append(credibility)

        if key not in source_agg:
            source_agg[key] = {
                "source": source_name,
                "domain": domain,
                "tier": tier,
                "base_trust_override": base_trust_override,
                "domain_stats_score": domain_stats_score,
                "article_count": 0,
                "credibility_sum": 0.0,
                "strength_sum": 0.0,
            }
        source_agg[key]["article_count"] += 1
        source_agg[key]["credibility_sum"] += credibility
        source_agg[key]["strength_sum"] += strength

    if not source_agg:
        return None

    # Build source_breakdown and compute aggregates.
    source_breakdown: list[dict[str, Any]] = []
    tier_counts: dict[str, int] = {}
    domains: set[str] = set()

    for key, agg in source_agg.items():
        count = agg["article_count"]
        avg_cred = round(agg["credibility_sum"] / count, 4) if count else 0.0
        avg_str = round(agg["strength_sum"] / count, 4) if count else 0.0
        source_breakdown.append({
            "source": agg["source"],
            "domain": agg["domain"],
            "tier": agg["tier"],
            "article_count": count,
            "avg_credibility": avg_cred,
            "avg_strength": avg_str,
        })
        tier_counts[agg["tier"]] = tier_counts.get(agg["tier"], 0) + count
        if agg["domain"]:
            domains.add(agg["domain"])

    source_count = len(source_breakdown)
    domain_diversity = len(domains)
    official_count = tier_counts.get("official", 0)
    trusted_count = tier_counts.get("trusted", 0)
    established_count = tier_counts.get("established", 0)
    aggregator_count = tier_counts.get("aggregator", 0)
    unknown_count = tier_counts.get("unknown", 0)
    total_articles = sum(tier_counts.values())

    # Ratios (by source count, not article count — a source with 10 articles
    # from the same domain is still one source).
    trusted_source_ratio = 0.0
    unknown_source_ratio = 0.0
    if source_count > 0:
        trusted_source_ratio = round(
            (len([s for s in source_breakdown if s["tier"] in ("official", "trusted")]))
            / source_count, 4
        )
        unknown_source_ratio = round(
            len([s for s in source_breakdown if s["tier"] == "unknown"])
            / source_count, 4
        )

    # Overall score: weighted combination.
    # weighted_avg_tier_score: average tier score weighted by article count.
    # When a registry override provided base_trust for a source, use that
    # value first; otherwise use valid domain stats before tier defaults.
    if total_articles > 0:
        weighted_tier_sum = 0.0
        for agg in source_agg.values():
            if agg.get("base_trust_override") is not None:
                prior_score = agg["base_trust_override"]
            elif agg.get("domain_stats_score") is not None:
                prior_score = agg["domain_stats_score"]
            else:
                prior_score = _TIER_SCORES.get(agg["tier"], 0.20)
            weighted_tier_sum += prior_score * agg["article_count"]
        weighted_avg_tier_score = weighted_tier_sum / total_articles
    else:
        weighted_avg_tier_score = 0.20

    domain_diversity_ratio = (
        min(domain_diversity / min_domain_diversity, 1.0)
        if min_domain_diversity > 0 else 1.0
    )
    avg_credibility = (
        round(sum(total_credibility) / len(total_credibility), 4)
        if total_credibility else 0.0
    )

    overall_score = round(
        0.40 * weighted_avg_tier_score
        + 0.25 * domain_diversity_ratio
        + 0.20 * trusted_source_ratio
        + 0.15 * avg_credibility,
        4,
    )

    # Determine downgrade: first-match-wins among the 4 rules.
    raw_dir = _normalize_direction(raw_direction)
    downgrade_reason = _evaluate_downgrade(
        raw_dir=raw_dir,
        domain_diversity=domain_diversity,
        min_domain_diversity=min_domain_diversity,
        trusted_source_ratio=trusted_source_ratio,
        min_trusted_ratio=min_trusted_ratio,
        source_count=source_count,
        min_sources=min_sources,
        overall_score=overall_score,
        score_threshold=score_threshold,
    )

    if downgrade_reason is not None and raw_dir in _STRONG_DIRECTIONS:
        suggested_direction = "WAIT"
    else:
        suggested_direction = raw_dir

    result: dict[str, Any] = {
        "overall_score": overall_score,
        "source_count": source_count,
        "domain_diversity": domain_diversity,
        "trusted_source_ratio": trusted_source_ratio,
        "official_source_count": official_count,
        "unknown_source_ratio": unknown_source_ratio,
        "source_breakdown": source_breakdown,
        "downgrade_reason": downgrade_reason,
        "raw_direction": raw_dir,
        "suggested_direction": suggested_direction,
        "downgraded": suggested_direction != raw_dir,
        "applied_to_displayed_direction": False,  # set by merge step
    }
    # source_prior_affected is only surfaced when the source-trust-registry
    # prior is in play (registry_overrides provided). When the registry is
    # disabled (the default), the block stays byte-identical to pre-Plan-4
    # (12 keys) — satisfying the Global Constraint that all new feature
    # flags default to OFF.
    if registry_overrides is not None:
        result["source_prior_affected"] = source_prior_affected
    if domain_stats_overrides is not None:
        result["domain_stats_prior_affected"] = domain_stats_prior_affected
    return result


def _normalize_source(source: str) -> str:
    """Normalize a source display name for matching.

    Lowercases and strips whitespace. Does NOT parse URLs or extract domains
    — that is done separately by ``extract_domain``. Examples:
        "Reuters Politics" -> "reuters politics"
        "CoinDesk"          -> "coindesk"
        ""                  -> ""
    """
    if not isinstance(source, str):
        return ""
    return source.strip().lower()


def _normalize_direction(direction: str | None) -> str:
    """Normalize and validate a recommendation direction.

    Returns the direction as-is when it is in {YES, NO, WAIT, AVOID}.
    Returns "WAIT" for None / empty / unrecognized values (safe default).
    """
    if not isinstance(direction, str):
        return "WAIT"
    normalized = direction.strip().upper()
    if normalized in ("YES", "NO", "WAIT", "AVOID"):
        return normalized
    return "WAIT"


def _evaluate_downgrade(
    *,
    raw_dir: str,
    domain_diversity: int,
    min_domain_diversity: int,
    trusted_source_ratio: float,
    min_trusted_ratio: float,
    source_count: int,
    min_sources: int,
    overall_score: float,
    score_threshold: float,
) -> str | None:
    """Evaluate the 4 downgrade rules in order (first-match-wins).

    Only applies to strong directions (YES/NO). Returns the Chinese reason
    string for the first matching rule, or None when no rule fires.

    Rules:
        1. domain_diversity < min_domain_diversity
        2. trusted_source_ratio < min_trusted_ratio
        3. source_count < min_sources
        4. overall_score < score_threshold
    """
    # Non-directional recommendations are never downgraded by this layer.
    if raw_dir not in _STRONG_DIRECTIONS:
        return None

    if domain_diversity < min_domain_diversity:
        return "来源域名多样性不足（单一来源回声室风险）"
    if trusted_source_ratio < min_trusted_ratio:
        return "可信来源占比过低"
    if source_count < min_sources:
        return "来源数量不足"
    if overall_score < score_threshold:
        return "来源整体可靠性低于阈值"
    return None


def _shrunk_reliability(correct: Any, sample: Any, K: int) -> float | None:
    """Return Beta(0.5K, 0.5K) posterior mean, or None when unusable."""
    if isinstance(sample, bool) or not isinstance(sample, int):
        return None
    if sample <= 0 or K <= 0:
        return None
    if isinstance(correct, bool) or not isinstance(correct, int):
        correct_int = 0
    else:
        correct_int = correct
    correct_int = max(0, min(correct_int, sample))
    return (correct_int + 0.5 * K) / (sample + K)


def _domain_suffix_matches(domain: str, pattern: str) -> bool:
    """True when domain equals pattern or is a subdomain of pattern."""
    domain_lower = (domain or "").strip().lower()
    pattern_lower = (pattern or "").strip().lower()
    if not domain_lower or not pattern_lower:
        return False
    return domain_lower == pattern_lower or domain_lower.endswith("." + pattern_lower)


def _match_domain_stats_override(
    domain: str,
    overrides: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the longest domain suffix match in domain stats overrides."""
    best: dict[str, Any] | None = None
    best_len = -1
    for entry in overrides:
        if not isinstance(entry, dict):
            continue
        pattern = entry.get("domain")
        if not isinstance(pattern, str):
            continue
        pattern = pattern.strip().lower()
        if _domain_suffix_matches(domain, pattern) and len(pattern) > best_len:
            best = entry
            best_len = len(pattern)
    return best


def _match_registry_override(
    source_name: str,
    domain: str,
    overrides: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the longest domain suffix match or first source_name substring
    match in ``overrides``. Returns the override dict or None.
    """
    best: dict[str, Any] | None = None
    best_len = -1
    domain_lower = (domain or "").lower()
    name_lower = (source_name or "").lower()
    for entry in overrides:
        ptype = entry.get("pattern_type")
        pattern = (entry.get("pattern") or "").lower()
        if not pattern:
            continue
        if ptype == "domain" and domain_lower:
            if domain_lower == pattern or domain_lower.endswith("." + pattern):
                if len(pattern) > best_len:
                    best = entry
                    best_len = len(pattern)
        elif ptype == "source_name" and name_lower:
            if pattern in name_lower:
                if best is None:
                    best = entry
    return best
