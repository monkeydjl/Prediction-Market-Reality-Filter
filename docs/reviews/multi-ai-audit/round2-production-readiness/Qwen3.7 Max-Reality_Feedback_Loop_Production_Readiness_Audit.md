# Reality Feedback Loop - CTO Production-Readiness Audit

**Project:** Prediction Market Reality Filter
**Audit Scope:** Reality Feedback Loop (Scheduler → Discover → Event → Verified Link → Freeze Prediction → Resolve Outcome → Calibration → Trust → Decision Report)
**Date:** 2026-06-20
**Reviewer Role:** CTO — production-readiness gate

---

## Executive Summary

The Reality Feedback Loop is a well-architected single-node system that discovers prediction-market events, commits probability assessments, resolves outcomes against market settlements, and accumulates calibration data for trust-weighted decision-making. The codebase demonstrates disciplined engineering in its core invariants: atomic file writes, fail-closed identity verification, one-event-one-prediction commitment, and graceful degradation at every external boundary.

However, the system has critical gaps in **observability**, **cross-store transactional integrity**, and **automated recovery** that make unattended 90-day operation inadvisable without intervention. The loop *can* run unattended, but it will accumulate silent failures that erode the calibration dataset — the very asset that gives the system its value.

**Verdict: CONDITIONAL PASS — requires 3 P0 fixes before unattended deployment.**

---

## Stage-by-Stage Analysis

### Stage 1: Scheduler

**Implementation:** `backend/app/core/scheduler.py` — APScheduler `AsyncIOScheduler` with `CronTrigger`.

**What data is created:** Job triggers for discovery (07:15 UTC) and auto-resolve (22:30 UTC).

**What data is persisted:** None — the scheduler is a pure orchestrator with no state of its own.

**What can fail:**

| Failure Mode | Impact | Likelihood |
|---|---|---|
| Process crash / OOM kill | No jobs fire until restart | Medium |
| Import error in job function | Job silently fails every fire | Low |
| APScheduler internal error | Jobs stop firing | Very Low |
| Missed fire (grace_time=300s exceeded) | Job dropped, logged | Medium |
| Overlapping fire (max_instances=1) | Second fire dropped | Low |

**Recoverable?** Partially. `coalesce=True` and `misfire_grace_time=300` handle brief outages. A process crash requires external restart (systemd, Docker restart policy, or equivalent) — there is no self-healing.

**Observable?** Only via `logger.info` / `logger.exception`. No metrics, no health endpoint, no alerting. If the scheduler silently stops firing jobs, there is no signal.

**Can the loop continue automatically?** Yes, if the process is alive. A single job failure does not affect subsequent fires. But there is no retry — a failed run is simply lost until the next cron fire.

---

### Stage 2: Discover

**Implementation:** `backend/app/services/event_intelligence_service.py` — `_collect_candidate_events()`.

**What data is created:** Raw candidate events from Polymarket, Manifold, Kalshi, optionally Polymarket Crypto and open-web extraction.

**What data is persisted:** None at this stage — candidates exist only in memory.

**What can fail:**

| Failure Mode | Impact | Likelihood |
|---|---|---|
| All market APIs down | Zero candidates → empty scan | Low |
| Single market API down | Reduced candidate pool | Medium |
| Rate limiting by market APIs | Partial or zero candidates | Medium |
| Network timeout (30s per source) | Source dropped for this scan | Medium |

**Recoverable?** Yes. Each source is isolated via `asyncio.gather(return_exceptions=True)`. A failing source is logged and skipped; remaining sources continue. The next scan retries all sources.

**Observable?** `logger.warning` on source failure with `[label]: error`. No aggregation of failure rates over time.

**Can the loop continue automatically?** Yes. Zero candidates produces an empty scan, which is a valid (if unproductive) loop iteration.

---

### Stage 3: Event (Analysis)

**Implementation:** `event_intelligence_service.py` — `process_event()` → `analyze_event()` → `ai_analysis_service.analyze_market()` + `cross_validation_service.cross_validate()`.

**What data is created:** Fully analyzed `EventRecord` with probability estimate, confidence, evidence profile, credibility score, impact score, cross-validation result, and optional calibration feedback adjustment.

**What data is persisted:** None at this stage — analysis exists only in memory until `_persist_events`.

**What can fail:**

| Failure Mode | Impact | Likelihood |
|---|---|---|
| Primary LLM (DeepSeek) down | Deterministic fallback used | Medium |
| Cross-validation LLM (Qwen) down | No cross-validation, credibility unadjusted | Medium |
| LLM returns unparseable JSON | Deterministic fallback triggered | Medium |
| Google News API failure | Reduced evidence, lower confidence | Medium |
| Embedding API failure | Semantic relevance skipped, keyword-only filtering | Low |
| Single candidate analysis exception | That candidate dropped (WARNING logged) | Low |

**Recoverable?** Partially. The deterministic fallback (`build_deterministic_fallback_analysis`) produces a usable analysis when the LLM is down, using evidence direction × strength × relevance × freshness. This is well-designed. However, the LLM cost for that analysis is wasted, and there is no retry — the fallback result is final for this scan.

Cross-validation failure degrades gracefully: the analysis continues without credibility adjustment.

**Observable?** `logger.warning` on LLM fallback, cross-validation failure, and candidate drops. The fallback analysis includes `narrative_type: "evidence_fallback"` as a machine-readable signal.

**Critical concern:** If the LLM is down for an extended period (days), ALL analyses use the deterministic fallback. The resulting probability estimates are materially lower quality, but the system produces output that looks normal. Without log monitoring, this degradation is invisible.

**Can the loop continue automatically?** Yes. The fallback ensures every candidate produces an analysis. Quality degrades but the pipeline never stalls.

---

### Stage 4: Verified Link

**Implementation:** `backend/app/memory/event_market_link_store.py` — SQLite `event_market_links` table with `UNIQUE(event_id, contract_id)`.

**What data is created:** `MarketLink` record binding an event to a prediction-market contract. Fields: `link_method` (auto/manual), `link_confidence`, `verified` (bool).

**What data is persisted:** SQLite row in `v2_loop.db`. Survives restarts.

**What can fail:**

| Failure Mode | Impact | Likelihood |
|---|---|---|
| SQLite write failure (disk full, locked) | Link not recorded | Very Low |
| Fuzzy match below `AUTO_VERIFY_THRESHOLD` (1.0) | Link recorded but `verified=0` → NOT scored | High (by design) |
| Multiple contracts match one event | Multiple links created; `get_verified_link` returns first verified | Low |
| Contract ID changes (market platform migration) | Orphaned link, new contract unmatched | Medium |

**Recoverable?** Yes. `upsert_link` is idempotent. Unverified links are preserved for human review. The `list_pending()` function surfaces unverified links.

**Observable?** Links are queryable via the API. No alerting on verification failures or orphaned links.

**Fail-closed gate:** This is one of the system's strongest design choices. An unverified link means the event is NEVER scored against a market outcome. This prevents false settlements from corrupting the calibration dataset.

**Can the loop continue automatically?** Yes. Unverified links are bypassed during resolution; the loop continues with verified links only.

---

### Stage 5: Freeze Prediction

**Implementation:** `backend/app/memory/prediction_store.py` — `freeze_prediction()` writing to SQLite `predictions` table.

**What data is created:** `Prediction` record — a frozen point-in-time commitment: `ai_probability`, `market_probability`, `raw_edge`, `trust`, `adjusted_edge`, `decision` (act/watch/skip), `base_rate_category`, liquidity/volume snapshot.

**What data is persisted:** SQLite row with `UNIQUE(event_id)`. `ON CONFLICT(event_id) DO NOTHING` — the first commitment is immutable.

**What can fail:**

| Failure Mode | Impact | Likelihood |
|---|---|---|
| SQLite write failure | Prediction not frozen | Very Low |
| Non-market event (no contract_id or probability) | `freeze_prediction` returns `None` — correct no-op | High (normal) |
| `_persist_events` Stage 1 failure (event store write) | Entire batch aborted — freeze never called | Low |
| `_persist_events` Stage 3 per-event failure | This event's prediction not frozen; WARNING logged | Low |

**Recoverable?** Yes. A missed freeze is recovered on the next scan — if the event is re-discovered, `_persist_events` will attempt freeze again. Since `ON CONFLICT DO NOTHING`, a duplicate freeze is a safe no-op.

**Observable?** Per-event `WARNING` on freeze failure. The `predictions` table is queryable for open/scored/observed/voided counts.

**Can the loop continue automatically?** Yes. A missing prediction means the event cannot be scored at resolution time, but the event itself is still stored and tracked.

---

### Stage 6: Resolve Outcome

**Implementation:** `backend/app/services/event_resolve_service.py` — `auto_resolve_events()` and `resolve_with_calibration()`.

**What data is created:** `Outcome` record (status, actual_outcome, confidence, resolved_at, source) and `Calibration` record (brier_score, skill_score, grade, trajectory observations).

**What data is persisted:** FOUR stores touched in sequence:
1. `event_store.json` — outcome + calibration attached to event record
2. `event_audit.jsonl` — outcome snapshot appended
3. `v2_loop.db` predictions table — `score_prediction()` or `void_prediction()`
4. `v2_loop.db` event_market_links table — `upsert_link()` for fuzzy matches

**What can fail:**

| Failure Mode | Impact | Likelihood |
|---|---|---|
| All market resolve APIs down | No resolved markets fetched → "no_resolved_markets" returned | Low |
| Single market resolve API down | Resolved markets from that source missed | Medium |
| Event store write failure | Outcome not persisted; exception propagates | Very Low |
| Prediction score/void failure | Event resolved but prediction not scored — INCONSISTENT STATE | Low |
| Partial failure across 4 stores | INCONSISTENT STATE — some stores updated, others not | Low |
| Fuzzy match produces unverified link | Link recorded, event NOT scored (fail-closed) | High (by design) |
| `resolved_limit=200` exceeded | Resolved markets beyond 200 per source missed | Low |

**Recoverable?** **NO — this is the most critical gap.** The four stores are touched without a distributed transaction. If `resolve_event()` succeeds (event_store.json updated) but `score_prediction()` fails (SQLite), the event is marked resolved but the prediction remains `open`. On the next resolve run, the event is skipped (already resolved), and the prediction is never scored. This is a **permanent data inconsistency** that degrades calibration quality.

