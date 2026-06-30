# Reality Feedback Loop CTO Production Readiness Review

Date: 2026-06-20  
Scope: Scheduler -> Discover -> Event -> Verified Link -> Freeze Prediction -> Resolve Outcome -> Calibration -> Trust -> Decision Report  
Review stance: production readiness only. Style, naming, formatting, and minor refactors are intentionally excluded.

## Executive Verdict

The system has a real Reality Feedback Loop implementation: scheduled discovery creates event records, freezes one market-derived prediction per event, scheduled auto-resolve tries to bind unsettled events to resolved market outcomes, and resolved predictions feed calibration and trust.

It is not yet production-ready for 90 days of unattended operation.

The main loop can continue automatically only for exact or already-verified market links. Fuzzy links fail closed into a pending human queue, and there is no durable run-state table, alerting, dead-letter queue, or health signal that proves daily discovery/resolution actually succeeded. Failures are mostly logged, not operationally observable. A quiet source/API/model failure can leave the process running but stop accumulation.

## Stage Review

### 1. Scheduler

Implementation: `backend/app/core/scheduler.py`, started from FastAPI lifespan in `backend/app/main.py`.

Data created:

- APScheduler jobs:
  - `event_discover` at 07:15 UTC when `EVENT_DISCOVER_ENABLED=true`.
  - `event_auto_resolve` at 22:30 UTC always.
- Runtime log entries for start, success, and exception paths.

Data persisted:

- No scheduler run record is persisted.
- No durable last-success timestamp, last-error, per-job duration, resolved count, discovery count, or heartbeat is stored.

What can fail:

- FastAPI process not running.
- Scheduler not started because app lifespan does not execute in a non-standard runner.
- `EVENT_DISCOVER_ENABLED=false` disables new sample creation.
- Misfire beyond 5 minutes is dropped.
- Discover or resolve job exceptions are caught and logged only.
- A job can return zero work forever without raising.

Recoverable:

- Yes at process level: exceptions do not crash the scheduler.
- Partially at job level: next scheduled run can try again.
- Not recoverable for missed runs beyond the misfire grace window; there is no catch-up queue.

Observable:

- Weak. Logs show failures if logs are collected and watched.
- No API-visible scheduler status, run history, SLO, or alertable metric.
- No persisted evidence that the loop is accumulating daily.

Can continue automatically:

- Yes, if the process stays up, configuration is correct, external dependencies work, and failures are transient.
- No guarantee of automatic catch-up or operator notification.

Production assessment:

- P0 for unattended operation: lack of durable scheduler/run observability can let the loop silently stop accumulating.

### 2. Discover

Implementation: `backend/app/services/event_intelligence_service.py`.

Data created:

- Candidate event list from Polymarket, Manifold, Kalshi, optional Polymarket crypto, and optional open-web extraction.
- Per-event analysis record with probability, credibility, impact, risk, evidence, semantics, source metadata, legacy analysis, and calibration components.
- Evidence items and translated article snippets when available.

Data persisted:

- Fresh records are saved to `EVENT_STORE_FILE` through `event_store.save_events`.
- Probability snapshots are appended to `EVENT_AUDIT_FILE` through `event_audit_service.record_event`.
- Market-derived events attempt to freeze one prediction in SQLite through `prediction_store.freeze_prediction`.
- Cache entries may be persisted in `EVENT_CACHE_FILE` when `use_cache=true`; scheduled discovery uses `use_cache=false`.

What can fail:

- Market source APIs fail or return incompatible data.
- Shared article collection fails.
- News filtering selects zero evidence, causing the candidate to be skipped.
- LLM analysis fails, times out, rate limits, or returns malformed output.
- Translation fails.
- Event store write fails or JSON file is corrupt.
- Audit append fails.
- Prediction freeze fails.
- Candidate pool may be too small or dominated by sources that rarely resolve.

Recoverable:

- Per-source failures are isolated.
- Per-candidate analysis failures are isolated.
- If event store write fails, audit and freeze are intentionally skipped for the batch. This prevents orphan data but stops that batch's sample creation.
- Audit failures do not block freeze.
- Freeze failures do not roll back event storage.
- Next discovery can retry only if the external condition is transient.

Observable:

- Partial. Source and per-candidate failures are logged.
- Persisted event count and predictions can be inspected after the fact.
- No persisted discovery job summary exists.
- No failure counter or dead-letter list exists for skipped candidates.
- No alert when discovery count is zero or freeze count is zero.

Can continue automatically:

- Yes when enough candidates pass evidence filtering and source/API/LLM calls work.
- Degraded automatically when one source fails.
- Cannot automatically recover from persistent model/API configuration errors except by producing logs.

Production assessment:

- P0 for unattended operation if no durable daily run result exists.
- P1 for calibration quality: skipped zero-evidence candidates and source outages can bias the sample set without a visible quality gate.

### 3. Event

Implementation: `backend/app/memory/event_store.py`, model validation in `backend/app/models/event.py`.

Data created:

- Durable event entries keyed by deterministic `event_id` derived from event question text.
- `first_seen`, `last_updated`, and full event record.
- User tracking defaults and preserved tracking state.
- Outcome and calibration fields after resolution.

Data persisted:

- JSON event store at `EVENT_STORE_FILE`.
- Existing `tracking`, `outcome`, and `calibration` are preserved across re-discovery.
- Writes are atomic and strict read-modify-write paths abort on corrupt JSON.

What can fail:

- Corrupt JSON can block writes.
- File I/O errors can block writes.
- Deterministic question-hash identity can split the same real event if wording changes materially.
- Re-discovery updates the event record but prediction freeze remains first-commit only.
- JSON store has no relational constraint tying event rows to prediction/link rows.

Recoverable:

- Missing file is recoverable.
- Corrupt JSON is quarantined and write aborts, but there is no automatic repair.
- Wording drift is not automatically merged unless source IDs/linking compensate later.

Observable:

- Store corruption is logged.
- Event list and detail APIs expose current records.
- No store health endpoint reports corruption, row count changes, or write failures.

Can continue automatically:

- Yes if the JSON store remains valid and writable.
- No if the event store becomes corrupt or unwritable; discovery will stop persisting new events.

Production assessment:

- P1 for calibration quality: question-hash event identity can fragment samples across equivalent market wording.
- P2 for maintenance: JSON event store plus SQLite loop store creates cross-store consistency work.

