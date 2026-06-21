# Prediction Market Reality Filter v0.3.0 — 全面工程审计 + 事故就绪度评估

**日期**：2026-06-20
**工作流**：综合审计（工作流 1 代码审查 + 工作流 3 事故响应 + 工作流 5 技术债评估）
**参与成员**：Cody（代码审查）、Archi（架构评估）、Rex（SRE/事故响应）、Tessa（测试评估）、Docu（文档审查）
**审计范围**：后端 252 文件（36 服务 + 39 测试）、前端 Next.js 16 应用、41 个文档文件

---

## 📌 TL;DR（执行摘要）

- **整体结论**：系统核心架构正确、测试质量高（338 通过），但**运维基础设施几乎为零**（无监控、无备份、无进程守护），**当前不具备生产部署条件**
- **严重度分布**：🔴严重 14 项 / 🟠高 18 项 / 🟡中 22 项 / 🟢低 14 项 — 共计 68 项发现
- **生产就绪度**：🔴 **NO-GO** — 需修复 5 个 P0 阻塞项才能部署；P0+P1 全部完成约需 2-3 周
- **最强维度**：反馈闭环设计（契约优先结算、孤儿愈合、fail-closed gate）— 超越典型 v0.3 项目；测试单元质量（命名、隔离、边界覆盖）
- **最弱维度**：可观测性（零）、CI/CD（零）、前端测试（零）、API 版本控制（无）

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 整体评级 | 🔴 条件通过（架构/代码 B+，运维/CI/CD F） |
| 生产就绪度 | 🔴 NO-GO（5 个 P0 阻塞项） |
| 阻塞项数量 | 5 |
| 关键行动项 | 20 条（P0: 5, P1: 8, P2: 4, P3: 3） |
| 建议下一步 | 修复 5 个 P0 阻塞项 → 建立 CI/CD → 补前端测试 → 容器化 |

---

## 🔴 综合严重问题清单（14 项，需在 v0.4.0 前修复）

| # | 严重度 | 类别 | 发现 | 来源 |
|---|--------|------|------|------|
| 1 | 🔴严重 | 安全 | **CORS `allow_origins=["*"]` + `allow_credentials=True`** — 违反 CORS 规范，浏览器会拒绝请求 | Cody+Rex+Archi |
| 2 | 🔴严重 | 安全 | **零速率限制** — `/api/events/analyze` 等端点无 Rate Limiting，LLM 调用可被耗尽 | Cody+Rex |
| 3 | 🔴严重 | 可维护性 | **requirements.txt 全部依赖无版本锁定** — 上游 breaking change 可静默破坏生产环境 | Cody+Rex |
| 4 | 🔴严重 | 架构 | **无 API 版本控制** — 所有端点位于 `/api/events/*`，无 `/v1/` 前缀，Breaking Change 无法安全发布 | Archi |
| 5 | 🔴严重 | 架构 | **无 response_model** — 18 个端点返回裸 dict，OpenAPI 响应 schema 为空，前端无类型安全 | Archi |
| 6 | 🔴严重 | 运维 | **无健康检查端点** — 无法检测调度器状态、最后成功运行时间、待处理预测数 | Rex |
| 7 | 🔴严重 | 运维 | **无进程守护** — FastAPI 崩溃后无自动重启，调度器停摆无告警 | Rex |
| 8 | 🔴严重 | 运维 | **无自动化备份** — 所有数据（event_store.json + v2_loop.db + audit.jsonl）无定期备份 | Rex |
| 9 | 🔴严重 | 运维 | **LLM API Key 过期/无效无启动校验** — 系统静默产出 `count=0`，可能数天未发现 | Rex |
| 10 | 🔴严重 | 测试 | **零前端测试** — 20 个 TSX/TS 组件零测试覆盖 | Tessa |
| 11 | 🔴严重 | 测试 | **零 CI/CD Pipeline** — 无 GitHub Actions，338 个测试依赖开发者手动运行 | Tessa |
| 12 | 🔴严重 | 测试 | **gnews_service.py, openai_service.py, rss_service.py 无测试** — 外部 API 调用层零覆盖 | Tessa |
| 13 | 🔴严重 | 文档 | **HANDOFF.md（gitignored）承载架构文档角色** — 丢失将严重损坏项目上下文 | Docu |
| 14 | 🔴严重 | 文档 | **无系统架构图 / ADR** — 理解架构需读代码，关键决策理由（JSON 存储、静态导出）无记录 | Docu+Archi |

