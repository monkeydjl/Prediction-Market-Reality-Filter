# Conclusion Challenge Gate Design

Date: 2026-07-04

## 1. Architecture Boundary

Add a shared Conclusion Challenge Gate for World Cup predictions and the main event intelligence module.

The module is a post-conclusion challenge layer. It does not predict, remodel, persist, enqueue review items, or read settings directly. It receives a structured conclusion plus evidence and calculation trace, then returns a structured challenge result.

Core interface:

```python
challenge_conclusion(input: ChallengeInput) -> ChallengeResult
```

The module answers:

- Why was this calculated this way?
- Is the conclusion supported by evidence?
- Is there strong counterevidence that should block a strong conclusion?
- Is the confidence calibrated to the available evidence?
- Is the conclusion actionable, or should it be downgraded?

Integration seams:

- World Cup: run after `prediction_result` is produced in `run_prediction_pipeline()` and before writing `MatchPrediction` / `PredictionHistory`.
- Main event intelligence: run after `final_displayed_direction` and guardrails are produced in `event_intelligence_service`, before final output/review handling.

Version 1 is a single challenge gate, not a multi-API debate system. Multi-agent debate remains a later enhancement for high-risk or high-value conclusions.

## 2. Challenge Semantics

The gate performs five fixed checks.

### calculation_rationale_check

Checks whether the calculation path supports the conclusion.

World Cup examples:

- `outcome_probabilities`, predicted score, confidence, engine, data quality, and calibration should be internally consistent.
- A high-confidence home-win conclusion should not be based on weak home-win probability.
- High confidence with fallback/mock data should be challenged.

Main event examples:

- `baseline`, `estimated`, `change`, `actionable_recommendation.direction`, and `final_displayed_direction` should agree.
- A small probability edge should not become a strong action conclusion.

### evidence_support_check

Checks whether supporting evidence is strong enough.

World Cup evidence includes Elo, odds, team stats, injuries/player status, group context, weather/schedule, explanation contributions, and data quality metrics.

Main event evidence includes `evidence_breakdown`, `source_reliability`, `market_quality`, `decision_quality`, news context, and filtered articles.

Failure conditions include no supporting evidence, only neutral evidence, low-credibility support, or support that points away from the conclusion.

### counterevidence_check

Checks whether strong counterevidence exists.

World Cup examples:

- Odds strongly disagree with the model.
- Elo direction conflicts with the predicted winner.
- Key player absences contradict a high-confidence conclusion.
- Different engines disagree sharply.
- Data quality is fallback/mock.

Main event examples:

- `evidence_breakdown` refutes the conclusion.
- High-reliability sources oppose the direction.
- Market quality says the market is thin or not executable.
- Domain reliability indicates poor source history.
- Existing guardrails fired.

### confidence_calibration_check

Checks whether the stated confidence is too high for the data.

Failure conditions include unreliable calibration, insufficient samples, poor historical performance for similar cases, or quality overlays indicating low confidence.

### actionability_check

Checks whether the conclusion is actionable.

Main event failure conditions include small expected edge, insufficient liquidity, poor execution quality, excessive risk flags, or near-term reversal triggers.

World Cup failure conditions do not block prediction display, but they can block high-confidence presentation and cap confidence.

## 3. Verdict Aggregation

The final verdict is deterministic. The LLM critic, when enabled, may provide notes and missed counterarguments, but it does not own the final decision.

Verdicts:

- `pass`: no meaningful challenge failures.
- `pass_with_warnings`: weak concerns exist, but no downgrade is required.
- `revise`: the conclusion needs one recalculation/revision attempt.
- `reject`: strong conclusion should not be emitted.
- `insufficient_evidence`: evidence is too thin for a strong conclusion.

Aggregation:

- Any hard failure produces `reject` unless the failure is specifically evidence absence, which produces `insufficient_evidence`.
- Two or more soft failures produce `revise`.
- Warnings only produce `pass_with_warnings`.
- No failures produce `pass`.

The gate must be testable without calling an LLM.

## 4. Failure Handling

Version 1 allows at most one automatic revision loop.

General flow:

```text
initial conclusion
  -> challenge gate
      pass
        -> output normally
      pass_with_warnings
        -> output with challenge summary and possible confidence cap
      revise
        -> recalculate once if attempt_count == 0
        -> otherwise downgrade/review
      reject / insufficient_evidence
        -> do not output a strong conclusion
```

World Cup handling:

- `pass`: save original prediction.
- `pass_with_warnings`: save prediction, write challenge result, optionally cap confidence.
- `revise`: run one conservative retry or switch to a conservative engine path; if still failing, save conservative prediction and mark review required.
- `reject`: do not preserve high-confidence semantics; cap confidence to a conservative range, write challenge result, and mark review required.
- `insufficient_evidence`: save prediction as low-confidence/provisional and block high-confidence selection.

Main event handling:

- `pass`: preserve `final_displayed_direction`.
- `pass_with_warnings`: preserve direction and write `conclusion_challenge`.
- `revise`: recompute once; if still failing, downgrade to `WAIT`.
- `reject`: set `final_displayed_direction = "WAIT"` and set `final_downgrade_reason = "conclusion_challenge_rejected: ..."`.
- `insufficient_evidence`: set `final_displayed_direction = "WAIT"` and set `final_downgrade_reason = "insufficient_evidence_for_strong_conclusion"`.

Loop prevention:

```text
challenge_attempt_count: 0 | 1
challenge_recomputed: bool
```

If `attempt_count >= 1`, automatic recalculation is not allowed. The system must downgrade or enqueue review instead.

If the challenge gate itself fails:

- Feature disabled: no behavior change.
- Critic LLM/API failure: deterministic checks still run.
- Deterministic checks pass but critic fails: return `pass_with_warnings`.
- Deterministic checks fail: honor the deterministic failure.
- Rule execution exception: log warning, do not block production, and attach `challenge_error` when possible.

## 5. Interface And Adapters

The shared module exposes one external interface:

```python
challenge_conclusion(input: ChallengeInput) -> ChallengeResult
```

`ChallengeInput` shape:

```python
{
    "domain": "world_cup" | "event_intelligence",
    "subject": {
        "id": str,
        "title": str,
        "type": "match_prediction" | "event_recommendation",
    },
    "conclusion": {
        "direction": str | None,
        "predicted_score": dict | None,
        "probabilities": dict | None,
        "confidence": float | None,
        "recommended_action": str | None,
    },
    "calculation_trace": {
        "method": str,
        "engine_used": str | None,
        "weights": dict,
        "key_scores": dict,
        "calibration": dict | None,
    },
    "evidence": {
        "supporting": list[dict],
        "opposing": list[dict],
        "neutral": list[dict],
        "data_quality": dict,
        "source_reliability": dict | None,
    },
    "risk": {
        "level": str | None,
        "flags": list[str],
        "execution_constraints": dict,
    },
    "options": {
        "max_recompute_attempts": 1,
        "strictness": "normal" | "strict",
        "allow_llm_critic": bool,
    },
    "attempt_count": int,
}
```

`ChallengeResult` shape:

```python
{
    "verdict": "pass" | "pass_with_warnings" | "revise" | "reject" | "insufficient_evidence",
    "required_action": (
        "allow_output"
        | "cap_confidence"
        | "recalculate_once"
        | "downgrade_to_wait"
        | "enqueue_review"
    ),
    "failed_checks": [
        {
            "check": "evidence_support",
            "severity": "soft_fail" | "hard_fail",
            "reason": str,
            "details": dict,
        }
    ],
    "warnings": list[dict],
    "confidence_adjustment": {
        "cap": float | None,
        "reason": str | None,
    },
    "challenge_summary": str,
    "critic_notes": {
        "missing_counterarguments": list[str],
        "weak_assumptions": list[str],
        "evidence_gaps": list[str],
    },
    "attempt_count": int,
}
```

World Cup adapters:

```python
build_world_cup_challenge_input(match, prediction_result, factors) -> ChallengeInput
apply_world_cup_challenge_result(prediction_result, challenge_result) -> dict
```

Main event adapters:

```python
build_event_challenge_input(record) -> ChallengeInput
apply_event_challenge_result(record, challenge_result) -> None
```

