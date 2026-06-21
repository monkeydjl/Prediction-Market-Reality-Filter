# 上线前问题汇总册 · Consolidated Issue Registry

**生成日期：** 2026-06-21
**来源：** `docs/reviews/` 下全部 39 份审查文档（pre-launch / deliverables / multi-ai-audit / milestones）抽取去重 + 对当前代码逐条核实
**分支：** `fix/v0.3.0-hardening`

## 说明

- 原始审查文档共提出约 **348 条**问题；跨文档去重后约 **150 条**唯一问题（多个 AI 审查同一套代码，大量重复）。
- 每条问题标注两种状态：
  - **文档自述状态**（DOC）：审查文档/修复报告里声称的状态。
  - **核实状态**（VERIFIED）：本次对当前代码 `file:line` 逐条核实的结果。
- 关键发现：**go-nogo 文档与 reaudit 文档对多项"已修复"声称互相矛盾**（CI/CD、Dockerfile、备份定时器、启动密钥校验、`_clamp01` 去重、Semaphore 配置化）。因此一律以 VERIFIED 为准，不信任文档声称。
- 仅含开放项的精简清单见 **[open-issues-verified-2026-06-21.md](open-issues-verified-2026-06-21.md)**。

## 总览

| 维度 | 数量 |
|---|---|
| 核实已修复（FIXED） | ~74（含 2026-06-21 P0 修复批次的 P0-1/P0-2/P0-3 + P1-2/P1-4/P1-9） |
| 核实仍开放（OPEN / PARTIAL） | ~50 |
| 设计取舍（OPEN by-design，非缺陷） | 6 |
| 已过时 / 误报（OBSOLETE / FALSE-POSITIVE） | 3 |

**上线裁决（2026-06-21 修订）：3 个上线阻断 P0（fail-open 鉴权、迁移非原子、Docker healthcheck）已全部修复并经代码核实 + 518 passed / 11 skipped 测试确认（见 [p0-fix-report-2026-06-21.md](p0-fix-report-2026-06-21.md)）。剩余为 P1 安全/运维/数据闭环项，可在上线后持续迭代。无 P0 阻断。**

> **2026-06-21 修订注：** 本文档早期版本把 P0-1/P0-2/P0-3 列为 OPEN；这些已由 p0-fix-report-2026-06-21 修复批次落地，本次同步将之移入第一部分（FIXED），并修正"P0-2 多 worker 双跑"的优先级标错——它是 P1-8，非 P0。

---

## 第一部分：核实已修复（FIXED）

这些是文档声称修复、且本次核实在当前代码中确认到位的项。

### 数据闭环（V2 reality loop）— P0/P1 修复均已核实

| 问题 | 核实证据 |
|---|---|
| 结算写序导致孤儿预测（score 在 resolve 之前） | `event_resolve_service.py:149-154` score/void 先于 `resolve_event` |
| 缺孤儿修复任务 | `reconcile_predictions()` 存在，`auto_resolve_events:219` 起始即调用 |
| 结算靠问题文本匹配而非 contract_id | `event_resolve_service.py:303-333` contract-first 为 PRIMARY 路径，文本仅 fallback |
| 冻结时不写 verified link → contract-first 不可达 | `prediction_store.py:269-278` `if inserted: upsert_link(verified=True)` |
| trust=0 吸收态永久锁死分类 | `config.py:229-231` `DIAGNOSIS_TRUST_FLOOR=0.1`，`diagnosis_service.py:47` 应用 |
| 再扫描覆盖已结算 outcome/calibration | `event_store.py:74-77` presence-check 保留 outcome/calibration |
| `_persist_events` 单一 try 吞掉 save/audit/freeze 边界 | `event_intelligence_service.py:428-448` 三个独立 try 边界（save 为 gate） |
| invalid/void 预测留 open 持续出现在机会面 | `prediction_store.py:324` `void_prediction` 置 voided；`event_resolve_service.py:152` 调用 |
| 多行 ledger 实验遗留（superseded/get_predictions） | 已回退；`prediction_store.py:39` `UNIQUE`，`:243` `ON CONFLICT DO NOTHING`，承诺模型完整 |
| SQL 注入风险 | 全部数据查询用 `?` 占位符；f-string 仅拼内部常量，无注入 |

