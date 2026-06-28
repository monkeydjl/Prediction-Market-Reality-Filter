# Stage 3: 可执行结论 + 冷启动旁路 设计文档

> **For agentic workers:** 本文档为设计 spec，不是实施计划。实施需先由 `superpowers:writing-plans` 转换为任务化计划。

**Goal:** 打破冷启动死循环，让系统在无校准历史时也能发出结构化可执行结论（方向 + 置信度 + 建议仓位），而非卡在 "watch/skip + 模糊短语"。

**Architecture:** 不新建服务层。在现有 `event_intelligence_service.build_event_record()` 中新增 `actionable_recommendation` 字段，从已计算的 `legacy_analysis` 提取信号并映射为结构化结论。冷启动旁路在 `diagnosis_service.decide()` 中实现：dormant 类别 + edge 足够大 → `provisional_act`（而非 `watch`）。

**Tech Stack:** Python 3.11+ / FastAPI / Pydantic / Next.js / TypeScript

---

## 背景与问题诊断

### 现状：两个并行结论面

1. **Surface A — Intelligence Report** (`/discover`, `/analyze`)
   - `intelligence_report.recommended_action` 由 `scoring_service.recommended_action()` 产出
   - 仅 3 句模糊中文短语："建议人工复核，并持续关注后续证据。" / "作为活跃情报项跟踪，等待进一步确认。" / "保持观察；当前证据强度不足以升级处理。"
   - 从不指定方向（YES/NO）、置信度、仓位

2. **Surface B — Decision Gate** (`/decisions/open`, `/{event_id}/decision`)
   - `recommendation.decision` ∈ {`act`, `watch`, `skip`}
   - 这是真正的可执行判定，但冷启动时冻结在 `watch`/`skip`

### 冷启动死循环根源

`diagnosis_service.decide()` (lines 59-74)：

```python
def decide(adjusted_edge, *, qualified, act_edge, watch_edge) -> str:
    magnitude = abs(adjusted_edge)
    if qualified and magnitude >= act_edge:     # act 要求 qualified=True
        return "act"
    if magnitude >= watch_edge:
        return "watch"
    return "skip"
```

- `qualified = segment_n >= min_samples` (line 104)，`min_samples = CALIBRATION_FEEDBACK_MIN_SAMPLES = 8`
- `segment_n` 仅统计 `scored`/`observed` 状态的预测（需市场结算）
- 新系统 `segment_n = 0` → `qualified = False` → 永不 `act`，无论 edge 多大
- 市场结算需数周到数月 → 死循环

### 已计算但埋藏的信号

`ai_analysis_service.analyze_market()` 已返回：
- `signal` ∈ {`STRONG_LONG`, `LONG`, `STRONG_SHORT`, `SHORT`, `WATCHLIST`}
- `signal_direction` ∈ {`LONG`, `SHORT`, `NEUTRAL`}
- `signal_strength` ∈ {`HIGH`, `MEDIUM`, `LOW`}
- `position_size` (0.02-0.25)
- `expected_edge` (= divergence / 100)
- `risk_level`, `risk_flags`

但这些被 `event_intelligence_service.build_event_record()` (line 128) 塞进 `legacy_analysis` 子字段，**事件 UI schema 完全忽略它们**。

### 用户视角症状

- `/discover` 返回的 `recommended_action` 是模糊短语
- `/decisions/open` 每张卡片显示黄色/灰色 "持续观察" / "暂不参与"
- 诊断理由字面是："类别样本不足（0/8 条，未达合格线），暂不行动"
- 这正是 "看不到可执行结论" 的来源

---

## 设计

### 新增字段：`actionable_recommendation`

结构：

```python
{
    "direction": "YES" | "NO" | "AVOID" | "WAIT",
    "confidence": "high" | "medium" | "low",
    "suggested_position_pct": 5.0,    # 0-25, 复用 legacy position_size
    "edge": 12.5,                    # expected_edge, 百分点
    "risk_level": "medium",          # low/medium/high
    "rationale": "市场定价 40%，估计 55%，证据强度高...",
    "calibration_status": "calibrated" | "uncalibrated_provisional"
}
```

**字段语义**：
- `direction=YES`: 估计概率 > 市场价 → 押事件发生
- `direction=NO`: 估计概率 < 市场价 → 押事件不发生
- `direction=AVOID`: risk_flags 多 + confidence 低 → 回避
- `direction=WAIT`: edge 不足 → 等待更多证据
- `calibration_status=calibrated`: 类别已有 ≥8 条已结算样本
- `calibration_status=uncalibrated_provisional`: dormant 类别但 edge 足够 → 临时结论
- 证据质量门未通过 → 整个 `actionable_recommendation = None`（无 calibration_status 字段）

