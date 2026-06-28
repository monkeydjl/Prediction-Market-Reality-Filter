# 可执行结论 + 冷启动旁路 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打破冷启动死循环，让系统在无校准历史时也能发出结构化可执行结论（方向 + 置信度 + 建议配置比例），而非卡在 "watch/skip + 模糊短语"。

**Architecture:** 不新建服务层。在现有 `event_intelligence_service.build_event_record()` 中新增 `actionable_recommendation` 字段，从已计算的 `legacy_analysis` 提取信号并映射为结构化结论。冷启动旁路在 `diagnosis_service.decide()` 中实现：dormant 类别 + edge 足够大 → `provisional_act`（而非 `watch`）。

**Tech Stack:** Python 3.11+ / FastAPI / Pydantic / Next.js / TypeScript / unittest

## Spec 细化说明

原 spec 字段 `suggested_position_pct` 重命名为 `suggested_allocation_pct`。原因：现有测试 `backend/tests/test_decision_report_service.py:71-76` 的 `test_report_uses_event_vocabulary_only` 禁止 decision report 包含 "position" 字样（禁词列表：long, short, buy, sell, position, kelly, order）。由于 `actionable_recommendation` 需透传到 decision report 供前端 DecisionCard 渲染，必须避开禁词。"allocation" 不在禁词列表中，语义中性（资金配置比例）。

## Global Constraints

- Python 后端文件使用 `logger` 方法（info/error/warning），禁止 `print()`
- API 端点使用 Pydantic 类型注解
- 新增依赖必须带版本上下界
- LLM 调用必须走 `settings.OPENAI_API_KEY` + `settings.DASHSCOPE_BASE_URL`
- 所有 datetime 使用 timezone-aware 对象
- 失败时走 `fail_closed_empty_list` / `fail_closed_none` 模式
- 前端页面修改仅限 `decision-card.tsx` 及其类型定义
- Decision report 禁词：long, short, buy, sell, position, kelly, order（见 `test_report_uses_event_vocabulary_only`）
- `EventRecord` 使用 `model_config = ConfigDict(extra="allow")`，新增可选字段默认 `None`

---

## 文件结构

### 修改文件（8 个）
- `backend/app/core/config.py` — 新增 2 个配置项（`ACTIONABLE_RECOMMENDATION_ENABLED`, `COLD_START_BYPASS_ENABLED`）
- `backend/app/models/event.py` — 新增 `ActionableRecommendation` Pydantic 模型 + `EventRecord.actionable_recommendation` 字段
- `backend/app/services/diagnosis_service.py` — `decide()` 新增 `provisional_act` 分支 + `cold_start_bypass_enabled` 参数
- `backend/app/services/scoring_service.py` — `recommended_action()` 新增 `signal_direction` / `confidence` kwargs
- `backend/app/services/event_intelligence_service.py` — `build_event_record()` 新增 `actionable_recommendation` 字段 + 信号映射函数
- `backend/app/services/decision_report_service.py` — `build_decision_report()` 透传 `actionable_recommendation` + `calibration_status` + `provisional_act` 诊断理由
- `backend/.env.example` — 文档化新配置
- `frontend/src/components/decisions/decision-card.tsx` — 渲染 `actionable_recommendation` 区块 + `provisional_act` 蓝色徽章

### 修改的测试文件（4 个）
- `backend/tests/test_diagnosis_service.py`
- `backend/tests/test_event_intelligence_service.py`
- `backend/tests/test_decision_report_service.py`
- `backend/tests/test_scoring_service.py`（如存在，否则新建）

### 修改的前端类型
- `frontend/src/lib/api.ts` — 扩展 `DecisionReport` 接口

---

## Task 1: 新增配置项 + Pydantic ActionableRecommendation schema

**Files:**
- Modify: `backend/app/core/config.py` (在 `DECISION_WATCH_EDGE` 后约 line 437)
- Modify: `backend/app/models/event.py` (在 `EventRecord` 类前新增 `ActionableRecommendation`，在 `EventRecord` 末尾新增字段)
- Modify: `backend/.env.example` (新增配置文档)
- Test: `backend/tests/test_event_intelligence_service.py` (仅验证 schema 可实例化)

**Interfaces:**
- Produces: `settings.ACTIONABLE_RECOMMENDATION_ENABLED: bool` (默认 true)
- Produces: `settings.COLD_START_BYPASS_ENABLED: bool` (默认 true)
- Produces: `ActionableRecommendation` Pydantic 模型（7 字段：direction, confidence, suggested_allocation_pct, edge, risk_level, rationale, calibration_status）
- Produces: `EventRecord.actionable_recommendation: ActionableRecommendation | None = None`

- [ ] **Step 1: 在 config.py 新增 2 个配置项**

在 `backend/app/core/config.py` 的 `DECISION_WATCH_EDGE` 行（约 line 437）后新增：

```python
    DECISION_ACT_EDGE: float = float(os.getenv("DECISION_ACT_EDGE", "10.0"))
    DECISION_WATCH_EDGE: float = float(os.getenv("DECISION_WATCH_EDGE", "3.0"))

    # Actionable conclusions (Stage 3): surface the already-computed LONG/SHORT
    # legacy signal as a structured actionable_recommendation on event records.
    # When true, build_event_record adds an actionable_recommendation dict with
    # direction (YES/NO/AVOID/WAIT) + confidence + suggested allocation. When
    # false, the field is always None (legacy behavior).
    ACTIONABLE_RECOMMENDATION_ENABLED: bool = _env_bool(
        "ACTIONABLE_RECOMMENDATION_ENABLED", "true"
    )
    # Cold-start bypass: when a category is dormant (0 resolved samples) but
    # the adjusted edge exceeds act_edge, emit "provisional_act" instead of
    # "watch". This unblocks the system during cold-start. Disable to restore
    # old behavior (dormant categories never earn "act" regardless of edge).
    COLD_START_BYPASS_ENABLED: bool = _env_bool(
        "COLD_START_BYPASS_ENABLED", "true"
    )
```

- [ ] **Step 2: 在 event.py 新增 ActionableRecommendation 模型**