### 后端服务 / 配置 — 已核实

| 问题 | 核实证据 |
|---|---|
| God Object（event_intelligence_service） | commit 09787b6 真实拆分：EIS 580 行，10 个评分函数迁至 `scoring_service.py:16-130` |
| 校准反馈静默吞异常 | `calibration_feedback_service.py:228-233` 改为 `logger.warning(exc_info=True)` |
| `_clamp01` 多处重复签名不一 | 统一为 `utils/helpers.py:9 clamp01`，无 `_clamp01` |
| `_now`/`utcutc_now` 重复 | 统一为 `utils/helpers.py:5 utc_now` |
| LLM 并发硬编码 Semaphore(4) | `config.py:255 LLM_CONCURRENCY`，调用点 `event_intelligence_service.py:335` 读配置 |
| 旧 openai_service 无 retry | `openai_service.py:19`/`probability_engine_service.py:81`/`cross_validation_service.py:83` 均 `max_retries=2` |
| cross_validation 死代码 | 仍在但 `:53` `CROSS_VALIDATION_MODEL` 未设即 early-out，默认关闭 |
| API key 前缀泄露日志 | `main.py:26,28` 仅记 `len=%d` |
| 日志只到 stdout | `logging.py:25` `RotatingFileHandler` 写 `logs/app.log` |
| 备份无轮转 | `backup_stores.py:23 _prune_backups` + `--keep`（默认 30） |

### 文档 / 测试 — 已核实

| 问题 | 核实证据 |
|---|---|
| 无 CHANGELOG | `CHANGELOG.md` 存在（v0.1.0→v0.3.0），commit 09787b6 |
| 无文档索引 | `docs/README.md` 存在 |
| 无架构文档 / ADR | `docs/dev/ARCHITECTURE.md` + `docs/dev/adr/001..003` 存在 |
| 文档"141 tests"陈旧 | `Event Intelligence Platform.md:392` 已更新为 503 tests |
| 代码评审混在 docs/user/ | 已迁出，docs/user/ 仅余用户文档 |

### 前端 — 已核实（fix-verification 批次属实）

| 问题 | 核实证据 |
|---|---|
| operator key 存储/传输 | sessionStorage（非 localStorage），`X-API-Key` 头，`type=password` |
| API 错误处理（白屏/原始 JSON） | `api.ts:60-80 buildApiErrorMessage` 映射 4xx/5xx + 422 detail；TypeError→网络/AbortError→超时；无裸 `res.text()` |
| API 无超时 | `api.ts:105-118` AbortController，默认 60s，discover 300s，analyze/resolveAuto 180s |
| evidence 类型过窄 | `types.ts:77-84 EvidenceAggregate`，signal-panel 无 `as` 断言 |
| history 缺 edge 字段 | `api.ts:338-341` 返回类型含 `edge?: EdgeTrajectory` |
| analyze 缺 volume/liquidity | `analyze/page.tsx:43-64` 校验并发送 |
| 错误/加载/404 边界 | `error.tsx`/`loading.tsx`/`not-found.tsx` + `SectionErrorBoundary` 分段容错均在 |
| 列表无分页 | 现已有 offset load-more（`page.tsx:71-85` PAGE_SIZE=50；history 同） |
| /dashboard 死链 | 不存在；nav 仅 `/ /decisions /analyze /history` |
| manual-resolve 测试失败 | **本会话期一度 2 失败，现 16/16 通过**；`setup.ts:5-7` 含 `afterEach(cleanup)`，根因（DOM 泄漏）已消除 |

### 2026-06-21 P0 修复批次 — 已核实（详见 [p0-fix-report-2026-06-21.md](p0-fix-report-2026-06-21.md)）

> 后端全套 **518 passed / 11 skipped**（较修复前 +7），无新增 `v2_loop.db` 泄漏。前端未触碰。

