# World Cup Prediction System Design

本文定义世界杯预测系统的产品边界、技术架构和后续优先级。目标不是做博彩下注工具，而是把 PMRF 现有的事件情报、证据评分、概率变化、校准闭环，优先扩展成一个可验证的世界杯垂直预测系统。

## Strategic Anchor

优先完成世界杯预测系统，用高热度、高频、可结算的赛事场景验证 PMRF 从通用事件情报平台走向垂直概率系统的能力。

## Strategy Kernel

Diagnosis：世界杯信息密集、事件高频、结果明确，但伤病、阵容、红黄牌、赛程、积分形势对概率的影响高度依赖上下文，不能简单写死。

Guiding policy：事实和结算规则确定性处理；概率影响由 AI/模型判断，但必须接受结构化事实输入、置信度约束和赛后校准反馈。

Coherent actions：

- 建立世界杯候选事件与赛事事实层。
- 接入球队、球员、赛程、赛果、纪律、伤病、阵容等结构化信号。
- 把结构化信号作为 AI 概率分析的强上下文，而不是让 AI 自由阅读新闻后自行猜事实。
- 用自动结算和校准闭环复盘每类预测是否可靠。

## Product Scope

### In Scope

- 世界杯事件发现：出线、晋级、冠军、进球数、红黄牌、点球大战、加时、球员奖项。
- 赛前概率报告：市场基线、系统估计、关键证据、结构化信号、风险说明。
- 事实信号抽取：伤病、停赛、首发、红黄牌、积分、净胜球、赛程密度。
- 自动结算：比赛结果、晋级结果、累计红牌/进球阈值、淘汰赛规则事件。
- 赛后复盘：Brier score、方向命中、哪些信号提升/损害判断质量。

### Out of Scope

- 自动下注、仓位建议、资金管理。
- 把某个事实硬编码成固定概率变化，例如“主力受伤 = -10%”。
- 第一阶段自研完整足球强弱模型。
- 第一阶段做复杂蒙特卡洛淘汰赛模拟器。

## Current State

当前系统已经具备通用事件发现和分析主链路：

- `event_intelligence_service.discover_events()`：多源候选事件进入分析池。
- `world_cup_event_source.py`：本地策划的 2026 世界杯候选事件源。
- `rss_service.py`：已加入足球新闻 RSS，用作世界杯证据补充。
- `event_store.json` / `event_audit.jsonl`：事件存储与概率轨迹。
- `prediction_store` / `calibration`：市场事件的冻结预测和校准闭环。

现有不足：

- 没有结构化赛程、赛果、红黄牌、伤病、首发数据源。
- 世界杯事件目前主要作为候选问题进入通用 AI 分析，缺少体育专用事实层。
- 非 prediction market 的体育事件不会进入 `prediction_store.freeze_prediction()`，因此需要专门的 sports prediction commitment 设计，或明确复用 event audit 做非市场事件校准。

## Core Design

### Three-Layer Model

```text
Raw Sources
  ├─ news / RSS / Google News
  ├─ official FIFA / match data
  ├─ injury / lineup sources
  └─ prediction markets

Fact Layer
  ├─ match schedule
  ├─ match result
  ├─ team table / qualification status
  ├─ player injury / availability
  ├─ red/yellow cards
  └─ lineup / suspension

Signal Layer
  ├─ injury_signal
  ├─ discipline_signal
  ├─ qualification_pressure
  ├─ schedule_fatigue
  ├─ market_divergence
  └─ evidence_quality

Probability Layer
  ├─ baseline probability
  ├─ AI probability estimate
  ├─ cross-validation estimate
  ├─ calibration adjustment
  └─ final report
```

### Deterministic vs AI Responsibilities

| Area | Deterministic | AI / model judgment |
|---|---|---|
| 球员受伤 | 抽取球员、球队、状态、来源、时间、可信度 | 判断该球员重要性、替补深度、对具体事件概率的影响 |
| 球员停赛 | 根据黄牌/红牌/纪律规则确定是否停赛 | 判断停赛对战术、晋级概率、比赛节奏的影响 |
| 累计红牌 | 统计官方比赛红牌数量，达到阈值直接结算 | 判断当前判罚尺度是否影响未来红牌事件概率 |
| 小组出线 | 积分、净胜球、赛程、数学出线/淘汰状态 | 判断未赛比赛中的形势变化和非确定性 |
| 淘汰赛晋级 | 官方赛果决定结算 | 判断赛前伤病、赛程、对手风格带来的概率变化 |
| 新闻可信度 | 来源等级、时间、是否多源确认 | 判断新闻对事件的方向和幅度 |

