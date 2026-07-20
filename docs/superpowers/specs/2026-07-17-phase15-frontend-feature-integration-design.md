# Phase 15: Frontend Feature Integration

**Date**: 2026-07-17  
**Status**: Draft  
**Predecessor**: Phase 14 (Frontend Deep Refactor)  

## 1. Problem Statement

Phase 7-12 added significant backend capabilities — Edge Detector, Calibration Fusion, WebSocket real-time price push, Bayesian optimization, Kalshi integration, futures markets — but the frontend has not kept pace. The current frontend pages show only a fraction of the available data:

- **Edge Detector** (`/sport-edges/*`, 3 endpoints): Zero frontend integration. The core "Reality Filter" output (`adjusted_edge`, `trust`, `liquidity_factor`, multi-source comparison) is completely invisible.
- **WebSocket real-time prices** (`/ws/matches/{id}/prices`): Infrastructure built but `usePriceStream()`'s `updates[]` is discarded — only `isConnected` is used for a LIVE/OFFLINE badge.
- **Optimization Dashboard** (`/sport-optimization/*`, 3 POST + 3 GET): Only static parameter table. No "run optimization" button, no task progress, no "apply params" button. `triggerOptimization` is defined but never called.
- **Settlements** (`POST /sport-settlements/process/{match_id}`): Endpoint ready but no UI trigger.
- **`/sports/[matchId]`**: Only 2 tabs (details + odds). Missing Edge analysis, real-time prices, and multi-source comparison.

## 2. Scope

### In Scope
1. Edge Detector frontend: new `/sports/edges` page + edge hooks + edge components + Edge tab in match detail
2. Real-time price display: new `RealtimePriceTable` component + "实时价格" tab in match detail
3. Optimization dashboard enhancement: run/ingest/apply/status buttons + polling
4. Match detail enhancement: 2 tabs → 4 tabs
5. Settlements manual trigger: "重算" button per row
6. Navigation: add "Edge 偏离" entry to Sports Prediction OS group

### Out of Scope
- Phase 9 Backtesting frontend (CLI-only, no API endpoints for results)
- Phase 8 Calibration Fusion standalone visualization (output is baked into edge `trust` field — visible via Edge tab)
- Phase 11 Kalshi dedicated UI (Kalshi data flows through existing sport-markets links)
- Event-intelligence dashboard redesign
- Backend changes
- Mobile-responsive redesign

## 3. Architecture

### 3.1 New Files

```
frontend/src/
├── lib/sports-api/hooks/
│   └── use-edges.ts                          # NEW: 3 edge hooks
├── components/sports/edges/
│   ├── EdgeDiscrepanciesTable.tsx            # NEW: global edge leaderboard
│   ├── EdgeTimelineChart.tsx                 # NEW: recharts edge history
│   └── EdgeDetailPanel.tsx                   # NEW: single-match edge detail
├── components/sports/realtime/
│   └── RealtimePriceTable.tsx                # NEW: live price feed table
├── app/sports/edges/
│   └── page.tsx                              # NEW: /sports/edges route
```

### 3.2 Modified Files

```
frontend/src/
├── lib/sports-api/hooks/use-optimization.ts   # +triggerIngest, +useTaskStatus, +applyParams
├── lib/sports-api/hooks/use-settlements.ts    # +processSettlement
├── lib/sports-api/index.ts                    # +re-export edge hooks + new functions
├── components/app-nav.tsx                     # +Edge 偏离 nav entry
├── components/sports/optimization/
│   └── OptimizationDashboard.tsx             # +run/ingest/apply/status UI
├── components/sports/realtime/
│   └── RealtimePriceIndicator.tsx            # inline style → Tailwind
├── app/sports/[matchId]/page.tsx              # 2 tabs → 4 tabs
├── app/sports/settlements/page.tsx            # +重算 button
└── app/navigation-shell.test.ts               # +sports/edges/page.tsx
```

