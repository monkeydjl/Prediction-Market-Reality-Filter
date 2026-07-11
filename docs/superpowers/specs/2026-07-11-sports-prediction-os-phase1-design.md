# Sports Prediction OS — Phase 1: Prediction Kernel + WorldCup Adapter

> **Status**: Approved Design
> **Date**: 2026-07-11
> **Author**: Architecture Review
> **Predecessor**: World Cup Prediction Module (50+ backend files, 11 SQLite tables, 5 engines)

---

## 1. 背景与动机

### 1.1 现状

系统当前拥有一个自包含的世界杯预测模块，包含：

- **后端**: 50+ 个 `world_cup_*` 前缀文件，11 张 SQLite 表，2 个 API 路由组，5 个预测引擎
- **前端**: 单页 8 Tab，12 个组件，3 套 API 客户端，15 个测试文件
- **数据源**: Football-Data.org / API-Football / SportMonks / OpenFootball / The Odds API / Transfermarkt

### 1.2 问题

1. **样本瓶颈**: 世界杯每 4 年仅 64 场，无法形成有效的持续学习闭环
2. **耦合严重**: 预测引擎直接 import `world_cup_*` 服务，无法复用于其他赛事
3. **无学习闭环**: 预测结束后缺少自动化的"赛果→误差→校准→权重更新"闭环

### 1.3 目标

构建一个能够预测所有体育赛事的统一预测平台，通过更多比赛数据持续训练和验证模型，提高整体预测能力。

**量化目标**: 从 64 场/年扩大到 4000-8000 场/年高质量样本，驱动预测准确率从 ~67% 提升到 72%-75%+。

### 1.4 四阶段路线图

| 阶段 | 内容 | 依赖 |
|------|------|------|
| **Phase 1** (本文档) | 抽取 Prediction Kernel + 世界杯 Adapter 化 | 无 |
| Phase 2 | 扩展足球联赛（欧冠→英超→西甲→德甲→意甲→法甲） | Phase 1 |
| Phase 3 | 统一学习闭环（赛果→误差→校准→权重→模型注册） | Phase 1+2 |
| Phase 4 | NBA 接入 + Basketball Feature Builder | Phase 1 |

---

## 2. 架构总览

```
                    Sports Prediction OS
                               │
                    ┌──────────┼──────────┐
                    │          │          │
              Prediction    Feature    Learning
                Kernel     Registry    Registry
                    │          │          │
                    └──────────┼──────────┘
                               │
                        FeatureBuilder
                               │
                    ┌──────────┴──────────┐
                    │                     │
            FootballBuilder       BasketballBuilder
                    │                     │
            ┌───────┴───────┐     ┌───────┴───────┐
            │               │     │               │
     WorldCupAdapter   EPLAdapter   NBAAdapter   ...
```

### 2.1 核心设计原则

1. **Prediction Kernel 不 import 任何 `world_cup_*` / `epl_*` / `nba_*` 模块** — 完全通过 Protocol 接口与 Adapter 交互
2. **Adapter 只负责拿数据，不计算特征** — 特征计算由 FeatureBuilder 负责
3. **Engine 不硬编码 Feature 名字** — 通过 `FeatureSet` 分层属性访问
4. **领域值对象全部 frozen** — 可安全用于 Cache key、DB 主键、EventBus 消息
5. **现有 `world_cup_*` 代码保持不动** — WorldCupAdapter 内部桥接调用，新代码通过 Kernel API 工作

---

## 3. 领域模型

### 3.1 值对象层

所有值对象均为 `@dataclass(frozen=True)`，不可变，可安全传递和缓存。

```python
@dataclass(frozen=True)
class SportIdentity:
    code: str          # "football" / "basketball"
    name: str          # "Football"

@dataclass(frozen=True)
class CompetitionIdentity:
    code: str          # "world_cup" / "epl" / "nba"
    name: str          # "FIFA World Cup"
    sport: SportIdentity

@dataclass(frozen=True)
class SeasonIdentity:
    competition: CompetitionIdentity
    season_key: str    # "2026" / "2025-26"

@dataclass(frozen=True)
class TeamIdentity:
    code: str          # "ARS" / "BRA" / "LAL"
    name: str          # "Arsenal" / "Brazil"
    competition: CompetitionIdentity

@dataclass(frozen=True)
class MatchIdentity:
    match_id: str
    season: SeasonIdentity
    stage: str         # "group_stage" / "regular_season" / "final"
    round: str | None  # "Matchday 32" / "Quarter-final"
    home: TeamIdentity
    away: TeamIdentity
    kickoff_utc: datetime

@dataclass(frozen=True)
class MatchOutcome:
    match_id: str
    home_score: int
    away_score: int
    outcome: str       # "home_win" / "draw" / "away_win"
    finished_at: datetime
```

