## Prediction Market Reality Filter — 上线前审查报告

**版本**: v0.3.0  
**审查日期**: 2026-06-20  
**测试**: 508 tests, 全部通过 (skipped=1)  
**分支**: fix/v0.3.0-hardening

---

### 一、上次审计 P0 问题修复状态

上次 CTO 审计（2026-06-19/20）识别出 5 个业务循环 P0 缺陷，当前代码已全部修复：

| # | 原始问题 | 修复措施 | 验证方式 |
|---|---------|---------|---------|
| 1 | 跨存储非原子写入导致僵尸预测 | `resolve_with_calibration()` 现在先写 SQLite（score_prediction），后写 JSON（resolve_event），崩溃后下次运行可重试 | `event_resolve_service.py` L131-157，逻辑正确 |
| 2 | 孤儿预测无法自愈 | 新增 `reconcile_predictions()` 函数，每次 auto-resolve 前扫描并修复孤儿 | `event_resolve_service.py` L160-203，幂等安全 |
| 3 | freeze 时未写入 verified link，导致 contract-first 结算永远不触发 | `freeze_prediction()` 现在在冻结时写入 verified link | `prediction_store.py` 已验证 |
| 4 | Trust 吸收态死锁（Brier>0.25 → trust=0 → 永久 skip） | 新增 `DIAGNOSIS_TRUST_FLOOR=0.1`，保持小概率采样通道 | `config.py` L229-231，`diagnosis_service.py` 已验证 |
| 5 | 无循环运行账本和可观测性 | `loop_runs` 表 + `/api/health` 端点 + systemd 备份/健康检查定时器 | 全链路已验证 |

**结论：核心业务循环（M0-M5）的数据路径已加固，可以支撑有监督的生产运行。**

---

### 二、基础设施就绪度

| 维度 | 状态 | 详情 |
|------|------|------|
| 进程监督 | ✅ 就绪 | systemd `Restart=always`, `RestartSec=10`；docker-compose `restart: unless-stopped` |
| 健康检查 | ✅ 就绪 | `/api/health` 端点返回 scheduler/failed_runs/loop 状态；systemd timer 每 5 分钟 curl 一次；docker-compose 30s 间隔 |
| 自动备份 | ✅ 就绪 | `scripts/backup_stores.py` 打包 event_store.json + event_audit.jsonl + event_cache.json + v2_loop.db（含 WAL/SHM）；systemd daily timer + `Persistent=true` 保证重启后补跑 |
| CI 管线 | ✅ 就绪 | GitHub Actions: push/PR → pip install → compileall → unittest discover |
| 容器化 | ✅ 就绪 | Dockerfile (python:3.11-slim) + docker-compose.yml |
| 速率限制 | ✅ 就绪 | 内存滑动窗口，120 req/60s per client，429 + Retry-After |
| CORS | ✅ 就绪 | 默认仅限 localhost:3000/8000，credentials=false |
| API 鉴权 | ✅ 就绪 | `API_WRITE_KEY` + `X-API-Key` header 校验，空值时降级为公开模式（开发用） |
| 日志 | ✅ 就绪 | RotatingFileHandler 10MB × 5 轮转，UTF-8 |

---

### 三、剩余风险清单

#### P1 — 上线前建议修复

**P1-1. `_load_resolved_records` 静默吞异常**

`calibration_feedback_service.py` L221-226：

```python
except Exception:
    return []
```

此函数是校准反馈的唯一数据源。如果 event_store.json 读取失败，校准反馈会静默退化为空，没有任何日志提示。这意味着系统可能在校准数据已损坏的情况下持续运行数周而无人察觉。

**建议**：至少加一行 `logger.warning` 或 `logger.exception`。

**P1-2. 备份无轮转策略**

`backup_stores.py` 每天生成一个 zip 文件到 `backups/` 目录，但没有清理旧备份的逻辑。长期运行后磁盘会逐渐填满。

**建议**：增加 `--keep N` 参数，保留最近 N 个备份；或在 systemd timer 后加清理逻辑。

**P1-3. 日志泄露 API Key 前 3 字符**

`main.py` L26:
```python
logger.info("OPENAI_API_KEY is configured (%.3s...)", settings.OPENAI_API_KEY[:3])
```

