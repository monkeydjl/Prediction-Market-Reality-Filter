# Frontend Optimization Review - 2026-06-21

**项目**: Prediction Market Reality Filter — Frontend (Next.js 16 + React 19)  
**审查日期**: 2026-06-21  
**审查范围**: 全部 5 个页面、28 个组件、7 个 lib 模块、样式和构建配置  
**审查重点**: UX 体验、性能、代码组织、可访问性、视觉一致性

---

## Executive Summary

前端代码质量整体较高：TypeScript strict 模式零 `any`，构建干净，所有页面都具备 loading/error/empty 三态处理。但在用户体验的精细度和工程效率上仍有明显提升空间。

本次审查发现 **32 个优化项**，按影响力分为三档：高影响 6 项、中等影响 12 项、低影响 14 项。其中多项属于"低成本高回报"的快速优化（标记为 Easy），可在 2-3 小时内显著提升用户的感知质量。

---

## 1. 高影响优化（High Impact）

### 1.1 路由级加载反馈缺失

**影响**: UX | **工作量**: Easy  
**文件**: `src/app/loading.tsx`（仅有根路由），缺少 `events/loading.tsx`、`history/loading.tsx`、`decisions/loading.tsx`、`analyze/loading.tsx`

当前在页面间导航时，目标页面的 `"use client"` 组件挂载并开始 fetch 数据之前，用户看不到任何反馈。Next.js 16 的 `loading.tsx` 可以在客户端导航时立即展示骨架屏，大幅改善导航的速度感知。

**建议**: 为每个路由添加 `loading.tsx`，内容应与目标页面的布局骨架匹配（如 Dashboard 用表格骨架、History 用列表骨架）。

### 1.2 Recharts 未做懒加载，每个页面都加载 ~200KB

**影响**: Performance | **工作量**: Easy  
**文件**: `src/components/detail/probability-chart.tsx:3`、`src/components/history/category-accuracy.tsx:2`

Recharts 库约 200KB+，目前通过顶层 `import` 被打包进每个页面的 JS bundle。但实际上只有事件详情页和 History 页用到图表。

**建议**: 使用 `next/dynamic` 懒加载图表组件：

```tsx
import dynamic from "next/dynamic";
const ProbabilityChart = dynamic(
  () => import("./probability-chart").then(m => ({ default: m.ProbabilityChart })),
  { ssr: false, loading: () => <div className="h-48 animate-pulse bg-secondary rounded" /> }
);
```

### 1.3 加载状态只有文字 spinner，缺少内容骨架屏

**影响**: UX | **工作量**: Medium  
**文件**: `page.tsx:171`、`events/page.tsx:78`、`history/page.tsx:144`、`decisions/page.tsx:130`、`decision-report-panel.tsx:37`

所有页面的 loading 态都是居中的"加载中…"文本框。替换为与真实内容形状一致的骨架占位（skeleton），可以消除布局抖动（layout shift）并让页面"感觉"更快。

### 1.4 异步数据获取模式重复 ~8 次

**影响**: Code Quality | **工作量**: Medium  
**文件**: `system-status.tsx`、`recent-predictions.tsx`、`pending-links.tsx`、`decision-report-panel.tsx` 等

至少 8 个组件中存在几乎相同的 `{loading, error, data}` 状态 + `useEffect` + `setTimeout(0)` + async load 模式。特别是 `setTimeout(0)` 包裹模式在 4+ 个组件中重复出现，疑似用于规避 SSR/hydration 问题。

**建议**: 提取 `useAsyncData<T>` 自定义 hook：

```tsx
function useAsyncData<T>(fetcher: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // ...统一处理 setTimeout(0)、cleanup、error 格式化
  return { data, loading, error, refetch };
}
```

### 1.5 破坏性操作缺少确认步骤

**影响**: UX | **工作量**: Medium  
**文件**: `history/page.tsx:91`（批量自动结算）、`manual-resolve-panel.tsx:35`（手动结算）

"立即结算"和"确认结算"两个按钮点击后立即执行，没有二次确认。结算是不可逆操作，误触可能导致数据错误。

**建议**: 至少添加 `window.confirm("确认要结算该事件吗？此操作不可撤销。")`，或使用确认弹窗组件。

### 1.6 移动端导航缺少响应式折叠

**影响**: UX / Responsive | **工作量**: Medium  
**文件**: `src/components/app-nav.tsx:24-67`

4 个导航项 + 状态指示器 + 主题切换 + API Key 控制全部排列在一行 `flex` 容器中，没有换行或溢出处理。在 640px 以下的屏幕上会溢出或挤压变形。

**建议**: 移动端使用汉堡菜单（hamburger menu）或横向滚动 + 渐变遮罩。

---

## 2. 中等影响优化（Medium Impact）

### 2.1 导航活跃链接缺少 `aria-current="page"`

**类别**: A11y | **工作量**: Easy  
**文件**: `src/components/app-nav.tsx:42-55`

当前活跃导航链接有视觉高亮（`bg-secondary text-foreground`），但缺少 `aria-current="page"` 属性，屏幕阅读器无法识别当前所在页面。