### 4. Verified Link

Implementation: `backend/app/memory/event_market_link_store.py`, used by `backend/app/services/event_resolve_service.py`.

Data created:

- Event-to-market link rows with event id, platform, contract id, market question, resolution criteria, method, confidence, verified flag, and timestamp.
- Pending unverified links for fuzzy matches below threshold.

Data persisted:

- SQLite table `event_market_links` in `LOOP_DB_FILE`.
- Unique key on `(event_id, contract_id)`.
- Indexes on event and contract.
- Manual resolution inserts a verified manual provenance link.
- Auto-resolve inserts auto links; exact matches can verify automatically.

What can fail:

- Source adapters may not supply stable `id` / `contract_id`.
- Default `AUTO_VERIFY_THRESHOLD=1.0` means only exact normalized question matches auto-verify.
- Fuzzy but correct matches become pending and require human verification.
- The route comment says pending links are verified through `POST /events/{event_id}/link/verify`, but actual mounted prefix is `/api/events/...`; this is documentation/API discoverability friction, not a loop logic failure.
- Resolution criteria stored on auto link is event-side criteria only; matched market's own criteria is not fetched yet.
- Multiple verified links for one event can exist; `get_verified_link` picks newest.

Recoverable:

- Pending fuzzy links are recoverable by manual verification.
- Exact contract-id resolution is robust after a verified link exists.
- Missing/unstable contract IDs are not automatically recoverable.

Observable:

- Pending links are visible through `/api/events/links/pending`.
- Verified links can be inspected only through store helpers; no full operational link-quality dashboard.
- No alert if pending links grow indefinitely.

Can continue automatically:

- Yes for already-verified contract links and exact auto matches.
- No for fuzzy matches below threshold; the loop intentionally stops until human verification.

Production assessment:

- P0 for 90-day unattended accumulation: pending links require humans. If exact matches are not frequent enough, resolved prediction accumulation stalls.
- P1 for calibration quality: missing market-native resolution criteria can allow semantically stale links to be trusted if question text/contract id is not enough.

### 5. Freeze Prediction

Implementation: `backend/app/memory/prediction_store.py`.

Data created:

- One committed prediction per event:
  - event id
  - contract id
  - platform
  - base-rate category
  - AI probability
  - market probability
  - raw edge
  - trust
  - adjusted edge
  - liquidity/volume
  - decision `act` / `watch` / `skip`
  - diagnosis inputs
  - created timestamp
  - status `open`

Data persisted:

- SQLite table `predictions` in `LOOP_DB_FILE`.
- `event_id` is unique.
- First freeze wins through `ON CONFLICT(event_id) DO NOTHING`.
- Later probability/edge movement is persisted in audit snapshots, not in prediction rows.

What can fail:

- Non-market events are intentionally skipped.
- Missing source type, contract id, AI probability, or market probability causes no prediction.
- SQLite write can fail.
- Freeze failure is logged but event remains stored.
- First-commit-only design can freeze a weak early estimate and ignore later stronger evidence in the prediction ledger.
- If all categories remain below `CALIBRATION_FEEDBACK_MIN_SAMPLES`, decisions are mostly `watch`, so act-only headline calibration may remain empty for a long time.

Recoverable:

- Re-running discovery can retry freeze only if no prediction exists.
- If an event was stored but freeze failed, the next discovery can freeze it if the record is reprocessed as fresh. If it comes from cache or is not reselected, recovery is uncertain.
- Bad first freeze is not corrected automatically.

Observable:

- Recent predictions API exposes frozen rows.
- Freeze failures are logs only.
- No metric compares market events discovered vs predictions frozen.

Can continue automatically:

- Yes for market-derived records with contract IDs and valid probabilities.
- Degraded if source adapters omit required fields.

Production assessment:

- P1 for calibration quality: one-event-one-prediction freezes first sight only; later material evidence changes are not committed as new prediction samples.
- P1 for bootstrapping: act-only scoring plus dormant category gating can produce little or no headline calibration until enough watch observations qualify segments.
- P2 for maintenance: prediction history semantics are split between SQLite commitment rows and JSONL audit trajectories.

### 6. Resolve Outcome

Implementation: `backend/app/services/event_resolve_service.py`.

Data created:

- Outcome dict with status, actual outcome, confidence, resolved timestamp, source, and notes.
- Event calibration snapshot for status `resolved`.
- Audit outcome snapshot.
- Prediction terminal status:
  - `scored` for `act`
  - `observed` for `watch` / `skip`
  - `voided` for non-genuine outcomes

Data persisted:

- Outcome and calibration onto event store JSON.
- Outcome snapshot to JSONL audit log.
- Prediction score/outcome/status to SQLite.
- Auto link rows to SQLite for fallback matches.

What can fail:

- Resolved source fetch fails.
- Resolved source returns no stable contract id.
- Fuzzy match below threshold creates pending link and does not resolve.
- `actual_outcome` can be non-numeric from source data; route validation protects manual API but auto path relies on source adapter quality and downstream numeric handling.
- Event store write can fail.
- Audit outcome append can fail after event store update.
- Prediction scoring can fail after event resolution, creating cross-store inconsistency.
- Already linked events whose contract is not in the resolved feed do not fall back to text matching, by design.

Recoverable:

- Source failures are isolated and next run retries.
- Unverified links are recoverable by manual verification.
- If event was resolved but prediction scoring failed, auto-resolve will skip it later because outcome already exists; no automatic repair path is visible.
- If audit append fails after event store update, calibration can still exist but trajectory observability is incomplete.

Observable:

- Auto-resolve returns a summary when called manually.
- Scheduled auto-resolve only logs summary.
- Event records expose outcome/calibration.
- Prediction calibration exposes scored rows.
- No persisted reconcile report for "event resolved but prediction still open" or "outcome written but audit missing."

Can continue automatically:

- Yes for exact/verified links and transient source failures.
- No for pending fuzzy links.
- No guaranteed automatic repair for partial cross-store write failures.

Production assessment:

- P0 for unattended operation: no automatic repair/reconciliation for partial event outcome vs prediction score inconsistency.
- P0/P1 depending on data mix: default exact-match-only verification can prevent many otherwise resolvable predictions from closing.

### 7. Calibration

Implementation:

- Event calibration math: `backend/app/services/calibration_service_event.py`
- Prediction calibration summary: `backend/app/memory/prediction_store.py`
- Event calibration API: `/api/events/calibration`
- Prediction calibration API: `/api/events/predictions/calibration`

