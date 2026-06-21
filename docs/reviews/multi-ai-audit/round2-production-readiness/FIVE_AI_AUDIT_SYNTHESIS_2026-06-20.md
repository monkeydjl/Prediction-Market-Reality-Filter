# 5 份 AI 审计报告综合总结

日期：2026-06-20  
范围：`docs/Review-doc/2` 下 5 份审计报告  
目标：去重、归并共识、标出分歧，形成项目级整改优先级。

## 参与综合的报告

1. `Qwen3.7 Max-Reality_Feedback_Loop_Production_Readiness_Audit.md`
2. `Opus4.8-CTO_PRODUCTION_READINESS_REVIEW.md`
3. `GPT5.5-REALITY_FEEDBACK_LOOP_CTO_REVIEW.md`
4. `GLM5.2-REALITY_LOOP_AUDIT.md`
5. `DeepseekV4Pro-COMPREHENSIVE_ARCHITECTURE_REVIEW.md`

## 总体结论

5 份报告的共同判断是：

**当前系统有真实的 Reality Feedback Loop 骨架，但还不能按生产标准无人值守运行 90 天。**

系统已经具备几个重要优点：

- market / RSS / LLM 等外部源有一定隔离能力，单源失败通常不会拖垮整轮任务；
- event store 使用原子 JSON 写入，避免半写文件；
- prediction freeze 使用 SQLite `UNIQUE(event_id)` + `ON CONFLICT DO NOTHING`，能保证一事件一承诺；
- event-market link fail-closed，降低错误 outcome 污染 calibration 的概率；
- scoring 使用 `WHERE status='open'`，重复 resolve 不会重复计分；
- trust / decision / report 多数是纯计算或冻结快照，局部逻辑可测试性较好。

但这些优点仍不足以支撑长期无人值守。主风险不是“代码会不会立刻崩”，而是：

- loop 停了无人知道；
- run 失败了没有 durable run state；
- resolve 写一半会留下永久不一致；
- 发现/结算错过后没有可靠 backfill；
- calibration/trust 的语义分叉会长期积累解释成本；
- 多进程/重载部署会导致重复 scheduler 和跨进程写竞争。

## 五份报告的核心共识

### 1. Scheduler 是生产不可用的单点

共同结论：

- scheduler 是进程内 APScheduler；
- job store 不持久；
- 没有 last-success、last-error、run count、missed-run 记录；
- `misfire_grace_time=300` 只能处理很短误差，不能补 24 小时级别停机；
- 没有进程守护、leader election 或外部 cron 约束；
- 多 worker / reload 场景可能启动多个 scheduler。

综合严重性：**P0**

原因：

一个长期无人值守系统最先需要证明“它还在跑”。当前只能靠日志推断，且日志本身不形成系统状态。更严重的是，如果在多进程模式下运行，scheduler 可能重复触发，进而放大 LLM 成本、JSON lost update、SQLite contention 和 partial persistence。

### 2. Resolve Outcome 跨存储写入没有原子性

共同结论：

`resolve_with_calibration()` 实际会依次写：

1. event store JSON：写入 outcome + event calibration；
2. audit JSONL：追加 outcome snapshot；
3. SQLite predictions：`score_prediction()` / `void_prediction()`；
4. 有时还写 SQLite links。

这些写入没有共享事务。

最危险状态：

- event 已经写入 outcome；
- prediction 仍然是 `open`；
- 下一轮 auto-resolve 因为 event 已有 outcome 而跳过；
- prediction 永久不进入 `scored` / `observed` / `voided`。

综合严重性：**P0**

原因：

这会直接破坏“resolved predictions 持续积累”这一系统目标。更糟的是，它不是立刻可见的 crash，而是 silent inconsistency。

### 3. 可观测性不足是贯穿全链路的 P0

共同结论：

当前主要 observable surface 是日志和少量 API 查询。缺少：

- durable job runs；
- source-level success/failure counters；
- discovery count / freeze count / resolved count delta；
- pending link aging；
- orphan prediction detector；
- calibration sample growth monitor；
- process health / scheduler health endpoint；
- dead-man switch / alerting。

