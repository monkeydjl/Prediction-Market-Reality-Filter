# Operational Resilience Review

**Date:** 2026-06-20  
**Scope:** Recovery under LLM API outages, Polymarket outages, RSS failures, scheduler restart, process crash, disk write failure, partial persistence  
**Method:** Trace every write path, every error handler, every retry (or lack thereof)

---

## 1. Failure Scenario Matrix

### 1.1 LLM API Outage

| Dimension | Finding |
|-----------|---------|
| **What happens** | Every `analyze_event()` call in `process_event()` fails. Each candidate returns `None` (logged as warning). `discover_events` returns `count=0`. The scheduler job `_job_event_discover` logs `count=0` and exits cleanly — no error raised. |
| **Detection** | `logger.warning("Event discovery failed [%s]: %s", question, exc)` — per-candidate, buried in logs. No structured metric. No count of zero-result runs. No alert. |
| **Recovery** | **None automatic.** The next scheduled run is 24 hours later at 07:15 UTC. No retry. No backoff. No re-queue. The gap is fixed. |
| **Data impact** | Zero events discovered. Zero predictions frozen. Zero audit snapshots. The calibration loop starves for one full day per outage. |
| **Severity** | **P0.** The LLM is the single hard dependency for the entire discovery pipeline. No fallback. No graceful degradation beyond "produce zero." |

### 1.2 Prediction Market API Outage (Polymarket / Manifold / Kalshi)

| Dimension | Finding |
|-----------|---------|
| **What happens** | `_collect_candidate_events` isolates failing sources via `asyncio.gather(return_exceptions=True)`. A failed source contributes zero candidates. If all 3 market APIs are down: zero market candidates. Only Open Web extraction candidates remain (if `OPEN_WEB_EXTRACTION_MODEL` is configured). For `auto_resolve_events`: same isolation pattern. If all 3 down → returns `"no_resolved_markets"`. |
| **Detection** | `logger.warning("Event source failed [%s]: %s", name, exc)` — one line per failed source. `auto_resolve_events` returns `by_source` dict showing counts per platform. No alert. |
| **Recovery** | **None automatic.** Next discover cycle is 24h later. Next auto-resolve is 24h later. No retry. No backoff. No queuing. |
| **Data impact — Discover** | Reduced candidate pool → fewer events → fewer predictions frozen. Calibration accumulation slows but doesn't stop if at least one source succeeds. |
| **Data impact — Resolve** | Zero resolutions for the cycle. Open predictions remain open. Calibration doesn't accumulate. Events that settled during the outage window may be matched on the next run (text-match is forgiving). But events matched by `contract_id` primary path still resolve if the contract appears in a later run. |
| **Severity** | **P1.** Multi-source isolation is well-designed — one down source doesn't break the run. But "all three down" = full auto-resolve loss for that cycle, and there's no retry. |

### 1.3 RSS / Official Source Failures

| Dimension | Finding |
|-----------|---------|
| **What happens** | `rss_service._fetch_one()` catches all exceptions, returns `[]`. `collect_shared_articles` isolates each source class (RSS, official, SEC, BLS) via `return_exceptions=True`. Individual feed failures are swallowed silently inside `_fetch_one`. Source-class failures log a warning. `collect_articles` wraps `fetch_google_news` in try/except → returns `[]` on failure. |
| **Detection** | Source-class failures: `logger.warning("Source collection failed [%s]: %s", label, exc)`. Individual RSS feed failures: **silently swallowed** — `_fetch_one` returns `[]` with no log. Google News failure: `logger.warning("Source collection failed [gnews]: %s", exc)`. |
| **Recovery** | **None automatic.** Feeds are re-fetched on next discover cycle. No retry. |
| **Data impact** | Fewer evidence articles → lower `credibility.source_count`, potentially lower `credibility.news_quality` and `credibility.evidence_strength`. Events still discovered, but evidence quality degraded. Not loop-stopping. |
| **Severity** | **P2.** Degraded quality, not loop failure. But silent individual feed failures mean an RSS source could be dead for weeks with no one noticing. |

---

## 2. Scheduler Restart Behavior

