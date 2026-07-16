# Frontend Deep Refactor Design

> **Status:** Design phase
> **Date:** 2026-07-17
> **Scope:** Pure frontend (zero backend modifications)
> **Product positioning:** Dual-track — Event Intelligence Platform + Sports Prediction OS, each with independent entry points, sharing one navigation shell and infrastructure

## 1. Background

The frontend has accumulated significant technical debt across 13 phases of backend evolution. A pre-refactor investigation surfaced these systemic issues:

### 1.1 Current State (from investigation)

- **20 `page.tsx` files**: 7 event-intelligence routes + 7 sports routes + 1 legacy `/world-cup` route + 5 generic pages
- **90+ components** across `world-cup/` (12), `sports/` (17 in 7 subdirs + 5 loose), `dashboard/` (15), `detail/` (11), `history/` (7), `edges/` (1), `decisions/` (1), top-level (10)
- **13 API clients** with **3 incompatible patterns**:
  - (a) `api.ts` unified `api<T>()` with 15s in-memory cache + inflight dedup + auto auth injection
  - (b) `analytics-api.ts` `analyticsFetch<T>()` wrapper with 60s/180s timeout + auth + `X-Client-Source: world-cup-dashboard`
  - (c) 8 `sport-*-api.ts` files using **bare `fetch`** — no timeout, no auth injection, no error localization, no cache
- **Navigation**: 13 flat items, no grouping; 3 routes missing from nav (`/quality`, `/sports/futures`, `/sports/optimization`); `/sports/[matchId]` is a detail page reached from the list, not a nav entry; duplicate `Target` icon on items 11+12
- **World Cup residue**: 12 components (1 orphan `GroupStandingsTable`), 5 lib files, 2 orphan dashboard components, 8 `worldCup*` methods + 14 `WorldCup*` types inside `api.ts`, 1 route, 1 nav item, 2 test assertions
- **SWR underused**: Only 1 hook (`useWorldCupMatches`) uses SWR despite a global `SWRProvider` with auth injection + error localization
- **64 test files**, no skipped tests; coverage gap in `dashboard/` (8/15 untested), `detail/` (6/11 untested), `history/` (5/7 untested)
- **Env var inconsistency**: `futures-api.ts` and `optimization-api.ts` use `NEXT_PUBLIC_API_BASE_URL` (with `_URL` suffix), all others use `NEXT_PUBLIC_API_BASE`

### 1.2 Goals

1. **Clean up World Cup residue** — delete the safely-deletable, migrate World Cup–specific features into `sports/world-cup/`
2. **Unify all `sport-*-api.ts` onto SWR** — replace 8 bare-fetch clients with SWR hooks reusing the global provider's auth + error handling
3. **Group navigation into two sections** — Event Intelligence / Sports Prediction OS; fill in the 4 missing entries
4. **Organize loose components** — 5 loose `components/sports/` top-level components into `common/`
5. **Unify naming** — `sports-api.ts` → `sports-api/` module; `analytics-api.ts` → `world-cup/analytics-api.ts`; env var `NEXT_PUBLIC_API_BASE_URL` → `NEXT_PUBLIC_API_BASE`
6. **Expand test coverage** — add tests for `dashboard/`, `detail/`, `history/` untested components; extend `navigation-shell.test.ts` to cover `sports/*` routes

### 1.3 Non-Goals

- Backend changes (zero modifications to `backend/`)
- Redesigning the event-intelligence dashboard UI
- Replacing recharts or the chart-lite wrapper
- Changing the Tailwind v4 design token system
- Adding new product features
- Migrating event-intelligence `api.ts` to SWR (out of scope — it has its own cache layer that works)

## 2. Architecture

### 2.1 Dual-Track with Shared Infrastructure