There is no reconciliation mechanism to detect or repair this state.

**Observable?** Per-event `WARNING` on failure. Summary dict at end of run. But no consistency check between event outcomes and prediction statuses.

**Can the loop continue automatically?** Yes, but with accumulating inconsistencies. Each partial failure creates a "zombie" — a resolved event with an unscore prediction — that permanently reduces the calibration dataset.

---

### Stage 7: Calibration

**Implementation:** `backend/app/services/calibration_service_event.py` (scoring math) + `backend/app/services/calibration_feedback_service.py` (closed-loop feedback).

**What data is created:** Brier scores, skill scores, grades (EXCELLENT/GOOD/ACCEPTABLE/POOR/RANDOM_LEVEL), component weights for probability fusion.

**What data is persisted:** Calibration is persisted as part of the event record (Stage 6). The feedback service reads resolved records from `event_store.json` at call time — no separate persistence.

**What can fail:**

| Failure Mode | Impact | Likelihood |
|---|---|---|
| `_load_resolved_records()` fails silently | Entire feedback pipeline goes dormant — returns `[]` | **LOW but CRITICAL** |
| Non-finite Brier value in historical data | Silently skipped in aggregation | Low |
| `min_samples` gate not met (< 8 resolved) | Feedback dormant by design | High (early stage) |
| Grade boundary magic numbers produce misleading grades | User trust in grades eroded | Medium |

**CRITICAL SILENT FAILURE:** `_load_resolved_records()` in `calibration_feedback_service.py` catches `Exception` and returns `[]` **without any logging**. If the event store is corrupt, locked, or the read fails for any reason, the entire calibration feedback pipeline goes dormant with zero signal. The published probabilities revert to raw LLM estimates without cross-validation adjustment — a significant quality degradation that is completely invisible.

**Recoverable?** The math is stateless — each invocation recomputes from current data. If the store read recovers, feedback recovers. But the silent failure means recovery depends on an operator noticing the problem.

**Observable?** **NO.** This is the single most dangerous observability gap in the system. A silent `[]` return from `_load_resolved_records()` means:
- Component weights are not computed
- Probability fusion does not happen
- Base-rate shrinkage is not applied
- All published probabilities are raw LLM output

And there is **no log, no metric, no alert** indicating this has happened.

**Can the loop continue automatically?** Yes, but in a degraded state. The loop produces output that looks valid but is based on uncalibrated probabilities.

---

### Stage 8: Trust (Diagnosis)

**Implementation:** `backend/app/services/diagnosis_service.py` — `calibration_trust()`, `liquidity_factor()`, `decide()`, `diagnose()`.

**What data is created:** Trust score (0-1), liquidity factor, adjusted edge, decision verdict (act/watch/skip), diagnosis reasoning.

**What data is persisted:** Trust is computed at analysis time and stored as part of the prediction record (Stage 5). Diagnosis is computed on-demand for decision reports.

**What can fail:**

| Failure Mode | Impact | Likelihood |
|---|---|---|
| Zero resolved predictions in segment | Trust = `DORMANT_TRUST` (0.5) — neutral | High (early stage) |
| Misconfigured settings (act_edge < watch_edge) | Unexpected decision verdicts | Very Low |
| Empty segment_stats | Safe defaults via `.get()` | Low |

**Recoverable?** Yes. Trust is recomputed from current calibration data on every invocation. Once enough predictions are resolved, trust naturally converges.

**Observable?** Trust values are stored in the predictions table and visible in decision reports. No alerting on trust anomalies (e.g., trust stuck at dormant level).

**Can the loop continue automatically?** Yes. Trust gracefully defaults to neutral when data is insufficient.

---

### Stage 9: Decision Report

**Implementation:** `backend/app/services/decision_report_service.py` — `build_decision_report()`.

**What data is created:** Structured report combining prediction, diagnosis, calibration, and recommendation.

**What data is persisted:** On-demand only — reports are not stored, they are computed when requested.

**What can fail:**

| Failure Mode | Impact | Likelihood |
|---|---|---|
| Prediction without corresponding event record | Handled: `record = record or {}` | Low |
| Missing fields in input dict | Handled: `.get()` with defaults throughout | Low |

**Recoverable?** N/A — reports are stateless computations.

**Observable?** Reports are the primary user-facing output. Quality depends entirely on upstream stages.

**Can the loop continue automatically?** Yes. Reports are always producible given a prediction record.

---

## Cross-Cutting Analysis

### Persistence Architecture

The system uses a **dual-storage** model:

| Store | Technology | Purpose | Atomicity | Thread Safety |
|---|---|---|---|---|
| `event_store.json` | JSON file | Event records (flexible schema) | Atomic write (tempfile + os.replace) | Per-file RLock |
| `event_audit.jsonl` | JSONL append-only | Probability trajectory log | Atomic rewrite on compaction | Per-file RLock |
| `v2_loop.db` (predictions) | SQLite WAL | Frozen predictions | Transaction per operation | Process-wide write Lock |
| `v2_loop.db` (links) | SQLite WAL | Event-market links | Transaction per operation | Same write Lock |
| `event_cache.json` | JSON file | LLM compute cache (1h TTL) | Atomic write | Per-file RLock |

**Strengths:**
- Atomic file writes prevent corruption on crash
- WAL mode enables concurrent SQLite reads during writes
- `read_json_strict` prevents the "corrupt → read empty → write empty" data loss cycle
- Per-file locking prevents cross-store contention

**Weaknesses:**
- No cross-store transactions (JSON + SQLite writes are independent)
- No `fsync` — a power loss between `os.replace` and disk flush could lose the write (narrow window)
- Single write lock serializes all SQLite writes across both tables
- No connection pooling — every operation opens and closes a connection
- No automated backups or point-in-time recovery

### Error Handling Philosophy

The codebase follows a consistent **"degrade, don't crash"** philosophy:

1. External API failures are isolated (one source down ≠ all sources down)
2. LLM failures trigger deterministic fallbacks
3. Per-event errors don't kill batch operations
4. Scheduler jobs catch all exceptions to prevent process death

This is the correct philosophy for a long-running autonomous system. However, the implementation has a critical gap: **degradation without notification**.

### Observability Assessment

| Signal | Present? | Actionable? |
|---|---|---|
| Job start/end logging | Yes | No — no alerting |
| Per-event error logging | Yes | No — requires log tailing |
| LLM fallback logging | Yes (WARNING) | No — easily lost in log volume |
| Source failure logging | Yes (WARNING) | No — no aggregation |
| Health endpoint | **NO** | N/A |
| Metrics/counters | **NO** | N/A |
| Alerting (email/webhook/pager) | **NO** | N/A |
| Consistency check (event vs prediction) | **NO** | N/A |
| Calibration feedback health signal | **NO** | N/A |
| Audit log size/age monitoring | **NO** | N/A |

**The system is observable only to someone actively tailing logs.** For unattended operation, this is a P0 gap.

### Retry and Recovery

| Operation | Retry Logic | Recovery Path |
|---|---|---|
| Market API fetch | None (beyond httpx timeout) | Next scan retries all sources |
| LLM call | OpenAI SDK `max_retries=2` | Deterministic fallback |
| Cross-validation LLM | OpenAI SDK `max_retries=2` | Skip cross-validation |
| SQLite write | None | Next operation retries |
| JSON file write | None | Next scan retries (for persist) |
| Resolve (cross-store) | None | **NO RECOVERY — permanent inconsistency** |
| Calibration store read | None | Returns `[]` silently — **NO RECOVERY** |

---

## Issue Classification

### P0 — Stops the Loop or Silently Corrupts Calibration

**P0-1: Non-Atomic Cross-Store Resolution (Stage 6)**

