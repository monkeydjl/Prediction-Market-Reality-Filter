# Probability Watch 前端第二轮审查报告

**审查日期**: 2025-06-21  
**对比基线**: 第一轮审计 (2025-06-20)  
**审查方式**: 逐项验证 + 新增代码审查

---

## 一、逐项修复验证

### 🔴 P0 严重问题 (3项)

| # | 问题 | 第一轮 | 第二轮 | 证据 |
|---|------|--------|--------|------|
| 1 | 完全缺失测试基础设施 | ❌ | ✅ **已修复** | `vitest` 已添加为 devDependency；`package.json` 新增 `"test": "vitest run"`；编写了 2 个测试文件共 7 个用例，全部通过 |
| 2 | `api.ts` Content-Type 头被覆盖 | ❌ | ✅ **已修复** | 改用 `new Headers(init?.headers)` 先捕获传入 headers，再按条件设置 Content-Type（仅当 body 存在且 header 未设置时） |
| 3 | `api.ts` 缺网络层错误处理 | ❌ | ✅ **已修复** | 新增 `TypeError` 捕获 → "无法连接到服务器"；新增 `AbortError` 捕获 → "请求超时"；默认 60s 超时 |

### 🟡 P1 中等问题 (5项)

| # | 问题 | 第一轮 | 第二轮 | 证据 |
|---|------|--------|--------|------|
| 4 | 事件列表无分页 | ❌ | ❌ **未修复** | 仍一次性加载 100/200 条数据 |
| 5 | 无数据缓存层 | ❌ | ❌ **未修复** | 未引入 SWR/TanStack Query |
| 6 | `analyze` 空值校验逻辑缺陷 | ❌ | ✅ **已修复** | 先检查 `baseline.trim() === ""`，再检查 `Number.isFinite()`，再检查范围 0-100；同时补全了 volume/liquidity 输入验证 |
| 7 | decisions 页重复 loading 管理 | ❌ | ⚠️ **设计确认** | `load()` 定位为纯数据获取器，调用方管理 UI 状态 — 这是有意的职责分离，非 bug |
| 8 | 缺少 `not-found.tsx` | ❌ | ✅ **已修复** | 创建 `src/app/not-found.tsx`，复用 AppNav + 品牌风格警告卡片 + 返回链接 |

### 🔵 P2 低优先级 (4项)

| # | 问题 | 第一轮 | 第二轮 | 证据 |
|---|------|--------|--------|------|
| 9 | 无组件级 ErrorBoundary | ❌ | ❌ **未修复** | 仍仅依赖全局 `error.tsx` |
| 10 | Sparkline SVG 缺无障碍描述 | ❌ | ❌ **未修复** | SVG 仍标记 `aria-hidden="true"` 无 title/desc |
| 11 | 无亮色模式支持 | ❌ | ❌ **未修复** | `:root` 直接定义暗色值 |
| 12 | 无环境变量校验 | ❌ | ❌ **未修复** | 无构建时校验 |

---

## 二、修复率统计

| 严重度 | 第一轮问题数 | 已修复 | 未修复 | 修复率 |
|--------|-------------|--------|--------|--------|
| 🔴 P0 | 3 | 3 | 0 | **100%** |
| 🟡 P1 | 5 | 2 | 2 + 1⚠️ | **50%** |
| 🔵 P2 | 4 | 0 | 4 | **0%** |
| **合计** | **12** | **5** | **6 + 1⚠️** | **46%** |

> P0 清零是最大亮点；P1 还剩分页和缓存两个性能项目未修。

---

## 三、新增代码审查

### 🟢 新增模块 (8个)

| 模块 | 文件 | 说明 |
|------|------|------|
| 系统状态面板 | `components/dashboard/system-status.tsx` | 显示循环调度器状态、任务计数、最近运行时间 |
| Operator Key 控件 | `components/operator-key-control.tsx` | 导航栏内嵌 API Key 配置，sessionStorage 持久化 |
| 手动结算面板 | `components/detail/manual-resolve-panel.tsx` | 事件详情页手动结算表单（实际结果 + 置信度 + 备注） |
| 决策报告面板 | `components/detail/decision-report-panel.tsx` | 事件详情页决策分析模块 |
| 待审市场链接 | `components/history/pending-links.tsx` | 待确认市场链接列表 + 一键确认 |
| 最近预测记录 | `components/history/recent-predictions.tsx` | 最近 50 条预测记录明细表 |
| 全局 Loading 骨架 | `app/loading.tsx` | 替代纯文本"加载中…"，含脉冲动画骨架 |
| 404 页面 | `app/not-found.tsx` | 品牌风格 404 页面 |

