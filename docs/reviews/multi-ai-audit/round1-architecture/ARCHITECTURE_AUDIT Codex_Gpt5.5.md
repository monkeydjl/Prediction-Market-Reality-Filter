# 第一部分 路线图与设计原则架构审计

日期：2026-06-19  
范围：当前工作区全部代码变更的架构审查  
审查重点：是否违反路线图和设计原则，不评代码风格  

## 审查原则

1. M0 只负责 EventMarket Link Ground Truth
2. M1 一事件一冻结 Prediction
3. Prediction 是 Commitment，不是 Trajectory
4. M2 Trust 仅使用 WATCH + ACT 的 resolved predictions
5. Dormant Segment 永远不能 ACT
6. Segment = Category Only
7. M3 只做 KPI，不做 Append-only Ledger 重构
8. 不提前实现 M4/M5 需求
9. Build Only What Next Milestone Requires
10. No Big Bang Rewrite

## 总体结论

当前变更已经越过 M0-M3 的最小路线，提前进入了 M3 ledger、M5 decision surface、fresh edge opportunity surface、持续调度闭环和未来 schema 设计。

最严重的问题不是单点 bug，而是系统语义已经从“逐步验证的反馈闭环”推进成“未来完整产品化闭环”。特别是 `Prediction` 已经从 M1 的“一事件一冻结 Commitment”变成了多行、可 supersede、可重新采样的 trajectory/ledger。这直接违反了当前用户确认的核心原则：Prediction 是 Commitment，不是 Trajectory；M3 只做 KPI，不做 append-only ledger 重构。

需要优先收敛的方向：

- 恢复 M1 的一事件一冻结 prediction 语义。
- 暂停或隐藏 M5 decision/opportunity surface。
- 关闭默认持续 discovery 调度。
- 把 future schema 从当前里程碑中移出。
- 保留当前正确的 M2 约束：category-only segment、act/watch resolved trust、dormant never act。

---

## P0：必须优先处理

### P0-1：Prediction 被实现成多行 append-only ledger

类别：与路线图冲突 / Scope Creep / Future Schema Leakage  
违反原则：2、3、7、9、10  

位置：

- `backend/app/memory/prediction_store.py`
- `backend/app/models/event.py`
- `backend/tests/test_prediction_store.py`

问题：

`prediction_store.py` 的模块注释明确写入：

- append-only
- multi-row per event
- M3
- re-scan appends a new frozen prediction
- prior open row marked `superseded`

schema 层面也已经取消 `UNIQUE(event_id)`，并改为 `CREATE INDEX idx_pred_event ON predictions(event_id)`。`_migrate()` 还显式检测旧的 `event_id UNIQUE` 并重建表，以支持一个 event 多条 prediction。

这与当前路线图约束冲突：

- M1 要求“一事件一冻结 Prediction”。
- Prediction 是 Commitment，不是 Trajectory。
- M3 只做 KPI，不做 append-only ledger 重构。

架构影响：

一旦 prediction 表支持多行、superseded、history 查询，团队后续所有服务都会开始依赖“prediction history”这个概念。这个概念如果现在落地，就会把后续里程碑的设计空间提前锁死。

建议：

恢复 `event_id UNIQUE` 或至少恢复“每个 event 只有一个 committed prediction”的业务不变量。M3 的轨迹类 KPI 应继续读取 audit snapshots，而不是把 prediction 表改造成 trajectory ledger。

### P0-2：`freeze_prediction()` 把 Commitment 变成 re-snapshot 机制

类别：与路线图冲突 / Premature Abstraction  
违反原则：2、3、7、9  

位置：

- `backend/app/memory/prediction_store.py`
- `backend/app/core/config.py`

问题：

`freeze_prediction()` 当前不是“第一次看到 event 时冻结一次 prediction”，而是：

- 每次扫描重新计算 Decision Gate。
- 如果 decision 变化，append 新 prediction。
- 如果 decision 不变但 adjusted edge 或 ai_probability 变化超过 `PREDICTION_RESNAPSHOT_DELTA`，也 append 新 prediction。
- 旧 open prediction 被标记为 `superseded`。

这已经不是 Commitment，而是 trajectory snapshotting。

`PREDICTION_RESNAPSHOT_DELTA` 本身也是一个 premature abstraction：它为“是否重新冻结 prediction”提供配置阈值，但当前路线图并不允许 re-snapshot prediction。

架构影响：

系统会把一次承诺变成连续修订。这样后续评分时，“我们到底承诺了哪一次判断”会变得依赖 ledger 选择规则，而不是 M1 的单一承诺。

建议：

删除或禁用 re-snapshot 逻辑。M1/M2 阶段应只允许首次 commit，后续变化进入 audit/history，不进入 prediction commitment。

### P0-3：M5 Decision / Opportunity Surface 已经进入后端和前端

类别：Scope Creep / 不提前实现 M4/M5  
违反原则：8、9、10  

位置：

- `backend/app/services/decision_report_service.py`
- `backend/app/api/routes/events.py`
- `frontend/src/app/decisions/page.tsx`
- `frontend/src/components/decisions/decision-card.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/components/app-nav.tsx`

问题：

当前已经实现并暴露：

- `GET /events/decisions/open`
- `GET /events/edges/fresh`
- `GET /events/{event_id}/decision`
- 前端 `/decisions` 页面
- decision card
- fresh edge 标记
- `eventsApi.openDecisions()`
- `eventsApi.freshEdges()`

这些是 M5 的核心产品面，不是当前里程碑所需。

架构影响：

前端导航已经把“决策机会”作为主功能暴露，意味着团队会开始围绕 opportunity/report workflow 继续开发，而不是先验证 M0/M1/M2/M3 的基础不变量。

建议：

如果当前仍按路线图推进，应暂时隐藏或移除 M5 surface。可以保留纯后端实验代码，但不应作为主导航、主 API 或稳定产品契约暴露。

### P0-4：默认开启 event discovery 调度，形成自动闭环

类别：No Big Bang Rewrite / Scope Creep  
违反原则：1、8、9、10  

位置：

- `backend/app/core/config.py`
- `backend/app/core/scheduler.py`

问题：

`EVENT_DISCOVER_ENABLED` 默认值为 `true`，scheduler 注册 `event_discover@07:15UTC`。该 job 调用 `discover_events(use_cache=False)`，并通过 persistence 路径触发 `freeze_prediction()`。

这意味着系统默认运行：

- event discovery
- prediction freeze
- event auto resolve
- calibration
- opportunity/report surface

这不是 M0/M1/M2 的局部验证，而是完整运行闭环。

建议：

默认关闭 `EVENT_DISCOVER_ENABLED`。当前阶段应由人工或测试驱动小规模样本进入闭环，不应默认让系统持续生成 prediction commitment。

---

## P1：高优先级收敛

### P1-1：Future Schema Leakage 过重

类别：Future Schema Leakage  
违反原则：2、3、7、8、9  

位置：

- `backend/app/models/event.py`
- `backend/app/memory/prediction_store.py`
- `docs/user/DATABASE_DESIGN.md`

问题：

`Prediction` model 和 SQLite schema 已经包含：

- `superseded`
- `observed`
- `voided`
- `segment_n`
- `segment_skill`
- `liquidity_factor`
- `qualified`
- `adjusted_edge`
- `trust`

其中 M2 诊断需要 `trust` / `adjusted_edge` / category skill 的概念可以理解，但 `superseded`、多行 history、void/supersede terminal status 明显是未来 ledger 设计。

`DATABASE_DESIGN.md` 还定义了未来 segment schema：`global / category / edge_bucket / evidence_profile`。这与当前明确原则“Segment = Category Only”冲突。

建议：

当前 schema 只保留下一里程碑真正需要的字段。未来 segment 类型、edge bucket、evidence profile calibration、append-only status 都应移回设计文档的“future”部分，不应进入当前运行 schema。

### P1-2：Fresh Edge / Edge Trajectory 已变成机会发现功能

类别：Scope Creep / Premature Product Surface  
违反原则：7、8、9  

位置：

- `backend/app/services/trend_analysis_service.py`
- `backend/app/api/routes/events.py`
- `frontend/src/app/decisions/page.tsx`

问题：

`analyze_edge_trajectory()` 和 `rank_fresh_edges()` 已经实现：

- latest edge
- peak edge
- recent edge change
- stale / fresh / decaying classification
- ranked fresh edge list

这不只是 M3 KPI，而是 M5 opportunity discovery surface 的组成部分。

建议：

如果坚持“M3 只做 KPI”，fresh edge ranking 不应暴露为 `/events/edges/fresh` 产品 API，也不应接入 `/decisions` 页面。M3 可保留内部 KPI 计算，但不要形成机会工作流。

### P1-3：M0 Ground Truth 被 resolution/scoring 逻辑扩大

类别：Scope Creep  
违反原则：1、9  

位置：

- `backend/app/memory/event_market_link_store.py`
- `backend/app/services/event_resolve_service.py`

问题：

`event_market_link_store` 本身符合 M0 方向：记录 event-market link、verified、pending review、resolution criteria。

但 `event_resolve_service.py` 同时做了：

- 多源 resolved market fetch
- fuzzy match
- verified/pending gate
- identity conflict invalid
- resolve event
- score prediction
- void prediction

这些已经超出“M0 只负责 EventMarket Link Ground Truth”。它们是 resolution/scoring loop 的实现。

建议：

M0 层应只产生和维护可信 link ground truth。resolve 与 score 可以在后续 milestone 使用该 link，但不应把 M0 的职责描述成完整 auto-resolve/scoring workflow。

### P1-4：测试正在固化超范围行为

类别：Future Schema Leakage / 变更固化风险  
违反原则：7、8、9  

位置：

