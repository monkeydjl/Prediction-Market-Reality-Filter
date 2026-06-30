# 架构审计意见：V2 闭环变更对照路线图原则

日期：2026-06-19
审查者视角：资深架构师
范围：本会话内对 V2 反馈闭环的全部代码变更（PR-A act-only scoring、PR-B resolution_criteria、PR-C 前端 M5、M3 append-only ledger、DecisionReport 解释字段、2026-06-19 全量审查修复）
方法：逐条对照下列 10 条路线图/设计原则，重点检查 Scope Creep / Dead Code / Premature Abstraction / Future Schema Leakage / 与路线图冲突，**不评代码风格**。

审查的 10 条原则：
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

本文件只记录审查意见，不修改业务代码。

---

## 一、总体判定

本次审查点名的违规，**绝大多数来自本会话最近几轮新增的代码**，尤其是被命名为
“M3 append-only ledger” 的那批改动。

原则 2 / 3 / 7 共同主张：**Prediction 是一次性 Commitment，轨迹（Trajectory）
属于 audit 快照层，M3 只在现有 audit log 上做 KPI，不重构预测账本。**

这恰好是项目原本的 M3 定义（“edge trajectory + fresh/decaying，KPI-only on the
existing audit log”）。但本会话在“直接做 M3”的驱动下，用一个 append-only 多行
账本重构覆盖了那个合规的 M3。这是本次所有 P0/P1 的根源。

关键观察：`analyze_edge_trajectory` / `rank_fresh_edges` 本来就从
`event_audit.jsonl` 快照读取轨迹——轨迹层已存在且合规。把轨迹再塞进 prediction
行，等于将同一概念实现两遍，且塞进了本该是 Commitment 的表。

<!-- AUDIT-BODY -->

---

## 二、P0 — 违反核心原则，建议回退

### P0-1：Append-on-material-change（把 Prediction 变成 Trajectory）— 违反原则 3

位置：`backend/app/memory/prediction_store.py` 的 `freeze_prediction` →
`_materially_changed` + `backend/app/core/config.py` 的 `PREDICTION_RESNAPSHOT_DELTA`
（2026-06-19 全量审查 P1-b 引入）。

问题：同一 Decision Gate verdict 内，概率 / adjusted_edge 漂移 ≥ 5pt 就 re-snapshot
一条新 prediction 行。这就是“轨迹”，而轨迹已经存在于 audit 快照层。Commitment
不应随漂移重新冻结。

双重错误：它是为了“修”P0-2 那个本不该存在的多行账本而加的（让同 verdict 的显著
edge 变化也能进 ledger），等于在错误地基上继续盖楼。

### P0-2：M3 多行 append-only 账本重构 — 违反原则 7 + 原则 2

位置：`backend/app/memory/prediction_store.py`——去掉 `event_id UNIQUE`、新增
`superseded` 状态、建表重建迁移（`_migrate` 的 rename → rebuild → COALESCE）。

问题：
- 原则 7 明说 M3 只做 KPI、不重构账本；
- 原则 2 说一事件一冻结 prediction；
- 这批属于被**显式 DEFERRED** 的工作被提前拉进来（项目自身记录即“append-only
  multi-row ledger 是 deliberately DEFERRED，inert 时无 KPI 收益”）。

当时用“避免 dormant 事件丢失 act 样本”论证它有 KPI 收益——对照原则，这是为
scope creep 找理由。在 Commitment 语义下，“首次冻结即承诺、不再 re-commit”本就是
设计语义，不是缺陷（见第五节“回退的代价”）。

---

## 三、P1 — Scope Creep / Dead Code

### P1-3：`get_predictions()` + `GET /events/{event_id}/predictions` — Dead Code

位置：`prediction_store.get_predictions`、`backend/app/api/routes/events.py` 的
ledger 路由。

无任何消费者：前端 `api.ts` 仅有 `openDecisions` / `freshEdges` /
`predictionCalibration`，从不调用此端点。它纯为“暴露多行账本”而加，随 P0-2
回退一并消失。

### P1-4：Diagnosis 解释字段冻结进 prediction 行 — Premature（原则 8）+ Future Schema Leakage

位置：`backend/app/models/event.py` + `prediction_store` schema 新增四列
`liquidity_factor` / `qualified` / `segment_n` / `segment_skill`（post-M5 审查 P3）。

问题：这是给 M5 报告面镀金，而循环正 dormant、`/decisions` 根本没有 act 项可解释。
把 M2 诊断内部量固化进 M1 Commitment 行，是 M2 细节向 M1 的 schema 渗透。解释
可在报告层按需展示，不必往 commitment 表加 4 列。

---

## 四、P2 — Premature Abstraction / 观察项

### P2-5：`PREDICTION_RESNAPSHOT_DELTA` 配置旋钮 — Premature Abstraction

为一个本不该存在的行为（re-snapshot）提供可调阈值。随 P0-1 回退消失。

### P2-6：四终态模型 `scored / observed / voided / superseded`

- `superseded` 仅因多行账本而存在 → 随 P0-2 回退消失。
- `observed`、`voided` 合规，应保留（见第六节）。

---

## 五、未违规 / 应保留（对照原则确认正确）

- **act-only scoring + `observed` 状态**：原则 4 + 路线图硬不变式“Only act rows
  are scored”。`observed` 让 watch resolved 但不进 headline calibration，原则 4
  需要它。✅
- **`void_prediction` + `voided`**：invalid 事件掉出机会面，是纯正确性，**在
  一行模型里同样可做**，不依赖多行。✅
