# 生产就绪缺失项分析

**文档版本**: v1.0  
**创建日期**: 2026-06-30  
**状态**: Phase 5 已完成，Phase 1-4 + 运维工具待实现

---

## 📋 执行摘要

Decision Quality Engine 的设计规格已完整（Phase 1-5），其中 **Phase 5 (LLM Telemetry)** 已实现并通过测试。本文档列出从当前状态到生产就绪的所有缺失项，分为三类：

1. **核心功能实现** (Phase 1-4)
2. **监控与可观测性**
3. **数据与运维工具**

---

## 🎯 一、核心功能实现 (Phase 1-4)

### Phase 1: Decision Quality (决策质量层)

**状态**: ❌ 未实现

#### 功能描述
- 证据强度评分 + 冲突降级逻辑
- 为用户提供可解释的决策依据

#### 缺失内容
```python
# backend/app/services/decision_quality_service.py
def build_decision_quality(
    analysis: dict,
    sentiment_profile: dict | None,
    news_context: str,
    enabled: bool = True,
) -> dict | None:
    """
    返回结构:
    {
        "evidence_strength": float,      # 0.0-1.0
        "conflict_score": float,          # 0.0-1.0
        "confidence_downgrade": bool,     # True = 降级
        "explanation": str,               # 用户可见的证据摘要
        "evidence_sources": int,          # 证据来源数量
    }
    """
    pass
```

#### 集成点
- `event_intelligence_service.py:build_event_record()` 
- 在构建 record 时调用，结果写入 `record["decision_quality"]`

#### 单元测试
- `backend/tests/test_decision_quality_service.py` (参考 `test_llm_telemetry_service.py` 的结构)

#### 特性开关
- `.env`: `DECISION_QUALITY_ENABLED=false` (默认关闭)

---

### Phase 2: Market Quality (市场质量层)

**状态**: ❌ 未实现

#### 功能描述
- 流动性/价差门控
- 过滤不可交易市场 (spread > 10%)

#### 缺失内容
```python
# backend/app/services/market_quality_service.py
def build_market_quality(
    market_probability: float,
    volume_24h: float | None,
    liquidity: float | None,
    enabled: bool = True,
) -> dict | None:
    """
    返回结构:
    {
        "spread_quality": str,            # "excellent" | "good" | "poor"
        "liquidity_tier": str,            # "high" | "medium" | "low"
        "tradability": str,               # "tradable" | "marginal" | "non_tradable"
        "quality_gates": list[str],       # 触发的门控规则
    }
    """
    pass
```

#### 前置需求
- **Market API 数据完整性**：需要从 Manifold/Polymarket API 提取 `spread`, `liquidity`, `volume_24h` 字段
- 当前 `manifold_event_source.py` 只取 `probability`，需增强数据提取

#### 集成点
- `event_intelligence_service.py:build_event_record()`
- 结果写入 `record["market_quality"]`

#### 单元测试
- `backend/tests/test_market_quality_service.py`

#### 特性开关
- `.env`: `MARKET_QUALITY_ENABLED=false`

---

### Phase 3: Prediction Calibration (预测校准层)

**状态**: ❌ 未实现

#### 功能描述
- 校准追踪 + Brier score 计算
- 依赖现有 `calibration_summary` 表

#### 缺失内容
```python
# backend/app/services/prediction_calibration_service.py
def build_prediction_outcome(
    event_id: str,
    ai_probability: float,
    market_probability: float,
    enabled: bool = True,
) -> dict | None:
    """
    返回结构:
    {
        "calibration_status": str,        # "calibrated" | "uncalibrated_provisional" | "overconfident"
        "segment_history": dict | None,   # 来自 calibration_summary 的分段统计
        "brier_score": float | None,      # 当前预测的 Brier score (需要真实结果后回填)
        "expected_brier": float | None,   # 基于历史的期望 Brier score
    }
    """
    pass
```

#### 数据依赖
- `calibration_summary` 表的分段统计 (已存在)
- 需要读取对应 segment 的历史校准数据

#### 集成点
- `event_intelligence_service.py:build_event_record()`
- 结果写入 `record["prediction_outcome"]`

#### 单元测试
- `backend/tests/test_prediction_calibration_service.py`

#### 特性开关
- `.env`: `PREDICTION_CALIBRATION_ENABLED=false`

---

### Phase 4: Source Reliability (来源可靠性层)

**状态**: ❌ 未实现

#### 功能描述
- 来源多样性评分 + 分层分类
- 区分主流媒体 / 专业博客 / 社交媒体

