# Prediction Market Reality Filter - Frontend Audit Report (Revised)

**Date:** 2026-06-20  
**Scope:** Frontend audit re-check based on source verification  
**Method:** code review against the live frontend codebase under `frontend/src`  
**Audit target:** Next.js 16.2.9 + React 19.2.4 frontend

---

## TL;DR

- **Overall judgment:** the frontend is structurally workable and code quality is decent, but the original audit overstated several findings. The main confirmed gaps are:
  1. missing route-level error boundary
  2. backend error text is exposed directly to users
  3. zero automated test coverage
  4. repeated client-side data fetching with no shared cache layer
- **Current risk level:** medium. There is no immediate evidence of a fatal architectural flaw, but resilience and verification are weak.
- **Most important correction to the original report:** Dashboard data loading is **not** a full serial waterfall. It performs `list` and `movers` in parallel, then fetches history only for the top 3 movers.
- **Newly added finding:** the frontend has acceptable route-level bundle separation by App Router page boundaries, so "no code splitting" should not be stated as a confirmed defect. The real issue is narrower: charting dependencies are eagerly imported inside chart-bearing routes/components.

---

## Verified System Snapshot

### Actual route structure

Under `frontend/src/app`, the current business routes are:

- `/` -> dashboard
- `/events` -> event detail page via query param `id`
- `/analyze`
- `/decisions`
- `/history`

Supporting app files:

- `layout.tsx`
- `globals.css`

### Actual source inventory

Verified at time of review:

- **5 business pages**
- **18 component files**
- **5 lib files**
- **0 test files**

This differs from the original report's page/component counts, so any effort estimates derived from those counts should be treated as rough only.

### Architecture summary

- App Router frontend with client-rendered business pages
- production build uses static export via `output: "export"`
- API access is centralized in `src/lib/api.ts`
- backend DTOs are adapted into frontend view models in `src/lib/adapt.ts`
- styling is Tailwind v4 + CSS token theme

---

## Confirmed Findings

### Critical

#### 1. Missing route-level error boundary

**Status:** confirmed  
**Evidence:** no `frontend/src/app/error.tsx`; no `frontend/src/app/global-error.tsx`  
**Impact:** runtime rendering failures in a route subtree can collapse to a poor failure mode instead of a controlled recovery screen.

This is one of the strongest points from the original report and should remain P0.

#### 2. Raw backend error text is exposed directly to the UI

**Status:** confirmed  
**Location:** `frontend/src/lib/api.ts`

```ts
if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
```

**Impact:** server-originated error text can leak internal detail into the UI and produces inconsistent UX across endpoints.

This should remain P0.

### High

#### 3. Zero automated tests

**Status:** confirmed  
**Evidence:** no `.test.*`, `.spec.*`, or `__tests__` files found under `frontend`

**Impact:** regressions in formatting, adapters, and page loading flows are currently unchecked.

The codebase is testable enough that this is a real quality gap, not just a process preference.

#### 4. No shared data cache or request deduplication layer

**Status:** confirmed  
**Evidence:** page-level fetch logic is hand-written with `useEffect`/`useCallback`; no React Query or SWR dependency is present.

**Impact:** each route manages loading/error/data state manually, repeated navigations refetch, and there is no built-in stale data policy or dedupe layer.

This is real, but it should be described as an architectural limitation rather than an outright bug.

#### 5. Optimistic tracking updates can race under rapid repeated user actions

**Status:** confirmed  
**Location:** `frontend/src/components/detail/tracking-decision.tsx`

The component applies optimistic local state, starts a request, and rolls back on failure. Because requests are not serialized or versioned, quick successive clicks can resolve out of order.

**Impact:** local UI state can momentarily show stale or reverted values if responses return out of order.

This was directionally present in the original report and is valid.

### Medium

#### 6. Analyze form accepts invalid numeric coercions too loosely

**Status:** confirmed  
**Location:** `frontend/src/app/analyze/page.tsx`

`baseline_probability` is sent as `Number(baseline)`. An empty or malformed string can coerce in ways that rely too much on browser input behavior and backend validation.

**Impact:** weak client-side validation and inconsistent error behavior.

This is worth keeping as a medium issue.

#### 7. `adapt.ts` uses a type escape for `legacy_analysis`

**Status:** confirmed  
**Location:** `frontend/src/lib/adapt.ts`

```ts
const legacy = (record as unknown as { legacy_analysis?: Record<string, unknown> })
  .legacy_analysis;
```

**Impact:** this weakens type guarantees around one of the adapter's fallback paths.

This is not severe, but it is a legitimate maintainability finding.

#### 8. Repeated local loading/error patterns across pages

**Status:** confirmed  
**Evidence:** `page.tsx`, `events/page.tsx`, `history/page.tsx`, `decisions/page.tsx`

**Impact:** repeated boilerplate raises consistency risk and makes future changes to request lifecycle behavior more expensive.

This supports the case for a query library or a local fetch abstraction.

---

## Corrections to the Original Report

The following items from the original audit should be corrected or downgraded.

### 1. "Dashboard serial waterfall: list -> movers -> N x history"

**Correction:** inaccurate.

Actual behavior:

1. `eventsApi.list(100)` and `eventsApi.movers(10)` run in parallel
2. only the top 3 movers request history
3. those 3 history requests run in parallel

So the real finding is:

- extra history requests exist on dashboard load
- the dashboard is not fully cached
- but it is **not** a broad serial waterfall

### 2. "Dashboard lacks cancel flag, therefore race condition"

**Correction:** overstated.