| Dimension | Finding |
|-----------|---------|
| **Startup** | `start_scheduler()` called in FastAPI lifespan. Scheduler starts, both jobs registered. Startup log confirms. |
| **Missed run behavior** | `misfire_grace_time=300` (5 minutes). If the process was down at 07:15 and restarts at 07:19 → job fires immediately (within grace window). If process restarts at 07:21 → **job is permanently dropped.** APScheduler logs the misfire but no structured event. |
| **Coalesce** | `coalesce=True` — if multiple runs are pending, only the latest fires. Prevents backlog stampede. |
| **Max instances** | `max_instances=1` — prevents concurrent execution of the same job. A job that runs long won't stack. |
| **Restart after crash** | No persistent "last run timestamp." No way to detect that 3 cycles were missed during a 72-hour outage. No backfill trigger. |
| **Severity** | **P1.** The 5-minute grace window is reasonable for brief restarts. But for any outage >5 minutes, the cycle is permanently lost. No detection of how many cycles were missed. |

---

## 3. Process Crash Analysis

### 3.1 Crash During `save_events` (atomic write)

```
write_json_atomic: tempfile.mkstemp → json.dump → os.replace
```

| Property | Finding |
|----------|---------|
| **Partial write risk** | **None.** `os.replace` is atomic on POSIX. On Windows, `os.replace` is atomic if on the same filesystem. Either the old file exists intact, or the new file exists complete. |
| **Temp file cleanup** | If crash occurs before `os.replace`: temp file remains on disk (`.event_store.json.*.tmp`). No automatic cleanup. Harmless but clutters the directory. |
| **Corrupt JSON recovery** | `read_json_strict` quarantines corrupt JSON to `.corrupt` and raises. `read_json` returns fallback. The old file's integrity is preserved. |
| **Write lock** | `locked_file` uses a per-file `threading.RLock`. If the process crashes while holding the lock: lock is released (OS cleans up). Next process start gets a fresh lock. |

### 3.2 Crash During `resolve_with_calibration`

```
Line 130: resolve_event(event_id, outcome, calibration)     → event_store.json write
Line 132: record_outcome(event_id, title, outcome)           → event_audit.jsonl append
Line 139: score_prediction(event_id, actual_outcome)         → v2_loop.db UPDATE
```

| Crash point | State after restart | Recovery |
|-------------|---------------------|----------|
| **Before line 130** | Event unresolved. Prediction open. | Auto-resolve will retry on next cycle. ✓ |
| **After line 130, before line 132** | Event resolved in event_store. No audit snapshot. Prediction open. | Auto-resolve skips (has outcome). Prediction orphaned. **No recovery.** ✗ |
| **After line 132, before line 139** | Event resolved in event_store. Audit log has outcome. Prediction open. | Same as above. **No recovery.** ✗ |
| **After line 139** | All three stores consistent. | Normal state. ✓ |

**This is the same P0 issue identified in the Production Readiness and Data Model audits.** The three-store write has no atomicity boundary.

### 3.3 Crash During SQLite Write

```
writing() context manager: conn.execute → conn.commit() (on success) / conn.rollback() (on exception)
```

| Property | Finding |
|----------|---------|
| **Transaction integrity** | Each `writing()` block is one transaction. Commit or rollback. No partial rows. |
| **Between-statement crash** | If Python crashes between `conn.execute()` and `conn.commit()`: transaction rolled back by SQLite on next open. No corruption. |
| **WAL durability** | WAL mode with default `synchronous=FULL` (SQLite default in WAL mode). Committed data survives crash. |
| **WAL file accumulation** | WAL file grows with writes. SQLite auto-checkpoints at 1000 pages. No explicit `PRAGMA wal_checkpoint`. After a long-running process, WAL file may be large on restart — SQLite will recover it on first open. Data safe. Slightly slower startup. |

### 3.4 Crash During `event_audit.jsonl` Append

```
record_event: open(path, "a") → write(line + "\n") → close (implicit)
```

| Property | Finding |
|----------|---------|
| **Partial line risk** | **Yes.** If crash occurs during `write()`, a partial line may be written. `json.loads` on the partial line will fail → skipped by `_read_all`. No data corruption, but the partial line stays in the file. |
| **Truncation risk** | **Yes.** If crash occurs during `_maybe_compact → rewrite_lines_atomic`, the atomic write protects the file — tempfile + os.replace. Either old file or new compacted file. OK. |
| **Compaction failure** | `_maybe_compact` catches all exceptions, logs warning. Append already succeeded → no data loss. |

---

## 4. Disk Write Failure

