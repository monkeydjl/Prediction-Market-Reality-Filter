# Production Readiness Review — Reality Feedback Loop

**Date:** 2026-06-20  
**Reviewer:** CTO-level audit  
**System:** Prediction Market Reality Filter v0.3.0  
**Scope:** The 9-stage Reality Feedback Loop only — not style, naming, formatting, or minor refactors.

---

## 1. Loop Overview

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

### Persistence Architecture

| Store | File | Type | Role in Loop |
|-------|------|------|-------------|
| Event Store | `event_store.json` | JSON dict | Canonical event records (upsert, resolve) |
| Event Audit | `event_audit.jsonl` | JSONL append-only | Probability trajectory, outcome markers |
| Loop DB | `v2_loop.db` | SQLite (WAL) | `predictions` + `event_market_links` tables |
| Event Cache | `event_cache.json` | JSON dict | 1h TTL compute cache |

---

## 2. Stage-by-Stage Failure Analysis

### Stage 1 — Scheduler

**Implementation:** `backend/app/core/scheduler.py` (APScheduler, AsyncIOScheduler)

| Dimension | Assessment |
|-----------|-----------|
| **Data created** | None directly; orchestrates `_job_event_discover` and `_job_event_auto_resolve` |
| **Data persisted** | None |
| **What can fail** | (a) FastAPI process crashes → scheduler dead; (b) APScheduler internal crash → both jobs silently stop; (c) process not started at all; (d) `misfire_grace_time=300` drops runs when the process was down >5 min |
| **Recoverable?** | (a) **No** — no process supervisor (systemd/PM2/supervisor) in the repository; (b) **No** — `logger.exception()` catches but the scheduler itself may not restart; (c) **No** — no auto-start; (d) **By design** — dropped run is logged, next scheduled run proceeds normally |
| **Observable?** | Startup log confirms scheduler start; `logger.exception()` on crash; but no external healthcheck, no dead-man's-switch, no metrics endpoint |
| **Loop continues?** | Only if the process stays alive. A 5+ minute outage loses one cycle permanently. A longer outage loses all cycles until manual restart |

**Verdict:** No process supervision. No alerting. The single-threaded async scheduler shares fate with the FastAPI process.

---

### Stage 2 — Discover

**Implementation:** `backend/app/services/event_intelligence_service.py` → `discover_events()`

| Dimension | Assessment |
|-----------|-----------|
| **Data created** | EventRecord (event_id, probability, credibility, impact, evidence, intelligence_report); frozen Prediction row |
| **Data persisted** | `event_store.json` (atomic write via `save_events`); `event_audit.jsonl` (append-only outcome markers); `v2_loop.db` (prediction row via `freeze_prediction`) |
| **What can fail** | (a) LLM API key expired/invalid → ALL candidate analyses fail → `discover_events` returns 0 events; (b) Polymarket/Manifold/Kalshi APIs all down → 0 market candidates; (c) RSS/EDGAR/BLS feeds unreachable → 0 news articles → reduced evidence quality but candidates still collected; (d) `save_events` write failure (disk full, permissions) → entire batch aborted; (e) Individual candidate LLM call fails → logged as warning, other candidates proceed; (f) `freeze_prediction` fails → warning only, events still saved |
| **Recoverable?** | (a) **No** — next run also fails with same expired key, loop silently produces zero until key is rotated; (b) Partially — next run may succeed if APIs recover, but lost cycle; (c) Partially — evidence quality degrades but loop continues; (d) **No** — persistent write failure blocks the entire stage; (e) **Yes** — isolated by `asyncio.Semaphore(4)`, `return_exceptions=True` in gather; (f) **Yes** — warning logged, events persist |
| **Observable?** | `logger.info("count=%d")` shows event count; `logger.warning` for source failures; but NO healthcheck endpoint exposes "last successful discover count > 0" |
| **Loop continues?** | Conditional: depends on LLM API health + at least one market source being reachable + writable disk. No automated health verification |

