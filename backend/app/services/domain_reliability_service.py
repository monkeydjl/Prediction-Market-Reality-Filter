"""Domain reliability service (LATER #2 - source trust feedback loop).

Pure functions that compute per-domain reliability statistics from resolved
event records. No I/O, no settings import. The store module handles
persistence; this module handles attribution semantics and aggregation.

EvidenceBreakdownItem.direction historically uses "support" / "oppose" /
"neutral" in this codebase. The public attribution interface normalizes those
values to the spec vocabulary: "supports" / "refutes".

Two loss measures per attribution (Q3)
--------------------------------------
``correct`` is a 0/1 label: did the recommendation direction match the outcome.
It carries no information about how confident the committed estimate was, so a
domain that keeps appearing on 51%-that-went-YES events scores exactly like one
that appears on 95%-that-went-YES events.

``brier`` is the confidence-aware version of the same judgement: the squared
error of the committed probability, flipped for a refuting domain (which was
arguing the complement). It stays ``None`` when the caller cannot supply the
committed probability -- see ``attribute_evidence``.

What this attribution is NOT: a leave-one-out marginal contribution. Every
domain on one event shares that event's probability, so neither measure
discriminates *within* an event; both discriminate *across* events, and Brier
does so with confidence weighting where ``correct`` does not.
"""
from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlparse

from app.services.calibration_service_event import brier_score

_VALID_DIRECTIONS = {"YES", "NO"}
_STANCE_ALIASES = {
    "support": "supports",
    "supports": "supports",
    "oppose": "refutes",
    "refutes": "refutes",
}


def _normalize_category(value: Any) -> str:
    if not isinstance(value, str):
        return "_unknown"
    category = value.strip()
    if not category or category == "_all":
        return "_unknown"
    return category


