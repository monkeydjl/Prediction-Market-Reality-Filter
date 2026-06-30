# Milestone 4 代码审查意见

日期：2026-06-18

范围：
- 本轮审查当前代码中标称 Milestone 4 相关的新改动
- 重点查看：
  - `decision_report_service.py`
  - `trend_analysis_service.py`
  - `events.py` 新增决策/edge endpoint
  - `scheduler.py` 新增事件发现调度
  - `prediction_store.py` 当前 scoring / opportunity surface
  - evidence 相关服务是否发生实质 M4 refinement

本文只记录审查意见，不修改实现代码。

---

## 一、执行结论

这轮代码有实质进展，但它**不像 V2 Roadmap 定义的 Milestone 4**。

按 `docs/user/V2_ROADMAP.md`，Milestone 4 的主题是：

- Evidence factor refinement
- correctness batch first
- opinion penalty
- numeric proximity
- source reliability
- time-window alignment
- 每个 tuning factor 必须用 resolved samples 的 conditional Brier 验证

但当前实际新增/主要改动集中在：

- decision report
- open decisions endpoint
- fresh edge endpoint
- edge trajectory / freshness classification
- scheduler 自动 event discovery

这些更接近：

- M3 的 temporal / edge trajectory surface
- M5 的 decision reporting / opportunity surface

而不是 M4 的 evidence factor refinement。

所以我的总体判断是：

- **这轮代码有价值**
- **但不能按 V2 文档标准判定为 M4 完成**
- 更准确的定性是：**M3/M5 方向的展示与调度能力增强，M4 证据因子验证尚未真正落地**

---

## 二、这轮代码做对的地方

## 1. Decision Report 结构清晰

新增：

- `backend/app/services/decision_report_service.py`

`build_decision_report(prediction, record)` 是纯函数：

- 不读 store
- 不访问网络
- 不依赖 FastAPI
- 输入 prediction + event record
- 输出面向人类审阅的 decision report

这点设计是好的。

它把 report 分成：

- event
- probability
- market_view
- edge
- confidence
- recommendation
- risk
- category
- status

这符合 V2 后期 report engine 的方向。

### 评价

这是一个合理的小模块，边界清楚，测试也容易写。

但它属于 **Decision / Reporting surface**，不是 evidence factor refinement。

---

## 2. Open Decisions endpoint 是有用的产品面

新增：

- `GET /events/decisions/open`

它从 `prediction_store.list_open_opportunities()` 读取 open predictions，再 join event record，输出 decision report。

默认筛选：

- `act`
- `watch`

并排除：

- `skip`

这个 endpoint 对实际使用有价值，因为它让系统从“分析很多事件”进一步变成“告诉我现在该看哪些机会”。

### 评价

这是 M5 方向的能力。

如果团队目标是让用户看到可操作机会，这个接口是对的。

但它不是 M4 的核心。

---

## 3. Fresh Edge surface 有实际价值

新增：

- `analyze_edge_trajectory()`
- `rank_fresh_edges()`
- `GET /events/edges/fresh`

这套逻辑把事件历史 snapshots 转成 edge trajectory：

- latest_edge
- first_edge
- peak_edge
- net_edge_change
- recent_edge_change
- age_hours
- freshness_band
- classification

分类包括：

- no_data
- stale
- closed
- fresh
- decaying

这对捕捉“edge 是否还活着”是有意义的。

### 评价

这是 temporal / opportunity surface 方向的增强。

它有助于回答：

- 这个 edge 是刚出现的，还是已经衰减？
- 这个 divergence 是否还值得看？

这比只看当前 raw edge 更好。

但它仍然不是 evidence factor refinement。

---

## 4. Scheduler 增加 event discovery job 是必要的

新增：

- `_job_event_discover()`
- `EVENT_DISCOVER_ENABLED`
- `EVENT_DISCOVER_LIMIT`
- scheduler 注册 `event_discover@07:15UTC`

这很重要，因为如果没有自动 event discovery：

- predictions 不会持续冻结
- audit snapshots 不会持续增长
- edge trajectory 永远没有足够数据
- calibration / diagnosis 也会长期 dormant

### 评价

这是让闭环持续产出样本的必要基础设施。

`use_cache=False` 强制刷新，也符合“需要新的 audit snapshot”的目的。

但这仍然是 M3 temporal loop / operationalization，不是 M4 证据因子。

---

## 三、主要问题与风险

## 1. 这轮不是 V2 Roadmap 意义上的 M4

这是最重要的结论。

### V2 Roadmap 中 M4 的要求

