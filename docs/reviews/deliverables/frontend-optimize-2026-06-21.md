# 前端优化审查报告

**项目**: Prediction Market Reality Filter — 前端  
**审查日期**: 2026-06-21  
**技术栈**: Next.js 16 + React 19 + Tailwind v4 + Recharts + Vitest  
**审查范围**: ~50 个源文件，4,653 行 TypeScript/TSX  
**当前指标**: 静态导出 ~2.5MB，测试 14/16 通过，加载流畅

---

## 总体评价

| 维度 | 评分 | 说明 |
|------|------|------|
| 代码质量 | ⭐⭐⭐⭐⭐ | 结构清晰、类型安全、注释出色 |
| 性能 | ⭐⭐⭐⭐ | Bundle 小、缓存好，缺懒加载 |
| 可维护性 | ⭐⭐⭐⭐ | 组件拆分合理，有一个函数体偏大 |
| 可访问性 | ⭐⭐⭐⭐ | 大部分 aria 标签到位 |
| 测试 | ⭐⭐⭐ | 14/16 通过，缺少 setup cleanup |
| 安全性 | ⭐⭐⭐⭐⭐ | API Key 存储合理，XSS 防护到位 |

**关键发现**: 前端代码整体质量非常高，仅 3 个问题值得立即修复（全部是测试和配置），其余都是锦上添花的优化建议。

---

## 一、必须修复（影响功能）

### 🔴 #1 测试 Setup 缺少 Cleanup — 2 个测试失败

- **文件**: `src/test/setup.ts` + `src/components/detail/manual-resolve-panel.test.tsx`
- **根因**: vitest 配置缺少 `globals: true`，setup.ts 只有 `import "@testing-library/jest-dom/vitest"`，没有注册 `afterEach(cleanup)`
- **影响**: 第一个测试的 DOM 未清理，第二个测试的 `getByLabelText("实际结果（0–100）")` 找到两个 `<input>` 元素
- **修复** (二选一):

**方案 A** — 修改 `setup.ts`（推荐，改动最小）:
```ts
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
afterEach(() => cleanup());
```

**方案 B** — 修改 `vitest.config.ts`:
```ts
export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,           // ← 添加
    setupFiles: ["./src/test/setup.ts"],
  },
});
```

---

## 二、高优先级优化

### 🟠 #2 recharts 全量打包 — 首屏体积可减 ~150KB

- **文件**: `next.config.ts` 和 3 个图表组件
- **现状**: recharts 被静态导入，打包进所有页面的共享 chunk（~393KB 的最大 chunk 包含 recharts）
- **影响范围**: 仅 3 个页面使用图表（analyze、events/detail、history），但首屏 Dashboard 也会加载
- **优化方案** — 使用 `next/dynamic` 懒加载图表组件:

```ts
// 在 analyze/page.tsx、events/page.tsx、history/page.tsx 中:
import dynamic from "next/dynamic";

const ProbabilityChart = dynamic(
  () => import("@/components/detail/probability-chart").then(m => ({ default: m.ProbabilityChart })),
  { ssr: false, loading: () => <ChartSkeleton /> }
);

const CategoryAccuracy = dynamic(
  () => import("@/components/history/category-accuracy").then(m => ({ default: m.CategoryAccuracy })),
  { ssr: false, loading: () => <ChartSkeleton /> }
);
```

- **预计收益**: 首屏 JS 减少 ~150KB（recharts 从共享 chunk 移出），Dashboard 页面加载更快

### 🟠 #3 详情页组件未拆分 — DetailInner 函数体 200+ 行

- **文件**: `src/app/events/page.tsx`，行 22-230
- **问题**: `DetailInner` 函数体内混合了数据获取、状态管理、多个面板渲染。无法做模块级懒加载
- **建议**: 拆分出独立子组件:
  ```
  DetailHeader     —  标题、概率、趋势 sparkline
  DetailCharts     —  ProbabilityChart（已完成，懒加载即可）
  DetailEvidence   —  EvidenceList、MarketPanel
  DetailActions    —  TrackingDecision、ManualResolvePanel、DecisionReportPanel
  ```
- **收益**: 每个子组件可独立懒加载，代码可读性提升，测试更容易

---

## 三、中优先级优化

### 🟡 #4 缺少 useCallback — SystemStatus 组件

- **文件**: `src/components/dashboard/system-status.tsx`，行 23
- **问题**: `load` 函数未用 `useCallback` 包裹，每次渲染都创建新函数引用
- **影响**: 极小（`load` 不传给子组件，仅作为 onClick handler）
- **建议**: 
  ```ts
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try { setStatus(await eventsApi.loopStatus()); }
    catch (e) { setError(e instanceof Error ? e.message : "状态加载失败"); }
    finally { setLoading(false); }
  }, []);
  ```

### 🟡 #5 DashboardPage `loadMore` 依赖不稳定

- **文件**: `src/app/page.tsx`，行 71-85
- **问题**: `loadMore` 的 `useCallback` 依赖 `[events.length]`，每次 `events` 变化都会重建函数。虽然 `loadMore` 仅在按钮 onClick 中调用（无子组件传递），但语义上不完美
- **建议**: 使用 ref 追踪最新 length:
  ```ts
  const eventsLenRef = useRef(events.length);
  eventsLenRef.current = events.length;
  const loadMore = useCallback(async () => {
    const data = await fetchDashboardData(PAGE_SIZE, eventsLenRef.current);
    // ...
  }, []);
  ```

### 🟡 #6 API 缓存永不过期清理

