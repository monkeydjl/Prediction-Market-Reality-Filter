# 生产就绪度评审：Reality Feedback Loop（CTO 视角 / 代码核实版）

日期：2026-06-20
评审者：CTO 生产就绪度评审（Claude Opus 4.8）
范围：**仅** Reality Feedback Loop 全链路 —
`Scheduler → Discover → Event → Verified Link → Freeze Prediction → Resolve Outcome → Calibration → Trust → Decision Report`
方法：三个独立子审计并行读**当前磁盘代码**（非凭记忆），关键论断由评审者亲自复核到 file:line。逐阶段核对六项：创建 / 落盘 / 失败模式 / 可恢复 / 可观测 / 能否自动继续。
**不评**：风格、命名、格式、小重构。

> 说明：本目录已有一份 `PRODUCTION_READINESS_REVIEW_2026-06-20.md`（非本次评审产出）。本文件是代码核实版，并**修正**了那份文档的一处关键错误：它把「contract-id 优先结算」当作设计优点，但代码显示该路径在 freeze 阶段从不写 link，首次结算根本不触发（见 P1-2）；它也遗漏了 trust→0 吸收态（见 P1-3）。

---

## 零、一句话结论

**不能无人值守 90 天稳定累积。** happy path（单进程不崩溃 + 措辞稳定 + 合约在 top-200 已结算集 + 不出现 worse-than-random 类目）是正确且幂等的；但链路**没有进程守护、没有错过运行检测、没有跨存储原子性、没有任何对账/自愈扫描**（已 grep 全仓确认无 `reconcil|orphan|overdue|missed-run|last-success|repair`）。90 天里一次进程崩溃即**静默终止整个循环且无告警**（P0）；即使不崩溃，措辞漂移 / 合约翻页 / 崩溃孤儿 open / 类目信任锁死会**静默且不可恢复地**持续扣减已结算样本（P1 群）。累积速率在无人察觉中衰减。

---

## 一、链路数据流与落盘点（实测）

```
[FastAPI lifespan: main.py:21] --start_scheduler()--> APScheduler(in-process, MemoryJobStore, UTC)
   │  job_defaults: coalesce=True, misfire_grace_time=300  (scheduler.py:26-29)
   │  无持久 jobstore、无 last-success、无 missed-run 检测
   │
   ├─ 07:15 UTC  event_discover (gated: EVENT_DISCOVER_ENABLED)            [写盘]
   │     discover_events(use_cache=False)
   │       _collect_candidate_events  ← Polymarket/Manifold/Kalshi + open-web (源隔离)
   │       process_event → LLM → build_event_record   (per-candidate try/except)
   │       _persist_events(fresh):                     三段独立 error boundary
   │         ├ save_events()      → event_store.json   [locked + atomic + strict-load]  ← 批次门
   │         ├ record_event()     → event_audit.jsonl  [append + 压缩]   (隔离)
   │         └ freeze_prediction()→ v2_loop.db          [UNIQUE(event_id)+ON CONFLICT DO NOTHING]
   │              market-gated；写 status='open'        (隔离)
   │
   └─ 22:30 UTC  event_auto_resolve                                        [写盘 × 2 存储]
         auto_resolve_events(resolved_limit=200)
           fetch_resolved_markets × 3 源（Polymarket 按成交量 top-N）
           build_index + market_by_contract
           每个未结算 event:
             PRIMARY  get_verified_link(verified=1) 且 contract 在已结算集 → resolve_by_contract
             FALLBACK 未绑定 event → find_match(question, FUZZY≥0.82) → upsert_link
                      verified = (score >= AUTO_VERIFY_THRESHOLD=1.0)  ← 仅“精确归一化匹配”才 verified
             resolve_with_calibration (event_resolve_service.py:130-141):
                ① resolve_event()    → event_store.json  (写 outcome ← 这步使事件“已结算”)
                ② record_outcome()   → event_audit.jsonl
                ③ score_prediction()/void_prediction() → v2_loop.db (open→scored/observed/voided)
              ①②③ 跨两个存储后端，无共享事务、无两阶段协调

[读取侧 / 纯计算，无定时驱动、不落盘]
  calibration_summary()  act-only Brier/realized_edge   ← GET /events/predictions/calibration
  segment_skill(cat)     act+watch trust 输入            ← freeze 时读
  build_decision_report()                               ← GET /events/decisions/open, /{id}/decision
```

落盘三处：`event_store.json`（locked+atomic+strict-load）、`event_audit.jsonl`（append+压缩）、`v2_loop.db`（SQLite WAL + 进程级写锁）。**循环两端（predictions@SQLite 与 outcome@JSON）无共享事务、无对账作业。** 全仓无任何 reconciliation/orphan/overdue/missed-run/repair 扫描。

---

## 二、逐阶段六项核对

下表每行 = 一个阶段；六项按「创建 / 落盘 / 可失败 / 可恢复 / 可观测 / 自动继续」浓缩。file:line 为评审者亲核点。

### 1. Scheduler (`core/scheduler.py`)
- **创建**：无持久数据；进程内 `AsyncIOScheduler` + 两个 cron job。
- **落盘**：无。`MemoryJobStore`（无持久 jobstore，scheduler.py:26-29）。`next_run_time` 只在内存。
- **可失败**：① 进程在 07:15/22:30 宕机；② job body 抛错；③ `EVENT_DISCOVER_ENABLED=false` 静默零产出；④ 单 job 跑超 24h（max_instances=1 跳过）。
- **可恢复**：① **否** — 进程宕机错过的那次**不补跑**（`misfire_grace_time=300` 只救「调度器在跑但忙」，救不了「进程不在」），当天 freeze/score 永久缺失；② 是（外层 try/except + `logger.exception`，下次照常）。
- **可观测**：job 异常有 ERROR 日志；**进程宕机错过运行完全不可观测** — 无 last-success、无 missed-run 日志、无 healthcheck、无 dead-man switch。
- **自动继续**：进程活着就继续；进程死了**永久停**，无 supervisor（仓库无 systemd/PM2 配置）。