M4 是：

- Evidence factor refinement

核心约束是：

- 每个证据调参必须用 Brier 验证
- 每个 tuning factor 只有在 conditional Brier 改善时才算 ship

Roadmap 中明确列出：

- opinion penalty
- numeric proximity
- source reliability
- time-window alignment

### 当前代码事实

从本轮 diff 看，主要修改文件是：

- `backend/app/services/trend_analysis_service.py`
- `backend/app/services/decision_report_service.py`
- `backend/app/api/routes/events.py`
- `backend/app/core/scheduler.py`
- `backend/tests/test_trend_analysis_service.py`
- `backend/tests/test_decision_report_service.py`
- `backend/tests/test_scheduler.py`

而 evidence 核心服务并没有对应的大幅改动：

- `evidence_extraction_service.py`
- `evidence_scoring_service.py`
- `news_filter_service.py`

也没有看到新增的：

- evidence factor ablation
- evidence profile segment calibration
- factor-level Brier comparison
- resolved-sample validation harness

### 结论

这轮更像：

- M3 temporal edge surface
- M5 decision report surface

而不是：

- M4 evidence factor refinement

如果团队内部称它为 M4，需要修正文档或修正实现，否则里程碑会失真。

---

## 2. `Only act rows are scored` 问题仍未修复

这是上轮已经指出的问题，这轮仍然存在。

### 当前实现

`backend/app/services/event_resolve_service.py`

- `resolve_with_calibration()` 在 `status == "resolved"` 时调用 `score_prediction(event_id, actual_outcome)`

`backend/app/memory/prediction_store.py`

- `score_prediction()` 查询：
  - `WHERE event_id=? AND status='open'`

它不检查：

- `decision == "act"`

`calibration_summary()` 查询：

- `FROM predictions WHERE status='scored'`

也不检查：

- `decision == "act"`

### 后果

只要某个 prediction 被 freeze，并且之后 resolve，就会被打分。

这意味着：

- `watch`
- `skip`

也会进入 scored predictions。

### 为什么严重

V2 文档要求：

- Only act rows are scored

因为系统要学习的是：

- 我们真正决定 act 的机会是否 beat market

而不是：

- 所有被系统看过、冻结过、甚至判定为 watch/skip 的事件表现如何

如果不修，后续：

- `segment_skill()`
- `calibration_summary()`
- `diagnosis_service.calibration_trust()`

都会被非 act 样本污染。

这会直接影响 future trust。

---

## 3. `list_open_opportunities()` 做了展示过滤，但没有修正校准口径

这轮新增：

- `list_open_opportunities(decisions=("act", "watch"))`

它对 open opportunity surface 有帮助：

- 默认只展示 act/watch
- 排除 skip
- 按 adjusted_edge 排序

但这只是展示层过滤。

它没有改变：

- scoring 口径
- calibration 口径
- segment skill 口径

也就是说：

- 前端看到的是 act/watch opportunities
- 但系统学习时仍然可能学习所有 scored predictions

这两个口径不一致，会造成后续解释混乱。

---

## 4. Fresh edge 依赖 audit snapshots，但底层 prediction history 仍不是 append-only

`analyze_edge_trajectory()` 是基于 `event_audit.jsonl` 的 snapshots。

它可以回答：

- event 的 estimated-baseline edge 如何变化

但它不是 prediction history。

当前 `prediction_store` 仍然是：

- `event_id TEXT NOT NULL UNIQUE`
- `ON CONFLICT(event_id) DO NOTHING`
- resolve 时 UPDATE 同一行

因此：

- event audit 有多次 snapshots
- 但 predictions 仍然一事件一行

这意味着系统目前有“事件概率轨迹”，但还没有“append-only committed prediction history”。

### 影响

Fresh edge surface 能看趋势，但不能替代 M3 要求的：

- 每次 re-evaluation append prediction
- point-in-time committed decision history

如果后续要评估“每次出现 fresh edge 时是否值得 act”，还需要真正的多行 prediction ledger。

---

## 5. Fresh edge 的 classification 是启发式，不是经过校准验证的 edge quality

当前 classification 规则是：

- stale：last snapshot older than `EDGE_STALE_HOURS`
- closed：latest edge below `DECISION_WATCH_EDGE`
- fresh：latest edge holding near peak
- decaying：material edge shrunk from peak

这个启发式合理，但它不是 outcome-validated。

也就是说，它只能说明：

- 这个 edge 形态上还新鲜

不能说明：

- 这种 edge 形态历史上真的 beat market

