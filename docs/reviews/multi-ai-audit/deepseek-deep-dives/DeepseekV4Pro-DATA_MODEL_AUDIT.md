# Data Model Audit — What the Code Actually Does

**Date:** 2026-06-20  
**Scope:** Event, Prediction, Outcome, Calibration, Trust — ground truth from implementation, not documentation  
**Method:** Read every `save_*`, `resolve_*`, `freeze_*`, `score_*`, `diagnose` call site and every model definition

---

## 1. The Actual Data Model

### 1.1 Event

```
Source of truth: event_store.json
Shape: { "<event_id>": { "first_seen": str, "last_updated": str, "record": {...} } }
event_id = SHA1(event_title)[:12]   ← computed in build_event_record(), NOT a UUID
```

| Property | Reality |
|----------|---------|
| **Mutable?** | **Yes.** Upsert on re-discovery overwrites `probability`, `credibility`, `impact`, `evidence`, `source`, `value_score`, `intelligence_report`, `evidence_items`, `event_summary`, `event_title_zh` |
| **Immutable fields** | `outcome`, `calibration`, `tracking` — preserved from existing record when incoming record lacks them (lines 73-80 of event_store.py) |
| **Append-only?** | **No.** The store is a mutable dict. Each `save_events` call does a full read → in-place dict merge → atomic write. |
| **Invariants** | (1) `EventRecord.model_validate()` gates every write — a malformed record raises, entire batch aborted. (2) `first_seen` never changes. (3) `outcome`/`calibration`/`tracking` are write-once. |
| **Validation gate** | Pydantic v2 `EventRecord.model_validate(record)`. `extra='allow'` on EventRecord means unknown fields pass through silently. |

**What happens on re-discovery:** The SAME event_id (same question text) is discovered again 3 days later. The new `EventRecord` carries fresh probability/credibility/evidence but no outcome/calibration. `save_events` overwrites probability/credibility/evidence with new values, preserves the old outcome/calibration/tracking. The record silently drifts over time — only the id is stable.

**Key downstream readers:**
- `auto_resolve_events()` → reads `record.get("outcome")` to skip resolved
- `list_resolved_events()` → reads `outcome.status == "resolved"`
- `freeze_prediction()` → reads `source.type`, `source.source_id`, `probability.estimated`, `probability.baseline`, `legacy_analysis.base_rate_category`
- `decision_report_service` → reads `probability`, `credibility`, `risk`, `intelligence_report`
- `calibration_feedback_service` → reads `calibration_components`, `calibration.brier_score`, `legacy_analysis.base_rate_category`, `outcome.actual_outcome`

---

### 1.2 Prediction

```
Source of truth: v2_loop.db → predictions table
Shape: One row per event_id (UNIQUE constraint)
```

| Property | Reality |
|----------|---------|
| **Mutable?** | **Two-phase.** Phase 1 (INSERT): immutable — `INSERT ... ON CONFLICT DO NOTHING`. Phase 2 (UPDATE): mutable at resolve — `UPDATE status, actual_outcome, brier_score, resolved_at` |
| **Append-only?** | **No.** One insert, one update. No version history in the table. No multi-snapshot rows. |
| **Invariants** | (1) `UNIQUE(event_id)` — one row per event, enforced by schema. (2) Only market-derived events get frozen (`source.type == "prediction_market"` with `contract_id`). (3) Status transitions: `open → scored` (if `decision='act'`), `open → observed` (if `decision ∈ {watch, skip}`), `open → voided` (if invalid). (4) `decision='tracked'` is a pre-M2 legacy default — exists only on old rows that predate the M2 migration. |
| **The commitment** | Row frozen at first sight. `INSERT ... ON CONFLICT(event_id) DO NOTHING` means re-scan is silent no-op — the first-sight verdict is permanent. |

**The frozen diagnosis fields:** `trust`, `adjusted_edge`, `liquidity_factor`, `qualified`, `segment_n`, `segment_skill` are all computed at `freeze_prediction()` time via `diagnose()` and frozen in the row. They reflect the calibration state AT FREEZE TIME. As more predictions in the same category get scored, `segment_skill()` changes, but the frozen row retains the OLD diagnosis.