综合严重性：**P0**

原因：

无人值守系统必须能回答：

- 今天 discover 跑了吗？
- 跑了以后写入了多少 event？
- 冻结了多少 prediction？
- resolve 查了多少 settled markets？
- 新增了多少 scored / observed predictions？
- pending links 是否在堆积？
- calibration `n` 是否在增长？

当前不能稳定回答这些问题。

### 4. Verified Link 的 fail-closed 设计正确，但默认策略会饿死自动 resolution

共同结论：

fail-closed 是正确的：低置信 link 不应进入 scoring。

但多个报告指出：

- `AUTO_VERIFY_THRESHOLD=1.0` 意味着只有 exact normalized match 才能 auto-verify；
- fuzzy but correct matches 会进入 pending；
- pending links 需要人工 verify；
- 没有 pending backlog SLA / alert / auto-promotion workflow。

综合严重性：**P0 / P1，取决于生产目标**

- 如果目标是“无人值守持续积累 resolved predictions”：**P0**。
- 如果目标是“保守 supervised beta”：**P1**。

综合判断：

当前策略偏向 calibration integrity，但牺牲 unattended accumulation。项目必须明确策略：宁可少积累但低污染，还是引入自动语义校验来提高闭环率。

### 5. Prediction freeze 语义清晰，但不是 append-only ledger

共同结论：

实际模型是：

- 一事件一 prediction；
- first sight wins；
- re-scan 不更新 prediction；
- resolve 后同一行 UPDATE status/outcome/brier；
- trajectory 在 audit JSONL，不在 prediction table。

优点：

- 防 hindsight bias；
- freeze idempotency 强；
- scoring 幂等。

问题：

- 它不是 append-only prediction history；
- first bad analysis 永久进入 prediction layer；
- later material evidence 不会形成新 commitment；
- 如果 LLM/API degraded 时冻结，后续没有 re-freeze / quarantine 机制。

综合严重性：**P1**

### 6. Calibration 有两个语义不同的 Brier surface

共同结论：

当前至少有两个 calibration surface：

1. Event-level calibration：使用最新 audit trajectory estimate vs outcome；
2. Prediction-level calibration：使用 frozen first-sight `ai_probability` vs outcome。

此外：

- headline calibration：act-only；
- segment trust：act + watch；
- optional probability feedback：读取 event records；
- diagnosis trust：读取 prediction rows。

综合严重性：**P1**

原因：

这些分叉各自有合理性，但现在没有明确命名、文档化和产品表达。未来打开 calibration feedback 后，event-side 和 prediction-side 可能对同一 category 给出不同 skill 结论。

### 7. Trust 是冻结快照，不是当前实时信任

共同结论：

trust 在 freeze 时计算并冻结到 prediction row。随着新 outcomes 进入，当前 segment skill 会变化，但旧 open predictions 的 trust / adjusted_edge / decision 不会重算。

影响：

- decision report 展示的是 decision-time trust；
- opportunity ranking 可能基于过期 trust；
- category taxonomy 或 thresholds 变化后，旧 row 无法解释除非保存 settings/version。

综合严重性：**P1 / P2**

### 8. 24 小时 outage 后只能“继续跑”，不能“无损恢复”

报告之间措辞有分歧：

- 有的报告说“不可靠恢复”；
- 有的报告说“clean outage 可恢复，但有缺口”；
- 有的报告说“部分恢复”。

综合判断：

**系统可以在外部重启后继续 forward motion，但不能保证自动 catch up，也不能证明恢复干净。**

具体来说：

- process 如果没有 supervisor，不会自己恢复；
- 即使恢复，错过的 discover cron 不会补跑；
- resolved markets 是否还能被抓到取决于 source API 当前返回窗口；
- crash mid-resolve 的 orphan 不会自动修；
- LLM / price fallback 期间产生的低质量 frozen predictions 不会自动隔离；
- 没有 health state 告诉 operator 是否已经 catch up。

综合严重性：**P0 for unattended recovery**

## 主要分歧与裁决

