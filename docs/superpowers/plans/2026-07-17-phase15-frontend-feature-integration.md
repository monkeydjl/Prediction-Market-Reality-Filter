# Phase 15: Frontend Feature Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Phase 7-12 backend capabilities (Edge Detector, real-time prices, optimization, settlements) into the frontend.

**Architecture:** New SWR hooks in `lib/sports-api/hooks/` fetch edge/optimization/settlement data. New React components in `components/sports/edges/` and `components/sports/realtime/` render the data. The match detail page expands from 2 to 4 tabs. The optimization dashboard gains mutation buttons with task polling.

**Tech Stack:** Next.js (static export), React, TypeScript, SWR, recharts, Tailwind CSS, vitest, @testing-library/react

## Global Constraints

- Zero backend changes
- All text in Chinese (UI labels, test descriptions)
- Follow existing SWR hook patterns: `useSWR<ResponseType>(key)` with `getApiBase()` prefix
- POST mutations use `sportPost<T>(path, body)` from `lib/sports-api/client.ts`
- Query strings use `buildQuery()` from `lib/sports-api/client.ts`
- Tests use `@testing-library/react`, mock `swr` and `@/lib/env` per existing patterns
- Recharts components must be mocked in tests
- `npx tsc --noEmit` must pass with zero errors
- `npx vitest run` must pass with zero failures

---

### Task 1: Edge Detector Hooks + Types

**Files:**
- Create: `frontend/src/lib/sports-api/hooks/use-edges.ts`
- Create: `frontend/src/lib/sports-api/hooks/use-edges.test.ts`
- Modify: `frontend/src/lib/sports-api/index.ts`

**Interfaces:**
- Consumes: `getApiBase` from `@/lib/env`, `buildQuery` from `../client`, `useSWR` from `swr`
- Produces: `useEdgeLatest`, `useEdgeHistory`, `useEdgeDiscrepancies` hooks + edge type interfaces

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/sports-api/hooks/use-edges.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { useEdgeLatest, useEdgeHistory, useEdgeDiscrepancies } from "./use-edges";
import useSWR from "swr";

describe("useEdgeLatest", () => {
  it("builds key with matchId", () => {
    useEdgeLatest("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-edges/m1/latest");
  });

  it("returns null key when matchId is null", () => {
    useEdgeLatest(null);
    expect(useSWR).toHaveBeenCalledWith(null);
  });
});

describe("useEdgeHistory", () => {
  it("builds key without mappedOutcome", () => {
    useEdgeHistory("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-edges/m1/history");
  });

  it("builds key with mappedOutcome query param", () => {
    useEdgeHistory("m1", "HOME_WIN");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-edges/m1/history?mapped_outcome=HOME_WIN");
  });

  it("returns null key when matchId is null", () => {
    useEdgeHistory(null);
    expect(useSWR).toHaveBeenCalledWith(null);
  });
});