---

## 🟠 综合高优先级问题（18 项）

| # | 类别 | 发现 | 来源 |
|---|------|------|------|
| 15 | 架构 | **`event_intelligence_service.py` God Object** — 697 行混入 10+ 种职责（编排/评分/报告/持久化） | Archi |
| 16 | 架构 | **无事件源抽象接口** — 新数据源依赖命名约定，无 Protocol/ABC 类型检查 | Archi |
| 17 | 架构 | **`_clamp01` 和 `_now()` 多处重复定义** — DRY 违反，行为分歧风险 | Archi |
| 18 | 架构 | **文件锁单进程假设** — `fcntl` 文件锁在多实例部署时失效 | Archi |
| 19 | 架构 | **调度器与 API 进程耦合** — API 崩溃 = 调度停止，无独立存活能力 | Archi |
| 20 | 架构 | **`Semaphore(4)` 硬编码** — LLM 并发限制不可配置 | Archi+Cody |
| 21 | 性能 | **并发 LLM 调用一次性创建所有任务** — `asyncio.gather` 创建最多 30 个协程造成内存压力 | Cody |
| 22 | 性能 | **`histories_by_event()` 每次全量读审计日志** — 每日 auto-resolve 全量扫描 JSONL 文件 | Cody |
| 23 | 性能 | **`list_all_events()` 无界读取** — 返回整个 JSON 存储到内存，事件增长后性能恶化 | Cody |
| 24 | 正确性 | **惩罚因子被均值稀释** — 9 个维度取平均导致高风险信号（如 meme 0.40）净影响仅 ~0.097 | Cody |
| 25 | 正确性 | **`resolve_with_calibration` 三阶段写入间崩溃导致预测永久孤儿** — 需重排写入顺序 | Rex |
| 26 | 安全 | **API Key 仅环境变量存储** — `.env` 误提交即泄露，无 SecretStr 保护 | Cody |
| 27 | 安全 | **敏感端点无认证** — `POST /api/events/resolve/auto` 等破坏性操作完全公开 | Cody |
| 28 | 文档 | **`calibration_feedback_service.py` 无 docstring** — 最复杂算法模块完全无文档 | Docu |
| 29 | 文档 | **`Event Intelligence Platform.md` 数据过时** — "141 tests" 实际 338，引用已删除路径 | Docu |
| 30 | 文档 | **无 Dockerfile 实体文件** — 部署依赖 USER_GUIDE 中的示例代码手写 | Docu |
| 31 | 文档 | **无 Runbook** — 故障时无标准操作流程，只能"试错 + 读源码" | Docu+Rex |
| 32 | 运维 | **日志无文件持久化** — 仅 stdout 输出，进程重启后日志丢失 | Rex |

---

## 🟡 中等优先级问题（22 项）

略，详见各成员原始产出。代表性项目：
- `{event_id}` 路径参数无格式验证（Cody M1）
- `json.loads()` 解析 LLM 响应无深度限制（Cody M2）
- 新闻质量评分基于正则的脆弱模式匹配（Cody M3）
- 手动结算 `upsert_link` 无条件覆盖（Cody M5）
- `misfire_grace_time=300` 秒可能不足（Cody L5）
- config.py 无分组，226 行扁平 Settings 类（Archi AD-11）
- 遗留服务未清理（`polymarket_service.py` 等）（Archi AD-13）
- 惰性导入模式普遍，14 处内部 import 掩盖依赖关系（Archi AD-15）
- `docs/user/` 混入代码审查记录（Docu P2）
- 41 个 md 文件无导航索引（Docu P2）
- API 集成测试套件缺失（Tessa P0#1）
- config.py 无测试 — 配置错误可能静默上线（Tessa）

---

## 🟢 低优先级问题（14 项）

如硬编码常数 (`_CANDIDATE_POOL_FACTOR`)、重复工具函数（3 处 `_clamp`）、端点命名不一致等。详见各成员原始产出。

