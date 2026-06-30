# 生产就绪缺失项分析

**文档版本**: v3.1
**创建日期**: 2026-06-30
**最近更新**: 2026-06-30
**状态**: Decision Quality Engine Phase 1-5 全部实现并提交；本文档仅列出未实现的生产就绪、可观测、回放评估和人机协同缺失项

---

## 📋 执行摘要

Decision Quality Engine Phase 1-5 均已实现并通过测试（详见 [设计文档](./2026-06-30-decision-quality-engine-design.md)）。当前系统最缺的不是继续堆叠预测算法，而是把已实现的质量引擎变成可观测、可回放、可审计、可恢复、可解释的生产闭环。本文档列出从当前状态到生产就绪的**未实现**缺失项，分为六类：

1. **监控与可观测性**（最大短板：无指标暴露、无错误追踪、无日志聚合）
2. **部署与运维**（无 CD、无多环境配置、无 Secrets 管理、无反代示例、无 restore 脚本）
3. **数据层与 Schema 演进**（部分 store 缺列级迁移、event_store.json 无版本、死代码字段）
4. **测试覆盖与运维工具**（三路合并缺集成测试、运维 CLI/审计脚本全缺）
5. **前端工程与文档一致性**（前后端类型手工同步、README 缺生产部署示例）
6. **策略评估、人机协同与产品可解释性**（缺统一回放实验、模型评估实验室、人工复核闭环、事件变化解释）

**战略锚点**：让系统能安全地从真实结算结果中学习，并且每次降级、改判、置信度变化都可解释、可追踪。

**范围边界**：本文不重复记录 Phase 1-5 的已实现字段和纯函数逻辑；只记录仍会阻碍生产运行、长期评估、用户信任或团队维护效率的缺口。世界杯子系统中已有的局部质量分析、预测时间线和仿真能力不等于主 Reality Filter 引擎已有统一能力，本文按主引擎生产闭环口径评估。

---

## 🔍 一、监控与可观测性（最大短板）

### 1.1 应用指标暴露 — ❌ 完全空白

**问题**：生产无法告警、无法看趋势、无法定位降级原因。

**现状**：
- `backend/requirements.txt` 仅 15 个依赖，无 `prometheus_client` / `sentry-sdk` / `opentelemetry`
- 已有 `/api/health`，可报告 scheduler 是否运行、失败 run 和 loop 状态；但它是健康检查，不是时间序列指标出口
- 无 `/metrics` 端点，也无 `/api/quality-metrics` 聚合端点
- spec 自己定义的 5 个核心指标无处落盘或暴露：
  - `decision_quality_downgrade_rate`
  - `consensus_distribution`
  - `rule_fire_count`
  - `build_failure_count`
  - `latency_ms`
- 生产运行还缺少以下关键指标：
  - `pmrf_scheduler_last_success_timestamp`
  - `pmrf_scheduler_failed_runs_total`
  - `pmrf_llm_token_cost_total`
  - `pmrf_calibration_brier_score`
  - `pmrf_calibration_drift_score`
  - `pmrf_overlay_build_failure_total`
  - `pmrf_final_direction_change_total`

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
SCHEDULER_LAST_SUCCESS = Gauge(
    "pmrf_scheduler_last_success_timestamp",
    "最近一次 scheduler job 成功完成时间戳",
    ["job_name"]
)
```

**前端可视化**：`frontend/src/components/dashboard/quality-metrics.tsx`（不存在）— 应展示降级率时间序列、LLM 成本趋势、Brier score 漂移预警

---

### 1.2 错误追踪 — ❌ 未实现

**问题**：线上偶发错误会丢失上下文。

**现状**：
- 前端 JS 错误仅 `console.error`，无 Sentry SDK
- 后端异常仅落本地 `logs/app.log`，无 stack trace 上报

**缺失内容**：
- 后端集成 `sentry-sdk`，捕获 FastAPI 异常 + scheduler 异常
- 前端集成 `@sentry/nextjs`，捕获路由级与全局错误
- 在 `.env` 增加 `SENTRY_DSN` 配置项

---

### 1.3 日志聚合 — ❌ 未实现

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

### 1.4 质量层性能追踪 — ❌ 未实现

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

### 1.5 特性开关 A/B 对比 — ❌ 未实现

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

### 1.6 统一质量运营仪表盘 — ❌ 未实现

**问题**：Phase 1-5 已经把质量信号写入事件和报告，但系统没有一个跨模块的运营面板回答“今天系统变得更保守了吗、哪里在降级、成本是否异常、校准是否漂移”。

**现状**：
- 世界杯子系统已有 `/api/world-cup/analytics/quality-loop`、`prediction-timeline`、`system-health` 等局部分析能力
- 主 Reality Filter 事件流有 `decision_report`、`calibration`、`loop_status` 等分散 API
- 无统一质量运营视图；前端也没有跨 Phase 的质量仪表盘

**缺失内容**：

```text
backend/app/api/routes/quality_metrics.py
  GET /api/quality-metrics/summary
  GET /api/quality-metrics/timeseries?window=7d
  GET /api/quality-metrics/anomalies

