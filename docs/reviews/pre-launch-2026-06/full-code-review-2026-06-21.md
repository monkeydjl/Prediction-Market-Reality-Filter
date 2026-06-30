# Full Code Review - 2026-06-21

**项目**: Prediction Market Reality Filter (PMRF) / Event Intelligence Platform v0.3.0  
**审查日期**: 2026-06-21  
**审查范围**: 后端（Python FastAPI）、前端（Next.js 16 + React 19）、测试套件、部署与运维  
**审查人**: QoderWork CTO Audit  
**总体评级**: **B+**（修复 P0/P1 后可达 A-）

---

## Executive Summary

本次全面审查覆盖后端 30+ 服务模块、前端 19 个组件、50+ 测试文件、以及全部部署配置。项目整体工程质量扎实：架构分层清晰、数据持久化设计考虑了原子写入和并发安全、测试覆盖率高（508+ 用例）。

审查共发现 **1 个 P0、8 个 P1、12 个 P2、8 个 P3**，合计 29 项问题。最关键的发现集中在 Docker 部署配置（healthcheck 命令不存在）和后端运行时健壮性（静默异常、内存泄漏、N+1 查询）。前端代码质量较高，TypeScript strict 模式下无 `any`、无 `@ts-ignore`，构建通过且所有 6 个路由均可正常静态导出。

**上线建议**: 修复 P0 + 5 个后端 P1 后可有条件上线（supervised launch）。前端 P1 可在上线后一周内跟进。

---

## 1. P0 - 阻塞上线

### P0-1: Docker healthcheck 使用 curl，但 python:3.11-slim 镜像中不存在

**文件**: `deploy/docker-compose.yml:18`, `deploy/Dockerfile:10-12`

`docker-compose.yml` 配置了 `test: ["CMD", "curl", "-sf", "http://localhost:8000/api/health"]`，但 Dockerfile 基于 `python:3.11-slim`，该镜像不包含 `curl`。这意味着 **Docker 容器的 healthcheck 永远会失败**，容器会被标记为 unhealthy。

**修复方案（二选一）**:

```dockerfile
# 方案 A: 在 Dockerfile 中安装 curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 curl \
    && rm -rf /var/lib/apt/lists/*
```

```yaml
# 方案 B: 改用 python 内置的 urllib（无需安装额外依赖）
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]
```

---

## 2. P1 - 上线前必须修复

### P1-1: 多个服务静默吞没异常，生产环境无法排查问题

**文件**: `backend/app/services/` 下多个服务模块

审查发现多处 `except Exception` 或 `except` 后无任何日志输出的代码路径，主要集中在外部 API 调用（RSS 解析、GNews、SEC EDGAR 等）和 LLM 调用。一旦这些调用失败，事件会被静默跳过，运维人员无法从日志中发现数据缺失。

**修复**: 所有 `except` 块至少添加 `logger.warning("...", exc_info=True)` 或 `logger.debug()`。关键路径（事件发现、自动结算）应使用 `logger.error`。

### P1-2: /api/health 始终返回 HTTP 200，外部监控无法感知降级

**文件**: `backend/app/main.py:93-110`

当前 `/api/health` 即使在 `status: "degraded"`（存在失败任务）时也返回 HTTP 200。Docker healthcheck 和 systemd healthcheck timer 都依赖 HTTP 状态码判断健康状态，降级场景下不会触发告警或自动重启。

**修复**:

```python
@app.get("/api/health")
async def api_health():
    from app.core.scheduler import scheduler
    from app.services.loop_status_service import loop_status

    status = loop_status(scheduler_running=scheduler.running)
    failed_runs = [
        job for job, run in status.get("runs", {}).items()
        if run and run.get("status") == "failed"
    ]
    health = "degraded" if failed_runs else "ok"
    return JSONResponse(
        status_code=200 if health == "ok" else 503,
        content={
            "status": health,
            "version": "0.3.0",
            "scheduler_running": scheduler.running,
            "failed_runs": failed_runs,
            "loop": status,
        },
    )
```

### P1-3: Rate Limiter 内存泄漏 — _hits 字典无限增长

**文件**: `backend/app/core/rate_limit.py:15`

`self._hits: dict[str, Deque[float]]` 以 `client:method:path` 为 key。虽然 deque 中过期的时间戳会被 popleft 清除，但 **dict 的 key 永远不会被删除**。长期运行后，每个曾经出现过的客户端 IP + 请求路径组合都会在内存中留下一个空 deque 条目。

