# 赛程密度跨赛事合并设计（P1-F2 残项）

**Date:** 2026-08-06
**Status:** Proposed
**Backlog ID:** P1-F2（跨联赛合并赛程 / 更细窗口）
**代码锚点:** `backend/app/sports/football/adapters/_shared.py`、`backend/app/sports/_shared/team_aliases.py`、`backend/app/sports/football/engines/football_multi_factor_engine.py`

---

## 1. 背景

`OPPORTUNITY_BACKLOG_2026-07-17.md` 的 P1-F2 记为「✅ 部分 2026-07-25」，残项两条：**跨联赛合并赛程**、**更细窗口**。

本设计只做这两条。**不做**：真实赛程 API、国际比赛日（俱乐部球员被征召）建模 —— 前者需外部密钥，后者需球员级名单数据。

---

## 2. 问题 1：单赛事窗口 → 拥堵被系统性低估

### 2.1 现状

```python
def _fixture_history_for_density(competition: str | None) -> list[dict] | None:
    ...
    if competition:
        q = q.filter(KernelMatchFixture.competition == competition)
```

密度计数只在**当前这一个赛事**内统计。一支周二打欧冠、周六打联赛的球队，在联赛比赛上算出的 `matches_last_7d` 是 1 而不是 2，于是 `schedule_congested_*`（阈值 ≥2）为 False。

### 2.2 为什么这比「噪声」更糟

这不是随机误差，而是**有方向的偏差**：能同时踢欧冠 / 欧联的正是强队。于是拥堵惩罚被系统性地从最应该承受它的球队身上拿掉，且**恰好在赛季最密集的阶段**（欧战淘汰赛与联赛冲刺重叠）失效。

误差与球队强度相关，模型无法靠其它因子把它平均掉。

### 2.3 方案

把密度用的 fixture 历史从「单一 competition」放宽到**全部足球赛事**，复用 `FactorRegistry._FOOTBALL_COMPETITIONS`（已有集合，含 wc / ucl / epl / laliga / bundesliga / seriea / ligue1 及其别名拼写），而不是放宽到全库 —— 后者会让 nba / mlb / nhl 的队名参与比较。

---

## 3. 问题 2：合并之后，精确字符串匹配成为瓶颈

### 3.1 现状

`app/sports/_shared/rest_form.py`：

```python
def _team_in_match(team: str, m: Mapping[str, Any]) -> bool:
    return m.get("home_team") == team or m.get("away_team") == team
```

**区分大小写的原始字符串相等**（连 `.lower()` 都没有），docstring 亦写明 "Exact team name match"。

单赛事内这尚可接受：同一次 ingest 的写法通常一致。一旦跨赛事合并，两次不同 ingest 的写法差异就成为常态 —— EPL 侧存 `Manchester City`、UCL 侧存 `Man City` 时，合并进来的那场欧冠**一场也数不上**，问题 1 的修复直接归零。

### 3.2 方案：在历史构造侧解析，不动共享模块

`rest_form.py` 位于 `app/sports/_shared/`，另有 3 个适配器（nba / mlb / nhl）与回测 `match_loader` 依赖它。改它的匹配语义会波及这些路径。

但 `matches_in_window_as_of` 全仓**只有一个调用方**（足球 `_shared.py`）。因此把解析放在**历史构造侧**：`_fixture_history_for_density` 返回的行里，`home_team` / `away_team` 已经是解析后的比较键；查询名同法解析后再传入。`rest_form.py` 一行不改，其余三个适配器与回测逐字节不受影响。

### 3.3 每行按各自的 competition 解析

别名表按赛事分区，跨赛事直接拍平会撞车。足球赛事内实测有 3 组冲突：

| 别名 | 含义 A | 含义 B |
|------|--------|--------|
| `CEL` | laliga → `celta_vigo` | ucl → `celtic` |
| `ESP` | laliga → `espanyol` | wc → `spain` |
| `POR` | ucl → `porto` | wc → `portugal` |

