# 待修复问题（已对当前代码核实）· Open Issues — Verified

**生成日期：** 2026-06-21（**2026-06-21 修订：P0 全部修复后同步**）
**核实方式：** 对 `docs/reviews/` 全部审查文档去重后，逐条读取当前代码 `file:line` 核实，仅保留确认"仍存在"的项。
**分支：** `fix/v0.3.0-hardening`

> 本文档只列**核实后仍开放**的问题。已修复项见 [consolidated-issue-registry-2026-06-21.md](consolidated-issue-registry-2026-06-21.md) 第一部分。
> 每条含：核实证据（`file:line`）、影响、建议修复。状态分 OPEN（确认缺陷）/ PARTIAL（部分到位）/ BY-DESIGN（设计取舍，列出供决策）。

> ✅ **2026-06-21 修订：原 3 个 P0（fail-open 鉴权 / 迁移非原子 / Docker healthcheck）已由 [p0-fix-report-2026-06-21.md](p0-fix-report-2026-06-21.md) 修复批次落地并核实，连同顺带修掉的 P1-2（常量时间比较）、P1-4（输入长度上限）、P1-9（health 503）一并从本清单移除。后端测试 518 passed / 11 skipped。本清单现在**无 P0 阻断**，最高为 P1。**

---

## 🟠 P1 — 上线前应修

### 安全 / 鉴权
- **P1-3 匿名泄露 job 原始 error** — `main.py:110`→`loop_status_service.py`→`loop_run_store.py:103,138` `SELECT *` + `dict(row)` 返回原始 `error` 串；`/api/health` 与 `/events/loop/status` 均未鉴权。修复：未鉴权端点只返状态/失败 job 名，error 详情门控写 key。
- **P1-5 缺安全响应头** — `main.py:61-67` 仅 CORS+限流。修复：加 X-Content-Type-Options / X-Frame-Options / CSP / HSTS 中间件。
- **P1-6 限流器内存增长 + 代理盲** — `rate_limit.py:25` key 含路径参数永不清理；`:24` `request.client.host` 反代后塌缩成一个桶。修复：按路由模板计 key + GC/LRU + 信任 X-Forwarded-For。
- **P1-7 CORS 通配方法/头（PARTIAL）** — `main.py:64-66` `allow_methods/headers=["*"]`；origins 默认安全但无 origins=* + credentials 守卫。修复：启动断言拒绝该组合。

> ✅ **2026-06-21 已修（移出本清单）：** ~~P1-1 未鉴权成本放大~~（P0-1 启动守卫已封堵主入口——强制 key 后 discover/analyze 不再匿名可调）、~~P1-2 key 非常量时间比较~~（`security.py:17` hmac.compare_digest）、~~P1-4 输入无长度上限~~（`event.py:9-11` Field 约束）。

### 调度 / 运维 / 部署
- **P1-8 多 worker 双跑** — `main.py:43` 无条件 `start_scheduler()`；`run.py:8 reload=True`。修复：单实例守卫/leader-election，生产关 reload。
- **P1-10 无启动期 LLM key 校验** — `main.py:23-26` 仅记长度。修复：启动发一次测试请求，失败则退出。
- **P1-11 Docker 安全（PARTIAL）** — 无 `USER`（以 root 跑）；无 `.dockerignore`（`.env` 可能进镜像层）。修复：加非 root appuser + .dockerignore 排除 .env/缓存/stores。
- **P1-12 无外部监控 / dead-man switch** — `/api/health` 存在但无人 ping。修复：接 UptimeRobot/cronitor 等外部 ping。

> ✅ **2026-06-21 已修（移出本清单）：** ~~P1-9 health 降级返 200~~（`main.py:116-122` degraded 返 503，配合 P0-3 python healthcheck 才真正有意义）。

### 后端数据闭环
- **P1-13 一条坏记录 abort 整批** — `event_store.py:78` 循环内 `EventRecord.model_validate` 在单次 `write_json_atomic` 前；一条畸形 LLM 输出令其余 N-1 条（含 LLM 成本）全丢。修复：per-record 隔离校验。
- **P1-14 瞬时 LLM 故障毒化首见事件** — `ai_analysis_service.py` 回退确定性估计（≈市场价）；discover 首见即 `ON CONFLICT DO NOTHING` 冻结，永不替换。回退≈市场→skip→不进 scored 校准（聚合受保护），但 edge/decision 展示面终身错误。修复：来自回退时跳过 freeze。
- **P1-15 event_id 48-bit 文本耦合** — `event_intelligence_service.py:573` `sha1(question)[:12]`。措辞漂移分裂同一事件；~16.7M 事件生日碰撞。修复：16+ hex 或基于 contract 的身份。
- **P1-16 Kalshi 结算侧近零产出** — `kalshi_event_source.py:183-197` resolved 侧只保留 `result in {yes,no}` 单腿且不像 candidate 侧过量抓取。Kalshi 事件 contract-first 路径要求其 contract 出现在已结算集合中，但 Kalshi 永不返回 settled → 永不结算、永不进校准，样本偏向 Polymarket/Manifold。修复：确认 settled 状态标签、resolved 侧过量抓取、对"有 open link 却 0 resolved"告警。

