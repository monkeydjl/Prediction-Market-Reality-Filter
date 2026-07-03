# Domain Reliability Feedback Design (LATER #2 v2)

**Date:** 2026-07-04
**Spec gap:** LATER #2 v2 — feed domain reliability statistics back into `build_source_reliability` as a historical posterior prior
**Status:** Design written for user review

---

## 1. Goal

Close the source-trust feedback loop. The v1 domain reliability layer
(`docs/superpowers/specs/2026-07-03-domain-reliability-design.md`) records, per
domain, how often its evidence-backed stances were later correct. v2 feeds those
aggregate statistics back into `build_source_reliability` so the per-event source
reliability score is informed by historical accuracy, not just the static
registry / tier defaults.

v1 explicitly deferred this: "Feeding those scores back into
`build_source_reliability` is intentionally left for a later step." v2 is that
step.

## 2. Non-Goals

- No change to `domain_reliability_service` or `domain_reliability_store` write
  path. The statistics layer is reused as-is (read-only).
- No per-category reliability prior. v2 only consumes the `(domain, "_all")`
  aggregate row.
- No frontend dashboard changes.
- No CLI / API changes for the feedback itself (the existing
  `/quality-metrics/domain-reliability` endpoint already exposes the stats).
- No change to `build_source_reliability`'s downgrade threshold logic. The
  prior only adjusts `weighted_avg_tier_score`; the threshold gates that consume
  it are untouched.
- No new provenance flag for tier-default fallback. Only registry and
  domain-stats priors get distinct flags.

## 3. Layered Prior Semantics

v2 adopts a layered prior. Within `weighted_avg_tier_score`, each source's tier
score is selected by the following priority, highest first:

| Priority | Source | Condition |
| --- | --- | --- |
| 1 | `base_trust_override` (registry) | Source matched a registry entry whose `base_trust` is non-null |
| 2 | `shrunk_reliability` (domain stats) | Source's domain matched a stats entry with `sample_count > 0` (i.e. `shrunk is not None`) |
| 3 | `_TIER_SCORES[tier]` (default) | Fallback |

Formally:

> v2 adopts a layered prior: registry is an explicit operator override; domain
> reliability is a historical posterior fallback used when the registry does not
> cover a domain. The two sources are independent and have independent
> provenance, but the same source never has both applied simultaneously.

Rationale:

- The registry is hand-curated and explicit; it must have the highest priority.
- Domain reliability is automatic historical statistics; it is appropriate as a
  refinement of the default prior for domains the operator has not configured.
- Provenance is clear: `source_prior_affected` marks registry intervention;
  `domain_stats_prior_affected` marks historical-statistics intervention.
- A historical sample can never silently rewrite an operator-configured prior.

## 4. Shrunk Reliability Formula

Raw `correct_count / sample_count` is noisy at low sample counts. v2 applies
Beta(0.5K, 0.5K) shrinkage toward the 0.5 neutral point:

```
shrunk = (correct + 0.5 * K) / (sample + K)
```

- `K = settings.DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT` (default 5)
- Shrinkage point is fixed at 0.5 (neutral), fully decoupled from registry
  `base_trust` and `_TIER_SCORES[tier]`
- `sample == 0` → `shrunk = None` (the source falls through to priority 3)
- `K <= 0` → `shrunk = None` (silent degrade, see §6)
- `correct` is clamped to `[0, sample]` before the formula runs (defensive)

A source with `sample = 1, K = 5, correct = 1` yields `shrunk ≈ 0.583`, so a
single observation barely moves the score off neutral. With `sample = 50,
K = 5, correct = 40`, `shrunk ≈ 0.818` — the historical signal dominates.

## 5. Integration Point

### 5.1 Pure function: `build_source_reliability`

`backend/app/services/source_reliability_service.py` gains two new keyword-only
parameters:

```python
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
```

- `domain_stats_overrides` — projected list of `{domain, sample_count,
  correct_count}`. `None` means "flag off" (byte-identical to v1).
- `domain_reliability_shrinkage_pseudocount` — K, passed by the orchestrator
  from `settings.DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT`. The service does
  NOT import `settings`.

Each entry of `domain_stats_overrides` is expected to have exactly three
fields:

```python
{
    "domain": str,            # e.g. "reuters.com"
    "sample_count": int,
    "correct_count": int,
}
```

Other fields emitted by `domain_reliability_store.get_stats` (e.g.
`reliability_score`, `credibility_avg`) are NOT passed in — the service
recomputes the shrunk score internally to avoid double computation and to keep
the parameter shape minimal.

### 5.2 Per-source matching

Each source's domain is matched against `domain_stats_overrides` using
**longest domain suffix match**: `foo.reuters.com` matches `reuters.com`,
and a longer pattern wins over a shorter one. This mirrors the registry's
domain branch semantics; the implementation will reuse the same suffix-match
helper logic (function name to be decided at implementation time, not bound
to the existing `_match_registry_override` name).