### 2. Discover (`event_intelligence_service.discover_events`)
- **创建**：候选 dict → `build_event_record` 产出 EventRecord dict（按 value_score 排序）。
- **落盘**：本阶段不直接落盘（`use_cache=False` 连缓存都不写），交给 `_persist_events`。
- **可失败**：① LLM key 失效 → 所有候选分析失败 → `count=0`；② 三市场源全挂 → 0 候选；③ 全部证据 feed 空 → 每候选 `selected_count==0` → 0 事件、无错误；④ 单候选 LLM/parse 失败（隔离）；⑤ 顶层异常（`_collect_candidate_events` 抛出）→ 整轮中止。
- **可恢复**：①**否**（次轮同 key 同样失败，静默零产出直到轮换）；②③ 次日可恢复但当天样本丢失；④**是**（`Semaphore(4)` + `return_exceptions=True`，单候选 skip）。
- **可观测**：源失败 WARNING、单候选失败 WARNING；**`count=0` 与「真没新事件」「所有 feed 全挂」三者日志无法区分**（都只是 INFO count=0）。
- **自动继续**：轮内单候选隔离、轮间调度器继续；顶层异常只中止当轮。

### 3. Event 持久化 (`_persist_events` + `event_store.save_events` + `event_audit_service.record_event`)
- **创建**：`{event_id, first_seen, last_updated, record}` upsert 项；一行 JSONL 审计快照；（freeze 见 §5）。
- **落盘**：event_store = `locked_file`(进程级 RLock) + `read_json_strict`（损坏抛错不覆盖）+ `write_json_atomic`（temp+os.replace）→ **强 fail-closed，设计优秀**。audit = `locked_file` append + 原子压缩。
- **可失败**：① **批内一条 record `model_validate` 失败 → `save_events` 抛错 → 整批中止**（同批有效记录全不落盘、不 freeze）；② save 成功 / audit 失败（隔离）；③ save 成功 / freeze 失败（隔离）。
- **可恢复**：① 市场事件次日可重发（freeze 写一次但本次没 freeze 过，可重新 freeze）；但当天审计快照（M3 轨迹点）永久丢、已滚出 feed 的 news 事件永久丢；③ **危险** — freeze 写一次（首见即承诺），save-ok/freeze-fail 只有在事件**结算前**被再次发现才会重试成功；若市场在此前结算或跌出候选池，该预测**永久丢失、永不进校准**（无重试队列/死信）。
- **可观测**：save 中止 ERROR（好）；audit/freeze 失败仅 per-event WARNING；**无「saved=M vs frozen=N」对账指标**。
- **自动继续**：是。三段独立 error boundary（save 是门：失败即 abort+ERROR）。**freeze GATE 正确**：仅 `source.type=='prediction_market'` 且有 source_id 且有 ai/market 概率才冻结，news 事件正确跳过。

### 4. Verified Link (`event_market_link_store` + `auto_resolve_events`)
- **创建**：`MarketLink` 行（event_id↔contract_id，`UNIQUE(event_id,contract_id)`，verified 标志）。
- **落盘**：SQLite，`ON CONFLICT DO UPDATE`，进程写锁 + WAL，幂等（单进程内）。
- **可失败**：① **freeze 阶段从不写 link**（全仓 `upsert_link` 仅 2 处调用：manual-resolve 与 text-fallback）→ 新事件首次结算时 `get_verified_link` 必为 None；② `verified=1` 要求 `score>=AUTO_VERIFY_THRESHOLD=1.0`（精确归一化匹配），fuzzy 0.82–0.99 写成 **unverified** link；③ manual link 存 `contract_id=""`。
- **可恢复**：见 P1-2 — 这条直接削弱 contract-first 的全部价值。
- **可观测**：`pending_count` + match_log；但 unverified-link-loop 无 per-event 告警。
- **自动继续**：fail-closed（None → 不评分），不会写错 outcome；代价是漏评分静默累积。

### 5. Freeze Prediction (`prediction_store.freeze_prediction`)
- **创建**：一行 Prediction（ai/market/raw_edge/trust/adjusted_edge/decision + 冻结诊断列），status='open'。
- **落盘**：SQLite，`UNIQUE(event_id)` + `INSERT ON CONFLICT DO NOTHING`，进程写锁。**一事件一预测，首见即承诺，DB 强制**。
- **可失败**：gate 未过 → None（设计）；DB 写失败 → 抛错（被 §3 隔离）；冲突 → 静默 DO NOTHING。
- **可恢复**：「至多一条 open」由 **DB 约束**保证（非仅应用逻辑），关闭了经典孤儿双写路径 — 这是相对上一版审计的真实改进。
- **可观测**：gate→None 对调用方静默；`DO NOTHING` 无日志。
- **自动继续**：是。**注意**：`UNIQUE(event_id)`（非 partial-on-status），故 scored/voided 行也挡住未来 re-freeze — 已结算事件永不能再预测（符合承诺语义，但要清楚）。`_migrate` 的 legacy 折叠依赖 `ROW_NUMBER()`（SQLite≥3.25），旧 sqlite 上整个 loop DB 初始化每次抛错（环境风险）。

### 6. Resolve Outcome (`resolve_with_calibration` + `score_prediction`/`void_prediction`)
- **创建**：outcome dict + calibration 快照；audit outcome 快照；prediction open→scored(act)/observed(watch,skip)/voided。
- **落盘**：**跨两存储，无共享事务**（resolve_event→JSON 在前 line130，score_prediction→SQLite 在后 line139）。
- **可失败**：① **崩溃于 ①resolve_event 之后、③score_prediction 之前** → 事件已结算但预测仍 open；次轮因 `outcome is not None` 跳过该事件 → 预测**永远不评分**；② **已绑定但合约不在 top-200 已结算集** → 每轮 `continue`、**永远等待、无 overdue 检测、无日志**；③ 未绑定事件 wording drift（阈值 1.0）→ pending、需手动 set_verified；④ 市场返回错误 outcome → 校准永久污染。
- **可恢复**：① **否（永久卡死，无对账扫描）**；② 否（除非合约重回 top-N）；③ 仅手动；text-fallback 路径若 resolve 抛错可被次轮 contract-first 重试治愈（因 link 已先 upsert+verified）。
- **可观测**：`resolved/pending/invalid_count` + match_log + 失败 WARNING；但孤儿 open、合约翻页**完全静默**。`invalid_count` 恒为 0（死指标）。
- **自动继续**：单事件失败隔离、循环继续；幂等性好（`score/void` 均 `WHERE status='open'`，二次 resolve UPDATE 0 行；resolve_event 覆盖式幂等）。