### 分歧 1：LLM outage 是 zero discovery，还是 fallback degraded output？

部分报告认为 LLM outage 会导致 `count=0`；部分报告认为会 deterministic fallback，继续产出。

综合裁决：

以当前代码路径看，LLM failure 存在 fallback 分析路径；但不同 LLM 调用层、cross validation、open-web extraction 或模型解析失败路径可能表现不同。生产审计上不应把它简单归为“安全降级”。

最终风险应描述为：

**LLM outage 可能导致 zero output，也可能导致 fallback output；两者都缺少 durable detection。fallback output 如果被冻结为 prediction，会造成 calibration quality 污染。**

严重性：**P1，若 fallback rows 进入 headline calibration 且不可识别，则升 P0/P1 边界。**

### 分歧 2：24 小时 outage 是否能自动恢复？

综合裁决：

要区分两个层次：

- **Forward recovery**：能继续跑。部分能。
- **Correctness recovery**：能补齐缺口、修复 partial state、证明无损。不能。

所以项目级答案是：

**不能按生产标准自动恢复。**

### 分歧 3：AUTO_VERIFY_THRESHOLD=1.0 是 P0 还是 P1？

综合裁决：

如果系统目标是 calibration integrity，fail-closed 是正确设计；如果目标是 90 天无人值守持续积累 resolved predictions，它就是 P0。

项目当前目标明确是 Reality Feedback Loop 自动积累 resolved predictions，因此按 **P0 for unattended mode** 处理。

### 分歧 4：多进程 scheduler 是否一定存在？

部分报告把它列为关键 P0，部分报告没有展开。

综合裁决：

这取决于部署方式。但生产 readiness 必须禁止“部署方式一变就双跑”。所以即使当前单进程运行，仍应作为 **P0 deployment hazard** 处理：

- 单 worker 可临时规避；
- 长期应使用 external scheduler 或 leader election。

## 统一 P0 清单

### P0-1：缺少 durable scheduler/run state 与 healthcheck

影响：

- loop 停止、missed run、zero output 都可能无人发现。

整改：

- 新增 `loop_runs` 表；
- 记录每次 discover / auto_resolve 的 started_at、finished_at、status、counts、error；
- 增加 `/api/health` 或 `/api/loop/status`；
- 增加 dead-man alert。

### P0-2：进程内 scheduler 无 leader election / supervisor

影响：

- crash 后不自启；
- 多 worker / reload 可能重复触发 job；
- JSON store 跨进程 lost update；
- SQLite contention 导致 partial failure。

整改：

- 明确生产只允许一个 scheduler owner；
- 推荐 external cron / systemd timer / separate worker；
- 或 SQLite leader lock；
- 增加启动日志包含 PID 和 scheduler owner；
- 部署层增加 supervisor。

### P0-3：`resolve_with_calibration` 跨存储非原子写

影响：

- resolved event + open prediction orphan；
- calibration 样本永久丢失；
- 无自动检测。

整改：

- 最小改法：先 score/void prediction，再写 event outcome；若 scoring 失败，不写 outcome，让下轮可重试；
- 更完整：新增 reconciliation job；
- 最终：统一 outcome/prediction/link 到 SQLite 事务内，event JSON 只做 projection。

### P0-4：无 reconciliation / orphan repair

影响：

- partial persistence、manual double resolve、store drift 都无法自愈。

整改：

- 每日/启动运行 reconcile：
  - event has outcome but prediction open；
  - prediction terminal but event lacks outcome；
  - market event without prediction；
  - verified link without event；
  - pending links older than SLA；
  - calibration sample count stagnant。

### P0-5：无人值守 link policy 不成立

影响：

- fuzzy but correct matches pending forever；
- resolved predictions accumulation 被饿死。

整改选项：

- 保持 threshold=1.0，但承认 supervised mode，并加 pending review 工作流；
- 降 threshold 到 0.85-0.90，并增加 resolution_criteria / entity / source_id 校验；
- 引入自动 verification model，但保留 fail-closed confidence band。

### P0-6：无备份与灾难恢复

