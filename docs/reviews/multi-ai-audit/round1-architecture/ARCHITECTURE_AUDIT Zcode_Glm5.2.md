# 第二部分：架构审计报告（ZCode · GLM-5.2）

日期：2026-06-19
审查者视角：资深架构师
范围：相对 `HEAD`（commit `e5ff910`）的全量未提交变更，重点是 V2 反馈闭环新增/修改的代码：
`prediction_store.py`、`event_market_link_store.py`、`diagnosis_service.py`、`decision_report_service.py`、
`event_resolve_service.py`、`trend_analysis_service.py`、`event_intelligence_service._persist_events`、
`routes/events.py`、`models/event.py`、`core/config.py`、`core/scheduler.py`，以及前端 `lib/api.ts`。

方法：逐条对照下方 10 条路线图/设计原则审查，**只评架构原则，不评代码风格**。
所有结论均由实际读取代码 + grep 调用关系核验得出（非凭记忆/推断）。

---

## 一、审查的 10 条原则

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

## 二、总体判定

闭环的「机制层」基本健全且符合原则：M0 身份闸门、act-only 校准、dormant 永不 act、
category-only 分段都成立，改动整体是增量式的（原则 10 基本守住）。

**但本批变更的核心违规集中在一处：以「M3」之名引入了多行 append-only 预测账本。**
这条线同时触碰原则 2（一事件一冻结）、原则 3（Prediction 是 Commitment 不是 Trajectory）、
原则 7（M3 只做 KPI 不重构账本）、原则 9（不提前实现）。它是本次所有 P0/P1 的根源。

关键事实（已核验）：
- **轨迹层已经存在且合规**。`trend_analysis_service.analyze_edge_trajectory` /
  `rank_fresh_edges`（`trend_analysis_service.py:179`、`:289`）从 `event_audit.jsonl`
  的概率快照读取 edge 轨迹，并已暴露 `/events/edges/fresh`、`/events/{id}/history`。
  即「fresh/decaying/stale edge」这个 M3 概念已经实现在它该在的层（audit 快照）。
- 把「轨迹」再塞进 `predictions` 表（多行 + `superseded` + append-on-material-change），
  是**把同一概念实现两遍**，并且塞进了本应是 Commitment 的表。

---

## 三、违规分类速查表

| 类别 | 条目 | 优先级 | 触犯原则 | 位置 |
|---|---|---|---|---|
| 与路线图冲突 | Append-on-material-change（`_materially_changed`） | **P0** | 3 | `prediction_store.py:165,260` + `config.PREDICTION_RESNAPSHOT_DELTA` |
| Scope Creep / 与路线图冲突 | M3 多行 append-only 账本重构（移除 `UNIQUE(event_id)` + `superseded`） | **P0** | 2, 7, 9 | `prediction_store.py:38-132` |
| Dead Code | `get_predictions()` + `GET /events/{id}/predictions` 端点 | **P1** | 8, 9 | `prediction_store.py:378`、`events.py:316` |
| Future Schema Leakage / Premature | Diagnosis 解释字段冻结进 prediction 行（4 列） | **P1** | 8 | `prediction_store.py:52-56`、`models/event.py:211-217` |
| Premature Abstraction | `PREDICTION_RESNAPSHOT_DELTA` 配置旋钮 | **P2** | 9 | `config.py:206` |
| 观察项 | 四终态模型 `scored/observed/voided/superseded` | **P2** | 2 | `prediction_store.py` 全文 |
| 静默失败（数据闭环隐患） | freeze 与 save 同一 try 块吞错 | **P1** | — | `event_intelligence_service.py:402-415` |
| 静默失败（数据闭环隐患） | resolve 靠 question 文本匹配，未消费已落盘的 `contract_id` | **P0** | 1（M0 ground truth 建好但下游未消费） | `event_resolve_service.py:230` |
| Dead Code（前端） | `/dashboard "经典"` 死链 | **P2** | — | `app-nav.tsx:59` |
| 死代码（Legacy） | `signal_service.py` 9 行存根 0 调用 | **P2** | — | `backend/app/services/signal_service.py` |

---

## 四、P0 — 违反核心原则，建议回退

### P0-1：M3 多行 append-only 账本重构 —— 违反原则 2 / 7 / 9

位置：`backend/app/memory/prediction_store.py:38-132`（`_SCHEMA` 删去 `UNIQUE(event_id)`、
新增 `superseded` 状态、`_migrate` 的 rename→rebuild→copy 重建）。

证据：
- `V2_ROADMAP.md` Milestone 1「Known simplifications」**明确写**："`predictions` is a
  **one-row-per-event ledger (UNIQUE event_id)** … The append-only multi-row history is
  **M3, not now**." 且 Milestone 3 的标题是「Temporal dimension」，产物是「edge trajectory +
  fresh/decaying」——**不是**账本重构。
- 项目自身的设计注释（`DATABASE_DESIGN.md:222`「Two metric scopes」与 ROADMAP Part 3 不变式）
  把「only act rows are scored」作为硬不变式；**一行模型足以承载它**，不需要多行账本。
- 此批改动恰恰把那条「M3, not now」的简化推翻了，等于把**显式 DEFERRED** 的工作提前拉进来。

为什么这是架构问题而非细节：
- 一行模型下，「至多一条 open」是 **DB 约束**（`UNIQUE(event_id)`）。
- 多行模型把它**降级为应用代码**（`freeze_prediction` 的 SELECT-open→比较→INSERT 逻辑）。
  不变式从「DB 强制」变成「靠注释和锁维持」，更易被未来改动破坏，且单进程锁
  （`_WRITE_LOCK` 是 `threading.Lock`）在多进程部署下失效，可能产生两条 open。

结论：这是把「dormant 事件丢失 act 样本」当成缺陷来解，但**在 Commitment 语义下「首次冻结即承诺、
不再 re-commit」本就是设计语义**，不是缺陷。用多行账本去「修」它，是在偏离原则的地基上盖楼。

### P0-2：Append-on-material-change —— 违反原则 3

位置：`prediction_store.py:165`（`_materially_changed`）、`:260-271`（verdict 不变但 edge/概率
漂移 ≥ `PREDICTION_RESNAPSHOT_DELTA` 就 re-snapshot），`config.py:206`
（`PREDICTION_RESNAPSHOT_DELTA=5.0`）。

问题：**同一 Decision Gate verdict 内**，概率或 adjusted_edge 漂移 ≥5pt 就再造一条新 prediction 行。
这就是「轨迹」——而轨迹已存在于 audit 快照层（见第二节）。把轨迹塞进 Commitment 表，
让「Prediction 是 Commitment，不是 Trajectory」这一原则名存实亡。

双重错误：它是为「修」P0-1 那个本不该存在的多行账本而加的（让同 verdict 的显著 edge 变化
也能进 ledger），等于在错误地基上继续盖楼。随 P0-1 回退一并消失。

### P0-3（数据闭环）：resolve 用 question 文本匹配，未消费已落盘的 `contract_id` —— 违反原则 1 的下游兑现

位置：`backend/app/services/event_resolve_service.py:230`（`find_match(question, index)`）。

证据：
- 冻结时已拿到稳定合约 id 并写进 link（`event_intelligence_service` 把 `source_id` 当作 contract_id）；
  本批改动也让 Kalshi/Manifold 的 `fetch_resolved_markets` 返回与冻结同源的 `id`
  （`kalshi_event_source.py:171`、`manifold_event_source.py:144`）——**M0 identity ground truth 已贯通到三源**。
