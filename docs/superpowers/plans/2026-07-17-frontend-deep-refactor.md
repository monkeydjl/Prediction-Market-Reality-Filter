# Frontend Deep Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up World Cup residue, unify all `sport-*-api.ts` clients onto SWR, redesign navigation into two groups, organize loose components, and expand test coverage — all as a pure frontend refactor with zero backend changes and zero regressions.

**Architecture:** Six sequential tasks execute bottom-up: (1) migrate World Cup lib files into `lib/world-cup/` and switch from `getWorldCupApiBase()` to `getApiBase()`; (2) migrate World Cup components into `components/sports/world-cup/` and the route to `app/sports/world-cup/`; (3) strip `worldCup*` methods and `WorldCup*` types from `lib/api.ts`; (4) create a unified `lib/sports-api/` SWR module replacing 8 bare-fetch clients; (5) redesign `app-nav.tsx` into two groups; (6) move 5 loose components into `components/sports/common/` and add test coverage for untested components. Each task ends with a green test suite and a commit.

**Tech Stack:** Next.js 16.2.9, React 19.2.4, SWR 2.4.2, TypeScript 5, Tailwind v4, vitest 4.1.9, @testing-library/react 16.3.2, lucide-react

## Global Constraints

- Zero backend modifications — this is a pure frontend refactor
- All 64 existing tests must pass (after import path updates) — zero regressions
- Static export compatibility — `next.config.ts` `output: "export"` + `trailingSlash: true` must remain functional; no Next.js route groups
- No new dependencies — use existing `swr`, `react`, `lucide-react`, `vitest`, `@testing-library/react`
- TDD where applicable — new `lib/sports-api/` hooks and `sportPost<T>()` follow RED → GREEN
- Import paths use `@/` alias — consistent with existing codebase
- Component behavior unchanged — only imports, file locations, and API client patterns change
- Subagent-driven task execution — each task dispatched to a fresh subagent with TDD + inter-task review
- `NEXT_PUBLIC_API_BASE` is the single source of truth for the API base URL — `NEXT_PUBLIC_API_BASE_URL` is no longer referenced after Task 4

## Key Technical Decisions (verified from code)

### SWR fetcher strategy

The global `SWRProvider` (`frontend/src/components/providers/swr-provider.tsx`) already configures a `swrFetcher` that handles 60s timeout, `X-API-Key` / `X-Operator` header injection, and error localization via `buildApiErrorMessage`. Its global config sets `revalidateOnFocus: false`, `dedupingInterval: 30_000`, `errorRetryCount: 2`.

**Consequence:** New SWR hooks call `useSWR<T>(key)` with **only the key** — no custom fetcher, no per-hook config (the global defaults are inherited). This eliminates the need for a `sportFetch<T>()` GET wrapper.

### SWR key format

The SWR key is the **full URL** `${getApiBase()}${path}`, because `swrFetcher` receives the key as the URL and passes it directly to `fetch()`. `getApiBase()` returns consistent values in SSR and CSR (both based on `NEXT_PUBLIC_API_BASE`), so the key is stable.

- `getApiBase()` returns `/api` (default) or `http://localhost:8000/api` (local static) or a full URL ending in `/api`
- The `path` argument must **NOT** include the `/api` prefix — `getApiBase()` already provides it
- Example: `getApiBase()` = `/api`, path = `/predictions/matches?sport=nba`, key = `/api/predictions/matches?sport=nba`

### `getWorldCupApiBase()` → `getApiBase()` migration rule

Old World Cup lib files use `getWorldCupApiBase()` (returns `""` for relative, `http://localhost:8000` for local static) and paths WITH `/api` prefix (e.g., `${API_BASE}/api/world-cup/predictions/matches`).

When migrating to `getApiBase()` (returns `/api` for relative, `http://localhost:8000/api` for local static), the `/api` prefix must be **REMOVED** from path strings to avoid double-prefixing.

**Before (old):**
```ts
const API_BASE = getWorldCupApiBase(); // ""
const url = `${API_BASE}/api/world-cup/predictions/matches`; // "/api/world-cup/predictions/matches"
```

**After (new):**
```ts
const API_BASE = getApiBase(); // "/api"
const url = `${API_BASE}/world-cup/predictions/matches`; // "/api/world-cup/predictions/matches"
```

### Mutation strategy (POST endpoints)

POST mutations cannot use the global SWR fetcher (which is GET-only). A dedicated `sportPost<T>(path, body?)` wrapper in `lib/sports-api/client.ts` mirrors `swrFetcher`'s auth + timeout + error localization but uses `method: "POST"`. Mutation functions are plain async exports (not hooks) that call `sportPost<T>()` and then invalidate relevant SWR cache keys via the global `mutate()` from `swr`.

### Verified API signatures (from source code)

The following signatures are verified from the actual source files. Hooks preserve these signatures to maintain caller compatibility:

| Old function | Old file | Signature |
|---|---|---|
| `fetchMatches` | `lib/sports-api.ts` | `(sport?: string) → MatchSummary[]` |
| `fetchMatchDetail` | `lib/sports-api.ts` | `(matchId: string) → { match, prediction }` (404 throws `NotFoundError`) |
| `triggerPrediction` | `lib/sports-api.ts` | `(matchId: string) → PredictionResult` |
| `fetchEngineScores` | `lib/learning-api.ts` | `(params?: { engine?, competition?, sport? }) → EngineScoreItem[]` |
| `fetchPredictionHistory` | `lib/learning-api.ts` | `(params?: { sport?, competition?, limit?, offset? }) → PredictionHistoryList` |
| `fetchPredictionTrajectory` | `lib/learning-api.ts` | `(matchId: string) → PredictionTrajectory` |
| `fetchCalibration` | `lib/learning-api.ts` | `(params?: { engine?, competition? }) → CalibrationItem[]` |
| `fetchReliability` | `lib/learning-api.ts` | `(params?: { engine?, competition?, bins? }) → ReliabilityData` |
| `fetchMarketLinks` | `lib/sport-markets-api.ts` | `(params?: { match_id?, source?, verified? }) → MarketLinkList` |
| `fetchMarketLinksByMatch` | `lib/sport-markets-api.ts` | `(matchId: string) → MarketLinkList` |
| `fetchLatestLinks` | `lib/sport-markets-api.ts` | `(matchId: string) → { items: LatestLink[], total }` |
| `fetchPendingLinks` | `lib/sport-markets-api.ts` | `() → MarketLinkList` |
| `verifyLink` | `lib/sport-markets-api.ts` | `(matchId, contractId, verified: boolean) → void` — POST `/api/sport-markets/links/${matchId}/${contractId}/verify` with body `{ verified }` |
| `fetchMarketSnapshots` | `lib/sport-markets-api.ts` | `(matchId: string) → { series: SnapshotSeries[] }` |
| `fetchTraditionalOddsLatest` | `lib/sport-odds-api.ts` | `(matchId: string) → TraditionalOddsLatest` |
| `fetchTraditionalOddsHistory` | `lib/sport-odds-api.ts` | `(matchId: string, mappedOutcome?: string) → TraditionalOddsHistory` |
| `fetchRecommendation` | `lib/sport-recommendations-api.ts` | `(matchId: string) → SportRecommendation` |
| `fetchOpenDecisions` | `lib/sport-recommendations-api.ts` | `(params?: { limit?, decision? }) → RecommendationList` |
| `fetchTopPicks` | `lib/sport-recommendations-api.ts` | `(params?: { limit?, min_abs_edge? }) → RecommendationList` |
| `fetchSettlement` | `lib/sport-settlements-api.ts` | `(matchId: string) → SettlementList` |
| `fetchSettlementHistory` | `lib/sport-settlements-api.ts` | `(limit = 20, engine?: string) → SettlementList` |
| `fetchCalibrations` | `lib/sport-settlements-api.ts` | `(engine?: string, competition?: string) → CalibrationList` |
| `fetchAvailableFutures` | `lib/futures-api.ts` | `() → AvailableFuturesResponse` |
| `fetchFuturesLinks` | `lib/futures-api.ts` | `(competition, season) → FuturesLinksResponse` |
| `fetchLatestSnapshots` | `lib/futures-api.ts` | `(competition, season) → FuturesSnapshotsResponse` |
| `fetchOptimizationParams` | `lib/optimization-api.ts` | `() → OptimizedParams[]` |
| `triggerOptimization` | `lib/optimization-api.ts` | `(sport: string, nTrials = 150) → { task_id: string }` — body `{ sport, n_trials }` |

---

## Task 1: World Cup lib migration

**Goal:** Create `lib/world-cup/` directory, migrate 7 lib files, switch from `getWorldCupApiBase()` to `getApiBase()` (removing `/api` from paths), update all consumers, delete old files.

**Files:**
- Create: `frontend/src/lib/world-cup/predictions-api.ts` (migrated from `lib/world-cup-predictions.ts`)
- Create: `frontend/src/lib/world-cup/analytics-api.ts` (migrated from `lib/analytics-api.ts`)
- Create: `frontend/src/lib/world-cup/time.ts` (migrated from `lib/world-cup-time.ts`)
- Create: `frontend/src/lib/world-cup/group-standings.ts` (migrated from `lib/group-standings.ts`)
- Create: `frontend/src/lib/world-cup/qualification-probability.ts` (migrated from `lib/qualification-probability.ts`)
- Create: `frontend/src/lib/world-cup/team-names-zh.ts` (migrated from `lib/team-names-zh.ts`)
- Create: `frontend/src/lib/world-cup/swr-hooks.ts` (migrated from `lib/swr-hooks.ts`)
- Modify: `frontend/src/lib/world-cup-predictions.test.ts` → move to `frontend/src/lib/world-cup/predictions-api.test.ts`
- Modify: `frontend/src/lib/learning-api.test.ts` → delete (consolidated into sports-api tests in Task 4)
- Modify: `frontend/src/lib/qualification-probability.test.ts` → move to `frontend/src/lib/world-cup/qualification-probability.test.ts`
- Delete: `frontend/src/lib/world-cup-predictions.ts`, `frontend/src/lib/analytics-api.ts`, `frontend/src/lib/world-cup-time.ts`, `frontend/src/lib/group-standings.ts`, `frontend/src/lib/qualification-probability.ts`, `frontend/src/lib/team-names-zh.ts`, `frontend/src/lib/swr-hooks.ts`
- Modify: all consumers of the old paths (enumerated below)

