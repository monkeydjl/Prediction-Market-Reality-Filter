# 足球 form / H2H 深化实施计划（P1-F1 / P1-F4 残项）

**Date:** 2026-08-06
**Design:** [`2026-08-06-football-form-h2h-deepening-design.md`](../specs/2026-08-06-football-form-h2h-deepening-design.md)
**基线:** 后端全量 3614 passed / 11 skipped（`../.venv/Scripts/python.exe -m pytest tests`）

三块彼此独立，按顺序各自 RED → GREEN → 提交。任何一块出问题不阻塞其余两块。

---

## Task 1 — 别名感知匹配（P1-F1 / P1-F4「别名」）

**文件:** `backend/app/sports/football/club_form.py`、`backend/tests/test_club_form.py`

### Step 1 — 写失败测试（RED）

在 `test_club_form.py` 新增 `TestAliasMatching`：

- [x] 库存 `Manchester City` + `Chelsea`，查询 `Man City` → `team_form_from_kernel` 返回非 None
- [x] 同上但 `competition=None` → 返回 None（别名层不启用）
- [x] 同上但 `competition="not_a_league"` → 返回 None
- [x] nba 的 `BOS`（凯尔特人）不匹配 mlb 的 `BOS`（红袜）—— 跨赛事隔离
- [x] 一侧解析失败（库里存的是别名表没有的队名）→ 退回字符串比较，仍能匹配
- [x] `h2h_from_kernel("Man City", "Spurs", competition="epl")` 命中库里的 `Manchester City` vs `Tottenham Hotspur`
- [x] `h2h_from_kernel("Spurs", "Tottenham Hotspur", competition="epl")` → None（解析后同队）

跑一次确认全红。

### Step 2 — 实现（GREEN）

- [x] 加内部 `_match_key(name, alias_index)`：别名索引命中则返回 `canon:<id>`，否则返回 `_normalize(name)` 的结果。加 `canon:` 前缀是为了让 canonical id 与恰好同名的原始串不会意外相等。
- [x] 加 `_alias_index(competition)`：把 `TEAM_ALIASES[comp]` 压成一次性的小写 dict，`competition` 为空 / 未知时返回 `None`。用 `functools.lru_cache` 缓存，避免每行 fixture 重建（对应设计 §8 的性能风险）。
- [x] `team_form_from_kernel`：`key` 与逐行的 `_normalize(h)` / `_normalize(a)` 全部换成 `_match_key`。注意 `_points_result` 与 goals_for 归属判断内部也各有一次 `_normalize` 比较，必须一起换，否则会出现「行匹配上了但胜负判给了另一边」的错位。
- [x] `h2h_from_kernel`：`home_key` / `away_key` / `fh` / `fa` 全部换成 `_match_key`；`home_key == away_key` 的自我对阵检查移到解析之后。

### Step 3 — 验证

- [x] `python -m pytest tests/test_club_form.py -q` 全绿
- [x] `python -m pytest tests/test_team_aliases.py tests/test_adapter_shared.py -q` 无回归
- [x] `ruff check app/`

---

## Task 2 — 加权近 N form（P1-F1「加权近 N」）

**文件:** `backend/app/sports/football/club_form.py`、`backend/app/sports/football/adapters/_shared.py`、`backend/tests/test_club_form.py`

### Step 1 — 写失败测试（RED）

新增 `TestWeightedPointsFormRate`：

- [x] `["W"]*5` → 1.0；`["L"]*5` → 0.0；`["D"]*5` → 1/3
- [x] `["W","W","L","L"]` 严格大于 `["L","L","W","W"]`（同计数、顺序相反）
- [x] 空序列 → None
- [x] 未知字符（如 `"?"`）被忽略而不是当成 0 分
- [x] `half_life` 越小，近期权重越高（同一序列下差值单调）

以及 `team_form_from_kernel` 的返回新增两键的断言。

### Step 2 — 实现（GREEN）

- [x] `weighted_points_form_rate(results, *, half_life=5.0)`，权重 `0.5 ** (i / half_life)`，单场得分 W=1.0 / D=1/3 / L=0.0
- [x] `team_form_from_kernel` 收集倒序 `recent_results` 并算出 `form_rate_weighted`；`wins/draws/losses/played` 不动
- [x] `_shared.py` 的 enrich：`form_home` / `form_away` 优先读 `form_rate_weighted`，缺失时退回 `points_form_rate`

### Step 3 — 验证

- [x] `pytest tests/test_club_form.py tests/test_adapter_shared.py -q`
- [x] 确认 `points_form_rate` 的 9 个原测试一字未改仍通过（验收标准 6）

---

## Task 3 — H2H 主客场分拆（P1-F4「主客场分拆」）

**文件:** `club_form.py`、`adapters/_shared.py`、`engines/football_multi_factor_engine.py`、`core/config.py`、`.env.example`、对应测试

### Step 1 — 写失败测试（RED）

- [x] `TestH2hVenueSplit`：库里 Arsenal 主场 vs Chelsea 一场、Chelsea 主场一场 → 以 Arsenal 为当前主队时 `home_venue_matches == 1`
- [x] 全部历史交锋都在对方主场 → `home_venue_matches == 0`（不是 None）
- [x] enrich 写入 `custom.h2h_home_venue_*`
- [x] 引擎：flag OFF 时输出与不带 venue 键时逐位相同
- [x] 引擎：flag ON 且 `home_venue_matches >= 4` 时，h2h 因子明显偏向同场地记录

### Step 2 — 实现（GREEN）

- [x] `h2h_from_kernel` 的 meetings 元组带上「当前主队是否做东」标志，累加出四个 `home_venue_*`
- [x] `_shared.py` 写 `custom.h2h_home_venue_win_rate` / `_draw_rate` / `_matches`
- [x] `config.py` 加 `FOOTBALL_H2H_VENUE_SPLIT_ENABLED: bool = False`，`.env.example` 同步（P1-F7 的教训：配置键必须同时进 `.env.example`）
- [x] 引擎 h2h 因子按 `alpha = min(1.0, matches / 4)` 混合

### Step 3 — 验证

- [x] 相关测试文件全绿
- [x] 后端全量套件对齐基线
- [x] `ruff check app/`

---

## Task 4 — 文档与回写

- [x] `OPPORTUNITY_BACKLOG_2026-07-17.md` 的 P1-F1 / P1-F4 行补 `✅ 2026-08-06` 与残项收敛说明（§14 维护约定要求）
- [x] `CHANGELOG.md` 记一条
- [x] 本计划勾选完成

---

## 规范扫描

- [x] 无占位符 / TODO / `pass  # implement later`
- [x] 新配置键同时出现在 `config.py` 与 `.env.example`
- [x] 新 flag 默认 OFF
- [x] 无新表、无 `domain.py` 契约变更
- [x] 注释语言与所在文件一致（`club_form.py` 英文、backlog 中文）
