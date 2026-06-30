# 架构审计：全量代码对照 10 条路线图原则

日期：2026-06-19  
审查者：资深架构师  
范围：当前工作区 `backend/` + `frontend/` 全部代码  
方法：逐条对照下列 10 条原则，重点检查 Scope Creep / Dead Code / Premature Abstraction / Future Schema Leakage / 与路线图冲突。**不评代码风格。**

> **与 2026-06-19 前三次审计的关系**：前三次审计指出的 P0 违规（多行账本、re-snapshot、M5 surface、默认持续闭环）**均未被回退**，当前代码仍包含这些违规。本文档是对当前代码的独立重新审计，不依赖前次审计结论。

---

## 审查的 10 条原则

1. M0 只负责 Event↔Market Link Ground Truth
2. M1 一事件一冻结 Prediction
3. Prediction 是 Commitment，不是 Trajectory
4. M2 Trust 仅使用 WATCH + ACT 的 resolved predictions
5. Dormant Segment 永远不能 ACT
6. Segment = Category Only
7. M3 只做 KPI，不做 Append-only Ledger 重构
8. 不提前实现 M4/M5 需求
9. Build Only What Next Milestone Requires
10. No Big Bang Rewrite

---

## 总体判定

**当前代码已大幅越过 M0–M2 的里程碑边界。** 最严重的违规集中在三件事上：

1. **Prediction 表被实现为 M3 多行 append-only ledger**（违反原则 2、3、7），包含 `superseded`、`_materially_changed` re-snapshot 等未来设计；
2. **M5 Decision Report / Opportunity Surface 已完整暴露为稳定 API + 主导航页面**（违反原则 8、9）；
3. **默认持续调度闭环**（`EVENT_DISCOVER_ENABLED=true`）让系统自动生成 predictions，在基础不变量未验证前就已进入全自动运行（违反原则 1、9）。

好消息是 M2 核心判断机制正确：category-only segment、act/watch resolved trust、dormant never act 都成立。问题不在细节正确性，在路线图越界。

---

## P0 — 违反核心原则，必须处理

### P0-1：Prediction 表实现为多行 append-only ledger（M3）

- **类别**：与路线图冲突 / Scope Creep / Future Schema Leakage
- **违反原则**：2（一事件一冻结）、3（Commitment 非 Trajectory）、7（M3 只做 KPI）
- **位置**：
  - `backend/app/memory/prediction_store.py`（全文件）
  - `backend/app/models/event.py` `Prediction` class（lines 177-222）
  - `backend/tests/test_prediction_store.py`

**证据**：

1. **Schema 移除 `UNIQUE(event_id)`**：`_migrate()`（line 82-132）检测旧的 `event_id UNIQUE` 约束并通过 `rename → recreate → copy → drop` 重建表，移除该约束。替换为普通索引 `CREATE INDEX IF NOT EXISTS idx_pred_event ON predictions(event_id)`（line 64）。

2. **多行 semantic 在模块级声明**：docstring（line 8）明确写 "multi-row per event (M3)"；`Prediction` 模型 docstring（`event.py` line 181）写 "M3 append-only ledger: multiple rows per event (no UNIQUE(event_id))"。

3. **五终态模型**：`open / scored / observed / voided / superseded`。其中 `superseded` 仅因多行账本而存在——旧 open 行不被覆盖，被标记为 `superseded`（line 268-271）。

4. **`get_predictions(event_id)` 返回完整历史**（line 378-389）—— oldest-to-newest 的全部冻结行。

5. **`freeze_prediction` 不是「首次冻结后不变」**，而是每次扫描重新评估 Decision Gate，判决变化或概率漂移达标则 append 新行（line 260-271）。

**影响**：一旦 prediction 表支持多行、superseded、history 查询，后续所有服务都会依赖「prediction 有历史版本」这个概念，将后续里程碑设计空间提前锁死。M3 轨迹应继续读 `event_audit.jsonl` 快照层（`analyze_edge_trajectory` 正是从那里读）。

---

### P0-2：`_materially_changed` — 把 Commitment 变成 re-snapshot 机制

- **类别**：与路线图冲突 / Premature Abstraction
- **违反原则**：2、3、7、9
- **位置**：
  - `backend/app/memory/prediction_store.py` `_materially_changed()`（lines 165-181）
  - `backend/app/core/config.py` `PREDICTION_RESNAPSHOT_DELTA`（line 206-208）

**证据**：

`_materially_changed(open_row, prediction)` 在两件事上触发新行追加：
1. Decision Gate verdict 变化（如 dormant → watch → act）；
2. **同一 verdict 内** `|Δ adjusted_edge|` 或 `|Δ ai_probability| >= PREDICTION_RESNAPSHOT_DELTA`（默认 5pt）。

后一种情况是纯 trajectory snapshotting——概率漂移 5 个点就重新冻结。轨迹已经存在于 `event_audit.jsonl`，在 prediction 表里再做一遍是双重实现。

`PREDICTION_RESNAPSHOT_DELTA` 本身是 premature abstraction：为不该存在的行为（同一 verdict 内 re-snapshot）提供可调旋钮。

`freeze_prediction` docstring（line 192-201）声称 "This is what lets an event frozen while its category was dormant later become a scored `act` prediction"——这是为 scope creep 找理由。在 Commitment 语义下，「dormant 时冻结为 watch、毕业后不重新承诺」是设计语义，不是缺陷。

---

### P0-3：M5 Decision / Opportunity Surface 已暴露为稳定 API + 主导航

- **类别**：Scope Creep / 提前实现 M4/M5
- **违反原则**：8、9
- **位置**：
  - **后端 API**：`backend/app/api/routes/events.py`
    - `GET /events/decisions/open`（line 267-285）— M5 核心产品面
    - `GET /events/edges/fresh`（line 288-295）— M5 fresh edge 机会发现
    - `GET /events/{event_id}/decision`（line 298-313）— M5 单事件决策报告
    - `GET /events/predictions/calibration`（line 249-257）— M2 校准（合规，但文档标为 M5）
  - **后端服务**：`backend/app/services/decision_report_service.py`
    - `build_decision_report()` — 完整 M5 Report Engine
  - **前端页面**：`frontend/src/app/decisions/page.tsx`
    - 完整的「决策机会」页面，含 act/watch 筛选、fresh edge badge、空状态说明
  - **前端组件**：`frontend/src/components/decisions/decision-card.tsx`
  - **前端导航**：`frontend/src/components/app-nav.tsx` line 10
    - 「决策机会」作为主导航第二项（`/decisions`）
  - **前端类型/API**：`frontend/src/lib/api.ts`
    - `DecisionReport` 接口（lines 32-60）
    - `EdgeTrajectory` / `FreshEdge` 接口（lines 63-80）
    - `PredictionCalibration` 接口（lines 83-92）
    - `eventsApi.openDecisions()`（line 160）
    - `eventsApi.freshEdges()`（line 166）
    - `eventsApi.predictionCalibration()`（line 170）— 有消费者（`history/page.tsx` line 31）

**影响**：前端已把「决策机会」作为主功能暴露，意味着产品面已承诺 M5 的 opportunity/report workflow。但在 M0/M1/M2 基础不变量未验证前，这些页面在 dormant 状态下始终展示空状态——对用户而言是 broken promise。

---

### P0-4：默认开启持续 discovery 调度，形成自动闭环

- **类别**：Scope Creep / No Big Bang Rewrite
- **违反原则**：1、8、9
- **位置**：
  - `backend/app/core/config.py` `EVENT_DISCOVER_ENABLED` 默认 `true`（line 225-228）
  - `backend/app/core/scheduler.py` `_job_event_discover`（lines 217-240）
  - `backend/app/core/scheduler.py` `start_scheduler()`（lines 243-280）

**证据**：

`EVENT_DISCOVER_ENABLED` 默认 `true`。scheduler 注册 4 个 job：
- `07:00 UTC` morning_scan（legacy 市场层）
- `07:15 UTC` event_discover → `discover_events(use_cache=False)` → `_persist_events` → `freeze_prediction`
- `22:00 UTC` evening_resolve（legacy 市场层 auto-resolve）
- `22:30 UTC` event_auto_resolve → score predictions

这意味着系统默认运行完整的 discovery → freeze → resolve → score 闭环。当前阶段 M0/M1/M2 不变量未验证（prediction 语义在 dispute 中、resolve 靠文本匹配而非 contract_id），系统不应默认自动生成 committed predictions。

---

## P1 — 高优先级收敛

### P1-1：Dead Code — 无消费者的 prediction ledger 端点

- **类别**：Dead Code
- **违反原则**：8、9
- **位置**：
  - `GET /events/predictions/recent`（`events.py` line 260-264）
  - `GET /events/{event_id}/predictions`（`events.py` line 316-325）
  - `prediction_store.list_recent()`（line 392-399）
  - `prediction_store.get_predictions()`（line 378-389）

**证据**：前端 `api.ts` **不调用**这两个端点。搜索前端全部源码，无 `predictions/recent`、无 `event_id/predictions`、无 `list_recent`、无 `get_predictions` 的消费方。这两个端点纯为「暴露多行账本」而加，随 P0-1 回退应一并移除。

唯一的 prediction 相关消费者是 `eventsApi.predictionCalibration()`（→ `GET /events/predictions/calibration`），被 `history/page.tsx` 调用——这是合规的 M2 校准查询。

---

### P1-2：`rank_fresh_edges()` / `GET /events/edges/fresh` — M5 机会发现伪装成 M3 KPI

- **类别**：Scope Creep / Premature Product Surface
- **违反原则**：7、8、9
- **位置**：
  - `backend/app/services/trend_analysis_service.py` `rank_fresh_edges()`（lines 289-312）
  - `backend/app/api/routes/events.py` `GET /events/edges/fresh`（lines 288-295）

**证据**：

`analyze_edge_trajectory()` 本身是合规的 M3 KPI——它从 audit snapshots 读取 edge 轨迹并分类（fresh/stale/decaying/closed）。但 `rank_fresh_edges()` 把 fresh 分类的物品按 `|latest_edge|` 排序并作为「机会面」暴露——这不是 KPI，是 M5 opportunity discovery。

`EDGE_STALE_HOURS`（默认 72h）配置项为这个 M5 分类服务，也属于 premature 配置。

**合规部分（应保留）**：`analyze_edge_trajectory()` 和 `analyze_trend()` 作为纯 KPI 函数，从 audit 快照计算轨迹/趋势，不排序、不暴露机会——符合 M3「只做 KPI」原则。

---

### P1-3：M0 Ground Truth 身份与 resolve/scoring 逻辑耦合

- **类别**：Scope Creep / 原则边界模糊
- **违反原则**：1、9
- **位置**：
  - `backend/app/services/event_resolve_service.py`
  - `backend/app/memory/event_market_link_store.py`

**证据**：

`event_market_link_store` 本身正确实现了 M0 的 verified/pending/fail-closed link。但 `event_resolve_service.auto_resolve_events` 同时承担：
- 多源 resolved market fetch
- fuzzy text match（非 contract_id 直接对账）
- link verification
- identity conflict detection
- event resolve
- prediction score
- prediction void