影响：

- `v2_loop.db` 损坏会丢 prediction/link/calibration history；
- event store 损坏需要人工恢复。

整改：

- 每日备份 `event_store.json`、`event_audit.jsonl`、`v2_loop.db`；
- 启动时做 store integrity check；
- 提供 restore runbook。

## 补强发现（综合后补充，经逐行核对代码）

以下两条经评审者亲核 file:line，是 5 份原始报告**遗漏或弱化**的结构性缺陷。它们不是新增观点，而是对综合 §4 与 §7 的硬化——把"策略偏保守 / 快照会过期"这类措辞，修正为"自动路径上事实失效 / 数学上永久锁死"。

### 补-A（P0，硬化 §4 / P0-5）：contract-first 主路径在自动闭环上**事实不生效**

综合 §4 / P0-5 只说"`AUTO_VERIFY_THRESHOLD=1.0` → fuzzy match 进 pending 需人工"。实际链条比这更严重：

1. `freeze_prediction`（`prediction_store.py`）**全程不调用 `upsert_link`**——全仓只有 `event_resolve_service.py:80`（manual resolve）与 `:292`（text-match fallback）两处写 link。冻结一条 prediction 时，并不会为它建立 event→contract 的 verified link。
2. auto_resolve 的 "PRIMARY contract-first 路径" 入口判定是 `linked = get_verified_link(event_id)`，而 `get_verified_link`（`event_market_link_store.py:139-151`）只取 `WHERE verified=1`。
3. 于是**常规事件首轮 resolve 时 `get_verified_link` 必为 None**，contract-first 分支根本进不去，只能落到 text-match fallback。
4. text-fallback 写入的 link 其 `verified = (score >= AUTO_VERIFY_THRESHOLD=1.0)`。fuzzy 命中（0.82–0.99，`FUZZY_THRESHOLD=0.82`）→ 写入的是 `verified=0` 的 link → 它**后续轮次也永远不会被 contract-first 重新选中**（因为 get_verified_link 过滤掉 verified=0）。

**结论**：文档与多份报告描述的 "contract-id 主路径，可抵御 question wording drift" 这一卖点，在**无人工干预的自动路径上等于不存在**。事件能否结算，完全取决于 text-match 是否精确命中（threshold 1.0）。这把 §4 的"calibration integrity vs unattended accumulation 的权衡"升级为"**自动积累实际依赖精确文本匹配，contract 身份未被消费**"。

**最小整改**：在 `freeze_prediction` 成功冻结市场型事件时，同步 `upsert_link(event_id, contract_id, verified=True)`——冻结时 contract_id 已知且确定（来自 `source.source_id`），本就该在此刻落地 verified link。这样 contract-first 路径才真正成为主路径，wording drift 才真正被绕开。

### 补-B（P1，硬化 §7）：已毕业 category 的 trust→0 **吸收态**（数学上永久锁死，无自愈）

综合 §7 只说 "trust 是冻结快照，旧 open prediction 不随新 outcome 重算"。但还有一个**比快照过期更硬**的结构缺陷，5 份报告均未提及：

1. `skill_score = 1 - brier/0.25`（`calibration_service_event.py:47`）。当某 category 的 `mean_brier > 0.25`（劣于随机），skill 为负。
2. `calibration_trust` 返回 `clamp01(skill_score(mean_brier))`（`diagnosis_service.py:43`）→ trust 被夹到 **0**。
3. trust=0 → `adjusted_edge = raw_edge × 0 × liquidity_factor = 0` → `decide` 对该 category **所有新事件**恒返回 `skip`。
4. 而 `segment_skill` 的统计群体是 `WHERE status IN('scored','observed') AND decision IN('act','watch')`（`prediction_store.py`，skip 被显式排除）。
5. 于是该 category 不再产生任何 act/watch 行 → 其 segment 统计群体**被冻结** → `mean_brier` 再也不会被新数据改善 → trust 永远是 0。

