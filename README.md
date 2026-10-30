# Prediction Market Reality Filter

AI 驱动的**事件情报与概率变化分析平台**，内置 **Sports Prediction OS** —— 基于 Protocol 接口的多体育预测内核。

```text
公开信息 → 候选事件 → 证据评分 → 概率变化 → 情报报告 → 人工审阅
                                        ↓
                      Sports Prediction OS（Phase 1-13）
```

它**不是**新闻聚合器、自动交易机器人，也不是只围绕某个预测市场的扫描器。Polymarket / Kalshi 只是事件来源与概率基线之一。

## 预测市场发现

当前默认发现源：**Polymarket**、**Kalshi**、公共 **Limitless** adapter。Opinion 与 Predict.fun 已具备真实 adapter 接入路径，但分别需要 `OPINION_API_KEY` 和 `PREDICT_FUN_API_KEY` —— 没有密钥时 fail closed，不贡献事件。Probable 保持计划接入状态，直到官方 API / indexer / 合约事件接口被验证。新增链上 adapter 暂不参与自动结算。

## 架构总览

| 部分 | 技术 | 说明 |
|------|------|------|
| `backend/` | FastAPI + Python 3.11 | 事件发现 / 分析 / 存储 / 校准 API，多源采集，多模型交叉验证；内置 Sports Prediction OS |
| `frontend/` | Next.js 16 + React 19（静态导出） | 仪表盘 UI，`output: "export"` 构建到 `frontend/out/`；dev 模式由 Next 代理 `/api` 到后端 |
| 存储 | SQLite + JSON 文件 | 无重型数据库（[ADR-001](docs/dev/adr/001-json-file-store.md)）；状态文件部署时统一挂到 `/app/data` 卷 |
| CI | GitHub Actions（5 job） | backend-tests（ruff + mypy + pytest + pip-audit + 覆盖率）/ frontend-tests（typecheck + vitest + lint + build）/ type-sync-check / docker-build / secret-scan |

### 后端分层

| 层 | 目录 | 职责 |
|----|------|------|
| API | `backend/app/api/routes/` | 17 个路由文件：`/events`、`/predictions`、`/review-queue`、`/betting`、`/futures`、`/analytics`、`/world-cup/*`、`/ws`（WebSocket 实时推送）等；写接口经 X-API-Key 鉴权 |
| Services | `backend/app/services/` | 业务服务层（157 个服务）：事件智能、LLM 网关、校准闭环、结论挑战、体育事实/信号/结算、数据源客户端等 |
| Kernel | `backend/app/kernel/` | 运动无关预测核心：Protocol 协议（DataAdapter / FeatureBuilder / PredictionEngine / LearningService）、11 个预测引擎（Elo-Odds、Dixon-Coles、BTD、GBM、Ensemble、Situational 等）、回测框架 + Bayesian 参数优化 |
| Sports | `backend/app/sports/` | 体育专用实现：football / basketball / baseball / hockey / lol + `_shared`（Elo 计算器、队伍别名等） |
| Memory | `backend/app/memory/` | 13 个状态 store：event、prediction、review queue、decision timeline、simulated trade、source trust registry 等 |
| Realtime | `backend/app/realtime/` | WebSocket 连接管理 + 票据鉴权 + 价格推送 |
| Replay | `backend/app/replay/` | 回放评估：在冻结事件记录上重跑 Phase 1-5 overlay |

### 前端页面

`frontend/src/app/`：仪表盘 `/`、`analyze`、`events`、`decisions`、`digest`（每日情报摘要）、`edges`、`history`、`quality`、`quality-metrics`、`review-queue`、`trades`，以及 `sports/`（betting / edges / futures / markets / recommendations / settlements / optimization / world-cup 等）。

组件库在 `frontend/src/components/`（dashboard / decisions / edges / review / sports / ui 等子目录）。

## Sports Prediction OS

Phase 1-13 构建的多体育预测内核，覆盖 10 个赛事（世界杯 + 6 大足球联赛 + NBA + MLB + NHL），约 6,872 场/年。详见 [CHANGELOG.md](CHANGELOG.md)（最新正式版本 v0.4.0）。

### 赛事 ID 前缀路由

`match_id` 前缀决定 MultiAdapter / MultiFeatureBuilder 派发到具体体育实现：

| 前缀 | 赛事 | 引擎 |
|------|------|------|
| `wc-` | FIFA World Cup | EloOddsEngine (football) |
| `ucl-` / `epl-` / `laliga-` / `bundesliga-` / `seriea-` / `ligue1-` | 6 大足球联赛 | EloOddsEngine (football) |
| `nba-` | NBA | BasketballEngine (Bradley-Terry, 4 factors) |
| `mlb-` | MLB | BaseballEngine (Bradley-Terry, 5 factors) |
| `nhl-` | NHL | HockeyEngine (Bradley-Terry, 5 factors) |
| `lol-` | 英雄联盟电竞 | 见 [docs/dev/ESPORTS_BOUNDARY.md](docs/dev/ESPORTS_BOUNDARY.md) 与 [ADR-004/005](docs/dev/adr/) |