**Key downstream readers:**
- `score_prediction()` → reads `status='open'` row, computes Brier from frozen `ai_probability`
- `void_prediction()` → reads `status='open'` row
- `segment_skill(category)` → reads `status IN ('scored','observed') AND decision IN ('act','watch')`
- `calibration_summary()` → reads `status='scored' AND decision='act'`
- `list_open_opportunities()` → reads `status='open' AND decision IN ('act','watch')`
- `decision_report_service` → reads `prediction` dict directly

---

### 1.3 Outcome

```
Source of truth: event_store.json → record["outcome"] (primary)
                  v2_loop.db → predictions.actual_outcome (secondary, derived)
                  event_audit.jsonl → kind="outcome" snapshot (audit trail)
                  Written in THREE places with NO transaction boundary.
```

| Property | Reality |
|----------|---------|
| **Mutable?** | **Write-once.** `resolve_event()` writes it. `save_events()` preserves it across re-scans (line 77-78). Never overwritten by discovery. |
| **Append-only?** | Yes for the record (single write). Yes for audit log (one `kind="outcome"` line). No for predictions table (UPDATE, not INSERT). |
| **Invariants** | (1) `status="resolved"` → admitted to calibration. `status="invalid"` → excluded from calibration, prediction is voided. (2) `actual_outcome` is 0-100 float. (3) `confidence` is 0-1 float. |
| **The write sequence** | `resolve_with_calibration()` writes outcome to event_store (line 130) → then appends to audit log (line 132) → then scores prediction (line 139). Three writes, no transaction. |

**What breaks if the third write fails:** The event_store has `outcome.status="resolved"`. The audit log has an outcome snapshot. The predictions table has `status='open'`. Auto-resolve skips this event (has outcome). The prediction is orphaned as 'open' forever. **No recovery path.**

**Key downstream readers:**
- `auto_resolve_events()` → `record.get("outcome") is not None` (skip gate)
- `list_resolved_events()` → `outcome.status == "resolved"`
- `score_prediction()` → reads the passed `actual_outcome` parameter (NOT from event_store)
- `calibration_feedback_service` → reads `outcome.actual_outcome`

---

### 1.4 Calibration

```
TWO INDEPENDENT CALIBRATION SIGNALS exist for the same event.
They use DIFFERENT probability estimates and are stored in DIFFERENT places.
```

#### Calibration A — Event-Level (on EventRecord)

| Property | Reality |
|----------|---------|
| **Source of truth** | `event_store.json → record["calibration"]` |
| **Computed from** | `score_event(latest_probability, actual_outcome, trajectory_*)` — the LATEST probability estimate from the audit trail (line 103 of event_resolve_service) |
| **Mutable?** | Write-once. Preserved across re-scans. |
| **Fields** | `brier_score`, `skill_score`, `grade`, `estimated_probability`, `actual_outcome`, `trajectory_observations`, `trajectory_span_hours` |
| **Read by** | `calibration_feedback_service` (component weighting, category shrinkage), `summarize()` (event-layer calibration endpoint) |

#### Calibration B — Prediction-Level (on predictions table)

| Property | Reality |
|----------|---------|
| **Source of truth** | `v2_loop.db → predictions.brier_score` |
| **Computed from** | `brier_score(frozen_ai_probability, actual_outcome)` — the FROZEN first-sight estimate (line 287 of prediction_store) |
| **Mutable?** | Written at resolve time via UPDATE. |
| **Aggregated by** | `calibration_summary()` → overall + by_category Brier. `segment_skill()` → per-category Brier for trust. |
| **Filter** | `calibration_summary()`: `status='scored' AND decision='act'`. `segment_skill()`: `status IN ('scored','observed') AND decision IN ('act','watch')`. |

**The split:** For the same event, Calibration A might score `estimated=72` (latest, improved by evidence) while Calibration B scores `ai_probability=65` (first-sight, frozen). Two different Brier scores. No reconciliation. No documentation of which is authoritative.

