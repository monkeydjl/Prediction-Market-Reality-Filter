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
| **收敛 13 个方向常量** | ⏳ 未动。§34.8 已给出"同值不同义"的逐项语义 —— 拍板前不宜合并 |
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
| 改 `_check_service_running()`（选项 a）| ⏳ 未动（**需拍板**；且要有配套用例——否则"修好了"无从验证）|
| 第二台机器/带代理 CI 的复现 | ⏳ 未做（35.4 的第三行因此只能标"推的"）|
| 写进 RUNBOOK（选项 d）| ⏳ 未做 |

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
| §35.5 的修法 (a)+(d) | ⏳ 未动（**待拍板**）|

**提交状态**：本节改 **1 个生产文件**（+ 本文档）。**未提交**（等指令）。
