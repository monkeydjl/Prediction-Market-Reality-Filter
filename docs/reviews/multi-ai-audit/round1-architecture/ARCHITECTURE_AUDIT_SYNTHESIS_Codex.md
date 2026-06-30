# 架构审计综合判断报告（四份 AI Agent 审计汇总）

日期：2026-06-19

范围：

- `ARCHITECTURE_AUDIT Claude_Opus4.8.md`
- `ARCHITECTURE_AUDIT Codex_Gpt5.5.md`
- `ARCHITECTURE_AUDIT WorkBuddy_DeepseekV4Pro.md`
- `ARCHITECTURE_AUDIT Zcode_Glm5.2.md`
- 当前工作树代码（未假设这些变更已经提交）

方法：

本报告不重新展开完整代码审计，而是对四份报告的关键判断做交叉归纳，并抽查当前代码验证其是否仍成立。结论按“真实数据闭环风险”“核心架构语义风险”“产品范围/开源清理风险”分层，避免把路线图取舍和实际缺陷混为一谈。

---

## 一、总判定

四份审计报告的主干判断基本可信，但优先级需要重新排序。

当前项目最大问题不是某个单点实现丑陋，而是三类风险叠在一起：

1. **数据闭环可靠性存在真实缺陷**：事件可能被发现、入库、展示，但不能稳定进入 resolve / score / calibration 闭环。
2. **Prediction 语义已经从 Commitment 漂移为 Trajectory / Ledger**：如果项目原则仍是 One Event -> One Prediction，则当前实现已经偏离。
3. **M5 opportunity / decision surface 和 legacy API 同时存在**：这会让产品边界和开源入口变混乱，但它更像产品范围决策，不是所有项都应按 bug 回退。

建议的最高层处理顺序：

1. **先修真实数据闭环缺陷**。
2. **再决定 prediction 是一行 commitment，还是明确接受多版本 ledger**。
3. **然后清理 dead endpoint / premature config / dead link**。
4. **最后做开源瘦身，决定是否删除 legacy market scanner 兼容线**。

---

## 二、四份报告的共识

### 1. 多行 prediction ledger 真实存在

四份报告都指出：当前 `predictions` 表不再是严格的一事件一预测。

已核验：

- `backend/app/memory/prediction_store.py` 的 schema 中 `event_id` 没有唯一约束。
- `_migrate()` 明确检测并删除 legacy `UNIQUE(event_id)`。
- `status='superseded'` 被引入，用来保留旧 open row。
- `freeze_prediction()` 会在 verdict 或关键数值变化时 append 新 prediction。

判断：

这是事实，不是误报。

如果当前路线图原则仍是：

- One Event -> One Prediction
- Prediction is Commitment, not Trajectory
- M3 只做 KPI，不重构账本

那么 Claude / Zcode 关于“回退到一行 Commitment 模型”的判断更稳。

如果团队决定保留多版本 ledger，则不能继续以“隐式多行 + 应用锁 + 注释约定”维持语义，至少要补：

- `UNIQUE(event_id) WHERE status='open'` 部分唯一索引；
- 明确一个 event 如何贡献 trust / calibration sample；
- 明确 superseded 是否永不参与统计；
- schema version 表，替代结构探测式迁移。

### 2. `_materially_changed()` / `PREDICTION_RESNAPSHOT_DELTA` 让 commitment 变成 re-snapshot

已核验：

- `backend/app/memory/prediction_store.py` 存在 `_materially_changed()`。
- `backend/app/core/config.py` 存在 `PREDICTION_RESNAPSHOT_DELTA`。
- 只要同一 verdict 下 `adjusted_edge` 或 `ai_probability` 变化超过阈值，也会 append 新 row。

判断：

这是对 commitment 语义的实质改变。四份报告将其列为 P0/P1 是合理的，但修法取决于上一节的产品决策：