虽然只有 3 字符，在生产日志中仍属不必要信息暴露。DashScope key 前缀为 `sk-`，泄露前 3 字符等同于泄露了 key 格式确认。

**建议**：改为 `logger.info("OPENAI_API_KEY is configured (len=%d)", len(settings.OPENAI_API_KEY))`。

**P1-4. 备份时 JSON + SQLite 非一致性快照**

`backup_stores.py` 依次复制 `event_store.json` 和 `v2_loop.db`，两次读取之间没有全局锁。如果备份时刻恰好有 resolve 操作，可能拿到一个 JSON 已写入 outcome 但 SQLite prediction 尚未 scored 的中间状态。

**实际影响**：低。`reconcile_predictions()` 可以在恢复后自愈这种不一致。但恢复后应显式运行一次 reconcile。

**建议**：在备份脚本文档或恢复手册中注明"恢复后需运行 reconcile"。

#### P2 — 可接受的已知限制

**P2-1. 无应用级重试**

LLM API 调用依赖 OpenAI SDK 内置的 `max_retries=2`（仅覆盖 HTTP 429/5xx 瞬时故障）。对于超时、JSON 解析失败、provider 整体宕机等场景没有重试。当前行为是直接降级到确定性回退分析（max_move=22），功能上可以接受，但会损失分析质量。

**P2-2. 依赖版本范围而非锁定**

`requirements.txt` 使用 `>=x,<y` 范围约束（如 `fastapi>=0.115,<1.0`）。在大多数情况下这没问题，但如果某个依赖发布了有破坏性变更的小版本，可能导致环境不一致。

**建议**：生产部署时生成 `pip freeze > requirements.lock` 并使用锁定文件。

**P2-3. 前端无测试**

前端（Next.js + TypeScript）没有任何自动化测试。41 个测试文件全部是后端。Dashboard UI 的正确性完全依赖人工验证。

**P2-4. 无 API 版本前缀**

所有端点在 `/api/events/` 下，没有 `/v1/` 版本前缀。如果未来需要破坏性变更 API 契约，迁移成本会较高。当前用户量级下可接受。

**P2-5. SQLite 无完整性校验**

JSON 存储有 corrupt 文件隔离机制（`.corrupt` sidecar），但 SQLite 没有 `PRAGMA integrity_check` 或等效的定期健康检查。WAL 模式降低了写入损坏风险，但磁盘故障或异常关机仍可能导致静默损坏。

---

### 四、架构决策记录

以下问题不需要上线前修复，但应在后续版本中做出明确决策：

**预测语义：承诺模型 vs. 账本模型**  
`prediction_store.py` 中 `_materially_changed()` + `PREDICTION_RESNAPSHOT_DELTA` 允许同一个 event_id 多次写入预测（当概率变化足够大时）。这使得"一次冻结"的承诺模型实际上变成了"重大变更时重新快照"的账本模型。这个设计选择需要在 M5 反馈闭环中明确：如果预测可以被重新快照，Brier 评分应该针对哪个快照？

---

### 五、Go / No-Go 判定

| 场景 | 判定 | 理由 |
|------|------|------|
| 有监督日常运行（每日人工查看 /api/health） | **Go** | 核心循环加固，进程监督/备份/健康检查齐备，508 tests 全绿 |
| 7 天无人值守运行 | **Conditional Go** | 修复 P1-1（加日志）和 P1-2（备份轮转）后可 Go |
| 30 天无人值守运行 | **No-Go** | 需额外解决：SQLite 完整性校验、备份恢复演练、告警通知渠道（healthcheck timer 目前只 curl 不外发通知） |

---

### 六、快速修复清单（上线前最小改动）

以下 4 项改动总量约 20 行代码，可在一小时内完成：

1. `calibration_feedback_service.py` L225: `except Exception:` → `except Exception: logger.warning("Failed to load resolved records for calibration feedback", exc_info=True); return []`
2. `main.py` L26: 移除 API key 前 3 字符日志，改为只记录长度
3. `backup_stores.py`: 增加 `--keep` 参数，默认保留 30 天备份
4. `.env` 确认未被 git 追踪（当前状态：未被追踪，`.gitignore` 已正确配置）
