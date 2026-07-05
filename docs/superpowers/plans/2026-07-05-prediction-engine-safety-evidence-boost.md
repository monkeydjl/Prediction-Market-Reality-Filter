# Prediction Engine Safety Evidence Boost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep `watch` simulated trading enabled while making the event prediction engine more conservative under weak longshot evidence, safer for unknown-category market events, and clearer about evidence quality and confidence caps.

**Architecture:** Add deterministic helper functions in `backend/app/services/probability_engine_service.py` for evidence quality, longshot guardrails, and confidence caps. Integrate those helpers in `backend/app/services/ai_analysis_service.py` without changing settlement, simulated trade creation, or frontend UI. Cover the behavior through focused `unittest` tests in `backend/tests/test_ai_analysis_service.py`.

**Tech Stack:** Python 3, FastAPI service modules, `unittest`, `unittest.mock.AsyncMock`, existing deterministic analysis helpers.

## Global Constraints

- Preserve `watch` simulated trading; do not disable `PAPER_TRADE_WATCH_ENABLED`.
- Do not stop `watch` rows from opening simulated trades.
- Do not delete or rewrite existing simulated trade history.
- Do not change settlement or auto-resolve behavior.
- Do not perform broad frontend UI work in v1.
- Do not perform a large prediction-engine rewrite.
- Do not stage unrelated working-tree changes.
- Use CodeGraph before grep or direct file reads when locating code.
- Do not revert settlement fixes.
- Do not kill unknown Python or Node processes.
- If live market APIs are needed, request escalation because network access is restricted.
- Segment-skill market-relative trust is excluded from this v1 plan because it is optional in the spec and may require broader prediction-store/schema work.

---

## File Structure

- Modify: `backend/app/services/probability_engine_service.py`
  - Owns deterministic probability math helpers.
  - Add `calculate_evidence_quality`, `apply_confidence_caps`, `apply_longshot_guardrail`, and `constrain_probability`.
  - Keep existing `clamp_probability` return type unchanged for backward compatibility.
- Modify: `backend/app/services/ai_analysis_service.py`
  - Orchestrates analysis.
  - Imports the new helpers.
  - Computes evidence quality before confidence is used.
  - Uses capped confidence for signal/risk/position/probability calculations.
  - Uses market probability as the effective prior for `unknown` category events.
  - Adds diagnostic fields to the returned analysis dict.
- Modify: `backend/tests/test_ai_analysis_service.py`
  - Adds deterministic unit tests for evidence quality, confidence caps, longshot guardrails, unknown-category anchoring, and analyze-market diagnostics.
  - Updates the full fallback contract test with the new diagnostic fields.

---

### Task 1: Add Evidence Quality Helper

**Files:**
- Modify: `backend/app/services/probability_engine_service.py`
- Modify: `backend/app/services/ai_analysis_service.py`
- Modify: `backend/tests/test_ai_analysis_service.py`

**Interfaces:**
- Consumes: existing `default_evidence_profile()`, `default_semantics_profile()`, and `_clamp()` in `probability_engine_service.py`.
- Produces: `calculate_evidence_quality(evidence_profile: dict[str, Any] | None, news_quality_score: float, semantics_profile: dict[str, Any] | None = None, priced_in_risk_score: int = 0) -> dict[str, Any]` returning `{factor: float, bucket: str, reasons: list[str]}`.

- [ ] **Step 1: Add failing imports and tests**

In `backend/tests/test_ai_analysis_service.py`, add `calculate_evidence_quality` to the existing import from `app.services.ai_analysis_service`. Then add these tests inside `ProbabilityMathTests` after `test_calculate_confidence_score`:

```python
    def test_calculate_evidence_quality_weak(self):
        quality = calculate_evidence_quality(
            evidence_profile={
                "evidence_direction": "support",
                "evidence_strength": 0.12,
                "conflict_score": 0.65,
                "freshness_score": 0.25,
                "resolution_relevance_score": 0.15,
                "source_count": 1,
            },
            news_quality_score=0.25,
            semantics_profile={"condition_type": "unknown", "ambiguity_score": 75},
            priced_in_risk_score=80,
        )
        self.assertEqual(quality["bucket"], "weak")
        self.assertLessEqual(quality["factor"], 0.35)
        self.assertIn("thin_or_indirect_evidence", quality["reasons"])
        self.assertIn("high_conflict", quality["reasons"])

    def test_calculate_evidence_quality_strong(self):
        quality = calculate_evidence_quality(
            evidence_profile={
                "evidence_direction": "support",
                "evidence_strength": 0.85,
                "conflict_score": 0.05,
                "freshness_score": 0.92,
                "resolution_relevance_score": 0.88,
                "source_count": 6,
            },
            news_quality_score=0.86,
            semantics_profile={"condition_type": "threshold", "ambiguity_score": 18},
            priced_in_risk_score=20,
        )
        self.assertEqual(quality["bucket"], "strong")
        self.assertGreaterEqual(quality["factor"], 0.75)
        self.assertIn("direct_relevant_evidence", quality["reasons"])
        self.assertIn("multi_source_support", quality["reasons"])
```

- [ ] **Step 2: Run test to verify failure**

Run from `backend/`:

```powershell
python -m unittest tests.test_ai_analysis_service.ProbabilityMathTests.test_calculate_evidence_quality_weak tests.test_ai_analysis_service.ProbabilityMathTests.test_calculate_evidence_quality_strong -v
```

Expected: FAIL with `ImportError` or `AttributeError` for `calculate_evidence_quality`.

- [ ] **Step 3: Export helper from adapter**

In `backend/app/services/ai_analysis_service.py`, add `calculate_evidence_quality` to the import from `app.services.probability_engine_service`.

- [ ] **Step 4: Implement helper**

In `backend/app/services/probability_engine_service.py`, insert this helper after `calculate_confidence_score`:

```python
def calculate_evidence_quality(
    evidence_profile: dict[str, Any] | None,
    news_quality_score: float,
    semantics_profile: dict[str, Any] | None = None,
    priced_in_risk_score: int = 0,
) -> dict[str, Any]:
    """Return a deterministic evidence-quality factor and bucket."""
    evidence = evidence_profile or default_evidence_profile()
    semantics = semantics_profile or default_semantics_profile()
    strength = _clamp(float(evidence.get("evidence_strength", 0.0)), 0.0, 1.0)
    relevance = _clamp(float(evidence.get("resolution_relevance_score", 0.0)), 0.0, 1.0)
    freshness = _clamp(float(evidence.get("freshness_score", 0.5)), 0.0, 1.0)
    conflict = _clamp(float(evidence.get("conflict_score", 0.0)), 0.0, 1.0)
    source_count = max(0, int(evidence.get("source_count", 0) or 0))
    news_quality = _clamp(news_quality_score, 0.0, 1.0)
    ambiguity = _clamp(float(semantics.get("ambiguity_score", 50)), 0.0, 100.0) / 100.0
    priced_in = _clamp(float(priced_in_risk_score), 0.0, 100.0) / 100.0
    source_score = _clamp(source_count / 5.0, 0.0, 1.0)
    factor = round(_clamp(
        strength * 0.24 + relevance * 0.20 + freshness * 0.14
        + (1.0 - conflict) * 0.14 + source_score * 0.12
        + news_quality * 0.10 + (1.0 - ambiguity) * 0.04
        + (1.0 - priced_in) * 0.02,
        0.0,
        1.0,
    ), 3)
    reasons: list[str] = []
    if strength < 0.25 or relevance < 0.25:
        reasons.append("thin_or_indirect_evidence")
    if conflict >= 0.55:
        reasons.append("high_conflict")
    if freshness < 0.35:
        reasons.append("stale_evidence")
    if source_count < 2:
        reasons.append("single_or_missing_source")
    if ambiguity >= 0.70:
        reasons.append("ambiguous_resolution")
    if priced_in >= 0.70:
        reasons.append("likely_priced_in")
    if strength >= 0.70 and relevance >= 0.70:
        reasons.append("direct_relevant_evidence")
    if source_count >= 5 and conflict <= 0.20:
        reasons.append("multi_source_support")
    if factor < 0.35:
        bucket = "weak"
    elif factor < 0.55:
        bucket = "mixed"
    elif factor < 0.75:
        bucket = "solid"
    else:
        bucket = "strong"
    return {"factor": factor, "bucket": bucket, "reasons": reasons}
```

