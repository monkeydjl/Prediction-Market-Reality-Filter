# Replay / Evaluation Harness 设计

**文档版本**: v1.0
**创建日期**: 2026-06-30
**状态**: 设计已确定，待写实施计划
**关联**: [production-readiness-gaps.md](./2026-06-30-production-readiness-gaps.md) §4.5 / §1.5 / §4.2

---

## 📋 执行摘要

把 spec 的三个独立项目收敛为一个统一工具：

| spec 项目 | 章节 | 在本设计的角色 |
|---|---|---|
| 全链路 Replay / Simulation Harness | §4.5 | 主能力——重放 Phase 1-5 + merge + guardrail |
| 特性开关 A/B 对比 | §1.5 | replay 的双 config 对比模式 |
| 降级场景测试 | §4.2 | replay 的 pytest 集成（preset_llm_degraded profile） |

**战略锚点**：让"启用某 Phase 后多少 YES 变 WAIT"和"Brier 是否改善"变成可量化、可复现、可对比的指标，为后续所有质量评估投资提供 ROI 度量。

---

## 🏗️ 架构与文件组织

```
backend/scripts/replay_decision_pipeline.py     # CLI 入口 + 主循环
backend/app/replay/
  ├── __init__.py
  ├── config.py          # ReplayConfig dataclass + apply_replay_config ctx mgr
  ├── runner.py          # 单条 record 重放：apply config → 5 overlays → merge → guardrail
  ├── metrics.py         # 指标累积器（direction 矩阵 / Brier / direction_correct / 分组 / 边际）
  └── report.py          # Markdown + JSON 双输出
backend/tests/
  ├── test_replay_runner.py
  ├── test_replay_config.py
  ├── test_replay_metrics.py
  └── test_replay_degraded_modes.py
```

### 职责切分

- **config.py**：纯数据 dataclass + contextmanager，不依赖 IO
- **runner.py**：单条 record 重放，输入 dict 输出 dict（replayed record copy），除 settings 切换外无副作用
- **metrics.py**：累积器对象，喂 `(original, replayed)` 对，输出统计 dict
- **report.py**：把统计 dict 渲染成 Markdown + JSON
- **replay_decision_pipeline.py**：读 event_store + prediction_store，循环调 runner，喂 metrics，调 report

### 设计原则

- runner / config / metrics 都是纯函数式，可独立单元测试，不读真实 event_store
- 降级测试复用 runner 的 config profile，不模拟真实 LLM 失败
- pipeline 脚本本身（IO 层）不做单元测试，靠 runner / metrics 单测覆盖逻辑

---

## ⚙️ ReplayConfig — 配置切换机制

### ReplayConfig dataclass

```python
@dataclass
class ReplayConfig:
    """Replay-time feature flag profile. Only includes flags that affect
    overlay output — arbitrary env vars are out of scope."""
    decision_quality_enabled: bool | None = None       # None = use current settings
    market_quality_enabled: bool | None = None
    source_reliability_enabled: bool | None = None
    prediction_calibration_enabled: bool | None = None
    llm_telemetry_enabled: bool | None = None
    guardrails_enabled: bool | None = None
    guardrail_llm_degraded_blocks_act: bool | None = None
    guardrail_uncalibrated_category_blocks_act: bool | None = None
    guardrail_high_conflict_blocks_act: bool | None = None
```

### 预设 Profile

```python
@classmethod
def preset_all_off(cls) -> "ReplayConfig":
    """Pre-Phase-1 baseline (byte-identical to pre-overlay records)."""
    return cls(
        decision_quality_enabled=False,
        market_quality_enabled=False,
        source_reliability_enabled=False,
        prediction_calibration_enabled=False,
        llm_telemetry_enabled=False,
        guardrails_enabled=False,
    )

@classmethod
def preset_all_on(cls) -> "ReplayConfig":
    """Use current settings (assume production-on)."""
    return cls()  # all None

@classmethod
def preset_llm_degraded(cls) -> "ReplayConfig":
    """Simulate full LLM failure scenario."""
    return cls(llm_telemetry_enabled=True, guardrails_enabled=True)
```

### apply_replay_config contextmanager