### 3.2 层次关系

```
Sport (运动)
 └── Competition (赛事)
      └── Season (赛季)
           └── Round (轮次/阶段)
                └── Match (比赛)
                     ├── Team (home/away)
                     ├── MatchContext (场馆/天气/赛程密度等)
                     ├── FeatureSet (标准化特征向量)
                     └── Prediction (预测结果)
                          └── PredictionOutcome (赛后回填)
```

---

## 4. FeatureSet（分层特征结构）

### 4.1 设计理念

特征不是按"运动"分，而是按**领域维度**分。ELO 不是足球特有（NBA 也有），赔率也不是足球特有（网球也有）。只有真正运动特有的特征（足球 xG、篮球 Pace、电竞 Gold Diff）才放入 `custom`。

### 4.2 分层定义

```python
@dataclass(frozen=True)
class GeneralFeatures:
    """跨运动通用特征"""
    rest_days_home: float | None
    rest_days_away: float | None
    travel_distance_km: float | None
    days_since_last_match: float | None

@dataclass(frozen=True)
class TeamFeatures:
    """球队级特征（跨运动）"""
    elo_rating_home: float | None
    elo_rating_away: float | None
    form_home: float | None        # 近 N 场表现
    form_away: float | None
    h2h_home_win_rate: float | None
    h2h_draw_rate: float | None
    market_value_home: float | None
    market_value_away: float | None

@dataclass(frozen=True)
class MarketFeatures:
    """博彩市场特征（跨运动）"""
    odds_home: float | None
    odds_draw: float | None     # 仅足球，篮球为 None
    odds_away: float | None
    odds_source: str | None
    odds_fresh: bool            # 是否实时赔率

@dataclass(frozen=True)
class PlayerFeatures:
    """球员级特征（跨运动）"""
    key_players_available_home: float | None  # 0-1 可用率
    key_players_available_away: float | None
    injury_impact_home: float | None
    injury_impact_away: float | None

@dataclass(frozen=True)
class EnvironmentFeatures:
    """环境特征（跨运动）"""
    venue: str | None
    weather_temp_c: float | None
    weather_condition: str | None
    is_home_advantage: bool

@dataclass(frozen=True)
class FeatureSet:
    """引擎消费的标准化特征包"""
    match: MatchIdentity
    general: GeneralFeatures
    team: TeamFeatures
    market: MarketFeatures
    player: PlayerFeatures
    environment: EnvironmentFeatures
    custom: dict[str, float]    # 运动特有：足球 xG / 篮球 Pace / 电竞 Gold Diff
    data_quality: str           # "real" / "partial"
    quality_notes: list[str]
    feature_version: str        # 特征版本号，用于学习闭环追踪
```

### 4.3 引擎访问方式

```python
# 通用特征 — 直接属性访问
elo_home = features.team.elo_rating_home
odds_home = features.market.odds_home

# 运动特定特征 — custom dict
xg_home = features.custom.get("xg_home")
pace_home = features.custom.get("pace_home")
```

---

## 5. Registry 层

### 5.1 FeatureRegistry（特征元数据注册表）

```python
@dataclass(frozen=True)
class FeatureDefinition:
    key: str               # "elo_rating_home"
    category: str          # "team" / "market" / "player" / "general" / "environment" / "custom"
    version: str           # "1.0"
    description: str
    sport: str | None      # None=通用, "football"=足球特有
    enabled: bool

class FeatureRegistry:
    """Feature 元数据注册表"""

    def register(self, key: str, category: str, version: str,
                 description: str, sport: str | None = None) -> None: ...
    def get(self, key: str) -> FeatureDefinition | None: ...
    def list_by_category(self, category: str) -> list[FeatureDefinition]: ...
    def list_by_sport(self, sport: str) -> list[FeatureDefinition]: ...
```