- 若坚持 commitment：删除 re-snapshot，恢复首次冻结即承诺。
- 若接受 ledger：保留也可以，但必须正式建模为版本化预测，而不是继续叫“一次冻结”。

### 3. `get_predictions()` 和 prediction history endpoint 是低价值暴露

已核验：

- 后端存在 `get_predictions(event_id)`。
- `events.py` 暴露 `GET /events/{event_id}/predictions`。
- 前端主路径没有消费该 endpoint。

判断：

这是较可信的 dead / premature API。若回退多行 ledger，应一并删除。若保留 ledger，也建议先隐藏在实验接口，不应作为当前主线 API 暴露。

### 4. diagnosis 字段冻结进 prediction 行属于 schema 泄漏

四份报告都提到 prediction 行中冻结了 diagnosis / decision gate 的解释字段。

判断：

这不是马上会炸的数据 bug，但确实扩大了 commitment 表面。更干净的做法是：

- commitment 行只保留最终决策所需的稳定输入和输出；
- 解释型字段在报告层按需重算或放入独立 report snapshot；
- 若要冻结解释，应把它视为 M5 decision report 的快照，而不是 M1/M3 prediction schema 的核心字段。

---

## 三、我认为应上调优先级的问题

### P0-A：`save_events()` 重扫可能覆盖 resolved outcome / calibration

WorkBuddy / Codex 报告指出此问题，且代码核验后成立。

当前 `save_events()` 只保留已有 `tracking`：

- 读取 `existing = store.get(event_id) or {}`；
- 只提取 `(existing.get("record") or {}).get("tracking")`；
- 新 record 通过校验后直接覆盖 `store[event_id]`。

风险：

已 resolved 的 event 如果被后续 discover / re-scan 以同一 `event_id` 重写，新 record 可能不带 `outcome` / `calibration`，导致：

- resolved 状态回退为 unresolved；
- auto resolve 的幂等保护失效；
- prediction 可能再次 freeze；
- calibration/trust 样本丢失。

判断：

这是实际数据丢失风险，优先级应高于“是否提前做 M5 surface”。

建议：

- `save_events()` 合并时保留已有 `outcome`、`calibration`，必要时也保留人工/系统已确认的 link 元信息；
- 增加测试：resolved event 被同 ID re-scan 后，outcome/calibration 不丢。

### P0-B：auto resolve 仍以 question 文本匹配为主路径

Zcode / Claude / Codex 都指出 resolve 侧未真正消费已落盘的 `contract_id`。核验后，结论基本成立，但需要精确表述：

当前代码不是完全没有 link store，而是：

1. 先对 unresolved local event 的 `event_title` 做 `find_match(question, index)`；
2. 得到 matched market 后提取 `contract_id`；
3. 再 `upsert_link()`；
4. 如果已有 verified link 且 contract_id 不同，才标记 diverged/invalid。

也就是说，`contract_id` 目前是**文本匹配后的校验和记录**，不是 resolve 的主键入口。

风险：

- market question 文本变化，可能永不匹配；
- local event title 与 market question 口径不同，可能永不结算；
- 没有 durable unresolved queue / alert，失败会静默表现为长期无样本；
- dormant category 的毕业依赖 resolved samples，因此会被连带阻塞。

建议：

- auto resolve 先读取 verified event-market link；
- 对已有 `contract_id` 的 event，直接到对应 source 查询 settled outcome；
- 文本匹配只作为发现候选 link 的兜底，不作为已绑定事件的主结算路径；
- unmatched linked events 应输出可观测指标或告警。

### P1-A：`_persist_events()` 一个 try 块吞掉 save/audit/freeze 的边界

四份报告都指出这点。核验成立。

当前 `_persist_events(records)`：

- `save_events(records)`
- `record_event(record)`
- `freeze_prediction(record)`

都在同一个 `try/except` 中，异常只 `logger.warning`。

风险：

