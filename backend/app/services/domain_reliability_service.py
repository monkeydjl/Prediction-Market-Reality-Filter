"""Domain reliability service (LATER #2 - source trust feedback loop).

Pure functions that compute per-domain reliability statistics from resolved
event records. No I/O, no settings import. The store module handles
persistence; this module handles attribution semantics and aggregation.

EvidenceBreakdownItem.direction historically uses "support" / "oppose" /
"neutral" in this codebase. The public attribution interface normalizes those
values to the spec vocabulary: "supports" / "refutes".
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

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


def attribute_evidence(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract per-domain attribution from one resolved event record.

    Returns list of dicts with keys: event_id, domain, category, stance,
    credibility (float | None), correct (bool).

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
        else:  # refutes
            correct = not rec_correct

        result.append({
            "event_id": event_id,
            "domain": domain,
            "category": category,
            "stance": stance,
            "credibility": credibility,
            "correct": correct,
        })

    return result


def compute_reliability_stats(
    attributions: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Aggregate attributions into per-domain per-category stats.

    Returns dict keyed by (domain, category) with values:
        {sample_count, correct_count, wrong_count, credibility_sum}
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
    return stats


def compute_reliability_score(stats: dict[str, Any]) -> float | None:
    """Return correct_count / sample_count, or None when sample_count == 0."""
    if stats.get("sample_count", 0) == 0:
        return None
    return stats["correct_count"] / stats["sample_count"]
