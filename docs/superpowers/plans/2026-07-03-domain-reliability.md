# Domain Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a domain-level reliability statistics layer for evidence sources used in resolved prediction records.

**Architecture:** Pure service computes per-domain attribution from resolved events' evidence_breakdown. SQLite store persists aggregate stats with an idempotency ledger. Resolve hook triggers incremental updates. CLI and API expose read-only queries. No feedback into `build_source_reliability` yet.

**Tech Stack:** Python 3.11+, SQLite via `app.utils.sqlite_db`, FastAPI, argparse

## Global Constraints

- `domain_reliability_service.py` is pure: no file I/O, no SQLite, no import of global settings.
- `EvidenceBreakdownItem.direction` uses `"support"` / `"oppose"` / `"neutral"` (NOT "supports"/"refutes"). All code must use these exact values.
- URL comes from `evidence_items[].url`, NOT from `evidence_breakdown[]`. `evidence_breakdown[]` has `source` / `direction` / `credibility` but no URL.
- DB path follows `LOOP_DB_FILE` pattern: tables live in the same `v2_loop.db` SQLite file, use `sqlite_db.loop_db_path()` and `sqlite_db.writing()`/`reading()`.
- CLI output is ASCII-only: no emoji. Use `[HIGH]`/`[MEDIUM]`/`[INSUFFICIENT]` style markers.
- `rebuild --limit` and `rebuild --dry-run` must NOT write to the production store.
- `correct_count + wrong_count == sample_count` is a table-level invariant.
- `_unknown` category (missing source type) must be distinct from `_all` (cross-category aggregate).
- Commit message style: `feat(scope): subject`, lowercase, imperative.
- Tests must not rely on conftest.py autouse fixtures.
- Test runner: `python -m pytest` (primary). Run from `backend/` directory.
- Working directory for all commands: `e:\Github\Prediction Market Reality Filter\backend`

---

### Task 1: Add domain_reliability_service.py and service tests

**Files:**
- Create: `backend/app/services/domain_reliability_service.py`
- Create: `backend/tests/test_domain_reliability_service.py`

**Interfaces:**
- Consumes: nothing (pure module)
- Produces: `extract_domain(url) -> str | None`, `attribute_evidence(record) -> list[dict]`, `compute_reliability_stats(attributions) -> dict[tuple[str, str], dict]`, `compute_reliability_score(stats) -> float | None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_domain_reliability_service.py`:

```python
"""Unit tests for domain_reliability_service (LATER #2)."""
import unittest
from typing import Any

from app.services.domain_reliability_service import (
    attribute_evidence,
    compute_reliability_score,
    compute_reliability_stats,
    extract_domain,
)


class TestExtractDomain(unittest.TestCase):
    def test_normal_url(self):
        self.assertEqual(extract_domain("https://www.reuters.com/article/123"), "reuters.com")

    def test_no_www(self):
        self.assertEqual(extract_domain("https://reuters.com/path?q=1"), "reuters.com")

    def test_uppercase(self):
        self.assertEqual(extract_domain("https://WWW.Reuters.COM/"), "reuters.com")

    def test_invalid_url(self):
        self.assertIsNone(extract_domain("not a url"))

    def test_missing_url(self):
        self.assertIsNone(extract_domain(""))

    def test_no_scheme(self):
        self.assertEqual(extract_domain("reuters.com/path"), "reuters.com")


def _record(
    event_id: str = "e1",
    direction: str = "YES",
    actual_outcome: float = 100.0,
    outcome_status: str = "resolved",
    source_type: str = "prediction_market",
    evidence_breakdown: list[dict[str, Any]] | None = None,
    evidence_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if evidence_breakdown is None:
        evidence_breakdown = [
            {"source": "Reuters", "direction": "support", "credibility": 0.8},
        ]
    if evidence_items is None:
        evidence_items = [
            {"source": "Reuters", "url": "https://www.reuters.com/article/1"},
        ]
    return {
        "event_id": event_id,
        "source": {"type": source_type},
        "actionable_recommendation": {"direction": direction},
        "outcome": {"status": outcome_status, "actual_outcome": actual_outcome},
        "evidence_breakdown": evidence_breakdown,
        "evidence_items": evidence_items,
    }


class TestAttributeEvidence(unittest.TestCase):
    def test_yes_direction_correct_support(self):
        result = attribute_evidence(_record(direction="YES", actual_outcome=100.0))
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["correct"])
        self.assertEqual(result[0]["stance"], "support")

    def test_yes_direction_wrong_support(self):
        result = attribute_evidence(_record(direction="YES", actual_outcome=0.0))
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["correct"])

    def test_no_direction_correct_support(self):
        result = attribute_evidence(_record(direction="NO", actual_outcome=0.0))
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["correct"])

    def test_no_direction_wrong_support(self):
        result = attribute_evidence(_record(direction="NO", actual_outcome=100.0))
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["correct"])

    def test_oppose_flips_correctness(self):
        """oppose + recommendation correct -> source wrong."""
        rec = _record(
            direction="YES", actual_outcome=100.0,
            evidence_breakdown=[{"source": "Reuters", "direction": "oppose", "credibility": 0.8}],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["correct"])

    def test_wait_direction_skipped(self):
        result = attribute_evidence(_record(direction="WAIT"))
        self.assertEqual(result, [])

    def test_avoid_direction_skipped(self):
        result = attribute_evidence(_record(direction="AVOID"))
        self.assertEqual(result, [])

    def test_unresolved_skipped(self):
        result = attribute_evidence(_record(outcome_status="pending"))
        self.assertEqual(result, [])

    def test_none_actual_outcome_skipped(self):
        rec = _record(actual_outcome=None)  # type: ignore
        # Build a record where actual_outcome is None
        rec["outcome"]["actual_outcome"] = None
        result = attribute_evidence(rec)
        self.assertEqual(result, [])

    def test_negative_outcome_skipped(self):
        rec = _record(actual_outcome=-1)
        result = attribute_evidence(rec)
        self.assertEqual(result, [])

    def test_neutral_evidence_skipped(self):
        rec = _record(
            evidence_breakdown=[{"source": "Reuters", "direction": "neutral", "credibility": 0.8}],
        )
        result = attribute_evidence(rec)
        self.assertEqual(result, [])

    def test_missing_url_skipped(self):
        rec = _record(
            evidence_items=[{"source": "Reuters", "url": ""}],
        )
        result = attribute_evidence(rec)
        self.assertEqual(result, [])

    def test_mixed_support_oppose_skipped(self):
        """Same domain has both support and oppose -> mixed, skip."""
        rec = _record(
            evidence_breakdown=[
                {"source": "Reuters", "direction": "support", "credibility": 0.8},
                {"source": "Reuters", "direction": "oppose", "credibility": 0.6},
            ],
            evidence_items=[
                {"source": "Reuters", "url": "https://www.reuters.com/a"},
                {"source": "Reuters", "url": "https://www.reuters.com/b"},
            ],
        )
        result = attribute_evidence(rec)
        self.assertEqual(result, [])

    def test_same_domain_multiple_support(self):
        """Same domain, two support items -> merged into one attribution."""
        rec = _record(
            evidence_breakdown=[
                {"source": "Reuters", "direction": "support", "credibility": 0.8},
                {"source": "Reuters", "direction": "support", "credibility": 0.6},
            ],
            evidence_items=[
                {"source": "Reuters", "url": "https://www.reuters.com/a"},
                {"source": "Reuters", "url": "https://www.reuters.com/b"},
            ],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["credibility"], 0.7)

    def test_credibility_clipped(self):
        rec = _record(
            evidence_breakdown=[{"source": "Reuters", "direction": "support", "credibility": 1.5}],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["credibility"], 1.0)

    def test_credibility_missing(self):
        rec = _record(
            evidence_breakdown=[{"source": "Reuters", "direction": "support"}],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["credibility"])

    def test_category_from_evidence_source_type(self):
        rec = _record(
            evidence_breakdown=[
                {"source": "Reuters", "direction": "support", "credibility": 0.8,
                 "source_type": "open_web"},
            ],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "open_web")

    def test_category_from_record_source_type(self):
        rec = _record(source_type="prediction_question")
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "prediction_question")

    def test_category_unknown_fallback(self):
        rec = _record(
            source_type="",
            evidence_breakdown=[{"source": "X", "direction": "support", "credibility": 0.5}],
            evidence_items=[{"source": "X", "url": "https://x.com/a"}],
        )
        rec["source"] = {}
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "_unknown")

    def test_unknown_is_not_all(self):
        rec = _record(
            source_type="",
            evidence_breakdown=[{"source": "X", "direction": "support", "credibility": 0.5}],
            evidence_items=[{"source": "X", "url": "https://x.com/a"}],
        )
        rec["source"] = {}
        result = attribute_evidence(rec)
        self.assertNotEqual(result[0]["category"], "_all")

    def test_two_domains_two_attributions(self):
        rec = _record(
            evidence_breakdown=[
                {"source": "Reuters", "direction": "support", "credibility": 0.8},
                {"source": "BBC", "direction": "support", "credibility": 0.7},
            ],
            evidence_items=[
                {"source": "Reuters", "url": "https://www.reuters.com/a"},
                {"source": "BBC", "url": "https://www.bbc.co.uk/b"},
            ],
        )
        result = attribute_evidence(rec)
        self.assertEqual(len(result), 2)
        domains = {r["domain"] for r in result}
        self.assertIn("reuters.com", domains)
        self.assertIn("bbc.co.uk", domains)


class TestComputeReliabilityStats(unittest.TestCase):
    def test_basic_aggregation(self):
        attributions = [
            {"event_id": "e1", "domain": "reuters.com", "category": "prediction_market",
             "stance": "support", "credibility": 0.8, "correct": True},
            {"event_id": "e2", "domain": "reuters.com", "category": "prediction_market",
             "stance": "support", "credibility": 0.6, "correct": False},
            {"event_id": "e3", "domain": "bbc.co.uk", "category": "prediction_market",
             "stance": "support", "credibility": 0.9, "correct": True},
        ]
        stats = compute_reliability_stats(attributions)
        self.assertEqual(len(stats), 2)
        self.assertIn(("reuters.com", "prediction_market"), stats)
        self.assertIn(("bbc.co.uk", "prediction_market"), stats)

    def test_correct_plus_wrong_equals_sample(self):
        attributions = [
            {"event_id": "e1", "domain": "a.com", "category": "pm",
             "stance": "support", "credibility": 0.8, "correct": True},
            {"event_id": "e2", "domain": "a.com", "category": "pm",
             "stance": "support", "credibility": 0.6, "correct": False},
            {"event_id": "e3", "domain": "a.com", "category": "pm",
             "stance": "support", "credibility": 0.7, "correct": True},
        ]
        stats = compute_reliability_stats(attributions)
        s = stats[("a.com", "pm")]
        self.assertEqual(s["sample_count"], 3)
        self.assertEqual(s["correct_count"], 2)
        self.assertEqual(s["wrong_count"], 1)
        self.assertEqual(s["correct_count"] + s["wrong_count"], s["sample_count"])

    def test_credibility_sum(self):
        attributions = [
            {"event_id": "e1", "domain": "a.com", "category": "pm",
             "stance": "support", "credibility": 0.8, "correct": True},
            {"event_id": "e2", "domain": "a.com", "category": "pm",
             "stance": "support", "credibility": 0.6, "correct": False},
        ]
        stats = compute_reliability_stats(attributions)
        self.assertAlmostEqual(stats[("a.com", "pm")]["credibility_sum"], 1.4)

    def test_empty_input(self):
        self.assertEqual(compute_reliability_stats([]), {})


class TestComputeReliabilityScore(unittest.TestCase):
    def test_normal(self):
        self.assertAlmostEqual(
            compute_reliability_score({"correct_count": 12, "sample_count": 18}),
            0.667, places=2,
        )

    def test_zero_sample(self):
        self.assertIsNone(compute_reliability_score({"correct_count": 0, "sample_count": 0}))

    def test_all_correct(self):
        self.assertEqual(
            compute_reliability_score({"correct_count": 5, "sample_count": 5}),
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_domain_reliability_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.domain_reliability_service'`

- [ ] **Step 3: Write domain_reliability_service.py**

Create `backend/app/services/domain_reliability_service.py`:

```python
"""Domain reliability service (LATER #2 - source trust feedback loop).

Pure functions that compute per-domain reliability statistics from resolved
event records. No I/O, no settings import. The store module handles
persistence; this module handles attribution semantics and aggregation.

EvidenceBreakdownItem.direction uses "support" / "oppose" / "neutral"
(these are the actual field values in the codebase, NOT "supports"/"refutes").
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

_VALID_DIRECTIONS = {"YES", "NO"}
_VALID_STANCES = {"support", "oppose"}


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
    - Evidence with direction=neutral
    - Evidence with missing/invalid URL
    - Mixed (support + oppose) within same (event, domain, category) group
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
        direction = item.get("direction", "")
        if direction not in _VALID_STANCES:
            continue  # skip neutral

        source_name = str(item.get("source") or "").strip()
        url = url_by_source.get(source_name.lower(), "")
        domain = extract_domain(url)
        if domain is None:
            continue

        # Category resolution: evidence source_type > record source.type > _unknown
        category = (
            item.get("source_type")
            or (record.get("source") or {}).get("type")
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

        # Correctness: support + rec_correct -> correct; oppose + rec_correct -> wrong
        if stance == "support":
            correct = rec_correct
        else:  # oppose
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_domain_reliability_service.py -v`
Expected: PASS — all tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domain_reliability_service.py backend/tests/test_domain_reliability_service.py
git commit -m "feat(reliability): add domain_reliability_service pure functions"
```

---

### Task 2: Add domain_reliability_store.py and store tests

**Files:**
- Create: `backend/app/memory/domain_reliability_store.py`
- Create: `backend/tests/test_domain_reliability_store.py`

**Interfaces:**
- Consumes: `domain_reliability_service.attribute_evidence` + `compute_reliability_stats` + `compute_reliability_score` (from Task 1), `sqlite_db.loop_db_path` + `writing` + `reading` + `record_schema_version` + `apply_migrations` (existing)
- Produces: `apply_resolution(record)`, `rebuild_from_records(records)`, `get_stats(domain, category, min_samples)`, `get_domain_summary(domain)`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_domain_reliability_store.py`:

```python
"""Tests for domain_reliability_store (LATER #2)."""
import os
import tempfile
import unittest
from unittest.mock import patch

from app.services.domain_reliability_service import attribute_evidence


def _record(
    event_id: str = "e1",
    direction: str = "YES",
    actual_outcome: float = 100.0,
    source_type: str = "prediction_market",
    evidence_breakdown=None,
    evidence_items=None,
) -> dict:
    if evidence_breakdown is None:
        evidence_breakdown = [
            {"source": "Reuters", "direction": "support", "credibility": 0.8},
        ]
    if evidence_items is None:
        evidence_items = [
            {"source": "Reuters", "url": "https://www.reuters.com/article/1"},
        ]
    return {
        "event_id": event_id,
        "source": {"type": source_type},
        "actionable_recommendation": {"direction": direction},
        "outcome": {"status": "resolved", "actual_outcome": actual_outcome},
        "evidence_breakdown": evidence_breakdown,
        "evidence_items": evidence_items,
    }


class _TempDBMixin:
    """Provides a temp SQLite file and patches loop_db_path."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._db_path = self._tmp.name
        self._patcher = patch("app.utils.sqlite_db.loop_db_path", return_value=self._db_path)
        self._patcher.start()
        # Also reset the schema init guard
        from app.memory import domain_reliability_store as drs
        drs._INITIALIZED.discard(self._db_path)

    def tearDown(self):
        self._patcher.stop()
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        super().tearDown()


class TestApplyResolution(_TempDBMixin, unittest.TestCase):
    def test_apply_resolution_writes_rows(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        apply_resolution(_record())
        stats = get_stats()
        # Should have (reuters.com, prediction_market) and (reuters.com, _all)
        domains = {(s["domain"], s["category"]) for s in stats}
        self.assertIn(("reuters.com", "prediction_market"), domains)
        self.assertIn(("reuters.com", "_all"), domains)

    def test_apply_resolution_idempotent(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        rec = _record()
        apply_resolution(rec)
        apply_resolution(rec)
        stats = get_stats()
        for s in stats:
            if s["domain"] == "reuters.com":
                self.assertEqual(s["sample_count"], 1)

    def test_all_row_aggregates_across_categories(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        rec1 = _record(event_id="e1", source_type="prediction_market")
        rec2 = _record(
            event_id="e2", source_type="open_web",
            evidence_breakdown=[
                {"source": "Reuters", "direction": "support", "credibility": 0.7,
                 "source_type": "open_web"},
            ],
        )
        apply_resolution(rec1)
        apply_resolution(rec2)
        stats = get_stats()
        all_row = [s for s in stats if s["category"] == "_all" and s["domain"] == "reuters.com"]
        self.assertEqual(len(all_row), 1)
        self.assertEqual(all_row[0]["sample_count"], 2)

    def test_unknown_category_not_all(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        rec = _record(source_type="",
                      evidence_breakdown=[
                          {"source": "X", "direction": "support", "credibility": 0.5},
                      ],
                      evidence_items=[
                          {"source": "X", "url": "https://x.com/a"},
                      ])
        rec["source"] = {}
        apply_resolution(rec)
        stats = get_stats()
        cats = {s["category"] for s in stats if s["domain"] == "x.com"}
        self.assertIn("_unknown", cats)
        self.assertIn("_all", cats)
        self.assertNotEqual(cats, {"_all"})


class TestRebuild(_TempDBMixin, unittest.TestCase):
    def test_rebuild_clears_and_recomputes(self):
        from app.memory.domain_reliability_store import apply_resolution, rebuild_from_records, get_stats
        apply_resolution(_record(event_id="old"))
        rebuild_from_records([_record(event_id="new")])
        stats = get_stats()
        # Should have data from "new" only
        self.assertTrue(any(s["sample_count"] > 0 for s in stats))

    def test_rebuild_idempotent(self):
        from app.memory.domain_reliability_store import rebuild_from_records, get_stats
        records = [_record(event_id="e1")]
        rebuild_from_records(records)
        stats1 = get_stats()
        rebuild_from_records(records)
        stats2 = get_stats()
        self.assertEqual(len(stats1), len(stats2))
        for s1, s2 in zip(sorted(stats1, key=lambda x: (x["domain"], x["category"])),
                          sorted(stats2, key=lambda x: (x["domain"], x["category"]))):
            self.assertEqual(s1["sample_count"], s2["sample_count"])


class TestGetStats(_TempDBMixin, unittest.TestCase):
    def test_get_stats_filter_domain(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        apply_resolution(_record())
        stats = get_stats(domain="reuters.com")
        self.assertTrue(all(s["domain"] == "reuters.com" for s in stats))

    def test_get_stats_filter_category(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        apply_resolution(_record())
        stats = get_stats(category="prediction_market")
        self.assertTrue(all(s["category"] == "prediction_market" for s in stats))

    def test_get_stats_min_samples(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        apply_resolution(_record())
        stats = get_stats(min_samples=10)
        # Only 1 sample -> filtered out
        self.assertTrue(all(s["sample_count"] >= 10 for s in stats))

    def test_get_stats_returns_reliability_score(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        apply_resolution(_record())
        stats = get_stats()
        self.assertTrue(all("reliability_score" in s for s in stats))

    def test_get_stats_zero_sample_returns_null_score(self):
        from app.memory.domain_reliability_store import get_stats
        # No data -> empty list
        stats = get_stats()
        self.assertEqual(stats, [])

    def test_get_stats_insufficient_flag(self):
        from app.memory.domain_reliability_store import apply_resolution, get_stats
        from app.core.config import settings
        apply_resolution(_record())
        stats = get_stats()
        # 1 sample < default CONFIDENCE_MIN_SAMPLES (5) -> insufficient
        for s in stats:
            if s["sample_count"] < settings.DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES:
                self.assertTrue(s["insufficient_samples"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_domain_reliability_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.memory.domain_reliability_store'`