| Storage | Failure behavior |
|---------|-----------------|
| **event_store.json** | `write_json_atomic`: `mkstemp` may succeed, `json.dump` may raise `OSError`. Temp file cleaned up in exception handler. `os.replace` may fail → temp file orphaned. Old file intact. |
| **event_audit.jsonl** | `record_event`: `open("a")` append may fail → `OSError`. No partial write — append is either complete or never happened. `record_outcome`: same. `_maybe_compact`: tempfile + os.replace protects integrity; compaction failure logged, not raised. |
| **event_cache.json** | `read_json(path, {})` — missing file returns `{}`. Corrupt returns `{}`. Write failure raises → no harm. Cache is ephemeral. |
| **v2_loop.db (SQLite)** | Disk full during `conn.commit()` → `OperationalError`. `writing()` catches, rolls back. Data consistent. Disk full before commit → rollback. |

**Disk-full scenario:** The event store is the most critical. If it can't write, `save_events` raises → `_persist_events` aborts → `discover_events` returns what it could (the response still shows events, but none were persisted). `logger.error` fires. But the scheduler sees the job complete without exception — the error was caught in `_persist_events`.

---

## 5. Retry Behavior

| Component | Retry? | Details |
|-----------|--------|---------|
| **LLM API calls** | **No** | `httpx.AsyncClient` with 60s timeout. No retry, no backoff, no circuit breaker. Single failure → candidate lost. |
| **Market API calls** | **No** | `httpx.AsyncClient` with 30s timeout. No retry. Source isolation means other sources proceed. |
| **RSS feeds** | **No** | `feedparser.parse(url)` with no timeout. No retry. `_fetch_one` catches all exceptions, returns `[]`. |
| **SQLite writes** | **No** | `sqlite3.connect(timeout=30.0)` — 30s lock wait, not retry. Busy → `OperationalError`. |
| **File writes** | **No** | Immediate fail. No retry. |
| **Scheduler jobs** | **No** | Failed job logged. Next run is the next cron tick. |

**Zero retry anywhere in the system.** Every failure is one-shot. The only "retry" is the next scheduled cycle — 24 hours later for discover, 24 hours later for auto-resolve.

---

## 6. Idempotency

| Operation | Idempotent? | Mechanism |
|-----------|-------------|-----------|
| `save_events` | **Yes** (for event identity) | Upsert by event_id. Preserves outcome/calibration/tracking. BUT overwrites probability/credibility/evidence — if re-run with different data, the record changes. Same event_id, different record content. |
| `freeze_prediction` | **Yes** | `INSERT ... ON CONFLICT(event_id) DO NOTHING`. First write wins. Re-run is silent no-op. |
| `score_prediction` | **Yes** | `UPDATE ... WHERE status='open'`. Already-scored row → no row matched → returns None. |
| `void_prediction` | **Yes** | Same pattern. `WHERE status='open'` gate. |
| `upsert_link` | **Yes** | `INSERT ... ON CONFLICT(event_id, contract_id) DO UPDATE`. Re-run updates `link_confidence`, `linked_at`, `verified`. Content may change. |
| `record_event` | **No** | Always appends. Re-run produces duplicate audit lines. `_maybe_compact` keeps only the most recent 200 per event → duplicates survive until compaction. |
| `record_outcome` | **No** | Always appends. BUT `_compact_records` keeps at most ONE outcome snapshot per event (line 162 of event_audit_service). If re-run before compaction, duplicates exist temporarily. |
| `resolve_event` | **No** | `record["outcome"] = outcome` — OVERWRITES existing outcome. If re-run with different `actual_outcome`, the previous outcome is silently replaced. No version history. |

**Critical non-idempotent path:** `resolve_event` overwrites the outcome dict. If auto-resolve runs twice for the same event (e.g., after a partial crash between writes), the second run could overwrite the first outcome with a different value. The `score_prediction` gate (`WHERE status='open'`) prevents double-scoring, but the outcome in event_store is silently overwritten.

---

## 7. Duplicate Processing

| Scenario | Duplication risk |
|----------|-----------------|
| **Same event from two market sources** | `candidate_dedup_service.dedupe_candidates()` — token Jaccard similarity on question text. Keeps first occurrence in source order. Analyzes once. Low duplication risk. |
| **Same question text, different discover cycles** | `use_cache=False` in scheduled run → cache bypassed → full LLM re-analysis every cycle. Fresh audit snapshot each time (by design for M3 trajectory). Not duplication — this is the intended behavior. |
| **Two workers/processes** | File-level `locked_file` and `_WRITE_LOCK` protect against in-process concurrency. But no cross-process coordination. Two FastAPI processes sharing the same `event_store.json` will race on reads and writes. The atomic write (`os.replace`) prevents corruption, but one process's write can silently overwrite the other's recent changes. **The system is single-process by design.** |
| **Cache poisoning** | `event_cache.json` has 1h TTL, purged on write. Expired entries removed. No risk of serving stale data beyond TTL. |

