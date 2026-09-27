# 代码审核报告 — 每日情报摘要（`feat/daily-intel-digest` WIP）

- **审核时间**：2026-09-26
- **审核对象**：分支 `feat/daily-intel-digest` 上**未提交**的工作树，HEAD = `3a6baca`
- **范围**：7 个文件 —— 3 个已跟踪改动（`backend/app/api/routes/events.py`、
  `frontend/src/lib/api.ts`、`frontend/src/components/app-nav.tsx`）+ 4 个新增文件
  （`backend/app/services/daily_digest_service.py`、`backend/tests/test_daily_digest_service.py`、
  `frontend/src/components/dashboard/daily-intel-digest-dashboard.tsx` 及其测试、
  `frontend/src/app/digest/page.tsx`）
- **方法**：逐文件读源码 + 实跑测试 + 针对性探针复现（不依赖文档自述状态）
- **本报告只做审核，未修改任何代码**

---

## 一、验证记录（实跑，非推断）

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端摘要单测 | `../.venv/Scripts/python.exe -m pytest tests/test_daily_digest_service.py -q` | **19 passed** |
| 后端事件路由回归 | `... -m pytest tests/test_events_routes.py -q` | **128 passed**，无回归 |
| 后端风格 | `... -m ruff check`（3 个改动文件） | **All checks passed** |
| 前端摘要组件 | `npx vitest run src/components/dashboard/daily-intel-digest-dashboard.test.tsx` | **3 passed** |
| 前端导航 | `npx vitest run src/components/app-nav.test.tsx` | **18 passed** |

测试面本身是扎实的：19 个后端用例覆盖了"日变化 = 收盘 vs 前收盘而非全时段轨迹"、
排序确定性 tiebreak、limit 截断、outcome 快照不污染、无时间戳跳过、真实 audit 文件默认加载路径、
以及路由不被 `/{event_id}` 遮蔽。**这条 bug 不在测试覆盖范围内，而不是测试写了但没锁住。**

---

## 二、发现清单

### F1 【真实缺陷 · 中】非法日期触发未捕获 `ValueError` → 生产 500

`events.py:467` 只用正则校验格式：

```python
date: str = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
```

正则**不校验语义**。`2026-02-30`、`2026-13-01`、`2026-00-10` 都能过正则，随后在
`daily_digest_service._day_window()` 的 `datetime.strptime(date, "%Y-%m-%d")` 抛 `ValueError`，
无人捕获 → HTTP 500。

**探针实测**：

```
2026-06-12 -> 200
2026-02-30 -> RAISED ValueError day is out of range for month
2026-13-01 -> RAISED ValueError time data '2026-13-01' does not match format '%Y-%m-%d'
2026-00-10 -> RAISED ValueError time data '2026-00-10' does not match format '%Y-%m-%d'
```

**为什么测试没拦住**：`test_digest_route_rejects_bad_date` 只喂了 `12/06/2026`（格式错 → 422），
语义非法的分支没有用例。

**建议修法**（二选一，推荐前者）：
- 把参数类型直接改成 `datetime.date`，交给 FastAPI/Pydantic 校验 —— 语义非法自动 422，
  且 `openapi.json` 里类型更准确；
- 或在 `_day_window` 内 `try/except ValueError` 转 400。

---

### F2 【约定漂移 · 中】新端点绕过了项目的"响应模型 → 生成类型"链路

这是本项目一条**明确且有测试保障**的约定：返回结构化 payload 的端点要配专用
Pydantic 模型，并经 `_frontend_export.py` 白名单生成前端类型。

相邻端点都是这么做的：

| 端点 | `response_model` | 是否在白名单 |
|---|---|---|
| `GET /events/movers` | `EventMoversResponse` | ✅ |
| `GET /events/category-counts` | `CategoryCountsResponse` | ✅ |
| `GET /events/digest`（本次新增） | **裸 `FlexibleResponse`** | ❌ |

而 `_frontend_export.py` 的模块注释**明确写着**：

```
Do NOT import models that should not leak to the frontend:
- FlexibleResponse (base class, no fields)
```

**后果**：
- `/openapi.json` 中 digest 响应**没有任何字段契约**（文档退化）；
- `tests/test_generate_types.py` 与 CI 的 `type-sync-check` job **覆盖不到**这个端点；
- 前端 `DailyDigestPayload` / `DailyDigestMover` 是手写在 `api.ts`（L812-850）的，
  与后端字段各改各的，**会静默漂移**且没有测试能发现。