规则：**每一行 fixture 用它自己的 `competition` 解析，查询名用当前比赛的 `competition` 解析，然后比较 canonical id。**

这条规则让上述冲突自动得到正确结果，无需特判：查询 `CEL`（ucl）得 `celtic`，laliga 行的 `CEL` 得 `celta_vigo`，两者不等 → 不匹配，正确。反之若把所有足球别名拍平成一张表，`CEL` 会指向不确定的一方 —— 这正是下面「被否决方案」里拒绝拍平的原因。

canonical id 跨赛事稳定（`Manchester City` 在 epl 与 ucl 都解析为 `manchester_city`），所以正确的合并同样成立。

解析失败的一侧退回 `_normalize` 字符串比较，与 P1-F1/F4 同一约定 —— 保证纯增量。

---

## 4. 问题 3：只有一个 7 天窗口

### 4.1 现状

只有 `window_days=7` 与 `>= 2` 一个阈值。「6 天前踢过一场」与「2 天前踢过一场」被判为同一档拥堵，但二者对体能的含义相差很大。

### 4.2 方案

在合并历史上再算一个 3 天窗口，写 `matches_merged_3d_*`。引擎按两档施加惩罚：3 天内有比赛的一侧惩罚更重。

---

## 5. 写新键而非改旧键

现有 `matches_last_7d_*` 与 `schedule_congested_*` 已被 `FootballMultiFactorEngine` 无条件读取。若就地改写它们的口径，即使挂 flag，数据侧与引擎侧也会耦合在同一个开关上。

因此：

| 键 | 口径 | 是否新增 |
|----|------|----------|
| `matches_last_7d_*` | 单赛事，精确字符串 | 不变 |
| `schedule_congested_*` | 由上者派生 | 不变 |
| `matches_merged_7d_*` | 全足球赛事，别名解析 | 新增 |
| `matches_merged_3d_*` | 同上，3 天窗口 | 新增 |

数据侧**不挂 flag**（多写 `custom` 键无副作用，且便于打开前先离线看分布），引擎侧挂 `FOOTBALL_SCHEDULE_MERGE_ENABLED`，**默认 OFF**。与 P1-F4 主客场分拆同一模式。

OFF 时引擎完全不读 merged 键，输出与改动前逐位相同。

---

## 6. 引擎侧

`FootballMultiFactorEngine` 的 rest 因子现有分档：

```
b2b（rest <= 1）        ±0.03
congest（rest <= 2 或 7 日 >= 2）  ±0.015
```

flag ON 时，congest 的判定改读 `matches_merged_7d_* >= 2`（缺失则退回现有 `schedule_congested_*`），并新增一档：`matches_merged_3d_* >= 1` 时按 b2b 档处理（0.03），因为 3 天内两赛意味着真正的短周转。

分档互斥、幅度不变，不引入新的量级。

---

## 7. 被否决的替代方案

| 方案 | 否决理由 |
|------|----------|
| 改 `rest_form.py` 的 `_team_in_match` 加别名 | 该模块被 nba / mlb / nhl 适配器与回测共用，改匹配语义波及 4 条无关路径；而唯一需要它的调用方只有一个 |
| 把全部足球别名拍平成一张表 | `CEL` / `ESP` / `POR` 三组跨赛事冲突会指向错误球队；按行赛事解析可零特判地避开 |
| 直接改写 `matches_last_7d_*` 的口径 | 该键已被引擎无条件读取，就地改口径会让数据侧与引擎侧共用一个开关，无法「先观察分布再启用」 |
| 放宽到全库 fixture（不限赛事集合） | nba / mlb / nhl 队名会进入比较空间，且这些赛事的 canonical id 与足球无关 |
| 用固定的「欧战球队名单」硬编码 | 名单每季变动，且不覆盖国内杯赛；从 fixture 表直接合并无需维护名单 |