- [ ] **Step 5: Run test to verify pass**

Run from `backend/`:

```powershell
python -m unittest tests.test_ai_analysis_service.ProbabilityMathTests.test_calculate_evidence_quality_weak tests.test_ai_analysis_service.ProbabilityMathTests.test_calculate_evidence_quality_strong -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```powershell
git add backend/app/services/probability_engine_service.py backend/app/services/ai_analysis_service.py backend/tests/test_ai_analysis_service.py
git commit -m "feat: score event evidence quality"
```

---

### Task 2: Add Longshot Probability Guardrail

**Files:**
- Modify: `backend/app/services/probability_engine_service.py`
- Modify: `backend/app/services/ai_analysis_service.py`
- Modify: `backend/tests/test_ai_analysis_service.py`

**Interfaces:**
- Consumes: `calculate_evidence_quality(...)` from Task 1 and existing `clamp_probability(...)`.
- Produces:
  - `apply_longshot_guardrail(market_probability: float, ai_probability: float, evidence_quality: dict[str, Any], has_strong_evidence: bool = False, base_rate_category: str = "unknown") -> dict[str, Any]` returning `{probability: float, triggered: bool, reason: str}`.
  - `constrain_probability(...) -> dict[str, Any]` returning probability plus evidence-quality and guardrail diagnostics.

- [ ] **Step 1: Add failing imports and tests**

In `backend/tests/test_ai_analysis_service.py`, add `apply_longshot_guardrail` and `constrain_probability` to the import from `app.services.ai_analysis_service`. Add these tests inside `ProbabilityMathTests`:

```python
    def test_apply_longshot_guardrail_caps_weak_low_probability_lift(self):
        result = apply_longshot_guardrail(
            market_probability=3.8,
            ai_probability=30.7,
            evidence_quality={"factor": 0.24, "bucket": "weak", "reasons": []},
            has_strong_evidence=False,
            base_rate_category="unknown",
        )
        self.assertTrue(result["triggered"])
        self.assertEqual(result["reason"], "low_probability_weak_evidence_cap")
        self.assertLessEqual(result["probability"], 15.8)

    def test_apply_longshot_guardrail_allows_strong_evidence_more_room(self):
        result = apply_longshot_guardrail(
            market_probability=4.0,
            ai_probability=31.0,
            evidence_quality={"factor": 0.82, "bucket": "strong", "reasons": []},
            has_strong_evidence=True,
            base_rate_category="unknown",
        )
        self.assertFalse(result["triggered"])
        self.assertEqual(result["probability"], 31.0)

    def test_constrain_probability_returns_diagnostics(self):
        result = constrain_probability(
            market_probability=3.8,
            ai_probability=30.7,
            confidence=0.58,
            narrative_type="factual",
            has_strong_evidence=False,
            evidence_profile={
                "evidence_direction": "support",
                "evidence_strength": 0.12,
                "conflict_score": 0.65,
                "freshness_score": 0.25,
                "resolution_relevance_score": 0.15,
                "source_count": 1,
            },
            priced_in_risk_score=80,
            semantics_profile={"condition_type": "unknown", "ambiguity_score": 75},
            news_quality_score=0.25,
            base_rate_category="unknown",
        )
        self.assertEqual(result["evidence_quality_bucket"], "weak")
        self.assertTrue(result["guardrail_triggered"])
        self.assertEqual(result["guardrail_reason"], "low_probability_weak_evidence_cap")
        self.assertLessEqual(result["probability"], 15.8)