### 7. Calibration (`calibration_service_event` + `prediction_store.calibration_summary`)
- **创建/落盘**：per-prediction `brier_score` 在 score 时落盘；`calibration_summary` / `summarize` 全部**读时计算**，反映当前 scored 群体。
- **可失败**：除零/空/NaN 在 `summarize` 有防护（跳过 None/非有限 Brier、空群体返回 `_empty_overall`）；**但 `calibration_summary`/`_aggregate` 的 `round(mean_brier,4)` 无 None 防护** — 仅靠「scored 行必有 Brier」不变式，迁移/手注 NULL Brier 会崩。
- **可恢复 / 可观测 / 继续**：纯读自愈；空态显式 `grade:"no_data"`/`n:0`（非静默 0）；不阻塞循环。**最稳阶段。**

### 8. Trust (`segment_skill` + `diagnosis_service`)
- **创建**：`segment_skill` = {n, mean_brier, skill}（act+watch 已结算群体）；`diagnose` 产出 trust/adjusted_edge/decision/qualified/segment_n，**冻结进 prediction 行**（freeze 时一次，永不重算）。
- **毕业链（亲核）**：population = `status IN('scored','observed') AND decision IN('act','watch')`；`qualified = segment_n>=min_samples(8)`；`decide` 中 act 需 `qualified AND |adj_edge|>=10`。**watch 计入毕业**（observed+watch）→ 新类目可经 watch 自举出 dormancy，**无 act-only 死锁**（正确实现）。
- **度量口径（亲核）**：headline `calibration_summary` = act-only（`decision='act'`），`segment_skill` = act+watch（排除 skip），**两个群体在代码中确实不同、未被统一**，skip 两处都排除 — 无静默信任污染。
- **可失败**：dormant（n<8 或 mean_brier None）→ trust=0.5、cap 在 watch（设计冷启动保护）；**真正陷阱：已毕业类目 mean_brier>0.25 → skill<0 → trust clamp 0 → adjusted_edge=0 → 全 skip → skip 被排除出 trust 群体 → mean_brier 冻结 → 永久锁死**（无 recency/decay 窗口，仅手工 DB 干预可解）。
- **可恢复**：自举段自愈；**worse-than-random 锁死段不可自愈**（见 P1-3）。
- **可观测**：qualified/segment_n 冻结可见；锁死态无日志/告警，仅表现为「某类目静默永不出 act/watch」。

### 9. Decision Report (`decision_report_service` + routes)
- **创建/落盘**：纯读组装，**不写盘**。诊断块读自**冻结行**（反映决策时态，非当前段状态）。
- **可失败**：`record or {}` 等防护，缺失事件→最小报告不崩；`/decisions/open` 的 `decision` 正则 `^(act|watch)$`→坏值 422（非静默空）；`_diagnosis_reason` 对 `qualified is None`（迁移前行）落到泛化文案（误导非崩）。
- **可恢复 / 可观测 / 继续**：纯读、不阻塞；终端阶段。

---

## 三、问题分级

判级口径：**P0 = 停掉循环（累积归零或永久停摆）；P1 = 循环继续但静默扣减/污染校准质量；P2 = 未来维护成本。**

### P0 — 停掉循环

| # | 问题 | 阶段 | 机制（file:line） | 后果 |
|---|---|---|---|---|
| **P0-1** | **无进程守护 + 错过运行不补跑不告警** | Scheduler | in-process `AsyncIOScheduler` + `MemoryJobStore`（scheduler.py:26-29），仓库无 systemd/PM2；`misfire_grace_time=300` 救不了进程宕机 | 进程一旦崩溃/重启/OOM/被部署替换，循环**永久停**，无自动重启、无告警。90 天里这是头号杀手 |
| **P0-2** | **静默失败不可观测**（无 healthcheck / last-success / missed-run） | Scheduler+Discover+Persist | `count=` 取自**分析数**响应（event_intelligence_service:390），非落盘数；whole-batch save abort 仍报 `count=N` INFO（§3①） | 「整批持久化失败」与「成功」在运维唯一看的日志里**完全同形**；LLM key 失效 → 连日 `count=0` 无人知 |

> 判级说明：P0-1/P0-2 本身不改业务逻辑，但在「无人值守 90 天」这一**命题**下，它们直接决定「循环是否还在跑 / 是否还在产数」——一次未观测的进程死亡就让累积归零。故定为 P0。

### P1 — 静默扣减/污染校准质量（循环仍在跑）

| # | 问题 | 阶段 | 机制（file:line） | 后果 |
|---|---|---|---|---|
| **P1-1** | **跨存储无原子性 → 崩溃产生永久孤儿 open** | Resolve | `resolve_with_calibration` 先写 JSON outcome(line130) 后写 SQLite score(line139)，无共享事务；崩在中间 → 事件已结算、预测仍 open，次轮 `outcome is not None` 跳过 → 永不评分；**全仓无对账扫描** | 静默、不可恢复的校准样本丢失 + 机会面永挂幽灵 open |
| **P1-2** | **contract-first 名不副实：freeze 不写 link + 仅精确匹配才 verified** | Verified Link / Resolve | `upsert_link` 仅 manual+text-fallback 调用（freeze 从不写）；`get_verified_link` 要求 `verified=1`，而 `verified=(score>=1.0)`；fuzzy 0.82–0.99 写 unverified link 永不再被 contract-first 选中 | 首次结算只能靠**精确归一化问题匹配**；措辞漂移事件每轮重跑 text-match、永远 pending、需手动 verify — 设计最承重的「抗漂移」属性实际不生效 |
| **P1-3** | **trust→0 吸收态：类目永久锁死** | Trust | 已毕业类目 mean_brier>0.25 → `skill=1-brier/0.25<0` → trust clamp 0 → adjusted_edge=0 → 全 skip；skip 被排除出 `segment_skill` 群体（decision IN act,watch）→ mean_brier 冻结、无 recency/decay → 自我强化锁死 | 被前 8 个差样本「毒化」的类目永久停在 skip，仅手工 DB 可解；无日志 |
| **P1-4** | **已结算合约不在 top-200 → 永久等待无 overdue 检测** | Resolve | 已绑定事件每轮 `continue`（event_resolve_service.py:267-270），Polymarket 按成交量 top-N（resolved_limit=200）；低成交量/翻页合约静默缺席 | 该事件每轮跳过、永不结算、完全静默 |
| **P1-5** | **批内一条坏 record 中止整批持久化** | Event | `save_events` 统一 validate+单次原子写；一条 `model_validate` 失败 → 整批 abort（有效 N-1 条全不落盘/不 freeze） | LLM 输出方差导致整轮样本丢失；build_event_record 防御性强故概率低，但代价是「全有或全无」 |
| **P1-6** | **freeze 写一次失败的样本可能永久丢** | Persist/Freeze | save-ok/freeze-fail 仅在事件结算前被再次发现才重试；快速结算或跌出候选池则永久丢，无重试队列/死信 | 静默永久丢失该事件校准样本 |
| **P1-7** | **三市场源齐挂 = resolve 整轮丢失** | Resolve | 三源全不可达 → `auto_resolve_events` 返回 no_resolved_markets，无缓存/队列 | 结算周期丢失（次日可恢复但当天 0 结算） |

