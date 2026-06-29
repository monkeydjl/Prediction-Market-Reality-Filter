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
    "downgrade_reason": None,
    "raw_direction": "YES",
    "displayed_direction": "YES",
    "downgraded": false
}
```

#### Field Semantics

`raw_direction` mirrors `actionable_recommendation.direction` at the time
`decision_quality` is built. It is captured once and never mutated by the
downgrade pipeline, so audit consumers can always recover the original signal
even after a downgrade overlay is applied.

`displayed_direction` starts equal to `raw_direction`. When a downgrade rule
fires (high conflict, missing support, official counter-evidence, high risk +
low consensus), `displayed_direction` is rewritten to the downgraded value
(`WAIT` or `AVOID`) and `downgraded` is set to `true`. Downstream report
renderers SHOULD surface `displayed_direction` to end users and MUST NOT
overwrite `raw_direction`.

`downgraded` is a convenience boolean for filtering / metrics; it is `true`
iff `displayed_direction != raw_direction`.

`actionable_recommendation.direction` (the raw signal) is NEVER mutated by
this layer — downgrades are an explicit overlay on `decision_quality` only.

### Evidence Selection

Use `evidence_breakdown` as input.

#### Two distinct direction vocabularies

The system carries two separate direction vocabularies that must not be mixed:

- `EvidenceBreakdownItem.direction` ∈ `{support, oppose, neutral}` — article
  stance relative to the **YES outcome** of the event question. `support`
  means the article supports YES occurring; `oppose` means it supports NO.
- `actionable_recommendation.direction` ∈ `{YES, NO, WAIT, AVOID}` — the
  recommended trading/event side the system suggests acting on.

`decision_quality` translates between these two vocabularies when selecting
evidence, but the underlying `direction` values stored on each
`EvidenceBreakdownItem` MUST NOT be rewritten.

Selection rules:

- Support current recommendation direction:
  - YES recommendation uses `direction == "support"` as supporting evidence,
    `direction == "oppose"` as opposing evidence.
  - NO recommendation uses `direction == "oppose"` as supporting evidence,
    `direction == "support"` as opposing evidence.
- WAIT and AVOID surface BOTH non-neutral sides (support and oppose), capped
  independently, and explain why no strong direction is justified. There is no
  "supporting side" for WAIT/AVOID — both columns are presented for balance.
- Neutral items are never selected into either column.
- Rank by `strength * credibility` descending.
- Keep at most 3 supporting and 3 opposing items in the initial implementation
  (configurable via `DECISION_QUALITY_MAX_EVIDENCE_ITEMS`).

#### Empty `evidence_breakdown` handling

When `evidence_breakdown` is empty or absent, `decision_quality` is still
emitted with:

- `supporting_evidence = []`, `opposing_evidence = []`
- `conflict_score = 0.0`, `consensus_level = "none"`
- `decision_rationale_zh` = "缺少可解析的证据分解，无法判断证据一致性。"
- `downgrade_reason` = "缺少证据支持，强方向建议降级为 WAIT。" when the raw
  direction is YES or NO; otherwise `None`.
- `displayed_direction` downgraded to `WAIT` for raw YES/NO; unchanged for
  raw WAIT/AVOID.

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

#### Known Limitation — Strength vs Count Imbalance

The pure weighted formula can misjudge cases where one side has a single
strong item and the other has many weak items. Example:

- 1 supporting item: `strength=0.9, credibility=0.9 → weight=0.81`
- 5 opposing items: each `strength=0.1, credibility=0.9 → weight=0.09` each,
  `total=0.45`
- `conflict_score = 0.45 / (0.81 + 0.45) ≈ 0.36` → `medium` consensus

But intuitively, 5 consistent opposing sources — even if individually weak
— should give a `low` consensus, not `medium`.

**Phase 1 decision**: keep the simple formula. The `consensus_level`
thresholds are configurable (`DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD`,
`DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD`), so operators can tune on
their data without code changes. The formula is also transparent and
auditable — important for a v1 explanation layer.

**Phase 3 follow-up**: once real resolved-outcome data exists, evaluate
whether adding an `evidence_count_weight = log(1 + count)` term improves
Brier-score correlation. Do NOT add it speculatively in Phase 1 — premature
complexity in an explanation layer makes the explanation itself harder to
explain.

**Safety net**: the `low` consensus level also fires when "both sides have
strong items" (any item with `strength >= 0.7` on both sides). This catches
the imbalance case where strong opposing evidence exists regardless of
count.

### Downgrade Rules

Decision quality may downgrade the **displayed** recommendation, but must not
change raw probability or raw `actionable_recommendation.direction`.

Downgrade is evaluated in TWO sequential stages:

**Stage A — initial downgrade (first match wins, then stop):**

1. If `consensus_level == "low"` and raw direction is YES or NO:
   - `displayed_direction = WAIT`
   - `downgrade_reason = "证据冲突较高，强方向建议降级为 WAIT。"`
2. If opposing evidence contains an official/regulatory source with
   `strength >= 0.7` and raw direction is YES or NO:
   - `displayed_direction = WAIT`
   - `downgrade_reason = "存在高强度的官方/监管反向证据，降级为 WAIT。"`
3. If supporting evidence is empty and raw direction is YES or NO:
   - `displayed_direction = WAIT`
   - `downgrade_reason = "缺少支持证据，强方向建议降级为 WAIT。"`
4. If `evidence_breakdown` is empty/absent and raw direction is YES or NO
   (covered by the empty-handling section above):
   - `displayed_direction = WAIT`
   - `downgrade_reason = "缺少证据支持，强方向建议降级为 WAIT。"`

Rules 1-3 can only fire when `decision_quality` is built with evidence
available; rule 4 covers the empty case. The FIRST matching rule among
1, 2, 3, 4 (in that order) sets `displayed_direction` and
`downgrade_reason`; subsequent Stage-A rules are skipped.

If no Stage-A rule fires, `displayed_direction` equals `raw_direction`,
`downgraded = false`, and `downgrade_reason = None`.

**Stage B — risk escalation (evaluated after Stage A):**

After Stage A produces a `displayed_direction` (which may already be WAIT
or unchanged), apply the risk-escalation rule:

- Risk escalation rule: If `risk_level == "high"` AND `consensus_level` in `("low", "none")`:
  - `displayed_direction = AVOID`
  - `downgrade_reason = "高风险且证据不足/冲突，降级为 AVOID。"`
  - (overrides any WAIT produced by Stage A — this is an escalation, not a first-match rule)

Stage B runs UNCONDITIONALLY after Stage A. It can escalate WAIT → AVOID
but cannot de-escalate. If Stage A left `displayed_direction = YES` (no
Stage-A rule fired) and Stage B fires, the result is AVOID — Stage B is the
only path that can jump raw YES/NO directly to AVOID.

**Test coverage for the two-stage sequence:**

- Rule 1 + risk escalation both match → final `displayed_direction = AVOID` (Stage A sets WAIT, Stage B escalates to AVOID)
- Risk escalation alone matches (consensus_level="none" from empty breakdown, risk_level="high") → final AVOID
- Rule 1 matches, risk escalation does not → final WAIT
- No rule matches → `displayed_direction = raw_direction`, `downgraded = false`

If no rule fires in either stage, `displayed_direction` equals
`raw_direction`, `downgraded = false`, and `downgrade_reason = None`.

The downgrade should be explicit:

```python
"downgrade_reason": "强反向证据与当前方向冲突，降级为 WAIT。"
```

#### Downgrade interaction with `actionable_recommendation`

`actionable_recommendation` is NEVER mutated by `decision_quality`. The
downgrade is an overlay exposed via `displayed_direction` and `downgraded`.
Frontends that render a single user-facing direction MUST read
`displayed_direction` from `decision_quality` when the block is present and
`downgraded == true`; otherwise fall back to
`actionable_recommendation.direction`.

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

#### Relationship with `actionable_recommendation.rationale`

The system carries TWO distinct rationales. They coexist and serve different
audiences; neither replaces the other:

| Field | Owner | Audience | Content |
| --- | --- | --- | --- |
| `actionable_recommendation.rationale` | `event_intelligence_service._build_actionable_recommendation` | Action taker | Market pricing, edge, evidence strength — "why act on this" |
| `decision_quality.decision_rationale_zh` | `decision_quality_service` | Auditor / reviewer | Supporting vs opposing evidence, conflict level, downgrade reason — "why this conclusion is trustworthy" |

Implementation rules:

- Both fields are always populated when their respective features are enabled.
- `decision_quality.decision_rationale_zh` MUST NOT duplicate market pricing
  or edge prose; it focuses on evidence balance and conflict.
- `actionable_recommendation.rationale` MUST NOT reference
  `decision_quality` fields (no circular dependency —
  `decision_quality_service` reads `actionable_recommendation` as input, not
  the reverse).
- When `decision_quality` is disabled (`DECISION_QUALITY_ENABLED=false`),
  `actionable_recommendation.rationale` continues to work unchanged.
- When `actionable_recommendation` is `None` (e.g., signal=WATCHLIST or
  feature disabled), `decision_quality` MUST still be built — see the
  "Missing `actionable_recommendation`" section under Error Handling.

## Phase 2 Detailed Design

### New Field: `market_quality`

```python
{
    "score": 0.76,
    "liquidity_score": 0.8,
    "spread_penalty": 0.1,
    "thin_market_flag": False,
    "stale_price_flag": False,
    "downgrade_reason": None,
    "raw_direction": "YES",
    "suggested_direction": "YES",
    "downgraded": False,
    "applied_to_displayed_direction": False
}
```

`raw_direction` mirrors the direction passed into `market_quality_service`
from the raw recommendation. `suggested_direction` starts equal to
`raw_direction`; it becomes `WAIT` when market quality fails a configured
threshold. `downgraded` is `true` when `suggested_direction != raw_direction`.

`applied_to_displayed_direction` is set by the merge step when
`market_quality.suggested_direction` is stricter than the current
`decision_quality.displayed_direction` and therefore changes the final
user-facing direction. This lets audits distinguish whether market quality
changed the final direction or merely recorded a quality score without
acting.

### Applicability — Market Source Gating

`market_quality` is only computed for events whose `source.type ==
"prediction_market"` (e.g., Polymarket, Kalshi). For other source types:

- `prediction_question` (Metaculus): no trading volume / bid-ask; the block
  is set to `None` (key omitted from record entirely)
- `open_web` (manual news-driven events): same — `None`
- `sports_event`: same — `None`

This mirrors the existing `freeze_prediction` market-gated convention.
`decision_quality`, by contrast, applies to ALL source types because
`evidence_breakdown` is populated for all sources.

### Input Fields

Use fields already present or being added by market sources:

- `baseline`
- `bid_ask.bid`
- `bid_ask.ask`
- `bid_ask.spread`
- `volume`
- `liquidity`
- `last_updated`

### Market Adapter Field Audit (Pre-Implementation)

Before Phase 2 implementation, complete a one-time audit documenting the
actual availability of each input field across all market adapters. Record
the result in `docs/superpowers/audits/market-quality-field-audit.md`:

| Adapter | `bid` | `ask` | `spread` | `volume` | `liquidity` | `last_updated` |
| --- | --- | --- | --- | --- | --- | --- |
| Polymarket | audit required | audit required | audit required | audit required | audit required | audit required |
| Kalshi | audit required | audit required | audit required | audit required | audit required | audit required |
| Metaculus | N/A | N/A | N/A | N/A | N/A (no market) | N/A |

Where a field is unavailable, the adapter returns `None` and
`market_quality` records the corresponding sub-score as `unknown` rather
than failing. Metaculus is explicitly recorded as a no-market source — it
does not produce a `market_quality` block at all (see Applicability above).

### Market Quality Rules

Initial thresholds should be configurable:

- `MARKET_QUALITY_ENABLED=false` (default OFF)
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

### Downgrade Chain — Parallel + Most-Strict Semantics

`decision_quality` and `market_quality` are computed INDEPENDENTLY and in
PARALLEL. Neither reads the other's output as input. After both complete,
`event_intelligence_service` merges them using a "most-strict direction
wins" rule:

```text
severity_rank = { YES: 0, NO: 0, WAIT: 1, AVOID: 2 }

