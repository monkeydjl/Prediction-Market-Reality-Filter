# Prediction Market Reality Filter v0.3.0 — 增量复核报告

**日期**：2026-06-20
**工作流**：综合增量审计（代码审查 + 架构评估 + SRE 就绪度 + 测试复核 + 文档复核）
**参与成员**：Cody（代码审查）、Archi（架构评估）、Rex（SRE 复核）、Tessa（测试复核）、Docu（文档复核）
**审计目的**：对比第一轮审计报告，逐一验证用户声称的修复项是否真实完成，更新残留阻塞项清单

---

## 📌 TL;DR（执行摘要）

- **整体结论**：声称完成的 5 项核心闭环修复**全部核实通过** ✅；当前全量后端测试可复现 **503 OK, 1 skipped**。但安全、运维、部署和 CI/CD 阻塞项仍基本未动，整体判定**仍为 NO-GO for unattended 90-day production**
- **验证通过率**：45 项检查中 12 项 ✅ / 1 项 ⚠️ / 32 项 ❌（总通过率 **27%**）
- **核心矛盾**：业务逻辑修复质量高（写入顺序/孤儿愈合/trust floor），但 DevOps 和安全面为零改善
- **P0 阻塞项**：安全/运维/部署类 P0 仍未修复（进程守护、自动备份、CORS、依赖锁定、CI/CD）；业务闭环一致性 P0（写入顺序、孤儿愈合、loop status）已完成核心修复

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 整体评级 | 🔴 仍为 NO-GO（业务逻辑 B+ → A-，运维就绪度 5% → 8%） |
| 声称修复完成 | ✅ 5 项全部验证通过 |
| 仍未修复阻塞项 | 安全/运维/部署类 P0 仍未修复（CORS、依赖锁定、进程守护、自动备份、CI/CD）；业务闭环一致性 P0 已完成 |
| 新增改善项 | 文件锁机制、calibration docstrings、LLM 主路径 retry |
| 建议下一步 | 优先修复安全基础设施（CORS + 认证 + 限流）→ 运维基础设施（进程守护 + 备份 + 日志） |

---

## 🔍 增量验证矩阵（45 项逐项对照）

### 一、声称修复项（5/5 ✅ — 全部确认完成）

| # | 检查项 | 判断 | 关键证据 | 验证成员 |
|---|--------|------|----------|----------|
| 1 | resolve_with_calibration 写入顺序 | ✅ | `event_resolve_service.py:149-154` — score_prediction 先于 resolve_event，含详细 crash 恢复注释 | Cody |
| 2 | orphan prediction reconciliation | ✅ | `event_resolve_service.py:160-203` — `reconcile_predictions()` 在 auto_resolve 开头调用（L219） | Cody |
| 3 | freeze 时 verified link seeding | ✅ | `prediction_store.py:268-277` — `upsert_link(verified=True, link_confidence=1.0)` 仅在首次 freeze 播种 | Cody |
| 4 | trust floor | ✅ | `diagnosis_service.py:50` — `qualified_floor` 默认 0.1（`config.py:198-200`），防止吸收态 | Cody |
| 5 | loop run ledger + /api/events/loop/status | ✅ | `loop_run_store.py` + `loop_status_service.py` + `events.py:108-113` — 三组件完整连线 | Cody |

### 二、额外发现的新增改善（3 项）

| # | 检查项 | 判断 | 关键证据 | 验证成员 |
|---|--------|------|----------|----------|
| 6 | 文件锁 fcntl → threading.RLock | ✅ | `file_store.py:13-31` — 全局 `_LOCKS` 字典 + `threading.RLock`，移除 fcntl 依赖 | Archi |
| 7 | calibration_feedback_service.py docstrings | ✅ | 3/3 核心函数完成：`component_weights`(L112)、`category_shrinkage`(L138)、`adjust_probability`(L180) | Docu |
| 8 | LLM 主路径 retry | ⚠️ | `probability_engine_service.py:76-81` 有 `max_retries=2`；但旧 agent `openai_service.py` 无 retry | Rex |

