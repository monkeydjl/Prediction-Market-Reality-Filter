# Milestone 5 代码审查意见

日期：2026-06-18

范围：
- 本轮审查 Milestone 5 相关实现
- 重点查看：
  - `decision_report_service.py`
  - `/events/decisions/open`
  - `/events/{event_id}/decision`
  - `/events/edges/fresh`
  - `prediction_store.list_open_opportunities()`
  - `prediction_store.calibration_summary()` 新增 realized edge / hit rate
  - 前端是否实际接入 M5 决策接口

本文只记录审查意见，不修改实现代码。

---

## 一、执行结论

这轮代码已经明显进入 M5 的方向：

- 有 decision report
- 有 open decisions endpoint
- 有 fresh edge endpoint
- 有 realized edge / directional hit rate 指标
- 有面向人类审阅的 report shape

因此，如果按 M5 的目标“把系统输出变成用户可理解、可审阅、可行动的机会报告”来看，这版已经做出了核心后端雏形。

但当前还不能算完整 M5，主要原因有三点：

1. **Decision Gate 的 scoring 口径仍然错误**
   - `watch` / `skip` 仍可能进入 scored predictions 和 calibration

2. **前端没有真正接入 M5 新接口**
   - `eventsApi` 还没有 `decisions/open`、`edges/fresh`、`{id}/decision`
   - dashboard 仍主要展示 event list / movers，而不是 decision opportunities

3. **M5 输出是 report surface，但底层 M3/M4 结构债仍在**
   - prediction history 仍不是 append-only multi-row
   - M4 evidence factor validation 仍未真正落地

所以我的判断是：

- **M5 后端雏形成立**
- **M5 产品闭环尚未完成**
- **M5 的可信度仍受 M2/M3/M4 遗留问题影响**

---

## 二、这轮做对的地方

## 1. Decision Report 模块边界清楚

`backend/app/services/decision_report_service.py`

`build_decision_report(prediction, record)` 是一个纯组装函数：

- 不读数据库
- 不访问网络
- 不依赖 FastAPI
- 输入 prediction + event record
- 输出 decision report

这很适合作为 report engine 的基础。

输出结构包含：

- event
- probability
- market_view
- edge
- confidence
- recommendation
- risk
- category
- status

这些字段基本覆盖了 V2 report 的核心需求：

- 事件是什么
- 系统概率是多少
- 市场怎么看
- edge 多大
- trust 多高
- 系统建议是什么
- 风险是什么

### 评价

这个模块是 M5 里最干净的一块。

---

## 2. `/events/decisions/open` 是正确的机会入口

`backend/app/api/routes/events.py`

新增：

- `GET /events/decisions/open`

它读取：

- `prediction_store.list_open_opportunities()`

然后 join：

- `event_store.get_event()`

最后输出：

- decision report 列表

默认筛选：

- `act`
- `watch`

排除：

- `skip`

并按：

- `ABS(adjusted_edge)`

排序。

### 评价

这是 M5 需要的机会列表入口。

它比传统 event list 更接近用户真正需要的答案：

> 现在有什么值得看？

---

## 3. `/events/{event_id}/decision` 补齐了单事件决策视图

新增：

- `GET /events/{event_id}/decision`

它返回某个 event 的 committed prediction + event record 拼出的 decision report。

如果 event 没有 committed prediction，会返回 404。

这个行为合理，因为非市场事件没有 market edge，不应伪造 decision report。

---

## 4. Fresh edge surface 对机会发现有价值

新增：

- `GET /events/edges/fresh`
- `analyze_edge_trajectory()`
- `rank_fresh_edges()`

这让系统能区分：

- edge 是否刚出现
- edge 是否仍接近 peak
- edge 是否已经 stale / closed / decaying

这对 M5 很有价值，因为用户不只需要知道 edge 存在，还需要知道：

- 它是不是还活着
- 是不是已经错过

---

## 5. realized edge / directional hit rate 是好的方向

`prediction_store.calibration_summary()` 新增：

- `realized_edge`
- `directional_hit_rate`

这是比纯 Brier 更贴近交易/机会判断的指标。

Brier 回答：

- 概率准不准？

realized edge 回答：

- 我们认为市场错的方向，后来现实是否支持？

这符合项目目标：

> identify prediction market mispricing

---

## 三、主要问题与风险

## 1. `Only act rows are scored` 仍未实现