```

- [ ] **Step 2: Run tests to verify failure**

Run from `backend/`:

```powershell
python -m unittest tests.test_ai_analysis_service.ProbabilityMathTests.test_apply_longshot_guardrail_caps_weak_low_probability_lift tests.test_ai_analysis_service.ProbabilityMathTests.test_apply_longshot_guardrail_allows_strong_evidence_more_room tests.test_ai_analysis_service.ProbabilityMathTests.test_constrain_probability_returns_diagnostics -v
```

Expected: FAIL with missing imports or missing functions.

- [ ] **Step 3: Export new helpers from adapter**

In `backend/app/services/ai_analysis_service.py`, add `apply_longshot_guardrail` and `constrain_probability` to the import from `app.services.probability_engine_service`.

- [ ] **Step 4: Implement longshot helpers**

In `backend/app/services/probability_engine_service.py`, insert these functions after `calculate_evidence_quality`:

```python
def apply_longshot_guardrail(
    market_probability: float,
    ai_probability: float,
    evidence_quality: dict[str, Any],
    has_strong_evidence: bool = False,
    base_rate_category: str = "unknown",
) -> dict[str, Any]:
    """Cap large upward moves on low-probability markets unless evidence is strong."""
    market = _clamp(market_probability, 0.0, 100.0)
    ai = _clamp(ai_probability, 0.0, 100.0)
    bucket = str((evidence_quality or {}).get("bucket") or "weak")
    category = (base_rate_category or "unknown").lower()
    if market >= 10.0 or ai <= market:
        return {"probability": round(ai, 2), "triggered": False, "reason": ""}
    if has_strong_evidence or bucket == "strong":
        return {"probability": round(ai, 2), "triggered": False, "reason": ""}
    if market < 5.0:
        max_lift = 12.0 if bucket == "solid" else 10.0
    else:
        max_lift = 18.0 if bucket == "solid" else 14.0
    if category == "unknown":
        max_lift -= 2.0
    cap = market + max_lift
    if ai <= cap:
        return {"probability": round(ai, 2), "triggered": False, "reason": ""}
    return {
        "probability": round(_clamp(cap, 0.0, 100.0), 2),
        "triggered": True,
        "reason": "low_probability_weak_evidence_cap",
    }


def constrain_probability(
    market_probability: float,
    ai_probability: float,
    confidence: float = 0.0,
    narrative_type: str = "",
    has_strong_evidence: bool = False,
    evidence_profile: dict[str, Any] | None = None,
    priced_in_risk_score: int = 0,
    semantics_profile: dict[str, Any] | None = None,
    news_quality_score: float = 0.0,
    base_rate_category: str = "unknown",
) -> dict[str, Any]:
    """Constrain probability and return diagnostics without changing clamp_probability."""
    evidence_quality = calculate_evidence_quality(
        evidence_profile=evidence_profile,
        news_quality_score=news_quality_score,
        semantics_profile=semantics_profile,
        priced_in_risk_score=priced_in_risk_score,
    )
    constrained = clamp_probability(
        market_probability=market_probability,
        ai_probability=ai_probability,
        confidence=confidence,
        narrative_type=narrative_type,
        has_strong_evidence=has_strong_evidence,
        evidence_profile=evidence_profile,
        priced_in_risk_score=priced_in_risk_score,
        semantics_profile=semantics_profile,
    )
    guardrail = apply_longshot_guardrail(
        market_probability=market_probability,
        ai_probability=constrained,
        evidence_quality=evidence_quality,
        has_strong_evidence=has_strong_evidence,
        base_rate_category=base_rate_category,
    )
    return {
        "probability": guardrail["probability"],
        "evidence_quality_factor": evidence_quality["factor"],
        "evidence_quality_bucket": evidence_quality["bucket"],
        "evidence_quality_reasons": evidence_quality["reasons"],
        "guardrail_triggered": guardrail["triggered"],
        "guardrail_reason": guardrail["reason"],
    }