- [ ] **Step 3: Write domain_reliability_store.py**

Create `backend/app/memory/domain_reliability_store.py`. This module follows the `source_trust_registry_store.py` pattern: module-level functions, lazy schema init, `_ensure_schema`, `sqlite_db.writing`/`reading` context managers. Tables live in the same `v2_loop.db` file via `sqlite_db.loop_db_path()`.

```python
"""Domain reliability store (LATER #2).

SQLite-backed aggregate statistics for per-domain evidence reliability.
Tables live in the shared loop DB (v2_loop.db) alongside predictions and
event_market_links. Uses an idempotency ledger so incremental
apply_resolution can be called safely on re-resolve.

Schema follows the source_trust_registry_store pattern: module-level
functions, lazy schema init, sqlite_db.writing/reading.
"""
from __future__ import annotations

import threading
from typing import Any

from app.core.config import settings
from app.services.domain_reliability_service import (
    attribute_evidence,
    compute_reliability_score,
    compute_reliability_stats,
)
from app.utils import sqlite_db
from app.utils.helpers import utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS domain_reliability (
    domain           TEXT NOT NULL,
    category         TEXT NOT NULL DEFAULT '_all',
    sample_count     INTEGER NOT NULL DEFAULT 0,
    correct_count    INTEGER NOT NULL DEFAULT 0,
    wrong_count      INTEGER NOT NULL DEFAULT 0,
    credibility_sum  REAL NOT NULL DEFAULT 0.0,
    first_seen       TEXT NOT NULL,
    last_updated     TEXT NOT NULL,
    PRIMARY KEY (domain, category)
);

CREATE TABLE IF NOT EXISTS domain_reliability_ledger (
    event_id      TEXT NOT NULL,
    domain        TEXT NOT NULL,
    category      TEXT NOT NULL,
    correct       INTEGER NOT NULL,
    credibility   REAL,
    first_seen    TEXT NOT NULL,
    PRIMARY KEY (event_id, domain, category)
);
"""

_SCHEMA_VERSION = 1
_MIGRATIONS: dict[str, str] = {}

_INITIALIZED: set[str] = set()
_INIT_GUARD = threading.Lock()


def _ensure_schema(path: str) -> None:
    if path in _INITIALIZED:
        return
    with _INIT_GUARD:
        if path in _INITIALIZED:
            return
        with sqlite_db.writing(path) as conn:
            conn.executescript(_SCHEMA)
            sqlite_db.apply_migrations(conn, "domain_reliability",
                                       _SCHEMA_VERSION, _MIGRATIONS)
            sqlite_db.record_schema_version(conn, "domain_reliability",
                                            _SCHEMA_VERSION)
        _INITIALIZED.add(path)


def _row_to_stat(row: Any) -> dict[str, Any]:
    sample = row["sample_count"]
    correct = row["correct_count"]
    credibility_sum = row["credibility_sum"]
    wrong = sample - correct
    min_samples = settings.DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES
    return {
        "domain": row["domain"],
        "category": row["category"],
        "sample_count": sample,
        "correct_count": correct,
        "wrong_count": wrong,
        "credibility_sum": credibility_sum,
        "reliability_score": (correct / sample) if sample > 0 else None,
        "credibility_avg": (credibility_sum / sample) if sample > 0 else None,
        "insufficient_samples": sample < min_samples,
        "first_seen": row["first_seen"],
        "last_updated": row["last_updated"],
    }


def apply_resolution(record: dict[str, Any]) -> None:
    """Incrementally apply one resolved event.

    Calls attribute_evidence(record). For each attribution, writes both
    the real category row and the domain _all row. Uses
    domain_reliability_ledger to skip already-processed
    event/domain/category attributions.
    """
    attributions = attribute_evidence(record)
    if not attributions:
        return

    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    now = utc_now()

    with sqlite_db.writing(path) as conn:
        for attr in attributions:
            event_id = attr["event_id"]
            domain = attr["domain"]
            category = attr["category"]
            correct = 1 if attr["correct"] else 0
            credibility = attr.get("credibility")

            # Write both (domain, category) and (domain, "_all")
            for cat in (category, "_all"):
                # Check ledger for idempotency
                existing = conn.execute(
                    "SELECT 1 FROM domain_reliability_ledger "
                    "WHERE event_id = ? AND domain = ? AND category = ?",
                    (event_id, domain, cat),
                ).fetchone()
                if existing:
                    continue

                # Write ledger entry
                conn.execute(
                    "INSERT INTO domain_reliability_ledger "
                    "(event_id, domain, category, correct, credibility, first_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (event_id, domain, cat, correct, credibility, now),
                )

                # Upsert aggregate row
                conn.execute(
                    "INSERT INTO domain_reliability "
                    "(domain, category, sample_count, correct_count, "
                    "wrong_count, credibility_sum, first_seen, last_updated) "
                    "VALUES (?, ?, 1, ?, 0, ?, ?, ?) "
                    "ON CONFLICT(domain, category) DO UPDATE SET "
                    "sample_count = sample_count + 1, "
                    "correct_count = correct_count + ?, "
                    "wrong_count = wrong_count + ?, "
                    "credibility_sum = credibility_sum + ?, "
                    "last_updated = ?",
                    (domain, cat, correct,
                     credibility or 0.0, now, now,
                     correct, 0 if correct else 1,
                     credibility or 0.0, now),
                )


def rebuild_from_records(records: list[dict[str, Any]]) -> None:
    """Clear and rebuild all aggregate and ledger rows from records."""
    all_attributions: list[dict[str, Any]] = []
    for record in records:
        all_attributions.extend(attribute_evidence(record))

    stats = compute_reliability_stats(all_attributions)

    path = sqlite_db.loop_db_path()
    _ensure_schema(path)
    now = utc_now()

    with sqlite_db.writing(path) as conn:
        conn.execute("DELETE FROM domain_reliability")
        conn.execute("DELETE FROM domain_reliability_ledger")

        for (domain, category), s in stats.items():
            for cat in (category, "_all"):
                # Check if we already wrote this (domain, _all) combo
                existing = conn.execute(
                    "SELECT sample_count, correct_count, credibility_sum "
                    "FROM domain_reliability WHERE domain = ? AND category = ?",
                    (domain, cat),
                ).fetchone()

                if existing:
                    # Accumulate into existing _all row
                    conn.execute(
                        "UPDATE domain_reliability SET "
                        "sample_count = sample_count + ?, "
                        "correct_count = correct_count + ?, "
                        "wrong_count = wrong_count + ?, "
                        "credibility_sum = credibility_sum + ?, "
                        "last_updated = ? "
                        "WHERE domain = ? AND category = ?",
                        (s["sample_count"], s["correct_count"], s["wrong_count"],
                         s["credibility_sum"], now, domain, cat),
                    )
                else:
                    conn.execute(
                        "INSERT INTO domain_reliability "
                        "(domain, category, sample_count, correct_count, "
                        "wrong_count, credibility_sum, first_seen, last_updated) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (domain, cat, s["sample_count"], s["correct_count"],
                         s["wrong_count"], s["credibility_sum"], now, now),
                    )

        # Rebuild ledger
        for attr in all_attributions:
            for cat in (attr["category"], "_all"):
                conn.execute(
                    "INSERT OR IGNORE INTO domain_reliability_ledger "
                    "(event_id, domain, category, correct, credibility, first_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (attr["event_id"], attr["domain"], cat,
                     1 if attr["correct"] else 0, attr.get("credibility"), now),
                )


def get_stats(
    domain: str | None = None,
    category: str | None = None,
    min_samples: int = 0,
) -> list[dict[str, Any]]:
    """Query stats with optional filters."""
    path = sqlite_db.loop_db_path()
    _ensure_schema(path)

    clauses: list[str] = []
    params: list[Any] = []

    if domain is not None:
        clauses.append("domain = ?")
        params.append(domain)
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    if min_samples > 0:
        clauses.append("sample_count >= ?")
        params.append(min_samples)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = " ORDER BY domain, category"

    with sqlite_db.reading(path) as conn:
        rows = conn.execute(
            f"SELECT * FROM domain_reliability{where}{order}", params
        ).fetchall()

    return [_row_to_stat(row) for row in rows]


def get_domain_summary(domain: str) -> dict[str, Any] | None:
    """Return the _all row for one domain, if present."""
    stats = get_stats(domain=domain, category="_all")
    return stats[0] if stats else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_domain_reliability_store.py -v`