**Critical insight:** `discover_events` uses `use_cache=False` in the scheduled job, meaning every run does fresh LLM analysis. This is correct for accumulating audit snapshots (M3 edge trajectory), but means the LLM API is a hard dependency — no caching fallback in the scheduled path.

---

### Stage 3 — Event

**Implementation:** `backend/app/models/event.py` + `backend/app/services/event_intelligence_service.py` → `build_event_record()`

| Dimension | Assessment |
|-----------|-----------|
| **Data created** | `EventRecord` Pydantic model: probability, credibility, impact, risk, evidence, source, value_score, intelligence_report, semantics, legacy_analysis |
| **Data persisted** | Inside `event_store.json` as `store[event_id] = {first_seen, last_updated, record}`; validated via `EventRecord.model_validate()` before write |
| **What can fail** | (a) `EventRecord.model_validate()` rejects malformed record → `save_events` raises, batch aborted; (b) `event_id` collision (SHA1 of question text) → upsert merges, old record gets new `last_updated`; (c) `event_id` not generated (empty question) → record skipped |
| **Recoverable?** | (a) **No** for current batch — raises and stops; next run with different data may succeed; (b) **Not a failure** — upsert by design, preserves `outcome`/`calibration`/`tracking`; (c) **By design** — empty question events are rejected at `_event_id()` |
| **Observable?** | Exception propagates to scheduler job which logs it; no per-record validation failure tracking |
| **Loop continues?** | A validation failure in one record blocks the ENTIRE batch. This is the correct conservative choice (no partial corrupt writes), but means one bad LLM output could silently cause `discover_events` to return 0 when it should return N-1 valid events |

**Important invariant:** `save_events` preserves `outcome`, `calibration`, and `tracking` across re-scans. A re-discovered event NEVER reverts to unresolved. This is correct and well-tested.

---

### Stage 4 — Verified Link (M0 Identity Layer)

**Implementation:** `backend/app/memory/event_market_link_store.py` + `backend/app/services/event_resolve_service.py` → `auto_resolve_events()`

| Dimension | Assessment |
|-----------|-----------|
| **Data created** | `MarketLink` row: event_id → contract_id binding with `verified` flag and `link_confidence` |
| **Data persisted** | `v2_loop.db` → `event_market_links` table; `UNIQUE(event_id, contract_id)` constraint; upsert on conflict |
| **What can fail** | (a) `AUTO_VERIFY_THRESHOLD = 1.0` (default) → only EXACT normalized-question matches auto-verify; (b) `MarketLink` Pydantic validation fails → `upsert_link` raises; (c) DB write failure; (d) Market question wording drifts between freeze and settle → text match fails but contract-id primary path handles it; (e) Event has verified link but contract hasn't settled yet → skipped (correct), but no retry mechanism |
| **Recoverable?** | (a) **NOT a bug, but a P1 design choice** — fuzzy matches go to `pending` queue, requiring human review to verify. Without human intervention, these events are NEVER scored; (b) **No** — raises and blocks; (c) **No** — raises; (d) **Yes** — the contract-id primary path bypasses text matching entirely; (e) **Yes** — next auto-resolve run will catch it when contract settles |
| **Observable?** | `auto_resolve_events` returns `pending_count`; `list_pending()` API exposes unverified links; match_log includes `result: "pending"` entries |
| **Loop continues?** | Fail-closed by design: unverified links = no scoring. This prevents wrong-outcome corruption but creates a **calibration starvation risk** — if many events fall below the auto-verify threshold, the calibration aggregate grows very slowly |

**Design strength:** The dual-path settlement (contract-id primary + text-match fallback) prevents wording drift from silently breaking resolution. This is architecturally sound.

---

### Stage 5 — Freeze Prediction (M1 Commitment)

**Implementation:** `backend/app/memory/prediction_store.py` → `freeze_prediction()`

