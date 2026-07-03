# Model Evaluation Lab — Design Spec

**Date**: 2026-07-03
**Spec source**: `2026-06-30-production-readiness-gaps.md` §4.6
**Status**: Approved (brainstorming complete, ready for plan)
**Scope**: 离线分析历史 resolved 事件，按 model / analysis_quality / degraded_mode 分组，报告 Brier / ECE / direction accuracy / cost / guardrail 触发率

---

## 1. 背景

`production-readiness-gaps.md` §4.6 要求"模型评估实验室"：按 model/prompt/config/analysis_quality 分组，报告 Brier / ECE / cost / guardrail 结果。

### 1.1 现有基础设施

| 能力 | 文件 | 状态 |
|---|---|---|
| Replay harness | [replay_decision_pipeline.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/scripts/replay_decision_pipeline.py) + `backend/app/replay/` | ✅ 但明确跳过 live LLM，只切 overlay 配置 |
| 质量切片 service | [quality_metrics_report_service.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/services/quality_metrics_report_service.py) | ✅ 有 `extract_metrics` / `slice_metrics` / `group_by` / `calibration_deviation` / `build_report` |
| Brier / skill / grade | [calibration_service_event.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/services/calibration_service_event.py) | ✅ `brier_score` / `skill_score` / `grade` / `_aggregate` |
| Direction accuracy | `prediction_calibration_service.compute_direction_correct` | ✅ |
| LLMTelemetry.model | [event.py:357](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/models/event.py) | ✅ 字符串字段 |
| LLMTelemetry.estimated_token_cost | [event.py:355](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/models/event.py) | ✅ float 字段 |
| guardrail_fired | EventRecord 字段 | ✅ list[str] |
| ECE 标量函数 | — | ❌ 不存在，calibration_deviation 是近亲但非标量 |
| prompt_version | — | ❌ LLMTelemetry 无此字段，全局 grep 无匹配 |

### 1.2 核心决策：方案 A（纯只读切片）

模型评估实验室有两种定位：

- **方案 A — 纯只读切片**：仅按现有 `record.llm_telemetry.model` 分组，不调 LLM。复用 `extract_metrics` + `slice_metrics` + `group_by`，新增 ECE / cost / guardrail 指标。
- **方案 B — 重新分析**：在 A 基础上，对冻结事件用新 model/prompt 重新调 LLM，生成新 telemetry 再 diff。需加 `prompt_version` 字段 + live LLM 路径。
- **方案 C — A 先行 + 预留 B**

**选择方案 A**。理由：
1. spec §4.6 原文"groups resolved events by ... and reports ..."是切片语义，不是"re-runs and compares"
2. `prompt_version` 字段不存在，加它需改 LLMTelemetry + llm_telemetry_service + freeze_prediction + 历史回填，是独立大工程
3. 方案 A 能立即交付价值（"gpt-4o-mini 在 sports_event 类目 Brier 0.23"这类洞察）
4. 后续若需 B，可作为独立 spec 推进

---

## 2. 架构

### 2.1 模块拆分

1. **纯函数 service** `backend/app/services/model_eval_lab_service.py`
   - `extract_model_metrics(record)` — 调 `extract_metrics` 后 append model / degraded_mode / cost / guardrail
   - `compute_ece(items)` — 新 ECE 标量函数
   - `slice_model_metrics(items)` — 扩展 `slice_metrics`，加 ECE / cost / guardrail / degraded 聚合
   - `group_model_slices(items, key, min_samples)` — 局部 `_group_by` + `slice_model_metrics`，不足组标 `insufficient_samples`
   - `build_model_eval_report(items, report_errors, min_samples)` — overview + by_model + by_analysis_quality + by_degraded_mode + calibration_deviation + report_errors

2. **CLI** `backend/scripts/model_eval_lab.py`
   - 仿 `report_quality_metrics.py` 骨架（argparse + UTF-8 + `_print` + `--json`），但自带 `_collect_entries`，不逐字复用旧 CLI
   - ASCII-only 输出（仅用 `==` 作分隔线，不用 emoji / box drawing chars）