`resolve_with_calibration()` touches 4 stores sequentially without a transaction. Partial failure creates permanent "zombie" records: events marked resolved but predictions unscored. These zombies are never retried (the event is skipped on subsequent resolve runs because it's already resolved). Each zombie permanently reduces the calibration dataset.

**Impact:** Over 90 days, zombie accumulation could materially reduce the calibration sample size, degrading trust scores and decision quality.

**Fix:** Add a reconciliation pass in `auto_resolve_events()` that detects resolved events with `open` predictions and scores them. Alternatively, implement a two-phase commit pattern with a "pending resolution" state.

**P0-2: Silent Calibration Feedback Failure (Stage 7)**

`_load_resolved_records()` in `calibration_feedback_service.py` catches `Exception` and returns `[]` without logging. When this fails, the entire feedback pipeline goes dormant — probabilities are uncalibrated — with zero signal to operators.

**Impact:** Calibrated probabilities silently degrade to raw LLM output. The system produces output that looks normal but is materially lower quality. This could persist for weeks without detection.

**Fix:** Add `logger.error` to the except block. Add a health signal (e.g., a counter or flag) that indicates whether feedback is active or dormant due to error.

**P0-3: No Process Health Monitoring or Alerting (Cross-Cutting)**

The scheduler can silently stop firing jobs, the LLM can silently fall back to deterministic mode, and calibration can silently go dormant. There is no external health signal, no metrics endpoint, no alerting integration.

**Impact:** An operator not actively tailing logs cannot detect that the system is degraded or dead. Unattended 90-day operation requires at minimum a heartbeat signal and failure alerts.

**Fix:** Add a `/health` endpoint that reports: last discovery run timestamp, last resolve run timestamp, calibration feedback status (active/dormant), and counts of open/scored/voided predictions. Add a dead-man's-switch alert if no successful runs occur within 2× the cron interval.

---

### P1 — Degrades Calibration Quality

**P1-1: Batch Atomicity in `save_events` (Stage 3)**

One malformed `EventRecord` in a batch of N kills the entire batch. The other N-1 valid records are lost along with the LLM cost to analyze them.

**Impact:** A single bad record wastes an entire scan's LLM spend. At current scale (10 events/scan, ~$0.01-0.05/event), the dollar cost is trivial. But the lost calibration data accumulates.

**Fix:** Switch to per-event persistence with isolated error boundaries, matching the pattern already used for audit and freeze stages.

**P1-2: No Retry on Transient Failures (Cross-Cutting)**

Beyond the OpenAI SDK's built-in `max_retries=2`, there is zero application-level retry. A transient SQLite `OperationalError: database is locked` or a 31-second network timeout (exceeding the 30s limit by 1 second) is a permanent failure for that run.

**Impact:** Marginally fewer resolved predictions per run. Over 90 days, the cumulative effect is small but non-zero.

**Fix:** Add retry with exponential backoff (3 attempts, 1s/2s/4s) for SQLite writes and external API calls.

**P1-3: `resolved_limit=200` Cap (Stage 6)**

`auto_resolve_events` fetches at most 200 resolved markets per source. If a platform has more than 200 resolved markets (likely for Polymarket over time), older resolutions are missed.

**Impact:** Missed resolutions mean open predictions that could have been scored, reducing calibration data.

**Fix:** Paginate resolved market fetches, or increase the limit to a safe ceiling (e.g., 1000).

**P1-4: LLM Fallback Quality Gap (Stage 3)**

The deterministic fallback produces plausible-looking output labeled `narrative_type: "evidence_fallback"`, but the probability estimates are materially lower quality than LLM analysis (max 22-point shift from baseline, no reasoning). During extended LLM outages, the system accumulates low-quality predictions that will eventually be scored, producing misleading calibration data.

**Impact:** Calibration scores for fallback-generated predictions do not reflect true system accuracy. If 30% of predictions used the fallback, the aggregate Brier score is a blend of LLM quality and fallback quality — neither is accurately represented.

**Fix:** Tag predictions with an `analysis_mode` field (llm/fallback). Segment calibration reporting by analysis mode. Consider excluding fallback predictions from headline calibration metrics.

**P1-5: Event ID Collision Risk (Cross-Cutting)**

`event_id` is SHA-1 truncated to 12 hex characters (48 bits). Birthday paradox collision at ~16.7M events. While unlikely at current scale, the risk grows with time and is irreversible when it occurs (two different events sharing an ID would be merged).

**Impact:** A collision would merge two unrelated events, corrupting both their outcomes and calibration data.

**Fix:** Increase to 16 hex characters (64 bits, collision at ~5.3B events) or use the full SHA-1 hex (40 chars).

---

### P2 — Future Maintenance Cost

**P2-1: Hardcoded Magic Numbers Across Modules**

Grade boundaries (0.05/0.10/0.15/0.20), `_EPS=0.01`, `_MAX_SHRINK=0.5`, `_RANDOM_BRIER=0.25`, `max_deviation=35.0`, trust weights (30/25/20/15/10), cron times (07:15/22:30), `Semaphore(4)`, `resolved_limit=200` — all scattered across modules without a central configuration or documentation of their rationale.

**Fix:** Consolidate into `config.py` with documented defaults and rationale.

**P2-2: No Automated Backups**

There is a single manual backup (`backup-20260612-181108.tar.gz`). No scheduled backup of `event_store.json`, `event_audit.jsonl`, or `v2_loop.db`. A disk failure would lose the entire calibration dataset.

**Fix:** Add a daily backup cron job that copies the three data files to a timestamped archive.

**P2-3: `_LOCKS` Dict Never Evicts (file_store.py)**

Per-file RLocks are created lazily and never cleaned up. The set of store files is currently fixed, so this is not a practical issue, but it's a latent leak if dynamic paths are ever introduced.

**P2-4: No Graceful Shutdown (scheduler.py)**

`stop_scheduler()` uses `shutdown(wait=False)`. If a resolve job is mid-write (halfway through the 4-store resolution sequence), the process exits with partial state. This is a narrower version of P0-1.

**Fix:** Use `shutdown(wait=True)` with a timeout, and add a "resolution in progress" flag that the resolve service checks before starting a new write sequence.

**P2-5: Aspirational Schema vs Implementation Gap**

`docs/user/DATABASE_DESIGN.md` defines a complete relational schema (events, evidence, probability_assessments, market_snapshots, etc.) that has not been migrated to SQLite. The JSON file store works but diverges from the documented design, creating confusion for new contributors.

**Fix:** Either implement the documented schema or update the documentation to match reality.

**P2-6: Cross-Validation Client Singleton Without Lock**

`_second_client` and `_client` in the LLM services are module-level singletons initialized without thread safety. Under asyncio this is safe; under multi-threaded deployment it could create duplicate clients.

**P2-7: Audit Compaction Blocks Appends**

`_maybe_compact` holds the file lock during the full read-rewrite-replace cycle. At 5000+ lines, this could take several seconds, blocking concurrent audit appends. Currently acceptable because audit writes are infrequent (2× daily), but becomes a bottleneck if scan frequency increases.

---

## Data Flow Integrity Matrix

| Stage | Creates | Persists | Survives Restart | Survives Disk Full | Survives API Outage |
|---|---|---|---|---|---|
| Scheduler | Job triggers | — | Requires external restart | N/A | N/A |
| Discover | Raw candidates | — | No (in-memory) | N/A | Degrades (fewer sources) |
| Event | Analyzed records | — | No (in-memory) | N/A | Degrades (fallback) |
| Verified Link | MarketLink | SQLite | Yes | Fails (write error) | N/A (no API call) |
| Freeze | Prediction | SQLite | Yes | Fails (write error) | N/A (no API call) |
| Resolve | Outcome + Calibration | JSON + SQLite | Yes | Fails (write error) | Degrades (no resolved markets) |
| Calibration | Brier/skill/grade | JSON (via Resolve) | Yes | Fails | Degrades (dormant feedback) |
| Trust | Trust score | SQLite (via Freeze) | Yes | Fails | N/A |
| Report | Decision report | On-demand only | N/A | N/A | N/A |

---

## Final Assessment

### Can this system run unattended for 90 days and continuously accumulate resolved predictions?

**Yes, but with significant caveats.**

The loop's architecture is sound. The core invariants — one-event-one-prediction, fail-closed verification, atomic file writes, deterministic fallbacks — are well-implemented and will keep the pipeline running through most transient failures. The system will produce output every day for 90 days.

However, "continuously accumulate resolved predictions" is where the caveats emerge:

1. **Zombie accumulation (P0-1):** Partial resolution failures will create a slow drip of unscored predictions. At an estimated rate of 1-2 per week (based on current error rates), 90 days could produce 15-30 zombies — a non-trivial fraction of a still-small calibration dataset.

2. **Silent calibration degradation (P0-2):** If `_load_resolved_records()` fails even once, the feedback pipeline goes dormant without signal. All subsequent probability estimates are uncalibrated until an operator intervenes. Over 90 days, this could mean months of uncalibrated predictions being frozen and scored — the calibration data is technically accumulating, but its quality is compromised.

3. **No operator awareness (P0-3):** Without health monitoring or alerting, the operator has no way to know whether the system is healthy, degraded, or dead without manually checking logs. A 90-day unattended deployment is effectively a 90-day bet that nothing has gone wrong — and if something has gone wrong, the operator won't know until they check.

### Recommendation

**Ship with the 3 P0 fixes. Defer P1s to the next sprint. Log P2s as tech debt.**

Minimum viable changes for unattended deployment:

1. Add a reconciliation pass for zombie predictions (P0-1) — ~2 hours of work
2. Add logging to `_load_resolved_records()` failure path (P0-2) — ~5 minutes of work
3. Add a `/health` endpoint with dead-man's-switch alerting (P0-3) — ~4 hours of work

With these three changes, the system can run unattended for 90 days with confidence. Without them, it will run — but you won't know if it's running *correctly*.

---

## Appendix: Source File Index

| Stage | Primary File | Lines |
|---|---|---|
| Scheduler | `backend/app/core/scheduler.py` | 103 |
| Discover + Event | `backend/app/services/event_intelligence_service.py` | ~440 |
| Verified Link | `backend/app/memory/event_market_link_store.py` | 195 |
| Freeze Prediction | `backend/app/memory/prediction_store.py` | 476 |
| Resolve Outcome | `backend/app/services/event_resolve_service.py` | 371 |
| Calibration (scoring) | `backend/app/services/calibration_service_event.py` | 164 |
| Calibration (feedback) | `backend/app/services/calibration_feedback_service.py` | 227 |
| Trust / Diagnosis | `backend/app/services/diagnosis_service.py` | 114 |
| Decision Report | `backend/app/services/decision_report_service.py` | 102 |
| Event Store (JSON) | `backend/app/memory/event_store.py` | ~175 |
| Audit Trail (JSONL) | `backend/app/services/event_audit_service.py` | ~180 |
| LLM Analysis | `backend/app/services/ai_analysis_service.py` | ~200 |
| Probability Engine | `backend/app/services/probability_engine_service.py` | ~450 |
| Cross-Validation | `backend/app/services/cross_validation_service.py` | ~115 |
| File I/O Utilities | `backend/app/utils/file_store.py` | 138 |
| SQLite Utilities | `backend/app/utils/sqlite_db.py` | 83 |
| Configuration | `backend/app/core/config.py` | ~120 |
| Models | `backend/app/models/event.py` | ~250 |

---

# Data Model Audit — Semantic Layer Review

**Audit Scope:** Event, Prediction, Outcome, Calibration, Trust — semantic models as actually implemented (not as documented)
**Date:** 2026-06-20
**Reviewer Role:** CTO — data-model integrity gate

---

## What Is the Actual Data Model Today?

The system implements five core semantic entities across two physical stores (JSON file + SQLite), connected by a single deterministic key (`event_id` = SHA-1 prefix of the question text, 12 hex chars). The documented schema in `DATABASE_DESIGN.md` is aspirational — the real model is a hybrid of Pydantic-validated JSON records and flat SQLite rows, bound by application-level invariants rather than foreign keys.

```
┌─────────────────────────────────────────────────────────────────┐
│                     event_store.json                            │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ EventRecord (keyed by event_id)                           │  │
│  │  ├── Probability {baseline, estimated, change, direction} │  │
│  │  ├── Credibility {score, level, confidence, ...}          │  │
│  │  ├── Impact {score, level, drivers}                       │  │
│  │  ├── Risk {level, flags}                                  │  │
│  │  ├── EvidenceProfile {direction, strength, conflict, ...} │  │
│  │  ├── EventSource {type, ...}          extra="allow"       │  │
│  │  ├── IntelligenceReport {headline, why_it_matters, ...}   │  │
│  │  ├── EvidenceItem[] {kind, source, title, url, ...}       │  │
│  │  ├── Tracking? {status, priority}        ← user-owned     │  │
│  │  ├── Outcome? {status, actual_outcome, ...} ← resolve     │  │
│  │  ├── Calibration? {brier_score, grade, ...} ← resolve     │  │
│  │  ├── EventSemantics? {resolution_criteria, ...}           │  │
│  │  ├── legacy_analysis: dict                                │  │
│  │  └── [extra]: news_filter, cross_validation,              │  │
│  │         calibration_components, calibration_feedback       │  │
│  └───────────────────────────────────────────────────────────┘  │
│  Store metadata: first_seen, last_updated                       │
└─────────────────────────────────────────────────────────────────┘
         │ event_id                    │ event_id
         ▼                             ▼
┌────────────────────────┐   ┌──────────────────────────────────┐
│ event_audit.jsonl       │   │ v2_loop.db                       │
│ (append-only JSONL)     │   │  ┌────────────────────────────┐  │
│ probability snapshots   │   │  │ predictions                 │  │
│ outcome snapshots       │   │  │  event_id (UNIQUE)          │  │
│                         │   │  │  ai_probability (FROZEN)    │  │
│ compaction: max 200/evt │   │  │  market_probability         │  │
└────────────────────────┘   │  │  raw_edge, trust, adj_edge   │  │
                              │  │  decision (act/watch/skip)   │  │
                              │  │  status (open→scored/       │  │
                              │  │    observed/voided)          │  │
                              │  │  actual_outcome, brier_score │  │
                              │  └────────────────────────────┘  │
                              │  ┌────────────────────────────┐  │
                              │  │ event_market_links          │  │
                              │  │  (event_id, contract_id)    │  │
                              │  │  verified (bool) ← GATE     │  │
                              │  │  link_method, link_confidence│  │
                              │  └────────────────────────────┘  │
                              └──────────────────────────────────┘
```

---

## Stage 1: Event Semantics

### Source of Truth

The `event_store.json` file is the authoritative source. Each entry is a dict keyed by `event_id`, wrapping an `EventRecord` validated by Pydantic. Store-level metadata (`first_seen`, `last_updated`) lives outside the record.

### Mutability Model

EventRecord is **selectively mutable** — a hybrid of mutable analysis fields and immutable settlement fields:

| Field Group | Mutability | Mechanism |
|---|---|---|
| Analysis fields (probability, credibility, impact, risk, evidence, intelligence_report, evidence_items, semantics, legacy_analysis, extra fields) | **OVERWRITTEN** on every re-scan | `save_events()` replaces the entire record dict |
| `tracking` | **PRESERVED** from existing record on re-scan; user-settable via `set_tracking()` | Upsert logic explicitly copies from existing |
| `outcome` | **PRESERVED** from existing record on re-scan; set once at resolution | Upsert logic checks `"outcome" not in record` before overwriting |
| `calibration` | **PRESERVED** from existing record on re-scan; set once at resolution | Same pattern as outcome |
| `event_id` | **IMMUTABLE** — deterministic from question text | SHA-1 hash prefix, never changes |
| Store `first_seen` | **IMMUTABLE** after first write | Preserved by upsert logic |
| Store `last_updated` | **REFRESHED** on every write | Always set to current time |

### Append-Only or Overwrite?

**Overwrite** for the record body. The event store is a key-value map, not an append-only log. Each re-scan produces a fresh analysis that **replaces** the previous one. The probability trajectory is preserved separately in the append-only audit log (`event_audit.jsonl`).

### Invariants

1. **Deterministic identity**: `event_id = SHA1(question)[:12]`. Same question → same ID, always. This enables cross-source deduplication but creates a 48-bit collision domain (~16.7M events for 50% birthday collision).
2. **Preservation on re-discovery**: `outcome`, `calibration`, and `tracking` survive re-scans. Everything else is overwritten. This prevents a re-discovery from "un-resolving" a settled event.
3. **Validation gate**: Every write passes through `EventRecord.model_validate()`. Malformed records raise instead of corrupting the store.
4. **Forward compatibility**: `extra="allow"` on `EventRecord` and `EventSource` permits services to attach undocumented fields (`news_filter`, `cross_validation`, `calibration_components`, `calibration_feedback`) that survive validation.

### Downstream Assumptions

- **Resolve service** assumes `record["event_title"]` is the question text for matching. Events without a title are skipped.
- **Audit service** assumes `record` carries `probability`, `credibility`, `impact`, `source` sub-dicts (accessed with `.get()` fallback).
- **Freeze prediction** assumes `record["source"]["type"] == "prediction_market"` and `record["source"]["source_id"]` is the contract ID. Non-market events produce no prediction.
- **Calibration feedback** assumes `record` has `calibration_components` (market/llm/cross_validation probabilities). Older events without this field are excluded from feedback.

### Critical Semantic Issue: `probability.estimated` Has Two Meanings

Before calibration feedback is applied, `probability.estimated` is the raw AI estimate from the LLM. After `_apply_calibration_feedback()` (when `CALIBRATION_FEEDBACK_ENABLED`), it becomes a fused/adjusted value weighted by component Brier histories. The pre-adjustment value is preserved in `calibration_components.llm`, but the `probability.estimated` field itself silently changes meaning. Any downstream consumer reading `probability.estimated` from a persisted record cannot determine which meaning applies without also checking for the presence of `calibration_feedback`.

---

## Stage 2: Prediction Semantics

### Source of Truth

The `predictions` SQLite table in `v2_loop.db`. Each row is a committed prediction for exactly one event.

### Mutability Model

Prediction is **write-once with a single resolve-time mutation**:

| Phase | Fields Set | Mutable After? |
|---|---|---|
| **Freeze** (INSERT) | `id`, `event_id`, `contract_id`, `platform`, `base_rate_category`, `ai_probability`, `market_probability`, `raw_edge`, `trust`, `adjusted_edge`, `liquidity`, `volume`, `decision`, `liquidity_factor`, `qualified`, `segment_n`, `segment_skill`, `created_at`, `status="open"` | No — write-once |
| **Resolve** (UPDATE) | `status` → scored/observed/voided, `actual_outcome`, `brier_score`, `resolved_at` | No — terminal state |

18 of 22 columns are **immutable after freeze**. The remaining 4 (`status`, `actual_outcome`, `brier_score`, `resolved_at`) are mutated exactly once at resolve time and then become immutable.

### Append-Only or Overwrite?

**Append-only in practice.** `ON CONFLICT(event_id) DO NOTHING` guarantees the first freeze is the only freeze. Re-scans of the same event are silent no-ops. The row is updated (not re-inserted) at resolve time, but the update is a one-time terminal transition.

### Status Lifecycle

```
           freeze
             │
             ▼
          ┌──────┐
          │ open │
          └──────┘
         /   |    \
        /    |     \
       ▼     ▼      ▼
   scored  observed  voided
   (act)  (watch/   (invalid/
           skip)    conflict)
```

All terminal states are **final**. No path back to `open`. `score_prediction` and `void_prediction` filter `WHERE status='open'`, making re-resolution a safe no-op.

### Invariants

1. **One event, one prediction.** Enforced at the schema level: `UNIQUE(event_id)`. The migration code collapses any historical multi-row state (from the M3 experiment) by keeping the `open` row or the most recent.
2. **Commitment, not trajectory.** The prediction captures the system's state at decision time. Probability trajectories live in the audit log. Trust, adjusted edge, and all diagnosis inputs are frozen so a reviewer can audit WHY the decision was made without recomputing from potentially-changed inputs.
3. **Act-only calibration.** Only `decision="act"` predictions enter the headline calibration aggregate (`status="scored"`). Watch/skip predictions are `"observed"` — Brier is computed and stored, but excluded from `calibration_summary()`.
4. **Market-gated creation.** Only events with `source.type == "prediction_market"` and a non-empty `contract_id` produce a prediction row. News events, manual events, and open-web events are invisible to the prediction layer.

### Downstream Assumptions

- **`score_prediction`** assumes an `open` prediction exists. If not, it returns `None` (silent no-op). It does not raise and does not create a prediction.
- **`decide()`** assumes `trust` and `liquidity_factor` are already computed and frozen. It does not recompute them.
- **`calibration_summary`** reads only `status='scored' AND decision='act'`. This is narrower than `segment_skill` (which reads `status IN ('scored','observed') AND decision IN ('act','watch')`). The two queries disagree on what counts as "calibration data."
- **`decision_report_service`** tolerates orphan predictions (event deleted from JSON store) by building a minimal report from the prediction row alone.

### Critical Semantic Issue: Two Independent Brier Scores Per Event

A single resolved event produces **two different Brier scores** stored in **two different places**:

| Aspect | `EventRecord.calibration.brier_score` (JSON) | `Prediction.brier_score` (SQLite) |
|---|---|---|
| Estimated probability used | Latest audit-trajectory probability (or `baseline` fallback) | Frozen `ai_probability` from freeze time |
| Scope | All resolved events (no decision filter) | Only `status='scored' AND decision='act'` |
| What it measures | How good was our evolving probability tracking? | How good was our committed bet at decision time? |

These CAN diverge. If the audit trajectory updated the estimate after the prediction was frozen (which happens on every re-scan), the event-layer Brier reflects the newer estimate while the prediction-layer Brier reflects the older committed one. Neither is wrong, but they measure different things. The system has no single "authoritative" accuracy number for a given event.

---

## Stage 3: Outcome Semantics

### Source of Truth

Constructed in `resolve_with_calibration()` and persisted to `EventRecord.outcome` (JSON store) and appended to `event_audit.jsonl` as an outcome snapshot.

### Mutability Model

**Effectively immutable once written.** `resolve_event()` overwrites the outcome field, but no code path re-resolves an already-resolved event — `auto_resolve_events` skips events where `outcome is not None`. There is no "un-resolve" endpoint.

### Invariants

1. **`actual_outcome` is probability-shaped (0-100).** Not binary. A market that resolves YES at 87 cents produces `actual_outcome=87`, not `actual_outcome=100`. The calibration math clamps it defensively via `_clamp_pct()`.
2. **`status` determines calibration eligibility.** Only `status="resolved"` produces a Calibration record and a scored prediction. Non-resolved statuses (e.g., `"invalid"`) route to `void_prediction` — no Brier, no calibration.
3. **`confidence` (0-1) is dead weight.** It records resolution certainty but is **never consumed** by any downstream computation — not in Brier weighting, not in trust, not in shrinkage. A low-confidence resolution scores identically to a high-confidence one.

### Downstream Assumptions

- **Calibration math** assumes `actual_outcome` is a finite float in 0-100. Non-finite values are silently filtered.
- **Calibration feedback** reads `outcome.actual_outcome` from resolved records and computes per-component Brier histories. Records with non-finite outcomes are skipped.
- **Prediction scoring** receives the same `actual_outcome` value from `resolve_with_calibration`. The event-layer and prediction-layer Brier scores use the same outcome but different estimates.

### Critical Semantic Issue: No Retraction Path

Once an outcome is written, there is no formal mechanism to retract, dispute, or correct it. If a market resolves incorrectly (e.g., a Polymarket oracle error) and is later re-resolved by the platform, the system has no way to update the stored outcome. The event is skipped on subsequent resolve runs because `outcome is not None`. A wrongly-resolved event permanently pollutes the calibration dataset.

---

## Stage 4: Calibration Semantics

### Source of Truth

Two independent calibration computations exist, each with its own source of truth:

**Event-layer calibration** (`EventRecord.calibration`): Computed by `calibration_service_event.score_event()` at resolve time. Pure math, no I/O. The `estimated_probability` input is the **latest audit-trajectory probability** (from `analyze_trend()`), falling back to `probability.baseline` if no trajectory exists.

**Prediction-layer calibration** (`Prediction.brier_score`): Computed by `prediction_store.score_prediction()` at resolve time. Uses the **frozen `ai_probability`** from the prediction row.

Both produce Brier scores using the same `actual_outcome` but different estimated probabilities.

### Mutability Model

Both are **immutable once written.** Neither is ever recomputed or updated.

### The Calibration Feedback Loop

`calibration_feedback_service` closes the loop by reading resolved events and adjusting new events' probabilities. Two mechanisms:

1. **Component weighting**: Fuses market/llm/cross_validation signals weighted by inverse mean Brier. Requires `calibration_components` dict on resolved records — not present on events analyzed before this feature was added.
2. **Category shrinkage**: Pulls overconfident categories toward their prior. Uses `calibration.brier_score` from resolved records.

Both are **dormant by design** until `CALIBRATION_FEEDBACK_ENABLED` is on AND `min_samples` (default 8) resolved events exist.

### Invariants

1. **Only resolved events produce calibration.** Non-resolved outcomes (invalid, void) get `calibration = None`.
2. **Grade boundaries are hardcoded.** EXCELLENT (<=0.05), GOOD (<=0.10), ACCEPTABLE (<=0.15), POOR (<=0.20), RANDOM_LEVEL (>0.20). Not configurable.
3. **Event-layer calibration has no decision filter.** All resolved events contribute, regardless of whether the prediction was act/watch/skip. Prediction-layer calibration is act-only. This means the event-layer Brier aggregate includes events the system decided not to bet on.

### Downstream Assumptions

- **`calibration_feedback_service`** assumes `calibration_components` exists on resolved records. Older events without this field are silently excluded from component weighting, reducing the effective sample size.
- **`calibration_service_event.summarize()`** reads `base_rate_category` from `legacy_analysis` (the raw analysis dict). If `legacy_analysis` is missing or the key is absent, the category breakdown is incomplete.
- **Trust computation** reads `segment_skill` from the predictions table, which uses prediction-layer Brier scores. Trust is therefore calibrated against committed bets, not evolving estimates.

### Critical Semantic Issue: Dual Calibration Creates Ambiguous Truth

When someone asks "what is our Brier score?", the system has two honest but different answers. The event-layer score (all resolved events, latest estimates) will generally be better than the prediction-layer score (act-only, frozen estimates) because the latest trajectory estimate has more information than the frozen one. Neither is wrong, but presenting them without context is misleading.

---

## Stage 5: Trust Semantics

### Source of Truth

Computed by `diagnosis_service.calibration_trust()` at prediction freeze time and stored in `Prediction.trust` (SQLite). The input is `segment_skill(category)` — a query over the predictions table.

### What Trust Measures

Trust = `clamp(skill_score(mean_brier_of_segment), 0, 1)` where `skill_score = 1 - brier/0.25`.

In plain language: **trust measures how accurately the system has historically predicted in this base-rate category, relative to random guessing.** A segment that consistently beats random earns high trust; one at random or worse earns trust = 0.

### What Trust Does NOT Measure

- Not the quality of the current event's analysis
- Not confidence in the resolution itself (`Outcome.confidence` — which is unused anyway)
- Not whether the market link is correct (`MarketLink.verified`)
- Not liquidity or volume (separate multiplicative factors)
- Not the model's confidence (that's `Credibility.confidence`)