### P2 — 未来维护成本

| # | 问题 | 机制 | 影响 |
|---|---|---|---|
| **P2-1** | `calibration_summary`/`_aggregate` 的 `round(mean_brier,4)` 无 None 防护（prediction_store.py:401,464） | 仅靠「scored 行必有 Brier」不变式 | 迁移/手注 NULL Brier 行会 `round(None)` 崩；event 层 `summarize` 才有 None/NaN 防护 |
| **P2-2** | `_migrate` legacy 折叠依赖 `ROW_NUMBER()`（SQLite≥3.25，prediction_store.py:124） | 旧 sqlite 上每次初始化抛错 | 仅影响经历过 M3 实验的 DB；新 DB 跳过该路径 |
| **P2-3** | 无 schema 版本表，迁移靠 PRAGMA 结构探测（prediction_store.py:_migrate） | 加列可探测，改/删列不可探测 | 方向性迁移脆弱 |
| **P2-4** | 全部 atomicity 依赖**进程内**锁（file_store.py:13 RLock, sqlite_db.py:29） | 多 worker/多进程部署失效；`write_json_atomic` 无跨进程文件锁 | 横向扩展时 event_store 丢更新、「至多一条 open」破裂 |
| **P2-5** | `invalid_count` 恒为 0 死指标（event_resolve_service.py:217,353）；manual link 存 `contract_id=""` | 身份冲突分支已删但计数残留 | resolve 摘要误导性遗漏 |
| **P2-6** | event_store.json 全量读改写（每次 resolve/save 重写整文件，O(N)）+ 无 TTL/归档；audit 压缩截断旧轨迹（M3 KPI 依赖） | 事件量增长后写放大；长轨迹丢失 | 扩展瓶颈 + KPI 数据衰减 |

---

## 四、最终问题：能否无人值守 90 天持续累积已结算预测？

**不能。** 架构骨架对，但缺运维肌肉与几处静默数据完整性漏洞。

**按时间线推演最先坏什么：**
- **D1–7**：手动起进程且不崩 → 正常发现+结算，累积如常。
- **D7–30**：最可能的静默失效 = **LLM key 失效 / 限流**（P0-2）→ `discover` 连日 `count=0`，job「成功」，无告警。同时 P1-2 让大量措辞漂移事件停在 pending、不进校准。
- **D30–60**：一次进程崩溃/OOM/重启（P0-1）→ 调度器随之死亡、无 supervisor → 数据在盘上安全但**累积彻底停止**，无人知。
- **D60–90**：即便进程不死、API 正常，P1-1 的孤儿 open、P1-4 的合约翻页、P1-3 的类目锁死会持续静默扣减；多数类目仍 dormant，realized_edge 长期 no_data。

**做得好、确实利于长跑的部分（不算问题）：**
- 数据**耐久性**：strict-load 损坏不覆盖、temp+os.replace 原子写、WAL、re-scan 保留 outcome/calibration。系统不会自毁数据。
- **fail-closed**：错 outcome 不进校准；invalid/void 不评分；`get_verified_link` 单闸门。
- **幂等**：`score/void WHERE status='open'`、`freeze ON CONFLICT DO NOTHING`、`UNIQUE(event_id)` DB 强制「一事件一预测/至多一 open」。
- **隔离**：源/候选 `return_exceptions=True`；`_persist_events` 三段 error boundary。
- **冷启动正确**：watch 计入毕业，无 act-only 死锁；act-only vs act+watch 两套口径在代码中真实区分。

**达到 90 天无人值守信心所需的最小改动（按优先级）：**
1. **进程守护**（systemd/PM2，自动重启）— 修 P0-1。
2. **healthcheck 端点**：报 scheduler 状态 / 上次 discover count / 上次 resolve count / open 数 / pending 数 / 各类目离毕业差几条；`count` 改报**落盘数**而非分析数 — 修 P0-2。
3. **启动 + 周期性 API key 探活**，失效快速失败并告警 — 修 P0-2。
4. **自动每日备份** 三个存储到带时间戳归档 — 防灾难。
5. **resolve 跨存储一致性**：score_prediction 与 outcome 同事务，或调换顺序（先 score 后 写 outcome），或加**启动对账扫描**（event 已 resolved 但 prediction 仍 open → 修复）— 修 P1-1。
6. **freeze 阶段即写 verified link**（用已知 contract_id），让 contract-first 真正成为首选路径；并把 `AUTO_VERIFY_THRESHOLD` 降到 0.9 左右 / 或建立 pending 复核流程 — 修 P1-2。
7. **trust recency 窗口 / 衰减**，打破 worse-than-random 吸收态 — 修 P1-3。
8. **「verified link 超期未结算」检测** + 告警 — 修 P1-4。
9. **save 改为逐条容错**（坏 record 跳过而非整批 abort）— 修 P1-5。

**部署建议路径：** 先上 P0-1/2/3/4 → 前 30 天带主动监控运行、按观测到的匹配率调 `AUTO_VERIFY_THRESHOLD` → 修 P1-1/P1-2（孤儿对账 + contract-first 真正生效）→ 累计 >100 条已结算预测且覆盖 >5 类目、且无孤儿 open 后，方可考虑减监控运行。完整无人值守需同时修 P1-3（吸收态）。

---

## 五、速查表

