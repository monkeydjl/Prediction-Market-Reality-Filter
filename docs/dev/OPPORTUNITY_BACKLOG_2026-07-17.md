# PMRF 可做项 / 优化全量清单

**版本：** 1.0  
**生成日期：** 2026-07-17  
**范围：** 全仓库现状盘点（文档、后端、前端、Sports Prediction OS、运维）  
**用途：** 作为「接下来还能做什么」的单一权威清单；按档位选型，不要求一次做完。  
**配套阅读：**

- [README.md](../../README.md) — 产品与 Sports OS 总览  
- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统架构  
- [ARCHITECTURE_PHILOSOPHY.md](../user/ARCHITECTURE_PHILOSOPHY.md) — 设计哲学  
- [V2_ROADMAP.md](../user/V2_ROADMAP.md) — V2 闭环路线  
- [CHANGELOG.md](../../CHANGELOG.md) — v0.4.0 Phase 1–13  
- [open-issues-verified-2026-06-21.md](../reviews/open-issues-verified-2026-06-21.md)  
- [pending-issues-2026-06-27.md](../reviews/pending-issues-2026-06-27.md)  
- [douyin-model-feasibility-2026-06-28.md](../reviews/douyin-model-feasibility-2026-06-28.md)  
- [Phase 15 前端集成设计](../superpowers/specs/2026-07-17-phase15-frontend-feature-integration-design.md)  
- [Phase 9 精度冲刺设计](../superpowers/specs/2026-07-16-sports-prediction-os-phase9-design.md)  

---

## 0. 如何使用本文档

| 符号 | 含义 |
|------|------|
| **P0** | 安全 / 闭环能否转起来；不做则其它优化难验证 |
| **P1** | 精度或产品主路径，投入产出比高 |
| **P2** | 明显增强，可排期 |
| **P3** | 体验 / 平台化 / 长尾，不阻塞主路径 |
| **BY-DESIGN** | 有意取舍，改前需产品决策 |

**推荐原则（与哲学一致）：** 先让闭环转起来并积累 resolved 样本，再加深引擎与因子——「反馈优于空泛智能」。

**硬约束（Sports Kernel）：**

- `prediction_kernel.py` / `domain.py` / `learning_service.py` / 已有 `engines/*.py` 在既有 Phase 中约定「零侵入」扩展时优先走 Protocol + FactorRegistry + 新引擎注册。  
- 新表用 `kernel_` 前缀；feature flag 默认 OFF。  
- 不做自动下注 / 仓位 / 资金管理。

---

## 1. 系统现状快照（2026-07-17）

### 1.1 双产品面

```text
事件情报平台（EIP）
  公开信息 → 候选事件 → 证据评分 → 概率变化 → 报告 → 人工审阅 → 市场结算校准

Sports Prediction OS（Phase 1–13 已落地代码）
  Adapter → FeatureBuilder → Engine → Learning
  + Market Bridge / Edge / Recommendation / Settlement
  + Futures / WebSocket / 回测与调参框架
```

### 1.2 技术栈

| 层 | 技术 |
|----|------|
| 后端 | FastAPI、APScheduler、JSON 文件存储 + SQLite WAL、Sentry 可选 |
| 前端 | Next.js 16 静态导出、React 19、SWR、Recharts、Tailwind 4 |
| 部署 | 单端口 `:8000` 同源服务；systemd / Docker 可选 |

### 1.3 Feature Flag（默认多为 OFF）

打开前请在 `backend/.env` 显式配置。主开关示例：

| Flag | 作用 |
|------|------|
| `KERNEL_PREDICTION_ENABLED` | Sports Kernel / `/api/predictions/*` |
| `PHASE2_LEAGUES_ENABLED` | 六大足球联赛适配器 |
| `PHASE3_LEARNING_ENABLED` | 赛后学习闭环 |
| `PHASE4_NBA_ENABLED` / `PHASE5_MLB_ENABLED` / `PHASE5_NHL_ENABLED` | NBA / MLB / NHL |
| `PHASE7_*` | 市场桥接 / 推荐 / 结算反馈 |
| `PHASE8_CALIBRATION_FUSION_ENABLED` | 校准融合进 Edge trust |
| `PHASE9_ACCURACY_SPRINT_ENABLED` | 回测 + 参数优化 |
| `PHASE10_REALTIME_PUSH_ENABLED` | WebSocket 价格推送 |
| `PHASE11_KALSHI_SPORTS_ENABLED` | Kalshi 体育市场 |
| `PHASE12_FUTURES_MARKETS_ENABLED` | 期货 / 冠军市场 |

**风险：** 功能已实现但 flag 关闭时，前端/API 表现为 503 或 legacy 路径，容易误判为「没做」。

### 1.4 引擎与因子现状（关键结论）

