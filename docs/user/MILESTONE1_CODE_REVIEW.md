# Milestone 1 代码审查意见

日期：2026-06-18

范围：
- Milestone 0 / 1 相关后端实现
- 重点审查 `event_market_links`、`predictions`、`auto_resolve`、事件闭环入口
- 本文只针对当前代码状态，不讨论证据因子细化

---

## 一、执行结论

当前这版代码**方向是对的**，而且已经实现了一个最薄的闭环：

`analyzed event -> freeze prediction -> resolve outcome -> score prediction -> calibration summary`

这说明 Milestone 1 不是空壳，系统已经开始把“冻结时的预测”与“后来的真实结果”连接起来。

但从 V2 文档定义来看，这一版**还不能算完全对齐设计目标**。主要原因有两类：

1. `predictions` 现在是“单事件单行、后续 update”的实现，不是文档要求的 **append-only point-in-time ledger**
2. `Manifold` / `Kalshi` 的 resolved market 返回数据缺少稳定 `contract_id`，导致 M0 的 identity gate 在跨平台场景下并不完整

所以更准确的定性是：

- **M0：主方向正确，但 identity integrity 还没有完全站稳**
- **M1：闭环已经跑通，但还是简化闭环，不是最终 V2 账本**

---

## 二、这版代码已经做对的地方

### 1. 已经建立了独立的 prediction ledger

新增 `backend/app/memory/prediction_store.py`，把 committed prediction 从 JSON 当前态里分离出来，改为 SQLite 持久化。

这一步是对的，因为它开始区分：

- 事件当前展示状态
- 预测冻结时的点位

这是 V2 的必要前提。

### 2. 冻结入口已经接进事件持久化链路

`backend/app/services/event_intelligence_service.py` 在 `_persist_events()` 中调用 `freeze_prediction(record)`。

这意味着市场来源事件在被保存时，会同步冻结一条 point-in-time prediction。闭环不再只是“分析完显示一下”，而是开始留下后续可评分的历史点。

### 3. resolve 时已经开始给 frozen prediction 打分

`backend/app/services/event_resolve_service.py` 在 `resolve_with_calibration()` 中调用 `score_prediction(event_id, actual_outcome)`。

这一步非常关键。它说明：

- 评分不再依赖当前重新计算的概率
- 而是依赖先前冻结的 prediction

这符合闭环最核心的诚实性要求。

### 4. identity fail-closed 思路已经建立

`backend/app/memory/event_market_link_store.py` 的设计方向是正确的：

- `event_market_links` 独立表
- `verified` 标记
- `get_verified_link()` 作为可评分入口
- fuzzy match 进入 pending，不直接评分

这套思路和 V2 文档是一致的。

### 5. invalid outcome 不会进入 prediction calibration

`resolve_with_calibration()` 在 `status != "resolved"` 时不会调用 prediction scoring。

这意味着 identity conflict 产生的 `invalid` 结果不会污染预测校准指标，这一点是对的。

### 6. 测试对主路径已有覆盖

当前测试已经覆盖了：

- prediction freeze
- freeze 幂等
- prediction scoring
- calibration summary
- fuzzy match pending
- exact match verified
- divergent link -> invalid

这说明 Milestone 1 不是无测试落地。

---

## 三、主要问题与风险

## 1. `predictions` 不是 append-only，实现与文档定义冲突

这是当前最重要的问题。

### 当前实现

`backend/app/memory/prediction_store.py`

- schema 中 `event_id TEXT NOT NULL UNIQUE`
- `freeze_prediction()` 使用 `ON CONFLICT(event_id) DO NOTHING`
- `score_prediction()` 使用 `UPDATE predictions SET status='scored', actual_outcome=?, brier_score=?, resolved_at=?`

这意味着当前数据模型是：

- 一个 event 只允许一条 prediction
- 这条 prediction 在 resolve 后被原地更新

### 与文档的冲突

V2 文档要求的是：

- prediction 是 append-only
- committed prediction 永不修改
- 未来可以随着时间重新评估，一个 event 会有多条 prediction

当前实现不满足这三点。

### 为什么这是结构性问题

Milestone 1 勉强可用，但到了后面会立刻受限：