**结论**：一个被早期样本"毒化"（mean_brier>0.25）的已毕业 category，会进入**自我强化的吸收态**——永久 skip，且**没有 recency 窗口 / 衰减 / 重新评估机制**能让它自愈（整个查询跨全历史，不滚动）。只能靠人工改 DB 解锁。这是比 §7"过期快照"更严重的**单向锁死**，应作为独立 P1（接近 P0 的隐性闭环杀手）。

**最小整改**：trust 不要硬夹到 0（给一个 >0 的下限，如 dormant_trust，让劣质 category 仍能偶发 watch 以重新采样）；或对 `segment_skill` 引入滚动时间窗口 / 指数衰减，使被毒化的 category 能凭新近表现翻身。

## 统一 P1 清单

### P1-1：Calibration fork 未命名、未治理

整改：

- 明确命名：
  - `event_latest_estimate_calibration`
  - `frozen_prediction_calibration`
  - `trust_qualification_calibration`
- API 返回中明确 population 和 score basis；
- 产品层不要混用。

### P1-2：First-sight prediction 永久冻结，坏输入不可修正

整改：

- 给 frozen prediction 增加 input quality flags；
- LLM fallback / price fallback 时不进入 headline calibration；
- 必要时支持 `voided_due_to_bad_input`。

### P1-3：LLM / market price fallback 可能污染 calibration

整改：

- fallback 必须带 `quality_flag`；
- freeze 时如果 `API_ERROR` / synthetic market probability，则 skip freeze 或 freeze 为 non-calibratable；
- calibration SQL 过滤不可校准样本。

### P1-4：Trust frozen 后不随新 evidence / skill 更新

整改：

- 保留 frozen trust 作为 decision-time truth；
- 另加 current trust projection；
- open opportunities 排名可选择使用 current-adjusted view，但 report 必须显示 frozen vs current。

### P1-5：Cold-start / category dormancy 可能长期 watch-only

整改：

- 降低 min samples 或增加 bootstrap category；
- 合并 category taxonomy；
- 增加 category readiness dashboard；
- 对 watch samples 的进入/排除规则明确标注。

### P1-6：Backfill 能力不足

整改：

- resolved market fetch 改成时间窗口 / cursor，而不是只靠 top-N；
- 支持 missed-run catch-up；
- 支持重新处理 fallback analyses；
- 支持 old events link/provenance backfill。

## 统一 P2 清单

1. `event_id = question hash`，长期会遇到 wording drift / re-key 风险。
2. `legacy_analysis` 实际是核心数据，命名与职责不一致。
3. JSON + JSONL + SQLite 三套 store 增加迁移成本。
4. SQLite migration 依赖结构探测，缺少 schema version。
5. audit compaction 会限制长期 trajectory 分析。
6. RSS/feed 单源失败缺少细粒度指标。
7. `decision='tracked'` legacy default 可能产生隐形 rows。
8. 多处 status / decision / outcome 是自由字符串，缺少 enum 约束。
9. 缺少 endurance test / outage injection test。
10. 缺少 operator runbook。

## 推荐整改路线

### 第一阶段：先让系统“可知道是否还活着”

目标：解决无人值守 P0。

1. 加 `loop_runs` SQLite 表。
2. 每次 scheduler job 记录 run result。
3. 加 `/api/loop/status`：
   - last discover run；
   - last auto-resolve run；
   - event count；
   - open prediction count；
   - scored / observed / voided count；
   - pending link count；
   - orphan count；
   - calibration `n` delta。
4. 加进程 supervisor 或外部 scheduler。
5. 禁止多 worker 内重复启动 scheduler。

### 第二阶段：修复 resolved event / prediction 一致性

目标：解决最危险的数据一致性 P0。

1. 调整 resolve write order 或实现事务化。
2. 新增 reconcile job。
3. 新增 orphan repair API / command。
4. 加测试：
   - event outcome written but prediction open；
   - score failure 后下轮可恢复；
   - duplicate resolve 不造成 JSON/SQLite divergence。

### 第三阶段：治理 link 与 calibration 质量

目标：让 loop 真正积累可用样本。