- `backend/tests/test_prediction_store.py`
- `backend/tests/test_trend_analysis_service.py`
- `backend/tests/test_scheduler.py`

问题：

测试已经覆盖并保护：

- migration drop `UNIQUE(event_id)`
- multi-row ledger
- `superseded`
- `get_predictions()` history oldest-first
- material re-snapshot
- fresh edge ranking
- scheduled event discovery

这些测试会让未来回退变得更困难，因为它们把路线图冲突变成了“测试保护的正确行为”。

建议：

在回退前，不要继续扩大这些测试。回退后测试应改为保护当前不变量：

- 一个 event 最多一个 committed prediction。
- re-scan 不会创建新 prediction。
- dormant category never act。
- segment skill 只读 category 的 resolved act/watch。
- M3 KPI 不依赖 prediction ledger。

---

## P2：中低优先级观察项

### P2-1：Dead Code / 当前里程碑无人必要使用的接口

类别：Dead Code / Premature API  
违反原则：8、9  

位置：

- `GET /events/predictions/recent`
- `GET /events/{event_id}/predictions`
- `prediction_store.get_predictions()`
- `prediction_store.list_recent()`

问题：

这些接口主要服务 ledger/history 可视化，但当前路线图原则不允许 prediction ledger。前端主流程也没有必要依赖它们。

建议：

随 P0 ledger 回退一起移除或隐藏。

### P2-2：文档与当前原则不一致

类别：Future Schema Leakage / Roadmap Drift  
违反原则：6、7、9  

位置：

- `docs/user/V2_ROADMAP.md`
- `docs/user/DATABASE_DESIGN.md`

问题：

文档里仍存在支持未来实现的表述：

- predictions append-only
- multi-row point-in-time
- market snapshots
- independent outcomes
- edge_bucket segment
- evidence_profile segment

但本次明确原则是：

- M3 只做 KPI
- Segment = Category Only
- Prediction 是 Commitment，不是 Trajectory

建议：

文档需要分清：

- current milestone contract
- deferred future design
- explicitly forbidden for current milestone

否则开发者会继续用未来文档为当前超范围实现背书。

### P2-3：配置项提前产品化

类别：Premature Abstraction  
违反原则：8、9  

位置：

- `PREDICTION_RESNAPSHOT_DELTA`
- `EDGE_STALE_HOURS`
- `EVENT_DISCOVER_ENABLED`
- `EVENT_DISCOVER_LIMIT`

问题：

这些配置项本身不是错，但它们代表系统已经进入：

- prediction re-snapshot tuning
- fresh edge opportunity classification
- continuous operational loop

这不是当前里程碑最小需求。

建议：

保留 M0/M1/M2 必需配置，其他配置先移除、隐藏或默认关闭。

---

## 当前符合原则的部分

### M2 Trust 口径基本正确

`segment_skill(category)` 当前使用 category 作为 segment key，并读取 resolved 的 act/watch 样本。这符合原则 4 和原则 6。

注意：headline calibration summary 只统计 act scored rows，这和“系统公开报告 act 成效”可以兼容；M2 trust 用 act/watch resolved 样本，也符合冷启动需求。

### Dormant Segment Never ACT 基本正确

`diagnosis_service` 中 act 需要 qualified segment。样本不足时，即使 raw edge 很大，也只能 watch 或 skip。这符合原则 5。

### EventMarket Link Store 本身方向正确

`event_market_link_store` 的 verified/pending/fail-closed 设计符合 M0 的 ground truth 目标。问题在于它被 resolution/scoring loop 过早包进了完整闭环，而不是 link store 本身错误。

---

## 建议处理顺序

1. 先决策是否严格执行当前 10 条原则。如果执行，P0 必须回退，不能继续在当前结构上修补。
2. 回退 prediction ledger：恢复一事件一 committed prediction，不允许 re-snapshot / superseded。
3. 关闭默认 event discovery 调度，避免系统继续自动生成未来语义的数据。
4. 隐藏或移除 M5 API 和前端 `/decisions` 页面。
5. 把 `DATABASE_DESIGN.md` 和 `V2_ROADMAP.md` 中的 future schema 明确标注为 deferred，不允许当前 milestone 实现。
6. 调整测试，让测试保护当前路线图不变量，而不是保护未来 ledger 行为。

---

## 整改边界

这一节用于防止修复时继续扩大范围。建议按“必须保留 / 必须回退 / 暂缓实现”三类执行。

### 必须保留

这些能力与当前原则兼容，不应因为回退 M3/M5 而删除：

- `event_market_links` 的 verified / pending / fail-closed 设计。
- link 上的 `resolution_criteria`，它属于 M0 ground truth。
- M2 diagnosis 的 category-only segment。
- dormant segment never act 的 gate。
- `segment_skill(category)` 使用 resolved act/watch 样本。
- headline calibration 只报告 act scored rows。
- genuine resolution 与 invalid/void resolution 的区分。

### 必须回退

这些能力直接违反当前路线图原则：

- prediction 表支持一个 event 多行。
- `_migrate()` 删除 `event_id UNIQUE`。
- `superseded` 状态。
- re-scan 触发新 prediction commitment。
- `PREDICTION_RESNAPSHOT_DELTA`。
- `get_predictions()` 和 `GET /events/{event_id}/predictions` 这种 ledger API。
- 默认开启 `EVENT_DISCOVER_ENABLED`。
- 前端主导航暴露 `/decisions`。
- `/events/decisions/open` 作为稳定产品 API。
- `/events/edges/fresh` 作为稳定产品 API。

### 暂缓实现

这些方向未来可能有价值，但不应在当前 milestone 落地：

- append-only prediction ledger。
- independent outcomes fact table。
- market_snapshots table。
- edge_bucket segment。
- evidence_profile segment。
- decision report engine。
- fresh edge opportunity ranking。
- continuous scheduled discovery loop。
- M5 opportunity workflow。

---

## 验收标准

如果团队按本文回退，建议用以下标准判断是否真正收敛：

1. 数据库层：`predictions` 对 `event_id` 有唯一约束，或业务层有等价的不变量保护。
2. 行为层：同一个 event 重复扫描不会新增第二条 open prediction，也不会产生 `superseded`。
3. 语义层：prediction 被解释为一次 commitment；概率轨迹只存在于 audit/history。
4. M2 层：trust 只按 category 聚合 resolved act/watch；没有 edge_bucket 或 evidence_profile segment。
5. Dormant gate：样本不足的 category 永远不会输出 act。
6. API 层：当前稳定 API 不暴露 prediction ledger、decision report、fresh edge opportunity。
7. 调度层：event discovery 不默认自动运行并生成 committed predictions。
8. 测试层：测试保护上述不变量，而不是保护多行 ledger 或 M5 surface。

---

## 分类汇总

| 类别 | 问题 | 优先级 |
|---|---|---|
| Scope Creep | M5 decision/opportunity surface 已进入后端和前端 | P0 |
| Scope Creep | 默认 event discovery 形成持续闭环 | P0 |
| Scope Creep | M0 link ground truth 扩展成 auto-resolve/scoring workflow | P1 |
| Dead Code | prediction ledger recent/history API 当前里程碑不需要 | P2 |
| Premature Abstraction | `PREDICTION_RESNAPSHOT_DELTA` 支持 re-snapshot | P0 |
| Premature Abstraction | fresh edge classification/ranking 产品化 | P1 |
| Future Schema Leakage | `superseded` / multi-row ledger / future statuses | P0 |
| Future Schema Leakage | `edge_bucket` / `evidence_profile` segment 进入设计文档 | P2 |
| 路线图冲突 | Prediction 从 Commitment 变成 Trajectory | P0 |
| 路线图冲突 | M3 做了 append-only ledger，而不是 KPI-only | P0 |
| 路线图冲突 | 提前实现 M4/M5 surface | P0 |

## 最终判断

当前代码中，M2 的核心判断机制有正确部分：category-only segment、act/watch resolved trust、dormant never act 都成立。

但整体架构已经明显越界。最需要回退的是 prediction ledger 和 M5 surface。否则项目会在还没完成 M0/M1/M2/M3 基础不变量验证前，就进入未来产品闭环，后续每一步都会在错误的系统语义上继续叠功能。


# 第二部分 数据闭环审计

日期：2026-06-19  
范围：Scheduler -> Discover -> Event -> Market Link -> Freeze Prediction -> Resolve Outcome -> Calibration -> Trust -> Decision Report  
审查目标：判断系统是否真正形成数据闭环，而不是只存在分散的数据流。  

## 总体结论

当前系统 **部分形成了数据闭环**，但还不是可靠闭环。

实际已经连通的闭环是：

```text
Scheduler / API
  -> Discover
  -> Event Store + Event Audit
  -> Freeze Prediction
  -> Resolve Outcome
  -> Score Prediction
  -> segment_skill(category)
  -> 下一次 Freeze Prediction 的 Diagnosis / Decision Gate
```

也就是说，M2 trust 的核心反馈路径是存在的：resolved 的 act/watch prediction 会进入 `segment_skill(category)`，下一次同 category 的 prediction 会读取该 trust。

但闭环存在关键断点：

1. `event_store.save_events()` 重扫时可能覆盖掉已有 `outcome` / `calibration`，使 resolved event 回到 unresolved 状态。
2. `predictions` 支持多版本，闭环不是 One Event One Prediction，而是隐式 trajectory ledger。
3. `event_store.json`、`event_audit.jsonl`、`v2_loop.db` 之间没有事务，一步失败会留下半写状态。
4. 多个路径吞异常并继续返回成功或部分成功，存在静默失败。
5. category dormant 毕业依赖足够多 resolved watch/act 样本；在默认 exact-match auto-verify 和低样本情况下，很容易长期无法毕业。
6. Event calibration feedback 另有一套概率反哺机制，但默认关闭，因此正常运行时不参与闭环。