**Which one matters?**
- The feedback loop (`calibration_feedback_service`) reads Calibration A (event-level).
- The trust gate (`segment_skill → diagnose`) reads Calibration B (prediction-level).
- The calibration dashboard shows both but under different API endpoints (`/api/events/calibration` vs `/api/events/predictions/calibration`).

---

### 1.5 Trust

```
Source of truth: v2_loop.db → predictions table (frozen values)
                  Recomputable: segment_skill() + diagnose() (live computation)
```

| Property | Reality |
|----------|---------|
| **Mutable?** | **Frozen values are immutable** (written at freeze time, never updated). **Live values change** every time a prediction is scored. |
| **Append-only?** | No. Frozen once. New calls to `freeze_prediction()` get the live-computed value. |
| **Invariants** | (1) `trust ∈ [0, 1]`. (2) Dormant → `trust = DIAGNOSIS_DORMANT_TRUST` (0.5). (3) Qualified → `trust = clamp(skill_score(mean_brier), 0, 1)`. (4) Unqualified segment → `decision` capped at "watch" (never "act"). (5) `adjusted_edge = raw_edge × trust × liquidity_factor`. |
| **Frozen at** | `freeze_prediction()` calls `diagnose(raw_edge, segment_skill(category), liquidity)` → writes all diagnosis fields to row |

**The staleness problem:** A prediction frozen on day 1 (when its category was dormant, `trust=0.5`) keeps `adjusted_edge=raw_edge×0.5×liq` forever. By day 30, the same category has 8+ scored predictions and `segment_skill()` returns `trust=0.85`. But the day-1 prediction still shows the old `adjusted_edge`. `list_open_opportunities` ranks by `ABS(adjusted_edge)` — so a day-1 opportunity is ranked lower than a day-30 opportunity with the same raw edge, purely because of stale trust.

**What reads the frozen values:** `list_open_opportunities()` — the opportunity surface. `decision_report_service` — the decision report.

---

## 2. Semantic Inconsistencies

### SI-1: Event record is "write-once for resolution" but "last-write-wins for analysis"

The event_store documentation says outcome/calibration are write-once, implying immutability. But the record's `probability`, `credibility`, `evidence_items`, `intelligence_report` are silently overwritten on re-discovery. A re-scanned event has a different `probability.estimated` than when it was first saved. The frozen prediction was based on the first-sight estimate, but the event record now shows the latest estimate. No one reconciling these would know.

**Impact:** Someone reading an event record 30 days later sees a probability of 72, checks the prediction and sees `ai_probability=58`, and cannot tell whether this is a bug or re-scan drift.

### SI-2: Two Brier scores, two probability sources, one event

Calibration A (event-level): scores the LATEST audit-trail probability estimate.
Calibration B (prediction-level): scores the FROZEN first-sight estimate.

The calibration feedback loop reads Calibration A. The trust gate reads Calibration B. The feedback adjusts published probabilities based on event-level accuracy (latest estimate), but trust weights future edges based on prediction-level accuracy (first-sight commitment).

**Impact:** A category where the AI consistently corrects its estimates over time (low event-level Brier, high prediction-level Brier) gets misleading feedback: calibration feedback sees "good" and doesn't shrink, but trust sees "bad" and caps edges. The two signals work at cross-purposes.

### SI-3: `decision='tracked'` — invisible rows

The `predictions` table default for `decision` is `'tracked'`. The migration preserves `'tracked'` as a not-null default. The `Prediction` Pydantic model default is `'tracked'`. But `segment_skill()` filters `decision IN ('act','watch')`. `calibration_summary()` filters `decision='act'`. `list_open_opportunities()` filters `decision IN ('act','watch')`.

Any row with `decision='tracked'` is **invisible to every downstream consumer** except `get_prediction()` and `list_recent()`. It never contributes to calibration, never qualifies a segment for trust, never appears on the opportunity surface.

**Impact:** Pre-M2 rows (if any exist) are dead weight in the database. If a future code change accidentally sets `decision='tracked'`, the row silently vanishes from all aggregates.

### SI-4: `segment_skill()` and `calibration_summary()` read different populations

