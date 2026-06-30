# World Cup Code Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a stability-focused code review of the current uncommitted World Cup prediction chain without modifying business code.

**Architecture:** This is a read-only review plan. It traces the World Cup path from backend API routes through prediction pipeline, engines, scoring, quality/calibration, scheduler, analytics API, and frontend rendering. Findings must be grounded in current diff evidence and targeted verification.

**Tech Stack:** Python, FastAPI, SQLAlchemy, unittest/pytest-compatible backend tests, Next.js, React, TypeScript, Vitest.

## Global Constraints

- Do not edit business code during this review.
- Preserve all existing uncommitted user changes.
- Prioritize stability: report correctness, production, data integrity, API contract, scheduler, UI state, and regression-test risks before style concerns.
- Findings require concrete file and line references.
- Full repository test runs are optional; use targeted tests for the World Cup path first.

---

## File Structure

No production files will be created or modified. The review reads these existing areas:

- `backend/app/api/routes/world_cup_predictions.py`: prediction endpoints and request/response contracts.
- `backend/app/api/routes/world_cup_analytics.py`: analytics endpoints consumed by the frontend dashboard.
- `backend/app/services/world_cup_prediction_pipeline.py`: core prediction orchestration and persistence.
- `backend/app/services/world_cup_prediction_scheduler.py`: scheduled prediction/backfill entry points.
- `backend/app/services/world_cup_match_service.py`: fixture sync and match lifecycle behavior.
- `backend/app/services/world_cup_quality_service.py`: quality metrics and stale-data signals.
- `backend/app/services/world_cup_confidence_calibration.py`: confidence calibration behavior.
- `backend/app/services/world_cup_scoring_service.py`: scoring and evaluation behavior.
- `backend/app/services/world_cup_factor_service.py`: factor generation and input assumptions.
- `backend/app/services/world_cup_engines/*.py`: engine outputs and probability contract.
- `frontend/src/app/world-cup/page.tsx`: top-level World Cup page data flow.
- `frontend/src/components/world-cup/*.tsx`: analytics, engine comparison, auto-tune, batch switching, match cards, and prediction history UI.
- `frontend/src/lib/analytics-api.ts`: analytics client contract.
- `frontend/src/lib/world-cup-predictions.ts`: prediction API client contract.
- `backend/tests/test_world_cup_*.py` and `frontend/src/**/*.test.tsx`: targeted regression coverage.

## Task 1: Backend API Contract Review

**Files:**
- Read: `backend/app/api/routes/world_cup_predictions.py`
- Read: `backend/app/api/routes/world_cup_analytics.py`
- Read: `frontend/src/lib/world-cup-predictions.ts`
- Read: `frontend/src/lib/analytics-api.ts`
- Test references: `backend/tests/test_world_cup_predictions_routes.py`, `backend/tests/test_world_cup_analytics_routes.py`

**Interfaces:**
- Consumes: current diff for backend routes and frontend clients.
- Produces: notes on broken request/response contracts, missing error handling, authorization gaps, or frontend/backend schema drift.

- [ ] **Step 1: Inspect scoped route diffs**

Run: `git diff -- backend/app/api/routes/world_cup_predictions.py backend/app/api/routes/world_cup_analytics.py frontend/src/lib/world-cup-predictions.ts frontend/src/lib/analytics-api.ts`

Expected: route and client changes only; record line numbers for any suspected contract issue.

- [ ] **Step 2: Read current route implementations with line numbers**

Run: `Select-String -Path backend/app/api/routes/world_cup_predictions.py,backend/app/api/routes/world_cup_analytics.py -Pattern "def |async def |HTTPException|Depends|return|@router" -Context 2,4`

Expected: enough route structure to map endpoints to clients.

- [ ] **Step 3: Cross-check route tests**

Run: `Select-String -Path backend/tests/test_world_cup_predictions_routes.py,backend/tests/test_world_cup_analytics_routes.py -Pattern "def test_|client\\.|assert|status_code" -Context 1,3`

