# 待修复问题（已对当前代码核实）· Open Issues — Verified

**生成日期：** 2026-06-21（**2026-06-22 修订：P1 前端/闭环 + scheduler 多进程守卫 + P2/P3 批次后同步**）
**核实方式：** 对 `docs/reviews/` 全部审查文档去重后，逐条读取当前代码 `file:line` 核实，仅保留确认"仍存在"的项。
**分支：** `fix/v0.3.0-hardening`

> 本文档只列**核实后仍开放**的问题。已修复项见 [consolidated-issue-registry-2026-06-21.md](consolidated-issue-registry-2026-06-21.md) 第一部分。
> 每条含：核实证据（`file:line`）、影响、建议修复。状态分 OPEN（确认缺陷）/ PARTIAL（部分到位）/ BY-DESIGN（设计取舍，列出供决策）。

> ✅ **2026-06-22 修订：原 3 个 P0（fail-open 鉴权 / 迁移非原子 / Docker healthcheck）仍关闭；P1-3/P1-5/P1-6/P1-7/P1-8/P1-10/P1-11/P1-12/P1-13/P1-14/P1-15/P1-16/P1-17/P1-18/P1-19/P1-20/P1-21/P1-22/P1-23，P2-2/P2-5/P2-6/P2-7/P2-8/P2-9/P2-11/P2-12/P2-13/P2-14/P2-15/P2-16/P2-17/P2-18，以及一批 P3 后端/前端整洁项已由 2026-06-21/22 批次落地并从本清单移除。最新后端验证 592 tests / 1 skipped，前端验证 20 tests + lint/build。**

---

## 🟠 P1 — 上线前应修

### 安全 / 鉴权

> ✅ **已修（移出本清单）：** ~~P1-1 未鉴权成本放大~~（P0-1 启动守卫已封堵主入口）、~~P1-2 key 非常量时间比较~~（`security.py` hmac.compare_digest）、~~P1-3 匿名泄露 job 原始 error~~（run details 需写 key）、~~P1-4 输入无长度上限~~（`event.py` Field 约束）、~~P1-5 缺安全响应头~~、~~P1-6 限流器内存增长 + 代理盲~~、~~P1-7 CORS 通配方法/头~~（显式 methods/headers + 启动校验）。

### 调度 / 运维 / 部署

> ✅ **已修（移出本清单）：** ~~P1-8 多 worker 双跑 / 生产 reload~~（`run.py` 默认 `SERVER_RELOAD=false`，`start.bat dev` 显式开启 reload；scheduler 受 `SCHEDULER_ENABLED` 门控并用本机进程锁避免同机多 worker 重复启动）、~~P1-9 health 降级返 200~~（degraded 返 503）、~~P1-10 无启动期 LLM key 校验~~（`LLM_STARTUP_CHECK_ENABLED=true` 时 fail-fast 探测 primary LLM）、~~P1-11 Docker 安全~~（非 root + `.dockerignore`）、~~P1-12 外部监控 / dead-man switch~~（systemd healthcheck 脚本先验本地 health，再 ping `PMRF_DEADMAN_URL`）。

### 后端数据闭环

> ✅ **已修（移出本清单）：** ~~P1-13 一条坏记录 abort 整批~~（批量保存 per-record 隔离）、~~P1-14 瞬时 LLM 故障毒化首见事件~~（fallback 只 audit 不 freeze）、~~P1-15 event_id 48-bit 文本耦合 / 旧数据未迁移~~（新 ID 为 16 hex；`scripts/migrate_event_ids.py` dry-run/apply 迁移 JSON store、audit、predictions、links；本地已迁移 78 个旧 ID）、~~P1-16 Kalshi 结算侧近零产出~~（resolved 侧 over-fetch + 0 resolved 告警）。

### 后端服务 / 性能（P1）

> ✅ **已修（移出本清单）：** ~~P1-17 /decisions/open N+1~~（一次加载 event store 建索引）、~~P1-18 histories_by_event 全量扫描~~（audit 文件签名缓存 + 单事件历史复用缓存）、~~P1-19 端点零 response_model~~（events router 全端点声明 response_model）、~~P1-20 静默失败适配器~~（RSS/SEC/Polymarket 异常 warning 可观测）。

### 前端（P1）

> ✅ **已修（移出本清单）：** ~~P1-21 不可逆结算无二次确认~~（manual resolve 与批量 auto-resolve 均改为预览/二次确认）、~~P1-22 recharts 未 lazy~~（图表渲染拆到动态 Recharts 子组件并提供 skeleton）、~~P1-23 路由级 loading 仅根级~~（analyze/decisions/edges/events/history 均补 `loading.tsx`）。

---

## 🟡 P2 — 上线后处理

