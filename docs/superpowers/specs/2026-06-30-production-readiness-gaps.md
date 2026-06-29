# 生产就绪缺失项分析

**文档版本**: v2.0
**创建日期**: 2026-06-30
**最近更新**: 2026-06-30
**状态**: Decision Quality Engine Phase 1-5 全部实现并提交；可观测性、部署自动化、运维工具、数据层补全仍有缺失

---

## 📋 执行摘要

Decision Quality Engine 的设计规格（[2026-06-30-decision-quality-engine-design.md](./2026-06-30-decision-quality-engine-design.md)）已完整定义 Phase 1-5，且**五个 Phase 均已实现并通过测试**（见 §一）。本文档列出从当前状态到生产就绪的真实缺失项，分为六类：

1. **核心功能实现状态**（Phase 1-5 均已完成，本节记录实现位置与限制）
2. **监控与可观测性**（最大短板：无指标暴露、无错误追踪、无日志聚合）
3. **部署与运维**（无 CD、无多环境配置、无 Secrets 管理、无反代示例、无 restore 脚本）
4. **数据层与 Schema 演进**（部分 store 缺列级迁移、event_store.json 无版本、死代码字段）
5. **测试覆盖与运维工具**（三路合并缺集成测试、运维 CLI/审计脚本全缺）
6. **前端工程与文档一致性**（前后端类型手工同步、README 缺生产部署示例）

---

## 🎯 一、核心功能实现状态 (Phase 1-5)

> **重要修正**：v1.0 版本误将 Phase 1-4 标注为"未实现"。实际代码验证显示四个服务文件均已存在、有完整 docstring 与单元测试，且 `.env.example` 第 57-126 行已包含全部 5 个 Phase 的特性开关。

### Phase 1: Decision Quality (决策质量层) — ✅ 已实现

**实现位置**: `backend/app/services/decision_quality_service.py`
**Commit**: `a710961`, `8814af7`, `64dfbde`, `802c29e`
**测试**: `backend/tests/test_decision_quality_service.py`（含禁词过滤测试）
**特性开关**: `DECISION_QUALITY_ENABLED=false`（默认 OFF，byte-identical 到 pre-Phase-1）

**已实现的契约**：
- `build_decision_quality(analysis, sentiment_profile, news_context, enabled) -> dict | None`
- 纯函数：无 LLM / 无 IO
- 输入：`actionable_recommendation + evidence_breakdown`
- 输出：`raw_direction` / `displayed_direction` / `downgrade_reason` / `supporting_evidence` / `opposing_evidence` / `consensus_level` / `conflict_score`
- 4 条降级规则：证据缺失→WAIT、冲突高→WAIT、官方反向→WAIT、高风险→AVOID
- 禁词过滤：long/short/buy/sell/position/kelly/order（case-insensitive）

**已知限制**：
- 依赖 `actionable_recommendation` 与 `evidence_breakdown` 字段，二者缺失时返回 None
- `consensus_level` 公式在 strength vs count 失衡时有限制（spec 注明 Phase 3 重评估）

---

### Phase 2: Market Quality (市场质量层) — ✅ 已实现

**实现位置**: `backend/app/services/market_quality_service.py`
**Commit**: `104fb91`，wide_spread 硬截止修复在 `0b69624`
**测试**: `backend/tests/test_market_quality_service.py`（44 测试，含 7 个 wide_spread 回归）
**特性开关**: `MARKET_QUALITY_ENABLED=false`

**已实现的契约**：
- `build_market_quality(market_quote, source_type, enabled, max_spread_pct, min_liquidity, ...) -> dict | None`
- 仅 `source.type == "prediction_market"` 适用（Metaculus/open_web/sports_event 跳过）
- 评分维度：spread / liquidity / volume → 综合分数
- **wide_spread_flag 硬截止**（v2.0 修复）：spread > max_spread_pct 时强制降级为 WAIT，独立于聚合分数，防止健康流动性掩盖不可交易价差
- `merge_quality_overlays()` 三路合并（dq + mq + sr）：most-strict-direction-wins