3. **API 端点**：暂不做（spec §4.6 只要求 CLI）

### 2.2 为什么独立 service 而不扩展 quality_metrics_report_service

- `quality_metrics_report_service` 是 4 维固定切片（source_type / analysis_quality / edge_bucket / source_reliability_bucket），结构稳定，前端已消费
- model_eval_lab 的切片维度不同（model / analysis_quality / degraded_mode），且新增 ECE / cost / guardrail 指标
- 混入会破坏现有 API 契约

### 2.3 为什么不扩展 analyze_feature_flag_impact

- `analyze_feature_flag_impact` 切换 overlay 配置（`*_enabled` + 阈值 settings），是重放语义
- 模型评估实验室是切片语义（只读历史），不需要重放
- 但 model_eval_lab 复用 `extract_metrics` / `slice_metrics` / `compute_direction_correct` / `compute_edge_bucket` / `brier_score` 等原语

---

## 3. 纯函数 service 详细设计

### 3.1 文件：`backend/app/services/model_eval_lab_service.py`

### 3.2 `extract_model_metrics(record) -> dict[str, Any]`

```python
def extract_model_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Extract model-eval metrics from a record.

    Calls existing extract_metrics (preserves direction/Brier/edge semantics),
    then appends model / degraded_mode / degraded_mode_label /
    estimated_token_cost / guardrail_fired.

    model source: llm_telemetry.model, missing -> "unknown".
    Never infer model from current settings (would pollute historical attribution).
    """
    item = extract_metrics(record)  # from quality_metrics_report_service
    llm = record.get("llm_telemetry") or {}
    if not isinstance(llm, dict):
        llm = {}
    item["model"] = llm.get("model") or "unknown"
    item["degraded_mode"] = bool(llm.get("degraded_mode", False))
    item["degraded_mode_label"] = "degraded" if item["degraded_mode"] else "normal"
    cost = safe_float(llm.get("estimated_token_cost"))
    item["estimated_token_cost"] = cost  # None when missing/non-finite/bool
    guardrails = record.get("guardrail_fired")
    item["guardrail_fired"] = guardrails if isinstance(guardrails, list) else []
    return item
```

**复用关系**：`extract_metrics` 来自 `quality_metrics_report_service`，`safe_float` 同源。不复制逻辑，只 append。

**model 来源锁定**：`llm_telemetry.model` 优先，缺失为 `"unknown"`。绝不从 `settings` 推断旧记录模型。

### 3.3 `compute_ece(items) -> float | None`

```python
_PROB_BUCKETS = [(0.0, 20.0), (20.0, 40.0), (40.0, 60.0), (60.0, 80.0), (80.0, 101.0)]

def compute_ece(items: list[dict[str, Any]]) -> float | None:
    """Expected Calibration Error (0-100 scale).

    Formula: sum(bucket_n / total_n * abs(predicted_mean - actual_mean))
    Only counts records with both estimated_probability and actual_outcome.
    Returns None when no eligible records.

    Scale: 0-100 probability points (consistent with calibration_deviation).
    Last bucket upper bound 101.0 to cover 100.0 with < comparison.
    """
    eligible = [
        it for it in items
        if isinstance(it.get("estimated_probability"), (int, float))
        and not isinstance(it.get("estimated_probability"), bool)
        and isinstance(it.get("actual_outcome"), (int, float))
        and not isinstance(it.get("actual_outcome"), bool)
    ]
    total = len(eligible)
    if total == 0:
        return None
    ece = 0.0
    for lo, hi in _PROB_BUCKETS:
        bucket = [
            it for it in eligible
            if lo <= it["estimated_probability"] < hi
        ]
        if not bucket:
            continue
        bucket_n = len(bucket)
        predicted_mean = sum(it["estimated_probability"] for it in bucket) / bucket_n
        actual_mean = sum(it["actual_outcome"] for it in bucket) / bucket_n
        ece += (bucket_n / total) * abs(predicted_mean - actual_mean)
    return ece
```