dq_direction = decision_quality.displayed_direction   # may be raw or downgraded
mq_direction = market_quality.suggested_direction     # WAIT if score < threshold, else raw

final_displayed_direction = the one with HIGHER severity_rank
```

If both downgraded to WAIT, the `downgrade_reason` fields are concatenated:

```text
final_downgrade_reason = dq.downgrade_reason + " | " + mq.downgrade_reason
```

If only one layer downgraded, its `downgrade_reason` is used as-is.

This parallel design avoids ordering coupling between the two services and
ensures the most conservative direction always surfaces to the user. The
merged `final_displayed_direction` is stored on a new top-level field:

```python
record["final_displayed_direction"] = "AVOID"  # or YES/NO/WAIT
record["final_downgrade_reason"] = "证据冲突较高... | 市场价差过大..."
```

Both `decision_quality` and `market_quality` retain their own
`displayed_direction` / `suggested_direction` fields unchanged, so audits
can always trace which layer contributed which downgrade.

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
| `decision_quality_service.py` | Build `decision_quality` from `actionable_recommendation` + `evidence_breakdown`. Pure function: reads inputs, returns overlay block, no writeback. |
| `market_quality_service.py` | Score market feasibility and produce market downgrade reasons |
| `prediction_outcome_service.py` | Store prediction snapshots and score resolved outcomes |
| `source_reliability_service.py` | Build source reliability stats from historical outcomes |
| existing LLM services | Keep sentiment and probability analysis unchanged |

Do not place all logic inside `event_intelligence_service.py`. That file should orchestrate calls and attach resulting fields.

### `decision_quality_service` contract

```python
def build_decision_quality(
    *,
    recommendation: dict[str, Any] | None,  # actionable_recommendation
    evidence_breakdown: list[dict[str, Any]],  # may be empty
    enabled: bool,
    max_items: int,
    high_threshold: float,
    medium_threshold: float,
) -> dict[str, Any]:
    """Build the decision_quality overlay block.

    Pure function: does not mutate `recommendation` or `evidence_breakdown`.
    Returns a dict with keys: supporting_evidence, opposing_evidence,
    conflict_score, consensus_level, decision_rationale_zh, reversal_triggers,
    downgrade_reason, raw_direction, displayed_direction, downgraded.
    """
