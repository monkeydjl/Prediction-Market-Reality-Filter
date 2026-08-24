"""Pinned model-eval sets — 固定评测集 / 版本号 (Q1).

Why this module exists: ``scripts/model_eval_lab --sample N`` advertised
"reproducible seed=42" and was not reproducible in the way that matters for a
routine eval. ``random.Random(42).sample(population, k)`` picks *positions*,
so its membership depends on the population's length and order. Measured on a
200-event population: adding 20 resolved events replaced 10 of 50 members, and
merely reordering the list (which an ``event_store.json`` rewrite does)
replaced 36 of 50. Two runs a week apart were therefore grading different
events while reporting one metric — the report was not comparable to itself.

A pinned set fixes that by writing the membership down. Two properties make it
a *fixed* set rather than a fixed list of ids:

  1. **Stable selection.** ``select_event_ids`` ranks candidates by
     ``sha256(seed || event_id)`` instead of by position, so the choice is
     independent of order entirely and a growing population can only displace
     an incumbent one-for-one. That makes minting a set reproducible; the
     manifest is what makes it *fixed*.
  2. **Fingerprints.** A record can be re-graded after it was pinned (outcome
     rewritten, estimate replaced). The same event id then names different
     data, and the score silently stops being comparable. Each pinned id
     carries a digest over the fields the eval actually reads, so drift is
     detected rather than absorbed.

A drifted event is **kept in the set and reported**, never dropped: dropping it
would quietly shrink the denominator, which reads as "we evaluated the whole
set" when we did not. ``model_eval_gate_service`` is what blocks on it.

Pure: no I/O, no clock, no network, no settings import. ``created_at`` is
supplied by the caller so a manifest built from the same inputs is
byte-identical.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

EVAL_SET_SCHEMA_VERSION = 1

# Ranking strategy recorded in the manifest so a future strategy change is
# visible in the artifact instead of silently re-minting a different set.
SELECTION_STRATEGY = "sha256-rank"

# The fields a fingerprint covers: exactly what build_model_eval_report reads
# off an item. Deliberately NOT the whole record -- a cosmetic edit elsewhere
# on the record must not invalidate a pinned set, or drift detection becomes
# noise nobody reads.
FINGERPRINT_FIELDS: tuple[str, ...] = (
    "estimated_probability",
    "actual_outcome",
    "direction_correct",
    "brier_score",
    "model",
    "analysis_quality",
    "degraded_mode",
    "estimated_token_cost",
)

# Separator between seed and id when hashing. Without it, seed "a" + id "bc"
# and seed "ab" + id "c" hash to the same digest, so two different sets could
# rank identically.
_SEP = "\x00"


def _canonical_json(payload: Any) -> str:
    """Canonical JSON for hashing: sorted keys, no whitespace, ASCII-escaped."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )


def _fingerprint_value(value: Any) -> Any:
    """Normalize one field value for hashing.

    Every real number — int or float — goes through ``repr(v + 0.0)``. Doing it
    for floats only was a bug: JSON ``72`` parses to an int and JSON ``72.0`` to
    a float, so a store rewrite that changed nothing but a value's spelling
    flagged the whole set as drifted, and drift detection that cries wolf is
    drift detection nobody reads.

    ``repr(v + 0.0)`` is the shortest round-trip form (deterministic for IEEE
    doubles), it folds ``-0.0`` into ``0.0`` so two numerically equal estimates
    cannot fingerprint differently, and it keeps ``nan`` / ``inf``
    distinguishable from each other instead of collapsing them into one
    "unusable" token. Bools are left alone: ``True`` is an int in Python and
    must not fingerprint like ``1.0``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return repr(value + 0.0)
        except OverflowError:
            # An int too large for a double. No field here can legitimately
            # hold one, but a mint should not die on a malformed record.
            return repr(value)
    return value


def record_fingerprint(item: dict[str, Any]) -> str:
    """Digest of the graded fields of one extracted item.

    Missing fields participate as ``None``, so a record that *loses* a field
    fingerprints differently from one that always lacked it only if the value
    differed — which is the intended equivalence.
    """
    body = {name: _fingerprint_value(item.get(name)) for name in FINGERPRINT_FIELDS}
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _selection_digest(seed: str, event_id: str) -> str:
    return hashlib.sha256(f"{seed}{_SEP}{event_id}".encode("utf-8")).hexdigest()


def select_event_ids(event_ids: list[str], *, seed: str, size: int) -> list[str]:
    """The ``size`` event ids a given seed selects, sorted by event id.

    Order-independent and duplicate-safe: the caller's list order never affects
    membership, and a repeated id cannot occupy two slots. ``size`` larger than
    the population returns the whole population rather than raising — a set
    minted from a small store is legitimate, and the manifest records the
    population it was drawn from.
    """
    if size < 0:
        raise ValueError("size must be >= 0")
    unique = {eid for eid in event_ids if isinstance(eid, str) and eid}
    # Tie-break on the id so two colliding digests still rank deterministically.
    ranked = sorted(unique, key=lambda eid: (_selection_digest(seed, eid), eid))
    return sorted(ranked[:size])


def manifest_digest(manifest: dict[str, Any]) -> str:
    """Digest over every manifest field except ``digest`` itself.

    This is a tamper seal, not a membership identity. It covers ``created_at``
    too, so two mints of the *same* 30 events taken a minute apart carry
    different digests — verified on the live 130-event store. Do not read
    "different digest" as "different set": the membership identity is
    ``name`` + ``revision``, which is why a membership change is supposed to
    come with a revision bump. What the digest answers is the narrower
    question ``validate_manifest`` asks: was this file edited after it was
    minted?
    """
    body = {k: v for k, v in manifest.items() if k != "digest"}
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def build_manifest(
    items: list[dict[str, Any]],
    *,
    name: str,
    revision: str,
    seed: str,
    size: int,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Mint a manifest pinning ``size`` of ``items`` under ``seed``.

    ``items`` are ``extract_model_metrics`` outputs. Raises ValueError on an
    empty name/revision/seed, a non-positive size, or two items sharing an
    event id — a duplicate id makes "which record did we pin?" unanswerable,
    and guessing would silently pin whichever one came last.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("revision must be a non-empty string")
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be a non-empty string")
    if size <= 0:
        raise ValueError("size must be > 0")

    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        event_id = item.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            continue
        if event_id in by_id:
            raise ValueError(f"duplicate event_id in items: {event_id}")
        by_id[event_id] = item

    selected = select_event_ids(list(by_id), seed=seed, size=size)
    if not selected:
        # An empty manifest mints cleanly and then fails validate_manifest on
        # load. Refuse here so the failure lands where the mistake is.
        raise ValueError("no usable event_id in items -- nothing to pin")
    manifest: dict[str, Any] = {
        "eval_set_schema_version": EVAL_SET_SCHEMA_VERSION,
        "name": name.strip(),
        "revision": revision.strip(),
        "created_at": created_at,
        "selection": {
            "strategy": SELECTION_STRATEGY,
            "seed": seed,
            "size": size,
            "population": len(by_id),
        },
        "event_ids": selected,
        "fingerprints": {eid: record_fingerprint(by_id[eid]) for eid in selected},
    }
    manifest["digest"] = manifest_digest(manifest)
    return manifest


def validate_manifest(obj: Any) -> list[str]:
    """Every problem found in a loaded manifest; empty list means usable.

    Returns all problems rather than raising on the first so an operator fixes
    one file once. A digest mismatch is reported as a problem, not repaired:
    recomputing it would launder a hand-edited membership into an
    authoritative-looking artifact.
    """
    problems: list[str] = []
    if not isinstance(obj, dict):
        return ["manifest is not a JSON object"]

    version = obj.get("eval_set_schema_version")
    if version != EVAL_SET_SCHEMA_VERSION:
        problems.append(
            f"unsupported eval_set_schema_version: {version!r} "
            f"(expected {EVAL_SET_SCHEMA_VERSION})"
        )

    for field in ("name", "revision"):
        value = obj.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{field} must be a non-empty string")

    event_ids = obj.get("event_ids")
    if not isinstance(event_ids, list) or not event_ids:
        problems.append("event_ids must be a non-empty list")
        event_ids = []
    else:
        if any(not isinstance(eid, str) or not eid for eid in event_ids):
            problems.append("event_ids must all be non-empty strings")
            event_ids = [eid for eid in event_ids if isinstance(eid, str) and eid]
        if len(set(event_ids)) != len(event_ids):
            problems.append("event_ids contains duplicates")

    fingerprints = obj.get("fingerprints")
    if not isinstance(fingerprints, dict):
        problems.append("fingerprints must be a JSON object")
    elif event_ids and set(fingerprints) != set(event_ids):
        missing = sorted(set(event_ids) - set(fingerprints))
        extra = sorted(set(fingerprints) - set(event_ids))
        problems.append(
            f"fingerprints do not cover event_ids exactly "
            f"(missing {len(missing)}, extra {len(extra)})"
        )

    digest = obj.get("digest")
    if not isinstance(digest, str) or not digest:
        problems.append("digest must be a non-empty string")
    elif digest != manifest_digest(obj):
        problems.append("digest mismatch (manifest edited after it was minted?)")

    return problems


def resolve_eval_set(
    manifest: dict[str, Any],
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Restrict ``items`` to the pinned set; return (items, summary).

    Items come back in manifest order so a rendered report is stable. An id the
    store no longer has lands in ``missing_event_ids``; an id whose graded
    fields changed since minting lands in ``drifted_event_ids`` **and stays in
    the returned items** — see the module docstring. Items outside the manifest
    are dropped and counted in ``ignored``.

    The summary is the report's ``eval_set`` block. It carries no items, so a
    JSON report stays the size of a report.
    """
    event_ids: list[str] = [
        eid for eid in manifest.get("event_ids") or [] if isinstance(eid, str)
    ]
    fingerprints = manifest.get("fingerprints")
    if not isinstance(fingerprints, dict):
        fingerprints = {}

    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        event_id = item.get("event_id")
        if isinstance(event_id, str) and event_id and event_id not in by_id:
            by_id[event_id] = item

    selected: list[dict[str, Any]] = []
    missing: list[str] = []
    drifted: list[str] = []
    for event_id in event_ids:
        matched = by_id.get(event_id)
        if matched is None:
            missing.append(event_id)
            continue
        selected.append(matched)
        expected = fingerprints.get(event_id)
        if expected is not None and record_fingerprint(matched) != expected:
            drifted.append(event_id)

    selection = manifest.get("selection")
    summary: dict[str, Any] = {
        "name": manifest.get("name"),
        "revision": manifest.get("revision"),
        "digest": manifest.get("digest"),
        "created_at": manifest.get("created_at"),
        "selection": selection if isinstance(selection, dict) else None,
        "event_count": len(event_ids),
        "matched": len(selected),
        "missing_event_ids": missing,
        "drifted_event_ids": drifted,
        "ignored": len(by_id) - len(selected),
        "coverage": round(len(selected) / len(event_ids), 4) if event_ids else None,
        "complete": bool(event_ids) and not missing and not drifted,
    }
    return selected, summary
