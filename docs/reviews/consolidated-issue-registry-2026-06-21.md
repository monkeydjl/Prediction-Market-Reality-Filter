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
| 核实已修复（FIXED） | ~101（含 2026-06-21 P0 修复批次 + 2026-06-22 P1/P2 前端/闭环/scheduler/CORS/LLM 启动校验/dead-man/event_id 迁移批次） |
| 核实仍开放（OPEN / PARTIAL） | ~23 |
| 设计取舍（OPEN by-design，非缺陷） | 6 |
| 已过时 / 误报（OBSOLETE / FALSE-POSITIVE） | 3 |

**上线裁决（2026-06-22 修订）：3 个上线阻断 P0（fail-open 鉴权、迁移非原子、Docker healthcheck）仍全部关闭；本轮继续关闭多项 P1/P2 前端/闭环/scheduler/CORS/LLM 启动校验/dead-man/event_id 迁移/Recharts lazy/路由 loading/后端性能与 API 契约问题。最新后端验证 567 tests / 1 skipped；前端验证 20 tests + lint/build。无 P0 阻断。**

> **2026-06-21 修订注：** 本文档早期版本把 P0-1/P0-2/P0-3 列为 OPEN；这些已由 p0-fix-report-2026-06-21 修复批次落地，本次同步将之移入第一部分（FIXED），并修正"P0-2 多 worker 双跑"的优先级标错——它是 P1-8，非 P0。

> **2026-06-22 修订注：** P1-3/P1-5/P1-6/P1-7/P1-8/P1-10/P1-11/P1-12/P1-13/P1-14/P1-15/P1-16/P1-17/P1-18/P1-19/P1-20/P1-21/P1-22/P1-23 与 P2-12/P2-13/P2-14/P2-15/P2-16/P2-17/P2-18 已由 2026-06-21/22 批次落地，本次同步从 OPEN 索引移入 FIXED。

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

### 2026-06-21/22 P1 前端/闭环/scheduler 批次 — 已核实

| 问题（原编号） | 核实证据 |
|---|---|
| **P1-3** 匿名泄露 job 原始 error | `/api/health` 与 `/events/loop/status` 默认脱敏；带写 key 才返回 run detail |
| **P1-5** 缺安全响应头 | `main.py` 全局添加 X-Content-Type-Options / X-Frame-Options / Referrer-Policy / HSTS / CSP |
| **P1-6** 限流器内存增长 + 代理盲 | `rate_limit.py` 支持 forwarded client IP、动态路径归一、过期桶清理和总桶上限 |
| **P1-7** CORS 通配方法/头 | `config.py` 默认显式 `CORS_ALLOWED_METHODS`/`CORS_ALLOWED_HEADERS`；`main.py` 启动校验拒绝 methods/headers wildcard 与 `origins=* + credentials=true` |
| **P1-8** 多 worker 双跑 / 生产 reload | `run.py` 默认 `SERVER_RELOAD=false`；`start.bat dev` 显式开启 reload；`main.py` 受 `SCHEDULER_ENABLED` 门控；`scheduler.py` 使用本机进程锁防止同机多 worker 重复启动 |
| **P1-10** 无启动期 LLM key 校验 | `LLM_STARTUP_CHECK_ENABLED=true` 时启动期发极小 chat 请求验证 primary LLM key/model/base URL，失败拒绝启动并脱敏 key |
| **P1-11** Docker 安全 | `deploy/Dockerfile` 切换非 root `app` 用户；`.dockerignore` 排除 env/cache/runtime stores |
| **P1-12** 无外部监控 / dead-man switch | `scripts/healthcheck.py` 先校验本地 `/api/health` 为 ok，再 ping 可选 `PMRF_DEADMAN_URL`；systemd healthcheck timer 已改为执行该脚本 |
| **P1-13** 一条坏记录 abort 整批 | `event_store.py` 批量保存改为 per-record 隔离 |
| **P1-14** 瞬时 LLM 故障毒化首见事件 | fallback 分析只 audit，不 freeze prediction |
| **P1-15** event_id 48-bit 文本耦合 / 旧数据未迁移 | 新生成 ID 已为 16 hex；`scripts/migrate_event_ids.py` 默认 dry-run、`--apply` 同步 JSON event_store、audit JSONL、predictions、event_market_links；本地已迁移 78 个旧 ID |
| **P1-16** Kalshi resolved-side 近零产出 | resolved 侧 over-fetch 到 200，并对本地有未结算 Kalshi 但远端 0 resolved 的场景告警 |
| **P1-21** 不可逆结算无二次确认 | manual resolve 与批量 auto-resolve 均增加预览/二次确认 |
| **P1-22** recharts 静态导入未 lazy | probability/edge/category/edges timeline 图表拆到 Next dynamic 子组件，父组件保留轻量 wrapper 与 skeleton |
| **P1-23** 子路由缺 `loading.tsx` | `analyze`/`decisions`/`edges`/`events`/`history` 均补路由级 loading UI |
| **P1-17** `/decisions/open` N+1 | `/events/decisions/open` 一次性 `list_all_events()` 建 event_id 索引，循环内不再逐条 `get_event` 全量读 JSON |
| **P1-18** `histories_by_event()` 每次全量扫描 | audit 历史按文件 `mtime_ns + size` 做进程内缓存；`history_for_event()` 复用缓存；返回副本避免调用方污染 |
| **P1-19** 端点零 `response_model=` | events router 全端点声明 response_model；新增 OpenAPI 契约测试防回归 |
| **P1-20** RSS/SEC/Polymarket 静默失败 | RSS/SEC 抓取异常与 Polymarket 单市场坏行均记录 warning，仍 fail-soft 返回可用结果 |
| **P2-12** NaN 渲染 | `fmtPct`/`fmtSignedPct`/decision edge formatter 对非 finite 数字返回 `—` |
| **P2-13** recent-predictions 缺字段崩险 | 最近预测列表不再直接 `.toFixed()` permissive 字段，改用 `fmtPct` 与 Brier finite guard |
| **P2-14** CSV 公式注入 + Excel 乱码 | `toCsv()` 加 UTF-8 BOM，并对 `= + - @` 开头单元格前缀 `'` |
| **P2-15** sparkSeries 丢 0% | `sparkSeries()` 直接按 `Number(estimated)` 过滤 finite 值，保留真实 0% |
| **P2-16** 主题 FOUC | root layout head 内联脚本在首屏绘制前同步设置 `light`/`dark` class；ThemeControl 只同步按钮状态 |
| **P2-17** 缺 global-error.tsx | 新增 `app/global-error.tsx`，根布局异常时显示可重试的全屏错误页 |
| **P2-18** 状态/交互细节 | TrackingDecision 随事件/状态 key 重挂载；SystemStatus 初始 loading；移动端 nav 换行横滑；Event/Review 表格移动端补列标签 |