- 但 `auto_resolve_events` 仍用 `find_match(question, index)` 做**文本相似度**匹配
  resolved market，`contract_id` 只用于「diverged」身份冲突检测（`event_resolve_service.py:243-248`）。

后果（这是会让整个闭环静默不工作的隐患）：
- resolved market 的 question 措辞与冻结时不同 → 匹配不上 → 事件**永不结算** → 永不评分。
- 该市场不在当日 `fetch_resolved_markets` 返回窗口 → 同样永不结算。
- **完全静默**：没有「这个有 verified link 的事件早该结算了」的检测。
- 而 dormant 毕业又强依赖 resolved 样本：某 category 长期匹配失败 → `segment_skill.n` 恒为 0
  → 该 category **数学上永远无法毕业** → realized_edge 永远 `no_data`。无告警。

判断：M0 的 ground truth「建好了」，但**结算侧没消费它**。这是原则 1 的下游兑现缺口，
比 P0-1/P0-2 更直接影响闭环能否真正产出数据。建议优先级与 P0-1/P0-2 并列。

---

## 五、P1 — Scope Creep / Dead Code / Future Schema Leakage

### P1-1：`get_predictions()` + `GET /events/{event_id}/predictions` —— Dead Code

位置：`prediction_store.get_predictions`（`prediction_store.py:378`）、
`routes/events.py:316`（ledger 端点）。

核验（grep 全仓 + 前端）：
- 前端 `frontend/src/lib/api.ts` 对 V2 prediction 仅有三个 wrapper：
  `openDecisions`、`freshEdges`、`predictionCalibration`（`api.ts:160-171`）。
- **没有任何前端 wrapper 调用 `/events/{id}/predictions`**；后端除该端点自身外也无消费者。
- 它纯为「暴露多行账本」而加，随 P0-1 回退一并消失。

### P1-2：Diagnosis 解释字段冻结进 prediction 行 —— Future Schema Leakage + 原则 8

位置：`prediction_store.py:52-56`（`liquidity_factor / qualified / segment_n / segment_skill`
四列）、`models/event.py:211-217`。

问题：这 4 个量是 **M2 诊断的内部中间量**，把它冻结进 M1 Commitment 行，是 M2 细节向 M1
schema 的渗透。其唯一消费者是 `decision_report_service._diagnosis_reason`
（`decision_report_service.py:17`），用于 M5 报告面解释「为什么不是 act」——
而当前循环 dormant、`/decisions/open` 根本没有 act 项可解释，属于提前给 M5 镀金。

建议：解释可在报告层按需算（diagnose 本是纯函数，读时重算即可），不必往 commitment 表加 4 列。

### P1-3：freeze 与 save 同一 try 块 —— 静默吞错

位置：`event_intelligence_service.py:402-415`（`_persist_events`）。

`save_events` / `record_event` / `freeze_prediction` 在同一 try/except，任一抛错 →
`logger.warning` 吞掉，该批后续步骤不执行且无显式信号。例如 save 成功但 freeze 抛错，
事件入库却无预测，下游无从知晓「这个事件本应有预测」。建议拆分 save 与 freeze 的错误处理，
freeze 失败单独告警。

---

## 六、P2 — Premature Abstraction / 观察项

### P2-1：`PREDICTION_RESNAPSHOT_DELTA` 配置旋钮 —— Premature Abstraction（原则 9）

为一个本不该存在的行为（re-snapshot）提供可调阈值（`config.py:206`）。随 P0-2 回退消失。

### P2-2：四终态模型 `scored / observed / voided / superseded`

- `superseded` 仅因多行账本而存在 → 随 P0-1 回退消失。
- `observed`、`voided` **合规应保留**：
  - `observed`（watch/skip resolved）支撑原则 4「Trust 仅用 act+watch」——headline 校准
    act-only（`calibration_summary` 显式 `decision='act'`，`prediction_store.py:480`），
    而 `segment_skill` 用 act+watch 排除 skip（`:449`），两套口径精确分离，正确。
  - `void_prediction` + `voided` 是纯正确性（invalid 事件掉出机会面），**不依赖多行模型**，
    在一行 Commitment 下同样可做。

### P2-3：前端 `/dashboard "经典"` 死链

`frontend/src/components/app-nav.tsx:59` 指向 `/dashboard`，但 `frontend/src/app/dashboard`
目录不存在（已核验：`dir frontend\src\app\dashboard` → NO dashboard dir）。死链，建议删 1 行。

### P2-4：`signal_service.py` 9 行存根 0 调用

`backend/app/services/signal_service.py` 全仓 0 调用、0 测试引用，纯死代码（属 Legacy 层，
非本次新增，但既然在审查范围附近的清理项里，一并记录）。可直接删。

### P2-5：`signal_tracker.py` 路径修复（合规，仅记录）

`signal_tracker.py` 本批 diff 仅把 `_audit_path` 从 `..\\..\\..` 改为 `..\\..`
（修正一个多一层、指向不存在目录的旧路径）。属 Legacy 市场层，**非 V2 闭环违规**，记录备查。

---

## 七、未违规 / 应保留（逐条对照原则确认）

- **原则 1（M0 身份）**：`event_market_link_store` 的 `verified` + `link_confidence` +
  `get_verified_link` fail-closed 闸门 + `resolution_criteria` 列，全部在 M0 范围内。✅
  （唯一缺口是 P0-3：ground truth 建好但 resolve 侧未消费 `contract_id`。）
- **原则 2（一事件一冻结）**：DB 层 **已违反**（见 P0-1）；仅靠应用代码维持「至多一条 open」。⚠️
- **原则 3（Commitment 非 Trajectory）**：**已违反**（见 P0-2）。⚠️
- **原则 4（Trust 仅用 act+watch）**：`segment_skill` = `status IN ('scored','observed')
  AND decision IN ('act','watch')`（`prediction_store.py:449`），排除 skip 与 superseded，
  精确匹配。`calibration_summary` 严格 act-only。两套口径分离正确。✅
- **原则 5（dormant 永不 act）**：`diagnosis_service.decide` 中 `qualified and magnitude>=act_edge`
  才返回 `act`（`diagnosis_service.py:66`），`qualified = segment_n >= min_samples`，
  未毕业恒为 watch。✅
- **原则 6（Segment = Category Only）**：segment 键 = `base_rate_category`，未引入
  `edge_bucket` / `evidence_profile` 分段。克制正确。✅
- **原则 7（M3 只做 KPI）**：**已违反**（见 P0-1）。⚠️
- **原则 8（不提前实现 M4/M5）**：Decision Report / fresh-edges 表面是 M5 范畴，但它们
  是「读取侧、按需组装、纯函数」，不污染 commitment 语义，可接受；唯一越界是 P1-2 的
  diagnosis 4 列。⚠️（局部）
- **原则 9（Build Only What Next Milestone Requires）**：**已违反**（见 P0-1/P0-2）。⚠️
- **原则 10（No Big Bang Rewrite）**：改动整体增量式；唯一接近「重写」的是 P0-1 的建表迁移
  （rename→rebuild→copy）。基本守住。✅（除 P0-1 外）

---

## 八、必须摊开的张力：路线图字面 vs 原则（需你决策）

`V2_ROADMAP.md` 的**字面文本**与原则 3 / 7 存在表面冲突：
- Part 3 不变式："Predictions are **append-only** and point-in-time frozen."
- Temporal note："Each pass appends new snapshots **and, if it clears the gate, a new
  prediction**. Probability and edge are **trajectories**."