**建议修法**：在 `backend/app/models/event.py` 新增
`DailyDigestResponse(FlexibleResponse)`（内嵌 `headline` / movers / unchanged 子模型）→
加进 `_frontend_export.py` 白名单 → 跑 `python -m scripts.generate_types` →
前端删掉手写 interface 改为从 `generated-types.ts` re-export。

---

### F3 【边界不一致 · 低】MATERIALITY 阈值的严格/非严格混用

同一份 0.5 阈值，两处判断符号不一致：

- 收进 movers：`abs(day_net) >= MATERIALITY`（**非严格**，`daily_digest_service.py:163`）
- 判定方向：`_movement()` 用 `> MATERIALITY` / `< -MATERIALITY`（**严格**，`:113-117`）

**探针实测**：`day_net == +0.5` 的事件**会进 movers 列表**，但标 `movement == "stable"`。
前端 `DeltaPill` 的显著性阈值同样是 `> 0.5`，因此它在"今日变动"表里渲染成一个**灰色 "—" 胶囊**。

**影响**：一张标题为"今日变动"的表里出现中性胶囊，语义自相矛盾。出现概率低（恰为 ±0.5），
但属确定性可复现的边界缺陷。

**建议修法**：把 movers 的准入也改成严格 `>`，或把 `_movement` 改成 `>=`/`<=` —— 两端统一即可。

---

### F4 【漏测 · 低】新增导航项未同步 `app-nav.test.tsx`

`app-nav.test.tsx` 有两处**显式枚举**标签清单的用例，是本仓库维护导航的标准做法：

- `renders all Event Intelligence entries`（L112-122）—— 列了 8 个条目，无"每日摘要"
- `keeps navigation labels on a single line`（L194-207）—— 同样枚举，无"每日摘要"

本次在 `app-nav.tsx:56` 新增了 `{ href: "/digest", label: "每日摘要" }`，但两处清单都没更新，
也没有 `/digest` 的 href 断言。测试仍然是绿的 —— 这是**漏测**而非失败，意味着新条目
（含它的单行样式约束）没有任何覆盖。

**建议修法**：把"每日摘要"加进上述两处清单，并补一条 `expect(link).toHaveAttribute("href", "/digest")`。

---

### F5 【小 UX · 低】`quiet` 与 `empty` 同时为真时语义重复；一个死字段

`empty = not movers and not new_event_ids`（`:199`）。当某天所有事件都"安静"时：
`movers=[]`、`new_event_ids=[]`、`quiet_event_ids=[...]` → `empty=True`。

前端据此**同时**渲染：
1. `payload?.empty` 的横幅："今日暂无日内变动——所有已建立事件安静，**或还没有任何概率快照**"
2. `unchanged.quiet_event_ids.length > 0` 的"安静日 (N)"卡片

两段文案不算冲突，但语义重复；且横幅第二分句"或还没有任何概率快照"在**确有事件、只是安静**时是误导。

另：`unchanged.quiet`（布尔）在整个前端从未被读取，属死字段 —— 要么用起来，要么删掉。

---

### F6 【性能 · 低，非本次回归】按分钟轮询 + 全量 store 解析

- 前端 `REFRESH_MS = 60_000`：对一个**每日**粒度的摘要偏密（虽已用 `document.hidden` 跳过后台标签页）。
- 后端 `build_daily_digest` 调 `list_all_events()` 仅为取标题，实际路径是
  `read_json()` → 取跨进程文件锁 + 解析**全部** `event_store.json`（当前 **3.86 MB**）+
  逐条 `normalize_event_record`。
- **这是既有模式，不是本次引入**：`/movers` 及另外 5 个以上端点同样用 `list_all_events()` 做标题富集。
  另注意 `histories_by_event()` 自带 mtime/size 缓存，而 `list_all_events()` 没有。

**建议**（优先级低）：若日后 store 继续增长，摘要只需 `event_id → title` 映射，可走更轻的读取路径；
或把 `REFRESH_MS` 放宽到 5-15 分钟。

---

## 三、明确排除的问题（查过、没问题）

| 项 | 结论 |
|---|---|
| **单位标度混用**（最常见的同类 bug） | ✅ 无问题。`estimated` 是 **0–100 百分点**；`MATERIALITY=0.5`、前端 `fmtPct`（加 `%`）、`fmtSignedPct`（加 `pt`）、`DeltaPill` 阈值（±0.5）**全部对齐**，不存在 0–1 vs 0–100 混用。 |
| **路由遮蔽** | ✅ 已处理。`/digest` 注册在 `/{event_id}` 之前，且有 `test_digest_route_is_not_shadowed_by_event_id` 钉住。 |
| **鉴权** | ✅ 只读端点，不涉及 `require_write_key`，无需写鉴权。 |
| **前端图标导入** | ✅ `Newspaper` 已在 `app-nav.tsx:21` 正确 import。 |
| **outcome 快照污染概率** | ✅ 已过滤（`kind == "outcome"` 跳过），且有专门测试。 |
| **测试是否为空转** | ✅ 19 个用例断言的是具体数值（`day_net`、`open`、`all_time_net_change`、排序序列），不是"跑通即过"。 |