---

## 第二部分：核实仍开放（OPEN / PARTIAL）

按子系统列出。完整含证据与建议修复见 **[open-issues-verified-2026-06-21.md](open-issues-verified-2026-06-21.md)**；此处为索引。

### 安全 / 鉴权

> **已修（移入第一部分）：** ~~P0-1 fail-open / 未鉴权成本放大主入口~~（启动守卫）、~~P0-2 迁移非原子~~（discrete execute）、~~P1-2 key 非常量时间比较~~（hmac.compare_digest）、~~P1-3 health/loop-status 匿名泄露~~、~~P1-4 无 max_length~~（Field 约束）、~~P1-5 缺安全响应头~~、~~P1-6 限流器内存增长 + 代理盲~~、~~P1-7 CORS allow_methods/headers wildcard~~。

### 调度 / 运维 / 部署
- **[P2]** 调度器无持久 jobstore、与 API 进程同生死，无 supervisor（systemd 文件存在但需部署）
- **[P2]** graceful shutdown `shutdown(wait=False)` 可中断结算中
- **[P2]** misfire 注释（300s）与实际（86400s）不符（`scheduler.py:25-26`）
- **[P2]** CI 无 pip-audit/npm audit，无前端 job

> **已修（移入第一部分）：** ~~P0-3 Docker healthcheck~~（python urllib）、~~P1-8 多 worker 双跑 / 生产 reload~~（本机进程锁 + reload 默认关）、~~P1-9 health 降级返 200~~（503）、~~P1-10 无启动期 LLM key 有效性校验~~、~~P1-11 Docker 以 root 运行 / 无 `.dockerignore`~~、~~P1-12 外部监控 / dead-man switch~~。

### 后端数据闭环
- **[P2]** resolve_event 无条件覆盖 outcome（非幂等，无版本史）（by-design）
- **[P2]** AUTO_VERIFY_THRESHOLD=1.0 仅精确匹配自动核验（by-design，fail-closed）

> **已修（移入第一部分）：** ~~P1-13 一条坏记录 abort 整批~~、~~P1-14 瞬时 LLM 故障冻结降级预测~~、~~P1-15 event_id 48-bit / 旧数据未迁移~~、~~P1-16 Kalshi resolved-side 近零产出~~。

### 后端存储 / 性能（多为规模债，当前量级未触发）
- **[P2]** event_store.json 每次 save 全量重写（无归档/TTL）
- **[P2]** SQLite 无 wal_checkpoint 管理；无 integrity_check
- **[P2]** 跨存储无外键/引用完整性；无 schema version 表
- **[P3]** `utcutc_now` 死代码（`prediction_store.py:160-161`）

### 后端服务（可观测性 / API 契约）
- **[P2]** {event_id} 路径参数无格式校验
- **[P2]** 无 API 版本前缀 /v1/（by-design）
- **[P2]** 异常处理模式不统一（[] / fallback / None 三种）
- **[P2]** 校准评级阈值魔法数；SEC User-Agent 占位邮箱
- **[P3]** 类型注解不全；中英混合注释；懒导入隐藏依赖

### 前端（多为 P2/P3 体验/无障碍/性能，未逐条复核，按文档计 OPEN）

> **已修（移入第一部分）：** ~~P1-21 不可逆结算无二次确认~~、~~P1-22 recharts 未 lazy~~、~~P1-23 子路由缺 loading~~、~~P2-12 NaN 渲染~~、~~P2-13 recent-predictions 缺字段崩险~~、~~P2-14 CSV 公式注入 + 无 BOM~~、~~P2-15 sparkSeries 丢 0%~~、~~P2-16 主题 FOUC~~、~~P2-17 缺 global-error.tsx~~、~~P2-18 状态/交互细节~~。
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
