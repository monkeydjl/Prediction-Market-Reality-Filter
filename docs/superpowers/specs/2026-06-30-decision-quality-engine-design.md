# Decision Quality Engine Design Spec

**Date:** 2026-06-30
**Status:** Proposed
**Scope:** Probability engine decision quality, explanation, conflict handling, market quality, and long-term calibration
**Branch target:** `fix/v0.3.0-hardening` or a new feature branch from it

## Why

The system can already discover events, filter news, run LLM sentiment analysis, estimate probabilities, emit actionable recommendations, and attach per-article `evidence_breakdown`.

The next bottleneck is not another standalone LLM field. The engine needs a decision-quality layer that answers four questions:

1. Why did the system recommend YES, NO, WAIT, or AVOID?
2. What evidence could change that recommendation?
3. Is the market itself good enough to act on?
4. Over time, is the system calibrated, or only confident?

This spec turns the previous enhancement report into a concrete design direction. It intentionally keeps the probability path stable and adds quality gates and explanation layers around it.

## Current Baseline

Relevant existing capabilities:

- `EventRecord.evidence_breakdown` records article-level support or opposition for the YES outcome.
- `EventRecord.actionable_recommendation` exposes direction, confidence, edge, risk level, rationale, and calibration status.
- `decision_report_service` presents recommendation, risk, confidence, and calibration context.
- `news_sentiment_service` produces LLM-backed article sentiment and evidence fields.
- Discovery has hard timeout handling and partial result preservation.
- Market adapters already expose or are being extended to expose bid/ask, liquidity, volume, and spread fields.

Current gaps:

- `evidence_breakdown` is audit data, not yet a decision explanation.
- `actionable_recommendation.rationale` is not reliably traceable to concrete evidence.
- Counter-evidence and conflicting evidence do not have a dedicated downgrade path.
- Market quality is not yet a first-class gate for recommendations.
- Long-term calibration exists in pieces, but the general event engine does not yet have a clean prediction outcome loop.
- LLM cost, fallback, and schema failure telemetry are not surfaced as engine quality signals.

## Goals

1. Convert article-level evidence into a clear decision explanation.
2. Add counter-evidence and conflict-aware downgrades.
3. Add market quality gates before strong recommendations are surfaced.
4. Define a long-term prediction calibration loop for resolved outcomes.
5. Define source reliability memory as a later data asset.
6. Track LLM cost and degraded-mode behavior without blocking the main workflow.

## Non-Goals

- Do not change the core `ai_probability` computation in the first implementation phase.
- Do not replace `evidence_profile` or `apply_sentiment_fusion`.
- Do not ask the main probability LLM to produce marginal probabilities.
- Do not build frontend-heavy visualizations in the first implementation phase.
- Do not make calibration mandatory before emitting provisional recommendations.
- Do not introduce trading vocabulary banned by existing report tests.

## Recommended Roadmap

### Phase 1: Decision Explanation + Conflict Layer

This phase should be implemented first.

It converts `evidence_breakdown` into a structured `decision_quality` block and uses counter-evidence to downgrade overconfident recommendations.

Outputs:

- `decision_quality.supporting_evidence`
- `decision_quality.opposing_evidence`
- `decision_quality.conflict_score`
- `decision_quality.consensus_level`
- `decision_quality.decision_rationale_zh`
- `decision_quality.reversal_triggers`
- `decision_quality.downgrade_reason`

### Phase 2: Market Quality Layer

This phase adds market feasibility checks.

Outputs:

- `market_quality.score`
- `market_quality.liquidity_score`
- `market_quality.spread_penalty`
- `market_quality.thin_market_flag`
- `market_quality.stale_price_flag`
- `market_quality.downgrade_reason`

Rules:

- Wide spread can downgrade YES/NO to WAIT.
- Low liquidity can suppress strong action.
- Stale price lowers market confidence.
- Missing market fields should degrade gracefully, not crash analysis.

### Phase 3: Prediction Outcome Calibration

This phase records prediction snapshots and evaluates them after resolution.