frontend/src/components/dashboard/quality-operations-dashboard.tsx
  - downgrade_rate by phase / reason
  - final_displayed_direction 分布和变化率
  - LLM tokens / estimated cost trend
  - calibration Brier / ECE / bucket drift
  - source reliability distribution
  - market quality wide_spread / thin_market trend
  - scheduler last-success / failed runs
```

**验收口径**：一名维护者不读日志、不查 SQLite，也能在 30 秒内判断质量引擎是否健康、哪个 Phase 正在触发降级、是否需要回滚某个开关。

---

### 1.7 校准漂移与告警策略 — ❌ 未实现

**问题**：系统已经能计算 calibration/Brier，但缺少“什么时候必须提醒维护者”的策略。质量变差会静默累积，直到用户发现输出不可信。

**缺失内容**：
- 按 source、category、analysis_quality、engine/phase 维度计算滚动 Brier / ECE
- 对比最近 N 条 resolved outcomes 与历史基线，输出 `calibration_drift_score`
- 告警规则示例：
  - Brier 7 日均值高于历史均值 30%
  - 某 bucket 样本数达标后命中率偏离预测区间超过 20 个百分点
  - fallback/LLM degraded 样本混入 headline calibration 指标
  - scheduler 连续成功但 resolved outcome 数为 0
- 告警出口：日志 + metrics + Sentry breadcrumb/alert 或 webhook

---

## 🚀 二、部署与运维

### 2.1 CD 流程 — ❌ 未实现

**现状**：`.github/workflows/ci.yml` 三个 job（backend-tests / frontend-tests / secret-scan），但：
- 无镜像发布到 registry
- 无 staging/prod 部署自动化
- mypy（256 个已知错误）与 gitleaks 都 `continue-on-error: true`，**实际只挡 lint + 单测 + 构建**

**缺失内容**：
- 增加 `deploy.yml` workflow：构建镜像 → push 到 registry → 部署到 staging（tag 触发）→ 手动 approve → prod
- mypy 与 gitleaks 改为阻塞（先清理 256 个 mypy 错误）

---

### 2.2 多环境配置 — ❌ 未实现

**现状**：单个 `backend/.env`，dev/staging/prod 靠人工区分。

**缺失内容**：
- `.env.staging` / `.env.production` 模板
- `settings` 按 `PMRF_ENV` 环境变量加载不同配置文件
- 至少区分：LLM 模型（dev=便宜模型，prod=生产模型）、特性开关默认值、日志级别、限流策略

---

### 2.3 Secrets 管理 — ❌ 未实现

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

### 2.4 反代 / TLS 配置示例 — ❌ 未提供

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

### 2.5 前端构建未纳入容器 — ❌ 未串联

**现状**：
- `deploy/Dockerfile` 假设 `frontend/out/` 已在外部构建好
- CI 无前端构建 + 镜像打包串联步骤
- 部署者需手动两步构建（`cd frontend && npm run build` → `docker build`）

**缺失内容**：
- Dockerfile 多阶段构建加入 frontend builder stage（`node:22-alpine` → `npm ci && npm run build`）
- 或在 CI 增加"构建前端 → 复制到 backend → 构建 backend 镜像"job

---

### 2.6 备份恢复脚本 — ❌ 未实现

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

## 📊 三、数据层与 Schema 演进

### 3.1 部分 store 缺列级 migration 机制 — ⚠️ 部分实现

**现状**：
- `prediction_store` ✅ 有完整 `_MIGRATIONS` dict + `_SCHEMA_VERSION=4` + `record_schema_version`
- `backend/app/utils/sqlite_db.py` ✅ 已有共享连接、WAL 维护、完整性检查和 `record_schema_version`
- `event_market_link_store` / `loop_run_store` / `optimization_task_store` / `simulated_trade_store` ❌ 仍只有 `CREATE TABLE IF NOT EXISTS` + `record_schema_version`，缺统一列级 migration runner，新增列需各 store 手动 ALTER

**风险**：未来给这些表加列时，旧实例升级后 schema 不变 → 写入会丢字段 / 读取会报错。

**建议**：扩展现有 `sqlite_db.py`，增加 `apply_migrations(component, schema_version, migrations)` 共享工具，所有 SQLite store 统一使用，避免每个 store 自己维护迁移细节。

---

### 3.2 event_store.json 无 schema 版本 — ❌ 未实现

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

### 3.3 死代码字段 — ⚠️ 需清理

**现状**：
- `MarketQuality.stale_price_flag` 永远为 None（所有 adapter 都不暴露 `last_updated`）
- `LLMTelemetry.llm_call_count` 是保守下界（无法检测 `translate_title` 调用）

**建议**：
- `stale_price_flag`：要么在 Polymarket/Kalshi adapter 补 `last_updated`，要么删除该字段并在 docstring 标注"未实现"
- `llm_call_count`：保持现状但 docstring 明确"保守下界"

---

### 3.4 历史数据回填（overlay 层） — ❌ 未实现

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

### 3.5 市场微结构与可执行性模型 — ⚠️ 部分实现

**现状**：
- `market_quality_service` 已使用 spread、liquidity、volume，且 wide spread 已是 hard cutoff
- `probability_engine_service` 可接收 `market_microstructure` 的部分点位信号（如 price_change、bid_ask_spread、volume_z_score）
- 但主决策引擎还没有把“这笔建议是否可执行”作为独立、可审计的约束模型

**缺失内容**：
- 统一 adapter 字段：`bid` / `ask` / `spread` / `depth` / `last_updated` / `fees` / `min_order_size`
- stale price 检测：`last_updated` 超过阈值时强制降级为 WAIT/AVOID
- 滑点估算：按目标下单规模估算 effective entry price，而不是只看当前 mid/YES price
- 平台约束：手续费、提现/结算延迟、最小交易单位、市场暂停状态
- 执行可行性字段：

```python
execution_quality = {
    "executable": bool,
    "effective_entry_price": float | None,
    "estimated_slippage_pct": float | None,
    "max_safe_position_size": float | None,
    "stale_price_flag": bool | None,
    "platform_constraint_reasons": list[str],
}
```

**收益**：把“预测上有 edge”与“市场上可执行”分开，避免系统给出理论上正确但实际无法成交或滑点吞掉 edge 的建议。

---

## 🧪 四、测试覆盖与运维工具

### 4.1 三路 overlay 合并的端到端集成测试 — ❌ 缺失

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

### 4.2 降级场景覆盖 — ❌ 未实现

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

### 4.3 质量诊断 CLI — ❌ 未实现

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

### 4.4 批量质量审计脚本 — ❌ 未实现

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

### 4.5 全链路 Replay / Simulation Harness — ❌ 未实现

**问题**：`analyze_feature_flag_impact.py` 只能比较单个 Phase 开关，不能复现实验“某一版完整引擎在历史事件上会做出什么决策”。没有全链路 replay，就很难量化增强方案是否真的降低误判、改善 Brier、减少不该下注的 YES。

**缺失内容**：

```python
# backend/scripts/replay_decision_pipeline.py  (不存在)
def replay_decision_pipeline(
    event_ids: list[str] | None = None,
    sample_size: int = 500,
    config_profile: str = "current",
    write_report: bool = True,
):
    """
    1. 读取历史 event_store + prediction snapshots
    2. 冻结输入，不调用外部 API，不重新请求 LLM
    3. 按指定配置重放 Phase 1-5 + merge_quality_overlays
    4. 对比原始 recommendation 与 replay recommendation
    5. 对已 resolved 样本计算 Brier、方向正确率、误伤率、降级收益
    6. 输出 HTML/Markdown/JSON 报告
    """
    pass