When multiple overrides match, the longest suffix wins. When a source's
domain is `None` / empty / unparseable, no override matches and the source
falls through to priority 3.

### 5.3 Provenance flag

A new boolean `domain_stats_prior_affected` is emitted in the result dict
under the same conditional shape rule as `source_prior_affected`:

- `domain_stats_overrides is None` → field is NOT emitted (byte-identical to v1)
- `domain_stats_overrides is not None` → field IS emitted, value is `True` iff
  at least one source was actually assigned the shrunk-reliability score
  (priority 2 hit with `shrunk is not None`)

An empty list, a list with all-zero samples, or `K <= 0` all yield `False` —
they never cause a false-positive `True`.

### 5.4 Orchestrator: `event_intelligence_service`

`backend/app/services/event_intelligence_service.py` (the only production call
site of `build_source_reliability`) gains a new best-effort load block, placed
inside the existing `if settings.SOURCE_RELIABILITY_ENABLED:` guard and
alongside the existing `registry_overrides` block:

```python
# Existing registry block (unchanged)
registry_overrides: list[dict[str, Any]] | None = None
if settings.SOURCE_TRUST_REGISTRY_ENABLED:
    try:
        from app.memory import source_trust_registry_store
        registry_overrides = source_trust_registry_store.list_entries()
    except Exception as exc:
        logger.warning("source_trust_registry load failed: %s", exc, exc_info=True)
        registry_overrides = None

# New domain stats block (parallel structure)
domain_stats_overrides: list[dict[str, Any]] | None = None
if settings.DOMAIN_RELIABILITY_FEEDBACK_ENABLED:
    try:
        from app.memory import domain_reliability_store
        rows = domain_reliability_store.get_stats(category="_all", min_samples=0)
        domain_stats_overrides = [
            {
                "domain": r["domain"],
                "sample_count": r["sample_count"],
                "correct_count": r["correct_count"],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning(
            "domain_reliability load failed, continuing without stats prior: %s",
            exc, exc_info=True,
        )
        domain_stats_overrides = None

sr = build_source_reliability(
    evidence_breakdown=record.get("evidence_breakdown", []),
    evidence_items=filtered_articles or [],
    raw_direction=raw_direction,
    enabled=True,
    score_threshold=settings.SOURCE_RELIABILITY_SCORE_THRESHOLD,
    min_trusted_ratio=settings.SOURCE_RELIABILITY_MIN_TRUSTED_RATIO,
    min_domain_diversity=settings.SOURCE_RELIABILITY_MIN_DOMAIN_DIVERSITY,
    min_sources=settings.SOURCE_RELIABILITY_MIN_SOURCES,
    registry_overrides=registry_overrides,
    domain_stats_overrides=domain_stats_overrides,
    domain_reliability_shrinkage_pseudocount=settings.DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT,
)
```

The guard is `if settings.DOMAIN_RELIABILITY_FEEDBACK_ENABLED:` only — no
redundant `SOURCE_RELIABILITY_ENABLED` check, because the whole block already
lives inside the outer `if settings.SOURCE_RELIABILITY_ENABLED:`.

## 6. Error Handling and Edge Cases

| Scenario | Behavior |
| --- | --- |
| `DOMAIN_RELIABILITY_FEEDBACK_ENABLED=false` | `domain_stats_overrides=None`; function does not emit `domain_stats_prior_affected` (byte-identical to v1) |
| `SOURCE_RELIABILITY_ENABLED=false` | Entire load block skipped (outer guard); no `source_reliability` key on record |
| `domain_reliability_store.get_stats` raises | `logger.warning(..., exc_info=True)`, `domain_stats_overrides=None`, main flow continues |
| `K <= 0` | `_shrunk_reliability` returns `None`; `domain_stats_prior_affected=False`; main flow continues. No exception propagates. |
| Override entry missing `domain` key | Entry skipped |
| Override entry `sample_count` non-int or negative | `_shrunk_reliability` returns `None` via `sample <= 0` |
| Override entry `correct_count` out of `[0, sample]` | Clamped to `[0, sample]` |
| Multiple overrides match the same source | Longest domain suffix wins |
| `domain_stats_overrides=[]` (empty list) | Function returns normally, `domain_stats_prior_affected=False` |
| All sources have `sample_count == 0` | All `shrunk=None`, all sources fall to priority 3, `domain_stats_prior_affected=False` |
| Source's domain is `None` / empty | No override matches, source falls to priority 3 |

### 6.1 Key invariants

1. flag OFF → output shape is identical to v1 (no `domain_stats_prior_affected` key).
2. flag ON with no usable stats → `domain_stats_prior_affected=False` (key present
   because `domain_stats_overrides is not None`, but value is `False`).