如果把它展示为“机会”，可以接受；但如果把它当成“已验证 alpha”，还不够。

---

## 6. Scheduler 仍同时保留 legacy market scan 和 event discover 两条线

`scheduler.py` 现在有：

- legacy morning scan
- event discover
- legacy evening resolve
- event auto-resolve

这符合兼容期现状，但也带来风险：

- legacy `analysis_audit` / `agent_memory`
- event-layer `event_store` / `prediction_store`

会继续并行产生数据。

如果文档或 UI 没有明确区分，用户容易混淆：

- legacy signal calibration
- event calibration
- prediction calibration

这不是本轮新增 bug，但自动调度会让并行数据持续增长，所以需要尽早治理展示口径。

---

## 四、测试覆盖评价

### 已覆盖得比较好的部分

这轮测试覆盖了：

1. decision report 拼装
2. decision endpoint 基本返回
3. open decisions report join event record
4. edge trajectory classification
5. fresh edge ranking
6. history route 包含 edge block
7. scheduler event discovery job 是否注册
8. scheduler event discovery 是否 `use_cache=False`

这些测试方向是合理的。

### 缺失的关键测试

仍缺：

1. `decision != "act"` 的 prediction resolve 后不得被 score
2. `calibration_summary()` 不应统计 watch/skip rows
3. `segment_skill()` 不应统计 watch/skip rows
4. M4 evidence factor 的 resolved-sample Brier 验证测试
5. evidence tuning factor 是否改善 conditional Brier 的回归测试

其中前 3 个是当前系统学习口径的核心测试。

后 2 个才是真正 M4 的测试。

---

## 五、当前状态的准确命名建议

为了避免项目认知偏差，我建议不要把这轮直接叫“Milestone 4 完成”。

更准确的叫法是：

> M3/M5 surface progress: edge trajectory, fresh edge ranking, decision reports, and scheduled event discovery.

或者：

> M3.5 operational surface: continuous discovery + fresh edge and decision report endpoints.

如果一定要保留“M4”名称，那么需要补齐真正的 M4 内容：

- evidence factor tuning
- resolved-sample validation
- Brier-based acceptance criteria

---

## 六、建议的修正优先级

## 优先级 1：先修 scoring 口径

必须让以下逻辑成立：

- 只有 `decision == "act"` 的 prediction 才能进入 scored state
- `watch` / `skip` resolve 后可以记录 outcome，但不能进入 prediction calibration
- `segment_skill()` 和 `calibration_summary()` 只读 act-scored rows

这是 Decision Gate 的核心。

否则后续所有 trust 都会被污染。

---

## 优先级 2：把本轮能力重新归类为 M3/M5 surface

当前新增功能有价值，但应归类正确：

- `decision_report_service` -> M5 report engine
- `/events/decisions/open` -> M5 opportunity surface
- `/events/edges/fresh` -> M3 temporal edge surface
- scheduler event discovery -> M3 operational loop

这样路线会更清楚。

---

## 优先级 3：真正启动 M4 evidence validation

M4 应该从 resolved samples 出发，而不是从直觉调权重出发。

建议最小 M4 验证框架：

1. 读取 resolved predictions / events
2. 提取当时的 evidence profile
3. 按 factor 分桶：
   - opinion-heavy
   - numeric proximity high/low
   - source reliability high/low
   - time-window aligned/misaligned
4. 对比各桶 Brier
5. 只有 Brier 改善的 factor 才进入默认评分逻辑

这才符合 Roadmap 里的：

> Each tuning factor ships only if conditional Brier improves on resolved samples.

---

## 优先级 4：继续推进 append-only prediction history

Fresh edge 现在依赖 event audit snapshots，这是有用的。

但如果要严肃评估：

- 每次 fresh edge 出现时是否值得 act

仍然需要 append-only prediction history，而不是一事件一行。

这部分仍是后续结构债。

---

## 七、最终判断

这轮代码不是无效工作。相反，它增加了几个有实际使用价值的能力：

- decision report
- open decisions
- fresh edge
- scheduled event discovery

这些能力能让系统更像一个可用产品，而不是只是一堆后台分析。

但如果按 V2 Roadmap 严格审查：

> 这轮不是 M4 evidence factor refinement。

它更像：

> M3/M5 operational and reporting surface.

当前最需要修的仍然是：

> Decision Gate 的 scoring 口径：只有 act rows 应进入 prediction calibration。

而真正的 M4 还需要补：

> 用 resolved samples 和 conditional Brier 验证 evidence factors。