Data created:

- Per-event Brier, skill score, grade, estimated probability, actual outcome, trajectory observation count, and trajectory span.
- Prediction Brier and actual outcome.
- Aggregates by source/category and overall prediction scorecard.

Data persisted:

- Event calibration is persisted in the event JSON record.
- Prediction Brier/outcome/status is persisted in SQLite.
- Aggregate summaries are computed on read and not persisted.

What can fail:

- Event calibration scores latest probability from audit trajectory, while prediction scoring scores frozen first-commit probability. These are different calibration surfaces.
- Missing audit snapshots make event calibration fall back to record baseline, which may be lower quality than intended.
- Void/invalid outcomes are excluded, correctly, but high rates of exclusions can hide source/link quality problems.
- Act-only headline prediction calibration can remain `no_data` if the decision gate rarely produces `act`.
- Category trust uses act+watch observed rows; headline uses act-only. This is intentional but easy to misinterpret operationally.

Recoverable:

- New resolved samples improve aggregates automatically.
- Missing/incorrect past calibration is not automatically recomputed unless event is manually repaired.

Observable:

- Calibration APIs expose aggregate state.
- No trend over time, run-level deltas, confidence intervals, or minimum viable sample warnings beyond `n`.
- No alert when calibration remains `no_data` for too long.

Can continue automatically:

- Yes after predictions resolve.
- Feedback only becomes meaningful once enough qualified samples accumulate.

Production assessment:

- P1 for calibration quality: two different scoring surfaces can confuse decision-making unless explicitly monitored.
- P1 for unattended learning: no guardrail alerts when `n` does not increase.

### 8. Trust

Implementation:

- Diagnosis trust in `backend/app/services/diagnosis_service.py` as called by `prediction_store.freeze_prediction`.
- Category skill in `prediction_store.segment_skill`.
- Optional probability feedback in `calibration_feedback_service.adjust_probability`, gated by `CALIBRATION_FEEDBACK_ENABLED`.

Data created:

- At freeze time:
  - category sample count
  - segment skill
  - trust
  - liquidity factor
  - adjusted edge
  - qualified flag
  - decision
- Optional calibration feedback metadata:
  - component weights
  - shrinkage
  - fused probability
  - sample count

Data persisted:

- Frozen trust/diagnosis fields are persisted in the prediction row.
- Optional calibration feedback info is stored in event record when enabled.
- Trust aggregate itself is computed on demand from resolved predictions.

What can fail:

- Trust is stale by design once frozen; later resolved samples do not update existing open predictions.
- If categories do not accumulate enough resolved act/watch samples, trust remains dormant.
- If liquidity is missing or malformed, liquidity factor may not represent true execution quality.
- Optional probability feedback is default-off, so the closed-loop learning may affect only future decision gating, not probability estimates, unless configured.

Recoverable:

- Future predictions benefit from newly resolved samples.
- Existing frozen decisions are not automatically re-evaluated.
- Enabling calibration feedback later can affect future discoveries.

Observable:

- Decision reports expose diagnosis fields.
- Prediction rows expose trust and segment fields.
- No operational dashboard shows dormant categories, trust changes, or category readiness.

Can continue automatically:

- Yes, but learning is slow and only affects future freeze decisions.
- No automatic redecision for open predictions after trust improves.

Production assessment:

- P1 for calibration/decision quality: trust is frozen and not re-applied to open opportunities after new outcomes arrive.
- P2 for maintenance: trust source-of-truth spans prediction rows, aggregate SQL, and optional event-level feedback metadata.

### 9. Decision Report

Implementation: `backend/app/services/decision_report_service.py`, exposed through `/api/events/decisions/open` and `/api/events/{event_id}/decision`.

Data created:

- Human-readable report joining a frozen prediction with current event record:
  - event summary
  - probability
  - market view
  - raw/adjusted edge
  - trust
  - diagnosis reason
  - confidence
  - recommendation
  - risk
  - category
  - status

Data persisted:

- None. Reports are assembled on read.
- Underlying event, prediction, and diagnosis fields are persisted elsewhere.

What can fail:

- Missing event record yields a minimal report from prediction only.
- Event record probability may be newer than frozen prediction market view; report includes both but can be misread.
- Reports do not show verified link status, outcome eligibility, or pending link blockers.
- Open report list excludes `skip` and closed statuses.

Recoverable:

- If event record reappears or prediction exists, report assembles.
- Missing link/provenance visibility requires feature work.

Observable:

- Good for human review of open opportunities.
- Weak for production loop health.
- Does not answer "why has this event not resolved yet?"

Can continue automatically:

- Report generation is not required for the loop to run.
- It can continue as long as underlying stores are readable.

Production assessment:

- P2 for maintenance/operations: decision reports are useful but not a loop health report.

## P0 Issues That Stop The Loop

1. No durable scheduler/run-state observability.

The scheduler catches exceptions and logs them, but does not persist run attempts, success/failure, counts, duration, or last error. A system can run for 90 days while discovering zero events or resolving zero predictions without an API-visible failure state.

2. Fuzzy link resolution is intentionally human-gated.

Default `AUTO_VERIFY_THRESHOLD=1.0` means exact normalized matches can auto-resolve, but fuzzy matches become pending links and require manual verification. This is correct for calibration integrity, but it means the system is not fully unattended unless exact/contract-linked resolution is sufficient in practice.

3. No automatic reconciliation after partial cross-store failures.

Resolution writes event outcome JSON, audit JSONL, and prediction SQLite. If event resolution succeeds but prediction scoring fails, later auto-resolve skips the event because it already has an outcome. There is no visible reconciler to close or score orphaned open predictions.

4. Event store corruption or persistent write failure blocks sample creation.

Strict durable writes correctly avoid overwriting corrupt data, but there is no automated repair, failover store, or alerting path.

## P1 Issues That Degrade Calibration Quality

1. First-freeze-only prediction semantics.

The prediction row is frozen on first sight and never updated. Later material evidence changes are captured in audit snapshots, but not as new committed prediction samples. This limits learning about timing and edge decay.

2. Event calibration and prediction calibration score different things.

Event calibration scores the latest probability trajectory. Prediction calibration scores the frozen commitment. Both are valid, but mixing them without explicit operational separation can lead to wrong conclusions.

3. Exact-match auto verification may bias resolved samples.