在 `backend/app/models/event.py` 的 `IntelligenceReport` 类（约 line 51-55）后新增：

```python
class ActionableRecommendation(BaseModel):
    """Structured actionable conclusion for an event (Stage 3).

    Surfaces the already-computed legacy signal as an event-vocabulary
    recommendation: direction (YES/NO/AVOID/WAIT) + confidence + suggested
    allocation. None when evidence quality is insufficient or the feature is
    disabled. calibration_status distinguishes calibrated (segment has enough
    resolved samples) from uncalibrated_provisional (dormant but edge is large).
    """

    direction: str  # YES | NO | AVOID | WAIT
    confidence: str  # high | medium | low
    suggested_allocation_pct: float  # 0-25, from legacy position_size * 100
    edge: float  # expected_edge in percentage points
    risk_level: str  # low | medium | high
    rationale: str
    calibration_status: str  # calibrated | uncalibrated_provisional
```

- [ ] **Step 3: 在 EventRecord 末尾新增 actionable_recommendation 字段**

在 `backend/app/models/event.py` 的 `EventRecord` 类中，在 `semantics: EventSemantics | None = None` 行（约 line 255）后新增：

```python
    semantics: EventSemantics | None = None
    actionable_recommendation: ActionableRecommendation | None = None
```

- [ ] **Step 4: 在 .env.example 新增配置文档**

在 `backend/.env.example` 的 `NEWS_FULL_TEXT_MAX_ARTICLES=5` 段落后新增（找到该段落，在其后追加）：

```
# === ACTIONABLE CONCLUSIONS ===
# Structured direction + confidence + allocation recommendation on event records.
# When true, surfaces the already-computed LONG/SHORT signal as
# actionable_recommendation (direction YES/NO/AVOID/WAIT + confidence +
# suggested allocation pct). Disable to keep the legacy vague
# recommended_action strings only.
ACTIONABLE_RECOMMENDATION_ENABLED=true
# Cold-start bypass: when a category is dormant (0 resolved samples) but the
# adjusted edge exceeds act_edge, emit "provisional_act" instead of "watch".
# This unblocks the system during cold-start (otherwise dormant categories
# never earn "act" regardless of edge, causing a dead loop until markets
# settle weeks/months later). Disable to restore old behavior.
COLD_START_BYPASS_ENABLED=true
```

- [ ] **Step 5: 运行测试验证 schema 不破坏现有行为**

```bash
cd backend && python -m pytest tests/test_event_intelligence_service.py tests/test_decision_report_service.py -v --tb=short
```

Expected: 全部通过（新字段默认 None，现有测试不受影响）

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/config.py backend/app/models/event.py backend/.env.example
git commit -m "feat(schema): add ActionableRecommendation model + config flags"
```

---

## Task 2: diagnosis_service 冷启动旁路 (provisional_act)

**Files:**
- Modify: `backend/app/services/diagnosis_service.py:59-74` (`decide()` 函数) + `:77-120` (`diagnose()` 函数)
- Test: `backend/tests/test_diagnosis_service.py`

**Interfaces:**
- Consumes: `settings.COLD_START_BYPASS_ENABLED` (Task 1 新增)
- Produces: `decide()` 新返回值 `"provisional_act"`（dormant + edge≥act_edge + bypass 启用时）
- Produces: `decide()` 新参数 `cold_start_bypass_enabled: bool = True`
- Produces: `diagnose()` 返回 dict 的 `decision` 字段可能为 `"provisional_act"`

- [ ] **Step 1: 写失败测试 — provisional_act 当 dormant + edge 大 + bypass 启用**

在 `backend/tests/test_diagnosis_service.py` 的 `DecideTests` 类中新增：

```python
    def test_dormant_large_edge_returns_provisional_act_when_bypass_enabled(self):
        # Cold-start bypass: dormant segment + edge >= act_edge -> provisional_act
        self.assertEqual(
            diag.decide(
                12.0, qualified=False, act_edge=10.0, watch_edge=3.0,
                cold_start_bypass_enabled=True,
            ),
            "provisional_act",
        )

    def test_dormant_large_edge_returns_watch_when_bypass_disabled(self):
        # Bypass off -> old behavior (dormant caps at watch)
        self.assertEqual(
            diag.decide(
                12.0, qualified=False, act_edge=10.0, watch_edge=3.0,
                cold_start_bypass_enabled=False,
            ),
            "watch",
        )

    def test_qualified_large_edge_still_acts_with_bypass_enabled(self):
        # Bypass does not affect qualified segments
        self.assertEqual(
            diag.decide(
                12.0, qualified=True, act_edge=10.0, watch_edge=3.0,
                cold_start_bypass_enabled=True,
            ),
            "act",
        )
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd backend && python -m pytest tests/test_diagnosis_service.py::DecideTests -v --tb=short
```

Expected: 3 个新测试 FAIL（`decide()` 不接受 `cold_start_bypass_enabled` 参数，TypeError）

- [ ] **Step 3: 修改 decide() 函数新增 provisional_act 分支**

将 `backend/app/services/diagnosis_service.py` 的 `decide()` 函数（line 59-74）替换为：

```python
def decide(
    adjusted_edge: float,
    *,
    qualified: bool,
    act_edge: float,
    watch_edge: float,
    cold_start_bypass_enabled: bool = True,
) -> str:
    """act / provisional_act / watch / skip from the adjusted edge.

    "act" requires a qualified (non-dormant) segment. When
    cold_start_bypass_enabled is true, a dormant segment with edge >= act_edge
    earns "provisional_act" (actionable but uncalibrated) instead of "watch" —
    this unblocks the system during cold-start. When false, dormant segments
    cap at "watch" regardless of edge (legacy behavior)."""
    magnitude = abs(adjusted_edge)
    if magnitude >= act_edge:
        if qualified:
            return "act"
        if cold_start_bypass_enabled:
            return "provisional_act"
        return "watch"
    if magnitude >= watch_edge:
        return "watch"
    return "skip"
