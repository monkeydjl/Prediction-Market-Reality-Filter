# Milestone 2/3 代码审查意见

日期：2026-06-18

范围：
- 本轮仅审查 Milestone 2 / 3 相关新代码
- 重点关注：
  - `diagnosis_service`
  - `prediction_store`
  - `event_resolve_service`
  - `manifold_event_source`
  - `kalshi_event_source`
- 本文基于当前代码状态，不修改代码

---

## 一、执行结论

这轮代码相较于 Milestone 1 有明确进展，尤其是两点：

1. **跨平台 resolved identity 补强了**
   - `Manifold` resolved 返回现在带 `id`
   - `Kalshi` resolved 返回现在也带 `id`
   - 这修复了上一轮最重要的 identity 缺口之一

2. **Disagreement Diagnosis / Decision Gate 已经进入实现**
   - 新增 `diagnosis_service.py`
   - `prediction_store.freeze_prediction()` 现在会冻结：
     - `base_rate_category`
     - `trust`
     - `adjusted_edge`
     - `decision`

这说明系统已经不再只是“AI 概率减市场概率”的原始差值，而是开始引入：

- 条件校准
- trust 权重
- adjusted edge
- act / watch / skip

这是正确方向。

但是，按 V2 文档标准来看，这轮代码**仍然不能判定为 Milestone 3 已完成**。当前最主要的问题有三个：

1. `Decision Gate` 已引入，但 prediction scoring 仍未按 `decision == act` 过滤
2. `event_market_links` 虽然支持 `resolution_criteria`，但写入路径仍未真正保存它
3. 时间维度 / append-only prediction history / 独立 outcomes 与 market_snapshots 仍未落地

因此，我的总体判断是：

- **M2：大体成立，方向正确**
- **M3：如果按 V2 文档定义，当前仍未完成**

---

## 二、这轮代码已经做对的地方

## 1. Manifold / Kalshi resolved identity 已经补上

上一轮的一个核心问题是：

- `auto_resolve_events()` 需要用 resolved market 的 `id` / `contract_id`
- 但 `Manifold` / `Kalshi` 的 resolved 返回里没有稳定 identity

当前这轮代码已经修正：

### Manifold

`backend/app/services/manifold_event_source.py`

现在 `fetch_resolved_markets()` 返回：

- `id`
- `question`
- `actual_outcome`

这样 resolved identity 就和 candidate 阶段的 `source_id` 对齐了。

### Kalshi

`backend/app/services/kalshi_event_source.py`

现在 `fetch_resolved_markets()` 返回：

- `id`（`event_ticker`）
- `question`
- `actual_outcome`

这也显著提升了跨平台 link consistency。

### 结论

这部分是实质修复，不是表面调整。

它直接改善了：

- `event_id -> contract_id -> outcome`

这条闭环的可信度。

---

## 2. Diagnosis Service 设计是干净的

新增 `backend/app/services/diagnosis_service.py`。

从结构上看，这个模块拆得比较好：

- `calibration_trust()`
- `liquidity_factor()`
- `decide()`
- `diagnose()`

这是纯函数实现，不依赖 store 层，测试也比较直接。

### 优点

1. 将 trust 的计算和 persistence 解耦
2. 将 liquidity 惩罚逻辑明确暴露
3. 将 Decision Gate 判定显式化
4. dormant segment 的行为有清晰边界：
   - 使用默认 trust
   - 不允许直接 `act`

这是符合 V2 哲学的：

> divergence is a hypothesis, not an edge

---

## 3. `prediction_store` 已能冻结 diagnosis 结果

当前 `freeze_prediction()` 不只是冻结：

- `ai_probability`
- `market_probability`
- `raw_edge`

还会冻结：

- `base_rate_category`
- `trust`
- `adjusted_edge`
- `decision`

这点是重要提升。

它说明：

- 预测冻结点已经开始包含“当时系统如何看待这个 edge”
- 后续回看时，可以知道当时为什么是 `act/watch/skip`

这比 M1 只有 raw edge 的实现更接近 Decision Gate 语义。

---

## 4. 条件校准能力已经开始成形