### 三、安全基础设施（4/4 ❌）

| # | 检查项 | 判断 | 关键证据 | 验证成员 |
|---|--------|------|----------|----------|
| 9 | CORS 配置 | ❌ | `main.py:41-42` — `allow_origins=["*"]` + `allow_credentials=True` — CORS 规范禁止的组合 | Cody+Rex |
| 10 | 认证/授权 | ❌ | 全项目零 JWT/API key/session。写操作端点完全开放 | Cody |
| 11 | 速率限制 | ❌ | 全项目零 slowapi/throttle/RateLimiter | Cody |
| 12 | 标准 /api/health | ❌ | 无 `/api/health` 端点。`/api/events/loop/status` 存在但非通用 health check | Cody+Rex |

### 四、运维基础设施（7/7 ❌）

| # | 检查项 | 判断 | 关键证据 | 验证成员 |
|---|--------|------|----------|----------|
| 13 | 进程守护 | ❌ | 无 systemd/PM2/Docker supervisor | Rex |
| 14 | 自动化备份 | ❌ | 仅一个手工 `backup-20260612-181108.tar.gz` | Rex |
| 15 | 日志持久化 | ❌ | `logging.py:5` — `basicConfig` → stdout only | Rex |
| 16 | 启动时 Key 校验 | ❌ | run.py/main.py lifespan 无 LLM Key 校验 | Rex |
| 17 | misfire_grace_time | ❌ | `scheduler.py:30` 仍为 300s | Rex |
| 18 | 独立调度器 | ❌ | APScheduler 与 API 进程耦合，无外部 cron 触发 | Rex |
| 19 | 告警/Dead-man switch | ❌ | 零外部监控/零 webhook/零 health check ping | Rex |

### 五、架构改进项（8/9 ❌）

| # | 检查项 | 判断 | 关键证据 | 验证成员 |
|---|--------|------|----------|----------|
| 20 | 文件锁机制 | ✅ | 见 #6 — 唯一修复的架构项 | Archi |
| 21 | API 版本控制 | ❌ | 有 `/api` 前缀但无 `/api/v1/` | Archi |
| 22 | response_model | ❌ | 全后端零 `response_model=` 使用 | Archi |
| 23 | 事件源抽象接口 | ❌ | 无 Protocol/ABC，仅靠命名约定 | Archi |
| 24 | `_clamp01` / `_now()` 去重 | ❌ | `_clamp01` 2 处（签名不同！）；`_now()` 4 处 | Archi |
| 25 | God Object 拆分 | ❌ | 仍 697 行，评分函数全部未迁出 | Archi |
| 26 | 配置分组 | ❌ | Settings 类 226 行完全扁平 | Archi |
| 27 | 遗留服务清理 | ❌ | 3 个文件均在且活跃被 import | Archi |
| 28 | Semaphore(4) 配置化 | ❌ | 硬编码，无配置入口 | Archi |

### 六、测试改进（2/6 ✅，4/6 ❌）

| # | 检查项 | 判断 | 关键证据 | 验证成员 |
|---|--------|------|----------|----------|
| 29 | 测试文件数量 | ✅ | 仍为 39 个 test_*.py，含新增文件 `test_loop_run_store.py` 及 5 个被修改的测试文件；全量 unittest 可通过 | Tessa |
| 30 | 测试计数验证 | ✅ | 可复现：`python -m unittest discover -s tests` → **503 tests OK, 1 skipped** | Tessa (经 Codex 复核确认) |
| 31 | gnews/openai/rss/config 测试 | ❌ | 4 个缺口全部仍无测试文件 | Tessa |
| 32 | CI Pipeline | ❌ | `.github/workflows/` 不存在 | Tessa |
| 33 | 覆盖率配置 | ❌ | .coveragerc/pyproject.toml/pytest.ini 全缺 | Tessa |
| 34 | 前端测试 | ❌ | 零测试文件、零测试框架、package.json 无 test 命令 | Tessa |