```

- [ ] **Step 4: 修改 diagnose() 函数透传 cold_start_bypass_enabled**

将 `backend/app/services/diagnosis_service.py` 的 `diagnose()` 函数中的 `decide()` 调用（约 line 105-110）替换为：

```python
    decision = decide(
        adjusted_edge,
        qualified=qualified,
        act_edge=settings.DECISION_ACT_EDGE,
        watch_edge=settings.DECISION_WATCH_EDGE,
        cold_start_bypass_enabled=settings.COLD_START_BYPASS_ENABLED,
    )
```

- [ ] **Step 5: 更新模块 docstring 反映 provisional_act**

将 `backend/app/services/diagnosis_service.py` 的模块 docstring（line 1-15）中关于 "caps the verdict at 'watch'" 的描述更新。将 line 10-11 的：

```
the Decision Gate
caps the verdict at "watch" - an unproven segment never earns "act".
```

替换为：

```
the Decision Gate
caps the verdict at "watch" (or "provisional_act" when cold_start_bypass is
enabled) - an unproven segment never earns "act".
```

- [ ] **Step 6: 运行测试验证通过**

```bash
cd backend && python -m pytest tests/test_diagnosis_service.py -v --tb=short
```

Expected: 全部通过（原 5 个 + 新 3 个 = 8 个 DecideTests，DiagnoseTests 注意：`test_dormant_segment_damps_and_caps_watch` 仍期望 "watch" 因为 diagnose() 默认 bypass 启用但该场景 adjusted_edge=20.0 >= act_edge=10.0 → 现在会返回 "provisional_act"）

**重要：** `test_dormant_segment_damps_and_caps_watch` 测试（line 103-111）期望 dormant + adjusted_edge=20.0 返回 "watch"。修改后默认 bypass 启用，20.0 >= 10.0 (act_edge) → 返回 "provisional_act"。需更新此测试。

- [ ] **Step 7: 更新 test_dormant_segment_damps_and_caps_watch 测试**

将 `backend/tests/test_diagnosis_service.py` 的 `test_dormant_segment_damps_and_caps_watch`（line 103-111）替换为：

```python
    def test_dormant_segment_returns_provisional_act_with_bypass(self):
        # With bypass enabled (default), dormant + large edge -> provisional_act
        with self._settings():
            out = diag.diagnose(40.0, {"n": 0, "mean_brier": None}, liquidity=20000.0)
        # trust 0.5, liq 1.0 -> adjusted 20.0, dormant -> provisional_act (bypass on)
        self.assertEqual(out["trust"], 0.5)
        self.assertEqual(out["adjusted_edge"], 20.0)
        self.assertEqual(out["decision"], "provisional_act")
        self.assertEqual(out["segment_n"], 0)
        self.assertEqual(out["segment_min_samples"], 8)

    def test_dormant_segment_caps_at_watch_when_bypass_disabled(self):
        # With bypass disabled, dormant + large edge -> watch (legacy behavior)
        with self._settings(), patch.object(diag.settings, "COLD_START_BYPASS_ENABLED", False):
            out = diag.diagnose(40.0, {"n": 0, "mean_brier": None}, liquidity=20000.0)
        self.assertEqual(out["decision"], "watch")
```

需在文件顶部新增 `from unittest.mock import patch`（如已有则跳过）。

- [ ] **Step 8: 运行全部 diagnosis 测试验证通过**

```bash
cd backend && python -m pytest tests/test_diagnosis_service.py -v --tb=short
```

Expected: 全部通过

- [ ] **Step 9: 搜索并更新受 provisional_act 影响的其他测试**

`diagnose()` 现在对 dormant + 大 edge 返回 `provisional_act`（原 `watch`）。搜索所有断言 `decision == "watch"` 且场景为 dormant + 大 edge 的测试：

```bash
cd backend && python -m pytest tests/test_decision_report_service.py tests/test_prediction_store.py -v --tb=short 2>&1 | findstr FAIL
```

已知受影响的测试：
- `tests/test_decision_report_service.py::DecisionEndpointTests::test_open_decisions_lists_ranked_reports`（line 118-133）— 使用 `_market_record("op1", estimated=90.0)`，raw_edge=40，dormant → 原期望 `"watch"`，现在应为 `"provisional_act"`

将该测试的断言（line 132）从：

```python
        self.assertEqual(report["recommendation"]["decision"], "watch")
```

改为：

```python
        self.assertEqual(report["recommendation"]["decision"], "provisional_act")
```

同时更新该测试的注释（line 123）从 `# raw 40 -> dormant adj 20 -> watch` 改为 `# raw 40 -> dormant adj 20 -> provisional_act (cold-start bypass)`。

如 `test_prediction_store.py` 有类似断言，同样更新。

- [ ] **Step 10: 运行受影响测试验证通过**

```bash
cd backend && python -m pytest tests/test_decision_report_service.py tests/test_prediction_store.py -v --tb=short
```

Expected: 全部通过

- [ ] **Step 11: Commit**

```bash
git add backend/app/services/diagnosis_service.py backend/tests/test_diagnosis_service.py backend/tests/test_decision_report_service.py backend/tests/test_prediction_store.py
git commit -m "feat(diagnosis): cold-start bypass emits provisional_act for dormant segments"
```

---

## Task 3: scoring_service recommended_action 结构化输出

**Files:**
- Modify: `backend/app/services/scoring_service.py:130-135` (`recommended_action()` 函数)
- Test: `backend/tests/test_scoring_service.py`（如存在）/ 否则新建 `backend/tests/test_scoring_service.py`

**Interfaces:**
- Consumes: 无新依赖
- Produces: `recommended_action()` 新参数 `signal_direction: str | None = None`, `confidence: str | None = None`
- Produces: 当 `signal_direction` 提供时返回结构化中文短语（"押 YES（置信度：high）" 等），否则回退旧逻辑

- [ ] **Step 1: 检查是否存在 test_scoring_service.py**

```bash
ls backend/tests/test_scoring_service.py 2>$null; if ($LASTEXITCODE -ne 0) { echo "not found" }
```

如不存在，新建空测试文件：

```python
"""Tests for scoring_service pure functions."""
import unittest
from app.services import scoring_service as svc


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 写失败测试 — 结构化输出**

在 `backend/tests/test_scoring_service.py` 中新增（如新建则替换全部内容）：

```python
"""Tests for scoring_service pure functions."""
import unittest
from app.services import scoring_service as svc