**Interfaces:**
- Consumes: `getApiBase` from `@/lib/env`, `buildApiErrorMessage` / `getOperatorApiKey` / `getOperatorId` from `@/lib/api`
- Produces: same exported function/type names as before, from new `@/lib/world-cup/*` paths

**Consumer import map (old → new):**
| Old import | New import |
|---|---|
| `@/lib/world-cup-predictions` | `@/lib/world-cup/predictions-api` |
| `@/lib/analytics-api` | `@/lib/world-cup/analytics-api` |
| `@/lib/world-cup-time` | `@/lib/world-cup/time` |
| `@/lib/group-standings` | `@/lib/world-cup/group-standings` |
| `@/lib/qualification-probability` | `@/lib/world-cup/qualification-probability` |
| `@/lib/team-names-zh` | `@/lib/world-cup/team-names-zh` |
| `@/lib/swr-hooks` | `@/lib/world-cup/swr-hooks` |

- [ ] **Step 1: Create `lib/world-cup/` directory and migrate `predictions-api.ts`**

Read `frontend/src/lib/world-cup-predictions.ts` in full. Create `frontend/src/lib/world-cup/predictions-api.ts` with identical content, except:

1. Change `import { getWorldCupApiBase } from "./env";` → `import { getApiBase } from "@/lib/env";`
2. Change `import { buildApiErrorMessage, getOperatorApiKey, getOperatorId } from "./api";` → `import { buildApiErrorMessage, getOperatorApiKey, getOperatorId } from "@/lib/api";`
3. Change `const API_BASE = getWorldCupApiBase();` → `const API_BASE = getApiBase();`
4. In every URL template literal, remove the `/api` prefix after `${API_BASE}`:
   - `${API_BASE}/api/world-cup/predictions/matches` → `${API_BASE}/world-cup/predictions/matches`
   - `${API_BASE}/api/world-cup/predictions/matches/${matchId}` → `${API_BASE}/world-cup/predictions/matches/${matchId}`
   - `${API_BASE}/api/world-cup/predictions/matches/${matchId}/prediction-history` → `${API_BASE}/world-cup/predictions/matches/${matchId}/prediction-history`
   - `${API_BASE}/api/world-cup/predictions/today` → `${API_BASE}/world-cup/predictions/today`
   - `${API_BASE}/api/world-cup/predictions/matches/${matchId}/predict` → `${API_BASE}/world-cup/predictions/matches/${matchId}/predict`
   - `${API_BASE}/api/world-cup/predictions/matches/${matchId}/analyze` → `${API_BASE}/world-cup/predictions/matches/${matchId}/analyze`
   - `${API_BASE}/api/world-cup/predictions/sync-fixtures` → `${API_BASE}/world-cup/predictions/sync-fixtures`

All exported types and function names stay identical.

- [ ] **Step 2: Migrate `analytics-api.ts`**

Read `frontend/src/lib/analytics-api.ts` in full. Create `frontend/src/lib/world-cup/analytics-api.ts` with identical content, except:

1. `import { getWorldCupApiBase } from "./env";` → `import { getApiBase } from "@/lib/env";`
2. `import { buildApiErrorMessage, getOperatorApiKey, getOperatorId } from "./api";` → `import { buildApiErrorMessage, getOperatorApiKey, getOperatorId } from "@/lib/api";`
3. `const API_BASE = getWorldCupApiBase();` → `const API_BASE = getApiBase();`
4. Remove `/api` prefix from all URL template literals (e.g., `${API_BASE}/api/analytics/...` → `${API_BASE}/analytics/...`)

- [ ] **Step 3: Migrate `time.ts`, `group-standings.ts`, `qualification-probability.ts`, `team-names-zh.ts`**

