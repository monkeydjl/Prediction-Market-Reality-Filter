# Comprehensive Architecture Review

**Date:** 2026-06-20  
**System:** Prediction Market Reality Filter v0.3.0  
**Reviewer:** CTO-level audit, three perspectives  
**Method:** Read every call site, write path, model definition, error handler, and retry pattern — ground truth from implementation

---

## Table of Contents

| Part | Perspective | Focus |
|------|-------------|-------|
| **I** | Production Readiness | Can the 9-stage Reality Feedback Loop run unattended for 90 days? |
| **II** | Data Model | What is the actual data model, not what documentation says? |
| **III** | Operational Resilience | Can the system recover automatically after a 24-hour outage? |
| **IV** | Unified Issue Registry | All P0/P1/P2 issues, cross-referenced and de-duplicated |
| **V** | Unified Recommendations | Prioritized fix plan across all three perspectives |

---

## Persistence Architecture (Reference)

| Store | File | Type | Role |
|-------|------|------|------|
| Event Store | `event_store.json` | JSON dict | Canonical event records (upsert, resolve) |
| Event Audit | `event_audit.jsonl` | JSONL append-only | Probability trajectory, outcome markers |
| Loop DB | `v2_loop.db` | SQLite (WAL) | `predictions` + `event_market_links` tables |
| Event Cache | `event_cache.json` | JSON dict | 1h TTL LLM compute cache |

---

---

# Part I — Production Readiness

## I.1 Loop Overview

```
Scheduler (07:15/22:30 UTC cron)
  → Discover (fetch candidates from Polymarket/Manifold/Kalshi + news)
    → Event (LLM analysis → EventRecord)
      → Verified Link (M0 identity gate, fail-closed)
        → Freeze Prediction (M1 commitment, one event, one prediction)
          → Resolve Outcome (auto/manual settlement against market results)
            → Calibration (Brier score, skill score, grade)
              → Trust (M2 diagnosis, edge weighting)
                → Decision Report (M5 assembly for human review)
```

## I.2 Stage-by-Stage Failure Analysis

### Stage 1 — Scheduler

| Dimension | Assessment |
|-----------|-----------|
| **Data** | None created/persisted directly; orchestrates two cron jobs |
| **Can fail** | Process crash → scheduler dead; APScheduler internal crash; `misfire_grace_time=300` drops runs >5min late |
| **Recoverable?** | No — no process supervision, no auto-restart, no gap detection |
| **Observable?** | Startup log only. No healthcheck, no metrics, no dead-man's-switch |
| **Loop continues?** | Only if process stays alive. A 5+ minute outage loses one cycle permanently |

### Stage 2 — Discover

| Dimension | Assessment |
|-----------|-----------|
| **Data** | EventRecord + frozen Prediction row |
| **Persisted** | `event_store.json` (atomic), `event_audit.jsonl` (append), `v2_loop.db` (insert) |
| **Can fail** | LLM API expired → all analyses fail → `count=0`; all market APIs down → 0 candidates; disk full → batch abort; individual candidate failures are isolated |
| **Recoverable?** | No for API key expiry (silent zero-output until rotated). Source failures isolated per-candidate. Disk failure blocks entire stage |
| **Observable?** | `logger.info("count=%d")` only. No healthcheck tracking "last discover count > 0" |
| **Critical insight** | `use_cache=False` in scheduled run → cache bypassed → LLM is a hard dependency |

### Stage 3 — Event

| Dimension | Assessment |
|-----------|-----------|
| **Data** | EventRecord with probability, credibility, impact, evidence, intelligence_report |
| **Persisted** | `event_store.json` via `EventRecord.model_validate()` gate |
| **Can fail** | One bad record → `model_validate` raises → entire batch aborted. event_id collision → upsert preserves outcome/calibration/tracking |
| **Recoverable?** | No for the current batch. Next run with different data may succeed |
| **Invariant** | `save_events` preserves `outcome`/`calibration`/`tracking` across re-scans — correct and well-tested |