---

## 四、结论

**这个 WIP 的质量高于平均水准**：模块职责清晰、注入式设计（store / history_loader / now 全可注入）
让测试完全确定性、docstring 写清了设计意图（"日变化 ≠ 全时段轨迹"这个区分很关键，且被测试锁住）。
测试数字是可信的。

**但有 2 项需要在上线/提交前处理**：

| 优先级 | 项 | 类型 |
|---|---|---|
| **P1** | F1 非法日期 → 500 | 真实缺陷，可被外部触发 |
| **P2** | F2 绕过响应模型/生成类型链路 | 约定漂移，长期维护成本 + 契约缺失 |

F3–F6 属可选打磨，不阻断。

> 注：以上为**审核当期**的原始记录（当时未改动任何代码）。
> **F1–F6 已于同日全部实施，见下方「五、修复批次」** —— 该章节取代本节结尾的"尚未实施"。

---

## 五、修复批次（2026-09-26 同日实施）

F1–F6 全部落地。**原报告正文保留不改**，本结构为追加记录。

### 各条落地方式

| 项 | 修法 | 落点 |
|---|---|---|
| **F1** | `date` 参数从 `str` + 正则改为 **`datetime.date`**，交给 FastAPI 校验：语义非法日期在边界即 422，不再进服务层 | `app/api/routes/events.py` |
| **F1** | `_day_window` 同时接受 `datetime.date` / ISO 字符串 / `None`（保留直调后向兼容） | `app/services/daily_digest_service.py` |
| **F2** | 新增 `DailyDigestResponse` + `DailyDigestHeadline` / `DailyDigestMover` / `DailyDigestUnchanged`（严格 `BaseModel`，非 `FlexibleResponse`） | `app/models/event.py` |
| **F2** | 加入 `_frontend_export.py` 白名单 → 重新生成 `generated-types.ts`；前端删掉手写 interface，改为 `from "./generated-types"` 别名再导出 | `_frontend_export.py`、`frontend/src/lib/api.ts` |
| **F3** | `_movement` 改为 `>=` / `<=`：与 movers 准入的 `abs(day_net) >= MATERIALITY` 对齐，±0.5 是 mover **且**有方向 | `daily_digest_service.py` |
| **F4** | "每日摘要"加入 `app-nav.test.tsx` 两处标签清单 + 新增 `/digest` href 断言 | `app-nav.test.tsx` |
| **F5** | 删除死字段 `unchanged.quiet`；`empty` 重定义为"当天完全无可摘要数据"（无 movers **且**无 new **且**无 quiet）；前端空态文案同步 | 服务层 + 组件 + 类型 |
| **F6** | `REFRESH_MS` 60s → **5min** 并 `export`（测试按真实间隔推进）；服务层改为**惰性读取 store**：无 in-window 数据时完全不解析那 3.86 MB | 组件 + 服务层 |

### 顺带修掉的两处

- `tests/test_generate_types.py` 的模型清单**漏了 `CategoryCountsResponse`**（该模型 7 月加入白名单时未同步测试）；文档里的"14 个模型"也早已过期。已补齐并更正为 16。
- 发现 `backend/scripts/` 目录本身**行尾混杂**（既有 LF 文件、CRLF 文件，也有 MIXED），是仓库既有卫生问题，未改动。

### 新增/改动的文件

**后端**：`app/models/event.py`(+50)、`app/models/_frontend_export.py`(+2)、`app/api/routes/events.py`(+24)、
`app/services/daily_digest_service.py`（重写 235 行）、`tests/test_daily_digest_service.py`（19 → **27** 用例）、
`tests/test_generate_types.py`、`scripts/generate_types.py`、`scripts/mutation_verify_daily_digest.py`（新）

**前端**：`src/lib/api.ts`、`src/lib/generated-types.ts`（重新生成 +52）、
`components/dashboard/daily-intel-digest-dashboard.tsx`（3 → **5** 用例）、`components/app-nav.test.tsx`（18 → **19**）

### 验证记录（全部实跑）