对于一个面向少量用户的内部工具，短期内不会造成严重问题，但如果暴露到公网，来自爬虫和扫描器的请求会快速积累数万个 key。

**修复**: 添加定期清理逻辑：

```python
# 在 dispatch 方法中，每 1000 次请求做一次清理
if len(self._hits) > 1000:
    stale = [k for k, v in self._hits.items() if not v]
    for k in stale:
        del self._hits[k]
```

### P1-4: N+1 查询模式 — /decisions/open 逐个读取事件文件

**文件**: `backend/app/api/routes/events.py` 中 decisions 相关端点

`/decisions/open` 需要加载所有事件的完整记录来构建决策报告，当前实现为逐个调用 `event_store.get(id)`。当事件数达到 50+ 时，每次请求触发 50+ 次独立的 JSON 文件读取操作。

**修复**: 改为批量加载 — 一次读取 `event_store.json` 全量数据，在内存中按 ID 索引。

### P1-5: 审计日志全量扫描 — histories_by_event() 每次读取整个 audit log

**文件**: 审计服务中 `histories_by_event()` 函数

每次调用会读取完整的 `event_audit.jsonl`（可能达 5000+ 行）来过滤某个事件的历史快照。随着运行时间增长，这个文件只会越来越大，每次请求的 I/O 成本线性增长。

**修复方案**:
- 短期: 添加内存缓存，按 `event_id` 索引，定期刷新
- 中期: 将审计日志迁移到 SQLite，按 `event_id` 建索引

### P1-6: Docker 容器缺少 .dockerignore，存在 .env 泄露风险

**文件**: 项目根目录（缺少 `.dockerignore`）

Dockerfile 使用 `COPY backend/ /app/` 复制整个 backend 目录。如果没有 `.dockerignore`，`backend/.env`（包含 API 密钥）会被复制到 Docker 镜像层中，即使运行时通过 `env_file` 挂载新的配置，镜像层中的旧 `.env` 仍可被提取。

**修复**: 在项目根目录创建 `.dockerignore`:

```
**/.env
**/.env.*
**/__pycache__
**/node_modules
**/.git
**/logs
**/*.pyc
backend/event_store.json
backend/event_cache.json
backend/market_cache.json
backend/v2_loop.db
```

### P1-7: Docker 容器以 root 用户运行

**文件**: `deploy/Dockerfile`

Dockerfile 未创建非 root 用户，uvicorn 以 root 权限运行。如果容器被入侵，攻击者将获得 root 权限。

**修复**: 在 Dockerfile 中添加：

```dockerfile
RUN useradd -m -r appuser && chown -R appuser:appuser /app
USER appuser
```

### P1-8: 前端测试 ManualResolvePanel 失败（2 个用例）

**文件**: `frontend/src/components/detail/manual-resolve-panel.test.tsx:27,40`

运行 `vitest run` 发现 `ManualResolvePanel` 的 2 个测试用例均失败，错误为 `getByLabelText("实际结果（0–100）")` 返回了多个匹配元素。该组件使用 `<label>` 隐式关联（label 包裹 input），可能在 Testing Library v16 下产生歧义匹配。

**当前状态**: 后端 511 测试全部通过，前端 6/7 测试文件通过（14/16 用例通过）。

**修复**: 将 label 文本包裹在 `<span>` 中或使用显式 `<label htmlFor>` + `<input id>` 关联，确保 Testing Library 能唯一匹配。

---

## 3. P2 - 上线后两周内修复

### P2-1: 跨存储事务缺口 — JSON + SQLite 无共享事务保障

**文件**: `backend/app/memory/` 和 `backend/app/utils/`

事件解析（resolution）需要同时更新 JSON event_store 和 SQLite predictions/links 表。当前两者使用独立的原子写入（JSON 用 tempfile+os.replace，SQLite 用 BEGIN/COMMIT），但没有跨存储的事务协调。如果 JSON 写入成功但 SQLite 写入失败（或反之），会产生数据不一致。

**缓解措施**: 已有的 reconciliation 逻辑可以检测并修复大部分不一致。建议增加定时对账任务（每小时扫描一次 orphan predictions 和 orphan links）。

### P2-2: CORS 配置过于宽松