### Stage 4 — Verified Link (M0 Identity Layer)

| Dimension | Assessment |
|-----------|-----------|
| **Data** | MarketLink row: event_id → contract_id binding with `verified` flag |
| **Persisted** | `v2_loop.db` → `event_market_links` table; `UNIQUE(event_id, contract_id)` |
| **Can fail** | `AUTO_VERIFY_THRESHOLD=1.0` → only exact matches auto-verify → fuzzy matches go to `pending` |
| **Recoverable?** | Fail-closed by design: unverified links = no scoring. **Calibration starvation risk** when many events fall below threshold |
| **Design strength** | Dual-path settlement (contract-id primary + text-match fallback) prevents wording drift |

### Stage 5 — Freeze Prediction (M1 Commitment)

| Dimension | Assessment |
|-----------|-----------|
| **Data** | Prediction row: ai_probability, market_probability, raw_edge, trust, adjusted_edge, decision, diagnosis fields |
| **Persisted** | `v2_loop.db` → `INSERT ... ON CONFLICT(event_id) DO NOTHING`; `UNIQUE(event_id)` |
| **Can fail** | Non-market events gated → returns None; missing contract_id/probability → returns None; DB write failure |
| **Critical tension** | First-sight verdict is PERMANENT. Bad first analysis is forever calibration input. No re-freeze mechanism |

### Stage 6 — Resolve Outcome

| Dimension | Assessment |
|-----------|-----------|
| **Data** | Outcome dict + Calibration snapshot |
| **Persisted** | `event_store.json` (outcome + calibration), `event_audit.jsonl` (snapshot), `v2_loop.db` (scored/observed/voided) |
| **Can fail** | All 3 market APIs down → `"no_resolved_markets"`; **score_prediction fails AFTER outcome written → prediction orphaned (P0)** |
| **Recoverable?** | Individual failures isolated per-event. But orphaned predictions have NO recovery — event already has outcome, auto-resolve skips |

### Stage 7 — Calibration

| Dimension | Assessment |
|-----------|-----------|
| **Data** | Brier score, skill score, grade, trajectory context |
| **Persisted** | Event-level: on record in `event_store.json`. Prediction-level: computed on-demand from `predictions` table |
| **Can fail** | Missing probability → fallback to baseline (50.0). Non-finite Brier → filtered out. Feedback `_load_resolved_records` failure → returns `[]` (no-op) |
| **Recoverable?** | Pure math, no external dependencies. Most robust stage |
| **Note** | `CALIBRATION_FEEDBACK_ENABLED` defaults to `false`. Dormant-by-design until >= 8 samples per category |

### Stage 8 — Trust (M2 Disagreement Diagnosis)

| Dimension | Assessment |
|-----------|-----------|
| **Data** | trust (0..1), adjusted_edge, decision (act/watch/skip), liquidity_factor, qualified, segment diagnostics |
| **Persisted** | Frozen in `predictions` row at freeze time. Recomputable via `segment_skill() + diagnose()` |
| **Can fail** | All categories dormant → trust=0.5, decision capped at "watch" → **perpetual degraded mode for months** |
| **Cold-start math** | ~10 events/day, ~7-8 predictions, ~20 categories, min_samples=8 → most categories stay dormant for months |

### Stage 9 — Decision Report (M5)

| Dimension | Assessment |
|-----------|-----------|
| **Data** | Assembled report dict from prediction row + event record |
| **Persisted** | NOT persisted — assembled on-demand |
| **Can fail** | Event record missing → minimal report. Prediction missing → API error. Stale data not surfaced |
| **Recoverable?** | Pure assembly, no loop dependency. Terminal stage |

## I.3 90-Day Unattended Assessment

**Answer: No.** The architecture is sound but the operational musculature is missing.