字面读，这是支持多行账本的。**但路线图同一段的「Known simplifications」又明说一行 ledger、
多行是 M3——两处文本本身就在打架。** 架构判断：原则更可信，且与项目「multi-row ledger is
deliberately DEFERRED」的既定立场一致。

建议改清楚 `V2_ROADMAP.md` 的措辞，消除导致本次跑偏的歧义：**轨迹在 audit 快照层，
predictions 是一次性 Commitment**。否则下一轮还会有人依据「字面」再次引入多行账本。

---

## 九、回退的代价（诚实告知）

回退 P0-1/P0-2 到一行 Commitment 模型后：**首次在 dormant 期被冻成 watch 的事件，
日后即使类目毕业也不会再变 act。** 但在 Commitment 语义下这是设计而非缺陷——只承诺一次；
类目毕业后**新到达**的事件会正确地冻成 act。把这种「丢失」当问题来解，本身就是偏离原则的起点。

回退还带一个迁移债：现存 `superseded` 历史行会违反重新加回的 `UNIQUE(event_id)`，
迁移必须先折叠/删除非 open 历史行再加约束。

---

## 十、建议处理顺序（待确认，本次未改任何业务代码）

1. **P0-3 优先**：resolve 改用已落盘的 `contract_id` 做主匹配，question 文本仅作兜底；
   并加「有 verified link 但超期未结算」的检测。这是让闭环**真正能产出数据**的前提。
2. **P0-1 回退**：恢复 `UNIQUE(event_id)` + `ON CONFLICT DO NOTHING`（先折叠历史行）。
3. **P0-2 回退**：删 `_materially_changed` + `PREDICTION_RESNAPSHOT_DELTA`。
4. **P1-1**：删 dead 的 `get_predictions` + `/events/{id}/predictions` 端点（随 2 自然消失）。
5. **P1-2**：decision diagnosis 4 列移到报告层按需算，或明确接受这点 M5 镀金。
6. **P1-3**：`_persist_events` 拆分 save 与 freeze 的错误处理。
7. 更新 `V2_ROADMAP.md` Temporal note + `prediction_store` / `DATABASE_DESIGN` 文档，
   锁定「轨迹在 audit、预测是 commitment」。
8. P2 清理：删 `/dashboard` 死链、删 `signal_service.py` 存根。
9. 回退后重跑全套测试 + 一次 live smoke。

回退不是 big-bang——它是把里程碑恢复到与原则一致的最小形态。

---

## 十一、最终结论

主路径可运行，测试与构建通过，M0 身份、act-only、dormant-gate、category-only 等核心原则都成立。
但有**三处必须处理**：

1. **P0-1/P0-2**：以「M3」之名引入的多行 append-only 账本与 append-on-material-change，
   违反原则 2 / 3 / 7 / 9，把本应 DEFERRED 的工作提前实现，并制造了重复的轨迹概念。
   → **回退到一行 Commitment 模型**，保留 act-only scoring 与 `void_prediction`。
2. **P0-3**：resolve 侧未消费已贯通的 `contract_id`，靠文本匹配结算，存在静默不结算、
   进而导致 dormant 永不毕业的隐患。→ **用 contract_id 主匹配 + 超期未结算检测**。
3. **P1-1/P1-2**：dead 的 ledger 端点与渗透进 commitment 表的 diagnosis 4 列，
   随架构回退一并清理或迁移到报告层。

处理建议已列于第十节。本报告为审查记录，**未修改任何业务代码**。

---

# 第二部分：数据闭环连通性审计

日期：2026-06-19
范围：Scheduler → Discover → Event → Market Link → Freeze Prediction → Resolve Outcome →
Calibration → Trust → Decision Report，逐环节核对五项：**连通 / 落盘 / 可恢复 / fail-closed / 静默失败**。
方法：逐环节读实现代码（含 `event_store`、`event_audit_service`、`sqlite_db`、`text_match`）核验，
非凭记忆。

## 一、实际数据流图（按代码画，非按文档）

```
[FastAPI lifespan] --start_scheduler()--> APScheduler (in-process, UTC)
      │   job_defaults: coalesce=True, misfire_grace_time=300s
      │
      ├── 07:00  morning_scan ............ LEGACY 市场层，写 agent_memory/analysis_audit
      │                                   （与事件闭环并行且独立，不进下游）
      │
      ├── 07:15  event_discover ─────────────────────────────────────┐
      │           (gated: EVENT_DISCOVER_ENABLED, 默认 true)            ▼
      │     discover_events(use_cache=False)
      │       └─ _collect_candidate_events()  ← Polymarket/Manifold/Kalshi (公开API)
      │            每候选: {question, baseline_probability(市场价),
      │                      source:{type:'prediction_market', source_id(合约id)}}
      │       └─ process_event() → LLM 分析 → build_event_record()
      │            event_id = sha1(question)[:12]   ← 同问题跨天同 id
      │       └─ _persist_events(fresh):            ← ⚠️ 整块在同一 try/except (断点 B)
      │            ├─ save_events()      → event_store.json    [落盘✓ locked+atomic+strict-load]
      │            ├─ record_event()     → event_audit.jsonl   [落盘✓ append, 概率快照]
      │            └─ freeze_prediction()→ v2_loop.db          [落盘✓ SQLite]
      │                 gate: source.type=='prediction_market' AND source_id AND
      │                       baseline/estimated 非空  → 否则 return None (news 事件)
      │                 diagnose(raw_edge, segment_skill(category), liquidity)
      │                   → trust / adjusted_edge / decision(act|watch|skip)
      │                 写 predictions 行 status='open'
      │
      ├── 22:00  evening_resolve ........ LEGACY 市场层 auto_resolve（独立，不进事件闭环）
      │
      └── 22:30  event_auto_resolve ────────────────────────────────┐
                   auto_resolve_events()                              ▼
                     └─ fetch_resolved_markets() × 3源 (return_exceptions 隔离)
                     └─ build_index(resolved_markets)   ← ⚠️ 仅按 normalized(question) 索引
                     └─ market_by_key = {normalize(question): market}  ← 含 id，但未被 match 使用
                     └─ 每个未结算 event (list_all_events，全量):
                          ├─ find_match(question, index)  ← ⚠️ 闭环连接点：question 文本匹配
                          │     命中: exact normalized key → score 1.0；
                          │           否则 Jaccard token overlap >= 0.82；否则 None
                          │     （从不读 contract_id 来匹配）
                          ├─ contract_id = market_by_key[...].get('id')  ← 只在 match 之后取，仅用于 diverged 检查
                          ├─ upsert_link()        → event_market_links [落盘✓]
                          ├─ verified = score >= AUTO_VERIFY_THRESHOLD(默认 1.0)
                          │    ├─ diverged        → resolve(status='invalid') → void_prediction()
                          │    ├─ 未 verified      → pending（记录，不评分）[fail-closed✓]
                          │    └─ verified         → resolve_with_calibration()
                          │         ├─ score_event()   → event_store outcome+calibration [落盘✓]
                          │         ├─ record_outcome()→ event_audit.jsonl            [落盘✓]
                          │         └─ score_prediction(): act→scored / watch,skip→observed
                          │              UPDATE status, actual_outcome, brier_score [落盘✓]
                          ▼
   [读取侧 / 按需，无定时驱动]
   calibration_summary()  → act-only Brier/realized_edge   ← GET /events/predictions/calibration
   segment_skill(cat)     → act+watch trust 输入            ← freeze 时下一轮读
   build_decision_report()→ DecisionReport                 ← GET /events/decisions/open, /{id}/decision
   analyze_edge_trajectory()/rank_fresh_edges → 从 event_audit.jsonl 读 ← GET /events/edges/fresh
```