describe("useEdgeDiscrepancies", () => {
  it("builds key with default params", () => {
    useEdgeDiscrepancies();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-edges/discrepancies?limit=20");
  });

  it("builds key with custom params", () => {
    useEdgeDiscrepancies({ limit: 50, min_abs_edge: 0.05 });
    expect(useSWR).toHaveBeenCalledWith("/api/sport-edges/discrepancies?limit=50&min_abs_edge=0.05");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run src/lib/sports-api/hooks/use-edges.test.ts 2>&1 | Select-Object -Last 10`
Expected: FAIL with "Cannot find module './use-edges'"

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/lib/sports-api/hooks/use-edges.ts`:

```typescript
"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import { buildQuery } from "../client";

// --- Types ---

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

// --- Hooks ---

export function useEdgeLatest(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-edges/${matchId}/latest` : null;
  return useSWR<EdgeLatestResponse>(key);
}

export function useEdgeHistory(matchId: string | null, mappedOutcome?: string) {
  const q = buildQuery({ mapped_outcome: mappedOutcome });
  const key = matchId ? `${getApiBase()}/sport-edges/${matchId}/history${q}` : null;
  return useSWR<EdgeHistoryResponse>(key);
}

export function useEdgeDiscrepancies(params?: { limit?: number; min_abs_edge?: number }) {
  const q = buildQuery({ limit: params?.limit ?? 20, min_abs_edge: params?.min_abs_edge });
  const key = `${getApiBase()}/sport-edges/discrepancies${q}`;
  return useSWR<EdgeDiscrepanciesResponse>(key);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run src/lib/sports-api/hooks/use-edges.test.ts 2>&1 | Select-Object -Last 10`
Expected: PASS (7 tests)

- [ ] **Step 5: Update index.ts re-exports**

Modify `frontend/src/lib/sports-api/index.ts` — add after the optimization exports (end of file):

```typescript
export {
  useEdgeLatest,
  useEdgeHistory,
  useEdgeDiscrepancies,
} from "./hooks/use-edges";
export type {
  EdgeSource,
  EdgeResult,
  EdgeLatestResponse,
  EdgeHistoryPoint,
  EdgeHistoryResponse,
  EdgeDiscrepancyItem,
  EdgeDiscrepanciesResponse,
} from "./hooks/use-edges";
```

- [ ] **Step 6: Run tsc to verify no type errors**

Run: `cd frontend; npx tsc --noEmit 2>&1 | Select-Object -Last 5`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
cd "e:\Github\Prediction Market Reality Filter"
git add frontend/src/lib/sports-api/hooks/use-edges.ts frontend/src/lib/sports-api/hooks/use-edges.test.ts frontend/src/lib/sports-api/index.ts
git commit -m "feat(frontend): add edge detector SWR hooks with types"
```

---

### Task 2: Edge Detector Components + Page + Navigation

**Files:**
- Create: `frontend/src/components/sports/edges/EdgeDiscrepanciesTable.tsx`
- Create: `frontend/src/components/sports/edges/EdgeDiscrepanciesTable.test.tsx`
- Create: `frontend/src/components/sports/edges/EdgeTimelineChart.tsx`
- Create: `frontend/src/components/sports/edges/EdgeTimelineChart.test.tsx`
- Create: `frontend/src/components/sports/edges/EdgeDetailPanel.tsx`
- Create: `frontend/src/components/sports/edges/EdgeDetailPanel.test.tsx`
- Create: `frontend/src/app/sports/edges/page.tsx`
- Modify: `frontend/src/components/app-nav.tsx`
- Modify: `frontend/src/components/app-nav.test.tsx`
- Modify: `frontend/src/app/navigation-shell.test.ts`

**Interfaces:**
- Consumes: `useEdgeLatest`, `useEdgeHistory`, `useEdgeDiscrepancies` from Task 1
- Produces: `EdgeDiscrepanciesTable`, `EdgeTimelineChart`, `EdgeDetailPanel` components + `/sports/edges` page

- [ ] **Step 1: Write EdgeDiscrepanciesTable failing test**

Create `frontend/src/components/sports/edges/EdgeDiscrepanciesTable.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/link", () => ({ default: "a" }));

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { EdgeDiscrepanciesTable } from "./EdgeDiscrepanciesTable";

describe("EdgeDiscrepanciesTable", () => {
  it("加载期间显示加载中", () => {
    render(<EdgeDiscrepanciesTable />);
    expect(screen.getByText("加载中...")).toBeDefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run src/components/sports/edges/EdgeDiscrepanciesTable.test.tsx 2>&1 | Select-Object -Last 10`
Expected: FAIL with "Cannot find module"

- [ ] **Step 3: Implement EdgeDiscrepanciesTable**

Create `frontend/src/components/sports/edges/EdgeDiscrepanciesTable.tsx`:

```typescript
"use client";

import Link from "next/link";
import { useEdgeDiscrepancies } from "@/lib/sports-api";

export function EdgeDiscrepanciesTable() {
  const { data, error, isLoading } = useEdgeDiscrepancies();

  if (isLoading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">加载失败: {error instanceof Error ? error.message : "未知错误"}</div>;
  if (!data || data.items.length === 0) return <div data-testid="empty">暂无 Edge 偏离数据</div>;

  return (
    <div data-testid="discrepancies-table" className="space-y-2">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="p-2 text-left">比赛</th>
            <th className="p-2 text-left">结果</th>
            <th className="p-2 text-right">模型概率</th>
            <th className="p-2 text-right">市场概率</th>
            <th className="p-2 text-right">原始 Edge</th>
            <th className="p-2 text-right">调整 Edge</th>
            <th className="p-2 text-center">状态</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((item) => (
            <tr key={`${item.match_id}-${item.mapped_outcome}`} className="border-b hover:bg-muted/30">
              <td className="p-2">
                <Link href={`/sports/${item.match_id}?tab=edge`} className="text-primary hover:underline">
                  {item.match_id}
                </Link>
              </td>
              <td className="p-2">{item.mapped_outcome}</td>
              <td className="p-2 text-right">{(item.model_prob * 100).toFixed(1)}%</td>
              <td className="p-2 text-right">{(item.market_prob * 100).toFixed(1)}%</td>
              <td className="p-2 text-right">{(item.raw_edge * 100).toFixed(1)}%</td>
              <td className="p-2 text-right font-medium">{(item.adjusted_edge * 100).toFixed(1)}%</td>
              <td className="p-2 text-center">
                {item.stale ? (
                  <span className="text-muted-foreground">过期</span>
                ) : (
                  <span className="text-green-600">活跃</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-muted-foreground">共 {data.total} 条记录</p>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run src/components/sports/edges/EdgeDiscrepanciesTable.test.tsx 2>&1 | Select-Object -Last 10`
Expected: PASS

- [ ] **Step 5: Write EdgeTimelineChart failing test**

Create `frontend/src/components/sports/edges/EdgeTimelineChart.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("recharts", () => ({
  LineChart: ({ children }: { children: React.ReactNode }) => <div data-testid="line-chart">{children}</div>,
  Line: () => <div data-testid="line" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="grid" />,
  Tooltip: () => <div data-testid="tooltip" />,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div data-testid="container">{children}</div>,
}));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { EdgeTimelineChart } from "./EdgeTimelineChart";

describe("EdgeTimelineChart", () => {
  it("加载期间显示加载中", () => {
    render(<EdgeTimelineChart matchId="m1" />);
    expect(screen.getByText("加载中...")).toBeDefined();
  });
});
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd frontend; npx vitest run src/components/sports/edges/EdgeTimelineChart.test.tsx 2>&1 | Select-Object -Last 10`
Expected: FAIL with "Cannot find module"

- [ ] **Step 7: Implement EdgeTimelineChart**

Create `frontend/src/components/sports/edges/EdgeTimelineChart.tsx`:

```typescript
"use client";

import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { useEdgeHistory } from "@/lib/sports-api";

interface EdgeTimelineChartProps {
  matchId: string;
  mappedOutcome?: string;
}

export function EdgeTimelineChart({ matchId, mappedOutcome }: EdgeTimelineChartProps) {
  const { data, error, isLoading } = useEdgeHistory(matchId, mappedOutcome);

  if (isLoading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">加载失败: {error instanceof Error ? error.message : "未知错误"}</div>;
  if (!data || data.series.length === 0) return <div data-testid="empty">暂无 Edge 历史数据</div>;

  // Flatten all series snapshots into chart data points
  const chartData = data.series.flatMap((s) =>
    s.snapshots.map((snap) => ({
      captured_at: snap.captured_at,
      outcome: s.mapped_outcome,
      model_prob: snap.model_prob,
      market_prob: snap.market_prob,
      adjusted_edge: snap.adjusted_edge,
    })),
  );

  return (
    <div data-testid="timeline-chart" className="space-y-2">
      <h3 className="text-sm font-medium">Edge 历史</h3>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="captured_at" tick={{ fontSize: 10 }} />
          <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
          <Tooltip />
          <Line type="monotone" dataKey="model_prob" stroke="#3b82f6" name="模型概率" dot={false} />
          <Line type="monotone" dataKey="market_prob" stroke="#ef4444" name="市场概率" dot={false} />
          <Line type="monotone" dataKey="adjusted_edge" stroke="#10b981" name="调整 Edge" dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd frontend; npx vitest run src/components/sports/edges/EdgeTimelineChart.test.tsx 2>&1 | Select-Object -Last 10`
Expected: PASS

- [ ] **Step 9: Write EdgeDetailPanel failing test**

Create `frontend/src/components/sports/edges/EdgeDetailPanel.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { EdgeDetailPanel } from "./EdgeDetailPanel";

describe("EdgeDetailPanel", () => {
  it("加载期间显示加载中", () => {
    render(<EdgeDetailPanel matchId="m1" />);
    expect(screen.getByText("加载中...")).toBeDefined();
  });
});
```

- [ ] **Step 10: Run test to verify it fails**

Run: `cd frontend; npx vitest run src/components/sports/edges/EdgeDetailPanel.test.tsx 2>&1 | Select-Object -Last 10`
Expected: FAIL with "Cannot find module"

- [ ] **Step 11: Implement EdgeDetailPanel**

Create `frontend/src/components/sports/edges/EdgeDetailPanel.tsx`:

```typescript
"use client";

import { useEdgeLatest } from "@/lib/sports-api";

interface EdgeDetailPanelProps {
  matchId: string;
}

export function EdgeDetailPanel({ matchId }: EdgeDetailPanelProps) {
  const { data, error, isLoading } = useEdgeLatest(matchId);

  if (isLoading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">加载失败: {error instanceof Error ? error.message : "未知错误"}</div>;
  if (!data) return <div data-testid="empty">暂无数据</div>;

  if (data.skipped) {
    return (
      <div data-testid="skipped" className="rounded border border-yellow-500/50 bg-yellow-500/10 p-3 text-sm">
        <p className="font-medium">Edge 检测已跳过</p>
        <p className="text-muted-foreground">原因: {data.skip_reason}</p>
      </div>
    );
  }

  return (
    <div data-testid="edge-detail" className="space-y-4">
      {data.engine_name && (
        <p className="text-sm text-muted-foreground">引擎: {data.engine_name}</p>
      )}
      {data.outcomes.map((outcome) => (
        <div key={outcome.mapped_outcome} className="rounded border p-3 space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="font-medium">{outcome.mapped_outcome}</h3>
            {outcome.stale && <span className="text-xs text-muted-foreground">过期</span>}
          </div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>模型概率: <span className="font-mono">{(outcome.model_prob * 100).toFixed(1)}%</span></div>
            <div>市场概率: <span className="font-mono">{(outcome.market_prob * 100).toFixed(1)}%</span></div>
            <div>原始 Edge: <span className="font-mono">{(outcome.raw_edge * 100).toFixed(1)}%</span></div>
            <div>调整 Edge: <span className="font-mono font-medium">{(outcome.adjusted_edge * 100).toFixed(1)}%</span></div>
            <div>信任度: <span className="font-mono">{outcome.trust.toFixed(3)}</span></div>
            <div>流动性因子: <span className="font-mono">{outcome.liquidity_factor.toFixed(3)}</span></div>
            {outcome.spread !== null && (
              <div>价差: <span className="font-mono">{outcome.spread.toFixed(3)}</span></div>
            )}
            <div>数据源数: <span className="font-mono">{outcome.sources_count}</span></div>
          </div>
          {outcome.sources.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">数据源详情 ({outcome.sources.length})</summary>
              <table className="mt-1 w-full border-collapse">
                <thead>
                  <tr className="border-b">
                    <th className="p-1 text-left">来源</th>
                    <th className="p-1 text-right">隐含概率</th>
                    <th className="p-1 text-right">权重</th>
                  </tr>
                </thead>
                <tbody>
                  {outcome.sources.map((src) => (
                    <tr key={src.link_id} className="border-b">
                      <td className="p-1">{src.source}</td>
                      <td className="p-1 text-right font-mono">{(src.implied_prob * 100).toFixed(1)}%</td>
                      <td className="p-1 text-right font-mono">{src.weight.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 12: Run test to verify it passes**

Run: `cd frontend; npx vitest run src/components/sports/edges/EdgeDetailPanel.test.tsx 2>&1 | Select-Object -Last 10`
Expected: PASS

- [ ] **Step 13: Create /sports/edges page**

Create `frontend/src/app/sports/edges/page.tsx`:

```typescript
import { EdgeDiscrepanciesTable } from "@/components/sports/edges/EdgeDiscrepanciesTable";

export default function EdgesPage() {
  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-6 md:px-6">
      <h1 className="text-2xl font-bold">Edge 偏离</h1>
      <p className="text-sm text-muted-foreground">
        展示模型预测与市场概率的最大偏离，按调整 Edge 排序。
      </p>
      <EdgeDiscrepanciesTable />
    </main>
  );
}
```

- [ ] **Step 14: Update navigation-shell.test.ts**

Modify `frontend/src/app/navigation-shell.test.ts` — add `"sports/edges/page.tsx"` to the file list array (after `"sports/page.tsx"`):

```typescript
      "sports/page.tsx",
      "sports/edges/page.tsx",
      "sports/[matchId]/page.tsx",
```

- [ ] **Step 15: Update app-nav.tsx — add Crosshair import + nav entry**

Modify `frontend/src/components/app-nav.tsx`:

1. Add `Crosshair` to the lucide-react import (after `CircleDollarSign`):

```typescript
import {
  Activity,
  CircleDollarSign,
  Crosshair,
  FlaskConical,
```

2. Add the nav entry after `{ href: "/sports", ... }`:

```typescript
      { href: "/sports", label: "体育预测", icon: Medal, match: ["/sports"] },
      { href: "/sports/edges", label: "Edge 偏离", icon: Crosshair, match: ["/sports/edges"] },
      { href: "/sports/futures", label: "期货市场", icon: Trophy, match: ["/sports/futures"] },
```

- [ ] **Step 16: Run all edge tests + tsc**

Run: `cd frontend; npx vitest run src/components/sports/edges/ src/lib/sports-api/hooks/use-edges.test.ts src/app/navigation-shell.test.ts 2>&1 | Select-Object -Last 15`
Expected: all PASS

Run: `cd frontend; npx tsc --noEmit 2>&1 | Select-Object -Last 5`
Expected: no errors

- [ ] **Step 17: Commit**

```bash
cd "e:\Github\Prediction Market Reality Filter"
git add frontend/src/components/sports/edges/ frontend/src/app/sports/edges/ frontend/src/components/app-nav.tsx frontend/src/app/navigation-shell.test.ts
git commit -m "feat(frontend): add edge detector components, /sports/edges page, and nav entry"
```

---

### Task 3: Real-time Price Table

**Files:**
- Create: `frontend/src/components/sports/realtime/RealtimePriceTable.tsx`
- Create: `frontend/src/components/sports/realtime/RealtimePriceTable.test.tsx`
- Modify: `frontend/src/components/sports/realtime/RealtimePriceIndicator.tsx`

**Interfaces:**
- Consumes: `usePriceStream` from `@/lib/use-price-stream`, `RealtimePriceIndicator`
- Produces: `RealtimePriceTable` component

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/sports/realtime/RealtimePriceTable.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/lib/use-price-stream", () => ({
  usePriceStream: vi.fn(() => ({
    updates: [],
    isConnected: false,
    error: null,
  })),
}));

import { RealtimePriceTable } from "./RealtimePriceTable";
import { usePriceStream } from "@/lib/use-price-stream";

describe("RealtimePriceTable", () => {
  it("未连接时显示 OFFLINE", () => {
    render(<RealtimePriceTable matchId="m1" />);
    expect(screen.getByText("OFFLINE")).toBeDefined();
  });

  it("连接但无数据时显示等待提示", () => {
    vi.mocked(usePriceStream).mockReturnValue({
      updates: [],
      isConnected: true,
      error: null,
    });
    render(<RealtimePriceTable matchId="m1" />);
    expect(screen.getByText("等待实时数据...")).toBeDefined();
  });

  it("有数据时渲染价格表格", () => {
    vi.mocked(usePriceStream).mockReturnValue({
      updates: [
        {
          type: "market_snapshot",
          implied_prob: 0.65,
          price: 1.54,
          outcome: "HOME_WIN",
          bookmaker: "pinnacle",
          captured_at: "2026-01-01T12:00:00Z",
        },
      ],
      isConnected: true,
      error: null,
    });
    render(<RealtimePriceTable matchId="m1" />);
    expect(screen.getByTestId("price-table")).toBeDefined();
    expect(screen.getByText("HOME_WIN")).toBeDefined();
    expect(screen.getByText("pinnacle")).toBeDefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run src/components/sports/realtime/RealtimePriceTable.test.tsx 2>&1 | Select-Object -Last 10`
Expected: FAIL with "Cannot find module"

- [ ] **Step 3: Implement RealtimePriceTable**

Create `frontend/src/components/sports/realtime/RealtimePriceTable.tsx`:

```typescript
"use client";

import { usePriceStream, type PriceUpdate } from "@/lib/use-price-stream";
import { RealtimePriceIndicator } from "./RealtimePriceIndicator";

interface RealtimePriceTableProps {
  matchId: string;
}

function formatTime(iso: string | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}

function typeLabel(type: string | undefined): string {
  if (type === "market_snapshot") return "市场快照";
  if (type === "odds_snapshot") return "赔率快照";
  return type ?? "—";
}

export function RealtimePriceTable({ matchId }: RealtimePriceTableProps) {
  const { updates, isConnected, error } = usePriceStream(matchId);

  const sorted = [...updates].reverse();

  return (
    <div data-testid="realtime-price-table" className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium">实时价格</h3>
        <RealtimePriceIndicator isConnected={isConnected} matchId={matchId} />
      </div>
      {error && <p className="text-sm text-destructive">{error.message}</p>}
      {!isConnected && sorted.length === 0 && (
        <p className="text-sm text-muted-foreground">未连接到实时数据源</p>
      )}
      {isConnected && sorted.length === 0 && (
        <p className="text-sm text-muted-foreground">等待实时数据...</p>
      )}
      {sorted.length > 0 && (
        <div data-testid="price-table" className="max-h-96 overflow-y-auto">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 bg-background">
              <tr className="border-b">
                <th className="p-2 text-left">时间</th>
                <th className="p-2 text-left">类型</th>
                <th className="p-2 text-left">结果</th>
                <th className="p-2 text-right">隐含概率</th>
                <th className="p-2 text-right">价格</th>
                <th className="p-2 text-right">赔率</th>
                <th className="p-2 text-left">来源</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((u, i) => (
                <tr key={`${i}-${u.captured_at ?? ""}`} className="border-b">
                  <td className="p-2 font-mono text-xs">{formatTime(u.captured_at)}</td>
                  <td className="p-2 text-xs">{typeLabel(u.type)}</td>
                  <td className="p-2">{u.outcome ?? "—"}</td>
                  <td className="p-2 text-right font-mono">
                    {u.implied_prob !== undefined ? `${(u.implied_prob * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="p-2 text-right font-mono">
                    {u.price !== undefined ? u.price.toFixed(2) : "—"}
                  </td>
                  <td className="p-2 text-right font-mono">
                    {u.decimal_odds !== undefined ? u.decimal_odds.toFixed(2) : "—"}
                  </td>
                  <td className="p-2 text-xs">{u.bookmaker ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run src/components/sports/realtime/RealtimePriceTable.test.tsx 2>&1 | Select-Object -Last 10`
Expected: PASS (3 tests)

- [ ] **Step 5: Update RealtimePriceIndicator to use Tailwind**

Modify `frontend/src/components/sports/realtime/RealtimePriceIndicator.tsx` — replace entire file:

```typescript
"use client";

interface RealtimePriceIndicatorProps {
  isConnected: boolean;
  matchId?: string | null;
}

export function RealtimePriceIndicator({
  isConnected,
  matchId,
}: RealtimePriceIndicatorProps) {
  if (matchId === null) {
    return null;
  }

  const label = isConnected ? "LIVE" : "OFFLINE";

  return (
    <span
      className={`ml-2 rounded border px-1.5 py-0.5 text-xs font-semibold ${
        isConnected
          ? "border-green-500 text-green-600"
          : "border-gray-400 text-gray-500"
      }`}
      data-testid="realtime-indicator"
    >
      {label}
    </span>
  );
}
```

- [ ] **Step 6: Run all realtime tests**

Run: `cd frontend; npx vitest run src/components/sports/realtime/ 2>&1 | Select-Object -Last 10`
Expected: PASS

- [ ] **Step 7: Run tsc**

Run: `cd frontend; npx tsc --noEmit 2>&1 | Select-Object -Last 5`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
cd "e:\Github\Prediction Market Reality Filter"
git add frontend/src/components/sports/realtime/
git commit -m "feat(frontend): add RealtimePriceTable consuming WebSocket price updates"
```

---

### Task 4: Match Detail 4-Tab Enhancement

**Files:**
- Modify: `frontend/src/app/sports/[matchId]/page.tsx`

**Interfaces:**
- Consumes: `EdgeDetailPanel`, `EdgeTimelineChart` from Task 2; `RealtimePriceTable` from Task 3
- Produces: 4-tab match detail page with URL deep-linking

- [ ] **Step 1: Read the current match detail page**

Read `frontend/src/app/sports/[matchId]/page.tsx` to confirm current structure (2 tabs: details + odds).

- [ ] **Step 2: Implement 4-tab enhancement**

Modify `frontend/src/app/sports/[matchId]/page.tsx`:

1. Add imports at the top (after existing imports):

```typescript
import { useSearchParams, useRouter } from "next/navigation";
import { EdgeDetailPanel } from "@/components/sports/edges/EdgeDetailPanel";
import { EdgeTimelineChart } from "@/components/sports/edges/EdgeTimelineChart";
import { RealtimePriceTable } from "@/components/sports/realtime/RealtimePriceTable";
```

2. Update the TabId type:

```typescript
type TabId = "details" | "edge" | "odds" | "realtime";
```

3. Replace the component body from `export default function MatchDetailPage()` onward. The key changes are:
   - Use `useSearchParams` to read `tab` param
   - Use `useRouter` to update URL on tab change
   - Add 2 new tab buttons
   - Add 2 new tab content panels

```typescript
export default function MatchDetailPage() {
  const params = useParams();
  const matchId = params.matchId as string;
  const searchParams = useSearchParams();
  const router = useRouter();

  const { data, error, isLoading } = useMatchDetail(matchId);
  const [prediction, setPrediction] = useState<PredictionResult | null>(null);
  const [isPredicting, setIsPredicting] = useState(false);
  const [predictError, setPredictError] = useState<string | null>(null);

  const match: MatchDetail | null = data?.match ?? null;
  const currentPrediction = prediction ?? data?.prediction ?? null;

  const notFound = error instanceof ApiError && error.status === 404;
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : predictError;

  const tabParam = searchParams.get("tab") as TabId | null;
  const activeTab: TabId = tabParam && ["details", "edge", "odds", "realtime"].includes(tabParam)
    ? tabParam
    : "details";

  const handleTabChange = (tab: TabId) => {
    router.replace(`/sports/${matchId}?tab=${tab}`);
  };

  const handlePredict = () => {
    setIsPredicting(true);
    setPredictError(null);
    triggerPrediction(matchId)
      .then((result) => {
        setPrediction(result);
        setIsPredicting(false);
      })
      .catch((err) => {
        setPredictError(err instanceof Error ? err.message : "预测失败");
        setIsPredicting(false);
      });
  };

  if (isLoading) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-6 md:px-6">
        <p className="text-muted-foreground">加载中...</p>
      </main>
    );
  }

  if (notFound) {
    return (
      <main className="mx-auto max-w-4xl space-y-4 px-4 py-6 md:px-6">
        <p className="text-muted-foreground">比赛不存在</p>
        <Link href="/sports" className="text-primary hover:underline">
          返回列表
        </Link>
      </main>
    );
  }

  if (errorMessage || !match) {
    return (
      <main className="mx-auto max-w-4xl space-y-4 px-4 py-6 md:px-6">
        <p className="text-destructive">加载失败: {errorMessage}</p>
        <Link href="/sports" className="text-primary hover:underline">
          返回列表
        </Link>
      </main>
    );
  }

  const tabs: { id: TabId; label: string }[] = [
    { id: "details", label: "比赛详情" },
    { id: "edge", label: "Edge 分析" },
    { id: "odds", label: "赔率对比" },
    { id: "realtime", label: "实时价格" },
  ];

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <Link href="/sports" className="text-sm text-muted-foreground hover:underline">
        ← 返回列表
      </Link>
      <div className="flex gap-2 border-b">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => handleTabChange(tab.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 ${
              activeTab === tab.id
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {activeTab === "details" && (
        <MatchDetailPanel
          match={match}
          prediction={currentPrediction}
          onPredict={handlePredict}
          isPredicting={isPredicting}
        />
      )}
      {activeTab === "edge" && (
        <div className="space-y-6">
          <EdgeDetailPanel matchId={matchId} />
          <EdgeTimelineChart matchId={matchId} />
        </div>
      )}
      {activeTab === "odds" && <TraditionalOddsChart matchId={matchId} />}
      {activeTab === "realtime" && <RealtimePriceTable matchId={matchId} />}
    </main>
  );
}
```

- [ ] **Step 3: Run tsc**

Run: `cd frontend; npx tsc --noEmit 2>&1 | Select-Object -Last 5`
Expected: no errors

- [ ] **Step 4: Run full test suite**

Run: `cd frontend; npx vitest run --maxWorkers=4 2>&1 | Select-Object -Last 10`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd "e:\Github\Prediction Market Reality Filter"
git add frontend/src/app/sports/\[matchId\]/page.tsx
git commit -m "feat(frontend): expand match detail to 4 tabs with Edge analysis and real-time prices"
```

---

### Task 5: Optimization Dashboard Enhancement

**Files:**
- Modify: `frontend/src/lib/sports-api/hooks/use-optimization.ts`
- Modify: `frontend/src/lib/sports-api/hooks/use-optimization.test.ts`
- Modify: `frontend/src/lib/sports-api/index.ts`
- Modify: `frontend/src/components/sports/optimization/OptimizationDashboard.tsx`

**Interfaces:**
- Consumes: `sportPost` from `../client`, `useSWR` from `swr`, `mutate` from `swr`
- Produces: `triggerIngest`, `useTaskStatus`, `applyParams` functions

- [ ] **Step 1: Write failing tests for new optimization functions**

Modify `frontend/src/lib/sports-api/hooks/use-optimization.test.ts` — add these tests at the end of the file:

```typescript
import { triggerIngest, applyParams } from "./use-optimization";

// Re-mock sportPost to track calls
vi.mock("../client", () => ({
  sportPost: vi.fn().mockResolvedValue({ task_id: "t1" }),
  buildQuery: (params: Record<string, unknown>) => {
    const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
    if (entries.length === 0) return "";
    const usp = new URLSearchParams();
    for (const [k, v] of entries) usp.set(k, String(v));
    return `?${usp.toString()}`;
  },
}));

describe("triggerIngest", () => {
  it("is a function", () => {
    expect(typeof triggerIngest).toBe("function");
  });
});

describe("applyParams", () => {
  it("is a function", () => {
    expect(typeof applyParams).toBe("function");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run src/lib/sports-api/hooks/use-optimization.test.ts 2>&1 | Select-Object -Last 10`
Expected: FAIL with "triggerIngest is not exported"

- [ ] **Step 3: Implement new optimization functions**

Modify `frontend/src/lib/sports-api/hooks/use-optimization.ts` — add these at the end of the file:

```typescript
export async function triggerIngest(
  sport: string,
  seasons: string[],
): Promise<Record<string, unknown>> {
  const result = await sportPost<Record<string, unknown>>(
    `/sport-optimization/ingest`,
    { sport, seasons },
  );
  return result;
}

export function useTaskStatus(taskId: string | null) {
  const key = taskId ? `${getApiBase()}/sport-optimization/status/${taskId}` : null;
  return useSWR<TaskStatus>(key, {
    refreshInterval: (data) => {
      if (!data) return 2000;
      return data.status === "completed" || data.status === "failed" ? 0 : 2000;
    },
  });
}

export interface TaskStatus {
  task_id: string;
  status: "pending" | "running" | "completed" | "failed";
  progress: number;
  result: unknown;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export async function applyParams(paramsId: number): Promise<unknown> {
  const result = await sportPost<unknown>(
    `/sport-optimization/apply/${paramsId}`,
  );
  await mutate(`${getApiBase()}/sport-optimization/params`);
  return result;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run src/lib/sports-api/hooks/use-optimization.test.ts 2>&1 | Select-Object -Last 10`
Expected: PASS

- [ ] **Step 5: Update index.ts re-exports**

Modify `frontend/src/lib/sports-api/index.ts` — update the optimization export block:

```typescript
export {
  useOptimizationParams,
  triggerOptimization,
  triggerIngest,
  useTaskStatus,
  applyParams,
} from "./hooks/use-optimization";
export type { TaskStatus } from "./hooks/use-optimization";
```

- [ ] **Step 6: Update OptimizationDashboard with mutation UI**

Modify `frontend/src/components/sports/optimization/OptimizationDashboard.tsx` — replace entire file:

```typescript
"use client";
import { useState } from "react";
import {
  useOptimizationParams,
  triggerOptimization,
  triggerIngest,
  useTaskStatus,
  applyParams,
  type OptimizedParams,
} from "@/lib/sports-api";

export function OptimizationDashboard() {
  const { data: params, error, isLoading, mutate } = useOptimizationParams();
  const [taskId, setTaskId] = useState<string | null>(null);
  const [runSport, setRunSport] = useState("nba");
  const [ingestSport, setIngestSport] = useState("nba");
  const [ingestSeasons, setIngestSeasons] = useState("2024-25");
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [ingestResult, setIngestResult] = useState<string | null>(null);

  const { data: taskStatus } = useTaskStatus(taskId);

  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : actionError;

  const handleRun = () => {
    setActionLoading(true);
    setActionError(null);
    triggerOptimization(runSport, 150)
      .then((res) => {
        setTaskId(res.task_id);
        setActionLoading(false);
      })
      .catch((err) => {
        setActionError(err instanceof Error ? err.message : "优化启动失败");
        setActionLoading(false);
      });
  };

  const handleIngest = () => {
    setActionLoading(true);
    setActionError(null);
    const seasons = ingestSeasons.split(",").map((s) => s.trim()).filter(Boolean);
    triggerIngest(ingestSport, seasons)
      .then((res) => {
        setIngestResult(`数据导入完成: ${Object.keys(res).length} 个赛季`);
        setActionLoading(false);
      })
      .catch((err) => {
        setActionError(err instanceof Error ? err.message : "数据导入失败");
        setActionLoading(false);
      });
  };

  const handleApply = (paramsId: number) => {
    setActionLoading(true);
    setActionError(null);
    applyParams(paramsId)
      .then(() => {
        mutate();
        setActionLoading(false);
      })
      .catch((err) => {
        setActionError(err instanceof Error ? err.message : "应用参数失败");
        setActionLoading(false);
      });
  };

  if (isLoading) return <div data-testid="loading">加载中...</div>;

  return (
    <div data-testid="optimization-dashboard" className="space-y-4">
      <h2 className="text-xl font-bold">参数优化</h2>

      {errorMessage && (
        <div className="rounded border border-destructive/50 bg-destructive/10 p-2 text-sm text-destructive">
          {errorMessage}
        </div>
      )}

      {/* Action bar */}
      <div className="flex flex-wrap gap-4 rounded border p-3">
        <div className="flex items-center gap-2">
          <select
            value={runSport}
            onChange={(e) => setRunSport(e.target.value)}
            className="rounded border bg-background px-2 py-1 text-sm"
          >
            <option value="nba">NBA</option>
            <option value="mlb">MLB</option>
            <option value="nhl">NHL</option>
            <option value="all">全部</option>
          </select>
          <button
            type="button"
            onClick={handleRun}
            disabled={actionLoading}
            className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
          >
            运行优化
          </button>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={ingestSport}
            onChange={(e) => setIngestSport(e.target.value)}
            className="rounded border bg-background px-2 py-1 text-sm"
          >
            <option value="nba">NBA</option>
            <option value="mlb">MLB</option>
            <option value="nhl">NHL</option>
            <option value="all">全部</option>
          </select>
          <input
            value={ingestSeasons}
            onChange={(e) => setIngestSeasons(e.target.value)}
            placeholder="赛季(逗号分隔)"
            className="w-32 rounded border bg-background px-2 py-1 text-sm"
          />
          <button
            type="button"
            onClick={handleIngest}
            disabled={actionLoading}
            className="rounded border px-3 py-1 text-sm disabled:opacity-50"
          >
            数据导入
          </button>
        </div>
      </div>

      {ingestResult && (
        <p className="text-sm text-muted-foreground">{ingestResult}</p>
      )}

      {/* Task progress */}
      {taskStatus && (
        <div data-testid="task-progress" className="rounded border p-3 text-sm">
          <p>任务状态: <span className="font-medium">{taskStatus.status}</span></p>
          {taskStatus.status === "running" && (
            <div className="mt-1 h-2 w-full rounded bg-muted">
              <div
                className="h-2 rounded bg-primary transition-all"
                style={{ width: `${(taskStatus.progress ?? 0) * 100}%` }}
              />
            </div>
          )}
          {taskStatus.status === "completed" && (
            <p className="text-green-600">优化完成</p>
          )}
          {taskStatus.status === "failed" && (
            <p className="text-destructive">优化失败: {taskStatus.error}</p>
          )}
        </div>
      )}

      {/* Params table */}
      {!params || params.length === 0 ? (
        <div data-testid="empty">暂无优化参数</div>
      ) : (
        <div data-testid="params-table">
          <table className="w-full border-collapse border">
            <thead>
              <tr className="bg-gray-100">
                <th className="border p-2 text-left">Sport</th>
                <th className="border p-2 text-left">Score</th>
                <th className="border p-2 text-left">Accuracy</th>
                <th className="border p-2 text-left">Brier</th>
                <th className="border p-2 text-left">MAE</th>
                <th className="border p-2 text-left">Samples</th>
                <th className="border p-2 text-left">Status</th>
                <th className="border p-2 text-left">操作</th>
              </tr>
            </thead>
            <tbody>
              {params.map((p: OptimizedParams) => (
                <tr key={p.id}>
                  <td className="border p-2">{p.sport}</td>
                  <td className="border p-2">{p.score.toFixed(4)}</td>
                  <td className="border p-2">{p.accuracy.toFixed(4)}</td>
                  <td className="border p-2">{p.brier_score.toFixed(4)}</td>
                  <td className="border p-2">{p.mae.toFixed(4)}</td>
                  <td className="border p-2">{p.sample_count}</td>
                  <td className="border p-2">{p.status}</td>
                  <td className="border p-2">
                    <button
                      type="button"
                      onClick={() => handleApply(p.id)}
                      disabled={actionLoading}
                      className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                    >
                      应用
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 7: Run tsc**

Run: `cd frontend; npx tsc --noEmit 2>&1 | Select-Object -Last 5`
Expected: no errors

- [ ] **Step 8: Run tests**

Run: `cd frontend; npx vitest run src/lib/sports-api/hooks/use-optimization.test.ts src/components/sports/optimization/ 2>&1 | Select-Object -Last 10`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
cd "e:\Github\Prediction Market Reality Filter"
git add frontend/src/lib/sports-api/hooks/use-optimization.ts frontend/src/lib/sports-api/hooks/use-optimization.test.ts frontend/src/lib/sports-api/index.ts frontend/src/components/sports/optimization/OptimizationDashboard.tsx
git commit -m "feat(frontend): add optimization run/ingest/apply/status UI with task polling"
```

---

### Task 6: Settlements Manual Trigger

**Files:**
- Modify: `frontend/src/lib/sports-api/hooks/use-settlements.ts`
- Modify: `frontend/src/lib/sports-api/hooks/use-settlements.test.ts` (if exists, else create)
- Modify: `frontend/src/lib/sports-api/index.ts`
- Modify: `frontend/src/app/sports/settlements/page.tsx`

**Interfaces:**
- Consumes: `sportPost` from `../client`, `mutate` from `swr`
- Produces: `processSettlement` function

- [ ] **Step 1: Write failing test**

Check if `frontend/src/lib/sports-api/hooks/use-settlements.test.ts` exists. If not, create it. Add this test:

```typescript
import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("../client", () => ({
  sportPost: vi.fn().mockResolvedValue({ match_id: "m1", status: "ok", settlements_count: 3 }),
  buildQuery: (params: Record<string, unknown>) => {
    const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
    if (entries.length === 0) return "";
    const usp = new URLSearchParams();
    for (const [k, v] of entries) usp.set(k, String(v));
    return `?${usp.toString()}`;
  },
}));

vi.mock("swr", () => ({
  default: vi.fn(() => ({ data: undefined, error: undefined, isLoading: true })),
  mutate: vi.fn(),
}));

import { processSettlement } from "./use-settlements";

describe("processSettlement", () => {
  it("is a function", () => {
    expect(typeof processSettlement).toBe("function");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run src/lib/sports-api/hooks/use-settlements.test.ts 2>&1 | Select-Object -Last 10`
Expected: FAIL with "processSettlement is not exported"

- [ ] **Step 3: Implement processSettlement**

Modify `frontend/src/lib/sports-api/hooks/use-settlements.ts` — add import and function:

1. Add `mutate` to the swr import and `sportPost` import:

```typescript
import useSWR from "swr";
import { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { buildQuery, sportPost } from "../client";
import type { SettlementList, CalibrationList } from "../types";
```

2. Add `processSettlement` at the end:

```typescript
export async function processSettlement(matchId: string): Promise<void> {
  await sportPost(`/sport-settlements/process/${matchId}`);
  await mutate(`${getApiBase()}/sport-settlements/history`);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run src/lib/sports-api/hooks/use-settlements.test.ts 2>&1 | Select-Object -Last 10`
Expected: PASS

- [ ] **Step 5: Update index.ts re-exports**

Modify `frontend/src/lib/sports-api/index.ts` — update the settlements export:

```typescript
export {
  useSettlement,
  useSettlementHistory,
  useCalibrations,
  processSettlement,
} from "./hooks/use-settlements";
```

- [ ] **Step 6: Read and update settlements page**

Read `frontend/src/app/sports/settlements/page.tsx` to understand current structure. Then add a "重算" button to each row in the settlement history table. The button should:
- Call `processSettlement(matchId)` on click
- Show a confirmation prompt
- Show loading state on the button
- Mutate the history list on success

The implementation will depend on the current page structure. Add a `ProcessButton` client component inline or as a separate component. Since the page may be a server component, create a small client component:

Create `frontend/src/components/sports/settlements/ProcessSettlementButton.tsx`:

```typescript
"use client";

import { useState } from "react";
import { processSettlement } from "@/lib/sports-api";

interface ProcessSettlementButtonProps {
  matchId: string;
}

export function ProcessSettlementButton({ matchId }: ProcessSettlementButtonProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = () => {
    if (!window.confirm(`确认重新计算 ${matchId} 的结算?`)) return;
    setLoading(true);
    setError(null);
    processSettlement(matchId)
      .then(() => setLoading(false))
      .catch((err) => {
        setError(err instanceof Error ? err.message : "结算失败");
        setLoading(false);
      });
  };

  return (
    <div className="inline-flex flex-col gap-0.5">
      <button
        type="button"
        onClick={handleClick}
        disabled={loading}
        className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
      >
        {loading ? "计算中..." : "重算"}
      </button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}
```

Then modify `frontend/src/app/sports/settlements/page.tsx` to import and render this button in each row of the history table. The exact modification depends on the current page structure — read it first and add a `<ProcessSettlementButton matchId={item.match_id} />` to each row.

- [ ] **Step 7: Run tsc**

Run: `cd frontend; npx tsc --noEmit 2>&1 | Select-Object -Last 5`
Expected: no errors

- [ ] **Step 8: Run tests**

Run: `cd frontend; npx vitest run src/lib/sports-api/hooks/use-settlements.test.ts 2>&1 | Select-Object -Last 10`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
cd "e:\Github\Prediction Market Reality Filter"
git add frontend/src/lib/sports-api/hooks/use-settlements.ts frontend/src/lib/sports-api/hooks/use-settlements.test.ts frontend/src/lib/sports-api/index.ts frontend/src/components/sports/settlements/ frontend/src/app/sports/settlements/page.tsx
git commit -m "feat(frontend): add manual settlement re-process button per match"
```

---

### Task 7: Full Test Run + tsc + Final Commit

**Files:**
- None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `cd frontend; npx vitest run --maxWorkers=4 2>&1 | Select-Object -Last 15`
Expected: all PASS

- [ ] **Step 2: Run tsc**

Run: `cd frontend; npx tsc --noEmit 2>&1 | Select-Object -Last 5`
Expected: no errors

- [ ] **Step 3: Verify no backend changes**

Run: `cd "e:\Github\Prediction Market Reality Filter"; git diff --stat HEAD~7 -- backend/`
Expected: empty (zero backend changes)

- [ ] **Step 4: Verify all success criteria from spec**

Verify each of the 12 success criteria:
1. `/sports/edges` page exists with EdgeDiscrepanciesTable
2. `/sports/[matchId]` has 4 tabs
3. Edge tab renders EdgeDetailPanel + EdgeTimelineChart
4. Real-time price tab renders RealtimePriceTable
5. OptimizationDashboard has "运行优化" button + task progress + "应用" button
6. OptimizationDashboard has "数据导入" button
7. Settlements page has "重算" button
8. Navigation has "Edge 偏离" entry (9 items in Sports group)
9. All new hooks/components have tests
10. vitest all pass
11. tsc zero errors
12. Zero backend changes

- [ ] **Step 5: Final commit (if any uncommitted changes remain)**

```bash
cd "e:\Github\Prediction Market Reality Filter"
git status --short
# If clean, nothing to commit. If not:
git add -A
git commit -m "test(frontend): verify Phase 15 integration complete"
```
