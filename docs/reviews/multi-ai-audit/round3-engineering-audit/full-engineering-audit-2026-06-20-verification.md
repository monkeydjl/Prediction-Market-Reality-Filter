# full-engineering-audit-2026-06-20 复核附录

日期：2026-06-20

范围：核对 `docs/Review-doc/3/full-engineering-audit-2026-06-20.md` 与当前未提交工作区是否一致。

## 总体结论

原报告总体方向仍成立：系统仍未达到 90 天无人值守生产标准。

但报告是旧快照，部分事实已经过期。当前工作区已经补上了部分核心审计修复，包括：

- `resolve_with_calibration` 写入顺序已调整为先 `score_prediction` / `void_prediction`，再 `resolve_event` 写 event outcome。
- 已新增 `reconcile_predictions()`，`auto_resolve_events()` 每次运行前会修复 event 已 resolved 但 prediction 仍 open 的 orphan。
- `freeze_prediction()` 首次冻结市场事件时会 seed verified event-contract link，contract-first auto-resolve 主路径已经真正可用。
- 已新增 `DIAGNOSIS_TRUST_FLOOR`，避免低质量 category 永久进入 trust=0 吸收态。
- 已新增 `loop_runs` 持久化运行账本。
- 已新增 `/api/events/loop/status`，返回 scheduler 状态、最近 run、event/prediction/link/orphan/calibration 计数。
- 当前后端测试为 `503 tests OK, 1 skipped`，不是报告中的 `338`。

## P0 逐项核对

| 原报告项 | 当前状态 | 复核结论 |
|---|---|---|
| CI/CD Pipeline 缺失 | `.github/PULL_REQUEST_TEMPLATE.md` 存在，但没有 `.github/workflows/ci.yml` | 仍成立 |
| CORS `allow_origins=["*"]` + `allow_credentials=True` | `backend/app/main.py` 仍如此配置 | 仍成立 |
| 依赖版本未锁定 | `backend/requirements.txt` 仍未锁定版本，未见 lock 文件 | 仍成立 |
| 进程守护缺失 | 未见 systemd / PM2 / Docker supervisor 配置 | 仍成立 |
| 自动备份缺失 | 未见 backup script / retention policy | 仍成立 |
| resolve 写入顺序 | 已修复并有测试覆盖 | 已完成 |
| 健康检查端点缺失 | 已有 `/api/events/loop/status`，但没有标准 `/api/health` | 部分完成 |

## 仍成立的高优先级问题

- 无 API 版本前缀 `/api/v1`。
- 多数 route 未声明 `response_model`。
- 关键破坏性端点仍无认证/授权。
- 无 rate limiting。
- 前端测试仍未建立。
- `gnews_service.py` / `openai_service.py` / `rss_service.py` 外部 API 层测试仍需补。
- 日志仍主要依赖 stdout，未见 RotatingFileHandler 或集中日志配置。
- 进程内 scheduler 仍和 API 生命周期耦合；`loop_runs` 只提升可观测性，不等于 supervisor / leader election。

## 原报告需要修正的口径

- 报告同时写“5 个 P0 阻塞项”和行动清单中 7 个 P0，应统一口径。
- “无健康检查端点”应改为“已有 loop status endpoint，但缺少标准 health endpoint、告警和 dead-man switch”。
- “测试 338”应更新为当前 `503`。
- “resolve 写入顺序待修复”应移动到已完成项。
- “可观测性为零”应改为“已有最小可观测性，但仍缺少生产级告警、持久日志和外部监控”。

## 当前生产判断

当前工作区比原报告快照更接近可运行闭环：已经具备最小 run ledger、loop status、orphan repair、contract-first link seeding 和 trust floor。

但生产结论仍是：

**NO-GO for unattended 90 days**

主要剩余阻塞：

1. 无进程守护 / 外部 scheduler owner。
2. 无自动备份与恢复演练。
3. 无 CI workflow。
4. CORS 与认证/限流仍不满足公网部署要求。
5. 无标准健康检查、告警和 dead-man switch。