原则：事实不要交给 AI 猜；影响幅度不要简单写死。

## Proposed Data Model

第一阶段不需要一次性建完整体育数据库，但需要稳定的中间结构，供 AI 和后续自动结算复用。

### Sports Fact

```json
{
  "fact_id": "sports:worldcup2026:injury:player:source",
  "kind": "injury",
  "tournament": "2026 FIFA World Cup",
  "team": "Brazil",
  "player": "Player Name",
  "status": "out|doubtful|questionable|fit|suspended",
  "severity": "low|medium|high|unknown",
  "source": "FIFA|BBC|Guardian|Reuters|manual",
  "source_url": "https://...",
  "confidence": 0.0,
  "observed_at": "ISO-8601",
  "applies_to": ["match_id", "event_id"]
}
```

### Match State

```json
{
  "match_id": "worldcup2026:group-a:usa-mexico",
  "tournament": "2026 FIFA World Cup",
  "stage": "group|round_of_32|round_of_16|quarterfinal|semifinal|final",
  "home_team": "United States",
  "away_team": "Mexico",
  "kickoff_at": "ISO-8601",
  "status": "scheduled|live|finished",
  "score": {"home": 0, "away": 0},
  "red_cards": {"home": 0, "away": 0},
  "yellow_cards": {"home": 0, "away": 0}
}
```

### Sports Signal

```json
{
  "event_id": "abc123",
  "signals": {
    "injury_signal": {
      "level": "none|low|medium|high",
      "direction": "supports_yes|supports_no|neutral",
      "summary": "Two starting defenders are doubtful.",
      "facts": ["fact_id_1", "fact_id_2"]
    },
    "discipline_signal": {
      "red_card_total": 6,
      "threshold_progress": 0.75,
      "suspensions": 1
    },
    "qualification_signal": {
      "team_points": 4,
      "remaining_matches": 1,
      "already_qualified": false,
      "already_eliminated": false
    }
  }
}
```

## Service Architecture

### New Services

- `world_cup_event_source.py`  
  已有初版。负责提供世界杯候选事件，不负责事实更新和概率判断。

- `sports_fact_service.py`  
  标准化伤病、停赛、红黄牌、赛程、赛果等事实。初期可以从 RSS/手动输入/简单 JSON 导入开始。

- `world_cup_match_source.py`  
  接入赛程和赛果数据源。优先只读公开或低成本来源，输出标准 `Match State`。

- `sports_signal_service.py`  
  把事实转成结构化信号。这里做确定性特征和规则进度，例如红牌累计、已出线、已淘汰、停赛状态。

- `sports_resolution_service.py`  
  根据赛果和累计统计自动结算世界杯事件。可结算的事件必须有明确 `resolution_criteria`。

- `sports_probability_context_service.py`  
  把结构化信号拼进 LLM `news_context`，让 AI 分析概率变化时读取的是已整理事实，而不是自由猜测。

### Integration Points

```text
discover_events()
  ├─ collect_shared_articles()
  ├─ fetch World Cup candidate events
  ├─ collect sports facts
  ├─ build sports signals per candidate
  ├─ analyze_event(..., news_context + sports_context)
  ├─ record_event()
  └─ sports_resolution_service later resolves outcomes
```

### API Additions

第一阶段建议只加读接口和一个受保护的导入接口：

- `GET /events/sports/world-cup/status`：世界杯数据源状态、事实数量、最近更新时间。
- `GET /events/sports/world-cup/facts`：查看当前结构化事实。
- `POST /events/sports/world-cup/facts/import`：手动导入/刷新事实，需 `X-API-Key`。
- `POST /events/sports/world-cup/resolve`：按当前赛果/事实自动结算世界杯事件，需 `X-API-Key`。

## Roadmap

### NOW: 当前迭代 / 1-2 周

Must ship:

- 固化世界杯候选事件源，覆盖球队晋级、比赛形态、纪律、球员奖项。
- 增加体育事实结构和 `sports_fact_service.py`。
- 增加手动/文件导入事实能力，先支持伤病、停赛、红牌累计、赛果。
- 把 sports signals 注入 `analyze_event()` 的上下文。
- 做世界杯专用筛选：按 `source.type = sports_event` 或 `source.tournament` 过滤。