## 4. Component Design

### 4.1 Edge Detector Hooks (`use-edges.ts`)

```typescript
export interface EdgeSource {
  link_id: number;
  source: string;
  contract_id: string;
  implied_prob: number;
  liquidity: number | null;
  volume: number | null;
  weight: number;
  link_confidence: number;
}

export interface EdgeResult {
  mapped_outcome: string;
  model_prob: number;
  market_prob: number;
  raw_edge: number;
  trust: number;
  liquidity_factor: number;
  adjusted_edge: number;
  spread: number | null;
  sources_count: number;
  stale: boolean;
  captured_at: string;
  sources: EdgeSource[];
}

export interface EdgeLatestResponse {
  match_id: string;
  outcomes: EdgeResult[];
  engine_name: string | null;
  competition: string | null;
  prediction_timestamp: string | null;
  skipped: boolean;
  skip_reason: string | null;
}

export interface EdgeHistoryPoint {
  captured_at: string;
  model_prob: number;
  market_prob: number;
  raw_edge: number;
  adjusted_edge: number;
  stale: boolean;
}

export interface EdgeHistoryResponse {
  match_id: string;
  series: {
    mapped_outcome: string;
    snapshots: EdgeHistoryPoint[];
  }[];
}

export interface EdgeDiscrepancyItem {
  match_id: string;
  mapped_outcome: string;
  model_prob: number;
  market_prob: number;
  raw_edge: number;
  adjusted_edge: number;
  stale: boolean;
  captured_at: string;
}

export interface EdgeDiscrepanciesResponse {
  items: EdgeDiscrepancyItem[];
  total: number;
}

export function useEdgeLatest(matchId: string | null)
export function useEdgeHistory(matchId: string | null, mappedOutcome?: string)
export function useEdgeDiscrepancies(params?: { limit?: number; min_abs_edge?: number })
```

### 4.2 Edge Components

**EdgeDiscrepanciesTable** — Global edge leaderboard table.
- Props: `{ params?: { limit?: number; min_abs_edge?: number } }`
- Uses `useEdgeDiscrepancies(params)`
- Columns: Match ID, Outcome, Model Prob, Market Prob, Raw Edge, Adjusted Edge, Stale, Captured At
- Row click → navigate to `/sports/[matchId]?tab=edge`
- Loading/error/empty states

**EdgeTimelineChart** — Recharts LineChart showing edge history over time.
- Props: `{ matchId: string; mappedOutcome?: string }`
- Uses `useEdgeHistory(matchId, mappedOutcome)`
- 3 lines: model_prob, market_prob, adjusted_edge (Y axis 0-1)
- X axis: captured_at timestamps
- Recharts mock in tests

**EdgeDetailPanel** — Single-match edge detail.
- Props: `{ matchId: string }`
- Uses `useEdgeLatest(matchId)`
- Shows per-outcome: model_prob vs market_prob bar, adjusted_edge, trust, liquidity_factor, sources breakdown
- Shows skip reason if skipped

### 4.3 RealtimePriceTable

- Props: `{ matchId: string }`
- Uses `usePriceStream(matchId)`
- Renders `RealtimePriceIndicator` (connection badge) + scrollable table
- Table columns: Time, Type, Outcome, Implied Prob, Price, Decimal Odds, Bookmaker
- `updates[]` displayed in reverse chronological order (newest first)
- Empty state: "等待实时数据..." when connected but no updates
- Color-coded implied_prob delta (green if moved toward model_prob)

### 4.4 OptimizationDashboard Enhancement

**New hooks in `use-optimization.ts`:**
```typescript
export async function triggerIngest(sport: string, seasons: string[]): Promise<Record<string, unknown>>
export function useTaskStatus(taskId: string | null)  // polls GET /status/{task_id} every 2s
export async function applyParams(paramsId: number): Promise<unknown>
```