| Dimension | Assessment |
|-----------|-----------|
| **Data created** | `Prediction` row: ai_probability, market_probability, raw_edge, trust, adjusted_edge, decision, liquidity_factor, segment diagnostics |
| **Data persisted** | `v2_loop.db` → `predictions` table; `INSERT ... ON CONFLICT(event_id) DO NOTHING` (commitment model); `UNIQUE(event_id)` |
| **What can fail** | (a) Event not market-derived (`source.type != "prediction_market"`) → returns None (by design); (b) Missing `contract_id` → returns None; (c) Missing `ai_probability` or `market_probability` → returns None; (d) DB write failure → exception propagates; (e) `INSERT ... DO NOTHING` on conflict → existing prediction kept, new one silently discarded |
| **Recoverable?** | (a-c) **By design** — these are gates, not failures; (d) **No** — write failure prevents freeze; (e) **By design (but P1 concern)** — the first-sight verdict is PERMANENT. If the initial LLM analysis was wrong (API flakiness, bad news context), that bad prediction is forever part of calibration |
| **Observable?** | `freeze_prediction` returns None on gate failures (silent from caller perspective); DB failure logged by scheduler; `ON CONFLICT DO NOTHING` is silent (no log) |
| **Loop continues?** | Yes, for new events. But existing events never get updated predictions |

**Critical design tension:** "One Event, One Prediction" correctly prevents hindsight bias in calibration. BUT it also means the first analysis — potentially the least informed — is the permanent verdict. There is no mechanism to freeze a second prediction when the edge materially changes (e.g., +15% probability shift with fresh evidence). The audit log captures edge trajectory, but `calibration_summary` scores only the frozen prediction, not the trajectory.

---

### Stage 6 — Resolve Outcome

**Implementation:** `backend/app/services/event_resolve_service.py` → `resolve_with_calibration()` + `auto_resolve_events()`

| Dimension | Assessment |
|-----------|-----------|
| **Data created** | `Outcome` dict (status, actual_outcome, confidence, source, notes, resolved_at); `Calibration` dict (brier_score, skill_score, grade, trajectory_*) |
| **Data persisted** | `event_store.json` (outcome + calibration on record via `resolve_event`); `event_audit.jsonl` (outcome snapshot); `v2_loop.db` (scored/observed/voided via `score_prediction`/`void_prediction`) |
| **What can fail** | (a) Event not in store → returns None (caller raises 404); (b) No verified link AND no text match → not resolved (pending); (c) All three market APIs (Polymarket/Manifold/Kalshi) down → `auto_resolve_events` returns `"no_resolved_markets"`; (d) `resolve_event` atomic write fails → exception; (e) `score_prediction` fails AFTER outcome written → outcome persisted but prediction NOT scored — **inconsistency**; (f) Wrong actual_outcome from market API → corrupt calibration |
| **Recoverable?** | (a) **Yes** — skip, next events proceed; (b) **Yes** — pending queue, human can verify later; (c) **No** — full cycle lost, no retry; (d) **No** — exception; (e) **Partially** — outcome is in event_store but prediction remains 'open'. On next auto-resolve, event is already-resolved and skipped, so the prediction is NEVER scored. **This is a P1 gap**; (f) **No** — wrong calibration is permanent unless manually corrected |
| **Observable?** | `resolved_count`, `pending_count`, `invalid_count`, `match_log` in auto-resolve response; `logger.warning` for individual resolution failures |
| **Loop continues?** | Individual resolution failures are isolated (`try/except` per event). But if resolution succeeds and `score_prediction` fails after, the prediction is orphaned as 'open' forever |

**Sequence risk identified:** In `resolve_with_calibration()`, lines 130-141:
1. `resolve_event()` writes outcome + calibration to event_store ✓
2. `record_outcome()` appends to audit log ✓
3. `score_prediction()` / `void_prediction()` updates prediction status ✗ (if this fails)

Steps 1-2 succeed but step 3 fails → event is marked resolved in event_store but prediction row stays `status='open'`. Re-running auto-resolve skips already-resolved events, so the orphaned prediction is never scored. This is a **P1 issue**.

---