```

- [ ] **Step 5: Integrate `constrain_probability` in `analyze_market`**

In `backend/app/services/ai_analysis_service.py`, replace the `evidence_constrained_probability = clamp_probability(...)` block with:

```python
    probability_constraint = constrain_probability(
        market_probability=market_probability,
        ai_probability=normalized["ai_probability"],
        confidence=confidence_score,
        narrative_type=narrative_type,
        has_strong_evidence=normalized["has_strong_evidence"],
        evidence_profile=evidence_profile,
        priced_in_risk_score=priced_in_risk_score,
        semantics_profile=semantics_profile,
        news_quality_score=news_quality_score,
        base_rate_category=base_rate.category,
    )
    evidence_constrained_probability = probability_constraint["probability"]
```

Add these fields to the returned dict after `"evidence_constrained_probability": evidence_constrained_probability,`:

```python
        "evidence_quality_factor": probability_constraint["evidence_quality_factor"],
        "evidence_quality_bucket": probability_constraint["evidence_quality_bucket"],
        "evidence_quality_reasons": probability_constraint["evidence_quality_reasons"],
        "probability_guardrail_triggered": probability_constraint["guardrail_triggered"],
        "probability_guardrail_reason": probability_constraint["guardrail_reason"],
```

- [ ] **Step 6: Update full fallback contract expected dict**

In `AnalyzeMarketContractTests.test_analyze_market_fallback_contract`, add these fields after `"evidence_constrained_probability": 51.66,`:

```python
                "evidence_quality_factor": 0.693,
                "evidence_quality_bucket": "solid",
                "evidence_quality_reasons": [],
                "probability_guardrail_triggered": False,
                "probability_guardrail_reason": "",
```

- [ ] **Step 7: Run tests to verify pass**

Run from `backend/`:

```powershell
python -m unittest tests.test_ai_analysis_service -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```powershell
git add backend/app/services/probability_engine_service.py backend/app/services/ai_analysis_service.py backend/tests/test_ai_analysis_service.py
git commit -m "feat: cap weak longshot probability moves"
```

---

### Task 3: Apply Confidence Caps from Evidence Quality

**Files:**
- Modify: `backend/app/services/probability_engine_service.py`
- Modify: `backend/app/services/ai_analysis_service.py`
- Modify: `backend/tests/test_ai_analysis_service.py`

**Interfaces:**
- Consumes: `calculate_evidence_quality(...)` from Task 1.
- Produces: `apply_confidence_caps(confidence: float, market_probability: float, base_rate_category: str, evidence_quality: dict[str, Any]) -> dict[str, Any]` returning `{confidence: float, reasons: list[str]}`.

- [ ] **Step 1: Add failing import and tests**

In `backend/tests/test_ai_analysis_service.py`, add `apply_confidence_caps` to the import from `app.services.ai_analysis_service`. Add these tests inside `ProbabilityMathTests`:

```python
    def test_apply_confidence_caps_weak_unknown_longshot(self):
        result = apply_confidence_caps(
            confidence=0.82,
            market_probability=4.0,
            base_rate_category="unknown",
            evidence_quality={"factor": 0.24, "bucket": "weak", "reasons": []},
        )
        self.assertEqual(result["confidence"], 0.55)
        self.assertIn("weak_evidence_cap", result["reasons"])
        self.assertIn("unknown_category_cap", result["reasons"])
        self.assertIn("low_probability_evidence_cap", result["reasons"])

    def test_apply_confidence_caps_solid_known_category_keeps_confidence(self):
        result = apply_confidence_caps(
            confidence=0.68,
            market_probability=42.0,
            base_rate_category="crypto_price",
            evidence_quality={"factor": 0.68, "bucket": "solid", "reasons": []},
        )
        self.assertEqual(result["confidence"], 0.68)
        self.assertEqual(result["reasons"], [])
```

- [ ] **Step 2: Run tests to verify failure**

Run from `backend/`:

```powershell
python -m unittest tests.test_ai_analysis_service.ProbabilityMathTests.test_apply_confidence_caps_weak_unknown_longshot tests.test_ai_analysis_service.ProbabilityMathTests.test_apply_confidence_caps_solid_known_category_keeps_confidence -v
```

