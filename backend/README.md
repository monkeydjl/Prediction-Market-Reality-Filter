# Backend — Event Intelligence Platform

FastAPI 后端：事件发现、概率分析、存储与校准。完整系统总览见仓库根的 [README](../README.md)。

## 运行

```bash
cp .env.example .env          # 填入你自己的 API key
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests               # 无网络回归测试
python run.py                          # http://127.0.0.1:8000
```

`run.py` 默认绑定 `127.0.0.1`（`SERVER_HOST`）。写接口没有 `API_WRITE_KEY` 时需要
`ALLOW_OPEN_WRITES=true`，那是无鉴权的，所以 `run.py` 会拒绝「非本机地址 + 无 key」的组合；
要给别的机器访问就设 `API_WRITE_KEY`。生产的 Docker / systemd 直接给 uvicorn 传 `--host`，
不经过这个文件。先体检配置：`python -m scripts.check_write_auth`（0 已授权 / 1 会拒启 / 2 读不到）。

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

## 主要目录

```text
app/main.py                                  FastAPI app
app/api/routes/events.py                     Event Intelligence API
app/services/event_intelligence_service.py   发现 / 分析编排
app/services/cross_validation_service.py     多模型交叉验证（可选）
app/memory/event_store.py                    事件持久化
tests/                                        无网络回归测试（见 tests/README.md）
```

## 文档

- 安装与配置：[../docs/user/QUICK_START.md](../docs/user/QUICK_START.md)
- 测试套件（隔离机制、`clean_env`、manual 脚本）：[tests/README.md](tests/README.md)
- 产品愿景：[../docs/dev/Event Intelligence Platform.md](../docs/dev/Event%20Intelligence%20Platform.md)
- 历史进度日志（本地，未纳入版本控制）：`docs/PROJECT_PROGRESS.md`、`docs/工程进度.md`

## 验证约定

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m compileall app tests
python -m pytest tests
```

改前端仪表盘时，另需运行 QUICK_START 中覆盖中英文页面的 inline JS 语法检查。