```

The function is synchronous and deterministic — no LLM calls, no I/O. This
makes it trivially testable and safe to call inside `analyze_event` without
adding latency or failure modes.

`settings` is intentionally not passed into this function. The orchestrator
extracts concrete scalar config values and passes them explicitly. This keeps
the pure function easy to unit test and prevents hidden dependencies on the
global settings object.

### `analyze_event` integration contract

`decision_quality_service` is a best-effort audit layer. `analyze_event`
MUST wrap the call in try/except so a build failure (e.g., malformed
evidence, unexpected None in recommendation fields, future regression)
never blocks event production:

```python
try:
    from app.services.decision_quality_service import build_decision_quality
    if settings.DECISION_QUALITY_ENABLED:
        record["decision_quality"] = build_decision_quality(
            recommendation=record.get("actionable_recommendation"),
            evidence_breakdown=record.get("evidence_breakdown", []),
            enabled=True,
            max_items=settings.DECISION_QUALITY_MAX_EVIDENCE_ITEMS,
            high_threshold=settings.DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD,
            medium_threshold=settings.DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD,
        )
except Exception as exc:
    logger.warning("decision_quality build failed: %s", exc)
    record["decision_quality"] = {
        "error": "build_failed",
        "raw_direction": (record.get("actionable_recommendation") or {}).get("direction", "WAIT"),
        "displayed_direction": (record.get("actionable_recommendation") or {}).get("direction", "WAIT"),
        "downgraded": False,
        "downgrade_reason": None,
        "decision_rationale_zh": "决策质量构建失败，使用原始方向。",
        "supporting_evidence": [],
        "opposing_evidence": [],
        "conflict_score": 0.0,
        "consensus_level": "none",
        "reversal_triggers": [],
    }
