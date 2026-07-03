# Frontend/Backend Type Synchronization — Design Spec

**Date**: 2026-07-03
**Spec source**: `2026-06-30-production-readiness-gaps.md` §5.1
**Status**: Approved (brainstorming complete, ready for plan)
**Scope**: 用 pydantic2ts 从 Pydantic models 自动生成 TypeScript 类型，替换手工维护的 types.ts；前置修复让 OpenAPI schema 准确

---

## 1. 背景

`production-readiness-gaps.md` §5.1 要求"前后端类型自动同步"。当前 `frontend/src/lib/types.ts` 手工维护，已有实质性 drift。

### 1.1 现状（调研发现）

**已有 drift（实质性）**：
- 前端 `EventRecord` 缺 11 个字段（Phase 1-5 overlays 全缺：`actionable_recommendation`、`evidence_breakdown`、`decision_quality`、`market_quality`、`source_reliability`、`llm_telemetry`、`final_displayed_direction`、`final_downgrade_reason`、`schema_version`、`legacy_analysis`、`risk`）
- 前端有 phantom `CrossValidation` 类型，后端 `EventRecord` 未声明该字段（但 `event_intelligence_service.py` 实际写入 `record["cross_validation"]`）
- 3 处命名不匹配：`EvidenceProfile`→`EvidenceAggregate`、`EventSemantics`→`Semantics`、`EventStoreEntry`→`TrackedEntry`
- `EvidenceItem.title_zh`/`summary_zh` 靠 `extra="allow"` 存活，未在 Pydantic 声明

**后端结构限制**：
- `quality_metrics.py`（6 端点）和 `world_cup_analytics.py` 全部返回 `dict[str, Any]`，无 `response_model`
- `EventRecord` 的 overlay 字段（`decision_quality` 等）类型是 `dict[str, Any] | None`，而非对应的 Pydantic 类
- OpenAPI spec 对大部分操作 API 不透明

### 1.2 核心决策：方案 A（Pydantic-first）

三种方案：
- **方案 A — Pydantic-first（pydantic2ts）**：从 Pydantic models 直接生成 TS 类型
- **方案 B — OpenAPI-first（openapi-typescript）**：导出 OpenAPI spec 生成 TS，需先给所有端点加 response_model
- **方案 C — 混合（pydantic2ts + CI drift check）**

**选择方案 A**。理由：
1. 方案 B 的后端 refactoring（给所有端点加 response_model）是独立大工程
2. 方案 A 的前置修复（overlay 字段类型化）本身有价值——让 OpenAPI spec 更准确，为未来方案 B 铺路
3. EventRecord 是前端消费最频繁的类型，修好它的 drift 收益最大

### 1.3 核心原则

**Pydantic models → `generated-types.ts` 是后端契约 source of truth；`types.ts` 是前端消费适配层，不再手写重复后端字段，只补充后端没有建模但前端需要的 UI/legacy 字段。**

---

## 2. 前置修复与调研

### 2.1 cross_validation 决策：补 Pydantic 字段

后端 `event_intelligence_service.py` 实际写入 `record["cross_validation"]`，前端 `events/page.tsx`、`signal-panel.tsx`、`signal-summary.tsx` 都读取。补 Pydantic 模型：

```python
class CrossValidation(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    probability: float | None = None
    agreement: str | None = None
    divergence: float | None = None
```

`EventRecord` 新增字段：`cross_validation: CrossValidation | None = None`。

### 2.2 EventRecord overlay 字段类型化