**何时为 None**：
- `signal == WATCHLIST` 且 edge 不足 → 整个字段为 `None`，用户看到 "证据不足，暂无建议"
- 证据质量门（confidence ≥ 0.50, news_quality ≥ 0.40, evidence_strength ≥ 0.20）未通过 → `None`

### 信号映射逻辑

`legacy_analysis.signal` → `actionable_recommendation.direction`：

| legacy signal | signal_direction | direction | 说明 |
|---|---|---|---|
| `STRONG_LONG` | `LONG` | `YES` | 估计 > 市场 → 押发生 |
| `LONG` | `LONG` | `YES` | 同上，confidence 较低 |
| `STRONG_SHORT` | `SHORT` | `NO` | 估计 < 市场 → 押不发生 |
| `SHORT` | `SHORT` | `NO` | 同上，confidence 较低 |
| `WATCHLIST` | `NEUTRAL` | `WAIT` | edge 不足 |

`signal_strength` → `confidence`：HIGH→high, MEDIUM→medium, LOW→low

**`suggested_position_pct` 映射**：`legacy_analysis.position_size`（0.02-0.25 分数）× 100 → `suggested_position_pct`（2.0-25.0 百分比）

**AVOID 触发**：`risk_flags` 数量 ≥ 2 且 `confidence == low` → direction 改为 `AVOID`

### 冷启动旁路

`diagnosis_service.decide()` 修改：

```python
def decide(adjusted_edge, *, qualified, act_edge, watch_edge,
           cold_start_bypass_enabled: bool = True) -> str:
    magnitude = abs(adjusted_edge)
    if magnitude >= act_edge:
        if qualified:
            return "act"
        if cold_start_bypass_enabled:
            return "provisional_act"  # 冷启动: edge 够大但未经校准
        return "watch"  # 旧行为 (flag 关闭时)
    if magnitude >= watch_edge:
        return "watch"
    return "skip"
```

- `provisional_act` 语义：edge 足够大，但未经校准 → 可行动但需谨慎
- `qualified` 判定逻辑不变（仍需 8 条已结算样本），只是不再阻塞行动
- flag `COLD_START_BYPASS_ENABLED` 控制是否启用旁路（默认 true），关闭时回退旧行为

### `recommended_action` 结构化

`scoring_service.recommended_action()` 改造，新增可选 kwargs：

```python
def recommended_action(trust_score: int, impact_score: int, change: float, *,
                       signal_direction: str | None = None,
                       confidence: str | None = None) -> str:
    # 新路径: 有信号时返回结构化方向
    if signal_direction in ("LONG", "STRONG_LONG"):
        return f"押 YES（置信度：{confidence or '未知'}）"
    if signal_direction in ("SHORT", "STRONG_SHORT"):
        return f"押 NO（置信度：{confidence or '未知'}）"
    if signal_direction == "WATCHLIST":
        return "等待更多证据"
    # 旧路径: 无信号时回退到 trust/impact 逻辑
    if trust_score >= 70 and impact_score >= 60:
        return "建议人工复核，并持续关注后续证据。"
    if trust_score >= 45 and abs(change) >= 5:
        return "作为活跃情报项跟踪，等待进一步确认。"
    return "保持观察；当前证据强度不足以升级处理。"
```

向后兼容：`signal_direction` / `confidence` 为可选 kwargs，未传时回退旧逻辑。

### `decision_report_service` 改造

`build_decision_report()` 新增：
- `recommendation.decision` 可能值扩展为 `{act, provisional_act, watch, skip}`
- 新增 `recommendation.calibration_status` 字段：`calibrated` / `uncalibrated_provisional` / `dormant`
- 诊断理由文本调整：dormant + provisional_act 时显示 "未经校准的临时行动建议（类别样本 0/8）" 而非 "暂不行动"

### Pydantic Schema 更新

`backend/app/models/event.py`：

```python
class ActionableRecommendation(BaseModel):
    direction: Literal["YES", "NO", "AVOID", "WAIT"]
    confidence: Literal["high", "medium", "low"]
    suggested_position_pct: float
    edge: float
    risk_level: Literal["low", "medium", "high"]
    rationale: str
    calibration_status: Literal["calibrated", "uncalibrated_provisional"]

class EventRecord(BaseModel):
    # ... existing fields ...
    actionable_recommendation: ActionableRecommendation | None = None
```