If only exact text matches auto-resolve, calibration will overrepresent clean/stable market wording and underrepresent renamed or semantically similar markets.

4. Market-native resolution criteria are not captured on auto links.

The link stores event-side resolution criteria, not the resolved market's own criteria. This weakens later audits of whether the prediction and outcome truly answer the same question.

5. Trust/decision is frozen and not recomputed for open predictions.

New resolved samples can improve segment skill, but open decisions created before that learning keep old trust and verdict.

6. No alert when calibration sample count fails to grow.

The APIs expose `n`, but the system does not detect stalled accumulation.

## P2 Issues That Create Future Maintenance Cost

1. Cross-store consistency burden.

Event records live in JSON, audit snapshots in JSONL, and links/predictions in SQLite. This is workable at small scale but requires reconcile tooling for production.

2. Operational state is logs-first.

Logs are useful for developers, but production operation needs queryable job runs, source failures, queue sizes, and sample deltas.

3. Report surface is not a health surface.

Decision reports explain opportunities, but not link blockers, unresolved age, source coverage, or why a prediction has not closed.

4. Event identity is question-hash based.

Question wording drift can fragment one real-world event into multiple event ids unless source contract identity is consistently available early.

5. Compacted audit log bounds file size but creates historical-analysis ambiguity.

Keeping recent snapshots is pragmatic, but long-horizon trajectory analysis over 90 days may need a separate durable rollup.

## 90-Day Unattended Operation Answer

Can this system run unattended for 90 days and continuously accumulate resolved predictions?

No, not with production confidence.

The implementation can run scheduled discovery and auto-resolution, and it can automatically accumulate resolved predictions when market-derived events have stable contract IDs or exact verified matches. However, the system is not yet safe to leave unattended for 90 days because:

- there is no durable job/run health record;
- fuzzy links require manual verification;
- partial write failures across event store, audit log, and prediction DB are not reconciled automatically;
- stalled discovery/resolution/calibration growth is not alertable;
- calibration quality can silently skew toward easy exact-match outcomes.

The right production target is not "never fail"; it is "fail visibly, retry safely, and reconcile automatically." The current loop retries some transient failures and preserves calibration integrity with fail-closed link handling, but it does not yet provide enough observability or repair automation to guarantee continuous unattended accumulation.

## Minimum Production Readiness Bar

Before calling this 90-day unattended:

1. Add a durable `loop_runs` table for scheduler job attempts with job id, started_at, finished_at, status, counts, error, and duration.
2. Add a reconciliation job that detects:
   - resolved event with open prediction;
   - scored prediction whose event lacks outcome;
   - pending links older than threshold;
   - market event without frozen prediction;
   - discovery/resolve count zero for N consecutive runs.
3. Add an API health endpoint for loop status:
   - last discovery success;
   - last auto-resolve success;
   - unresolved count;
   - open prediction count;
   - resolved prediction count delta;
   - pending link count;
   - calibration `n` delta.
4. Decide the unattended link policy:
   - keep exact-only and accept lower accumulation;
   - lower threshold with stronger semantic/resolution-criteria validation;
   - or add an automated verification model with fail-closed confidence bands.
5. Persist source-level discovery and resolved-fetch stats so API/model/source degradation is measurable.
6. Add a repair path for event outcome written but prediction not terminal.

Until those exist, this is a strong prototype loop and a reasonable supervised beta, not a 90-day unattended production loop.

---

# Data Model Perspective Review

Date: 2026-06-20  
Scope: actual implemented data model only. Implementation mechanics are ignored except where they define persistence semantics. Documentation claims are not treated as truth.

## Actual Data Model Implemented Today

The implemented model is a split-store feedback model:

- Event state is the mutable canonical record in `EVENT_STORE_FILE` JSON.
- Event probability trajectory is append-oriented JSONL in `EVENT_AUDIT_FILE`, with compaction.
- Market link identity and frozen predictions are canonical SQLite rows in `LOOP_DB_FILE`.
- Calibration exists in two separate semantic layers:
  - event calibration, attached to the mutable event record;
  - prediction calibration, attached to the frozen prediction row.
- Trust is not a first-class durable aggregate. It is computed from prediction history at freeze time, then copied into the prediction row as a frozen diagnosis snapshot.

This is not a single normalized relational model. It is a coordinated set of projections with implicit joins by `event_id`.

## 1. Event Semantics

Source of truth:

- The event record in `EVENT_STORE_FILE`.
- `event_id` is generated from the event question text.
- The event record contains title, summary, probability, credibility, impact, risk, evidence profile, source metadata, value score, report text, evidence items, tracking, legacy analysis, optional outcome, optional calibration, and optional semantics.

Mutable or immutable:

- Mutable.
- Re-discovery overwrites most event fields for the same `event_id`.
- `first_seen` is preserved.
- `last_updated` changes.
- `tracking`, `outcome`, and `calibration` are preserved when incoming records omit them.

Append-only or overwrite:

- Event record is overwrite-by-event-id.
- Probability history is append-oriented in audit JSONL, not in the event record.
- Audit compaction means the trajectory is not strictly permanent append-only over the full lifetime.

Invariants:

- `event_id` must exist and key the store.
- Event record must validate against `EventRecord`.
- Event has exactly one current mutable record.
- Outcome/calibration should not be erased by later discovery.
- Tracking is user-owned and preserved across re-scans.

Downstream assumptions:

- `event_id` is stable enough to join event, prediction, link, audit, and reports.
- `event_title` is the matching text for unresolved auto-resolution.
- `record["probability"]` is the latest event estimate.
- `record["source"]` carries enough market metadata to freeze a prediction.
- `record["legacy_analysis"]` carries base-rate category and other analysis fields needed by later services.
- `record["semantics"]` is useful but optional.

Semantic inconsistencies:

- Event identity is question-hash based, not contract/entity based. The same real-world event can become multiple event ids if wording changes.
- The current event probability is mutable, but the frozen prediction probability is immutable. Reports show both, which are semantically different.
- `legacy_analysis` remains a load-bearing part of the actual data model despite being named legacy.
- Event semantics are LLM-derived free text and raw entities; they are not canonical domain entities.

Hidden coupling:

- `event_id` is the implicit foreign key across JSON, JSONL, and SQLite.
- Prediction freeze depends on `source.type == "prediction_market"` and `source.source_id`.
- Calibration and trust depend on fields nested under `legacy_analysis`.
- Auto-resolution depends on `event_title` text unless a verified contract link already exists.