- **PR-B `resolution_criteria`**：M0 身份 ground truth 范围内（原则 1）。✅
- **`segment_skill` = act + watch、仅按 category**：精确匹配原则 4 + 原则 6
  （未加 edge_bucket / evidence_profile 分段，克制正确）。✅
- **dormant 永不 act**：`diagnose.decide` 的 `qualified` 闸门正确（原则 5）。✅
- 改动均为增量、非 big-bang（原则 10）；唯一接近“重写”的是 P0-2 的建表迁移。

---

## 六、必须摊开的张力：路线图字面 vs 原则（需决策）

`V2_ROADMAP.md` 的**字面文本**与原则 3 / 7 冲突：
- Part 3 不变式：“Predictions are **append-only** and point-in-time frozen.”
- Temporal note：“Each pass appends new snapshots **and, if it clears the gate,
  a new prediction**. Probability and edge are **trajectories**.”

字面读，这是支持多行账本的。但原则主张 Prediction 是 Commitment、轨迹归 audit、
M3 只做 KPI。

架构判断：**原则更可信，且与项目自身“deferred ledger”的既定立场一致。**
路线图那句“append a new prediction”应理解为“出现一个真正的新承诺（如类目毕业后
的新 act 决策）”，而非把同一事件的概率漂移重新快照；“trajectories”指 audit
快照层（`analyze_edge_trajectory` 正是从那里读）。建议改清楚 Temporal note 的
措辞：**轨迹在 audit，预测是 commitment**，以消除导致本次跑偏的歧义。

---

## 七、回退的代价（诚实告知）

回退 P0-1 / P0-2 到一行 Commitment 模型后：**首次在 dormant 期被冻成 watch 的
事件，日后即使类目毕业也不会再变 act。** 但在 Commitment 语义下这是设计而非缺陷
——只承诺一次；类目毕业后**新到达**的事件会正确地冻成 act。把这个“丢失”当问题
来解，本身就是偏离原则的起点。

---

## 八、建议处理顺序（待确认，未改业务代码）

1. **P0-1 先回退** append-on-material-change（`_materially_changed` /
   `PREDICTION_RESNAPSHOT_DELTA`）——它建在 P0-2 之上。
2. **P0-2 回退**多行账本到一行 Commitment（恢复 `UNIQUE(event_id)` +
   `ON CONFLICT DO NOTHING`），保留 `score_prediction` 的 act-only 分流与
   `void_prediction`（两者不依赖多行）。
3. **P1-3** 删 dead 的 `get_predictions` + ledger 端点（随 2 自然消失）。
4. **P1-4** 决定 diagnosis 解释字段：从 prediction 行移除、改报告层按需算；
   或明确接受这点 M5 镀金。
5. 更新 `V2_ROADMAP.md` Temporal note + `prediction_store` / `DATABASE_DESIGN`
   文档，锁定“轨迹在 audit、预测是 commitment”。
6. 回退后重跑全套测试 + 一次 live smoke。

---

## 九、违规归类速查表

| 类别 | 条目 | 优先级 | 原则 |
|---|---|---|---|
| 与路线图冲突 | Append-on-material-change（P0-1） | P0 | 3 |
| Scope Creep / 与路线图冲突 | M3 多行 append-only 账本（P0-2） | P0 | 2, 7 |
| Dead Code | `get_predictions` + ledger 端点（P1-3） | P1 | 8, 9 |
| Future Schema Leakage / Premature | diagnosis 字段冻结进 prediction 行（P1-4） | P1 | 8 |
| Premature Abstraction | `PREDICTION_RESNAPSHOT_DELTA` 旋钮（P2-5） | P2 | 9 |
| 观察项 | 四终态模型（P2-6，`superseded` 随回退消失） | P2 | 2 |

---

## 十、最终结论

主路径可运行，测试与构建通过，act-only / dormant-gate / category-only / M0
身份等核心原则都成立。但本会话以“M3”之名引入的多行 append-only 账本与
append-on-material-change，违反原则 2 / 3 / 7，并把本应延后的工作提前实现。

处理建议：**回退到一行 Commitment 模型**，保留 act-only scoring 与
`void_prediction`，删除 dead ledger 端点，并就 diagnosis 解释字段与路线图 Temporal
note 的措辞做决策。回退不是 big-bang——它是把里程碑恢复到与原则一致的最小形态。

<!-- DATAFLOW-AUDIT -->

---

# 第二部分：数据闭环连通性审计

日期：2026-06-19
范围：Scheduler → Discover → Event → Market Link → Freeze Prediction →
Resolve Outcome → Calibration → Trust → Decision Report
方法：逐环节读实现代码，核对连通 / 落盘 / 可恢复 / fail-closed / 静默失败五项。

## 一、实际数据流图

