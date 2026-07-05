# Confidence Breakdown Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose why evidence source structure affects confidence without changing existing probability movement or watch/settlement behavior.

**Architecture:** Add a pure diagnostics helper beside `calculate_confidence_score()` in the probability engine. Keep `calculate_confidence_score()` returning a float for compatibility, and have `analyze_market()` attach the helper output as `confidence_breakdown` in the legacy result dict.

**Tech Stack:** Python backend, `unittest`, existing services in `backend/app/services`.

## Global Constraints

- Do not disable or alter `watch` simulated trades.
- Do not touch settlement / auto-resolve logic.
- Keep changes surgical and backwards-compatible.
- Use TDD: write and run a failing test before production changes.
- Do not change probability movement formulas.

---

### Task 1: Add confidence source-structure diagnostics

**Files:**
- Modify: `backend/tests/test_ai_analysis_service.py`
- Modify: `backend/app/services/probability_engine_service.py`
- Modify: `backend/app/services/ai_analysis_service.py`

**Interfaces:**
- Consumes: existing `calculate_confidence_score(...)` inputs and `extract_evidence_profile(...)` output fields.
- Produces: `calculate_confidence_breakdown(evidence_profile: dict[str, Any] | None = None) -> dict[str, Any]` and `analyze_market()` result field `confidence_breakdown`.

- [ ] **Step 1: Write the failing helper test**

Add a test that calls the new helper with same total `source_count` but strong source structure and asserts:

```python
breakdown = calculate_confidence_breakdown({
    "source_count": 4,
    "independent_source_count": 4,
    "official_source_count": 2,
    "counterevidence_considered": True,
})
self.assertTrue(breakdown["source_structure_used"])
self.assertIn("independent_source_support", breakdown["source_quality_reasons"])
self.assertIn("official_source_support", breakdown["source_quality_reasons"])
self.assertIn("counterevidence_considered", breakdown["source_quality_reasons"])
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
cd backend
python -m unittest tests.test_ai_analysis_service.ProbabilityMathTests.test_calculate_confidence_breakdown_marks_source_structure_used -v
```

Expected: fail because `calculate_confidence_breakdown` is not defined/importable.

- [ ] **Step 3: Implement minimal helper and reuse it in confidence scoring**

Add the helper in `backend/app/services/probability_engine_service.py` near `calculate_confidence_score()`. It must compute the same source structure score currently embedded in `calculate_confidence_score()` and return a dict with counts, scores, boolean `source_structure_used`, and reason strings.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run:

```powershell
cd backend
python -m unittest tests.test_ai_analysis_service.ProbabilityMathTests.test_calculate_confidence_breakdown_marks_source_structure_used tests.test_ai_analysis_service.ProbabilityMathTests.test_calculate_confidence_score_rewards_v2_source_quality -v
```

Expected: both pass.

- [ ] **Step 5: Add analyze_market contract test**

Add a test that patches `_ask_ai` to return a valid minimal LLM analysis, patches `translate_title` to avoid network/model calls, passes structured source fields in `news_context`, then asserts `result["confidence_breakdown"]` exists and includes source-structure diagnostics.

- [ ] **Step 6: Implement analyze_market output field**

Import/use `calculate_confidence_breakdown` in `backend/app/services/ai_analysis_service.py` and include:

```python
"confidence_breakdown": calculate_confidence_breakdown(evidence_profile),
```

in the returned dict.

- [ ] **Step 7: Run regression checks**

Run:

```powershell
cd backend
python -m unittest tests.test_ai_analysis_service tests.test_evidence_scoring_service tests.test_news_filter_service -v
python -m unittest tests.test_event_resolve_service tests.test_polymarket_history_service tests.test_events_routes.ResolveExpiredRouteTests tests.test_simulated_trade_store -v
python -m compileall app/services/probability_engine_service.py app/services/ai_analysis_service.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add backend/app/services/probability_engine_service.py backend/app/services/ai_analysis_service.py backend/tests/test_ai_analysis_service.py docs/superpowers/plans/2026-07-05-confidence-breakdown-diagnostics.md
git commit -m "feat: expose confidence source diagnostics"
```
