# Evidence Decomposition Design Spec

**Date:** 2026-06-29
**Status:** Draft (pending user review)
**Scope:** AI probability engine analysis capability enhancement
**Branch target:** `fix/v0.3.0-hardening` (or new feature branch off it)

## Why

当前 AI 概率引擎把所有新闻拼成一坨字符串丢给 LLM,LLM 输出一个概率 + 一段 reasoning。三个问题:

1. **黑盒** — 无法知道哪篇文章贡献了多少
2. **rationale 不可追溯** — Stage 3 的 `actionable_recommendation.rationale` 引用 LLM 编的 reasoning,无法对应到具体证据
3. **evidence_profile 不可信** — 关键词规则在集合层面算 direction/strength,丢失文章级粒度

本 spec 通过扩展现有 `analyze_sentiment` LLM 调用的输出 schema,产出可审计的 `evidence_breakdown` 字段。

## Goal

新增 `evidence_breakdown: list[EvidenceItem]` 字段到 `EventRecord`,记录每篇文章对最终结论的贡献 (direction / strength / credibility / rationale)。

**不改变**:`ai_probability` 的推理路径、`evidence_profile` 关键词规则、回归层行为、前端展示。

## Architecture

### 数据流

```
filter_news_for_market → filtered["articles"] (结构化列表)
                            ↓
              analyze_sentiment (扩展输出 schema)
                  ↓ 每篇文章输出 evidence_direction/strength/credibility/rationale_zh
                            ↓
       aggregate_evidence_breakdown (新建纯函数,确定性可单测)
                  ↓ 过滤 neutral + low strength,关联 source/title,过禁词
                            ↓
              evidence_breakdown → EventRecord
```

### 关键设计决策

1. **扩展而非新增 LLM 调用** — `analyze_sentiment` 已经在 `_build_filtered_news` 里跑过,扩展输出字段几乎零成本 (输入 token 不变,输出 token +30%)
2. **不动 `evidence_profile`** — 关键词规则保留,`regression_to_market` 的 8 维惩罚不变
3. **不输出 `marginal_p`** — LLM 不擅长贝叶斯推理,只输出定性 direction + strength
4. **不动 `_ask_ai` prompt** — 解耦,避免 LLM 链路依赖 (sentiment LLM 失败 ≠ _ask_ai 失败)
5. **不动前端** — 遵守硬约束 "Frontend pages must not be modified during engine optimization work"
6. **`evidence_breakdown` 默认 `[]`** — 向后兼容,旧调用者不受影响

## Component Design

### 1. `analyze_sentiment` 扩展 (news_sentiment_service.py)

**当前输出 schema** (每篇文章):
```json
{
  "index": 0,
  "sentiment": "positive|negative|neutral",
  "impact": "high|medium|low",
  "key_facts": ["..."],
  "relevance_to_question": 0.0-1.0
}
```

**扩展后** (加 4 个字段):
```json
{
  "index": 0,
  "sentiment": "positive|negative|neutral",
  "impact": "high|medium|low",
  "key_facts": ["..."],
  "relevance_to_question": 0.0-1.0,
  "evidence_direction": "support|oppose|neutral",
  "evidence_strength": 0.0-1.0,
  "source_credibility": 0.0-1.0,
  "rationale_zh": "一句中文,解释 direction+strength 评估"
}
```

**集合层输出不变**:`overall_direction` / `overall_strength` / `conflict_level` / `summary`

### 2. Prompt 改动

在 `_SYSTEM_PROMPT` 的输出 schema 描述里加 4 个字段 + 分析指令:

```
For each article, also assess:
5. evidence_direction: does this article support or oppose the YES outcome?
   (support | oppose | neutral) — based on concrete facts, not tone
6. evidence_strength: how strongly does this article move the probability?
   (0.0-1.0) — consider specificity, directness, source authority
7. source_credibility: how trustworthy is this source for this topic?
   (0.0-1.0) — official/government > reuters/AP > aggregators > blogs
8. rationale_zh: one Chinese sentence explaining your direction+strength assessment
   Use event vocabulary (YES/NO/支持/反对). Do NOT use trading terms (long/short/buy/sell).
```

