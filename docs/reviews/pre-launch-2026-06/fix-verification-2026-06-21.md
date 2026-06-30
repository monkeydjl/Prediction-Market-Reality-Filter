# 修复复查报告 — 2026-06-21

## 复查范围

本次复查验证了自上次审查（pre-launch-review-2026-06-20.md 与 frontend-review-2026-06-20.md）以来所有修复的实现情况，涵盖后端 4 项 P1 问题、前端 4 项 Bug、6 项缺失模块、3 项改进建议。

---

## 后端修复验证（4/4 已修复）

### P1-1：校准反馈静默异常 ✅ 已修复

**原问题**：`calibration_feedback_service.py` 的 `_load_resolved_records()` 捕获异常后直接 `return []`，无任何日志，导致数据加载故障完全不可见。

**修复验证**：
- 文件 `backend/app/services/calibration_feedback_service.py`
- 已添加 `import logging` 和 `logger = logging.getLogger(__name__)`
- 异常捕获处新增 `logger.warning("Failed to load resolved records for calibration feedback", exc_info=True)`
- 确保故障可见，运维可及时察觉

### P1-2：备份无限积累 ✅ 已修复

**原问题**：`backup_stores.py` 每次运行创建备份但无轮转机制，长期运行将耗尽磁盘。

**修复验证**：
- 文件 `backend/scripts/backup_stores.py`
- 新增 `_prune_backups(backup_dir, keep)` 函数，按文件名倒序保留最近 N 个备份
- `create_backup()` 新增 `keep: int | None = 30` 参数，默认保留 30 份
- CLI 新增 `--keep` 参数，支持自定义保留数量
- 测试 `test_backup_prunes_old_archives_only` 验证轮转逻辑
- `docs/ops/RUNBOOK.md` 同步更新 `--keep N` 用法

### P1-3：API Key 前缀泄露 ✅ 已修复

**原问题**：`main.py` 启动日志打印 `OPENAI_API_KEY[:3]`，泄露密钥前缀。

**修复验证**：
- 文件 `backend/app/main.py` L26
- 已改为 `logger.info("OPENAI_API_KEY is configured (len=%d)", len(settings.OPENAI_API_KEY))`
- 仅记录长度，不泄露任何密钥内容

### P1-4：备份恢复指引缺失 ✅ 已修复

**原问题**：运维文档缺少备份恢复后的必要操作步骤。

**修复验证**：
- 文件 `docs/ops/RUNBOOK.md`
- 已添加恢复后指引："Run `reconcile_predictions()` after restore to heal zombie predictions"
- 确保恢复操作闭环

---

## 前端修复验证

### Bug 修复（4/4 已修复）

#### B1：历史页 edge 类型缺失 ✅ 已修复

**原问题**：`eventsApi.history()` 返回类型缺少 `edge` 字段，导致边缘轨迹数据无法正确解析。

**修复验证**：
- 文件 `frontend/src/lib/api.ts`
- `history()` 返回类型新增 `edge?: EdgeTrajectory`
- 确保边缘轨迹数据可正确传递

#### B2：分析页缺少成交量/流动性字段 ✅ 已修复

**原问题**：分析页未提供成交量和流动性输入，导致后端无法获取完整市场数据。

**修复验证**：
- 文件 `frontend/src/app/analyze/page.tsx`
- 新增 `volume` 和 `liquidity` state 变量与输入框
- 输入验证：非负数字
- `submit()` 传递 `volume: volumeValue` 和 `liquidity: liquidityValue` 到 API

#### B3：422 错误显示原始 JSON ✅ 已修复

**原问题**：FastAPI 422 验证错误直接显示原始 JSON body，用户体验差。

**修复验证**：
- 文件 `frontend/src/lib/api.ts`
- 新增 `buildApiErrorMessage(status, bodyText)` 函数
- 结构化解析 FastAPI 422 验证错误，提取 `detail[].msg` 并用中文分号连接
- 映射常见 HTTP 状态码到中文提示（401→"未获授权"，503→"服务器暂时不可用"等）
- 测试 `api.test.ts` 验证 422/401/503/纯文本场景

#### B4：证据类型过窄 ✅ 已修复

**原问题**：`EventRecord.evidence` 类型仅包含 `direction`，无法承载后端返回的完整证据聚合数据。

**修复验证**：
- 文件 `frontend/src/lib/types.ts`
- 新增 `EvidenceAggregate` 接口：`direction, strength, conflict, freshness, resolution_relevance, source_count`
- `EventRecord.evidence` 类型改为 `EvidenceAggregate`
- `signal-panel.tsx` 移除类型断言，直接使用 `EvidenceAggregate`

### 缺失模块（6/6 已补齐）

#### M1：系统状态面板（P1） ✅ 已实现

**新文件**：`frontend/src/components/dashboard/system-status.tsx`
- 显示调度器运行状态、最近任务时间、关键计数（事件/已结算/待审链接/校准样本）
- 失败任务高亮显示
- 手动刷新按钮
- 已集成到监控面板 `page.tsx`

#### M2：手动结算面板（P1） ✅ 已实现

**新文件**：`frontend/src/components/detail/manual-resolve-panel.tsx`
- 表单输入：实际结果（0-100）、置信度（0-1）、备注
- 输入验证：范围检查、必填项
- 已结算事件显示结算结果
- 已集成到事件详情页 `events/page.tsx`

#### M3：事件搜索 ✅ 已实现

**修改文件**：`frontend/src/components/dashboard/event-table.tsx`
- 新增搜索输入框（Search 图标）
- 过滤逻辑：`${title} ${description} ${categoryLabel}` 全文匹配
- 实时过滤，无需回车

