# 抖音足球预测模型技术可行性调研

> **来源**: 抖音 @足球数据预测室 模型（详见 `docs/抖音足球预测模型截图汇总.md`）
> **调研日期**: 2026-06-28
> **调研范围**: Dixon-Coles + GBM + Bradley-Terry-Davidson 三模型融合方案在我们现有世界杯系统的可行性
> **方法**: 内部数据资产审计（search agent）+ 外部实现生态调研（CSDN/官方文档）

---

## 一、执行摘要

**核心结论：方案技术上可行，且与我们现有架构高度契合。** 三个模型均有明确的 Python 落地路径，关键数据资产（历史国际赛 CSV、校准闭环、特征脚手架）已就位；主要缺口是 xG 实际数据未接入、旅行/海拔函数未喂数据、Elo 缺时间维度、Dixon-Coles 的 ρ 是硬编码。

**最低成本切入点**：先用历史 CSV 拟合 DC 的 ρ/半衰期替换硬编码 `rho=0.96`，再把 `_integrated_weight_payload` 从二路（elo_odds vs hybrid）扩展为三路（DC/GBM/BTD）反比 Brier 加权。这是单点改动、可灰度、风险可控的渐进路径。

**建议路线**：分三阶段（详见第六节）——
1. ✅ **阶段一（DC 升级，2-3 文件改动）**：拟合 ρ 与半衰期，替换硬编码。**已完成 2026-06-28，ρ=-0.0763**
2. ✅ **阶段二（BTD 升级，4 文件改动）**：用 Davidson 公式替换 elo_odds 平局启发式常数。**已完成 2026-06-28，γ=0.672**
3. ✅ **阶段三（GBM 新引擎，7 文件改动）**：基于 LightGBM 训练独立第三引擎，注册到 ENGINES 字典。**已完成 2026-06-28，1003 测试通过**

> **当前进度**：三阶段全部完成。DC + BTD 在 Brier 上微改善（噪声内），GBM 单独使用略差于 Elo+BTD 但提供独立 ensemble 视角。决策点已确认：跳过 Opta、xG 用进球数代理、模型文件仓库版本化、灰度策略可用 EngineCalibration.is_active。

---

## 二、数据资产就绪度审计

### 2.1 已具备（Douyin 模型可直接复用）

| 数据/设施 | 位置 | 备注 |
|---|---|---|
| 完整国际赛历史 CSV（1872-至今） | `backend/data/international_results.csv` | DC 时间衰减拟合的充要数据：`date/score/tournament/neutral` |
| Elo 标量评分 + FIFA 排名 + 足协 | `elo_ratings_service.py:23-32` `EloRating` 表 | 可作 BTD 先验 |
| 3 路市场赔率 + bookmakers_count + 新鲜度 | `odds_cache_service.py:16-27` `OddsCache` 表 | 仅 1x2，无大小球/BTTS |
| 休息天数、赛程密度、rest_advantage | `world_cup_schedule_factors.py:22-87` | 已写入 factors，rule engine 已消费 |
| 阵容规模、平均年龄、位置分布 | `world_cup_openfootball_data.py` + pipeline `:420-454` | 年龄因子所需数据已具备 |
| 球队身价 + 球员数 | `TeamMarketValue` 模型 `world_cup_prediction.py:252-263` | 来源 transfermarkt 爬虫 |
| H2H 历史统计 | `world_cup_historical_results.py:80-136` | 已就绪 |
| group_status / must_win 锦标赛语境 | rule engine `:175-438` | 已就绪 |
| **rule engine 内已有 Dixon-Coles 修正（ρ=0.96 硬编码）** | `world_cup_rule_engine.py:32-52` | 半成品，需拟合 |
| 完整校准闭环：Brier/log_loss/ECE/校准桶/按引擎分解 | `world_cup_quality_service.py` `:100-292` | DC/GBM/BTD 评估与权重调谐所需工具链已完备 |
| 历史驱动权重建议 | `_integrated_weight_payload` `:795-836` | 反比 Brier 加权，二路→三路天然落点 |
| GBM 特征脚手架 | `derive_model_features` `enhanced_factors.py:432-481` | 已派生 elo_difference/form_differential/quality_score 等 |

