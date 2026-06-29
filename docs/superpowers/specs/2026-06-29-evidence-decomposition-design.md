# Evidence Decomposition Design Spec

**Date:** 2026-06-29
**Status:** Draft strengthened after codebase review (pending user review)
**Scope:** AI probability engine analysis capability enhancement
**Branch target:** `fix/v0.3.0-hardening` (or new feature branch off it)

## Why

当前 AI 概率引擎已经能输出结构化结论，但证据链仍然不够可审计：

1. **文章级贡献不可见** - `news_sentiment_service.analyze_sentiment()` 输出整体方向和整体强度，但事件记录里看不到每篇文章如何支撑或反对 YES 结果。
2. **`actionable_recommendation.rationale` 不可追溯** - Stage 3 已经落地 `actionable_recommendation`，但 rationale 仍来自聚合后的 legacy analysis，无法对应到具体证据。
3. **`evidence_profile` 是聚合信号，不是审计明细** - 当前 `evidence_profile` 适合参与概率回归和风控惩罚，但不适合回答“哪篇文章贡献了这个方向”。

本 spec 通过扩展现有 `analyze_sentiment` LLM 调用的输出 schema，产出可审计的 `evidence_breakdown` 字段。它是一个附加解释层，不改变主概率路径。

## Current Code Baseline

本次补强基于当前代码状态，而不是只按旧 spec 推断：

- `backend/app/models/event.py` 已经存在 `EvidenceItem`，用于 `EventRecord.evidence_items` 的 UI 证据条目。新模型不能再叫 `EvidenceItem`，否则会和现有语义冲突。
- `EventRecord` 当前字段包含 `evidence_items: list[EvidenceItem] = []`，这些条目只暴露质量、相关性、来源、标题、摘要，不包含逐篇支持/反对 stance。
- `_build_filtered_news()` 已经完成：收集文章 -> 过滤 -> 全文抓取 -> `analyze_sentiment()` -> `apply_sentiment_fusion()`。`filtered["articles"]` 和 `filtered["sentiment_profile"]` 是新字段聚合的真实来源。
- `build_event_record()` 当前只接收 `analysis` 和 `source`，无法直接访问 `filtered["articles"]`。因此 `evidence_breakdown` 的集成点应该在 `analyze_event()` 或其调用方，而不是只写在 `build_event_record()` 伪代码里。
- Stage 3 的 `actionable_recommendation`、`provisional_act`、前端 DecisionCard 渲染已经存在。本文只补证据拆解，不重复改 Stage 3。

## Goal

新增 `evidence_breakdown: list[EvidenceBreakdownItem]` 字段到 `EventRecord`，记录每篇文章对 YES 结论的贡献：

- `source`
- `title`
- `direction` (`support` / `oppose`)
- `strength` (`0.0` - `1.0`)
- `credibility` (`0.0` - `1.0`)
- `rationale_zh`

**不改变**：`ai_probability` 推理路径、`evidence_profile` 关键词/LLM 融合规则、回归层行为、前端页面展示。

## Architecture

### 数据流

```
_build_filtered_news()
  -> filter_news_for_market()
  -> filtered["articles"]                         # 过滤、去重、排序后的文章
  -> analyze_sentiment(event_question, articles)  # 扩展每篇文章输出 schema
  -> filtered["sentiment_profile"]
  -> apply_sentiment_fusion()                     # 保持现有 evidence_profile 行为

analyze_event(..., sentiment_profile, filtered_articles)
  -> analyze_market()
  -> build_event_record()                         # 仍负责主事件记录
  -> aggregate_evidence_breakdown()               # 新纯函数
  -> record["evidence_breakdown"]
```

### 关键设计决策

1. **扩展而非新增 LLM 调用** - `analyze_sentiment` 已经在 `_build_filtered_news()` 里运行，扩展输出字段不增加输入 token，只增加少量输出 token。
2. **不动 `evidence_profile`** - 现有 `apply_sentiment_fusion()` 仍是概率引擎信号来源；`evidence_breakdown` 只做解释和审计。
3. **不复用现有 `EvidenceItem` 名称** - 新模型命名为 `EvidenceBreakdownItem`，避免和 `evidence_items` 的 UI 条目冲突。
4. **不输出 `marginal_p`** - LLM 不负责边际概率计算，只输出定性方向和强度。
5. **不动 `_ask_ai` / `analyze_market` prompt** - 避免让主概率 LLM 依赖 sentiment LLM 的新增字段。
6. **不动前端页面** - 本次只产出字段；展示是后续独立工作。
7. **默认 fail closed** - 缺字段、格式异常、禁词污染、索引错误都降级为跳过 item 或 `evidence_breakdown=[]`。