1. 无法支持同一事件多次重新评估
2. 无法表示不同时间点的 committed decisions
3. outcome / score 是回写到原 prediction 行，不是独立结果事实
4. 后续做 temporal dimension 时，要么大改 schema，要么继续在错误模型上堆功能

### 我的判断

这版 `predictions` 更像：

- “M1 最薄 frozen-prediction ledger”

而不是：

- “V2 最终 append-only prediction history”

这个差别必须在文档和命名上讲清楚，否则团队会误以为底层账本已经定型。

---

## 2. Manifold / Kalshi 缺少 resolved contract identity，identity gate 不完整

这是第二个实质性问题。

### 当前实现

`backend/app/services/event_resolve_service.py` 中：

- auto resolve 通过 `market.get("id") or market.get("contract_id")` 取 `contract_id`

但：

- `backend/app/services/manifold_event_source.py` 的 `fetch_resolved_markets()` 只返回 `{question, actual_outcome}`
- `backend/app/services/kalshi_event_source.py` 的 `fetch_resolved_markets()` 也只返回 `{question, actual_outcome}`

因此在这两条 source 上：

- `contract_id` 为空

### 直接后果

这会导致：

1. link 可以被写入，但 identity 不完整
2. `diverged` 检查对空 contract 基本失效
3. 无法严格确认“这次 resolved market 是否就是之前冻结预测时对应的那一份合约”
4. M0 文档里的“same event_id, same contract_id, same outcome meaning”在这两条源上并没有真正成立

### 为什么这不是小问题

M0 的核心不是“能匹配上 question 就算完成”，而是：

- 后续评分必须能证明是对同一个市场对象打分

如果 source 只返回 question + outcome，没有稳定 contract identity，那么 fail-closed 只做了一半。

Polymarket 这条链路相对完整，因为有 `id`。Manifold / Kalshi 目前不完整。

---

## 3. 当前的“commit”语义过宽，还不是 Decision Gate

### 当前实现

`event_intelligence_service._persist_events()` 中，只要记录是 market-derived event，就会自动 `freeze_prediction(record)`。

这意味着当前系统的实际规则是：

- 只要来自 prediction market 来源
- 只要有 `source_id`
- 只要有 baseline / estimated

就自动被视为 committed prediction

### 问题在哪里

V2 文档中的 Decision Gate 语义更强：

- 不是所有分析结果都进入闭环
- 只有真正决定要 commit 的预测才进入 tracking / calibration
- 后续还会有 act / watch / skip

当前实现还没有这个层次。

### 影响

这会导致“committed prediction”一词被提前使用，造成语义膨胀。

现在更准确的说法应该是：

- “market-derived tracked prediction”

而不是严格意义上的：

- “Decision Gate committed prediction”

这不是说当前实现错了，而是要明确它只是 M1 的简化门槛，不是最终决策门。

---

## 4. 现在有两套 calibration 口径，容易让人误读

当前至少有两套相关指标：

1. 事件层 calibration  
   基于事件记录上的 `record.calibration`

2. prediction 层 calibration  
   基于 `prediction_store.calibration_summary()`

### 风险

如果前端或文档没有明确区分，用户会误以为：

- 系统已经有统一、唯一的 calibration 指标

但实际上不是：

- 事件层校准评的是事件当前概率轨迹
- prediction 层校准评的是冻结过的 market-derived predictions

在 M1 阶段并存是可以接受的，但必须清晰标注用途，不然指标解释会变得混乱。

---

## 5. `prediction_store` 的表名和行为容易让后续重构成本上升

虽然这不是立即 bug，但值得尽早判断。

当前 `predictions` 表同时承担了：

- frozen snapshot
- resolution status
- scored result
- calibration input

这会让后面拆分出：

- `market_snapshots`
- `predictions`
- `outcomes`

时，需要做 schema 迁移和语义拆解。

如果团队已经明确 M1 只是临时简化模型，那问题不大；如果把它当成长期结构，后面会越来越难改。

---

## 四、不是 bug，但需要明确的地方

### 1. 当前实现更像“最薄闭环”而不是“完整 V2 闭环”

这是合理的 Milestone 策略，但必须在内部口径上明确。

建议描述为：

- M1 已跑通 freeze -> resolve -> score -> summary
- 但 append-only history、独立 outcomes、真正 Decision Gate 仍未完成

