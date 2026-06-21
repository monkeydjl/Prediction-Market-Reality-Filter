# Reality Feedback Loop — Production-Readiness Audit

**Scope:** `Scheduler → Discover → Event → Verified Link → Freeze Prediction → Resolve Outcome → Calibration → Trust → Decision Report`
**Out of scope:** style, naming, formatting, minor refactors.
**Reviewer perspective:** CTO production-readiness review.
**Date:** 2026-06-20
**Verdict TL;DR:** The loop is structurally sound and graceful under cold-start and partial failure, but it is **not yet safe to run unattended for 90 days**. There are two P0 issues (multi-process scheduler + JSON/SQLite split-write non-atomicity) and one P1 silent-degradation path (Polymarket price fallback to 0.5) that will quietly corrupt calibration quality over a long unattended run. See §"Can it run 90 days?" at the end.

---

## How to read this report

For each stage:
1. **Created** — data produced in memory by the stage.
2. **Persisted** — what is written durably, where, in what format.
3. **Failure modes** — what can go wrong.
4. **Recoverable?** — does a fix-it-later path exist, or is the data lost/orphaned?
5. **Observable?** — is the failure surfaced (log/metric/route) or swallowed silently?
6. **Loop continues?** — does the rest of the pipeline keep running after this failure?

Severity tags at the end:
- **P0** — stops or silently corrupts the loop.
- **P1** — degrades calibration quality.
- **P2** — creates future maintenance cost.

---

## Stage 1 — Scheduler

`backend/app/core/scheduler.py`

| | |
|---|---|
| **Created** | Two APScheduler cron jobs: `event_discover@07:15 UTC`, `event_auto_resolve@22:30 UTC`. |
| **Persisted** | Nothing by the scheduler itself (APScheduler is in-memory, default jobstore). |
| **Can fail** | Job raises → caught by `try/except` + `logger.exception` (`scheduler.py:44-45, 70-71`). Misfire (app down at fire time) → dropped after `misfire_grace_time=300`. |
| **Recoverable** | Yes — `coalesce=True`, `max_instances=1`, `replace_existing=True` mean missed runs collapse to one; no backlog stampede. Next day's cron resumes. |
| **Observable** | Yes for job-level exceptions (full traceback logged). **No for misfires** — APScheduler logs them at WARNING only if a logger is configured; the default `misfire_grace_time` drop is near-silent. |
| **Loop continues** | Yes — each job is isolated; one failure never stops the scheduler. |

**Critical issue:** `start_scheduler()` is called from the FastAPI `lifespan` (`main.py:21`). With `uvicorn.run(reload=True)` (`run.py:8`) — the shipped launcher — **the reloader parent process AND the worker process both import `app.main` and both run the lifespan**, so **two schedulers fire at 07:15 and 22:30** in development. In production, every additional Uvicorn worker does the same. `max_instances=1` is per-scheduler-instance, not global. → **P0 (P1 if deployed single-worker).** See F-1.

---

## Stage 2 — Discover

`backend/app/services/event_intelligence_service.py` → `discover_events()`

| | |
|---|---|
| **Created** | In-memory candidate events (Polymarket + Manifold + Kalshi + opt-in open-web extractor), each with filtered news, evidence items, AI probability, cross-validation, optional calibration feedback, `value_score`. |
| **Persisted** | None directly. Delegates to Stage 3 (`save_events`), audit log, and Stage 5 (`freeze_prediction`) via `_persist_events`. |
| **Can fail** | (a) Any market source 404/rate-limit → `asyncio.gather(..., return_exceptions=True)` isolates it, drops that source for this scan. (b) LLM outage → `analyze_market` falls back to a deterministic estimate. (c) Single-event bug → caught and swallowed in `process_event` (`event_intelligence_service.py:368-374`). |
| **Recoverable** | Mostly yes. Per-source and per-event failures self-heal on next day's run. **No retry/backoff** anywhere — a transient blip costs 24h. |
| **Observable** | Source failures: logged. Per-event failures: **only a `logger.warning`**, no counter, no DLQ. **LLM fallback is silent quality degradation** — output looks plausible, quality collapses, the only signal is the narrative `type=API_ERROR`. |
| **Loop continues** | Yes. No path in `discover_events` re-raises. |

**Issue:** `process_event` swallows *all* exceptions including programming bugs (`KeyError`, `AttributeError`) into the same "candidate vanished" path as a network blip. → **P2 (observability).** See F-7.

---

## Stage 3 — Event (persistence)

`backend/app/memory/event_store.py` (JSON), `backend/app/memory/event_cache.py`, `backend/app/services/event_audit_service.py` (JSONL)

| | |
|---|---|
| **Created** | `EventRecord` Pydantic objects (events, probabilities, calibration snapshots, outcomes). |
| **Persisted** | `event_store.json` (single JSON object keyed by `event_id`, atomic `write_json_atomic` = tempfile + `os.replace`); `event_audit.jsonl` (append-only, bounded by compaction at 5000 lines / 200 per event); `event_cache.json` (1h TTL, compute cache, non-durable). |
| **Can fail** | Disk full / IO error / corrupt JSON on read. Strict-read (`read_json_strict`) raises instead of overwriting with `{}`; corrupt files are quarantined to `<path>.corrupt`. |
| **Recoverable** | Yes for corruption (quarantine + recompute on next discover). **Atomicity is single-file only** — there is no transaction across `save_events` + audit + freeze (see `_persist_events` docstring, `event_intelligence_service.py:395-407`). |
| **Observable** | `save_events` failure is logged and **aborts the batch** ("Event store write failed, skipping audit/freeze"). Audit/freeze per-event failures are logged but do **not** abort. |
| **Loop continues** | Yes — `save_events` is the gate; if it fails, audit/freeze are skipped cleanly. |

**Issue:** `locked_file` is a `threading.RLock`, **not cross-process** (`file_store.py:13-31`). The atomic `os.replace` prevents a torn file but **not a lost update** when two processes (reloader + worker, or N workers) read-modify-write the same JSON. → **P0 under multi-worker.** See F-1.

---

## Stage 4 — Verified Link

`backend/app/memory/event_market_link_store.py`, `backend/app/services/event_resolve_service.py:240-319`

| | |
|---|---|
| **Created** | `event_market_links` rows (event_id ↔ contract_id, `verified` 0/1, `link_confidence`, method). |
| **Persisted** | SQLite (`v2_loop.db`), `UNIQUE(event_id, contract_id)`, `upsert_link` with `ON CONFLICT DO UPDATE`. |
| **Can fail** | Fuzzy match below `AUTO_VERIFY_THRESHOLD` → row written with `verified=0`, **fail-closed, never scored** (`event_resolve_service.py:307-319`). |
| **Recoverable** | **Yes, but only via human action.** Promotion happens only via `set_verified()` → `POST /events/{event_id}/link/verify` (`events.py:227-245`). Auto-resolve never promotes a pending link. |
| **Observable** | Yes — `GET /events/links/pending` exposes the queue. Job result includes `pending_count`. |
| **Loop continues** | Yes — pending links are isolated; verified links settle by contract id on next run. |

**Critical issue:** `AUTO_VERIFY_THRESHOLD` defaults to **`1.0`** (`config.py:168-170`). The fuzzy matcher (`text_match.py`) returns a Jaccard score strictly `< 1.0` for any non-exact match. **Therefore, with the shipped default, fuzzy matches can NEVER auto-verify** — every fuzzy question match is permanently pending until a human hits the verify endpoint. Over 90 days unattended, this means: any event whose market question wording drifted even slightly from discovery will **never resolve**, even after its market settles. This is the single biggest threat to accumulating resolved predictions at volume. → **P0.** See F-2.

**Secondary:** the verify endpoint exists (good), but there is no operator-facing backlog-drain job, no SLA alert on pending-count growth.

---

## Stage 5 — Freeze Prediction

`backend/app/memory/prediction_store.py:169-255`

| | |
|---|---|
| **Created** | One `predictions` row per market-derived event: `ai_probability`, `market_probability`, `raw_edge`, and the M2 diagnosis snapshot (`trust`, `adjusted_edge`, `liquidity_factor`, `qualified`, `segment_n`, `segment_skill`, `decision`). |
| **Persisted** | SQLite `predictions` table, `event_id TEXT NOT NULL UNIQUE`. |
| **Can fail** | Non-market events (no `contract_id` or missing probabilities) → `freeze_prediction` returns `None` (intended — "no market, no edge"). SQLite write failure → raised, caught per-event in `_persist_events`. |
| **Recoverable** | **Yes.** The INSERT is `ON CONFLICT(event_id) DO NOTHING` — a re-scan is a pure no-op, preserving the first-sight commitment. `score_prediction` only acts on `status='open'`, so re-resolve is idempotent. A crash between `save_events` and `freeze_prediction` self-heals on next discover (scheduler forces `use_cache=False`). |
| **Observable** | Per-event freeze failures logged with event_id and reason. |
| **Loop continues** | Yes. |

**This is the best-designed stage.** Write-once freeze + DB-level `UNIQUE(event_id)` + idempotent scoring + a `_migrate` that restores the one-row-per-event invariant (`prediction_store.py:77-136`). The immutability story is real, not aspirational.

**Issue (latent):** `freeze_prediction` calls `diagnose(raw_edge, segment_skill(category), liquidity)` (`prediction_store.py:207`). `segment_skill` opens SQLite synchronously. Under cross-process contention this can raise `OperationalError` (timeout 30s) → silently no-ops the freeze → event saved to JSON but **never gets a prediction row** → invisible forever. → **P1** in multi-worker; see F-1/F-4.

---

## Stage 6 — Resolve Outcome

`backend/app/services/event_resolve_service.py` → `auto_resolve_events()`, `resolve_with_calibration()`

