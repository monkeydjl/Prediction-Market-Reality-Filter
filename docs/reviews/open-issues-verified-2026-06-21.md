# 待修复问题（已对当前代码核实）· Open Issues — Verified

**生成日期：** 2026-06-21（**2026-06-22 修订：P1 前端/闭环 + scheduler 多进程守卫批次后同步**）
**核实方式：** 对 `docs/reviews/` 全部审查文档去重后，逐条读取当前代码 `file:line` 核实，仅保留确认"仍存在"的项。
**分支：** `fix/v0.3.0-hardening`

> 本文档只列**核实后仍开放**的问题。已修复项见 [consolidated-issue-registry-2026-06-21.md](consolidated-issue-registry-2026-06-21.md) 第一部分。
> 每条含：核实证据（`file:line`）、影响、建议修复。状态分 OPEN（确认缺陷）/ PARTIAL（部分到位）/ BY-DESIGN（设计取舍，列出供决策）。

> ✅ **2026-06-22 修订：原 3 个 P0（fail-open 鉴权 / 迁移非原子 / Docker healthcheck）仍关闭；P1-3/P1-5/P1-6/P1-7/P1-8/P1-10/P1-11/P1-12/P1-13/P1-14/P1-15/P1-16/P1-17/P1-18/P1-19/P1-20/P1-21/P1-22/P1-23 与 P2-12/P2-13/P2-14/P2-15/P2-16/P2-17/P2-18 已由 2026-06-21/22 批次落地并从本清单移除。最新后端验证 567 tests / 1 skipped，前端验证 20 tests + lint/build。**

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
- **P2-2 SQLite 无 wal_checkpoint / integrity_check** — `sqlite_db.py:54` 仅设 WAL；长跑 WAL 可膨胀，无完整性校验。修复：启动 `wal_checkpoint(TRUNCATE)` + 周期 `PRAGMA integrity_check`。
- **P2-3 跨存储无引用完整性 / schema version** — event_id 是 JSON+SQLite 无外键 join；迁移靠结构探测无版本表。
- **P2-4 resolve_event 无条件覆盖 outcome** — `event_store.py:121-124` 非幂等无版本史（BY-DESIGN：承诺模型，但重结算分歧需注意）。
- **P2-5 graceful shutdown wait=False** — `scheduler.py:135` 可中断结算中。修复：`wait=True` + 超时。
- **P2-6 misfire 注释与实际不符** — `scheduler.py:25-26` 注释 300s，实际 `config.py:257` 86400s。修复：改注释。
- **P2-7 CI 无安全扫描** — `.github/workflows/ci.yml` 仅 compileall+unittest，无 pip-audit/npm audit，无前端 job。
- **P2-8 {event_id} 路径参数无校验** — `events.py` 多处裸 str，未导入 Path。
- **P2-9 异常处理模式不统一** — [] / fallback / None 三种，失败模式难推理。
- **P2-10 魔法数 / 占位邮箱** — 校准评级阈值内联；`config.py` SEC_USER_AGENT 占位邮箱（SEC 可能拒）。
- **P2-11 调度器无持久 jobstore / 与 API 同生死** — systemd 文件存在但需部署单实例 + 文档化。

### 前端

> ✅ **已修（移出本清单）：** ~~P2-12 NaN 渲染~~（非 finite 数字显示 `—`）、~~P2-13 recent-predictions 渲染崩险~~（改用 `fmtPct`/finite 守卫）、~~P2-14 CSV 公式注入 + 无 BOM~~（BOM + 公式前缀中和）、~~P2-15 sparkSeries 丢 0%~~（保留真实 0，丢弃缺失/非法值）、~~P2-16 主题 FOUC~~（head 内联脚本首屏设置主题 class）、~~P2-17 缺 global-error.tsx~~（补根级错误页）、~~P2-18 状态/交互细节~~（tracking 状态重挂载、系统状态首帧 loading、移动端 nav/table 改善）。

## 🔵 P3 — 长尾（体验 / 无障碍 / 整洁）

- 后端：类型注解不全；中英混合注释；懒导入隐藏依赖；`utcutc_now` 死代码（`prediction_store.py:160-161`）；无 test_config.py；无覆盖率配置（.coveragerc/pytest.ini）；无完整 analyze→resolve HTTP E2E（已有 TestClient 单点集成）。
- 前端（约 25 项，多源自 frontend-optimization 批，未逐条复核按文档计 OPEN）：recent-predictions 显示 event_id 而非标题；图标按钮缺 aria-label；Link 卡片缺 focus-visible 环；缺 aria-current/skip-link；summarize/Sparkline 未 memo；fmtDateTime 每次 new Intl；relativeTime 陈旧；按钮/inputCls 样式重复 8+ 处；外链 rel 不一致；截断标题无 tooltip；过滤下拉无可见标签；API cache 不清过期；EvidenceList 死代码导出；等。

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
2. **其余 P2/P3：** 上线后迭代。