These files are pure-logic modules (no API calls). Create the new files by copying the old content verbatim — no import changes needed (they don't import from `env.ts`). The file moves are:

- `lib/world-cup-time.ts` → `lib/world-cup/time.ts` (copy verbatim)
- `lib/group-standings.ts` → `lib/world-cup/group-standings.ts` (copy verbatim)
- `lib/qualification-probability.ts` → `lib/world-cup/qualification-probability.ts` (copy verbatim)
- `lib/team-names-zh.ts` → `lib/world-cup/team-names-zh.ts` (copy verbatim)

- [ ] **Step 4: Migrate `swr-hooks.ts`**

Read `frontend/src/lib/swr-hooks.ts` in full. Create `frontend/src/lib/world-cup/swr-hooks.ts` with identical content, except:

1. `import { buildApiErrorMessage, getOperatorApiKey, getOperatorId } from "./api";` → `import { buildApiErrorMessage, getOperatorApiKey, getOperatorId } from "@/lib/api";`
2. `import { getWorldCupApiBase } from "./env";` → `import { getApiBase } from "@/lib/env";`
3. `import type { MatchWithPrediction } from "./world-cup-predictions";` → `import type { MatchWithPrediction } from "./predictions-api";`
4. `const API_BASE = getWorldCupApiBase();` → `const API_BASE = getApiBase();`
5. `const url = \`${API_BASE}/api/world-cup/predictions/matches?${query}\`;` → `const url = \`${API_BASE}/world-cup/predictions/matches?${query}\`;`

The custom `matchesFetcher` stays as-is (it does `data.matches ?? []` extraction that the global fetcher cannot do).

- [ ] **Step 5: Migrate test files**

Move `frontend/src/lib/world-cup-predictions.test.ts` → `frontend/src/lib/world-cup/predictions-api.test.ts`. Update its imports:
- `import ... from "./world-cup-predictions"` → `import ... from "./predictions-api"`
- `vi.mock("./env", ...)` → mock `@/lib/env` with `getApiBase: () => "http://localhost:8000/api"` (note: now returns `/api`-suffixed value)
- Update all URL assertions: `"http://localhost:8000/api/world-cup/predictions/matches"` → `"http://localhost:8000/api/world-cup/predictions/matches"` (unchanged, because `getApiBase()` returns `http://localhost:8000/api` and path is `/world-cup/predictions/matches`)

Move `frontend/src/lib/qualification-probability.test.ts` → `frontend/src/lib/world-cup/qualification-probability.test.ts`. Update its import: `from "./qualification-probability"` → `from "./qualification-probability"` (stays same, relative path resolves within new dir). No other changes needed since this is pure logic.

Delete `frontend/src/lib/learning-api.test.ts` — its coverage is replaced by `lib/sports-api/` hook tests in Task 4.

- [ ] **Step 6: Update all consumers**

Search for all files importing from the old paths and update them. The consumers are:

1. `frontend/src/app/world-cup/page.tsx` — update 5 imports:
   - `@/lib/world-cup-predictions` → `@/lib/world-cup/predictions-api`
   - `@/lib/swr-hooks` → `@/lib/world-cup/swr-hooks`
   - `@/lib/group-standings` → `@/lib/world-cup/group-standings`
   - `@/lib/qualification-probability` → `@/lib/world-cup/qualification-probability`
   - `@/lib/team-names-zh` → `@/lib/world-cup/team-names-zh`
   - `@/lib/world-cup-time` → `@/lib/world-cup/time`

2. `frontend/src/components/world-cup/analytics-dashboard.tsx` — update `@/lib/analytics-api` → `@/lib/world-cup/analytics-api`

3. All `frontend/src/components/world-cup/*.tsx` that import from `@/lib/world-cup-predictions` — update to `@/lib/world-cup/predictions-api`. Search with: `Grep` for `@/lib/world-cup-predictions` in `frontend/src/components/world-cup/`.

4. All `frontend/src/components/world-cup/*.tsx` that import from `@/lib/world-cup-time`, `@/lib/group-standings`, `@/lib/qualification-probability`, `@/lib/team-names-zh` — update to `@/lib/world-cup/*`.

5. Any `frontend/src/components/world-cup/*.test.tsx` that import from old paths — update to new paths.

Run a grep to find all remaining references:
```
Grep pattern: "@/lib/(world-cup-predictions|analytics-api|world-cup-time|group-standings|qualification-probability|team-names-zh|swr-hooks)"
path: frontend/src
```
Update every match to the new `@/lib/world-cup/*` path.

- [ ] **Step 7: Delete old lib files**

Delete these 7 files:
- `frontend/src/lib/world-cup-predictions.ts`
- `frontend/src/lib/analytics-api.ts`
- `frontend/src/lib/world-cup-time.ts`
- `frontend/src/lib/group-standings.ts`
- `frontend/src/lib/qualification-probability.ts`
- `frontend/src/lib/team-names-zh.ts`
- `frontend/src/lib/swr-hooks.ts`

Also delete `frontend/src/lib/learning-api.test.ts`.

- [ ] **Step 8: Run tests to verify no regressions**

Run: `cd frontend; npx vitest run`
Expected: ALL tests pass (64 minus the deleted `learning-api.test.ts` = 63 files, plus the 2 moved test files retain their counts). If any test fails due to an import path, fix the import and re-run.

- [ ] **Step 9: Commit**

```bash
cd frontend
git add -A
git commit -m "refactor(frontend): task 1 - migrate World Cup lib files to lib/world-cup/"
```

---

## Task 2: World Cup component migration

**Goal:** Create `components/sports/world-cup/`, migrate 9 components, delete 3 superseded components, delete 2 dashboard orphans, migrate the route `app/world-cup/` → `app/sports/world-cup/`, update `navigation-shell.test.ts`.

**Files:**
- Create: `frontend/src/components/sports/world-cup/` (directory)
- Move 9 components (+ their test files) from `components/world-cup/` to `components/sports/world-cup/`:
  - `analytics-dashboard.tsx` + `.test.tsx`
  - `engine-comparison-card.tsx` + `.test.tsx`
  - `engine-comparison-view.tsx` + `.test.tsx`
  - `knockout-view.tsx`
  - `match-prediction-card.tsx` + `.test.tsx` + `.rule-label.test.tsx`
  - `prediction-analysis-card.tsx`
  - `prediction-history-card.tsx` + `.test.tsx`
  - `qualification-table.tsx` + `.test.tsx`
  - `tournament-simulation.tsx` + `.test.tsx`
- Delete: `components/world-cup/batch-engine-switcher.tsx` + `.test.tsx`
- Delete: `components/world-cup/engine-auto-tune-dashboard.tsx` + `.test.tsx`
- Delete: `components/world-cup/group-standings-table.tsx` (orphan, no test)
- Delete: `components/dashboard/world-cup-data-sources.tsx` + `.test.tsx`
- Delete: `components/dashboard/world-cup-resolution-panel.tsx` + `.test.tsx`
- Move: `app/world-cup/page.tsx` → `app/sports/world-cup/page.tsx`
- Delete: `app/world-cup/` (entire directory after page move)
- Modify: `app/navigation-shell.test.ts`

**Interfaces:**
- Consumes: Task 1's `@/lib/world-cup/*` modules
- Produces: `@/components/sports/world-cup/*` component paths for consumers

- [ ] **Step 1: Move 9 components to `components/sports/world-cup/`**

For each of the 9 components listed above, read the original file and write it to the new location. In the new file, update intra-`world-cup/` imports:

| Old import | New import |
|---|---|
| `@/components/world-cup/match-prediction-card` | `@/components/sports/world-cup/match-prediction-card` |
| `@/components/world-cup/engine-comparison-card` | `@/components/sports/world-cup/engine-comparison-card` |
| `@/components/world-cup/prediction-analysis-card` | `@/components/sports/world-cup/prediction-analysis-card` |
| `@/components/world-cup/prediction-history-card` | `@/components/sports/world-cup/prediction-history-card` |
| `@/components/world-cup/qualification-table` | `@/components/sports/world-cup/qualification-table` |
| `@/components/world-cup/knockout-view` | `@/components/sports/world-cup/knockout-view` |
| `@/components/world-cup/tournament-simulation` | `@/components/sports/world-cup/tournament-simulation` |
| `@/components/world-cup/analytics-dashboard` | `@/components/sports/world-cup/analytics-dashboard` |
| `@/components/world-cup/engine-comparison-view` | `@/components/sports/world-cup/engine-comparison-view` |

The `@/lib/world-cup/*` imports from Task 1 are already correct — no change needed.

Do the same for all `.test.tsx` files: update their component imports from `@/components/world-cup/*` to `@/components/sports/world-cup/*`.

- [ ] **Step 2: Delete 3 superseded + orphan components**

Delete:
- `frontend/src/components/world-cup/batch-engine-switcher.tsx`
- `frontend/src/components/world-cup/batch-engine-switcher.test.tsx`
- `frontend/src/components/world-cup/engine-auto-tune-dashboard.tsx`
- `frontend/src/components/world-cup/engine-auto-tune-dashboard.test.tsx`
- `frontend/src/components/world-cup/group-standings-table.tsx`

- [ ] **Step 3: Delete 2 dashboard orphans**

Delete:
- `frontend/src/components/dashboard/world-cup-data-sources.tsx`
- `frontend/src/components/dashboard/world-cup-data-sources.test.tsx`
- `frontend/src/components/dashboard/world-cup-resolution-panel.tsx`
- `frontend/src/components/dashboard/world-cup-resolution-panel.test.tsx`

- [ ] **Step 4: Migrate route `app/world-cup/page.tsx` → `app/sports/world-cup/page.tsx`**

Read `frontend/src/app/world-cup/page.tsx` and write it to `frontend/src/app/sports/world-cup/page.tsx`. In the new file:

1. Update component imports:
   - `@/components/world-cup/match-prediction-card` → `@/components/sports/world-cup/match-prediction-card`
   - `@/components/world-cup/qualification-table` → `@/components/sports/world-cup/qualification-table`
   - `@/components/world-cup/knockout-view` → `@/components/sports/world-cup/knockout-view`
   - `@/components/world-cup/engine-comparison-view` → `@/components/sports/world-cup/engine-comparison-view`
   - `@/components/world-cup/engine-auto-tune-dashboard` → **DELETE this import and its usage** (component deleted in Step 2)
   - `@/components/world-cup/batch-engine-switcher` → **DELETE this import and its usage** (component deleted in Step 2)
   - `@/components/world-cup/tournament-simulation` → `@/components/sports/world-cup/tournament-simulation`
   - `@/components/world-cup/analytics-dashboard` → `@/components/sports/world-cup/analytics-dashboard`

2. The `@/lib/world-cup/*` imports are already correct from Task 1.

3. Remove the `"auto-tune"` tab from `TabView` type, `STAGE_LABELS`, the tab button, and the tab content section that renders `<BatchEngineSwitcher>` and `<EngineAutoTuneDashboard>`. Specifically:
   - Remove `"auto-tune"` from the `TabView` union type
   - Remove the `EngineAutoTuneDashboard` and `BatchEngineSwitcher` dynamic imports
   - Remove the `auto-tune` tab button from the tab navigation
   - Remove the `{activeTab === "auto-tune" && (...)}` content block

4. Delete the old `frontend/src/app/world-cup/` directory entirely.

- [ ] **Step 5: Update `navigation-shell.test.ts`**

Modify `frontend/src/app/navigation-shell.test.ts`. In the array of files that should NOT contain `<AppNav />`:

- Remove `"world-cup/page.tsx"` (route migrated)
- Add `"sports/world-cup/page.tsx"` (new route location)
- Add all other `sports/*` routes that are full pages (not `[matchId]` dynamic segments):

```ts
for (const file of [
  "page.tsx",
  "loading.tsx",
  "error.tsx",
  "not-found.tsx",
  "analyze/page.tsx",
  "decisions/page.tsx",
  "edges/page.tsx",
  "events/page.tsx",
  "history/page.tsx",
  "quality/page.tsx",
  "quality-metrics/page.tsx",
  "trades/page.tsx",
  "sports/world-cup/page.tsx",
  "sports/futures/page.tsx",
  "sports/learning/page.tsx",
  "sports/markets/page.tsx",
  "sports/optimization/page.tsx",
  "sports/recommendations/page.tsx",
  "sports/settlements/page.tsx",
]) {
  expect(readAppFile(file), file).not.toContain("<AppNav />");
}
```

- [ ] **Step 6: Delete `components/world-cup/` directory**

After moving all 9 components and deleting the 3 superseded ones, the `frontend/src/components/world-cup/` directory should be empty. Delete it.

- [ ] **Step 7: Run tests to verify no regressions**

Run: `cd frontend; npx vitest run`
Expected: All tests pass. If any test fails due to a missing import or component reference, fix it and re-run.

- [ ] **Step 8: Commit**

```bash
cd frontend
git add -A
git commit -m "refactor(frontend): task 2 - migrate World Cup components to components/sports/world-cup/"
```

---

## Task 3: lib/api.ts cleanup

**Goal:** Remove 8 `worldCup*` methods and 14 `WorldCup*` types from `lib/api.ts`. These were only used by the deleted dashboard orphans and have no remaining callers after Task 2.

**Files:**
- Modify: `frontend/src/lib/api.ts` — remove the 8 methods + 14 types + `WORLD_CUP_DATA_SOURCE_ACTION_PATHS` constant
- Modify: `frontend/src/lib/api.test.ts` — remove any tests for the deleted methods (if present)

**Interfaces:**
- Consumes: Task 2's deletion of dashboard orphans (which were the only callers)
- Produces: a slimmer `eventsApi` object with no `worldCup*` methods

- [ ] **Step 1: Verify no remaining callers of the 8 `worldCup*` methods**

Run a grep to confirm zero references to `worldCupDataSourcesStatus`, `worldCupDataSourcePreview`, `worldCupDataSourceImport`, `worldCupResolveDryRun`, `worldCupApiFootballTest`, `worldCupApiFootballValidate`, `worldCupSportmonksTest`, `worldCupSportmonksValidate` outside of `lib/api.ts` and `lib/api.test.ts`:

```
Grep pattern: "worldCup(DataSources|DataSource|Resolve|ApiFootball|Sportmonks)"
path: frontend/src
glob: !**/api.ts
```

Expected: zero matches (the dashboard orphans that called these were deleted in Task 2).

- [ ] **Step 2: Remove the 14 `WorldCup*` types from `lib/api.ts`**

Delete these interface/type declarations from `frontend/src/lib/api.ts`:

1. `WorldCupSourceFetch`
2. `WorldCupSkippedSource`
3. `WorldCupCallBudget`
4. `WorldCupRunSummary`
5. `WorldCupFileConfig`
6. `WorldCupUrlConfig`
7. `WorldCupFeedConfig`
8. `WorldCupDataSourceStatus`
9. `WorldCupDataSourceActionMode`
10. `WorldCupDataSourceActionResult`
11. `WorldCupResolveMatch`
12. `WorldCupResolveResult`
13. `WorldCupApiFootballConnectionResult`
14. `WorldCupSportmonksConnectionResult`
15. `WorldCupPipelineValidateResult`

Also delete the `WORLD_CUP_DATA_SOURCE_ACTION_PATHS` constant.

- [ ] **Step 3: Remove the 8 `worldCup*` methods from the `eventsApi` object**

Delete these methods from the `eventsApi` object in `frontend/src/lib/api.ts`:

1. `worldCupDataSourcesStatus`
2. `worldCupDataSourcePreview`
3. `worldCupDataSourceImport`
4. `worldCupResolveDryRun`
5. `worldCupApiFootballTest`
6. `worldCupApiFootballValidate`
7. `worldCupSportmonksTest`
8. `worldCupSportmonksValidate`

- [ ] **Step 4: Remove tests for deleted methods from `api.test.ts`**

Read `frontend/src/lib/api.test.ts`. Delete any `describe` or `it` blocks that test the 8 removed `worldCup*` methods or reference the 14 removed types.

- [ ] **Step 5: Run tests to verify no regressions**

Run: `cd frontend; npx vitest run`
Expected: All tests pass. The TypeScript compiler should also not report any errors because no remaining code references the deleted methods/types.

- [ ] **Step 6: Commit**

```bash
cd frontend
git add -A
git commit -m "refactor(frontend): task 3 - remove worldCup* methods and WorldCup* types from lib/api.ts"
```

---

## Task 4: lib/sports-api/ SWR module

**Goal:** Create a unified `lib/sports-api/` module with `sportPost<T>()` for POST mutations and 8 SWR hook files that replace the 8 old bare-fetch clients. Update all consumers. Delete old clients and `getWorldCupApiBase()`.

**Files:**
- Create: `frontend/src/lib/sports-api/index.ts`
- Create: `frontend/src/lib/sports-api/client.ts`
- Create: `frontend/src/lib/sports-api/types.ts`
- Create: `frontend/src/lib/sports-api/hooks/use-matches.ts` + `.test.ts`
- Create: `frontend/src/lib/sports-api/hooks/use-learning.ts` + `.test.ts`
- Create: `frontend/src/lib/sports-api/hooks/use-markets.ts` + `.test.ts`
- Create: `frontend/src/lib/sports-api/hooks/use-odds.ts` + `.test.ts`
- Create: `frontend/src/lib/sports-api/hooks/use-recommendations.ts` + `.test.ts`
- Create: `frontend/src/lib/sports-api/hooks/use-settlements.ts` + `.test.ts`
- Create: `frontend/src/lib/sports-api/hooks/use-futures.ts` + `.test.ts`
- Create: `frontend/src/lib/sports-api/hooks/use-optimization.ts` + `.test.ts`
- Delete: `frontend/src/lib/sports-api.ts` + `frontend/src/lib/sports-api.test.ts`
- Delete: `frontend/src/lib/learning-api.ts` (test already deleted in Task 1)
- Delete: `frontend/src/lib/sport-markets-api.ts`, `frontend/src/lib/sport-odds-api.ts`, `frontend/src/lib/sport-recommendations-api.ts`, `frontend/src/lib/sport-settlements-api.ts`, `frontend/src/lib/futures-api.ts`, `frontend/src/lib/optimization-api.ts`
- Modify: `frontend/src/lib/env.ts` — delete `getWorldCupApiBase()`
- Modify: `frontend/src/lib/env.test.ts` — delete the `getWorldCupApiBase` describe block
- Modify: all `app/sports/**` pages and `components/sports/**` that import from old clients

**Interfaces:**
- Consumes: `getApiBase` from `@/lib/env`, `buildApiErrorMessage` / `getOperatorApiKey` / `getOperatorId` from `@/lib/api`, `useSWR` / `mutate` from `swr`
- Produces: all hooks and types re-exported from `@/lib/sports-api`

- [ ] **Step 1: Write failing test for `sportPost<T>()`**

Create `frontend/src/lib/sports-api/client.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({
  getApiBase: () => "http://localhost:8000/api",
}));

vi.mock("@/lib/api", () => ({
  buildApiErrorMessage: (status: number, body: string) =>
    `localized error ${status}: ${body}`,
  getOperatorApiKey: () => "test-key",
  getOperatorId: () => "test-operator",
}));

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

import { sportPost } from "./client";

afterEach(() => {
  fetchMock.mockReset();
});

describe("sportPost", () => {
  beforeEach(() => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ result: "ok" }),
    });
  });

  it("sends a POST request to the correct URL", async () => {
    await sportPost("/predictions/matches/m1/predict");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/matches/m1/predict",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("injects auth headers and content-type", async () => {
    await sportPost("/test", { foo: "bar" });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-API-Key"]).toBe("test-key");
    expect(init.headers["X-Operator"]).toBe("test-operator");
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("serializes body as JSON", async () => {
    await sportPost("/test", { verified: true });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.body).toBe(JSON.stringify({ verified: true }));
  });

  it("omits body when not provided", async () => {
    await sportPost("/test");
    const [, init] = fetchMock.mock.calls[0];
    expect(init.body).toBeUndefined();
  });

  it("returns parsed JSON", async () => {
    const result = await sportPost<{ result: string }>("/test");
    expect(result).toEqual({ result: "ok" });
  });

  it("throws a localized error on non-2xx response", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => "internal error",
    });
    await expect(sportPost("/test")).rejects.toThrow("localized error 500: internal error");
  });

  it("throws a localized network error on TypeError", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(sportPost("/test")).rejects.toThrow("无法连接到服务器");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend; npx vitest run src/lib/sports-api/client.test.ts`
Expected: FAIL — `Cannot find module './client'`

- [ ] **Step 3: Implement `sportPost<T>()`**

Create `frontend/src/lib/sports-api/client.ts`:

```ts
import { getApiBase } from "@/lib/env";
import { buildApiErrorMessage, getOperatorApiKey, getOperatorId } from "@/lib/api";

const SPORT_POST_TIMEOUT_MS = 60_000;

/**
 * POST wrapper for sport mutations. Mirrors the global `swrFetcher`'s
 * auth + timeout + error-localization behavior but uses `method: "POST"`.
 * Use this for mutations that invalidate SWR cache keys via the global
 * `mutate()` from "swr".
 */
export async function sportPost<T>(path: string, body?: unknown): Promise<T> {
  const url = `${getApiBase()}${path}`;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const operatorKey = getOperatorApiKey();
  if (operatorKey) headers["X-API-Key"] = operatorKey;
  const operatorId = getOperatorId();
  if (operatorId) headers["X-Operator"] = operatorId;

  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), SPORT_POST_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    if (!response.ok) {
      const bodyText = await response.text();
      throw new Error(buildApiErrorMessage(response.status, bodyText));
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("请求超时，请稍后重试");
    }
    if (error instanceof TypeError) {
      throw new Error("无法连接到服务器，请检查网络或后端服务状态");
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend; npx vitest run src/lib/sports-api/client.test.ts`
Expected: PASS — all 7 tests green

- [ ] **Step 5: Create `types.ts` with all consolidated types**

Create `frontend/src/lib/sports-api/types.ts` by consolidating all type definitions from the 8 old client files. Copy each interface verbatim from its source file:

```ts
// From lib/sports-api.ts
export interface MatchSummary { ... }
export interface MatchDetail { ... }
export interface ContributionItem { ... }
export interface PredictionResult { ... }
export class NotFoundError extends Error {}

// From lib/learning-api.ts
export interface EngineScoreItem { ... }
export interface PredictionHistoryItem { ... }
export interface PredictionHistoryList { ... }
export interface PredictionTrajectory { ... }
export interface CalibrationItem { ... }
export interface ReliabilityBin { ... }
export interface ReliabilityData { ... }

// From lib/sport-markets-api.ts
export interface MarketLink { ... }
export interface MarketLinkList { ... }
export interface LatestLink extends MarketLink { ... }
export interface SnapshotPoint { ... }
export interface SnapshotSeries { ... }

// From lib/sport-odds-api.ts
export interface TraditionalOddsSnapshot { ... }
export interface TraditionalOddsSeries { ... }
export interface TraditionalOddsHistory { ... }
export interface TraditionalOddsLatest { ... }

// From lib/sport-recommendations-api.ts
export interface SportRecommendation { ... }
export interface RecommendationList { ... }

// From lib/sport-settlements-api.ts
export interface MarketSettlement { ... }
export interface MarketCalibration { ... }
export interface SettlementList { ... }
export interface CalibrationList { ... }

// From lib/futures-api.ts
export interface FuturesPair { ... }
export interface FuturesLink { ... }
export interface FuturesSnapshot { ... }
export interface AvailableFuturesResponse { ... }
export interface FuturesLinksResponse { ... }
export interface FuturesSnapshotsResponse { ... }

// From lib/optimization-api.ts
export interface OptimizedParams { ... }
```

Copy each interface's full body from the source files read earlier in this plan. Do NOT abbreviate — every field must be present.

- [ ] **Step 6: Write failing tests for all 8 hook files**

Create the following 8 test files. They all follow the same pattern: mock `swr` to capture the key, call the hook, assert the key.

Create `frontend/src/lib/sports-api/hooks/use-matches.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
vi.mock("swr", () => ({ default: useSWRMock, mutate: vi.fn() }));

import { useMatches, useMatchDetail, triggerPrediction } from "./use-matches";
import useSWR from "swr";

describe("useMatches", () => {
  it("builds key without sport param", () => {
    useMatches();
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/matches");
  });

  it("builds key with sport param", () => {
    useMatches("nba");
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/matches?sport=nba");
  });
});

describe("useMatchDetail", () => {
  it("builds key for a matchId", () => {
    useMatchDetail("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/matches/m1");
  });

  it("returns null key when matchId is null", () => {
    useMatchDetail(null);
    expect(useSWR).toHaveBeenCalledWith(null);
  });
});

describe("triggerPrediction", () => {
  it("is a function", () => {
    expect(typeof triggerPrediction).toBe("function");
  });
});
```

Create `frontend/src/lib/sports-api/hooks/use-learning.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
vi.mock("swr", () => ({ default: useSWRMock, mutate: vi.fn() }));

import {
  useEngineScores,
  usePredictionHistory,
  usePredictionTrajectory,
  useCalibration,
  useReliability,
} from "./use-learning";
import useSWR from "swr";

describe("useEngineScores", () => {
  it("builds key without params", () => {
    useEngineScores();
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/engines/scores");
  });

  it("builds key with params", () => {
    useEngineScores({ engine: "elo", competition: "nba" });
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/engines/scores?engine=elo&competition=nba");
  });
});

describe("usePredictionHistory", () => {
  it("builds key with params", () => {
    usePredictionHistory({ sport: "nba", limit: 10 });
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/history?sport=nba&limit=10");
  });
});

describe("usePredictionTrajectory", () => {
  it("builds key for a matchId", () => {
    usePredictionTrajectory("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/history/m1");
  });

  it("returns null key when matchId is null", () => {
    usePredictionTrajectory(null);
    expect(useSWR).toHaveBeenCalledWith(null);
  });
});

describe("useCalibration", () => {
  it("builds key with params", () => {
    useCalibration({ engine: "elo" });
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/calibration?engine=elo");
  });
});

describe("useReliability", () => {
  it("builds key with params", () => {
    useReliability({ bins: 10 });
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/calibration/reliability?bins=10");
  });
});
```

Create `frontend/src/lib/sports-api/hooks/use-markets.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
vi.mock("swr", () => ({ default: useSWRMock, mutate: vi.fn() }));

import {
  useMarketLinks,
  useMarketLinksByMatch,
  useLatestLinks,
  usePendingLinks,
  useMarketSnapshots,
  verifyLink,
} from "./use-markets";
import useSWR from "swr";

describe("useMarketLinks", () => {
  it("builds key without params", () => {
    useMarketLinks();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/links");
  });

  it("builds key with params", () => {
    useMarketLinks({ source: "polymarket", verified: true });
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/links?source=polymarket&verified=true");
  });
});

describe("useMarketLinksByMatch", () => {
  it("builds key for a matchId", () => {
    useMarketLinksByMatch("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/links/m1");
  });
});

describe("useLatestLinks", () => {
  it("builds key for a matchId", () => {
    useLatestLinks("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/links/m1/latest");
  });
});

describe("usePendingLinks", () => {
  it("builds key", () => {
    usePendingLinks();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/pending");
  });
});

describe("useMarketSnapshots", () => {
  it("builds key for a matchId", () => {
    useMarketSnapshots("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/snapshots/m1");
  });
});

describe("verifyLink", () => {
  it("is a function", () => {
    expect(typeof verifyLink).toBe("function");
  });
});
```

Create `frontend/src/lib/sports-api/hooks/use-odds.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
vi.mock("swr", () => ({ default: useSWRMock, mutate: vi.fn() }));

import { useTraditionalOddsLatest, useTraditionalOddsHistory } from "./use-odds";
import useSWR from "swr";

describe("useTraditionalOddsLatest", () => {
  it("builds key for a matchId", () => {
    useTraditionalOddsLatest("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-odds/m1/latest");
  });
});

describe("useTraditionalOddsHistory", () => {
  it("builds key without mappedOutcome", () => {
    useTraditionalOddsHistory("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-odds/m1/history");
  });

  it("builds key with mappedOutcome", () => {
    useTraditionalOddsHistory("m1", "home_win");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-odds/m1/history?mapped_outcome=home_win");
  });
});
```

Create `frontend/src/lib/sports-api/hooks/use-recommendations.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
vi.mock("swr", () => ({ default: useSWRMock, mutate: vi.fn() }));

import { useRecommendation, useOpenDecisions, useTopPicks } from "./use-recommendations";
import useSWR from "swr";

describe("useRecommendation", () => {
  it("builds key for a matchId", () => {
    useRecommendation("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-recommendations/m1");
  });
});

describe("useOpenDecisions", () => {
  it("builds key without params", () => {
    useOpenDecisions();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-recommendations/open");
  });

  it("builds key with params", () => {
    useOpenDecisions({ limit: 5, decision: "act" });
    expect(useSWR).toHaveBeenCalledWith("/api/sport-recommendations/open?limit=5&decision=act");
  });
});

describe("useTopPicks", () => {
  it("builds key with params", () => {
    useTopPicks({ limit: 10, min_abs_edge: 0.05 });
    expect(useSWR).toHaveBeenCalledWith(
      "/api/sport-recommendations/discrepancies?limit=10&min_abs_edge=0.05",
    );
  });
});
```

Create `frontend/src/lib/sports-api/hooks/use-settlements.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
vi.mock("swr", () => ({ default: useSWRMock, mutate: vi.fn() }));

import { useSettlement, useSettlementHistory, useCalibrations } from "./use-settlements";
import useSWR from "swr";

describe("useSettlement", () => {
  it("builds key for a matchId", () => {
    useSettlement("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-settlements/m1");
  });
});

describe("useSettlementHistory", () => {
  it("builds key with defaults", () => {
    useSettlementHistory();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-settlements/history?limit=20");
  });

  it("builds key with params", () => {
    useSettlementHistory(50, "elo");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-settlements/history?limit=50&engine=elo");
  });
});

describe("useCalibrations", () => {
  it("builds key without params", () => {
    useCalibrations();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-settlements/calibrations");
  });

  it("builds key with params", () => {
    useCalibrations("elo", "nba");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-settlements/calibrations?engine=elo&competition=nba");
  });
});
```

Create `frontend/src/lib/sports-api/hooks/use-futures.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
vi.mock("swr", () => ({ default: useSWRMock, mutate: vi.fn() }));

import { useAvailableFutures, useFuturesLinks, useLatestSnapshots } from "./use-futures";
import useSWR from "swr";

describe("useAvailableFutures", () => {
  it("builds key", () => {
    useAvailableFutures();
    expect(useSWR).toHaveBeenCalledWith("/api/futures");
  });
});

describe("useFuturesLinks", () => {
  it("builds key for competition and season", () => {
    useFuturesLinks("nba", "2026");
    expect(useSWR).toHaveBeenCalledWith("/api/futures/nba/2026");
  });
});

describe("useLatestSnapshots", () => {
  it("builds key for competition and season", () => {
    useLatestSnapshots("nba", "2026");
    expect(useSWR).toHaveBeenCalledWith("/api/futures/nba/2026/latest");
  });
});
```

Create `frontend/src/lib/sports-api/hooks/use-optimization.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
vi.mock("swr", () => ({ default: useSWRMock, mutate: vi.fn() }));

import { useOptimizationParams, triggerOptimization } from "./use-optimization";
import useSWR from "swr";

describe("useOptimizationParams", () => {
  it("builds key", () => {
    useOptimizationParams();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-optimization/params");
  });
});

describe("triggerOptimization", () => {
  it("is a function", () => {
    expect(typeof triggerOptimization).toBe("function");
  });
});
```

- [ ] **Step 7: Run all hook tests to verify they fail**

Run: `cd frontend; npx vitest run src/lib/sports-api/hooks/`
Expected: FAIL — all 8 hook modules not found

- [ ] **Step 8: Implement all 8 hook files**

Create `frontend/src/lib/sports-api/hooks/use-matches.ts`:

```ts
"use client";

import useSWR from "swr";
import { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { sportPost } from "../client";
import type { MatchSummary, MatchDetail, PredictionResult, NotFoundError } from "../types";

type MatchDetailResponse = { match: MatchDetail; prediction: PredictionResult | null };

export function useMatches(sport?: string) {
  const params = sport ? `?sport=${sport}` : "";
  const key = `${getApiBase()}/predictions/matches${params}`;
  return useSWR<MatchSummary[]>(key);
}

export function useMatchDetail(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/predictions/matches/${matchId}` : null;
  return useSWR<MatchDetailResponse>(key);
}