| | |
|---|---|
| **Created** | Outcome (`actual_outcome` 0-100), per-event `calibration` snapshot (Brier, skill, grade), `match_log`. |
| **Persisted** | **Three separate stores, no encompassing transaction:** (1) `resolve_event` → `event_store.json` (sets `outcome` + `calibration`, write-once-protected against re-discovery blanking); (2) `record_outcome` → `event_audit.jsonl`; (3) `score_prediction` / `void_prediction` → SQLite `predictions`. |
| **Can fail** | Per-event resolve is wrapped in `try/except` (`event_resolve_service.py:253-258, 330-334`) — one event's failure logs + continues. Source fetch failures isolated by `asyncio.gather(return_exceptions=True)`. |
| **Recoverable** | **Partially — and this is the most important recovery gap.** Three failure modes: (a) Market un-resolves / flips outcome → **unhandled**. `auto_resolve_events` skips events with `outcome is not None` (`:230`), and `score_prediction` is one-shot — a flipped market leaves stale Brier frozen in calibration forever, silently. (b) Crash between JSON-write and SQLite-write → event marked resolved in JSON but prediction stays `open` → **orphaned, never reconciled**, no re-score job. (c) `actual_outcome=None` from a source adapter → `brier_score` raises `TypeError`, caught per-event, event never resolved, never flagged. |
| **Observable** | Per-event failures logged. **The orphaned-prediction and flipped-market cases are invisible** — no metric, no reconciliation job, no report flag. |
| **Loop continues** | Yes — per-event isolation is solid. |

**Issue A (no transaction):** `resolve_with_calibration` writes JSON → audit → SQLite under three separate locks. The ordering matters: JSON is committed first, so any later failure orphans the prediction. No reconciliation exists. → **P1.** See F-4.

**Issue B (flip unhandled):** Polymarket and Manifold both occasionally un-resolve. A flip silently corrupts the calibration aggregate. There is no `unresolve`/`reopen`/re-score path. → **P1 (calibration integrity).** See F-3.

**Issue C (silent price fallback):** `parse_outcome_prices` returns `0.5, 0.5` on any parse failure (`polymarket_service.py:107-108`). A transient price-fetch error freezes a **synthetic 50% market probability** into `raw_edge`/`adjusted_edge` as if it were a real market view. The frozen prediction then scores against a fake baseline, polluting both edge and calibration. No log. → **P1.** See F-5.

---

## Stage 7 — Calibration

**Two parallel calibration systems exist, reading from different stores:**

| System | Reads | Used by | Live by default? |
|---|---|---|---|
| `calibration_feedback_service` (prob fusion + base-rate shrinkage) | **`event_store.json`** via `list_resolved_events()` | `analyze_event` at discover | **No** (`CALIBRATION_FEEDBACK_ENABLED=False`) |
| `calibration_service_event` + `prediction_store.calibration_summary` (Brier/skill/grade, per-category) | **`v2_loop.db`** | `/events/predictions/calibration`, trust at freeze | Yes |

| | |
|---|---|
| **Created** | Per-event Brier score, skill score, grade; per-category and per-source aggregates. |
| **Persisted** | Per-event `calibration` snapshot frozen into `event_store.json` at resolve time. Aggregates are **pure functions, recomputed on every call** — not persisted. |
| **Can fail** | Cold start (zero resolved samples) → graceful `no_data` everywhere; dormant defaults. `_load_resolved_records` swallows ALL exceptions to `[]` (`calibration_feedback_service.py:225-226`) — a corrupt JSON store silently disables the feedback loop. |
| **Recoverable** | Per-event Brier is deterministic given its frozen inputs. **But the inputs (`estimated` from the audit trajectory) are time-sensitive** — the snapshot is computed once and never recomputed. Re-resolving later (after more audit snapshots) would give a different score. There is no "recompute all calibration from scratch" path. |
| **Observable** | Yes via `/events/calibration` (JSON) and `/events/predictions/calibration` (SQLite) — **which can report materially different numbers for the same deployment.** |
| **Loop continues** | Yes — calibration never blocks a job. |

**Issue (the structural finding):** The JSON-side and SQLite-side calibration populations are **different by design**. `calibration_feedback_service.category_briers` counts every resolved event regardless of decision; `prediction_store.segment_skill` counts only `decision IN ('act','watch')` rows. A category can look skilled on one side and dormant on the other. The two endpoints will disagree. This is latent today (feedback service is off), but the moment `CALIBRATION_FEEDBACK_ENABLED` is flipped, the two feedback paths will silently disagree about the same category's skill. → **P1.** See F-6.

**Issue (silent disable):** `_load_resolved_records: except Exception: return []` makes a corrupt JSON store look identical to a healthy cold start. Over 90 days, a slow corruption could disable probability fusion with zero signal. → **P1 (observability).**

---

## Stage 8 — Trust

`backend/app/services/diagnosis_service.py`

| | |
|---|---|
| **Created** | `trust` (0..1 multiplier on raw edge), `adjusted_edge = raw_edge * trust * liquidity_factor`, `decision` (act/watch/skip). |
| **Persisted** | Frozen verbatim into the `predictions` row at freeze time (`prediction_store.py:204-227`). Never recomputed. |
| **Can fail** | Dormant category (`segment_n < CALIBRATION_FEEDBACK_MIN_SAMPLES`, default 8) → `trust = DIAGNOSIS_DORMANT_TRUST` (default 0.5), decision capped at `watch` (`decide` requires `qualified=True` for `act`). All None/empty inputs explicitly guarded. |
| **Recoverable** | Yes — trust is recomputed from `segment_skill` at each freeze, so once a category accumulates ≥8 SQLite rows it graduates automatically on subsequent freezes. |
| **Observable** | Trust/segment_n/qualified are surfaced in every decision report. |
| **Loop continues** | Yes. |

**This stage is the strongest part of the design.** Cold-start is handled correctly: a fresh deploy can never produce an `act` decision until a category proves itself, and the graduation threshold is data-driven. No crash points found — all division/None cases are explicitly guarded.

**Issue:** Trust graduates only from **SQLite** rows. If `freeze_prediction` ever silently fails (F-1/F-4 contention) while `resolve_event` succeeds to JSON, those events count toward JSON calibration but **never toward trust graduation**. The category can stay dormant indefinitely with no error. → **P1 (ties to F-1/F-4).**

---

## Stage 9 — Decision Report

`backend/app/services/decision_report_service.py`

| | |
|---|---|
| **Created** | Flat review dict: `event`, `probability`, `market_view`, `edge {raw, adjusted, trust}`, `diagnosis`, `confidence`, `recommendation {decision, action}`, `risk`, `category`, `status`. |
| **Persisted** | **Nothing.** Pure assembly — prediction dict + event record in, report out. Regenerated on every call. |
| **Can fail** | Heavy `.get(...)` with defaults throughout; tolerates `record=None`. No deref hazards found. |
| **Recoverable** | N/A (stateless). |
| **Observable** | Three routes: `GET /events/decisions/open`, `GET /events/{id}/decision`. |
| **Loop continues** | Yes. |