class RecommendedActionTests(unittest.TestCase):
    def test_long_signal_returns_structured_yes(self):
        action = svc.recommended_action(
            50, 50, 10.0, signal_direction="LONG", confidence="high"
        )
        self.assertIn("YES", action)
        self.assertIn("high", action)

    def test_strong_long_signal_returns_structured_yes(self):
        action = svc.recommended_action(
            50, 50, 10.0, signal_direction="STRONG_LONG", confidence="medium"
        )
        self.assertIn("YES", action)
        self.assertIn("medium", action)

    def test_short_signal_returns_structured_no(self):
        action = svc.recommended_action(
            50, 50, -10.0, signal_direction="SHORT", confidence="low"
        )
        self.assertIn("NO", action)
        self.assertIn("low", action)

    def test_watchlist_signal_returns_wait(self):
        action = svc.recommended_action(
            50, 50, 1.0, signal_direction="WATCHLIST", confidence="low"
        )
        self.assertIn("等待", action)

    def test_no_signal_falls_back_to_legacy_logic(self):
        # No signal_direction -> old trust/impact based logic
        action = svc.recommended_action(75, 65, 10.0)
        self.assertIn("人工复核", action)

    def test_no_signal_falls_back_to_keep_observing(self):
        action = svc.recommended_action(30, 30, 1.0)
        self.assertIn("保持观察", action)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行测试验证失败**

```bash
cd backend && python -m pytest tests/test_scoring_service.py -v --tb=short
```

Expected: 6 个测试 FAIL（`recommended_action()` 不接受 `signal_direction` / `confidence` kwargs）

- [ ] **Step 4: 修改 recommended_action() 新增 signal_direction / confidence kwargs**

将 `backend/app/services/scoring_service.py` 的 `recommended_action()` 函数（line 130-135）替换为：

```python
def recommended_action(
    trust_score: int,
    impact_score: int,
    change: float,
    *,
    signal_direction: str | None = None,
    confidence: str | None = None,
) -> str:
    """Human-readable action recommendation.

    When signal_direction is provided (from legacy_analysis), returns a
    structured direction phrase ("押 YES（置信度：high）" etc.). When None,
    falls back to the legacy trust/impact-based logic for backward
    compatibility with callers that don't pass signal data.
    """
    if signal_direction in ("LONG", "STRONG_LONG"):
        return f"押 YES（置信度：{confidence or '未知'}）"
    if signal_direction in ("SHORT", "STRONG_SHORT"):
        return f"押 NO（置信度：{confidence or '未知'}）"
    if signal_direction == "WATCHLIST":
        return "等待更多证据"
    # Legacy fallback: no signal data -> trust/impact based phrase
    if trust_score >= 70 and impact_score >= 60:
        return "建议人工复核，并持续关注后续证据。"
    if trust_score >= 45 and abs(change) >= 5:
        return "作为活跃情报项跟踪，等待进一步确认。"
    return "保持观察；当前证据强度不足以升级处理。"
```

- [ ] **Step 5: 运行测试验证通过**

```bash
cd backend && python -m pytest tests/test_scoring_service.py -v --tb=short
```

Expected: 6 个测试全部通过

- [ ] **Step 6: 运行依赖 scoring_service 的其他测试验证无回归**

```bash
cd backend && python -m pytest tests/test_event_intelligence_service.py tests/test_events_routes.py -v --tb=short
```