因此，系统不是“没有闭环”，而是“闭环存在，但可靠性、幂等性和毕业路径不足”。

---

## 当前实际数据流

```text
                      ┌────────────────────────────┐
                      │ Scheduler                  │
                      │ - event_discover@07:15 UTC │
                      │ - event_auto_resolve@22:30 │
                      └──────────────┬─────────────┘
                                     │
                                     v
┌───────────────────────────────────────────────────────────────┐
│ Discover                                                       │
│ discover_events()                                              │
│ - collect candidate events                                     │
│ - analyze_event()                                              │
│ - build_event_record()                                         │
└──────────────┬────────────────────────────────────────────────┘
               │
               v
┌───────────────────────────────────────────────────────────────┐
│ Event Persistence                                              │
│ _persist_events()                                              │
│ - event_store.save_events() -> event_store.json                │
│ - record_event() -> event_audit.jsonl                          │
│ - freeze_prediction() -> v2_loop.db.predictions                │
└──────────────┬────────────────────────────────────────────────┘
               │
               v
┌───────────────────────────────────────────────────────────────┐
│ Market Link                                                    │
│ auto_resolve_events() / manual resolve                         │
│ - upsert_link() -> v2_loop.db.event_market_links               │
│ - verified=true can score                                      │
│ - fuzzy below threshold becomes pending                        │
└──────────────┬────────────────────────────────────────────────┘
               │
               v
┌───────────────────────────────────────────────────────────────┐
│ Resolve Outcome                                                │
│ resolve_with_calibration()                                     │
│ - resolve_event() -> event_store.json outcome/calibration      │
│ - record_outcome() -> event_audit.jsonl outcome marker         │
│ - score_prediction()/void_prediction() -> predictions status   │
└──────────────┬────────────────────────────────────────────────┘
               │
               v
┌───────────────────────────────────────────────────────────────┐
│ Calibration / Trust                                            │
│ - calibration_summary(): act-only public score                 │
│ - segment_skill(category): resolved act/watch trust sample     │
└──────────────┬────────────────────────────────────────────────┘
               │
               v
┌───────────────────────────────────────────────────────────────┐
│ Next Decision Gate                                             │
│ freeze_prediction()                                            │
│ - diagnose(raw_edge, segment_skill(category), liquidity)       │
│ - decision = act/watch/skip                                    │
└──────────────┬────────────────────────────────────────────────┘
               │
               v
┌───────────────────────────────────────────────────────────────┐
│ Decision Report                                                │
│ /events/decisions/open                                         │
│ /events/{event_id}/decision                                    │
│ - reads open predictions                                       │
│ - joins event_store record                                     │
└───────────────────────────────────────────────────────────────┘
```

---

## 1. Scheduler

### 是否实际连通

是。

当前 scheduler 注册：

- market-layer morning scan
- market-layer evening resolve
- event-layer auto resolve
- event-layer discovery

`EVENT_DISCOVER_ENABLED` 默认是 true，因此事件层 discovery 默认会被注册并运行。

### 是否有数据落盘

Scheduler 本身不落盘。它触发的 downstream job 会落盘：

- Discover 写 `event_store.json` / `event_audit.jsonl` / `predictions`
- Resolve 写 `event_store.json` / `event_audit.jsonl` / `predictions` / `event_market_links`

### 是否可恢复

部分可恢复。

应用重启后 scheduler 会重新注册 job，但 job 本身没有 durable job cursor。错过的运行只依赖 APScheduler misfire 机制，不记录“上次处理到哪里”。

### 是否有 fail-closed 保护

有限。

job 内部 try/except 捕获异常并记录日志，避免 scheduler 崩溃。但这属于 fail-soft，不是严格 fail-closed。

### 是否存在静默失败

存在。

job 失败只写日志，不向上暴露状态。用户界面和 API 不一定知道某次 discovery / resolve 实际失败。

---

## 2. Discover

### 是否实际连通

是。

`discover_events()` 会：

1. 收集候选事件。
2. 分析事件。
3. 生成 `EventRecord`。
4. 调用 `_persist_events(fresh)`。

`_persist_events()` 再调用：

- `save_events(records)`
- `record_event(record)`
- `freeze_prediction(record)`

### 是否有数据落盘

是。

落盘目标：

- `event_store.json`
- `event_audit.jsonl`
- `v2_loop.db.predictions`

### 是否可恢复

部分可恢复。

事件 record 存在 JSON；audit 存在 JSONL；prediction 存在 SQLite。重启后可读取。

但 `_persist_events()` 不是事务：

```text
save_events 成功
record_event 失败
freeze_prediction 未执行
```

会留下 event 已保存但没有 audit/prediction 的半状态。

### 是否有 fail-closed 保护

不充分。

`freeze_prediction()` 对非 prediction market event 会 no-op，这是正确的 fail-closed。

但 `_persist_events()` 捕获整个持久化异常并吞掉，只记录 warning。调用方仍可能返回 discovery 结果，用户会以为事件已进入系统。

### 是否存在静默失败

存在。

`_persist_events()` 失败后不会抛出，discover API 仍可能返回事件。实际数据可能没有完整落盘。

---

## 3. Event

### 是否实际连通

是。

`event_store.json` 是事件层主记录，resolve、calibration feedback、decision report 都会读取它。

### 是否有数据落盘

是。

`event_store.save_events()` upsert record；`resolve_event()` 写 outcome / calibration。

### 是否可恢复

部分可恢复。

JSON 文件可恢复读取，写入使用 atomic replace。

但存在重大恢复风险：`save_events()` 重扫时只保留 tracking，不保留已有 outcome/calibration。

这会导致：

```text
resolved event
  -> rediscover same event_id
  -> save_events overwrites record
  -> outcome/calibration 丢失
  -> event 看起来又 unresolved
```

### 是否有 fail-closed 保护

部分有。

写路径用 Pydantic `EventRecord.model_validate()`，坏数据不会直接写入。

但对 terminal state 没有保护：已有 outcome 的 event 可以被未 resolved 的新 record 覆盖。

### 是否存在静默失败

存在。

read-only 路径使用 lenient read，JSON corrupt 时可能 fallback 空对象。虽然 write path 用 strict read，但展示/汇总可能表现为“没有数据”，而不是明确报错。

---

## 4. Market Link

### 是否实际连通

是，但只在 resolve 路径上连通，不在 freeze 路径上强制连通。

`auto_resolve_events()` 会 `upsert_link()`，manual resolve 也会写 verified manual link。

### 是否有数据落盘

是。

落盘到 `v2_loop.db.event_market_links`。

### 是否可恢复

是。

SQLite 持久化，重启后可读。

### 是否有 fail-closed 保护

部分有。

auto resolve 中：

- fuzzy match 低于 `AUTO_VERIFY_THRESHOLD` 时只记录 pending，不 resolve。
- verified link diverged 时标记 invalid，不按新 contract 评分。

但 `score_prediction()` 本身不检查 verified link。它依赖调用路径正确。如果未来有人直接调用 `score_prediction()`，可以绕过 link gate。

### 是否存在静默失败

存在。

auto resolve 对 source fetch failure 和 per-event resolve failure 都是 warning + continue。最终返回部分结果，但系统没有 durable error queue。

---

## 5. Freeze Prediction

### 是否实际连通

是。

`_persist_events()` 每次 fresh record 都调用 `freeze_prediction(record)`。

`freeze_prediction()` 读取：

- event record source
- event probability
- base_rate_category
- `segment_skill(category)`
- liquidity

然后写入 `predictions`。

### 是否有数据落盘

是。

写入 `v2_loop.db.predictions`。

### 是否可恢复

部分可恢复。

SQLite 数据可恢复。但当前语义是多版本 prediction，不是 One Event One Prediction。

### 是否有 fail-closed 保护

有一部分：

- 非 prediction market source 不 freeze。
- 无 contract_id 不 freeze。
- 无 ai/market probability 不 freeze。

不足：

- 不检查 event 是否已经 resolved。
- 不检查 event 是否已有 terminal prediction。
- 数据库没有 `UNIQUE(event_id)` 或 partial unique one-open 约束。

### 是否存在静默失败

存在。

如果 `freeze_prediction()` 在 `_persist_events()` 中失败，异常被 `_persist_events()` 捕获并吞掉。event 可能已经保存和 audited，但没有 prediction。

---

## 6. Resolve Outcome

### 是否实际连通

是。

resolve 路径：

```text
resolve_with_calibration()
  -> resolve_event()
  -> record_outcome()
  -> score_prediction() or void_prediction()
```

auto resolve 会从 resolved markets 拉取结果并匹配 event。

### 是否有数据落盘

是。

落盘到：

- `event_store.json`: outcome / calibration
- `event_audit.jsonl`: outcome snapshot
- `predictions`: scored / observed / voided
- `event_market_links`: link provenance

### 是否可恢复

部分可恢复。

各 store 单独可恢复，但没有跨 store transaction。

可能出现：

```text
event_store outcome 写入成功
record_outcome 失败
prediction 未 score
```

或者：

```text
event_store outcome 写入成功
prediction score 成功
audit outcome 失败
```

当前没有 repair job 对齐这些半状态。

### 是否有 fail-closed 保护

auto resolve 有：

- fuzzy pending 不 resolve
- diverged verified link -> invalid，不评分

manual resolve 没有严格 fail-closed，因为 manual 被视为人工验证。

不足：

- `resolve_with_calibration()` 不检查 event 是否已经有 outcome。
- 重复 manual resolve 会覆盖 outcome/calibration 并追加 outcome snapshot。

### 是否存在静默失败

存在。

auto resolve per-event 失败会 warning + continue；没有 retry queue。

---

## 7. Calibration

### 是否实际连通