export async function triggerPrediction(matchId: string): Promise<PredictionResult> {
  const result = await sportPost<PredictionResult>(
    `/predictions/matches/${matchId}/predict`,
  );
  await mutate(`${getApiBase()}/predictions/matches/${matchId}`);
  return result;
}

export { NotFoundError };
```

Create `frontend/src/lib/sports-api/hooks/use-learning.ts`:

```ts
"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import type {
  EngineScoreItem,
  PredictionHistoryList,
  PredictionTrajectory,
  CalibrationItem,
  ReliabilityData,
} from "../types";

function buildQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${k}=${v}`).join("&");
}

export function useEngineScores(params?: {
  engine?: string;
  competition?: string;
  sport?: string;
}) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/predictions/engines/scores${qs}`;
  return useSWR<EngineScoreItem[]>(key);
}

export function usePredictionHistory(params?: {
  sport?: string;
  competition?: string;
  limit?: number;
  offset?: number;
}) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/predictions/history${qs}`;
  return useSWR<PredictionHistoryList>(key);
}

export function usePredictionTrajectory(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/predictions/history/${matchId}` : null;
  return useSWR<PredictionTrajectory>(key);
}

export function useCalibration(params?: { engine?: string; competition?: string }) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/predictions/calibration${qs}`;
  return useSWR<CalibrationItem[]>(key);
}

export function useReliability(params?: {
  engine?: string;
  competition?: string;
  bins?: number;
}) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/predictions/calibration/reliability${qs}`;
  return useSWR<ReliabilityData>(key);
}
```

Create `frontend/src/lib/sports-api/hooks/use-markets.ts`:

```ts
"use client";

