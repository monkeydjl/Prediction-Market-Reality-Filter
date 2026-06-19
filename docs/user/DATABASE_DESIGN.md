# Prediction Market Reality Filter

# Database Design

Version: 2.0

---

# Design Principles

The database must support:

* Historical analysis
* Outcome tracking
* Calibration
* Model evaluation
* Market comparison
* Answering, per segment, "was the edge real or was it our error?"

Every prediction must be permanently stored.

Predictions are append-only and point-in-time. A committed prediction is never

updated; re-evaluation writes a new row. This is what keeps calibration honest.

---

# events

Stores discovered events.

CREATE TABLE events (

id UUID PRIMARY KEY,

title TEXT,

description TEXT,

category VARCHAR(50),

event_time TIMESTAMP,

created_at TIMESTAMP

);

---

# evidence

Stores extracted evidence.

CREATE TABLE evidence (

id UUID PRIMARY KEY,

event_id UUID,

source_name TEXT,

source_url TEXT,

content TEXT,

relevance_score FLOAT,

credibility_score FLOAT,

created_at TIMESTAMP

);

---

# probability_assessments

Stores AI predictions.

CREATE TABLE probability_assessments (

id UUID PRIMARY KEY,

event_id UUID,

model_version TEXT,

probability FLOAT,

confidence FLOAT,

reasoning JSONB,

created_at TIMESTAMP

);

---

# market_snapshots

Stores market state.

CREATE TABLE market_snapshots (

id UUID PRIMARY KEY,

event_id UUID,

market_name TEXT,

contract_id TEXT,

market_probability FLOAT,

liquidity FLOAT,

volume_24h FLOAT,

captured_at TIMESTAMP

);

---

# event_market_links

Binds an internal event to a specific market contract. The join the whole loop depends on.

CREATE TABLE event_market_links (

id UUID PRIMARY KEY,

event_id UUID,

market_name TEXT,

contract_id TEXT,

market_question TEXT,

resolution_criteria TEXT,

link_method VARCHAR(20),

link_confidence FLOAT,

verified BOOLEAN,

linked_at TIMESTAMP

);

market_question and resolution_criteria are the market's own words, stored so a

resolved outcome can later be checked to mean the same thing we predicted.

link_method is auto / manual. link_confidence is 0..1.

---

# Identity and Linkage Integrity

This is the highest-risk part of the schema, and the easiest to get silently wrong.

Calibration is honest only if event_id, contract_id, and outcome all refer to the

same question. Markets get renamed, split, partially resolved, or voided.

A wrong or stale link does not raise an error. It computes Brier against the wrong

truth and quietly corrupts the feedback loop the entire system depends on.

Rules:

* No prediction is scored unless its event_market_link is verified.
* An unverified or low-confidence link is fail-closed: excluded from calibration, not guessed.
* resolution_criteria is captured at link time and re-checked at resolve time.
* If the resolved question no longer matches the predicted question, the outcome is marked invalid.

A divergence is only an edge if the market resolves on the reality we measured.

---

# predictions

Core table. Append-only. One row per committed evaluation, frozen point-in-time.

CREATE TABLE predictions (

id UUID PRIMARY KEY,

event_id UUID,

probability_id UUID,

market_snapshot_id UUID,

ai_probability FLOAT,

market_probability FLOAT,

raw_edge FLOAT,

calibration_trust FLOAT,

adjusted_edge FLOAT,

decision VARCHAR(10),

signal VARCHAR(20),

created_at TIMESTAMP

);

ai_probability and market_probability are frozen copies, not recomputed.

decision is act / watch / skip. Only act rows are scored as live predictions.

## Two metric scopes, two populations (implementation note)

These are deliberately different and must not be collapsed into one filter. The
implementation (`prediction_store.py`) marks a resolved prediction `scored` when
its decision was `act`, and `observed` when it was `watch` / `skip` (outcome and
Brier recorded for diagnostics, kept out of the headline calibration). A
non-genuine resolution (identity conflict -> invalid, or a void market) closes
the open row as `voided` (no Brier, off the opportunity surface). One prediction
per event (`UNIQUE(event_id)`): the commitment is frozen at first sight and a
re-scan never overwrites or re-snapshots it. Terminal statuses: `scored` /
`observed` / `voided`; `open` is the single live commitment per event.

* Headline prediction calibration (the scorecard: Brier, realized edge,
  directional hit rate) is **act-only** — `status='scored' AND decision='act'`.
  It answers the project's success test: when we committed to act, did acting
  beat the market consensus?
* Category trust qualification (`segment_skill`, the conditional calibration the
  Disagreement Diagnosis reads) counts **act + watch and excludes skip** —
  `status != 'open' AND decision IN ('act','watch')`. This is intentional and
  load-bearing: an act-only trust gate cannot bootstrap a fresh category (no act
  history -> never qualified -> never acts -> never accrues act history), so the
  loop would never leave dormancy. watch rows let a category qualify; skip is an
  easy agree-with-the-market forecast whose low Brier would inflate trust.

Do not "simplify" `segment_skill` to act-only — that re-introduces the cold-start
deadlock. Do not let watch/skip into the headline calibration — that re-pollutes
the success metric. See V2_ROADMAP "Only act rows are scored".

---

# outcomes

Stores final resolved reality.

CREATE TABLE outcomes (

id UUID PRIMARY KEY,

event_id UUID,

status VARCHAR(20),

actual_outcome FLOAT,

resolution_source TEXT,

resolved_at TIMESTAMP

);

status is resolved / void / invalid.

actual_outcome is 0..1, allowing partial or probabilistic resolution, not just YES/NO.

void and invalid outcomes are excluded from Brier and calibration, never counted as 0 or 1.

A single BOOLEAN here would silently corrupt calibration on voided or scalar markets.

---

# calibration_metrics

Stores model performance, segmented.

CREATE TABLE calibration_metrics (

id UUID PRIMARY KEY,

model_version TEXT,

segment_type VARCHAR(20),

segment_value TEXT,

brier_score FLOAT,

log_loss FLOAT,

ece FLOAT,

sample_count INTEGER,

generated_at TIMESTAMP

);

segment_type is global / category / edge_bucket / evidence_profile.

A global row alone cannot tell the Decision Gate which divergences to trust.

The Disagreement Diagnosis reads the matching segment to weight each new divergence.

---

# Relationships

events

1:N evidence

1:N probability_assessments

1:N event_market_links

1:N market_snapshots

1:N predictions

1:1 outcomes

---

# Performance Metrics

Primary Metrics:

Brier Score

Expected Calibration Error

Log Loss

Prediction Accuracy

Average Edge

Realized Edge

Signal Win Rate

---

# Future Tables

model_versions

feature_store

simulation_results

strategy_backtests

These tables should be introduced only after V2 is stable.

---

# Long-Term Objective

The database is not merely storage.

It is the memory system of the prediction engine.

Without historical memory there is no calibration.

Without calibration there is no learning.

Without learning there is no edge.