| 路径 | 引擎 | 实际消费的因子 | 备注 |
|------|------|----------------|------|
| Kernel 足球 | `EloOddsEngine` + `FootballMultiFactorEngine` | multi: elo/odds/form/rest/injury/h2h/**travel/xg/market_value**；EloOdds 仍为 elo+odds | multi-factor 默认 flag；xG 为进球代理；身价 cache-only |
| Kernel 篮球 | `BasketballEngine` | elo / home_court / rest / form / net_rating / travel / injury | Bradley-Terry 二元；缺因子权重重分配 |
| Kernel 棒球 | `BaseballEngine` | + starting_pitcher / park / bullpen / weather | 缺因子时权重重分配 |
| Kernel 冰球 | `HockeyEngine` | + goalie / travel / b2b rest | 同上 |
| 世界杯 legacy | DC / BTD / GBM / Rule / EloOdds | 更丰富 | DC / BTD / GBM / EloOdds 已于 2026-07-17 移植进 Kernel（见 §3.2 P1-E5/E6/E7），但引擎 flag 默认 OFF；仅 Rule（比分预测，非 3-way 概率）仍只在 legacy `world_cup_engines.ENGINES` 内 |

`FootballFeatureBuilder` 已能组装 form、h2h、身价、伤病、天气、xG/PPDA 等；**瓶颈在引擎融合层，不在特征脚手架。**

`FactorRegistry` 空库默认只种子 **elo / odds**；各运动引擎另有 `_DEFAULT_WEIGHTS`。

---

## 2. P0 — 安全、配置与闭环能否转起来

### 2.1 仓库外安全（代码无法单独完成）

| ID | 项 | 说明 | 动作 |
|----|-----|------|------|
| P0-S1 | LLM / DashScope Key 轮换 | 历史审查文档与 git 历史曾出现明文 key | 控制台 revoke + regenerate；可选 `git filter-repo` 清历史；启用 GitHub secret push protection |
| P0-S2 | 生产密钥与联系人 | `API_WRITE_KEY`、`SEC_USER_AGENT` 真实联系人、`PMRF_DEADMAN_URL`、`SENTRY_DSN` | 按 `docs/ops/RUNBOOK.md` 与 `.env.example` 配置 |
| P0-S3 | 备份包扫毒 | 含 `.env` 的备份勿进仓库/分享 | 轮换后清理旧包 |

### 2.2 运行配置（让系统「真的开着」）

| ID | 项 | 说明 |
|----|-----|------|
| P0-C1 | 按环境打开 Feature Flag | 开发/预发至少：`KERNEL_PREDICTION_ENABLED` + 所需体育 Phase；需要闭环时打开 discover / resolve 相关调度 |
| P0-C2 | 写鉴权 | 生产必须 `API_WRITE_KEY`；禁止生产 `ALLOW_OPEN_WRITES=true` |
| P0-C3 | LLM 启动探测 | 生产建议 `LLM_STARTUP_CHECK_ENABLED=true` |
| P0-C4 | 调度拆分 | 多进程时 API unit `SCHEDULER_ENABLED=false`，独立 scheduler unit，避免双跑 |
| P0-C5 | 事件发现样本 | 确认 `EVENT_DISCOVER_ENABLED` 与每日 07:15 discover → 22:30 auto-resolve 在产数据 |

### 2.3 闭环健康验收（建议做成检查表）

1. `GET /api/health` 为 ok / 明确 degraded 原因  
2. 手动或调度完成一轮 `discover`，`event_store` 有新记录  
3. 存在 `freeze_prediction` 与后续 resolve 打分  
4. Kernel 路径：对一场已知 `match_id` `POST` 预测成功  
5. （可选）市场链接 → edge → settlement 有一条完整样例  

**验收失败时不要先做复杂引擎调参**——先修数据源 / flag / 鉴权 / 调度。

---

## 3. P1 — 精度与引擎（收益最高）

### 3.1 足球：FeatureSet 已建、引擎未吃满

| ID | 项 | 现状 | 建议 |
|----|-----|------|------|
| P1-E1 | **多因子足球融合** | ✅ 2026-07-17：`FootballMultiFactorEngine` + flag `FOOTBALL_MULTI_FACTOR_ENGINE_ENABLED`（默认 OFF） | 融 elo/odds/form/rest/injury/h2h；缺因子重分配；不改 EloOddsEngine 与 global 0.30/0.70 |
| P1-E2 | 默认因子入库 | ✅ 部分 2026-07-20：足球 soft 含 altitude；NBA/MLB/NHL 软因子种子；不覆盖 global elo/odds |
| P1-E3 | 联赛差异化权重 | ✅ 部分 2026-07-19：`FootballMultiFactorEngine` 内建 epl/ucl/wc 等 profile（不改 EloOdds 全局 30/70） | registry 竞赛级 elo/odds 仍可再 seed |
| P1-E4 | 市场薄时降 odds 权重 | ✅ 2026-07-19：`odds_quality` + `market_liquidity` 从 verified links/snapshots 注入 `custom.liquidity_factor`（MultiFeatureBuilder / football adapter） | |

**设计注意：** 优先不修改已有 frozen 引擎契约的话，用 **新引擎注册 + EngineRegistry.select**，满足 Phase 零侵入精神。

### 3.2 世界杯 legacy 三引擎进入 Kernel

| ID | 项 | 现状 | 建议 |
|----|-----|------|------|
| P1-E5 | Dixon-Coles 进 Kernel | ✅ 2026-07-17：`DixonColesEngine` + `DIXON_COLES_ENGINE_ENABLED` | Elo→xG→Poisson+rho；读 `data/dixon_coles_params.json` |
| P1-E6 | GBM 进 Kernel | ✅ 2026-07-17：`GbmEngine` + `GBM_ENGINE_ENABLED` | 包装 legacy LightGBM；无模型时 Elo 基线 |
| P1-E7 | Ensemble 融合 | ✅ 2026-07-17：`EnsembleEngine` + `FOOTBALL_ENSEMBLE_ENGINE_ENABLED` | 反比 Brier（样本不足时等权）融合已注册足球引擎 |
| P1-E8 | Rule / 情境引擎 | ✅ 2026-07-20：`SituationalEngine` + soft adj；WorldCup/`_shared` 注入 group_context→custom；`SITUATIONAL_ENGINE_ENABLED` | `group_context_bridge` |

### 3.3 Phase 9 精度冲刺：跑通并应用

目标（设计文档）：NBA/MLB/NHL **~67% → 72–75%+**。

| ID | 项 | 现状 | 建议 |
|----|-----|------|------|
| P1-A1 | 历史数据 ingest | ✅ 2026-07-24：NBA 2023-24+2024-25（3962）；MLB 2024+2025 正赛/季后赛（6803）；NHL 2023-24+2024-25（3014） | 运营侧可再补更早赛季；校验 `kernel_match_*` |
| P1-A2 | EloTimeMachine 回放 | ✅ 2026-07-24：三盟 `seed_elo_ratings` 已写；applied K/HFA 重播后 basketball 33 / baseball 30 / hockey 34 | holdout 验收见 A4 |
| P1-A3 | Optuna 批量调参 | ✅ 2026-07-24：flat 特征 80 trials + **as-of rest/form 再 80 trials**；新结果 NBA 70.24% / MLB 54.22% / NHL 62.35%；`save_candidate` upsert 修复 UNIQUE；`rest_form.py` + match_loader + adapters 已落地 | 可加 trials / 赛季；勿自动 apply |
| P1-A4 | apply 优化参数 | ✅ 2026-07-24：NBA **5**/MLB **6**/NHL **7**；Elo HFA/K 接线 + re-seed；holdout applied vs settings Elo **+3.0/+1.2/+3.3pp**；NBA registry elo/form 已 re-apply 对齐 | 监控线上准确率；`eval_applied_params.py` |
| P1-A5 | 打开学习闭环 | ⏸ 暂缓：`PHASE3_LEARNING` OFF；kernel 仅 8 predictions / 1 outcome（&lt; MIN_SAMPLES=10） | 先积累 settled 预测样本，再开 EWMA；勿与 Optuna 权重冲突 |
| P1-A6 | 足球历史回测 | Phase 9 **明确不做**（ClubElo 无历史） | 另立「足球 Elo 时间机」项目：自有 K/HFA 回放 international_results.csv |

