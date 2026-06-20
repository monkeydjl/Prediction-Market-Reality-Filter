# M5 优化后代码复审意见

日期：2026-06-18

## 结论

这轮优化明显修掉了上一版最关键的 M5 问题：预测层校准已经从“所有已结算冻结预测”收窄为“仅 act 决策样本”，watch / skip 结算后只进入 observed，不再污染 headline calibration。M5 的前端入口也已经接上：新增 `/decisions` 页面、决策卡片、历史页预测层校准卡片，以及对应的前端 API wrapper。

当前代码可以继续推进，但仍不建议视为 M5 完全收口。主要剩余问题有一个确定的前端导航 bug，一个需要明确写进产品/指标文档的统计口径差异，以及 M3/M4 遗留结构债。

本次没有修改业务代码，只做代码审查并新增本文档。

## 本次验证

已执行：

```text
cd frontend
npm.cmd run build
```

结果：通过。Next.js 生产构建成功，TypeScript 阶段通过。构建输出路由包含 `/decisions`、`/events`、`/history`，不包含 `/events/[id]`。

已执行：

```text
cd backend
python -m unittest tests.test_prediction_store tests.test_event_resolve_service
```

结果：通过。共 42 个测试 OK。

补充说明：从仓库根目录直接执行 `python -m unittest backend.tests...` 会因为 `app` 包路径不在 Python import path 中失败；切到 `backend` 目录执行后通过。这是运行姿势问题，不是本轮业务断言失败。

## 已修复的问题

### 1. act-only scoring 已经落地

位置：

- `backend/app/memory/prediction_store.py`
- `backend/tests/test_prediction_store.py`
- `backend/tests/test_event_resolve_service.py`

当前 `score_prediction()` 的核心逻辑是：

```python
new_status = "scored" if row["decision"] == "act" else "observed"
```

这符合之前审查意见里的核心要求：系统只把真正建议行动的预测纳入预测层校准。watch / skip 仍然记录 outcome 和 brier，用于诊断，但不会进入 scored 样本。

测试覆盖也补上了：

- act row 结算后进入 `scored`
- watch row 结算后进入 `observed`
- watch / skip 不进入 `calibration_summary()["n"]`
- event resolve 路径调用 `score_prediction()` 后仍保持 act-only 口径

这个修复是 M5 反馈闭环能否可信的关键点，目前实现方向是正确的。

### 2. headline prediction calibration 已经显式过滤 act

位置：

- `backend/app/memory/prediction_store.py`
- `frontend/src/components/history/prediction-calibration.tsx`

`calibration_summary()` 的 overall、by_category、realized edge 查询都显式使用：

```sql
WHERE status='scored' AND decision='act'
```

这比只依赖 `status='scored'` 更稳，因为它把统计口径直接写进查询条件，后续即使有人改动状态流转，也不容易把 watch / skip 意外混进来。

历史页新增的 `PredictionCalibrationCard` 文案也明确说明这是“仅建议行动”的预测层校准，并与事件层校准区分开。这一点比上一版清晰很多。

### 3. M5 机会面前端已接入

位置：

- `frontend/src/lib/api.ts`
- `frontend/src/app/decisions/page.tsx`
- `frontend/src/components/decisions/decision-card.tsx`
- `frontend/src/components/app-nav.tsx`

前端已经补齐：

- `eventsApi.openDecisions()`
- `eventsApi.freshEdges()`
- `eventsApi.predictionCalibration()`
- `/decisions` 页面
- 导航入口
- 决策卡片展示 adjusted edge、raw edge、trust、类别、市场概率、模型估计、平台、风险 flags

这解决了上一版“后端有 M5 API，但用户看不到 M5 结果”的问题。

## 仍需修复的问题

### P1：DecisionCard 跳转详情页的路由是错的

位置：

- `frontend/src/components/decisions/decision-card.tsx`
- `frontend/src/app/events/page.tsx`

`DecisionCard` 当前链接：

```tsx
href={`/events/${encodeURIComponent(report.event_id)}`}
```

但实际详情页只有：

```text
frontend/src/app/events/page.tsx
```

并且详情页通过 `useSearchParams()` 读取：

```tsx
const id = params.get("id");
```

其他相似事件链接也是：

```tsx
href={`/events?id=${encodeURIComponent(s.event_id)}`}
```

同时前端 build 输出只列出 `/events`，没有 `/events/[id]`。因此 `/decisions` 页面卡片点击后大概率会进入 404 或找不到详情页。

建议修复为：

```tsx
href={`/events?id=${encodeURIComponent(report.event_id)}`}
```

这个问题不影响构建，但会直接破坏新 M5 页面最重要的用户路径，优先级应高于其他细节优化。

### P2：segment_skill 与 headline calibration 的统计口径不同，需要被正式写进设计文档

位置：

- `backend/app/memory/prediction_store.py`
- `backend/tests/test_prediction_store.py`

当前实现：

- `calibration_summary()`：只统计 `status='scored' AND decision='act'`
- `segment_skill(category)`：统计 `status != 'open' AND decision IN ('act', 'watch')`，排除 skip

这不是代码 bug。当前 docstring 已解释理由：如果 trust qualification 也只看 act，那么新类别会因为没有 act 历史而永远无法 bootstrap；watch 样本用于让类别脱离 dormant，skip 则排除，避免“与市场一致的简单预测”抬高信任度。

这个设计是合理的，但它和上一轮审查里“segment_skill 也严格 act-only”的建议不同。现在需要在 `V2_ROADMAP` / `DATABASE_DESIGN` / 用户说明中明确写成双口径：