```

This mirrors the project's existing `fail_closed_empty_list` pattern used by
external event sources — partial failures degrade per-item, never as a
whole-response fallback. The error block retains `raw_direction` /
`displayed_direction` so downstream consumers always have a direction to
read.

When `DECISION_QUALITY_ENABLED=false` (default), the entire try/except is
skipped — the record has no `decision_quality` key, byte-identical to a
record built without the feature.

### Audit Trail Integration

When `decision_quality` is built successfully, the following subset of
fields is appended to `event_audit.jsonl` alongside the existing snapshot:

```json
{
    "event_id": "ev_...",
    "timestamp": "2026-06-30T...",
    "decision_quality_audit": {
        "raw_direction": "YES",
        "displayed_direction": "WAIT",
        "downgraded": true,
        "downgrade_reason": "证据冲突较高，强方向建议降级为 WAIT。",
        "consensus_level": "low",
        "conflict_score": 0.45,
        "supporting_count": 1,
        "opposing_count": 3
    }
}
```

Full evidence items are NOT duplicated into the audit log — they already
live in `evidence_breakdown` and the event record. Only the decision-level
summary fields are captured, so auditors can answer "what did the system
recommend and why was it downgraded?" without re-running the engine.

When `build_decision_quality` fails (the try/except fallback fires), the
audit entry records `decision_quality_audit.error = "build_failed"` plus
the `raw_direction` fallback, so the audit trail is never silently
incomplete.

## Data Flow

```text
candidate event
  -> filtered news
  -> sentiment_profile
  -> evidence_breakdown
  -> analyze_event / build_event_record
  -> actionable_recommendation  (raw signal)
  -> decision_quality_service   (overlay: displayed_direction, downgrade_reason)
  -> market_quality_service     (overlay: market_quality block, may further constrain)
  -> final record / decision report
```

The raw probability and raw recommendation remain visible. Downgrades are
explicit overlays (`displayed_direction`, `downgraded`, `downgrade_reason`)
rather than silent mutations of `actionable_recommendation`.

## Error Handling

| Failure | Behavior |
| --- | --- |
| Missing `evidence_breakdown` | `decision_quality.consensus_level = "none"` and no downgrade unless recommendation lacks support |
| Missing `actionable_recommendation` | Build `decision_quality` with `raw_direction = "WAIT"`, both evidence columns from `evidence_breakdown` (no recommendation-side filtering), `consensus_level` per the conflict formula, and `displayed_direction = "WAIT"`, `downgraded = false`. The block is still emitted so audits remain complete. |
| Missing both `evidence_breakdown` and `actionable_recommendation` | Emit `decision_quality` with all evidence lists empty, `consensus_level = "none"`, `raw_direction = "WAIT"`, `displayed_direction = "WAIT"`, `downgraded = false`, `downgrade_reason = None`, and `decision_rationale_zh` = "缺少建议与证据分解，无法判断决策质量。" |
| Malformed evidence item | Skip item (do not include in either column); if all items are malformed, treat as empty `evidence_breakdown` |
| Market fields missing | Use unknown sub-scores and avoid hard failure |
| Timestamp parse failure | Treat stale status as unknown |
| Outcome unavailable | Leave calibration fields unresolved |
| Source reliability sample too small | Do not apply reliability adjustment |
| LLM telemetry unavailable | Omit telemetry or default to non-degraded |
| `DECISION_QUALITY_ENABLED=false` | Do not attach `decision_quality` to the record at all; `actionable_recommendation` and `evidence_breakdown` continue to work independently |

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
- WAIT and AVOID surface BOTH support and oppose columns (no recommendation-side filtering)
- empty `evidence_breakdown` produces `consensus_level = "none"` and downgrades raw YES/NO to WAIT
- missing `actionable_recommendation` still emits `decision_quality` with `raw_direction = "WAIT"`
- two-stage downgrade: Stage A rule 1 fires before Stage A rule 2 when both conditions hold
- Stage B escalates WAIT to AVOID when `risk_level == "high"` and `consensus_level` in `("low", "none")` (even if Stage A already set WAIT)
- Stage B can escalate raw YES to AVOID directly when no Stage A rule fired (high risk + low consensus + raw YES/NO)
- `decision_rationale_zh` does NOT contain market pricing or edge prose (separation from `actionable_recommendation.rationale`)
- `EvidenceBreakdownItem.direction` rejects values outside `{support, oppose, neutral}` (vocabulary lock — see Type Lock below)
- `DECISION_QUALITY_ENABLED=false` leaves the record without a `decision_quality` key
- `build_decision_quality` raises nothing — even on adversarial input (all-None, all-empty, all-malformed), it returns a well-formed block
- audit layer isolation: `actionable_recommendation` dict is byte-equal before and after the call (deep equality, not just identity)

#### Vocabulary Lock — Type-Level Enforcement

`EvidenceBreakdownItem.direction` MUST be typed as:

```python
direction: Literal["support", "oppose", "neutral"]
```

NOT `direction: str`. The Pydantic `Literal` constraint makes invalid
values (e.g., `"YES"`, `"LONG"`, `"buy"`) raise `ValidationError` at model
construction time, before they ever reach `decision_quality_service`.

The unit test asserts this by attempting `EvidenceBreakdownItem(direction="YES")`
and expecting `ValidationError`:

```python
with self.assertRaises(ValidationError):
    EvidenceBreakdownItem(direction="YES", ...)