**Dashboard UI changes:**
- Top bar: "数据导入" button (opens inline form: sport select + seasons input) → calls `triggerIngest`
- Top bar: "运行优化" button (sport select + n_trials input) → calls `triggerOptimization` → gets `task_id` → starts `useTaskStatus` polling
- Task progress: when `taskId` is set, show progress bar with task status (pending → running → completed/failed)
- Table: each row gets an "应用" button (only when `status === "completed"`) → calls `applyParams(id)` → confirmation dialog → mutate params list
- Error handling: all mutations show inline error messages

### 4.5 Match Detail Page (4 Tabs)

`/sports/[matchId]` tab structure:
```typescript
type TabId = "details" | "edge" | "odds" | "realtime";
```

- "比赛详情" (existing) — MatchDetailPanel
- "Edge 分析" (NEW) — EdgeDetailPanel + EdgeTimelineChart
- "赔率对比" (existing) — TraditionalOddsChart
- "实时价格" (NEW) — RealtimePriceTable

URL param `?tab=edge` etc. for deep linking — read from `useSearchParams()`, default to `"details"`. Tab buttons update the URL via `router.replace()`.

### 4.6 Settlements Manual Trigger

- `use-settlements.ts`: add `processSettlement(matchId: string): Promise<void>`
- Settlements page: each row in the history table gets a "重算" button
- Button calls `processSettlement(matchId)` → on success, `mutate` the history list
- Confirmation prompt before triggering (POST is a write operation)
- Shows inline loading state on the button

### 4.7 Navigation Update

Add to Sports Prediction OS group:
```typescript
{ href: "/sports/edges", label: "Edge 偏离", icon: Crosshair, match: ["/sports/edges"] },
```

Icon: `Crosshair` from lucide-react (not used elsewhere, fits "targeting edge opportunities" semantics).

Final Sports Prediction OS group (9 items):
1. 体育预测 (Medal)
2. Edge 偏离 (Crosshair) — NEW
3. 期货市场 (Trophy)
4. 学习仪表盘 (GraduationCap)
5. 体育市场 (LineChart)
6. 参数优化 (Wrench)
7. 体育推荐 (Lightbulb)
8. 体育结算 (CircleDollarSign)
9. 世界杯专属 (Globe)

## 5. Data Flow

### Edge Detector
```
Backend: /sport-edges/{match_id}/latest → EdgeLatestResponse
         /sport-edges/{match_id}/history → EdgeHistoryResponse
         /sport-edges/discrepancies → EdgeDiscrepanciesResponse
                     ↓
Frontend: use-edges.ts (SWR hooks)
                     ↓
Components: EdgeDiscrepanciesTable (global view)
            EdgeDetailPanel (per-match view)
            EdgeTimelineChart (history chart)
                     ↓
Pages: /sports/edges (discrepancies table)
       /sports/[matchId]?tab=edge (detail + timeline)
```

### Real-time Prices
```
Backend: WebSocket /ws/matches/{match_id}/prices
                     ↓
Frontend: use-price-stream.ts (existing hook)
         returns { updates: PriceUpdate[], isConnected, error }
                     ↓
Component: RealtimePriceTable (NEW — consumes updates[])
           RealtimePriceIndicator (existing — consumes isConnected)
                     ↓
Page: /sports/[matchId]?tab=realtime
```

### Optimization
```
User clicks "运行优化"
  → triggerOptimization(sport, nTrials) → POST /sport-optimization/run → { task_id }
  → useTaskStatus(task_id) polls GET /sport-optimization/status/{task_id} every 2s
  → status transitions: pending → running → completed/failed
  → on completed: mutate useOptimizationParams() to refresh table

User clicks "应用" on a completed params row
  → applyParams(paramsId) → POST /sport-optimization/apply/{params_id}
  → mutate useOptimizationParams()

User clicks "数据导入"
  → triggerIngest(sport, seasons) → POST /sport-optimization/ingest
  → show result summary
```

## 6. Error Handling