## Component Design

### 1. `news_sentiment_service.py` 扩展

当前每篇文章输出：

```json
{
  "index": 0,
  "sentiment": "positive|negative|neutral",
  "impact": "high|medium|low",
  "key_facts": ["fact 1"],
  "relevance_to_question": 0.8
}
```

扩展后每篇文章增加 4 个字段：

```json
{
  "index": 0,
  "sentiment": "positive|negative|neutral",
  "impact": "high|medium|low",
  "key_facts": ["fact 1"],
  "relevance_to_question": 0.8,
  "evidence_direction": "support|oppose|neutral",
  "evidence_strength": 0.0,
  "source_credibility": 0.0,
  "rationale_zh": "一句中文，说明这篇文章为什么支持或反对 YES。"
}
```

集合层输出保持不变：

- `overall_direction`
- `overall_strength`
- `conflict_level`
- `summary`

Prompt 新增要求：

```text
For each article, also assess:
5. evidence_direction: does this article support or oppose the YES outcome?
   (support | oppose | neutral) - based on concrete facts, not tone.
6. evidence_strength: how strongly does this article move the probability?
   (0.0-1.0) - consider specificity, directness, freshness, and source authority.
7. source_credibility: how trustworthy is this source for this topic?
   (0.0-1.0) - official/regulatory > Reuters/AP/Bloomberg > established media > aggregators/blogs.
8. rationale_zh: one Simplified Chinese sentence explaining direction+strength.
   Use event vocabulary (YES/NO/支持/反对). Do not use trading terms:
   long, short, buy, sell, position, kelly, order.
```

`analyze_sentiment()` 不应因为某篇文章缺少新增字段而整体 fallback。最小结构仍是 `articles` + `overall_direction`；新增字段由聚合函数逐项校验，缺失时该 item 视为 neutral 并跳过。

### 2. `evidence_aggregation_service.py` 新建

新建纯函数，无 LLM、无 IO，便于完整单测：

```python
def aggregate_evidence_breakdown(
    sentiment_articles: list[dict[str, Any]] | None,
    original_articles: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Transform LLM sentiment article output into EventRecord.evidence_breakdown."""
```

过滤规则：

- `direction` 缺失、未知或 `neutral` -> 跳过
- `strength < 0.2` -> 跳过
- `index` 缺失、非整数、bool、负数、越界 -> 跳过
- `original_articles[index]` 没有有效 title -> 跳过

规范化规则：

- `direction`: 只允许 `support` / `oppose`
- `strength`: clamp 到 `[0, 1]`
- `credibility`: clamp 到 `[0, 1]`，缺失默认 `0.5`
- `source`: 从 `original_articles[index]["source"]` 获取，缺失用 `"unknown"`
- `title`: 从 `original_articles[index]["title"]` 获取，截断到 200 字符
- `rationale_zh`: 字符串化、去首尾空白、禁词替换、截断到 300 字符
- 输出顺序保持 LLM 返回顺序，不重新排序

禁词处理：

- 必须过滤大小写变体：`long`, `short`, `buy`, `sell`, `position`, `kelly`, `order`
- 建议映射：
  - `long` / `buy` -> `支持 YES`
  - `short` / `sell` -> `支持 NO`
  - `position` -> `配置`
  - `kelly` -> `风险预算`
  - `order` -> `决策`

输出结构：

```python
{
    "source": "Reuters",
    "title": "Fed signals...",
    "direction": "support",
    "strength": 0.8,
    "credibility": 0.9,
    "rationale_zh": "这篇报道提供了直接支持 YES 的事实。"
}
```

### 3. `models/event.py` schema

不要修改现有 `EvidenceItem`。新增：

```python
class EvidenceBreakdownItem(BaseModel):
    """Single article's contribution to the event-level YES/NO evidence."""

    source: str = ""
    title: str = ""
    direction: str  # support | oppose
    strength: float = 0.0
    credibility: float = 0.0
    rationale_zh: str = ""
```

`EventRecord` 新增字段时使用 `default_factory`，避免可变默认值：