| 阶段 | 创建 | 落盘 | 关键失败 | 可恢复 | 可观测 | 自动继续 |
|---|---|---|---|---|---|---|
| Scheduler | job | ❌内存 | 进程宕机错过 | ❌不补跑 | ❌missed 静默 | ⚠️进程死即停 |
| Discover | EventRecord | →下游 | LLM key 失效 | ❌同 key 复败 | ⚠️count=0 同形 | ✅候选隔离 |
| Event | upsert 项+审计 | ✅原子+strict | 批内坏 record 整批 abort | ⚠️次日 | ✅save ERROR | ✅三段边界 |
| Verified Link | MarketLink | ✅SQLite | freeze 不写 link + verified 须=1.0 | ⚠️见 P1-2 | ⚠️pending | ✅fail-closed |
| Freeze | Prediction | ✅UNIQUE+DO NOTHING | gate/DB 写 | ✅DB 强制一 open | ⚠️DO NOTHING 静默 | ✅ |
| Resolve | outcome+calib | ⚠️跨2存储无事务 | 崩溃孤儿 open / 合约翻页 | ❌孤儿永久卡 | ❌孤儿/翻页静默 | ✅单事件隔离+幂等 |
| Calibration | Brier/grade | 读时算 | round(None) 无防护 | ✅自愈 | ✅no_data | ✅最稳 |
| Trust | trust/decision | 冻结进行 | worse-than-random 锁死 | ❌锁死段不自愈 | ⚠️锁死无日志 | ✅冷启动正确 |
| Decision Report | 报告 dict | ❌纯读 | 缺事件→最小报告 | ✅ | ✅422 非静默 | ✅终端 |

---

*本文件为只读评审记录，未改任何业务代码。结论基于 2026-06-20 磁盘代码 + 三路并行子审计 + 评审者亲核关键 file:line。*

---

# 第二部分：数据模型审计（实测今日实现，非文档宣称）

日期：2026-06-20
范围：Event / Prediction / Outcome / Calibration / Trust 五个核心模型。**忽略实现细节，只看数据语义。**
每个模型核对：真相源 / 可变还是不可变 / append 还是覆盖 / 不变式 / 下游假设。
方法：直读 schema 与记录形状（`models/event.py`、`prediction_store._SCHEMA`、`event_store.save_events`、`event_audit_service`、`event_resolve_service`），亲核到 file:line。

## 总览：今天实际的数据模型（不是文档说的）

文档说「predictions append-only、never recomputed」「一个 outcome」「calibration 打分最新估计」。**实测并非如此。** 真实形态是：

- **5 个逻辑模型散落在 3 个物理存储**（`event_store.json` / `event_audit.jsonl` / `v2_loop.db`），**无外键、无共享事务、无对账**。
- **同一个事实被写进多处**：outcome 存 3 份，calibration 存 2 份（且用两个不同概率算）。
- **没有任何持久化的 Trust 模型**：trust 是「predictions 表上的读时聚合」+「冻结进每行的历史快照」的二元存在。
- **跨存储的连接键是 `event_id = sha1(问题文本)[:12]`**——身份由内容派生，问题文本即主键。

<!-- DM-MODELS -->

---

## 一、Event 语义

| 维度 | 实测 |
|---|---|
| **真相源** | `event_store.json`，dict 键 = `event_id`。`event_id = hashlib.sha1(question)[:12]`（event_intelligence_service.py:684-685）——**48 位、由问题文本派生**。 |
| **可变/不可变** | **可变**。`save_events` 是 upsert（event_store.py:44-91）：每次 re-scan 用新 `record` **整体覆盖**旧 record。 |
| **append/覆盖** | **覆盖**（read-modify-write 整个 dict）。例外：`outcome`/`calibration`/`tracking` 三字段「缺失则继承」——incoming record 不带它们时保留库中已有（event_store.py:71-80），防止 re-scan 把已结算事件打回未结算。 |
| **不变式** | 一 event_id 一条 entry；`EventRecord.model_validate` 是写入门（坏记录抛错，event_store.py:81）；`first_seen` 永不变、`last_updated` 每次刷新。 |
| **下游假设** | predictions / links / audit 都假设 `event_id` **跨 scan 稳定**；decision_report 假设事件 record 始终存在（缺失则退最小报告）。 |

**关键语义陷阱**：event 身份 = 问题文本哈希。**问题文本是内容，又是主键。** 任何对问题文本的归一化/改写（市场措辞漂移、清洗逻辑变更）→ 产生**新 event_id** → 同一现实事件变成两条事件、两条预测，旧的永远停更。身份与内容耦合。

## 二、Prediction 语义

| 维度 | 实测 |
|---|---|
| **真相源** | `v2_loop.db` `predictions` 表，`event_id TEXT NOT NULL UNIQUE`（prediction_store.py:37）。 |
| **可变/不可变** | **混合**。决策字段（ai/market/raw_edge/trust/adjusted_edge/decision + 4 个冻结诊断列）**写一次不可变**；结局字段（status/actual_outcome/brier_score/resolved_at）**就地 UPDATE 填充**（prediction_store.py:288-296）。 |
| **append/覆盖** | **既非 append 也非纯覆盖**：首次 `INSERT ON CONFLICT(event_id) DO NOTHING`（写一次承诺），之后 status 单向流转 `open→scored/observed/voided` 的**就地 UPDATE**。文档「append-only」**不成立**——准确表述应为「决策字段 write-once；结局字段 write-once-fill；status 单向就地流转」。 |
| **不变式** | **至多一行/event_id（DB 强制 UNIQUE，非仅应用逻辑）**。`score/void` 均 `WHERE status='open'` → 幂等。已结算事件因 UNIQUE 永不能 re-freeze。 |
| **下游假设** | 假设 `predictions.event_id` 能 join 到 event_store 的 event_id——**但无外键**（SQLite↔JSON 无法 FK）。孤儿预测（指向已删/从未保存事件）不被任何约束阻止。 |

<!-- DM-OCT -->

## 三、Outcome 语义