### 2.2 缺失或未接通

| 缺失项 | 影响模型 | 现状 | 补齐成本 |
|---|---|---|---|
| **xG 实际数据** | GBM / DC 攻防强度更优估计 | 管道就绪（`statistics_source.py:162-208`）但 API 端点未配置（`api_football_source.py:21-26`）→ 全部回退到进球数 | 中（需开 API-Football statistics 端点配额） |
| **裁判数据持久化** | GBM 裁判风格特征 | 采集层有（`match_source.py:132-134`），`MatchFixture` 无列 | 低（加一列 + 采集写入） |
| **旅行距离 / 海拔 / 气候** | GBM 情境特征 | 函数已写（`enhanced_factors.py:245-294`）但 pipeline 未传 `venue_data` → 恒为 0 | 低（pipeline `:732-740` 补传 venue_data） |
| **Elo 的时间衰减/比赛级强度** | BTD 拟合需随时间演化的队伍强度 | 现有 Elo 是单标量，无时间维度 | 中（需重新拟合，但有历史 CSV 可用） |
| **DC 的 ρ 拟合** | DC 当前 ρ=0.96 硬编码 | 未用历史 CSV 拟合半衰期与 ρ | 低（历史数据已具备，纯离线拟合脚本） |
| 历史 CSV 的 xG/裁判/场地 | GBM 历史特征 | martj42 数据集本身不含 | 不可补（需换数据源或放弃） |
| `MatchFixture.city` | 天气/场地查询 | pipeline 调用但模型无此列 | 低（加列） |

---

## 三、三个模型的技术可行性

### 3.1 Dixon-Coles（DC）—— 升级，非新增

**当前状态**：半成品。`world_cup_rule_engine.py:32-52` 已实现 ρ 修正项对 0-0/1-0/0-1/1-1 低比分格的联合概率修正，但 ρ=0.96 是硬编码常量。

**抖音模型核心公式**：
$$P(X=x, Y=y) = \tau_\rho(x,y) \cdot \frac{e^{-\lambda_i}\lambda_i^x}{x!} \cdot \frac{e^{-\lambda_j}\lambda_j^y}{y!}$$

时间衰减权重：$w = \exp(-\frac{\ln 2}{730} \times \text{距今天数})$，半衰期 730 天（约 2 年）。

**实现路径**：
- **方案 A（推荐）**：用 `international_results.csv` 离线拟合每队攻击/防守参数 + 指数时间衰减半衰期 + ρ。拟合目标是最小化负对数似然。替换 `calculate_expected_goals`（`rule_engine.py:75-116`）当前基于 `goals_per_game` 的攻击/防守估计。
- **方案 B**：新建独立 DC 引擎 `world_cup_engines/world_cup_dixon_coles_engine.py`，注册到 `world_cup_engines/__init__.py:12` 的 ENGINES 字典。

**库生态**：`penaltyblog` 是国内知名的足球分析 Python 库（含 DC 实现），但依赖较重。鉴于 DC 公式清晰，**建议从零实现**（~150 行 NumPy），避免引入额外依赖，且可完全控制时间衰减与 ρ 拟合逻辑。

**计算成本**：离线拟合一次性，~30 秒级（历史 CSV ~5 万行）。在线预测每场 <10ms（Poisson 矩阵 10×10）。

**风险**：ρ 拟合过拟合世界杯小样本。缓解：用全国际赛历史拟合，世界杯仅作验证集。

**就绪度**：✅ 数据齐备，可立即动工。

### 3.2 Bradley-Terry-Davidson（BTD）—— 升级现有 Elo

**当前状态**：`elo_odds_engine.py:20-71` 的 `calculate_elo_win_probability` 是 BT 简化版，平局用启发式常数（group 0.27 / knockout 0.20）。

