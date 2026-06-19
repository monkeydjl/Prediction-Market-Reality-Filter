# Prediction Market Reality Filter

# V2 Workflow and Roadmap

Version: 2.0

---

This document turns the three design documents into a concrete plan.

It defines two things:

1. The runtime workflow — what happens to one event, end to end, in the closed loop.
2. The build roadmap — the order in which we make that workflow real.

It is governed by the philosophy documents. When in doubt, the philosophy wins.

Read alongside:

ARCHITECTURE_PHILOSOPHY.md, V2_REFACTOR_PLAN.md, DATABASE_DESIGN.md.

---

# Where We Are

Honest baseline before planning forward.

Already exists:

* Evidence extraction and scoring
* Probability with base-rate anchoring
* Outcome and Calibration models (probability-shaped, partial outcomes supported)
* A calibration feedback loop, currently dormant (needs resolved samples)
* Automatic resolution against settled markets
* JSON persistence

Missing, per the design documents:

* Market price as a first-class, persisted input
* A verified event to contract linkage
* Disagreement Diagnosis (who is more likely wrong)
* A Decision Gate that freezes predictions point-in-time
* Conditional calibration (per segment), and the feedback arrows that consume it

The skeleton exists. The loop does not yet close, and the market is not yet in it.

---

# Guiding Decision

We do not rebuild into eight engines first.

Philosophy 6 says every service must justify its existence; Philosophy 4 says

feedback beats intelligence. The fastest way to honor both is to close the loop

on what we already have, then deepen it.

So the roadmap is ordered by one rule:

Get real resolved outcomes flowing through a closed loop as early as possible.

Everything else — including better evidence factors — comes after the loop turns,

because only the loop can tell us what actually improves the result.

---

# Part 1: Runtime Workflow

What happens to a single event, end to end. Each step names the engine, what it

persists, and the fail-closed rule that protects the loop.

## 1. Discover

Event Service detects and clusters an event.

Writes: events.

## 2. Evidence

Evidence Engine extracts, validates, scores, ranks.

Writes: evidence.

## 3. Probability

Probability Engine estimates probability and confidence.

Reads: calibration feedback for this segment (see step 9).

Writes: probability_assessments (with model_version).

## 4. Market Link and Snapshot

Market Engine binds the event to a specific contract, then snapshots its price.

The link stores the market's own question and resolution criteria.

Writes: event_market_links, market_snapshots.

Fail-closed: if the link is not verified, the event proceeds to display but is

NOT eligible to become a scored prediction.

## 5. Disagreement Diagnosis

Diagnosis Engine asks: if we differ from the market, who is more likely wrong?

It weighs the divergence by our conditional calibration in this segment and by

market efficiency (liquidity, volume).

Output: a trust weight in 0..1 on the divergence.

## 6. Edge

Edge Engine sizes the edge.

Raw Edge = AI Probability - Market Probability.

Adjusted Edge = Raw Edge × trust × liquidity_factor.

## 7. Decision Gate

Decision Engine applies the action bar. Adjusted edge and trust must both clear it.

If they do, it freezes the prediction point-in-time: AI probability, market price,

timestamp, raw and adjusted edge.

Writes: predictions (append-only, immutable). decision is act / watch / skip.

Only act rows are scored.

## 8. Outcome Tracking

Tracking Engine monitors for resolution. At resolve time it re-checks that the

resolved question still matches what we predicted.

Writes: outcomes. status is resolved / void / invalid.

Fail-closed: void and invalid are excluded from calibration, never counted as 0 or 1.

## 9. Calibration and Feedback

Calibration Engine scores committed predictions against outcomes, segmented by

category, edge size, and evidence profile.

Writes: calibration_metrics.

Feeds back into step 3 (correct future probabilities) and step 5 (trust on future

divergences). This arrow is the reason V2 exists.

## Temporal note

An event is re-evaluated over time. Each pass appends new snapshots and, if it

clears the gate, a new prediction. Nothing is overwritten. Probability and edge

are trajectories.

---

# Part 2: Build Roadmap

Six milestones. Each milestone ends with a working loop, only deeper than before.

## Milestone 0 — Foundation: persistence and identity

Goal: an honest place to store predictions, and trustworthy joins.

* Stand up the schema from DATABASE_DESIGN.md.
* Implement event_market_links with verified and link_confidence.
* Enforce the identity rules: no link, no scoring; fail-closed by default.

Exit criteria:

Every prediction can be traced to a verified contract and a resolution criteria

string. Unverified links are visibly excluded, not silently scored.

Why first: nothing downstream is trustworthy if the joins are wrong. This is the

highest-risk part of the system and it protects everything after it.

## Milestone 1 — Close the thinnest loop

Goal: get real resolved samples flowing, even with naive math.

* Market Engine: retrieve and persist market price.
* Decision Gate: minimal — freeze a point-in-time prediction (raw edge is fine here).
* Tracking: use existing auto-resolution to fill outcomes.
* Calibration: wake the existing dormant loop, global Brier only.

Exit criteria:

The loop turns end to end. Resolved predictions accumulate. The calibration loop

crosses its activation threshold and produces a real, if global, Brier score.