def extract_domain(url: str) -> str | None:
    """Normalize URL to domain: lowercase, strip www., netloc only.

    Returns None for invalid/missing URL. Mirrors
    source_reliability_service.extract_domain but returns None instead
    of empty string to let callers skip without truthiness checks.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parsed = urlparse(url.strip())
    except (ValueError, TypeError):
        return None
    # Tolerate URLs without scheme: "reuters.com/path" has no netloc.
    hostname = parsed.hostname
    if not hostname and parsed.path:
        # Try treating the path as "host/rest"
        maybe_host = parsed.path.split("/")[0]
        if "." in maybe_host:
            hostname = maybe_host
    if not hostname:
        return None
    hostname = hostname.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def attribute_evidence(
    record: dict[str, Any],
    *,
    committed_probability: float | None = None,
) -> list[dict[str, Any]]:
    """Extract per-domain attribution from one resolved event record.

    Returns list of dicts with keys: event_id, domain, category, stance,
    credibility (float | None), correct (bool), brier (float | None).

    ``committed_probability`` is the 0-100 estimate that was FROZEN for this
    event before the outcome was known (``predictions.ai_probability``), not
    ``record["ai_probability"]``. The record's field is rewritten by every
    re-scan, so a scan that ran after the outcome leaked would grade the model
    against an estimate it never committed to. The store supplies this; when it
    is None (event never frozen, or a caller that has no ledger access) ``brier``
    is None for every attribution -- there is deliberately no fallback to the
    record's latest estimate.

    Skips:
    - Non-resolved records or direction not in {YES, NO}
    - Non-numeric / None / negative actual_outcome
    - Evidence with direction=neutral or unknown stance
    - Evidence with missing/invalid URL
    - Mixed (supports + refutes) within same (event, domain, category) group
    """
    outcome = record.get("outcome") or {}
    if outcome.get("status") != "resolved":
        return []

    rec_direction = (record.get("actionable_recommendation") or {}).get("direction")
    if rec_direction not in _VALID_DIRECTIONS:
        return []

    actual = outcome.get("actual_outcome")
    if not isinstance(actual, (int, float)) or actual < 0:
        return []

    rec_correct = (
        (rec_direction == "YES" and actual > 0)
        or (rec_direction == "NO" and actual == 0)
    )

    # Stance-adjusted Brier of the committed estimate. A supporting domain
    # backed P(YES)=p; a refuting one argued the complement, so it is graded on
    # 100-p against the same outcome. Both are None together -- a per-stance
    # default would invent a loss for evidence nobody can grade.
    #
    # NaN is rejected rather than clamped: ``min(100.0, nan)`` returns 100.0, so
    # an unusable value would grade as a maximally confident call and, on a YES
    # outcome, as a perfect 0.0 Brier -- a score nobody earned.
    brier_supports: float | None = None
    brier_refutes: float | None = None
    if (
        isinstance(committed_probability, (int, float))
        and not isinstance(committed_probability, bool)
        and math.isfinite(committed_probability)
    ):
        committed = max(0.0, min(100.0, float(committed_probability)))
        brier_supports = brier_score(committed, float(actual))
        brier_refutes = brier_score(100.0 - committed, float(actual))

    evidence_breakdown = record.get("evidence_breakdown") or []
    evidence_items = record.get("evidence_items") or []

    # Build source_name -> url lookup from evidence_items.
    url_by_source: dict[str, str] = {}
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        src = str(item.get("source") or "").strip()
        url = str(item.get("url") or "").strip()
        if src and url:
            url_by_source[src.lower()] = url

    # Group by (domain, category) within this event.
    # Key: (domain, category) -> {stances: set, credibilities: list}
    groups: dict[tuple[str, str], dict[str, Any]] = {}

    for item in evidence_breakdown:
        if not isinstance(item, dict):
            continue
        direction = _STANCE_ALIASES.get(item.get("direction", ""))
        if direction is None:
            continue  # skip neutral

        source_name = str(item.get("source") or "").strip()
        url = str(item.get("url") or "").strip()
        if not url:
            url = url_by_source.get(source_name.lower(), "")
        domain = extract_domain(url)
        if domain is None:
            continue

        # Category resolution: evidence source_type > record source.type
        # > record.source_type > _unknown.
        category = _normalize_category(
            item.get("source_type")
            or (record.get("source") or {}).get("type")
            or record.get("source_type")
            or "_unknown"
        )

        key = (domain, category)
        if key not in groups:
            groups[key] = {"stances": set(), "credibilities": []}

        groups[key]["stances"].add(direction)

        cred = item.get("credibility")
        if isinstance(cred, (int, float)):
            groups[key]["credibilities"].append(max(0.0, min(1.0, float(cred))))

    # Convert groups to attributions, skipping mixed.
    event_id = record.get("event_id", "")
    result: list[dict[str, Any]] = []

    for (domain, category), group in groups.items():
        stances = group["stances"]
        if len(stances) > 1:
            continue  # mixed support + oppose -> skip

        stance = next(iter(stances))  # only one stance in set
        cred_values = group["credibilities"]
        credibility = sum(cred_values) / len(cred_values) if cred_values else None

        # Correctness: supports + rec_correct -> correct;
        # refutes + rec_correct -> wrong.
        if stance == "supports":
            correct = rec_correct
            brier = brier_supports
        else:  # refutes
            correct = not rec_correct
            brier = brier_refutes

        result.append({
            "event_id": event_id,
            "domain": domain,
            "category": category,
            "stance": stance,
            "credibility": credibility,
            "correct": correct,
            "brier": brier,
        })

    return result


def compute_reliability_stats(
    attributions: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Aggregate attributions into per-domain per-category stats.

    Returns dict keyed by (domain, category) with values:
        {sample_count, correct_count, wrong_count, credibility_sum,
         brier_sum, brier_count}

    ``brier_count`` is tracked separately from ``sample_count`` on purpose: an
    attribution whose event was never frozen has no gradeable estimate, so
    folding it into ``sample_count`` would silently deflate the mean Brier
    toward whatever default we picked. A reader comparing the two counts can see
    exactly how much of the sample is gradeable.
    """
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    for attr in attributions:
        key = (attr["domain"], attr["category"])
        if key not in stats:
            stats[key] = {
                "sample_count": 0,
                "correct_count": 0,
                "wrong_count": 0,
                "credibility_sum": 0.0,
                "brier_sum": 0.0,
                "brier_count": 0,
            }
        s = stats[key]
        s["sample_count"] += 1
        if attr["correct"]:
            s["correct_count"] += 1
        else:
            s["wrong_count"] += 1
        cred = attr.get("credibility")
        if isinstance(cred, (int, float)):
            s["credibility_sum"] += float(cred)
        brier = attr.get("brier")
        if isinstance(brier, (int, float)) and not isinstance(brier, bool):
            s["brier_sum"] += float(brier)
            s["brier_count"] += 1
    return stats


def compute_reliability_score(stats: dict[str, Any]) -> float | None:
    """Return correct_count / sample_count, or None when sample_count == 0."""
    if stats.get("sample_count", 0) == 0:
        return None
    return stats["correct_count"] / stats["sample_count"]


def compute_brier_skill(stats: dict[str, Any]) -> float | None:
    """Return ``1 - mean(brier)``, or None when no attribution was gradeable.

    Higher is better, on the same 0-1 scale as ``compute_reliability_score`` so
    both can serve as a prior weight -- but NOT on the same *distribution*: a
    coin-flip estimate scores 0.75 here and 0.5 there. Swapping one for the
    other therefore shifts every prior upward and must be opt-in.
    """
    count = stats.get("brier_count", 0)
    if not count:
        return None
    return 1.0 - (stats.get("brier_sum", 0.0) / count)