**目的**: 引擎和 FeatureBuilder 通过 registry 查询可用特征，而非硬编码字符串。Feature 改名时引擎不用改。

### 5.2 FactorRegistry（因子权重与生命周期管理）

```python
@dataclass
class FactorConfig:
    factor_id: str
    category: str
    version: str
    weight: float
    competition: str | None  # None=全局
    enabled: bool
    source: str              # "manual" / "auto_tune" / "learning"
    updated_at: datetime

class FactorRegistry:
    """因子权重管理 — 驱动模型迭代"""

    def register_factor(self, factor: FactorConfig) -> None: ...
    def get_weight(self, factor_id: str, competition: str) -> float: ...
    def update_weight(self, factor_id: str, competition: str,
                      new_weight: float, source: str) -> None: ...
    def list_active(self, competition: str) -> list[FactorConfig]: ...
```

**目的**: 未来 400+ Feature 的权重管理。Prediction 读取 Registry 配置而非 Python 硬编码。支持按赛事差异化权重（Integrated 引擎在英超和世界杯可用不同权重）。

### 5.3 EngineRegistry（引擎注册与选择）

```python
class EngineRegistry:
    """引擎注册与选择"""

    def register(self, engine: PredictionEngine) -> None: ...
    def get(self, name: str) -> PredictionEngine: ...
    def list_engines(self) -> list[str]: ...
    def select(self, strategy: str, features: FeatureSet) -> PredictionEngine: ...
```

---

## 6. Adapter + FeatureBuilder

### 6.1 DataAdapter（纯数据获取）

```python
class DataAdapter(Protocol):
    """只负责从外部数据源拿原始数据，不做任何计算"""

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]: ...
    def fetch_team_data(self, team: TeamIdentity) -> dict: ...
    def fetch_player_data(self, team: TeamIdentity) -> dict: ...
    def fetch_market_data(self, match: MatchIdentity) -> dict: ...
    def fetch_outcome(self, match_id: str) -> MatchOutcome | None: ...
    def sync_schedule(self) -> int: ...
    def get_match_identity(self, match_id: str) -> MatchIdentity: ...
```

**职责边界**: Adapter 把 FIFA API / Football-Data / API-Football 的 JSON 转换为 `RawMatchData`，到此为止。不计算 Elo、不计算 form、不计算 xG。

### 6.2 FeatureBuilder（特征计算）

```python
class FeatureBuilder(Protocol):
    """从原始数据计算标准化 FeatureSet"""

    def build(self, match: MatchIdentity, raw: dict) -> FeatureSet: ...
    def sport(self) -> SportIdentity: ...
```

**FootballFeatureBuilder** 负责计算：
- 通用层: `GeneralFeatures` (rest_days, travel_distance) + `TeamFeatures` (elo, form, h2h, market_value) + `MarketFeatures` (odds) + `PlayerFeatures` (injury_impact) + `EnvironmentFeatures` (weather, venue)
- 足球特定层: xG / PPDA / Possession / Shots → 放入 `custom`

**职责边界**: FeatureBuilder 消费 Adapter 产出的 `RawMatchData`，输出标准化 `FeatureSet`。计算逻辑包括：最近 5 场状态、Elo 变化、Travel Distance、Rest Days、Expected Goals、Momentum 等。

### 6.3 WorldCupAdapter（桥接现有代码）

```python
class WorldCupAdapter:
    """把现有 world_cup_* 服务桥接为 DataAdapter Protocol"""

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        # 调用 world_cup_match_service.sync_world_cup_fixtures()
        ...

    def fetch_team_data(self, team: TeamIdentity) -> dict:
        # 调用 world_cup_team_stats_service + elo_ratings_service
        ...

    def fetch_market_data(self, match: MatchIdentity) -> dict:
        # 调用 odds_cache_service.get_cached_odds()
        ...

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        # 调用 match_results 表查询
        ...
```

**Phase 1 策略**: WorldCupAdapter 内部调用现有 `world_cup_*` 服务，不做任何重写。新代码通过 Kernel API 工作，旧代码保持不动。

---

## 7. PredictionEngine（引擎层）

### 7.1 引擎接口

