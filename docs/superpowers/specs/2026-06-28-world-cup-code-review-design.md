# World Cup Code Review Design

Date: 2026-06-28

## Goal

Review the current uncommitted World Cup prediction chain for correctness, production risk, and regression risk before the next implementation or commit.

## Scope

The review is limited to the World Cup flow and its directly connected UI:

- Backend API routes for World Cup predictions and analytics.
- Prediction pipeline, scheduler, match service, quality service, confidence calibration, scoring, factor service, and prediction engines.
- Frontend World Cup page, analytics dashboard, engine comparison, auto-tune, batch switching, match cards, prediction history, and analytics API client.
- Tests that directly cover the files above.

The review will not include unrelated event-intelligence flows, deployment hardening, broad repository cleanup, or formatting-only concerns unless they directly affect the World Cup path.

## Review Approach

Use a risk-directed diff review:

1. Inspect the current diff and new files in the scoped World Cup chain.
2. Trace the main data flow from API request to pipeline execution, engine/scoring output, persistence, analytics API, and frontend rendering.
3. Prioritize issues that can cause wrong predictions, stale or misleading UI state, failed scheduled jobs, broken API contracts, lost data, security exposure, or missing regression coverage.
4. Treat style, naming, and refactoring suggestions as non-findings unless they mask a real defect.

## Output

The review output will be a code-review style report:

- Findings first, ordered by severity.
- Each finding includes file and line reference, impact, and a concrete fix direction.
- Open questions or assumptions follow findings.
- A short verification note lists commands run and any commands not run.

If no material issues are found, the report will say that clearly and call out remaining test gaps or residual risk.

## Verification Boundary

Prefer targeted verification for the reviewed path:

- Backend targeted unit tests for World Cup prediction, analytics, scoring, quality, scheduler, and pipeline behavior.
- Frontend typecheck or targeted Vitest files for World Cup UI contracts when practical.

Full repository test runs are optional because the current worktree is large and includes many unrelated changes.