### 3.4 各体育因子增强清单

#### 足球（Kernel + Adapter 数据）

| ID | 因子 / 信号 | 数据侧 | 模型侧 |
|----|-------------|--------|--------|
| P1-F1 | form（近 N 场） | ✅ 部分 2026-07-25：`form_*` = 积分率 (3W+D)/(3N)（historical + club_form 经 enrich 统一写入）；✅ 2026-08-06：别名感知匹配（`club_form` 接入 `TEAM_ALIASES`，单赛事内解析、解析失败退回字符串比较）+ 加权近 N（`weighted_points_form_rate`，半衰期 5 场；enrich 优先取 `form_rate_weighted`，CSV 国家队路径无逐场序列自然退回扁平值）；真实覆盖率提升幅度待线上观测 | 引擎 form 差分未改 |
| P1-F2 | rest / 赛程密度 | ✅ 部分 2026-07-25：`matches_last_7d_*` + congest 由 7 日场次≥2 驱动（rest≤2 仅 fallback）；b2b 仍 rest≤1；✅ 2026-08-06：跨赛事合并（`_FOOTBALL_COMPETITIONS` 全量 fixture，每行按各自 competition 解析别名 → `custom.matches_merged_7d_*`）+ 3 天短周转窗口（`matches_merged_3d_*`）；引擎拥堵判定改读合并计数、3 天档按 b2b 幅度，`FOOTBALL_SCHEDULE_MERGE_ENABLED` 默认 OFF；✅ 2026-08-18：默认 OFF 的只读配置化赛程快照 provider，kernel 赛程优先、空/不可用时按赛事回退，严格校验、缓存、历史窗口和无数据库写入；真实国际比赛日覆盖已交付 2026-08-19：国家队赛程密度并入随仓库发布的国际赛果 CSV 真实比赛日（预选赛 / 友谊赛 / 洲际赛，kernel 仅有锦标赛赛程），按比赛日保守去重（同日即同场，不假设两源 fixture ID 兼容）后写入既有 `matches_merged_{7,3}d_*`，并附 `matches_intl_7d_*` / `schedule_intl_source` 诊断；俱乐部赛事不查该源，查询失败保留 kernel 计数，引擎与 `FOOTBALL_SCHEDULE_MERGE_ENABLED` 默认 OFF 未变 | 跨联赛合并赛程 / 更细窗口已交付 |
| P1-F3 | injury / availability | ✅ 部分 2026-08-17：默认 OFF 的 API-Football 联赛/赛季伤停快照（规范化球队名、成功快照缓存、`injury_source_*`）；实时源不可用时静态 Out + WC facts 回退。✅ 2026-08-18：默认 OFF 的球员分钟占比+身价占比可用性快照，严格合同和缓存；完整上下文按有界贡献增强原有 role 权重，缺上下文仍保持旧 role-only 结果，`injury_source_*=live_availability_provider`。生产仍需授权 provider URL/key，见 [provider contract](football-live-availability-provider-contract.md) | MultiFactor 公式/权重未改 |
| P1-F4 | h2h | ✅ 2026-07-25：historical 优先 + kernel 俱乐部交锋回退（当前主队视角）；✅ 2026-08-06：别名感知配对（自我对阵检查移到解析之后）+ 主客场分拆（`home_venue_*` 四计数 → `custom.h2h_home_venue_*`；引擎按 `alpha = min(1, n/4)` 混合，`FOOTBALL_H2H_VENUE_SPLIT_ENABLED` 默认 OFF）；✅ 2026-08-18：CSV + kernel 逐场合并，按比赛日、当前主队视角比分和主场归属保守去重后再截取近 20 场；中性场 CSV 不计入主场子集，源不可用时保留另一源 | 小权重已在 multi-factor |
| P1-F5 | 真实 xG | ✅ 部分 2026-08-17：默认 OFF 的配置化真 xG 赛季快照，严格只接收 `{"teams": [{"team": "…", "xg_per90": 1.72}]}` 合同；双方完整 live 命中才写 `xg_source=live_provider`，否则静态双边表→goals 代理回退。生产仍需授权 provider URL/key，见 [provider contract](football-live-xg-provider-contract.md) | MultiFactor soft xg 已在，公式/权重未改 |
| P1-F6 | PPDA / possession / shots | ✅ 部分 2026-08-17：默认 OFF 的配置化真实控球/射门/PPDA 赛季快照，严格合同、双边完整命中才写 `style_source=live_provider`；否则静态双边表→form_share 代理回退。生产仍需授权 provider URL/key，见 [provider contract](football-live-style-provider-contract.md) | MultiFactor soft possession 已在，公式/权重未改 |
| P1-F7 | 场地 / 旅行 / 海拔 / 天气 | ✅ 部分 2026-08-02：俱乐部城市表 + 稀疏海拔表 fill-only（`altitude_source=static_table`）；travel soft 俱乐部可解析；静态气候 fill（`weather_source=static_climate`）+ 实时天气预报源已交付（`live_weather_for_match` → `weather_source=live_forecast`，Open-Meteo 风格无密钥 JSON，horizon/TTL 可配，失败静默回退静态）；多源气象已交付 2026-08-18：可选第二气象源（`football_live_weather_service`，默认关闭，独立 URL/密钥/TTL/字节上限，严格校验共享条件词表）+ 确定性共识（单源原样、双源温差 ≤5°C 取均值、超差以主源为准），provenance 写 `weather_source_count` / `weather_agreement`，特征契约与 MultiFactor 未变 |
| P1-F8 | 裁判 | ✅ 部分 2026-08-17：默认 OFF 的配置化裁判赛季主胜率快照，严格合同、规范化姓名和缓存；显式字段优先，live 命中写 `referee_source=live_provider`，否则静态 bias map 回退。生产仍需授权 provider URL/key，见 [provider contract](football-live-referee-provider-contract.md) | MultiFactor referee 公式/权重未改 |