```python
class PredictionEngine(Protocol):
    """纯函数式引擎 — 输入 FeatureSet，输出 PredictionResult"""

    def predict(self, features: FeatureSet, match: MatchIdentity) -> PredictionResult: ...
    def name(self) -> str: ...
    def supported_sports(self) -> list[str]: ...  # ["football"] 或 ["*"] 全运动
```

### 7.2 五个引擎迁移

现有 5 个引擎从 `world_cup_engines/` 迁移到 `kernel/engines/`，泛化为运动无关：

| 现有文件 | 迁移后 | 消费的 FeatureSet 层 |
|---------|--------|---------------------|
| `world_cup_elo_odds_engine.py` | `elo_odds_engine.py` | `features.team.elo_*` + `features.market.odds_*` |
| `world_cup_prediction_engine.py` | `hybrid_engine.py` | `features.team` + `features.player` + `features.custom` |
| `world_cup_rule_engine.py` | `rule_engine.py` | `features.team` + `features.custom` (xG) |
| `world_cup_ai_engine.py` | `ai_engine.py` | 完整 FeatureSet (透传给 LLM) |
| `world_cup_gbm_engine.py` | `gbm_engine.py` | 完整 FeatureSet |
| `world_cup_btd_model.py` | `btd_model.py` | `features.team.elo_*` |
| `world_cup_confidence_calibration.py` | `calibration.py` | `PredictionResult.confidence` |

**迁移原则**:
- 引擎内部不再 import `world_cup_*` 服务，所有数据通过 `FeatureSet` 获取
- 引擎行为保持不变（输出相同的概率和比分）
- `world_cup_*` 原文件保留，WorldCupAdapter 内部仍可调用（过渡期）

### 7.3 PredictionResult

```python
@dataclass(frozen=True)
class PredictionResult:
    """引擎输出的统一预测结果"""
    predicted_scores: dict[str, float]      # {"home": 2.1, "away": 1.3}
    outcome_probabilities: dict[str, float] # {"home_win": 0.55, "draw": 0.25, "away_win": 0.20}
    confidence: float                        # 0-1
    engine_name: str                         # "elo_odds" / "hybrid" / ...
    explanation: list[ContributionItem]      # 贡献拆解
    betting_analysis: dict | None            # 可选
    feature_version: str                     # 特征版本追踪
    prediction_timestamp: datetime

@dataclass(frozen=True)
class ContributionItem:
    factor: str          # "elo" / "odds" / "form" / "xg" / ...
    direction: str       # "support" / "oppose" / "neutral"
    weight: float        # 贡献权重
    available: bool      # 是否参与计算
    detail: str | None   # 人类可读说明
```

---

## 8. Learning Layer（学习闭环）

### 8.1 接口定义

```python
class LearningService(Protocol):
    """赛后学习闭环 — 核心差异化能力"""

    def record_prediction(self, match: MatchIdentity,
                          prediction: PredictionResult) -> None: ...

    def record_outcome(self, outcome: MatchOutcome) -> None: ...

    def compute_error(self, match_id: str) -> PredictionError | None: ...

    def update_calibration(self, competition: str,
                           engine: str) -> CalibrationParams: ...

    def update_weights(self, competition: str) -> WeightUpdate: ...

    def engine_score(self, engine: str,
                     competition: str | None = None) -> EngineScore: ...
```

### 8.2 数据结构

```python
@dataclass(frozen=True)
class PredictionError:
    match_id: str
    engine: str
    score_mae: float
    outcome_correct: bool
    brier_score: float
    confidence_calibrated: bool

@dataclass(frozen=True)
class EngineScore:
    engine: str
    competition: str | None
    accuracy: float           # 方向准确率
    avg_mae: float            # 平均绝对误差
    brier_score: float        # 布里尔分数
    sample_count: int
    confidence_calibration: float  # 置信度校准度
    last_updated: datetime
```

### 8.3 闭环流程

```
Match finished
  → record_outcome()
  → compute_error()
  → update_calibration()   ← 校准引擎参数
  → update_weights()       ← 更新 FactorRegistry 权重
  → engine_score()         ← 更新 EngineRegistry 排名
  → 下一场预测使用新参数
```

### 8.4 Phase 1 实现范围