**已知限制**：
- `stale_price_flag` 永远为 None（所有 adapter 都不暴露 `last_updated`，死代码）
- 依赖外部 API 返回 spread/liquidity/volume 字段完整性

---

### Phase 3: Prediction Calibration (预测结果校准层) — ✅ 已实现

**实现位置**: `backend/app/services/prediction_calibration_service.py`
**Commit**: `c79b62c`，backfill 脚本在 `0b69624`
**测试**: `backend/tests/test_prediction_calibration_service.py`（54 单元 + 11 集成）
**特性开关**: `PREDICTION_CALIBRATION_ENABLED=false`
**配套脚本**: `backend/scripts/backfill_prediction_snapshots.py`（已支持 `--dry-run`，已修复未解包 event_store entry 的 bug）

**已实现的契约**：
- 5 个纯函数：`compute_edge_bucket` / `compute_confidence_bucket` / `compute_direction_correct` / `build_prediction_snapshot` / `build_resolution_buckets`
- Edge bucket 半开区间：`[0,5) / [5,10) / [10,20) / [20,+inf)`，边界值归上 bucket
- `direction_correct` 独立于 Brier score（区分方向准确度 vs 概率准确度）
- prediction_store schema 版本 3→4，新增 10 列 snapshot 字段
- `freeze_prediction`（捕获 snapshot）+ `score_prediction`（计算 bucket）+ `calibration_bucket_summary`（聚合）
- `ON_CONFLICT(event_id) DO NOTHING` 保证 first commitment 冻结

**已知限制**：
- pre-Phase-3 frozen predictions 必须用 `backfill_prediction_snapshots.py` 一次性回填（spec §1616-1621 要求）
- snapshot 缺失时 `direction_correct` 为 None，但仍计入 calibration aggregate

---

### Phase 4: Source Reliability (来源可靠性层) — ✅ 已实现

**实现位置**: `backend/app/services/source_reliability_service.py`
**Commit**: `8764392`
**测试**: `backend/tests/test_source_reliability_service.py`（46 单元 + 13 集成 + 2 透传）
**特性开关**: `SOURCE_RELIABILITY_ENABLED=false`

**已实现的契约**：
- 3 个纯函数：`extract_domain`（URL→域名）/ `classify_source_tier`（5 级分类：official/trusted/established/aggregator/unknown）/ `build_source_reliability`（加权评分 + 4 条降级规则）
- `merge_quality_overlays` 在 Phase 4 后扩展为 4-tuple 返回 `(direction, reason, market_applied, source_applied)`
- `downgrade_reason` 使用中文模板，禁词 invariant 一致
- 仅 `evidence_breakdown` 非空的事件适用

**已知限制**：
- tier 分类是启发式，未用 Public Suffix List（`bbc.co.uk` 不会被简化为 `bbc`）
- 不依赖外部 `source_tier_mapping.json` 数据文件，tier 判定逻辑硬编码在 `classify_source_tier` 内

---

### Phase 5: LLM Cost & Stability Telemetry — ✅ 已实现

**实现位置**: `backend/app/services/llm_telemetry_service.py`
**Commit**: `660655b`
**测试**: `backend/tests/test_llm_telemetry_service.py`（35 单元）+ `backend/tests/test_event_intelligence_service.py`（13 集成）
**特性开关**: `LLM_TELEMETRY_ENABLED=false`

**已实现的契约**：
- 混合方案：最小埋点（`_ask_ai` 捕获 `response.usage` → `_llm_usage` 私有键）+ 纯函数 overlay（`build_llm_telemetry` 聚合 `analysis_quality` + `llm_usage` + `sentiment_profile.fallback` + 定价表 → 结构化遥测块）
- 7 个常见模型定价表（gpt-4o-mini/4o/4-turbo/4/3.5-turbo/deepseek-chat/deepseek-reasoner）+ 保守默认 $0.005/1K tokens
- 成本估算：有真实 token 时精确计算；降级时用 chars/4 启发式