- save 成功但 freeze 失败：事件存在，prediction 缺失；
- audit 失败可能阻断后续 freeze；
- 日志只有 generic warning，没有结构化失败计数；
- 下游无法知道“这个事件本应被冻结但没有”。

建议：

- 将 save、audit、freeze 分开处理；
- save 失败应阻断后续并明确记录；
- audit 失败不应阻断 freeze；
- freeze 失败应单独记录 event_id、reason，并纳入健康检查或补偿任务。

---

## 四、我认为应降级或标为产品决策的问题

### 1. M5 Decision / Opportunity Surface

Codex / WorkBuddy 将 M5 surface 列为 P0。代码上确实存在：

- `/events/decisions/open`
- `/events/{event_id}/decision`
- 前端 `/decisions`
- `decision_report_service.py`

但这是否必须回退，取决于路线图解释。

如果团队当前目标是严格按 M0-M3 收敛，那么它是 scope creep，应隐藏或删除。

如果团队已经决定需要一个可用的人工决策界面，那么它可以保留，但必须遵守边界：

- 不反向污染 prediction commitment schema；
- 不要求底层 ledger 为它提前让路；
- API 标记为 experimental 或 M5 preview；
- 测试不要把未来路线图行为固化成当前不变量。

所以我不建议把它和数据闭环缺陷同级处理。

### 2. 默认 discovery 调度闭环

WorkBuddy / Codex 将默认持续 discovery 列为 P0 scope creep。

判断：

它确实扩大了运行面，但如果项目定位已经是“事件情报平台”，定时 discover / resolve 并不天然错误。真正的问题是：

- 错过触发时间没有补跑；
- job 状态不持久；
- 失败没有 durable error queue；
- scheduler 与 event loop 的数据一致性不够强。

建议：

不要先删 scheduler。先补：

- 上次成功运行时间；
- missed run detection；
- 每个 job 的成功/失败计数；
- 可手动补跑入口；
- 启动时对账。

### 3. Legacy scanner / trades / backtest / classic dashboard

四份报告都认为 legacy 线会污染开源边界。这个判断大体正确。

但删除它会改变对外 API 和部分文档入口，因此属于发布策略决策。

建议：

- 短期：在 OpenAPI / README 中标为 legacy；
- 开源前：若目标是清晰展示 Event Intelligence 主线，应整体删除 legacy market scanner 兼容线；
- 删除前：迁移仍被主线使用的 resolved-market fetch、health/summary 依赖。

---

## 五、建议执行路线

### Phase 1：修真实数据闭环

优先级最高，建议先做。

1. `save_events()` 保留已有 `outcome` / `calibration`。
2. `_persist_events()` 拆分 save / audit / freeze 错误边界。
3. auto resolve 改为 contract_id 主路径，文本匹配仅兜底。
4. 增加启动对账：
   - event 已 resolved 但 prediction 仍 open；
   - event 有 verified link 但长期未 resolve；
   - event 已存在 outcome 但 calibration 缺失。

验收标准：

- resolved event 重扫后不会回退为 unresolved；
- freeze 失败可观测，不再只是一条 generic warning；
- verified link 可以不经文本匹配直接结算；
- trust/calibration 样本不会因重扫丢失。

### Phase 2：决策 prediction 语义

必须先做产品/架构选择。

#### 选项 A：严格 Commitment

执行：

- 恢复 `UNIQUE(event_id)`；
- 删除 `_materially_changed()`；
- 删除 `PREDICTION_RESNAPSHOT_DELTA`；
- 删除 `superseded`；
- 删除 `get_predictions()` 和 prediction history endpoint；
- 迁移时先折叠历史多行数据，只保留当前 open 或最终 resolved row。

优点：

- 与当前审计原则最一致；
- trust/calibration 口径简单；
- 回退后系统更容易解释。

代价：

- dormant 期间首次冻结为 watch 的事件，后续不会因为 category 毕业自动变成 act commitment；
- 若想分析轨迹，只能从 audit/history 层读，不从 prediction 表读。