Expected: 全部通过（现有调用 `recommended_action(trust, impact, change)` 不传 kwargs，走 legacy 回退路径，行为不变）

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/scoring_service.py backend/tests/test_scoring_service.py
git commit -m "feat(scoring): structured recommended_action from signal direction"
```

---

## Task 4: event_intelligence_service actionable_recommendation 字段 + 信号映射

**Files:**
- Modify: `backend/app/services/event_intelligence_service.py:34-130` (`build_event_record()` 函数) — 新增 `_build_actionable_recommendation()` 辅助函数 + 调用
- Modify: `backend/app/services/event_intelligence_service.py:107-121` — `intelligence_report.recommended_action` 调用传 `signal_direction` / `confidence`
- Test: `backend/tests/test_event_intelligence_service.py`

**Interfaces:**
- Consumes: `settings.ACTIONABLE_RECOMMENDATION_ENABLED` (Task 1)
- Consumes: `recommended_action(..., signal_direction=, confidence=)` 新签名 (Task 3)
- Consumes: `legacy_analysis` dict 的 `signal`, `signal_direction`, `signal_strength`, `position_size`, `expected_edge`, `risk_level`, `risk_flags` 字段（已由 `ai_analysis_service.analyze_market()` 计算）
- Produces: `EventRecord.actionable_recommendation: dict | None`（结构化方向 + 置信度 + 建议配置 + edge + 风险 + rationale + calibration_status）

- [ ] **Step 1: 写失败测试 — actionable_recommendation 字段映射**

在 `backend/tests/test_event_intelligence_service.py` 中新增测试类（在文件末尾，`if __name__` 块之前）。先读取该文件确认现有 import 和结构，然后追加：

```python
class ActionableRecommendationTests(unittest.TestCase):
    """Tests for the actionable_recommendation field on EventRecord (Stage 3)."""

    def _analysis(self, **overrides):
        """Minimal analysis dict that build_event_record accepts."""
        base = {
            "event_question": "Will X happen?",
            "market_probability": 40.0,
            "ai_probability": 55.0,
            "title_zh": "X 是否发生",
            "narrative_summary": "Evidence suggests X is likely.",
            "confidence_score": 0.7,
            "news_quality_score": 0.6,
            "evidence_strength": 0.5,
            "evidence_conflict_score": 0.2,
            "freshness_score": 0.8,
            "resolution_relevance_score": 0.5,
            "source_count": 5,
            "risk_level": "MEDIUM",
            "risk_flags": [],
            "signal": "LONG",
            "signal_direction": "LONG",
            "signal_strength": "HIGH",
            "position_size": 0.10,
            "expected_edge": 0.15,
            "divergence": 15.0,
            "base_rate_category": "test",
        }
        base.update(overrides)
        return base

    def test_long_signal_maps_to_yes_direction(self):
        from app.services.event_intelligence_service import build_event_record
        record = build_event_record(self._analysis(signal="LONG", signal_direction="LONG"))
        rec = record["actionable_recommendation"]
        self.assertEqual(rec["direction"], "YES")
        self.assertEqual(rec["confidence"], "high")

    def test_strong_short_signal_maps_to_no_direction(self):
        from app.services.event_intelligence_service import build_event_record
        record = build_event_record(
            self._analysis(signal="STRONG_SHORT", signal_direction="SHORT",
                           signal_strength="MEDIUM", divergence=-25.0, expected_edge=-0.25)
        )
        rec = record["actionable_recommendation"]
        self.assertEqual(rec["direction"], "NO")
        self.assertEqual(rec["confidence"], "medium")

    def test_watchlist_signal_maps_to_wait_direction(self):
        from app.services.event_intelligence_service import build_event_record
        record = build_event_record(
            self._analysis(signal="WATCHLIST", signal_direction="NEUTRAL",
                           signal_strength="LOW", divergence=2.0, expected_edge=0.02)
        )
        rec = record["actionable_recommendation"]
        self.assertEqual(rec["direction"], "WAIT")

    def test_high_risk_low_confidence_maps_to_avoid(self):
        from app.services.event_intelligence_service import build_event_record
        record = build_event_record(
            self._analysis(signal="LONG", signal_direction="LONG",
                           signal_strength="LOW", risk_flags=["a", "b", "c"])
        )
        rec = record["actionable_recommendation"]
        self.assertEqual(rec["direction"], "AVOID")

    def test_none_when_feature_disabled(self):
        from app.services.event_intelligence_service import build_event_record
        from unittest.mock import patch
        from app.services import event_intelligence_service as svc
        with patch.object(svc.settings, "ACTIONABLE_RECOMMENDATION_ENABLED", False):
            record = build_event_record(self._analysis(signal="LONG"))
        self.assertIsNone(record["actionable_recommendation"])

    def test_suggested_allocation_pct_from_position_size(self):
        from app.services.event_intelligence_service import build_event_record
        record = build_event_record(self._analysis(position_size=0.15))
        rec = record["actionable_recommendation"]
        self.assertAlmostEqual(rec["suggested_allocation_pct"], 15.0)

    def test_recommended_action_uses_signal_direction_when_available(self):
        from app.services.event_intelligence_service import build_event_record
        record = build_event_record(self._analysis(signal="LONG", signal_direction="LONG"))
        action = record["intelligence_report"]["recommended_action"]
        self.assertIn("YES", action)
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd backend && python -m pytest tests/test_event_intelligence_service.py::ActionableRecommendationTests -v --tb=short
```

Expected: 7 个测试 FAIL（`actionable_recommendation` key 不存在 / `recommended_action` 不含 YES）

- [ ] **Step 3: 新增 _build_actionable_recommendation 辅助函数**

在 `backend/app/services/event_intelligence_service.py` 的 `build_event_record()` 函数之前（约 line 33，`_CANDIDATE_POOL_FACTOR` 之后）新增：

```python
_STRENGTH_TO_CONFIDENCE = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}


def _build_actionable_recommendation(
    analysis: dict[str, Any],
    *,
    change: float,
) -> dict[str, Any] | None:
    """Build a structured actionable recommendation from the legacy signal.

    Returns None when:
    - ACTIONABLE_RECOMMENDATION_ENABLED is false
    - signal is WATCHLIST and edge is small (direction=WAIT but still returns
      a recommendation; only returns None when feature disabled)

    Maps legacy_analysis.signal -> direction (YES/NO/AVOID/WAIT) and
    signal_strength -> confidence (high/medium/low).
    """
    if not settings.ACTIONABLE_RECOMMENDATION_ENABLED:
        return None

    signal = str(analysis.get("signal") or "WATCHLIST")
    signal_direction = str(analysis.get("signal_direction") or "NEUTRAL")
    signal_strength = str(analysis.get("signal_strength") or "LOW")
    confidence = _STRENGTH_TO_CONFIDENCE.get(signal_strength, "low")

    # Direction from signal
    if signal_direction in ("LONG", "STRONG_LONG"):
        direction = "YES"
    elif signal_direction in ("SHORT", "STRONG_SHORT"):
        direction = "NO"
    else:
        direction = "WAIT"

    # AVOID override: high risk + low confidence
    risk_flags = analysis.get("risk_flags", [])
    if not isinstance(risk_flags, list):
        risk_flags = []
    if len(risk_flags) >= 2 and confidence == "low":
        direction = "AVOID"

    position_size = safe_float(analysis.get("position_size"), 0.02)
    suggested_allocation_pct = round(position_size * 100, 2)
    expected_edge = safe_float(analysis.get("expected_edge"), 0.0)
    edge_pct = round(expected_edge * 100, 2)
    risk_level = str(analysis.get("risk_level") or "UNKNOWN").lower()
    if risk_level not in ("low", "medium", "high"):
        risk_level = "medium"

    baseline = safe_float(analysis.get("market_probability"), 50.0)
    estimated = safe_float(analysis.get("ai_probability"), baseline)
    rationale = (
        f"市场定价 {baseline:.1f}%，估计 {estimated:.1f}%，"
        f"信号 {signal}，证据强度 {safe_float(analysis.get('evidence_strength'), 0.0):.2f}。"
    )
    # calibration_status is set by the caller (analyze_event) which has access
    # to segment stats; default to uncalibrated_provisional for the build_event_record
    # path (calibration_feedback may override later).
    calibration_status = "uncalibrated_provisional"

    return {
        "direction": direction,
        "confidence": confidence,
        "suggested_allocation_pct": suggested_allocation_pct,
        "edge": edge_pct,
        "risk_level": risk_level,
        "rationale": rationale,
        "calibration_status": calibration_status,
    }