## 二、逐环节核对（五项）

### 1. Scheduler
- **连通**：✅ `main.py` lifespan → `start_scheduler()`，APScheduler 注册 4 个 job（2 个 V2 + 2 个 Legacy）。
- **落盘**：⚠️ 调度状态本身**不落盘**（in-process）。
- **可恢复**：❌ 任务仅存于进程内存；进程重启后当天已错过的触发**不会补跑**
  （`misfire_grace_time=300` 仅 5 分钟窗口）。无持久化任务表。
- **fail-closed**：✅ 每个 job body 包 try/except，单 job 失败被隔离、不拖垮调度器。
- **静默失败**：⚠️ **是（风险）**。若进程在 07:15 / 22:30 UTC 未运行，当天无 freeze / 无 score，
  **无任何告警**，日志也不会记录「本该跑但没跑」。

### 2. Discover
- **连通**：✅ `event_discover` → `discover_events(use_cache=False)` → 三个公开市场 API + 开放网络。
- **落盘**：✅ 候选不单独落盘，下游 `_persist_events` 落盘。
- **可恢复**：✅ 下次触发重新拉取；无状态。
- **fail-closed**：✅ `_collect_candidate_events` 对每源 `return_exceptions=True`，单源故障隔离。
- **静默失败**：⚠️ 部分。整个 discover 包 try/except 且 `logger.exception`，会记日志；但 LLM 分析失败的单个候选在 `process_event` 内被吞为 skip（仅 `logger.warning`）。

### 3. Event（记录构建 + 持久化）
- **连通**：✅ `build_event_record` → `save_events` → `event_store.json`。
- **落盘**：✅ `locked_file` + `write_json_atomic`（原子替换）。
- **可恢复**：✅ `_load_for_write` 用 `read_json_strict`，损坏/IO 错误时**抛错而非用空 dict 覆盖**
  ——强 fail-closed，防止整库被清空。
- **fail-closed**：✅ `EventRecord.model_validate` 在写入前校验，坏记录抛错。
- **静默失败**：✅ 记日志；但见断点 B（与 freeze 同块，save 抛错则 freeze 不执行）。

### 4. Market Link
- **连通**：✅ `auto_resolve_events` 与 manual resolve 都调 `upsert_link`。
- **落盘**：✅ `event_market_links` 表（v2_loop.db）。
- **可恢复**：✅ SQLite 持久。
- **fail-closed**：✅（M0 核心）`get_verified_link` 是评分唯一闸门；`verified = score>=阈值 AND not diverged`；
  fuzzy 进 pending 不评分；contract 分歧 → invalid。
- **静默失败**：⚠️ **resolution_criteria 仅事件侧**（从 `record.semantics` 取），市场侧 criteria 仍空
  （`fetch_resolved_markets` 不返回市场自身 criteria）——身份审计不完整（已知，非本次新增）。

### 5. Freeze Prediction
- **连通**：✅ `_persist_events` → `freeze_prediction`。
- **落盘**：✅ `predictions` 表。
- **可恢复**：✅ SQLite 持久；幂等（同 verdict 且无 material change → no-op）。
- **fail-closed**：✅ market-gated：非 prediction_market / 无 source_id / 无 baseline|estimated → `return None`。
- **静默失败**：⚠️ **是（风险）**。freeze 在 `_persist_events` 的 try 块内，抛错只 `logger.warning` 吞掉，
  该事件无预测但无显式告警。见断点 B。

### 6. Resolve Outcome
- **连通**：✅（机制上）`event_auto_resolve` → `auto_resolve_events` → `resolve_with_calibration`。
- **落盘**：✅ event_store outcome+calibration、event_audit outcome 快照、prediction 终态三处都落盘。
- **可恢复**：✅ 已结算事件下次被 `outcome is not None` 跳过；幂等。
- **fail-closed**：✅ `status != 'resolved'`（invalid/void）→ 不进 calibration + `void_prediction`；
  diverged 身份冲突 → invalid。
- **静默失败**：❌ **是（结构性风险）**。连接点是 **question 文本相似度匹配**（`find_match`），不是 contract_id。
  若 resolved market 的 question 文本与冻结时不一致、或该市场不在当日 `fetch_resolved_markets` 返回里，
  事件**永不被匹配 → 永不结算 → 永不评分**，且无任何告警。见断点 A。

### 7. Calibration
- **连通**：✅（读取侧）`calibration_summary` / `segment_skill`。
- **落盘**：⚠️ 读 predictions 表；无独立 `calibration_metrics` 表（M2 仍折叠在 predictions）。
- **可恢复**：✅ 纯查询，无状态。
- **fail-closed**：✅ act-only 显式过滤；invalid/void/superseded 不进聚合。
- **静默失败**：✅ 无数据时返回 `no_data`，语义明确。

### 8. Trust
- **连通**：✅ `freeze_prediction` 调 `segment_skill(category)` → `diagnose`。
- **落盘**：✅ trust 值冻结进 prediction 行；segment_skill 实时查询不落盘。
- **可恢复**：✅
- **fail-closed**：✅ dormant（n<8）→ 默认 trust 0.5 且 `qualified=False` → 永不 act（原则 5）。
- **静默失败**：✅ 否，但见「dormant 毕业风险」。

### 9. Decision Report
- **连通**：✅（读取侧，按需）`build_decision_report` ← `/events/decisions/open` 等。
- **落盘**：N/A（实时组装，纯函数）。
- **可恢复**：✅
- **fail-closed**：✅ 无 prediction → 404 / 最小报告。
- **静默失败**：✅ 否。

## 三、闭环断点与风险

### 断点 A（P0）：Resolve 靠 question 文本匹配，非 contract_id —— 静默不结算
- 冻结时已拿到稳定 `source_id`（合约 id）并写进 link；本批改动也让三源 `fetch_resolved_markets`
  返回与冻结同源的 `id`（Kalshi `event_ticker`、Manifold market id、Polymarket id）。
- 但 `auto_resolve_events` 用 `find_match(question, index)` 做**文本相似度**匹配：
  exact normalized key → 1.0，否则 Jaccard token overlap ≥ 0.82（`text_match.py:33,120-132`）。
  `build_index` / `find_match` **从不读 `id` 字段**；`contract_id` 只在 match 之后从 `market_by_key`
  取出，仅用于 diverged 检查（`event_resolve_service.py:230-248`）。
- 后果：
  - resolved market 的 question 措辞与冻结时不同 → 匹配不上 → 事件永不结算。
  - 该市场不在当日 `fetch_resolved_markets` 窗口 → 同样永不结算。
  - **完全静默**：没有「这个有 verified link 的事件早该结算了」的检测。
- 本可用已落盘的 `contract_id` 直接对账（link 里有，resolved market 里也有 id），却没用——
  **M0 身份 ground truth 建好了但结算侧没消费它**。

### 断点 B（P1）：freeze 与 save 同一 try 块 —— 一损俱损且静默
`_persist_events`（`event_intelligence_service.py:402-415`）把 `save_events` / `record_event` /
`freeze_prediction` 放在同一 try/except，任一抛错 → `logger.warning` 吞掉，该批后续步骤不执行。
例如 save 成功但 freeze 抛错，事件入库却无预测，无显式信号；下游不会知道「这个事件本应有预测」。