Outputs:

- `prediction_snapshot`
- `market_snapshot`
- `recommendation_snapshot`
- `evidence_snapshot`
- `resolved_outcome`
- `brier_score`
- `calibration_bucket`
- `category_accuracy`

This phase is long-term: it only becomes valuable after enough resolved samples exist.

### Phase 4: Source Reliability Memory

This phase builds a source/domain reliability table from resolved outcomes and source contribution history.

Outputs:

- domain reliability by event category
- stale-source indicators
- aggregation/repost indicators
- source-specific evidence weighting hints

### Phase 5: LLM Cost and Stability Telemetry

This phase makes model behavior observable.

Outputs:

- sentiment cache hit rate
- evidence breakdown cache hit rate
- LLM timeout count
- schema fallback count
- per-event token cost estimate
- `degraded_mode` reason

## Phase 1 Detailed Design

### New Field: `decision_quality`

Add an optional top-level field to `EventRecord` and decision reports:

```python
{
    "supporting_evidence": [
        {
            "source": "Reuters",
            "title": "Article title",
            "strength": 0.82,
            "credibility": 0.9,
            "rationale_zh": "这篇报道提供了直接支持 YES 的事实。"
        }
    ],
    "opposing_evidence": [],
    "conflict_score": 0.15,
    "consensus_level": "high",
    "decision_rationale_zh": "主要证据来自高可信来源，且反向证据较弱，因此维持 YES 方向。",
    "reversal_triggers": [
        "如果官方来源否认关键事实，则应降级为 WAIT。",
        "如果市场流动性继续下降，则不应输出强行动建议。"
    ],
    "downgrade_reason": None
}
```

### Evidence Selection

Use `evidence_breakdown` as input.

Selection rules:

- Support current direction:
  - YES recommendation uses `direction == "support"` as supporting evidence.
  - NO recommendation uses `direction == "oppose"` as supporting evidence.
- Oppose current direction:
  - YES recommendation uses `direction == "oppose"` as opposing evidence.
  - NO recommendation uses `direction == "support"` as opposing evidence.
- WAIT and AVOID should surface both sides and explain why no strong direction is justified.
- Rank by `strength * credibility`.
- Keep at most 3 supporting and 3 opposing items in the initial implementation.

### Conflict Score

Use deterministic scoring, not an additional LLM call.

Suggested formula:

```text
support_weight = sum(strength * credibility for supporting items)
oppose_weight = sum(strength * credibility for opposing items)
total = support_weight + oppose_weight
conflict_score = min(support_weight, oppose_weight) / total if total > 0 else 0
```

Suggested consensus levels:

- `high`: conflict_score < 0.20 and at least one strong supporting item exists
- `medium`: conflict_score < 0.40
- `low`: conflict_score >= 0.40 or both sides have strong items
- `none`: no usable evidence breakdown

### Downgrade Rules

Decision quality may downgrade recommendations, but must not change raw probability.

Initial deterministic rules:

- If `consensus_level == "low"` and current recommendation is YES or NO, downgrade display recommendation to WAIT.
- If opposing evidence contains an official/regulatory source with `strength >= 0.7`, downgrade to WAIT.
- If no supporting evidence exists for YES/NO, downgrade to WAIT.
- If risk level is high and consensus is low, downgrade to AVOID.

The downgrade should be explicit:

```python
"downgrade_reason": "强反向证据与当前方向冲突，降级为 WAIT。"
```

### Rationale Generation

Phase 1 should avoid a new LLM call. Generate `decision_rationale_zh` from templates using:

- current recommendation
- support count and top source
- opposing count and top source
- consensus level
- downgrade reason

Example template:

```text
主要证据来自 {top_support_source}，支持 {direction} 的强度较高；反向证据较弱，因此维持 {direction} 方向。
```

If downgraded:

```text
虽然存在支持 {direction} 的证据，但反向证据强度较高，当前结论降级为 WAIT。
```

All generated text must avoid banned vocabulary:

- long
- short
- buy
- sell
- position
- kelly
- order