No issues. Stateless and defensive. **The `decision` reported here is the frozen verdict from Stage 5**, not recomputed — which is correct for a point-in-time commitment model, but means a report can show `act` for a category that has since gone dormant (the row's frozen `decision` is never re-evaluated).

---

## Findings Index

### P0 — stops or silently corrupts the loop

**F-1. Multi-process scheduler + non-cross-process locks → duplicate runs, lost JSON updates, orphaned predictions.**
- `start_scheduler()` runs in every process that imports `app.main` (`main.py:21`). With the shipped `uvicorn.run(reload=True)` (`run.py:8`), the reloader parent + worker both run the lifespan → **2 schedulers fire every cron tick**. Each extra Uvicorn worker adds another.
- `max_instances=1` (`scheduler.py:81,87`) is per-scheduler, not global.
- Consequences: (a) N× LLM cost per discover; (b) N concurrent `auto_resolve_events` racing on `event_store.json` — `locked_file` is a `threading.RLock` (`file_store.py:13-31`), not cross-process, so read-modify-write interleaving **loses events** (atomic `os.replace` prevents a torn file, not a lost update); (c) concurrent SQLite writes raise `OperationalError` on the 30s `timeout` (`sqlite_db.py:52`), caught per-event but leaving JSON-resolved / SQLite-unresolved orphans.
- **Fix:** run the scheduler in exactly one process. Either (a) gate `start_scheduler()` behind a single-worker deployment (`workers=1`), or (b) use an external scheduler / a leader-election lock (e.g. a `scheduler_lock` row in SQLite + `UPDATE ... WHERE owner=?`), or (c) move cron to a separate process (e.g. a systemd timer / celery beat) decoupled from the web workers. Also switch `locked_file` to an OS-level lock (`filelock`) or migrate the JSON stores entirely into SQLite.

**F-2. `AUTO_VERIFY_THRESHOLD=1.0` default means fuzzy market matches never auto-resolve.**
- The fuzzy matcher returns Jaccard < 1.0 for any non-exact normalized-question match (`text_match.py`). With threshold `1.0` (`config.py:168-170`), **only exact matches verify**.
- Any event whose market question drifts (synonym, capitalization, minor rewording — common on Polymarket/Manifold) lands in `event_market_links` with `verified=0` and **stays pending forever** until a human hits `POST /events/{event_id}/link/verify`.
- Over 90 days unattended, this starves the loop of resolved predictions for exactly the events most likely to need resolution.
- **Fix:** lower the default to ~0.85 (align with the existing `FUZZY_THRESHOLD=0.82` gate so they aren't two confusing knobs), OR add a background job that re-evaluates pending links against freshly-settled markets and auto-promotes when the question still matches a settled contract at a sane threshold.

### P1 — degrades calibration quality

**F-3. Market un-resolve / flip is unhandled.**
- `auto_resolve_events` skips events with `outcome is not None` (`event_resolve_service.py:230`); `score_prediction` is one-shot (`status='open'` guard). A flipped market leaves stale Brier frozen in calibration forever.
- No `unresolve`/`reopen`/re-score path, no endpoint, no reconciliation job.
- **Fix:** add a periodic reconciliation that detects outcome changes on already-resolved events (compare stored outcome vs. source's current outcome for recently-resolved markets within a window) and re-scores, plus an `unresolve` endpoint for manual correction.

**F-4. Resolve writes JSON → audit → SQLite under three separate locks, no transaction.**
- JSON is committed first (`event_resolve_service.py:130`). Any later failure (audit write, SQLite `OperationalError`, process kill) leaves the event marked resolved in JSON but the prediction `open` in SQLite — **orphaned, never reconciled**, never flagged.
- This also breaks trust graduation (Stage 8): orphaned events count toward JSON calibration but never toward SQLite `segment_skill`.
- **Fix:** either (a) write SQLite first and treat it as the source of truth, then JSON (so a failure leaves the prediction `open` and re-resolvable, not the event falsely resolved), or (b) add a reconciliation job that finds `open` predictions whose events are already resolved and scores them. Document which store is authoritative for calibration.

**F-5. `parse_outcome_prices` returns `0.5, 0.5` on any failure — silent synthetic market price.**
- `polymarket_service.py:107-108`. A transient parse/fetch error freezes a **fake 50% market probability** into `raw_edge`/`adjusted_edge` with no log. The frozen prediction then scores against a fabricated baseline.
- Over 90 days, intermittent price-fetch failures will inject synthetic edges into the calibration aggregate and the decision surface, biasing both.
- **Fix:** on parse failure, either skip the freeze for that event (no market price → no genuine edge → same as the existing "no market, no edge" gate) or mark the prediction with a `price_source=fallback` flag and exclude it from calibration. At minimum, log the failure.

**F-6. Two parallel calibration systems reading from disjoint stores (JSON vs SQLite).**
- `calibration_feedback_service` reads `event_store.json`; `calibration_service_event` + `prediction_store` read `v2_loop.db`. Same `CALIBRATION_FEEDBACK_MIN_SAMPLES` and `skill_score` math, **different populations** (every resolved event vs. only `act`/`watch` predictions).
- `/events/calibration` (JSON) and `/events/predictions/calibration` (SQLite) can report materially different numbers for the same deployment.
- Latent today (`CALIBRATION_FEEDBACK_ENABLED=False`), but the moment it's enabled the two feedback paths will silently disagree about a category's skill.
- **Fix:** declare one store authoritative for calibration (recommend SQLite, since it has the frozen point-in-time commitment and the trust path already uses it). Either remove the JSON-reading path or make it read from SQLite. Unify the population definition.

**F-7. Widespread silent `except Exception: return []` in source adapters, and `process_event` swallows programming bugs.**
- `calibration_feedback_service.py:225`, `economic_data_service.py:28`, `official_source_service.py:24`, `rss_service.py:51`, `sec_edgar_service.py:27` — none log the exception. A chronically-dead RSS feed is invisible.
- `process_event` (`event_intelligence_service.py:368-374`) catches `Exception` broadly — a `KeyError`/`AttributeError` from a code bug looks identical to a network blip (candidate just vanishes, one warning line).
- **Fix:** replace `return []` with `logger.exception(...); return []`. Add a `failed_events` counter to the discover result so the scheduler log line surfaces degradation. Consider separating "expected" (network/parse) from "unexpected" (programming) exceptions in `process_event`.

**F-8. No retry/backoff on any external call.**
- Every `httpx.AsyncClient(timeout=30)` and every LLM call is a single attempt. A transient 429/5xx on a market source or the LLM provider costs a full 24h until the next cron tick.
- Not a crash, but a reliability/quality tax. For a 90-day unattended run, expect occasional "thin" discover days with no recovery mechanism.
- **Fix:** wrap external fetches and LLM calls in a bounded retry (e.g. tenacity, 3 attempts, exponential backoff). At minimum retry once on 429/5xx.

### P2 — future maintenance cost

**F-9. `_migrate` rename-rebuild window is a data-loss vector if killed mid-migration.**
- `prediction_store.py:120-136`: `RENAME TO predictions_old` → rebuild → copy → `DROP TABLE predictions_old`. If the process is killed between rename and copy-completion, the `predictions` table is left **empty** with no recovery path.
- Today this migration is idempotent and usually a no-op (most DBs already have `UNIQUE(event_id)`), so it runs once per DB lifetime. Low probability, high blast radius.
- **Fix:** wrap the rename/rebuild/copy/drop in a single transaction (DDL + DML in one `executescript` under one connection), or copy-first-then-swap.

**F-10. SQLite calls are synchronous inside async resolve/freeze paths.**
- `score_prediction`, `upsert_link`, `freeze_prediction`, `segment_skill` are sync and run on the event-loop thread inside `async` callers. Worst case a stuck writer (`timeout=30.0`) stalls the event loop for 30s.
- Fine at current volume, latent at scale.
- **Fix:** wrap DB calls in `asyncio.to_thread` / `run_in_executor`, or accept `workers=1`.

**F-11. `event_audit.jsonl` growth under cache-busting.**
- Scheduler forces `use_cache=False` (`scheduler.py:64`), so every daily discover appends up to `EVENT_DISCOVER_LIMIT` snapshots per event. Compaction triggers past 5000 lines / keeps 200 per event (`event_audit_service.py:89-123`) — bounded, but a script hammering `/discover` with cache-busts could grow it fast. Compaction failures are swallowed (`:117-123`).
- **Fix:** rate-limit the discover endpoint, or make compaction failure non-silent.

**F-12. Calibration snapshots are non-reproducible after the fact.**
- `estimated` is computed from the audit trajectory at resolve time and frozen. Re-resolving later (more snapshots) gives a different score. There is no "recompute all calibration" path.
- For auditability of historical calibration numbers, you must also retain the audit-log state at resolve time.
- **Fix:** document this; consider persisting the `estimated` value used in the calibration snapshot (it may already be on the JSON record — verify) so historical scores are reproducible.

**F-13. `_migrate` uses f-string column interpolation in SQL.**
- `prediction_store.py:117` interpolates column names into SQL via f-string. Safe today (columns are hardcoded in `_MIGRATIONS`/`not_null_defaults`), but a future manual schema edit could introduce injection/corruption.
- **Fix:** whitelist column names; reject anything not in the known set.

---

## Final Question: Can this system run unattended for 90 days and continuously accumulate resolved predictions?

**No — not with the current configuration.** Two issues block it:

1. **F-1 (multi-process scheduler).** With the shipped `uvicorn.run(reload=True)` launcher, **two schedulers already run in development**, double-firing discover and resolve, racing on `event_store.json` (lost updates) and SQLite (`OperationalError` orphans). In any multi-worker production deployment this gets worse. The loop does not degrade gracefully here — it silently loses data. **This must be fixed before any unattended run.**

2. **F-2 (`AUTO_VERIFY_THRESHOLD=1.0`).** With the shipped default, every market whose question wording drifts even slightly lands in the pending queue and **never resolves without human action**. Polymarket and Manifold routinely re-word questions. Over 90 days, a large fraction of otherwise-resolvable events will pile up in `GET /events/links/pending` unattended, starving calibration of exactly the samples it needs. **Lower the threshold (or add an auto-promote job) before any unattended run.**

**If F-1 and F-2 are fixed, the loop can run unattended — with caveats:**

- It **will** continuously accumulate resolved predictions (the write-once freeze, idempotent scoring, per-event/per-source isolation, and cold-start handling are all sound).
- Calibration quality will **slowly degrade** due to F-3 (flipped markets), F-4 (orphaned predictions), and F-5 (synthetic 0.5 prices) — none of which are surfaced today. Over 90 days, expect the calibration aggregate to drift from reality without any alarm.
- There is **no monitoring signal** for any of the silent-degradation paths (F-3/F-4/F-5/F-7). An operator would not know the loop is sick until they manually diff the two calibration endpoints (F-6) or notice the open-decisions surface is stale.
- A single transient failure on the LLM or a market source costs a full 24h (F-8, no retry).

**Recommended minimum hardening before a 90-day unattended run (in order):**

1. **F-1:** Guarantee single-scheduler execution (single worker, or leader-elected / external scheduler).
2. **F-2:** Lower `AUTO_VERIFY_THRESHOLD` to ~0.85, or add an auto-promote reconciliation job.
3. **F-5:** Stop freezing synthetic 0.5 prices (skip freeze on price-fetch failure, or flag + exclude).
4. **F-4 + F-3:** Add a reconciliation job that (a) re-scores orphaned `open` predictions on already-resolved events, and (b) detects and re-scores flipped outcomes within a recent window.
5. **F-7:** Add `logger.exception` to every silent `return []`; add a `failed_events`/`pending_count`/`orphaned_predictions` metric to the scheduler result so degradation is observable from logs alone.
6. **F-6:** Declare SQLite the calibration source of truth; unify or remove the JSON-reading feedback path before enabling `CALIBRATION_FEEDBACK_ENABLED`.

With items 1–3 done, the loop is **safe to run unattended** (it will not lose data or crash). With items 4–6 done, its calibration will also **stay trustworthy** for the full 90 days. As-is, it is a well-engineered prototype that needs a hardening pass before production autonomy.

---

## Appendix — key file references

| Stage | File | Load-bearing lines |
|---|---|---|
| Scheduler | `backend/app/core/scheduler.py` | 26-29 (job defaults), 74-89 (add_job) |
| App lifespan | `backend/app/main.py` | 21 (start_scheduler) |
| Launcher | `backend/run.py` | 8 (reload=True) |
| Discover | `backend/app/services/event_intelligence_service.py` | 308-435 (discover + persist) |
| Event store | `backend/app/memory/event_store.py` | 44-91 (save_events), 74-80 (write-once outcome) |
| File locks | `backend/app/utils/file_store.py` | 13-31 (RLock, in-process only) |
| Verified link | `backend/app/memory/event_market_link_store.py` | 28-44 (schema), 182-194 (set_verified) |
| Verify endpoint | `backend/app/api/routes/events.py` | 216-245 |
| Freeze | `backend/app/memory/prediction_store.py` | 169-255 (freeze), 77-136 (migrate) |
| Resolve | `backend/app/services/event_resolve_service.py` | 145-358 (auto_resolve), 73-142 (resolve_with_calibration), 230 (skip resolved), 307-319 (pending) |
| SQLite | `backend/app/utils/sqlite_db.py` | 29 (write lock), 52 (timeout), 69-82 (writing) |
| Price fallback | `backend/app/services/polymarket_service.py` | 101-108 |
| Calibration (JSON) | `backend/app/services/calibration_feedback_service.py` | 180-226 |
| Calibration (SQLite) | `backend/app/services/calibration_service_event.py` + `prediction_store.py:419-473` |
| Trust | `backend/app/services/diagnosis_service.py` | 27-113 |
| Decision report | `backend/app/services/decision_report_service.py` | 35-101 |
| Config | `backend/app/core/config.py` | 128-211 (all loop settings + defaults) |

---
---

# Part 2 — Data-Model Review

**Scope:** Event / Prediction / Outcome / Calibration / Trust semantics.
**Out of scope:** implementation details (concurrency, atomicity, error handling — covered in Part 1).
**Question answered at the end:** *What is the actual data model implemented today — not what the documentation says.*

---

## 0. The actual data model in one diagram

The system is **not** one data model. It is **two overlapping models sharing an ID (`event_id`) but disagreeing on everything else**, plus a **third shadow model** (the audit log) that carries a field nobody calls a "model." Drawn honestly:

```
                    event_id  (shared key)
                        │
        ┌───────────────┼────────────────┐
        ▼                                ▼
 ┌──────────────┐              ┌──────────────────┐
 │  EVENT       │              │  PREDICTION      │
 │  (JSON file) │              │  (SQLite table)  │
 │              │              │                  │
 │  record.outcome  ◄──── dual outcome ────►  actual_outcome
 │  record.calibration                       brier_score
 │  record.probability.estimated ◄─ dup ──► ai_probability
 │  record.probability.baseline   ◄─ dup ──► market_probability
 │  record.legacy_analysis.base_rate_category ◄─ dup ─► base_rate_category
 │  record.tracking                         (no equivalent)
 └──────────────┘              └──────────────────┘
        │                                │
        │  reads for                     │  reads for
        ▼  calibration_feedback          ▼  segment_skill / trust
 ┌──────────────┐              ┌──────────────────┐
 │ CALIBRATION  │              │  TRUST           │
 │ (JSON-side)  │   ◄ disagree ►  (SQLite-side)   │
 │ counts ALL   │              │ counts act+watch │
 │ resolved     │              │ scored+observed  │
 └──────────────┘              └──────────────────┘

        ┌──────────────────────────────────┐
        │  AUDIT LOG  (event_audit.jsonl)  │  ◄── "trajectory"
        │  append-only snapshots, NOT a    │      lives here but is
        │  declared model; feeds score_event│     never reconciled with
        └──────────────────────────────────┘     Prediction.ai_probability
```

The rest of Part 2 walks each model and then names the inconsistencies.

---

## 1. Event semantics

**Source of truth:** `event_store.json`, keyed by `event_id`. Each entry is `{event_id, first_seen, last_updated, record}` where `record` validates against the Pydantic `EventRecord` (`models/event.py:224-253`).

**Mutable or immutable:** **Mutable.** `EventRecord` is upserted on every re-scan. Three fields are write-once-protected by `save_events` (`event_store.py:74-80`):
- `tracking` (user-owned) — preserved over incoming.
- `outcome` — preserved when the incoming record lacks it (so re-discovery doesn't revert a settled event).
- `calibration` — same.

Everything else (`probability`, `credibility`, `evidence`, `value_score`, `event_title`, …) is **overwritten in place** on every re-scan. The event's *current view* is mutable; its *resolution result* is frozen.

**Append-only or overwrite:** **Overwrite** at the field level. The only append-only artifact for an event is its audit-log trajectory (see H-4 below), which is **not part of the EventRecord**.

**Invariants (stated or implied):**
- I-E1: One `event_id` → one `EventRecord`. (Enforced by JSON-object key.)
- I-E2: `outcome` is write-once-once-set: a re-discovery cannot blank it (`event_store.py:77-78`).
- I-E3: `calibration` is write-once-once-set (`event_store.py:79-80`).
- I-E4: `tracking` is user-owned and sticky (`event_store.py:71-73`).
- I-E5: `outcome.status == "resolved"` is the gate for entering the calibration aggregate (`list_resolved_events`, `event_store.py:204`). A status of `"invalid"` (written on a wrong-link divergence) settles the event but **excludes** it.

**Assumptions downstream services make:**
- `calibration_feedback_service` assumes `record.calibration.brier_score` is present and finite for every resolved event (`calibration_feedback_service.py:90-109`).
- `calibration_service_event.summarize` assumes `base_rate_category` is readable as `record.legacy_analysis.base_rate_category` (see H-1 — this is the single biggest semantic smell).
- `auto_resolve_events` assumes `record.outcome is None` ⟺ "not yet resolved" (`event_resolve_service.py:230`) — **this is the assumption that breaks on a market flip** (see V-2).

**Semantic problem:** An `EventRecord` is simultaneously (a) a *live analysis snapshot* (mutable probability/evidence, refreshed each scan) and (b) a *resolution container* (frozen outcome/calibration). These are two different lifecycles fused into one record. The write-once protection on `outcome`/`calibration` is the only seam between them, and it is a *field-presence* check, not a lifecycle state machine.

---

## 2. Prediction semantics

**Source of truth:** SQLite table `predictions` (`prediction_store.py:34-60`), one row per `event_id` (`UNIQUE(event_id)`).

**Mutable or immutable:** **Immutable at freeze; terminal-transition only at resolve.** This is the cleanest model in the system.
- Freeze: `INSERT ... ON CONFLICT(event_id) DO NOTHING` (`prediction_store.py:241`). A re-scan is a no-op. The first-sight commitment is frozen forever.
- Resolve: `score_prediction` / `void_prediction` transition `open → scored | observed | voided` via `UPDATE ... WHERE status='open'` (so a terminal row can never be touched again).

**Append-only or overwrite:** Neither, strictly. It is **single-row with a state machine**: one row per event, created once, transitioned exactly once to terminal.

**Invariants:**
- I-P1: One `event_id` → at most one prediction row (`UNIQUE(event_id)`).
- I-P2: The frozen probability/edge is never recomputed. `ai_probability`, `market_probability`, `raw_edge`, `trust`, `adjusted_edge`, `decision`, and the diagnosis fields are frozen at first sight (`prediction_store.py:204-227`).
- I-P3 (V2 invariant, `prediction_store.py:262-267`): **Only `act` rows become calibration samples** (`scored`); `watch`/`skip` rows resolve to `observed` (Brier recorded, excluded from calibration). This is the load-bearing semantic choice that separates "what we acted on" from "what we merely froze."
- I-P4: `raw_edge = ai_probability - market_probability` (both 0-100). Defined in the model docstring but **not enforced by a constraint** — it's a computed-at-freeze value that could in principle drift if a migration rewrote columns independently.
- I-P5: `status` transitions are one-way: `open → {scored | observed | voided}`. No reopen path exists.

**Assumptions downstream services make:**
- `segment_skill` (trust input) assumes the prediction population is **act+watch, scored+observed** (`prediction_store.py:393`).
- `calibration_summary` assumes the population is **act-only, scored-only** (`prediction_store.py:424, 431, 438`).
- `list_open_opportunities` assumes `status='open' AND decision IN (act, watch)` is the opportunity surface (`prediction_store.py:364`).
- `decision_report_service` assumes the frozen `decision` is the verdict to report (never recomputed).

**Semantic problem:** A Prediction's `decision` (act/watch/skip) is frozen at the moment of *first sight*, but the trust that produced that decision is itself a function of the segment's calibration **at that same moment**. If the segment later graduates from dormant to qualified (more samples accrue), **existing frozen predictions do not get re-diagnosed**. So the opportunity surface can show `watch` decisions for divergences that, under today's trust, would now be `act` — and the system has no way to know. The "commitment, not trajectory" design is internally consistent, but it means **trust is point-in-time for decisions and live for new freezes** — two different semantics for the same concept.

---

## 3. Outcome semantics

**Source of truth:** **Dual. This is the central semantic inconsistency in the system.**

- **JSON side:** `record.outcome` validates against the Pydantic `Outcome` model (`models/event.py:65-80`): `{status, actual_outcome (0-100), confidence (0-1), resolved_at, source, notes}`.
- **SQLite side:** `predictions.actual_outcome` is a bare `REAL` column (`prediction_store.py:55`), written by `score_prediction` alongside `brier_score` and `resolved_at`. **No confidence, no source, no status, no notes.**

The two are written by the *same* function (`resolve_with_calibration`, `event_resolve_service.py:130-141`) but to *different schemas* with *different fields*. They are meant to describe the same settlement, but only the JSON side carries provenance (`source`, `confidence`, `notes`). The SQLite side is a denormalized, lossy copy.

**Mutable or immutable:**
- JSON `outcome`: write-once-once-set (I-E2). **Cannot be updated** — which means a market flip is unrepresentable (see V-2).
- SQLite `actual_outcome`: write-once via the `status='open'` guard. Also cannot be updated after scoring.

**Append-only or overwrite:** **Neither can change once set.** This is the semantic trap: the model has **no concept of outcome revision**.

**Invariants:**
- I-O1: `actual_outcome` ∈ [0, 100], where 0=NO, 100=YES, middle=partial/probabilistic (per `Outcome` docstring).
- I-O2 (JSON): `status == "resolved"` is the only status that enters calibration; `"invalid"` settles-but-excludes (I-E5).
- I-O3: Outcome is terminal — once written, it is never revised or withdrawn.

**Assumptions downstream services make:**
- `calibration_service_event.score_event` assumes `actual_outcome` is a clean 0-100 float and clamps defensively (`calibration_service_event.py:83-84`).
- `score_prediction` assumes `actual_outcome` is non-None — **but nothing validates this on the auto-resolve path** (the manual route validates via FastAPI `ge=0, le=100`; auto-resolve passes the source's value straight through, `event_resolve_service.py:247, 324`). A `None` from a malformed source feeds `brier_score` and raises `TypeError`.

**Semantic problem:** "Outcome" is modeled as a one-shot immutable fact. In real prediction markets, outcomes are **revisable** (Polymarket/Manifold un-resolve and re-settle). The data model has no field, status, or path for revision. The closest thing — `status="invalid"` — means "settled but excluded," not "revised." This is not an implementation bug; it is a **modeling omission**: the outcome lifecycle is missing a `revised` / `superseded` state.

---

## 4. Calibration semantics

**Source of truth:** **Dual and non-reconcilable.** Same structural problem as Outcome, but worse because the two sides measure **different populations**.

| Aspect | JSON-side calibration | SQLite-side calibration |
|---|---|---|
| Where stored | `record.calibration` (one snapshot per event, `models/event.py:83-101`) | computed on demand from `predictions` rows |
| Population | **every** resolved event (`list_resolved_events`) | **act-only, scored-only** (`calibration_summary`); **act+watch, scored+observed** for trust (`segment_skill`) |
| What's scored | `estimated` = audit-trajectory latest estimate at resolve time | `ai_probability` = frozen-at-first-sight estimate |
| Persistence | snapshot, frozen at resolve | recomputed every call (pure query) |
| Reproducibility | depends on audit-log state at resolve time (time-sensitive) | fully reproducible from frozen rows |

**Mutable or immutable:**
- JSON `calibration` snapshot: **write-once-once-set** (I-E3). Never recomputed.
- SQLite calibration aggregates: **recomputed every call** (no persistence).

**Append-only or overwrite:** The JSON snapshot is write-once (so effectively immutable). The SQLite side is stateless.

**Invariants:**
- I-C1: `brier_score = ((estimated/100) - (actual_outcome/100))²`, 0=perfect, 0.25=random, 1=fully wrong (`calibration_service_event.py:44`).
- I-C2: `skill_score = 1 - brier/0.25`, >0 beats random (`:49`).
- I-C3 (intended): a resolved event contributes to calibration **exactly once**. (Enforced on the SQLite side by `status='open'` guard; on the JSON side by write-once + `list_resolved_events` dedup by event_id.)
- I-C4 (violated — see V-1): the JSON `estimated` and the SQLite `ai_probability` **should be the same quantity** (the AI's probability for the event) but are **different points in time**: JSON uses the *latest trajectory estimate at resolve*; SQLite uses the *first-sight frozen estimate*. The two can disagree for the same event.

**Assumptions downstream services make:**
- `calibration_feedback_service` (JSON-side, default **off**) assumes Brier history is meaningful for probability fusion. It reads `record.calibration.brier_score` for **every** resolved event regardless of decision — so its notion of "category Brier" includes watch/skip/observed events that the SQLite trust path explicitly excludes.
- `diagnosis_service.calibration_trust` (SQLite-side, **on**) assumes segment stats come from `segment_skill` (act+watch). It never reads the JSON side.

**Semantic problem:** "Calibration" is **one word for three different measurements**:
1. Per-event accuracy of the *trajectory's latest* estimate (JSON snapshot).
2. Per-event accuracy of the *frozen first-sight* estimate (SQLite row).
3. Category-level skill used to trust future divergences.

The system treats these as if they were the same signal. They are not — (1) and (2) differ in *what estimate is scored*; (2) and (3) differ in *which rows are aggregated*. A category can be "well-calibrated" by one definition and "unskilled" by another, simultaneously, for the same data.

---

## 5. Trust semantics

**Source of truth:** **Derived, not stored as a first-class entity.** Trust is computed inside `diagnosis_service.diagnose` (`diagnosis_service.py:73-113`) from `segment_skill(category)` at freeze time, then **frozen into the prediction row** (`trust`, `adjusted_edge`, `qualified`, `segment_n`, `segment_skill` columns). There is no `trust` table or `trust` history.

**Mutable or immutable:** **Mutable in derivation, immutable once frozen.**
- The *formula* (`trust = clamp(skill_score(mean_brier), 0, 1)` once qualified, else `DIAGNOSIS_DORMANT_TRUST`) is live and recomputed per freeze.
- The *value* is frozen per prediction and never updated.

**Append-only or overwrite:** Trust has no append semantics — it is a derived field stamped onto a prediction at freeze. The "history" of trust is implicit in the sequence of frozen rows, not stored.

**Invariants:**
- I-T1: Trust ∈ [0, 1]. Dormant category → `DIAGNOSIS_DORMANT_TRUST` (default 0.5, `config.py:183-185`).
- I-T2: A category is `qualified` ⟺ `segment_n ≥ CALIBRATION_FEEDBACK_MIN_SAMPLES` (default 8) over act+watch, scored+observed rows (`diagnosis_service.py:98`, `prediction_store.py:393`).
- I-T3: `act` decision requires `qualified=True` (`diagnosis_service.py:66`). An unproven category caps at `watch`.
- I-T4: `adjusted_edge = raw_edge × trust × liquidity_factor` (`diagnosis_service.py:96`).
- I-T5 (implicit, load-bearing): **trust for new freezes reflects current segment state; trust for existing frozen rows reflects the segment state at their freeze.** This is never stated as an invariant but is the actual behavior — and it creates a silent drift between the *opportunity surface* (frozen, possibly stale trust) and *new decisions* (live trust).

**Assumptions downstream services make:**
- `decision_report_service` assumes the frozen `trust`/`qualified`/`segment_n` explain the verdict and never need recomputation (`build_decision_report` reads them directly).
- `freeze_prediction` assumes `segment_skill(category)` is the correct trust input **at freeze time** — but the category key comes from `record.legacy_analysis.base_rate_category` (see H-1), so trust is only as stable as that field's derivation.

**Semantic problem:** Trust is simultaneously **a property of a category** (it's derived from segment aggregates) and **a property of a prediction** (it's frozen per-row). These two views diverge over time and nothing reconciles them. The model has no concept of "trust as of now" versus "trust as frozen" — they are conflated into one frozen column that ages.

---

## Semantic inconsistencies

### S-1. Dual source of truth for the *same* event fact, in two stores
The probability, the edge, the base-rate category, and the outcome of one event are each stored **twice** (JSON `EventRecord` + SQLite `predictions`), written by the same function but to non-transactional stores with non-identical schemas. They are intended to be equal but have **no enforced relationship**. (See Part 1 F-4 for the failure mode; here the point is that the *model* permits divergence by design.)

### S-2. "Calibration" is one word for three different measurements
Per §4: trajectory-latest vs frozen-first-sight estimate (different *input*); act-only vs act+watch population (different *aggregation*). The two `/calibration` endpoints can disagree, and the feedback fusion path (off by default) disagrees with the trust path by construction.

### S-3. "Outcome" has no revision lifecycle
Per §3: outcomes are terminal and immutable. Real markets revise. The model cannot represent "resolved → un-resolved → re-resolved to the opposite outcome." The closest state (`status="invalid"`) means "excluded," not "superseded." A flip silently leaves stale Brier in both stores forever.

### S-4. Trust is point-in-time for frozen rows but live for new freezes
Per §5 / I-T5. The opportunity surface (`list_open_opportunities`) ranks by `ABS(adjusted_edge)` frozen at first sight, but the trust behind that edge may be stale relative to a category that has since graduated. The model has no "re-diagnose" concept, so decisions silently age.

### S-5. `base_rate_category` is the join key for trust but is not a first-class field
See H-1 below. It lives in `record.legacy_analysis.base_rate_category` — a bag named "legacy" — and is the **only** thing connecting a frozen prediction to its trust segment. If its derivation changes, every frozen prediction's segment membership becomes retrospectively ambiguous.

---

## Hidden coupling

### H-1. `base_rate_category` — the load-bearing field hidden in a "legacy" bag
This is the most important coupling finding. The segment key that the *entire trust/calibration system* pivots on is **not** a field on `EventRecord` or `Prediction`. It is read from three different places in three different ways:

| Reader | Path | File:line |
|---|---|---|
| `freeze_prediction` | `record.legacy_analysis.base_rate_category` | `prediction_store.py:204` |
| `calibration_feedback_service` | `record.legacy_analysis.base_rate_category` | `calibration_feedback_service.py:102` |
| `calibration_service_event.summarize` | `event.base_rate_category` (top-level, **injected by the route**) | `events.py:102`, `calibration_service_event.py:127` |

The route (`events.py:98-103`) manually extracts it from `legacy_analysis` and re-injects it as a top-level key so `summarize` can read it as `event.get("base_rate_category")`. So **the same field is accessed three different ways depending on which service reads it.** The field's name ("legacy_analysis") advertises it as deprecated, yet it is the spine of the trust model. This is hidden coupling disguised as a legacy escape hatch.

### H-2. `Probability.estimated` and `Probability.baseline` carry two different meanings depending on caller
In an `EventRecord`, `probability.estimated` is the AI estimate and `probability.baseline` is the market price (used as the freeze's `ai_probability` and `market_probability` respectively, `prediction_store.py:190-191`). But `baseline` elsewhere means "base-rate prior" (e.g. `base_rate_service`). The same field name ("baseline") is **market price** in the freeze path and **base-rate prior** in the base-rate path. Downstream readers must know which context they are in.

### H-3. `record.outcome` vs `predictions.actual_outcome` coupling via `event_id`
There is no foreign key from `predictions.event_id` to `event_store.json`'s `event_id` (they are different storage engines). The join is **purely by string equality on `event_id`**, maintained by convention in `resolve_with_calibration`. If an event is ever re-keyed (event_id changes), the prediction orphans with no referential integrity and no detection.

### H-4. The audit log is an undeclared model
`event_audit.jsonl` holds the probability/edge **trajectory** that `score_event` consumes to produce the JSON-side calibration `estimated`. It is the actual source of the trajectory_* fields on `Calibration`. But it is not modeled: there is no `Trajectory` or `Snapshot` Pydantic class, no schema, no invariant beyond "one JSON line per snapshot." It is structurally load-bearing for calibration reproducibility (I-C4) yet treated as an implementation detail.

### H-5. `decision="tracked"` as a pre-M2 sentinel
`Prediction.decision` defaults to `"tracked"` (`models/event.py:209`, `prediction_store.py:48`) and the docstring admits it "predates the M2 Decision Gate." `segment_skill` and `calibration_summary` filter on `act`/`watch` and silently exclude `tracked`. So a `tracked` row is a **ghost decision** — it exists in the enum by default, is written by old code, but is invisible to every aggregate. A future reader assuming `decision ∈ {act, watch, skip}` will be quietly wrong on legacy rows.

---

## Violated invariants

### V-1. I-C4 (calibration reproducibility) is violated by design
The invariant "the scored estimate is the event's probability estimate" is satisfied, but the *value* of that estimate differs between the two calibration systems: JSON scores the trajectory-latest, SQLite scores the frozen-first-sight. For the same event these can be materially different, so the two calibration numbers are **not measuring the same prediction**. This is not enforced anywhere and is not flagged in the model.

### V-2. I-O3 (outcome immutability) conflicts with real-world outcome revision
The invariant "once written, an outcome is never revised" is **incompatible with how prediction markets behave**. The model provides no escape valve, so when a market un-resolves, the only consistent states (re-resolve to the new outcome) are unreachable. The system instead enters an **inconsistent state** (stale outcome frozen, new outcome ignored) that no invariant forbids because no invariant contemplates it.

### V-3. "Resolved ⟺ outcome is not None" is a false equivalence
`auto_resolve_events` treats `record.outcome is not None` as "already resolved, skip" (`event_resolve_service.py:230`). But `outcome` can be non-None with `status="invalid"` (a settled-but-excluded event, I-E5). Such an event is "resolved" by the skip check yet **excluded** from calibration by `list_resolved_events`. So there exists a class of events that are resolved-but-not-calibrated, and the skip logic cannot distinguish "resolved-and-scored" from "resolved-and-invalidated." This makes a reconcile/repair job harder: you cannot ask "which resolved events lack a scored prediction?" cleanly.

### V-4. I-P3 ("only act rows are scored") is enforced only at the prediction layer
The SQLite invariant holds, but the **JSON-side calibration has no such gate** — `calibration_feedback_service` aggregates Brier over **all** resolved events including watch/skip. So the V2 invariant is true in one store and false in the other. They cannot both be "the calibration."

---

## Future migration risks

### M-1. Promoting `base_rate_category` out of `legacy_analysis` is a cross-store schema change
Because the segment key is read three different ways (H-1), any rename/promotion requires touching `prediction_store`, `calibration_feedback_service`, `calibration_service_event`, and the `events.py` route simultaneously. Frozen predictions already store it under `base_rate_category` (correct), but the *source* is `legacy_analysis`. A migration that moves the source field will silently strand every frozen prediction whose category was derived from the old location — and since trust is frozen, the damage is permanent and invisible.

### M-2. Merging the two calibration systems requires choosing a population definition
If you ever unify JSON-side and SQLite-side calibration (recommended), you must pick: is calibration over *all resolved events* or *act-only*? Each choice changes the meaning of historical Brier aggregates and the trust graduation threshold. There is no migration that preserves both interpretations; one set of historical numbers becomes wrong.

### M-3. Adding an outcome-revision lifecycle retrofits a state machine onto a terminal field
To fix V-2, `Outcome.status` needs `revised`/`superseded` states and `predictions.status` needs a re-score path. But `score_prediction` is one-shot (`status='open'` guard), and `list_resolved_events` keys off `status=="resolved"`. Adding revision means re-opening terminal states — exactly what the current invariants forbid. The migration touches the JSON Outcome model, the SQLite state machine, both calibration aggregates, and the resolve idempotency logic.

### M-4. The audit log has no schema, so it cannot be migrated
Because `event_audit.jsonl` is an undeclared model (H-4), any change to the snapshot shape is a silent breaking change for `score_event`'s trajectory input. There is no version field, no validator, no migration path. Old lines and new lines coexist untyped.

### M-5. The `EventRecord` fuses two lifecycles (live analysis + resolution container)
A future move to separate "live analysis view" from "resolved event" into two models is blocked by the write-once field-presence seam (`event_store.py:77-80`). That seam is the only thing distinguishing the two lifecycles today; splitting them requires a real state machine on the event, which does not exist.

---

## Answer: What is the actual data model implemented today?

Not what the docs say. Stripped to what the code actually enforces:

1. **An Event is a mutable JSON record** (`event_store.json`) keyed by `event_id`, whose `record` is validated against `EventRecord`. It is simultaneously a live analysis snapshot (probability/evidence overwritten each scan) and a resolution container (`outcome`/`calibration` write-once). **It has no lifecycle state machine** — "resolved" is inferred from `record.outcome is not None`, and "resolved-and-valid" from `outcome.status == "resolved"`.

2. **A Prediction is an immutable SQLite row** (`predictions`, `UNIQUE(event_id)`), frozen once at first sight, transitioned once to a terminal status. This is the only model with a real state machine (`open → scored | observed | voided`) and it is the cleanest.

3. **An Outcome is a dual, denormalized, terminal fact** — a rich `Outcome` object on the JSON side (status, confidence, source, notes) and a bare `REAL actual_outcome` on the SQLite side, written by the same function, **non-transactionally**, with **no revision path**.

4. **Calibration is two non-reconcilable measurements**: a frozen per-event JSON snapshot (scoring the trajectory-latest estimate, over all resolved events) and a live SQLite aggregate (scoring the frozen-first-sight estimate, over act-only or act+watch rows). They share vocabulary (`brier_score`, `skill_score`, `min_samples`) but **different input, different population, and a different source of truth**.

5. **Trust is a derived field, not an entity** — computed from `segment_skill` at freeze time and stamped onto the prediction, then never recomputed. It ages silently relative to the live segment state.

6. **The join key for the whole trust/calibration system — `base_rate_category` — is not a first-class field.** It lives in `record.legacy_analysis` (a bag literally named "legacy"), is read three different ways by three services, and is the spine of a model that treats it as an afterthought.

7. **There is a third, undeclared model — the audit-log trajectory** (`event_audit.jsonl`) — that is structurally load-bearing for JSON-side calibration (it supplies the `estimated` that gets scored) but has no schema, no validator, and no version.

**In one sentence:** the system has *one* well-modeled entity (Prediction, immutable with a real state machine), *one* fused-lifecycle entity (Event, mutable with no state machine), *one* dual-source terminal fact (Outcome, non-revisable), *two parallel calibration systems that disagree by construction*, a derived-and-frozen trust concept that drifts, and a join key for the entire feedback layer that is hidden inside a field named "legacy." The data model works — but it works by convention across two storage engines and three undeclared coupling points, not by enforced invariant.

**Highest-leverage data-model fixes (in order):**
1. **Promote `base_rate_category` to a first-class field** on `EventRecord` (and keep it the single source for both the JSON and SQLite sides). This removes the hidden join key (H-1, M-1).
2. **Declare one calibration source of truth** (recommend SQLite — it has the frozen commitment and the real state machine) and either delete the JSON-side feedback path or make it read SQLite. This collapses S-2 / V-4 / M-2.
3. **Add an outcome-revision lifecycle** (`revised`/`superseded` on `Outcome.status`, plus a re-score path on `predictions`). This fixes S-3 / V-2 / M-3 — the single biggest correctness gap for a 90-day unattended run.
4. **Give the event a real lifecycle state** (e.g. `discovered | tracking | resolved | invalidated`) instead of inferring it from `outcome is not None`. This fixes S-1 / V-3 / M-5.
5. **Model the audit-log trajectory explicitly** (a `Snapshot`/`Trajectory` type with a version). This fixes H-4 / M-4 and makes calibration reproducible.

Until #1 and #2 are done, "calibration" is not a single concept in this system — it is two concepts wearing the same name, and any operator trusting the calibration number is implicitly trusting one of two divergent measurements without knowing which.

---
---

# Part 3 — Operational Resilience Review

**Scope:** Recovery paths, retry behavior, idempotency, duplicate processing, data-corruption risk, and backfill capability under six concrete failure scenarios:
LLM API outage · Polymarket outage · RSS failure · scheduler restart · process crash · disk-write failure · partial persistence.
**Out of scope:** concurrency/atomicity internals (Part 1), data-model semantics (Part 2).
**Final question answered at the end:** *Could this system recover automatically after a 24-hour outage?*

Severity scale used below: **Critical** (data loss or silent corruption, unrecoverable without manual repair) · **High** (loop stalls or degrades silently, recovers on next tick or with a manual nudge) · **Medium** (single-tick quality loss, self-heals) · **Low** (cosmetic / future cost).

---

## 0. Resilience posture at a glance

The system is built on a **fail-soft, no-retry** philosophy. Almost every external call is wrapped so a failure *degrades gracefully* (returns empty, falls back, logs) rather than crashing — but **nothing retries, nothing backs off, and nothing detects degradation**. There is no health probe, no DLQ, no alert hook, no reconcile job. Durability is protected well (atomic writes, quarantine, write-once gates); *correctness over time* is protected poorly (no reconciliation, no flip handling, no orphan detection).

| Concern | Implemented? | Where |
|---|---|---|
| Atomic file writes (no torn files) | ✅ Yes | `file_store.py:85-105` (`write_json_atomic`) |
| Corruption quarantine | ✅ Yes | `file_store.py:34-39` (`_quarantine_corrupt` → `.corrupt`) |
| Strict read aborts overwrite-on-corrupt | ✅ Yes | `file_store.py:63-82` (`read_json_strict`) |
| Per-source / per-event failure isolation | ✅ Yes | `asyncio.gather(return_exceptions=True)` |
| Write-once outcomes / freeze / score | ✅ Yes | event_store + prediction_store |
| Scheduler misfire coalescing | ✅ Yes (5-min window) | `scheduler.py:26-29` |
| HTTP retry / backoff | ❌ No | single attempt, `timeout=30` |
| LLM retry / backoff | ❌ No | catches → `narrative_type=API_ERROR` fallback |
| Health / readiness probe | ❌ No | `main.py` has no `/health` route |
| Reconciliation / backfill of loop state | ❌ No | only `backfill_market_zh.py` (URLs + i18n) |
| Degradation metrics / alerting | ❌ No | only `logger.warning` |

---

## 1. Recovery paths

### R-1. After a scheduler restart (process restart, normal)
- APScheduler is **in-memory only** (default jobstore). On restart it rebuilds the two cron jobs fresh (`start_scheduler`, `scheduler.py:74-90`).
- The in-memory store is *not* the data store — all durable state is in `event_store.json`, `event_audit.jsonl`, and `v2_loop.db`. **A restart loses no committed data.** The only loss is the in-memory scheduler state, which is fully reconstructable from the cron triggers.
- Next cron tick resumes normally. No recovery action needed. **Severity: Low (by design, correct).**

### R-2. After a missed cron run (app down at 07:15 / 22:30)
- `coalesce=True` + `misfire_grace_time=300` (`scheduler.py:26-29`): if the app comes back within **5 minutes** of the missed fire time, the job fires once (no stampede). Beyond 5 minutes, **the run is dropped silently** (APScheduler default for cron jobs is to not reschedule a missed past run).
- A 24-hour outage therefore **misses both jobs for that day with no makeup**. The discover job not running means no new predictions frozen that day; the resolve job not running means markets that settled during the outage are not scored until the *next* scheduled resolve (up to ~24h later).
- **There is no "catch-up" logic** — a missed discover is never re-run, a missed resolve is never re-run. The loop just skips a day. **Severity: High** (see the 24-hour outage answer; the system *does* resume, but the missed day's samples are permanently gone for discover, and resolve is delayed).

### R-3. After a partial persistence (crash between store writes)
- `resolve_with_calibration` writes JSON → audit → SQLite in three separate non-transactional steps (`event_resolve_service.py:130-141`).
- If the process crashes after the JSON write but before the SQLite `score_prediction`, the event is marked resolved in JSON but its prediction stays `open` in SQLite. **This orphan is never reconciled**: `auto_resolve_events` skips events with `outcome is not None` (`event_resolve_service.py:230`), so it will never re-attempt the score.
- Symmetrically, `save_events` (JSON) is the gate in `_persist_events`: if it fails, audit+freeze are skipped cleanly (good); if it succeeds and a later per-event freeze fails, the event is durable in JSON but has no prediction row (also self-heals on next discover via `use_cache=False`). **Severity: High** for the resolve orphan (silent, permanent); Medium for the freeze gap (self-heals next day).
- **No reconciliation job exists** to find `open` predictions on already-resolved events, or resolved events missing a scored prediction. This is the single biggest recovery-path gap.

### R-4. After a disk-write failure (full disk, I/O error)
- JSON writes: `write_json_atomic` writes to a temp file then `os.replace`; on any exception it unlinks the temp and **re-raises** (`file_store.py:100-105`). The durable file is never truncated or partially written.
- `save_events` catches the re-raise and logs "Event store write failed, skipping audit/freeze", then returns (`event_intelligence_service.py:417-421`). The in-memory events are returned to the caller but **not durable** — they're lost on next restart.
- SQLite: `writing()` rolls back and re-raises on exception (`sqlite_db.py:78-80`). Per-event resolve calls catch it and `continue` (`event_resolve_service.py:253-258`).
- **Recovery:** next discover/resolve run re-attempts once the disk has space. No data corruption (atomicity holds), but **the failed run's events are silently dropped** unless they recur in a future candidate pool. **Severity: Medium** (transient; data integrity preserved, throughput lost).

### R-5. After a corrupt store file
- **JSON reads** bifurcate: `read_json` (read paths) logs, **quarantines to `<path>.corrupt`**, and returns the fallback `{}` — the app keeps running on an effectively empty store. `read_json_strict` (write paths) quarantines and **raises**, aborting the write so durable data isn't overwritten with empty.
- **This is the strongest resilience feature in the system.** A corrupt `event_store.json` cannot silently propagate: readers degrade, writers refuse.
- **But** a quarantined-then-empty store means the discover run proceeds as if fresh — **it will re-discover and re-freeze events, generating duplicate audit snapshots and, if `event_id`s drift, duplicate predictions** (see D-2). And the `.corrupt` copy is "latest only" — successive corruptions overwrite the quarantine, losing forensic history. **Severity: Medium** (corruption contained, but recovery re-runs discovery from scratch with dedup risk).

### R-6. After a flipped market outcome (Polymarket/Manifold un-resolve + re-settle)
- **No recovery path exists.** `auto_resolve_events` skips resolved events; `score_prediction` is one-shot (`status='open'` guard). The stale outcome and stale Brier are frozen forever in both stores. No endpoint, no job, no detection. **Severity: Critical** for calibration integrity over a long run (Part 2 V-2; this is also a resilience gap because the system cannot *return to a correct state* after a real-world revision).

---

## 2. Retry behavior

### RT-1. HTTP calls (Polymarket / Manifold / Kalshi / RSS / SEC / BLS / GNews)
- Every source uses `httpx.AsyncClient(timeout=30)` with a **single attempt** and `raise_for_status()` (e.g. `polymarket_service.py:47-49`). No tenacity, no retry loop, no exponential backoff.
- A transient 429 or 503 propagates to the per-source `try/except` and that source contributes `[]` for the scan. The next daily run retries naturally, but **a 24h blip costs a full day for that source**.
- **Detection:** source failure is logged (`event_intelligence_service.py:286-290`; `collect_shared_articles` logs `[label]: result`). **Recovery:** next cron tick. **Severity: Medium** (self-healing, but slow — see RT-3).

### RT-2. LLM calls (DeepSeek / DashScope / OpenAI)
- `ask_llm` (`openai_service.py:32-78`) catches **all** exceptions and returns a deterministic fallback: `{probability: 50.0, confidence: 0.0, narrative_type: "API_ERROR", reasoning: <exc>}`. **No retry.**
- This means an LLM outage does **not** crash the discover run — it produces events whose `probability.estimated` is a flat 50.0. Those events can still be frozen as predictions (freeze only requires `ai` and `market` non-None, `prediction_store.py:192`), so **a frozen prediction during an LLM outage commits a 50% AI estimate against the real market price** — the edge is pure market--price-minus-50, meaningless, and it will be scored against the real outcome later, polluting calibration.
- **Detection:** the only signal is `narrative_type == "API_ERROR"` on the record and `confidence: 0.0`. **No metric, no alarm, no exclusion of API_ERROR predictions from calibration.** **Recovery:** next cron tick. **Severity: High** (silent calibration pollution during outages — see Part 1 F-5 sibling risk; this is the LLM-side equivalent).

### RT-3. No backoff anywhere
- A retrying-with-backoff layer on (a) every market source fetch and (b) the LLM call would convert "transient blip → lost day" into "transient blip → retry within the same run." This is the cheapest single resilience improvement available. **Severity: Medium.**

---

## 3. Idempotency

### I-1. `freeze_prediction` — idempotent ✅
- `INSERT ... ON CONFLICT(event_id) DO NOTHING` (`prediction_store.py:241`) + `UNIQUE(event_id)`. A re-scan or a duplicate run is a pure no-op; the first-sight commitment is preserved exactly. **The strongest idempotency in the system.**

### I-2. `score_prediction` / `void_prediction` — idempotent ✅
- Both update only `WHERE status='open'` (`prediction_store.py:293, 319`). Once terminal, a re-resolve matches zero rows → no-op. A duplicate resolve run cannot double-score. **Correct.**

### I-3. `save_events` outcome/calibration — write-once idempotent ✅ (with a gap)
- Write-once protection (`event_store.py:74-80`) means a re-discovery that lacks `outcome`/`calibration` inherits the stored values — a settled event is not reverted. **Idempotent for re-discovery.**
- **Gap:** idempotency is by *field presence*, not by value. If `resolve_event` is somehow called twice with *different* outcomes (e.g. a flip re-resolved manually), the **second call overwrites the first** (`resolve_event` does `record["outcome"] = outcome` unconditionally, `event_store.py:124`). The write-once protection only blocks *re-discovery* from blanking; it does **not** block a second *resolve*. So resolve is idempotent only if called with the same outcome. **Severity: Medium** (manual double-resolve with differing outcomes is not guarded; the prediction side, however, IS guarded by the one-shot status gate — so the JSON and SQLite can end up with *different* outcomes).

### I-4. `auto_resolve_events` skip logic — idempotent but lossy ⚠️
- Skips events with `outcome is not None` (`event_resolve_service.py:230`). Idempotent in the sense that a re-run won't re-resolve — but it also won't *repair* an orphaned prediction (R-3). **Idempotency here hides the recovery gap rather than closing it.**

### I-5. Audit-log appends — NOT idempotent by design ⚠️
- `record_event` appends a snapshot every call (`event_audit_service.py`). A duplicate discover run appends a duplicate snapshot. The scheduler forces `use_cache=False` so each run intentionally appends. **This is desired for the trajectory** (more observations = better trend analysis), but it means **duplicate processing inflates `trajectory_observations`** on the Calibration snapshot (`models/event.py:100`), which in turn affects how a reviewer judges the score's reliability. Not corruption, but a subtle duplication side-effect. **Severity: Low.**

---

## 4. Duplicate processing

### D-1. Scheduler double-fire (multi-process) — Critical duplication
- Already covered in Part 1 F-1: `reload=True` runs two schedulers; multi-worker runs N. Each fires discover + resolve independently → N× LLM cost, N concurrent races on `event_store.json` (lost updates) and SQLite (`OperationalError`). **Severity: Critical.** This is the dominant duplicate-processing risk.

### D-2. Duplicate predictions from `event_id` drift
- `freeze_prediction` dedupes on `event_id`. But `event_id` is derived from the market contract / question at discovery. If the same contract surfaces twice under different `event_id`s (e.g. question wording changed slightly between scans, producing a different hash), **two predictions are frozen for what is economically the same market.** The candidate dedup (`candidate_dedup_service`, Jaccard ≥0.82) runs *within* a single scan, not *across* scans — so cross-scan drift is not deduped. **Severity: Medium** (rare but produces double-counted calibration samples with no detection).

### D-3. Duplicate audit snapshots — benign
- See I-5. Desired behavior, bounded by compaction (5000 lines / 200 per event). **Severity: Low.**

### D-4. Duplicate resolve runs — safe on the SQLite side, unsafe on the JSON side
- A second resolve run is a no-op on predictions (status gate) but, if it reaches `resolve_event` with a different outcome, overwrites the JSON outcome (I-3 gap). **Severity: Medium.**

---

## 5. Data corruption risk

### DC-1. Torn/partial JSON writes — fully mitigated ✅
- `write_json_atomic` (tempfile + `os.replace`) and `rewrite_lines_atomic` guarantee the durable file is never half-written. A crash mid-write leaves the old file intact. **Severity: None (by design).**

### DC-2. Overwrite-corrupt-on-read — fully mitigated ✅
- `read_json_strict` raises on corrupt JSON so a write-path aborts instead of writing `{}` over real data. Corrupt files are quarantined to `.corrupt`. **Severity: None (by design).**

### DC-3. Cross-process write race on JSON — Critical corruption risk
- `locked_file` is a `threading.RLock`, **not cross-process** (`file_store.py:13-31`). Two processes doing read-modify-write on `event_store.json` interleave: both read the same base, both write their version, **one update is silently lost** (atomic `os.replace` prevents a torn file but not a lost update). **Severity: Critical** (silent data loss; ties to D-1).

### DC-4. SQLite cross-process contention — Medium
- SQLite's own file locking + WAL + `timeout=30` (`sqlite_db.py:52`) handles cross-process writes by waiting, then raising `OperationalError` on timeout. The `_WRITE_LOCK` (`sqlite_db.py:29`) only serializes in-process. Under contention, writes raise rather than corrupt — so this is a **throughput/availability** risk, not a corruption risk. **Severity: Medium.**

### DC-5. Silent synthetic-data corruption (0.5 prices + API_ERROR estimates)
- Not disk corruption but **semantic corruption**: `parse_outcome_prices` returns `0.5, 0.5` on failure (`polymarket_service.py:107-108`) and `ask_llm` returns `50.0` on failure (`openai_service.py:72-78`). A frozen prediction built during an outage commits fabricated market/AI values that look valid and flow into `raw_edge`, `adjusted_edge`, and eventually Brier. **Severity: High** (silent, undetected, permanent once frozen — see Part 1 F-5).

### DC-6. `_migrate` rename-rebuild window — Critical if triggered mid-kill
- `prediction_store.py:120-136`: `RENAME TO predictions_old` → rebuild → copy → drop. A process kill between rename and copy leaves the `predictions` table **empty**. The migration is idempotent and usually a no-op (most DBs already have `UNIQUE(event_id)`), so it runs once per DB lifetime — low probability, but **catastrophic blast radius** (all predictions lost, no recovery from the renamed `_old` because the drop already... actually the drop is last, so `_old` survives if killed before drop — but `_old` is never consulted after a fresh schema exists). **Severity: Critical** (conditional on being killed mid-migration).

---

## 6. Backfill capability

### B-1. Forward-only by default
- The loop is designed **forward-only**: discover freezes new predictions, resolve scores settled ones. There is no general "re-derive X from Y" capability.

### B-2. The only backfill script is cosmetic
- `scripts/backfill_market_zh.py` backfills **market URLs** (`source.url`) and **Chinese translations** of evidence items. It explicitly goes through `save_events` (preserving `first_seen`/`tracking`) and clears the event cache. **It does not touch predictions, outcomes, calibration, trust, or links.** Useful for presentation, irrelevant to loop recovery.

### B-3. No backfill for the loop's state machines — the critical gap
There is **no script or job** that can:
- Re-score a prediction whose event is already resolved but whose prediction is stuck `open` (the R-3 orphan).
- Re-resolve an event whose market outcome flipped (the R-6 gap).
- Re-freeze a prediction that was skipped during an LLM outage (RT-2 pollution).
- Rebuild the SQLite prediction store from `event_store.json` (or vice versa) after a store divergence.
- Recompute calibration from scratch after the audit log changed.

Every one of these requires **direct DB/JSON surgery** by an operator today. **Severity: High** — this is the operational difference between "self-healing loop" and "loop that needs a human on call."

### B-4. Calibration is re-derivable (one side)
- SQLite-side calibration (`calibration_summary`, `segment_skill`) is a pure query over `predictions` rows — **fully re-derivable** from the frozen rows, no backfill needed. This is the loop's one genuine resilience win: even if the calibration *report* is lost or wrong, recomputing it is free.
- JSON-side calibration is a frozen snapshot — **not** re-derivable without the audit-log state at resolve time (Part 2 I-C4 / M-4). If the audit log is lost, historical JSON calibration numbers are unrecoverable.

---

## Risk register (consolidated, by severity)

| ID | Risk | Detection method | Recovery method | Severity |
|---|---|---|---|---|
| **RES-1** | Scheduler double-fire (multi-process / reload) → duplicate runs, lost JSON updates | No detection today. Add: log "scheduler started" with PID; alert if >1 PID/tick. Detect via duplicate audit timestamps within same minute. | Run single worker, or leader-elected scheduler, or external cron. | **Critical** |
| **RES-2** | Missed cron run (>5 min outage) never made up | No detection. Compare expected vs actual run count per day in logs. | None automatic — the day is skipped. Manual: trigger `/resolve/auto` + `/discover`. | **High** |
| **RES-3** | Orphaned prediction (event resolved in JSON, prediction stuck `open` in SQLite) | **No detection.** Add: query `predictions p JOIN event-store WHERE p.status='open' AND event.outcome IS NOT NULL`. | No recovery today — needs a reconcile job (does not exist). | **High** |
| **RES-4** | Flipped market outcome frozen forever | **No detection.** Would need: re-fetch recently-resolved markets' current outcome, diff vs stored. | No recovery today — needs un-resolve + re-score path (does not exist). | **Critical** |
| **RES-5** | LLM outage freezes 50% estimates → calibration pollution | Partial: `narrative_type='API_ERROR'`, `confidence=0`. Add: alert on API_ERROR rate; exclude API_ERROR predictions from calibration. | Next cron tick produces real estimates, but the polluted frozen rows are permanent. | **High** |
| **RES-6** | Polymarket price-fetch failure freezes 0.5 prices → fake edges | **No detection** (silent `0.5,0.5`). Add: log on `parse_outcome_prices` fallback; flag `price_source='fallback'`. | None for already-frozen rows. Prevent: skip freeze on price-fetch failure. | **High** |
| **RES-7** | Transient HTTP blip on a market source → empty source for the day | Logged per source (`Event source failed [...]`). | Next cron tick (up to 24h delay). Add retry/backoff for same-run recovery. | **Medium** |
| **RES-8** | Disk-write failure → events dropped from that run | Logged ("Event store write failed"). | Next discover run re-attempts if candidates recur (not guaranteed). | **Medium** |
| **RES-9** | Corrupt JSON store → quarantine + empty-store operation | Logged + quarantined to `.corrupt`. | Degrades to empty store; re-discovery rebuilds. Risk: dedup drift / duplicate freezes. | **Medium** |
| **RES-10** | `_migrate` killed mid-rebuild → empty `predictions` table | App would surface no predictions; `predictions` row count drops to 0. | `_old` table survives if killed before DROP — manual recovery possible but unsupported. | **Critical** (conditional) |
| **RES-11** | SQLite cross-worker write contention → `OperationalError` | Caught per-event, logged ("failed to resolve ..."). | Per-event: skipped, never retried. That event stays unresolved until next run. | **Medium** |
| **RES-12** | Audit-log duplicate appends inflate trajectory_observations | No detection (desired behavior). | None needed — bounded by compaction. | **Low** |
| **RES-13** | Manual double-resolve with differing outcomes → JSON/SQLite outcome mismatch | No detection. | None — JSON overwrites, SQLite no-ops; they diverge silently. | **Medium** |

---

## Answer: Could this system recover automatically after a 24-hour outage?

**Partially — and "partially" is doing a lot of work here.** Break the 24-hour outage into what actually happens:

### What recovers automatically ✅
1. **The process comes back up cleanly.** APScheduler rebuilds its jobs from cron triggers; no scheduler state was durable anyway, so nothing is lost by losing it. Both jobs fire on their next scheduled time.
2. **No committed data is lost to the restart.** All durable state is in the three on-disk stores (JSON + JSONL + SQLite); the in-memory scheduler holds nothing load-bearing.
3. **The next discover run resumes freezing new predictions**, and the next resolve run resumes scoring settled markets. The loop's *forward motion* restarts without human action.
4. **Corruption and torn writes are not a risk** during the outage or recovery — atomic writes + quarantine + write-once gates hold regardless of uptime.

### What does NOT recover automatically ❌
1. **The missed day's discover run is gone forever.** `misfire_grace_time=300` means any outage longer than 5 minutes drops the missed cron run with **no makeup**. Events that only surfaced during that 24h window and then fell out of the candidate pool are never frozen. *(Mitigation: an operator can hit `POST /events/resolve/auto` and `GET /events/discover` manually after recovery — but that's not automatic.)*
2. **Markets that settled during the outage are scored late**, at the next scheduled resolve (could be another ~24h). They are not lost, just delayed — but if a settled market *un-resolves* before the delayed resolve catches it, RES-4 means the wrong outcome can be frozen with no recovery.
3. **Orphans created by a crash during the outage (RES-3) are never reconciled.** If the outage included a process crash mid-resolve, any JSON-resolved-but-SQLite-open predictions stay broken permanently — the skip logic actively prevents repair.
4. **If the outage was caused by or coincided with an LLM/Polymarket outage**, any predictions frozen during the degraded window carry fabricated 50%/0.5 values (RES-5/RES-6) that pollute calibration **permanently** — nothing detects or excludes them.
5. **There is no signal that recovery happened.** No health probe, no "loop is healthy / caught up" indicator, no metric comparing expected vs actual run count. An operator cannot tell from outside whether the loop resumed correctly or is silently degraded.

### The honest answer
**Yes, the loop will resume forward motion automatically after a 24-hour outage** — it is genuinely designed to fail-soft and self-restart. **But it will not return to a fully correct state without human intervention**, because:
- the missed discover day is permanently lost (no makeup),
- any crash-induced orphans are permanently stuck (no reconcile),
- any outage-induced fabricated values are permanently in calibration (no detection/exclusion),
- and there is no way to *know* the recovery was clean (no health signal).

A 24-hour outage is survivable for *throughput* (the loop keeps producing) but not for *correctness* (the loop accumulates silent permanent damage during the outage that no automatic path repairs). For a system whose entire purpose is accumulating trustworthy resolved predictions, that is the wrong asymmetry: it prioritizes *not crashing* over *not corrupting*.

### Minimum hardening for true automatic recovery (in order)
1. **Leader-elect the scheduler** (or run single-worker / external cron) — kills RES-1, the dominant duplicate/corruption source.
2. **Add a reconcile job** (daily) that finds and repairs RES-3 orphans (resolved event + open prediction) — this is the single highest-value recovery feature that is entirely missing.
3. **Add a missed-run makeup path** — either an external watchdog that triggers discover+resolve on recovery, or raise `misfire_grace_time` and add a "last-run tracker" that fires a catch-up if a day was skipped. Addresses RES-2.
4. **Exclude outage-produced predictions from calibration** — flag `narrative_type='API_ERROR'` and `price_source='fallback'` rows and exclude them from Brier aggregates. Addresses RES-5/RES-6.
5. **Add a health/readiness endpoint** that reports: last discover time, last resolve time, pending-link count, orphan count, API_ERROR rate. Without this, recovery is invisible. Addresses the detection gap across RES-1/2/3/5/6.
6. **Add retry/backoff** to market-source fetches and the LLM call — converts "transient blip → lost day" into "transient blip → retry in-run." Addresses RES-7 and RT-2.

With #1, #2, and #5 done, the system could recover from a 24-hour outage **correctly and observably** without human action. As it stands today, it recovers *eventually* and *partially*, and silently accrues permanent damage along the way.