| 问题（原编号） | 核实证据 |
|---|---|
| **P0-1** 写接口 fail-open（空 key 放行） | `main.py:27-42` 启动守卫三分支：有 key 正常 / 空 key+opt-in 警告 / 空 key 无 opt-in `raise RuntimeError`；`config.py` 新增 `ALLOW_OPEN_WRITES`（默认 false） |
| **P0-2** predictions 迁移非原子 | `prediction_store.py:144` `_SCHEMA_STATEMENTS` 逐条 `conn.execute()`；`:142-143` rebuild 前 DROP INDEX；`:150-160` 关联子查询替代 ROW_NUMBER（去版本依赖） |
| **P0-3** Docker healthcheck 用 curl（slim 镜像无） | `docker-compose.yml:20` 改 `python urllib`；`main.py:120-122` `/api/health` degraded 返 503 |
| **P1-2** key 非常量时间比较（顺带修） | `security.py:17` `hmac.compare_digest` |
| **P1-4** event_question/news_context 无 max_length（顺带修） | `event.py:9-11` `Field(min_length=1, max_length=2000)` / `Field(max_length=20000)` / volume/liquidity 加 `ge=0` |
| **P1-9** health 降级返 200（顺带修） | `main.py:116-122` 有失败 job 或 scheduler 未运行则 503 |

**设计取舍注：** P0-1 强制点放在启动守卫（部署期单点）而非请求路径，避免破坏 bare-app 路由测试（不走 lifespan → 守卫不跑 → 请求路径保持简单）。运行期改空 key 需重启才生效，故"运行中被改空"不在守卫覆盖范围但实际不可达。

---

## 第二部分：核实仍开放（OPEN / PARTIAL）

按子系统列出。完整含证据与建议修复见 **[open-issues-verified-2026-06-21.md](open-issues-verified-2026-06-21.md)**；此处为索引。

### 安全 / 鉴权
- **[P1]** 未鉴权成本放大（discover/analyze 仅 require_write_key 守护；P0-1 启动守卫已封堵主入口，强制 key 后不再匿名可调）
- **[P1]** health/loop-status 向匿名泄露 job 原始 error 串（`loop_run_store.py:138 dict(row)`）
- **[P1]** CORS allow_methods/headers=["*"]，无 origins=* + credentials 守卫（PARTIAL：origins 默认安全）
- **[P1]** 缺安全响应头（X-Content-Type-Options / X-Frame-Options / CSP / HSTS）
- **[P1]** 限流器 `_hits` 按含路径参数的 key 永不清理（内存增长）+ 代理盲（`rate_limit.py:24-25`）

> **2026-06-21 已修（移入第一部分）：** ~~P0-1 fail-open~~（启动守卫）、~~P0-2 迁移非原子~~（discrete execute）、~~P1-2 key 非常量时间比较~~（hmac.compare_digest）、~~P1-4 无 max_length~~（Field 约束）。

### 调度 / 运维 / 部署
- **[P1]** 多 worker 双跑（`main.py:43` 无条件 start_scheduler；`run.py:8 reload=True`）
- **[P1]** 无启动期 LLM key 有效性校验（仅记长度）
- **[P1]** Docker 以 root 运行；无 `.dockerignore`（.env 可能进镜像层）
- **[P1]** 缺外部监控 / dead-man switch（无人 ping /api/health）
- **[P2]** 调度器无持久 jobstore、与 API 进程同生死，无 supervisor（systemd 文件存在但需部署）
- **[P2]** graceful shutdown `shutdown(wait=False)` 可中断结算中
- **[P2]** misfire 注释（300s）与实际（86400s）不符（`scheduler.py:25-26`）
- **[P2]** CI 无 pip-audit/npm audit，无前端 job

> **2026-06-21 已修（移入第一部分）：** ~~P0-3 Docker healthcheck~~（python urllib）、~~P1-9 health 降级返 200~~（503）。注：早期版本曾把"多 worker 双跑"误标为 P0，实际为 P1-8（与 open-issues 一致）。