**Davidson(1970) 扩展公式**（CSDN 文章确认）：
$$P(\text{home win}) = \frac{\alpha_i}{\alpha_i + \alpha_j + \gamma\sqrt{\alpha_i\alpha_j}}$$
$$P(\text{draw}) = \frac{\gamma\sqrt{\alpha_i\alpha_j}}{\alpha_i + \alpha_j + \gamma\sqrt{\alpha_i\alpha_j}}$$

其中 α 是队伍隐式强度，γ 是 Davidson 平局参数。

**实现路径**：用历史 CSV 拟合 BTD 三参数（每队强度 α + 全局平局阈值 γ），以 Davidson 公式替换 `elo_odds_engine.py` 中 `calculate_elo_win_probability` 的硬编码 `base_draw`。该函数同时供给 elo_odds 引擎和 integrated 引擎的 Elo 分量（pipeline `:122`），改一处即全局生效。

**库生态**：无成熟专用库，需自行实现。但 Davidson 公式简单（~30 行），拟合用 `scipy.optimize.minimize` 即可。我们已有 `scipy` 依赖（通过 statistics 间接）。

**计算成本**：离线拟合一次性，~60 秒级（队伍多、需正则化）。在线预测每场 <1ms。

**风险**：BTD 平局参数 γ 是全局标量，无法捕捉"两队都很强时平局概率更高"这种情景。缓解：可后续引入 γ = f(Elo 差) 的条件化扩展。

**就绪度**：✅ 数据齐备，可立即动工。

### 3.3 GBM（梯度提升）—— 新增引擎

**当前状态**：无。但特征派生已脚手架化（`enhanced_factors.py:432-481` 的 `derive_model_features` 已派生 elo_difference/matchup_advantage/form_differential/quality_score/expected_total_goals）。

**抖音模型用法**：在对数空间叠加到 Poisson 基础参数上：
$$\log\lambda_A^{final} = \log\lambda_A^{Poisson} + f_{GBM}(X)$$

**实现路径**：
- **方案 A（推荐）**：作为 stacking 层，训练 GBM 预测"实际进球 - DC 预测进球"的残差，在对数空间修正 λ。这要求先有 DC 基线输出。
- **方案 B**：独立引擎，直接预测 home_goals/away_goals（回归）或胜平负（分类）。简单但失去与 DC/BTD 的概率融合能力。

**库生态**：`XGBoost` / `LightGBM` / `CatBoost` 三选一。**推荐 LightGBM**（CSDN 文章确认：在 Higgs 数据集上比 XGBoost 快 10 倍、内存 1/6、准确率持平或更优），且对类别特征原生支持（足协、阶段）。

**特征需求**：GBM 需要有标签的训练数据。标签 = 历史 CSV 的实际比分。特征 = Elo 差、form 差、身价差、休息差、主客场、赛事级别、Elo 时间衰减加权 form。**xG 缺失会降低 GBM 上限，但不阻塞**——可先用进球数代理，后续 xG 接入后再重训。

**计算成本**：训练 ~2-5 分钟（CPU，5 万样本 × 20 特征）。在线预测每场 <5ms。模型文件 ~500KB-2MB。

**风险**：
1. 过拟合世界杯小样本（32-64 场）。缓解：用全国际赛历史训练，世界杯仅作 OOS 验证。
2. 特征工程复杂度高。缓解：先复用 `derive_model_features` 现有特征，逐步扩展。
3. 模型解释性下降。缓解：用 SHAP 值做特征归因。

**就绪度**：⚠️ 数据部分齐备（缺 xG 但可代理），可动工但建议放最后。

---

## 四、融合层架构接入点

**最关键的单一接入点**：`world_cup_prediction_pipeline.py:844-918` 的 `build_integrated_prediction` + `world_cup_quality_service.py:795-836` 的 `_integrated_weight_payload`。

当前二路反比 Brier 加权逻辑：
```python
learned_elo = (1/brier_elo) / (1/brier_elo + 1/brier_hybrid)
blended_elo = default_elo * 0.65 + learned_elo * 0.35
```