- All SWR hooks use the global `SWRProvider` fetcher (handles 503 feature-flag-off → localized error message)
- POST mutations (triggerOptimization, triggerIngest, applyParams, processSettlement) use `sportPost` (handles auth, timeout, error localization)
- Feature-flag-off (503) shows: "此功能暂未启用" in components
- Edge `skipped` state shows skip_reason prominently (not buried in error)
- WebSocket disconnect shows OFFLINE badge + last received prices (stale indicator)
- Task polling stops on `completed` or `failed` status

## 7. Testing

### New Hook Tests
- `use-edges.test.ts` — 3 hooks × 2-3 tests each (basic render, error, empty)
- `use-optimization.test.ts` — extend with triggerIngest, useTaskStatus, applyParams tests
- `use-settlements.test.ts` — extend with processSettlement test

### New Component Tests
- `EdgeDiscrepanciesTable.test.tsx` — render, row click navigation, loading/error/empty
- `EdgeTimelineChart.test.tsx` — render with data, empty state, recharts mock
- `EdgeDetailPanel.test.tsx` — render with outcomes, skipped state, sources breakdown
- `RealtimePriceTable.test.tsx` — render with updates, empty state, connection status

### Modified Component Tests
- `OptimizationDashboard.test.tsx` — extend with run button, task polling, apply button
- `app-nav.test.tsx` — extend with Edge 偏离 entry + icon uniqueness (now 17 items)
- `navigation-shell.test.ts` — add `sports/edges/page.tsx`

### Page Tests
- `/sports/[matchId]` tab switching (4 tabs) — existing test may need update

## 8. Success Criteria

1. `/sports/edges` page shows global edge discrepancy leaderboard
2. `/sports/[matchId]` has 4 tabs: 比赛详情, Edge 分析, 赔率对比, 实时价格
3. Edge 分析 tab shows edge timeline chart + per-outcome detail with trust/liquidity/sources
4. 实时价格 tab shows live-updating price table from WebSocket `updates[]`
5. `/sports/optimization` has "运行优化" button → task progress → "应用" button per row
6. `/sports/optimization` has "数据导入" button
7. `/sports/settlements` has "重算" button per row
8. Navigation has "Edge 偏离" entry (Sports Prediction OS group, 9 items)
9. All new hooks/components have tests
10. `npx vitest run` all pass
11. `npx tsc --noEmit` zero errors
12. Zero backend changes

## 9. Task Breakdown

### Task 1: Edge Detector hooks + types
- Create `use-edges.ts` with 3 hooks + types
- Create `use-edges.test.ts`
- Update `index.ts` re-exports
- TDD throughout

### Task 2: Edge Detector components + page
- Create `EdgeDiscrepanciesTable.tsx` + test
- Create `EdgeTimelineChart.tsx` + test
- Create `EdgeDetailPanel.tsx` + test
- Create `app/sports/edges/page.tsx`
- Update `navigation-shell.test.ts`
- Update `app-nav.tsx` + `app-nav.test.tsx` (new nav entry)

### Task 3: Real-time price table
- Create `RealtimePriceTable.tsx` + test
- Update `RealtimePriceIndicator.tsx` (inline → Tailwind)

### Task 4: Match detail 4-tab enhancement
- Update `app/sports/[matchId]/page.tsx` — 2 tabs → 4 tabs
- Wire Edge tab to EdgeDetailPanel + EdgeTimelineChart
- Wire 实时价格 tab to RealtimePriceTable
- Update existing tests if needed

### Task 5: Optimization dashboard enhancement
- Extend `use-optimization.ts` with triggerIngest, useTaskStatus, applyParams + tests
- Update `OptimizationDashboard.tsx` with run/ingest/apply/status UI + tests

### Task 6: Settlements manual trigger
- Extend `use-settlements.ts` with processSettlement + tests
- Update `app/sports/settlements/page.tsx` with 重算 button + tests

### Task 7: Full test run + tsc + commit