### 后端服务 / 性能（P1）
- **P1-17 /decisions/open N+1** — `events.py:303-306` 每预测一次 `get_event` 全量读 JSON（50 预测 250-1000ms）。修复：一次性批量加载 events 建内存索引。
- **P1-18 histories_by_event 全量扫描** — `event_audit_service.py:198-210` 每次读整个 audit 文件；被 /movers /edges/fresh /calibration 调用。修复：60s 缓存或迁 SQLite 加 event_id 索引。
- **P1-19 端点零 response_model** — `events.py` 全部端点返裸 dict，OpenAPI 响应 schema 空，前端无类型安全。修复：声明 response_model。
- **P1-20 静默失败适配器（PARTIAL）** — `rss_service.py:51`/`sec_edgar_service.py:25` 裸 `except: return []` 无日志；`polymarket_history_service.py:88` 内层 per-market 静默 continue（外层已加日志）。修复：加 `logger.warning` + 失败计数，使死源可见。

### 前端（P1）
- **P1-21 不可逆结算无二次确认** — `manual-resolve-panel.tsx:108-115` 单击即 POST；`history/page.tsx` 批量 auto-resolve 同。误点即不可逆写校准闭环。修复：加确认步骤（回显所填值）。
- **P1-22 recharts 未 lazy** — `probability-chart.tsx:3`/`category-accuracy.tsx:3` 静态顶层 import，每页 ~200KB。修复：`next/dynamic` + skeleton。
- **P1-23 路由级 loading 仅根级** — 子路由（events/history/decisions/analyze）缺 loading.tsx，客户端导航无即时反馈。修复：各路由加 loading.tsx。

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
- **P2-12 NaN 渲染** — `format.ts:76,80` / `decision-card.tsx:31` `Number(n ?? 0)` 漏真 NaN → "NaN%"。修复：`Number.isFinite(v) ? … : "—"`。
- **P2-13 recent-predictions 渲染崩险** — `:59,62` 对 permissive 字段直接 `.toFixed()`，缺失即崩（try/catch 只包 fetch）。修复：`Number()`/`fmtPct` 守卫。
- **P2-14 CSV 公式注入 + 无 BOM** — `csv.ts:4-7` 未中和 `= + - @`；`:10` 无 UTF-8 BOM（Excel CJK 乱码）。修复：危险单元格前缀 `'` + 加 `﻿`。
- **P2-15 sparkSeries 丢 0%** — `adapt.ts:118-122` `.filter(v > 0)` 丢真 0% 点。修复：`Number.isFinite`。
- **P2-16 主题 FOUC** — `theme-control.tsx:17-26` useEffect+setTimeout 应用主题；light 用户每次先闪 dark。修复：head 内联阻塞脚本。
- **P2-17 缺 global-error.tsx** — 仅有 error.tsx（段级）；根布局抛错则白屏。
- **P2-18 状态/交互细节** — TrackingDecision props 变化不同步（`tracking-decision.tsx:21-22`）；SystemStatus 首帧闪烁（loading 初值 false）；移动端 nav 不折叠；移动端表格丢列头。

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

> ✅ **P0 全部完成（2026-06-21）。** 以下为上线前/上线后可选迭代。

1. **上线前建议修（P1 安全/运维，可选）：** P1-3 error 泄露门控、P1-5 安全响应头、P1-8 多 worker 守卫、P1-11 Docker 非 root + .dockerignore、P1-12 外部监控。改动小、收益清晰，建议上线前补齐。
2. **数据质量（P1 闭环子集，关系校准闭环可信度）：** P1-13 批量隔离、P1-14 回退不冻结、P1-16 Kalshi 结算。
3. **前端 P1：** P1-21 结算二次确认（关系闭环可信度，改动小）。
4. **其余 P2/P3：** 上线后迭代。