### 七、文档改进（2/10 准 ✅，8/10 ❌）

| # | 检查项 | 判断 | 关键证据 | 验证成员 |
|---|--------|------|----------|----------|
| 35 | calibration docstrings | ✅ | 见 #7 | Docu |
| 36 | HANDOFF.md 在 .gitignore | ✅ | 预期行为 | Docu |
| 37 | ARCHITECTURE.md | ❌ | 仍未创建 | Docu |
| 38 | ADR 目录 | ❌ | `docs/dev/adr/` 不存在 | Docu |
| 39 | RUNBOOK.md | ❌ | `docs/ops/` 目录不存在 | Docu |
| 40 | Dockerfile | ❌ | 零容器化文件 | Docu+Rex |
| 41 | 文档索引 | ❌ | 无 docs/README.md | Docu |
| 42 | CHANGELOG | ❌ | 不存在 | Docu |
| 43 | 过时数据更新 | ❌ | "141 tests" 未更新 | Docu |
| 44 | docs/user/ 清理 | ❌ | 6 个代码审查文件未清理 | Docu |
| 45 | 依赖锁定 | ❌ | requirements.txt 全部 9 个依赖无版本号 | Cody |

---

## 📊 各维度通过率对比

| 维度 | 检查项数 | ✅ | ⚠️ | ❌ | 通过率 |
|------|---------|----|----|----|--------|
| 声称修复项 | 5 | 5 | 0 | 0 | **100%** |
| 新增改善 | 3 | 2 | 1 | 0 | 67% |
| 安全基础设施 | 4 | 0 | 0 | 4 | **0%** |
| 运维基础设施 | 7 | 0 | 0 | 7 | **0%** |
| 架构改进 | 9 | 1 | 0 | 8 | **11%** |
| 测试改进 | 6 | 2 | 0 | 4 | **33%** |
| 文档改进 | 10 | 2 | 0 | 8 | **20%** |
| 依赖锁定 | 1 | 0 | 0 | 1 | **0%** |
| **合计** | **45** | **12** | **1** | **32** | **27%** |

> 注：若仅计算"需修复项"（排除声称修复已完成的 5 项 + 预期的 HANDOFF.md），则 39 项中 4 项完成（**10%**），1 项部分改善（LLM retry）。

---

## 🏗️ 为什么第一轮审计中的某些问题仍在报告中？

审计报告反映的是**审计时刻的代码快照**。第一轮于 2026-06-20 进行，用户在此之后进行了修复。第二轮增量复核逐一验证了每个声称修复：

| 第一轮报告中的问题 | 修复状态 | 第二轮验证 |
|---|------|------|
| resolve_with_calibration 写入顺序（Rex B-05） | ✅ 已修复 | `score_prediction` 先于 `resolve_event` |
| 孤儿预测愈合（Rex B-05 相关） | ✅ 已修复 | `reconcile_predictions()` 已添加 |
| freeze verified link seeding | ✅ 已修复 | `upsert_link(verified=True)` 已添加 |
| trust floor | ✅ 已修复 | `qualified_floor=0.1` 已添加 |
| loop run ledger + /api/events/loop/status | ✅ 已修复 | 三组件完整 |
| 文件锁 fcntl → RLock（Archi AD-9） | ✅ 已修复 | `threading.RLock` 替代 |
| calibration docstrings（Docu P1） | ✅ 已修复 | 3/3 函数完成 |
| LLM retry（Rex R-01） | ⚠️ 部分 | 主路径有，旧 agent 路径无 |

**结论**：报告中的问题不是误报 — 它们在第一轮审计时确实存在。用户随后修复了核心逻辑层面（写入顺序/孤儿愈合等），但安全/运维/架构/测试/文档层面的大量问题仍未触及。

---

## ✅ 行动清单（更新后，按优先级排序）

### 🔴 P0 — 安全/运维/部署阻塞项（仍未修复）