**Most likely failure timeline:**
1. Day 1-7: Normal operation if manually started and stable
2. Day 7-30: API key expiry → silent zero-output, no alert
3. Day 30-60: Process crash → scheduler dead, no restart
4. Day 60-90: Cold-start → most categories still dormant, mostly "watch" decisions

**What works well:**
- Data durability (atomic writes, WAL, corruption isolation)
- Fail-closed design (wrong outcomes blocked, invalid events voided)
- Source isolation (one failing API doesn't crash the run)
- Conservative defaults (dormant trust, high verify threshold, disabled feedback)

---

---

# Part II — Data Model

## II.1 The Actual Data Model (code truth, not documentation)

### Event

```
Source of truth: event_store.json
Shape: { "<event_id>": { "first_seen": str, "last_updated": str, "record": {...} } }
event_id = SHA1(event_title)[:12] — NOT a UUID
```

| Property | Reality |
|----------|---------|
| **Mutable?** | **Yes.** Upsert on re-discovery overwrites probability, credibility, impact, evidence, source, intelligence_report, evidence_items |
| **Immutable fields** | `outcome`, `calibration`, `tracking` — preserved from existing record |
| **Append-only?** | **No.** Full read → in-place dict merge → atomic write |
| **Invariants** | `EventRecord.model_validate()` gates every write. `first_seen` never changes. `outcome`/`calibration`/`tracking` are write-once |

**Re-discovery behavior:** Same event_id, new analysis. Probability/credibility/evidence overwritten with new values. Outcome/calibration/tracking preserved. The record silently drifts — a frozen prediction's `ai_probability=58` may not match the event record's current `probability.estimated=72`.

### Prediction

```
Source of truth: v2_loop.db → predictions table
Shape: One row per event_id (UNIQUE constraint)
```

| Property | Reality |
|----------|---------|
| **Mutable?** | **Two-phase.** Phase 1 (INSERT): immutable — `ON CONFLICT DO NOTHING`. Phase 2 (UPDATE): mutable at resolve |
| **Append-only?** | **No.** One insert, one update. No version history |
| **Invariants** | `UNIQUE(event_id)`. Only market-derived events frozen. Status transitions: `open → scored` (act), `open → observed` (watch/skip), `open → voided` (invalid) |
| **Commitment** | First-sight verdict is permanent. Re-scan is silent no-op |

**Frozen diagnosis:** `trust`, `adjusted_edge`, `qualified`, `segment_n`, `segment_skill` all computed at freeze time via `diagnose()` and frozen. As `segment_skill()` changes with more scored predictions, frozen rows retain outdated values.

### Outcome

```
Source of truth: event_store.json → record["outcome"] (primary)
                  v2_loop.db → predictions.actual_outcome (secondary)
                  event_audit.jsonl → kind="outcome" snapshot (audit)
                  Written in THREE places with NO transaction boundary.
```

| Property | Reality |
|----------|---------|
| **Mutable?** | Write-once. Preserved across re-scans |
| **Invariants** | `status="resolved"` → admitted to calibration. `status="invalid"` → excluded, prediction voided |
| **Write sequence** | `resolve_event` (event_store) → `record_outcome` (audit) → `score_prediction` (predictions table). Three writes, no atomicity |

### Calibration — Two Independent Signals

| | Calibration A (Event-Level) | Calibration B (Prediction-Level) |
|---|---|---|
| **Source** | `event_store.json → record["calibration"]` | `v2_loop.db → predictions.brier_score` |
| **Scores** | LATEST audit-trail probability estimate | FROZEN first-sight `ai_probability` |
| **Read by** | `calibration_feedback_service` (component weighting, category shrinkage) | `segment_skill()` (trust gate), `calibration_summary()` (dashboard) |
| **Filter** | All `status="resolved"` events | `calibration_summary`: act only; `segment_skill`: act+watch |

**The split:** Same event. Two Brier scores. Two probability sources. No reconciliation. The feedback loop reads Calibration A (latest estimate → better numbers). The trust gate reads Calibration B (first-sight commitment → honest numbers). These two signals can disagree and work at cross-purposes.

### Trust

```
Source of truth: v2_loop.db → predictions table (frozen at freeze time)
                  Recomputable: segment_skill() + diagnose() (live)
```

| Property | Reality |
|----------|---------|
| **Mutable?** | Frozen values immutable. Live values change with every scored prediction |
| **Invariants** | `trust ∈ [0,1]`. Dormant → 0.5. Qualified → `clamp(skill, 0, 1)`. Unqualified → decision capped at "watch". `adjusted_edge = raw_edge × trust × liquidity_factor` |
| **Staleness** | `list_open_opportunities` ranks by frozen `adjusted_edge`. Day-1 prediction (dormant, trust=0.5) ranked BELOW identical Day-30 prediction (qualified, trust=0.85) even though the live trust for both is now 0.85 |

## II.2 Semantic Inconsistencies

| # | Inconsistency | Impact |
|---|---------------|--------|
| **SI-1** | Event record "write-once for resolution" but "last-write-wins for analysis" | Re-scanned event has different probability than when frozen. No one reconciling would know. |
| **SI-2** | Two Brier scores, two probability sources, one event | Feedback loop and trust gate read different calibration signals. They can work at cross-purposes. |
| **SI-3** | `decision='tracked'` — invisible rows | Schema default with no semantic meaning. All downstream consumers filter it out. |
| **SI-4** | `segment_skill()` and `calibration_summary()` read different populations | Trust gate shows n=8 (qualified). Calibration dashboard shows n=3. Undocumented divergence. |
| **SI-5** | Frozen trust ≠ live trust | Opportunity surface ranked by historical confidence, not current confidence. |

## II.3 Hidden Coupling

| # | Coupling | Risk |
|---|----------|------|
| **HC-1** | `resolve_with_calibration` crosses three stores with no atomicity | P0: partial state on failure, no recovery |
| **HC-2** | `event_id` is universal join key with no referential integrity | Orphaned rows in prediction table if event deleted |
| **HC-3** | `base_rate_category` extracted from untyped `legacy_analysis` dict | Silent "unknown" on schema change. No type safety |
| **HC-4** | Calibration feedback reads event_store, trust reads predictions table | Different data sources, filters, probability estimates. Never reconciled |

## II.4 Violated Invariants

| # | Invariant | Status |
|---|-----------|--------|
| **VI-1** | `outcome.status == 'resolved' ⇒ prediction.status ≠ 'open'` | **VIOLATED.** score_prediction failure after resolve_event success → prediction permanently orphaned |
| **VI-2** | "Scored prediction and event record agree on actual_outcome" | **NOT GUARANTEED.** No post-write verification |
| **VI-3** | `calibration_summary.n == segment_skill(category).n` | **VIOLATED by design.** Different filters produce different n-values |

## II.5 Future Migration Risks

| # | Risk |
|---|------|
| **MR-1** | No per-record version markers in event_store.json — schema change requires full-file migration |
| **MR-2** | `decision` column defaults to magic string `'tracked'` — accidental insertion invisible to all consumers |
| **MR-3** | Two calibration signals — refactoring to one breaks consumers of the other. No migration path |
| **MR-4** | `legacy_analysis` is untyped dict — category extraction has zero type safety |
| **MR-5** | SQLite migration uses structural detection (PRAGMA), not versioning — cannot handle renames or type changes |

## II.6 The Fundamental Tension

The system has two competing design goals:

1. **Commitment integrity** (the honest loop): Freeze at first sight. Never recompute. Score what we actually believed. → Prediction-level model.
2. **Estimate accuracy** (the best guess): Use latest probability for calibration. Score the most informed view. → Event-level model.

Both are valid. Having BOTH without explicit acknowledgment creates a semantic fault line.

---

---

# Part III — Operational Resilience

## III.1 Failure Scenario Matrix

### LLM API Outage

| Dimension | Finding |
|-----------|---------|
| **What happens** | Every `analyze_event()` fails. `discover_events` returns `count=0`. Scheduler sees "success" |
| **Detection** | `logger.warning` per-candidate. No structured metric. No zero-result alert |
| **Recovery** | **None.** Next run is 24h later. No retry. No backoff. No fallback |
| **Severity** | **P0.** LLM is the single hard dependency. No graceful degradation |

### Prediction Market API Outage

| Dimension | Finding |
|-----------|---------|
| **What happens** | Source isolation via `asyncio.gather(return_exceptions=True)`. Failed source → 0 candidates. All 3 down → 0 market candidates for discover, `"no_resolved_markets"` for auto-resolve |
| **Detection** | `logger.warning` per failed source. `by_source` dict shows counts |
| **Recovery** | **None.** Next cycle 24h later. No retry |
| **Severity** | **P1.** Multi-source isolation is well-designed. But all-three-down = full cycle loss |

### RSS / Official Source Failures

| Dimension | Finding |
|-----------|---------|
| **What happens** | `_fetch_one` catches all exceptions, returns `[]`. Source classes isolated. Individual feed failures **silently swallowed** |
| **Detection** | Source-class failures logged. Individual RSS feeds: no log |
| **Recovery** | **None.** Re-fetched next cycle |
| **Severity** | **P2.** Degraded evidence quality. A feed could be dead for weeks unnoticed |

## III.2 Scheduler Restart

| Behavior | Detail |
|----------|--------|
| **Grace window** | `misfire_grace_time=300` (5 min). Restart within 5 min → job fires. Beyond → permanently dropped |
| **Coalesce** | `True` — prevents backlog stampede |
| **Gap detection** | **None.** No persistent "last run" timestamp |
| **Severity** | **P1.** >5 minute outage = lost cycle. >24 hour outage = no way to know how many were lost |

## III.3 Process Crash Analysis

### Crash During `save_events`

`write_json_atomic`: tempfile → write → `os.replace`. Atomic. Either old file intact or new file complete. Corrupt JSON → quarantined to `.corrupt`. `read_json_strict` raises, aborting write. **Safe.**

### Crash During `resolve_with_calibration`

```
Line 130: resolve_event()        → event_store write
Line 132: record_outcome()       → audit log append
Line 139: score_prediction()     → predictions UPDATE
```

| Crash point | State | Recovery |
|-------------|-------|----------|
| Before line 130 | Event unresolved, prediction open | ✓ Auto-resolve retries next cycle |
| After 130, before 132 | Event resolved, no audit, prediction open | ✗ **Permanently orphaned** |
| After 132, before 139 | Event resolved, audit has snapshot, prediction open | ✗ **Permanently orphaned** |
| After 139 | All three stores consistent | ✓ Normal |

### Crash During SQLite Write

`writing()` context manager: single transaction, commit or rollback. Crash before commit → rolled back on next open. WAL mode + `synchronous=FULL` → committed data survives. **Safe within a single write block.**

### Crash During Audit Append

Partial line risk: yes. `_read_all` skips unparseable lines. Compaction removes them. **Low impact.**

## III.4 Disk Write Failure

| Storage | Behavior |
|---------|----------|
| `event_store.json` | Atomic write (tempfile + replace). Old file intact on failure |
| `event_audit.jsonl` | Append is either complete or not. Compaction is atomic |
| `event_cache.json` | Corrupt → return `{}`. Cache is ephemeral |
| `v2_loop.db` | `writing()` rolls back on exception. Data consistent |

**Disk-full scenario:** Event store write fails → `save_events` raises → `_persist_events` aborts → `logger.error`. Scheduler job "succeeds" (error caught in `_persist_events`).

## III.5 Retry Behavior

| Component | Retry? |
|-----------|--------|
| LLM API | **No** — 60s timeout, one shot |
| Market APIs | **No** — 30s timeout, source isolation masks it |
| RSS feeds | **No** — no timeout, `_fetch_one` swallows exceptions |
| SQLite writes | **No** — 30s lock wait, not retry |
| File writes | **No** — immediate fail |
| Scheduler jobs | **No** — failed job logged, next tick is 24h later |

**Zero retry anywhere.** Every failure is one-shot. The only "retry" is the next cron tick.

## III.6 Idempotency

| Operation | Idempotent? | Note |
|-----------|-------------|------|
| `save_events` | Yes (for identity) | BUT overwrites analysis fields |
| `freeze_prediction` | Yes | `ON CONFLICT DO NOTHING` |
| `score_prediction` | Yes | `WHERE status='open'` gate |
| `void_prediction` | Yes | Same pattern |
| `upsert_link` | Yes | `ON CONFLICT UPDATE` |
| `record_event` | **No** | Always appends. Compaction removes duplicates eventually |
| `record_outcome` | **No** | Always appends. Compaction keeps at most 1 outcome/event |
| `resolve_event` | **No** | **Silently overwrites** existing outcome dict |

## III.7 24-Hour Outage Recovery

**Scenario:** Process crashes at 06:00 UTC. Restarts at 06:00 UTC next day.

| What was missed | Recovery |
|-----------------|----------|
| 07:15 discover | **Permanently lost.** `misfire_grace_time=300` → 23h late → dropped. No backfill |
| 22:30 auto-resolve | **Partially recovered.** Tonight's 22:30 run will score events whose markets settled. But events not yet discovered have no predictions to score |

**Answer: No, the system cannot recover automatically from a 24-hour outage.** It survives (data intact), auto-resolve partially recovers, but the discover cycle is permanently lost with no gap detection. The operator must manually call API endpoints and check logs.

---

---

# Part IV — Unified Issue Registry

## P0 — Loop-Stopping / Data Loss

| # | Issue | Found In | Mechanism |
|---|-------|----------|-----------|
| **P0-1** | Three-store write in `resolve_with_calibration` has no atomicity | All three audits | Process crash between writes → event resolved but prediction open → permanently orphaned. No recovery |
| **P0-2** | LLM API outage → zero discovery for 24+ hours | Prod, Ops | `count=0` is not an error. No retry, no alert, no fallback |
| **P0-3** | No process supervision | Prod, Ops | Crash → scheduler dead until human restarts |
| **P0-4** | No API key health monitoring | Prod | Expired key → silent zero-output for days/weeks |
| **P0-5** | No healthcheck / dead-man's-switch | Prod | Cannot detect: scheduler running?, last discover >0?, last auto-resolve >0? |
| **P0-6** | No automated backup | Prod | Disk failure/corruption → ALL accumulated calibration lost |

## P1 — Calibration Quality / Degraded Recovery

| # | Issue | Found In | Mechanism |
|---|-------|----------|-----------|
| **P1-1** | Two independent calibration signals, no reconciliation | Data Model | Feedback reads A (latest est.), trust reads B (first-sight). Cross-purposes |
| **P1-2** | Cold-start deadlock: categories dormant for months | Prod, Data Model | min_samples=8, ~20 categories, ~7-8 preds/day → perpetual "watch" mode |
| **P1-3** | `AUTO_VERIFY_THRESHOLD=1.0` → only exact matches | Prod | Fuzzy matches → pending → never scored without human review |
| **P1-4** | First-sight verdict permanent, never updated | Prod, Data Model | Bad first analysis → permanent calibration contamination |
| **P1-5** | Frozen trust never updated | Data Model | Opportunity surface ranked by stale confidence |
| **P1-6** | All three market APIs down → zero auto-resolve | Prod, Ops | No retry, no cached resolution data |
| **P1-7** | `misfire_grace_time=300` drops runs after 5 minutes | Ops | >5min outage → permanently lost cycle |
| **P1-8** | Zero retry anywhere in the system | Ops | Transient error → candidate/source lost for 24h |
| **P1-9** | No persistent "last run" timestamp | Ops | Cannot detect how many cycles were missed |
| **P1-10** | `resolve_event` silently overwrites existing outcome | Ops | Re-resolution loses previous outcome with no audit |
| **P1-11** | `decision='tracked'` rows invisible to all consumers | Data Model | Schema default is a magic string; accidental use → silent exclusion |
| **P1-12** | Validation failure in one record aborts entire discover batch | Prod | N-1 valid events lost per bad LLM output |

## P2 — Future Maintenance / Observability

| # | Issue | Found In | Mechanism |
|---|-------|----------|-----------|
| **P2-1** | `event_audit.jsonl` unbounded growth between compactions | Prod | 5000-line threshold for 200/event compaction |
| **P2-2** | `event_store.json` grows without bound | Prod | No TTL, no archival, no limit |
| **P2-3** | SQLite WAL accumulation | Prod, Ops | No explicit checkpoint/truncation |
| **P2-4** | No database migration version tracking | Prod, Data Model | Structural detection fragile for renames/type changes |
| **P2-5** | `calibration_summary` reads entire scored set | Prod | O(n) in scored predictions |
| **P2-6** | `base_rate_category` from untyped `legacy_analysis` | Data Model | Silent "unknown" on schema change |
| **P2-7** | Individual RSS feed failures silently swallowed | Ops | Dead feed for weeks unnoticed |
| **P2-8** | `record_event` not idempotent | Ops | Duplicate audit lines survive until compaction |
| **P2-9** | No per-record version marker in event_store | Data Model | Full-file migration required on schema change |
| **P2-10** | No cross-process coordination | Ops | Single-process by design, not enforced |
| **P2-11** | No recovery runbook | Ops | Operator has no documented procedure |
| **P2-12** | No endurance test (24h/7d/30d) | Prod | Cannot verify loop survives extended unattended operation |
| **P2-13** | Two calibration signals → no refactoring path | Data Model | Choosing one breaks consumers of the other |
| **P2-14** | `segment_skill` / `calibration_summary` divergence undocumented | Data Model | Different n-values from different filters |

---

---

# Part V — Unified Recommendations

## Immediate (before any production deployment)

| # | Action | Addresses |
|---|--------|-----------|
| 1 | **Fix the three-store write atomicity.** Score prediction FIRST, then write outcome. If scoring fails, outcome is never written → auto-resolve can retry. | P0-1 |
| 2 | **Add process supervision** (systemd unit / PM2 config) with auto-restart | P0-3 |
| 3 | **Add `/api/health` endpoint** reporting: scheduler status, last discover count, last auto-resolve count, open predictions, pending links, last successful run timestamps | P0-2, P0-5, P1-9 |
| 4 | **Add API key validity check at startup** — fail fast if key is invalid | P0-4 |
| 5 | **Add automated daily backup** — `v2_loop.db` + `event_store.json` + `event_audit.jsonl` | P0-6 |

## High Priority (before unattended operation)

| # | Action | Addresses |
|---|--------|-----------|
| 6 | **Add LLM API retry** with exponential backoff (3 retries, 1s/2s/4s) | P0-2 |
| 7 | **Add `/api/health` alerting** — dead-man's-switch that fires if discover count stays 0 for 2+ cycles | P0-2, P0-5 |
| 8 | **Extend `misfire_grace_time`** to 86400 or persist "last successful run" and detect gaps | P1-7 |
| 9 | **Lower `AUTO_VERIFY_THRESHOLD`** to 0.85–0.90, or implement periodic human-review workflow | P1-3 |
| 10 | **Add market API retry** (2 retries, 1s/2s) | P1-6 |
| 11 | **Resolve the calibration fork.** Either: (a) pick ONE calibration signal and migrate all consumers, or (b) document the split explicitly and ensure consumers understand which they're reading | P1-1, SI-2, HC-4 |
| 12 | **Add edge trajectory scoring** — allow calibration to consider trajectory max edge, not just first-sight, or implement multi-snapshot prediction scoring | P1-4 |
| 13 | **Reduce cold-start friction** — lower `CALIBRATION_FEEDBACK_MIN_SAMPLES` to 5, or implement bootstrap mode | P1-2 |

## Medium Priority (within first 60 days of operation)

| # | Action | Addresses |
|---|--------|-----------|
| 14 | **Add per-record version marker** to `event_store.json` + migration framework | P2-9, MR-1 |
| 15 | **Replace `decision='tracked'` default** with a valid semantic value (e.g., `'skip'`) and migrate old rows | P1-11, SI-3, MR-2 |
| 16 | **Type `legacy_analysis`** or extract `base_rate_category` into a typed Pydantic field | HC-3, MR-4 |
| 17 | **Add SQLite migration version table** for safe schema evolution | P2-4, MR-5 |
| 18 | **Log individual RSS feed failures** (not just source-class failures) | P2-7 |
| 19 | **Add `PRAGMA wal_checkpoint(TRUNCATE)`** on scheduler startup | P2-3 |
| 20 | **Add startup temp-file cleanup** for `.tmp` files in store directory | P2-1 |
| 21 | **Document recovery runbook** — "system was down for N hours → here's what to do" | P2-11 |
| 22 | **Add endurance test** — simulate 7-day unattended loop with injected failures | P2-12 |

---

---

# Part VI — Final Assessment

### What the system gets right

| Principle | Evidence |
|-----------|----------|
| Fail-closed identity | `AUTO_VERIFY_THRESHOLD`, `get_verified_link()` gate — wrong outcomes are blocked |
| Commitment model | `INSERT ON CONFLICT DO NOTHING` — prevents hindsight bias |
| Re-scan safety | `outcome`/`calibration`/`tracking` preserved across re-discoveries |
| Source isolation | `asyncio.gather(return_exceptions=True)` — one failure doesn't crash all |
| Defensive math | `_clamp_pct()`, non-finite filtering, `_EPS` for division-by-zero |
| Pure function separation | `diagnosis`, `calibration`, `decision_report` are pure, testable |
| Dormant-by-design | Calibration feedback is no-op until sufficient samples |
| WAL-mode SQLite | Safe concurrent reads/writes |
| Atomic file writes | `tempfile + os.replace` prevents corruption |

### What needs attention

The system has a sound architectural skeleton. Three issues span all three audit perspectives:

1. **The three-store write in `resolve_with_calibration`** (P0-1) — found in production readiness (orphaned prediction), data model (violated invariant), and operational resilience (crash between writes). This is the single highest-priority fix.

2. **The calibration fork** (P1-1) — two independent Brier scores, different probability sources, different consumers. The data model audit identified the semantic split; the production audit identified that feedback and trust read different signals. This needs an architectural decision.

3. **Zero retry / zero alerting** (P0-2, P1-8) — every failure is one-shot, every outage is silent. The operational resilience audit catalogued every retry gap; the production audit identified the monitoring gap. Together they make unattended operation impossible.

### Answer to the three framing questions

| Question | Answer |
|----------|--------|
| Can it run unattended for 90 days? | **No.** No process supervision, no healthcheck, no alerting. With P0 fixes applied: yes, but in degraded mode for first 30-60 days. |
| What is the actual data model? | Event: mutable, last-write-wins for analysis, write-once for outcome/calibration. Prediction: insert-once, update-once, one row per event, frozen trust. Calibration: two independent signals. Trust: frozen at freeze time, stale in opportunity surface. |
| Can it recover after a 24-hour outage? | **No.** Data survives intact. Auto-resolve partially recovers. Discover cycle permanently lost. No gap detection. Operator must manually intervene. |