**字段来源**：
- `estimated_probability` — 来自 `actionable_recommendation.ai_probability`（`extract_metrics` 已抽取）
- `actual_outcome` — 来自 `outcome.actual_outcome`（100.0=YES, 0.0=NO）

**bool 防御**：Python 里 `bool` 是 `int` 子类，`isinstance(True, (int, float))` 为 `True`，需显式排除。

### 3.4 `slice_model_metrics(items) -> dict[str, Any]`

```python
def slice_model_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Extended slice_metrics with ECE, cost, and guardrail aggregations.

    Inherits all fields from slice_metrics (n, direction_correct_*, brier,
    missing_calibration_rate, direction_accuracy), then adds:
        ece                  — float | None
        cost_total           — float (0.0 when no cost data)
        cost_avg             — float | None (None when cost_n == 0)
        cost_n               — int (count of non-None costs)
        guardrail_count      — int
        guardrail_rate       — float (0.0-1.0)
        degraded_count       — int
        degraded_rate        — float (0.0-1.0)
    """
    base = slice_metrics(items)  # from quality_metrics_report_service
    cost_values = [
        it["estimated_token_cost"]
        for it in items
        if it.get("estimated_token_cost") is not None
    ]
    cost_total = sum(cost_values) if cost_values else 0.0
    cost_n = len(cost_values)
    cost_avg = cost_total / cost_n if cost_n else None
    guardrail_count = sum(1 for it in items if it.get("guardrail_fired"))
    guardrail_rate = guardrail_count / len(items) if items else 0.0
    degraded_count = sum(1 for it in items if it.get("degraded_mode"))
    degraded_rate = degraded_count / len(items) if items else 0.0
    return {
        **base,
        "ece": compute_ece(items),
        "cost_total": cost_total,
        "cost_avg": cost_avg,
        "cost_n": cost_n,
        "guardrail_count": guardrail_count,
        "guardrail_rate": guardrail_rate,
        "degraded_count": degraded_count,
        "degraded_rate": degraded_rate,
    }
```

**cost 语义**：
- `extract_model_metrics` 中缺失/非有限/bool cost → `None`
- `slice_model_metrics` 中 `cost_n` 只统计非 None，`cost_avg` 在 `cost_n == 0` 时为 `None`
- 这样旧记录大量缺失 cost 不会被 0 稀释平均值

### 3.5 `_group_by(items, key) -> dict[str, list]`（内部）

```python
def _group_by(
    items: list[dict[str, Any]],
    key: str,
) -> dict[str, list[dict[str, Any]]]:
    """Group items by a flat key on the item dict. Local helper — does
    not touch quality_metrics_report_service.group_by (which hardcodes
    slice_metrics and would drop cost/guardrail/ECE)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        k = str(it.get(key, "unknown"))
        groups.setdefault(k, []).append(it)
    return groups
```

### 3.6 `group_model_slices(items, key, min_samples=0) -> dict[str, dict]`

```python
def group_model_slices(
    items: list[dict[str, Any]],
    key: str,
    *,
    min_samples: int = 0,
) -> dict[str, dict[str, Any]]:
    """Group items by key, slice each group with slice_model_metrics.

    Groups with fewer than min_samples are still computed but flagged
    ``insufficient_samples: True`` (not dropped — caller decides).
    """
    groups = _group_by(items, key)
    result: dict[str, dict[str, Any]] = {}
    for k, group_items in groups.items():
        slice_data = slice_model_metrics(group_items)
        slice_data["insufficient_samples"] = len(group_items) < min_samples
        result[k] = slice_data
    return result
```

### 3.7 `build_model_eval_report(items, report_errors, min_samples=0) -> dict`