```
[FastAPI lifespan] --start_scheduler()--> APScheduler (in-process, UTC)
      │
      ├── 07:00  morning_scan ......... LEGACY 市场层，写 agent_memory/analysis_audit
      │                                  （与事件闭环并行且独立，不进下游）
      │
      ├── 07:15  event_discover ───────────────────────────────────────┐
      │            (gated: EVENT_DISCOVER_ENABLED, 默认 true)            │
      │                                                                  ▼
      │   discover_events(use_cache=False)
      │     └─ _collect_candidate_events()  ← Polymarket/Manifold/Kalshi (公开API)
      │          每候选: {question, baseline_probability(市场价),
      │                    source:{type:'prediction_market', source_id(合约id)}}
      │     └─ process_event() → LLM 分析 → build_event_record()
      │          event_id = sha1(question)[:12]   ← 同问题跨天同 id
      │     └─ _persist_events(records):
      │          ├─ save_events()      → event_store.json   [落盘✓ locked+atomic]
      │          ├─ record_event()     → event_audit.jsonl  [落盘✓ append, 概率快照]
      │          └─ freeze_prediction()→ v2_loop.db         [落盘✓ SQLite]
      │               gate: source.type=='prediction_market' AND source_id AND
      │                     baseline/estimated 非空  → 否则 return None (news 事件)
      │               diagnose(raw_edge, segment_skill(category), liquidity)
      │                 → trust / adjusted_edge / decision(act|watch|skip)
      │               写 predictions 行 status='open'
      │
      ├── 22:00  evening_resolve ...... LEGACY 市场层 auto_resolve（独立）
      │
      └── 22:30  event_auto_resolve ───────────────────────────────────┐
                   auto_resolve_events()                                ▼
                     └─ fetch_resolved_markets() × 3源 (Polymarket/Manifold/Kalshi)
                     └─ build_index() + find_match(question 相似度)
                          ↑ 闭环关键连接点：靠 question 文本匹配，非 contract_id
                     └─ 每个未结算 event:
                          ├─ get_verified_link() → diverged 检查 (fail-closed)
                          ├─ upsert_link()        → event_market_links [落盘✓]
                          ├─ verified=score>=AUTO_VERIFY_THRESHOLD
                          │    ├─ diverged       → resolve(status='invalid') → void_prediction()
                          │    ├─ 未 verified    → pending（记录，不评分）[fail-closed✓]
                          │    └─ verified       → resolve_with_calibration()
                          │         ├─ score_event()  → event_store outcome+calibration [落盘✓]
                          │         ├─ record_outcome()→ event_audit.jsonl [落盘✓]
                          │         └─ score_prediction(): act→scored / watch,skip→observed
                          │              写 brier_score, status 终态  [落盘✓]
                          ▼
   [读取侧 / 按需，无定时驱动]
   calibration_summary()  → act-only Brier/realized_edge   ← GET /events/predictions/calibration
   segment_skill(cat)     → act+watch trust 输入            ← freeze 时下一轮读
   build_decision_report()→ DecisionReport                 ← GET /events/decisions/open, /{id}/decision
```

<!-- DATAFLOW-DETAIL -->

## 二、逐环节核对

### 1. Scheduler
- **连通**：是。`main.py` lifespan → `start_scheduler()`，APScheduler 注册 4 个 job。
- **落盘**：调度状态本身不落盘（in-process）。
- **可恢复**：**否（风险）**。任务仅存于进程内存；进程重启后当天已错过的触发不会补跑（`misfire_grace_time=300` 仅 5 分钟）。无持久化任务表。
- **fail-closed**：每个 job body 包 try/except，单 job 失败被隔离、不拖垮调度器。
- **静默失败**：**是（风险）**。若进程在 07:15 / 22:30 UTC 未运行，当天无 freeze / 无 score，**无任何告警**，日志也不会记录“本该跑但没跑”。

### 2. Discover
- **连通**：是。`event_discover` → `discover_events(use_cache=False)` → 三个公开市场 API + 开放网络。
- **落盘**：候选不单独落盘；下游 `_persist_events` 落盘。
- **可恢复**：是。下次触发重新拉取；无状态。
- **fail-closed**：是。`_collect_candidate_events` 对每源 `return_exceptions=True`，单源故障隔离。
- **静默失败**：部分。整个 discover 包 try/except 且 `logger.exception`，会记日志；但 LLM 分析失败的单个候选在 `process_event` 内被吞为 skip（仅 `logger.warning`）。

### 3. Event（记录构建 + 持久化）
- **连通**：是。`build_event_record` → `save_events` → `event_store.json`。
- **落盘**：是。`locked_file` + `write_json_atomic`（原子替换）。
- **可恢复**：是。`_load_for_write` 用 `read_json_strict`，损坏/IO 错误时**抛错而非用空 dict 覆盖**——这是强 fail-closed，防止整库被清空。
- **fail-closed**：是。`EventRecord.model_validate` 在写入前校验，坏记录抛错。
- **静默失败**：否。`_persist_events` 的 except 仅 `logger.warning("Event persistence failed")`——**注意**：若 save 抛错，freeze 也不会执行（同一 try 块），该轮静默丢失。见断点 B。

### 4. Market Link
- **连通**：是。`auto_resolve_events` 与 manual resolve 都调 `upsert_link`。
- **落盘**：是。`event_market_links` 表（v2_loop.db）。
- **可恢复**：是。SQLite 持久。
- **fail-closed**：是（M0 核心）。`get_verified_link` 是评分唯一闸门；`verified=score>=阈值 AND not diverged`；fuzzy match 进 pending 不评分；contract 分歧 → invalid。
- **静默失败**：否，但 **resolution_criteria 仅事件侧**（PR-B），市场侧 criteria 仍空——身份审计不完整（已知，非本次新增）。

### 5. Freeze Prediction
- **连通**：是。`_persist_events` → `freeze_prediction`。
- **落盘**：是。`predictions` 表。
- **可恢复**：是。SQLite 持久；幂等（同 verdict 无操作）。
- **fail-closed**：是。market-gated：非 prediction_market / 无 source_id / 无 baseline|estimated → `return None`，news 事件不冻结（无市场无 edge）。
- **静默失败**：**是（风险）**。freeze 在 `_persist_events` 的 try 块内，若它抛错只 `logger.warning`，该事件无预测但无显式告警；下游不会知道“这个事件本应有预测”。

<!-- DATAFLOW-DETAIL2 -->