Phase 1 实现 `record_prediction` + `record_outcome` + `compute_error` + `engine_score`（基础记录和误差计算）。`update_calibration` + `update_weights` 留到 Phase 3 完整实现。

---

## 9. PredictionKernel（编排器）

```python
class PredictionKernel:
    """核心编排器 — 连接 Adapter → Builder → Engine → Learning"""

    def __init__(self, adapter: DataAdapter,
                 feature_builder: FeatureBuilder,
                 engine_registry: EngineRegistry,
                 factor_registry: FactorRegistry,
                 learning: LearningService): ...

    def predict(self, match_id: str, engine: str = "auto") -> PredictionResult:
        # 1. 获取比赛身份
        match = self.adapter.get_match_identity(match_id)
        # 2. 获取原始数据
        raw = self.adapter.fetch_all_data(match)
        # 3. 构建特征
        features = self.feature_builder.build(match, raw)
        # 4. 选择引擎
        engine_impl = self.engine_registry.select(engine, features)
        # 5. 运行预测
        prediction = engine_impl.predict(features, match)
        # 6. 记录预测（供学习闭环使用）
        self.learning.record_prediction(match, prediction)
        # 7. 返回结果
        return prediction

    def batch_predict(self, match_ids: list[str],
                      engine: str = "auto") -> list[PredictionResult]: ...

    def process_outcome(self, match_id: str) -> None:
        """赛后处理 — 触发学习闭环"""
        outcome = self.adapter.fetch_outcome(match_id)
        self.learning.record_outcome(outcome)
        self.learning.compute_error(match_id)
        self.learning.update_calibration(
            match.competition.code, prediction.engine_name
        )
        self.learning.update_weights(match.competition.code)
```

---

## 10. 目录结构

### 10.1 新增目录

```
backend/app/
├── kernel/                          # Prediction Kernel（运动无关）
│   ├── __init__.py
│   ├── domain.py                    # 所有 Identity 值对象 + FeatureSet + PredictionResult
│   ├── protocols.py                 # DataAdapter / FeatureBuilder / Engine / Learning Protocol
│   ├── prediction_kernel.py         # 编排器
│   ├── engine_registry.py           # 引擎注册表
│   ├── feature_registry.py          # 特征元数据注册表
│   ├── factor_registry.py           # 因子权重管理
│   ├── learning_service.py          # 学习闭环实现
│   └── engines/                     # 内置引擎（从 world_cup_engines 迁移泛化）
│       ├── __init__.py
│       ├── elo_odds_engine.py       # 从 world_cup_elo_odds_engine.py 抽取
│       ├── hybrid_engine.py         # 从 world_cup_prediction_engine.py 抽取
│       ├── rule_engine.py           # 从 world_cup_rule_engine.py 抽取
│       ├── ai_engine.py             # 从 world_cup_ai_engine.py 抽取
│       ├── gbm_engine.py            # 从 world_cup_gbm_engine.py 抽取
│       ├── btd_model.py             # 从 world_cup_btd_model.py 抽取
│       └── calibration.py           # 从 world_cup_confidence_calibration.py 抽取

├── sports/                          # Sport Layer（运动特定逻辑）
│   ├── __init__.py
│   ├── football/                    # 足球
│   │   ├── __init__.py
│   │   ├── feature_builder.py       # FootballFeatureBuilder
│   │   └── adapters/
│   │       ├── __init__.py
│   │       └── world_cup_adapter.py # WorldCupAdapter（桥接现有代码）
│   └── (future: basketball/)
```

### 10.2 现有文件策略

- **保留不动**: 所有 `world_cup_*` 文件保持原位，WorldCupAdapter 内部调用
- **逐步迁移**: Kernel 引擎从 `world_cup_engines/` 抽取泛化，原文件保留供 Adapter 桥接
- **API 路由**: Phase 1 新增 `/api/predictions/*` 通用路由，旧 `/api/world-cup/predictions/*` 路由保持兼容
- **前端**: Phase 1 不改前端，继续使用现有 `/world-cup` 页面

---

## 11. API 设计

### 11.1 新增通用预测 API

```
POST   /api/predictions/matches/{match_id}/predict    # 通用预测入口
GET    /api/predictions/engines                        # 列出可用引擎
GET    /api/predictions/engines/{name}/score           # 引擎评分
POST   /api/predictions/outcomes/{match_id}/process    # 赛后学习闭环触发
```