**关键设计差异**：
- 纯观测层 — **不参与 `merge_quality_overlays`**，不产生 `suggested_direction` / `downgrade_reason`
- 适用于所有事件（不受 source.type 限制）

**已知限制**：
- `llm_call_count` 是保守下界（无法检测 `translate_title` 调用）
- 前端无聚合仪表板（运维看不到日/周成本曲线）

---

### 1.6 三路合并语义（已实现）

**位置**: `market_quality_service.py::merge_quality_overlays`
**集成点**: `event_intelligence_service.py::analyze_event`
**报告透传**: `decision_report_service.py`

- 三路并行：dq + mq + sr 各自独立计算（互不依赖）
- severity 排序：YES/NO=0 < WAIT=1 < AVOID=2
- 最高 severity 方向胜出（most-strict-direction-wins）
- 多方并列时 reasons 用 ` | ` 拼接
- `final_displayed_direction` + `final_downgrade_reason` 写入 record 顶层

---

## 🔍 二、监控与可观测性（最大短板）

### 2.1 应用指标暴露 — ❌ 完全空白

**问题**：生产无法告警、无法看趋势、无法定位降级原因。

**现状**：
- `backend/requirements.txt` 仅 15 个依赖，无 `prometheus_client` / `sentry-sdk` / `opentelemetry`
- 无 `/metrics` 端点
- spec 自己定义的 5 个核心指标无处落盘：
  - `decision_quality_downgrade_rate`
  - `consensus_distribution`
  - `rule_fire_count`
  - `build_failure_count`
  - `latency_ms`

**缺失内容**：

```python
# backend/app/api/routes/quality_metrics.py  (不存在)
@router.get("/api/quality-metrics")
async def get_quality_metrics(timeframe: str = "24h"):
    """聚合 event_store 中的质量数据，统计降级率、成本总和"""
    pass

# backend/app/utils/metrics.py  (不存在)
from prometheus_client import Counter, Histogram, Gauge

DECISION_DOWNGRADE = Counter(
    "pmrf_decision_quality_downgrade_total",
    "Phase 1 降级次数",
    ["reason"]
)
OVERLAY_LATENCY = Histogram(
    "pmrf_overlay_latency_ms",
    "各 overlay 计算耗时",
    ["phase"],
    buckets=(1, 5, 10, 20, 50, 100)
)
```

**前端可视化**：`frontend/src/components/dashboard/quality-metrics.tsx`（不存在）— 应展示降级率时间序列、LLM 成本趋势、Brier score 漂移预警

---

### 2.2 错误追踪 — ❌ 未实现

**问题**：线上偶发错误会丢失上下文。

**现状**：
- 前端 JS 错误仅 `console.error`，无 Sentry SDK
- 后端异常仅落本地 `logs/app.log`，无 stack trace 上报

**缺失内容**：
- 后端集成 `sentry-sdk`，捕获 FastAPI 异常 + scheduler 异常
- 前端集成 `@sentry/nextjs`，捕获路由级与全局错误
- 在 `.env` 增加 `SENTRY_DSN` 配置项

---

### 2.3 日志聚合 — ❌ 未实现

**问题**：多实例部署时只能 SSH `tail` 日志。

**现状**：
- `backend/app/core/logging.py` 仅写本地文件（10MB×5 轮转）
- 无 JSON 结构化日志
- 无 log shipping（Loki/ELK/Datadog 都没接）

**缺失内容**：
- 改用 `structlog` 输出 JSON 结构化日志
- 增加 log shipping 配置示例（ Loki / Filebeat）
- 在 docker-compose 增加 Loki + Grafana sidecar（可选）

---

### 2.4 质量层性能追踪 — ❌ 未实现

**问题**：spec 承诺纯函数 <5ms / 100 证据 <20ms，但无运行时验证。

**缺失内容**：