---

## 🏗️ 架构综合评估

### 综合评分：⭐⭐⭐⭐ (3.6/5)

| 维度 | 评分 | 关键判断 |
|------|------|----------|
| 分层架构 | ⭐⭐⭐⭐ | 三层分离清晰（API → Service → Persistence），memory/services 边界良好 |
| 管道设计 | ⭐⭐⭐⭐⭐ | 反馈闭环成熟（契约优先结算、孤儿愈合、fail-closed gate 均超越 v0.3 阶段） |
| API 设计 | ⭐⭐⭐ | 功能完整但无版本控制、无 response_model |
| 可扩展性 | ⭐⭐⭐ | 新数据源易添加，但存储层单节点瓶颈（SQLite+JSON 无法多实例） |
| 代码组织 | ⭐⭐⭐ | 大部分清晰，但 `event_intelligence_service.py` 为 God Object（10+ 种职责混合） |

### 关键架构决策（ADR 草案）

**ADR-001: 单体部署 + 文件存储** → 正确选择（v0.3 阶段）。当需要多实例部署时需迁移到 PostgreSQL。

**ADR-002: 契约 ID 优先的事件结算** → 出色设计。双路径（contract_id 精确匹配 → 文本匹配回退），fail-closed gate 防止错误结算。

**ADR-003: 跨存储事务的崩溃恢复** → 先 SQLite 后 JSON + 孤儿愈合扫描 = 最终一致性。

### 架构阻塞项

| 阻塞项 | 触发场景 | 当前缓解 |
|--------|----------|----------|
| **God Object 编排器** | 修改核心管道时影响面大 | 无 |
| **无 API 版本控制** | v0.4.0 Breaking Change 发布时前端崩溃 | 无 |
| **单进程文件锁** | 多实例部署 | 无（当前单实例） |

---

## 🔬 代码审查核心发现

### 各维度分布

| 维度 | 🔴 Critical | 🟠 High | 🟡 Medium | 🟢 Low | 总计 |
|------|------------|---------|-----------|--------|------|
| 安全性 | 2 | 1 | 3 | 1 | 7 |
| 性能 | 0 | 3 | 1 | 1 | 5 |
| 正确性 | 0 | 1 | 2 | 1 | 4 |
| 可维护性 | 1 | 0 | 3 | 4 | 8 |
| **总计** | **3** | **5** | **9** | **7** | **24** |

### 代码质量亮点

1. **文件操作原子性** — `write_json_atomic` 使用 `tempfile.mkstemp` + `os.replace`，崩溃安全
2. **SQL 参数化 100%** — 所有查询使用 `?` 占位符，无 SQL 注入
3. **并发隔离** — `asyncio.gather(return_exceptions=True)` 确保单源故障不中断扫描
4. **Fail-Closed 设计** — 低于阈值的模糊匹配记录但不评分，需人工验证
5. **验证门控** — 持久化前通过 Pydantic 验证数据（`EventRecord.model_validate`）
6. **确定性回退** — LLM 不可用时基于证据的确定性概率估算，降级而非失败

---

## 🚨 事故响应就绪度

### SEV 故障严重度预估矩阵

| 故障场景 | SEV | 检测 | 自动恢复 | RPN |
|----------|-----|------|----------|-----|
| LLM API Key 过期 | SEV1 | ❌ 无告警 | ❌ 无 | 600 |
| 进程崩溃 | SEV1 | ❌ 无守护 | ❌ 无 | 360 |
| misfire_grace_time 300s | SEV2 | ❌ 无 | ❌ 无后填 | 315 |
| 依赖版本浮动 | SEV2 | ❌ 无锁 | ❌ 无 | 210 |
| RSS 源静默失败 | SEV4 | ❌ 被吞掉 | ❌ 无重试 | 210 |
| 冷启动死锁 | SEV3 | ❌ 无 | ❌ 数月休眠 | 240 |
| CORS `*` + host `0.0.0.0` | SEV3 | ❌ 无 | ❌ 无 | 252 |

### 生产就绪度：🔴 NO-GO

**运维就绪度 ≈ 5%**（零可观测性、零自动化恢复、零备份）。

### 5 个 P0 阻塞项（必须修复才能部署）