### 后端数据闭环
- **[P1]** 一条坏记录 abort 整批 discover 保存（`event_store.py:78`）
- **[P1]** 瞬时 LLM 故障当天首见事件冻结降级预测且永不替换
- **[P1]** event_id = SHA1(question)[:12]（48-bit，文本耦合，`event_intelligence_service.py:573`）
- **[P1]** Kalshi resolved-side 近零产出，Kalshi 事件永不结算
- **[P2]** resolve_event 无条件覆盖 outcome（非幂等，无版本史）（by-design）
- **[P2]** AUTO_VERIFY_THRESHOLD=1.0 仅精确匹配自动核验（by-design，fail-closed）

### 后端存储 / 性能（多为规模债，当前量级未触发）
- **[P1]** /decisions/open N+1：每预测一次 `get_event` 全量读 JSON（`events.py:303-306`）
- **[P1]** `histories_by_event()` 每次全量读 audit 文件（`event_audit_service.py:198-210`）
- **[P2]** event_store.json 每次 save 全量重写（无归档/TTL）
- **[P2]** SQLite 无 wal_checkpoint 管理；无 integrity_check
- **[P2]** 跨存储无外键/引用完整性；无 schema version 表
- **[P3]** `utcutc_now` 死代码（`prediction_store.py:160-161`）

### 后端服务（可观测性 / API 契约）
- **[P1]** 端点零 `response_model=`（OpenAPI 响应 schema 空，前端无类型安全）
- **[P1]** rss/sec_edgar 静默 `except: return []` 无日志；polymarket_history 内层 per-market 静默 continue（PARTIAL：外层已加日志）
- **[P2]** {event_id} 路径参数无格式校验
- **[P2]** 无 API 版本前缀 /v1/（by-design）
- **[P2]** 异常处理模式不统一（[] / fallback / None 三种）
- **[P2]** 校准评级阈值魔法数；SEC User-Agent 占位邮箱
- **[P3]** 类型注解不全；中英混合注释；懒导入隐藏依赖

### 前端（多为 P2/P3 体验/无障碍/性能，未逐条复核，按文档计 OPEN）
- **[P1]** 不可逆结算无二次确认（manual-resolve + 批量 auto-resolve 单击即提交）
- **[P1]** recharts 静态导入未 lazy（每页 ~200KB）
- **[P1]** 路由级 loading.tsx 仅根级，子路由缺
- **[P2]** fmtPct/fmtEdge 用 `Number(n ?? 0)` 漏真 NaN → 渲染 "NaN%"（`format.ts:76,80`）
- **[P2]** recent-predictions 对 permissive 字段直接 `.toFixed()`，缺失即渲染崩（`:59,62`）
- **[P2]** csv.ts 未中和 `= + - @`（公式注入）+ 无 UTF-8 BOM（Excel 乱码）
- **[P2]** sparkSeries `v > 0` 过滤丢真 0%（`adapt.ts:118-122`）
- **[P2]** 主题 FOUC（useEffect 应用主题，无阻塞内联脚本）
- **[P2]** 缺 global-error.tsx（根布局抛错则白屏）
- **[P2]** TrackingDecision props 变化不同步；SystemStatus 首帧闪烁；移动端 nav 不折叠；表格移动端丢列头
- **[P3]** recent-predictions 显示 event_id 而非标题；图标按钮缺 aria-label；焦点环缺失；fmtDateTime 每次 new Intl；按钮/inputCls 样式重复；等约 25 项体验/无障碍长尾

### 设计取舍（非缺陷，记录在案）
- AUTO_VERIFY_THRESHOLD=1.0（fail-closed，宁缺勿错）
- resolve_event 覆盖 outcome（承诺模型语义）
- 无 /v1/ API 版本（当前规模可接受）
- 一事件一预测首见即冻结永久（commitment-not-trajectory 语义）

### 已过时 / 误报
- manual-resolve 测试"2 失败"：现 16/16 通过（OBSOLETE）
- /dashboard 死链：不存在（FALSE-POSITIVE）
- legacy 交易层、多行 ledger 相关里程碑问题：对应代码已删除/回退（OBSOLETE）