| # | 行动 | 第一轮状态 | 当前状态 | 预计工作量 |
|---|------|-----------|---------|-----------|
| 1 | 修复 CORS 配置（`allow_origins` 改为具体域名） | ❌ | ❌ 仍为 `["*"]` | 1 行 |
| 2 | 添加最小认证机制（API key middleware 保护写操作） | ❌ | ❌ | 2h |
| 3 | 添加速率限制（slowapi 保护 LLM 端点） | ❌ | ❌ | 2h |
| 4 | 依赖版本锁定（`requirements.txt` → `>=` 下限或精确锁） | ❌ | ❌ | 15min |
| 5 | 创建 CI/CD Pipeline（`.github/workflows/ci.yml`） | ❌ | ❌ | 1h |
| 6 | 进程守护（systemd/PM2） | ❌ | ❌ | 30min |
| 7 | 自动化每日备份（cron + tar） | ❌ | ❌ | 1h |
| 8 | 日志持久化（RotatingFileHandler） | ❌ | ❌ | 30min |

### 🟡 P1 — 强烈建议

| # | 行动 | 状态 |
|---|------|------|
| 9 | 添加标准 `/api/health` 端点 | ❌ |
| 10 | 启动时 LLM Key 有效性校验 | ❌ |
| 11 | misfire_grace_time 扩展（300s → 86400s） | ❌ |
| 12 | 旧 agent LLM 路径添加 retry | ⚠️ 主路径已改善 |
| 13 | 添加 API 版本前缀 `/api/v1/` | ❌ |
| 14 | 关键端点添加 response_model | ❌ |
| 15 | 创建 Dockerfile + docker-compose.yml | ❌ |

### 🟢 P2 — 技术债

| # | 行动 | 状态 |
|---|------|------|
| 16 | 创建 `docs/dev/ARCHITECTURE.md` + ADR 目录 | ❌ |
| 17 | 编写 Runbook | ❌ |
| 18 | 拆分 `event_intelligence_service.py` God Object | ❌ |
| 19 | 统一 `_clamp01` / `_now()` 定义 | ❌ |
| 20 | 补 gnews/openai/rss/config 测试 | ❌ |
| 21 | 搭建前端测试框架 | ❌ |

---

## ⚠️ 待完善 / 已知局限

1. **测试计数已验证**：经 Codex 复核确认，当前环境可复现 `python -m unittest discover -s tests` → **503 tests OK, 1 skipped**。第二轮审计中因沙箱依赖限制未能加载全部模块，导致此处结论偏差。
2. **运行时行为未验证**：审计基于静态代码分析，未实际运行系统观察行为
3. **遗留服务判定**：`polymarket_service.py` 等 3 个文件在第一轮被标记为"遗留"，但实际仍被生产代码 import。它们可能是 adapters 的底层依赖而非真正的废弃代码
4. **Semaphore(4) 合理性**：虽硬编码但可能已针对当前 LLM provider 调优。配置化是良好实践，但当前值可能已是最优

---

## 📚 数据来源 & 成员产出索引

| 成员 | 角色 | 产出摘要 | 检查项 |
|------|------|----------|--------|
| **Cody** | 代码审查师 | 10 项增量代码审查：5 项声称修复全部确认 ✅，CORS/认证/限流全 ❌ | 10 |
| **Archi** | 系统架构师 | 9 项架构复核：仅文件锁 1 项修复，其余 8 项未动 | 9 |
| **Rex** | SRE 工程师 | 10 项 SRE 复核：5 个原始 P0 全部未修复，主路径 LLM retry 改善 | 10 |
| **Tessa** | 测试专家 | 6 项测试复核：测试计数已由外部复核确认 503 OK，新增 `test_loop_run_store.py`，CI/CD/前端测试仍缺失 | 6 |
| **Docu** | 技术文档师 | 10 项文档复核：仅 docstrings + HANDOFF.md 2 项准 ✅ | 10 |

---

> 本报告由工程保障团队 AI 协作生成，基于 2026-06-20 第二轮增量复核代码快照。关键决策请由人类工程负责人复核。