#### 篮球（NBA）

| ID | 项 |
|----|-----|
| P1-B1 | ✅ 部分 2026-07-24：静态 Out 名单 + 角色加权 `injury_impact_*`（adapter player/custom 双写 + FeatureBuilder 透传）。已交付 2026-08-19：`nba_live_injury_service` 可选实时名单源（默认关闭；bearer 认证、仅 http/https、限长响应、严格校验、重复队名整体拒绝、仅缓存有效快照、按 URL 分键）；仅 out/inactive/suspended 计为缺阵，questionable/probable/day-to-day 不计；角色权重与影响公式仍留在 `nba_injury.py`，未识别档位交回该模块的 bench 默认；adapter 优先采用已连通的实时源并记录 `custom.injury_source_{home,away}`（`live_provider`/`static_table`），禁用/传输失败/快照被拒/服务抛错/该队无数据均退回静态表，两者皆无则不写入任何键；契约见 `docs/dev/nba-live-injury-provider-contract.md`，生产启用仍待授权数据源 |
| P1-B2 | ✅ 部分 2026-07-20：`b2b_home/away` + BasketballEngine rest 额外惩罚 |
| P1-B3 | ✅ 部分 2026-07-20：`team_geo` + adapter 注入 `travel_km_away`/时区 + BasketballEngine `travel` |
| P1-B4 | ✅ 部分 2026-07-24：30 队静态 ORtg/DRtg → `custom` + BasketballEngine `net_rating` soft。已交付 2026-08-19：`nba_live_ratings_service` 可选赛季动态效率源（默认关闭；bearer 认证、仅 http/https、赛季起始年份 query 解析、限长响应、严格校验、重复队名整体拒绝、仅缓存有效快照、按解析后 URL 分键）；供应商必须提供 points / points_allowed / **真 possessions**，ORtg/DRtg 由回合数在本地换算，仅带预先算好的评分而无回合样本的载荷直接拒绝；换算值超出 `[80,140]` 视为单位错误整体拒绝；结构性错误整体拒绝，但样本不足 `NBA_LIVE_RATINGS_MIN_POSSESSIONS`（默认 500 回合）只丢该队并退回静态表；两侧必须同源并记录 `custom.ratings_source`（引擎吃 ORtg−DRtg 差值，实时赛季水平与静态多年水平混用会凭空造出优势），单侧命中亦退回静态；`net_rating` 公式与权重未变；契约见 `docs/dev/nba-live-ratings-provider-contract.md`，生产启用仍待授权数据源 |
| P1-B5 | ✅ 部分 2026-07-20：季后赛 `NBA_ELO_HFA_PLAYOFF` + 主场 0.55；回测验证最优 K/HFA 仍待 Phase9 |

#### 棒球（MLB）