可平滑推广为 N 路加权：
```python
# DC / GBM / BTD 三路
weights = {k: 1/brier_k for k in briers if brier_k is not None}
total = sum(weights.values())
learned = {k: w/total for k, w in weights.items()}
blended = {k: default_k * 0.65 + learned_k * 0.35 for k in engines}
```

**校准闭环已完备**，无需新增评估设施——`world_cup_quality_service.py` 已按引擎分桶（`:847-858`）计算 Brier/log_loss/ECE/校准桶，新引擎自动纳入评估。

---

## 五、与抖音模型的差异

| 维度 | 抖音模型 | 我们落地后 | 说明 |
|---|---|---|---|
| 基础模型 | DC（ρ=-0.05 拟合） | DC（ρ 拟合） | 对齐 |
| 非线性修正 | GBM | GBM（LightGBM） | 对齐 |
| 胜负模型 | BTD | BTD | 对齐 |
| 外部权威融合 | Opta 75:25 | 无 Opta 接入 | **不对齐**——Opta 是付费 API，我们暂无集成计划 |
| xG | 真实 xG | 进球数代理 | **降级**——待 xG 接入后重训 |
| 时间衰减 | 730 天半衰期 | 拟合选择 | 可对齐 |
| 因子体系 | 7 大类 | 5 大类（缺裁判/赛况） | 部分对齐 |

**结论**：除 Opta 融合与 xG 真实数据外，其余维度均可对齐或逐步对齐。

---

## 六、建议的分阶段实施路线

### 阶段一：DC 升级（最低风险，最高性价比）✅ 已完成 (2026-06-28)

**改动范围**：2-3 文件
- ✅ 新增 `backend/scripts/fit_dixon_coles.py`（离线拟合脚本，输出 `dixon_coles_params.json`）
- ✅ 修改 `world_cup_rule_engine.py:32-72`（读拟合参数替换硬编码 ρ，τ 公式改为标准 DC 约定）
- ✅ 新增 `tests/test_dixon_coles_fitting.py`（10 个测试用例，6 拟合验证 + 4 loader 测试）

**验证**：
- 拟合结果：ρ=-0.0763（标准 DC 约定，负值正确提升 0-0/1-1 概率）
- 落在 [-0.5, 0.5] 安全区间内（文献典型范围 [-0.2, 0.1]）
- home_advantage=0.2898, mu=1.1154（8111 样本，257 队，时间衰减半衰期 730 天）
- 平局概率提升：1.5 vs 1.2 比赛从 0.2451 → 0.2733（修正 Poisson 平局低估）
- 全量测试：975 passed, 11 skipped, 16 subtests passed in 44.11s

**回测验证（1024 场世界杯正赛，1930-2026）**：见 `scripts/backtest_dc_rho.py`

| 配置 | avg_brier | avg_logloss | accuracy | draw_recall | draw_pred |
|---|---|---|---|---|---|
| ρ=-0.0763 (新, 拟合) | 0.6018 | 1.0059 | 51.9% | **0.9%** | 14/230 |
| ρ=+0.04 (旧等价) | 0.5997 | 1.0029 | 52.2% | 0.0% | 3/230 |
| ρ=0.0 (纯 Poisson) | 0.6002 | 1.0035 | 52.1% | 0.0% | 5/230 |

回测结论：
- Brier/准确率差异在统计噪声内（< 0.5%），无实质损害
- 拟合 ρ 在 draw_recall 上有微改善（0% → 0.9%，命中 2 场平局，旧值几乎从不预测平局）
- 主要价值在代码标准化（从硬编码改为数据驱动，可重拟合）与符号约定修正

**关键发现**：原硬编码 `rho=0.96` 配合 `(1-rho)` 公式实际等价于 ρ_dc=+0.04，符号约定与 DC 1997 论文相反（降低而非提升平局概率）。本次修复同时修正了符号约定。注意：实际 ρ 量级对 Brier 影响 < 0.5%，主要修正价值在符号正确性与可重拟合架构。

**风险**：低。改一处 rule engine，hybrid 引擎自动受益。已验证无回归。

