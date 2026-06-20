# 全量代码审查意见

日期：2026-06-19

## 结论

这次代码整体比上一轮更完整：M5 路由修复、前端 `/decisions`、预测层校准、diagnosis 展示、act-only scoring、M3 append-on-change ledger 都已经进入实现。全量后端测试和前端生产构建均通过。

但仍有两个需要优先处理的问题：一个会让已判定 invalid 的事件继续出现在机会列表；另一个会让同一 Decision Gate verdict 下的显著 edge 变化无法重新冻结，影响 point-in-time ledger 的真实性。

本次只做审查记录，不修改业务代码。

## 验证结果

已执行：

```text
cd backend
python -m unittest discover
```

结果：通过。共 509 个测试 OK，1 skipped。

已执行：

```text
cd frontend
npm.cmd run build
```

结果：通过。Next.js 生产构建成功，TypeScript 阶段通过。

## 主要发现

### P1：invalid / void outcome 后 prediction 仍保持 open，会继续进入机会列表

位置：

- `backend/app/services/event_resolve_service.py`
- `backend/app/memory/prediction_store.py`

当前 `resolve_with_calibration()` 只有在：

```python
if status == "resolved":
    score_prediction(event_id, actual_outcome)
```

时才会关闭 prediction。非 resolved 状态，例如 identity conflict 写入的 `status="invalid"`，会给 event 写 outcome，但不会更新 prediction。

同时机会列表来自：

```sql
WHERE status='open' AND decision IN (...)
```

这意味着一个已经被判定为 identity conflict / invalid 的事件，仍可能因为 prediction 还是 `open` 而继续出现在 `/events/decisions/open`。

影响：

- 用户会看到已经不应再行动的机会。
- opportunity surface 与 event outcome 状态不一致。
- 后续如果人工处理这些 open prediction，可能造成重复判断或错误操作。

建议：

1. 给 prediction 增加一个关闭非 resolved 事件的路径，例如 `void_prediction()` / `invalidate_prediction()`。
2. `resolve_with_calibration(status!="resolved")` 时，把当前 open prediction 置为 `invalid` / `void` / `observed_invalid` 等终态。
3. `list_open_opportunities()` 继续只读取 `status='open'`，这样 invalid/void 自动排除。
4. 增加测试：identity conflict 后 event outcome 为 invalid，prediction 不再出现在 open opportunities。

### P1：append-on-change 只看 decision，显著 edge 变化不会重新冻结

位置：

- `backend/app/memory/prediction_store.py`

当前 M3 ledger 的 append 条件是：

```python
if open_row is not None and open_row["decision"] == prediction.decision:
    return get_prediction(event_id)
```

也就是说，只要 Decision Gate verdict 没变，就不会 append 新 prediction。

问题场景：

- 第一次：watch，adjusted edge = 4.5pt
- 第二次：watch，adjusted edge = 20pt
- 因为仍然是 watch，不会新建 row
- 后续机会列表和结算使用的仍是旧概率、旧 market price、旧 edge

这和文档中 “point-in-time prediction ledger / probability and edge trajectories” 的目标不完全一致。当前实现更像 “verdict-change ledger”，不是完整的 prediction snapshot ledger。

影响：

- 同一 verdict 内的重大概率变化无法进入 prediction ledger。
- `list_open_opportunities()` 的排序可能使用过时 adjusted edge。
- 结算时 Brier 使用旧 AI probability，不代表最新一次系统判断。
- “fresh / decaying edge” 分析依赖 audit snapshots，但 decision surface 使用 prediction row，两者可能分叉。

建议：

至少把 append 条件扩展为：

- decision 改变时 append；
- adjusted_edge 变化超过阈值时 append；
- ai_probability 或 market_probability 变化超过阈值时 append；
- contract_id 改变时 append；
- 或超过一定时间窗口后 append。

如果暂时不做完整 snapshot ledger，应在文档里明确当前是 “append on material change / verdict-change ledger”，不要称为完整 append-only point-in-time prediction history。

### P2：Prediction 模型注释仍是 M1 旧语义

位置：

- `backend/app/models/event.py`

`Prediction` 的 docstring 仍写：

- M1 simplified ledger
- one row per event
- `UNIQUE(event_id)`
- status `open | scored`
- append-only multi-row history 是 M3

但当前实现已经：

- 去掉 `event_id UNIQUE`
- 支持同一 event 多条 prediction
- 支持 `superseded`
- watch/skip resolve 后进入 `observed`
- 新增 diagnosis explanation 字段

影响不在运行时，而在维护风险：后续开发者读模型注释会得到错误语义，容易按旧结构改坏当前实现。

建议：

更新 `Prediction` docstring，使其与当前实现一致：

- multi-row per event；
- at most one open prediction per event；
- terminal statuses 包含 `scored` / `observed` / `superseded`，以及未来可能的 invalid/void；
- diagnosis 字段是冻结于 decision time 的解释输入。

## 已确认正常的部分

### M5 路由修复已成立

`DecisionCard` 已使用：

```tsx
href={`/events?id=${encodeURIComponent(report.event_id)}`}
```

与 `frontend/src/app/events/page.tsx` 读取 `id` query param 的实现一致。

### act-only scoring 仍成立

`score_prediction()` 仍保持：

```python
new_status = "scored" if row["decision"] == "act" else "observed"
```

`calibration_summary()` 显式过滤：

```sql
WHERE status='scored' AND decision='act'
```

watch / skip 不会污染 headline prediction calibration。

### 双口径指标已经文档化

`DATABASE_DESIGN.md` 和 `V2_ROADMAP.md` 已明确：

- headline prediction calibration：act-only；
- trust qualification / `segment_skill`：act + watch，排除 skip。

这个口径目前实现和文档一致。

### diagnosis 解释已进入报告与前端

`decision_report_service.py` 已输出：

- `qualified`
- `segment_n`
- `segment_skill`
- `liquidity_factor`
- `reason`

前端 `DecisionCard` 已展示诊断原因、类别样本数和流动性因子。这解决了上一轮“为什么是 watch 而不是 act”解释不足的问题。

### M3 migration 有测试覆盖

`test_prediction_store.py` 已覆盖：

- legacy `UNIQUE(event_id)` schema migration；
- migration 后允许同一 event 插入第二条 prediction；
- superseded row 不进入 `segment_skill`；
- prediction ledger 按 oldest-first 返回。

## 测试缺口

建议补以下测试：

1. `resolve_with_calibration(status="invalid")` 后，open prediction 被关闭，`list_open_opportunities()` 不再返回该 event。
2. 同一 decision 下 adjusted edge 大幅变化时，应 append 新 prediction，旧 row superseded。
3. `/events/resolve/auto` 的路由测试，确认 POST 静态路由不会被动态路由行为影响。
4. `/events/decisions/open?decision=bad` 返回 422。
5. 前端 `/decisions` 卡片链接保持 `/events?id=...` 的组件级测试或快照测试。

## 建议处理顺序

1. 先修 invalid / void outcome 后 prediction 仍 open 的问题。
2. 再决定 M3 ledger 的 append 策略：完整 snapshot、material-change append，或明确降低文档承诺。
3. 更新 `Prediction` 模型注释。
4. 补上述回归测试。

## 总体判断

当前主路径可运行，测试和构建都通过；M5 的核心闭环已经比较稳。真正需要关注的是“状态一致性”和“冻结语义真实性”：invalid event 不应继续出现在机会面；显著变化不应被旧 frozen row 遮蔽。

处理完这两点后，当前版本可以作为 M3/M5 的更可靠基线。