- 预测层成绩单：act-only，只回答“我们真正建议行动时是否跑赢市场”
- 类别信任资格：act + watch，排除 skip，用于 cold-start 和判断某类别是否足够可信

如果不写清楚，后续维护者很容易把这段改回严格 act-only，导致类别永远难以从 dormant 中启动；或者反过来把 watch 误解为也参与 headline calibration。

### P2：M3 的 prediction ledger 仍不是严格 append-only 多快照结构

位置：

- `backend/app/memory/prediction_store.py`

当前 schema 仍然是：

```sql
event_id TEXT NOT NULL UNIQUE
```

`freeze_prediction()` 使用：

```sql
ON CONFLICT(event_id) DO NOTHING
```

所以一个 event 仍只能有一条 frozen prediction。代码注释里写“append-only”，但实际是“每个 event 首次冻结后不再覆盖”。这比会覆盖旧预测要好，但还不是严格意义上的多时间点 prediction ledger。

如果产品定义只需要“每个事件第一次进入市场信号时冻结一次”，当前实现可以接受；如果目标是 M3 文档里更完整的“每次重新扫描都保留一条 point-in-time prediction”，那还需要改 schema：

- 去掉 `event_id UNIQUE`
- 增加 `prediction_id` / `snapshot_at`
- resolution 时选择需要结算的 committed prediction 集合
- calibration 按 prediction rows 而不是 event rows 计算

这属于结构债，不建议混在 P1 路由修复里做。

### P2：M4 evidence factor validation 仍没有形成独立验证闭环

当前 M5 的预测层校准已经更干净，但证据因子层面仍没有看到独立的因子验证框架，例如：

- 每个 evidence factor 的冻结值
- 因子与 outcome 的相关性 / Brier 分层
- conflict、source quality、resolution relevance 等因子的单独回测
- 因子权重的可解释调参依据

也就是说，系统现在能回答“act 决策整体是否有效”，但还不能回答“哪些证据因子真正提升了预测”。如果后续要继续优化模型，不应只看最终 prediction calibration，否则很难定位错误来自市场数据、证据抽取、概率估计、还是 diagnosis gate。

### P3：open decisions 的 API 参数缺少枚举校验

位置：

- `backend/app/api/routes/events.py`

`/events/decisions/open` 接收：

```python
decision: str | None = Query(default=None)
```

然后直接传给：

```python
decisions = (decision,) if decision else ("act", "watch")
```

如果调用方传 `decision=bad_value`，接口不会 422，而是返回空列表。这不算严重错误，但对调试不友好。建议把参数限制为 `act | watch`，或者在后端显式校验并返回 422。

### P3：前端机会列表没有显示 dormant / qualification 的关键原因

位置：

- `frontend/src/app/decisions/page.tsx`
- `frontend/src/components/decisions/decision-card.tsx`
- `backend/app/services/decision_report_service.py`

页面文案提到“只有在某类别积累足够已结算预测后才会出现建议行动”，但单个卡片没有展示：

- 当前类别样本数 `segment_n`
- 当前类别 skill / trust 来源
- dormant / not qualified 的原因
- liquidity factor 如何影响 adjusted edge

结果是用户能看到 trust 和 adjusted edge，但不容易判断“为什么这是 watch 而不是 act”。这会影响人工复核效率。建议后续把 diagnosis 的关键解释字段进入 `DecisionReport`，而不是只暴露最终 trust 数值。

## 测试覆盖评价

后端相关测试这轮是明显增强的，尤其是：

- `test_watch_row_scores_to_observed`
- `test_calibration_excludes_watch_and_skip`
- `test_segment_skill_counts_watch_excludes_skip`
- `test_realized_edge_and_hit_rate`
- event resolve 中 watch -> observed 的集成路径

这些测试能防止上一版最危险的 calibration pollution 回归。

前端目前主要依赖 `next build` 类型检查。它能发现语法和类型问题，但不能发现链接目标是否符合产品路由约定，所以 DecisionCard 的错误没有被 build 捕获。建议增加最小化前端测试或至少 lint/route convention 检查：

- `/decisions` 页面渲染时卡片链接应为 `/events?id=...`
- `eventsApi.openDecisions()`、`freshEdges()`、`predictionCalibration()` 的返回结构 mock 测试
- `PredictionCalibrationCard` 在 `n=0` 时显示 no-data 状态

## 建议处理顺序

1. 立刻修 `DecisionCard` 路由：`/events/{id}` 改成 `/events?id={id}`。
2. 把“双口径指标”写进设计文档：headline calibration = act-only；trust qualification = act + watch，exclude skip。
3. 给 `/events/decisions/open` 的 `decision` 参数加枚举校验。
4. 扩展 `DecisionReport`，加入 segment sample、skill、dormant/qualification、liquidity adjustment 等解释字段。
5. 后续单独规划 M3 append-only prediction ledger，不要和小修混做。
6. 后续单独规划 M4 evidence factor validation，让系统能评估因子，而不是只评估最终行动。

## 总体判断

M5 的核心闭环已经从“概念上有接口”推进到“后端统计口径基本正确、前端有入口、关键测试能守住 act-only invariant”。这是一轮实质性优化。

但当前仍有一个会直接影响用户点击路径的前端路由 bug；同时 `segment_skill` 的 act+watch 设计需要正式固化，否则很容易在后续重构中被误改。处理完这两点后，M5 可以视为可用版本；append-only ledger 和 evidence factor validation 则应作为后续结构性里程碑继续推进。
