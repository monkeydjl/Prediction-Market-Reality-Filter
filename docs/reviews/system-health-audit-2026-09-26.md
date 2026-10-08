# 系统健康审计 — Prediction Market Reality Filter

- **审计时间**：2026-09-26 23:30–23:40 (UTC+8)
- **方法**：按项目自带清单 `docs/system-review-prompt.md` 逐项核查；**实际启动后端**（`127.0.0.1:8000`，`SCHEDULER_ENABLED=false`，`ALLOW_OPEN_WRITES=true` 仅为让 fail-closed 守卫放行）并打真实端点，而非只读代码
- **数据来源**：`/api/health`、`/api/llm/diagnostics`、`/api/events/calibration`、`/api/events/decisions/open`、`v2_loop.db`（只读）、`event_store.json`、`event_audit.jsonl`、外部数据源直连探测
- **审计后已停服务**，运行时未做任何写入

> ⚠️ 本清单本身已过期：§7 要求核对 `SOURCE_WEIGHTS` 含 **Manifold 0.3**，但 Manifold 已被 `7b5f0ed chore: remove Manifold from active sources` 移除，当前权重表里没有它。§7 的预期值是陈旧的。

> 🔴 **本报告已发布后二次核实，§2 与 §8 的原始结论被推翻，总分与 TOP 3 已更正。**
> 更正详情见文末「更正记录」。阅读时请以文末为准，正文 §2/§8 保留原始措辞以便对照。

---

## 总览

| # | 项目 | 结论 | 关键数据 |
|---|---|---|---|
| 1 | 数据源健康 | ✅ 正常 | 3/3 直连 200；Polymarket **未被 Cloudflare 拦** |
| 2 | LLM 分析链 | ⚠️ **环境未配置**（原判 ❌，已更正） | **0/8 任务可用**，api_key 全空 |
| 3 | 翻译质量 | ⚠️ 警告 | 开关 on、既有标题是中文；但无 key → 新事件无法翻译 |
| 4 | 模拟交易 | ✅ 正常 | 表结构正确，80 笔（31 closed 有 PnL / 49 open） |
| 5 | 校准反馈 | ⚠️ 警告 | n=89，**总体 skill 0.20 / POOR** |
| 6 | 决策阈值 | ⚠️ 警告 | act 2 / provisional 31 / watch 46 / skip 92；1 条标度错位 |
| 7 | 配置文件 | ⚠️ 警告 | 开关均符合预期；生产模板缺 `LLM_STARTUP_CHECK_ENABLED` |
| 8 | 调度与频率 | ✅ **正常**（原判 ❌，已更正） | 节奏正确；503 与 running 回收**均为设计行为** |

### 总体健康分：**52 / 100**（原 49，§8 由 6 更正为 9）

| 维度 | 满分 | 得分 | 说明 |
|---|---|---|---|
| 数据源健康 | 15 | 14 | 全部可达，仅未验证需认证的 provider |
| LLM 分析链 | 25 | **0** | 本机无凭据；**属环境状态，非代码缺陷** |
| 翻译质量 | 5 | 3 | 机制在，凭据缺 |
| 模拟交易 | 10 | 8 | 逻辑已验证，但 7/24 后无新数据 |
| 校准反馈 | 15 | 6 | 有样本，但整体不达标 |
| 决策阈值 | 10 | 6 | 阈值合理，存在 1 条虚假信号 |
| 配置文件 | 10 | 6 | 生产模板缺启动校验；无花费上限（已改正为生产有 25） |
| 调度与频率 | 10 | **9** | 节奏正确，回收机制已接线，503 为设计 |
| **合计** | **100** | **52** | |

> 分数低的**主因是一个环境问题**（本机 `.env` 密钥已按安全要求清空，LLM 链无法工作），而不是代码缺陷。
> 把凭据配好并重跑，§2（25 分）与 §3（部分）的分数会立刻回来。

---

## §1 数据源健康 — ✅ 正常

直连探测（非经应用）：

| 数据源 | HTTP | 耗时 |
|---|---|---|
| `gamma-api.polymarket.com/markets` | **200** | 2.27s |
| `api.elections.kalshi.com/trade-api/v2/markets` | **200** | 3.95s |
| `api.manifold.markets/v0/markets` | **200** | 2.95s |

- **清单担心的事项未发生**：Polymarket gamma-api 返回 200，**没有被 Cloudflare 403**。
- 事件存储内 257 条记录，来源分布正常（Polymarket 190 / Kalshi 12 / Limitless 5，其余无 baseline）。
- 未验证：需要认证的 provider（Opinion / Predict.fun / API-Football / Sportmonks / The Odds API）——这些 key 在 `.env` 中为空，按设计 fail-closed 不贡献事件。
- ⚠️ 数据**陈旧**：事件存储最后更新 2026-07-08，审计日志最后一天 2026-09-10，台账最后活动 2026-09-11。

---

## §2 LLM 分析链 — ⚠️ 环境未配置（**首版判 ❌，二次核实后更正**）

> **更正**：首版把这条判为代码缺陷 ❌。二次核实：本机 `.env` 的密钥是被**按安全要求主动清空**的
> （见 HANDOFF 的"需轮换 key"待办），属**预期的本机状态**，不是代码缺陷。正确的表述是
> "本机无法评估 §2/§3，且生产部署前必须配置"。**这条仍然是总分的主要拖累项，但归因从"代码坏"改为"环境未配"。**

应用启动即自报：

```
CRITICAL - No configured LLM route/API key — LLM calls will fail at runtime
```

`GET /api/llm/diagnostics` 逐任务确认：

```
configured_task_count   = 0
unconfigured_task_count = 8
```

8 个任务（`default` / `probability_analysis` / `translation` / `open_web_extraction` / …）全部：

| 字段 | 值 |
|---|---|
| `route_source` | `legacy_openai` |
| `provider_configured` | True |
| **`api_key_configured`** | **False** |
| `base_url_configured` | True |

**根因**：`backend/.env` 里 4 路 provider 的凭据**全为空**：

```
OPENAI_API_KEY_1..4  = (空)
OPENAI_BASE_URL_1..4 = (空)
```

模型名有值（`nvidia/nemotron-3-ultra-550b-a55b:free`、`mistral-medium-3-5`、`deepseek-v4-flash`、`sensenova-6.7-flash-lite`、`agnes-2.0-flash` …），但**没有 key 也没有 base_url**，因此 4 路 fallback 一路都用不了。传统单路 `OPENAI_API_KEY` 在 `.env` 中**完全不存在**（默认 `""`）。进程环境变量里也没有任何 `OPENAI_*`。

> 说明：编号式 `OPENAI_API_KEY_N` / `OPENAI_MODEL_N_M` 是真实的 LLM fallback gateway 功能（spec: `docs/superpowers/specs/2026-07-05-llm-fallback-gateway-design.md`，测试: `test_llm_gateway_service.py`），从 `os.environ` 直读，**不经过 `settings`**——排查时不要在 `config.py` 里找它。

**影响面**：清单 §2 全部子项（key 有效性、中文标题生成、>30pp 偏离 risk_flag）与 §3 全部子项都无法评估；"确定性回退比例" 事实上是 **100%**。附带：`LLM_STARTUP_CHECK_ENABLED=False`，所以这只是 CRITICAL 日志，不会阻止启动。

**修复建议**：填入 `OPENAI_API_KEY_N` + `OPENAI_BASE_URL_N`（至少一路），生产环境同时把 `LLM_STARTUP_CHECK_ENABLED=true`，让缺 key 变成启动失败而不是静默降级。

---

## §3 翻译质量 — ⚠️ 警告

| 检查项 | 结果 |
|---|---|
| `AUTO_TRANSLATE_TITLES` | **True** ✅ |
| 既有中文标题 | 存在且质量正常（例：「安德鲁·塔特的政党会在下一次英国大选中赢得一个席位吗？」） |
| 定时任务 `translate_titles` | 台账有记录，最后成功 **2026-09-10**，耗时 5.27s |
| title 翻译是否独立于主分析 | 是（独立 job + 独立 `LLM_ROUTE_TRANSLATION` 路由）✅ |

**问题**：`LLM_ROUTE_TRANSLATION` 的 `api_key_configured=False`。既有标题是 7–9 月间用旧 key 翻译的存量；**重启后新发现的事件将无法获得中文标题**。

---

## §4 模拟交易 — ✅ 正常（但数据陈旧）

- `PAPER_TRADE_ENABLED=True`、`PAPER_TRADE_WATCH_ENABLED=True` ✅
- `simulated_trades` 表存在，24 列结构完整（`entry_prob` / `market_prob` / `entry_edge` / `exit_*` / `pnl_pct` / `is_win` / `status`）✅
- **80 笔**：`closed: 31`（`pnl_pct` 与 `exit_time` 均已填充）、`open: 49`
  → **自动创建 + 自动平仓 + PnL 计算链路均已跑通** ✅
- 标度健康：`entry_edge` 区间 **(-31.06, 30.0)**，符合 0–100 百分点标度 ✅
- ⚠️ 最后一笔 `entry_time` = **2026-07-24**，此后无新交易。

---

## §5 校准反馈 — ⚠️ 警告

`CALIBRATION_FEEDBACK_ENABLED=True` ✅，样本在（`n=89`），但**结果不达标**：

| 维度 | Brier | Skill | 评级 | n |
|---|---|---|---|---|
| **总体** | 0.1997 | **0.2013** | **POOR** | 89 |
| Polymarket | 0.2059 | 0.1763 | RANDOM_LEVEL | 83 |
| manifold | 0.1133 | 0.5467 | ACCEPTABLE | 6 |

按类别（节选）：

| 类别 | Skill | 评级 | n |
|---|---|---|---|
| sports_game | 0.0494 | RANDOM_LEVEL | 33 |
| sports_general | 0.1307 | RANDOM_LEVEL | 20 |
| crypto_price_btc | 0.2845 | POOR | 6 |
| geopolitics_general | 0.8604 | EXCELLENT | **2** |
| monetary | 0.19 | RANDOM_LEVEL | 2 |

**照实说**：系统整体概率技能分 **0.20（POOR）**；占样本 93% 的 Polymarket 基线本身只有 **0.176（RANDOM_LEVEL）**——即当前输出基本没有超出市场基线的预测能力。表里的 `EXCELLENT` 是 n=2 的噪声，**不要**当作好消息。

另外："校准后 trust_weight 是否合理"这一项，样本里 `trust` 稳定为 `0.5`（休眠类别默认值），说明绝大多数类别仍处于休眠、未获得真实校准权重。

---

## §6 决策阈值 — ⚠️ 警告

| 配置 | 值 | 清单预期 |
|---|---|---|
| `DECISION_ACT_EDGE` | **6.0** | 6.0 ✅ |
| `DECISION_WATCH_EDGE` | **2.0** | 2.0 ✅ |

`predictions` 表 171 条的决策分布：

```
act              :   2
provisional_act  :  31
watch            :  46
skip             :  92
```

（`/api/events/decisions/open` 只统计未结算子集：`act 0 / provisional_act 21 / watch 27`，共 48。）

- `provisional_act` 有 31 条，冷启动样本积累**在进行** ✅
- `review_queue_items = 0`、`decision_timeline = 0` —— 人工复核队列与决策时间线**均为空**。若有流程期望 `act` 决策进入人工复核，当前没有任何一条被路由进去。
- ⚠️ **1 条决策由标度错位的基线驱动**（详见下节）。

### 附带发现：1 条 Kalshi 遗留记录的标度错位

| 项 | 值 |
|---|---|
| event | `0779bde4dcd63e08`（"Andrew Tate's party … UK election"） |
| platform | Kalshi (`KXBRUVSEAT-35`) |
| `market_probability`（存储） | **0.2** |
| `ai_probability` | 30.17 |
| `raw_edge` | **29.97** = 30.17 − 0.2 |
| `adjusted_edge` | **14.98** = 29.97 × trust(0.5) |
| `decision` | **provisional_act** |

**定性**（已核实，不是当前适配器的 bug）：现行 `kalshi_event_source._baseline_and_quote` 读 `last_price_dollars` 并 `× 100`，注释明确 "All three values are on the 0-100 scale"，且 12 条 Kalshi 记录中 **11 条标度正常**。只有这 1 条（`first_seen 2026-07-01`、`last_updated 2026-07-08`）是旧代码留下的存量。全库 `market_probability < 1` 的行**有且仅有这 1 条**。

**危害**：若基线按正确的 20 计算，`adjusted_edge = (30.17 − 20) × 0.5 = 5.09`，**低于 ACT 阈值 6.0** → 应为 `watch`。它是 31 条 `provisional_act` 之一，也是决策列表的首条展示样本。系统自己的报告也被带偏，写下「基准概率 **0.2%** 变化至 30.2%」。

**更值得注意的**：`ai_analysis_service.py:70` 只做 `_clamp(market_probability, 0, 100)`——**采信 0.2 而不怀疑**。全链路缺少"两向市场概率不该离 0/100 太近"的合理性守卫，所以同类坏数据只会在下游被放大成假 edge。

**修复建议**：(a) 重算或作废这条存量记录；(b) 在写入/读取预测时加标度合理性校验（例如 `0 < mp < 5` 且该市场有成交量时判为可疑并拒绝或重取）；(c) 给 edge 计算加跨标度守卫而不是只 clamp。

> **更正（2026-09-27）：上方「这是旧代码留下的存量」这句归因是错的。** 当时推断"2026-07-01 那条是旧版
> Kalshi 适配器忘了 `×100`"，**未经 git 核实**。穷举该文件全部历史（`git log --all`）后：
>
> ```
> git log --all -S"last * 100"  -- backend/app/services/kalshi_event_source.py
>   → 只有 6cc99f3（2026-06-16 Initial commit）一处
> git log --all -p -- <file> | grep "return last\|last_price"
>   → 143: last = safe_float(market.get("last_price_dollars"), 0.0)
>     145:- return last * 100            ← 初始提交就是这样
>     146:+ return last * 100, 0.0, 0.0   ← 2026-06-29 只改了返回值个数
> ```
>
> **即：该适配器从第一版起就 `×100`，从未存在过不乘 100 的版本**，产不出 0.2。
> 唯一能到达 `predictions.market_probability` 的路径是 `POST /api/events/analyze` 的
> `baseline_probability`（`events.py:274` → `analyze_event` → `build_event_record:157`
> `baseline = safe_float(analysis["market_probability"])`），而它 `ge=0.0` **接受 0.2**。
> 取证旁证：该行 `snapshot_*` 字段**全为空**（自动发现路径会填），`event_market_links.link_method='freeze'`
> 发生在预测创建后 0.15 秒 —— 与"先经 API 分析、随后冻结链接"一致。
>
> **归因改错的后果**：原措辞会引导后来者去"修 Kalshi 适配器"，而那里没有 bug。
> **对处置的影响**：原建议 (a)「重算」隐含"真值是 20"，这是**推断而非事实** —— 调用方也可能真的在说 0.2%。
> 存量数据里没有任何字段能区分两者，**所以第七章 §8.4 的三选项仍未决，且「重算」的依据比原稿更弱**。
>
> 另一个细节：`build_event_record` 的护栏是 `abs(change) > 30`（加 `large_deviation_*` 标记），
> 而这条的 `change = 29.97` —— **差 0.03pp 没触发**。即护栏刚好处在"擦肩而过"的位置，这条记录没被标记出来。

---

## §7 配置文件 — ⚠️ 警告

| 项 | 值 | 判断 |
|---|---|---|
| `SOURCE_WEIGHTS` | `{Polymarket 3.0, Kalshi 1.0, Limitless 0.8, Opinion 0.6, Predict.fun 0.5, Open Web 0.5, Polymarket Crypto 1.0, World Cup 0.3, Metaculus 0.5}` | ✅ 平衡；**清单里的 Manifold 已不存在**（清单过期） |
| `WORLD_CUP_SOURCE_ENABLED` | False | ✅ 符合预期 |
| `OPEN_WEB_ENABLED` | False | ✅ 符合预期 |
| `LIMITLESS_SOURCE_ENABLED` | True | — |
| `AUTO_TRANSLATE_TITLES` | True | ✅ |
| `LLM_STARTUP_CHECK_ENABLED` | **False** | ⚠️ 生产应置 True |
| `LLM_DAILY_COST_CAP_USD` | **0.0** | ⚠️ **0 = 不限额**（代码注释明说），且应用启动时主动告警 `daily LLM spend is UNLIMITED` |
| 明显笔误（`hhttp://` 双 h） | 未发现 | ✅ |
| 凭据 | 4 路 key **全空** | ❌ 见 §2 |

**告警出口缺失**（启动日志自报）：

```
WARNING - No alert push channel is configured — a failed job is recorded
          (loop_runs ledger, pmrf_scheduler_failed_runs_total, /api/health 503)
          but nothing notifies anybody.
INFO    - Sentry disabled: SENTRY_DSN is empty.
```

→ 任务失败只落台账，**没有任何人会被通知**。

---

## §8 调度与频率 — ✅ 正常（**首版判 ⚠️/❌，二次核实后更正**）

> **更正**：首版把"`/api/health` 永久 503"和"4 条孤立 running 未回收"都判为缺陷，并称生产会
> "容器无限重启"。**两半都错了**，详见文末「更正记录」。本节的 ❌ 小节保留原始措辞以便对照。

节奏**符合清单预期**：

| 任务 | 触发 | 位置 |
|---|---|---|
| `event_discover` | `IntervalTrigger(hours=4)` | `app/core/scheduler.py:1603` ✅ |
| `event_discover_startup` | 启动后 **30 秒**一次性（limit=10，成本护栏） | `:1613` ✅ |
| `event_auto_resolve` | `CronTrigger(hour=22, minute=30)` | `:1620` ✅ |

### ❌ `/api/health` 永久返回 503

实测：`GET /api/health` → **HTTP 503**，`status: "degraded"`。

判定逻辑（`app/main.py:510–527`）：`failed_runs` 取自 **`latest_run_per_job`**，只要**每个任务最近一次**运行是 `failed`，就 `degraded` → 返回 503。

而唯一的失败项是一条**不会自行消失**的陈旧记录：

```
job    : world_cup_api_football_validate
status : failed
started: 2026-07-07T17:46:46   finished: 2026-07-07T17:46:48
error  : API-Football returned 0 fixtures for league=1 season=2026;
         check provider coverage/config before import.
```

该任务自 7 月 7 日后再未运行（World Cup 源已关闭），所以这条 `failed` 会**永久**留在 `latest_run_per_job` 里。

**生产后果**：`main.py:520` 的注释说明 503 是给容器/systemd 健康检查用的 → 一个 7 月的、可选 provider 的数据问题会让健康检查**永久失败**，容器编排会**无限重启**，外部监控会**持续告警**。

### ⚠️ 4 条孤立的 `running` 记录从未回收

`event_discover_startup` 有 4 行 `finished_at = NULL`，全部在 2026-09-10：

```
running  2026-09-10T13:42:13  -> None
running  2026-09-10T19:45:29  -> None
running  2026-09-10T19:49:36  -> None
running  2026-09-10T19:51:22  -> None
```

根因：`_job_event_discover_startup` 在校验开始即 `_start_run(...)` 落一行，进程被强杀时不会收尾。

> 🔴 **本节两段结论均已推翻 —— 见文末「更正记录」。** 正确结论：
> (1) `/api/health` 报 503 **是刻意设计且已文档化的修复**，不是缺陷；
> (2) 遗留 `running` 行**有对账机制且已接线**（`scheduler.py:471`），之所以没回收是因为**我在审计时把调度器关了**。

---

## TOP 3 优先修复（**已按二次核实更正**）

1. **配置 LLM 凭据（部署就绪项，非代码修复）** —— 本机 `.env` 密钥已按安全要求清空，属预期；但**生产部署前必须**填 `OPENAI_API_KEY_N` + `OPENAI_BASE_URL_N`，并**把 `LLM_STARTUP_CHECK_ENABLED=true` 写进 `.env.production.example`**（当前模板没有该项 → 生产默认 False → 坏 key 会静默降级而不是拒绝启动）。
2. **清掉决策链里的标度异常** —— 作废/重算 `0779bde4dcd63e08`，并给 market_probability 加标度合理性守卫。附带发现：`kalshi_sports_source.py` 与 `kalshi_event_source.py` 对同一数据源用了**两种标度**（0–1 vs 0–100），需一并确认下游是否有混用。
3. **补齐生产可观测性** —— `.env.production.example` 里 `SENTRY_DSN` 与 `SCHEDULER_FAILURE_ALERT_ENABLED` 均为空/false，等于**任务失败无人被通知**；`LLM_DAILY_COST_CAP_USD=25` 已设（此项正常）。

**已撤销的首版 TOP 项**：~~解掉 `/api/health` 永久 503~~（非缺陷，见更正记录）。

**仍待确认（非缺陷，需业务判断）**：`review_queue_items=0` 是否符合人工复核流程预期；`docs/system-review-prompt.md` 清单过期（Manifold 已移除）。

---

## 更正记录（2026-09-26 二次核实）

首版报告有两处结论错误。原文保留在上方以便对照，此处记录更正依据。

### 更正 1：`/api/health` 的 503 — 不是缺陷，是刻意的修复

**首版主张**：陈旧失败（2026-07-07 的 `world_cup_api_football_validate`）永久锁死健康判定，生产会导致容器无限重启 + 监控疲劳；建议给 `failed_runs` 加时效窗口。

**核实推翻**：

- `app/memory/loop_run_store.py:166-190`（`latest_run_per_job` 的 docstring）明确写道：此前四个调用方各自硬编码任务名，导致 15 个任务里 12 个无人看管，**而"唯一一个最后运行失败的任务"正是 `world_cup_api_football_validate`（2026-07-07，"API-Football returned 0 fixtures"）**，于是 *"`/api/health` answered 200 'ok' and `scripts/healthcheck.py` went on feeding the dead-man switch"*。
- 并且写明："deriving from the ledger rather than from `scheduler.py`'s `add_job` ids is deliberate: what matters to an operator is which recorded work failed, whoever started it."
  → `world_cup_api_football_validate` 正是"request-triggered"任务（由 `events.py:795` 触发），台账推导就是为覆盖它。

⇒ **现在的 503 就是那次修复的产物。** 按首版建议加"时效窗口"，等于把这个修复回退，重新让 health 在确有失败任务时报 ok。

**"无限重启"的说法也不成立**：`scripts/healthcheck.py` 是 **systemd timer 驱动的 oneshot**（`deploy/prediction-market-reality-filter-healthcheck.{service,timer}`）。它失败时只 `return 1` 并**跳过 dead-man ping**（`healthcheck.py:63-69`），**不含任何 `Restart=`**。实际后果是外部 dead-man 监控**响一次警报**——这正是 dead-man switch 该有的行为。

### 更正 2：遗留 `running` 行 — 有回收机制且已接线

**首版主张**：4 条 `finished_at=NULL` 的行"启动时从不回收"。

**核实推翻**：`loop_run_store.fail_stale_running_rows()` **存在且已接线** —— `app/core/scheduler.py:471` 在 `loop_runs` 台账维护任务里调用，cutoff 为 `now - LOOP_RUN_STALE_RUNNING_HOURS`。2026-09-10 的行远超该阈值，**下次调度维护即会回收**。

之所以在本次审计中观察到 4 条未回收：**我在审计时用 `SCHEDULER_ENABLED=false` 启动**，维护任务根本没跑。这是我自己的观测条件造成的，不是系统缺陷。

> 该函数的 docstring 还解释了为何用"阈值"而非"启动时"判定：API 与 scheduler 是两个进程共享同一台账，"另一个进程刚启动"不能等同于"那行没有归属"。

### 更正 3：标度异常记录的归因

**首版主张**：该属"旧代码遗留"（现行为 `last_price_dollars × 100`，"修复后"才对）。

**核实推翻**：`git log -S "last_price_dollars"` 显示该转换来自 **`6cc99f3` 初始提交（2026-06-16）**，**从来就是 ×100**。所以那条 `first_seen 2026-07-01` 的记录是在正确代码已就位之后写下的，**"旧代码遗留"的解释站不住**。

**目前能确证的**：症状真实（该记录基线 0.2 驱动出虚假 `provisional_act`），且 `_clamp(market_probability, 0, 100)` **不拦截 0–1 标度**；另发现 `kalshi_sports_source.py:62-74` 用 0–1 标度（`no_price = 1.0 - price`）而 `kalshi_event_source.py` 用 0–100，**同一数据源两条适配器标度不一致**。**注**：该记录是选举市场（非体育路径），因此**具体来源未能确证**——不排除 LLM 把市场价回显为分数（同记录里 `evidence_constrained_probability = 0.02` 也是 0–1 标度）。修法是加写入侧标度守卫，而非猜测来源。

### 教训

三条错误的共同形态：**看到一个异常值就推断成因，而没有先读那一处的 docstring 与调用链**。本仓库大量把"为什么这么设计"写在 docstring 里（且往往正是为了修掉我指控的那个问题）——
**在本仓库下结论前，先读 docstring 与调用点；只读数据会得出反向结论。**


---

*本报告为只读审计结果：未修改任何业务数据，未调用任何写端点。审计期间启动的后端已在结束时停止。*

---

# 六、修复批次二（A/B/C/D/E）

> **本节追加于正文之后，不改写正文。** 它**取代上一行的结束语**：本批次有代码改动，
> 正文与「更正记录」保持原样以便对照。三件套口径：改动清单、变异验证、回归结果。
>
> ⚠️ **本批次最重要的产出是三个"不做"**：A、B、E 在调查后各自被推翻前提，
> **没有为了交付而改动代码**。每一处都写明了推翻依据。

## A —— 结案：不是缺陷（零改动）

结论已在正文「更正 1」「更正 2」记录（`/api/health` 的 503 是刻意的台账推导设计；
孤立 `running` 行有 `fail_stale_running_rows()` 且已接线在 `scheduler.py:471`）。
本批次复核后**无残项**，因此**没有代码改动**——首版建议的"加时效窗口"会回退那次修复。

## B —— 结案：**没有安全的守卫可加，故不改代码**

### 证据（只读打开 `backend/v2_loop.db`，`mode=ro`）

全库统计：`predictions` 共 **171 行**。

| 口径 | 行数 |
|---|---|
| `market_probability ∈ (0,1)` | **1** |
| `market_probability ∈ [1,100]` | 170 |
| `market_probability > 100` | 0 |
| `market_probability = 0` | 0 |

→ **一次性个例，非系统性。** 该行（与镜像的 `simulated_trades` id=42）：

| 字段 | 值 |
|---|---|
| `event_id` | `0779bde4dcd63e08` |
| `contract_id` / `platform` | `KXBRUVSEAT-35` / Kalshi |
| 市场 | "Will Andrew Tate's party win a seat in the next UK election?" |
| `ai_probability` | 30.17 |
| `market_probability` | **0.2** |
| `raw_edge` / `adjusted_edge` | 29.97 / 14.98 |
| `decision` | `provisional_act` |
| `created_at` | 2026-07-01T10:17 |

`raw_edge = 30.17 − 0.2 = 29.97` 精确自洽 → 判定链**确实**用了那个 0.2。
按正确标度重算：`raw_edge = 10.17`，`adjusted_edge = (30.17−20)×0.5 = 5.09 < ACT 6.0` → 应为 **`watch`**。

### 为什么现行为产生不出它

`kalshi_event_source._baseline_and_quote()` 三个分支分别返回
`last*100` / `(bid+ask)/2*100` / `50.0` —— **全部 0–100**。
`git log -S "last_price_dollars"` 证明该 ×100 自初始提交 `6cc99f3`（2026-06-16）即在，
即这条 2026-07-01 的记录**写下时那条路径已经是对的**。

### 唯一能合法写入 0.2 的入口

`EventAnalysisRequest.baseline_probability = Field(default=50.0, ge=0.0, le=100.0)`
→ **`0.2` 是合法值**，经
`events.py:274 → analyze_event(baseline_probability=…) → event_intelligence_service.py:839 market_probability=baseline_probability`
原样落库。`ai_analysis_service.py:70` 只做 `_clamp(mp, 0, 100)`，**不拦 0–1 标度**。

**关于首版"同源不一致"的说法**：`kalshi_sports_source.py` 确实用 0–1
（`"price": price`、`no_price = 1.0 - price`），但它只喂体育/期货管线（`kernel_predictions.db`），
与本条 `v2_loop.db` 的**选举**记录**不同源**。所以"两条适配器标度不一致"是真的，
但**与这条记录无关** —— 首版把它列为"疑似同源"过于宽。

### 为什么**不加**守卫（这是结论，不是遗漏）

值本身无法自证标度：**`0.2` 在 0–100 标度下是一个合法的 0.2%**。
而请求体里**只有这一个概率字段**，没有任何同请求内的参照值可供交叉校验。
→ 任何"拒绝 `(0,1)`"的阈值都会**误杀真实存在的 0.2% 市场**。
可靠守卫只能来自写入侧的上游契约（谁提供了这个数），**不是在决策层猜标度**。

### 留给业务方的三个选项

| 选项 | 代价 | 我的保留意见 |
|---|---|---|
| i. 只作废/重算这一行 + 镜像 `simulated_trades` | 小 | **我不能替业务判定 20 就是对的**：7 月的 Kalshi 盘口已不可回溯，`20` 只是"同源 ×100"的推断 |
| ii. 收紧 `baseline_probability` 写入契约（接受 0–1 并 ×100，或显式 422） | 中 | 改动对外契约，需业务确认；且会挡住真实 0.2% |
| iii. **不动数据**，仅保留可复用探针 | 零 | 推荐。只读探针已留档（见下），下次出现同类行可立刻识别 |

可复用探针（已按仓库惯例落盘，**只读**）：`backend/scripts/report_probability_scale_outliers.py`
—— 用 SQLite `mode=ro` 打开 loop DB，打印全量分布与所有 `0 < market_probability < 1` 的行，
并给出"若按 ×100 修正则 edge 应为多少"。实测输出与上表一致（`min/max = 0.2 / 99.45`）。

## C —— 已修复：让「0」只表示一种意思

### 问题

`review_queue_items=0` **本身是预期的**：`REVIEW_QUEUE_ENABLED` 默认 `false`，且
`backend/.env`、`.env.staging.example`、`.env.production.example`、`deploy/docker-compose.yml`
**全都没有提到它**（只有 `backend/.env.example` 赋值）。

但它**不可读**——同一个 `items: []` 同时表示三种情况：

1. 队列确实已清空；
2. **探测器从未运行**（`REVIEW_QUEUE_ENABLED=false`）；
3. 读失败（`loop_status_service._review_queue_counts()` 在异常时**降级为 0**）。

看板空态在三种情况下都显示同一句「当前没有待复核条目。」

**决定性对照**：同一仓库的 `frontend/src/components/detail/decision-timeline-panel.tsx:111`
空态**是**写明开关的 ——
「暂无决策时间线数据。该事件可能在 `DECISION_TIMELINE_ENABLED` 关闭期间保存。」
→ 两个同类面板，一个自解释、一个不自解释。**这不是新发明，是把已有先例补齐。**

### 改动

| 文件 | 改动 |
|---|---|
| `backend/app/api/routes/review_queue.py` | `GET /review-queue` 响应多回 `"enabled": settings.REVIEW_QUEUE_ENABLED`（+ docstring 说明为何要带上） |
| `frontend/src/lib/api.ts` | `ReviewQueueListResponse.enabled?: boolean` —— **可选**，缺省视为"未知" |
| `frontend/src/components/review/review-queue-board.tsx` | 仅在 `enabled === false` 时改说「复核队列未启用（REVIEW_QUEUE_ENABLED=false）。探测器不会写入条目，因此这里是空的。」 |

**为什么前端字段可选、且只在严格 `=== false` 时才改文案**：老后端不带该字段时不能被误报成"已停用"；
既有测试的 mock 不带该字段，因此空态文案保持中性 → **既有测试一行不用改**。

`/review-queue/sla` 的 `enabled` 未加（该端点是队列读数，不是生产者状态）—— 已知的、有意留下的窄口。

### 附带发现（模板层，同一根因）

`.env.production.example` 里那句
`# Feature flags ON in production (the overlays are the product).` 只列了 **6 个**开关。
另外 **6 个**默认全 `false`、**任何部署文件都没提**，却各自关着一个操作员会看的界面：

| 开关 | 关掉后操作员看到什么 |
|---|---|
| `REVIEW_QUEUE_ENABLED` | `/review-queue` 永久为空（即本项） |
| `DECISION_TIMELINE_ENABLED` | 事件页决策时间线永久为空 |
| `CONCLUSION_CHALLENGE_ENABLED` + `EVENT_CHALLENGE_ENABLED` | 无 `conclusion_challenge` → 复核队列的 `conclusion_challenge_failed` 触发**永不发生**（即使队列开关是开的） |
| `SOURCE_TRUST_REGISTRY_ENABLED` | 来源信任用内置档位，注册表覆盖不生效 |
| `WORLD_CUP_CHALLENGE_ENABLED` | 世界杯结算挑战不跑 |

已加**注释块**逐条写明「关掉后什么样」。**故意写成注释而不是赋值**：overlay 以 `override=True` 加载，
`KEY=false` 会**压掉操作员在 base `.env` 里开的 `true`** —— 与同文件 SENTRY_DSN 那段记录的陷阱同类。

## D —— 已修复：生产模板开启 LLM 启动校验

`.env.production.example` 在 `LLM_DAILY_COST_CAP_USD=25` 之后新增：

```ini
LLM_STARTUP_CHECK_ENABLED=true
```

`LLM_STARTUP_CHECK_ENABLED` 默认 `false`；`main.py:213` 用它决定是否
`await validate_primary_llm_startup()`，失败则 `RuntimeError` 拒绝启动。
模板此前**没有这一项** → 生产部署带坏 key 会**静默降级**而不是拒绝启动。
注释里同时写明了代价（provider 启动瞬间抖动也会拒启动，这是有意的取舍）。

## E —— 结案：根目录不是"散落"，`.gitignore` 逐条列明了它们（零改动）

- 根目录**已跟踪**的顶层条目只有 **8 个文件 + 6 个目录**：`.dockerignore` `.gitattributes`
  `.gitignore` `.gitleaks.toml` `CHANGELOG.md` `LICENSE` `README.md` `start.bat` 与
  `.github/ backend/ deploy/ docs/ frontend/ relay-bridge/`。**这个表面是干净的。**
- 所有"散落"物（`HANDOFF.md`、10×`SESSION_MEMORY_*.md`、4×`sdd-*.diff`、`sdd-task-*.md`、
  `hive.yml`、`check_db.py`、`skills-lock.json`、`code-review-*/`、`.fix-backup/`）
  在 `.gitignore` 里**逐条有名有姓**，分列于
  「Internal working logs」「AI tooling / local agent state (not product code)」
  「Local debug / diff scratch (not source)」三节 → 是**刻意的本地文件**，不是漏归置。
- 全仓检索：**没有任何已跟踪的代码或运维文档依赖它们**。唯一引用出现在
  `docs/superpowers/plans/*` 的**历史计划**里，而那些还明写
  「Do not commit `SESSION_MEMORY_2026-07-08.md`; it is ignored by `.gitignore`」。
- **确实过期的**（3 个月、已被取代）：4 个 `sdd-*.diff`、2 个 `sdd-task-4-*.md`、`check_db.py`、
  `hive.yml`、`code-review-2026-06-24/`、`.fix-backup/`；10 个 `SESSION_MEMORY_*.md`
  已被 `.workbuddy/memory/` 取代。
- **本批次没有移动任何文件**，理由有二：① 这是用户自己的本地草稿；
  ② 把它们移进 `docs/` 会让文件**变成新跟踪文件**，与 `.gitignore` 里
  "kept locally, not published" 的意图相反。归档命令已交给用户，动作留给他决定。

---

# 七、批次二的验证

## 回归

| 命令 | 结果 |
|---|---|
| `pytest tests/test_review_queue_endpoint.py test_review_queue_store.py test_review_queue_detectors.py test_env_overlay_examples.py test_production_deploy_consistency.py` | **190 passed / 0 failed / 0 error** |
| `pytest tests/test_env_overlay_examples.py`（收紧断言后） | 9 passed + 10 subtests |
| 生产模板组（`test_env_overlay_examples` + `test_production_deploy_consistency` + `test_alert_channel_posture` + `test_backup_plaintext_guard` + `test_log_level_setting`） | **93 passed / 0 failed** |
| `npx vitest run`（全量） | **125 files / 730 tests 全通过**（原 729，+1） |
| `npx tsc --noEmit` | exit 0 |
| 行尾审计（`backend/scripts/eol_audit.py`） | 本次触及的 15 个已跟踪文件**无 CRLF 损坏**（判据：无"CRLF→bare LF"，无混用） |
| `pytest tests/`（后端全量，**本机代理 env 在场**） | 7235 tests / **1 failure** / 0 error / 11 skipped —— 那 1 项是**本机代理**造成的，见下 |
| `pytest tests/`（后端全量，`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY`） | **7251 tests / 0 failure / 0 error / 11 skipped** |
| `pytest tests/test_backup_restore_drill.py`（去掉代理 env 后） | **0 failure** |
| `ruff check app/`（CI 原样） / `compileall -q app tests` | All checks passed / exit 0 |
| `npx eslint`（改动的 3 个前端文件） | exit 0 |

> ⚠️ 这两次全量运行的**进程退出码都是 1**，但第二次的 junit 是 `failures=0 errors=0`。
> 真因是本机拦截器：日志末尾只有
> `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":50,...,"targets":[...pytest-of-Alin\garbage-...]}`，
> 即 safe-delete 拦住了 **pytest 自己的临时目录清理**。
> **判绿只能看 junit 的 `failures`/`errors` 集合，`rc` 不是判据。**

### 关于后端全量那唯一 1 项失败：是环境，不是改动

失败项：`tests.test_backup_restore_drill.BackupRestoreDrillTests::test_a_real_archive_restores_every_store_to_its_configured_path`

```
AssertionError: Lists differ: ['PMRF service appears to be running (SQLite DB is locked). ...'] != []
```

**根因链（已复现，非推断）**：

1. `scripts/restore_stores.py:275-303` 在 Windows 上（无 `fcntl`）走**健康探测**分支，
   默认 URL `http://localhost:8000/api/health`；
2. 该分支的注释明说：**任何** HTTP 响应——**包括 503/502/504**——都算"服务在跑"，
   只有连接级失败（refused/reset/timeout）才算"没跑"；
3. 本机 `http_proxy = http://127.0.0.1:64584` 会**拦截**对 `localhost:8000` 的请求并回 **502**。
   直接复现（pytest 之外）：

   ```
   $ python -c "urllib.request.urlopen('http://localhost:8000/api/health')"
   http_proxy= http://127.0.0.1:64584
   RESULT: HTTPError 502 -> _check_service_running() would return True
   ```

4. 于是脚本认为"服务在跑"并**追加一条 warning**，测试断言 `warnings == []` 失败；
5. **决定性对照**：同一文件在去掉代理 env 后重跑 → **failures 1 → 0**；
   整仓全量在去掉代理 env 后重跑 → **failures 0**（7251 passed）。

**与本批次改动的关联：没有。** 本批次没有触碰 `restore_stores.py`、端口、DB 或备份链路。
（同理，审计期间 `curl` 也必须带 `--noproxy '*'` 才能访问本机端口。）

## 变异验证（`backend/scripts/mutation_verify_review_queue_flag.py`，字节级，已随交付落盘）

三次操作：`backup` → `apply` → 跑守卫测试 → `revert`。复跑记录：`tests=43 failures=5`，
四条守卫**全部命中**（`failures` 比 `RED` 名单多 1，是 `subTest` 的两次展开）。

| 回退的改动 | 期望变红的测试 | 实测 |
|---|---|---|
| 删掉路由的 `"enabled": settings.REVIEW_QUEUE_ENABLED,` | 精确契约测试 + 开关回显测试 | **RED**（43 tests / 4 failures，全部命中预期用例） |
| 删掉模板的 `# REVIEW_QUEUE_ENABLED=true` 行 | 「模板必须命名它关掉的开关」 | **RED**（`AssertionError: ['REVIEW_QUEUE_ENABLED'] != []`） |
| 把 `# WORLD_CUP_CHALLENGE_ENABLED=true` 改为赋值 `=false` | 「不得把开关钉死为 false」 | **RED** |
| 看板空态改回旧文案 | 新增的 vitest 用例 | **RED**（1 failed / 18 passed） |

全部还原后与备份 **sha256 一致**（`review_queue.py` `a189d0f3…`，`.env.production.example` `6c9342b5…`）。

## 一个被变异验证抓出来的自欺（值得单列）

模板「命名」测试**首版用裸子串匹配**（`k not in text`）。删掉 `# REVIEW_QUEUE_ENABLED=true` 那行之后，
**测试仍然是绿的** —— 因为同一段注释散文里还有一句
"…so the review queue's `conclusion_challenge_failed` trigger can never fire even with REVIEW_QUEUE_ENABLED=true."
一个字符串出现在了散文里，就满足了"出现过"的断言。**是变异验证把它抓出来的，不是代码评审。**

→ 已改为要求 `KEY=` **形状**（`test_env_overlay_examples._names_in_any_form()`）。
**教训：断言"某名字出现过"的测试可以被散文满足；要锁行为就必须锁形状。**

---

# 八、决策批次三：七个待决项，逐条决定并执行

批次二留下七个「需要业务方或用户拍板」的事项。本批次不再逐项征询，**由我按最优方式决定并执行**，
并在此逐条记录决定、依据与落地物。**其中第 5 项推翻了我自己在批次二里给出的判断。**

| # | 事项 | 决定 | 落地 |
|---|------|------|------|
| 1 | E1 根目录过期草稿归档 | **不移动** | 零改动（见 §8.1） |
| 2 | P `docs/system-review-prompt.md` 陈旧 | **修复** | 2 处事实错误（见 §8.2） |
| 3 | D1 生产模板未命名 LLM 凭据 | **修复** | 模板 +27 行（见 §8.3） |
| 4 | B 标度错位记录 | **不改数据、不加守卫，补标度契约** | 2 处注释（见 §8.4） |
| 5 | C1 `GET /review-queue/sla` 回显 `enabled` | **加**（**推翻批次二的判断**） | 路由 + 测试 + 文档（见 §8.5） |
| 6 | C2 `loop_status_service._review_queue_counts()` | **不加** | 零改动（见 §8.6） |
| 7 | C1b CLI `sla` / `list` 命名开关 | **加提示，不改退出码**（决定 5 时新发现） | CLI + 3 测试 + RUNBOOK（见 §8.7） |

## 8.1 E1 —— 经核实无需归置，零改动

核实（只读）：

```
git ls-files | awk -F/ 'NF==1'        → 恰好 8 个：.dockerignore .gitattributes .gitignore
                                        .gitleaks.toml CHANGELOG.md LICENSE README.md start.bat
git ls-files | awk -F/ 'NF>1{print $1}' → 恰好 6 个：.github/ backend/ deploy/ docs/ frontend/ relay-bridge/
git status --porcelain 过滤出「未跟踪且未忽略」的根目录文件 → 空
```

批次二称作「散落文件」的那些路径，**全部被 `.gitignore` 逐字点名**，分属三类：
`内部工作日志`（`backend/docs/PROJECT_PROGRESS.md`）、`AI 工具/本地代理状态`（`HANDOFF.md`、`SESSION_MEMORY_*.md`、
`hive.yml`、`skills-lock.json`、`.claude/`、`AGENTS.md`）、`本地调试/差异草稿`（`sdd-*.diff`、`sdd-task-*.md`、
`.fix-backup/`、`check_db.py`、`code-review-*/`）。

**不移动的理由**：① 移进 `docs/` 会让它们变成**新的受控文件**，与 `.gitignore` 那句"留在本地、不发布"的意图
正相反；② `HANDOFF.md` 是随会话滚动的**现行约定文件**（见 `.workbuddy/memory/MEMORY.md`），移动会破坏工作流；
③ 移动的收益是目录观感，风险是文件身份变化，不划算。

**本批次自己造成并已清除的污染**：根目录 `.tmp_*.xml` × 8 与 `.tmp_manifest.txt`（junit 与哈希清单的落盘物）。
这些**不被 `.gitignore` 覆盖**，会出现在 `git status` 里 —— 属本轮自查发现，已用 Python `unlink()` 逐个删除。
清除后根目录「未跟踪且未忽略」文件为 **0**。

## 8.2 P —— 审计清单指向一个已退役的数据源

`docs/system-review-prompt.md` 是**已受控、已发布**的审计清单。它两处写着 Manifold：

| 行 | 原文 | 问题 |
|----|------|------|
| §1 第 6 行 | 检查 Polymarket / Kalshi / **Manifold** 是否可达 | 会让下一次审计去探测一个不存在的源 |
| §7 第 42 行 | SOURCE_WEIGHTS 是否平衡（Polymarket 3.0 / Kalshi 1.0 / **Manifold 0.3**） | 数字本身也已过期 |

证据（代码自己写明了退役）：

```
app/core/config.py:556  # Legacy Manifold settings are kept only so existing .env files do not break
                        # startup. Manifold is no longer an active discovery or auto-resolution source.
```

且 `SOURCE_WEIGHTS` 已无 `Manifold` 键；`grep` 全仓 `manifold_event_source|fetch_manifold` 的调用方为 **0**。

现行活跃源以代码为准（`event_intelligence_service.py` 组装 `candidate_sources` 处）：

| 源 | 门控 |
|----|------|
| Polymarket / Kalshi | 常开 |
| Limitless | `LIMITLESS_SOURCE_ENABLED` |
| Opinion | `OPINION_SOURCE_ENABLED` **且** `OPINION_API_KEY` |
| Predict.fun | `PREDICT_FUN_SOURCE_ENABLED` **且** `PREDICT_FUN_API_KEY` |
| Polymarket Crypto | `POLYMARKET_CRYPTO_FETCH_ENABLED` |
| World Cup | `WORLD_CUP_SOURCE_ENABLED` |
| Metaculus | `METACULUS_API_TOKEN` |
| Open Web | 独立路径，`OPEN_WEB_ENABLED` |

改法不只换名字。清单里新增了两条**给下一次审计的提醒**：
① 上述可选源报告 0 条候选时，「源坏了」和「开关没开」在面板上长得一样，**要先确认开关状态再下结论**——
这与本批次 §8.5/§8.7 要修的是同一个病（**0 不能让读者猜**）；
② 编号式 LLM 配置 `OPENAI_API_KEY_N` / `OPENAI_MODEL_N_M` / `OPENAI_BASE_URL_N` 是 `llm_gateway_service`
**直接 `os.getenv`** 读取的，**不经过 `settings`**，因此**不在生产 preflight 的检查范围内、也没有任何名字校验**，
名字写错只会静默少一条路由 —— 核对时必须逐字比对。

## 8.3 D1 —— 模板命名了模型，却一个凭据都没命名

`.env.production.example` 原先只有 `OPENAI_MODEL=deepseek-chat` 一行。读者很容易据此认为"LLM 已配好"，
而模板里**没有任何一个 API key 变量名**。

我实测了「照抄模板直接部署」这条路径会撞上什么（探针 `pmrf_probe_llm_creds.py`，把环境里的
`OPENAI_*` 全部清空后重放模板字面默认值）：

```
ROUTES: [('legacy_openai', ['deepseek-chat'])]
HAS_CONFIGURED_ROUTE: False
RESULT: RuntimeError -> Primary LLM startup check failed: all_routes_failed: missing_api_key
```

两个结论：

1. **失败字符串是 `all_routes_failed: missing_api_key`** —— 能看出"缺 key"，但**不告诉你变量名**。
   批次二刚把 `LLM_STARTUP_CHECK_ENABLED=true` 写进模板，于是这个字符串就是照抄模板的部署**一定**会看到的东西。
2. **「有路由」不等于「配了 LLM」**：`build_route()` 会回退到 legacy 模型名，而 `OPENAI_MODEL` 在
   `config.py:191` **有非空默认值**，所以零凭据时它**仍然返回一条路由**；说 false 的是另一个函数
   `has_configured_llm_route()`。两个函数给出相反答案，是运维排查时的陷阱。

→ 模板新增 `# --- LLM credentials ---` 块（27 行），**全部注释**（理由同 C/D 两处：覆盖层 `override=True`，
写空值会盖掉基座 `.env`）：写明了失败字符串、两种配置形式与解析顺序
（task route → `LLM_ROUTE_DEFAULT` → 编号式 → legacy）、"编号式走 `os.getenv` 所以无校验"、
以及"有路由≠配置了 LLM"。

**一个**探针自查：首版探针的注释写着"cwd 在仓库外所以找不到 .env"，实测**错**——python-dotenv 的
`find_dotenv()` 是从**调用方文件**向上找，`import app.core.config` 就足以加载 `backend/.env`。
首版因此**并非**无凭据环境（清空前 ROUTES 恰好也是 legacy，结论对得侥幸）。已改为**显式清空 `os.environ`** 后重跑，
输出同上 —— 结论不变，但现在是**验过**的，不是**碰巧**的。

## 8.4 B —— 不改数据、不加守卫，只补标度契约

批次二已给出只读证据：`predictions` 171 行，`market_probability ∈ (0,1)` **恰好 1 行**（`0.2`），
160 余行落在 [1,100]，`min/max = 0.2 / 99.45`。那行是 `KXBRUVSEAT-35`（Kalshi），
`ai=30.17 market=0.2 raw_edge=29.97`，镜像到 `simulated_trades` id=42。

本批次**复核了"能不能加守卫"**，直接读入口契约（`app/models/event.py:6-13`）：

```python
class EventAnalysisRequest(BaseModel):
    event_question: str = Field(min_length=1, max_length=2000)
    baseline_probability: float = Field(default=50.0, ge=0.0, le=100.0)   # ← 调用方自报
```

`POST /api/events/analyze`（`events.py:269-281`）把 `baseline_probability` **原样**透传给 `analyze_event_question`，
**不取任何行情报价**。所以：

- 请求体里**只有一个概率字段**，**没有第二个事实**可以交叉核对；
- `0.2` 在 0–100 标度下是**合法的 0.2%**；
- 若它本是"20%"被误写成 0.2，**我们无从得知**。

→ **任何 `(0,1)` 阈值都是在猜调用方的意图，而不是在查事实**，还会误杀真实的 0.2% 市场。**不加守卫。**
→ **数据也不改**：那行的"真值"只能靠"同源 ×100"**推断**（7 月的 Kalshi 盘口不可恢复）。按推断改写历史，
会让审计轨迹不再反映系统当时**实际算出**的值 —— 这比留一行可疑数据更糟。三个处置选项仍留给业务方（见 §六/B）。
→ **做了唯一可做的那半**：在入口模型与传参点（`event_intelligence_service.py:837`）各补一段标度契约
（0–100 百分点、非 0–1 小数），并写明本次误用的证据，**说清为什么这里只能记文档、不能加校验**。

## 8.5 C1 —— 我推翻了批次二自己的判断

批次二我写的是：

> `/review-queue/sla` 的 `enabled` 未加（**该端点是队列读数，不是生产者状态**）—— 已知的、有意留下的窄口。

**这个理由站不住。** 本批次查到两条反证：

1. `docs/ops/RUNBOOK.md:323` 把 `GET /api/review-queue/sla` 列为运维接口，`:352` 说明它与
   `scripts.review_queue_cli sla` 是**同一个聚合**；而后者在 RUNBOOK 里的定位是
   **"exits 1 when anything has breached, so it can be run as a check rather than read by eye"**
   —— 它是**给机器读的**。
2. `frontend/src/lib/api.ts:1243` 确实在消费它。

于是"队列读数 vs 生产者状态"这个区分**不成立**：`pending_total` 与 `breached_total` 在
「队列排空」和「生产者关闭」两种状态下**都是 0、且序列化结果完全相同**，
一个 `pending_total == 0` 的阈值比较会对着**一个根本没在跑的队列**长期保持绿色。
**歧义的 0 出现在机器判据上，比出现在人读的列表上更危险，而不是更安全。**

→ 已加 `"enabled": settings.REVIEW_QUEUE_ENABLED`（与列表端点同形），更新 docstring 说明它保护的是机器读者，
新增测试 `test_sla_reports_whether_the_producer_flag_is_on`（两个取值都钉住，硬编码 `false` 会红），
`api.ts` 的 `sla()` 返回类型补 `enabled?: boolean` 并注明与列表端点同因。

## 8.6 C2 —— 不加，理由与 C1 相反却不矛盾

`loop_status_service._review_queue_counts()` 在 DB 读失败时降级为全 0（`loop_status_service.py:97-119`）。
**不加 `enabled`/`degraded`**，依据：

- **判据边界是自己立的、并且经 C1 复核后仍然成立**：只有当**同一仓库里存在"在某界面上命名该开关"的既有先例**
  时才去消歧。`decision-timeline-panel.tsx:111` 是先例，`/sla` 因此可依（§8.5）；`/api/status` 的 `counts`
  **没有**这样的先例。
- `/api/status` 是按名读取的**已发布载荷**（`pending_reviews`/`breached_reviews` 有注释说明"因看板按名读取而保留"），
  为一个**纯诊断**歧义扩键，改的是发布契约。
- 该降级路径**已经会 `logger.warning(..., exc_info=True)`**，失败并非无声；要如实再报一个 `degraded` 标志，
  就得先区分"读失败"与"真空"，那是对一条**有意 fail-open** 的路径做重构（其 docstring 明确写了
  "a status endpoint that raises ... is worse than one reporting an empty queue"），成本大于收益。

## 8.7 C1b —— 决定 C1 时新发现的假绿：CLI 的 SLA 检查

`scripts/review_queue_cli.py` 的 `sla` 子命令在开关关闭时输出
`[OK] pending=0 oldest=n/a breached=0` 并 `return 0` —— 与"队列干净"**逐字相同**。
而 RUNBOOK 把它推荐为**巡检检查**。也就是说：**默认配置下（无人命名 `REVIEW_QUEUE_ENABLED`），
一个被当作检查用的命令会对一个从未运行的队列永远 exit 0。**

→ 新增 `_flag_note()`，在 `sla` 与 `list` 的输出里（开关关闭时）补一行
`[WARN] REVIEW_QUEUE_ENABLED=false: ... A zero below means 'producer disabled', not 'queue drained'.`

→ **退出码故意不改**（并在测试里钉住）：RUNBOOK 写明 `1` 的含义是"有东西超期了"，
拿来表示"生产者关闭"会让一个**被有意关闭**的队列在巡检里报故障。RUNBOOK 另加一段说明这个取舍，
并指出"若你的检查要把生产者关闭当失败，请读端点的 `enabled` 而不是退出码"。

## 8.8 本批次不改健康分

维持 **52/100**。本批次修的是**文档准确性与 0 的歧义**，没有触及拉低分数的那些结论
（引擎 CLV 为负、扫描器误报、决策阈值分布、校准样本不足）。**不为了让批次看起来有产出而调分。**

---

# 九、批次三的验证

## 回归（全部在本机、**剥掉代理变量** —— 见批次二的代理→502 误报）

| 项目 | 命令 | 结果 |
|------|------|------|
| 后端全量 | `pytest tests/ --junitxml=…` | **7257 tests / 0 failures / 0 errors / 11 skipped** |
| 收集数独立复核 | `pytest tests/ --collect-only` | **6368 collected**（= 6357 passed + 11 skipped，**自洽**） |
| 前端全量 | `npx vitest run` | **125 files / 730 tests** 全通过 |
| 前端类型 | `npx tsc --noEmit` | rc=0 |
| 后端 lint | `ruff check app/` | All checks passed |
| 后端编译 | `compileall -q app tests scripts` | rc=0 |
| 行尾 | `scripts/eol_audit.py` | **line-ending damage: none** |

**7257 的解释**（差 6 个不是回归）：批次二同一口径为 7251；本批次新增 4 个测试，其中
`test_sla_reports_whether_the_producer_flag_is_on` 有 2 个 `subTest`，junit 记 3 个 testcase →
新增 **6** 个 junit testcase。`7251 + 6 = 7257`，**逐项对齐**。

**「无静默回退」的证明**：长时任务前对 35 个改动文件取 sha256 落盘；
全量套件跑完后逐条复核 → **34 个逐字节未变**，**恰好 1 个变化**（`backend/app/models/event.py`），
即我**刻意**在快照之后编辑的那个文件。这排除了机器上出现过的"长时后台任务期间被就地编辑的文件被回退到基线"
（见 `.workbuddy/memory/MEMORY.md`）。清单与 9 个根目录临时文件已在核验后删除。

## 变异验证（`scripts/mutation_verify_review_queue_flag.py`，本轮扩到 6 个）

本批次新增 3 个变异。**每个变异：应用 → 跑守护测试 → 还原；还原后 sha256 与备份逐字节一致。**

| # | 变异 | 期望的 RED | 实测 |
|---|------|-----------|------|
| 4 | 从 `/sla` 返回值删掉 `enabled` | `test_sla_reports_whether_the_producer_flag_is_on` | ✅ 1 RED |
| 5 | 把 `_flag_note()` 的开关判断删掉（提示变**无条件**） | `test_sla_says_nothing_extra_when_the_producer_is_on` | ✅ 1 RED |
| 6 | 把 `_flag_note()` 改成恒返回 `None`（提示**不可达**） | `test_sla_names_…` + `test_list_names_…` | ✅ 2 RED |

变异 5 与 6 是**同一段字节的两个相反改写**，因此互斥；为此给 `apply` 加了可选的**单点索引**
（`apply 5` / `apply 6`），否则第二个会因"期望 1 处匹配、实际 0 处"而中止。
**变异 5 是必要的**：没有它，"每次运行都打印提示"的实现能通过其余**全部** CLI 断言。

## 本轮自查抓到的两个问题

1. **探针自身说谎**（§8.3）："cwd 在仓库外"并不等于"没有 .env"。是**回读探针自己打印的 `cwd`/`env file found` 与
   意外出现的 `OPENAI_API_KEY_1..4`** 才发现的 —— 结论对得侥幸，已改成显式清空后重跑。
2. **我上一批发布的判断是错的**（§8.5）：`/review-queue/sla` 那个"有意留下的窄口"。
   批次的**测试全绿**并不保护这个判断 —— 它是**判断**，不是断言。**绿只说明代码与既有断言一致，
   不说明结论正确。**

## 遗留项（批次三留下的，已于批次四处置，见 §十）

1. ~~**`scripts/report_probability_scale_outliers.py` 无测试守护**~~ → **批次四已补**（§十）。
2. **CLI `sla` 的退出码语义**（§8.7）。我选了"打印提示、不改退出码"。若业务方认为"生产者关闭"应当让巡检失败，
   那是一次**契约变更**（会让现有巡检开始报警），需要单独决定，我没有替业务方做。→ **维持现状**（见 §十.1）。
3. **模板里的编号式模型名仍是示例值**（`# OPENAI_MODEL_1_1=deepseek-chat`）。它与基座 `.env.example` 一致，
   但真实部署该填什么取决于用哪家网关，未替你假设。→ **维持现状**（见 §十.1）。
4. **§8.4 那行数据的三个处置选项**（作废 / 重算 / 保留）仍待业务方选择。
   → 我按"保留数据 + 保留探针"落地，并使探针**可被信任**（§十）。

---

# 十、批次四：把「保留探针」这条建议坐实

批次三我给的四个处置里，2 和 3 的建议**就是"维持现状"**（打印不改退出码 / 编号式模型名保持示例值），
已经是落地状态，无需改动。4 的建议是"保留数据 + 保留探针"。**唯一真正待做的是第 1 项：给探针补测试**——
而它恰好是第 4 项成立的前提：一个没人守护的探针，静默失效时不会有人知道，
"保留探针"就退化成"我们以为有个探针"。

## 10.1 探针此前从未用**已知答案**的输入验证过

`report_probability_scale_outliers.py` 是批次二写的常驻探针，当时**只对生产库跑过一次**。
也就是说它的 6 条桶查询、过滤边界、修正算术**从未在答案已知的输入上被检查**——
它报出的"171 行 / 1 条 suspect / min-max 0.2 / 99.45"是**它在说**，不是**我验过**。

## 10.2 新增 `tests/test_report_probability_scale_outliers.py`（16 个用例）

用**真实 schema**（`preds._ensure_schema` / `trade_store._ensure_schema` / `link_store._ensure_schema`）
建临时库，而不是手搓一份建表语句——测的是它会真正遇到的表。锁住的东西：

| 组 | 锁住什么 |
|----|---------|
| 分桶 | 5 个桶互斥且完备；`mp>100` **单独成桶**（`1<=mp<=100` 必须有上界，否则 105 会躲在"健康"计数里） |
| 边界 | `mp=0` 与 `mp=1.0` **都不算** suspect（过滤是严格 `> 0` / `< 1`）；min/max 覆盖全部行 |
| 修正算术 | 审计那行的真实数字：`ai=30.17 market=0.2` → `if the market value were 20.0: raw_edge=10.17` |
| 只读承诺 | `mode=ro` 下 INSERT 必须报 readonly；整脚本跑完前后，库文件**字节相同**且行内容相同 |
| `--event-id` | 三张表都覆盖；无行的表必须明说 `(no row)`；超 200 字符截断；不带 flag 时不输出 |

## 10.3 补测试时发现的两件事

**① 两个"防御性守卫"里，一个可达、一个不可达**，而两者看着一模一样：

| 守卫 | 判定 | 依据 |
|------|------|------|
| `_suspects()` 里的 `if isinstance(ai, (int, float)):` | **可达** | SQLite 只做类型**亲和**不做强制：`'abc'` 存进 `REAL NOT NULL` 列会**原样存为 TEXT**（实测 `typeof='text'`），读回来是 `str`，守卫触发 |
| `_distribution()` 里的 `market_probability NULL` 桶 | **恒为 0** | `prediction_store._SCHEMA` 里该列是 `REAL NOT NULL`，NULL 插不进去 |

首版测试我写的是"把 `ai_probability` 置为 NULL"，跑出来是 `IntegrityError` —— **是我的假设错了，不是代码错了**。
改成存 `'abc'` 后守卫**确实可达**，测试才有意义。不可达的那个桶则被显式断言为 0，
**并配一条会抛 `IntegrityError` 的 INSERT 作为"它不可能非 0"的证明** —— 而不是把它当活信号展示。

**② 边界测试最初是"空头"的。** 见 10.5。

## 10.4 变异验证：新增 `scripts/mutation_verify_probability_probe.py`（15 个变异）

**每个变异：应用 → 跑守护测试 → 还原；还原后 sha256 与备份逐字节一致**
（`report_probability_scale_outliers.py` 15 轮 apply/revert 后仍为 `aeae0d6c…`）。

| # | 变异 | 实测 RED |
|---|------|---------|
| P1 | 桶查询下界 `> 0` → `>= 0` | 1（分桶测试） |
| P2 | `1<=mp<=100` 去掉上界 | 2（分桶 + 上界单测） |
| P3 | 修正值不再 `×100` | 1 |
| P4 | `mode=ro` → `mode=rw` | 1（**只**红了只读单测，见 10.6） |
| P5 | 删掉 `isinstance` 守卫 | 1（失败信息即 `TypeError: unsupported operand type(s) for -: 'str' and 'float'`） |
| P6 | 关掉 200 字符截断 | 1 |
| P7 | **列表**查询下界 `> 0` → `>= 0` | 1 |
| P8 | **列表**查询上界 `< 1` → `<= 1` | 1 |
| P9 | `MAX` → `MIN` | 1 |
| P10 | NULL 桶查 `IS NOT NULL` | 1 |
| P11 | 删掉"none"提示 | 1 |
| P12 | `--event-id` 只遍历 1 张表 | 2 |
| P13 | 删掉 `(no row)` | 1 |
| P14 | `--event-id` 无条件执行 | 1 |
| P15 | 缺失 DB 守卫恒不触发 | 1 |

**15 个变异覆盖 16 个用例中的 15 个**，唯一未覆盖的在 10.6 说明。

## 10.5 是变异验证（再次）抓出了"空头断言"

- 只有 P1 时，`test_a_zero_market_probability_is_not_a_suspect` **仍然是绿的** ——
  因为过滤条件在源码里**写了两遍**（分桶查询一份、列表查询一份），P1 只碰了分桶那份，
  而该测试读的是列表。→ **补 P7**。
- 补了 P7 之后，`test_a_market_probability_of_exactly_one_is_not_a_suspect` **仍然是绿的** ——
  P7 改的是**下界**，而 `mp=1.0` 是被**上界**排除的。→ **补 P8**。

也就是说：**两个"边界"用例写下来的时候看着都很像样，实际一个都没锁住**，
是逐个变异试出来的。这与批次二那次"裸子串匹配被散文满足"是同一个病，只是这次披着"边界测试"的皮。

## 10.6 一个**不**做单变异覆盖的用例，理由写明

`test_running_the_report_leaves_every_stored_value_alone`（比对库文件字节 + 行内容）**无单变异可红**。
这不是漏测，而是这条守卫的性质：只要 `mode=ro` 成立，它要检测的"写入"就**结构上不可能发生**，
只有**同时**去掉只读连接**并且**加一处写入（两步改动）才会触发。它是纵深防御，保留；
真正被 P4 锁住的是它兄弟用例里的 `mode=ro` 断言。**把这条写进 harness 文档串，而不是假装它有覆盖。**

## 10.7 我自己的 harness 文档串也被实测纠正了

首版文档串写"变异 4 会让**两个**只读测试变红"、"变异 1 会让 `test_a_zero…` 变红"。
**实测都不对**（各只红一个）。已按实测改写，并把"1 只管桶查询 / 7 才管列表"写进去。
**又一次印证：断言"会怎样"的文字必须拿输出核对，不能凭设计意图写。**

## 10.8 批次四的回归

| 项目 | 结果 |
|------|------|
| 后端全量 | **6373 passed / 11 skipped / 889 subtests passed**；junit `7273 tests / 0 failures / 0 errors / 11 skipped` |
| 收集数独立复核 | **6384 collected** = 6373 + 11（**自洽**）|
| 增量说明 | 批次三为 6357 passed / junit 7257；本轮 **+16**（新增 16 个用例）→ 6373 / 7273，**逐项对齐** |
| `tests/test_report_probability_scale_outliers.py` | 16 passed |
| lint / 编译 | `ruff`（3 个新文件）All checks passed；`compileall` rc=0 |
| 行尾 | 三个新文件均为 bare LF（工具创建、未被 git 检出过），**非损坏** |
| 无静默回退 | 长任务前快照 49 个改动文件 → 跑完**逐条复核：49 不变 / 0 变化 / 0 缺失** |

## 10.9 遗留（批次四仍未做）

- ~~**三个变异 harness 高度重复**（`mutation_verify_daily_digest.py` /
  `mutation_verify_review_queue_flag.py` / `mutation_verify_probability_probe.py`
  各有一套 backup/apply/revert）。可合并为一个接受 `--set` 的通用 runner。
  本轮**故意没做**：会改动两个已验证过的工具，收益是去重，风险是动坏在用的东西。**待你决定。**~~
  → **已完成**（老板指示「合并」）：三者合并为 `backend/scripts/mutation_verify.py`，
  三个旧脚本已删除，26 个变异全部校验通过。**明细见 §十一。**
- 上表 §8.4 那行数据（`KXBRUVSEAT-35` / `0.2`）的处置仍待业务方选择。
  ⚠️ **2026-09-27 追加**：该节的**归因已更正**（不是"旧适配器存量"，而是经 `/analyze` 的
  `baseline_probability` 传入 —— 见 §8.4 的更正块）。**新证据削弱而非支持「重算为 20」这个选项**，
  三个选项仍需业务方定，但依据变了。
- ~~`review_queue_items = 0` 是否符合人工复核流程预期（需业务判断）~~
  → **已结案**（2026-09-27）：成因是**生产者开关关闭**（`REVIEW_QUEUE_ENABLED` 未配置 → 默认 false），
  不是路由缺陷；且打开开关**不会回填存量**。**明细见 §十二。**

---

*本节为改动批次的交付记录；正文与「更正记录」保持原样不改写。*

---

# 十一、合并三个变异 harness（2026-09-26，老板指示「合并」）

## 11.1 做了什么

三个 harness 各自实现了一套 backup/apply/revert，其中 `mutation_verify_daily_digest.py`
还是**自驱动**的（自己跑 pytest 并断言「变异前绿 / 变异后红 / 还原后字节一致」），
但只覆盖 F1–F6、且**没有单点索引**；另两个是手动模式，有单点索引（`apply 5`）、
有 `group` 互斥概念，却**不做还原后复验**。合并保留**两者的并集**：

> 一份清单（`Mutation` / `MutationSet`）+ 一个引擎 + 三个 set，共 **26 个变异**。

产物：**`backend/scripts/mutation_verify.py`**（新建、未跟踪、bare LF）。

| set key | 变异数 | 覆盖对象 |
|---|---|---|
| `daily-digest` | 5 | `events.py` + `daily_digest_service.py`（原 F1–F6 那套）|
| `review-queue` | 6 | `review_queue.py` + `review_queue_cli.py` + `.env.production.example` |
| `probability-probe` | 15 | `report_probability_scale_outliers.py` |

子命令：`list` / `verify [keys…]` / `backup <key>` / `apply <key> [index]` / `revert <key>`。

**`verify` 从三阶段升为四阶段**（旧的自驱动脚本只做 1/2/4，且只对 `daily-digest`）：

1. 未改动的树上守卫**必须全绿**（证明 `-k` 选择器真的选中了东西，而非"没跑测试"）；
2. 应用变异后守卫**必须变红**（证明这条守卫是承重的）；
3. 还原后守卫**必须重新变绿**（证明还原的是"行为"，不只是"字节"）；
4. 还原后 sha256 与原字节**逐字节一致**（证明还原的是"字节"）。

`group` 用于**同一段字节的相反改写**（`C5`/`C6` 改 `_flag_note`；`P7`/`P8` 改列表查询的
`WHERE` 子句）：`verify` 逐条单跑不受影响；`apply <set>`（全量）会跳过同组后续变异并打印说明。

## 11.2 合并时抓到并修掉的一个真 bug（本轮自查）

新 runner 第一版对 **26 个变异全部报 `[BAD] stayed GREEN with the fix reverted`** ——
但每条的 tail 明明写着 `1 failed, 15 deselected` / `3 failed, 1 passed, 21 deselected`，
且 `bytes restored True`、`green after restore True`。**tail 与判定自相矛盾**，说明是判定错，不是变异失效。

根因：`_run_guards()` 的返回是**「选中的守卫是否全部通过」**（`proc.returncode == 0`，
docstring 也是这么写的），但 `_verify_one()` 把它的返回值直接赋给了名为 `red_after` 的变量 ——
**变量名与语义相反**。于是"变异生效 → pytest 失败 → 返回 `False`（未通过 = 已变红）"
被读成了"没变红"，`if not red_after` 成立 → 误报 BAD。

修复：`red_after = not passed_after`（并把局部变量改名为 `passed_after`，附注释说明为什么反转）。

> 教训：**这里没有"测试全绿所以是对的"可依赖** —— 是 tail 里那句 `1 failed` 与布尔量方向不符
> 暴露了问题。**「我验过」与「我以为」的区别，又一次体现在读原始输出上**，而不是读汇总。

## 11.3 校验结果（26/26）

`cd backend && python scripts/mutation_verify.py verify`，用时 **10 分 21 秒**：

| 项目 | 结果 |
|------|------|
| 总数 | **26** |
| `[OK ]` | **26** |
| `[BAD]` | **0** |
| 每条形态 | `green before True \| red after mutation True \| green after restore True \| bytes restored True` |
| 跑完后 6 个目标文件 | 与跑前快照**逐字节一致**（`d0654dd9…` / `4d6a0810…` / `0ae9bc43…` / `864d6061…` / `4d4bc62d…` / `aeae0d6c…`）|
| 静态检查 | `ruff` All checks passed；`py_compile` rc=0 |

## 11.4 旧 → 新 索引（正文提到的三个旧脚本名已删除，指向如下）

本报告正文（§"变异验证"、§10.4、§10.9）按「交付记录不改写」保留旧文件名。
**这三个文件已在本次合并中删除**，对应关系：

| 旧脚本（已删除） | 现在这样跑 | 变异数 |
|---|---|---|
| `scripts/mutation_verify_daily_digest.py` | `python scripts/mutation_verify.py verify daily-digest` | 5 |
| `scripts/mutation_verify_review_queue_flag.py` | `python scripts/mutation_verify.py verify review-queue` | 6 |
| `scripts/mutation_verify_probability_probe.py` | `python scripts/mutation_verify.py verify probability-probe` | 15 |

引用核查：`grep -rn mutation_verify` 的全部命中**只在文档里**（本报告、`daily-digest-review`）
与即将删除的三个脚本自身 —— **无 CI / 代码依赖**，删除是安全的。

## 11.5 未改动

- 本合并**不动**被测源码：26 个变异全部是在**临时应用→还原**的原子上跑的，跑完 6 个目标文件字节不变。
- `docs/reviews/daily-digest-review-2026-09-26.md` 的交付记录同样**不改写**，另加一段后记指向本节。

---

# 十二、结案：`review_queue_items = 0` 到底是什么（2026-09-27）

本节回答正文 §"人工复核队列与决策时间线均为空"与 §10.9 里标注「需业务判断」的那一条。
**结论：这个 0 是"生产者关闭"，不是"队列已清空"，也不是"路由坏了"。**

## 12.1 证据链（全部只读）

| 环节 | 事实 |
|---|---|
| 生产者 | **只有两处**，且都被 `settings.REVIEW_QUEUE_ENABLED` 门控：`event_intelligence_service.py:757`（`analyze_event()` 内，overlay 构建时按事件跑探测器）、`event_resolve_service.py:178`（结算路径）|
| 代码默认 | `config.py:1192` → `REVIEW_QUEUE_ENABLED: bool = _env_bool("REVIEW_QUEUE_ENABLED", "false")` |
| 实际配置 | `backend/.env` **没有这个键** → 落回默认 |
| **生效值** | **`False`**（实测：清空环境变量后 `import settings` 打印）|
| 存储 | loop DB（`backend/v2_loop.db`）里 `review_queue_items` **0 行**、`review_queue_audit` **0 行** |
| 部署文件 | `.env.example` 是 `=false`；`.env.production.example` **只写注释行**（`# REVIEW_QUEUE_ENABLED=true`，见 §8.3）；`docker-compose.yml` / `.env.staging.example` 完全不提 |

源码自己把这个姿态写在注释里（`event_intelligence_service.py:753-755`）：

> `When REVIEW_QUEUE_ENABLED=false (default), this block is a no-op — byte-identical to pre-Plan-4.`

**即"默认关闭"是 Plan 4 §6.2 的刻意设计，不是漏接线。**

## 12.2 这不是"缺陷"，所以本轮也不改它

- **不改 `backend/.env`**：那是**部署姿态**，不是代码缺陷。把它打开是运维决定，不是修 bug。
- **不改默认值**：改了等于推翻 Plan 4 §6.2 的"默认与 Plan 4 之前字节一致"。
- 本批次真正该做的已经做了 —— 给队列读数**带上 `enabled`**（§8.5/§8.7）。所以这个 0 现在在
  `/review-queue` 面板上会**明说自己为什么是空的**（前端 `enabled === false` 分支的文案），
  而在 CLI 上会打 `[WARN]`。**这条"需业务判断"的问题，答案其实是"配置姿态"，且已经可读。**

## 12.3 一个必须同时告诉运维的前提（否则"打开开关"会落空）

探测器**只在下面两个时刻**被调用，**没有任何回填路径**：

```
grep -rn "detect_review_candidates\|detect_auto_resolve_low_confidence" backend/app backend/scripts
→ 仅命中 event_intelligence_service.py:758/762 与 event_resolve_service.py:182/188（定义处除外）
```

没有回填脚本、没有重建端点、调度任务清单里没有队列相关 job（`scheduler.py` 的 22 个 `id=` 无一涉及）。
**所以把 `REVIEW_QUEUE_ENABLED` 置 true 之后，只有"此后被分析或被结算"的事件会产生复核项；
存量记录（含那 31 条 `provisional_act`）不会追溯进队列。** 若要队列立刻有内容，
除置 true 外还需**对存量事件重新触发一次分析**。

> 这一条属**运维操作含义**，不是本次审计的修复项。已同步进 `HANDOFF.md` 与
> `.workbuddy/memory/MEMORY.md`，避免下一次会话再把同一个 0 当成"路由坏了"重查一遍。

## 12.4 同批结案：`decision_timeline = 0` 同因

| 环节 | 事实 |
|---|---|
| 代码默认 | `config.py:1235` → `_env_bool("DECISION_TIMELINE_ENABLED", "false")` |
| **生效值** | **`False`**（实测）|
| 存储 | loop DB 里 `decision_timeline` **0 行** |
| 源码自述 | `decision_timeline_store.py:110`：「`settings.DECISION_TIMELINE_ENABLED` so the store **stays empty**」|
| 是否已可读 | ✅ **本来就已消歧** —— `decision-timeline-panel.tsx:111` 的空态**点名**了该开关：
「暂无决策时间线数据。该事件可能在 DECISION_TIMELINE_ENABLED 关闭期间保存。」|

**即：两条"为空"的读数同因（生产者默认关闭），且都不是缺陷。** 复核队列表原本是这三个 0 里
**唯一一个说不清**的（面板只说"当前没有待复核条目"），本批次给它补 `enabled` 回显正是对症 ——
而这个先例（时间线面板点名开关）也正是 §「0 必须只说一件事」判据的来源。

| 事项 | 状态 |
|---|---|
| `review_queue_items = 0` | ✅ 已结案（生产者关闭；本批次已让它可读）|
| `decision_timeline = 0` | ✅ 已结案（同上；面板**早已**可读）|


---

# 十三、把 §十二 的规律机械化扫一遍：还有哪些 0 说不清自己为什么是 0（2026-09-27）

§十二 处理了两条**同因**的空读数（生产者开关默认关闭）。两条不是巧合 —— 只要"开关关掉 →
某段载荷结构性地空"这个形状还在，同样的歧义就会在别处复现。所以本轮不问"还有没有别的 bug"，
只问一个机械问题：**改成"关"之后，界面上会不会出现一个不带任何解释的 0？**

## 13.1 扫描方法

1. 枚举 `config.py` 里所有 `_env_bool(..., "false")` 的开关（默认关闭者）→ **23 个**。
2. 对每个开关名，在 `frontend/src` 全量搜索 → 只有 **`REVIEW_QUEUE_ENABLED`（3 个文件）** 与
   **`DECISION_TIMELINE_ENABLED`（2 个文件）** 被前端点名，且二者**已在 §8.5/§12.4 消歧**。
3. 对**没被点名**的开关，问第二个问题：它门控的数据，在前端是否会**显示为 0**（而不是"缺席"）。

## 13.2 判据：`if (!x) return null` 是"缺席"，不是"假 0"

`ConclusionChallengePanel`（`conclusion-challenge-panel.tsx`）在无数据时 `if (!challenge) return null`
—— **整块不渲染**。这与"渲染一个 `样本数：0`"有本质区别：**缺席**不会让人把它读成一个测量值，
**假 0** 才会。**所以这类不改**（改了反而是往界面上贴无用的解释文本）。

## 13.3 真实发现：`quality-summary-panel.tsx` 有 4 处假 0

| 显示项 | 门控开关 |
|---|---|
| 事件计数 · 含决策质量 / 含市场质量 / 含LLM遥测（三行值）| `DECISION_QUALITY_ENABLED` / `MARKET_QUALITY_ENABLED` / `LLM_TELEMETRY_ENABLED` |
| 区块「市场质量」 | `MARKET_QUALITY_ENABLED` |
| 区块「LLM 遥测」 | `LLM_TELEMETRY_ENABLED` |
| 区块「来源可信度」 | `SOURCE_RELIABILITY_ENABLED` |

四段载荷在此之前**一律显示数字**。开关关闭时它们全 `0`，面板**没有任何文案**说明这是"层没开"
还是"层开着但没数据"。这正是 §十二 的形状，只是没被点名。

## 13.4 严重度取证：不是理论问题，staging 就是关的

| 部署文件 | 四个开关的取向 |
|---|---|
| `.env.production.example:25-29` | **四个全 `true`**（生产开）|
| `.env.staging.example:20-24` | **四个全 `false`**（staging 关）|
| `deploy/docker-compose.yml` | `env_file: ../backend/.env` → **overlay 会被加载** |

→ **staging 环境下这个面板的四个区块恒为 0，且无从解释**，而 staging 恰恰是有人会去看面板的地方。
**故判为真实缺陷，本轮修。**

## 13.5 修复（回带生产者开关，与既有三处先例同形）

1. **后端** `quality_metrics.py`：端点响应新增 `overlay_flags`（四个 bool），
   **与它解释的那些计数并列发布** —— 文档串写明"每个区块在'开关关'与'开着但无数据'两种情况下
   渲染成同样的 0"，并显式指向既有先例 `alerts_enabled`（`/quality-metrics/anomalies`）。
2. **契约** `lib/api.ts`：`overlay_flags` 为**可选**字段 —— **缺省 = "未知"，绝不当作 `false`**
   （否则老后端会被误报成"已停用"，且既有不带该字段的 mock 测试会连带破碎）。
3. **面板** `quality-summary-panel.tsx`：加 `flagOff()`（**只认显式 `false`**）+ `DisabledNote`；
   三个区块在开关关闭时显示 `该层未启用（<KEY>=false），这里没有可统计的数据。`；
   三行事件计数改为 `未启用`。

> **注**：`decision_quality` **没有独立区块**，它只门控「含决策质量」这一行 —— 所以它出的是行内
> `未启用`，**不是** `DisabledNote`。这一点在写测试时被实测纠正（见 §13.7）。

## 13.6 判据边界（本项目"0 必须只说一件事"的适用面）

- **只在同仓存在"在某界面命名该开关"的既有先例时才消歧。** 本轮四个开关的消歧，
  正是沿用 `alerts_enabled` / `decision-timeline-panel` / 队列 `enabled` 的先例。
- **不**给 `loop_status_service._review_queue_counts()` 加 `enabled`（批次三已定，见 §8.6）——
  理由不变：那是**按名读取的已发布载荷**，且前端**完全不显示**这两个字段。**先例判据不可当成"见 0 就加"。**
- ⚠️ **`source_reliability` 的门控是 `SOURCE_RELIABILITY_ENABLED`，不是 `SOURCE_TRUST_REGISTRY_ENABLED`。**
  后者只在 `event_intelligence_service.py:501` 的 `if settings.SOURCE_RELIABILITY_ENABLED:` **内部**
  再套一层、且只管 registry overrides。**我在本轮一度把外层判成后者，差点把"应消歧集合"从 4 个
  错改成 3 个** —— 是**读了外层代码**才纠正回来的。教训：门控判定要读**调用点外层**，不要只看见
  一个同域名字就配对。

## 13.7 验证（全部本机，`env -u http_proxy` 剥掉代理）

| 项 | 结果 |
|---|---|
| `ruff check app/` | **All checks passed** |
| 后端 `tests/test_quality_metrics.py` + `tests/test_operational_readiness.py` | **119 tests / 0 failures / 0 errors**（junit 判据）|
| 前端 `quality-summary-panel` + `quality-operations-dashboard` + `quality-metrics-report-dashboard` | **11 passed** |
| `npx tsc --noEmit` | **exit 0** |
| 行尾审计（`scripts/eol_audit.py`）| 三个源文件 TREE 纯 CRLF（符合检出）；测试文件 HEAD/TREE **同为纯 LF** —— **无降级** |

**新增守卫 + 变异验证**（照 §「断言出现过 ≠ 锁住行为」的纪律，不只断言存在）：

- 后端新增 2 用例：`test_summary_publishes_overlay_flags`（四个键齐备且**逐个断言是 `bool`**）、
  `test_summary_overlay_flags_track_their_settings`（四个开关分别 patch 成 `F/T/F/T`，断言回显**逐一对应**）。
- 前端新增 4 用例：显式 `false` → 出 `DisabledNote` 且行内 `未启用`；四个全关 → 3 条 note + 3 行 `未启用`；
  **缺省 → `queryByText(/该层未启用/)` 为 `null`（"缺省不当作已停用"）**；显式全 `true` → 同样不出 note。
- **变异**：把 `"decision_quality": bool(settings.DECISION_QUALITY_ENABLED)` 硬编码为 `True`
  → 跟踪用例 **failures=1**（守卫确实锁住了行为）→ **逐字节还原，sha256 前后一致**
  （`faa6222fa3bf0648…`）。**若只断言"字段存在"，这个变异会全绿通过** —— 所以"存在"与"对应"是两条断言。

## 13.8 状态

| 事项 | 状态 |
|---|---|
| 其余"默认关闭的开关"是否有说不清的空读数 | ✅ 已扫描：**23 个开关 → 仅 `quality-summary-panel` 有真实假 0**，已修 |
| `ConclusionChallengePanel` 的空态 | ✅ **无需改**（`if (!x) return null` 属"缺席"，不是假 0）|
| 判据边界 | ✅ 已写明（先例驱动；不适用于按名读取的已发布载荷）|

---

# 十四、同一方法的第二个对象：退役源（Manifold）是否还有**可执行**残留（2026-09-27）

§十三 给的是**方法**（不要逐条修，要机械化扫一类）。换个对象再跑一次：
`grep -ri manifold` 全仓命中 **80+ 处** —— 但**命中数不是结论，判据才是**。
本轮判据**不是"该不该出现 Manifold"**，而是**退役设计文档自己列的应删清单**。

## 14.1 判据来源：设计文档的「应删 / Non-goals」

`docs/superpowers/specs/2026-07-08-remove-manifold-channel-design.md` 明确写了
**要删的 5 项**（discovery / auto-resolution / 前端入口 / config 权重与文档 / candidate dedup 优先级），
以及**刻意不删的**（第 18-23 行 Non-goals）：**不删存量事件、不删历史评审与里程碑文档、
不为"提到 Manifold"而清洗旧记录**。

> ⚠️ **这一步是本节的承重墙**：不先读 Non-goals，就会把"刻意保留的历史文档"当成"漏删的残留"，
> 然后去删一堆本该留下的评审记录 —— **把正确的实现改成错的**。

## 14.2 逐条对照（结论：**五项全部已执行**）

| 设计条目 | 现状 | 取证 |
|---|---|---|
| 从 discovery 移除 | ✅ | `event_intelligence_service.py` 的候选源清单无 Manifold；`config.py:556` 自述 "no longer an active discovery or auto-resolution source" |
| 从 auto-resolution 移除 | ✅ | 同上注释；`event_resolve_service.py` 无 Manifold 调用 |
| **前端入口移除** | ✅ | `market-links.tsx:9` `const RETIRED_SOURCE_PLATFORMS = new Set(["Manifold"])`；第 46 行 `showSourceMarketLink = Boolean(source.url) && !RETIRED_SOURCE_PLATFORMS.has(...)` —— **正是设计要的"保留平台文字、不给搜索链接"** |
| config 权重 / 文档 | ✅ | `SOURCE_WEIGHTS` 无该键；只剩 `.env` 兼容用的 legacy **no-op**（设计第 66 行明确允许"treat as no-op"）|
| candidate dedup 优先级 | ✅ | 只在 `candidate_dedup_service.py:87` 的**注释**里出现，作为 `_UNKNOWN_PRIORITY = 99` 的例子（"未排名平台"）—— **不在任何优先级表内** |

**剩余命中分布**：几乎全部落在历史文档（`docs/reviews/**`、`docs/superpowers/specs|plans/**`、
`backend/docs/工程进度.md`）、**备份**（`backend/backups/manifold-purge-*`）、**日志**与 `event_store.json`
—— 正是设计 Non-goals 说**刻意保留**的部分。**可执行代码里没有一处把 Manifold 当活跃源。**

补充一条**刻意保留但看起来像遗漏**的：`app/services/manifold_event_source.py` **文件仍在**，
但 `grep -rn "manifold_event_source" backend/app backend/scripts` **只命中它自己的模块 docstring**
（**调用方 0**）。设计第 35 行写明产品级移除"**safer than deleting every Manifold module
immediately**" → **留着是设计选择，不是漏删**。

## 14.3 结果与它的价值

**无需任何改动。** 本轮产出的不是修复，而是**"已验证干净"的取证记录** ——
它的价值与 §10.5 同源：**让下一次审计不必把同一条重查一遍**，也让它不至于被"命中 80 处"这个数字误导。

**只报告、不改**（按 §8「发现但未修的同类项不自扩范围」的纪律）：

- `backend/docs/工程进度.md:177` 有 `- [ ] Kalshi / Manifold 支持` 这个**未勾选复选框**，读起来像两项都还没做
  （Kalshi **早已支持**、Manifold **已退役**）。它是历史进度日志，设计 Non-goals 明确**不清洗**，
  **故不动**；仅在此登记，供将来整理进度文档时一并处理。

| 事项 | 状态 |
|---|---|
| 退役源是否有可执行残留 | ✅ 已扫描：**五项设计条目全部已执行**，无可执行残留，**零改动** |
| 判据 | ✅ 以**设计文档的应删清单 + Non-goals** 为准，**不是"该不该出现某词"** |
| 只报告未改 | `backend/docs/工程进度.md:177` 的陈旧复选框（历史文档，按 Non-goals 保留）|

---

# 十五、执行 §8.4 的「作废」——并顺带抓出一个产品级缺陷（2026-09-27）

业务方指令：**「作废 Kalshi」**，随后在三个选项中明确选**「只是 §8.4 那条存量数据」**。
（另两个选项是"退役事件发现+自动裁定"与"全量退役 Kalshi"，**均未采纳**：

> 取证旁注（供将来参考）：Kalshi 占事件库 **12/257 = 4.7%**（Polymarket 73.9%、manifold 遗留 19.1%、Limitless 1.9%），
> 但接在 **4 条通路**上（事件发现 / 自动裁定 / 体育调度任务 `kalshi_sports_source` / 期货+体育实时价格 kernel），
> 被 `app/services` **49 文件**、`backend/tests` **147 文件**引用 —— 全量退役是产品决策，不是顺手删源。)

## 15.1 执行（用**项目自带的** API，不是手写 SQL）

`§8.4` 说"`0.2` 在 `predictions` 表"，**不在事件库**（事件库那条记录的 `market_probability` 是 `None`；
它带的是 `probability.baseline = 0.2`）。目标行：

```
predictions.id = aa74e181-c783-4d25-8a0c-8543fce21585
event_id = 0779bde4dcd63e08   contract_id = KXBRUVSEAT-35   platform = Kalshi
ai_probability = 30.17   market_probability = 0.2   raw_edge = 29.97   adjusted_edge = 14.98
decision = provisional_act   status = open   qualified = 0   actual_outcome = NULL   brier_score = NULL
```

**发现项目里已有第一等的作废 API**：`prediction_store.void_prediction()`（`prediction_store.py:490`），
docstring 明说它是**"非真实结算"的终态** —— `open → voided`：**无 Brier、不进校准、移出机会面**，且**幂等**。

| 步骤 | 结果 |
|---|---|
| 备份 | `v2_loop.db.bak-before-void-scale-outlier-20260927-231253`（SQLite `backup()` API 一致性快照；`integrity_check=ok`；predictions 171 / trades 80 / loop_runs 1759 逐表计数与源一致；目标行在备份里为 `('open', 0.2, 29.97)`）|
| 执行 | `void_prediction('0779bde4dcd63e08')` |
| **值是否被改写** | **没有** —— `market_probability` 仍 `0.2`、`raw_edge` 仍 `29.97`；只多了 `status='voided'` 与 `resolved_at` |
| 幂等复核 | 第二次调用返回 `None`（`WHERE status='open'` 已无匹配）✅ |

> **这正合 §8.4 自己定的原则**："按推断改写历史会让审计轨迹不再反映系统当时实际算出的值" ——
> 所以作废 = **加一个终态标记**，不是把 `0.2` 改成 `20`。

## 15.2 一个意外：它**本来就没进"机会面"**

`prediction_store.list_open_opportunities()` 在作废**前后都是 27 条、且都不包含该事件**。
即这条数据从未出现在"可执行机会"列表上。**影响面比 §8.4 预想的小**：
它真正的暴露点是**镜像交易**（见下），不是机会列表。

## 15.3 🔴 新发现（产品级缺陷）：`void_prediction` **不平仓** → 每次非真实结算留下一笔悬空交易

| 路径 | 是否处理 `simulated_trades` |
|---|---|
| **真实结算** `score_prediction()` | ✅ 末行 `_maybe_close_trade(event_id, actual_outcome)`（`prediction_store.py:486`）|
| **非真实结算** `void_prediction()` | ❌ **函数体只 `UPDATE predictions`**，完全不碰交易 |

`event_resolve_service.py` 的两条路径都据此分支（`:162` 活路径、`:284` 对账/自愈路径），
所以**每一次"非真实结算"（身份冲突 → invalid，或市场作废）都会留下一笔永远 `open` 的模拟交易**：
它既不会进 `closed` 统计，也**永远不会被平仓**（该事件不会再产出真实结果）。

**实测存量（本仓 loop DB）**：49 笔 `open` 交易中 —— **47 正常**（prediction 也 `open`）、
**2 悬空**：`id=42`（本次作废产生的）+ `id=76`（`event_id='evtExpired'`，
**根本没有对应的 prediction 行** —— 形似测试夹具泄进了真实库，属另一条小疑点）。

## 15.4 为什么**不能**简单地把它改成 `closed`

`simulated_trade_store.trade_stats()` 的**每一条**查询都按 `status='closed'` 过滤，而 `entry_edge` 是 `NOT NULL`
—— 所以标 `closed` 不是"作废"，是**换一种方式污染**。实测代价（31 笔现状）：

| 指标 | 现状 | 若标 closed |
|---|---|---|
| `total_closed` | 31 | 32 |
| `avg_edge_at_entry` | **10.79** | **11.39** |
| `win_rate` | **0.419** | **0.406** |

（`SUM/AVG(pnl_pct)` 不受影响，因该行 `pnl_pct IS NULL`；但 `total` 分母 +1 会拉低胜率，
而 `AVG(ABS(entry_edge))` 会被 `29.97` 这个离群值抬高。）
并且 `simulated_trades.status` 有 **CHECK 约束** `IN ('open','closed')` —— 加第三个状态需要**表重建迁移**
（`_MIGRATIONS` 目前是**空字典**、`_SCHEMA_VERSION = 1`）。

## 15.5 建议（**未做，待业务方定** —— 属代码变更 + 更多数据变更）

1. **修缺陷（推荐，代码）**：让 `void_prediction()` 在**同一事务内**把该事件的 `open` 交易也置为终态，
   使"非真实结算"不再留悬空交易。需要给 `simulated_trades.status` 的 CHECK 增加 `'voided'`
   （一次表重建迁移），并让 `list_open_trades` / `list_closed_trades` / `trade_stats` 自然忽略它
   （它们都按显式状态白名单过滤，**无需改动**）。
2. **清存量**：`id=42`（本次）与 `id=76`（无预测行）两笔悬空交易。
3. **顺带核**：`evtExpired` 这个 event_id 是怎么进真实库的（疑似测试夹具泄漏）。

| 事项 | 状态 |
|---|---|
| §8.4 那行 `predictions` 作废 | ✅ **已完成**（项目自带 `void_prediction`，值未改写，幂等，已备份）|
| 它"本来就没进机会面" | ✅ 已核实（前后都是 27 条、不含该事件）|
| `void_prediction` 不平仓 | 🔴 **已发现并取证**，建议修，**未动手**（涉迁移）|
| 2 笔存量悬空交易 | ⏳ 待定（清理属数据变更）|
| `evtExpired` 进真实库 | ⏳ 已登记待核 |

---

# 十六、执行 §15.5 建议 ① —— 让「非真实结算」同时平仓（2026-09-27）

业务方在修复方案三选一中选 **A：给 `status` 增加第三个终态 `'voided'`（一次表重建迁移）**；
「存量数据」一项选 **「先不清理」** —— 即**只修前向行为，不动那 2 笔存量悬空行**。

## 16.1 为什么是表重建，以及为什么选"第三态"而不是"标 closed"

本仓的迁移助手 `sqlite_db.apply_migrations()` **只会 `ALTER TABLE ADD COLUMN`**，
而这里要改的是 **CHECK 约束**（`IN ('open','closed')` → 增加 `'voided'`）。SQLite 无法 ALTER CHECK，
只能**重建表**；仓内已有同类先例：`prediction_store._migrate()` 的 UNIQUE 恢复路径。

**选第三态而非"标 closed"**：标 closed 要往 ~8 处统计/列表查询逐一加排除条件
（`trade_stats` / `list_closed_trades` / `count_closed_trades` / `recompute_closed_trades` …），
正是"同一条过滤条件写两遍 → 空头断言"的高发地；加第三态后**这些查询一行都不用改** ——
它们本就按显式状态白名单（`status='open'` / `status='closed'`）过滤。

## 16.2 三个"看起来对、实际会出事"的细节（都有测试守着）

| 细节 | 不这么做会怎样 | 守卫 |
|---|---|---|
| 重建时**显式拷贝 `id`** | `id` 是 `INTEGER PRIMARY KEY AUTOINCREMENT`；不带进列清单会按 1,2,3 重编号，运营引用过的行号**静默改变** | `test_migrate_widens_the_status_check_and_preserves_row_ids`（用 `id=42`/`7` 保证抓得到）|
| 重建前**先 `DROP INDEX`** | 索引名全局唯一，RENAME 后仍挂在 `*_old` 上；`CREATE INDEX IF NOT EXISTS` 会 no-op → 重建后的表**无索引** | 同上 |
| 探测条件**别用裸子串** | 迁移靠读 `sqlite_master.sql` 判断"是否已加宽"；本文件 schema 注释里含同一个词且会被原样存进 `sqlite_master` → v1 表可能被误判"已迁移"而跳过重建 | 已改为**归一化空白后精确匹配 CHECK 子句** `IN ('open','closed','voided')` |

## 16.3 改动面（6 文件）

| 文件 | 改动 |
|---|---|
| `backend/app/memory/simulated_trade_store.py` | CHECK 加 `'voided'`；拆出 `_SCHEMA_STATEMENTS`；新增 `_migrate()`；`_SCHEMA_VERSION` 1→2；新增 `void_trade()` |
| `backend/app/memory/prediction_store.py` | 新增 `_maybe_void_trade()`；`void_prediction()` 在 `with writing()` **之外**调用它 |
| `frontend/src/lib/api.ts` | `SimTrade.status` 由 `"open" \| "closed"` 放宽为**三态** |
| `backend/tests/test_simulated_trade_store.py` | +5 用例 |
| `backend/tests/test_prediction_store.py` | +2 用例 |
| 本文档 | 本节 |

> `void_trade` 必须在 `with writing()` **之外**调用：`sqlite_db._WRITE_LOCK` 是**非重入**的普通 `Lock`，
> 而 `void_trade` 自己会开一个 `writing()` 作用域 —— 持锁时调用会**死锁**。
> `score_prediction` 调 `_maybe_close_trade` 也是出于同一原因放在 `with` 之外。

## 16.4 验证

| 项 | 结果 |
|---|---|
| `ruff check`（改动文件）| ✅ All checks passed |
| 后端守卫批次（6 文件，181 passed）| ✅ junit `tests=217 failures=0 errors=0 skipped=0`（判据取自 `--junitxml`，**不看 stdout**）|
| trades 路由/服务/迁移批次（46 passed）| ✅ junit `tests=52 failures=0 errors=0 skipped=0` |
| 前端 `tsc --noEmit` | ✅ exit 0 |
| **四阶段变异验证** | ✅ 4/4：变异后守卫变红 → **字节级**还原 → 复绿 → sha256 与原字节一致 |

变异清单（脚本在 `%TEMP%`，**未入库**）：

| 变异 | 守卫 |
|---|---|
| `void_prediction` 不再回调 `_maybe_void_trade` | `test_void_prediction_also_voids_the_open_simulated_trade` |
| 表重建漏拷 `id` | `test_migrate_widens_the_status_check_and_preserves_row_ids` |
| CHECK 退回两态 | 上述迁移用例 + 各 `void*` 用例 |
| `void_trade` 只写 `exit_reason`、`status` 仍 `open` | `test_void_trade_*` |

## 16.5 未做（明确保留）

| 事项 | 状态 |
|---|---|
| §15.5 建议 ①（修 `void_prediction` 不平仓）| ✅ **已完成**（本节）|
| §15.5 建议 ②（清 2 笔存量悬空交易 `id=42` / `id=76`）| ⏳ **业务方选"先不清理"，未动** —— 新代码只影响**此后**的非真实结算 |
| §15.5 建议 ③（核 `evtExpired` 来源）| ⏳ 仍待核 |

---

# 十七、批次十二：`voided` 第三态一致性审计 + `baseline_probability` 上游契约调查（2026-09-28）

> 本节承接 §十六。**两条都是只读调查，未改任何源码**（调查前后 `git status` 均为干净）。

## 17.1 先更正 §十六 中两处已被后续工作推翻的陈述

本文档「只追加不改写」，故不回头修改 §十六，在此更正：

| §十六 原文 | 现状（2026-09-28）|
|---|---|
| 16.4 末：「变异清单（脚本在 `%TEMP%`，**未入库**）」 | ❌ **已过时** —— 那 4 条变异已在本日**注册进仓内统一 harness** `backend/scripts/mutation_verify.py`（第 4 套 `voided-trade`，V1–V4），并补了 harness 的**首个守护测试** `backend/tests/test_mutation_verify.py`。提交 `cd3459c`。复验：`python scripts/mutation_verify.py verify voided-trade` **4/4 全绿** |
| 16.5 表：建议 ③（核 `evtExpired` 来源）「⏳ 仍待核」 | ✅ **已结案** —— 见 17.1.1 |

### 17.1.1 建议 ③ 结案：`evtExpired` 是测试夹具泄漏的化石

| 证据 | 内容 |
|---|---|
| 夹具身份 | `tests/test_events_routes.py:2149` 的 `_make_record("evtExpired", …)`；真实事件库 **grep 0 命中** |
| 真实库里的行 | **唯此一行**：`simulated_trades.id=76`、`status='open'`、`entry_time=2026-07-04T19:42:16` |
| 归因 | `git log --all -S'evtExpired'` → 夹具与 `patch.object(trades, "loop_db_path")` **同在 `7ead138`**（2026-07-05 08:30 +08:00）引入 → 泄漏发生在**提交前那次本地运行**（该行早于提交约 5 小时写入）|
| 早已被记录 | E2（`c0c5b52`，2026-08-24）**已点名它**："the one genuinely stranded row in the live database" —— 它正是 `simulated_trades` 被加进 `REFERENCING_TABLES` 的理由 |
| 套件现状 | 跑 `test_events_routes.py` + `test_event_ref_census.py`（146 用例）前后 `simulated_trades` **80→80**、`evtExpired` **1→1** → **已隔离** |

同类先例：E10（`56b9665`）修的是 kernel DB 被测试写了 43 天。**方法**：只用只读连接
（`file:<abs>?mode=ro` 且 `uri=True`）查询，并做前后计数对比。

## 17.2 审计：`voided` 第三态在读取 / 变更 / 展示各面的一致性 —— **通过**

不新增断言，只做机械核对：凡"对交易状态做分支判断"的位置，逐个看它是否会被第三态绕过。

| 面 | 位置 | 结论 |
|---|---|---|
| 读路径 | `list_open_trades` / `list_closed_trades` / `trade_stats`（5 条 SQL）/ `recompute_closed_trades` / `_count_trades` | **全部**按显式 `status='open'` / `'closed'` 过滤 → voided 既不进统计也不进两个列表，**与 §十六 设计一致** |
| 变更路径 | `open_trade` / `close_trade` / `void_trade` | 三者都以 `WHERE … AND status='open'` 为锚 → **voided 与 closed 互斥、不可互相复活**；`close_trade` 对已 voided 的行返回 `None` |
| 定向查询 | `has_open_trade` | 在 `app/` 内**无调用方**（仅定义）→ 无影响 |
| 悬空普查 | `event_ref_census.REFERENCING_TABLES` 含 `simulated_trades`，按 `DISTINCT event_id` 计数 | **与 status 无关** → voided 行照样提供引用完整性；其事件被删时照样计入（正确）|
| 前端 | 仅 `frontend/src/app/trades/page.tsx` 消费 `SimTrade`，只有 open / closed 两个 tab | **永不收到 voided** → 不存在 TypeScript 穷尽分支漏网 |

### 17.2.1 缺口（**产品决策，未动**）：voided 交易在任何界面都不可见

`void_trade` 的 docstring 承诺 `exit_reason='voided'` 能让这笔交易"无需回连预测即可读懂为何离开开放列表"，
但该行**既不在「当前持仓」也不在「已平仓」** → 该承诺**只在 DB 层成立**；`/trades` 页也没有任何地方
显示"N 笔已作废"。这与本仓既定的「**『0』必须只说一件事**」准则同族：`已平仓` 的计数是个
**不声明自己排除了什么**的读数。

对照：**prediction 层**的 void **是**可见的 —— `recent-predictions.tsx` 渲染 `p.status`，会显示 `voided`。

可选处置（未决）：给 `/trades` 增设「已作废」第三 tab（`count_voided_trades` / `list_voided_trades` + 一个端点），
或至少让该页显示作废计数。**属产品决策，等待拍板。**

## 17.3 调查：`baseline_probability` 的「上游契约」找到了 —— 但它是三份未测试的启发式副本

既有的「`baseline_probability` 的标度不能靠值本身守卫」结论是"可靠守卫只能来自上游契约"。本次把该契约查实。

**所有活跃 discovery 源都在源头归一化到 0–100**：

| 源 | 归一化方式 |
|---|---|
| `polymarket_event_source` | `safe_float(market.yes_price, 0.5) * 100` |
| `kalshi_event_source` | `last * 100` / `(bid + ask) / 2 * 100` |
| `metacus_event_source` | `_clamp_pct(safe_float(prob, 0.0) * 100.0)` |
| `limitless` / `opinion` / `predict_fun` | 各自复制一份 `_normalize_probability` |

🔴 **`_normalize_probability` 有三份逐字相同的副本**（实测 sha256 **全等** `315701473ea6`、各 7 行），
且 **`tests/` 对它零引用**（`grep _normalize_probability tests/` 无匹配）。其实现是：

```python
value = safe_float(_clean_number(raw), -1.0)
if 0.0 <= value <= 1.0:
    return value * 100
if 0.0 <= value <= 100.0:
    return value
return None
```

该分支在 `(0, 1]` 上**原理上无法区分**「`0.5` 表示 50%」与「`0.5` 表示 0.5%」：若某源真按**百分数**
报 `0.5`，系统会静默记成 **50%**（`0.2` → 20%）—— 把低概率市场变成接近抛硬币。

🔴 **它比一般 bug 更隐蔽**：正因为它**在源头就把 0–1 值转成像样的 0–100 值**（`0.5 → 50`），
下游探针 `report_probability_scale_outliers.py` 的 `0 < market_probability < 1` 规则
**永远看不到这类泄漏** —— 探针只能抓**绕过归一化**的路径（即 HTTP 请求体 `events.py:274`）。
即 **"下游守卫被上游启发式掩蔽"**：在此处"再加一条下游守卫"注定无效。

→ 结论修正：真正要动的是**这三个源**（或把它们收敛成一份带测试的共享助手）；
"要不要在写入侧加阈值"仍是否命题（会误杀真实存在的 0.2% 市场）。

## 17.4 本节未做（明确保留）

| 事项 | 状态 |
|---|---|
| 17.1.1 建议 ③ 结案 | ✅ 已完成 |
| 17.2 审计 | ✅ 已做（结论：通过）|
| 17.2.1 给 `/trades` 增「已作废」展示 | ⏳ **产品决策，未动** |
| 17.3 收敛三份 `_normalize_probability` + 补测试 | ⏳ **需先定这三个源的真实标度**，未动 |
| §15.5 建议 ②（清 2 笔存量悬空交易 `id=42` / `id=76`）| ⏳ 业务方选"先不清理"，仍未动 |

---

# 十八、补充取证：三个源的标度是**夹具可证**的，真正的缺口是**未测试的兜底字段**（2026-09-28）

> 承接 §17.3。§十七 把 `_normalize_probability` 的歧义描述为"在 `(0,1]` 上无法区分 0.5 是 50% 还是 0.5%"。
> 补做取证后发现：**对已测试的字段该歧义是潜伏的、不是活跃的**；活跃缺口在别处。本节据此收敛 §17.3 的落点。

## 18.1 夹具把三个源的标度钉住了

| 源 | 被测试的原始字段 | 夹具原值 | 期望输出 | 结论 |
|---|---|---|---|---|
| `limitless_event_source` | `prices`（数组）| `[38.0, 62.0]` | `62.0` | 上游 **0–100** → 走第二分支（**不**乘 100）|
| `opinion_event_source` | `latestPrice` | `0.41` | `41.0` | 上游 **0–1** → 乘 100 |
| `predict_fun_event_source` | `resolution.bestBid/bestAsk.price` | `0.57` / `0.59` | `58.0`（中值）| 上游 **0–1** → 乘 100 |

→ 即：**该启发式在"它被测试到的那些字段"上是正确的**。说"无法区分 0.5 是 50% 还是 0.5%"对一个
**已知标度**的字段并不致命 —— 夹具恰恰证明作者知道这些字段的标度。

## 18.2 但活跃缺口在**没有被测试的兜底字段**上

`opinion_event_source._PROBABILITY_FIELDS` =
`("latestPrice", "yesPrice", "yesTokenPrice", "probability")` —— **四个字段共用同一个启发式**，
而 `test_opinion_event_source.py` **只出现 `latestPrice`**
（`grep -E 'yesPrice|yesTokenPrice|probability'` → **零命中**）。

| 兜底字段 | 风险 |
|---|---|
| `yesPrice` / `yesTokenPrice` | 名字即价格 → 0–1 假设**大概率**成立，但**仍无测试** |
| 🔴 `probability` | **唯一"名字不暗示 0–1"的字段**。若上游按百分数返回 `probability: 0.41`（=0.41%），启发式返回 **41**（=41%）→ **100× 误读**，且**下游探针看不到**（掩蔽机制见 §17.3）|

## 18.3 缺口的一般形态（可复用判据）

`_normalize_probability` 不是"算错了标度"，而是把一条**未经校验的假设**
（"本源的每个概率字段要么 0–1、要么 0–100，且 1.0 处无歧义"）**编码成了分支**。
其契约风险随**被接受的字段个数**增长，而测试只覆盖**每源的第一个字段**：

> **判据：一个"标度嗅探"函数，其被测试的字段数 < 它实际接受的字段数 → 未测试的那些就是缺口。**
> 修它**不必写新逻辑**，只要**为每个兜底字段补一条夹具**（字段名 + 已知标度）—— 歧义立刻变成可证事实。

## 18.4 对 §17.4 未做项的更新

| 事项 | 状态 |
|---|---|
| 17.3 收敛三份 `_normalize_probability` + 补测试 | ⏳ 仍待办，**但前置已解**：不必先"定三个源的真实标度"（§18.1 已用夹具定住），**只需为每个兜底字段补夹具**即可把假设变成受测事实 |

---

# 十九、把 §十八 的规律机械化扫一遍：全仓还有哪些"跨文件逐字重复的函数体"（2026-09-28）

> 承接 §十八 的判据（"逐字重复的副本 = 改一处、漏两处的风险"）。不问"有没有 bug"，只问机械问题：
> **把每个函数体的 AST 规范化后哈希，哪些哈希跨 ≥2 个文件出现？**
> 只读：`ast` 解析 + `ast.unparse` 归一化（**丢弃 docstring**、逐语句去空白）+ sha256 前 10 位；函数体 **≥3 行**才计入。
> 口径：`backend/app/**/*.py`（329 个文件）。

## 十九.1 结果：**40 组**

（扫描器写在仓库**外**的临时目录，**未入库**。）

### 19.1.1 与 §十八 直接同源的一簇：三个事件源适配器共享 **5 个**逐字相同的助手

| 助手 | 出现的文件 |
|---|---|
| `_normalize_probability` | `limitless` / `opinion` / `predict_fun`（各 1 份）|
| `_clean_number` | 同上 |
| `_extract_text` | 同上 |
| `_extract_number` | 同上 |
| `_extract_market_list` | `limitless` / `predict_fun`（2 份）|

→ **§十八 只点出 1 个（`_normalize_probability`），实际是 5 个**；收敛这一簇可一次删掉 **13 份**副本（4×3 + 2）。
尤其 `_clean_number` 与 `_normalize_probability` **必须同进同退**（后者调用前者），分开写就有"只改了一个"的漂移面。

### 19.1.2 其余按「跨文件数」排序（前 8 组）

| 组 | 文件数 | 助手 / 位置 |
|---|---|---|
| `_first` / `_dig` / `_text` | **8** | 8 个 `world_cup_*_source.py` 各一份 |
| `_season_year` | 6 | 6 个 `football_live_*_service.py` |
| `_referee_key` ≡ `_team_key` | 6 | 5 个 sports service + `market_totals_service` |
| `_clean_list` | 4 | `sports_fact_service` + 3 个 world_cup 源 |
| `_numeric` | 4 | 4 个 `*_live_*_service.py` |
| `_team_key` | 4 | 4 个 `football_live_*_service.py` |
| `query_fixture` ≡ `query_result` / `save_fixture` / `build_match_outcome` | 3 | `mlb` / `nba` / `nhl` adapter 各一套 |
| `_enforce_rate_limit` | 3 | `mlb` / `balldontlie` / `nhl` stats client |

（余下 32 组多为 2 份，含 `_utc_naive`、`_utc_timestamp`、`_load_params`、`_number`、`_is_prediction_market`、
`normalize_competition_code`、`_extract_market_list` 等。）

## 十九.2 判读：**不是所有重复都该收敛**（无脑收敛会造出另一类风险）

- ✅ **该收敛**：同一语义、同一契约、且**已被 §十八 证明会漂移**的那一簇（19.1.1）。它们在"标度语义"上是
  **同一件事**，分开写纯属历史；收敛后 §18.4 的"补一条夹具"只需补**一次**。
- ⚠️ **不该无脑收敛**：`_first` / `_dig` / `_text` 在 8 个 `world_cup_*_source.py` 里各一份，是**每源独立解析自己 provider**
  的产物，**契约并不共享**（各 provider 的字段形态不同）。抽成公共模块会制造一个"谁用谁都要改"的共享点 ——
  正是 §十八 那类风险的**镜像**。
- 📌 **判据：重复要不要收敛，看它们是否共享同一个"会变的契约"。**
  共享 → 收敛（改一处生效）；不共享 → 保留（差异是特性，不是冗余）。

## 十九.3 附带的工具坑（本次真实踩到，值得记住）

首版扫描器输出 **"0 组"** —— **假阴性**。根因不是"没有重复"，而是这一行：

```python
path.read_text(encoding="utf-8", newline="")   # ❌ 在项目 venv 上 TypeError
```

| 解释器 | `Path.read_text` 签名 |
|---|---|
| 项目 venv **Python 3.11.9** | `(self, encoding=None, errors=None)` —— **无 `newline`** |
| 系统 Python 3.13.14 | `(self, encoding=None, errors=None, newline=None)` —— 有 |

`newline=` 是 **Python 3.13** 才加到 `Path.read_text` 的。它**不是"更严格的读法"**，而是**在新解释器上可用、
在旧解释器上崩**；配 `except: continue` 就变成**静默跳过所有文件**（本次正是如此，输出还"看起来很干净"）。

→ **要字节忠读，用 `path.read_bytes().decode("utf-8")`（全版本安全）**，或 `path.open(encoding=..., newline="").read()`。
**且不要在 `try/except` 里吞掉这类 `TypeError`** —— 它只在环境不匹配时出现，静默吞掉＝把环境错误伪装成"没有发现"。

## 十九.4 本节未做

| 事项 | 状态 |
|---|---|
| 收敛 §19.1.1 的 5 个助手（事件源簇） | ⏳ 未动（属 §18.4 的落地，待拍板）|
| 其余 39 组逐组定性 | ⏳ 只做了 top-8 排序与"是否共享契约"的判据，未逐组判 |

---

# 二十、近重复「漂移」检测：逐字重复之外，有没有"曾相同、后来走散"的孪生（2026-09-28）

> 承接 §十九。§十九 找的是**逐字相同**的副本 —— 它们本身**不会**产生不一致（"改一处漏两处"的风险在"改"的那一刻才发生）。
> 真正的缺陷形态是**近重复**：一对函数曾经相同，一处被修、另一处没被修。本节把它机械化。

## 20.1 方法（两级，只读）

1. **近重复对**：**同名**、**跨文件**、函数体规范化后 `difflib` 相似度 ∈ `[0.5, 1)` → **289 对**（体 ≥4 行、丢 docstring）。
2. **只看漂移点**：对相似度 ≥ **0.90** 的对（96 对）计算**标识符 / 属性名 / 字符串常量的对称差** `Δ`。
   **Δ 小且非空 = "一条改过、另一条没改"的签名** —— 比肉眼比对可靠，也不受"289 对"这个数量级困扰。

## 20.2 结果：**Δ 的最大值只有 4，且没有任何一对差在"守卫"上**

| Δ | 对数 | 差集内容（抽样）| 判读 |
|---|---|---|---|
| 2 | 18 | `'mlb'`/`'nba'`、`'epl'`/`'ucl'`、`'review_queue'`/`'decision_timeline'`、`'drift webhook…'`/`'scheduler failure…'` | **平台名 / 日志串 / 迁移名** —— 正当参数化 |
| 3 | 16 | `_team_key` 的 `{'fc','cf'}`（见 20.3b）、`_check_vocabulary` 的报错措辞、**`_football_data_get` 的异常类名**（见 20.3a）| 见下 |
| 4 | 4 | `fetch_schedule` 的 `'Failed to fetch EPL/NBA… schedule: %s'` + 联赛码 | 正当 |
| ≥5 | **0** | —— | —— |

→ **没有一对孪生函数在"守卫 / 阈值 / 异常类型"上悄悄走散。**

## 20.3 两处**语义**差异：一处潜伏、一处正当

### (a) `_football_data_get`：同一段 fetch 写两遍、各带一个异常类 —— **潜伏，非活缺陷**

`football_data_source.FootballDataAPIError` 与 `football_data_client.FootballDataClientError` 是**两个类**，
而两个模块里各有一份**逐字相同**的 `_football_data_get`（只差异常类名）。实测捕获面：

| 异常类 | 捕获点 |
|---|---|
| `FootballDataAPIError` | **4 处**：`events.py:854` / `events.py:873` / `football_data_source.py:244` / `world_cup_match_service.py:301` |
| `FootballDataClientError` | **0 处**（整个 `app/` 内无 `except FootballDataClientError`）|

**为什么不是活缺陷**：`football_data_client` 只被三个足球适配器的 `sync_schedule` 使用
（`epl_adapter.py:144`、`league_adapter.py:253`、`ucl_adapter.py:180`），而它们都是
`except Exception as exc:  # noqa: BLE001` → log + `return 0/[]`，**宽捕获兜住了**；
且捕获 `FootballDataAPIError` 的那 4 处都在 **world_cup/football_data_source 路径**上，与 client 路径不相交。

→ **潜伏风险**：任何**窄捕获** `FootballDataAPIError` 的**新**调用点，若误走 client 路径就会漏网。
判据同 §十八：**"潜伏"与"活跃"必须分开说。**

### (b) `_team_key`：4 处剥掉 `{'fc','cf'}`、5 处不剥 —— **正当**（每处都在自己的模块内自洽）

`_team_key` / `_referee_key` 的**全部**调用点都在**同一模块内**做"调用方给的名字 vs provider 返回的名字"的比对
（如 `_team_key(_team_name(row)) == team_key`），**不需要跨模块一致**：

- 剥 `fc`/`cf` 的 4 处都是 **club football** provider（行里写 `"Arsenal FC"`、调用方写 `"Arsenal"`，剥后缀正是桥接）；
- `football_live_referee_service._referee_key` 归一化的是**裁判名**（无俱乐部后缀）→ 本就不该剥；
- NBA / NHL / MLB 无此后缀 → 剥与不剥等价。

→ **同 §十九 的判据：共享同一个"会变的契约"才需要收敛。** 此处不共享，差异是**特性**。

## 20.4 结论：对"要不要收敛"的定性影响

**40 组逐字重复 + 289 对近重复里，没有发现活的漂移缺陷。**

→ 因此 §19.1.1 那簇的收敛（删 13 份副本）是**可维护性**改进，**不是 bug 修复**。
这改变了它的立项理由：**不该以"修 bug"的名义做，应以"下次改标度只改一处"的名义做。**

---

# 二十一、批次十六（落实 §18.4 / §19.1.1）：收敛事件源 5 个助手 + 补兜底字段夹具（2026-09-28）

> 承接 §十九.2 的判据（"重复要不要收敛，看是否共享同一个**会变的契约**"）。§19.1.1 指出事件源簇
> 共享 5 个逐字相同的助手。本节把这一簇收敛，并补上 §18.2 点名的"被接受字段数 > 被测试字段数"缺口。
> **只动这一簇**；§19.2 判为"不该无脑收敛"的其余 39 组一律未动。

## 21.1 先更正 §19.1.1 / §20.4 的一个算术错误（追加式，不改旧文）

两处都写了"收敛可删 **13 份**副本"。括号里的 `4×3 + 2` = **14** 是**总定义数**，不是可删数。
可删数 = 总定义数 − 每助手保留 1 份 = 14 − 5 = **9**。

| 助手 | 收敛前定义数 | 收敛后 | 备注 |
|---|---|---|---|
| `normalize_probability` | 3 | 1（共享）| |
| `clean_number` | 3 | 1（共享）| 仅共享模块内部调用 |
| `extract_text` | 3 | 1（共享）| |
| `extract_number` | 3 | 1（共享）| |
| `extract_market_list` | 2 | 1（共享）| 只 limitless + predict_fun；**opinion 保留本地** |
| **合计** | **14** | **5** | **删 9** |

→ 上表是**实测**（收敛后三个适配器共少 9 个 `def`）。§20.4 的定性结论**不变**：这是可维护性改进，不是 bug 修复。

## 21.2 落地

新增 `backend/app/services/event_source_utils.py`（5 个**公开名**函数）。
三个适配器改为 import，并**用别名保持调用点不变**（`extract_text as _extract_text` …），
所以每个适配器的 diff 只有"删本地 def + 换 import"两种形态，调用点零改动。

⚠️ **必须同时删掉各自不再使用的 `safe_float` 导入** —— 改前它只出现在被删的两个助手里
（`_extract_number` / `_normalize_probability`）；保留即触发 `ruff check app/` 的 **F401**（CI 的 lint 门会红）。
`clean_number` 同理**不可**被适配器 import（改后适配器不再直接调用它，import 即 F401）。

| 文件 | 动作 | 行数 |
|---|---|---|
| `event_source_utils.py` | 新建 | 0 → 93 |
| `limitless_event_source.py` | import 4 个；删 5 个本地 def + `safe_float` 导入 | 188 → 152 |
| `opinion_event_source.py` | import 3 个；删 4 个本地 def + `safe_float` 导入 | 173 → 147 |
| `predict_fun_event_source.py` | import 4 个；删 5 个本地 def + `safe_float` 导入 | 173 → 137 |

**唯一"不收敛"的决定**：`_extract_market_list`。opinion 读 `{"result": {"list": [...]}}`，
另两个读裸 `list` / `{"data": [...]}` —— 这是**两个不同的 provider 契约**。按 §19.2 的判据应保留差异，
故 opinion 那份**留在本地**，并把这层差异写进共享模块的 docstring 与一条 `assertIsNot` 守卫。

## 21.3 补夹具（§18.2 的缺口）

§18.2 的缺口形态是"**被接受的字段数 > 被测试的字段数**"。补齐如下：

| 文件 | 新增 | 锁住什么 |
|---|---|---|
| `tests/test_event_source_utils.py`（新建）| 5 个类 | 共享助手直测：标度契约（0–1 → ×100、0–100 直通、越界 → `None`）、`extract_number`/`extract_text` 的"首个非空 / 非缺"顺序与默认值、`extract_market_list` 的形状 |
| `test_opinion_event_source.py` | 2 个用例 | **`_PROBABILITY_FIELDS` 的 4 个字段逐个被读到**（此前只测 `latestPrice`）；且 `latestPrice` 优先于回退位 |
| `test_limitless_event_source.py` | 1 个用例 | `_QUESTION_FIELDS` / `_VOLUME_FIELDS` 的回退位真的会被读到 |
| `test_predict_fun_event_source.py` | 1 个用例 | `_ID_FIELDS` / `_LIQUIDITY_FIELDS` 的回退位 |

📌 **对 §19.2 "补一条夹具只需补一次"的修正**：只有**助手的契约**收敛成了一处（所以标度只需测一次）；
**字段清单仍然是每个适配器一份**（它们本就是各 provider 各不相同的东西），故回退位夹具仍需 per-adapter。
—— 收敛省掉的是"标度语义改三处"，不是"字段清单能共用"。

## 21.4 一条新发现：`normalize_probability` **不取整**

写直测时首版断言 `normalize_probability(0.57) == 57.0` → **红**：IEEE-754 下 `0.57 * 100` 是
`56.99999999999999`。

→ 这**不是缺陷**：取整发生在**适配器构建事件载荷时**（`round(probability, 2)`）。
既有适配器用例（`[0.57, 0.59]` → `58.0`）之所以绿，正是因为有那一次 `round`。

**结论**：助手的契约是"**不取整**"，测试必须用容差（`assertAlmostEqual(..., places=9)`），已写进助手 docstring。
→ 复用 §十八 的教训：**判"是不是 bug"要先问"是哪一层的契约"** —— 在错误的层断言精确相等，会造出假红。

## 21.5 守卫按 **identity** 钉住共享（含变异验证）

`AdaptersShareTheHelpersTests` 用 `assertIs` 把三个适配器钉在共享**对象**上
（`_extract_text` / `_extract_number` / `_normalize_probability`；`_extract_market_list` 只钉 limitless + predict_fun），
并用 `assertIsNot(opinion._extract_market_list, utils.extract_market_list)` 把"opinion 合法保留本地"也钉住。

**变异验证（本地、一次性）**：把一份**真实副本**放回 `predict_fun_event_source.py`（连同它自己的 `_clean_number`）：

| 阶段 | junit | 观察 |
|---|---|---|
| 变异后 | `tests=47 failures=1 errors=0` | **只有 identity 守卫变红**；行为用例 22 passed **全绿** |
| 还原后 | `tests=63 failures=0 errors=0` | 全绿；blob 回到 `d5c26ddd…`（逐字节一致）|

→ **关键观察：一份功能等价的本地副本让所有行为用例都保持绿色**（47 个里只有那 1 个 identity 断言红）。
**行为测试对"重复"是盲的。** 这正是本守卫存在的理由 —— 只用"结果对不对"守卫，重复会静默回来。
（与 §十九.3 的"空头断言"同族：守卫必须锁**形状**，而"有没有副本"本身就是一种形状。）

## 21.6 验证口径与结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 单测 | 4 个测试文件 | junit `tests=63 failures=0 errors=0`（含 28 subtests）|
| 集成 | `tests/test_event_intelligence_service.py` | junit `tests=127 failures=0 errors=0` |
| Lint | `ruff check app/` | `All checks passed!` |
| 行尾 | `scripts/eol_audit.py` | `damage: none`（6 个改动文件 + 2 个新文件全纯 CRLF、无 BOM）|
| 影响面 | 全仓 grep | 生产侧只有 `event_intelligence_service.py` 引用（取 `fetch_candidate_events`，未受影响）|

判"绿"只看 junit 的 `failures`/`errors`，不看 stdout 的 `passed`（沿用既有口径）。

## 21.7 本节未做

| 事项 | 状态 |
|---|---|
| §19.2 判为"不该无脑收敛"的其余 **39 组**（`_first`/`_dig`/`_text`/`_season_year` 等）| ⏳ 未动（契约不共享，差异是特性）|
| 把本 identity 守卫**登记进** `scripts/mutation_verify.py` | ⏳ 未做（本次变异是**一次性本地**；常驻需新增一个 set，取舍同 §19.4）|
| §17.2.1 的 `/trades` "已作废" 视图 | ⏳ 产品决策，未决 |

---

# 二十二、把 §十八 的缺口形态机械化扫一遍：全仓「被接受但未被测试的字段清单」+ 两处常量级发现（2026-09-28）

> 承接 §18.2 的判据（"**被接受的字段数 > 被测试的字段数 = 缺口**"）与 §二十一（我只按此修了 3 个事件源）。
> 本节只问机械问题：**全仓还有多少个"模块级全字符串常量"，其元素从未在覆盖它的测试里被引用？**
> 只读：`ast` 解析 + 正则字面量匹配。扫描器写在仓库**外**，未入库。

## 22.1 口径，以及**第一版扫描器是错的**

| 维度 | 口径 |
|---|---|
| 候选 | `backend/app/**/*.py` 顶层 `Assign`，值为 `Tuple/List/Set` 且元素**全是字符串字面量**、个数 ≥2 → **73 个** |
| "已测试" | 该元素以**带引号字面量**（`"x"` / `'x'`）出现在"覆盖该模块的测试文件"里 |
| 覆盖判定 | 测试文件名 == `test_<模块名>.py`，**或**该测试文本含该模块的点号路径（`app.services.x.y`）|

🔴 **第一版有一处系统性假阳性**（**与 §十九.3 同族：先别信扫描器**）：
`limitless._ACTIVE_STATUSES` 被判 **0/4**，但夹具里明明有 `"status": "FUNDED"` ——
该集合是拿**小写化后**的值去 `in` 的（`str(...).lower()`），而扫描器按**大小写敏感**比字面量。
把两侧都 `lower()` 后，该常量变成 **1/4**（`funded` 由 `"FUNDED"` 覆盖）。
→ **判据：匹配的宽严必须与被测代码的"归一化"一致。** 代码 `lower()` 了，扫描器就不能区分大小写。

**口径收敛**：`STOPWORDS` / `*_KEYWORDS` / `TRUSTED_SOURCES` / `*_DOMAINS` 这类**词表**（11 个常量）
逐元素测覆盖率**没有意义** → 单列计数、不逐条列；`__all__`（2 个）同理。

## 22.2 结果：29 个"契约型"常量存在未被引用的元素

### 22.2.1 ⚠️ 最该先说：**§二十一 的修复只是抽样，不是补齐**

§二十一 给三个事件源的每个字段元组**各补了 1 条回退位用例**。扫描器把它量化了 —— 仍未被引用的元素：

| 模块 | 常量 | 已覆盖 | 未被引用 |
|---|---|---|---|
| `limitless` | `_LIQUIDITY_FIELDS` | 1/5 | `liquidity`, `liquidityUsd`, `liquidity_usd`, `totalLiquidity` |
| `limitless` | `_VOLUME_FIELDS` | 2/5 | `volumeUsd`, `volume_usd`, `totalVolume` |
| `limitless` | `_ID_FIELDS` | 2/5 | `marketId`, `market_id`, `address` |
| `limitless` | `_ACTIVE_STATUSES` | 1/4 | `active`, `open`, `trading` |
| `opinion` | `_VOLUME_FIELDS` | 1/4 | `volumeUsd`, `volume_usd`, `totalVolume` |
| `opinion` | `_LIQUIDITY_FIELDS` | 1/4 | `liquidityUsd`, `liquidity_usd`, `totalLiquidity` |
| `opinion` | `_ID_FIELDS` | 1/4 | `id`, `market_id`, `slug` |
| `opinion` | `_QUESTION_FIELDS` | 2/4 | `title`, `name` |
| `opinion` | `_ACTIVE_STATUSES` | 1/4 | `active`, `open`, `trading` |
| `predict_fun` | `_VOLUME_FIELDS` | 1/4 | `volumeUsd`, `volume_usd`, `totalVolume` |
| `predict_fun` | `_LIQUIDITY_FIELDS` | 2/4 | `liquidity_usd`, `totalLiquidity` |
| `predict_fun` | `_ID_FIELDS` | 2/4 | `market_id`, `slug` |
| `predict_fun` | `_QUESTION_FIELDS` | 2/3 | `name` |
| `predict_fun` | `_ACTIVE_TRADING_STATUSES` | 1/2 | `trading` |
| `predict_fun` | `_ACTIVE_MARKET_STATUSES` | 2/3 | `active` |

→ **诚实的结论**：§二十一 钉住的是**标度契约**（那才是它的目的），而**字段清单的逐元素覆盖仍是抽样**。
**"修了 1 个代表" ≠ "补齐"。** 这正是"补一条夹具"这种措辞的危险处。

### 22.2.2 全仓最有价值的一条：`sports_fact_service._KNOWN_KINDS`（5/12）

它是一个**校验白名单**：`if kind not in _KNOWN_KINDS: raise SportsFactValidationError("unsupported_kind")`。
12 个成员里只有 **5 个**在测试里出现（已逐条回读 `test_sports_fact_service.py` 核实，与扫描器一致）：
`injury` / `discipline` / `qualification` / `player_award` / `team_stat`。
**未被引用的 7 个**：`availability`、`suspension`、`match_state`、`match_result`、`lineup`、`player_stat`、`tournament_status`。

→ **为什么这条最值得修**：白名单成员一旦拼错 → 该 `kind` 的体育事实在**入库时被静默拒绝**
（`unsupported_kind`），而现有测试**不会红**。与 §18.2 同构，落点从"标度"换成"**准入名单**"。

### 22.2.3 其余契约型常量（未逐条定性）

`world_cup_prediction_pipeline._KNOCKOUT_STAGES` 0/4、`football_live_schedule_service._ALLOWED_STATUSES` 1/6、
`world_cup_statistics_source._SKIP_PLAYER_STAT_PATHS` 0/10、`kalshi_event_source._SETTLED_STATUSES` 1/4、
`football_live_availability_service._ALLOWED_ROLES` 1/4、`event_store._PLATFORM_NAME_SETTINGS` 0/8、
`club_elo_service._PREFIXES` / `_SUFFIXES` 0/8、`world_cup_quality_service.ENGINE_NAMES` 3/4、
`execution_quality_service._STRONG_DIRECTIONS` 1/2、`factor_attribution._OUTCOME_KEYS` 3/5、
`mlb_adapter._MLB_PLAYOFF_TYPES` 1/5。

⚠️ **其中 3 个是低信号，别当缺口报**：`_MLB_PLAYOFF_TYPES` 的未覆盖项是**单字母** `D/F/W/P`、
`_STRONG_DIRECTIONS` 是 `NO`、`_OUTCOME_KEYS` 是 `home`/`away` —— 短词/常用词做"字面量存在性"判据几乎不可靠。
**判据：元素越短、越通用，这个扫描越不适用。**

📌 **附带取证**：`world_cup_statistics_source._SKIP_PLAYER_STAT_PATHS` 是个 `set`，里面**同时**有
`"games.appearences"` 与 `"games.appearances"` —— **这不是笔误**，是给"上游拼错键名"留的**防御性条目**
（用于 `if path in _SKIP_PLAYER_STAT_PATHS: continue`）。它从未被测试引用，属**真覆盖缺口**，
但"两处拼写"本身是**正当的**（同 §20.3b 的 `{'fc','cf'}`）。

## 22.3 两处**常量级**发现（不是覆盖率问题，是数据本身）

### 22.3.1 🔴 `kalshi_sports_source._KALSHI_SPORTS_SERIES_PREFIXES` 有**重复元素**

```python
_KALSHI_SPORTS_SERIES_PREFIXES = (
    "KXNBAGAME", "KXMLBGAME", "KXNHLGAME",
    "KXSOCCEREPL", "KXSOCCERUCL", "KXSOCCERWCS",
    "KXNFL", "KXNBAGAME",          # ← 与第 1 项重复
)
```

它是 `tuple`（不是 `set`）→ 重复**不会**被折叠，但用于
`series.upper().startswith(_KALSHI_SPORTS_SERIES_PREFIXES)` → **功能上无害**。
但第 8 个槽位**本该是另一个联赛前缀**（如 WNBA / NCAA），现在被重复项占掉
→ **那类体育市场会被静默丢出过滤**。
**这是"疑似漏项"，不是"冗余"** —— 建议找作者确认第 8 项本意，**不宜代决**。

### 22.3.2 `club_elo_service._PREFIXES` 与 `_SUFFIXES` **逐字相同**

两者都是 `("afc","fc.","cf.","ac.","fc","cf","ac","sc")`。
读 `_normalize_team_name` 的 docstring 与上方注释（"Keep 3-char tokens ahead of 2-char tokens"）后判断：
**同一套前后缀在两侧都出现是合理的**（`FC Bayern` 与 `Arsenal FC`）→ **正当的重复**。
但它是 §20 那句"**重复的『数据』也要比**"的实例：**这份重复在"函数体"口径下完全隐形。**

## 22.4 判据小结（可复用）

1. **"被接受的枚举 / 白名单"是缺口高发地** —— 对它写代码容易、逐成员做夹具很难。
   凡 `if x not in CONST` / `startswith(CONST)` / `CONST[x]` 这种**按名单查**的位置，都该照此扫一遍。
2. **匹配宽严必须对齐代码的归一化**（`lower()` / `strip()` / 别名），否则扫描器制造**大面积假阳性**（本次实测）。
3. **元素越短越通用，扫描越不可靠**（单字母、`home`/`away`、`NO`）→ 这类命中必须人工降级。
4. **词表（STOPWORDS / KEYWORDS）不进这个口径** —— 逐元素覆盖无意义；它们该用**行为测试**（喂一篇文本看输出）覆盖。
5. **含重复元素的常量**是独立的第三类发现：`set` 会折叠、`tuple` 不会；后者常藏着**漏项**。

## 22.5 本节未做

| 事项 | 状态 |
|---|---|
| 为 §22.2 的任一缺口**补夹具** | ⏳ 未动（本节只做扫描与定性；改测试需单独拍板）|
| 修 `_KALSHI_SPORTS_SERIES_PREFIXES` 的疑似漏项 | ⏳ 未动（**第 8 项本意需作者确认**，不宜代决）|
| §19.2 的其余 39 组、§21.7 的 harness 登记、§17.2.1 的 `/trades` 视图 | ⏳ 仍未动 |

---

# 二十三、按 §22.2 补夹具：白名单全量 + 事件源字段元组改「自扩展」，并量出这类夹具的**盲区**（2026-09-28）

> 承接 §22.2（29 个契约型常量存在未测元素）与 §22.2.1（§21 的修复只是抽样）。
> 本节**只改测试**，生产代码一字未动。

## 23.1 改法：把「逐条枚举」换成「**驱动自常量**」

| 位置 | 做法 |
|---|---|
| `sports_fact_service._KNOWN_KINDS` | 新增 `test_every_known_kind_passes_validation`：对白名单里**每一个** kind 各跑一遍 ingest，断言 `error_count == 0`；给每条 fact 独立 `fact_id`，顺带断言 `total == 12`（证明无静默 upsert 覆盖）|
| 三个事件源的 `_QUESTION_FIELDS` / `_ID_FIELDS` / `_VOLUME_FIELDS` / `_LIQUIDITY_FIELDS` / `_ACTIVE*_STATUSES` | 各新增 `test_every_listed_field_and_status_is_honoured`：`for field in getattr(source, CONST)` —— **遍历模块自己的常量** |
| `opinion._PROBABILITY_FIELDS` | 把 §21 那条「枚举 3 个兜底字段」改为**遍历常量本身**，4 个成员全测 |

**为什么用「驱动自常量」**：以后往这个元组/白名单**加一个成员，它立刻被覆盖**，不需要有人记得回来补用例。
—— 这正是 §18.2 缺口的成因：**加字段的人不会同时回来加夹具。**

## 23.2 但必须量出这类夹具的**盲区**（变异验证）

**三个变异**（字节级；每次跑完**逐字节还原**并核对 sha256）：

| 变异 | 结果 | 说明 |
|---|---|---|
| **M1** 白名单成员没小写化（`"match_state"` → `"Match_State"`）| **RED**（2 failures）| **承重**：代码对输入 `.lower()` 再查名单 → 混合大小写的成员**永远匹配不上**，该 kind 静默被拒 |
| **M2** 从白名单**删掉**一个成员（`"availability"`）| 首轮 **GREEN** → 加钉子后 **RED** | 见 §23.2.1 |
| **M3** 从 `opinion._QUESTION_FIELDS` **删掉**一个字段（`"name"`）| **GREEN** | **盲区仍在**，见 §23.2.2 |

🔴 **关键结论：驱动自常量的夹具，对「成员被删除」是盲的。**
遍历 `_KNOWN_KINDS` 时删掉一个成员 = 少跑一轮，**全部断言照样通过**（实测 M2 首轮 GREEN、M3 GREEN）。
与 §21.5 的「行为测试对重复是盲的」同族：**它只证明「列出来的都被兑现」，不证明「清单是全的」。**

### 23.2.1 对白名单补了「完整性钉子」（M2 由此转红）

沿用本仓既有做法（`test_mutation_verify.py` 用**显式有序清单**钉住 set 名单、app-nav 测试用显式标签清单），
新增 `test_the_whitelist_matches_the_documented_kinds`：把 12 个 kind 写成**显式字面量集合**与 `_KNOWN_KINDS` 比相等。
→ **M2 从 GREEN 转 RED**（删除成员必须是一次「看得见」的编辑）。复测：M1 = 2 failures、M2 = 1 failure。

### 23.2.2 事件源字段元组的完整性**没有**加钉子（有意）

原因：这些元组是**回退优先级表**，"完整性"取决于 **provider 到底发哪些字段** ——
仓库里**没有可据以钉住的权威清单**（§22.3.1 的 kalshi 疑似漏项正是要问作者）。
硬钉一份我猜的清单 = 把猜测写成契约。→ **保留盲区并在此显式声明**：
`_QUESTION_FIELDS` / `_ID_FIELDS` / `_VOLUME_FIELDS` / `_LIQUIDITY_FIELDS` / `_ACTIVE*_STATUSES`
的**成员增删不受任何守卫约束**。

## 23.3 验证

| 检查 | 命令 | 结果 |
|---|---|---|
| 受影响 5 个 + 集成 1 个文件 | pytest | junit `tests=264 failures=0 errors=0`（含 91 subtests）|
| Lint | `ruff check app/` | `All checks passed!` |
| 行尾 | `scripts/eol_audit.py` | `damage: none`（4 个测试文件 + 文档；测试文件均纯 CRLF）|
| 变异 | M1 / M2 / M3 | 见 §23.2；三次均**逐字节还原**（sha256 前后一致）|

新增用例：`sports_fact_service` **+3**（全量 ingest、完整性钉子、负向 `unsupported_kind`）、
三个事件源**各 +1**（自扩展遍历）、`opinion._PROBABILITY_FIELDS` 由枚举 3 个改为遍历 4 个。

## 23.4 本节未做

| 事项 | 状态 |
|---|---|
| §22.2.3 其余契约常量（`_KNOCKOUT_STAGES`、`_ALLOWED_STATUSES`、`_SETTLED_STATUSES`、`_ALLOWED_ROLES`、`_PLATFORM_NAME_SETTINGS` 等）| ⏳ 未动（同一改法可套用；本轮先做 §22 点名的优先项）|
| §22.3.1 `_KALSHI_SPORTS_SERIES_PREFIXES` 第 8 项 | ⏳ 仍需作者确认 |
| §21.7 的 harness 登记 / §17.2.1 的 `/trades` 视图 | ⏳ 仍未动 |

**提交状态**：本节只改测试（4 个文件）+ 文档；**未提交**。

---

# 二十四、把 §23 的改法套到 §22.2.3：先摸**消费方式**再取舍，并量出「驱动自常量」的盲区是**系统性**的（2026-09-28）

> 承接 §22.2.3（11 个契约型常量，未逐条定性）与 §23.4（该清单标着"未动"）。
> 本节**只改测试**（7 个已有文件 + 1 个新文件），生产代码一字未动。

## 24.1 取舍判据：先看常量**怎么被消费**，再问夹具能不能承重

§23 的结论是「驱动自常量 + 完整性钉子」。照抄之前必须先问一句：
**这个常量是按"名字 / 成员资格"被消费的吗？** 只有是，逐成员夹具才承重；否则就是同义反复。

| 消费形态 | 典型代码 | 逐成员夹具 |
|---|---|---|
| **校验白名单**（不匹配 → 整体拒绝） | `if x not in CONST: return None` | ✅ 承重：缺一成员 = 合法输入被**静默**丢掉 |
| **准入 / 排除集**（匹配 → 排除、跳过） | `if x in CONST: continue` | ✅ 承重：缺一成员 = 该排除的**静默**留下 |
| **同源循环**（拿常量当字典键集） | `for k in CONST: out[k] = …` | ❌ 同义反复（遍历自己） |
| **能力探测** | `for k in CONST: if hasattr(obj, k)` | ❌ 同义反复 |
| **先归一化再查表** | `_normalize(x) in CONST` | ⚠️ 必须连 `_normalize` 的**值域**一起查（§24.4） |

## 24.2 §22.2.3 十一条逐条定性

| # | 常量 | 消费方式 | 判定 | 落地 |
|---|---|---|---|---|
| 1 | `football_live_availability_service._ALLOWED_ROLES` (4) | `role not in …` → `_parse_absences` 返回 `None` → **整份快照作废** | ✅ 补 | 循环 + 钉子 |
| 2 | `football_live_schedule_service._ALLOWED_STATUSES` (6) | 同上（`status not in …` → 整份作废） | ✅ 补 | 循环 + 钉子 |
| 3 | `kalshi_event_source._SETTLED_STATUSES` (4) | `status.lower() in …` → 排除该市场 | ✅ 补 | 循环（含 `.upper()` 一腿）+ 钉子 |
| 4 | `world_cup_statistics_source._SKIP_PLAYER_STAT_PATHS` (10) | `if path in …: continue` → 跳过该叶子 | ✅ 补 | 循环 + 钉子 |
| 5 | `club_elo_service._SUFFIXES` (8) | `for s in …: if normalized.endswith(s)` | ✅ 补 | 循环 + 钉子（**有序**元组） |
| 6 | `club_elo_service._PREFIXES` (8) | `for p in …: if normalized.startswith(p)` | ✅ 补 | 同上 |
| 7 | `mlb_adapter._MLB_PLAYOFF_TYPES` (5) | `game_type in … or any(tok in series_desc…)` | ✅ 补 | 循环（payload 刻意省 `seriesDescription`）+ 钉子 |
| 8 | `world_cup_quality_service.ENGINE_NAMES` (4) | `for engine in ENGINE_NAMES` 建 `by_engine` 字典 | ❌ 同义反复 → **改关系型守卫** | §24.4 |
| 9 | `world_cup_prediction_pipeline._KNOCKOUT_STAGES` (4) | `_normalize_stage(stage) in …` | ⚠️ 归一化后查表 → **关系型守卫** | §24.4 |
| 10 | `event_store._PLATFORM_NAME_SETTINGS` (8) | `for attr in …: getattr(settings, attr)` | ⛔ **跳过**：已有守卫 | 见下 |
| 11 | `execution_quality_service._STRONG_DIRECTIONS` (2) | `raw_direction in …` | ⛔ 跳过：元素太短，且真正的病是**5 处重复**（§24.6.2） | — |
| — | `factor_attribution._OUTCOME_KEYS` (5) | `for k in …: if hasattr(item, k)` | ⛔ 跳过（能力探测，无物可断言） | — |

**第 10 项为何跳过**：`app/memory/event_store.py:517-522` 的注释已写明 `test_event_store_source_names`
**把这份名单与"扫描适配器模块"的结果对比**。读码核实，那是**两道精确划分（exact partition）+ 一道下界**：

| 守卫 | 位置 | 断言 |
|---|---|---|
| `test_platform_name_settings_matches_what_the_adapters_read` | `test_event_store_source_names.py:135-150` | `assertEqual(scanned, set(_PLATFORM_NAME_SETTINGS))`，docstring 明说 *"Exact partition, not a subset: **extra or missing entries both fail**"* |
| `test_every_platform_name_setting_is_classified` | `test_source_platform_identity.py:89-103` | `assertEqual(classified, set(_PLATFORM_NAME_SETTINGS))` |
| `test_scan_source_is_populated`（守卫的守卫） | `test_source_platform_identity.py:83-87` | `assertGreaterEqual(len(…), 8)` |

→ 这里**已有独立 oracle**（"扫适配器模块"扫出来的那份），所以**删除方向本来就被覆盖** ——
它恰好是 §24.5.1 那条判据的**现成正例**。再加一道夹具只是叠床架屋。

**第 11 项与 `_OUTCOME_KEYS` 为何跳过**：§22.2.3 已判定"**字面量存在性扫描**"对单字母 / 常用词不可靠。
这与"能不能写夹具"是两件事 —— 第 7 项（`_MLB_PLAYOFF_TYPES`，也是单字母）就补了夹具，
因为它的消费形态**有行为可断言**；而 `_STRONG_DIRECTIONS` / `_OUTCOME_KEYS` 没有（一个是 `in` 判等、
一个是 `hasattr` 探测），**再加夹具只会得到一条同义反复**。

## 24.3 落地夹具：**循环 + 钉子**成对使用

7 个常量各补两条：

- **循环**（`for member in CONST:`）—— 逐成员走一遍真实的拒绝 / 放行路径，断言**列出来的都被兑现**；
- **钉子**（`assertEqual(CONST, {…显式字面量…})`）—— 把成员集本身钉住。

二者**缺一不可**，理由见 §24.5。几个实现细节：

- `kalshi` 的循环跑**两个拼写**（`status` 与 `status.upper()`），因为代码先 `.lower()` —— 顺带钉住归一化；
- `world_cup_statistics_source` 的 payload 由 `_SKIP_PLAYER_STAT_PATHS` **生成**（按 `.` 拆成嵌套 dict），
  再塞一个 `shots.total` 作**对照叶子**：断言"没有任何被禁路径出现"**且**"对照叶子在" —— 否则空载荷会让测试真空通过；
- `mlb_adapter` 的 payload **刻意不带 `seriesDescription`**：该函数`OR`了一条文本兜底
  （`any(tok in series_desc …)`），带上它就会**掩盖**成员缺失；
- `club_elo_service` 的两个表钉的是**有序元组**（不是集合）：`_normalize_team_name` 遇到第一个匹配就
  `break`，所以"3 字符记号必须排在 2 字符记号之前"是**契约的一部分**（文件里的注释也这么说）。

## 24.4 两条关系型守卫：**用"另一个常量"当 oracle**（不写字面量）

`ENGINE_NAMES` 与 pipeline 的 `_KNOCKOUT_STAGES` 属于「同源循环 / 归一化后查表」两类，
逐成员夹具只能得到同义反复。对它们改用**跨常量关系**：

| 守卫 | 关系 | 破坏后会怎样 |
|---|---|---|
| `world_cup_quality_service.ENGINE_NAMES` | `== set(world_cup_engines.ENGINES) \| {"integrated"}` | 新注册一个可运行引擎而忘了加进名单 → 质量报告的 `by_engine` **少一个键**（`_summarize([])` 报一个干净的空桶，**不报错**） |
| pipeline `_KNOCKOUT_STAGES` | `⊆ set(_STAGE_MAP.values())` | 加一个 normalizer **永远产生不出来**的名字 = 死成员，`is_knockout` 恒 False |
| pipeline `_STAGE_MAP.values()` | `− _KNOCKOUT_STAGES == {"group_stage", "third_place"}` | 别名**目标**拼错（`"quarter finals": "quarterfinals"`）→ 多出一个既非淘汰赛、也非小组赛的规范形 |
| `elo_odds_engine._KNOCKOUT_STAGES`、`situational_adjust._KNOCKOUT_STAGES` | `⊇ pipeline._KNOCKOUT_STAGES` | 引擎副本少一个规范形 → 该轮次静默回落到"允许平局"的安全默认 |

`ENGINE_NAMES` 那条落在 `backend/tests/test_world_cup_quality_service.py`；
其余三条落在新文件 `backend/tests/test_knockout_stage_whitelist_consistency.py`
（后者的模块 docstring 里写明三份副本各自的成员与归一化方式）。

## 24.5 🔴 变异验证：「驱动自常量」对**成员删除**是**系统性**盲的

对 7 个常量各做一次**字节级**删除成员变异，**同一个变异分别只跑「循环」与只跑「钉子」**：

| 变异（删掉一个成员） | 只跑**循环** | 只跑**钉子** |
|---|---|---|
| `_ALLOWED_ROLES -= bench` | **GREEN** | **RED** |
| `_ALLOWED_STATUSES -= suspended` | **GREEN** | **RED** |
| `_SETTLED_STATUSES -= determined` | **GREEN** | **RED** |
| `_SKIP_PLAYER_STAT_PATHS -= substitutes.bench` | **GREEN** | **RED** |
| `_SUFFIXES -= ac.` | **GREEN** | **RED** |
| `_PREFIXES -= sc` | **GREEN** | **RED** |
| `_MLB_PLAYOFF_TYPES -= W` | **GREEN** | **RED** |

**7/7 循环全绿、7/7 钉子全红** —— §23.2 在 `_KNOWN_KINDS` 上看到的现象**不是个案，而是这类夹具的定义性盲区**：
`for member in CONST` 里的 `CONST` **就是被测对象本身**，删掉一个成员只是**少跑一轮**，
"每一条都通过了"这句话里**少掉的那一条不会说话**。

关系型守卫**不需要钉子也承重**（同一批变异）：

| 变异 | 结果 |
|---|---|
| `ENGINE_NAMES -= gbm` | **RED** |
| `_STAGE_MAP["quarter_final"] = "quarterfinals"`（别名目标拼错） | **RED** |
| pipeline `_KNOCKOUT_STAGES += round_of_32`（死成员） | **RED** |
| `elo_odds_engine._KNOCKOUT_STAGES -= final` | **RED** |
| **对照**：`situational_adjust._KNOCKOUT_STAGES -= third_place` | **GREEN**（有意：见 §24.6.1，这条差异**故意不钉**） |

### 24.5.1 判据升级（把 §23.2 的说法收紧）

§23.2 的结论是"驱动自常量对删除是盲的，需要**显式字面量**"。本轮实测把它收紧成更本质的一句：

> **oracle 必须独立于被测常量。**
>
> - 显式字面量清单 → 独立 ✅（钉**成员集**）
> - **另一个常量** → 独立 ✅（钉**关系**）
> - 常量自己 → **不独立** ❌（断言写得再细也一样，见上表 7/7）

所以"驱动自常量"这个技巧的正确表述是：**它只覆盖"新增成员会被自动跑到"这一个方向**
（§23.1 的原意），**不能**当作"成员集正确性"的守卫。两者互补，不要拿一个当另一个。
—— 这也解释了 §23.2.2 为什么对事件源字段元组**只能**保留盲区：那里的 oracle 既不是仓库里的另一个常量，
也不是可以据以钉住的清单（它取决于 provider），**三个选项里没有独立的那一个**。

## 24.6 常量级发现（不是覆盖率问题，是数据本身）

### 24.6.1 🔴 三份 `_KNOCKOUT_STAGES` 的成员集**互不相同**，且其中一份的注释与事实不符

| 模块 | 成员数 | 内容 | 该处对 stage 的归一化 |
|---|---|---|---|
| `world_cup_prediction_pipeline` | **4** | `round_of_16, quarterfinal, semifinal, final` | 先过 `_STAGE_MAP` 取**规范形** |
| `kernel.engines.elo_odds_engine` | **6** | + `quarter_final, semi_final` | 只 `.lower().strip()`（**连空格都不换**） |
| `kernel.engines.situational_adjust` | **7** | + `third_place` | `.lower().strip()` + 空格→下划线 |

三处**归一化宽严不同**，所以"成员集不同"部分是**有原因的**（各自的输入长什么样不一样）。
但由此暴露两条真实差异：

1. 🔴 `elo_odds_engine.py:44` 的注释写着 *"Matches the legacy pipeline's `_KNOCKOUT_STAGES` set"* ——
   **按字面不成立**（4 vs 6）。它只说对了"**归一化之后**语义一致"。后人若照字面把它"同步"成 4 个成员，
   带空格的 `"quarter final"` 一类输入会**静默变成非淘汰赛**。
2. ⚠️ `third_place` 只在 `situational_adjust` 里算淘汰赛，pipeline 不算。
   三四名决赛同样必有加时 / 点球（**不允许平局**），所以这**可能是 pipeline 侧的漏项**；
   也可能是"三四名不参与 `is_knockout` 的建模选择"。**本机无判据，需作者确认**（同 §22.3.1 的处置）。

本节只把**必须成立的方向**（规范形 ⊆ 两个引擎副本）钉住，**这条差异故意不钉**（对照变异实测 **GREEN**）。

### 24.6.2 `_STRONG_DIRECTIONS` 在 **5 处**逐字重复（且容器类型不一致）

```python
app/replay/metrics.py:33                        _STRONG_DIRECTIONS = {"YES", "NO"}   # set
app/services/execution_quality_service.py:49    _STRONG_DIRECTIONS = ("YES", "NO")   # tuple
app/services/guardrail_service.py:63            _STRONG_DIRECTIONS = ("YES", "NO")   # tuple
app/services/market_quality_service.py:45       _STRONG_DIRECTIONS = ("YES", "NO")   # tuple
app/services/source_reliability_service.py:50   _STRONG_DIRECTIONS = ("YES", "NO")   # tuple
```

（另在 2 份设计文档 `docs/superpowers/plans/` 里也各有一份。）五处的注释都在描述**同一个契约**：
"可被降级为 WAIT 的方向；WAIT/AVOID 已是保守态，不再被降级"。
→ 这是 §18 / §19 那类"同一份数据写多遍"的实例，只是**落在常量上**（§20 的"函数体口径"看不见）。
本轮**不收敛**（改生产代码需单独拍板），仅登记。

### 24.6.3 🔴 `ENGINE_NAMES` 有 4 个成员，批量汇总却只数 **3 个桶**（`gbm` 落空）

`world_cup_quality_service.ENGINE_NAMES` = `(elo_odds, hybrid, gbm, integrated)`。
但两处批量汇总都是**无 `else` 的三段 `if/elif`**：

- `world_cup_prediction_pipeline.py:1582-1587`（`batch_predict_matches` 汇总）
- `api/routes/world_cup_predictions.py:665-670`（SSE `engine switch` 汇总）

两处都只累计 `elo_odds_count / hybrid_count / integrated_count`，**没有 `gbm_count`**
（全仓 `grep -n gbm_count` **零命中**）。前端 `engine-console.tsx:43-45` 也照这三桶渲染（`ELO n / HYB n / 融合 n`）。

→ **后果**：一次 `engine="gbm"` 的批量预测，`succeeded` 会计数，但三个桶全 0，
**控制台看起来像"什么都没跑"**。这与 §22.2.2 的 `_KNOWN_KINDS` **同形**：
**被接受的枚举值在下一跳没有落点**。（`frontend/src/lib/world-cup/engine-api.ts:40-42` 的字段均为可选，
所以它不会报类型错 —— 又一个"类型系统不会替你发现"的例子。）
**未改**：属展示 / 产品口径，需拍板。

## 24.7 明确保留的盲区（有意，不是遗漏）

| 盲区 | 为什么保留 |
|---|---|
| 两个 kernel 引擎副本里的**别名**成员（`quarter_final` / `semi_final`） | 完整性取决于"上游到底会发哪些拼写"，仓库里没有权威清单 → 硬钉 = 把猜测写成契约（同 §23.2.2） |
| `event_store._PLATFORM_NAME_SETTINGS` | 已有两道独立守卫（§24.2 第 10 项） |
| `_STRONG_DIRECTIONS` / `_OUTCOME_KEYS` 的成员 | 元素太短 + 消费形态无物可断言（§24.2） |

## 24.8 验证

| 检查 | 命令 | 结果 |
|---|---|---|
| 8 个文件 | pytest | junit `tests=108 failures=0 errors=0 skipped=0`（§24.3 的循环 + §24.4 的关系落地后为 **102**，再补 6 条钉子后 **108**） |
| Lint | `ruff check <8 个测试文件>`（`backend/ruff.toml`） | 只有 3 个**既有** F401，全在 `test_club_elo_service.py`（`io.StringIO` / `pytest` / `datetime.timedelta`，**均已在 HEAD 存在**）→ **新行零新增** |
| 行尾 | `scripts/eol_audit.py` | `line-ending damage: none`（CRLF 文件保 CRLF、两个 LF 文件保 LF、新文件 `NEW CRLF=83 bareLF=0`） |
| 变异 | 12 个变异 × 分腿 = **19 项检查** | **19/19 全部符合预期**；每次跑完**逐字节还原**，7 个被变异的 app 文件 **sha256 前后一致** |

本批新增 **17** 个用例：**循环 7**（§24.3 的第 1-7 项）、**关系 4**（§24.4 的四条）、**钉子 6**
（前 4 项各 1 + `club_elo` 两个表合并为 1 + `mlb` 1）。`backend/tests/` 这 8 个文件的 junit 计数由
**102（循环 + 关系）→ 108（补钉子）**。

## 24.9 本节未做

| 事项 | 状态 |
|---|---|
| 收敛 `_STRONG_DIRECTIONS` 的 5 处重复 | ⏳ 未动（改生产代码需单独拍板） |
| 给 `gbm` 补汇总桶（后端 2 处 + 前端 1 处） | ⏳ 未动（展示 / 产品口径） |
| 判定 `third_place` 该不该算淘汰赛 | ⏳ 未动（**需作者确认**） |
| 把本批 12 个变异登记进 `scripts/mutation_verify.py` | ⏳ 未动（同 §21.7，harness 登记待拍板） |
| §22.3.1 `_KALSHI_SPORTS_SERIES_PREFIXES` 第 8 项 | ⏳ 仍待作者确认 |
| §17.2.1 的 `/trades` 已作废视图 | ⏳ 仍未动 |

**提交状态**：本节只改测试（7 个已有文件 + 1 个新文件）+ 文档；**未提交**。

---

# 二十五、把 §24.5 的变异登记进常驻 harness（含 §21.7 的 identity 守卫）—— 5 套 / 44 个变异（2026-09-28）

> 承接 §24.9 的「把本批 12 个变异登记进 `scripts/mutation_verify.py`」与 §21.7 的「identity 守卫登记」。
> 本节**改的是工具**（`backend/scripts/mutation_verify.py` + `backend/tests/test_mutation_verify.py`），
> 生产代码一字未动。

## 25.1 为什么要登记，而不是继续用一次性脚本

§23 / §24 的变异验证都跑在**仓库外的一次性脚本**上，跑完即删。后果是：
**那 19 项检查只在当时成立过这一次**，而它们守着的守卫是**永久**的 —— 半年后没人能把它重跑一遍。
本仓自己的规矩（§十一 的 harness 合并、以及技能里的"常驻探针必须有守护测试 + 变异 harness"）
指向同一个结论：**被验证过的东西要能被再验证一次。**

## 25.2 新增第 5 套 `whitelist-fixtures`（W1–W14）

| # | 变异 | 目标文件 | 守卫 |
|---|---|---|---|
| W1 | `_ALLOWED_ROLES` 删 `bench` | `football_live_availability_service` | `test_the_role_list_is_pinned` |
| W2 | `_ALLOWED_STATUSES` 删 `suspended` | `football_live_schedule_service` | `test_the_status_list_is_pinned` |
| W3 | `_SETTLED_STATUSES` 删 `determined` | `kalshi_event_source` | `test_the_settled_status_list_is_pinned` |
| W4 | `_SKIP_PLAYER_STAT_PATHS` 删 `substitutes.bench` | `world_cup_statistics_source` | `test_the_skip_list_is_pinned` |
| W5 | `_SUFFIXES` 删 `ac.` | `club_elo_service` | `test_the_affix_tables_are_pinned` |
| W6 | `_PREFIXES` 删 `sc` | `club_elo_service` | 同上（一条钉子同时保护两个表）|
| W7 | `_MLB_PLAYOFF_TYPES` 删 `W` | `mlb_adapter` | `test_the_playoff_game_types_are_pinned` |
| W8 | `ENGINE_NAMES` 删 `gbm` | `world_cup_quality_service` | `test_engine_names_match_the_runnable_registry` |
| W9 | `_STAGE_MAP` 别名目标 → `"quarterfinals"` | `world_cup_prediction_pipeline` | `test_stage_map_values_partition_into_knockout_and_group_stages` |
| W10 | `_KNOCKOUT_STAGES` 增死成员 `round_of_32` | 同上 | `test_pipeline_knockout_names_are_producible_by_the_stage_map` |
| W11 | `elo_odds_engine._KNOCKOUT_STAGES` 删 `final` | `kernel/engines/elo_odds_engine` | `test_kernel_engines_recognise_every_canonical_knockout_stage` |
| W12 | 共享助手放回**功能等价**的本地副本 | `limitless_event_source` | `test_text_number_and_probability_helpers_are_the_shared_objects` |
| W13 | `_KNOWN_KINDS` 成员违反 `.lower()` 归一化 | `sports_fact_service` | `test_every_known_kind_passes_validation` + `test_the_whitelist_matches_the_documented_kinds` |
| W14 | `_KNOWN_KINDS` 删 `availability` | `sports_fact_service` | `test_the_whitelist_matches_the_documented_kinds` |

W12 / W13 / W14 顺带把 §21.7（identity）与 §二十三（白名单钉子）的守卫**补登记**了 —— 它们和 W1–W11 同族。

### 25.2.1 🔴 **只登记一半**：7 条「循环腿」不进 harness（有意）

W1–W8 在原地是**成对**落的两条用例：循环（`for m in CONST`）+ 钉子。本节**只登记钉子那条**。
原因不是省事：harness 的 phase 2 断言的是「**变红**」，一条**期望保持 GREEN** 的变异会被读成失败。
那半边的实测结论（**7/7 循环全绿**）留在 §24.5。

**这条已写进该套的 `rationale`** —— 否则下一个人看到"W1–W8 的循环腿没登记"会好心补上，
一补上就把"**盲区**"读成了"**守卫正常**"。

## 25.3 一条从此可复现的证据：W12 的 `1 failed, 1 passed`

`verify` 输出里 W12 的变异轮是：

```
mutated run -> 1 failed, 1 passed, 15 deselected, 2 subtests passed
```

那个 `1 passed` 就是**行为用例**（换成功能等价的本地副本后照样过），`1 failed` 才是 identity 断言。
**§21.5 的「行为测试对重复是盲的」从此可由一条命令复现**（`verify whitelist-fixtures`），
不再依赖任何人的记忆或一张一次性截图。

## 25.4 验证

| 检查 | 命令 | 结果 |
|---|---|---|
| 静态清单守卫 | `pytest tests/test_mutation_verify.py` | **8 passed, 2 subtests passed** —— 14 条新锚点各命中**恰好 1 次**、无裸 LF、守卫名齐、`old != new`、`note` 非空 |
| 新集四阶段 | `mutation_verify.py verify whitelist-fixtures` | **14/14 全部通过**：phase1 绿 → phase2 红 → phase3 还原后绿 → phase4 sha256 一致 |
| 全量四阶段 | `mutation_verify.py verify`（**5 套 / 44 个变异**）| **44/44 通过**（18m24s，退出码 0）—— 见 §25.4.1 |
| Lint | `ruff check scripts/mutation_verify.py tests/test_mutation_verify.py` | `All checks passed!` |
| 行尾 | `scripts/eol_audit.py` | `line-ending damage: none`；且**被变异的 9 个 app 文件根本不在"已改动"列表里** —— 与 HEAD 逐字节相同，这就是还原的直接证据 |

### 25.4.1 全量 44 个变异的结果

在加入第 5 套之后跑了**一次全量** `verify`（**18 分 24 秒**，退出码 0）：

```
=== daily-digest        — 每日情报摘要（F1/F2/F3/F5/F6） ===
=== review-queue        — 复核队列 enabled 回显 + overlay 开关块 + CLI 提示（C1–C6） ===
=== probability-probe   — 标度错位探针（P1–P15） ===
=== voided-trade        — 作废预测时冻结其模拟交易（V1–V4） ===
=== whitelist-fixtures  — 白名单 / 枚举常量的「循环 + 钉子」与关系型守卫（W1–W14） ===

=== 44 个变异校验完毕 ===
全部通过：每个变异在其修复被回退时都会让对应守卫变红，且还原后逐字节一致。
```

| 判据 | 结果 |
|---|---|
| `[OK ]` 条数 | **44 / 44** |
| `[FAIL]` 条数 | **0** |
| `bytes restored False`（还原后 sha256 不一致）| **0** |
| `red after mutation False`（变异没让守卫变红）| **0** |

→ 这次全量跑的意义有两层：① 新套 14 条**在完整清单里**同样通过；
② 它同时是**对 harness 本身改动的回归检查** —— 本节对 `mutation_verify.py` 的改动是**纯追加**
（一句 docstring、一组路径常量、一个追加的 `MutationSet`），引擎一行未动，四套既有变异因此
**原样通过**。这两点合起来才构成"改动无害"的证据，不是单看新套绿。

📌 **一条经验**：`verify > f.txt` 在跑完前文件**始终是 0 字节** —— Python 重定向时全缓冲，
看不到任何进度。要观察进度就别重定向（或加 `-u`）。本次 18 分钟里只能靠
`git status --porcelain -- backend/app` 为空、以及进程仍在，来推断"当下不在变异态"。

## 25.5 结案（追加式，不改旧文）

- **§21.7**「把 identity 守卫登记进 `scripts/mutation_verify.py`」→ 由 **W12** 结案。
- **§24.9**「把本批 12 个变异登记进 `scripts/mutation_verify.py`」→ 由 **W1–W11**（11 条）+ W12 结案。
- W13 / W14 是**额外**补登记（§二十三 的 `_KNOWN_KINDS` 守卫），原不在两份清单里。

## 25.6 状态

- 本节改 2 个**已跟踪**文件：`backend/scripts/mutation_verify.py`、`backend/tests/test_mutation_verify.py`。
- 两者都是 **LF**（HEAD LF / 树 LF），`eol_audit` 无 damage。
- `tests/test_mutation_verify.py` 的 `test_the_four_sets_are_present` 已按该文件自己的要求
  改名/改为 `test_the_five_sets_are_present` 并追加 `"whitelist-fixtures"` —— 那个断言是**故意有序且精确**的
  （注释原话：*"an inventory nobody has to touch is an inventory that can rot"*）。
- **未提交**。

## 25.7 本节未做

| 事项 | 状态 |
|---|---|
| `_STRONG_DIRECTIONS` 的 5 处重复是否收敛 | ⏳ 仍待拍板（§24.6.2）|
| 给 `gbm` 补汇总桶（后端 2 处 + 前端 1 处）| ⏳ 仍待拍板（§24.6.3）|
| `third_place` 该不该算淘汰赛 | ⏳ 仍待作者确认（§24.6.1）|
| §22.3.1 `_KALSHI_SPORTS_SERIES_PREFIXES` 第 8 项 | ⏳ 仍待作者确认 |
| §17.2.1 的 `/trades` 已作废视图 | ⏳ 仍未动 |

---

# 二十六、把 §24.6.1 / §22.3.1 两条「待作者确认」查到证据层，并把淘汰赛白名单的**户数**从 3 更正为 5（2026-09-28）

> 承接 §24.6.1（三份 `_KNOCKOUT_STAGES`）、§24.6.3（`gbm` 落空）、§22.3.1（Kalshi 第 8 项）与 §25.7 的未做清单。
> **本节只查事实：生产代码一字未改**，唯一改动是新增测试模块的 **docstring 措辞**（§26.6）。
> 结论里有一条**扩大**（3 → 5 户）、两条**收窄**（§24.6.3 的可达路径、§22.3.1 的"漏项"前提）。

## 26.1 口径

只问三个问题，前两个可机械回答，第三个不是：

| # | 问题 | 可否机械回答 |
|---|---|---|
| ① | 同一件事在仓里**共有几处**？ | ✅ 普查 |
| ② | 这几处**是否给出不同答案**？ | ✅ 可复现 |
| ③ | **谁对谁错**？ | ❌ 不代决（§24.6.1 / §22.3.1 的挂账**一分不减**）|

**本节只交付 ① 与 ②。**

## 26.2 淘汰赛白名单全户普查：不是 3 处，是 **5 处定义 + 1 处 import 复用**

§24.6.1 数的是**带 `_KNOCKOUT_STAGES` 这个名字的定义**（3 处）。
按**行为**数（"谁在判 is_knockout"），另有 2 处把同样的名单写在**函数体里**：

| # | 位置 | 形态 | 成员 | 对判定输入做的归一化 |
|---|---|---|---|---|
| 1 | `world_cup_prediction_pipeline.py:196` | 具名 `set` | **4** `round_of_16, quarterfinal, semifinal, final` | 先过 `_STAGE_MAP`（:172-193）取**规范形** |
| 2 | `kernel/engines/elo_odds_engine.py:47` | 具名 `frozenset` | **6** +`quarter_final, semi_final` | `.lower().strip()` |
| 3 | `kernel/engines/situational_adjust.py:20` | 具名 `frozenset` | **7** +`third_place` | `.lower().strip()` + 空格→`_` |
| 4 | `kernel/engines/gbm_engine.py:40` | **函数内联 set** | **6** `round_of_16, quarter_final, semi_final, final, knockout, playoff` | 只 `.lower()` |
| 5 | `world_cup_verified_result_correction_service.py:253` | **函数内联 set** | **9** `round_of_32, round_of_16, quarterfinal, quarterfinals, semi_final, semifinal, semifinals, third_place, final` | `.strip().lower()` + `-`/空格→`_` |
| （复用）| `sports/football/engines/football_multi_factor_engine.py:44` | `from … import _KNOCKOUT_STAGES` | = **#2** | 同 #2 |

📌 **第 6 处不是副本，是真 `import`**（`from app.kernel.engines.elo_odds_engine import _KNOCKOUT_STAGES`）。
**这正好是 §19.2 判据的正例**：这一处**共享契约**，所以它**已经收敛**了；其余 5 处各自为政。

🔴 **两个内联副本是本节新增的发现**（§24.6.1 漏了它们）：
- **#4 `gbm_engine` 缺两个规范形**（`quarterfinal` / `semifinal`），反而多了 `knockout` / `playoff`；
- **#5 结果校正服务 9 个成员**、含复数形，且**含 `third_place`**。

## 26.3 `third_place`：5 个判定点里 **2 个**算淘汰赛 —— 而且这不是"格式差异"

对 `third_place` 表态的一共 5 处（#6 随 #2）：

| 判定点 | `third_place` 算淘汰赛？ |
|---|---|
| #1 pipeline `is_knockout` | ❌ |
| #2 `elo_odds`（及复用它的 #6）| ❌ |
| #3 `situational_adjust` | ✅ |
| #4 `gbm_engine` | ❌（不在名单里）|
| #5 **结果校正服务** | ✅ |

→ **4 / 6 / 7 的成员数差异**可以归因于"各自输入拼写不同"（§24.6.1 已说明）。
但 `third_place` 是**同一个词**：它在 #3 / #5 里算、在 #1 / #2 里不算 —— **归一化宽严解释不了这个**。

🔴 **直接后果（读代码即得，不需运行）**：一场三四名决赛
- `world_cup_verified_result_correction_service.py:224` 认为它**不许打平** →
  `home_score == away_score` 时**强制**要求 `winner` + `penalty_score`，
  否则返回 `knockout_draw_requires_winner` / `knockout_draw_requires_penalty_score`（**拒绝该次校正**）；
- 而 pipeline 的 `is_knockout=False` → 传到 `calculate_elo_win_probability(is_knockout=False)`
  （`world_cup_prediction_pipeline.py:240`）→ **不做平局修正**，模型会给出可观的平局概率。

**同一场球：一侧说"不许平"，另一侧按"可平"建模。** 谁对**仍不代决**，
但这条**不再是"可能"** —— 它写在两处代码里。

📌 **顺带更正 §24.6.1 的一句**：那里说 `third_place` 的差异"**故意不钉**"。按字面**不准** ——
本节新增的 `test_stage_map_values_partition_into_knockout_and_group_stages` 断言的补集**恰等于**
`{"group_stage", "third_place"}`，把 `third_place` **钉在非淘汰赛一侧**（W9 变异即由此变红）。
它**不是盲区，是一个被钉住的建模决定**：翻案要**显式改断言**。这正是夹具该有的样子。

## 26.4 `gbm`：两条补强 + **一条收窄**

**补强 1 —— `gbm` 是"有意"的一等 engine，有 commit 为证。**
`f2610b9`（2026-08-13）message 原话：*"Also aligns `PredictionEngine` with reality: the pipeline
dispatches `"gbm"` and the engine-comparison card posts it, but the `Literal` omitted it."*
→ 它不是遗留值，是**被明确对齐过**的一等成员。§24.6.3 的"被接受的枚举值在下一跳没有落点"因此成立。

**补强 2 —— `gbm_engine` 自己的白名单（#4）缺规范形。**
`is_knockout = (match.stage or "").lower() in {...}`：名单里是 `quarter_final` / `semi_final`，
**没有** pipeline 会产出的规范形 `quarterfinal` / `semifinal`，也**不做** `.strip()` / 空格归一。
→ 与 §24.6.1 警告的是**同一机制**（"引擎副本掉一个成员 → 该轮静默变成可平"），
只是这里不是"将来会掉"，是**名单一开始就没写**。
⚠️ **但这不等于活缺陷**：实际拼写取决于 `MatchIdentity.stage`，而适配器把 `fixture.stage`
**原样**传进去（`sports/football/adapters/world_cup_adapter.py:121`）；`fixture.stage` 的真实分布
本节未测 → **按 §18 的规矩标"潜伏"，不标"活跃"**。

**🔴 收窄（1）—— §24.6.3 说"控制台看起来像什么都没跑"，这句**过头了**。**
`engine-console.tsx`：引擎选择器 `ENGINES`（:15-20）是 4 项 `elo_odds / hybrid / integrated /
high_confidence`，**不含 `gbm`** —— 唯一的缺桶（`gbm`）**恰好也是唯一选不到的**。
而 `high_confidence`（**选择器里有、汇总里没有桶**）**不是第二个洞**：pipeline 在持久化**之前**
把 `selected_engine` **改写成**它选中的具体引擎（`:1048-1082`），所以计数拿到的是
`elo_odds|hybrid|integrated` 之一，3 个桶对它**是对的**。

**🔴 收窄（2）—— 前端 batch 路径其实自洽；不一致在**后端**。**
`frontend/src/lib/world-cup/engine-api.ts:30`
`EngineName = "elo_odds" | "hybrid" | "integrated" | "high_confidence"`（**无 `gbm`**），
与 `BatchSummary` 的 3 个 `*_count` + high_confidence 的改写规则**互相自洽**。
真正不一致的是**后端**：batch 路由的 `engine` 参数用 6 成员 `PredictionEngine`（含 `gbm`，
`world_cup_predictions.py:536/602` 的 description 里明写 "gbm"），而汇总只有 3 个桶。

→ 修正后的说法：**`gbm` 批量只能从 API 直接发起，UI 不可达。**
"看起来什么都没跑"是**API 场景**，不是 UI 场景 —— **性质不变**（同一枚举在下一跳无落点），
只是**可达路径从 UI 改回 API**。

## 26.5 §22.3.1 更正：那个重复**自引入提交起就在**，"第 8 槽本意"在仓里**无迹可寻**

§22.3.1 的读法是"第 8 个槽位**本该是另一个联赛前缀**（如 WNBA / NCAA）"。查历史后**这个前提不成立**：

```
$ git log --oneline --follow -- backend/app/services/kalshi_sports_source.py
4dbf90b feat(phase11): add Kalshi sports source + config + .env.example   ← 引入该文件
f2610b9 fix(typing): clear the nine worst mypy files … (#14)             ← 未碰这个元组

$ git show 4dbf90b:backend/app/services/kalshi_sports_source.py | head -30
_KALSHI_SPORTS_SERIES_PREFIXES = (
    "KXNBAGAME", "KXMLBGAME", "KXNHLGAME",
    "KXSOCCEREPL", "KXSOCCERUCL", "KXSOCCERWCS",
    "KXNFL", "KXNBAGAME",
)
```

→ 第 8 项**从文件诞生的第一版就是这个重复**；**没有任何一次提交"替换掉"过某个联赛**。
"本该是 WNBA / NCAA"**是推测，不是可考古的事实**。收窄为：

| 原判（§22.3.1） | 更正 |
|---|---|
| "疑似**漏项**"（暗示知道缺哪个） | **自始存在的惰性重复**；`startswith(tuple)` 下**完全无效果**（重复项永远不是第一个匹配）|
| "那类体育市场会被静默丢出过滤" | **成立但不可归因**：缺哪些联赛是**覆盖策略**问题，仓库里没有"应含联赛"的清单可对照 |

📌 **辅证**：同一元组把足球写成**三个**前缀（`KXSOCCEREPL/UCL/WCS`）而不是一个 `KXSOCCER`
→ 读起来是**刻意收窄范围**，而不是"照着一张完整联赛表抄漏了一行"。
**结论降级为：可安全删掉第 8 项（纯清理，行为不变）；补哪个联赛是产品决定。**

## 26.6 本节唯一的改动：测试模块 docstring 措辞

`backend/tests/test_knockout_stage_whitelist_consistency.py` 的模块 docstring 原写
"the same whitelist exists in **three** modules"。按 §26.2 的普查这**只对"具名常量"成立**，
对"行为"不成立 → 改为：三个具名常量 + 点名两个内联副本 + 点名那处 `import` 复用，
并把 §26.3 的"被钉住的建模决定"写进去。**测试逻辑（3 条守卫）与断言一字未改。**

## 26.7 提交前再验证

| 检查 | 命令 | 结果 |
|---|---|---|
| 13 个改动测试文件（全批）| `pytest … --junitxml` | **tests=214 failures=0 errors=0 skipped=0** |
| 两个守卫文件（docstring 改动后）| `pytest tests/test_knockout_stage_whitelist_consistency.py tests/test_mutation_verify.py` | **11 passed, 2 subtests passed** |
| Lint | `ruff check`（新测试 + 两个工具文件）| `All checks passed!` |
| 行尾 | 字节计数 | 新测试文件 **CRLF=94 / bareLF=0**（Edit 未破坏 CRLF）|

📌 判据照旧用 junit 的 `failures` / `errors`，**不看 stdout 的 passed**。

## 26.8 本节未做

| 事项 | 状态 |
|---|---|
| 收敛 5 处淘汰赛白名单（含两处内联）| ⏳ 未动（改生产代码，待拍板）|
| 让 `gbm_engine` 认规范形 `quarterfinal`/`semifinal` | ⏳ 未动（**是否活缺陷取决于 `fixture.stage` 实际拼写，本机未有判据**）|
| 删 `_KALSHI_SPORTS_SERIES_PREFIXES` 第 8 项 | ⏳ 未动（纯清理、行为不变，但仍是生产代码）|
| 汇总桶对齐 `ENGINE_NAMES`（后端 2 处；前端 1 处随类型一并）| ⏳ 未动（§24.6.3）|
| 控制台是否该给操作员 `gbm` | ⏳ 未动（产品口径）|
| §17.2.1 `/trades` 已作废视图 | ⏳ 仍未动 |

**提交状态**：本节只改**文档**（本文件 + 一个测试模块的 docstring）；生产代码一字未动。**未提交。**

---

# 二十七、把 `MatchIdentity.stage` 的**生产者**查出来：两处消费者在测它们**收不到**的拼写，而 `third_place` 没有任何生产者（2026-09-28）

> 承接 §26.4 补强 2（"实际拼写取决于 `fixture.stage`，本节未测 → 标潜伏"）与 §26.3（`third_place` 的"相反前提"）。
> **本节只读代码**，生产代码一字未改。结论里**一条升级**（潜伏 → 由真实数据触发）、**一条降级**（§26.3 的相反前提不可达）。

## 27.1 生产者只有一个：`world_cup_match_service.parse_fixture`

```
provider round ──parse_fixture──▶ {"stage": …} ──save_fixtures_to_db──▶ MatchFixture.stage
                                     (world_cup_match_service.py:197 `MatchFixture(**fixture_dict)`)
MatchFixture.stage ──world_cup_adapter.py:121 `stage=fixture.stage or "group_stage"`──▶ MatchIdentity.stage
MatchIdentity.stage ──kernel engines / pipeline 的 challenge adapter──▶ is_knockout
```

`parse_fixture`（`:103-127`）的判定链，与**它实际能产出的词表**：

| round 子串 | 产出 |
|---|---|
| `"group"` | `group_stage` |
| `"final"`（且不含 `semi`/`quarter`）| `final` |
| `"semi"` | `semifinal` |
| `"quarter"` | `quarterfinal` |
| `"16"` | `round_of_16` |
| **其它** | **`unknown`** |

→ 只有 **6 个值，全是规范形**（无分隔/下划线的**单数**）。**`quarter_final` / `semi_final` 不是其中之一**；
`third_place` 也不是。

## 27.2 🔴 升级：两处消费者读**同一个对象**，却测**生产者不发的拼写**

| 消费者 | 读什么 | 测什么 | QF/SF 判成淘汰赛？ |
|---|---|---|---|
| `kernel/engines/elo_odds_engine.py:47` | `match.stage` | 含 `quarterfinal`/`semifinal` | ✅ |
| `kernel/engines/situational_adjust.py:20` | `match.stage` | 含两者 | ✅ |
| **`kernel/engines/gbm_engine.py:40`** | `match.stage` | **只有 `quarter_final`/`semi_final`** | ❌ |
| **`conclusion_challenge_world_cup_adapter.py:96`** | `match.stage`（调用方是 pipeline，传 `MatchFixture`）| **只有 `quarter_final`/`semi_final`** | ❌ |

→ **同一次调用、同一个 `MatchIdentity`，两组消费者给出相反判定。** 后两行的共同点：
它们测的拼写**生产者从来不发** → 对 QF/SF 而言那些分支**是死的**。后果（读代码即得）：
- `gbm_engine`：`predict_match_gbm(is_knockout=False)` → QF/SF **不做平局修正**；
- `conclusion_challenge_world_cup_adapter`：`risk.level` 在 QF/SF 上算成 **`medium`**（只有 R16 与决赛才 `high`）。

→ 这把 §26.4 补强 2 **从"潜伏"升级为"由真实数据触发"**（生产者确实发规范形）。
**但仍不代决是否修**：也可能是有意的建模选择（见 §27.4 的另一种解释）。
⚠️ 无论哪种，**"错得保守"不成立**：`is_knockout=False` 的含义是**允许平局**，在淘汰赛里是**错的方向**。

📌 同时**更正 §26.2 的户数**：本节的第 4 行是 §26.2 的普查**没数到**的第 6 个消费者
（它不叫 `_KNOCKOUT_STAGES`、也不在函数内联 `set` 之外 —— 它是一个**内联在字典字面量里的集合**，
`grep '_KNOCKOUT_STAGES'` 与"函数内联 set"两种口径都会漏掉它）。
**教训：口径本身也会漏 —— "按行为数"要一路数到"任何地方对 stage 做成员测试"。**

## 27.3 🔴 降级：`third_place` **没有任何生产者**，所以 §26.3 的"相反前提"不可达

- `parse_fixture` 对 openfootball 的 `"round": "Match for third place"` → **`unknown`**（不含 group/final/semi/quarter/16）；
- 全 `app/` 内**没有任何**把 `third_place` 写进 `MatchFixture.stage` 的代码
  （`.stage =` 的写入点只有各运动适配器 + `_FIELDS` 共享层 + `historical_data_ingestor`，都与 WC 无关）。

→ **§26.3 的"同一场球两个模块前提相反"降级为"潜伏"**：要达成它，得先让 sync 生产者认这个 round。
**但换出一个更直接的结论**：`_STAGE_MAP`（`world_cup_prediction_pipeline.py:184-185`）的
`"third_place"` / `"third place"` 两个键是**没有生产者的目标** —— 不是 §22.2 那种"被接受但没被测"，
而是**被定义但没人产**（同一族的另一个方向）。

→ **而真正的现状比 §26.3 描述的更朴素**：三四名决赛被 sync 判成 **`unknown`**，
`unknown` 在下游**没有任何特殊处理**（`_STAGE_MAP` 无此键 → 原样返回 → 不在任何 `_KNOCKOUT_STAGES`）
→ 它**按"可平"建模**，与小组赛同待遇。
**这才是 §26.3 所担心的那件事的真实机制：不是两个模块打架，是归类丢失。**

## 27.4 还有第三份词表 —— 说明"归一化宽窄"不是唯一的轴

`world_cup_tournament_state_service._KNOCKOUT_STAGE_ORDER`（`:10-26`）收了 **7 种拼写**
（`quarter_final` / `quarter-final` / `quarterfinal` / `quarterfinals` / `r32` / …），
而它的输入是**归一化 sports facts**（`build_qualification_state(facts)`）—— **另一个生产者**。

→ **判据再升级（比 §26.2 的"按行为数户数"更细）**：
**每个词表要对它自己的生产者判，不能拿所有词表的并集判。**
`gbm_engine` 的 `quarter_final` **未必是"多余的别名"**：它读起来像是**对着另一个生产者的词表写的**
（facts / provider 原始串那一族），只是**被放到了读 `MatchIdentity` 的地方**（§27.2）。
所以"5 户合并成 1 户"**可能是错的处方**：正确做法是**按生产者分组**
（`MatchIdentity` 规范形一组、facts 一组、provider 原始串一组），或在**边界做一次归一化**。

## 27.5 验证

| 检查 | 命令 | 结果 |
|---|---|---|
| 工作树 | `git status --porcelain` | 与 §26 相同 —— **本节未改任何文件**（仅追加本文档）|
| 行尾 | 字节计数 | 文档仍为纯 LF |
| 回归 | 未跑 | 本节零代码改动，无需（§26.7 的 214/0/0/0 仍然有效）|

## 27.6 本节未做

| 事项 | 状态 |
|---|---|
| 给 `gbm_engine` / `conclusion_challenge_world_cup_adapter` 补规范形拼写 | ⏳ 未动（**§27.2 表明是真缺口**，仍待拍板）|
| 让 `parse_fixture` 认 `"third place"` → `third_place` | ⏳ 未动（**把归一化做在生产侧**是最省事的修法，但属生产代码）|
| 判定"5 户合并"这个处方对不对 | ⏳ 未动（**§27.4 主张按生产者分组**，需拍板）|
| §26.8 的其余各项 | ⏳ 仍未动 |

**提交状态**：本节**只读**（未改任何生产文件）；文档追加除外。**未提交。**

---

# 二十八、落地 §27.2：修两处「生产者不发的拼写」—— 本审计**首次改生产代码**（2026-09-28）

> 承接 §27.2 的两处死分支。口径：**先修 → 把内联集合提成具名常量（使其可被守卫）→ 用独立 oracle 加关系型守卫
> → 用生产者驱动探针证明缺陷真实存在 → 用变异证明守卫承重。**
> 与 §24~§27 "只查不改"不同：**本轮按 B 选项执行，动生产代码。**

## 28.1 改动（2 处生产 + 1 处测试）

| 文件 | 改动 |
|---|---|
| `kernel/engines/gbm_engine.py` | 函数内联 `set` → 模块常量 `_KNOCKOUT_STAGES`；**补 `quarterfinal` / `semifinal`**（`quarter_final`/`semi_final`/`knockout`/`playoff` 一律保留）|
| `services/conclusion_challenge_world_cup_adapter.py` | 字典内联 `set` → 模块常量 `_HIGH_RISK_STAGES`；**补 `quarterfinal` / `semifinal`** |
| `tests/test_knockout_stage_whitelist_consistency.py` | 守卫扩到 gbm（并入 `_KERNEL_ENGINE_STAGE_SETS`）+ 新增 1 条 challenge 守卫 |

**为什么必须提成常量**：内联集合**不可被 import** → 既写不了关系型守卫，也登记不进变异 harness。
提出来之后两件事都能做（§28.3、§28.4）。

**刻意只改"拼写"，不动归一化宽度**：`gbm_engine` 仍是 `.lower()`（**没有**加 `.strip()`），
challenge 仍不做大小写处理（生产者本来就发小写）。这样"改了什么"是单一变量，见 §28.5。

## 28.2 生产者驱动探针：把 oracle 从**生产者**导出，而不是手写

新判据（§27.4 的落地）：**别手写"哪些 stage 算淘汰赛"，去问生产者。** 探针（仓库外，跑完即删）三步：
1. 把真实 round 串喂进 `parse_fixture`，收集**它实际产出的 stage**；
2. **从 git HEAD 用 AST 读出修改前的集合**（`ast.walk` 找"元素全是字符串常量"的 `ast.Set`）——**不手抄**；
3. 逐格比对。

实测矩阵：

| round 串 | 生产者 stage | elo | sit | gbm | **gbm(HEAD)** | chal | **chal(HEAD)** | pipeline |
|---|---|---|---|---|---|---|---|---|
| `Group A` / `Group C - 2` | `group_stage` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `Round of 16` | `round_of_16` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `Quarter-finals` | `quarterfinal` | ✅ | ✅ | ✅ | **❌** | ✅ | **❌** | ✅ |
| `Semi-finals` | `semifinal` | ✅ | ✅ | ✅ | **❌** | ✅ | **❌** | ✅ |
| `Final` | `final` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `Match for third place` | **`unknown`** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `Round of 32` | **`unknown`** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `3rd Place Final` | **`final`** ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

- **两列 `(HEAD)` 的 ❌ 就是缺陷的实测证据**，而且旧集合是**从 git 读出来的**，不是我复述的。
- 顺带量出 §27.3 之外的两条（**均未改**，超出 §27.2 范围）：
  - **`Round of 32` 也被判 `unknown`** —— 那是 2026 世界杯**真实存在**的轮次；
  - **`3rd Place Final` 会被解析成 `final`** —— 季军战与决赛同待遇（`"final" in round_info` 命中）。

## 28.3 守卫：oracle 用**另一个常量**

oracle = `world_cup_prediction_pipeline._KNOCKOUT_STAGES`（"生产者规范形"的权威集合），
**独立于被测集合**（§24.5.1 的判据）：

```python
_KERNEL_ENGINE_STAGE_SETS = {
    "elo_odds_engine": ELO_ODDS_KNOCKOUT_STAGES,
    "gbm_engine": GBM_KNOCKOUT_STAGES,          # ← 本轮新增
    "situational_adjust": SITUATIONAL_KNOCKOUT_STAGES,
}
# test_kernel_engines_recognise_every_canonical_knockout_stage
self.assertLessEqual(set(PIPELINE_KNOCKOUT_STAGES), set(stages), ...)

# test_challenge_adapter_rates_every_canonical_knockout_stage_high_risk
self.assertLessEqual(set(PIPELINE_KNOCKOUT_STAGES), set(CHALLENGE_HIGH_RISK_STAGES), ...)
```

## 28.4 验证与变异

| 检查 | 命令 | 结果 |
|---|---|---|
| 守卫文件 | `pytest tests/test_knockout_stage_whitelist_consistency.py` | junit **tests=4 failures=0 errors=0 skipped=0**（原 3 条 → +1）|
| 受影响套件（7 文件）| `pytest test_gbm_engine / test_conclusion_challenge_world_cup_adapter / test_conclusion_challenge_service / test_factor_vote_wrapper_engines / test_knockout_… / test_world_cup_prediction_pipeline / test_world_cup_predictions_routes` | junit **tests=125 failures=0 errors=0 skipped=0** |
| Lint | `ruff check`（2 生产 + 1 测试）| `All checks passed!` |
| 行尾 | `scripts/eol_audit.py` | **`line-ending damage: none`**；两个生产文件 **CRLF 保持**（gbm 166、challenge 155，`bareLF=0`）|
| **变异** | 四阶段（仓库外脚本）| **M1 删 gbm 的 `quarterfinal` → `failures=1`；M2 删 challenge 的 `semifinal` → `failures=1`**；两者还原后均回绿，且 **sha256 逐字节一致** |

变异锚点刻意选**单行**（避免把行终止符卷进锚点，那是本仓踩过的坑）：
`"quarterfinal",` → `"quarterfinals",`、`"semifinal",` → `"semifinals",`，
脚本**断言各命中恰好 1 次**。

## 28.5 本节未做

| 事项 | 状态 |
|---|---|
| 让 `parse_fixture` 认 `"third place"` / `"round of 32"` | ⏳ 未动（§28.2 已实测出这两条**归类丢失**；属生产侧归一化，**需拍板**）|
| 处理 `"3rd Place Final" → "final"` 这个同名陷阱 | ⏳ 未动（要先定"季军战该怎么表示"）|
| 统一归一化宽度（`gbm` 加 `.strip()`；challenge 加 `.lower()`）| ⏳ 未动（**刻意保持"只改拼写"**，见 §28.1）|
| 把这 2 个变异登记进 `scripts/mutation_verify.py` | ⏳ 未动（**建议单开一批**，同 §25 的做法）|
| `_STRONG_DIRECTIONS` 5 处收敛 / `gbm` 汇总桶 / `/trades` 已作废视图 | ⏳ 仍未动 |

**提交状态**：本节**首次改生产代码** —— 2 个生产文件 + 1 个测试文件；**未提交**。

---

# 二十九、把「`_STRONG_DIRECTIONS` 5 处」从具名普查升级为行为级普查 —— 并因此查出 51 个 `git status` 完全看不见的混合行尾文件

**本节零改动**（只读 + 仓库外临时探针，跑完已删）。起因是上一轮列出的待办 D：
「`_STRONG_DIRECTIONS` 5 处的行为级普查」。做完之后有两件事改变了结论：
§24/§26 的「5 户」是**下限**（真实同义消费者 9 处、同成员集 15 处），
以及**一次顺带触发的全仓字节普查，查出 51 个工作树混合行尾文件**（`git status` 与 `git diff` 对它们完全失明）。

## 29.1 口径：为什么「5 处」是下限

§24 用的是 `grep 'CONST ='` —— **具名常量口径**。§26 与 §8d 已经确立了正确口径
（"户数要按 *行为* 数，不能只数 *具名* 副本"），**但那套判据只用在了淘汰赛白名单上**，
从没用在 `_STRONG_DIRECTIONS` 上。本节补上，并把判据再推一步：

> **按行为数完之后，必须再判一次语义** —— 因为"成员集相同"根本推不出"同一件事"。

口径（"行为"）= **任何地方对一个 direction 值做 YES/NO 成员测试**，
不限于具名常量、不限于 `set`/`tuple`、把字典字面量与 `return` 里的内联也算上。

## 29.2 成员集 `{"YES","NO"}` 在 `backend/app/` 里共 15 处

**A 组 —— 具名（8 处）**

| # | 位置 | 名字 | 形态 | 语义 |
|---|---|---|---|---|
| 1 | `replay/metrics.py:33` | `_STRONG_DIRECTIONS` | `set` | 可降级强方向（计数用）|
| 2 | `services/execution_quality_service.py:49` | `_STRONG_DIRECTIONS` | `tuple` | 可降级强方向 |
| 3 | `services/guardrail_service.py:63` | `_STRONG_DIRECTIONS` | `tuple` | 可降级强方向（护栏门）|
| 4 | `services/market_quality_service.py:45` | `_STRONG_DIRECTIONS` | `tuple` | 可降级强方向 |
| 5 | `services/source_reliability_service.py:50` | `_STRONG_DIRECTIONS` | `tuple` | 可降级强方向 |
| 6 | `services/prediction_calibration_service.py:92` | **`_DIRECTIONAL`** | `tuple` | **可评估方向正确性** |
| 7 | `services/review_queue_detectors.py:79` | **`_CALLED_DIRECTIONS`** | `frozenset` | **已下注的调用**（vs 弃权）|
| 8 | `services/domain_reliability_service.py:36` | **`_VALID_DIRECTIONS`** | `set` | **合法方向** |

**B 组 —— 内联（7 处）**

| # | 位置 | 语义 |
|---|---|---|
| 9 | `services/decision_quality_service.py:275` | 可降级强方向（Stage A 降级门）|
| 10 | `services/decision_quality_service.py:331` | 可降级强方向 —— **死分支**，见 §29.7 |
| 11 | `services/conclusion_challenge_service.py:44` | 强事件方向（函数名就叫 `_is_strong_event_direction`）|
| 12 | `services/conclusion_challenge_service.py:85` | 强事件方向（"变化太小不足支撑"）|
| 13 | `services/event_intelligence_service.py:1570` | **可交易方向**（不是 YES/NO → 落回 `"YES"`）|
| 14 | `api/routes/quality_metrics.py:366` | **已下注却未降级**（异常面板取样）|
| 15 | `memory/simulated_trade_store.py:502` | **同源循环**（按方向分组统计）|

→ §24/§26 报的「5 户」= **5 具名**；按行为数是 **15 处**；
其中**语义同义**（"YES/NO 是可被软化的强方向"）= A1–A5 + B9–B12 = **9 处**。

## 29.3 决定性的一步：同值 ≠ 同义 —— `{"YES","NO"}` 承载 5 种不同语义

§8d 有一条"**成员数不同 ≠ 分歧**"（要用归一化宽窄先把这一解释排掉）。本节补它的**反方向**：

> **成员集完全相同，也推不出"同一件事"。**

| 语义 | 消费者 | 若明天新增第 5 个方向 `HOLD`，要跟改吗？|
|---|---|---|
| ① 可降级的强方向 | A1–A5、B9–B12（**9 处**）| ✅ 全部要跟改（"`HOLD` 可不可降级"是同一个决定）|
| ② 合法输入值 | A8 + 4 值内联（`decision_quality_service:145`、`execution_quality_service:189`、`market_quality_service:116`、`source_reliability_service:469`）| ❌ 独立的另一套（跟 4 值契约走）|
| ③ 可检验方向 | A6 `_DIRECTIONAL` | ❌ 独立（"`HOLD` 有没有可检验立场"）|
| ④ 已下注的调用 | A7 `_CALLED_DIRECTIONS`、B14 | ❌ 独立（"`HOLD` 算不算下注"）|
| ⑤ 可交易方向 | B13 | ❌ 独立（"`HOLD` 能不能下单"）|

→ **处方因此分化**：
- **"把 5 处 `_STRONG_DIRECTIONS` 收敛成 1 处"是对的**（它们共享同一个会变的契约，符合 §8d 收敛判据）；
- **但"看到 `{"YES","NO"}` 就并进来"是错的** —— ②③④⑤ 只是**碰巧成员集相同**。
  （这是 §8d 反面清单「找到 N 处同义词表就直接提议合并」在 `_STRONG_DIRECTIONS` 上的翻版。）

## 29.4 同名 ≠ 同值：`_VALID_DIRECTIONS` 是两个不同集合

| 位置 | 值 | 概念 |
|---|---|---|
| `services/domain_reliability_service.py:36` | `{"YES", "NO"}` | 推荐方向 |
| `services/evidence_aggregation_service.py:45` | `{"support", "oppose"}` | 证据立场 |

两个**完全不同**的概念共用一个名字。（真正的同名同义反例是 A1–A5 那 5 份逐字相同的副本。）

## 29.5 归一化点只有 2 处，其余 13+ 处原样比对

| 位置 | 归一化 | 调用面 |
|---|---|---|
| `source_reliability_service.py:460 _normalize_direction` | `.strip().upper()` | **仅 `:396` 一处**（本文件私有）|
| `prediction_calibration_service.py:158` | `.strip().upper()` | 本函数内 |
| 其余（含 A1–A5 五个 `in` 判定、B9–B15 全部）| **无**（原样比对）| —— |

→ 同一个词会得到**不同答案**：输入 `"yes"`（小写）时上面两处**认**，
其余 13+ 处**一律不匹配** → 各自走安全默认（`_extract_raw_direction` → `"WAIT"`；
`guardrail_service` → **跳过全部护栏**；`source_reliability` 降级分支 → 不降级）。

⚠️ **本节只报事实，不给路径**：LLM 是否真的会发小写 direction 属于**上游契约**
（未取证，不外推）。可靠判据只能来自上游是否已归一化。

## 29.6 变异取证：3 个具名副本里 1 个是空头守卫

`backend/tests/` 里 **零处**按名引用这 8 个常量，且 `("YES", "NO")` / `{"YES", "NO"}`
二元字面量在测试里**出现 0 次**（唯二的 2 元组是 4 值全集 `("YES","NO","WAIT","AVOID")`）
→ **没有任何测试显式钉住这些集合的成员集**。

但"没有字面量"**推不出**"没被覆盖"（`"YES"` 作为输入值出现在 70 个测试文件里）。
所以直接变异：把 `("YES", "NO")` 削成 `("YES",)`，跑该模块自己的测试文件：

| 模块 | 变异前 (tests/f/e/s) | 变异后 | 还原 | 判定 |
|---|---|---|---|---|
| `guardrail_service` | 36/0/0/0 | 36/**2**/0/0 | ✅ sha256 一致 | **RED —— 承重** |
| `market_quality_service` | 44/0/0/0 | 44/**2**/0/0 | ✅ sha256 一致 | **RED —— 承重** |
| **`execution_quality_service`** | 12/0/0/0 | 12/**0**/0/0 | ✅ sha256 一致 | 🔴 **GREEN —— 空头！** |

→ `execution_quality_service.py:159` 的 `raw_direction in _STRONG_DIRECTIONS` 分支
**对 `"NO"` 从未被任何测试走到**：删掉 `"NO"` 后该文件 12 个用例**全绿**
（对照：`"YES"` 在该测试文件里出现 3 次、`"NO"` 一次也没有）。

**这是"驱动自常量"盲区的又一实证**：3 个结构完全一样的副本，2 个承重、1 个空头，
**光读代码分不出来**。

## 29.7 一个可直接读出的死分支

`services/decision_quality_service.py:330-333`：

```python
    if consensus_level == "none":
        if raw_direction in ("YES", "NO"):
            return "缺少可解析的证据分解，无法判断证据一致性。"
        return "缺少可解析的证据分解，无法判断证据一致性。"
```

**两个分支返回完全相同的字符串** → 条件无效果。
（性质：**纯冗余**，不是错误分支；`raw_direction` 未参与返回文本。可安全清理，需拍板。）

## 29.8 前端：第 16 处是手写 union，不走生成器

| 位置 | 内容 | 来源 |
|---|---|---|
| `frontend/src/lib/generated-types.ts:515,516,569,570,612,613` | `"YES"｜"NO"｜"WAIT"｜"AVOID"` | ✅ **生成**自 `models/event.py` 的 `Literal` |
| **`frontend/src/lib/api.ts:447`** | `direction: "YES" ｜ "NO"` | ⚠️ **手写**（`SimTrade`，不在生成文件里）|

→ 后端 `models/event.py` 的 `Literal[...]` 与 `simulated_trade_store.py:36` 的
`CHECK (direction IN ('YES','NO'))` 构成**三层同一契约**（API / DB / TS）；
其中 TS 那层**只有一半走生成器**，另一半靠人手维护。

## 29.9 由本节触发的全仓字节普查：51 个 MIXED 文件，`git status` 全盲

普查初衷是"§29.2 已数到 15 处，会不会别处还有这个集合"。
普查本身没再找出方向消费者，**却顺带量出一个与主题无关、但更值得报的事实**。

按字节画像扫 4 个目录：

| 目录 | 纯 CRLF | 纯 LF | **MIXED** | 空 |
|---|---|---|---|---|
| `backend/app`（*.py）| 261 | 57 | **12** | 0 |
| `backend/tests`（*.py）| 322 | 111 | **12** | 1 |
| `backend/scripts`（*.py）| 25 | 21 | **1** | 1 |
| `frontend/src`（*.ts/tsx）| 177 | 122 | **21** | 0 |
| **合计** | 785 | 311 | **46** | 2 |

**再用仓库自带的 `eol_audit.py --all` 交叉验证**（它覆盖全部 tracked 文件，不止代码）：
报 **52 个 DAMAGE**，其中**上述 46 个全部在内**；多出的 6 个是**5 个非代码文本 + 1 个二进制误报**：

| 多出的文件 | HEAD | TREE | `.gitattributes` 属性 | 判定 |
|---|---|---|---|---|
| `backend/.sdd/final-review-package.diff` | LF | MIXED | unspecified | **真 DAMAGE** |
| `docs/README.md` | LF | MIXED | unspecified | **真 DAMAGE** |
| `docs/superpowers/plans/2026-07-05-confidence-breakdown-diagnostics.md` | LF | MIXED | unspecified | **真 DAMAGE** |
| `docs/superpowers/plans/2026-07-05-llm-fallback-gateway.md` | LF | MIXED | unspecified | **真 DAMAGE** |
| `docs/superpowers/specs/2026-07-05-prediction-engine-safety-evidence-boost-design.md` | LF | MIXED | unspecified | **真 DAMAGE** |
| **`frontend/src/app/favicon.ico`** | **MIXED** | MIXED | **`binary: set`** | 🔴 **误报** |

→ **真实的混合行尾文件合计 51 个**（46 个代码 + 5 个文本）。

🔴 **`--all` 的二进制误报（探针缺陷，可修）**：`_candidates(True)` 直接取 `git ls-files` 全量、
**不筛 binary**。`favicon.ico` 被 `.gitattributes` 的 `*.ico binary` 保护、git 从不碰它的字节
（HEAD 与工作树**逐字节相同**），但 ICO 内部**恰好**同时含 `\r\n` 与裸 `\n`
→ 命中 signature ① 的判据 `head_crlf > 0 and tree_lf > 0` → **假阳性**
（`*.woff2`/`*.pdf` 等同类风险）。**修法**：`git check-attr binary` 为 `set` 的路径直接跳过。
⚠️ 因此 `--all` 的输出**必须人工甄别**，不能直接把计数当结论。

**46/46 全部命中 `eol_audit.py` 自己定义的 signature ②**（"a file that is now mixed,
which no checkout produces"）：`HEAD pure LF=46 / HEAD pure CRLF=0 / HEAD MIXED=0`。
但 **`git status` 对这 46 个一个都不报** —— `core.autocrlf=true` + `* text=auto`
在比对前归一化，**行尾差异被完全抹平**（`git diff --numstat` 只对 §28 真正改过内容的
`conclusion_challenge_world_cup_adapter.py` 有输出，其余 11 个 app 文件零输出）。

**归因（git 历史 + mtime，非猜测）**：
- 这批文件的 bare LF 高度**成块**（多为 1–5 个连续块，不是逐行污染）；
  例：`guardrail_service.py` **恰好 1 块 = L220–L235，正是 `_extract_category` 整个函数**；
- 该函数的引入提交是 **`7ca0862`（2026-06-30）**；
- 12 个 `app/services` 文件的**最后提交**都在 **2026-07-05 ~ 07-09**，工作树 mtime 同期；
- 46 个代码文件的 mtime 绝大多数落在 **07-03 ~ 07-19**（主力开发期）。

→ **结论**：这是**长期就地编辑累积**的工作树状态（每次"整段写入"留下一段工具自己的行尾），
**不是最近产生、也不是本节产生**。本节只**读**这些文件（变异脚本对其中 3 个做过**行内**字节替换，
已用 sha256 + 行尾画像双重核对还原，见 §29.10）。

**严重度判定（按 §8d 报告纪律，不夸大）**：
- **不影响功能**（Python/TS 都不在意）、**不影响 CI**、**不影响提交**（归一化后内容一致）；
- 只影响**字节级工具**：补丁锚点命中、副本等价性比对、sha256 清单核对；
- → 属**工作树卫生**问题，**不是 bug**。**是否规范化这 46 个文件需拍板，本节不动手。**

📌 **同时更正一条记忆（否则下次还会误判）**：
`scripts/eol_audit.py` **已经支持 `--all`**，docstring 原话——
*"With ``--all`` every tracked file is checked, which is slower but catches damage in a file
that happens to have no other differences"*，**正是这 46 个的场景**。
此前记忆只记了"无参数时只审 `git status` 里已改动的文件"，漏了这一半，
会让下一个人以为探针有盲区、另写一个。
**用法**：`cd backend && python scripts/eol_audit.py --all`
（逐文件 `git show`，数百文件时会跑到几分钟量级）。

## 29.10 验证

| 检查 | 结果 |
|---|---|
| 普查口径 | 非具名副本按"任何地方做成员测试"枚举（含字典字面量、`return` 内联）|
| HEAD 画像（46 个代码文件）| **HEAD pure LF=46 / CRLF=0 / MIXED=0**（`git show HEAD:<path>` 逐条字节计数）|
| `--all` 交叉验证 | 报 **52** = 上述 46（全部在内）+ 5 个非代码文本（`.diff`/`.md`）+ **1 个二进制误报**（`favicon.ico`，`binary: set`）|
| `git status` 独立性 | 46 个**零出现在 `git status`**；`git diff --numstat` 对它们**零输出** |
| 变异三例 | before 36/12/44 全绿 → after **RED / RED / GREEN** → 还原后 sha256 与行尾画像**双重一致** |
| 变异还原（独立复核）| 三文件行尾画像与预期相符、**不含变异字符串**（`= ("YES",)` 命中 0）；`git status` 与进场时**逐项一致** |
| `eol_audit`（默认口径）| **`line-ending damage: none`** |
| 临时产物 | 仓库外探针（`%TEMP%`）跑完已删；**本节零生产、零仓库内文件改动** |

## 29.11 本节未做

| 事项 | 状态 |
|---|---|
| 把 51 个 MIXED 文件规范化（统一 CRLF）| ⏳ 未动（**需拍板**；批量改工作树字节属高风险操作，且不影响功能/git）|
| 给 `execution_quality_service.py:159` 的 `"NO"` 分支补测试 | ⏳ 未动（§29.6 已实证它是空头；属生产侧测试改动）|
| 把 §29.6 的 3 个变异登记进 `scripts/mutation_verify.py` | ⏳ 未动（**建议与 §28.5 的 2 个变异合并成一批**）|
| 清理 `decision_quality_service.py:331` 的死分支 | ⏳ 未动（纯冗余，需拍板）|
| 收敛 5 处 `_STRONG_DIRECTIONS` | ⏳ 未动（§29.3 已判：**这 5 处收敛是对的**，但须与 ②③④⑤ 分开处理）|
| 统一 direction 归一化（13+ 处原样比对）| ⏳ 未动（要先定上游契约）|
| 前端 `api.ts:447` 手写 union 改引生成类型 | ⏳ 未动 |

**提交状态**：本节**零改动**（不改任何仓库内文件）；工作树与 §28 结束时**逐项一致**（16 个已改 + 1 个新文件）。

---

# 三十、把 §28/§29 的 4 个变异登记进常驻 harness（W15–W18），并说明**为什么第 5 个副本故意不登记**

**本节改的是 harness 自身**（`backend/scripts/mutation_verify.py`），**不改生产代码**。

## 30.1 为什么是 4 个而不是 5 个

§28 留下 2 个候选、§29 留下 3 个，表面上是 5 个。但 harness 的 `_verify_one` 是**四阶段**，
其中阶段②硬性要求**变异必须让守卫变红**：

| 候选变异 | 阶段②结果 | 可登记？|
|---|---|---|
| `gbm_engine._KNOCKOUT_STAGES` 删 `quarterfinal` | RED（§28 已实测）| ✅ |
| challenge `_HIGH_RISK_STAGES` 删 `semifinal` | RED（§28 已实测）| ✅ |
| `guardrail_service._STRONG_DIRECTIONS` 删 `NO` | RED（2 条）| ✅ |
| `market_quality_service._STRONG_DIRECTIONS` 删 `NO` | RED（2 条）| ✅ |
| **`execution_quality_service._STRONG_DIRECTIONS` 删 `NO`** | **全绿**（§29.6 实测的空头）| ❌ **登记会让 `verify` 在阶段②失败** |

→ **登记 4 个**，并在该 set 的 `rationale` 里**显式写明"没有 W19，这是故意的"**。
这一步不能省：一条**不存在的**变异如果不写清，下一个人要么以为漏了，
要么会在补完用例后重新发现一次"它原来是空头"—— 而后者正是这套 harness 存在的意义。

## 30.2 为什么不新建 set

`whitelist-fixtures` 已有 **W1–W14**，其中 **W11 正是** `elo_odds_engine._KNOCKOUT_STAGES` 删成员、
守卫**同一个** `_T_STAGE` —— §28/§29 的变异与它**同族**。追加为 W15–W18 还有一个机械好处：
守护测试 `test_the_five_sets_are_present` **精确有序**地锁了 5 个 key，新建 set 就必须改它；
追加则 5 个 set 仍是 5 个，**那份清单不用动**。

## 30.3 四条变异（全为**行内**锚点 —— 这是硬约束）

| # | 变异 | 锚点（字节） | guard 文件 | 断言用例 |
|---|---|---|---|---|
| W15 | `gbm_engine._KNOCKOUT_STAGES` 删规范形 | `"quarterfinal",` → `"quarterfinals",` | `_T_STAGE` | `test_kernel_engines_recognise_every_canonical_knockout_stage` |
| W16 | challenge `_HIGH_RISK_STAGES` 删规范形 | `"semifinal",` → `"semifinals",` | `_T_STAGE` | `test_challenge_adapter_rates_every_canonical_knockout_stage_high_risk` |
| W17 | `guardrail_service._STRONG_DIRECTIONS` 删 `NO` | `= ("YES", "NO")` → `= ("YES",)` | `_T_GUARDRAIL` | `test_fires_for_no_when_llm_degraded` + `test_two_rules_fire_preserves_existing_reason` |
| W18 | `market_quality_service._STRONG_DIRECTIONS` 删 `NO` | 同上 | `_T_MARKET_QUALITY` | `test_downgrade_no_to_wait_when_score_low` + `test_wide_spread_downgrades_no_direction` |

**锚点必须是行内的**：守护测试 `test_no_needle_carries_a_bare_lf` 要求
`old.count(b"\n") == old.count(b"\r\n")` —— 本仓**提交 LF / 检出 CRLF**，含换行的锚点只能写 CRLF。
这 4 条**都不含换行**（`0 == 0`），因此**与行尾无关**，在纯 CRLF 与 MIXED 文件上都能命中。

**W17/W18 的用例名是实测出来的，不是猜的**：先跑一次变异 + `pytest -rf`，
看到 `guardrail_service.py` 红 2 条、`market_quality_service.py` 红 2 条，再字节还原。
两个用例名都自带 `no`（`..._for_no_when_llm_degraded` / `..._downgrade_no_to_...`）——
正是"删掉 `NO` 才会红"的直接证据。

## 30.4 验证

| 检查 | 结果 |
|---|---|
| 清单 | `mutation_verify.py list` → whitelist-fixtures **18 个**（W1–W18）；全仓 **5 套 / 48 个**（原 44，+4）|
| 守护测试 | `pytest tests/test_mutation_verify.py` → junit **10/0/0/0**（8 用例 + 2 subTest）|
| **四阶段** | `verify whitelist-fixtures` → **18 个变异校验完毕，全部通过**（`EXIT=0`，耗时 **7m11s**）|
| W15 | green before ✅ / **red after（1 failed, 3 deselected）** / green after restore ✅ / bytes restored ✅ |
| W16 | green before ✅ / **red after（1 failed, 3 deselected）** / green after restore ✅ / bytes restored ✅ |
| W17 | green before ✅ / **red after（2 failed, 34 deselected）** / green after restore ✅ / bytes restored ✅ |
| W18 | green before ✅ / **red after（2 failed, 42 deselected）** / green after restore ✅ / bytes restored ✅ |
| Lint | `ruff check scripts/mutation_verify.py` → **All checks passed!** |
| 行尾（harness）| `mutation_verify.py` **纯 LF**（906 → 964 行）|
| **还原独立复核** | 4 个被变异文件**变异字符串残留 = 0、原始字符串 = 1**；行尾画像与变异前**逐一相同**（含 `guardrail_service.py` 仍是 **237/16 MIXED** —— 即**行内锚点不破坏 MIXED 状态**）；`eol_audit` → **`damage: none`** |

## 30.5 本节未做

| 事项 | 状态 |
|---|---|
| 给 `execution_quality_service.py:159` 补 `"NO"` 用例 | ⏳ 未做（补完才可登记为 **W19**；本轮已把"为什么没有 W19"写进 harness 的 `rationale`）|
| 规范化 51 个 MIXED 文件 | ⏳ 未动（需拍板）|
| 清理 `decision_quality_service:331` 死分支 | ⏳ 未动（纯冗余）|
| 收敛 5 处 `_STRONG_DIRECTIONS` | ⏳ 未动（§29.3 已判方向）|
| 统一 13+ 处 direction 归一化 / 前端 `api.ts:447` 手写 union | ⏳ 未动 |

**提交状态**：本节改 **1 个文件**（`backend/scripts/mutation_verify.py`，**本来就在改动列表里** → 工作树仍是 **16 个已改 + 1 个新文件**，数量未变）。**未提交**。

---

# 三十一、补空头：给第三个 `_STRONG_DIRECTIONS` 副本补 `"NO"` 用例，并把它登记为 W19

§30 把 4 个变异登记进 harness，**独独留下第 5 个不登记**，理由是它是**空头守卫**（删 `NO` 全绿）。
本节就把那个"待办"做掉 —— **补用例使它承重，然后登记为 W19**，让"同样的三个副本"闭环。

**本节改 2 个文件**：`backend/tests/test_execution_quality_service.py`（**新进改动列表**）
与 `backend/scripts/mutation_verify.py`（**本就在列表里**）。**不改生产代码**。

## 31.1 根因：不是抄错，是**测试覆盖面的差异**

三个副本的**生产字节完全一致**（`_STRONG_DIRECTIONS = ("YES", "NO")`）。差异全在**测试侧**：

| 副本 | 删 `NO` 后 | 为什么 |
|---|---|---|
| `guardrail_service` | 红 2 条 | 有直接钉 `NO` 的用例 |
| `market_quality_service` | 红 2 条 | 同上 |
| `execution_quality_service` | **全绿（空头）** | **共用 helper `_rec()` 把 `direction` 默认成 `"YES"`** |

`tests/test_execution_quality_service.py` 的 helper 签名是
`_rec(direction: str = "YES", ...)` —— 全部 12 个既有用例**只走 YES 那一侧**，
于是 `:159` 的 `if not executable and raw_direction in _STRONG_DIRECTIONS:`
里 `"NO"` 这个成员**从未被触达**。删掉它，行为分毫不差，测试当然不红。

**这正是 §24.5/§29.6 那条判据的又一实例**：`测试里零二元字面量 ≠ 没被覆盖`，
反之 `常量里有两个成员 ≠ 两个都被覆盖`。且它**不能用"改循环"消掉**
（循环里的 `CONST` 就是被测对象，删成员只是少跑一轮 —— 见 §30 rationale ①），
**只能用补用例消掉**。

## 31.2 补的两个用例（顺带覆盖一处从未被测的分支）

在 `test_raw_direction_wait_stays_wait` 之后插入：

| 用例 | 触发点 | 断言 |
|---|---|---|
| `test_raw_direction_no_is_downgraded_like_yes` | `direction="NO"`, `spread=20.0`（> `max_spread_pct=12`） | 不可执行 → `suggested_direction == "WAIT"`、`downgraded is True`、有 `downgrade_reason`。**这条就是 W19 的承重腿** |
| `test_no_direction_entry_price_is_complement_of_bid` | `direction="NO"`，可执行（`spread=4.0`, `liquidity=10000`） | `effective_entry_price == 100 - bid == 52.0` |

第二个用例**顺带补上另一处盲区**：生产代码 `:125`
`elif bid is not None and raw_direction == "NO": effective_entry_price = 100.0 - bid`
（"买 NO 等于卖 YES"，有效入场价 = `100 - bid`）此前**同样从未被测试**——
所有用例都是 YES 侧走 `effective_entry_price = ask`。

## 31.3 登记 W19

`whitelist-fixtures` 追加第 19 条（`title` 改 `（W1–W19）`）：

| # | 变异 | 锚点（字节） | guard 文件 | 断言用例 |
|---|---|---|---|---|
| W19 | `execution_quality_service._STRONG_DIRECTIONS` 删 `NO` | `= ("YES", "NO")` → `= ("YES",)` | `_T_EXECUTION_QUALITY` | `test_raw_direction_no_is_downgraded_like_yes` |

锚点**行内**（不含换行 → `0 == 0`），与文件行尾无关（该文件是**纯 CRLF**）。
**同时改写 `rationale` ④**：原句"⚠️ **没有 W19，这是故意的**"已过时，
改为记录**两次登记的差别** —— W17/W18 是**一次登记**（本就承重），
W19 是**补完用例才登记**，并点明"差异不是抄错，是测试覆盖面"。

## 31.4 验证

| 检查 | 结果 |
|---|---|
| 补用例前 | `pytest tests/test_execution_quality_service.py` → junit **12/0/0/0** |
| **补用例后** | 同上 → junit **14/0/0/0**（+2，无回归）|
| 测试文件行尾 | `test_execution_quality_service.py` **纯 CRLF**（333 → 384 行，`CRLF=384 bareLF=0`）|
| 清单 | `mutation_verify.py list` → whitelist-fixtures **19 个**（W1–W19）；全仓 **5 套 / 49 个**（原 48，+1）|
| 守护测试 | `pytest tests/test_mutation_verify.py` → junit **10/0/0/0** |
| **四阶段** | `verify whitelist-fixtures` → **19 个变异校验完毕，全部通过**（`EXIT=0`）|
| **W19** | green before ✅ / **red after（1 failed, 13 deselected）** / green after restore ✅ / bytes restored ✅ |
| Lint | `ruff check scripts/mutation_verify.py` → **All checks passed!** |
| 行尾（harness）| `mutation_verify.py` 仍 **纯 LF**（964 → 980 行）|
| **还原独立复核** | 24 个被变异文件对**变异前 sha256 快照**逐条比对 → **changed = 0**；`execution_quality_service.py` **变异字符串残留 = 0、原始字符串 = 1**；`eol_audit` → **`damage: none`** |

**为什么快照比对是必要的**：`verify` 会**就地改写真实工作树文件**（硬杀可能留变异态），
所以跑之前对全部 24 个被触及文件落了 `sha256 + 行尾画像`，跑完**逐条复核**——
比 harness 自报的 `bytes restored True` 更硬（后者只比它改过的那一个文件）。

## 31.5 本节未做

| 事项 | 状态 |
|---|---|
| 规范化 51 个 MIXED 文件 | ⏳ 未动（需拍板）|
| 清理 `decision_quality_service:331` 死分支 | ⏳ 未动（纯冗余）|
| 收敛 5 处 `_STRONG_DIRECTIONS` | ⏳ 未动（§29.3 已判方向：收敛 5 处对、并进另 4 种语义错）|
| 统一 13+ 处 direction 归一化 / 前端 `api.ts:447` 手写 union | ⏳ 未动 |
| 其余 direction 成员集的**空头普查** | ⏳ 未做（§29.2 的 9 处同义副本里，只有具名 5 处各做过一次变异取证；另 4 处异名/内联未逐一取证）|

**提交状态**：本节改 **2 个文件** → 工作树 **17 个已改 + 1 个新文件**（原 16+1，
新增的是 `backend/tests/test_execution_quality_service.py`）。**未提交**。

---

# 三十二、方向成员集空头普查：把 §29.2 剩下的 12 处逐一削 `NO` 跑测试（并入 W20–W23）

§29.6 只对 3 个具名副本做了变异取证，§31 补掉了其中那个空头。本节把 §29.2 表里**剩下的
12 处**（A1、A5–A8、B9–B15）逐一做同样的取证 —— 即"把 `{"YES","NO"}` 削成 `{"YES"}`，
跑测试看是否变红"。**结论推翻了一条直觉：承重与否，不能按"名字/语义是否同义"推。**

**本节改 1 个文件**：`backend/scripts/mutation_verify.py`（**本就在改动列表里**）。
**不改生产代码**（普查用的变异全部还原）。

## 32.1 方法：两阶段（窄集 → 宽集）+ 跑前 28 文件快照

一个副本"变异后本模块测试没红"，**不等于**它是空头 —— 也可能只是**本模块的测试文件**没覆盖它
（别处的集成测试覆盖了）。所以每一处跑**两阶段**：

| 阶段 | 跑什么 | 判定 |
|---|---|---|
| **窄** | 该模块**自己的**测试文件（如 `tests/test_source_reliability_service.py`）| RED → **承重**，收工 |
| **宽** | 仅当窄集 GREEN 时：`grep -rl` 出**所有引用该模块**的测试文件（**剔除 live/网络类**，如 `test_integration_live.py`），全部跑一遍 | 宽集仍 GREEN → **真空头** |

**探针写在仓库外**（`%TEMP%` 下），跑后删除；`--junitxml` 也落 Temp，只从 junit 读
`failures`/`errors`（**不读 stdout 的 `passed/failed` 行** —— 本机 `[safe-delete]` 会吞掉 summary）。
**跑前对全部 28 个被变异文件落 `sha256 + 行尾画像` 快照，跑完逐条比对 `changed = 0`**
（探针每处都会就地改写真实工作树文件；这是 §31 沿用下来的硬复核）。

## 32.2 全表：15 处的最终判定

| # | 位置 | 语义 | 窄集（用例/失败）| 宽集 | 判定 |
|---|---|---|---|---|---|
| A1 | `replay/metrics.py:33` `_STRONG_DIRECTIONS` | ① 可降级强方向 | GREEN | `test_replay_cli.py` n=44 **GREEN** | 🔴 **空头** |
| A2 | `execution_quality_service.py:49` | ① | —（§29.6 已测）| — | 🔴 空头 → **§31 补用例 → W19** |
| A3 | `guardrail_service.py:63` | ① | —（§29.6）| — | ✅ 承重 → **W17** |
| A4 | `market_quality_service.py:45` | ① | —（§29.6）| — | ✅ 承重 → **W18** |
| A5 | `source_reliability_service.py:50` | ① | n=61 **f=1** | — | ✅ 承重 → **W20** |
| A6 | `prediction_calibration_service.py:92` `_DIRECTIONAL` | ③ 可检验方向 | n=54 **f=3** | — | ✅ 承重 → **W21** |
| A7 | `review_queue_detectors.py:79` `_CALLED_DIRECTIONS` | ④ 已下注的调用 | n=87 **f=3** | — | ✅ 承重 → **W22** |
| A8 | `domain_reliability_service.py:36` `_VALID_DIRECTIONS` | ② 合法方向 | n=38 **f=2** | — | ✅ 承重 → **W23** |
| B9 | `decision_quality_service.py:275` 内联 | ① | GREEN | n=155 **GREEN** | 🔴 空头 |
| B10 | `decision_quality_service.py:331` 内联 | ①（**死分支**）| GREEN | n=23 **GREEN** | 🔴 空头（死分支，必空）|
| B11 | `conclusion_challenge_service.py:44` 内联 | ① | GREEN | n=157 **GREEN** | 🔴 空头 |
| B12 | `conclusion_challenge_service.py:85` 内联 | ① | GREEN | n=157 **GREEN** | 🔴 空头 |
| B13 | `event_intelligence_service.py:1570` 内联 | ⑤ 可交易方向 | GREEN | n=142 **GREEN** | 🔴 空头 |
| B14 | `api/routes/quality_metrics.py:366` 内联 | ④ 已下注却未降级 | GREEN | n=126 **GREEN** | 🔴 空头 |
| B15 | `memory/simulated_trade_store.py:502` 内联 | 同源循环 | n=15 **GREEN** | （无宽集）| 🔴 空头 |

→ 15 处合计：**承重 6（A3/A4/A5/A6/A7/A8）、空头 9（A1/A2/B9–B15）**。

## 32.3 🔴 三条因此可下判据的事实

**① 承重与否，不能按"名字/语义是否同义"推 —— 直觉得到的答案是反的。**

| 分组 | 处数 | 承重 | 空头 |
|---|---|---|---|
| **语义同义**（① 可降级强方向 = A1–A5 + B9–B12）| 9 | **3** | **6** |
| 异名常量（②③④ = A6/A7/A8）| 3 | **3** | 0 |
| 其它内联（⑤ + 杂 = B13/B14/B15）| 3 | 0 | 3 |

直觉会说"9 处同义副本里总有几处是抄过来没测的" —— 实测**恰恰是**：语义最同义的那一组
**6/9 是空头**；而**异名的三个常量全部承重**。→ **判据只有变异。**

**② "测试文件里有 `"NO"`" 既不充分、也不必要。**

| 位置 | 测试文件里 `"NO"` 出现 | 结果 |
|---|---|---|
| B11/B12 `conclusion_challenge` | **0 次** | 空头（意料之中）|
| B13 `event_intelligence` | 1 次 | 空头 |
| A1 `replay/metrics` | 4 次 | 空头 |
| B14 `quality_metrics` | **8 次** | 🔴 **空头** |
| B15 `simulated_trade_store` | **15 次** | 🔴 **空头** |

**"有 NO" 推不出"NO 被覆盖"**：B14/B15 有大把 `"NO"`，仍全绿。所以 §29.6 那句
"没有字面量推不出没被覆盖"要补上**反方向**：**有字面量也推不出被覆盖。**

**③ 空头有三种不同的成因，修法不同。**

| 成因 | 实例 | 修法 |
|---|---|---|
| **(a) 夹具默认值把一侧锁死** | A2（helper `_rec(direction="YES")`）、A1（`downgrades_caused` 两个用例都用 `base=YES`）| 补一条 `direction="NO"` 的真实用例 |
| **(b) 缺的是"合取"，不是缺 NO** | B14：测试里 `"NO"` 有 8 次，但**没有一处**把 `wide_spread_flag` **与** `final_displayed_direction=="NO"` **与** `eid` 三者凑在一条记录上 | 补一条把三个条件**同时**满足的记录 |
| **(c) 断言是负向的** | B15：全仓**唯一**一条 `by_direction` 断言是 `assertNotIn("NO", stats["by_direction"])` | 补一条**正向**断言（某 NO 交易**确实**进 `by_direction`）|

→ 🔴 **负向断言（`assertNotIn` / 期望为空 / 期望 `[]`）天生对"该分支根本不存在"盲**：
删掉 `("YES","NO")` 里的 `"NO"` 后，"不产出 NO" 与"产出了但被正确过滤掉"**给出一模一样的断言结果**。
这是"空头"里最隐蔽的一类 —— 它**看起来**有一处很贴切的断言。

## 32.4 并入 W20–W23（不新建 set）

4 个**承重**的异名/同名常量登记进 `whitelist-fixtures`（W1–W23）：

| # | 变异 | 锚点（行内，唯一）| guard 文件 | 断言用例 |
|---|---|---|---|---|
| W20 | `source_reliability_service._STRONG_DIRECTIONS` 删 `NO` | `= ("YES", "NO")` → `= ("YES",)` | `_T_SOURCE_RELIABILITY` | `test_downgraded_flag_true_when_suggested_differs` |
| W21 | `prediction_calibration_service._DIRECTIONAL` 删 `NO` | 同上 | `_T_PREDICTION_CALIBRATION` | `test_case_insensitive` + `test_no_recommendation_no_outcome_correct` + `test_full_resolution_no_correct` |
| W22 | `review_queue_detectors._CALLED_DIRECTIONS` 删 `NO` | `frozenset({"YES", "NO"})` → `frozenset({"YES"})` | `_T_REVIEW_QUEUE_DETECTORS` | `test_high_value_downgraded_fires_for_a_no_call_too` + `test_fires_when_a_confident_no_call_resolves_yes` + `test_a_partial_resolution_above_zero_counts_as_yes` |
| W23 | `domain_reliability_service._VALID_DIRECTIONS` 删 `NO` | `= {"YES", "NO"}` → `= {"YES"}` | `_T_DOMAIN_RELIABILITY` | `test_no_direction_correct_support` + `test_no_direction_wrong_support` |

**守卫用例名全部实测自失败列表**（不是猜的）。三条自检：
- **W21 的 `test_case_insensitive` 同名的有两个**（另一个在 `compute_confidence_bucket` 的测试类里，
  与本变异无关）→ 变异后 `-k` 选中 2 条，**只红 1 条**（实测 `3 failed, 1 passed, 50 deselected`
  里的那个 `1 passed` 就是它）。这是"用例名不唯一"的可接受情形：阶段②只要求**至少一条红**。
- **W22 的 `test_high_value_downgraded_fires_for_a_no_call_too` 名字自带 `no`**；W23 两条都叫
  `test_no_direction_*`；W21 的 `test_case_insensitive:167` 断言的是**小写 `"no"`**（归一化后 `"NO"`）
  —— 名字里没有 `no`，但**语义**直指 NO，所以是"名字不可靠、只有变异可靠"的又一例。
- **锚点全为行内**（不含换行 → `0 == 0`）→ 满足守护测试，且与文件行尾无关。

## 32.5 验证

| 检查 | 结果 |
|---|---|
| `list` | whitelist-fixtures **23 个**（W1–W23）；全仓 **5 套 / 53 个**（原 49，+4）|
| 守护测试 | `pytest tests/test_mutation_verify.py` → junit **10/0/0/0** |
| **四阶段** | `verify whitelist-fixtures` → **23 个变异校验完毕，全部通过**（`EXIT=0`，**9m17s**；`[OK]` 23 条 / `[BAD]` 0 条）|
| W20 | green before ✅ / **red after（1 failed, 60 deselected）** / green after restore ✅ / bytes restored ✅ |
| W21 | green before ✅ / **red after（3 failed, 1 passed, 50 deselected）** / green after restore ✅ / bytes restored ✅ |
| W22 | green before ✅ / **red after（3 failed, 36 deselected）** / green after restore ✅ / bytes restored ✅ |
| W23 | green before ✅ / **red after（2 failed, 36 deselected）** / green after restore ✅ / bytes restored ✅ |
| Lint | `ruff check scripts/mutation_verify.py` → **All checks passed!** |
| 行尾（harness）| `mutation_verify.py` 仍 **纯 LF**（980 → 1041 行）|
| **还原独立复核** | **28 个被变异文件**对跑前 `sha256` 快照逐条比对 → **`changed = 0`**；W20–W23 四个目标文件**变异字符串残留 = 0、原始字符串 = 1**；`eol_audit` → **`damage: none`** |

## 32.6 本节未做

| 事项 | 状态 |
|---|---|
| 给 **A1 `replay/metrics._STRONG_DIRECTIONS`**（新查出的空头）补用例并登记 | ⏳ **未做** —— 与 §31 同类，但需先确认 `downgrades_caused` 的 NO 场景可测；**待拍板** |
| 给 B10 死分支（`decision_quality_service:331`）补用例 | ⏳ 未做（**该分支两臂返回同一字符串**，补用例也无意义，先清理——见 §29.7）|
| 给 B11–B15 四个内联空头补用例 | ⏳ 未做（内联字面量**既不能 `import` 也定位不到**，修法恒是"先提取为模块级具名常量"再补）|
| 规范化 51 个 MIXED 文件 / 收敛 5 处 `_STRONG_DIRECTIONS` / 统一 13+ 处归一化 | ⏳ 未动（均需拍板）|

**提交状态**：本节改 **1 个文件**（`backend/scripts/mutation_verify.py`，**本就在改动列表里**
→ 工作树仍 **17 个已改 + 1 个新文件**，数量未变）。**未提交**。

---

# 三十三、修 A1：给 `replay/metrics._STRONG_DIRECTIONS` 补 `NO` 侧用例，并登记 W24

§32 普查查出 **9 个空头**，其中只有 `execution_quality_service`（§31）被补过用例。
本节把**第 2 个具名空头**（`replay/metrics._STRONG_DIRECTIONS`，5 个同名副本里的第 5 个）也补掉 ——
与 §31 完全同一套做法。

**本节改 2 个文件**：`backend/tests/test_replay_metrics.py`（**新进改动列表**）
与 `backend/scripts/mutation_verify.py`（**本就在列表里**）。**不改生产代码**。

## 33.1 根因：两个方向敏感分支**都只用 `YES` 侧**被测

`replay/metrics.add_phase_result()` 有两处消费 `_STRONG_DIRECTIONS`：

| 行 | 表达式 | 语义 |
|---|---|---|
| `:150` | `if base_dir in _STRONG_DIRECTIONS and phase_dir in _WEAK_DIRECTIONS:` → `downgrades_caused += 1` | 该 phase 把**强方向降级**了 |
| `:156-162` | `... and phase_dir in _STRONG_DIRECTIONS and final_dir in _WEAK_DIRECTIONS` → `conflicts_with_final += 1` | 该 phase 的强方向**被别的 phase 覆盖** |

既有用例里：`test_downgrades_caused_counted` 两条断言都用 `base_dir="YES"`
（"base=YES, phase_only=WAIT" / "base=YES, phase_only=YES"）；
`test_conflict_case_collected_when_phase_overridden` 用 `phase_dir="YES"`；
`test_no_conflict_when_phase_agrees_with_final` 也是 `YES`。
`"NO"` 只以 `final_displayed_direction` 出现在**不经过这两处**的用例里（如
`test_accumulates_yes_to_wait` 的 `NO->AVOID`，那只走 `direction_matrix`）。
→ **`_STRONG_DIRECTIONS` 里的 `"NO"` 从未被这两个分支读到**，删掉它整份文件保持绿。

## 33.2 补的两个用例

在 `test_downgrades_caused_counted` 之后插入（同类 `TestPhaseContributions`）：

| 用例 | 触发点 | 断言 |
|---|---|---|
| `test_downgrades_caused_counted_for_a_no_base` | `add_phase_result("e1","decision_quality","NO","WAIT","WAIT")` | `downgrades_caused == 1`（`NO` 是强方向、`WAIT` 是弱方向）|
| `test_conflict_case_collected_for_a_no_phase` | `add_phase_result("e1","source_reliability","NO","NO","WAIT")` | `conflict_cases_total == 1`，且 `phase_dir=="NO"` / `final_dir=="WAIT"` |

两个用例**分别**钉住两个分支 —— 这是**有意的**：§24.5/§32 的教训是
"同一条过滤条件在源码里写两遍时，只变异一处会漏"；同理，**一个成员在两处被消费时，
两处要各自有一条走到它的用例**，否则补了一处仍是半空头。

## 33.3 登记 W24

| # | 变异 | 锚点（行内，唯一）| guard 文件 | 断言用例 |
|---|---|---|---|---|
| W24 | `replay/metrics._STRONG_DIRECTIONS` 删 `NO` | `= {"YES", "NO"}` → `= {"YES"}` | `_T_REPLAY_METRICS` | 上面两条 |

`title` 改 `（W1–W24）`；`rationale` 追加说明 **W24 与 W19 同款**（都是"补完用例才登记"）。

## 33.4 验证

| 检查 | 结果 |
|---|---|
| 补用例前 | `pytest tests/test_replay_metrics.py` → **11 个用例**（3 个 matrix + 5 个 brier/dc + 3 个 phase）|
| **补用例后** | `pytest tests/test_replay_metrics.py` → junit **13/0/0/0**（+2，无回归）|
| 测试文件行尾 | **纯 CRLF**（257 → 293 行，`CRLF=293 bareLF=0`）|
| `list` | whitelist-fixtures **24 个**（W1–W24）；全仓 **5 套 / 54 个**（原 53，+1）|
| 守护测试 | `pytest tests/test_mutation_verify.py` → junit **10/0/0/0** |
| **四阶段** | `verify whitelist-fixtures` → **24 个变异校验完毕，全部通过**（`EXIT=0`，**9m46s**；`[OK]` 24 / `[BAD]` 0）|
| **W24** | green before ✅ / **red after（2 failed, 11 deselected）** / green after restore ✅ / bytes restored ✅ |
| Lint | `ruff check scripts/mutation_verify.py` → **All checks passed!** |
| 行尾（harness）| `mutation_verify.py` 仍 **纯 LF**（1041 → 1059 行）|
| **还原独立复核** | **29 个被变异文件**对跑前 `sha256` 快照逐条比对 → **`changed = 0`**；`metrics.py` **变异字符串残留 = 0、原始 = 1**；`eol_audit` → **`damage: none`** |

## 33.5 收口：5 个同名 `_STRONG_DIRECTIONS` 副本的空头普查**全部闭环**

§24 报"5 户"、§29 升级为行为级 15 处、§32 把 15 处全量普查、§31/§33 补掉两个空头。
现在 5 个**同名**副本的状态可以固定下来了：

| 副本 | §29.6 / §32 判定 | 处置 |
|---|---|---|
| `guardrail_service.py:63` | ✅ 承重 | **W17** |
| `market_quality_service.py:45` | ✅ 承重 | **W18** |
| `source_reliability_service.py:50` | ✅ 承重 | **W20** |
| `execution_quality_service.py:49` | 🔴 空头 | §31 补 `NO` 用例 → **W19** |
| `replay/metrics.py:33` | 🔴 空头 | §33 补 `NO` 用例 → **W24** |

→ **2 空头 / 3 承重，5/5 全部登记。** 这条留痕的意义在"要不要收敛 5 处"那个待决问题上：
**在讨论收敛之前，先得知道其中 2 处原本是"没有任何测试读到其成员"的** ——
否则"收敛"会显得比实际更安全（"反正行为一致"）或更紧迫（"都是一样的副本"），两者都不准确。

## 33.6 本节未做

| 事项 | 状态 |
|---|---|
| `decision_quality_service:275` 内联空头补用例 | ⏳ 未做（内联字面量**既不能 `import` 也定位不到** → 修法恒是"先提取为模块级具名常量"）|
| `decision_quality_service:331` 死分支（§29.7）| ⏳ 未做（**两臂返回同一字符串**，补用例无意义 → 应先清理）|
| `conclusion_challenge:44/85`、`event_intelligence:1570`、`quality_metrics:366`、`simulated_trade_store:502` 四处内联空头 | ⏳ 未做（同上，需先提取具名常量；`quality_metrics` 缺的是**合取**、`simulated_trade_store` 缺的是**正向断言**）|
| 收敛 5 处 `_STRONG_DIRECTIONS` / 规范化 51 个 MIXED 文件 / 统一 13+ 处归一化 | ⏳ 未动（均需拍板）|

**提交状态**：本节改 **2 个文件** → 工作树 **18 个已改 + 1 个新文件**（原 17+1，
新增的是 `backend/tests/test_replay_metrics.py`）。**未提交**。

---

# 三十四、把 5 处内联方向集提取为具名常量：5 处**全是空头** → 补 6 条用例 → 登记 W25–W29

§33.6 把余下内联方向集的处置**唯一地**定成了「先提取为模块级具名常量」，理由是
**内联字面量既不能被 `import`、也不能被任何比"改源码字符串"更细的手段定位**。
本节执行这条修法 —— 这也是本次会话**首次修改生产代码**。

**本节改 10 个文件**：5 个生产（`simulated_trade_store.py` / `quality_metrics.py` /
`event_intelligence_service.py` / `conclusion_challenge_service.py` /
`decision_quality_service.py`）+ 5 个测试；`mutation_verify.py` 与本文档**本就在改动列表里**。

## 34.1 5 处提取点（定义行 / 消费行均为**提取后**的行号）

| # | 文件 | 新常量（定义行）| 消费点（行）| 原形态 |
|---|---|---|---|---|
| B15 | `memory/simulated_trade_store.py` | `_REPORTED_DIRECTIONS` `:463` | `:509` `for d in …` | `("YES", "NO")` |
| B14 | `api/routes/quality_metrics.py` | `_STRONG_DISPLAY_DIRECTIONS` `:43` | `:372` `if final_dir in … and eid:` | `("YES", "NO")` |
| B13 | `services/event_intelligence_service.py` | `_TRADABLE_DIRECTIONS` `:48` | `:1579` `if direction not in …` | `("YES", "NO")` |
| B11/B12 | `services/conclusion_challenge_service.py` | `_STRONG_EVENT_DIRECTIONS` `:22` | `:51` + `:92` | `{"YES", "NO"}` ×2 |
| B9 | `services/decision_quality_service.py` | `_STRONG_DIRECTIONS` `:61` | `:283` `if raw_direction in …` | `("YES", "NO")` |

→ **5 处提取前的判定全是空头**（与 §32 对 12 处余量的一致：内联处比具名处更容易空头 ——
具名常量至少有"被别处 import 的可能"，内联字形**只能**靠本模块自己的用例覆盖）。

## 34.2 四条空头成因（§32.3）在这 5 处**各命中一条，没有重复**

| 处 | 成因 | 实测证据 |
|---|---|---|
| B15 `simulated_trade_store` | **(c) 断言负向** | 该文件有 15 次 `"NO"`，但方向相关的唯一断言是 `assertNotIn("NO", …)`（"作废交易不该出现"）。删掉 `NO` 后"不产出"与"产出后被正确过滤"**断言结果一模一样** |
| B14 `quality_metrics` | **(b) 缺合取** | 8 次 `"NO"` 全在**不同**的 `e*` 夹具里，**没有任何一条**同时满足 "`final_direction == NO`" 与 "`wide_spread_flag`" —— 而消费点要的正是这个合取 |
| B13 `event_intelligence` | **(整体未执行)** | `_persist_events` 的纸面交易分支**从未被跑过**：3208 行测试里每一处都 `patch.object(eis, "_persist_events", new=lambda records: None)`，且 `PAPER_TRADE_ENABLED` **零引用** |
| B11/B12 `conclusion_challenge` | **(两个消费点各空一边)** | `:51` 所在的证据门只有 YES 侧用例；`:92` 所在的 `_check_calculation` 的 `event_intelligence` 分支**整段从未被执行**（既有 5 个用例的 `change` 全是 18.0） |
| B9 `decision_quality` | **(a) 锁死一侧 + 负控假安全** | 走该门的 NO 用例只有 `test_no_recommendation_support_is_oppose_direction`（只断言证据列、不断言方向），其余全用 `_recommendation("YES")`；唯一的 NO 相关对照 `test_rule4_empty_breakdown_does_not_downgrade_wait` 是**负控**，而负控在"分支整体消失"时**照样绿** |

## 34.3 补的 6 条用例

| 处 | 用例 | 触发 | 断言（删 `NO` 后即失败的那条）|
|---|---|---|---|
| B15 | `test_by_direction_reports_both_strong_directions` | 开/平一笔 YES + 一笔 NO | `set(by_direction) == {"YES","NO"}` **且两侧键都在** |
| B14 | `test_anomalies_flags_wide_spread_not_downgraded`（**改**）| 新增 `e3`：`final_direction="NO"` + `wide_spread_flag=True`；保留 `e2`(WAIT) 作负控 | `count == 2`、`set(event_ids) == {"e1","e3"}` （原为 `count == 1`、`{"e1"}`）|
| B13 | `test_persist_events_opens_the_trade_as_no_for_a_no_recommendation` | 真跑 `_persist_events`（唯一一条）| `trades[0]["direction"] == "NO"` 且 `decision == "act"` |
| B11 | `test_strong_no_conclusion_without_support_is_insufficient_evidence` | `direction="NO"` + `supporting=[]` | `verdict == "insufficient_evidence"`、`failed_checks[0]["check"] == "evidence_support"` |
| B12 | `test_small_probability_change_counts_for_a_no_conclusion` | `direction="NO"` + `change=2.0`(<3.0) + `liquidity_ok=False` | `verdict == "revise"`、`required_action == "recalculate_once"` |
| B9 | `test_rule4_empty_breakdown_downgrades_no_to_wait` | `_recommendation("NO")` + `evidence_breakdown=[]` | `displayed_direction == "WAIT"`、`downgraded is True`、理由含「缺少证据支持」|

⚠️ **B12 的设计改过一次**：初稿按"空 `supporting` + `change<3.0` → `revise`"写，**是错的** ——
`_aggregate` 先判 `CHECK_EVIDENCE` 的 hard_fail，空证据只会得到 `insufficient_evidence`，
根本走不到 `revise`。改成"证据齐备但变化太小（soft）+ 流动性不足（soft）"凑够 2 条 soft 才对。
**教训**：`revise` 的判据是 `len(soft) >= 2`，不是"有一条 soft"。

## 34.4 一次常量被 N 处消费 → 就补 N 条用例

`_STRONG_EVENT_DIRECTIONS`（B11/B12）有 **2 个消费点**，所以补的是 **2 条**用例，**不是 1 条**。
这不是洁癖：两条用例**各自独立**变红是实测出来的 ——

```
W28 … mutated run -> 2 failed, 5 deselected in 0.69s
```

（两条**同时**失败，说明两条都真的走到了该常量）。若只补 B11 那条，`_check_calculation` 的失效
就没有任何用例看得见 —— 那正是 §32 说的**半空头**。

同理，**变异打在常量定义行、不打调用行**：一个常量被 N 处消费时，打定义行只需 **1 条变异**就能
同时覆盖 N 处；反之若把变异打在某一处调用行上，另一处失效就漏了。

## 34.5 顺带发现：`_persist_events` 的纸面交易分支此前**从未被执行**

B13 为了补用例，不得不**第一次真正装配** `_persist_events`（temp `loop_db_path` +
`_INITIALIZED` 就位 + `PAPER_TRADE_ENABLED=True` + stub 掉 `save_events`/`record_event`/
`get_verified_link`/`upsert_link`/`freeze_prediction`）。装起来之后才看清一件事：

**`_TRADABLE_DIRECTIONS` 只有 `entry_edge == 0` 这一条可观测路径** ——
后文（约 `:1580-1583`）会按 `edge` 的**符号**覆写方向，所以任何 `edge != 0` 的输入都会
把方向改回去。构造用例时必须让 `ai_probability == market_probability == 50`。
→ 这条留痕的价值是：**"这个白名单看着很宽"与"它实际只有一条路径能被观察到"是两件事**，
后者只有真跑一次才知道。

## 34.6 登记 W25–W29

| # | 变异（改**常量定义行**）| guard 文件 | 断言用例 |
|---|---|---|---|
| W25 | `_REPORTED_DIRECTIONS` `= ("YES", "NO")` → `= ("YES",)` | `_T_TRADES_STORE` | 1 条 |
| W26 | `_STRONG_DISPLAY_DIRECTIONS` 同上 | `_T_QUALITY_METRICS` | 1 条 |
| W27 | `_TRADABLE_DIRECTIONS` 同上 | `_T_EVENT_INTELLIGENCE` | 1 条 |
| W28 | `_STRONG_EVENT_DIRECTIONS` 同上 | `_T_CONCLUSION_CHALLENGE` | **2 条**（见 §34.4）|
| W29 | `_STRONG_DIRECTIONS` 同上 | `_T_DECISION_QUALITY` | 1 条 |

`title` 改 `（W1–W29）`；`rationale` 追加 ⑥ 段（内联提取、5 处全空头、打定义行不打调用行、
"本来就有 `NO` 字样仍是空头"三件事）；新增 9 个路径常量
（`_QUALITY_METRICS` / `_EVENT_INTELLIGENCE` / `_CONCLUSION_CHALLENGE` / `_DECISION_QUALITY` +
5 个 `_T_*`）。

## 34.7 验证

| 检查 | 结果 |
|---|---|
| 5 个测试文件用例数 | `16 / 32 / 128 / 7 / 33`（合 216）；对照 **HEAD 基线** `15 / 32 / 127 / 5 / 32` → **+6**，其中 `test_quality_metrics` 是**改**不是加 |
| 单文件跑 | `pytest tests/test_conclusion_challenge_service.py tests/test_decision_quality_service.py` → junit **40/0/0/0** |
| 行尾（测试）| 均**纯 CRLF**：`test_conclusion_challenge_service.py` `crlf=164 bareLF=0`（115→164）、`test_decision_quality_service.py` `crlf=570 bareLF=0`（551→570）|
| 行尾（生产）| `conclusion_challenge_service.py` `crlf=375 bareLF=0`（368→375）、`decision_quality_service.py` `crlf=394 bareLF=0`（386→394）|
| **单点变异自检**（跑 verify 前）| 3 次独立 `mutcheck`：**needle 各 1 次 / green before ✅ / red after ✅ / bytes restored ✅**；`conclusion_challenge` 的**两个消费点分别**验过（不是只验一次）|
| `list` | whitelist-fixtures **29 个**（W1–W29）；全仓 **5 套 / 59 个** |
| 守护测试 | `pytest tests/test_mutation_verify.py` → **8 passed, 2 subtests passed** |
| Lint | `ruff check`（harness + 2 个生产 + 2 个测试）→ **All checks passed!** |
| **四阶段** | `verify whitelist-fixtures` → **29 个变异校验完毕，全部通过**（`EXIT=0`，**11m47s**；`[OK]` 29 / `[BAD]` **0**）|
| **W25–W29** | 全部 `green before ✅ / red after ✅ / green after restore ✅ / bytes restored ✅`；red-after 明细 `1 / 1 / 1 / **2** / 1` failed |
| **还原独立复核** | 跑**前**对 harness 全部 **33 个**被变异文件落 `sha256 + 行尾画像` 快照，跑**后**逐条比对 → **`changed = 0`** |
| 行尾审计 | `scripts/eol_audit.py` → **`line-ending damage: none`** |

## 34.8 收口：`backend/app/` 下**已无内联方向成员集**

提取之后，全 `backend/app/` 里 `("YES","NO")` 形态的**内联**字面量**只剩 2 处**，且都不是本节该管的：

| 剩余落点 | 为什么不动 |
|---|---|
| `services/decision_quality_service.py:331` | `_build_rationale_body` 的**死分支**（两臂返回同一字符串）—— 已写进 `_STRONG_DIRECTIONS` 的注释；属 §29.7 的待决项，**不代决** |
| `memory/simulated_trade_store.py:36` | 是 SQL DDL 里的 `CHECK (direction IN ('YES','NO'))`，**schema 层**约束，不是 Python 集合；它反而是"该列只有两个取值"的**权威来源** |

具名常量现在的总数：**13 个** ——
6 个同名 `_STRONG_DIRECTIONS`（`replay/metrics` / `decision_quality` / `execution_quality` /
`guardrail` / `market_quality` / `source_reliability`）+ 3 个异名旧常量
（`_DIRECTIONAL` / `_CALLED_DIRECTIONS` / `_VALID_DIRECTIONS`）+ 本节的 4 个新名
（`_REPORTED_DIRECTIONS` / `_STRONG_DISPLAY_DIRECTIONS` / `_TRADABLE_DIRECTIONS` /
`_STRONG_EVENT_DIRECTIONS`）。

⚠️ **这 13 个常量"同值"但**不是**同义（§29.3 的五种语义）** —— `_VALID_DIRECTIONS` 是"合法输入值"、
`_CALLED_DIRECTIONS` 是"已下注的调用"、`_TRADABLE_DIRECTIONS` 是"可开仓方向"、
`_STRONG_DISPLAY_DIRECTIONS` 是"异常列表要展示的强方向"、`_STRONG_DIRECTIONS` 是"可被降级的强方向"。
→ **本节只做"具名化 + 补用例"，一处**都没有**合并**。合并要等拍板（§34.9）。

## 34.9 本节未做

| 事项 | 状态 |
|---|---|
| **收敛 13 个方向常量** | ⏳ 未动 → **已评估：见 §四十二**（13 常量 ⟷ 13 变异 ⟷ 22 守卫；真实代价是 harness 表达不了跨文件的 N 条守护，不是"少一条变异"）。**仍未收敛**，待拍板 |
| `decision_quality_service:331` 死分支清理 | ⏳ 未动（**两臂返回同一字符串**，补用例无意义；属 B10）|
| 统一 13+ 处 direction 归一化（`in ("YES","NO","WAIT","AVOID")` 的四元组）| ⏳ 未动（是**另一个集合**：合法输入词表，不是强方向）|
| 前端 `api.ts:447` 手写 union / 规范化 51 个 MIXED 文件 | ⏳ 未动（需拍板）|

**提交状态**：本节改 **10 个文件**（5 生产 + 5 测试）→ 工作树 **28 个已改 + 1 个新文件**
（原 18+1）。**逐项对账**（因为 18 与 28 差得远，必须能对上）：本批新增的 10 个是
`quality_metrics.py` / `simulated_trade_store.py` / `event_intelligence_service.py` /
`conclusion_challenge_service.py` / `decision_quality_service.py` +
对应的 5 个测试文件 ——
恰好 `18 + 10 = 28`；另 18 个的构成是 §22/§24 留下的 **11 个测试文件**（mtime 全在
`2026-09-28 02:37–02:55`，由批次脚本写入）+ §28 的 2 个生产文件 + harness 2 件 + 本文档。
**未提交**（本批**首次修改生产代码**，是否提交等指令）。

> 追加（2026-09-29）：§34 的这 10 个文件已在 **`e52b364`** 与本节的记录一起提交；
> 文档为 **`b8af753`**。§34 页脚那句"未提交"是当时的实测状态，保留不改。

---

# 三十五、`restore_stores._check_service_running()` 的保守偏向被**环境级 `HTTP_PROXY`** 抵消：一次全量套件失败的环境归因

## 35.1 起因：提交前的全量套件出现 1 个失败，先归因再定性

`pytest tests/` → **failures 1 / errors 0 / skipped 11**（21m；junit 报 `tests=7420`，
而 `--collect-only` 实测 **6454 收集** —— 差额是 **subTest 展开**，两份计数口径不同，别互相验算）。
唯一失败：`tests/test_backup_restore_drill.py::BackupRestoreDrillTests::
test_a_real_archive_restores_every_store_to_its_configured_path`。

**三重反证（缺一就只能"觉得"是环境问题）**：

| # | 反证 | 结果 |
|---|---|---|
| ① | 该文件在不在改动列表里 | **不在**（`git status` 无它）|
| ② | 单独跑该文件是否同样失败 | **是** —— 55 个里 1 个失败（与全量同一条）|
| ③ | 失败信息指向的外部依赖是否存在 | 要求"`localhost:8000` 上没人听"，但**探测说有人在听** |

→ 结论**当时**只能是"环境相关"，**根因未查明**。本节把根因查到证据层。

## 35.2 机制：这是一处**有意的**保守偏向，不是疏漏

`backend/scripts/restore_stores.py:240-303`：

| 分支 | 做法 |
|---|---|
| POSIX | `fcntl.flock(loop_db, LOCK_EX \| LOCK_NB)`：抢到锁 = 没人持有 = 没在跑（`:266-274`）|
| Windows（`except ImportError`）| `urllib.request.urlopen(PMRF_HEALTHCHECK_URL, timeout=…)`（`:280-303`）|

Windows 分支的关键在 `:286-293` 的**注释**：任何 HTTP 响应（**含 503、以及 502/504 —— "代理后面"**）
都算"服务活着"；`:298-301` 的 `except urllib.error.HTTPError: return True` 与之对应。
注释同时写明**为什么**放宽：早先返回 `resp.status == 200`，导致**降级但仍在跑的 503 服务被判成"没跑"**
→ 静默覆盖活库。

→ 方向是**安全的**：假"在跑"只多一条警告；假"没跑"才覆盖数据。**所以本节不是"作者漏了 502"。**

## 35.3 新事实：环境级 `HTTP_PROXY` 让探测请求**根本没到 localhost**

实测（2026-09-29 本机，win32）：

| 观测 | 值 |
|---|---|
| `HTTP_PROXY` / `http_proxy` / `HTTPS_PROXY` / `https_proxy` | **全设** → `http://127.0.0.1:52950` |
| `NO_PROXY` / `no_proxy` | **未设** |
| `urllib.request.getproxies()` | `{"http": "http://127.0.0.1:52950", "https": "http://127.0.0.1:52950"}` |
| `urllib.request.proxy_bypass('localhost')` / `('127.0.0.1')` | **False** / **False** |
| `urlopen('http://localhost:8000/api/health')` | **`HTTPError 502`**，正文 `upstream connect failed: 由于目标计算机积极拒绝，无法连接。` |
| **`_check_service_running()`** | **`True`** |
| `netstat` 在 `:8000` 上 | **没有任何监听者**（IPv4/IPv6 都没有；唯一含 "8000" 的是 `127.0.0.1:58000`）|
| `HKCU\Environment` / HKLM 机器级里的 proxy 变量 | **无** |
| WinINET `ProxyEnable` / `ProxyServer` / `ProxyOverride` | `1` / `127.0.0.1:7897` / **含 `localhost` 与 `<local>`** |

**三个必须分清的点**：

1. **两个不同的代理**：环境变量指向 `127.0.0.1:52950`；WinINET（系统代理）指向 `127.0.0.1:7897`。
   本函数走的是 **urllib + 环境变量**那条。
2. **系统代理本来就绕过 localhost** —— `ProxyOverride` 明写 `localhost;127.*;…;<local>`；
   但 **urllib 不看 WinINET，只看环境变量** → 它绕不过。这就是"浏览器打不开时正常、Python 却踩到"的原因。
3. `HTTP_PROXY` **未持久化**（`HKCU\Environment` 为空）→ 它来自**启动本会话的外层进程**
   （同一环境里还有 `CODEBUDDY_SERVICE_PROXY_URL`）→ **用户自己新开的终端不必然有它**。

→ 于是该检查在这类环境里退化成"**代理可达吗**"，答案恒真 → **警告恒亮**。

## 35.4 影响面：分清"有证据的"与"推的"

| 判断 | 证据强度 |
|---|---|
| 该测试是**唯一**受害用例 | ✅ 有证据：`test_backup_restore_drill.py` 里**只有** `:114` 断言 `warnings`；同文件 `:172/:205/:274` 的 `apply=True` 都不看它。全量套件也只红这一条 |
| **CI 不受影响** | ✅ 实测 + 🟡 一步推断，分开标：**实测** —— `.github/workflows/` 里**没有任何 proxy 设置**，而 CI 的 `Run backend tests` 就是 `pytest tests/ --cov=…`（该 job 会跑这条用例）；**推断**（未跨环境验证）—— GitHub 托管 runner 上 `:8000` 无监听、且不注入 `HTTP_PROXY` → `urlopen` 抛 `URLError` → 返回 `False` → warnings 为空 |
| 任何"导出 `HTTP_PROXY` 却不给 localhost 设 `NO_PROXY`"的机器（企业代理下的 CI、开了全局代理的开发机）都会**恒亮** | 🟡 **推的，未做第二台机器验证** —— 只写了机制，没跨机复现 |
| **无数据风险** | ✅ 有证据：这条警告**不阻止 `--apply`**（`restore_from_backup` 只看 `apply`，`:345-349` 只是 `warnings.append`）。危险方向（假"没跑" → 覆盖活库）**没有被本次发现削弱** |

> **追加（2026-09-30）：本表第 3 行已从「推的」升级为「实测」** —— 做法 = **自建一个与机器无关的桩代理**
> （`127.0.0.1:64599`，对任何请求一律回 **502**）设成 `HTTP_PROXY`/`HTTPS_PROXY` 并清掉 `NO_PROXY`，
> 对**确定无人监听**的 `127.0.0.1:9` 探测，对照两条**与 shipped 代码逐字一致**的路径：
> **旧**（默认 opener ⇒ 等价 `urlopen`，走桩代理）→ **`HTTPError 502`** ⇒ 旧 `_check_service_running()` = **`True`**（"在跑"）；
> **新**（`build_opener(ProxyHandler({}))`，§41 的 loopback 绕过）→ **`URLError [WinError 10061] 目标计算机积极拒绝`** ⇒ **`False`**（正确）。
> 桩代理与机器自带代理（`127.0.0.1:52950`）**不是同一个** ⇒ **机制不依赖这台机器的那个代理**：只需
> (i) 有代理替死端口作答、(ii) `urllib` 不绕 loopback（`proxy_bypass('127.0.0.1')` 实测仍 **False**）。
> 本表第 2 行「CI 不受影响」里那半句 🟡 推断（"GitHub runner 不注入 `HTTP_PROXY`"）**不受本条影响**，仍是推的。
> **残留（如实说，不夸大为"跨机验证"）**：实验仍在**同一台机器**上 —— "换一台 Windows 是否相同"属**平台行为**，本机无法排除。
> 探针写在**仓库外**、跑完删除；本次**不改任何生产代码**。

## 35.5 修法选项（**不代决**，留给老板）

| 选项 | 说明 | 代价 |
|---|---|---|
| **(a) 探测时显式不用代理** | `build_opener(ProxyHandler({}))`，且**只在目标是 loopback**（`localhost`/`127.0.0.1`）时生效 | 最贴合语义：问的是"**本机**这个端口有没有人听"。**必须限定 loopback** —— 若运维故意把 `PMRF_HEALTHCHECK_URL` 指向反向代理，硬关代理会改变语义 |
| (b) 收窄"算活着"的响应 | 要求 `200` 或响应体含已知字段 | **会退回 `:286-293` 注释记录过的旧缺陷**（503 降级被误判成没跑 → 覆盖活库）|
| (c) 改测试期望 | 允许 warnings 出现该条 | **不建议**：断言本身是对的；改期望等于把"警告恒亮"固化成契约 |
| (d) 只加运维说明 | RUNBOOK 写明"跑 `restore --all --apply` 前先 `set NO_PROXY=localhost,127.0.0.1`" | 零代码风险，但治标 |

→ 倾向 **(a) + (d)**；但"要不要动生产代码"是决定，不在本节。

## 35.6 与 §三十四 提交消息的关系（一处精度更正）

§34 的提交（`e52b364`）消息里写的是 *"a local HTTP proxy answers localhost:8000/api/health with a 502"*
—— **方向对、精度不够**。真正起作用的是**环境变量代理**（`127.0.0.1:52950`），
而**系统代理（WinINET，`127.0.0.1:7897`）本来就绕过 localhost**。
本节把这条更正到证据层。提交消息按约定不改写。

## 35.7 本节未做

| 事项 | 状态 |
|---|---|
| 改 `_check_service_running()`（选项 a）| ✅ **已在 §四十一 完成**（含配套用例：4 条重指 + 5 条新增 + 4 条变异；本节当时的"需拍板"是时点状态）|
| 第二台机器/带代理 CI 的复现 | ⏳ 未做（35.4 的第三行因此只能标"推的"；**§四十一 也没有跨环境验证**）|
| 写进 RUNBOOK（选项 d）| ✅ **已在 §四十一 完成**（且措辞随 (a) 一起改：见 §41.7 最后一行的"不要把残留 `[WARN]` 当代理假象"）|

**提交状态**：本节**只改本文档**；生产代码与测试**一字未动**。**未提交。**

> 追加（2026-09-29）：本节已在 **`89c66a6`** 提交（1 文件 +104）。页脚那句"未提交"是提交前的
> 实测状态，保留不改。

---

# 三十六、清理 `_build_rationale_body` 的死分支（B10 收口）：一个"删掉也不会让任何守卫变红"的改动

## 36.1 结论先行

| 问题 | 答案 |
|---|---|
| 这个改动能被守卫抓到吗 | **不能** |
| 能登记为新变异吗 | **不能** —— `mutation_verify._verify_one` 的阶段②要求"变异后必红"，而死分支被恢复后**行为零变化** |
| 那它的价值是什么 | **代码卫生**：让 §34 那次提取的账目变准 —— `_STRONG_DIRECTIONS` 现**恰好 1 个消费者** |
| 顺带发现 | **存活的那条早退本身是空头**（§36.4）—— 但按 §34 的自家规矩（补完用例才登记），本节**只记录不扩批** |

§29.7 把它列为 B10（`:331` 死分支），§34.2 把它标成 5 处里"**唯一补用例无意义**"的一处；
本节就按那个结论**先清理**。

## 36.2 改动（**1 个文件 / 2 处**）

| # | 位置 | 改动 |
|---|---|---|
| 1 | `_build_rationale_body`（`:338`）| 删掉内层 `if raw_direction in ("YES", "NO"):` 与其后的 `return`（两臂返回**同一字符串**），只留外层 `if consensus_level == "none":` 的单条 `return` |
| 2 | `_STRONG_DIRECTIONS` 上方 NOTE（`:58-60`）| 原文写"该死分支被单独跟踪、此处**故意不动**"→ 改为"该分支**已删**（审计 §36），此常量现**恰好 1 个消费者**：`_apply_downgrade_rules`" |

**签名不动**：首版把 `raw_direction` 读成"未使用参数"是**误判**，grep 证伪 ——
`:341`（降级臂的 `displayed_direction != raw_direction`）与 `:344 / :357 / :358 / :364 / :368`（f-string）都在用它。
**只删不可达分支，不删参数。**

## 36.3 为什么不能登记为变异（附实测，不是推理）

判据来自 `backend/scripts/mutation_verify.py` 的 `_verify_one` 四阶段，**阶段②**是硬门：

```python
red_after = not passed_after      # 变异之后必须"变红"，否则 [BAD]
```

死分支两臂同字符串 → **把死分支写回去，行为零变化** → 守卫全绿 → `red_after = False` → `[BAD]`。

→ 判定只有一条路可走：**不登记**。这与 §34 的"补完用例才登记"并不矛盾 ——
那里是**加了能变红的用例**，这里是**没有任何用例能变红**。

**探针 A（跑出来的）**：用字节级注入把死分支**逐字写回**工作树，再跑守卫文件：

| 项 | 值 |
|---|---|
| 注入前 | `sha256=ae10b573…`，`crlf=393 bareLF=0` |
| 注入后 | `crlf=395 bareLF=0`（**只多 2 个 CRLF** —— 正是被删的那两行）|
| `pytest tests/test_decision_quality_service.py` | **33 passed / failures=0 / errors=0** |
| 还原后 | `sha256=ae10b573…`（**逐字节一致**）|

## 36.4 顺带发现：存活的那条早退**是空头**（本节**不修**）

把**存活**的 `if consensus_level == "none":` 改成 `if consensus_level == "NEVER":`（= 让早退**不可达**，
落到底部尾返回"无明确支持证据，当前方向 WAIT 为保守建议。"）—— **探针 B**：

```
[mutate-b] needle hits=1  before sha=ae10b573… crlf=393
[mutate-b] after  sha=e0da98ef… crlf=393
33 passed in 0.64s          <- 全绿
```

**为什么全绿**：唯一同时"断言 `consensus_level == "none"`"且"看 rationale"的用例是
`test_missing_both_inputs_emits_block`（`:448`），而它对 rationale 的断言只有非空：

```python
self.assertNotEqual(result["decision_rationale_zh"], "")     # :460
```

—— **非空断言对"早退根本没执行"天然盲**（尾返回也是非空字符串）。

| 用例 | 断言 `consensus_level == "none"` | 看 rationale |
|---|---|---|
| `test_consensus_none_when_empty_breakdown`（`:216`）| ✅（`raw_direction=YES`）| ❌ |
| `test_missing_both_inputs_emits_block`（`:448`）| ✅ | ✅ 但**只断言非空** |
| `test_adversarial_input_never_raises`（`:462`）| ✅ | ❌ |

→ 这是 §32.3 成因 **(c) 断言负向/过弱** 在 `decision_quality_service` 里的**另一处**实例
（B9 那处是**集合成员**负控，这处是**字符串非空**）。
→ **要登记就得先补一条"精确字符串"用例**（`assertEqual(…, "缺少可解析的证据分解，无法判断证据一致性。")`）；
本节口径是**纯清理**，故只记为后续候选（§36.6）。
→ 附一条边界：**全仓 grep `缺少可解析` 只有服务本体 1 处命中**，没有别的守卫在看这个字符串。

## 36.5 验证

| 检查 | 结果 |
|---|---|
| `ruff check app/services/decision_quality_service.py` | `All checks passed!`（exit 0）|
| `pytest tests/test_decision_quality_service.py` | junit **tests=33 / failures=0 / errors=0 / skipped=0** |
| W29 的 `-k` 选择器（harness 阶段①前置条件）| `1 passed, 32 deselected` —— 仍**选中 1 个**用例 |
| 两个探针的还原 | 均 `sha256=ae10b573…`、`crlf=393 bareLF=0`（**逐字节一致**）|
| 改动文件行尾 | `crlf=393 bareLF=0`（**纯 CRLF**，无 bare LF）|

**为什么没跑全量套件**：本批删除的是**不可达分支**，爆炸半径 = `_build_rationale_body` 的**唯一**调用点
（`:120`）；且全仓 grep 该函数名与它输出的字符串，**只有 `decision_quality_service.py` 自己**命中。
→ 单文件覆盖即完整，跑 21 分钟全量对这条改动**没有增量证据**。

## 36.6 本节未做

| 事项 | 状态 |
|---|---|
| 给**存活**的早退补"精确字符串"用例 → 登记 W30 | ⏳ 未做（§34 规矩：**补完用例才登记**；本节口径是纯清理）|
| 收敛 13 个方向常量 / 归一化四元组 / 前端 `api.ts` union / 51 个 MIXED 文件 | ⏳ 未动（同 §34.9，**待拍板**）|
| §35.5 的修法 (a)+(d) | ✅ **已在 §四十一 完成**（本节当时的"待拍板"是时点状态；改动见 §41.2–§41.7）|

**提交状态**：本节改 **1 个生产文件**（+ 本文档）。**未提交**（等指令）。

> 追加（2026-09-29）：本节已提交 —— 生产代码 **`66d85e6`**（1 文件 +4/-5）、
> 本文档 **`61570fb`**（1 文件 +108）。页脚那句"未提交"是提交前的实测状态，保留不改。
>
> 一处**对账**（避免读成自相矛盾）：§36.5 说的是"**为这条改动**跑全量没有**增量证据**"，
> 而提交协议**仍然**跑了全量套件闸门 —— junit **tests=7420 / failures=1 / errors=0 / skipped=11**
> （18m35s），唯一失败即 §三十五 已归因的那条（环境级 `HTTP_PROXY` 造成的假警告），
> 与 §30 的闸门画像**逐项一致**。两句不冲突：前者论的是**证据增量**，后者是**提交前协议门槛**。

---

# 三十七、把 §36.4 的空头变成承重：给 `none` 早退补**精确字符串**断言 → 登记 W30

## 37.1 起因

§36.4 的探针 B 证明了一件事：`decision_quality_service._build_rationale_body` 的
`if consensus_level == "none":` 早退一旦**不可达**，该文件 **33 个用例全绿** ——
因为唯一看着 rationale 的断言只有 `assertNotEqual(..., "")`，而尾部保守模板**同样非空**。
§36.6 把它记为"要登记就得先补一条精确字符串用例"。本节就是那一步（§34 规矩：**补完用例才登记**）。

## 37.2 补的用例（**1 条**，33 → 34）

| 项 | 值 |
|---|---|
| 文件 | `backend/tests/test_decision_quality_service.py`（紧接那条弱断言之后）|
| 名字 | `test_missing_both_inputs_rationale_names_the_missing_breakdown` |
| 输入 | `recommendation=None, evidence_breakdown=[]` —— 与弱断言那条**完全相同**（同一输入、同一分支，只把断言从"非空"换成"精确"）|
| 断言 | `assertEqual(result["decision_rationale_zh"], "缺少可解析的证据分解，无法判断证据一致性。" + _DISCLAIMER_SUFFIX)` |

**oracle 的分工**：**正文写字面量**（被锁的正是这条分支的措辞），**后缀用模块自己的
`_DISCLAIMER_SUFFIX`**（后缀不是被测对象，不必抄第二遍）。这与 §24「oracle 必须独立于被测常量」
不冲突 —— 被测的是**分支**，不是免责声明。

## 37.3 登记 W30

| 项 | 值 |
|---|---|
| 标签 | `W30 decision_quality._build_rationale_body 的 none 早退改成不可达` |
| path | `backend/app/services/decision_quality_service.py`（**复用已有路径常量**，未新增）|
| old → new | `    if consensus_level == "none":\r\n        return` → `    if consensus_level == "NEVER":\r\n        return` |
| guard | `test_missing_both_inputs_rationale_names_the_missing_breakdown`（**1 条**）|
| 清单 | `whitelist-fixtures` 29 → **30**；总计 59 → **60**；仍 **5 套** |

## 37.4 🔴 登记的不是「被删掉的那段死代码」

W30 **不是**为 §36 删掉的死分支登记的 —— 那段**根本登记不了**（§36.3：写回去行为零变化，
阶段②必过不了）。W30 登记的是**清理之后活下来的那条早退**。两者在同一函数里相邻，
极易混为一谈，所以这条区分写进了 set 的 `rationale` ⑦。

## 37.5 🔴 针必须唯一：那条 `if` 在文件里出现 **2 次**

`if consensus_level == "none":` 出现两次：`_apply_downgrade_rules:285`（**8 空格**缩进）与
`_build_rationale_body:339`（**4 空格**）。

- 只写「4 空格 + `if consensus_level == "none":`」→ **命中 2 次** ——
  因为 8 空格那行**包含**"4 空格 + if…"作为子串。`apply` 会直接拒绝，静态守护测试也会报。
- 解法：针**跨到下一行**（`if` 行 + `CRLF` + `        return`）→
  8 空格那行后面跟的是注释 `# Rule 4:`，不匹配 → 命中 **1** 次。
- 这是本套**第一个含换行的针**，因此必须带 **CRLF**（`test_no_needle_carries_a_bare_lf` 的判据是
  「针里的换行数 == 其中的 CRLF 数」）。实测 `hits=1 bareLF=0`。

## 37.6 验证

**单条四阶段**（探针直接读**已登记的** inventory，不是手抄针 —— 保证探的就是 `verify` 将来会跑的那条）：

```
target   : whitelist-fixtures #30  W30 …
needle   : hits=1  bareLF=0
phase 1  : rc=0  OK                 | 1 passed, 33 deselected
phase 2  : rc=1  red_after=True OK  | 1 failed, 33 deselected
phase 3  : rc=0  OK                 | 1 passed, 33 deselected
phase 4  : bytes restored -> True
sha256   : ae10b573d0c024ea (before) / ae10b573d0c024ea (after)
```

**harness 静态守护**：`tests/test_mutation_verify.py` → **8 passed + 2 subtests**
（junit `tests=10 / failures=0`）。`ruff check scripts/mutation_verify.py` → `All checks passed!`。

**全套**：`verify whitelist-fixtures` → **29/30 标 `[OK]`，其中 1 条的读数被环境拦截器污染**（见 37.6.1）。
跑前对 **35 个文件**（33 个变异目标 + harness + 本测试文件）取 `sha256 + 行尾画像` 快照，
跑完**逐条比对 `changed = 0`**；`git status` 里只剩本节自己改的 2 个文件。

### 37.6.1 🔴 W27 的 `[FAIL]` 是**环境拦截器**，不是变异失败

全套输出里 W27 没有 `[OK]`，失败行是：

```
FAILURES (1):
  - whitelist-fixtures 27: guard already RED before mutation -> .
    [safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]
    {"count":227,"threshold":50,"scope":"turn","targetCount":1}
```

**判据**：`_run_guards` 的通过判据是 **`proc.returncode == 0`**；而本机 `safe-delete` shim
会拦掉 pytest 自己的临时目录清理、把进程退出码抬成非 0。→ **阶段① 被读成"变异前守卫就是红的"，
实际是守卫跑绿了、只是清理阶段被拦。** tail 里的 `[safe-delete]` 签名就是它的指纹。

**三重反证**（缺一就只能"觉得"是环境问题）：

| # | 反证 | 结果 |
|---|---|---|
| ① | 失败发生在**阶段①**（写文件**之前**）| 是 —— `_verify_one` 在 `green_before` 处就 `return`，所以**没留下变异态**（快照 `changed = 0` 印证）|
| ② | 该守卫在**当前（未变异）**状态单独跑 | **rc=0 / `1 passed, 127 deselected`** |
| ③ | W27 用**单条四阶段探针**重跑 | **四阶段全过**（`red_after=True`、`bytes restored=True`、`sha256 5508ab33…` 前后一致）|

→ 结论：**30 条变异各自都通过了四阶段**；其中 W27 由独立探针取证，全套那一次的读数是环境噪音。
**不重跑的理由**：`scope:"turn"` 是**按本轮**累计的，本轮已跑过大量 pytest（含一次 18 分钟全量），
再跑一次更可能在前面的序号就撞上同一个拦截器 —— 用一次更差的观测去覆盖一份已完整的证据没有意义。

## 37.7 本节未做

| 事项 | 状态 |
|---|---|
| 收敛 13 个方向常量 / 归一化四元组 / 前端 `api.ts` union / 51 个 MIXED 文件 | ⏳ 未动（同 §34.9，**待拍板**）|
| §35.5 的修法 (a)+(d) | ✅ **已在 §四十一 完成**（本节当时的"待拍板"是时点状态；改动见 §41.2–§41.7）|
| 清 `%TEMP%\pytest-of-Alin`（降低下次长跑撞上拦截器的概率）| 🟡 **部分成功**：删掉 3 个 `pytest-NNNN`；**3 个更旧的 `pytest-*` 与 5 个 `garbage-*` 被 ACL 拒绝**（`WinError 5`，`chmod` 后仍拒 —— 与 HANDOFF 记的 `backend/.pytest_cache` ACL 损坏同类，需管理员提权）|
| 给 harness 加**按序号单跑** `verify` 的入口 | ✅ **已在 §三十八 完成**（本批的 §37 页脚早于它，故此处按未交付内容更新）。本节当时只能**在仓库外**搭探针（探针直接 `import scripts.mutation_verify` 读**已登记的** inventory，而不是手抄针）—— 那正是这条入口要固化的做法 |
| 提交 | ⏳ 未提交（等指令）|

**提交状态**：本节改 **2 个文件**（1 个测试 + harness）。**未提交**（等指令）。

> 追加（2026-09-30）：本节已提交 —— `test_decision_quality_service.py` 的新用例与 `mutation_verify.py` 里的
> W30 登记同在 **`d57d72a`**，本文件在 **`727e70e`**。页脚那句"未提交"是提交前的实测状态，**保留不改**（同 §35/§36 的做法）。
> ⚠️ §37.6 那张表里的"33 → 34 passed"是**当时**的画像；写这份追注时该文件已是 **34 个用例**
> （junit 侧 `test_decision_quality_service.py` 34 tests、全量 7534 里的 `+1` 就是它）。

---

# 三十八、给 harness 加 `verify <set> --index N`：把「单条四阶段」从自制探针变成常驻能力

## 38.1 起因

§37 里"单条四阶段"这件事实用到了**两次** —— W30 的常规取证、以及 W27 被环境拦截器污染后的重取 ——
而两次都是**在仓库外手搭探针**。根因：`verify` 只接 set key（`keys` 是 `nargs="*"`），
没有按序号单跑的入口，于是"证明一条变异"只能靠临时脚本，而临时脚本**本身不受任何守护**。
本节把它变成常驻能力。

## 38.2 设计：把「选哪几条」抽成纯函数

`_select_verify_targets(keys, index)` → `list[(MutationSet, 1-based position, Mutation)]`

| 输入 | 行为 |
|---|---|
| `keys=[]`、`index=None` | 全部 5 套、**60** 条，按清单顺序 |
| `keys=["x"]`、`index=None` | 该套全部 |
| `keys=["x"]`、`index=N` | **恰好 1 条**（`x` 的第 N 条）|
| `index` 但给了 **0 或 ≥2 个 set** | `SystemExit`：`--index needs exactly one set, got 2: …` |
| `index` 越界（`0` / 负数 / `> len`）| `SystemExit`：`index out of range: 99 (whitelist-fixtures 有 30 个)` |
| 未知 set key | `SystemExit`（沿用原文案）|

**为什么抽成纯函数**：这些规则**不需要跑 pytest 就能判定**。抽出来之后静态测试才钉得住 ——
否则一个 off-by-one 会**安静地重取相邻的那条变异**并报"通过"，而那正是最坏的一种错
（它看起来像成功）。

⚠️ **序号口径必须与 `apply` 一致**：`apply` 是 1-based，所以 `-i 0` 与负数**一律拒绝**
（`1 <= index <= len`）。两边口径不一致的话，"同一个序号"在两条命令里会指向**不同的变异**。

## 38.3 CLI

```
verify [key ...] [-i N | --index N]
```

`--index` 只在该次运行**恰好一个 set** 时有意义 → 不满足就**报错**而不是猜
（`verify a b --index 3` 没有唯一解）。输出沿用原形状：仍打 `=== <set> — <title> ===` 头与
逐条 `[OK ] key N. label`，只是头改为**在切换 set 时**才打（单条时自然只出现一次）。

## 38.4 新增的静态守卫（+7 个用例）

`tests/test_mutation_verify.py` 新增 `VerifySelectionTests`：

| 用例 | 钉住 |
|---|---|
| `..._no_index_covers_every_mutation_in_inventory_order` | 扁平化后的 `(key, position)` 序列 == 清单顺序（**不变式**，不硬写 60）|
| `..._the_selected_mutation_is_the_inventory_object` | 选出的是**清单里那个对象**（`assertIs`，不是等价副本）|
| `..._index_one_selects_the_first_mutation_of_that_set` | 下界 `1` |
| `..._index_at_the_last_position_selects_the_last_mutation` | 上界 `len(...)` |
| `..._index_needs_exactly_one_set` | 0 或 ≥2 个 set 配 `--index` → `SystemExit` |
| `..._index_out_of_range_is_rejected_at_both_ends` | `0` / `-1` / `len+1` → `SystemExit` |
| `..._unknown_set_is_rejected` | 未知 key → `SystemExit` |

→ 该文件从 **8 passed + 2 subtests** 变成 **15 passed + 65 subtests**。

## 38.5 验证

| 检查 | 结果 |
|---|---|
| `verify whitelist-fixtures --index 30`（端到端）| 只跑 1 条：`green before True / red after True / green after restore True / bytes restored True`；`mutated run -> 1 failed, 33 deselected`；exit **0** |
| `verify voided-trade -i 1`（短旗标）| 同上，1 条通过 |
| `verify daily-digest review-queue --index 1` | 拒绝：`--index needs exactly one set, got 2: …`（exit 1）|
| `verify whitelist-fixtures --index 99` | 拒绝：`index out of range: 99 (whitelist-fixtures 有 30 个)`（exit 1）|
| `tests/test_mutation_verify.py` | **15 passed + 65 subtests** |
| `ruff check scripts/mutation_verify.py`（CI 闸门）| `All checks passed!` |
| 冒烟后 33 个变异目标文件的 sha256 快照 | **changed = 0** —— 新入口自己也会把文件还原干净 |

## 38.6 顺带修正

模块 docstring 里 *"asserts a full three-phase cycle"* 与实际列出的 **4** 条不符
（第 3 阶段是合并 harness 时新增的，描述漏改）→ 改为 **four-phase**；
Usage 段补上 `verify whitelist-fixtures --index 30` 与 `--index` 的说明段。

## 38.7 本节未做

| 事项 | 状态 |
|---|---|
| 让 `--index` 一次支持多条（如 `--index 27,30`）| ✅ **已在 §三十九 完成**（本批的 §38 页脚早于它，故此处按未交付内容更新）|
| 收敛 13 个方向常量（**已评估**：§四十二）| ⏳ **仍未收敛**，待拍板（§42.6 四方案）|
| `api.ts` union / 51 个 MIXED 文件 | ⏳ 待拍板 |
| §35.5 修法 (a)+(d) | ✅ **已在 §四十一 完成**（本节的页脚早于它，故此处按未交付内容更新）|
| 提交 | ⏳ 未提交（等指令）|

**提交状态**：本节改 **2 个文件**（harness + 它的静态守护测试）+ 本文档。**未提交**（等指令）。

> 追加（2026-09-30）：本节已提交 —— 代码/测试在 **`d57d72a`**（3 文件 +627/-39：`mutation_verify.py` · `test_mutation_verify.py` · `test_decision_quality_service.py`），
> 本文件在 **`727e70e`**（1 文件 +451）。页脚那句"未提交"是提交前的实测状态，**保留不改**（同 §35/§36 的做法）。
> 提交前的全量闸门：junit `tests=7534 / 1 failure / 0 errors / 11 skipped`，唯一失败是 §35/§30 已归因的环境级
> `test_backup_restore_drill` 那条；7534 与上一道闸门 7420 的差额（`+1` 用例 `+113` 来自 `test_mutation_verify.py`
> 的 junit 贡献 10 → 123）**算得平**。

---

# 三十九、`--index` 一次多条：`-i 27,30`

## 39.1 起因

§三十八 的入口一次只能重取**一条**。而 §37 里需要重取的是**两条**（W27 被环境拦截器污染、W30 新登记），于是要用同一套机制跑两遍 —— 每遍都要重走一次参数解析与 pytest 冷启动。
§38.7 当时把它记成"现在跑两次即可；单值口径不必先复杂化"（该行已按 §37.7 的先例改为指向本节，
**原措辞保留在此引用**，避免改写时点事实）。做完后发现**"复杂化"的成本实际只落在一处**（一个新的 `type=` 回调，见 39.3），
其余选择规则本来就已经在 `_select_verify_targets` 里 —— 那个函数接受的**本就该是一个序列**。所以这不是新增复杂度，而是把已有的口径改对。

## 39.2 为什么是逗号，而不是 `nargs="+"`

`verify` 的第一个位置参数 `keys` 已经是 `nargs="*"` → `--index 27 30` 会被当作**两个 set key** 读进去
（然后报 `unknown set(s): 30`，一条与真实错误无关的消息）。**逗号形式只占一个 token，不可能被误读。**
代价是这个 token 要自己拆 → 需要自定义 `type=`。

## 39.3 解析与选择分离

| 层 | 函数 | 职责 |
|---|---|---|
| 值级 | `_parse_index_list(value) -> tuple[int, ...]` | `"27,30"` → `(27, 30)`；容忍两侧空白（`" 27 , 30 "`）；**空项/非整数抛 `argparse.ArgumentTypeError`** |
| 语义级 | `_select_verify_targets(keys, indexes: tuple[int, ...] \| None)` | 越界 / 重复 / 未给 set key 的判定 |

**值级错误交给 argparse**（exit **2** + usage），于是笔误在**跑任何 pytest 之前**就被拒 ——
而不是等到一条四阶段跑完才发现第二条序号不存在。

`_select_verify_targets` 的多值口径：

| 输入 | 行为 |
|---|---|
| `indexes=(2, 1)` | **按给定顺序**返回 `[(…,2,…), (…,1,…)]` |
| 重复序号（`27,27`）| `SystemExit`，**不静默去重** |
| 任一序号越界 | `SystemExit`，**整个请求拒绝**并点名越界的那个序号 |
| 未给 set key（`verify --index 1`）| `SystemExit`：`--index needs a set key, e.g. verify whitelist-fixtures --index 27,30` |

两条设计理由：

1. **顺序按输入而非清单顺序**：结尾那行 `=== N 个变异校验完毕 ===` 是这次运行的**收据**。
   收据若被重排，就与操作员敲下的命令行对不上了 —— 而收据的用途正是"对上"。
2. **重复与越界一律拒绝，而不是裁剪**：静默跑得比写下的少，会让收据的**条数与命令不符**。
   §38.2 里那条"序号口径必须与 `apply` 一致"的约束在这里同样成立 —— 少跑一条看起来和跑对了一样。

## 39.4 一处错误信息质量修正：`verify --index 1` 不该报 `got 5`

`keys=[]` 会被 default 成**全部 5 套**，于是旧实现报
`--index needs exactly one set, got 5: daily-digest, review-queue, …` ——
操作员**一个 key 都没写**，却被告诉"你给了 5 个"。这个歧义的唯一区分点是**调用方到底有没有传 key**，
所以函数开头留下 `given = list(keys)`，只在 `not given` 时换用 `--index needs a set key, e.g. …`。

⚠️ **不要拿 `len(keys) == len(SETS)` 当判据**：显式写全 5 个 key 也会命中这条分支，消息同样是错的
（首版就是这么写的，在写用例时才暴露）。判据必须来自**参数是否存在**，不能来自**参数的个数**。

## 39.5 新增的静态守卫（+9 个用例 / +9 个 subtest）

`VerifySelectionTests` 原有 7 条用例的调用点从 `int` 改为**元组**（如 `([key], (1,))`），并新增 4 条：

| 用例 | 钉住 |
|---|---|
| `..._multiple_indexes_run_in_the_order_typed` | `(2, 1)` 不被排成 `(1, 2)` |
| `..._duplicate_index_is_rejected_instead_of_de_duplicated` | 消息含 `duplicate` |
| `..._one_bad_index_rejects_the_whole_multi_index_request` | 越界序号被点名、整请求拒绝 |
| `..._index_without_a_set_key_says_so_instead_of_counting_all_of_them` | 两条消息**互不塌缩**（一条含 `set key` 且不含默认计数；另一条含 `got <n>`）|

新增 `IndexListParsingTests`（5 条）：

| 用例 | 钉住 |
|---|---|
| `..._single_value_parses_to_a_one_tuple` | `"30"` → `(30,)` |
| `..._multiple_values_parse_in_order` | `"27,30"` / `"30,27"` 保持顺序 |
| `..._surrounding_whitespace_is_tolerated` | `" 27 , 30 "` → `(27, 30)` |
| `..._empty_entry_is_rejected` | `""` / `","` / `"27,"` / `",27"` / `"27,,30"` **全拒** |
| `..._non_integer_entry_is_rejected` | `"a"` / `"27,b"` / `"2.5"` / `"1 2"` **全拒** |

→ 该文件：**15 passed + 65 subtests** → **24 passed + 74 subtests**。

⚠️ **为什么连 `_parse_index_list` 也要静态钉住**：argparse 只把错误表现为**退出码**。
一个把 `27,,30` 读成 `27,30`（**丢掉空项**而不是拒绝笔误）的解析器，在端到端看**与正确的完全一样** ——
两遍都"跑通了 2 条"。这类"只能靠退出码观测"的行为，正是 §38.2 那条"能静态判定就静态钉住"的适用面。

## 39.6 验证

| 检查 | 结果 |
|---|---|
| `verify whitelist-fixtures --index 27,30`（端到端）| 2 条**全过**，`green before/red after/green after restore/bytes restored` 皆 `True`；表头**只打一次**；收据 `2 个变异校验完毕`；exit **0** |
| `verify whitelist-fixtures -i 30,27` | 输出顺序**30 在前、27 在后**（按输入，非清单顺序）；exit **0** |
| `verify whitelist-fixtures --index 27,27` | 拒绝：`duplicate --index: 27`（exit **1**）|
| `verify whitelist-fixtures --index 27,` | 拒绝：`empty entry in --index '27,'`（exit **2**，argparse）|
| `verify whitelist-fixtures --index 27,x` | 拒绝：`--index expects integers, got 'x'`（exit **2**）|
| `verify whitelist-fixtures --index 0,30` | 拒绝：`index out of range: 0 (whitelist-fixtures 有 30 个)`（exit 1）|
| `verify whitelist-fixtures daily-digest --index 1` | 拒绝：`--index needs exactly one set, got 2: …`（exit 1）|
| `verify --index 1` | 拒绝：`--index needs a set key, e.g. …`（exit 1）|
| `pytest tests/test_mutation_verify.py tests/test_decision_quality_service.py` | **58 passed + 74 subtests**（junit `failures=0 / errors=0`）|
| `ruff check scripts/mutation_verify.py tests/test_mutation_verify.py` | `All checks passed!` |
| 两轮端到端之后的 **30 文件** sha256 快照 | **changed = 0**（26 个该套变异目标 + 本批 4 个改动文件）|
| `scripts/eol_audit.py`（4 个改动文件的行尾画像）| `line-ending damage: none` |

**W27 本次一次通过**（`green before True`）—— 与 §37.6 的归因一致：同一条变异、同一个守卫，
换成一条独立命令就正常。**这不是替换上次的结论，而是同一归因的第二次观测**：
上次的 `guard already RED before mutation` 来自 `safe-delete` 拦截器对**退出码**的污染，不是变异或守卫的问题。

## 39.7 本节未做

| 事项 | 状态 |
|---|---|
| `apply <set> <index>` 也支持多值 | ✅ **已在 §四十 完成**（并顺带做了区间语法与写入前的原子校验）。本节当时判断"不必与 `verify` 对称"**被推翻** —— 理由见 §40.2：两条命令共用同一个"位置"概念，共用解析器与选择器比"记得同步两处实现"更可靠 |
| 收敛 13 个方向常量（**已评估**：§四十二）| ⏳ **仍未收敛**，待拍板（§42.6 四方案）|
| `api.ts` union / 51 个 MIXED 文件 | ⏳ 待拍板 |
| §35.5 修法 (a)+(d) | ✅ **已在 §四十一 完成**（本节的页脚早于它，故此处按未交付内容更新）|
| 提交 | ⏳ 未提交（等指令）|

**提交状态**：本节改 **2 个文件**（harness + 它的静态守护测试）+ 本文档。**未提交**（等指令）。

> 追加（2026-09-30）：本节已提交 —— 代码/测试在 **`d57d72a`**，本文件在 **`727e70e`**。页脚原文保留为时点快照。

---

# 四十、`apply` 对齐同一条序号口径：多值、区间，与写入前的原子校验

## 40.1 起因

§三十九 把 `verify --index` 扩到多值 + 区间，而 `apply` 仍是**单个 `int`**。
§38.2 早就写下过一条规则："序号口径必须与 `apply` 一致" —— 两边不一致时，**同一个序号在两条命令里会指向不同的变异**。
当时那是一条靠评审维持的约定；本节把它变成**结构**：两条命令共用**同一个解析器**与**同一个选择器**。

## 40.2 共用解析器 + 共用选择器

| 命令 | 改动前 | 改动后 |
|---|---|---|
| `apply <set>` | `index` 是 `nargs="?"` + `type=int`（0 或 1 个）| `nargs="*"` + `type=_parse_index_list` |
| 选择 | `cmd_apply` 内联 `if not 1 <= index <= len(...)` | `_select_verify_targets([key], positions)` —— **与 `verify` 同一个函数** |

于是这三种写法都成立：

```
python scripts/mutation_verify.py apply review-queue 5 8
python scripts/mutation_verify.py apply review-queue 27-30
python scripts/mutation_verify.py apply review-queue 1-3,7
```

每个 token 解析成一个元组（`5 8` → 两个一元组，`27-30` → 一个四元组），扁平化后交给选择器。

**为什么这比"两处各自实现、约定同步"强**：`§38.2` 那个约束现在的成立方式是**共用函数**，
不是"评审时记得检查" —— 一个靠记忆维持的不变式迟早会漂移，而漂移的表现是"序号指向了另一条变异"，看起来像成功。

⚠️ `verify --index 27 30` 的歧义（被读成两个 set key）在 `apply` **不存在**（`key` 是带 `choices` 的单值位置参数，`27` 匹配不上任何 set key 会直接报错）。
但 `apply` **仍然用逗号**：让两条命令支持不同的分隔符，只会制造"哪条命令支持哪种"的记忆负担。

## 40.3 `--index` 支持区间 `a-b`

| 输入 | 结果 |
|---|---|
| `27-30` | `(27, 28, 29, 30)` |
| `27-27` | `(27,)` |
| `1-3,7` | `(1, 2, 3, 7)` |
| `30-27` | **拒绝**：`--index range '30-27' is reversed; write 27-30` |
| `1-` / `-1-2` / `1-2-3` | 落到整数错误分支（不匹配「数字-数字」形状）|
| `0-2` | **解析通过**为 `(0, 1, 2)`，由边界层拒绝 `0` |

两条判据：

1. **只允许升序**。位置决定输出顺序，静默把 `30-27` 反转成 `27-30` 会让收据与命令行对不上（与 §三十九 同一条判据）。
   错误消息里**直接给出可照抄的修法**。
2. **区间不在解析层做边界检查**。`0-2` 解析通过、由 `_select_verify_targets` 拒绝 ——
   否则 `--index 0` 与 `--index 0-2` 会长出**两种说法**解释同一件事。
   判据：**同一类错误只能有一个消息来源**。

## 40.4 `apply` 写入前的原子校验：`_apply_preflight`

多值 apply 引入一个新风险：**部分应用** —— 第一个文件已改、第二根针已失效，于是工作树既不是原始态也不是目标态。
位置越多，"现在到底改到哪一步了"越难推理。→ **在校验完成之前，不写任何一个字节。**

| 检查 | 报出 |
|---|---|
| 文件存在 | `{position}: missing file {path}` |
| 针**恰好命中 1 次** | `{position}: needle appears Nx in {path} (expected 1). If the file's line endings changed, fix the needle rather than loosening this check.` |
| 同 group 被选中 ≥2 条 | `positions 5, 6 share group 'cli-flag-guard': they rewrite the same bytes in opposite directions, so applying both would leave neither mutation in place` |

**三条一起报**，不是遇到第一个就停 —— 一条条修要来回多趟（这也是它返回 `list[str]` 而非直接 `raise` 的原因之一）。

顺带：`_apply_preflight` 也接管了**全量** `apply` 的校验，于是全量路径同样获得了原子性（改动前是"边检查边写"）。

## 40.5 「跳过」与「拒绝」的分界

同一情形（一个 group 的成员被同时涉及）有两种处置，**分界是"意图是谁给的"**：

| 场景 | 处置 | 理由 |
|---|---|---|
| `apply <set>`（全量）| **跳过**同组后续成员并说明 | 操作员要的是"全部"，而一个 group 的成员不可能同时成立；这里替他裁剪是合理的 |
| `apply <set> 5 6`（点名两条）| **拒绝** | 两个敲出来的位置是**明确请求**，不是可以替操作员裁剪的便利扫描 |

## 40.6 顺手发现：preflight 的针检查同时兜住「跨状态误用 group 的另一半」

冒烟时踩到（**是我漏了一步 revert**，不是命令的问题）：

1. `apply review-queue`（全量）→ 应用 C1–C5（**C6 被跳过**）
2. **没有 revert** 就执行 `apply review-queue 6`
3. → `refusing to apply, 1 problem(s): 6: needle appears 0x in backend/scripts/review_queue_cli.py ...`，**exit 1，未写任何文件**

C5/C6 是**同一段字节的两个相反改写** → C5 应用之后，C6 的针必然消失。
→ 这说明两道防线**各守一块**：`group` 检查管「同一次选择之内」，针检查管「工作树已经被改过」。
两次都**拒绝并且不写**。结论是"拒绝得对"，不是"命令有 bug"。

## 40.7 新增的静态守卫（+15 用例 / +10 subtests）

`IndexListParsingTests` **+5**：区间展开（含 `27-27`）· 与逗号组合（`1-3,7` / `7,1-3`）· 降序被拒且**把修法写进消息** · 畸形区间落到整数错误 · **`0-2` 解析通过而由边界层拒绝**。

新增 `ApplyPreflightTests`（**7 条**）：

| 用例 | 钉住 |
|---|---|
| `..._an_ungrouped_selection_is_accepted` | 无 group 的选择不报问题 |
| `..._naming_both_members_of_a_group_is_a_problem` | 恰好 1 个问题，消息含 group 名与 `5, 6` |
| `..._naming_one_member_of_a_group_is_fine` | 同组**单条**通过（否则 `apply <set> 5` 会被自己挡掉）|
| `..._a_needle_that_no_longer_matches_is_a_problem` | `appears 0x` |
| `..._a_missing_file_is_a_problem` | `missing file` |
| `..._problems_do_not_stop_at_the_first_one` | 3 根坏针**一次全报**（3 条问题）|
| `..._a_needle_that_matches_more_than_once_is_a_problem` | 「命中 >1」分支（用**空针**打：它在每个位置都命中，不依赖具体文件内容）|

新增 `WholeSetApplyTests`（**3 条**）：`_whole_set_targets` 的「5 应用 / 1 跳过」· 无 group 的 set 全应用 · **全量选择必须过它自己的 preflight**（否则 `apply <set>` 在它**唯一存在的场景**下就是死的）。

→ 该文件：**24 passed + 74 subtests → 39 passed + 84 subtests**。

⚠️ 为让「跳过规则」可静态钉住，全量路径的收集逻辑抽成了纯函数 `_whole_set_targets(mutation_set) -> (to_apply, skipped)`
—— 沿用 §38.2 那条判据：**能靠读代码判定的，就不该只靠一次运行的 stdout 保护**。

## 40.8 验证

| 检查 | 结果 |
|---|---|
| `apply voided-trade 1 2`（跨 2 个文件）| `mutated 1` + `mutated 2`；`git diff --stat` 显示**两个文件各 1 行**；exit **0** |
| `apply voided-trade 1-3`（区间）| 3 条写入：`prediction_store.py` 1 处 + `simulated_trade_store.py` 2 处（diff：`2 +-` / `4 ++--`）|
| `apply review-queue`（全量）| `mutated 1..5` 然后 `skipped 6: C6 … [与同组变异互斥…]`；exit **0** |
| `apply review-queue 6`（干净状态）| 单独应用成功（证明 C6 的针在原始态存在，§40.6 的 0x 是**跨状态**误用）|
| `apply review-queue 5 6`（同组两条）| 拒绝：`positions 5, 6 share group 'cli-flag-guard' …`；exit **1** |
| `apply review-queue 1 1` | 拒绝：`duplicate --index: 1`；exit 1 |
| `apply review-queue 0` / `apply voided-trade 99` | 拒绝：`index out of range: …`；exit 1 |
| `apply review-queue 5,x` | 拒绝（argparse，**exit 2**）：`--index expects integers or ranges, got 'x'` |
| `verify voided-trade -i 1-2`（区间在 verify 端）| 2 条全过，四阶段皆 `True`；exit **0** |
| `pytest tests/test_mutation_verify.py tests/test_decision_quality_service.py` | **73 passed + 84 subtests**（junit `failures=0 / errors=0`）|
| `ruff check scripts/mutation_verify.py tests/test_mutation_verify.py` | `All checks passed!` |
| 所有 apply/revert 冒烟之后：**33 个变异目标**的 sha256 | **changed = 0** |
| 同上，37 文件全量快照（33 目标 + 本批 4 个改动文件）| changed = **2** —— 且这 2 个正是**快照之后我又编辑过**的 harness 与它的测试（属 extra 组，不在变异目标内）→ 见下方说明 |
| `scripts/eol_audit.py`（4 个改动文件）| `line-ending damage: none` |

⚠️ **关于 changed = 2**：报告数字时必须能解释差额。本次的做法是先跑一次 37 文件全量核对（changed = 2），
再**只对 33 个变异目标**重跑一次（changed = 0）—— 两条一起才说明"变异的还原是逐字节的，而我自己的编辑被如实计入"。
只报一个数字而不给分组，会把"我自己改的文件"读成"还原不干净"。

## 40.9 本节未做

| 事项 | 状态 |
|---|---|
| 收敛 13 个方向常量（**已评估**：§四十二）| ⏳ **仍未收敛**，待拍板（§42.6 四方案）|
| `api.ts` union / 51 个 MIXED 文件 | ⏳ 待拍板 |
| §35.5 修法 (a)+(d) | ✅ **已在 §四十一 完成**（本节的页脚早于它，故此处按未交付内容更新）|
| 提交 | ⏳ 未提交（等指令）|

**提交状态**：本节改 **2 个文件**（harness + 它的静态守护测试）+ 本文档。**未提交**（等指令）。

> 追加（2026-09-30）：本节已提交 —— 代码/测试在 **`d57d72a`**，本文件在 **`727e70e`**。页脚原文保留为时点快照。
> §40.8 那张表里"所有 apply/revert 冒烟之后 33 个变异目标 changed = 0"是在**提交之前**测的，
> 提交本身**没有改动任何被变异过的文件**，所以该结论现在依然成立（`git diff HEAD` 为空即证）。

**提交历史（本批）**：`d57d72a`（代码/测试）+ `727e70e`（文档）→ `main..HEAD` = **14**，工作树干净，**未 push**、**未开 PR**、**未声称已合并**。

> **追加（2026-10-01）**：上面那句"未 push、未开 PR"以及同节表里的"未提交"**已作废** —— 本批已随
> **PR #111** 推送并**已于 2026-10-01 合入 main**（merge commit **`cbbcf5e`**）。完整证据见 §47.10 末尾的追加。

---

# 四十一、§35.5 修法 (a)+(d) 落地：loopback 探测显式绕过环境代理

## 41.1 结论先行

| 问题 | 答案 |
|---|---|
| §35 那条"环境级"失败现在还是失败吗 | **不是** —— 本机 `test_backup_restore_drill.py::BackupRestoreDrillTests::test_a_real_archive_restores_every_store_to_its_configured_path` **通过**（§41.8 有逐项读数）|
| 改了什么 | `restore_stores` 新增 `_is_loopback_url()` / `_open_health_url()`；`_check_service_running()` 的 Windows 分支改走它们 |
| 会改变"远端健康检查"的语义吗 | **不会** —— 绕过**只对 loopback 生效**；`PMRF_HEALTHCHECK_URL` 指向反向代理时仍走环境代理（§35.5 明写的约束）|
| 有配套守卫吗 | **有**：G3 的 4 条旧用例**重指**到新 seam、新增 **5 条**、并**登记 4 条变异（L1–L4）**走四阶段 |
| (b)/(c) 呢 | **仍然不做**（§41.6），理由与 §35.5 一致 |

## 41.2 改动 1：新增两个函数，改一处调用（`backend/scripts/restore_stores.py`）

| # | 位置 | 改动 |
|---|---|---|
| 1 | 顶部导入 | `ipaddress` / `urllib.error` / `urllib.parse` / `urllib.request` 提到**模块级**（原先 `urllib.request` / `urllib.error` 的 import 在 `_check_service_running()` **函数体内**）|
| 2 | `_is_loopback_url(url)` | **新增**：`host == "localhost"` **或** `ipaddress.ip_address(host).is_loopback`；`urlsplit` 抛 `ValueError`、或 host 为空 → `False` |
| 3 | `_open_health_url(url, timeout)` | **新增**：loopback → `build_opener(ProxyHandler({}))`；非 loopback → 默认 `urllib.request.urlopen` |
| 4 | `_check_service_running()` Windows 分支 | `urllib.request.urlopen(req, …)` → `_open_health_url(health_url, timeout)`；删掉函数体内联的两条 import；docstring 与 `:286-293` 附近的注释各补一段"**为什么**" |

**为什么必须限定 loopback**（沿用 §35.5 原话）：问的是"**本机**这个端口有没有人听"。运维若故意把
`PMRF_HEALTHCHECK_URL` 指向反向代理，硬关代理会**改变这条检查的含义** —— 所以绕过写在"是不是 loopback"的**判断之后**，而不是无条件生效。

**明确没有改的方向**：`except urllib.error.HTTPError: return True`（把 4xx/5xx 当"服务活着"）**一字未动**。
那是 §35.2 记录过的**有意的保守偏向** —— 选项 (b) 要求"只认 200"，会**退回**"降级但仍在跑的服务被判成没跑 → 覆盖活库"的旧缺陷。

## 41.3 改动 2：G3 的 4 条旧用例重指到新 seam（**为什么必须重指**）

原 4 条用例 patch 的是 `urllib.request.urlopen`。新代码对 loopback 走 `build_opener(...).open(...)`，
**`urlopen` 不再被调用** → 改完代码先跑，`test_windows_path_returns_true_on_http_503` 与
`test_windows_path_uses_health_endpoint` **`2 failed`**。

→ 4 条统一改成 `patch.object(restore_stores, "_open_health_url", …)`：**把 patch 点从"库函数"换成"我们新加的那一层"**。

这**不是**"为了让测试通过而改测试"：这正是 §35.5 那条要求——"修法要有配套用例，否则'修好了'无从验证"。
patch 点若不落在新 seam 上，测的仍是**旧路径**，新代码分支一条都盖不到。

## 41.4 新增的 5 条用例（`backend/tests/test_p0_fixes_round2.py`）

| 用例 | 钉住 |
|---|---|
| `test_loopback_urls_are_recognised` | `http://localhost:` / `http://LOCALHOST:` / `127.0.0.1` / `127.1.2.3` / `[::1]` 五种拼法都算 loopback（**大小写**与**非 `127.0.0.1` 的 127/8** 都覆盖）|
| `test_remote_urls_are_not_treated_as_loopback` | 域名 / `192.168.*` / `10.*` / `0.0.0.0` / **空串** 都不是 —— 绕过的**边界**，与上一条互为负控 |
| `test_a_loopback_probe_builds_an_opener_with_no_proxy` | loopback 时**恰好**调用 `ProxyHandler({})` 一次、`build_opener` 一次、`opener.open` 一次（钉"**空**"这个字面量，不是"有没有 build_opener"）|
| `test_a_remote_probe_keeps_the_default_opener` | 远端时 `build_opener` **一次都不许调**、`urlopen` **恰好一次** |
| `test_a_proxy_answering_everything_cannot_make_a_dead_port_look_alive` | §35 的**端到端复现**：真起一个对一切 GET 回 502 的代理（`_Always502Proxy`），真占住一个**不可连接**的 loopback 端口（`bind` 后不 `listen`），置 `HTTP_PROXY=…` 且 `NO_PROXY=` 空，断言 `_check_service_running()` 为 **`False`** |

⚠️ 最后一条是**这五条里唯一能证伪原缺陷的**：前四条断言的是"调用了哪个函数"，这一条断言的是
"**代理替死端口答了 502，而结论仍然是不能恢复**"。只留前四条，一个"分支判断正确但 opener 仍读环境变量"的实现照样全绿（§41.5 的 L4 就是拿这件事做的变异）。

## 41.5 四条变异 L1–L4（新 set `restore-loopback-probe`）

`mutation_verify.py` 现在 **6 套 / 64 个变异**。新 set 的动力是 §35.5 那个修法**有两半**（判断"是不是 loopback"、
以及"绕过时 handler 空不空"），任一半失效都能让整套守卫仍绿 —— 所以拆成四条：

| # | 变异 | 打的字节 | 期望红的守卫 |
|---|---|---|---|
| L1 | loopback 目标改用默认 opener（**绕过方向反转**）| `if not _is_loopback_url(url):` → `if _is_loopback_url(url):` | 3 条（loopback 建 opener / 远端保默认 / **死端口**）|
| L2 | `_is_loopback_url` 的 **localhost 名字**分支 `return True` → `return False` | `if host == "localhost":` 那一支 | 2 条（loopback 识别 / **死端口**）|
| L3 | `_is_loopback_url` 的 **IP 字面量**分支 → `return False` | `return ipaddress.ip_address(host).is_loopback` | 2 条（loopback 识别 / **死端口**）|
| L4 | 绕过用的 handler 退回**读环境变量**（`ProxyHandler({})` → `ProxyHandler()`）| `build_opener(urllib.request.ProxyHandler({}))` | 1 条（loopback 建 opener）|

**四阶段实测**（`verify restore-loopback-probe`，exit **0**）：

```text
[OK ] restore-loopback-probe  1. L1 …  green before True | red after mutation True | green after restore True | bytes restored True
        mutated run -> 3 failed, 15 deselected in 1.51s
[OK ] restore-loopback-probe  2. L2 …  mutated run -> 2 failed, 2 passed, 16 deselected, 3 subtests passed in 3.30s
[OK ] restore-loopback-probe  3. L3 …  mutated run -> 4 failed, 1 passed, 16 deselected, 2 subtests passed in 1.35s
[OK ] restore-loopback-probe  4. L4 …  mutated run -> 1 failed, 17 deselected in 0.81s
=== 4 个变异校验完毕 ===
```

四处读数都**对得上头条差异**，不是"红了就行"：

- **L2 的 `2 passed, 3 subtests`**：名字分支被改坏后，`test_remote_urls_are_not_treated_as_loopback`（5 个 subTest）
  与 `test_a_loopback_probe_builds_an_opener_with_no_proxy` 仍绿 —— 它们不读名字分支；红的 2 条与 subtests 3 个
  正对应"`localhost` 两个 URL 识别不了"+"死端口复活"。
- **L3 的 `4 failed, 1 passed, 2 subtests passed`**：IP 分支被改坏后，`1 passed` 是远端那条、`2 subtests passed`
  恰是 `localhost` / `LOCALHOST`（走名字分支）；`4 failed` 是 3 个 IP 拼法 + 死端口。
- **L1 的 3 条全红**：方向反转是"最狠"的一种，三个守卫一起响。

**顺序说明**：`test_the_five_sets_are_present` 已改名为 **`test_the_six_sets_are_present`** 并追加 `"restore-loopback-probe"` ——
沿用 §24 那条"故意有序且精确"的约定（改名是**预期动作**，不改名才说明这个断言没被人看见）。守护测试：
**39 passed + 84 → 89 subtests**，差值 **+5** 能算平：`+4` 来自 `test_the_selected_mutation_is_the_inventory_object`
（每个变异一个 subTest，60→64）、`+1` 来自 `test_the_whole_set_selection_passes_preflight`（每个 set 一个 subTest，5→6）。

## 41.6 (b)/(c) 仍然不做

| 选项 | 决定 | 理由（与 §35.5 一致，此处补"为什么现在仍不该做"）|
|---|---|---|
| (b) 收窄"算活着"的响应 | **不做** | 会**退回** `:286-293` 注释记录过的旧缺陷（503 降级被误判成"没跑"→ 覆盖活库）。而且本次修法**已经**在**正确的层次**上解决了假警告 —— 不是靠收紧判据，是靠**问到本机的端口**。收紧判据是在**掩盖**症状 |
| (c) 改测试期望（允许该条 warnings 出现）| **不做** | 断言本身是对的，而且 **(a) 落地后该断言恢复了原意**：警告现在只在服务真的占着库时出现。改期望等于把"警告可能恒亮"固化成契约 |

## 41.7 选项 (d)：RUNBOOK（**措辞必须跟着 (a) 改**）

写在 `docs/ops/RUNBOOK.md` → "Restoring from a backup" 的**第 3 步（Preview）之后**，
紧跟那句 `A [WARN] about the service means step 1 did not take effect.` —— 那正是这条假警告会被**误读**的位置。

四个要点，每个都有理由：

| 写什么 | 为什么 |
|---|---|
| 机制：`urllib` 只读**环境变量**代理，不读系统（WinINET）代理，而后者的 `ProxyOverride` **本来就含 `localhost`** | 解释"为什么浏览器打不开时正常、Python 却踩到"（§35.3 的原话）|
| "A **current** build bypasses the proxy for loopback URLs itself, so this should not fire." | (a) 已落地 → 必须在文本里说清**这版之后不该再亮**，否则运维会继续把它当成"代理噪音" |
| 仍给出 `NO_PROXY=localhost,127.0.0.1`（并提示小写 `no_proxy`）| 对**旧构建**（未含本次修法）仍然有用；这一条正是选项 (d) 原本要写的东西 |
| ⚠️ "On a current build, do **not** dismiss a surviving `[WARN]` as a proxy artifact" | **(a)+(d) 同时做的副作用**：单做 (d) 时 `[WARN]` 的含义是"**可能**是代理"；做完 (a) 它**恢复成"服务确实在跑"**。运维文本若不跟着改，就会把一处**真的危险信号**解释掉 |

**改动量**：`git diff --stat` → `1 file changed, 16 insertions(+)`，**零删除**（第 4 步的标题与正文原样保留，
是编辑过程中一度被吞掉又补回的 —— 见 §41.9）。

## 41.8 验证

| 检查 | 结果 |
|---|---|
| `verify restore-loopback-probe`（四阶段 × 4）| 4/4 全过，exit **0**；`bytes restored` 全 `True` |
| `pytest tests/test_mutation_verify.py` | **39 passed + 89 subtests**（junit `failures=0 / errors=0`）|
| `pytest tests/test_p0_fixes_round2.py` | **18 passed + 10 subtests**（改前 13 passed + 0；新增 5 条用例 + 10 个 subTest）|
| `pytest tests/test_runbook_restore_contract.py tests/test_p0_fixes_round2.py` | junit **tests=45 / failures=0 / errors=0 / skipped=0** |
| `pytest tests/test_restore_stores.py tests/test_backup_restore_drill.py` | junit **tests=108 / failures=0 / errors=0 / skipped=0**（summary `29 passed, 79 subtests`，34.52s）|
| **§35 那条环境级失败** | **通过** —— junit 里 `test_a_real_archive_restores_every_store_to_its_configured_path` **在 `passed` 集合内**（逐条按名核对，不靠"总数对了"）|
| `ruff check app/`（**CI 门**）| `All checks passed!` |
| `ruff check scripts/restore_stores.py scripts/mutation_verify.py tests/test_mutation_verify.py tests/test_p0_fixes_round2.py` | 只剩 `tests/test_p0_fixes_round2.py:27` 的 `F401 pathlib.Path` —— **既有问题**：`git show HEAD:… \| ruff check --select F401 --stdin-filename -` 同样报 **1** 条。CI 不跑 `tests/`（§三十五 已记），按约定**不动** |
| `scripts/eol_audit.py`（5 个改动文件）| `line-ending damage: none`（`mutation_verify.py` / `test_mutation_verify.py` = LF 两侧；其余 3 个 = `LF -> CRLF checkout`，即正常检出态）|

## 41.9 本节未做 / 一处过程留痕

| 事项 | 状态 |
|---|---|
| 第二台机器 / 带代理 CI 的复现（§35.4 第 3 行仍是"**推的**"）| ⏳ 未做 —— 本节的证据是**本机**上"从失败变成通过"，跨环境那条推断**没有被本次验证** |
| (b) / (c) | ✅ **有意不做**（§41.6）|
| 收敛 13 个方向常量 / `api.ts` union / 51 个 MIXED 文件 | ⏳ 待拍板（同 §34.9）|
| 提交 | ⏳ 未提交（等指令）|

⚠️ **一次过程留痕（值得记，因为它是"编辑工具会吞行"的实例）**：往 RUNBOOK 插引用块时，第一次编辑把
`**4. Apply it.**` 那行**一起删掉了** —— 因为 `old_string` 多带了一行、`new_string` 没带回来。
`git diff --stat` 显示 `16 insertions, 0 deletions` **才知道**（若只看插入数就会漏）。
→ 教训：**插入型编辑也要看 diff 的两侧计数**；"只加了东西"不该有删除数。

**提交状态**：本节改 **5 个文件**（`restore_stores.py` + `mutation_verify.py` + `test_p0_fixes_round2.py` +
`test_mutation_verify.py` + `docs/ops/RUNBOOK.md`）+ 本文档。**未提交**（等指令）。**未 push**、**未开 PR**。

> 追加（2026-09-30）：老板选的是"**先只提交代码/测试，文档留下一批**" → 代码与测试已在 **`06cb3cb`**
> （4 文件 **+303/-16**）提交；`docs/ops/RUNBOOK.md` 与本文档在**写下这条追注这一刻仍未提交**
> （页脚那句"未提交"保留不改 —— 它记的是提交前那一刻的实测状态，与 §34–§40 的做法一致）。
>
> **闸门只跑一道，覆盖本批的两个提交**（沿用 §㊴ 的先例：`d57d72a` 代码 + `727e70e` 文档共用同一道全量闸门）。
> §41.10 那张画像就是本批的闸门（`tests=7570 / 0 failures`）。文档提交相对它的增量只有两个 md：
> `RUNBOOK.md` 由 `tests/test_runbook_restore_contract.py` 覆盖（提交前已跑，全绿）；
> **本文档没有任何测试读它** —— 已 grep 取证：`backend/tests/` 里只有**两处注释/文档字符串**
> 提到本文件的路径（`test_event_source_utils.py:9` 与 `test_knockout_stage_whitelist_consistency.py:20`），
> **没有解析它的用例**，所以纯文档改动不会改变任何断言。

> **追加（2026-10-01）**：上面那句"未 push、未开 PR"以及同节表里的"未提交"**已作废** —— 本批已随
> **PR #111** 推送并**已于 2026-10-01 合入 main**（merge commit **`cbbcf5e`**）。完整证据见 §47.10 末尾的追加。

## 41.10 提交前的全量闸门：**0 失败**，以及那 **+36** 是怎么来的

`pytest tests/` → junit **tests=7570 / 0 failures / 0 errors / 11 skipped**（18m50s；summary
`6480 passed, 11 skipped, 1079 subtests passed`）。**这是本会话第一次全绿的全量闸门** —— §35 那条环境级失败**没了**。

与上一道闸门（7534 / **1 failure**）的差额 **+36**，必须算得平。做法是**取真值而不是推算**：
在 `HEAD` 上开一个临时 worktree，把涉及的两个测试文件在**新旧两棵树**上各跑一次 junit，拿到它们各自的贡献：

| 组成部分 | 上一道闸门 | 现在 | 差额 | 来源 |
|---|---|---|---|---|
| `test_mutation_verify.py` | 39 方法 + 84 子用例 = **123** | 39 + 89 = **128** | **+5** | `test_the_selected_mutation_is_the_inventory_object` 每个变异一个 subTest（60→64 = +4）、`…_selection_passes_preflight` 每个 set 一个（5→6 = +1）|
| `test_p0_fixes_round2.py` | 13 方法 + 0 = **13** | 18 + 10 = **28** | **+15** | 5 条新用例 + 两组 `subTest`（loopback 5 + 远端 5）|
| `test_backup_restore_drill.py` | 8 方法 + 47 = **55** | 8 + 63 = **71** | **+16** | ⚠️ **见下** |
| **合计** | **7534** | **7570** | **+36** | ✓ |

⚠️ **那 +16 不是新用例，是"失败藏起来的子用例"**：`test_a_real_archive_restores_every_store_to_its_configured_path`
里有 `with self.subTest(store=name):` 的循环（`:117`）。它**上一道闸门是失败的** → 循环没跑完 →
**16 次 subTest 根本没被记数**。修好之后循环跑完并全过 → 同一个文件的 `tests` 贡献从 55 涨到 71。

→ **可复用判据**：**全量 `tests=` 的差额不全是"新增用例"** —— 一条**既有的失败**会把它内部的 subTest
循环**从计数里挖掉**，修好时差额以"新增"的面目回来。所以对账要**按文件取真值**（开 worktree 跑旧树），
不能只用"我改了哪几个文件"推算；否则会得到 +20 与观察到的 +36 差了 16，然后白找半天。
反向也成立：**失败数从 1 变 0 时，tests= 必然同时变大**，这不是"多收集了文件"。

**长时任务对冲**（本仓惯例）：跑前对 6 个改动文件落 `sha256 + 字节数`，跑完逐条复核 → **changed = 0**
（本轮没有发生"就地编辑被回退"）。

---

# 四十二、13 个方向常量的收敛评估：**13 个常量 ⟷ 13 条变异 ⟷ 22 条守卫**，一一对应

本节**只做评估，不做收敛**（不代决）。§34.8 已给出"同值不同义"的逐项语义；本节把那份语义
**补成完整的 13 行**，并把"收敛要付什么代价"算到能拍板的粒度 —— 结论是：代价**不在"少一条变异"**，
而在"**harness 现在表达不了跨文件的 N 条守护**"（§42.4）。

## 42.1 结论先行

| 问题 | 答案 |
|---|---|
| "13 个常量"具体是哪 13 个 | 6 个同名 `_STRONG_DIRECTIONS` + 3 个异名旧常量（`_DIRECTIONAL` / `_CALLED_DIRECTIONS` / `_VALID_DIRECTIONS`）+ §34 提取的 4 个新名 —— 逐项见 §42.2 |
| 它们"**同值**"吗 | **只在"成员集合 = {YES,NO}"这一个层面上成立**：容器有 **3 种**（tuple 10 / set 2 / frozenset 1），且有 **1 处消费者是迭代而非成员判定**（`simulated_trade_store.py:509`）→ §42.3 |
| 它们"**同义**"吗 | **不**。§29.3 给的是 **5 种**语义，而按 13 行逐项落位需要 **8 种** —— §34 那 4 个新名里有 3 个（被展示的 / 被报告的 / 强结论）落在那 5 种**之外**（§42.2 末列）|
| 收敛的代价是什么 | **不是"少一条变异"**：13 条已登记变异是**一个文件一条、一个 guard_file 一条**；合并后要把 N 个文件的守护挂在一条变异上，而 `Mutation.guard_file` 是**单值** → 现状表达不了 → §42.4 |
| 本节的结论 | **维持 13 个具名独立**（现状）不是"懒得动"，而是**当前 harness 形态下合并会真丢锚定**；要合并得先扩 harness。方案表见 §42.6 |

## 42.2 普查：13 个常量的完整画像

`grep -rn "^_\(STRONG_DIRECTIONS\|DIRECTIONAL\|CALLED_DIRECTIONS\|VALID_DIRECTIONS\|REPORTED_DIRECTIONS\|STRONG_DISPLAY_DIRECTIONS\|TRADABLE_DIRECTIONS\|STRONG_EVENT_DIRECTIONS\)\s*[:=]" app/`

| # | 名字 | 定义 | 容器 | 消费点 | 定义处注释 | 语义 |
|---|---|---|---|---|---|---|
| 1 | `_STRONG_DIRECTIONS` | `replay/metrics.py:33` | **`set`** | `:150` `:160` | ❌ **无** | ① 可降级的强方向 **→ §45 已合并** |
| 2 | `_STRONG_DIRECTIONS` | `services/decision_quality_service.py:62` | `tuple` | `:284` | ✅ 7 行："the only ones **Stage A** of the downgrade pipeline may rewrite" | ① **→ §45 已合并** |
| 3 | `_STRONG_DIRECTIONS` | `services/execution_quality_service.py:49` | `tuple` | `:159` | ❌ **无** | ① **→ §45 已合并** |
| 4 | `_STRONG_DIRECTIONS` | `services/guardrail_service.py:63` | `tuple` | `:119` | ✅ 3 行："can be downgraded … by **these guardrails**" | ① **→ §45 已合并** |
| 5 | `_STRONG_DIRECTIONS` | `services/market_quality_service.py:45` | `tuple` | `:262` | ❌ **无** | ① **→ §45 已合并** |
| 6 | `_STRONG_DIRECTIONS` | `services/source_reliability_service.py:50` | `tuple` | `:409` `:498` | ✅ 2 行："can be downgraded to WAIT … by **this layer**" | ① **→ §45 已合并** |
| 7 | `_DIRECTIONAL` | `services/prediction_calibration_service.py:92` | `tuple` | `:159` | ✅ 2 行："have a **checkable stance**（`direction_correct` is None otherwise）" | ③ 可检验方向 |
| 8 | `_CALLED_DIRECTIONS` | `services/review_queue_detectors.py:79` | **`frozenset`** | `:148` `:262` | ✅ 3 行："A **committed call**, as opposed to an abstention… Decision Gate's act / provisional_act" | ④ 已下注的调用 |
| 9 | `_VALID_DIRECTIONS` | `services/domain_reliability_service.py:36` | **`set`** | `:113` | ❌ **无** | ② 合法输入值 |
| 10 | `_STRONG_DISPLAY_DIRECTIONS` | `api/routes/quality_metrics.py:43` | `tuple` | `:372` | ✅ 4 行："…the **anomalies endpoint** surfaces…；**Lives here rather than inline so a mutation can target it**" | ⑥ 被展示的强方向 |
| 11 | `_REPORTED_DIRECTIONS` | `memory/simulated_trade_store.py:463` | `tuple` | `:509` **（迭代）** | ✅ 4 行："reported in `trade_stats()["by_direction"]`…；**Lives here … so a mutation can target it**" | ⑦ 被报告的强方向 |
| 12 | `_TRADABLE_DIRECTIONS` | `services/event_intelligence_service.py:48` | `tuple` | `:1579` | ✅ 6 行："a **simulated (paper) trade** may be opened with" | ⑤ 可交易方向 |
| 13 | `_STRONG_EVENT_DIRECTIONS` | `services/conclusion_challenge_service.py:22` | `tuple` | `:51` `:92` | ✅ 5 行："**Two call sites** read this same membership test" | ⑧ 强结论方向 |

**合计**：13 个定义 / **17 个消费点**（16 个成员判定 + 1 个迭代）/ **3 种容器**（tuple **10**、set **2**、frozenset **1**）/ 注释 **9 有 4 无**。

⚠️ **一处必须点名的旁证**：`services/evidence_aggregation_service.py:45` 也叫 `_VALID_DIRECTIONS`，值是
`{"support","oppose"}` —— **同名不同值**（§29.4）。它**不在 13 之内**，但它说明"按名字合并"这条路本身就存在陷阱。

⚠️ **§29.3 的 5 种语义不够用**：那张表列的是 ①可降级 ②合法输入 ③可检验 ④已下注 ⑤可交易。
按上表逐项落位，§34 的 4 个新名里有 **3 个落在它之外** —— ⑥被展示（异常面）· ⑦被报告（统计面）· ⑧强结论（挑战面）。
→ **"13 个常量"不是"5 种语义 × 若干副本"，而是 8 种语义**。这一点让"看到 `{YES,NO}` 就并进来"更不成立。

## 42.3 "同值"也不完全成立：三种容器 + 一个迭代消费点

| 事实 | 取值 | 后果 |
|---|---|---|
| 容器类型 | `tuple` 10 · `set` 2（1、9）· `frozenset` 1（8）| 想合并就必须先**统一容器类型**；但 16 个消费点全是 `in` 判定 → **类型对它们无影响**（这一半是安全的）|
| 迭代消费 | `memory/simulated_trade_store.py:509` `for d in _REPORTED_DIRECTIONS:` | ⚠️ **唯一一处不是成员判定**。它把顺序带进 `trade_stats()["by_direction"]` 的 **JSON 键序** |
| 该迭代的"顺序"是契约吗 | **不是**。守卫断言是 `assertEqual(set(stats["by_direction"]), {"YES","NO"})`（`test_simulated_trade_store.py:311`）—— **顺序无关** | 但源码注释自己写了"Ordering only affects the JSON key order" |
| 若把它并成 `set`/`frozenset` 会怎样 | 本仓**没有** `PYTHONHASHSEED` 固定（`grep` 为空）→ 默认**每进程随机化字符串哈希** → 该 payload 的键序**逐次运行变化** | 对"看 diff 判回归"的消费者就是噪声。**结论：这个常量的容器类型不是自由选择** |

## 42.4 收敛的真实代价：不是"少一条变异"，是 **harness 现在表达不了跨文件的 N 条守护**

先摆事实（程序化取证，不是肉眼数）：**W17–W29 恰好 13 条，落在 13 个不同文件上，共挂 22 条具名守卫。**

| 变异 | 常量所在文件 | `guard_file` | 守卫数 |
|---|---|---|---|
| W17 | `guardrail_service.py` | `tests/test_guardrail_service.py` | 2 |
| W18 | `market_quality_service.py` | `tests/test_market_quality_service.py` | 2 |
| W19 | `execution_quality_service.py` | `tests/test_execution_quality_service.py` | 1 |
| W20 | `source_reliability_service.py` | `tests/test_source_reliability_service.py` | 1 |
| W21 | `prediction_calibration_service.py` | `tests/test_prediction_calibration_service.py` | 3 |
| W22 | `review_queue_detectors.py` | `tests/test_review_queue_detectors.py` | 3 |
| W23 | `domain_reliability_service.py` | `tests/test_domain_reliability_service.py` | 2 |
| W24 | `replay/metrics.py` | `tests/test_replay_metrics.py` | 2 |
| W25 | `simulated_trade_store.py` | `tests/test_simulated_trade_store.py` | 1 |
| W26 | `quality_metrics.py` | `tests/test_quality_metrics.py` | 1 |
| W27 | `event_intelligence_service.py` | `tests/test_event_intelligence_service.py` | 1 |
| W28 | `conclusion_challenge_service.py` | `tests/test_conclusion_challenge_service.py` | 2 |
| W29 | `decision_quality_service.py` | `tests/test_decision_quality_service.py` | 1 |

🔴 **关键**：`Mutation.guard_file` 是**单值**（`_run_guards` 跑 `pytest <guard_file> -q -k "<guards 用 or 连接>"`）。
所以合并 6 个 `_STRONG_DIRECTIONS` 之后，那条落在共享定义上的变异**只能挂一个文件的守卫** ——
另外 5 个文件的行为用例就**真的不再被任何变异锚定**。

→ **这不是"13 条塌成 8 条"的记账问题，而是一个能力缺口**。要把"一处定义 + N 个文件的 N 条守护"表达出来，
得先给 harness 加"多 guard_file"（这是**可测的新能力**，不是文档改动）。

⚠️ 顺带一条会**连带失效**的东西：那 6 条变异的针是 `_STRONG_DIRECTIONS = ("YES","NO")` 形式的**定义行**；
合并后该行不复存在 → `verify`/`apply` 会直接报 `needle appears 0x`（**拒绝，不是静默**），
所以合并必须**同批**改 harness 与本文档里 W17–W29 的历史编号说明。

**✅ 已落地**：能力见 **§四十四**（多 guard_file）；合并见 **§四十五**（实测 W31 之下 9 条守卫跨 6 文件**全红**）。

## 42.5 分组：只有 6 个同名 `_STRONG_DIRECTIONS` 是"共享会变契约"的候选

§8d 的收敛判据是"**是否共享同一个会变的契约**"。按 §42.2 的语义列：

| 组 | 成员 | 判据 | 可否合并 |
|---|---|---|---|
| ① 可降级的强方向 | #1–#6（6 个同名副本，8 个消费点）| **共享**："新增第 5 个方向 `HOLD` 时它可不可降级"是**同一个决定** | ✅ 唯一候选（但受 §42.4 制约）|
| ②–⑧ 其余 7 个 | #7–#13 | **各自独立**：`HOLD` 有没有可检验立场 / 算不算下注 / 能不能下单 / 要不要进统计 / 要不要在异常面展示 / 算不算强结论 —— **每一个都是独立的决定** | ❌ 合并 = 把 8 个决定压成 1 个 |

⚠️ 注意组 ① 里 #1 是 `replay/`（回放层）而 #2–#6 是 `services/`（服务层）—— 跨层。合并会引入
**6 个文件里第一次出现 `from app.…` 顶层导入**（取证：这 6 个文件现在 `grep -c "from app\."` **全为 0**）。
好消息是这次取证同时证明**没有循环导入风险**：新模块只要不反向导入，就永远是叶子。
（另有一条环境事实：`app/`、`app/services/`、`app/utils/` 都**没有 `__init__.py`**（namespace 包），
而 `app/replay/` **有**且只有一句 docstring —— 放共享模块时不必纠结包结构。）

## 42.6 四个方案（**决策表，不代决**）

| 方案 | 做什么 | 代价 | 收益 |
|---|---|---|---|
| **A. 不动**（现状）| 13 个具名独立保持原样 | 0 | 13 条变异 / 22 条守卫**全部继续承重**；语义各自在定义处有注释（9 个有、4 个无 → 见方案 D）|
| **B. 先扩 harness，再合并组 ①**（6 个 `_STRONG_DIRECTIONS`）| 给 `Mutation` 加"多 guard_file"；新建共享常量模块；6 处改 import | ① harness 新能力（**必须自带守卫用例 + 变异**，否则是新的空头）② 迁移 6 条变异的针与守卫 ③ 本文档 W 编号要加历史说明 ④ 6 个文件的导入面从 0 变 1 | 一处定义；"新增方向"只剩一个地方要改（可测：一条变异 + 9 条跨文件守卫）|
| **C. 合并全部 13 个** | 连 ②–⑧ 一起并 | **与 §29.3/§42.2 的语义结论相反**（8 种语义压成 1）；#11 的容器类型被绑死（§42.3）；#10/#11 的注释明写"**存在是为了能被变异**"，合并会删掉这两个变异靶 | 名字最少 |
| **D. 不合并，只补 4 处注释** | 给 #1 / #3 / #5 / #9（现在**没有注释**）各补一段"为什么是这个成员集" | **零契约变化**（注释不被变异针命中）| 消掉"4 个常量比名字更少信息"这个真实缺口 |

> **落地状态**：**B 已做完** —— ① harness"多 guard_file"见 **§四十四**；②③④（共享模块 + 6 处 import + 6 条变异收成 W31）见 **§四十五**。**D 已做完**（§四十三，其 4 处常量已随 §45 并入共享模块）。A 是"不动"、已不适用；C **未做且不建议**。

> **§47 追注（2026-09-30）**：C 的两条代价里有**一条已经过期** —— 「合并会删掉这两个变异靶」不再成立。
> §44 给 `Mutation` 加了「一条变异可挂多个 guard_file」、§46 又把阶段②判据升级为「**每文件各一红**」，
> 于是「多处定义」的锚定**可以集中到一个模块上而不丢**（§45 的 W31 已证明：1 处定义 / 6 个 guard_file / 9 条守卫；
> §47 的 7 条重定向又证明一次：1 个 `path` / 14 个 guard_file / 23 条守卫）。
> ⚠️ 但这**不等于原 C 该做**：C 只剩 **「8 种语义压成 1」** 一条真代价（§42.2/§42.5 的语义结论），**仍不建议**。
> §47 做的是**另一种形态**：一个模块里**保留 8 个具名常量** —— 只收敛**位置**，不合并语义。

→ 我的**倾向**（供参考，不是决定）：**先 D**（零风险、可立刻做）→ 组 ① 的合并（B）**排在 harness 扩完之后**，
否则会以"看起来只是少几条变异"的方式**真丢锚定**。C 不建议。

## 42.7 本节未做

| 事项 | 状态 |
|---|---|
| 收敛任何常量 | ⏳ **未动**（本节只评估；§42.6 待拍板）|
| 给 #1/#3/#5/#9 补语义注释（方案 D）| ✅ **已做** —— 见 **§四十三**（本节评估时尚未动，下一节落地）|
| 给 harness 加"多 guard_file" | ✅ **已做** —— 见 **§四十四**（方案 B 第 ① 步；②③④ 仍待做）|
| 统一 13+ 处 direction 归一化（`in ("YES","NO","WAIT","AVOID")` 四元组）| ⏳ 未动（**另一个集合**，§29.5：归一化点只有 2 处，其余 13+ 处原样比对）|

**本节只改本文档。未提交。未 push、未开 PR。**

> 追加（2026-09-30）：本节（含 §42.6 的追注）已提交 —— 本节只改本文档 ⇒ 本文件在 **`6341d93`**（1 文件 +842/−4）。
> ⚠️ 该提交的信息写的是 "record sections **43-47**"，但它的 diff **也把本节 §42（42.1–42.8）一并记入** ⇒ 本批实为
> **§42–§47**、信息少算了一节（**内容无误、已提交，未改写历史**）。页脚那句"未提交"是提交前的实测状态，**保留不改**（同 §34–§40 的做法）。

> **追加（2026-10-01）**：上面那句"未 push、未开 PR"以及同节表里的"未提交"**已作废** —— 本批已随
> **PR #111** 推送并**已于 2026-10-01 合入 main**（merge commit **`cbbcf5e`**）。完整证据见 §47.10 末尾的追加。

## 42.8 取证命令（本节每条事实的来源）

```bash
# 13 个定义行
grep -rn "^_\(STRONG_DIRECTIONS\|DIRECTIONAL\|CALLED_DIRECTIONS\|VALID_DIRECTIONS\|REPORTED_DIRECTIONS\|STRONG_DISPLAY_DIRECTIONS\|TRADABLE_DIRECTIONS\|STRONG_EVENT_DIRECTIONS\)\s*[:=]" app/
# 消费点（逐常量）
grep -rn "\b_STRONG_DIRECTIONS\b" app/          # 其余 7 个同理
# 6 个文件现在有没有 app 内导入（证明"导入面从 0 变 1"）
for f in app/replay/metrics.py app/services/{decision_quality,execution_quality,guardrail,market_quality,source_reliability}_service.py; do grep -c "from app\." "$f"; done
# 13 条变异 / 13 个 guard_file / 22 条守卫（程序化，不肉眼数）
cd backend && ../.venv/Scripts/python.exe -c "import scripts.mutation_verify as mv; s=mv.SETS_BY_KEY['whitelist-fixtures']; ws=[m for m in s.mutations if m.label.split()[0] in {f'W{i}' for i in range(17,30)}]; print(len(ws), len({m.path for m in ws}), len({m.guard_file for m in ws}), sum(len(m.guards) for m in ws))"
# 容器类型 3 种 + 迭代消费点
grep -rn "for d in _REPORTED_DIRECTIONS" app/
# 无 PYTHONHASHSEED 固定
grep -rn "PYTHONHASHSEED" backend .github
```

# 四十三、§42.6 方案 D：给 4 个「定义处无注释」的方向常量各补一段成员集理由

## 43.1 结论先行

| 问题 | 答案 |
|---|---|
| 本批做了什么 | §42.6 的**方案 D**：给 §42.2 里"定义处注释 = ❌ 无"的 **4 个**常量（#1 / #3 / #5 / #9）各补一段"为什么是这个成员集" |
| 契约变了吗 | **没有**。注释不进 AST、不被任何变异针命中；4 个常量的**赋值行一字未动** —— 4 根针对应变异（W18/W19/W23/W24）的命中数仍 = **1**（§43.3 程序化取证）|
| 为什么值得做 | §42.2 末列"定义处注释"这一列里 9 个 ✅、4 个 ❌；这 4 个恰是"**比名字更少信息**"的那批（名字说了是什么，没说为什么是这个成员集、谁在消费）|
| 验证 | 4 个文件共 **109** 条用例（0 failures / 0 errors / 0 skipped）+ `ruff check app/` 全过；4 文件行尾保持**纯 CRLF**；`git diff --stat` = **16 insertions / 0 deletions** |
| 提交 | **未提交**（§42 与 §43 同属一个未提交批次，见 §43.5）|

## 43.2 四处改动（逐处对齐消费点）

| # | 文件:行 | 常量 | +行 | 注释说清的事 |
|---|---|---|---|---|
| #1 | `app/replay/metrics.py:33` | `_STRONG_DIRECTIONS = {"YES","NO"}`（**set**）| +4 | 这是"4 方向词表按**是否对结果表态**切的一刀"；`add_phase_result`（`:134`）两边都读：强→弱 = 降级（`downgrades_caused` `:155`），阶段保持强而合并终值变弱 = 冲突（`conflicts_with_final` `:167`）|
| #3 | `app/services/execution_quality_service.py:49` | `_STRONG_DIRECTIONS = ("YES","NO")` | +5 | 不可执行（宽价差 / 薄流动性 / 陈旧价 / 高费）时，已表态的调用被降级为 WAIT（`build_execution_quality` 的方向建议步）；WAIT/AVOID 本就非表态，原样通过 |
| #5 | `app/services/market_quality_service.py:45` | `_STRONG_DIRECTIONS = ("YES","NO")` | +3 | 只有这些会被 `_apply_market_downgrade`（`:248`）改写为 WAIT —— 且仅在市场质量低于阈值**或**价差不可交易时；WAIT/AVOID 不动 |
| #9 | `app/services/domain_reliability_service.py:36` | `_VALID_DIRECTIONS = {"YES","NO"}`（**set**）| +4 | 这是"**可判定**"（而非"可降级"）这一支：`attribute_evidence` 跳过集合外的记录（WAIT/AVOID 没有可打分的 YES/NO 调用）；**放宽它就会开始给从未表态的记录打分** |

⚠️ #9 的注释特意点出"放宽的后果"，因为它的名字（`_VALID_`）与其余 3 处的 `_STRONG_` 不同 —— §42.2 已证**异名不等于异义、同名不等于同义**，注释的作用正是把"名字没说清的那一半"写死。

## 43.3 "零契约变化"是可验证的，不是声称

| 判据 | 取证 | 结果 |
|---|---|---|
| 变异针命中数不变 | 4 根针（W18/W19/W23/W24）的 `old` 字节 = 常量赋值行本身；注释插在**上一行** → `path.read_bytes().count(old)` | 4 处均为 **1**（与改动前相同）|
| 纯插入 | `git diff --stat -- <4 files>` | **16 insertions(+), 0 deletions(-)** |
| 不引入编码噪声 | `raw[:3]` 逐文件检查 | 4 文件均 **no-BOM** |
| 注释引用真实 | `grep -n` 对齐 `add_phase_result`(`:134`) / `downgrades_caused`(`:155`) / `conflicts_with_final`(`:167`) / `_apply_market_downgrade`(`:248`) | 一致 |
| 行尾不变 | 字节画像 `b.count(b"\r\n")` vs `b.count(b"\n") - crlf` | 4 文件均**纯 CRLF**（bareLF = 0）|

## 43.4 验证

```bash
cd backend
# 4 个相关测试文件（含 W18/W19/W23/W24 的具名守卫）
../.venv/Scripts/python.exe -m pytest tests/test_replay_metrics.py \
  tests/test_execution_quality_service.py tests/test_market_quality_service.py \
  tests/test_domain_reliability_service.py -q -p no:cacheprovider --junitxml=<outside-repo>
# CI 的 ruff 门
../.venv/Scripts/ruff.exe check app/
```

| 项 | 读数 |
|---|---|
| pytest（4 文件）| **109 testcases / 0 failures / 0 errors / 0 skipped** |
| 按类分布 | replay_metrics 13 · execution_quality 14 · market_quality 44 · domain_reliability 38 |
| `ruff check app/` | **All checks passed!** |
| 4 文件行尾增量 | `metrics.py` 277→281 · `execution_quality_service.py` 239→244 · `market_quality_service.py` 373→376 · `domain_reliability_service.py` 291→295 |

CRLF 增量（+4 / +5 / +3 / +4）与 §43.2 的注释行数**逐条相等** —— 即"插入的行数 = 新增的换行数"，无吞行。

## 43.5 本节未做 / 交接

| 事项 | 状态 |
|---|---|
| 运行**全量**闸门 | ⏳ 未跑（本节只动注释，用 4 个相关文件 + ruff 定向覆盖；全量闸门的成本判断见 §41.10）|
| 提交 §42 + §43 | ⏳ **未提交**（用户未指示提交）|
| §42.6 的方案 A / B / C | ⏳ **仍待拍板**（B 的前置是给 harness 加"多 guard_file"）|
| 统一 13+ 处 direction 归一化 | ⏳ 未动 |

**本节只改本文档 + 4 个源码文件的注释。未提交。未 push、未开 PR。**

> 追加（2026-09-30）：本节已提交 —— 4 个源码文件的注释与本节记录同在 **`d619f84`**（17 文件 +782/−200），
> 本文件在 **`6341d93`**（1 文件 +842/−4）。页脚那句"未提交"是提交前的实测状态，**保留不改**（同 §34–§40 的做法）；
> 表里 §43.5 的"提交 §42 + §43 ⏳ 未提交"与 §43.1 的"| 提交 | **未提交** |"同样作废（保留为时点快照）。

> **追加（2026-10-01）**：上面那句"未 push、未开 PR"以及同节表里的"未提交"**已作废** —— 本批已随
> **PR #111** 推送并**已于 2026-10-01 合入 main**（merge commit **`cbbcf5e`**）。完整证据见 §47.10 末尾的追加。

# 四十四、给 harness 加「一条变异可挂多个 guard_file」的能力（§42.6 方案 B 的第 ① 步）

## 44.1 结论先行

| 问题 | 答案 |
|---|---|
| 本批做了什么 | §42.6 **方案 B 的第 ① 步**：把 `Mutation.guard_file` 从**单值**扩成**可多值**（`str \| tuple[str, ...]`），让一条变异能同时锚定**多个文件**的守护 —— 这是"合并组 ① 六个 `_STRONG_DIRECTIONS`"的**前置能力**，不是合并本身 |
| 为什么必须单独成批 | §42.4 的结论：合并常量后那条变异**只能挂一个文件的守卫**，另 5 个文件的行为用例**真丢锚定**。所以**能力先于消费者**，且必须**自带守卫用例 + 自带变异**，否则就是把"少几条变异"换成了"多一个空头能力" |
| 能力落在哪 | 唯一 seam = `Mutation.guard_files()`（`str`/`tuple` 归一成 tuple）；唯一消费点 = `_run_guards()`（把**全部**文件交给**同一次** pytest，`-k` 跨合并后的收集集过滤）|
| 自带变异 | 新 set `multi-guard-file`（**H1**）：把 `_run_guards` 改成"只交第一个文件" → 跨文件守护静默失效 |
| 对现有变异的影响 | **0**。64 条既有变异一字未改（它们传单字符串 → `guard_files()` 归一成 1 元组 → argv 与改前**逐字节相同**）。现共 **7 套 / 65 个变异** |
| 验证 | H1 **四阶段全过**；既有 `restore-loopback-probe` **4/4 仍全过**（回归）；`test_mutation_verify.py` **43 passed + 91 subtests**；`ruff check app/` 全过 |
| 未做 | **没有合并任何常量**（§42.6 的 A/B/C 仍待拍板）；该能力此刻**无生产消费点**，由一条 pin 测试钉住（见 §44.4）|

## 44.2 能力：一个 seam + 一个消费点

`backend/scripts/mutation_verify.py`

```python
@dataclasses.dataclass(frozen=True)
class Mutation:
    ...
    guard_file: str | tuple[str, ...]      # 原本是 str

    def guard_files(self) -> tuple[str, ...]:
        if isinstance(self.guard_file, str):
            return (self.guard_file,)
        return tuple(self.guard_file)
```

`_run_guards` 的 argv 从 `mutation.guard_file` 改为 `*mutation.guard_files(),`：

```python
        [str(PY), "-m", "pytest", *mutation.guard_files(), "-q",
         "-p", "no:cacheprovider", "-k", " or ".join(mutation.guards)],
```

**为什么是"交给同一次 pytest"而不是"每文件跑一次"**：`-k` 的选择器对所有文件是同一个，
跨合并后的收集集过滤即可；某个 guard 名只在其中一个文件里存在时，`-k` 在那里**匹配不到**（无害）。
逐文件循环会多跑 N−1 次进程，还会让"哪些 guard 真的被选中"分散在 N 份输出里。
→ 归一化放在 `guard_files()`，好处是**别处都不必知道它是哪种拼写**：
旧用法（单字符串）与能力（多文件）走同一条路径。

## 44.3 第一条**自指**变异 —— 针必须拆成两段（本节踩了一脚）

H1 是仓库里**第一条以 harness 自身为靶**的变异（此前所有针都打在 app/ 与另外两个脚本上）。
它踩了一个**只在自指时出现**的坑：**`old=<针>` 这个字面量本身就含针文本**。

| 写法 | `read_bytes().count(old)` | 结果 |
|---|---|---|
| `old=b"*mutation.guard_files(),"` | **2**（`_run_guards` 里 1 处 + **定义里的字面量** 1 处）| `_apply_preflight` 拒绝（`expected 1`）|
| 拆成 `_GUARD_FILES_ARG = b"*mutation.guard_files()" + b","` | **1** | ✅ |

更阴的一次：改完定义后仍报 **2** —— 因为**我给这个针写的注释里逐字引用了它**
（`… a single ``b"*mutation.guard_files(),"`` …`）。**注释也是文件字节**，一样进计数。
→ 最终写法既拆了字面量，**也不在注释里复现针**（改成了描述）。

⚠️ **通用判据**：**自指变异（靶 = 载体脚本）的针不能以"完整字面量 + 注释引用"的形式出现在同一文件里**；
必须拆片段，并保证注释只**描述**不复现。这与"插入型编辑删除数必须为 0"是同一类：
**先算针在文件里的出现次数，再决定怎么写它**。

## 44.4 测试（+4 用例；1 处改名）

`backend/tests/test_mutation_verify.py`

- 新增 **`MultiGuardFileTests`**（4 条）：
  `test_a_single_guard_file_normalises_to_a_one_tuple` ·
  `test_several_guard_files_keep_their_order` ·
  **`test_run_guards_passes_every_named_file_to_pytest`**（H1 的守卫：**patch `subprocess.run`、断言 argv 含全部文件名**）·
  `test_no_mutation_names_several_guard_files_yet`（pin：能力此刻无消费者）。
- `test_the_six_sets_are_present` → **`test_the_seven_sets_are_present`**（追加 `multi-guard-file`），docstring 注明改名由来。
- `test_every_guard_file_exists_and_every_guard_is_named` 改为**遍历** `mutation.guard_files()`。

🔴 **为什么 H1 的守卫必须看 argv，而不是看返回值**：`_run_guards` 只回 `(是否全绿, 末行)`。
"只跑第一个文件"的实现**照样**返回一个像模像样的 `(True/False, …)` —— 返回值**区分不了**两种实现。
（与 §41.4 的"patch 点必须落在新 seam 上"同源：**守卫要钉住的是调用形状，不是它给人的印象**。）

## 44.5 验证

```bash
cd backend
# H1 四阶段
../.venv/Scripts/python.exe scripts/mutation_verify.py verify multi-guard-file
# 回归：既有单文件 set 路径未变
../.venv/Scripts/python.exe scripts/mutation_verify.py verify restore-loopback-probe
# 守护测试
../.venv/Scripts/python.exe -m pytest tests/test_mutation_verify.py -q -p no:cacheprovider --junitxml=<outside-repo>
```

| 项 | 读数 |
|---|---|
| H1 `verify` | `[OK] green before True \| red after mutation True \| green after restore True \| bytes restored True`；突变后 `1 failed, 42 deselected` |
| `restore-loopback-probe` 回归 | **4/4 全过**（单文件路径行为未变）|
| `test_mutation_verify.py` | **43 passed + 91 subtests**（改前 39 + 89；+4 用例、+2 subTest = 新 set 在"每变异/每 set 各一 subTest"的两条遍历里各 +1）|
| `ruff check app/` | All checks passed! |
| 两个被改文件行尾 | **纯 LF**（`mutation_verify.py` 1504 行、`test_mutation_verify.py` 514 行，bareLF 0）|
| 删除行 | 共 **9** 行，**逐条可对**（3 处在 `mutation_verify.py`：`guard_file: str` / `_run_guards` 旧 docstring / `mutation.guard_file,`；6 处在测试：改名 4 行 + guard_file 检查 2 行）|

## 44.6 本节未做 / 交接

| 事项 | 状态 |
|---|---|
| 合并组 ① 六个 `_STRONG_DIRECTIONS`（方案 B 的 ②③④）| ⏳ **未做**（下一批；本批只铺路）|
| 该能力的第一条**生产**变异 | ⏳ 未登记 —— 由 `test_no_mutation_names_several_guard_files_yet` 钉住，合并批必须删它并登记一条跨文件变异 |
| §42.6 的 A / C | ⏳ 仍待拍板 |
| 提交 | ⏳ **未提交** |

**本节只改 2 个 harness 文件（脚本 + 其守护测试）+ 本文档。未 push、未开 PR。**

> **追加（2026-09-30，§46）**：本表第 2 行（"该能力的第一条**生产**变异 ⏳ 未登记"）**已由 §45 结清** ——
> `test_no_mutation_names_several_guard_files_yet` 随合并批删除，W31 成为第一条（也是当前唯一一条）
> 多 `guard_file` 变异（6 个文件 / 9 条守卫）。第 3 行"该能力此刻无生产消费点"同理作废。
> 另外 §44.1 写的"唯一消费点 = `_run_guards()`（把**全部**文件交给**同一次** pytest）"是**当时**的形态；
> §46 把它改成"**每个 guard_file 一次** pytest"，阶段②判据也随之从"≥1 红"升级为"每文件各一红"（见 §46.2）。

> 追加（2026-09-30）：本节已提交 —— 2 个 harness 文件（脚本 + 其守护测试）与本节记录同在 **`d619f84`**（17 文件 +782/−200），
> 本文件在 **`6341d93`**（1 文件 +842/−4）。页脚与 §44.6 表里那句"未提交"是提交前的实测状态，**保留不改**（同 §34–§40 的做法）。

> **追加（2026-10-01）**：上面那句"未 push、未开 PR"以及同节表里的"未提交"**已作废** —— 本批已随
> **PR #111** 推送并**已于 2026-10-01 合入 main**（merge commit **`cbbcf5e`**）。完整证据见 §47.10 末尾的追加。

# 四十五、执行 §42.6 方案 B 的 ②③④：6 个 `_STRONG_DIRECTIONS` 并为 `app/utils/direction_vocabulary.py` 的一处定义

## 45.1 结论先行

| 问题 | 答案 |
|---|---|
| 本批做了什么 | §42.6 **方案 B 的 ②③④**：把 6 个同名 `_STRONG_DIRECTIONS` 并为 `app/utils/direction_vocabulary.py` 的**一处定义**；6 个文件改 import；**8 个消费点改名**；**6 条变异收成 1 条** |
| 为什么现在才能合 | §44 的多 guard_file 能力落地后，W31 能挂 **6 个 `guard_file` / 9 条守卫** —— 它是该能力的**第一个真实消费者**（§44.4 那条 pin 因此删除）|
| 成员集变了吗 | **没变**，仍是 `("YES", "NO")`。变的是**定义处**（6 → 1）与**名字**（`_STRONG_DIRECTIONS` → 公有 `STRONG_DIRECTIONS`）|
| 名字真的统一了吗 | **是**：`grep -rn "_STRONG_DIRECTIONS" backend/app` 现在**只剩 `.pyc` 缓存**，源码里 **0 处**（§45.6 取证）|
| 丢锚定了吗 | **实测没有**：W31 变异后 **`9 failed, 193 deselected`** —— 跨 **6 个文件的 9 条守卫全部变红**（§45.4）|
| ⚠️ 必须点名的缺口 | harness 只**强制**"选中的守卫里 ≥1 条红"，**不强制**"每个文件各至少一条红"。本次是**实测 9/9**，不是被规则保证的 → §45.5 |
| 验证 | 全集合 `verify whitelist-fixtures` **25/25 全过**（EXIT 0）；9 个相关测试文件 **244 passed / 0 failed**；`ruff check app/` 全过；变异涉及文件快照 **changed = 0** |
| 未做 | §42.6 的方案 C（合并全部 13 个）仍**不建议**；其余待拍板项见 §45.7 |

## 45.2 改动：一处定义 + 6 处 import + 8 处改名

| # | 文件 | 删 | 加 | 8 个消费点 |
|---|---|---|---|---|
| ① | **`app/utils/direction_vocabulary.py`（新）** | — | 26 行 | 定义处（唯一）|
| ② | `app/replay/metrics.py` | `_STRONG_DIRECTIONS`（set）| import；`_WEAK_DIRECTIONS` 保留 | `:156` `:166` |
| ③ | `services/decision_quality_service.py` | 常量 + 8 行注释 | import | `:280` |
| ④ | `services/execution_quality_service.py` | 常量 + §43 注释 | import + 消费点注释 | `:162` |
| ⑤ | `services/guardrail_service.py` | 常量 + 3 行注释 | import | `:116` |
| ⑥ | `services/market_quality_service.py` | 常量 + §43 注释 | import | `:262` |
| ⑦ | `services/source_reliability_service.py` | 常量 + 2 行注释 | import | `:407` `:496` |

**容器取 tuple**：#1 原本是 `set`（其余 5 个是 tuple）。共享值取 **tuple** —— 因为**每个读者都是成员判定**、
没有一处迭代它（§42.3 已逐一核过），所以顺序无含义；tuple 让 repr 稳定且与多数派一致。合并**没有**把一个
"被迭代的容器"可变性偷偷带进来（§42.3 点名的那个坑在 #11，不在本组）。

**注释的去向**：§43 给 #1/#3/#5/#9 补的注释，其"**这组是什么**"那一半搬进了新模块 docstring；
"**这一层拿它做什么**"那一半留在消费点（如 #4 的 `# A committed call is downgraded to WAIT when the market
is not executable…`；#3 的 §36 历史留痕也随之下移到消费点，见 §45.3）。

## 45.3 新模块（`backend/app/utils/direction_vocabulary.py`，26 行 PURE-CRLF）

```python
"""The direction vocabulary shared by the downgrade layers (audit section 45).
...
* ``guardrail_service``          -- which directions its guardrails may rewrite
...
Only the *membership* is shared. What each layer does with a strong direction
stays local ...
History: these were six byte-identical copies until the merge in audit section
45. They were a ``tuple`` in five of the six and a ``set`` in ``replay/metrics``.
"""
from __future__ import annotations

STRONG_DIRECTIONS = ("YES", "NO")
```

**位置**：`app/utils/`（§42.5 已取证：`app/`、`app/services/`、`app/utils/` 都**没有 `__init__.py`**（namespace 包），
放这里不必纠结包结构）。**跨层**：它同时被 `app/services/*`（5 处）与 `app/replay/*`（1 处）导入 ——
§42.5 担心的"6 个文件第一次出现 `from app.…`"在这里兑现（导入面 0 → 1），但**无循环风险**：新模块
**不反向导入任何 app 模块**，是叶子。

**保留的历史留痕**：`decision_quality_service.py` 原注释里那句"`_build_rationale_body` 曾经重复同一字面量、
那个死分支已在 §36 移除，故本常量只有一个消费者"**没有被丢掉** —— 下移到了消费点（`:280` 上方）。

## 45.4 变异迁移：6 条 → 1 条

| 原 | 常量所在文件 | 原 guard_file | 原守卫数 |
|---|---|---|---|
| W17 | `guardrail_service.py` | `test_guardrail_service.py` | 2 |
| W18 | `market_quality_service.py` | `test_market_quality_service.py` | 2 |
| W19 | `execution_quality_service.py` | `test_execution_quality_service.py` | 1 |
| W20 | `source_reliability_service.py` | `test_source_reliability_service.py` | 1 |
| W24 | `replay/metrics.py` | `test_replay_metrics.py` | 2 |
| W29 | `decision_quality_service.py` | `test_decision_quality_service.py` | 1 |

→ 合成 **W31**：`path = app/utils/direction_vocabulary.py`，针 `STRONG_DIRECTIONS = ("YES", "NO")`，
**`guard_file` = 上面 6 个测试文件的元组**，`guards` = 上面 9 条具名守卫。

🔴 **四阶段实测**：`[OK]`，`green before True | red after mutation True | green after restore True | bytes restored True`，
突变后 **`9 failed, 193 deselected in 2.08s` —— 9 条全部变红**。这正是 §42.4 担心的那件事的反证：
合并**没有**让另外 5 个文件的用例失去锚定。

**编号沿用"只增不改"**：W17–W30 保持历史含义（它们各自的 `note` 里记着当初为什么建），
新增 **W31** 并在标签里注明"（合并 W17/W18/W19/W20/W24/W29）"。本仓计数：**7 套 / 60 个变异**（原 65），
`whitelist-fixtures` **30 → 25**。set 标题同步为"（W1–W30 + §45 合并的 W31）"。

## 45.5 ⚠️ 一个必须点名的缺口：**"≥1 条红" ≠ "每文件各一红"**

`_run_guards` 把 6 个文件交给**同一次** pytest，`verify` 判据是 `proc.returncode == 0` ——
即**只要选中的守卫里有任意一条失败**就算"红"。所以 W31 的四阶段**并不保证**"6 个文件的守卫各自都还在承重"；
它只保证"这条共享值仍然承重于至少一个消费点"。

- **本次实测**：`9 failed` —— 9 条守卫**全部**变红，因此"每个消费点的锚定都还在"这次**是事实**。
- **但它不是被规则保证的**：将来若某个文件的守卫退化成空头，W31 仍会绿（另 8 条照红）。
- 要把它变成**被保证**的，需要再扩 harness：**要求多文件变异的每一个 `guard_file` 都至少贡献一次失败**
  （即把返回值从"是否全绿"改成"每文件的结果"）。§42.6 记录方案 B 时**没有**包含这一步 —— 本节**不擅自加**，
  只把缺口写清楚，留作下一步（§45.7 的选项）。

## 45.6 验证

```bash
cd backend
# 全集合回归（25 个变异，四阶段）
../.venv/Scripts/python.exe scripts/mutation_verify.py verify whitelist-fixtures
# 9 个相关测试文件（守护测试 + 6 个消费文件 + domain_reliability 未变）
../.venv/Scripts/python.exe -m pytest tests/test_mutation_verify.py tests/test_guardrail_service.py \
  tests/test_market_quality_service.py tests/test_execution_quality_service.py \
  tests/test_source_reliability_service.py tests/test_replay_metrics.py \
  tests/test_decision_quality_service.py -q -p no:cacheprovider --junitxml=<outside-repo>
# 旧名是否真的消失（源码层面）
grep -rn "_STRONG_DIRECTIONS" app/       # 只剩 .pyc
```

| 项 | 读数 |
|---|---|
| `verify whitelist-fixtures`（全集合）| **25 个变异校验完毕 / 全部通过**，EXIT 0（10m51s）|
| W31 单条 | `[OK]`，突变后 **`9 failed, 193 deselected`** |
| 9 个测试文件 | **244 passed / 0 failed / 0 errors** |
| `test_mutation_verify.py` | **42 passed + 86 subtests**（改前 43 + 91；−1 = 删掉的 pin，−5 = 消失的 5 条变异的 subTest）|
| `ruff check app/` | All checks passed! |
| `app/` 里旧名残留 | **源码 0 处**（仅 `.pyc`）|
| 变异涉及 31 个文件的 sha256 快照 | **changed = 0**（跑完后逐条复核，harness 未留变异态）|
| 合并 diff | 9 文件 **+196 / −110**（app 6 + harness 2 + 本次文档不计）|

### 45.6.1 一处**行尾**取证（本节的过程留痕：差点误判一次）
`guardrail_service.py` 在本批后是 **PURE-LF**，而另外 5 个被改的 app 文件都是 **PURE-CRLF**。
第一反应是"Edit 把 CRLF 转成了 LF"（那正是本仓最危险的一种损坏）。**取证后否定**：
1. `app/` 全量普查：**CRLF 262 / LF 58 / MIXED 11** —— LF 本就是该目录的普遍既有状态
   （`daily_digest_service.py` / `analysis_report_service.py` / `category_inference.py` 等一长串都在 LF 列）；
2. **决定性探针**：在仓库外造一个 CRLF 文件 → 用 Edit 改一个词 → 字节复核 **仍是 3 个 CRLF、0 个裸 LF**
   （Edit 保留行尾）；
3. 同一批另外 5 个 CRLF 文件改后仍**纯 CRLF**。
→ 结论：`guardrail_service.py` **本来就是 LF**，不是被改坏的。**判据：行尾"异常"要先与该目录的分布比基线，
再用探针排除工具嫌疑，不要直接归因。**

## 45.7 本节未做 / 交接

| 事项 | 状态 |
|---|---|
| 把"多文件变异要求每文件各一红"变成被保证的 | ⏳ 未做（§45.5 的缺口；需要动 `_run_guards` 的返回值形态）|
| §42.6 方案 C（合并全部 13 个）| ⏳ 不做（§42.5 语义结论不变：8 种语义压成 1 是错的）|
| 统一 13+ 处 direction 归一化（`in ("YES","NO","WAIT","AVOID")` 四元组）| ⏳ 未动 |
| 前端 `api.ts:447` 手写 union；规范化 51 个 MIXED 文件 | ⏳ 未动 |
| 提交 | ⏳ **未提交**（用户未指示提交）|

**本节改 6 个 app 文件 + 1 个新模块 + 2 个 harness 文件 + 本文档。未 push、未开 PR。**

> **追加（2026-09-30，§46）**：本表第 1 行（"把『多文件变异要求每文件各一红』变成被保证的"）
> **本批做完** —— §45.5 点名的缺口不再是"实测"，而是被 `GuardRun.every_file_red` 规则保证。见 §46。

> 追加（2026-09-30）：本节已提交 —— 6 个 app 文件 + 1 个新模块 + 2 个 harness 文件与本节记录同在 **`d619f84`**
> （17 文件 +782/−200），本文件在 **`6341d93`**（1 文件 +842/−4）。页脚与 §45.7 表里那句"未提交"是提交前的
> 实测状态，**保留不改**（同 §34–§40 的做法）。

---

> **追加（2026-10-01）**：上面那句"未 push、未开 PR"以及同节表里的"未提交"**已作废** —— 本批已随
> **PR #111** 推送并**已于 2026-10-01 合入 main**（merge commit **`cbbcf5e`**）。完整证据见 §47.10 末尾的追加。

# 四十六、把 §45.5 的缺口补上：阶段②判据从「≥1 条红」升级为「**每文件各一红**」

本批**只做 §45.5 自己点名的那一件事**：让"每个 `guard_file` 各至少一条红"从**实测**变成**被规则保证**。
§45.5 已经写明这"需要动 `_run_guards` 的返回值形态"，不是补一条断言就能做到 —— 本批就是那次改动。

顺带修掉两处**我自己前几批留下的**陈述漂移（§46.7），它们都在 harness 自己的文档里。

## 46.1 结论先行

| 问题 | 答案 |
|---|---|
| 本批做了什么 | `_run_guards`：从「一次调用传**全部** `guard_file`、返回**单个 bool**」改成「**每个 `guard_file` 一次**调用、返回 `GuardRun`（逐文件结果）」；`_verify_one` 阶段②判据随之从 `not passed`（≥1 红）升级为 `after.every_file_red`（**每文件各一红**）|
| 缺口原文 | §45.5：`returncode == 0` 只强制"选中的守卫里**≥1 条**失败"，**不强制**"每个文件各至少一条失败" → 某个文件的守卫退化成空头时，W31 仍会绿（另 8 条照红）|
| 现在被保证了吗 | **是**。每个文件**单独**跑一次 pytest，`every_file_red` 要求 `green_files` 为空 → 任何文件没掉红都直接判 BAD，并在**收据里点名**是哪个文件留在了绿 |
| W31（6 文件 / 9 守卫）实测 | `red after mutation **6/6 files red**`；收据逐文件列出 2+2+1+1+2+1 = **9** 条失败，与 §45 的 `9 failed` 对得上 |
| H1 的针为什么变了 | `_run_guards` 从"一次调用传全部文件"改成"逐文件循环"，H1 原来钉的 argv 展开 `*mutation.guard_files(),` 在源码里**已不存在** → 针重定向到那次循环。**能力没变，锚点随 seam 移动**（§46.4）|
| 新增变异 | **H2**：把 `every_file_red` 退化成"≥1 红"，由新用例 `test_every_file_red_requires_every_file_not_just_one` 抓住 |
| 为什么不用"单次调用 + 解析 pytest 输出" | 见 §46.3 —— 那会保住 H1 的旧针，但把判据从 `proc.returncode` 换成**文本解析**（还要额外处理收集期的 `ERROR`）|
| 计数变化 | `multi-guard-file` **1 → 2** 条变异；共 **7 套 / 61 个变异**（原 60）|
| 验证 | 见 §46.6 |
| 提交 | ⏳ **未提交**（用户未指示提交）|

## 46.2 改动：一个记录类型 + 一个每文件循环 + 一处判据

`backend/scripts/mutation_verify.py`（PURE-LF）：

| 改动 | 位置 | 说明 |
|---|---|---|
| 新增 `FileGuardRun` | 引擎段 | `(guard_file, passed, tail)` 的 frozen dataclass —— **一个文件一次跑**的结果 |
| 新增 `GuardRun` | 引擎段 | `per_file: tuple[FileGuardRun, ...]` + 三个聚合：`all_green` / `red_files` / `green_files` / `every_file_red`，以及收据用的 `tally()` / `red_summary()` / `green_summary()` |
| 重写 `_run_guards` | 原 `:1052` | 改为遍历 `mutation.guard_files()`，把**每次调用**交给新助手 `_run_one_guard_file`。返回 `GuardRun` 而非 `(bool, str)` |
| 新增 `_run_one_guard_file` | 引擎段 | argv 只含**一个** `guard_file`；`-k` 选择器照旧 `" or ".join(mutation.guards)`；`passed = proc.returncode == 0`（判据没换）|
| `_verify_one` 阶段① | 原 `:1246` | 早退消息带上 `before.tally()` + `before.red_summary()` —— 多文件时能看出**是哪几个**文件本来就红 |
| `_verify_one` 阶段② | 原 `:1262` | `red_after = not passed_after` → `after.every_file_red`；失败消息从"`stayed GREEN with the fix reverted`"改成"`stayed GREEN in N/M file(s) -> <逐文件尾行>`" |
| `_verify_one` 收据 | 原 `:1276` | `red after mutation {bool}` → `red after mutation {after.tally()}`，例：`6/6 files red` |
| 模块 docstring 第 2 阶段 | 文件头 | "they FAIL with the mutation applied" → 补一句"**in EVERY named guard file**"，并把理由写进去 |

**`every_file_red` 为什么带 `bool(self.per_file) and`**：`per_file` 为空时"没有任何文件通过"是**空真**，
于是一条**没命名任何文件**的变异会"跑了个寂寞却四阶段全过"。所以聚合本身写成
`bool(self.per_file) and not self.green_files`，并在清单测试里把这种情况判为违规（§46.5）：
两层都要有 —— 聚合层保证语义正确，清单层保证真实变异走不到那条路。

## 46.3 判据为什么不能靠"一次调用的返回码"

两个问题**只在多文件变异上才分得开**：

| 阶段 | 问题 | `returncode == 0` 是什么 |
|---|---|---|
| ① / ③ | **每个**文件都绿？ | **正好是**（任一文件失败 ⇒ 非 0）。所以绿色阶段**本来就不需要**逐文件 |
| ② | **每个**文件都红？ | 只是"**至少一个**红"—— 错的那一半 |

也就是说 `all_green` 与 `every_file_red` 在 `len(guard_files) == 1` 时互为否定，
在 `>1` 时**不是** —— 那个差值就是 §45.5 说的盲区。

**另一条路（没走）**：保持**单次**调用，从 pytest 的 `-q` 短摘要里解析 `FAILED <path>::<test>` 行，
按出现的文件集合判"每文件各一红"。它的好处是 H1 的旧针**一字不用动**；没走的理由：
① 判据从 `proc.returncode` 变成**对 pytest 输出文本的依赖**（本仓现有判据一律是返回码）；
② 收集期失败报的是 `ERROR …` 而不是 `FAILED …`，解析要多处理一个类别；
③ 逐文件调用额外把**收集**也隔离了 —— 一个文件 import 失败不会污染别的文件。

代价只有 W31 付：3 个阶段 × 6 个文件 = 18 次调用（原 3 次），**+15 次**；单文件变异一字不变。

## 46.4 H1 的针重定向：一条**元**变异随 seam 移动（含一个反直觉处）

H1 要锁的性质没变（"**每个**命名的文件都真的跑到了"），但 seam 变了：
从"一个 argv 里展开 `*guard_files()`"变成"对 `guard_files()` 的一次 **for 循环**"。因此：

- 常量由 `_GUARD_FILES_ARG` / `_GUARD_FILES_ARG_FIRST` 换成 `_GUARD_FILES_LOOP` / `_GUARD_FILES_LOOP_FIRST`；
- **两个**针的组合仍要**拆片段**写（H2 的也是）—— H1/H2 的靶就是本文件，把针写成一个字面量会**再命中一次**，
  `_apply_preflight` 会以 `expected 1, found 2` 拒绝（§44.3 已经踩过一次的同一个坑）；
- **守卫测试的用例名保持不变**（`test_run_guards_passes_every_named_file_to_pytest`），所以 H1 的 `guards`
  元组不用动；改的是**用例体**：从"断言一次 argv 含两个文件名"改成"断言**每个文件各一次调用**"
  （`runner.call_args_list`）、并按顺序核对 `argv[argv.index("pytest") + 1]` 与 `-k` 选择器。

🔴 **反直觉处（值得单列）**：H1 的 `guard_files()` 只有**一个**文件，所以在 H1 自己身上
"只跑第一个"是**空操作** —— 它的守卫会不会跑，与这条变异无关。H1 之所以**仍然可验证**，
是因为那条守卫用例会用一个**合成的两文件变异**去驱动 `_run_guards`（`tests/a.py` + `tests/b.py`），
于是"只循环一次"在**用例内部**可观测。→ **自指变异的可观测性来自守卫用例构造的输入，不来自变异自身的文件数。**

四阶段实测：`green before True | red after mutation 1/1 files red | green after restore True | bytes restored True`，
突变后 `1 failed, 47 deselected in 0.76s`。

## 46.5 新增的用例（+6 passed / +1 subtest）

`backend/tests/test_mutation_verify.py`（PURE-LF）：

| 用例 | 角色 |
|---|---|
| `test_run_guards_passes_every_named_file_to_pytest`（**改体**，名字不动） | H1 的守卫：每个命名文件各一次调用，`-k` 选择器逐字不变 |
| `test_run_guards_keeps_one_record_per_file` | 逐文件记录：顺序 = `guard_files()` 顺序、`tally()` 读数、尾行取自**该文件自己**的运行（不是合并运行） |
| `test_a_single_file_run_records_its_pass_flag_and_tail` | 单文件路径的形态：`per_file` 长度为 1、`passed` / 尾行 / `tally()` |
| `GuardRunAggregateTests::test_every_file_red_requires_every_file_not_just_one` | **H2 的守卫**：一红一绿时 `every_file_red` 必须为 False |
| `GuardRunAggregateTests::test_all_green_needs_every_file_to_pass` | 绿色聚合的对偶方向 |
| `GuardRunAggregateTests::test_an_empty_run_is_green_but_does_not_prove_every_file_red` | 钉住 `bool(self.per_file) and` 那半句（空运行**不得**读作"每文件都红"）|
| `GuardRunAggregateTests::test_the_tally_counts_red_over_total` | 收据读数 `2/3 files red` |
| `test_every_guard_file_exists_and_every_guard_is_named`（**加一条判据**） | 每条变异必须**至少命名一个** `guard_file`（否则空运行可"全过"）|

subTest 的 +1 来自 `VerifySelectionTests` 里按"每个变异一个 subTest"展开的用例 —— 变异总数 60 → 61，
与新增用例无关（两者要对得上账，所以分开记）。

## 46.6 验证

```bash
cd backend
# 元能力的两半（H1 / H2）
../.venv/Scripts/python.exe scripts/mutation_verify.py verify multi-guard-file
# W31 单条：6 个 guard_file 是否"各一红"
../.venv/Scripts/python.exe scripts/mutation_verify.py verify whitelist-fixtures --index 25
# 全量（7 套 / 61 个变异）
../.venv/Scripts/python.exe scripts/mutation_verify.py verify
# harness 的静态清单测试
../.venv/Scripts/python.exe -m pytest tests/test_mutation_verify.py -q -p no:cacheprovider --junitxml=<outside-repo>
# 6 个消费文件（W31 的 guard_file）
../.venv/Scripts/python.exe -m pytest tests/test_guardrail_service.py tests/test_market_quality_service.py \
  tests/test_execution_quality_service.py tests/test_source_reliability_service.py \
  tests/test_replay_metrics.py tests/test_decision_quality_service.py -q -p no:cacheprovider --junitxml=<outside-repo>
```

| 项 | 读数 |
|---|---|
| `verify multi-guard-file`（H1 + H2）| **2/2 全部通过**，EXIT 0；两条突变后都是 `1 failed, 47 deselected` |
| `verify whitelist-fixtures --index 25`（W31）| `[OK]`；`red after mutation **6/6 files red**`；收据 2+2+1+1+2+1 = **9** 条失败 |
| 全量 `verify`（7 套 / 61 个变异，**27m47s**）| **60 条当场 `[OK]`**；`FAILURES (1)`：`whitelist-fixtures 22: guard already RED before mutation …` —— **环境噪音，非本批回归**（§46.6.1）|
| 同一条的单条重跑（`--index 22` = W27）| `[OK]`；`green before True / 1/1 files red / bytes restored True`，`1 failed, 127 deselected in 22.63s`，EXIT 0 |
| harness 测试 + 6 个消费文件（共 7 个）| **250 passed + 87 subtests / 0 failed / 0 errors**；对账：harness 由 §45 的 **42 → 48**（本批 +6 用例），6 个消费文件仍是 **202** 条 → `42 + 202 = 244`（§45 读数）→ `48 + 202 = 250`，**差额只有 harness 的 +6** |
| `ruff check app/`；`ruff check scripts/mutation_verify.py tests/test_mutation_verify.py` | 两处均 **All checks passed!**（本批**未改** `app/`）|
| 变异路径 sha256 快照（**31 条**，跑前落、跑完逐条复核）| **changed = 0**（harness 未留变异态）|
| 两个 harness 文件跑完后的行尾 | 仍 **PURE-LF**；`mutation_verify.py` sha `e119d862c0b79848`、`test_mutation_verify.py` sha `87aebb36a44088b3` —— 与**跑前逐字节相同** |

### 46.6.1 全量那道唯一的失败：**环境噪音**，判据是 tail 里的拦截器签名

失败的是 `whitelist-fixtures 22`（W27 = `event_intelligence._TRADABLE_DIRECTIONS` 删 `NO`），报的是
**阶段①**"变异前守卫就是红的"，而 tail 是：

```
tests/test_event_intelligence_service.py -> .  [100%]
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":548,"threshold":50,"scope":"turn",
  "targets":["\\?\C:\Users\Alin\AppData\Local\Temp\pytest-of-Alin\garbage-…"],"targetCount":1}
```

**指纹 = tail 里的拦截器签名**。阶段①的判据是 `proc.returncode == 0`，而**进程退出期的拦截器能把一次跑绿的守卫
变成非 0**（它拒删 pytest 自己的临时目录）。三条互相独立的观测说明它不是本批的回归：

1. **该条单独重跑 `[OK]`** —— 突变后选中的守卫确实变红，还原后逐字节一致；
2. 它在**未变异**的树上也绿：这正是重跑时打印的 `green before True`；
3. 跑完后 **31 条变异路径 sha256 changed = 0**，工作树没有被留在变异态。

🔴 **不重跑整套**：`scope:"turn"` 是**按本轮累计**的同类拦截器 —— 本轮跑得越多，越可能**更早**撞上它，
于是重跑可能拿到的是一份**更差**的观测，却用它覆盖一份已经完整的证据。正确顺序是**先把单条重跑出来**
（它同时给出更强的证据），再决定要不要重跑。
→ 这条判据与"把阶段①的非 0 当成真失败"是同一类坑，已进技能反面清单。

> **`--index 25` 而不是 31**：W31 是**新号**，但它是 `whitelist-fixtures` 元组里的**第 25 个**位置 ——
> 因为 W17/W18/W19/W20/W24/W29 六条已随合并删除（§45.4）。"编号"（历史含义）与"位置"（元组下标）
> 在这一批之后**不再相等**，跑单条时按 `list` 输出的位置给。

## 46.7 顺带修掉的两处**自陈漂移**（都是我前几批留下的）

| 处 | 原话 | 为什么是错的 | 改成 |
|---|---|---|---|
| `multi-guard-file` 的 rationale | "⚠️ 此刻该能力**尚无生产消费点**（合并组① 是下一批），故 InventoryTests 里有一条测试把它钉在…" | **§45 已经落地 W31**（6 文件 / 9 守卫），且那条 pin 测试已被 §45 删除 | 改成"生产消费点已由 §45 的 W31 落地；§44 当时写的『尚无生产消费点、且由一条测试钉住』已随之作废"，并补 H2 的半句 |
| 模块 docstring | "Merging keeps the union of both: one inventory, one engine, **five sets**." | 集合数已 5 → 6 → 7（§43、§45 各加一个）；**硬编码的计数已经过期两次** | 去掉计数："…and every set either of the three had" + 一句"计数故意不写死" |

**教训（进 MEMORY/技能）**：**文档里的计数和"当前还没有 X"是最易过期的两种陈述** ——
前者的正确写法是**不写数**或写"以 `list` 输出为准"，后者必须与**同一个提交**里的落地动作一起改。

## 46.8 本节未做 / 交接

| 事项 | 状态 |
|---|---|
| §42.6 方案 C（合并全部 13 个常量）| ⏳ 不做（§42.5 的语义结论不变）|
| 统一 13+ 处 direction 归一化（`in ("YES","NO","WAIT","AVOID")` 四元组）| ⏳ 未动 |
| 前端 `api.ts:447` 手写 union；规范化 51 个 MIXED 文件 | ⏳ 未动 |
| 03 处 `## 4x.y` 之外的结构：本文档 §43–§45 的 H1 标题 | ✅ 本批补齐（纯加标题，无内容改动）|
| 提交本会话累计批次（§43–§46 + 6 个 app 文件 + 新模块 + 2 个 harness 文件）| ⏳ **未提交**（用户未指示）|
| push / 开 PR | ⏳ 未做（本地 **17** 个提交未推送）|

**本节改 2 个 harness 文件（脚本 + 其守护测试）+ 本文档（+ 3 行 H1 标题修补）。生产代码 `app/` 一字未动。
未 push、未开 PR。**

> 追加（2026-09-30）：本节已提交 —— 2 个 harness 文件与本节记录同在 **`d619f84`**（17 文件 +782/−200），
> 本文件在 **`6341d93`**（1 文件 +842/−4）。页脚与 §46.1 / §46.10 两张表里那句"未提交"是提交前的实测状态，
> **保留不改**（同 §34–§40 的做法）。

> **追加（2026-10-01）**：上面那句"未 push、未开 PR"以及同节表里的"未提交"**已作废** —— 本批已随
> **PR #111** 推送并**已于 2026-10-01 合入 main**（merge commit **`cbbcf5e`**）。完整证据见 §47.10 末尾的追加。

# 四十七、执行 §42.6「方案 C 的**位置版**」：7 个方向常量的定义迁进共享模块（13 个定义点 → 8 个定义 / 1 个模块）

## 47.1 结论先行

§42.6 把 C 记成「连 ②–⑧ 一起并」，§42.5 判它「把 8 个决定压成 1 个」。本批用户拍的是**另一种形态**：

> **一个模块、8 个具名常量** —— 名字 / 成员集 / 容器 / 定义处注释**逐字保留**，
> 只把 7 处定义**搬**进 `app/utils/direction_vocabulary.py`（§45 已把 6 个 `_STRONG_DIRECTIONS` 副本并成那里的 `STRONG_DIRECTIONS`）。

结果：**8 个方向常量 / 8 个定义点 / 1 个模块**。语义**零变化**、22 条方向守卫**一条不丢**、
7 条变异的针**一个字节未改**，只多了 1 条容器守卫（W32）把新增的「顺序」性质钉住。

## 47.2 迁移清单（7 处：原定义处 → 模块内行号）

| 常量 | 容器 | 原定义处 | 模块内 | 消费语义 |
|---|---|---|---|---|
| `_DIRECTIONAL` | tuple | `services/prediction_calibration_service.py` | 68 | 有可检验立场 → `direction_correct` |
| `_CALLED_DIRECTIONS` | **frozenset** | `services/review_queue_detectors.py` | 72 | 已下注的调用 → review 触发器 |
| `_VALID_DIRECTIONS` | **set** | `services/domain_reliability_service.py` | 78 | `attribute_evidence` 的计分白名单 |
| `_STRONG_DISPLAY_DIRECTIONS` | tuple | `api/routes/quality_metrics.py` | 84 | `final_displayed_direction` 的异常面 |
| `_REPORTED_DIRECTIONS` | tuple | `memory/simulated_trade_store.py` | 94 | `trade_stats()["by_direction"]`，**唯一被迭代** |
| `_TRADABLE_DIRECTIONS` | tuple | `services/event_intelligence_service.py` | 103 | 纸面开仓的方向白名单 |
| `_STRONG_EVENT_DIRECTIONS` | tuple | `services/conclusion_challenge_service.py` | 110 | 证据门 + 计算门，**两处调用点** |

（`STRONG_DIRECTIONS` 是 §45 的既有成员，在 64 行。8 个常量的值**全是** `{"YES", "NO"}` —— 但这是巧合，不是理由。）

四种容器（tuple / set / frozenset）**故意不同**，搬动时逐一保留：容器类型本身是**契约**的一部分。
新模块的 docstring 把「共享的是**位置**不是**含义**」写在最前面，并点名 `_REPORTED_DIRECTIONS` 的
顺序约束与 `evidence_aggregation_service` 的同名陷阱。

## 47.3 一个**同名不同值**的陷阱：`evidence_aggregation_service._VALID_DIRECTIONS` **不并**

`app/services/evidence_aggregation_service.py:45` 有**自己的** `_VALID_DIRECTIONS = {"support", "oppose"}`
—— 另一个字段、另一套词汇。**名字相同、语义无关**：按名字合并会**静默改变**那个模块接受什么。

→ 留在原地，并在新模块 docstring 里点名「不要与它混」。`test_no_other_module_under_app_defines_any_of_these_names`
把这一条写成**具名豁免**（`FOREIGN_DEFINITIONS`），而不是一条隐含的过滤规则 —— 放宽豁免会在 diff 里现形。

## 47.4 机械副作用：6 个 `path` 常量变成死代码 → 删除

harness 里 7 条变异原先各自 `path=` 指一个源文件；改指共享模块后，这 6 个路径常量**失去全部引用**：

`_PREDICTION_CALIBRATION`、`_REVIEW_QUEUE_DETECTORS`、`_DOMAIN_RELIABILITY`、`_QUALITY_METRICS`、
`_EVENT_INTELLIGENCE`、`_CONCLUSION_CHALLENGE`。

（`_TRADES_STORE` **保留** —— 另 3 条变异仍以它为 `path`；`_DECISION_QUALITY` 保留 —— W30 仍指向它。）

## 47.5 harness：针不变、`path` 改指、`label`/`note` 跟新，并新增容器守卫 **W32**

**7 条变异只改了 `path`（+ 标签与注释）；`old` / `new` / `guards` 一个字节未动。**
这正是「定义行**逐字**搬过去」的可验证推论：如果针也要跟着改，说明搬动掺了改动。

自洽取证（程序化，不肉眼数）—— **每条针在模块里恰好命中 1 次**：

```bash
cd backend && ../.venv/Scripts/python.exe -c "
import sys, pathlib; sys.path.insert(0,'.')
import scripts.mutation_verify as mv
v = pathlib.Path('app/utils/direction_vocabulary.py').read_bytes()
s = mv.SETS_BY_KEY['whitelist-fixtures']
p = [m for m in s.mutations if m.label.split()[0]
     in {'W21','W22','W23','W25','W26','W27','W28','W31','W32'}]
print(len(p), sorted({m.path for m in p}), sum(len(m.guards) for m in p))
print([(m.label.split()[0], v.count(m.old)) for m in p])
"
# -> 9 ['backend/app/utils/direction_vocabulary.py'] 23
# -> [('W21',1),('W22',1),('W23',1),('W25',1),('W26',1),('W27',1),('W28',1),('W31',1),('W32',1)]
```

**新增 W32（容器守卫）**：把 `_REPORTED_DIRECTIONS` 由 `tuple` 改成 `set`。理由 —— 它是 8 个常量里
**唯一被迭代消费**的（`simulated_trade_store.trade_stats` 里的 `for d in _REPORTED_DIRECTIONS:`），
而本仓**不固定 `PYTHONHASHSEED`** → 改成 set 会让 `by_direction` 的 **JSON 键序在运行间漂移**。
守卫放在**新模块自己的测试**里，所以这条变异**同时证明**那个新测试文件确实被跑到
（否则它会以「守卫名匹配不到任何用例」的形式在阶段①就红，而不是在阶段②）。

⚠️ **W25 与 W32 改写的是同一行**（`_REPORTED_DIRECTIONS` 的定义）→ 必须同属一个 `group`：
整组 `apply` 只留前一条并**说明跳过了哪条**；显式同时点名两条会被 `_apply_preflight` **拒绝**
（同一段字节写两遍只可能是笔误）。这与既有的 C5/C6、P7/P8 是同一种关系。

## 47.6 新增 `backend/tests/test_direction_vocabulary.py`（7 条，PURE-CRLF）

| 用例 | 钉什么 | 为什么不是空头 |
|---|---|---|
| `test_every_expected_name_is_declared` | 8 个名字都在 | 少一个即红 |
| `test_the_module_declares_nothing_else_that_looks_like_a_direction` | 模块面**恰好**这 8 个 | `hasattr` 只挡「少了」；这条挡「多了」 |
| `test_each_name_holds_the_members_it_had_before_the_move` | 期望值**写在测试里**，不回读模块 | 回读只会**复述模块**；改成员即红 |
| `test_the_container_types_are_pinned` | 3 种容器逐一对齐 | tuple / set / frozenset 互换即红 |
| `test_reported_directions_stays_an_ordered_sequence` | **W32 的守卫** | 先断类型再断值：读的人要知道**为什么**，不只是「两个对象不相等」 |
| `test_no_other_module_under_app_defines_any_of_these_names` | 枚举 `app/**/*.py` 的每个定义点并与期望集比对 | **带正向控制** —— 必须先找到模块，否则「没有游离副本」在空扫描上**恒真** |
| `test_the_module_imports_nothing_from_the_application` | 模块是**叶子** | 这正是 `app/memory/`、`app/api/` 能反向依赖 `app/utils/` 的前提 |

## 47.7 验证

```bash
cd backend
# 定位验证：7 条重定向 + W31 + W32（位置 17–23 / 25 / 26）
../.venv/Scripts/python.exe scripts/mutation_verify.py verify whitelist-fixtures --index 17-23,25,26
# 环境噪音那一条的单条重跑
../.venv/Scripts/python.exe scripts/mutation_verify.py verify whitelist-fixtures --index 22
# 新测试 + 7 个消费文件的测试 + harness 测试
../.venv/Scripts/python.exe -m pytest tests/test_direction_vocabulary.py tests/test_prediction_calibration_service.py \
  tests/test_review_queue_detectors.py tests/test_domain_reliability_service.py tests/test_quality_metrics.py \
  tests/test_event_intelligence_service.py tests/test_conclusion_challenge_service.py \
  tests/test_simulated_trade_store.py tests/test_mutation_verify.py \
  -q -p no:cacheprovider --junitxml=<outside-repo>
../.venv/Scripts/ruff.exe check app/
../.venv/Scripts/python.exe scripts/eol_audit.py
```

| 项 | 读数 |
|---|---|
| `verify whitelist-fixtures --index 17-23,25,26`（9 条，**5m26s**）| **8 条 `[OK]`**；`FAILURES (1)` = 位置 22（W27）「变异前就红」，tail 带 `[safe-delete]…` → **环境噪音**（§46.6.1 的同一指纹）|
| 位置 22 单条重跑 | `[OK]`；`green before True / 1/1 files red / bytes restored True`，`1 failed, 127 deselected in 1.36s` |
| W31（位置 25）| `[OK]`；**`6/6 files red`** —— §46 的「每文件各一红」在**共享定义**上继续成立 |
| W32（位置 26）| `[OK]`；`1/1 files red`，失败落在 `tests/test_direction_vocabulary.py` → 新测试文件**确实被跑到** |
| 9 个测试文件 | junit：**`tests=506 / failures=0 / errors=0 / skipped=0`**（= 369 passed + 137 subtests）|
| `ruff check app/`（CI 门）；对 10 个改动文件单独 `ruff check` | 两处均 **All checks passed!** |
| `scripts/eol_audit.py` | **line-ending damage: none**；`direction_vocabulary.py`（110 CRLF）与 `test_direction_vocabulary.py`（133 CRLF）均 **PURE-CRLF**，两个 harness 文件仍 **PURE-LF** |
| 变异路径 sha256 快照（**15 条**，跑前落、跑完逐条复核）| **changed = 0**（未留变异态）|

## 47.8 §42.6 的记录更正：C 的第三条代价**已过期**（已在 §42.6 补追注）

§42.6 表里 C 的代价写着「#10/#11 的注释明写『存在是为了能被变异』，**合并会删掉这两个变异靶**」。
这条**已不成立**：§44 给了 harness「一条变异可挂多个 guard_file」的能力，§46 又把阶段②判据升级为
「**每文件各一红**」—— 于是「多处定义」的锚定**可以集中到一个模块而不丢**。§45 的 W31 与
本批的 7 条重定向各证明一次。

⚠️ 但这**不等于原 C 该做**：C 只剩 **「8 种语义压成 1」** 一条真代价（§42.2/§42.5 的语义结论），
**仍不建议**。§47 做的是**另一种形态**：一个模块里**保留 8 个具名常量** —— 只收敛**位置**。

**教训**：一条「因为 A 所以不能做」的结论，会在 A 被别的批次修掉之后**静默过期**。
复述这类结论时要连同它的**前提**一起带上（这里是「单值 `guard_file`」），
否则下一批会照着一条已经失效的前提继续决策。

## 47.9 一条观察项（**未动**）

本批共 **13 个 `app/**` 改动文件**，其中 **12 个在树内是 PURE-CRLF**，只有一个例外：
`backend/app/services/guardrail_service.py` 树内是 **PURE-LF**（`eol_audit` 报 `ok (LF both sides)`）。

它**不是** `eol_audit` 能判定的那两种损坏之一 —— 那两种要求 **HEAD 侧是 CRLF**，而本仓
`core.autocrlf=true` + `* text=auto` 下 **HEAD 一律存 LF**。也就是说：
**「树内 CRLF→LF」这一类漂移对 `git diff`（比对前归一化）与 `eol_audit`（按 HEAD 判据）都是隐形的。**
它对提交内容零影响（入库即归一化为 LF），下次全新检出会还原成 CRLF。

按「不顺手修」的约定**未动**，只记为观察项。

> **追注（2026-09-30，提交前）**：**这条观察项已当场处理** —— 提交前把 `guardrail_service.py` 的
> 树内行尾对齐成 **PURE-CRLF**（`\n` → `\r\n`，250 行），使 13 个 `app/**` 改动文件在树内**全部一致**。
> 三条判据证明该操作**对提交内容零影响**：① LF 归一化后与改前**逐字节相同**；② `git diff -- <file>` 的
> 输出**前后同为 1286 字节**（逐字节相同）⇒ git 完全看不见这次改动；③ 字节增量 `+250` == 行数 `250`
> （即只加了 `\r`，没有别的改动）。→ §47.10 里那行「未动（对提交不可见）」随之作废：**现已对齐**，
> 而且仍然对提交不可见。

## 47.10 本节未做 / 交接

| 事项 | 状态 |
|---|---|
| 统一 13+ 处 direction 归一化（`in ("YES","NO","WAIT","AVOID")` 四元组）| ⏳ 未动 |
| 前端 `api.ts:447` 手写 union；规范化 51 个 MIXED 文件 | ⏳ 未动 |
| `guardrail_service.py` 树内 LF（§47.9）| ⏳ 未动（对提交不可见）|
| §35.4 第 3 行「任何带代理机器都会恒亮」| ✅ **已实测**（2026-09-30：自建桩代理复现 ⇒ 机制**不依赖机器自带代理**；残留的"换一台 Windows"属平台行为、未证）—— 见 §35.4 的追加 |
| `gbm` 汇总桶 / `third_place` / `_KALSHI_SPORTS_SERIES_PREFIXES` 第 8 项 / `/trades`「已作废」tab | ⏳ 未动 |
| 提交本会话累计批次（§43–§47 + 13 个 `app/**` + 1 个新模块 + 1 个新测试文件 + 2 个 harness）| ⏳ **未提交**（用户未指示）|
| push / 开 PR | ⏳ 未做（本地 **17** 个提交未推送；分支无 upstream）|

> **⚠️ 本批**没有**重跑全量 `verify`（7 套 / 62 个）**：本批只动了**共享模块**（所有方向常量的唯一位置）、
> **harness** 与**新测试文件**。其余 6 套变异的靶文件与守卫**一个字节未动**，`whitelist-fixtures` 的
> 位置 `1–16 / 24` 同样未动 —— §46 的全量读数（**60 条 `[OK]` + 1 条 safe-delete 噪音**，27m47s）对它们仍然有效。
> 本批重跑的 9 条（位置 17–23 / 25 / 26）**恰好覆盖了本批改动的全部字节**（9 条变异、1 个 `path`）。
> → 不跑全量的理由是**靶集合等于改动集合**，不是「省时间」。

**本节改 13 个 `app/**` 文件（7 处常量搬出 + 6 处 import）+ 1 个新模块 + 1 个新测试文件 + 2 个 harness 文件 + 本文档。
未 push、未开 PR。**

> 追加（2026-09-30）：本节已提交 —— 13 个 `app/**` + 1 个新模块 + 1 个新测试文件 + 2 个 harness 文件同在
> **`d619f84`**（17 文件 +782/−200），本文件在 **`6341d93`**（1 文件 +842/−4）。页脚与 §47.10 表里那句"未提交"
> 是提交前的实测状态，**保留不改**（同 §34–§40 的做法）。→ 本批（**§42–§47**）至此**全部提交**；
> **未 push、未开 PR**（§47.10 表里"本地 **17** 个提交未推送"是提交前的读数 ⇒ 现为 **19**）。

> **追加（2026-10-01）—— 本批（§16–§47）已 push、已开 PR、并已合入 main；所有"未 push、未开 PR / 未提交"**
> **至此作废（原文保留为时点快照，同 §34–§40 的做法）**：
>
> - **推送**：分支 `fix/voided-trade-terminal-state` 已推到远端（本仓远端名是 **`monkeydjl`**，不是 `origin`）。
> - **PR #111**（**23 个提交**）以 **merge commit** 合并 ⇒ **`cbbcf5e`**（`2026-10-01T04:20:53Z`），
>   父提交 = `d3fb8c2`(main) + `c0755e3`(head)；**源分支未删**。
> - **CI**：`backend-tests` **pass**（`6503 passed, 18 skipped, 0 failed`）—— 整条轨迹是 **`10 failed → 7 failed → 0`**，
>   前两档的根因是两个**只在 CI 裸检出里出现**的问题：解释器路径被硬编码成 `.venv/Scripts/python.exe`、
>   变异针按 CRLF 拼写。合入那一刻唯一红的是 `frontend-tests`，**与 PR #111 无关**。
> - **那道红**：`npm audit` 的 advisory 库在 09-27 与 10-01 之间新增了条目 —— **同一份 lockfile 字节**在
>   `d3fb8c2`(09-27) 是 success、在 `cbbcf5e`(10-01) 是 failure，而 PR #111 **一个字节都没动过该文件**。
>   已由 **PR #114** 修掉并以 **squash** 合入（main = **`00aa264`**），main 上 run `36817426626` **五个 job 全绿**。
> - **提交数折算**：本表"本地 **17** 个提交未推送"与 §41.9 追注里折算的"⇒ 现为 **19**"都是**时点读数**；
>   推送时 **PR #111 实际含 23 个提交**（`gh pr view 111` 的 `commits` 长度）。**squash 合并的提交另计**：
>   `652fa41`（#114）不在 main 的历史里，main 上是新生成的 `00aa264`。
> - **本条只改文档，不重跑任何闸门**：依据同 §41.9 追注已取证的事实 —— `backend/tests/` 里只有两处
>   **注释/文档字符串**提到本文件路径（`test_event_source_utils.py:9`、`test_knockout_stage_whitelist_consistency.py:20`），
>   **没有任何用例解析它**，所以纯文档追加不改变任何断言。

# 四十八、执行 §47.10 表里的三个零风险项：E-② / B-② / C-②（2026-10-01）

沿用执行前对齐的口径编号：**E-②** = 删重复常量；**B-②** = 行尾规范化；**C-②** = 补 `gbm` 桶。

§47.10 表里标「⏳ 未动」的项中，有三项是**零契约风险**的机械项，本节做掉：
E-②（删重复常量）、B-②（规范化行尾）、C-②（补一个**已存在**的引擎桶）。
另两项（`third_place`、`/trades`「已作废」tab）与 `api.ts:447` 的契约补齐**仍留**。

**本节改 9 个文件**（5 个源 + 4 个测试）。**未提交、未 push。**

## 48.1 结论先行

| 项 | 范围 | 判据（全部实测） |
|---|---|---|
| **E-②** | 1 源 + 1 测试 | 常量 8 项 → **7 项**；新增「无重复成员」守卫 |
| **B-②** | 50 个 tracked 文件（**零内容变更**）| 复算 `MIXED = 0`；且**不拿 `git` 当判据** |
| **C-②** | 后端 2 源 + 前端 2 源 + 3 测试 | 4 个桶 == `ENGINE_NAMES`；SSE 逐桶断言 |

## 48.2 E-②：那个重复元素（§22.3.1 → §26.5 → 本节）

§22.3.1 首次发现 `_KALSHI_SPORTS_SERIES_PREFIXES` 有重复元素；§26.5 把证据追到提交历史
（「第 8 槽本意」在仓里**无迹可寻**）。本节直接删第 8 项，常量变 7 项：

```python
_KALSHI_SPORTS_SERIES_PREFIXES = (
    "KXNBAGAME", "KXMLBGAME", "KXNHLGAME",
    "KXSOCCEREPL", "KXSOCCERUCL", "KXSOCCERWCS",
    "KXNFL",
)
```

新守卫 `test_series_prefixes_contain_no_duplicate_member`（`tests/test_kalshi_sports_source.py:17`）三条断言：
`len(p) == len(set(p))`；`len(p) == 7`；`all(x.startswith("KX") and x == x.upper())`。

**为什么是「删」而不是「改成一个不同的前缀」**：§26.5 已证第 8 槽没有可恢复的本意，
换一个前缀等于**发明**一条契约；删掉才是唯一有据的选择。

## 48.3 B-②：50 个 MIXED 文件规范化为 PURE-CRLF

**口径**：`git ls-files`（tracked）、跳过含 NUL 的二进制、`\r\n > 0 且裸 LF > 0` 记 MIXED。
判据**只用字节计数**（`b.count(b"\r\n")` vs `b.count(b"\n") - b.count(b"\r\n")`）——
**不用 `grep`**：Git Bash 会把 `\r` 吃掉，`grep -c $'\r'` 给假结果。

| 项 | 前（执行时 dry-run）| 后（本节复算）|
|---|---|---|
| tracked | 1491 | 1491 |
| binary | 1 | 1 |
| PURE-CRLF（含 7 个无换行文件）| 1000 | **1050** |
| PURE-LF | 440 | 440 |
| **MIXED** | **50** | **0** |
| 字节增量 | — | **+3393** == 裸 LF 总数 **3393** |

- 「后」的原始复算：`PURE-CRLF 1043 + 无换行 7 = 1050`，与 `1000 + 50` **自洽**。
- 重跑入口脚本的 **dry-run**：`MIXED tracked files frozen: 0` / `nothing to do` ⇒ 判据**可复现**。
- 每个文件都满足 **LF 归一化后逐字节相同**（脚本内 `sha(lf_norm(before)) == sha(lf_norm(after))`）⇒ 纯行尾变更，无内容漂移。
- **零提交内容**：`git add` 后**暂存 blob 哈希 == HEAD blob 哈希**、`git diff --cached --numstat` 为空。

### 48.3.1 必须记下的现象：`git status` 的 stat-dirty

规范化后 `git status` 一度显示 **50 个 ` M`**，而 `git diff` / `git diff --cached` **全为空**。
这是 **stat-dirty**（mtime/size 变了、内容没变），**不是**判据失败。决定性实验：
`git add` 刷新索引后，暂存 blob 哈希 `e7f945fe…` 与 HEAD **相同**，刷新后工作树恢复**全空**。

⚠️ `git update-index --refresh` **清不掉它**（rc=1、`needs update` 仍在）—— stat-dirty 必须 `git add` 才更新 stat 缓存。

### 48.3.2 与 §29.9 的「51 个」差 1（**未取证**）

§29.9 记的是 **51**，本节入口读数是 **50**。这 1 个的差额**没有取证**
（可能口径不同，如是否含 untracked；也可能期间有文件增删）。**本节结论不依赖它**：
50 个在本次被规范化、复算 `MIXED = 0`，都是本次实测。

## 48.4 C-②：`gbm` 落空的那个桶（§24.6.3 的落地）

§24.6.3 发现 `ENGINE_NAMES`（`world_cup_quality_service.py:20`，**4 个成员**
`("elo_odds","hybrid","gbm","integrated")`）与**批量预测的控制台汇总**（只数 3 个桶）不一致。

**先把「哪一面确实缺」钉住**：`build_quality_loop_report`（同文件 `:855` 起）**是**按
`for engine in ENGINE_NAMES` 迭代的（`by_engine` 与 `trends.by_engine` 两处）⇒
**质量报告里 `gbm` 本来就有桶**；缺桶只出现在**批量预测的汇总**这一条路径上。

改动 5 处：

| 文件 | 行 | 内容 |
|---|---|---|
| `services/world_cup_prediction_pipeline.py` | `:1528` / `:1589-1590` | init `"gbm_count": 0` + `elif engine_used == "gbm"` |
| `api/routes/world_cup_predictions.py` | `:632` / `:672-673` / `:715` | init `gbm_count = 0` + `elif` + 输出 `"gbm_count"` |
| `components/sports/world-cup/engine-console.tsx` | `:46` | 汇总串加 `GBM {n}` |
| `lib/world-cup/engine-api.ts` | `:43` | `gbm_count?: number` |

新增守卫 4 条（另 2 条 `test_trigger_prediction_accepts_gbm_engine` / `test_gbm_prediction_serializes_engine_used_as_gbm` 是**既有**的）：

| 用例 | 位置 | 断言 |
|---|---|---|
| `test_batch_summary_exposes_one_bucket_per_engine_name` | `tests/test_world_cup_prediction_pipeline.py:670` | `{k for k in result if k.endswith("_count")}` == `{f"{n}_count" for n in ENGINE_NAMES}` —— **oracle 用另一个常量**，不写字面量 |
| `test_batch_predict_matches_counts_gbm_runs` | 同文件 `:691` | mock 返回 `engine_used="gbm"` ⇒ `succeeded == 1` 且 `gbm_count == 1` |
| `test_batch_switch_engine_stream_reports_a_gbm_bucket` | `tests/test_world_cup_predictions_routes.py:238` | 解析 SSE 文本里含 `"gbm_count"` 的 `data:` 行 ⇒ `gbm_count == 1`、其余三桶为 0 |
| `shows a bucket for every engine the batch summary reports` | `components/sports/world-cup/engine-console.test.tsx:65` | `engine-summary` 里同时出现 `ELO 1` / `混合 1` / `融合 1` / `GBM 1` |

### 48.4.1 补桶**不会**让控制台出现 `gbm`（产品决定，**未动**）

前端 `EngineName`（`engine-api.ts:30`）是 `"elo_odds" | "hybrid" | "integrated" | "high_confidence"` —— **无 `gbm`**；
`ENGINES` 下拉（`engine-console.tsx:16/23`）也没有。而**后端 API 确实接受 `engine=gbm`**
（`world_cup_predictions.py:536` / `:602` 的 Query 描述里列了它），所以补桶**必要且正确**；
但「控制台要不要能选 `gbm`」是**产品决定**，本节**不代决、未动**。

### 48.4.2 第三份不一致（本节新发现，**未守卫、未动**）

`api/routes/world_cup_analytics.py:299` 有一份**独立**的元组字面量
`engine_keys = ("elo_odds", "hybrid", "integrated", "gbm")`，**全仓没有任何测试**钉住它
（无 `engine_keys` 匹配）。它是引擎键集的**第三份**、且**顺序也不同**，本节**未动**（超出批准范围）。

## 48.5 🔴 本节抓到并修掉的一个真 bug（过程留痕）

对 `world_cup_predictions.py` 的 3 次编辑里，**第 1 次（`gbm_count = 0` 的 init）静默未生效**：
`git diff --numstat` 显示 `3 0`，而预期是 `4 0`。

抓出它的是**本节新写的 SSE 守卫测试**：`UnboundLocalError: cannot access local variable 'gbm_count'` ——
那条 `elif engine_used == "gbm"` 引用了一个**从未初始化**的局部变量。补上 init 后 `numstat` 变 `4 0`。

> **教训**：`grep` 只证明**锚点在**，不证明**改动自洽**。静态看「代码齐全」的批次，
> 一条未初始化的局部变量只能靠**跑测试**暴露。这也是「新增守卫测试」不只是形式主义的现场证明。

## 48.6 验证（全部本机，2026-10-01 复跑）

| 项 | 读数 |
|---|---|
| 后端受影响 3 个测试文件 | junit `tests=74 / failures=0 / errors=0 / skipped=0`（= 65 passed + 9 subtests）|
| 前端守卫文件 | `Test Files 1 passed (1)` / `Tests 7 passed (7)` |
| 前端全量 | `Test Files 125 passed (125)` / `Tests 735 passed (735)` |
| `ruff check app/`（CI 门）| **All checks passed!** |
| `npx tsc --noEmit` | `TSC_EXIT=0`（无输出）|
| `npm run lint`（eslint）| `ESLINT_EXIT=0`（无输出）|
| `scripts/eol_audit.py` | **line-ending damage: none**；9 个文件均 `ok (LF -> CRLF checkout)` |
| 9 个文件 sha256 vs 变异前快照 | **9/9 一致**，且 9/9 均 **PURE-CRLF** |
| `git diff --numstat` | `4/0`、`1/1`、`3/0`、`27/1`、`45/0`、`48/1`、`26/0`、`1/0`、`1/0` |

## 48.7 变异验证（5 条，四阶段）

靶集合小且**跨后端 + 前端**，不值得进常驻 `mutation_verify.py`，所以用仓库外一次性 harness；
每条都走满 **基线绿 → 变异红 → sha256 还原一致 → 还原绿**：

| 变异 | 靶 | 结果 |
|---|---|---|
| `E-dup` | `kalshi_sports_source`（把重复项加回去）| `1/0/0` → **`1/1/0 RED`** → sha 一致 → `1/0/0 GREEN` |
| `C-pipe-elif` | pipeline 的 `gbm` 分支 | 同上 |
| `C-pipe-init` | pipeline 的 `gbm_count` init | 同上 |
| `C-sse-elif` | route 的 `gbm` 分支 | 同上 |
| `C-sse-init` | route 的 `gbm_count` init | 同上 |

⇒ 这 5 条守卫**全部承重**（不是「改完就绿」的空头）。

## 48.8 §47.10 表的状态更新（追加式，旧文保留为时点快照）

| §47.10 原文 | 本节后 |
|---|---|
| 「前端 `api.ts:447` 手写 union；**规范化 51 个 MIXED 文件**」的**后半** | ✅ **已做**（50 个 → PURE-CRLF）|
| 「**`gbm` 汇总桶** / `third_place` / **`_KALSHI_SPORTS_SERIES_PREFIXES` 第 8 项** / `/trades`「已作废」tab」中的 **`gbm` 汇总桶** 与 **第 8 项** | ✅ **已做** |
| 同行的 `third_place` 与 `/trades`「已作废」tab | ⏳ 仍**未动** |
| 「**前端 `api.ts:447` 手写 union**；…」的**前半** | ⏳ 仍**未动** |

## 48.9 本节未做 / 交接

| 事项 | 状态 |
|---|---|
| 本节 9 个文件的提交 | ⏳ **未提交**（用户未指示）|
| `world_cup_analytics.py:299` 的第三份引擎键集补守卫 | ⏳ 未动（本节新发现）|
| 控制台支持选 `gbm`（前端 `EngineName` / `ENGINES`）| ⏳ 未动（**产品决定**）|
| `third_place` / `/trades`「已作废」tab / `api.ts:447` 契约 | ⏳ 未动 |
| §29.9「51」与本节入口「50」的差值取证 | ⏳ 未做 |
| push / 开 PR | ⏳ 未做 |

**本节改 9 个文件（5 源 + 4 测试）。未提交、未 push。**

# 四十九、修行尾审计工具**自身**的假阳性：二进制被当文本 + 一个静默漏审（2026-10-01）

本节接 §48 的尾巴。§48.6 的验证表里有一行是「`scripts/eol_audit.py` → **line-ending damage: none**」，
但那次跑的是**默认模式**（只审 `git` 报为 changed 的文件）。本节第一次把 **`--all`（全树）** 跑到底，
它 **exit 1**，唯一命中 `frontend/src/app/favicon.ico` —— 取证证明那是**假阳性**；
顺着同一条路径又挖出同一族的**静默漏审**（2 条非 ASCII 路径）。

**本节改 4 个文件**（2 个工具 + 2 个测试），提交 **`fa33239`**，**已 push**，CI 全绿（见 §49.7、§49.8）。

## 49.1 结论先行

| 项 | 范围 | 判据（全部实测） |
|---|---|---|
| **假阳性** | `--all` 全树 | 真实仓库 `--all` **exit 1 → 0**；`favicon.ico` 由 `DAMAGE: …` 变为 `skip (git stores it as binary)` |
| **判词不诚实** | `classify()` 第 1 条规则 | 补 `head_lf == 0`；「HEAD 本就混合」改判 `DAMAGE: mixed` |
| **静默漏审** | 非 ASCII 路径 | 2 条 tracked 路径由「解析不到、无声跳过」变为「被审计」 |
| **守卫** | 1 新测试文件 + 1 新变异集 | 12 条用例；常驻 set `eol-audit`(4) |
| **交付** | 4 文件 | `+374 / −27`；`fa33239`；**已 push**；CI 5/5 绿 |

## 49.2 假阳性的现场：判词与**它自己打印的画像**矛盾

`--all` 那一行输出（逐字）：

```
frontend/src/app/favicon.ico    HEAD(crlf=1,lf=29) TREE(crlf=1,lf=29)  DAMAGE: was pure CRLF at HEAD, now has bare LF
```

同一行里，画像两侧**完全相同**（`crlf=1, lf=29`）—— 那**不是** "pure CRLF"。
**判词与它自己打印的数据自相矛盾**，这正是抓住它的线索：不需要先懂工具实现，只要读它自己吐出的数字。

独立取证（证明该文件**零改动**）：

| 判据 | 读数 |
|---|---|
| `git show HEAD:<path>` vs 工作树 | **逐字节相同**（25931 B，`raw identical: True`）|
| 两侧 sha256 | 同为 `2b8ad2d33455a8f7…` |
| `git status --porcelain` / `git diff --stat` | **皆空** |
| `git check-attr text diff` | `text: unset` / `diff: unset`（git 按**二进制**处理）|
| `git hash-object <file>` vs `git rev-parse HEAD:<path>` | 同为 `718d6fea4835ec2d246af9800eddb7ffb276240c` |
| 文件头 | `00 00 01 00 04 00 10 10` = ICO 魔数，且含 NUL 字节 |

⇒ **非损伤**。判据要点同 §48.3：**不拿 `git diff` 当唯一 oracle** —— `core.autocrlf` 在比对前归一化，
`git diff` 对**行尾漂移是瞎的**；这里用的是三条**互相独立**的判据（字节相等、对象哈希相等、属性为二进制）。

## 49.3 两个缺陷一个症状：**只补合取项不够**

| # | 缺陷 | 性质 |
|---|---|---|
| 1 | 二进制文件被当**文本画像** | **根因** —— `favicon.ico` 的 1 个 CRLF + 29 个裸 LF 只是 ICO 填充；一旦被画像，**无论判据怎么写**都会落进 mixed |
| 2 | 第 1 条规则只判 `head_crlf > 0` | **判词不诚实** —— 文件可能**本来就混合**，却被说成"曾经纯 CRLF" |
| 3 | 非 ASCII 路径被**静默跳过** | 同族（见 §49.5）|

修复：`head_crlf > 0` → `head_crlf > 0 and head_lf == 0`（**补上第二项合取**）。这样"本来就混合"的文件落到第 2 条规则上，
报出来的是它**实际**的样子（`DAMAGE: mixed line endings`）。

⚠️ **但只改这一处，`favicon.ico` 仍然红** —— 它会被第 2 条（mixed）抓住。所以**二进制排除才是根治**，
合取项只是让剩余情形的判词诚实。两者都要。

## 49.4 二进制判据用 **git 自己的 `i/-text`**，不是 NUL 启发式

`git ls-files --eol -z` 一次调用同时给出索引行尾标记与 worktree 标记，`i/-text` 即"git 把它存为二进制"：

| 文件 | `ls-files --eol` 读数 |
|---|---|
| `frontend/src/app/favicon.ico` | `i/-text w/-text attr/-text`（`.gitattributes` 的 `*.ico binary`）|
| `backend/app/main.py` | `i/lf w/crlf attr/text=auto` |

**为什么不用"含 NUL 就算二进制"**：`.gitattributes` 把 **`backend/data/gbm_*.txt`** 标为 `binary` ——
那是 **CRLF 文本、一个 NUL 都没有**（LightGBM 解析器对 `\r\n` 敏感，所以才**刻意**标 binary）。
NUL 启发式会漏掉它，而它恰恰是最容易被误判成"损伤"的那类文件。
⇒ **判据取 git 的结论，而不是自己再猜一遍 git 的规则。**

## 49.5 同族缺陷：非 ASCII 路径被**静默**跳过

`git ls-files` 默认会把非 ASCII 路径**加引号转义**输出：

```
i/lf    w/lf    attr/                "docs/user/\344\270\255\346\226\207\344\275\277\347\224\250\346\225\231\347\250\213.md"
```

于是 `REPO_ROOT / rel` 解析不到文件 → `is_file()` 为 False → **无声跳过**。本仓受影响 **2 条**。

| 修法 | 说明 |
|---|---|
| `git ls-files --eol -z` | `-z` 下路径**不加引号**；一次调用兼顾"候选清单 + 二进制集合" |
| `git status --porcelain -z` | 同理；**并显式消费 rename/copy 的第二条记录** —— 否则会把源路径当作"前 3 个字符被切掉"的路径 |

用 `os.fsdecode()` 解码路径字节，`_git_bytes()` 不再用 `text=True`（避免按本机 locale 解码 UTF-8 路径）。

> 这与 §48.3 的口径同源：**判据要能用字节复算**，别让一层隐式转换在中间改写事实。

## 49.6 结构：抽纯函数 + 可驱动的 `main`；**0 必须只说一件事**

| 抽出 | 作用 |
|---|---|
| `classify(head, tree)` | 纯函数，两类损伤 + 正常 LF/CRLF checkout + 新增文件都能**不建仓库**直接钉 |
| `binary_paths()` / `tracked_eol()` | 二进制的来源与消费分开，可分别变异 |
| `main(argv=None)` | 沿用 `tests/test_review_queue_cli.py` 那套「sys.path + 捕获 stdout」的范式 |

**结尾那行**：`line-ending damage: none` **原样保留**（**本文件 §48.6 就引用了这句话**，
改掉会让旧记录与工具输出对不上），**新增下一行**报跳过数：

```
line-ending damage: none
  (1 file(s) skipped: git stores them as binary)
```

否则"没有任何损伤"与"把二进制全跳过了"读数**完全相同** —— 这正是本仓反复确认的
「**0 必须只说一件事**」（见 §47 系列与 `decision-timeline-panel.tsx` 的空态先例）。

## 49.7 验证（全部实测，2026-10-01）

| 项 | 读数 |
|---|---|
| 新守卫文件 `tests/test_eol_audit.py` | junit `tests=12 / failures=0 / errors=0 / skipped=0` |
| harness 清单测试 `tests/test_mutation_verify.py` | **62 passed + 94 subtests** |
| 真实仓库 `scripts/eol_audit.py --all` | **exit 0**；1491 个 `ok` + **1 个 skip**（就是 favicon）；耗时 7m29s |
| `ruff check`（两个新文件） | **All checks passed!** |
| 4 个改动文件 sha256（长跑前后） | **4/4 一致** |
| 提交后 blob 复核 | 4 个 HEAD blob **全为 LF**（autocrlf 入库归一）；工作树 = 1 LF + 3 CRLF；**LF 归一后内容一致** |

**默认模式**（只审 changed 文件）依旧全绿；`--all` 从"必然红"变为"0 skip 之外的 1491 个全 ok"。

### 49.7.1 🔴 全量套件里那**唯一一败**不是本节的（并已由 CI 反证）

本机全量：**6525 passed + 1 failed + 11 skipped**（比基线 6525 多 **12**，正是新增用例）。
唯一失败 `tests/test_backup_restore_drill.py::…restores_every_store_to_its_configured_path`，
告警是「PMRF service appears to be running (SQLite DB is locked)」。

| 判据 | 读数 |
|---|---|
| 本机 **:8000** | **有进程在监听**（8001 / 3001 也开着）|
| 健康探测 | 拿到 HTTP 响应（**404**）；而 `restore_stores.py:338-357` **有意**把任何 HTTP 响应判为"服务在跑" |
| `import fcntl` | **MISSING** → 本机走 **Windows 健康探测**分支（而非 POSIX 的 flock 分支）|
| 单独复跑该用例 | **同样失败**（2.79 s，**确定性**，非排序/波动）|
| 长跑后 4 个改动文件 | **4/4 逐字节未变** |

**CI 反证**：CI 跑在 Linux 上、`fcntl` **可用** → 走 flock 分支，本机那条"探测 :8000"的分支在 CI 里
**根本不存在** ⇒ 同一份代码、两个平台、**两条不同的判据**。CI 上该文件全绿，本机红，正是这个原因。
⇒ 该用例对"**开发者本机跑着自己的服务**"不免疫，属**既有**脆弱点，本节**未动**。

## 49.8 变异验证：常驻 set **`eol-audit`(4)**，四阶段

本节的值班守卫**进了常驻 harness**（不像 §48 那样留在仓库外一次性脚本），因为它是**常驻工具**：

| 变异 | 靶 | 结果 |
|---|---|---|
| `E1` | 去掉 `head_lf == 0` 合取项 | `green before → **1/1 files red** → green after restore → bytes restored` |
| `E2` | 关掉二进制跳过（`if False and rel in binary:`）| 同上 |
| `E3` | mixed 判据的 `and` 改成 `or` | 同上 |
| `E4` | `binary_paths()` 返回空集 | 同上 |

**为什么有 E3 / E4**：只锁"**不再假红**"的修复，会让"**干脆不报损伤**"通过其余全部断言 ——
那是比假红更坏的失效（工具被关掉而没人决定关它）。E1/E2 各拆修复的一半，E3/E4 保证修复**不等于**关掉检测。

harness 规模随之变为 **8 套 / 66 个变异 / 26 个目标文件 / 33 个守卫文件**
（`sorted({m.path for s in SETS for m in s.mutations})` 取证）。

## 49.9 §48.9 表的状态更新（追加式，旧文保留为时点快照）

| §48.9 原文 | 本节后 |
|---|---|
| 「本节 9 个文件的提交 ⏳ **未提交**」 | ✅ **已提交**（`f0b662c` 9 files / `a9af1cf` 文档）|
| 「push / 开 PR ⏳ 未做」 | ✅ **已 push**（`932df82 → b9f44ea`，CI 全绿）；按惯例**直推 `main`，无 PR** |
| 「`world_cup_analytics.py:299` 的第三份引擎键集补守卫」| ⏳ 仍**未动**（见 §49.10）|
| 「控制台支持选 `gbm`」/「`third_place`」/「`/trades`『已作废』tab」/「`api.ts:447` 契约」| ⏳ 仍**未动** |
| 「§29.9『51』与 §48.3 入口『50』的差值取证」| ⏳ 仍**未做** |

## 49.10 本节未做 / 交接

| 事项 | 状态 |
|---|---|
| `test_backup_restore_drill` 对"本机 :8000 有服务"不免疫（§49.7.1）| ⏳ **未动**（既有脆弱点）|
| `--all` 需 7~8 分钟（每文件一次 `git show`）| ⏳ 未优化；可用 `git cat-file --batch` 一次调用降下来，属**另一批**改动 |
| §48.9 遗留：`world_cup_analytics.py:299` 守卫 / 控制台 `gbm` / `third_place` / `/trades` tab / `api.ts:447` | ⏳ 全部**未动**（照旧）|
| 上线前 6 项里的 ②–⑥（生产必填配置 / 反代 + TLS / 代理头与限流 / 告警渠道 / 6 个 overlay 开关）| ⏳ **未做**（见 `go-live-readiness-2026-10-01.html`）|
| push / 开 PR | ✅ **已 push**（`fa33239`）；按惯例直推 `main`，**无 PR** |

**本节改 4 个文件（2 工具 + 2 测试），`+374 / −27`。已提交 `fa33239`、已 push、CI 全绿。**

---

# 五十、三处契约取证（`third_place` / `/trades`「已作废」/ `api.ts:447`）与上线前 ②–⑥ 清单（2026-10-01）

> 承接 §49.10 交接表的两行遗留。**本节只读生产代码取证** —— `third_place` / `/trades` / `api.ts:447`
> 三处**生产代码一字未改**；另附 §49.10 第 4 行「上线前 ②–⑥」的可执行清单。
> 三处里的两处（`third_place` 归属、`/trades` tab）属**产品/建模决定**，本节**只给事实与边界、不代决**；
> 第三处（`api.ts:447`）的**根因**本节查清。

## 50.1 `third_place`：唯一生产者产不出它，5 个判定点里 2 个算淘汰赛，且已被测试钉在非淘汰赛一侧

**生产者侧（`MatchFixture.stage` 的唯一来源）。** 把 openfootball 数据解析成 `stage` 的只有
`world_cup_match_service.parse_fixture`（§27.1）。其分支（:103-127）产出**恰好 6 个值**：
`group_stage` / `final` / `semifinal` / `quarterfinal` / `round_of_16` / `unknown`。
**没有 `third_place`** —— openfootball 的 `"round": "Match for third place"` 不含 group/final/semi/quarter/16，
落进 `else` → **`unknown`**。

**消费者侧（实测 `third_place` 的归属）。** 对 `stage` 做成员测试的一共 5 个「`_KNOCKOUT_STAGES` 族」判定点，
外加 §27.2 的「第二消费者」（一个**异名** set）：

| # | 判定点 | 成员数 | `third_place` 算淘汰赛 |
|---|---|---|---|
| #1 | `world_cup_prediction_pipeline._KNOCKOUT_STAGES:196` | 4 | ❌ |
| #2 | `kernel.engines.elo_odds_engine._KNOCKOUT_STAGES:47`（`football_multi_factor_engine:44` **直接 import 复用**它，即 §26.1 的「#6 随 #2」）| 6 | ❌ |
| #3 | `kernel.engines.situational_adjust._KNOCKOUT_STAGES:20` | 7 | ✅ |
| #4 | `kernel.engines.gbm_engine._KNOCKOUT_STAGES:33` | 8 | ❌ |
| #5 | `world_cup_verified_result_correction_service._is_knockout_stage:251` | 9 | ✅ |
| （另）| `conclusion_challenge_world_cup_adapter._HIGH_RISK_STAGES`（异名，§27.2 的「第二消费者」）| 6 | ❌ |

§26.3 的「**5 个判定点里 2 个算淘汰赛**」是同一事实的另一种分桶（把 #2 与复用它的「#6」算一处）——**逐数对得上**。

**`_STAGE_MAP` 的两个键是「死目标」。** `world_cup_prediction_pipeline._STAGE_MAP:184-185` 有
`"third_place"` / `"third place"` → `third_place` 两个键，但上方生产者产不出这两个输入 ⇒
归一化到 `third_place` 的路径**当前不可达**（§27.3 的降级结论不变）。

**且它已被一条断言钉住。** `test_knockout_stage_whitelist_consistency.py::test_stage_map_values_partition_into_knockout_and_group_stages`
断言 `_STAGE_MAP.values() − _KNOCKOUT_STAGES == {"group_stage", "third_place"}` ——
把 `third_place` **显式钉在非淘汰赛一侧**；翻案必须**改断言**（W9 变异即由此变红），**不会静默漂移**。

→ **结论（不代决）**：三四名决赛必有加时/点球（**不许平**），所以 #3/#5 的立场更贴近规则；但**当前无数据能到达这些分支**，
差别是**潜伏**的。`third_place` 该不该算淘汰赛是**建模决定**，不动。

## 50.2 `/trades`「已作废」tab：前端只有两个 tab、后端没有读 voided 的函数 —— 展示缺口（产品决策）

- **前端**：`frontend/src/app/trades/page.tsx` 的 tab 清单（:305-309）**只有两项** ——
  `{open, 当前持仓}` / `{closed, 已平仓}`；`tradeView` 的类型是 `"open" | "closed"`（:65）。
- **后端**：`void_trade` **存在**（`simulated_trade_store.py:268`，写 `status='voided'` + `exit_reason='voided'`），
  但全仓**没有** `count_voided_trades` / `list_voided_trades`（`grep` 命中 0）⇒ **读 voided 的路径不存在**。
- 后果：voided 行**既不在「当前持仓」也不在「已平仓」** → `void_trade` docstring 的承诺
  （"无需回连预测即可读懂为何离开开放列表"）**只在 DB 层成立**。
  对照：**prediction 层**的 void **可见**（`recent-predictions.tsx` 渲染 `p.status`）。
- 定性沿用 §17.2.1：「**产品决策，等待拍板**」。可选处置（未决）：增设第三 tab（需 `count/list` + 一个端点），或至少显示作废计数。

→ **不代决**：要不要做、做多少，是**产品取舍**。

## 50.3 `api.ts:447` 的根因：`/trades/*` 用裸 `FlexibleResponse`，OpenAPI 无字段契约 → `SimTrade` 不可能被生成

`frontend/src/lib/api.ts:447` 的 `direction: "YES" | "NO"` 是**手写 union**，根因不在前端偷懒，在**后端契约**：

- `/trades/stats`、`/trades/open`、`/trades/closed`、`/trades/{event_id}/close`
  **四个端点全部** `response_model=FlexibleResponse`（`events.py:1585/1592/1609/1626`）。
- `FlexibleResponse`（`models/event.py:577`）是**无字段基类**，且 `_frontend_export.py:13-14` 明确把它
  **排除**在导出白名单外（"FlexibleResponse (base class, no fields)"）⇒
  **OpenAPI 里 `/trades/*` 无字段 schema** ⇒ `generate_types` **无从生成** `SimTrade`。
- 实测 `SimTrade` / `SimulatedTrade` 在 `generated-types.ts` **不存在**（`grep` 命中 0）。
  生成文件里带同值的 union（`raw_direction?: "YES" | "NO" | "WAIT" | "AVOID"`，:515/516/569/570）是
  **4 值超集**，**不是**交易的 2 值契约。
- **契约仍是三层同源**：`models/event.py` 的 `Literal`（API）+ `simulated_trade_store.py:37` 的
  `CHECK (direction IN ('YES','NO'))`（DB）+ `api.ts:447`（TS）。三层今天**一致**，只是**第三层手写、无 CI 同步**。

→ **根因是 `FlexibleResponse` 反模式**（项目记忆已载：裸用它作 `response_model` 会让 CI 的 `type-sync-check`
覆盖不到该端点）。要把 `api.ts:447` 接上生成类型，**必须先给 `/trades/*` 加具名响应模型**
（`SimTradeResponse(FlexibleResponse)` 之类）并进白名单 —— **属契约改动，不代决**。

## 50.4 上线前 ②–⑥ 的可执行清单（只列「哪一步 / 在哪 / 怎么验证」，不填值）

> ① （推送 2 个本地提交）**已完成** —— `f0b662c` / `a9af1cf` 已 push、CI 全绿（见 §49）。
> 下列 ②–⑥ 的**事实基线**：`backend/app/core/preflight.py`（8 项门禁）、`backend/.env.production.example`、
> `deploy/{nginx.conf,Caddyfile}.example`、`docs/ops/RUNBOOK.md` 的「Production preflight」节。

| # | 要做哪一步 | 在哪做 | 怎么验证 |
|---|---|---|---|
| ②a | 填 4 个生产必填项 | `backend/.env.production`：`API_WRITE_KEY=`（:80 空）、`CORS_ALLOWED_ORIGINS=`（:87 空，**禁 `*`**）、`LLM_DAILY_COST_CAP_USD=25`（:95 **已是真数**，别设 0）、`BACKUP_ENCRYPTION_KEY=`（:167 空；**仅当 `BACKUP_SCHEDULE_ENABLED=true` 时门禁强制**）| `PMRF_ENV=production python -c "from app.core.preflight import production_config_failures as f; print(f())"` → 期望 `[]`；否则**启动即 raise 并一次列全**缺口 |
| ②b | 填真实 LLM 凭据 | 同上。legacy（`OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_BASE_URL`）或编号式（`OPENAI_API_KEY_N` + `OPENAI_MODEL_N_M`(+`OPENAI_BASE_URL_N`)；格式见 :129-134 注释）| `LLM_STARTUP_CHECK_ENABLED=true`（:147）时坏凭据**启动期拒启**，错误串 `Primary LLM startup check failed: all_routes_failed: missing_api_key`（:99-103）。⚠️ **编号式从 `os.environ` 直读、不经 `settings`** ⇒ preflight 看不见它，名字要逐字符照抄 |
| ③ | 前置反代 + 终止 TLS | `deploy/nginx.conf.example` 或 `deploy/Caddyfile.example`（compose **只绑 `127.0.0.1:8000`**，容器不做 TLS）| 反代后 `curl -sI https://<域名>/api/health` 得 200/503；**直连公网 `IP:8000` 应连不上** |
| ④ | 定代理跳数 | 模板 **:68 已是** `TRUSTED_PROXY_HEADER=true`（**非注释**）⇒ 真正要定的是 `RATE_LIMIT_TRUSTED_PROXY_HOPS`（默认 **1**，`config.py:288`；**模板未命名**）| 两个 shipped 反代例（nginx `$proxy_add_x_forwarded_for` / Caddy 替换头）**默认 1 即对**；**仅当前面还叠了 CDN（如 Cloudflare）才升 2**（`config.py:283-287`）。看限流是否按真实客户端分桶 |
| ⑤ | 接通至少一个告警渠道 | `backend/.env.production`，二选一：`SENTRY_DSN=<dsn>`（:214 注释，成本最低、覆盖请求路径）；或 `SCHEDULER_FAILURE_ALERT_ENABLED=true`（:217 **已赋 false，要改**）+ `SCHEDULER_FAILURE_ALERT_WEBHOOK_URL=<url>`（:218 注释）| **开关与地址必须成对**；只开开关不填地址 = 没接。启动日志不应再出现 `No alert push channel is configured`（:200-201）|
| ⑥ | 决定 6 个默认 OFF 的 overlay 开关 | `backend/.env.production` 的 **:47/51/56/57/61/64** 全是**注释行** `# KEY=true`；按需**取消注释**（**绝不能写成 `=false`** —— overlay `override=True` 会压掉 base 里的 true）| 未开的后果：对应界面**永久为空、且界面不说明原因**（:36-37/:45-46/:50/:55）。⚠️ `CONCLUSION_CHALLENGE_ENABLED` 与 `EVENT_CHALLENGE_ENABLED` **要两个都开**，否则 review queue 的 challenge trigger 永不触发（:53-55）。验证：读 `/review-queue` 的 `enabled` 字段 |

**对 `go-live-readiness-2026-10-01.html` 的一处事实更正（追加式，不改 HTML）**：该文第 ④ 项写「设置 `TRUSTED_PROXY_HEADER=true`」——
但生产模板 :68 **已经是** `TRUSTED_PROXY_HEADER=true`。真正需要操作员决定的是 **`RATE_LIMIT_TRUSTED_PROXY_HOPS`**（默认 1，多数部署无需动）。

## 50.5 本节未做 / 交接

| 事项 | 状态 |
|---|---|
| `third_place` 该不该算淘汰赛 | ⏳ **建模决定，未动**（§50.1；当前不可达）|
| `/trades` 增「已作废」tab | ⏳ **产品决策，未动**（§50.2）|
| `api.ts:447` 接生成类型 | ⏳ **未动**；根因已查清（§50.3）—— 需先给 `/trades/*` 加具名响应模型 |
| 控制台支持选 `gbm`（前端 `EngineName` / `ENGINES`）| ⏳ **产品决定，未动**（§48.9 遗留）|
| 上线前 ②–⑥ | ⏳ **清单已给（§50.4），执行需操作员**（不代填值）|
| push / 开 PR | ⏳ 本节**只改本文档**（生产代码一行未动）；按惯例直推 `main`，**无 PR** |

**本节改 1 个文件（本文档，追加式）。未提交、未 push。**

---

# §51 落地：①生产者、②③ `/trades` 契约、④前端 `gbm`（2026-10-02）

> 承接 §50：四项**未代决**事项经用户确认「按建议做」后**动手实施**。
> 与 §49/§50 **只改文档**不同 —— **本节改的是生产代码**，且首次暴露「改生产代码会打断
> 已登记的变异」这一连带（§51.8）。
> 结构：结论先行 → 逐项改动 → 验证证据 → 变异登记 → 发现但未动 → 交接。

## 51.1 结论先行

| # | §50 的定性 | 本节做了什么 | 关键判据 |
|---|---|---|---|
| ① | `parse_fixture` **产不出** `third_place`/`round_of_32`；5 个判定点里 2 个算淘汰赛，而测试把它钉在**非淘汰赛**一侧 | 补两支分支 + 5 个判定点对齐 + 分区断言改为 `{group_stage}` | `Round of 32`→`round_of_32`、`3rd Place Final`/`Match for third place`→`third_place`，两者 `is_knockout=True`；非淘汰赛只剩 `{group_stage}` |
| ② | 前端 2 tab、后端无 voided 读函数（`status` 早已是三态） | 后端加读侧 + 路由；前端加**第三 tab** + 第三张表 | `GET /trades/voided` 路由级 3 用例；store 级 2 用例；前端 1 用例 |
| ③ | `api.ts:447` 手写 `SimTrade` 的根因 = 4 端点用裸 `FlexibleResponse` ⇒ OpenAPI 无字段契约 ⇒ 生成类型覆盖不到 | 4 个具名模型 + 白名单 + 重生成 + **删手写 interface** | `generate_types --check` up to date；`tsc` 0 错（含一处**必须**放宽的连带，§51.5） |
| ④ | 前端 `EngineName` 恰好少 `gbm`，而后端已接受 | 加 `gbm` + 选择器一项（**不动 `TunableEngine`**） | 选择器选项列表被逐项钉住；选中 `gbm` 真的以 `"gbm"` 调路由 |

## 51.2 ① 生产者：两支补上，5 个判定点对齐

**改动清单**（`stage` 词表在本仓有 5 个判定点，见 §26–§28 的普查）：

| 文件 | 改动 |
|---|---|
| `services/world_cup_match_service.py` | `parse_fixture` 补 `third_place` 与 `round_of_32` 两支 |
| `services/world_cup_prediction_pipeline.py` | `_STAGE_MAP` 加两个别名；`_KNOCKOUT_STAGES` **4 → 6** |
| `kernel/engines/elo_odds_engine.py` | `_KNOCKOUT_STAGES` → **8** |
| `kernel/engines/gbm_engine.py` | → **10** |
| `kernel/engines/situational_adjust.py` | → **8** |
| `services/conclusion_challenge_world_cup_adapter.py` | `_HIGH_RISK_STAGES` → **8** |

**一个顺序陷阱（写在代码注释里，也写在这里）**：`third_place` 分支**必须排在 `final` 之前** ——
API-Football 的拼写 `"3rd Place Final"` **含 `"final"`**，顺序反了就把三四名决赛读成决赛本身。
两个新分支都带注释说明「为什么在这里」而不只是「这里做什么」。

**测试**：`tests/test_world_cup_match_service.py` 新增 3 条（`round_of_32`、两种 `third_place` 拼写），
`tests/test_knockout_stage_whitelist_consistency.py` 的 `test_stage_map_values_partition_into_knockout_and_group_stages`
断言由「钉住 `third_place` 在非淘汰赛一侧」改为 `assertEqual(non_knockout, {"group_stage"})`，
并新增 `ProducerVocabularyTests`（**直接驱动真实 `parse_fixture`**，而不是抄一份名字清单 ——
抄的清单会以这个类要防的方式变陈旧）。

## 51.3 ② `/trades/voided`：读侧 + 第三 tab

**后端**（`memory/simulated_trade_store.py`）：新增 `count_voided_trades()` / `list_voided_trades()`，
排序 `exit_time DESC, trade_id DESC`（`trade_id` 是随机 uuid ⇒ 只作 tiebreaker，见 §51.8）。
**路由**（`api/routes/events.py`）：新增 `GET /trades/voided`，与 `/open`、`/closed` 同信封。

**前端**（`app/trades/page.tsx`）：第三个 tab「已作废」+ 第三张表（事件/方向/raw edge/方向 edge/仓位%/决策/入场时间/**作废时间**）。

**空态文案按「0 必须只说一件事」处理**（§49.6 的判据）：作废由 `void_prediction` 驱动、**不受任何具名开关门控**
⇒ 按判据边界（「只在同仓存在『在某界面命名该开关』的先例时才消歧」）**不编造开关**，只做**语义**说明 ——
写明「作废是终态但不结算…其结算字段必然为空，**空值表示未结算，不是 0 收益**」。
依据 `void_trade` 的实现：它只写 `exit_reason='voided'` / `exit_time` / `status`，
`actual_outcome`/`pnl_pct`/`is_win` **保持 NULL**。

## 51.4 ③ 具名响应模型：**NOT NULL 列必须去掉默认值**

`models/event.py` 新增 `SimTrade` / `SimTradeListResponse` / `TradeStatBucket` / `TradeStats`（均继承
`FlexibleResponse`），并加进 `models/_frontend_export.py` 的 import + `__all__`，再
`python -m scripts.generate_types` 重生成。

**一个反直觉的实现细节**：`pydantic2ts` 对**带默认值**的字段一律生成可选（`field?: T`）。
若给 `SimTrade` 的每个字段都写 `= None`，生成的 TS 会**比被替换掉的手写 interface 更松** ——
`t.market_prob.toFixed(1)` 这类调用会停止通过类型检查，而它们本来是通的。
所以**镜像 store 的 NOT NULL 列**的字段（`trade_id`/`event_id`/`event_title`/`direction`/`entry_prob`/
`market_prob`/`entry_edge`/`entry_time`/`position_pct`/`decision`/`status`/`created_at`/`updated_at`）
**故意不给默认值** ⇒ 生成为**必需**。依据：`_row_to_dict` 总是发出这些键，缺一个是 store 的 bug，
一个响亮的 422 才是对信号。

**前端收口**（`lib/api.ts`）：加 import + re-export 3 个类型，删掉手写的 `SimTrade` / `TradeStats`
（后者还带一个**后端从来没返回过**的键 `avg_raw_edge_at_entry`）。原来的两个 `eventsApi` 方法改用
`api<SimTradeListResponse>`，并新增 `voidedTrades`。

## 51.5 ③ 的连带：`PnlBadge` 必须接受 `undefined`

删掉手写 interface 后 `tsc` 报 **4 处** `TS2322`，都在 `app/trades/page.tsx`：
`<PnlBadge pnl={…} />` 收到 `number | null | undefined`。

根因不是笔误，而是**契约本身的表达力缺口**：`pydantic2ts` 对 `T | None` 一律加 `?`，
所以「**必存在但可为 null**」这个状态在生成的 TS 里**无法表达**，只能落成 `T | null | undefined`。
修法选**放宽 prop 类型**（`pnl: number | null | undefined`）而不是在 4 个调用点撒 `?? null`：
`PnlBadge` 内部本来就用 `pnl == null`（同时兜 `null` 与 `undefined`），语义上它**本就**接受「未测量」；
而在 4 处加 `?? null` 会把同一个事实写四遍，且下个调用点还会再犯。

## 51.6 ④ 前端 `gbm`

`lib/world-cup/engine-api.ts` 的 `EngineName` 加 `"gbm"`（**`TunableEngine` 不动** —— 后端
`autoTune` 的 `engine_name` 只接受 `elo_odds`/`hybrid`/`integrated`）；
`components/sports/world-cup/engine-console.tsx` 的 `ENGINES` 加一项，标签用**全仓一致的 `GBM`**
（`analytics-dashboard.tsx:429` 与 `match-prediction-card.tsx:87` 都这么写）。

**为什么定性是「漏项」而不是「产品决定」**（§50 已给，此处留证）：`BatchSummary.gbm_count`、
批量摘要里的 `GBM ${n}` 分桶、以及 `engine-console.test.tsx` 里一条 **`expect(summary).toHaveTextContent("GBM 1")`**
的既有断言**早就存在** —— 即控制台**已经在显示** GBM，只是**选不出来**。
更有 `analytics-dashboard` 的 `EngineKey`、`engine-comparison-view` 的 `key: "gbm"`、
`predictions-api.ts` 的两处 union 都包含 `gbm`。

## 51.7 验证（全部本机，2026-10-02）

| 闸门 | 结果 |
|---|---|
| `ruff check app/`（= CI `Lint backend`） | All checks passed |
| `mypy app/ --config-file mypy.ini` | Success: no issues found in 331 source files |
| `python -m scripts.generate_types --check`（= CI `type-sync-check`） | generated-types.ts is up to date |
| `scripts/eol_audit.py`（本项目常驻探针） | **line-ending damage: none** |
| 后端全量 | 见 §51.7.1（**第一轮 3 败 → 修 → 第二轮**） |
| 前端全量 | **125 files / 737 tests passed**（改动前 735 ⇒ **+2** 恰为本批新增的两条用例） |
| 前端 `tsc --noEmit` / `eslint` | 0 错 |

### 51.7.1 🔴 后端全量第一轮 **3 败**：不是新代码坏，是**本批打断了已登记的变异**

第一轮：`3 failed, 6539 passed, 11 skipped`。三败**全部**来自同一个变异 —— 详见 §51.8.1。
这是**本批自己造成的**，不是既有缺陷；修完后第二轮应全绿。
`11 skipped` 全是 `test_integration_live` 的 `RUN_LIVE_TESTS` 选择性跳过（与 CI 的 18 条差异是平台性的）。

## 51.8 变异登记（四阶段，`scripts/mutation_verify.py`）

| 集合 | 变化 | 新增/改的条 | 四阶段结果 |
|---|---|---|---|
| `voided-trade` | 4 → **6** | **V5** `count_voided_trades` 读的终态、**V6** `list_voided_trades` 的 WHERE | 各 **1/1 files red** |
| `whitelist-fixtures` | 25 → **28** | **W10 改针**、**W33/W34** 生产者分支 | W10 **1/1**；W33/W34 各 **2/2 files red** |

**总计 67 → 71 条**；`tests/test_mutation_verify.py` 全绿（清单静态自洽由它守，见 §51.8.1）。

### 51.8.1 W10：**改生产代码会打断已登记的变异**（本节最重要的教训）

W10 原针是 pipeline 的**旧 4 成员字面量**：
`_KNOCKOUT_STAGES = {"round_of_16", "quarterfinal", "semifinal", "final"}`。
① 把它扩成 6 成员多行字面量之后，**这根针一个字节都匹配不到了**。

**它是被既有守卫抓到的，不是被我发现**：`tests/test_mutation_verify.py` 的
`test_every_needle_occurs_exactly_once_in_its_file` / `..._matches_whichever_line_ending_the_checkout_uses` /
`WholeSetApplyTests.test_the_whole_set_selection_passes_preflight` 三条同时变红，
报的都是同一件事：`whitelist-fixtures 10: needle occurs 0x in …/world_cup_prediction_pipeline.py`。
→ **可操作结论：动到「变异针所指的那些字节」时，必须回头跑 `tests/test_mutation_verify.py`**；
`verify` 只为**你点名的那条**做前置检查，不会替你看整张清单。

**W10 的语义也一并变了**：它的用途是「给白名单塞一个 normalizer **永远产不出来**的名字 ⇒ `is_knockout` 恒 `False`」。
`round_of_32` 恰恰是 **① 刚变成可达**的规范形，塞回去**不再是死成员**，于是这条守卫会失去它**唯一**的触发器。
改为 `"round_of_64"`（仍不可达），并在注释里写明这次替换的**原因**——
否则下一个人只会看到一个莫名其妙的 `round_of_64`。

### 51.8.2 V5 差点就是**空头守卫**：同一个数可以是两个答案

V5 打 `count_voided_trades` 的状态 token。它的守卫用例初版是「1 条 voided + 1 条 closed + 1 条 open」——
此时「读对状态」与「读错状态」**返回同一个 1**，断言对**正确实现和坏实现都成立**，变异阶段②永远过不了。
把 closed 改成**两条**才让两个数分开。这正是本仓反复踩到的形态（§31/§33/§34：**空头守卫只能靠补用例消掉**）。

### 51.8.3 W33 的跨模块断言是**部分盲**的 —— 所以两个 guard 文件都要挂

W33 废掉 `parse_fixture` 的 `third_place` 分支。跨模块那条关系
（`ProducerVocabularyTests.test_the_two_new_rounds_are_classified_knockout`）**会红**，
但**只靠 openfootball 拼写那一腿**：「3rd Place Final」含「final」，分支没了就落回 `final`，
而 `final` 本来就在淘汰赛集合里 ⇒ 这一腿**照样绿**。
实测 `2/2 files red` 成立，正是因为并挂了生产者自己的**精确形状**断言
（`test_third_place_mapping_from_api_football_spelling`）。
→ **精确断言与关系断言不是互相替代，是互相补盲**（与 §45.5 的 `every_file_red` 同源）。

## 51.9 发现但**未动**（本节不代决）

| 发现 | 位置 | 为什么没动 |
|---|---|---|
| `batch_switch_engine` / `batch_predict` 的 **docstring 只列 4 个引擎**，漏 `gbm` | `api/routes/world_cup_predictions.py`（:546-548 一带） | 参数类型 `PredictionEngine` **已含 `gbm`**、描述串也已列全 ⇒ 只是**注释**陈旧。用户点名只改前端 `EngineName`/`ENGINES` ⇒ 仅**记录**，不顺手改（避免扩大本批 diff） |
| 「必存在但可为 null」在生成 TS 里**不可表达** | `pydantic2ts` 语义 | 属生成器能力缺口，不是本仓能就地修的；已在 §51.5 用放宽 prop 类型局部收口 |
| `parse_fixture` 仍有 `unknown` 兜底 | `world_cup_match_service.py` | 兜底**本身是对的**（未知轮次不该猜成淘汰赛）；① 只保证**已知拼写**不再落到它。`test_every_emitted_stage_is_a_name_the_pipeline_can_classify` 允许 `unknown` 但 `test_the_two_new_rounds_are_classified_knockout` 把这两个**特定**轮次排除在外 |

## 51.10 本节未做 / 交接

| 事项 | 状态 |
|---|---|
| 上线前 ②–⑥ | ⏳ **仍是操作员动作**（§50.4 的清单未变） |
| `batch_switch_engine` docstring 补 `gbm` | ⏳ 已记录（§51.9），**未动** |
| 前端 `TunableEngine` 是否也该有 `gbm` | ⏳ **未动** —— 后端 `autoTune` 只接受三个，属**另一件事** |
| 提交 / push | ⏳ **本节全部改动未提交、未 push**（21 个已改文件 + 2 个 `scripts`/`tests` 清单文件），等明确指令 |
| 后端全量第二轮 | ⏳ 见 §51.7.1；第一轮那 3 败已由本节的 W10 改针消掉，第二轮结果回填于本节末尾 |

**本节改的文件（追加式，不覆盖 §1–§50）**：仅本文档。**生产代码的改动未提交、未 push。**

## 51.11 后端全量第二轮：`1 failed`，且**不是本批的**（并把一条自伤记下来）

第二轮读数：**`1 failed, 6540 passed, 11 skipped, 1089 subtests passed`（22m29s）**。
⚠️ 进程 `rc=1`，但**判据只看 junit**（本机 `safe-delete` 会拦 pytest 临时目录清理：
`test_first_member…` 那类"全绿却 rc=1"的形态见 §48）。

唯一失败与它的原始异常：

```
tests/test_realtime_ticket_store.py::test_spawned_workers_share_atomic_consume_once
sqlite3.OperationalError: attempt to write a readonly database
  ← spawned worker → app/realtime/ws_auth.py:142  conn.execute(...)
  tmp_path = …/pytest-of-Alin/pytest-3598/test_spawned_workers_share_ato0/realtime-tickets.db
```

**判定它不属本批（四条，都可复算）**：

1. **交集为空**：本批改动的文件（§51.2–§51.6 的 21 个 + `scripts/mutation_verify.py` +
   `tests/test_simulated_trade_store.py`）与 `app/realtime/ws_auth.py`、
   `tests/test_realtime_ticket_store.py` **没有一个相交**。
2. **第一轮它是绿的**：第一轮的 3 败**全部**是 W10 的针（`needle occurs 0x`），失败集合里没有它。
3. **隔离复跑 3/3 全绿**：`pytest tests/test_realtime_ticket_store.py` → `tests=4 failures=0 errors=0` ×3。
4. **异常形态是"环境态"而不是"断言态"**：它是 **spawn 出来的子进程**在写**另一个进程刚创建的临时目录**里的
   SQLite 时拿到的只读句柄。这是 Windows（AV/索引器/临时目录清理）的经典瞬时态，
   与 `TicketStore.redeem` 的"socket 只能核销一次"这条**被测不变量**无关 ——
   也就是说，**这个用例的断言（`True×1 / False×1`）根本没有被证伪**，被证伪的是操作系统的文件权限。

### 51.11.1 ⚠️ 一条自伤：第二轮跑着的时候，我又起了一个 pytest

第二轮进行中我起过一次 `pytest tests/test_mutation_verify.py`（隔离、约 1 秒）。
**两个 pytest 同时在跑 ⇒ 我无法排除自己对上面那次瞬时态的贡献。**
→ **纪律（本项目通用）：全量套件在跑时，不要再起第二个 pytest。**
要并行推进就改做**不做 I/O 竞争**的事（读文件、写文档）。
第三轮因此**在没有任何其它 pytest 的情况下**重跑，作为干净回执（结果记于 §51.12）。

## 51.12 后端全量第三轮：干净绿底（并把 §51.9 的一处记账更正掉）

第三轮在**没有任何其它 pytest 并行**的条件下重跑（纪律见 §51.11.1）：

| 轮次 | junit `tests` | `failures` | `errors` | `skipped` | 判读 |
|---|---|---|---|---|---|
| 第一轮（W10 未改针） | 7637 | **3** | 0 | 11 | 3 败**全**是 W10 的针 |
| 第二轮（并发偶发） | — | 1 | 0 | — | 见 §51.11，非本批 |
| **第三轮** | **7641** | **0** | **0** | **11** | ✅ 干净绿底 |

日志尾：`6541 passed, 11 skipped, 1 warning, 1089 subtests passed in 1337.77s (0:22:17)`。
11 skipped **全部**来自 `test_integration_live` 的 `RUN_LIVE_TESTS` 选择性跳过（非异常，非本批引入）。

### 51.12.1 「6553 vs 6552」的计数困惑：两轮**唯一用例集合完全相同**

第三轮与第一轮的 junit 逐条取 `(classname, name)` 做集合 diff：

```
round1 6552   round3 6552
--- only in round1 ---   (空)
--- only in round3 ---   (空)
```

⇒ 两轮**用例集合一致，无增无减**。`passed + failed + skipped` 的加总与唯一用例集**天然差 1**，
属 pytest 的计数口径（对比 `--collect-only` 收集数时必须用 `passed + skipped`），**不是用例漂移**。

### 51.12.2 ⚠️ 更正 §51.9：`batch_predict` 的 docstring **本来就列了 `gbm`**

§51.9 首行（及 §51.10 第 2 行）写的是「`batch_switch_engine` **/ `batch_predict`** 的 docstring 只列 4 个引擎」。
**实测只有一处漏。** `batch_predict` 的 docstring（`api/routes/world_cup_predictions.py:522-523`）
本来就写着 `"integrated", "high_confidence", "gbm"`；**漏 `gbm` 的只有 `batch_switch_engine`**
（同文件 `:544-547` 的 Args 枚举）。按「只追加不改写」的约定，**不动 §51.9 原文**，在此更正。

### 51.12.3 C 项已做：`batch_switch_engine` docstring 补 `gbm`

```diff
             - "high_confidence": Auto-select best engine based on confidence
+            - "gbm": LightGBM xG model engine
         status_filter: Only process matches with this status (default: "scheduled")
```

纯**注释**改动（零行为影响）——但按纪律照样过闸门，不留"注释就不用验"的口子：

| 闸门 | 结果 |
|---|---|
| `ruff check app/` | All checks passed |
| 目标文件字节画像 | `CRLF=1022 / bare_LF=0`（+1 行，**行尾未漂移**） |
| `scripts/eol_audit.py` | `line-ending damage: none` |
| `pytest tests/test_world_cup_predictions_routes.py` | `tests=34 failures=0 errors=0 skipped=0` |

措辞与 `PredictionEngine`（`services/world_cup_engine_names.py:15-17`，成员序 `…, "gbm", "auto"`）
和两个路由的 `Query(description=…)` 描述串对齐 —— 三者现已**一致含 `gbm`**。

### 51.12.4 本批最终改动清单：24 个文件

| 类别 | 数 | 文件 |
|---|---|---|
| 后端生产 | 11 | `api/routes/{events,world_cup_predictions}.py` · `kernel/engines/{elo_odds_engine,gbm_engine,situational_adjust}.py` · `memory/simulated_trade_store.py` · `models/{event,_frontend_export}.py` · `services/{conclusion_challenge_world_cup_adapter,world_cup_match_service,world_cup_prediction_pipeline}.py` |
| 前端生产 | 5 | `app/trades/page.tsx` · `components/sports/world-cup/engine-console.tsx` · `lib/{api,generated-types}.ts` · `lib/world-cup/engine-api.ts` |
| 工具 | 1 | `backend/scripts/mutation_verify.py` |
| 测试 | 6 | `tests/{test_events_routes,test_knockout_stage_whitelist_consistency,test_simulated_trade_store,test_world_cup_match_service}.py` · `app/trades/page.test.tsx` · `components/sports/world-cup/engine-console.test.tsx` |
| 文档 | 1 | 本文档 |

⇒ 切**两个提交**：① 代码 + 测试（**23** 个）／② 文档（**1** 个）。`HANDOFF.md` 与 `.workbuddy/**` 均 gitignored，**不进任何提交**。

# §52 前端 `TunableEngine` 的**三个成员是「别名规则」，不是白名单**（2026-10-02，只改注释）

## 52.1 结论先行

| 问题 | 结论 |
|---|---|
| 前端 `TunableEngine` 只有 `elo_odds`/`hybrid`/`integrated` —— 这是**白名单**吗？ | **不是**。它精确对应 `engine_method_filter()` 的 **3 个显式别名分支**；其余引擎走**兜底** `like("%name%")` |
| `gbm` 能被 auto-tune 吗？ | **能**。库中 `ai_optimized_predictions.original_engine` 实有 **`gbm_lightgbm` 10 条**，兜底分支正好命中 |
| 「后端只接受三个」这句话出自哪里？ | **只出自 5 处 docstring**。相关函数签名全是裸 `str`，**且都不校验白名单** |
| 本轮改了行为吗？ | **零行为影响**：6 处改动**全在 docstring**，无一行可执行语句变动 |

## 52.2 起因：§51.10 把它记成「属另一件事」，这个判断**不准确**

§51.10 写「前端 `TunableEngine` 是否也该有 `gbm` ⏳ 未动 —— 后端 `autoTune` 只接受三个，属**另一件事**」。
**「后端只接受三个」是注释级断言，不是契约**：`auto_tune_engine` 的参数是 `engine_name: str`
（**不是** `PredictionEngine`），`run_full_auto_tuning_cycle` / `calculate_optimization_patterns` /
`analyze_and_optimize_all_predictions` / `run_async_optimization` 同样都是裸 `str`，**无一校验白名单**。

## 52.3 判据来自三处（都可复算）

**① `engine_method_filter()` = 3 个显式分支 + 1 个兜底**（`services/engine_auto_tuning_service.py:20`）

```python
if engine_name == "integrated": return method_column.like("integrated%")
if engine_name == "elo_odds":   return method_column.like("elo%")
if engine_name == "hybrid":     return or_(method_column.in_(["hybrid", "rule_only", "rule_dominant"]),
                                           and_(method_column.like("%hybrid%"), ~method_column.like("integrated%")))
return method_column.like(f"%{engine_name}%")        # ← 兜底：其余任何名字
```

三个分支处理的是**别名**：管道写进 `original_engine` 的是变体/带后缀的名字，所以每个需要自己的匹配器。
**它们不是「只有这三个能过滤」**。

**② 权威引擎集含 `gbm`**：`world_cup_quality_service.ENGINE_NAMES = ("elo_odds", "hybrid", "gbm", "integrated")`。
⚠️ 它与 `services/world_cup_engine_names.PredictionEngine` 的 6 个**不是同一集合** —— 后者是 **API 可接受的名称**
（含 `auto` / `high_confidence` 这类选择器），前者是**可运行的实现引擎**。

**③ 实证**（只读 `backend/world_cup_predictions.db`，`ai_optimized_predictions.original_engine` 的 distinct）：

```
hybrid (98) · elo_odds_fusion (Elo 30% + Odds 70%) (82) · rule_only (38) · elo_only (37)
· integrated (…) (18/14/13/6/2/1) · gbm_lightgbm (10) · …
```

`gbm_lightgbm` 已被兜底分支 `like("%gbm%")` 命中 ⇒ **gbm 的优化链路是活的**（它那 10 条记录正是
不带 `engine_filter` 的全量优化跑出来的）。

## 52.4 改了什么（6 处，**全在 docstring**）

| 文件 | 函数 | 改动 |
|---|---|---|
| `services/engine_auto_tuning_service.py` | `engine_method_filter` | 说明三分支是**别名规则**、兜底覆盖其余、**分支顺序不得移到兜底之后**（否则某个桶静默变形） |
| 同上 | `analyze_and_optimize_all_predictions` | `engine_filter` 写明经 `engine_method_filter` 匹配、三别名非白名单 |
| 同上 | `calculate_optimization_patterns` | 同上 |
| 同上 | `run_full_auto_tuning_cycle` | 同上 |
| `services/engine_auto_tuning_async.py` | `run_async_optimization` | 同上 |
| `api/routes/world_cup_predictions.py` | `auto_tune_engine` | 「三个引擎」→ 指向 `engine_method_filter`，明说 `gbm` 可达 |

**`git diff --numstat` 实测**：service `+17/−4`、async `+3/−1`、routes `+4/−1` ⇒ 合计 **`+24 / −6`**。
⚠️ 这 6 行全部是 docstring 行 —— **无一行可执行语句变动**。

## 52.5 验证（全部本机，2026-10-02）

| 闸门 | 结果 |
|---|---|
| `ruff check app/` | All checks passed |
| 字节画像（改后 → 改前） | service **522**（←509）· async **269**（←267）· routes **1025**（←1022），三文件 **bare_LF=0** |
| `scripts/eol_audit.py` | `line-ending damage: none`（三文件均 `ok (LF -> CRLF checkout)`） |
| `pytest tests/test_engine_auto_tuning_service.py tests/test_world_cup_predictions_routes.py` | `tests=39 failures=0 errors=0 skipped=0` |

§51 已验证的「哪些测试文件**不**引用这些 docstring」也一并查过：`grep` 无命中 ⇒ 改注释不可能打断断言。

## 52.6 顺带查清并**否掉**的一条（写下来，免得下一个人再查一遍）

全仓仍有 **78 个**端点用裸 `response_model=FlexibleResponse`（`events.py` 58 + `world_cup_predictions.py` 20）。
**不要**把它们「补成具名模型」：

- `backend/mypy.ini:191` 明说这是**既定的、被接受的**策略 —— 它 **hide the return boundary**，
  且**已用 `-> dict[str, Any]` 注解补偿**（"annotating a handler to `-> dict[str, Any]` is the proof the body
  returns what the route promises"）。这是**已还过的账**，不是欠债。
- `frontend/src/lib/api.ts` 对 world-cup **零引用** ⇒ 这些端点**不经 api.ts 消费**。

⇒ §51 ③ 之所以只需要修 `/trades/*`，正因为**只有那批有前端消费者**。
**判据是「有没有消费者」，不是「有多少端点」。**

## 52.7 未做（本节不代决）

| 事项 | 状态 |
|---|---|
| 前端 `TunableEngine` / `TUNABLE_ENGINES` 加 `gbm` | ⏳ **未动** —— 会让控制台多出一个**会写库**（发起调参任务）的入口，属**产品决策**，需明确授权 |
| 5 处 docstring 之外是否还有同类陈旧注释 | ⏳ 未普查（本节只处理了 auto-tune 这一族） |
| 提交 / push | ⏳ 本节 **3 个代码文件 + 本文档**（共 4 个）**未提交、未 push**，等明确指令 |

## 52.8 同类陈旧注释的普查（收口 §52.7 那条「未普查」）

对全仓做了一次「硬编码引擎枚举」的**只读**普查（正则：`"elo_odds"` 与 `"hybrid"`/`"integrated"` 同现，
前后端各一遍），逐条与**权威来源**比对。权威来源有两个，**别混**：

- **运行时白名单**：`run_prediction_pipeline` Step 3 的集合字面量
- **质量报告键**：`world_cup_quality_service.ENGINE_NAMES`

| 位置 | 原文 | 判定 | 处理 |
|---|---|---|---|
| `api/routes/world_cup_predictions.py:353` | `"auto" (default), "elo_odds", "hybrid", "integrated", "high_confidence", or "gbm"` | ✅ **已含 `gbm`** | 不动 |
| 同文件 `:522` / `:536` / `:603` | docstring / Query description | ✅ 含 `gbm` | 不动 |
| `services/world_cup_prediction_pipeline.py:667`（`run_prediction_pipeline`） | `("elo_odds", "hybrid", "integrated", "high_confidence", "auto")` | ❌ **缺 `gbm`**，而**同文件 `:734` 的白名单含 `gbm`** —— 自相矛盾 | **已改** |
| 同文件 `:1495`（`batch_predict_matches`） | `("elo_odds", "hybrid", "auto")` | ❌ 只列 3 个，**还漏 `integrated` / `high_confidence` / `gbm`** | **已改**：改为「原样转发给 `run_prediction_pipeline`，接受同一组名字」 |
| `services/world_cup_confidence_calibration.py:95`（`compute_reliability_curve`） | `(e.g., "elo_odds", "hybrid", "integrated")` | ❌ 举例漏 `gbm` —— 取值实为 `quality["by_engine"]` 的键（= `ENGINE_NAMES`，**含 `gbm`**） | **已改** |
| `services/world_cup_dynamic_weights.py:39` | `(e.g., "elo_odds", "hybrid")` | ⚪ 纯举例（`e.g.`），只举 2 个 | **未动**（属举例，非枚举） |
| `services/world_cup_engine_names.py:16` | `PredictionEngine` Literal | ✅ 权威来源本身 | 不动 |
| `services/world_cup_quality_service.py:20` | `ENGINE_NAMES` | ✅ 权威来源本身 | 不动 |
| 前端 `predictions-api.ts:91` | `"elo_odds" \| "hybrid" \| "integrated" \| string` | ✅ 末尾有 `\| string` 兜底 | 不动 |
| 前端 `engine-api.ts:42` | `TunableEngine`（3 个） | ⏳ **已知、待产品决策**（§52.7） | 不动 |

**关键事实（写下来，免得下次再推一遍）**：`run_prediction_pipeline` Step 3 的实际语义是
**「`auto` 先解析为 `elo_odds`；其余必须落在 `{elo_odds, hybrid, integrated, high_confidence, gbm}` 内」**
⇒ **可接受输入的全体 = 那 5 个 + `auto` = `PredictionEngine` 的全部 6 个成员**。
`gbm` 只在 `:734` 出现、`auto` 只在 `:728` 出现 —— **要合起来看才等于 6**；只看任意一处都会得出错的枚举。

### 52.8.1 验证

`git diff --numstat`（本轮**全部为 docstring 行**）：`world_cup_prediction_pipeline.py` **`+8/−2`**、
`world_cup_confidence_calibration.py` **`+4/−1`** ⇒ **§52 全部代码改动合计 `+36 / −9`，共 5 个代码文件**
（§52.4 的 3 个 + §52.8 的 2 个；`events.py` 那批属于已提交的 §51，不计入）。

⚠️ 又验证了一次「别口算行数」：pipeline 那处我口算 `+13/−7`，实测 **`+8/−2`** ——
差在 **git 把未变的相同行**（`- "elo_odds": …`、`- "auto": …` 等）**当作上下文**，
只有首行整行替换与新增的 `gbm` 行计入增删。

| 闸门 | 结果 |
|---|---|
| `ruff check app/` | All checks passed |
| 字节画像 | pipeline **1609**（←1603）· calibration **272**（←269），两文件 `bare_LF=0` |
| `scripts/eol_audit.py` | `line-ending damage: none`（**6 个**改动文件全 `ok (LF -> CRLF checkout)`） |
| `pytest` 四个受影响文件 | **`tests=76 failures=0 errors=0 skipped=0`** |
| `mypy app/` | **331 source files, no issues** |
| `generate_types --check` | `generated-types.ts is up to date` |
| `pytest tests/`（**全量**，21m27s） | **`tests=7641 failures=0 errors=0 skipped=11`** —— 与 §51 第三轮**逐数相同**（docstring-only 改动本该如此）。⚠️ 进程 `rc=1` 的根因是日志尾的 `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]`（§48 先例），**不是测试失败** —— 判据只用 junit 的 `failures`/`errors` |
| 测试是否引用新写的 docstring | `grep` **零命中**（`alias rules` / `whitelist at Step 3` / `quality["by_engine"]` / `LightGBM xG model engine`）⇒ 改注释不可能打断断言 |

**A 项（`world_cup_dynamic_weights.py:39`）的追加判据 —— 确认**不动**：该函数的**两个调用点**
（`:75` / `:76`）分别只传 `"elo_odds"` 与 `"hybrid"`，所以那句 `(e.g., "elo_odds", "hybrid")`
是**如实反映实际用法**，**不是陈旧枚举**；给它补 `gbm` 反而会误导。

### 52.8.2 本节收口后的开放项

| 事项 | 状态 |
|---|---|
| 前端 `TunableEngine` / `TUNABLE_ENGINES` 加 `gbm` | ⏳ 未动（产品决策，同 §52.7） |
| `world_cup_dynamic_weights.py:39` 的 `e.g.` 举例是否补 `gbm` | ⏳ 未动（举例形式，无害） |
| 提交 / push | ⏳ 本节 **5 个代码文件 + 本文档**（共 6 个）**未提交、未 push** |


---

# §53 独立复检：2026-10-03 的工作区现状（不复用任何既有读数）

> **性质**：本节是**全新一轮独立复检**，所有数字都是本机 2026-10-03 当场跑出来的，**没有引用 §48–§52 的读数**。
> 目的是回答「现在的树到底是什么状态」，而不是延续上一轮的叙事。基线：`HEAD=c13e6bd`、`git status --porcelain` 0 行。

## 53.1 结论先行

**1. 🔴 唯一的新增红：CI 的 `frontend-tests` job 现在会失败。**
`npm audit --audit-level=high`（CI 第 98 行那道门）报 **5 个 high**，根因是 `braces <= 3.0.3` 的
[CVE-2026-93687 / GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)（栈耗尽 DoS），
而 **该 advisory 的 `Patched versions: None`**、npm 上 `braces` 最新版就是 `3.0.3` ⇒ **没有可升的修复版**。
机制与 `00aa264` **完全同型**：锁文件一个字节没动，是 advisory 库新增条目把 main 变红的。

**2. ⚠️ junit 的 `tests` 属性虚高 1089，但它**不是**「有测试没跑」。**
`<testsuite tests="7642">`，可文件里 `<testcase>` 恰 **6553** 个（且 `--collect-only` 也是 6553）⇒ 没有静默跳过。
虚高来源已定位：**1089 条 `unittest` 子测试（`subTest()`）被 junit 算作「用例」、而终端汇总单列不计**（见 53.5）。

**3. ⚠️ `MEMORY.md` §1 的「工作树有 51 个 MIXED 文件」已过期。** 实测 MIXED = **0**，`eol_audit.py --all` 报
`line-ending damage: none`（§48.3 早已收口）。本轮已就地更正该条。

**4. ✅ 除此之外，四道静态门 + 后端全量 + 前端四关全绿。** 详见 53.3。

## 53.2 复核口径

| 项 | 值 | 说明 |
|---|---|---|
| 基线 HEAD | `c13e6bd` | 工作树干净 |
| 本地 vs 远端 | **领先 `monkeydjl/main` 2 个提交**（`c13e6bd`、`93d4c38`） | **未推送**；远端最新是 `6fdb280` |
| Python | 3.11.9（项目 `.venv`） | 与 CI 的 3.11 同大版本 |
| Node | v22.22.2 | CI 固定 node 22 |
| 跑法 | 一切以 **junit 的 `failures`/`errors`** 为判据，不看进程退出码 | `safe-delete` shim 会让 rc 不可信 |

## 53.3 逐门读数（本机 2026-10-03）

| CI job / 命令 | 本机结果 | 判据来源 |
|---|---|---|
| `ruff check app/` | **All checks passed!** | 输出 |
| `python -m compileall app tests` | **零输出** | 输出 |
| `mypy app/ --config-file mypy.ini`（`PYTHONUTF8=1`） | **331 source files, no issues found** | 输出 |
| `pytest tests/` | junit `tests=7642 failures=0 errors=0 skipped=11`；日志 `6542 passed, 11 skipped`，**23:11** | **junit** |
| `scripts.generate_types --check` | `generated-types.ts is up to date.` | **输出文本**（脚本失败时 rc 仍 0） |
| `tsc --noEmit` | 零输出 | 输出 |
| `vitest run` | **125 文件 / 738 测试全过**，100.1 s | 输出 |
| `eslint` | 零输出 | 输出 |
| `next build` | `Compiled successfully`；静态页 **40/40**；`out/` **260 文件** | 日志 |
| `npm audit --audit-level=high` | ❌ **5 high**（见 53.4） | 输出 + `--json` |
| `pip-audit -r requirements.txt` | **No known vulnerabilities found** | 输出 |
| docker build（`deploy/Dockerfile`） | ⏳ **本机未复现** —— docker daemon 未运行 | — |
| gitleaks secret scan | ⏳ **本机未复现** —— 未安装 gitleaks | — |

**构建那一处 `Build error occurred` 是假红**：末尾报的是
`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":997,"threshold":50,...targets:["...\\.next\\export-detail.json"]}`
—— 拦点是**收尾清理**，不是编译。判据取 `Compiled successfully` + `Generating static pages (40/40)` + `out/` 落盘。
另：`tsconfig.json` 的 sha256 构建前后**完全一致**（`732a19e6…c187`），未被 Next 重排。

**gitleaks 的替代（弱证据，只作补位）**：对 tracked 文件扫 4 类密钥模式
（`sk-` / `AIza` / `ghp_` / `AKIA`）⇒ **零匹配**；`.env` 未被跟踪，`.gitignore:5-6` 覆盖 `.env` / `.env.*`。
这**不能**替代 gitleaks 的全历史扫描，只是「没有明面漏网」。

## 53.4 🔴 唯一新增红：`braces` 无补丁的 high advisory

```
braces  <= 3.0.3        Severity: high   CVE-2026-93687
Patched versions: None
fixAvailable: eslint-config-next@14.2.35 (isSemVerMajor: true)
```

依赖链（**实测**读 `node_modules/*/package.json`，不是猜的）：

```
eslint-config-next 16.3.6  →  @next/eslint-plugin-next 16.3.6  →  fast-glob 3.3.1
                                                             →  micromatch 4.0.8  →  braces 3.0.3
```

三点必须写清，免得被误判严重度：

1. **全在 `devDependencies`**（`eslint-config-next` 是 devDep）⇒ 受影响的是 **lint/构建工具链**，
   **不是运行时**产物。攻击面要求攻击者能给你喂一个深度嵌套的 brace 模式去 glob。
2. **没有修复版**：`npm view braces version` = `3.0.3`，advisory 的 `patched` 为空。
   `npm audit` 给出的「修复」是**把 `eslint-config-next` 降到 14.2.35**（semver-major，等于把 Next 16 的
   lint 配置退回 Next 14）——这不是修复，是换一个不依赖该链的旧版本。
3. **与本地两个未推送提交无关**：`git diff --name-only monkeydjl/main..main` 里**不含任何锁文件**
   （14 个文件：11 个 backend + 3 个 frontend，均为源码/测试）⇒ 远端 `6fdb280` 的 `package-lock.json`
   与本机**逐字节相同**，CI 一跑就红，与「推没推」无关。

**处置选项（本节不代决）**：等待上游 `braces` 出补丁 / 给 `npm audit` 加 `--omit=dev`（会削弱这道门，
须显式拍板）/ 在 `package.json` 里加 `overrides` 指向将来的修复版 / 记录豁免。四条各有代价。

## 53.5 junit `tests` 属性的 1089 缺口

**事实（三层互证）**：

| 口径 | 读数 |
|---|---|
| `<testsuite tests="7642">` | **7642** |
| junit 文件里 `<testcase>` 元素数 | **6553** |
| 其中唯一 `(classname, name)` 组合 | **6553**（零重复） |
| 有子元素的 testcase | **11**（全是 `LiveIntegrationTests`，即 live/网络类，与既有约定一致） |
| `pytest --collect-only -q` | **6553 tests collected** |
| pytest 终端汇总 | `6542 passed, 11 skipped` |
| pytest 终端汇总（带探针轮，多出子测试计数） | `6542 passed, 11 skipped, 1 warning, **1089 subtests passed**` |
| `tests/` 里 `subTest(` 调用点 | **179 处**，分布在 **59 个文件** |
| 差额 | **7642 − 6553 = 1089 = 子测试数** |

**可下的结论（不含猜测）**：
- **没有任何测试被静默跳过**：收集 6553 = 执行 6553 = 元素 6553。
- `tests` 属性 = `stats["passed"] + stats["failure"] + stats["skipped"] + stats["error"] − cnt_double_fail_tests`
  （`_pytest/junitxml.py:655-662`），**不是** `<testcase>` 的个数 ⇒ 引它当「测试总数」会系统性高估。
- 差额**跨轮稳定**：上一轮记录 7641 − 6552 = 1089，本轮 7642 − 6553 = 1089 ⇒ 确定性偏移，非漂移。
- **§51.12.1 的「天然差 1」解释的是另一件事**（唯一用例集 6552 vs `passed+skipped` 6553），**没有覆盖这 1089**。

**机制（分层取证；三项假设已被实验排除）**

| 假设 | 实验 | 结果 |
|---|---|---|
| 收集阶段的报告污染了计数 | 带同一探针的 `--collect-only` 全量运行（25 s，零执行） | **stats 全 0** ⇒ 排除 |
| 存在重复 nodeid（junit 按 `(nodeid, worker)` 合并 reporter） | 对 `--collect-only -q` 的全部行做去重统计 | **冗余 0** ⇒ 排除 |
| async 用例被双重投递（本机装了 `pytest-asyncio 1.4.0` + `anyio 4.14.2`；仓库内 **无**任何自定义 `pytest_runtest_logreport` / `pytest_runtest_protocol` 钩子） | 把 async 最密集的 5 个文件（含 17/15/14/12/10 个 `async def test_`）单独带探针跑 | `stats.passed = 109 = 实际通过 109 = reporter 109` ⇒ **排除** |

**全量探针轮的实测计数关系**（`junit-probe.xml`）：

```
PROBE_STATS {'error': 0, 'passed': 7631, 'failure': 0, 'skipped': 11}
PROBE_NODE_REPORTERS 6553
PROBE_TESTCOLLECTED  6553
终端汇总              6542 passed, 11 skipped
```

⇒ `append_pass` 实际被调用 **7631** 次，而 pytest 自己的终端汇总只认 **6542** 条通过、junit 也只写出 **6553** 个
`<testcase>`。即 **7631 − 6542 = 1089** 条 `when == "call" and passed` 的报告被投递给了
`pytest_runtest_logreport`，却**不被终端汇总算作用例**。计数公式自洽：`7631 + 0 + 11 + 0 − 0 = 7642`。

**影响面（这部分是确定的）**：该属性**只影响读数，不影响执行** —— 收集、执行、`<testcase>` 三者都是 6553，
且同一文件里 `failures=0 / errors=0` 与「无 `<failure>`/`<error>` 子元素」互证。
⇒ **把 `tests="7642"` 当「测试总数」会稳定高估 1089**；跨轮比较仍自洽（两轮同为 +1089），
但对外引用（README / 报告）不该用这个数。推荐口径：`passed + skipped` 或 `--collect-only`。

**根因（已定论）：`unittest` 的 `subTest()`。**

```
PROBE2 total_call_passes = 7631    distinct_nodeids = 6542
PROBE2 nodeids_with_multi = 181    extra = 1089
终端汇总  6542 passed, 11 skipped, 1 warning, 1089 subtests passed
```

- **181 个用例方法**调用 `subTest()`（`tests/` 里 **179 处调用点**、分布在 **59 个文件**，循环展开出 **1089 条子测试**）；
- 每条子测试**额外投递一次** `when == "call" and passed` 的报告（nodeid 与父方法相同）；
- junitxml 对每条报告跑一次 `append_pass` ⇒ `stats["passed"] = 6542 + 1089 = 7631`；
- 但 `node_reporter` **按 nodeid 去重** ⇒ 一个方法仍只写出**一个** `<testcase>`（6553 个元素）；
- 终端汇总**把子测试单列** ⇒ 只说 `6542 passed`，另报 `1089 subtests passed`。

⇒ `tests="7642"` = **6542 通过 + 1089 子测试 + 11 跳过**，与公式精确吻合（`7631 + 0 + 11 + 0 − 0`）。
**这不是 pytest 的缺陷，也不是用例漂移** —— 是「junit 的 `tests` 把子测试算作用例、而终端汇总不算」的**口径差**。

📌 **可复用判据**：本仓 junit 的 `tests` 属性 ≈ 「通过数 + 子测试数」。**引用总数时用 `passed + skipped`（= 6553 = 收集数）**，
或直接引 `--collect-only`。跨轮比较仍自洽（两轮都是 +1089），但把它当用例总数会稳定高估。

## 53.6 已核实为「非缺陷」的四件事（写下来，免得下一个人再查）

1. **`backend/` 没有 pytest 配置文件**（无 `pytest.ini` / `pyproject.toml` / `setup.cfg`，`inifile: None`）
   —— **有意为之**：`tests/conftest.py:134-135` 以编程方式设 `config.option.asyncio_mode = "auto"`，
   套件里 51 个文件含 `async def test_`。不是遗漏。
2. **`loop_runs` 里 127 条 `running` 未回收** —— **不是缺陷，是预期残留**。
   回收器 `loop_run_store.fail_stale_running_rows()` 由 `scheduler.py:471` 的
   `loop_run_ledger_maintenance`（`CronTrigger(hour=6, minute=48)`，UTC）调用；该函数是 **2026-09-24（`6b820e7`）**
   才引入的，而这 127 条**全部早于它**（`event_discover` 120 条 06-29→07-23、`event_discover_startup` 4 条 09-10、
   `translate_titles` 2、`event_auto_resolve` 1）。本机调度器是**临时启动**的（按日运行量从 6 月每天上百条降到
   9 月只有 3–44 条），自 09-24 后**从未在 06:48 UTC 存活**过 ⇒ 任务没机会跑。
   注意账本里 `loop_run_ledger_maintenance` 的记录数 = **0**（那 8 条 `loop_db_maintenance` 是**另一个**任务，
   且它们的实际启动时刻是 :45@13/22 时，与当前 `hour=6` 的注册不符 ⇒ cron 历史变更过）。
   ⚠️ §8 记的「4 条孤立 `running`」与 120 条**不同源**（4 = `event_discover_startup`），别把 127 读成回归。
3. **`.env.staging.example` 里的 `=false` 赋值** —— 属 §六/C 已拍板的既决事项（staging 故意全关 + 加
   `overlay_flags` 让「0」消歧），**不是新缺陷**。
4. **§52.8.2 的「前端 `TUNABLE_ENGINES` 未加 `gbm`」已不成立**：实测
   `frontend/src/components/sports/world-cup/engine-console.tsx:34` 有 `{ value: "gbm", label: "GBM" }`，
   `engine-api.ts:51` 的 `TunableEngine` 也已含 `gbm` —— 在 **`93d4c38`（未推送）**里做掉了。
   仅 `world_cup_dynamic_weights.py:39` 的 `e.g.` 举例仍未列 `gbm`（举例形式，无害）。

## 53.7 数据面（只读，为免误读单列）

四个运行库**都是本机工作产物，且全线陈旧** ⇒ 本机后端/调度器**现在没在跑**，这些 0 **不是健康空集**：

| 库 | mtime | 最新数据 | 关键读数 |
|---|---|---|---|
| `v2_loop.db` | 2026-09-27 | `loop_runs` 最后启动 09-26 | `success 1629 / running 127 / failed 3`；`predictions` 171（max 07-25）；`simulated_trades` 80（max 07-24）；`review_queue_items` **0**；`decision_timeline` **0**；`llm_daily_spend` **0** |
| `kernel_predictions.db` | 2026-08-28 | `kernel_prediction_history` 1224（max 08-25） | `kernel_market_*` / `kernel_calibration` / `kernel_sport_edges` 全 **0** |
| `world_cup_predictions.db` | 2026-07-31 | `match_results` 104（max 07-30） | `match_predictions` 58（max 07-18）；`prediction_accuracy` 5，max(updated_at)=**None** |
| `domain_reliability.db` | 2026-07-31 | — | `domain_reliability` 2 行 |

📌 **按 §5 的规矩**：上表的每一个 0 都只表示「**生产者没开**」，不表示「真的没有」。任何据此下结论的读数
都必须先确认服务在跑。

## 53.8 本轮交付状态与未决

| 事项 | 状态 |
|---|---|
| 本轮是否改动了产品代码 | **零改动**（只读了代码/数据，未修改任何 tracked 文件） |
| 本轮写入的非代码文件 | `MEMORY.md` §1 的过期条目更正（`.workbuddy/**`，**永不入库**）；审计日志在 `.workbuddy/audit-2026-10-03/` |
| 本节文档 | **未提交** |
| 🔴 `braces` high advisory | **未决（需拍板）**，四条处置路径见 53.4 |
| 本地 2 个未推送提交（`c13e6bd`、`93d4c38`） | **未推送**（用户未授权） |
| docker-build / gitleaks 两个 CI job | **本机未复现**（daemon 未运行 / 未安装），非「通过」 |

## 53.9 A 项处置：`braces` 那道门（2026-10-03，改 CI 1 处）

### 53.9.1 先证伪两条「修复」路径（免得把它们当备选）

| 路径 | 取证 | 结论 |
|---|---|---|
| `overrides` 强制升 `braces` | `npm view braces dist-tags` → **只有 `latest` 一个 tag，值 = `3.0.3`**（发布于 **2024-05-21**）；advisory 的 `Patched versions: **None**` | **今天做不到** |
| 绕开依赖链 | `@next/eslint-plugin-next@latest`（**16.3.8**）仍**钉死 `fast-glob: 3.3.1`**；`fast-glob@latest`（3.3.3）→ `micromatch ^4.0.8`；`micromatch@latest`（4.0.8）→ `braces ^3.0.3` | **每一环都已最新且为硬依赖** |
| 等上游补丁 | `micromatch/braces#70` **就是这份 advisory 本身**（不是已合并的修复 PR）：无 `maxDepth` 实现、无发布时间表 | **无时间表** |

### 53.9.2 再确定影响面（这是定「门范围」的依据）

- `npm audit --omit=dev --audit-level=high` → **exit 0 / `found 0 vulnerabilities`**；
- `npm ls braces --omit=dev` → **`(empty)`**。

⇒ 这条 advisory **完全不在生产依赖树里**，**shipped bundle 零影响**；它是 lint/构建工具链的输入侧问题。

### 53.9.3 改动（`.github/workflows/ci.yml`，**+13 / −1**）

形态校验：`git diff --numstat` = `13 1`（净增 12 行 = CRLF 增量 12）；改后 `bare LF = 0`；
PyYAML 可解析，`frontend-tests` 步骤数 **7 → 9**，其后各步顺序未动。

```yaml
      - name: Audit frontend dependencies (production)
        run: npm audit --omit=dev --audit-level=high
      - name: Audit frontend dev dependencies (report only)
        run: npm audit --audit-level=high
        continue-on-error: true
```

**取舍原则**：**生产依赖继续阻断**（那才是会上线的东西），**dev 树保留可见但不再阻断** ——
既不静默丢掉信号，也不再让一个「无可用修复」的 dev advisory 把每个 PR 变红。**不引入新依赖。**

**本机复跑读数**：阻断门 **exit 0**；报告门 **exit 1**（由 `continue-on-error` 兜住）。

### 53.9.4 未做 / 未决

- **未提交、未 push**。
- 若更希望**保住 dev 树的阻断性**，正解是引入 `audit-ci` 之类**带 allowlist 的包装器**，按 GHSA 显式豁免这一条
  —— 代价是新增一个 devDependency + 一条 CI 步骤。本节取的是「不新增依赖」这条路；
  **两条路都成立，取值需拍板**（本节不代决）。
- `braces` 出补丁后应撤掉 `--omit=dev`、恢复单条全树门；该 advisory 的 ID 与原因已写进 YAML 注释，便于回溯。
- `--omit=dev` 之后，**dev 树的高危不再有任何阻断机制**，只有日志可见 —— 这是本取舍的已知代价，写在这里而非埋着。

## 53.10 D 项处置：把 dev 树的阻断性找回来（`audit-ci` + **路径限定** allowlist）

> ⚠️ **本节取代 §53.9 的取舍。** §53.9 走的是「用 `--omit=dev` **缩窄门范围**」，代价是 dev 树彻底失去阻断；
> 本节改为「**保住全树阻断 + 显式豁免一条**」，并把 ci.yml 的两条门**合并回一条**。
> 按「只追加不改写」的约定，**§53.9 原文保留**作过程记录 —— 但它描述的 ci.yml 状态**已被本节覆盖**。

### 53.10.1 为什么能「既阻断又放行」

- `audit-ci` 的 allowlist 按 **GHSA 标识符**豁免（v6.0.0 起由 NPM 数字 ID 改为 GitHub ID，理由是数字 ID 不稳定）；
- 它**默认就审计 `devDependencies`**（与 `npm audit` 默认一致）⇒ 不需要 `--skip-dev`，**全树覆盖不变**；
- 阈值字段 `"high": true`，与原 `--audit-level=high` **同口径**。

### 53.10.2 改动清单

| 文件 | 改动 | 形态 |
|---|---|---|
| `frontend/package.json` | devDependencies **+1 行** `"audit-ci": "^7.1.0"` | `+1 / −0` |
| `frontend/package-lock.json` | 包数 **764 → 795** | `+370 / −1`；唯一删除是某条目的 `"peer": true` 元数据；**无任何既有包版本变动** |
| `frontend/audit-ci.jsonc` | **新增**（配置 + 长注释：为何豁免、何时该删） | 新文件 |
| `.github/workflows/ci.yml` | 两条门合并为**一条阻断门**，改跑 `npx audit-ci --config audit-ci.jsonc` | `+17 / −1` |

**先验证过一件事**：装完 `audit-ci` 后 `npm audit --json` 仍是**同样的 5 条 high**（全在 braces 那一族链上），
`{info:0, low:0, moderate:0, high:5, critical:0}` ⇒ **新工具自己没带进新告警**。

### 53.10.3 关键设计：豁免**限定在链上**，不是无差别放行

`audit-ci` 的失败报文会给出**带路径的标识符**：

```
GHSA-vfj7-8cjw-p6xm|eslint-config-next>@next/eslint-plugin-next>fast-glob>micromatch>braces
```

所以配置里用的是：

```jsonc
"GHSA-vfj7-8cjw-p6xm|eslint-config-next>*"
```

含义：**只在 `eslint-config-next` 这条链上豁免**。相对裸 `"GHSA-vfj7-8cjw-p6xm"` 的好处是：
**若将来别的依赖也把 `braces` 拉进来，门依然会响**（裸 GHSA 会把那种情况一并静默掉 —— 那正是本项目最反感的假绿）。

### 53.10.4 红/绿对照（三次运行，**唯一变量是 allowlist**）

| # | allowlist 内容 | 结果 | 说明 |
|---|---|---|---|
| 1 | `[]` | **exit 1** · `Failed security audit due to high vulnerabilities` | **门是真的会红** |
| 2 | `GHSA-…\|some-other-dependency>*` | **exit 1** | **路径限定不是装饰**：不匹配就不放行 |
| 3 | `GHSA-…\|eslint-config-next>*` | **exit 0** · `Passed npm security audit` | 豁免生效 |

配套复核：按 CI 里的**原样**（含多行 shell 与三条 `echo`）复跑该步骤 ⇒ **exit 0**；
`npm ci --dry-run` ⇒ **exit 0**（锁文件与新 devDep 同步）；
加依赖后 `tsc --noEmit` 零输出、`eslint` 零输出。

### 53.10.5 一个必须写明的行为差异（不写会误导后来人）

**`audit-ci` 对「路径限定」形式的豁免，不在日志里公告。** 实测两份日志 `diff` **只差一行**：

```
< Found vulnerable allowlisted advisories: GHSA-vfj7-8cjw-p6xm.     ← 只有裸 GHSA 形式会打
```

即：路径形式下 CI 会**绿着且不提**这条例外。为了让例外**每轮可见**，ci.yml 的该步骤里加了三条 `echo`
（点名 GHSA、路径、以及「无补丁 / 纯 dev」三个事实），并声明 `audit-ci.jsonc` 是唯一真相源、echo 只是指针。
**这是刻意的取舍，不是遗漏**：优先「结构上更窄」，再用 echo 把「日志可见」补回来。

### 53.10.6 何时该删掉这条豁免

npm `braces` **> 3.0.3** 发布之后（跟踪 `micromatch/braces#70`）：删掉 `audit-ci.jsonc` 里那条 entry，
**门会自己恢复阻断**（对照 53.10.4 的 #1 已证）。该条件同时写在三处：配置文件注释与 `notes`、ci.yml 的 echo、本节。

### 53.10.7 遗留（写明，不埋）

- `audit-ci` 自身把 **31 个包**（`yargs` / `cross-spawn` / `semver` / `event-stream` / `readline-transform` 等）
  带进 devDependencies ⇒ 这是一次**供应链面扩大**。已核实它**没带来新的 high**，但 review 时应知情。
- **未提交、未 push。**

## 53.11 E 项深查：Sports 市场桥接（Phase 7–8）——三种「0」、一条真该修的东西，和一个不可恢复的问号

本节回答 E 项选定的问题：「kernel_market_* / kernel_sport_edges / kernel_calibration 全表 0 行，但代码完整 —— 到底是旗标默认关，还是真的不可达（典型的『被定义但没人产』）？」

查完的答案是：**这里混着三件性质完全不同的事，不能用一个"0"说完**；其中第三件暴露了一个独立且确实该修的东西；而第三件的**根因已经被另一件东西销毁了**。

### 53.11.1 结论先行

`v2_loop.db.loop_runs`（真实账本，非测试副本）里 Phase 7/8/12 相关 job 的**调用史**，与 `kernel_predictions.db` 里的**物证**并排：

| job | ledger 行数 | 最后调用 | 目标表的行数 | 判定 |
|---|---|---|---|---|
| `sport_market_discover` | 30 | 2026-07-31 | `kernel_sport_market_links` **0** | 跑过，**产出 0**（自述也是 0） |
| `sport_market_odds_fetch` | **0** | — | `kernel_traditional_odds_snapshots` **0** | **从未调用** |
| `sport_market_snapshots` | **0** | — | `kernel_market_snapshots` **0** | **从未调用** |
| `sport_edge_detect` | **0** | — | `kernel_sport_edges` **0** | **从未调用** |
| `market_settlement_feedback` | **0** | — | `kernel_market_settlements` **0** | **从未调用** |
| `futures_market_discover` | **0** | — | `kernel_futures_links` **0** | **从未调用** |
| `futures_snapshots_capture` | 13 | 2026-07-31 | `kernel_futures_snapshots` **0** | 跑过，**自述写了东西，物证不在** |

外加 `kernel_market_calibrations` 0、`kernel_calibration` 0。

### 53.11.2 方法论收获：三种「0」必须分开判

这一节最重要的产出不是某张表为空，而是**「表是空的」有三种成因，判据完全不同**：

- **(a) 生产者没开 / 没注册** —— ledger 里连一行都没有。**证据是"账本里没有这个 job"**，不是"表是空的"。
- **(b) 生产者跑了，产出就是 0** —— ledger 有行，且**自述的产出也是 0**。这是可接受的 0（真的没有候选），**前提是自述里带了"输入侧"的读数**（这里是 `kalshi_candidates`/`polymarket_candidates`）。如果没有这两个键，这个 0 就退化成 §5 说的歧义 0。
- **(c) 生产者跑了，自述写了东西，物证不在** —— **这才是缺陷信号**。判据：**自述的产出不可能在没有物证的情况下成立**（下节）。

把 (a)(b)(c) 混成一个「全表 0 行」，会把 (c) 藏进 (a) 里 —— 本仓恰好就是这么藏的。

### 53.11.3 旗标姿态：production / staging 模板里**根本没有**这三个开关

```
backend/.env.example              PHASE7=12  PHASE8=1  PHASE12=1   （全部 =false）
backend/.env.production.example   PHASE7=0   PHASE8=0  PHASE12=0
backend/.env.staging.example      PHASE7=0   PHASE8=0  PHASE12=0
```

`config.py:1834` `PHASE12_FUTURES_MARKETS_ENABLED = _env_bool(..., "false")`；`PHASE7_*`（`:1709-1772`）与 `PHASE8_CALIBRATION_FUSION_ENABLED`（`:1795`）同为 `_env_bool`。**模板不写 ⇒ 取代码默认 ⇒ 全 false。**

按 §5 的判据，这意味着上表里所有 (a) 类的 0 **是"开关关着"，不是"真的没有"**；而且和先例（`/quality-metrics/summary` 回 `overlay_flags`、`/anomalies` 回 `alerts_enabled`）不同 —— **这些表旁边没有任何地方写出开关名**。所以任何一个只看 DB 的人，看到的是四个无法区分的 0。**这里不判缺陷**（§5 的边界：有界面先例才消歧；且 prod 关掉 Phase 7/8 是有意姿态），但**读这些表的人必须知道自己在读什么**。

### 53.11.4 (c) 类：`futures_snapshots_capture` 的 13 次 `{captured: 3}` 与任何现存物证都对不上

13 行的原始 payload **完全一致**，逐行读出、未经转述：

```
2026-07-16T14:31:35 / 14:31:52 / 14:32:55 / 14:33:38 / 14:38:26 / 14:51:08
            14:51:26 / 15:19:25 / 15:21:00 / 15:23:56      → result={'captured': 3, 'errors': 0}
2026-07-31T04:01:07 / 04:18:30 / 14:13:13                  → result={'captured': 3, 'errors': 0}
```

**为什么 `{captured: 3, errors: 0}` 是一条强断言**（`futures_market_service.py`，该文件自 2026-07-16 起只有一个提交 `5828b55`，语义未变）：

- `:116-118` 无 verified link 时**直接**返回 `{"captured": 0, "errors": 0}` ⇒ `captured > 0` 蕴含「运行时至少存在 3 条 verified link」；
- `:144-152` **先 `append_snapshot()` 成功、再 `captured += 1`** ⇒ `captured == 3` 蕴含「3 次 append 成功」；
- `:123-125` 拉取失败会返回 `errors=len(verified)` ⇒ `errors == 0` 排除「拉取失败」这一分支。

也就是说：**39 条快照 + ≥3 条 link，当时必定写进了某个库**。而现在：

1. `settings.KERNEL_DB_FILE` = `backend/kernel_predictions.db`，其 `kernel_futures_links` 与 `kernel_futures_snapshots` **均为 0 行**（该库 19 张表都在，DDL 存在 ⇒ 建过表、只是没行）。
2. **E10 前后的路径是同一个文件**：pre-E10 的 `init_kernel_db()` 硬编码 `Path(__file__).resolve().parents[2] / "kernel_predictions.db"`（`56b9665^`），等于 `backend/kernel_predictions.db`；E10（`56b9665`，2026-08-26）把它变成同名 setting，默认值就是这条路径。**不存在"路径改了所以写别处"的解释。**
3. **文件没被换过**：Windows 创建时间 `2026-07-14 21:18:01`，恰好等于库内最早行的时间戳（`kernel_prediction_history.created_at` min `2026-07-14 13:18:01` = UTC+8 的 21:18:01）。运行日（07-16 / 07-31）**在文件生命周期内**。
4. **没有任何代码删过它们**：全 `app/` 的 `DROP TABLE` 只有三处（`kernel_db.py:551-553`，`kernel_factors` / `kernel_engine_scores` / `kernel_prediction_history`），且受「老 PK」条件保护 —— 当前库里这三张表分别是 43 / 2 / 1224 行，**条件未触发**。`drop_all`、`DELETE FROM kernel_futures*` 均不存在。
5. **也不在测试库**：`%TEMP%` 下 159 个 `pmrf_test_data_*` 目录、其中 12 个有 `kernel_predictions.db`，**逐一扫过，futures 两表全 0**。
6. **更矛盾的是它的上游没跑过**：`futures_market_discover` 是 `kernel_futures_links` 的唯一生产者，它的 ledger 行数是 **0**。而 `capture` 要读到 3 条 verified link 才可能返回 3。

**这六条不能同时为真。** 最可能的解释（**领先假设，未证实**）：这 13 次是**手工验证会话**，进程里 `KERNEL_DB_FILE` 被指到了一个临时/草稿库 —— 于是账本（走 `LOOP_DB_FILE`，默认即真库）留下 13 行 `success`，而快照写进了那个草稿库，事后被删。这个假设有现成的机制可循：`init_kernel_db()` 在 `kernel_db.py:493` **`if _engine is not None: return`** —— **进程内第一次用哪个路径，整个进程就都用哪个**，后续任何无参调用都会静默写向那个路径（这正是 E10 要解决的同一族问题的手工版）。

**但必须写明：这条路无法证实。** 本该定案的那条证据是 `init_kernel_db()` 自己打的 `logger.info("Kernel DB initialized at %s", db_path)`（`kernel_db.py:521`）—— 而 `backend/logs/` 只剩 3 个文件（`app.log` 09-12..10-03、`app.log.1` 09-06..09-12、`app.log.5` 09-02..09-06），07-16 与 07-31 的启动行**已被轮转删除**。**这条线索是被下一节那个发现销毁的。**

### 53.11.5 附带发现（本节唯一一条"该修"）：测试在往生产日志里写，并且它把审计线索一起写掉了

`runtime_stores.py:70-71` 把 `LOG_FILE` 归入 **EPHEMERAL_STORES**，理由写的是 *"log output; the app never reads it back"* —— **对程序成立，对操作员不成立**：

- `config.py:309` `LOG_FILE` 是 setting；`setup_logging()`（`logging*.py:128-131`）用它建 `RotatingFileHandler`；
- **`tests/conftest.py`（414 行）从头到尾没有重定向 `LOG_FILE`** —— 它重定向了 9 个数据路径，唯独漏了日志。

后果，在 `backend/logs/app.log` 上直接量得到（2026-10-03）：

- 当日 **10357** 行；按小时分布 `00→3867, 19→1261, 20→875, 21→724, 22→3630` —— 这是**测试跑**的形状，不是服务器的形状；
- 当日 `SQLite integrity failed at startup for: ...` / `SQLite store maintenance could not run` **共 70 条**（`00→28, 19→2, 20→12, 22→28`）；
- **闭环**：本次审计自己在 2026-10-03 跑全量套件（19:42:47 → 20:05:58，`junit-full.xml` mtime 20:05:57），**其中落在 19 时的 2 条 + 20 时的 12 条 = 14 条，就是我那一次跑出来的。**

而且它报的是**假警报**。同一秒级序列连着三个"启动"，只能是三套 settings 夹具：

```
22:33:59  Loop DB maintenance passed: {'ok': True} → SQLite maintenance passed for 0 store(s)
22:34:02  Loop DB maintenance passed: {'ok': True} → ERROR - SQLite store maintenance could not run at startup
22:34:05  Loop DB maintenance passed: {'ok': True} → ERROR - SQLite integrity failed at startup for: KERNEL_DB_FILE
```

- `passed for 0 store(s)` 要求 `sqlite_state_paths()` 为空 —— 只有被改过设置时才可能；
- 而**真实的那个库是好的**：`backend/kernel_predictions.db` 上 `PRAGMA integrity_check` → **`ok`**，`quick_check` → `ok`，`foreign_key_check` → 0 行。按 `sqlite_db.maintain()` 的三步（WAL 打开 → `wal_checkpoint(TRUNCATE)` → `integrity_check`）原样复现，**全部通过**。

所以日志里那句 *"Restore these stores from a backup."* 是**测试造出来的、指向一个健康库的、叫人去恢复备份的**指令。任何照它执行的人都会做一次不必要（且有风险）的恢复。**这是本节唯一建议动手修的一处**（最小修法：conftest 把 `LOG_FILE` 一并重定向；或者让 `setup_logging()` 尊重测试环境）。**本节不改代码，只登记。**

两个发现是这样咬合的：**日志被测试写满 ⇒ 轮转变快 ⇒ 7 月的启动行被挤掉 ⇒ (c) 类的线索不可恢复。** 一个"只是测试污染日志"的小毛病，代价是另一处问题永久失去判据。

### 53.11.6 一个可复用的读法：ledger 的 payload 形状会过期

`sport_market_discover` 的 30 行，**只带两个键** `{kalshi_candidates, polymarket_candidates}`。而当前代码（`scheduler.py:942-948`）发的是**五个键**，且带着一句注释：*"Record link outcomes, not just candidate counts: a run that fetched 100 candidates and linked none is not a healthy run, and the ledger should say so."*

⇒ **这 30 行记录的是"改好之前"的形状**。也就是说：**账本自身的诊断能力是在这些运行之后才长出来的**，拿今天的预期去读老 ledger 是苹果比橘子；反过来，**"当时没记"不能读成"当时没发生"**。这条对任何跨期对读 ledger 的人都成立。

### 53.11.7 未决 / 明确不做

- **(c) 类根因**：领先假设是手工会话把内核库指向了草稿路径（机制在 `kernel_db.py:493` 的 early-return），**未证实，且已不可证实**（证据被轮转删除）。**不建议为了"查清"去做任何数据动作** —— 39 行快照已不可寻回。
- **`sport_market_discover` 的 0 是否"应该"是 0**：`kalshi_candidates` 恒为 0、`polymarket_candidates` 恒为 0，只出现过 1 次 `kalshi_candidates: 1`。这**符合**"发现源需要 key 而当时没配"的读法，但**我没有配 key 去复现**，所以只能说"自述自洽"，不能说"已排除缺陷"。
- **`sport_market_discover` 的三联行**（30 行 = 10 组、组内相隔 1 秒）：形状是harness/手工的，**不是 cron 的**；但"谁在跑"我没有定论，**只把它记为形状异常**。
- **不做**：不改任何旗标默认、不改 conftest、不动数据库、不提交。**所有动作留给拍板。**

## 53.12 A 项处置：把 `LOG_FILE` 纳入 conftest 的重定向（4 处赋值 + 1 行文档）

§53.11.5 登记了「测试在写生产日志」这条缺陷，并写明**当时未改代码**。本节是它的处置，**不是改写**。
（§0 的规矩：§53.11.5 里「未做修法」这个结论的**前提是"未动代码"**，该前提已被本节取代；它其余的取证部分——假警报、两个发现的咬合——**仍然成立**。）

### 53.12.1 改动清单（唯一一个文件）

`backend/tests/conftest.py`，**4 处赋值 + 1 行 docstring**：

| 位置 | 内容 | 作用 |
|---|---|---|
| `:87` | `_SettingsClass.LOG_FILE = os.path.join(_TEST_DATA_DIR, "app.log")` | **类级**默认，供**尚未实例化**的读取方 |
| `:103` | `_settings_instance.LOG_FILE = _SettingsClass.LOG_FILE` | 已在用的**实例**（`setup_logging` 读的就是它） |
| `:138` | `cls.LOG_FILE = ...`（在 `_restore_config_module()` 内） | `test_config.py` 用 `importlib.reload` 重定义类之后**重新压回去** |
| `:149` | `_ORIGINAL_SETTINGS.LOG_FILE = cls.LOG_FILE` | 同上，恢复实例 |
| `:5-7` | docstring：`(event store, databases, caches)` → `(…, the log file)`，`reads` → `reads or writes` | 承诺原文只说了"读"，实际泄漏的是"**写**" |

行数（`git diff --numstat backend/tests/conftest.py`）：**26 / 2**。

### 53.12.2 为什么是这 4 处，且为什么必须**在 `import app.main` 之前**

不是"设了就生效"。绑定点只有一个：

- `app/main.py:29` 在**模块级**调用 `setup_logging()`；
- `setup_logging()`（`logging.py:108-138`）读**一次** `settings.LOG_FILE` 建 root `RotatingFileHandler`；
- 函数体 `:117-118` 有 `if root.handlers: return` ⇒ **装上一次就再也不重绑**。

conftest 在 `:113` 才 `import app.main`，而类级/实例级赋值在 `:87`/`:103` ⇒ **顺序是对的**（`grep -n` 实测行号）。这也是为什么修在 conftest 有效、而"事后 patch `settings.LOG_FILE`"无效 —— 后者对已经装好的 handler 毫无影响。

### 53.12.3 红 → 绿（成对取证，避免「空真」）

探针：`tests/test_production_preflight.py -k api_lifespan_boots_a_correctly_configured_production`（**不带 `assertLogs`** 的 lifespan 测试）。

| | 生产 `backend/logs/app.log` | `_TEST_DATA_DIR/app.log` |
|---|---|---|
| **RED**（改前） | **+1673 字节**，首行 `2026-10-04 00:28:27,362 - app.main - INFO - EIP v0.3.0 starting`；junit `tests=1 failures=0 errors=0` | 不存在 |
| **GREEN**（改后） | **+0 字节，sha256 不变**（`3ed0137c7bb483d3`）；junit `tests=1 failures=0 errors=0` | **1673 字节**（`pmrf_test_data_lopysdbc/app.log`） |

**同一个 1673。** 这一对是必要的：只报「生产日志没变」是**空真**判据 —— handler 完全可能压根没被装上。
必须同时证明**同一份字节出现在别处**，才叫"它搬走了"而不是"它没了"。

### 53.12.4 一个会让验证变假绿的探针陷阱（写明，供复用）

一开始选的探针是 `tests/test_alert_channel_posture.py`（它也 `with TestClient(app)`）。**不能选**：
它用 `self.assertLogs("app.main", level="INFO")`，而 `assertLogs` 会把该 logger 的 handler **换成捕获器**并置 `propagate=False` ⇒ 记录**根本不流向 root** ⇒ 拿它当探针会在改动前也测不到泄漏（假绿）。
选探针前先 `grep -c assertLogs <file>` 排掉。

### 53.12.5 回归与静态门

| 门 | 读数 | 对比修前 |
|---|---|---|
| 单测探针 | `tests=1 failures=0 errors=0 skipped=0` | 同 |
| 8 文件子集（preflight / operational_readiness / alert_posture / log_level / **production_db_hermeticity** / main_frontend_mount / deploy_consistency / singleton_reset_census） | `tests=230 failures=0 errors=0 skipped=0`，且生产日志 **+0** | 新增口径 |
| **全量套件** | `tests=7642 failures=0 errors=0 skipped=11 time=1282.044`（21m34s） | **与修前逐项相同**（7642/0/0/11） |
| 全量套件对生产日志的影响 | **+0 字节，sha256 不变，未轮转** | 修前同规模约 6k–10k 行/天 |
| `ruff check app/` | All checks passed | 同 |
| `ruff check tests/conftest.py` | 1 条 F401 —— 用 `--stdin-filename` 对 **HEAD 原文**跑同一条，**同样 1 条** ⇒ 本次改动**零新增 lint** | 同（既有项：`import app.core.config as _config_module` 未使用，**未动**） |
| `mypy app/` / `generate_types --check` | **不可能受影响**：`git diff --name-only` 里**没有** `backend/app/**`（实测） | — |

### 53.12.6 先备份再验证：这次「验证」本来会销毁证据

目标日志 `LOG_MAX_BYTES=10485760`，跑全量前 `app.log` 已 **9,620,076** ⇒ **余量仅 865,684**。
而修前的全量套件每天写 6k–10k 行 ⇒ **足以触发轮转，把最老的 `app.log.5`（09-02..09-06）挤掉** ——
而 §53.11.4 缺的正是被轮转删掉的那类历史证据。

所以先 `cp -p` 三份日志到 `.workbuddy/audit-2026-10-03/logs-backup/`（仓库外），事后逐一核对：

```
app.log    9620076   (现场 == 备份，mtime 均 Oct 4 00:28)
app.log.1  10485736  (现场 == 备份，mtime 均 Sep 12 19:10)
app.log.5  10482400  (现场 == 备份，mtime 均 Sep  6 09:49)
```

**未轮转**，三份历史证据完好。（顺带：修好之后这个风险本身也消失了 —— 全量套件不再写生产日志。）

### 53.12.7 未做 / 建议下一步

- **未加守卫用例**。本轮范围只到修复，没有把「测试不得写生产日志」钉成测试。建议补
  `tests/test_production_log_hermeticity.py`，形制照 `test_production_db_hermeticity.py`（E10 的先例），
  断言**两半都要有**：写一条带随机 marker 的日志记录 → 断言该 marker **不在** `backend/logs/app.log` 里，
  **同时**断言它**确实**出现在某个 file handler 的目标里。只有前半条的话，将来 `setup_logging()` 因 root 已有 handler 而早退，测试会在"没有任何 file handler"时**空真通过**。
- **未提交、未推送**；`frontend/audit-ci.jsonc` 等 10-03 的改动仍原样保留。

## 53.13 把「测试不得写生产日志」钉成用例：3 条，以及它顺手抓到的一枚诱饵 handler

§53.12.7 把「测试不得写生产日志」列为建议并明确**未做**。本轮照做：新增
`backend/tests/test_production_log_hermeticity.py`（3 条 / 223 行 / 纯 CRLF / **零 ruff 错误**）。

形制照 E10 的先例（`test_production_db_hermeticity.py`）：**生产路径从仓库布局推导**
（`config.py` 的 `parents[2]` = `backend/`，故 `_PRODUCTION_LOG_DIR = backend/logs`），**不读 `settings`**。
理由与 E10 逐字相同：读 `settings.LOG_FILE` 再跟 handler 比，等于**拿沙箱比沙箱**，无论代码怎么改都通过。

写这条用例的过程抓到一件计划外的事（§53.13.3），而且**我的第一版判据是错的** —— 它会在修复之后也报红。

### 53.13.1 三条用例，各管一件事

| 用例 | 断言 | 它红说明什么 |
|---|---|---|
| `test_the_setting_points_inside_the_test_temp_dir` | `resolve(settings.LOG_FILE)` 落在 `conftest._TEST_DATA_DIR` 内 | conftest 的重定向**没写到** `LOG_FILE` |
| `test_no_root_file_handler_writes_inside_the_checkout` | root 的 file handler 目标里（a）**没有一个**在仓库根内，（b）**至少有一个**在沙箱内 | 覆盖被挪到 `import app.main` **之后**：设置读起来是对的，但 handler 在 import 期就绑到仓库了，而**没人重绑** |
| `test_a_record_logged_by_a_test_reaches_the_sandbox_and_not_the_repo` | 发一条带随机 marker 的记录 →（a）marker **读得回来**（在 handler 实际持有的文件里），（b）`backend/logs/` 的逐文件摘要**不变** | 有记录真写进了仓库里的日志 |

### 53.13.2 红 → 绿（同一个产物，两种状态）

RED 的装法是**把工作树 conftest 换成 `git show HEAD:` 的原文**（先 `cp -p` 备份修复版，事后还原并核对 sha256）：

| 状态 | conftest | junit | `backend/logs/app.log` | 沙箱 `app.log` |
|---|---|---|---|---|
| **RED** | `HEAD`（无 `LOG_FILE` 重定向；`git diff --numstat` 对 conftest **为空**） | `tests=3 failures=3 errors=0 skipped=0` | **+366 B**（两次红跑合计） | — |
| **GREEN** | 工作树（4 处赋值 + 1 行文档，`26 / 2`） | `tests=3 failures=0 errors=0 skipped=0` | **+0 B，sha256 不变** | **122 B，含 1 行 marker** |

RED 的三条失败信息都指到了正确的东西，例如第二条：
`a root file handler writes inside the checkout: ['D:\...\backend\logs\app.log'] (the production log dir is D:\...\backend\logs)`。

正向对照（第三条的后半）**不是**拿 `settings.LOG_FILE` 比，而是把 marker 从 **handler 实际持有的文件**里读回来，
理由同上：拿设置跟设置比什么都证明不了。两半合起来才是「记录**搬走了**，不是**消失了**」。

### 53.13.3 🔴 计划外发现：pytest 自己往 root 挂了一枚 devnull「file handler」

第三条用例的判据是「marker 读不回来 ⇒ 后半条（仓库没变）就是空真」。但**「root 上有 file handler」本身也是空真判据**。

临时 `-p` 插件（放仓库外 + 指 `PYTHONPATH`，**不往 `tests/` 塞文件**）在 `pytest_configure` 与
`pytest_sessionstart` 各 dump 一次 root handlers：

| 时点 | root.handlers |
|---|---|
| `pytest_configure` | **2**：`logging.StreamHandler` + `logging.handlers.RotatingFileHandler`（当时指向 `backend/logs/app.log`） |
| `pytest_sessionstart` | **4**：上述 2 + `_pytest.logging._LiveLoggingNullHandler` + **`_pytest.logging._FileHandler`，`baseFilename` 形如 `\\.\nul`** |

即 **pytest 的 logging 插件在 session 开始时挂了一个写 `os.devnull` 的 `FileHandler`**（Windows 上 `os.devnull` 就是 `nul`）。
两个后果：

- **一份什么都没写的跑也满足**「root 上有 file handler」⇒ 该判据空真；
- 我第一版把判据写成「**所有** file handler 目标都在沙箱内」⇒ **GREEN 下也报红**（诱饵在沙箱外）。

后者比前者更值得记：**一条在修复之后仍然红的守卫，会诱使下一个人去改判据**，而改判据正是让守卫失效的标准路径。
修法是把判据拆成（a）无一在仓库内 +（b）至少有一个在沙箱内（§53.13.1 第 2 行）。
另一面还好：诱饵**吞掉一切** ⇒ 永远无法满足「把 marker 读回来」⇒ **正向对照对它天然免疫**。

### 53.13.4 顺带核一遍：`path_setting_names()` 13 个，conftest 只重定向 10 个

`LOG_FILE` 修好之后，「最后一个漏掉的路径设置」这个说法**必须重新验证** —— 不能因为刚修了一个就当作没有了。
实测（`runtime_stores.path_setting_names()` 共 13，conftest 覆盖 10）：

| 未重定向的设置 | 解析到 | 实测 | 今天有没有被测试写 |
|---|---|---|---|
| `EVENT_AUDIT_FILE` | `backend/event_audit.jsonl` | 175,150 B，mtime **2026-09-11 02:39:44** | 否 |
| `SCHEDULER_LOCK_FILE` | `backend/v2_loop.db.scheduler.lock` | 5 B，mtime **2026-09-11 03:50:52** | 否 |
| `LOL_DRY_RUN_FIXTURES_PATH` | `''`（解析为 cwd） | — | 无任何测试引用 |

两条读法：

- `EVENT_AUDIT_FILE` **是 `STATE_STORES` 的 8 个成员之一**（append-only 概率快照审计轨），比 `LOG_FILE` 更像正经数据；
- `SCHEDULER_LOCK_FILE = LOOP_DB_FILE + ".scheduler.lock"` 是在**类定义期**求值的 ⇒ 覆盖 `LOOP_DB_FILE` **不会**移动它。
  这是它在重定向下仍指向仓库的机制 —— 不是「忘了写」，是「写了也无效」。

**判据用 mtime**：两个有默认值的文件都停在 **2026-09-11**，即今天的红跑 / 绿跑 / 子集跑**都没有写它们** ⇒
**潜在泄漏、当前不触发**。对照 `LOG_FILE`：两次红跑 **+366 B**，是**确实在触发**的。两者优先级不同，故**未改**（§53.13.6）。

✅ 将来要扩展也低风险：**没有任何测试断言 conftest 的重定向清单本身**；4 处测试 `patch.object(…, "EVENT_AUDIT_FILE", …)`、
2 处 patch `SCHEDULER_LOCK_FILE`；`test_runtime_stores.py:149-152` 钉住 `SCHEDULER_LOCK_FILE` ∈ `EPHEMERAL_STORES`、
∉ `STATE_STORES`，且其文件名不在备份清单里。

### 53.13.5 回归与静态门

| 门 | 读数 |
|---|---|
| 守卫用例单独跑 | `tests=3 failures=0 errors=0 skipped=0 time=0.499` |
| 9 文件子集（含 `test_log_level_setting.py` —— 会清空并恢复 root handlers 的高危邻居） | `tests=233 failures=0 errors=0 skipped=0 time=42.626`；生产日志 **sha256 不变** |
| 全量 `pytest tests/` | `tests=7645 failures=0 errors=0 skipped=11 time=1303.674`（21m43s；`6545 passed` + `1089 subtests`）；上一轮基线 `7642` ⇒ **+3 恰为本轮新增**；生产日志 **+0 B，`.1`/`.5` sha256 均未变（未轮转）** |
| `ruff check app/`（CI 门） | All checks passed |
| `ruff check tests/test_production_log_hermeticity.py` | All checks passed |
| `ruff check tests/`（CI 不覆盖） | 324 条**既有**；新文件贡献 **0** |
| `python -m compileall app tests` | OK |

子集 `233` = 上一轮 `230` + 本轮新增 `3`，**数字精确对齐** ⇒ 新增文件没有连带改变别的测试的收集数。
行尾不变量：改动集 5 个文件工作树内**全部纯 CRLF**，tracked 的四个 `git ls-files --eol` 均 `i/lf w/crlf attr/text=auto`。

### 53.13.6 未做 / 未决

- **三个未重定向的路径设置未改**。依据是 mtime 判据（§53.13.4）：当前不触发。**要不要改属于拍板，不属于取证。**
- **未提交、未推送**；`frontend/audit-ci.jsonc` 等 10-03 的改动仍原样保留。

## 53.14 A 项第二轮：剩下两个「可写路径」纳入重定向，并把「下次还会漏一个」本身关掉

§53.13.4 把三个未重定向的设置列为「潜在、当前不触发」并**未改**（那属于拍板）。本轮做了 A 项。
结论不是「三个都补上」，而是**改 2 个、明确不改 1 个** —— 并且顺手把「每次都要人肉重新推一遍清单」这件事关掉：
新增 `backend/tests/test_settings_redirect_census.py`（6 条 / 244 行 / 纯 CRLF）。

### 53.14.1 三个设置里只有一个不是「路径」，判据是**读者**而不是名字

| 设置 | 写入者（`app/`） | 是输出吗 | 处理 |
|---|---|---|---|
| `EVENT_AUDIT_FILE` | `event_audit_service.record_event()`（`os.makedirs(dirname)` 后 append）、`routes/events.py:343` | **是** | **重定向** |
| `SCHEDULER_LOCK_FILE` | `scheduler._try_acquire_scheduler_lock()`（`os.makedirs(dirname)` 后 `open(...,"a+")` 写 pid） | **是** | **重定向** |
| `LOL_DRY_RUN_FIXTURES_PATH` | 无（只被读） | **否，是输入** | **不重定向**，进豁免表并写明理由 |

第三行是本节最该记住的一条。`runtime_stores.DERIVED_STORES` 对它的原话是
**"dry-run fixture *input*, empty by default"**，而 `lol_adapter._resolve_dry_run_path()` 的读法是：

```
if configured:                                    # 非空
    return path if path.is_file() else None
return _DEFAULT_SAMPLE_PATH if _DEFAULT_SAMPLE_PATH.is_file() else None
```

默认 `''` 是一个**姿态**（「未配置 ⇒ 走内置样例」），不是一个指向 cwd 的路径。
把它指向一个**不存在**的临时路径，会让 dry-run 导入**静默关闭**（非空但不存在 ⇒ `return None`，
而不是回落到内置样例）。**改它会改变行为 —— 那不是 hermeticity 修复，是伪造一个修复。**
没有任何测试需要它被重定向：`tests/test_lol_adapter.py` 自己设值再还原，那正是一个输入该有的形状。

⚠️ **这是判据边界，与 §5 的门控判据同源**：`if (!x) return null` 型是「**缺席**」，不是「假 0」；
`LOG_FILE` 那种「默认真在指向仓库里的一个真文件、并且真有人往里写」才是。

### 53.14.2 改动清单

`backend/tests/conftest.py`：**本轮自身 `49 / 6`**（对 HEAD 累计 `26 / 2` → `69 / 2`）。
每个设置 4 处（类级 / 实例级 / `_restore_config_module` 的 `cls.*` 与 `_ORIGINAL_SETTINGS.*`），两个设置共 8 行赋值。

`SCHEDULER_LOCK_FILE` 用 `_SettingsClass.LOOP_DB_FILE + ".scheduler.lock"` 而不是写死临时文件名 ——
生产侧的规则**就是** `LOOP_DB_FILE + ".scheduler.lock"`，**复述规则**而不是复述结果，
这样将来 `LOOP_DB_FILE` 的重定向一旦移动，两者仍然同步。

🔴 本轮 `6` 处删除**全部是注释/文档文本，零代码行删除**，逐行列出：

```
DEL| -2. Redirecting all file-path settings (event store, databases, caches, the log
DEL| -   file) to a session-scoped temp directory so no test reads or writes real
DEL| -   production data.
DEL| -# Not a data store, but the same class of leak, and the last one left: a path
DEL| -# setting that decided where this suite WRITES outside its own sandbox.  It
DEL| -# differs from the eight above in when it has to be set.  `app/main.py` calls
```

顺带把模块 docstring 第 2 条的**枚举**删掉了。理由是它**漂过两次**：写成 `(event store, databases, caches)`
时漏了 `LOG_FILE`；而 `LOG_FILE` 注释里那句 "differs from the **eight** above" 实际是**九个**（数错了）。
既然普查用例现在强制清单，注释里再放一份枚举就是**第二个真相源**，而且已经证明它会漂 ⇒
改成指向 `tests/test_settings_redirect_census.py`。同一条道理也适用于「注释里不放会漂的数字」。

### 53.14.3 新增守卫：把「漏一个」本身变成会红的测试

判据是**分区**（与 `test_singleton_reset_census.py` 同形）：

```
{redirected} | {exempted}  ==  runtime_stores.path_setting_names()        # 13 个
```

关键设计：**「已重定向」用行为判**（该设置 `resolve()` 后的值落在临时目录内），**不解析 conftest 源码**。
源码扫描对「赋值点写在读取点之后」是**通过**的，而那正是 `LOG_FILE` 掉进去的坑（§53.12.2）。

6 条用例：

| 用例 | 作用 |
|---|---|
| `test_the_scan_sees_the_settings` | 仪器控制：总体非空、能认出已知名、能对假名说「不在」 |
| `test_the_containment_check_discriminates` | 仪器控制：`_inside` 必须能返回 `False`（否则「全都在沙箱内」空真） |
| `test_every_path_setting_is_redirected_or_exempted` | 主判据：未覆盖集合必须为空 |
| `test_the_exemption_table_is_not_stale` | 豁免表反向：已重定向的不该再豁免、豁免名必须真实存在、理由不许为空 |
| `test_recording_an_audit_row_writes_into_the_sandbox` | 写入探针一（两半：临时文件里有 + 仓库文件逐字节不变） |
| `test_acquiring_the_scheduler_lock_writes_into_the_sandbox` | 写入探针二（同上） |

### 53.14.4 红 → 绿

| 状态 | conftest | junit |
|---|---|---|
| **RED** | pre-A 版（sha `77ce3a0d4e48d993`，`numstat` 对 conftest `26 / 2`） | `tests=6 failures=3 errors=0 skipped=0` |
| **GREEN** | 本轮版（sha `da684704c92bb7b9`） | `tests=6 failures=0 errors=0 skipped=0`（与 `test_production_log_hermeticity.py` 合并跑：`tests=9 failures=0 errors=0 skipped=0 time=0.744`） |

RED 的三条失败各自点名了对象：

```
these path settings resolve outside the test temp dir and have no exemption:
    ['EVENT_AUDIT_FILE', 'SCHEDULER_LOCK_FILE']
an unpatched record_event() appended to ...\backend\event_audit.jsonl -- a fabricated
    row in an audit trail, not merely a noisy log.  The row it added is
    redirect-census-36805f60866f4b40b18e40a57d4a32d4.
an unpatched lock acquisition wrote into ...\backend\v2_loop.db.scheduler.lock;
    it now holds '10116'
```

**红跑真的写了两处**（这是本节的硬证据，不是推演）：

| 文件 | 跑前 | 跑后 |
|---|---|---|
| `backend/event_audit.jsonl` | 175,150 B / `3b3a8fb39804d96c` | **175,468 B / `1e545e7252fe1e75`**（+318 B） |
| `backend/v2_loop.db.scheduler.lock` | 5 B / `6513ea0fa99b6478` | **5 B，内容 `'10116'`（本进程 pid）** |

追加的那一行是
`{"timestamp": "2026-10-04T11:05:09.973968+00:00", "event_id": "redirect-census-36805f60866f4b40b18e40a57d4a32d4", "event_title": "redirect census probe", ...}` ——
**一条伪造的审计记录**，这正是 §53.12 里那条 `LOG_FILE` 泄漏的升级版：日志脏了是噪声，**审计轨脏了是伪证**。

**正向对照是同一条字节数**：GREEN 单跑之后，那次的沙箱里躺着 `event_audit.jsonl` **318 字节** ——
与红跑写进仓库的 **+318 B 完全同一个数**（同一份字节**搬走了**，不是消失了），外加
`v2_loop.db.scheduler.lock` 5 B、`app.log` 122 B（含 marker）、以及 `locked_file()` 留下的
`event_audit.jsonl.crosslock` 0 B；而同一跑之后仓库里的三个文件全部逐字节不变。
这与 §53.12.3 那个 1673 B 是**同一个取证形状**：只报「仓库文件没变」永远是空真。

⚠️ 两条探针的**写入刻意放在前提断言之前**。若把「设置是否在沙箱内」那条断言放前面，红跑会停在前提上，
**永远演示不出那次真实的仓库写入** —— 而演示那次写入才是这两条探针存在的理由（前提那条与普查用例重复）。

**还原**：三样（conftest + 两个运行期数据文件）逐字节还原并核 sha256，全部 `MATCH=True`。
两个文件都是 `.gitignore` 的运行期数据（`.gitignore:44` / `:53`），所以还原不影响 git 状态；
但审计轨的还原是**必须的** —— 让一条伪造记录留在审计轨里比留在日志里更糟。

### 53.14.5 门

| 门 | 读数 |
|---|---|
| 新用例单独跑（RED） | `tests=6 failures=3 errors=0 skipped=0` |
| 新用例单独跑（GREEN，两个守卫文件合并） | `tests=9 failures=0 errors=0 skipped=0 time=0.744` |
| **全量 `pytest tests/`** | **`tests=7651 failures=0 errors=0 skipped=11 time=1327.506`**（22m07s，`6551 passed` + `1089 subtests`）；上一轮基线 `7645` ⇒ **+6 恰为本轮新增**；`test_events_routes` 131 / `test_operational_readiness` 71 / `test_lol_adapter` 8 全部通过 |
| `ruff check app/`（CI 门） | All checks passed |
| `ruff check` 两个新守卫文件 | All checks passed |
| `ruff check tests/`（CI 不覆盖） | 324 条**既有**；两个新文件贡献 **0** |
| `python -m compileall app tests` | OK |
| **跑后三文件指纹** | `app.log` `9620442 / f666cad0c8d6a05f`、`event_audit.jsonl` `175150 / 3b3a8fb39804d96c`、`v2_loop.db.scheduler.lock` `5 / 6513ea0fa99b6478` —— **三者全部逐字节不变** |

⚠️ 注意这条读数的**证明力边界**：光看「全量跑完两个文件没变」**不能**证明重定向生效 ——
它们的 mtime 本来就是 2026-09-11，**修之前那一轮全量也没写过它们**。重定向的证明在两处：
普查用例（设置的值落在沙箱内）+ 两条写入探针（写者真的落到沙箱）。全量这条只证明**没弄坏别的**（7645 → 7651，`failures=0`）。

⚠️ **一条诚实记录**：本轮第一次把 10 个受影响的文件放在**前台**跑，在约 81% 处被 `SIGTERM` 中断
（日志里全是点、**没有任何 `F`**），**未产出 junit** ⇒ 该次运行**不作为任何证据**，
受影响面改由全量套件覆盖（全量本身就包含这些文件，可按文件从 junit 取读数）。

### 53.14.6 未决 / 已知边界

- 🔴 **普查的边界**：`path_setting_names()` 靠**后缀**认路径（`runtime_stores.py:82`
  `_PATH_SETTING_SUFFIXES = ("_FILE", "_DIR", "_PATH")`）⇒ 一个**不带这三类后缀**的路径设置**不在普查范围内**
  （当前 13 个全部符合，`_DIR` 一个都没匹配到）。这是普查的已知边界，不是它的缺陷 —— 但**新加路径设置时按后缀命名**是加入普查的前提。
- **未提交、未推送**；`frontend/audit-ci.jsonc` 等 10-03 的改动仍原样保留。

## 53.15 E 项 (c) 类收口：那 13 行 `{captured: 3}` 是**测试写进生产账本的行**，不是「不可恢复的问号」

§53.11.4 把 13 行记为「(c) 类：与任何现存物证都对不上」，§53.11.7 记「**未证实，且已不可证实**」，并特意写了「**不建议为了『查清』去做任何数据动作**」。本轮把这条**收口**——而且**没有做任何数据动作**。

按 §0 的规矩，本节是**取代**而不是改写：

- **仍然成立**：§53.11.4 的取证本身——事实 1/2/3/4/5（真库两表 0 行、E10 前后同路径、文件没被换过、无任何删除路径、159 个测试库全 0）；事实 6 的**观察**（`futures_market_discover` 没有账本行）。
- **被取代**：事实 6 的**推断**（「所以它的上游没跑过」）、§53.11.4 的中心推断「**39 条快照 + ≥3 条 link，当时必定写进了某个库**」、以及 §53.11.7 的「不可证实」。

### 53.15.1 结论先行

**这 13 行是 `backend/tests/test_futures_scheduler.py` 里那条用例写进生产账本的，不是任何一次真实抓取。**

```python
# backend/tests/test_futures_scheduler.py:28-38（该文件 2026-07-16 建立，至今只有 cdd8fd3 一个提交）
async def test_capture_futures_snapshots_job_calls_service_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", True)
    with patch("app.core.scheduler.FuturesMarketService") as MockSvc:
        instance = MockSvc.return_value
        instance.capture_snapshots = AsyncMock(return_value={"captured": 3, "errors": 0})  # ← 字面量
        await _job_capture_futures_snapshots()
        instance.capture_snapshots.assert_awaited_once()
```

链路是三行代码：

1. `_job_capture_futures_snapshots()`（`scheduler.py:1480`）第一步就是 `_start_run("futures_snapshots_capture")`；
2. `_start_run` → `loop_run_store.start_run` → `sqlite_db.loop_db_path()` → `os.path.abspath(settings.LOOP_DB_FILE)`（`sqlite_db.py:34-42`）；
3. `_finish_run(..., result=<mock 的返回值>)` → `loop_run_store.py:93` 写的是 `json.dumps(result, ensure_ascii=False, sort_keys=True)`。

⇒ 账本里那一列 **就是** mock 的字面量本身。

而 `capture_snapshots` 是 `AsyncMock` ⇒ **它一次都没被真正调用** ⇒ **没有任何快照被写进任何库**。
「39 条快照不见了」这个问题**不成立**：**从来没有 39 条快照**。

### 53.15.2 四条互相独立的证人

| # | 证据 | 读数 |
|---|---|---|
| 1 | **字面量唯一性** | 全 `backend/` 树里 `{"captured": 3, "errors": 0}` 只出现 **1 次**（`tests/test_futures_scheduler.py:36`），而 13 行的 `result_json` 与它**逐字节相同** |
| 2 | **设计出处** | `docs/superpowers/plans/2026-07-16-phase12-futures-markets.md:1258` 就写着这句 mock —— 文档日期 `2026-07-16`，与 13 行的首日同一天 |
| 3 | **时间边界** | 该测试文件建于 `2026-07-16 22:32:37 +0800`（`cdd8fd3`，= **14:32:37Z**）；首行是 **14:31:35Z**，早 **62 秒**（先写文件、跑一次、再提交）。末行 `2026-07-31T14:13:13Z` |
| 4 | **有界复现** | 本轮单跑该文件，在**沙箱**账本里得到**同一行**，生产侧逐字节未变（§53.15.3） |

证 3 的「边界」要和下一条对上才成立：

| 事件 | 时间（UTC） |
|---|---|
| `test_futures_scheduler.py`（含那句 mock）建立 | 2026-07-16 **14:32:37** |
| 首行 `futures_snapshots_capture` | 2026-07-16 **14:31:35** |
| 末行 `futures_snapshots_capture` | 2026-07-31 **14:13:13** |
| `9647ca2`「test: make pytest baseline deterministic and hermetic」把 `LOOP_DB_FILE` 纳入 conftest 重定向 | 2026-08-01 **01:20:30** |
| 08-01 之后 `futures_*` 行数 | **0** |
| 08-01 之后 `loop_runs` 全部行数（对照，证明账本此后仍在被写） | 64 |

**账本在重定向上线的那一刻停止被测试污染。** 这既是 13 行的解释，也是对 §53.12/§53.14 那套重定向的**事后验证**（顺带说明：`dc1541d`「test: isolate backend pytest environment」在 07-31 18:14 +0800 落地时**没有**重定向 `LOOP_DB_FILE`——已核 `git show dc1541d:backend/tests/conftest.py`）。

### 53.15.3 复现（本轮唯一的「动作」，且**没有碰生产**）

证据形状沿用 §53.12.3/§53.13.2 的成对取证：**只报「生产没变」永远是空真**，必须同时报「沙箱里出现了什么」。

```bash
cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_futures_scheduler.py -p no:cacheprovider -q
# 2 passed in 1.33s
```

| 观测点 | 跑之前 | 跑之后 |
|---|---|---|
| 生产 `backend/v2_loop.db` sha256(前 16) | `35346d467fe24ce2` | `35346d467fe24ce2` |
| 生产 `loop_runs` 行数 | 1759 | 1759 |
| 生产 `futures_*` 行数 | 13 | 13 |
| 沙箱（`pmrf_test_data_*`）里的新行 | — | `('futures_snapshots_capture','success','{"captured": 3, "errors": 0}',None)` |
| 同一行的 `duration_ms` | — | **252** |

- 生产侧 WAL 当时是 **0 字节**（其 sha256 = `e3b0c442…` = 空串的 sha），所以主文件的 sha 是**有效**见证，不是「改了但看不出来」。
- 沙箱那份账本里**只有这一行**：跑单文件时，另一个用例把 `PHASE12_FUTURES_MARKETS_ENABLED` 置 `False`，job 在 `_start_run` 之前就 `return` 了 ⇒ **一次会话恰好一行**。**13 行 = 13 次会话**。
- **252 ms** 与 13 行的 282–364 ms 同带（见 §53.15.6(2)）。

⇒ 这就是 §53.11.7 那条「不建议为了查清去做数据动作」的**正确替代**：**不需要任何数据动作，跑一次本来就在跑的测试就够了**。

### 53.15.4 §53.11.4 说「该定案的那条证据被轮转删掉了」—— 对**机制**而言它还在

§53.11.4 末段：「本该定案的那条证据是 `init_kernel_db()` 自己打的 `logger.info("Kernel DB initialized at %s", db_path)`（`kernel_db.py:521`）—— 而 `backend/logs/` 只剩 3 个文件……07-16 与 07-31 的启动行**已被轮转删除**。」

**那两天的行确实没了（不改）。但同一个机制的行，在存活日志里有几万条：**

| 日志 | `Kernel DB initialized at` 行 | 指向 `pytest-of-…` 临时路径 | 指向仓库内 `…\backend\kernel_predictions.db` |
|---|---|---|---|
| `app.log`（09-12..10-03） | 26010 | 5125 | 0 |
| `app.log.1`（09-06..09-12） | 14676 | 8000 | 0 |
| `app.log.5`（09-02..09-06） | 20415 | 13663 | 0 |
| **合计** | **61101** | **26788** | **0** |

而且**同一份日志里就有一处完整的记录**（`app.log.1:2612-2638`）：

```
2026-09-06 10:15:46,469 - app.kernel.kernel_db - INFO - Kernel DB initialized at
    C:\Users\Alin\AppData\Local\Temp\pytest-of-Alin\pytest-2190\test_an_unreadable_links_table1\futures_links_degraded.db
2026-09-06 10:15:46,500 - app.core.scheduler - INFO - [Scheduler] Futures snapshot capture starting...
2026-09-06 10:15:46,502 - app.core.scheduler - ERROR - [Scheduler] Futures snapshot capture failed
    ...
    File "backend\app\core\scheduler.py", line 1311, in _job_capture_futures_snapshots
      result = await service.capture_snapshots()
    File "backend\app\kernel\futures_market_service.py", line 116, in capture_snapshots
      verified = self._store.get_verified_links()
    sqlite3.OperationalError: no such table: kernel_futures_links
```

⇒ **「测试把内核库指到临时路径、然后在那个临时库上跑 scheduler job」这件事不需要推断 —— 它被日志逐行记下来了。**
差别只在结果：9 月那次 job 真跑、报 `failed`；7 月那次 `capture_snapshots` 被 mock、报 `success` 且带字面量。

**这回头修正 §53.11.4 的措辞**：被轮转删掉的是**7 月那两天的实例**，不是**机制的证据**。

### 53.15.5 六条事实现在怎么读

| 事实（§53.11.4 原文） | 在「测试写账本」读数下 |
|---|---|
| 1. 真库 `kernel_futures_links`/`kernel_futures_snapshots` 均 0 行 | **不需要解释**：service 被 mock，从未碰过任何库 |
| 2. E10 前后路径是同一个文件 | **无关**：进程一次也没用这个路径 |
| 3. 文件没被换过（创建时间 == 最早行时间） | **无关** |
| 4. 没有任何代码删过这两张表 | **无关**（结论仍有效：真库里确实从未有过行） |
| 5. 159 个测试库全 0 | **一致**：`capture_snapshots` 被 mock ⇒ 测试库也不会有行 |
| 6. `futures_market_discover` 的账本行数 0 | **前提纠正**（见下） |

**事实 6 的前提纠正**：原文写「它**没跑过**」，依据是「它的 ledger 行数是 0」。但 `scheduler.py:136-155` 的 `_start_run` 是**尽力而为**的：

> 「The ledger is best-effort: a job whose `loop_runs` insert fails still runs.」

⇒ **「没有账本行」≠「没跑过」**。这条 docstring 自己还记了实测（用 `BEFORE INSERT … RAISE(ABORT)` 触发器）。所以事实 6 的**矛盾力**应从「不可能」降到「**没记录**」。（本条独立于本轮结论成立，单独记在这里，因为它是 §53.11.4 里唯一的**过度断言**。）

### 53.15.6 顺手证到的两件事（都是新的，且可独立复用）

**(1) 🔴 `futures_market_service.py:123-125` 是死分支。**
`fetch_kalshi_futures_markets()` 是 **fail-closed** 的：`futures_market_source.py:238-240` 捕获一切异常并 `return []`，**从不抛出**。所以调用方那段

```python
try:
    candidates = await fetch_kalshi_futures_markets(limit=200)
except Exception:
    ...
    return {"captured": 0, "errors": len(verified)}   # ← 不可达
```

**不可达**（07-16 的版本同样如此，已核 `git show 5828b55:backend/app/kernel/futures_market_service.py`）。⇒ `errors` 的**唯一**来源是 `contract is None`。这**加强**了 `errors == 0` 的含义（§53.11.4 曾把 `:123-125` 当作一个**可达**的排除项，实际它只是排除项的名字之一）。
⚠️ 按 §4.2：**删死分支前先问「删掉后活下来的那段是不是空头」**；删/不删属于拍板，**本轮不动**。

**(2) 🔴 时长指纹：这 13 行不可能来自一次真抓取。**
`futures_market_source.py:234` 在**每个被接受的候选**之后 `await asyncio.sleep(settings.KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS)`，默认 **1.0 s**（`config.py:1831`；`.env` 未覆盖；`.env.example:775` = `1.0`；**07-16 的版本就有这一行**）。

- `captured=3, errors=0` ⇒ `price_by_ticker` 必须含那 3 个 ticker ⇒ 抓取**成功返回 ≥1 个候选**（否则 `errors = len(verified) > 0`）⇒ **至少 1 次 `sleep(1.0)`** ⇒ **≥1000 ms**。
- 13 行的 `duration_ms`：282 / 282 / 297 / 297 / 304 / 305 / 305 / 305 / 322 / 329 / 339 / 340 / 346 / 364 —— **最大 364 ms**。
- 本轮复现（mock 路径、无网络）：**252 ms**。

⇒ **同带**。真抓取读数应 **≥1000 ms**，观测是 **282–364 ms**，**不相容**。
⚠️ 保留一条残余可能：若当时把 `KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS` 导出为 0，则 300 ms 也能装下一次真抓取。但这**不改变结论** —— 因为事实 1（真库两表 0 行）已独立排除「真抓取」：真抓取会写下 3 行快照，而**没有任何文件有**。
同一次会话的**交叉印证**：`sport_market_discover` 在 07-16 有 6 次报 `kalshi_candidates: 1`、`duration_ms` 296–355；07-31 再 4 次、296–324 —— **同一个 1.0 s sleep 也在 `kalshi_sports_source.py:110`**。

### 53.15.7 (c) 类的可复用判据（本节要交付的东西）

**(a) 定义**：一条 `status=success` 的账本行，其 `result_json` 断言的**正计数**（>0）所对应的下游物证，在**任何存活的存储里都找不到能与该行关联的见证**。

**(b) 与 §53.11.2 的三种「0」的区别**：§53.11.2 管「**0 说不清**」（开关关着 / 真的没有 / 读失败）；(c) 类管「**正数说不清它指向谁**」。两者是不同的病，判据不能互相套用。

**(c) 为什么 (c) 类**按构造**就可能查不清（根因判据）**：账本记录**运行**，物证落在**另一个存储**，而两者**没有共享键**。此处：

- `futures_snapshots_capture` 的 `result_json` 只有 `{captured, errors}` —— **无 link id、无 ticker、无路径、无 run_id**；
- `kernel_futures_snapshots` 的 DDL（`kernel_db.py:460-473`）**没有 `run_id` 列**；
- 全仓 `run_id` 只活在 `scheduler.py` 的内存 map（`_RUN_TO_JOB`）与 `loop_runs.id` 里，**不被任何业务表引用**。

⇒ 这一行与它的产物**在 schema 层就不可关联**。**这不是「我们没查到」，而是「按设计查不到」** —— 所以判据必须落在 schema 上，不能落在「再努力找找」。

**(d) 甄别方法（便宜、静态、可复用）**：对**每一个「返回正计数」的生产者**问一句：

> **这一行有没有一个持久化的 correlator 指回它的产物？**

没有 ⇒ (c) 风险点。此时**不要先追根因**（很可能查不清），而是**先问「这个正计数会不会根本是被 mock 出来的」** —— 本例成本最低的一步就是 `grep` 那个字面量（§53.15.2 证 1），**一步结案**。

**(e) 处置规则**：对 (c) 类

1. **先查字面量**：`grep -rn '<result_json 的内容>'` —— 若命中测试的 mock，**结案**（本例）。
2. 再查**账本路径是否被重定向**：若未重定向，**测试会写生产账本**（本例的机制）。
3. 仍然查不清时，**不做数据动作**（恢复不了已丢的字节，且有二次伤害风险），改为**写进交付记录**并转成结构修复（见 (f)）。
4. ⚠️ **边界**：只在「生产者确实返回正计数」时有意义；`{captured: 0}` 归 §53.11.2，不归 (c)。

**(f) 结构性修法（提议，本轮不改代码）**：

1. 让「返回正计数」的账本行带上**可独立解析的 correlator**（最少：在 `capture_snapshots` 的 result 里回写那 N 个 ticker / link id）；
2. 或让产物侧记账（`kernel_futures_snapshots` 加 `run_id`）。

任一条都能把**未来的 (c) 变成 (b)**。另与 §53.11.2 的老账**合并看**：`/trades` 的 `status='voided'` 不出现、`已平仓` 的计数不声明排除项 —— 同族的「读数不声明自己指向什么」。

### 53.15.8 本轮「动作」清单（一句话交代清楚）

- **只读**：扫 `backend/**/*.db`（9 个）+ `.workbench/kernel_copy.db` + 3 个 `backups/*/v2_loop.db`；读 `loop_runs`；读 3 份 `backend/logs/`；读 git 历史。
- **写入**：**只写了本节的文字**，外加 `.workbuddy/audit-2026-10-03/` 下的取证记录（`e53-before.txt`、`junit-e53-repro.xml`）。
- **跑测试一次**：`pytest tests/test_futures_scheduler.py`（2 passed）—— **生产账本 sha 前后一致**（§53.15.3）。
- **没有做**：不动任何数据库、不改任何默认、不改任何代码、不提交、不推送。

### 53.15.9 明确不做 / 留给拍板

- **不动**那 13 行账本（它们现在是**判据的一部分**，不是垃圾）。⚠️ 但要写明：`LOOP_RUN_RETENTION_DAYS` 默认 **90**（`config.py:519-526`），而维护 job 的账本行**最后一条是 2026-07-24**（近期没跑）。**若保留期恢复执行**：07-16 的 9 行会在 ~**2026-10-14** 出局、07-31 的前 2 行 ~**10-29**；**`07-31T14:13:13` 那一行**因「每个 job 名的最新一行豁免」而**永久留存**。⇒ 本节把判据落到文档里，正是为了**此后不必再依赖那 13 行**。
- **不改** `test_futures_scheduler.py` 的那句 mock（它现在是「一次会话写一行账本」的可复现锚点；要改先拍板）。
- **不改** `PHASE12_FUTURES_MARKETS_ENABLED` 的默认（`false`，§53.11.3 的姿态）。
- **不动** `futures_market_service.py:123-125` 的死分支（§53.15.6(1)）——**删/改属于拍板**。
- **不提交、不推送。**

## 53.16 把 §53.15 的判据推成普查：账本里 **127 行永久 `running`**，而写来清理它们的 job **一次都没执行过**

§53.15 交付了一条判据（「**正计数的孤证**：先 `grep` 字面量，再查路径是否被重定向」）。按 §53.14 的老规矩 —— **同类出现到第三次就该改成普查** —— 本轮先把它推成普查（§53.16.1），普查本身**干净**；但同一次普查把一个**新的、独立的问题**照了出来（§53.16.2 起）。

### 53.16.1 普查：把「测试写账本」这条判据推给全部 19 个 job

方法：取账本里**每一个 distinct `result_json`**（共 19 个 job），去仓库源码（`backend/app`、`backend/tests`、`backend/scripts`、`docs`，1082 个文件）里找**逐字出现**。

| 结果 | 读数 |
|---|---|
| payload 在源码里逐字出现的 job | **恰好 1 个**：`futures_snapshots_capture` 的 `{"captured": 3, "errors": 0}` → `backend/tests/test_futures_scheduler.py`（§53.15 的结论） |
| 其余 18 个 job | **无逐字出处** |

⇒ **§53.11.4 把 `sport_market_discover`（30 行）判为 (b) —— 普查后这条判定站得住**：它的两种 payload（`kalshi_candidates` 0 / 1）在仓库里找不到逐字出处，不是测试写进去的。
⚠️ **扫描器陷阱（写下来）**：`startup_prediction_db_init`/`startup_sqlite_integrity` 的 payload 是 `{}`，长度 2，**任何 Python 文件都含它** ⇒ 第一版扫描把它们报成 HIT。**必须设长度下限**（本普查取 `len >= 12`）才不产生假阳性。

### 53.16.2 普查照出来的新问题：127 行 `status='running'`

| 读数 | 值 |
|---|---|
| `loop_runs` 总行数 | 1759 |
| `status='running'` | **127**（7.2%），**且 127 行的 `finished_at` 全为 `NULL`** |
| 其中 `started_at < 2026-07-24` | **123** |
| 其余（2026-09-10） | 4 |
| 分布在哪些 job | `event_discover` 120、`event_discover_startup` 4、`translate_titles` 2、`event_auto_resolve` 1 |
| **「最新一行」就是 `running` 的 job** | **1 个**：`event_discover_startup` |

### 53.16.3 解药存在、无条件注册、有测试 —— 但**从未在活数据上执行过**

`_job_loop_run_ledger_maintenance`（`scheduler.py:441-482`）的 docstring 自己就是这条缺陷的诉状：

> 「Measured on the live ledger **before this job existed: 123 rows stuck in `running` (2026-06-29 to 07-23**, every owner process long dead) … a `running` row has exactly two exits -- the job's `_finish_run`, or nobody. No process re-attaches to a stored row, so an abandoned row is **permanent**.」

**今天这 123 行一行没少。** 逐项核对：

| 项 | 读数 |
|---|---|
| 注册 | `scheduler.py:1654-1660`，`CronTrigger(hour=6, minute=48)`，**无任何开关门控** |
| 它在账本里的行数 | **0**（19 个 job 名里没有 `loop_run_ledger_maintenance`） |
| 测试覆盖 | `test_loop_run_store.py` 5 条（`fail_stale_running_rows`）+ `test_scheduler.py` 2 条（含 `assertIn("loop_run_ledger_maintenance", ids)`）⇒ **是承重不是空头** |
| 引入提交 | `6b820e7`，**2026-09-24 12:37 +0800**（= 04:37Z） |
| 孪生 job `loop_db_maintenance`（`hour=6, minute=45`，早 3 分钟） | 8 行，**最后一行 2026-07-24 22:45Z**（= 06:45 CST） |

**⇒ 这不是注册 bug。** 两个 job 相隔 3 分钟注册，老的那个自 **07-24** 起就没再命中过每日窗口；而这个 job 是 **09-24** 才进来的 ⇒ **「0 行」= 这段每日窗口自 07-24 以来从未被命中，而它 09-24 才存在**。
**但结论一样硬**：**写来清理那 123 行的 job，至今一次都没执行过；它专属的那批行今天一行没少。**

### 53.16.4 为什么这是个真问题：`running` 让「死了」与「在跑」在**四个门**上都不可区分

`latest_run_per_job()` 有四个消费者（`loop_run_store.py:166` 的 docstring 自己列的）。逐个看它们怎么读 `running`：

| 门 | 位置 | 对 `running` 的读法 |
|---|---|---|
| `SCHEDULER_LAST_SUCCESS` 仪表 | `utils/metrics.py:333` `if run.get("status") != "success": continue` | **序列根本不创建**。它自己的 docstring：*"this gauge is what a 'last success older than N' alert reads -- **an alert cannot fire on a series that was never created**"* |
| `scheduler_job_failed` 异常 | `routes/quality_metrics.py:320` `if run.get("status") == "failed"` | **不触发**（`running` ≠ `failed`） |
| `/api/health` 的 `runs` | `services/loop_status_service.py:60` → `_visible_run()` | **原样透出** `running`（`_visible_run` 只挑 `job_name/status/started_at/finished_at/duration_ms`，**不判超期**） |
| quality-metrics 调度块 | `routes/quality_metrics.py:503` | 同源，同透出 |

**实测的受害行**：`event_discover_startup` 的 6 行里，**最新一行是 `running`，停在 `2026-09-10T19:51:22`，已 24 天**，`finished_at`/`duration_ms` 皆 `NULL`。

```
2026-09-10T13:14:53  d=311815  success   ← 该 job 单次要跑 ~5.2 分钟
2026-09-10T13:42:13  d=None    running
2026-09-10T18:35:17  d=267038  success   ← ~4.5 分钟
2026-09-10T19:45:29  d=None    running
2026-09-10T19:49:36  d=None    running
2026-09-10T19:51:22  d=None    running   ← 「最新一行」，/api/health / 仪表 / 异常 都读这一行
```

它是个**启动期** job、单次要跑 **4.5–5.2 分钟**；19:51 起跑后进程就没了（同一分钟的 19:54:52 还有 `world_cup_live_update` 成功行，说明进程随后才退）⇒ 典型的「owner process long dead」，也正是 §53.16.3 那条 docstring 描述的对象。

⇒ **两个后果都是具体的**：(1) 这个 job **没有 `SCHEDULER_LAST_SUCCESS` 序列** ⇒ 那条「N 小时没成功就报警」的告警**结构上不可能触发**；(2) 它**永远不会**产生 `scheduler_job_failed` 异常，因为唯一能把它变成 `failed` 的路径就是那个从未执行的 reconciler。

### 53.16.5 判据边界：这是缺陷还是姿态？（§5 的规矩）

**同仓先例存在，且在同一个 `lifespan` 里** —— `main.py:293-315` 对**两张邻表**用的是**启动期** reconcile：

- `reconcile_predictions()`（"Startup reconciliation healed %d orphan predictions"）
- `get_task_manager().reconcile_interrupted_tasks()`，注释写得和本条一模一样：
  *"a `'pending'`/`'running'` row in SQLite after a restart has no `asyncio.Task` behind it, so `/auto-tune/status` would report it running forever"*

⇒ **同一个问题，仓库里已有两种治法**：邻表用**启动期**（重启即自愈），`loop_runs` 用**每日 cron**。后者是唯一的「治愈取决于应用是否正好在那个钟点活着」。
在这台**不常驻**的机器上，后果就是 §53.16.2 的 127 行**永久**存活。

🔴 **可命名的一般性质**：一条 `running` 行的「死↔忙」不可区分窗口 = `LOOP_RUN_STALE_RUNNING_HOURS`（默认 **36h**）+ 最长 **24h** 的 cron 延迟 ≈ **最长 ~60 小时**；**超过这个窗口之后，四个门里没有任何一个会再报警**（仪表序列不存在、异常不触发、health 原样透出）。也就是说：**一个 job 死掉超过约 60 小时后，它在所有机器读法上都等同于「正在跑」。**

### 53.16.6 未决 / 明确不做

- **未决（属于拍板）**：是否给 `loop_runs` 也加一条**启动期** reconcile（与 `main.py` 里两张邻表一致），或缩短 cron 延迟。**本轮不改代码** —— 这属于业务/契约取值。
- **不改**那 127 行（它们是本节的判据）。⚠️ 与 §53.15 同一条时间提醒：`LOOP_RUN_RETENTION_DAYS` 默认 90，而保留期只在**同一个**从未执行的 job 里生效 ⇒ **保留期目前也没在跑**（最老行 06-29 已 97 天仍在，与 §53.15 的读数一致）。
- **不动** `_job_loop_run_ledger_maintenance` 的 cron 时刻/阈值、不动 `LOOP_RUN_STALE_RUNNING_HOURS` 默认。
- **不提交、不推送。**

## 53.17 追 §53.16：127 行孤儿的成因，一半是**代码路径**（可复现），一半是**取消/进程死亡**（`except Exception` 结构性抓不到）

§53.16 只说这些 `running` 行是「owner process long dead」，把它当成**环境现象**记录。本轮往下追一层：**它是不是也有一条代码路径？** 结论：**是，而且恰好一条。**

### 53.17.1 结论先行

- 对 `backend/app/core/scheduler.py` 里**全部 23 处 `_start_run`** 做 AST 普查（源码取自 `git HEAD`，不手抄）：**恰好 1 处**是「起了不结」的泄漏路径 —— `_job_translate_titles`。
- 该 job **正是账本里 4 个漏过的 job 之一**（`translate_titles`，2 行孤儿）。
- **其余 126 行**的成因不是这个，而是另一层：全部 23 处 job 体用的都是 `except Exception`，而 **`asyncio.CancelledError` 不是 `Exception` 的子类**（实测 `False`）⇒ 取消/非优雅终止时 `_finish_run` **永不执行**。

### 53.17.2 普查：23 处 `_start_run`，1 处泄漏

方法（可复用）：把 `scheduler.py` 从 `git HEAD` 读出 → `ast.parse` → 对每个函数体，找赋值语句里调用 `_start_run` 的位置 → **看紧随其后的那条语句是不是 `Try`**。中间隔着任何语句 ⇒ 那一段在 `try` 之外，抛了就没人 `_finish_run`。

| 判定 | 数量 | 明细 |
|---|---|---|
| 紧跟 `try:`（OK） | **22** | `:316`、`:351`、`:388`、`:422`、`:464`、`:508`、`:585`、`:642`、`:705`、`:763`、`:790`、`:816`、`:849`、`:907`、`:960`、`:1180`、`:1250`、`:1289`、`:1320`、`:1377`、`:1462`、`:1485` |
| **下一个语句不是 `try`（泄漏路径）** | **1** | **`:284` `_job_translate_titles`** |

```python
run_id = _start_run("translate_titles")   # :284
events = list_all_events()                # :285  ← 在 try 之外
translated = 0
try:                                      # :287
    ...
    _finish_run(run_id, "success", ...)   # :304
except Exception as exc:
    _finish_run(run_id, "failed", ...)    # :310
```

`:285` 一抛，`:304` 与 `:310` **两边都到不了** ⇒ 行留在 `running`，而且异常还会**穿出 job**（调度器侧只能记一条日志，`_finish_run` 再也回不来）。
⚠️ 这是普查的**结构性判据**，不是「我看它像有问题」；对照的 22 处形状一致，只有这一处不同。

### 53.17.3 复现（沙箱保险，生产逐字节未变）

一次性探针（跑完即删）：patch `app.memory.event_store.list_all_events` 抛 `RuntimeError`，然后 `await scheduler._job_translate_titles()`。
⚠️ 必须 patch **源模块**（`app.memory.event_store.list_all_events`）—— 该函数在 job 体内 `from … import`，`from X import f` 是**按值绑定**，patch 使用方模块名无效。

| 观测 | 值 |
|---|---|
| 沙箱账本 | `…\Temp\pmrf_test_data_5lkoi7cs\v2_loop.db` |
| 逃出 job 的异常 | `RuntimeError: probe: list_all_events exploded` |
| 沙箱账本内容 | **恰好 1 行**：`{'job_name': 'translate_titles', 'status': 'running', 'finished_at': None, 'duration_ms': None}` |
| 生产 `backend/v2_loop.db` | sha `35346d467fe24ce2`、**1759 行** —— 跑前跑后一致 |

⇒ 与生产里那 2 行孤儿（`translate_titles`，`running`，`finished_at` NULL）**形状完全相同**。
⚠️ 取证形状与 §53.15.3 一致：**只报「生产没变」永远是空真**，必须同时报「沙箱里出现了什么」。

### 53.17.4 另外 126 行：`except Exception` 结构性抓不到「取消」

- 全文件**没有一处 `except BaseException`**，也**没有一处 `finally:` 去兜 `_finish_run`**（23 处 job 体清一色 `except Exception`）。
- 实测（本仓 venv）：`issubclass(asyncio.CancelledError, Exception)` → **False**；`issubclass(…, BaseException)` → **True**。
- 关闭路径：`stop_scheduler()`（`scheduler.py:1850-1856`）→ `scheduler.shutdown(wait=True)`；`main.py:357-358` 只在 `scheduler_started` 为真时调用它。
- ⇒ **非优雅终止**（Ctrl-C 未走 lifespan、SIGKILL、终端关闭、进程被拦等）不会让 `_finish_run` 执行；`wait=True` 也只在**优雅**路径上有效。

**量级与形状证据**：`event_discover` **224 行里 120 行 `running`（54%）**，且这些孤儿**散布在全部 24 个小时**（`10h–17h UTC` 为峰，即 18h–01h CST），**不是**集中在 cron 时点 ⇒ 与「频繁地被中途杀掉」一致，而不是「某一次特定故障」。

### 53.17.5 三层凑成的闭环（这也是 §53.16 那条「解药没跑过」为什么这么要紧）

| 层 | 读数 |
|---|---|
| **泄漏** | 1 条**代码路径**（`translate_titles`，已复现）＋ 全 23 处对**取消**无免疫（`except Exception`） |
| **出口** | `running` 只有两个出口：job 自己的 `_finish_run`，**或没人**（`_job_loop_run_ledger_maintenance` docstring 原话） |
| **解药** | 只在每日 cron，且**本环境从未执行**（§53.16：它的账本行数 = 0，那 123 行一行没少） |

⇒ **三层各自都成立** ⇒ 127 行永久 `running` 是**结构决定的**，不是运气不好。

### 53.17.6 未决 / 明确不做

- **未决（均属改动，留给拍板）**：
  ① 把 `translate_titles` 的 `list_all_events()` 挪进 `try`（一行）；
  ② 23 处加 `finally:` 兜 `_finish_run`（或改捕 `BaseException`）；
  ③ §53.16 的启动期 reconcile。
  ⚠️ 按 §4.2：若走 ②，必须问「**原来的 `except` 分支活下来之后是不是空头**」—— 该分支现在承担 `error=str(exc)`，`finally` **给不出**这个字段 ⇒ **②不是「把 except 换成 finally」这么简单的等价改写**。
- **不新增**常驻守卫用例（探针**已删**，只留读数）。**不改代码、不提交、不推送。**
- 探针文件 `backend/tests/test_zz_tmp_translate_leak_probe.py` **已删除**，`git status` 回到原本的 8 项。

## 53.18 回头核 §53.17 的两条前提：一条要**改窄**，一条**已静默过期**（并顺带查出第四条）

**这一节不是新发现，是自检。** 上一节（§53.17）下了两个判据：① `translate_titles` 的 `events = list_all_events()` 是「**可达的**泄漏路径」；② 归档里 `/trades` 的 voided 那条是「**未决**」。两条都要回头核——因为 §4.1 说了，结论会在前提被修掉之后**静默过期**，而「我刚写的」并不豁免。

核的结果：**① 部分成立但要改窄；② 确实已经过期**。另外在核 ① 的路上撞出**第四条**独立发现。

### 53.18.1 修正一：`list_all_events()` 能否抛？——源码说「不能」，探针说「只在一个窄缝上能」

§53.17.2 的原话是：`scheduler.py:285` 的 `events = list_all_events()` 在 `try:` 之外，所以读失败会让 `run_id` 永久停在 `running`。这条**依赖一个未验的前提**：`list_all_events()` 会抛。

把调用链逐层读到底（全部取自工作树字节）：

| 层 | 位置 | 异常面 |
|---|---|---|
| `list_all_events()` | `event_store.py:349-356` | `list(_load_unlocked(_store_path()).values())` —— 无包装 |
| `_load_unlocked()` | `event_store.py:29-50` | `read_json(path, {})`；`normalize_event_record` 包在 `except Exception: pass`（`:46-49`，注释：*"we never want a read path to fail"*） |
| `read_json()` | `file_store.py:171-197` | docstring 第 172 行逐字：**"Lenient read for caches / read-only paths. **Never raises.**"** 内层捕获 `json.JSONDecodeError`（`:185`）与 `OSError`（`:189`）；外层再捕 `OSError`（`:192`，含 `locked_file` 抢锁失败） |

⇒ 就**缺失文件 / JSON 损坏 / OSError / 抢锁失败**这四类而言，`read_json` 真的不抛（损坏 JSON 会 quarantine 后回退，见 §53.17.2 已记录）。**§53.17.2 说的「读失败」若指这四类，则路径不可达。**

**但 `"Never raises"` 是过度承诺。** 我把非法 UTF-8 字节喂进 `read_json` 实测（沙箱临时目录，未触碰生产数据）：

```
read_json        -> RAISED | UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 7
read_json_strict -> RAISED | UnicodeDecodeError: ...
UnicodeDecodeError is OSError?   False
UnicodeDecodeError is ValueError? True
```

原因：`open(path, encoding="utf-8")` + `json.load` 遇到非法字节抛的是 `UnicodeDecodeError`，它是 **`ValueError` 的子类、不是 `OSError`** ⇒ **同时穿透**内层的 `except json.JSONDecodeError` 与外层的 `except OSError`。

⇒ **修正后的判据（更窄，但不再无据）**：`list_all_events()` 不是全函数；它**恰好在「`event_store.json` 含非法 UTF-8 字节」这一条缝上**会抛。这条缝比「任意读失败」窄得多，而且要求 event store 本身已损坏。

⇒ **对两条 `translate_titles` 孤儿的成因排序随之回到**：首选仍是 **`CancelledError`**（`except Exception` 结构性抓不到，§53.17.4 已验），非法 UTF-8 只是**理论上**的第二可能。**结论（那两行是孤儿）不变，变的只是机制的可达性描述。**

### 53.18.2 第四条：`read_json` 的实现比它的契约窄一个异常类

上一条实测顺带钉出一个**独立于泄漏路径的**事实：`read_json` docstring 写的是无条件的 `"Never raises"`，实际异常面是 `{UnicodeDecodeError}`（且 `read_json_strict` 也穿透——虽然它「该抛」，但抛的**不是**它 docstring 里承诺的 `JSONDecodeError`，而是解码错误，调用方的 `except` 若只认前者同样漏）。

- **影响面**：所有走 `read_json` 的读者（不止 event store）。凡是「假定这个函数绝不抛」而把它放在 `try` 之外、或在 `except` 里只列 `OSError/JSONDecodeError` 的调用点，在 store 文件含非法字节时都会**意外冒泡**。
- **同族**：这属于「**注释承诺 > 实现**」——与 §53.5「0 必须只说一件事」是**不同**的一类（那是语义歧义，这是文档-实现偏离）。按 §0，记事实、**不代决要不要修**（修法是一行 `except (json.JSONDecodeError, UnicodeDecodeError)`，或把 `ValueError` 一并纳入——留给拍板）。

### 53.18.3 修正二：项目记忆里 `/trades` voided 那条「未决」**已过期**

我要核的原话（**两处落点**）：

- 注入版核心 `MEMORY.md:125`：`⏳ **未决**：/trades 的 status='voided' **在任何列表里都不出现**；同族：**已平仓 的计数是个不声明自己排除了什么的读数。**`
- 归档 `MEMORY-archive.md:236`：`⏳ **未决**：/trades 只有「当前持仓」/「已平仓」两 tab，而 status='voided' **在任何列表里都不出现**`

**提交链（这是关键——它说明为什么当时是对的）：**

| 提交 | 时间 (+0800) | 做了什么 | 路由读者？ |
|---|---|---|---|
| `fb1d767` | 2026-09-27 23:45:42 | `fix(trades): give a voided prediction's paper trade a terminal state` —— 加**终态**（`prediction_store.py` / `simulated_trade_store.py` +145 / `api.ts`） | ❌ **没有** |
| `2ec731f` | 2026-09-27 23:45:53 | `docs(audit): record the voided-trade fix as §16` | ❌ |
| `bc9235a` | 2026-10-02 15:29:44 | `fix(world-cup): … and read voided trades` —— 加**路由 + 前端 + 计数** | ✅ **有** |

⇒ `fb1d767` **只造了 `status='voided'` 这个取值，没造读者** ⇒ 在 2026-09-27 写下时，**「不出现」是真的**。五天后 `bc9235a` 补上读者 ⇒ 结论**静默过期**。这正是 §4.1 说的那类：**「因为 A 所以不能做」的结论会在 A 被修掉之后静默过期**。

**当前工作树的证据（全部现读，`bc9235a` 是 HEAD `c13e6bd` 的祖先，已验 `merge-base --is-ancestor` ⇒ YES）：**

- 路由：`events.py:1629` `@router.get("/trades/voided", response_model=SimTradeListResponse)`，返回 `count` / `total: count_voided_trades()` / `limit` / `offset` / `trades`。docstring 逐字：*"It left the open list when it was voided, **so without this route it was visible in no list at all**"*。
- store 读者：`simulated_trade_store.py:411-427` `list_voided_trades()` —— `WHERE status='voided' ORDER BY exit_time DESC, trade_id DESC`；`count_voided_trades()` `:370-378`。
- 测试：`test_events_routes.py:3771` docstring 逐字 *"GET /events/trades/voided -- **the only reader for status='voided'**"*，`:3805`/`:3851` 跑分页。
- **前端（说明不是「有路由没人用」）**：`frontend/src/app/trades/page.tsx:319` 有**第三个 tab** `{ key: "voided", label: "已作废", count: voidedTotal }`，`:516-607` 有整块 `voided-trades-section` 与分页；`api.ts:1024` `voidedTrades()`。

**「同族」子句也一并过期**，而且修复代码**原样复述了症状**：`count_voided_trades` 的 docstring（`:373-376`）逐字 —— *"Without this count the /trades page showed a 'closed' figure that silently excluded them -- **a reading that did not say what it had left out**."* 这与我记忆里记的「**已平仓 的计数是个不声明自己排除了什么的读数**」是同一句话。谓词也核了：`_count_trades`（`:349-357`）= `SELECT COUNT(*) FROM simulated_trades WHERE status=?` ⇒ `已平仓` 数（`status='closed'`）**结构性排除** `voided`，第三个 tab 现在把这个排除**摆到台面上**。

**处置**：两处落点**都改写标记（`⏳ 未决` → `✅ 已修复`）+ 附提交号与日期**，**不删原句**（保留「当时确实如此」这个历史事实）。已在 2026-10-04 本地记忆轮次内完成。

### 53.18.4 可复用判据（写进 `audit-delivery-verification`）

**「未决 / 不出现 / 只有两 tab」这类记忆项，用一个「症状短语反查源码」的动作就能判死活——比重新读一遍代码便宜得多。**

1. 从记忆项里抽出**症状的措辞**（本案：*"在任何列表里都不出现"* / *"不声明自己排除了什么"*）。
2. 在源码里 grep **这句话的语义近邻**（英文注释常逐字复述：*"no list at all"* / *"did not say what it had left out"*）。
3. **命中即判死**：修复提交往往把症状写在**近旁的 docstring / 注释**里（本案 `events.py:1639`、`count_voided_trades` docstring、`api.ts:458`），而且是**过去时**（*"without this route it **was** visible in no list at all"*）⇒ 语言的时态就是判据。

⚠️ 配套两条：① 反查必须**同时**看注入版核心（`MEMORY.md`）与归档（`MEMORY-archive.md`），否则会只改一半——本案两处都有。② 判死要**给出提交号+日期**，不能写「已修复」，否则下次又得重查（与 §0「结论复述必须带前提」同源）。

### 53.18.5 未决 / 明确不做

- **未决（均属改动，留给拍板）**：
  ① `read_json` 的异常面补 `UnicodeDecodeError`（或把 docstring 的 `"Never raises"` 改成有条件的）；
  ② §53.17.5 那三条（`translate_titles` 挪进 `try` / 23 处兜 `finally` / 启动期 reconcile）。
- **不新增**常驻守卫用例（本节的探针是**临时文件、已删**，只留读数）。
- **不改代码、不提交、不推送。** 生产库 sha 与 §53.17 一致，未触碰。

## 53.19 A 项收口：`sport_market_discover` 那 10 组「三联行」也是**测试写进生产账本**的行（(c) 类第二次命中），并顺带找到第二个独立信宿

§53.11.7 第三条把它登记为「形状异常，谁在跑没有定论」，**本节把它定案**：这 30 行与 §53.15 的 13 行是**同一个机制、同一个种群**，只是**指纹不同**——而正是这个不同让上一轮的普查漏掉了它。

### 53.19.1 结论先行

1. 30 行 = **10 组、每组恰好 3 行**、组内模式**恒为**「首行 `kalshi_candidates: 1`，后两行 `0`」、跨度 1.43–1.63 s。**零单行组**。
2. 生产者 = `tests/test_kalshi_scheduler.py` 的**三个用例**（`:22`、`:39`、`:61`），跑一次该文件 ⇒ 恰 3 行，且**顺序就是文件里的用例顺序**（1 → 0 → 0）。
3. **有界复现**：沙箱内跑该文件，账本**恰好 3 行**、模式 `1 / 0 / 0`；生产库 sha 前后一致。
4. ⇒ 这 30 行应归 **(c) 类**（成功行断言了一个计数，却没有任何**生产**物证与之对应），**§53.11.4 给它的 (b)「跑过、产出 0」判据要重述**；并且 **§53.16 那次字面量普查有假阴性**（见 53.19.6）。

### 53.19.2 账本侧：严格分组普查

把 30 行按「相邻间隔 ≤ 5 s」分组（改前不预设组大小，让数据自己分组）：

```
groups = 10 | sizes = [3,3,3,3,3,3,3,3,3,3] | 直方图 {3: 10}
每组的 payload 模式（1 = 首行 kalshi_candidates: 1）：100 × 10
跳过跨度：1.43s / 1.52s / 1.53s / 1.54s / 1.55s ×3 / 1.56s ×2 / 1.57s / 1.63s
```

**零单行组**是要紧的：真实 cron 跑只可能产生**单行组**（`IntervalTrigger(minutes=60)`、`max_instances=1`）。30 行**全部**属于 3 人组 ⇒ **没有一行是 cron 的产物**。

### 53.19.3 有界复现（沙箱内演绎，生产只读）

```
PROD before : sha 35346d467fe24ce2 | 1759 rows | sport rows 30
pytest tests/test_kalshi_scheduler.py  ->  tests=3 failures=0 errors=0  (1.87s)
沙箱账本（%TEMP%/pmrf_test_data_*/v2_loop.db）: rows = 3
  2026-10-04T15:25:50.095Z | sport_market_discover | success | 259ms | {kalshi_candidates:1, kalshi_linked:1, ...}
  2026-10-04T15:25:50.868Z | sport_market_discover | success |  74ms | {kalshi_candidates:0, ...}
  2026-10-04T15:25:51.134Z | sport_market_discover | success |  95ms | {kalshi_candidates:0, ...}
PROD after  : sha 35346d467fe24ce2 | 1759 rows | sport rows 30   （未变）
```

三个用例各自为什么产出那个数（读源码即可对上，不靠巧合）：

| 用例 | 手段 | 产出 |
|---|---|---|
| `test_scheduler_discovers_kalshi_when_enabled` | `fetch_kalshi_sport_markets` 桩返回**1 个**候选 | `kalshi_candidates: 1` ← **组首行** |
| `test_scheduler_skips_kalshi_when_disabled` | `PHASE11_KALSHI_SPORTS_ENABLED=False` | `kalshi_candidates: 0` |
| `test_kalshi_failure_does_not_break_polymarket` | 桩 `side_effect=RuntimeError`，被内层 `except`（`:936`）吞掉 | `kalshi_candidates: 0` |

三个用例都把 `PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED=False`（第三个虽为 True 但桩返回 `[]`）⇒ **30 行的 `polymarket_candidates` 恒为 0**。✔

**生产库未受影响的第二个理由（比"重定向"更早生效）**：两个旗标**默认都是 `false`**（`config.py:1709-1710`、`:1829`）。`PHASE7_SPORT_MARKET_BRIDGE_ENABLED=false` 时，job **既不会被注册**（`scheduler.py:1743` 的门），进来了也在 `:904-905` **在 `_start_run` 之前**就 return ⇒ **默认配置下的真实应用根本产不出这些行**。能产出的只有"强制把开关打开"的东西 = 测试。

### 53.19.4 四条时间边界互相印证

| 边界 | 提交 | 时间 | 与 30 行的关系 |
|---|---|---|---|
| 测试文件诞生 | `34780ad` | 2026-07-16 **22:06:15 +0800**（=14:06:15Z） | 首组首行 14:05:12Z ⇒ **提交前 63 s**；第二组 14:05:33Z 也在提交前 ⇒ 「写→跑(红)→改→跑(绿)→提交」 |
| `LOOP_DB_FILE` 重定向 | `9647ca2` | 2026-08-01 **09:20:30 +0800** | 末行 07-31 ⇒ 之后测试**不再**往生产账本写行 ⇒ **末日期可解释** |
| payload 变 5 键 | `dba5971` | 2026-08-09 **22:23:59 +0800** | 30 行**全是 2 键** ⇒ 现代码**结构上不可能**产出它们 |
| 新增测试文件 | `05edb2d` | 2026-09-04 **13:38:30 +0900**（=12:38:30 +0800） | 见 53.19.5 的 3→5 迁移 |

### 53.19.5 第二个独立信宿：日志（它给出了账本**看不到**的那些会话）

账本 08-01 后干净，但 `backend/logs/app.log*` 到 **10-04** 还在打 `[Scheduler] Sport market discovery starting...`。初看像矛盾，查下去是**两个信宿的净化日期不同**：

- `LOOP_DB_FILE` 重定向**已提交**（`9647ca2`，08-01）⇒ 账本早干净。
- `LOG_FILE` 重定向**不在 HEAD**，只在**工作树**里（`conftest.py:124/142/179/192`，即 §53.12 那笔**未提交**的 A 项处置）⇒ 整个 9 月测试**照样写生产日志**。

⇒ **08-01 到（LOG_FILE 重定向落地）之间是一个「半净化窗口」：账本干净、日志脏。** 而"日志里的这些行是测试的"有独立证据：同一时间窗内夹着**假夹具**（`boston_celtics`/`los_angeles_lakers`、`KXNBAGAME-TEST`）与**临时库**的 `Kernel DB initialized` 连打（§53.15 已测：26,788 条指向 `pytest-of-…`，0 条指向仓库路径）。

**并且它做了一个可证伪的预测，验过了。** 日志簇大小按"每会话调用次数"应等于 3；若 9-04 新增的文件再加 2 次（它有两处 `_ledger_row()` 调用，见 53.19.7）就应变成 5：

```
app.log.5 (09-02→09-06) 的簇时间线：
  … 09-04 09:59:04  n=3        ← 新增文件尚未落地
     09-04 12:19:24  n=2  ← 作者按 -k 选集迭代（12:19–12:21 连续七簇 n=2）
     …
     09-04 12:26:33  n=5        ← 迁移点
     09-04 之后全部   n=5
   迁移点 12:26:33 比该文件的提交（12:38:30 +0800）**早 12 分钟** ⇒ 又是「写→跑→提交」
```

三份日志合计 **327 行 / 74 簇**（`{2:7, 3:11, 5:11}` + 16×5 + 29×5），全部 `duration` 类信息一致。**簇数 ≫ 10** —— 正是"账本停了、日志没停"的直接读数。

### 53.19.6 🔴 方法论升级：§53.16 的字面量普查有**假阴性**，必须补第二遍

§53.16 判 `sport_market_discover` 「(b) 站得住」的依据是：**它的 payload 在仓库里找不到逐字出处**。这条依据**成立**，但**不充分**：

| 测试写行的两种方式 | payload 里有字面量吗 | 字面量普查能抓到吗 |
|---|---|---|
| **伪造 payload**（`AsyncMock(return_value={"captured": 3, ...})`，§53.15 的 13 行） | 有（就是测试源码里的字面量） | ✅ 抓得到 |
| **跑真 job**（patch 掉 fetch、让生产代码自己算 payload，本节 30 行） | **没有**（payload 由生产代码生成） | ❌ **抓不到** |

⇒ **「payload 无逐字出处」只能排除"伪造"，不能排除"测试"**。两次命中恰好是两类各一例，凑成一对反例。

**补充判据（应加进普查第二遍）**：对每个 job，问一句 **「有没有测试直接调用这个 job 函数？」** —— 有，则账本里可能混着测试行，**与 payload 长什么样无关**。本案的测法很便宜：

```
grep -rn "_job_<name>\b" backend/tests/     # 命中即列入可疑
```

### 53.19.7 新判据：**信宿不对称** 与 **「日志行数 > 账本行数」= 有调用者把写入口打桩**

**信宿不对称（可复用）**：同一批测试可能在**多个信宿**（账本 / 日志 / 落盘文件）留痕，而**各信宿被"净化"的日期各不相同** ⇒ 会出现「A 干净、B 脏」的窗口。**用途**：用一个**便宜**的信宿（日志）去数**昂贵**信宿（账本）看不见的那些会话；反过来，**「A 有、B 无」的差异先怀疑"日期不同"，不要先怀疑"没发生"**。（同族：§53.15 用日志补回了账本丢掉的机制证据。）

**计数差的指纹（可复用）**：`test_kalshi_fixture_resolution_degraded.py:122-141` 的 `_ledger_row()` helper **把 `scheduler._start_run` 打桩成 `lambda: "run-1"`** ⇒ 它调用 job 会**打日志但不写账本行**。它被调 **2 次**（`:226`、`:229`，同一用例内）⇒ **满量会话日志 5 行、账本 3 行**。

⇒ **「某 job 的日志行数 > 账本行数」不是噪声，是「有调用者把 `_start_run` 打桩（或写入失败被 best-effort 吞掉）」的签名。** 反查：
```
grep -rn 'patch.object(scheduler, "_start_run"\|setattr(scheduler, "_start_run"' backend/tests/
```
（同族：§53.15 的 fact-6 —— `_start_run` best-effort ⇒ 「无行」≠「没跑」。两者合起来：**账本行数只是调用次数的下界**。）

### 53.19.8 对 §53.11.4 / §53.16 / §53.11.7 的重述（不是改写）

- **§53.11.4** 把 `sport_market_discover` 记为 **(b) 跑过、产出 0**：**前提是"这些行是生产运行"**，该前提已被本节取代。它其余的取证（**它的 payload 无逐字出处**）仍然成立。
- **§53.11.7 第二条**（「0 是否"应该"是 0」）：账本里的 0（以及那个 1）**都是测试产生的**，因此账本**对此问题不提供任何证据**——原来"自述自洽"的读法要降级为"**无证据**"。（那个 `kalshi_candidates: 1` 也是**桩返回的假候选**，不是真的市场。）
- **§53.11.7 第三条**（「三联行 / 谁在跑」）：**收口**，见 53.19.1。
- ⚠️ 注意 **(c) 类现在有两个实例**（13 行 + 30 行），且**分属两类指纹** ⇒ §53.11.1 里"一个不可恢复的问号"的措辞应读作"**一类问题、两个实例**"。

### 53.19.9 未决 / 明确不做

- **未决（改动，留给拍板）**：把 53.19.6 的第二遍普查写进探针；`LOG_FILE` 重定向（§53.12，工作树未提交）落地后，**"半净化窗口"会关闭，日志作为第二信宿的能力也会消失** —— 这是个**取舍**，不是纯收益。
- **不做**：不改旗标默认、不改 conftest、不动数据库、不提交。探针未新增（本节的复现用的是**既有测试文件**，无临时文件留下）。
- 生产库 sha `35346d467fe24ce2`、1759 行、sport 30 行，**全程未变**。

## 53.20 B 项：第二遍普查跑完 —— 账本有 **30 个写入点**（§53.17 只扫了 23 个），而它**救回了 §53.16**

§53.19.6 说「逐字普查有假阴性，要补第二遍：`grep -rn "_job_<name>\b" tests/`」。本节把第二遍**真跑了一遍**，覆盖全部 19 个 job；结果比预期大：不只找到更多可疑对象，还顺带**证伪了我自己的一个怀疑**，并把 §53.17 的普查**范围**问题暴露出来。

### 53.20.1 结论先行

1. **19 个 job 里 11 个被测试直接调用过**（只有 `sentiment_refresh` 零引用，7 个因「写入点不在 `scheduler.py`」而未被第一遍覆盖）。
2. 🔴 **`loop_runs` 有 30 个写入点，分布在 8 个文件、4 类模块** —— §53.17 的「23 处、22 OK、1 泄漏」只扫了 `scheduler.py`，**覆盖率 23/30 = 77%**；漏掉的 7 处里有**一个 API 路由**。
3. 🔴 **判据 ⑫（"紧随其后是不是 `try`"）在两个方向上都会错**：对 **start-only helper** 假阳性；对 **动态 run 名** 假阴性（我自己的报表里就有一次读数错误，见 53.20.3）。
4. 🔴 **我怀疑「`event_discover` 的 120 个孤儿行是测试写的」—— 被数据推翻。** 它们**是真实运行被进程重启打断**，机制在历史注册代码里（53.20.5）。⇒ **§53.16 成立且被量化，不需要翻案。**
5. §53.17.4（取消/进程死亡路径）由此被**量化**：**127 个孤儿里 120 个来自一个「有 `try` 保护、不是泄漏点」的 job** ⇒ 孤儿的主因是**杀进程**，不是代码泄漏。

### 53.20.2 写入点普查：30 处 / 8 文件 / 4 类模块

AST 扫 `backend/app/**/*.py`，抓 `_start_run(...)` 与 `*.start_run(...)`（注释与定义体自动排除，比 grep 可靠）：

| 文件 | 处数 | 类别 |
|---|---|---|
| `core/scheduler.py` | **23** | 调度 job 体（`_start_run("<字面量>")`） |
| `api/routes/events.py` | 1 | 🔴 **API 路由**（`:798`，`world_cup_api_football_validate`） |
| `main.py` | 1 | lifespan 启动记录（`:113`，循环 2 个 `startup_*` 名） |
| `services/world_cup_post_match_backfill_service.py` | 1 | 服务类 `_start_audit_run` |
| `services/world_cup_quality_service.py` | 1 | 同上（`world_cup_consistency_repair`） |
| `services/world_cup_result_fact_backfill_service.py` | 1 | 同上 |
| `services/world_cup_scoring_service.py` | 1 | `world_cup_scoring_reconcile` |
| `services/world_cup_verified_result_correction_service.py` | 1 | 写了但 **0 行**（见 53.20.3） |
| **合计** | **30** | 4 类模块 |

⇒ **账本不是"调度器的账本"**，它是**全应用共用的审计账本**：一个 HTTP 请求处理器也会开一条 run。§53.17 关于「配对」的结论**只对 `scheduler.py` 的那 23 处成立**。

### 53.20.3 🔴 判据 ⑫ 的两个方向的误用（含我自己的一次读数错误）

"看紧随其后的语句是不是 `try`"这次报了 **7 处**，逐条甄别后**没有一处是新泄漏**：

| 站点 | 形态 | 甄别 |
|---|---|---|
| `scheduler.py:284` `_job_translate_titles` | next=`Assign`，**同函数内有 `_finish_run`** | ✅ **真泄漏**（§53.17 已登记） |
| `world_cup_scoring_service.py:265` `score_all_finished_matches` | next=`Assign`，同函数内有 finish | ⚠️ **待判**（形态同 284，留拍板） |
| `main.py:113` `_record_core_startup_outcome` | next=`<last>`，有 finish | ⚠️ **待判**（"开一条就返回"可能是设计） |
| `world_cup_post_match_backfill_service.py:29` `_start_audit_run` | next=`<last>`，**无 finish** | ❌ **假阳性**：这是 **start-only helper**，配对在**调用方** |
| `world_cup_quality_service.py:598` `_start_consistency_repair_audit_run` | 同上 | ❌ 假阳性 |
| `world_cup_result_fact_backfill_service.py:206` `_start_audit_run` | 同上 | ❌ 假阳性 |
| `world_cup_verified_result_correction_service.py:298` `_start_audit_run` | 同上 | ❌ 假阳性 |

⇒ **判据 ⑫ 必须加限定**：**只对"既开启、又在同一函数体内关闭"的函数适用**（即函数体内出现 `finish_run`）。否则一个**唯一的职责就是把 run_id 交出去**的 helper 会被误判。**这是 §53.17 那条判据的修订，不是否定**（对 `scheduler.py` 那 23 处的用法仍然正确）。

🔴 **另有一个我自己的读数错误要如实登记**：普查报表里 `world_cup_scoring_service.py:265` 那行显示 `rows=0`，而该 job 实际有 **1201 行**。原因是我把 `<dynamic:SCORING_RECONCILE_AUDIT_JOB_NAME>` 直接拿去查表 ⇒ **动态名查不到就让"行数"变成 0**，而 0 在这里读起来"像事实"。**教训**：普查里的**每个派生读数都要有可信度**，`<dynamic>` 必须显式渲染成"未解析"，**绝不能落成一个数字**。（同族：§5「0 必须只说一件事」。）

### 53.20.4 用 08-01 重定向当「嫌疑 / 真实」判别器

`9647ca2`（2026-08-01）把测试的 `LOOP_DB_FILE` 指向临时目录 ⇒ **之后由测试产生的行不会再落进生产账本**。于是：

- **只出现在 08-01 之后** ⇒ **必然是生产运行**（测试写不进来）。
- **只出现在 08-01 之前** ⇒ **有嫌疑**（该窗口里测试写过生产账本，§53.15/§53.19 都实证过）。

| 分组 | job（行数） |
|---|---|
| **post-only ⇒ 必然生产** | `event_discover_startup`(6) · `startup_prediction_db_init`(8) · `startup_sqlite_integrity`(8) · `world_cup_live_update`(20) |
| **spans both** | `translate_titles`(160: 157/3) · `world_cup_scoring_reconcile`(1201: 1182/19) |
| **pre-only ⇒ 有嫌疑（13 个）** | `event_discover`(224) · `sport_market_discover`(30) · `world_cup_post_match_backfill`(24) · `futures_snapshots_capture`(13) · `world_cup_result_fact_backfill`(12) · `event_auto_resolve`(8) · `loop_db_maintenance`(8) · `optimization_task_cleanup`(8) · `sentiment_refresh`(8) · `world_cup_prediction_update`(8) · `world_cup_consistency_repair`(6) · `world_cup_source_bundle_import`(6) · `world_cup_api_football_validate`(1) |

**全局**：1759 行里 **1695 行（96%）在 08-01 之前**；**127 个孤儿里 123 个在之前**。

⚠️ **"有嫌疑"是必要不充分** —— 它只说明"那个窗口里测试**可以**写"，不说明"这行**就是**测试写的"。判定仍要靠 §53.15/§53.19 那种**独立指纹**。

### 53.20.5 🔴 我怀疑 120 个孤儿是测试写的 —— 数据把它推翻了

嫌疑最重的是 `event_discover`：**224 行全在 08-01 前**，其中 **120 行 `running`**（占全部孤儿的 95%）。按上面的判别器，它完全可能是测试行。**实测否掉了**：

| 观测 | 读数 | 含义 |
|---|---|---|
| 104 个 `success` 行的 `duration_ms` | **615,000–631,000 ms（~10.3 分钟）** | 测试**造不出** 10 分钟的真实墙钟 |
| 它们的 `result_json` | `{"count": 22..32, "events":[…真实事件，带嵌套 actionable_recommendation…]}` | 是**真实**发现结果 |
| 104+120 行的**邻域** | **全部 isolated**（60 s 内无其它同 job 行） | 不是"每会话 3 行"的测试簇 |
| 120 个 `running` 行 | `duration_ms` **全 NULL**、`result_json` **全 NULL** | 开了没关 |
| 07-04 单日 | **72 行**、且当天 09:41–15:06 三小时内 **~25 次启动** | 4 小时 cron **不可能**产出 |

**机制在历史注册代码里（`42d6aaa`，2026-07-05）：**

```python
scheduler.add_job(_job_event_discover, IntervalTrigger(hours=4),
                  id="event_discover", max_instances=1)          # 4 小时一次
scheduler.add_job(_job_event_discover, "date",
                  run_date=datetime.now(timezone.utc)+timedelta(seconds=30),
                  id="event_discover_startup")                    # 每次启动 +30s
```

**两个 `add_job` 用的是 `id` 不同、但函数是同一个 `_job_event_discover`** ⇒ **`_start_run("event_discover")` 这个 run 名被"启动"和"cron"两条路共用**。而**启动路径每次进程启动都触发**，该 job **要跑 ~10 分钟** ⇒ **开发者每几分钟重启一次 ⇒ 每次重启都杀掉一个跑到一半的 run ⇒ 一行永久 `running`。**

✅ **三条交叉验证**：
- `event_discover_startup` 这个**独立 run 名**是 `7b596bf`（**2026-08-07**）才引入的 ⇒ **08-07 前不可能有 `event_discover_startup` 行** —— 账本里正是 **0 行**（其 6 行全在 09-10 之后）✔
- `event_discover`（cron 名）的**末行是 07-25** ⇒ 拆分后启动路径改用了新名字，而 4 小时 cron 需要连续 4 小时 uptime 才触发 —— 本环境的 uptime 从来没到过 ⇒ **0 行** ✔
- 07-04 三小时 25 次启动 = 平均**应用寿命 ~7 分钟**，而 job 要 10 分钟 ⇒ **多数 run 必然被打断** ✔

⇒ **这 120 个孤儿是真实生产行为，不是测试噪声。** §53.16 的核心论断（`running` 只有两个出口、`except Exception` 看不见取消、解药从未执行）**成立**，并且现在有了**量化**：**127 个孤儿里 120 个来自 `event_discover`**。

### 53.20.6 对 §53.16 / §53.17 的重述（收窄 + 量化，不是翻案）

- **§53.17.2/3（代码泄漏）**：`event_discover` 是**有 `try` 保护**的（紧随 `_start_run`），**不是泄漏点** —— 却贡献了 **120/127 的孤儿**。⇒ **孤儿的主因是"杀进程"（§53.17.4），不是代码泄漏。** 泄漏点 `translate_titles` 只对应 **2 行**（其 160 行里 2 行 `running`，占 1.6%）。
- **§53.16**：结论**不变**；新增的量化是「一个 **10 分钟**长的 job + 一个**每次启动都触发**的入口 + 开发期**每几分钟重启**」这个具体组合。可推广的读法：**孤儿的数量 ≈ 重启次数 × P(run 未跑完)**，**与"代码有没有 `try`"关系很小**。
- ⚠️ 同时要**反向自省**：§53.19 建立的"测试写生产账本"是**真的但局部的** —— 96% 的账本行在 08-01 前，其中**绝大多数是真实运行**（`event_discover` 即证）。**不能把"08-01 前的行"整体读成测试数据。**

### 53.20.7 新判据（写进 `audit-delivery-verification`）

1. **普查写入点，别把范围钉在一个文件上**：先枚举**所有**调用写入函数的站点（AST，不是 grep），**按文件归类**；若发现写入点分布在多个模块（尤其**路由/服务**），则先前在同一文件内的结论**要标注适用范围**。
2. **用"净化日期"当嫌疑判别器，但只当必要不充分**：把账本按重定向提交切两段 ⇒ post-only 必真、pre-only 有嫌疑。**判罪仍须独立指纹**（时长 / payload 多样性 / 邻域孤立性）。
3. **三个便宜的"真实性"指纹**：① **墙钟时长**（测试造不出 10 分钟）；② **payload 多样性**（真实运行几乎每行不同；测试常量只有 1–2 种）；③ **邻域孤立性**（测试会成簇，cron/启动会孤立）。**三者同时指向同一结论才算定案**（本案三条全中）。
4. **判据自身要标适用前提**（⑫ 的修订）：结构判据若假定"开与关在同体内"，就必须**先断言这个前提**，否则 helper 会假阳性。
5. **普查的派生读数不得落成数字**：未解析的动态名要渲染成 `未解析`，**不能渲染成 0**（本案我因此写过一次假读数）。

### 53.20.8 未决 / 明确不做

- **未决（改动，留拍板）**：53.20.3 表里那 **2 处待判站点**（`world_cup_scoring_service.py:265`、`main.py:113`）；`events.py:798` 那个 API 路由（next=`Try`，形态合规，但**请求级** run 是否需要请求中断保护，是产品判断）。
- **不做**：不改代码、不改旗标、不动数据库、不提交。普查脚本与报表落在 `.workbuddy/audit-2026-10-03/`（`census_pass2.py` / `census-pass2.txt` / `census_write_sites.py` / `census-writing-sites.txt`），**不入库**。
- 生产库 sha `35346d467fe24ce2`、1759 行，**全程只读未变**。

## 53.21 C 项：把 §53.16 / §53.17 压在下面的前提逐条实测 —— 一条被**推翻**、一条被**升级**、并查出一处**潜伏缺陷**

前几节（§53.16/§53.17/§53.19）的结论都压在几个**没有验过**的前提上。本节把它们逐条拿实测顶一遍。方法一律是：**先看代码字节，再在副本上把它演出来**（只读生产、副本用完即删）。

### 53.21.1 结论先行

1. **一条被推翻**：「`started_at` 是带 `T` 的文本、而参数可能是 datetime ⇒ 字典序比较会失效」—— **不存在**。1759/1759 严格同形（见 53.21.2），字典序比较**有效**。
2. **一条被升级**：§53.16 说「解药从未执行」。在副本上**真跑**那条 SQL ⇒ **rowcount = 127、`running` 127→0** ⇒ **解药本身是对的，只是从没被执行**（不是"解药是坏的"）。
3. 🔴 **查出一处潜伏缺陷**：`fail_stale_running_rows` 把 `duration_ms` 绑成 `now`（**isoformat 字符串**）⇒ 一旦执行，127 行会拿到**文本型 `duration_ms`**，实测三条后果（见 53.21.5）。⇒ **"把解药打开"不是安全修法。**
4. **一条在修正范围后仍成立**：30/30 写入点**没有一处**捕 `BaseException`、**没有一处**裸 `except:`、30/30 捕 `Exception`；`finally`-闭合**只有 1 处** ⇒ §53.17.4 的"取消不可免疫"**全范围成立**。
5. **判据 ⑫ 第三次假阳性**，并暴露**我自己的一处方法缺陷**（`next_stmt` 只对顶层语句有效）。
6. 🔴 **同一危险在代码库里已有先例并被逐字写明**（`main.py:103-108`）—— 所以这不是"没人想到"，是**已知取舍**。

### 53.21.2 前提一（**被推翻**）：`started_at` 的可比性

**怀疑**：`fail_stale_running_rows` 的 `WHERE ... started_at < ?` 是**文本**比较；写入用 `utc_now()`（`helpers.py:5-6` = `datetime.now(timezone.utc).isoformat()`），调用点传 `(now - timedelta(hours=36)).isoformat()`。若两者格式不同（典型：一方带 `T`+`+00:00`，另一方是空格分隔），则**任何行都不会被选中**——那会是"127 行清不掉"的**第二个**独立原因。

**实测（全表 1759 行）**：

```
严格形 `len==32 且 [10]=='T' 且 [19]=='.' 且 [26:]=='+00:00'` :  1759 / 1759
长度直方图: {32: 1759}      后缀直方图: {'+00:00': 1759}
异常样例: []
```

⇒ **全部同形**，字典序 = 时间序，**比较有效**。我自己提的这条假设**不成立**，如实记录为负结果。

⚠️ **同一节里我两次踩到"正则静默零命中"**：同一段 Python 里 `\d{4}` 形式的正则在**经工具传入后**匹配到 0 行（第一次把日志聚类算成 0 簇，第二次把格式检查算成 `{False: 1759}`）。**教训**：扫描器返回 0 时必须用**已知阳性**输入做一次控制（技能 ⑩ 已写，这次是我自己没照做）；且**能用切片就别用正则**（本例改用 `len`/`[10]`/`[26:]` 后一次就对）。

### 53.21.3 前提二（**成立**）：`running` 的等价式与"清理器没碰过"

```
status='running'                      : 127
其中 finished_at IS NULL              : 127
非 running 却 finished_at IS NULL     :   0     ← 双向等价，无例外
127 行 running 的 error 取值          : {NULL: 127}   ← 一个都没有 STALE_RUN_ERROR
status 直方图                          : {success: 1629, running: 127, failed: 3}
3 行 failed 的 error                   : "WORLD_CUP_SOURCE_BUNDLE_URL is not configured" ×2
                                        "API-Football returned 0 fixtures ..." ×1
```

⇒ §53.16 用的口径（`status='running'`）与"孤儿"**严格同延**，没有把 `running` 与 `finished_at` 混用；并且**没有任何一行**带着清理器才会写的 `STALE_RUN_ERROR` ⇒ **独立确认"清理器一行都没碰过"**（此前只有"它名下 0 行"这一条证据，而"0 行"会被 best-effort 的 `_start_run` 掩盖）。

### 53.21.4 前提三（**升级**）：解药**本可一次修好**

把生产库**拷到临时目录**（生产只读），用与源码**逐字相同**的 SQL 与同一套 cutoff 计算跑一次：

```
cutoff(36h)    = 2026-10-03T04:00:25.724954+00:00
running before = 127  |  rowcount = 127  |  running after = 0
```

⇒ **语句、阈值、比较全部正确**，一次调用即可清空全部孤儿。这把 §53.16 的结论从「解药从未执行」**升级**为「**解药是正确的、且本可一次修好，只是从未被执行**」——两者对处置的含义不同（前者会让人怀疑"是不是它自己也是坏的"）。

### 53.21.5 🔴 新缺陷（潜伏）：清理器会把 `duration_ms` 写成**文本**

`fail_stale_running_rows`（`loop_run_store.py:276-288`）：

```python
now = utc_now()                    # ← 返回 str（isoformat）
...
SET status='failed', error=..., finished_at=COALESCE(finished_at, ?), duration_ms=?
(..., (STALE_RUN_ERROR, now, now, cutoff_started_at))     # ← duration_ms 拿到的是 now
```

而**正确语义**在 `finish_run`：`duration_ms = _duration_ms(started_at, finished_at)` ⇒ **整数毫秒**。

**副本实测（跑完之后读回）**：

```
样本：finished_at=2026-10-04T16:00:43.676577  duration_ms=2026-10-04T16:00:43.676577  typeof=text
按存储类分组 × 谓词：
   typeof=integer  <500=0  >500=1  n=365
   typeof=integer  <500=1  >500=0  n=1267
   typeof=text     <500=0  >500=1  n=127      ← 127 行文本（TEXT 在 SQLite 里排在 INTEGER 之后）
聚合： avg=37604.83  min=136  max='2026-10-04T16:01:01.577271+00:00'   ← max() 返回了时间串
```

**三条后果（都实测过）**：
1. **排序/范围查询失真**：`duration_ms > 500` 对这 127 行为**真**、`< 500` 为**假** —— 任何"找慢 job / 找快 job"的过滤都会把被清理的行**误列为最慢的一批**。
2. **聚合污染**：`max(duration_ms)` 直接返回那个**时间字符串**；`avg()` 被非数值行拉偏。
3. **跨层类型违约**：前端 `scheduler-timeseries.tsx:41` 逐字是 `` {p.duration_ms != null ? `${p.duration_ms}ms` : "—"} `` ⇒ 会渲染成 **`2026-10-04T16:01:01.577271+00:00ms`**；`system-status.tsx:97` 把 `duration_ms ?? duration_seconds*1000` 当数字用。而 TS 契约（`duration_ms: number | null`）与 SQLite 的列声明 `duration_ms INTEGER` **都**假定它是数字 —— SQLite 是动态类型，**没人拦得住**。

**语义也错**：写进去的是**清理发生的时刻**，不是这条 run 的时长；而且它与 `finished_at` **完全相同**。而该函数 docstring 的原话是"*gains `finished_at` ... and `duration_ms`, so a reconciled row is **distinguishable from a completed one***"——**意图是让被清理的行可辨认**，实现却让它与 `finished_at` 相等、类型还不同。

**潜伏性**：因为清理器从未执行，**当前生产库 0 行**是文本型（实测 `typeof(duration_ms)='text'` 计数为 0）⇒ 这个缺陷**只会在解药第一次运行时爆发**。⇒ **"打开解药"必须先修这一行**，否则一次修好 127 行的代价是往账本灌 127 行类型违约数据。

### 53.21.6 前提四（**修正范围后仍成立**）：取消不可免疫

把 §53.17 的判据按**全部 30 个**写入点重跑（它当时只扫 `scheduler.py` 的 23 处）：

```
TOTAL sites = 30
  有 finally 块闭合     : 1       （唯一：world_cup_scoring_service.py:269）
  捕 BaseException      : 0
  裸 except:            : 0
  捕 Exception          : 30 / 30
  完全没有 close 调用的 : 4       （4 个 start-only helper，配对在调用方）
```

⇒ **§53.17.4 全范围成立**：`asyncio.CancelledError` 不是 `Exception` 子类，而**没有一处**捕 `BaseException` ⇒ **取消/进程死亡不可能让任何站点写出收尾**。

**那唯一一处 `finally` 值得单说**（见 53.21.7）：它让**保护段内**的取消也能收尾 —— 这正是 §53.17.4 缺的那一半。⇒ **`finally` 是"抗取消"的充要手段**，而 30 处里只有 1 处有。

**4 个 start-only helper 的配对在调用方**，逐个核过：`post_match_backfill:189` / `result_fact_backfill:60` / `quality_service:652` 走本地 `finish()` 闭包；`verified_result_correction:68` 走 `try:`。⚠️ **闭包式闭合本身仍是"被调用才闭合"**，与 §53.17 的"出口必须每条路径可达"是同一类问题，**本节不展开**，留作后续。

### 53.21.7 判据 ⑫ 第三次假阳性 + 我自己的一处方法缺陷

- **假阳性 #3**：`world_cup_scoring_service.py:265` 的 open 之后是 `started = time.monotonic()` / `session = get_prediction_session()` / 然后 `try:` —— 按 ⑫ 判"泄漏"。**实测它有 `finally` 闭合（`:269`）** ⇒ 保护段内的异常与**取消**都会收尾。**但**：`get_prediction_session()` 在 `try` **之前**，它若抛，`finally` **不会进入** ⇒ **仍有缺口，但比 `translate_titles` 窄**（2 条语句 vs 1 条），且**抗取消**。⇒ §53.20.8 的"待判 #1"**结案：非泄漏（有窄缺口）**。
- 🔴 **我自己的方法缺陷**：上一节报表里的 `next stmt` 字段**只对函数的顶层语句有效**。`main.py:113` 的 open 嵌在 `try` 内，我印出的 `<last>` 是**内层块的最后一句**，读起来像"函数最后一句"——**误导**。已在 53.21.5 定性为"3 条语句后即 `finish_run`，无缺口"。**登记在案，不再拿 `next_stmt` 单独下结论。**

### 53.21.8 🔴 这个危险在代码库里**已有先例，并被逐字写明**

`main.py:103-108`（`_record_core_startup_outcome` 的 docstring）逐字：

> *"`start_run` succeeding and `finish_run` raising leaves a `running` row nothing will finish, and the probe degrades only on `status == "failed"` -- so **the residue is indistinguishable from a job still in progress**."*

⇒ 两点结论：
1. **§53.16 命名的"死↔忙不可分辨"不是我的发现，是仓库里已经写下的知识** —— 这**加强**了 §53.16（有作者自己的措辞佐证），也说明它**不是疏忽**。
2. **但那一处的立场与这里相反**：`main.py` 选择 **fail-fast**（记录失败就 `raise RuntimeError`，宁可不启动），而 `loop_runs` 的孤儿选择了 **每日 cron 兜底** —— 而那个兜底**从未执行**，且（53.21.5）**执行了还会写坏类型**。⇒ **同一危险、两种处置，其中一种从未生效。**

### 53.21.9 未决 / 明确不做

- **未决（改动，留拍板）**：
  ① `fail_stale_running_rows` 的 `duration_ms` 改成**整数**（如 `int((now - started_at).total_seconds()*1000)`，或直接置 NULL）—— **这是"打开解药"的前置条件**；
  ② §53.17.5 的三条（含"23 处"应更新为 **30 处 / 1 处有 `finally`**）；
  ③ `world_cup_scoring_service.py:265` 是否把 `get_prediction_session()` 挪进 `try`（窄缺口）；
  ④ 4 个 helper 的**闭包式闭合**是否需要逐路径审计。
- **不做**：不改代码、不改阈值、不动生产库、不提交。本节所有实测都在**临时副本**上做，用完即删（`shutil.rmtree`，已验证目录不存在）。
- 普查脚本与报表在 `.workbuddy/audit-2026-10-03/`（`census_close_reachability.py` / `census-close-reachability.txt`），**不入库**。
- 生产库 sha `35346d467fe24ce2`、1759 行、127 `running`，**全程未变**。

## 53.22 附带发现（C 项收尾时撞见）：`_TEST_DATA_DIR` 每个 pytest 进程留一个目录，且**从不清理**

C 项最后要交代"临时物"的时候顺手 `glob` 了一次系统临时目录，撞见一件与本节主题无关、但属于同一族的事：**hermetic 夹具自己往 `%TEMP%` 里漏东西，而且没有任何清理。**

### 53.22.1 事实

`backend/tests/conftest.py:55`（模块导入时执行）：

```python
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="pmrf_test_data_")
```

- **创建时机 = 导入 conftest 时**，即**每个 pytest 进程一次**（`--collect-only` 也会创建）。
- **清理**：conftest 里 **没有** `atexit` / `shutil.rmtree` / `tmp_path_factory` / `basetemp`；唯一的 hook 是 `pytest_configure`（`:200`，只设 `asyncio_mode = "auto"`）；有一个 session 级 autouse 夹具带 `yield` 收尾（`:206-213`），但它只**恢复 `os.environ`**。
  ⇒ **没有任何一条路径会删掉这个目录。**

**实测（本机，2026-10-04 23:2x）**：

```
pmrf_test_data_* 目录 : 178 个
内含文件              : 301 个
合计                   : 12,903,555 B（12.3 MB）
mtime 范围            : 2026-10-01 15:13  ..  2026-10-04 23:26
⇒ ≈ 44 个/天、≈ 3 MB/天（活跃开发期）
```

**量级不大，但斜率是常数**：这是 `9647ca2`（2026-08-01）那个"让 pytest 基线确定且 hermetic"的提交的**自身副产物**——它把路径**改道**到了临时目录（这是对的），但**没给出口**。同族的先例：`LOG_FILE` 不在重定向清单里（§53.11.5 / §53.12）、`mixing 的 51 个文件`（§1）。**"hermetic 到仓库"与"不漏到机器"是两件事。**

### 53.22.2 为什么不能简单删掉——它同时是**证据仓**

🔴 这些目录不是纯垃圾：**它们本身就是取证材料**。§53.15 与 §53.19 的定案读数，正是**读沙箱账本**得到的（"沙箱里恰好 3 行、模式 `1/0/0`"、"生产 sha 前后一致"）。如果夹具改成用完即删，那么"某个测试打了几行账本"这类**运行期事实就再也无法事后核对**（会退化成只能靠读代码推断）。

⇒ **这是一个取舍，不是纯缺陷**：
- **留着** ⇒ 磁盘线性增长（≈3 MB/天），且是**未声明**的增长（没人知道 `%TEMP%` 里有个证据仓）。
- **删掉** ⇒ 失去一类**事后可核**的运行期证据。
- **中间选项**（留给拍板）：保留**最近 N 次**会话（像 `tmp_path_factory` 默认只留 3 个根目录那样），或保留但**在目录名/日志里声明它是证据仓**，或把沙箱账本**导出到 `.workbuddy/`** 后在收尾时删除。

⚠️ 按 §5 的口径：这里的问题不是"目录有没有"，而是**"这个目录是什么"没有对外声明** —— 一个不声明自己是证据仓的证据仓，和"不声明自己排除了什么的读数"是同族。

### 53.22.3 处置 / 明确不做

- **本节只登记，不清理。** 理由有二：① 它是**跨会话累积**的、**不属于本次任务**；② 批量删除系统临时目录里的一族目录，属于"vague cleanup"，**不做**。
- ✅ **本次确实清掉了自己的一个**：`pmrf_readjson_probe_b_tgao71`（§53.18 那个 `read_json` 探针留下的，当时只删了 `.py` 没删目录）—— **这是我自己的漏项，已在核验阶段发现并删除**（删前读过内容：仅 `broken.json` + 其 `.crosslock`）。本节其余的数字**全是只读测量**。
- ⚠️ 另有一批**更早会话**的散件（`pmrf_junit_*.xml` / `pmrf_pytest_*.log` / `pmrf_*.txt` 共 35 个 / 4.0 MB，名字里带 `s52`、`mv`、`rt` 等），**不是本次产生的**，**只报不删**。
- **未决（改动，留拍板）**：53.22.2 的三个选项；若采纳，落地位置现成——`:206` 那个 session 级 autouse 夹具已经有 `yield` 收尾。

## 53.23 「按顺序做」第 1 项：修掉清理器的 `duration_ms` 类型缺陷（代码 + 测试），并做四相变异验证

本节是这段工作里**第一段真正改代码**的小节（§53.1–§53.22 全部是只读取证）。改动面两处：
`backend/app/memory/loop_run_store.py` 一个函数体 + docstring；`backend/tests/test_loop_run_store.py`
新增一个测试 + 一行 `datetime` import。**未提交、未推送**；生产 `v2_loop.db` 全程只读。

### 53.23.1 结论先行

- §53.21.5 记的那条**潜伏缺陷已修**：`fail_stale_running_rows` 不再把 ISO 时间串写进 `duration_ms`（INTEGER 列）。
- 修法是**逐行**算 `_duration_ms(started_at, now)` —— 复用 `finish_run` 用的同一个 helper ⇒ 被清理的行与
  正常结束的行现在是**同一种值**（integer），且该行的 `(finished_at, duration_ms)` 这一对**自洽**。
- 新测试是**承重的**，不是陪跑：把修复变异回退后它**变红**，红的症状正是预测的那一个
  ——`{'stale-1': 'text', 'stale-2': 'text'}`（旧绑定落 TEXT）。
- 四道门全过：`ruff` 干净；`mypy app/` = *Success: no issues found in 331 source files*；
  `generate_types --check` = *up to date*；pytest 63 passed / 0 failures / 0 errors（以 junit 计）。
- 行尾不变：两个文件仍是 pure CRLF / 0 bare LF / 无 BOM，且 CRLF 增量**恰等于**行数增量。
- 🔴 **诚实边界**：本项修复在生产上**没有任何运行期证据**——清理器（`loop_runs` 每日 06:48）**从未执行过**，
  所以生产库里至今 0 行 TEXT 型 `duration_ms`。改动的影响面是「未来的某一次执行」。

### 53.23.2 改了什么

| | 改前 | 改后 |
|---|---|---|
| SQL 形态 | 一条 `UPDATE … WHERE status='running' AND started_at < ?` | 先 `SELECT id, started_at` 取候选行，再**逐行** `UPDATE … WHERE id=?` |
| `duration_ms` 的绑定 | `now`（**ISO 字符串**） | `_duration_ms(row["started_at"], now)`（**int 毫秒**） |
| 返回值 | `int(cur.rowcount or 0)` | `changed` 累加各行 `rowcount` |
| 其它列 | `status` / `error`(COALESCE) / `finished_at`(COALESCE) | **逐字保持**，语义不变 |
| docstring | 只说「gains `finished_at` … 和 `duration_ms`，可辨认」 | 说清那个数**是什么**（见 53.23.3），并记下这处缺陷**曾经存在** |

`writing()` 全程持 `_WRITE_LOCK` 且是一个事务（`sqlite_db.py:244-257`：成功 commit / 异常 rollback），
所以「SELECT 候选 → 逐行 UPDATE」之间不会被写者插队；候选数在正常态是 0。

### 53.23.3 那个数到底是什么 —— 不是「时长」，是「账本认为它在跑的那一段」

原实现把 `finished_at` 记成 reconcile 的那一刻，并在 docstring 里写明这是「它被发现的时刻」。既然
`finished_at` 已经这么定义了，`duration_ms` 只有跟着它算才**自洽**：这个行的 `(finished_at − started_at)`
就是「账本从 `started_at` 起一直以为它在 `running`，直到 reconcile 那一刻」——**这句话是真的**。
它不是「干了多少活」的度量：进程可能早就死了，所以它是**上界**，而这个上界的大小本身就是证据。
改后的 docstring 逐字写明了这一点，不再留模糊。

### 53.23.4 另外两种「更省事」的改法，为什么否掉

- **置 `NULL`**：「时长不知道」确实诚实，代价是同一列在同一张表里多出第三种值语义，而且 §53.21.5 赖以成立的
  「可辨认」论据要重述。更关键的是它把「上界」这个**有用的读数**丢掉了——一个几千小时的 `duration_ms`
  恰恰是「这行被遗忘了很久」的直接信号。
- **`CAST(? AS INTEGER)`**：把时间串 cast 成 `0`。`0` 会伪装成「跑了 0 毫秒的正常结束」，比 `NULL` **更坏**。

### 53.23.5 新测试钉住的是「种类 + 逐行」，不是「正数」

新增 `FailStaleRunningRowsTests.test_reconciled_rows_get_their_own_integer_duration`，插两行
`started_at` 相差**恰好 2 天**的 stale 行，断言三件事：

1. `typeof(duration_ms)` 对**两行都是** `integer`；
2. 每行 `duration_ms == int((finished_at − started_at).total_seconds()*1000)`（自洽性，确定性、不含墙钟）；
3. 两行的 `duration_ms` 之差**恰好 172800000 ms**。

第 3 条是**真正的承重项**：一个「把所有行写成同一个常量」的实现（比如恒定 `0`）能通过第 1、2 条里除
自洽性之外的部分，但过不了第 3 条。第 1 条则专治「正数断言漏过字符串」——
`assertGreater(duration_ms, 0)` 在 TEXT 上会因为 `'2026-…' < 0` 的隐式比较而**恰好通过**（§53.21.5 实测
TEXT 排在 INTEGER 之后），所以这里断言的是**种类**。

📌 记一次我自己的错误：第一次跑，第 3 条失败的读数是 `-172800000 != 172800000` —— 我把方向写反了
（`stale-2` 起得**更晚**，跨度应**少** 2 天）。这个「恰好 2 天」的差值本身反过来证明逐行计算是**精确**的，
不是近似的。断言已按正确方向修正。

### 53.23.6 四相变异验证（判据：junit 的 failures/errors）

| 相位 | 动作 | 读数 |
|---|---|---|
| ① 变异前 | `pytest tests/test_loop_run_store.py tests/test_scheduler.py` | junit `tests=63 failures=0 errors=0` |
| ② 变异后 | 字节级把 `_duration_ms(row["started_at"], now),` 换回 `now,`（唯一命中，`count==1`） | junit `tests=1 failures=1 errors=0`，症状 `{'stale-1': 'text', 'stale-2': 'text'}` |
| ③ 还原后 | 重跑该测试类 | junit `tests=6 failures=0 errors=0` |
| ④ 字节一致 | `sha256` 与原字节比 | `c70bef99ed0da3da` 一致，`identical_to_backup=True` |

变异脚本走**字节**（`.workbuddy/audit-2026-10-03/mutate_item1.py`，`read_bytes`/`write_bytes`），
不用 `read_text`——后者会把 CRLF 归一化成 LF，还原就成了一句谎。
🔑 **另一条前置检查**：`grep -n` 确认 `mutation_verify.py` 里**没有** `loop_run_store` 的针，
即本文件不在任何 `m.path` 名单里，故不必触发 `test_mutation_verify.py` 那张「过期针」清单。

### 53.23.7 字节证据

| 文件 | 改前 | 改后 | `git diff --numstat` | CRLF 增量 vs 行数增量 |
|---|---|---|---|---|
| `app/memory/loop_run_store.py` | 13015 B / 346 CRLF | 14442 B / 373 CRLF | `43 16` | 373−346 = **27** = 43−16 ✓ |
| `tests/test_loop_run_store.py` | 15985 B / 361 CRLF | 18082 B / 404 CRLF | `43 0` | 404−361 = **43** = 43−0 ✓ |

两个文件都 0 bare LF、无 BOM。测试文件是**纯插入型**（删除数为 **0**）；账本文件是**替换型**
（docstring 段落收窄 + 函数体重构），故删除数 16 是预期的，不适用「插入型必须 0 删除」那条。

### 53.23.8 对 §53.21.4 / §53.21.5 的重述（§0：前提被我自己的修复改了）

🔴 **§53.21.4 里「解药本可一次修好」的定语，已被我这次修复变成过期。** 该小节当时的判据是在**生产副本**上跑
那段**原样 SQL**，得到 `rowcount=127`、`running 127→0` —— **这个读数仍然成立**，它证明的是那条语句的
**语义正确且充分**。但「一次」描述的是**修复前**的解药形态（单条 `UPDATE`）；把 `duration_ms` 的类型缺陷
修掉之后，解药必然是「先 SELECT 再逐行 UPDATE」，**不再是一条语句**。⇒ 按 §0 **改窄、不翻案**：
「解药语义正确且充分，一条 SQL 就能覆盖全部 127 行，只差被执行；但要让写出的 `duration_ms` 与其它行
同类型，它必须逐行写。」

✅ **§53.21.5 从「潜伏」升级为「已修」**：那一小节说 *"opening the cure must fix this line first"* ——
本项就是把那一行先修掉了。生产的 0 行 TEXT 读数不变（生产未动，只读）。

### 53.23.9 未决 / 明确不做

- ⏳ **没有运行期证据**：本项无法在生产上验收，只能靠单测 + 变异。若要让它在生产上「被走到」，需要
  在某个受控环境实际触发一次 06:48 的维护任务——本项**不做**（属运维动作，不是代码交付）。
- ⏳ **`duration_ms` 为「上界」这一语义没有被任何前端消费者感知**：`scheduler-timeseries.tsx` 直接渲染数字。
  是否要在 UI 上区分「正常结束」与「reconcile 出来的行」——**未决**，属产品判断，不代决。
- ⛔ **不改**：`test_scheduler.py:832` 的 wiring 测试（它断言 `calls["fail"]` 的入参是字符串，那说的是
  **cutoff 参数**，与本列无关）；也不动 `finish_run`（它本来就写 integer）。
- ⏭️ 本项**不提交**：按 §0，等「按顺序做」全部完成后由老板定提交时机。

## 53.24 「按顺序做」第 2 项：`translate_titles` 的 try 泄漏已修、取消安全网**拒绝**、既有解药由「够不到」变「够得到」

改动面四个文件：`app/core/scheduler.py`、`app/main.py`、`app/memory/loop_run_store.py`（仅 docstring 改窄）、
两个测试文件。**未提交、未推送**；生产 `v2_loop.db` 全程只读（sha `35346d467fe24ce2` 前后一致）。

### 53.24.1 结论先行

- **① 已修**：`_job_translate_titles` 把 `list_all_events()` 移进 `try`。写入点普查复跑确认，全仓 **30 个**
  账本写入点里，这是**唯一**一处「`_start_run` 与 `try` 之间有语句、且函数内真有 `_finish_run`」的形态。
- **② 拒绝实现**「ContextVar + wrapper `finally` 取消安全网」。不是省事，是有证据：见 53.24.3。按 §4.3
  记为「**某条变异故意不存在**」，理由写进本节。
- **③ 接受并实现**：把**同一个**阈值判据的 reconcile 接到**启动路径**上。理由是实测的——那个每日 06:48 的
  cron **在本部署结构上不可能触发**（见 53.24.4）。判据一个字没改，只多了一个触发点。
- **顺带**：这道改动被仓库**既有守卫**当场拦下（`AssertionError: 4 != 3`），说明本仓对启动步骤有结构约束；同时暴露出
  那道守卫**只比计数**，删掉调用它照样绿 ⇒ 我另补了一条**正向**断言。
- **落这个改动前我先量了它的后果**（53.24.6）：`/api/health` 在本部署**本来就是 503 degraded**，
  127 行里只有 **1 行**是「某 job 的最新行」，所以净影响是 `failed_runs` 里**多一个名字**。

### 53.24.2 ① 泄漏点全仓只有一处

写入点普查（`.workbuddy/audit-2026-10-03/census_write_sites.py`，从 `git HEAD` 用 AST 读出）复跑，
30 个点里 `next_stmt != Try` 的共 7 处，逐条甄别：

| 位置 | `next_stmt` | 函数内有 `_finish_run`？ | 判定 |
|---|---|---|---|
| `scheduler.py:284` `_job_translate_titles` | `Assign`（`events = list_all_events()`） | 有 | 🔴 **真泄漏**（本项修掉） |
| `main.py:113` `_record_core_startup_outcome` | `<last>`（嵌套块的产物） | 有 | ⑫ 假阳性（§53.20.3 已记） |
| `world_cup_scoring_service.py:265` `score_all_finished_matches` | `Assign` | 有（但靠 `finally`） | 有 `try` 前的窄缝（`get_prediction_session()`）⇒ **留给第 6 项** |
| 4 个 `_start_audit_run` / `_start_consistency_repair_audit_run` | `<last>` | **无** | start-only helper，配对在调用方 ⇒ **留给第 6 项** |

**为什么把 `translated = 0` 留在 `try` 外**：它不抛（纯赋值），而 `list_all_events()` 会——把不抛的语句留在外面
是为了让 `try` 的第一句就是「唯一可能抛的那句」，读代码时一眼可见。

### 53.24.3 ② 拒绝「ContextVar + wrapper `finally`」——并绑定它的前提

`except Exception` 抓不到 `CancelledError`（后者继承 `BaseException`），所以「取消」确实会留下 `running` 行。
但**这条路径在部署里有多可达**决定了要不要为它加机械：

1. `stop_scheduler()` 走的是 `scheduler.shutdown(wait=True)`（`scheduler.py:1853`）。APScheduler 在 `wait=True`
   下**等**正在执行的任务结束，**不取消**它们。全仓 grep **没有** `timeout_graceful_shutdown`（uvicorn 默认
   无限等）。
2. 因此干净关停（SIGTERM / 一次 Ctrl-C）时，任务会被**允许跑完**，`_finish_run` 照常执行 —— 不产生孤儿。
3. 真正产生孤儿的是**硬杀**：第二次 Ctrl-C 触发的强制退出、SIGKILL、断电。这条路径上**没有任何 Python 代码会执行**
   ⇒ `finally` **救不了**。
4. 而数据支持的机制正是硬杀：§53.20 查明 120 个孤儿来自「一个约 10 分钟的 `event_discover` + 每次进程启动都触发的
   `now+30s` 任务 + 开发者每隔几分钟重启」。
5. ⇒ 一个**只覆盖「取消」这一窄分支、且该分支在被观测的机制里并非成因**的网，是投机机械；它还要动 **23 个
   `add_job` 注册点**。本仓对此已有既定判据风格：**先补用例、再把空头变成可登记的变异**，而不是先把测试写绿。

🔴 **这条结论的前提**（§4.1：前提变了结论会静默过期）：**部署保持现状**（无 `timeout_graceful_shutdown`、
shutdown 走 `wait=True`、任务时长 ≪ 关停预算）。若哪天改成 systemd 且 `TimeoutStopSec` 短于任务时长，
「取消」就变成可达的主路径，本节要**重估**。

⛔ 因此**不实现**；记为「某条变异故意不存在」，`rationale` = 上面 1–5。

### 53.24.4 ③ 解药为什么「够不到」：只读取证

只读打开生产账本（`mode=ro`，sha 前后 `35346d467fe24ce2`，1759 行 / 19 个 job 名）：

| job_name | 行数 | running | 首次 | 末次 |
|---|---:|---:|---|---|
| `world_cup_scoring_reconcile` | 1201 | 0 | 2026-06-29 | 2026-09-26 |
| `event_discover` | 224 | **120** | 2026-06-29 | **2026-07-25** |
| `translate_titles` | 160 | 2 | 2026-07-04 | 2026-09-10 |
| `loop_db_maintenance` | 8 | 0 | 2026-06-29 | **2026-07-24** |
| `optimization_task_cleanup` | 8 | 0 | 2026-06-29 | **2026-07-24** |
| `world_cup_prediction_update` | 8 | 0 | 2026-06-29 | **2026-07-24** |
| `event_auto_resolve` | 8 | 1 | 2026-06-30 | **2026-07-24** |
| `startup_prediction_db_init` | 8 | 0 | 2026-09-10 | 2026-09-26 |
| `event_discover_startup` | 6 | 4 | 2026-09-10 | 2026-09-10 |
| **`loop_run_ledger_maintenance`** | **0** | — | — | — |

两条读数决定了这一项：

1. **`loop_run_ledger_maintenance` 一行都没有。** 解药那个每日 06:48 的 cron **从未执行过一次**。
2. **所有固定 UTC 时刻的 cron 都在 2026-07-24/25 之后消失**（`loop_db_maintenance` 22:45、`optimization_task_cleanup`
   22:50、`world_cup_prediction_update` 22:00、`event_auto_resolve` 14:30），而 `event_discover`（07:15）也止于 07-25。
   ⇒ 这不是「偶尔错过一次」，是**进程生命周期以小时计**、固定钟点触发**结构上不可能**。

⇒ 与 §53.21.4 的升级连起来：解药**语义正确且充分**（§53.21 已在副本上跑出 `running 127→0`），缺的是**被执行**。

### 53.24.5 ③ 处置：接到启动路径，且不改判据

**先例就在库里**：`main.py` 的 lifespan 里已有 `optimization_task_manager.reconcile_interrupted_tasks()`
—— 它正是 `fail_stale_running_rows` 的 docstring 声明「**刻意不同于**」的那个兄弟实现（见 `main.py:300-310` 的注释
与 `loop_run_store` 的对比段），而它**就挂在启动路径上**。⇒ 「启动时 reconcile」在本仓是**既有架构**，不是新花样。

新增 `scheduler.reconcile_stale_running_runs()`，并在 lifespan 的启动序列里以**同样的 best-effort 形态**调用。
三个设计取舍：

- **复用同一条 store 函数与同一个阈值**（`LOOP_RUN_STALE_RUNNING_HOURS`，默认 36h）。判据不变 —— 这正是它能被
  **任何**进程安全调用的原因：本仓最长的 job 约 10 分钟，超过阈值的行**不可能有活着的属主**，无论它是本进程、
  调度器进程还是某个 API 请求写的。
- **只做 reconcile，不做 retention 删除**。删除不是正确性修复，每次启动都删是白折腾；保留留在 cron 里。
- **不动 `optimization_task_store`**（另一条线）。

🔴 **两处 docstring 按 §0 改窄、不覆盖**：
- `fail_stale_running_rows` 里 *"Threshold-based, not startup-based, unlike ``optimization_task_store.fail_interrupted_tasks``"*
  —— 那句话**关于判据仍然成立**，我保留了原句，另加一段说明「**判据**不变、它才是可被任何进程调用的原因；此后扩宽的是
  **触发点**」。
- `_job_loop_run_ledger_maintenance` 的两条 bullet 全保留，另加一段把「criterion 与 trigger 是两个决定」写清。

### 53.24.6 🔴 落这个改动前必须先回答：它会不会把 `/api/health` 从 200 变成**永久** 503？

`api_health`（`main.py:529-538`）的判据是：**某个 job 的「最新一行」`status == 'failed'` ⇒ degraded ⇒ 503**。
把 127 行从 `running` 改成 `failed` 显然会动这个谓词，而**并非每一行都会被读到**（只有每 job 最新的那行会）。
所以在生产账本的**副本**上实测（`.workbuddy/audit-2026-10-03/probe_health_effect.py`，原库 sha 前后不变）：

| 读数 | 值 |
|---|---|
| `running` 行 | 127 |
| 其中「是某 job 最新行」的 | **1**（`event_discover_startup`） |
| `failed_runs` **前** | `['world_cup_api_football_validate']` |
| `failed_runs` **后** | `['event_discover_startup', 'world_cup_api_football_validate']` |
| health degraded？ | **前 = 真，后 = 真** |
| `running` 行后 | 0 |

⇒ **净影响是 `failed_runs` 里多一个名字**，不是 200→503。而且：

⚠️ **顺带查出一件与本项无关的既存事实**：本部署的 `/api/health` **本来就已是 503 degraded**，原因是
`world_cup_api_football_validate` 那一行（唯一一行，2026-07-07，状态 `failed`，由 `api/routes/events.py:798`
写入）。**本项不调查它**，登记为待办。

📌 方法论：这一问本来可以靠「读代码推一推」回答，但**推不出那个 1/127**。**在副本上跑一次，比读十遍代码可靠**
（同 §53.21.6 的 ⑰ 判据）。

### 53.24.7 顺带：仓库既有守卫把我拦下了 —— 以及它的边界

第一次跑启动相关测试，`test_startup_health_integrity.py::StartupPartitionMatchesTheSourceTests::
test_every_guarded_startup_step_is_declared_in_exactly_one_set` 报：

```
AssertionError: 4 != 3 : a startup step is guarded by try/except but declared in
neither CORE_STARTUP_COMPONENTS nor OPTIONAL_STARTUP_COMPONENTS
```

这道守卫用 AST 扫 `lifespan` 的 `try` 块，把「调用 `_record_core_startup_outcome` 的」归 core、其余归 optional，
再与 `app/main.py` 的两个声明元组**比计数**。⇒ 本仓对启动步骤有**结构约束**：加一步必须登记。
我的步骤是 best-effort ⇒ 归 `OPTIONAL_STARTUP_COMPONENTS`，取名 `scheduler_run_reconcile`。

🔴 **但它的边界必须点明**：它**只比计数**，所以「把 `reconcile_stale_running_runs()` 调用删掉」它**照样绿**
（try 块还在、计数还对）—— 这是 §4.2 说的**负控假安全**。因此我在同一个文件里另加了一条**正向**断言：
启动前插一行 stale 的 `running` 行，启动后它**必须**变成 `failed`。删掉调用，这条会红（见 53.24.8 的 (b)）。

### 53.24.8 四相变异验证（三处改动分别验）

| 改动 | 变异 | 变异读数 | 还原 |
|---|---|---|---|
| (a) `try` 位置 | 字节级把 `events = list_all_events()` 挪回 `try` 之外 | junit `tests=1 failures=1 errors=0`，症状 = `OSError: event store unreadable` **穿透协程**（连 `except` 都没到，正是孤儿成因） | sha `bcb50541065e5268`，`identical_to_backup=True` |
| (b) 启动接线 | `abandoned = reconcile_stale_running_runs()` → `abandoned = 0`（**保留** try 块，故分区守卫仍绿） | junit `tests=1 failures=1 errors=0` | sha `0412931e95aa91ec`，`identical_to_backup=True` |
| (c) 分区登记 | **自然发生**（先加代码、后跑测试），非人为变异 | `AssertionError: 4 != 3` | — |

(a)/(b) 的脚本走字节（`mutate_item2a.py` / `mutate_item2b.py`），且都先断言针**唯一命中**（`count==1`）。
🔑 **前置检查**：`mutation_verify.py` 里没有 `loop_run_store` / `scheduler` 的针，本批文件不在任何 `m.path` 名单里，
故不必触发 `test_mutation_verify.py` 的「过期针」清单。

### 53.24.9 门与字节证据

四道门（改后）：`ruff check app/` 干净；`mypy app/` = *Success: no issues found in 331 source files*；
`generate_types --check` = *up to date*。

**受影响面回归**（不是抽样）：按「引用 `loop_run_store` / `app.core.scheduler` / `from app.main import`
的测试文件」筛出 **66 个文件**全部跑一遍，junit `tests=1278 failures=0 errors=0`（1051 passed + 227 subtests，
0 skipped）。这一批同时覆盖了 `test_startup_health_integrity` / `test_scheduler` / `test_loop_run_store` /
`test_events_routes` 等。

| 文件 | 字节（改后） | CRLF | `git diff --numstat` |
|---|---:|---:|---|
| `app/core/scheduler.py` | 83,344 | 1,900 | `45 1` |
| `app/main.py` | 28,147 | 594 | `19 0` |
| `app/memory/loop_run_store.py` | 14,950 | 381 | `51 16`（含第 1 项的 43/16） |
| `tests/test_scheduler.py` | 48,251 | 943 | `65 0` |
| `tests/test_startup_health_integrity.py` | 28,044 | 637 | `49 0` |

五个文件全部 **0 bare LF、无 BOM**。

### 53.24.10 未决 / 明确不做

- ⏳ **启动 reconcile 会把 §53.20 用作证据的那 127 行改写成 `failed`**（并给 `finished_at`/`duration_ms`）。
  那些证据已**固化在 §53.20 的表格与本节的 53.24.4 里**；但下一个看账本的人看到的将是「已被清理」的形态。
  这是**有意为之**（它就是修复），也是本条必须写明的原因。
- ⏳ **`world_cup_api_football_validate` 让本部署的健康探针长期 503**（53.24.6 的顺带发现）。本项**不调查**。
- ⏳ 第 6 项要处理 `world_cup_scoring_service.py:265` 的 `try` 前窄缝与 4 个 start-only helper。
- ⛔ **不实现**取消安全网（53.24.3 的 1–5），前提已绑定。
- ⛔ 不改 `optimization_task_store` / 不动 retention 删除。
- ⏭️ **不提交**：按 §0，等「按顺序做」全部完成后由老板定提交时机。

## 53.25 「按顺序做」第 3 项：`read_json` 的「Never raises」比实现宽一个异常类 —— 已对齐，并让 §53.18 记的那条窄缝**消失**

改动面两个文件：`app/utils/file_store.py`（两个函数 + docstring）、`tests/test_file_store.py`（两条新测试）。
**未提交、未推送**。

### 53.25.1 结论先行

- **已修**：`read_json` 与 `read_json_strict` 的「损坏」分支都收 `UnicodeDecodeError`。
- **修法有本仓先例，因此没有争议**：**15 个模块**已经在用 `except (UnicodeDecodeError, json.JSONDecodeError)`
  成对捕获 ⇒ 这是把一个**例外**拉回既有约定，不是发明新约定。
- docstring **改窄**（不是「继续写 Never raises 然后不管」）：写明覆盖集，并把**故意不覆盖**的那一类也写明。
- 🔴 **连带效果（比本项本身更重要）**：`read_json` 的读者包含 `event_store._load_unlocked` ⇒
  **`list_all_events()` 变成 total**，§53.18.1 说的「唯一的窄缝」**没了**。

### 53.25.2 影响面（先把读者数清）

| 函数 | 调用点 |
|---|---|
| `read_json`（宽松） | `event_cache.py:40`、`event_cache.py:55`、`event_store.py:30`（= `_load_unlocked`）、`sports_fact_service.py:65`、`sports_fact_service.py:146` |
| `read_json_strict`（严格） | `event_store.py:59`、`sports_fact_service.py:118`、`world_cup_data_source_service.py:126`、`world_cup_source_bundle.py:188` |

先例计数：`UnicodeDecodeError` 在 `app/` 出现 **19 处**，其中 **15 个文件**用的是与 `json.JSONDecodeError`
成对的写法（`football_live_*` 系列、`nba/nhl/mlb` 系列、`market_totals_service` 等）。⇒ 这些模块的作者
**默认**「非法 UTF-8 就是损坏」，只有 `read_json` 没跟上。

### 53.25.3 为什么把非法 UTF-8 归到「损坏」而不是「I/O」

- **语义**：不是本进程写出来的文件（`write_json_atomic` 永远写 UTF-8）⇒ 与 corrupt JSON 同类，而既有的
  「log error + quarantine + fallback」正是为这一类准备的。
- 若归到 `OSError` 那支（warning + fallback），就**丢掉了 quarantine** —— 运维再也看不到是哪个文件坏了。
- **明写不覆盖项**：在**打开**一个不是合法 OS 路径的字符串（内嵌 NUL、孤立代理对）时抛的 `ValueError` 仍然穿透。
  那是**参数**的错，不是内容的错。**故意不改**：把外层放宽到 `ValueError` 会把「测试里 patch 出来的程序错误」
  一并吞掉，那是拿真信号换假安全。

### 53.25.4 `read_json_strict` 那处是「半边改动」，可单独回退

- 它**本来就**会抛：`UnicodeDecodeError` 既不是 `OSError` 也不是 `JSONDecodeError`，直接穿透 ⇒
  「corrupt/OSError -> raise so the caller ABORTS」这个**承诺已经兑现**（行为本来就对）。
- 缺的只是**日志与 quarantine**。我把 `UnicodeDecodeError` 加进它的 corrupt 分支 ⇒ 仍然是 raise，
  只多一行 log + 一个 `.corrupt` 副本。
- ⚠️ 这属于**超出最小修法**的一步，理由是让两个函数对称。若认为「最小」更重要，这处（两行）可单独回退，
  不影响本项主结论。

### 53.25.5 新测试

| 测试 | 输入 | 断言 |
|---|---|---|
| `ReadJsonLenientTests::test_undecodable_bytes_degrade_like_corrupt_json` | `b'{"a": "\xff\xfe"}'` | 返回 fallback，**且**生成 `.corrupt` |
| `ReadJsonStrictTests::test_undecodable_bytes_abort_and_quarantine` | 同上 | 抛 `UnicodeDecodeError`，**且**生成 `.corrupt`（后者在我改动前**不成立**，见 53.25.4） |

两条都用 `write_bytes` 而不是 `write_text` —— 要的正是「非法 UTF-8 的文件」，而 `write_text` **写不出**它。
顺带把类 docstring 里那句 `read_json is for caches / read-only paths – never raises` 改成 `… – it degrades,
never raises`：原来的措辞就是那句过宽承诺的副本。

### 53.25.6 四相变异验证

| 相位 | 动作 | 读数 |
|---|---|---|
| ① 变异前 | `pytest tests/test_file_store.py tests/test_event_store.py` | junit `tests=89 failures=0 errors=0`（75 passed + 14 subtests） |
| ② 变异后 | 字节级把**宽松支**的 `except (json.JSONDecodeError, UnicodeDecodeError):` 换回 `except json.JSONDecodeError:` | junit `tests=1 failures=1 errors=0`，症状 = `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 7` **从 `read_json` 穿透** |
| ③ 还原后 | 重跑同两个文件 | junit `tests=89 failures=0 errors=0`（`junit-item3-restored.xml`） |
| ④ 字节一致 | sha256 | `dbff44185f472a29`，`identical_to_backup=True` |

📌 **我自己的一次错误，以及它被拦住的方式**：脚本初版断言「那行在文件里出现 **2** 次（宽松 + 严格）」，
实测只有 **1** 次 —— 因为严格支的 `except` 缩进是 **8 空格**（它的 `try` 在函数层），宽松支是 **12 空格**。
脚本据此直接 `sys.exit`，**一个字节都没写**。这正是 §4.3 那条「写入前必须全校验、未通过一个字节都不写」
在起作用：错误的**前置断言**没能污染被测文件。
🔑 **另一个前置检查**：`mutation_verify.py` 里没有 `file_store` 的针，本文件不在任何 `m.path` 名单里。

### 53.25.7 对 §53.18.1 / §53.18.2 的重述（§0：前提被我这次修复改了）

🔴 §53.18.1 的推理链是：`list_all_events()` → `_load_unlocked` → `read_json`，在探针下发现
**非法 UTF-8 会让 `read_json` 抛 `UnicodeDecodeError`**，而 `issubclass(UnicodeDecodeError, OSError)` = False，
故它同时逃过两层 handler ⇒ **「该路径在一个窄缝上非 total」**。

**这条缝现在没有了**：`read_json` 收了这个异常类。因此：
- **§53.18.2 记的「第四条：`read_json` 的实现比它的契约窄一个异常类」不再是缺陷**，改为「已对齐」。
- **§53.18.1 的结论仍然成立**（`translate_titles` 的两个孤儿照样存在、照样最可能是 `CancelledError`），
  但**置信度的来源变了**：此前「`CancelledError` 是首选」之所以只是**首选**，是因为还有那条窄缝可能背锅；
  现在**没有已知的异常路径**能让 `list_all_events()` 抛 ⇒ 候选集里只剩取消（与硬杀）。结论不变，
  「首选」升为「唯一候选」。
- ⚠️ 第 2 项的 ①（把 `list_all_events()` 移进 `try`）**不是因为这条缝才改的**：它防的是**任何**未来的抛出源
  （包括 `read_json` 契约之外的那类 `ValueError`）。两处修复互补，不重复。

### 53.25.8 门与字节证据

`ruff check app/utils/file_store.py tests/test_file_store.py` 干净；`mypy app/utils/file_store.py` = *no issues*；
`pytest tests/test_file_store.py tests/test_event_store.py` junit `tests=89 failures=0 errors=0`。

| 文件 | 字节 | CRLF | `git diff --numstat` |
|---|---:|---:|---|
| `app/utils/file_store.py` | 12,915 | 294 | `27 8` |
| `tests/test_file_store.py` | 9,988 | 204 | `26 1` |

两个文件均 **0 bare LF、无 BOM**。

### 53.25.9 未决 / 明确不做

- ⏳ **`read_json` 之外还有没有同类「契约比实现宽」的函数**，本项**没有普查**。已知同族候选是那些自带
  `except (TypeError, json.JSONDecodeError)` 的 store（`optimized_params_store.py:205/225`）——它们连
  `TypeError` 都收了，方向相反（更宽），值得单独看一眼。**登记为待办，不在本项。**
- ⛔ **不放宽外层到 `ValueError`**（理由见 53.25.3）。
- ⛔ 不改那 15 个已成对捕获的模块（它们本来就对）。
- ⏭️ **不提交**。

## 53.26 「按顺序做」第 4 项：把「哪些已注册 job 会被测试直接驱动」做成常设守卫（判据全部从源码派生）

新增一个测试文件，**不改生产代码**：`backend/tests/test_scheduler_job_test_reachability.py`。**未提交、未推送**。

### 53.26.1 结论先行

- 守卫的**两侧都从源码派生**：人口 = `app/core/scheduler.py` 里每个 `scheduler.add_job(...)` 的**第一个实参**（AST）；
  另一侧 = `tests/*.py` 里被**引用**的名字（也是 AST）。**只有豁免表**是手写的。
- 现状读数：**23 个注册 job 函数 / 22 个被测试命名 / 唯一例外 `_job_sentiment_refresh`**（在 `tests/` 里**零引用**）。
- 表格双向过期都会红：M1（出现驱动它的测试）与 M2（加一个假条目）——见 53.26.5。
- 顺带**确证了半净化窗口**是**当前**事实（不是历史）：见 53.26.6。

### 53.26.2 为什么既不能用字面量、也不能用正则

- **字面量扫描有假阴性**（§53.19.6）：测试若直接跑**真** job，就**不伪造任何 payload**，字面量扫描看不见它。
  §53.19 的 10 组三联行正是这种测试写的。
- **正则扫描有假阳性，而且我自己连踩两次**：
  1. `_job_[a-z0-9_]+` 把**测试方法名**当成了 job —— `_job_failed` / `_job_name` / `_job_0` /
     `_job_skips_when_disabled` … 全都不是 job 函数。
  2. 改成精确匹配后又用了 `(?<![\w.])`，把 `scheduler._job_backup_stores` 这种**属性访问**排除掉了
     ⇒ `_job_backup_stores` 被误判成「无人驱动」（实测：错判 1 个）。
- ⇒ 改用 AST：`ast.Attribute.attr` 收属性名（`scheduler._job_x` **正确计入**），而注释、文档字符串、
  字符串字面量天然**不计入**。两条都写成**控制断言**
  （`TestTheDerivationItself::test_the_identifier_walk_sees_calls_and_ignores_prose`）。

### 53.26.3 人口为什么按 `add_job` 的第一个实参取，而不是按 `_job_` 前缀

`_job_name_for_run` 也是 `_job_` 前缀，但**不是 job**——它是个查表 helper（`scheduler.py:266`）。
按前缀取人口，**这个仓今天就已经是错的**。⇒ 守卫里有一条专门钉这一点的控制断言
（`test_a_job_prefixed_helper_is_not_a_job`），并把它写进 `_JOB_PREFIXED_NON_JOBS`。

### 53.26.4 表格为什么是「豁免表」而不是「清单」

本仓既有形态就三种，全是**只声明例外 + 要求理由 + 双向过期检查**：
`conftest._RESET_EXEMPT`、`test_settings_redirect_census._REDIRECT_EXEMPT`、`CORE/OPTIONAL_STARTUP_COMPONENTS`。
⇒ 这里同样只声明「**没被测试命名的那 1 个**」，而不是列 22 行理由墙。

⛔ **明确不做**：不要求「每个 job 都必须有直接测试」。好几个 job 是靠它调用的 **service** 被覆盖的
（如 `score_all_finished_matches`），把「job 级覆盖」写成硬规则是**拿覆盖率政策冒充正确性规则**。
本守卫守的是「两侧仍然构成划分、且例外仍带理由」，让下一次普查从一个**已派生**的答案出发。

### 53.26.5 四相变异验证（两处，都不碰生产代码）

| 相位 | 动作 | 读数 |
|---|---|---|
| ① | `pytest tests/test_scheduler_job_test_reachability.py tests/test_no_assertionless_tests.py` | junit `tests=39 failures=0 errors=0`（21 passed + 18 subtests） |
| M1 | 在 `tests/` 下临时放一个引用 `scheduler._job_sentiment_refresh` 的文件 | 划分测试红，junit `tests=1 failures=1`；症状 = *"the set of registered jobs no test names changed"* |
| M1 还原 | 删除该临时文件 | `tests/` 文件清单与运行前**逐名相同** |
| M2 | 字节级往 `_NOT_DRIVEN_BY_TESTS` 里插一个假条目 `"_job_name_for_run": ""` | 悬空检查红 `tests=1 failures=1`；划分测试也红 `tests=1 failures=1` |
| ③ 还原后 | 重跑守卫 | junit `tests=6 failures=0 errors=0` |
| ④ 字节一致 | sha256 | `6387dfd42340592b`，`identical_to_backup=True` |

M1 是关键的那半边：**唯一那条例外声明**必须真的会在「有测试驱动它」时过期——否则「豁免表」就是一张
永远不会被检查的装饰。

### 53.26.6 🔴 半净化窗口：确证（只读取证）

| 位置 | `LOOP_DB_FILE` 重定向 | `LOG_FILE` 重定向 |
|---|---|---|
| `git show HEAD:backend/tests/conftest.py` | **有**（4 处，`9647ca2` 已提交） | **一处都没有** |
| 工作树 `backend/tests/conftest.py` | 有 | **有**（`git diff HEAD --stat` = `+69 −2`，未提交） |

⇒ 「账本已净化、日志未净化」这个**半净化窗口**是**当前**事实，不是历史遗留叙述。它的后果见 §53.19.5：
9 月的测试行写进了 `backend/logs/app.log*`，也正是「**日志行数 > 账本行数**」这条判据能成立的原因
（`log 5 vs ledger 3` —— 有调用者把写入口打了桩）。

⇒ 本项**只确证、不代决**：是否把那处重定向落成提交属交付时机（§0），与 `test_production_log_hermeticity.py`
一并由老板定。

### 53.26.7 🔴 我自己踩的一个工具坑，值得记下来

**Write 新建仓库内的 `.py` 会写成 LF。** 本仓工作树约定是 **CRLF**——同一目录下两个兄弟文件
（`test_settings_redirect_census.py`、`test_production_log_hermeticity.py`）都是纯 CRLF，只有我新建的这个是
**纯 LF**（0 CRLF / 199 bare LF）。

⇒ 新建后必须做一次**字节级** LF→CRLF 对齐（读 bytes → 归一 → 写 bytes），并把「**字节增量 == 行数**」当验收。
本次读数：CRLF `0 → 199`、bare LF `199 → 0`、字节增量 **199 == 199**、无 `\r\r\n`（没被重复插入回车）。

⚠️ 这个坑是**被变异脚本自己暴露的**：M2 第一次跑报 `needle count 0`，因为脚本的针带 CRLF 而文件是 LF。
**这个失败是好事**——它说明「针必须与被测文件的真实字节一致」这条纪律在自动生效（§4.3）。

### 53.26.8 门与字节证据

| 文件 | 状态 | 字节 | CRLF | bare LF | BOM |
|---|---|---:|---:|---:|---|
| `backend/tests/test_scheduler_job_test_reachability.py` | **未跟踪（新建）** | 9,193 | 199 | 0 | 无 |

`ruff check` 干净（该文件随 `tests/` 一起在门里）；`pytest` 见 53.26.5 的①③。

### 53.26.9 未决 / 明确不做

- ⏳ **非调度器侧的账本写入点没有进这道守卫**。§53.20 查到 30 个写入点 / 8 文件 / 4 类模块，本守卫只覆盖
  「调度器注册的 job 函数」这一侧。另外几侧（`api/routes/events.py:798`、`main.py:113`、
  5 个 service helper）要派生它们的人口需要解析「run 名实参是常量还是变量」，更重；**登记为待办，不在本项**。
- ⏳ `_job_sentiment_refresh` **确实没有任何直接覆盖**——本守卫把它**记下来**，但**不顺手补测试**：
  补测试属新增覆盖，不是本项范围。
- ⛔ 不把「每个 job 都要有直接测试」写成规则（理由见 53.26.4）。
- ⛔ 不代决 `LOG_FILE` 重定向的提交时机。
- ⏭️ **不提交**。

## 53.27 「按顺序做」第 5 项：`events.py:798` 那条 API 路由的「请求级中断保护」——**评估后拒绝**，并纠正我自己在 §53.21.6 记错的一个数

本项**零代码改动**（只读普查 + 探针脚本）。目的是把 §53.20.8 留下的「待判 #1」收口。

### 53.27.1 结论先行

- **拒绝**为 `validate_api_football_pipeline_route` 加取消/中断安全网。理由逐条实测，见 53.27.7。
- 🔴 **纠正 §53.21.6**：全仓 **0 / 31** 个写入点在 `finally` 里闭合账本行（我此前记的是「1」）。全仓唯一的那个
  `finally` 关的是**数据库 session**，不是账本行。
- 生产账本里那条唯一的行是**正面证据**：`try` **之后**的登记段**跑过，并且正确闭合**。

### 53.27.2 先定位：这条路由到底有没有 §53.17.4 那种泄漏形态

写入点普查的读数是 `next=Try` —— `start_run` 之后**紧跟** `try`，**没有** §53.17.4 那种「在 try 之前先跑一句」
的形态。所以真正要评估的只有两处：

- **(a)** `try` **之后**的登记段：`summary = _api_football_validation_run_summary(result)`、
  `status = "success" if result.get("ok") else "failed"`、`finish_run(...)`（`events.py:806-813`）。
- **(b)** `await asyncio.to_thread(validate_world_cup_api_football_pipeline)` 上的**取消**。

### 53.27.3 (a) 逐条实测：那段登记代码对 dict 输入是 **total** 的

按 ⑰「**跑一次比读一遍可靠**」，我没有读代码推断，而是把 11 个输入喂进 `_api_football_validation_run_summary`：

| 输入 | 结果 |
|---|---|
| `{"steps": None}` | OK（`step_count=0`） |
| `{"steps": [None, 1, "x", {"ok": False}, {"name": "fixture_fetch"}]}` | OK（`step_count=5`） |
| `{"coverage": None}` | OK |
| `{}` | OK |
| `{"ok": "yes"}` | OK |
| `{"steps": {"not": "a list"}}` | OK |
| `{"steps": [], "coverage": {...}}` | OK |
| `None` / `[]` / `"result"` / `5` | **`AttributeError: 'X' object has no attribute 'get'`** |

⇒ **7/7 畸形 dict 全不抛；只有非 dict 才抛。** 那条边界是**类型边界**，不是取值边界 ——
而 `validate_world_cup_api_football_pipeline` 的注解是 `-> dict[str, Any]`，由 **mypy 门**
（§0 四道门之一，实测 *no issues found in 331 source files*）守住。

### 53.27.4 生产证据：那段登记段**真的跑过**，而且闭合正确

| 字段 | 值 |
|---|---|
| `status` | `failed` |
| `started_at` / `finished_at` | 2026-07-07T17:46:46 / 17:46:48（跨 1.57 s） |
| `duration_ms` | `1568`（**int**） |
| `error` | `API-Football returned 0 fixtures for league=1 season=2026; …` |
| `result_json` | 完整 8 键摘要（`provider`/`ok`/`failed_step`/`fixture_count`/`step_count` …） |

`error` 是**验证器自己产的结构化错误串**，走的是 `result.ok == False` 那一条 —— 即 `try` **之后**的
`finish_run`。**不是** `except` 分支：后者的错误串格式是
`API-Football pipeline validation failed: <异常类名>`，在账本里**从未出现过**。

⇒ 两件事同时成立：**(a) 段可达且有真实落地证据；(a) 段在那次真实输入上是正确的。**

### 53.27.5 (b) 取消：与第 2 项**同一套前提、同一套结论**

`stop_scheduler()` → `scheduler.shutdown(wait=True)`、全仓无 `timeout_graceful_shutdown`、
「第二次 Ctrl-C / SIGKILL 上 Python 一行都不跑」这三条对 HTTP 处理器同样成立（uvicorn 默认也是无限等）。
⇒ 取消需要**强制关停**才能到达，而那种情况下**没有 Python 代码可执行**，`finally` 救不了。

🔴 **前提绑定照旧**（§4.1）：若关停预算被改成短于请求时长，取消变成主路径，本节要**重估**。

### 53.27.6 🔴 纠正 §53.21.6：「`finally` 只有 1 个」是错的，实际是 **0 个**

用 AST 对 `app/**/*.py` 普查（判据落在 `ast.Try.finalbody` 里**有没有调用** `finish_run`/`_finish_run`）：

| 读数 | 值 |
|---|---|
| 含 `start_run` / `_start_run` 调用的函数 | **31** |
| 其中 `finish_run` / `_finish_run` 出现在 `finally` 块里的 | **0** |

那个曾被我算作「1」的 `finally` 是 `world_cup_scoring_service.py:329`，它执行的是
`close_prediction_session(session)` —— 关的是**数据库 session**，**不是账本行**。

⇒ 结论**变强**，不是变弱：§53.17.4 的「取消不可免疫」不是「29/30 缺」，而是 **31/31 全缺**；
第 2 项里「加一张网等于引入本仓**没有**的先例」这个论证因此**更硬**。

📌 **这是我自己的错，根因值得记**：我把「函数里有 `finally`」当成了「`finally` 里闭合账本行」。
**判据必须落在 `finally.finalbody` 的内容上，而不是「函数里有没有 `finally`」**——同族错误在本仓已出现过
（⑫ 的 `next_stmt` 只看外层语句）。已把这条写进 skill。

### 53.27.7 为什么最终**拒绝**

1. **没有泄漏形态**：`start_run` 后紧跟 `try`（53.27.2）。
2. **(a) 不可达**：对声明的返回类型是 total 的（7/7），边界由静态门守住，且有真实落地证据（53.27.3 / 53.27.4）。
3. **(b) 与第 2 项同一前提**，同一结论（53.27.5）。
4. 本仓**没有任何** `finally` 闭合账本行的先例（53.27.6）⇒ 在这条路由里加一张，是**为假设引入新约定**。
5. 另一种更「顺眼」的改法——**把登记段搬进 `try`**——我**算过账**：若 `finish_run` 在 `try` 内失败，
   会被同一个 `except` 再捕获 → 再调一次 `finish_run`（同样失败，因为库坏了）→ 客户端拿到的是账本错误、
   而行**仍然**是 `running`。**结果与今天逐字相同**，只是内部多走一次、错误语义更混、异常链更难看。
   **净收益为零。**

### 53.27.8 登记但不修的一处观察

这条路由（以及普查出的另外 26 个函数）调用 `loop_run_store.finish_run` **没有保护**，而调度器的
`_finish_run` 是**有**保护的（`scheduler.py:183-196` 用 `try/except` 包住并降级为日志）。
⚠️ **不可在进程内修**：`finish_run` 失败意味着**账本库本身**坏了，没有任何重试能写出那一行。
⇒ 记为观察，**不改**（「知道但不动」比「加一层假保护」诚实）。

### 53.27.9 未决 / 明确不做

- ⏳ 「`finish_run` 无保护」这条观察**留给运维层**：账本库损坏时唯一能做的是一次修复/搬迁，
  不是代码里的重试。
- ⛔ 不为这条路由加取消网（53.27.7）。
- ⛔ 不改 `except Exception` 把编程错误标成「pipeline validation failed」这个既存语义（超出本项范围）。
- ⏭️ **不提交**。

## 53.28 「按顺序做」第 6 项：`score_all_finished_matches` 的「开行到开 session」走廊已闭合；4 个 start-only helper 的配对标 —— **存在但静态不可达，不改**

### 53.28.1 本项的两个子目标

- **③** `world_cup_scoring_service.score_all_finished_matches`：`get_prediction_session()` 落在 `try:` **之前** ⇒
  该走廊里的失败会留下一行**永久** `running` 的账本行。
- **④** 4 个「只开不闭」的 start-only helper（`_start_audit_run` / `_start_consistency_repair_audit_run`）：
  与它们配对的 `_finish_*` **在不在调用方**、两者之间的缝隙**可不可达**。

### 53.28.2 ③ 的判据：为什么这是个真缺陷（不是「理论上可能」）

三件事要同时成立才算缺陷，逐条核对：

1. **那一句真能抛。** `get_prediction_session()`（`utils/prediction_db.py:58-73`）在 `_SessionLocal is None` 时走
   `get_prediction_engine()`（`:27-49`），后者含 `db_path.parent.mkdir(parents=True, exist_ok=True)` 与
   `create_engine(...)`（连接时设 `PRAGMA journal_mode=WAL`）⇒ **可抛 `OSError`**（权限、路径、ENOSPC、只读挂载）。
2. **行已经开了。** `:265` 已经 `loop_run_store.start_run(SCORING_RECONCILE_AUDIT_JOB_NAME)`。
3. **没人能闭合它。** 该函数的 `except`（`:317-327`）与 `finally`（`:329-330`）在那一句之后才进入；
   `finally` 只 `close_prediction_session(session)` —— 关的是 **DB session**，**不是账本行**。

⇒ `session` 打开失败时，`except` **够不到**（异常发生在 `try` 之前），行停在 `running`，
而 `/api/health` 判据是「每个 job 的**最新**行 `status == 'failed'`」⇒ 这行会**永久**占据该 job 的最新行位置。

### 53.28.3 改法：为什么不是「把 open 包进一个嵌套 try」

改后的形状：

```python
    session: Session | None = None
    try:
        session = get_prediction_session()
        ...
    except Exception as exc:
        ...  loop_run_store.finish_run(run_id, "failed", ...)
        raise
    finally:
        if session is not None:
            close_prediction_session(session)
```

三个决定各有理由：

- **搬进 `try` 而不是加嵌套 `try`。** 嵌套 `try` 只是把异常换到别处接住，那一行照样留 `running`；
  正确解是让**同一个** `except` 看得见它 —— 而那个 `except` 已经会把行置 `failed` 并 `raise`。
- **必须预绑定 `session = None`。** `finally` 是**无条件**执行的；若 `session` 首次赋值就在 `try` 内且该赋值失败，
  `finally` 会去读一个未绑定的局部名 ⇒ `NameError` 会把 `OSError` **换掉**，比原缺陷更难查。
  （本仓已有同类教训：`from X import f` 是按值绑定、`except` 里的 `NameError` 让人误判失败原因。）
- **不用 `contextlib.closing` / 不重构成 with。** 本函数要把 session **借给** `score_finished_match(match_id, session=session)`；
  该内层函数自带 `should_close = session is None` 的「借不关」约定（`:92-94`），改成 with 会同时改掉两处风格且无收益。

### 53.28.4 改动读数

| 项 | 值 |
|---|---|
| 文件 sha256[:16] | `9f849ae984b01c5c` |
| 字节 / CRLF / bare LF / BOM | 12,667 / **339** / **0** / 无 |
| `git diff --numstat` | `11 2` |
| 第一段 | +9 −1（6 行说明注释 + `session: Session \| None = None` + `session = ...` + 空行；删掉原来那行裸赋值） |
| 第二段 | +2 −1（`finally` 加 `if session is not None:` 一层） |

🔴 **这里的「删除数 = 2」是预期，不是缺陷。** §0 那条「插入型编辑的删除数必须是 0」只约束**只加注释**的编辑；
本项是**真搬移代码**（一行赋值挪进 `try`、一行调用挪进 `if`），删除正是搬移的痕迹。
反过来用它做**自查**：删除数若 > 2 就说明我多删了东西（`11 2` 与手工逐段核对一致）。

### 53.28.5 新测试钉住什么

`tests/test_world_cup_scoring_service.py::WorldCupScoringServiceTests::test_a_failing_session_open_still_fails_the_ledger_row`

- 输入：`patch(get_prediction_session, side_effect=OSError("cannot open prediction DB"))`。
- 断言四条：① `assertRaises(OSError)`（异常**照旧**穿透，没有被吞成 `None` 或 `{}`）；
  ② `loop_run_store.last_run(SCORING_RECONCILE_AUDIT_JOB_NAME)["status"] == "failed"`；
  ③ `error == "Scoring reconciliation failed"`；④ `finished_at is not None`。
- 🔑 **故意不 patch `close_prediction_session`。** `close_prediction_session(None)` 会执行 `None.close()`
  ⇒ `AttributeError`（`prediction_db.py:92-94`）。不 patch 这一处，就等于让**同一条**测试顺手把
  `if session is not None` 那层守卫也钉住（见 53.28.6 的 M2）。
  这是有意的「一条测试两个边界」，代价是它**只**在守卫缺失时以 `AttributeError` 失败 —— 症状与 M1 不同，可区分。

| 项 | 值 |
|---|---|
| 文件 sha256[:16] | `15cc9898ddb88e18` |
| 字节 / CRLF / bare LF / BOM | 6,025 / **160** / **0** / 无 |
| `git diff --numstat` | `24 0`（纯插入，删除数为 0 —— 与 53.28.4 相反，因为这里只加了测试） |

### 53.28.6 四相变异验证（两条**不同**边界各变异一次）

脚本 `.workbuddy/audit-2026-10-03/mutate_item6.py`。判据取 **junit 的 `failures`/`errors`**，不看进程退出码。

| 相 | 动作 | 读数 |
|---|---|---|
| phase0 | 针命中数 | M1 = 1、M2 = 1；sha `9f849ae984b01c5c`；339 CRLF / 0 bare LF |
| phase1 | 未变异跑 | `tests=3 failures=0 errors=0` ✅ |
| **M1** | session 打开移回 `try` 外（`session: Session \| None = get_prediction_session()`）→ `delta_bytes = -24` | `AssertionError: 'running' != 'failed'` 🔴 |
| **M2** | 删掉 `if session is not None:` → `delta_bytes = -37` | `AttributeError: 'NoneType' object has no attribute 'close'`（`prediction_db.py:94`）🔴 |
| phase3 | 还原 | sha 与备份**逐字节相同**；339 CRLF / 0 bare LF；重跑 `tests=3 failures=0 errors=0` ✅ |

🔑 **两条变异的症状不同**（M1 是「状态错」、M2 是「守卫缺失」）⇒ 证明这确实是一条测试覆盖**两个**边界，
而不是一条弱断言同时被两个变异「碰巧」触发。若两条变异给出同一个断言消息，就该怀疑测试只钉住了一处。

⚠️ **两次自力踩坑，都记下来**：

1. M1 的针首版**少了 `try:` 之后那个空行**（我自己编辑时留了空行，写针时按直觉省了）⇒ `needle count 0`。
   脚本在**写入前** `sys.exit`，**一个字节都没写** —— 4.3「写入前全校验、未通过一个字节都不写」第 N 次生效。
   教训：**针要从实际字节里生成，不要靠回忆**（`read_bytes()` 打印目标区域，或直接 `count()` 预检）。
2. 我用 heredoc 做「独立复核」时，`b"...\r\n..."` 被 shell 变成字面 `/r/n`（源文件里是 `\` + `r`，
   heredoc 之后成了 `/r/n`）⇒ 复核脚本自己给出了假读数。**含转义序列的东西一律写成文件再跑，不走 heredoc。**
   （本机长期记忆里已有这条，这次是它第二次咬我，说明我对「只是查一下」的临时命令太随意。）

### 53.28.7 四道门

| 门 | 结果 |
|---|---|
| `ruff check app/` | All checks passed |
| `mypy app/` | Success: no issues found in **331** source files（`session: Session \| None` 的窄化被接受，`session.query(...)` 处不报 Optional） |
| `python -m scripts.generate_types --check` | `generated-types.ts is up to date` |
| 受影响面 pytest | junit `tests=158 failures=0 errors=0`（`137 passed + 21 subtests`） |

受影响面的**选法**（不是随手挑的）：全仓 grep/AST 出**唯一**会 import 或 patch `score_all_finished_matches` 的四个测试文件 ——
`test_world_cup_scoring_service.py`（本函数）、`test_operational_readiness.py:414`（patch）、
`test_startup_health_integrity.py:227`（patch，启动路径）、`test_world_cup_analytics_routes.py:985`（patch，路由）。
除这 4 个之外另有 2 个**生产**调用点（`world_cup_analytics.py:493` 路由、`main.py:363` 启动）已被这 4 个文件覆盖。

### 53.28.8 ④ 的取证：4 个 start-only helper 的配对**都在调用方**，缝隙只有 2 处且**静态不可达**

**先说配对形态（4/4 都在调用方，两个变体）：**

| 文件 | 配对实现 |
|---|---|
| `world_cup_post_match_backfill_service.py:192-196` | 调用方内嵌 `def finish(result)` → `_finish_audit_run(run_id, result)` |
| `world_cup_quality_service.py:655-659` | 同上（`_finish_consistency_repair_audit_run`） |
| `world_cup_result_fact_backfill_service.py:62-66` | 同上（`_finish_audit_run`） |
| `world_cup_verified_result_correction_service.py` | 模块级 `_finish(run_id, result)`（`:304-`），在 `try` 内 `:74/86/125/135` 等处调用 |

四个 `_finish_*` 的签名一律 `run_id: str | None` + `if not run_id: return` + **内部 `try/except` 降级为日志**
⇒ **不存在「开了行但没人能闭」的形态**，也不存在「闭行本身抛出去」的形态。

**再说缝隙**（判据：`run_id = _start_*` 与 `try:` 之间**能抛的语句**）：

| 文件 | `run_id` 行 | `try:` 行 | 中间的内容 | 判断 |
|---|---|---|---|---|
| `world_cup_verified_result_correction_service.py` | 68 | **69** | 无 | **无缝隙** |
| `world_cup_result_fact_backfill_service.py` | 60 | 68 | `def finish` 的定义 | 定义函数**不可能抛** |
| `world_cup_post_match_backfill_service.py` | 189 | 198 | `normalize_audit_metadata(...)` + `def finish` | **理论可抛**（见下） |
| `world_cup_quality_service.py` | 652 | 663 | 同上 + `protected = not dry_run and not confirm` | **理论可抛**（见下） |

**那两处「理论可抛」为什么不可达 —— 三重闸门：**

1. `normalize_audit_metadata`（`services/audit_metadata.py:9-28`）只在传入**真值且非 Mapping** 时，
   于 `:17` 的 `metadata.get(...)` 抛 `AttributeError`。传 `None` 或 `{}` 都安全（`:16` 的 `metadata or {}`）。
2. **HTTP 层没有输入通道。** 5 个相关路由一律 `audit_metadata: dict[str, str] = Depends(get_audit_metadata)`
   （`world_cup_analytics.py:438 / 474 / 490 / 532 / 547`），而 `get_audit_metadata`（`api/audit_metadata.py:12-24`）
   **就地**用 header（`X-Client-Source` / `X-Operator`）加 `request.url.path` 构造一个全新的 `dict[str, str]`。
   客户端**只能塞字符串值，不能改类型**。
3. **静态类型也关掉了它。** 4 个服务函数的参数都标注 `dict[str, Any] | None`
   （`:177` / `:645` / `:45` / `:34`）⇒ 传 list/tuple 是 `mypy` 会报的类型错误。

🔑 **② + ③ 合起来意味着：这不是「实践中够不到」，而是「类型系统关掉了」。**
与前面几项「可达但没人走」的走廊**不同类** —— 那些是运行期可达、只是没有入口；这两处连静态都过不去。
⇒ **登记为观察，不改。** 加固方式（把 `audit_meta` 提到 `_start_*` 之前）是纯防御性重排，
收益是「让一个静态不可达的缝隙变成结构性不存在」，成本是动 2 个生产文件且**没有任何测试信号**能区分改前改后。

### 53.28.9 顺带发现的一处**排序不对称**（值得记、不值得改）

同一批 4 个文件里，`audit_meta = normalize_audit_metadata(...)` 相对 `_start_audit_run()` 的位置**不一致**：

- **先算元数据、后开行**（`:59` → `:60`；`:44` → `:68`）：`world_cup_result_fact_backfill_service.py`、
  `world_cup_verified_result_correction_service.py`。
  ⇒ 即便元数据那一步真能抛，那时**行还不存在**，**不可能**产生孤儿。
- **先开行、后算元数据**（`:189` → `:190`；`:652` → `:653`）：`world_cup_post_match_backfill_service.py`、
  `world_cup_quality_service.py`。⇒ 缝隙正是**这个顺序**造出来的。

🔑 也就是说：**缝隙不是「有元数据要算」造成的，是「先开行后算」这个顺序造成的**，而这个顺序本身没有正当理由。
两处「顺序更好」的实现（先算后开）看起来是**碰巧**，不是设计。⛔ 不改：见 53.28.8 的成本/收益。

**另附一条确认（反面走廊不存在）**：这 4 个函数**都**在开账本行**之前**打开 DB session
（`:187`→`:189`、`:650`→`:652`、`:55`→`:60`、`:66`→`:68`）⇒ 53.28.2 那种「行已开、session 打不开」的走廊
在这 4 处**结构上不存在**。那两处把 session 打开放在 try 之前（`:187` 等）也不构成缺陷，因为**那时行还没开**。
🔑 **「session 打开在 try 之前」本身不是缺陷；「session 打开在 *开行* 之后、*try* 之前」才是。** 判据是**三者的相对顺序**，不是单独某一处的位置。

### 53.28.10 未决 / 明确不做

- ⏭️ `audit_meta` 与 `_start_*` 的**顺序统一**（4 个文件里 2 反 2 正）—— 记为观察，理由见 53.28.9。
  ⚠️ 若将来有人要给这 4 个函数加「真的会抛」的语句，**必须**同时把顺序改成「先算后开」。
- ⛔ 不给那 2 处缝隙加 try（53.28.8）。
- ⛔ 不把 `_finish_audit_run` 的 `try/except` 去掉（它是**故意**降级为日志的，与调度器 `_finish_run` 同族）。
- ⏭️ **第 7 项（TaskCreate #13，§53.22.3）移交下一轮**：`evidence` 会话库「保留最近 N 个会话 / 声明它是什么 /
  先导出再删除」三选一，或至少在代码里声明证据仓语义 + 风险声明。
- ⏭️ **不提交**。

## 53.29 「按顺序做」第 7 项：把「一个不声明自己是证据仓的证据仓」变成**被声明的**（在代码里声明，不删任何东西）

### 53.29.1 结论先行

- §53.22 诊断的是**声明**，不是保留量。§53.22.3 留的三选一里选 **(b) 在代码里声明**，并把它做成**可被守卫的行为**，不是注释。
- **一个字节都没删。** 本项**不做**任何清理（理由见 53.29.6）。
- `tests/conftest.py` 现在在沙箱目录里写一份自述清单 `READ-ME-BEFORE-DELETING.txt`，写于**第一条测试能跑之前**（import 时）⇒ **会话中途被硬杀也把答案留在原地**。
- 新增守卫 `tests/test_test_sandbox_is_declared.py`（6 条）。它钉住清单的**机器可读字段**，并把每个字段对照**清单之外**的东西校验（细节见 53.29.5）。

### 53.29.2 为什么选 (b)，而不是「保留最近 N」或「先导出再删除」

- **(a) 保留最近 N 个会话**：既含**删除动作**、又含**保留策略**，两者都是操作员的取值决定（§0「业务/契约取值不代决」）；而且它的落地形态必然是**批量删系统临时目录里的一族目录** ⇒ 属 vague cleanup，本仓既定做法是**不做**（同族先例：§1 那 51 个 MIXED 文件「是否规范化需拍板，别擅自批量改」）。
- **(c) 先导出再删除**：导出到哪、留多久、删什么，同样要先定策略；更关键的是它**没有解决**本题的问题 —— 一个已经被导出的目录，在目录列表里**照样**不可解释。
- **(b) 之所以是正确的最小项**：§5 已把问题定成「**这个目录是什么**没有对外声明」，而 (b) **正好**是这条判据的解。它不改保留量、不动一个字节，却让「我能不能删这个？」在**提问发生的地方**（那个目录里）有答案。
- 一句话：**(a)/(c) 是策略，(b) 是缺失的判据。** 先补判据，策略留给操作员。

### 53.29.3 改了什么（`tests/conftest.py` 三个动作）

1. **抽出前缀常量** `_SANDBOX_PREFIX = "pmrf_test_data_"`，`tempfile.mkdtemp(prefix=_SANDBOX_PREFIX)` 用它。
   ⇒ 清单里那条「回收用 glob」**由构造保证**与目录名同源，不是抄第二遍（抄一遍就会漂）。
2. **新增 `sandbox_manifest_text(sandbox_dir, ledger_path) -> str`**：清单文本是**它参数的函数** —— 既不读全局、更不是硬编码模板。
   ⇒ 守卫可以用一个**合成目录**调用它并看见文本跟着变（53.29.5 的正向控制就靠这一点）。
3. **新增 `_write_sandbox_manifest(...)`，在所有重定向完成之后调用一次**（`best-effort`：`except OSError: pass`）。
   放在那里是因为清单要能报出**真实被重定向的账本路径**；而**不**放在 session 收尾夹具里，因为那恰好是「硬杀就丢」的位置 —— 写在这里，正是为了让「进程被杀死」这种最需要答案的情况也留得下答案。

### 53.29.4 清单长什么样（前 5 行是机器可读块）

```
PMRF TEST SANDBOX -- read this before assuming the directory is disposable.

sandbox_dir : <本目录绝对路径>
evidence_ledger : <本目录里的 v2_loop.db>
created_utc : <ISO-8601，UTC 偏移>
pid : <写它的进程号>
reclaim_glob : pmrf_test_data_*
```

其后是给人读的散文五段：**这是什么** / **这不是什么（不是缓存、不会被自动清理）** / **为什么保留（它是证据仓，点名审计档与 §53.22）** / **保留的成本（≈3 MB/天、≈44 目录/天）** / **怎么回收空间**。

⚠️ **回收配方刻意写成「先列出来、再删一份复核过的显式清单」并保留 `-WhatIf`，不写通配符直删。**
一个**失配**的清理配方比没有配方更糟 —— 它读起来像**已经核对过的许可**。这一点正是守卫要钉住的东西（53.29.5 最后两行）。

### 53.29.5 守卫钉住什么（每条判据都来自清单**之外**）

| 断言 | 对照物 | 为什么 |
|---|---|---|
| 清单文件存在 | 行为本体 | conftest **真的**在声明，而不是文档里说它在声明 |
| `sandbox_dir` **等于**实际沙箱 | `conftest._TEST_DATA_DIR` | 清单没在描述**另一个**沙箱 |
| `evidence_ledger` **等于**实际账本，且必须在沙箱**内** | `settings.LOOP_DB_FILE` | 被声明的「证据」就是这仓里的证据 |
| `pid` **等于** `os.getpid()` | 本进程 | 🔑 **上一次会话留下的清单不能通过** —— 沙箱按设计比进程活得久，所以「文件在」本身**不是**「还有人在写它」 |
| `created_utc` 可解析、且偏移为 0 | — | 配方按 mtime 排序 ⇒ 时间戳得说清自己是哪个时区 |
| `reclaim_glob` 去掉 `*` 后是**本目录名的前缀** | 目录名本身 | 🔑 配方失配（改了前缀、忘了改配方）**必须**红 |
| 清单点名审计档文件名 + `53.22` | 文档文件名 | 「这是证据仓」这句断言背后得有东西；这也是读者该从哪一节开始 |
| **正向控制**：用合成目录调用 `sandbox_manifest_text`，文本跟着变、且**不含**真实沙箱路径 | 传进去的参数 | 否则把函数换成硬编码字符串，上表**全部**仍然绿 |

🔴 **最后一条是本模块「不空」的唯一原因。** 真实沙箱只有一个，所以「清单与沙箱一致」在硬编码实现下是**恒真**的；只有**换一个参数**才能把硬编码暴露出来。
这也是 §6a-3「一条测试覆盖两个边界」的反面用法：**一个模块里必须至少有一条断言，其真值取决于实现是"算出来的"还是"印上去的"。**

### 53.29.6 明确不做

- ⛔ **不删任何目录。** 既有的 `pmrf_test_data_*` 一个没动（本项开工时 178 个，跑完几轮后 204 个 —— 这个增长正是本项要让它**可见**的东西）；更早会话的 35 个散件同样只报不删（§53.22.3）。
- ⛔ **不加「保留最近 N」开关**：它是操作员的策略取值，且落地形态一定是「批量删临时目录」。
- ⛔ **不把回收配方写成代码去执行** —— 配方**只作为文本**存在于清单中，供人复核后自行运行。
- ⏭️ **不提交。**

### 53.29.7 四相变异验证（两条边界各一次）

脚本 `.workbuddy/audit-2026-10-03/mutate_item7.py`。判据取 **junit 的 `failures`/`errors`**，不看进程退出码。

| 相 | 动作 | 读数 |
|---|---|---|
| phase0 | 针 M1 / M2 各命中 **1** 次；sha `f5344be868fcfe10`；584 CRLF / 0 bare LF | ✅ |
| phase1 | 未变异跑 | `tests=6 failures=0 errors=0` ✅ |
| **M1** | 删掉 `_write_sandbox_manifest(...)` 调用（`delta_bytes = -70`） | **6 条里 5 条红**；症状 = `...READ-ME-BEFORE-DELETING.txt is missing` + 4 × `FileNotFoundError` 🔴 |
| **M2** | 把 `reclaim_glob` 从 `{_SANDBOX_PREFIX}*` 换成字面量 `scratch_*`（`delta_bytes = -10`） | **1 条红**，症状 = `the manifest tells the reader to reclaim 'scratch_*' but this sandbox is named 'pmrf_...'` 🔴 |
| M2 对照 | 同一次变异下**只**跑「清单存在」那条 | **绿** ⇒ 变异是**局部**的，不是碰巧打翻一片 ✅ |
| phase3 | 还原 | sha 与备份**逐字节相同**；584 CRLF / 0 bare LF；重跑 `tests=6 failures=0 errors=0` ✅ |

🔑 两条变异**症状不同**（缺文件 vs 配方失配）⇒ 这是**两个**边界，不是一条弱断言被两次触发（§6a-3 的判据）。
🔑 **M1 里那 1 条保持绿的是正向控制**，它本来就不读沙箱清单（它读的是**传进去的参数**）—— 整片红里唯一还能区分「算法 vs 硬编码」的那一条没被一起打掉，这正好从反面证明控制项是独立的。

### 53.29.8 与 §53.22 的关系（§0：这是一条「前提被改」的重述）

- §53.22.2 记的是「**这是一个取舍，不是纯缺陷**」；§53.22.3 记的是「**未决（改动，留拍板）**：53.22.2 的三个选项」。
- 本节把其中一项**落地**（选项 b），因此：
  - **「未决」那条现在只剩 (a)/(c)**；
  - **「取舍」这个结论不变** —— 磁盘仍按 ≈3 MB/天增长，因为本项**没有**触碰保留策略。
- 🔴 **本项不声称解决了增长。** 它解决的是**可解释性**：目录列表里那个 `pmrf_test_data_xxxx` 现在自带答案。将来真要回收空间时，配方在目录里、且**被判据保证仍然匹配**。

### 53.29.9 门与字节证据

| 门 | 结果 |
|---|---|
| `ruff check app/` | All checks passed |
| `ruff check tests/test_test_sandbox_is_declared.py tests/conftest.py` | **1 个 F401，在 HEAD 就存在**：`import app.core.config as _config_module`（conftest.py:131）。**我未触及它**（`git diff` 里它只出现在 hunk 头部，不是改动行）；且 `tests/` 不在项目 lint 门内（`ruff check tests/` 在本项之前就有 324 个既有错误）。新测试文件 **0 错**。 |
| `mypy app/` | Success: no issues found in 331 source files |
| `python -m scripts.generate_types --check` | `generated-types.ts is up to date` |
| 后端全量 pytest（`tests/`，本项影响面**就是全集**） | junit `tests=7672 failures=0 errors=0 skipped=11`（1329.6 s）|

🔴 **`conftest.py` 的 `git diff --numstat` 是 `173 3`，但其中只有 `+104 −1` 是本项的。**
该文件在**本项开工之前**就带着未提交的重定向工作（`EVENT_AUDIT_FILE` / `SCHEDULER_LOCK_FILE` / `LOG_FILE`）。
**分解方式**（`.workbuddy/audit-2026-10-03/split_conftest_diff.py`）：把本项的三处编辑**逐字节反向**得到一个 probe，再 `git diff --no-index --numstat` 得 probe 相对 HEAD = **`+69 −2`**。
⇒ `69 + 104 = 173`、`2 + 1 = 3`，**可加性成立**。
🔑 而且这个 `+69 −2` 与上一轮记录的「工作树 `+69 −2` 未提交」**逐字一致** ⇒ 这既是分解的自检，也是「半净化窗口」那条既有结论的独立复核。

**文件画像**（字节级，均无 BOM）：

| 文件 | sha256[:16] | bytes | CRLF | bare LF |
|---|---|---|---|---|
| `backend/tests/conftest.py` | `f5344be868fcfe10` | 31,448 | **584** | **0** |
| `backend/tests/test_test_sandbox_is_declared.py`（新） | `1639207b509de6a3` | 9,541 | **200** | **0** |

⚠️ 新测试文件是 Write 工具的产物（**落盘为 LF**），已按 4.3 的配方做字节级 LF→CRLF 对齐，验收四条**同时**成立：CRLF `0 → 200`、bare LF `200 → 0`、字节增量 `200 == 200`、无 `\r\r\n`。**这是本会话第二次踩同一个坑**（第一次是第 4 项），说明「新建 `.py` 默认不是 CRLF」已应当作本仓的**前提**而不是意外。

⚠️ **为什么全量回归是必跑而非可选**：`conftest.py` 被**每一个**测试文件导入 ⇒ 本项的影响面**就是全集**，不存在"更小的影响面"可选。判据同样只取 junit 的 `failures`/`errors`；这次进程尾部也被 `[safe-delete]` 拦了（指纹：`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":9791,...,"targets":["...\\Temp\\pytest-of-Alin\\garbage-..."]}` —— 拦的是 pytest 自己的临时目录清理，**不是测试失败**），所以 `-q` 的末尾统计行看不到，判定完全来自 junit。

### 53.29.10 未决 / 明确不做

- ⏳ **(a) 保留最近 N / (c) 先导出再删除** 两条策略仍留操作员拍板；判据已经补齐，所以现在**具备**拍板条件了。
- ⏳ 历史沙箱目录与更早会话的 35 个散件：**只报不删**。
- ⏭️ **不提交。**

## 53.30 待办 ①／选项 A：`world_cup_api_football_validate` 那个 503 —— 一次 2026-07-07 的**一次性产物**，被 2026-09-24 的**可见化变更**提升成「进程一起来就报」（零代码改动）

### 53.30.1 结论先行

- 这个 503 **不是**进行中的故障，也**不是**假阳性：账本里那一行**确实**是 `failed`，`/api/health` 的判据**确实**该红。
- 它的成因是**两件互不相干的事撞在一起**：① 2026-07-07 一次**手动触发**的验证，拿到的是一次**干净的成功响应 + 空数组**（provider 答「0 场」）；② 2026-09-24 的 `6b820e7` 把一个**只读最新一行**的判据接进了 `/api/health`。
- ⇒ **07-07 那天没有任何东西被写成「失败」以外的样子；是 09-24 才有人开始读它。** 时间差 79 天，这解释了「为什么一个七月的读数会在十月把进程按在 503 上」。
- **本项零代码改动、零文档外改动**（本节本身是追加）。三个探针全部只读 / 沙箱内演绎，生产文件 **`files changed: 0`**。
- 🔴 **我一度以为的两件事，实测都错了**（53.30.6 / 53.30.8），两条都在下面显式记下：一条是「第三步有死锁」，一条是「22 处 `str(exc)` 是泄漏放大器」。**后者是我上一轮的结论，本节点名推翻它。**

### 53.30.2 那唯一一行，逐字段

账本 `backend/v2_loop.db`（只读，sha16 `35346d467fe24ce2`，17,772,544 B，**1759 行 / 19 个 job 名**）。该 job **全史只有 1 行**：

| 字段 | 值 |
|---|---|
| `id` | `a6acb79c-b3c8-413c-baf2-bd60b228609d` |
| `started_at` | `2026-07-07T17:46:46.955912+00:00` |
| `finished_at` | `2026-07-07T17:46:48.524224+00:00` |
| `duration_ms` | `1568` |
| `status` | `failed` |

`result_json` 的 8 个键（**注意 `step_count`**）：

```
provider            : 'api_football'
ok                  : False
error               : 'API-Football returned 0 fixtures for league=1 season=2026; check provider coverage/config before import.'
step_count          : 2
failed_step         : 'fixture_fetch'
fixture_count       : 0
covered             : None
missing_from_store  : None
```

🔑 **`step_count = 2` 是那一行在自证它停在哪**：验证器共三步，它只记了两步 ⇒ 第三步（`fact_coverage`）**从未执行**。这比读 `failed_step` 更硬 —— `failed_step` 是验证器**自己**填的字段，`step_count` 是 `len(result["steps"])`，两者独立。
🔑 另两个 `None`（`covered` / `missing_from_store`）是**同一件事的第三个证人**：那两键只在第三步之后才有值。

### 53.30.3 为什么这次**不是**异常：`except` 的格式串一次都没出现过

`validate_world_cup_api_football_pipeline()`（`services/world_cup_api_football_source.py:237-330`）有**两条**失败出口，形态不同：

- `:290-293` —— **响应成功但列表为空** ⇒ `result["error"] = fixture_error`，即上面那个**固定串** `"API-Football returned 0 fixtures for league=… season=…; check provider coverage/config before import."`
- `:294-303` —— **真抛异常** ⇒ `error = f"Fixture fetch failed: {type(exc).__name__}"`

账本里写的是**前者**；后者在那个 job 的整段历史里**一次都没出现**。⇒ 07-07 那天不是网络/钥匙/超时的异常路径，是**服务器回了 200 和一个空 `response` 数组**。
🔑 佐证：**连接步通过了**（`step_count=2` 意味着 `:246` 的 `if not conn["ok"]` 没触发）⇒ 那天 `WORLD_CUP_API_FOOTBALL_API_KEY` **存在且有效**。现在它 len=0（53.30.10）。

### 53.30.4 本地对照：本机有 104 场，而 provider 在**赛期内**答「0 场」

只读 `world_cup_predictions.db`（`.workbuddy/audit-2026-10-03/probe_health_503_local.py`）：

- `match_fixtures` 表 **104 行，全部 `finished`**；
- kickoff 区间 `2026-06-11 → 2026-07-19`，其中 **2026 年 7 月内有 26 场**；
- `sports_facts.json` 里 WC 的 `match_result` 事实：**0 条**。

⇒ 07-07 那天**正在世界杯赛程中间的休息日附近**，本机自己的赛程表说「这届有 104 场」。provider 答 0。**两个数据源在同一个问题上给出互斥答案**，而 `league=1 / season=2026` 这两个参数**恰好也是本仓自己配的**（53.30.10）—— 所以「provider 真的没有」与「这组 league/season 对 provider 没意义」两种解释**都还活着**，本项**没有**把它们分开（53.30.12）。

### 53.30.5 归因：`latest_run_per_job` 由 `6b820e7` 引入 ⇒ 503 是 09-24 才**可见化**的

```
$ git log --oneline -1 6b820e7
6b820e7 feat(security): stage 2A production hardening — ledger alarm gate, realtime auth, preflight (#105)
```

`/api/health` 的 `degraded` 判据（`main.py:529-538`）是 `bool(failed_runs) or (SCHEDULER_ENABLED and not scheduler.running and not …lock())`，而 `failed_runs` 的**数据源**是 `loop_status` → `latest_run_per_job()`。后者由 `6b820e7`（**2026-09-24**）引入。
⇒ 同一条 07-07 的行，在 **09-24 之前**对 `/api/health` **不可见**；从 09-24 起，它成为 `failed_runs` 的**单元素**。
🔑 **这条行本身没有变，变的是「有没有人读它」** —— 这正是 §4.1 那条「结论会静默过期」的**镜像形态**：这里是「缺陷会静默**诞生**」。

**它还成立吗？** 成立，而且是**静止**的：

- 账本最大 `started_at` = `2026-09-26T15:32:31.305310+00:00` ⇒ 生产库最后一次写入**早于**本次审计，07-07 之后该 job **再没被写过**。
- `app/` 里 `world_cup_api_football_validate` 只出现 3 类位置：**1 个生产者**（`events.py:798`，字面量）、**1 个读取门**（`events.py:766`）、**1 组只读消费者**（`world_cup_data_source_status_service.py:139/355`）。**调度器里没有它** ⇒ 「重跑一次」**不是自动的**，必须有人去打那个 POST 路由（07-07 就是这么来的）。

### 53.30.6 🔴 我一度以为的「死锁」被证伪

我原本的假设：第三步 `coverage_ok = len(missing) == 0 or len(stored_facts) > 0`（`:320`）若为假，`result["ok"]` 就永远不可能是 `True` ⇒ 「永远修不好」。**读代码即被推翻**：

```
:320    coverage_ok = len(missing) == 0 or len(stored_facts) > 0
:321    result["steps"].append({"name": "fact_coverage", "ok": coverage_ok, "detail": coverage})
:323    result["ok"] = True          # ← 无条件
```

`coverage_ok` **只被记进 `steps[]`，从不参与 `result["ok"]`**。⇒ **无死锁**。
🔴 但这句话反过来是一处**真缺陷**：`steps[]` 里那一步可以带着 `"ok": False`，而**同一个 `result` 的顶层 `ok` 是 `True`** ⇒ **同一份 payload 里两个 `ok` 互相矛盾**，而顶层那个才是路由判 `status` 的依据（`:807`）。这是「0/False 必须只说一件事」（§5）的同族问题，**登记备查、不当场改**（53.30.12）。

### 53.30.7 🔴 可见性：同一个 `error` 出现在**三个**面上，鉴权姿态**各不相同**

用副本端到端实证（`.workbuddy/audit-2026-10-03/probe_health_503_endtoend.py`：11 个存储路径全部重定向到沙箱副本 + 对生产目录做前后 sha 快照）。生产 **`files changed: 0`**。三个面的实测：

| # | 路由 | 鉴权 | 匿名/本部署能拿到 `error` 吗 |
|---|---|---|---|
| 1 | `GET /api/health` | **匿名 + 可选 key** | ❌ 匿名只给 `['duration_ms','finished_at','job_name','started_at','status']`（`_visible_run` 剥掉 `error`/`result`，`loop_status_service.py:127-140`） |
| 2 | `GET /quality-metrics/anomalies` | **0 个鉴权依赖**（`quality_metrics.py:283-284`） | ✅ **原样全文**，在 `scheduler_job_failed` 的 `detail.error` 里（`:318-330`） |
| 3 | `GET /sports/world-cup/data/sources/status` | `Depends(require_write_key)`（`events.py:535-537`） | ⛔ 本部署 **503**（`API_WRITE_KEY` 未配置 + `ALLOW_OPEN_WRITES=False` ⇒ `security.py:16-27`） |

实测 payload（面 2，本部署共 3 条异常）：

```
code='scheduler_not_running'         severity='high'
code='scheduler_job_failed'          severity='high'   <<< THE ONE
    detail = {"job_name": "world_cup_api_football_validate",
              "started_at": "2026-07-07T17:46:46.955912+00:00",
              "error": "API-Football returned 0 fixtures for league=1 season=2026; ..."}
code='llm_degraded_mode_events'      severity='low'
```

🔑 **本部署有一条「原因通道」是开着的，而它不是文档点名的那个。** 而且——`api_health` 的详情开关是 `include_run_details=is_write_key_valid(x_api_key)`（`main.py:527`）：本部署 `API_WRITE_KEY` len=0 ⇒ `is_write_key_valid` **恒 `False`**（`security.py:8-12`）⇒ **`/api/health` 在本部署下对任何人都不给 `error`**。所以面 2 是**唯一**可达的原因面。
⚠️ 但 `SCHEDULER_ENABLED=True` 且本部署 `scheduler.running` 的实测姿态决定 `scheduler_not_running` 那条在不在 —— 探针里 `loop_status(scheduler_running=False, …)` 是**显式传参**，不是读进程真值；**面 1 的 200/503 本身在任何调用者下都可见**，这条与传参无关。

**核对 RUNBOOK（`docs/ops/RUNBOOK.md`）483-487 行**，原文：

> The response names the failing component and nothing else: recorded errors carry the component name and the exception *type*, never `str(exc)`, because a sqlite3 or `OSError` message routinely contains the database path and an authenticated `/api/health` returns the `error` column verbatim.

- 前半句（`never str(exc)`）**成立** —— 但**不是因为调用点自律**，而是因为 `_finish_run` 的覆盖（53.30.8）。
- 后半句把暴露面写成「an **authenticated** `/api/health`」⇒ **它把闸门认成了鉴权。真实闸门在写侧。** 面 2 这条例外说明：**存在一个 0 鉴权路由会把同一列原样吐出来**。所以 483-487 行**不是错的，是不完整的**，而完整性在这里恰好是安全论证的一部分。**登记备查，不当场改**（RUNBOOK 不属本文件）。

### 53.30.8 🔴 纠正我自己：那 22 处 `error=str(exc)` **不是**放大器，是**被丢弃的死计算**

我上一轮的结论是「`app/` 里 21 处 `finish_run(..., error=str(exc))` 是这条矛盾的可能放大器」。**本节点名推翻它。** 全 `app/` 普查（AST，两种调用形态 `ast.Attribute` + `ast.Name`）：

- **38 处** `finish_run`/`_finish_run`/`_finish_*` 调用带 `error=` 关键字；分布：`core/scheduler.py` **30**、`api/routes/events.py` 2、`main.py` 1、5 个 `world_cup_*` service 各 1。
- 其中 `error=str(exc)` 共 **22 处**（**不是 21**——我上一轮的手抄清单漏了 `:827`），**22/22 都同时传了 `exc=exc`**，**没有一处** 是「传了 `str(exc)` 却不传 `exc`」。

为什么「同时传 `exc=`」就让它失效 —— `_finish_run`（`core/scheduler.py:158-196`）：

```
:178    safe_error = error
:179    exc_type = type(exc).__name__ if exc is not None else None
:180    if status == "failed" and exc_type is not None:
:181        safe_error = f"Scheduler job failed: {exc_type}"     # ← 覆盖掉 str(exc)
:185    loop_run_store.finish_run(run_id, status, result=result, error=safe_error)
```

⇒ 那 22 个 `str(exc)` 被**急切求值后整段丢弃**。它们**不泄漏**，只是**白算**。RUNBOOK「never `str(exc)`」之所以成立，靠的就是 `:180-181` 这三行覆盖。
🔑 三条旁证：① **`exc_type=` 这个关键字全仓 0 使用**（`_finish_run` 的形参叫 `exc`，判据由 `type(exc)` 现场派生）；② 另外 4 处传变量的也安全 —— `scheduler.py:185` 传 `safe_error`（已净化）、`scheduler.py:439` 传本地拼的 `f"SQLite integrity failed for: …"`、`events.py:803` 传 `f"API-Football pipeline validation failed: {type(exc).__name__}"`（只类型名）、`main.py:115` 的 `error` 形参的**全部调用点**都传 `None` / `_safe_error(...)`（= `f"{what}: {type(exc).__name__}"`，`main.py:76`）；③ 这个覆盖**已被 6 个测试文件钉住**（`test_scheduler.py` 6 处、`test_scheduler_ledger_alarm_gate.py` 8 处、`test_optimization_cleanup_job.py:241`、`test_drift_alert_job.py:165`、`test_scheduler_live_update_ledger.py:202`、`test_sentry_integration.py:187/223`，断言形如 `== "Scheduler job failed: RuntimeError"`）。
🔴 **真正该记的风险不是「现在漏」，而是「这条不变量只由 `_finish_run` 一个函数兜着」**：任何新增一处 `_finish_run(run_id, "failed", error=str(exc))` **忘了带 `exc=`**，就会**立刻**把原始文本写进账本，而**目前没有任何静态守卫会红**（现有 6 个测试钉的是**具体 job 的行为**，不是**调用点的不变量**）。本仓已有成熟的 AST 守卫先例（`test_runtime_stores.py` / `test_elo_provenance.py` / `test_event_store_source_names.py`）⇒ 这条**具备做成守卫的条件**，但属代码改动，**登记、不当场做**（53.30.12）。

⚠️ **方法论旁注（一次差点发生的空扫描）**：本次普查第一版用 `b.decode("utf-8")`，**2 个文件因 BOM 被 `ast.parse` 拒绝**（`services/llm_gateway_service.py`、`services/world_cup_tournament_state_service.py`）⇒ 它们**静默地从人口里消失**。改用 `utf-8-sig` 后 0 跳过，读数不变（38 与 22 在两种口径下相同）。**「跳过」与「拒绝」必须分开报**，否则「某文件不在普查结果里」会被读成「它没问题」。

### 53.30.9 这条 `failed` 行有**第二个后果**：它同时**关掉**导入路径

`POST /sports/world-cup/data/bundle/api-football/import`（`events.py:750-762`）第一句就是 `_require_successful_api_football_validation_before_import()`（`:765-782`）：

- `last_run(...)` 非 dict ⇒ **409** `"Run API-Football pipeline validation before import."`
- `run.get("status") != "success" or result.get("ok") is not True` ⇒ **409** `f"Latest API-Football pipeline validation failed{suffix}"`

⇒ 「health 503」只是**症状**；**API-Football 这个 provider 的导入通道整体被这一行关着**。同一行、两个面、两套状态码（503/409），**由两个不同的读法产生**（`latest_run_per_job` vs `last_run`）—— 这一点在 §5 的口径下值得单列：它**不是**「同一个 0 有两种解释」，而是「同一个 `failed` 有两种**后果**」。

### 53.30.10 为什么不做 / 做不到

- 🔴 **不建议「手动把那行改成 success 或删掉」**：那是在**改生产证据库**，而这个库正是所有健康判据的唯一真相源（`main.py:86-95` 的 docstring 逐字说了这件事）。**清它的唯一正当方式是让该 job 再成功跑一次。**
- ⛔ **但本部署做不到**：那个生产者路由（`events.py:793-796`）带 `Depends(require_write_key)`，本部署 `API_WRITE_KEY` len=0 + `ALLOW_OPEN_WRITES=False` ⇒ **503**（`security.py:16-27`）。⇒ **「天然解药」正好被写面关闭挡在门外**，而**这不是**本次调查引入的、是既有姿态。
- **本部署配置姿态实测**：`API_WRITE_KEY` len 0 / `ALLOW_OPEN_WRITES=False` / `SCHEDULER_ENABLED=True` / `SCHEDULER_LOCK_ENABLED=True` / `WORLD_CUP_API_FOOTBALL_API_KEY` **len 0** / `BASE_URL=https://v3.football.api-sports.io` / `LEAGUE_ID=1` / `SEASON=2026` / `settings` 上**不存在** `AUTOTRADE_ENABLED` / `SENTRY_DSN` len 0（⇒ `_finish_run:201-212` 的 Sentry 转发在本部署是 no-op）。
- ⏳ **「provider 配置缺失」是更上游、独立的一件「未声明缺失」**：`LEAGUE_ID=1` / `SEASON=2026` 是**本仓自己填的**，而 provider 在赛期内答「0 场」。这是不是「对 provider 而言这组参数无意义」，本项**没有**判定。**登记，不代决。**

### 53.30.11 交叉核对

- **`start_run` 调用点 = 31**（AST，`app/` 全域，含 BOM 文件），文件分布 `scheduler.py` **24** / 6 个 service 各 1 / `main.py` 1 / `events.py` 1 —— 与 §53.28 记的「31 个写入点」**逐字一致 ⇒ 独立吻合**。（本节点的复查同时**澄清**了那个口径：31 是 **`start_run`** 口径，不是「start+finish」；`finish*` 全量是 **72** 处，其中 38 处带 `error=`。）
- 上面三个探针（`probe_health_503.py` / `probe_health_503_local.py` / `probe_health_503_endtoend.py`）全部复跑通过；生产 `v2_loop.db` sha16 全程 `35346d467fe24ce2`。

### 53.30.12 未决 / 明确不做

- ⏳ **从未用现有 key 试过该路由** —— 07-07 那次是唯一一次真实调用，key 现已移除 ⇒ **无法区分**「provider 真的说 0」与「这组 league/season 对 provider 没意义」。**不代决。**
- 📝 **登记（三处，均不当场改）**：① `RUNBOOK.md` 483-487 行把暴露面写成「authenticated `/api/health`」，**真实闸门在写侧**，且存在 0 鉴权路由（`/quality-metrics/anomalies`）会原样输出该列；② `_finish_run` 的覆盖是**唯一**防线，**22 处 `str(exc)` 靠「都带了 `exc=`」才安全**，建议做成 AST 守卫；③ `validate_…pipeline` 的 `steps[].ok` 与顶层 `ok` **可以互相矛盾**（`:320-323`）。
- ⏭️ **本项零代码改动。不提交。**

## 53.31 选项 A 的收尾：把「`error` 列的唯一防线」钉成**调用点不变量**（新增 1 个守卫文件 / 10 条用例 / 3 次四相变异）

### 53.31.1 结论先行

- §53.30.8 记的那条风险——**「这条不变量只由 `_finish_run` 一个函数兜着，且没有任何静态守卫」**——本项把它补上了：新增 `backend/tests/test_ledger_error_sanitization_guard.py`（**10 条用例**，AST 遍历 `app/` 全域）。
- 它钉住**两条**不变量：① 凡 `finish_run`/`_finish_run` 的 `error=` 实参能读到异常**消息**的，必须**转发 `exc=`**；② 转发 `exc=` 的（= 靠覆盖洗白的）调用点，`status` 必须是**字面量 `"failed"`**。
- **扫描器本身就是被测对象**：`analyse_module(source, filename)` 是**源码文本的纯函数**，所以「它到底会不会红」可以用**合成片段**正面证明，而不是只验当前仓库恰好全绿（§6a-3 的「空头守卫」在**单一实例**场景下的唯一解）。
- 🔴 顺带证到一条**方法论事实**：这条不变量的人口，**AST 数与文本数不相等**（**22 vs 21**）—— 第 22 处多了个 `result=` 实参。**这就是「用 grep 数调用点」会算错的一手证据**，也解释了为什么 §53.30.8 里我手抄的「21」既漏了 `:827`、又对不上这个 22。
- ⏭️ 本项**不改任何生产代码**；登记 ① 落成动作（RUNBOOK 追加引块，不改写原句）；登记 ③ **不落动作**（理由是取值决定，见 53.31.10）。

### 53.31.2 为什么这条不变量此前**钉不住**

行为测试**能**钉住「覆盖存在」——`_finish_run` 删掉覆盖后，**6 个行为测试文件会红**（本项用 M3 实测，见 53.31.7）。但它们钉住的是**那 6 个 job 的行为**：

```
test_scheduler.py（6 处断言）· test_scheduler_ledger_alarm_gate.py（8 处）
test_optimization_cleanup_job.py:241 · test_drift_alert_job.py:165
test_scheduler_live_update_ledger.py:202 · test_sentry_integration.py:187/223
```

断言形如 `row["error"] == "Scheduler job failed: RuntimeError"` ⇒ 换句话说是**按 job 抽查**。而泄漏面的定义是「**任意一个**调用点忘了转发 `exc=`」⇒ **属性在调用点上，就不可能在恰好被抽查到的那几个上被钉住**。**第 23 个调用点会直接上线，没有一个测试会点它的名。**

### 53.31.3 人口口径：61 / 38 / 22，以及三个**必须排除**的假阳性

口径：`app/**/*.py`（`utf-8-sig`，见 53.31.11），`ast.parse`，收 `finish_run` 与 `_finish_run` **两个名字**（属性形态 `loop_run_store.finish_run(...)` 与裸名形态 `_finish_run(...)` 都覆盖）。

| 读数 | 值 |
|---|---|
| 解析的模块 | **331** |
| `finish_run`/`_finish_run` 调用点 | **61** |
| 其中带 `error=` 的 | **38** |
| `error=` 能读到异常**消息**的（= 人口） | **22**（全部在 `core/scheduler.py`） |
| 其中**没**转发 `exc=` 的 | **0** |
| 转发 `exc=` 但 `status` 非字面量 `"failed"` 的 | **0** |

三个**差点被算进来**的假阳性（都在筛人口时排除了，理由各自不同）：

1. **`_finished_unscored_matches`** —— 我用 `startswith("_finish")` 的宽松口径扫过它一次，它是个**查询 helper**（`world_cup_post_match_backfill_service.py:106`），**不是账本写入者**。⇒ 人口必须按**名字全等**取，不能按前缀。
2. **5 处裸名 `finish(...)`**（3 个 `world_cup_*` service）—— 它们是**内层闭包** `def finish(result)`，写的是 **`result_json`**，不是 `error` 列；且全部**无关键字实参**。⇒ 不计入 `error` 列人口。
3. **3 个 service 级 `_finish_*` 助手**（`_finish_audit_run` ×2、`_finish_consistency_repair_audit_run`、`_finish`）—— 调用点签名是 `(run_id, result)`，**根本没有 `error=` 关键字**。它们**内部**那句 `loop_run_store.finish_run(..., error=result.get("error") ...)` 才是写入点，**已经在 22/38 之外、但在 38 之内**（属 `error=` 的「无异常在作用域内」那一类）。

🔴 **口径边界（写下来，防止后来者以为它覆盖了全部）**：本守卫**只看 `error=` 实参里引用了被捕获异常的那些**。那 4 处 `error=result.get("error") if status == "failed" else None`（`post_match:65` / `quality:628` / `result_fact:221` / `verified_correction:312`）把**任意上游服务**产出的字符串写进同一列，**本守卫不覆盖** —— 那是**另一条**独立路径，也是下一个自然目标（53.31.12）。

### 53.31.4 扫描器理解哪两种形态，以及哪两种**不算**泄漏

**算泄漏（两种形态都抓）**：

- **直接**：`error=str(exc)`。
- **间接**：`error = str(exc)` 之后再 `error=error` —— 这一种**纯文本搜 `str(exc)` 会漏**，靠 handler 内的**赋值定点**（`_names_carrying_the_message`，含链式赋值的 fixpoint）抓到。

**不算泄漏（用 `_message_reads` 显式排除）**：

- `f"...{type(exc).__name__}"` —— 只印**类名**。**这一条是必须的**：真实代码里 `events.py:803` 就是 `f"API-Football pipeline validation failed: {type(exc).__name__}"`，而它是**安全的**。一个「引用了 `exc` 就必须转发」的朴素规则会**在这里给出假阳性**。
- `exc.__class__.__name__` —— 同族。

### 53.31.5 第二条不变量：靠覆盖洗白的调用点，`status` 必须是**字面量** `"failed"`

`_finish_run` 的覆盖有**合取前提**：`status == "failed" and exc_type is not None`。⇒ 一个**转发了 `exc=`** 却把 `status` **算出来**的调用点，会在**成功路径上**把原始文本原样写进列里 —— 也就是说，转发 `exc=` 只是「看起来安全」。
实测：22 处**全部**是 `_finish_run(run_id, "failed", ...)`（status 是位置实参、字面量）。所以这条不变量**今天成立**，且**可被守卫**。

### 53.31.6 四相变异（三条，症状**互不相同**）

配方沿用 §4.3：备份 → 变异 → 跑 → 还原 → sha 逐字节复核；判据只取 junit 的 `failures`/`errors`。`core/scheduler.py` sha16 全程 **`bcb50541065e5268`**。

| # | 变异 | 针命中 | Δ字节 | 结果 | 红的**是哪一条** |
|---|---|---|---|---|---|
| 0 | 无（基线） | — | — | `tests=10 failures=0 errors=0` GREEN | — |
| **M1** | 抽掉一处 `, exc=exc` | 21 | **−9** | RED `failures=1` | `test_no_call_reads_an_exception_message_without_forwarding_exc` |
| **M2** | 把一处 `"failed"` 换成变量 `job_status` | 21 | **+2** | RED `failures=1` | `test_a_call_relying_on_the_override_passes_a_literal_failed_status` |
| **M3** | 删掉 `_finish_run` 的覆盖两行 | 1 | **−111** | RED `failures=15`（11 个文件） | **守卫文件 0 条红**（见 53.31.7） |

三条红**症状各不相同**（不同用例名、不同断言文本），且每条**还原后都回到 GREEN**、sha 逐个对上。M1 的错误串实际读数：

```
scheduler.py:315: error= reads ['exc'] without forwarding exc=, so _finish_run's override never fires
```

⚠️ **M1/M2 的针命中是 21 不是 22** —— 见 53.31.8，这**不是** bug，是「文本人口 ≠ AST 人口」。

### 53.31.7 🔴 M3 是一次**证伪实验**：docstring 里那条「耦合声明」**没有被证伪**

守卫文件的 docstring 里我写下了一句关于**界限**的话（§4.3 要求「某条变异故意不存在」必须写明）：

> Documented coupling: if `_finish_run`'s override is deleted, the six behaviour tests above go red — **not this file**.

这句话是**可证伪的预测**，所以 M3 就把它当实验做：删覆盖 → 同时跑守卫文件 + 那 6 个行为文件（共 **104** 条）。实测：

```
m3: tests=104 failures=15 errors=0 -> RED
    test_job_failure_is_isolated: AssertionError: 'boom' != 'Scheduler job failed: RuntimeError'
    test_a_failing_event_read_still_fails_the_row: AssertionError: 'event store unreadable' != 'Scheduler job failed: OSError'
失败分布：test_scheduler（6 个类各 1）· test_scheduler_ledger_alarm_gate（4）
          test_sentry_integration（2）· drift_alert / optimization_cleanup / live_update_ledger（各 1）
guard file failures: []          ← 0 条
```

⇒ **预测成立**：删掉安全网由**行为测试**抓（11 个文件、15 条红），**守卫文件保持全绿**。
🔑 这条实验的价值在于：它证明了本守卫**不是**把安全网本身又钉了一遍，而是**恰好补上了另一半**——两份测试**职责不重叠**。若 M3 下守卫也红了，就说明我在文件里重复了别人已有的断言（§4.3 的「空头」在另一个方向上的形态）。

### 53.31.8 🔴 顺带证到：「AST 人口」≠「文本人口」（22 vs 21）

M1/M2 的针写的是最典型的那个形态：

```
_finish_run(run_id, "failed", error=str(exc), exc=exc)
```

它只命中 **21** 次，而 AST 数出 **22** 处。差的那一处是 `core/scheduler.py:764`：

```
_finish_run(run_id, "failed", result={"mode": mode}, error=str(exc), exc=exc)
```

—— 多了一个 `result=` 实参。
🔑 这条小事实是**整节最可复用的一条**：**调用点的「形态」不只一种**，所以「按字面量 grep 数调用点」**系统性少算**。同一批取证里我已经因为它错过一次（§53.30.8 手抄清单漏了 `:827`）。**结论**：数调用点必须**按 AST 的调用节点**数，**字面量只能用来定位、不能用来计数**。

### 53.31.9 登记 ① 落成动作：RUNBOOK 追加引块（**不改写原句**）

`docs/ops/RUNBOOK.md` 483-487 行原句表达的是「为什么这列只写类型名」——**那个结论是对的**，它错误的是**归因**（把闸门认成鉴权）。按该文件**自己的先例**（489-497 行那条 `> **Before 2026-09-05 this section was false as written.**`），处理方式是**保留原句 + 追加引块**，而不是重写历史。

插入位置：原句之后、既有 `>` 引块之前。字节级取证（`.workbuddy/audit-2026-10-03/patch_runbook.py`，写入前**先全校验**）：

```
BEFORE bytes=87015 crlf=1635 bare_lf=0 sha16=f744fc69c4136944
needle occurrences: 1
AFTER  bytes=87752 crlf=1645 bare_lf=0 sha16=e8b8e70563a0af5d
prefix unchanged      : True
suffix unchanged      : True
byte delta == want    : True (737 == 737)
crlf delta == want    : True (10 == 10)
bare LF stays 0       : True
no CRCRLF             : True
```

新增的 9 行引块说三件事：① 洗白发生在**写侧**（`_finish_run`），不在调用点；② **auth 不是**这个列的读取闸门（`/quality-metrics/anomalies` 0 鉴权原样返回）；③ 指到新守卫文件。备份在 `.workbuddy/audit-2026-10-03/RUNBOOK.md.bak`。

### 53.31.10 登记 ③ 为什么**不落动作**：它是**取值决定**，不是判据缺失

§53.30.6 记的那处矛盾——`validate_world_cup_api_football_pipeline()` 的 `steps[]` 里可以有 `"ok": False`（`fact_coverage` 步），而**同一个 `result` 顶层 `ok` 无条件是 `True`**（`:320-323`）。

- 修它必须先回答**一个业务问题**：「算出来的 `coverage_ok` 为假时，这条 pipeline 到底算不算失败、要不要连坐挡住 import 路由（409）？」——**这是契约取值**，按 §0 **不代决**。
- 而且**今天就改它会改变行为**：`result["ok"]` 是 `events.py:807` 判 `status` 的唯一依据，也是 `_require_successful_api_football_validation_before_import()` 放行 import 的依据 ⇒ **任何改动都会同时改 503/409 两个面的语义**。
- ⇒ 本项**只登记**：建议的下一步是**先把它变成可观测**（例如把 `coverage_ok` 也提到 `result` 顶层并起一个**不与 `ok` 同名**的键），让操作员看得见矛盾，**再由人定取值**。

### 53.31.11 门与字节证据

| 项 | 读数 |
|---|---|
| `ruff check app/ tests/test_ledger_error_sanitization_guard.py` | `All checks passed!` |
| `mypy app/` | `Success: no issues found in 331 source files` |
| `python -m scripts.generate_types --check` | `generated-types.ts is up to date.` |
| 新守卫文件 junit | `tests=10 failures=0 errors=0 skipped=0` |

**新文件画像**（`backend/tests/test_ledger_error_sanitization_guard.py`，Write 工具产物 = **LF**，已做字节级对齐）：

| 时点 | sha256[:16] | bytes | CRLF | bare LF |
|---|---|---|---|---|
| 落盘（LF） | `902fa8272c9b59d8` | 14,043 | 0 | 353 |
| 对齐后（CRLF） | `ca66caf917c6cac7` | 14,396 | **353** | **0** |

验收四条**同时**成立：CRLF `0 → 353`、bare LF `353 → 0`、字节增量 `353 == 353`、无 `\r\r\n`；无 BOM。**这是本会话第三次踩「Write 工具落盘为 LF」这个坑**（第 4 项、第 7 项之后）。
⚠️ 顺带核过：`.workbuddy/audit-2026-10-03/*.py`（含本项新增的 `mutate_itemA.py`、`patch_runbook.py`）**本来就地就是纯 LF** ⇒ **不需要**动，**改了反而造出全文件 diff**。

### 53.31.12 未决 / 明确不做

- ⏳ **本守卫只覆盖「`error=` 引用了被捕获异常」这一条路径。** 另一条路径（`error=result.get("error")`，4 处）把**上游服务产出的字符串**写进同一列 → **未覆盖**，是下一个自然目标。判据已经现成（同样的 AST 手法），但**要先确定「什么算上游产出的原始异常文本」**，否则会做成一张**豁免表**而不是一条不变量。
- ⏳ **登记 ③**（`steps[].ok` vs 顶层 `ok`）**只登记不落动作**，理由与建议见 53.31.10。
- 📌 **`main.py:76` 的 `_safe_error`** 与 **`events.py:803`** 是**两条独立的「只写类型名」约定**（同形不同源）⇒ 将来若要做 §8d 的「重复与漂移」检查，这两处是候选；本项**不合并**（同形≠同义，§4.1）。
- ⏭️ **本项不改任何生产代码，不提交。**

## 53.32 攻边界：先定义「上游产出的原始异常文本」，再把「继承」钉成可执行不变量 —— 以及**上一轮那处修复被本轮实测证伪并回退**

### 53.32.1 结论先行（含一条自我更正）

- **定义**（本项的核心交付，§53.31.12 说「不先定义就会做成豁免表」的那个定义）：

  > 一个值算**原始异常文本**，当且仅当它**取自一个被捕获异常、且取法携带该异常的消息**：`str(exc)` / `repr(exc)` / `f"{exc}"` / `exc.args`。
  > **不算**：只取类名（`f"{type(exc).__name__}"`、`exc.__class__.__name__`）、只取**数字**字段（`exc.code` / `exc.status_code` / `exc.errno`）、以及**声明字面量**。
  >
  > 而**不变量**不是「值是不是原始文本」，是**「这个值是从哪来的」**：写进账本 `error` 列的字符串，必须在**写下它的那个作用域里可归因**。读一个**本作用域没接收到的** mapping 的键 = **洗白**（laundering）—— 那个异常在别的模块的 `except` 里，本处连 `exc=` 都无处转发。**判据是归因，不是内容**；内容判据写不出来，因为本仓 46 处整体字符串化里 44 处捕的是库/OS 异常，`str(exc)` **不是**本仓的违规。

- **普查三读数**：键读到**异常消息**的形状 **30 处**（22 处 `error=` kwarg + 8 处 `dict['error']`），其中 **3 处是假阳性**（`f'HTTP {exc.code}'`，数字状态码）⇒ **真 5 处**；**读上游 mapping 的 `"error"` 键**的形状 **6 处**，**全在出口方向**（把账本行读出来渲染成 API 响应），**入口方向只有 1 处**（`post_match:212`）。
- 🔴 **本项最重要的一条是自我更正**：§53.31 之后我落的**那处 `post_match:212` 修复是错的，本轮实测证伪并已回退。** 它把 `"error": sync_result.get("error")` 换成 `"error": "fixture_sync_failed"` —— 而**账本承载产出方的失败原因**是本仓**有测试钉住的契约**，一改就有 **2 条既有测试变红**（`test_sync_error_short_circuits_without_scoring`、`test_post_match_backfill_does_not_expose_or_persist_provider_error_body`）。回退留痕：`git diff --numstat` = **`7 0`**（只留 7 行注释，**删除数 0**）。
  **教训不是「测得不够」，是「我先改了产物的上游消费者，而没先读上游」**——真正的裸文本产生点在**产出方**（`world_cup_match_service.py:322` 的 `str(e)`），不在这条 relay。
- **本轮实际交付**：① 上述定义与普查；② 守卫文件的**第二套不变量**（同样的 AST 纯函数手法，跨模块 taint fixpoint），把「继承逃逸」钉成**棘轮**（§53.32.8）；③ 一条**契约冲突的完整取证**（§53.32.10，政策族 6 条测试要求声明 token，而 1 条断言要求裸消息）。
- ⏭️ **那处契约冲突只差 1 行生产代码**，但它是**取值决定**（§0）⇒ **只登记、不代决、不落动作**。本轮**不新增任何功能性的生产代码改动**；唯一的生产文件改动是 `post_match:212` 的 **7 行注释**（回退后原位留痕）。

### 53.32.2 透传链：三跳，每一跳都有测试看着

链路（三跳，端点各被测试钉住）：

| # | 位置 | 形态 | 谁钉住它 |
|---|---|---|---|
| ① | `world_cup_match_service.py:315-320` | `except FootballDataAPIError:` → **字面量** `"Football-Data.org API request failed"` | `test_world_cup_match_service.py:544`（`assertEqual`）；`test_world_cup_analytics_routes.py:1218/:1224`（`assertEqual`） |
| ② | `world_cup_match_service.py:321-326` | `except Exception as e:` → **`str(e)`** ← **裸文本的真正入口** | `test_world_cup_match_service.py:510-514`（`assertIn("network down", result["error"])`） |
| ③ | `world_cup_post_match_backfill_service.py:212`（原） | `"error": sync_result.get("error")` ← relay | `test_world_cup_post_match_backfill_service.py:216/:224`（`assertEqual("source unavailable")`） |
| ④ | `world_cup_post_match_backfill_service.py:65` | `error=result.get("error") if status=="failed" else None` ← **账本 sink**，**不转发 `exc=`** | 无（结构性） |

关键结构性事实：**sink 转发不了 `exc=`**，因为它手上没有异常 —— 异常在别的模块被捕获（①②）。所以 `_finish_run` 的净化器在这条链上**结构性地不可达**，而不是「忘了调」。

- `world_cup_match_service.py:315`（`FootballDataAPIError`）走的**就是**声明 token 分支 ✅ —— 这也解释了为什么 §53.32.6 的 provider-body 测试是**绿的**（敏感响应体在 `:86` 被塞进 `FootballDataAPIError`，被 `:315` 的 token 丢弃）。
- 但**同一个函数的 `:322` 把无类型异常的 `str(e)` 原样返回** —— 这就是 §53.32.7 要处理的那一跳。

### 53.32.3 定义的三条正面判据与三条反面判据（可被守卫执行）

| 判据 | 归属 | 守卫里的实现 |
|---|---|---|
| `str(exc)` / `repr(exc)` / `f"{exc}"` / `exc.args` | **是** | `_message_reads()` 命中 |
| `f"{type(exc).__name__}"` / `exc.__class__.__name__` | **不是** | `_SAFE_EXCEPTION_WRAPPERS = {"type"}` 排除 `type(...)`；`parent.attr == "__class__"` 排除 |
| `f"HTTP {exc.code}"` | **不是**（数字） | `_NUMERIC_EXCEPTION_ATTRS = {"code","status_code","errno"}` |
| 声明字面量 | **不是** | 常量节点 |
| **`m.get("error")` / `m["error"]`，`m` 不是本函数的参数** | **违规**（洗白） | `_mapping_reads()` + `_local_producer()` + taint fixpoint |
| `m.get("error")`，`m` **是本函数的参数** | **允许**（契约边界） | `_params(func)` 白名单 —— **这是一条原则，不是名录** |

🔴 **`_NUMERIC_EXCEPTION_ATTRS` 是定义的一部分，不是豁免**。本节第一次实现时漏了它，代价是：3 处 `f'HTTP {exc.code}'` 污染了 `test_world_cup_api_football_connection` / `test_world_cup_sportmonks_connection`，再经 `conn.get("error")` **传导**到 `validate_*_pipeline`，于是 `events.py:812` 变成**假阳性** —— **一处定义缺口，三个误报**。把「数字字段不算消息」写进共享定义后，产出方集合从 13 收敛到 **9**（见 §53.32.8），三个误报同时消失。

🔴 **`"status"` 不是 sink 键**。sink 的写法是 `error=result.get("reason") if result.get("status") == "error" else None` —— 若按整表达式取键，`"status"`（**条件**里的读）会被当成「sink 消费的键」。判据要用**能供值的位置**（`_value_positions`：`IfExp` 只取 body/orelse，`BoolOp` 取各操作数），不取值守卫条件。这条由 `test_the_sink_keys_are_derived_and_not_widened_by_a_guard_condition` 精确钉住（断言 `("error","reason")`，不是子集）。

### 53.32.4 产出方人口：键读到异常消息的形状（30 处 / 5 真 / 3 假）

普查件 `.workbuddy/audit-2026-10-03/census-errorkeys.txt`，handler 绑定名直方图 `{None:492, 'exc':268, 'e':32, …}`：

| 类 | 处数 | 位置 |
|---|---|---|
| `error=` kwarg，`str(exc)` | **22** | 全在 `core/scheduler.py`，status 全是字面量 `"failed"`，22/22 传 `exc=`（§53.31 已覆盖） |
| `dict['error']`，**真消息读** | **5** | `quality_metrics.py:686`、`quality_diff_service.py:200`、`sentiment_aggregator.py:260`、`sports_fact_service.py:102`、`world_cup_match_service.py:322` |
| `dict['error']`，**假阳性** | **3** | `world_cup_api_football_source.py:212`、`world_cup_sportmonks_source.py:117`、`:190` —— 全是 `f'HTTP {exc.code}'` |
| 合计 | **30** | |

**独立交叉校验**：第二套不变量算出的「能在 `"error"` 键下吐出原始文本的函数」共 **6** 个（合成 fixpoint 后）：`build_diff` / `fetch_reddit_sentiment` / `import_sports_facts` / `quality_metrics_alerts` / `sync_world_cup_fixtures` / `run_post_match_backfill`（末一个是**继承**来的）。**前 5 个与上表「真消息读」5 处逐一对上** —— 两条互不依赖的手法（文本普查 vs AST taint）给出同一集合。

### 53.32.5 键形状普查：78 个 `"error"` 键里，只有 5 个落在「会写账本的函数」内部

普查件 `.workbuddy/audit-2026-10-03/census_error_key_shapes.py` / `census_writer_scoped_sites.py`：

| 读数 | 值 |
|---|---|
| `app/` 里带 `"error"` 键的 dict 字面量 | **78** |
| 其中「值 = 读另一个 mapping 的 `"error"` 键」 | **6** |
| 其中「值 = 纯字符串字面量」 | **36** |
| 其中「**同时**带 `"status"` 与 `"error"`」（信封形状） | **26** |
| `error=` kwarg 里读另一个 mapping 的 `"error"` 键 | **0**（我第一版提取器漏了 `IfExp`，把它当成 0 —— 已修正） |

**那 6 处「读上游 `"error"` 键」的全是出口方向**（把账本行/服务结果读出来渲染成 API 响应，不是写进去）：

| 位置 | 函数 | 方向 |
|---|---|---|
| `api/routes/quality_metrics.py:328` | `/quality-metrics/anomalies` 的 `latest_run_per_job()` 循环 | 出口 |
| `api/routes/world_cup_analytics.py:520` | `recent_runs()` 渲染 | 出口 |
| `core/scheduler.py:1551` | `_summarize_prediction_update()` | 出口（写进 `result`，不进 `error` 列） |
| `memory/optimization_task_store.py:90` | `_row_to_task()` | 出口 |
| `services/world_cup_post_match_backfill_service.py:73` | `_audit_run_payload()` | 出口 |
| `services/world_cup_result_fact_backfill_service.py:243` | `_audit_run_payload()` | 出口 |

⇒ 「出口读上游的 `"error"` 键」是**正当的**（渲染），**入口**才是问题 —— 而入口**只有 1 处**。这条正交性由 `test_it_does_not_flag_a_read_outside_a_ledger_writer` 负向控制钉住。

### 53.32.6 契约侧证据：政策族 6 条测试要的是**声明 token**，不是裸消息

`api/routes/world_cup_analytics.py` 一族测试（`test_world_cup_analytics_routes.py`）把这件事**写成了政策**：

| 行 | 测试 | 它要求的账本 `error` |
|---|---|---|
| `:141` | `test_consistency_repair_failure_is_safe_in_ledger_and_public_anomalies` | `"Consistency repair failed: RuntimeError"` |
| `:187` | `test_verified_result_correction_failure_hides_and_does_not_persist_sql` | `"Verified result correction failed"` |
| `:912` | `test_result_fact_backfill_failure_hides_and_does_not_persist_path` | `"Result fact backfill failed"` |
| `:1013` | `test_scoring_reconcile_failure_hides_and_does_not_persist_sql` | （同类） |
| `:1070` | `test_post_match_backfill_exception_is_safe_in_public_history_and_anomalies` | `"Post-match backfill failed: RuntimeError"` |
| `:1173` | `test_post_match_backfill_does_not_expose_or_persist_provider_error_body` | `"Football-Data.org API request failed"`（**产出方的字面量**） |

三条可比对的共同措辞：

1. **账本、响应、`/quality-metrics/anomalies`、CLI 日志四处都不许出现**同一组片段：
   `"SELECT secret"`、`"C:/private"`、`"fake-api-key"`、`"fake-ticket"`、`"fake-subprotocol"`、**`"upstream.example"`**、`"Traceback"`。
   🔴 **`"upstream.example"`（一个 URL host）在禁止清单里** —— 这与本项探针复现出的泄漏形状（urllib 消息里的 host + query string）**是同一件事**。
2. **类名走 `f"…: {type(exc).__name__}"` 惯用语，且这一点也被断言**：`:141` / `:187` / `:1070` 全部 `assertIn("RuntimeError", output)`（日志）—— 即「**日志记类名、禁记消息**」。
3. `:1173` 断言的 `"Football-Data.org API request failed"` **本身就是产出方的字面量**（§53.32.2 ①）⇒ 政策与「账本承载产出方 token」**并不冲突**。

⇒ **政策的意思是：账本 `error` 列放声明 token；需要区分类时用 `f"…: {type(exc).__name__}"`。** 而 §53.32.2 ② 的 `str(e)` 与 `test_world_cup_match_service.py:514` 的 `assertIn("network down", …)` 是**唯一**与政策相抵的一对。

### 53.32.7 我的修复为什么错：实测，不是论证

上一轮（§53.31 之后）我改的是 `post_match:212`：

```
- "error": sync_result.get("error"),
+ "error": "fixture_sync_failed",
```

**它破了 2 条既有测试**（`junit-itemB-restored.xml`：`tests=87 failures=2 errors=0`）：

| 失败的测试 | 断言 | 为什么破 |
|---|---|---|
| `test_world_cup_post_match_backfill_service.py:216/:224` | `assertEqual(run["error"], "source unavailable")` | 账本被要求承载**产出方给的原因**（patch 出来的值），换成 token 就不等于它了 |
| `test_world_cup_analytics_routes.py:1224` | `assertEqual(history…["error"], "Football-Data.org API request failed")` | 同上：`test_post_match_backfill_does_not_expose_or_persist_provider_error_body` 断言的正是产出方那个字面量 |

🔴 **反证方向比「改测试」更值得记一笔**：这 2 条测试与 §53.32.6 的 6 条政策测试**同处一个文件家族**，而且 `:1224` 那条**同时**断言「token 进账本」与「敏感响应体不进任何地方」。所以「把 relay 换成 token」**不是**在贯彻政策，而是**在破坏政策已经划好的那条线**（产物的**内容**由产出方决定，relay 忠实传递）——**该动的是产出方**。

⇒ **已回退**（`mutate_itemB.py revert`，`byte-identical to the backup: True`）。回退后该文件对 HEAD 的差异是 **`7 0`**（**纯注释插入**），sha16 `5ef80a0ac8418c83`，12,505 B / 311 CRLF / 0 bare LF。留下的注释写明：**这一行的安全性就是产出方 `sync_world_cup_fixtures` 的安全性**，并由守卫追踪这层耦合。

同一轮里还有一次**我自己的测量更正**：为做「新增方向」变异，我先在 `world_cup_result_fact_backfill_service.py` 的 **成功信封**里插了 `"error": import_result.get("error"),`，并**声称它运行期惰性**（sink 只在 `status=="failed"` 时读该键）。**实测 6 条红**（`junit-itemB-m4c-behaviour.xml`：`tests=202 failures=6`）——因为 dry-run 路径上 `import_result` 是 `None`，`.get` 抛 `AttributeError`。**「读一遍觉得惰性」不等于惰性**；改成条件式 `… if import_result else None` 后才是真惰性（§53.32.8）。

### 53.32.8 本轮交付的第二套不变量（机制 / 棘轮 / 人口）

落点：**同一个守卫文件**（`backend/tests/test_ledger_error_sanitization_guard.py`，一层表面一个文件，复用既有 AST 助手）。

机制（跨模块，因为 taint 要跨文件）：

```
_parse_modules()      一次 parse 一棵树（见下）
_functions/_own_nodes 只在本函数体内走，不下潜嵌套 def
_bindings()           dict 字面量键 + x[key] = value 两种产出拼法
_value_positions()    能供值的位置（不取条件）
_mapping_reads()      m.get(K) / m[K]，K 为常量字符串
_message_reads()      （Layer 1 共享）消息读，已含数字字段排除
_sink_keys()          从 sink 自己读出它消费的键 → ("error","reason")
_producer_taint()     fixpoint：绑到消息读 ⇒ 污染；绑到「读被污染产出方的键」⇒ 也污染
_provenance_offenders 写手函数内，绑到 sink 键且继承被污染产出方 ⇒ 违规
```

**七条实现要点（每条都由一条控制钉住）**：

1. 🔴 **一次 parse**：`_parent_map` 用 `id(node)` 查表，**树与父表必须同一次 parse**。原型里我 parse 了两次 ⇒ 查表恒 `None` ⇒ `type(exc)` 排除**静默失效** ⇒ 两处**假阳性**。由 `test_it_does_not_flag_a_class_name_only_read_under_a_sink_key` 钉住（若重新引入两次 parse，这条立刻红）。
2. **`_own_nodes` 不下潜嵌套 def**：`run_post_match_backfill` 里有嵌套的 `finish(result)` 闭包，它会调 sink；若下潜，闭包的 `result` 参数会被当成外层函数的参数 —— **那正是本节要判的那个绑定**。
3. **`await` 要拆**：`result = await asyncio.to_thread(f)` 的值是 `Await` 不是 `Call`；不拆则 `events.py:812` 的产出方**解析不出来**，于是它**靠侥幸**通过而不是靠判据。
4. **sink 键是推导的**：从 sink 的 `error=` 实参里读出它引用**自己参数**的键 ⇒ `("error","reason")`。`_finish` 读的是 `"reason"`，写死 `{"error"}` 会漏掉它。
5. **产出方要一层解析 + 传导**：`validate_*_pipeline` 本身不写消息，但它 `conn.get("error")` 把 `test_*_connection` 的键**传导**过来 —— 不做 fixpoint 就看不出这条链。
6. **参数读 = 契约边界，允许**：`_finish_audit_run(run_id, result)` 读自己的 `result` 是对的（调用方承诺了安全 token）；**承诺在调用方建 dict 处被检查**。这是一条**原则**（`src in _params(func)`），不是名录。
7. **`x[key] = value` 也要收**：`world_cup_api_football_source` 用 `result["error"] = …` 建失败信息，只看 dict 字面量会**整块漏掉**这个产出方。

人口（`analyse_app_provenance()`）：

| 读数 | 值 |
|---|---|
| 会写账本的函数 | **38** |
| sink 键（推导） | **`("error","reason")`** |
| 写手函数内绑定 sink 键的站点 | **49**（40 处 `error=` kwarg + 9 处 dict 键） |
| 能吐出原始异常文本的函数（taint 集合） | **10**（其中 6 个在 `"error"` 键下，5 个真产出 + 1 个继承） |
| **登记在案的继承站点** | **1**（`run_post_match_backfill` 从 `sync_world_cup_fixtures` 继承 `"error"`） |

**为什么是棘轮而不是「0 违规」**：状态量**今天确实违反**这条属性（§53.32.6 的政策与 §53.32.2 ② 相抵，那是取值决定，§0 不代决）。所以断言做成**精确集合比对**：

- 属性**照判**（`_provenance_offenders` 每次都跑），
- 集合**精确比对** `_RECORDED_INHERITING_SITES`（1 条，**不带行号**：`文件 (函数): '键' inherits '键' from 产出方`，所以编辑挪行不会误报），
- ⇒ **多一条 relay 红**，**少一条也红**（名录过期也算红）。

这不是豁免表：豁免表是「**不报**」，棘轮是「**报，并点名**」。登记点旁边写清了它为什么站着（两条行为测试钉住它）、以及对面政策族的 6 条测试行号。

### 53.32.9 变异与门

**四相（关掉/打开/还原/字节复核）**，全部判据取 junit 的 `failures`/`errors`：

| 变异 | 变更 | 守卫文件 | 行为测试 |
|---|---|---|---|
| **M4**（上一轮的收尾实验，记录在案） | `post_match:212` → 改回 `sync_result.get("error")`（当时守卫断言 `offenders == ()`） | `tests=21 failures=1` ✅ 点名该处 | 4 文件 `tests=66 failures=0` ⇒ **行为测试全盲** |
| **M4'** | `result_fact_backfill:108` → `"error": import_result.get("error")` | `tests=21 failures=1` ✅ 点名新 relay | 4 文件 `tests=65 failures=1` ⇒ **重叠**（被 `test_result_fact_backfill_failure_hides_and_does_not_persist_path` 抓到） |
| **M4''** | `result_fact_backfill` **成功信封**插入 `"error": import_result.get("error") if import_result else None` | `tests=21 failures=1` ✅ 点名新 relay | **两批共 202 条全绿**（`tests=52 failures=0` + `tests=150 failures=0`）⇒ **非重叠**，只有源码级守卫看得见 |

- M4' 的**重叠是信息**：说明那个信封是 §53.32.6 政策族钉住的 token —— 也反过来印证「该动产出方，不该动 relay」。
- M4'' 是**新增方向**的干净证明：一条**运行期惰性**的 relay（成功信封上绑 `"error"`，sink 根本不读），**任何黑盒测试都不可能抓到**，而棘轮抓到了。
- **还原**：三次 `revert` 全部 `byte-identical to the backup: True`；`world_cup_result_fact_backfill_service.py` 回到 `543a1c748b06d7fa`（= **HEAD 原状**，`git status` 里不出现）；`world_cup_post_match_backfill_service.py` 的 M4 还原回到 `f8ff2a0742ddb4ff`。
- **四道门**（还原后）：`ruff check app/ tests/test_ledger_error_sanitization_guard.py` → `All checks passed!`；`mypy app/` → `Success: no issues found in 331 source files`；`generate_types --check` → `generated-types.ts is up to date.`；守卫文件 `tests=21 failures=0 errors=0 skipped=0`。

🔴 **一处前提被改、结论未变的显式记录（§4.1）**：本项把「数字字段不算消息」并入了 **Layer 1 共享的** `_message_reads()`。既有不变量（`missing_exc_forward` / `non_literal_failed`）的读数**逐项未变**：331 模块 / 61 写手调用 / 38 带 `error=` / **22** 消息读站点 / 0 / 0 —— 与 §53.31 记的完全一致。⇒ 该改动对 Layer 1 **行为中性**，但它**确实改了共享定义**，故在此重述而非静默。

### 53.32.10 未决：契约冲突只差 1 行，但按 §0 不代决

**冲突**（两边都在仓库里、都被测试钉住）：

| 一侧 | 位置 | 要求 |
|---|---|---|
| **政策族**（6 条） | `test_world_cup_analytics_routes.py:141/:187/:912/:1013/:1070/:1173` | 账本 `error` = **声明 token**（或 `f"…: {type(exc).__name__}"`）；URL host 在禁列 |
| **相抵的一条** | `test_world_cup_match_service.py:514` | `assertIn("network down", result["error"])` —— 要求**裸消息**出现在返回 dict 里 |

**建议的处置**（**未执行**，等拍板）：

1. `world_cup_match_service.py:322`：`"error": str(e)` → `"error": f"Football-Data.org request failed: {type(e).__name__}"`
   —— 与本仓 5 处既有惯用语同形（`main.py:76`、`events.py:802`、`world_cup_quality_service.py:736`、`world_cup_post_match_backfill_service.py:298`、`world_cup_api_football_source.py:295`），**按 §53.32.3 的定义属于可归因**，且**类名仍可见**（诊断价值不丢，只丢 host/path/query）。
2. 随之把 `test_world_cup_match_service.py:514` 的 `assertIn("network down")` 改成 `assertIn("RuntimeError")`
   —— **这一步是契约取值，按 §0 不代决。**
3. 做完之后 §53.32.8 的棘轮应自然变成**空集**（登记点消失）⇒ 更新 `_RECORDED_INHERITING_SITES` 为空元组即可，守卫无需改判据。
4. **不做的代价（照实说）**：今天这条链能到达账本 `error` 列与 `/quality-metrics/anomalies`（**0 鉴权依赖**）。当前**实证到的暴露是「上游主机名/路径/query 文本」**；**未实证到凭据泄漏** —— 两个 provider 的 key 都走 **header**（`X-Auth-Token` / `x-apisports-key`），不进 URL。而 provider **响应体**（`response.text[:200]`，见 `football_data_source.py:86`）在 `:315` 的 token 分支被丢弃，且这一点由 `:1173` 专门断言。

### 53.32.11 明确不覆盖（本守卫的四条边界，与 §9d 末条同步）

1. **只判「绑定处是不是直接/间接读了一个 mapping」** ⇒ **不判运行期可达性**：给成功信封上的 `"error"` 绑定也会被报（M4'' 正是利用这一点做非重叠证明）。这是**故意的保守**。
2. **不追局部变量链**：`err = m.get("error")` 再 `"error": err` 这种**间接拼法，本守卫不报**（taint fixpoint 只沿「绑定的值」传播，站点检查要求值本身是 mapping 读）。
3. **不覆盖非写手函数里的中转**：调用方若**不是**写手函数（例如某个 route 建好 dict 交给服务去写账本），那一跳不在扫描范围内。今天 49 个站点全在写手函数内，但这是**今天的事实**不是不变量。
4. **不覆盖 `result` 里嵌的 `"sync"` 子字典**：sink 只读顶层键；上游文本仍会出现在响应体与 `result_json["sync"]["error"]`（§53.32.2 ④）。**这是另一件事**（「同一 0 必须只说一件事」的姊妹问题：**同一个字段在不同出口不同治理**），本项只登记。

### 53.32.12 文件画像与字节证据

**本轮唯一的 repo 内非文档改动**（`backend/app/services/world_cup_post_match_backfill_service.py`）：`6 行注释 + 1 行注释` ⇒ `git diff --numstat` **`7 0`**（**删除数 0**，符合「插入型编辑的删除数必须是 0」）；12,505 B / **311 CRLF** / 0 bare LF / 无 BOM / 无 `\r\r\n` / sha16 `5ef80a0ac8418c83`。

**守卫文件**（`backend/tests/test_ledger_error_sanitization_guard.py`，Write 工具产物 = **LF**，本轮逐次 Edit 追加，**Edit 保留原行尾**，故全程 CRLF 未被破坏）：

| 时点 | sha16 | bytes | CRLF | bare LF |
|---|---|---|---|---|
| §53.31 记录态 | `ca66caf917c6cac7` | 14,396 | 353 | 0 |
| 本轮：Layer 2 移植后（未加棘轮） | `bf7a4f9a8b9c2f58` | 38,625 | 919 | 0 |
| 本轮：棘轮版 | `4e08246171ce4878` | 41,453 | 972 | 0 |
| **本轮终态**（补政策指针注释后） | **`05d8d03226b7eb79`** | **41,934** | **978** | **0** |

⚠️ **§53.31.11 记的 14,396 B / `ca66caf917c6cac7` 是当时态**；本项在同一文件上追加了第二套不变量（§0：前提被改的结论要重述，不静默矛盾）。文件现 **978 行**，其中约 560 行是 AST 机制 —— **体积偏大**；若将来再扩，建议把机制抽到 `backend/tests/_ledger_ast.py`（**只登记，不做**：抽与不抽是取舍，且会让本轮的四相证据失效）。

**未改动的三个文件**（`git status` 里不出现，证明变异全部干净还原）：`world_cup_result_fact_backfill_service.py`（9,242 B / 258 CRLF / `543a1c748b06d7fa`）、`world_cup_match_service.py`（10,854 B / 326 CRLF / `64885fce36ae7161`）、`docs/ops/RUNBOOK.md`（87,752 B / **1,645 CRLF** / `e8b8e70563a0af5d`，与 §53.31 记录一致）。

**本节追加读数**：见本节末的 BEFORE/AFTER 打印（`append_generic.py`），验收三条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

### 53.32.13 更正：`result_json` 里没有 `"sync"` 子字典（§53.32.11 第 4 条的 (b) 半句作废）

**结论先行**：§53.32.11 第 4 条的后半句「上游文本仍会出现在响应体与 `result_json["sync"]["error"]`」**只有前半句成立**。`loop_runs.result_json` **不含 `"sync"` 键、也不含 `"error"` 键** —— 写进去的是 `_audit_summary(result)`，它只留 `sync_status`。本小节是**追加更正**（不改写原句，§0 交付记录只追加），并给出原始文本全部出口的普查。

**为什么写错（可复用的教训）**：我读的是 `loop_run_store.finish_run(..., result=…)` 的**形参名**，就推断「传进去的是整个 result ⇒ `result_json` 里有 `sync`」。真实调用点是 `world_cup_post_match_backfill_service.py:56-66` 的 `_finish_audit_run`，它传的是 **`result=_audit_summary(result)`**（`:35-53`）——一个只保留标量与 `sync_status` 的**投影**。⇒ **判「某列承载什么」必须看调用方传的表达式，不能看被调函数的形参名**（同族：§9d ④「人口按 AST 调用节点数，字面量只能定位、不能计数」）。

**实证（跑一次，不是读一遍）**：

```
from app.services.world_cup_post_match_backfill_service import _audit_summary
s = _audit_summary({
    "status": "error",
    "sync": {"status": "error", "error": "...network down at upstream.example", "reason": None},
    "error": "...network down at upstream.example",
})
print(sorted(s.keys()))
# ['audit_metadata','candidate_count','dry_run','errors','quality_samples',
#  'result_facts_imported','scored','skipped','source','stale_unfinished_count',
#  'stale_unfinished_fixtures','status','sync_status']        # 无 'error'、无 'sync'
json.dumps(s, ensure_ascii=False) 含 'upstream.example' -> False
json.dumps(s, ensure_ascii=False) 含 'network down'     -> False
```

**原始文本的全部出口（普查，五处）**：

| # | 出口 | 承载的键 | 数据来源 | 未类型化异常路径下原文可达？ | 鉴权 |
|---|---|---|---|---|---|
| ① | `POST /api/analytics/post-match-backfill` 响应体 | `result["error"]` **与** `result["sync"]["error"]` | `finish()` 把整个 `result` 返回给路由，路由原样 `return`（`world_cup_analytics.py:535`，`sync_first=True` 硬编码） | ✅（需 `dry_run=false`） | `require_write_key` 依赖 |
| ② | `GET /api/analytics/post-match-backfill/runs` | `runs[].error` = **账本 `error` 列** | `_audit_run_payload` → `run.get("error")` | ✅ | 无 |
| ③ | `GET /api/quality-metrics/anomalies` | `detail.error` = **账本 `error` 列** | `latest_run_per_job()` → `run.get("error")` | ✅ | 无 |
| ④ | `loop_runs.result_json` 列 | —— | `_audit_summary(result)` | ❌ **不可达**（无 `sync`、无 `error` 键） | —— |
| ⑤ | 调度器 job 的 `result_json` | —— | `_world_cup_post_match_backfill_summary`（`scheduler.py:1621`）/ `_summarize_prediction_update`（`:1572`） | ❌ **不可达**（只取 `candidate_count` + `scoring.scored/errors`） | —— |

**更正后的精确表述**：`result["sync"]["error"]` 只出现在**一处** HTTP 出口——`POST /post-match-backfill` 的响应体，**且与同一响应里的 `result["error"]` 是同一份文本**（sync 子字典**不是独立的第二个泄漏**，只是同一响应里更深一层）。真正由**账本 `error` 列**带出去的出口是 ②③（**列**，不是 `result_json`）。

**一个副产品读数**：§53.32.6 说的政策族「四处」（账本 / 响应 / `anomalies` / 日志）**恰好覆盖 ①②③**；而政策测试 `test_post_match_backfill_does_not_expose_or_persist_provider_error_body`（`:1173`）断言的是 `post_response.text + history_response.text` 的**整段文本**（`assertNotIn(fragment, exported)`）⇒ 它**其实也覆盖了 `sync` 子字典**，只是走的是**有类型的** `FootballDataAPIError` 路径（该路径在 `world_cup_match_service.py:315` 已被声明 token 丢弃）。**未被覆盖的仍是 §53.32.10 那一条**：未类型化异常在 `:322` 走 `str(e)`，会同时进入 ① 的两个键与 ②③ 的账本列。

**与 §53.32.11 的关系**：第 4 条**保留** (a)「上游文本仍会出现在**响应体**」，**删除** (b)「与 `result_json["sync"]["error"]`」。原句不改写（只追加本小节）；读 §53.32.11 时以本小节为准。

**本节追加读数**：见本节末的 BEFORE/AFTER 打印（`append_generic.py`），验收三条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

### 53.32.14 §53.32.10 契约冲突的「改哪几个断言会红」**实测**清单（只登记不代决）

**结论先行**：把 `world_cup_match_service.py:322` 的 `"error": str(e)` 换成声明 token 后，**全量 7693 用例里恰好 2 条变红**，且**这 2 条恰好等于 §53.32.10 建议里点名的两处** ⇒ 清单完整、无隐藏的第三个消费者。但其中一条**不是行为测试**，而是**守卫自己的棘轮** —— 也就是说那句话在这条线上有**三个**消费者（行为契约 1 / 守卫棘轮 1 / 政策族 0）。

**方法（四相）**：

| 相 | 动作 | 读数 |
|---|---|---|
| ① 备份 | 字节级复制到 `.workbuddy/audit-2026-10-03/wcms_backup.py` | 10,854 B / 326 CRLF / sha16 **`64885fce36ae7161`**（= 未改动态） |
| ② 变异 | `mutate_conflict.py apply`（针唯一性前置校验 `count == 1`） | 10,903 B（**+49**）/ 326 CRLF（**不变**）/ sha16 `8c3833ef69ee4708` |
| ③ 全量 | `pytest tests/ --junitxml=…`（判据只取 junit 的 failures/errors） | `tests=7693 failures=2 errors=0 skipped=11` |
| ④ 还原 | `mutate_conflict.py revert` | `byte-identical to the backup: True`；sha16 回 **`64885fce36ae7161`**；**`git status` 与 `git diff --numstat` 对该文件均为空** |

**红集（2 条，逐条附实测消息）**：

| # | 测试 | 实测消息 | 性质 |
|---|---|---|---|
| 1 | `tests.test_world_cup_match_service.SyncWorldCupFixturesTests::test_api_football_error_returns_error`（`:508-514`） | `AssertionError: 'network down' not found in 'Football-Data.org request failed: RuntimeError'` | **行为契约**：该测试 `patch(…, side_effect=RuntimeError("network down"))` + `assertIn("network down", result["error"])` ⇒ 它**就是**在钉「裸消息必须出现在返回 dict 里」 |
| 2 | `tests.test_ledger_error_sanitization_guard.LedgerErrorProvenanceTests::test_the_inheriting_sites_are_exactly_the_recorded_ones`（`:845`） | `AssertionError: Tuples differ: () != ("services/world_cup_post_match_backfill_s…",)  Second tuple contains 1 additional elements` | **守卫的棘轮**：计算侧已是 `()`，登记项还留着 1 条 ⇒ **棘轮设计成「多一条红、少一条也红」**，这里正是「少一条」在响 |

⚠️ **第 2 条是对 §53.32.10 第 3 条措辞的更正**：原话「棘轮应自然变成空集 ⇒ 更新 `_RECORDED_INHERITING_SITES` 为空元组即可」——**「更新」是一个必须执行的动作，不是一个自动结果**。不更新它就有一条红。措辞上它读起来像「无需处置」，实测不是。

**为什么其余候选**不**红（逐条给判据，不是「大概不会」）**：

| 测试 | 为什么不红 |
|---|---|
| 政策族 6 条（`test_world_cup_analytics_routes.py:141/:187/:912/:1013/:1070/:1173`） | 各自的敏感文本走**别的服务**（consistency repair / verified correction / result_fact_backfill / scoring reconcile / post_match 自身的 `except` 与 typed `FootballDataAPIError`）——**没有一条**经过 `world_cup_match_service.py:322` 的未类型化分支 |
| `test_world_cup_predictions_routes.py:97` `…_does_not_expose_exception_text` | 它走的**就是**未类型化分支，但 `/world-cup/predictions/sync-fixtures`（`world_cup_predictions.py:167-180`）在 `status == "error"` 时**丢弃 result**、抛固定 `HTTPException(500, "Fixture synchronization failed")` ⇒ 它断言的是 `assertNotIn`，换成 token 同样通过 |
| `test_world_cup_post_match_backfill_service.py:216/:224` | 把 `sync_world_cup_fixtures` **整个 mock 掉**（`:202` `return_value={"status":"error","error":"source unavailable"}`）⇒ 与产出方内部格式**解耦**（这正是 §53.32.2 的 relay 测试，它钉的是 relay，不是产出方） |
| `test_world_cup_match_service.py:544` | 走 typed `FootballDataAPIError` 分支（`:315`），那一行**未动** |

**「建议处置是否自洽」的实测判据**：建议是三处改动 —— (1) 改 `:322`；(2) 把 `:514` 的 `assertIn("network down", …)` 改成 `assertIn("RuntimeError", …)`；(3) 置空 `_RECORDED_INHERITING_SITES`。实测红集 = {`:514`, `:845`} ⇒ **(2)(3) 各消一条、无剩余** ⇒ 三步自洽。第 (3) 步「置空即可、守卫判据不用改」**成立且完整**，证据两条：① 失败消息显示**计算侧已经是 `()`**（只剩登记项多余）；② 同一文件里那条会**构造**继承站点的用例（`:895` `test_it_flags_inheriting_a_sink_key_from_a_tainted_producer`）用的是**合成三文件**（`sink.py` / `producer.py` / `caller.py`，见 `:888-893`），与真实树解耦 ⇒ **它在本轮全量里没红**。

**仍未决**：第 (1)(2) 步是**契约取值**（「返回 dict 里是裸消息还是声明 token」），按 §0 **只登记不代决**。本轮只给出「改了就红谁」的实测读数，不动任何一行。

**本节追加读数**：见本节末的 BEFORE/AFTER 打印（`append_generic.py`），验收三条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## 53.33 「契约比实现宽」的同类普查：两个方向、14 处可折叠元组，以及一个**活的**过宽声明（`optional_write_key`）

承接 §53.25.9 登记的待办 ②（原话：「已知同族候选是那些自带 `except (TypeError, json.JSONDecodeError)` 的 store（`optimized_params_store.py:205/225`）」）。本项**只读普查**，不改任何一行。

### 53.33.1 结论先行

1. **候选待办已裁决**：`optimized_params_store.py` 那两处**只有一半成立** —— `:225` 的 `TypeError` **可证明是死的**（过宽声明），`:205` 的 `TypeError` **可证明是活的**（元组准确）。详见 §53.33.6。
2. **B 方向（捕获宽于需要）**：全仓 **255** 个多成员 `except` 元组里 **14** 个含**可证明冗余**的成员（§53.33.5）—— 全部是**无害**的冗余枚举，但会误导读者以为在区分多种失败模式。
3. 🔴 **A 方向（契约宽于实现）查出一个活样本**：`api/security.py:36 optional_write_key` 的 docstring 写 *"Unlike `require_write_key` this never raises"*，而实测**能抛 `TypeError`**，且**有两条独立触发路径**（§53.33.4）。这与 §53.25 的 `read_json` **同形**，且落在**鉴权**路径上。
4. ⚠️ **诚实边界**：本部署 `API_WRITE_KEY` 长度为 0 ⇒ `bool("")` 短路 ⇒ **当前不可达**。它是**潜伏**缺陷，不是现行故障。

### 53.33.2 两个方向必须先分开，否则普查会做成一锅

「契约比实现宽」原指 §53.25 那一类：**声明**（docstring / 类型 / `except` 子句）覆盖的集合，与**实现实际能产生**的集合**不等**。它是**双向**的，且两个方向的**危害相反**：

| 方向 | 形态 | 危害 | 本次读数 |
|---|---|---|---|
| **A 契约宽于实现** | docstring 说「never raises / is total」，实现仍能抛 | **承诺过宽**：调用方按契约不设防 ⇒ 异常穿透 | 24 条声明，其中 **12** 条无兜底 handler；**1 条经实测为真缺陷** |
| **B 捕获宽于需要** | `except (A, B)` 里 A ⊂ B | **冗余枚举**：读者以为区分了多种失败模式；若其中之一其实不可能 ⇒ 幽灵分支 | 255 元组 / **14** 可折叠 |

判据只有一条、两个方向共用：**把「声明的集合」与「可实现/可命中的集合」都算出来，看差集是否为空且可枚举。**

### 53.33.3 A 方向普查：24 条绝对化声明，12 条无兜底 —— 但这是一个**筛**，不是判决

普查件 `.workbuddy/audit-2026-10-03/census_contract_width.py`（AST，24 条声明的**行号全部从源码派生**，没有手抄）：

- 只读 **函数 docstring**。grep 的 30 行里混着**注释**（如 `event_intelligence_service.py:681`）与**模块级 docstring**（如 `match_outcome.py:15`）——二者**都不在**本样本内，本项只对**函数级**声明下判（⚠️ 因此 **30 与 24 不是「差 6 个」的关系**，是**类别不同**）。
- 判定列 `broad_handler` = 该函数内**是否存在**能吸收意外类的 handler（`except Exception` / `BaseException` / 裸 `except`）。

| 读数 | 值 |
|---|---|
| docstring 绝对声明 | **24** |
| 其中**无**兜底 handler | **12** |
| 有兜底 handler | 12 |

🔴 **这个筛会过报，而且我有一条现成的校准样本**：`utils/file_store.py:171 read_json` 落在「无兜底 handler」那一档 —— 但它**在 §53.25 刚被修好**（docstring 改窄为 *"it degrades, never raises"*，并把会抛的两类都收进 handler）。⇒ **`broad_handler=False` ≠ 缺陷**：它只说明「该声明依赖所有被调函数也 total」，**逐条判真伪必须看调用图**。**本项不对这 12 条下结论**，只登记入口。

### 53.33.4 A 方向的**活样本**：`optional_write_key` 的「never raises」是假的（实测）

`api/security.py:8-12` 的 `is_write_key_valid` 用 `hmac.compare_digest`：

```python
def is_write_key_valid(x_api_key: str | None) -> bool:
    return bool(settings.API_WRITE_KEY) and hmac.compare_digest(
        x_api_key or "", settings.API_WRITE_KEY,
    )
```

**实测 `hmac.compare_digest` 对含非 ASCII 的 str 的行为**（§4⑰：跑一次，不读一遍）：

```
compare_digest('ascii', 'ascii')  -> True
compare_digest('ascii', '')       -> False
compare_digest('ascii', '密钥')    -> RAISES TypeError: comparing strings with non-ASCII characters is not supported
compare_digest('', '密钥')        -> RAISES TypeError      ← 注意：一侧为空也照样抛
compare_digest('ascii', 'é')      -> RAISES TypeError
```

**端到端复现**（直接调用两个依赖函数，`api/security.py`）：

| `API_WRITE_KEY` | 请求头 | 结果 |
|---|---|---|
| `''`（**本部署**） | 任意（含非 ASCII） | **不抛** —— `bool('')` 短路，`compare_digest` 根本没被调用 |
| `'ascii-key'` | `None` / ASCII | 不抛 |
| `'ascii-key'` | **非 ASCII**（`é-…`） | 🔴 `optional_write_key` **RAISES TypeError** |
| `'密钥'`（非 ASCII 配置） | 任意 | 🔴 **必抛** |

`require_write_key` 同一根因、同样抛（`is_write_key_valid` 是共用实现）：

```
key='密钥'      hdr=None    -> RAISES TypeError: comparing strings with non-ASCII characters is not supported
key='ascii-key' hdr='é-bad' -> RAISES TypeError
```

⇒ **两条独立触发路径**：① 运维把 key 配成非 ASCII ⇒ **任何**请求都中；② key 是 ASCII，但**请求头含非 ASCII**（Starlette 按 latin-1 解码头，字节 ≥0x80 即非 ASCII）⇒ **客户端可控**地让该请求中。

**后果分两档**（照实说）：
- `optional_write_key`（`quality_metrics.py:422`、`world_cup_predictions.py:346` 两个 `Depends`）：**声明的契约被违反** —— 它承诺「不抛、由调用方决定」，实际抛 ⇒ FastAPI 回 **500** 而不是 `can_dispatch=False` 的正常分支。
- `require_write_key`（所有写路由）：TypeError 取代**本该给出的 401** ⇒ 也是 500。它**没有**「never raises」的声明，所以不算 A 方向；但**同一根因**、**后果更重**。

🔴 **零守卫**：全仓 `backend/tests/` 里 **没有任何一处**引用 `compare_digest` 或 `is_write_key_valid`，也没有非 ASCII key 的用例 ⇒ 这条声明**没有被任何测试钉住**（同族：§4.2 的「空头」——负控/无控都是假安全）。

### 53.33.5 B 方向普查：255 个多成员元组，14 个含可证明冗余的成员

`census_contract_width.py` 把每个元组成员解析到**真实类**（只用一张**显式**的 `CLASSES` 映射表，覆盖本次出现的全部名字；解析不了的报 `unchecked` 而不是猜）。判据：**某成员是其某个同组成员的后代** ⇒ 捕获它已经不改变该元组能命中的类集合。

| 读数 | 值 |
|---|---|
| 多成员元组 | **255** |
| 可折叠 | **14** |
| `unchecked`（名字无法解析） | **0** |

**清单（14 处）**：

| 元组 | 处数 | 冗余成员 | 等价于 |
|---|---:|---|---|
| `(HTTPError, URLError, TimeoutError, OSError)` | **12** | `HTTPError`、`URLError`、`TimeoutError` | **`except OSError`** |
| `(HTTPError, URLError, TimeoutError)` | 1（`world_cup_match_service.py:65`） | `HTTPError` | `except (URLError, TimeoutError)` ⚠️ **不能收成 `OSError`**（那会**放宽**） |
| `(HTTPError, URLError, TimeoutError, JSONDecodeError, UnicodeDecodeError)` | 1（`world_cup_team_stats_service.py:75`） | `HTTPError` | `except (OSError, JSONDecodeError, UnicodeDecodeError)` ⚠️ 同前，不可收成 `OSError` |

12 处同形元组分布在 `football_live_{availability,injury,referee,schedule,style,weather,xg}_service.py`、`nba_live_{injury,ratings}_service.py`、`nhl_live_xg_service.py`、`mlb_live_park_service.py`、`market_totals_service.py`。

**折叠的证明是实跑的**（不是读一眼认定的）：

```
issubclass(TimeoutError, OSError) -> True      (内建 TimeoutError)
issubclass(HTTPError,  URLError)  -> True
issubclass(HTTPError,  OSError)   -> True
issubclass(URLError,   OSError)   -> True
=> 4 成员元组 ≡ except OSError : True
```

**分类（重要，别把它们当缺陷）**：这 14 处**都不是缺陷**，是**冗余枚举**。它们的危害只有一条：**下一个读者会以为这四种是不同的失败模式**。⇒ 与 §53.32.11 的 4 条边界同类，**只登记**。

📌 **本项自动判据抓不到候选待办那两处** —— 因为 `TypeError` 与 `json.JSONDecodeError` **没有继承关系**（`JSONDecodeError ⊂ ValueError`，与 `TypeError` 是兄弟）。⇒ 冗余有**两种**：**继承型**（本节能自动查）与**控制流型**（下一节，只能逐点看）。

### 53.33.6 候选待办的裁决：`:225` 死、`:205` 活（实测，两处各给证明）

`optimized_params_store.py` 两处的上下文不同，结论**相反**：

| 位置 | 守卫 | 实测（喂非 str 值） | 裁决 |
|---|---|---|---|
| `:205` `json.loads(previous_applied["factor_weights"])` | 只有**真值**检查（`:202` `if previous_applied and previous_applied.get("factor_weights")`） | `5` → **caught TypeError**；`{'a':1}` → **caught TypeError**；`'"ok"'` → `'ok'`；`'not json'` → **caught JSONDecodeError** | ✅ **两个名字都活 ⇒ 元组准确，不是缺陷** |
| `:225` `json.loads(elo_params_raw) if isinstance(elo_params_raw, str) else elo_params_raw` | **`isinstance(..., str)` 把非 str 路由到 `else`** | `5` → **returned 5**；`['x']` → returned；`None` → returned；`'"ok"'`→parsed；`'not json'` → caught | 🔴 **`TypeError` 永不可达 ⇒ 过宽声明**（无害） |

证明脚本直接把两处的**表达式形状**隔离出来跑（不依赖 store、不碰数据库）：`:225` 在 `raw=5/['x']/None` 下**直接返回**，异常分支一次都没进；`:205` 在 `val=5/{...}` 下**确实进** `except TypeError`。

⇒ **§53.25.9 的候选待办结论**：**一半成立**（`:225` 的 `TypeError` 是死的），**一半不成立**（`:205` 是准的）。两者都**无害**，登记即可。

### 53.33.7 明确不做 / 未决

- ⏭️ **不改 14 处冗余元组**：它们是既有约定的一部分（`*_live_*` 系列同形），改一处反而制造不一致；且「收成 `OSError`」在两处会**放宽**捕获（§53.33.5 表内的 ⚠️）。
- ⏭️ **不改 `is_write_key_valid`**：修法是 1 行（比较前换成 bytes，或对非 ASCII 显式失败），但**放不放进本批是契约取值**，按 **§0 只登记不代决**。登记的建议：`hmac.compare_digest((x_api_key or "").encode("utf-8"), settings.API_WRITE_KEY.encode("utf-8"))`（bytes 版没有非 ASCII 限制）。
- ⏳ **A 方向另外 11 条无兜底声明未逐条核**（本节已证 `optional_write_key` 是**假**、`read_json` 是**已修**）⇒ 逐条需要调用图，**登记为待办**。
- ⛔ 不改 `:225` 的 `except`（删掉 `TypeError` 是**纯清理**、无行为变化；但**删死代码不可登记为变异**（§4.2），且它现在是那份名单上的一个已知点，删了反而少一处可追踪样本）。

### 53.33.8 文件画像

本节**未改动任何 repo 文件**（纯只读普查）。普查件 `census_contract_width.py` 与输出 `census-contract-width.txt` 在 `.workbuddy/audit-2026-10-03/`（**纯 LF**）。

**本节追加读数**：见本节末的 BEFORE/AFTER 打印（`append_generic.py`），验收三条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## 53.34 修 `is_write_key_valid`：把「非 ASCII ⇒ TypeError」这条契约拉回实现（用户点选 A；1 行实改 + 4 条用例）

承接 §53.33.4 查出的活样本。**本项动代码**（此前 §53.32–§53.33 都是只读），因此**写了测试 + 做了四相变异 + 跑了四道门**。

### 53.34.1 结论先行

- **已修**：`is_write_key_valid` 改成在 **UTF-8 字节**上比较 ⇒ `optional_write_key` 的 *"Unlike `require_write_key` this never raises"* **重新为真**；`require_write_key` 在 key 含非 ASCII 时**重新给出它声明的 401**（此前是 `TypeError` 逃逸）。
- ⚠️ **同时更正 §53.33 我自己写的一句**：§53.33.4 写「请求头含非 ASCII（Starlette 按 latin-1 解码，**客户端可控**）」——**结论本身成立**，但我在动手时**差点把它误判成不成立**：第一版测试用 `headers={"x-api-key": "é"}`（**str** 形式），被 httpx **在客户端侧**拒绝（`UnicodeEncodeError`），看起来像「这条路不存在」。**那是 harness 的限制，不是产品的事实**（§53.34.3）。
- 四相齐备（第 ② 相 **4/4** 新用例全红）；四门绿；**未提交、未推送**。

### 53.34.2 改动（`backend/app/api/security.py`，`git diff --numstat` = **`9 2`**）

```diff
 def is_write_key_valid(x_api_key: str | None) -> bool:
+    # compare_digest() rejects `str` operands that hold non-ASCII characters
+    # with a TypeError, and both operands here are controlled from outside this
+    # module (the request header, and API_WRITE_KEY): a single byte >= 0x80 in
+    # either one used to escape the auth dependency instead of answering -- see
+    # WriteKeyNonAsciiTests. The UTF-8 bytes are equally constant-time and carry
+    # no such restriction. (A lone surrogate would raise UnicodeEncodeError, but
+    # HTTP header values decode as latin-1, so it cannot arrive from a request.)
     return bool(settings.API_WRITE_KEY) and hmac.compare_digest(
-        x_api_key or "",
-        settings.API_WRITE_KEY,
+        (x_api_key or "").encode("utf-8"),
+        settings.API_WRITE_KEY.encode("utf-8"),
     )
```

**为什么是 bytes 而不是「先判断有没有非 ASCII」**：`compare_digest` 对 bytes 的**定长时间**语义与对 str 相同（这正是它存在的理由），而 UTF-8 是**单射**的 ⇒ `a == b`（str）⟺ `a.encode() == b.encode()`，**比较语义零变化**。若改成「检测到非 ASCII 就提前失败」，反而引入一条**按内容分支**的路径（正是我们要避免的那类）。⚠️ **残留边界**：孤立代理项（lone surrogate）会让 `.encode("utf-8")` 抛 `UnicodeEncodeError`；HTTP 头按 latin-1 解码 ⇒ 只可能是 0x00–0xFF ⇒ **从请求不可达**（已写进代码注释）。

### 53.34.3 ⚠️ 一条真实路径差点被测试 harness 判成不存在（可复用）

第一版两条 HTTP 用例写法是 `client.get(..., headers={"x-api-key": "é"})`，**跑出来是**：

```
UnicodeEncodeError: 'ascii' codec can't encode character '\xe9' in position 0: ordinal not in range(128)
```

失败发生在 **httpx 内部**（`httpx/_models.py:82 _normalize_header_value` → `value.encode(encoding or "ascii")`），**根本还没进被测应用**。若不追这一跳，就会得出「非 ASCII 头不可达 ⇒ §53.33 那条判据是假的」——**而这是错的**。实测三条：

| 探针 | 结果 |
|---|---|
| `httpx.Request(..., headers={'x-api-key': 'é'})`（**str** 形式） | RAISES `UnicodeEncodeError`（客户端侧） |
| `httpx.Request(..., headers=[(b'x-api-key', b'\xe9')])`（**bytes** 形式） | **OK**，`raw=[(b'Host', b'x'), (b'x-api-key', b'\xe9')]` |
| `Headers(raw=[(b'x-api-key', b'\xe9')])['x-api-key']`（Starlette） | `'é'`（**latin-1 解码**） |

⇒ **路径②成立**：客户端只要按**字节**发 0x80–0xFF，Starlette 就把它解成非 ASCII `str` 交给依赖。**httpx 只是不接受 str 形式，不是不接受这条路。**

🔴 **可复用判据**：**失败发生在 harness 内部时，先问「这是产品行为还是 harness 行为」**；判据是**失败栈里有没有被测代码**。本例的修法**不是删掉那条用例**，而是把值**换成 harness 能承载的形式**（bytes）并把断言留在**依赖边界**上。同族：§53.25.6 记的「脚本前置断言数错 ⇒ 一个字节都没写」——两者都是**变通处不在被测对象，而在工具链**。

### 53.34.4 四条新用例各自钉什么（`tests/test_operational_readiness.py`，新增类 `WriteKeyNonAsciiTests`）

| 用例 | 钉住 | 为什么这样写 |
|---|---|---|
| `test_non_ascii_configured_key_does_not_raise` | key 含非 ASCII 时 `optional_write_key` **返回 bool**（`None`→`False`、对值→`True`），且 `require_write_key` 抛的是 **`HTTPException` 401** | 直接钉 §53.33 的两条声明（"never raises" + 401） |
| `test_non_ascii_configured_key_is_a_401_not_an_escaped_error` | **经 HTTP**：key 非 ASCII + **不带** header ⇒ **401** | 非 ASCII 只在**配置**里 ⇒ httpx 能承载（这是唯一能在 TestClient 里走通的 HTTP 形态） |
| `test_raw_client_byte_reaches_the_dependency_as_non_ascii` | 把 `Headers(raw=[(b"x-api-key", b"\xe9")])` 解出的值**自己算出来**（`assertEqual(header_value, "é")`）再喂给依赖 ⇒ **401** | 值**不手写**：解码器变了这条测试跟着变；钉的是路径②真实落点 |
| `test_non_ascii_key_authenticates_its_own_value` | key 非 ASCII 且**值正确** ⇒ `True` | 防止「只在错误路径上不抛、正确路径反而被改坏」 |

**四相变异（判据只取 junit 的 failures/errors）**：

| 相 | 动作 | 读数 |
|---|---|---|
| ① 修复前基线 | 新增用例 + 修复态跑 `tests/test_operational_readiness.py` | **`tests=91 failures=0 errors=0 skipped=0`** |
| ② 变异 | 字节级把 `security.py` 退回备份（`eadf8a49267f88d4`） | **`tests=91 failures=4 errors=0`** —— 4 条新用例**全红**，症状统一 `TypeError: comparing strings with non-ASCII characters is not supported` |
| ③ 还原 | 字节级恢复修复态 | **`tests=91 failures=0 errors=0 skipped=0`** |
| ④ 字节一致 | sha256 比对快照 | **`byte-identical to the fixed snapshot: True`**（`2e5172a4cdfdca0b`） |

⇒ **4/4 承重**：每条新用例都在变异下变红（不是空头）。用例总数 90 → 91（新增 4 − 替换掉 3 条走不通的第一版）。

### 53.34.5 门与字节证据

| 门 | 读数 |
|---|---|
| `ruff check app/` | **All checks passed!** |
| `mypy app/` | **Success: no issues found in 331 source files** |
| `python -m scripts.generate_types --check` | **generated-types.ts is up to date.** |
| `pytest tests/test_operational_readiness.py`（junit） | `tests=91 failures=0 errors=0 skipped=0` |
| `pytest tests/`（**全量回归**） | **`tests=7697 failures=0 errors=0 skipped=11`**（改前同口径 `7693`/11 ⇒ 差额 **+4 = 本轮新增的 4 条用例**；改的是**共享鉴权函数**，故跑全量） |
| 两个改动文件是否在 `mutation_verify.py` 的 `m.path` 名单里 | **都不在** ⇒ 守卫测试 `test_mutation_verify.py` 的必跑判据不触发（§4.3） |

| 文件 | bytes | CRLF | bare LF | BOM | `git diff --numstat` | sha16 |
|---|---:|---:|---:|---|---|---|
| `app/api/security.py`（改前） | 1,764 | 44 | 0 | False | `9 2` | `eadf8a49267f88d4` |
| `app/api/security.py`（改后） | 2,364 | 51 | 0 | False | — | **`2e5172a4cdfdca0b`** |
| `tests/test_operational_readiness.py` | — | — | 0 | False | `60 1` | — |

`security.py` 的**删除数 2 是正确的**：这是**真代码改动**，不是「只加注释」——§0 那条「插入型编辑删除数必须为 0」不适用。

### 53.34.6 明确不做 / 未决

- ⛔ **不改 `require_write_key` 的形状**：它本来就不承诺「不抛」（它的契约是**抛 401**）；本次修好的是**它抛的对不对**。
- ⏳ **`tests/test_operational_readiness.py` 里有一条 `F401`（`from app.main import app, lifespan` 的 `lifespan` 未使用）** —— 实测**在 `HEAD` 版本上就报**（`git show HEAD:… | ruff check --stdin-filename …` ⇒ `Found 1 error`）⇒ **既有**，非本项引入；且项目门是 `ruff check app/`（**不含** `tests/`）⇒ **只登记，不顺手改**（顺手改会把一处**既有**问题混进本次改动）。但它值一眼：`lifespan` 被导入却不用，**可能**意味着那条用例本来想驱动 lifespan 而没驱动 ⇒ 登记为待办。
- ⏳ **未加端到端（TestClient + bytes 头）用例**：§53.34.3 已实测 bytes 形式在 httpx 里可用，但把它加进去只多覆盖「harness 转发原始字节」这一段（**harness 性质，不是产品性质**），而解码这一跳已由用例 ③ 从**真实解码器**派生。**登记为可选加固。**
- ⏭️ **不提交**。

### 53.34.7 文件画像

三个「工作台件」在 `.workbuddy/audit-2026-10-03/`（纯 LF）：`security_backup.py`（改前快照 `eadf8a49267f88d4`）、`security_fixed.py`（改后快照 `2e5172a4cdfdca0b`）、四相的 junit（`junit-fix-phase1/2/3.xml`、`junit-fix-full.xml`）。

**本节追加读数**：见本节末的 BEFORE/AFTER 打印（`append_generic.py`），验收三条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## 53.35 `lifespan` 的 F401 背后没有空头 —— 但「出厂默认值」这一类守卫确实没人看（用户点选 B）

承接 §53.34 留下的那条既有 lint（`tests/test_operational_readiness.py:221` `from app.main import app, lifespan`，`lifespan` 导而不用）。用户的假设是：**「用例本来想驱动 lifespan 却没驱动」**。查下来**假设不成立**，但顺着它查出一条**同类且更值钱**的缺口。

### 53.35.1 结论先行

- **F401 = 残留导入，不是空头。** `test_lifespan_refuses_keyless_boot_without_opt_in` **确实驱动了** lifespan —— 走 `with TestClient(app):` 的 ASGI lifespan 协议；`lifespan` 这个名字**不参与驱动**（同文件另外 14 条 lifespan 用例都只 `from app.main import app`）。**两条独立证据**见 §53.35.2。
- 🔴 **真正的新发现**：`config.py` 里三个**姿态开关的出厂默认值**，**没有任何测试观测**。对 P0 级的 `ALLOW_OPEN_WRITES` 做出厂默认变异（`false`→`true`，即「无密钥也能起来」），**7 个文件 178 条测试读数完全不变（0 red）**；宽集（**所有提到该开关的测试文件**，33 个）见 §53.35.4。
- 机制是 §4.2(a)「夹具默认值锁死一侧」的直接实例：`StartupGuardTests` 的负向臂把开关**显式钉成字面量** ⇒ 它们钉的是**分支**，不是**出厂默认**。
- 本节**未改动任何 repo 文件**（只读取证 + 一次可逆变异，已逐字节还原；`git status --porcelain backend/app/core/config.py` 空）。**未提交、未推送。**

### 53.35.2 为什么 F401 不是空头：两条独立证据

**证据一（源码链路）**：`starlette/routing.py:629-654` 在 startup 失败时先 `send({"type": "lifespan.startup.failed", ...})`、**紧接着第 652 行 `raise`** ⇒ 原异常从 app 协程逃出；`starlette/testclient.py:710-715` 的 lifespan 任务在 `finally` 里再 `send(None)`；`testclient.py:717-732` 的 `wait_startup` 收到 `failed` 后会**再收一次消息**拿到那个 `None` ⇒ 走 `self.task.result()` ⇒ **原样重抛**。⇒ 不经过任何包装。

**证据二（实测，`probe_testclient_lifespan_propagation.py`，纯 LF，环境 `.venv` = CPython 3.11.9 / starlette 1.3.1 / httpx 0.28.1）**：Part 1 是**探针自身的正向控制**（合成 app，不碰项目代码），Part 2 打真 app。

| 用例 | 抛出的类型 | `isinstance(RuntimeError)` | 是同一个对象 | 裸 `assertRaises(RuntimeError)` |
|---|---|---|---|---|
| `control-ValueError`（合成） | `builtins.ValueError` | False | **True** | **MISS** |
| `control-OSError`（合成） | `builtins.OSError` | False | **True** | **MISS** |
| `control-RuntimeError`（合成） | `builtins.RuntimeError` | True | **True** | PASS |
| `real-keyless-no-opt-in` | `builtins.RuntimeError` | True | n/a | PASS |
| `real-lifespan-ValueError`（打 `validate_production_config`） | `builtins.ValueError` | False | **True** | **MISS** |
| `real-lifespan-OSError`（同上） | `builtins.OSError` | False | **True** | **MISS** |

三条推论：
1. **`assertRaises(RuntimeError)` 不是类型盲**：换一个非 `RuntimeError` 的起因，`assertRaises(RuntimeError)` 立刻 MISS ⇒ 这是**真类型检查**（Part 1 是它的正向控制：若探针分辨不出类型，Part 2 的「类型被保留」在空扫描上恒真）。
2. **`same_object=True`** ⇒ 抛的是**同一个异常对象**，不复制、不包装。
3. `real-keyless-no-opt-in` 抛出的消息**逐字等于** `main.py:229-231` 的 `raise RuntimeError(...)` ⇒ **raise 站点被钉住**，这条用例的绿不是「恰好有个 RuntimeError」。

✅ **顺带确认 `test_no_assertionless_tests.py:46-49` 的豁免理由是成立的**：该表把 `test_lifespan_boots_with_key`（零断言）登记为豁免，理由写 *"the property is that app startup completes; **TestClient raises if it does not**"* —— 上面证据一/二同时证明**这句是真的**。⇒ **该豁免不是空头**（详见 §53.35.3 的残留顾虑）。

### 53.35.3 ⚠️ 那条豁免理由只是「被顺带钉住」（可复用判据）

`test_no_assertionless_tests.py` 的豁免表是**双向棘轮**（`test_every_assertion_free_test_is_declared` 抓未声明的空断言用例，`test_no_allowlist_entry_is_stale` 抓过期条目），看起来严密。但**理由本身不可执行**——它是一条关于 **harness 行为**的断言，写在字符串里。

真正会因「上游开始包装异常」而变红的是两处 `assertRaisesRegex`：

| 位置 | 断言 | 名义职责 |
|---|---|---|
| `test_operational_readiness.py:263` | `assertRaisesRegex(RuntimeError, "bad loop db")` | 产品：坏 loop DB 必须中止启动 |
| `test_production_preflight.py:372` | `assertRaisesRegex(RuntimeError, "OPENAPI_ENABLED")` | 产品：生产预检必须拒绝启动 |

⇒ 一旦 Starlette 改成包装（或 `testclient.py:731-732` 那第二次 `receive()` 被删），**报错会指向安全守卫**（"the P0 guard broke"）而**不是 harness** —— 信号指向错误的位置。

🔴 **可复用判据**：**理由里出现的前提，要检查它 (a) 可执行 (b) 有正向控制**。「零断言但被声明豁免」的用例，其**全部价值**都压在理由上 ⇒ **理由必须比用例本身更容易被验证**，否则就是用一句话顶替一条测试。同族：§53.31「给调用点不变量补静态守卫」。

### 53.35.4 🔴 三个姿态开关的「出厂默认值」零观测（实测）

**静态事实（`grep` 穷举）**：测试里对 `ALLOW_OPEN_WRITES` / `LLM_STARTUP_CHECK_ENABLED` / `SCHEDULER_ENABLED` 的**断言**，全部打在**模板 / stdout / systemd unit** 上：

| 断言位 | 打的是什么 |
|---|---|
| `test_production_deploy_consistency.py:236` | `assertNotEqual(assigned.get("ALLOW_OPEN_WRITES","").lower(), "true")` —— **部署模板** |
| `test_verify_local_stack.py:32/43`、`test_check_write_auth.py:70`、`test_run_entrypoint.py:144` | 子进程的 **stdout 字符串** |
| `test_deploy_units.py:207/215` | systemd unit 里的 `Environment=SCHEDULER_ENABLED=...` |

**没有一处**断言 `settings.<KEY>` 的默认值。（测试文件里该键名大量出现，但绝大多数是 `patch.object(settings, …)` ⇒ **全是分支**。）

**机制**：`StartupGuardTests` 的负向臂把开关**显式钉成字面量** —— `:224` `ALLOW_OPEN_WRITES=False`、`:352` `LLM_STARTUP_CHECK_ENABLED=False`、`:430` `SCHEDULER_ENABLED=False`（`:232-243` 那条是它的正向孪生，`assertEqual(started["n"], 1)`）。

**定向变异**（`mutate_allow_open_writes_default.py`，字节级；针 `b'_env_bool("ALLOW_OPEN_WRITES", "false")'` **恰好 1 处**才写，否则一个字节都不写）：

先证**变异是活的**（否则「全绿」会得出反的结论）：`os.environ` 无该键、`backend/.env` **根本没提**它 ⇒ 生效值来自代码默认（`config.py:263`）。改前 `settings.ALLOW_OPEN_WRITES = False`，应用变异后实测 **`= True`**。

| 口径 | 测试集 | ① 基线（未变异） | ② 变异后（默认 `false`→`true`） |
|---|---|---|---|
| 窄集 | `test_operational_readiness` / `test_production_preflight` / `test_env_overlay_examples` / `test_no_assertionless_tests` / `test_startup_health_integrity` / `test_sentry_integration` / `test_alert_channel_posture` | `tests=178 red=0` | **`tests=178 red=0`** |
| 宽集 | **33** 个文件 = **所有提到 `ALLOW_OPEN_WRITES` 的 `tests/*.py`** | `tests=611 red=0` | **`tests=611 red=0`** |

📌 **宽集变异那一次进程 rc = 1，而 junit 是 `red=0`** —— 这就是本机 `safe-delete` shim 的指纹（日志里有 1 处 `[safe-delete]`，且它拦的是**收尾清理**而不是用例）。**判据只取 junit 的 `failures`/`errors`，不看 rc。** 差一点据此把「出厂默认翻成 fail-open 无人察觉」写成「有人察觉了」。

字节证据：`config.py` `96260 B / crlf 1839 / bare_lf 0 / sha16 811e638907eb365b` → 变异 `96259 B / crlf 1839 / sha16 f55e5ec105a34980` → 还原 **`byte-identical to snapshot: True`（`811e638907eb365b`）**，`git status --porcelain` 该文件**空**。

📌 `backend/app/core/config.py` **不在** `backend/scripts/mutation_verify.py` 的 `m.path` 名单里 ⇒ 本次变异**不触发**守卫测试 `test_mutation_verify.py`（不改其 `note`、不登记为新 set）。

**模板层另有守卫，但它守的是另一个人群。** `test_env_overlay_examples.py:15-23` 的 `SECURITY_KEYS`（5 个：`ALLOW_OPEN_WRITES`/`API_WRITE_KEY`/`CORS_ALLOWED_ORIGINS`/`SERVER_RELOAD`/`LLM_DAILY_COST_CAP_USD`）要求**每个 overlay 显式赋值**（含 `false`，因为「继承 dev 值不安全」）。实测三个模板都赋了值：`.env.example:57` `ALLOW_OPEN_WRITES=false`、`.env.staging.example:35`、`.env.production.example:83`。
⚠️ 但它**不覆盖「不复制模板的部署」**：裸容器、或只给 `API_WRITE_KEY` 的 systemd 单元 —— 那些看到的就是**代码默认**。**那正是该守卫自己 docstring（`:216-218`）里叫 P0 的那条状态。**

**三个开关的风险方向不对称**（登记用，**均不代决**）：

| 开关 | 默认 | 翻向 | 后果 | 级 |
|---|---|---|---|---|
| `ALLOW_OPEN_WRITES` | `false` | → `true` | **fail-open**：无密钥也能起来，写接口全公开 | 🔴 危险 |
| `SCHEDULER_ENABLED` | `true` | → `false` | 作业**全停**，而 `/api/health` 照常 200 ⇒ **静默** | 🟠 次之 |
| `LLM_STARTUP_CHECK_ENABLED` | `false` | → `true` | fail-closed：坏凭证**拒绝启动** | ⚪ 无害 |

⚠️ 末者**不宜做变异**：翻成 `true` 会让每条启动用例真去 `complete_chat()`（`llm_startup_check_service.py:9`）⇒ 变异引入网络与超时，读数不可读。

### 53.35.5 一条 §5 口径的界限更正（避免下次误用）

`MEMORY` 的 §5 写着「`.env` 模板**只写注释行**（`# KEY=true`），**绝不写成赋值**」。**这不是全局规则** —— 本仓有**两个相反的约定并存**：

- `SECURITY_KEYS`（`test_env_overlay_examples.py:15-23`）：**要求每个 overlay 显式赋值**（含 `false`）。
- **其它**开关：在 overlay 里**只点名、不赋值**（`test_the_production_overlay_names_the_flags_it_leaves_off`）。

⇒ 引用 §5 那条时**先问「这个键属不属于 `SECURITY_KEYS`」**；两条规则各自都有人守，混用会把一条正确的约定判成违规。

### 53.35.6 未决（只登记，不代决）

1. 三个出厂默认值是否各加一条断言（最低成本形态：`assertIs(settings.ALLOW_OPEN_WRITES, False)`）。**取舍权在用户。**
2. `test_lifespan_does_not_check_llm_by_default`（`:347`）的**名字超出其函数体**：名字说「**默认**不检查」，函数体却 `patch.object(settings, "LLM_STARTUP_CHECK_ENABLED", False)` 把值钉死 ⇒ 它证的是「显式关掉时不检查」。改名还是补默认断言，**不代决**。
3. `lifespan` 那条 F401：删掉 import 里的 `lifespan` 即可（`ruff --fix` 一行）。**在项目门覆盖范围之外**（门只扫 `app/`），且是 **HEAD 上就有**（HEAD 行号 **162**，由 `ec0e1d7` 引入）⇒ **只登记，不顺手改**。

**本节追加读数**：见本节末的 BEFORE/AFTER 打印（`append_generic.py`），验收三条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## 53.36 给「出厂默认值」补守卫：两层 + 一类新变异（用户点选 A；动代码）

承接 §53.35 的实测缺口。**本项动代码**（新增 1 个测试文件，不改生产代码），因此**做了四相变异（含一类新变异）+ 跑了四道门 + 全量回归**。

### 53.36.1 结论先行

- 新增 `backend/tests/test_posture_switch_defaults.py`（**18 条用例**；纯 LF / 13,039 B / 298 行 / sha16 `6350c2e16daaa5c8`，`git status` 显示 `??` 未跟踪）。**未改任何生产代码** —— `config.py` 全程只被变异再逐字节还原，终态 sha16 `811e638907eb365b`。
- 三个出厂默认现在各有 **1 条声明层断言 + 1 条观测层断言**。
- 🔴 **用户给的最小形态 `assertIs(settings.ALLOW_OPEN_WRITES, False)` 被否掉**：它读的是**生效值**，会被环境 / `.env` 满足 ⇒ **在恰恰会配置该开关的机器上，它是空头**。§53.36.5 是决定性演示，不是论证。
- 🔴 **发明了一类新变异（shadow）**：翻字面量只让**声明层**红；**字面量不动、对象被改**只让**观测层**红。没有这一类，「观测层确实承重」就只能靠读代码来断言。

### 53.36.2 两层各测什么（判据）

| 层 | 做法 | 独立于什么 | 能抓到什么 |
|---|---|---|---|
| **声明层** `shipped_default_literals()` | AST 读 `config.py` 里 `_env_bool("KEY", "<lit>")` 的第二个实参 | **完全不读环境** | 有人**改了默认值字面量** |
| **观测层** `_reloaded_settings(drop=KEY, ignore_dotenv=True)` | 从 `os.environ` 摘掉该名 + **中和 `dotenv.load_dotenv`** + `importlib.reload(config)` | 环境与 `.env` **两层都被摘掉** | 字面量没动但**对象**被改（后续覆盖、换了读取方式、被 shadow） |

⚠️ **观测层没有用 `skip`。** 先例 `test_run_entrypoint.py:112` 的做法是 `skipIf(.env 里赋了该名, "…the shipped default is not observable here")` —— 但那会在**恰好配置了该开关的机器上把守卫静默关掉**。这里改成**中和 `load_dotenv`**：`config.py` 是 `from dotenv import load_dotenv`（`:1`），`importlib.reload` 会重新执行这行 `getattr` ⇒ **先 patch `dotenv.load_dotenv` 再 reload，`.env` 那一层就整个进不来**（实测 `load.called=True`，且 `Settings()` 无必需字段失败）。
✅ **有正向控制**：`assert load.called`。若 import 形式改成 `import dotenv`，patch 会**落空**，于是测试会**读着 `.env` 的值却看着全绿** —— 这种静默失效必须由断言挡掉。

### 53.36.3 四相变异（判据只取 junit；读数由 `junit_red.py` 汇总，**不看进程 rc**）

| 相 | 动作 | 读数 |
|---|---|---|
| ① 基线 | 未变异 | **`18 / red 0 / skipped 0`** |
| ②a 字面量 `ALLOW_OPEN_WRITES` `false`→`true` | `apply` | **`red 2`** = 声明层 `test_the_shipped_default_of_allow_open_writes_is_false` + 控制臂 `test_the_other_direction_moves_it_too` |
| ②b 字面量 `LLM_STARTUP_CHECK_ENABLED` `false`→`true` | `apply` | **`red 1`** = 仅声明层 `test_the_shipped_default_of_llm_startup_check_enabled_is_false` |
| ②c 字面量 `SCHEDULER_ENABLED` `true`→`false` | `apply` | **`red 2`** = 声明层 `test_the_shipped_default_of_scheduler_enabled_is_true` + 控制臂 `test_an_environment_variable_moves_the_value` |
| ②d **shadow** `SCHEDULER_ENABLED`（字面量不动，文件末尾追加 `settings.SCHEDULER_ENABLED = False`） | `apply-shadow` | **`red 1`** = **只有** `TestTheDefaultIsObservedNotOnlyDeclared::test_scheduler_enabled` ⇒ **观测层承重被隔离验证** |
| ③ 还原 | 每轮 `revert` | 每次回到 `18 / red 0`，且 `byte-identical to snapshot: True` |
| ④ 字节一致 | sha256 复核 | 每轮回到 `811e638907eb365b`（`git status --porcelain` 空） |

🔴 **②a 里观测层那条 `test_allow_open_writes` 是绿的 —— 这是设计，不是漏网。** 观测层拿 `_default_bool(KEY)`（**同一份被改过的源码**）当期望值 ⇒ 字面量一改，两边一起动。**声明层是唯一能看见「字面量被编辑」的那层**，这正是要两层的理由；②d 证明反过来也成立（只动对象时声明层全绿）。⚠️ 若把这条写成「观测层也红了」就等于在粉饰。
📌 **`SCHEDULER_ENABLED` 的 shadow 变异使 `bare_lf` 从 0 变 4**（追加的 4 个 `\n`）—— 这是**变异态**的临时特征，还原后回到 0；记在这里以免日后把工作树上的 4 个 bare LF 当成损坏。

### 53.36.4 一条写进测试的诚实边界

§53.36.1 说 naive 形式在**会配置该开关的机器上**是空头。本机 `backend/.env` 对这三个名**都没有赋值**（实测 `grep -cE "^\s*KEY\s*="` 三者皆 0，且无 overlay 文件）⇒ **在这台机器上 naive 形式恰好也好用**。这个差别不靠注释说明，靠 §53.36.5 的对照跑出来。

### 53.36.5 🔴 决定性演示：遮蔽环境下 naive 形式 PASS，本守卫照样红

步骤：`apply ALLOW_OPEN_WRITES`（**默认值此刻是 `true`**）+ 进程环境导出 `ALLOW_OPEN_WRITES=false`：

| 读数 | 值 |
|---|---|
| `settings.ALLOW_OPEN_WRITES`（**naive 形式读的就是它**） | **`False`** ⇒ `assertIs(settings.ALLOW_OPEN_WRITES, False)` **PASS —— 守了个寂寞** |
| 本守卫在**同一条件**下的 junit | **`red 2`**，与**无遮蔽**时的红集**完全一致** ⇒ 守卫与环境无关 |

⇒ **「默认值被翻成 fail-open」这件事，在遮蔽环境下只有本守卫看得见。** 这就是把「最小形态」换成两层的收益，也是它可被证伪的地方。

### 53.36.6 门与回归

| 门 | 读数 |
|---|---|
| `ruff check app/` | `All checks passed!` |
| `ruff check tests/test_posture_switch_defaults.py` | `All checks passed!` |
| `python -m scripts.generate_types --check` | `generated-types.ts is up to date.` |
| `mypy app/` | `Success: no issues found in 331 source files` |
| 全量 `pytest tests/` | `tests=6626 / red=1 / skipped=11` —— `1 failed, 6614 passed, 11 skipped, 1095 subtests passed`（1418.99s）；`safe-delete` 指纹 **0** ⇒ 真失败 |

🔴 **那唯一一条红不是本批的**：`tests/test_realtime_ticket_store.py::test_spawned_workers_share_atomic_consume_once`。四条判据（都可复算）：

1. **交集为空**：本批改动清单（新增 `tests/test_posture_switch_defaults.py` + 追加 §53.35/§53.36 + `.workbuddy/**`；`config.py` 只被变异后**逐字节还原**）与 `app/realtime/ws_auth.py`、`tests/test_realtime_ticket_store.py` **零相交**（`git status --porcelain | grep -E "ws_auth|realtime_ticket_store"` 无输出）。
2. **新用例本身全绿**：`pytest tests/test_posture_switch_defaults.py` → `18 passed, 6 subtests passed`。
3. **隔离复跑 3/3 全绿**：`pytest tests/test_realtime_ticket_store.py` → junit `tests=4 / red=0 / skipped=0` ×3（与 §51.11 判据③ 同口径）。
4. **异常是环境态而非断言态**：`sqlite3.OperationalError: attempt to write a readonly database ← spawned worker → app/realtime/ws_auth.py:142`（`spawn` 池）。这是 spawn 子进程写**另一进程刚创建的临时目录** SQLite 时拿到只读句柄 —— Windows（AV/索引器/临时清理）的经典瞬时态，**被测不变量（socket 只能核销一次）未被证伪**。同一用例、同一异常文本、同一落点已在 **§51.11** 与 `.workbuddy/memory/2026-10-02.md:126-129` 登记为既有偶发。

⚠️ **一处更正（§0）**：两次记录的 `tmp_path` 目录号**不同** —— 既有是 `…/pytest-of-Alin/pytest-3598/…`，本次是 `…/pytest-of-Alin/pytest-4055/…`。这是**每次 pytest 自增的计数器**，**不是同一目录**。同一性成立于「同一用例 + 同一异常文本 + 同一落点 + 同一机制」，**不含目录号**；此前若有「同一个 `pytest-3598` 目录」的读法，以本条为准。

📌 **为什么这一项的非全量回归不行**（不是形式主义）：新用例会 `importlib.reload(app.core.config)`，而 `test_config.py:107-135` 记着一次**真实泄漏** —— 恢复性 reload 若放在 `patch.dict` 块**内**，会把被污染的值留在**进程级对象**里，症状出现在一个**不相关的**时间戳排序用例上。本项因此：把恢复性 reload 放在块**外**，并且**断言清理生效**（`test_the_environment_is_restored_afterwards`）；而 `conftest.py:259-280` 的 autouse fixture 另有一道 canonical 单例恢复。

### 53.36.7 未决（只登记，不代决）

1. `LLM_STARTUP_CHECK_ENABLED` 的翻向是 **fail-closed**（无害），所以它只作为②b 的**变异**出现，没有被写成「危险方向」叙事。
2. 新工具 `mutate_posture_default.py`（`apply` / `apply-shadow` / `revert` / `status`，支持三个键）**取代** `mutate_allow_open_writes_default.py`；后者保留，因为 §53.35.4 按**文件名**引用了它。两者用不同快照路径，但**改的是同一个文件 ⇒ 不可同时运行**。
3. `tests/test_operational_readiness.py:221` 那条 `lifespan` F401 仍未清（§53.35.6 第 3 条）。
4. 三个默认值现在**各 1 条**断言；`DIRECTIONS` 式的「顺序即契约」类默认值未纳入本普查范围。

**本节追加读数**：见本节末的 BEFORE/AFTER 打印（`append_generic.py`），验收三条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## 53.37 §53.36.7 四项未决的处置（用户点选 D）

处置口径：**能实测的实测、能修的修、不能替用户定的只登记**。四项里第 2、3 条各改了一个文件（一个工作台工具、一个测试文件），第 1、4 条是取证 + 措辞更正。**本节不改生产代码。**

### 53.37.1 结论先行

| # | §53.36.7 的未决 | 处置 | 一句话结论 |
|---|---|---|---|
| 2 | 两个变异工具「改同一文件 ⇒ 不可同时运行」 | ✅ **已修**（工作台工具） | 各自的「快照已存在」检查**只看自己那条路径** ⇒ 先后各 `apply` 一次会把**变异态存成"原件"**，之后的 `revert` 忠实还原到变异态。改成扫 `config.py.snapshot*` 通配，任一存在即拒绝 |
| 3 | `test_operational_readiness.py:221` 的 `lifespan` F401 | ✅ **已清**（测试代码） | `from app.main import app, lifespan` → `from app.main import app`；`ruff` **正控成立**（HEAD 版仍报 F401） |
| 1 | `LLM_STARTUP_CHECK_ENABLED` 只作变异出现、未写「危险方向」叙事 | 📌 **维持叙事，更正措辞** | 翻 `true` **不是 fail-open**（不放大写入面）⇒ 不进危险叙事是对的；但「**无害**」过宽：它让**启动依赖 LLM 可达性**，且该检查跑在**写密钥守卫之前** |
| 4 | `DIRECTIONS` 式「顺序即契约」类未纳入普查 | 📌 **已做有界普查** | 该类**恰好 2 个成员**：`DIRECTIONS`（顺序**有钉**）与 `ENGINE_NAMES`（顺序**无人钉**，却有 4 处 dict-building 消费者）⇒ **不对称**，登记不代决 |

### 53.37.2 第 2 条：两个变异工具的互斥 footgun（已修）

`.workbuddy/audit-2026-10-03/` 下两个工具改同一个 `backend/app/core/config.py`：

| 工具 | 自己的快照路径 |
|---|---|
| `mutate_posture_default.py` | `config.py.snapshot.posture` |
| `mutate_allow_open_writes_default.py` | `config.py.snapshot` |

两者的 `apply` 前置检查都是 `if <自己的快照>.exists(): refuse`。**故：**

1. `mutate_posture_default.py apply SCHEDULER_ENABLED` ⇒ 写 `config.py.snapshot.posture`（= 原件）+ 改 `config.py`。
2. `mutate_allow_open_writes_default.py apply` ⇒ 它只看 `config.py.snapshot`（**不存在**）⇒ **不拒绝**；于是把**已被变异**的 `config.py` 存成**它认为的原件**。
3. 之后任一次 `revert` ⇒ 忠实还原到**第 1 步之后的变异态**。`git diff` 看得见（`config.py` 确实被改了），但**工具自己会打印 `byte-identical to snapshot: True`** —— 一个会自证的静默污染。

**修法**（保留两个文件名，因为 §53.35.4 按文件名引用旧工具）：把前置检查从「本路径存在」改成「**扫同目录 `config.py.snapshot*` 通配，任一存在即拒绝**」，并在拒绝信息里点名找到的快照。两个工具各加一行常量 `SNAPSHOT_GLOB = "config.py.snapshot*"`。

**证据**（端到端，都在工作树里跑）：

| 步骤 | 动作 | 读数 |
|---|---|---|
| a | 手放一个兄弟快照 `config.py.snapshot`（0 字节）后 `mutate_posture_default.py apply SCHEDULER_ENABLED` | `REFUSED: snapshot already present (config.py.snapshot)`，**rc=1**，`config.py` 仍 `811e638907eb365b`（零写入） |
| b | 同一条件下 `mutate_allow_open_writes_default.py apply` | 同样 `REFUSED`，**rc=1** |
| c | 移除兄弟快照后 `mutate_posture_default.py apply SCHEDULER_ENABLED` | `apply -> 8519d65307702e22`（快照 `811e638907eb365b`） |
| d | **跨工具**：`mutate_allow_open_writes_default.py apply` | `REFUSED: snapshot already present (config.py.snapshot.posture)`，**rc=1** |
| e | `mutate_posture_default.py revert` | `restored 811e638907eb365b` / `byte-identical to snapshot: True` / `snapshot removed` |
| f | 终态 | 无 `config.py.snapshot*` 残留；`git status --porcelain backend/app/core/config.py` **空** |

两个工具均仍为**纯 LF / 无 BOM**（`6071 B` / `4430 B`），`py_compile` 通过。⚠️ **两个工具都只被打包在 `.workbuddy/**`，永不入库**，所以本次改动**不进任何提交**。

### 53.37.3 第 3 条：`lifespan` F401（已清）

`backend/tests/test_operational_readiness.py:221` 的 `from app.main import app, lifespan` —— `lifespan` 全文件**仅此一处**出现（`:228` / `:302` / `:390` 都是注释或 docstring），删掉它不改变任何驱动路径（`with TestClient(app):` 走 ASGI 协议）。

| 项 | 读数 |
|---|---|
| 字节画像 | 改前 `72069 B` / sha16 `b00481707cbf33ba` → 改后 `72059 B` / sha16 `f78f34fab98ac20b`（**−10 = `", lifespan"` 的长度**）；仍纯 CRLF（`crlf=1602` / `bare_lf=0`）/ 无 BOM |
| `ruff check tests/test_operational_readiness.py` | `All checks passed!`（rc=0） |
| **正控**：对 `git show HEAD:<file>` 跑同一命令 | `F401 [*] app.main.lifespan imported but unused`（rc=1）⇒ **正是这处改动消掉的**，不是命令本身失效 |
| `git diff --numstat` | `61 2`（§53.34 收尾时是 `60 1` ⇒ **+1/−1 恰为本行**） |
| `pytest tests/test_operational_readiness.py` | junit `tests=75 / red=0 / skipped=0` |

**为什么这一处可以只跑定向集**（不是偷懒）：同一文件既**不在**任何变异针的 `m.path` 里，也**不被**任何变异当守卫 —— 这两条都是从 harness 自述**派生**的，不是手抄：
`mutation_verify.SETS` 的 `path` 全集 = **27 条**（无 `test_operational_readiness.py`）；`guards` 全集 = **87 个函数名**，逐个在本文件里 `def` 一遍 ⇒ **命中 0**。全量回归仍作为批次级门统一跑（读数见 §53.37.7）。

### 53.37.4 第 1 条：`LLM_STARTUP_CHECK_ENABLED` 的方向叙事（只登记，更正措辞）

§53.35.4 的方向表把它的「翻 `true`」写成 **fail-closed（无害）**，且注明**不宜做变异**（会真去 `complete_chat()`）。本次把这句话的**边界**查清了：

| 事实 | 位置 |
|---|---|
| 出厂字面量是 `false` | `config.py:192-193` |
| **唯一**消费点 | `app/main.py:214` `if settings.LLM_STARTUP_CHECK_ENABLED: await validate_primary_llm_startup()` |
| 失败时**抛**（不是警） | `services/llm_startup_check_service.py:24`（调 `complete_chat` 抛异常时）与 `:36`（`result.ok` 为假时）都 `raise RuntimeError` |
| 异常消息**已脱敏** | 同文件 `:20-21`：命中 `settings.OPENAI_API_KEY` 就替换成 `<redacted>`，并截断到 300 字 |
| **顺序**：该检查在写密钥守卫**之前** | `main.py:214` 早于 `:217-232` 的 `API_WRITE_KEY` fail-closed 块 |

⇒ **两条结论**：① 翻 `true` **不打开任何写入面**，所以「不进危险方向叙事」是**对的**；② 但「**无害**」是过宽的 —— 它把**启动**变成依赖 LLM 可达性的操作（LLM 不通就 `RuntimeError` 拒绝启动），而且它会**先于**写密钥守卫抛出，也就是一个无密钥部署届时看到的是**LLM 错误**而不是那条「API_WRITE_KEY is empty」的指引。⚠️ 这一条是**读源码**得出的，**没有实测**（按 §53.35.4 的理由：变异会引入网络与超时，读数不可读）—— 这一点必须写明，别让下一轮把「读来的」当成「量来的」。

### 53.37.5 第 4 条：`DIRECTIONS` 式「顺序即契约」默认值的有界普查（只登记）

§53.36.7 第 4 条说这类「未纳入普查范围」。本次把它**划成有界的一类**并测完。判据取 `DIRECTIONS` 自己的 signature：**一个有序容器（tuple/list），被迭代进 dict**（`{x: … for x in NAME}` 或 `dict.fromkeys(NAME)`）—— 因为 dict 键序跟随插入序、而本仓**不固定 `PYTHONHASHSEED`**，这类常量的顺序会**印进发布的 JSON**。只做 `in` / `sorted()` 用的关键词表**不属此类**，刻意不报。

**探针**：`.workbuddy/audit-2026-10-03/census_ordered_defaults.py`（AST，常量人群与每个消费者**全部派生**，不手抄）。

| 读数 | 值 |
|---|---|
| `app/**` 模块级「同质字符串序列」常量（不含 `__all__`） | **61** |
| 其中有序（Tuple/List） | **42** |
| 其中无序（Set） | **19** |
| **顺序泄漏进 dict（= `DIRECTIONS` signature）** | **2** |

两个成员，守卫状况**不对称**：

| 常量 | 定义 | dict-building 消费者 | 顺序有没有守卫 |
|---|---|---|---|
| `DIRECTIONS` | `utils/direction_vocabulary.py:127`，tuple n=4 | `api/routes/quality_metrics.py:97`（`fromkeys`）、`utils/metrics.py:285`（`fromkeys`） | ✅ **有**：`tests/test_direction_vocabulary.py:108` `test_directions_stays_an_ordered_sequence` = `isinstance(..., tuple)` **且** `== ("YES","NO","WAIT","AVOID")`；另有 `:88` 容器类型钉与 `:57` 的 `_EXPECTED` 值钉 |
| `ENGINE_NAMES` | `services/world_cup_quality_service.py:20`，tuple n=4 | `api/routes/world_cup_analytics.py:304`、`:323`；`services/world_cup_quality_service.py:863`、`:870` | ❌ **无**：全部比较都是集合式 —— `tests/test_gbm_engine.py:40` `assertIn("gbm", …)`、`tests/test_world_cup_quality_service.py:456` `assertEqual(set(ENGINE_NAMES), set(ENGINES) | {"integrated"})`、`tests/test_world_cup_prediction_pipeline.py:688` 用**集合推导** `{f"{name}_count" for name in ENGINE_NAMES}` |

**`ENGINE_NAMES` 的顺序确实出现在输出里**（所以它够格做契约），两条证据：
1. `world_cup_quality_service.py:863-873` 用它按序建 `by_engine` 与 `trends["by_engine"]`，随 `build_quality_loop_report` 的返回体发布；`world_cup_analytics.py:304` + `:321-327` 同理建响应 `by_engine`。
2. `world_cup_analytics.py:300-303` 的既有注释**自己记着**：该端点**曾自带一份"顺序不同"的引擎词表**、且"没有任何测试点名它"，被当缺陷修掉。⇒ 本项目此前已把「同一集合、不同顺序」当作**真实缺陷**处理过。

⇒ **本项只登记，不代决**：现在「`DIRECTIONS` 有顺序钉 / `ENGINE_NAMES` 没有」是**实测出的不对称**；要不要给 `ENGINE_NAMES` 也补一条顺序钉，属于**契约取值**（等于宣布当前顺序是契约），按 §0 留给你定。⚠️ 注意 `ENGINE_NAMES` 现有守卫的 docstring（`tests/test_world_cup_quality_service.py:446-452`）只论证「**成员陈旧**不可见」（`_summarize([])` 报空桶即绿），**不涉顺序** —— 所以它看起来"已守"，实际守的是另一件事（同族：§4.2「看到弱正向断言先假定它对那个分支空头」）。

### 53.37.6 副产物：两个 `app/` 文件带 UTF-8 BOM（既有，只登记）

`census_ordered_defaults.py` 第一次运行就 `SyntaxError: invalid non-printable character U+FEFF` —— 起因是 `app/**` 里有两个文件以 BOM 开头：

`app/services/llm_gateway_service.py`、`app/services/world_cup_tournament_state_service.py`

实测 `git show HEAD:<file>` **同样带 BOM** ⇒ **既有、已提交**，不是本批引入（这两个文件 `git status` 也未改）。影响面：任何用**裸 `utf-8`** 读源码的工具（本探针就是）会在第一行 `SyntaxError`；本仓 `ruff` / `mypy` / `pytest` 不受影响（它们容忍 BOM）。**只登记不代决**（剥 BOM 会让 `git diff` 出现全文件首行变化，属独立批次）。

### 53.37.7 门与回归

| 门 | 读数 |
|---|---|
| `ruff check app/` | `All checks passed!` |
| `ruff check tests/test_operational_readiness.py` | `All checks passed!`（正控见 §53.37.3） |
| `python -m scripts.generate_types --check` | `generated-types.ts is up to date.` |
| `mypy app/` | `Success: no issues found in 331 source files` |
| 全量 `pytest tests/` | `tests=6626 / red=0 / skipped=11` —— `6615 passed, 11 skipped, 1095 subtests passed`（1434.54s）；`safe-delete` 指纹 **0**；进程 `rc=0`（**全绿**） |

📌 **本次全量全绿，而 §53.36 那次同一代码态下是 `red=1`**（唯一红 = `test_realtime_ticket_store.py::test_spawned_workers_share_atomic_consume_once`）。两次唯一差别是**跑了几次**，第三次（本次）该用例绿 —— 与 §53.37.3 的「它不在任何针/守卫里」一起，进一步坐实它是**环境态偶发**而非本批引入。

📌 **跑前 / 跑后 sha 复核**（防长任务期文件回退）：`test_operational_readiness.py` `f78f34fab98ac20b`、`config.py` `811e638907eb365b`、`test_posture_switch_defaults.py` `6350c2e16daaa5c8` —— **PRE == POST**（`prerun-shas.txt` 留档）。

## 53.38 给 `ENGINE_NAMES` 补顺序钉：把「顺序即契约」的另一半补上（用户点选 A；动代码）

承接 §53.37.5 的有界普查（那一类**恰好 2 个**成员：`DIRECTIONS` 有顺序钉、`ENGINE_NAMES` 没有）。用户点选 A = **明确授权**把当前顺序宣布为契约 —— §0 的「契约取值不代决」在这里由用户点头解除。**本项动代码**：1 条新用例 + 1 条新变异。

### 53.38.1 结论先行

- 新增 `tests/test_world_cup_quality_service.py::WorldCupQualityServiceTests::test_engine_names_order_is_pinned`：`assertIsInstance(ENGINE_NAMES, tuple)` + `assertEqual(ENGINE_NAMES, ("elo_odds", "hybrid", "gbm", "integrated"))`。
- harness 新增 **W35**（`whitelist-fixtures` 第 9 位），与既有 **W8**（删成员）**同 `group="engine-names-literal"`** —— 两条改写**同一行**字节（一个删、一个换序）。
- 🔴 **加钉之前这条顺序确实无人守**：变异态下跑**完整**两个守卫文件（57 个测试）⇒ **恰好 1 红**，就是新写的那条。**这是量出来的，不是推出来的。**
- `list` 由 **71 → 72** 个变异；`test_mutation_verify.py` 仍 **`62 / red 0`**（四条不变量全过）。

### 53.38.2 待钉的那半边长什么样

| 项 | 值 |
|---|---|
| 定义 | `app/services/world_cup_quality_service.py:20` —— `ENGINE_NAMES = ("elo_odds", "hybrid", "gbm", "integrated")` |
| dict-building 消费者（顺序印进发布 JSON） | `build_quality_loop_report` 的 `by_engine`（`:863-866`）与 `trends` 的 `by_engine` 子表（`:868-873`）；`/analytics/engine-stats` 的 `grouped`（`world_cup_analytics.py:304`）与响应 `by_engine`（`:321-327`） |
| 为什么顺序可观测（不是审美） | `world_cup_analytics.py:300-303` 的既有注释：该端点**曾自带一份「顺序不同」的引擎词表**、且没有任何测试点名它，被当缺陷修掉 |
| **加钉前**全仓对它的比较 | 清一色**集合式**：`set(ENGINE_NAMES) == set(ENGINES) \| {"integrated"}`、`assertIn("gbm", …)`、`{f"{n}_count" for n in ENGINE_NAMES}`；连 `by_engine` 的断言也全按键索引（`report["by_engine"]["elo_odds"]`）⇒ **对键序天然盲** |
| 同类的另一半 | `DIRECTIONS` 早有顺序钉（`test_direction_vocabulary.py:108`） |

### 53.38.3 四阶段变异（判据只看 junit）

`scripts/mutation_verify.py verify whitelist-fixtures -i 9`：

```
[OK ] whitelist-fixtures  9. W35 ENGINE_NAMES 前两成员对调（同集合、不同顺序）
        green before True | red after mutation 1/1 files red | green after restore True | bytes restored True
        mutated run -> tests/test_world_cup_quality_service.py -> 1 failed, 18 deselected
```

### 53.38.4 🔴 决定性读数：「此前是空头」是量出来的

`verify` 只跑**点名的**守卫（`-k`），所以它**答不了**「别的测试会不会也抓到」。为此另做一次**宽集**：`backup` → `apply whitelist-fixtures 9`（只 W35）→ 跑**两个完整守卫文件** → `revert`。

| 读数 | 值 |
|---|---|
| 变异态下 `tests/test_world_cup_quality_service.py` + `tests/test_world_cup_analytics_routes.py` | **`tests=57 / red=1`**（`1 failed, 56 passed, 5 subtests passed`） |
| 那唯一的红 | `WorldCupQualityServiceTests::test_engine_names_order_is_pinned` —— **新写的那条** |
| `revert` 后 | `world_cup_quality_service.py` 回 `ae072d3dc62ec8f5`（**与跑前逐字节一致**）；该 set 目标文件 `git status` 全空 |

⇒ **换了顺序之后，57 个测试里只有新钉看得见。** 不做这一步，「没人守」就只是推理。

### 53.38.5 `group` 机制：为什么同组是必须的（不是整洁癖）

`_apply_preflight` 的实测输出：

```
refusing to apply, 1 problem(s):
  positions 8, 9 share group 'engine-names-literal': they rewrite the same bytes in opposite directions,
  so applying both would leave neither mutation in place
```

且**一个字节都没写**（`world_cup_quality_service.py` 仍 `ae072d3dc62ec8f5`）。⚠️ 若不给两条同 `group`：`apply whitelist-fixtures`（整组）应用到第二条时会因针 0 命中而崩；而 `tests/test_mutation_verify.py::test_grouped_mutations_rewrite_the_same_bytes` 还要求同组成员**同 `path` 同 `old`** —— W8/W35 恰好都满足（`old` 逐字节相同）。

### 53.38.6 门与回归

| 门 | 读数 |
|---|---|
| `ruff check app/` | `All checks passed!` |
| `ruff check scripts/mutation_verify.py tests/test_world_cup_quality_service.py` | `All checks passed!`（对 HEAD 版 `mutation_verify.py` 跑同一命令也全过 ⇒ 我引入的问题为 0） |
| `python -m scripts.generate_types --check` | `generated-types.ts is up to date.` |
| `mypy app/` | `Success: no issues found in 331 source files` |
| `mypy scripts/mutation_verify.py` | `Success: no issues found in 1 source file` |
| `pytest tests/test_mutation_verify.py` | `tests=62 / red=0 / skipped=0` |
| 全量 `pytest tests/` | `tests=6627 / red=1 / skipped=11` —— `1 failed, 6615 passed, 11 skipped, 1097 subtests passed`（1452.49s）；`safe-delete` 指纹 **0** ⇒ 真失败 |

📌 **计数算术自洽**：`6627 = 6626 + 1`（新用例）；`6627 = 6615 passed + 1 failed + 11 skipped`。§53.36 那次是 `6626 / red 0`、更早那次 `6626 / red 1` —— 三次唯一差别是**跑了几次**。

🔴 **那唯一一条红不是本批的**（四条判据，都可复算）：

1. **交集为空**：本批只改 `backend/tests/test_world_cup_quality_service.py`（+23/−0）与 `backend/scripts/mutation_verify.py`，与 `app/realtime/ws_auth.py`、`tests/test_realtime_ticket_store.py` **零相交**（`git status --porcelain | grep -E "ws_auth|realtime_ticket_store"` 无输出）。
2. **新用例本身全绿**：`pytest tests/test_world_cup_quality_service.py -k engine_names` → `tests=2 / red=0`。
3. **隔离复跑 3/3 全绿**：`pytest tests/test_realtime_ticket_store.py` → junit `tests=4 / red=0 / skipped=0` ×3（与 §51.11 判据③ 同口径）。
4. **异常是环境态而非断言态**：`sqlite3.OperationalError: attempt to write a readonly database ← spawned worker → app/realtime/ws_auth.py:142`（`spawn` 池）—— spawn 子进程写**另一进程刚创建的临时目录** SQLite 时拿到只读句柄，Windows（AV/索引器/临时清理）的经典瞬时态；**被测不变量（socket 只能核销一次）未被证伪**。同一用例已在 **§51.11** / **§53.37.6 第 4 条** / `.workbuddy/memory/2026-10-02.md:126-129` 登记为既有偶发。

📌 **跑前 / 跑后 sha 一致**（`prerun-shas-53-38.txt` 留档）：`test_world_cup_quality_service.py` `8fc7a157240643d2`、`mutation_verify.py` `c1882e64dc610a39`、`world_cup_quality_service.py` `ae072d3dc62ec8f5`、`test_operational_readiness.py` `f78f34fab98ac20b` —— **PRE == POST**，长任务期无文件回退。

📌 **为什么必须跑 `test_mutation_verify.py`**：本次改了 `backend/scripts/mutation_verify.py`，而它**在** `m.path` 名单里 —— §4.3 的判据是「diff 触及任何 `m.path` 就加进必跑列表」。

### 53.38.7 两条留痕（别让下一轮读成别的东西）

1. `scripts/` 下有一批**既有**的 F541（`audit_quality_consistency.py` 等），但项目门是 `ruff check app/`（**不含 `scripts/`**），且 `mutation_verify.py` 在 HEAD 版就干净 ⇒ **不是本项引入、不顺手改**（同族：§53.34 末尾那条 `tests/` 里的 F401 登记）。
2. `assertIsInstance(ENGINE_NAMES, tuple)` 比「钉顺序」多半步：顺序只在有序容器上才有意义，而 `DIRECTIONS` 那条钉同样先断言容器类型（模块注释也解释了 `set` 会让键序在运行间漂移）。这是**授权范围内的一致做法**，写明以免被读成顺手加料。

**本节追加读数**：见 `append_generic.py` 的 BEFORE/AFTER 打印，验收四条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## 53.39 剥掉两个 `app/` 文件的 UTF-8 BOM（用户点选 A；独立批次）

本节是 §53.37.6 那条「只登记不代决」的直接处置。当时不做的理由原文是「剥 BOM 会让 `git diff` 出现全文件首行变化，属独立批次」—— 现在把它当成那个独立批次来做。

### 53.39.1 结论先行

`app/services/llm_gateway_service.py` 与 `app/services/world_cup_tournament_state_service.py` 的 **UTF-8 BOM 已剥除**，**只删了各自文件头的前 3 个字节**，其余字节逐字节未动（`new == old[3:]` 两文件均为真）。工作树仍是**纯 CRLF**（`crlf` 计数不变、`bare_lf` 仍为 0），入库 blob 仍是**纯 LF**。四道门全过。**未提交、未推送。**

| 文件 | worktree 前 → 后 | crlf | bom | worktree sha16 前 → 后 |
|---|---|---|---|---|
| `app/services/llm_gateway_service.py` | 30443 → 30440 | 811 → 811 | True → **False** | `13a6131a92bd930f` → `f95bc825535f7f30` |
| `app/services/world_cup_tournament_state_service.py` | 6074 → 6071 | 159 → 159 | True → **False** | `05b8021c23d12406` → `6cd6f524292416bd` |

入库侧（HEAD blob，纯 LF）：`29632 → 29629`、`5915 → 5912`；HEAD blob sha `<HEAD>` 后由 `eddc8ca…`、`f8399c9…` 变为 `9fb3ead…`、`73d1f52…`（与 `git diff` 打印的新 index 一致，见 53.39.1 的读数）。

⚠️ **范围更正**：本节只处理用户点名的这 **2 个**。全仓字节级普查（53.39.8）显示带 BOM 的 tracked 文件**实为 15 个**（含 `backend/tests/` 2 个、`frontend/**` 2 个、4 个 `.diff` 证据文件、6 个 `docs/superpowers/**`）—— **其余 13 个只登记、不代决**。

### 53.39.2 怎么做的（字节级，只删前 3 字节）

`strip_bom_53_39.py`：读 bytes → 断言 `raw[:3] == b"\xef\xbb\xbf"` 且 `raw[3:6] != BOM`（防双 BOM）→ 原件另存 `.workbuddy/audit-2026-10-03/bom-backup-53-39/*.withbom` → 写 `raw[3:]` → 回读画像。

写前有一条**自证**：`stripped` 的 `crlf` 与 `bare_lf` 必须与 `raw` 相等（断言），即「删的 3 个字节不含任何 `\n`」。这条断言是必要的 —— §1 的教训是「读入-改-回写」一旦走文本 API 就会把整个文件的行尾改掉而 `git diff` 看不见；本处**全程 bytes**，且用断言把「行尾不变」变成可执行判据而不是靠眼睛。

`verify_bom_strip_53_39.py` 给出**后缀逐字节同一性**：

```
new == old[3:] ........... True   (两文件)
len(new) == len(old)-3 .. True   (两文件)
compile(utf-8) .......... OK     (两文件)
```

### 53.39.3 正控：BOM 不是装饰（裸 `utf-8` 的 AST 工具会炸）

剥 BOM 若只是「整洁」，就不值得开独立批次。`bom_positive_control_53_39.py` 用**同一段代码、同一个读法**分别解析「带 BOM 的备份」与「已剥离的现行文件」：

```
backup(.withbom)       SyntaxError: invalid non-printable character U+FEFF (<unknown>, line 1)
worktree(stripped)     OK
control fires: True    (两文件)
```

也就是说：任何走**裸 `utf-8`** 的解析路径在这两个文件上都会炸；此前不炸，只是因为**谁都没用那条路径读过它们**（`import` 那条路自己带 BOM/cookie 侦测，绕过了这个坑）。`ruff` / `mypy` / `pytest` 都容忍 BOM，所以这条缺陷**在 CI 上是不可见的**，只对第三方 AST 工具/脚本生效 —— 判它「值得修」的依据是**可解析性**，不是「整洁」。正控同时说明了修法的边界：修的是**文件**，不是**工具**（工具侧写 `encoding="utf-8-sig"` 只是绕过症状）。

### 53.39.4 §4.3 的必跑判据：本次**不触发**（派生事实，不是印象）

§4.3 的判据是「diff 触及任何 `m.path` ⇒ 把 `tests/test_mutation_verify.py` 加进必跑列表」。本处不靠 grep 印象，而是从**模块导出**读出全集：

```
import mutation_verify as m
{mu.path for s in m.SETS for mu in s.mutations}  ->  72 条变异 / 27 个 distinct path
两个 BOM 文件是否在集合内： NO
```

⇒ **不必跑 `test_mutation_verify.py`**，且这是**计算出来的**（`m.SETS` 是本仓唯一真相源；grep 常量名会漏掉间接引用）。留痕原因：下一轮若只看到「改了 `app/services/*.py`」很容易反射性地把它加进必跑列表，而**多跑不可证伪**、只会让「必跑列表」这个判据变松。

### 53.39.5 门与回归

| 检查 | 命令 | 结果 |
|---|---|---|
| lint | `ruff check app/`（在 `backend/` 下） | `All checks passed!` |
| 类型 | `mypy app/`（完整门） | `Success: no issues found in 331 source files` |
| 契约 | `python -m scripts.generate_types --check` | `generated-types.ts is up to date.` |
| 定向（tournament state） | `pytest tests/test_world_cup_tournament_state_service.py` | junit `tests=5 red=0 skipped=0` |
| 定向（llm_gateway 面） | `pytest` 21 个引用 `llm_gateway` 的测试文件 | junit `tests=363 red=0 skipped=0` |
| 全量 | `pytest tests/` | 见下方 `FULL_RESULT` |

`ruff check app/` 必须**在 `backend/` 下**执行：从仓库根跑 `ruff check app/` 会得到 `E902 系统找不到指定的文件 (os error 2) --> app:1:1`（因为 `app/` 在 `backend/` 下）。这不是代码问题，是**调用目录**问题，写下来免得下次误当成回归。

`FULL_RESULT`：**跑了两次**（判据只取 `failures`/`errors`，不看进程 rc；两次 `safe-delete` 指纹均为 **0**）

| 次 | 命令 | junit 读数 | 唯一红 |
|---|---|---|---|
| #1 | `pytest tests/` | `tests=6627 red=1 skipped=11`（`1 failed, 6615 passed, 11 skipped, 1097 subtests passed`, 1449.44s） | `test_sport_market_stores::test_get_latest_snapshot` |
| #2 | `pytest tests/` | `tests=6627 red=0 skipped=11`（`6616 passed, 11 skipped, 1097 subtests passed`, 1446.65s） | —— **全绿** |

⇒ 判据取「#2 全绿」，#1 的那个红**不可复现**（判定见 53.39.9）。**PRE == POST** sha 对照（`sha-53-39-before.txt` vs `-after.txt`，9 个热点文件逐条相同）⇒ 两次长任务期间**无文件回退**。

### 53.39.6 🔴 剥 BOM 的**前提过期面**：7 处（只登记，不代决）

这是 §4.1 那条「**结论会在前提被修掉之后静默过期**」的教科书级实例，也是本节最有价值的产出。

先把事实摆出来：**剥之前 `app/` 有 2 个 BOM 文件，剥之后 `app/` 有 0 个**（逐目录量）：

```
app            -> 0
app/services   -> 0
app/sports     -> 0
app/kernel     -> 0
tests          -> 2   (tests/test_llm_gateway_service.py, tests/test_probability_engine_llm_gateway.py)
```

本仓有一批 AST 扫描守卫，reads 时统一用 `encoding="utf-8-sig"`，其中若干处**把「某文件带 BOM」当成事实写进了注释/docstring/测试名**。剥 BOM 之后，凡是**以 `app/` 或具体文件为作用域**的那几句就**由真变假**（`test_elo_provenance.py` 那句「Two files in this repository carry a UTF-8 BOM」连全仓都不成立了 —— 全仓实为 15 个）：

| # | 位置 | 原文主张 | 作用域 | 剥 BOM 后 |
|---|---|---|---|---|
| 1 | `test_elo_provenance.py:296` | *"Two files in **this repository** carry a UTF-8 BOM"* | 全仓 | 🔴 **假**（全仓实为 15 个） |
| 2 | `test_event_store_source_names.py:63-64` | *"`app/services/llm_gateway_service.py` carries a BOM that ast.parse rejects"* | 具名文件 | 🔴 **假** |
| 3 | `test_ledger_error_sanitization_guard.py:315` | *"modules in **this tree** carry a BOM"* | `_APP_DIR`（`app/`） | 🔴 **假**（`app/` 现 0 个） |
| 4 | `test_runtime_stores.py:31-32` | *"at least one module in this tree carries a BOM"* | `_CONFIG_PATH`（单文件 `app/core/config.py`） | 🔴 **假**（该文件本就无 BOM） |
| 5 | `test_singleton_reset_census.py:73` | *"at least one source file carries a UTF-8 BOM"* | `_census()` 扫 `app/` | 🔴 **假** |
| 6 | **`test_singleton_reset_census.py:118`（测试名）** | **`test_scanner_reads_the_file_with_a_utf8_bom`** + docstring *"app/services/llm_gateway_service.py **starts with U+FEFF**"* | 具名文件 | 🔴 **假**（**唯一一处写在标识符里的**） |
| 7 | `test_no_assertionless_tests.py:99-101` | *"two files **in this suite** carry a UTF-8 BOM"* | `tests/` | ✅ **仍真**（`tests/` 恰 2 个，本节未动） |

三条判据性结论：

1. **7 处全部仍然 PASS**（本节全量与 21 文件定向跑都绿）。原因：`utf-8-sig` **在有/无 BOM 时都能正确解码** ⇒ 这些守卫的**行为**没变，变的只是它们**自述的理由**。⇒ 这是 **散文层（prose）缺陷，不是功能缺陷**；`ruff`/`mypy`/`pytest`/变异 harness **全都看不见它**（第 6 条尤其隐蔽：`test_scanner_reads_the_file_with_a_utf8_bom` 的**函数体**只断言 census 里有 `_client_cache`，**根本没检查 BOM** —— 所以它「名字在撒谎、身体仍然有效」）。
2. **「防 BOM 的写法」不等于「BOM 存在」**：`utf-8-sig` 是**正确的防御性默认**（对未来的新文件同样有效），**不该因为 BOM 被剥掉就改回 `utf-8`** —— 那会**降低**鲁棒性，且正好把 53.39.3 那条缺陷重新引入。要改的只有**叙述**。
3. **第 6 条是「标识符级前提过期」**，比注释更硬：注释至少跟着代码走，而**测试名会被 pytest 的输出、`-k` 过滤、报告直接引用** ⇒ 一行 `pytest -k utf8_bom` 的输出就会把一个假事实广播出去。**留痕给下一轮：改它要同时改函数名与 docstring，且改名会让任何按名点选它的地方失效（先 grep）。**

⚠️ **本节新增一条对既有记录的更正**：§53.37.6 写的「`app/**` 里有两个文件带 BOM」在**本节执行后已不再成立**（`app/` 现为 0）—— 那句话必须读作「**截至 §53.37 时**为真」，不要当现状引用（§4.1）。

**只登记不代决**：本节只做用户点名的「剥 BOM」，**没有**去改这 6 个测试文件的散文/标识符（那是另一个批次：**5 个**测试文件里 6 处假叙述 —— 第 7 条仍真、不必动；其中 1 处要 `grep` 名字引用）。上表即为待办清单。

### 53.39.7 一条记账更正（§0 纪律：前提被改的结论必须显式更正）

本节动手**之前**我写的预期是「`numstat` 删除数应为 **0**，因只删文件头 3 字节」。**这是错的**，实测两个文件各为 `1 1`：

```
1	1	backend/app/services/llm_gateway_service.py
1	1	backend/app/services/world_cup_tournament_state_service.py
```

原因：BOM 是**拼在第一行（docstring 首行）之前**的，删掉它 ⇒ 那一整行的字节变了 ⇒ git 按**行**记账，记 1 删 1 增。所以：

- **「删除数 == 0」是审计档追加（EOF 纯插入）的验收条款，不是本次代码改动的验收条款。** 两者别串用。
- 本次的等价判据是**字节级**的：「只删 3 字节」+「`new == old[3:]`」+「`crlf` 计数不变」，三条都已成立（53.39.2）。
- `git diff -U0` 的 hunk 数 = **2**，两个 hunk 都是 `@@ -1,2 +1,2 @@` ⇒ **只有首行变化**，与「只删 3 字节」互相印证（行级读数与字节级读数在这一处必须一致，不一致就说明别处也被改了）。

### 53.39.8 全仓 BOM 普查（只读）：**不是 2 个，是 15 个**

§53.37.6 说「`app/**` 里有两个文件」—— 那是**当时探针的作用域**（`census_ordered_defaults.py` 只解析 `app/` 源码），不是全仓结论。本次补一次**全仓字节级普查**（`census_bom_repo.py`，`git ls-files -z` + 未跟踪未忽略，读 bytes 不看行尾）：

```
tracked files: 1493
untracked-but-not-ignored files: 7
BOM files: 15
```

15 个命中的**全部**是 tracked（`git cat-file blob HEAD:<path>` 逐个复核，15/15 首位都是 `ef bb bf` ⇒ **全部已提交**，无一例外）。分布：

| 目录 | 数量 | 代表 |
|---|---|---|
| `backend/.sdd/*.diff` | 4 | `final-review-package.diff` 等（**diff 文件也带 BOM**） |
| `backend/data/openfootball-2026/` | 1 | `LICENSE.metadata.json` |
| **`backend/tests/`** | **2** | **`test_llm_gateway_service.py`、`test_probability_engine_llm_gateway.py`** |
| `docs/superpowers/plans` | 4 | 2026-07-05 一批 |
| `docs/superpowers/specs` | 2 | 2026-07-05 一批 |
| `frontend/src/components/detail` | 2 | `confidence-breakdown-panel.test.tsx`、`signal-summary.test.tsx` |

三条**派生结论**：

1. **这不是「`app/` 的卫生问题」，是「仓里 15 个文件的历史遗留」。** 本节只剥了用户点名的 2 个（`app/services/*.py`），**其余 13 个只登记、不代决** —— 剥它们＝再开一个批次（每个都会产生「首行变化」的 diff，其中 4 个还是 `.diff` 文件，改它们会改变**证据文件**的内容，需要单独判断）。
2. **BOM 对 `pytest` 无影响，有实证**：命中里有 2 个是**测试文件**，而它们**就在**本节 53.39.5 那次 21 文件定向跑里（`tests=363 red=0`）。⇒ 「BOM 会让测试炸」是**假的**；标准 import 机制自己会处理 BOM（与 53.39.3 的 `ast.parse` 正控不矛盾：差异在于**谁读文件**——`import` 用自己那套 cookie/BOM 侦测，裸 `open().read()` + `ast.parse` 不会）。
3. **带 BOM 的形态高度集中**：6 个 `docs/superpowers/**` 的文件名**全部**含同一天日期 `2026-07-05`，4 个 `backend/.sdd/*.diff` 自成一簇 ⇒ 各自指向某次**一次性批量产出**（同族：§3 的「生产者驱动探针」—— 想知道会不会再产出，要看那个生成器还在不在；本节**没查**）。⚠️ 这里**只**说「形态上像批量产出」，**没有**断言是谁生成的。

### 53.39.9 #1 次全量唯一红测的判定：**偶发**（四条判据，其中第 4 条是本次新取的）

#1 次的唯一红是 `tests/test_sport_market_stores.py::test_get_latest_snapshot`，断言 `assert 0.6 == 0.65`（`tests/test_sport_market_stores.py:207`）。**它不是本节改动引起的**，四条判据：

| # | 判据 | 读数 |
|---|---|---|
| ① | **零相交**：该测试文件是否引用被改的两个模块 | `grep "llm_gateway\|world_cup_tournament_state"` ⇒ **NO REFERENCE**（改动是「两个无关模块各删 3 字节」，无因果路径） |
| ② | 本批是否新增/改动测试 | **否**（本节只改 2 个 `app/` 文件；全量计数 `6627` 与 §53.38 相同） |
| ③ | **隔离复跑** | `pytest tests/test_sport_market_stores.py` ×5 ⇒ `tests=11 red=0` **5/5 全绿** |
| ④ | **全量再跑一次** | #2 次 `tests=6627 red=0` ⇒ **不可复现** |

⚠️ **一条必须照实说的：这条红测在归档的历次全量里从未出现过。** 我扫了工作台里 112 个历史 junit（`scan_junit_reds.py`，按 `<failure>`/`<error>` 精确解析、不靠文件名 grep），全量档里只出现过这两个红：

```
junit-posture-full.xml, junit-53-38-full.xml  -> test_realtime_ticket_store::test_spawned_workers_share_atomic_consume_once
junit-53-39-full.xml                          -> test_sport_market_stores::test_get_latest_snapshot   (本节 #1，首次)
```

而 `junit-53-37-full.xml` / `junit-full.xml` / `junit-fix-full.xml` / `junit-log-full.xml` / `junit-logguard-full.xml` / `junit-redirect-full.xml` / `junit-probe.xml` 全是 `red=0`。⇒ **不能说「这是既有偶发」**（那是把一个没证据的说法当结论）。能说的是：**同一批测试在三次最近的全量里，唯一红的身份分别是「无 / realtime_ticket_store / sport_market_stores」⇒ 这是「某一条会偶发」的形态，而不是「某一条稳定坏」的形态**，而判据 ①③④ 排除了与本节的因果关系。

**机制未查清（只登记）**：`kernel_db` fixture 是**函数级 + `tmp_path`**（每用例新库），且 `captured_at` 由 `datetime.now(timezone.utc)`（微秒精度）赋值、列是普通 `DateTime` ⇒ **「两个快照拿到同一个 `captured_at` 而 `ORDER BY ... DESC` 无 tiebreaker」这条假设在微秒精度下很难成立**，但我**没有实测否定它**，也没有为「全局 engine 被并发重初始化 ⇒ 两次 append 落到不同 db 文件」这条备选假设取证。⇒ **留给下一轮，别把上面两条当已证结论。** ⚠️ 顺带一条**已证**的观察：`get_latest_snapshot`（`order_by(captured_at.desc())`）与 `get_snapshots`（`asc()`）**都没有 tiebreaker**，这在**任何**时间戳并列的场景下都是不确定序 —— 是否要补 tiebreaker 属**契约取值**，不代决。

### 53.39.10 未决 / 留痕

0. 🔴 **优先项：53.39.6 的 5 个测试文件 / 6 处过期叙述**（含 1 处**测试名**）。这是本节剥 BOM 的直接**副产品**，不修的话下一个人读那些注释会得到假事实。修它要动 **5 个**测试文件（6 处假叙述；第 7 条仍真不必动）、其中 1 处需先 `grep` 函数名引用。**本批未做（用户只点名了剥 BOM）。**
1. **是否提交**由用户定（本节不提交、不推送）。若提交，属 `app/` 代码改动 ⇒ 按 §0 应**单独成提交**、且与文档提交分开。
2. **其余 13 个 BOM 文件**（清单见 53.39.8）**只登记不代决**。要拍板的话，建议的判据顺序是：① `.diff` 那 4 个先排除（它们是**证据**，改动会改证据内容）② 4 个前端/后端**测试文件**次之（可执行、可用同样的正控验证）③ `docs/**` 最后（纯文本，风险最低）。本节**不预设**这个顺序一定对。
3. **`docs/superpowers/**` 的 BOM 是否由某个仍在使用的生成器持续产出** —— 未查（见 53.39.8 第 3 条）。若还在，剥了会复发。
4. **#1 次那条偶发红的机制**（53.39.9）—— 只登记，未查清。附带确认：`MarketSnapshotStore.get_latest_snapshot` / `get_snapshots` 的 `ORDER BY captured_at` **都没有 tiebreaker**。
5. `probe_bom_state.py` / `strip_bom_53_39.py` / `verify_bom_strip_53_39.py` / `bom_positive_control_53_39.py` / `census_bom_repo.py` / `census_bom_claims.py` / `scan_junit_reds.py` / `sha_snapshot.py` 与 `.withbom` 备份都在工作台（`.workbuddy/audit-2026-10-03/`）。备份在剥 BOM **核验完成前**不要删。

**本节追加读数**：见 `append_generic.py` 的 BEFORE/AFTER 打印，验收四条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## 53.40 修 §53.39.6 的 6 处过期叙述（用户点选 A；动测试代码）

本节是 §53.39.6 那张表的直接处置。那节把「剥 BOM 之后由真变假的 6 处叙述」登记成待办，用户点选 A = 明确授权去改。

### 53.40.1 结论先行

**6 处假叙述已全部改掉**（分布在 **5 个**测试文件），**1 处仍真的叙述按纪律原样保留**。改动**只落在注释 / docstring / 一个测试名**上，**没有一行代码或断言被改**。5 个文件**仍是纯 CRLF、无 BOM**。

| # | 文件 | 前字节 → 后字节 | crlf | numstat | 改法 |
|---|---|---|---|---|---|
| 1 | `test_elo_provenance.py` | 25905 → **26057** | 584 → 584 | `5 3` | docstring 重写 |
| 2 | `test_event_store_source_names.py` | 11458 → **11435** | 261 → 261 | `3 3` | 注释重写 |
| 3 | `test_ledger_error_sanitization_guard.py` | 41928 → **41930** | 978 → 978 | **（未跟踪 ⇒ 无 numstat）** | 注释重写 |
| 4 | `test_runtime_stores.py` | 8023 → **8005** | 173 → 173 | `2 2` | docstring 重写 |
| 5 | `test_singleton_reset_census.py` | 12460 → **12716** | 304 → 304 | `12 7` | docstring 重写 **+ 测试改名** |

⚠️ **第 3 条在 `git diff --numstat` 里是看不到的** —— 该文件是**未跟踪**文件（早前批次新建的守卫，跨会话待办里有登记）。⇒ 一个只看 numstat 的人会**多数出/少数出**一个文件，把「6 处」读成「4 个文件 5 处」。**「改了 5 个文件」这个数是从工作树量的，不是从 numstat 抄的。**

### 53.40.2 改法：把「**快照**」换成「**语言性质**」（这是本节唯一需要动脑的地方）

这 6 处的原始形态都是**同一种错误**：把「某个文件此刻带 BOM」这个**快照**写成了「所以我们用 `utf-8-sig`」的**理由**。快照一定会过期（本次剥 BOM 就让它过期了），语言性质不会。

⇒ 新措辞统一改成陈述**语言层面的不变量**：

> a UTF-8 BOM is legal Python source —— Python's import machinery strips it, while `ast.parse` rejects it outright

这句话的价值在于**它对「将来有没有 BOM 文件」免疫**：
- 若某天有人又提交一个带 BOM 的源码文件，这句话**仍然为真且仍然解释了为什么要用 `utf-8-sig`**；
- 若仓里永远不再有 BOM，这句话**也为真**（它说的是语言规则，不是仓内现状）。

⚠️ **同时保留的另一半**：`utf-8-sig` **没有被改成 `utf-8`**。§53.39.6 第 2 条已论证：`utf-8-sig` 是**正确的防御性默认**（有/无 BOM 都能解码），改回 `utf-8` 会**降低**鲁棒性、并把 §53.39.3 那条缺陷重新引入。**本节只改叙述，不改读写方式。**

⚠️ **顺带修掉一处作用域本来就错的措辞**：`test_runtime_stores.py` 那处原文写的是「at least one module **in this tree** carries a BOM」，而它的读取对象是**单个文件** `_CONFIG_PATH`（`app/core/config.py`）—— 即便在剥 BOM 之前，`config.py` 也从没带过 BOM。新措辞不再做任何作用域断言，这个固有的错配也被消掉了（**它不是本次剥 BOM 造成的**，属顺带发现）。

### 53.40.3 改名是本批唯一有**外部影响面**的操作 —— 先量，再改

唯一不是「纯叙述」的改动是**测试改名**：

```
test_scanner_reads_the_file_with_a_utf8_bom
  -> test_the_bom_tolerant_read_reaches_the_gateway_module
```

改名会改变 pytest 节点 ID，所以**改之前先量引用面**（§53.39.6 第 3 条已预告「改名会让任何按名点选它的地方失效，先 grep」）：

| 引用渠道 | 量法 | 读数 |
|---|---|---|
| 全仓文本引用 | ripgrep 旧名（含 `*.py/*.md/*.json/*.txt/*.yml`） | **2 处，全在审计档**（§53.39.6 自己的记录）+ **1 处定义**；**无第三处** |
| 变异 harness `guards` | **模块导出**（不是 grep）：`{g for s in m.SETS for mu in s.mutations for g in mu.guard_files()}` = **35 个 guard 文件** | 旧名、新名**都不在其中** |
| 变异 harness `m.path` | 同上 = **27 个** | 5 个被改文件**都不在其中** |

⇒ **§4.3 的「必跑 `test_mutation_verify.py`」不触发**，且**改名不会打断任何 harness 守卫**。两条都是**算出来的**（`m.SETS` 是唯一真相源），不是 grep 印象——理由同 §53.39.4：grep 常量名会漏掉间接引用。

**新名的选法**：它必须描述**函数体实际断言的东西**。函数体只有一句 `assert "app.services.llm_gateway_service._client_cache" in census` ⇒ 它钉的是**coverage**（扫描器读到了那个文件、没跳过它），**不是**「该文件带 BOM」。所以新名说 "read **reaches the gateway module**"，docstring 里补上「为什么这份 coverage 能证明 `utf-8-sig` 是对的」，并写明**断言的边界**（钉的是 coverage，不是 BOM 的存在 —— 钉存在就是钉快照）。

🔴 **本节对审计档自身的一处「过期标识符」留痕（不改旧文）**：§53.39.6 的表格里**记的就是旧名** `test_scanner_reads_the_file_with_a_utf8_bom`。按交付记录**只追加不改写**的纪律，**没有**回头去编辑 §53.39.6，而是在本节记录「该名已于 §53.40 改为 …」。⇒ **读到 §53.39.6 那一行的人必须知道它记的是「当时的名字」。** 这是本批自己制造的第二例同族现象（第一例是 §53.39.6 对 §53.37.6 的更正），也正说明「前提过期」不是一次性事件而是**持续过程**。

### 53.40.4 「修完了」是**量**出来的：`defects: 0`

只改不验等于没改。`verify_bom_claims_53_40.py` 复扫 `app/`、`tests/`、`scripts/` 里所有**做存在性/数量断言**的措辞（`carries a BOM` / `carry a BOM` / `starts with U+FEFF` / `N files in this (repository|suite|tree)`），并且**逐条给判定**（不是只列出来）：

```
existence/count claims still present: 1
  backend/tests/test_no_assertionless_tests.py:100  [backend/tests] -> OK (scope has 2)
      this suite carry a UTF-8 BOM, and an earlier version of this scan silently

defects: 0
```

- 该文件**故意保留**：它的作用域是 `tests/`，而 `tests/` **确实仍有 2 个** BOM 文件（§53.39.8 的普查 + 本节复扫一致）⇒ 这句话**仍然为真**。
- 探针**不是**把「命中数」当判据 —— 它按**该文件实际扫描的根**去数 BOM 文件（`ROOT_FOR_FILE` 解析 `Path(__file__).resolve().parent` 形式），**作用域里还有 BOM 就判 OK，作用域里为 0 才判 DEFECT**。⇒ 判据是「陈述与作用域是否一致」，不是「有没有提到 BOM」。
- ⚠️ **反过来说**：如果当初为了「清零」把这句话也一起改了，那就是**把一句真话改成假话** —— 这正是「按命中数打地鼠」会犯的错。**判据要跟作用域走。**

### 53.40.5 门与回归

| 检查 | 命令 | 结果 |
|---|---|---|
| 收集（改名后） | `pytest tests/test_singleton_reset_census.py --collect-only` | 新名出现在节点 ID 里，收集正常 |
| 定向（5 个被改文件） | `pytest` 5 个文件 | junit `tests=117 red=0 skipped=0` |
| lint | `ruff check app/`（在 `backend/` 下） | `All checks passed!`（⚠️ 覆盖面见下第 1 条） |
| 类型 | `mypy app/` | `Success: no issues found in 331 source files`（⚠️ 同上） |
| 契约 | `python -m scripts.generate_types --check` | `generated-types.ts is up to date.` |
| 全量 | `pytest tests/` | 见下方 `FULL_RESULT` |

⚠️ **两个「判据不适用」要写明，别当成通过**：
1. 项目 lint 门是 `ruff check app/`，**不含 `tests/`** ⇒ 本批改的 5 个文件**不在 lint 门的覆盖范围内**，「ruff 全过」**不能**被读成「这 5 个文件被 lint 过」。本次另行确认：改动行**没有一行超过 100 字符**（唯一一处 >100 是 `test_ledger_error_sanitization_guard.py:195` 的 `def` 行，**既有**、非本批）。
2. `mypy app/` 同理（改的是 `tests/`）。⇒ 本批的**真正判据**是定向跑 + 全量 + 声称面复扫这三样。

`FULL_RESULT`：`pytest tests/` ⇒ junit **`tests=6627 red=0 skipped=11`**（`6616 passed, 11 skipped, 1097 subtests passed`, 1467.78s），进程 `RAW_EXIT=0`，`safe-delete` 指纹 **0**。

- **计数与 §53.38/§53.39 完全相同（6627）** —— 本批是**改名**不是增删，所以节点数不该变；**这一点本身就是一条交叉验证**（若改名被写成「删一个 + 加一个」，计数会露馅）。
- **`red=0` 全绿** —— 顺带说明：§53.39 那次 #1 全量的偶发红（`test_sport_market_stores::test_get_latest_snapshot`）在本批**没有复现**，与本批改动无关。
- **PRE == POST** sha 对照（`sha-53-40-before.txt`）⇒ 长任务期间**无文件回退**。

### 53.40.6 留痕

1. **`numstat` 覆盖不到未跟踪文件**（53.40.1 已记）—— 改「未跟踪的守卫文件」时，**必须另外报它**，否则改动会静默。
2. **只改叙述、不改读写方式**（53.40.2）—— 下一轮若想「顺手把 `utf-8-sig` 改回 `utf-8`」，先读 §53.39.6 第 2 条。
3. **改名的复核配方**（可复用）：全仓文本 grep + **从 `m.SETS` 导出** `guard_files()`/`path` 两个全集，三者都干净才动手。
4. `test_no_assertionless_tests.py` **未改**（53.40.4）—— 它是真话。
5. 本批**不提交、不推送**。若提交：这是**测试代码**改动，按 §0 应与审计档（文档）**分成两个提交**。

**本节追加读数**：见 `append_generic.py` 的 BEFORE/AFTER 打印，验收四条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## 53.41 剩余 BOM 文件的分档拍板与处置（用户点选 A；动代码 + 动按库记录）

本节承接 §53.39.8 的全仓普查与 §53.39.10 的待办 ①。用户点选 A = 对「剩余那些」做分档并处置。

### 53.41.1 先更正两处**我自己写错的东西**（它们是本节的前提，不先纠正后面全部读歪）

**(1) 🔴「其余 13 个」是错的，应为 15 个。** §53.39 有 **3 处**这么写（`:10058` §53.39.1、`:10194` §53.39.8、`:10224` §53.39.10）。错因很具体：**全仓普查（15）跑在剥 BOM 之后**，我又拿「15 − 2」得 13 —— **对一个已经量到手的读数再做了一次算术**。同族：§0 的「行数只读 `git diff --numstat`，绝不口算/倒推」。

**独立佐证（不用靠回忆错因）**：`:10058` 自己那句就列了构成 —— 「含 `backend/tests/` 2 个、`frontend/**` 2 个、4 个 `.diff`、6 个 `docs/superpowers/**`」= **14**，再加没列出的 `backend/data/.../LICENSE.metadata.json` = **15** ⇒ **这 15 个的枚举里根本没有那 2 个 `app/services/*.py`** ⇒ 「15 已含被剥的 2 个」不成立 ⇒ 减 2 是双重扣减。⚠️ `:10194` 那一句甚至**自相矛盾**：同一句里既写「仓里 **15 个**文件的历史遗留」又写「其余 **13 个**」。

**(2) 🔴 审计档那张「52 个 DAMAGE / 51 个 MIXED」表已经过期**（`:2883-2895`）。那张表把本节要处理的若干个文件列为「**真 DAMAGE**」。现在实测：

```
eol_audit.py --all          ->  DAMAGE 计数 = 0
独立字节计数（1493 tracked） ->  真正的 MIXED = 0
                                 唯一的 "MIXED" = frontend/src/app/favicon.ico
```

`favicon.ico` 正是**那张表自己**（`:2893`）已经标为 🔴 **误报**的二进制文件（`*.ico binary` ⇒ git 从不碰它字节；ICO 内部恰好同时含 `\r\n` 与裸 `\n`）。逐字节复核那张表点名的 5 个「真 DAMAGE」文件，**5/5 现在都是纯 CRLF**（`bare_lf = 0`）。

⇒ **「真实的混合行尾文件合计 51 个」已不成立**（曾是 51，现在 0）。**注入版 `MEMORY.md` §1 的「工作树有 51 个 MIXED 文件」也已过期** —— 但**本节并没有改它**：取证发现磁盘上的 `MEMORY.md` §1 **早已是更正后的文本**（「✅ MIXED 已收口 … 2026-10-03 复检 MIXED = 0（CRLF 1044 / LF 441 / 无换行 7）… 旧记录「51 个 MIXED」已过期」），该文件 mtime = **2026-10-04 23:20**、早于本节。⇒ 本节草稿原写「**已同批更正**」是错的，已改（见 53.41.9 第 2 条）。⚠️ 本节**只**报告「现在量到 0」与「它曾经是 51」；**没有**去查那次 LF→CRLF 规范化具体发生在哪一批（未查，不代决）。

### 53.41.2 分档判据（**两条同时成立才剥**）

为了让「剥不剥」不靠手感，本节用一条**可以逐项对照**的规则：

> **(1) 该文件是「活的输入」，不是「当时的记录」** —— 它会被构建 / 测试 / 工具当作输入消费；不是作为历史交付物被引用的那类东西。
> **(2) 它的 BOM 对**可能存在**的朴素消费者构成 §53.39.3 类缺陷** —— 判据是「文件类型 + 该类型在本仓**确有**裸 `utf-8` 读取先例」，**不必等某个具体消费者现在就炸**。

第 (1) 条是**用户既有约定**的直接应用：`USER.md` 写明「交付文档的事后修正要**追加不要覆盖** —— 他把 `ENGINEERING_DESIGN_*.md` 当当时的交付记录看」。⇒ **设计/评审类文档是「当时的记录」，改它们的字节＝改写记录**，与「只追加不改写」冲突。
第 (2) 条是**代码卫生**：源码/数据是活输入，朴素读法在本仓**有先例**（Python 侧 ~20 个 AST 扫描器；数据侧 `json.load`；前端侧 `readFileSync(..., "utf8")`）。

### 53.41.3 分档表（15 个）

| 档 | 文件 | 处置 | 判据 |
|---|---|---|---|
| **A** | `backend/data/openfootball-2026/LICENSE.metadata.json` | **剥** | 活的**数据**输入；(1)✓ (2)✓（`json.load` **实测炸**） |
| **B** | `backend/tests/test_llm_gateway_service.py`<br>`backend/tests/test_probability_engine_llm_gateway.py` | **剥** | 活的**源码**输入（CI 有 `compileall app tests`）；(1)✓ (2)✓（`ast.parse` **实测炸**） |
| **C** | `frontend/src/components/detail/confidence-breakdown-panel.test.tsx`<br>`frontend/src/components/detail/signal-summary.test.tsx` | **剥** | 活的**源码**输入（CI 有 `vitest`/`tsc`/`eslint`）；(1)✓ (2)✓（本仓有 `readFileSync(...,"utf8")` 先例） |
| **D** | `docs/superpowers/**` × **6** | **不剥** | ❌(1)：**当时的记录**（设计/交付物）；且**无任何工具把它们当输入**（按路径提及它们的全是源码**注释**） |
| **E** | `backend/.sdd/*.diff` × **4** | **不剥** | ❌(1)：**评审补丁包**（交付记录）；且**实测连潜在消费者都没有**（`git apply` 对带 BOM 的补丁**完全等价**，见 53.41.5） |

⇒ **剥 5 / 留 10**。全仓 BOM 文件：**15 → 10**（剩下的 10 个全是 D/E 两档的记录）。**`app/` 与 `tests/` 现在都是 0**。

⚠️ **D/E 两档的「不剥」是我做的判断，不是量出来的结论**（量出来的是「没有消费者会炸」）。理由见 53.41.5，**可以推翻** —— 若用户认为「BOM 一律清掉，记录也一样」，那 10 个文件都是无风险可剥的（字节级删除、行尾不变，本节的方法完全适用）。

### 53.41.4 剥的那 5 个：逐类正控 + 后缀逐字节同一

**正控（同一段代码分别读「带 BOM 备份」与「已剥离文件」）**：

```
(a) json.load(utf-8)      before(.withbom)  JSONDecodeError: Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1
                          after (stripped)  OK
    json.load(utf-8-sig)  both              OK (5 keys)

(b) ast.parse(naive open(utf-8))  before  SyntaxError: invalid non-printable character U+FEFF
                                  after   OK          （两个 backend 测试文件均如此）
```

⇒ **两类缺陷都是真的**（不是推测）：JSON 边是 `json` 模块**明文拒绝** BOM；Python 边是 §53.39.3 那个 `U+FEFF` 的老相识。

**字节级验收（3 条，同 §53.39）**：只删 3 字节 ✅ / `stripped == backup[3:]` **逐字节为真（5/5）** ✅ / `crlf` 与 `bare_lf` 计数不变 ✅。

**行级侧面印证**（`git diff --numstat`）：5 个被剥文件**各 `1 1`**（BOM 拼在第一行之前 ⇒ 只有首行字节变 ⇒ git 按行记账），与 §53.39 的读数同形；解耦的 `test_no_assertionless_tests.py` 是 `4 4`（纯 docstring 重写）。⚠️ **`numstat` 删除数不为 0 在这里是正常的** —— 「删除数 == 0」是**审计档 EOF 纯插入**的验收条款，**不是**代码改动的（同族：§0 与 §53.39.4）。

| 文件 | 前 → 后字节 | crlf | bom | sha16 前 → 后 |
|---|---|---|---|---|
| `LICENSE.metadata.json` | 207 → **204** | 7 → 7 | True → **False** | `e2df31cc…` → `a9c57095…` |
| `test_llm_gateway_service.py` | 26214 → **26211** | 585 → 585 | True → **False** | `f2ba43fd…` → `9791ddcf…` |
| `test_probability_engine_llm_gateway.py` | 5659 → **5656** | 125 → 125 | True → **False** | `9dae334d…` → `ecb39087…` |
| `confidence-breakdown-panel.test.tsx` | 1726 → **1723** | 44 → 44 | True → **False** | `3b47f511…` → `2dcbc5b8…` |
| `signal-summary.test.tsx` | 1159 → **1156** | 32 → 32 | True → **False** | `a818f4d6…` → `f3ba8c3d…` |

**KEEP 档零改动自证**：剥之前先记下 D/E 两档 10 个文件的 sha256，剥完之后逐一比对 ⇒ **`unchanged` 10/10，违规 0**。这条是必要的：否则「我只剥了 5 个」只是一句话。

### 53.41.5 E 档（`.diff`）不剥的理由是**语义**的，不是技术的 —— 这一点必须写明

直觉上会说「`.diff` 带 BOM 会让 `git apply` 失败」。**实测否定**：

```
合成一个真能应用的补丁：
  git apply --check [plain            ] rc=0   1 file changed, 1 insertion(+)
  git apply --check [prefixed with BOM] rc=0   1 file changed, 1 insertion(+)
```

⇒ **`git apply` 对带 BOM 的补丁输出与不带 BOM 的完全一致**。所以 E 档**没有**技术理由需要剥；不剥的理由只有一条：**它们是历史评审补丁包，改字节＝改「当时被评审的是什么」**。

⚠️ 顺带一条**方法论**：本节最早差点**用错理由**——我先发现审计档 `:2888` 把 `final-review-package.diff` 记成「真 DAMAGE」，一度打算拿「有 DAMAGE 在案」当不剥的硬理由。**但那张表已经过期**（53.41.1(2)）。⇒ **拿一条记录当理由之前，先确认那条记录本身还成立**（同族：§4.1「复述结论时必须带上它的前提」）。

### 53.41.6 耦合：剥 B 档会**推翻一句刚被认证为真**的声明

`test_no_assertionless_tests.py:99-101` 原文：

> two files **in this suite** carry a UTF-8 BOM, and an earlier version of this scan silently dropped them on `SyntaxError` …

§53.40.4 刚刚**把它判为「仍真、故意保留」**（因为 `tests/` 恰有 2 个 BOM 文件 —— 就是 B 档的这两个）。**剥掉 B 档，它就立刻变假。**

⇒ **这条耦合必须在同一批里解掉**，做法与 §53.40 一致（把**快照**换成**语言性质**）：

> a UTF-8 BOM is **legal Python source**, and an earlier version of this scan silently dropped **such files** on `SyntaxError` …

改后：**过去时的事实用过去时讲**（"an earlier version … dropped"），**现在时不再数数** ⇒ 无论将来有没有 BOM 文件，它都为真。该文件 `numstat 4 4`（纯 docstring 重写，无代码行）。

📌 **这条耦合本身值得记**：上一批「保留一处真声明」的动作，**给下一批埋了一个必须一起改的点**。⇒ **「保留」不是零成本的选择**，它把一个隐式依赖留在了后面的批次里。

### 53.41.7 取证副产物：`docs/superpowers/**` 的 BOM 是**一次性批次**（跨会话待办 ② 的答案）

原待办是「查那个生成器是否还在用（不查则剥了会复发）」。**不用剥也能查** —— 看分布：

```
docs/superpowers/** 共 122 个文件，带 BOM 的恰好 6 个，全部是 2026-07-05
同一目录 06-28 / 06-29 / 06-30 / 07-01 / 07-02 / 07-03 / 07-04 / 07-08 /
        07-11 / 07-14 / 07-15 / 07-16 … 的文件：全都没有 BOM
```

⇒ 该目录**在此之后一直被继续写入**（最新到 07-16），而**那之后没有任何一个文件带 BOM** ⇒ **形态是一次性批次产出，不是仍在运行的生成器**。**即使将来要剥 D 档，也不会复发。**

⚠️ **仍然只说「形态」**：本节**没有**找到生成这些文件的脚本（仓内按路径提及 `docs/superpowers/**` 的全是源码注释，无写入者），也**没有**断言是谁生成的。

### 53.41.8 门与回归

| 检查 | 命令 | 结果 |
|---|---|---|
| 定向（触及 + 8 个守卫文件） | `pytest` 8 个文件 | junit `tests=161 red=0 skipped=0` |
| 前端测试（剥前 / 剥后） | `npx vitest run` 2 个文件 | **3 passed / 3 passed**（两次一致） |
| 前端类型（剥前 / 剥后） | `npx tsc --noEmit` | `rc=0` / `rc=0` |
| 前端 lint（剥前 / 剥后） | `npx eslint` 2 个文件 | `rc=0` / `rc=0` |
| 后端 lint | `ruff check app/` | 见下 |
| 后端类型 | `mypy app/` | 见下 |
| 契约 | `python -m scripts.generate_types --check` | 见下 |
| 全量 | `pytest tests/` | 见下方 `FULL_RESULT` |

📌 **§4.3 的「必跑」触发器先算过再决定**：本节 diff 触及的 **6 个文件**（5 个被剥 + 1 个解耦）逐个对照 harness 的**派生集合**（`importlib` 载入 `backend/scripts/mutation_verify.py` 后从 `m.SETS[].mutations` 取 `{mu.path}` 与全部 `mu.guard_files()`，**不 grep 常量名**）⇒ **6/6 都是 `in m.path=False` / `in guard_file=False`** ⇒ **`test_mutation_verify.py` 不在必跑列表**（且它本来就被全量覆盖）。

⚠️ **顺带澄清一个「两个数」的坑**（差点写成「记忆过期」）：
```
HEAD   = 8 个 set / 71 个变异 / 27 distinct m.path / 35 guard_file
工作树 = 8 个 set / 72 个变异 / 27 distinct m.path / 35 guard_file
```
多出的那一条是 **§53.38 的 W35**（`mutation_verify.py` 在工作树里是 ` M`，未提交）。⇒ **`MEMORY.md` §4.3 记的「71」是 HEAD 侧的读数，不是过期**；**同一个 harness 在两棵树上可以有两个数 ⇒ 报数时必须说清是哪棵树**（`list`/`numstat` 同理）。

⚠️ **前端的「剥前/剥后一致」要读对**：它证明的是 **BOM 对前端工具链不可见**（`tsc`/`eslint`/`vitest` 都自己处理 BOM）—— 也就是说 C 档**不是靠 CI 变红暴露的**。C 档剥它的理由是 53.41.2 第 (2) 条的**先例存在性**，不是「它现在会炸」。**别把「两次都绿」读成「剥它没用」。**

`FULL_RESULT`：`pytest tests/` = **junit `tests=6627 / red 0 / skipped=11`**；pytest 自述 `6616 passed, 11 skipped, 1097 subtests passed in 1447.93s`；**`safe-delete` 指纹 `grep -c` = 0** ⇒ 这次跑**没被 shim 污染**（`6616 + 11 = 6627` 自洽）。判据只取 junit 的 `failures`/`errors`（均为 0），**不看进程 rc**。

**POST sha 对照（长跑期是否回退）**：全量跑前对 6 个热点文件（5 剥 + 1 解耦）记 sha256，跑完逐一重算 ⇒ **`violations: 0 / 6`（全 `unchanged`）** ⇒ 本次长跑**没有把就地编辑过的文件回退到基线**（同族：跨会话踩过的「长时后台任务期文件被回退」）。

### 53.41.9 留痕

1. **两处更正已写在 53.41.1**（「13 个」→ 15 个；「51 个 MIXED」→ 0）。按只追加不改写的纪律，**没有**回头编辑 §53.39 那 3 行与 `:2883-2895` 那张表；读它们的人必须知道**它们记的是「当时」**。
2. 🔴 **更正一句我自己写错的话**：本节草稿原写「注入版 `MEMORY.md` §1 的『51 个 MIXED 文件』**已同批更正**」—— 取证后发现**磁盘上它早就更正过了**（`MEMORY.md` mtime **2026-10-04 23:20**，文本自称「2026-10-03 复检 MIXED = 0」），本节**一个字节都没动它**；已把 §53.41.1(2) 与本条一起改掉。⚠️ 而**本会话上下文里注入的那份 §1 仍是旧文本**（「📌 工作树有 51 个 MIXED 文件」）⇒ **注入快照落后于文件**。这正是 §4.1 的镜像：**「我读到的」不等于「现在的」** —— 更早的轮次大概就是照着注入文本把它列成了待办，而 **Read 一下磁盘就能发现它早已改好**。⇒ **要断言「某文件现在写着什么」，去 Read 文件，别读注入快照。**
3. **D/E 两档「不剥」是我的判断，可推翻**（53.41.3 末）。
4. **B 档与 `test_no_assertionless_tests.py` 的耦合已解**（53.41.6）；若将来改动那条声明，注意它现在**不再依赖任何文件带 BOM**。
5. **BOM 原件备份**：`.workbuddy/audit-2026-10-03/bom-backup-53-41/*.withbom`（5 个）。**核验完成前不要删。**
6. 本批**不提交、不推送**。若提交：这是**代码（含测试 + 数据 + 前端）**改动，按 §0 应与审计档**分成两个提交**。
7. 📌 **顺手更正了归档件 `MEMORY-archive.md` §1 里那句「普查：工作树 51 个 MIXED 文件」**（改成「旧记录，已过期」+ 现况 0 + 一句「作用域/时点」提醒）。理由：archive 自己的 §0 写了「**滚动记忆可改写**」（与「交付记录只追加不改写」分野），而**留着 51 会让以后 grep 它的人读到假前提**。读数：`130323 → 130952` B、crlf `0 → 0` 保持、bare_lf `842 → 845`（**2 行换成 5 行，增量 +3 与行数一致**）、sha16 `3c15cfb11897c66f → 98b2d187108b1438`。⚠️ 这是本批**唯一**一次「改写滚动记忆」，与「审计档只追加」不冲突。

**本节追加读数**：见 `append_generic.py` 的 BEFORE/AFTER 打印，验收四条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## 53.42 剥掉剩余 10 个 BOM 文件（用户授权，覆盖 §53.41 的「不剥」判断）

本节承接 §53.41。§53.41 把剩余 10 个 BOM 文件判为 **KEEP**，但**明确写了那是判断、可推翻**。用户随后**明确选择了「剥剩余 10 个 BOM」** ⇒ 本节执行该决定。

### 53.42.1 处置反转本身要留痕（§53.41.3 的表格仍写着「不剥」）

- §53.41.3 末原话：*「D/E 两档的『不剥』**是我做的判断，不是量出来的结论**（量出来的是『没有消费者会炸』）……**可以推翻**」*。本节就是那次推翻。
- ⚠️ **按只追加纪律，§53.41.3 的表格与 §53.41.9 第 3 条没有回头编辑** ⇒ **读它们的人必须知道「不剥」已被本节反转**（同族：§53.40.6 的「自指累积」——每次追认都留下新的「当时」）。
- 🔴 **反转的是「取值」，不是「事实」**：判决当时基于的事实（**无消费者**、**字节级无风险**、**`git apply` 对 BOM 等价**）**至今全部成立**。变的只有「**记录类文件允不允许改写**」这一条取值 —— 那是用户的权限，不是可测出来的量。⇒ 这也说明 §53.41 把「判断」与「事实」分开标注是对的：**事实没变，判断被覆盖，一句都不用改测出来的部分。**

### 53.42.2 取证：这 10 个文件**零程序化消费者**

| 检查 | 结果 |
|---|---|
| `.github/**` 有没有碰它们 | **0 命中**（CI 既不 `git apply` 也不读取） |
| `backend/{app,tests,scripts}`、`frontend/src`、`deploy` 里的按路径引用 | 全是**文档互链与源码注释**（`CHANGELOG.md`、`README.md`、`docs/dev/**`、`docs/archive/**`、`frontend/src/lib/generated-types.ts:213` 的注释），**无 read/open** |
| 有没有测试 `git apply` 这些 `.diff` | `grep -E "git apply\|\.sdd"` in `backend/tests` ⇒ **0 文件** |
| 有没有「某个文件带 BOM」的存在性声称会因此变假 | `verify_bom_claims_53_40.py` ⇒ **`existence/count claims: 0` / `defects: 0`** |

⇒ 与 §53.41 的取证一致，唯一的差别是**消费者这一项从来就没能阻止剥离** —— 它只支持「无风险」，不决定「该不该」。

### 53.42.3 剥除：字节级三条 + 全仓归零

**全仓普查**（`--cached --others --exclude-standard`，1500 = 1493 tracked + 7 untracked）：
```
BEFORE: 1500 files scanned, 10 carry a BOM
AFTER : 1500 files scanned,  0 carry a BOM  -> ZERO
```

**逐文件读数**（每文件**恰 −3 字节**；`crlf` 与 `bare_lf` **不变**；写回后断言 `new == old[3:]` **逐字节**成立）：

| 文件 | 前 → 后字节 | crlf | bare_lf | sha16 前 → 后 |
|---|---|---|---|---|
| `backend/.sdd/final-review-package.diff` | 39800 → **39797** | 923 → 923 | 0 → 0 | `f989db62…` → `90f75236…` |
| `backend/.sdd/review-6a13b71..e75af1c.diff` | 17581 → **17578** | 431 → 431 | 0 → 0 | `96a05ebb…` → `412fefa4…` |
| `backend/.sdd/review-e75af1c..f0818f9.diff` | 12498 → **12495** | 284 → 284 | 0 → 0 | `dad2b7e6…` → `b8e8639e…` |
| `backend/.sdd/task-3-review-package.diff` | 14995 → **14992** | 326 → 326 | 0 → 0 | `f967b64d…` → `bc3f3019…` |
| `docs/superpowers/plans/2026-07-05-confidence-breakdown-diagnostics.md` | 4855 → **4852** | 107 → 107 | 0 → 0 | `cc9e0144…` → `a9370481…` |
| `docs/superpowers/plans/2026-07-05-llm-fallback-gateway.md` | 15430 → **15427** | 382 → 382 | 0 → 0 | `938c5050…` → `47068549…` |
| `docs/superpowers/plans/2026-07-05-prediction-engine-safety-evidence-boost.md` | 33813 → **33810** | 820 → 820 | 0 → 0 | `97d9bf2d…` → `fd874da4…` |
| `docs/superpowers/plans/2026-07-05-world-cup-qualification-status.md` | 2814 → **2811** | 58 → 58 | 0 → 0 | `c162d215…` → `621ae58b…` |
| `docs/superpowers/specs/2026-07-05-llm-fallback-gateway-design.md` | 16341 → **16338** | 545 → 545 | 0 → 0 | `a044d9f2…` → `ed663015…` |
| `docs/superpowers/specs/2026-07-05-prediction-engine-safety-evidence-boost-design.md` | 10632 → **10629** | 252 → 252 | 0 → 0 | `1472592a…` → `9a901e28…` |

**行级侧面印证**：`git diff --numstat` 10 个文件**各 `1 1`**（BOM 拼在第一行之前 ⇒ 只有首行字节变），与 §53.39/§53.41 同形。

**写前门控**：脚本先做「声明的 10 个文件必须都真的带 BOM」的前置断言 —— **首跑就靠它拦住一次我自己的错**（普查命令写成 `git ls-files -o` 只列未跟踪、只扫到 7 个 ⇒ `declared-but-not-found: 10` ⇒ **断言失败、一个字节都没写**）。⚠️ 这是「**写入前全校验、未通过不写**」那条纪律第二次真的救命（第一次见 §53.41 的 KEEP 自证）。

### 53.42.4 🔴 **本次没有新的「前提过期」面** —— 与 §53.39 的成分对照

§53.39 剥 2 个文件，**暴露出 7 处**「因为某文件带 BOM，所以我们这么写」的过期叙述。本节剥 **10 个**（是那次的 5 倍），复扫结果：

```
verify_bom_claims_53_40.py  ->  existence/count claims still present: 0
                                defects: 0
```

⇒ **零新增过期面。** 原因是可追溯的：§53.40 把 6 处、§53.41.6 把最后 1 处，**从「快照」改成了「语言性质」**（"a UTF-8 BOM is legal Python source …"）。语言性质**不依赖仓内现状** ⇒ 无论还有没有 BOM 文件，那些句子都为真。

📌 **这条可以当成「修叙述修到位了没有」的验收判据**：
> **再对同一类对象做一次同类操作，若不再产生新的假叙述，说明叙述已经与「现状」解耦。**

⚠️ 反之，若这次又冒出一批假叙述，就说明上一轮只是把命中的地鼠打掉了、没换掉地鼠洞。**本节是「换洞」成功的实证。**

### 53.42.5 顺带发现（**既有**，与剥离无关）：4 个评审包里只有 2 个是**可应用的补丁**

做「剥离后补丁还能不能解析」的正控时（`git apply --numstat` **只解析不应用**，对已漂移的历史补丁也适用），发现 4 个 `.diff` **分两类**：

| 文件 | 剥离后 | 带 BOM 备份 | 判定 |
|---|---|---|---|
| `final-review-package.diff` | `rc=0` → `378 0 backend/scripts/diagnose_event_quality.py` | `rc=0` → **同一行** | **合法补丁** |
| `review-6a13b71..e75af1c.diff` | `rc=0` → `199 0 …` | `rc=0` → **同一行** | **合法补丁** |
| `review-e75af1c..f0818f9.diff` | `rc=128 corrupt patch` | `rc=128 corrupt patch` | **不是可应用的补丁** |
| `task-3-review-package.diff` | `rc=128 corrupt patch` | `rc=128 corrupt patch` | **不是可应用的补丁** |

- **正控结论**：`same rc + error category` ⇒ **4/4**。⇒ 剥除**没有改变**任何一个文件的解析结果（前两个输出逐字相同；后两个两侧同为 `rc=128`）。
  ⚠️ **一个探针陷阱**：第一版正控比的是**原始输出文本**，得到 `2/4`「不一致」—— 因为 git 的报错信息**把文件路径截断后嵌进去了**，两个被比的文件路径必然不同。⇒ **比错误信息要先去掉路径**（比 `rc` + `at ` 之前的错误类别）。**「不一致」有时是探针造成的，不是对象造成的。**
- **机制**（`--recount` 给出线索：`warning: recount: unexpected line: ?`）：hunk 体里存在**丢了前导空格的空行**。`final-review-package.diff` 的 hunk 内**空行 = 0**（空行只出现在前言与 EOF），故可解析；另两个在 hunk 内有 6–12 个空行（如 `:18/:19`、`:162/:168/:170`、`:22/:23`、`:74/:75`）⇒ 那些空行本应是 `" "`（单个空格）却成了 `""`。
- 🔴 **这是既有损伤，不是本节的**：`withbom` 备份**同样** `rc=128`，且剥离是 `old[3:]` 的**逐字节后缀**（已断言）。⇒ 它在 HEAD 就存在。
- ⚠️ **只登记、不修**：修它＝**改写证据文件的内容**（不是删 3 字节），超出「剥 BOM」的授权；且这两个文件是**评审过程的快照**，不是用来 apply 的。⇒ 记为跨会话待办。

### 53.42.6 门与回归

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端 lint | `ruff check app/` | `All checks passed!` |
| 后端类型 | `mypy app/` | `Success: no issues found in 331 source files` |
| 契约 | `python -m scripts.generate_types --check` | `generated-types.ts is up to date.` |
| 全仓行尾 | `eol_audit.py --all` | **`line-ending damage: none`**（1 个文件按二进制跳过） |
| 定向（BOM 措辞相关 8 个守卫文件） | `pytest` 8 文件 | junit `tests=161 red=0 skipped=0` |
| 全量 | `pytest tests/` | 见下方 `FULL_RESULT` |

⚠️ **本次没有改任何 `app/`、`tests/` 或前端源码**（只动了 4 个 `.diff` + 6 个 `.md`）⇒ 前端 `tsc`/`eslint`/`vitest` **不适用**（未跑，也没有理由跑）。**判据只有：三道静态门 + EOL 探针 + 定向 + 全量。**

`FULL_RESULT`：`pytest tests/` = **junit `tests=6627 / red 0 / skipped=11`**；pytest 自述 `6616 passed, 11 skipped, 1097 subtests passed in 1403.21s`；**`safe-delete` 指纹 `grep -c` = 0** ⇒ 这次跑没被 shim 污染（`6616 + 11 = 6627` 自洽）。判据只取 junit 的 `failures`/`errors`（均为 0），**不看进程 rc**。与 §53.41 的全量读数**同值**（`6627 / red 0 / skipped=11`）。

**POST sha**：双向对照 10 个文件 —— **`changed vs 剥前: 10/10`**（剥离确实落在盘上）且 **`violations vs 跑前(剥后)快照: 0/10`**（长跑期**没有回退**）。两个方向都成立才算过。

### 53.42.7 留痕

1. **BOM 原件备份**：`.workbuddy/audit-2026-10-03/bom-backup-53-42/*.withbom`（**10 个**）。**核验完成前不要删。**
2. **§53.41 的「不剥」未回头编辑**，反转记在本节（53.42.1）。
3. **两个非补丁的 `.diff` 只登记不修**（53.42.5）。
4. 本批**不提交、不推送**。若提交：这是**数据/文档**改动（4 个 `.diff` + 6 个 `.md`），按 §0 与审计档**分成两个提交**。

### 53.42.8 本节追加读数（补录）

⚠️ **说明**：本节的读数**没有写进第一次追加的块里**（`§53.42.1–§53.42.7` 先落盘）。按只追加纪律**不回头编辑已落盘的块**，故另起本小节补录。

**审计档自身（本节追加）**：`818422 → 828568`（**字节增量 +10146 == 块长**）、`crlf 10492 → 10597`（**+105 == 块内 CRLF 数**）、`bare_lf stays 0`、`prefix unchanged`、`git diff --numstat 4315 0 → 4420 0`（**删除数 0**）、sha16 `e2e6f87ec7c00892 → 1d43c3dd591ea507`。

**全量**：`tests=6627 / red 0 / skipped=11`；`safe-delete` 指纹（`grep -c safe-delete`）= **0**。⚠️ 与 §53.41 的全量读数**同值**（`6627 / red 0 / skipped=11`）。

**POST sha（双向）**：`changed vs 剥前 = 10/10`（剥离确实在盘上）且 `violations vs 跑前(剥后)快照 = 0/10`（长跑期无回退）。

## 53.43 查 §53.39 #1 次偶发红的机制（市场快照表无 tiebreaker；只登记不动代码）

本节是 §53.39.10 第 4 条（「`#1` 次那条偶发红的机制 —— 只登记，未查清」）的续。**没有改任何生产代码**：结论只到「形状已证、触发未查清」，是否补 tiebreaker 属**契约取值**，不代决。

### 53.43.1 结论先行（三句话）

| # | 结论 | 依据 |
|---|---|---|
| 1 | **机制的形状已证**：这条红**就是**「两行 `captured_at` 并列」那一类 —— 并列时 `get_latest_snapshot` **确定性**返回**先插入**的那一行，于是先插入的 `0.6` 被当成最新 ⇒ 现场断言 `assert 0.6 == 0.65` | 53.43.3（F3 20/20 + 端到端正控） |
| 2 | **触发条件未查清**（只登记）：正常路径下并列概率 ≈ 1e-3/对，**支撑不起**「约六分之一的全量跑红一次」；五条备选机制已逐条取证排除 | 53.43.4 |
| 3 | **「无 tiebreaker」是事实，而且这次不再是「附带确认」** —— 它就是这条红的**唯一形状** | 53.43.2 + 53.43.3 |

⚠️ **对 §53.39.10 第 4 条的更正（§0 纪律）**：那一节把「那条红」与「两条查询都没有 tiebreaker」并列成**两条独立事实**（原文措辞：*「附带确认」*）。本次把两者**接起来**：无 tiebreaker **就是**红的形状。同时保留那一节说对的一半 —— 「两个快照拿到同一个 `captured_at`」在微秒精度下**概率上**确实很难成立（本轮实测：400 次 0 并列，见 53.43.4 ①）。**难成立的是触发，不是形状。**

### 53.43.2 先读断言原文（不只看测试名）

`junit-53-39-full.xml` 里的失败元素（XML 解析，非 grep）：

- `class=tests.test_sport_market_stores`、`name=test_get_latest_snapshot`、`time=0.302`
- `message="assert 0.6 == 0.65"`，落点 `tests\test_sport_market_stores.py:207`

`assert latest is not None`（:206）排在它前面且**没有报错** ⇒ 这一条红的语义是**读到了一行，且那行的 `implied_prob` 是 0.6**。

🔑 **断言原文是机制的判别式**：它把机制空间**劈掉一半** —— 任何「读到了空结果」的解释（回落到空库、空表、被清理的 tmp 目录）都会以 `assert None is not None` 收场，**不是** `assert 0.6 == 0.65`。所以只剩两类可选项：**(a) 0.65 那行在被读的库里不存在；(b) 0.65 那行存在但排在 0.6 之后。**

### 53.43.3 正控：并列 ⇒ 恰好这条断言（形状已证）

**(1) 派生事实**（`null_and_tie_ordering_53_43.py`，输出留档 `null_and_tie_ordering_53_43.txt`）：

| 插入顺序 | `get_latest_snapshot` 返回 | 试次 |
|---|---|---|
| `0.6` → `0.65`（同一 `captured_at`） | **0.6** | **20/20** |
| `0.65` → `0.6`（同一 `captured_at`） | **0.65** | **20/20** |

⇒ 并列**不是「任意挑一行」**，而是**先插入者胜**（两次都返回 `id` 更小的那行）。**这是一条确定性的静默错答**：它把更旧的行报成「最新」（`latest` 的语义是"最近一次采样"）。

**(2) 端到端正控**（临时用例 `tests/test_zz_control_tie_53_43.py`，读数取完后**已删**，副本在 `.workbuddy/audit-2026-10-03/`）：

- 把 `market_snapshot_store._utcnow` 冻结成一个常量 ⇒ 跑**真测试体**（真 `kernel_db` fixture、真 `MarketSnapshotStore`）：
  `s1.id=1 s2.id=2 captured_at=datetime(2026, 10, 7, 12, 0, 0, 654321) -> latest.implied_prob=0.6`，且断言 `latest["id"] == s1["id"]` 成立。
- **负控**（不冻结、同体）⇒ 通过，实测两值间隙 `gap_us=1999.0`。

⇒ **充分性已证：并列 ⇒ 恰好这条红（连断言文本都一致）。** 必要性来自 53.43.2（读到了一行）+ 53.43.4（把「行不存在」的路全部排除）。

### 53.43.4 触发条件：五条备选逐条取证排除

| # | 机制 | 判据 / 探针 | 读数 | 结论 |
|---|---|---|---|---|
| ① | 正常路径下时间戳**并列** | 仪器化探针：在真 conftest + autouse 单例重置下跑真测试体 **400 次** | 并列 **0** / 倒流 **0** / 失败 **0**；两值间隙 min **1518µs**、median **2239µs**、max **8001µs** | **量级不符**：间隙**不是 1000µs 的整数倍** ⇒ 时钟量子 ≤1µs ⇒ P(并列)≈1e-3，解释不了 ~1/6 的红 |
| ② | 读/写**回落到 `KERNEL_DB_FILE`**（会话级共享库） | **322 个保留沙箱**的 `kernel_predictions.db` 普查（`sandbox_snapshot_census_53_43.py`） | `kernel_market_snapshots` **322/322 = 0 行** | **排除**：那条路只能读到 `None`，现场断言会变成 `assert None is not None` |
| ③ | 用例**中途**全局引擎被重置 | 同 ① 的探针逐次记录 `id(_engine)` + `engine.url` | 400 次迭代**迭代内引擎身份变化 0 次**；每次都是自己那一个 `tmp_path/kernel_test.db` | **排除** |
| ④ | 时钟被冻结/篡改（freezegun / time-machine 之类） | 98 个发行包按 `freez\|time\|fake\|clock\|mock` 筛；全仓 grep `freeze_time\|freezegun\|time_machine\|setattr(time\|patch("datetime` | **0 命中**（`elo_time_machine` 是**领域**模块——回测时间推进，不是时钟补丁） | **排除** |
| ⑤ | 泄漏的真调度器线程在别处写快照 | 全仓 `start_scheduler()` 调用点 | 唯一调用者 `tests/test_drift_alert_job.py:39,57` 把 `scheduler.scheduler` 打成 `MagicMock`（`SCHEDULER_LOCK_ENABLED=False`）⇒ **不存在 APScheduler 线程** | **排除** |

②的取证细节（**最有力的一条**）：conftest 明写沙箱目录**故意不清理**（它同时是证据仓库），所以**失败那一次的会话级库还在盘上**。用清单里的 `created_utc`/`pid` 定位：`pmrf_test_data_95053fwv` = `created_utc 2026-10-06T13:37:14Z`、`pid 14752`，与 `junit-53-39-full.xml`（mtime `22:01:28 +0800`）相减得 **24m14s**，与同批全量的运行时长（§53.41/§53.42 实测 24m07s / 23m23s）一致 ⇒ 就是那一次。它的 `kernel_market_snapshots`：**0 行**。全量普查：**322 个沙箱，0 个有快照行**。

### 53.43.5 两条新取的派生事实（排序契约本身）

**(a) 可空性不对称**（`PRAGMA table_info`，派生读数）：

```
kernel_market_snapshots   captured_at  DATETIME  notnull=0
kernel_futures_snapshots  captured_at  DATETIME  notnull=1
```

两张表由不同的人写成，对同一语义的列给了不同的约束。

**(b) NULL 行有「双重不可见性」**：数据里同时有「`captured_at` = NULL」和「有值」两行时 ——

- `get_snapshots`（`asc()`）把 NULL 排**第一** ⇒ 它会成为图表的**首点**；
- `get_latest_snapshot`（`desc()`）**永远返回有值那行**（SQLite 的 NULL 最小，DESC 时排最后）⇒ 它**永远当不了「最新」**。

两处都**不出声**。⇒ 「NULL 时间戳」不是"缺失"，而是**一个能进序列、不能进最新**的幽灵行。

**(c) 存量对比（同类问题的另一张表怎么说）**：`KernelSportEdge` 的 docstring 把 `captured_at` **写进了行键**（*"One row per (match_id, mapped_outcome, captured_at)"*）⇒ 那张表的并列是**设计**；而 `KernelMarketSnapshot` 的 docstring 只有一句 `Append-only snapshot store.` / `Price time-series for a sport market link (append-only).`，对并列**什么都没说**。

**(d) 并列在兄弟链路是构造性可达的**：`app/kernel/futures_market_service.py:137` 把 `now = datetime.now(timezone.utc)` 算在**循环外**，内层每个 link 复用同一个 `captured_at`；而市场表在 `app/` 里的唯一 `append_snapshot` 调用点（`app/core/scheduler.py:1247`）是在内层**每 append 现取**一个 `captured_at`（:1246）。⇒ 「并列」在这个仓里**不是理论好奇**：同族链路里它是**按代码写法定会发生的**。

**(e) 🔴 `get_latest_snapshot` 是**承重**的，不是只给测试用**：`app/` 里写入市场快照表只有 **1 处**（`scheduler.py:1247`），但读它有 **3 个生产消费者** —— `api/routes/sport_markets.py:80`（`/sport-markets/...` 的 `latest` 字段）、`kernel/edge_detector_service.py:143`（**边值检测**取市场侧价格）、`kernel/market_liquidity.py:145`（**流动性**判定）。⇒ 53.43.5(b)(c) 那种「静默错答」不是只污染一个测试断言，它会**沿这三个消费者继续往下走**（边值 = 模型概率 − 市场概率）。**这条只作严重度登记，不代决是否修。**

### 53.43.6 门与回归

- **§4.3 触发器（按判据，不凭印象）**：`import` 载入 harness 后派生 —— `sets=8`、`mutations=**72**`、`{m.path}=27`、`{guard_files}=35`；再取 `git status` 的改动清单与之求交 ⇒ 命中 `backend/scripts/mutation_verify.py`（**§53.38 未提交的改动**使它为脏，不是本节改的）⇒ 按「diff 触及任何 `m.path`/guard_file 就必跑」**跑了** `tests/test_mutation_verify.py`：junit **`tests=163 failures=0 errors=0 skipped=0`**（判据取 junit 的 `failures`/`errors`，**不看进程 rc**）。
- **本节在仓库内的改动只有一个文件**：本审计档（本节追加）。
- **临时产物跑完即删**：`backend/tests/test_zz_probe_snapshot_latest_53_43.py`（400 次仪器化探针）与 `backend/tests/test_zz_control_tie_53_43.py`（正控）—— 删后 `git status` 未跟踪数回到 **7**（与本节开始前同值）。副本/读数留在工作台。

### 53.43.7 探针副作用（照实说）

为直查会话级库，我用 `sqlite3` 的 `mode=ro` 打开沙箱里的 WAL 库；**只读打开仍会落下 `-shm`/`-wal` 侧文件**，把受影响的沙箱目录 mtime 顶到 `2026-10-07 10:03`。两条推论：

1. 这些是 conftest **声明为故意保留**的测试草稿（不是生产数据；生产 `v2_loop.db` 全程只读、未打开）；
2. 🔴 **用目录 mtime 做时间定位会失真** —— 本次是靠清单里的 `created_utc` 才把「失败那次」对上（见 53.43.4 ②）。下次做同款定位，**直接读清单**。

### 53.43.8 未决 / 留痕

0. 🔴 **是否给两条查询补 tiebreaker**（形如 `order_by(captured_at.desc(), id.desc())` / `asc(), id.asc()`）—— **契约取值，不代决**。本轮把它从"该不该"推到"值不值得"，实测基础三条：① 失效形态是**静默 + 确定性**（先插入者被报成最新），不是"偶发随机"；② 正常路径复现概率仅 ~1e-3/对，但**并列在兄弟链路是构造性可得的**；③ 补 `id` 次序会把并列解析到**后插入**那行 = 符合"最新"的直觉，**但会改变并列时的返回值** ⇒ 属**取值变更**，不是修 bug。
1. **触发条件未查清**（只登记）。形状已证（53.43.3）、触发未见。🔴 留给下一个人一句话：**别再把「这条红」和「无 tiebreaker」当两条独立事实 —— 它们是同一个。**
2. **另一次全量红的身份**是 `tests/test_realtime_ticket_store.py::test_spawned_workers_share_atomic_consume_once`（**线程**类）。本节未查；若要查，注意它在归档历次全量里也出现过（`junit-posture-full.xml` / `junit-53-38-full.xml`）。
3. 本节**不提交、不推送**。记忆落在 `.workbuddy/memory/2026-10-07.md` 与 `MEMORY-archive.md` §S；方法落在 `audit-delivery-verification` skill `### 9o.`。

**本节追加读数**：见 `append_generic.py` 的 BEFORE/AFTER 打印，验收四条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## 53.44 给 `MarketSnapshotStore` 两条读法补 tiebreaker（用户点选「补 tiebreaker」；动代码）

本节是 §53.43.8 第 0 条的处置：用户明确点选 **A = 补 tiebreaker**。§0 的「契约取值不代决」在此**由用户解除** —— 本节做的是一次**取值变更**，不是修 bug（§53.43 已把「形状已证、触发未见」写清）。

### 53.44.1 结论先行

| # | 事项 | 结论 |
|---|---|---|
| 1 | 改了什么 | `get_latest_snapshot` 的 `ORDER BY` 加 `id DESC`；`get_snapshots` 的加 `id ASC`（各自 docstring 写明「为什么它不是装饰」） |
| 2 | 取值语义 | 并列时的返回值**变了**：从「先插入者胜（= 把更旧的那次采样报成最新）」改为「后写入者胜（= latest 就是字面意思）」。**这正是取值得以变更的部分** |
| 3 | 行为钉 | 新增 **2** 条用例（一条盖 `latest`、一条盖序列），并**各自**挂一条变异 —— 两条变异都过四阶段 |
| 4 | 🔴 **本次修复不解释 §53.43 的触发** | 它消除的是**可观测后果**：并列再发生也不会给出错答案。**触发为什么发生仍未查清**（§53.43.8 第 1 条不因本次改动而作废） |

### 53.44.2 改动（字节级）

`backend/app/kernel/market_snapshot_store.py`（**纯 CRLF**，本批开工前 = HEAD blob 5930 B + 184 CR，即干净的 CRLF 检出态）：

```
@@ get_snapshots
-                .order_by(KernelMarketSnapshot.captured_at.asc())
+                .order_by(
+                    KernelMarketSnapshot.captured_at.asc(),
+                    KernelMarketSnapshot.id.asc(),
+                )
@@ get_latest_snapshot
-                .order_by(KernelMarketSnapshot.captured_at.desc())
+                .order_by(
+                    KernelMarketSnapshot.captured_at.desc(),
+                    KernelMarketSnapshot.id.desc(),
+                )
```

读数：`6114 → 7022` B、`crlf 184 → 204`（**增量 == 净增行数 20** ⇒ 新增行全为 CRLF）、`bare_lf` 恒 0、sha16 `03e905b37be0a078 → fbf6f5df6ba0d643`；`git diff --numstat` = **24 / 4**（4 条删除 = 2 条 docstring 行 + 2 条 `order_by` 行的真实替换；本节的数据改动不是「只加注释」，所以 §0 那 4 条注释证据不适用）。

两处 docstring 都写明这不是装饰，其中一条把**旧行为**写进去（「the tiebreaker-less form returned the *first*-inserted row, so a tie silently reported the *older* capture as the newest」）—— 读者不需要先读 §53.43 才知道为什么有 `id`。

### 53.44.3 行为钉：为什么必须是**两条**用例

| 用例（`tests/test_sport_market_stores.py`，**纯 LF**） | 盖的读法 | 构造 |
|---|---|---|
| `test_get_latest_snapshot_tie_returns_the_later_row` | `get_latest_snapshot`（标量） | 两次 `append_snapshot(captured_at=same)`，断言 `latest["id"] == second["id"]` 且 `implied_prob == 0.65` |
| `test_get_snapshots_tie_keeps_insertion_order` | `get_snapshots`（折线序列） | 三次同一 `captured_at`，断言返回行序 == 写入行序，且其值序 `[0.6, 0.62, 0.65]` |

两条用例都先**自证并列真的成立**（`assert first["captured_at"] == second["captured_at"]` / `assert len({w["captured_at"] …}) == 1`），否则「并列用例」可能在两个不同时间戳上静静通过 —— 那是空头。

🔴 **两个读法喂的是两个不同消费者**（标量 vs 折线序列，见 §53.43.5(e)），所以**没有一条守卫能同时盖住两个方向的 tiebreaker** ⇒ 故意不合并成一条「两个 ORDER BY 都改」的变异，否则一条守卫变红时分不清是哪一处坏了。

文件读数：`8015 → 11312` B、`crlf` 恒 0（纯 LF 保持不变）、`bare_lf 213 → 284`、sha16 `d3bdeaddc75622dc → 04cc97d0be3446d7`、`numstat 71 / 0`（**插入型 ⇒ 删除数 0**）。

### 53.44.4 变异验证：新 set `market-snapshot-order`（S1/S2）

按 §4.2「先补用例、再把空头变成可登记的变异」：两条新用例各自登记一条变异，打的是**定义行**而不是调用行。

| # | 变异 | `old` → `new` | 守卫 |
|---|---|---|---|
| S1 | `get_latest_snapshot` 的 id 次序反向 | `…id.desc(),` → `…id.asc(),` | `test_get_latest_snapshot_tie_returns_the_later_row` |
| S2 | `get_snapshots` 的 id 次序反向 | `…id.asc(),` → `…id.desc(),` | `test_get_snapshots_tie_keeps_insertion_order` |

**四阶段读数**（`verify market-snapshot-order`）：

```
[OK] 1. S1  green before True | red after mutation 1/1 files red | green after restore True | bytes restored True
        mutated run -> tests/test_sport_market_stores.py -> 1 failed, 12 deselected
[OK] 2. S2  green before True | red after mutation 1/1 files red | green after restore True | bytes restored True
        mutated run -> tests/test_sport_market_stores.py -> 1 failed, 12 deselected
```

`1 failed, 12 deselected` ⇒ `-k` 只选中它自己那条守卫（该文件共 13 条用例 = 原 11 + 本节 2）⇒ **两条用例各自承重，不是互为正控**。还原后 store 文件 sha16 回到 `fbf6f5df6ba0d643`（逐字节一致）。

**针的唯一性（改针前的必做项）**：`read_bytes().count()` 实测四个候选针（含缩进的 `…id.desc(),` / `…id.asc(),`，以及不带缩进的两版）**各命中 1 次**；目标文件 `bare_lf == 0` ⇒ 单行针成立。

**新 set 的连带改动（§4.3 规定）**：`tests/test_mutation_verify.py` 的**显式有序清单**加一行 `"market-snapshot-order"`（`numstat 1 / 0`）。改完 `list` 读数：**共 9 套 / 74 个变异**（HEAD 侧仍为 8 套 / 71 条 —— **两棵树两个数**，见 §53.41.3 的纪律）。守卫测试本身：junit **`tests=166 failures=0 errors=0`**（由 163 升到 166 是新 set 带来的参数化用例）。

### 53.44.5 门

- **`ruff check app/`（§0 定义的门）**：**All checks passed!**
- `ruff` 对四个改动文件报 3 条（`F401` @8、`F841` @80、`E741` @111），**全部是既有**：把 HEAD blob 拷进 `backend/tests/` 在**同一套配置**下重跑，得到**同样三条**（行号整体平移，因为本节在文件头加了 1 行 import）⇒ **本节新增 0 条**。🔴 这条判据必须**派生**：在 `backend/` 之外跑 ruff 会落到另一套配置（本次在 `.workbuddy/` 下跑出了 3 条 `I001`，是**配置差异**不是代码差异）。
- **定向回归**（`test_sport_market_stores` + 三个消费者的用例 `test_sport_market_routes` / `test_edge_detector_service` / `test_market_liquidity` / `test_market_liquidity_degraded`）：junit **`tests=86 failures=0 errors=0 skipped=0`**；日志里 `safe-delete` 出现 **0** 次（⇒ 这次进程 rc 可信）。
- 🔴 **一条方法论坑（本轮踩到并派生澄清）**：`market_snapshot_store.py` 的 `git show HEAD:` 长度是 5930 B，开工前工作树是 6114 B，**两者不等** —— 但那个差 **184 恰好是 CRLF 数**，即「HEAD 存 LF、检出 CRLF」的正常态（§1）。⇒ **"HEAD blob 长度 != 工作树长度" 不是"文件脏"的判据**；同理 `test_mutation_verify.py`（35040 vs 35797，差 757 == 其 CRLF 数）也是**干净**的。真判据是 `git status`（它先归一化）。本节开工前**真正脏的只有 `mutation_verify.py`**（102279 B ≠ 97786 + 1966）。
- ⇒ 因此 `mutation_verify.py` 的 `git diff --numstat` **83 / 1 是对 HEAD 的**，含 §53.38 未提交的 W35；**本节对该文件的增量** = 开工前 `102279 → 105619` B（**+3340，纯插入**，`key="market-snapshot-order"` 命中 1 次、`# §53.44` 命中 1 次）。**别把 83 行记成本节改的。**

### 53.44.6 全量回归

`pytest tests -q --junitxml=junit-53-44-full.xml`（后台）：**读数待落** —— 完成后补录在 §53.44.9。判据只看 junit 的 `failures` / `errors`，不看进程 rc（本机 `safe-delete` shim 会污染 rc）。

### 53.44.7 未决 / 留痕

0. 🔴 **触发仍未查清**（承 §53.43.8 第 1 条，**不作废**）：本节消除的是**可观测后果** —— 并列再发生也不再给出错答案，`test_get_latest_snapshot` 在并列下会通过。但「那一次为什么并列了」**依然没有答案**，别把「红不再出现」读成「原因已找到」。
1. **B 项仍未做**：`app/kernel/futures_market_service.py:137` 把 `now` 算在**循环外**、内层每个 link 复用 ⇒ 那是**构造性并列源**（兄弟表 `kernel_futures_snapshots`）。要不要顺手改成内层现取，属取值，**待拍板**。
2. **C 项仍未做**：`kernel_market_snapshots.captured_at` 可空（notnull=0）而兄弟表 notnull=1。**补了 tiebreaker 之后 NULL 行的性质不变**：NULL 在 ASC 里仍排第一（进图表）、在 DESC 里仍永远当不了最新 ⇒ 这一条与本次改动**正交**，不要以为一起修掉了。
3. 本节**不提交、不推送**。按 §0，代码改动（4 个文件）与文档改动（本审计档）应**分成两次提交**；`mutation_verify.py` 里 §53.38 的 W35 与本节新增共处一个未提交改动，若要提交需先想清是否一起提交。

**本节追加读数**：见 `append_generic.py` 的 BEFORE/AFTER 打印，验收四条同时成立：字节增量 == 块长、CRLF 增量 == 块内 CRLF 数、`bare_lf stays 0`、`prefix unchanged`。

## §53.45 (B) 处置与一条被推翻的结论：futures 的循环外 `now` 不是并列源

> 追加于 §53.44 之后。**本节更正 §53.43.4(d) 与 §53.44.7 第 2 条** —— 两处都称 `futures_market_service` 把 `now` 算在循环外是「构造性并列源」。审计档不改写，故以新节更正。

### 53.45.1 结论先行

**(B) 的处置 = 不改。** `app/kernel/futures_market_service.py:136` 的 `now` 算在 `for link in verified`（:137）**之外**，它让同一次 capture 的**不同 link**共享一个时间戳；而 `kernel_futures_snapshots` 的读法 `FuturesLinkStore.get_latest_snapshots` 是 `max(captured_at)` **GROUP BY link_id** 再 JOIN 回表 —— **per-link** 取最大。跨 link 共享时间戳在这套读法下**不产生任何错答**。改它属于 churn，**不是**取值代决。

**§53.43.4(d) 的定义用错了**：它把「同一时刻写多行」当成并列。并列的定义是**同一 link 的两行** `captured_at` 相等；**跨 link 共享时间戳 ≠ 并列**。

### 53.45.2 取证（临时探针，读数取完即删）

`backend/tests/test_zz_probe_futures_53_45.py`，三测点全部走真实 `FuturesLinkStore` / `FuturesMarketService`（同一 temp kernel DB）：

| 测点 | 场景 | 读数 | 含义 |
|---|---|---|---|
| A | 2 个 link、各 1 行、**同一** `captured_at` | 返回 **2 行**，`per_link_counts={"1":1,"2":1}` | 跨 link 共享时间戳**无害**，每 link 恰好 1 行 |
| B | **同一个 link**、2 行同一 `captured_at` | 返回 **4 行**，`link_ids=[1,2,3,3]` | 真并列时 JOIN **把两行都吐出来** ⇒ 该 link 在结果里**重复出现** |
| C | 真实 `capture_snapshots()` 跑两次（mock 掉 fetch） | 两行 `captured_at` = `…13.837612` / `…13.877991`，`distinct=2` | 两次 capture 的时间戳**不同** ⇒ 生产路径不产生同一 link 并列 |

⇒ 两条派生事实：① **形状不同** —— futures 侧真并列的后果是**返回重复行**（同一 link 出现两次，并让 `multi_leg_integrity` 重复计数），与市场表 `LIMIT 1` 的**静默选错**不是同一种失效；② **触发同样未见** —— 唯一写点每 link 每 capture 一行，两次 capture 的 `now` 实测不同。

### 53.45.3 (B) 为什么不改

- **不是可观测缺陷**：跨 link 共享时间戳在 per-link MAX 语义下无后果（测点 A）。
- **改它也不影响真并列**：真并列是同一 link 的两行相撞，取决于两次 capture 的时刻，与 `now` 提不提循环外无关（测点 C 已证两次 `now` 不同）。

### 53.45.4 顺带更正的第二处（代码内注释）

`backend/tests/test_sport_market_stores.py` 的 `test_get_latest_snapshot_tie_returns_the_later_row` docstring 曾把 `futures_market_service.capture_snapshots` 当作「按代码写法就造出并列」的例子 —— 同上，**该举例不成立**；已改写为准确表述（保留「并列是每个调用点都能造出的形状」这一正确部分，并点明 futures 侧因 per-link MAX 而无害）。

### 53.45.5 一处随之过期的自述

§53.44.7 第 3 条写「本节**不提交、不推送**」。该节已于 `251271a` 落库（三提交之一），**这句话随之过期**；按 §4.1，复述它时必须带上前提。§53.43 / §53.44 的其它结论不受影响。

### 53.45.6 未决（更新）

- **(C)** 仍未做：`kernel_market_snapshots.captured_at` 可空（notnull=0）vs 兄弟表 notnull=1。
- **(D)** 仍未做：另一次全量红（`realtime_ticket_store` 线程类）。
- **(新)** futures 侧「同一 link 两行同 `captured_at`」⇒ `get_latest_snapshots` 返回**重复行**（测点 B）。**生产不可达**，只登记形状，**不加 tiebreaker**。
- 本节**未提交、未推送**。

## §53.46 (D) 处置：那次偶发红的机制是「两个进程同时创建 WAL sidecar 的竞态」

> 承接 §53.44.7 第 2 条与 §53.36.4 的登记。**本节不解释「全量为什么比单跑更常红」的全部环境因素**，但把机制从「AV 的经典瞬时态（当时的猜测）」推进到**可复现、可判别、并被一条改动消除**。

### 53.46.1 结论先行

| 问题 | 答案 | 证据 |
|---|---|---|
| 能不能复现 | **能**：单跑 6.7% / 换目录 5% | 实验 A / B |
| 与 `%TEMP%` 有关吗 | **无关** | B：移出 `%TEMP%` 后仍 3/60 |
| 与**并发**有关吗 | **是** | C 串行 0/60；**D 强制同步并发 6/20 = 30%** |
| 机制是什么 | **两进程同时创建 `-wal`/`-shm` 的竞态** | 正控 + D + F |
| 能消除吗 | **能**：spawn 前保持一个打开的连接（预热 sidecar）⇒ **F 0/20** | D vs F，Fisher 单侧 **p≈0.010** |
| 被测不变量被证伪了吗 | **没有** | 异常来自 SQLite/OS 层，`True×1 / False×1` 从未被证伪（与 §51.11 同判） |

**D vs F 是本节的关键对照**：唯一差异是 spawn 之前 `-wal` / `-shm` 是否**已存在**。

### 53.46.2 实验矩阵

| # | 唯一改动 | 样本 | 红 | 判读 |
|---|---|---|---|---|
| A | 原样（`Pool(2)`，`tmp_path` 在 `%TEMP%`） | 30 | **2**（[14][15] **连续**） | 基线复现；"连续"指向**时间窗口** |
| B | 目录**移出** `%TEMP%`（仓库内） | 60 | **3** | ⇒ 否掉「`%TEMP%` 特有」 |
| C | `Pool(1)`（**串行**） | 60 | **0** | 方向一致，但与 B **不显著**（p≈0.24） |
| E | 预热 sidecar（**无**栅栏） | 60 | **0** | 同上，不显著 |
| **D** | `Barrier(2)` **强制同步** | 20 | **6（30%）** | ⇒ 竞态被放大 6 倍 |
| **F** | D + 预热 sidecar | 20 | **0** | ⇒ **消除**（对 D，p≈0.010） |
| 正控 | 手工把 db（+sidecar）设只读 | 1 | 1（**逐字复现**） | 见 53.46.3 |

⚠️ **诚实边界**：C / E 的 `0/60` **单独**证明不了什么（Fisher p≈0.24，功效不够）。它们只与 D / F **方向一致**。**本节结论立在 D/F 上**（30% vs 0%，p≈0.010）。

### 53.46.3 正控：形状已证（逐字）

`.workbuddy/audit-2026-10-03/probe53_46.py`（读数取完即删；**脚本第一行就把 `LOOP_DB_FILE` 指向 temp，绝不动生产库**）：

```
TREATMENT(db read-only) -> OperationalError: attempt to write a readonly database
  File ".../backend/app/realtime/ws_auth.py", line 142, in redeem
```

与现场（§51.11 / §53.36.4）**异常类型、文本、落点三者逐字一致**。

🔑 **一条关键派生事实**：`issue()` 返回后 `-wal` / `-shm` **都不存在**（探针打印 `exists ... -wal False`）——父进程 `writing()` 在 `close()` 时 checkpoint 并**删除了** sidecar。⇒ **每个 spawn worker 都必须自己创建 sidecar**，两个 worker 同时做这件事就是 D 里那 30%。

这同时解释了**为什么 `_ensure_schema` 不报错而 `:142` 报错**：`CREATE TABLE IF NOT EXISTS`（表已存在）是 **no-op**，不写；第一个 `DELETE` 才需要写。

### 53.46.4 边界与未决

1. **修法的形态（改测试）**：在 `issue()` 与 `Pool` 之间保持一个打开的连接（即 E / F 的做法）。它**不改变被测语义** —— 两个 worker 仍是独立进程争抢那一次 `DELETE`。**是否采纳属拍板**，本节只给读数。
2. **生产侧同族风险（只登记，未动产品代码）**：多个 API worker **首次**同时写一个**尚无 sidecar** 的库会撞同一竞态。生产里 `LOOP_DB_FILE` 通常早已建好 sidecar ⇒ 窗口极小；**冷启动**时理论上可达。⚠️ 这一条是**由本节的实测形状外推**的，**没有**在生产路径上复现。
3. ⚠️ **全量红率远高于单跑**（历次观测 2/3 vs 本节 5%）：说明红率**依赖环境负载**（全量时前面七千个用例的临时文件与 AV 活动）。**本节不解释这一部分**。
4. **一条 harness 教训**：第一版复现脚本解析 pytest 的 **stdout**，30 次里有 **22 次输出为空**，统计会读成「8 passed / 0 failed」——即「全绿」。**空输出不是通过**；判据只能用 junit（同族：§4.2 的空头判据）。
5. 本节**未提交、未推送**；5 个临时探针跑完已删，工作树未跟踪回到 **0**。

### 53.46.5 拍板（用户点选，2026-10-08）

- **(D-修法) 不采纳**：不给该用例加「预热 sidecar」。口径：它是**环境态**、红率**受负载影响**，且被测不变量**未被证伪** ⇒ 不为一条不改变语义的环境噪声改守卫。§53.46.4 第 1 条的「是否采纳属拍板」**以此为准**（结论：**不改测试**）。
- **本节提交**：审计档改动单独成一个**文档提交**（`docs(audit): …`）；**不推送**。
- **更正 §53.46.4 第 5 条**：该条写「本节**未提交**、未推送」——本补记落地并提交后，其中「**未提交**」半句**作废**（本节改动即以该文档提交落库）；「**未推送**」「临时探针已删、未跟踪回到 0」仍成立。

## §53.47 (C) 处置：承重的是**读法**，不是那个 NULL —— 而「改模型」不是修法

> 追加于 §53.46 之后。承接 §53.44.7 第 2 条与 §53.45.6 的 (C) 项：`kernel_market_snapshots.captured_at` 可空（notnull=0）而兄弟表 notnull=1。**用户拍板：加固读法**（不采纳「只登记」，也不采纳「改模型」）。

### 53.47.1 结论先行

(C) 项过去被记成「形状与兄弟表不一致」。查完的结论是：**那个形状本身不可达，但它「授权」的一种状态，在未变异的代码上就能让两个 audit 路由 500。**

- `KernelMarketSnapshot.captured_at` **可空、且无默认值**（`kernel_db.py:272`）；兄弟表 `kernel_futures_snapshots:473`、`kernel_traditional_odds_snapshots:396`、`kernel_sport_edges:304` 都是 `nullable=False`。
- NULL **不由代码产出**：唯一 ORM 构造点在 `MarketSnapshotStore.append_snapshot`，它写死 `when = captured_at or _utcnow()`（必非空）；全仓无 raw insert；`backend/kernel_predictions.db` 里该表 **0 行**（§53.11.1 已记：生产 job `sport_market_snapshots` **从未被调用**）。
- 但「可空」**授权**了 `KernelMarketSnapshot(link_id=…, implied_prob=…)` 这种**合法**构造 —— 不需要任何写入方「出错」。
- §53.44 补的 tiebreaker **不改变**这一点（§53.44.7 第 2 条已写明「正交」）。

⇒ 处置：**让读法对「无时间戳行」有确定、可声明的行为**；**不动模型**（理由见 53.47.4）。

### 53.47.2 一行合法 NULL 的四个后果（**未变异**代码实测）

探针 `.workbuddy/audit-2026-10-03/probe5347_null_row.py`（真 `MarketSnapshotStore` + temp kernel DB，**不经过任何变异**）：写 1 行带时间戳（`2020-01-01`）+ 1 行**按模型合法**地省略时间戳。

| 读法 | 读数 | 后果 |
|---|---|---|
| `get_snapshots`（ASC，喂 `series` 路由 / 折线） | `asc_series_order_ids=[2, 1]` ⇒ NULL 行**排最前** | 被当成「最早」的点（静默） |
| `get_latest_snapshot`（DESC） | `desc_latest_row_id=1`（非空行） | NULL 行永远当不了「最新」⇒ **安全** |
| `_find_settlement_snapshot`（`captured_at <= T`） | `settlement_filter_visible_rows=1`（共 2 行） | SQL 三值逻辑 ⇒ NULL 行**静默不可见** |
| `audit_summary` | `TypeError: '<' not supported between instances of 'datetime.datetime' and 'str'` | **500** |

`rows_with_null_captured_at=1` —— 即「合法构造」确实落了库。两个 audit 路由（`sport_markets.py:140` / `:165`）**外面没有 try** ⇒ 是 500 而非降级。前端 `link-audit-trail.tsx:23` 的 `stamp(value: string | null | undefined)` **显式接受 null** ⇒ 前端不会崩，崩只发生在后端。

### 53.47.3 `audit_summary` 为什么会抛

```python
def sort_key(item):
    t = _ts(item[0])      # 真实读法给 datetime；既有夹具给 ISO 字符串
    return t or ""        # ← 缺时间戳时是 str：键「随输入变类型」
```

`_ts(s) = s.get("captured_at") or s.get("created_at")`。**真实行给 `datetime`，而 `test_audit_summary_with_prices` 的夹具给的是 ISO 字符串** —— 说明这个键从一开始就假定「两种类型都可能」。一旦同一序列里既有 `datetime` 又有「缺失」，`sorted` 就炸。

⇒ 根因不是「有人写了 NULL」，而是**排序键的类型取决于输入**。

### 53.47.4 为什么「把模型改成 NOT NULL」不是修法

两条都是派生的：

1. **对已有库无效。** `init_kernel_db`（`kernel_db.py:481`）= `_migrate_dormant_tables`（只 DROP 三张具名老表）+ `KernelBase.metadata.create_all`，而 `create_all` 是 `CREATE TABLE IF NOT EXISTS`、**从不 ALTER**。所以改完之后，已存在的 `kernel_predictions.db` **仍是可空列**，而 `test_kernel_db_model_shape.py` 会**断言一个部署库里并不存在的形状** —— 正是那份守卫自己的文档所警告的 model↔DB 漂移，只是换了个方向。
2. **它管不住来源。** 就算新库变成 NOT NULL，任何一行来自旧库 / 脚本 / 手工 SQL 的 NULL 照旧让读法炸。要让读法安全，只能改读法。

⇒ 采用：`audit_summary` 把「无时间戳行」分区出去（不参与 first/last），用 `untimestamped_count` + `flags` 显式声明；排序键**归一到单一类型**。

### 53.47.5 空头实验：149 条相关用例对「写入侧回退被删」完全盲

按 §4.2 先证明既有断言是空头，再补。把 `market_snapshot_store.py` 的 `when = captured_at or _utcnow()` 改成 `when = captured_at`（其余不动），跑 8 个快照消费者文件（`test_sport_market_stores` / `test_sport_market_routes` / `test_market_liquidity_degraded` / `test_edge_detector_service` / `test_market_settlement_service` / `test_market_settlement_routes` / `test_market_settlement_cli` / `test_phase7_e2e_integration`）：

| 状态 | junit |
|---|---|
| 变异前 | `tests=149 failures=0 errors=0 skipped=0` |
| 变异后 | `tests=149 failures=0 errors=0 skipped=0` |

⇒ **一条都没红**。变异的有效性由正控证明（同一变异下 `row_b_omitted_captured_at=None`、ASC 串首是 NULL 行）。还原后该文件 sha256 与基线**逐字节一致**（`fbf6f5df6ba0d643c5690088fc2b7bead73f1a85893d3d77eac193283f44ce9c`）。

⇒ 成因是 §4.2 的 **(c) 断言负向或过弱**：既有断言只查 `len(snaps)` / `implied_prob`，**从不查 `captured_at`**；而 `get_latest_snapshot` 在「全 NULL」时靠 `id.desc()` 仍返回最后一行 ⇒ 连 `implied_prob == 0.65` 也照过。

### 53.47.6 修法与变异登记

代码（`backend/app/kernel/market_snapshot_store.py` 的 `audit_summary`）：

- priced 行分成 `timestamped` / 无时间戳两段；无时间戳行**不参与** first/last/delta/max_drawdown。
- 全是无时间戳 ⇒ `available: False` + `untimestamped_count` + `flags: ["untimestamped_snapshots"]`。
- `sort_key` 归一到单一类型（`datetime` → `isoformat()`，其余 `str()`）；缺时间戳的行已被分区排除。
- 成功分支新增 `untimestamped_count`（**纯加法**）；`snapshot_count` 仍是有效价行数。

守卫（`backend/tests/test_ece_and_market_audit.py`，+3 条 ⇒ junit `tests=7 failures=0 errors=0`）：

- **端点取值**断言（不是「没抛异常」）：`first/last_captured_at` 等于真实时间戳行、`first/last_price` 等于对应价格。
- 全无时间戳 ⇒ `available is False` 且 `flags == ["untimestamped_snapshots"]`。
- 混排 `datetime` + ISO 字符串 ⇒ 不抛且按时间序。

harness（`backend/scripts/mutation_verify.py` 新 set `market-audit-timestamps`；`tests/test_mutation_verify.py` 的**显式有序清单**加一行）：

| 变异 | 打掉什么 | 守卫 | 变异后读数 |
|---|---|---|---|
| A1 | 分区谓词（NULL 行落回时间轴） | `…_endpoint` + `…_not_available` | `2 failed, 5 deselected` |
| A2 | 排序键的类型一致 | `…_mixed_timestamp_types…` | `1 failed, 6 deselected` |

两条都走完四阶段（变异前绿 / 变异后 **1/1 文件红** / 还原后绿 / 逐字节一致），尾巴里 `[safe-delete]` 出现 **0** 次。`list` 自报：**共 10 套 / 76 个变异**（原 9 套 / 74 条）。

同时把 `test_the_eight_sets_are_present` 改名为 `test_the_ten_sets_are_present`：它此前**名字写 eight、清单却列九项**（`market-snapshot-order` 于 §53.44 加入时没改名），本次一并更正。

### 53.47.7 门

- `ruff check app/`（§0 门）：**All checks passed!**
- `mypy app/`：**Success: no issues found in 331 source files**
- `python -m scripts.generate_types --check`：**generated-types.ts is up to date.**
- 定向回归（10 个文件，含本节的守卫文件与 harness 守卫）：junit **`tests=325 failures=0 errors=0 skipped=0`**
- ruff 派生核对（§53.44.5 的口径）：两个改动的测试文件，**当前与 HEAD blob 在同配置下都是 `All checks passed!`** ⇒ 本节新增 **0** 条。
- `verify market-snapshot-order` **复验通过** —— 本节 diff 触及该集的 `m.path`，故按 §4.3 一并跑（两条都 `1/1 文件红`、四阶段全过）。
- 全量回归：见 53.47.9。

### 53.47.8 未决 / 留痕

1. **`get_snapshots` 仍是「忠实的原始读法」**：一行无时间戳的快照仍会由 `series` 路由 / 折线图收到，并按 SQLite 的 ASC 语义**排在最前**（53.47.2 第一行）。审计侧现已把它排除并声明，于是**两个门对「无时间戳行」的处理不同**：审计 = 排除 + flag；原始序列 = 照发。这是**有意**的（原始读法的 docstring 承诺 "All snapshots"），但它是一处新的**不对称**，登记待拍板。
2. **`_find_settlement_snapshot` 静默忽略无时间戳行**（SQL 三值逻辑），安全但无声 —— 与 §5「0 必须只说一件事」同族，**只登记**。
3. **`snapshot_count` 在两个分支里含义不同**：`no_prices` 分支是 `len(snaps)`（全部行）、成功分支是有效价行数。**既有**、与本节正交，**只登记**。
4. **`_ts` 的 `created_at` 回退是死键**：`KernelMarketSnapshot` 无该列、`_row_to_dict` 也不产它 ⇒ `s.get("created_at")` 恒为 None（同族：`_price` 里的 `yes_price` / `market_prob` / `prob` 三个键同样无生产者）。**代码卫生**，按 §4.2 不登记为变异。
5. 模型形状本身（`nullable=True` vs 兄弟表 `NOT NULL`）**保持原样**，`test_kernel_db_model_shape.py` 的冻结清单**不动** —— 见 53.47.4。

### 53.47.9 全量读数

`pytest tests -q --junitxml=junit-5347-full.xml`（后台，23m47s）：**`tests=7735 failures=0 errors=0 skipped=11`**；日志里 `[safe-delete]` 出现 **0** 次。

与 §53.45 的全量（`junit-53-45-full.xml`：`tests=7729 failures=0 errors=0`）**逐文件**比对，元素级只差一处：

| | §53.45 | §53.47 | 差 |
|---|---|---|---|
| `tests.test_ece_and_market_audit` | 4 | 7 | **+3** = 本节新增的 3 条守卫 |
| 全部 `testcase` 元素 | 6629 | 6632 | **+3** |

`tests` 属性由 7729 升到 7735（**+6**）—— 另 3 条落在「pytest 把**子测试**也计入 `tests`」这一口径上（与元素数无关）：`test_mutation_verify.py` 的 `test_the_selected_mutation_is_the_inventory_object` 遍历**每个变异**（新 set ⇒ **+2** 次迭代），`test_the_whole_set_selection_passes_preflight` 遍历**每个 set**（⇒ **+1**）。⇒ **6 = 3 条用例 + 3 条子测试**，逐项有归属。

## §53.48 两个门对「无时间戳行」的口径统一：一处谓词，两个门

> 追加于 §53.47 之后。承接 §53.47.8 第 1 条：审计门已「排除 + 声明」，而序列门把它当**最早**的点发给图表。用户拍板：**序列门也排除**。

### 53.48.1 结论先行

口径定为：**没有时间戳的快照不是价格路径上的点。** 两个画路径的读法都排除它，并各自**声明丢了几行**：审计门 = `untimestamped_count` + `flags: ["untimestamped_snapshots"]`（§53.47 已做）；序列门 = `GET /snapshots/{match_id}` 每条序列新增 `untimestamped_count`（本节）。

两点合起来才是本节的重点：

1. **判据只有一份。** 提成 `market_snapshot_store.is_timestamped(row)`：审计的分区（`audit_summary`）与序列的过滤（`get_price_path`）都用它。此前审计用的是 `audit_summary` **函数体内嵌套**的 `_ts`，而序列门根本没有这道判断 —— 两处会各自漂移。
2. 🔴 **过滤只能放在消费侧，不能放进 `get_snapshots` 的默认读法。** 若在 `get_snapshots` 里过滤，`audit_summary` 的 `untimestamped_count` 会退化成**永远为 0 的死分支**，而猴补 `get_snapshots` 的守卫测试**照样绿** —— 这正是 §4.2 那类**假安全**。⇒ 原始读法保持「照发」，两个消费者各自排除 + 声明。

### 53.48.2 取证与修法

- `MarketSnapshotStore.get_price_path(link_id=…) -> (points, untimestamped_count)`：序列门的**唯一**取数口；它调 `get_snapshots`（照发）后按共享谓词过滤，并把丢掉的条数一并返回 ⇒ 一次查询就够，不必为一个计数再打一次库。
- `_ts` 从 `audit_summary` 的嵌套函数**提到模块级**（体不变），`is_timestamped` 建在它之上；`audit_summary` 的四个 `_ts(...)` 调用点改指向模块级同名函数，分区谓词换成 `is_timestamped(s)`。
- 路由 `GET /snapshots/{match_id}`：`snapshots` 只含可定位的行，另回 `untimestamped_count`。

守卫（`tests/test_sport_market_routes.py`，+1 条；并把既有 `test_snapshots_timeseries` 补上 `untimestamped_count == 0`）：新用例写 1 行正常 + 1 行**按模型合法**省略时间戳的 `KernelMarketSnapshot(link_id=…, implied_prob=…)`，断言的是**确切点位与计数** —— `len(snapshots) == 1`、`implied_prob == 0.6`、`captured_at is not None`、`untimestamped_count == 1`。断「请求成功」是不够的：行数变了但计数没报也会通过。

### 53.48.3 变异（追加进同族 set，§4.3）

`market-audit-timestamps` 由 2 条扩到 4 条：

| 变异 | 打掉什么 | 守卫文件 | 变异后读数 |
|---|---|---|---|
| A3 | `get_price_path` 的过滤（照发） | `tests/test_sport_market_routes.py` | `1 failed, 14 deselected` |
| A4 | **共享谓词** `is_timestamped` 塌成恒真 | **两个文件** | **`2/2 files red`**（各 `1 failed`） |

A4 的 `2/2` 就是「一处谓词、两个门」的可验证表述。四条全部走完四阶段（变异前绿 / 变异后红 / 还原后绿 / 逐字节一致）。

### 53.48.4 未决 / 留痕

1. 前端 `SnapshotPoint.captured_at` 仍是 `string | null`。**不去收紧它** —— 按 §2，接收端类型偏宽是安全方向，收紧反而要动前端类型与前端测试。本节不动前端。
2. `_ts` 的 `created_at` 回退仍是**死键**（§53.47.8 第 4 条未做），而且现在被 `is_timestamped` 一起继承。**登记不变**。
3. `/snapshots/{match_id}` 仍是裸 `dict[str, Any]`（无具名 `response_model`）⇒ 前端 `SnapshotSeries` 是手写 interface（§2 的既有症状）。本节只**追加**一个字段，不处理这条。

### 53.48.5 门 + 全量

四道门：`ruff check app/` 通过、`mypy app/` 331 文件通过、`generate_types --check` 最新、定向 pytest 通过。ruff 做**派生**核对（§0：判据派生且同配置）——改动文件与各自 HEAD blob 在同一条 `ruff check app/` 配置下都是 `All checks passed!` ⇒ 新增 **+0** 条。

- 定向 junit（三个受影响文件）：`tests=49 failures=0 errors=0 skipped=0`。
- MV 守卫 junit（`tests/test_mutation_verify.py`）：`tests=171 failures=0 errors=0 skipped=0`。
- 定向 junit（本节点相关的更大集合）：`tests=328 failures=0 errors=0 skipped=0`。
- **全量**（`junit-5348-full.xml`，946008 B）：`tests=7738 failures=0 errors=0 skipped=11`，元素级 **6633**。

`tests` 属性的增量**逐 classname 对齐**（不口算）：与 §53.47 全量（`junit-5347-full.xml`：`7735 / 0 / 0 / 11`，元素 6632）相减，元素差**只**落在 `tests.test_sport_market_routes 14 → 15` —— 即新增的那条守卫（+1）。`tests` 属性是 `+3`（7735 → 7738）而元素只 `+1` ⇒ 另 **+2 子测试**，来自 `test_the_selected_mutation_is_the_inventory_object`（它逐条遍历**全部**变异并用 `subTest`）；本节点把 `market-audit-timestamps` 由 A1/A2 扩到 A3/A4 ⇒ 变异总数 76 → 78 ⇒ `+2`。这与 §53.47 同型（那次元素 +3 / 子测试 +3）。其余**可能**随规模漂移的 `subTest` 循环都不受影响：`grouped.items()` 只随 (set, group) 对数量变，而 A3/A4 未声明 `group`；`for mutation_set in SETS` 随**套数**变，本节点套数不变（仍 **10 套**）。

变异：`verify market-audit-timestamps` 的 A1–A4 四条**全部**走完四阶段（变异前绿 / 变异后红 / 还原后绿 / sha256 与原字节一致）；A4 的守卫跨两个文件、实测 **`2/2 files red`**。复验 `verify market-snapshot-order`（diff 触及该集 `m.path`，§4.3）通过。harness 自报 `list` = **10 套 / 78 个变异**。

本节**未推送**。

## §53.49 把 `/snapshots/{match_id}` 的合约补成具名模型（§2 三件套），并更正一处被 §53.48 改动过的行号引用

> 承接 §53.48.4 第 3 条：该端点仍是裸 `dict[str, Any]`，而前端 `SnapshotSeries` 是**手写** interface —— §53.48 刚给它加了 `untimestamped_count`，手写 interface **当场过期**。用户点选「补具名响应模型（§2 三件套）」。

### 53.49.1 结论先行

三件事在同一条线上：

1. **补 `response_model`**：`/snapshots/{match_id}` 现在挂 `response_model=MarketSnapshotsResponse`（`MarketSnapshotsResponse` → `MarketSnapshotSeries` → `MarketSnapshotPoint`）；前端类型改为从 `generated-types.ts` **re-export**，手写的 `SnapshotPoint`/`SnapshotSeries` 删除。
2. 🔴 **发现并处理好「生成类型比手写更松」的一条机制**：`pydantic2ts` 只对**根模型**临时置 `extra="forbid"`，以此抑制 `json2ts` 的 `[k: string]: unknown` 尾巴；嵌套模型不享受 ⇒ interface 是**开放**的。故三个模型都登记进 `_frontend_export`（= 导入进模块命名空间），产物是**闭合** interface。这不是洁癖 —— 被替换的手写 interface 本来是闭合的。
3. **顺带更正一处前提过期**（§0）：`mutation_verify.py` 里 A2 说明写死的 `sport_markets.py:140 / :165` 已被 §53.48 的改动下移，本轮又 +1 ⇒ 更正为 **151 / 176**。

### 53.49.2 缺口是 §53.48 当场造出来的

调用点在 §53.48 从 `snaps.get_snapshots(...)` 改成 `snaps.get_price_path(...)` 并把 `untimestamped_count` 加进每条 series —— 而前端 `sports-api/types.ts` 的 `SnapshotSeries` 是**手写**的四字段 interface。于是「后端发了、前端类型里没有」。当时 §53.48.4 第 3 条只作登记（该节只追加一个字段、不处理这条）。

实测（`grep`，非印象）：`untimestamped_count` 在 `frontend/src` 命中 **0**。今天它**无害**（TS 不校验多余字段、暂无组件读它），所以这是「类型漂移」而不是运行缺陷 —— 但它正是 §2 所说「前端手写 interface 常常是后端缺具名 `response_model` 的症状」。

### 53.49.3 机制：为什么必须「登记为根」才闭合

- `scripts/generate_types.py` 走 `pydantic2ts`。`_extract_pydantic_models(module)` = `inspect.getmembers(module, _is_pydantic_model)` ⇒ 收集的是**模块命名空间里导入的类**，`__all__` **不参与**（它只是人读的清单）。
- `_generate_json_schema()` 造一个 `_Master_` 把每个模型当一个字段，并对**每个被收集到的模型** `enter_context(_schema_generation_overrides(model))`；该 contextmanager 把 `config.extra` 临时改成 `"forbid"`（除非显式设过 `"allow"`），其 docstring 原话：*"This prevents '[k: string]: any' from automatically being added to every interface."*
- ⇒ **根模型** → `additionalProperties: false` → `json2ts` 出**闭合** interface；**只在 `$defs` 里被 `$ref` 的嵌套模型** → 无 `additionalProperties` → 出 `[k: string]: unknown`。（字段仍全部必填 —— 松掉的只是「允许多余键」。）
- **实测对照**：`DailyDigestResponse`（根）闭合，而它的嵌套 `DailyDigestHeadline` / `DailyDigestMover` **带尾巴**；`DecisionTimelineResponse`（根）闭合而 `DecisionTimelineSnapshot`（嵌套）带尾巴；`TradeStatBucket` 是根但**承 `FlexibleResponse`（`extra="allow"`）** ⇒ 覆盖跳过、仍带尾巴。
- ⇒ 「登记为根」是**必要条件**：只靠 `MarketSnapshotsResponse` 引用它们，产物会是松的。

### 53.49.4 产物（三个 interface，逐字）

```ts
export interface MarketSnapshotPoint {
  id: number;
  link_id: number;
  implied_prob: number;
  price: number | null;
  liquidity: number | null;
  volume: number | null;
  captured_at: string | null;
}
export interface MarketSnapshotSeries {
  contract_id: string;
  outcome_label: string;
  mapped_outcome: string;
  snapshots: MarketSnapshotPoint[];
  untimestamped_count: number;
}
export interface MarketSnapshotsResponse {
  match_id: string;
  series: MarketSnapshotSeries[];
}
```

三条值得记下的取值：

- `price` / `liquidity` / `volume` / `captured_at` 是 `number | null`（**必填可空**）而不是 `?`：模型里写作 `X | None` 且**不给默认值** ⇒ pydantic 视为 required-but-nullable，这是唯一能表达「必存在但可为 null」的拼法。给了 `= None` 就变成 `?: number | null`，比手写的**更松**（§2 的原话）。
- `captured_at` 在模型里是 `datetime | None`，**不是** `str | None`：`_row_to_dict` 交出来的是 SQLAlchemy `DateTime`，声明成 `str` 会让 pydantic 校验失败 ⇒ 端点 500。pydantic 序列化成 ISO 串，TS 侧仍是 `string | null`。
- 手写 `SnapshotPoint` 原本**漏了 `link_id` / `liquidity` / `volume`**（端点一直在发这三个键）—— 生成类型把它们**补齐**了，这是本次的附带修正。

### 53.49.5 前端：re-export，不再手写

- `sports-api/types.ts`：删 `SnapshotPoint` / `SnapshotSeries`，改 `export type { MarketSnapshotPoint, MarketSnapshotSeries, MarketSnapshotsResponse } from "@/lib/generated-types";`
- `hooks/use-markets.ts`：本地 `type SnapshotsResponse = { series: SnapshotSeries[] }` 删除，改用生成的 `MarketSnapshotsResponse`（它多了 `match_id`，正是端点一直在发的）。
- 两个消费组件（`MarketSnapshotChart.tsx` / `TraditionalOddsChart.tsx`）`SnapshotSeries` → `MarketSnapshotSeries`（机械改名；`SnapshotPoint` 无人直接引用，零改动）。
- 全前端 `\bSnapshotSeries\b|\bSnapshotPoint\b` 命中 **0**。

### 53.49.6 守卫与变异（新增 `market-snapshot-contract`，C1–C2）

| 变异 | 打掉什么 | 守卫文件 | 变异后读数 |
|---|---|---|---|
| C1 | 路由的 `response_model=` | `tests/test_sport_market_routes.py` | `1 failed, 15 deselected` |
| C2 | `_frontend_export` 对该模型的**导入** | `tests/test_generate_types.py` | `1 failed, 6 deselected` |

两条都必须有，因为**§2 三件套在行为上是隐形的**：C1 删掉 `response_model` 后返回体一字节没变、该文件其余断言全绿；C2 挪走导入后该模型仍是嵌套 interface、`tsc` 照样绿。⇒ 各钉一半，分别落在**路由对象**与**导出模块**上。

- **C1 的阶段①抓到了我自己的 bug**：守卫最初写 `route.path == "/snapshots/{match_id}"`，而 `APIRoute.path` **已含 router 前缀**（实为 `/sport-markets/snapshots/{match_id}`）⇒ 未变异时就是红的（`assert 0 == 1`）。四阶段的「变异前必绿」正是为这类错误准备的 —— 依赖行为断言的守卫永远碰不到它。
- C2 的改写是**改名**而非删除（`MarketSnapshotSeries,` → `_MarketSnapshotSeriesUnused,`）：`__all__` 里仍有带引号的同名项，而只有 `from module import *` 才会因缺名报错（本仓不这么用），所以模块照常导入。针在各自文件里各出现**恰好 1 次**（`read_bytes().count()` 实测）。
- `test_mutation_verify.py` 的清单守卫随之由 `test_the_ten_sets_are_present` 改名 `test_the_eleven_sets_are_present`（名字里的数字就是计数，加集必须改名 —— 该测试的 docstring 已把这条写成传统）。
- harness `list` 自报 **11 套 / 80 个变异**。

### 53.49.7 前提过期更正（§0）

`mutation_verify.py` 里 A2 的说明引用 `sport_markets.py:140 / :165`，指的是两处 **`audit_summary(...)` 调用点**（外面没有 try ⇒ 是 500 而非降级）。该引用在 §53.47 时**是对的**（该提交里正是 140/165）；但 §53.48 给本文件加了 docstring 与序列门调用、整体下移到 146/165，**而 §53.48 没有改它**；本节又加了一行 import ⇒ 现值 **151 / 176**。已就地更新为 151/176，并把两次位移写进说明。另：审计档 §53.47.2 与 §53.43.5(e) 也有同类写死行号 —— **档只追加不改写**，在此登记。

（行号取自 `grep -n`，非口算；`140 → 151` 与 `165 → 176` 各 +11 = §53.48 的 +10 与本节的 +1。）

### 53.49.8 未决 / 留痕

1. ⚠️ **「登记为根」本身只有 C2 覆盖的那三个模型有守卫**；既有嵌套模型（`DailyDigestHeadline` / `DailyDigestMover` / `DecisionTimelineSnapshot` 等）仍带 `[k: string]: unknown`。是否把它们也登记为根要**逐处判断**（登记会改产物、可能牵动下游类型），**不代决**。
2. `/sport-markets/*` 其余端点仍裸 `dict[str, Any]`（§2：既定策略；具名模型只在「有前端消费者」时才有意义 —— 本节点处理的正是唯一那个有消费者的）。`LatestLink.latest_snapshot` 的内联形状与 `_row_to_dict` 同源、同样漏了 3 个字段 —— **登记**，本节不动（那是另一个端点）。
3. 前端 `captured_at` 不再由手写控制，而是生成类型的 `string | null` ⇒ §53.48.4 第 1 条的「不收紧」自动满足，且不再有手写副本可漂。
4. 本节**未推送**。

### 53.49.9 门 + 全量

**四道门**（改动后的工作树上重跑）：

- `ruff check app/`：`All checks passed!`
- `mypy app/`：`Success: no issues found in 331 source files`
- `python -m scripts.generate_types --check`：`generated-types.ts is up to date.`
- 前端：`tsc --noEmit` rc=0；`next build` ✓（40/40 静态页落盘、`tsconfig.json` 未被重排）；受影响 vitest 19 文件 / 107 用例全过（后两者与本项同工作树）。

**全量**（`pytest tests -q --junitxml=junit-5349-full.xml`；**判据只看 junit，不看进程 rc** —— 本机 `safe-delete` shim 把 rc 恒置 1）：

`tests=7743 failures=0 errors=0 skipped=11`（`<testcase>` 元素 6635）。

**增量归因**（对 §53.48 基线 `junit-5348-full.xml`：`tests=7738 failures=0 errors=0 skipped=11`，元素 6633）：

- `tests` 属性 **+5**。
- `<testcase>` **元素 +2**，逐 classname 对齐 = 两条新用例：`tests.test_sport_market_routes` 15→16（`test_snapshots_route_publishes_its_response_model`）、`tests.test_generate_types.AllowlistCompositionTests` 0→1（`test_market_snapshot_models_are_export_roots`）；`test_the_ten_sets_are_present` → `test_the_eleven_sets_are_present` 是**改名**，同类内一进一出 ⇒ 元素净 0。
- 余下 **+3 是 subTest 迭代，不是元素**。机制先实测再归因：本机 pytest 9.1.1、**未装** `pytest_subtests`；`with self.subTest(...)` 的**每次迭代**都给 `tests` 属性 +1，**却不写** `<testcase>`（探针：3 个方法、其中一个 `for i in range(4)` → `tests=7` 而元素 3）。⇒ `tests` 属性与元素数之差 = 全表 subTest 迭代总数。增长全落在两处循环：
  - `test_the_selected_mutation_is_the_inventory_object` 逐条遍历**全部**变异：**78 → 80**（+2）；
  - 整集 preflight 的 `for mutation_set in mutation_verify.SETS` 循环：**10 → 11**（+1）。
- **交叉验证**：`tests` 属性与元素的差在两版间为 1105 与 1108；扣掉上面两处循环（88 与 91）后，**其余全表 subTest 迭代数两版恒为 1017**（1105−88 = 1108−91）⇒ 增长的 3 **恰好**来自这两个循环，无未解释余数。⇒ `tests` +5 = **2 元素 + 3 子测试**。

（读数为工具自打印：`mutation_verify list` 报 **11 套 / 80 变异**；`len(mv.SETS)`=11、`_select_verify_targets([], None)`=80 系 `python -c` 实测。）

## §53.50 把「可达即须登记为根」推广成规则：43 条尾巴拆成 25 条故意 + 18 条可闭合

> 承接 §53.49.8 第 1 条：既有嵌套模型（`DailyDigestHeadline` / `DailyDigestMover` / `DecisionTimelineSnapshot` 等）仍带 `[k: string]: unknown`，是否登记为根「要逐处判断（登记会改产物、可能牵动下游类型），**不代决**」。用户点选 A。

### 53.50.1 结论先行

1. **登记了 18 个**模型为根（`_frontend_export` 由 23 名字 → **41 名字**）⇒ `generated-types.ts` 里带 `[k: string]: unknown` 尾巴的 interface 由 **43 条降到 25 条**；重生成后 `tsc --noEmit` **rc=0**（收紧要检过才知道安全，这句是实测不是推断）。
2. **剩下 25 条一律是"故意开放"**，不动：16 个承 `FlexibleResponse` 的根 + `EventRecord` + 8 个**显式**声明 `ConfigDict(extra="allow")` 的 overlay 模型（`ConfidenceBreakdown` / `CrossValidation` / `DecisionQuality` / `EventSource` / `EvidenceItem` / `LLMTelemetry` / `MarketQuality` / `SourceReliability`）。把它们塌成闭合才是**失真**。
3. 🔴 **本轮最值钱的两条是"检测错了"**：第一版把「尾巴」按**缩进盲搜**，把 `dict[str, Any]` 字段的 **inline** `{ [k: string]: unknown }` 也算成尾巴（25 误报成 43）；第一版又按**名字出现**判消费者，把 26 个嵌套模型判成"无人消费"——实际它们全是 `EventRecord` 的字段类型，而 `EventRecord` 被 61 处消费。两条都在下面留证。
4. 顺带修掉一处**守卫弱于其名**：`test_generates_file_with_all_allowlist_models` 硬编码 16 个模型名，而 allowlist 已有 23 ⇒ 静默漏检 7 个（`SimTrade` / `SimTradeListResponse` / `TradeStatBucket` / `TradeStats` + 三个 `MarketSnapshot*`）。已改为**遍历 `__all__`**。

### 53.50.2 「开放」有三种成因，第一版检测把它们混了

`json2ts` 只在 JSON schema 有 `additionalProperties: false` 时才不补尾巴。没有该键的原因有三种，**判据要把它们分开**：

| 成因 | 机制 | 该不该闭合 | HEAD 条目数 |
|---|---|---|---|
| (a) `extra="allow"` | 生成器 `_schema_generation_overrides` **刻意跳过**（源码注释原话：*"This prevents '[k: string]: unknown'…"* 的前提就是 allow 者除外） | **不** —— 故意开放 | 25 |
| (b) 纯嵌套 `BaseModel` | 只被 `$ref` 到 ⇒ 从未作为根跑覆盖 | **是** —— 登记为根即可 | 18 |
| (c) 裸 `dict[str, Any]` 字段 | 渲染成 **inline** `{ [k: string]: unknown }`，在**字段自己的花括号内** | §2 既定策略，本节**不动** | （不计入尾巴） |

- (a) 的 25 条 = 16 个承 `FlexibleResponse` 的根 + `EventRecord`（显式 `ConfigDict(extra="allow")`，docstring 写明是为 `build_event_record` 的前向兼容）+ **8 个嵌套但显式 allow 的 overlay 模型**。**这 8 个也是"嵌套"，但它们与 (b) 的处置相反** —— 这正是"逐处判断"的真正内容：**不是看"是不是嵌套"，是看"有没有显式 opt-in"**。
- 🔴 **第一版的错**：判尾巴用 `^\s*\[k: string]: unknown;`（`\s*` 允许任意缩进）⇒ (c) 的 inline 行也被计入。实测两版对照：`DecisionTimelineSnapshot` 体内有 5 处 inline（`probability` / `decision_quality` / …），它们不是尾巴。**严格判据 = 接口体内最后一个非空行恰好是 `  [k: string]: unknown;`**。改正后：HEAD = **43**（此前报 43 是巧合：+18 假阳性 − 18 漏计真尾巴，见 53.50.4 的对照），实验态 = **25**。
- ⚠️ 这条值得单独记：**"数出来一样"不等于"数对了"** —— 两个方向的错误可以互相抵消出正确的总数。

### 53.50.3 「消费者」要按可达性判，不是按名字出现判

第一版用 `\bName\b` 搜前端，`Probability` / `Outcome` / `Calibration` 这类**普通英文词**命中一堆（`app/layout.tsx` 里的 `Probability` 显然是别的意思）⇒ 假阳性。改用**真实类型引用**（`import`/`export … from "…generated-types"`）后：

- 前端从 `generated-types` 命名导入/再导出的**全部**名字只有 **15 个**，落在 3 个 hub：`lib/api.ts`（9）、`lib/types.ts`（3）、`lib/sports-api/types.ts`（3）。
- 26 个候选嵌套模型里，**只有 2 个**被直接命名：`EvidenceProfile`、`EventSemantics`（`lib/types.ts:15-19` 的 `import type { … as Backend… }`），且它们的别名 `EvidenceAggregate` / `Semantics` **各只有 1 处命中 = 它自己的定义行** ⇒ 别名零消费者。
- **但这不是"无人消费"**：26 个**全是** `EventRecord` 的字段类型，而 `EventRecord` 被 **61 处**消费（`app/analyze/page.tsx`、`app/events/page.tsx`、`components/detail/*`，并含 `evidence-list.test.tsx:20  const baseRecord: EventRecord = {` 这类**对象字面量构造**）。⇒ 传递性**都有**消费者。
- 🔴 教训：**"名字没出现"≠"没有消费者"**。名字是**命名**，字段是**可达**；判消费要走可达性（生产者的字段注解），不是 grep 名字。

### 53.50.4 判定与产物

- **登记 18 个**（= (b) 类全部）：`ActionableRecommendation`、`Calibration`、`Credibility`、`DailyDigestHeadline`、`DailyDigestMover`、`DailyDigestUnchanged`、`DecisionEvidenceItem`、`DecisionTimelineDiff`、`DecisionTimelineSnapshot`、`EventSemantics`、`EvidenceBreakdownItem`、`EvidenceProfile`、`Impact`、`IntelligenceReport`、`Outcome`、`Probability`、`Risk`、`Tracking`。
- **登记 ≠ 全闭合**：为验证机制边界，先把**全部 26 个**嵌套模型都登记（含 8 个显式 allow 的），重生成后实测只闭合 **18** ⇒ 那 8 个**照旧开放**（成因是 (a) 不是 (b)）。这一步把"嵌套 ⇒ 登记即可闭合"从推断变成**有边界的实测**，也确认了 `DecisionTimelineDiff` / `DecisionTimelineSnapshot` 属于 (b) 而非顽固项。
- **最终对照**（严格判据，`git show HEAD:…` vs 工作树）：尾巴 **43 → 25**；**新变开放的 = 0**；闭合的 18 个与上面清单**逐一相等**。
- 可达性实测：`_frontend_export` 命名空间里的根 **41**；从根沿字段注解可达的模型 **49**；其中 `extra != "allow"` 且必须登记的 **24**（= 本轮 18 + 既有 6 个纯 `BaseModel` 根）⇒ 登记后 missing = **0**。

### 53.50.5 守卫：生产者驱动，单文件可变异

`test_generate_types.py::AllowlistCompositionTests`（**不**需要 json2ts，故在 CI 后端作业里也跑）新增：

`test_every_reachable_plain_model_is_registered` —— 从 `_frontend_export` **命名空间**（= 生成器真正收集的那一组，**不是** `__all__`）取根，沿 `model_fields` 的注解递归展开 `Optional` / `list` 等，要求**每个可达且 `extra != "allow"` 的模型都在命名空间里**。

- 为什么是**生产者驱动**：它问的是"模型互相引用什么"，所以**在重新生成之前**就会红。若读生成好的文件，则回归要等到有人跑生成器才暴露。
- 与既有 C2 守卫**互补**：C2 钉 `MarketSnapshot*` 这一族的**具体成员**；这条钉「可达即须登记」这条**规则**本身。
- 新变异集 `frontend-type-closure`（**D1**）：把 `EvidenceProfile` 从导入里改名挪走 ⇒ `EventRecord.evidence` 的类型变成 missing ⇒ 守卫红。**四阶段全过**：变异前绿 / 变异后 `1 failed, 7 deselected` / 还原后绿 / `bytes restored True`。针 `    EvidenceProfile,` 在本文件恰好 1 次（`read_bytes().count()` 实测）。
- 清单守卫随之 `test_the_eleven_sets_are_present` → `test_the_twelve_sets_are_present`（名字里的数字就是计数）。harness `list` 自报 **12 套 / 81 个变异**。

### 53.50.6 顺带修正（§0）

1. **守卫弱于其名**：`test_generates_file_with_all_allowlist_models` 的硬编码清单 16 名 vs allowlist 23 名（漏 `SimTrade` / `SimTradeListResponse` / `TradeStatBucket` / `TradeStats` / 三个 `MarketSnapshot*`）⇒ 改为遍历 `_frontend_export.__all__`，名字不再需要人工同步。
2. **过期数字**：该文件 docstring 写 "all 14 allowlist root models"，而当时已是 23 ⇒ 改为"every model listed in the allowlist（read from `__all__`）"。
3. **既存 lint 债务**：`os` / `shutil` / `tempfile` 三个导入在本文件**从未被使用**（`git show HEAD` 上跑 ruff 同样报 3 条 F401）⇒ 本节既然在改此文件，顺手删掉，使它 lint 干净。**不在 `ruff check app/` 门内**，故此前未被拦。

### 53.50.7 未决 / 留痕

1. (c) 类（裸 `dict[str, Any]` 字段的 inline 尾巴）本节**未动** —— 属 §2 既定策略；`DecisionTimelineSnapshot` / `EventRecord.legacy_analysis` 等都在这一类。
2. 那 **8 个显式 `extra="allow"` 的 overlay 模型**保持开放是**刻意**的；若将来要收紧，须先改它们的 `model_config`（那是**契约变更**，不是登记问题）。
3. `LatestLink.latest_snapshot` 内联形状同源同漏 3 字段 —— 仍**登记**（另一个端点，属 §53.49.8 第 2 条）。
4. 本节**未推送**。

### 53.50.8 门 + 全量

**四道门**（改动后的工作树上）：

- `ruff check app/`：`All checks passed!`（另：`tests/test_generate_types.py` 上的 3 条**既存** F401 顺带清掉，见 53.50.6 第 3 条。）
- `mypy app/`：`Success: no issues found in 331 source files`
- `python -m scripts.generate_types --check`：`generated-types.ts is up to date.`
- 前端：`tsc --noEmit` **rc=0**；vitest **125 文件 / 738 用例**全过。

**产物完整性（严格判据）**：把 `HEAD:generated-types.ts` 与工作树逐 interface 比对，规则是「删掉**恰在两格缩进的尾巴行**再比较」（不能用子串替换 —— 4 格缩进的 inline 行**包含** 2 格判据，会误判，第一次就把 2 个 interface 错列成"其他差异"）：**18 个恰好只少一行尾巴**、**31 个逐字节相同**、**0 个其他差异**、**增删声明 0**。⇒ 本轮对生成物的改动**就是**这 18 行。

**变异**：`verify frontend-type-closure` → **D1 四阶段全过**（`1 failed, 7 deselected`）。因本轮**重写**了 `_frontend_export.py`（C2 的 `m.path`），一并重跑 `verify market-snapshot-contract` → **C1 / C2 各四阶段全过**。`tests/test_mutation_verify.py`：`tests=176 failures=0 errors=0`。

**全量**（`pytest tests -q --junitxml=junit-5350-full.xml`；**判据只看 junit**）：`tests=7746 failures=0 errors=0 skipped=11`（`<testcase>` 元素 6636）。

对 §53.49 基线（`tests=7743`、元素 6635）的增量：`tests` **+3** = **1 个新元素**（`tests.test_generate_types.AllowlistCompositionTests::test_every_reachable_plain_model_is_registered`）+ **2 次 subTest 迭代**（变异清单 80→81、集合 11→12）；`test_the_eleven_sets_are_present` → `test_the_twelve_sets_are_present` 是**改名**，同类内一进一出 ⇒ 元素净 0。**交叉验证**：`tests` 属性与元素数之差两版为 1108 与 1110，扣掉两处循环（80+11 与 81+12）后**未解释余数恒为 1017** ⇒ 增长的 2 全部来自这两个循环。

（读数为工具自打印：`mutation_verify list` 报 **12 套 / 81 变异**；可达性 41 根 / 49 可达 / 24 须登记 系 `python -c` 实测。）


## §53.51 八个 opt-in 的 overlay 模型：收七个、留一个 —— 以及三处「看着在钉、其实没钉」

§53.50.7 第 2 条留了一句话：*"那 8 个显式 `extra="allow"` 的 overlay 模型保持开放是刻意的；若将来要收紧，须先改它们的 `model_config`（那是**契约变更**，不是登记问题）。"* 本节就是那次拍板。结论是**不能一起收** —— 8 个里只有 7 个收得起，第 8 个（`EventSource`）**必须留**。

### 53.51.1 判据：按「生产者发不发未声明键」分，不按「是不是 opt-in」分

`extra="allow"` 的**意图**和**实际需要**是两件事。逐个模型读它是**谁**产出的、产出什么键之后：

| 模型 | 真生产者（读过的） | 结论 |
|---|---|---|
| `CrossValidation` | `cross_validation_service.cross_validate` —— docstring 第 48 行自己写明返回 5 键，代码也恰好是那 5 键 | **可收** |
| `ConfidenceBreakdown` | `probability_engine_service.calculate_confidence_breakdown`（9 键，与声明**逐一相等**） | **可收** |
| `EvidenceItem` | `event_intelligence_service.build_evidence_items` —— **显式映射**（`description`→`summary`、`quality_score`→`quality`、`relevance_score`→`relevance`）；`record["evidence_items"]` 只在该文件 `:1030` / `:1395` 两处被写 | **可收** |
| `DecisionQuality` | `build_decision_quality` + `event_intelligence_service:376-388` 的构建失败回退块（11 键**全已声明**） | **可收** |
| `MarketQuality` | `market_quality_service.py:90-103`（12 键，唯一没写的是 `error`，那是回退块写的）+ 回退块 `:431-444`（12 键全已声明） | **可收** |
| `SourceReliability` | `build_source_reliability` + 回退块 `:580-594`（13 键全已声明） | **可收** |
| `LLMTelemetry` | `build_llm_telemetry` + 回退块 `:646-658`（11 键全已声明） | **可收** |
| `EventSource` | **9 个 adapter 往 `source` dict 里加自己的键**：实测 `kalshi_event_source.py:144-155` 发 `question`/`status`/`close_time`/`bid_ask`、`limitless_event_source.py:98` 发 `chain`/`question`/`status`；AST 探针在 `manifold`/`metacus`/`opinion`/`polymarket`/`predict_fun`/`world_cup`/`event_extraction` + `decision_report_service.py:81` + `event_intelligence_service.py:1254` 命中同型 | **必留** |

- 🔴 **`EventSource` 是 8 个里唯一为 allow 举证的 docstring**：*"Permissive: source adapters add their own fields beyond the declared ones (via `extra="allow"`)."* 另外 7 个的 docstring 全是 audit/observability 说明，**没有一个主张**需要接未声明键。**这是"docstring 也是证据"的一次正向兑现**：唯一写下理由的那个，恰好是唯一真需要的。
- ⚠️ **同族污染（探针的坑，§53.50 已踩过）**：探针把 `execution_quality_service.py:167` 与 `event_intelligence_service.py:481` 的块报成 `MarketQuality` 的候选（覆盖率 0.55）。实际它们是**另一个 dict** —— `record["execution_quality"]`，而 `execution_quality` 在 `DecisionTimelineSnapshot` 上声明为 `dict[str, Any] | None`，**没有模型**。⇒ 两条都是假阳性；`MarketQuality` 干净。
- ⚠️ **同名不同物**：`ConfidenceBreakdown`（事件级）与 `kernel/engines/confidence.confidence_breakdown`（体育引擎，`SportConfidencePanel` 的 `total`/`decision_strength`/`data_completeness`…）**只共享名字**。后者进的是体育引擎自己的 payload，从不进 `EventRecord` ⇒ 与本节无关，但正是「同名≠同物」的又一例。

### 53.51.2 为什么这是**纯 codegen** 改动：运行时不变量三条

收紧一个模型的 `extra` 会碰到"运行时行为"的担忧。三条实测把它排掉：

1. **API 报文不变**：`EventStoreEntry.record` 声明为 `dict[str, Any]`（`event.py:589`），`EventListResponse.events` 是 `list[EventStoreEntry]`，而**没有任何端点把 `EventRecord` 当 `response_model`** ⇒ FastAPI 从不按 `EventRecord` 收窄 record ⇒ 报文逐字节不变。
2. **存储不变**：`EventRecord.model_validate(...)` 只在 `event_store.py` 作**校验闸**（`:155` / `:232` / `:270` / `:330`），**返回值被丢弃**；落盘的是原始 dict（`:234-239` `updated = {... "record": record}`）。⇒ 闸门检的还是同一份数据。
3. **旧数据不会开始失败**：pydantic 默认是 `extra="ignore"`（**不是** `forbid`）。**`ignore` 永不 raise** ⇒ 无论旧记录里有什么未声明键，`model_validate` 都仍然成功。真正会让旧数据 500 的是把运行时的 `extra` 设成 `forbid` —— 本节**没有**这么做（`forbid` 只由生成器在**生成 TS 时临时**施加）。
   - ⚠️ 这留下一个**语义位移的说明义务**：`allow`→`ignore` 之后，若将来有人写 `EventRecord.model_validate(x).model_dump()`，未声明键会被**静默丢弃**。今天没有任何调用点这么做（`model_dump` 全仓只有 `event_market_link_store.py:128`，与 event record 无关）⇒ 已写进 7 个模型的 docstring 与 `_frontend_export` 的模块说明。

### 53.51.3 三处「看着在钉、其实没钉」的用例（本轮最贵的发现）

`tests/test_event_record_overlay_typing.py` 里有 3 个用例，**名字和注释都在声称在钉 `extra="allow"`**，但断言是**弱正向**，所以在收紧后**一个都不会变红**：

| 用例 | 它声称的 | 它实际断言的 | 为什么空头 |
|---|---|---|---|
| `test_accepts_extra_fields_via_extra_allow` | "Forward-compat: extra fields don't break validation" | 只 `assertEqual(cv.model, "x")` | **从不读 `cv.extra_field`** ⇒ allow / ignore 两种设置下都绿 |
| `test_record_with_overlay_build_failure_block_validates` | "must pass through extra='allow' without crashing" | 只 `assertIsNotNone(ev.decision_quality)` | 同上；且喂的 `{"reason": "exception"}` **没有任何生产者会写** |
| `test_record_with_full_overlay_dicts_validates` | "Normal record with all 4 overlays" | 4 个 `assertIsNotNone` | 且用的是**现实中不存在的形状**：`market_quality={"wide_spread":…,"thin_market":…}`（真名是 `wide_spread_flag`/`thin_market_flag`）、`source_reliability={"level":…,"score":…,"factors":{}}`（真名是 `overall_score`，且没有 `level`/`factors`）、`decision_quality={"reasons":[],"downgrade_count":0}`（不是声明的任何字段） |

- 🔴 **这是 §4.2(c) 的教科书样本**："断言负向或过弱 ⇒ 对『分支不存在/不可达』天然盲"。这 3 条的注释就是它们唯一的"证据"，而**注释不是判据**。
- 🔴 更贵的一层：**"钉住了"这个说法本身不可信**。若只看用例名与注释，会得出"allow 被三个用例保护着，不能动"。实际这三个用例对 allow **完全免疫**。⇒ **凡"某行为被测试保护"的判断，都要回读断言行，而不是读用例名。**
- ✅ 处置：按 §0「前提被改的结论必须显式更正」，三个用例**就地改成钉新契约**，且都补上**正向**断言：
  - `test_ignores_keys_it_does_not_declare`：`assertEqual(cv.model, "x")` **且** `assertNotIn("extra_field", cv.model_dump())` —— 两半合取，重新 allow 就会红。
  - 两个 overlay 用例：把**编造的** dict 换成**真生产者**的形状（`build_market_quality` 的返回、`source_reliability` / `llm_telemetry` / `decision_quality` 的回退块），断言**具体取值**（`score == 0.82`、`suggested_direction == "YES"`、`error == "build_failed"` …）而不只是非 None。
- ⚠️ 旁证：`test_diagnose_event_quality.py:92` 也有一句 `extra="allow"` 的注释，但它说的是 **`EventRecord` 顶层**（`market_quote` 等确实仍未声明）⇒ 那句话**仍然成立，不动**。**同一句话在另一个主语上是对的** —— 更正要按主语切，不能整段扫。

### 53.51.4 改动面：四处耦合 + 一个守卫

| # | 文件 | 改了什么 |
|---|---|---|
| 1 | `backend/app/models/event.py` | 7 个模型删掉 `model_config = ConfigDict(extra="allow")`（连它前后的空行），并在各自 docstring 里写明**为什么现在可以闭**（生产者是谁、写了哪些键）。剩下的 3 个 `ConfigDict(extra="allow")` = `EventSource` / `EventRecord` / `FlexibleResponse` |
| 2 | `backend/app/models/_frontend_export.py` | 7 个加进 import + `__all__`（按 ASCII 序），**23 → 41 → 48 名**；重写模块说明：把"9 个模型故意开放"改成"只剩 `FlexibleResponse` 家族 + `EventRecord`"，并写清 `EventSource` **为何不在收缩名单里** |
| 3 | `backend/scripts/generate_types.py`（跑一次） | `generated-types.ts` 尾巴 **25 → 18**（残留 18 = 16 个 `FlexibleResponse` 子类 + `EventRecord` + `EventSource`） |
| 4 | `backend/tests/test_event_record_overlay_typing.py` | 模块 docstring + 3 个用例（见 53.51.3） |

⚠️ **被牵连的守卫与变异（§4.3：「diff 触及任何 `m.path` 就必跑」）**：本轮 diff 触及 `_frontend_export.py`（C2 与 D1 的 `m.path`）与 `event.py`（新增 D2/D3 的 `m.path`）⇒ 一并重跑 `market-snapshot-contract`（C1/C2）与 `frontend-type-closure`（D1/D2/D3），全部四阶段通过。

**新增守卫**（`test_generate_types.py`，放进**不需要 json2ts** 的 `AllowlistCompositionTests`，所以在 CI 后端作业里也跑）：

- 模块级常量 `DELIBERATELY_OPEN = frozenset({"EventRecord", "EventSource"})` —— 把"哪些必须开放"**写成拍过板的事实**，并注明"往这里加名字是契约决策，不是清理"。
- `test_only_the_documented_models_stay_open` —— 从根出发沿字段注解取**可达**模型，筛出**自身** `model_config["extra"] == "allow"` 的（按 `issubclass(…, FlexibleResponse)` 排除那一族），断言集合**恰好等于** `DELIBERATELY_OPEN`。
  - 🔑 **为什么必须"集合相等"而不是"禁止新增"**：禁令只挡一个方向。本节的失败模式恰恰是**另一个方向** —— 一口气收七个时**顺手把 `EventSource` 也扫进去**（`tsc` 依旧绿，因为前端只是看不见 adapter 真发的键）。集合相等两个方向都接得住。
- 同时把 CLI 侧那个 `test_nested_models_included` **改掉**：它的前提（"这 5 个是嵌套的"）在本节之后**不成立**了（它们已是根），而它的内容（断言名字出现在产物里）被 `test_generates_file_with_all_allowlist_models`（遍历 `__all__`）完全覆盖。⇒ 换成 `test_closed_overlay_interfaces_have_no_tail`：按 `}` 在行首切出每个 interface 的体内行，断言 7 个**没有** `  [k: string]: unknown;` 这一行，并断言 `EventSource` **有**（方向相反的一条）。这是**产物级**证据，与模块级那条互补 —— 因为尾巴的成因在**生成器**（`_schema_generation_overrides` 对显式 allow 跳过覆盖），只看 `model_config` 是看不到那一半的。

### 53.51.5 变异：一条两向规则，两条相反改写（D2 / D3）

往同族 set `frontend-type-closure` 追加两条（§4.3："新变异优先追加进同族 set"）：

- **D2** `event.py`：在 `EvidenceItem` 第一个字段前**插回** `model_config = ConfigDict(extra="allow")` ⇒ 已闭合的 interface 重新长出尾巴。针取单行字段行 `    kind: str = "news"          # official | news`（`read_bytes().count()` 实测 **1** 次）。
  - ⚠️ 为什么不用 `    model_config = ConfigDict(extra="allow")` 本身：它在 `event.py` 里出现 **10 次**（改前）/ **3 次**（改后）⇒ **单行针不能用**。
  - ⚠️ `new` 里含换行：交给 `rewrite_needle`，它按**文件**行尾重拼（`test_a_single_line_needle_falls_back_to_the_files_convention` 钉的正是这条），所以不会把 CRLF 文件写成混用。
- **D3** `event.py`：**删掉** `EventSource` 的 allow 声明（反向）。针必须**跨行** —— 靠它前面那句 docstring 尾行才唯一；`count_needle` 在 LF / CRLF 两种拼写下各命中 1 次。
- 两条**共用** `test_only_the_documented_models_stay_open`，且**不设 group**：`group` 的语义是"同一段字节的两条相反改写"（`apply` 会跳过同组后续成员），而 D2/D3 的针在**不同行**、可以同时施加，所以它们只是"同一守卫的两个方向"，不是同一段字节。`test_grouped_mutations_rewrite_the_same_bytes` 因此不受影响。
- ⚠️ 标签改过一次：初版用 `E1`/`E2`，与既有 set `eol-audit` 的 `E1–E5` 撞号（`list` 输出暴露）。虽然 `--index` 是**按 set** 解析的、跨套撞号不影响执行，但**审计留痕会歧义**（"E1 变成红了"指向两个不同东西）⇒ 改为同套的 `D2`/`D3`。**教训：新标签落地前先 `list` 一次，看有没有撞号。**

### 53.51.6 门 + 全量

**四道门**（改动后的工作树上）：

- `ruff check app/`：`All checks passed!`
- `mypy app/`：`Success: no issues found in 331 source files`
- `python -m scripts.generate_types --check`：`generated-types.ts is up to date.`
- 前端：`tsc --noEmit` **rc=0**（**先判**：闭合 7 个 interface 之前，先读过它们的全部前端消费者 —— 只有 `confidence-breakdown-panel.tsx`（用 `Required<Pick<…>>` 挑 9 个已声明字段）、`evidence-list.tsx` / `evidence-list.test.tsx`、`signal-summary.tsx`（读 `.divergence` / `.model`）三个；`decision-card.tsx` 走的是 `lib/api.ts` **手写**的 `DecisionQualityOverlay` / `MarketQualityOverlay` / `SourceReliabilityOverlay`，与生成类型无关；`DecisionTimelineSnapshot` 的 overlay 本就是 inline `{[k:string]:unknown}`，也不受影响）；vitest **125 文件 / 738 用例**全过。

**变异**：`verify frontend-type-closure` → **D1 / D2 / D3 各四阶段全过**（`1 failed, 8 deselected`）；`verify market-snapshot-contract` → **C1 / C2 各四阶段全过**。`tests/test_mutation_verify.py`：**`tests=178 failures=0 errors=0`**（元素 62；`tests` 属性 176 → 178 的 **+2** 来自 `test_the_selected_mutation_is_the_inventory_object` 对**全部变异**做 subTest 的循环，即本轮新增的 2 条变异）。harness `list` 自报 **12 套 / 83 个变异**（原 81）。

**全量**（`pytest -q --junitxml=…`；**判据只看 junit 的 `failures`/`errors`**）：`tests=7756 failures=0 errors=0 skipped=11`（`<testcase>` 元素 `6637`）。

对 §53.50 基线（`tests=7746`、元素 6636）的增量：``tests` **+10**、元素 **+1**。分解：**1 个新元素**（`AllowlistCompositionTests::test_only_the_documented_models_stay_open`）+ **7 次 subTest 迭代**（`test_closed_overlay_interfaces_have_no_tail` 按 7 个 interface 各一次）+ **2 次 subTest 迭代**（变异清单 81→83 —— `test_the_selected_mutation_is_the_inventory_object` 对**全部变异**做 subTest）。两个**改名**（`test_nested_models_included` → `test_closed_overlay_interfaces_have_no_tail`；overlay 回归里那三条的改名）同类内一进一出 ⇒ 元素净 0。**交叉验证**：`tests − elements` = **1119**，扣掉三个循环（**83 + 12 + 7**）后**未解释余数仍是 1017** ⇒ 增长的 10 全部来自这三处。`。

（读数为工具自打印：`mutation_verify list` 报 **12 套 / 83 变异**；尾巴行数用"接口体内最后一个非空行恰好是 `  [k: string]: unknown;`"这一严格判据在**字节**上数出 —— 25 → 18，且残留 18 个能被逐个点名。）

### 53.51.7 留痕 / 未决

1. **`EventSource` 保持 allow，这不是欠债**：9 个 adapter 真的加键；它的 docstring 是 8 个里唯一举证的，与本节的判据一致。**将来若某个 adapter 增键，仍应走这条路 → 改 docstring/模型，而不是"顺手关掉"。**
2. **`EventRecord` 保持 allow**：docstring 举证前向兼容 + `normalize_event_record()` 的 schema 升级；`frontend/src/lib/types.ts` 也**自己声明** `conclusion_challenge`（它写在 record dict 上而模型未声明）。⇒ 两条独立理由，都不属于"没人管"。
3. **`lib/api.ts` 的三个手写 overlay 类型可退休**（`DecisionQualityOverlay` / `MarketQualityOverlay` / `SourceReliabilityOverlay`，各带 `[key: string]: unknown`）。它们是 §2「前端手写 union 常是后端契约缺口之症状」的同族，而`DecisionReport` 现在完全可以改用**已闭合**的生成类型。**未做** —— 那是前端契约取舍，且 `decision-card.tsx` 的消费面要一起看，另行拍板。
4. §53.50.7 第 2 条**已消解**（本节即是它的处置）；第 1 条（(c) 类 inline 尾巴）与第 3、4 条（`LatestLink` / 未推送）**不变**。
5. 本节**未推送**。

## §53.52 同一条规则的消费侧：`api.ts` 不再自己声明 overlay 副本（收 3 个手写类型）

> **前提更正（§0）**：§53.51.7 第 3 条登记过「`lib/api.ts` 里三个**手写** overlay 类型现在可以退休」，当时**只做只读取证、没碰任何文件**。本节把它做掉，并给出「为什么**不必**同时动后端」的实测依据。
> 本节的**行数**一律取 `git diff --numstat`，**计数**取工具自打印（`mutation_verify list`、`junit_summary.py`、字节探针），**不口算、不倒推**；行号是**读出来的**，不推。

### 53.52.1 判据：谁在真的吃这三份手写副本

| 手写类型（`frontend/src/lib/api.ts`，**改动前**行号） | 字段 | 尾巴 | 生成对应物（`generated-types.ts`） | 字段 |
| --- | --- | --- | --- | --- |
| `DecisionQualityOverlay`（:268） | 3 | `[key: string]: unknown` | `DecisionQuality`（:223） | 11 |
| `MarketQualityOverlay`（:275） | 2 | 同上 | `MarketQuality`（:559） | 12 |
| `SourceReliabilityOverlay`（:281） | 2 | 同上 | `SourceReliability`（:604） | 13 |

- **消费者实测**：`grep -rn "DecisionQualityOverlay\|MarketQualityOverlay\|SourceReliabilityOverlay" frontend/src/` ⇒ 命中**全部落在 `lib/api.ts` 自己身上**（定义 3 处 + `DecisionReport` 引用 3 处，均在**改动前**的 :268 / :275 / :281 与 :326 / :327 / :328）。⇒ 技术上就是「只为给 `DecisionReport` 三个字段贴标签而存在」。
- **生成类型是严格超集**：三个手写的字段名（`displayed_direction` / `downgrade_reason` / `decision_rationale_zh` / `score` / `overall_score`）**逐一**被生成类型覆盖，且生成类型还多出 `conflict_score` / `consensus_level` / `wide_spread_flag` / `source_breakdown` 等。
- 🔑 **同一文件里已经有先例**：`generated-types.ts:385-387` 的 `EventRecord` 就是
  `decision_quality?: DecisionQuality | null; market_quality?: MarketQuality | null; source_reliability?: SourceReliability | null;`
  ⇒ `DecisionReport` 是**仓里唯一**还在手写这三个形状的地方。而 `frontend/src/lib/types.ts:8-9` 的抬头写得很直白：*"DO NOT hand-write fields that exist in the Pydantic models."* 本节做的是**消除这个例外**，不是引入新约定。
- **前端真正消费的字段只有 4 个**，全在 `components/decisions/decision-card.tsx`：`decision_quality.decision_rationale_zh`（:170/:173）、`decision_quality.downgrade_reason`（:176/:178）、`market_quality.downgrade_reason`（:181/:183）、`source_reliability.downgrade_reason`（:186/:188）。其余 6 个消费 `DecisionReport` 的文件（`app/decisions/page.tsx`(+test)、`components/detail/decision-report-panel.tsx`(+test)）只读非 overlay 字段，且**两个测试夹具根本不构造 overlay 字段**。
- **零运行时效果**：`api.ts` 的 `api<T>` 助手是 `const data = await res.json() as T`（:211，**盲转**），两个调用点都是 `api<DecisionReport>(…)`；没有任何地方读未声明键。⇒ 换类型是**纯编译期收紧**。

### 53.52.2 两个端点其实都带 `response_model`，但都给不出报告的契约

- `events.py:1455` `/decisions/open` → `response_model=OpenDecisionsResponse`（**具名**）；然而 `models/event.py:805` 是 `decisions: list[dict[str, Any]]` ⇒ **列表元素仍然没有契约**。
- `events.py:1541` `/{event_id}/decision` → `response_model=FlexibleResponse`（**裸**）⇒ 整个响应没有字段契约。
- 但这不是「忘了加模型」：`build_decision_report`（`decision_report_service.py:41`）签名就是 `-> dict[str, Any]`，overlay 三键是 **`record.get("decision_quality")` 原样透传**（:127 / :135 / :141）的 dict ⇒ **报告形状本身没有对应的 Pydantic 模型**。
- 兄弟守卫 `test_operational_readiness.test_event_routes_declare_response_models` 只查 `route.response_model is None`（不许**缺席**）⇒ 裸 `FlexibleResponse` 是合规的，与 §2 已记的「裸 `FlexibleResponse` 是既定策略、不是欠债」一致。
- 仓内**既定模式**也支持这个读法：`OpenDecisionsResponse.decisions` / `FreshEdgesResponse.edges` / `SimilarEventsResponse.similar` 三个都是「**具名信封 + `list[dict[str, Any]]` 内芯**」⇒「内芯保持 dict」是拍过板的形态。
- ⇒ **本节只做消费侧**；补具名 `response_model` 的收益与代价写在 53.52.7 第 1 条，留待单独拍板。

### 53.52.3 改动面（1 个文件）

`frontend/src/lib/api.ts`（`git diff --numstat`：**+17 / −23**，net **−6**）：

1. 从 `./generated-types` **增导入** `DecisionQuality` / `MarketQuality` / `SourceReliability`（不加别名 —— 生成名就是报告要显示的名）。
2. **删掉**三个 `export interface *Overlay`，位置换成一段说明：报告本身没有模型所以继续手写，但三个 overlay 此后读生成物。
3. `DecisionReport` 的三个字段改指生成名（`DecisionQualityOverlay` → `DecisionQuality` 等）。

**字节画像（探针实测，非推算）**：`41026 → 41269` B；`crlf 1254 → 1248`（**−6 == net 行数**，说明插入行全部按 CRLF 落）；`bare_lf` 恒 **0**（**没有混用**）；`export interface` **45 → 42**（−3）；带尾巴的 interface **3 → 0**；inline 索引签名 **0 → 0**。

### 53.52.4 守卫：`api.ts` 不得自己声明副本

`backend/tests/test_generate_types.py` 新增 `ApiTsTypeSourceTests::test_decision_report_reads_the_generated_overlays`，两层、方向相反：

1. `_overlay_copies(bodies)` 的集合**恰好等于** `ALLOWED_OVERLAY_COPIES_IN_API_TS`（**空集**）。算作「副本」的两个分支：**名字以 `Overlay` 结尾**，或**体内任一处出现索引签名**（两种拼写都认：生成器写 `[k: string]`，手写文件写 `[key: string]`）。
2. `DecisionReport` 的三个字段**逐条**断言等于 `  <field>?: <Generated> | null;`。

为什么是**两层**：「副本没回来」与「字段仍读生成类型」是**两件事**，D5 存在的意义就是不让一个绿结果把两者混为一谈。
为什么索引签名要扫**体内任意位置**而不是**末行**：初版只看「体内最后一个非空行」，D6（插在**中段**）**不会红** —— 所以 D6 同时是这条判据选择的证据。
为什么**只扫 interface 体内、不做整文件 grep**：⚠️ `api.ts` 的注释里**逐字写了** `[key: string]: unknown`（正是在描述被退休的那三份副本）。**注释也是文件字节** ⇒ 整文件搜会踩到自己的文档（同 §4.3「自指变异」那一族）。而**体内**才是索引签名真能放松类型的地方。

**被否掉的守卫形态（留痕）**：更早想用「`api.ts` 不得与 `generated-types.ts` 声明同名 interface」。实测 **COLLISIONS = 0** —— 手写名带 `Overlay` 后缀，从来就没撞过车 ⇒ 那条守卫**改动前后都不变红**，对本次改动**不敏感**。按 §4.2「守卫必须对该改动敏感」，它不能充当本节的守卫。

### 53.52.5 变异：一个守卫、三个互不重叠的判据（D4 / D5 / D6）

往同族 set `frontend-type-closure` 追加三条（§4.3：「新变异优先追加进同族 set」）：

- **D4** `frontend/src/lib/api.ts`：把 `SourceReliabilityOverlay` 那份**手写副本还原回去**（放在 `DecisionReport` **之前** ⇒ TS 语法仍合法；守卫是文本判据，不看语法）。踩「名字以 `Overlay` 结尾」分支。针 `export interface DecisionReport {`，`count_needle` 实测 **1** 次、单行 ⇒ 与检出约定无关。
- **D5** 同文件：把 `source_reliability` 字段改指 `Record<string, unknown>`。名字不以 `Overlay` 结尾、体内也没有索引签名 ⇒ 前两个分支**都通过**，只有**字段指向**那半会红。没有它，守卫的第一半就可能是**空头**。
- **D6** 同文件：把 `[key: string]: unknown;` 插进 `DecisionReport` 的**中段**（**不是末行**）。⇒ 只有索引分支会红，同时证明「体内任意位置」这条判据是必要的。
- 三条**共用**同一守卫、**不设 group**：`group` 的语义是「同一段字节的两条相反改写」（`apply` 会跳过同组后续成员），而这三条的针在**不同行**、可以同时施加，所以它们只是「同一守卫的三个判据」，不是同一段字节。`test_grouped_mutations_rewrite_the_same_bytes` 因此不受影响。
- `mutation_verify list` 自报 **12 套 / 86 个变异**（原 **83**）。标签 `D1–D6` 全落在本 set 内，与既有 set `eol-audit` 的 `E1–E5` **无撞号**（§53.51 踩过一次跨套撞号，故先 `list` 核对）。

### 53.52.6 门 + 全量

**四道门 + 前端两道**（改动后的工作树上）：

- `ruff check app/`：`All checks passed!`；改动过的 `scripts/mutation_verify.py` 与 `tests/test_generate_types.py` 亦 `All checks passed!`
- `mypy app/`：`Success: no issues found in 331 source files`
- `python -m scripts.generate_types --check`：`generated-types.ts is up to date.`（本节**没有**动后端模型与白名单 ⇒ 产物本就不该变）
- 前端 `tsc --noEmit`：**rc=0**，全文 **0** 处 `error TS`
- 前端 `eslint`：干净（无输出）
- 前端 vitest：**125 文件 / 738 用例**全过

**变异**：`verify frontend-type-closure` → **D1–D6 六条各四阶段全过**（`green before True | red after mutation 1/1 files red | green after restore True | bytes restored True`）。
`tests/test_mutation_verify.py`：**`tests=181 failures=0 errors=0`**（`<testcase>` 元素 **62**；`tests` 属性 178 → 181 的 **+3** 来自 `test_the_selected_mutation_is_the_inventory_object` 对**全部变异**做 subTest 的循环，即本轮新增的 3 条）。

**全量**（`pytest -q --junitxml=…`；**判据只看 junit 的 `failures`/`errors`**）：`tests=7763 failures=0 errors=0 skipped=11`（元素 **6638**）。
对 §53.51 基线（`tests=7756` / 元素 **6637**）的增量：**`tests` +7、元素 +1**。
分解（按工具读数，不口算）：**+1 个元素** = 新增守卫 `test_decision_report_reads_the_generated_overlays`；**+6 次 subTest 迭代** = 该守卫的 3 个字段 `subTest`（3）+ `test_the_selected_mutation_is_the_inventory_object` 对**全部变异**做 subTest 的循环（变异 83 → 86，+3）。三者相加即 `tests` 的 +7 与元素的 +1。
（`rc=1`：日志末尾是 `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":1888,…,"targets":["…\\Temp\\pytest-of-Alin\\garbage-…"]}` —— 就是那个 shim 拦临时目录清理，**与测试结果无关**。）

### 53.52.7 留痕 / 未决

1. **为什么不顺手补具名 `response_model`（B 项的代价，写在这里备查）**：一旦给 `/{event_id}/decision` 声明 `DecisionReportResponse` 并把 `OpenDecisionsResponse.decisions` 改成 `list[DecisionReportResponse]`，FastAPI 就会**校验并重序列化** —— 任何字段类型漂移会把 200 变成 500；而 overlay 是 `record.get()` **透传的原始 dict**，收进已闭合模型还会**静默丢掉未声明键**。现在两个端点各自合规（一个具名信封、一个是既定策略），报告形状本身**没有模型**，补它等于**新增契约**而不是修正缺口 ⇒ **需要单独拍板**，不在本节范围内。
2. ⚠️ **pytest 对 `subTest` 失败的报法**：失败会被单列成 `SUBFAILED(field='source_reliability')`，而**父用例仍报 `1 passed`**（`rc != 0`，所以 harness 的 `passed` 判据不受影响）。⇒ 读含 subTest 的守卫时**不能只看 `N passed`**；这也是 junit 的 `tests` 属性大于元素数的原因之一。
3. ⚠️ **手动 `apply` 之前必须先 `backup`**：本节手动复现 D5 时先 `apply` 后 `revert` ⇒ `revert` 报 `no backup for backend/app/models/_frontend_export.py; run 'backup frontend-type-closure' first`，工作树**留在变异态**（`git status` 立刻显示 `M frontend/src/lib/api.ts`）。**还原判据**：字节必须回到 `41269 / crlf 1248 / bare_lf 0`，并用探针确认尾巴集合回 **0**（本节即按此复核）。`verify` 子命令自带 backup/restore，不受影响。
4. **未推送**。
5. **遗留**：§53.51.7 第 1/2 条不变；§53.50.7 第 1 条（(c) 类 inline 尾巴）仍**未动**；`/sport-markets/*` 其余裸 dict 端点仍按 §2 既定策略。

### 53.52.8 拍板记录：报告形状**不**补具名 `response_model`（挂账）

**裁决（2026-10-08，本节即裁决书）**：**不做**。53.52.7 第 1 条由此从「需要单独拍板」转为**已裁决的挂账项** —— 将来读到那条时，不要把它当成「还没人管」。

**为什么记这一小节**：裁决与「尚未决」在文档里长得一样，而**结论会静默过期**（§4.1）。所以这里把**前提**一并钉住 —— 复述「不补具名模型」时必须带上它们：

1. **报告形状没有模型**：`build_decision_report`（`decision_report_service.py:41`）签名就是 `-> dict[str, Any]`。
2. **overlay 是透传的原始 dict**：`record.get("decision_quality")` 等（`:127` / `:135` / `:141`），不是 pydantic 对象。
3. **两个端点各自已合规**：`/decisions/open`（`events.py:1455`）是具名信封；`/{event_id}/decision`（`events.py:1541`）的裸 `FlexibleResponse` 是 §2 记过的既定策略，兄弟守卫只查 `response_model is None`。
4. **消费者只有一处**：报告形状目前只被**前端**的 `DecisionReport`（手写）消费（1 处声明 + 2 个 `api<DecisionReport>()` 调用点 + 5 个组件/页面文件），没有任何**非前端**消费者（CLI / 外部 / 另一个服务）。

**任一条前提被改掉，这条挂账就该重开。** 具体触发条件（任一成立）：

- 真给报告建了 pydantic 模型，或 `OpenDecisionsResponse.decisions` 从 `list[dict[str, Any]]` 换成了具名元素类型；
- 出现**第二个**消费者面（CLI、外部集成、另一个后端模块要按契约吃这个 payload）；
- 或者「字段类型漂移把 200 变成 500」**被实测发生过**一次 —— 到那时这个风险就不再是理论的。

**将来若要做的先决条件**（不是"顺手补一下"就完事）：

1. §2 **三件套**（具名 `response_model` → `_frontend_export` 白名单 → 重生成 `generated-types.ts`），并让 `DecisionReport` 整个退休吃生成类型；
2. **逐字段的类型稳定性取证** —— 不能只读 `build_decision_report` 的签名，要把 `prediction.get(...)` / `record.get(...)` 的**所有生产者**读一遍（同 §53.51 的做法），否则 `response_model` 会**校验并重序列化**，任何一处类型漂移直接 200 → 500；
3. overlay 三字段若引用**已闭合**的模型（§53.51），还要确认透传 dict 里没有未声明键 —— 否则会在 API 边界上**静默丢键**（这比 500 更难发现）；
4. 新增一条守卫 + 对应变异（同族 set `frontend-type-closure`），并在 53.52.4 记下「为什么它对本改动敏感」。

本节随 §53.52 **同批未推送**。

## §53.53 (c) 类 inline 尾巴试点：`GET /events/{id}/history` 的三件套 + 消费侧退役

本节处置 **§53.50.7 第 1 条** 里 C1 族的**一个端点**。选它作试点的理由（§53.53 侦察记已记）：**一个调用点盖 3 个字段**，且路由 `events.py:1246` **已有**具名 `response_model=EventHistoryResponse` —— 也就是说这处缺的不是「合约没发布」，而是**发布出去的合约里三个字段是 inline `{ [k: string]: unknown }`**，前端于是自己手写了三份副本。

### 53.53.1 判据：三种成因要分清，判「尾巴」要按缩进

`generated-types.ts` 里出现 `{ [k: string]: unknown }` 有三种**互不相同**的成因：

| 成因 | 机制 | 是否要改 |
| --- | --- | --- |
| (a) `extra="allow"` | 生成器**刻意跳过** `extra="forbid"` 覆盖，开发者显式声明开放 | **不改**（开放是拍过板的） |
| (b) 纯嵌套 `BaseModel` | 只被 `$ref` 引用 ⇒ 拿不到 `additionalProperties: false` ⇒ `json2ts` 补尾巴 | 登记为根即可闭合 |
| (c) 字段是裸 `dict[str, Any]` | 渲染成 **inline** `{ [k: string]: unknown }`，在字段**自己的花括号内**（缩进 ≥4） | 给它一个**具名模型** |

**本端点是 (c)**。判「尾巴」的严格判据是**体内最后一个非空行恰为 `  [k: string]: unknown;`**（缩进恰 2 格）；缩进盲搜会把 (c) 的 inline 行一起算进来，所以本次的读数一律按**体内 inline 尾巴行数**报，不报「接口里有没有尾巴」。

### 53.53.2 决定性取证：磁盘形状只有一种

`history` 字段是**原样透传的磁盘 JSONL 行**，所以「能不能建模」不是设计偏好，而是**磁盘形状问题**。新增只读探针 `probe_history_shape.py`（`.workbuddy/audit-2026-10-03/`），对全部 5 份 `event_audit.jsonl` 逐行普查（工作树 1 份 + `backend/backups/` 3 份 + `.workbuddy` 1 份；其中 2 份同源）：

| 语料 | 行 | 不可解析 | `kind="outcome"`（路由过滤） | 概率快照 |
| --- | --- | --- | --- | --- |
| `backend/event_audit.jsonl` | 526 | 0 | 68 | 458 |
| `backups/event-id-migration-20260622-040713/` | 307 | 0 | 1 | 306 |
| `backups/manifold-lowercase-purge-20260709-150947/` | 781 | 0 | 15 | 766 |
| `backups/manifold-purge-20260709-150402/` | 1059 | 0 | 27 | 1032 |

**四代语料、2562 条概率快照，key set 只有一种**（各语料内部 100%）：

```
['baseline','change','credibility_score','direction','estimated',
 'event_id','event_title','impact_score','source_type','timestamp','value_score']
```

类型直方图（`backend/event_audit.jsonl`，458/458；其余语料同）：

| 字段 | 读数 |
| --- | --- |
| `timestamp` / `event_id` / `event_title` / `direction` / `source_type` | **str 100%** |
| `baseline` / `estimated` / `change` | **float 100%**（其中 `baseline` 有 245/458 是 `x.0` 的整数值 float） |
| `credibility_score` / `impact_score` / `value_score` | **int 100%** |

**非数值样本 0 例。** ⇒ 模型可以**照抄磁盘形状**，没有推断成分。

**逐字段「必写 / 可能缺」**（读 `event_audit_service.record_event`，不是读语料）：`record_event` 无条件写**全部 11 键**，缺值写 `None`（不是省键）。其中 `event_id` 的「必在」是**推导**出来的，不是观察出来的：路由经 `histories_by_event()` → `_group_records()` **丢弃 `event_id` 为假值的行**，再以 `event_id` 为分组键、按 **str** 查表 ⇒ 能被本路由取到的行，其 `event_id` **必然是非空 str**。所以它不给默认值是**有据的**，不是省事。

`trend` / `edge` 是**纯函数产物**（`analyze_trend` / `analyze_edge_trajectory`），且两条分支形状一致：`_empty_trend` 与正常返回**同为 14 键**，`_empty_edge` 与正常返回**同为 12 键** ⇒ 不会因走回退而校验失败。**`first_seen` / `last_seen` 声明为 `str | None` 而非 `datetime`**：它们取的是审计行自己的 `timestamp` 值（本就是 ISO 串），声明成 `datetime` 只会让 pydantic 把它**重新序列化一次**，没有收益。这与 `MarketSnapshotPoint.captured_at: datetime | None` **不同**，两者不要互推。

### 53.53.3 唯一一条真实的行为变化（本节点已经拍板）

绑 `response_model` **会校验并重新序列化**，所以两个方向都量了：

- **静默丢键**：11 键之外的键会被闭合模型丢掉。已观测语料**零丢失**（key set 唯一 + `record_event` 无条件写全 11 键）。残余风险是「`record` 未经 `EventRecord` 校验的裸 dict 带别的键」，**未观测到**。
- **200 → 500 的类型漂移**：`estimated` 若是**非数值字符串**（如 `"bad"`），今天是**原样透传 200**；声明 `float | None` 后 → `ResponseValidationError` → **500**。证据：**2562/2562 全是 float，0 例**。
  📌 前端**确实**为这种情况写了防御（`adapt.test.ts:196` 的夹具含 `{ estimated: "bad" }`，`sparkSeries` 用 `Number.isFinite` 过滤）—— 但那是**合成夹具、不是磁盘证据**。这条恰好是「"跑通了"不等于"对"」的一个具体形状。

**`int` vs `float` 的线格式漂移**同样量过：三个 score 声明 `int` ⇒ `26` 仍是 `26`，**零漂移**；若声明 `float` ⇒ **每条**变成 `26.0`（458/458 条线全变），为无收益的改动。`baseline`/`estimated`/`change` 语料里本就是 Python float（`45.0` 序列化成 `45.0`）⇒ 声明 `float` **零漂移**。

**拍板结果（2026-10-08）**：三问全取 A —— ① `history` 行**严格** `float | None`；② 前端三个手写类型**同批退役**并让调用点改吃生成信封；③ `EventHistoryResponse` 五个字段**按生产者收紧成 required**。③ 的安全性由构造保证：路由无快照就 404，有则返回**含全部 5 键**的 dict 字面量；留着默认值只会让生成的 TS 渲染成 `trend?:`，比端点真正承诺的更松。

### 53.53.4 改动面（8 个文件）

**后端**（`git diff --numstat`）：

| 文件 | 改动 |
| --- | --- |
| `backend/app/models/event.py` | 新增 `EventAuditSnapshot` / `TrendSummary` / `EdgeTrajectorySummary` 三个模型；`EventHistoryResponse` 五字段改为 required 且指向它们 |
| `backend/app/models/_frontend_export.py` | 三个新模型进 import + `__all__`（**必须**：`pydantic2ts` 按**模块命名空间**收根，只被 `$ref` 引用不算） |
| `backend/tests/test_generate_types.py` | 闭合清单 +3 名；新增 `EventHistoryContractTests` 两个用例 |
| `backend/scripts/mutation_verify.py` | `frontend-type-closure` 追加 J1–J4；新增 `_TYPES_TS` 路径常量 |

**前端**：`generated-types.ts`（重生成）、`api.ts`、`types.ts`、`adapt.ts`。

**字节画像**（探针 `probe_5353_profile.py`；「改前」由 HEAD blob 按检出约定反推 —— 本仓 `core.autocrlf=true` + `* text=auto`，`git show` 返回的是**归一化后的 LF**，不是磁盘字节。反推自校验：`api.ts` 反推出 **41269**，与 §53.52 记的磁盘读数**逐字节相同**）：

| 文件 | bytes | crlf | bare_lf | interface | 体内 inline 尾巴行 |
| --- | --- | --- | --- | --- | --- |
| `generated-types.ts` | 28734 → **33142** | 844 → 940 | 0 → 0 | 49 → **52** | `EventHistoryResponse` **4 → 1** |
| `api.ts` | 41269 → **41568** | 1248 → 1244 | 0 → 0 | 42 → **41** | 0 → 0 |
| `types.ts` | 3117 → **3480** | 95 → 89 | 0 → 0 | 6 → **4** | `TrackedEntry` 1 → 1 |
| `adapt.ts` | 5582 → **6336** | 165 → 175 | 0 → 0 | 1 → 1 | 0 → 0 |

`generated-types.ts` 的 `EventHistoryResponse` 仍**保留 1 条尾巴** —— 它承 `FlexibleResponse`（`extra="allow"`），那是 §2 记过的**既定策略**；被消掉的是它体内**三个字段**的 inline 尾巴（4 → 1）。其余 20 个带尾巴的接口**逐个不变**（探针逐名对比：`tail-line count changes` 里除 `EventHistoryResponse` 外全是 `(n, n)`）。新增接口名恰为三个。

`api.ts` 的 crlf 减小 4 与 `--numstat` 的净行数（+13/−17 = −4）**逐一对上**，`bare_lf` 全程 0（即改动没有把任何 CRLF 文件写混）。

**`TypeScript` 侧一处被改动暴露的旧空头**：`adapt.ts` 的 `adaptMover` 写的是 `const t = m.trend ?? {}`，而当时 `Trend` 是**全可选**的手写接口 ⇒ `{}` 可赋给 `Trend` ⇒ 联合类型**折叠成 `Trend`**，那个 `?? {}` 兜底是个**空操作**，底下 4 处「防御性」读取从来不是真的防御。换成字段必填的生成类型后 `tsc` 立刻报 4 条 `TS2339`。修法是把局部变量显式放宽成 `Partial<TrendSummary>`（并注明**不能写 `Partial<Trend>`** —— `adapt.ts` 自己还有第三个 `Trend`，是 `"up" | "down" | "flat"` 联合，会遮蔽）。这属于 §4.2「看到弱正向断言先假定它对那个分支空头」的同族，只不过这次空头在**类型层**而不是断言层。

### 53.53.5 守卫：两层五判据

| 判据 | 落在哪 |
| --- | --- |
| ① 三个新模型必须是**根**（否则退回嵌套、变松） | 既有 `test_every_reachable_plain_model_is_registered`（生产者驱动，**不必重新生成**就红；⚠️ 它「承重」这句话的取证前提本轮被更正过，见 **§53.53.9**） |
| ② 开放集**集合相等** | 既有 `test_only_the_documented_models_stay_open` |
| ③ 三个新接口渲染**闭合** | `test_closed_overlay_interfaces_have_no_tail` 清单 +3 名（读生成字节） |
| ④ 信封三字段必须**指名**模型（身份，非 `issubclass`） | 新增 `test_event_history_response_points_at_the_published_models` |
| ⑤ `api.ts` / `types.ts` 不得再声明六个名字里的任何一个，且三行别名 + 调用点必须读 `EventHistoryResponse` | 新增 `test_history_endpoint_reads_the_generated_envelope` |

**④ 为什么必须是身份判据**：回归成 `dict[str, Any]` 是**唯一的静默路径** —— `FlexibleResponse` 照样校验、返回字节一模一样、`tsc` 依旧全绿，只有生成的 `trend` 会退回 inline。`issubclass`、读生成文件都抓不到（后者还要先重新生成，而变异跑在**未重新生成**的树上）。

**⑤ 为什么必须按名字判**：既有的 `ALLOWED_OVERLAY_COPIES_IN_API_TS` 判据是「名字以 `Overlay` 结尾」**或**「体内有索引签名」。一份去掉三个字段的 `EdgeTrajectory` 副本**两条都不占** ⇒ 老守卫对它**天然盲**。「更普适」的判据在这里等于「对本改动不敏感」，与 §53.52 那条 COLLISIONS=0 的教训同源。

**⑤ 只扫 interface 体内、不整文件 grep**：`types.ts` 与 `api.ts` 的注释里都**逐字出现**被退休的名字（描述历史用的）。`_interface_bodies` 只认以 `export interface ` **开头**的行，所以注释天然出局 —— 这是 §53.52 自指陷阱那条教训的直接复用。

**发布形状的直证**（`EventHistoryResponse.model_json_schema()`，不经 pytest、不经生成器）：

```
required: ['event_id', 'count', 'trend', 'edge', 'history']
  trend:   {"$ref": "#/$defs/TrendSummary"}
  edge:    {"$ref": "#/$defs/EdgeTrajectorySummary"}
  history: {"items": {"$ref": "#/$defs/EventAuditSnapshot"}, "type": "array"}
```

`EventAuditSnapshot` 11 属性 / `TrendSummary` 14 / `EdgeTrajectorySummary` 12，**三者 required 都列全了**（`X | None` 不给默认值的直接后果）。⚠️ **这两个 schema 上都没有 `additionalProperties`，而生成的 TS 是闭合的** —— 这不是矛盾：闭合来自生成器在**生成期**临时对**根模型**置 `extra="forbid"`（`_schema_generation_overrides`），模型运行时仍是普通 `BaseModel`（`model_config == {}`，即 `extra="ignore"`）。将来的读者看到「schema 里没 `additionalProperties: false`」时别据此判断接口是开放的。

### 53.53.6 变异：J1–J4，四条各踩一条互不重叠的判据，分布在三个文件

| 变异 | 打到哪 | 踩哪条 |
| --- | --- | --- |
| **J1** | `api.ts`：别名退回手写 `EdgeTrajectory`（去掉三个字段） | ⑤ 的**负向**半（`api.ts`） |
| **J2** | `api.ts`：`api<EventHistoryResponse>` → `api<{ history: unknown[] }>` | ⑤ 的**正向**半（调用点） |
| **J3** | `types.ts`：`export type Trend = TrendSummary;` 退回手写接口 | ⑤ 的负向半在**另一个文件** |
| **J4** | `event.py`：`trend: TrendSummary` → `trend: dict[str, Any]` | ④ 模型身份 |

**为什么 J3 与 J1 必须分开**：同一条判据作用在**两个文件**上，只扫 `api.ts` 的守卫会放过 `types.ts` —— 而 `types.ts` 恰恰是**自己写着**「DO NOT hand-write fields that exist in the Pydantic models」却**没有**守卫的那个文件（规则在注释里 ≈ 空头）。

**为什么 J2 不能还原成原内联形状**：那要重新 import `HistorySnapshot` / `Trend`，会顺带引入第二个缺陷，让「红了」分不清踩了哪一条。选 `unknown[]` ⇒ `tsc` 依然绿（`api<…>` 是盲目 cast），隔离干净。

**有意不登记的变异**（§4.2 要求写进 `rationale`，已写）：不再为「把 `EventAuditSnapshot` 从 `_frontend_export` 的 import 里挪走」补一条 —— 它踩的是 D1 已证明承重的**同一条**判据，换个成员再加一条只会让计数虚胖。⚠️ 该结论有前提：① 若那条守卫的判据哪天从「可达即须登记」改成只认具体名单，这条推理即过期；② 它依赖「D1 真的踩在那条判据上」，而这一点**本轮才发现原来不成立** ⇒ 见 §53.53.9。

**确实缺失、登记备查**：`EventHistoryResponse` 五字段 optional → required 的**收紧**没有独立变异。它只间接体现在 ③ 的「三个新模型必须闭合」上；要单独钉可选性需读生成文件写正向断言（`event_id: string;` vs `event_id?: string;`），而那条需要先重新生成，超出本 set 的静态判据口径。**这是本轮已知的守卫缺口，不是遗漏。**

**四阶段读数**（`verify frontend-type-closure`，10 条全过）：

| 变异 | 变异后变红的守卫 | 读数 |
| --- | --- | --- |
| J1 | `test_history_endpoint_reads_the_generated_envelope` | `2 failed, 1 passed, 7 subtests passed` |
| J2 | 同上 | `1 failed, 9 subtests passed` |
| J3 | 同上 | `2 failed, 1 passed, 7 subtests passed` |
| J4 | `test_event_history_response_points_at_the_published_models` | `1 failed, **1 passed**`（= ⑤那条**仍绿**） |

J2 只有 1 条红、J4 的另一个用例仍绿 ⇒ 证明 J2 与 J4 的判据**互相隔离**，不是被同一个红结果混在一起。

**清单**：`12 套 / 90 个变异`（原 86，`frontend-type-closure` 由 6 条增至 10 条）。`test_mutation_verify.py`：`tests=185 failures=0 errors=0`（元素 62；原 181）。

### 53.53.7 门与全量

| 门 | 读数 |
| --- | --- |
| `ruff check app/` | `All checks passed!` |
| `mypy app/` | `Success: no issues found in 331 source files` |
| `python -m scripts.generate_types --check` | `generated-types.ts is up to date.` |
| `tsc --noEmit` | 0 条 `error TS` |
| `eslint .` | 无输出（干净） |
| `vitest run` | **125 文件 / 738 用例全过** |
| 变异 | `12 套 / 90 个变异`；`frontend-type-closure` 10 条**四阶段全过** |
| `test_mutation_verify.py` | `tests=185 failures=0 errors=0`（元素 62） |
| 后端全量 | **`tests=7784 failures=0 errors=0 skipped=11`**（元素 **6640**） |

**全量对账**（对 §53.52 基线 `tests=7763` / 元素 `6638`）：`tests` **+21**、元素 **+2**，逐项可查 ——
两个新 `<testcase>`（+2 元素）、`test_closed_overlay_interfaces_have_no_tail` 的清单 **+3 名**（3 个 subTest）、
新用例的 `subTest` 迭代 **3 + 9 = 12**、`test_mutation_verify.py` 因针由 86 增至 90 而 **+4**。`2 + 3 + 12 + 4 = 21` ✅ 无未解释余量。

⚠️ **顺序披露**：D1/C2 的更正（见 §53.53.9）发生在全量**之后**。该改动只触及 `backend/scripts/mutation_verify.py` ——
一个**不被 app、也不被任何被测模块导入**的脚本。唯一读它的测试 `test_mutation_verify.py` 已**复跑**并全绿（上表读数即复跑所得），
两条被改的变异各自**复跑四阶段全过**；因此**未重跑全量**，且此处不声称全量是在更正后跑的。

### 53.53.8 留痕 / 未决 / 有意不做

- **C1 还剩 10 个信封**：`EventDiscoveryResponse.events`、`EventMoversResponse.movers`、`SimilarEventsResponse.similar`、`FreshEdgesResponse.edges`、`PendingLinksResponse.pending`、`RecentPredictionsResponse.predictions`、`OpenDecisionsResponse.decisions`、`AutoResolveResponse.matches`，加上 `EventListResponse`（已是具名元素类型）。本节点各有一处 `api.ts` 内联调用点。
- **C2（7 个 timeline 字段）** 与 **C3（3 处刻意的无形状）** 未动，见 §53.50.7。
- **死 re-export 少了一个**：`EventHistoryResponse` 的 re-export **从零消费者变成有消费者**（`api.ts:886`）。剩下的三个（`AutoResolveResponse` / `EventListResponse` / `EventMoversResponse`）仍然零消费者。
- 🔴 **`history` 的「严格 vs 宽松」是个真选择，不是清理**：本轮取严格，代价是「非数值 `estimated` 由 200 变 500」。这个代价的**前提**是「磁盘上从未出现过」—— 若哪天在**真实记录**里观测到一次，就该重新评估（而不是当成 bug 去加 `str`）。同族先例：§53.52.8 的挂账写法。
- ⚠️ **`int` 声明的前提**：三个 score 现在是 `int | None`，依据是 `Credibility.score` / `Impact.score` / `EventRecord.value_score` 都声明 `int` **且**语料 458/458 是 int。若生产者哪天改成 float（比如某个 adapter 算术平均），`int` 字段会**拒绝非整数**并 500 —— 那时的修法是重新拍板字段类型，不是加 `float | int`。
- 本节随本轮改动**未推送**。

### 53.53.9 顺带更正：D1 / C2 早就「红」，但红的不是它们声称的那条判据

**怎么发现的**：为了把 §53.53.5 判据①那句「既有且承重」从**推断**变成**测量**，我手工复现了「把 `EventAuditSnapshot` 从 `_frontend_export` 的 import 里挪走」。
用的手法与 D1 当时的写法一致（把名字换掉）。结果**不是可达性断言失败**，而是：

```
ImportError: cannot import name '_EventAuditSnapshotUnused' from 'app.models.event'
```

原因是守卫体里那行 `from app.models import _frontend_export` —— 把 import 列表里的**源名字**改掉，`_frontend_export` 模块本身就导入失败。
**任何**语法错误、任何导入错误都会让这条守卫变红 ⇒ 那个变异的判据**近乎空**：它对「模块能导入但名字从命名空间里消失」这种真实回归**永远不会红**。

**正确写法是「导入别名」**（`EvidenceProfile as _EvidenceProfileUnused`）：源名字仍在 ⇒ `from app.models.event import …` 照常成立、`__all__` 里那串字符串也仍在，**只有命名空间里少了这个名字**。
实测两种写法对比（`probe_5353_d1_mechanism.py`，字节安全、跑完 sha256 逐字节还原）：

| 写法 | 失败文本 |
| --- | --- |
| D1 原文（把名字换掉） | `ImportError: cannot import name '_EvidenceProfileUnused' from 'app.models.event'` |
| D1 更正为（导入别名） | `AssertionError: Lists differ: ['EvidenceProfile'] != []` ← **红在可达性上** |

⇒ **已更正 D1（`frontend-type-closure`）与同族 C2（`market-snapshot-contract`）**：`new` 改成别名写法、`label` 从「不再导入该模型」改成「不再保留该名字」，`note` **显式记下原文与更正理由**（不覆盖式改写历史，而是写明改过什么）。两条各自**复跑四阶段全过**。

🔴 **本节最该带走的一条**：harness 的四阶段只判「变红没有」，**不判「红在哪条断言上」**——
原 D1 与更正后的 D1 在四阶段输出里**一模一样**（都是 `1 failed`）。所以「某条变异承重」这个结论，
**必须另有一次读失败文本的取证**才成立；否则你证明的只是「这条守卫对某种破坏敏感」，而那种破坏可能与本变异要表达的事无关。
（同族：§4.2 的「负控给的是假安全」—— 那次是「不产出」与「产出后被正确过滤」断言结果相同；这次是「模块坏了」与「名字没了」都让同一个守卫红。）

## 53.54 `GET /events/edges/fresh`：把「元素类型」也变成已发布契约

承接 §53.53。§2 的三件套此前只解决**信封**——`FreshEdgesResponse` 早在白名单里、也早就闭合，
但它的 `edges` 是 `list[dict[str, Any]]`，生成的 TS 于是是 `{ [k: string]: unknown }[]`：
**一个前端读不动的元素类型**。`frontend/src/lib/api.ts` 只能自己手写 `FreshEdge`，
并在**两个**调用点把整个信封内联重写一遍。

本节把元素命名成 `FreshEdgeItem`、把它的点命名成 `EdgeSeriesPoint`，登记为根、重生成、
把两个手写类型退役成别名，并把信封里**恒写**的字段按生产者收紧成 required。

🔴 **但本节最值钱的一条是负面的**：这条路由此前的**第一条分支**（`rank_fresh_edges`）
**在全部五份存量语料里一次都没有运行过** —— 任何「把路由对着语料跑一遍」的普查都会**静默地**漏掉它。
见 53.54.2。

### 53.54.1 取证：生产者驱动，不读词表

方法同 §53.53：导入真实的 `trend_analysis_service` 函数喂真实语料，
只复刻路由自己的六行胶水（`api/routes/events.py:1511-1536`，逐字复制）。
探针 `probe_fresh_edges_shape.py`，读数 `probe_fresh_edges_shape.txt`。

| 语料 | 文件大小 | 事件数 | 快照数 |
| --- | --- | --- | --- |
| `backend/event_audit.jsonl`（工作树） | 175 150 B | 146 | 526 |
| `backend/backups/event-id-migration-20260622-040713/` | 101 868 B | 78 | 307 |
| `backend/backups/manifold-lowercase-purge-20260709-150947/` | 264 083 B | 126 | 781 |
| `backend/backups/manifold-purge-20260709-150402/` | 357 938 B | 171 | 1 059 |
| `.workbuddy/audit-2026-10-03/preA-backup/`（与第 1 份同源） | 175 150 B | 146 | 526 |

参数组合覆盖 `classification` 六值 × `include_series` 两值 × `offset` 两值。

**共发出 2 324 条 item，而 key 集只有四种**，且全都是
`{event_id, event_title, edge}` ∪ `{event_title_zh?}` ∪ `{series?}` 的子集：

| 计数 | key 集 | 出处 |
| --- | --- | --- |
| 281 | `edge, event_id, event_title, event_title_zh` | 语料 1 / 5 |
| 281 | 同上 `+ series` | 语料 1 / 5 |
| 200 | `edge, event_id, event_title` | 语料 2 |
| 200 | 同上 `+ series` | 语料 2 |
| 118 | `edge, event_id, event_title` | 语料 4 |
| 82 | `edge, event_id, event_title, event_title_zh` | 语料 4 |

🔑 **语料 4 是关键**：它在**同一次普查内**同时出现「有 `event_title_zh`」（82 条）与「没有」（118 条）。
这把「该键是条件性写入」从**代码读法**变成了**观测**——否则语料 1/5 的 100% 与语料 2 的 0%
都可以用「语料差异」解释掉，而模型里给它一个默认值这个决定就失去了依据。

另外两个集合各只有一种形状：

- **嵌套 `edge`：12 键，2 324/2 324 一致**，且**两条生产者分支与 `_empty_edge()` 路径三者完全相同**。
- **`series` 元素：4 键**（`baseline` / `edge` / `estimated` / `timestamp`），全部 4 666 个点一致。

### 53.54.2 两个可达性陷阱

**（a）第一条分支从未运行。** 所有 `classification=fresh & include_series=False & offset=0`
的组合在**五份语料里全部返回 0 条**。原因是 `settings.EDGE_STALE_HOURS = 72.0`，
而语料里最新的快照是 `2026-09-10T18:39:44`（普查日 2026-10-08）：
`_classify_edge` 一律返回 `"stale"` ⇒ `rank_fresh_edges` 什么都产不出来。

⇒ 那条路上如果写了判据，它**在本轮的全部证据里都是空的**，而报告会显示「跑过了、没问题」。

**（b）于是直接驱动分支 A。** `rank_fresh_edges` **就是**该分支的生产者，
而它的 `now` 参数本来就是「给确定性测试用」的（见其 docstring）⇒ 扫 `now` 不改变任何输入数据。
探针 `probe_fresh_edges_branch_a.py`，读数 `.txt`。五份语料分别得 415 / 108 / 368 / 407 / 415 条：

- item key 集**恰为** `['edge', 'event_id', 'event_title']`，**一种**；
- `edge` 的 12 键与分支 B **相同**；
- **`series` 从不产出**（5/5，与源码读法一致）；
- **且分支 A 的每一个 edge 字段都非空**。

最后一条是本节最尖的地方：分支 A 只保留 `classification == "fresh"`，
而全 null 的 `_empty_edge()` **只**经 `no_data` 到达。所以
`latest_edge` / `first_edge` / `peak_edge` / `first_seen` / `last_seen` / `span_hours` / `age_hours`
这七个字段的 `float | None` **空值分支只由分支 B 的 `no_data` 路径产生**
（语料 1 与 5 各提供 162 条；语料 2 / 3 / 4 一条也没有）。
**只读分支 A 就会误判成 `float`（非空）就够** —— 这正是「承重与空头」在**行级**的翻版。

### 53.54.3 `exclude_unset` 会穿透 list-of-models ⇒ 命名不是行为变更

路由声明的是 `response_model=FreshEdgesResponse, response_model_exclude_unset=True`
（`events.py:1492-1494`）。今天 `edges` 是 `list[dict]`，元素是**裸 dict**，
`exclude_unset` 碰不到它 ⇒「没有中文标题」意味着**键缺席**，而不是 `null`。
一旦改成 `list[FreshEdgeItem]`，pydantic 会逐元素校验并重新序列化，于是问题变成
「声明了 `event_title_zh: str | None = None` 之后，一个从没设过它的元素会变成缺席还是 null？」

探针 `probe_exclude_unset_nested.py` 用与 FastAPI **等价**的调用
（`model_validate` → `model_dump(mode="json", exclude_unset=True)`）实测：

| case | 输入 | 结果 |
| --- | --- | --- |
| 1 | 元素不给两个可选键 | **两键都缺席** |
| 2 | 元素给了两个可选键 | 都在（值原样） |
| 3（控制组） | 信封字段整体不给 | 被丢弃 |

⇒ `event_title_zh: str | None = None` / `series: list[EdgeSeriesPoint] | None = None`
**保持了今天「不给就不出现」的行为**。在这一点上，命名是**纯类型变更**。
反过来说：这两个字段**必须**带默认值 —— 不然「没有中文标题的事件」（五份语料里三份的常态）
会从「键缺席」变成 **500**。

### 53.54.4 落地

**后端**（`backend/app/models/event.py`，+95/−7）

- 新增 `EdgeSeriesPoint`：`timestamp: str | None`（**无默认**）/ `estimated` / `baseline` / `edge`（`float`，无默认）。
  `timestamp` 写成 required-but-nullable 而不是 `str`：`edge_series` 总是写这个键，
  但值是 `snap.get("timestamp")` 原样透传，代码路径允许它是 `None`。
  4 666 个点里它**每次都是字符串** —— 那是关于这几份语料的**观测**，不是保证，所以类型保留了代码真有的那个分支。
- 新增 `FreshEdgeItem`：`event_id` / `event_title` / `edge: EdgeTrajectorySummary` 无默认；
  `event_title_zh: str | None = None` / `series: list[EdgeSeriesPoint] | None = None` 带默认。
- `FreshEdgesResponse.edges`：`list[dict[str, Any]]` → `list[FreshEdgeItem]`；
  六个恒写字段（`count` / `total` / `limit` / `offset` / `classification` / `classification_totals`）
  去掉默认值。`classification` 由 `str | None = None` 收紧成 `str` —— 它其实就是那个已校验的查询参数。

  ⚠️ **口径说明（须显式记下）**：请拍板的原话是「`count` / `total` / `limit` / `offset` / `classification`
  这 5 个字段收紧成 required」。实际动手时把 **`classification_totals` 与 `edges` 一并**收紧了，
  理由是唯一那条路由**无条件设置全部 7 个键**，只收 5 个会让同一个信封一半严一半松。
  这是**同一条裁决沿同一根轴的自然延伸**，但它确实是超出原话的部分，故记在此处备查。

- 白名单（`backend/app/models/_frontend_export.py`，+4/−0）：两个新模型加进 import 与 `__all__`。
  **必须是根**（而不是靠 `$ref` 到达）：只被引用到的模型拿不到 `additionalProperties: false`，
  `json2ts` 会补回 `[k: string]: unknown` —— 恰好回到前端读不动的那一格。

**重生成**（`frontend/src/lib/generated-types.ts`，磁盘 33 142 → **37 705 B**，crlf 940 → 1 024，`bare_lf` 全程 0）

| 读数 | 改前 | 改后 |
| --- | --- | --- |
| `export interface` 数 | 52 | **54**（+`EdgeSeriesPoint`、+`FreshEdgeItem`） |
| `FreshEdgesResponse` 体内行数 | 12 | 22 |
| `FreshEdgesResponse` 体内 **inline 尾巴行数** | **2** | **1**（只剩 `classification_totals` 的 `Record<string,int>`） |
| `FreshEdgesResponse.edges` 字段 | `edges?: {` | `edges: FreshEdgeItem[];` |

（`inline 尾巴`按 §2 的严格判据说：体内出现了 `  [k: string]: unknown;` 的行数。
`classification_totals` 是**合法**的 inline 索引签名 —— 它镜像 `dict[str, int]`，不是漏登记。）

**前端**

- `frontend/src/lib/api.ts`（+22/−23，磁盘 41 568 → 42 249 B，crlf 1 244 → 1 243）：
  两个调用点（`freshEdges` / `edgeMonitor`）改成 `api<FreshEdgesResponse>`；
  手写的 `export interface FreshEdge` / `EdgePoint` **退役成三行别名**
  （`FreshEdge = GeneratedFreshEdgeItem` / `EdgePoint = GeneratedEdgeSeriesPoint` /
  `FreshEdgesResponse = GeneratedFreshEdgesResponse`）。`export interface` 数 **41 → 39**。
- `frontend/src/app/decisions/page.tsx`（+2/−2）：`for (const e of (fresh.edges ?? []) as FreshEdge[])`
  → `for (const e of fresh.edges ?? [])`。强转变冗余，一并清掉；`FreshEdge` 从 import 里去掉。
- `frontend/src/app/edges/page.tsx`（+7/−1）：**一处 §2 型修正**。
  `tsc` 报 `TS2322: Type 'EdgeSeriesPoint[] | null | undefined' is not assignable to type 'EdgeSeriesPoint[] | undefined'`。
  这**不是**调用点的错：`series` 在生成类型里是 `EdgeSeriesPoint[] | null | undefined`，
  而「必存在但可为 null」在生成的 TS 里**无法表达** ⇒ 按 §2 的既定修法**放宽接收端的 prop**
  （`series?: EdgePoint[] | null`），而不是在每个调用点撒 `?? undefined`。函数体里的 `series ?? []` 本来就两种都吃。

**顺带修正一处既有漂移（须显式记下）**

`frontend/src/app/edges/page.test.tsx` 与 `decisions/page.test.tsx` 的夹具里，
`edge` 对象只有 **9** 个字段，缺 `first_seen` / `last_seen` / `span_hours`
—— 而这**正是 §53.53 当时删掉的那三个**：手写的 `EdgeTrajectory` 比生产者窄三个字段，
§53.53 把 `EdgeTrajectory` 换成 12 字段的生成类型之后，这两个夹具就**静默地过期了**。
它们没被任何东西拦下，原因见 53.54.8 第 3 条。本节把两个夹具补齐成 12 个字段。

### 53.54.5 守卫与变异

**守卫**（`backend/tests/test_generate_types.py`，+176/−1）

| 判据 | 层 | 读什么 |
| --- | --- | --- |
| ① `edges` 必须指名 `FreshEdgeItem` | 模型身份 | `model_fields['edges'].annotation == list[FreshEdgeItem]` |
| ② 元素的 `edge` / `series` 必须指名 `EdgeTrajectorySummary` / `list[EdgeSeriesPoint] \| None` | 模型身份 | 同上 |
| ③ **恒写**字段必须 required | 模型可选性 | `is_required()` |
| ④ **条件写**字段必须**保留默认值** | 模型可选性 | `is_required() == False` |
| ⑤ `api.ts` / `types.ts` 不得再声明这四个名字的 `interface` | 按名判（文本） | `_interface_bodies` |
| ⑥ 三行别名必须在 | 文本 | `assertIn` |
| ⑦ 两个调用点必须读 `api<FreshEdgesResponse>` | 文本 | 从路径行**往回**找最近的 `api<` |

③④ 是**两向**的，而且方向相反：
去掉 `event_title_zh` 的默认值会让「没有中文标题的事件」变成 500；
而给 `count` 加回默认值会**静默地**把已发布信封重新放宽成 `count?:`。
`model_fields[...].is_required()` 是唯一能看见第二种读法（生成文本本身仍然「合法」，只是更松）。

⑦ 用「往回找最近的 `api<`」而不是固定窗口：`edgeMonitor` 那个调用点下方不远处就是
`resetData` 的内联 `api<{ ... }>`，任何够宽的固定窗口都会匹配到那个**邻居**。

另外把 `EdgeSeriesPoint` / `FreshEdgeItem` 加进既有的闭合清单
（`test_closed_overlay_interfaces_have_no_tail`）—— 它们作为**根**才闭合，这一条钉的就是那个前提。

**变异**（`frontend-type-closure` 追加 J5–J12，该 set 由 10 条增至 **18 条**；`list` 报 **12 套 / 98 条**）

四阶段全部通过（`verify --index 11-18`）：

| # | 变异 | 踩的判据 | 结果 |
| --- | --- | --- | --- |
| J5 | `api.ts` 又声明 `EdgePoint`（**别名刻意留着**） | ⑤ / api.ts | OK |
| J6 | `types.ts` 又声明 `FreshEdgeItem` | ⑤ / types.ts | OK |
| J7 | 别名被改指内联形状（**不插副本**） | ⑥ | OK |
| J8 | `freshEdges` 调用点 generic 退回内联 | ⑦ / 第 1 处 | OK |
| J9 | `edgeMonitor` 调用点 generic 退回内联 | ⑦ / 第 2 处 | OK |
| J10 | `edges` 退回 `list[dict[str, Any]]` | ① | OK |
| J11 | `FreshEdgeItem.edge` 退回裸 dict | ② | OK |
| J12 | `event_title_zh` 丢掉默认值 | ④ | OK |

**J5 留别名、J7 不插副本是刻意的**：⑥ 与 ⑤ 落在同一个测试里，
一改别名就两半同时红 ⇒ 红的归因不可辨，`J5`/`J6` 也就无法证明 ⑤ 承重。
两条合起来才把两半**隔离**出来。

🔴 **失败文本取证**（§53.53 那条教训的执行）：四阶段只判「红没红」。
探针 `probe_5354_failure_text.py` 逐条应用、跑守卫、读失败文本、按 sha256 还原，
**每一次都恰好 1 个测试失败**（`1 failed, 14 passed` 或 `1 failed, 13 passed`）：

| # | 失败文本（截断） |
| --- | --- |
| J5 | `'EdgePoint' unexpectedly found in {…}` ← **只**红在负向那半，别名断言通过 |
| J6 | `'FreshEdgeItem' unexpectedly found in {…}` |
| J7 | `'export type FreshEdge = GeneratedFreshEdgeItem;' not found in '…'` ← **只**红在别名那半 |
| J8 | `'api<FreshEdgesResponse>(' not found in '    api<{ edges: unknown[] }>(\`/events/edges/fresh?limit=${limit}\`),'` |
| J9 | `'api<FreshEdgesResponse>(' not found in '    api<{ edges: unknown[] }>('` |
| J10 | `list[dict[str, typing.Any]] != list[app.models.event.FreshEdgeItem] : FreshEdgesResponse.edges no longer names its published element` |
| J11 | `dict[str, typing.Any] != <class 'app.models.event.EdgeTrajectorySummary'> : FreshEdgeItem.edge no longer names the 53.53 trajectory model` |
| J12 | `True is not false : FreshEdgeItem.event_title_zh lost its default. …` |

`test_mutation_verify.py`：`tests=193 failures=0 errors=0`（§53.53 为 185；+8 = 八条新变异）。

### 53.54.6 门与全量

| 门 | 读数 |
| --- | --- |
| `ruff check app/` | `All checks passed!` |
| `mypy app/` | `Success: no issues found in 331 source files` |
| `python -m scripts.generate_types --check` | `generated-types.ts is up to date.` |
| `tsc --noEmit` | 0 条 `error TS`（含 53.54.4 那次 `TS2322` 修正后复跑） |
| `eslint`（六个改动的前端文件） | 无输出（干净） |
| `vitest run` | **125 文件 / 738 用例**全过 |
| `test_mutation_verify.py` | `tests=193 failures=0 errors=0`（元素 62；§53.53 为 185，+8） |
| 后端全量 | **`tests=7816 failures=0 errors=0 skipped=11`**（元素 **6642**） |

**全量对账**（对 §53.53 基线 `tests=7784` / 元素 `6640`）：两份 junit **逐 `<testcase>` 对比，零删除**，
新增者恰为两个 `FreshEdgesContractTests` 用例。`tests` 差 **+32** 逐项可查：

| 来源 | §53.53 | §53.54 | Δ | 怎么测的 |
| --- | --- | --- | --- | --- |
| `<testcase>` 元素 | 6640 | **6642** | **+2** | `full_5353.xml` vs `junit_full_53_54.xml` 按 `(classname, name)` diff |
| closure 守卫 subtest | 10 | **12** | +2 | 单跑 `-k test_closed_overlay_interfaces_have_no_tail` → `1 passed, 12 subtests passed` |
| `FreshEdgesContractTests` subtest | 0 | **20** | +20 | 单跑 `-k FreshEdgesContractTests` → `2 passed, 20 subtests passed` |
| 变异 subtest | 123 | **131** | +8 | 单跑 `tests/test_mutation_verify.py` → `62 passed, 131 subtests passed`（= 套件 `tests` **193**） |
| 合计 `tests` | 7784 | **7816** | **+32** | `2 + 2 + 20 + 8 = 32` ✅ **无未解释余量** |

⚠️ **subtest 不落 junit**：`junit_full_53_54.xml` 里 `subtest` 字样 **0 次**、`<subtests>` 元素 **0 个** ——
pytest-subtests 只把 subtest 计入**套件级** `tests` 属性，**不生成 `<testcase>`**。
所以上表后三行的 Δ **只能单跑实测**，从 junit 里读不出来。
（这也是对 §53.53 那张对账表的一个补充：它的 `3 + 9 = 12` 同样是实测所得，只是当时没写明这一层。）

✅ **复跑守卫未漂生成物**：`test_generate_types.py` 会**重新生成** `generated-types.ts`（见 53.54.8 第 2 条），
而本节为对账又跑了该文件两次 ⇒ 跑前/跑后对 `generated-types.ts` 与 `api.ts` 各取 sha256，**逐字节相同**
（37 705 B / `377db528…`；42 249 B / `f25803ad…`）。

### 53.54.7 未决 / 有意不做

- **C1 剩余**：`EventDiscoveryResponse.events`、`EventMoversResponse.movers`、`SimilarEventsResponse.similar`、
  `PendingLinksResponse.pending`、`RecentPredictionsResponse.predictions`、`OpenDecisionsResponse.decisions`，
  加上 `EventListResponse`（生成侧 `EventStoreEntry[]`，调用侧却写 `TrackedEntry[]`，**两者不一致，需先判谁忠实**）。
  🔴 **本节更正 §53.53 的一处清单错误**：`AutoResolveResponse.matches` **不是** C1 候选 ——
  `grep -rn "auto-resolve\|autoResolve" frontend/src` **零命中**，`api.ts` 根本没有调用点；
  后端路由在（`events.py:1339`），前端只剩一条**死 re-export**（`api.ts:329`）。它归「死 re-export」一类。
- **孪生线索（未取证）**：`EventMoversResponse.movers` 的结构与本节点同款
  （`rank_movers` 产 `{event_id, event_title, trend}`，而 `TrendSummary` 与 `Mover.trend?: Trend` 两半都已命名）。
  **仅源码读得出，未跑探针**，故只作为线索登记。
- **有意不登记的变异**：恒写字段加回默认值（与 J12 共用同一个 `is_required()` 断言形状）；
  `FreshEdgeItem.series` 的同一性（与 J11 共用同一个 `assertEqual(annotation, …)` 形状）。
  ⚠️ 两条的前提都写进了 set 的 `rationale`：**若那两个断言形状被拆开，就要各补一条**。
- **死 re-export 仍是三个**：`AutoResolveResponse` / `EventListResponse` / `EventMoversResponse`（`api.ts:329-331`）零消费者。
- ⚠️ **`EdgeSeriesPoint.timestamp` 的前提**：声明成 `str | None` 是因为代码路径允许 `None`，
  而 4 666 个观测点里**一次都没出现**。若哪天在真实语料里观测到一次，那是**印证**而非缺陷。
- 本节随本轮改动**未推送**。

### 53.54.8 本轮新增的三条方法学（都来自本节踩的坑）

**1. 变异探针必须崩溃安全。** 第一版失败文本探针在前台跑，撞上工具 120 s 超时被 **SIGTERM** ——
它**已经写入** J7 的变异、还没走到 `finally` 的还原。Python 对 SIGTERM 的默认处置是直接终止，
**`finally` 不会执行** ⇒ api.ts 被留在变异态。下一轮探针在 index 13 才以
`ValueError: needle not found` 暴露出来。
修法是**类别级**的：① 原字节**先落盘**成 `*.534bak`（在任何写入之前），② 启动时做**预检**、
发现残留就从备份恢复、恢复不了就**拒绝启动**，③ 长任务一律后台跑。
⚠️ 备份**不要写在被测文件旁边** —— 第一版把 `.534bak` 写进了仓库树（`git status` 里三个 `??`）。
本条已写进 skill。

**2. 跑守卫的探针，必须把守卫**生成**的文件也纳入复核。** 我只对 `m.path` 做了 sha256 还原，
而 `test_generate_types.py` 里的 `_run_script()` 会**重新生成** `generated-types.ts`。
于是 J12 那一跑把「`event_title_zh` 无默认值」的生成结果**留在了树里**
（后续 J10/J11 的泄漏被 J12 的覆盖掉，所以最终只漂了一行）。
探针报告「bytes restored True」是**真的** —— 它只是**答错了问题**。
发现它的是最后那道 `generate_types --check`。⇒ **守卫会写文件时，「还原」的判据必须覆盖那些文件。**
harness 自己不受影响：它的阶段③会**再跑一次守卫**，用还原后的模型把它重新生成回来。

**3. `tsconfig.json` 排除了 `**/*.test.ts` 与 `**/*.test.tsx`。**
所以 `npm run typecheck`（`tsc --noEmit`）**从不检查测试文件**，
而 vitest 走 transpile-only、也不做类型检查。
后果：§53.53 把 `EdgeTrajectory` 从 9 字段换成 12 字段的生成类型之后，
两个夹具**静默地**停在 9 个字段上，`tsc` 仍然全绿、`vitest` 仍然全过。
⇒ **本仓「tsc 全绿」的前提是「不含测试文件」** —— 复述这个读数时必须带上它。
本节已把两个夹具补齐（53.54.4）。

## 53.55 `GET /events/movers`：元素具名的第三个节点，以及一个「不具名就改字节」的偏差

承接 §53.54。C1 剩下七个节点里，只有它有**孪生线索**（§53.54.7 登记为「仅源码读得出」）：
信封 `EventMoversResponse` 早已在白名单里，元素的 `trend` 也已由 §53.53 发布成 `TrendSummary`，
只差把那个四字段外壳命名成 `EventMoverItem`。查完确实如此，工作量与 §53.54 同级。

🔴 **但它与 §53.54 有一处真差别，而这正好是本节最值钱的一条**：姊妹路由 `/edges/fresh` 声明了
`response_model_exclude_unset=True`，而 `/movers` **没有**。今天元素是裸 dict、pydantic 不碰它，
于是「没有中文标题」= **键缺席**；一旦具名，模型会给 `event_title_zh` 补上**显式 `null`** ——
**具名本身就改变了线上字节**。修法不是放弃具名，而是**同一改动里把 `exclude_unset` 补上**。
见 53.55.4。

### 53.55.1 取证：生产者驱动，不读词表

方法同 §53.53 / §53.54：导入真实的 `rank_movers` 与 `trend_analysis_service`，
喂五份语料、各跑五个 `limit`，只复刻路由自己的胶水（`events.py` 的 enrichment 与过滤）。
探针 `probe_movers_shape.py`，读数 `probe_movers_shape.txt`。

| 语料 | 文件大小 | 事件数 | 快照数 | 发出的 item |
| --- | --- | --- | --- | --- |
| `backend/event_audit.jsonl`（工作树） | 175 150 B | 146 | 526 | 91 |
| `backend/backups/event-id-migration-20260622-040713/` | 101 868 B | 78 | 307 | **0** |
| `backend/backups/manifold-lowercase-purge-20260709-150947/` | 264 083 B | 126 | 781 | 91 |
| `backend/backups/manifold-purge-20260709-150402/` | 357 938 B | 171 | 1 059 | 59 |
| `.workbuddy/audit-2026-10-03/preA-backup/`（与第 1 份同源） | 175 150 B | 146 | 526 | 91 |

参数覆盖 `limit ∈ {1, 5, 10, 25, 50}`。**共发出 332 条 item，而 key 集有且只有一种**：

| 计数 | key 集 |
| --- | --- |
| 332 | `['event_id', 'event_title', 'event_title_zh', 'trend']` |

三个标量字段全部 `str`（`event_title` **一条空串也没有**）。

**嵌套 `trend`：key 集与 `TrendSummary.model_fields` 的 14 个字段完全一致，332/332。**
另做**接受性测试**（严格模型会不会把 200 变成 500？）：对每份语料的每个事件都跑
`analyze_trend(...)` **和** `_empty_trend()`（两条分支都喂），共 **1 334 次 `model_validate`，0 次被拒**
（292 + 156 + 252 + 342 + 292 = 1 334）⇒ 让 `trend` 指名 `TrendSummary` 不可能把 200 变成 500。

### 53.55.2 陷阱：`event_title_zh` 的「缺席」分支**当前不可达**

- 语料侧：`event_title_zh` **332/332 全在**。
- 活库侧（阴性对照，`probe_movers_shape.py` 开头那段）：store 257 条、**257 条都有非空 `event_title_zh`**。

⇒「没有中文标题的事件」这一支在**今天的数据上不可达**：两侧证据指向同一结论，
所以普查**观测不到**那个分支。「具名后是 `null` 还是缺席」**无法用观测解决，只能拍板**。
⚠️ 注意 `event_title_zh` 的**存在性**由 `if title_zh:` 控制，**不是** `is not None` 控制 ——
它由语料的 enrichment 决定，而 enrichment 读的是**活库**，不是被测语料。

### 53.55.3 路由还有一条**真实被走到**的过滤分支 ⇒ item 数对 `limit` 不单调

`events.py`：mover 的 `event_id` 若**不在活库**里，整条**丢弃**（"Event was deleted"）。
后果：`count = |rank_movers(limit) ∩ 活库|` —— **对 `limit` 不单调**。语料 4 实测：

| `limit` | 1 | 5 | 10 | 25 | 50 |
| --- | --- | --- | --- | --- | --- |
| item | **0** | 3 | 6 | 19 | 31 |

极端例子：语料 2（`event-id-migration-20260622-040713`）**在每一个 `limit` 下都是 0 条** ——
它的 `event_id` 与当前活库不对应，被这条过滤**整批丢掉**。这不是缺陷（是"删掉的事件不进榜单"），
但**任何「跑一遍看条数」的普查都要先知道它**，否则会把「0 条」误读成「该分支没运行」。

### 53.55.4 契约点：`exclude_unset` 缺席 ⇒ 具名**本身**会把「键缺席」变成 `null`

`/movers` 路由声明 `response_model=EventMoversResponse`（**没有** `exclude_unset`）。
今天元素是裸 dict ⇒ pydantic 不碰 ⇒ 路由的 dict **原样**出去 ⇒ 没有中文标题时**键缺席**。

探针 `probe_movers_exclude_unset.py` 用与 FastAPI **等价**的调用
（`model_validate` → `model_dump(mode="json")`，`trend` 用**真实** `analyze_trend` 输出）实测：

| 情形 | `movers[0]`（无中文标题的事件）键集 |
| --- | --- |
| 今天 `list[dict]` 透传 | `event_id, event_title, trend` ← **缺席** |
| 具名后 `exclude_unset=False`（= `/movers` 原状） | 4 键，`event_title_zh: None` ← **null** |
| 具名后 `exclude_unset=True` | 3 键 ← 缺席**保住** |

**控制组**：`count` / `movers` 两个恒写字段在**两种模式**下都活着 ⇒ 加 `exclude_unset=True`
不会把它们丢掉。

⇒ 拍板走 **B（保字节）**：同 A 具名，另给 `/movers` 加 `response_model_exclude_unset=True`
（与姊妹路由 `/edges/fresh` 一致）⇒ 输出**逐字节不变**。
⚠️ 代价（须显式记下）：这把「丢不丢字段」变成依赖 `unset` 语义的机制 ——
**将来若有别的字段带默认值会被静默省略**。今天的 `count` / `movers` 不受影响（都恒写、都 required）。

### 53.55.5 落地

**后端**

- `backend/app/models/event.py`（+61/−5）：把原来的 `class EventMoversResponse` 从 `:627`
  （`CategoryCountsResponse` 与 `DailyDigestHeadline` 之间）**移走**，改放到 `TrendSummary` 家族之后
  （`EdgeTrajectorySummary` 与 `EventHistoryResponse` 之间），并在它前面新增 `EventMoverItem`。
  ```python
  class EventMoverItem(BaseModel):
      event_id: str
      event_title: str
      trend: TrendSummary
      event_title_zh: str | None = None

  class EventMoversResponse(FlexibleResponse):
      count: int
      movers: list[EventMoverItem]
  ```
  - **为什么搬家**：`EventMoverItem.trend: TrendSummary` 需要 `TrendSummary` 已定义，
    而本文件**没有** `from __future__ import annotations`、也**没有**字符串注解的先例 ⇒
    把 `EventMoverItem` 放到 `TrendSummary` 之后是唯一不动前向引用的位置。
  - `event_id` / `event_title` / `trend` **无默认**：三者都是 `rank_movers` **逐条无条件写**的
    （`_latest_title` 在无标题快照时返回 `""`、**从不** `None`）。
  - `event_title_zh: str | None = None` 带默认：它是**条件写入**，不是"有时为 null"。
    去掉默认值会让「没有中文标题的事件」从常态变成 **500**。
  - `count` 由 `int = 0` **收紧**成 `int` —— 唯一那条路由回一个**无条件设置两键**的 dict 字面量。
    `movers` 由 `list[dict[str, Any]] = Field(default_factory=list)` 收紧成 `list[EventMoverItem]`（无默认）。

- `backend/app/models/_frontend_export.py`（+2/−0）：`EventMoverItem` 加进 import 与 `__all__`，
  且**排在 `EventMoversResponse` 之前**（import 块与 `__all__` 都按名字排序）。
  **必须是根**（而不是靠 `$ref` 到达）：只被引用到的模型拿不到 `additionalProperties: false`,
  `json2ts` 会补回 `[k: string]: unknown` —— 恰好回到前端读不动的那一格。

- `backend/app/api/routes/events.py`（+11/−1）：`/movers` 路由改多行装饰器，
  **同一改动里**补上 `response_model_exclude_unset=True`（53.55.4）；
  单行装饰器变成四行正是为了容下这个参数。docstring 增补了这面旗子的理由。

**重生成**（`frontend/src/lib/generated-types.ts`，+57/−4）

| 读数 | 改前（§53.54 末） | 改后 |
| --- | --- | --- |
| 工作树字节 | 37 705 B | **40 621 B** |
| CRLF（= 行数） | 1 024 | **1 077**（+53 = numstat 57−4） |
| `bare_lf` | 0 | **0** |
| `export interface` 数 | 54 | **55**（+`EventMoverItem`；`EventMoversResponse` 本来就在） |
| `EventMoversResponse` 体内 **inline 尾巴行数** | 1（`movers` 内那张索引签名） | **0**（只剩信封自己的、设计内的那张） |

`EventMoversResponse` 体内的变化（生成顺序按名字排序 ⇒ 无重排）：

```diff
-  count?: number;
-  movers?: {
-    [k: string]: unknown;
-  }[];
+  count: number;
+  movers: EventMoverItem[];
   [k: string]: unknown;
```

新元素是**闭合**的（4 字段、无尾巴）：

```ts
export interface EventMoverItem {
  event_id: string;
  event_title: string;
  trend: TrendSummary;
  event_title_zh?: string | null;
}
```

（信封自己那张 `[k: string]: unknown;` 是 `FlexibleResponse` 子类的**设计内开放**，不是漏登记。）

**前端**

- `frontend/src/lib/types.ts`（+7/−6，`export interface` 4 → 3、`export type` 6 → 7）：
  `Mover` 从**手写 interface** 退役成 `export type Mover = EventMoverItem;`，
  并显式 `import type { EventMoverItem }`（`export type * from` **不创建本地绑定**，必须显式导入）。
  **旧 `Mover` 比生产者更松**（`event_title?: string` / `trend?: Trend`，而生产者两个恒写）
  ⇒ 退役是**收紧**，与 §53.54 同向。
- `frontend/src/lib/api.ts`（+2/−7）：调用点 `api<{ movers: Mover[]; count?: number }>(…)`
  → `api<EventMoversResponse>(\`/events/movers?limit=${limit}\`)`；import 行去掉 `Mover`。
- `frontend/src/components/app-nav.test.tsx`（+31/−4）：**顺带修正一处既有漂移**（见 53.55.8 第 3 条）。

### 53.55.6 守卫与变异

**守卫**（`backend/tests/test_generate_types.py`，+190/−0，新类 `EventMoversContractTests`，3 用例 / 7 subtests）

| 判据 | 层 | 读什么 |
| --- | --- | --- |
| ① `movers` 必须指名 `EventMoverItem` | 模型身份 | `model_fields['movers'].annotation == list[EventMoverItem]` |
| ② 元素的 `trend` 必须指名 `TrendSummary` | 模型身份 | `model_fields['trend'].annotation` |
| ③ **恒写**字段（`event_id`/`event_title`/`trend`/`count`）必须 required | 模型可选性 | `is_required()` |
| ④ **条件写**字段（`event_title_zh`）必须**保留默认值** | 模型可选性 | `is_required() == False` |
| ⑤ `api.ts` / `types.ts` 不得再声明 `Mover` / `EventMoverItem` 的 `interface` | 按名判（文本） | `_interface_bodies` |
| ⑥ 别名 `export type Mover = EventMoverItem;` 必须在 | 文本 | `assertIn` |
| ⑦ 调用点必须读 `api<EventMoversResponse>` | 文本 | 从路径行**往回**找最近的 `api<` |
| ⑧ `/movers` 装饰器必须**同时**含 `response_model=EventMoversResponse,` 与 `response_model_exclude_unset=True,` | **路由源码** | 按行取锚点后 4 行、`strip()` 后判 |

③④ 是**两向**的、方向相反：去掉 `event_title_zh` 默认值 ⇒ 500；
而给 `count` 加回默认值会**静默地**把已发布信封重新放宽成 `count?:`
（生成文本仍「合法」，只是更松 —— `is_required()` 是唯一能看见的读法）。

**⑧ 是新类型判据**：它读的是**路由源码的装饰器**，既不在模型里、也不在生成文件里。
`exclude_unset` 这件事**只有**它能看见（模型两半加不加这旗子都过、`tsc` 不看测试文件、前端消费者不在乎）。
判据**可按行判**（不跨行），因为 HEAD 是 LF、工作树是 CRLF，
多行子串会依赖检出的行尾（§1）。

另外把 `EventMoverItem` 加进既有的闭合清单 `test_closed_overlay_interfaces_have_no_tail`
—— 它作为**根**才闭合，这一条钉的就是那个前提。

**变异**（`frontend-type-closure` 追加 J13–J21，该 set 由 18 条增至 **27 条**；`list` 报 **12 套 / 107 条**）

四阶段全部通过（`verify --index 19-27`）：

| # | 变异 | 踩的判据 | 结果 |
| --- | --- | --- | --- |
| J13 | `api.ts` 又声明 `Mover`（**别名刻意留着**） | ⑤ / api.ts | OK |
| J14 | `types.ts` 又声明 `EventMoverItem`（同判据的另一个文件，§4.2） | ⑤ / types.ts | OK |
| J15 | 别名被改指内联形状（**不插副本**） | ⑥ | OK |
| J16 | 调用点 generic 退回内联 | ⑦ | OK |
| J17 | `movers` 退回 `list[dict[str, Any]]` | ① | OK |
| J18 | `EventMoverItem.trend` 退回裸 dict | ② | OK |
| J19 | `event_title_zh` 丢掉默认值 | ④ | OK |
| J20 | `count` 加回默认值 | ③ | OK |
| J21 | `/movers` 丢掉 `response_model_exclude_unset` | ⑧ | OK |

四阶段逐条：`green before True | red after mutation 1/1 files red | green after restore True | bytes restored True`；
每条**恰好 1 个测试失败**（J17/J18/J21 为 `1 failed, 16 deselected` ⇒ 只 1 个用例；其余 `1 failed, 1 passed, …`）。
`J20` 是 **`assertTrue(is_required())` 性红的第一条变异**（详见后文「有意不登记」那条的前提）。

🔴 **两条既有针在本节从唯一变成 2 次**（§4.3 应验：针的语义会随生产代码变化）：

- **J4** 的针 `    trend: TrendSummary` 因新增 `EventMoverItem.trend` ⇒ 2 次，
  改成跨行带上上一行 `    count: int`（只有 `EventHistoryResponse` 命中）。
- **J12** 的针 `    event_title_zh: str | None = None` 因新增同名同形字段 ⇒ 2 次，
  改成跨行带上上一行 `    event_title: str`。
- 两处**替换原因都已写进各自 `note`**。发现它们的正是 `test_every_needle_occurs_exactly_once_in_its_file`。

🔴 **两条新针与既有针压在同一行字节上，整集 apply 会冲突** ⇒ J19 / J20 改取**不相邻的上下文**：
J19 从 `event_title_zh` **下方**（空行 + `class EventMoversResponse`）消歧，J20 从 `count` **上方**（docstring 尾行）消歧。

### 53.55.7 门与全量

| 门 | 读数 |
| --- | --- |
| `ruff check app/` | `All checks passed!` |
| `mypy app/` | `Success: no issues found in 331 source files` |
| `python -m scripts.generate_types --check` | `generated-types.ts is up to date.` |
| `tsc --noEmit` | 0 条 `error TS` |
| `eslint`（四个改动的前端文件） | 无输出（干净） |
| `vitest run` | **125 文件 / 738 用例**全过（与 §53.54 相同 —— 本节只改夹具、不加用例） |
| `test_mutation_verify.py` | `tests=202 failures=0 errors=0`（元素 62；§53.54 为 193，+9） |
| 后端全量 | **`tests=7836 failures=0 errors=0 skipped=11`**（元素 **6645**） |

**全量对账**（对 §53.54 基线 `tests=7816` / 元素 `6642`）：两份 junit 按 `(classname, name)` **逐条 diff，零删除**，
新增者**恰为** `EventMoversContractTests` 的 3 个用例。`tests` 差 **+20** 逐项**实测**：

| 来源 | §53.54 | §53.55 | Δ | 怎么测的 |
| --- | --- | --- | --- | --- |
| `<testcase>` 元素 | 6642 | **6645** | **+3** | `junit_full_53_54.xml` vs `junit_full_53_55.xml` 按 `(classname, name)` diff |
| closure 守卫 subtest | 12 | **13** | +1 | 单跑 `-k test_closed_overlay_interfaces_have_no_tail` → `1 passed, 13 subtests passed` |
| `EventMoversContractTests` subtest | 0 | **7** | +7 | 单跑 `-k EventMoversContractTests` → `3 passed, 7 subtests passed` |
| 变异 subtest | 131 | **140** | +9 | 单跑 `tests/test_mutation_verify.py` → `62 passed, 140 subtests passed`（= 套件 `tests` **202**） |
| 合计 `tests` | 7816 | **7836** | **+20** | `3 + 1 + 7 + 9 = 20` ✅ **无未解释余量** |

⚠️ **subtest 不落 junit**（§53.54 已立）：`junit_full_53_55.xml` 里 `<subtests>` 元素 **0 个** ⇒ 上表后三行的 Δ **只能单跑实测**。
⚠️ 另：§53.55 全量的 summary 行**没进日志** —— `[safe-delete]` 把 100% 那行后的收尾覆盖掉了
（`rc=1` 是它的指纹，**判据只看 junit**）。所以那份分量的来源是**单跑**，不是全量日志。

✅ **复跑守卫未漂生成物**：`test_generate_types.py` 会**重新生成** `generated-types.ts`（§53.54.8 第 2 条），
本节又跑了它多次（全量 + 三次单跑）⇒ 跑前/跑后对 `generated-types.ts` 与 `api.ts` 各取 sha256，**逐字节相同**
（40 621 B / `428bb6ff…`；42 212 B / `9faa0bd8…`）。
⚠️ **`tsc 全绿` 的前提仍是"不含测试文件"**（`tsconfig.json` 排除 `**/*.test.ts(x)`，§53.54.8 第 3 条）。

### 53.55.8 未决 / 有意不做

- **C1 剩余**：`EventDiscoveryResponse.events`、`SimilarEventsResponse.similar`、
  `PendingLinksResponse.pending`、`RecentPredictionsResponse.predictions`、`OpenDecisionsResponse.decisions`，
  加上 `EventListResponse`（生成侧 `EventStoreEntry[]`，调用侧却写 `TrackedEntry[]`，**两者不一致，需先判谁忠实**）。
- **死 re-export 由 3 个减为 2 个**：`EventMoversResponse` 的调用点改用它之后**从死变活**；
  剩 `AutoResolveResponse` / `EventListResponse`（`api.ts` 尾部）。
- **有意不登记的变异**：
  - §53.54 envelope（`FreshEdgesResponse`）**恒写字段加回默认值** —— 与 **J20** 共用
    「`assertTrue(is_required())`」这一断言**形状**。
    🔴 **本节据此更正/细化一条旧结论**：`rationale` 记下了「**本 set 此前没有任何一条变异性红过
    `assertTrue(is_required())` 这一半**（J12 红的是 `assertFalse` 那半）」—— 所以 **J20 是首条**，
    它与 §53.54 那条取舍**不矛盾**（§53.54 是"避免只让计数虚胖"，不是"该半已覆盖"）。
    ⚠️ **但 J20 覆盖的是 `/movers` 的 `count`，不是 `FreshEdgesResponse` 的那六个字段** ⇒
    那个 `envelope` 循环的 `assertTrue` 半**至今仍从未被性红过**，本轮**只登记不修**；
    要补就再加一条「`FreshEdgesResponse.count` 加回默认值」的变异。
  - `FreshEdgeItem.series` 的同一性 —— 与 **J11** 共用「`assertEqual(annotation, …)`」形状。
  - ⚠️ 三条的前提都写进了 set 的 `rationale`：**若那些断言形状被拆开，就要各补一条**。
- ⚠️ **`exclude_unset=True` 的代价已在 53.55.4 记下**：将来若有别的字段带默认值会被**静默省略**。
- ⚠️ **`event_title_zh` 契约的前提**：选 B 保字节是在「缺席支**今天不可达**」这一观测下做的，
  在**当前真实数据上 A/B 输出完全相同**。若哪天「没有中文标题的事件」变得可达，A/B 才真正分叉。
- 本节随本轮改动**未推送**。

### 53.55.9 本轮方法学（本节踩到的坑）

**1. 具名一个「裸 dict 元素」可能改字节 —— 前提是有没有 `exclude_unset`。**
§53.54 的 `/edges/fresh` 有这旗子 ⇒ 具名是纯类型变更；`/movers` 没有 ⇒ 具名把「缺席」变 `null`。
🔑 **推论：动手具名元素之前，先读路由装饰器上有没有 `response_model_exclude_unset`。**
这条与「信封有没有闭合」「元素有没有被登记为根」并列，是同一个三件套里的**第四个**检查点。
已写进 skill。

**2. 路由装饰器可以当静态判据 —— 而且有时是唯一的判据。**
`exclude_unset` 这件事在模型层、生成层、前端层**全部不可见**。把它做成「按行读 `"/movers",` 锚点后 4 行、
`strip()` 后断言」的守卫，是本节唯一新增的判据类型。

**3. 同一段字节上的两条相反改写必须同 `group`；针重叠时要换上下文而不是硬改。**
J18/J19/J20/J21 四条针都落在 `EventMoverItem` / `EventMoversResponse` 附近。
设计初稿里 J19 用 `trend` 行、J20 用 `movers` 行消歧 ⇒ 分别与 J18/J17 的针**压在同一行** ⇒
整集 apply 时 `_apply_preflight` 无法同时落位。
**改法是换方向取上下文**（一个向上、一个向下），而不是把它们塞进同一个 `group` ——
`group` 只该用于「真正的同一段字节、互为反向」的情形（§4.3）。