| # | 阻塞项 | 工作量 |
|---|--------|--------|
| B-01 | 进程守护（systemd unit / PM2） | 低 |
| B-02 | 健康检查端点 `GET /api/health` | 中 |
| B-03 | 自动化每日备份（cron + tar） | 低 |
| B-04 | 依赖版本锁定（`pip freeze > requirements.lock`） | 低 |
| B-05 | 修复 `resolve_with_calibration` 写入顺序 | 低 |

---

## 🧪 测试质量评估

### 综合评分：B+（单元测试 A，集成测试 D，前端测试 F，CI/CD F）

| 测试类型 | 数量 | 占比 | 理想占比 | 评级 |
|---------|------|------|---------|------|
| 单元测试 | 338 | ~99% | 70% | A |
| 集成测试 | 1（跳过） | ~0.3% | 20% | D |
| E2E 测试 | 0 | 0% | 10% | F |
| 前端测试 | 0 | 0% | — | F |

### 测试亮点

- 精确边界断言（`self.assertEqual(self._clamp_strong(0.80), 43.25)`）
- 完全隔离（tempfile + unittest.mock + 无网络/时间/随机数依赖）
- 优秀回归保护（Bug #1, #7, M0/M1 里程碑均有对应回归测试）

### 测试关键缺失

| 优先级 | 缺失项 | 工作量 |
|--------|--------|--------|
| 🔴 P0 | API 集成测试套件（FastAPI + SQLite 端到端） | 3-5 天 |
| 🔴 P0 | gnews/openai/rss 服务测试 | 1.5 天 |
| 🔴 P0 | 前端组件测试（Vitest + Testing Library） | 3-5 天 |
| 🔴 P0 | CI/CD Pipeline（GitHub Actions） | 1 天 |
| 🟡 P1 | 事件生命周期集成测试 | 2 天 |
| 🟡 P1 | 并发/竞态测试 | 1 天 |

---

## 📝 文档质量评估

### 综合评分：5.75/10（合格偏弱）

| 维度 | 评分 | 等级 |
|------|------|------|
| README 可操作性 | 6.5/10 | 合格 |
| API 文档 | 7.5/10 | 良好 |
| 架构文档 | 5.0/10 | 不足 |
| 运维文档 | 6.0/10 | 合格 |
| 代码注释 | 5.5/10 | 合格偏弱 |
| 文档组织健康度 | 4.0/10 | 较差 |

### 最大风险

**HANDOFF.md（gitignored）承载了事实上的架构文档角色** — 一旦丢失，新开发者将失去对事件源、概率链路、数据流的全部上下文理解。

---

## ✅ 行动清单（按优先级排序）

### 🔴 P0 — 阻塞生产部署（立即修复，1-3 天）

| # | 行动 | 负责角色 | 紧急度 | 预期完成 |
|---|------|---------|--------|---------|
| 1 | **建立 CI/CD Pipeline** — `.github/workflows/ci.yml`，运行 338 个单元测试 + coverage 报告 | Backend | P0 | 1 天 |
| 2 | **修复 CORS 配置** — `main.py:41` 将 `allow_origins=["*"]` 改为可配置白名单，移除 `allow_credentials=True` 或配合具体源 | Backend | P0 | 1 行 |
| 3 | **依赖版本锁定** — 运行 `pip freeze` 生成 `requirements.lock.txt` | Backend | P0 | 15min |
| 4 | **进程守护** — 创建 systemd unit 或 PM2 配置，确保崩溃自动重启 | DevOps | P0 | 30min |
| 5 | **自动化每日备份** — `scripts/backup.sh`：tar 压缩 event_store.json + v2_loop.db + event_audit.jsonl，保留 30 天 | DevOps | P0 | 1h |
| 6 | **修复 resolve_with_calibration 写入顺序** — 将 score_prediction 移到 resolve_event 之前，防止崩溃产生孤儿预测 | Backend | P0 | 1h |
| 7 | **添加健康检查端点** — `GET /api/health` 返回调度器状态、最后运行时间、待处理预测数 | Backend | P0 | 2-3h |

### 🟡 P1 — 部署后第一周（高优先级）

