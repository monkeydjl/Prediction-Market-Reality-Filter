# 质量诊断 CLI 使用文档

`python -m scripts.diagnose_event_quality` — 单事件 6 层质量分解 + 守卫状态 + 最终方向。

## 什么时候用

- **事件方向异常**：最终显示 YES/NO 与直觉不符，想看是哪一层把方向推过去的
- **校准偏差排查**：`direction_correct` 为 False，想确认是推荐错还是结算数据错
- **LMS 降级影响**：怀疑 LLM 降级模式导致方向偏移，用 `--replay` 对比 all-on vs all-off
- **数据源质量回溯**：source_reliability 分数低，想看 source_count / domain_diversity
- **守卫触发审计**：guardrail_fired 列表是否合理

## 前置条件

```bash
cd backend
pip install -r requirements.txt        # 运行时依赖
pip install -r requirements-dev.txt    # 仅测试需要（CLI 本身不依赖）
```

CLI 是**纯只读**的：不写文件、不调 LLM、不联网。只读 `event_store.json`。

## 命令

```bash
# 基本文本输出（默认）
python -m scripts.diagnose_event_quality EVENT_ID

# JSON 输出（便于脚本处理）
python -m scripts.diagnose_event_quality EVENT_ID --json

# 附带 replay 对比（all_on vs all_off 方向变化）
python -m scripts.diagnose_event_quality EVENT_ID --replay

# 组合使用
python -m scripts.diagnose_event_quality EVENT_ID --json --replay
```

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | event_id 在 event_store 中未找到 |
| 2 | 其他错误（加载失败、record 损坏、replay 异常等，详见 stderr） |

## 输出字段解读

### Phase 1: Decision Quality

| 字段 | 含义 |
|------|------|
| `evidence_strength` | 证据强度 0-1，越高越可信 |
| `conflict_score` | 证据冲突度 0-1，越高越矛盾 |
| `downgrade_reason` | 若方向被降级，记录原因（如 `evidence_too_weak`） |
| `displayed_direction` | 决策层最终显示的方向（YES/NO/WAIT/AVOID） |

### Phase 2: Market Quality

| 字段 | 含义 |
|------|------|
| `degraded` | 市场数据是否降级（True 时方向可信度下降） |
| `degrade_reason` | 降级原因（如 `wide_spread`、`low_liquidity`） |
| `wide_spread_flag` | 价差过大标志 |
| `low_liquidity_flag` | 流动性不足标志 |

### Phase 3: Prediction Calibration

| 字段 | 含义 |
|------|------|
| `snapshot_recommendation` | 快照时刻的原始推荐（YES/NO/WAIT/AVOID） |
| `calibration_status` | 校准状态（如 `uncalibrated_provisional`、`calibrated`） |
| `edge_bucket` | edge 分桶：`0-5` / `5-10` / `10-20` / `20+` / `""`（缺失）。使用 `abs(edge)`，半开区间，与校准报表一致 |
| `direction_correct` | 推荐是否匹配结算结果。**True**=YES/NO 推荐匹配 outcome；**False**=不匹配；**None**=未结算或推荐为 WAIT/AVOID（不计分） |

#### direction_correct 语义要点

- **True**：推荐 YES + 结算 outcome≥50（YES），或推荐 NO + 结算 outcome<50（NO）
- **False**：推荐 YES + 结算 outcome<50（NO），或推荐 NO + 结算 outcome≥50（YES）
- **None**：以下情况返回 None，不计入校准样本：
  - 未结算（`outcome` 缺失或 `actual_outcome` 为 None）
  - `outcome.status != "resolved"`（如 `status="invalid"`，只记录 marker，不进入校准）
  - 推荐为 WAIT 或 AVOID（非方向性推荐）

### Phase 4: Source Reliability

| 字段 | 含义 |
|------|------|
| `overall_score` | 数据源综合可信度 0-1 |
| `source_count` | 数据源数量 |
| `domain_diversity` | 域名多样性（独立域名数） |

### Phase 5: LLM Telemetry

| 字段 | 含义 |
|------|------|
| `degraded_mode` | 是否运行在 LLM 降级模式（True 时分析质量下降） |
| `analysis_quality` | 分析质量等级（`llm` / `degraded` / `fallback`） |
| `total_tokens` | 本次分析消耗的 token 总数 |
| `estimated_token_cost` | 估算成本（美元） |

### Phase 6: Execution Quality

| 字段 | 含义 |
|------|------|
| `executable` | 是否可执行（市场流动性足够） |
| `estimated_slippage_pct` | 预估滑点百分比 |
| `stale_price_flag` | 价格是否过期 |
| `max_safe_size` | 最大安全规模（已去词汇锁：原字段名 `max_safe_position_size` 被重命名） |

### Guardrails

| 字段 | 含义 |
|------|------|
| `fired_rules` | 触发的守卫规则列表（如 `["max_edge_exceeded", "source_conflict"]`） |

### Final Direction

最终显示给用户的方向（YES/NO/WAIT/AVOID）。这是所有 6 层 + 守卫综合后的结果。

## --replay 输出

启用 `--replay` 后追加 `[Replay Comparison]` 段：

| 字段 | 含义 |
|------|------|
| `all_on_direction` | 所有 overlay 开启时的方向 |
| `all_off_direction` | 所有 overlay 关闭时的方向 |
| `delta` | `changed`（方向不同）或 `no_change`（方向相同） |

**用途**：`delta=changed` 说明某个 overlay 改变了方向，逐层排查能定位是哪层的影响。`delta=no_change` 说明 overlay 没改变最终方向（可能被守卫或最终方向逻辑覆盖）。

## 常见排查路径

### "最终方向与推荐不符"

1. 看 Phase 1 `displayed_direction` vs `snapshot_recommendation` — 是否在决策层被改
2. 看 Phase 3 `calibration_status` — 是否被校准逻辑调整
3. 看 Guardrails `fired_rules` — 是否有守卫覆盖了推荐
4. 用 `--replay` 看 `all_on` vs `all_off` — 是否某个 overlay 翻转了方向

### "direction_correct 是 False，但推荐看起来对"

1. 确认 `outcome.status` 是否为 `"resolved"`（非 resolved 不应返回 False，应返回 None）
2. 确认 `actual_outcome` 是否正确：≥50 = YES，<50 = NO
3. 确认推荐是 YES/NO 而非 WAIT/AVOID（后者不计分）

### "edge_bucket 显示 20+ 但 edge 看起来不大"

`edge_bucket` 使用 `abs(edge)`，所以负 edge 和正 edge 归同一桶。`edge=-25` → `"20+"`，`edge=25` → `"20+"`。半开区间：`[0,5)` / `[5,10)` / `[10,20)` / `[20,+inf)`。边界值归上桶（`edge=5.0` → `"5-10"`）。

## 相关文档

- 设计 spec：`docs/superpowers/specs/2026-07-02-quality-diagnosis-cli-design.md`
- 实现 plan：`docs/superpowers/plans/2026-07-02-quality-diagnosis-cli.md`
- SDD 过程记录：`backend/.sdd/progress.md`
- 校准服务（复用的纯函数来源）：`backend/app/services/prediction_calibration_service.py`