### Mutability Model

**Frozen at prediction time.** Trust is computed once and stored. It is never recomputed even if the segment's skill changes later. This is by design — the diagnosis explains WHY the decision was made at decision time.

### The Dormant Fallback

When a segment has fewer than `min_samples` (default 8) resolved predictions, trust falls back to `DIAGNOSIS_DORMANT_TRUST` (default 0.5). The `decide()` function caps dormant segments at `"watch"` regardless of edge magnitude (`qualified=False` blocks `"act"`). This prevents the system from acting on categories where it has no proven track record.

### The Segment Scope Discrepancy

`segment_skill()` (used by trust) reads `status IN ('scored','observed') AND decision IN ('act','watch')` — a broader pool that includes watch predictions. `calibration_summary()` reads `status='scored' AND decision='act'` — act-only. These are intentionally different scopes: the broader pool lets new categories bootstrap, while the narrower pool reports only on committed bets. But this means **trust is calibrated against a different dataset than the headline calibration metric**.

### The Trust Formula in Context

```
adjusted_edge = raw_edge × trust × liquidity_factor
```

Where `liquidity_factor = clamp(liquidity / DIAGNOSIS_LIQUIDITY_FLOOR, 0, 1)`. Trust is one of two multiplicative dampeners. A zero-trust segment collapses the edge to 0, which cascades to `decision = "skip"` regardless of raw edge.