有两套 calibration，连通程度不同。

### A. Prediction calibration / trust loop

实际连通。

路径：

```text
score_prediction()
  -> predictions.status = scored/observed
  -> segment_skill(category)
  -> diagnose()
  -> next freeze_prediction()
```

这是当前真正参与 decision gate 的闭环。

### B. Event calibration feedback

默认不连通。

`analyze_event()` 会记录 `calibration_components`，但只有 `CALIBRATION_FEEDBACK_ENABLED=true` 时才调用 `adjust_probability()` 修改 published probability。

默认配置下该逻辑不会参与系统闭环。

### 是否有数据落盘

有。

Prediction calibration 落在 `predictions`。

Event calibration 落在 `event_store.json` 的 `calibration` 和 `calibration_components`。

### 是否可恢复

部分可恢复。

数据可读，但 `event_store.save_events()` 可能覆盖 resolved calibration。

### 是否有 fail-closed 保护

有一定保护：

- invalid/void outcome 不进 event calibration。
- prediction calibration summary 只统计 act scored。
- `segment_skill()` 不统计 skip 和 superseded。

### 是否存在静默失败

存在。

`calibration_feedback_service._load_resolved_records()` 捕获异常并返回空列表。结果是 feedback 静默降级为 no-op。

---

## 8. Trust

### 是否实际连通

是。

`freeze_prediction()` 调用：

```text
diagnose(raw_edge, segment_skill(category), liquidity)
```

`segment_skill(category)` 读取 resolved act/watch predictions。

### 是否有数据落盘

Trust 本身不单独落盘。

每次 prediction freeze 会把当时的：

- trust
- adjusted_edge
- qualified
- segment_n
- segment_skill

冻结进 `predictions` row。

### 是否可恢复

可从 `predictions` 重算 segment_skill，也可读取历史 prediction 上冻结的 diagnosis 字段。

但如果 predictions 存在重复样本，trust 会被污染并持续影响后续 decision。

### 是否有 fail-closed 保护

有。

dormant category 不允许 act。样本不足时即使 raw edge 很大，也只能 watch 或 skip。

### 是否存在静默失败

逻辑上没有明显静默失败，但有“静默停滞”：

如果没有足够 resolved act/watch，category 会一直 dormant，系统不会报错，只是长期无法 act。

---

## 9. Decision Report

### 是否实际连通

是。

Decision report 读取：

- open predictions
- event_store record

并组装 report。

### 是否有数据落盘

没有。

Decision report 是 read-time projection，不落盘。

### 是否可恢复

可恢复，因为它可由 prediction + event record 重新计算。

### 是否有 fail-closed 保护

部分有。

没有 prediction 的 event 返回 404；open decisions 默认只列 act/watch，不列 skip。

不足：

- 如果 event_store 丢失或被覆盖，report 仍可能用 prediction alone 生成弱 report。
- 如果 invalid/resolved event 因 prediction 仍 open，可能出现在 opportunity surface；当前 `void_prediction()` 试图处理 invalid，但依赖 resolve 路径完整执行。

### 是否存在静默失败

部分存在。

report 组装对缺失 event record 容忍，会返回 minimal report。这对可用性好，但也可能掩盖 event/prediction 脱节。

---

## 闭环断点

### 断点 1：Discover 持久化不是事务

`save_events()`、`record_event()`、`freeze_prediction()` 分属 JSON、JSONL、SQLite，任何一步失败都会留下半状态。

### 断点 2：Event resolved 状态可能被重扫覆盖

这是最严重断点。它会破坏 auto resolve 幂等性，并可能导致同一 event 再次 freeze / resolve。

### 断点 3：Market Link 没有成为 score_prediction 的硬前置

auto resolve 路径使用 link gate，但 `score_prediction()` 本身不验证 link。未来新增调用方容易绕过 M0 gate。

### 断点 4：Prediction 既是 commitment 又是 trajectory

多版本 prediction 会让 trust 样本不再天然对应独立事件。

### 断点 5：Event calibration feedback 默认关闭

如果预期“calibration 会调整下一次概率估计”，当前默认不成立。真正生效的是 prediction trust loop，而不是 event probability calibration feedback。

---

## 数据丢失风险

1. `event_store.save_events()` 覆盖已有 outcome/calibration。
2. `event_audit.jsonl` compaction 会丢旧 probability snapshots。
3. `_persist_events()` 吞异常导致 event 返回给用户但未完整落盘。
4. JSON store 跨进程写入没有 OS-level lock，多进程可能丢更新。
5. SQLite 与 JSON 没有 transaction，崩溃或异常会导致半写状态。
6. `calibration_feedback_service` 读 store 失败时返回空历史，feedback 静默变 no-op。

---

## 永远不会触发或默认不会触发的逻辑

### 默认不会触发

`CALIBRATION_FEEDBACK_ENABLED` 默认 false，因此：

- `adjust_probability()` 默认不会改变 published probability。
- component weighting / base-rate shrinkage 默认不会参与下一次分析。

### 长期可能不会触发

act 决策可能长期不会触发：

- category 样本数低于 `CALIBRATION_FEEDBACK_MIN_SAMPLES` 时永远不能 act。
- 默认 min samples 是 8。
- auto verify 默认阈值是 1.0，非 exact match 会 pending，不 resolve。
- 如果 early predictions 多数是 skip，skip resolved 不进入 `segment_skill()`，category 样本不会增长。

### 表面存在但未形成强闭环

Decision report 已经连通，但它只是 projection，不反哺系统。它不构成闭环，只是闭环输出面。

---

## Dormant 状态无法毕业的风险

Dormant 毕业条件：

```text
segment_skill(category).n >= CALIBRATION_FEEDBACK_MIN_SAMPLES
```

`segment_skill()` 只计：

```text
status IN ('scored', 'observed')
AND decision IN ('act', 'watch')
AND base_rate_category = category
```

因此 category 毕业需要足够多 resolved watch/act predictions。

风险：

1. 如果 category 初期 raw edge 小，decision 是 skip，resolve 后不计入 segment_skill。
2. 如果 auto resolve 匹配不到 exact market，link 会 pending，不产生 resolved prediction。
3. 如果 event discovery 没有持续运行，样本不会积累。
4. 如果 resolved event 被重扫覆盖 outcome，样本状态可能混乱。
5. 如果 category 太细或事件分布稀疏，永远达不到 8 个样本。

判断：

系统理论上能从 dormant 毕业，因为 watch rows 会在 resolve 后以 `observed` 进入 `segment_skill()`。但在真实运行中，毕业依赖足够多“已验证且可解析的 watch 级别事件”。这个条件偏强，存在长期 dormant 的现实风险。

---

## 分环节汇总表

| 环节 | 实际连通 | 数据落盘 | 可恢复 | Fail-closed | 静默失败 |
|---|---|---|---|---|---|
| Scheduler | 是 | 否 | 部分 | 弱 | 是 |
| Discover | 是 | 是 | 部分 | 部分 | 是 |
| Event | 是 | 是 | 部分 | 部分 | 是 |
| Market Link | 是 | 是 | 是 | 部分 | 是 |
| Freeze Prediction | 是 | 是 | 部分 | 部分 | 是 |
| Resolve Outcome | 是 | 是 | 部分 | 部分 | 是 |
| Calibration | 部分 | 是 | 部分 | 部分 | 是 |
| Trust | 是 | 间接 | 部分 | 是 | 静默停滞 |
| Decision Report | 是 | 否 | 是 | 部分 | 部分 |

---

## 最终判断

系统已经有一条可工作的反馈链：

```text
resolved prediction -> segment_skill -> future diagnosis -> decision gate
```

但它还不是可靠、可审计、可长期运行的数据闭环。

优先修复顺序：

1. `save_events()` 必须保留已有 outcome/calibration，防止 resolved event 回退。
2. `resolve_with_calibration()` 对已有 outcome 默认幂等。
3. `predictions` 恢复 One Event One Prediction，或至少加 one-open unique。
4. `score_prediction()` 应验证 event 有 verified market link，不能只依赖调用方。
5. `_persist_events()` 不应吞掉关键持久化失败，至少要返回 partial persistence 状态。
6. 为 dormant 毕业设计可观测指标：每个 category 的 watch/act resolved count、pending link count、skip count、distance-to-graduation。

只有这些补上后，系统才算真正形成“可恢复、可审计、可毕业”的数据闭环。



# 第三部分 当前存储设计审计

日期：2026-06-19  
范围：`event_store.json`、`event_audit.jsonl`、`v2_loop.db` 中的 `event_market_links` / `predictions`，以及 freeze / resolve / trust 统计路径。  
审查重点：One Event One Prediction、多版本 prediction、重复冻结、重复 resolve、trust 统计污染、未来迁移障碍。  

## 总体结论

当前存储设计不符合严格的 **One Event One Prediction**。

实际状态是：

- `event_store.json` 保存 mutable event record。
- `event_audit.jsonl` 保存概率/结果快照，但会 compaction，不是严格永久 append-only。
- `event_market_links` 存在 SQLite 中，按 `(event_id, contract_id)` 去重。
- `predictions` 存在 SQLite 中，但已经移除 `event_id UNIQUE`，允许一个 event 多条 prediction。
- `prediction_store` 只保证“最多一个 open prediction”的应用层语义，不保证“一个 event 只有一个 prediction”。

最大风险有三个：

1. `predictions` 表已经支持隐式多版本 prediction：`open / superseded / scored / observed / voided`。
2. `event_store.save_events()` 重扫时只保留 `tracking`，不保留已有 `outcome` / `calibration`，可能把已 resolved event 覆盖回 unresolved。
3. JSON store、audit log、SQLite prediction/link store 之间没有事务边界，存在跨存储部分成功导致的不一致。

---

## 数据模型图

当前实际模型：