[backend/app/models/event.py](file:///e:/Github/Prediction%20Market%20Reality%20Filter/backend/app/models/event.py) `EventRecord` 类，将以下字段类型从 `dict[str, Any] | None` 改为对应 Pydantic 类：

```python
# Before (现状)
decision_quality: dict[str, Any] | None = None
market_quality: dict[str, Any] | None = None
source_reliability: dict[str, Any] | None = None
llm_telemetry: dict[str, Any] | None = None

# After
decision_quality: DecisionQuality | None = None
market_quality: MarketQuality | None = None
source_reliability: SourceReliability | None = None
llm_telemetry: LLMTelemetry | None = None
```

**前置确认**：所有 overlay 类已设置 `extra="allow"`（`DecisionQuality`/`MarketQuality`/`SourceReliability`/`LLMTelemetry` 都有 `model_config = ConfigDict(extra="allow")`）。

**风险评估**：这些 Pydantic 类已经存在，且字段定义与实际写入的 dict 结构一致（同一个 service 层构建）。改类型后：
- Pydantic 校验从 dict（不校验）变为 model（校验），可能暴露历史数据中字段类型不匹配的问题
- `extra="allow"` 保证历史额外字段不被丢弃

**配套测试**（必须通过）：
- `EventRecord.model_validate()` 能吃当前正常 overlay dict
- 能吃 overlay build failure 的 `{"error": "build_failed", ...}` block（靠 `extra="allow"`）
- 能吃旧记录缺失 overlay 字段（字段是 `Optional`，缺失为 `None`）
- 现有 event_store normalize / save / list 回归

### 2.3 Allowlist 范围与限制

14 个 root models：

```python
ALLOWLIST = [
    "EventRecord",
    "EventStoreEntry",
    "EventListResponse",
    "EventMoversResponse",
    "EventHistoryResponse",
    "DecisionTimelineResponse",
    "AutoResolveResponse",
    "PendingLinksResponse",
    "RecentPredictionsResponse",
    "OpenDecisionsResponse",
    "FreshEdgesResponse",
    "SimilarEventsResponse",
    "EventAnalysisRequest",
    "EventDiscoveryResponse",
]
```

嵌套模型（`DecisionQuality`、`MarketQuality`、`SourceReliability`、`LLMTelemetry`、`ActionableRecommendation`、`EvidenceBreakdownItem`、`Outcome`、`Calibration`、`CrossValidation` 等）通过 root model 的字段引用自动递归生成。

不生成：
- `FlexibleResponse`（基类，无字段）
- `MarketModel` / `NewsModel`（无前端消费）
- world_cup_prediction.py 的 SQLAlchemy ORM（非 Pydantic）
- `quality_metrics.py` / `world_cup_analytics.py` 的 `dict[str, Any]` 端点（无 Pydantic 模型）

**第一阶段限制**：

> allowlist 第一阶段只保证 `EventRecord` 及已建模响应的类型同步；对仍为 `dict[str, Any]` 的响应字段（`AutoResolveResponse.matches`、`PendingLinksResponse.pending`、`RecentPredictionsResponse.predictions`、`OpenDecisionsResponse.decisions`、`FreshEdgesResponse.edges`、`SimilarEventsResponse.similar`），本轮不强行深度建模，除非前端正在消费且收益明确。

### 2.4 Pydantic2ts 工具锁定

实现阶段确认 `pydantic2ts` 包：
- 支持 Pydantic v2（项目用 pydantic v2）
- 输入方式（Pydantic 模型列表 vs JSON schema）
- 输出排序稳定性（避免无意义 diff）
- CLI vs Python API

---

## 3. 生成脚本与前端整合

### 3.1 命名映射写法

```typescript
// frontend/src/lib/types.ts

import type {
  EventRecord as BackendEventRecord,
  EventStoreEntry as BackendEventStoreEntry,
  EvidenceProfile as BackendEvidenceProfile,
  EventSemantics as BackendEventSemantics,
} from "./generated-types";

// Re-export all generated types
export type * from "./generated-types";

// Frontend naming aliases (preserve existing frontend names)
export type EventRecord = BackendEventRecord;
export type TrackedEntry = BackendEventStoreEntry;
export type EvidenceAggregate = BackendEvidenceProfile;
export type Semantics = BackendEventSemantics;
```

**类型严格化风险处理**：生成类型会比现有手写类型严格（required vs optional）。实现时若前端测试 fixture / partial mock 暴露类型错误，改用 `Partial<EventRecord>` 或测试专用 helper，**不为前端测试改宽生成类型**。

### 3.2 `api.ts` 整合（分两档）

**明确替换**（generated types 直接替代 inline）：
- `TrackedEntry`, `EventRecord`, `DecisionTimelineResponse`, `AutoResolveResponse`, `EventListResponse`, `EventMoversResponse`, `EventHistoryResponse`

**暂不替换**（保留手写）：
- `quality_metrics` 全部
- `world_cup` 全部
- 后端仍是 `dict[str, Any]` 的响应内部数组：`PendingLinksResponse.pending`、`FreshEdgesResponse.edges`、`RecentPredictionsResponse.predictions`、`OpenDecisionsResponse.decisions`、`SimilarEventsResponse.similar`、`AutoResolveResponse.matches`

### 3.3 生成脚本 `--check` 模式

`generate_types.py` 支持两种模式：
- **默认模式**：生成并写入 `generated-types.ts`
- **`--check` 模式**：生成到内存，与目标文件比较，不一致 exit 1（不修改工作区）

### 3.4 CI Drift Check

```yaml
type-sync-check:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - name: Setup Python
      uses: actions/setup-python@v5
      with:
        python-version: "3.11"
    - name: Install deps
      run: pip install -r backend/requirements-dev.txt
    - name: Check type sync
      run: cd backend && python -m scripts.generate_types --check
```

**保护范围说明**：
- `generated-types.ts`：generator 产物，`--check` 模式直接校验
- `types.ts`：非 generator 产物（adapter 层），不重写，但通过 git diff 纳入保护（`--check` 失败时人工审查 types.ts 是否被误改）

---

## 4. 文件清单

### 新增

| 文件 | 责任 |
|---|---|
| `backend/scripts/generate_types.py` | 生成脚本：默认模式写入 + `--check` 模式校验 |
| `frontend/src/lib/generated-types.ts` | generator 产物（首次生成后提交） |
| `backend/tests/test_generate_types.py` | 生成脚本测试（allowlist 覆盖、--check 模式、输出格式） |
| `backend/tests/test_event_record_overlay_typing.py` | overlay 字段类型化回归测试 |

### 修改

| 文件 | 修改内容 |
|---|---|
| `backend/app/models/event.py` | 新增 `CrossValidation` 类；`EventRecord` 加 `cross_validation` 字段；overlay 字段类型从 `dict` 改为 Pydantic 类 |
| `frontend/src/lib/types.ts` | 改为 adapter 层：import generated types + 命名 alias + 删除手写重复字段 |
| `frontend/src/lib/api.ts` | 明确替换档的 inline 类型改为 import generated |
| `backend/requirements-dev.txt` | 添加 `pydantic2ts` |
| `.github/workflows/ci.yml` | 新增 `type-sync-check` job |

---

## 5. 测试覆盖

### 5.1 后端测试

- `test_event_record_overlay_typing.py`
  - `EventRecord.model_validate()` 吃正常 overlay dict
  - 吃 overlay build failure `{"error": "build_failed", ...}` block
  - 吃旧记录缺失 overlay 字段
  - 吃 `cross_validation` dict
  - 现有 event_store normalize / save / list 回归
- `test_generate_types.py`
  - allowlist 14 个 root models 全部生成
  - 嵌套模型递归生成
  - `--check` 模式：一致时 exit 0，不一致时 exit 1
  - 输出文件含 header comment
  - 输出排序稳定（多次生成 diff 为空）

### 5.2 前端测试

- `tsc --noEmit` 通过（类型严格化后无编译错误）
- 现有 vitest 测试通过（fixture 改用 `Partial<EventRecord>` 如需）

### 5.3 CI 集成测试

- `type-sync-check` job 在 clean 工作区 exit 0
- 故意修改 `generated-types.ts` 后 exit 1

---

## 6. 范围边界

### 包含

- `CrossValidation` Pydantic 模型新增
- `EventRecord` overlay 字段类型化 + 回归测试
- `generate_types.py` 生成脚本（默认 + `--check` 模式）
- `generated-types.ts` 首次生成
- `types.ts` 改为 adapter 层
- `api.ts` 明确替换档的 inline 类型替换
- CI `type-sync-check` job
- `pydantic2ts` 依赖添加

### 不包含

- 不给 `quality_metrics.py` / `world_cup_analytics.py` 端点加 `response_model`（独立大工程）
- 不深度建模 `dict[str, Any]` 响应内部数组
- 不处理 `world_cup_prediction.py` 的 SQLAlchemy ORM
- 不做运行时验证（zod/valibot）
- 不重命名前端现有类型（用 alias 保持兼容）

---

## 7. 验收标准

- [ ] `CrossValidation` Pydantic 模型新增，`EventRecord.cross_validation` 字段声明
- [ ] `EventRecord` overlay 字段类型从 `dict[str, Any] | None` 改为对应 Pydantic 类
- [ ] 所有 overlay 类确认有 `extra="allow"`
- [ ] `EventRecord.model_validate()` 能吃正常 overlay dict / build failure block / 缺失字段
- [ ] 现有 event_store normalize / save / list 回归测试通过
- [ ] `generate_types.py` 支持 14 个 root models 的 allowlist
- [ ] `generate_types.py` 默认模式写入 `generated-types.ts`
- [ ] `generate_types.py` `--check` 模式：一致 exit 0，不一致 exit 1
- [ ] `generate_types.py` 输出排序稳定（多次生成 diff 为空）
- [ ] `types.ts` 改为 adapter 层：import generated + 命名 alias + 删除手写重复
- [ ] `types.ts` 命名映射：`TrackedEntry`/`EvidenceAggregate`/`Semantics` 保持前端现有命名
- [ ] `api.ts` 明确替换档的 inline 类型改为 import generated
- [ ] `api.ts` 暂不替换档保留手写（quality_metrics / world_cup / dict 内部数组）
- [ ] `tsc --noEmit` 通过
- [ ] 现有 vitest 测试通过（fixture 改 `Partial<EventRecord>` 如需）
- [ ] CI `type-sync-check` job 在 clean 工作区 exit 0
- [ ] 故意修改 `generated-types.ts` 后 `--check` exit 1
- [ ] `pydantic2ts` 添加到 `requirements-dev.txt`
- [ ] 全后端回归测试通过，无 regression