### Stage 7 — Calibration

**Implementation:** `backend/app/services/calibration_service_event.py` + `backend/app/services/calibration_feedback_service.py`

| Dimension | Assessment |
|-----------|-----------|
| **Data created** | `Calibration` snapshot (brier_score, skill_score, grade, trajectory_*); aggregate summaries (by_source, by_category) |
| **Data persisted** | On event record in `event_store.json`; calculated on-demand from `predictions` table via `calibration_summary()` |
| **What can fail** | (a) `estimated` probability is None → falls back to record baseline (50.0); (b) `actual_outcome` is None/NaN → defensive `_clamp_pct()` to 0-100, Brier computed; (c) Calibration feedback `_load_resolved_records()` fails → returns `[]`, feedback is a no-op; (d) Non-finite Brier (NaN/inf) → filtered out in aggregate |
| **Recoverable?** | (a) **Yes** — graceful fallback; (b) **Yes** — clamped, result may be misleading but won't crash; (c) **Yes** — degrades cleanly; (d) **Yes** — skipped in aggregate |
| **Observable?** | `calibration_summary()` returns `"no_data"` grade when n=0; calibration endpoint always returns valid JSON |
| **Loop continues?** | Pure math, no external dependencies. Most robust stage in the loop |

**Note on calibration feedback:** `CALIBRATION_FEEDBACK_ENABLED` defaults to `false`. Enabling it activates component-weighted fusion and base-rate shrinkage, but each breakdown requires >= `CALIBRATION_FEEDBACK_MIN_SAMPLES` (default 8) resolved samples — dormant by design. This is correctly conservative.

---

### Stage 8 — Trust (M2 Disagreement Diagnosis)

**Implementation:** `backend/app/services/diagnosis_service.py` → `diagnose()`

| Dimension | Assessment |
|-----------|-----------|
| **Data created** | trust (0..1), adjusted_edge, decision (act/watch/skip), liquidity_factor, qualified, segment_n, segment_skill |
| **Data persisted** | Frozen in `predictions` table row at freeze time; recomputed by `segment_skill()` from current predictions table |
| **What can fail** | (a) All categories dormant (no scored predictions yet) → `trust = DIAGNOSIS_DORMANT_TRUST` (0.5), decision capped at "watch"; (b) `segment_skill()` returns skill=None → trust falls back to dormant; (c) `DIAGNOSIS_LIQUIDITY_FLOOR` too high → `liquidity_factor` penalizes all markets, adjusted_edge is low, fewer "act" decisions |
| **Recoverable?** | (a) **By design** — cold-start protection, but calibration accumulation is slow; (b) **By design** — `calibration_trust()` correctly handles None skill; (c) **Configurable** — operator can adjust floor |
| **Observable?** | `qualified` boolean in prediction row; `segment_n` shows sample count; `decision` shows verdict |
| **Loop continues?** | The loop continues but operates in degraded mode (all "watch", no "act") until sufficient samples accumulate per category |

**Cold-start math:** With `EVENT_DISCOVER_LIMIT=10` per day, and only market-derived events getting predictions (typically ~7-8 per run), and auto-resolve matching a fraction of those, reaching 8 scored predictions in ONE category could take weeks. With ~20 base-rate categories, most will stay dormant for months. **This is a P1 calibration starvation concern.**

---

### Stage 9 — Decision Report (M5)

**Implementation:** `backend/app/services/decision_report_service.py` → `build_decision_report()`

| Dimension | Assessment |
|-----------|-----------|
| **Data created** | Report dict: event, probability, market_view, edge, diagnosis, confidence, recommendation, risk, category, status |
| **Data persisted** | NOT persisted — assembled on-demand from `prediction` row + `event_store` record |
| **What can fail** | (a) Event record not found → minimal report from prediction alone; (b) Prediction row not found → API returns error; (c) Stale data (prediction frozen weeks ago, market moved significantly) — report shows original frozen values |
| **Recoverable?** | (a) **Yes** — graceful degradation; (b) **No** — API error; (c) **Design limitation** — the report shows the committed prediction, not current state. M3 freshness tracking (`EDGE_STALE_HOURS=72`) exists but is NOT surfaced in the decision report |
| **Observable?** | Always returns valid JSON (never raises); stale edges identifiable via `/api/events/edges/fresh` but not in the decision report itself |
| **Loop continues?** | Pure assembly, no loop dependency. Terminal stage |

