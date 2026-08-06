# 足球 form / H2H 深化设计（P1-F1 / P1-F4 残项）

**Date:** 2026-08-06
**Status:** Proposed
**Backlog IDs:** P1-F1（form 加权近 N、覆盖率与别名）、P1-F4（h2h 主客场分拆、别名）
**代码锚点:** `backend/app/sports/football/club_form.py`、`backend/app/sports/football/adapters/_shared.py`、`backend/app/sports/football/engines/football_multi_factor_engine.py`

---

## 1. 背景

`OPPORTUNITY_BACKLOG_2026-07-17.md` 中 P1-F1 与 P1-F4 均为「✅ 部分」，剩下三条残项：

| 残项 | 出处 |
|------|------|
| form 加权近 N | P1-F1「加权近 N / 覆盖率与别名仍待」 |
| 队名别名导致的覆盖率损失 | P1-F1 与 P1-F4 都写了「别名仍待」 |
| h2h 主客场分拆 | P1-F4「主客场分拆/别名/合并源仍待」 |

本设计只做这三条。**不做**的：真伤病 / 真 xG / 真裁判 API（需外部密钥），以及 P1-F4 的「合并源」（跨 CSV + kernel 去重合并，属另一项工程）。

---

## 2. 问题 1：别名不匹配 → 静默零覆盖

### 2.1 现状

`club_form.py` 的两个查询函数都靠一个函数做队名比较：

```python
def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())
```

即**小写 + 空白折叠后的精确字符串相等**。调用方传进来的名字来自 `MatchIdentity.home.name`（适配器 / 市场源 / 前端），而被比较的名字来自 `kernel_match_fixture.home_team`（历史 ingest）。两者同源的保证并不存在。

因此 `Man City` vs `Manchester City`、`Spurs` vs `Tottenham Hotspur`、`Inter` vs `Inter Milan` 全部不匹配。

### 2.2 为什么这个 bug 不显眼

三层静默：

1. `team_form_from_kernel` 找不到行 → `return None`
2. `enrich_situational_features` 里 `if home_stats:` 为假 → 不写 `form_home`
3. `FootballMultiFactorEngine` 对缺失因子做权重重分配 → 预测照常产出，只是少吃一个因子

没有异常、没有日志、没有 `data_quality` 降级。**表现为「模型正常但没变准」，而不是「报错」**，所以容易长期存在。

### 2.3 方案

仓库已有 `app/sports/_shared/team_aliases.py`，覆盖 10 个赛事、每队 ≥4 个别名（缩写 / 简称 / 全称 / 中文），并提供：

```python
def resolve_team(alias: str, competition: str) -> str | None
```

把它接进 `club_form.py` 的比较路径，得到一个**双层匹配**：

```
canonical(query, comp) 与 canonical(stored, comp) 都非 None  →  比较 canonical
否则                                                          →  退回 _normalize 精确比较
```

### 2.4 关键约束：不得引入假阳性

别名表按赛事分区，同一个缩写在不同赛事指向不同球队 —— `BOS` 在 nba 是凯尔特人、在 mlb 是红袜；`CHI` 在 nba 是公牛、在 wc 是智利；`Rangers` 在 nhl 是纽约游骑兵、在 ucl 是格拉斯哥流浪者。

因此 canonical 比较**必须**限定在单一 `competition` 内。本设计的规则：

- `competition` 为空 / 不在 `TEAM_ALIASES` 中 → 完全不启用别名层，行为与今天逐字节一致
- 只有当查询名与库中名在**同一个** competition 下都解析成功，才用 canonical 比较
- 任一侧解析失败 → 该行退回字符串比较（而不是判定为不匹配）

第三条保证这是**纯增量**：今天能匹配上的，改后依然匹配得上。

### 2.5 H2H 的额外约束

`h2h_from_kernel` 用集合相等判断一场历史比赛是不是这一对：

```python
if {fh, fa} != pair: continue
```

混用 canonical 与 raw 名会让集合比较出错（一侧 canonical、一侧 raw，永不相等）。方案：先把 fixture 双方各自解析成「比较键」（canonical 优先，失败退回 normalize），查询双方同法，再比集合。两侧用同一个函数产生键，集合语义才成立。

同时 `home_key == away_key` 的自我对阵防御要在**解析后**再查一次 —— 否则 `h2h_from_kernel("Spurs", "Tottenham")` 会被当成两支不同的队，去数一支球队和自己的交锋。

---

## 3. 问题 2：form 不做时间加权

### 3.1 现状

```python
rate = (3 * w + d) / (3 * n)
```

10 场里第 1 场和第 10 场权重相同。足球的近期状态衰减快（伤停、换帅、赛程密度），扁平平均把信号抹平。

