# P0 修复报告 · P0 Fix Report

**日期：** 2026-06-21
**分支：** `fix/v0.3.0-hardening`
**范围：** 修复 [open-issues-verified-2026-06-21.md](open-issues-verified-2026-06-21.md) 中 3 个上线阻断项（P0），并顺带修掉同根因的 P1-9（health 降级返 200）与 P2（key 非常量时间比较）。
**验证：** 后端 `tests/` 全套 **518 passed / 11 skipped**（较修复前 +7，全部为本轮新增/改写测试）；无新增 `v2_loop.db` 泄漏。前端未触碰。

---

## 一、改动总览

| 文件 | 改动 | 对应问题 |
|---|---|---|
| `backend/app/api/security.py` | 常量时间比较 + 注释化 fail-open 语义 | P0-1, P1-2 |
| `backend/app/core/config.py` | 新增 `ALLOW_OPEN_WRITES` 配置项（默认 false） | P0-1 |
| `backend/app/main.py` | 启动守卫：空 key 且未 opt-in 则拒绝启动；health 降级返 503 | P0-1, P0-3/P1-9 |
| `backend/.env.example` | 文档化 `ALLOW_OPEN_WRITES` + 强制 key 说明 | P0-1 |
| `backend/app/memory/prediction_store.py` | 迁移原子化：discrete execute 替代 executescript；去掉 ROW_NUMBER 版本依赖；rebuild 前 DROP INDEX | P0-2 |
| `deploy/docker-compose.yml` | healthcheck 用 python urllib 替代 curl | P0-3 |
| `backend/tests/test_operational_readiness.py` | 新增启动守卫测试类 + health 503 测试 + 改写 auth 测试 | 验证 P0-1/P0-3 |
| `backend/tests/test_events_routes.py` | DashboardSmokeTests 补 key patch（适配启动守卫） | 测试适配 |

---

## 二、逐项详情

### P0-1 写接口 fail-open（已修复）

**问题：** `security.py` 在 `API_WRITE_KEY` 为空时直接放行；`.env` 默认空 key，忘记设置的部署 = 所有写接口（resolve / resolve/auto / discover / analyze / tracking / link verify）匿名可调，可注入虚假结算永久污染校准闭环。

**修复方案（启动守卫为强制点，请求路径保持简单）：**

1. **`config.py`** 新增 `ALLOW_OPEN_WRITES: bool`（默认 `false`）。
2. **`main.py` lifespan** 增加启动守卫：
   - 有 key → 正常启动（仅记 `len`）。
   - 空 key + `ALLOW_OPEN_WRITES=true` → 启动但打 warning（"write endpoints are PUBLIC. Never use this in production"）。
   - 空 key + 未 opt-in → `raise RuntimeError` 拒绝启动。
3. **`security.py`** 请求路径保持「空 key 即放行」的简单语义（因为启动守卫已保证：能跑起来的无 key 状态必然是显式 opt-in 的），并把 `!=` 改为 `hmac.compare_digest`（常量时间，顺带修 P1-2）。

**设计取舍：** 把强制点放在启动守卫而非请求路径。原因：请求路径若改成「无 key 即 503」会破坏所有 bare-app 路由测试（不走 lifespan → 守卫没跑 → 所有写路由 503）。启动守卫是部署期的单点拦截，语义更清晰，且不影响测试隔离。

**测试：**
- `StartupGuardTests.test_lifespan_refuses_keyless_boot_without_opt_in` — 空 key 无 opt-in → lifespan 抛 `RuntimeError`。
- `StartupGuardTests.test_lifespan_boots_keyless_with_opt_in` — 空 key + opt-in → 正常启动。
- `StartupGuardTests.test_lifespan_boots_with_key` — 有 key → 正常启动。
- `WriteAuthTests.test_write_key_wrong_header_rejected` — 错误 key → 401。
- `WriteAuthTests.test_keyless_with_opt_in_passes_through` — opt-in 下请求路径放行。

### P0-2 predictions 表迁移非原子（已修复）

**问题：** `prediction_store._migrate` 的 collapse 路径里 `RENAME → executescript(_SCHEMA) → INSERT...SELECT → DROP`。`executescript()` 先隐式 COMMIT，故 RENAME+CREATE 在数据拷贝前已提交；若 INSERT 失败（旧行 NOT NULL 违例 / 旧 SQLite 不支持 ROW_NUMBER），回滚无法撤销 → 空 `predictions` 表 + 孤立 `predictions_old`，数据永久搁浅。

**修复方案：**

1. 把 `_SCHEMA` 拆为 `_SCHEMA_STATEMENTS`（discrete 语句元组）；`_SCHEMA`（join 后）仍供首次建表的 `executescript` 用（幂等 `CREATE IF NOT EXISTS`，无害）。
2. collapse 路径改用 `for stmt in _SCHEMA_STATEMENTS: conn.execute(stmt)` —— 不触发隐式 COMMIT，整个 RENAME→CREATE→INSERT→DROP 跑在 `writing()` 打开的单事务里，失败整体回滚。
3. **去掉 ROW_NUMBER() 版本依赖**：改用关联子查询（correlated subquery）选每个 event 的存活行（open 优先 → created_at 最新 → rowid 最大），适配所有 SQLite 版本，比「加版本守卫」更彻底。
4. **顺带修一个潜在 bug**：`RENAME` 后索引名是全局的会跟着旧表，`CREATE INDEX IF NOT EXISTS` 会静默 no-op 导致新表无索引。rebuild 前显式 `DROP INDEX IF EXISTS idx_pred_status / idx_pred_category`，确保索引重建到新表。