### 后端
- **P2-1 event_store.json 每次 save 全量重写** — `event_store.py:59-87` 整文件 load+rewrite，无归档/TTL。规模债，当前量级未触发。
- **P2-3 跨存储无硬外键 join（PARTIAL）** — event_id 仍跨 JSON event store + SQLite loop store，无法由 SQLite FK 强制约束；已补 SQLite schema version 表与 loop status dangling prediction/link 监控。
- **P2-4 resolve_event 无条件覆盖 outcome** — `event_store.py:121-124` 非幂等无版本史（BY-DESIGN：承诺模型，但重结算分歧需注意）。
- **P2-10 SEC_USER_AGENT 真实联系人仍需运营配置** — 校准评级阈值已常量化；`config.py` 默认 User-Agent 仍只能作为声明占位，生产应在 `.env` 配置真实联系人（人工/运营项，本批按用户要求不处理）。

> ✅ **已修（移出本清单）：** ~~P2-2 SQLite 无 wal_checkpoint / integrity_check~~（启动维护 + daily `loop_db_maintenance`：`wal_checkpoint(TRUNCATE)` + `PRAGMA integrity_check`）、~~P2-5 graceful shutdown wait=False~~（`scheduler.shutdown(wait=True)`）、~~P2-6 misfire 注释与实际不符~~、~~P2-7 CI 无安全扫描~~（backend `pip-audit` + frontend npm test/lint/build/audit job）、~~P2-8 `{event_id}` 路径参数无校验~~（FastAPI `Path` pattern/length）、~~P2-9 异常处理模式不统一~~（`failure_policy.py` 统一 fail-closed empty-list / None / fallback 日志语义，主要外部源与可选 LLM 辅助路径已接入）、~~P2-11 调度器与 API 同生死 / 无 supervisor~~（新增独立 scheduler worker + systemd unit；API unit 默认关闭 in-process scheduler，进程锁兜底单 owner）。

### 前端

> ✅ **已修（移出本清单）：** ~~P2-12 NaN 渲染~~（非 finite 数字显示 `—`）、~~P2-13 recent-predictions 渲染崩险~~（改用 `fmtPct`/finite 守卫）、~~P2-14 CSV 公式注入 + 无 BOM~~（BOM + 公式前缀中和）、~~P2-15 sparkSeries 丢 0%~~（保留真实 0，丢弃缺失/非法值）、~~P2-16 主题 FOUC~~（head 内联脚本首屏设置主题 class）、~~P2-17 缺 global-error.tsx~~（补根级错误页）、~~P2-18 状态/交互细节~~（tracking 状态重挂载、系统状态首帧 loading、移动端 nav/table 改善）。

## 🔵 P3 — 长尾（体验 / 无障碍 / 整洁）

- 后端：类型注解不全；中英混合注释；懒导入隐藏依赖。
- 前端（剩余长尾）：图标按钮 aria-label 需继续逐页抽查；按钮/inputCls 样式重复 8+ 处；少量标题 tooltip 需继续补齐；等。

> ✅ **本批已修 P3：** 后端删除 `utcutc_now` 死代码并补 `test_config.py`、`.coveragerc`、完整 analyze→resolve HTTP E2E；前端 recent-predictions 显示事件标题、Link 卡片 focus-visible、AppNav `aria-current` + skip-link、Sparkline memo、`fmtDateTime` 复用 `Intl.DateTimeFormat`、证据时间改绝对时间、外链 `rel="noopener noreferrer"`、recent prediction 截断标题 tooltip、GET cache 过期清理、过滤控件可见 label、删除 `EvidenceList` 死导出。

## ⚪ 设计取舍（BY-DESIGN，非缺陷 — 列出供决策）

| 项 | 现状 | 取舍 |
|---|---|---|
| AUTO_VERIFY_THRESHOLD=1.0 | `config.py:199` 仅精确匹配自动核验 | fail-closed，宁缺勿错；模糊匹配入人工 pending 队列 |
| resolve_event 覆盖 outcome | 无版本史 | 承诺模型语义 |
| 无 /v1/ API 版本前缀 | 仅 /api/events | 当前规模可接受 |
| 一事件一预测首见冻结永久 | `ON CONFLICT DO NOTHING` | commitment-not-trajectory；后续大幅 edge 变化不重冻结 |

---

## 建议处置顺序

> ✅ **P0 全部完成（2026-06-21）；多项 P1 已在 2026-06-21/22 批次完成。** 以下为仍开放的上线前/上线后可选迭代。

1. **安全/运维人工项：** 轮换真实 DashScope key，清理含 `.env` 的备份包。
2. **其余 P2/P3：** 重点是 P2-1/P2-3 硬 FK 这类结构性项，以及剩余前端长尾一致性。