## Phase 2 Detailed Design

### New Field: `market_quality`

```python
{
    "score": 0.76,
    "liquidity_score": 0.8,
    "spread_penalty": 0.1,
    "thin_market_flag": False,
    "stale_price_flag": False,
    "downgrade_reason": None
}
```

### Input Fields

Use fields already present or being added by market sources:

- `baseline`
- `bid_ask.bid`
- `bid_ask.ask`
- `bid_ask.spread`
- `volume`
- `liquidity`
- `last_updated`

### Market Quality Rules

Initial thresholds should be configurable:

- `MARKET_QUALITY_ENABLED=true`
- `MARKET_MAX_SPREAD_PCT=12`
- `MARKET_MIN_LIQUIDITY=1000`
- `MARKET_MIN_VOLUME=1000`
- `MARKET_STALE_AFTER_MINUTES=180`

Behavior:

- Missing fields produce unknown sub-scores, not exceptions.
- Wide spread lowers score.
- Low liquidity sets `thin_market_flag=true`.
- Stale timestamps set `stale_price_flag=true`.
- If score is below threshold, downgrade strong recommendations to WAIT.

## Phase 3 Detailed Design

### Prediction Snapshot

Record immutable snapshots when a recommendation is emitted:

```python
{
    "event_id": "ev_123",
    "created_at": "2026-06-30T00:00:00Z",
    "question": "Will ...?",
    "estimated_probability": 62.0,
    "market_probability": 49.0,
    "edge": 13.0,
    "recommendation": "YES",
    "confidence": "medium",
    "evidence_strength": 0.72,
    "conflict_score": 0.18,
    "market_quality_score": 0.81,
    "category": "policy",
    "source": "Kalshi"
}
```

### Outcome Scoring

When resolved:

- `resolved_outcome`: true / false / unknown
- `brier_score`: `(probability - outcome)^2`
- `direction_correct`: whether YES/NO direction matched outcome
- `edge_bucket`: e.g. 0-5, 5-10, 10-20, 20+
- `confidence_bucket`: low / medium / high

This should be append-only where possible.

## Phase 4 Detailed Design

Source reliability should be a derived data product, not a blocking dependency.

Suggested source record:

```python
{
    "domain": "reuters.com",
    "category": "policy",
    "sample_count": 42,
    "support_when_correct_rate": 0.67,
    "avg_credibility": 0.86,
    "stale_rate": 0.05,
    "last_updated": "2026-06-30T00:00:00Z"
}
```

Use this data only after sample thresholds are met. Before then, keep LLM `source_credibility` and simple source-tier heuristics.

## Phase 5 Detailed Design

Track LLM stability without changing user-facing behavior.

Suggested fields:

```python
{
    "degraded_mode": false,
    "degraded_reason": None,
    "llm_schema_fallbacks": 0,
    "llm_timeout_count": 0,
    "sentiment_cache_hit": true,
    "estimated_token_cost": 0.003
}
```

This can live in internal audit logs first, then become API output if useful.

## Architecture

Recommended service boundaries:

| Service | Responsibility |
| --- | --- |
| `decision_quality_service.py` | Build `decision_quality` from recommendation + evidence breakdown |
| `market_quality_service.py` | Score market feasibility and produce market downgrade reasons |
| `prediction_outcome_service.py` | Store prediction snapshots and score resolved outcomes |
| `source_reliability_service.py` | Build source reliability stats from historical outcomes |
| existing LLM services | Keep sentiment and probability analysis unchanged |

Do not place all logic inside `event_intelligence_service.py`. That file should orchestrate calls and attach resulting fields.

## Data Flow

```text
candidate event
  -> filtered news
  -> sentiment_profile
  -> evidence_breakdown
  -> analyze_event / build_event_record
  -> actionable_recommendation
  -> decision_quality_service
  -> market_quality_service
  -> final record / decision report
```

The raw probability and raw recommendation remain visible. Downgrades should be explicit overlays rather than silent mutations.

## Error Handling