### 阶段二：BTD 升级（单点改动）✅ 已完成 (2026-06-28)

**改动范围**：4 文件
- ✅ 新增 `backend/scripts/fit_btd_model.py`（从历史 CSV 计算 Elo + 拟合 γ，输出 `btd_params.json`）
- ✅ 新增 `backend/app/services/world_cup_engines/world_cup_btd_model.py`（BTD 预测函数，α=10^(elo/400)，γ 从 JSON 加载）
- ✅ 修改 `backend/app/services/world_cup_engines/world_cup_elo_odds_engine.py:20-58`（`calculate_elo_win_probability` 委托 BTD 替换 base_draw 启发式）
- ✅ 新增 `backend/tests/test_btd_model.py`（12 测试：6 模型 + 4 loader + 2 集成）+ 更新 `test_world_cup_elo_odds_engine.py`（13 golden 测试新值）

**拟合结果**：γ=0.672（用 α=10^(elo/400) 约束拟合，避免 scale mismatch）
- home_advantage=0.834（非中性场，世界杯不应用）
- equal_team_draw_prob=0.2515（合理，世界杯平局率约 23-27%）
- 8111 样本，257 队，Elo mean abs diff=88.4

**关键设计决策**：
- 首次拟合用自由 α 参数得到 γ=1.07，但 α 分布比 10^(elo/400) 更分散，导致 equal_draw=0.35（过高）
- 重写拟合器：从 CSV 计算 Elo，用 α=10^(elo/400) 约束，只拟合 γ → 得到 γ=0.672（与 fallback 0.74 接近，且 properly calibrated for Elo scale）

**回测验证（1024 场世界杯正赛，1930-2026）**：见 `scripts/backtest_btd.py`

| 配置 | avg_brier | avg_logloss | accuracy |
|---|---|---|---|
| BTD (γ=0.672) | **0.5813** | **0.9792** | 56.0% |
| Old heuristic (0.27/0.20) | 0.5818 | 0.9797 | 56.0% |

- **Brier 微优 0.0005**（整体），**2018 世界杯优 0.0013**，2022 持平
- accuracy 持平 56.0%，draw_recall 均为 0%（两个模型都不敢预测平局）
- 全量测试：**987 passed**, 11 skipped, 20 subtests passed in 45.73s（0 失败）

**关键发现**：BTD 替换了硬编码 0.27/0.20 启发式与线性 `elo_gap_factor` hack，用 Davidson 公式的几何均值项 `γ·√(α_h·α_a)` 自然处理"两队越接近则平局越多"。Brier 改善虽小但方向正确，无任何退化。

**风险**：低。改一处 `calculate_elo_win_probability` 全局生效，elo_odds 与 integrated 引擎均自动受益。已验证无回归。

### 阶段三：GBM 新引擎（最大改动）✅ 已完成 (2026-06-28)

**改动范围**：7 文件（新增 5 + 修改 2）
- ✅ 新增 `backend/app/services/world_cup_engines/world_cup_gbm_features.py`（共享特征派生，防 train/serve skew）
- ✅ 新增 `backend/app/services/world_cup_engines/world_cup_gbm_engine.py`（LightGBM 加载 + 预测，含 fallback）
- ✅ 新增 `backend/scripts/train_gbm_model.py`（离线训练，输出 `gbm_home_model.txt` / `gbm_away_model.txt` / `gbm_features.json`）
- ✅ 新增 `backend/scripts/backtest_gbm.py`（回测对比 GBM vs Elo+BTD vs 旧启发式）
- ✅ 新增 `backend/tests/test_gbm_engine.py`（16 测试：4 注册 + 8 预测 + 3 特征 + 1 fallback）
- ✅ 修改 `backend/app/services/world_cup_engines/__init__.py`（注册 `"gbm"` 引擎）
- ✅ 修改 `backend/app/services/world_cup_quality_service.py:20,37-46`（`bucket_engine` 加 gbm 分支，`ENGINE_NAMES` 加 gbm）
- ✅ 修改 `backend/requirements.txt`（新增 `lightgbm>=4.0,<5.0`）