#### 选项 B：正式接受 Ledger

执行：

- 加 `UNIQUE(event_id) WHERE status='open'`；
- 引入 version / current semantics；
- 明确 superseded 不参与 score/trust；
- 明确每个 event 最多贡献一个最终 sample，或定义多 sample 规则；
- 文档中删除“One Event -> One Prediction”的绝对表述。

优点：

- 可保留时间序列决策演化；
- 对 opportunity surface 更友好。

代价：

- 需要补正式版本模型；
- 测试和统计口径复杂度显著上升。

### Phase 3：清理 dead / premature 表面

在 Phase 2 决策后处理：

- 删除或隐藏 `GET /events/{event_id}/predictions`；
- 删除或实验化 fresh edges / opportunity API；
- 处理 diagnosis 解释字段；
- 删除前端 `/dashboard` 死链或恢复对应页面；
- 删除 `signal_service.py` 这类明确废弃且无消费者的小文件。

### Phase 4：开源发布瘦身

建议分两层。

零风险可先做：

- 删除工作垃圾、临时 diff、debug log、备份文件；
- 补 `.gitignore`；
- 清理死链和无消费者的小函数。

需要产品确认后做：

- 删除 legacy scanner routes；
- 删除 trade journal；
- 删除 legacy signal accuracy；
- 删除 classic static dashboard；
- 删除 legacy backtest route；
- 删除或迁移 old calibration/resolve services。

---

## 六、对四份报告的可信度评价

### Claude_Opus4.8

优点：

- 对 commitment vs trajectory 的边界判断清晰；
- 回退建议较克制；
- 没有把所有未来功能都混成同一类缺陷。

不足：

- 对 `save_events()` 覆盖 resolved outcome 的数据丢失风险强调不够。

### Zcode_Glm5.2

优点：

- 对 resolve 文本匹配断点、contract_id 未成为主路径的判断准确；
- 对存储约束和迁移债的分析较实用；
- 优先级比 Claude 更接近真实运行风险。

不足：

- 对 M5 surface 的产品取舍讨论较少。

### Codex_Gpt5.5

优点：

- 覆盖面最广；
- 开源删除路线和 legacy 线梳理较完整；
- 抓到了 `save_events()` outcome/calibration 覆盖风险。

不足：

- 将 M5 surface、默认闭环、ledger 语义、真实数据丢失都列为 P0，优先级偏挤；
- 对“必须删除”和“产品选择”的边界不够清楚。

### WorkBuddy_DeepseekV4Pro

优点：

- 对闭环断点、数据丢失、重复冻结、重复 resolve 的风险展开最充分；
- 对开源瘦身和 legacy API 的复杂度判断有价值。

不足：

- 有些建议偏 aggressive，例如直接删除大块 legacy / agents，需要产品确认；
- 个别项应降级为发布策略，而非架构 P0。

---

## 七、最终结论

我建议采纳四份报告的交集，但按以下优先级执行：

1. **必须先修**：`save_events()` 保留 resolved outcome/calibration；`_persist_events()` 拆错；auto resolve 改 contract_id 主路径。
2. **必须决策**：Prediction 到底是 commitment 还是 ledger。当前处于半承认 ledger、半沿用 commitment 语言的危险状态。
3. **应清理**：`get_predictions()` / prediction history endpoint、`PREDICTION_RESNAPSHOT_DELTA`、`superseded` 或其替代正式版本模型、前端 `/dashboard` 死链。
4. **产品确认后清理**：M5 decision surface 是否隐藏；legacy market scanner 兼容线是否为开源版删除。

一句话：

四份报告不是互相否定，而是分别照亮了不同层面。Claude / Zcode 更适合指导“语义收敛”，Codex / WorkBuddy 更适合指导“闭环可靠性和开源瘦身”。真正的第一刀应落在数据闭环可靠性上，而不是先做大规模删除。