---

## Semantic Inconsistencies

### SI-1: `probability.estimated` Has Dual Meaning (Event Layer)

The field silently changes meaning when calibration feedback is applied. Before feedback: raw LLM estimate. After feedback: fused/adjusted value. The pre-adjustment value is preserved in `calibration_components.llm`, but any consumer reading `probability.estimated` from a stored record cannot determine which meaning applies without also checking for the `calibration_feedback` extra field. This is a latent bug waiting to happen — a future service that reads `probability.estimated` for any purpose will get inconsistent data.

### SI-2: Two Brier Scores, No Canonical Answer

Event-layer and prediction-layer Brier scores use different estimated probabilities and different aggregation scopes. The system has no single "our accuracy" number. This creates ambiguity for any UI, report, or external consumer that asks "what is the system's calibration?"

### SI-3: `Outcome.confidence` Is Computed But Never Consumed

Every resolution records a confidence value, but no downstream service uses it. Low-confidence resolutions (e.g., fuzzy market matches, low-volume markets) score identically to high-confidence ones. This means a noisy resolution pollutes the calibration dataset just as much as a clean one.

### SI-4: `Calibration.trajectory_span_hours` Is Stored But Never Used

The field records how long the probability trajectory spanned, but no aggregation, weighting, or filtering uses it. A prediction with a 1-hour trajectory and one with a 200-hour trajectory are scored identically.

### SI-5: `MarketLink.resolution_criteria` Is Never Validated

The link records the event's own resolution criteria at link time but never compares it against the market's actual resolution criteria at resolve time. A semantic mismatch — our prediction asks "will X happen?" while the market resolves on "will X happen by date Y?" — could silently score as correct.

### SI-6: Trust and Headline Calibration Use Different Sample Pools

Trust is computed from a pool that includes `watch` predictions; headline calibration (`calibration_summary`) excludes them. A segment could have high trust (driven by good watch predictions) but the headline calibration could show poor performance (because act predictions in that segment are worse). The user sees conflicting signals without explanation.

---

## Hidden Coupling

### HC-1: `calibration_components` Presence Gates the Feedback Loop

The calibration feedback service requires `calibration_components` (market/llm/cross_validation probabilities) on resolved records to compute component weights. This field is only populated on events analyzed after the feature was added. Older resolved events are silently excluded, reducing the effective sample size and potentially biasing component weights toward newer (possibly atypical) events.

### HC-2: `legacy_analysis.base_rate_category` Links Event Store to Calibration Aggregation

`calibration_service_event.summarize()` reads `base_rate_category` from `legacy_analysis` — a raw dict embedded in the event record. If the analysis pipeline changes how it assigns categories, historical events retain their old categories, creating a mixed-category dataset that breaks clean time-series analysis.

### HC-3: `source.type` Silently Gates Prediction Creation

`freeze_prediction` returns `None` for any event where `source.type != "prediction_market"`. This is documented behavior, but the coupling is invisible at the model level — `EventRecord.source` uses `extra="allow"` and the `type` field has no enum constraint. A typo in the source type (e.g., `"Prediction_Market"`) would silently skip prediction creation.

### HC-4: Audit Log Compaction Silently Alters Calibration Input

Compaction drops older probability snapshots beyond the per-event budget (200). The `analyze_trend()` function reads the full audit history for an event. After compaction, the trajectory has fewer observations and a shorter span. The event-layer calibration's `trajectory_observations` and `trajectory_span_hours` will reflect the compacted history, not the full history. If compaction happens between two resolve runs for different events in the same batch, they will have inconsistent trajectory depths.

### HC-5: `AUTO_VERIFY_THRESHOLD = 1.0` Makes the Verified Gate Identity-Only

The default threshold requires an exact text match for auto-verification. This means the fail-closed gate is effectively an identity gate — only events whose question text exactly matches a resolved market's question text are auto-resolved. Fuzzy matches are recorded as unverified links and require human intervention. This is a safe default but creates a growing backlog of unverified links as the system accumulates events with non-identical phrasing.