| # | 行动 | 负责角色 | 紧急度 | 预期完成 |
|---|------|---------|--------|---------|
| 8 | 创建 `/api/v1/` 前缀，保留 `/api/` 重定向 | Backend | P1 | 2h |
| 9 | 关键端点添加 `response_model` | Backend | P1 | 3h |
| 10 | 添加速率限制中间件（slowapi）到 `/api/events/analyze`、`/discover`、`/resolve/auto` | Backend | P1 | 2h |
| 11 | 添加 LLM API 重试机制（3 次 + 指数退避 1s/2s/4s） | Backend | P1 | 2h |
| 12 | 启动时验证 LLM API Key 有效性 | Backend | P1 | 1h |
| 13 | 日志持久化 — `logging.py` 添加 RotatingFileHandler（10MB × 5） | Backend | P1 | 30min |
| 14 | 为 gnews/openai/rss 添加测试 | Backend | P1 | 1.5 天 |
| 15 | 补 `calibration_feedback_service.py` docstrings | Backend | P1 | 1h |

### 🟢 P2 — 第一个月

| # | 行动 | 负责角色 | 紧急度 | 预期完成 |
|---|------|---------|--------|---------|
| 16 | 创建 `docs/dev/ARCHITECTURE.md`（C4 图 + 数据流 + ADR） | Docu | P2 | 4h |
| 17 | 编写 Runbook（5 个核心故障场景恢复流程） | SRE | P2 | 3h |
| 18 | 拆分 `event_intelligence_service.py` God Object — 评分/报告/工具函数迁出 | Backend | P2 | 3 天 |
| 19 | 搭建前端测试框架（Vitest + Testing Library），覆盖关键组件 | Frontend | P2 | 3-5 天 |

### 🔵 P3 — 持续改进

| # | 行动 | 负责角色 | 紧急度 | 预期完成 |
|---|------|---------|--------|---------|
| 20 | 容器化（Docker Compose） | DevOps | P3 | 8h |
| 21 | 关键路径集成测试（analyze → store → resolve 全链路） | QA | P3 | 3 天 |
| 22 | 冷启动缓解 — 降低 `CALIBRATION_FEEDBACK_MIN_SAMPLES` 或实现 bootstrap 模式 | Backend | P3 | 4h |

---

## ⚠️ 待完善 / 已知局限

1. **审计基于静态代码分析**：未运行动态分析/渗透测试/负载测试。实际生产行为可能与分析有出入
2. **前端审查范围有限**：主要关注后端代码和架构。前端仅为粗粒度评估（零测试、无 Error Boundary）
3. **数据规模假设**：性能分析假设事件数量增长至数千条。当前 ~59 条记录下所有性能问题均为"待触发"
4. **LLM 调用成本未量化**：未实际测算每次 discover 的 LLM 调用次数和 API 费用
5. **3 个遗留服务**（`polymarket_service.py`, `polymarket_history_service.py`, `market_filter_service.py`）可能已废弃，需开发者确认后清理

---

## 📚 数据来源 & 成员产出索引

| 成员 | 角色 | 产出摘要 | 发现数 |
|------|------|----------|--------|
| **Cody** | 代码审查师 | 后端 14 个核心文件审查：安全/性能/正确性/可维护性 | 24 项（3C/5H/9M/7L） |
| **Archi** | 系统架构师 | 全栈架构评估：分层/API/数据流/可扩展性/ADR | 17 项架构债（5C/5H/5M/2L） |
| **Rex** | SRE 工程师 | 事故就绪度评估：FMEA/SEV矩阵/部署检查清单/Go-No-Go | 23 项行动建议（5P0/6P1/8P2/4P3） |
| **Tessa** | 测试专家 | 测试策略与质量：覆盖矩阵/金字塔分析/CI建议 | 12 项测试债（4P0/5P1/3P2） |
| **Docu** | 技术文档师 | 文档质量：6 维度评分/P0-P3 文档债/12 项缺失文档 | 13 项文档债（3P0/4P1/4P2/2P3） |

> 本报告由工程保障团队（甄宇航 + 科迪 + 阿奇 + 雷克斯 + 泰莎 + 多库）AI 协作生成，基于 2026-06-20 代码快照。关键决策请由人类工程负责人复核。
