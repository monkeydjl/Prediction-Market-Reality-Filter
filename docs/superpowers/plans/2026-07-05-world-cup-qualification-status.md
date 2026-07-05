# World Cup Qualification Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Mark World Cup teams as 已出线 / 已淘汰 on the qualification probability page once a group has no remaining group-stage matches.

**Architecture:** Extend the qualification probability domain model with an explicit `qualificationStatus` field. Keep probability calculation deterministic: completed groups return 100% for the top two and 0% for the rest; incomplete groups keep heuristic probabilities and `pending` status. The table renders a compact Chinese status badge without changing data fetching.

**Tech Stack:** Next.js, React, TypeScript, Vitest, Testing Library.

## Global Constraints

- Follow existing World Cup module patterns.
- Use TDD: write a failing test before production changes.
- Do not add new dependencies.
- Keep the change frontend-only unless tests prove backend changes are required.

---

### Task 1: Qualification model status

**Files:**
- Modify: `frontend/src/lib/qualification-probability.ts`
- Test: `frontend/src/lib/qualification-probability.test.ts`

**Interfaces:**
- Produces: `QualificationProbability.qualificationStatus: "qualified" | "eliminated" | "pending"`
- Produces: completed-group probabilities of `1` for top two and `0` for bottom teams.

- [x] **Step 1: Write failing tests** for completed and incomplete groups.
- [x] **Step 2: Run targeted Vitest file** and confirm failure because `qualificationStatus` is absent.
- [x] **Step 3: Implement minimal model changes** in `calculateQualificationProbabilities`.
- [x] **Step 4: Re-run targeted tests** and confirm pass.

### Task 2: Qualification table badge

**Files:**
- Modify: `frontend/src/components/world-cup/qualification-table.tsx`
- Test: `frontend/src/components/world-cup/qualification-table.test.tsx`

**Interfaces:**
- Consumes: `QualificationProbability.qualificationStatus`.
- Produces: visible badges `已出线` / `已淘汰`; pending teams continue to show probability normally.

- [x] **Step 1: Write failing rendering tests** for qualified and eliminated badges.
- [x] **Step 2: Run targeted component test** and confirm failure.
- [x] **Step 3: Implement minimal badge rendering** with existing styling utilities.
- [x] **Step 4: Re-run component test** and confirm pass.

### Task 3: Verification and commit

**Files:**
- Verify all touched files.

- [x] **Step 1:** Run targeted tests.
- [x] **Step 2:** Run `npm.cmd run typecheck`.
- [x] **Step 3:** Run `npm.cmd run build`.
- [x] **Step 4:** Stage only intended files and commit.