---

## Violated Invariants

### VI-1: Non-Atomic Cross-Store Resolution Can Create Zombies

The resolve pipeline writes to 4 stores sequentially: event_store.json (outcome), event_audit.jsonl (outcome snapshot), predictions (score/void), event_market_links (upsert). There is no distributed transaction. If `resolve_event()` succeeds but `score_prediction()` fails, the event is marked resolved (outcome set) but the prediction remains `open`. On the next resolve run, the event is skipped (outcome is not None), and the prediction is never scored.

**Invariant violated:** "every resolved event with an open prediction should eventually be scored."

### VI-2: Re-Resolution Overwrites Without Detection

`resolve_event()` in the store does not check for a prior outcome before overwriting. While no current code path re-resolves (auto-resolve skips resolved events), a manual API call or future code change could trigger a double-resolution. The second outcome would silently replace the first, and the audit log would have two outcome snapshots for the same event with no "superseded" marker.

**Invariant at risk:** "outcome is write-once."

### VI-3: `save_events` Preservation Logic Is Fragile

The preservation check is `"outcome" not in record`, not `record.get("outcome") is None`. If `build_event_record()` were ever modified to include `"outcome": None` in its output, the preservation would NOT trigger — the existing resolved outcome would be overwritten with `None`. Currently safe because `build_event_record()` does not include outcome/calibration keys, but this is a latent fragility.

**Invariant at risk:** "re-discovery never reverts a resolved event."

### VI-4: Batch Atomicity Loses Valid Events

`save_events()` validates every record in a batch via `EventRecord.model_validate()`. If one record fails validation, the entire batch is aborted — the N-1 valid records are lost along with their LLM analysis cost. This violates the implicit invariant that "a bad event should not prevent good events from being stored."

---

## Future Migration Risks

### MR-1: JSON Event Store Cannot Scale to Relational Queries

The event store is a single JSON file loaded entirely into memory for every write. `list_all_events()` returns every entry. `list_resolved_events()` filters in Python. As the store grows (hundreds or thousands of events), every write operation becomes O(n) in file I/O and memory. A migration to SQLite (or a real database) is inevitable but will be painful because:

- The `extra="allow"` fields (`news_filter`, `cross_validation`, `calibration_components`, `calibration_feedback`) have no defined schema and would need column definitions.
- The preservation logic (selective field merging on upsert) is application-level and would need to be reimplemented as SQL UPDATE semantics.
- The `legacy_analysis` dict is an unstructured blob that resists normalization.

### MR-2: `extra="allow"` Creates Schema Drift

Services attach undocumented fields to EventRecord that survive validation. Any migration must discover and define these fields retroactively. New services may add more extra fields, increasing the gap between the declared model and the actual stored data.

### MR-3: Audit Log Has No Schema Version

The JSONL audit log has no version field. If the snapshot schema changes (new fields added, fields renamed, types changed), historical entries and new entries will have different shapes. Any consumer that reads the full log (e.g., `analyze_trend()`) must handle both old and new shapes indefinitely.

### MR-4: `base_rate_category` Is Unstructured Text

The category is a free-text string assigned by the LLM analysis. There is no enum, no taxonomy, no validation. Different LLM versions or prompts could produce different category names for the same event type, fragmenting the segment data. Trust computation groups by exact category match — a category rename creates a new empty segment with dormant trust.

### MR-5: `event_id` Collision Domain Is Fixed

12 hex chars (48 bits) is the collision domain. Expanding to 16+ chars would require migrating all existing event_ids in both stores (JSON event store, SQLite predictions, SQLite links, audit log). This is a cross-store migration with no foreign key to join on — the event_id string appears as a dict key in JSON and a text column in SQLite.

---

## Summary Verdict

The actual data model is a **commitment-oriented system** with strong immutability guarantees on predictions and outcomes, selective mutability on event analysis fields, and well-designed fail-closed gates. The dual-store architecture (JSON for flexible event intelligence, SQLite for relational prediction integrity) is pragmatic for the current scale but carries clear migration debt.

The model's greatest strength — one-event-one-prediction with frozen diagnosis — is also its greatest rigidity: there is no correction path for wrong outcomes, no re-scoring of predictions when the segment matures, and no reconciliation when cross-store writes partially fail.

**The three highest-risk semantic issues for continued development:**

1. **Dual Brier scores** (SI-2): will confuse users and make "system accuracy" an ambiguous claim.
2. **`probability.estimated` dual meaning** (SI-1): will cause a subtle bug when a future service reads this field.
3. **No outcome retraction path** (SI in Outcome): a single wrongly-resolved event permanently pollutes calibration, and the risk grows with time and market volume.

---

# Operational Resilience Review

**Audit Scope:** Recovery paths, retry behavior, idempotency, duplicate processing, data corruption risk, and backfill capability under seven failure scenarios
**Date:** 2026-06-20
**Reviewer Role:** CTO — operational resilience gate

---

## Failure Scenario Matrix

### Scenario 1: LLM API Outage (Primary DeepSeek Model)

**Detection:** `ai_analysis_service.py:78` — bare `except Exception` catches any SDK failure (timeout, auth error, rate limit after 2 retries, network error, JSON parse error). Logged at `WARNING` level.

**Recovery path:** Deterministic fallback (`probability_engine_service.py:241-278`) computes `ai_probability` from evidence signals:

```
ai_probability = market_probability + direction_sign × 22 × evidence_multiplier
```

Capped to max 22-point deviation from market. The result is labeled `narrative_type: "evidence_fallback"` — a machine-readable marker.

**What the fallback produces vs. a normal analysis:**

| Field | Normal (LLM) | Fallback |
|---|---|---|
| `ai_probability` | LLM-estimated, clamped | Evidence-derived, max 22pt move |
| `narrative_type` | LLM-chosen | Always `"evidence_fallback"` |
| `narrative_summary` | LLM-generated | Fixed Chinese fallback string |
| `title_zh` | LLM-translated | Empty `""` |
| `resolution_criteria` | LLM-extracted | Empty `""` |
| `time_horizon` | LLM-extracted | Empty `""` |
| `entities` | LLM-extracted | Empty `[]` |

**Can the fallback freeze a prediction?** **Yes.** `freeze_prediction` requires `source.type == "prediction_market"`, `contract_id`, and numeric probabilities — all present in the fallback. The prediction is frozen with the evidence-derived probability, which becomes the permanent committed value.

**Impact on calibration feedback:** The fallback probability is recorded as the `llm` component in `calibration_components`. Over time, if many fallback predictions are resolved, the "LLM" component weight will reflect fallback quality rather than LLM quality. This degrades the feedback loop's signal.

**Retry behavior:** OpenAI SDK `max_retries=2` with internal backoff. Beyond that, no application-level retry. The fallback result is final for this scan.

**Severity:** MEDIUM. The system continues producing output, but quality is materially lower. Frozen fallback predictions are irreversible due to the one-event-one-prediction model.

---

### Scenario 2: Cross-Validation LLM Outage (Qwen)

**Detection:** `cross_validation_service.py:61` — `except Exception` catches any failure. Logged at `WARNING`. Returns `None`.

**Recovery path:** `analyze_event()` at `event_intelligence_service.py:143` checks if `cross_validate()` returned `None`:
- If `None`: `cross_validation` key is not set on the record. Credibility score is not adjusted by `credibility_delta()`. `calibration_components` lacks the `cross_validation` entry.
- Analysis proceeds with market + LLM signals only.

**Impact on credibility:**

| Cross-validation result | Credibility delta |
|---|---|
| High agreement (divergence ≤ 10) | +5 |
| Medium agreement (divergence ≤ 25) | 0 |
| Low agreement (divergence > 25) | -15 |
| **Outage (None returned)** | **0 (no adjustment)** |

Events that would have been penalized (-15 for low agreement) avoid the penalty during an outage — a silent quality degradation.

**Impact on calibration feedback:** The feedback loop operates with 2 components (market, LLM) instead of 3. Component weighting still functions (requires ≥ 2 components).

**Severity:** LOW. Credibility scores are slightly less informed. No data is lost.

---

### Scenario 3: Polymarket API Outage

**Detection:** `event_intelligence_service.py:288` and `event_resolve_service.py:183` — `asyncio.gather(return_exceptions=True)` catches per-source exceptions. Logged at `WARNING`.

**Discovery impact:** Polymarket candidates are absent from the pool. Discovery degrades to Manifold + Kalshi + Open Web. Round-robin interleaving proceeds with remaining sources.

**Resolve impact:**

| Event state | Can resolve without Polymarket? |
|---|---|
| Verified link to Polymarket contract | **NO** — contract-first path fails; `continue` at line 270 prevents text-match fallthrough |
| Verified link to Manifold/Kalshi contract | Yes — contract-first path matches normally |
| No verified link | Yes — text-matching fallback can match any resolved market from any source |

**Critical subtlety:** Events already linked to a Polymarket contract are **stuck** until Polymarket returns. They will not be resolved against Manifold/Kalshi even if the same real-world event settles there. This is by design (prevents cross-platform false matches) but creates a growing backlog during extended outages.

**Severity:** MEDIUM. Resolution delayed for Polymarket-linked events. Discovery reduced but functional.

---

### Scenario 4: All Market APIs Down Simultaneously

**Detection:** Same per-source `return_exceptions=True` pattern.

**Discovery:** `_collect_candidate_events()` returns empty list. `discover_events()` returns `{"count": 0, "events": []}`. No writes to any store.

**Resolve:** `auto_resolve_events()` returns `{"status": "no_resolved_markets", "resolved_count": 0}`. No writes to any store.

**State corruption:** **None.** The system is entirely read-only during total API failure. No partial writes, no corrupt state.

**Severity:** LOW. Complete operational pause but zero data corruption. Full recovery on next run.

---

### Scenario 5: RSS / News Feed Failures

**Detection:** `rss_service.py:51` — `_fetch_one()` catches all exceptions per feed, returns `[]`. `event_collection_service.py:46` — `collect_shared_articles()` catches per-source failures in `asyncio.gather`. Logged at `WARNING`.