### 11.2 兼容策略

现有 `/api/world-cup/predictions/*` 路由保持不变。Phase 1 内部改为调用 `PredictionKernel.predict()`，但 API 响应格式保持兼容。前端无感知。

---

## 12. 数据库

### 12.1 Phase 1 新增表

```sql
-- 通用预测记录表（跨赛事）
CREATE TABLE kernel_predictions (
    match_id TEXT PRIMARY KEY,
    sport TEXT NOT NULL,
    competition TEXT NOT NULL,
    season TEXT NOT NULL,
    engine TEXT NOT NULL,
    predicted_scores JSON NOT NULL,
    outcome_probabilities JSON NOT NULL,
    confidence REAL NOT NULL,
    feature_version TEXT NOT NULL,
    explanation JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 通用预测历史表
CREATE TABLE kernel_prediction_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    predicted_scores JSON,
    outcome_probabilities JSON,
    confidence REAL,
    trigger TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 通用比赛结果与误差表
CREATE TABLE kernel_match_outcomes (
    match_id TEXT PRIMARY KEY,
    home_score INTEGER,
    away_score INTEGER,
    outcome TEXT,
    engine TEXT,
    score_mae REAL,
    outcome_correct INTEGER,
    brier_score REAL,
    finished_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 引擎评分表
CREATE TABLE kernel_engine_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine TEXT NOT NULL,
    competition TEXT,          -- NULL = 全局
    accuracy REAL,
    avg_mae REAL,
    brier_score REAL,
    sample_count INTEGER DEFAULT 0,
    last_updated TIMESTAMP,
    UNIQUE(engine, competition)
);

-- 因子注册表
CREATE TABLE kernel_factors (
    factor_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    version TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    competition TEXT,          -- NULL = 全局
    enabled INTEGER DEFAULT 1,
    source TEXT DEFAULT 'manual',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 12.2 现有表策略

- 现有 `world_cup_predictions.db` 中的 11 张表保持不动
- WorldCupAdapter 继续读写旧表
- Kernel 新表使用独立的 `kernel_predictions.db` 或同一 SQLite（新表前缀 `kernel_`）
- 数据迁移不在 Phase 1 范围内

---

## 13. 测试策略

### 13.1 Kernel 单元测试

- `domain.py`: 值对象不可变性、相等性、序列化
- `protocols.py`: Protocol 接口合规性检查
- `prediction_kernel.py`: 编排器流程测试（mock Adapter/Builder/Engine）
- `engine_registry.py`: 注册、选择、降级逻辑
- `feature_registry.py`: 注册、查询、按运动过滤
- `factor_registry.py`: 权重读取、更新、按赛事差异化
- `learning_service.py`: 记录、误差计算、评分聚合

### 13.2 引擎迁移测试

每个迁移的引擎必须有：
- **输出等价性测试**: 相同输入下，新引擎输出与旧引擎一致（容差内）
- **FeatureSet 消费测试**: 引擎正确从 FeatureSet 读取所需字段
- **缺失特征降级测试**: 当 `elo_rating_home` 为 None 时引擎优雅降级

### 13.3 Adapter 桥接测试

- WorldCupAdapter 正确调用现有 `world_cup_*` 服务
- 返回的 `RawMatchData` 结构符合 FeatureBuilder 预期
- `fetch_outcome` 正确映射 `match_results` 表

### 13.4 集成测试

- 端到端: `WorldCupAdapter → FootballFeatureBuilder → EloOddsEngine → PredictionResult`
- 结果与现有 `run_prediction_pipeline` 输出在容差内一致

### 13.5 回归保护

- 现有 30+ 个 `test_world_cup_*.py` 测试全部保持通过
- 现有前端 15 个测试全部保持通过
- 现有 `/api/world-cup/predictions/*` API 响应格式不变

---

## 14. 迁移安全约束

1. **现有 `world_cup_*` 文件不删除、不重命名** — WorldCupAdapter 内部调用
2. **现有 API 路由 `/api/world-cup/predictions/*` 保持兼容** — 前端无感知
3. **现有数据库表不修改 schema** — 新表使用 `kernel_` 前缀
4. **引擎迁移逐个进行** — 每个引擎迁移后立即运行等价性测试
5. **所有 feature flag 默认 OFF** — `KERNEL_PREDICTION_ENABLED=false` 时回退到旧管线
6. **前端页面 Phase 1 不修改** — 保持现有 `/world-cup` 页面

---

## 15. Phase 1 交付物清单

| 交付物 | 说明 |
|--------|------|
| `kernel/domain.py` | 全部值对象 + FeatureSet + PredictionResult |
| `kernel/protocols.py` | DataAdapter / FeatureBuilder / Engine / Learning Protocol |
| `kernel/prediction_kernel.py` | 编排器 |
| `kernel/engine_registry.py` | 引擎注册与选择 |
| `kernel/feature_registry.py` | 特征元数据注册表 |
| `kernel/factor_registry.py` | 因子权重管理 |
| `kernel/learning_service.py` | 学习闭环基础实现（记录+误差+评分） |
| `kernel/engines/` | 5 个引擎迁移泛化 + BTD 模型 + 校准 |
| `sports/football/feature_builder.py` | FootballFeatureBuilder |
| `sports/football/adapters/world_cup_adapter.py` | WorldCupAdapter 桥接 |
| `kernel_predictions.db` | 新数据库（kernel_ 前缀表） |
| `/api/predictions/*` 路由 | 通用预测 API |
| 单元测试 + 集成测试 + 等价性测试 | 覆盖全部新增代码 |
| 回归测试通过 | 现有全部测试保持绿色 |

---

## 16. 后续阶段预告

### Phase 2: 足球联赛扩展
- 新增 `EPLAdapter` / `UCLAdapter` / `LaLigaAdapter` 等
- 复用 `FootballFeatureBuilder`，按联赛调整 custom 特征
- 前端重构为通用竞猜页面

### Phase 3: 统一学习闭环
- 完整实现 `update_calibration` + `update_weights`
- 自动化闭环: 赛果→误差→校准→权重→引擎评分→下一场
- 按赛事/联赛差异化权重

### Phase 4: NBA 接入
- 新增 `BasketballFeatureBuilder`（Pace / Off Rating / Def Rating / Usage → custom）
- 新增 `NBAAdapter`
- 验证跨运动泛化能力

---

## 附录 A: 现有世界杯模块文件清单（迁移参考）

### 后端核心服务（WorldCupAdapter 桥接目标）
- `world_cup_prediction_pipeline.py` → Kernel.predict() 替代
- `world_cup_match_service.py` → Adapter.fetch_schedule()
- `world_cup_team_stats_service.py` → Adapter.fetch_team_data()
- `odds_cache_service.py` → Adapter.fetch_market_data()
- `elo_ratings_service.py` → FeatureBuilder 计算 team.elo
- `world_cup_factor_service.py` → FeatureBuilder 计算 team/player
- `world_cup_enhanced_factors.py` → FeatureBuilder 计算 custom
- `world_cup_schedule_factors.py` → FeatureBuilder 计算 general
- `world_cup_group_context.py` → FeatureBuilder 计算 custom
- `world_cup_confidence_calibration.py` → kernel/engines/calibration.py
- `world_cup_scoring_service.py` → LearningService.compute_error()
- `world_cup_quality_service.py` → LearningService.engine_score()

### 引擎（直接迁移到 kernel/engines/）
- `world_cup_elo_odds_engine.py` → `elo_odds_engine.py`
- `world_cup_prediction_engine.py` → `hybrid_engine.py`
- `world_cup_rule_engine.py` → `rule_engine.py`
- `world_cup_ai_engine.py` → `ai_engine.py`
- `world_cup_gbm_engine.py` → `gbm_engine.py`
- `world_cup_btd_model.py` → `btd_model.py`
- `world_cup_gbm_features.py` → `gbm_features.py`

### 数据源（Adapter 内部调用）
- `football_data_source.py` → WorldCupAdapter
- `world_cup_api_football_source.py` → WorldCupAdapter
- `world_cup_sportmonks_source.py` → WorldCupAdapter
- `world_cup_openfootball_data.py` → WorldCupAdapter
- `transfermarkt_scraper.py` → WorldCupAdapter
- `sentiment_aggregator.py` → WorldCupAdapter