```python
from pydantic import Field

class EventRecord(BaseModel):
    ...
    evidence_items: list[EvidenceItem] = []
    evidence_breakdown: list[EvidenceBreakdownItem] = Field(default_factory=list)
```

后续可单独修正现有 `evidence_items: list[EvidenceItem] = []` 的可变默认值，但不作为本次范围的必要条件。

### 4. 集成点

`build_event_record()` 保持可独立调用，并默认产出空字段：

```python
return {
    ...
    "evidence_breakdown": [],
}
```

`analyze_event()` 增加可选参数：

```python
async def analyze_event(
    ...,
    sentiment_profile: dict[str, Any] | None = None,
    filtered_articles: list[dict[str, Any]] | None = None,
    ...
) -> dict[str, Any]:
```

在 `record = build_event_record(...)` 后聚合：

```python
from app.services.evidence_aggregation_service import aggregate_evidence_breakdown

if settings.EVIDENCE_BREAKDOWN_ENABLED:
    record["evidence_breakdown"] = aggregate_evidence_breakdown(
        (sentiment_profile or {}).get("articles", []),
        filtered_articles or [],
    )
else:
    record["evidence_breakdown"] = []
```

所有调用 `_build_filtered_news()` 的路径需要把过滤后的文章传入 `analyze_event()`：

```python
record = await analyze_event(
    ...,
    sentiment_profile=filtered_news.get("sentiment_profile"),
    filtered_articles=filtered_news.get("articles", []),
)
```

这比在每个调用方手动设置 `record["evidence_breakdown"]` 更稳，因为聚合逻辑只存在一处。

### 5. 配置项

放在 `NEWS_SENTIMENT_*` 配置旁边：

```python
EVIDENCE_BREAKDOWN_ENABLED: bool = _env_bool(
    "EVIDENCE_BREAKDOWN_ENABLED", "true"
)
```

`.env.example` 增加：

```env
# Emit per-article evidence contribution details from the existing
# news sentiment LLM output. Does not affect probability calculation.
EVIDENCE_BREAKDOWN_ENABLED=true
```

## File List

| 文件 | 类型 | 改动 |
|------|------|------|
| `backend/app/services/news_sentiment_service.py` | 改 | 扩展 `_SYSTEM_PROMPT` 的 article schema 和评估指令 |
| `backend/app/services/evidence_aggregation_service.py` | 新建 | 新增聚合纯函数和禁词清洗 |
| `backend/app/services/event_intelligence_service.py` | 改 | `analyze_event()` 增加 `filtered_articles` 参数并写入 `evidence_breakdown` |
| `backend/app/models/event.py` | 改 | 新增 `EvidenceBreakdownItem` + `EventRecord.evidence_breakdown` |
| `backend/app/core/config.py` | 改 | 加 `EVIDENCE_BREAKDOWN_ENABLED` |
| `backend/.env.example` | 改 | 文档化新配置 |
| `backend/tests/test_news_sentiment_service.py` | 改 | 验证 prompt/schema 和 mock LLM 新字段透传 |
| `backend/tests/test_evidence_aggregation_service.py` | 新建 | 聚合函数完整单测 |
| `backend/tests/test_event_intelligence_service.py` | 改 | 验证 `analyze_event()` / discovery wiring 写入 `evidence_breakdown` |

## Testing Strategy

### `news_sentiment_service`

- `_SYSTEM_PROMPT` 包含 `evidence_direction`、`evidence_strength`、`source_credibility`、`rationale_zh`
- mock LLM 返回新字段时，`analyze_sentiment()` 原样保留在 `result["articles"][0]`
- mock LLM 返回旧字段但最小结构有效时，不整体 fallback；聚合层负责跳过缺失新字段的 item
- malformed JSON / 缺 `articles` / 缺 `overall_direction` 仍 fallback

### `evidence_aggregation_service`

必须覆盖：

- neutral direction 被过滤
- strength < 0.2 被过滤
- 缺失或非法 direction 被过滤
- index 缺失、非整数、bool、负数、越界被过滤
- original article 缺 title 被过滤
- 空输入返回 `[]`
- strength / credibility clamp 到 `[0, 1]`
- credibility 缺失默认 `0.5`
- source 缺失输出 `"unknown"`
- title 截断到 200 字符
- rationale 截断到 300 字符
- rationale 禁词替换大小写变体
- 输出顺序保持 sentiment article 顺序
- source/title 从 `original_articles[index]` 取，不信任 LLM 自带 source/title