| ID | 项 |
|----|-----|
| P1-M1 | ✅ 2026-07-24：probable SP（v1.1 feed / schedule hydrate）ERA/WHIP + relief-only IP 加权 `bullpen_era_*` + team ERA；league-avg 回退 |
| P1-M2 | ✅ 部分 2026-08-19：30 队静态 runs `park_factor`（+ Athletics 别名）+ BaseballEngine `park` soft；新增可选 `mlb_live_park_service` 实测球场系数源（默认关闭），必须提供主/客场次与双方合计得分，`(home_runs/home_games)/(road_runs/road_games)` 本地计算 → 只带预计算 factor 的响应一律拒绝；`[0.70,1.40]` 越界整快照拒绝；结构损坏拒整快照、场次不足（`MLB_LIVE_PARK_MIN_GAMES`，主客双向）仅弃该球场；球场为单一场馆属性故无需 same-source 配对；`custom.park_source` 记录来源；契约见 `docs/dev/mlb-live-park-provider-contract.md`。HR park factor 仍待（需新引擎因子/权重）、L-R 打者左右分野仍待（缺打线打者手别，适配器只有 SP `pitchHand`） |
| P1-M3 | ✅ 2026-07-24：v1.1 feed weather（F→C + wind mph）→ custom/env；Open roof 才注入引擎 soft；dome 降级 |
| P1-M4 | ✅ 2026-07-24：team hitting splits vs LHP/RHP（`vl,vr` OPS）+ SP `pitchHand` → `platoon_ops_*` / `platoon_advantage_home`；引擎 soft 已接线 |

#### 冰球（NHL）

| ID | 项 |
|----|-----|
| P1-H1 | ✅ 已交付 2026-08-19：club-stats 汇总 GF/GA/SF/SA + shot_share→`corsi_pct_*`（soft xG=0.09×SF）作为回退；新增默认关闭的 `nhl_live_xg_service` 真实 5v5 数据源，要求 5v5 出场时间 + 真 xGF 与/或真 corsi 事件计数，xGF/60 与 CF% 本地计算（仅带预先算好的比率一律拒绝），`[1.0,4.5]` / `[0.30,0.70]` 单位与合理性区间越界即整份拒绝，结构性错误拒整份、样本不足仅弃该队；每个指标必须双方同源，measured-xG-only 时清空 corsi 代理以免代理压过真实数据，`custom.skating_source` 记录来源；HockeyEngine `attack_share` 公式与权重不变；契约见 `docs/dev/nhl-live-5v5-provider-contract.md` |
| P1-H2 | ✅ 部分 2026-07-20：NHL `b2b_*` + HockeyEngine rest 额外惩罚 |
| P1-H3 | ✅ 部分 2026-07-20：`team_geo` NHL 城市 + HockeyEngine `travel`（含跨加跨区） |

### 3.5 赔率与市场信号

| ID | 项 | 说明 |
|----|-----|------|
| P1-O1 | 多玩法 | ✅ 部分 2026-08-19：足球/NBA/NHL/MLB 软 totals（独立泊松）+ FE；修复截断缺陷——原实现在固定 `0..10` 每方比分网格上求和，NBA 尺度（每方约 110 分）几乎没有概率质量落在网格内，导致每场篮球赛 `p_over=0.0`／`p_under=1.0` 直接展示给用户；改为「两个独立泊松之和仍是泊松」的一维总分分布（`math.lgamma` 对数空间、按均值缩放的 `lam + 10*sqrt(lam)` 上界，尾部精度不随总分增大而退化），棒球 0.4674→0.4769、冰球 0.4707→0.4711、足球四位小数不变；删除声明却从未传递的 `max_g` 参数；新增 `tests/test_soft_totals_distribution.py`（含与显式二维卷积的交叉验证）并收紧原本空洞的 `test_basketball_soft_totals`，两者对旧实现均失败；输出键、取整、盘口语义、引擎公式与权重均未改动；整数盘口上恰好命中总分仍计为小（220 线约 2.7% 质量），真盘口/亚盘仍待 ｜ ✅ 真盘口 2026-08-20：`market_totals_service` 可选默认关闭的真实大小球盘口线数据源，覆盖足球/NBA/NHL/MLB。动机是结构性缺陷——三大北美联赛引擎按 `league_avg/2 ± margin/2` 生成比分，主客场之和恒等于 `league_avg`，而软 totals 恰以 `line=league_avg` 询价，故期望总分与盘口线在构造上完全相同、`p_over` 退化为每运动常数（篮球实测在 margin 为 0/+5/+15/−12 时均为 0.4821）；足球因 `_probabilities_to_scores` 施加平局因子而总分会相对固定 2.5 线移动，是唯一例外。供应商须同时给出盘口线与**两侧十进制赔率**：单独一个线是没有依据的数字，两侧报价才构成市场；赔率在本地去水，overround 须落在 `(1.00, 1.30]`，去水后大球隐含概率须接近均势（盘口线按定义就是庄家平衡后的水位），线本身须落在所替代基准的 `[0.5×, 2.0×]` 单位带内。结构性破损行否决整份快照；两侧赔率显式为 `null` 是真实存在但暂未开盘的市场，仅该场次留空。请求日期严格要求 `YYYY-MM-DD` 并规范化后再发出——供应商拿到时间戳可自行忽略并返回另一天的盘面，而错日快照看起来完全合法却在给错误场次报价。已接入五处 adapter 入口（MLB/NHL/NBA、足球 `fetch_elo_and_odds`、以及自行构造 `custom` 的 `world_cup_adapter`），并以 wiring 测试逐一钉死，避免「能力已实现但无人调用」；`line_source` 记录 `market_provider`／`league_average`，FE 面板加盘口来源徽标、盘口隐含大对比块，并在线与期望总分数值重合时显式提示该比例不含本场信息。引擎公式、权重、输出键与默认行为均未改动。亚盘仍待：需要 Skellam 一类净胜分分布模型（引擎均未提供）以及尚不存在的 FE 消费方，否则等于再发布一个无从比较的数字 |
| P1-O2 | 多庄家离散度 | ✅ 部分 2026-07-20：`odds_dispersion_from_books` + TraditionalOddsStore 注入 + confidence damp |
| P1-O3 | 赔率时效 | ✅ 部分 2026-07-20：Edge `stale` + `review_priority`；list API/FE 表按优先级排序展示 |
| P1-O4 | 传统赔率 vs 预测市场 | ✅ 部分 2026-07-20：图表 + 最新价差表（≥5pp 高亮）；全链路样例验收仍待 |
| P1-O5 | Edge → 决策 | ✅ 部分 2026-07-20：`review_priority` 软降级 act→provisional/watch；rationale 标注；FE 卡片徽章 |