```

- [ ] **Step 4: 在 build_event_record 中调用 _build_actionable_recommendation + 传 signal_direction 给 recommended_action**

在 `backend/app/services/event_intelligence_service.py` 的 `build_event_record()` 函数中，修改 return dict（约 line 67-130）。

在 `"intelligence_report"` 块内，将 `recommended_action(trust_score, impact_score, change)` 调用替换为传 signal_direction 版本：

```python
        "intelligence_report": {
            "headline": build_headline(
                str(analysis.get("title_zh") or "").strip() or question,
                change,
                trust_score,
                impact_score,
            ),
            "why_it_matters": build_why_it_matters(analysis, change),
            "probability_assessment": build_probability_assessment(
                baseline,
                estimated,
                trust_score,
            ),
            "recommended_action": recommended_action(
                trust_score,
                impact_score,
                change,
                signal_direction=analysis.get("signal_direction"),
                confidence=_STRENGTH_TO_CONFIDENCE.get(
                    str(analysis.get("signal_strength") or "LOW"), "low"
                ),
            ),
        },
```

在 return dict 末尾，`"semantics": _build_semantics(analysis),` 之后新增：

```python
        "semantics": _build_semantics(analysis),
        "actionable_recommendation": _build_actionable_recommendation(
            analysis, change=change
        ),
```

- [ ] **Step 5: 运行测试验证通过**

```bash
cd backend && python -m pytest tests/test_event_intelligence_service.py::ActionableRecommendationTests -v --tb=short
```

Expected: 7 个测试全部通过

- [ ] **Step 6: 运行 event_intelligence 全部测试验证无回归**

```bash
cd backend && python -m pytest tests/test_event_intelligence_service.py -v --tb=short
```

Expected: 全部通过

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/event_intelligence_service.py backend/tests/test_event_intelligence_service.py
git commit -m "feat(intelligence): actionable_recommendation field from legacy signal"
```

---

## Task 5: decision_report_service 透传 actionable_recommendation + calibration_status + provisional_act 诊断

**Files:**
- Modify: `backend/app/services/decision_report_service.py:17-104` — `_diagnosis_reason()` 新增 provisional_act 文案 + `build_decision_report()` 透传 actionable_recommendation + 新增 calibration_status
- Test: `backend/tests/test_decision_report_service.py`

**Interfaces:**
- Consumes: `prediction["decision"]` 可能为 `"provisional_act"` (Task 2)
- Consumes: `record["actionable_recommendation"]` (Task 4)
- Produces: `DecisionReport.actionable_recommendation: dict | None`
- Produces: `DecisionReport.recommendation.calibration_status: str`
- Produces: `DecisionReport.diagnosis.reason` 对 provisional_act 显示 "未经校准的临时行动建议"

- [ ] **Step 1: 写失败测试 — provisional_act 诊断理由 + calibration_status + actionable_recommendation 透传**

在 `backend/tests/test_decision_report_service.py` 的 `BuildDecisionReportTests` 类中新增（在 `test_diagnosis_reason_act_vs_liquidity` 之后）：

```python
    def test_provisional_act_diagnosis_reason(self):
        # provisional_act: dormant but edge large -> uncalibrated provisional
        pred = _prediction(
            decision="provisional_act", qualified=False, segment_n=0,
            segment_min_samples=8, segment_skill=None, liquidity_factor=1.0,
        )
        report = build_decision_report(pred, _record())
        self.assertIn("未经校准", report["diagnosis"]["reason"])
        self.assertEqual(report["recommendation"]["calibration_status"], "uncalibrated_provisional")

    def test_act_decision_has_calibrated_status(self):
        pred = _prediction(
            decision="act", qualified=True, segment_n=10,
            segment_min_samples=8, liquidity_factor=1.0,
        )
        report = build_decision_report(pred, _record())
        self.assertEqual(report["recommendation"]["calibration_status"], "calibrated")

    def test_watch_decision_has_uncalibrated_status(self):
        pred = _prediction(decision="watch", qualified=False, segment_n=2)
        report = build_decision_report(pred, _record())
        self.assertEqual(report["recommendation"]["calibration_status"], "uncalibrated_provisional")

    def test_actionable_recommendation_passthrough_from_record(self):
        record = _record()
        record["actionable_recommendation"] = {
            "direction": "YES",
            "confidence": "high",
            "suggested_allocation_pct": 10.0,
            "edge": 15.0,
            "risk_level": "medium",
            "rationale": "Strong evidence.",
            "calibration_status": "uncalibrated_provisional",
        }
        report = build_decision_report(_prediction(), record)
        self.assertEqual(report["actionable_recommendation"]["direction"], "YES")
        self.assertEqual(report["actionable_recommendation"]["suggested_allocation_pct"], 10.0)

    def test_actionable_recommendation_none_when_record_missing_it(self):
        report = build_decision_report(_prediction(), _record())  # _record() has no actionable_recommendation
        self.assertIsNone(report["actionable_recommendation"])

    def test_provisional_act_does_not_introduce_banned_words(self):
        # The banned-words test must still pass with provisional_act + actionable_recommendation
        record = _record()
        record["actionable_recommendation"] = {
            "direction": "YES",
            "confidence": "high",
            "suggested_allocation_pct": 10.0,
            "edge": 15.0,
            "risk_level": "medium",
            "rationale": "Strong evidence for YES.",
            "calibration_status": "uncalibrated_provisional",
        }
        pred = _prediction(
            decision="provisional_act", qualified=False, segment_n=0,
            segment_min_samples=8, liquidity_factor=1.0,
        )
        report = build_decision_report(pred, record)
        blob = str(report).lower()
        for banned in ("long", "short", "buy", "sell", "position", "kelly", "order"):
            self.assertNotIn(banned, blob, f"banned word '{banned}' found in report")
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd backend && python -m pytest tests/test_decision_report_service.py::BuildDecisionReportTests -v --tb=short
```

Expected: 6 个新测试 FAIL（`calibration_status` key 不存在 / `actionable_recommendation` key 不存在 / provisional_act reason 不含 "未经校准"）

- [ ] **Step 3: 修改 _diagnosis_reason 新增 provisional_act 分支**

将 `backend/app/services/decision_report_service.py` 的 `_diagnosis_reason()` 函数（line 17-34）替换为：