#### M4：待审链接审查 ✅ 已实现

**新文件**：`frontend/src/components/history/pending-links.tsx`
- 显示待人工确认的市场链接列表
- 每条显示市场问题、置信度、关联时间
- "确认关联"按钮调用 `eventsApi.verifyLink()`
- 确认后从列表移除
- 已集成到历史复盘页

#### M5：预测记录视图 ✅ 已实现

**新文件**：`frontend/src/components/history/recent-predictions.tsx`
- 显示最近 50 条冻结预测
- 表格列：事件 ID、平台、时间、AI 概率、市场概率、原始边缘、Brier 分数
- 已集成到历史复盘页

#### M6：事件决策报告 ✅ 已实现

**新文件**：`frontend/src/components/detail/decision-report-panel.tsx`
- 调用 `eventsApi.decision(eventId)` 获取决策报告
- 使用 `DecisionCard` 组件渲染
- 加载中/错误/空状态处理
- 已集成到事件详情页

### 改进建议（3/3 已落实）

#### R1：API 超时控制 ✅ 已实现

**修改文件**：`frontend/src/lib/api.ts`
- `api<T>()` 新增 `options: { timeoutMs?: number }` 参数
- 默认 60 秒，discover 300 秒，analyze/resolveAuto 180 秒
- 使用 `AbortController` 实现超时中断
- 网络错误单独捕获："无法连接到服务器"

#### R2：操作员 API Key 控制 ✅ 已实现

**新文件**：`frontend/src/components/operator-key-control.tsx`
- 导航栏右侧显示授权状态按钮
- 点击展开密码输入框
- 使用 `sessionStorage` 存储（`getOperatorApiKey()`/`setOperatorApiKey()`）
- 已集成到 `app-nav.tsx`

#### R3：前端测试 ✅ 已实现

**新文件**：
- `frontend/src/lib/adapt.test.ts`：4 个测试（adaptRecord 优先级/回退、trendOf 死区、sparkSeries 过滤）
- `frontend/src/lib/api.test.ts`：3 个测试（422 解析、状态码映射、纯文本详情）
- 全部 7 个测试通过 vitest

---

## 额外改进（未在原始审查中提及）

### 全局错误边界 ✅

**新文件**：`frontend/src/app/error.tsx`
- Next.js 错误边界组件
- 显示错误信息、重试按钮
- 提升用户体验

### 全局加载骨架 ✅

**新文件**：`frontend/src/app/loading.tsx`
- Next.js loading.tsx 约定
- 脉冲动画骨架屏
- 减少白屏时间

### 404 页面 ✅

**新文件**：`frontend/src/app/not-found.tsx`
- 友好提示"页面不存在"
- 返回监控面板按钮

### 跟踪决策竞态修复 ✅

**修改文件**：`frontend/src/components/detail/tracking-decision.tsx`
- 新增 `saveSeq` ref 追踪保存序列号
- 防止快速连续点击导致状态回滚错乱
- 仅最后一次保存失败时才回滚状态

### Sparkline 无障碍改进 ✅

**修改文件**：`frontend/src/components/sparkline.tsx`
- 新增 `aria-label` 描述趋势方向和数值范围
- 添加 `<title>` 和 `<desc>` 标签
- 空数据时提供 `aria-label` 提示

---

## 测试验证结果

### 后端测试

```
509 passed, 11 skipped, 0 failed, 0 errors
Runtime: 40.32 seconds
```

- 所有单元测试通过
- 11 个跳过：`test_integration_live.py` 需外部 API（opt-in）
- 无回归

### 前端测试

```
TypeScript: 0 errors
Next.js build: SUCCESS (8 static pages)
Vitest: 7 tests passed (375ms)
```

- 类型检查通过
- 生产构建成功
- 单元测试全部通过

---

## 代码质量审查

### 新增组件质量评估

所有新增组件（system-status、manual-resolve-panel、decision-report-panel、pending-links、recent-predictions、operator-key-control）均符合以下标准：

1. **状态管理**：正确使用 `useState`/`useEffect`，避免不必要的重渲染
2. **错误处理**：统一 try/catch + 用户友好提示
3. **加载状态**：spinner/skeleton 反馈
4. **类型安全**：TypeScript strict 模式，无 `any` 类型
5. **样式一致性**：使用 Tailwind CSS tokens，符合设计系统
6. **无障碍**：`aria-hidden`、`aria-label`、语义化标签

### 潜在改进点（非阻塞）

1. **system-status.tsx L36**：`useEffect` 依赖数组为空但 `load` 函数未列入。当前设计为仅挂载时加载一次，符合预期，但若未来需响应状态变化需调整。

2. **operator-key-control.tsx**：使用 `sessionStorage` 存储 API Key，关闭标签页后丢失。当前为客户端工具可接受，若需持久化可改用 `localStorage`。

3. **pending-links.tsx L39**：`setLinks((items) => items.filter((item) => item !== link))` 依赖引用相等性。当前数据来自 state 数组引用，工作正常；若未来数据源变化需改用 ID 匹配。

---

## 总结

**修复完成率：100%**

- 后端 P1：4/4 ✅
- 前端 Bug：4/4 ✅
- 前端缺失模块：6/6 ✅
- 前端改进建议：3/3 ✅
- 额外改进：5 项

**测试状态：全部通过**

- 后端：509 passed
- 前端：7 tests + TypeScript + build

**结论**：所有上次审查发现的问题均已正确修复，代码质量良好，测试覆盖充分。系统已具备上线条件。