### Feature Flag

所有 Phase 默认 OFF，关闭时回退到既有行为：

- `KERNEL_PREDICTION_ENABLED` — 主开关（关闭时 `/api/predictions/*` 返回 503）
- `PHASE2_LEAGUES_ENABLED` — 6 大足球联赛；`PHASE3_LEARNING_ENABLED` — 学习闭环
- `PHASE4_NBA_ENABLED` / `PHASE5_MLB_ENABLED` / `PHASE5_NHL_ENABLED` — NBA / MLB / NHL
- `PHASE7_SPORT_MARKET_BRIDGE_ENABLED` / `PHASE7_SPORT_RECOMMENDATION_ENABLED` / `PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED` — 市场桥接（三层匹配：rule ≥0.9 → LLM ≥0.85 → 人工门禁）/ 推荐 / 结算反馈
- `PHASE8_CALIBRATION_FUSION_ENABLED` — 校准融合；`PHASE9_ACCURACY_SPRINT_ENABLED` — 回测 + 调参
- `PHASE10_REALTIME_PUSH_ENABLED` — WebSocket 实时推送；`PHASE11_KALSHI_SPORTS_ENABLED` — Kalshi 体育市场；`PHASE12_FUTURES_MARKETS_ENABLED` — 期货/冠军市场

### 零侵入约束

`prediction_kernel.py`、`domain.py`、`learning_service.py`、`engines/*.py` 在 Phase 1-13 全周期均未修改 —— 所有体育差异通过 Protocol 接口注入。新 DB 表使用 `kernel_` 前缀，既有 `world_cup_predictions.db` 表结构不变。

## 快速开始

### 一键启动（Windows）

```bat
start.bat          :: 生产：装依赖 → 确保 .env → 按需构建前端 → 后端 :8000 + 静态前端 :3000（两窗口，自动开浏览器）
start.bat build    :: 同上，但强制重新构建前端
start.bat dev      :: 开发：后端 :8000 热重载 + Next dev :3000（两窗口）
start.bat check    :: 只体检：报告解释器与写鉴权配置，不装依赖、不起服务。退出码 1 = 启动会失败
```

启动器优先用项目自带 `.venv\Scripts\python.exe`，没有才退回 PATH 上的 `python`；启动前会自动释放 8000/3000 端口上残留的监听进程。

### 写接口鉴权（fail-closed）

写接口需要 `API_WRITE_KEY`（请求头 `X-API-Key`）或 `ALLOW_OPEN_WRITES=true`，两个都没有时后端**拒绝启动**。`start.bat check` 会在起服务前把这件事说清楚。`ALLOW_OPEN_WRITES=true` 仅限本机：`run.py` 默认只绑定 `127.0.0.1`（`SERVER_HOST`），并拒绝「非本机地址 + 无 key」组合。生成 key：

```bash
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

### 手动启动

```bash
# 后端
cd backend
cp .env.example .env          # 填入你自己的 API key + 写鉴权配置
pip install -r requirements.txt
python run.py

# 前端（生产构建）
cd frontend
npm install
npm run build
```

访问：

- 仪表盘：http://localhost:3000（`start.bat` 生产模式）；或设 `BACKEND_SERVE_FRONTEND=true` 由后端在 http://localhost:8000 单端口托管
- API 文档：http://localhost:8000/docs（生产可用 `OPENAPI_ENABLED=false` 关闭）

## 配置

后端配置在 `backend/.env`（参考 `backend/.env.example`，约千行、每项带中文注释）。关键分组：

- **LLM 网关**：编号式多提供商路由（`OPENAI_API_KEY_N` / `MODEL_N_M` / `BASE_URL_N`），八条专用路由（默认 / 概率分析 / 翻译 / 开放信息抽取 / 交叉验证 / 世界杯 / 启动自检 / embedding）；具名提供商 DeepSeek / DashScope / OpenAI / OpenRouter；`LLM_DAILY_COST_CAP_USD` 每日花费上限；`LLM_STARTUP_CHECK_ENABLED=true` 启动期探测，凭据无效拒绝启动。
- **安全与限流**：`API_WRITE_KEY` / `ALLOW_OPEN_WRITES`、CORS、`RATE_LIMIT_*`、`TRUSTED_PROXY_HEADER`、`OPENAPI_ENABLED`。
- **环境**：`PMRF_ENV`（development / staging / production）决定叠加哪个 `.env.overlay`；生产触发 preflight 门禁。
- **分析叠加层（Phase 1-5，默认全关）**：决策质量 / 市场质量 / 预测校准 / 来源可信度 / LLM 遥测 / 人工审阅队列 —— 关闭时前端只认显式 `false` 标注「未启用」，不显示无解释的 0。
- **调度与运维**：`SCHEDULER_ENABLED`（本地 `true`；systemd 部署时 API unit 覆盖为 `false`，由独立 scheduler unit 跑）、`EVENT_DISCOVER_*`、备份加密、`PMRF_HEALTHCHECK_URL` / `PMRF_DEADMAN_URL` dead-man ping、Sentry / 漂移告警。
- **数据源**：世界杯多源 bundle（文件 / URL / raw feeds / API-Football / SportMonks 五路 preview→import，全部 fail-closed 校验 `source` / `observed_at` 与快照年龄）、Odds API、天气 / xG / 裁判 / 伤停 / 赛程等外部 provider 契约、`SOURCE_WEIGHT_*` 多源权重。
- **存储路径**：`LOOP_DB_FILE`、`KERNEL_DB_FILE`、`EVENT_STORE_FILE` 等全部可重定向到挂载卷。

## 测试与质量

```bash
# 后端（436 个测试文件）
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m compileall app tests
python -m pytest tests