| 维度 | 实测 |
|---|---|
| **真相源** | **三份拷贝，无单一真相源**：① `EventRecord.outcome`（event_store.json，`Outcome` 模型）；② `predictions.actual_outcome`（v2_loop.db）；③ audit outcome 标记（event_audit.jsonl，record_outcome）。 |
| **可变/不可变** | ① 覆盖（`resolve_event` 重写）；② 就地填充；③ append（每次 record_outcome 追加一行）。 |
| **append/覆盖** | 三种语义混存同一事实。 |
| **不变式（应有，未强制）** | 三份应一致。**无任何机制保证**——`resolve_with_calibration` 顺序写 ①(line130)→③(line132)→②(line139)，**无共享事务**。 |
| **下游假设** | 「事件是否已结算」由 ①`outcome is not None` 判定（auto_resolve 跳过门，event_resolve_service.py:230）；「预测是否已评分」由 ② status 判定。**一个逻辑状态、两个布尔，仅靠 happy-path 顺序同步。** |

**语义不一致**：崩溃于 ①之后 ②之前 → 事件「已结算」但预测「仍 open」，两份 outcome 真相**永久分歧**（即第一部分 P1-1 孤儿）。`Outcome.status` 文档注「单一状态 resolved」但字段实际透传 `status`（invalid/void 也写入 ①，calibration=None）——event 层与 prediction 层终态不对称（prediction 有 `voided`，event 层 Outcome 无对应专门态）。

## 四、Calibration 语义

| 维度 | 实测 |
|---|---|
| **真相源** | **两个不同的数，都叫 calibration/brier**：① `EventRecord.calibration`（event_store，`Calibration` 模型）；② `predictions.brier_score`（v2_loop.db）。 |
| **打的什么概率** | ① 打**最新轨迹估计** `trend["latest_probability"]`（event_resolve_service.py:104, score_event）；② 打**冻结的首见 `ai_probability`**（prediction_store.py:287）。**同一事件可得两个不同 Brier。** |
| **可变/不可变** | 均 resolve 时**写一次快照**，之后不变。 |
| **append/覆盖** | 覆盖式写一次（随 outcome 落 ①；随 score_prediction 落 ②）。 |
| **不变式** | ① 打移动估计的准确度；② 打承诺的准确度——**两个语义不同的指标共用 calibration/brier 名字**。`calibration_summary` 读 ②（act-only）；`GET /events/calibration` 读 ①（事件层全体）。两套口径、两个群体、两个概率。 |
| **下游假设** | 前端 history 页同时展示二者（分别标注），但**没有任何文档/代码说明它们打的是不同概率**——读者易当成同一指标的两个聚合。 |

**这是最深的语义不一致**：「calibration」是个**重载词**，指向两个用不同输入算出的数。文档分别说「打最新估计」「冻结」各自成立，但**合起来 = 一事件两个 Brier、无人协调、不可比**。

## 五、Trust 语义

| 维度 | 实测 |
|---|---|
| **真相源** | **无持久化 Trust 模型/表**。live 值 = `segment_skill(category)` 对 `predictions` 表的**读时聚合**（prediction_store.py:373-401）；另有一份**冻结快照**（trust/segment_skill/qualified/segment_n）写进每条 prediction 行（freeze 时一次）。 |
| **可变/不可变** | live 聚合**每次重算**（随新结算预测变化）；行上冻结副本**不可变**（决策时态上下文）。 |
| **append/覆盖** | live = 纯函数无落盘；冻结副本 = 随预测行 write-once。 |
| **不变式** | 群体 = `status IN('scored','observed') AND decision IN('act','watch') AND base_rate_category=?`（亲核 prediction_store.py:390-395）。watch 计入毕业 → 无 act-only 死锁。 |
| **下游假设** | `diagnose` 假设 `segment_skill` 反映该类目当前技能；decision_report 展示的是**冻结时态**的 trust，非当前（已毕业类目的旧 open 行仍显示 `qualified=False`）。 |

**二元存在的风险**：trust 既是「predictions 表的实时投影」，又是「散落在每行的历史快照」。`base_rate_category` 作为分段键被冻结进行——**若类目划分逻辑变更，旧行的冻结类目不迁移**，trust 分段分裂（老样本留旧类目名下、新样本进新类目，同一现实类目技能被劈成两段）。

<!-- DM-SYNTH -->

---

## 六、综合发现

### 语义不一致
1. **「Calibration」重载**：event 层打移动估计、loop 层打冻结承诺，同名不同义不同值，无协调（最严重）。
2. **「Outcome」三写无事务**：3 份拷贝靠 happy-path 顺序同步，崩溃即永久分歧。
3. **「append-only」名不副实**：predictions 的 status/outcome 字段是就地 UPDATE，不是 append；真相在 audit 层（轨迹）而非 predictions。
4. **终态不对称**：prediction 有 `voided`，event 层 `Outcome` 模型无对应专门态。

### 隐藏耦合
1. **predictions.event_id ⋈ event_store.event_id 无外键**（SQLite↔JSON），孤儿预测无约束阻止。
2. **「事件已结算」(event_store.outcome) 与「预测已评分」(loop status) 是一个逻辑态的两个布尔**，仅靠写顺序同步。
3. **event_id = hash(问题文本)**：身份耦合内容，问题改写即换身份。
4. **decision_report join 冻结预测 + 实时事件 record**：`market_probability` 冻结 vs record baseline 实时，可漂移。
5. **base_rate_category 冻结进预测行**：类目逻辑变更不回迁 → trust 分段分裂。

### 被违反的不变式
- 路线图「Predictions are append-only … never recomputed」：决策字段成立，但**行不是 append-only**（status/outcome 就地写）。
- 「一个 outcome」：实为 3 份，可分歧。
- 「at most one open per event」：**今已 DB 强制**（UNIQUE+ON CONFLICT），但只在**单进程**内（threading.Lock）；多进程部署破裂。

### 未来迁移风险
1. **event_id = sha1(question)[:12]（48 位、内容派生）**：任何问题文本归一化变更 → **全量事件 re-key、身份大规模断裂**，历史 event_id 无法对应。最大迁移地雷。
2. **两个 Brier 不可调和**：若日后「统一 calibration」，event 层（移动估计）与 loop 层（冻结承诺）历史数字**语义不可比、无迁移路径**。
3. **无 schema_version 表**：迁移靠 PRAGMA 结构探测（加列可探、改/删列不可探），方向性迁移脆弱。
4. **Calibration/Outcome 以冻结 dict 存于 EventRecord（extra=allow）**：改模型形状后旧记录仍是旧形状，无版本化、无回填。
5. **legacy 折叠依赖 SQLite≥3.25 `ROW_NUMBER()`**：旧 sqlite 上经历过 M3 实验的 DB 初始化每次抛错。

---

