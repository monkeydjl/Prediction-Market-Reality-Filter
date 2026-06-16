# Backend — Event Intelligence Platform

FastAPI 后端：事件发现、概率分析、存储与校准。完整系统总览见仓库根的 [README](../README.md)。

## 运行

```bash
cp .env.example .env          # 填入你自己的 API key
pip install -r requirements.txt
python -m unittest discover -s tests   # 无网络回归测试
python run.py                          # http://localhost:8000
```

## 主要 API

| 能力 | 端点 |
|------|------|
| 手动事件分析 | `POST /events/analyze` |
| 事件发现 | `GET /events/discover` |
| 事件列表 / 详情 | `GET /events/` ・ `GET /events/{id}` |
| 概率历史 | `GET /events/{id}/history` |
| 最大变动事件 | `GET /events/movers` |
| 相似历史事件 | `GET /events/{id}/similar` |
| 事件校准 | `GET /events/calibration` |
| 经典仪表盘 | `/dashboard` ・ `/dashboard_zh` |

## 主要目录

```text
app/main.py                                  FastAPI app
app/api/routes/events.py                     Event Intelligence API
app/services/event_intelligence_service.py   发现 / 分析编排
app/services/cross_validation_service.py     多模型交叉验证（可选）
app/agents/                                  概率 / 叙事 LLM agents
app/memory/event_store.py                    事件持久化
static/                                       经典仪表盘（单文件）
tests/                                        无网络回归测试
```

## 文档

- 安装与配置：[../docs/user/QUICK_START.md](../docs/user/QUICK_START.md)
- 产品愿景：[../docs/dev/Event Intelligence Platform.md](../docs/dev/Event%20Intelligence%20Platform.md)
- 历史进度日志（本地，未纳入版本控制）：`docs/PROJECT_PROGRESS.md`、`docs/工程进度.md`

## 验证约定

```bash
python -m compileall app tests
python -m unittest discover -s tests
```

改前端仪表盘时，另需运行 QUICK_START 中覆盖中英文页面的 inline JS 语法检查。