### 6. Resolve Outcome
- **连通**：是。`event_auto_resolve` → `auto_resolve_events` → `resolve_with_calibration`。
- **落盘**：是。event_store outcome+calibration、event_audit outcome 快照、prediction 终态三处都落盘。
- **可恢复**：是。已结算事件下次被 `outcome is not None` 跳过；幂等。
- **fail-closed**：是。`status != 'resolved'`（invalid/void）→ 不进 calibration + `void_prediction`；diverged 身份冲突 → invalid。
- **静默失败**：**是（结构性风险）**。连接点是 **question 文本相似度匹配**（`find_match`），不是 contract_id。若 resolved market 的 question 文本与冻结时不一致、或该市场未出现在 `fetch_resolved_markets` 返回里，事件**永不被匹配 → 永不结算 → 永不评分**，且无任何告警。见断点 A。

### 7. Calibration
- **连通**：是（读取侧）。`calibration_summary` / `segment_skill`。
- **落盘**：是。读 predictions 表，无独立 calibration_metrics 表（M2 仍折叠在 predictions）。
- **可恢复**：是。纯查询，无状态。
- **fail-closed**：是。act-only 显式过滤；invalid/void/superseded 不进聚合。
- **静默失败**：否。无数据时返回 `no_data`，语义明确。

### 8. Trust
- **连通**：是。`freeze_prediction` 调 `segment_skill(category)` → `diagnose`。
- **落盘**：trust 值冻结进 prediction 行；segment_skill 实时查询不落盘。
- **可恢复**：是。
- **fail-closed**：是。dormant（n<8）→ 默认 trust 0.5 且 `qualified=False` → 永不 act（原则 5）。
- **静默失败**：否，但见“dormant 毕业风险”。

### 9. Decision Report
- **连通**：是（读取侧，按需）。`build_decision_report` ← `/events/decisions/open` 等。
- **落盘**：否（实时组装）。
- **可恢复**：是。纯函数。
- **fail-closed**：是。无 prediction → 404 / 最小报告。
- **静默失败**：否。

## 三、闭环断点与风险

### 断点 A（P0）：Resolve 靠 question 文本匹配，非 contract_id —— 静默不结算
冻结时已拿到稳定 `source_id`（合约 id）并写进 link，但 `auto_resolve_events` 仍用
`find_match(question, index)` 做**文本相似度**匹配 resolved market。后果：
- resolved market 的 question 与冻结时措辞不同 → 匹配不上 → 事件永不结算。
- 该市场不在当日 `fetch_resolved_markets` 窗口 → 同样永不结算。
- **完全静默**：没有“这个有 verified link 的事件早该结算了”的检测。
- 本可用已落盘的 `contract_id` 直接对账（link 里有，resolved market 里也有 id），
  却没用——身份 ground truth（M0）建好了但**结算侧没消费它**。

### 断点 B（P1）：freeze 与 save 同一 try 块 —— 一损俱损且静默
`_persist_events` 把 `save_events` / `record_event` / `freeze_prediction` 放在同一
try/except，任一抛错 → `logger.warning` 吞掉，该批后续步骤不执行。例如 save 成功但
freeze 抛错，事件入库却无预测，无显式信号。

### 数据丢失风险
1. **Scheduler 不持久**：进程在触发时点宕机 → 当天 freeze/score 永久缺失，无补跑、无告警。
2. **event_audit 压缩**：`EVENT_AUDIT_MAX_PER_EVENT` 截断旧概率快照——edge trajectory 只保最近 N 条，长历史丢失（设计取舍，但 M3 KPI 依赖它）。
3. **freeze 静默吞错**（断点 B）。

### 永远不会触发的逻辑
- **`diverged` 身份冲突分支**几乎不可达：它要求事件**已有**一条 verified link 且
  contract_id 与本次匹配的不同。但 verified link 几乎只在 auto_resolve 当场写入，
  正常路径下事件在结算前没有 verified link（freeze 不写 link），所以 diverged
  长期为 False。该 fail-closed 保护基本是死逻辑。
- **act 决策**：在 dormant 未毕业前，`qualified=False` → `decide` 永不返回 act
  （正确，但见下）。

### Dormant 状态无法毕业的风险（P0 级隐患）
毕业条件：某 category 累计 ≥ 8 条 **resolved 的 act+watch** 预测（`segment_skill.n>=8`）。
推演链条上的三重收窄：
1. **要先有 resolved 预测** → 依赖断点 A 的文本匹配成功结算。若匹配长期失败，
   `segment_skill.n` 永远是 0 → 永远 dormant → 永远不 act → realized_edge 永远 no_data。
2. **category 分散**：trust 按 category 累计（原则 6）。若 discover 的事件类目高度
   分散，单一 category 很难单独攒满 8 条，毕业被进一步拖慢。
3. **watch 供给**：dormant 期 trust=0.5、liquidity_factor 视流动性。watch 需
   `|adjusted_edge|=|raw×0.5×liq|>=3`。流动性充足时需 raw>=6pt；不足时门槛更高。
   多数小 edge 事件落入 skip，不计入毕业（segment_skill 排除 skip）。

**结论**：闭环在“机制”上连通且能产数据，但毕业**强依赖断点 A 的文本匹配真能成功**。
断点 A 一旦在某 category 上长期匹配失败，该 category 数学上**永远无法毕业**，且无告警。

## 四、数据闭环连通性速查表