import useSWR from "swr";
import { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { sportPost } from "../client";
import type {
  MarketLinkList,
  LatestLink,
  SnapshotSeries,
} from "../types";

function buildQuery(params: Record<string, string | number | undefined | boolean>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${k}=${v}`).join("&");
}

type LatestLinksResponse = { items: LatestLink[]; total: number };
type SnapshotsResponse = { series: SnapshotSeries[] };

export function useMarketLinks(params?: {
  match_id?: string;
  source?: string;
  verified?: boolean;
}) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/sport-markets/links${qs}`;
  return useSWR<MarketLinkList>(key);
}

export function useMarketLinksByMatch(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-markets/links/${matchId}` : null;
  return useSWR<MarketLinkList>(key);
}

export function useLatestLinks(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-markets/links/${matchId}/latest` : null;
  return useSWR<LatestLinksResponse>(key);
}

export function usePendingLinks() {
  const key = `${getApiBase()}/sport-markets/pending`;
  return useSWR<MarketLinkList>(key);
}

export function useMarketSnapshots(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-markets/snapshots/${matchId}` : null;
  return useSWR<SnapshotsResponse>(key);
}

export async function verifyLink(
  matchId: string,
  contractId: string,
  verified: boolean,
): Promise<void> {
  await sportPost<void>(
    `/sport-markets/links/${matchId}/${contractId}/verify`,
    { verified },
  );
  await mutate(`${getApiBase()}/sport-markets/pending`);
  await mutate(`${getApiBase()}/sport-markets/links/${matchId}`);
}
```

Create `frontend/src/lib/sports-api/hooks/use-odds.ts`:

```ts
"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import type { TraditionalOddsLatest, TraditionalOddsHistory } from "../types";

export function useTraditionalOddsLatest(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-odds/${matchId}/latest` : null;
  return useSWR<TraditionalOddsLatest>(key);
}

export function useTraditionalOddsHistory(matchId: string | null, mappedOutcome?: string) {
  const usp = new URLSearchParams();
  if (mappedOutcome) usp.set("mapped_outcome", mappedOutcome);
  const q = usp.toString() ? `?${usp.toString()}` : "";
  const key = matchId ? `${getApiBase()}/sport-odds/${matchId}/history${q}` : null;
  return useSWR<TraditionalOddsHistory>(key);
}
```

Create `frontend/src/lib/sports-api/hooks/use-recommendations.ts`:

```ts
"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import type { SportRecommendation, RecommendationList } from "../types";

function buildQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join("&");
}

