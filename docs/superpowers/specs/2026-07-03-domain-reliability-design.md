# Domain Reliability Design (LATER #2)

**Date:** 2026-07-03
**Spec gap:** LATER #2 - source trust feedback loop, statistics layer first
**Status:** Design written for user review

---

## 1. Goal

Add a domain-level reliability statistics layer for evidence sources used in
resolved prediction records.

The first version answers: "When a source domain supported or refuted a
recommendation, was that stance later correct?" It records aggregate statistics
that can be queried through a CLI and read-only API. Feeding those scores back
into `build_source_reliability` is intentionally left for a later step.

## 2. Non-Goals

- No change to `build_source_reliability` in this spec.
- No frontend dashboard changes.
- No webhook, alert, or external dispatch.
- No per-event evidence detail table beyond the idempotency ledger.
- No attempt to score neutral evidence.
- No blocking of event resolution when reliability tracking fails.

## 3. Source Attribution Semantics

Attribution is computed from resolved event records with evidence items.

Expected evidence item fields:

```python
{
    "source": str,          # display name
    "url": str,             # used to extract domain
    "direction": str,       # "supports" | "refutes" | "neutral"
    "credibility": float,   # optional, expected 0-1
    "source_type": str,     # optional category override
}
```

Expected record fields:

```python
record["actionable_recommendation"]["direction"]  # "YES" | "NO"
record["outcome"]["status"]                       # "resolved"
record["outcome"]["actual_outcome"]               # numeric
```

Only records meeting all conditions are eligible:

- `outcome.status == "resolved"`
- recommendation direction is `YES` or `NO`
- `actual_outcome` is numeric, non-null, and non-negative

Skip records whose recommendation direction is `WAIT`, `AVOID`, missing, or
anything other than `YES` or `NO`.

Outcome correctness:

| Recommendation | Actual outcome | Recommendation correct |
| --- | --- | --- |
| `YES` | `> 0` | true |
| `YES` | `== 0` | false |
| `NO` | `== 0` | true |
| `NO` | `> 0` | false |

Evidence stance semantics:

- `supports` means the source supported the record recommendation direction.
- `refutes` means the source opposed the record recommendation direction, so it
  supported the opposite direction.
- `neutral` does not participate in attribution.

Source correctness:

| Evidence stance | Recommendation correct | Source correct |
| --- | --- | --- |
| `supports` | true | true |
| `supports` | false | false |
| `refutes` | true | false |
| `refutes` | false | true |

## 4. Domain Normalization

Aggregate by normalized domain, not by source display name.

`extract_domain(url)` must:

- lowercase the host
- strip a leading `www.`
- keep only the host/netloc
- tolerate URLs without a scheme, such as `reuters.com/path`
- return `None` for missing, empty, or invalid URLs

When a URL is missing or invalid, the evidence item is skipped. Do not fall back
to source display name in version 1 because that would mix domain identities
with arbitrary labels.

Use the same domain semantics as the existing source reliability extraction
logic when possible.

## 5. Category Semantics

Statistics are bucketed by category, usually source type.

Category resolution:

```text
category = evidence.source_type
        or record.source.type
        or record.source_type
        or "_unknown"
```

`_unknown` is a real category for evidence whose source type is unavailable.
It must not be confused with `_all`.

`_all` is a synthetic aggregate row across all real categories for the same
domain. Store writes must update both:

- `(domain, category)`
- `(domain, "_all")`

## 6. Per-Event Domain Grouping

Within one event, one domain/category pair should produce at most one
attribution.

Grouping key:

```text
(event_id, domain, category)
```

Rules inside the group:

- Ignore `neutral` evidence.
- If all non-neutral evidence has the same stance, emit one attribution with
  that stance.
- If both `supports` and `refutes` appear for the same domain/category in the
  same event, treat the group as mixed and skip it in version 1.
- Credibility is the mean of finite, non-null, non-neutral credibility values in
  the group after clipping each value to `[0.0, 1.0]`.
- If no usable credibility values remain, attribution `credibility` is `None`;
  the attribution still counts as one sample.

Mixed groups are skipped to avoid giving an apparently precise reliability
score to a source that supplied contradictory evidence for the same event.