### 3.2 方案

新增 `weighted_points_form_rate(results, *, half_life=5.0)`：

- 入参是**按时间倒序**的单场结果序列 `["W", "D", "L", ...]`，`results[0]` 为最近一场
- 第 i 场（0-based）权重 `0.5 ** (i / half_life)`
- 单场得分 W=1.0 / D=1/3 / L=0.0（与 `points_form_rate` 的 (3W+D)/3N 同尺度）
- 返回 `sum(w_i * s_i) / sum(w_i)`，落在 [0, 1]

`half_life=5.0` 意味着 5 场前的比赛权重减半；10 场窗口末端权重 0.25。

**不改** `points_form_rate(wins, draws, played)` 的签名与语义 —— 它有 9 个现存测试，并被 `_shared.py` 的 enrich 路径直接调用。新函数是并列的第二个入口。

`team_form_from_kernel` 的返回字典增加两个键：

| 键 | 含义 |
|----|------|
| `form_rate_weighted` | 上述加权值 |
| `recent_results` | 倒序结果串，供调试与上层复用 |

`wins/draws/losses/played` 全部保持不变，老调用方零改动。

### 3.3 enrich 侧如何取用

`enrich_situational_features` 中，`form_home` / `form_away` 优先取 `form_rate_weighted`，缺失时退回 `points_form_rate(...)`。

注意 CSV 路径（`get_historical_team_stats`，世界杯国家队）只返回汇总计数、没有逐场序列，因此**拿不到**加权值 —— 那条路径自然退回扁平值。这是可接受的：加权针对的是俱乐部赛程密集的场景。

---

## 4. 问题 3：H2H 不分主客场

### 4.1 现状

`h2h_from_kernel` 把所有历史交锋映射到「当前主队视角」，不管当年谁主场。Arsenal 客场 0-3 输给 Chelsea 与 Arsenal 主场 0-3 输，在统计里完全等价。

主场优势在足球里是显著且稳定的效应，抹掉它等于给 h2h 因子注入噪声。

### 4.2 方案

`h2h_from_kernel` 的返回增加一组只统计「当前主队当年也在主场」的计数：

| 键 | 含义 |
|----|------|
| `home_venue_matches` | 当前主队做东的历史交锋数 |
| `home_venue_home_wins` | 其中当前主队胜 |
| `home_venue_draws` | 其中平 |
| `home_venue_away_wins` | 其中当前主队负 |

现有四个键（`matches_played` / `home_wins` / `draws` / `away_wins`）语义不变。

### 4.3 引擎侧融合与样本量门槛

`enrich_situational_features` 把同场地胜率写进 `custom`：

```
custom["h2h_home_venue_win_rate"]   同场地主胜率
custom["h2h_home_venue_draw_rate"]  同场地平局率
custom["h2h_home_venue_matches"]    样本量（供门槛判断）
```

写 `custom` 而不是加 `TeamFeatures` 字段，是为了避开 `domain.py` 的 frozen dataclass 契约与 `scripts/generate_types` 的类型同步 CI（E9），符合既有 Phase 的零侵入约定。

`FootballMultiFactorEngine` 的 h2h 因子改为混合：

```
blended = (1 - alpha) * overall + alpha * home_venue
alpha   = min(1.0, home_venue_matches / MIN_VENUE_SAMPLES)
```

`MIN_VENUE_SAMPLES = 4`。样本为 0 时 `alpha=0`，退化为今天的行为；样本充足时同场地记录占满一半权重上限（`alpha` 封顶 1.0 表示完全采用同场地）。

### 4.4 Flag

新行为改变已注册引擎的输出数值，按仓库约定挂 flag 且**默认 OFF**：

```
FOOTBALL_H2H_VENUE_SPLIT_ENABLED=false
```

OFF 时引擎读都不读 `custom` 里的这几个键。数据侧（`club_form` 与 enrich 的写入）**不挂 flag** —— 多写几个 `custom` 键无副作用，且便于打开前先离线观察分布。

---

## 5. 被否决的替代方案

| 方案 | 否决理由 |
|------|----------|
| 在 `kernel_match_fixture` 落库时就规范化队名 | 需要历史数据迁移；ingest 与查询解耦更安全；且原始名有溯源价值 |
| 模糊匹配（Levenshtein / token 集合） | 假阳性风险高（"Manchester City" vs "Manchester United" 距离很近），而别名表是人工审定的确定映射 |
| 给 `TeamFeatures` 加 h2h 主客场字段 | 触发 frozen dataclass 契约变更 + 类型同步 CI（E9）+ 全体育适配器改签名，与「零侵入」相悖 |
| form 用线性衰减而非指数 | 指数半衰期是单参数、可解释、且尾部不会突然截断；线性需要额外定义窗口长度 |
| h2h 直接只用同场地记录 | 俱乐部交锋本就稀疏，只取同场地会让多数比赛样本量为 0～1，方差大于收益 |