### 数据丢失风险
1. **Scheduler 不持久**：进程在触发时点宕机 → 当天 freeze/score 永久缺失，无补跑、无告警
   （仅 5 分钟 misfire 窗口）。
2. **event_audit 压缩**：`EVENT_AUDIT_COMPACTION_THRESHOLD=5000` / `EVENT_AUDIT_MAX_PER_EVENT=200`
  （`event_audit_service._maybe_compact`）按 event 保留最近 N 条概率快照——edge trajectory 只保最近
   200 条。outcome 快照单独保留（`_compact_records` 区分 kind），不会与概率预算互相挤占。
3. **freeze 静默吞错**（断点 B）。
4. **跨存储无事务边界**：resolve 路径先写 SQLite（score_prediction）再写 JSON（resolve_event/
   record_outcome），中途崩溃会留下**两库不一致**（事件已 resolved 但 prediction 仍 open，或反之），
   无补偿/对账作业。

### 永远不会触发的逻辑
- **`diverged` 身份冲突分支**几乎不可达（`event_resolve_service.py:243-248`）：它要求事件**已有**
  一条 verified link 且 contract_id 与本次匹配的不同。但 verified link 几乎只在 auto_resolve 当场写入，
  正常路径下事件在结算前没有 verified link（freeze 不写 link），所以 diverged 长期为 False。
  该 fail-closed 保护基本是**死逻辑**（与第一部分 P2 观察一致）。
- **act 决策**：在 dormant 未毕业前，`qualified=False` → `decide` 永不返回 act（正确，但见下）。

### Dormant 状态无法毕业的风险（P0 级隐患）
毕业条件：某 category 累计 ≥ 8 条 **resolved 的 act+watch** 预测（`segment_skill` n≥
`CALIBRATION_FEEDBACK_MIN_SAMPLES=8`，`prediction_store.py:449`）。推演链条上的三重收窄：
1. **要先有 resolved 预测** → 依赖断点 A 的文本匹配成功结算。若匹配长期失败，`segment_skill.n`
   永远是 0 → 永远 dormant → 永远不 act → realized_edge 永远 no_data。
2. **category 分散**：trust 按 category 累计（原则 6）。若 discover 的事件类目高度分散，单一 category
   很难单独攒满 8 条，毕业被进一步拖慢。
3. **watch 供给**：dormant 期 trust=0.5、liquidity_factor 视流动性。watch 需
   `|adjusted_edge|=|raw×0.5×liq|>=3`（`DECISION_WATCH_EDGE`）。流动性充足时需 raw≥6pt；不足时门槛更高。
   多数小 edge 事件落入 skip，不计入毕业（segment_skill 排除 skip）。

**结论**：闭环在「机制」上连通且能产数据，但毕业**强依赖断点 A 的文本匹配真能成功**。
断点 A 一旦在某 category 上长期匹配失败，该 category 数学上**永远无法毕业**，且无告警。
这是比第一部分 P0-1/P0-2（账本设计偏离原则）**更直接影响闭环能否真正产出 ground truth** 的隐患。

## 四、数据闭环连通性速查表

| 环节 | 连通 | 落盘 | 可恢复 | fail-closed | 静默失败 |
|---|---|---|---|---|---|
| Scheduler | ✅ | ⚠️内存 | ❌不补跑(仅5min窗口) | ✅job隔离 | ⚠️错过无告警 |
| Discover | ✅ | →下游 | ✅ | ✅源隔离 | ⚠️单候选吞错 |
| Event | ✅ | ✅原子+strict-load | ✅ | ✅校验 | ✅记日志 |
| Market Link | ✅ | ✅ | ✅ | ✅M0闸门 | ⚠️criteria仅事件侧 |
| Freeze | ✅ | ✅ | ✅幂等 | ✅market-gated | ⚠️同块吞错(断点B) |
| Resolve | ✅(机制) | ✅×3处 | ✅幂等 | ✅invalid不评分 | ❌文本匹配静默漏(断点A) |
| Calibration | ✅ | ⚠️折叠无独立表 | ✅ | ✅act-only | ✅no_data |
| Trust | ✅ | ✅冻结 | ✅ | ✅dormant封顶 | ✅ |
| Decision Report | ✅ | N/A | ✅ | ✅404 | ✅ |

## 五、闭环修复优先级建议（数据闭环部分）

- **P0**：断点 A —— resolve 用已落盘的 `contract_id` 做主匹配，question 文本仅作兜底；
  并增加「有 verified link 但超期未结算」的检测，消除静默不毕业。
  （实现上最小侵入：`build_index` 同时索引 `id`；`find_match` 先按 contract_id 命中，再退回 question。）
- **P0**：dormant 毕业可观测性 —— 暴露每 category 的 `segment_n` 进度（已冻结进 prediction 行，
  缺一个汇总视图），让「离毕业还差几条」可见，避免静默卡死。
- **P1**：断点 B —— `_persist_events` 拆分 save 与 freeze 的错误处理，freeze 失败单独告警，
  不被 save 成功掩盖。
- **P1**：Scheduler 持久化 / 错过补跑 —— 至少在启动时检测「上次运行至今是否跨过触发时点」，
  或记录一条明确的 missed-run 日志。
- **P1**：跨 SQLite/JSON 的 resolve 一致性 —— 加启动对账（event 已 resolved 但 prediction
  仍 open 的修复扫描），或把 outcome 也落进 SQLite 以获得单库事务。
- **P2**：`diverged` 死逻辑 —— 要么让 freeze 阶段就写 verified link（使身份冲突检查真正可达），
  要么明确它只在 manual 场景生效并文档化。

**本部分为审查记录，未修改任何业务代码。**

---

# 第三部分：存储设计审计

日期：2026-06-19
范围：v2_loop.db（`predictions` / `event_market_links`）+ JSON stores（`event_store.json` /
`event_audit.jsonl`）。对照**当前磁盘 schema**（已读 `prediction_store._SCHEMA` /
`event_market_link_store._SCHEMA` / `event_store` / `event_audit_service`），非凭记忆。
环境：SQLite 3.50.4（已确认支持 partial unique index）。

## 一、数据模型图（当前实际）

```
SQLite: v2_loop.db  (WAL, foreign_keys=ON, 进程级 _WRITE_LOCK=threading.Lock)
├── predictions
│     id                  TEXT PRIMARY KEY         ← uuid4，每行唯一
│     event_id            TEXT NOT NULL            ← ⚠️ 无 UNIQUE（P0-1 已移除）
│     contract_id         TEXT DEFAULT ''
│     platform            TEXT DEFAULT ''
│     base_rate_category  TEXT DEFAULT 'unknown'   ← segment key（category-only）
│     ai_probability / market_probability / raw_edge   [冻结，不可变]
│     trust / adjusted_edge                            [冻结]
│     liquidity / volume                                [冻结]
│     decision            TEXT DEFAULT 'tracked'   ← act|watch|skip
│     liquidity_factor / qualified / segment_n /
│       segment_skill                                  [冻结，P1-2 诊断解释 4 列]
│     created_at          TEXT
│     status              TEXT DEFAULT 'open'
│                           open|scored|observed|voided|superseded
│     actual_outcome / brier_score / resolved_at       [resolve 时 UPDATE in place]
│     INDEX(status), INDEX(event_id), INDEX(base_rate_category)
│     约束：仅 PK(id)。一事件多行靠"应用逻辑"维持"至多一条 open"，DB 不强制。
│
└── event_market_links
      id / event_id / contract_id / market_question / resolution_criteria
      link_method / link_confidence / verified / linked_at
      UNIQUE(event_id, contract_id)   ← ✅ DB 强制
      INDEX(event_id), INDEX(contract_id)

JSON: event_store.json   { event_id: {first_seen, last_updated, record:{...,outcome,calibration}} }
      键唯一性 = dict key（event_id）→ 一事件一条 ✅；locked_file + write_json_atomic + read_json_strict
JSON: event_audit.jsonl  append-only 概率/outcome 快照；按 EVENT_AUDIT_MAX_PER_EVENT=200 压缩
                           （outcome 快照单独保留，不挤占概率预算）
```