High confidence:

- 先支持确定性自动结算：累计红牌阈值、比赛是否加时/点球、球队是否晋级。
- 先不做复杂强弱模型，用市场基线 + AI + 结构化事实解释。
- 所有新增写接口接入 `require_write_key`。

### NEXT: 1-2 个月

Build:

- 接入可靠赛程/赛果数据源，减少手动维护。
- 接入伤病/阵容数据源；无法稳定接入时，先做多源新闻抽取 + 人工确认状态。
- 新增世界杯 Dashboard 视图：球队、阶段、事件类型、风险信号、待结算事件。
- 增加赛后复盘页：每个事件的预测、结果、误差、主要信号。

Validate:

- 判断 sports signals 是否让概率解释更稳定。
- 跟踪“AI 只读新闻” vs “AI + 结构化事实”的校准差异。
- 观察用户是否持续查看世界杯页，而不是只点击一次。

### LATER: 3 个月+

Plan:

- 锦标赛路径模拟：小组出线、晋级树、冠军概率。
- 球员重要性模型：首发概率、位置权重、球队依赖度。
- 更细的 live match signals：红牌后比赛状态、加时概率、点球风险。
- 扩展到欧冠、NBA、电竞等高频赛事。

Revisit:

- 是否为非市场 sports_event 建立独立 prediction commitment 表。
- 是否把 sports facts 从 JSON/导入文件迁移到 SQLite 表。
- 是否引入外部付费体育数据源。

### NOT NOW

- 不做自动下注和资金建议：不服务当前产品边界，合规风险高。
- 不做全量 live-play-by-play 引擎：成本高，且不影响第一阶段验证。
- 不做复杂自研 Elo/xG 模型：没有足够数据校准前容易制造伪精度。
- 不对伤病影响写死固定百分点：上下文差异太大，且不利于校准学习。

## Strategic Bets

### Bet 1: Structured Sports Facts Improve Trust

Thesis：如果用户关心世界杯概率变化，他们需要看到事实链条，而不是只看到 AI 总结；结构化事实会提升报告可信度和复盘质量。

Signal to validate：两周内世界杯事件详情页中，用户主要查看证据和事实信号；结构化事实覆盖多数高价值事件。

Kill condition：事实源维护成本过高，且没有明显改善报告解释质量。

Capacity：当前迭代 40%-50% 后端能力。

### Bet 2: Fast Settlement Creates Calibration Momentum

Thesis：世界杯事件结算周期短，能比宏观/政策事件更快累积预测评分，从而让校准闭环更快产出可见价值。

Signal to validate：一个比赛阶段内能自动结算多数赛事形态事件，并生成可读复盘。

Kill condition：结算身份匹配大量需要人工修正，导致复盘不可依赖。

Capacity：当前迭代 25%-35% 后端能力。

## Success Metrics

- 世界杯候选事件发现成功率：`discover` 返回的 sports_event 数量。
- 结构化事实覆盖率：有 sports signals 的世界杯事件占比。
- 自动结算率：无需人工干预完成 outcome 的世界杯事件占比。
- 校准样本数：世界杯 resolved predictions/events 数量。
- 解释质量：每个高价值事件至少包含 2 个以上可追溯事实或证据。

## Engineering Constraints

- 保持现有事件情报主链路，不为世界杯复制一套平行系统。
- 新源适配器必须 thin：只 fetch / normalize，不做概率判断。
- 确定性结算必须 fail-closed：没有足够身份匹配或规则证据时，不自动结算。
- AI 输出不得成为事实源；AI 可以解释事实影响，但事实本身来自数据源、新闻证据或人工确认。
- 非市场事件不能伪装成 `prediction_market`，否则会污染 edge / freeze 语义。

## Highest-Confidence Next Move

先实现 `sports_fact_service.py` 和 `sports_signal_service.py`，支持手动/文件导入世界杯事实，并把伤病、停赛、红牌累计、赛果转成结构化上下文注入 `analyze_event()`。

The one assumption that could break this roadmap is：世界杯事件的事实层足够稳定、可维护，且能明显改善概率报告的可信度。We'll know within 2 weeks.