#### 缺失内容
```python
# backend/app/services/source_reliability_service.py
def build_source_reliability(
    news_articles: list[dict],
    enabled: bool = True,
) -> dict | None:
    """
    返回结构:
    {
        "domain_diversity": float,        # 0.0-1.0, 来源域名的多样性
        "source_tier_distribution": dict, # {"tier_1": 3, "tier_2": 2, "tier_3": 1}
        "reliability_score": float,       # 0.0-1.0, 综合可靠性
        "dominant_source": str | None,    # 占比 >50% 的域名 (红旗)
    }
    """
    pass
```

#### 前置需求
- **域名分层数据**: `backend/data/source_tier_mapping.json`
  ```json
  {
    "tier_1": ["reuters.com", "apnews.com", "bbc.com", ...],
    "tier_2": ["techcrunch.com", "arstechnica.com", ...],
    "tier_3": ["twitter.com", "reddit.com", ...],
    "tier_rules": {
      "social_media_keywords": ["twitter", "reddit", "facebook"]
    }
  }
  ```

#### 集成点
- `event_intelligence_service.py:build_event_record()`
- 结果写入 `record["source_reliability"]`

#### 单元测试
- `backend/tests/test_source_reliability_service.py`

#### 特性开关
- `.env`: `SOURCE_RELIABILITY_ENABLED=false`

---

## 🔍 二、监控与可观测性

### 2.1 质量指标仪表板

**状态**: ❌ 未实现

#### 需求描述
实时监控质量引擎的健康状况和使用情况

#### 缺失内容

**前端组件**: `frontend/src/components/dashboard/quality-metrics.tsx`
```typescript
interface QualityMetrics {
  phase1_enabled_rate: number;      // Phase 1 启用事件占比
  phase2_non_tradable_rate: number; // Phase 2 标记不可交易的比率
  phase3_calibrated_rate: number;   // Phase 3 校准事件占比
  phase4_low_diversity_rate: number;// Phase 4 低多样性警告比率
  phase5_degraded_rate: number;     // Phase 5 降级比率
  llm_cost_per_event: number;       // 平均每事件 LLM 成本
  total_token_cost_24h: number;     // 24h 总 token 成本
}
```

**后端 API**: `backend/app/api/routes/quality_metrics.py`
```python
@router.get("/quality-metrics")
async def get_quality_metrics(timeframe: str = "24h"):
    """
    聚合 event_store 中的质量数据:
    - 遍历所有事件，统计各 overlay 字段
    - 计算降级率、成本总和
    - 返回 QualityMetrics 结构
    """
    pass
```

#### 可视化需求
- 降级率时间序列图 (Recharts)
- LLM 成本趋势
- 校准漂移预警 (Brier score 突增 >0.1)

---

### 2.2 质量层性能追踪

**状态**: ❌ 未实现

#### 需求描述
确保每个 overlay 的计算耗时符合纯函数承诺 (<10ms)

#### 缺失内容

**性能装饰器**: `backend/app/utils/performance.py`
```python
import time
from functools import wraps

def track_overlay_perf(phase_name: str):
    """记录 overlay 函数的执行时间"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = func(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - start) * 1000
            # 写入 Prometheus / CloudWatch / logging
            logger.info(f"{phase_name} took {elapsed_ms:.2f}ms")
            return result
        return wrapper
    return decorator
```

**应用到所有服务**:
```python
@track_overlay_perf("decision_quality")
def build_decision_quality(...):
    pass
```

---

### 2.3 特性开关 A/B 对比

**状态**: ❌ 未实现

#### 需求描述
比较启用/禁用某个 Phase 时的系统表现

#### 缺失内容

**分析脚本**: `backend/scripts/analyze_feature_flag_impact.py`
```python
def compare_phase_impact(phase: str, event_sample: int = 1000):
    """
    1. 随机抽取 N 个事件
    2. 分别以 enabled=True/False 重新计算 overlay
    3. 对比 recommendation.direction 的变化率
    4. 输出报告: "Phase 1 启用后，17% 的 YES 建议变为 WAIT"
    """
    pass
```

---

## 🧪 三、集成验证

### 3.1 端到端质量测试

**状态**: ❌ 未实现

#### 测试文件
`backend/tests/test_decision_quality_engine_integration.py`

#### 测试用例
```python
class TestQualityEngineIntegration(unittest.TestCase):
    
    def test_all_phases_enabled_merge_correctly(self):
        """五层 overlay 同时启用时，字段不冲突"""
        pass
    
    def test_most_strict_direction_wins(self):
        """Phase 1: AVOID, Phase 2: WAIT → 最终 AVOID"""
        pass
    
    def test_overlay_independence(self):
        """llm_telemetry 降级不影响 decision_quality 输出"""
        pass
    
    def test_all_phases_disabled_backward_compatible(self):
        """所有开关关闭时，输出与 Phase 5 前一致"""
        pass
```