Expected: identify whether changed endpoints have tests for success and failure behavior.

## Task 2: Prediction Pipeline Review

**Files:**
- Read: `backend/app/services/world_cup_prediction_pipeline.py`
- Read: `backend/app/services/world_cup_engines/world_cup_prediction_engine.py`
- Read: `backend/app/services/world_cup_engines/world_cup_rule_engine.py`
- Read: `backend/app/services/world_cup_engines/world_cup_elo_odds_engine.py`
- Read: `backend/app/services/world_cup_engines/world_cup_gbm_engine.py`
- Read: `backend/app/services/world_cup_engines/world_cup_ai_engine.py`
- Test references: `backend/tests/test_world_cup_prediction_pipeline.py`, `backend/tests/test_world_cup_rule_engine.py`, `backend/tests/test_world_cup_engine_comparison.py`

**Interfaces:**
- Consumes: API-triggered match prediction inputs and engine outputs.
- Produces: notes on wrong probabilities, stale persistence, async blocking, exception swallowing, or inconsistent engine comparison behavior.

- [ ] **Step 1: Inspect pipeline and engine diffs**

Run: `git diff -- backend/app/services/world_cup_prediction_pipeline.py backend/app/services/world_cup_engines/world_cup_prediction_engine.py backend/app/services/world_cup_engines/world_cup_rule_engine.py backend/app/services/world_cup_engines/world_cup_elo_odds_engine.py backend/app/services/world_cup_engines/world_cup_gbm_engine.py backend/app/services/world_cup_engines/world_cup_ai_engine.py`

Expected: focused view of current changes in prediction execution.

- [ ] **Step 2: Locate main pipeline boundaries**

Run: `Select-String -Path backend/app/services/world_cup_prediction_pipeline.py -Pattern "async def|def |session|commit|rollback|to_thread|gather|Semaphore|Prediction\\(|Engine|probability" -Context 2,4`

Expected: identify persistence, concurrency, and engine selection paths.

- [ ] **Step 3: Cross-check probability contract tests**

Run: `Select-String -Path backend/tests/test_world_cup_prediction_pipeline.py,backend/tests/test_world_cup_rule_engine.py,backend/tests/test_world_cup_engine_comparison.py -Pattern "def test_|assert|probability|engine|comparison" -Context 1,3`

Expected: identify whether changed engine behavior is covered by tests.

## Task 3: Quality, Calibration, Scoring, And Factors Review

**Files:**
- Read: `backend/app/services/world_cup_quality_service.py`
- Read: `backend/app/services/world_cup_confidence_calibration.py`
- Read: `backend/app/services/world_cup_scoring_service.py`
- Read: `backend/app/services/world_cup_factor_service.py`
- Test references: `backend/tests/test_world_cup_quality_service.py`, `backend/tests/test_world_cup_confidence_calibration.py`, `backend/tests/test_world_cup_scoring_service.py`, `backend/tests/test_world_cup_schedule_factors.py`

**Interfaces:**
- Consumes: prediction outputs, match facts, historical results, and analytics summaries.
- Produces: notes on misleading quality indicators, incorrect confidence calibration, scoring regressions, or factor assumptions that can distort predictions.

- [ ] **Step 1: Inspect changed service diffs**

Run: `git diff -- backend/app/services/world_cup_quality_service.py backend/app/services/world_cup_confidence_calibration.py backend/app/services/world_cup_scoring_service.py backend/app/services/world_cup_factor_service.py`

Expected: current quality/calibration/scoring/factor changes.

- [ ] **Step 2: Read key computations**

Run: `Select-String -Path backend/app/services/world_cup_quality_service.py,backend/app/services/world_cup_confidence_calibration.py,backend/app/services/world_cup_scoring_service.py,backend/app/services/world_cup_factor_service.py -Pattern "def |return|raise|confidence|quality|score|factor|None|datetime|timezone" -Context 2,4`