```python
def build_model_eval_report(
    items: list[dict[str, Any]],
    report_errors: list[dict[str, Any]],
    *,
    min_samples: int = 0,
) -> dict[str, Any]:
    """Build the full model evaluation report.

    overview always computed from ALL items (min_samples does NOT filter
    overview). by_model / by_analysis_quality / by_degraded_mode use
    group_model_slices with min_samples flagging (not filtering).
    """
    overview = slice_model_metrics(items)
    return {
        "overview": overview,
        "by_model": group_model_slices(items, "model", min_samples=min_samples),
        "by_analysis_quality": group_model_slices(
            items, "analysis_quality", min_samples=min_samples,
        ),
        "by_degraded_mode": group_model_slices(
            items, "degraded_mode_label", min_samples=min_samples,
        ),
        "calibration_deviation": calibration_deviation(items),
        "report_errors": report_errors,
        "min_samples": min_samples,
    }
```

**overview 不受 min_samples 影响**：从全部 items 计算。min_samples 只在 by_* 分组里标记 `insufficient_samples: True`，让 CLI 渲染时决定是否标记。

**by_degraded_mode 按 `degraded_mode_label` 分组**（"degraded"/"normal"），而非 bool 字符串 "True"/"False"。

---

## 4. CLI 详细设计

### 4.1 文件：`backend/scripts/model_eval_lab.py`

### 4.2 参数

```
python -m scripts.model_eval_lab [--sample N] [--event-ids id1,id2]
                                  [--min-samples N] [--json]
```

| 参数 | 默认 | 用途 | 校验 |
|---|---|---|---|
| `--sample N` | None | 随机抽样 N 条 resolved 事件（seed=42 可复现） | N < 0 → exit 2；N == 0 合法（输出空 report） |
| `--event-ids` | None | 限定指定事件（逗号分隔） | 解析后为空列表（如 ","）→ exit 2 |
| `--min-samples N` | 5 | 表格展示阈值，不足组标 `[INSUFFICIENT]`；overview 不受影响 | N < 0 → exit 2 |
| `--json` | flag | stdout 输出 JSON（否则 ASCII 表格） | — |

**`--sample` 与 `--event-ids` 同时给**：先按 event_ids 过滤，再在过滤结果里 sample。语义最直观。

### 4.3 数据加载：`_collect_entries(sample, event_ids)`

新脚本自带，不逐字复用 `report_quality_metrics.py`：

```python
def _collect_entries(
    sample: int | None,
    event_ids: list[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load resolved events from event_store, extract model metrics.

    Returns (items, report_errors).
    report_errors only records:
      - record is not a dict
      - extract_model_metrics raised
    Does NOT validate field types (degraded_mode not bool etc.).
    """
    records = _load_event_store()  # read event_store.json
    if event_ids:
        id_set = set(event_ids)
        records = [r for r in records if r.get("event_id") in id_set]
    # Filter to resolved events only
    records = [r for r in records if _is_resolved(r)]
    if sample is not None:
        rng = random.Random(42)
        records = rng.sample(records, min(sample, len(records)))
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            errors.append({"index": idx, "error": "record is not a dict"})
            continue
        try:
            items.append(extract_model_metrics(rec))
        except Exception as exc:
            errors.append({
                "event_id": rec.get("event_id", "<unknown>"),
                "error": str(exc),
            })
    return items, errors
```

### 4.4 输出结构（ASCII 模式）