Expected: FAIL with missing import or missing function.

- [ ] **Step 3: Export helper from adapter**

In `backend/app/services/ai_analysis_service.py`, add `apply_confidence_caps` to the import from `app.services.probability_engine_service`.

- [ ] **Step 4: Implement `apply_confidence_caps`**

In `backend/app/services/probability_engine_service.py`, insert this function after `apply_longshot_guardrail`:

```python
def apply_confidence_caps(
    confidence: float,
    market_probability: float,
    base_rate_category: str,
    evidence_quality: dict[str, Any],
) -> dict[str, Any]:
    """Cap confidence when evidence or category quality cannot justify high certainty."""
    capped = _clamp(confidence, 0.0, 0.90)
    market = _clamp(market_probability, 0.0, 100.0)
    category = (base_rate_category or "unknown").lower()
    bucket = str((evidence_quality or {}).get("bucket") or "weak")
    reasons: list[str] = []
    if bucket == "weak" and capped > 0.55:
        capped = 0.55
        reasons.append("weak_evidence_cap")
    elif bucket == "mixed" and capped > 0.65:
        capped = 0.65
        reasons.append("mixed_evidence_cap")
    if category == "unknown" and bucket in {"weak", "mixed"} and capped > 0.60:
        capped = 0.60
        reasons.append("unknown_category_cap")
    elif category == "unknown" and bucket == "weak":
        reasons.append("unknown_category_cap")
    if market < 10.0 and bucket != "strong" and capped > 0.62:
        capped = 0.62
        reasons.append("low_probability_evidence_cap")
    elif market < 10.0 and bucket == "weak":
        reasons.append("low_probability_evidence_cap")
    return {"confidence": round(capped, 3), "reasons": list(dict.fromkeys(reasons))}
```

- [ ] **Step 5: Integrate confidence caps in `analyze_market`**

In `backend/app/services/ai_analysis_service.py`, immediately after `base_rate = classify_market(market_question)`, add:

```python
    evidence_quality = calculate_evidence_quality(
        evidence_profile=evidence_profile,
        news_quality_score=news_quality_score,
        semantics_profile=semantics_profile,
        priced_in_risk_score=priced_in_risk_score,
    )
```

Immediately after the existing `confidence_score = calculate_confidence_score(...)` block, add:

```python
    confidence_cap = apply_confidence_caps(
        confidence=confidence_score,
        market_probability=market_probability,
        base_rate_category=base_rate.category,
        evidence_quality=evidence_quality,
    )
    confidence_score = confidence_cap["confidence"]
```

Add this field to the returned dict immediately after `"confidence_score": confidence_score,`:

```python
        "confidence_cap_reasons": confidence_cap["reasons"],
```

- [ ] **Step 6: Update full fallback contract expected dict**

In `AnalyzeMarketContractTests.test_analyze_market_fallback_contract`, add this field after `"confidence_score": 0.584,`:

```python
                "confidence_cap_reasons": [],
```

- [ ] **Step 7: Add analyze-market confidence cap regression test**

Add this test inside `AnalyzeMarketContractTests`:

```python
    def test_analyze_market_caps_confidence_for_weak_unknown_longshot(self):
        weak_context = (
            "EVIDENCE PROFILE\n"
            "direction: support\n"
            "strength: 0.12\n"
            "conflict: 0.65\n"
            "freshness: 0.25\n"
            "resolution_relevance: 0.15\n"
            "source_count: 1\n"
            "MARKET SEMANTICS\n"
            "condition_type: unknown\n"
            "ambiguity_score: 75\n"
            "news item: unconfirmed rumor. quality: 0.25 relevance: 0.20\n"
        )
        async def run():
            with (
                patch.object(ai, "_ask_ai", new=AsyncMock(return_value={
                    "ai_probability": 34,
                    "narrative_type": "factual",
                    "narrative_summary": "Weak rumor points upward.",
                    "reasoning": REASONING,
                    "has_strong_evidence": False,
                    "reasoning_consistency": 0.9,
                })),
                patch.object(ai, "translate_title", new=AsyncMock(return_value="")),
            ):
                return await ai.analyze_market(
                    market_question="Will an obscure unclassified event happen this week?",
                    market_probability=4,
                    news_context=weak_context,
                    volume=1000,
                    liquidity=500,
                )
        result = asyncio.run(run())
        self.assertLessEqual(result["confidence_score"], 0.55)
        self.assertIn("weak_evidence_cap", result["confidence_cap_reasons"])
        self.assertIn("low_probability_evidence_cap", result["confidence_cap_reasons"])
```