| 环节 | 连通 | 落盘 | 可恢复 | fail-closed | 静默失败 |
|---|---|---|---|---|---|
| Scheduler | ✅ | ⚠️内存 | ❌不补跑 | ✅隔离 | ⚠️错过无告警 |
| Discover | ✅ | →下游 | ✅ | ✅源隔离 | ⚠️单候选吞错 |
| Event | ✅ | ✅原子 | ✅strict load | ✅校验 | ✅记日志 |
| Market Link | ✅ | ✅ | ✅ | ✅M0闸门 | ⚠️criteria仅事件侧 |
| Freeze | ✅ | ✅ | ✅幂等 | ✅market-gated | ⚠️同块吞错 |
| Resolve | ✅ | ✅×3 | ✅幂等 | ✅invalid不评分 | ❌文本匹配静默漏 |
| Calibration | ✅ | ✅折叠 | ✅ | ✅act-only | ✅no_data |
| Trust | ✅ | ✅冻结 | ✅ | ✅dormant封顶 | ✅ |
| Decision Report | ✅ | N/A | ✅ | ✅404 | ✅ |

## 五、修复优先级建议（数据闭环部分）

- **P0**：断点 A —— resolve 用已落盘的 `contract_id` 做主匹配，question 文本仅作
  兜底；并增加“有 verified link 但超期未结算”的检测，消除静默不毕业。
- **P0**：dormant 毕业可观测性 —— 暴露每 category 的 `segment_n` 进度（已冻结进
  prediction，缺一个汇总视图），让“离毕业还差几条”可见。
- **P1**：断点 B —— `_persist_events` 拆分 save 与 freeze 的错误处理，freeze 失败
  单独告警，不被 save 成功掩盖。
- **P1**：Scheduler 持久化 / 错过补跑 —— 至少在启动时检测“上次运行至今是否跨过
  触发时点”，或记录一条明确的 missed-run 日志。
- **P2**：`diverged` 死逻辑 —— 要么让 freeze 阶段就写 verified link（使身份冲突
  检查真正可达），要么明确它只在 manual 场景生效并文档化。

<!-- STORAGE-AUDIT -->

---

# 第三部分：存储设计审计

日期：2026-06-19
范围：v2_loop.db（predictions / event_market_links）+ JSON stores（event_store /
event_audit）。对照当前磁盘 schema，非凭记忆。

## 一、数据模型图（当前实际）

```
SQLite: v2_loop.db  (WAL, foreign_keys=ON, 进程级 _WRITE_LOCK)
├── predictions
│     id                 TEXT PRIMARY KEY        ← uuid4，每行唯一
│     event_id           TEXT NOT NULL           ← ⚠️ 无 UNIQUE（P0-2 已移除）
│     contract_id        TEXT
│     base_rate_category  TEXT                    ← segment key（category-only）
│     ai_probability / market_probability / raw_edge   [冻结，不可变]
│     trust / adjusted_edge / decision                 [冻结]
│     liquidity / volume / liquidity_factor /
│       qualified / segment_n / segment_skill          [冻结，P1-4 诊断解释]
│     created_at         TEXT
│     status             TEXT  open|scored|observed|voided|superseded
│     actual_outcome / brier_score / resolved_at       [resolve 时 UPDATE in place]
│     INDEX(status), INDEX(event_id)
│     约束：仅 PK(id)。一事件多行靠"应用逻辑"维持"至多一条 open"，DB 不强制。
│
└── event_market_links
      id                 TEXT PRIMARY KEY
      event_id / contract_id / market_question / resolution_criteria
      link_method / link_confidence / verified / linked_at
      UNIQUE(event_id, contract_id)   ← ✅ DB 强制
      INDEX(event_id), INDEX(contract_id)

JSON: event_store.json   { event_id: {first_seen,last_updated,record:{...,outcome,calibration}} }
      键唯一性 = dict key（event_id）→ 一事件一条 ✅；locked_file + atomic + strict-load
JSON: event_audit.jsonl  append-only 概率/outcome 快照；按 EVENT_AUDIT_MAX_PER_EVENT 压缩
```

关系：`event_store(event_id)` 1 — N `predictions(event_id)`；
`event_store(event_id)` 1 — N `event_market_links(event_id, contract_id)`。
**无外键**（predictions/links 不 FK 到 event_store，因后者是 JSON）。

## 二、逐项核对

### 1. 是否仍符合 One Event → One Prediction —— ❌ 否（DB 层），⚠️ 仅应用层维持
- DB 已无 `UNIQUE(event_id)`（P0-2 移除）。一事件可有多行（superseded + 终态 + open）。
- "至多一条 open" **不再由 DB 约束保证**，只由 `freeze_prediction` 的
  SELECT-open→比较→INSERT 逻辑维持。**不变式从 DB 约束降级为应用代码**——更弱、更易被
  未来改动破坏。

### 2. 是否存在隐式多版本 Prediction —— ⚠️ 是
- `superseded` 行就是显式的旧版本；append-on-material-change（P0-1）会在**同一 verdict**
  内因概率漂移 ≥5pt 再造一版。一个事件在结算前可能积累多条历史版本行。
- 这正是第一部分判定违反"Prediction 是 Commitment 不是 Trajectory"的存储层证据。

### 3. 是否有重复冻结风险 —— 单进程✅安全 / 多进程⚠️有
- `freeze_prediction` 的 SELECT-open + INSERT 都在 `with writing()`（持 `_WRITE_LOCK`）
  内，**单进程内串行**，不会并发产生两条 open。
- 但 `_WRITE_LOCK` 是**进程级**（threading.Lock）。若部署多个后端进程/worker 指向同一
  v2_loop.db，两进程可同时 SELECT 到无 open → 各 INSERT 一条 → **两条 open**，
  "至多一条 open"破裂。当前单进程假设下安全，横向扩展时是隐患。

### 4. 是否有重复 Resolve 风险 —— ✅ 双重幂等，但有孤儿 open 风险
- `score_prediction` / `void_prediction` 都 `WHERE status='open'`：首次 resolve 后该行
  转终态，二次 resolve 找不到 open → no-op，幂等。