export function useRecommendation(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-recommendations/${matchId}` : null;
  return useSWR<SportRecommendation>(key);
}

export function useOpenDecisions(params?: { limit?: number; decision?: string }) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/sport-recommendations/open${qs}`;
  return useSWR<RecommendationList>(key);
}

export function useTopPicks(params?: { limit?: number; min_abs_edge?: number }) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/sport-recommendations/discrepancies${qs}`;
  return useSWR<RecommendationList>(key);
}
```

Create `frontend/src/lib/sports-api/hooks/use-settlements.ts`:

```ts
"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import type { SettlementList, CalibrationList } from "../types";

function buildQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  if (entries.length === 0) return "";
  const usp = new URLSearchParams();
  for (const [k, v] of entries) usp.set(k, String(v));
  return `?${usp.toString()}`;
}

export function useSettlement(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-settlements/${matchId}` : null;
  return useSWR<SettlementList>(key);
}

export function useSettlementHistory(limit: number = 20, engine?: string) {
  const q = buildQuery({ limit, engine });
  const key = `${getApiBase()}/sport-settlements/history${q}`;
  return useSWR<SettlementList>(key);
}

export function useCalibrations(engine?: string, competition?: string) {
  const q = buildQuery({ engine, competition });
  const key = `${getApiBase()}/sport-settlements/calibrations${q}`;
  return useSWR<CalibrationList>(key);
}
```

Create `frontend/src/lib/sports-api/hooks/use-futures.ts`:

```ts
"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import type {
  AvailableFuturesResponse,
  FuturesLinksResponse,
  FuturesSnapshotsResponse,
} from "../types";

export function useAvailableFutures() {
  const key = `${getApiBase()}/futures`;
  return useSWR<AvailableFuturesResponse>(key);
}

export function useFuturesLinks(competition: string | null, season: string | null) {
  const key = competition && season
    ? `${getApiBase()}/futures/${competition}/${season}`
    : null;
  return useSWR<FuturesLinksResponse>(key);
}

export function useLatestSnapshots(competition: string | null, season: string | null) {
  const key = competition && season
    ? `${getApiBase()}/futures/${competition}/${season}/latest`
    : null;
  return useSWR<FuturesSnapshotsResponse>(key);
}
```

Create `frontend/src/lib/sports-api/hooks/use-optimization.ts`:

```ts
"use client";