### 🟢 API 层增强

- 新增 `X-API-Key` 操作员认证头自动注入
- 新增端点: `loopStatus`, `pendingLinks`, `verifyLink`, `recentPredictions`, `resolveManual`, `decision`
- 改进的错误解析: 支持 FastAPI 422 validation errors 的 `detail[]` 数组格式
- 新增 `timeoutMs` 选项，默认 60s，discover/analyze/resolve 按需延长

### 🟢 测试覆盖

| 文件 | 用例数 | 覆盖范围 |
|------|--------|---------|
| `lib/api.test.ts` | 3 | `buildApiErrorMessage` — 422 validation errors, auth/server errors, plain text |
| `lib/adapt.test.ts` | 4 | `adaptRecord` 完整字段和缺字段回退, `trendOf` 死区, `sparkSeries` 过滤 |

运行结果: **7/7 通过** ✅

### 🟡 发现的新增问题

#### 1. 缺少 `vitest.config.ts`（轻微）
- **问题**: Vitest 仅依赖零配置运行，无显式配置文件
- **影响**: 无法为 @/\* 路径别名等自定义配置
- **建议**: `frontend/vitest.config.ts` 添加路径别名解析

#### 2. `system-status.tsx` 无卸载保护（轻微）
- **问题**: `useEffect` 中用 `setTimeout(load, 0)` 延迟执行，但未用 `cancelled` 标志
- **影响**: React 19 已解决 setState after unmount 警告，实际无副作用
- **建议**: 技术上无害，但保持与其他组件风格一致可加分

#### 3. `history/page.tsx` — `runResolve` 数据刷新性能（注意）
- **问题**: 结算成功后调用 `loadData()` 重新加载全部 200 条事件 + 校准数据
- **影响**: 在事件总量增长后会有明显延迟
- **建议**: 可以只刷新校准数据（API 已有独立端点），或添加后台刷新不阻塞 UI

#### 4. 测试覆盖仍不充分（注意）
- **问题**: 7 个用例仅覆盖 `api.ts` 的错误消息构建和 `adapt.ts` 的数据适配
- **缺失**: 组件测试（SummaryBar, EventTable, MoversBoard 等核心 UI 组件全无覆盖）
- **建议**: 下一步增加至少 3 个组件的渲染测试

---

## 四、前后对比总结

| 维度 | 第一轮评分 | 第二轮评分 | 变化 |
|------|-----------|-----------|------|
| 类型安全 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | — |
| 错误处理 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | +1 (网络超时全覆盖) |
| API 层设计 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | +1 (Headers 隔离/Auth/422解析) |
| 组件复用 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | +1 (8 个新模块风格一致) |
| 无障碍 | ⭐⭐⭐ | ⭐⭐⭐ | — |
| 性能优化 | ⭐⭐⭐ | ⭐⭐⭐ | — |
| 可测试性 | ⭐ | ⭐⭐⭐ | +2 (框架 + 7 个用例) |
| 功能完整性 | ⭐⭐⭐ | ⭐⭐⭐⭐ | +1 (系统状态/手动结算/市场链接) |
| 代码文档 | ⭐⭐ | ⭐⭐ | — |

**总体评分**: 3.3/5 → **3.8/5** (+0.5)

---

## 五、下一阶段建议

### 立即修复（1-2h）
1. 添加 `vitest.config.ts` 配置路径别名

### 短期（1-3天）
2. 引入 TanStack Query 解决数据缓存和去重（同时解决 P1 #5）
3. 为 3 个核心组件添加渲染测试
4. 事件列表虚拟滚动/分页（解决 P1 #4）

### 中期（1-2周）
5. 组件级 ErrorBoundary
6. 亮色主题支持
7. Sparkline SVG 无障碍增强

---

*审查人: Frontend Developer Agent*  
*基于对 41 个源文件的对比审查，包括 12 个新增/修改文件*