## 7. SQLite Schema

Add a domain reliability store with aggregate rows and a ledger for
idempotency.

```sql
CREATE TABLE domain_reliability (
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
```

```sql
CREATE TABLE domain_reliability_ledger (
    event_id      TEXT NOT NULL,
    domain        TEXT NOT NULL,
    category      TEXT NOT NULL,
    correct       INTEGER NOT NULL,
    credibility   REAL,
    first_seen    TEXT NOT NULL,
    PRIMARY KEY (event_id, domain, category)
);
```

Invariants:

- `correct_count + wrong_count == sample_count`
- `sample_count` counts event/domain/category attributions, not raw evidence
  items
- `credibility_sum` sums only non-null attribution credibility values
- `_all` rows are derived from the same attribution set as category rows

The ledger is required for incremental `apply_resolution` idempotency. Without
it, a repeated resolve hook or backfill would double-count aggregate rows.

## 8. Re-Resolution Policy

Incremental tracking handles first-time resolution only.

If a resolved event's outcome or evidence is later corrected, the incremental
path will not attempt to reverse old aggregate counts. Corrections must be
handled by `rebuild_from_records`, which clears aggregates and ledger entries,
then recomputes from current event store records.

This keeps the version 1 store simple and makes the rebuild command the
authoritative repair path for historical changes.

## 9. Pure Service Module

Add:

```text
backend/app/services/domain_reliability_service.py
```

Public interface:

```python
def extract_domain(url: str) -> str | None:
    """Normalize URL to domain. Return None for invalid or missing URL."""


def attribute_evidence(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract per-domain attribution from one resolved event record.

    Returns:
        [
            {
                "event_id": str,
                "domain": str,
                "category": str,
                "stance": "supports" | "refutes",
                "credibility": float | None,
                "correct": bool,
            },
        ]
    """


def compute_reliability_stats(
    attributions: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Aggregate attributions into per-domain per-category stats."""


def compute_reliability_score(stats: dict[str, Any]) -> float | None:
    """Return correct_count / sample_count, or None when sample_count == 0."""
```

The service module is pure: no file I/O, no SQLite connection, no import of
global settings. It is the test surface for attribution semantics.

## 10. Store Module

Add:

```text
backend/app/memory/domain_reliability_store.py
```

Public interface:

```python
def apply_resolution(record: dict[str, Any]) -> None:
    """Incrementally apply one resolved event.

    Calls attribute_evidence(record). For each attribution, writes both the real
    category row and the domain _all row. Uses domain_reliability_ledger to skip
    already-processed event/domain/category attributions.
    """


def rebuild_from_records(records: list[dict[str, Any]]) -> None:
    """Clear and rebuild all aggregate and ledger rows from records."""


def get_stats(
    domain: str | None = None,
    category: str | None = None,
    min_samples: int = 0,
) -> list[dict[str, Any]]:
    """Query stats with optional filters."""


def get_domain_summary(domain: str) -> dict[str, Any] | None:
    """Return the _all row for one domain, if present."""
```

Returned stat rows:

```python
{
    "domain": str,
    "category": str,
    "sample_count": int,
    "correct_count": int,
    "wrong_count": int,
    "credibility_sum": float,
    "reliability_score": float | None,
    "credibility_avg": float | None,
    "insufficient_samples": bool,
    "first_seen": str,
    "last_updated": str,
}
```

`min_samples` filters rows. The configured confidence minimum only controls the
`insufficient_samples` flag and does not filter rows by itself.

## 11. Resolve Hook

Add a best-effort hook at the end of the event resolution flow, after outcome
write and calibration update:

```python
if settings.DOMAIN_RELIABILITY_TRACKING_ENABLED:
    try:
        from app.memory.domain_reliability_store import apply_resolution
        apply_resolution(record)
    except Exception:
        logger.warning("domain reliability tracking failed", exc_info=True)
```

Design decisions:

- Gated by `DOMAIN_RELIABILITY_TRACKING_ENABLED`.
- Default is disabled.
- Failure logs a warning and does not block resolution.
- Hook runs after calibration so existing quality metrics remain the primary
  resolution side effect.

## 12. CLI

Add:

```text
backend/scripts/domain_reliability_cli.py
```

Command shape:

```python
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="domain_reliability")
    subparsers = parser.add_subparsers(dest="command")

    sp_list = subparsers.add_parser("list")
    sp_list.add_argument("--domain", type=str, default=None)
    sp_list.add_argument("--category", type=str, default=None)
    sp_list.add_argument("--min-samples", type=int, default=0)
    sp_list.add_argument("--json", action="store_true")

    sp_rebuild = subparsers.add_parser("rebuild")
    sp_rebuild.add_argument("--limit", type=int, default=None)
    sp_rebuild.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
```

`list` reads the statistics table and prints ASCII-only text by default.

Text example:

```text
Domain Reliability Report - 15 domains

Domain               Category           Samples  Correct  Wrong  Reliability  Avg Cred
reuters.com          prediction_market       18       12      6       66.7%      0.82
reuters.com          _all                    25       17      8       68.0%      0.80
coindesk.com         prediction_market       12        5      7       41.7%      0.55
example.com          _unknown                 3        1      2       33.3%      0.40

Summary: 15 domains, 40 total samples, 65.0% avg reliability.
```

Use `N/A` only in text output for `None` scores or averages.

JSON example:

```json
{
  "domains": [
    {
      "domain": "reuters.com",
      "category": "prediction_market",
      "sample_count": 18,
      "correct_count": 12,
      "wrong_count": 6,
      "reliability_score": 0.667,
      "credibility_avg": 0.82,
      "insufficient_samples": false,
      "first_seen": "2026-01-15T00:00:00Z",
      "last_updated": "2026-07-03T00:00:00Z"
    }
  ],
  "total_domains": 15,
  "total_rows": 27
}
```

`rebuild` behavior:

- `rebuild` with no `--limit` performs a full rebuild and writes the store.
- `rebuild --dry-run` computes and prints what would be written, but does not
  write.
- `rebuild --limit N` is preview-only and does not write. This prevents partial
  event samples from replacing production statistics.

Rebuild output:

```text
Rebuilding domain reliability from event_store...
Processed 142 resolved events, 38 with valid attribution.
Wrote 27 domain/category rows (14 domains).
Done.
```

## 13. API Endpoint

Add a read-only endpoint in:

```text
backend/app/api/routes/quality_metrics.py
```

Route:

```python
@router.get("/quality-metrics/domain-reliability")
async def domain_reliability(
    domain: str | None = Query(default=None),
    category: str | None = Query(default=None),
    min_samples: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    from app.memory.domain_reliability_store import get_stats

    stats = get_stats(domain=domain, category=category, min_samples=min_samples)
    return {
        "domains": stats,
        "total_domains": len({s["domain"] for s in stats}),
        "total_rows": len(stats),
    }
```

Behavior:

- No auth; this is read-only and matches the quality metrics endpoint pattern.
- `min_samples` filters returned rows and allows `0`.
- No `limit` or `sample`; this queries aggregate statistics, not event records.
- JSON fields keep stable types. Use `null` for unavailable
  `reliability_score` or `credibility_avg`, not `"N/A"`.

## 14. Configuration

Add settings in `backend/app/core/config.py`:

```python
# Domain reliability tracking (LATER #2): source trust feedback loop stats.
DOMAIN_RELIABILITY_TRACKING_ENABLED: bool = (
    os.getenv("DOMAIN_RELIABILITY_TRACKING_ENABLED", "false").lower() == "true"
)
DOMAIN_RELIABILITY_DB_PATH: str = os.getenv(
    "DOMAIN_RELIABILITY_DB_PATH",
    "domain_reliability.db",
)
DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES: int = int(
    os.getenv("DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES", "5")
)
```

`DOMAIN_RELIABILITY_DB_PATH` should be resolved through the same database path
helper pattern used by other local SQLite stores so CLI execution from
different working directories does not create different files accidentally.

`DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES` controls the
`insufficient_samples` flag only. It does not filter rows. API callers can
filter with the explicit `min_samples` query parameter.

## 15. Test Strategy

Add tests:

```text
backend/tests/test_domain_reliability_service.py
backend/tests/test_domain_reliability_store.py
backend/tests/test_domain_reliability_cli.py
backend/tests/test_domain_reliability_endpoint.py
backend/tests/test_resolve_hook.py
```