Expected: identify input validation, default behavior, and edge-case handling.

- [ ] **Step 3: Cross-check related tests**

Run: `Select-String -Path backend/tests/test_world_cup_quality_service.py,backend/tests/test_world_cup_confidence_calibration.py,backend/tests/test_world_cup_scoring_service.py,backend/tests/test_world_cup_schedule_factors.py -Pattern "def test_|assert|confidence|quality|score|factor" -Context 1,3`

Expected: identify missing regression coverage for changed computations.

## Task 4: Scheduler And Match Lifecycle Review

**Files:**
- Read: `backend/app/core/scheduler.py`
- Read: `backend/app/services/world_cup_prediction_scheduler.py`
- Read: `backend/app/services/world_cup_match_service.py`
- Read: `backend/app/services/world_cup_live_update_service.py`
- Test references: `backend/tests/test_world_cup_prediction_scheduler.py`, `backend/tests/test_world_cup_match_service.py`, `backend/tests/test_world_cup_live_update_service.py`

**Interfaces:**
- Consumes: scheduled jobs, match sync, live updates, and post-match backfill.
- Produces: notes on duplicate jobs, blocking work in async paths, missed backfills, broken lock semantics, or stale match state.

- [ ] **Step 1: Inspect scheduler and lifecycle diffs**

Run: `git diff -- backend/app/core/scheduler.py backend/app/services/world_cup_prediction_scheduler.py backend/app/services/world_cup_match_service.py backend/app/services/world_cup_live_update_service.py`

Expected: current scheduled-job and match lifecycle changes.

- [ ] **Step 2: Read job registration and async boundaries**

Run: `Select-String -Path backend/app/core/scheduler.py,backend/app/services/world_cup_prediction_scheduler.py,backend/app/services/world_cup_match_service.py,backend/app/services/world_cup_live_update_service.py -Pattern "add_job|async def|def |to_thread|run_|sync_|backfill|lock|timezone|trigger" -Context 2,4`

Expected: identify scheduler execution paths and stability hazards.

- [ ] **Step 3: Cross-check scheduler tests**

Run: `Select-String -Path backend/tests/test_world_cup_prediction_scheduler.py,backend/tests/test_world_cup_match_service.py,backend/tests/test_world_cup_live_update_service.py -Pattern "def test_|async|assert|scheduler|backfill|sync|live" -Context 1,3`

Expected: identify whether new scheduled behavior has tests.

## Task 5: Frontend World Cup UI Contract Review

**Files:**
- Read: `frontend/src/app/world-cup/page.tsx`
- Read: `frontend/src/components/world-cup/analytics-dashboard.tsx`
- Read: `frontend/src/components/world-cup/engine-auto-tune-dashboard.tsx`
- Read: `frontend/src/components/world-cup/engine-comparison-card.tsx`
- Read: `frontend/src/components/world-cup/engine-comparison-view.tsx`
- Read: `frontend/src/components/world-cup/batch-engine-switcher.tsx`
- Read: `frontend/src/components/world-cup/match-prediction-card.tsx`
- Read: `frontend/src/components/world-cup/prediction-history-card.tsx`
- Test references: matching `frontend/src/components/world-cup/*.test.tsx`

**Interfaces:**
- Consumes: prediction and analytics API responses.
- Produces: notes on stale UI state, misleading labels, broken loading/error paths, schema drift, or test gaps.

- [ ] **Step 1: Inspect frontend diffs**

Run: `git diff -- frontend/src/app/world-cup/page.tsx frontend/src/components/world-cup/analytics-dashboard.tsx frontend/src/components/world-cup/engine-auto-tune-dashboard.tsx frontend/src/components/world-cup/engine-comparison-card.tsx frontend/src/components/world-cup/engine-comparison-view.tsx frontend/src/components/world-cup/batch-engine-switcher.tsx frontend/src/components/world-cup/match-prediction-card.tsx frontend/src/components/world-cup/prediction-history-card.tsx frontend/src/lib/analytics-api.ts frontend/src/lib/world-cup-predictions.ts`