| 检查 | 结果 |
|---|---|
| `pytest tests/test_daily_digest_service.py` | **27 passed**（原 19 + 新增 8） |
| `pytest tests/test_events_routes.py tests/test_daily_digest_service.py` | **155 passed**, 14 subtests |
| `pytest tests/test_generate_types.py` | 通过（生成器 `--check` 与生成物一致） |
| `ruff check app/`（CI 原样） | **All checks passed** |
| `python -m compileall app tests` | exit 0 |
| `npx vitest run`（全量） | **125 files / 729 tests 全通过** |
| `npx tsc --noEmit` | **exit 0，零输出** |

### 变异验证（证明新测试真的锁住了行为）

新增 `backend/scripts/mutation_verify_daily_digest.py`：**逐条回退修复、确认 guard 测试变红、再恢复并核对 sha256 字节一致**。

```
[OK] F1  date typed as a real date        green before: True | red after: True | restored: True
[OK] F2  response_model publishes contract green before: True | red after: True | restored: True
[OK] F3  movement inclusive at threshold   green before: True | red after: True | restored: True
[OK] F5  empty means nothing digestable    green before: True | red after: True | restored: True
[OK] F6  store skipped when nothing in-window  green before: True | red after: True | restored: True
```

5 条 guard 全部"回退即红"，无一条是空转测试。

### 真实服务端到端确认（非仅单测）

启动后端对**真实数据**探测：

- **F1 生效**：`date=2026-02-30` / `2026-13-01` / `2026-00-10` / `12/06/2026` → 全部 **422**（原为 500）；合法日期仍 200。
- **F2/F5 生效**：`unchanged` 只返回 `['new_event_ids','quiet_event_ids']`，`quiet` 字段已消失。
- **真实载荷**：`GET /api/events/digest?date=2026-09-10` → `count 3`，头条 "Will a supervolcano erupt before 2050?" `day_net +5.37`（20.85 → 26.22，`previous_close`），另 2 条 falling（−4.29 / −2.95）。
- 无当日快照时（今天）返回 `empty: true`、`movers: []` —— 新语义符合预期。

### 一处必须记录的脚印（已修复）

首版变异脚本用 `Path.read_text()`（默认**通用换行**）读取，`write_text(newline="")` 回写，把
`app/api/routes/events.py` **整个文件从 CRLF 转成了 LF**（仓库 `core.autocrlf=true` + `* text=auto`，
工作树规范是 CRLF）。

**危险点**：`git diff` **看不到**——`autocrlf` 会在比对前归一化，diff 仍只显示 24 行插入。

处置：按字节还原为 CRLF（1916 CRLF / 0 bare LF / 78036 bytes），`git diff --numstat` 保持 24/0，
并用字节级实现重写脚本（`read_bytes` / `write_bytes`），复跑后 `events.py` 的 sha256 在整轮变异中稳定不变。

> 排查依据：`backend/scripts/` 与本仓库其他目录的 CRLF 基线对比，确认 `generate_types.py` 的 LF 是**既有状态**而非本次造成（Edit 工具在我改动的另外 6 个文件上均保住了 CRLF）。

### 未处理（本轮未授权/无法完成）

- `backend/tests/test_generate_types.py` 有 3 个**既有** F401（`os` / `shutil` / `tempfile` 未使用）。
  对 HEAD 原始文件复测得到同样 3 条 → 非本次引入；且 CI 跑的是 `ruff check app/`，**不含 `tests/`**，故长期未被拦截。未改动。
- 系统层面的运营问题（LLM 凭据缺失、`/api/health` 永久 503 等）不属于本次代码审核范围，
  另见 `docs/reviews/system-health-audit-2026-09-26.md`。

---

### 后记（2026-09-26，harness 合并）

本文正文按「交付记录不改写」保留原样。上文《新增/改动的文件》与《变异验证》两处提到的
`backend/scripts/mutation_verify_daily_digest.py` **已在后续合并中删除**：

- 它（自驱动，5 个变异，无单点索引）与另两个同批脚本合并为 **`backend/scripts/mutation_verify.py`**；
- 上表那 5 条 F1/F2/F3/F5/F6 现在是该 runner 的 **`daily-digest` set**，
  用 `cd backend && python scripts/mutation_verify.py verify daily-digest` 复跑；
- 新 runner 在原有三阶段（变异前绿 / 变异后红 / 还原后字节一致）之上**增加了第三阶段**：
  还原后守卫必须**重新变绿**。全文与 26 个变异的校验结果见
  `docs/reviews/system-health-audit-2026-09-26.md` **§十一**。