这是最严重的问题，而且已经连续多轮存在。

### 当前实现

`event_resolve_service.resolve_with_calibration()`：

- 只要 `status == "resolved"` 就调用 `score_prediction(event_id, actual_outcome)`

`prediction_store.score_prediction()`：

- 查询条件是 `event_id=? AND status='open'`
- 没有检查 `decision == 'act'`

`prediction_store.calibration_summary()`：

- 汇总所有 `status='scored'`
- 没有检查 `decision == 'act'`

`prediction_store.segment_skill()`：

- 也汇总所有 `status='scored'` 的 category rows

### 结果

以下 prediction 都可能进入 scored calibration：

- `act`
- `watch`
- `skip`

### 为什么这对 M5 更严重

M5 的目标是展示 actionable opportunities。

如果 calibration 学习的是所有 freeze 过的东西，而不是系统真正建议 act 的东西，那么 M5 report 的可信度会被稀释：

- report 展示的是 act/watch opportunity
- trust 学习的却可能包括 skip/watch 的表现

这会导致“系统是否真的发现机会”这个问题被口径污染。

### 应修规则

建议明确：

- `decision == "act"` 才能进入 prediction scoring
- `watch` / `skip` 可以保留 outcome 记录，但不得进入 prediction calibration
- `calibration_summary()` 和 `segment_skill()` 只统计 act-scored rows

---

## 2. 前端还没有接入 M5 新接口

后端已经有：

- `/events/decisions/open`
- `/events/edges/fresh`
- `/events/{event_id}/decision`

但 `frontend/src/lib/api.ts` 里没有对应 API wrapper。

当前前端主要仍在调用：

- `/events/`
- `/events/movers`
- `/events/{id}`
- `/events/{id}/history`
- `/events/{id}/similar`
- `/events/calibration`

Dashboard 页面仍主要展示：

- SummaryBar
- MoversBoard
- EventTable

Detail 页面仍主要展示：

- event detail
- probability chart
- evidence
- market links
- tracking
- similar events

### 影响

M5 的后端能力已经存在，但产品主界面还没有真正把它作为第一屏或核心工作流。

用户仍然会看到“事件监控面板”，而不是：

> 当前 act/watch opportunities

这意味着 M5 还停留在 API 层，没有完整变成用户体验。

---

## 3. `open decisions` 默认包含 watch，这适合 review queue，但不等于 actionable opportunities

`GET /events/decisions/open` 默认返回：

- act
- watch

这对 human review queue 是合理的。

但如果这个接口被称作 opportunity surface，就需要谨慎。

严格来说：

- `act` 是机会
- `watch` 是观察
- `skip` 是忽略

如果默认把 act + watch 都叫 decisions / opportunities，可能会模糊“真正可行动”的定义。

### 建议

接口可以保留默认 act+watch，但文档和 UI 应明确：

- `act` = actionable opportunity
- `watch` = review / monitor candidate
- `skip` = excluded

---

## 4. realized edge 指标没有按 decision 过滤

`realized_edge` 和 `directional_hit_rate` 是好指标，但当前它们基于：

- 所有 `status='scored'` rows

因为 scoring 没按 act 过滤，所以这两个指标同样会被 watch/skip 污染。

M5 最想回答的是：

> acting on surfaced opportunities would have beaten market consensus

那 realized edge 应该优先统计：

- act rows

否则它回答的是：

> all frozen predictions directionally beat market 吗？

这不是同一个问题。

---

## 5. Decision report 是静态 join，不解释为什么 trust 是当前值

当前 report 给出：

- raw edge
- adjusted edge
- trust

但没有给出 trust 的来源解释，例如：

- category sample size
- category Brier
- dormant segment
- liquidity factor
- why act/watch/skip

这会影响人类审阅。

用户看到：

- trust = 0.5

但不知道：

- 是因为 dormant default？
- 是因为 category history 刚好 skill=0.5？
- 是 liquidity 折损？
- 是样本不足导致不能 act？

### 建议

后续 report 可以增加：

- diagnosis_reason
- segment_n
- segment_brier
- liquidity_factor
- decision_reason

否则 report 可读，但解释力还不够。

---

## 6. Fresh edge 是有用 surface，但没有和 Decision Report 合并

当前：

- `/events/decisions/open` 展示 open decision reports
- `/events/edges/fresh` 展示 fresh edge trajectory