1. 明确 unattended link policy。
2. pending link 加 aging、SLA、review queue。
3. 降低 auto verify threshold 或增加更强语义校验。
4. 给 LLM/API/price fallback 样本加 quality flags。
5. calibration SQL 排除不可校准或低质量输入。

### 第四阶段：数据模型收敛

目标：降低未来迁移成本。

1. 给 event/prediction/outcome/calibration/status 定义显式 schema version。
2. 抽出 typed `base_rate_category`、`resolution_criteria`、`contract_identity`。
3. 把 outcome 变成一等模型，减少 event JSON 与 prediction SQLite duplicate truth。
4. 明确三类 calibration surface，不再用同一个名字。
5. 增加 settings/model version snapshot，保证 trust 可解释。

### 第五阶段：运行韧性与灾备

目标：能承受 24h outage 和磁盘/DB 故障。

1. 自动备份三大 store。
2. store integrity check。
3. resolved market cursor / time-window backfill。
4. source fetch retry/backoff。
5. LLM fallback retry backlog。
6. 7 天 endurance test + fault injection：
   - LLM outage；
   - market outage；
   - RSS outage；
   - process crash mid-resolve；
   - disk write failure；
   - duplicate scheduler instance。

## 最终项目级判断

### 能否无人值守运行 90 天？

**不能。**

当前系统可以在 happy path 下连续运转，也能在许多单点外部失败下继续产出。但它缺少生产无人值守所需的三件东西：

1. 证明 loop 仍在运行的 durable health；
2. 跨存储 partial failure 的自动修复；
3. missed run / outage / fallback data 的 backfill 与质量隔离。

### 能否 24 小时 outage 后自动恢复？

**只能恢复 forward motion，不能恢复 correctness。**

它能在外部重启后继续未来 cron；auto-resolve 对部分 unresolved events 可能追上。但 missed discovery、orphan predictions、fallback-contaminated frozen rows、pending fuzzy links 都不会自动修复。

### 当前实际数据模型是什么？

实际是一个三存储拼接模型：

- Event：mutable JSON profile，`event_id` 是 question hash；
- Audit：compacted JSONL trajectory；
- Prediction：SQLite one-row frozen commitment；
- Link：SQLite verified / pending contract binding；
- Outcome：event JSON primary + prediction SQLite duplicate；
- Calibration：event-latest-estimate 和 frozen-prediction 两套语义；
- Trust：从 prediction history 读时聚合，再冻结进 prediction row。

这不是完整 event-sourced model，也不是强约束 relational model。

### 是否应该继续迭代？

**应该。**

5 份报告一致认为核心架构方向是对的：fail-closed identity、first-sight commitment、act/watch/skip trust gate、source isolation、atomic local writes 都是正确基础。现在缺的是生产化骨架：

- run ledger；
- scheduler ownership；
- reconciliation；
- health/alert；
- backup；
- backfill；
- semantic cleanup。

优先修 P0 后，系统可以从 supervised beta 进入可控长期运行。

## 一页优先级摘要

| 优先级 | 事项 | 目标 |
|---|---|---|
| P0 | `loop_runs` + `/api/loop/status` | 知道 loop 是否还活着 |
| P0 | 单 scheduler owner / supervisor | 避免停机和重复 job |
| P0 | resolve 跨存储一致性 | 避免 resolved event + open prediction |
| P0 | reconciliation job | 自动修 orphan / stale state |
| P0 | backup + restore runbook | 防 SQLite / JSON 灾难损失 |
| P0 | **freeze 时 upsert verified link**（补强-A） | 让 contract-first 自动路径真正生效，不再饿死自动结算 |
| P0/P1 | link policy | 平衡 fail-closed 与自动积累 |
| P1 | **trust→0 吸收态加 recency 窗口**（补强-B） | 防 category 永久锁死在 skip、数学上无法自愈 |
| P1 | calibration surface 命名治理 | 避免指标互相打架 |
| P1 | fallback quality flags | 防坏输入污染 calibration |
| P1 | backfill cursor | 修 24h outage 缺口 |
| P2 | schema version / typed fields | 降低未来迁移成本 |