---

### 3.2 降级场景覆盖

**状态**: ❌ 未实现

#### 测试用例
```python
class TestDegradedModeScenarios(unittest.TestCase):
    
    def test_all_phases_degraded_still_produces_recommendation(self):
        """LLM 全面失效时，deterministic_fallback 仍可用"""
        pass
    
    def test_partial_degradation_does_not_block_pipeline(self):
        """Phase 2 降级，其他 Phase 正常工作"""
        pass
```

---

## 📊 四、数据质量保障

### 4.1 历史数据回填

**状态**: ❌ 未实现

#### 问题描述
现有 `event_store.json` 中的事件没有质量 overlay，导致历史数据缺失新字段

#### 解决方案

**迁移脚本**: `backend/scripts/backfill_quality_overlays.py`
```python
def backfill_quality_overlays(dry_run: bool = True):
    """
    1. 读取 event_store.json
    2. 对每个事件:
       - 调用 build_decision_quality(...)
       - 调用 build_market_quality(...)
       - 调用 build_prediction_outcome(...)
       - 调用 build_source_reliability(...)
       - 调用 build_llm_telemetry(...)
    3. 写回新的 overlay 字段
    4. dry_run=False 时才真正写入
    """
    pass
```

#### 执行计划
- 在 Phase 1-4 全部实现后执行
- 估计耗时: ~1000 事件 × 50ms = 50 秒
- 需要备份原始 `event_store.json`

---

### 4.2 Schema 演进管理

**状态**: ❌ 未实现

#### 问题描述
旧事件缺少新字段，读取时可能报错

#### 解决方案

**版本标记**: 在每个 event record 中加入 `schema_version`
```python
{
  "event_id": "manifold-12345",
  "schema_version": "v2.1",  # v2.0: Phase 5, v2.1: Phase 1-4
  "decision_quality": {...},
  ...
}
```

**向后兼容读取**: `backend/app/services/event_store.py`
```python
def normalize_event_record(record: dict) -> dict:
    """填充缺失字段的默认值"""
    schema_version = record.get("schema_version", "v1.0")
    
    if schema_version < "v2.0":
        record.setdefault("llm_telemetry", None)
    
    if schema_version < "v2.1":
        record.setdefault("decision_quality", None)
        record.setdefault("market_quality", None)
        record.setdefault("prediction_outcome", None)
        record.setdefault("source_reliability", None)
    
    return record
```

---

## 🎛️ 五、运维工具

### 5.1 质量诊断命令

**状态**: ❌ 未实现

#### 工具描述
单事件的五层分解视图，快速定位质量问题

#### 实现

**CLI 入口**: `backend/app/cli/diagnose.py`
```python
import click

@click.command()
@click.argument("event_id")
def diagnose_quality(event_id: str):
    """
    输出格式:
    
    Event: manifold-12345 (世界杯决赛)
    ───────────────────────────────────────────
    
    📊 Phase 1: Decision Quality
       ✅ Enabled
       evidence_strength: 0.82
       conflict_score: 0.15
       explanation: "3 条独立消息源支持，无重大冲突"
    
    📊 Phase 2: Market Quality
       ❌ Degraded (missing spread data)
       tradability: unknown
    
    ... (其余 Phase)
    """
    pass
```

---

### 5.2 批量质量审计

**状态**: ❌ 未实现

#### 工具描述
扫描 predictions 表，检查 overlay 字段冲突

#### 实现

**审计脚本**: `backend/scripts/audit_quality_consistency.py`
```python
def audit_quality_consistency():
    """
    检查:
    1. market_quality.tradability="non_tradable" 但有 trade_id
    2. decision_quality.confidence_downgrade=True 但 confidence="high"
    3. llm_telemetry.degraded_mode=True 但 analysis_quality="llm"
    
    输出不一致的记录列表
    """
    pass
```

---

## 🔗 六、外部依赖健壮性

### 6.1 Market API 数据完整性检查

**状态**: ❌ 未实现

#### 问题描述
Phase 2 需要 `spread` 和 `liquidity` 字段，但 Manifold API 可能不返回

#### 解决方案

**数据验证器**: `backend/app/utils/market_data_validator.py`
```python
def validate_market_data(market_dict: dict) -> dict:
    """
    检查必需字段:
    - probability: float (必需)
    - volume_24h: float | None (可选)
    - liquidity: float | None (可选)
    - spread: float | None (可选，计算自 bid/ask)
    
    返回:
    {
        "valid": bool,
        "missing_fields": list[str],
        "usable_for_phase2": bool,
    }
    """
    pass
```