```
┌──────────────────────────────────────────────────────────────┐
│  app/layout.tsx  (SWRProvider + AppNav + ScrollToTop + theme)│
├──────────────────────────────────────────────────────────────┤
│  AppNav: two groups                                          │
│  ┌─ 事件情报平台 ─┐  ┌─ Sports Prediction OS ────────────┐   │
│  │ 监控面板       │  │ 体育预测  学习仪表盘  体育市场     │   │
│  │ 决策机会       │  │ 体育推荐  体育结算  期货市场       │   │
│  │ Edge 监测      │  │ 参数优化  世界杯专属               │   │
│  │ 人工分析       │  └────────────────────────────────────┘   │
│  │ 历史复盘       │                                            │
│  │ 质量切片       │                                            │
│  │ 质量运营       │                                            │
│  │ 模拟交易       │                                            │
│  └───────────────┘                                            │
├──────────────────────────────────────────────────────────────┤
│  Shared: lib/utils.ts (cn), lib/format.ts, lib/env.ts,       │
│           lib/api.ts (operator key + buildApiErrorMessage),  │
│           components/ui/, components/providers/, globals.css │
├──────────────────────────────────────────────────────────────┤
│  Event Intelligence        │  Sports Prediction OS            │
│  ─────────────────         │  ──────────────────────────      │
│  components/dashboard/     │  components/sports/common/       │
│  components/detail/        │  components/sports/futures/      │
│  components/history/       │  components/sports/learning/     │
│  components/edges/         │  components/sports/markets/      │
│  components/decisions/     │  components/sports/optimization/ │
│  lib/api.ts (eventsApi)    │  components/sports/realtime/     │
│  lib/adapt.ts              │  components/sports/recommendations│
│  lib/dashboard-cache.ts    │  components/sports/settlements/  │
│  lib/csv.ts                │  components/sports/world-cup/    │
│                            │  lib/sports-api/ (SWR hooks)     │
│                            │  lib/world-cup/                  │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Route Structure (no Next.js route groups — keep flat for static export compatibility)

Next.js route groups like `(intelligence)/` do NOT affect URL paths but add complexity to static export config. The project uses `output: "export"` with `trailingSlash: true`. To stay safe, routes remain flat at their current paths. Grouping happens only in the **navigation UI**, not in the file system.

**Routes after refactor:**

| Path | Source | Group |
|------|--------|-------|
| `/` | `app/page.tsx` | Event Intelligence (monitor) |
| `/decisions` | `app/decisions/page.tsx` | Event Intelligence |
| `/edges` | `app/edges/page.tsx` | Event Intelligence |
| `/analyze` | `app/analyze/page.tsx` | Event Intelligence |
| `/history` | `app/history/page.tsx` | Event Intelligence |
| `/quality` | `app/quality/page.tsx` | Event Intelligence (added to nav) |
| `/quality-metrics` | `app/quality-metrics/page.tsx` | Event Intelligence |
| `/trades` | `app/trades/page.tsx` | Event Intelligence |
| `/events` | `app/events/page.tsx` | Event Intelligence (detail page) |
| `/sports` | `app/sports/page.tsx` | Sports OS |
| `/sports/[matchId]` | `app/sports/[matchId]/page.tsx` | Sports OS (linked from list) |
| `/sports/futures` | `app/sports/futures/page.tsx` | Sports OS (added to nav) |
| `/sports/learning` | `app/sports/learning/page.tsx` | Sports OS |
| `/sports/learning/history/[matchId]` | `app/sports/learning/history/[matchId]/page.tsx` | Sports OS (linked from learning) |
| `/sports/markets` | `app/sports/markets/page.tsx` | Sports OS |
| `/sports/optimization` | `app/sports/optimization/page.tsx` | Sports OS (added to nav) |
| `/sports/recommendations` | `app/sports/recommendations/page.tsx` | Sports OS |
| `/sports/settlements` | `app/sports/settlements/page.tsx` | Sports OS |
| `/sports/world-cup` | `app/sports/world-cup/page.tsx` (migrated from `app/world-cup/`) | Sports OS |

**Removed:** `/world-cup` (migrated to `/sports/world-cup`)

### 2.3 Component Directory Structure

```
components/
├── app-nav.tsx                    # MODIFIED — two-group nav
├── sports/
│   ├── common/                    # NEW — 5 loose components moved here
│   │   ├── factor-breakdown-table.tsx
│   │   ├── match-detail-panel.tsx
│   │   ├── match-list-card.tsx
│   │   ├── probability-bar.tsx
│   │   └── sport-filter.tsx
│   ├── futures/                   # unchanged
│   ├── learning/                  # unchanged
│   ├── markets/                   # unchanged
│   ├── optimization/              # unchanged
│   ├── realtime/                  # unchanged
│   ├── recommendations/           # unchanged
│   ├── settlements/               # unchanged
│   └── world-cup/                 # NEW — migrated from components/world-cup/
│       ├── analytics-dashboard.tsx
│       ├── engine-comparison-card.tsx        # kept (used by match-prediction-card)
│       ├── engine-comparison-view.tsx
│       ├── knockout-view.tsx
│       ├── match-prediction-card.tsx         # kept (used by /sports/world-cup page)
│       ├── prediction-analysis-card.tsx       # kept (used by match-prediction-card)
│       ├── prediction-history-card.tsx        # kept (used by match-prediction-card)
│       ├── qualification-table.tsx
│       └── tournament-simulation.tsx
├── dashboard/                     # 2 orphans deleted
├── detail/                        # unchanged
├── history/                       # unchanged
├── edges/                         # unchanged
├── decisions/                     # unchanged
└── ... (top-level unchanged)
```

**Deleted components** (safely deletable — superseded by `sports/` equivalents or orphan):
- `components/world-cup/batch-engine-switcher.tsx` + `.test.tsx` — superseded by `sports/optimization/OptimizationDashboard`
- `components/world-cup/engine-auto-tune-dashboard.tsx` + `.test.tsx` — superseded by `sports/optimization/OptimizationDashboard`
- `components/world-cup/group-standings-table.tsx` — orphan (zero references)

**Migrated components** (moved to `components/sports/world-cup/`):
- `analytics-dashboard.tsx` + `.test.tsx`
- `engine-comparison-card.tsx` + `.test.tsx`
- `engine-comparison-view.tsx` + `.test.tsx`
- `knockout-view.tsx`
- `match-prediction-card.tsx` + `.test.tsx` + `.rule-label.test.tsx`
- `prediction-analysis-card.tsx`
- `prediction-history-card.tsx` + `.test.tsx`
- `qualification-table.tsx` + `.test.tsx`
- `tournament-simulation.tsx` + `.test.tsx`

**Deleted from `components/dashboard/`** (orphans — only referenced in own `.test.tsx`):
- `world-cup-data-sources.tsx` + `.test.tsx`
- `world-cup-resolution-panel.tsx` + `.test.tsx`

### 2.4 API Client Unification — SWR Migration

**New module: `lib/sports-api/`**

```
lib/sports-api/
├── index.ts                  # re-exports all hooks + types
├── client.ts                 # sportFetch<T>() — shared fetch wrapper with timeout + auth + error localization
├── hooks/
│   ├── use-matches.ts        # useMatches(), useMatchDetail(matchId), useTriggerPrediction()
│   ├── use-learning.ts       # useEngineScores(), usePredictionHistory(), usePredictionTrajectory(), useCalibration(), useReliability()
│   ├── use-markets.ts        # useMarketLinks(), useMarketLinksByMatch(), usePendingLinks(), useMarketSnapshots(), useVerifyLink()
│   ├── use-odds.ts           # useTraditionalOddsLatest(), useTraditionalOddsHistory()
│   ├── use-recommendations.ts# useRecommendation(), useOpenDecisions(), useTopPicks()
│   ├── use-settlements.ts    # useSettlement(), useSettlementHistory(), useCalibrations()
│   ├── use-futures.ts        # useAvailableFutures(), useFuturesLinks(), useLatestSnapshots()
│   └── use-optimization.ts   # useOptimizationParams(), useTriggerOptimization()
└── types.ts                  # shared types (moved from individual files)
```

**`sportFetch<T>()` design:**
- Reuses `getApiBase()` from `lib/env.ts` (NOT `getWorldCupApiBase()` — we standardize on `getApiBase()`)
- 30s `AbortController` timeout (matching `swrFetcher` in `swr-provider.tsx`)
- Injects `X-API-Key` / `X-Operator` from `sessionStorage` (reusing `lib/api.ts` helpers)
- Localizes errors via `buildApiErrorMessage` (reusing `lib/api.ts`)
- Returns typed `Promise<T>`; throws on non-2xx

**SWR hook pattern:**
```ts
// Example: use-matches.ts
export function useMatches(opts?: { competition?: string; limit?: number }) {
  const params = new URLSearchParams();
  if (opts?.competition) params.set("competition", opts.competition);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const key = `/api/predictions/matches?${params}`;
  return useSWR<MatchSummary[]>(key, sportFetch, {
    revalidateOnFocus: false,
    dedupingInterval: 30_000,
  });
}
```

**Mutations** (POST endpoints — exposed as plain async functions, NOT SWR, because mutations don't cache):

| Function | Endpoint | Invalidates |
|----------|----------|-------------|
| `triggerPrediction(matchId)` | POST `/api/predictions/matches/${matchId}/predict` | `useSWRConfig().mutate(`/api/predictions/matches/${matchId}`)` |
| `verifyLink(linkId, matchId)` | POST `/api/sport-markets/links/${linkId}/verify` | `mutate(`/api/sport-markets/links/pending`)` |
| `triggerOptimization(opts)` | POST `/api/sport-optimization/run` | `mutate(`/api/sport-optimization/params`)` |

Each mutation function calls `mutate(key, undefined, { revalidate: true })` from `useSWRConfig` to invalidate relevant cache keys after success.

**Deleted clients** (replaced by `lib/sports-api/`):
- `lib/sports-api.ts` (old single-file version)
- `lib/learning-api.ts`
- `lib/sport-markets-api.ts`
- `lib/sport-odds-api.ts`
- `lib/sport-recommendations-api.ts`
- `lib/sport-settlements-api.ts`
- `lib/futures-api.ts`
- `lib/optimization-api.ts`

### 2.5 World Cup Lib Reorganization

**New: `lib/world-cup/`**

```
lib/world-cup/
├── predictions-api.ts         # migrated from lib/world-cup-predictions.ts
├── analytics-api.ts           # migrated from lib/analytics-api.ts
├── time.ts                    # migrated from lib/world-cup-time.ts
├── group-standings.ts         # migrated from lib/group-standings.ts
├── qualification-probability.ts # migrated from lib/qualification-probability.ts
├── team-names-zh.ts           # migrated from lib/team-names-zh.ts
└── swr-hooks.ts               # migrated from lib/swr-hooks.ts (useWorldCupMatches)
```

**`lib/api.ts` cleanup:**
- Remove 8 `worldCup*` methods from `eventsApi` object: `worldCupDataSourcesStatus`, `worldCupDataSourcePreview`, `worldCupDataSourceImport`, `worldCupResolveDryRun`, `worldCupApiFootballTest`, `worldCupApiFootballValidate`, `worldCupSportmonksTest`, `worldCupSportmonksValidate`
- Remove 14 `WorldCup*` types: `WorldCupSourceFetch`, `WorldCupSkippedSource`, `WorldCupCallBudget`, `WorldCupRunSummary`, `WorldCupFileConfig`, `WorldCupUrlConfig`, `WorldCupFeedConfig`, `WorldCupDataSourceStatus`, `WorldCupDataSourceActionMode`, `WorldCupDataSourceActionResult`, `WorldCupResolveMatch`, `WorldCupResolveResult`, `WorldCupApiFootballConnectionResult`, `WorldCupSportmonksConnectionResult`, `WorldCupPipelineValidateResult`
- These types/methods move to `lib/world-cup/predictions-api.ts`

### 2.6 Navigation Redesign

**New `AppNav` structure:**

```ts
type NavGroup = {
  label: string;
  items: NavItem[];
};