其中 resolve/score/void 属于 M1 职责，不应与 M0 link ground truth 在同一服务里混为一谈。

此外，resolve 路径仍用 **question 文本模糊匹配** 而非 `contract_id` 直接对账——M0 ground truth（verified link with contract_id）建好了，但结算侧没消费它。这是之前审计中指出的「断点 A」结构性风险，当前仍未修复。

---

### P1-4：Future Schema Leakage — Diagnosis 解释字段冻结进 prediction 行

- **类别**：Future Schema Leakage
- **违反原则**：8、9
- **位置**：
  - `backend/app/models/event.py` `Prediction` model（lines 214-217）
  - `backend/app/memory/prediction_store.py` `_SCHEMA`（lines 53-56）

**证据**：

Prediction 表包含 4 个 M2 诊断字段：
- `liquidity_factor` — liquidity weight
- `qualified` — segment 是否达合格线
- `segment_n` — 样本数
- `segment_skill` — 技能分

这些是 M2 Disagreement Diagnosis 的内部计算量。把它们冻结进 M1 Commitment 行，是 M2 细节向 M1 schema 的渗透。虽然有利于决策报告解释「为什么是这个 verdict」，但在 M1/M2 早期阶段，这些字段增加了 schema 表面积和迁移债务。解释可在报告层按需从 `prediction_store` 实时计算，不必固化。

---

### P1-5：测试固化超范围行为

- **类别**：变更固化风险
- **违反原则**：7、8、9
- **位置**：
  - `backend/tests/test_prediction_store.py`
  - `backend/tests/test_trend_analysis_service.py`
  - `backend/tests/test_scheduler.py`

**证据**：

测试覆盖并保护了以下超范围行为：
- migration drop `UNIQUE(event_id)`
- multi-row ledger
- `superseded` 状态
- `get_predictions()` history oldest-first
- `_materially_changed` re-snapshot
- `rank_fresh_edges()` fresh ranking
- scheduled event discovery

这些测试把路线图冲突固化为「测试保护的正确行为」，增加回退难度。

---

## P2 — 中低优先级观察项

### P2-1：Premature 配置旋钮

- **类别**：Premature Abstraction
- **违反原则**：8、9
- **位置**：`backend/app/core/config.py`

| 配置项 | 默认值 | 为何 premature |
|--------|--------|----------------|
| `PREDICTION_RESNAPSHOT_DELTA` | 5.0 | 为 re-snapshot（不该存在）提供可调阈值 |
| `EDGE_STALE_HOURS` | 72.0 | 为 M5 fresh-edge 分类服务 |
| `EVENT_DISCOVER_ENABLED` | true | 默认全自动闭环 |
| `EVENT_DISCOVER_LIMIT` | 10 | 持续闭环的规模旋钮 |
| `DECISION_ACT_EDGE` | 10.0 | M2 Decision Gate（合规，但值偏高导致 dormant 期几乎无 act） |
| `DECISION_WATCH_EDGE` | 3.0 | M2 Decision Gate（合规） |
| `DIAGNOSIS_DORMANT_TRUST` | 0.5 | M2（合规） |
| `DIAGNOSIS_LIQUIDITY_FLOOR` | 5000.0 | M2（合规） |

---

### P2-2：文档中的 Future Schema Leakage

- **类别**：Future Schema Leakage / Roadmap Drift
- **违反原则**：6、7、9
- **位置**：
  - `docs/user/V2_ROADMAP.md` Temporal note
  - `docs/user/DATABASE_DESIGN.md` calibration_metrics segment_type

**证据**：

1. `V2_ROADMAP.md` Temporal note 写 "Each pass appends new snapshots and, if it clears the gate, a new prediction. Probability and edge are trajectories." —— 这句被用来为多行 prediction 账本背书，但「轨迹在 audit、预测是 commitment」的原则与之相悖。路线图 Temporal note 需要澄清：轨迹归 audit 快照层，prediction 是 commitment。

2. `DATABASE_DESIGN.md` 的 `calibration_metrics.segment_type` 定义为 `global / category / edge_bucket / evidence_profile`。当前原则明确 Segment = Category Only。`edge_bucket` 和 `evidence_profile` 应标注为 deferred future design。

---

### P2-3：Prediction 模型 status 字段类型标注与实际不符

- **类别**：文档不一致
- **位置**：`backend/app/models/event.py` `Prediction.status`（line 219）

**证据**：`status: str = "open"  # open | scored` —— 类型注释只列了 2 种状态，但实际实现支持 5 种：`open | scored | observed | voided | superseded`。注释需要与实现同步。

---

### P2-4：Frontend M5 类型定义

- **类别**：Future Schema Leakage
- **位置**：`frontend/src/lib/api.ts`（lines 32-92）

**证据**：`DecisionReport`、`EdgeTrajectory`、`FreshEdge`、`PredictionCalibration` 四个接口定义在 `api.ts` 中。其中前三个是 M5 专属类型（DecisionReport + Opportunity Surface）。随 P0-3 回退，这些类型也应移除或标记为实验性。

`PredictionCalibration` 有合法消费者（`history/page.tsx`），应保留。

---

### P2-5：`_persist_events` 中 save / record / freeze 同一 try 块

- **类别**：静默失败风险
- **位置**：`backend/app/services/event_intelligence_service.py` `_persist_events()`（lines 394-415）

**证据**：`save_events` / `record_event` / `freeze_prediction` 三步在同一 try/except 中。任一抛错 → `logger.warning` 吞掉，后续步骤不执行。例如 save 成功但 freeze 抛错，事件入库却无 prediction，无显式信号。这是前次审计指出的「断点 B」，当前仍未修复。

---

## 符合原则的部分（应保留）

以下部分经审查与路线图原则一致，回退时不应删除：

| 项 | 对应原则 | 说明 |
|----|----------|------|
| `event_market_links` verified/pending/fail-closed | 1 | M0 ground truth 核心 |
| link 上的 `resolution_criteria` | 1 | M0 identity 不变量 |
| `score_prediction` act-only 分流 | 4 | 只 act 进 headline calibration |
| `void_prediction` invalid 掉出机会面 | 4 | geniune/non-genuine 区分 |
| `segment_skill(category)` act+watch, 排除 skip | 4, 6 | category-only, 正确排除 skip |
| `calibration_summary()` act-only | 4 | 只报告承诺行动的结果 |
| `diagnose.decide()` qualified 闸门 | 5 | dormant 永不 act |
| `diagnosis_service` 全模块 | 4, 5, 6 | Category-only, qualified gate, trust from resolved |
| `calibration_feedback_service` 休眠态 | 8 | opt-in, 默认 no-op, 合规 |
| `analyze_trend()` / `analyze_edge_trajectory()` | 7 | 从 audit 快照读轨迹的 M3 KPI |
| `trend_analysis_service.rank_movers()` | 7 | 概率变动排序 = M3 KPI |
| 增量变更模式 | 10 | 非 big-bang |

---

## 违规归类速查表

| 类别 | 条目 | 优先级 | 违反原则 |
|------|------|--------|----------|
| 与路线图冲突 | 多行 append-only prediction 账本 | P0 | 2, 3, 7 |
| Scope Creep | `_materially_changed` re-snapshot | P0 | 2, 3, 7, 9 |
| Scope Creep | M5 Decision/Opportunity Surface | P0 | 8, 9 |
| Scope Creep | 默认持续 discovery 调度闭环 | P0 | 1, 8, 9 |
| Dead Code | `get_predictions` + ledger 端点 | P1 | 8, 9 |
| Scope Creep | `rank_fresh_edges` 暴露为产品 API | P1 | 7, 8, 9 |
| Scope Creep | M0 ground truth + resolve/scoring 耦合 | P1 | 1, 9 |
| Future Schema Leakage | Diagnosis 字段冻结进 prediction 行 | P1 | 8, 9 |
| 变更固化 | 测试保护超范围行为 | P1 | 7, 8, 9 |
| Premature Abstraction | `PREDICTION_RESNAPSHOT_DELTA` 等 | P2 | 8, 9 |
| Future Schema Leakage | 路线图/数据库文档 future segment | P2 | 6, 7, 9 |
| 文档不一致 | Prediction.status 类型标注不全 | P2 | — |
| Future Schema Leakage | Frontend M5 类型定义 | P2 | 8, 9 |
| 静默失败 | save/freeze 同一 try 块 | P2 | — |

---

## 建议处理顺序

### 第一步：决策原则确认

当前代码包含两个相互矛盾的路线图解读：
- **A 解读（原则派）**：Prediction = Commitment，一事件一冻结，轨迹在 audit，M3 只做 KPI。
- **B 解读（字面派）**：路线图 Temporal note "append a new prediction" 允许多行账本。

本文档基于 **A 解读**编写。如果团队确认 A，则按下面顺序回退。如果确认 B，则 P0-1/P0-2 降级，但 P0-3（M5 surface）和 P0-4（默认闭环）仍需处理。

### 第二步：回退 P0 违规

1. **回退 prediction 多行账本**（P0-1 + P0-2）：
   - 恢复 `UNIQUE(event_id)` 约束（或至少 `UNIQUE WHERE status='open'` 部分索引）
   - 删除 `_materially_changed()`、`superseded` 状态
   - `freeze_prediction` 改回「首次 commit，不再 re-snapshot」
   - 现有 superseded 历史行需先折叠

2. **隐藏 M5 surface**（P0-3）：
   - 从主导航移除 `/decisions`
   - 下线 `GET /events/decisions/open`、`GET /events/edges/fresh`、`GET /events/{event_id}/decision`
   - 移除 `decision_report_service.py`、`DecisionCard`、前端 decisions 页面
   - 保留 `rank_fresh_edges()` 但不暴露为产品 API（或改为内部函数）

3. **关闭默认 discovery**（P0-4）：
   - `EVENT_DISCOVER_ENABLED` 默认改为 `false`
   - 保留 scheduler 注册逻辑，但默认不添加 event_discover job

### 第三步：清理 P1

4. 删除 dead ledger 端点（`GET /events/predictions/recent`、`GET /events/{event_id}/predictions`）
5. 决定 diagnosis 解释字段去留
6. 拆分 resolve 与 link verify 的错误处理
7. 调整测试保护当前不变量

### 第四步：修正文档

8. 澄清 `V2_ROADMAP.md` Temporal note 措辞
9. 标注 `DATABASE_DESIGN.md` 中 deferred 的 segment 类型
10. 修正 `Prediction.status` 类型注释

---

## 验收标准

回退后，以下条件应全部满足：

1. **数据库层**：`predictions` 对 `event_id` 有 DB 级唯一约束（或 `status='open'` 部分唯一）
2. **行为层**：同一 event 重复扫描不新增第二条 open prediction，不产生 `superseded`
3. **语义层**：prediction = 一次 commitment；概率轨迹只在 audit/history 中
4. **M2 层**：trust 只按 category 聚合 resolved act/watch；无 edge_bucket/evidence_profile
5. **Dormant gate**：样本不足的 category 永不输出 act
6. **API 层**：稳定 API 不暴露 prediction ledger、decision report、fresh edge opportunity
7. **调度层**：event discovery 默认不自动运行
8. **前端**：主导航不包含「决策机会」
9. **测试层**：测试保护上述不变量，而非多行 ledger 或 M5 surface