```python
@contextmanager
def apply_replay_config(cfg: ReplayConfig):
    """Temporarily overlay ReplayConfig onto global settings. Restores on exit
    even if exception. Single-threaded replay use only."""
    saved: dict[str, object] = {}
    try:
        for field_name in cfg.__dataclass_fields__:
            val = getattr(cfg, field_name)
            if val is not None:
                key = field_name.upper()
                saved[key] = getattr(settings, key)
                setattr(settings, key, val)
        yield
    finally:
        for key, val in saved.items():
            setattr(settings, key, val)
```

### 关键设计点

1. **只切换 overlay-relevant flags**，不碰 `OPENAI_API_KEY` / `EVENT_STORE_FILE` 等。replay 期间不会触发真实 LLM 调用（runner 用 record 里已存的 `legacy_analysis`）。

2. **`None` 语义 = 用当前 settings 值**，这样 `preset_all_on()` 不写死 true/false，而是继承生产配置——适合"对比当前 vs 全关"。

3. **LLM degraded 场景特殊处理**：`llm_telemetry.degraded_mode` 不是配置开关，而是 LLM 调用结果。replay 不能重新调用 LLM，所以 preset_llm_degraded 在 runner 里 post-process：把 record 的 `llm_telemetry.degraded_mode` 强制设为 True，然后重跑 guardrail（验证 `llm_degraded_blocks_act` 规则是否正确触发）。

4. **`guardrail_uncalibrated_category_blocks_act` 需要 `qualified_categories`**：runner 从 prediction_store 读 calibration_summary（best-effort，失败传 None），不在 config 里。

5. **线程安全**：replay 是单进程单线程串行循环，settings 切换不持锁；如果未来要并行重放需重新设计。

---

## 🔄 ReplayRunner — 单条 record 重放

### 核心接口

```python
def replay_record(record: dict, cfg: ReplayConfig) -> dict:
    """Re-run all 5 overlays + merge + guardrail on a frozen record.

    Input: original event record (with legacy_analysis / evidence_breakdown /
    market_quote / sentiment_profile already populated from the live event).
    Output: new record copy with overlay fields recomputed under cfg.

    Frozen input contract: the caller guarantees record contains the LLM-era
    artifacts. We never call analyze_market / cross_validate / translate_articles
    / fetch_full_text — those would require live LLM + network. If a required
    input is missing, the overlay's existing try/except produces an error block
    (same as live production behavior when an overlay fails).

    Idempotent: calling twice with the same cfg produces the same output.
    Does NOT mutate the input record (deep copy via copy.deepcopy).
    """
    replayed = copy.deepcopy(record)

    # Strip existing overlay fields so re-running produces fresh values
    for key in ("decision_quality", "market_quality", "source_reliability",
                "llm_telemetry", "final_displayed_direction",
                "final_downgrade_reason", "guardrail_fired"):
        replayed.pop(key, None)

    with apply_replay_config(cfg):
        _rebuild_overlays(replayed, original_record=record)

    return replayed
```

### _build_all_overlays 共享函数（关键重构）

为避免 replay 路径与 production 路径 drift，把 `event_intelligence_service.py:325-499` 的 overlay 构建逻辑抽成共享函数：

```python
# event_intelligence_service.py（重构后）
def _build_all_overlays(
    record: dict,
    *,
    analysis: dict,
    sentiment_profile: dict | None,
    news_context: str,
    market_quote: dict | None,
    filtered_articles: list[dict] | None = None,
) -> None:
    """Build all 5 overlays + merge + guardrail in-place on `record`.

    Shared between analyze_event (live) and replay_record (replay) so the
    overlay build sequence has a single source of truth. Refactor is pure
    pull-out — no behavior change, covered by existing 1688-test suite.
    """
    # Phase 1: Decision Quality
    # Phase 2: Market Quality
    # Phase 3: Prediction Calibration
    # Phase 4: Source Reliability
    # merge_quality_overlays
    # Phase 5: LLM Telemetry
    # P0-8: Guardrails
    ...


async def analyze_event(...):
    ...
    record = build_event_record(analysis, source=source)
    ...
    _build_all_overlays(
        record,
        analysis=analysis,
        sentiment_profile=sentiment_profile,
        news_context=combined_context,
        market_quote=market_quote,
        filtered_articles=filtered_articles,
    )
    ...
```