Violated or weak invariants:

- No database-enforced relation exists between an event record and its prediction/link rows.
- No invariant guarantees every market-derived event has exactly one prediction.
- No invariant guarantees every resolved event has a terminal prediction.
- No invariant guarantees the event's semantic identity equals the market contract's resolution identity.

Future migration risks:

- Moving from question-hash ids to contract/entity ids will require re-keying event store, audit log, prediction rows, and links.
- Normalizing `legacy_analysis` into first-class columns will require backfill.
- Long-lived audit compaction may make historical event trajectory migration incomplete.

## 2. Prediction Semantics

Source of truth:

- SQLite `predictions` table in `LOOP_DB_FILE`.
- One row per `event_id`.
- It stores the committed AI-vs-market probability, raw edge, trust-weighted adjusted edge, decision, diagnosis fields, and terminal scoring fields.

Mutable or immutable:

- Partially immutable.
- Decision-time fields are intended immutable after first insert:
  - AI probability
  - market probability
  - raw edge
  - trust
  - adjusted edge
  - decision
  - liquidity/volume
  - diagnosis fields
- Resolution fields mutate later:
  - status
  - actual outcome
  - Brier score
  - resolved timestamp

Append-only or overwrite:

- Not append-only.
- It is one-row-per-event, first insert wins.
- Re-scans do not append a new prediction and do not update the existing one.
- Status updates overwrite the same row at resolution.

Invariants:

- `event_id` is unique.
- A prediction starts `open`.
- A genuine resolution changes:
  - `act` -> `scored`
  - `watch` / `skip` -> `observed`
- A non-genuine resolution changes open prediction to `voided`.
- Headline prediction calibration reads only `status='scored' AND decision='act'`.
- Segment trust reads `status IN ('scored','observed') AND decision IN ('act','watch')`.

Downstream assumptions:

- The prediction row is the honest point-in-time commitment.
- `ai_probability` is the prediction to score, not the mutable event record probability.
- `decision` determines whether the prediction counts as headline calibration.
- `base_rate_category` is the segment key for trust.
- `actual_outcome` is on the same 0-100 scale as probabilities.

Semantic inconsistencies:

- The model is called point-in-time, but it is not an append-only ledger. It is a single standing commitment per event.
- The audit log contains probability trajectory, but predictions do not contain prediction trajectory.
- `status` model comment says `open | scored`, while actual statuses include `observed` and `voided`.
- `decision` default allows legacy `"tracked"` even though current downstream logic expects `act|watch|skip`.

Hidden coupling:

- Prediction creation depends on event source shape, not a first-class MarketEvent model.
- Trust calculation depends on historical prediction rows before the current row is inserted.
- Decision reports join prediction to the mutable event record by `event_id`.
- Calibration summary semantics depend on hard-coded SQL filters, not an explicit calibration eligibility field.

Violated or weak invariants:

- No invariant guarantees the prediction's `contract_id` has a verified link at freeze time.
- No invariant guarantees event source contract id equals latest verified link contract id.
- No invariant prevents stale open predictions after partial resolution failure.
- No invariant ensures `decision` belongs to the modern enum.

Future migration risks:

- Moving to append-only prediction snapshots will break the unique `event_id` assumption and all downstream "get_prediction(event_id)" reads.
- Adding multiple predictions per event requires defining which row is the commitment, which rows are diagnostics, and how scoring maps to outcomes.
- Separating act/watch/skip calibration surfaces later will require careful status migration.

## 3. Outcome Semantics

Source of truth:

- Event outcome lives on the event record in `EVENT_STORE_FILE`.
- Prediction outcome/scoring fields live separately on the prediction row in SQLite.
- Outcome snapshots are also appended to audit JSONL.

Mutable or immutable:

- Event outcome is effectively mutable because resolving writes the event record field.
- There is no explicit write-once guard preventing a second resolution from overwriting an existing outcome through direct store calls.
- Auto-resolve skips records with an outcome, but the underlying model does not enforce immutability.

Append-only or overwrite:

- Event outcome is overwrite-in-record.
- Prediction status/outcome fields are overwrite-in-row.
- Audit outcome marker is append-oriented, with compaction retaining at most the latest outcome snapshot per event.

Invariants:

- Outcome status is expected to distinguish genuine `resolved` from non-genuine statuses such as `invalid` or `void`.
- Only `status == "resolved"` enters event calibration and prediction scoring.
- Non-resolved statuses close predictions as `voided`.
- Outcome uses 0-100 probability-shaped actual outcome, not boolean.

Downstream assumptions:

- `outcome.actual_outcome` is numeric 0-100.
- `outcome.status == "resolved"` means it is valid for Brier scoring.
- If event has an outcome, auto-resolve should skip it.
- If prediction is terminal, it should reflect the event outcome state.

Semantic inconsistencies:

- Outcome source of truth is duplicated: event record outcome and prediction row actual outcome can diverge.
- Outcome status is a free string in the model, not a constrained enum.
- Manual resolution creates a verified manual link, but manual link has no real market contract id; this is provenance, not the same semantic object as a market link.
- Audit outcome snapshot is not the source of truth, but some historical tooling reads audit as the event timeline.

Hidden coupling:

- Resolution logic must update three projections: event record, audit log, prediction row.
- Calibration assumes outcome and latest audit probability are semantically aligned.
- Auto-resolution assumes a verified contract link means the market outcome and event outcome are equivalent.

Violated or weak invariants:

- No transaction spans event JSON, audit JSONL, and prediction SQLite.
- No invariant guarantees event outcome and prediction actual outcome are identical.
- No invariant guarantees outcome status is one of the expected states.
- No invariant guarantees non-resolved event outcomes remain excluded everywhere downstream.

Future migration risks:

- Moving outcome into a normalized `outcomes` table will require reconciling event JSON outcomes, prediction outcome columns, and audit outcome markers.
- Supporting multiple markets or partial/void settlement rules will require richer outcome identity than current `actual_outcome` plus free-text notes.

## 4. Calibration Semantics

Source of truth:

- Event calibration source is the `calibration` field on the event record.
- Prediction calibration source is the prediction row's `brier_score`, `status`, `decision`, and outcome fields.
- Aggregate calibration is computed on read, not persisted.

Mutable or immutable:

- Per-event calibration is mutable through event record overwrite.
- Prediction calibration mutates once when an open prediction resolves.
- Aggregates are dynamic projections over current stored rows.

Append-only or overwrite:

- Event calibration is overwrite-in-record.
- Prediction calibration is overwrite-in-row.
- There is no append-only calibration ledger.

Invariants:

- Event calibration scores latest probability trajectory against actual outcome.
- Prediction calibration scores frozen AI probability against actual outcome.
- Event calibration includes all genuinely resolved events.
- Headline prediction calibration includes act-only scored rows.
- Segment trust calibration includes act+watch resolved rows and excludes skip.

Downstream assumptions:

- Event calibration answers "was the latest event estimate accurate?"
- Prediction calibration answers "was the committed action prediction accurate?"
- Segment skill can safely use watch rows to bootstrap trust.
- Skip rows should not influence trust or headline calibration.
- Brier scores are comparable across event and prediction surfaces because all use 0-100 probabilities.

Semantic inconsistencies:

- There are two valid but different calibration meanings under the same word:
  - latest-estimate calibration;
  - frozen-commitment calibration.
- Trust uses a third calibration surface: act+watch only by category.
- Event calibration category is pulled through `legacy_analysis`, while prediction category is a first-class SQLite column.
- Calibration feedback uses event records, while diagnosis trust uses prediction rows.

Hidden coupling:

- Trust logic depends on prediction calibration filters.
- Optional probability adjustment depends on event calibration components and event outcomes.
- Decision quality reporting depends on status/decision SQL conventions.
- Trend/audit availability affects event calibration but not prediction calibration.

Violated or weak invariants:

- No explicit semantic type distinguishes event calibration from prediction calibration.
- No invariant guarantees all calibration inputs were generated from the same probability snapshot.
- No invariant guarantees category labels are stable over time.
- No invariant prevents calibration aggregates from silently changing after data repair or overwrite.

Future migration risks:

- Introducing a first-class `calibration_samples` table will require selecting which surface each sample represents.
- Category taxonomy changes will require reclassification/backfill of both event records and prediction rows.
- If prediction snapshots become append-only, calibration eligibility must move from event-level to prediction-version-level.

## 5. Trust Semantics

Source of truth:

- There is no independent trust table.
- Trust for a new prediction is computed from historical prediction rows through `segment_skill(category)`.
- The resulting trust, adjusted edge, qualification flag, sample count, and segment skill are frozen into the prediction row.
- Optional probability feedback metadata can be stored on event records when enabled, but that is separate from diagnosis trust.

Mutable or immutable:

- Historical trust aggregate is mutable as more predictions resolve.
- Frozen prediction trust is immutable after freeze.
- Event-level optional calibration feedback is mutable with event record overwrite.

Append-only or overwrite:

- Trust aggregate is computed, not persisted.
- Frozen trust is overwrite-never after prediction insert.
- No trust-history append log exists.

Invariants:

- Dormant segment below minimum samples uses default dormant trust.
- Act requires a qualified segment.
- Trust is clamp(skill, 0, 1) once qualified.
- Liquidity factor reduces adjusted edge unless liquidity is unknown/non-positive, in which case factor is 1.0.
- Decision is derived from adjusted edge plus qualification.

Downstream assumptions:

- `base_rate_category` is a stable trust segment.
- Historical act+watch outcomes are enough to estimate segment skill.
- Watch rows are useful for trust bootstrapping.
- Frozen trust explains the decision report and should not be recomputed at read time.
- Liquidity is comparable enough for the current floor rule.

Semantic inconsistencies:

- Trust is both a historical aggregate concept and a frozen per-prediction field.
- Optional probability feedback and decision trust are both "calibration feedback" concepts but use different source data.
- Unknown liquidity is treated as full liquidity trust, which is a semantic assumption, not merely a missing-data default.
- `qualified` is derived from sample count at freeze time and can become stale while the prediction remains open.

Hidden coupling:

- Trust is coupled to prediction status taxonomy.
- Trust is coupled to category extraction from legacy analysis.
- Trust is coupled to liquidity units across platforms.
- Decision reports treat frozen trust as explanatory truth even if current segment trust has changed.

Violated or weak invariants:

- No invariant guarantees category labels are canonical.
- No invariant guarantees liquidity units are normalized across platforms.
- No invariant guarantees trust values can be reconstructed exactly later if thresholds/settings change.
- No invariant records which settings version produced a frozen trust decision.

Future migration risks:

- Changing trust thresholds, liquidity floor, or minimum samples will not explain older frozen decisions unless settings snapshots are stored.
- Introducing canonical categories will require trust backfill.
- Per-platform liquidity normalization will change adjusted edge semantics.
- A trust table or materialized trust history will need to reconcile computed historical trust with frozen prediction trust.

## Cross-Model Findings

### Semantic Inconsistencies

1. `event_id` claims to identify an event, but actually identifies a question string hash.
2. `calibration` means different things depending on whether the reader is looking at event records, prediction rows, trust, or optional feedback.
3. `outcome` exists in both event and prediction models without a single transactional source of truth.
4. `prediction` is a committed snapshot, not a prediction history ledger.
5. `trust` is computed from live historical aggregates but interpreted later as frozen explanation.
6. `legacy_analysis` is semantically core, not legacy.

### Hidden Coupling

1. `event_id` is the untyped join key across all stores.
2. Source adapter field names define whether a prediction can exist.
3. SQL filters define calibration eligibility.
4. `base_rate_category` is the segment key for trust, calibration, and decisions, but is not canonicalized.
5. Audit snapshots define event calibration's scored estimate, while prediction rows define prediction calibration's scored estimate.

### Violated Or Weak Invariants

1. No cross-store transaction guarantees event, outcome, prediction, audit, and link consistency.
2. No enforced one-to-one invariant exists between market event and prediction.
3. No enforced terminal-state invariant exists between resolved event and prediction status.
4. No enum constraints protect outcome status or prediction decision/status semantics.
5. No canonical event identity protects against wording drift.

### Future Migration Risks

1. Append-only prediction history will require a breaking schema and API change.
2. Normalized outcomes will require reconciliation across three current locations.
3. Canonical event/entity identity will require re-keying historical stores.
4. Trust reproducibility will require storing settings/model/category versions.
5. Category taxonomy changes will require backfilling event records, prediction rows, and trust aggregates.

## Final Data-Model Answer

The actual model today is:

- A mutable event profile keyed by question-hash `event_id`.
- A compacted append-style probability observation log keyed by that same `event_id`.
- A one-row frozen prediction commitment per event, not an append-only prediction ledger.
- A separate event-to-market link table where verified contract identity is optional at freeze time but required for trusted automatic settlement.
- An outcome stored primarily on the event record and duplicated into prediction rows when scoring succeeds.
- Two calibration surfaces:
  - latest event estimate vs outcome;
  - frozen prediction commitment vs outcome.
- A trust system computed from resolved prediction rows and then frozen into future prediction rows.

The design is coherent enough for a supervised feedback loop, but it is not yet a clean domain model. Its main semantic debt is that identity, outcome, calibration, and trust are projections stitched together by convention rather than enforced model boundaries.

---

# Operational Resilience Review

Date: 2026-06-20  
Scope: LLM API outages, Polymarket outages, RSS failures, scheduler restart, process crash, disk write failure, and partial persistence.

## Executive Resilience Verdict

The system is resilient in the narrow sense that many failures are isolated and do not crash the process. It is not resilient in the production sense of automatically catching up, proving completeness, and repairing partial state.

Most external-source failures degrade to empty inputs plus logs. Scheduler failures retry on the next scheduled run but do not backfill missed windows. Persistence is safer than a naive file write because JSON writes are atomic and SQLite writes are transactional per table, but the loop has no transaction across event JSON, audit JSONL, and prediction/link SQLite. This creates partial-persistence risk.

## Recovery Paths

Current recovery paths:

- LLM outage: `analyze_market` catches provider errors and uses deterministic fallback analysis.
- RSS/GNews failures: source adapters return empty lists and log warnings.
- Manifold/Kalshi/Polymarket source failures: candidate or resolved fetches return empty lists or are isolated by `asyncio.gather(..., return_exceptions=True)`.
- Scheduler job exception: caught and logged; next scheduled run can try again.
- Process restart: scheduler starts again from FastAPI lifespan; stores are file-backed and survive.
- JSON corrupt read on durable write path: write aborts instead of overwriting with empty data.
- SQLite write failure: individual write rolls back.

Missing recovery paths:

- No durable retry queue.
- No missed-run catch-up after scheduler downtime.
- No backfill job for lost discovery windows.
- No reconciler for partial event/outcome/prediction persistence.
- No source-level circuit breaker or outage state.
- No automated repair for corrupt durable JSON.
- No operator-visible run ledger.

## Retry Behavior

Current behavior:

- There is no explicit retry with backoff for LLM, market APIs, RSS, GNews, or disk writes.
- External fetches generally make one attempt per scheduled run.
- Failed sources are skipped for that run.
- Failed discovery candidates are skipped for that run.
- Failed auto-resolution tries again only if the event remains unresolved and the next run fetches the relevant resolved market again.
- Scheduler coalesces missed executions and has a 5-minute misfire grace. Beyond that, missed jobs are dropped.

Operational implication:

- Transient outages shorter than one run can recover on the next run.
- A 24-hour outage loses that day's discovery window and potentially that day's resolution opportunities unless the resolved-market endpoint still returns those settled markets later.
- There is no guarantee of replaying what should have happened during the outage.

## Idempotency

Strong or acceptable idempotency:

- Event save is upsert by `event_id`.
- Prediction freeze is idempotent by `UNIQUE(event_id)` and first-write-wins.
- Link upsert is idempotent by `(event_id, contract_id)`.
- Prediction scoring is idempotent for an already-terminal prediction because it only scores `status='open'`.
- Auto-resolve skips events that already have an outcome.

Weak idempotency:

- Audit `record_event` appends a new probability snapshot each fresh discovery; repeated forced discovery creates multiple snapshots.
- Manual resolution can write another outcome unless blocked at route or domain level; the underlying store does not enforce write-once outcome semantics.
- A crash between event outcome write and prediction score creates a state that later auto-resolve will skip because the event already has an outcome.
- Retrying a failed partial batch is not transactionally idempotent across all stores.

## Duplicate Processing

Duplicate controls:

- Candidate dedupe runs before analysis.
- Event store upserts by deterministic question-hash `event_id`.
- Prediction freeze refuses duplicate event predictions.
- Link table refuses duplicate `(event_id, contract_id)` links.

Remaining duplicate risks:

- Same real-world event with changed wording becomes a new `event_id`.
- Same market appearing through multiple source adapters can be deduped only if candidate dedupe recognizes it semantically.
- Audit snapshots can duplicate similar observations across repeated scans.
- Pending links can accumulate for semantically similar but not identical contracts.

Severity:

- Usually P1 for calibration quality and operational noise.
- Can become P0 if duplicate event ids split resolution and prevent prediction scoring.

## Data Corruption Risk

Lower-risk areas:

- JSON writes use atomic temp-file replacement.
- Durable event writes use strict JSON read before write; corrupt JSON aborts write instead of replacing data with empty fallback.
- SQLite writes are transactional within the SQLite file.
- SQLite write lock serializes in-process writes.

Higher-risk areas:

- No cross-store transaction spans event store, audit log, predictions, and links.
- Audit JSONL can contain corrupt individual lines; readers skip bad lines, which avoids crashes but silently loses observations.
- Event cache uses lenient reads, appropriate for cache but not authoritative.
- Compaction rewrites audit history and may drop older trajectory data by design.
- Disk full or permission errors can leave one store updated and another not.

Severity:

- Cross-store partial persistence is P0 for loop correctness.
- Audit line loss is P1 for trajectory/event calibration quality.
- Cache corruption is P2.

## Backfill Capability

Implemented today:

- Auto-resolve can scan all stored unresolved events and match them against currently fetched resolved markets.
- If a resolved-market API returns historical settled markets after an outage, some missed resolutions can be recovered.
- Manual resolution can fill outcomes one event at a time.

Not implemented:

- No discovery backfill for missed market candidates during outage.
- No persisted source cursor, market created_at cursor, or resolved_at cursor.
- No LLM analysis retry backlog.
- No automatic backfill for failed prediction freezes.
- No reconciliation backfill for event outcome written but prediction not scored.
- No automated pending-link verification/backfill.

Severity:

- P0 for 24-hour unattended recovery if outage covers discovery or persistence.
- P1 if outage affects only one source and other sources continue.

## Risk Register

### LLM API Outage

Detection method:

- Warning logs: "LLM analysis failed, using deterministic fallback".
- Indirectly visible through lower-quality confidence/evidence-derived outputs.
- No durable outage counter or health endpoint.

Recovery method:

- Automatic fallback produces an analysis instead of failing the event.
- Next scheduled discovery can call the LLM again.
- No replay of events analyzed during outage with fallback.

Severity:

- P1. The loop continues, but calibration quality degrades because fallback predictions can be frozen as real commitments.

### Polymarket Candidate-Source Outage

Detection method:

- Source warning logs or zero Polymarket candidates.
- No persisted source-level count.

Recovery method:

- Discovery continues with other sources.
- Next scheduled run retries.
- No candidate backfill for markets that were only visible during the outage window.

Severity:

- P1 if Manifold/Kalshi provide enough coverage.
- P0 for Polymarket-dominant deployments because no new Polymarket predictions are frozen.

### Polymarket Resolved-Source Outage

Detection method:

- Warning logs from resolved fetch.
- Auto-resolve summary may show low `checked_count`, but scheduled summary is only logged.

Recovery method:

- Auto-resolve retries next day.
- Recovery depends on Polymarket historical resolved endpoint still returning the markets within the later fetch window.
- No cursor-based backfill.

Severity:

- P0/P1. If resolved markets roll out of the top-volume fetch window before recovery, outcomes are missed indefinitely.

### RSS / Official Feed Failure

Detection method:

- Source collection warning logs for shared sources.
- For individual RSS feeds, failures can collapse to empty feed results without durable metrics.

Recovery method:

- Other sources continue.
- Next run retries.
- No article backfill or source cursor.

Severity:

- P1. Discovery and analysis continue but evidence quality drops; candidates with zero selected evidence may be skipped.

### GNews Failure

Detection method:

- Warning logs from `gnews fetch failed`.
- No per-query failure persistence.

Recovery method:

- Shared articles may still provide evidence.
- Next run retries.
- No per-event retry backlog.

Severity:

- P1. Candidate analysis quality degrades or candidate is skipped due to no selected evidence.

### Scheduler Restart

Detection method:

- Startup logs.
- No durable scheduler state.

Recovery method:

- FastAPI lifespan starts scheduler.
- Jobs resume future cron executions.
- Missed jobs beyond 5-minute misfire grace are dropped.

Severity:

- P0 for unattended 24-hour outage recovery because missed discovery/resolve jobs are not replayed.

### Process Crash During Discovery

Detection method:

- Process logs / external supervisor only.
- No in-app incomplete-run marker.

Recovery method:

- Restart resumes scheduler.
- Stored events/predictions before crash remain.
- In-flight candidates are lost.
- Next discovery may rediscover some candidates, but no guaranteed replay.

Severity:

- P1 normally.
- P0 if crash happens repeatedly or during the only daily discovery window.

### Process Crash During Resolution

Detection method:

- External supervisor/logs.
- No persisted run phase.

Recovery method:

- Unresolved events can be retried next run.
- If crash occurs after event outcome write but before prediction scoring, event is no longer retried by auto-resolve.
- No automatic reconciliation exists.

Severity:

- P0 due to possible terminal cross-store inconsistency.

### Disk Write Failure

Detection method:

- Exceptions and logs when writes fail.
- No health endpoint for disk writability or free space.

Recovery method:

- JSON durable write aborts safely.
- SQLite write rolls back.
- Next run retries if condition resolves.
- No queued retry for the specific failed record.

Severity:

- P0. Persistent disk failure stops durable accumulation; transient disk failure can create partial persistence depending on stage.

### Partial Persistence

Detection method:

- Manual comparison across stores:
  - event has outcome but prediction remains open;
  - market event has no prediction;
  - link exists but event missing;
  - audit missing outcome marker.
- No automated detector.

Recovery method:

- Manual repair or custom script.
- Some cases may self-heal only if the event remains eligible for later processing.

Severity:

- P0. This directly corrupts the loop's ability to accumulate resolved predictions.

### Duplicate Processing

Detection method:

- Duplicate-like event titles, multiple event ids for same market, repeated audit snapshots, pending link growth.
- No canonical duplicate report.

Recovery method:

- Event upsert prevents exact duplicate event id.
- Prediction/link uniqueness prevents some duplicate rows.
- Semantic duplicates require manual cleanup or future identity migration.

Severity:

- P1. It biases calibration and fragments outcomes; can become P0 when duplicate identity prevents resolution.

### Data Corruption

Detection method:

- JSON decode errors are logged.
- Corrupt event JSON on write path raises and aborts.
- Audit corrupt lines are skipped silently by readers.

Recovery method:

- Corrupt JSON is copied to `.corrupt`, but no automated restore exists.
- Audit line corruption is skipped; lost observations are not reconstructed.
- SQLite corruption has no application-level recovery path.

Severity:

- P0 for event store or SQLite corruption.
- P1 for audit corruption.

## Can The System Recover Automatically After A 24-Hour Outage?

No, not reliably.

It can restart and resume future scheduled runs. It can also resolve some previously stored unresolved events if the resolved-market APIs still return the relevant settled markets after the outage. But it cannot guarantee automatic recovery from a 24-hour outage because:

- missed scheduled jobs are dropped, not replayed;
- discovery has no backfill cursor for markets or evidence;
- resolved-market fetches are bounded by limit/order and may miss settlements that fell out of the returned window;
- LLM fallback outputs are not marked for later re-analysis;
- partial persistence is not automatically detected or repaired;
- run success/failure is not durably recorded.

The system is restart-tolerant, but not outage-recovering.

## Minimum Resilience Work Required

1. Add durable job-run records with started/finished/status/counts/errors.
2. Add per-source fetch metrics and last-success timestamps.
3. Add cursor-based discovery and resolved-market backfill.
4. Add an LLM retry backlog for fallback-produced analyses that were frozen during provider outage.
5. Add reconciliation jobs for cross-store invariants.
6. Add disk health checks and write-failure alerts.
7. Add source-specific retry with bounded exponential backoff inside a run.
8. Add an operational endpoint that reports:
   - last successful discovery;
   - last successful auto-resolve;
   - source freshness;
   - pending link count;
   - open predictions older than threshold;
   - resolved events with nonterminal predictions;
   - calibration sample delta over 24h / 7d.

Until then, a 24-hour outage requires supervised recovery and manual data-quality inspection.