- [ ] **Step 8: Run tests to verify pass**

Run from `backend/`:

```powershell
python -m unittest tests.test_ai_analysis_service -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 3**

```powershell
git add backend/app/services/probability_engine_service.py backend/app/services/ai_analysis_service.py backend/tests/test_ai_analysis_service.py
git commit -m "feat: cap confidence for weak evidence"
```

---

### Task 4: Use Market Baseline for Unknown Category Anchoring

**Files:**
- Modify: `backend/app/services/ai_analysis_service.py`
- Modify: `backend/tests/test_ai_analysis_service.py`

**Interfaces:**
- Consumes: existing `anchor_probability(llm_probability: float, base_rate: BaseRate, confidence: float, effective_prior: float | None = None) -> float`.
- Produces: no new public function. `analyze_market(...)` uses `effective_prior=market_probability` when `base_rate.category == "unknown"`.

- [ ] **Step 1: Add failing regression test**

Add this test inside `AnalyzeMarketContractTests`:

```python
    def test_analyze_market_unknown_category_anchors_to_market_not_static_fifty(self):
        weak_context = (
            "EVIDENCE PROFILE\n"
            "direction: support\n"
            "strength: 0.12\n"
            "conflict: 0.65\n"
            "freshness: 0.25\n"
            "resolution_relevance: 0.15\n"
            "source_count: 1\n"
            "MARKET SEMANTICS\n"
            "condition_type: unknown\n"
            "ambiguity_score: 75\n"
            "news item: unconfirmed rumor. quality: 0.25 relevance: 0.20\n"
        )
        async def run():
            with (
                patch.object(ai, "_ask_ai", new=AsyncMock(return_value={
                    "ai_probability": 34,
                    "narrative_type": "factual",
                    "narrative_summary": "Weak rumor points upward.",
                    "reasoning": REASONING,
                    "has_strong_evidence": False,
                    "reasoning_consistency": 0.9,
                })),
                patch.object(ai, "translate_title", new=AsyncMock(return_value="")),
            ):
                return await ai.analyze_market(
                    market_question="Will an obscure unclassified event happen this week?",
                    market_probability=4,
                    news_context=weak_context,
                    volume=1000,
                    liquidity=500,
                )
        result = asyncio.run(run())
        self.assertEqual(result["base_rate_category"], "unknown")
        self.assertEqual(result["base_rate_prior"], 50)
        self.assertLessEqual(result["evidence_constrained_probability"], 14.0)
        self.assertLessEqual(result["ai_probability"], 14.0)
        self.assertTrue(result["probability_guardrail_triggered"])
```

- [ ] **Step 2: Run test to verify failure**

Run from `backend/`:

```powershell
python -m unittest tests.test_ai_analysis_service.AnalyzeMarketContractTests.test_analyze_market_unknown_category_anchors_to_market_not_static_fifty -v
```

Expected: FAIL because static unknown prior pulls the final `ai_probability` toward 50.

- [ ] **Step 3: Modify `analyze_market` anchor call**

In `backend/app/services/ai_analysis_service.py`, replace the existing `anchor_probability(...)` call with:

```python
    effective_anchor_prior = market_probability if base_rate.category == "unknown" else None
    ai_probability = anchor_probability(
        llm_probability=evidence_constrained_probability,
        base_rate=base_rate,
        confidence=confidence_score,
        effective_prior=effective_anchor_prior,
    )
