## 前端系统审查报告 — Prediction Market Reality Filter v0.3.0

**审查日期**: 2026-06-20  
**技术栈**: Next.js 16.2.9, React 19.2.4, Tailwind v4 (CSS-first), TypeScript 5, recharts  
**源文件**: 37 个 (5 页面 + 1 error boundary + 18 组件 + 5 lib + 1 CSS)  
**测试覆盖**: 0（无前端测试）

---

### 一、需要修复的 Bug

#### B1. `history()` API 返回类型缺失 `edge` 字段 — 高优先级

`api.ts` L156-158 定义 history 的返回类型为：

```typescript
{ history: HistorySnapshot[]; trend?: Trend; count?: number }
```

但后端 `GET /events/{id}/history` 实际返回：

```python
{"event_id": ..., "count": ..., "trend": ..., "edge": ..., "history": [...]}
```

`edge` 字段（类型为 `EdgeTrajectory`，已在 `api.ts` L89-99 定义）被 TypeScript 类型丢弃了。这意味着事件详情页虽然已经拿到了 edge trajectory 数据，但因为类型缺失而无法在 TypeScript 中使用它，只能额外调用 `freshEdges()` 重新获取。

**修复**：将 `history()` 返回类型改为 `{ history: HistorySnapshot[]; trend?: Trend; edge?: EdgeTrajectory; count?: number }`。

#### B2. `analyze()` 未转发 `volume` / `liquidity` — 高优先级

后端 `EventAnalysisRequest` 接受 5 个字段（event_question, baseline_probability, news_context, volume, liquidity），但前端 analyze 表单只发送前 3 个。volume 和 liquidity 始终为 None，导致手动分析的事件在 priced-in risk 评分和可信度计算中始终缺失这两项输入，分析质量低于自动发现的事件。

**修复**：在 analyze 页面表单中增加 volume 和 liquidity 的可选输入字段，并在 `api.ts` 的 `analyze()` 请求体中转发。

#### B3. 422 验证错误显示原始 JSON — 中优先级

`api.ts` L29-30 对 400 状态码直接返回 body text，但 FastAPI 的 422 验证错误（如 `setTracking` 的无效 status 值）返回的 JSON 字符串 `{"detail":[{"loc":["body","status"],"msg":...}]}` 会被原样展示给用户。

**修复**：在 `buildApiErrorMessage` 中对 400/422 尝试 `JSON.parse`，提取 `detail` 字段的文本。

#### B4. `evidence` 类型定义过窄 — 低优先级

`types.ts` 中 `EventRecord.evidence` 仅声明了 `{ direction?: string }`，但后端 `build_event_record` 实际返回 `{ direction, strength, conflict, freshness, resolution_relevance, source_count }`。`signal-panel.tsx` 用 `as` 断言绕过了这个问题（L49-55），但类型契约不正确。

**修复**：扩展 `EventRecord.evidence` 类型以包含完整字段。

---

### 二、缺失的功能模块

后端已提供 19 个 API 端点，前端只使用了 13 个。以下 6 个后端能力在前端完全没有界面：

#### M1. 系统运行状态面板 — 强烈建议增加

**缺失端点**: `GET /events/loop/status`

用户在 Dashboard 上看不到调度器是否在运行、上次 discover/auto-resolve 是什么时候执行的、是否失败了。这是运维可见性最关键的缺失。建议在 Dashboard 顶部或侧边增加一个紧凑的状态指示器：

- 调度器运行状态（running/stopped）
- 上次 discover 时间与结果
- 上次 auto-resolve 时间与结果
- 未结算事件数量

数据来源就是 `/api/events/loop/status`，已经存在。

#### M2. 手动事件结算 — 强烈建议增加

**缺失端点**: `POST /events/{event_id}/resolve`

用户无法在事件详情页手动标记某个事件的结果（YES/NO）。当前唯一的结算路径是 `resolveAuto()`（在历史页批量触发），无法对单个事件进行人工覆盖。

建议在事件详情页增加一个"手动结算"面板（仅当事件未 resolved 时显示），包含 outcome 输入（0-100）、置信度、备注。

#### M3. 待审链接队列 — 建议增加

**缺失端点**: `GET /events/links/pending` + `POST /events/{event_id}/link/verify`

系统的 fail-closed 门控会把模糊匹配的 event-market 链接标记为 pending（不自动结算），但前端没有界面让用户审核和确认这些链接。这意味着一批潜在可结算事件可能永远停在 pending 状态。

建议增加一个"待审链接"页面或在历史页增加一个 tab，展示 pending 列表并允许用户点击"确认关联"。

#### M4. 预测记录视图 — 建议增加

**缺失端点**: `GET /events/predictions/recent`

系统已冻结的预测（AI 估计 vs 市场价格）没有前端展示界面。这是反馈闭环的核心产出物，用户应该能看到系统做了哪些具体预测。

建议增加一个"预测记录"页面或作为历史页的子 tab，展示每条预测的冻结时间、AI 概率、市场概率、原始 edge、当前状态。

#### M5. 单事件决策报告 — 可选

**缺失端点**: `GET /events/{event_id}/decision`

当前决策页只展示批量列表（`decisions/open`），无法深度链接到单个事件的决策报告。建议事件详情页增加一个"决策分析"面板，直接调用此端点。

#### M6. 事件搜索 — 建议增加