### 3. 聚合函数 (evidence_aggregation_service.py 新建)

纯函数,无 LLM,确定性可单测。

```python
def aggregate_evidence_breakdown(
    sentiment_articles: list[dict],
    original_articles: list[dict],
) -> list[dict]:
    """Transform LLM sentiment output into evidence_breakdown field.
    
    Filters:
    - Skip articles with direction == "neutral" (no contribution)
    - Skip articles with strength < 0.2 (noise threshold)
    - Skip articles with malformed/missing index
    
    Applies:
    - Clamp strength/credibility to [0, 1]
    - 禁词 filter on rationale_zh (replace LONG/SHORT → YES/NO direction)
    - Truncate title to 200 chars
    """
```

**输出结构** (每个 item):
```python
{
    "source": "Reuters",          # 从 original_articles[idx].source 取
    "title": "Fed signals...",    # 从 original_articles[idx].title 取
    "direction": "support",       # 直接映射 LLM 的 evidence_direction
    "strength": 0.8,              # 直接映射 + clamp
    "credibility": 0.9,           # 直接映射 + clamp
    "rationale_zh": "..."         # 过禁词后
}
```

**关联逻辑**:用 `sentiment_articles[i]["index"]` 作为 join key,从 `original_articles[index]` 取 source/title。

**不排序**:保持 LLM 输出顺序 (通常按 relevance)。

### 4. EventRecord schema (models/event.py)

```python
class EvidenceItem(BaseModel):
    """Single article's contribution to the evidence breakdown."""
    source: str
    title: str
    direction: str  # support | oppose | neutral
    strength: float  # 0-1
    credibility: float  # 0-1
    rationale_zh: str

class EventRecord(BaseModel):
    ...
    evidence_breakdown: list[EvidenceItem] = []  # 默认空列表,向后兼容
```

### 5. 集成点 (event_intelligence_service.py)

`build_event_record` 里新增:

```python
from app.services.evidence_aggregation_service import aggregate_evidence_breakdown

# 在 build_event_record 内,analysis 之后
sentiment_profile = filtered_news.get("sentiment_profile", {}) or {}
evidence_breakdown = aggregate_evidence_breakdown(
    sentiment_articles=sentiment_profile.get("articles", []),
    original_articles=filtered_news.get("articles", []),
) if settings.EVIDENCE_BREAKDOWN_ENABLED else []

return {
    ...
    "legacy_analysis": analysis,
    "evidence_breakdown": evidence_breakdown,  # 新增
    ...
}
```

### 6. 配置项 (config.py)

```python
EVIDENCE_BREAKDOWN_ENABLED: bool = _env_bool(
    "EVIDENCE_BREAKDOWN_ENABLED", "true"
)
```

关闭时 `evidence_breakdown` 为空列表,行为退回当前。

## File List

| 文件 | 类型 | 改动 |
|------|------|------|
| `backend/app/services/news_sentiment_service.py` | 改 | 扩展 `_SYSTEM_PROMPT` + 输出 schema |
| `backend/app/services/evidence_aggregation_service.py` | 新建 | 聚合纯函数 |
| `backend/app/services/event_intelligence_service.py` | 改 | `build_event_record` 加 `evidence_breakdown` |
| `backend/app/models/event.py` | 改 | 加 `EvidenceItem` + `EventRecord.evidence_breakdown` |
| `backend/app/core/config.py` | 改 | 加 `EVIDENCE_BREAKDOWN_ENABLED` |
| `backend/.env.example` | 改 | 文档化新配置 |
| `backend/tests/test_news_sentiment_service.py` | 改 | 扩展输出结构校验 |
| `backend/tests/test_evidence_aggregation_service.py` | 新建 | 聚合函数单测 |
| `backend/tests/test_event_intelligence_service.py` | 改 | 加 `evidence_breakdown` 字段断言 |