关系：`event_store(event_id)` 1 — N `predictions(event_id)`；
`event_store(event_id)` 1 — N `event_market_links(event_id, contract_id)`。
**无外键**（predictions/links 不 FK 到 event_store，因后者是 JSON）。

## 二、逐项核对

### 1. 是否仍符合 One Event → One Prediction —— ❌ 否（DB 层），⚠️ 仅应用层维持
- DB 已无 `UNIQUE(event_id)`（P0-1 移除，见 `_migrate` 的 rename→rebuild→copy）。
  一个事件可有多行（superseded + 终态 + open）。
- 「至多一条 open」**不再由 DB 约束保证**，只由 `freeze_prediction` 的
  `SELECT-open → 比较 → INSERT` 逻辑维持（`prediction_store.py:252-292`）。
  **不变式从 DB 约束降级为应用代码**——更弱、更易被未来改动破坏。

### 2. 是否存在隐式多版本 Prediction —— ⚠️ 是
- `superseded` 行就是显式的旧版本；append-on-material-change（P0-2）会在**同一 verdict** 内
  因概率/adjusted_edge 漂移 ≥5pt 再造一版（`_materially_changed`）。一个事件在结算前可能积累
  多条历史版本行。这正是第一部分判定「Prediction 是 Commitment 不是 Trajectory」的存储层证据。
- 但**终态行每事件至多一条**（结算后事件被 `outcome is not None` 跳过，不再产生第二条终态）。

### 3. 是否有重复冻结风险 —— 单进程✅安全 / 多进程⚠️有
- `freeze_prediction` 的 SELECT-open + INSERT/UPDATE 都在 `with writing()`（持 `_WRITE_LOCK`）
  内，**单进程内串行**，不会并发产生两条 open。
- 但 `_WRITE_LOCK` 是**进程级**（`threading.Lock`，`sqlite_db.py:29`）。若部署多个后端进程/worker
  指向同一 v2_loop.db，两进程可同时 SELECT 到无 open → 各 INSERT 一条 → **两条 open**，
  「至多一条 open」破裂。当前单进程假设下安全，横向扩展时是隐患。

### 4. 是否有重复 Resolve 风险 —— ✅ 双重幂等，但有孤儿 open 风险
- `score_prediction` / `void_prediction` 都 `WHERE status='open'`：首次 resolve 后该行转终态，
  二次 resolve 找不到 open → no-op，幂等。
- 事件层：`auto_resolve_events` 跳过 `outcome is not None` 的事件，幂等。
- **孤儿 open 风险**：若事件已结算（event_store 有 outcome）后，discover 又对同一 event_id 触发
  freeze 并 append 一条新 open，则 auto_resolve 因事件已 resolved 而**永久跳过** → 这条 open
  永远不会被 resolve/score → 永久挂在机会面。append-on-change 放大了这个风险（P0-1 + P0-2
  共同副作用）。多行模型使该风险首次成为可能——一行模型下 `UNIQUE(event_id)` 会阻止 append。

### 5. 是否存在 Trust 统计污染 —— 当前✅未污染 / 结构上脆弱
- `segment_skill` = `status IN ('scored','observed') AND decision IN ('act','watch')`
  （`prediction_store.py:449`）：superseded（NULL brier）与 voided 被排除，正确。
- 每事件至多一条终态行进入统计（结算后事件被跳过，不会再有第二条终态），所以**当前无双计**。
  但这个保护来自「事件层 outcome 跳过」**不是 DB 约束**。若将来 resolve 逻辑改为给多条 open 评分，
  立即变成同一事件多行污染 trust。脆弱。

### 6. 是否有未来迁移障碍 —— ⚠️ 是（P0-1 制造的）
- 若按第一部分建议**回退到一行模型**（重加 `UNIQUE(event_id)`），现存多行数据（superseded 历史行）
  会**违反新约束**导致迁移失败。回退迁移必须先**折叠/删除非 open 历史行**再加约束——这是 P0-1
  反向制造的迁移债。
- `_migrate` 的检测式重建（rename→recreate→copy→drop）本身可复用，但**没有 schema 版本号**，
  靠「探测 UNIQUE 索引是否存在」判断（`prediction_store.py:101-108`），方向性迁移（加回约束）
  需要新的探测逻辑，且无法记录「这个库已迁移到哪一步」。
- P1-2 的 4 个诊断列是额外 schema 表面，回退或重构时需一并决定去留。

## 三、唯一约束建议

| 表 | 当前 | 建议 |
|---|---|---|
| predictions | 仅 PK(id) | **首选：加部分唯一索引** `CREATE UNIQUE INDEX ux_pred_open ON predictions(event_id) WHERE status='open'`（SQLite 3.50.4 已确认支持），把「至多一条 open」**下沉为 DB 约束**，同时消除重复冻结（第 3 项）与孤儿 open（第 4 项）。若回退一行模型：恢复 `UNIQUE(event_id)`（需先折叠历史行）。 |
| event_market_links | UNIQUE(event_id,contract_id) | ✅ 合理，保留。 |
| event_store(JSON) | dict key | ✅ 天然唯一。 |

**判断**：即使保留多行模型，也应加 `UNIQUE(event_id) WHERE status='open'` 部分索引——它让第 3 项
（重复冻结）和第 4 项（孤儿 open）在 DB 层被强制，而不是靠注释和锁。这是在「不回退」前提下
最低成本修补不变式的方法。

## 四、幂等性检查

| 操作 | 幂等 | 机制 | 风险 |
|---|---|---|---|
| freeze_prediction | ✅ 同 verdict + 无 material change → no-op | SELECT-open 比较 | 多进程下可双 INSERT |
| score_prediction | ✅ | WHERE status='open' | 无 |
| void_prediction | ✅ | WHERE status='open' | 无 |
| upsert_link | ✅ | ON CONFLICT(event_id,contract_id) DO UPDATE | 无 |
| save_events | ✅ upsert | dict key 覆盖，保 first_seen | 无 |
| resolve_event | ✅ | 覆盖 outcome（同值幂等） | 无 |
| record_event/outcome | ❌ 每次新增一行 | 设计如此（快照流水） | 重复触发 = 重复快照（压缩兜底） |

## 五、Append-only 合规性检查

**结论：表不是真正的 append-only，文档/docstring 措辞过度。**
- 决策字段（ai/market/raw_edge/trust/adjusted_edge/decision/诊断列）**冻结不可变** ✅。
- 但 resolve 时对**同一行** `UPDATE status, actual_outcome, brier_score, resolved_at`
  （`score_prediction` `prediction_store.py:327-334`）——这是**就地写入**，不是 append。
  `superseded` 也是对旧行 `UPDATE status`。