| Failure | Behavior |
| --- | --- |
| Missing `evidence_breakdown` | `decision_quality.consensus_level = "none"` and no downgrade unless recommendation lacks support |
| Malformed evidence item | Skip item |
| Market fields missing | Use unknown sub-scores and avoid hard failure |
| Timestamp parse failure | Treat stale status as unknown |
| Outcome unavailable | Leave calibration fields unresolved |
| Source reliability sample too small | Do not apply reliability adjustment |
| LLM telemetry unavailable | Omit telemetry or default to non-degraded |

## Testing Strategy

### Unit Tests

Add deterministic tests for:

- evidence selection for YES and NO directions
- support/opposition ranking by `strength * credibility`
- conflict score calculation
- consensus level thresholds
- downgrade to WAIT when conflict is high
- downgrade to AVOID when risk is high and conflict is high
- template rationale avoids banned vocabulary
- market quality score with wide spread
- market quality score with low liquidity
- missing market fields do not crash

### Integration Tests

Add focused tests for:

- `analyze_event()` attaches `decision_quality` when enabled
- decision report passes through `decision_quality`
- raw `ai_probability` is unchanged by decision quality
- raw `evidence_profile` is unchanged
- market downgrade reason appears when spread is too wide

### Regression Tests

Existing tests around these areas must continue to pass:

- `test_evidence_aggregation_service`
- `test_event_intelligence_service`
- `test_decision_report_service`
- `test_events_routes`
- banned vocabulary tests

## Config Flags

```python
DECISION_QUALITY_ENABLED=true
DECISION_QUALITY_MAX_EVIDENCE_ITEMS=3
DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD=0.40
DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD=0.20

MARKET_QUALITY_ENABLED=true
MARKET_MAX_SPREAD_PCT=12
MARKET_MIN_LIQUIDITY=1000
MARKET_MIN_VOLUME=1000
MARKET_STALE_AFTER_MINUTES=180

PREDICTION_OUTCOME_TRACKING_ENABLED=false
SOURCE_RELIABILITY_ENABLED=false
LLM_TELEMETRY_ENABLED=true
```

Long-term features default off until storage and data retention behavior are explicit.

## Acceptance Criteria

Phase 1 acceptance:

1. Event records can include a `decision_quality` block.
2. Decision reports can pass through `decision_quality`.
3. YES/NO explanations cite concrete supporting and opposing evidence.
4. High conflict downgrades strong recommendations to WAIT with a clear reason.
5. Raw `ai_probability` and `evidence_profile` behavior is unchanged.
6. No generated rationale contains banned vocabulary.

Phase 2 acceptance:

1. Event records can include a `market_quality` block.
2. Wide spread or low liquidity can downgrade the displayed recommendation.
3. Missing market fields do not crash discovery or analysis.
4. Market downgrade reasons are explicit.

Later phase acceptance:

1. Prediction snapshots are immutable.
2. Resolved outcomes can produce Brier scores.
3. Source reliability applies only after minimum sample thresholds.
4. LLM telemetry can identify degraded-mode events.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Too much scope for one implementation | Implement Phase 1 first; keep phases 2-5 as separate plans |
| Explanation becomes another black box | Use deterministic templates first, not a new LLM call |
| Downgrades hide raw signal | Preserve raw recommendation and add explicit overlay fields |
| Market thresholds are arbitrary | Put thresholds behind config and test boundary cases |
| Calibration needs data volume | Default outcome tracking off until storage policy is clear |
| Source reliability overfits sparse data | Require minimum sample thresholds before applying |
| Frontend grows noisy | Backend fields first; UI display should be a separate design |

## Recommended First Implementation

Start with Phase 1 only:

1. Add `decision_quality_service.py`.
2. Add `DecisionQuality` model to `models/event.py`.
3. Attach `decision_quality` in `analyze_event()` after `evidence_breakdown` and `actionable_recommendation` exist.
4. Pass `decision_quality` through `decision_report_service`.
5. Add unit and integration tests.

This produces the highest immediate quality improvement while keeping the probability engine stable.