---

## 8. 数据流

```
MatchIdentity(home="Man City", comp="epl", kickoff=T)
        │
        └─ enrich_situational_features
                 │
                 ├─ _fixture_history_for_density(competition="epl")   （现状，不变）
                 │        └─ 只查 epl → matches_last_7d_home = 1
                 │
                 └─ _merged_fixture_history()                          （新增）
                          │  查 _FOOTBALL_COMPETITIONS 全部赛事
                          │  每行按自己的 competition 解析队名
                          │  epl 行 "Manchester City" → canon:manchester_city
                          │  ucl 行 "Man City"        → canon:manchester_city  ✓ 同一队
                          │
                          └─→ matches_merged_7d_home = 2
                              matches_merged_3d_home = 1
                                       │
                                       └─→ FootballMultiFactorEngine
                                              flag ON  → congest 读 merged + 3 天档
                                              flag OFF → 今天的行为
```

---

## 9. 验收标准

1. 同一支球队在 epl 与 ucl 各有一场时，`matches_merged_7d_*` 计入两场，`matches_last_7d_*` 仍为一场
2. 两赛事队名写法不同（`Manchester City` / `Man City`）时仍合并为同一队
3. 跨赛事冲突别名（`CEL` laliga vs ucl）不互相匹配
4. 非足球赛事（nba / mlb / nhl）的 fixture 不进入合并历史
5. 任一侧解析失败时退回字符串比较，今天能数上的仍然数得上
6. `matches_merged_3d_*` 只数 3 天内的比赛
7. 当前这场比赛自身被排除（`exclude_match_id`）
8. `matches_last_7d_*` / `schedule_congested_*` 的值与改动前完全一致
9. `FOOTBALL_SCHEDULE_MERGE_ENABLED` 默认 OFF，OFF 时引擎输出与改动前逐位相同
10. `rest_form.py` 零改动，其 15 个现存测试原样通过
11. 后端全量套件保持绿（基线 3644 passed / 11 skipped）

---

## 10. 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 合并历史查询变大（全足球赛事而非单赛事） | 每次 enrich 多扫若干行 | 与现有查询同为一次全表读；别名索引用 `lru_cache`，解析为 O(1) |
| kernel 里只 ingest 了单一联赛 | merged 与单赛事计数相等，收益为 0 | 纯增量、不回退；ingest 覆盖变广后自动生效 |
| 别名表未覆盖的球队跨赛事仍失配 | 合并覆盖率有限 | 退回字符串比较，与今天持平；表可持续补充 |
| 3 天档惩罚与 b2b 档重复叠加 | 过度惩罚 | 分档互斥（elif 链），幅度沿用现有 0.03 |
| 引擎数值变化使已 apply 的 Optuna 参数失配 | 精度回退 | 默认 OFF；`eval_applied_params.py` 可在打开前复核 |

---

## 11. 测试计划

新增测试写在既有 `backend/tests/test_adapter_shared.py` 与 `backend/tests/test_football_multi_factor_engine.py`，不新建文件。`tests/test_rest_form.py` 不改。

| 用例 | 覆盖验收 |
|------|----------|
| epl + ucl 各一场 → merged_7d = 2，last_7d = 1 | 1, 8 |
| 两赛事队名写法不同仍合并 | 2 |
| `CEL` laliga vs ucl 不互相匹配 | 3 |
| nba fixture 不进入合并历史 | 4 |
| 别名表外的队名退回字符串匹配 | 5 |
| 5 天前的比赛不计入 merged_3d | 6 |
| 当前比赛自身不计入 | 7 |
| flag OFF 时引擎输出与无 merged 键时相同 | 9 |
| flag ON 且 merged_7d >= 2 时 congest 生效 | 9 |
| flag ON 且 merged_3d >= 1 时按 b2b 档惩罚 | 9 |