### 前端改动

`frontend/src/components/decisions/decision-card.tsx`：

1. **新增 `actionable_recommendation` 区块**：
   - 方向徽章：YES（绿）/ NO（红）/ AVOID（橙）/ WAIT（灰）
   - 置信度标签：high/medium/low
   - 建议仓位百分比
   - rationale 文本（折叠展开）

2. **`provisional_act` 状态**：
   - 蓝色徽章 + "未经校准" 标签
   - 与 `act`（绿色）区分
   - 显示 "类别样本 0/8" 提示

3. **保留现有**：
   - `diagnosis.reason` 文本仍显示
   - 现有 `decision` 徽章保留（act/watch/skip 扩展为 act/provisional_act/watch/skip）

### 配置项

`backend/app/core/config.py` 新增：

```python
ACTIONABLE_RECOMMENDATION_ENABLED: bool = _env_bool("ACTIONABLE_RECOMMENDATION_ENABLED", "true")
COLD_START_BYPASS_ENABLED: bool = _env_bool("COLD_START_BYPASS_ENABLED", "true")
```

- `ACTIONABLE_RECOMMENDATION_ENABLED=false` → `actionable_recommendation` 字段始终为 None
- `COLD_START_BYPASS_ENABLED=false` → 回退旧行为（dormant 永远 watch）

`backend/.env.example` 文档化：

```
# === ACTIONABLE CONCLUSIONS ===
# Structured direction + confidence + position recommendation on event records.
# When true, surfaces the already-computed LONG/SHORT signal as actionable_recommendation.
ACTIONABLE_RECOMMENDATION_ENABLED=true
# Cold-start bypass: when a category is dormant (0 resolved samples) but the
# adjusted edge exceeds act_edge, emit "provisional_act" instead of "watch".
# This unblocks the system during cold-start. Disable to restore old behavior
# (dormant categories never earn "act" regardless of edge).
COLD_START_BYPASS_ENABLED=true
```

---

## 数据流（修改后）

```
ai_analysis_service.analyze_market()
  → 计算: signal, signal_direction, position_size, expected_edge, risk_level
  ↓
event_intelligence_service.build_event_record()
  → legacy_analysis (保留，向后兼容)
  → 新增: actionable_recommendation (从 legacy_analysis 提取 + 映射)
  → 新增: 调用 scoring_service.recommended_action(..., signal_direction=, confidence=)
  ↓
diagnosis_service.decide()
  → qualified=True + edge≥act_edge → "act"
  → qualified=False + edge≥act_edge + bypass → "provisional_act"
  → edge≥watch_edge → "watch"
  → else → "skip"
  ↓
decision_report_service.build_decision_report()
  → recommendation.decision ∈ {act, provisional_act, watch, skip}
  → recommendation.calibration_status
  → diagnosis.reason (调整文案)
  ↓
API response → frontend DecisionCard
  → 显示 actionable_recommendation 区块 + decision 徽章
```

---

## 测试策略

### 后端单元测试

- `test_diagnosis_service.py`：
  - `test_decide_returns_provisional_act_when_dormant_and_edge_large`
  - `test_decide_returns_watch_when_bypass_disabled_and_dormant`
  - `test_decide_returns_act_when_qualified_and_edge_large`（保留现有）
  - `test_decide_returns_skip_when_edge_small`（保留现有）

- `test_event_intelligence_service.py`：
  - `test_actionable_recommendation_yes_for_long_signal`
  - `test_actionable_recommendation_no_for_short_signal`
  - `test_actionable_recommendation_wait_for_watchlist_signal`
  - `test_actionable_recommendation_avoid_for_high_risk_low_confidence`
  - `test_actionable_recommendation_none_when_evidence_quality_gate_fails`
  - `test_actionable_recommendation_none_when_feature_disabled`

- `test_scoring_service.py`：
  - `test_recommended_action_structured_for_long_signal`
  - `test_recommended_action_structured_for_short_signal`
  - `test_recommended_action_falls_back_to_legacy_when_no_signal`

- `test_decision_report_service.py`：
  - `test_calibration_status_calibrated_when_qualified`
  - `test_calibration_status_uncalibrated_provisional_when_dormant`
  - `test_decision_provisional_act_when_dormant_and_bypass_enabled`

### 前端测试

- `decision-card.test.tsx`：
  - 渲染 `actionable_recommendation` 区块（YES/NO/AVOID/WAIT 四种）
  - `provisional_act` 显示蓝色徽章 + "未经校准" 标签
  - `actionable_recommendation=null` 时不显示区块

