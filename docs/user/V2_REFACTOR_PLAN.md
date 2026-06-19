# Prediction Market Reality Filter

# V2 Refactor Plan

Version: 2.0

---

# Strategic Goal

Transform the platform from:

Research Engine

to

Decision Intelligence Engine

---

# Current Architecture

Source Collection
→ Event Discovery
→ Evidence Extraction
→ AI Analysis
→ Probability
→ Report

Problems:

* No market awareness
* No outcome tracking
* No calibration
* No feedback loop
* Divergence from market is treated as edge, never diagnosed as possible model error
* The flow is linear: it generates a signal but never learns whether the signal was true

---

# Target Architecture

Event Discovery
↓
Evidence Engine
↓
Probability Engine   ←─────────────────┐
↓                                       │
Market Engine                           │
↓                                       │
Disagreement Diagnosis   ←──────────────┤
↓                                       │
Edge Engine                             │
↓                                       │
Decision Gate                           │
↓                                       │
Outcome Tracker                         │
↓                                       │
Calibration Engine ─────────────────────┘
↓
Decision Report

The two feedback arrows are the point of V2.

Calibration feeds the Probability Engine (correct future probabilities)

and the Disagreement Diagnosis (how much to trust the next divergence).

Without those arrows the system is still a one-way report generator.

---

# Service Layout

backend/

services/

event_service/

evidence_engine/

probability_engine/

market_engine/

diagnosis_engine/

decision_engine/

tracking_engine/

report_engine/

shared/

models/

schemas/

utils/

---

# Event Service

Responsibilities:

* Source ingestion
* Event detection
* Event clustering
* Deduplication

Output:

Event Object

---

# Evidence Engine

Responsibilities:

* Extraction
* Validation
* Contradiction analysis
* Source scoring
* Evidence ranking

Public API:

evidence_engine.process(event)

Output:

Evidence Package

---

# Probability Engine

Responsibilities:

* Prior estimation
* Bayesian updating
* Confidence estimation
* Probability calculation

Output:

Probability Assessment

---

# Market Engine

Responsibilities:

* Polymarket integration
* Kalshi integration
* Bind each event to a specific contract, verified and point-in-time
* Store the market's own resolution criteria for later outcome verification
* Market probability retrieval

The event ↔ contract link is the make-or-break of the loop.

A wrong link silently scores calibration against the wrong outcome.

Unverified links are fail-closed, not guessed.

Output:

Market Snapshot

---

# Disagreement Diagnosis

The missing organ. Sits between observing the market and claiming an edge.

Responsibilities:

* Answer one question: if AI and market disagree, who is more likely wrong?
* Weigh the divergence by our conditional calibration in this category
* Weigh the divergence by market efficiency (liquidity, volume, spread)
* Flag divergences that are more likely model error than mispricing

Input:

AI Probability, Market Snapshot, conditional calibration history

Output:

Diagnosed Divergence

(direction, raw magnitude, and a trust weight in 0..1)

---

# Edge Engine

Responsibilities:

Convert a diagnosed divergence into a sized edge.

Raw Edge:

Raw Edge = AI Probability - Market Probability

Adjusted Edge:

Adjusted Edge = Raw Edge × calibration_trust × liquidity_factor

A large raw edge with low trust is not an opportunity.

Output:

Edge Signal (raw edge, adjusted edge, trust)

---

# Decision Gate

Turns an edge into a committed, trackable prediction.

Responsibilities:

* Apply the action threshold (adjusted edge and trust must both clear the bar)
* Freeze the prediction point-in-time: AI probability, market price, timestamp
* Emit act / watch / skip
* Only committed predictions enter Outcome Tracking and Calibration

You can only calibrate what you committed to. Tracking everything is not a decision.

Output:

Committed Prediction (immutable, append-only)

---

# Tracking Engine

Responsibilities:

* Prediction persistence
* Outcome monitoring
* Resolution updates

Output:

Historical prediction records

---

# Calibration Engine

Responsibilities:

* Brier score
* Log loss
* Reliability curves
* Probability correction

Calibration must be conditional, not only global.

Per category, per edge size, per evidence profile.

A global Brier cannot tell the Decision Gate which divergences to trust.

Feeds back into:

Probability Engine (correct future probabilities)

Disagreement Diagnosis (trust weight on future divergences)

Output:

Calibration statistics (segmented)

---

# Temporal Dimension

An event is not a single pass. It is re-evaluated over time.

Evidence arrives continuously.

Probability updates.

Market moves.

Edge opens and closes.

The largest real edge is often right after news, before the market absorbs it.

Each re-evaluation appends a snapshot; predictions are never overwritten.

Probability and edge are trajectories, not single values.

---

# Report Engine

Responsibilities:

Generate:

Decision Report

Instead of:

Research Report

Output Format:

Event

Probability

Market View

Edge

Confidence

Recommendation

Risk Factors

---

# Refactor Roadmap

For the concrete build order, runtime workflow, and exit criteria, see V2_ROADMAP.md.

The phases below are the high-level arc; V2_ROADMAP.md is the executable version.

Phase 3

Evidence Consolidation

Phase 4

Market Integration

Phase 5

Edge Detection

Phase 5.5

Disagreement Diagnosis and Decision Gate

Phase 6

Outcome Tracking

Phase 7

Calibration Framework (conditional, with feedback into Probability and Diagnosis)

Phase 8

Decision Optimization

---

# End State

The system becomes:

A self-improving prediction intelligence platform driven by reality feedback.

A closed loop: every resolved outcome changes how the next probability is formed,

how the next divergence is trusted, and which edges are worth committing to.