`_rebuild_overlays`（runner 内部）调用同一个 `_build_all_overlays`，传入从 frozen record 恢复的输入。

### LLM-era 输入恢复策略

**决策：不持久化 `news_context` / `filtered_articles`，用 record 现有字段足够。**

理由：
- overlay build 函数实际不读 `news_context` 本身（Phase 1 `build_decision_quality` 只读 `recommendation` + `evidence_breakdown`）
- `filtered_articles` 仅用于 Phase 1 的 `aggregate_evidence_breakdown` 重新聚合；若缺失则 replay 沿用 record 里已有的 `evidence_breakdown`，不重算
- 5 个 overlay 都能从 record 现有字段获得足够输入：
  - `legacy_analysis`（含 `ai_probability` / `signal` / `evidence_strength` / `market_probability`）
  - `market_quote`
  - `sentiment_profile`
  - `evidence_breakdown`
  - `source`

### LLM degraded 场景 post-processing

```python
def _simulate_llm_degraded(replayed: dict) -> dict:
    """Force llm_telemetry.degraded_mode=True and re-run guardrail
    to verify llm_degraded_blocks_act rule fires."""
    if isinstance(replayed.get("llm_telemetry"), dict):
        replayed["llm_telemetry"]["degraded_mode"] = True
        replayed["llm_telemetry"]["analysis_quality"] = "deterministic_fallback"
    # Re-run guardrail only (other overlays already built)
    replayed.pop("final_displayed_direction", None)
    replayed.pop("final_downgrade_reason", None)
    replayed.pop("guardrail_fired", None)
    # Re-call evaluate_guardrails with the degraded llm_telemetry
    ...
```

降级测试不需要真实 LLM 失败，只需 config profile + post-process。

---

## 📊 Metrics — 完整指标集

### ReplayMetrics dataclass

```python
@dataclass
class ReplayMetrics:
    """Accumulates pairwise (original, replayed) comparisons."""
    total: int = 0
    direction_matrix: dict[tuple[str, str], int] = field(default_factory=dict)
    # e.g. {("YES", "WAIT"): 17, ("YES", "AVOID"): 3, ("NO", "WAIT"): 8, ...}

    resolved_count: int = 0
    brier_original_sum: float = 0.0
    brier_replayed_sum: float = 0.0
    direction_correct_original: int = 0
    direction_correct_replayed: int = 0
    direction_correct_resolved_count: int = 0

    # spec §4.5: "fallback 与 LLM 样本分开统计"
    brier_by_quality: dict[str, _BrierBucket] = field(default_factory=dict)
    # _BrierBucket(llm_n=, llm_brier_sum=, fallback_n=, fallback_brier_sum=)

    # spec §4.5: "每个 Phase 的边际贡献"
    phase_contributions: dict[str, _PhaseContribution] = field(default_factory=dict)
    # _PhaseContribution(downgrades_caused=, directions_changed=, conflicts_with_others=)

    # spec §4.5: "冲突案例"
    conflict_cases: list[dict] = field(default_factory=list)
    # {event_id, original_dir, replayed_dir, conflicting_phases: [...]}
```

### 5 类指标 → spec §4.5 要求映射

| spec 要求 | 实现位置 |
|---|---|
| "YES → WAIT / YES → AVOID / WAIT → AVOID 的比例" | `direction_matrix` 二维计数 |
| "resolved 样本上的 Brier 改善或恶化" | `brier_original_sum` vs `brier_replayed_sum` |
| "降级命中的真实错误率" | `direction_correct_*` 在降级样本子集上的比率 |
| "fallback 与 LLM 样本分开统计" | `brier_by_quality["llm"]` vs `brier_by_quality["deterministic_fallback"]` |
| "每个 Phase 的边际贡献与冲突案例" | `phase_contributions` + `conflict_cases` |

### 每 Phase 边际贡献算法

为了算"Phase X 单独贡献了多少降级"，需要 **N+1 次 replay**：