```
[INFO] Loaded 142 resolved events (2 report errors)
[INFO] Min samples for table display: 5

== Overview (all 142 events) ==
n=142  brier=0.1842  skill=0.2632  grade=ACCEPTABLE  direction_acc=0.6829 (56/82)
ece=8.42  cost_total=$1.234  cost_avg=$0.0172 (n=118)  guardrail_rate=23.94%  degraded_rate=15.49%

== By Model ==
model          n    brier    ece    direction_acc  cost_avg   guardrail_rate  degraded_rate
gpt-4o-mini    98   0.1721   6.13   0.7143 (40/56) $0.0189    20.41%          10.20%
unknown        35   0.2203   12.41  0.6250 (15/24) [n/a]      31.43%          40.00%
deterministic  9    0.1850   7.82   0.5000 (4/8)   [n/a]      11.11%          100.00%  [INSUFFICIENT]

== By Analysis Quality ==
analysis_quality        n    brier    ece    direction_acc  cost_avg   guardrail_rate  degraded_rate
llm                     98   0.1721   6.13   0.7143 (40/56) $0.0189    20.41%          0.00%
deterministic_fallback  35   0.2203   12.41  0.6250 (15/24) [n/a]      31.43%          100.00%
unknown                 9    0.1850   7.82   0.5000 (4/8)   [n/a]      11.11%          0.00%  [INSUFFICIENT]

== By Degraded Mode ==
mode        n    brier    ece    direction_acc  cost_avg   guardrail_rate
normal      120  0.1798   7.21   0.7049 (43/61) $0.0189    21.67%
degraded    22   0.2125   11.30  0.6190 (13/21) [n/a]      36.36%

== Calibration Deviation ==
bucket      n    predicted_mean  actual_mean  deviation
[0,20)      18   12.4            5.6          +6.8
[20,40)     24   31.2            25.0         +6.2
[40,60)     31   49.8            52.1         -2.3
[60,80)     28   71.3            75.6         -4.3
[80,100]    41   88.9            92.1         -3.2

== Report Errors (2) ==
[WARN] record at index 17 is not a dict
[WARN] extract_model_metrics failed for event evt-xxx: <exception message>
```

**ASCII-only 约束**：仅用 `==` 作分隔线。不用 emoji（📊/⚠️）、box drawing chars（─/═/│）。

**cost 缺失显示**：`cost_n == 0` → ASCII 显示 `[n/a]`，JSON 中 `cost_avg: null`。

**`--min-samples` 行为**：不足组仍显示，行尾追加 `[INSUFFICIENT]`，不 drop。

### 4.5 JSON 模式

`--json` flag → stdout 直接输出 `build_model_eval_report` 的 dict（`json.dumps` with `indent=2, ensure_ascii=False`）。无 `[INFO]` 前缀，纯 JSON。

### 4.6 退出码

- 0：成功（含有 report_errors 仍为 0）
- 2：配置/参数错误（N < 0 / event_ids 解析为空等）

### 4.7 report_errors 范围（收紧）

只承诺两类：
- record 不是 dict
- `extract_model_metrics` 抛异常

不在 service 里加 validation warnings（degraded_mode 类型异常、cost 非数字等不进 errors）。`extract_model_metrics` 是 best-effort append，字段类型异常会被 `safe_float` / `isinstance` 防御性处理，不抛异常。

---

## 5. 文件清单

### 新增

| 文件 | 责任 |
|---|---|
| `backend/app/services/model_eval_lab_service.py` | 纯函数 service：extract / compute_ece / slice_model_metrics / group_model_slices / build_model_eval_report |
| `backend/scripts/model_eval_lab.py` | CLI：_collect_entries + 渲染 + argparse |
| `backend/tests/test_model_eval_lab_service.py` | service 纯函数测试 |
| `backend/tests/test_model_eval_lab_cli.py` | CLI main() 测试（exit code / 参数校验 / 输出格式） |

### 修改

无。本 spec 不修改任何现有文件（纯新增 + 复用）。

---

## 6. 测试覆盖

### 6.1 service 测试（`test_model_eval_lab_service.py`）

- `extract_model_metrics`
  - 正常 record：model / degraded_mode / cost / guardrail 正确 append
  - llm_telemetry 缺失：model="unknown", cost=None, guardrail_fired=[]
  - llm_telemetry 非 dict：同上
  - cost 为 bool / inf / nan / 字符串：safe_float 返回 None