```
segment_skill():         status IN ('scored','observed')  AND  decision IN ('act','watch')
calibration_summary():   status = 'scored'                AND  decision = 'act'
```

A category with 3 scored act rows and 5 observed watch rows shows `n=8` in `segment_skill()` but `n=3` in `calibration_summary().by_category`. The trust gate considers the category qualified (n=8 ≥ 8), but the calibration dashboard shows only 3 scored predictions.

**Impact:** A user looking at the calibration dashboard wonders why a category with "only 3 scored" is showing "act" decisions. The reason (watch rows count for qualification but not calibration) is buried in code comments.

### SI-5: Frozen trust is not live trust

`list_open_opportunities` ranks by `ABS(adjusted_edge)` where `adjusted_edge` was computed at freeze time. A prediction frozen yesterday (dormant category, trust=0.5) has lower edge than an identical prediction frozen today (qualified category, trust=0.8). The opportunity surface is ordered by historical confidence, not current confidence.

**Impact:** A user scanning the opportunity surface sees opportunities ordered by a trust value that may no longer be accurate. The best opportunity (by live trust) might be buried on page 2 because its frozen trust was computed when the category was dormant.

---

## 3. Hidden Coupling

### HC-1: resolve_with_calibration crosses three stores with no atomicity

```
resolve_event(event_store)     ← write 1 (json file)
record_outcome(audit_log)      ← write 2 (jsonl append)
score_prediction(loop_db)      ← write 3 (sqlite update)
```

Three independent storage systems. No two-phase commit. No compensation logic. If write 3 fails after writes 1+2 succeed, the system enters an inconsistent state with no automatic recovery.

### HC-2: event_id is the universal join key, but has no referential integrity

`event_id` links records across `event_store.json`, `event_audit.jsonl`, `v2_loop.db.predictions`, and `v2_loop.db.event_market_links`. But there is no foreign key enforcement. An event can be deleted from event_store and its prediction row in SQLite remains — the decision report gracefully degrades with `record=None`, but no one knows the row is orphaned.

### HC-3: base_rate_category is stored in `legacy_analysis` dict

`freeze_prediction()` extracts the category from `record["legacy_analysis"]["base_rate_category"]` (line 204 of prediction_store). The `legacy_analysis` field is `dict[str, Any]` on EventRecord — completely untyped. If `base_rate_service.py` changes its taxonomy or the `legacy_analysis` key changes, `freeze_prediction` silently gets `"unknown"`.

### HC-4: calibration_feedback reads event_store, but trust reads predictions table

`calibration_feedback_service._load_resolved_records()` reads `list_resolved_events()` from event_store. `segment_skill()` reads from predictions table. These are different data sources, different filters, different probability estimates. The feedback loop adjusts published probabilities based on event-store calibration, but the trust gate weights edges based on prediction-table calibration. These two calibration signals are never compared or reconciled.

---

## 4. Violated Invariants

### VI-1: "outcome.status == 'resolved' ⇒ prediction.status ≠ 'open'"

**Status: VIOLATED**

The code in `resolve_with_calibration()` writes outcome to event_store (line 130) BEFORE scoring the prediction (line 139). If `score_prediction()` fails (DB error, process crash between writes), the outcome exists but the prediction stays open. Auto-resolve then skips this event (has outcome), so the prediction is never scored. No recovery.

### VI-2: "A scored prediction and its event record agree on actual_outcome"

**Status: NOT GUARANTEED**

`resolve_event()` writes `actual_outcome` to event_store. `score_prediction()` writes `actual_outcome` to predictions table. Same value is passed to both — but if one write succeeds and the other fails (VI-1), they diverge. Even without failure, no code verifies they match post-write.

### VI-3: "calibration_summary.n == segment_skill(category).n for the same category"

**Status: VIOLATED by design, but undocumented**

`calibration_summary` filters `decision='act'`. `segment_skill` filters `decision IN ('act','watch')`. They will ALWAYS show different n-values. The docstrings explain this, but the API contract doesn't — a consumer of both endpoints sees conflicting counts with no explanation.

---

## 5. Future Migration Risks

### MR-1: event_store.json has no per-record version marker