---

## 3. Issue Classification

### P0 — Loop-Stopping Issues

These issues can halt the Reality Feedback Loop entirely, causing 0 resolved predictions to accumulate.

| # | Issue | Stage | Mechanism | Impact |
|---|-------|-------|-----------|--------|
| **P0-1** | **No process supervision** | Scheduler | FastAPI process crash → scheduler dead, no auto-restart. No systemd/PM2/supervisor config | Loop stops until human restarts. Data accumulation ceases. |
| **P0-2** | **No API key health monitoring** | Discover | Expired/invalid LLM API key → all candidate analyses fail → `discover_events` returns 0 events. `logger.warning` fires but no alert, no healthcheck | Silent zero-output for days/weeks. Loop starves. |
| **P0-3** | **No external healthcheck or dead-man's-switch** | All stages | No endpoint to verify: (a) scheduler is running, (b) last discover produced >0 events, (c) last auto-resolve scored >0 predictions | Operator unaware of silent failure. 90-day unattended run requires active monitoring |
| **P0-4** | **No automated backup** | All stores | `event_store.json`, `event_audit.jsonl`, `v2_loop.db` have no scheduled backup. Manual `backup-*.tar.gz` exists but is not automated | Data loss on disk failure/corruption means ALL accumulated calibration is lost |

### P1 — Calibration Quality Degradation

These issues don't stop the loop but degrade the quality of accumulated calibration data.

| # | Issue | Stage | Mechanism | Impact |
|---|-------|-------|-----------|--------|
| **P1-1** | **score_prediction failure AFTER resolve_event success creates orphaned predictions** | Resolve Outcome | `resolve_with_calibration()` writes outcome (line 130) then scores prediction (line 139). If score_prediction fails, prediction stays 'open' but event is resolved. Auto-resolve skips resolved events → prediction NEVER scored | Calibration samples lost. Since re-scan skips resolved events, orphaned predictions are permanent calibration gaps |
| **P1-2** | **AUTO_VERIFY_THRESHOLD=1.0 means only exact text matches auto-verify** | Verified Link | Fuzzy matches go to `pending` (human review queue). Without human intervention, these events are NEVER scored. | Dramatically reduces auto-resolution rate. With market question text evolving, many legitimate matches fail exact comparison |
| **P1-3** | **Cold-start deadlock: categories stay dormant for months** | Trust | `DIAGNOSIS_DORMANT_TRUST=0.5` + `DECISION_ACT_EDGE=10.0` + `CALIBRATION_FEEDBACK_MIN_SAMPLES=8` + ~20 categories + ~7-8 predictions/day → most categories never reach "act" | System operates in perpetual "watch" mode. The "Trust" stage is effectively a fixed 0.5 multiplier for months |
| **P1-4** | **One Event, One Prediction: first-sight verdict is permanent** | Freeze Prediction | `INSERT ... ON CONFLICT DO NOTHING` freezes the first prediction forever. If initial LLM analysis was poor quality (rate-limited API, bad news context), that bad prediction is calibration input forever | Calibration signal permanently contaminated by first-sight errors. No mechanism to update prediction when edge materially changes |
| **P1-5** | **All three market APIs as single point of failure for auto-resolve** | Resolve Outcome | If Polymarket, Manifold, AND Kalshi APIs are all unreachable, `auto_resolve_events` returns `"no_resolved_markets"` | Resolution cycle lost. No cached/queued resolution data |
| **P1-6** | **Validation failure in one record aborts entire discover batch** | Event | `save_events` validates ALL records via `EventRecord.model_validate()` before writing. One bad record → entire batch aborted | N-1 valid events lost per bad record. LLM output quality variance could cause frequent batch losses |