**文件**: `backend/app/core/config.py` (CORS_ALLOWED_ORIGINS), `backend/app/main.py:49-55`

生产环境应严格限制 CORS origins。当前 `allow_methods=["*"]` 和 `allow_headers=["*"]` 应收窄为实际需要的最小集合。

### P2-3: API 端点缺少输入验证

**文件**: `backend/app/api/routes/events.py`

`POST /analyze` 等写入端点未使用 Pydantic request model 进行参数校验。`event_question` 字段可以接受任意长度字符串（包括 1MB 的 payload），可能导致 LLM 调用成本异常或内存问题。

**修复**: 为所有写入端点添加 Pydantic BaseModel 校验，包含 `max_length` 约束。

### P2-4: 缺少 global-error.tsx

**文件**: `frontend/src/app/`（缺少此文件）

当前 `error.tsx` 只处理单个路由段的错误。如果根 `layout.tsx` 本身抛出异常（如字体加载失败），用户将看到空白页面。Next.js 16 建议添加 `global-error.tsx` 并提供独立的 `<html>` 和 `<body>` 标签。

### P2-5: TrackingDecision 组件状态在 props 变更时不会同步

**文件**: `frontend/src/components/detail/tracking-decision.tsx:21-22`

组件通过 `useState(status ?? "watching")` 初始化状态，但当父组件重新获取事件数据（如手动结算后刷新）时，已修改的 tracking status/priority 不会与新的 props 同步。

**修复**:

```tsx
useEffect(() => {
  setCurStatus(status ?? "watching");
  setCurPriority(priority ?? "medium");
}, [status, priority]);
```

### P2-6: SystemStatus 首次渲染闪烁

**文件**: `frontend/src/components/dashboard/system-status.tsx:21`

`loading` 初始值为 `false`，`status` 为 `null`。首次渲染时组件显示所有值为 "—" 的完整 UI，随后 `useEffect` 触发后才设置 `loading=true`，造成一帧闪烁。

**修复**: 将第 21 行 `useState(false)` 改为 `useState(true)`。

### P2-7: CSV 导出缺少 UTF-8 BOM，Excel 打开中文乱码

**文件**: `frontend/src/lib/csv.ts:16`

`downloadCsv` 创建的 CSV Blob 缺少 UTF-8 BOM 前缀。在 Microsoft Excel 中打开导出的 CSV 时，中文标题将显示为乱码。

**修复**:

```ts
const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
```

### P2-8: sparkSeries 过滤器丢弃零值数据点

**文件**: `frontend/src/lib/adapt.ts:121`

`.filter((v) => v > 0)` 会丢弃概率恰好为 0 的数据点。0% 概率是有效值（如某事件已确定不会发生），丢弃后会扭曲 sparkline 可视化。

**修复**: 改为 `.filter((v) => !Number.isNaN(v))`。

### P2-9: Docker 数据文件未挂载为 volume，容器重建丢失数据

**文件**: `deploy/docker-compose.yml:13-14`

当前仅挂载了 `backups` 和 `logs` 两个目录。`event_store.json`、`v2_loop.db`、`event_audit.jsonl` 等核心数据文件存在于 `/app/` 下，容器重建时会被镜像中的空文件覆盖，导致全部历史数据丢失。

**修复**: 在 `docker-compose.yml` 中增加数据卷挂载：

```yaml
volumes:
  - pmrf_data:/app/backups
  - pmrf_logs:/app/logs
  - pmrf_stores:/app/stores  # 或直接将 JSON/SQLite 文件移至 backups/ 子目录
```

同时需要更新代码中的数据路径配置。

### P2-10: CI 缺少依赖安全扫描

**文件**: `.github/workflows/ci.yml`

当前 CI 仅执行 `compileall` 和 `unittest`。缺少 `pip-audit`（Python 依赖漏洞扫描）和 `npm audit`（前端依赖漏洞扫描）。

### P2-11: 备份无恢复流程文档

**文件**: `backend/scripts/backup_stores.py`, `docs/ops/RUNBOOK.md`

`backup_stores.py` 可以正确创建带时间戳的 zip 归档并清理旧备份，但 RUNBOOK 中没有记录恢复步骤。运维人员在数据丢失时不知道如何从备份恢复。

### P2-12: decisions/page.tsx 错误提示位置不当