The shared module does not import SQLAlchemy models, event stores, review queue stores, or settings. Adapters and orchestrators handle I/O and persistence.

LLM critic seam:

```python
critic_adapter.review(input) -> CriticNotes
```

Adapters:

- `NullCriticAdapter`: no LLM call, deterministic and test-friendly.
- `LLMCriticAdapter`: asks an LLM to identify missed counterarguments, weak assumptions, and evidence gaps.

## 6. Configuration And Storage

Configuration:

```python
CONCLUSION_CHALLENGE_ENABLED = false
CONCLUSION_CHALLENGE_LLM_CRITIC_ENABLED = false
CONCLUSION_CHALLENGE_STRICTNESS = "normal"
CONCLUSION_CHALLENGE_MAX_RECOMPUTE_ATTEMPTS = 1
WORLD_CUP_CHALLENGE_ENABLED = false
EVENT_CHALLENGE_ENABLED = false
```

All flags default to disabled to preserve current behavior.

Strictness:

- `normal`: hard failures and multiple soft failures affect output.
- `strict`: high-risk or high-value events have higher evidence requirements and lower confidence caps.

Automatic strictness escalation:

- Main events: high risk, high suggested allocation, or `final_displayed_direction in {"YES", "NO"}`.
- World Cup: high confidence, high-confidence selection, knockout matches, or non-real data quality.

Storage:

World Cup:

```python
prediction_result["factors"]["challenge_result"] = {
    "verdict": "...",
    "required_action": "...",
    "failed_checks": [...],
    "challenge_summary": "...",
}
```

Main events:

```python
record["conclusion_challenge"] = {
    "verdict": "...",
    "required_action": "...",
    "failed_checks": [...],
    "challenge_summary": "...",
}
```

Review queue trigger:

```text
conclusion_challenge_failed
```

Enqueue conditions:

- Main events: `reject`, `insufficient_evidence`, or `revise` after retry failure.
- World Cup: `reject`, retry failure, or high-confidence prediction rejected by the challenge gate.

## 7. Testing Strategy

Pure function tests:

- All checks pass -> `pass`.
- Warnings only -> `pass_with_warnings`.
- One hard failure -> `reject`.
- Two soft failures -> `revise`.
- Strong conclusion without evidence -> `insufficient_evidence`.
- Critic unavailable with deterministic pass -> `pass_with_warnings`.
- Critic unavailable with deterministic failure -> deterministic failure wins.

Each fixed check covers:

- pass
- soft failure
- hard failure
- missing/invalid input

World Cup adapter tests:

- `prediction_result` maps into `ChallengeInput`.
- `factors` and `explanation_contributions` are included in evidence/calculation trace.
- `data_quality` and calibration are included.
- `pass` leaves prediction unchanged except challenge metadata.
- `pass_with_warnings` writes warnings and caps confidence when requested.
- `reject` caps confidence and blocks high-confidence semantics.
- challenge failure does not crash the pipeline.

Main event adapter tests:

- record maps into `ChallengeInput`.
- `final_displayed_direction`, `actionable_recommendation`, overlays, and `evidence_breakdown` are included.
- `pass` preserves final direction.
- `pass_with_warnings` writes `conclusion_challenge`.
- `reject` and `insufficient_evidence` downgrade to `WAIT`.
- review queue enqueue is best-effort and non-blocking.

Loop prevention tests:

- `attempt_count=0` + `revise` -> `recalculate_once`.
- `attempt_count=1` + `revise` -> downgrade/review.
- No path performs more than one automatic recalculation.

Regression tests:

- World Cup prediction pipeline tests.
- World Cup AI engine tests.
- Event intelligence service tests.
- Review queue detector tests.
- Guardrail service tests.
- Decision quality service tests.

Acceptance criteria:

- Feature flags off preserve current behavior.
- Strong conclusions include `challenge_result` / `conclusion_challenge` when enabled.
- Hard failures do not allow strong action conclusions.
- LLM critic failure does not block production.
- Downgrades have readable Chinese reasons.
- The gate is testable without network access or an LLM.