### 3.6 置信度与可解释性

| ID | 项 |
|----|-----|
| P1-X1 | ✅ 部分 2026-07-20：`confidence.compute_confidence`（strength+completeness+agreement+market damp）；全运动引擎接入；ECE 桶校准仍属后续 |
| P1-X2 | ✅ 部分：FactorBreakdownTable 已展示 explanation；2026-07-20 补全 situational/injury/h2h 中文名 |
| P1-X3 | ✅ 部分 2026-07-20：confidence_breakdown 入 betting_analysis + SportConfidencePanel 优先读 API |

---

## 4. P1/P2 — 事件情报与 Reality Filter 闭环

### 4.1 V2 路线图中仍「骨架强于闭环」的部分

| ID | 项 | 说明 |
|----|-----|------|
| P1-V1 | 市场价格一等公民 | ✅ 2026-07-20：链接 + 持续 snapshot（`_job_capture_market_snapshots` 调度）+ `MarketSnapshotStore.audit_summary` + `GET /sport-markets/links/{id}/audit`/`/matches/{id}/audit` + FE `MarketPriceAuditPanel` |
| P1-V2 | 已验证 event↔contract 链接率 | ✅ 部分 2026-07-20：auto-verify API + PendingReviewQueue dry-run/执行；评测集/吞吐仍待 |
| P1-V3 | 模型/市场谁错 | ✅ 部分 2026-07-20：分歧诊断 + factor_drivers 归因（explanation top impact）；端到端样例仍待 |
| P1-V4 | Decision Gate | 点时冻结已有；是否需要「修订预测」层（见 BY-DESIGN） |
| P1-V5 | 条件校准 | ✅ 部分 2026-07-20：confidence+stage 分桶 + POST /predictions/calibration/conditional；apply 默认 OFF |
| P2-V6 | 证据分解与 source trust | ✅ 部分 2026-07-20：`source_trust_registry_store` + `domain_reliability_store` + resolve-time 反馈钩子 + `domain_reliability_cli`；独立的 `evidence_decomposition` 模块未抽（评分仍在 `evidence_scoring_service` 内） |
| P2-V7 | 结论挑战门（challenge gate） | ✅ 2026-07-20：`conclusion_challenge_service` + 事件/世界杯适配器 + `review_queue_detectors` 集成 + 测试 |

### 4.2 发现源与结算

| ID | 项 | 说明 |
|----|-----|------|
| P2-D1 | Opinion / Predict.fun | 需 API key；无 key fail-closed |
| P2-D2 | Probable | 等官方 API/indexer 验证后再接 |
| P2-D3 | 链上 adapter 进 auto-resolve | 当前不参与自动结算；设计 resolution 适配器 |
| P2-D4 | Kalshi 结算产量 | 历史曾近零；持续监控 resolved 抓取与匹配 |
| P2-D5 | Limitless 等公共源质量 | 去重、类别推断、垃圾市场过滤 |

### 4.3 世界杯 / 体育事实层（事件侧）

| ID | 项 |
|----|-----|
| P2-W1 | 结构化 facts 定时 bundle import（flag 控制） |
| P2-W2 | 非市场类体育事件的 commitment / 校准路径（不出 prediction_store 时） |
| P2-W3 | 出线/晋级/纪律类确定性结算规则覆盖率 |
| P2-W4 | AI 只解释结构化事实，禁止模型「猜」红黄牌计数 |

---

## 5. P1/P2 — Sports 市场桥与交易决策层

| ID | 项 | 说明 |
|----|-----|------|
| P1-SB1 | 三层匹配准确率评测集 | ✅ 部分 2026-07-20：`scripts/eval_sport_market_matching.py` 评测脚本（rule/LLM/manual，输出 P/R/F1）+ `data/eval/sport_market_link_eval.sample.jsonl` 样例 6 case + 测试；真实标注集扩充与回归 CI 化仍待 |
| P1-SB2 | Edge Detector 端到端样例 | ✅ 部分：`POST .../detect` + UI 重算按钮 | 仍需真实链接+预测样例验收 |
| P1-SB3 | Recommendation 策略可解释 | ✅ 部分：开放决策列表 + 比赛详情推荐卡 + 503 提示 | 阈值/guardrail 深度验收仍待运营样例 |
| P1-SB4 | Settlement 反馈与 Phase3 校准隔离验收 | ✅ 部分：历史表手动重算 + 校准面板 503 提示 | 隔离性靠既有后端设计；需实盘样例 |
| P2-SB5 | Futures / 冠军市场覆盖 | ✅ 2026-07-19：扩展 series 前缀 + multi_leg_integrity + meta/coverage UI |
| P2-SB6 | `/trades` 与真实 edge 定义对齐 | ✅ 2026-07-19：directional_edge + stats edge_definition + UI |

**产品边界重申：** 推荐与 edge 是**决策辅助**，不是自动下单。

---

## 6. 前端

### 6.1 Phase 15 与完成度

Phase 15 设计曾写「Edge/WS/优化/结算几乎无 UI」；代码侧已出现：

- `use-edges`、Edge 表格/时间线/详情  
- `RealtimePriceTable`  
- Optimization 触发 / 状态 / apply  
- Settlement 重算按钮  
- 导航「Edge 偏离」  