**修复**: 在 `<Link>` 上添加 `aria-current={active ? "page" : undefined}`。

### 2.2 缺少 Skip-to-content 跳转链接

**类别**: A11y | **工作量**: Easy  
**文件**: `src/app/layout.tsx:31`

键盘用户每次都需要 Tab 遍历整个导航栏才能到达主内容区。

**修复**: 在 `<body>` 的第一个子元素添加一个视觉隐藏的跳转链接：

```tsx
<a href="#main-content" className="sr-only focus:not-sr-only absolute top-2 left-2 z-50 rounded bg-primary px-3 py-1 text-sm text-primary-foreground">
  跳转至主内容
</a>
```

并为每个页面的 `<main>` 添加 `id="main-content"`。

### 2.3 Dashboard `summarize()` 未做 memoize

**类别**: Performance | **工作量**: Easy  
**文件**: `src/app/page.tsx:116`

`const summary = summarize(events)` 在每次渲染时都重新计算。虽然对短列表开销不大，但用 `useMemo` 消除它是零成本的改进：

```tsx
const summary = useMemo(() => summarize(events), [events]);
```

### 2.4 Sparkline SVG 未做 React.memo

**类别**: Performance | **工作量**: Easy  
**文件**: `src/components/sparkline.tsx`

Sparkline 在每次渲染时都重新计算所有 SVG 坐标点。在事件表格中 50+ 行同时渲染时开销明显。

**修复**: 用 `React.memo` 包裹组件导出。

### 2.5 截断的标题没有 tooltip 显示完整文本

**类别**: UX | **工作量**: Easy  
**文件**: `event-table.tsx:193`、`review-table.tsx:122`、`evidence-list.tsx:24`

事件标题使用 CSS `truncate` 或 `line-clamp-2` 截断，但没有 `title` 属性或 tooltip，用户无法在悬停时查看完整标题。

**修复**: 添加 `title={e.title}`。

### 2.6 外部链接未向辅助技术提示"新窗口打开"

**类别**: A11y | **工作量**: Easy  
**文件**: `market-links.tsx:82-90,112-128`、`evidence-list.tsx:35-43`

`target="_blank"` 的链接没有通知屏幕阅读器会在新窗口打开。

**修复**: 添加 `<span className="sr-only">（在新窗口中打开）</span>`。

### 2.7 API GET 缓存无过期清理

**类别**: Performance | **工作量**: Easy  
**文件**: `src/lib/api.ts:17`

`getCache` 是一个 `Map`，条目有 TTL 过期机制，但过期条目从未被清理。长时间运行的会话中缓存会无限增长。

**修复**: 在 GET 请求成功后检查 `getCache.size`，超过阈值时清理过期条目。

### 2.8 `EvidenceList` 组件已导出但未被任何文件引用

**类别**: Code | **工作量**: Easy  
**文件**: `src/components/detail/evidence-list.tsx:107`

`EvidenceList` 被导出但 events 页面只使用 `OfficialColumn` 和 `NewsColumn`。属于死代码。

### 2.9 移动端表格列标题隐藏后缺少替代标签

**类别**: UX / A11y | **工作量**: Medium  
**文件**: `event-table.tsx:165`、`review-table.tsx:99`

列标题使用 `hidden md:grid`，移动端数据变成两列布局但没有列标签。用户在手机上看到的是没有上下文的裸数字。

**修复**: 在移动端添加行内标签，如 `<span className="text-xs text-muted-foreground md:hidden">概率</span>`。

### 2.10 筛选下拉缺少可见标签

**类别**: UX / A11y | **工作量**: Easy  
**文件**: `event-table.tsx:125-160`

三个并排的 `<select>` 有 `aria-label`（对屏幕阅读器友好），但没有可见的 `<label>` 或占位文本。视觉用户只能看到没有标签说明的下拉框。

### 2.11 浅色主题用户首次加载时的闪烁

**类别**: UX | **工作量**: Medium  
**文件**: `theme-control.tsx:17-26`、`layout.tsx:27-33`

主题通过 `useEffect` + `setTimeout(0)` 应用，导致深色主题先渲染，然后再切换到浅色（如果用户偏好浅色）。这会造成可见的闪烁。

**修复**: 在 `layout.tsx` 的 `<head>` 中内联一个阻塞脚本，在首次绘制前读取 `localStorage` 并应用 class。

### 2.12 客户端导航后滚动位置未重置

**类别**: UX | **工作量**: Easy  
**文件**: 所有页面组件

从 Dashboard 的滚动位置导航到事件详情页时，滚动位置可能保留。虽然 Next.js Link 默认 `scroll={true}`，但如果目标页面有异步渲染（Suspense），滚动重置可能不生效。

---

## 3. 低影响优化（Low Impact）