import useSWR from "swr";
import { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { sportPost } from "../client";
import type { OptimizedParams } from "../types";

export function useOptimizationParams() {
  const key = `${getApiBase()}/sport-optimization/params`;
  return useSWR<OptimizedParams[]>(key);
}

export async function triggerOptimization(
  sport: string,
  nTrials: number = 150,
): Promise<{ task_id: string }> {
  const result = await sportPost<{ task_id: string }>(
    `/sport-optimization/run`,
    { sport, n_trials: nTrials },
  );
  await mutate(`${getApiBase()}/sport-optimization/params`);
  return result;
}
```

- [ ] **Step 9: Run all hook tests to verify they pass**

Run: `cd frontend; npx vitest run src/lib/sports-api/`
Expected: PASS — all client + hook tests green

- [ ] **Step 10: Create `index.ts` re-export barrel**

Create `frontend/src/lib/sports-api/index.ts`:

```ts
export * from "./types";
export { sportPost } from "./client";
export {
  useMatches,
  useMatchDetail,
  triggerPrediction,
  NotFoundError,
} from "./hooks/use-matches";
export {
  useEngineScores,
  usePredictionHistory,
  usePredictionTrajectory,
  useCalibration,
  useReliability,
} from "./hooks/use-learning";
export {
  useMarketLinks,
  useMarketLinksByMatch,
  useLatestLinks,
  usePendingLinks,
  useMarketSnapshots,
  verifyLink,
} from "./hooks/use-markets";
export {
  useTraditionalOddsLatest,
  useTraditionalOddsHistory,
} from "./hooks/use-odds";
export {
  useRecommendation,
  useOpenDecisions,
  useTopPicks,
} from "./hooks/use-recommendations";
export {
  useSettlement,
  useSettlementHistory,
  useCalibrations,
} from "./hooks/use-settlements";
export {
  useAvailableFutures,
  useFuturesLinks,
  useLatestSnapshots,
} from "./hooks/use-futures";
export {
  useOptimizationParams,
  triggerOptimization,
} from "./hooks/use-optimization";
```

- [ ] **Step 11: Update all consumers to use new SWR hooks**

Search for all files importing from the 8 old client modules:

```
Grep pattern: "@/lib/(sports-api|learning-api|sport-markets-api|sport-odds-api|sport-recommendations-api|sport-settlements-api|futures-api|optimization-api)"
path: frontend/src
```

For each consumer file, update the imports and the data-fetching pattern:

**Pattern: `useEffect` + `useState` + `fetchXxx` → `useXxx` SWR hook**

Example migration for `frontend/src/app/sports/page.tsx`:

Before:
```tsx
import { useEffect, useState } from "react";
import { fetchMatches, type MatchSummary } from "@/lib/sports-api";

const [matches, setMatches] = useState<MatchSummary[]>([]);
const [loading, setLoading] = useState(true);
const [error, setError] = useState<string | null>(null);

useEffect(() => {
  setLoading(true);
  setError(null);
  fetchMatches(sport ?? undefined)
    .then((data) => { setMatches(data); setLoading(false); })
    .catch((err) => { setError(err.message); setLoading(false); });
}, [sport]);
```

After:
```tsx
import { useMatches, type MatchSummary } from "@/lib/sports-api";

const { data: matches = [], error, isLoading: loading } = useMatches(sport ?? undefined);
const errorMessage = error ? (error instanceof Error ? error.message : "加载失败") : null;
```

Apply this pattern to every `app/sports/**` page and `components/sports/**` component that imports from the old clients. The specific consumers to update (search and update each):

1. `frontend/src/app/sports/page.tsx` — `fetchMatches` → `useMatches`, `fetchMatchDetail` → `useMatchDetail`
2. `frontend/src/app/sports/[matchId]/page.tsx` — `fetchMatchDetail` → `useMatchDetail`, `triggerPrediction` (old) → `triggerPrediction` (new, from `@/lib/sports-api`)
3. `frontend/src/app/sports/futures/page.tsx` — `fetchAvailableFutures`, `fetchFuturesLinks`, `fetchLatestSnapshots` → `useAvailableFutures`, `useFuturesLinks`, `useLatestSnapshots`
4. `frontend/src/app/sports/learning/page.tsx` — (delegates to `LearningTabs`)
5. `frontend/src/app/sports/learning/history/[matchId]/page.tsx` — `fetchPredictionTrajectory` → `usePredictionTrajectory`
6. `frontend/src/app/sports/markets/page.tsx` — `fetchMarketLinks`, `fetchPendingLinks`, `fetchMarketSnapshots` → SWR hooks
7. `frontend/src/app/sports/optimization/page.tsx` — `fetchOptimizationParams`, `triggerOptimization` → `useOptimizationParams`, `triggerOptimization`
8. `frontend/src/app/sports/recommendations/page.tsx` — `fetchOpenDecisions`, `fetchTopPicks` → SWR hooks
9. `frontend/src/app/sports/settlements/page.tsx` — `fetchSettlementHistory`, `fetchCalibrations` → SWR hooks
10. All `frontend/src/components/sports/**` that import from old clients — update to SWR hooks or re-exported types from `@/lib/sports-api`

**Important:** Component files that only import **types** (not fetch functions) from old clients should update their import to `@/lib/sports-api` and import the types from there. Components that call fetch functions inside `useEffect` should migrate to the SWR hook pattern shown above.

For mutation calls (`triggerPrediction`, `verifyLink`, `triggerOptimization`), update the import source to `@/lib/sports-api` and keep the call pattern (they're still plain async functions called from event handlers).

- [ ] **Step 12: Delete old client files and `sports-api.test.ts`**

Delete these 9 files:
- `frontend/src/lib/sports-api.ts`
- `frontend/src/lib/sports-api.test.ts`
- `frontend/src/lib/learning-api.ts`
- `frontend/src/lib/sport-markets-api.ts`
- `frontend/src/lib/sport-odds-api.ts`
- `frontend/src/lib/sport-recommendations-api.ts`
- `frontend/src/lib/sport-settlements-api.ts`
- `frontend/src/lib/futures-api.ts`
- `frontend/src/lib/optimization-api.ts`

- [ ] **Step 13: Delete `getWorldCupApiBase()` from `env.ts`**

In `frontend/src/lib/env.ts`, delete the entire `getWorldCupApiBase` function (lines 23-45 in the current file, including the JSDoc comment block above it).

In `frontend/src/lib/env.test.ts`, delete the entire `describe("getWorldCupApiBase", ...)` block (lines 35-59).

- [ ] **Step 14: Run full test suite**

Run: `cd frontend; npx vitest run`
Expected: ALL tests pass. If any test fails:
- Import path issue → fix the import
- Missing type → verify `types.ts` includes it
- Hook key mismatch → verify the key construction matches the test expectation

- [ ] **Step 15: Verify no remaining references to old clients or `getWorldCupApiBase`**

```
Grep pattern: "getWorldCupApiBase"
path: frontend/src
```
Expected: zero matches.

```
Grep pattern: "@/lib/(sports-api|learning-api|sport-markets-api|sport-odds-api|sport-recommendations-api|sport-settlements-api|futures-api|optimization-api)\b"
path: frontend/src
```
Expected: zero matches (the new `@/lib/sports-api` matches `sports-api` but not `sports-api.ts` or `sports-api/` — verify carefully; the regex `\b` boundary after `optimization-api` ensures `sports-api/` is not matched by `sports-api` because the next char is `/` which is a word boundary... actually `/` is NOT a word character, so `\b` matches. To be safe, search for exact old file imports like `from "@/lib/sports-api"` — this should return zero because all consumers now import from `@/lib/sports-api/` subpaths or the barrel).

- [ ] **Step 16: Commit**

```bash
cd frontend
git add -A
git commit -m "refactor(frontend): task 4 - unify sport API clients onto SWR in lib/sports-api/"
```

---

## Task 5: Navigation redesign

**Goal:** Rewrite `components/app-nav.tsx` from a flat 13-item list to a two-group structure (事件情报平台 / Sports Prediction OS), add 4 missing entries (`/quality`, `/sports/futures`, `/sports/optimization`, `/sports/world-cup`), fix duplicate `Target` icons, update tests.

**Files:**
- Modify: `frontend/src/components/app-nav.tsx`
- Modify: `frontend/src/components/app-nav.test.tsx`

**Interfaces:**
- Consumes: Task 2's route migration (`/world-cup` → `/sports/world-cup`)
- Produces: a two-group `AppNav` with 17 total entries

- [ ] **Step 1: Write the failing test for the new nav structure**

Replace `frontend/src/components/app-nav.test.tsx` with:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { AppNav } from "./app-nav";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
}));

vi.mock("@/components/operator-key-control", () => ({
  OperatorKeyControl: () => <button type="button">Operator</button>,
}));

vi.mock("@/components/theme-control", () => ({
  ThemeControl: () => <button type="button">Theme</button>,
}));

vi.mock("@/components/live-status-indicator", () => ({
  LiveStatusIndicator: () => <span>LiveStatus</span>,
}));

describe("AppNav", () => {
  it("renders the brand and hot news ticker above the main navigation", () => {
    render(<AppNav />);

    const ticker = screen.getByRole("region", { name: "示例新闻" });
    const nav = screen.getByRole("navigation", { name: "主导航" });

    expect(within(ticker).getByRole("link", { name: /PROBABILITY/ })).toHaveAttribute(
      "href",
      "/",
    );
    expect(ticker).toHaveTextContent("示例新闻");
    expect(ticker).toHaveTextContent("美联储");
    expect(within(nav).queryByText(/PROBABILITY/)).not.toBeInTheDocument();
    expect(
      ticker.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("renders two group labels", () => {
    render(<AppNav />);

    expect(screen.getByText("事件情报平台")).toBeInTheDocument();
    expect(screen.getByText("Sports Prediction OS")).toBeInTheDocument();
  });

  it("renders all Event Intelligence entries", () => {
    render(<AppNav />);

    for (const label of [
      "监控面板", "决策机会", "Edge 监测", "人工分析",
      "历史复盘", "质量运营", "质量切片", "模拟交易",
    ]) {
      expect(screen.getByRole("link", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it("renders all Sports Prediction OS entries", () => {
    render(<AppNav />);

    for (const label of [
      "体育预测", "期货市场", "学习仪表盘", "体育市场",
      "参数优化", "体育推荐", "体育结算", "世界杯专属",
    ]) {
      expect(screen.getByRole("link", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it("links /quality entry", () => {
    render(<AppNav />);
    const link = screen.getByRole("link", { name: /质量运营/ });
    expect(link).toHaveAttribute("href", "/quality");
  });

  it("links /sports/futures entry", () => {
    render(<AppNav />);
    const link = screen.getByRole("link", { name: /期货市场/ });
    expect(link).toHaveAttribute("href", "/sports/futures");
  });

  it("links /sports/optimization entry", () => {
    render(<AppNav />);
    const link = screen.getByRole("link", { name: /参数优化/ });
    expect(link).toHaveAttribute("href", "/sports/optimization");
  });

  it("links /sports/world-cup entry (migrated from /world-cup)", () => {
    render(<AppNav />);
    const link = screen.getByRole("link", { name: /世界杯专属/ });
    expect(link).toHaveAttribute("href", "/sports/world-cup");
  });

  it("does not link the old /world-cup route", () => {
    render(<AppNav />);
    expect(screen.queryByRole("link", { name: /^世界杯$/ })).not.toBeInTheDocument();
  });

  it("keeps navigation labels on a single line", () => {
    render(<AppNav />);

    for (const label of [
      "监控面板", "决策机会", "Edge 监测", "人工分析",
      "历史复盘", "质量运营", "质量切片", "模拟交易",
      "体育预测", "期货市场", "学习仪表盘", "体育市场",
      "参数优化", "体育推荐", "体育结算", "世界杯专属",
    ]) {
      const link = screen.getByRole("link", { name: new RegExp(label) });
      expect(link).toHaveClass("whitespace-nowrap");
      expect(link).toHaveClass("shrink-0");
    }
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend; npx vitest run src/components/app-nav.test.tsx`
Expected: FAIL — new entries (`质量运营`, `期货市场`, `参数优化`, `世界杯专属`) and group labels not found

- [ ] **Step 3: Rewrite `app-nav.tsx`**

Replace the `NAV` constant and the `<nav>` rendering in `frontend/src/components/app-nav.tsx`. The new structure:

Update the import line (line 5) to add the new icons and remove unused ones:

```tsx
import {
  Activity,
  CircleDollarSign,
  FlaskConical,
  Gauge,
  GraduationCap,
  History,
  Lightbulb,
  LineChart,
  Medal,
  Newspaper,
  Radar,
  Target,
  TrendingUp,
  Trophy,
  Wrench,
  Zap,
} from "lucide-react";
```

Replace the `NAV` constant (lines 11-25) with:

```tsx
type NavItem = {
  href: string;
  label: string;
  icon: typeof Radar;
  match: string[];
};

type NavGroup = {
  label: string;
  items: NavItem[];
};

const NAV_GROUPS: NavGroup[] = [
  {
    label: "事件情报平台",
    items: [
      { href: "/", label: "监控面板", icon: Radar, match: ["/", "/events"] },
      { href: "/decisions", label: "决策机会", icon: Target, match: ["/decisions"] },
      { href: "/edges", label: "Edge 监测", icon: Zap, match: ["/edges"] },
      { href: "/analyze", label: "人工分析", icon: FlaskConical, match: ["/analyze"] },
      { href: "/history", label: "历史复盘", icon: History, match: ["/history"] },
      { href: "/quality", label: "质量运营", icon: Activity, match: ["/quality"] },
      { href: "/quality-metrics", label: "质量切片", icon: Gauge, match: ["/quality-metrics"] },
      { href: "/trades", label: "模拟交易", icon: TrendingUp, match: ["/trades"] },
    ],
  },
  {
    label: "Sports Prediction OS",
    items: [
      { href: "/sports", label: "体育预测", icon: Medal, match: ["/sports"] },
      { href: "/sports/futures", label: "期货市场", icon: Trophy, match: ["/sports/futures"] },
      { href: "/sports/learning", label: "学习仪表盘", icon: GraduationCap, match: ["/sports/learning"] },
      { href: "/sports/markets", label: "体育市场", icon: LineChart, match: ["/sports/markets"] },
      { href: "/sports/optimization", label: "参数优化", icon: Wrench, match: ["/sports/optimization"] },
      { href: "/sports/recommendations", label: "体育推荐", icon: Lightbulb, match: ["/sports/recommendations"] },
      { href: "/sports/settlements", label: "体育结算", icon: CircleDollarSign, match: ["/sports/settlements"] },
      { href: "/sports/world-cup", label: "世界杯专属", icon: Trophy, match: ["/sports/world-cup"] },
    ],
  },
];
```

Replace the `<nav>` rendering (the `{NAV.map((item) => ...)}` block, lines 100-123) with the two-group rendering:

```tsx
<nav aria-label="主导航" className="order-3 -mx-1 flex w-full items-center gap-1 overflow-x-auto px-1 pb-1 md:order-none md:mx-0 md:min-w-0 md:flex-1 md:px-0 md:pb-0">
  {NAV_GROUPS.map((group, groupIndex) => (
    <div key={group.label} className="flex shrink-0 items-center gap-1">
      {groupIndex > 0 && (
        <span className="h-4 w-px shrink-0 bg-border" aria-hidden="true" />
      )}
      <span className="hidden shrink-0 px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground md:inline">
        {group.label}
      </span>
      {group.items.map((item) => {
        const active =
          item.href === "/"
            ? norm === "/" || norm.startsWith("/events")
            : norm.startsWith(item.href);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            prefetch={false}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
            )}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            {item.label}
          </Link>
        );
      })}
    </div>
  ))}
</nav>
```

**Icon assignment rationale (no duplicates):**
- `Target` — used only for `/decisions` (the original icon for this entry)
- `Trophy` — used for both `/sports/futures` and `/sports/world-cup` (both are trophy-related; the spec assigned Trophy to both — this is acceptable because they're in different visual contexts and the original spec explicitly assigned Trophy to `/sports/futures` as "moved from old `/world-cup`")
- `Lightbulb` — `/sports/recommendations` (was `Target`, now fixed)
- `CircleDollarSign` — `/sports/settlements` (was `Target`, now fixed)
- `Wrench` — `/sports/optimization` (new entry)
- `Activity` — `/quality` (new entry; already imported for `BrandLink`)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend; npx vitest run src/components/app-nav.test.tsx`
Expected: PASS — all assertions green

- [ ] **Step 5: Run full test suite**

Run: `cd frontend; npx vitest run`
Expected: ALL tests pass, including `navigation-shell.test.ts` (already updated in Task 2)

- [ ] **Step 6: Commit**

```bash
cd frontend
git add -A
git commit -m "refactor(frontend): task 5 - redesign app-nav into two groups with missing entries"
```

---

## Task 6: Loose components + test coverage

**Goal:** Move 5 loose `components/sports/` top-level components into `components/sports/common/`. Add tests for the most important untested `dashboard/`, `detail/`, and `history/` components.

**Files:**
- Create: `frontend/src/components/sports/common/` (directory)
- Move 5 components (+ test files where they exist) from `components/sports/` to `components/sports/common/`:
  - `factor-breakdown-table.tsx` + `.test.tsx`
  - `match-detail-panel.tsx`
  - `match-list-card.tsx` + `.test.tsx`
  - `probability-bar.tsx` + `.test.tsx`
  - `sport-filter.tsx`
- Modify: all importers of the 5 moved components
- Create: ~5-8 new test files for untested components in `dashboard/`, `detail/`, `history/`

**Interfaces:**
- Consumes: Tasks 1-5 (all prior migrations complete)
- Produces: `@/components/sports/common/*` paths for the 5 moved components

- [ ] **Step 1: Move 5 loose components to `components/sports/common/`**

For each of the 5 components, read the original file and write it to the new location `frontend/src/components/sports/common/<name>.tsx`. The file contents stay identical — these components don't import from `@/components/sports/*` sibling paths (they import from `@/lib/*` and `@/components/ui/*`).

Do the same for their test files:
- `frontend/src/components/sports/factor-breakdown-table.test.tsx` → `frontend/src/components/sports/common/factor-breakdown-table.test.tsx`
- `frontend/src/components/sports/match-list-card.test.tsx` → `frontend/src/components/sports/common/match-list-card.test.tsx`
- `frontend/src/components/sports/probability-bar.test.tsx` → `frontend/src/components/sports/common/probability-bar.test.tsx`

In each test file, update the component import:
- `from "./factor-breakdown-table"` → `from "./factor-breakdown-table"` (stays same, relative path resolves within new dir)
- `from "../match-list-card"` → `from "./match-list-card"` (was one level up, now same dir)

Actually, verify each test's import path. The existing tests at `components/sports/*.test.tsx` import from `./<component>`. After moving both to `common/`, the relative import `./<component>` still works. No change needed.

- [ ] **Step 2: Update all importers of the 5 moved components**

Search for all files importing the 5 components:

```
Grep pattern: "@/components/sports/(factor-breakdown-table|match-detail-panel|match-list-card|probability-bar|sport-filter)"
path: frontend/src
```

Update every match:
- `@/components/sports/factor-breakdown-table` → `@/components/sports/common/factor-breakdown-table`
- `@/components/sports/match-detail-panel` → `@/components/sports/common/match-detail-panel`
- `@/components/sports/match-list-card` → `@/components/sports/common/match-list-card`
- `@/components/sports/probability-bar` → `@/components/sports/common/probability-bar`
- `@/components/sports/sport-filter` → `@/components/sports/common/sport-filter`

Known consumers include:
- `frontend/src/app/sports/page.tsx` — imports `SportFilter`, `MatchListCard`
- `frontend/src/app/sports/[matchId]/page.tsx` — imports `MatchDetailPanel`
- Various `frontend/src/components/sports/**/*.tsx` that use `ProbabilityBar`, `FactorBreakdownTable`

- [ ] **Step 3: Delete the 5 old component files from `components/sports/`**

Delete:
- `frontend/src/components/sports/factor-breakdown-table.tsx`
- `frontend/src/components/sports/factor-breakdown-table.test.tsx`
- `frontend/src/components/sports/match-detail-panel.tsx`
- `frontend/src/components/sports/match-list-card.tsx`
- `frontend/src/components/sports/match-list-card.test.tsx`
- `frontend/src/components/sports/probability-bar.tsx`
- `frontend/src/components/sports/probability-bar.test.tsx`
- `frontend/src/components/sports/sport-filter.tsx`

- [ ] **Step 4: Run tests to verify the move didn't break anything**

Run: `cd frontend; npx vitest run`
Expected: All tests pass

- [ ] **Step 5: Identify the most important untested components**

List all components in `dashboard/`, `detail/`, and `history/` that lack a co-located `.test.tsx` file:

```
Glob pattern: frontend/src/components/dashboard/*.tsx
Glob pattern: frontend/src/components/detail/*.tsx
Glob pattern: frontend/src/components/history/*.tsx
```

For each `.tsx` file, check if a `.test.tsx` sibling exists. Select the 5-8 most important untested components (prioritize those that render data from API calls or have user interactions; skip trivial presentational wrappers).

**Suggested priority list** (verify against the actual directory listing at execution time):
1. Any `dashboard/` component that renders `eventsApi` data
2. Any `detail/` component that renders event detail data
3. Any `history/` component that renders historical snapshots

For each selected component, write a test that verifies:
- It renders without crashing when given valid props
- It renders key content from props (e.g., a title, a value)
- It renders an empty state when given empty/null props

- [ ] **Step 6: Write tests for 5-8 untested components**

For each selected component, read its source to understand the props interface, then create a `.test.tsx` file following this template:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ComponentName } from "./component-name";

// Mock next/link if the component uses <Link>
vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

describe("ComponentName", () => {
  it("renders without crashing", () => {
    render(<ComponentName {...minimalProps} />);
    expect(screen.getByText(/expected text/)).toBeInTheDocument();
  });

  it("renders empty state when data is missing", () => {
    render(<ComponentName {...emptyProps} />);
    expect(screen.getByText(/empty state text/)).toBeInTheDocument();
  });
});
```

Write one test file per selected component. The exact props and assertions depend on the component's interface — read each component's source before writing its test.

**Important:** Do NOT modify the component source files. Only add test files. If a component is hard to test due to tight coupling, skip it and pick another — the goal is to add coverage for the most testable untested components, not to refactor them.

- [ ] **Step 7: Run all new tests**

Run: `cd frontend; npx vitest run`
Expected: ALL tests pass (existing + new)

- [ ] **Step 8: Commit**

```bash
cd frontend
git add -A
git commit -m "refactor(frontend): task 6 - move loose components to common/ and expand test coverage"
```

---

## Self-Review

### 1. Spec coverage

| Spec requirement | Task |
|---|---|
| §1.2.1 Clean up World Cup residue — delete safely-deletable, migrate World Cup features into `sports/world-cup/` | Task 1 (lib), Task 2 (components + route) |
| §1.2.2 Unify all `sport-*-api.ts` onto SWR | Task 4 |
| §1.2.3 Group navigation into two sections; fill 4 missing entries | Task 5 |
| §1.2.4 Organize 5 loose components into `common/` | Task 6 |
| §1.2.5 Unify naming — `sports-api.ts` → `sports-api/` module; `analytics-api.ts` → `world-cup/analytics-api.ts`; env var `NEXT_PUBLIC_API_BASE_URL` → `NEXT_PUBLIC_API_BASE` | Task 1 (analytics-api move), Task 4 (sports-api module + env var unification via `getApiBase()`) |
| §1.2.6 Expand test coverage | Task 4 (client + hooks), Task 5 (nav), Task 6 (dashboard/detail/history) |
| §2.4 `sportFetch<T>()` design | **Corrected by code verification:** `sportFetch<T>()` is NOT needed — the global `swrFetcher` handles GET. Only `sportPost<T>()` is created for mutations. This is documented in "Key Technical Decisions" above. |
| §2.4 Mutation table — `verifyLink(linkId, matchId)` | **Corrected by code verification:** actual signature is `verifyLink(matchId, contractId, verified)`, endpoint POST `/api/sport-markets/links/${matchId}/${contractId}/verify` with body `{ verified }`. Task 4 uses the correct signature. |
| §2.5 `lib/api.ts` cleanup — remove 8 methods + 14 types | Task 3 |
| §2.6 Navigation redesign — two groups, icon fixes | Task 5 |
| §2.7 Environment variable unification | Task 4 (delete `getWorldCupApiBase()`, all clients use `getApiBase()`) |
| §4.2 New tests — client, hooks, nav, dashboard/detail/history | Tasks 4, 5, 6 |
| §5.1 Phased execution — 6 tasks | This plan has 6 tasks |
| §5.3 Constraints | Listed in "Global Constraints" above |
| §6 Success criteria 1-12 | All addressed by Tasks 1-6 |

**Gaps:** None identified. The spec's `sportFetch<T>()` and `verifyLink(linkId, matchId)` are corrected based on code verification — the plan uses the actual signatures.

### 2. Placeholder scan

- No "TBD", "TODO", "implement later" found.
- No "add appropriate error handling" — `sportPost<T>()` includes complete error handling.
- No "similar to Task N" — each task's code is self-contained.
- Task 6 Step 5-6 says "select 5-8 most important untested components" — this is intentional flexibility because the exact untested components must be verified at execution time. The test template is complete; only the component selection is deferred.
- All SWR hook code blocks are complete.
- `sportPost<T>()` code block is complete.
- `app-nav.tsx` new structure is complete.

### 3. Type consistency

| Function | Defined in | Used in |
|---|---|---|
| `sportPost<T>(path, body?)` | Task 4 Step 3 (`client.ts`) | Task 4 hooks: `use-matches.ts`, `use-markets.ts`, `use-optimization.ts` |
| `useMatches(sport?)` | Task 4 Step 8 (`use-matches.ts`) | Task 4 Step 11 (consumer migration) |
| `useMatchDetail(matchId: string \| null)` | Task 4 Step 8 | Task 4 Step 11 |
| `triggerPrediction(matchId)` | Task 4 Step 8 | Task 4 Step 11 |
| `verifyLink(matchId, contractId, verified)` | Task 4 Step 8 (`use-markets.ts`) | Task 4 Step 11 |
| `triggerOptimization(sport, nTrials?)` | Task 4 Step 8 (`use-optimization.ts`) | Task 4 Step 11 |
| `getApiBase()` | `lib/env.ts` (existing) | Tasks 1, 4 |
| `NotFoundError` | Task 4 Step 5 (`types.ts`) | Task 4 `use-matches.ts` re-exports it |
| `NAV_GROUPS` | Task 5 Step 3 | Task 5 test assertions |

All signatures match across definitions and usages. The `useSWR<T>(key)` pattern (no custom fetcher, no per-hook config object) is consistent across all 8 hook files. Mutation functions use the global `mutate()` from `swr` (not `useSWRConfig().mutate`) — this is correct because mutations are plain async functions outside React components, and the global `mutate` from `swr` can invalidate any key in the cache.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-17-frontend-deep-refactor.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Each subagent receives one task's full specification and executes it TDD-style with a green-test gate before commit.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

**Which approach?**