| ID | 项 | 说明 |
|----|-----|------|
| P1-FE1 | **Phase 15 验收清单** | ✅ 部分：详情/优化/引擎 + markets 桥接提示 + pending auto-verify UI；其余页人工点检仍待 |
| P1-FE2 | WebSocket `updates[]` | ✅ 2026-07-17：`buildWsUrl` 开发指向 :8000；表消费完整字段；disabled 文案 | 需 PHASE10 + 调度推送才有真数据 |
| P1-FE3 | match 详情 tabs | ✅ tabs 齐全；引擎下拉 + 因子分解展示 | 数据一致性依赖后端 flag |
| P2-FE4 | 事件 `/edges` vs 体育 `/sports/edges` | ✅ 2026-07-19：DomainScopeBanner + 导航/标题文案 |
| P2-FE5 | 学习页展示 apply 前后权重（与优化联动） | ✅ 2026-07-19：apply weight_diff + 学习「已应用权重」tab |
| P2-FE6 | 世界杯专题页与 Kernel 多体育列表关系 | ✅ 2026-07-19：SportTrackBanner + 导航入口与高亮修复 |
| P3-FE7 | 无障碍与样式复用 | ✅ 2026-07-19：ui-classes + ScrollableTable；analyze/resolve/sports 表单与宽表 |
| P3-FE8 | 回测结果可视化 | ✅ 2026-07-19：optimize_sync 指标 + BacktestResultsPanel 图表/表 |
| P2-FE9 | Operator Key 长期方案 | ✅ 部分 2026-07-19：`operator-credentials` + 清除/遮罩/事件同步 + Runbook；BFF 会话仍属架构升级 |

### 6.2 事件情报前端增强

| ID | 项 |
|----|-----|
| P2-FE10 | 决策机会页与 diagnosis / actionable recommendation 字段对齐 | ✅ 2026-07-18：DecisionCard 展开诊断/校准/quality overlays |
| P2-FE11 | 质量运营告警可跳转 + 校准文案区分 | ✅ 2026-07-18：anomaly event_ids/href；历史 vs Kernel 文案 |
| P2-FE12 | 历史复盘按类别 / 分桶对比 | ✅ 2026-07-18：SegmentComparePanel + buckets API（引擎对比在学习页） |
| P3-FE13 | 热新闻条接真实 movers 标题 | ✅ 2026-07-18：AppNav HotNewsTicker → `/events/movers` |

---

## 7. 数据、存储与工程债务

| ID | 优先级 | 项 | 说明 |
|----|--------|-----|------|
| E1 | P2 | `event_store.json` 全量重写 | 规模债；归档/TTL 或迁 SQLite |
| E2 | P2 | 跨 JSON/SQLite 无硬 FK | dangling 监控已有；长期统一存储 |
| E3 | P2 | 限流多实例 | 进程内计数；可信反代 IP 或 Redis |
| E4 | P2 | API 与 scheduler 双进程写文件 | 文件锁已部分加固；仍建议关键路径 SQLite 化 |
| E5 | P3 | API 版本前缀 `/v1` | BY-DESIGN 可接受；公开前再议 |
| E6 | P2 | 世界杯 pipeline 与 Kernel 双轨 | 迁移策略：legacy 薄包装 / 只读 / 最终删除 |
| E7 | P3 | 类型注解与中英注释统一 | 长尾整洁 |
| E8 | P2 | 指标与告警 | ✅ 部分 2026-07-20：`scheduler_failure_alert_dispatcher` + Grafana JSON + RUNBOOK Monitoring 小节（/metrics 系列、drift/scheduler 告警 flag、eval CLI）+ `verify_local_stack` 探测 quality-metrics；生产 webhook 仍需运营配置 |
| E9 | P3 | 生成类型 CI | ✅ 2026-07-20：`.github/workflows/ci.yml` 含 `type-sync-check` job 跑 `python -m scripts.generate_types --check` |
| E10 | P2 | 测试数据隔离 | Phase 13 已修 kernel 单例；持续保证并行/逆序稳定 |
| E11 | P3 | 文档索引更新 | ✅ 2026-07-20：`docs/README.md` 已链入本 backlog |

---

## 8. 运维与部署

| ID | 优先级 | 项 |
|----|--------|-----|
| O1 | P0 | Runbook 演练：备份 / restore / health / dead-man |
| O2 | P1 | 容器资源限制与仅本机绑端口 + 反代 TLS（历史已修部分，部署时确认） |
| O3 | P1 | systemd 沙箱路径覆盖所有 DB/日志/备份写路径 |
| O4 | P2 | 多环境 `.env.staging` / `.env.production` 检查清单自动化 |
| O5 | P2 | LLM 成本与 discover limit 护栏看板 |
| O6 | P3 | 依赖审计（pip-audit / npm audit）周期 |

---

## 9. 质量、评测与研究

| ID | 项 | 说明 |
|----|-----|------|
| Q1 | Model Eval Lab 常规化 | 固定评测集、版本号、发布门槛 |
| Q2 | Replay harness | 决策流水线回放 + HTML 报告运维化 |
| Q3 | Domain reliability 反馈 | 来源域对 Brier 的贡献进权重 |
| Q4 | 特征 flag 影响分析脚本 | ✅ 2026-07-20：`scripts/analyze_feature_flag_impact.py` + `tests/test_analyze_feature_flag_impact.py` + `_cli.py` |
| Q5 | 抖音三模型后续 | DC/GBM/BTD 阶段已完成；下一跳是 xG 与 Kernel 集成（见 §3） |
| Q6 | 校准漂移告警 | ✅ 2026-07-20：`drift_alert_dispatcher` 三通道（webhook + Sentry breadcrumb + log）+ per-code 冷却 + `DRIFT_ALERTS_ENABLED` flag（默认 OFF）；`/quality-metrics/drift` 路由 |
| Q7 | 人工 review queue SLA | pending 链接 / 质量告警消化节奏 |