## 七、最终回答：今天实际实现的数据模型是什么？

**不是文档描述的「append-only 事件溯源闭环」。** 实测是一个**以问题文本哈希为连接键、把 5 个逻辑模型铺在 3 个无事务存储上的、读时投影 + 冻结快照混合的模型**：

- **Event** = 可变、覆盖式的 JSON 主记录（身份 = 问题文本哈希，内容即主键）。
- **Prediction** = 一事件一行、决策字段不可变 + 结局字段就地填充的 SQLite 承诺行（DB 强制唯一，无外键回指事件）。
- **Outcome** = 同一事实的 3 份拷贝（event_store 权威门 + loop DB 评分驱动 + audit 流水），无事务同步。
- **Calibration** = 两个同名不同义的 Brier（移动估计 vs 冻结承诺），两套口径并存、不可比、无人协调。
- **Trust** = 无持久模型；= predictions 表读时聚合 + 冻结进每行的历史快照（分段键冻结，类目变更即分裂）。

**一句话**：数据模型的**正确性**靠「单进程 + 不崩溃 + 问题文本永不归一化 + 不横向扩展」四个隐式前提支撑；任一被打破，身份、一致性、可比性即静默瓦解。与第一部分运维结论一致——骨架对，但完整性未写进约束，只写进了 happy-path 顺序与进程内锁。

*只读审计，未改业务代码。基于 2026-06-20 磁盘代码与亲核 file:line。*

---

# 第三部分：运营韧性审计（故障注入视角）

日期：2026-06-20
范围：在 7 类故障假设下审计 — LLM API 中断、Polymarket 中断、RSS 失败、调度器重启、进程崩溃、磁盘写失败、部分持久化。
核对 6 维：恢复路径 / 重试行为 / 幂等 / 重复处理 / 数据损坏风险 / 回填能力。每个风险给：检测方法 / 恢复方法 / 严重度。
方法：亲核重试配置、原子写语义、调度器 misfire 语义、resolve 回填窗口。

## 关键事实（亲核）
- **重试**：LLM 客户端 SDK 级 `max_retries=2` + `timeout=60s`（probability_engine_service.py:80-81；cross_validation 同）。**数据源 fetch 无重试**：Polymarket `httpx.AsyncClient(timeout=30)` + `raise_for_status()`，无重试无退避（polymarket_service.py:47-49）。
- **原子写**：`write_json_atomic` = tempfile + `os.replace`，失败清理 temp 并抛错（file_store.py:85-105）；`read_json_strict` 损坏/IO → 隔离 `.corrupt` + 抛错中止写（不覆盖）。
- **调度器**：`MemoryJobStore` + `coalesce=True` + `misfire_grace_time=300`。进程宕机错过的 cron **不补跑**（misfire 只救「调度器在跑但忙」≤5min）；重启后 `next_run_time` 重算到下一次未来 cron。
- **resolve 回填**：`auto_resolve` 每轮 `list_all_events()` 扫**全部**未结算事件（event_resolve_service.py:223-230）；但 `fetch_resolved_markets` 按 **volume 排序 + limit**（非时间窗口，polymarket_history_service.py:34-37）。
- **discover 回填**：`use_cache=False` 每轮重拉**当前 live** 候选——**无历史回填**。

<!-- RES-RISKS -->

## 一、逐故障审计（检测 / 恢复 / 严重度）

### 1. LLM API 中断
- **影响**：discover 阶段每候选分析失败；SDK 重试 2 次后抛错，被 per-candidate try/except 吞为 skip → `count=0`。
- **检测**：⚠️ 弱。源/候选 WARNING，但整轮 `count=0` 与「真没新事件」日志同形；无 key 探活、无告警。
- **恢复**：自动——API 恢复后下一轮 07:15 正常发现。**但**：中断期间出现并结算的市场永久错过（discover 无回填）。
- **严重度**：**P1**（短时自愈；长时 = 静默零产出 + 永久样本缺口）。

### 2. Polymarket 中断
- **影响**：discover 少一个候选源（源隔离，`return_exceptions=True`，不影响 Manifold/Kalshi）；resolve 侧 `fetch_resolved_markets` 该源返回空 → 该平台事件当轮不结算。
- **检测**：⚠️ 源失败 WARNING；resolve 摘要 `resolved_count` 偏低，但无「某源连续失败」告警。
- **恢复**：自动——resolve 每轮扫全部未结算事件，源恢复后下一轮按 contract/text 重试结算（幂等）。
- **严重度**：**P2**（源隔离 + 每轮全量重扫 = 良好自愈；唯一缺口见风险 4 的 top-N 窗口）。

### 3. RSS / 证据 feed 失败
- **影响**：`collect_shared_articles` 单源失败隔离；**全部 feed 失败 → 每候选 `selected_count==0` → 0 事件、无错误**。
- **检测**：❌ 差。全 feed 挂表现为 `count=0` INFO，与平静日无法区分。
- **恢复**：自动——feed 恢复后下一轮正常。当轮样本丢失。
- **严重度**：**P2**（部分失败仅降证据质量；全失败 = 静默空轮，但次日恢复）。

### 4. 调度器重启
- **影响**：进程重启后 `MemoryJobStore` 无持久任务；`next_run_time` 重算到下一次未来 cron。
- **检测**：❌ 无。无 last-success 时间戳、无 missed-run 日志。
- **恢复**：⚠️ **半自动**：若 **外部 supervisor 重启了进程**，调度器随之恢复、次日 cron 正常。**但仓库无 systemd/PM2**（第一部分 P0-1）——无外部拉起则不恢复。重启**当日**已过的 cron 不补跑。
- **严重度**：**P0**（恢复完全依赖一个不存在的外部 supervisor）。

### 5. 进程崩溃
- **影响**：调度器与 FastAPI 同生死；崩溃即循环停。盘上数据安全（崩溃前已 commit 的）。
- **检测**：❌ 无健康检查 / dead-man switch。
- **恢复**：同风险 4——依赖外部 supervisor。崩溃**正在写**时见风险 6/7。
- **严重度**：**P0**（无自动重启）。