- 事件层：`auto_resolve_events` 跳过 `outcome is not None` 的事件，幂等。
- **孤儿 open 风险**：若事件已结算（event_store 有 outcome）后，discover 又对同一
  event_id 触发 freeze 并 append 一条新 open，则 auto_resolve 因事件已 resolved 而
  **永久跳过** → 这条 open 永远不会被 resolve/score → 永久挂在机会面。append-on-change
  放大了这个风险（P0-1 + P0-2 共同副作用）。

### 5. 是否存在 Trust 统计污染 —— 当前✅未污染 / 结构上脆弱
- `segment_skill` = `status IN ('scored','observed') AND decision IN ('act','watch')`：
  superseded（NULL brier）与 voided 被排除，正确。
- 每事件至多一条终态行进入统计（结算后事件被跳过，不会再有第二条终态），所以**当前无
  双计**。但这个保护来自"事件层 outcome 跳过"，**不是 DB 约束**。若将来 resolve 逻辑改为
  给多条 open 评分，立即变成同一事件多行污染 trust。脆弱。

### 6. 是否有未来迁移障碍 —— ⚠️ 是（P0-2 制造的）
- 若按第一部分建议**回退到一行模型**（重加 `UNIQUE(event_id)`），现存多行数据
  （superseded 历史行）会**违反新约束**导致迁移失败。回退迁移必须先**折叠/删除非 open
  历史行**再加约束——这是 P0-2 反向制造的迁移债。
- `_migrate` 的检测式重建（rename→recreate→copy→drop）本身可复用，但**没有 schema 版本
  号**，靠"探测 UNIQUE 索引是否存在"判断，方向性迁移（加回约束）需要新的探测逻辑。
- P1-4 的 4 个诊断列是额外 schema 表面，回退或重构时需一并决定去留。

## 三、唯一约束建议

| 表 | 当前 | 建议 |
|---|---|---|
| predictions | 仅 PK(id) | 若维持多行：加**部分唯一索引** `CREATE UNIQUE INDEX ... ON predictions(event_id) WHERE status='open'`（SQLite 支持），把"至多一条 open"**下沉为 DB 约束**，消除应用层依赖与多进程竞态。若回退一行模型：恢复 `UNIQUE(event_id)`（需先折叠历史行）。 |
| event_market_links | UNIQUE(event_id,contract_id) | ✅ 合理，保留。 |
| event_store(JSON) | dict key | ✅ 天然唯一。 |

**首选**：即使保留多行，也应加 `UNIQUE(event_id) WHERE status='open'` 部分索引——它让
第 3 项（重复冻结）和第 4 项（孤儿 open）在 DB 层被强制，而不是靠注释和锁。

## 四、幂等性检查

| 操作 | 幂等 | 机制 | 风险 |
|---|---|---|---|
| freeze_prediction | ✅ 同 verdict+无显著变化 no-op | SELECT-open 比较 | 多进程下可双 INSERT |
| score_prediction | ✅ | WHERE status='open' | 无 |
| void_prediction | ✅ | WHERE status='open' | 无 |
| upsert_link | ✅ | ON CONFLICT(event_id,contract_id) DO UPDATE | 无 |
| save_events | ✅ upsert | dict key 覆盖，保 first_seen | 无 |
| resolve_event | ✅ | 覆盖 outcome（同值幂等） | 无 |
| record_event/outcome | ❌ append 每次新增一行 | 设计如此（快照流水） | 重复触发 = 重复快照（压缩兜底） |

## 五、Append-only 合规性检查

**结论：表不是真正的 append-only，文档措辞过度。**
- 决策字段（ai/market/raw_edge/trust/adjusted_edge/decision/诊断列）**冻结不可变** ✅。
- 但 resolve 时对**同一行** `UPDATE status, actual_outcome, brier_score, resolved_at`
  —— 这是**就地写入**，不是 append。`superseded` 也是对旧行 `UPDATE status`。
- 真实性质应表述为：**"决策字段 write-once 不可变；结局字段 write-once 就地填充；
  状态字段允许 open→终态的单向流转"**，而非"append-only"。
- 路线图 invariant "Predictions are append-only ... never recomputed" 在**决策字段**上
  成立（从不重算），但**字面"append-only"**与就地 UPDATE 实现不符——属第一部分指出的
  路线图措辞 vs 实现张力的存储层印证。

## 六、JSON Store / SQLite Store 风险评估

**SQLite（v2_loop.db）**
- 优点：WAL 并发读、事务 commit/rollback、约束可强制、short-lived 连接。
- 风险：①`_WRITE_LOCK` 进程级 → 多进程部署失去串行保证（重复冻结/孤儿 open）。
  ②无 schema 版本表，迁移靠结构探测，方向性回退缺机制。③`foreign_keys=ON` 但
  predictions/links 无实际 FK（指向 JSON 的 event_store，无法 FK）→ 引用完整性不被强制，
  可能存在指向已删除事件的孤儿预测。

**JSON（event_store.json / event_audit.jsonl）**
- 优点：event_store 用 locked_file + atomic write + strict-load（损坏不覆盖），相当稳。
- 风险：①event_store **全量读改写**——事件量增长后每次 resolve/save 重写整个文件，O(N)
  写放大，是未来扩展瓶颈。②跨 JSON 与 SQLite **无事务边界**：resolve 路径先写 SQLite
  （score_prediction）再写 JSON（或反之），中途崩溃会留下**两库不一致**（事件已 resolved
  但 prediction 仍 open，或反之）——无补偿/对账。③event_audit 压缩截断旧快照，M3 KPI
  依赖的长轨迹会丢。

