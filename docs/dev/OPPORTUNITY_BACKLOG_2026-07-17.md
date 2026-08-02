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
| 世界杯 legacy | DC / BTD / GBM / Rule / EloOdds | 更丰富 | **未统一注册进 Kernel EngineRegistry** |

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
| P1-F1 | form（近 N 场） | ✅ 部分 2026-07-25：`form_*` = 积分率 (3W+D)/(3N)（historical + club_form 经 enrich 统一写入）；加权近 N / 覆盖率与别名仍待 | 引擎 form 差分未改 |
| P1-F2 | rest / 赛程密度 | ✅ 部分 2026-07-25：`matches_last_7d_*` + congest 由 7 日场次≥2 驱动（rest≤2 仅 fallback）；b2b 仍 rest≤1 | 跨联赛合并赛程 / 更细窗口仍待 |
| P1-F3 | injury / availability | ✅ 部分 2026-07-25：静态 Out + 角色加权 `injury_impact_*`（player/custom 双写；WC 源仅 static None fallback）；真伤病 API 与分钟/身价加权仍待 |
| P1-F4 | h2h | ✅ 部分 2026-07-25：historical 优先 + kernel 俱乐部交锋回退（当前主队视角）；主客场分拆/别名/合并源仍待 | 小权重已在 multi-factor |
| P1-F5 | 真实 xG | ✅ 部分 2026-07-26：静态 `xg_for_team` 双方命中覆盖 `custom.xg_*`（goals 代理回退）；真 xG API 仍待 | MultiFactor soft xg 已在 |
| P1-F6 | PPDA / possession / shots | ✅ 部分 2026-07-26：静态 `stats_for_team` 双方命中覆盖 `custom.possession_*`/`shots_*`/`ppda_*`（form_share 代理回退）；真统计 API 仍待 |
| P1-F7 | 场地 / 旅行 / 海拔 / 天气 | ✅ 部分 2026-08-02：俱乐部城市表 + 稀疏海拔表 fill-only（`altitude_source=static_table`）；travel soft 俱乐部可解析；静态气候 fill（`weather_source=static_climate`）+ 实时天气预报源已交付（`live_weather_for_match` → `weather_source=live_forecast`，Open-Meteo 风格无密钥 JSON，horizon/TTL 可配，失败静默回退静态）；真多源气象 API 仍待 |
| P1-F8 | 裁判 | ✅ 部分 2026-07-26：静态 `bias_for_referee` + enrich fill-only（`referee_source=static_map`）；真裁判统计源与库列仍待 |

#### 篮球（NBA）

| ID | 项 |
|----|-----|
| P1-B1 | ✅ 部分 2026-07-24：静态 Out 名单 + 角色加权 `injury_impact_*`（adapter player/custom 双写 + FeatureBuilder 透传）；真实时名单源仍待 |
| P1-B2 | ✅ 部分 2026-07-20：`b2b_home/away` + BasketballEngine rest 额外惩罚 |
| P1-B3 | ✅ 部分 2026-07-20：`team_geo` + adapter 注入 `travel_km_away`/时区 + BasketballEngine `travel` |
| P1-B4 | ✅ 部分 2026-07-24：30 队静态 ORtg/DRtg → `custom` + BasketballEngine `net_rating` soft；真 possessions / 赛季动态源仍待 |
| P1-B5 | ✅ 部分 2026-07-20：季后赛 `NBA_ELO_HFA_PLAYOFF` + 主场 0.55；回测验证最优 K/HFA 仍待 Phase9 |

#### 棒球（MLB）

| ID | 项 |
|----|-----|
| P1-M1 | ✅ 2026-07-24：probable SP（v1.1 feed / schedule hydrate）ERA/WHIP + relief-only IP 加权 `bullpen_era_*` + team ERA；league-avg 回退 |
| P1-M2 | ✅ 2026-07-24：30 队静态 runs `park_factor`（+ Athletics 别名）+ BaseballEngine `park` soft；HR/L-R/动态源仍待 |
| P1-M3 | ✅ 2026-07-24：v1.1 feed weather（F→C + wind mph）→ custom/env；Open roof 才注入引擎 soft；dome 降级 |
| P1-M4 | ✅ 2026-07-24：team hitting splits vs LHP/RHP（`vl,vr` OPS）+ SP `pitchHand` → `platoon_ops_*` / `platoon_advantage_home`；引擎 soft 已接线 |

#### 冰球（NHL）

| ID | 项 |
|----|-----|
| P1-H1 | ✅ 部分 2026-07-24：club-stats 汇总 GF/GA/SF/SA + shot_share→`corsi_pct_*`；soft xG=0.09×SF；真 5v5 xG/corsi 源仍待 |
| P1-H2 | ✅ 部分 2026-07-20：NHL `b2b_*` + HockeyEngine rest 额外惩罚 |
| P1-H3 | ✅ 部分 2026-07-20：`team_geo` NHL 城市 + HockeyEngine `travel`（含跨加跨区） |

### 3.5 赔率与市场信号

| ID | 项 | 说明 |
|----|-----|------|
| P1-O1 | 多玩法 | ✅ 部分 2026-07-20：足球/NBA/NHL/MLB 软 totals（独立泊松）+ FE；真盘口/亚盘仍待 |
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