---

# 第二部分：数据闭环链路深度审查

## 审查方法

逐环节追踪 Scheduler → Discover → Event → Market Link → Freeze Prediction → Resolve Outcome → Calibration → Trust → Decision Report 的完整数据流。对每个环节回答 5 个问题：

1. 是否实际连通
2. 是否有数据落盘
3. 是否可恢复
4. 是否有 fail-closed 保护
5. 是否存在静默失败

---

## 当前实际数据流

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            SCHEDULER (APScheduler)                           │
│  07:00 UTC  morning_scan        (市场层, legacy)                              │
│  07:15 UTC  event_discover      (事件层, EVENT_DISCOVER_ENABLED gate)        │
│  22:00 UTC  evening_resolve     (市场层 auto-resolve, legacy)                 │
│  22:30 UTC  event_auto_resolve  (事件层 auto-resolve)                         │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │ _job_event_discover()
                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DISCOVER (discover_events)                            │
│                                                                             │
│  ① collect_shared_articles() → RSS feeds (1 query)                          │
│  ② _collect_candidate_events() ───┐                                         │
│     ├─ Polymarket (volume rank)   │  4 候选源, 共用 shared_articles          │
│     ├─ Manifold (score rank)      │  candidate_dedup_service 去重             │
│     ├─ Kalshi                     │  Poly > Manifold > Kalshi > OpenWeb      │
│     └─ Open Web (extraction) ─────┘                                         │
│  ③ 对每个候选:                                                               │
│     ├─ _build_filtered_news()                                                │
│     │   ├─ collect_articles() → RSS+Google News 按问题爬新闻                   │
│     │   ├─ parse_market_semantics() → entities/conditions                    │
│     │   ├─ annotate_semantic_relevance() ← [embedding, opt-in, 默认 no-op]    │
│     │   └─ filter_news_for_market() → relevance_score 筛选                    │
│     │                                                                       │
│     ├─ selected_count==0? → ⚡ 跳过 (现实过滤器)                               │
│     │                                                                       │
│     └─ analyze_event() ──────────────────────────────────┐                   │
│         ├─ ai_analysis_service.analyze_market() ── [LLM]  │                   │
│         ├─ build_event_record()                            │                   │
│         ├─ cross_validate() ── [LLM #2, 对照]              │                   │
│         └─ _apply_calibration_feedback() ← [dormant by default]              │
│                                                                             │
│  ④ _persist_events(fresh_records) ── 同一个 try/except 块                    │
│     ├─ save_events()   → event_store.json                                    │
│     ├─ record_event()  → event_audit.jsonl                                   │
│     └─ freeze_prediction() → v2_loop.db.predictions                          │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│            EVENT STORE (event_store.json, 持久化)                             │
│                                                                             │
│  save_events() upsert merge policy:                                          │
│  ✅ 保留 first_seen                                                          │
│  ✅ 保留 existing_tracking (用户手动选择)                                      │
│  ❌ 不保留 outcome          → 重扫可能覆盖已结算事件                            │
│  ❌ 不保留 calibration      → 重扫可能覆盖校准快照                              │
│  ❌ 不保留 calibration_components → 重扫丢失已记录的组件概率                    │
│                                                                             │
│  写保护: write_json_atomic (tmp file + os.replace)                            │
│  读保护: _load_for_write 用 read_json_strict (corrupt JSON → 抛异常不被清空)     │
│  并发: threading.RLock (进程内), 无跨进程锁                                     │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│          EVENT AUDIT (event_audit.jsonl, append + compaction)                 │
│                                                                             │
│  record_event() → 追加一行概率快照 (timestamp, estimated, baseline, change...)  │
│  record_outcome() → 追加一行 outcome 快照 (kind="outcome")                     │
│                                                                             │
│  compaction: 超过 EVENT_AUDIT_COMPACTION_THRESHOLD 行 → 每个 event 只保留       │
│  最近 EVENT_AUDIT_MAX_PER_EVENT 条概率快照 + 最近 1 条 outcome 快照              │
│                                                                             │
│  概率快照 | 概率快照 | ... | 概率快照 | outcome                               │
│  ─────────→ oldest ──────────→ newest ──────→                                │
│                                                                             │
│  ⚠ compaction 丢旧快照: 长期运行的 event 会丢失早期轨迹                         │
│  ⚠ 文末 outcome 快照保证能恢复, 但早期概率轨迹不可恢复                           │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│           EVENT↔MARKET LINK (v2_loop.db.event_market_links)                   │
│                                                                             │
│  upsert_link() 按 (event_id, contract_id) 去重                                │
│                                                                             │
│  verified=1 的路径:                                                          │
│  ├─ auto: 文本匹配 score ≥ AUTO_VERIFY_THRESHOLD (默认 1.0=精确匹配)            │
│  └─ manual: 人工 resolve 时记录                                                │
│                                                                             │
│  pending: score < threshold → 挂起, 等人工 review                              │
│  fail-closed: score < threshold → ⚡ 不计分, 不 resolve                         │
│                                                                             │
│  identity conflict 检查: existing verified link 的 contract_id ≠ 新的           │
│  → resolve status="invalid", void prediction (不进校准)                         │
│                                                                             │
│  ⚠ resolve 路径仍用 question 文本模糊匹配, 不用 contract_id 直接对账             │
│     M0 ground truth 建好了但结算侧不消费它                                      │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│     FREEZE PREDICTION (v2_loop.db.predictions, 插入新行)                       │
│                                                                             │
│  freeze_prediction(record):                                                   │
│  ├─ source.type != "prediction_market"? → ⚡ skip (返回 None)                  │
│  ├─ contract_id 为空? → ⚡ skip                                              │
│  ├─ ai/market probability 为空? → ⚡ skip                                    │
│  │                                                                          │
│  ├─ diagnose(raw_edge, segment_skill, liquidity) → M2 trust + adjusted_edge  │
│  │   ├─ segment_skill(category) → n=0 → {n:0, skill:None} → dormant         │
│  │   ├─ calibration_trust(dormant) → 0.5 (DIAGNOSIS_DORMANT_TRUST)           │
│  │   ├─ adjusted_edge = raw_edge * 0.5 * liq_factor                          │
│  │   ├─ qualified = False (n < 8)                                           │
│  │   └─ decision = "watch" (adjusted_edge ≥ WATCH_EDGE=3) 或 "skip"          │
│  │                                                                          │
│  └─ INSERT v2_loop.db.predictions (id, event_id, contract_id,                │
│       ai_probability, market_probability, raw_edge, trust, adjusted_edge,     │
│       decision, liquidity_factor, qualified, segment_n, segment_skill...)      │
│                                                                             │
│  ⚠ _materially_changed → 同一 event 可为 "watch→watch" 追加多行(superseded)    │
│  ⚠ 只对 "fresh records" persist → 缓存命中跳过 freeze, 无新 prediction        │
│  ⚠ _persist_events 整体 try/except → save 成功但 freeze 抛错 → 无 prediction   │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │ 22:30 UTC event_auto_resolve
                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│    RESOLVE OUTCOME (event_store.json outcome + calibration)                   │
│                                                                             │
│  auto_resolve_events():                                                       │
│  ① fetch_resolved_markets() ×3 (Polymarket+Manifold+Kalshi, 并发, 故障隔离)     │
│  ② build_index() → text_match.find_match()                                    │
│  ③ 对每个未结算 event:                                                         │
│     ├─ record.outcome is not None? → skip (已有 outcome)                      │
│     ├─ find_match() → None? → 跳过                                            │
│     ├─ match_score ≥ AUTO_VERIFY_THRESHOLD(1.0)? → verified                   │
│     ├─ identity conflict? → resolve status="invalid", void_prediction         │
│     ├─ !verified? → pending (不 resolve)                                      │
│     └─ resolve_with_calibration():                                            │
│         ├─ analyze_trend() → latest_probability                               │
│         ├─ score_event() → calibration snapshot (Brier, skill)                │
│         ├─ resolve_event() → event_store.json 写 outcome+calibration           │
│         ├─ record_outcome() → event_audit.jsonl 追加 outcome 快照              │
│         └─ score_prediction() → prediction 行 open→scored/observed             │
│             └─ decision="act" → scored (进 calibration_summary)                │
│             └─ decision!="act" → observed (进 segment_skill, 不进 calibration)  │
│                                                                             │
│  ⚠ 文本匹配 threshold=1.0 → 只有精确归一化匹配才 auto-verify                      │
│     → ⚡ 绝大多数匹配落入 pending, 不 resolve                                   │
│     → 导致「事件存在但永远不结算」                                                │
│  ⚠ resolve_with_calibration() 不幂等 → 重复 resolve 覆盖 outcome                │
│  ⚠ auto_resolve 单个事件失败 → continue, 不整体回滚                              │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│     CALIBRATION (calibration_summary + segment_skill)                         │
│                                                                             │
│  calibration_summary():                                                       │
│    SELECT ... WHERE status='scored' AND decision='act'                        │
│    返回 overall(Brier, skill, grade, n) + by_category                          │
│    → act 行 + scored 状态 → 两者需同时满足                                      │
│    → ⚡ 当前 n=0 (无 act 行被 resolve)                                         │
│                                                                             │
│  segment_skill(category):                                                     │
│    SELECT ... WHERE status IN ('scored', 'observed')                           │
│                       AND decision IN ('act', 'watch')                         │
│    → act+watch 的 scored+observed 都计入                                       │
│    → 正确设计: watch→observed 也能累积样本                                       │
│    → ⚡ 但当前也是 n=0 (尚无任何 prediction 被 resolve)                          │
│                                                                             │
│  校准反馈 (calibration_feedback_service):                                      │
│    ├─ analyze_event 时 ALWAYS 记 calibration_components                        │
│    ├─ CALIBRATION_FEEDBACK_ENABLED=False? → 跳过 adjustment                    │
│    └─ ENABLED=True + min_samples=8? → fuses components + shrinkage             │
│                                                                             │
│    激活条件:                                                                   │
│    ├─ 8 个已结算 event, 每个要有 calibration_components + outcome               │
│    ├─ calibration_components 在 analyze_event 时记入 (market/llm/cross)         │
│    └─ ⚡ 当前本地 1 个结算样本, 远未达标                                         │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                TRUST (diagnose → Decision Gate)                               │
│                                                                             │
│  每轮 freeze_prediction 重新 compute:                                          │
│    segment_stats = segment_skill(category)                                    │
│    trust = calibration_trust(segment_stats, min_samples=8, dormant=0.5)        │
│    qualified = (segment_n >= 8)                                               │
│    adjusted_edge = raw_edge * trust * liquidity_factor                         │
│                                                                             │
│  当前所有 category: n=0 → trust=0.5(constant), qualified=False, decision≤watch │
│                                                                             │
│  act 从未被触发: qualified=False → decision never "act"                        │
│  watch: adjusted_edge ≥ 3 (WATCH_EDGE) → trusted_0.5 下 raw≥6 可得              │
│  skip: adjusted_edge < 3                                                      │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│          DECISION REPORT (decision_report_service → API → 前端)                │
│                                                                             │
│  GET /events/decisions/open:                                                  │
│    list_open_opportunities(act+watch) → build_decision_report → DecisionCard  │
│    当前: act 0 条, watch N 条 (全部 dormant, trust=0.5)                        │
│                                                                             │
│  GET /events/{event_id}/decision: 单事件决策报告                                │
│                                                                             │
│  ⚠ 前端 /decisions 主页面: 所有 decision=watch 的 entry                        │
│     → 对用户: "当前没有可展示的机会" 或 满屏 "持续观察"                            │
│     → 在 trust 未建立前, 这页只展示 watch, 不是 actionable insights              │
│                                                                             │
│  空状态设计:                                                                   │
│    "反馈闭环需要先积累已结算的预测，才能为各类别建立校准信任度并发现 edge"             │
│    → 提示正确, 但积累路径不畅通                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 逐环诊断

### L0: Scheduler

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 实际连通 | ✅ 连通, 但 discovery 默认开启 | `start_scheduler()` 注册 4 个 job |
| 数据落盘 | N/A | 调度器本身只调度 |
| 可恢复 | ✅ coalesce=True, misfire_grace_time=300s | scheduler config |
| fail-closed | ✅ 任何 job 抛错被 catch 吞掉, 不影响其他 | per-job try/except |
| 静默失败 | ⚠️ 有风险 — job 失败只 log, 无告警 | `logger.exception` 无 alert/pager |

---

### L1: Discover

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 实际连通 | ✅ 4 候选源 → dedup → reality filter(selected_count) → LLM | `discover_events()` line 308–386 |
| 数据落盘 | ⚠️ 部分 — 缓存命中不 persist | `fresh = [r for r, is_new in results if is_new]` |
| 可恢复 | ⚠️ 部分 — 缓存命中恢复但无新 audit/prediction | cache 路径跳过 persist |
| fail-closed | ✅ 现实过滤器: selected_count=0 → skip | `if selected_count == 0: return None` |
| 静默失败 | ⚠️ 三处 — ① 单个候选失败 → None, gather 去空 ② _build_filtered_news 内 RSS/Google News fetch 失败 → 空 articles ③ Open Web extraction 需要 `OPEN_WEB_EXTRACTION_MODEL`, 未配 → 空 |

**额外风险**：
- 候选源按 volume/score 排序 → 加密事件可能被地缘政治刷出候选池（已知但未修复）
- shared_articles 只 fetch 一次 → RSS 源列表固定，不随候选动态变化

---

### L2: Event (event_store.json)

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 实际连通 | ✅ upsert by event_id | `save_events()` line 44–74 |
| 数据落盘 | ✅ write_json_atomic (tmp + os.replace) | 原子写，不会半坏文件 |
| 可恢复 | ⚠️ 重扫覆盖恢复不完整 | 只保留 tracking，不保留 outcome/calibration/calibration_components |
| fail-closed | ✅ EventRecord.model_validate gate | 无效 record 抛异常，不写入 |
| 静默失败 | ⚠️ _persist_events 内 save_events 失败 → 吞异常，不重试 | line 414–415 `except Exception: logger.warning` |

**数据丢失风险**：
```
场景：event_store.json 已有 { outcome: {...}, calibration: {...}, tracking: {...} }
重扫同一 event_id → save_events 调用 → merge 只保留 existing_tracking
→ outcome 被覆盖 → auto_resolve 跳过保护失效 → 事件可能被重复 resolve
→ calibration 被覆盖 → 历史校准快照丢失
→ calibration_components 被覆盖 → 组件 Brier 历史清零
```
这是**最严重的静默数据丢失风险**。

---

### L3: Event Audit (event_audit.jsonl)

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 实际连通 | ✅ 每次 analyze_event → record_event append | `_persist_events` → `record_event()` |
| 数据落盘 | ✅ append + 文件锁 | `locked_file` + `open(a)` |
| 可恢复 | ⚠️ compaction 丢旧快照 | `_maybe_compact` 只保留最近 N 条/event |
| fail-closed | ✅ 有效 — compaction 失败吞异常, 不丢 append | line 116–123 `except: logger.warning` |
| 静默失败 | ⚠️ compaction 丢早期轨迹静默, 无告警 | log only |

**compaction 影响**：
- 对「可恢复性」：概率轨迹不可无限回溯 — 只保留最新 N 条快照
- 对「长期校准」：M3 edge trajectory 靠 audit 快照计算 → 长期运行事件丢失早期轨迹 → trend 计算偏短窗口
- compaction 不丢 outcome 快照 (保留最后 1 条) → 结算标记可恢复

---

### L4: Market Link (event_market_links)

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 实际连通 | ✅ upsert + verified/pending | `upsert_link()` |
| 数据落盘 | ✅ SQLite, UNIQUE(event_id, contract_id) | line 24–28 in `event_market_link_store.py` |
| 可恢复 | ✅ 行级 upsert, 不丢旧 link | 覆盖式更新 verified/resolution_criteria |
| fail-closed | ✅ 三层: ① unverified → pending(不 resolve) ② identity conflict → invalid(不 score) ③ 需要人工 verify | `auto_resolve_events` lines 237–247 |
| 静默失败 | ⚠️ link 建好了但 resolve 侧不消费 contract_id | resolve 仍用 text match → link ground truth 闲置 |

---

### L5: Freeze Prediction (predictions)

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 实际连通 | ✅ market-gated → diagnose → INSERT | `freeze_prediction` lines 184–293 |
| 数据落盘 | ✅ SQLite, _WRITE_LOCK (进程内) | writing() context |
| 可恢复 | ⚠️ 无跨存储事务 — prediction 落盘但 event_store/audit 掉 | _persist_events try/except |
| fail-closed | ✅ market-gated: 非市场事件 → skip; contract_id 空 → skip | lines 202–213 |
| 静默失败 | ⚠️ 三处 — ① save_events 成功但 freeze 抛错 ② segment_skill n=0 → 全 dormant ③ _materially_changed 多行(superseded) | 分析见下 |

**freeze_prediction 内决策链**：
```
ALL categories n=0 (当前)
→ segment_skill → {n:0, skill:None}
→ calibration_trust(dormant, n<8) → 0.5 (恒定)
→ liquidity_factor(liq, floor=5000) → 0..1
→ adjusted_edge = raw_edge * 0.5 * liq
→ qualified = False
→ decide(adjusted_edge, qualified=False)
  → |adjusted_edge| ≥ 10? → 不可能 (qualified=False caps at watch)
  → |adjusted_edge| ≥ 3? → "watch"
  → else → "skip"
→ INSERT status="open", decision="watch"/"skip"
```

所有 prediction 冻结为 watch 或 skip。无 act。act 从未产生。

---

### L6: Resolve Outcome

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 实际连通 | ✅ 三源并发 fetch → text match → resolve | `auto_resolve_events` lines 145–340 |
| 数据落盘 | ✅ resolve_event (JSON) + record_outcome (JSONL) + score_prediction (SQLite) | `resolve_with_calibration` lines 82–142 |
| 可恢复 | ⚠️ 不幂等 — 重复 resolve 覆盖 outcome/calibration | `resolve_event` 无条件写 |
| fail-closed | ✅ 多层: ① outcome exists → skip ② match_score < 1.0 → pending ③ identity conflict → invalid | lines 220, 237, 265 |
| 静默失败 | ⚠️ 三处 — ① match_score<1.0 → pending forever (需人工 verify) ② 单个事件 fail → continue ③ per-component outcome 不存在 → briers_by_component 空 |

**永远不会触发（pending 永久队列）**：
```
AUTO_VERIFY_THRESHOLD=1.0 (默认)
→ 只有精确归一化匹配 (normalize local == normalize resolved) 才 auto-verify
→ 模糊匹配 score ∈ [0.82, 1.0) → pending
→ pending 需要人工 POST /events/{event_id}/link/verify
→ 前端无人工 verify UI → 无人触发 → pending 永久不 resolve
```

**resolve 不会产生 scored 行**：
```
当前所有 prediction decision ∈ {watch, skip}
resolve → score_prediction(event_id, outcome)
→ decision != "act" → status = "observed"
→ 0 条 scored → calibration_summary n=0 → "no_data"
→ 但 observed(watch) 计入 segment_skill → category 可累积毕业样本
```

---

### L7: Calibration

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 实际连通 | ⚠️ calibration_summary n=0, segment_skill n=0 | 无 scored 无 observed |
| 数据落盘 | ✅ calibration_summary 按需计算 (无持久化中间量) | 实时 SQL 聚合 |
| 可恢复 | ✅ 纯计算 → 无状态, 查询即恢复 | 依赖 predictions 行数据存在 |
| fail-closed | ✅ n=0 → None, no_data | `calibration_summary` line 516–518 |
| 静默失败 | ⚠️ n=0 永远不变 — 不是 bug, 是闭环没通 |

**calibration_feedback 激活路径**：
```
analyze_event() 时:
  ① 总是记 calibration_components = {market, llm, cross_validation}
  ② CALIBRATION_FEEDBACK_ENABLED=False → return (no-op)

假设开启:
  ③ _load_resolved_records() → list_resolved_events()
  ④ briers_by_component() → 需要 calibration_components 字段
     → ⚡ 老事件(1)有 outcome 但缺少 calibration_components → 跳过
  ⑤ component_weights() → 需要 ≥2 个 component 各有 ≥8 samples → ⚡ 空
  ⑥ category_briers() → 需要 calibration.brier_score → 老事件有 → 可能非空
  ⑦ adjust_probability() → 权重空 + 收缩 0 → 返回原 LLM estimate
  → 即使开启, 也是 no-op, 直到积够 8×2=16 个有 calibration_components 的结算事件
```

---

### L8: Trust

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 实际连通 | ⚠️ trust 恒定 0.5 (dormant), 从未用真实 skill | segment_skill n=0 |
| 数据落盘 | ✅ trust/adjusted_edge 冻结进 prediction 行 | `freeze_prediction` line 228–242 |
| 可恢复 | ✅ segment_skill 实时从 predictions 计算 | SQL 聚合 |
| fail-closed | ✅ qualified=False → 不开 act | `decide()` line 66 |
| 静默失败 | ⚠️ trust 从不变 — 不是 bug, 是闭环没通 |

**trust 真正的激活路径**：
```
1. 需要 event 结算 → resolve → prediction scored/observed
2. 需要 PER CATEGORY ≥8 个 scored+observed (act+watch)
3. watch→observed 累积可毕业
4. 毕业后 trust∈[0,1] 用实际 skill, 不再是 0.5 恒定
```

**Dormant 毕业时间预估**：
```
当前: 0 个结算样本/类别
增长路径: 每天 discover → freeze prediction(watch) → 等待市场结算 → auto_resolve
瓶颈:
  ① Polymarket 市场通常数月才结算 → 长期事件(2045/2050)永不结算
  ② match_score≥1.0 才 auto-verify → ⚡ pending 队列不推进
  ③ 无人工 verify UI → pending 永久不毕业
结论: 在 AUTO_VERIFY_THRESHOLD=1.0 且无人工 verify UI 的前提下
      → Dormant 状态自驱动毕业 > 6 个月
      → 实际为 "permanently dormant"
```

---

### L9: Decision Report

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 实际连通 | ✅ open_opportunities → build_decision_report → API → 前端 | 全链路通 |
| 数据落盘 | N/A | 纯读取 |
| 可恢复 | ✅ 从 predictions + event_store 实时读取 | 无状态 |
| fail-closed | ✅ 空结果 → 前端显示空状态 | `/decisions` 空状态提示 |
| 静默失败 | ⚠️ act 0 条 → 页面始终展示 "建议行动 0" | 用户感知: 功能未就绪 |

**决策报告的真相**：
```
前端 /decisions 展示:
  全部: N 条 (全部 watch)
  建议行动: 0 条 (act 从未产生)
  持续观察: N 条 (dormant → watch = 空观察)
→ 本质: 产品承诺了 M5, 但当前只能展示 "观察, 再观察, 继续观察"
```

---

## 闭环断点总览

```
Scheduler ────✅────▶ Discover ────✅────▶ Event
                                            │
                                    ⚡ 断点 A: save_events merge 不保留 outcome/calibration
                                    ⚡ 断点 B: _persist_events try/except 吞 save 后 freeze 失败
                                            │
                                    ┌───────┴───────┐
                                    ▼               ▼
                              Event Store      Event Audit
                              (可恢复不完整)    (compaction 丢轨迹)
                                    │               │
                                    ▼               ▼
                              Market Link ────✅────▶ Freeze Prediction
                                    │                      │
                          ⚡ 断点 C:               ⚡ 断点 D:
                          resolve 不消费          ALL dormant, 0 act
                          contract_id             watch/skip only
                                    │                      │
                                    ▼                      ▼
                              Resolve Outcome ──✅──▶ Calibration
                                    │                      │
                          ⚡ 断点 E:               ⚡ 断点 F:
                          AUTO_VERIFY=1.0         calibration_summary n=0
                          → pending 永久          → trust 恒定 0.5
                                    │                      │
                                    ▼                      ▼
                                   Trust ────✅───▶ Decision Report
                                                          │
                                                  ⚡ 断点 G:
                                                  act=0 → "永无建议行动"
```

---

## 数据丢失风险排行 (严重→轻微)

| # | 风险 | 触发条件 | 影响 |
|---|------|----------|------|
| 1 | save_events 覆盖 outcome/calibration | 重扫已有结算事件 | 结算记录消失，可重复 resolve |
| 2 | save_events 覆盖 calibration_components | 同上 | 组件 Brier 历史清零 |
| 3 | compaction 丢早期概率轨迹 | 长期运行 | M3 edge trajectory 偏短窗口 |
| 4 | _persist_events 内 save 成功 freeze 失败 | freeze 抛错 | event 入库但无 prediction |
| 5 | autio_resolve match < 1.0 → pending 永远 | 默认 + 无 UI | 事件永不结算 |
| 6 | resolve_with_calibration 重复 resolve 覆盖 | 手动重复 POST | 重复 outcome snapshot |
| 7 | mult-row prediction superseded | re-scan | 旧 commitment 被标记但保留 |

---

## 永远不会触发的逻辑

| 逻辑 | 位置 | 激活条件 | 为何当前永不触发 |
|------|------|----------|------------------|
| `decide → "act"` | `diagnosis_service.py:66` | qualified=True + \|adjusted_edge\| ≥ 10 | n<8 → qualified=False → caps at watch |
| `calibration_summary → non-zero` | `prediction_store.py:461-531` | ≥1 scored+act prediction | 0 act → 0 scored |
| `calibration_trust → non-dormant` | `diagnosis_service.py:27-43` | n ≥ 8 | segment_skill n=0 |
| `adjust_probability → actual adjustment` | `calibration_feedback_service.py:187-200` | ① ENABLED=True ② ≥8 samples per component ③ ≥2 components qualified | ① 默认 False ② 每个 component 需 8 个 calibration_components 记录 |
| `auto_verify → verify (score < 1.0)` | `event_resolve_service.py:237` | match_score ≥ 1.0 | 1.0 只匹配精确归一化 |
| `pending → human verify` | `routes/events.py:228-246` | 用户 POST /verify | 前端无 UI, 无人触发 |

---

## Dormant 状态无法毕业的风险

### 毕业路径

```
Dormant ───需要 n ≥ 8 resolved─→ Qualified ─── |adjusted_edge| ≥ 10 ─→ ACT
                 ↑
         需要: watch/act prediction 被 resolve
          → event 在 predict market 结算
          → auto-resolve 匹配成功 (score≥1.0)
```

### 阻碍毕业的 4 个瓶颈

| 瓶颈 | 本质 | 严重度 |
|------|------|--------|
| 长期事件阻力 | 大多数预测市场到期日在 2045/2050，数年不结算 | 高 |
| match threshold=1.0 | 精确匹配条件下极少事件能 auto-verify | 高 |
| 无人工 verify UI | pending 队列无出口 | 高 |
| 加密候选排挤 | 地缘政治刷屏 → 加密/crypto 事件进不了候选池 | 中 |

### 如果保持 AUTO_VERIFY_THRESHOLD=1.0

毕业时间线预估：
- 需要 ≥8 个 watch prediction 在某 category 被 resolve
- resolve 需要精确匹配 → 概率极低
- 当前 categories ("monetary_policy", "geopolitics", "crypto", "unknown"...)
- 假设日扫 10 事件，全 market-gated，每事件一条 watch prediction
- 每天产生 ≤10 条 prediction (有缓存过滤)
- 精确匹配可能 0 条/天 auto-resolve
- → 毕业时间 "可能数年或在 Auto-verify 下永远不到来"

### 减缓方案

1. **降低 AUTO_VERIFY_THRESHOLD 到 0.85–0.90** → 提高 auto-verify 率
2. **增加人工 verify 前端 UI** → pending 有出口
3. **短期降低 CALIBRATION_FEEDBACK_MIN_SAMPLES 到 3–5** → 降低毕业门槛
4. **手动 resolve 一批短期事件** → 快速攒样本


---

# 第三部分：存储设计专项审计

日期：2026-06-19  
范围：`event_store.json`、`event_audit.jsonl`、`v2_loop.db` 中的 `event_market_links` / `predictions`，以及 freeze / resolve / trust 统计路径。  
审查重点：One Event One Prediction、多版本 prediction、重复冻结、重复 resolve、trust 统计污染、未来迁移障碍。

---

## 1. 当前实际数据模型

```text
┌─────────────────────────────────────────────────────────────────┐
│                     event_store.json                             │
│  (JSON, mutable, 全量读写, threading.RLock)                      │
│                                                                 │
│  <event_id>: {                                                  │
│    first_seen: ISO8601                                          │
│    last_updated: ISO8601                                        │
│    record: {                                                    │
│      event_id:         str  (SHA256 of question[:200])          │
│      event_title:      str                                      │
│      event_title_zh:   str                                      │
│      probability:      { baseline, estimated, change, direction}│
│      credibility:      { score, level, confidence, ... }         │
│      impact:           { score, level, drivers }                 │
│      value_score:      int                                      │
│      source:           { type, platform, source_id, url, ... }   │
│      tracking:         { status, priority }   ← user-owned       │
│      outcome?:         { status, actual_outcome, ... }  ← ⚠ mutable│
│      calibration?:     { brier_score, skill, ... }     ← ⚠ mutable│
│      calibration_components?: { market, llm, cross }   ← ⚠ mutable│
│      legacy_analysis:  { ... }                                  │
│      evidence_items:   [...]                                    │
│    }                                                             │
│  }                                                               │
│                                                                 │
│  merge policy (save_events):                                     │
│    ✅ 保留: first_seen, existing_tracking                        │
│    ❌ 不保留: outcome, calibration, calibration_components       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    event_audit.jsonl                             │
│  (JSONL, append + compaction, threading.RLock)                   │
│                                                                 │
│  概率快照: { timestamp, event_id, event_title, baseline,          │
│             estimated, change, direction, credibility_score,     │
│             impact_score, value_score }                          │
│                                                                 │
│  outcome 快照: { kind: "outcome", timestamp, event_id,            │
│                 estimated: null, outcome: { status, ... } }      │
│                                                                 │
│  compaction: 超过 threshold → 每个 event 保留最多                   │
│              MAX_PER_EVENT 条概率快照 + 1 条 outcome 快照         │
│              (atomic rewrite via tempfile + os.replace)           │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│              v2_loop.db (SQLite, WAL, single file)               │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 表: event_market_links                                    │  │
│  │                                                          │  │
│  │ id            TEXT PK                                     │  │
│  │ event_id      TEXT NOT NULL                               │  │
│  │ contract_id   TEXT NOT NULL DEFAULT ''                    │  │
│  │ market_name   TEXT NOT NULL DEFAULT '' ← source platform │  │
│  │ market_question TEXT NOT NULL DEFAULT ''                  │  │
│  │ resolution_criteria TEXT NOT NULL DEFAULT ''             │  │
│  │ link_method   TEXT NOT NULL DEFAULT 'auto'                │  │
│  │ link_confidence REAL NOT NULL DEFAULT 0.0                 │  │
│  │ verified      INTEGER NOT NULL DEFAULT 0                  │  │
│  │ linked_at     TEXT NOT NULL DEFAULT ''                    │  │
│  │                                                          │  │
│  │ UNIQUE(event_id, contract_id)                             │  │
│  │ INDEX(event_id)  INDEX(contract_id)                       │  │
│  │                                                          │  │
│  │ ⚠ 无 FK 到 event_store.json                              │  │
│  │ ⚠ 同一 event 可有多个 verified links                     │  │
│  │ ⚠ 同一 contract 可 linked 到多个 event                   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 表: predictions                                           │  │
│  │                                                          │  │
│  │ id            TEXT PK                                     │  │
│  │ event_id      TEXT NOT NULL        ← ⚠ 无 UNIQUE        │  │
│  │ contract_id   TEXT NOT NULL DEFAULT ''                    │  │
│  │ platform      TEXT NOT NULL DEFAULT ''                    │  │
│  │ base_rate_category TEXT NOT NULL DEFAULT 'unknown'        │  │
│  │ ai_probability   REAL NOT NULL      (0-100)               │  │
│  │ market_probability REAL NOT NULL    (0-100)               │  │
│  │ raw_edge      REAL NOT NULL                               │  │
│  │ trust         REAL                ← M2 diagnosis frozen  │  │
│  │ adjusted_edge REAL                ← M2 diagnosis frozen  │  │
│  │ liquidity     REAL NOT NULL DEFAULT 0.0                   │  │
│  │ volume        REAL NOT NULL DEFAULT 0.0                   │  │
│  │ decision      TEXT NOT NULL DEFAULT 'tracked'              │  │
│  │ liquidity_factor REAL            ← diagnosis frozen      │  │
│  │ qualified     INTEGER            ← diagnosis frozen      │  │
│  │ segment_n     INTEGER            ← diagnosis frozen      │  │
│  │ segment_skill  REAL              ← diagnosis frozen      │  │
│  │ created_at    TEXT NOT NULL DEFAULT ''                    │  │
│  │ status        TEXT NOT NULL DEFAULT 'open'                 │  │
│  │ actual_outcome REAL                                       │  │
│  │ brier_score   REAL                                        │  │
│  │ resolved_at   TEXT                                        │  │
│  │                                                          │  │
│  │ INDEX(event_id)  INDEX(status)  INDEX(base_rate_category) │  │
│  │                                                          │  │
│  │ ⚠ event_id UNIQUE 被 _migrate 移除 (M3 多行)            │  │
│  │ ⚠ 支持 5 终态: open/scored/observed/voided/superseded   │  │
│  │ ⚠ 无 CHECK 约束 (ai_probability 范围等)                 │  │
│  │ ⚠ 无 FK 到 event_store.json 或 event_market_links       │  │
│  │ ⚠ 无 partial UNIQUE WHERE status='open'                 │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

跨存储关系:
  event_store.json.event_id  ←→  event_market_links.event_id  (无 FK)
  event_store.json.event_id  ←→  predictions.event_id          (无 FK)
  event_market_links.event_id ←→ predictions.event_id           (无 FK, 同 DB)
  event_market_links.contract_id 与 predictions.contract_id  (同一 event 应对齐)
```

---

## 目标模型（如果坚持 One Event One Prediction）

```text
events (或 event_store.json)
└── 每个 event_id 一个 mutable event record

event_market_links
├── UNIQUE(event_id, contract_id)
└── verified link(s) with resolution_criteria

predictions
├── UNIQUE(event_id)  ← 一个 event 仅一条 committed prediction
├── status transitions in-place (open → scored/observed/voided)
└── 无 superseded, 无 prediction history

event_audit
└── probability trajectory only (已有的行为，合规)
```

---

## 2. One Event One Prediction 检查

**结论：❌ 不符合。**

| 证据 | 位置 |
|------|------|
| `predictions` schema 无 `UNIQUE(event_id)` | `prediction_store.py` line 39-64 `_SCHEMA` |
| `_migrate()` 显式检测旧 `event_id UNIQUE` 并重建表移除 | `prediction_store.py` lines 99-132 |
| `get_predictions(event_id)` 返回完整多行 history | `prediction_store.py` lines 378-389 |
| 模块 docstring 声明 "multi-row per event (M3)" | `prediction_store.py` lines 8-9 |
| `Prediction` 模型 docstring 声明 "M3 append-only ledger" | `event.py` lines 181-196 |
| 测试 `test_prediction_store.py` 保护多行 ledger 行为 | 多个测试用例 |

当前设计只保证 **One Event → At Most One Open Prediction**（应用层语义），不保证 One Event → One Prediction（仅一个 commitment）。

---

## 3. 隐式多版本 Prediction

**结论：⚠️ 存在，且为显式实现。**

多版本来源：

| 触发条件 | 行为 | 代码位置 |
|----------|------|----------|
| 首次 `freeze_prediction()` | INSERT 一条 `open` 行 | lines 272-292 |
| 重扫 decision 变化 (watch→watch or watch→act) | 旧行 → `superseded`，新行 INSERT `open` | lines 264-271 |
| 重扫 same decision but `_materially_changed` | 同上 | lines 165-181, 260-271 |
| resolve 后 open→scored/observed，再重扫 | 无 open 行 → INSERT 新 open | lines 254-259 |
| resolve 后 open→voided，再重扫 | 同上 | lines 338-360 |

**角色冲突**：

```
predictions 表同时承担两个角色:
  Role A: commitment store — "我们承诺的是哪一次判断"
  Role B: trajectory ledger — "概率随时间如何变化"

这两个角色语义冲突:
  - Commitment 应该: 一次承诺，不变 (或原地修正)
  - Trajectory 应该: 每次变化记录，完整历史

当前 predictions 混合两者 → 既不是纯粹 commitment (因为 re-snapshot)
   也不是纯粹 ledger (因为 open 行会被 update superseded/scored/voided)
```

**如果坚持多版本**，需引入显式版本模型：
- `prediction_series_id` — 同一 event 的 prediction 系列
- `version_no` — 递增版本号
- `is_current` — 当前生效的版本
- `valid_from` / `valid_to` — 时间窗口
- 明确哪些版本进入 trust（当前版本？所有 scored+observed？）

---

## 4. 重复冻结风险

### 风险 A: 已 resolved event 可被重扫覆盖回 unresolved (P0)

**路径**：`save_events()` merge policy 只保留 tracking，不保留 outcome/calibration/calibration_components。

```python
# event_store.py lines 56-63
existing_tracking = (existing.get("record") or {}).get("tracking")
if existing_tracking is not None:
    record["tracking"] = existing_tracking
# outcome, calibration, calibration_components — NOT preserved
```

**后果**：
1. `discover_events` 重扫 → `save_events` 覆盖 → outcome 丢失
2. `auto_resolve_events` 通过 `record.get("outcome") is not None` 判断跳过 → 失效
3. `freeze_prediction` 再次调用 → INSERT 新 open prediction
4. 新 prediction 可能再次 resolve → 同一 event 产生多个 scored/observed 行
5. trust 统计被污染（同一真实事件重复计数）

### 风险 B: resolved 后无 open 行 → 重扫产生新 open (P1)

`freeze_prediction` 在 resolved (无 open 行) 后再次调用 → INSERT 全新 open 行。

```
Timeline:
  T0: freeze → open (watch)
  T1: resolve → scored/observed (terminal, no open)
  T2: re-scan → freeze → INSERT new open (watch)  ← 第二个 commitment
  T3: re-resolve → scored/observed  ← 第二个 terminal
  → 同一 event 2 个 terminal predictions
```

### 风险 C: 无数据库级 "最多一个 open" 约束 (P1)

应用层通过 `SELECT ... WHERE event_id=? AND status='open'` 保护，但数据库层没有 `UNIQUE(event_id) WHERE status='open'` 部分唯一索引。`_WRITE_LOCK` 是进程内锁，多进程/多 worker 仍可能产生两个 open predictions。

---

## 5. 重复 Resolve 风险

### 手动 resolve 不是幂等的 (P1)

`resolve_with_calibration()` 开头只检查 `get_event(event_id) is None`，不检查是否已有 outcome。

| 操作 | 幂等性 | 风险 |
|------|--------|------|
| `resolve_event()` | ❌ | 覆盖旧 outcome + calibration |
| `record_outcome()` | ❌ | 追加重复 outcome 快照 |
| `score_prediction()` | ⚠️ 部分 | 已无 open 行 → 返回 None → 不再 score |
| `void_prediction()` | ⚠️ 部分 | 同上 |

对 prediction scoring 部分幂等，但对 event_store 和 audit log 不幂等。

### auto resolve 有幂等保护但依赖 JSON outcome (P1)

`auto_resolve_events()` 通过 `record.get("outcome") is not None` 跳过。但如果 outcome 被重扫覆盖（见风险 A），保护失效。

---

## 6. Trust 统计污染

### 当前查询口径（基本正确）

| 查询 | WHERE 条件 | 正确性 |
|------|-----------|--------|
| `calibration_summary()` | `status='scored' AND decision='act'` | ✅ act-only |
| `segment_skill()` | `status IN ('scored', 'observed') AND decision IN ('act', 'watch')` | ✅ act+watch, 排除 skip |
| `list_open_opportunities()` | `status='open' AND decision IN ('act', 'watch')` | ✅ 只开放态 |

**正确点**：
- `skip` 不进入 trust ✅
- `superseded` 不进入 trust（status 不匹配）✅
- `voided` 不进入 trust（score_prediction 才写 terminal）✅
- `observed` watch 已进入 `segment_skill`（合规：用于 qualification gate）✅

### 污染风险

| 风险 | 触发条件 | 严重度 |
|------|----------|--------|
| 同一 event 多次 freeze+resolve → 多个 scored/observed | 重扫覆盖 + 重新 freeze | 高 |
| 同一 category 内同一 event 贡献多个样本 | 同上 | 高 |
| 不同 category 间同一 event 污染 | 重扫后 category 变化 → 不同 prediction 版本不同 category | 中 |
| `observed` watch 来自重复 freeze | 重扫后新 open → resolve → observed | 中 |
| `score_prediction` 无 link verified 检查 | 依赖调用方正确传参 | 低 |

---

## 7. Append-only 合规性

| Store | 当前行为 | Append-only? | 备注 |
|-------|----------|--------------|------|
| `event_store.json` | upsert mutable record | ❌ | 原地覆盖，不保留历史版本 |
| `event_audit.jsonl` | append + compaction | ⚠️ semi | 追加但会 compaction 丢旧行 |
| `event_market_links` | upsert, verified 可改 | ❌ | 覆盖更新，不保留 link history |
| `predictions` | INSERT rows, UPDATE status | ❌ | 多行但 status 原地改为 terminal |

**严格 append-only** 要求：所有写操作是 INSERT，已有行不可变。当前系统无 store 满足严格 append-only。

**合规的 "近 append-only"**：
- `event_audit.jsonl` 最接近 — 追加 + bonded compaction（保留最新 N 条）。对于概率轨迹用途可接受，但对于合规审计不够。
- `predictions` 插入多行 — 接近但 status 原地更新（open→scored/superseded），不满足 "已有行不可变"。

**语义澄清**：
- 不能把 `predictions` 称为 "严格 append-only ledger" — 它会 UPDATE status
- 不能把 `event_audit.jsonl` 称为 "永久审计日志" — compaction 丢历史

---

## 8. JSON Store / SQLite 风险评估

### event_store.json 风险矩阵

| 维度 | 状态 | 说明 |
|------|------|------|
| 原子写 | ✅ | `write_json_atomic` (tmp + os.replace) |
| corrupt 防护 | ✅ | `_load_for_write` 用 `read_json_strict` (corrupt→抛异常) |
| 并发写 | ⚠️ | `threading.RLock` (进程内), 无跨进程锁 |
| merge policy | ❌ | 只保留 tracking, 不保留 outcome/calibration |
| 全量读写 | ⚠️ | ~59 事件尚可, 增长后写放大 |
| 跨存储事务 | ❌ | 与 SQLite 无事务一致性 |
| 备份 | ✅ | 有 `.corrupt` quarantine + `.bak` 快照 |

**主要风险**：merge policy 不完整导致静默数据丢失（P0）。全量读写是次要问题（当前体量可接受）。

### event_audit.jsonl 风险矩阵

| 维度 | 状态 | 说明 |
|------|------|------|
| 原子追加 | ✅ | 文件锁 + `open(a)` |
| 原子 compaction | ✅ | `rewrite_lines_atomic` (tmp + os.replace) |
| 数据丢失 | ⚠️ | compaction 丢旧概率快照 |
| 乱序 | ⚠️ | 追加顺序 = 调用顺序, 无时间排序保证 |
| 并发 | ⚠️ | `threading.RLock` (进程内) |
| 恢复 | ✅ | outcome 快照始终保留 (compaction 保护) |

**主要风险**：compaction 丢早期轨迹，不适合作为长期事实表。

### SQLite Store (v2_loop.db) 风险矩阵

| 维度 | 状态 | 说明 |
|------|------|------|
| 并发写 | ✅ | `_WRITE_LOCK` + WAL (进程内安全) |
| 事务 | ✅ | commit on success / rollback on exception |
| FK 完整性 | ❌ | `PRAGMA foreign_keys=ON` 但 schema 无 FK 声明 |
| 唯一约束 | ⚠️ | event_market_links: ✅ UNIQUE(event_id, contract_id) |
| 唯一约束 | ❌ | predictions: 无 event-level unique 或 partial unique |
| CHECK 约束 | ❌ | 无 ai_probability/decision/status 范围约束 |
| Schema version | ❌ | 无 migration log table, ad hoc `_migrate()` |
| 重初始化 | ⚠️ | `_INITIALIZED` 以 path 缓存 → 同进程 schema 变化不重新迁移 |
| 跨 DB 引用 | ❌ | predictions.event_id 无 FK 到 event_store (JSON 不可能有) |

**主要风险**：
- predictions 无 event-level 唯一约束 — P0 设计问题
- 无 CHECK 约束 — 依赖应用层正确性
- 无 schema version table — 迁移不可审计

### 分裂存储风险

```
event_store.json  ← 事件身份 + 结算
event_audit.jsonl ← 轨迹
v2_loop.db        ← link + prediction

三者关系:
  - predictions.event_id 引用 event_store.json 中的 key — 但无 FK 保护
  - resolve 同时写 event_store.json (outcome) + event_audit.jsonl (snapshot) + v2_loop.db (score)
  - 三步无事务边界 — 任一失败导致不一致
  - 实际: _persist_events 中 save_event → record_event → freeze_prediction 同 try/except 吞错
```

---

## 9. 唯一约束建议

### 如果回退到 One Event One Prediction

```sql
-- 恢复 event-level 唯一性
CREATE UNIQUE INDEX uq_predictions_event ON predictions(event_id);

-- 清理 superseded 行后，移除其合法状态
-- CHECK (status IN ('open', 'scored', 'observed', 'voided'))
```

### 如果保留多版本（P0-1 决定不出手）

最低限度：

```sql
-- 数据库级 "最多一个 open prediction per event"
CREATE UNIQUE INDEX uq_predictions_one_open
ON predictions(event_id) WHERE status = 'open';

-- 字段合法性
ALTER TABLE predictions ADD CHECK (ai_probability BETWEEN 0 AND 100);
ALTER TABLE predictions ADD CHECK (market_probability BETWEEN 0 AND 100);
ALTER TABLE predictions ADD CHECK (actual_outcome IS NULL OR actual_outcome BETWEEN 0 AND 100);
ALTER TABLE predictions ADD CHECK (decision IN ('act', 'watch', 'skip', 'tracked'));
ALTER TABLE predictions ADD CHECK (status IN ('open', 'scored', 'observed', 'voided', 'superseded'));
```

### event_market_links

```sql
-- 同一 event 最多一个 verified link (严格环境)
CREATE UNIQUE INDEX uq_event_one_verified_link
ON event_market_links(event_id) WHERE verified = 1;

-- 同一真实 contract 不绑定多个 event
CREATE UNIQUE INDEX uq_contract_one_event
ON event_market_links(market_name, contract_id) WHERE contract_id <> '';
```

---

## 10. 幂等性总表

| 操作 | 当前幂等性 | 风险 |
|------|-----------|------|
| `save_events()` | ❌ 对 event_id upsert 但 merge 不完整 | 覆盖 outcome/calibration |
| `record_event()` | ❌ 每次 append | 重复扫描污染趋势 (有缓存部分防护) |
| `freeze_prediction()` | ⚠️ 对 open row 部分幂等 | 已 resolve 后重新 freeze; 多进程重复 open |
| `score_prediction()` | ✅ 对 terminal 幂等 | 只要无 open row 即 no-op |
| `void_prediction()` | ✅ 对 terminal 幂等 | 同上 |
| `resolve_with_calibration()` | ❌ 不幂等 | 覆盖 outcome + 追加 outcome snapshot |
| `auto_resolve_events()` | ⚠️ 依赖 outcome 跳过 | outcome 丢失时失效 |
| `upsert_link()` | ✅ 对 (event_id, contract_id) | 覆盖式 upsert |
| `set_verified()` | ✅ 幂等 | 设同一个值 no-op |

**优先修复**：

| 优先级 | 修复项目 | 原因 |
|--------|----------|------|
| 0 | `save_events()` 保留 outcome/calibration/calibration_components | 防止静默数据丢失 |
| 1 | `predictions` 加 UNIQUE(event_id) 或 partial unique WHERE status='open' | 防止重复 freeze |
| 2 | `resolve_with_calibration()` 对已有 outcome 默认 no-op | 防止重复 resolve |
| 3 | `event_market_links` verified link 唯一性策略 | 防止 ambiguous identity |
| 4 | predictions 加 CHECK 约束 | 防御性数据完整性 |

---

## 11. 未来迁移障碍

| 障碍 | 阻碍的迁移 | 当前影响 |
|------|-----------|----------|
| JSON event store + SQLite loop store 分裂 | 统一到 relational schema | 无 FK 保护一致性 |
| predictions 混合 commitment + trajectory | 拆分为 commitment 表 + trajectory 表 | 语义模糊累积 |
| event_audit.jsonl compaction | 升格为永久审计日志 | 不适合合规级 append-only |
| event_id 基于 question hash | 引入 canonical event identity | 问题文本变化 → 新 event_id |
| event_market_links 允许一 contract 多 event | 全局重复 resolve 防护 | 同一结算绑定到不同事件 |
| legacy_analysis 塞进 event record | schema 边界模糊 | 越来越多字段嵌套 |
| 无 schema version / migration ledger | 可审计迁移 | 迁移历史不可追溯 |
| `_INITIALIZED` 以 path 缓存 | dynamic schema 迁移 | 同进程 schema 变更不被检测 |

**建议迁移方向**：

```
短期（当前里程碑）：
  保持 JSON event store，修复 merge/idempotency
  恢复 predictions one-event-one-prediction
  增加关键唯一约束和 CHECK

中期（M2-M3）：
  将 outcome 从 mutable JSON → SQLite outcomes 表
  将 event identity / link / prediction / outcome 放入同一 SQLite transaction

长期（M4+）：
  统一到 relational schema (PostgreSQL/SQLite)
  JSON 降格为 cache/export，不作为 source of truth
```

---

## 12. 最终判断

| 维度 | 判等 | 评级 |
|------|------|------|
| One Event One Prediction | ❌ 不符合 | P0 |
| 隐式多版本 | ⚠️ 显式存在 | P0 |
| 重复冻结风险 | ❌ 存在 (3 条路径) | P0 |
| 重复 Resolve 风险 | ❌ 不幂等 | P1 |
| Trust 统计污染 | ⚠️ 按设计当前未污染，但多版本下会污染 | P1 |
| Append-only 合规 | ❌ 无 store 严格合规 | P2 |
| JSON/SQLite 安全 | ⚠️ JSON 可接受，SQLite 缺约束 | P1/P2 |
| 未来迁移障碍 | ⚠️ 7 项清单，部分需中期处理 | P2 |

**当前最需要立刻处理的**：

1. 修复 `save_events()` merge → 保留 outcome/calibration/calibration_components
2. 决定 predictions 语义 (commitment vs ledger) 并相应加唯一约束
3. 让 `resolve_with_calibration()` 对已 resolved event default no-op
4. predictions 增加 CHECK 约束 (ai_probability 0-100, decision/status 枚举)


---

# 第四部分：开源就绪审计 — 可删除代码

日期：2026-06-19  
标准：删除后不影响功能 / 降低复杂度 / 减少维护成本  
方法：全量扫描 `backend/` + `frontend/`，识别死代码、废弃接口、重复实现、历史兼容层、永不执行分支、过度抽象、无意义配置。

---

## 目录
1. [按收益排序总表](#按收益排序总表)
2. [P0: 遗留路由器 (20+ 端点)](#p0-遗留路由器)
3. [P0: 重复服务 (2 组)](#p0-重复服务)
4. [P0: 过度抽象的 Agents 目录](#p0-过度抽象的-agents-目录)
5. [P1: 死代码端点 (events 路由器内)](#p1-死代码端点)
6. [P1: 旧版 HTML 仪表板](#p1-旧版-html-仪表板)
7. [P2: 前端死代码](#p2-前端死代码)
8. [P2: 死配置 + 死文件](#p2-死配置--死文件)
9. [删除执行建议](#删除执行建议)

---

## 按收益排序总表

| # | 项目 | 代码量 | 收益类型 | 风险 |
|---|------|--------|----------|------|
| 1 | **8 个遗留路由器** + services | ~1200 行 | 降低复杂度 + 减少维护 | 无 (前端不调用) |
| 2 | **agents/ 目录** (10 files) | 1079 行 | 降低复杂度 | 中 (`/scan/deep` 唯一消费者) |
| 3 | **auto_resolve_service.py** (legacy) | 70 行 | 消除重复 | 低 |
| 4 | **calibration_service.py** (legacy) | 129 行 | 消除重复 | 低 (math 需提取) |
| 5 | **旧版 HTML 仪表板** (2 files) | ~96 KB | 减少体积 | 无 |
| 6 | **死代码端点** (6 endpoints) | ~80 行 | 减少维护 | 无 (无前端消费者) |
| 7 | **event_cache.py 合并** | 76 行 | 消除重复 | 低 |
| 8 | **agent_memory.py** (legacy) | 74 行 | 消除遗留 | 低 (仅 legacy consumers) |
| 9 | **前端死代码 + 样板文件** | ~50 行 | 清理 | 无 |
| 10 | **signal_service.py** (废弃) | 10 行 | 清理 | 无 |
| 11 | **MARKET_SCAN_LIMIT** (死配置) | 2 行 | 清理 | 无 |
| 12 | **备份/临时文件** | ~4 MB | 减少体积 | 无 (gitignored) |

---

## P0: 遗留路由器

### 全貌

`backend/app/api/router.py` 注册了 8 个遗留子路由器，`main.py` 额外注册了 `scanner.router`。前端 `api.ts` 仅调用 `events` 路由器 — **所有其他路由器的 20+ 个端点均无前端消费者**。

| 路由器 | 文件 | 端点 | 自述 |
|--------|------|------|------|
| `scanner` | `routes/scanner.py` (566行) | 5 | "Compatibility-only surface" |
| `analysis` | `routes/analysis.py` | 2 | "Compatibility-only surface" |
| `calibration` | `routes/calibration.py` | 3 | Legacy calibration |
| `resolve` | `routes/resolve.py` | 3 | "Compatibility-only surface" |
| `markets` | `routes/markets.py` | 1 | Legacy market data |
| `news` | `routes/news.py` | 1 | Legacy news feed |
| `trades` | `routes/trades.py` | 4 | Legacy trading |
| `backtest` | `routes/backtest.py` | 3 | Legacy backtest |
| `signal_accuracy` | `routes/signal_accuracy.py` | 1 | Legacy signal accuracy |

### 关联服务（一并删除）

这些遗留路由器依赖以下服务，它们被事件层等价物完全替代：

| 服务 | 替代品 | 遗留消费者 |
|------|--------|------------|
| `auto_resolve_service.py` (70行) | `event_resolve_service.py` | `resolve.py`, `scheduler.py`(evening_resolve) |
| `calibration_service.py` (129行) | `calibration_service_event.py` | `calibration.py`, `backtest.py`, `scheduler.py` |
| `analysis_audit_service.py` (149行) | `event_audit_service.py` | `scanner.py`, `analysis.py`, legacy routes |
| `agent_memory.py` (74行) | `prediction_store.py` | `scanner.py`, `resolve.py`, scheduler |
| `signal_audit_service.py` (66行) | N/A (无新替代) | `scanner.py` |
| `market_memory.py` (105行) | 无直接替代 (TTL cache) | `scanner.py`, `orchestrator.py`, scheduler |
| `polymarket_service.py` | 无替代 (仍在事件层使用) | — (保留) |
| `gnews_service.py` | 无替代 (事件层使用) | — (保留) |
| `rss_service.py` | 无替代 (事件层使用) | — (保留) |

### 核心障碍：morning_scan 仍依赖遗留路径

`_job_morning_scan()`（scheduler.py 第 57-163 行）是一个独立管线，写入 `analysis_audit.jsonl` / `agent_memory.json` / `market_cache.json`，绕过完整的事件层。删除遗留路由器前需处理 `morning_scan`。

**选项 A**：将 morning_scan 迁移到 `discover_events`（即 event_auto_discover 已做的事）→ 删除整个遗留管线  
**选项 B**：保留 morning_scan 但直接调用服务，删除 HTTP 端点

---

## P0: 重复服务

### 重复 #1: auto_resolve_service.py ↔ event_resolve_service.py

| 维度 | auto_resolve_service.py | event_resolve_service.py |
|------|------------------------|--------------------------|
| 行数 | 70 | 353 |
| 源 | 仅 Polymarket | Polymarket + Manifold + Kalshi |
| 写入 | `agent_memory.json` + `analysis_audit.jsonl` | `event_store.json` + `event_audit.jsonl` + `predictions` |
| 身份校验 | 无 | 有 (fail-closed, identity conflict) |
| 校准 | 无 (纯 resolve) | 有 (Brier + skill + trajectory) |
| 调用者 | `routes/resolve.py` + `scheduler._job_evening_resolve` | `routes/events.py` + `scheduler._job_event_auto_resolve` |

**event_resolve_service 是严格超集。** 两个都在 scheduler 中运行（22:00 + 22:30），产生双重工作。

**删除**：`auto_resolve_service.py`。将 `_job_evening_resolve` 改为调用 `event_resolve_service.auto_resolve_events` 或直接删除（event_auto_resolve 已做同样的事）。

### 重复 #2: calibration_service.py ↔ calibration_service_event.py

两个文件实现相同的 3 个数学函数：`brier_score()`, `skill_score()`, `grade()`。

| 文件 | 设计 | 消费者 |
|------|------|--------|
| `calibration_service.py` | I/O 耦合 (读 JSONL) | `routes/calibration.py`, `routes/backtest.py`, `scheduler` |
| `calibration_service_event.py` | 纯函数 (无 I/O) | `event_resolve_service.py`, `routes/events.py`, `prediction_store.py`, `diagnosis_service.py` |

**calibration_service_event 是更好的实现：** 纯函数、参数约束、数值安全。

**删除**：`calibration_service.py`。将其剩余的 `_grade_brier` 消费者 (`backtest.py`) 改为从 `calibration_service_event` 导入。

### 重复 #3: event_cache.py ↔ market_memory.py

| 维度 | market_memory.py | event_cache.py |
|------|------------------|----------------|
| 行数 | 105 | 76 |
| TTL | 1h (可配置) | 1h (可配置) |
| 键策略 | question hash | question hash |
| 锁机制 | `threading.RLock` per file | `threading.RLock` per file |
| 文件 | `market_cache.json` | `event_cache.json` |

**代码逐段重复。** 两个文件有相同的 `_cache_key()`, `_purge_expired()`, TTL 逻辑和锁模式。

**合并**：提取 `TTLCache` 类到 `app/utils/ttl_cache.py`，分别实例化为 `market_cache` 和 `event_cache`。

---

## P0: 过度抽象的 Agents 目录

`backend/app/agents/` 包含 10 个文件，1079 行代码，3 层抽象：

```
BaseAgent (13行) → 8 具体代理 (1079行合计) → AgentOrchestrator (295行)
```

**唯一调用者**：`routes/scanner.py` 的 `GET /scan/deep` — 这是一个遗留端点。

每个代理 `NarrativeAgent`, `ProbabilityAgent`, `ContrarianAgent`, `CrowdAgent`, `FundamentalAgent`, `ManipulationAgent`, `RiskAgent`, `SignalAgent`, `JudgeAgent`：
- 仅被 orchestrator 调用
- 无独立测试
- 无其他消费者
- 无复用

而新的事件流程 `analyze_event_question` → `ai_analysis_service.analyze_market` → **一次 LLM 调用** 完成全部分析。

**删除**：整个 `agents/` 目录（1079 行）。如果 deep analysis 能力需要保留，迁移为一个 ~200 行的 `deep_analysis.py` 模块，含内联提示函数和简单流水线。

**节约**：1079 行 → 0 行（或 ~200 行扁平化），文件数 10 → 0（或 1）。

---

## P1: 死代码端点（events 路由器内）

`backend/app/api/routes/events.py` 中 6 个端点无前端消费者：

| 端点 | 行号 | 前端替代 | 判定 |
|------|------|----------|------|
| `POST /{event_id}/resolve` | 172-199 | `POST /resolve/auto` (历史页按钮) | 手动结算 UI 未做 |
| `GET /links/pending` | 217-225 | 无 | 人工审核工作流未实现 |
| `POST /{event_id}/link/verify` | 228-246 | 无 | 同上 |
| `GET /predictions/recent` | 260-264 | 无 | prediction 列表未做 |
| `GET /{event_id}/decision` | 298-313 | `GET /decisions/open` (批量) | 单事件决策已由批量覆盖 |
| `GET /{event_id}/predictions` | 316-325 | 无 | prediction 账本未做 |

**关联后端函数**（一并删除）：
- `prediction_store.list_recent()` — 仅被 `GET /predictions/recent` 调用
- `prediction_store.get_predictions()` — 仅被 `GET /{event_id}/predictions` 调用

---

## P1: 旧版 HTML 仪表板

| 文件 | 大小 | 路由 |
|------|------|------|
| `backend/static/index.html` | 47.9 KB | `GET /dashboard` |
| `backend/static/index_zh.html` | 48.3 KB | `GET /dashboard/zh` |

Next.js 前端 (`frontend/out/`) 已挂载到 `/`，完全替代了这些 v0 HTML 仪表板。

**删除**：两个 HTML 文件 + `main.py` 中 `serve_dashboard` / `serve_dashboard_zh` / `serve_dashboard_zh_compat` 三个路由函数。

---

## P2: 前端死代码

### api.ts — 从未调用的方法

| 方法 | 行号 | 说明 |
|------|------|------|
| `eventsApi.health()` | 144-150 | 调用 `/api/calibration/summary`（遗留端点），零调用 |

### format.ts — 从未导入的常量

| 导出 | 行号 | 说明 |
|------|------|------|
| `LEVEL_LABELS` | 32-36 | 从未被任何组件导入 |
| `STANCE_LABELS` | 56-60 | 从未被任何组件导入 |

### adapt.ts — 应改为私有的导出

| 导出 | 行号 | 说明 |
|------|------|------|
| `trendOf()` | 36 | 仅被同文件 `adaptRecord`/`adaptMover` 调用，无外部消费者 |

### 样板 SVG 文件

| 文件 | 说明 |
|------|------|
| `frontend/public/file.svg` | Next.js 模板文件 |
| `frontend/public/globe.svg` | Next.js 模板文件 |
| `frontend/public/next.svg` | Next.js 模板文件 |
| `frontend/public/vercel.svg` | Next.js 模板文件 |
| `frontend/public/window.svg` | Next.js 模板文件 |

全部在 `frontend/src/` 中零引用。

---

## P2: 死配置 + 死文件

### 零引用的配置项

| 配置项 | 文件 | 行号 |
|--------|------|------|
| `MARKET_SCAN_LIMIT` | `config.py` | 21 |

全量 grep `backend/app/` 确认：仅在定义处出现，零引用。

### 已标记废弃的文件

| 文件 | 说明 |
|------|------|
| `backend/app/services/signal_service.py` | 仅含 docstring "此文件已废弃"，零引用 |

### 备份和临时文件（gitignored）

| 文件 | 大小 |
|------|------|
| `backup-20260612-181108.tar.gz` | 470 KB |
| `backend/archive/backend.7z` | 19 KB |
| `backend/event_store.json.bak` | — |
| `.diff_temp.txt` | 221 KB |

---

## 删除执行建议

### Phase 1 — 零风险（立即可删）

```
删除前端样板 SVG (5 files)
删除 frontend/src/lib/api.ts eventsApi.health()
删除 frontend/src/lib/format.ts LEVEL_LABELS + STANCE_LABELS
删除 frontend/src/lib/adapt.ts trendOf export → private
删除 config.py MARKET_SCAN_LIMIT
删除 backend/app/services/signal_service.py
删除备份文件: backup-*.tar.gz, archive/*.7z, *.bak, .diff_temp.txt
```

**预计减少**: ~60 行代码 + ~4 MB 文件

### Phase 2 — 低风险（确认无依赖后删除）

```
删除旧版仪表板: static/index.html, static/index_zh.html, main.py 3 路由
删除 events.py 内 6 个死代码端点 + list_recent() + get_predictions()
合并 event_cache.py + market_memory.py → ttl_cache.py
删除 agent_memory.py (需先移除 morning_scan 中的调用)
```

**预计减少**: ~350 行代码 + ~96 KB 文件

### Phase 3 — 中风险（需迁移路径）

```
删除 agents/ 目录 → 迁移 /scan/deep 为 deep_analysis.py（可选：直接删除）
删除 auto_resolve_service.py → 统一用 event_resolve_service
删除 calibration_service.py → 提取共享 math → 其余删除
```

**预计减少**: ~1200 行代码

### Phase 4 — 高风险（需全局清理）

```
删除全部 8 个遗留路由器 + 关联 legacy 服务
删除 routes/scanner.py
删除 routes/analysis.py, calibration.py, resolve.py, markets.py, news.py, backtest.py, trades.py, signal_accuracy.py
删除 analysis_audit_service.py（无遗留消费者后）
删除信号审计（无遗留消费者后）
迁移 morning_scan 或彻底删除（event_discover 替代）
```

**预计减少**: ~1200 行代码

---

## 总节约估算

| Phase | 行数 | 文件数 | 数据 |
|-------|------|--------|------|
| Phase 1 | ~60 | ~12 | ~4 MB |
| Phase 2 | ~350 | ~6 | ~96 KB |
| Phase 3 | ~1200 | ~13 | — |
| Phase 4 | ~1200 | ~14 | — |
| **总计** | **~2800** | **~45** | **~4 MB** |

**当前代码库规模参考**: 后端 ~60K 行 Python (含测试) + 前端 ~3K 行 TS  
**预计清理**: 后端减少 ~5%，前端减少 ~2%，消除 2 组代码重复 + 1 组过度抽象