```

#### Banned Vocabulary Test — Independent Pure-Function Target

The banned-vocabulary invariant is verified DIRECTLY on
`build_decision_quality` output, not via the report blob. This avoids the
no-op trap where `decision_quality` is not serialized into the report and
the test silently passes:

```python
def test_decision_quality_rationale_avoids_banned_vocab(self):
    rec = build_decision_quality(
        recommendation={"direction": "YES", "risk_level": "medium"},
        evidence_breakdown=[{...supporting...}, {...opposing...}],
        enabled=True, max_items=3,
        high_threshold=0.40, medium_threshold=0.20,
    )
    blob = (rec["decision_rationale_zh"] + " " +
            (rec["downgrade_reason"] or "") + " " +
            " ".join(item["rationale_zh"] for item in rec["supporting_evidence"]) +
            " ".join(item["rationale_zh"] for item in rec["opposing_evidence"])).lower()
    for banned in ("long", "short", "buy", "sell", "position", "kelly", "order"):
        self.assertNotIn(banned, blob)
```

The existing `test_report_uses_event_vocabulary_only` test (in
`test_decision_report_service.py`) is ALSO extended to cover
`decision_quality.decision_rationale_zh` and `downgrade_reason` — this
verifies the report path stays clean too. Both tests run in tandem; if
`decision_quality` is not serialized into the report, the report-side test
becomes a no-op but the pure-function test still catches the leak.

### Integration Tests

Add focused tests for:

- `analyze_event()` attaches `decision_quality` when enabled
- decision report passes through `decision_quality`
- raw `ai_probability` is unchanged by decision quality
- raw `evidence_profile` is unchanged
- `actionable_recommendation.direction` is byte-equal before and after `decision_quality_service` runs (no-writeback regression)
- `actionable_recommendation.rationale` is byte-equal before and after (no-writeback regression)
- market downgrade reason appears when spread is too wide
- Metaculus / `prediction_question` sources do not produce a `market_quality` block (Phase 2)

### Regression Tests

Existing tests around these areas must continue to pass:

- `test_evidence_aggregation_service`
- `test_event_intelligence_service`
- `test_decision_report_service`
- `test_events_routes`
- banned vocabulary tests (extended to cover `decision_rationale_zh` and `downgrade_reason`)

## Config Flags

All flags default to OFF to preserve backward compatibility with the existing
engine. Operators opt-in per phase after validating behavior on their data.

```python
# Phase 1 — Decision Explanation + Conflict Layer (default OFF)
DECISION_QUALITY_ENABLED=false
DECISION_QUALITY_MAX_EVIDENCE_ITEMS=3
DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD=0.40
DECISION_QUALITY_MEDIUM_CONFLICT_THRESHOLD=0.20

# Phase 2 — Market Quality Layer (default OFF)
MARKET_QUALITY_ENABLED=false
MARKET_MAX_SPREAD_PCT=12
MARKET_MIN_LIQUIDITY=1000
MARKET_MIN_VOLUME=1000
MARKET_STALE_AFTER_MINUTES=180

# Phase 3+ — Long-term features (default OFF, opt-in only)
PREDICTION_OUTCOME_TRACKING_ENABLED=false
SOURCE_RELIABILITY_ENABLED=false
LLM_TELEMETRY_ENABLED=false
```

### Audit Layer Isolation

`decision_quality` is a pure audit/explanation layer. When enabled, it MUST
NOT feed back into or alter any of the following fields:

- `ai_probability` (raw LLM estimate)
- `evidence_profile` (sentiment-driven evidence summary)
- `regression_to_market` (calibration shrinkage)
- `actionable_recommendation` (raw recommendation — direction, confidence,
  edge, risk_level, rationale, calibration_status)

`decision_quality` reads these fields as inputs and produces overlay outputs
(`displayed_direction`, `downgrade_reason`, `decision_rationale_zh`). The
data flow is one-way:

```text
actionable_recommendation + evidence_breakdown
  -> decision_quality_service
  -> decision_quality (overlay only, no writeback)