**Google News down:** Zero Google News articles. RSS/official/SEC/economic articles still present. Analysis quality mildly reduced.

**All feeds down:** `shared_articles` is empty. If Google News also fails, `articles` is empty.

**Impact on analysis quality when all news is down:**
- `score_news_quality()` returns `0.25` for empty text (the minimum).
- `extract_evidence_profile()` returns defaults: direction=neutral, strength=0, conflict=0, freshness=0.5, relevance=0, source_count=0.
- The LLM receives no evidence context. Its estimate heavily regresses toward market baseline (the `clamp_probability` regression penalizes low-confidence, low-evidence estimates).
- If the LLM is also down, the deterministic fallback produces `ai_probability ≈ market_probability` (evidence_multiplier ≈ 0).

**Severity:** LOW. Analysis degrades gracefully. Predictions reflect the low-evidence state (low confidence, low credibility).

---

### Scenario 6: Scheduler Restart (Process Kill + Restart)

**Detection:** The process simply stops. No detection — requires external monitoring (systemd, Docker, process manager).

**What happens to in-flight jobs:**

`stop_scheduler()` uses `shutdown(wait=False)`. On a hard kill, `stop_scheduler` is never called. Jobs stop mid-execution.

**Partially-written state per store:**

| Store | Kill during write | Result | Recovery |
|---|---|---|---|
| `event_store.json` | Atomic write (temp + rename) | Either complete or unchanged | Automatic |
| `event_audit.jsonl` | Append in progress | Partial last line possible | Audit reader skips malformed lines |
| `predictions` SQLite | Transaction in progress | Transaction rolled back on next open | Automatic (WAL journal) |
| `event_market_links` SQLite | Transaction in progress | Same as above | Automatic |
| `event_cache.json` | Atomic write | Either complete or unchanged | Automatic |

**APScheduler state:** In-memory only (`MemoryJobStore`). No persistence across restarts. On restart, `start_scheduler()` re-adds both jobs with `replace_existing=True`. Next fire times computed from current time.

**Missed-fire behavior:**
- `misfire_grace_time=300` (5 minutes): If the process restarts within 5 minutes of a missed fire, the job runs immediately.
- Beyond 5 minutes: The fire is dropped. Next run is the following day.
- `coalesce=True`: Multiple missed fires collapse to one.

**Severity:** MEDIUM. Atomic writes prevent corruption. But a missed daily fire (beyond the 5-minute grace) means a 24-hour gap with no compensating mechanism.

---

### Scenario 7: Process Crash Mid-Resolution

**Detection:** Same as Scenario 6 — process stops.

**Resolution writes 4 stores sequentially:**

```
1. event_store.resolve_event()     → event_store.json    (atomic write)
2. event_audit_service.record_outcome()  → event_audit.jsonl  (append)
3. prediction_store.score/void()   → v2_loop.db          (transaction)
4. event_market_link_store.upsert() → v2_loop.db          (transaction)
```

**Crash between stores — resulting state:**

| Crash point | event_store | audit log | predictions | links | Consequence |
|---|---|---|---|---|---|
| After 1, before 2 | Outcome set | No outcome snapshot | Still `open` | No upsert | Event resolved but prediction unscored. **ZOMBIE.** Next resolve run skips (outcome ≠ None). |
| After 2, before 3 | Outcome set | Outcome logged | Still `open` | No upsert | Same zombie. |
| After 3, before 4 | Outcome set | Outcome logged | Scored/voided | No upsert | Prediction scored correctly. Link missing — cosmetic issue for provenance. |

**The zombie problem (crash after store 1):** The event is marked resolved (`outcome` is not None). On the next auto-resolve run, `auto_resolve_events()` checks `record.get("outcome") is not None` and skips the event. The prediction remains `open` forever. No reconciliation pass detects this state.

**Severity:** HIGH. Each crash during resolution has a ~25% chance of creating a zombie (1 of 4 store boundaries). Zombies are permanent and accumulate.

---

## Idempotency Audit

### Operation: `freeze_prediction` (same event_id twice)

**Idempotent?** **YES.** INSERT uses `ON CONFLICT(event_id) DO NOTHING`. The second call is a silent no-op returning `None`. No duplicate rows.

### Operation: `resolve_with_calibration` (same event twice)

**Idempotent?** **PARTIALLY.**
- `event_store.resolve_event()` overwrites the outcome — no error, no duplicate. The second call replaces the first outcome with an identical one.
- `record_outcome()` appends a second outcome snapshot to the audit log — **duplicate entry** in the JSONL.
- `score_prediction()` filters `WHERE status='open'`. After the first resolve, status is `scored`/`observed`. The second call returns `None` (safe no-op).
- `void_prediction()` same pattern — safe no-op after first call.

**Net result:** One duplicate audit log line. No data corruption. But the duplicate outcome snapshot could confuse `analyze_trend()` if it reads outcome-kind entries (it filters them out at line 99-101, so this is safe).

### Operation: `upsert_link` (same event_id + contract_id twice)

**Idempotent?** **YES.** Uses `ON CONFLICT(event_id, contract_id) DO UPDATE SET ...`. The second call overwrites with identical values. `get_link()` returns the latest.

### Operation: `record_event` (audit append, same event twice)