**集成到 event source**:
```python
# manifold_event_source.py
for market in api_response:
    validation = validate_market_data(market)
    if not validation["usable_for_phase2"]:
        logger.warning(f"Market {market['id']} missing spread data")
```

---

### 6.2 News Source 分层数据

**状态**: ❌ 未实现

#### 问题描述
Phase 4 需要域名→tier 映射表

#### 解决方案

**数据文件**: `backend/data/source_tier_mapping.json`
```json
{
  "tier_1": [
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "nytimes.com",
    "wsj.com",
    "bloomberg.com",
    "ft.com"
  ],
  "tier_2": [
    "techcrunch.com",
    "arstechnica.com",
    "theverge.com",
    "wired.com",
    "zdnet.com"
  ],
  "tier_3": [
    "twitter.com",
    "reddit.com",
    "facebook.com",
    "medium.com"
  ],
  "tier_rules": {
    "social_media_keywords": ["twitter", "reddit", "facebook", "t.co"],
    "blog_keywords": ["blogspot", "wordpress", "substack"]
  }
}
```

**加载器**: `backend/app/services/source_reliability_service.py`
```python
import json

_TIER_MAPPING = None

def _load_tier_mapping():
    global _TIER_MAPPING
    if _TIER_MAPPING is None:
        with open("backend/data/source_tier_mapping.json") as f:
            _TIER_MAPPING = json.load(f)
    return _TIER_MAPPING

def classify_source_tier(domain: str) -> str:
    """返回 "tier_1" | "tier_2" | "tier_3" | "unknown" """
    mapping = _load_tier_mapping()
    # ... 匹配逻辑
```

---

## 📅 实施优先级

### 🔴 P0 - 立即需要 (Phase 1-4 实现前置条件)

1. **News Source 分层数据** (§6.2) — Phase 4 无法实现前必须有
2. **Market API 数据验证器** (§6.1) — Phase 2 降级率可能达 100%
3. **端到端集成测试** (§3.1) — 确保 Phase 1-4 实现后不冲突

### 🟡 P1 - Phase 1-4 完成后立即执行

4. **历史数据回填** (§4.1) — 旧事件缺失新字段
5. **Schema 演进管理** (§4.2) — 向后兼容读取
6. **质量指标仪表板** (§2.1) — 监控降级率和成本

### 🟢 P2 - 长期改进

7. **质量诊断命令** (§5.1) — 调试工具
8. **批量质量审计** (§5.2) — 数据一致性检查
9. **质量层性能追踪** (§2.2) — 性能监控
10. **特性开关 A/B 对比** (§2.3) — 效果验证

---

## 📊 工作量估算

| 类别 | 子项数量 | 预估人日 |
|------|---------|---------|
| Phase 1-4 实现 | 4 个服务 + 4 个测试文件 | 8-10 人日 |
| 监控仪表板 | 前端组件 + 后端 API | 3-4 人日 |
| 数据回填 + Schema 管理 | 2 个脚本 | 2 人日 |
| 运维工具 | 诊断命令 + 审计脚本 | 2 人日 |
| 外部依赖健壮性 | 验证器 + 数据文件 | 1 人日 |
| **总计** | | **16-19 人日** |

---

## ✅ 验收标准

### Phase 1-4 实现完成
- [ ] 4 个服务文件各有完整的纯函数实现
- [ ] 4 个测试文件覆盖率 >90%
- [ ] 集成测试通过 (§3.1)
- [ ] 所有特性开关默认关闭

### 监控就绪
- [ ] 质量指标仪表板上线
- [ ] 降级率监控预警 >5%
- [ ] LLM 成本日报自动生成

### 数据完整性
- [ ] 历史事件回填完成 (schema_version="v2.1")
- [ ] 向后兼容读取测试通过
- [ ] source_tier_mapping.json 覆盖 >100 个主流域名

### 运维工具可用
- [ ] `python -m app.cli.diagnose_quality <event_id>` 正常运行
- [ ] 质量审计脚本发现 0 个冲突 (或全部修复)

---

## 🔄 与现有设计文档的关系

本文档是 `2026-06-30-decision-quality-engine-design.md` 的**实施清单补充**:

- **设计文档**: 定义了 WHAT (Phase 1-5 的架构和字段规格)
- **本文档**: 定义了 HOW (具体实现路径) 和 MISSING (生产就绪的补全项)

两份文档配合使用:
1. 先读设计文档，理解架构和纯函数约束
2. 再读本文档，按优先级实施

---

## 📝 变更记录

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-06-30 | 初始版本 — Phase 5 完成后的系统缺失分析 |

---

**文档所有者**: 系统架构组  
**审核状态**: 待审核  
**下次更新**: Phase 1 实现完成后