```python
# backend/app/utils/performance.py  (不存在)
import time
from functools import wraps

def track_overlay_perf(phase_name: str):
    """记录 overlay 函数的执行时间，输出到 Prometheus + 日志"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                OVERLAY_LATENCY.labels(phase=phase_name).observe(elapsed_ms)
                if elapsed_ms > 100:
                    logger.warning(f"{phase_name} SLOW: {elapsed_ms:.2f}ms")
        return wrapper
    return decorator
```

应用到所有 5 个 overlay 服务的 build 函数。

---

### 2.5 特性开关 A/B 对比 — ❌ 未实现

**问题**：无法量化"启用某 Phase 后，多少 YES 变成 WAIT"。

**缺失内容**：

```python
# backend/scripts/analyze_feature_flag_impact.py  (不存在)
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

## 🚀 三、部署与运维

### 3.1 CD 流程 — ❌ 未实现

**现状**：`.github/workflows/ci.yml` 三个 job（backend-tests / frontend-tests / secret-scan），但：
- 无镜像发布到 registry
- 无 staging/prod 部署自动化
- mypy（256 个已知错误）与 gitleaks 都 `continue-on-error: true`，**实际只挡 lint + 单测 + 构建**

**缺失内容**：
- 增加 `deploy.yml` workflow：构建镜像 → push 到 registry → 部署到 staging（tag 触发）→ 手动 approve → prod
- mypy 与 gitleaks 改为阻塞（先清理 256 个 mypy 错误）

---

### 3.2 多环境配置 — ❌ 未实现

**现状**：单个 `backend/.env`，dev/staging/prod 靠人工区分。

**缺失内容**：
- `.env.staging` / `.env.production` 模板
- `settings` 按 `PMRF_ENV` 环境变量加载不同配置文件
- 至少区分：LLM 模型（dev=便宜模型，prod=生产模型）、特性开关默认值、日志级别、限流策略

---

### 3.3 Secrets 管理 — ❌ 未实现

**现状**：所有密钥（`OPENAI_API_KEY` / `API_WRITE_KEY` / `BACKUP_ENCRYPTION_KEY` / 各源 token）通过 `.env` 文件注入，无 vault 集成。

**风险**：
- `BACKUP_ENCRYPTION_KEY` 等高敏密钥与普通配置同级
- 备份 zip 加密了，但加密 key 本身仍是 env var
- CI gitleaks 非阻塞

**建议方案**（按复杂度排序）：
1. **最低**：用 SOPS + age 加密 `.env` 文件提交到 git
2. **中等**：用 Doppler / AWS Parameter Store 注入
3. **完整**：集成 HashiCorp Vault

---

### 3.4 反代 / TLS 配置示例 — ❌ 未提供

**现状**：`deploy/docker-compose.yml` 注释明确要求"放 nginx/caddy 在前面"（端口绑到 `127.0.0.1:8000`，禁止直接暴露公网），但仓库**无任何反代 conf 模板**。

**缺失内容**：

```nginx
# deploy/nginx.conf.example  (不存在)
server {
    listen 443 ssl http2;
    server_name pmrf.example.com;

    ssl_certificate /etc/letsencrypt/live/pmrf.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pmrf.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

或等价的 Caddyfile（更简单，自动 TLS）。

---

### 3.5 前端构建未纳入容器 — ❌ 未串联

**现状**：
- `deploy/Dockerfile` 假设 `frontend/out/` 已在外部构建好
- CI 无前端构建 + 镜像打包串联步骤
- 部署者需手动两步构建（`cd frontend && npm run build` → `docker build`）

**缺失内容**：
- Dockerfile 多阶段构建加入 frontend builder stage（`node:22-alpine` → `npm ci && npm run build`）
- 或在 CI 增加"构建前端 → 复制到 backend → 构建 backend 镜像"job

---

### 3.6 备份恢复脚本 — ❌ 未实现

**现状**：`backend/scripts/backup_stores.py` 只生成加密 zip，**无自动 restore 脚本**。灾难恢复需手动解压 + 手动复制文件。

**缺失内容**：

```python
# backend/scripts/restore_stores.py  (不存在)
def restore_from_backup(backup_path: str, target_dir: str, encryption_key: str | None = None):
    """
    1. 解压（必要时用 BACKUP_ENCRYPTION_KEY 解密）
    2. 验证备份完整性（文件清单 + checksum）
    3. 停止服务（warn-only，不自动停）
    4. 备份当前数据到 .pre_restore
    5. 恢复文件
    6. 输出恢复报告
    """
    pass
```

**重要**：恢复操作必须支持 dry-run（先预览会覆盖哪些文件）。

---

## 📊 四、数据层与 Schema 演进

### 4.1 部分 store 缺列级 migration 机制 — ⚠️ 部分实现

**现状**：
- `prediction_store` ✅ 有完整 `_MIGRATIONS` dict + `_SCHEMA_VERSION=4` + `record_schema_version`
- `event_market_link_store` / `loop_run_store` / `optimization_task_store` / `simulated_trade_store` ❌ 只有 `CREATE TABLE IF NOT EXISTS` + `record_schema_version`，新增列需手动 ALTER

**风险**：未来给这些表加列时，旧实例升级后 schema 不变 → 写入会丢字段 / 读取会报错。

**建议**：把 `prediction_store` 的迁移模式抽象到 `sqlite_db.py` 共享工具，所有表统一使用。

---

### 4.2 event_store.json 无 schema 版本 — ❌ 未实现

**现状**：
- 靠 `EventRecord.model_validate` 做写入前校验
- 但 `extra="allow"` 让旧记录 schema 变更后**静默通过**，缺字段也不报错
- 无 `schema_version` 字段标记每条记录的版本

**缺失内容**：

```python
# 在 EventRecord 增加 schema_version: str = "v2.1"
# v2.0: Phase 5; v2.1: Phase 1-4

# backend/app/services/event_store.py 增加 normalize 函数
def normalize_event_record(record: dict) -> dict:
    """填充缺失字段的默认值，按 schema_version 升级"""
    version = record.get("schema_version", "v1.0")
    if version < "v2.0":
        record.setdefault("llm_telemetry", None)
    if version < "v2.1":
        record.setdefault("decision_quality", None)
        record.setdefault("market_quality", None)
        record.setdefault("source_reliability", None)
    record["schema_version"] = "v2.1"
    return record
```

---

### 4.3 死代码字段 — ⚠️ 需清理

**现状**：
- `MarketQuality.stale_price_flag` 永远为 None（所有 adapter 都不暴露 `last_updated`）
- `LLMTelemetry.llm_call_count` 是保守下界（无法检测 `translate_title` 调用）

**建议**：
- `stale_price_flag`：要么在 Polymarket/Kalshi adapter 补 `last_updated`，要么删除该字段并在 docstring 标注"未实现"
- `llm_call_count`：保持现状但 docstring 明确"保守下界"

---

### 4.4 历史数据回填（overlay 层） — ❌ 未实现

**注意**：Phase 3 的 prediction snapshot 已有 `backfill_prediction_snapshots.py`。但 Phase 1/2/4/5 的 overlay 字段在 pre-Phase 事件上仍为空。

**缺失内容**：

```python
# backend/scripts/backfill_quality_overlays.py  (不存在)
def backfill_quality_overlays(dry_run: bool = True):
    """
    1. 读取 event_store.json
    2. 对每个事件调用 5 个 overlay 的 build 函数
    3. 写回 record 的 overlay 字段
    4. dry_run=False 时才真正写入
    """
    pass
```

**注意**：overlay 是纯函数 + 当前事件状态决定，不依赖历史，所以理论上可以重新计算。但 LLM 调用的原始 token usage 已丢失，Phase 5 只能填充 `degraded_mode=True`。

---

## 🧪 五、测试覆盖与运维工具

### 5.1 三路 overlay 合并的端到端集成测试 — ❌ 缺失

**现状**：
- 各 overlay 服务有独立单元测试（齐全）
- `merge_quality_overlays` 有单测
- **但缺端到端集成测试**：dq + mq + sr 同时启用、与 `analyze_event` 全链路串联、最严格方向胜出语义

**缺失内容**：

```python
# backend/tests/test_decision_quality_engine_integration.py  (不存在)
class TestQualityEngineIntegration(unittest.TestCase):

    def test_all_phases_enabled_merge_correctly(self):
        """五层 overlay 同时启用时，字段不冲突"""

    def test_most_strict_direction_wins(self):
        """Phase 1: AVOID, Phase 2: WAIT → 最终 AVOID"""

    def test_overlay_independence(self):
        """llm_telemetry 降级不影响 decision_quality 输出"""

    def test_all_phases_disabled_backward_compatible(self):
        """所有开关关闭时，输出与 pre-Phase-1 一致（byte-identical）"""

    def test_partial_degradation_does_not_block_pipeline(self):
        """Phase 2 异常，其他 Phase 正常工作（best-effort fallback）"""
```

---

### 5.2 降级场景覆盖 — ❌ 未实现

**缺失内容**：

```python
class TestDegradedModeScenarios(unittest.TestCase):

    def test_all_phases_degraded_still_produces_recommendation(self):
        """LLM 全面失效时，deterministic_fallback 仍可用"""

    def test_market_quality_disabled_when_source_not_prediction_market(self):
        """open_web / sports_event 事件不附加 market_quality"""

    def test_source_reliability_disabled_when_no_evidence_breakdown(self):
        """evidence_breakdown 为空时不附加 source_reliability"""
```

---

### 5.3 质量诊断 CLI — ❌ 未实现

**用途**：单事件的五层分解视图，快速定位质量问题。

**缺失内容**：

```python
# backend/app/cli/diagnose.py  (不存在)
import click

@click.command()
@click.argument("event_id")
def diagnose_quality(event_id: str):
    """
    输出示例：

    Event: manifold-12345 (世界杯决赛)
    ───────────────────────────────────────────

    📊 Phase 1: Decision Quality
       ✅ Enabled
       evidence_strength: 0.82
       conflict_score: 0.15
       downgrade_reason: None

    📊 Phase 2: Market Quality
       ❌ Degraded (missing spread data)
       wide_spread_flag: False

    📊 Phase 3: Prediction Calibration
       snapshot_recommendation: YES
       edge_bucket: 10-20
       direction_correct: True

    📊 Phase 4: Source Reliability
       overall_score: 0.78
       source_count: 4
       domain_diversity: 3

    📊 Phase 5: LLM Telemetry
       degraded_mode: False
       total_tokens: 1247
       estimated_token_cost: $0.0018

    🎯 Final Direction: YES (displayed)
    """
    pass
```

依赖 `click` 库（需加入 requirements.txt）。

---

### 5.4 批量质量审计脚本 — ❌ 未实现

**用途**：扫描 predictions 表 + event_store，检查 overlay 字段一致性。

**缺失内容**：

```python
# backend/scripts/audit_quality_consistency.py  (不存在)
def audit_quality_consistency():
    """
    检查:
    1. market_quality.score < threshold 但 final_displayed_direction == YES
    2. decision_quality.downgrade_reason 非空但 final_downgrade_reason 为空
    3. llm_telemetry.degraded_mode=True 但 analysis_quality="llm"
    4. source_reliability 应用但 final_downgrade_reason 未提到 source
    5. wide_spread_flag=True 但 direction 仍为 YES/NO

    输出不一致的记录列表 + 统计报告
    """
    pass
```

---

## 🎨 六、前端工程与文档一致性

### 6.1 前后端类型手工同步 — ⚠️ 工程债

**现状**：
- `frontend/src/lib/types.ts` 顶部注释明确："手工镜像后端 Pydantic，刻意放宽（多用 `?:`）"
- `api.ts` 内的 `DecisionReport` / `PredictionCalibration` / `EdgeTrajectory` 也是手工对应
- 后端 Pydantic 模型变更**不会触发前端类型校验失败**

**风险**：Phase 1-5 新增字段后，前端不知道，导致 UI 显示空值或运行时 NPE。

**建议方案**（按复杂度排序）：
1. **最低**：CI 增加 `make check-types-sync` 脚本，对比后端 Pydantic 字段与前端 types.ts
2. **中等**：用 `pydantic-to-typescript` 或 `datamodel-code-generator` 自动生成 `.d.ts`
3. **完整**：用 OpenAPI schema + `openapi-typescript` 全自动同步

---

### 6.2 空状态未集中抽象 — ⚠️ 工程债

**现状**：各组件自行处理空状态（`event-table` / `movers-board` 等），未统一抽象。不一致的 UX。

**建议**：抽出 `<EmptyState message action? />` 共享组件。

---

### 6.3 README 缺生产部署示例 — ❌ 未提供

**现状**：顶层 `README.md` 含 Windows 一键 `start.bat` + 手动启动 + 配置说明，但**无生产部署**：
- 无 nginx/caddy 反代示例
- 无 TLS 配置
- 无域名/端口规划

**缺失内容**：增加"生产部署"章节，引用 `deploy/nginx.conf.example`（§3.4）+ docker-compose 反代示例。

---

### 6.4 文档自身过时 — ✅ 本次修正

**v1.0 版本错误**：声明"Phase 1-4 未实现"，但代码与 `.env.example` 证明均已实现。本次 v2.0 已修正。

---

## 📅 实施优先级

### 🔴 P0 — 上线前必须

| # | 项目 | 章节 | 理由 |
|---|---|---|---|
| 1 | Prometheus `/metrics` 端点 | §2.1 | 生产无法告警 |
| 2 | Sentry 错误追踪 | §2.2 | 线上偶发错误丢失上下文 |
| 3 | 备份 restore 脚本 | §3.6 | 灾难恢复手动操作易错 |
| 4 | 反代 / TLS 配置示例 | §3.4 | docker-compose 已要求但未提供 |
| 5 | 三路 overlay 集成测试 | §5.1 | 合并语义无端到端验证 |

### 🟡 P1 — 上线后一周内

| # | 项目 | 章节 | 理由 |
|---|---|---|---|
| 6 | 日志结构化（JSON）+ log shipping | §2.3 | 多实例部署只能 SSH tail |
| 7 | LLM 成本仪表板（前端 + API） | §2.1 | LLM 成本失控无监控 |
| 8 | 前端构建纳入容器 | §3.5 | 部署者手动两步构建易错 |
| 9 | 多环境配置分离 | §3.2 | dev/staging/prod 共享配置易错 |
| 10 | 历史 overlay 回填脚本 | §4.4 | 旧事件缺失新字段 |

### 🟢 P2 — 持续改进

| # | 项目 | 章节 | 理由 |
|---|---|---|---|
| 11 | Secrets 管理（SOPS/Vault） | §3.3 | 高敏密钥保护级别不足 |
| 12 | CD 流程（镜像发布 + 部署） | §3.1 | 部署自动化 |
| 13 | 质量诊断 CLI | §5.3 | 调试工具 |
| 14 | 批量质量审计脚本 | §5.4 | 数据一致性检查 |
| 15 | 性能追踪装饰器 | §2.4 | 纯函数 <5ms 验证 |
| 16 | 特性开关 A/B 对比 | §2.5 | 量化 Phase 效果 |
| 17 | 各 store 列级 migration | §4.1 | schema 演进保护 |
| 18 | event_store schema_version | §4.2 | 旧记录静默通过风险 |
| 19 | 前后端类型自动同步 | §6.1 | 类型漂移 |
| 20 | 死代码清理 | §4.3 | stale_price_flag / llm_call_count |
| 21 | 空状态共享组件 | §6.2 | UX 一致性 |
| 22 | README 生产部署示例 | §6.3 | 文档完整性 |

---

## 📊 工作量估算

| 类别 | 子项数量 | 预估人日 |
|---|---|---|
| 监控可观测性（指标 + Sentry + 日志 + 性能 + A/B） | 5 项 | 6-8 人日 |
| 部署运维（CD + 多环境 + Secrets + 反代 + 容器 + restore） | 6 项 | 5-7 人日 |
| 数据层（migration 抽象 + schema_version + 回填 + 死代码清理） | 4 项 | 3-4 人日 |
| 测试与运维工具（集成测试 + 诊断 CLI + 审计脚本） | 3 项 | 3-4 人日 |
| 前端与文档（类型同步 + 空状态 + README） | 3 项 | 2-3 人日 |
| **总计** | 21 项 | **19-26 人日** |

> **对比 v1.0 估算**：v1.0 估 16-19 人日，但包含了"Phase 1-4 实现"（实际已完成）。v2.0 移除已实现部分，新增可观测性、部署运维、前端工程，工作量实际更高。

---

## ✅ 验收标准

### 监控就绪

- [ ] `/metrics` 端点返回 Prometheus 格式指标
- [ ] Sentry 捕获后端 FastAPI 异常 + scheduler 异常
- [ ] Sentry 捕获前端路由级与全局错误
- [ ] 日志输出 JSON 结构化格式
- [ ] 至少一个 log shipping 后端示例（Loki 或 Filebeat）
- [ ] 5 个 spec 定义的指标全部暴露
- [ ] LLM 成本仪表板上线下 LLM 成本日报自动生成

### 部署就绪

- [ ] CD workflow 自动构建镜像并 push 到 registry
- [ ] `.env.staging` / `.env.production` 模板存在
- [ ] Secrets 通过 SOPS / Vault / Doppler 注入（非明文 env）
- [ ] `deploy/nginx.conf.example` 或 Caddyfile 提供
- [ ] Dockerfile 包含 frontend builder stage
- [ ] `restore_stores.py` 支持 dry-run 恢复

### 数据完整性

- [ ] 所有 SQLite store 共用列级 migration 工具
- [ ] `event_store.json` 记录包含 `schema_version`
- [ ] `normalize_event_record` 函数按版本升级字段
- [ ] `backfill_quality_overlays.py` 完成 overlay 字段回填
- [ ] `stale_price_flag` 死代码已清理或补全数据源

### 测试与运维工具

- [ ] 三路 overlay 集成测试通过
- [ ] 降级场景测试通过
- [ ] `python -m app.cli.diagnose <event_id>` 正常运行
- [ ] `audit_quality_consistency.py` 输出 0 个冲突（或全部修复）
- [ ] 性能追踪显示所有 overlay <10ms

### 前端与文档

- [ ] 前后端类型同步 CI 检查通过
- [ ] `<EmptyState />` 共享组件抽象
- [ ] README 含生产部署章节

---

## 🔗 与现有设计文档的关系

本文档是 [2026-06-30-decision-quality-engine-design.md](./2026-06-30-decision-quality-engine-design.md) 的**实施清单补充**：

- **设计文档**：定义 WHAT（Phase 1-5 架构与字段规格）—— 已全部实现
- **本文档**：定义 MISSING（生产就绪补全项）—— 待实施

两份文档配合使用：
1. 先读设计文档，理解架构与纯函数约束
2. 再读本文档，按 P0 → P1 → P2 优先级补全生产就绪项

---

## 📝 变更记录

| 版本 | 日期 | 变更内容 |
|---|---|---|
| v1.0 | 2026-06-30 | 初始版本 — Phase 5 完成后的系统缺失分析（含错误：误报 Phase 1-4 未实现） |
| v2.0 | 2026-06-30 | **重大修正**：核实代码后确认 Phase 1-4 均已实现（commits `a710961`/`104fb91`/`c79b62c`/`8764392`）。移除"Phase 1-4 实现"章节，改为"实现状态记录"。新增可观测性、部署运维、数据层、前端工程等真实缺失项。优先级从 10 项扩展到 22 项。 |

---

**文档所有者**: 系统架构组
**审核状态**: 待审核
**下次更新**: P0 项目实施完成后