两者是分开的。

但用户真正需要的是：

> 当前 fresh 且 decision 值得看的机会

也就是：

- decision report
- edge freshness
- trend state

应在一个 surface 里合并。

现在用户需要自己把两个 endpoint 的结果拼起来。

---

## 7. M3/M4 的结构债会影响 M5 可信度

M5 是展示层，但展示层的可信度来自底层闭环。

当前仍有前几轮提到的结构问题：

- `predictions` 仍是 `UNIQUE(event_id)` 单行模型
- resolve 仍然 UPDATE 同一 prediction row
- 没有独立 `outcomes` fact table
- 没有独立 `market_snapshots`
- M4 evidence factor validation 仍未真正按 Brier 落地

这些不阻止 M5 API 工作，但会限制它成为“可信决策系统”。

---

## 四、测试覆盖评价

### 已覆盖部分

已有测试覆盖：

1. `build_decision_report()` 字段映射
2. 缺失 event record 时的 minimal report
3. report 不引入 trading vocabulary
4. `/events/decisions/open` 返回 joined report
5. `/events/{event_id}/decision` 返回 report 或 404
6. `list_open_opportunities()` 过滤 skip，排序 act/watch
7. `realized_edge` / `directional_hit_rate` 基本计算
8. fresh edge classification / ranking

这些测试对 M5 后端 surface 有帮助。

### 缺失测试

仍缺关键测试：

1. `decision != act` resolve 后不得进入 scored state
2. `calibration_summary()` 不统计 watch/skip
3. `segment_skill()` 不统计 watch/skip
4. `/events/decisions/open?decision=act` 只返回 act
5. 前端 API wrapper 覆盖 M5 endpoints
6. UI 是否实际渲染 decision report / fresh edge

其中 1-3 是学习口径问题，比 UI 更优先。

---

## 五、M5 完成度判断

### 可以认为完成的部分

- 后端 decision report assembly
- open decision endpoint
- per-event decision endpoint
- fresh edge endpoint
- opportunity ranking by adjusted edge
- realized edge / directional hit rate 指标雏形

### 不能认为完成的部分

- 前端完整 M5 工作流
- act-only scoring
- act-only calibration / realized edge
- decision report 的 trust 解释
- decision report 与 fresh edge 的统一机会视图
- 对“acting on surfaced opportunities beats market”的严格验证

---

## 六、建议修正优先级

## 优先级 1：立刻修 act-only scoring

这是 M5 是否可信的前提。

建议：

- `score_prediction()` 只 score `decision='act'`
- 或新增参数明确允许/禁止 score non-act，但默认必须 fail-closed
- `calibration_summary()` 只统计 act-scored rows
- `segment_skill()` 只统计 act-scored rows

如果还想观察 watch/skip 的表现，可以单独做 diagnostic metrics，不要混入主 calibration。

---

## 优先级 2：前端接入 M5 endpoints

至少需要在 `frontend/src/lib/api.ts` 加：

- `openDecisions()`
- `freshEdges()`
- `decision(id)`

然后在 dashboard 上增加一个真正的 opportunity panel：

- act opportunities
- watch candidates
- fresh edge state
- adjusted edge
- trust
- risk

否则 M5 只是后端 API，用户体验还没完成。

---

## 优先级 3：合并 decision report 与 edge freshness

建议 M5 主视图最终展示：

- decision
- adjusted edge
- trust
- fresh/stale/decaying
- latest edge
- category
- risk flags
- recommended action

这样用户不需要同时看 decisions 和 fresh edges 两套接口。

---

## 优先级 4：增强 report explainability

建议 report 增加：

- why this decision
- why trust is this value
- category sample count
- category Brier / skill
- liquidity factor
- dormant / qualified state

这会让用户知道系统不是随便给出 act/watch/skip。

---

## 七、最终判断

这轮 M5 实现是有价值的。

它已经把系统往“决策输出”方向推进，而不是只停留在：

- event list
- probability chart
- evidence detail

但现在最准确的评价是：

> M5 backend surface exists, but M5 product loop is not complete.

最关键的阻塞仍然是：

> calibration / scoring 没有只统计 act rows。

只要这个问题不修，系统就无法严肃回答：

> 我们建议 act 的机会，是否真的 beat market consensus？

这正是 M5 和整个项目最终要回答的问题。