## Testing Strategy

### LLM 部分 (news_sentiment_service)

- 结构校验:输出有 `articles` 数组,每个 item 有新字段
- 类型校验:direction 是 support/oppose/neutral;strength/credibility 是 float
- 范围校验:strength/credibility 在 [0, 1]
- 不测具体数值 (LLM 非确定)

### 聚合函数 (evidence_aggregation_service)

完全可单测:

- neutral direction 被过滤
- strength < 0.2 被过滤
- index 越界 skip
- 空输入返回空列表
- clamp 边界 (strength > 1 → 1)
- 禁词替换 (rationale_zh 含 "LONG" → 替换)
- title 截断到 200 字符
- 关联正确 (source/title 从 original_articles 取)

### 集成测试

- `build_event_record` 返回 dict 包含 `evidence_breakdown` 字段
- `EVIDENCE_BREAKDOWN_ENABLED=false` 时返回空列表
- `analyze_sentiment` 失败 (neutral fallback) 时 `evidence_breakdown` 为空

### 禁词不变量

扩展现有 `test_report_uses_event_vocabulary_only` 或新增测试,验证 `evidence_breakdown[*].rationale_zh` 不含 "long/short/buy/sell/position/kelly/order"。

## Error Handling

| 失败场景 | 行为 |
|---------|------|
| `analyze_sentiment` LLM 调用失败 | 已有 `_neutral_fallback` → `articles=[]` → `evidence_breakdown=[]` |
| `analyze_sentiment` 返回 malformed JSON | 已有 fallback → 同上 |
| LLM 输出缺 `evidence_direction` 字段 | 聚合函数默认 "neutral" → 被过滤 |
| LLM 输出 strength 超范围 | 聚合函数 clamp 到 [0, 1] |
| `index` 字段缺失/越界 | 聚合函数 skip 该 item |
| `rationale_zh` 含禁词 | post-filter 替换 |
| `EVIDENCE_BREAKDOWN_ENABLED=false` | 跳过聚合,返回空列表 |

所有失败路径都降级为 `evidence_breakdown=[]`,**不影响主流程** (`ai_probability` / `evidence_profile` / Stage 3 `actionable_recommendation` 都不依赖这个字段)。

## Backward Compatibility

- `evidence_breakdown` 默认 `[]`,旧调用者不读不受影响
- `analyze_sentiment` 集合层输出 schema 不变,现有读取 `overall_direction/strength` 的代码不受影响
- `evidence_profile` (关键词规则) 完全保留,`regression_to_market` 行为不变
- `_ask_ai` prompt 不动,`ai_probability` 推理路径不变
- `EVIDENCE_BREAKDOWN_ENABLED` 默认 true,但可通过环境变量关闭

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| LLM 输出新字段不稳定 | temperature=0 + 结构校验 + neutral fallback |
| rationale_zh 泄漏禁词 | prompt 约束 + post-filter 替换 |
| token 成本上升 | 输入不变,输出 +30%,实测后可降级 (如只对 top-3 文章要求新字段) |
| `analyze_sentiment` 失败 | 已有 neutral fallback,`evidence_breakdown` 降级为空 |
| 前端展示需求 | 本次不做,字段先产出 (遵守硬约束) |

## Out of Scope (YAGNI)

以下不在本次 spec 范围内,留作后续工作:

- `confidence_decomposition` (4 维置信度分解) — Stage 3 只需 high/medium/low
- `marginal_p` (LLM 输出边际概率) — LLM 不擅长,聚合层用规则
- 替换关键词 `evidence_profile` — 回归层不动
- 前端展示 `evidence_breakdown` — 遵守硬约束
- Stage 3 `actionable_recommendation.rationale` 合成优化 — 现在能用,先不动
- 多模型投票 / 不确定性量化 — 独立子项目
- 体育预测引擎 (`world_cup_engines`) 增强 — 独立子项目

## Open Questions

无。所有关键决策已通过自辩确认。