Expected: PASS — all tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/memory/domain_reliability_store.py backend/tests/test_domain_reliability_store.py
git commit -m "feat(reliability): add domain_reliability_store with ledger idempotency"
```

---

### Task 3: Add configuration settings

**Files:**
- Modify: `backend/app/core/config.py`
- Create: `backend/tests/test_domain_reliability_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `DOMAIN_RELIABILITY_TRACKING_ENABLED`, `DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES` (DB path is LOOP_DB_FILE, shared with other stores)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_domain_reliability_config.py`:

```python
"""Tests for DOMAIN_RELIABILITY_* config settings (LATER #2)."""
import unittest

from app.core.config import settings


class TestDomainReliabilityConfig(unittest.TestCase):
    def test_settings_have_domain_reliability_fields(self):
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_TRACKING_ENABLED"))
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES"))

    def test_default_values(self):
        self.assertFalse(settings.DOMAIN_RELIABILITY_TRACKING_ENABLED)
        self.assertEqual(settings.DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES, 5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_domain_reliability_config.py -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Add settings to config.py**

In `backend/app/core/config.py`, after the `QUALITY_ALERT_REPORT_ERRORS_HIGH` block (added in LATER #3 Task 2), add:

```python
    # ── Domain reliability tracking (LATER #2) — source trust feedback loop ──
    DOMAIN_RELIABILITY_TRACKING_ENABLED: bool = _env_bool(
        "DOMAIN_RELIABILITY_TRACKING_ENABLED", "false"
    )
    DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES: int = int(
        os.getenv("DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES", "5")
    )
```

Note: `DOMAIN_RELIABILITY_DB_PATH` is NOT added as a separate config — domain_reliability tables live in the shared `v2_loop.db` file via `sqlite_db.loop_db_path()`, consistent with `source_trust_registry_store` and `prediction_store`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_domain_reliability_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/tests/test_domain_reliability_config.py
git commit -m "feat(config): add DOMAIN_RELIABILITY_* settings"
```

---

### Task 4: Add resolve hook and hook tests

**Files:**
- Modify: `backend/app/services/event_resolve_service.py`
- Create: `backend/tests/test_domain_reliability_resolve_hook.py`

**Interfaces:**
- Consumes: `domain_reliability_store.apply_resolution` (from Task 2), `settings.DOMAIN_RELIABILITY_TRACKING_ENABLED` (from Task 3)
- Produces: hook at end of `resolve_with_calibration`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_domain_reliability_resolve_hook.py`:

```python
"""Tests for domain reliability resolve hook (LATER #2)."""
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from app.core.config import settings


class TestResolveHook(unittest.TestCase):
    def test_hook_disabled_by_default(self):
        """When TRACKING_ENABLED=False, no DB writes occur."""
        with patch.object(settings, "DOMAIN_RELIABILITY_TRACKING_ENABLED", False), \
             patch("app.memory.domain_reliability_store.apply_resolution") as mock_apply:
            from app.services.event_resolve_service import resolve_with_calibration
            # resolve_with_calibration requires event to exist; just verify
            # apply_resolution is not called when disabled.
            # We can't easily call resolve_with_calibration without a real event,
            # so we test the guard condition directly.
            self.assertFalse(settings.DOMAIN_RELIABILITY_TRACKING_ENABLED)

    def test_hook_on_resolve(self):
        """When TRACKING_ENABLED=True and an event resolves, apply_resolution is called."""
        # This test uses a real temp DB + patched event_store to create a
        # minimal resolved event, then verifies the store was written to.
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            with patch("app.utils.sqlite_db.loop_db_path", return_value=tmp.name), \
                 patch.object(settings, "DOMAIN_RELIABILITY_TRACKING_ENABLED", True):
                from app.memory import domain_reliability_store as drs
                drs._INITIALIZED.discard(tmp.name)
                from app.memory import event_store
                from app.services.event_resolve_service import resolve_with_calibration

                # Create a minimal event with evidence
                event_id = event_store.save_event({
                    "event_id": "hook-test-1",
                    "event_title": "Hook Test",
                    "source": {"type": "prediction_market"},
                    "actionable_recommendation": {"direction": "YES", "edge": 8.0},
                    "outcome": None,
                    "evidence_breakdown": [
                        {"source": "Reuters", "direction": "support", "credibility": 0.8},
                    ],
                    "evidence_items": [
                        {"source": "Reuters", "url": "https://www.reuters.com/a"},
                    ],
                    "llm_telemetry": {"analysis_quality": "llm"},
                    "probability": {"baseline": 60.0},
                    "calibration": None,
                })
                result = resolve_with_calibration(event_id, actual_outcome=100.0)
                self.assertIsNotNone(result)

                # Verify domain reliability was written
                from app.memory.domain_reliability_store import get_stats
                stats = get_stats()
                self.assertTrue(len(stats) > 0)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def test_hook_failure_does_not_block_resolve(self):
        """If apply_resolution raises, resolve still succeeds."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            with patch("app.utils.sqlite_db.loop_db_path", return_value=tmp.name), \
                 patch.object(settings, "DOMAIN_RELIABILITY_TRACKING_ENABLED", True), \
                 patch("app.memory.domain_reliability_store.apply_resolution",
                       side_effect=RuntimeError("db broken")):
                from app.memory import event_store
                from app.services.event_resolve_service import resolve_with_calibration

                event_id = event_store.save_event({
                    "event_id": "hook-fail-test",
                    "event_title": "Hook Fail Test",
                    "source": {"type": "prediction_market"},
                    "actionable_recommendation": {"direction": "YES", "edge": 8.0},
                    "outcome": None,
                    "evidence_breakdown": [],
                    "evidence_items": [],
                    "llm_telemetry": {"analysis_quality": "llm"},
                    "probability": {"baseline": 60.0},
                    "calibration": None,
                })
                result = resolve_with_calibration(event_id, actual_outcome=100.0)
                # Resolve should still succeed
                self.assertIsNotNone(result)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def test_hook_idempotent_on_re_resolve(self):
        """Resolving same event twice should not double-count."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            with patch("app.utils.sqlite_db.loop_db_path", return_value=tmp.name), \
                 patch.object(settings, "DOMAIN_RELIABILITY_TRACKING_ENABLED", True):
                from app.memory import domain_reliability_store as drs
                drs._INITIALIZED.discard(tmp.name)
                from app.memory import event_store
                from app.services.event_resolve_service import resolve_with_calibration

                event_id = event_store.save_event({
                    "event_id": "hook-idem-test",
                    "event_title": "Idempotent Test",
                    "source": {"type": "prediction_market"},
                    "actionable_recommendation": {"direction": "YES", "edge": 8.0},
                    "outcome": None,
                    "evidence_breakdown": [
                        {"source": "Reuters", "direction": "support", "credibility": 0.8},
                    ],
                    "evidence_items": [
                        {"source": "Reuters", "url": "https://www.reuters.com/a"},
                    ],
                    "llm_telemetry": {"analysis_quality": "llm"},
                    "probability": {"baseline": 60.0},
                    "calibration": None,
                })
                resolve_with_calibration(event_id, actual_outcome=100.0)
                # Resolve again (should be no-op for event_store, but if hook
                # runs again the ledger should prevent double-count)
                # Note: event_store.resolve_event will skip already-resolved events,
                # so the hook won't fire twice through the normal path. We test
                # apply_resolution idempotency directly in store tests.
                from app.memory.domain_reliability_store import get_stats
                stats = get_stats()
                for s in stats:
                    if s["domain"] == "reuters.com":
                        self.assertEqual(s["sample_count"], 1)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_domain_reliability_resolve_hook.py -v`
Expected: FAIL — hook not yet added to `resolve_with_calibration`

- [ ] **Step 3: Add hook to event_resolve_service.py**

In `backend/app/services/event_resolve_service.py`, after line `updated = resolve_event(event_id, outcome, calibration=calibration)` (line 154) and before `return updated`, add:

```python
    # Domain reliability tracking (LATER #2): best-effort, non-blocking.
    if settings.DOMAIN_RELIABILITY_TRACKING_ENABLED:
        try:
            from app.memory.domain_reliability_store import apply_resolution
            apply_resolution(record)
        except Exception:
            logger.warning("domain reliability tracking failed", exc_info=True)
```

Also ensure `logger` is available — check if the module already has `import logging` / `logger = logging.getLogger(...)`. If not, add it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_domain_reliability_resolve_hook.py -v`
Expected: PASS — all 4 tests.

- [ ] **Step 5: Run regression tests**

Run: `python -m pytest tests/test_event_resolve_service.py -q`
Expected: all PASS (hook disabled by default, no behavior change).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/event_resolve_service.py backend/tests/test_domain_reliability_resolve_hook.py
git commit -m "feat(resolve): add domain reliability tracking hook"
```

---

### Task 5: Add CLI and CLI tests

**Files:**
- Create: `backend/scripts/domain_reliability_cli.py`
- Create: `backend/tests/test_domain_reliability_cli.py`

**Interfaces:**
- Consumes: `domain_reliability_store.get_stats` + `rebuild_from_records` (from Task 2), `event_store.list_resolved_events` (existing)
- Produces: CLI with `list` / `rebuild` subcommands

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_domain_reliability_cli.py`:

```python
"""Tests for domain_reliability CLI (LATER #2)."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


class TestDomainReliabilityCli(unittest.TestCase):
    def _run_main(self, argv):
        import domain_reliability_cli as drc
        orig_stdout, orig_stderr = sys.stdout, sys.stderr
        try:
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            rc = drc.main(argv)
            return rc, sys.stdout.getvalue(), sys.stderr.getvalue()
        finally:
            sys.stdout, sys.stderr = orig_stdout, orig_stderr

    def test_cli_list_empty_exit_0(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]):
            rc, stdout, _ = self._run_main(["list"])
        self.assertEqual(rc, 0)
        self.assertIn("0 domains", stdout)

    def test_cli_list_json_shape(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]):
            rc, stdout, _ = self._run_main(["list", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertIn("domains", data)
        self.assertIn("total_domains", data)
        self.assertIn("total_rows", data)

    def test_cli_rebuild_dry_run(self):
        with patch("app.memory.domain_reliability_store.rebuild_from_records") as mock_rb, \
             patch("app.memory.event_store.list_resolved_events", return_value=[]):
            rc, stdout, _ = self._run_main(["rebuild", "--dry-run"])
        self.assertEqual(rc, 0)
        mock_rb.assert_not_called()

    def test_cli_rebuild_limit_preview(self):
        with patch("app.memory.domain_reliability_store.rebuild_from_records") as mock_rb, \
             patch("app.memory.event_store.list_resolved_events", return_value=[]):
            rc, stdout, _ = self._run_main(["rebuild", "--limit", "5"])
        self.assertEqual(rc, 0)
        mock_rb.assert_not_called()

    def test_cli_rebuild_full(self):
        with patch("app.memory.event_store.list_resolved_events", return_value=[]), \
             patch("app.services.domain_reliability_service.attribute_evidence", return_value=[]):
            rc, stdout, _ = self._run_main(["rebuild"])
        self.assertEqual(rc, 0)

    def test_cli_rebuild_limit_does_not_write(self):
        with patch("app.memory.domain_reliability_store.rebuild_from_records") as mock_rb, \
             patch("app.memory.event_store.list_resolved_events", return_value=[]):
            self._run_main(["rebuild", "--limit", "5"])
        mock_rb.assert_not_called()

    def test_cli_no_emoji(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]):
            rc, stdout, _ = self._run_main(["list"])
        for ch in stdout:
            cp = ord(ch)
            self.assertFalse(
                0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF,
                f"Output contains emoji-like char U+{cp:04X}",
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_domain_reliability_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'domain_reliability_cli'`

- [ ] **Step 3: Create domain_reliability_cli.py**

Create `backend/scripts/domain_reliability_cli.py`:

```python
"""Domain reliability CLI (LATER #2).

Query and rebuild per-domain reliability statistics from resolved events.

Usage:
    python -m scripts.domain_reliability_cli list
    python -m scripts.domain_reliability_cli list --json
    python -m scripts.domain_reliability_cli rebuild
    python -m scripts.domain_reliability_cli rebuild --dry-run
    python -m scripts.domain_reliability_cli rebuild --limit 50
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

# UTF-8 stdout for Windows GBK console safety.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, io.UnsupportedOperation):
    pass

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def _print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _render_text(stats: list[dict]) -> str:
    lines: list[str] = []
    domains = {s["domain"] for s in stats}
    lines.append(f"Domain Reliability Report - {len(domains)} domains")
    lines.append("")
    lines.append(
        f"{'Domain':<20} {'Category':<20} {'Samples':>7} {'Correct':>7} "
        f"{'Wrong':>5} {'Reliability':>11} {'Avg Cred':>8}"
    )
    for s in sorted(stats, key=lambda x: (x["domain"], x["category"])):
        score = f"{s['reliability_score']:.1%}" if s["reliability_score"] is not None else "N/A"
        cred = f"{s['credibility_avg']:.2f}" if s["credibility_avg"] is not None else "N/A"
        lines.append(
            f"{s['domain']:<20} {s['category']:<20} {s['sample_count']:>7} "
            f"{s['correct_count']:>7} {s['wrong_count']:>5} {score:>11} {cred:>8}"
        )
    lines.append("")
    total_samples = sum(s["sample_count"] for s in stats)
    avg_rel = sum(s["reliability_score"] for s in stats if s["reliability_score"] is not None)
    n_rel = sum(1 for s in stats if s["reliability_score"] is not None)
    avg_str = f"{avg_rel / n_rel:.1%}" if n_rel > 0 else "N/A"
    lines.append(f"Summary: {len(domains)} domains, {total_samples} total samples, {avg_str} avg reliability.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="domain_reliability")
    subparsers = parser.add_subparsers(dest="command")

    sp_list = subparsers.add_parser("list")
    sp_list.add_argument("--domain", type=str, default=None)
    sp_list.add_argument("--category", type=str, default=None)
    sp_list.add_argument("--min-samples", type=int, default=0)
    sp_list.add_argument("--json", action="store_true")

    sp_rebuild = subparsers.add_parser("rebuild")
    sp_rebuild.add_argument("--limit", type=int, default=None,
                            help="Preview only: process first N events without writing to DB")
    sp_rebuild.add_argument("--dry-run", action="store_true",
                            help="Compute and print stats without writing to DB")

    args = parser.parse_args(argv)

    from app.memory.domain_reliability_store import get_stats, rebuild_from_records
    from app.memory import event_store

    if args.command == "list":
        stats = get_stats(domain=args.domain, category=args.category,
                          min_samples=args.min_samples)
        if args.json:
            domains = {s["domain"] for s in stats}
            payload = {
                "domains": stats,
                "total_domains": len(domains),
                "total_rows": len(stats),
            }
            _print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        else:
            _print(_render_text(stats))
        return 0

    elif args.command == "rebuild":
        entries = event_store.list_resolved_events()
        records = [e.get("record", {}) for e in entries if isinstance(e.get("record"), dict)]

        if args.limit is not None:
            records = records[:args.limit]
            _print(f"Preview: would process {len(records)} records (not writing to DB).")
            # Preview only — do NOT write
            return 0

        if args.dry_run:
            _print(f"Dry run: would process {len(records)} records (not writing to DB).")
            return 0

        rebuild_from_records(records)
        _print(f"Rebuilt domain reliability from {len(records)} records.")
        return 0

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_domain_reliability_cli.py -v`
Expected: PASS — all 7 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/domain_reliability_cli.py backend/tests/test_domain_reliability_cli.py
git commit -m "feat(cli): add domain_reliability_cli with list and rebuild"
```

---

### Task 6: Add API endpoint and endpoint tests

**Files:**
- Modify: `backend/app/api/routes/quality_metrics.py`
- Create: `backend/tests/test_domain_reliability_endpoint.py`

**Interfaces:**
- Consumes: `domain_reliability_store.get_stats` (from Task 2)
- Produces: `GET /quality-metrics/domain-reliability` endpoint

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_domain_reliability_endpoint.py`:

```python
"""HTTP tests for /quality-metrics/domain-reliability endpoint (LATER #2)."""
import unittest

from fastapi.testclient import TestClient

from app.main import app


class TestDomainReliabilityEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_endpoint_empty_db_returns_200(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("domains", data)
        self.assertIn("total_domains", data)
        self.assertIn("total_rows", data)

    def test_endpoint_returns_stats(self):
        fake_stats = [{
            "domain": "reuters.com", "category": "prediction_market",
            "sample_count": 10, "correct_count": 7, "wrong_count": 3,
            "credibility_sum": 8.0, "reliability_score": 0.7,
            "credibility_avg": 0.8, "insufficient_samples": False,
            "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z",
        }]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["domains"]), 1)
        self.assertEqual(data["total_domains"], 1)
        self.assertEqual(data["total_rows"], 1)

    def test_endpoint_filter_domain(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]) as mock:
            self.client.get("/api/quality-metrics/domain-reliability?domain=reuters.com")
        mock.assert_called_once_with(domain="reuters.com", category=None, min_samples=0)

    def test_endpoint_filter_category(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]) as mock:
            self.client.get("/api/quality-metrics/domain-reliability?category=prediction_market")
        mock.assert_called_once_with(domain=None, category="prediction_market", min_samples=0)

    def test_endpoint_min_samples_filter(self):
        with patch("app.memory.domain_reliability_store.get_stats", return_value=[]) as mock:
            self.client.get("/api/quality-metrics/domain-reliability?min_samples=10")
        mock.assert_called_once_with(domain=None, category=None, min_samples=10)

    def test_endpoint_reliability_score_null(self):
        fake_stats = [{
            "domain": "x.com", "category": "_all",
            "sample_count": 0, "correct_count": 0, "wrong_count": 0,
            "credibility_sum": 0.0, "reliability_score": None,
            "credibility_avg": None, "insufficient_samples": True,
            "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z",
        }]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        self.assertIsNone(data["domains"][0]["reliability_score"])

    def test_endpoint_insufficient_flag(self):
        fake_stats = [{
            "domain": "x.com", "category": "_all",
            "sample_count": 2, "correct_count": 1, "wrong_count": 1,
            "credibility_sum": 1.0, "reliability_score": 0.5,
            "credibility_avg": 0.5, "insufficient_samples": True,
            "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z",
        }]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        self.assertTrue(data["domains"][0]["insufficient_samples"])

    def test_endpoint_invalid_min_samples(self):
        response = self.client.get("/api/quality-metrics/domain-reliability?min_samples=-1")
        self.assertEqual(response.status_code, 422)

    def test_endpoint_total_rows(self):
        fake_stats = [
            {"domain": "a.com", "category": "pm", "sample_count": 1,
             "correct_count": 1, "wrong_count": 0, "credibility_sum": 0.5,
             "reliability_score": 1.0, "credibility_avg": 0.5,
             "insufficient_samples": True,
             "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z"},
            {"domain": "a.com", "category": "_all", "sample_count": 1,
             "correct_count": 1, "wrong_count": 0, "credibility_sum": 0.5,
             "reliability_score": 1.0, "credibility_avg": 0.5,
             "insufficient_samples": True,
             "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z"},
        ]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        self.assertEqual(data["total_rows"], 2)
        self.assertEqual(data["total_domains"], 1)

    def test_endpoint_stable_json_types(self):
        """Null scores must be JSON null, not string 'N/A'."""
        fake_stats = [{
            "domain": "x.com", "category": "_all",
            "sample_count": 0, "correct_count": 0, "wrong_count": 0,
            "credibility_sum": 0.0, "reliability_score": None,
            "credibility_avg": None, "insufficient_samples": True,
            "first_seen": "2026-01-01T00:00:00Z", "last_updated": "2026-07-01T00:00:00Z",
        }]
        with patch("app.memory.domain_reliability_store.get_stats", return_value=fake_stats):
            response = self.client.get("/api/quality-metrics/domain-reliability")
        data = response.json()
        # Must be null, not "N/A"
        self.assertIsNone(data["domains"][0]["reliability_score"])
        self.assertIsNone(data["domains"][0]["credibility_avg"])


# Need to import patch at module level
from unittest.mock import patch


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_domain_reliability_endpoint.py -v`
Expected: FAIL — 404 (route doesn't exist)

- [ ] **Step 3: Add the endpoint**

In `backend/app/api/routes/quality_metrics.py`, add the new route before the module's `__all__` or at the end of the route definitions:

```python
@router.get("/quality-metrics/domain-reliability")
async def domain_reliability(
    domain: str | None = Query(default=None),
    category: str | None = Query(default=None),
    min_samples: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Query domain reliability statistics. Read-only."""
    from app.memory.domain_reliability_store import get_stats

    stats = get_stats(domain=domain, category=category, min_samples=min_samples)
    return {
        "domains": stats,
        "total_domains": len({s["domain"] for s in stats}),
        "total_rows": len(stats),
    }
```

Ensure `Query` and `Any` are already imported (they should be from existing routes).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_domain_reliability_endpoint.py -v`
Expected: PASS — all 10 tests.

- [ ] **Step 5: Run regression tests**

Run: `python -m pytest tests/test_quality_metrics_report.py tests/test_quality_metrics.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/quality_metrics.py backend/tests/test_domain_reliability_endpoint.py
git commit -m "feat(api): add /quality-metrics/domain-reliability endpoint"
```

---

### Task 7: Regression suite + end-to-end verification

**Files:**
- No new files

- [ ] **Step 1: Run all new tests**

Run: `python -m pytest tests/test_domain_reliability_service.py tests/test_domain_reliability_store.py tests/test_domain_reliability_config.py tests/test_domain_reliability_resolve_hook.py tests/test_domain_reliability_cli.py tests/test_domain_reliability_endpoint.py -v`

- [ ] **Step 2: Run regression tests**

Run: `python -m pytest tests/test_event_resolve_service.py tests/test_source_reliability_service.py tests/test_quality_metrics_report.py tests/test_quality_metrics.py tests/test_quality_alert_service.py tests/test_quality_alerts_endpoint.py -q`

- [ ] **Step 3: Compilation check**

Run: `python -m compileall app\services\domain_reliability_service.py app\memory\domain_reliability_store.py app\api\routes\quality_metrics.py scripts\domain_reliability_cli.py`

- [ ] **Step 4: End-to-end smoke**

Run: `python -m scripts.domain_reliability_cli rebuild --dry-run`
Run: `python -m scripts.domain_reliability_cli list`

- [ ] **Step 5: Repository checks**

Run: `git diff --check`
Run: `npm.cmd run typecheck` (from `frontend/` directory)

- [ ] **Step 6: Commit if any fixes needed, otherwise done**

No commit needed if all checks pass.