```text
event_store.json
└── event_id
    ├── first_seen
    ├── last_updated
    └── record
        ├── event_id
        ├── event_title
        ├── probability
        ├── source
        ├── legacy_analysis
        ├── tracking
        ├── outcome?          mutable, may be overwritten
        └── calibration?      mutable, may be overwritten

event_audit.jsonl
└── append/rewrite log lines
    ├── probability snapshots
    └── outcome snapshots: kind = "outcome"

v2_loop.db
├── event_market_links
│   ├── id PK
│   ├── event_id
│   ├── contract_id
│   ├── market_name
│   ├── resolution_criteria
│   ├── verified
│   └── UNIQUE(event_id, contract_id)
│
└── predictions
    ├── id PK
    ├── event_id
    ├── contract_id
    ├── base_rate_category
    ├── ai_probability
    ├── market_probability
    ├── raw_edge
    ├── trust / adjusted_edge
    ├── decision: act | watch | skip | tracked
    ├── status: open | scored | observed | voided | superseded
    ├── actual_outcome
    ├── brier_score
    └── no UNIQUE(event_id)
```

目标模型如果坚持 One Event One Prediction，应收敛为：

```text
events
└── one mutable event record per event_id

event_market_links
└── verified ground-truth link(s), but with clear uniqueness policy

predictions
└── exactly one committed prediction per event_id
    ├── event_id UNIQUE
    ├── status transitions in-place
    └── no superseded / no prediction history

event_audit
└── probability trajectory only, not prediction commitment history
```

---

## 1. One Event One Prediction 检查

结论：不符合。

证据：

- `predictions` schema 没有 `UNIQUE(event_id)`。
- `_migrate()` 明确检测旧的 `event_id UNIQUE` 并重建表移除它。
- `get_predictions(event_id)` 返回一个 event 的完整 prediction history。
- 测试 `test_prediction_store.py` 明确保护 “M3 append-only multi-row ledger”。

当前设计最多只保证：

```text
One Event -> At Most One Open Prediction
```

但不保证：

```text
One Event -> One Prediction
```

架构判断：

如果项目当前原则是 One Event One Prediction，则现有 `predictions` 表设计必须回退。否则后续所有 trust、calibration、opportunity surface 都会在“多版本 prediction”语义上继续增长。

---

## 2. 隐式多版本 Prediction 检查

结论：存在，而且是显式实现。

多版本来源：

- 同一 event 首次 `freeze_prediction()` 产生 `open` row。
- 重扫时如果 decision 变化，旧 row 更新为 `superseded`，新 row 插入为 `open`。
- 重扫时即使 decision 不变，只要 `ai_probability` 或 `adjusted_edge` 超过 `PREDICTION_RESNAPSHOT_DELTA`，也会产生新 row。
- resolve 后 open row 变成 `scored` 或 `observed`，后续如果同一 event 又被重扫，因为不存在 open row，会再插入新 open row。

这意味着当前 prediction 表同时承担了两个角色：

- commitment store
- prediction trajectory ledger

这两个角色冲突。Commitment 应该回答“我们承诺的是哪一次判断”；trajectory 应该回答“概率随时间如何变化”。现在两者混在 `predictions` 里。

建议：

- 如果坚持 One Event One Prediction：删除 `superseded` 语义，恢复 `event_id UNIQUE`。
- 如果未来真的要多版本 ledger：必须引入明确版本模型，例如 `prediction_series_id`、`version_no`、`is_current`、`valid_from`、`valid_to`，并明确哪些版本进入 trust，哪些只是历史。

---

## 3. 重复冻结风险

结论：存在 P0 级重复冻结风险。

### 风险 A：已 resolved event 可能被重扫覆盖回 unresolved

`event_store.save_events()` 只保留已有 `tracking`：

```text
existing_tracking = existing.record.tracking
record["tracking"] = existing_tracking
```

但它不保留：

- `outcome`
- `calibration`
- 已 verified link 的 resolve 状态

因此同一个 `event_id` 如果在 discovery 中再次出现，新 record 会覆盖旧 record，可能把已 resolved event 变回 unresolved。

后果：

- `auto_resolve_events()` 通过 `record.get("outcome") is not None` 判断是否跳过。
- 如果 outcome 被重扫覆盖丢失，事件会重新进入可 resolve 集合。
- `_persist_events()` 会再次调用 `freeze_prediction(record)`。
- 因为原 prediction 可能已经是 `scored` / `observed`，不存在 open row，`freeze_prediction()` 会插入新的 open prediction。

这是当前存储设计里最危险的重复冻结路径。

建议：

`save_events()` 应保留 user/system-owned terminal fields：

- `tracking`
- `outcome`
- `calibration`

至少应在 existing record 有 `outcome` 时禁止 incoming unresolved record 覆盖 outcome。

### 风险 B：prediction 表没有数据库级“最多一个 open”约束

应用层通过写锁和查询 open row 来避免重复 open：

```text
SELECT ... WHERE event_id=? AND status='open'
```

但数据库层没有约束：

```sql
UNIQUE(event_id) WHERE status = 'open'
```

当前 `_WRITE_LOCK` 只是 Python 进程内锁。如果部署成多进程、多个 worker、或有脚本同时写同一个 SQLite 文件，仍可能出现两个 open predictions。

建议：

如果保留多版本设计，至少加：

```sql
CREATE UNIQUE INDEX uq_predictions_one_open
ON predictions(event_id)
WHERE status = 'open';
```

如果回到 One Event One Prediction，则应直接加：

```sql
CREATE UNIQUE INDEX uq_predictions_event
ON predictions(event_id);
```

---

## 4. 重复 Resolve 风险

结论：存在。

### 手动 resolve 不是幂等的

`resolve_with_calibration()` 开头只检查 event 是否存在，不检查是否已有 outcome。

重复调用会发生：

- `event_store.resolve_event()` 覆盖旧 outcome / calibration。
- `event_audit_service.record_outcome()` 再 append 一个 outcome snapshot。
- `score_prediction()` 对已无 open prediction 的事件返回 None，因此 prediction 不会重复 scored。

这对 prediction scoring 层部分幂等，但对 event_store 和 audit log 不幂等。

更严重的是，如果前述“重扫覆盖 outcome”发生，重复 resolve 还可能重新 score 新 open prediction。

建议：

`resolve_with_calibration()` 应在进入写路径前检查：

```text
if record.outcome exists:
    return existing entry or require explicit force=True
```

并且 `record_outcome()` 应避免同一个 event 重复 outcome snapshot，或至少将重复 resolve 视为 explicit correction。

### auto resolve 有幂等保护，但依赖 JSON outcome

`auto_resolve_events()` 会跳过已有 outcome 的 event。这是正确方向。

但该保护依赖 `event_store.json` 里的 `record.outcome`。如果 outcome 被重扫覆盖丢失，auto resolve 幂等性失效。

---

## 5. Trust 统计污染检查

结论：当前查询口径基本正确，但会被重复冻结/重复 resolve 风险污染。

当前正确点：

- `calibration_summary()` 只统计 `status='scored' AND decision='act'`。
- `segment_skill(category)` 统计 `status IN ('scored', 'observed') AND decision IN ('act', 'watch')`。
- `skip` 不进入 trust。
- `superseded` 不进入 trust，因为 status 不匹配。
- dormant segment 依赖样本数，不会直接 act。

污染风险：

1. 同一个 event 多次 freeze + resolve 后，会产生多个 scored/observed rows，导致一个真实事件在同一 category 中被重复计数。
2. 同一个 event 重扫后 category 可能变化，多个 prediction 版本可能污染多个 category。
3. `observed` watch rows 进入 `segment_skill()` 是当前 M2 设计允许的，但如果 watch rows 来自重复 freeze，而不是独立事件，它会加速 category 离开 dormant。
4. `score_prediction()` 没有 join `event_market_links` 的 verified 状态，依赖 resolve path 调用正确；如果未来有其他调用方直接调用 `score_prediction()`，trust 会被未验证 outcome 污染。

建议：

- 在 trust 统计中以 `event_id` 去重，或从源头保证 `event_id UNIQUE`。
- `score_prediction()` 只应对 verified link 的 event 生效，或者至少 assert 当前 event 有 verified link。
- 如果保留多版本 ledger，必须定义“一个 event 只能贡献一个 trust sample”的规则。

---

## 6. Append-only 合规性检查

当前各 store 的 append-only 状态：

| Store | 当前行为 | 是否严格 append-only |
|---|---|---|
| `event_store.json` | upsert mutable record | 否 |
| `event_audit.jsonl` | append outcome/probability，但会 compaction rewrite | 否，最多是 bounded audit log |
| `event_market_links` | upsert link，verified 可改 | 否 |
| `predictions` | insert row，但 status 会 update 为 superseded/scored/observed/voided | 否 |

判断：

当前系统没有真正严格 append-only 的核心 store。`event_audit.jsonl` 最接近 append-only，但 compaction 会重写并丢弃旧 probability snapshots。

这不一定是 bug，但文档和架构语义必须准确：

- 不能把 `predictions` 称为严格 append-only ledger，因为它会 update status。
- 不能把 `event_audit.jsonl` 称为永久审计日志，因为它会 compaction。
- 如果未来需要合规级 append-only，需要单独设计不可变 fact table 或不可变 JSONL，不能复用当前 bounded audit log。

---

## 7. JSON Store / SQLite Store 风险评估

### JSON Store 风险

优点：

- 简单。
- 本地开发和小规模运行成本低。
- `write_json_atomic()` 使用临时文件 + `os.replace`，避免部分写坏文件。
- read-modify-write 路径使用 strict read，避免 corrupt JSON 被空对象覆盖。

风险：