---

## 8. Data Corruption Risk

| Risk | Likelihood | Impact |
|------|-----------|--------|
| **Partial JSONL line** | Medium (crash during append) | Low — `_read_all` skips unparseable lines. Compaction removes them. |
| **Corrupt event_store.json** | Low (atomic write) | High — `read_json_strict` raises → all writes abort. Manual recovery needed (restore from `.corrupt` or backup). |
| **Corrupt SQLite DB** | Very low (WAL + rollback) | High — manual recovery needed. No automated backup. |
| **Corrupt event_cache.json** | Irrelevant | Cache is ephemeral. Corrupt → fallback `{}` → cache miss → re-compute. |
| **Cross-store inconsistency** | Medium (three-store write in resolve_with_calibration) | Medium — orphaned predictions (P0). Inconsistent outcome between event_store and predictions (P1). |
| **Silent outcome overwrite** | Low (only on manual re-resolve) | High — previous outcome lost with no audit trail of the change. |

---

## 9. Backfill Capability

| Question | Answer |
|----------|--------|
| **Can missed discover cycles be backfilled?** | **No.** There is no mechanism to re-run `discover_events` for a specific past date. The only option is to call the API endpoint (`GET /api/events/discover`), which discovers CURRENT candidates — the past market state is gone. |
| **Can missed auto-resolve cycles be backfilled?** | **Partially.** `POST /api/events/resolve/auto` can be called at any time. It fetches currently-resolved markets and matches against all unresolved events. This WILL catch events that settled during a missed cycle, because the market API returns all resolved markets (not just recently-resolved). However, markets that resolved and were DELISTED between then and now are unrecoverable. |
| **Can a specific event's prediction be re-frozen?** | **No.** `INSERT ... ON CONFLICT DO NOTHING` means the first freeze is permanent. No "re-freeze" endpoint. No mechanism to create a second prediction snapshot. |
| **Can a mis-scored prediction be corrected?** | **Not easily.** `score_prediction` only operates on `status='open'` rows. Once scored, the row is terminal. To re-score: manually UPDATE the row to `status='open'` and re-call score_prediction. No API endpoint for this. |
| **Is there a persistent "last successful run" record?** | **No.** No timestamp persisted anywhere. The only evidence of missed cycles is gaps in the audit log timestamps — which requires manual inspection. |

---

## 10. 24-Hour Outage Recovery Assessment

### Scenario: Process crashes at 06:00 UTC. Restarts at 06:00 UTC the next day.

**What ran:**
- 07:15 discover → **MISSED** (process dead)
- 22:30 auto-resolve → **MISSED** (process dead)

**What state the system is in after restart:**

| Store | State |
|-------|-------|
| event_store.json | Intact. Last write before crash. |
| event_audit.jsonl | Intact. Last write before crash. No new snapshots from the missed day. |
| v2_loop.db | Intact. No new predictions frozen. No predictions scored. |
| Scheduler | Starts fresh. `misfire_grace_time=300` — the 07:15 run is 23+ hours late → permanently dropped. Next discover: 07:15 tomorrow. Next auto-resolve: 22:30 today. |

**What recovers automatically:**
- The 22:30 auto-resolve today will run. It will match any events whose markets settled during the outage. But events that were supposed to be DISCOVERED at 07:15 and RESOLVED at 22:30 the same day are gone — they were never frozen, so there's nothing to resolve.

**What is permanently lost:**
- One day of event discoveries, predictions, and audit snapshots.
- If markets settled in that window and were delisted: those resolutions are permanently lost.
- One day of calibration accumulation.

**What the operator must do manually:**
1. Call `GET /api/events/discover` to trigger an immediate discovery (catches up on current events, but not past ones).
2. Verify `POST /api/events/resolve/auto` returned a reasonable count.
3. Check audit log for the gap.
4. No structured recovery procedure exists.

### Answer: Could this system recover automatically after a 24-hour outage?

**No.** The system survives the outage (data intact, process restarts), but:

1. It does not **detect** that an outage occurred — no "last run" timestamp, no gap detection.
2. It does not **backfill** the missed discover cycle — the 07:15 run is permanently dropped. Current events are in a different state than they were 24 hours ago.
3. It does **recover the auto-resolve** — tonight's 22:30 run will score any events whose markets have now settled. This mitigates part of the damage.
4. It does not **alert** anyone — the operator may never know a day was lost unless they check logs.

**The gap in the calibration record is permanent.** The system will have one fewer day of predictions and snapshots than it should. This is not catastrophic — the calibration aggregate is statistical, one missing day doesn't invalidate it. But the system makes no effort to detect or report the gap.

---

## 11. Issue Summary

### P0 — Data Loss or Unrecoverable State

| # | Issue | Failure mode | Recovery |
|---|-------|-------------|----------|
| **P0-1** | Three-store write in `resolve_with_calibration` has no atomicity | Process crash between writes → event resolved but prediction not scored | **None.** Prediction permanently orphaned. |
| **P0-2** | LLM API outage → zero discovery for 24+ hours | Silent `count=0`. No retry. No backoff. No alert. | Next scheduled run (24h later). No backfill. |
| **P0-3** | No process supervision | Scheduler crash → loop stops until human restarts | Manual restart. All data intact. Accumulation gap permanent. |

### P1 — Degraded Recovery or Silent Data Loss

| # | Issue | Failure mode | Recovery |
|---|-------|-------------|----------|
| **P1-1** | `misfire_grace_time=300` drops runs after 5-minute outage | Process down >5 min → missed cycle permanently lost | None. No backfill. No gap detection. |
| **P1-2** | All three market APIs down → zero auto-resolve | `"no_resolved_markets"` returned. No retry. | Next cycle (24h). Markets still listed are catchable; delisted ones are lost. |
| **P1-3** | No retry anywhere in the system | Transient network error → candidate lost, source skipped | Next cycle only (24h later). |
| **P1-4** | No persistent "last run" timestamp | Cannot detect missed cycles or measure gap size | Manual log inspection only. |
| **P1-5** | `resolve_event` silently overwrites existing outcome | Re-resolution replaces previous outcome dict | Previous outcome lost. No audit of the change. |

### P2 — Maintenance and Observability Gaps

| # | Issue | Failure mode | Recovery |
|---|-------|-------------|----------|
| **P2-1** | Individual RSS feed failures silently swallowed | Feed dead for weeks, no one knows | Manual log inspection or RSS source health check. |
| **P2-2** | No WAL checkpoint management | WAL file grows unboundedly in long-running process | SQLite auto-checkpoint; restart recovers. Slow startup. |
| **P2-3** | Temp files from crashed atomic writes not cleaned up | `.tmp` files accumulate in store directory | Manual cleanup. Harmless. |
| **P2-4** | `record_event` not idempotent | Re-run appends duplicate audit lines | Compaction removes them eventually (at 5000-line threshold). |
| **P2-5** | No cross-process coordination | Multiple workers on same store → silent write conflicts | System is single-process by design, but not enforced. |
| **P2-6** | No recovery runbook | Operator faced with outage has no documented procedure | Trial and error via API endpoints. |

---

## 12. Minimum Changes for Acceptable Resilience

| Priority | Change | Addresses |
|----------|--------|-----------|
| **P0** | Add process supervision (systemd/PM2) with auto-restart | P0-3 |
| **P0** | Reorder `resolve_with_calibration`: score prediction BEFORE writing outcome, or wrap in a compensating transaction | P0-1 |
| **P0** | Add `/api/health` endpoint: last discover count, last auto-resolve count, open predictions, pending links | P0-2, P1-4 |
| **P1** | Add retry with exponential backoff on LLM API calls (3 retries, 1s/2s/4s) | P0-2, P1-3 |
| **P1** | Extend `misfire_grace_time` to 86400 (24 hours) or persist "last successful run" and detect gaps | P1-1 |
| **P1** | Add retry on market API calls (2 retries, 1s/2s) | P1-2, P1-3 |
| **P1** | Log individual RSS feed failures (not just source-class failures) | P2-1 |
| **P2** | Add `PRAGMA wal_checkpoint(TRUNCATE)` on scheduler startup | P2-2 |
| **P2** | Add startup temp-file cleanup for `.tmp` files in store directory | P2-3 |
| **P2** | Document recovery runbook: "System was down for N hours → here's what to do" | P2-6 |