### 15.1 Service Tests

`TestExtractDomain`:

| Test | Input | Assertion |
| --- | --- | --- |
| `test_normal_url` | `https://www.reuters.com/article/123` | `reuters.com` |
| `test_no_www` | `https://reuters.com/path?q=1` | `reuters.com` |
| `test_uppercase` | `https://WWW.Reuters.COM/` | `reuters.com` |
| `test_invalid_url` | `not a url` | `None` |
| `test_missing_url` | empty string | `None` |
| `test_no_scheme` | `reuters.com/path` | `reuters.com` |

`TestAttributeEvidence`:

| Test | Scenario | Assertion |
| --- | --- | --- |
| `test_yes_direction_correct_supports` | `YES`, outcome `100`, supports | correct true |
| `test_yes_direction_wrong_supports` | `YES`, outcome `0`, supports | correct false |
| `test_no_direction_correct_supports` | `NO`, outcome `0`, supports | correct true |
| `test_no_direction_wrong_supports` | `NO`, outcome `100`, supports | correct false |
| `test_refutes_flips_correctness` | `YES`, outcome `100`, refutes | correct false |
| `test_wait_direction_skipped` | direction `WAIT` | no attribution |
| `test_avoid_direction_skipped` | direction `AVOID` | no attribution |
| `test_unresolved_skipped` | outcome status pending | no attribution |
| `test_none_actual_outcome_skipped` | actual outcome `None` | no attribution |
| `test_negative_outcome_skipped` | actual outcome `-1` | no attribution |
| `test_neutral_evidence_skipped` | neutral evidence only | no attribution |
| `test_missing_url_skipped` | empty evidence URL | no attribution |
| `test_mixed_supports_refutes_skipped` | same domain supports and refutes | domain skipped |
| `test_same_domain_multiple_supports` | two supports from same domain | one attribution |
| `test_credibility_clipped` | credibility `1.5` | credibility `1.0` |
| `test_credibility_missing` | credibility missing or `None` | credibility `None`, sample still counts |
| `test_category_from_evidence` | evidence has `source_type` | category from evidence |
| `test_category_from_record_source_type` | evidence lacks source type | category from record |
| `test_category_unknown_fallback` | no source type anywhere | category `_unknown` |
| `test_unknown_is_not_all` | category fallback `_unknown` | category is not `_all` |

`TestComputeReliabilityStats`:

| Test | Input | Assertion |
| --- | --- | --- |
| `test_basic_aggregation` | 3 attributions, 2 domains | expected keys |
| `test_correct_plus_wrong_equals_sample` | mixed correctness | invariant holds |
| `test_credibility_sum` | credibility `0.8 + 0.6` | sum `1.4` |
| `test_empty_input` | empty list | empty dict |

`TestComputeReliabilityScore`:

| Test | Input | Assertion |
| --- | --- | --- |
| `test_normal` | correct `12`, sample `18` | about `0.667` |
| `test_zero_sample` | sample `0` | `None` |
| `test_all_correct` | correct `5`, sample `5` | `1.0` |

### 15.2 Store Tests

Use a temporary SQLite database.

| Test | Scenario | Assertion |
| --- | --- | --- |
| `test_apply_resolution_writes_rows` | one event with two domains | category and `_all` rows written |
| `test_apply_resolution_idempotent` | same event applied twice | samples do not double |
| `test_apply_resolution_ledger_prevents_dup` | same event applied twice | ledger rows stable, stats unchanged |
| `test_all_row_aggregates_across_categories` | same domain, two categories | `_all` sample count is combined |
| `test_unknown_category_not_all` | source type missing | `_unknown` row and `_all` row are distinct |
| `test_rebuild_clears_and_recomputes` | apply old events, rebuild new set | old data removed, new data correct |
| `test_rebuild_idempotent` | rebuild twice | identical results |
| `test_get_stats_filter_domain` | filter by domain | only requested domain |
| `test_get_stats_filter_category` | filter by category | only requested category |
| `test_get_stats_min_samples` | `min_samples=10` | low-sample rows filtered |
| `test_get_stats_returns_reliability_score` | normal row | score field present |
| `test_get_stats_zero_sample_returns_null_score` | zero-sample row | score is null/None |
| `test_get_stats_insufficient_flag` | sample below confidence minimum | flag true |