The dashboard effect runs from a stable `load` callback and mounts once. The code comment is explicit about relying on React's post-unmount behavior. That is a debatable style choice, but not enough evidence for a confirmed race bug by itself.

This should be downgraded to a note, not a high-severity finding.

### 3. "No code splitting, recharts in first screen globally"

**Correction:** too broad.

The app already benefits from route-level splitting through App Router page boundaries. What is true is narrower:

- `recharts` is eagerly imported in chart-bearing components
- chart code is not dynamically deferred within those routes/components

That is an optimization opportunity, not a proven global loading defect.

### 4. "React 19 useCallback compatibility risk"

**Correction:** insufficient evidence.

The reviewed `useCallback` usage is conventional and the report did not identify a concrete broken behavior. Without a demonstrated bug or an upstream documented incompatibility relevant to this code, this should not be listed as a high-severity issue.

### 5. Reported page/component/source counts

**Correction:** inaccurate.

The original report overstated structural counts. This does not invalidate every conclusion, but it reduces confidence in effort estimates and coverage projections.

---

## What the Original Report Got Right

These points were directionally correct and should be retained:

- missing error boundary
- raw API error exposure
- no automated tests
- no shared request cache layer
- TypeScript strict mode is enabled
- API layer is centralized and reasonably clean
- adapter pattern between backend data and UI view model is a good design choice

---

## Strengths of the Current Frontend

### 1. Centralized API access

`src/lib/api.ts` provides one place to adjust fetch behavior, endpoint typing, headers, and error policy.

### 2. Adapter boundary is a good design choice

`src/lib/adapt.ts` isolates backend shape drift from presentational components. That is a solid structural decision for a product like this.

### 3. Consistent client-side state handling

Pages generally follow a clear `loading / error / success` pattern. There is duplication, but not chaos.

### 4. TypeScript strict mode is enabled

This materially lowers risk in a UI that depends on nested backend data.

### 5. Styling system is coherent

Tailwind v4 plus semantic tokens in `globals.css` gives the frontend a stable theme base without obvious design-system fragmentation.

---

## New Additions from This Re-check

These were not clearly called out in the original report and are worth adding.

### A. Route-level splitting exists; route-internal lazy loading is the actual open question

The app should not be described as having zero code splitting. The more precise recommendation is:

- leave route-level splitting as-is
- consider dynamic import only for heavier chart surfaces if bundle analysis shows pressure

### B. Static export constraint is real, but should be framed as an explicit deployment tradeoff

`output: "export"` is clearly intentional. The tradeoff is:

- simpler static deployment
- no server-rendered freshness on first HTML response
- client fetch remains responsible for all live data

This belongs in architecture notes, not in the defect count.

### C. The navigation and layout foundation are simple and maintainable

`AppNav` is small, readable, and already normalizes trailing slashes for static export routing. That is a good sign of deployment-aware implementation detail rather than accidental coupling.

### D. The codebase appears operationally cleaner than the original severity mix implied

I did not find evidence for:

- deep component nesting problems
- circular dependency issues
- uncontrolled state sprawl
- obviously broken React 19 usage

So the overall codebase health should be assessed as "reasonable but under-tested," not "structurally unstable."

---

## Revised Priorities

### P0

1. Add `app/error.tsx` and, if desired, `app/global-error.tsx`
2. Sanitize API error handling in `src/lib/api.ts`
3. Introduce a minimal test harness and cover `adapt.ts`, `format.ts`, and API error behavior

### P1

4. Introduce a shared query/cache layer such as TanStack Query
5. Protect tracking updates from out-of-order optimistic saves
6. Tighten analyze-form numeric validation before request submission

### P2

7. Remove or formalize the `legacy_analysis` type escape
8. Reduce repeated page-level request boilerplate
9. Evaluate dynamic import for chart-heavy surfaces only if bundle measurements justify it

---

## Suggested Testing Strategy

Recommended stack remains sound:

- Vitest
- React Testing Library
- MSW

Priority order:

1. `src/lib/adapt.ts`
2. `src/lib/format.ts`
3. `src/lib/api.ts`
4. `TrackingDecision`
5. page-level loading/error rendering for dashboard, events, history, decisions

The original "~200 tests / ~5 days" estimate should be treated as speculative because the original source inventory was off.

---

## Final Assessment

The original audit identified several real weaknesses, but it mixed confirmed defects, architecture preferences, and performance hypotheses too aggressively.

**Revised final assessment:**

- **Code quality:** good enough to build on
- **Architecture:** acceptable for current scope, with clear room for a shared query/cache layer
- **Operational resilience:** weak because of missing error boundaries and no test coverage
- **Overall grade:** conditional pass

The frontend does **not** currently present evidence of a major architectural failure. Its biggest problems are resilience, verification, and some avoidable request/state duplication.

---

## Files Verified During Re-check

- `frontend/package.json`
- `frontend/next.config.ts`
- `frontend/tsconfig.json`
- `frontend/src/app/layout.tsx`
- `frontend/src/app/page.tsx`
- `frontend/src/app/events/page.tsx`
- `frontend/src/app/analyze/page.tsx`
- `frontend/src/app/decisions/page.tsx`
- `frontend/src/app/history/page.tsx`
- `frontend/src/components/detail/tracking-decision.tsx`
- `frontend/src/components/detail/probability-chart.tsx`
- `frontend/src/components/ui/chart-lite.tsx`
- `frontend/src/components/app-nav.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/adapt.ts`
- `frontend/src/app/globals.css`
- `frontend/eslint.config.mjs`