### P2 — Future Maintenance Cost

These issues create technical debt that will increase maintenance burden over time.

| # | Issue | Stage | Mechanism | Impact |
|---|-------|-------|-----------|--------|
| **P2-1** | **event_audit.jsonl unbounded growth between compactions** | Discover | Compaction triggers at 5000 lines, keeping 200/event. Between compactions, file grows linearly with `EVENT_DISCOVER_LIMIT × snapshots_per_event` | With 10 events/day × 1 snapshot each, 500 days to trigger compaction. If snapshots increase, compaction triggers more often but file can grow large |
| **P2-2** | **event_store.json grows without bound** | Event | No TTL, no archival, no event count limit. Every discovered event is stored forever | JSON file grows linearly. At 10 events/day × ~5KB/event, ~1.8MB/year. Manageable now but no pruning strategy |
| **P2-3** | **SQLite WAL file accumulation** | All DB writes | WAL mode enabled. WAL file grows with write volume. No periodic checkpoint/truncation | Long-running process may accumulate large WAL files. SQLite auto-checkpoints at 1000 pages but no explicit WAL management |
| **P2-4** | **No database migration version tracking** | Schema | `_migrate()` uses structural detection (PRAGMA table_info, PRAGMA index_list) rather than a version table | Works today but fragile. Adding columns is fine; removing or altering columns cannot be detected structurally |
| **P2-5** | **calibration_summary reads entire scored predictions set per call** | Calibration | Three sequential queries on the full `predictions` table | O(n) in scored predictions. Fine for <10K rows; may slow API response at scale |
| **P2-6** | **No automated test for the full unattended loop** | Testing | 509 unit tests, 11 live integration tests — but no 24h/7d/30d endurance test | Cannot verify that the loop actually survives extended unattended operation without accumulating state corruption |

---

## 4. 90-Day Unattended Operation Assessment

### Can this system run unattended for 90 days and continuously accumulate resolved predictions?

**Answer: NOT in its current state. It has the architectural skeleton but lacks the operational musculature.**

### What would fail first (ordered by likelihood):

1. **Day 1-7:** If the process is started manually and stays alive, the loop runs. Discoveries and resolutions accumulate normally.

2. **Day 7-30:** The most likely silent failure mode: API key expiration or rate limiting. `discover_events` starts returning 0 events. Auto-resolve continues scoring existing open predictions but no new ones enter the pipeline. The `event_discover` job "succeeds" with `count=0`. No alert fires.

3. **Day 30-60:** If the process crashes (memory leak, unhandled exception cascade, OS restart), the scheduler is dead. No restart mechanism. All data is safe on disk, but accumulation stops.

4. **Day 60-90:** Even if the process stays alive and APIs work, the cold-start problem means most categories remain dormant. `calibration_summary` shows `"no_data"` for the first several weeks. Eventually, 1-2 high-frequency categories (e.g., "geopolitics") reach the 8-sample threshold and exit dormancy. But most of the ~20 categories stay dormant.

### What works well for 90-day unattended operation:

- **Data durability:** Atomic writes, WAL mode, corruption isolation, re-scan protection. The system will not corrupt its own data.
- **Fail-closed design:** Wrong outcomes don't enter calibration. Invalid events are voided, not scored. Identity conflicts are caught.
- **Source isolation:** A single failing API doesn't crash the whole run.
- **Conservative defaults:** Dormant trust (0.5), high auto-verify threshold (1.0), disabled calibration feedback. These prevent overconfident decisions on insufficient data.
- **Audit trail:** `event_audit.jsonl` provides a complete, append-only record of every probability estimate.

### Minimum changes required for 90-day unattended confidence:

1. **Process supervision** — systemd unit file or PM2 config (P0)
2. **Healthcheck endpoint** — `/api/health` that reports: scheduler status, last discover count, last auto-resolve count, open prediction count, pending link count (P0)
3. **API key validity check** — at startup and periodically, fail fast if key is invalid (P0)
4. **Automated daily backup** — `v2_loop.db` + `event_store.json` + `event_audit.jsonl` to a timestamped archive (P0)
5. **Fix score_prediction ordering** — write outcome and score prediction in a transaction or reverse the order so prediction scoring happens first (P1)
6. **Lower AUTO_VERIFY_THRESHOLD** — to 0.85 or 0.90 to capture fuzzy-but-correct matches, or implement a periodic human-review workflow (P1)
7. **Reduce cold-start friction** — consider lowering `CALIBRATION_FEEDBACK_MIN_SAMPLES` to 5 for initial bootstrap, or implement a "bootstrap mode" (P1)
8. **Add edge trajectory scoring** — allow calibration to consider the trajectory maximum edge, not just the first-sight prediction (P1)

---

## 5. Architecture Assessment Summary

### What the system gets right

| Principle | Evidence |
|-----------|----------|
| **Fail-closed identity** | `AUTO_VERIFY_THRESHOLD`, `get_verified_link()` gate, `verified` flag — wrong outcomes are blocked, not scored |
| **Commitment model** | `INSERT ... ON CONFLICT DO NOTHING` — prevents hindsight bias in calibration |
| **Re-scan safety** | `outcome`/`calibration`/`tracking` preserved across re-discoveries |
| **Source isolation** | `asyncio.gather(return_exceptions=True)` — one failing source doesn't crash the run |
| **Defensive math** | `_clamp_pct()`, non-finite Brier filtering, `_EPS` for division-by-zero |
| **Pure function separation** | `diagnosis_service`, `calibration_service_event`, `decision_report_service` are pure, testable, no I/O |
| **Dormant-by-design feedback** | Calibration feedback is a no-op until sufficient samples exist |
| **WAL-mode SQLite** | Safe concurrent reads/writes with short-lived connections |

### What needs attention

| Gap | Severity | Fix Complexity |
|-----|----------|----------------|
| No process supervision | P0 | Low (systemd/PM2 config) |
| No healthcheck / alerting | P0 | Medium (new endpoint + metrics) |
| No API key health monitoring | P0 | Low (startup check + periodic probe) |
| No automated backup | P0 | Low (cron + tar/rsync) |
| Orphaned predictions on score failure | P1 | Low (reorder operations or add retry) |
| Cold-start calibration starvation | P1 | Medium (config tuning or bootstrap mode) |
| First-sight prediction permanence | P1 | Medium-High (multi-snapshot scoring or edge trajectory scoring) |
| Exact-match-only auto-verify | P1 | Low (threshold tuning) |

---

## 6. Final Verdict

The **Reality Feedback Loop architecture is sound**. The 9-stage pipeline is well-conceived, correctly implements fail-closed semantics, and has comprehensive test coverage (509 unit tests). The core design decisions — commitment model, identity gating, dormant-by-design feedback, defensive math — are the right choices.

However, the system is **not production-ready for unattended operation**. The gaps are in operations, not architecture: no process supervision, no health monitoring, no alerting, no automated backup. These are not difficult to fix but are absolute requirements for a system expected to run 90 days without human intervention.

**The answer to the final question:** No, this system cannot reliably run unattended for 90 days in its current state. With the 4 P0 fixes applied, it could run unattended but would operate in a degraded mode (most categories dormant, mostly "watch" decisions) for the first 30-60 days until sufficient calibration data accumulates.

**Recommended deployment path:**
1. Apply all 4 P0 fixes before any production deployment
2. Deploy with active monitoring for the first 30 days
3. Tune `AUTO_VERIFY_THRESHOLD` and `CALIBRATION_FEEDBACK_MIN_SAMPLES` based on observed match rates
4. After 60 days of stable operation with >100 resolved predictions across >5 categories, the system can be considered for reduced-monitoring operation
5. Full unattended operation requires the P1 fixes, particularly the score_prediction ordering fix and cold-start mitigation