### 15.3 CLI Tests

| Test | Scenario | Assertion |
| --- | --- | --- |
| `test_cli_list_empty_exit_0` | empty DB | exit `0`, `0 domains` |
| `test_cli_list_json_shape` | `list --json` | JSON has `domains`, `total_domains`, `total_rows` |
| `test_cli_rebuild_dry_run` | `rebuild --dry-run` | exit `0`, DB unchanged |
| `test_cli_rebuild_limit_preview` | `rebuild --limit 5` | exit `0`, preview output, DB unchanged |
| `test_cli_rebuild_full` | `rebuild` without limit | exit `0`, DB written |
| `test_cli_rebuild_limit_does_not_write` | existing DB plus `--limit` | existing stats unchanged |
| `test_cli_no_emoji` | any text output | no emoji |
| `test_cli_list_ascii_only` | text output | ASCII-only |

### 15.4 API Endpoint Tests

| Test | Scenario | Assertion |
| --- | --- | --- |
| `test_endpoint_empty_db_returns_200` | empty DB | `200`, empty domains |
| `test_endpoint_returns_stats` | prefilled data | complete stat shape |
| `test_endpoint_filter_domain` | `?domain=reuters.com` | only that domain |
| `test_endpoint_filter_category` | `?category=prediction_market` | only that category |
| `test_endpoint_min_samples_filter` | `?min_samples=10` | low-sample rows filtered |
| `test_endpoint_reliability_score_null` | zero-sample row | score null |
| `test_endpoint_insufficient_flag` | below confidence minimum | flag true |
| `test_endpoint_invalid_min_samples` | `?min_samples=-1` | `422` |
| `test_endpoint_total_rows` | multiple rows | `total_rows` present |
| `test_endpoint_stable_json_types` | null score row | no string `N/A` in JSON |

### 15.5 Resolve Hook Tests

| Test | Scenario | Assertion |
| --- | --- | --- |
| `test_hook_disabled_by_default` | tracking disabled | no DB writes |
| `test_hook_on_resolve` | resolve one event | DB has corresponding rows |
| `test_hook_failure_does_not_block_resolve` | store raises exception | resolve succeeds, warning logged |
| `test_hook_idempotent_on_re_resolve` | same event resolved twice | sample count does not double |

### 15.6 Regression Tests

Run existing resolve and source reliability tests to ensure the hook does not
change established behavior when disabled:

```powershell
python -m unittest tests.test_event_resolve_service tests.test_source_reliability_service -v
```

## 16. Verification Checklist

Backend tests:

```powershell
python -m unittest tests.test_domain_reliability_service tests.test_domain_reliability_store tests.test_domain_reliability_cli tests.test_domain_reliability_endpoint tests.test_resolve_hook -v
python -m unittest tests.test_event_resolve_service tests.test_source_reliability_service -v
```

Compilation:

```powershell
python -m compileall app\services\domain_reliability_service.py app\memory\domain_reliability_store.py app\api\routes\quality_metrics.py scripts\domain_reliability_cli.py tests\test_domain_reliability_service.py tests\test_domain_reliability_store.py tests\test_domain_reliability_cli.py tests\test_domain_reliability_endpoint.py tests\test_resolve_hook.py
```

End-to-end smoke:

```powershell
python -m scripts.domain_reliability_cli rebuild --dry-run
python -m scripts.domain_reliability_cli rebuild
python -m scripts.domain_reliability_cli list
```

Repository checks:

```powershell
git diff --check
npm.cmd run typecheck
```

Frontend typecheck is included as a side-effect check even though this spec does
not change frontend code.

## 17. Implementation Order

1. Add `domain_reliability_service.py` and service tests.
2. Add `domain_reliability_store.py`, schema initialization, ledger, and store
   tests.
3. Add configuration settings.
4. Add resolve hook and hook tests.
5. Add CLI and CLI tests.
6. Add API endpoint and endpoint tests.
7. Run regression and verification commands.