### 2. `market_snapshots` 仍未独立成表

现在 liquidity / volume / market price 仍然是 folded inline 到 prediction 行里。

这对 M1 是可接受的，但不应误判为 `market_snapshots` 已完成。

### 3. `outcomes` 仍不是独立事实表

现在 outcome 主要还是挂在 event record 上，同时 prediction 行会被 update 填入 `actual_outcome` 和 `brier_score`。

这也说明：

- outcome tracking 已经开始
- 但 outcome persistence 还不是 V2 目标形态

---

## 五、测试覆盖评价

### 已覆盖的关键路径

这部分是值得肯定的：

1. 冻结逻辑
2. freeze 幂等
3. score 幂等
4. calibration summary
5. fuzzy link pending
6. exact match verified
7. divergent link -> invalid
8. invalid outcome 不给 frozen prediction 打分

### 仍缺的测试

当前最应该补的测试有三类：

1. **Manifold resolved result 无 contract id**
   - 断言 auto-resolve 后 link 的 `contract_id` 为空
   - 断言这类场景会削弱 diverged detection

2. **Kalshi resolved result 无 contract id**
   - 同上

3. **多次 re-evaluation 的建模限制**
   - 同一个 event 再次 freeze 时无法追加第二条 prediction
   - 用测试把“这是当前 M1 的有意限制”明确下来

否则后续团队可能会误以为 append-only 已经实现，只是还没用上。

---

## 六、建议的判断口径

如果要给这次 Milestone 1 一个准确评价，我建议这样表述：

### 可以说已经完成的

- 已建立 prediction ledger
- 已建立最薄 frozen prediction 闭环
- 已接通 resolve -> score -> calibration summary
- 已把 event-market linking 引入自动 resolve 路径

### 不能说已经完成的

- 不能说 prediction history 已经 append-only
- 不能说 identity integrity 已经在所有 market source 上成立
- 不能说 Decision Gate 已经实现
- 不能说 `market_snapshots` / `outcomes` / `calibration_metrics` 已按 V2 目标建模完成

---

## 七、我建议的下一步优先级

这里不是让系统回头大重写，而是建议按风险顺序继续推进。

### 优先级 1：补全 resolved market 的稳定 contract identity

先解决：

- Manifold resolved API 返回时带上 market id
- Kalshi resolved API 返回时带上 market / ticker / contract identity

目标不是美化代码，而是让 M0 的 identity gate 真正成立。

如果这一步不补，prediction scoring 的可信度在跨平台上会一直打折。

### 优先级 2：明确 `predictions` 当前是 M1 简化账本

建议在文档和内部沟通中明确：

- 当前 `predictions` 不是最终 append-only 历史模型
- 它是 M1 为闭环验证服务的简化实现

这个认知很重要。否则后面做 M2/M3 时，团队容易在错误前提上继续加功能。

### 优先级 3：决定后续如何升级 prediction persistence

后续需要尽快决定：

1. 是继续演进当前 `predictions` 表
2. 还是在 M2/M3 引入真正的 append-only `predictions` + 独立 `outcomes`

这个决定越晚做，迁移成本越高。

### 优先级 4：保持 Decision Gate 与 freeze 语义分离

建议后续不要把“市场来源自动 freeze”直接等同于“最终决策已 commit”。

更稳妥的方式是：

- M1：自动 freeze，验证闭环
- M2：再引入 act/watch/skip 或 adjusted edge + trust gate

这样能保持路线清晰，不会把 Milestone 1 的临时策略误当成最终架构。

---

## 八、最终判断

这次 Milestone 1 不是“做坏了”，而是：

- **做出了一个有效的最薄闭环**
- **但底层数据模型还没有完全对齐 V2 的长期形态**

我认为最值得肯定的是：

- 它已经把“冻结时的预测”和“后来的真实结果”连上了

我认为最需要警惕的是：

- 团队如果把当前 `predictions` 表误认为最终 append-only 账本，会在后续阶段积累结构债

一句话总结：

> Milestone 1 已经证明闭环可以跑通，但还没有证明这套数据模型能支撑 V2 的时间维度、严格 identity integrity 和真正的 Decision Gate。