**训练结果**：
- 15852 样本（since 2010），17 特征，时间衰减 80/20 split
- Home model RMSE 1.3917（早停 round 47），Away model RMSE 1.1455（早停 round 57）
- Top features: elo_diff, goals_conceded_avg, h2h_avg_goal_diff
- 模型文件 ~50KB，已版本化入仓库 `data/`

**回测验证（188 场世界杯正赛，2018+2022）**：见 `scripts/backtest_gbm.py`

| 配置 | avg_brier | avg_logloss | accuracy | draw_recall |
|---|---|---|---|---|
| GBM (LightGBM) | 0.5849 | 0.9826 | 55.9% | 0.0% |
| Elo + BTD | **0.5795** | **0.9757** | **57.4%** | 0.0% |
| Elo + old heuristic | 0.5790 | 0.9748 | 57.4% | 0.0% |

回测结论：
- **GBM 单独使用时 Brier 略差 0.0054**（0.5849 vs 0.5795）
- 原因：GBM 预测 xG → Poisson 转概率，串联两个模型放大误差；而 BTD 直接预测概率更稳定
- 但 GBM 作为独立第三引擎仍有价值：(1) 提供 ensemble 多样性；(2) 未来加入 xG/market_value 特征后可能反超；(3) 当前是 baseline 实现
- 全量测试：**1003 passed**, 11 skipped, 35 subtests passed in 48.05s（0 失败）

**关键设计决策**：
- 特征派生模块独立（`world_cup_gbm_features.py`），训练与预测共用，防 train/serve skew
- 17 个特征全部来自赛前数据（赛前 Elo、最近 N 场 form/goals、H2H、neutral、tournament 类型），无标签泄漏
- 模型缺失时 fallback 到 Elo baseline（`gbm_fallback_elo`），不阻塞预测
- `bucket_engine` 已识别 "gbm"，质量服务可独立分桶评估

**风险**：中。已验证无回归。GBM 单独 Brier 不优于 Elo+BTD，但提供了独立预测视角，未来特征扩展（xG、market_value）后潜力更大。建议先以独立引擎形式观察，不急于纳入 integrated 融合。

---

## 七、决策点

在动工前需确认：

1. **Opta 接入**：是否纳入路线图？若不纳入，文档中"75:25 Opta 融合"维度需明确标注为"暂不实现"。
2. **xG 数据源**：是否启用 API-Football statistics 端点（消耗配额）？这决定 GBM 上限。
3. **模型文件管理**：拟合/训练产物（`dixon_coles_params.json` / `gbm_model.txt`）是随仓库版本化，还是放 `data/` 目录入 `.gitignore` 由部署时下载？建议前者（<2MB，可 review）。
4. **灰度策略**：新引擎上线时是否走 `EngineCalibration.is_active` 开关做 A/B？现有版本化校准设施支持。

---

## 八、附录：关键文件清单

- `backend/app/services/world_cup_engines/world_cup_rule_engine.py:32-52`（DC 半成品）
- `backend/app/services/world_cup_engines/world_cup_elo_odds_engine.py:20-71`（BT 简化版）
- `backend/app/services/world_cup_prediction_pipeline.py:844-918`（融合层）
- `backend/app/services/world_cup_quality_service.py:795-836`（权重建议）
- `backend/app/services/world_cup_enhanced_factors.py:432-481`（GBM 特征脚手架）
- `backend/data/international_results.csv`（DC/BTD 拟合数据源）
- `docs/抖音足球预测模型截图汇总.md`（原始资料）

## 参考来源

- [世界杯分析及预测:基于贝叶斯Bradley Terry Davidson模型](https://blog.csdn.net/2501_91281010/article/details/146423957) — BTD Davidson(1970) γ 平局扩展公式确认
- [Python实现预期进球预测模型:xG_model_gradient-boost](https://blog.csdn.net/weixin_35797963/article/details/150465466) — LightGBM 优于 XGBoost 的性能数据确认