3. Any load / parse / config failure (including `K <= 0`) → degrades to v1
   behavior. No exception is raised to the caller.

### 6.2 Degradation chain

```
Normal:     store load → project → shrunk computed → layered prior applied
  ↓ store exception
Degraded 1: domain_stats_overrides=None → registry + tier path (v1)
  ↓ registry also fails
Degraded 2: registry_overrides=None + domain_stats=None → pure tier path (v1 base)
  ↓ K <= 0
Degraded 3: domain_stats ignored, shrunk=None, tier path
```

Every degradation level is already-verified v1 behavior; v2 adds no new
failure modes.

## 7. Configuration

Two new settings in `backend/app/core/config.py`, alongside the existing
`DOMAIN_RELIABILITY_*` block:

```python
# Domain reliability feedback (LATER #2 v2): feed per-domain historical
# accuracy back into build_source_reliability as a layered prior.
DOMAIN_RELIABILITY_FEEDBACK_ENABLED: bool = _env_bool(
    "DOMAIN_RELIABILITY_FEEDBACK_ENABLED", "false"
)
DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT: int = int(
    os.getenv("DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT", "5")
)
```

- Both default to OFF / 5 for backward compatibility.
- `DOMAIN_RELIABILITY_FEEDBACK_ENABLED` is inert unless
  `SOURCE_RELIABILITY_ENABLED=true` (mirrors the registry's relationship).
- `DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT` (K) controls shrinkage strength;
  larger K pulls harder toward 0.5. K <= 0 is silently ignored as None (see §6).

## 8. Test Strategy

### 8.1 Pure function layer (`test_source_reliability_service.py`, new cases)

| Group | Cases |
| --- | --- |
| `_shrunk_reliability` unit | `sample>0, K=5` shrinks correctly; `sample=0` → None; `K=0` → None; `K<0` → None; `correct<0` clamps to 0; `correct>sample` clamps to sample |
| Layered priority | registry hit → uses `base_trust`; registry miss + stats hit → uses shrunk; both miss → tier; all absent → tier default |
| `domain_stats_prior_affected` flag | empty list → False; all-zero samples → False; at least one valid hit → True; `param=None` → field not emitted |
| Backward compat | `domain_stats_overrides=None` → output shape identical to v1 (no new field) |
| Longest suffix match | `foo.reuters.com` matches `reuters.com`; `reuters.com` matches `reuters.com`; no match → tier |
| Dirty data defense | override missing `domain` → skipped; non-int `sample_count` → skipped; multiple overrides match → longest suffix wins |

### 8.2 Orchestrator layer (`test_event_intelligence_service.py`, new cases)

| Group | Cases |
| --- | --- |
| flag OFF | `DOMAIN_RELIABILITY_FEEDBACK_ENABLED=false` → `build_source_reliability` called with `domain_stats_overrides=None`; `domain_reliability_store.get_stats` is NOT invoked |
| flag ON normal | flag ON + store has data → call receives projected three-field list |
| flag ON store failure | flag ON + store raises → `domain_stats_overrides=None`, `logger.warning` with `exc_info=True`, main flow continues |
| flag ON + SOURCE_RELIABILITY OFF | flag ON but `SOURCE_RELIABILITY_ENABLED=false` → `source_reliability` block does not execute; `domain_reliability_store` is neither imported nor called |
| Projection correctness | store returns full rows; passed list contains only `domain` / `sample_count` / `correct_count` fields |

### 8.3 Config layer (`test_domain_reliability_config.py`, extended)

| Test | Assertion |
| --- | --- |
| Two new settings exist | `DOMAIN_RELIABILITY_FEEDBACK_ENABLED`, `DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT` |
| Default values | `False`, `5` |

### 8.4 Regression

- Existing `test_source_reliability_service.py` cases: unchanged, not deleted
  (new params default to `None` / `5`, preserving backward compatibility).
- Existing `test_decision_quality_engine_integration.py` direct calls: unchanged.
- Existing `test_event_intelligence_service.py` source-reliability cases: unchanged.
- Full `python -m pytest -x -q` must pass with zero failures.

## 9. Self-Review Checklist

- [x] No "TBD" / "TODO" / placeholders
- [x] Internal consistency: §3 layered prior matches §5.1 parameter shape matches §6 error matrix
- [x] Scope: single feature, single call site, single plan-sized
- [x] No ambiguity: priority order, shrunk formula, flag semantics all explicit
- [x] K <= 0 handled as silent degrade, not raise
- [x] Domain matching named "longest suffix", not bound to legacy function name
- [x] Backward compat invariants explicit (§6.1)

## 10. Out of Scope (Future Work)

- Per-category reliability prior (consuming `(domain, category)` rows).
- Blending `credibility_avg` into the prior.
- Frontend surface for `domain_stats_prior_affected`.
- Feedback loop monitoring metrics (e.g. "what fraction of sources used shrunk
  prior this run").