- `locked_file()` 是进程内 `threading.RLock`，不是跨进程文件锁。多进程部署或多个脚本同时写同一 JSON 文件时，仍可能丢更新。
- `event_store.json` 是全量读写，事件数量增长后写放大会明显。
- `save_events()` 的 merge policy 不完整，目前只保留 tracking，不保留 outcome/calibration。
- `event_store.json` 与 SQLite 没有事务一致性。
- `event_audit.jsonl` compaction 会丢旧轨迹，不适合作为永久审计事实源。

### SQLite Store 风险

优点：

- 比 JSON 更适合唯一约束和状态查询。
- WAL + short-lived connection 适合当前体量。
- 写路径有进程内 `_WRITE_LOCK`，单进程内基本安全。

风险：

- `PRAGMA foreign_keys=ON` 但 schema 没有声明 FK，因此没有实际 referential integrity。
- `predictions.event_id` 没有 FK 指向 event store，因为 event store 在 JSON 中。
- `event_market_links.event_id` 也没有 FK。
- `predictions` 缺少 `UNIQUE(event_id)` 或 partial unique open 约束。
- `event_market_links` 允许同一 event 多个 verified links，也允许同一 contract linked 到多个 events。
- 没有 schema version table，迁移靠 ad hoc `_migrate()`。
- `_INITIALIZED` 以 path 缓存 schema 初始化；同一进程内如果 schema 代码改变，不会重新迁移已初始化 path。
- SQLite 与 JSON 分裂存储使未来迁移到统一 relational model 时需要 reconcile 数据。

---

## 唯一约束建议

如果坚持 One Event One Prediction，建议：

```sql
CREATE UNIQUE INDEX uq_predictions_event
ON predictions(event_id);
```

如果短期保留多版本 prediction，最低限度也应加：

```sql
CREATE UNIQUE INDEX uq_predictions_one_open
ON predictions(event_id)
WHERE status = 'open';
```

event-market link 建议：

```sql
-- 同一 event 最多一个 verified link，除非产品明确支持多市场 resolve。
CREATE UNIQUE INDEX uq_event_one_verified_link
ON event_market_links(event_id)
WHERE verified = 1;

-- 同一真实 contract 不应绑定到多个 event，避免重复 resolve 同一市场。
CREATE UNIQUE INDEX uq_contract_one_event
ON event_market_links(market_name, contract_id)
WHERE contract_id <> '';
```

字段合法性建议：

```sql
CHECK (ai_probability >= 0 AND ai_probability <= 100)
CHECK (market_probability >= 0 AND market_probability <= 100)
CHECK (actual_outcome IS NULL OR (actual_outcome >= 0 AND actual_outcome <= 100))
CHECK (decision IN ('act', 'watch', 'skip', 'tracked'))
CHECK (status IN ('open', 'scored', 'observed', 'voided', 'superseded'))
```

如果回退到 One Event One Prediction，`superseded` 应从合法状态中移除。

---

## 幂等性检查

| 操作 | 当前幂等性 | 风险 |
|---|---|---|
| `save_events()` | 对 event_id upsert，但只保留 tracking | 会丢 outcome/calibration |
| `record_event()` | 不幂等，每次 append | 重复扫描会污染 trend，虽缓存路径部分避免 |
| `freeze_prediction()` | 对 open row 有部分幂等 | resolved 后可再次 freeze；多进程可重复 open |
| `score_prediction()` | 对 terminal prediction 幂等 | 只要没有 open row就 no-op |
| `void_prediction()` | 对 terminal prediction 幂等 | 只要没有 open row就 no-op |
| `resolve_with_calibration()` | 不幂等 | 会覆盖 outcome/calibration 并追加 outcome snapshot |
| `auto_resolve_events()` | 依赖 outcome 跳过 | outcome 丢失时失效 |
| `upsert_link()` | 对 `(event_id, contract_id)` 幂等 | contract_id 为空或多 verified links 风险 |

优先修复建议：

1. `save_events()` 保留 outcome/calibration。
2. `resolve_with_calibration()` 对已有 outcome 默认 no-op。
3. `predictions` 增加 event-level 或 open-level unique 约束。
4. `event_market_links` 明确 verified link 的唯一策略。

---

## 未来迁移障碍

当前设计对未来迁移的主要障碍：

1. JSON event store 与 SQLite loop store 分裂，无法用数据库 FK 保护一致性。
2. `predictions` 已经混合 commitment 与 trajectory，未来拆分会很痛。
3. `event_audit.jsonl` 既做 trend source，又做 outcome marker，但会 compaction，不适合长期事实表。
4. `event_id` 基于 question hash，问题文本轻微变化会生成新 event，缺少 canonical event identity。
5. `event_market_links` 允许一个 contract 对多个 event，没有防重复 resolve 的全局约束。
6. `legacy_analysis` 被整体塞进 event record，长期会让 schema 边界越来越模糊。
7. 没有 schema version / migration ledger，未来迁移难以审计和回滚。

建议的迁移方向：

```text
短期：
  保持 JSON event store，但修复 merge/idempotency。
  SQLite predictions 恢复 one-event-one-prediction。
  增加关键唯一约束和 CHECK。

中期：
  将 event outcome 从 mutable JSON field 抽为 SQLite outcomes 表。
  将 event identity/link/prediction/outcome 放入同一个 SQLite transaction。

长期：
  统一 event records、market links、predictions、outcomes、audit facts 到 relational schema。
  JSON 只作为 cache/export，不作为 source of truth。
```

---

## 最终判断

当前存储设计能跑，但还不是可靠的 feedback-loop storage。

最需要立刻处理的是：

1. 恢复 One Event One Prediction 或至少数据库级保证 one open prediction。
2. 修复 `save_events()` 覆盖 resolved outcome/calibration 的问题。
3. 让 `resolve_with_calibration()` 默认对已 resolved event 幂等。
4. 防止同一 event 或同一 contract 产生重复 trust sample。
5. 明确 `event_audit.jsonl` 只是 bounded trend log，不是永久 append-only ledger。

如果这些不修，后续即使算法和界面正确，trust/calibration 也会被重复冻结、重复解析和跨存储不一致逐渐污染。


# 第四部分 开源发布前可删除内容审查

日期：2026-06-19  
范围：全仓库，重点审查不再被调用的代码、废弃接口、重复实现、历史兼容层、永远不会执行的分支、过度抽象 Service、无意义配置项。  
标准：按准备开源发布的标准，降低首屏复杂度、维护成本和误导性入口。  

## 总体结论

当前仓库有两套并行产品线：

```text
当前主线：
Event Intelligence / Event Loop
  -> /api/events/*
  -> event_store.json / event_audit.jsonl / v2_loop.db
  -> Prediction / Trust / Decision Report
  -> Next.js frontend

历史兼容线：
Legacy Market Scanner
  -> /api/scan / /api/analysis / /api/resolve / /api/calibration / /api/trades / /api/signals/accuracy / /api/backtest
  -> agent_memory.json / analysis_audit.jsonl / market_cache.json
  -> static/index.html / static/index_zh.html
```

如果开源版本定位是 README 中描述的“事件情报与概率变化分析平台”，高收益删除点不是单个小函数，而是整条 Legacy Market Scanner 兼容线。它仍然能运行，但已经不是产品主路径，并且会让开源用户误以为项目同时维护交易日志、旧信号扫描器、旧校准、旧多 Agent 深扫、经典 dashboard。

删除建议分三类：

- **A 类：删除后不影响当前主线功能**，可以直接清理。
- **B 类：删除后降低复杂度，但会移除 legacy API / classic dashboard**，适合开源发布前做一次破坏性清理。
- **C 类：需要先替换或确认产品取舍**，不建议马上删除，但应列为瘦身候选。

---

## 收益排序

### 1. 删除本地工作垃圾和 AI 工具状态

**收益：最高。风险：低。类型：删除后不影响功能 / 减少开源污染。**

候选：

```text
.diff_temp.txt
debug.log
.qoder/
.workbuddy/
.claude/
.impeccable/
backup-20260612-181108.tar.gz
HANDOFF.md
```

依据：

- `git status --short` 中 `.diff_temp.txt`、`debug.log`、`.qoder/`、`.workbuddy/` 是未跟踪文件。
- 这些不是运行时代码，也不是用户文档。
- `.gitignore` 已忽略 `*.tar.gz`，但根目录仍存在备份包，开源打包时不应带出。
- `.gitignore` 只忽略了 `backend/.claude/`、`backend/.impeccable/`，没有明确忽略根目录 `.claude/`、`.impeccable/`、`.qoder/`、`.workbuddy/`。

建议：

1. 从发布包中删除这些文件/目录。
2. `.gitignore` 增加：

```text
.claude/
.impeccable/
.qoder/
.workbuddy/
*.log
.diff_temp.txt
```

影响：

- 不影响后端、前端、测试、文档。
- 直接降低泄露内部过程、临时 diff、agent 状态的风险。

---

### 2. 删除 `backend/setup_v3.py`

**收益：高。风险：低。类型：删除后不影响功能 / 减少维护成本。**

文件：

```text
backend/setup_v3.py
```

依据：

- 没有被应用导入。
- 不是当前 README 的启动路径。
- 里面检查的是旧 v0.3.0 资产，例如：

```text
app/services/signal_tracker.py
app/api/routes/signal_accuracy.py
app/api/routes/trades.py
app/services/trade_journal_service.py
static/index.html
```

- 它还检查 classic dashboard 中“交易日志 tab”“signal accuracy”等旧产品能力，会误导开源用户以为这些仍是主线验收标准。

建议：

- 直接删除。
- 如果还需要发布前检查，另建一个面向当前主线的 `scripts/check_release.py`，只检查 `/api/events`、frontend build、核心测试。

影响：

- 不影响运行。
- 减少历史版本脚本与当前 README 的冲突。