### 6. 磁盘写失败（满 / 权限 / IO）
- **影响**：`write_json_atomic` 写 temp 或 `os.replace` 失败 → 清理 temp、抛错 → **原文件不动**（event_store 不被破坏）；SQLite 写失败 → 事务 rollback。
- **检测**：✅ save abort ERROR（_persist_events 门）；freeze/score 失败 per-event WARNING。
- **恢复**：自动——磁盘恢复后下一轮重写（event upsert 幂等、freeze ON CONFLICT、score WHERE open）。当轮审计快照丢。
- **严重度**：**P2**（设计良好：原子写 + 不覆盖 + 幂等重试；不致损坏）。

### 7. 部分持久化（跨存储崩溃）
- **影响**：`resolve_with_calibration` 顺序写 ①event_store outcome(line130) → ③audit(line132) → ②loop score(line139)，**无共享事务**。崩在①后②前 → 事件「已结算」、预测「仍 open」。
- **检测**：❌ **完全静默**。无对账扫描（全仓 grep 无 reconcil/orphan/repair）。
- **恢复**：❌ **不可恢复**：次轮因 `outcome is not None` 跳过该事件 → 孤儿 open 永不评分、永挂机会面。
- **严重度**：**P1**（静默永久数据不一致 + 校准样本丢失；即第一部分 P1-1）。

<!-- RES-DIMS -->

## 二、六维评估

| 维度 | 评估 |
|---|---|
| **1. 恢复路径** | 进程内自愈强（每轮全量重扫未结算事件 + 幂等写）；进程级恢复**缺失**（无 supervisor、无健康检查）。恢复 = 「外部拉起进程」→ 次日 cron 自然续跑，但「外部拉起」未实现。 |
| **2. 重试行为** | 仅 LLM SDK 级 `max_retries=2`。**数据源 fetch 无重试**（单次 30s timeout，失败即该轮该源缺席）。**无作业级重试 / 无死信队列 / 无退避**——失败的发现/结算靠「下一次定时轮」隐式重试。 |
| **3. 幂等** | **强**。`save_events` upsert 保留 first_seen/outcome/calibration；`freeze ON CONFLICT(event_id) DO NOTHING`；`score/void WHERE status='open'`；`resolve_event` 覆盖式；`auto_resolve` 跳过 `outcome is not None`。重启后重跑不双算。唯一非幂等：`record_outcome` 手动重 resolve 会追加重复审计行（无害）。 |
| **4. 重复处理** | 基本无。UNIQUE(event_id) + outcome-skip 门挡住重复 freeze/score。多进程下 `_WRITE_LOCK`（进程级）失效 → 可能双 freeze / 双写 event_store（last-writer-wins）——单进程假设下安全。 |
| **5. 数据损坏风险** | **低**。原子 temp+replace、strict-load 损坏不覆盖、`.corrupt` 隔离、SQLite WAL+事务。真正风险不是「损坏」而是**跨存储不一致**（风险 7 孤儿）——数据各自完好但逻辑态分歧。 |
| **6. 回填能力** | **不对称且部分缺失**。**Resolve 侧**：事件轴回填好（每轮扫全部未结算），但市场轴受限——`fetch_resolved_markets` 按 volume top-N 非时间窗口，故「中断期结算的高成交量合约」次日仍在 top-N → 被结算；「低成交量被挤出 top-N」→ 永久错过、无检测。**Discover 侧**：**无回填**——`use_cache=False` 只拉当前 live，中断窗口内「出现且结算」的事件永不被冻结，永久缺口。 |

## 三、最终回答：24 小时中断后能否自动恢复？

**部分能，但不完全，且关键一步依赖一个不存在的前提。**

分三层看一次跨越 07:15 与 22:30 的 24h 中断：

1. **进程层（决定性瓶颈）**：中断若由进程崩溃/宕机引起，**系统不会自己重新拉起**——仓库无 systemd/PM2/supervisor（P0）。必须有外部进程管理器重启它，否则 24h 后依然是停的。**「自动恢复」在这一层就断了。** 若有外部 supervisor，调度器在进程重启时自动恢复，次日 07:15/22:30 正常续跑。

2. **结算层（基本自愈）**：进程恢复后，`auto_resolve` 每轮扫全部未结算事件 + 幂等结算，会**自动追上**中断期间积压的、且仍在 top-N-by-volume 已结算集里的结算。这部分回填**自动且正确**。漏网的只有「中断期结算且已跌出 top-N 的低成交量合约」——静默永久错过。

3. **发现层（不可回填）**：中断当天的 07:15 discover **不补跑**（misfire 不覆盖进程宕机），且 discover 无历史回填能力。**中断窗口内出现并在恢复前结算的事件，永远不会被冻结成预测**——这是 24h 中断造成的永久、静默的样本缺口。

**结论**：
- **数据不损坏**（原子写 + 不覆盖 + 幂等），重启不会重复处理——这两点**稳**。
- **结算积压能自动追平**（高成交量部分）——**较稳**。
- **但**：① 进程不会自启（需外部 supervisor，当前缺）；② 当日发现作业不补跑、发现层无回填 → 中断窗口的新事件永久丢；③ 中断期已 resolve-写一半的事件留下静默孤儿。

**一句话**：给它一个外部 supervisor，24h 中断后它能**自动续跑并追平大部分结算**；但它**无法回填中断窗口内错过的发现**，也**无法自愈跨存储孤儿与低成交量漏结算**——恢复是「续跑」而非「无损还原」。要做到后者，需补：进程守护 + missed-run 检测/手动补跑入口 + 启动对账扫描（修孤儿）+ resolve 改时间窗口（而非纯 volume top-N）+ 数据源 fetch 重试。

---

## 速查：故障 × 严重度

| 故障 | 检测 | 自动恢复 | 严重度 |
|---|---|---|---|
| LLM 中断 | ⚠️count=0 同形 | ✅短时 / ❌窗口内事件丢 | P1 |
| Polymarket 中断 | ⚠️源 WARNING | ✅每轮重扫自愈 | P2 |
| RSS/feed 失败 | ❌全挂=count=0 | ✅次日 | P2 |
| 调度器重启 | ❌无 | ⚠️依赖外部 supervisor（缺） | P0 |
| 进程崩溃 | ❌无健康检查 | ❌无自启 | P0 |
| 磁盘写失败 | ✅ERROR/WARNING | ✅原子+幂等重试 | P2 |
| 部分持久化 | ❌完全静默 | ❌孤儿永久不一致 | P1 |

*只读运营韧性审计，未改业务代码。基于 2026-06-20 磁盘代码与亲核 file:line。*