**最大结构风险**：predictions(SQLite) 与 event_store(JSON) 是闭环两端却**无共享事务**。
resolve_with_calibration 跨两个存储多次写入，任一中途失败即不一致，且**无对账作业**检测。

## 七、存储审计修复优先级

- **P0**：加 `UNIQUE(event_id) WHERE status='open'` 部分索引（或回退一行模型），把
  "至多一条 open"从应用逻辑下沉为 DB 约束——同时消除重复冻结与孤儿 open。
- **P1**：跨 SQLite/JSON 的 resolve 一致性——加启动对账（event 已 resolved 但 prediction
  仍 open 的修复扫描），或把 outcome 也落进 SQLite 以获得单库事务。
- **P1**：回退/重构迁移需先折叠 superseded 历史行，否则重加 UNIQUE 失败（P0-2 迁移债）。
- **P2**：引入 schema 版本号表，替代"探测索引存在性"的隐式迁移判断。
- **P2**：修正 docstring/文档的"append-only"措辞为"决策不可变 + 结局就地填充"。

<!-- DELETION-AUDIT -->

---

# 第四部分：开源发布删除候选审计

日期：2026-06-19
标准：以“准备开源发布”为目标，找出可删除内容。结论基于真实调用关系核验（grep
全仓导入 + 路由注册 + 测试引用），非凭记忆。

## 背景判断

项目自身定位（见 V2 哲学 / 记忆）：**找市场错价（Edge）+ 对已结算现实保持校准的
反馈闭环，而非又一个 AI 新闻/交易平台。** 但仓库里并存两套系统：

- **V2 事件闭环（产品本体）**：`events` 路由 + `event_*` 服务 + prediction_store +
  event_market_link_store + diagnosis + decision_report + trend_analysis +
  calibration_service_event。数据走 event_store / event_audit / v2_loop.db。
- **Legacy 交易层（V0.3 前身）**：markets / scanner / analysis / trades / backtest /
  signal_accuracy / resolve 路由 + signal_* / trade_journal / 多 Agent 层 +
  reputation_engine + auto_resolve_service（市场层）。数据走 agent_memory /
  analysis_audit / market_cache。交易词汇，与闭环并行但不进下游。

删除候选按"是否需要产品决策"分三层。

## TIER 1 — 安全删除，零功能影响（最高 ROI，无需产品决策）

| 项 | 证据 | 收益 |
|---|---|---|
| `app/services/signal_service.py` | 全仓 0 调用、0 测试引用（仅 9 行存根） | 纯死代码，直接删 |
| 前端 `app-nav.tsx` 的 `/dashboard "经典"` 链接 | `frontend/src/app/dashboard` 不存在；build 路由表无 `/dashboard` | 死链，删 1 行，避免用户点击 404 |
| `get_predictions()` + `GET /events/{id}/predictions` | 前端 `api.ts` 无 wrapper，无消费者（见第一部分 P1-3） | 删 dead 端点（注：若回退多行账本则随之消失） |
| 仓库杂物：`.diff_temp.txt`、`.qoder/`、`backend/_full_out.txt`、`backend/event_store.json.bak`、`backend/archive/backend.7z` | 工作区产物 / 旧代码压缩包 | 不该进开源仓库；删除 + 补 `.gitignore` |

TIER 1 全部已用调用关系证实，删除不影响任何功能。

## TIER 2 — Legacy 子系统整体移除（需产品签字；复杂度大幅下降）

这是开源发布最大的一个决定：**是发布“干净的 V2 闭环”，还是连同 V0.3 交易助手一起发？**

| 子系统 | 文件 / 规模 | 可达性 | 风险 |
|---|---|---|---|
| 多 Agent 层 `app/agents/`（orchestrator + 9 agents） | 11 文件，~1409 行 | **仅** `scanner.py /api/scan/deep` 一处入口 | 过度抽象的 Service 群，与闭环无关 |
| `reputation_engine` + `judge_agent` | 2 文件 | 仅被 Agent 层内部用 | 随 Agent 层一起 |
| Legacy 交易路由 + 服务：`trades`+`trade_journal_service`、`signal_accuracy`+`signal_tracker`、`backtest`、`markets`、`scanner` | ~多文件，连同 Agent 层共 ~2693 行 | 各自路由注册，但**不进 V2 闭环下游** | 交易词汇，与“reality filter”定位冲突 |
| Legacy scheduler 作业 `morning_scan@07:00` / `evening_resolve@22:00` | scheduler.py 两个 job | 自动运行、消耗 LLM | 与 event_discover/event_auto_resolve 并行产生**第二套**校准数据，易混淆用户 |
| Legacy 存储：`agent_memory` / `analysis_audit` / `market_memory` / `auto_resolve_service`（市场层）/ `calibration_service`（市场层，非 _event） | 多文件 | 仅 legacy 层用 | 与 event_store 双轨 |

**架构师建议**：开源发布应只保留 V2 闭环，移除整个 Legacy 交易层 —— 它是 ~2700+ 行、
与产品定位（reality filter，非交易助手）冲突、且制造“两套校准口径”的认知负担。但这
**改变对外功能面**（删掉 /scan、/trades、/backtest 等端点），必须由你拍板，不能由审查
单方面执行。

**注意依赖**：删 Legacy 层前需确认前端 `/analyze` 页是否依赖 `analysis` 路由
（它走 ai_analysis_service / 市场层）。若 `/analyze` 要保留，需把它迁到 event 层的
`/events/analyze`，否则会断。这是删除前的唯一耦合点，需先解。

## TIER 3 — 配置 / 死分支 / 过度设计（局部清理）