`prediction_store` 里新增了：

- `segment_skill(category)`
- `calibration_summary()` 的 `by_category`

这使得系统不只是看全局平均 Brier，而开始看：

- 某个 category 历史上到底有没有 beat market

这是 M2 的核心资产，因为没有它，trust 就没有来源。

---

## 5. 相关测试方向正确

新增测试覆盖了：

- diagnosis trust / liquidity / decision
- `base_rate_category` 持久化
- `by_category` 汇总
- M1 schema -> M2 schema 的迁移列补齐
- Manifold / Kalshi resolved `id` 返回

这说明这轮代码不只是加功能，也在尝试锁行为。

---

## 三、主要问题与风险

## 1. Decision Gate 已经存在，但 scoring 口径仍然错误

这是当前最重要的问题。

### 当前实现

`backend/app/services/event_resolve_service.py` 中：

- `resolve_with_calibration()` 在 `status == "resolved"` 时，直接调用：
  - `score_prediction(event_id, actual_outcome)`

`backend/app/memory/prediction_store.py` 中：

- `score_prediction()` 只按：
  - `event_id`
  - `status='open'`

查找 prediction

它**不会检查**：

- `decision == "act"`

### 结果

只要某个 prediction：

- 被 freeze 了
- 还处于 `open`
- 之后被 resolve

它就会被打分。

这意味着：

- `watch`
- `skip`

也会进入 scored predictions。

### 为什么这是错误

V2 文档写得很明确：

- decision 是 `act / watch / skip`
- **Only act rows are scored as live predictions**

当前实现没有遵守这个规则。

### 影响

这会污染：

1. prediction calibration summary
2. by-category conditional calibration
3. diagnosis trust 的未来输入

换句话说：

- 现在系统不是在学习“哪些 act 真的 beat market”
- 而是在学习“所有 freeze 过的东西最终表现如何”

这两个问题不是一回事。

如果不改，Decision Gate 的存在意义会被削弱。

---

## 2. `resolution_criteria` 仍未在 link 写入路径中真正落地

### 当前状态

`event_market_links` 表结构里有：

- `resolution_criteria`

`MarketLink` 模型也有：

- `resolution_criteria`

但这轮代码里，`upsert_link(...)` 的主要调用路径仍然没有把它写进去。

包括：

1. manual resolve 写 link
2. auto resolve 写 link

当前传入的主要还是：

- `market_name`
- `contract_id`
- `market_question`
- `link_method`
- `link_confidence`
- `verified`

### 为什么这不是小问题

`question` 一致不等于 `resolution meaning` 一致。

后续如果要真正审计：

- 我们当时预测的到底是不是这个市场
- 这个 resolved market 的 YES/NO 含义是不是与当初相同

那 `resolution_criteria` 是关键字段。

现在表有了，但数据没真正进去，identity 审计能力仍然不完整。

### 影响

这不会马上造成代码崩溃，但会导致：

- link 看起来完整
- 实际上少了最关键的语义约束

这是典型的“结构有了，信息没落地”的风险。

---

## 3. 当前实现仍然不是 M3 的时间维度架构

这是第二个结构性问题。

### 代码事实

`prediction_store.py` 仍然是：

- `event_id TEXT NOT NULL UNIQUE`

`freeze_prediction()` 仍然：

- `ON CONFLICT(event_id) DO NOTHING`

`score_prediction()` 仍然：

- 对同一行做 `UPDATE`

### 这意味着什么

系统当前仍然是：

- 一个 event 只允许一条 prediction
- re-scan 不会追加新 prediction
- resolve 会原地补 outcome / brier / resolved_at

### 与 M3 的冲突

V2 文档中的 M3 / temporal dimension 要求是：

- 事件会被反复 re-evaluate
- 每次都会 append 新 snapshots
- 如果满足条件，还会 append 新 predictions
- predictions 是 append-only 历史，不会被覆盖

当前实现明显不满足。

### 更关键的一点

`backend/app/models/event.py` 里的注释也直接说明：

- 这仍然是 “M1 simplified ledger”
- 不是最终 append-only multi-row history
- separate `market_snapshots` / `outcomes` 也是后续工作