### `event_intelligence_service`

- `build_event_record()` 直接调用时包含 `evidence_breakdown: []`
- `analyze_event()` 在 `EVIDENCE_BREAKDOWN_ENABLED=true` 且有 sentiment + filtered articles 时写入 breakdown
- `EVIDENCE_BREAKDOWN_ENABLED=false` 时始终 `[]`
- `sentiment_profile` fallback 或 `articles=[]` 时 `[]`
- `_build_filtered_news()` 的两个 discovery 调用路径把 `filtered_news["articles"]` 传给 `analyze_event()`
- 不改变 `evidence_items` 的数量、字段和语义

## Error Handling

| 失败场景 | 行为 |
|---------|------|
| `analyze_sentiment` LLM 调用失败 | 已有 `_neutral_fallback` -> `articles=[]` -> `evidence_breakdown=[]` |
| `analyze_sentiment` 返回 malformed JSON | 已有 fallback -> `evidence_breakdown=[]` |
| LLM 只返回旧 article schema | 不整体失败；聚合层视为 neutral 并跳过 |
| LLM 输出非法 direction | skip 该 item |
| LLM 输出 strength / credibility 超范围 | clamp 到 `[0, 1]` |
| `index` 缺失/非法/越界 | skip 该 item |
| 原始文章缺 title | skip 该 item |
| `rationale_zh` 含禁词 | post-filter 替换 |
| `EVIDENCE_BREAKDOWN_ENABLED=false` | 跳过聚合，返回 `[]` |

所有失败路径都降级为 `evidence_breakdown=[]` 或跳过单条 item，**不影响主流程**。

## Backward Compatibility

- `evidence_breakdown` 默认 `[]`，旧调用者忽略即可。
- 现有 `evidence_items` 和 `EvidenceItem` 保持不变。
- `analyze_sentiment` 集合层输出 schema 不变，现有读取 `overall_direction/strength/conflict_level/summary` 的代码不受影响。
- `evidence_profile` 和 `apply_sentiment_fusion()` 完全保留。
- `_ask_ai` / `analyze_market` prompt 不变，`ai_probability` 推理路径不变。
- 前端不展示新字段，因此 UI 无回归风险。

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| 新模型名和现有 `EvidenceItem` 冲突 | 使用 `EvidenceBreakdownItem` |
| LLM 输出新字段不稳定 | 不把缺字段视为整体失败；聚合层逐项 fail closed |
| rationale 泄漏交易禁词 | prompt 约束 + 聚合层大小写禁词替换 + 单测 |
| token 成本上升 | 输入不变，只增加输出字段；若成本过高，后续可只要求 top-3 article 输出 rationale |
| 新字段被误解为概率计算依据 | 文档和字段注释明确：解释层，不参与 `evidence_profile` / `ai_probability` |
| 调用方遗漏 filtered articles | `analyze_event()` 用可选参数集中处理；缺失时返回 `[]` |

## Acceptance Criteria

1. `EventRecord` schema 允许并默认输出 `evidence_breakdown=[]`。
2. 有 sentiment article 新字段且 index 正确时，事件记录包含对应 breakdown item。
3. 旧 sentiment schema、fallback、空文章、配置关闭时均返回 `[]`。
4. `evidence_items` 行为不变。
5. `evidence_profile` / `ai_probability` / `actionable_recommendation` 的现有测试无回归。
6. 禁词测试覆盖 `evidence_breakdown[*].rationale_zh`。
7. 目标测试通过：
   - `python -m pytest backend/tests/test_news_sentiment_service.py`
   - `python -m pytest backend/tests/test_evidence_aggregation_service.py`
   - `python -m pytest backend/tests/test_event_intelligence_service.py`

## Out of Scope

- 前端展示 `evidence_breakdown`
- 用 `evidence_breakdown` 重写 `actionable_recommendation.rationale`
- 用文章级 breakdown 重新计算概率或边际概率
- 替换 `evidence_profile` / `apply_sentiment_fusion`
- 多模型投票、不确定性量化、体育预测引擎增强

## Open Questions

无阻塞问题。本文已将模型命名、真实集成点、失败路径和测试边界固定下来，可进入 implementation plan。