# 前端（127 个测试文件，vitest）
cd frontend
npm run typecheck && npm test && npm run lint && npm run build

# 前端 TS 类型由后端生成，不手写 interface
cd backend && python scripts/generate_types.py
```

质量工具链（`backend/scripts/`）：

- **`mutation_verify.py`** — 字节级变异验证 harness（15 套 / 300+ 条变异）：对每条守卫测试做「变异后必须变红 → 还原后必须重新变绿 → sha256 逐字节一致」四阶段验证，证明测试真的锁住了行为。
- **`eol_audit.py`** — 行尾符损坏审计（字节画像判据）。
- **`report_probability_scale_outliers.py`** — 常驻只读探针，报告疑似 0-1 刻度错位的存量概率。
- `backup_stores.py` / `restore_stores.py` / `verify_store_counts.py` — 备份恢复与数量核对。
- `healthcheck.py` — 本地 `/api/health` 确认 + dead-man ping。
- `smoke_check.py` / `verify_local_stack.py` / `check_write_auth.py` — 冒烟与配置体检。

## 部署

单源部署：FastAPI 在 `/` 服务 SPA，API 在 `/api/*`。

### Docker Compose

```bash
cd deploy
docker compose up -d --build
```

`deploy/docker-compose.yml`：仅绑定 `127.0.0.1:8000`（TLS 交给前置 nginx / caddy，示例见 `deploy/nginx.conf.example` / `deploy/Caddyfile.example`，会屏蔽 `/docs` `/redoc` `/openapi.json`）、`PMRF_ENV=production` 触发 preflight、状态文件挂载到 named volume、资源上限 + python 版 healthcheck。

### systemd（6 个 unit）

| Unit | 职责 |
|------|------|
| `prediction-market-reality-filter.service` | API 主服务（uvicorn，回环绑定 + 沙箱加固，`SCHEDULER_ENABLED=false`） |
| `...-scheduler.service` | 独立调度进程（`scripts/run_scheduler.py`） |
| `...-healthcheck.service` + `.timer` | 定时跑 `scripts/healthcheck.py` |
| `...-backup.service` + `.timer` | 每日跑 `scripts/backup_stores.py` |

## 文档

| 文档 | 用途 |
|------|------|
| [docs/user/QUICK_START.md](docs/user/QUICK_START.md) | 安装、配置、运行、测试 |
| [docs/user/USER_GUIDE.md](docs/user/USER_GUIDE.md) ・ [中文使用教程](docs/user/中文使用教程.md) | 使用教程 |
| [docs/user/ARCHITECTURE_PHILOSOPHY.md](docs/user/ARCHITECTURE_PHILOSOPHY.md) ・ [docs/dev/ARCHITECTURE.md](docs/dev/ARCHITECTURE.md) | 架构与设计哲学 |
| [docs/dev/Event Intelligence Platform.md](docs/dev/Event%20Intelligence%20Platform.md) | 产品愿景与边界 |
| [docs/dev/DESIGN.md](docs/dev/DESIGN.md) ・ [PRODUCT.md](docs/dev/PRODUCT.md) | 设计系统与产品上下文 |
| [docs/dev/WORLD_CUP_PREDICTION_SYSTEM_DESIGN.md](docs/dev/WORLD_CUP_PREDICTION_SYSTEM_DESIGN.md) | 世界杯预测系统设计与后续优先级 |
| [docs/user/WORLD_CUP_FACTS_GUIDE.md](docs/user/WORLD_CUP_FACTS_GUIDE.md) | 世界杯 facts 导入、预览结算和样例流程 |
| [docs/dev/provider-contract/](docs/dev/) | 外部数据源 provider 契约（约 12 份） |
| [docs/dev/adr/](docs/dev/adr/) | 架构决策记录（ADR 001-005） |
| [docs/ops/RUNBOOK.md](docs/ops/RUNBOOK.md) | 运维手册 |
| [docs/reviews/](docs/reviews/) | 代码评审、系统健康审计、多模型审计、上线就绪评审 |
| [docs/superpowers/specs/](docs/superpowers/specs/) ・ [plans/](docs/superpowers/plans/) | Phase 1-13 的完整 spec 和 plan |
| [docs/archive/](docs/archive/) | 历史里程碑与过程文档 |

## 许可

见 [LICENSE](LICENSE)。