当前事件表只支持领域/状态下拉筛选，没有文本搜索。当事件数量超过 20 个时，用户需要逐个翻阅列表找到特定事件。建议在事件表顶部增加搜索框，按标题（中文/英文）过滤。

---

### 三、健壮性问题

#### R1. 绝大多数 API 调用无超时

只有 `discover()` 使用了 `AbortSignal`（5 分钟超时）。其他可能触发 LLM 调用的端点（如 `analyze`）没有客户端超时。如果后端卡住，浏览器会一直等待。

**建议**：在 `api<T>()` 函数中增加默认 60 秒超时，长操作（discover/analyze）可以覆盖。

#### R2. 无分页机制

Dashboard 加载 100 条、历史页加载 200 条、决策页加载 50 条。随着系统运行时间增长，这些硬编码上限会成为瓶颈：

- 100 条之后的事件在 Dashboard 不可见
- 200 条之后的已结算事件在历史页不可见

后端端点已支持 `limit` 参数（最大 200），但前端没有 offset/page 参数。短期内事件数量不太可能突破上限，但建议在事件表增加"加载更多"按钮或简单分页。

#### R3. 无前端测试

41 个后端测试文件，508 个测试用例。前端：0。

建议至少增加：
- `adapt.ts` 的单元测试（纯函数，无依赖）
- `api.ts` 的 mock fetch 测试
- `tracking-decision.tsx` 的组件测试（乐观更新 + 回滚逻辑）

#### R4. 事件详情页使用 query param 而非动态路由

`/events?id=xxx` 而非 `/events/[id]`。这导致：
- 无法利用 Next.js 的 `notFound()` 处理
- 深度链接依赖 query string
- 不利于 SEO（虽然本项目是静态导出，影响有限）

当前设计可工作，但如果后续想优化可以考虑迁移。

---

### 四、现有代码质量评价

#### 做得好的部分

**设计系统成熟度高**。OKLCH 色彩空间、语义化 token、 Geist 字体、暗色主题统一。`globals.css` 的设计 token 体系完整且一致，组件可以直接引用 `var(--pos)`、`var(--neg)`、`var(--chart-1)` 等语义变量，视觉一致性很好。

**错误边界完整**。每个页面都有 loading/error/empty 三态处理。`error.tsx` 全局错误边界也有。Dashboard 的 discover 操作有专门的超时处理和中止逻辑。

**乐观更新 + 回滚**。`tracking-decision.tsx` 的 save 函数实现了乐观 UI（先更新界面，失败后回滚），并用 `saveSeq` 引用避免竞态条件。这是前端状态管理的高质量示例。

**组件隔离良好**。每个组件都有明确的 props 契约，没有全局状态依赖。适配层（`adapt.ts`）干净地将后端数据转换为视图模型，组件不直接读取后端原始结构。

**渐进增强**。交叉验证、语义相关性、校准反馈等功能在后端都是 opt-in 的，前端也相应地在数据缺失时显示"暂无数据"占位而非崩溃。

#### 可改进的部分

**`signal-panel.tsx` 的类型断言**。L49-55 用 `as` 强制将 `ev` 断言为一个更大的类型。这是因为 `EventRecord.evidence` 的类型定义过窄（B4）。应该修复类型定义而非用断言绕过。

**`decisions/page.tsx` 的双重加载**。L46-59 的 useEffect 和 L34-43 的 refresh 函数有重叠的 loading 状态管理。useEffect 中有自己的 `setLoading(true)` 和 try/catch，refresh 也有。可以统一为一个 `load` 函数。

**无 `loading.tsx` 文件**。Next.js 14+ 支持 `loading.tsx` 做路由级 Suspense fallback，当前所有页面都是手动管理 loading state。对静态导出场景影响不大，但加一个全局 `loading.tsx` 可以减少重复代码。

---

### 五、优先级排序与工作量估计

| 优先级 | 项目 | 工作量 | 影响面 |
|--------|------|--------|--------|
| **P0** | B1 — history 类型补 edge 字段 | 1 行改动 | 修复类型安全 |
| **P0** | B3 — 422 错误消息解析 | ~10 行 | 改善用户体验 |
| **P1** | M1 — 系统状态面板 | 新组件 + API 调用，~100 行 | 运维可见性 |
| **P1** | B2 — analyze 表单补 volume/liquidity | ~20 行 | 分析质量 |
| **P1** | M2 — 手动结算面板 | 新组件 + API 调用，~80 行 | 操作灵活性 |
| **P1** | R1 — API 客户端默认超时 | ~15 行 | 健壮性 |
| **P2** | M6 — 事件搜索框 | ~40 行 | 可用性 |
| **P2** | M3 — 待审链接队列 | 新页面 + 2 API 调用，~150 行 | 数据完整性 |
| **P2** | B4 — evidence 类型扩展 | ~10 行 | 类型安全 |
| **P2** | M4 — 预测记录页面 | 新页面，~120 行 | 反馈闭环可视化 |
| **P3** | R2 — 分页机制 | ~60 行 | 可扩展性 |
| **P3** | R3 — 前端测试 | ~200 行 | 回归安全 |
| **P3** | M5 — 单事件决策面板 | ~60 行 | 深度分析 |

**P0 + P1 全部完成约需 3-4 小时**，可显著提升系统的前端可用性和健壮性。