---

### 3. 删除根级 `backend/test_live_integration.py`

**收益：高。风险：低到中。类型：重复实现 / 减少维护成本。**

文件：

```text
backend/test_live_integration.py
```

依据：

- 仓库已有正式测试文件：

```text
backend/tests/test_integration_live.py
```

- 根级 `test_live_integration.py` 是手动脚本，直接调用真实 LLM：

```text
analyze_event_question(...)
```

- 开源发布时，这类脚本容易让用户误跑真实 API 成本，也不适合默认测试入口。

建议：

- 删除根级脚本。
- 如确实需要 live smoke test，把最小示例放入文档，明确标注需要 API key 和会产生费用。

影响：

- 不影响 `python -m unittest discover -s tests`。
- 不影响应用运行。

---

### 4. 删除 Legacy Trade Journal

**收益：高。风险：中。类型：废弃接口 / 历史兼容层 / 降低复杂度。**

候选：

```text
backend/app/api/routes/trades.py
backend/app/services/trade_journal_service.py
backend/tests/test_trade_journal_service.py
```

同时修改：

```text
backend/app/api/router.py
backend/app/main.py
README.md
docs/user/*
```

依据：

- 前端 `frontend/src/lib/api.ts` 没有调用 `/api/trades/*`。
- 当前产品文档强调“不是自动交易机器人”。
- `trades.py` 自己已经标注：

```text
Compatibility-only surface from the pre-EIP codebase;
not part of the Event Intelligence product direction.
```

- Trade Journal 写独立 JSON，与当前 event prediction / trust / decision report 闭环无关。

删除后不影响：

- `/api/events/*`
- discovery / resolve / prediction store
- Decision Report
- Next.js 当前 UI

会影响：

- 旧 classic dashboard 如果仍使用交易日志。
- 直接调用 `/api/trades/*` 的历史用户。

建议：

- 开源首版若要保持产品边界清晰，应删除。
- 若担心破坏兼容，至少从默认 router 移除，并把它放到 `legacy/` 或单独分支。

---

### 5. 删除 Legacy Signal Accuracy

**收益：高。风险：中。类型：废弃接口 / 无主线调用。**

候选：

```text
backend/app/api/routes/signal_accuracy.py
backend/app/services/signal_tracker.py
backend/tests/test_signal_audit_service.py   # 仅在删除旧 signal audit 时一起评估
```

同时修改：

```text
backend/app/api/router.py
backend/app/main.py
backend/setup_v3.py   # 若尚未删除
```

依据：

- `rg` 结果显示 `get_signal_accuracy()` 只被 `signal_accuracy.py` 路由调用。
- Next.js 前端没有调用 `/api/signals/accuracy`。
- 数据源是 `analysis_audit.jsonl`，属于 legacy market scanner，不是 event loop。

删除后不影响：

- event calibration
- prediction calibration
- trust segment skill
- current frontend

会影响：

- classic dashboard 中旧 signal accuracy 展示。
- 依赖 `/api/signals/accuracy` 的外部调用。

建议：

- 与 Legacy Scanner 一起删除。
- 不建议单独保留，因为它依赖旧 audit 格式，开源后会制造两套 accuracy 口径。

---

### 6. 删除 Legacy Scanner API 与 Deep Multi-Agent 扫描

**收益：很高。风险：中到高。类型：历史兼容层 / 过度抽象 / 重复产品线。**

候选：

```text
backend/app/api/routes/scanner.py
backend/app/agents/
backend/app/memory/reputation_engine.py
```

相关旧存储：

```text
backend/app/memory/agent_memory.py
backend/app/memory/market_memory.py
```

相关旧测试：

```text
backend/tests/test_probability_agent.py
backend/tests/test_signal_audit_service.py
```

依据：

- `scanner.py` 文件头已经声明是 legacy prediction-market scanner。
- Next.js 当前前端没有调用 `/api/scan/*`。
- Deep scanner 只在 `scanner.py` 的 `/deep` 使用 `AgentOrchestrator`。
- `AgentOrchestrator` 是 8 个 Agent 的串并行流水线：

```text
NarrativeAgent
ProbabilityAgent
ContrarianAgent
CrowdAgent
FundamentalAgent
ManipulationAgent
RiskAgent
JudgeAgent
SignalAgent
```

- 这套体系维护成本很高，但当前 event loop 的主分析路径是：

```text
event_intelligence_service -> ai_analysis_service -> probability_engine_service
```

不是 deep multi-agent scanner。

删除后不影响：

- `/api/events/discover`
- `/api/events/analyze`
- event source discovery
- prediction freeze / resolve / trust
- current Next.js UI

会影响：

- `/api/scan/*`
- classic dashboard
- scheduler 的旧 morning market scan
- 旧 `agent_memory.json` 和 `market_cache.json` 闭环

必要联动：

```text
backend/app/main.py
  - 删除 app.include_router(scanner.router, prefix="/api/scan", ...)

backend/app/core/scheduler.py
  - 删除 _job_morning_scan
  - 删除 morning_scan@07:00UTC 注册

backend/app/api/router.py / main.py API overview
  - 删除 scan/debug/cache/deep 文档
```

建议：

- 若开源版只维护 Event Intelligence，整块删除。
- 如果还想保留旧 scanner，至少把它移到 `backend/app/legacy/`，并默认不注册路由和 scheduler。

---

### 7. 删除 Legacy Market Resolve 与 Legacy Market Calibration

**收益：很高。风险：中到高。类型：重复实现 / 未来维护成本。**

候选：

```text
backend/app/api/routes/resolve.py
backend/app/api/routes/calibration.py
backend/app/services/auto_resolve_service.py
backend/app/services/calibration_service.py
backend/app/services/analysis_audit_service.py
backend/app/memory/agent_memory.py
```

注意保留或拆分：

```text
backend/app/services/polymarket_history_service.py
```

原因：`event_resolve_service.py` 仍使用其中的 `fetch_resolved_markets()` 作为 event auto-resolve 的 Polymarket resolved source。不能整文件无脑删除，除非先把 resolved-market fetch 函数迁移到 event source 层。

依据：

- `/api/resolve/*` 处理的是 `agent_memory.json / analysis_audit.jsonl`，不是 event store。
- `/api/calibration/*` 处理的是 legacy market-layer calibration。
- 当前主线已有：

```text
POST /api/events/{event_id}/resolve
POST /api/events/resolve/auto
GET  /api/events/calibration
GET  /api/events/predictions/calibration
```

- 两套 resolve/calibration 同时存在，会让开源用户难以理解应该看哪一个。

删除后不影响：

- event resolve
- event calibration
- prediction calibration
- decision report

会影响：

- `/api/resolve/*`
- `/api/calibration/*`
- `frontend/src/lib/api.ts` 当前 `eventsApi.health()` 调用 `/calibration/summary`，如果删除旧 calibration，需要改为 event-layer health 或新增 `/events/summary`。
- scheduler 的 `_job_evening_resolve`。

必要联动：

```text
backend/app/core/scheduler.py
  - 删除 _job_evening_resolve
  - 删除 evening_resolve@22:00UTC 注册

frontend/src/lib/api.ts
  - eventsApi.health 不再调用 /calibration/summary

backend/app/main.py
  - 删除 API overview 中 legacy resolve/calibration
```

建议：

- 与 Legacy Scanner 同一批删除。
- 如果短期不能删除，至少在 OpenAPI 文档中隐藏 legacy routes，避免开源用户把它当主线 API。

---

### 8. 删除 Legacy Manual Analysis API

**收益：中高。风险：中。类型：重复接口 / 历史兼容层。**

候选：

```text
backend/app/api/routes/analysis.py
backend/app/models/analysis.py
```

依据：

- 当前 Next.js “人工分析”页面调用的是：

```text
POST /api/events/analyze
```

不是：

```text
POST /api/analysis/
```

- `/api/analysis/` 返回 legacy market-analysis dict shape，并写 `analysis_audit.jsonl`。
- 当前主线事件分析已经由 `event_intelligence_service.analyze_event_question()` 封装。

删除后不影响：

- 当前前端人工分析页。
- event store / event audit。

会影响：

- 旧 API 用户。
- 旧 `analysis_audit.jsonl` 统计。

建议：

- 与 Legacy Market Calibration 一起删除。
- 如果保留，也应改名为 `/api/legacy/analysis` 并默认不出现在 README 快速开始中。

---

### 9. 删除 Classic Static Dashboard

**收益：中高。风险：中。类型：重复 UI / 历史兼容层。**

候选：

```text
backend/static/index.html
backend/static/index_zh.html
```

同时修改：

```text
backend/app/main.py
README.md
docs/user/QUICK_START.md
docs/user/USER_GUIDE.md
docs/user/中文使用教程.md
```

依据：

- 当前产品 UI 是 `frontend/` Next.js 静态导出，由 FastAPI 挂载到 `/`。
- classic dashboard 通过 `/dashboard`、`/dashboard/zh`、`/dashboard_zh` 暴露。
- README 仍把 classic dashboard 作为访问入口之一，这会扩大开源用户的认知负担。
- classic dashboard 历史上依赖 `/api/scan`、`/api/trades`、`/api/signals/accuracy` 等旧能力。

删除后不影响：

- Next.js root app。
- 当前 event pages / history / decisions。

会影响：

- `/dashboard` 兼容入口。
- 旧截图或旧教程。

建议：

- 如果删除 Legacy Scanner，应同步删除 classic dashboard。
- 如果暂不删除，至少 README 中把 classic dashboard 标记为 legacy，不作为推荐入口。

---

### 10. 删除 Legacy Backtest Route，保留 resolved market fetch

**收益：中。风险：低到中。类型：废弃接口 / 拆分保留。**

候选：

```text
backend/app/api/routes/backtest.py
```