1. baseline: `preset_all_off()` → 得到每个事件的 `base_dir`
2. for each phase P: `preset_all_off()` + 只开 P → 得到 `dir_with_P`
3. final: `preset_all_on()` → 得到 `final_dir`

Phase P 的 `downgrades_caused` = #{events where `base_dir` ∈ {YES,NO} and `dir_with_P` ∈ {WAIT, AVOID}}

**N 的口径**：N=6（phase 级别：DQ / MQ / SR / PC / LLM_Telemetry / Guard），不是 9（ReplayConfig 有 9 个字段，但 3 个 `guardrail_*_blocks_act` 是 Guard Phase 的子规则，不单独归因）。**这是 N+1=7 次完整重放**。spec 说"500 sample" 默认即 3500 次 record 重放，每次重放约 5-20ms（纯函数），总耗时约 30-120 秒，可接受。

**测试场景下的 `preset_all_on()` 行为澄清**：`preset_all_on()` 返回 `ReplayConfig()`（所有字段 None），实际行为是"用当前 settings 值"。在 pytest 中 settings 默认 feature off，所以测试里 `preset_all_on()` 等价于 `preset_all_off()`。要测"all on" 语义，测试需显式 `monkeypatch(settings.DECISION_QUALITY_ENABLED, True)` 或用专用 fixture。生产 CLI 运行时 settings 已被 .env 设为 on，`preset_all_on()` 才真正代表"当前生产配置"。

### conflict_cases 收集

当某 Phase 单独开时的 `dir_with_P` 与 final `final_dir` 方向不同（例如 Phase 2 单独开 → YES，但 final → WAIT，说明其他 Phase 覆盖了 Phase 2 的判断），记录为冲突案例。

---

## 🖥️ CLI + 报告输出

### CLI 接口

```python
# backend/scripts/replay_decision_pipeline.py
"""Replay Phase 1-5 overlays on frozen event records to quantify
direction-change impact, Brier delta, and per-phase contributions.

Usage:
    # Default: all-events, current-config vs all-off (marginal contribution)
    python -m scripts.replay_decision_pipeline

    # Specific events
    python -m scripts.replay_decision_pipeline --event-ids id1 id2

    # Sample N events
    python -m scripts.replay_decision_pipeline --sample-size 500

    # Custom config profile
    python -m scripts.replay_decision_pipeline --profile all_off_vs_current
    python -m scripts.replay_decision_pipeline --profile llm_degraded

    # Output
    python -m scripts.replay_decision_pipeline --output-dir docs/reports/replay/
"""
```

### A/B 对比模式（spec §1.5）

`--compare` 接受两个 config 名，跑两遍，输出对比报告：

```bash
python -m scripts.replay_decision_pipeline --compare current all_off
```

### 输出格式

Markdown + JSON + cases.jsonl 三格式：

```
docs/reports/replay/YYYY-MM-DD-HHMMSS/
  ├── report.md          # 人类阅读
  ├── metrics.json       # 机器解析 + 后续对比
  └── cases.jsonl         # 每条 event 的 (original, replayed) 详情
```

### Markdown 报告章节（对应 spec 5 类指标）

1. **Summary** — total / resolved / direction change rate
2. **Direction Matrix** — YES→WAIT / YES→AVOID / WAIT→AVOID 比例 + 表格
3. **Brier** — original vs replayed mean，improved/regressed delta
4. **Direction Accuracy** — resolved 样本上 direction_correct 比率
5. **LLM vs Fallback 分组** — Brier + 样本数 + 是否混入 headline
6. **Per-Phase Marginal Contribution** — 表格：Phase / downgrades_caused / conflicts
7. **Conflict Cases** — 前 20 个冲突案例详情

---

## 🧪 测试