**Idempotent?** **NO — appends duplicate.** The audit log is append-only. Two calls produce two probability snapshots for the same event at different timestamps. `analyze_trend()` reads both, which is correct behavior (it's a trajectory). But if the two calls happen in the same scan (a bug), the trajectory gets a spurious data point.

### Operation: `save_events` (same batch twice)

**Idempotent?** **YES with side effects.** The second call overwrites each event record with identical data. `first_seen` is preserved (it already exists). `last_updated` is refreshed to the second call's time (cosmetic). The only side effect is that `tracking`, `outcome`, and `calibration` are preserved from the existing record (which was just written by the first call), so the preservation logic is a safe no-op.

---

## Duplicate Processing Audit

### Can the same event be analyzed twice in a single scan?

**No.** Two dedup layers:

1. **Candidate dedup:** `candidate_dedup_service` normalizes question text and deduplicates before analysis.
2. **Cache check:** `process_event()` checks the per-question TTL cache (`event_cache.json`, 1-hour TTL). A cached question returns `(record, False)` and is excluded from `_persist_events`.

However, if two candidates have different raw question text but the same SHA-1 prefix (collision in the first 12 hex chars), they would be treated as the same event. This is the `event_id` collision risk documented in the data model audit.

### Can the same event be resolved twice in a single resolve run?

**No.** `auto_resolve_events()` iterates over `list_all_events()` and checks `record.get("outcome") is not None` at the top of the loop. After the first resolution, the outcome is set, and a second pass would skip it. Within a single iteration, `list_all_events()` returns a snapshot taken before any writes, so there is no re-entrancy risk.

### Can the same prediction be scored twice?

**No.** `score_prediction()` filters `WHERE status='open'`. After the first score, status transitions to `scored` or `observed`. A second call returns `None`.

---

## Data Corruption Risk Assessment

### Risk 1: Corrupt `event_store.json`

**Detection:** `file_store.read_json_strict()` raises on corrupt JSON. `read_json()` (lenient mode) quarantines and returns fallback.

**Quarantine behavior:** `_quarantine_corrupt()` copies the file to `<path>.corrupt`. If the copy fails, the corrupt file remains in place and the quarantine is retried on the next read.

**Recovery:**

| Mode | Behavior | Recovery |
|---|---|---|
| Lenient read (`read_json`) | Quarantine + return `{}` | Reads succeed (empty). The corrupt file is quarantined but may remain on disk. |
| Strict read (`read_json_strict`) | Quarantine + **raise** | **All writes fail** until an operator restores the file. |

**The write blockade problem:** `save_events()` uses `read_json_strict()` for the read-modify-write cycle. A corrupt store causes `read_json_strict()` to raise, which aborts `save_events()`, which aborts the entire `_persist_events` pipeline. All subsequent discovery runs fail to persist — analyses are computed (LLM cost incurred) but never stored. The quarantine copies the corrupt file, but does not remove it. **Manual intervention required.**

**Severity:** HIGH. A corrupt event store halts all discovery persistence with no automatic recovery.

### Risk 2: Corrupt SQLite Database (`v2_loop.db`)

**Detection:** `sqlite3.connect()` + first query raises `sqlite3.DatabaseError`.

**Quarantine behavior:** **None.** There is no quarantine, no backup, no recovery path for SQLite. The `sqlite_db.py` module does not handle `DatabaseError` specially — it propagates to the caller.

**Recovery:** The only recovery is to delete the corrupt database and let `_ensure_schema()` recreate it on next access. This **permanently loses all predictions, all market links, and all calibration history**. The event store (JSON) still has outcome and calibration data, but the prediction-layer data is gone.

**Severity:** CRITICAL. A corrupt SQLite database loses the entire calibration dataset with no recovery path.

### Risk 3: Corrupt `event_audit.jsonl`

**Detection:** `_read_all()` silently skips malformed JSON lines (`json.JSONDecodeError` → `continue`).

**Recovery:** Automatic. The audit reader is resilient to partial corruption. Malformed lines are skipped; valid lines are processed. Compaction rewrites only valid lines, effectively cleaning the file.

**Severity:** LOW. Audit log corruption is cosmetic — it affects observability but not the feedback loop.

### Risk 4: Partial Line in `event_audit.jsonl` (Process Crash During Append)

**Detection:** Same as Risk 3 — `_read_all()` skips the malformed last line.

**Recovery:** Automatic. The partial line is skipped. The next `record_event` call appends a new valid line after the partial one.

**Severity:** LOW. One lost audit entry.

### Risk 5: Disk Full During Write

**JSON atomic write:** `write_json_atomic()` writes to a temp file, then `os.replace()`. If the disk is full during the temp file write, `OSError` is raised. The temp file is cleaned up. The original file is untouched. The exception propagates to the caller.

**SQLite write:** `sqlite3.OperationalError: database disk image is malformed` or similar. The transaction is rolled back. The exception propagates.

**Recovery when disk space is freed:**
- JSON writes: Automatic. The next write attempt succeeds.
- SQLite writes: Automatic if the database was not corrupted. If `disk full` caused SQLite corruption, the database must be deleted and recreated (Risk 2).

**Severity:** MEDIUM to CRITICAL depending on whether SQLite survives the disk-full condition.

---

## Retry Behavior Summary

| Operation | Built-in Retry | Application Retry | Fallback |
|---|---|---|---|
| Primary LLM call | OpenAI SDK `max_retries=2` (429/5xx) | **None** | Deterministic fallback |
| Cross-validation LLM | OpenAI SDK `max_retries=2` | **None** | Return `None`, skip adjustment |
| Market API fetch (Polymarket/Manifold/Kalshi) | `httpx` timeout=30s | **None** | Source dropped for this scan |
| Google News | `gnews` library internal | **None** | Empty article list |
| RSS feeds | `feedparser` internal | **None** | Empty feed |
| SQLite write | **None** | **None** | Exception propagates |
| JSON file write | **None** | **None** | Exception propagates |
| Audit log append | **None** | **None** | Exception propagates (per-event isolation in `_persist_events`) |

**The system has zero application-level retry.** Every transient failure is a permanent failure for that run. The only retry mechanism is the OpenAI SDK's built-in `max_retries=2`, which handles rate limiting and server errors.

**Impact over time:** At the current scale (2 runs/day), a transient failure that lasts less than ~12 hours will be caught by the next run. A transient failure that lasts longer than 12 hours causes a missed daily cycle.

---

## Backfill Capability Assessment

### Can the system re-analyze events that were analyzed with the fallback?

**No.** The one-event-one-prediction model (`ON CONFLICT DO NOTHING`) means the fallback-derived prediction is permanent. Even if the event is re-discovered after the LLM recovers, the prediction is not re-frozen. The event record IS overwritten with the new LLM analysis (probability, credibility, etc.), but the prediction row in SQLite retains the original fallback values.

**Partial workaround:** The event record's updated `probability.estimated` (from the LLM) is used for the audit trajectory. The event-layer calibration (at resolve time) will use the latest trajectory estimate, not the frozen fallback. So the event-layer Brier score benefits from the recovery, but the prediction-layer Brier score does not.

### Can the system re-score predictions scored before calibration feedback was enabled?

**No.** `score_prediction()` is a one-time terminal transition (`open → scored`). Once scored, the prediction's `brier_score` and `actual_outcome` are immutable. There is no `re_score()` or `update_brier()` function.

**Impact:** Predictions scored before calibration feedback was enabled have Brier scores computed from the pre-feedback `ai_probability`. These scores enter the calibration dataset and influence trust for future predictions. There is no way to retroactively apply feedback-adjusted probabilities to historical predictions.

### Can the system backfill `calibration_components` on older events?

**No.** `calibration_components` is set during `analyze_event()` at discovery time. It captures the market, LLM, and cross-validation probabilities at analysis time. For events discovered before this field was added, the field is absent from the stored record. There is no backfill script or migration tool.

**Impact:** The calibration feedback service's `component_weights()` function requires `calibration_components` on resolved records to compute per-component Brier histories. Older events are silently excluded, reducing the effective sample size. As the system ages, the proportion of events WITH components grows, but the long tail of old events never contributes.

### Can the system backfill `event_market_links` for events discovered before the link store was added?

**Partially.** `auto_resolve_events()` creates `MarketLink` records during the text-matching phase. If an old event has not yet been resolved, the next auto-resolve run will attempt to match it and create a link. But if the event was already resolved (before the link store existed), it will be skipped by `auto_resolve_events()` (outcome ≠ None), and no link will be created.

**Impact:** Resolved events from before the link store era have no provenance record in `event_market_links`. This is a gap for audit and debugging but does not affect the feedback loop.

### Is there a general backfill framework?

**No.** There is no backfill script, migration tool, or replay mechanism. The project has one manual backup archive (`backup-20260612-181108.tar.gz`) and one documented backfill operation in `HANDOFF.md`, but no reusable tooling.

---

## Risk Register

| ID | Risk | Severity | Detection | Recovery | Permanent Loss |
|---|---|---|---|---|---|
| R-1 | Corrupt `event_store.json` blocks all writes | **HIGH** | `read_json_strict` raises | **Manual** — restore from `.corrupt` quarantine or backup | All events since last backup |
| R-2 | Corrupt `v2_loop.db` loses all predictions | **CRITICAL** | `sqlite3.DatabaseError` | **Manual** — delete and recreate | **All calibration history** |
| R-3 | Process crash mid-resolution creates zombies | **HIGH** | No detection mechanism | **Manual** — reconciliation query needed | Unscored predictions (calibration data) |
| R-4 | Fallback predictions are permanent | **MEDIUM** | `narrative_type == "evidence_fallback"` in record | **None** — by design | Lower-quality committed predictions |
| R-5 | Missed daily fire (>5min grace) | **MEDIUM** | Log entry | **Manual** — call API endpoint | 24h discovery/resolve delay |
| R-6 | No application-level retry | **LOW** | Implicit (failure logged, no retry) | Next scheduled run | Transient failures become permanent for that run |
| R-7 | Polymarket-linked events stuck during outage | **MEDIUM** | Log warning | Automatic (when Polymarket returns) | Resolution delay only |
| R-8 | `calibration_components` gap on old events | **LOW** | No detection | **Manual** — backfill script needed | Reduced feedback sample size |
| R-9 | Disk full during SQLite write | **CRITICAL** | `OperationalError` | Free disk space; may need DB rebuild | Possible total calibration loss |
| R-10 | Audit log partial line on crash | **LOW** | Malformed JSON line skipped | Automatic | One lost audit entry |

---

## Recovery Time by Scenario

| Scenario | Time to Detect | Time to Recover | Recovery Type | Data Gap |
|---|---|---|---|---|
| LLM outage (hours) | Immediate (log warning) | Automatic on next scan | Automatic | Fallback predictions for events first seen during outage |
| LLM outage (days) | Only if logs monitored | Automatic on next scan after recovery | Automatic | Many fallback predictions permanently frozen |
| Polymarket outage (hours) | Immediate (log warning) | Automatic when Polymarket returns | Automatic | Resolution delay for linked events |
| Polymarket outage (days) | Only if logs monitored | Automatic when Polymarket returns | Automatic | Growing backlog of unresolved events |
| All APIs down (hours) | Only if logs monitored | Automatic on next run | Automatic | No data gap — clean pause |
| Process crash (< 5 min) | External monitoring | Automatic (misfire grace catches up) | Automatic | Possible one zombie if crash was mid-resolve |
| Process crash (> 5 min, < 24h) | External monitoring | Automatic at next cron fire | Automatic | One missed run; zombies from crash |
| Process crash (> 24h) | External monitoring | Automatic at next cron fire | Automatic | Multiple missed runs; cumulative zombies |
| Disk full | `OSError` / `OperationalError` | Free disk space; manual DB check | **Semi-manual** | Possible SQLite corruption |
| Corrupt event store | `read_json_strict` raises | Restore from quarantine/backup | **Manual** | Events since last backup |
| Corrupt SQLite | `DatabaseError` | Delete and recreate | **Manual** | **All calibration history** |

---

## Final Assessment

### Could this system recover automatically after a 24-hour outage?

**Yes — with qualifications.**

The system's architecture is fundamentally resilient to time-bounded outages. Every external dependency (LLM, market APIs, news feeds) is isolated, every failure degrades gracefully, and every store has atomic write semantics. A 24-hour outage means:

1. **Both daily cron fires are missed.** The 07:15 discover and 22:30 resolve jobs do not fire. No data is written, no state is corrupted.

2. **On restart (within misfire grace):** If the process restarts within 5 minutes of a scheduled fire, the job runs immediately. If beyond 5 minutes, the fire is dropped, and the system waits for the next day's cron.

3. **On restart (next cron fire):** The next discover run fetches fresh candidates from all sources, analyzes them, and persists normally. The next resolve run checks all unresolved events against all resolved markets. Events that settled during the 24-hour outage are resolved. The loop resumes.

4. **The gap:** Events that settled during the outage but whose market contracts expired or were delisted during the 24 hours may be missed by `auto_resolve_events()` (which fetches only currently-resolved markets, not historical ones). This is a narrow edge case.

**What survives the 24-hour outage intact:**
- All existing event records (JSON store is untouched)
- All predictions (SQLite is untouched)
- All market links (SQLite is untouched)
- All audit log entries (JSONL is untouched)
- The scheduler's cron configuration (re-added on startup)

**What is lost:**
- 24 hours of discovery (events that appeared only during the outage window may be picked up on the next scan if they are still listed)
- 24 hours of resolution (events that settled during the outage are resolved on the next run — unless the market delists them)
- If the outage included a process crash mid-resolution: potential zombies (1-2 unscored predictions)

**What requires manual intervention:**
- If the outage was caused by disk full or SQLite corruption: the database must be repaired before the loop can resume.
- If zombies were created during a crash: a manual reconciliation query is needed.
- If the event store was corrupted: restore from quarantine or backup.

**The system can recover automatically from a clean 24-hour outage (power failure, network outage, planned maintenance) with zero manual intervention and zero permanent data loss.** The only cost is a 24-hour delay in discovery and resolution.

**The system cannot recover automatically from a 24-hour outage that involved data corruption (disk failure, filesystem corruption, SQLite WAL corruption).** These scenarios require manual intervention, and in the worst case (corrupt SQLite with no backup), result in permanent loss of the entire calibration dataset.

### Recommendation

The system's operational resilience is **strong for transient, non-destructive failures** and **weak for persistent, destructive failures**. The three highest-impact improvements:

1. **Add a startup reconciliation pass** that detects zombies (resolved events with open predictions) and scores them. This converts R-3 from a manual-recovery risk to an automatic one. Estimated effort: 2 hours.

2. **Add automated daily backups** of `event_store.json`, `event_audit.jsonl`, and `v2_loop.db` to a timestamped archive. This converts R-1 and R-2 from permanent-loss risks to recoverable ones. Estimated effort: 1 hour.

3. **Add a `/health` endpoint** that reports last-successful-run timestamps, zombie count, store integrity status, and calibration feedback state. This makes outage detection possible without tailing logs. Estimated effort: 3 hours.

With these three changes, the system could survive a 24-hour outage — including crash and corruption scenarios — with automatic recovery and zero permanent data loss.