- 真实性质应表述为：**「决策字段 write-once 不可变；结局字段 write-once 就地填充；
  状态字段允许 open→终态的单向流转」**，而非「append-only」。
- 路线图 invariant「Predictions are append-only ... never recomputed」在**决策字段**上成立
  （从不重算），但**字面「append-only」**与就地 UPDATE 实现不符——属第一部分指出的
  路线图措辞 vs 实现张力的存储层印证。建议改 docstring/文档措辞，避免误导后续维护者。

## 六、JSON Store / SQLite Store 风险评估

**SQLite（v2_loop.db）**
- 优点：WAL 并发读、事务 commit/rollback（`writing` 上下文：成功 commit / 异常 rollback，
  `sqlite_db.py:71-82`）、约束可强制、short-lived 连接（避免跨 async/threadpool 共享）。
- 风险：
  1. `_WRITE_LOCK` 进程级 → 多进程部署失去串行保证（重复冻结/孤儿 open，见第 3/4 项）。
  2. 无 schema 版本表，迁移靠结构探测，方向性回退缺机制（见第 6 项）。
  3. `foreign_keys=ON` 但 predictions/links **无实际 FK**（指向 JSON 的 event_store，无法 FK）
     → 引用完整性不被强制，可能存在指向已删除事件的孤儿预测/链接。

**JSON（event_store.json / event_audit.jsonl）**
- 优点：event_store 用 `locked_file` + `write_json_atomic` + `read_json_strict`（损坏抛错不覆盖），
  相当稳。
- 风险：
  1. event_store **全量读改写**——事件量增长后每次 resolve/save 重写整个文件，O(N) 写放大，
     是未来扩展瓶颈。
  2. 跨 JSON 与 SQLite **无事务边界**：resolve 路径先写 SQLite（score_prediction）再写 JSON
     （resolve_event/record_outcome），中途崩溃会留下**两库不一致**（事件已 resolved 但 prediction
     仍 open，或反之）——无补偿/对账作业检测。这正是第 4 项「孤儿 open」的存储层根因。
  3. event_audit 压缩截断旧概率快照（max 200 条），M3 edge trajectory 的长历史会丢
     （outcome 快照单独保留，不会丢结算标记，仅丢早期概率点）。

**最大结构风险**：predictions(SQLite) 与 event_store(JSON) 是闭环两端却**无共享事务**。
`resolve_with_calibration` 跨两个存储多次写入，任一中途失败即不一致，且**无对账作业**检测。

## 七、存储审计修复优先级

- **P0**：加 `UNIQUE(event_id) WHERE status='open'` 部分索引（或回退一行模型），把「至多一条 open」
  从应用逻辑下沉为 DB 约束——同时消除重复冻结（第 3 项）与孤儿 open（第 4 项）。
- **P0**：跨 SQLite/JSON 的 resolve 一致性——加启动对账（event 已 resolved 但 prediction 仍 open
  的修复扫描），或把 outcome 也落进 SQLite 以获得单库事务。与第二部分断点 A 修复配套。
- **P1**：回退/重构迁移需先折叠 superseded 历史行，否则重加 UNIQUE 失败（P0-1 迁移债）。
- **P1**：引入 schema 版本号表，替代「探测索引存在性」的隐式迁移判断。
- **P2**：修正 docstring/文档的「append-only」措辞为「决策不可变 + 结局就地填充 + 状态单向流转」。
- **P2**：`foreign_keys=ON` 当前是空转——要么真正建 FK（需把 event 落进 SQLite），要么在文档说明
  links/predictions 与 JSON event_store 的引用完整性不强制、靠应用保证。

**本部分为审查记录，未修改任何业务代码。**

---

# 第四部分：开源发布删除候选审计

日期：2026-06-19
标准：以「准备开源发布」为目标，找出可删除内容。**所有结论均由真实调用关系核验**
（grep 全仓导入 + `api/router.py` 路由注册 + `frontend/src/lib/api.ts` 消费 + 调度器引用 + 测试引用），
非凭记忆。

## 背景判断

项目自身定位（见 ARCHITECTURE_PHILOSOPHY / V2_ROADMAP）：**找市场错价（Edge）+ 对已结算现实
保持校准的反馈闭环，而非又一个 AI 新闻/交易平台。** 但仓库里并存两套系统：

- **V2 事件闭环（产品本体）**：`events` 路由 + `event_*` 服务 + `prediction_store` +
  `event_market_link_store` + `diagnosis_service` + `decision_report_service` + `trend_analysis_service`
  + `calibration_service_event`。数据走 `event_store.json` / `event_audit.jsonl` / `v2_loop.db`。
- **Legacy 交易层（V0.3 前身）**：`markets` / `scanner` / `analysis` / `trades` / `backtest` /
  `signal_accuracy` / `resolve`(旧) / `calibration`(旧) / `news` 路由 + `signal_*` /
  `trade_journal_service` + 多 Agent 层（`agents/` 11 文件，1369 行）+ `reputation_engine` +
  `auto_resolve_service`(市场层) + `calibration_service`(市场层)。数据走 `agent_memory.json` /
  `analysis_audit.jsonl` / `market_cache`。交易词汇，与闭环并行但**不进下游**。

删除候选按「是否需要产品决策」分三层。

## TIER 1 — 安全删除，零功能影响（最高 ROI，无需产品决策）

| 项 | 证据 | 收益 |
|---|---|---|
| `backend/app/services/signal_service.py` | 全仓 0 调用、0 测试引用（文件自身标注 `DEPRECATED`）。**不影响功能** | 删纯死代码（9 行） |
| 前端 `app-nav.tsx:59` 的 `/dashboard "经典"` 链接 | `frontend/src/app/dashboard` 目录**不存在**（已核验：`dir` → NO dashboard dir），build 路由表无 `/dashboard` | 删 1 行死链，避免用户点击 404 |
| `get_predictions()` + `GET /events/{id}/predictions` 端点 | 前端 `api.ts` 无 wrapper、无任何消费者（仅服务自身 import，见第一部分 P1-1） | 删 dead 端点（随多行账本回退自然消失） |
| 仓库杂物（已核验均存在）：`.diff_temp.txt`(221KB)、`debug.log`、`backend/_full_out.txt`(0B)、`backend/event_store.json.bak`(464KB)、`backend/archive/backend.7z`、`.qoder/`、`.workbuddy/` | 工作区产物 / 旧代码压缩包 / IDE 缓存 / 旧数据备份 | 不该进开源仓库；删除 + 补 `.gitignore` |

**TIER 1 全部已用调用关系证实，删除不影响任何功能。** 可立即执行。

## TIER 2 — Legacy 子系统整体移除（需产品签字；复杂度大幅下降）

开源发布最大的一个决定：**是发布「干净的 V2 闭环」，还是连同 V0.3 交易助手一起发？**

### 可达性核验（关键结论：Legacy 层前端零消费）

前端实际调用的后端路径（`frontend/src/lib/api.ts` 全量）**只有**：
`/events/*`、`/calibration/summary`、`/news`、`/analysis`、`/events/resolve/auto`。

逐路由可达性（grep + 路由注册 + 前端消费三重核验）：

