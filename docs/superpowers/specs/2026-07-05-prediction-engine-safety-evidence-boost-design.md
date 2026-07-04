# Prediction Engine Safety & Evidence Boost v1

Date: 2026-07-05
Repository: `E:\Github\Prediction Market Reality Filter`
Status: Approved for spec; awaiting implementation plan

## Context

After settlement repair, the current resolved event sample shows poor edge-direction performance:

- 22 resolved prediction rows.
- Edge-direction correctness: 4/22 = 18.18%.
- Closed simulated-trade win rate: 3/19 = 15.79%.
- Most rows are exploratory `watch` or `provisional_act`, not calibrated `act` samples.
- The clearest failure mode is low market-probability events, especially Manifold markets in the 0-10% bucket, where the engine lifted the probability far above market and the events resolved NO.

The user explicitly chose to keep `watch` simulated trades enabled for now because they are useful for data collection. This design therefore optimizes the prediction engine without disabling `watch` exploration.

## Goals

1. Preserve `watch` simulated trading so the system continues collecting exploration data.
2. Reduce fake AI-vs-market edges, especially when market probability is very low and evidence is weak.
3. Make `unknown` category anchoring safer for prediction-market-sourced events.
4. Add a simple evidence-quality layer that constrains probability movement and confidence.
5. Make confidence represent tradable belief quality rather than LLM tone.
6. Keep the first implementation small, testable, and reversible.

## Non-Goals

- Do not disable `PAPER_TRADE_WATCH_ENABLED`.
- Do not stop `watch` rows from opening simulated trades.
- Do not delete or rewrite existing simulated trade history.
- Do not change settlement or auto-resolve behavior.
- Do not perform broad frontend UI work in v1.
- Do not perform a large prediction-engine rewrite.
- Do not stage unrelated working-tree changes.

## Proposed Approach

Implement a small combined safety layer:

1. Low-probability longshot guardrail.
2. Safer `unknown` category anchoring for prediction-market-sourced events.
3. Evidence-quality factor derived from existing signals.
4. Confidence caps tied to evidence quality, market range, category certainty, and guardrail activation.
5. Tests proving weak longshot evidence cannot create large artificial YES edges, while strong evidence can still move probability.

## Design Details

### 1. Keep `watch` Exploration

`watch` remains a valid simulated-trade data collection path. The engine should continue producing and storing watch decisions.

However, reports and diagnostics should conceptually distinguish:

- `act`: calibrated trading decision.
- `provisional_act`: cold-start exploratory action.
- `watch`: exploratory observation / simulated trade.

This v1 design does not require UI changes, but implementation should avoid treating watch results as evidence that the engine is production-calibrated.

### 2. Low-Probability Longshot Guardrail

#### Problem

For markets below 10%, especially prediction-market-sourced events with weak or unknown evidence, the current engine can lift probabilities from single digits to 30%+. Current resolved data shows this has been the most obvious failure mode.

#### Rule

When all of the following are true:

- the event came from a prediction market or has a market probability baseline;
- `market_probability < 10`;
- the AI probability is substantially above the market probability;
- evidence quality is not `strong`;

then cap the upward probability move.

Initial cap guidance:

- If `market_probability < 5`, weak or mixed evidence should usually cap AI probability near market + 8 to 12 percentage points.
- If `5 <= market_probability < 10`, weak or mixed evidence should usually cap AI probability near market + 12 to 18 percentage points.
- Solid evidence may loosen the cap modestly.
- Strong evidence may bypass or substantially loosen the cap, but the engine must record that strong evidence justified the move.

The implementation should avoid hard-coding too many magic branches in scattered places. Prefer one helper that returns both the adjusted probability and an explanation payload.

#### Expected Behavior

Example weak longshot:

- Market: 3.8%.
- Raw AI: 30.7%.
- Category: unknown.
- Evidence: weak or mixed.
- Expected adjusted AI: not 30%+; capped near a conservative longshot range.

Example strong longshot:

- Market: 4%.
- Raw AI: 31%.
- Evidence: multiple direct, recent, credible sources with opposing evidence considered.
- Expected adjusted AI: may exceed the weak cap, but should still be explainable.

### 3. Safer `unknown` Category Anchoring

#### Problem

`unknown` currently behaves like a maximum-entropy 50% prior. For prediction-market-sourced events, that can pull low market probabilities toward 50%, creating fake YES edges.

#### Rule

For prediction-market-sourced events with `base_rate_category == "unknown"`, do not use a static 50% prior as the primary anchor.

Preferred behavior:

- Treat the market probability as the safest baseline until the event category is known or a calibrated category prior exists.
- If category is unknown and evidence is weak, probability movement away from market should be compressed.
- If category is known and has calibrated history, existing base-rate anchoring may still apply.

### 4. Evidence-Quality Factor

Add a simple evidence-quality factor using existing inputs. It should not require a new data source in v1.

Candidate inputs:

- `news_quality_score`.
- `evidence_profile`.
- `semantics_profile`.
- source reliability if already available at the point of calculation.
- market microstructure or priced-in risk if already available.
- whether evidence is direct vs indirect.
- whether opposing evidence is present or considered.
- freshness of evidence if available.

Output should include both a numeric factor and a bucket:

- `weak`.
- `mixed`.
- `solid`.
- `strong`.

Suggested semantics:

| Bucket | Meaning | Engine Effect |
| --- | --- | --- |
| `weak` | thin, indirect, stale, low-credibility, or one-sided evidence | strongly compress probability edge and confidence |
| `mixed` | some useful information but direction is not clean | mildly/moderately compress edge and confidence |
| `solid` | credible direct evidence or multi-source support | allow moderate edge |
| `strong` | direct, recent, credible, multi-source, with counterevidence considered | allow larger edge |

This factor should be deterministic and testable.

### 5. Confidence Caps

#### Problem

Confidence should not be high merely because the LLM explanation sounds confident. It should reflect whether the system has enough evidence quality, market quality, category certainty, and historical support to trust the edge.

#### Rule

Apply confidence caps after raw confidence calculation.

Potential caps:

- If low-probability guardrail triggers, cap confidence around 55-65 unless evidence is strong.
- If category is unknown and evidence is weak, cap confidence around 50-60.
- If evidence is weak, high confidence should be impossible.
- If market baseline exists and segment history has not shown AI beats the market, high confidence should be unavailable.

Implementation should make the cap reason visible in diagnostic output when possible.

### 6. Segment Skill Follow-Up

Current `segment_skill` appears to measure AI against random rather than requiring AI to beat the market baseline. The long-term decision gate should trust a segment only when AI has market-relative skill.

For v1, this is optional if the database fields make it more than a small change. If included, the rule should be:

- If mean AI Brier is not better than mean market Brier, segment trust must not unlock high trust.
- If market baseline is unavailable, keep conservative dormant behavior.

If this is not included in v1 implementation, create a separate follow-up item rather than forcing a broad schema change.

## Testing Strategy

Start with failing tests.

Minimum tests:

1. Weak low-probability event:
   - market probability in 1-5% range;
   - raw AI probability around 30%;
   - category unknown;
   - weak or mixed evidence;
   - adjusted probability is capped and confidence is not high.

2. Moderate low-probability event:
   - market probability in 5-10% range;
   - weak evidence;
   - adjusted probability does not jump to 30%+.

3. Strong evidence exception:
   - low market probability;
   - strong evidence bucket;
   - probability may move more than weak cap;
   - explanation indicates evidence strength justified the move.

4. Unknown prediction-market anchor:
   - prediction-market-sourced event;
   - category unknown;
   - market probability low;
   - base-rate anchoring does not pull toward 50% by default.

5. Confidence cap:
   - weak evidence and/or guardrail activation prevents high confidence.

Optional tests:

6. Segment skill market-relative trust:
   - AI Brier worse than market Brier produces low or zero trust.
   - AI Brier better than market Brier can produce positive trust when sample count is sufficient.

## Likely Files

Implementation will likely touch:

- `backend/app/services/probability_engine_service.py`
- `backend/app/services/ai_analysis_service.py`
- `backend/app/services/base_rate_service.py`
- `backend/tests/test_ai_analysis_service.py`
- possibly `backend/tests/test_probability_engine_service.py`
- possibly `backend/tests/test_base_rate_service.py`
- optionally `backend/app/memory/prediction_store.py` and `backend/tests/test_prediction_store.py` if segment skill is included

## Implementation Constraints

- Use CodeGraph before grep or direct file reads when locating code.
- Keep changes surgical.
- Do not revert settlement fixes.
- Do not stage unrelated working-tree changes.
- Do not kill unknown Python or Node processes.
- If live market APIs are needed, request escalation because network access is restricted.

## Acceptance Criteria

- `watch` simulated trading remains enabled and functionally unchanged.
- Low-probability weak-evidence markets cannot create large uncapped AI probabilities.
- Prediction-market-sourced unknown-category events are not anchored toward 50% by default.
- Confidence is capped under weak evidence, unknown category, or guardrail activation.
- Tests cover the new behavior.
- Existing settlement tests continue to pass.
- The implementation produces diagnostic reasons for guardrail or confidence cap decisions where practical.