- `compute_ece`
  - 全 eligible：返回 0-100 标量
  - 无 eligible（缺 estimated_probability 或 actual_outcome）：返回 None
  - bool 防御：`estimated_probability=True` 不计为 1.0
  - 100.0 边界：落入最后一桶
- `slice_model_metrics`
  - 继承 slice_metrics 全部字段
  - cost_n == 0 → cost_avg=None
  - cost_n > 0 → cost_avg 正确
  - guardrail_count / rate 正确
  - degraded_count / rate 正确
- `group_model_slices`
  - 按 key 分组正确
  - min_samples 标记 insufficient_samples=True（不 drop）
- `build_model_eval_report`
  - overview 从全部 items 计算
  - by_model / by_analysis_quality / by_degraded_mode 结构正确
  - by_degraded_mode key 是 "degraded"/"normal"（非 "True"/"False"）
  - min_samples 不影响 overview

### 6.2 CLI 测试（`test_model_eval_lab_cli.py`）

- 无参数：exit 0，输出含 `== Overview ==`
- `--json`：stdout 是合法 JSON，无 `[INFO]` 前缀
- `--sample 0`：exit 0，输出空 report（overview n=0）
- `--sample -1`：exit 2
- `--min-samples -1`：exit 2
- `--event-ids ","`：exit 2（解析为空列表）
- `--event-ids "evt-001,evt-002"`：只统计指定事件
- `--sample N --event-ids ids`：先过滤再 sample
- report_errors 存在时仍 exit 0
- ASCII-only：输出不含 emoji / box drawing chars

---

## 7. 范围边界

### 包含

- 纯函数 service（extract / compute_ece / slice / group / build_report）
- CLI（_collect_entries + 渲染 + 参数校验）
- 全部测试

### 不包含

- 不加 `prompt_version` 字段（独立大工程，后续 spec）
- 不调 live LLM（方案 A 定位）
- 不做 API 端点（spec §4.6 只要求 CLI）
- 不修改现有 `quality_metrics_report_service` / `group_by` / `slice_metrics`
- 不做前端可视化（后续单独做）
- 不加 validation warnings（report_errors 只承诺两类）

---

## 8. 验收标准

- [ ] `extract_model_metrics` 调 `extract_metrics` 后 append，不复制逻辑
- [ ] `compute_ece` 只统计同时有 `estimated_probability` 和 `actual_outcome` 的记录，返回 None 当无 eligible
- [ ] `compute_ece` 防御 bool（`isinstance(True, int)` 不计）
- [ ] `compute_ece` 最后一桶上界 101.0 覆盖 100.0
- [ ] `slice_model_metrics` 用 `**base` 展开 + 新字段
- [ ] cost 缺失为 None，cost_n 只统计非 None，cost_avg 在 cost_n==0 时为 None
- [ ] `group_model_slices` 不 drop 不足样本组，标 `insufficient_samples`
- [ ] `build_model_eval_report` 的 overview 不受 min_samples 影响
- [ ] `by_degraded_mode` 按 `degraded_mode_label`（"degraded"/"normal"）分组
- [ ] model 来源锁定 `llm_telemetry.model`，缺失为 "unknown"，不从 settings 推断
- [ ] CLI 自带 `_collect_entries`，不逐字复用旧 CLI
- [ ] `--sample` / `--event-ids` 同时给时：先过滤再 sample
- [ ] `--sample N` / `--min-samples N`：N < 0 → exit 2
- [ ] `--sample 0` 合法，输出空 report
- [ ] `--event-ids ","` → exit 2
- [ ] `--min-samples` 不足组标 `[INSUFFICIENT]`，不 drop
- [ ] ASCII-only 输出（仅 `==` 分隔线，无 emoji / box drawing chars）
- [ ] cost_n == 0 → ASCII 显示 `[n/a]`，JSON 中 `cost_avg: null`
- [ ] JSON 模式无 `[INFO]` 前缀
- [ ] report_errors 只承诺两类（record 非 dict / extract 抛异常）
- [ ] 全部测试通过，无回归