```

Add this field to the returned dict immediately after `"base_rate_prior": base_rate.prior,`:

```python
        "base_rate_effective_prior": effective_anchor_prior if effective_anchor_prior is not None else base_rate.prior,
```

- [ ] **Step 4: Update full fallback contract expected dict**

In `AnalyzeMarketContractTests.test_analyze_market_fallback_contract`, add this field after `"base_rate_prior": 50,`:

```python
                "base_rate_effective_prior": 50.0,
```

- [ ] **Step 5: Run tests to verify pass**

Run from `backend/`:

```powershell
python -m unittest tests.test_ai_analysis_service -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```powershell
git add backend/app/services/ai_analysis_service.py backend/tests/test_ai_analysis_service.py
git commit -m "fix: anchor unknown markets to market baseline"
```

---

### Task 5: Final Verification and Memory Update

**Files:**
- Modify: `SESSION_MEMORY_2026-07-05.md`

**Interfaces:**
- Consumes: all previous task commits.
- Produces: a short memory update documenting behavior, tests, and remaining follow-up.

- [ ] **Step 1: Run focused backend tests**

Run from `backend/`:

```powershell
python -m unittest tests.test_ai_analysis_service -v
```

Expected: PASS.

- [ ] **Step 2: Run settlement regression tests**

Run from `backend/`:

```powershell
python -m unittest tests.test_event_resolve_service tests.test_polymarket_history_service tests.test_events_routes.ResolveExpiredRouteTests tests.test_simulated_trade_store -v
```

Expected: PASS. If tests fail because of pre-existing unrelated local changes, inspect the failure and do not claim pass.

- [ ] **Step 3: Run compile check**

Run from `backend/`:

```powershell
python -m compileall app/services/probability_engine_service.py app/services/ai_analysis_service.py
```

Expected: PASS.

- [ ] **Step 4: Check working tree scope**

Run from repo root:

```powershell
git status --short
```

Expected: implementation task changes are committed or limited to the memory file. Many unrelated pre-existing files may still appear modified; do not stage them.

- [ ] **Step 5: Append memory update**

Append this section to `SESSION_MEMORY_2026-07-05.md`:

```markdown
---

# Session Memory Update - Prediction Engine Safety Evidence Boost v1

Time: 2026-07-05, Asia/Shanghai.

User decision: keep `watch` simulated trades enabled for exploration data. This work optimized the prediction engine rather than disabling watch trades.

Implemented:

- Added deterministic evidence quality scoring with buckets `weak`, `mixed`, `solid`, and `strong`.
- Added a low-probability longshot guardrail so weak evidence cannot lift sub-10% market probabilities into large artificial YES edges.
- Added confidence caps for weak/mixed evidence, unknown categories, and low-probability weak-evidence situations.
- Changed unknown-category anchoring in `analyze_market()` to use the market probability as effective prior instead of static 50%.
- Added diagnostics to analysis output: evidence quality factor/bucket/reasons, guardrail status/reason, confidence cap reasons, and base-rate effective prior.

Verification:

- `python -m unittest tests.test_ai_analysis_service -v`
- `python -m unittest tests.test_event_resolve_service tests.test_polymarket_history_service tests.test_events_routes.ResolveExpiredRouteTests tests.test_simulated_trade_store -v`
- `python -m compileall app/services/probability_engine_service.py app/services/ai_analysis_service.py`

Remaining follow-up:

- Segment skill should be made market-relative in a separate focused pass if prediction-store fields are sufficient or after adding explicit market baseline fields.
- UI/reporting should eventually separate `act`, `provisional_act`, and `watch` simulated-trade performance.
```

- [ ] **Step 6: Commit memory update**

```powershell
git add SESSION_MEMORY_2026-07-05.md
git commit -m "docs: update prediction engine safety memory"
```

- [ ] **Step 7: Final status summary**

Report:

- commits created;
- tests that passed;
- tests that failed, if any;
- files intentionally left untouched;
- reminder that `watch` simulated trading remains enabled.