type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  match: string[];
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
      { href: "/quality", label: "质量运营", icon: Activity, match: ["/quality"] },   // NEW entry
      { href: "/quality-metrics", label: "质量切片", icon: Gauge, match: ["/quality-metrics"] },
      { href: "/trades", label: "模拟交易", icon: TrendingUp, match: ["/trades"] },
    ],
  },
  {
    label: "Sports Prediction OS",
    items: [
      { href: "/sports", label: "体育预测", icon: Medal, match: ["/sports"] },
      { href: "/sports/futures", label: "期货市场", icon: Trophy, match: ["/sports/futures"] },    // NEW entry
      { href: "/sports/learning", label: "学习仪表盘", icon: GraduationCap, match: ["/sports/learning"] },
      { href: "/sports/markets", label: "体育市场", icon: LineChart, match: ["/sports/markets"] },
      { href: "/sports/optimization", label: "参数优化", icon: Wrench, match: ["/sports/optimization"] },  // NEW entry
      { href: "/sports/recommendations", label: "体育推荐", icon: Lightbulb, match: ["/sports/recommendations"] },  // icon fix (was duplicate Target)
      { href: "/sports/settlements", label: "体育结算", icon: CircleDollarSign, match: ["/sports/settlements"] },  // icon fix (was duplicate Target)
      { href: "/sports/world-cup", label: "世界杯专属", icon: Trophy, match: ["/sports/world-cup"] },  // migrated from /world-cup
    ],
  },
];
```

**Visual design:** Two groups separated by a thin divider; group label in `text-xs font-semibold uppercase tracking-wider text-muted-foreground` above each group's items. Collapsed on mobile (hamburger menu).

**Icon fixes:**
- `/sports/recommendations`: `Target` → `Lightbulb`
- `/sports/settlements`: `Target` → `CircleDollarSign`
- `/sports/optimization`: new entry uses `Wrench`
- `/sports/futures`: new entry uses `Trophy` (moved from old `/world-cup`)
- `/quality`: new entry uses `Activity` (already imported)

### 2.7 Environment Variable Unification

- `futures-api.ts` and `optimization-api.ts` currently read `process.env.NEXT_PUBLIC_API_BASE_URL`
- All other clients read `process.env.NEXT_PUBLIC_API_BASE` via `getApiBase()` / `getWorldCupApiBase()`
- **Fix:** The new `lib/sports-api/client.ts` uses `getApiBase()` exclusively; `NEXT_PUBLIC_API_BASE_URL` is no longer read anywhere
- **`getWorldCupApiBase()` in `lib/env.ts`:** Deleted in Task 4 (after all sports clients migrate to `getApiBase()`). World Cup lib files (`lib/world-cup/*`) also migrate from `getWorldCupApiBase()` to `getApiBase()` in Task 1, so by Task 4 no caller remains.
- **`.env.example` (frontend):** Document `NEXT_PUBLIC_API_BASE` as the single source of truth

## 3. File Changes Summary

### 3.1 Created

| File | Purpose |
|------|---------|
| `lib/sports-api/index.ts` | Re-exports all hooks + types |
| `lib/sports-api/client.ts` | `sportFetch<T>()` shared wrapper |
| `lib/sports-api/hooks/use-matches.ts` | SWR hooks for matches list/detail/predict |
| `lib/sports-api/hooks/use-learning.ts` | SWR hooks for learning dashboard |
| `lib/sports-api/hooks/use-markets.ts` | SWR hooks for market bridge |
| `lib/sports-api/hooks/use-odds.ts` | SWR hooks for traditional odds |
| `lib/sports-api/hooks/use-recommendations.ts` | SWR hooks for recommendations |
| `lib/sports-api/hooks/use-settlements.ts` | SWR hooks for settlements |
| `lib/sports-api/hooks/use-futures.ts` | SWR hooks for futures markets |
| `lib/sports-api/hooks/use-optimization.ts` | SWR hooks for parameter optimization |
| `lib/sports-api/types.ts` | Shared types consolidated from 8 old clients |
| `lib/world-cup/predictions-api.ts` | Migrated from `lib/world-cup-predictions.ts` |
| `lib/world-cup/analytics-api.ts` | Migrated from `lib/analytics-api.ts` |
| `lib/world-cup/time.ts` | Migrated from `lib/world-cup-time.ts` |
| `lib/world-cup/group-standings.ts` | Migrated from `lib/group-standings.ts` |
| `lib/world-cup/qualification-probability.ts` | Migrated from `lib/qualification-probability.ts` |
| `lib/world-cup/team-names-zh.ts` | Migrated from `lib/team-names-zh.ts` |
| `lib/world-cup/swr-hooks.ts` | Migrated from `lib/swr-hooks.ts` |
| `components/sports/common/` (dir) | 5 loose components moved here |
| `components/sports/world-cup/` (dir) | 9 World Cup–specific components migrated here |

### 3.2 Modified

| File | Change |
|------|--------|
| `components/app-nav.tsx` | Rewrite from flat 13 items to two-group structure; add 4 missing entries; fix duplicate icons |
| `components/app-nav.test.tsx` | Update label assertions for new nav structure |
| `app/navigation-shell.test.ts` | Add `sports/*` routes to the "no AppNav" list; remove `world-cup/page.tsx` (migrated) |
| `app/world-cup/page.tsx` → `app/sports/world-cup/page.tsx` | Migrate route; update imports to new `components/sports/world-cup/` paths |
| `app/sports/page.tsx` | Update imports from `lib/sports-api.ts` to `lib/sports-api/` SWR hooks |
| `app/sports/[matchId]/page.tsx` | Update imports to SWR hooks |
| `app/sports/futures/page.tsx` | Update imports to SWR hooks |
| `app/sports/learning/page.tsx` | Update imports to SWR hooks |
| `app/sports/learning/history/[matchId]/page.tsx` | Update imports to SWR hooks |
| `app/sports/markets/page.tsx` | Update imports to SWR hooks |
| `app/sports/optimization/page.tsx` | Update imports to SWR hooks |
| `app/sports/recommendations/page.tsx` | Update imports to SWR hooks |
| `app/sports/settlements/page.tsx` | Update imports to SWR hooks |
| `lib/api.ts` | Remove 8 `worldCup*` methods + 14 `WorldCup*` types |
| `lib/env.ts` | `getWorldCupApiBase()` marked deprecated (kept for backward compat during migration, removed after) |
| All `components/sports/**` that import from old API clients | Update imports to `lib/sports-api/` |
| All `components/sports/world-cup/**` | Update imports from `lib/world-cup-*` to `lib/world-cup/` |

### 3.3 Deleted

| File | Reason |
|------|--------|
| `lib/sports-api.ts` | Replaced by `lib/sports-api/` module |
| `lib/learning-api.ts` | Replaced by `lib/sports-api/hooks/use-learning.ts` |
| `lib/sport-markets-api.ts` | Replaced by `lib/sports-api/hooks/use-markets.ts` |
| `lib/sport-odds-api.ts` | Replaced by `lib/sports-api/hooks/use-odds.ts` |
| `lib/sport-recommendations-api.ts` | Replaced by `lib/sports-api/hooks/use-recommendations.ts` |
| `lib/sport-settlements-api.ts` | Replaced by `lib/sports-api/hooks/use-settlements.ts` |
| `lib/futures-api.ts` | Replaced by `lib/sports-api/hooks/use-futures.ts` |
| `lib/optimization-api.ts` | Replaced by `lib/sports-api/hooks/use-optimization.ts` |
| `lib/world-cup-predictions.ts` | Migrated to `lib/world-cup/predictions-api.ts` |
| `lib/analytics-api.ts` | Migrated to `lib/world-cup/analytics-api.ts` |
| `lib/world-cup-time.ts` | Migrated to `lib/world-cup/time.ts` |
| `lib/group-standings.ts` | Migrated to `lib/world-cup/group-standings.ts` |
| `lib/qualification-probability.ts` | Migrated to `lib/world-cup/qualification-probability.ts` |
| `lib/team-names-zh.ts` | Migrated to `lib/world-cup/team-names-zh.ts` |
| `lib/swr-hooks.ts` | Migrated to `lib/world-cup/swr-hooks.ts` |
| `components/world-cup/batch-engine-switcher.tsx` + `.test.tsx` | Superseded by `sports/optimization/OptimizationDashboard` |
| `components/world-cup/engine-auto-tune-dashboard.tsx` + `.test.tsx` | Superseded by `sports/optimization/OptimizationDashboard` |
| `components/world-cup/group-standings-table.tsx` | Orphan (zero references) |
| `components/dashboard/world-cup-data-sources.tsx` + `.test.tsx` | Orphan (only in own test) |
| `components/dashboard/world-cup-resolution-panel.tsx` + `.test.tsx` | Orphan (only in own test) |
| `app/world-cup/` (entire dir) | Migrated to `app/sports/world-cup/` |

## 4. Testing Strategy

### 4.1 Zero Regression

All 64 existing test files must pass unmodified (after import path updates). The refactoring is structural — no component behavior changes.

### 4.2 New Tests

| Area | New test files | Coverage |
|------|---------------|----------|
| `lib/sports-api/client.ts` | `lib/sports-api/client.test.ts` | `sportFetch<T>()` timeout, auth injection, error localization |
| `lib/sports-api/hooks/*.ts` | One test per hook file (8 files) | Each hook's key generation, fetch behavior, error handling |
| `components/app-nav.tsx` | Update existing `app-nav.test.tsx` | Two-group structure, new entries, icon uniqueness |
| `app/navigation-shell.test.ts` | Update existing | `sports/*` routes added to "no AppNav" list |
| `components/dashboard/` untested (8) | One test per component (8 files) | Basic render + prop contract |
| `components/detail/` untested (6) | One test per component (6 files) | Basic render + prop contract |
| `components/history/` untested (5) | One test per component (5 files) | Basic render + prop contract |

**Total new test files:** ~30 (1 client + 8 hooks + 8 dashboard + 6 detail + 5 history + 2 updated)

### 4.3 Test Pattern

Follow existing patterns:
- `vitest` + `@testing-library/react` + `jsdom`
- Mock `next/link` in components using `<Link>` (per `trades/page.test.tsx` pattern)
- Mock `swr` with `vi.mock("swr", ...)` for hook tests
- Use `full string matching` for empty state tests (per Phase 6 constraint)

## 5. Migration Safety

### 5.1 Phased Execution

The refactor executes in 6 phases (tasks), each independently committable and revertible:

1. **Task 1: World Cup lib migration** — create `lib/world-cup/`, move 7 files, update imports, delete old files
2. **Task 2: World Cup component migration** — create `components/sports/world-cup/`, move 9 components, delete 3, migrate route, delete `components/world-cup/`
3. **Task 3: `lib/api.ts` cleanup** — remove 8 `worldCup*` methods + 14 types
4. **Task 4: `lib/sports-api/` SWR module** — create client + 8 hook files + types; update all `app/sports/**` page imports; delete 8 old client files
5. **Task 5: Navigation redesign** — rewrite `app-nav.tsx` to two groups; update tests; add 4 missing entries; fix icons
6. **Task 6: Loose components + test coverage** — move 5 loose components to `common/`; add tests for untested dashboard/detail/history components

### 5.2 Rollback

Each task is a separate commit. If any task breaks tests, `git revert` that commit without affecting others.

### 5.3 Constraints

- **Zero backend modifications** — this is a pure frontend refactor
- **All 64 existing tests must pass** (after import path updates) — zero regressions
- **Static export compatibility** — `next.config.ts` `output: "export"` + `trailingSlash: true` must remain functional; no Next.js route groups
- **No new dependencies** — use existing `swr`, `react`, `lucide-react`, `vitest`, `@testing-library/react`
- **TDD where applicable** — new `lib/sports-api/` hooks and `sportFetch<T>()` follow RED → GREEN
- **Import paths use `@/` alias** — consistent with existing codebase
- **Component behavior unchanged** — only imports, file locations, and API client patterns change
- **Subagent-driven task execution** — each task dispatched to a fresh subagent with TDD + inter-task review

## 6. Success Criteria

1. `/world-cup` route removed; `/sports/world-cup` route functional with all World Cup–specific features intact
2. `components/world-cup/` directory deleted; 9 components migrated to `components/sports/world-cup/`; 3 deleted; 2 dashboard orphans deleted
3. `lib/api.ts` no longer contains any `worldCup*` method or `WorldCup*` type
4. `lib/sports-api/` module created with `sportFetch<T>()` + 8 hook files; all `app/sports/**` pages use SWR hooks
5. 8 old `sport-*-api.ts` / `learning-api.ts` / `futures-api.ts` / `optimization-api.ts` files deleted
6. `lib/world-cup/` created with 7 migrated files
7. `app-nav.tsx` displays two groups (事件情报平台 / Sports Prediction OS) with 4 new entries and no duplicate icons
8. `components/sports/common/` created with 5 migrated loose components
9. All 64 existing tests pass (after import updates) + ~30 new test files pass
10. `NEXT_PUBLIC_API_BASE_URL` no longer referenced anywhere; `NEXT_PUBLIC_API_BASE` is the single source of truth
11. Zero backend file modifications
12. `next build` (static export) succeeds

## 7. Risk Assessment

| Risk | Mitigation |
|------|-----------|
| SWR migration changes fetch behavior | `sportFetch<T>()` mirrors existing `swrFetcher` timeout + auth; SWR config matches `SWRProvider` defaults |
| Import path breaks cascade | Each task updates all imports in one commit; tests run before commit |
| Static export route break | No Next.js route groups; routes remain at flat paths; only `/world-cup` → `/sports/world-cup` is a real path change |
| World Cup component internal imports | Task 2 updates all intra-`world-cup/` imports to new `components/sports/world-cup/` paths |
| `api.ts` cleanup breaks event-intelligence tests | Task 3 only removes methods/types with zero event-intelligence references (verified by grep) |
| Test count regression | Each task commits only after `vitest run` passes |

## 8. Out of Scope (Future Work)

- Migrating event-intelligence `api.ts` to SWR (it has its own working cache layer)
- Redesigning event-intelligence dashboard UI
- Adding new product features
- Backend changes
- Replacing recharts or the chart-lite wrapper
- Changing the Tailwind v4 design token system
- Mobile-responsive redesign (current nav has mobile hamburger but groups need testing)