谨慎处理：

```text
backend/app/services/polymarket_history_service.py
```

依据：

- Next.js 当前前端没有调用 `/api/backtest/*`。
- `backtest.py` 自己标注是 legacy route。
- 但 `event_resolve_service.py` 仍导入：

```text
from app.services.polymarket_history_service import fetch_resolved_markets
```

因此不能把 `polymarket_history_service.py` 作为整体删除。

建议：

1. 删除 `/api/backtest/*` route。
2. 将 `polymarket_history_service.fetch_resolved_markets()` 改名或迁移为 event source resolved fetch。
3. 删除 `get_backtest_baseline()` 和 backtest-only 测试，前提是没有文档或外部流程依赖。

---

### 11. 删除或合并旧市场配置项

**收益：中。风险：取决于是否删除 legacy line。类型：无意义配置项 / 历史兼容。**

候选配置：

```text
MARKET_SCAN_LIMIT
MEMORY_FILE
PREDICTION_RESNAPSHOT_DELTA
EDGE_STALE_HOURS
CALIBRATION_FEEDBACK_ENABLED
OPEN_WEB_EXTRACTION_MODEL
CROSS_VALIDATION_MODEL
EMBEDDING_MODEL
POLYMARKET_CRYPTO_FETCH_ENABLED
```

判断：

- `MARKET_SCAN_LIMIT`：当前 `rg` 只发现定义，未看到主代码使用。可删除。
- `MEMORY_FILE`：legacy `agent_memory`、`market_memory`、`analysis_audit_service` 依赖。删除 legacy line 后可删除；否则不能删。
- `PREDICTION_RESNAPSHOT_DELTA`：当前 prediction multi-version 行为依赖。若路线图坚持 One Event One Prediction，应删除该配置和 resnapshot 逻辑。
- `EDGE_STALE_HOURS`：M5 fresh edge 使用；如果开源版保留 M5 decision surface，则不能删。
- `CALIBRATION_FEEDBACK_ENABLED`：event probability feedback 默认关闭，但仍有测试和服务。若开源主线只保留 trust feedback，建议删除或移到 experimental。
- `OPEN_WEB_EXTRACTION_MODEL`、`CROSS_VALIDATION_MODEL`、`EMBEDDING_MODEL`：都是 opt-in 增强能力。不是死代码，但会增加安装/配置理解成本；开源首版可考虑移到 “experimental features” 文档，而不是删除。
- `POLYMARKET_CRYPTO_FETCH_ENABLED`：默认关闭的补丁型采集逻辑。若源策略保持简单，可删除；若要保留多类别覆盖，则保留。

建议优先删除：

```text
MARKET_SCAN_LIMIT
```

建议条件删除：

```text
MEMORY_FILE
PREDICTION_RESNAPSHOT_DELTA
CALIBRATION_FEEDBACK_ENABLED
```

---

### 12. 删除 legacy market cache

**收益：中。风险：随 scanner 删除而降低。类型：历史兼容层。**

候选：

```text
backend/app/memory/market_memory.py
```

依据：

- 当前 event flow 使用的是：

```text
backend/app/memory/event_cache.py
```

- `market_memory.py` 被 legacy scanner、legacy scheduler morning scan、deep scanner 使用。
- 如果删除 `/api/scan` 和 `_job_morning_scan`，它就没有主线用途。

建议：

- 不单独删。
- 作为 Legacy Scanner 删除批次的一部分删除。

---

### 13. 删除重复或过时文档

**收益：中。风险：低。类型：减少开源认知负担。**

候选：

```text
docs/user/MILESTONE1_CODE_REVIEW.md
docs/user/MILESTONE3_CODE_REVIEW.md
docs/user/MILESTONE4_CODE_REVIEW.md
docs/user/MILESTONE5_CODE_REVIEW.md
docs/user/POST_M5_OPTIMIZATION_CODE_REVIEW.md
docs/user/FULL_CODE_REVIEW_2026-06-19.md
docs/Review-doc/*
docs/archive/*
```

判断：

- 这些对内部开发有价值，但不适合作为开源用户入口。
- 开源发布最好只保留：

```text
README.md
LICENSE
docs/user/QUICK_START.md
docs/user/USER_GUIDE.md
docs/user/中文使用教程.md
docs/user/V2_ROADMAP.md 或精简版 ROADMAP.md
docs/user/DATABASE_DESIGN.md 或精简版 ARCHITECTURE.md
```

建议：

- 不一定从仓库删除，可以移到 `docs/internal/` 并在 README 中不链接。
- 如果目标是公开仓库干净，删除过程型 review 文档，只保留最终架构文档。

---

## 可删除内容清单

### 删除后不影响当前主线功能

```text
.diff_temp.txt
debug.log
.qoder/
.workbuddy/
.claude/
.impeccable/
backup-20260612-181108.tar.gz
HANDOFF.md
backend/setup_v3.py
backend/test_live_integration.py
```

说明：这些不是当前应用运行路径，不影响 `/api/events/*` 或 Next.js app。

### 删除后能降低复杂度

```text
backend/app/api/routes/trades.py
backend/app/services/trade_journal_service.py
backend/app/api/routes/signal_accuracy.py
backend/app/services/signal_tracker.py
backend/app/api/routes/scanner.py
backend/app/agents/
backend/app/memory/reputation_engine.py
backend/static/index.html
backend/static/index_zh.html
```

说明：这些共同构成 legacy market scanner / classic dashboard 体系。删除后系统会更接近 README 描述的事件情报平台。

### 删除后能减少维护成本

```text
backend/app/api/routes/resolve.py
backend/app/api/routes/calibration.py
backend/app/api/routes/analysis.py
backend/app/api/routes/backtest.py
backend/app/services/auto_resolve_service.py
backend/app/services/calibration_service.py
backend/app/services/analysis_audit_service.py
backend/app/memory/agent_memory.py
backend/app/memory/market_memory.py
backend/app/models/analysis.py
```

说明：这些维护的是 legacy market-layer 数据闭环。删除前需要同步移除 scheduler legacy jobs、API overview、README 和前端 `eventsApi.health()` 对 `/calibration/summary` 的依赖。

---

## 不建议直接删除的内容

### `backend/app/services/polymarket_history_service.py`

原因：

- 虽然名字和注释偏 legacy，但 event auto-resolve 仍使用 `fetch_resolved_markets()`。
- 正确做法是先把 resolved fetch 迁移到 event source 层，再删除 backtest-only 函数。

### `backend/app/services/base_rate_service.py`

原因：

- legacy scanner 使用它，当前 event / prediction loop 也使用 base rate category。
- 不能作为旧代码删除。

### `backend/app/services/news_filter_service.py`

原因：

- legacy scanner 和 event intelligence 都使用。
- 虽然代码承担较多职责，但不是死代码。

### `backend/app/services/probability_engine_service.py`

原因：

- `ai_analysis_service`、`cross_validation_service`、`event_extraction_service` 都使用。
- 不是当前删除目标。

---

## 推荐删除路线

### Phase 1：发布垃圾清理

目标：不改变功能。

删除：

```text
.diff_temp.txt
debug.log
.qoder/
.workbuddy/
.claude/
.impeccable/
backup-20260612-181108.tar.gz
HANDOFF.md
backend/setup_v3.py
backend/test_live_integration.py
```

同时更新 `.gitignore`。

验收：

```text
python -m compileall app tests
python -m unittest discover -s tests
npm run build
```

### Phase 2：移除 classic dashboard 和 legacy public routes

目标：让开源 API 面只剩当前产品主线。

删除或停用：

```text
/api/scan
/api/trades
/api/signals/accuracy
/api/analysis
/api/resolve
/api/calibration
/api/backtest
/dashboard
/dashboard/zh
```

保留：

```text
/api/events/*
/api/markets
/api/news
```

注意：

- 如果保留 `/api/markets` 和 `/api/news` 作为调试/基础数据接口，问题不大。
- 如果要极简 API，也可以只保留 `/api/events/*`。

### Phase 3：删除 legacy services 和 tests

目标：真正降低维护成本。

删除：

```text
backend/app/agents/
backend/app/memory/agent_memory.py
backend/app/memory/market_memory.py
backend/app/memory/reputation_engine.py
backend/app/services/auto_resolve_service.py
backend/app/services/calibration_service.py
backend/app/services/analysis_audit_service.py
backend/app/services/trade_journal_service.py
backend/app/services/signal_tracker.py
backend/app/models/analysis.py
```

同步删除对应测试。

### Phase 4：收敛配置

目标：开源用户只看到必要配置。

删除：

```text
MARKET_SCAN_LIMIT
MEMORY_FILE
```

评估后删除或实验化：

```text
CALIBRATION_FEEDBACK_ENABLED
PREDICTION_RESNAPSHOT_DELTA
POLYMARKET_CRYPTO_FETCH_ENABLED
```

保留但文档标记 optional：

```text
CROSS_VALIDATION_MODEL
OPEN_WEB_EXTRACTION_MODEL
EMBEDDING_MODEL
```

---

## 最终建议

若目标是“尽快开源，但不冒功能回归风险”，先做 Phase 1。

若目标是“开源后让外部开发者能理解并维护”，必须做 Phase 2 和 Phase 3。否则仓库会暴露两套产品、两套校准、两套路由、两套存储，外部贡献者很难判断哪里是主线。

最高收益删除不是某个 Service，而是整条 legacy market scanner 兼容线：

```text
scanner + classic dashboard + trades + signal accuracy + legacy resolve/calibration + agent_memory/analysis_audit
```

删除这条线后，项目边界会清楚很多：

```text
Event -> Evidence -> Probability -> Prediction Commitment -> Resolve -> Trust -> Decision Report
```

这才符合当前路线图和 README 的开源产品定位。