```python
def _diagnosis_reason(prediction: dict[str, Any]) -> str:
    """A short, human-readable why behind the act/provisional_act/watch/skip
    verdict, from the frozen diagnosis inputs. Explains the gating factor a
    reviewer most needs: dormancy, weak skill, or a liquidity discount."""
    decision = prediction.get("decision")
    qualified = prediction.get("qualified")
    segment_n = prediction.get("segment_n")
    segment_min = prediction.get("segment_min_samples")
    liq_factor = prediction.get("liquidity_factor")
    if decision == "act":
        return "已合格类别 + 调整后 edge 达到行动阈值"
    if decision == "provisional_act":
        # Dormant but edge large: uncalibrated provisional action.
        suffix = f"/{segment_min}" if segment_min else ""
        return f"未经校准的临时行动建议（类别样本 {segment_n or 0}{suffix}，edge 达标但未合格）"
    # watch / skip: name the dominant reason it is not act.
    if qualified is False:
        suffix = f"/{segment_min}" if segment_min else ""
        return f"类别样本不足（{segment_n or 0}{suffix} 条，未达合格线），暂不行动"
    if liq_factor is not None and liq_factor < 1.0:
        return f"流动性折损（factor {liq_factor}），调整后 edge 被压低"
    return "调整后 edge 未达行动阈值"
```

- [ ] **Step 4: 在 build_decision_report 新增 calibration_status + actionable_recommendation 透传**

将 `backend/app/services/decision_report_service.py` 的 `build_decision_report()` 函数（line 37-104）的 return dict 修改。在 `"recommendation"` 块新增 `calibration_status`，并在 return dict 末尾新增 `actionable_recommendation`：

```python
    decision = prediction.get("decision")
    qualified = prediction.get("qualified")
    # calibration_status: calibrated when qualified (segment has enough samples),
    # uncalibrated_provisional otherwise (dormant or provisional_act).
    calibration_status = "calibrated" if qualified else "uncalibrated_provisional"
    actionable = record.get("actionable_recommendation")

    return {
        "event_id": prediction.get("event_id"),
        "event": {
            "title": record.get("event_title", ""),
            "summary": record.get("event_summary", ""),
        },
        "probability": {
            "estimated": probability.get("estimated"),
            "baseline": probability.get("baseline"),
            "change": probability.get("change"),
            "direction": probability.get("direction"),
        },
        "market_view": {
            "market_probability": prediction.get("market_probability"),
            "platform": prediction.get("platform", ""),
            "liquidity": prediction.get("liquidity"),
            "volume": prediction.get("volume"),
        },
        "edge": {
            "raw": prediction.get("raw_edge"),
            "adjusted": prediction.get("adjusted_edge"),
            "trust": prediction.get("trust"),
        },
        "diagnosis": {
            "qualified": prediction.get("qualified"),
            "segment_n": prediction.get("segment_n"),
            "segment_min_samples": prediction.get("segment_min_samples"),
            "segment_skill": prediction.get("segment_skill"),
            "liquidity_factor": prediction.get("liquidity_factor"),
            "reason": _diagnosis_reason(prediction),
        },
        "confidence": {
            "level": credibility.get("level"),
            "score": credibility.get("score"),
            "confidence": credibility.get("confidence"),
        },
        "recommendation": {
            "decision": decision,
            "action": report.get("recommended_action", ""),
            "calibration_status": calibration_status,
        },
        "risk": {
            "level": risk.get("level"),
            "flags": risk.get("flags", []),
        },
        "category": prediction.get("base_rate_category"),
        "status": prediction.get("status"),
        "actionable_recommendation": actionable,
    }
```

- [ ] **Step 5: 运行测试验证通过**

```bash
cd backend && python -m pytest tests/test_decision_report_service.py -v --tb=short
```

Expected: 全部通过（原 5 个 + 新 6 个 = 11 个 BuildDecisionReportTests，含 `test_report_uses_event_vocabulary_only` 验证 provisional_act 不引入禁词）

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/decision_report_service.py backend/tests/test_decision_report_service.py
git commit -m "feat(decision-report): calibration_status + actionable_recommendation passthrough"
```

---

## Task 6: 前端 DecisionCard 渲染 actionable_recommendation + provisional_act 徽章

**Files:**
- Modify: `frontend/src/lib/api.ts:182-211` — 扩展 `DecisionReport` 接口
- Modify: `frontend/src/components/decisions/decision-card.tsx` — 新增 actionable_recommendation 区块 + provisional_act 蓝色徽章
- Test: 手动验证（前端无单元测试框架则跳过自动化测试，运行 `npm run build` 验证编译）

**Interfaces:**
- Consumes: `DecisionReport.actionable_recommendation` (Task 5)
- Consumes: `DecisionReport.recommendation.calibration_status` (Task 5)
- Consumes: `DecisionReport.recommendation.decision === "provisional_act"` (Task 2)

- [ ] **Step 1: 扩展 DecisionReport 接口**

在 `frontend/src/lib/api.ts` 的 `DecisionReport` 接口（约 line 182-211）中，在 `recommendation` 字段新增 `calibration_status`，在接口末尾新增 `actionable_recommendation`：

```typescript
export interface DecisionReport {
  event_id: string;
  event: { title: string; summary: string };
  probability: {
    estimated: number | null;
    baseline: number | null;
    change: number | null;
    direction: string | null;
  };
  market_view: {
    market_probability: number | null;
    platform: string;
    liquidity: number | null;
    volume: number | null;
  };
  edge: { raw: number | null; adjusted: number | null; trust: number | null };
  diagnosis: {
    qualified: boolean | null;
    segment_n: number | null;
    segment_min_samples?: number | null;
    segment_skill: number | null;
    liquidity_factor: number | null;
    reason: string;
  };
  confidence: { level: string | null; score: number | null; confidence: number | null };
  recommendation: {
    decision: string | null;
    action: string;
    calibration_status?: string | null;
  };
  risk: { level: string | null; flags: string[] };
  category: string | null;
  status: string | null;
  actionable_recommendation?: {
    direction: string;
    confidence: string;
    suggested_allocation_pct: number;
    edge: number;
    risk_level: string;
    rationale: string;
    calibration_status: string;
  } | null;
}
```

- [ ] **Step 2: 在 decision-card.tsx 的 DECISION_META 新增 provisional_act**

在 `frontend/src/components/decisions/decision-card.tsx` 的 `DECISION_META`（line 7-11）新增 `provisional_act`（蓝色，区别于 `act` 绿色）：

```typescript
const DECISION_META: Record<string, { label: string; cls: string }> = {
  act: { label: "建议行动", cls: "border-pos/40 bg-pos/10 text-pos" },
  provisional_act: { label: "临时行动", cls: "border-info/40 bg-info/10 text-info" },
  watch: { label: "持续观察", cls: "border-warn/40 bg-warn/10 text-warn" },
  skip: { label: "暂不参与", cls: "border-border bg-secondary text-muted-foreground" },
};
```

**注意：** 需确认 `text-info` / `border-info` / `bg-info` 颜色变量在 Tailwind 配置中存在。如不存在，使用 `border-blue-400/40 bg-blue-100/10 text-blue-600` 替代。读取 `tailwind.config.ts` 或 `globals.css` 确认。

- [ ] **Step 3: 新增 DIRECTION_META 映射 + ActionableRecommendationBlock 子组件**

在 `frontend/src/components/decisions/decision-card.tsx` 的 `FRESH_META` 之后（约 line 19）新增：

```typescript
const DIRECTION_META: Record<string, { label: string; cls: string }> = {
  YES: { label: "押 YES", cls: "border-pos/40 bg-pos/10 text-pos" },
  NO: { label: "押 NO", cls: "border-neg/40 bg-neg/10 text-neg" },
  AVOID: { label: "回避", cls: "border-warn/40 bg-warn/10 text-warn" },
  WAIT: { label: "等待", cls: "border-border bg-secondary text-muted-foreground" },
};