- **文件**: `src/lib/api.ts`，行 17
- **问题**: `getCache` Map 中过期条目永远不会被删除（仅在写入时被覆盖），长期运行的单页应用中会积累过期缓存
- **影响**: 每个缓存条目 ~几百字节，在几个小时的会话中可忽略。但如果用户持续使用数天不刷新页面，Map 会增长
- **建议**: 添加定时清理或使用 LRU-cache 库:
  ```ts
  // 简单方案：定时清理
  if (typeof window !== "undefined") {
    setInterval(() => {
      const now = Date.now();
      for (const [k, v] of getCache) {
        if (v.expiresAt <= now) getCache.delete(k);
      }
    }, 30_000);
  }
  ```

### 🟡 #7 部分图标按钮缺少 aria-label

- **文件**: `src/components/operator-key-control.tsx`
- **问题**: `<button>` 元素内仅有图标（Eye/EyeOff/Key），缺少 `aria-label`，屏幕阅读器可能读作"button"
- **建议**: 为 3 个按钮添加 `aria-label="显示/隐藏 API Key"` 等

---

## 四、低优先级建议

### 🟢 #8 可考虑移除 recharts 依赖

- **影响**: recharts 安装体积 5.1MB，打包后占 ~150-200KB
- **方案**: 
  1. 当前 3 个图表用法都比较简单（LineChart、BarChart）
  2. `Sparkline` 组件已经手写 SVG 且做得很好
  3. 可以参考 Sparkline 风格，手写 `ProbabilityChart` 和 `CategoryAccuracy` 的 SVG
  4. 完全移除 recharts 后，bundle 可再减少 ~200KB
- **权衡**: 手写 SVG 会增加维护成本，recharts 的交互（Tooltip）需要自己实现
- **建议**: 不紧急，当前 recharts 已经足够好。如果未来 bundle 体积成为硬约束再考虑

### 🟢 #9 减少 lucide-react 图标导入

- **现状**: 当前每个组件单独 import 所需图标（如 `import { Moon, Sun } from "lucide-react"`）
- **现状分析**: lucide-react 默认支持 tree-shaking，各个图标独立导出，所以当前写法已经是最优
- **结论**: ✅ 无需修改

### 🟢 #10 next.config.ts 的 images.unoptimized 确认

- **文件**: `next.config.ts`
- **现状**: `images: { unoptimized: true }` 部署在静态导出中是正确且必要的（FastAPI 不提供 Next.js Image Optimization API）
- **确认**: ✅ 当前配置正确，无需修改

### 🟢 #11 添加路由级 Loading Skeleton

- **现状**: `src/app/loading.tsx` 已存在并工作良好 ✅
- **建议**: 各子路由（analyze、history、decisions）也可添加独立的 `loading.tsx`，提供更精细的加载体验

---

## 五、正面发现（做得好的地方）⭐

| # | 亮点 | 文件/位置 |
|---|------|----------|
| 1 | **Sparkline 手写 SVG** — 零依赖、高性能 | `components/sparkline.tsx` |
| 2 | **API 去重+缓存** — inflightGets 防止重复请求 | `lib/api.ts` |
| 3 | **SectionErrorBoundary** — 每个暗格独立容错 | `components/section-error-boundary.tsx` |
| 4 | **AbortController 超时** — 每 API 调用均可配置超时 | `lib/api.ts` |
| 5 | **Cleanup pattern** — 事件页使用 cancelled 标志防内存泄漏 | `app/events/page.tsx` |
| 6 | **CSS 主题系统** — OKLCH + 暗色/亮色 + prefers-reduced-motion | `globals.css` |
| 7 | **导出 CSV** — 内置数据导出功能 | `lib/csv.ts` |
| 8 | **API Key 安全** — 前端使用 sessionStorage + password 输入框 | `lib/api.ts`, `operator-key-control.tsx` |
| 9 | **零 console.log** — 仅 error boundary 中有 console.error | 全局 |
| 10 | **Turbopack** — 开发构建使用 Turbopack，速度极快 | — |
| 11 | **useMemo 正确使用** — EventTable 筛选/排序 memo 化 | `components/dashboard/event-table.tsx` |
| 12 | **类型安全** — adapt.ts 和安全转换器防 undefined | `lib/adapt.ts` |

---

## 六、优化路线图

| 优先级 | 问题 | 预计工时 | 收益 |
|--------|------|---------|------|
| 🔴 立即 | #1 修复测试 setup cleanup | 2 分钟 | 测试 16/16 全过 |
| 🟠 本周 | #2 recharts 懒加载 | 15 分钟 | 首屏 -150KB |
| 🟠 本周 | #3 拆分 DetailInner 组件 | 30 分钟 | 可维护性 ↑ |
| 🟡 本月 | #4-6 useCallback/ref/缓存清理 | 15 分钟 | 微小优化 |
| 🟢 长期 | #8 评估移除 recharts | 2 小时 | Bundle -200KB |

---

## 七、最终建议

1. **修测试** (2min) → 加 `afterEach(cleanup)` 让测试 16/16 全过
2. **加懒加载** (15min) → recharts 组件改成 `next/dynamic`，首屏快 15-20%
3. **拆大组件** (30min) → DetailInner 拆成 4-5 个子组件，未来维护更容易
4. **考虑 bundle** → 当前 2.5MB 静态导出已经很优秀，不急

**结论**: 🟢 前端代码质量很高，只需修复测试后即可上线。优化项是锦上添花，不阻塞发布。

---

**审查人**: 齐活林（Qi）· 交付总监  