| 路由 | 注册位置 | 前端消费者 | 判定 |
|---|---|---|---|
| `/markets` | `router.py:10` | **无** | 可删 |
| `/backtest`（文件自称 Legacy） | `router.py:14` | **无** | 可删 |
| `/signals/accuracy` | `router.py:17` | **无** | 可删 |
| `/trades` | `router.py:16` | **无** | 可删 |
| `/scan/*`（`scanner.py`，含 `/deep`） | `main.py:56` 单独注册 | **无** | 可删（**这是 Agent 层唯一入口**） |
| `/resolve`(旧) | `router.py:15` | **无**（前端 `resolveAuto` → `/events/resolve/auto`，非此路由） | 可删 |
| `/calibration`(旧) | `router.py:13` | 仅 `/calibration/summary` 一个端点被前端 `health()` 用 | **保留 `/summary`，其余可删** |
| `/news` | `router.py:11` | 待核（前端有 `/news` 字样） | **需先解耦再删** |
| `/analysis` | `router.py:12` | **无**（`/analyze` 前端页只调 `eventsApi.analyze`=`/events/analyze`） | 可删 |

**重要更正（相对可能存在的旧判断）**：`/analyze` 前端页（`frontend/src/app/analyze/page.tsx`）
**只依赖 `eventsApi.analyze`**（即 V2 的 `/events/analyze`），**不依赖** Legacy `/analysis` 路由。
因此移除 Legacy `/analysis` 不会断 `/analyze` 页——之前担心的「唯一耦合点」其实不存在，迁移负担
比预想小。

### 移除单元（按依赖自底向上）

| 子系统 | 文件 / 规模 | 可达性 | 风险 |
|---|---|---|---|
| 多 Agent 层 `app/agents/`（orchestrator + 9 agents + base） | 11 文件，**1369 行** | **仅** `scanner.py /api/scan/deep` 一处入口 | 过度抽象的 Service 群，与闭环无关 |
| `reputation_engine` + `judge_agent` | 含于 Agent 层 | 仅 Agent 层内部用 | 随 Agent 层一起 |
| Legacy 交易路由 + 服务：`trades`+`trade_journal_service`、`signal_accuracy`+`signal_tracker`、`backtest`、`markets`、`scanner`、`analysis`、`resolve`(旧) | ~17 文件，连同 Legacy 服务/存储共 **1823 行** | 各自路由注册，但**前端零消费、不进 V2 闭环下游** | 交易词汇，与「reality filter」定位冲突 |
| Legacy scheduler 作业 `morning_scan@07:00` / `evening_resolve@22:00` | `scheduler.py` 两个 job | 自动运行、消耗 LLM | 与 `event_discover`/`event_auto_resolve` 并行产生**第二套**校准数据，易混淆 |
| Legacy 存储：`agent_memory.json` / `analysis_audit.jsonl` / `market_cache` / `auto_resolve_service`(市场层) / `calibration_service`(市场层，非 `_event`) | 多文件 | 仅 Legacy 层用 | 与 event_store 双轨 |

**架构师建议**：开源发布应只保留 V2 闭环，移除整个 Legacy 交易层 —— 它是 **~3200 行**
（Agent 层 1369 + Legacy 路由/服务/存储 1823）、与产品定位（reality filter，非交易助手）冲突、
且制造「两套校准口径」的认知负担。但这**改变对外功能面**（删掉 `/scan`、`/trades`、`/backtest`、
`/markets`、`/signals/accuracy`、`/analysis`、`/resolve`(旧) 等端点），必须由你拍板，不能由审查
单方面执行。

**注意：`/calibration/summary` 必须保留**（前端 `health()` 用）；若移除 Legacy
`/calibration` 路由，需先把 `/summary` 端点迁到 V2 侧（或前端改读 `/events/calibration`）。
`/news` 同理需先确认无前端依赖再删。

## TIER 3 — 配置 / 死分支 / 过度设计（局部清理）

| 项 | 性质 | 建议 |
|---|---|---|
| `cross_validation_service` | opt-in，默认关闭（`CROSS_VALIDATION_MODEL` 未设即整模块 no-op） | 若从未启用：删服务 + 3 个 `CROSS_VALIDATION_*` 配置项；若保留：文档标注为可选 |
| `diverged` 身份冲突分支（`event_resolve_service.py:243-248`） | 永不触发的死逻辑（见第二部分） | 让 freeze 阶段写 verified link 使其可达，或删除并文档化「仅 manual」 |
| `PREDICTION_RESNAPSHOT_DELTA` + `_materially_changed` | 过度设计（第一部分 P0-2） | 随多行账本回退一并删除 |
| prediction 表 4 个诊断冻结列（`liquidity_factor`/`qualified`/`segment_n`/`segment_skill`） | 提前实现的 M5 镀金（第一部分 P1-2） | 移到报告层按需算，或接受 |
| `get_predictions()` + ledger 端点 | dead code（第一部分 P1-1，已列 TIER 1） | 随多行账本回退消失 |
| 无意义配置项排查 | `POLYMARKET_CRYPTO_FETCH_ENABLED`、`EMBEDDING_*`、`CROSS_VALIDATION_*` 等若发布时不用 | 逐项确认默认值与文档；未用的从 `.env.example` 删 |
| `signal_tracker.py` 路径修复 diff | 本批仅修旧路径 bug，属 Legacy（TIER 2 随移除） | 随 Legacy 层移除 |

## 收益排序

### 按「不影响功能」排序（先删这些，0 风险）
1. **仓库杂物（cruft）** —— 0 风险，立即删（`.diff_temp.txt` 等 7 项，含 464KB 的 `.bak`）。
2. `signal_service.py`（DEPRECATED 死码）+ 前端 `/dashboard` 死链 —— 0 风险。
3. dead `get_predictions` 端点 —— 0 消费者。

### 按「降低复杂度」排序（收益最大）
1. **多 Agent 层 `app/agents/` + `reputation_engine`**（~1369 行，单入口 `/scan/deep`）—— 删除即
   去掉最大的过度抽象块。
2. **整个 Legacy 交易层**（~1823 行）—— 去掉第二套词汇/存储/校准，使代码库与产品定位一致。
3. 回退多行账本 + 删 `PREDICTION_RESNAPSHOT_DELTA` / 诊断 4 列（第一/三部分 P0/P1）。

### 按「减少维护成本」排序
1. **Legacy scheduler 双作业** —— 停掉后不再产生第二套数据，运营心智减半。
2. `cross_validation` / 未用配置项 —— 减少「这是干嘛的」类疑问。
3. **双存储**（`agent_memory`/`analysis_audit` vs `event_store`）—— 删 Legacy 后只剩一套。

## 删除前置依赖（执行顺序）

1. **先删 TIER 1**（无依赖，无风险，立即执行）。
2. **TIER 2 前**：确认 `/news` 与 `/calibration/summary` 的前端依赖——保留或迁移这两个端点，
   其余 Legacy 路由可删。（`/analyze` 页经核验只走 V2，无需迁移。）
3. **TIER 2**：移除 Agent 层（`agents/` + `reputation_engine`）→ Legacy 交易路由/服务
   （`trades`/`signal_accuracy`/`backtest`/`markets`/`scanner`/`analysis`/`resolve`旧）→
   Legacy scheduler 作业（`morning_scan`/`evening_resolve`）→ Legacy 存储
   （`agent_memory`/`analysis_audit`/`market_cache`/`auto_resolve_service`/`calibration_service`），
   按依赖自底向上。
4. **TIER 3**：随对应架构回退（第一/三部分 P0/P1）一并处理。

**本部分为审查记录，未删除任何代码。** TIER 1 可直接执行；TIER 2 需你确认
「开源范围 = 仅 V2 闭环」后执行。