**测试：** 既有 `test_multirow_collapses_to_one_and_readds_unique`（多行无 UNIQUE 表 → 折叠为一行 + 重建 UNIQUE）全程通过，覆盖此路径。

### P0-3 Docker healthcheck 必失败 + health 降级返 200（已修复）

**问题 A：** `docker-compose.yml` healthcheck 用 `curl`，但 `python:3.11-slim` 无 curl → 容器永远 unhealthy。
**问题 B（P1-9）：** `/api/health` 即使 `status:degraded` 也返 200，编排器/外部监控无法感知降级。

**修复方案：**

1. **`docker-compose.yml`** healthcheck 改为 `python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(...).status < 400 else 1)"` —— 用镜像必有的 python，且 ≥400 时退非零。
2. **`main.py` `/api/health`** 注入 `Response`，`degraded`（有失败 job **或** scheduler 未运行）时设 `status_code = 503`，否则 200。body 仍带完整明细供人读。两者配合：healthcheck 的「≥400 退非零」此时才真正有意义。

**测试：**
- `HealthTests.test_api_health_ok_returns_200_when_healthy`
- `HealthTests.test_api_health_returns_503_when_degraded`（有失败 job）
- `HealthTests.test_api_health_returns_503_when_scheduler_stopped`
- 用 `_FakeScheduler` 替身解决 `scheduler.running` 是只读 property 无法 patch 的问题。

---

## 三、未完成的工作

### 仍开放的高优先项（详见 [open-issues-verified-2026-06-21.md](open-issues-verified-2026-06-21.md)）

**P1 安全/运维（建议上线前一并处理）：**
- P1-3 health/loop-status 向匿名泄露 job 原始 error 串
- P1-5 缺安全响应头（X-Content-Type-Options / X-Frame-Options / CSP / HSTS）
- P1-6 限流器 `_hits` 按含路径参数 key 永不清理（内存增长）+ 代理盲
- P1-8 多 worker 双跑（`main.py:31` 无条件 start_scheduler；`run.py reload=True`）
- P1-10 无启动期 LLM key 有效性校验
- P1-11 Docker 以 root 运行；无 `.dockerignore`（.env 可能进镜像层）
- P1-12 缺外部监控 / dead-man switch

> **注：** P0-1 已为 P1-1（未鉴权成本放大）封堵了主入口——强制 key 后 discover/analyze 不再匿名可调。但 `event_question` 仍无 `max_length`（P1-4），建议补。

**P1 数据闭环：**
- P1-13 一条坏记录 abort 整批 discover 保存
- P1-14 瞬时 LLM 故障毒化首见事件（回退预测永不替换）
- P1-15 event_id 48-bit 文本耦合
- P1-16 Kalshi 结算侧近零产出，Kalshi 事件永不结算

**P1 前端：**
- P1-21 不可逆结算无二次确认
- P1-22 recharts 未 lazy；P1-23 路由级 loading 仅根级

**P2/P3：** 约 40 项规模债 / 体验 / 无障碍长尾，上线后迭代。

### 本轮修复的已知限制

1. **P0-1 启动守卫不拦截"运行中被改空 key"**：守卫只在启动时跑一次。运行期若有人把 key 改空（理论上需重启才生效，settings 在导入时固化），不在守卫覆盖范围。当前 `settings.API_WRITE_KEY` 在模块导入时读取，运行期不变，所以此限制实际不可达——但若未来引入热重载配置需重新评估。
2. **P0-2 索引 DROP 仅覆盖已知两个索引名**（`idx_pred_status`/`idx_pred_category`）。若未来给 predictions 表加新索引，需同步更新 `_migrate` 的 DROP 列表。已在代码注释标注。
3. **P0-3 health 503 把"scheduler 未运行"也判为 degraded**。这在测试无 lifespan 的 bare-app 场景下会返 503（已通过替身处理）。生产中 scheduler 正常运行时不受影响。

### 未提交

本轮所有改动（8 文件）+ 前序的 review 文档归并（`docs/reviews/`）+ 两份汇总文档，目前均为工作区改动，**尚未 commit**。需要时可分两个 commit：(1) docs 归并 + 汇总，(2) P0 修复 + 测试。

---

## 四、复现验证命令

```bash
# 后端全套（518 passed / 11 skipped）
cd backend && uv run --python 3.14 --with pytest --with-requirements requirements.txt python -m pytest -q tests/

# 仅本轮相关测试
... python -m pytest -q tests/test_operational_readiness.py tests/test_prediction_store.py tests/test_events_routes.py

# 泄漏检查（应无新增 v2_loop.db；现存文件时间戳 Jun 20 22:16 早于本轮）
ls -la backend/v2_loop.db
```