---

## 6. 数据流

```
MatchIdentity(home="Man City", away="Spurs", comp="epl")
        │
        ├─ enrich_situational_features
        │        │
        │        ├─ get_historical_team_stats (CSV)  ── 国家队命中；俱乐部多半 None
        │        │
        │        └─ team_form_from_kernel ──────────────┐
        │                 │                             │
        │                 │  resolve_team("Man City","epl") → manchester_city
        │                 │  resolve_team(fixture.home_team,"epl") → manchester_city
        │                 │  ✓ 命中（今天在这里静默失配）
        │                 │                             │
        │                 └─→ {wins, draws, ..., form_rate_weighted, recent_results}
        │                                               │
        │        team.form_home ← form_rate_weighted ───┘
        │
        └─ h2h_from_kernel ── 同一套别名键 ──→ {..., home_venue_*}
                 │
                 ├─ team.h2h_home_win_rate      （不变）
                 └─ custom.h2h_home_venue_*     （新增）
                          │
                          └─→ FootballMultiFactorEngine
                                 flag ON  → blended h2h
                                 flag OFF → 今天的行为
```

---

## 7. 验收标准

1. `resolve_team` 能解析的别名对（如 `Man City` / `Manchester City`，同一 competition）在 `team_form_from_kernel` 与 `h2h_from_kernel` 中匹配成功
2. competition 为 `None` 或不在别名表中时，行为与改动前逐字节一致
3. 跨 competition 的同名缩写（`BOS` nba vs mlb、`Rangers` nhl vs ucl）不会互相匹配
4. 任一侧别名解析失败时退回字符串比较，今天能匹配的仍然匹配
5. `h2h_from_kernel("Spurs", "Tottenham Hotspur", competition="epl")` 返回 `None`（解析后同队）
6. `points_form_rate` 的签名、返回值与全部 9 个现存测试不变
7. `weighted_points_form_rate` 对全 W 返回 1.0、全 L 返回 0.0、全 D 返回 1/3；近期胜的序列严格高于同样计数但近期负的序列
8. `home_venue_*` 计数只含当前主队做东的交锋；无此类交锋时为 0 而非 None
9. `FOOTBALL_H2H_VENUE_SPLIT_ENABLED` 默认 OFF，且 OFF 时引擎输出与改动前逐位相同
10. 后端全量测试套件保持绿（基线 3614 passed / 11 skipped）

---

## 8. 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 别名表覆盖不全的球队仍然失配 | 覆盖率提升有限 | 纯增量、不回退；表可持续补充 |
| 别名表把两支队错误映射到同一 canonical | 假阳性历史数据 | 限定单 competition；验收标准 3 有跨赛事测试 |
| 加权 form 改变现有预测数值 | 已 apply 的 Optuna 参数是在扁平 form 上调出来的 | 影响面仅限 kernel 俱乐部路径（CSV 路径不受影响）；数值变化温和且同尺度；P1-A4 的 `eval_applied_params.py` 可复核 |
| h2h 同场地样本稀疏 | alpha 长期接近 0，收益不明显 | 这正是设默认 OFF 的原因；先观察 `home_venue_matches` 分布再决定是否打开 |
| `resolve_team` 是 O(n) 线性扫描 | 每行 fixture 两次解析，历史表大时变慢 | 在本函数内对 competition 的别名表建一次小写字典缓存，把每次查找降到 O(1) |

---

## 9. 测试计划

新增测试写在既有 `backend/tests/test_club_form.py`（别名 / 加权 / 场地分拆）与 `backend/tests/test_football_multi_factor_engine.py`（flag 开关与混合）中，不新建文件。

| 用例 | 覆盖验收 |
|------|----------|
| 别名命中：库存 `Manchester City`，查询 `Man City` | 1 |
| competition=None 时别名不生效 | 2 |
| 未知 competition 时别名不生效 | 2 |
| `BOS` 在 nba / mlb 不互相匹配 | 3 |
| 一侧无别名时退回字符串匹配 | 4 |
| 别名解析后同队 → None | 5 |
| 现存 9 个 `points_form_rate` 测试原样通过 | 6 |
| 加权：全 W / 全 L / 全 D | 7 |
| 加权：近期胜 > 近期负（同计数） | 7 |
| 加权：空序列 → None | 7 |
| `home_venue_*` 只数主队做东的交锋 | 8 |
| 无同场地交锋时为 0 | 8 |
| flag OFF 时引擎输出不变 | 9 |
| flag ON 且样本足时 h2h 因子偏向同场地 | 9 |