Every record in `event_store.json` is validated against the current `EventRecord` model. If the model adds a mandatory field in a future version, ALL existing records fail validation on next write. Migration requires: read entire file → transform every record → atomic write. No partial migration possible. No rollback.

### MR-2: The `decision` column default depends on a magic string

`decision` defaults to `'tracked'` in both SQL schema and Pydantic model. All current code paths set `decision` via `diagnose()` before INSERT. But if a future codepath inserts a row without setting `decision`, it silently becomes `'tracked'` — invisible to all aggregates. The `'tracked'` value has no semantic meaning in the current model but is the schema default.

### MR-3: Two calibration signals → which one survives a refactor?

If the system is refactored to have a single calibration concept, one of the two Brier computation paths must be chosen. The event-level path (latest estimate) gives better-looking numbers. The prediction-level path (first-sight estimate) gives honest commitment numbers. Choosing either breaks consumers of the other. There is no migration path documented.

### MR-4: `legacy_analysis` is an untyped dict

`base_rate_category`, `calibration_components`, and other fields are nested inside `record["legacy_analysis"]` — a `dict[str, Any]` with `extra='allow'` on the parent model. If `base_rate_service` restructures its output, `freeze_prediction` silently gets wrong categories. No type safety. No migration path.

### MR-5: SQLite migration uses structural detection, not versioning

`_migrate()` in `prediction_store.py` checks `PRAGMA table_info` for column existence and `PRAGMA index_list` for UNIQUE constraint. This works for additive changes but cannot detect: column type changes, removed columns, renamed columns, or intentional constraint removal. A future migration that renames `raw_edge` to `edge_raw` would silently create a duplicate column.

---

## 6. Summary

### What is the actual data model?

```
event_store.json:  Mutable dict. Last-write-wins for analysis fields; write-once for outcome/calibration/tracking.
                   event_id = SHA1(question_text)[:12]

v2_loop.db:
  predictions:     One row per event. Insert-once (first sight), update-once (resolve).
                   Frozen trust/diagnosis fields reflect state at freeze time, not live state.
  event_market_links: One row per (event_id, contract_id). verified flag gates scoring.

event_audit.jsonl: Append-only. Probability snapshots + outcome markers. Compacted at 5000 lines.

Two calibrations exist:
  A) Event-level:  latest probability estimate → Brier (on EventRecord, in event_store.json)
  B) Prediction-level: frozen first-sight probability → Brier (in predictions table, aggregated by calibration_summary)

Trust is frozen at freeze time. The opportunity surface uses stale trust values for ranking.
```

### Key findings

| Severity | Finding |
|----------|---------|
| **P0 data integrity** | `score_prediction` failure after `resolve_event` success creates permanently orphaned predictions. No automatic recovery. Three-store write with no atomicity. |
| **P1 semantic fork** | Two independent calibration signals (latest-estimate vs first-sight) with no reconciliation. Different consumers read different signals. |
| **P1 hidden staleness** | Frozen trust values never updated. Opportunity surface ranked by historical confidence, not current. |
| **P1 silent exclusion** | `decision='tracked'` rows invisible to all downstream consumers. Schema default is a magic string with no meaning. |
| **P2 divergence by design** | `segment_skill` and `calibration_summary` read different populations. Expected n-values differ. Not documented in API contract. |
| **P2 untyped coupling** | `base_rate_category` extracted from untyped `legacy_analysis` dict. No type safety. Silent "unknown" on schema change. |
| **P2 migration fragility** | No per-record version markers. Structural migration detection cannot handle renames or type changes. |

### The fundamental tension

The system has two competing design goals that create an unresolved tension:

1. **Commitment integrity** (the honest loop): Freeze predictions at first sight. Never recompute. Score what we actually believed at decision time. This is the prediction-level model.

2. **Estimate accuracy** (the best guess): Use the latest probability estimate for calibration. Score against the most informed view we had. This is the event-level model.

Both are valid. But having BOTH in the same system, feeding different consumers (feedback reads #2, trust reads #1), without explicit acknowledgment of the split, creates a semantic fault line that will widen as more consumers are added.