```
backend/tests/
  ├── test_replay_runner.py        # 单条 record replay 正确性
  │   - test_replay_preserves_legacy_analysis
  │   - test_replay_all_off_strips_overlays
  │   - test_replay_all_on_matches_original
  │   - test_replay_llm_degraded_forces_guardrail
  ├── test_replay_config.py        # config 切换 + 恢复
  │   - test_apply_replay_config_restores_on_exit
  │   - test_apply_replay_config_restores_on_exception
  │   - test_preset_all_off_disables_everything
  ├── test_replay_metrics.py       # 指标累积正确性
  │   - test_direction_matrix_accumulates
  │   - test_brier_delta_signed
  │   - test_phase_contributions_isolates_single_phase
  │   - test_conflict_cases_collected
  └── test_replay_degraded_modes.py  # spec §4.2 降级场景
      - test_all_phases_degraded_still_produces_recommendation
      - test_market_quality_disabled_when_source_not_prediction_market
      - test_source_reliability_disabled_when_no_evidence_breakdown
      - test_partial_degradation_does_not_block_pipeline
      - test_llm_degraded_triggers_guardrail_block
```

### 测试原则

- runner / config / metrics 都是纯函数式，pytest 用合成 record fixture 即可测，不读真实 event_store
- 降级测试复用 runner 的 `preset_llm_degraded` profile，不模拟真实 LLM 失败
- pipeline 脚本本身（IO 层）不做单元测试，靠 runner / metrics 单测覆盖逻辑

---

## 📋 决策汇总

| 决策 | 选择 | 理由 |
|---|---|---|
| 三项目组织 | 三合一 | A/B 本质是 replay 跑两遍不同 config；降级测试是 replay 的 config profile |
| 指标范围 | 完整 5 类 | 用户选完整集 |
| LLM 输入 | 不持久化，用 record 现有字段 | overlay 不读 news_context 本身，filtered_articles 缺失时沿用已有 evidence_breakdown |
| overlay 编排 | 抽 `_build_all_overlays` 共享函数 | 避免 replay 与 production drift，1688 测试兜底重构 |
| LLM degraded | post-process 模拟 | replay 不能重调 LLM，post-process 足够验证 guardrail |
| 每 Phase 边际 | N+1 次 replay | 唯一能严格归因单 Phase 贡献的方法，3500 次重放约 30-120 秒 |
| 输出 | Markdown + JSON + cases.jsonl | 人类阅读 + 机器解析 + 详情追溯 |
| 测试 | runner / config / metrics 纯单元 + 降级场景集成 | 纯函数易测，IO 层靠单测覆盖逻辑 |

---

## ⚠️ 风险与限制

1. **`_build_all_overlays` 重构 production 代码**：虽然是纯提取，但触及 `analyze_event` 核心。需先跑现有 1688 测试确认重构无 behavior change，再添加 replay 测试。

2. **Phase 3 Prediction Calibration 的特殊性**：`build_prediction_calibration` 是 freeze-time 计算（写入 prediction_store），不是 per-event overlay。replay 不重放 Phase 3 的 freeze/score 路径，只在 record 已有 `prediction_calibration` 字段时参与 merge。若 record 没有（pre-Phase-3 事件），replay 的 `preset_all_on` 在该事件上 Phase 3 相当于关闭。

3. **N+1 次 replay 性能**：500 sample × 7 replay = 3500 次，每次 5-20ms = 30-120 秒。可接受但若用户嫌慢可加 `--skip-marginal` 跳过 §6。

4. **LLM token cost 无法重算**：Phase 5 的 `estimated_token_cost` 依赖真实 token count，replay 只能沿用 record 里已有的值，不能重新估算。这是 spec §3.4 已提到的限制。

5. **replay 不写回 event_store**：replay 是只读分析工具，输出到 `docs/reports/replay/`。不修改 production 数据。

---

## 🔗 与现有设计文档的关系

- **[decision-quality-engine-design.md](./2026-06-30-decision-quality-engine-design.md)**：定义 Phase 1-5 overlay 架构（被 replay 的对象）
- **[production-readiness-gaps.md](./2026-06-30-production-readiness-gaps.md)**：列出 §4.5 / §1.5 / §4.2 三个未实现项目（本设计实现）
- **P0 已完成项**：guardrail_service / metrics / event_store schema version 等基础设施已就绪，replay 可直接复用

---

## 📝 变更记录

| 版本 | 日期 | 变更内容 |
|---|---|---|
| v1.0 | 2026-06-30 | 初始设计：三项目收敛为统一 replay harness，完整 5 类指标，N+1 次重放算法 |

---

**文档所有者**: 系统架构组
**审核状态**: 待审核
**下次更新**: 实施计划编写后