| 编号 | 类别 | 文件 | 问题 | 建议 |
|------|------|------|------|------|
| 3.1 | Code | 8+ 个文件 | 主按钮样式字符串重复拷贝粘贴 | 提取共享 `Button` 组件或样式常量 |
| 3.2 | Code | `page.tsx:94` | `discover()` 函数未用 `useCallback` 包裹 | 与其他 handler 保持一致 |
| 3.3 | UX | `recent-predictions.tsx:53` | 预测行显示 `event_id`（UUID）而非事件标题 | 从事件列表中解析标题或后端返回 |
| 3.4 | A11y | `decision-card.tsx:49` | 整个卡片是 `<Link>` 但缺少 `focus-visible` ring | 添加 `focus-visible:ring-2 focus-visible:ring-ring` |
| 3.5 | A11y | `movers-board.tsx:9` | MoverCard 同上，缺少键盘焦点指示 | 同上 |
| 3.6 | A11y | `event-table.tsx:183` | 表格行 Link 缺少 focus-visible ring | 同上 |
| 3.7 | A11y | `indicators.tsx:92-104` | SupportMeter 仅靠颜色区分状态 | 添加 `aria-label` 描述含义 |
| 3.8 | Code | `history/page.tsx:44,53-58` | "加载更多" 使用原始事件计数而非过滤后的已结算计数 | 统一使用 reviews 的 count |
| 3.9 | Code | `analyze/page.tsx:15`、`manual-resolve-panel.tsx:8` | `inputCls` 常量几乎相同但分别定义在两个文件中 | 提取到共享样式模块 |
| 3.10 | Perf | `format.ts:85-95` | `fmtDateTime` 每次调用创建新的 `Intl.DateTimeFormat` | 在模块级别缓存实例 |
| 3.11 | Code | `format.ts:97-107` | `relativeTime` 使用 `Date.now()` 是非确定性的，页面停留久了显示会过时 | 考虑定时器自动更新或改用绝对时间 |
| 3.12 | Code | `globals.css:84-150` | `@media (prefers-color-scheme: light)` 块与 `:root.light` 样式完全重复（约 30 行） | 添加注释说明是有意的（JS 运行前的初始绘制），或合并处理 |
| 3.13 | UX | 多个页面 | 缺少路由级 `error.tsx`（当前依赖 SectionErrorBoundary 组件级错误边界） | 为关键路由添加 `error.tsx` |
| 3.14 | A11y | 多个 Link 组件 | Link 卡片缺少非链接交互提示 | 确保所有可点击卡片有明确的焦点样式 |

---

## 4. 推荐修复路线

### 第一批：快速优化（2-3 小时，立竿见影）

| 项目 | 内容 |
|------|------|
| 1.1 | 为每个路由添加 `loading.tsx` 骨架屏 |
| 1.2 | Recharts 使用 `next/dynamic` 懒加载 |
| 2.1 | 导航添加 `aria-current="page"` |
| 2.3 | Dashboard `summarize()` 加 `useMemo` |
| 2.4 | Sparkline 加 `React.memo` |
| 2.5 | 截断标题添加 `title` 属性 |
| 2.7 | API 缓存添加过期清理 |
| 3.4-3.6 | 所有 Link 卡片添加 `focus-visible` ring |
| 3.10 | `fmtDateTime` 缓存 `Intl.DateTimeFormat` 实例 |

### 第二批：体验升级（4-6 小时）

| 项目 | 内容 |
|------|------|
| 1.3 | 加载状态替换为内容骨架屏 |
| 1.4 | 提取 `useAsyncData` 自定义 hook |
| 1.5 | 结算操作添加确认步骤 |
| 1.6 | 移动端导航响应式改造 |
| 2.2 | 添加 skip-to-content 链接 |
| 2.9 | 移动端表格添加行内标签 |
| 2.11 | 主题闪烁修复 |

### 第三批：工程治理（后续迭代）

| 项目 | 内容 |
|------|------|
| 3.1 | 提取共享 Button 组件 |
| 3.3 | RecentPredictions 显示事件标题 |
| 3.9 | 统一 inputCls 样式常量 |
| 3.11 | relativeTime 自动更新机制 |
| 2.8 | 清理死代码（EvidenceList 未使用导出） |

---

## 5. 当前做得好的方面

前端项目在以下方面表现出较高的工程成熟度：

**类型安全** — TypeScript strict 模式下零 `any`、零 `@ts-ignore`。`types.ts` 为所有 API 响应定义了完整的类型接口，`adapt.ts` 提供了干净的后端类型到视图模型的转换层。

**状态覆盖** — 所有异步页面都实现了 loading、error、empty 三种状态。`SectionErrorBoundary` 提供了组件级别的错误隔离，`error.tsx` 处理了路由级渲染错误。

**API 层设计** — `api.ts` 实现了 15 秒 TTL 缓存、inflight 请求去重、operator key 注入、超时处理和结构化错误解析。GET/POST 分离清晰。

**视觉系统** — Tailwind v4 CSS-first 配置，OKLCH 色彩空间，深色/浅色双主题。设计 token 统一，组件间视觉一致性高。

**可访问性基础** — 输入和 select 有 `aria-label`，装饰性图标有 `aria-hidden`，`reduced-motion` 媒体查询已处理动画。在此基础上补充上述 A11y 优化项即可接近 WCAG 2.1 AA 标准。