### 集成测试

- 冷启动场景（segment_n=0）+ 大 edge → `provisional_act` + `uncalibrated_provisional` + 结构化方向
- 校准场景（segment_n≥8）+ 大 edge → `act` + `calibrated` + 结构化方向

---

## 文件清单

### 修改文件（8 个）

| 文件 | 改动 |
|---|---|
| `backend/app/services/diagnosis_service.py` | `decide()` 新增 `provisional_act` 分支 + `cold_start_bypass_enabled` 参数 |
| `backend/app/services/event_intelligence_service.py` | `build_event_record()` 新增 `actionable_recommendation` 字段 + 信号映射逻辑 |
| `backend/app/services/scoring_service.py` | `recommended_action()` 新增 `signal_direction` / `confidence` kwargs |
| `backend/app/services/decision_report_service.py` | `build_decision_report()` 新增 `calibration_status` + 扩展 decision 枚举 |
| `backend/app/models/event.py` | 新增 `ActionableRecommendation` Pydantic 模型 + EventRecord 字段 |
| `backend/app/core/config.py` | 新增 `ACTIONABLE_RECOMMENDATION_ENABLED` + `COLD_START_BYPASS_ENABLED` |
| `backend/.env.example` | 文档化新配置 |
| `frontend/src/components/decisions/decision-card.tsx` | 新增 `actionable_recommendation` 渲染区块 |

### 测试文件（扩展）

- `backend/tests/test_diagnosis_service.py`
- `backend/tests/test_event_intelligence_service.py`
- `backend/tests/test_scoring_service.py`
- `backend/tests/test_decision_report_service.py`
- `frontend/src/components/decisions/decision-card.test.tsx`（如存在）

---

## 不做（Out of Scope）

- **不改 `evidence_scoring_service`**：它已正常工作，且事件流不依赖它
- **不改 `prediction_store` 的 `segment_n` 计算逻辑**：保持 8 样本门，只改 `decide()` 行为
- **不改 `probability_engine_service`**：它已提供概率，结论层在其之上
- **不引入新的 LLM 调用**：复用现有 `ai_analysis_service` 的计算结果
- **不改 `analysis_report_service.calculate_signal()` 的判定阈值**：保持现有 LONG/SHORT 触发逻辑
- **Stage 4（市场参与）**：本设计只产出结论，不自动下单

---

## 向后兼容性

- `actionable_recommendation` 字段默认 `None`，现有消费者不受影响
- `recommended_action()` 新 kwargs 有默认值，未传时回退旧逻辑
- `decide()` 新参数 `cold_start_bypass_enabled=True` 默认启用旁路，可通过 config flag 关闭回退旧行为
- `provisional_act` 是新增 decision 值，现有前端处理 `act/watch/skip`，需扩展（但 `provisional_act` 的前端处理是新增渲染，不破坏现有 act/watch/skip 显示）
- Pydantic schema 新增字段都是 `Optional` / `None` 默认，旧客户端忽略即可

---

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| `provisional_act` 误导用户在冷启动时过度下注 | 蓝色徽章 + "未经校准" 标签 + rationale 明确说明 |
| `actionable_recommendation` 与 `recommended_action` 语义重叠 | `recommended_action` 是简短中文短语（向后兼容），`actionable_recommendation` 是结构化字段（新）。前端优先展示后者，前者降级为 tooltip |
| 前端改动可能破坏现有 DecisionCard | 新增区块用条件渲染（`actionable_recommendation != null` 才显示），不改动现有元素 |
| `position_size` 来自 legacy 逻辑可能不合理 | 复用现有计算，不改逻辑；如需调整是后续优化 |

---

## 验收标准

1. 冷启动（segment_n=0）+ 大 edge 事件 → `decision=provisional_act` + `actionable_recommendation.direction=YES/NO` + `calibration_status=uncalibrated_provisional`
2. 校准（segment_n≥8）+ 大 edge → `decision=act` + `calibration_status=calibrated`
3. 小 edge → `decision=watch/skip` + `actionable_recommendation=WAIT` 或 `None`
4. 高风险 + 低置信度 → `direction=AVOID`
5. `ACTIONABLE_RECOMMENDATION_ENABLED=false` → `actionable_recommendation=None`，`recommended_action` 回退旧逻辑
6. `COLD_START_BYPASS_ENABLED=false` → dormant 永远 `watch`（旧行为）
7. 前端 DecisionCard 正确渲染四种 direction + provisional_act 蓝色徽章
8. 全部测试通过，无回归
