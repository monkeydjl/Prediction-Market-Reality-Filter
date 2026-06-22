# Prediction Market Reality Filter

AI 驱动的**事件情报与概率变化分析平台**。采集公开信息，抽取证据、评估可信度，估计未来事件的发生概率如何变化，并辅助人工判断是否值得持续跟踪。

```text
公开信息 → 候选事件 → 证据评分 → 概率变化 → 情报报告 → 人工审阅
```

它**不是**新闻聚合器、自动交易机器人，也不是只围绕某个预测市场的扫描器。Polymarket / Manifold / Kalshi 只是事件来源与概率基线之一。

## 架构

| 部分 | 技术 | 说明 |
|------|------|------|
| `backend/` | FastAPI + Python | 事件发现 / 分析 / 存储 / 校准 API，多源采集，多模型交叉验证 |
| `frontend/` | Next.js 16 (静态导出) | 仪表盘 UI，构建后由后端在 `/` 路径一并服务 |

生产模式下前端构建到 `frontend/out/`，由 FastAPI 同源服务，整个系统跑在一个端口（`:8000`）。

## 快速开始

### 一键启动（Windows）

```bat
start.bat          :: 生产：构建前端(按需) + 起后端，全部在 :8000
start.bat build    :: 强制重新构建前端后再起
start.bat dev      :: 开发：后端 :8000 + 前端热重载 :3000（两窗口）
```

### 手动启动

```bash
# 后端
cd backend
cp .env.example .env          # 填入你自己的 API key
pip install -r requirements.txt
python run.py

# 前端（生产构建，产物由后端服务）
cd frontend
npm install
npm run build
```

访问：

- 仪表盘：http://localhost:8000
- API 文档：http://localhost:8000/docs

## 配置

后端配置在 `backend/.env`（参考 `backend/.env.example`）。关键项：

- `OPENAI_API_KEY` / `OPENAI_BASE_URL`：LLM 提供商（默认阿里云 DashScope 兼容接口）。
- `OPENAI_MODEL`：主分析模型。
- `LLM_STARTUP_CHECK_ENABLED=true`：生产可开启启动期 LLM 探测，key/model/base URL 无效时拒绝启动。
- `SCHEDULER_ENABLED`：本地默认 `true`；systemd 部署时 API unit 会覆盖为 `false`，由独立 scheduler unit 运行定时任务。
- `PMRF_DEADMAN_URL`：生产可配置外部 dead-man ping；systemd healthcheck 会先确认本地 `/api/health` 为 ok，再 ping 该 URL。
- `CROSS_VALIDATION_MODEL` / `OPEN_WEB_EXTRACTION_MODEL`：可选能力的模型，留空则关闭。
- `WORLD_CUP_SOURCE_ENABLED`：启用 2026 世界杯策划事件源，丰富发现流的体育候选事件。
- `SPORTS_FACT_FILE`：结构化世界杯事实 JSON 文件，用于 sports signals 和自动结算。
- `WORLD_CUP_DATA_FILE`：可信世界杯数据源快照 JSON 文件，可通过 source preview/import 转换为 facts。
- `WORLD_CUP_SOURCE_BUNDLE_FILE`：多源世界杯数据源快照 JSON 文件，可通过 bundle source preview/import 一次转换多路 feed。
- `WORLD_CUP_SOURCE_BUNDLE_URL`：可信远程多源 bundle JSON URL，可通过 bundle URL preview/import 拉取后转换为 facts；如需鉴权，用 `WORLD_CUP_SOURCE_BUNDLE_AUTH_HEADER` / `WORLD_CUP_SOURCE_BUNDLE_AUTH_VALUE`。
- `WORLD_CUP_MATCH_SOURCE_URL` / `WORLD_CUP_MATCH_EVENTS_SOURCE_URL` / `WORLD_CUP_LINEUPS_SOURCE_URL` / `WORLD_CUP_STANDINGS_SOURCE_URL` / `WORLD_CUP_PLAYER_AWARDS_SOURCE_URL` / `WORLD_CUP_PLAYER_STATUS_SOURCE_URL`：可选 raw feed URL；通过 bundle feeds preview/import 拉取后组装成多源 bundle。
- `WORLD_CUP_API_FOOTBALL_API_KEY`：可选 API-Football provider key；配置后可通过 bundle api-football preview/import 拉取 fixtures、standings、top scorers、injuries；`WORLD_CUP_API_FOOTBALL_FETCH_EVENTS=true` 时会额外按 fixture 拉取 card/event rows，`WORLD_CUP_API_FOOTBALL_FETCH_LINEUPS=true` 时会额外按 fixture 拉取 starting XI / bench rows。
- `WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED=true`：可选定时导入多源 bundle；默认关闭，模式由 `WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE=url|file|feeds|api_football` 决定。
- `WORLD_CUP_DATA_MAX_AGE_HOURS`：配置源快照最大年龄，默认 168 小时；配置文件导入会拒绝缺少 `source` / `observed_at` 或过期的快照。

## 验证

```bash
cd backend
python -m compileall app tests
python -m unittest discover -s tests

cd ../frontend
npm run build
```

## 文档

| 文档 | 用途 |
|------|------|
| [docs/user/QUICK_START.md](docs/user/QUICK_START.md) | 安装、配置、运行、测试 |
| [docs/user/USER_GUIDE.md](docs/user/USER_GUIDE.md) ・ [中文使用教程](docs/user/中文使用教程.md) | 使用教程 |
| [docs/dev/Event Intelligence Platform.md](docs/dev/Event%20Intelligence%20Platform.md) | 产品愿景与边界 |
| [docs/dev/DESIGN.md](docs/dev/DESIGN.md) ・ [PRODUCT.md](docs/dev/PRODUCT.md) | 设计系统与产品上下文 |
| [docs/dev/WORLD_CUP_PREDICTION_SYSTEM_DESIGN.md](docs/dev/WORLD_CUP_PREDICTION_SYSTEM_DESIGN.md) | 世界杯预测系统设计与后续优先级 |
| [docs/user/WORLD_CUP_FACTS_GUIDE.md](docs/user/WORLD_CUP_FACTS_GUIDE.md) | 世界杯 facts 导入、预览结算和样例流程 |
| [docs/dev/INTEGRATION_TEST_REPORT.md](docs/dev/INTEGRATION_TEST_REPORT.md) | 端到端集成验证记录 |
| [docs/archive/](docs/archive/) | 历史里程碑与过程文档 |

## 许可

见 [LICENSE](LICENSE)（如未添加，请补充）。