```

**报告应包含**：
- YES → WAIT / YES → AVOID / WAIT → AVOID 的比例
- resolved 样本上的 Brier 改善或恶化
- 降级命中的真实错误率
- fallback 与 LLM 样本分开统计，避免污染 headline calibration
- 每个 Phase 的边际贡献与冲突案例

---

### 4.6 模型评估实验室 — ❌ 未实现

**问题**：系统已有多个分析路径和世界杯专用引擎，但缺少统一的模型评估框架。现在难以系统性比较“不同 LLM、fallback、规则权重、calibration 策略”对真实结果的影响。

**缺失内容**：
- 统一 experiment registry：记录模型版本、prompt 版本、feature flags、配置快照
- 按 `analysis_quality` 分组：`llm` / `deterministic_fallback` / degraded / world-cup-engine
- 按 category、source、market type、confidence bucket 计算 Brier / ECE / hit rate / downgrade benefit
- 支持 A/B model replay：同一批冻结事件输入，用不同模型或规则配置生成可比报告
- 自动生成“是否值得上线”的 guardrail：
  - Brier 不得显著变差
  - 高置信错误率不得上升
  - fallback 不能混入主 LLM calibration headline
  - 成本增长需对应可衡量质量收益

**建议位置**：
- `backend/app/services/model_evaluation_service.py`
- `backend/scripts/evaluate_model_variants.py`
- `docs/reports/model-evaluation/YYYY-MM-DD-*.md`

---

## 🎨 五、前端工程与文档一致性

### 5.1 前后端类型手工同步 — ⚠️ 工程债

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

### 5.2 空状态未集中抽象 — ⚠️ 工程债

**现状**：各组件自行处理空状态（`event-table` / `movers-board` 等），未统一抽象。不一致的 UX。

**建议**：抽出 `<EmptyState message action? />` 共享组件。

---

### 5.3 README 缺生产部署示例 — ❌ 未提供

**现状**：顶层 `README.md` 含 Windows 一键 `start.bat` + 手动启动 + 配置说明，但**无生产部署**：
- 无 nginx/caddy 反代示例
- 无 TLS 配置
- 无域名/端口规划

**缺失内容**：增加"生产部署"章节，引用 `deploy/nginx.conf.example`（§2.4）+ docker-compose 反代示例。

---

### 5.4 事件变化时间线 / Diff Viewer — ❌ 未实现

**问题**：用户看到一个事件从 YES 变 WAIT/AVOID 时，无法直接知道是价格变化、证据冲突、source reliability、market quality、calibration，还是 LLM degraded 导致的变化。

**现状**：
- 世界杯子系统已有 match prediction timeline
- 主事件流有 event history、decision report、movers 等页面
- 但没有把同一事件的多次分析结果做结构化 diff，也没有解释最终方向变化的主因

**缺失内容**：
- `GET /api/events/{event_id}/decision-timeline`：返回每次分析的输入摘要、overlay、final direction、downgrade reason
- `build_decision_diff(prev, current)`：输出变化原因排名
- 前端 `DecisionTimelinePanel`：
  - probability / baseline / raw edge 变化
  - final direction 变化
  - Phase 1-5 overlay diff
  - “primary change driver”：market move / source conflict / calibration / LLM degraded / manual resolution

**收益**：把系统从“给结论”升级为“解释结论如何演化”，降低用户对 WAIT/AVOID 降级的困惑。

---

## 🧭 六、策略评估、人机协同与产品可解释性

### 6.1 Source Trust Registry — ❌ 未实现

**问题**：Source Reliability 当前基于证据、域名、多样性等信号计算，但缺少可维护的源信誉注册表。现实中部分域名、官方源、市场源、聚合源需要人为设定基线权重和限制。

**缺失内容**：
- `config/source_trust_registry.yml` 或数据库表，记录 source/domain 的 base trust、类别、适用市场、降权原因
- 支持 source alias 归一化，例如同一机构的不同域名、API、RSS
- 支持 denylist / caution list / official list
- 在 `source_reliability_service` 中把 registry 作为可选输入，而不是硬编码规则
- 前端或 CLI 提供 registry audit：哪些源频繁参与错误判断，哪些源样本不足

**风险控制**：registry 只能作为先验权重，不应覆盖事件级证据冲突；需要在报告中展示“source prior affected score”。

---

### 6.2 人工复核与异常裁决队列 — ⚠️ 部分实现

**现状**：
- `event_market_link_store.list_pending()` 已提供未验证市场链接的人类复核队列
- open decisions 页面可承载人工查看
- 但没有覆盖质量异常、结算争议、source 冲突、模型高分歧等更广义的复核工作流

**缺失内容**：
- 新增 `review_queue` 概念，来源包括：
  - 高价值事件但 final direction 被降级
  - source_reliability 与 market_quality 强冲突
  - resolved outcome 与预测高置信相反
  - 自动结算低置信或来源冲突
  - audit_quality_consistency 发现不一致
- 支持 reviewer action：
  - confirm / override / request_more_evidence / mark_bad_source / mark_bad_resolution
- 所有人工动作写入 append-only audit log，不能静默覆盖模型输出
- 人工裁决结果进入 replay/model evaluation，作为后续规则调参样本

---

### 6.3 策略层 Guardrails — ❌ 未实现

**问题**：目前文档列了很多增强项，但缺少“什么时候禁止自动行动”的统一策略层。单个 Phase 的降级规则不能完全替代全局风险控制。

**缺失内容**：
- 全局 guardrail 配置：
  - 单日 LLM 成本上限
  - 单日 YES/ACT 数量上限
  - 同一 source/domain 暴露上限
  - 未校准类别只能 WATCH/WAIT
  - source conflict 高于阈值时禁止 YES
  - market executable=false 时禁止 YES
- guardrail 命中必须进入 `final_downgrade_reason`
- metrics 暴露 guardrail fire count
- replay harness 报告每条 guardrail 的命中收益和误伤率

---

## 📅 实施优先级

### 🔴 P0 — 上线前必须

| # | 项目 | 章节 | 理由 |
|---|---|---|---|
| 1 | Prometheus `/metrics` + `/api/quality-metrics` 基础指标 | §1.1 | 生产无法告警，也无法量化降级 |
| 2 | Sentry 错误追踪 | §1.2 | 线上偶发错误丢失上下文 |
| 3 | 备份 restore 脚本 | §2.6 | 灾难恢复手动操作易错 |
| 4 | 反代 / TLS 配置示例 | §2.4 | docker-compose 已要求但未提供 |
| 5 | 批量质量审计脚本 | §4.4 | 防止 YES/WAIT/AVOID 与 overlay 冲突静默进入生产 |
| 6 | 三路 overlay 集成测试 | §4.1 | 合并语义无端到端验证 |
| 7 | `event_store` schema version + normalize | §3.2 | 旧记录静默通过会破坏回放与审计 |
| 8 | 策略层 Guardrails 最小集 | §6.3 | 高风险场景必须 fail-closed |

### 🟡 P1 — 生产试运行后一到两周

| # | 项目 | 章节 | 理由 |
|---|---|---|---|
| 9 | 统一质量运营仪表盘 | §1.6 | 让维护者看到质量引擎实际表现 |
| 10 | 校准漂移与告警策略 | §1.7 | 防止质量退化静默累积 |
| 11 | 全链路 replay / simulation harness | §4.5 | 验证增强是否真的改善真实结果 |
| 12 | 特性开关 A/B 对比 | §1.5 | 量化单个 Phase 的边际影响 |
| 13 | 历史 overlay 回填脚本 | §3.4 | 旧事件缺失新字段，dashboard/replay 样本不足 |
| 14 | 多环境配置分离 | §2.2 | dev/staging/prod 共享配置易错 |
| 15 | 前端构建纳入容器 | §2.5 | 部署者手动两步构建易错 |
| 16 | 各 store 列级 migration runner | §3.1 | schema 演进保护 |
| 17 | 市场微结构与可执行性模型 | §3.5 | 避免理论 edge 被滑点/陈旧价格吞掉 |
| 18 | 事件变化时间线 / Diff Viewer | §5.4 | 解释为什么 YES/WAIT/AVOID 发生变化 |
| 19 | Source Trust Registry | §6.1 | 源信誉先验需要可维护、可审计 |
| 20 | 降级场景测试 | §4.2 | 验证部分失败时仍能产生安全输出 |
| 21 | 人工复核与异常裁决队列 | §6.2 | 高风险样本需要人类闭环 |

### 🟢 P2 — 持续改进

| # | 项目 | 章节 | 理由 |
|---|---|---|---|
| 22 | 日志结构化（JSON）+ log shipping | §1.3 | 多实例部署不能只依赖 SSH tail |
| 23 | Secrets 管理（SOPS/Vault） | §2.3 | 高敏密钥保护级别不足 |
| 24 | CD 流程（镜像发布 + 部署） | §2.1 | 部署自动化 |
| 25 | 质量诊断 CLI | §4.3 | 单事件调试工具 |
| 26 | 性能追踪装饰器 | §1.4 | 纯函数 <5ms 验证 |
| 27 | 模型评估实验室 | §4.6 | 系统比较模型/规则/prompt 版本 |
| 28 | 前后端类型自动同步 | §5.1 | 类型漂移 |
| 29 | 死代码清理 | §3.3 | stale_price_flag / llm_call_count |
| 30 | 空状态共享组件 | §5.2 | UX 一致性 |
| 31 | README 生产部署示例 | §5.3 | 文档完整性 |

---

## 📊 工作量估算

| 类别 | 子项数量 | 预估人日 |
|---|---|---|
| 监控可观测性（指标 + Sentry + 日志 + 性能 + A/B + 仪表盘 + 漂移告警） | 7 项 | 8-11 人日 |
| 部署运维（CD + 多环境 + Secrets + 反代 + 容器 + restore） | 6 项 | 5-7 人日 |
| 数据层（migration runner + schema_version + 回填 + 死代码清理 + 可执行性模型） | 5 项 | 5-7 人日 |
| 测试与运维工具（集成测试 + 降级测试 + 诊断 CLI + 审计 + replay + 模型评估） | 6 项 | 8-12 人日 |
| 前端与文档（类型同步 + 空状态 + README + 时间线 diff） | 4 项 | 4-6 人日 |
| 策略与人机协同（Source Registry + Review Queue + Guardrails） | 3 项 | 5-8 人日 |
| **总计** | 31 项 | **35-51 人日** |

---

## ✅ 验收标准

### 监控就绪

- [ ] `/metrics` 端点返回 Prometheus 格式指标
- [ ] `/api/quality-metrics/summary`、`/timeseries`、`/anomalies` 返回主引擎质量聚合数据
- [ ] Sentry 捕获后端 FastAPI 异常 + scheduler 异常
- [ ] Sentry 捕获前端路由级与全局错误
- [ ] 日志输出 JSON 结构化格式
- [ ] 至少一个 log shipping 后端示例（Loki 或 Filebeat）
- [ ] 5 个 spec 定义的指标和 scheduler last-success / failed-runs 指标全部暴露
- [ ] LLM 成本仪表板上线，LLM 成本日报自动生成
- [ ] 质量运营仪表盘展示 downgrade、direction、LLM cost、calibration、source、market、scheduler 指标
- [ ] 校准漂移告警能区分 LLM 与 fallback 样本，并能触发 webhook/Sentry/日志至少一种出口

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
- [ ] `execution_quality` 或等价结构能表示 stale price、slippage、platform constraint 和 executable 状态

### 测试与运维工具

- [ ] 三路 overlay 集成测试通过
- [ ] 降级场景测试通过
- [ ] `python -m app.cli.diagnose <event_id>` 正常运行
- [ ] `audit_quality_consistency.py` 输出 0 个冲突（或全部修复）
- [ ] 性能追踪显示所有 overlay <10ms
- [ ] `replay_decision_pipeline.py` 能在冻结输入上重放 Phase 1-5 并输出质量报告
- [ ] 模型评估报告按 model/prompt/config/analysis_quality 分组，展示 Brier、ECE、成本和 guardrail 结果

### 前端与文档

- [ ] 前后端类型同步 CI 检查通过
- [ ] `<EmptyState />` 共享组件抽象
- [ ] README 含生产部署章节
- [ ] `DecisionTimelinePanel` 或等价页面能解释单事件方向变化原因

### 策略与人机协同

- [ ] `source_trust_registry` 可配置、可审计，并在 source reliability 报告中展示影响
- [ ] `review_queue` 覆盖质量异常、结算争议、source 冲突和 audit 冲突
- [ ] 人工复核动作写入 append-only audit log
- [ ] Guardrails 命中后会进入 `final_downgrade_reason` 并暴露 fire count

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
| v2.0 | 2026-06-30 | 核实代码后确认 Phase 1-4 均已实现；新增可观测性、部署运维、数据层、前端工程等真实缺失项。优先级从 10 项扩展到 22 项。 |
| v3.0 | 2026-06-30 | 精简版本：移除已实现的 Phase 1-5 状态章节，只保留未实现缺失项。文档结构：监控可观测性 / 部署运维 / 数据层 / 测试与工具 / 前端与文档 五大类。 |
| v3.1 | 2026-06-30 | 查缺补漏：补充战略锚点、统一质量运营仪表盘、校准漂移告警、全链路 replay、模型评估实验室、市场可执行性模型、事件变化时间线、Source Trust Registry、人工复核队列和策略 Guardrails；修正 `/api/health` 与 `sqlite_db.py` 已有能力描述；优先级扩展到 31 项。 |

---

**文档所有者**: 系统架构组
**审核状态**: 待审核
**下次更新**: P0 项目实施完成后