所以如果当前对外说“Milestone 3 已完成”，那会和代码注释本身冲突。

---

## 4. `market_snapshots` / `outcomes` / `calibration_metrics` 仍未独立成表

按 V2 设计，这些应该是核心事实表。

但从当前代码看：

- 没有独立 `market_snapshots` store
- 没有独立 `outcomes` fact table
- 没有独立 `calibration_metrics` table

当前仍然主要依赖：

- `predictions` 表内折叠字段
- `event_store` 上的 outcome / calibration

### 影响

对 M2 来说还能工作，但对 M3 来说仍然不足：

1. 没有真正 point-in-time market snapshot history
2. 没有独立 outcome fact
3. 没有可扩展的 calibration metrics layer

也就是说：

- temporal analysis 还没有真正从数据模型上成立

---

## 5. 测试仍未覆盖“Only act rows are scored”

当前测试有：

- dormant segment 不会 `act`
- diagnosis 字段会被冻结
- `by_category` 汇总存在

但**没有**看到关键测试：

- `decision == watch` 时 resolve 后不应进入 scored predictions
- `decision == skip` 时 resolve 后不应进入 scored predictions
- `calibration_summary()` 不应统计非 act rows

这会导致当前错误评分口径即使存在，也不会被测试阻止。

---

## 四、我对当前状态的判断

为了避免里程碑命名和实际代码混淆，我建议这样定性：

### 可以认为已经成立的

- M0：跨平台 contract identity 基本补齐
- M1：最薄 freeze -> resolve -> score 闭环成立
- M2：diagnosis / trust / adjusted edge / decision 机制已进入实现

### 不能认为已经成立的

- 不能说 Decision Gate 的 scoring 口径已经正确
- 不能说 M3 时间维度已经落地
- 不能说 prediction history 已经 append-only
- 不能说 `market_snapshots` / `outcomes` / `calibration_metrics` 已经实现

---

## 五、建议的修正优先级

## 优先级 1：修正 scoring 口径

这是当前最需要先修的地方。

原则应明确为：

- 只有 `decision == "act"` 的 prediction 才允许进入 scored state
- 只有这些 scored rows 才进入 calibration summary / by_category

这是 Decision Gate 成立的最低条件。

如果这条不修，后面的 conditional calibration 都会被口径污染。

---

## 优先级 2：把 `resolution_criteria` 真正写进 links

建议至少在两类路径写入：

1. auto-resolve link
2. manual resolve link

如果当前 resolved source 无法直接拿到 market resolution criteria，也应明确记为空并在后续 source adapter 中补，而不是让这列长期停留在“有 schema 无数据”的状态。

---

## 优先级 3：明确当前仍是 M2.5，而不是完整 M3

如果不打算立刻做 append-only temporal schema，那么建议在文档和对内沟通里明确：

- 当前已完成 M2
- 正在走向 M3
- 但 M3 核心的数据模型工作还未完成

这比把尚未落地的结构提前宣布完成要稳妥得多。

---

## 优先级 4：为 M3 先定义清楚迁移目标

真正进入 M3 前，建议先明确三件事：

1. prediction history 是否改成一事件多行
2. outcomes 是否独立成事实表
3. market snapshots 是否独立成表

否则现在继续在 `prediction_store` 上叠功能，会把后面迁移成本继续抬高。

---

## 六、最终判断

这轮新代码**不是没有价值**，相反，它解决了上一轮一个很重要的真实问题：

- `Manifold` / `Kalshi` resolved identity 补齐了

同时也把系统从：

- “原始 raw edge 闭环”

推进到了：

- “带 diagnosis / trust / adjusted edge / decision 的闭环”

这一步是实质进展。

但我要明确指出：

> 当前代码已经实现了 M2 的核心方向，但还没有实现 V2 文档定义下的完整 M3。

最核心的原因有两个：

1. `Only act rows are scored` 还没有在实现中成立
2. 时间维度的 append-only 历史模型还没有落地

所以这轮更准确的结论是：

- **M2 已基本成立**
- **M3 仍未完成**