```

This mirrors the isolation contract that `evidence_breakdown` already follows,
and preserves the project's hard constraint that audit layers never mutate
the probability/recommendation path.

Long-term features default off until storage and data retention behavior are explicit.

## Acceptance Criteria

Phase 1 acceptance:

1. Event records can include a `decision_quality` block.
2. Decision reports can pass through `decision_quality`.
3. YES/NO explanations cite concrete supporting and opposing evidence.
4. High conflict downgrades strong recommendations to WAIT with a clear reason.
5. Raw `ai_probability` and `evidence_profile` behavior is unchanged.
6. No generated rationale contains banned vocabulary.
7. `raw_direction` always mirrors `actionable_recommendation.direction` at
   build time; `displayed_direction` is the only field that may diverge.
8. `actionable_recommendation.direction` is NEVER mutated by
   `decision_quality_service` (verified by a regression test asserting
   byte-equal values before/after the call).
9. When `actionable_recommendation` is `None`, `decision_quality` is still
   emitted with `raw_direction = "WAIT"` and non-empty
   `decision_rationale_zh` explaining the missing input.
10. When `evidence_breakdown` is empty, `decision_quality` is still emitted
    with `consensus_level = "none"` and an explicit downgrade reason for
    raw YES/NO directions.
11. `DECISION_QUALITY_ENABLED=false` (default) leaves the record byte-identical
    to a record built without the feature (no `decision_quality` key present).

Phase 2 acceptance:

1. Event records can include a `market_quality` block.
2. Wide spread or low liquidity can downgrade the displayed recommendation.
3. Missing market fields do not crash discovery or analysis.
4. Market downgrade reasons are explicit.
5. A market adapter field audit is completed BEFORE Phase 2 implementation,
   documenting the actual availability of `bid`, `ask`, `spread`, `volume`,
  `liquidity`, and `last_updated` across Polymarket, Kalshi, and Metaculus
   adapters. Where a field is unavailable, the adapter returns `None` and
   `market_quality` records the sub-score as `unknown` rather than failing.
6. Metaculus / `prediction_question` sources do not produce a
   `market_quality` block because they are not `prediction_market` sources.

Later phase acceptance:

1. Prediction snapshots are immutable.
2. Resolved outcomes can produce Brier scores.
3. Source reliability applies only after minimum sample thresholds.
4. LLM telemetry can identify degraded-mode events.
5. Phase 3 snapshots are written in the SAME transaction as
   `freeze_prediction` so that every frozen prediction has a matching
   immutable snapshot. Pre-existing frozen predictions (written before
   Phase 3) MUST be backfilled by a one-time migration script; the snapshot
   store MUST NOT use `freeze_prediction`'s idempotent skip path to avoid
   leaving gaps.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Too much scope for one implementation | Implement Phase 1 first; keep phases 2-5 as separate plans |
| Explanation becomes another black box | Use deterministic templates first, not a new LLM call |
| Downgrades hide raw signal | Preserve raw recommendation and add explicit overlay fields (`raw_direction` + `displayed_direction`) |
| Market thresholds are arbitrary | Put thresholds behind config and test boundary cases |
| Calibration needs data volume | Default outcome tracking off until storage policy is clear |
| Source reliability overfits sparse data | Require minimum sample thresholds before applying |
| Frontend grows noisy | Backend fields first; UI display should be a separate design |
| Two direction vocabularies confused in code | Document the `support/oppose` vs `YES/NO/WAIT/AVOID` distinction in code comments at every selection site; add a unit test that fails if any `EvidenceBreakdownItem.direction` value is set to `YES`/`NO`/`WAIT`/`AVOID` |
| `actionable_recommendation.rationale` and `decision_rationale_zh` overlap or contradict | Document ownership boundary (action vs audit); assert in tests that `decision_rationale_zh` does not contain market pricing or edge prose |
| Downgrade rules cascade unpredictably | First-match-wins ordering with documented rule precedence; the risk escalation rule is the only allowed WAIT→AVOID escalation |
| Metaculus `liquidity=0` triggers spurious `thin_market_flag` | Phase 2 explicitly gates `market_quality` to `prediction_market` sources only; Metaculus produces `prediction_question`, so it omits the block entirely |
| Phase 3 snapshot gap for pre-existing frozen predictions | One-time migration script backfills snapshots; snapshot write bypasses `freeze_prediction` idempotent skip |

## Recommended First Implementation

Start with Phase 1 only:

1. Add `decision_quality_service.py`.
2. Add `DecisionQuality` model to `models/event.py`.
3. Attach `decision_quality` in `analyze_event()` after `evidence_breakdown` and `actionable_recommendation` exist.
4. Pass `decision_quality` through `decision_report_service`.
5. Add unit and integration tests.
6. Add a regression test that captures `actionable_recommendation.direction` BEFORE calling `decision_quality_service`, then asserts it is byte-equal AFTER the call (locks the no-writeback invariant).
7. Extend the existing banned-vocabulary invariant test (`test_report_uses_event_vocabulary_only`) to include `decision_quality.decision_rationale_zh` and `decision_quality.downgrade_reason` in the screened blob.
8. Add a unit test that fails if any `EvidenceBreakdownItem.direction` value is set to a value outside `{support, oppose, neutral}` (locks the two-vocabulary separation at the model boundary).

This produces the highest immediate quality improvement while keeping the probability engine stable.

## Phase 1 Frontend Display Policy

Phase 1 is backend-first, but the contract for frontend consumption MUST be
defined now so the backend fields are shaped correctly. Frontend rendering
itself is a separate design and is NOT in Phase 1 scope.

### `get_displayed_direction(record)` Utility

All frontend direction reads MUST go through a single utility function
(`utils/eventDirection.ts` or equivalent) that implements the fallback
chain:

```typescript
function getDisplayedDirection(record: EventRecord): Direction {
    // 1. decision_quality present and downgraded → use displayed_direction
    if (record.decision_quality?.downgraded) {
        return record.decision_quality.displayed_direction;
    }
    // 2. decision_quality present, not downgraded → raw_direction (== actionable_recommendation.direction)
    if (record.decision_quality) {
        return record.decision_quality.raw_direction;
    }
    // 3. no decision_quality → fall back to actionable_recommendation.direction
    if (record.actionable_recommendation) {
        return record.actionable_recommendation.direction;
    }
    // 4. nothing → WAIT (safe default)
    return "WAIT";
}
```

This centralizes the fallback logic so individual components don't
re-implement it (and get it wrong).

### Display Slots

| UI Slot | Source Field | When |
| --- | --- | --- |
| Main direction badge | `getDisplayedDirection(record)` | always |
| Downgrade notice | `decision_quality.downgrade_reason` | when `downgraded == true` |
| Why-this-direction panel | `decision_quality.decision_rationale_zh` | when `decision_quality` present |
| Supporting evidence list | `decision_quality.supporting_evidence` | when present (top 3) |
| Opposing evidence list | `decision_quality.opposing_evidence` | when present (top 3) |
| All evidence (collapsed) | `evidence_breakdown` | existing UI, unchanged |
| Raw direction (audit view) | `actionable_recommendation.direction` | developer/admin only |

`evidence_breakdown` (full audit list) and `decision_quality.supporting_evidence`
/ `opposing_evidence` (top 3 decision drivers) are BOTH displayed, but in
different panels — `evidence_breakdown` is the "all evidence" collapsed
section, supporting/opposing is the "why this direction" main view. This
avoids redundancy and makes the decision rationale scannable.

### `reversal_triggers` — Phase 1 Decision

`reversal_triggers` is defined in the schema but Phase 1 generates it as
an EMPTY list `[]`. Rationale:

- Template-generated triggers (e.g., "if official source denies key fact")
  are too generic to be actionable — users see them as noise.
- LLM-generated triggers require a new LLM call, which violates Phase 1's
  "no new LLM" constraint.
- A `[]` value is honest: the system isn't yet producing specific triggers.

Phase 1.5 (a follow-up to Phase 1) may add LLM-generated triggers that
reference specific source names and titles from `evidence_breakdown`, but
only after Phase 1 stabilizes and user feedback confirms the value.

## Phase 1 Observability

`decision_quality` is a pure audit layer, but its behavior MUST be
observable so operators can tune thresholds and detect regressions.

### Core Metrics (5)

| Metric | Definition | Alert |
| --- | --- | --- |
| `decision_quality_downgrade_rate` | `downgraded == true` count / total count | > 0.5 (thresholds too strict) or < 0.01 (rules never fire) |
| `decision_quality_consensus_distribution` | histogram of `consensus_level` (high/medium/low/none) | `none` > 0.3 (evidence_breakdown often empty) |
| `decision_quality_rule_fire_count{rule="stage_a_1\|stage_a_2\|stage_a_3\|stage_a_4\|risk_escalation"}` | counter per rule | stage_a_1 fires > 80% of downgrades (other rules never trigger) |
| `decision_quality_build_failure_count` | try/except fallback fires | any non-zero value (bug or malformed data) |
| `decision_quality_latency_ms` | p50/p95/p99 of `build_decision_quality` call | p99 > 50ms (regression on large evidence lists) |

These metrics are emitted through the project's existing metrics or logging
facility. Prometheus counters/histograms are acceptable when the dependency
already exists, but Phase 1 MUST NOT introduce a new observability stack just
for this feature. Metric emission failures are silently swallowed:
observability must never block the audit layer.

### Threshold Tuning Workflow

1. Enable `DECISION_QUALITY_ENABLED=true` on a staging environment.
2. Run a representative discover batch (limit=20-50).
3. Check `decision_quality_downgrade_rate` and
   `consensus_distribution`.
4. If `downgrade_rate > 0.5`, raise
   `DECISION_QUALITY_HIGH_CONFLICT_THRESHOLD` (e.g., 0.40 → 0.50).
5. If `none` consensus > 0.3, investigate why `evidence_breakdown` is
   frequently empty — this is a `news_sentiment_service` issue, not a
   `decision_quality` issue.
6. After tuning, run for 7 days before promoting to production.

## Internationalization (i18n) Note

All `decision_rationale_zh`, `downgrade_reason`, and reversal-trigger
strings are currently hardcoded in Chinese (Simplified). This is
acceptable for the current user base.

When i18n becomes a requirement, these strings MUST be migrated to
message-key + parameterized-template form:

```python
# Before (Phase 1)
downgrade_reason = "证据冲突较高，强方向建议降级为 WAIT。"