---

## 10. 明确不在范围内 / BY-DESIGN（改前先决策）

| 项 | 现状 | 说明 |
|----|------|------|
| 自动下注 / 资金管理 | 永不做（产品边界） | 哲学与世界杯设计一致 |
| 「主力受伤 = -10%」硬编码 | 禁止 | 事实确定性，影响由模型+校准 |
| `AUTO_VERIFY_THRESHOLD=1.0` | 仅精确匹配自动核验 | fail-closed |
| 一事件一预测永久冻结 | `ON CONFLICT DO NOTHING` | commitment；大 edge 变化不重冻 |
| `resolve_event` 覆盖 outcome | 无版本史 | 承诺模型语义 |
| Phase 9 不做足球回测 | ClubElo 限制 | 需独立项目 |
| 无 `/v1` API 前缀 | 仅 `/api/*` | 当前规模可接受 |

---

## 11. 建议实施路线图（分档）

### 第 0 档 — 1～2 天：系统「开灯」

1. P0-S1 密钥轮换（若适用）  
2. P0-C1～C5 配置与调度  
3. §2.3 闭环验收 5 条  
4. P1-FE1 Phase 15 点检  

### 第 1 档 — 精度主航道（1～2 个迭代）

1. **P1-E1** 足球多因子进引擎  
2. **P1-A1～A4** Phase 9 历史数据 + 调参 + apply  
3. **P1-A5** 谨慎打开学习闭环  
4. **P1-SB1～SB2** 市场桥与 Edge 样例验收  

### 第 2 档 — 模型广度

1. P1-E5～E7 DC/GBM/Ensemble 进 Kernel  
2. P1-F5～F7 真实 xG、场地、旅行喂数  
3. P1-O1～O3 赔率深化  
4. P1-V3 / P1-V5 诊断与条件校准  

### 第 3 档 — 平台化

1. E1～E4 存储与多实例  
2. E6 消灭双轨 pipeline  
3. P2-D3 链上结算  
4. P2-FE9 BFF 鉴权  
5. P3-FE8 回测可视化  

---

## 12. 若只选三件事（决策默认）

| 排序 | ID | 做什么 | 理由 |
|------|-----|--------|------|
| 1 | P1-E1 | 足球引擎吃满 FeatureSet | 特征已建好，改融合层即可抬天花板 |
| 2 | P1-A* | Phase 9 跑通并 apply | 唯一写明 67%→72%+ 的路径 |
| 3 | P0-C* + 闭环验收 | Flag 与 resolved 样本 | 否则线上永远测不出优化是否有效 |

---

## 13. 全量 ID 索引（速查）

### 安全与配置
P0-S1, P0-S2, P0-S3, P0-C1, P0-C2, P0-C3, P0-C4, P0-C5  

### 引擎与精度
P1-E1 … P1-E8, P1-A1 … P1-A6, P1-F1 … P1-F8, P1-B1 … P1-B5, P1-M1 … P1-M4, P1-H1 … P1-H3, P1-O1 … P1-O5, P1-X1 … P1-X3  

### 事件情报与 V2
P1-V1 … P1-V5, P2-V6, P2-V7, P2-D1 … P2-D5, P2-W1 … P2-W4  

### 体育市场桥
P1-SB1 … P1-SB4, P2-SB5, P2-SB6  

### 前端
P1-FE1 … P1-FE3, P2-FE4 … P2-FE6, P2-FE9 … P2-FE12, P3-FE7, P3-FE8, P3-FE13  

### 工程 / 运维 / 质量
E1 … E11, O1 … O6, Q1 … Q7  

---

## 14. 维护约定

- 完成某 ID 后：在本文件对应行标注 `✅ YYYY-MM-DD` 与 PR/提交说明，或移入 CHANGELOG。  
- 新增可做项：分配新 ID，写入对应章节，并更新 §13 索引。  
- 与 `docs/superpowers/plans/*` 冲突时：以**已合并代码行为**为准，并回写本清单「现状」列。  
- 不在此文档展开具体补丁级实现；开工时再写 plan（`docs/superpowers/plans/`）或 issue。

---

## 15. 附录：关键代码锚点

| 主题 | 路径 |
|------|------|
| FastAPI 入口 | `backend/app/main.py` |
| API 路由聚合 | `backend/app/api/router.py` |
| 事件情报 | `backend/app/services/event_intelligence_service.py` |
| 调度 | `backend/app/core/scheduler.py` |
| Kernel 编排 | `backend/app/kernel/prediction_kernel.py` |
| 协议 | `backend/app/kernel/protocols.py` |
| Elo+赔率引擎 | `backend/app/kernel/engines/elo_odds_engine.py` |
| 因子注册 | `backend/app/kernel/factor_registry.py` |
| 足球特征 | `backend/app/sports/football/feature_builder.py` |
| 世界杯 legacy 引擎 | `backend/app/services/world_cup_engines/` |
| 前端导航 | `frontend/src/components/app-nav.tsx` |
| 体育 API hooks | `frontend/src/lib/sports-api/` |
| 配置与 flag | `backend/app/core/config.py`、`backend/.env.example` |

---

*本文档由 2026-07-17 全项目通读与既有审查/Phase 设计综合生成，反映当时仓库状态；实施前请以当前分支代码复核。*