| 项 | 性质 | 建议 |
|---|---|---|
| `cross_validation_service` | opt-in，默认关闭（`CROSS_VALIDATION_MODEL` 未设即整模块 no-op） | 若从未启用：删服务 + 3 个 `CROSS_VALIDATION_*` 配置项；若保留：文档标注为可选 |
| `diverged` 身份冲突分支（event_resolve_service） | 永不触发的死逻辑（见第二部分） | 让 freeze 阶段写 verified link 使其可达，或删除并文档化“仅 manual” |
| `PREDICTION_RESNAPSHOT_DELTA` + `_materially_changed` | 过度设计（见第一部分 P0-1） | 随多行账本回退一并删除 |
| prediction 表 4 个诊断冻结列（liquidity_factor/qualified/segment_n/segment_skill） | 提前实现的 M5 镀金（第一部分 P1-4） | 移到报告层按需算，或接受 |
| 无意义配置项排查 | `POLYMARKET_CRYPTO_FETCH_ENABLED`、`EMBEDDING_*`、`CROSS_VALIDATION_*` 等若发布时不用 | 逐项确认默认值与文档；未用的从 `.env.example` 删 |

## 收益排序（删除后影响）

**按“不影响功能”排序（先删这些）**
1. 仓库杂物（cruft）—— 0 风险，立即删。
2. `signal_service.py`（9 行死码）、前端 `/dashboard` 死链 —— 0 风险。
3. dead `get_predictions` 端点 —— 0 消费者。

**按“降低复杂度”排序（收益最大）**
1. 多 Agent 层 `app/agents/` + reputation_engine（~1400 行，单入口）—— 删除即去掉最大的
   过度抽象块。
2. 整个 Legacy 交易层（~2700 行）—— 去掉第二套词汇/存储/校准，使代码库与产品定位一致。
3. 回退多行账本 + 删 `PREDICTION_RESNAPSHOT_DELTA`/诊断列（第一/三部分 P0/P1）。

**按“减少维护成本”排序**
1. Legacy scheduler 双作业 —— 停掉后不再产生第二套数据，运营心智减半。
2. `cross_validation` / 未用配置项 —— 减少“这是干嘛的”类疑问。
3. 双存储（agent_memory/analysis_audit vs event_store）—— 删 Legacy 后只剩一套。

## 删除前置依赖（执行顺序）

1. 先删 TIER 1（无依赖，无风险）。
2. TIER 2 前：解决 `/analyze` 前端页对 legacy `analysis` 路由的耦合（迁移或保留决策）。
3. TIER 2：移除 Agent 层 → 交易路由/服务 → legacy scheduler 作业 → legacy 存储，按
   依赖自底向上。
4. TIER 3：随对应架构回退一并处理。

本部分为审查记录，**未删除任何代码**。TIER 1 可直接执行；TIER 2 需你确认“开源范围 =
仅 V2 闭环”后执行。

---

# 第五部分：Phase 1 数据闭环修复（已执行，2026-06-19）

采纳四份 AI 审计 + Codex 汇总的共识：**先修真实数据闭环可靠性，再做语义决策与删除。**
本部分记录已落地的 Phase 1（数据闭环），均带回归测试，514 测试通过、无 v2_loop.db 泄漏。

### P0-A（已修）：save_events 重扫覆盖 resolved outcome/calibration
- 根因：`save_events` 仅保留 `tracking`；已 resolved 事件被同 event_id 重扫时，新
  record 不带 outcome/calibration → 整体覆盖 → 退回 unresolved，校准样本静默丢失。
- 修复：`save_events` 合并时，若 incoming record 缺 `outcome`/`calibration` 而库中存在，
  则继承之（与 tracking 同等对待）。
- 回归测试：`test_rescan_does_not_revert_resolved_outcome`。

### P1-A（已修）：_persist_events 单 try 块吞掉 save/audit/freeze 边界
- 修复：拆成三段错误边界——save 为门（失败即 abort 并 `logger.error`）；audit 失败按
  事件隔离、不阻断 freeze；freeze 失败按事件隔离、`logger.warning` 带 event_id+reason。
- 回归测试：`test_freeze_failure_does_not_lose_saved_event`。

### P0-B（已修）：auto_resolve 文本匹配为主 → 改 contract_id 主路径
- 修复：新增 `market_by_contract` 索引。已绑定 verified link 的事件，一旦其 `contract_id`
  出现在 resolved 集合即**直接按 id 结算**（PRIMARY），不依赖问题文本一致；文本匹配仅作
  **未绑定**事件的兜底。已绑定但其合约未结算的事件**等待**，不回退文本匹配。
- 连带：`diverged`→invalid 分支在 contract-first 下对 auto 路径**不可达**，已删除该死分支。
  M0 身份保证不减反增——绑定事件**永不**被记到不同合约，且“等待本合约结算”比
  “标记 invalid”更可恢复。
- 回归测试：`test_linked_event_settles_by_contract_id`、
  `test_linked_event_not_scored_against_different_contract`。

### Phase 1 未覆盖、仍待办（按汇总路线）
- **Phase 2（需产品决策）**：Prediction = 严格 Commitment（回退多行账本）还是正式
  Ledger（加 `UNIQUE(event_id) WHERE status='open'` + 版本模型）。当前“半 ledger 半
  commitment”仍是危险中间态——下一步最该拍板。
- **跨 SQLite/JSON 一致性**：resolve 跨两库写仍无共享事务 / 无启动对账。
- **Scheduler 可观测性**：missed-run 检测 / 上次成功时间 / 手动补跑入口仍缺。
- 清理项（dead endpoint / premature config / dead link / TIER 1-2 删除）待 Phase 2 之后。