# After (future i18n)
downgrade_reason = i18n.t(
    "decision_quality.high_conflict_downgrade",
    direction=raw_direction,
)
```

Phase 1 tests should assert on message CONTENT (substring matches like
"证据冲突") rather than byte-equality, so the i18n migration does not
break tests en masse. The banned-vocabulary invariant test already uses
substring matching (`assertNotIn`), so it survives i18n.

## Legal Disclaimer

`decision_rationale_zh` templates MUST end with a fixed disclaimer suffix:

```text
本分析仅供参考，不构成投资建议。
```

This applies to all generated rationale strings (initial, downgraded, and
error-fallback). The generation order is:

1. Generate the rationale body from deterministic templates.
2. Append the fixed disclaimer suffix.
3. Run the banned-vocabulary check on the full final string.

The disclaimer itself contains no banned words, but the check must run on
the final string to protect future template edits.

Rationale templates without the disclaimer are a P1 test failure (unit
test asserts `decision_rationale_zh.endswith("不构成投资建议。")`).

## Brier Score Direction Convention (Phase 3 Clarification)

Phase 3 Brier score is ALWAYS computed against the YES-outcome probability,
regardless of the recommended direction:

```text
brier_score = (estimated_probability_yes - outcome_indicator) ^ 2

where outcome_indicator = 1.0 if YES occurred, 0.0 if NO occurred
```

This is the standard scoring-rule convention. It means:
- A YES recommendation with `estimated=0.7` and YES outcome → Brier = 0.09
- A NO recommendation with `estimated=0.3` (i.e., 0.7 for NO) and NO outcome → Brier = 0.09 (same)
- The Brier score measures PROBABILITY accuracy, not direction accuracy

`direction_correct` is a SEPARATE field that records whether the
YES/NO direction matched the outcome. The two fields are independent —
a system can be well-calibrated (low Brier) but direction-wrong (boundary
case where 0.51 vs 0.49 flips direction).

### Edge Bucket Boundaries

`edge_bucket` uses half-open intervals `[low, high)`:

```text
[0, 5)    → "0-5"
[5, 10)   → "5-10"
[10, 20)  → "10-20"
[20, +∞)  → "20+"
```

Edge values exactly on a boundary (e.g., `edge=5.0`) belong to the UPPER
bucket (`5-10`, not `0-5`). Negative edges use absolute value for
bucketing but preserve sign for direction analysis.

### `provisional_act` Interaction

`provisional_act` is a value of the `decision` field (cold-start bypass
path), NOT of `actionable_recommendation.direction`. The two vocabularies
are separate:

- `decision` ∈ `{act, watch, provisional_act, skip}` — routing/quality gate
- `actionable_recommendation.direction` ∈ `{YES, NO, WAIT, AVOID}` — user-facing direction
- `decision_quality.raw_direction` / `displayed_direction` ∈ `{YES, NO, WAIT, AVOID}` — same as `actionable_recommendation.direction`

`decision_quality` does NOT read or write the `decision` field. A
`provisional_act` prediction still goes through `decision_quality` using
its `actionable_recommendation.direction` (YES/NO) as input — the
cold-start bypass affects routing, not the direction overlay.

## Caching Strategy

`decision_quality` is computed inside `analyze_event`, which is itself
cached via `set_cached_event` / `get_cached_event`. Two scenarios:

1. **Cache hit on `analyze_event`**: the entire record (including
   `decision_quality`) is returned from cache. `build_decision_quality`
   is NOT re-run. This is correct — the inputs (`actionable_recommendation`
   + `evidence_breakdown`) are immutable for a cached event.

2. **Config flag toggled after caching**: if `DECISION_QUALITY_ENABLED`
   was `false` when the event was cached, the cached record has no
   `decision_quality` key. If the flag is later turned `true`, the cached
   record still lacks the key.

**Phase 1 decision**: do NOT recompute `decision_quality` on cache hits.
The cache TTL is short enough (default 1 hour) that flag toggles propagate
within an acceptable window. Recomputing on every cache hit would defeat
the cache and add latency.

**Phase 1.5 follow-up** (if needed): if operators report stale
`decision_quality` after flag changes, add a cache-invalidation hook that
clears cached events when `DECISION_QUALITY_ENABLED` is toggled. This is
a config-change event, not a per-request check.

### Performance Budget

`build_decision_quality` is a synchronous pure function called once per
`analyze_event`. Performance budget:

| Metric | Budget | Rationale |
| --- | --- | --- |
| Single call (≤ 10 evidence items) | < 5ms | Negligible vs LLM latency (~3-10s per event) |
| Single call (100 evidence items) | < 20ms | Worst-case stress test |
| 50-event batch total | < 100ms | Must not dominate discover_events latency |

Do not enforce these budgets as ordinary unit-test hard assertions: wall-clock
timing is flaky on Windows and shared CI runners. Instead, add an optional
benchmark or diagnostic test that is skipped by default and can be run during
performance tuning. Functional tests should assert that top-N selection is
bounded and deterministic; performance diagnostics can flag whether
`sorted(..., key=lambda x: x["strength"] * x["credibility"])` should be
replaced with `heapq.nlargest`.