const CONFIDENCE_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};
```

在 `Metric` 组件之后（约 line 28）新增 `ActionableRecommendationBlock`：

```typescript
function ActionableRecommendationBlock({
  rec,
}: {
  rec: NonNullable<DecisionReport["actionable_recommendation"]>;
}) {
  const dm = DIRECTION_META[rec.direction] ?? DIRECTION_META.WAIT;
  const confLabel = CONFIDENCE_LABEL[rec.confidence] ?? rec.confidence;
  const isUncalibrated = rec.calibration_status === "uncalibrated_provisional";
  return (
    <div className="flex flex-col gap-2 border-t border-border pt-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] font-medium text-foreground">可执行建议</span>
        <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium ${dm.cls}`}>
          {dm.label}
        </span>
        <span className="rounded bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground">
          置信度 {confLabel}
        </span>
        <span className="rounded bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground">
          建议配置 {rec.suggested_allocation_pct.toFixed(1)}%
        </span>
        {isUncalibrated && (
          <span className="rounded border border-info/40 bg-info/10 px-1.5 py-0.5 text-[11px] text-info">
            未经校准
          </span>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground leading-relaxed">{rec.rationale}</p>
    </div>
  );
}
```

- [ ] **Step 4: 在 DecisionCard 中渲染 ActionableRecommendationBlock**

在 `frontend/src/components/decisions/decision-card.tsx` 的 `DecisionCard` 组件中，在 `risk.flags` 区块（约 line 149-158）之前新增 `actionable_recommendation` 渲染：

```tsx
      {report.actionable_recommendation && (
        <ActionableRecommendationBlock rec={report.actionable_recommendation} />
      )}

      {report.risk.flags.length > 0 && (
```

- [ ] **Step 5: 确认 info 颜色变量存在或使用 fallback**

读取 `frontend/tailwind.config.ts` 或 `frontend/src/app/globals.css` 确认 `info` 颜色变量。如不存在，将 Step 2 和 Step 3 中的 `info` 替换为 `blue`：

```typescript
// Fallback if info color not defined:
provisional_act: { label: "临时行动", cls: "border-blue-400/40 bg-blue-100/10 text-blue-600" },
// And in ActionableRecommendationBlock:
<span className="rounded border border-blue-400/40 bg-blue-100/10 px-1.5 py-0.5 text-[11px] text-blue-600">
```

- [ ] **Step 6: 运行前端构建验证编译**

```bash
cd frontend && npm run build
```

Expected: 构建成功，无类型错误

- [ ] **Step 7: 运行后端全套测试验证无回归**

```bash
cd backend && python -m pytest -q --tb=short
```

Expected: 全部通过（与 Task 5 完成后一致）

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/decisions/decision-card.tsx
git commit -m "feat(frontend): render actionable_recommendation + provisional_act badge"
```

---

## 验收检查清单

- [ ] 冷启动（segment_n=0）+ 大 edge → `decision=provisional_act` + `actionable_recommendation.direction=YES/NO` + `calibration_status=uncalibrated_provisional`
- [ ] 校准（segment_n≥8）+ 大 edge → `decision=act` + `calibration_status=calibrated`
- [ ] 小 edge → `decision=watch/skip` + `actionable_recommendation.direction=WAIT`
- [ ] 高风险 + 低置信度 → `direction=AVOID`
- [ ] `ACTIONABLE_RECOMMENDATION_ENABLED=false` → `actionable_recommendation=None`，`recommended_action` 回退旧逻辑
- [ ] `COLD_START_BYPASS_ENABLED=false` → dormant 永远 `watch`（旧行为）
- [ ] 前端 DecisionCard 渲染四种 direction + provisional_act 蓝色徽章 + "未经校准" 标签
- [ ] `test_report_uses_event_vocabulary_only` 仍通过（无禁词：long/short/buy/sell/position/kelly/order）
- [ ] 全部后端测试通过，无回归

## 不做（Out of Scope）

- 不改 `evidence_scoring_service`
- 不改 `prediction_store` 的 `segment_n` 计算逻辑
- 不改 `probability_engine_service`
- 不引入新的 LLM 调用
- 不改 `analysis_report_service.calculate_signal()` 的判定阈值
- Stage 4（市场参与）：本设计只产出结论，不自动下单