**文件**: `frontend/src/app/decisions/page.tsx:125`

错误提示渲染在筛选按钮下方、内容区域上方。切换筛选条件时错误提示不会消失，造成困惑。

**修复**: 在 filter 的 `onClick` 中添加 `setError(null)`，或将错误提示移至筛选按钮上方。

---

## 4. P3 - 后续迭代改进

| 编号 | 文件 | 问题 |
|------|------|------|
| P3-1 | `detail/evidence-list.tsx:38`, `detail/market-links.tsx:85` | 外部链接 `rel` 属性不一致，统一为 `"noopener noreferrer"` |
| P3-2 | `lib/adapt.ts:64` | `categoryOf` 通过 `unknown` 转型访问 `legacy_analysis`，应在 EventRecord 类型中声明可选字段 |
| P3-3 | `components/operator-key-control.tsx:28` | 按钮缺少 `aria-label`，屏幕阅读器可能无法正确朗读 |
| P3-4 | `deploy/Dockerfile` | 可考虑多阶段构建减小镜像体积（当前安装 libxml2 增大了约 40MB） |
| P3-5 | 多个 services | 错误处理模式不统一：部分用 `logger.error`，部分用 `logger.warning`，部分返回空列表，建议建立标准模板 |
| P3-6 | `backend/app/api/routes/events.py` | 5 个 API 路由缺少 HTTP 级别的集成测试（仅通过 service 单元测试间接覆盖） |
| P3-7 | `.github/workflows/ci.yml` | 前端测试（Vitest）未纳入 CI，`npm test` 仅在本地执行 |
| P3-8 | 自动结算调度 | `event_auto_resolve` cron job 缺少专门的单元测试 |

---

## 5. 亮点与工程优势

审查过程中也注意到项目做得好的方面：

**后端架构** — 服务层按功能清晰划分（event intelligence、resolve、collection、scoring 等），编排器 `event_intelligence_service` 职责明确。JSON + SQLite 双存储策略针对不同数据特性做了合理选择：JSON 适合事件记录的灵活 schema，SQLite 适合需要关系完整性的预测和链接数据。

**数据完整性** — `file_store.py` 实现了 tempfile + `os.replace` 的原子写入模式，`sqlite_db.py` 配置了 WAL 模式和进程级写锁。备份脚本 `backup_stores.py` 覆盖了所有运行时存储文件并实现了自动轮转。

**安全基础** — API 密钥仅在日志中输出长度（不泄露内容），写入端点有 API Key 校验，CORS 和 Rate Limit 中间件已就位。

**前端质量** — TypeScript strict 模式下零 `any`、零 `@ts-ignore`。所有页面都有 loading、error、empty 三种状态处理。API 层实现了 15 秒 TTL 缓存和 inflight 请求去重。`SectionErrorBoundary` 提供了组件级别的错误隔离。

**测试覆盖** — 后端 44 个测试文件、508+ 用例，覆盖了几乎所有 service 和 store 模块。测试用例注重行为验证而非仅仅 mock。

**部署运维** — 同时提供 Docker、systemd、Windows 一键启动三种部署方式。systemd 配置了 `Restart=always`、每日备份 timer、5 分钟 healthcheck timer。

---

## 6. 修复优先级路线图

**Phase 1 — 上线前（预计 5-7 小时）**:
1. P0-1: Dockerfile 安装 curl 或改用 python healthcheck
2. P1-1: 为所有静默异常添加日志
3. P1-2: /api/health 降级时返回 503
4. P1-3: Rate limiter 添加过期 key 清理
5. P1-6: 添加 .dockerignore
6. P1-7: Docker 非 root 用户
7. P1-8: 修复 ManualResolvePanel 测试失败

**Phase 2 — 上线后一周内（预计 3-4 小时）**:
1. P1-4: decisions 端点批量加载优化
2. P1-5: 审计日志缓存或迁移 SQLite
3. P2-5: TrackingDecision props 同步
4. P2-6: SystemStatus 初始 loading 状态
5. P2-7: CSV 添加 BOM
6. P2-9: Docker 数据卷挂载

**Phase 3 — 一个月内**:
1. P2-2/P2-3: CORS 收紧 + Pydantic 输入校验
2. P2-4: global-error.tsx
3. P2-10/P2-11: CI 安全扫描 + 恢复文档
4. P3 系列改进