Expected: current frontend World Cup changes and API client changes.

- [ ] **Step 2: Read state and effect boundaries**

Run: `Select-String -Path frontend/src/app/world-cup/page.tsx,frontend/src/components/world-cup/analytics-dashboard.tsx,frontend/src/components/world-cup/engine-auto-tune-dashboard.tsx,frontend/src/components/world-cup/engine-comparison-card.tsx,frontend/src/components/world-cup/engine-comparison-view.tsx,frontend/src/components/world-cup/batch-engine-switcher.tsx,frontend/src/components/world-cup/match-prediction-card.tsx,frontend/src/components/world-cup/prediction-history-card.tsx -Pattern "useState|useEffect|useMemo|useCallback|fetch|analyticsApi|worldCup|error|loading|return" -Context 1,3`

Expected: identify UI behavior and stale data hazards.

- [ ] **Step 3: Cross-check frontend tests**

Run: `Select-String -Path frontend/src/components/world-cup/*.test.tsx,frontend/src/lib/world-cup-predictions.test.ts -Pattern "test\\(|it\\(|expect|render|screen|mock" -Context 1,3`

Expected: identify changed UI paths with or without regression tests.

## Task 6: Targeted Verification

**Files:**
- Test: `backend/tests/test_world_cup_predictions_routes.py`
- Test: `backend/tests/test_world_cup_analytics_routes.py`
- Test: `backend/tests/test_world_cup_prediction_pipeline.py`
- Test: `backend/tests/test_world_cup_quality_service.py`
- Test: `backend/tests/test_world_cup_confidence_calibration.py`
- Test: `backend/tests/test_world_cup_scoring_service.py`
- Test: `backend/tests/test_world_cup_prediction_scheduler.py`
- Test: `backend/tests/test_world_cup_match_service.py`
- Test: `frontend/src/components/world-cup/*.test.tsx`
- Test: `frontend/src/lib/world-cup-predictions.test.ts`

**Interfaces:**
- Consumes: findings from Tasks 1-5.
- Produces: verification evidence for the final code-review report.

- [ ] **Step 1: Run backend syntax check for scoped app/tests**

Run from `backend`: `python -m compileall app tests`

Expected: compile succeeds. If it fails, report the failing file and traceback as a finding or verification blocker.

- [ ] **Step 2: Run targeted backend World Cup tests**

Run from `backend`: `python -m unittest tests.test_world_cup_predictions_routes tests.test_world_cup_analytics_routes tests.test_world_cup_prediction_pipeline tests.test_world_cup_quality_service tests.test_world_cup_confidence_calibration tests.test_world_cup_scoring_service tests.test_world_cup_prediction_scheduler tests.test_world_cup_match_service`

Expected: selected tests pass. If unittest discovery cannot run a pytest-style test, report that and use the existing test file evidence in the review.

- [ ] **Step 3: Run frontend typecheck**

Run from `frontend`: `npm run typecheck`

Expected: TypeScript check passes. If it fails, report the first World Cup-related errors.

- [ ] **Step 4: Run targeted frontend tests**

Run from `frontend`: `npm run test -- --run frontend/src/components/world-cup frontend/src/lib/world-cup-predictions.test.ts`

Expected: targeted Vitest files pass. If the runner does not accept directory arguments, report the command failure and fall back to `npm run test`.

## Self-Review

- Spec coverage: The plan covers backend routes, pipeline, scheduler, match lifecycle, quality/calibration/scoring/factors, engines, frontend World Cup UI, API clients, tests, and verification.
- Placeholder scan: The plan contains no placeholder markers or unspecified implementation steps.
- Type consistency: No new runtime interfaces are introduced; the review consumes existing files and produces a findings report only.