Why second: this is the single highest-value milestone. It starts manufacturing

the ground truth that every later decision depends on.

Known simplifications (intentional — do not mistake for the final V2 model):

* `predictions` is a one-row-per-event ledger (UNIQUE event_id). The decision is
  frozen, but the outcome/score is filled in place at resolve. The append-only
  multi-row history is M3, not now.
* `decision` is always "tracked" — every market-derived event is committed. This
  is a freeze, not the Decision Gate; act/watch/skip on adjusted edge is M2.
* Market price/liquidity/volume are folded into the prediction row; a separate
  `market_snapshots` table and an independent `outcomes` fact table are M3.
* Two calibration views coexist: event-level (`GET /events/calibration`, the
  latest trajectory estimate) and prediction-level (`GET /events/predictions/calibration`,
  the frozen committed prediction). The prediction-level one is the honest,
  never-recomputed signal; label both clearly in any UI.
* Cross-platform identity: resolved markets from Polymarket, Manifold, and Kalshi
  all carry a stable `id`; the identity gate's contract-divergence check is only
  as strong as that id (now present on all three sources).

## Milestone 2 — Make the loop honest

Goal: stop chasing our own errors.

* Calibration: segment it (category, edge bucket, evidence profile).
* Diagnosis Engine: discount divergence by conditional calibration × liquidity.
* Decision Gate: switch the bar from raw edge to adjusted edge plus trust.

Exit criteria:

A large divergence in a segment where we have historically been wrong is

down-weighted automatically. The feedback arrows are live.

Why third: this is where "disagreement is a hypothesis" becomes mechanical, and

where the edge stops being a naive subtraction.

## Milestone 3 — Temporal dimension

Goal: catch edges when they are real, not stale.

* Re-evaluate events over time; append snapshots and predictions.
* Track probability and edge trajectories.
* Surface edges that open right after news, before the market absorbs them.

Exit criteria:

An event has a visible probability and edge trajectory, and the system can tell a

fresh edge from a decayed one.

## Milestone 4 — Evidence factor refinement

Goal: improve the front of the pipeline, now that we can measure it.

This is the work from the evidence factor discussion (correctness batch first,

then tuning batch). It is placed here deliberately.

* Correctness fixes (validate by construction): direction polarity for
  under / less than / exceed, window alignment, word-boundary matching,
  deduplicated source volume.
* Tuning factors (validate by Brier): opinion penalty, numeric proximity,
  entity role, full source authority tiers.

Exit criteria:

Each tuning factor ships only if conditional Brier improves on resolved samples.

Otherwise it stays a default-off opt-in.

Why here and not first: the evidence debate was blocked on "no ground truth to

tune against." Milestones 1 and 2 produce that ground truth. Doing this work

before the loop exists is guessing; doing it after is measuring.

## Milestone 5 — Decision optimization and reporting

Goal: turn the loop into decisions a human acts on.

* Decision Report format from V2_REFACTOR_PLAN.md (event, probability, market
  view, edge, confidence, recommendation, risk factors).
* Opportunity surfacing ranked by adjusted edge and trust.
* Track realized edge versus predicted edge.

Exit criteria:

The system consistently surfaces opportunities and reports whether acting on them

would have beaten the market consensus. This is the success criterion in

ARCHITECTURE_PHILOSOPHY.md.

---

# Part 3: Invariants

These hold at every milestone. Breaking one is a defect, not a tradeoff.

* Predictions are append-only and point-in-time frozen. Never recomputed.
* Unverified event-contract links are fail-closed: excluded from scoring.
* Only act rows enter the headline prediction calibration (the success
  scorecard). Resolved watch/skip rows are recorded but excluded. The trust
  qualification signal (segment_skill) is the one deliberate exception: it
  counts act + watch (excluding skip) so a fresh category can bootstrap out of
  dormancy — an act-only trust gate would deadlock. See DATABASE_DESIGN
  "Two metric scopes, two populations".
* void and invalid outcomes are excluded from Brier, never counted as 0 or 1.
* A divergence is a hypothesis. Large edge raises suspicion before conviction.
* The loop must close. Calibration that changes nothing downstream is a defect.
* Every new service must justify its existence with measurable value.
* Each milestone ships a working loop. No big-bang rewrite.

---

# Part 4: Sequencing Summary

| Milestone | Theme | Produces | Depends on |
|---|---|---|---|
| 0 | Persistence and identity | Trustworthy joins | — |
| 1 | Thinnest closed loop | Real resolved samples | 0 |
| 2 | Honest loop | Trust-weighted edge | 1 |
| 3 | Temporal | Edge trajectories | 1 |
| 4 | Evidence refinement | Better, measured inputs | 2 |
| 5 | Decision and reporting | Actionable opportunities | 2, 3 |

The critical path is 0 → 1 → 2. Everything valuable depends on the loop being

closed and honest. Build that first; deepen the rest against real outcomes.

---

# End State

The same end state as V2_REFACTOR_PLAN.md, stated as a test:

The system succeeds when, on resolved outcomes, acting on its surfaced edges would

have beaten market consensus — and when every resolved outcome has already changed

how the next probability and the next edge are formed.

A self-improving loop, not a longer report.
