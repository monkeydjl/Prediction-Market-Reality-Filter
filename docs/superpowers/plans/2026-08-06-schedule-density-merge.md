# 赛程密度跨赛事合并实施计划（P1-F2 残项）

**Date:** 2026-08-06
**Design:** [`2026-08-06-schedule-density-merge-design.md`](../specs/2026-08-06-schedule-density-merge-design.md)
**基线:** 后端全量 3644 passed / 11 skipped（`../.venv/Scripts/python.exe -m pytest tests`）

三块按顺序 RED → GREEN → 提交。Task 1 是 Task 2 的前置（比较键要先能共用）。

---

## Task 1 — 把别名比较键提到共享层

**文件:** `backend/app/sports/_shared/team_aliases.py`、`backend/app/sports/football/club_form.py`、`backend/tests/test_team_aliases.py`

P1-F1/F4 把 `_alias_index` / `_match_key` 写在了 `club_form.py` 里。现在 `_shared.py` 的密度路径也要用同一套键，复制一份会让两处逻辑漂移。

### Step 1 — 写失败测试（RED）

在 `test_team_aliases.py` 新增 `TestComparisonKey`：

- [x] `comparison_key("Man City", "epl") == comparison_key("Manchester City", "epl")`
- [x] `comparison_key("Man City", "ucl") == comparison_key("Manchester City", "epl")`（canonical 跨赛事稳定）
- [x] `comparison_key("CEL", "laliga") != comparison_key("CEL", "ucl")`（冲突别名按赛事分开）
- [x] `competition=None` → 退回 normalize，且不等于任何 canonical 键
- [x] 别名表外的队名 → 大小写与空白折叠后相等
- [x] 空字符串 → 空字符串

### Step 2 — 实现（GREEN）

- [x] `team_aliases.py` 加 `comparison_key(name, competition) -> str`，内部 `@lru_cache` 的 `_alias_index(competition)`，语义与 `club_form._match_key` 完全一致（canonical 命中返回 `canon:<id>`，否则 normalize）
- [x] `club_form.py` 的 `_alias_index` / `_match_key` 改为委托新函数，删掉重复实现与不再需要的 `lru_cache` 导入
- [x] 保持 `club_form` 内部调用点签名不变，避免动到 P1-F1/F4 刚验证过的逻辑

### Step 3 — 验证

- [x] `pytest tests/test_team_aliases.py tests/test_club_form.py -q` 全绿（含 P1-F1/F4 的 30 个新测试）
- [x] `ruff check app/`

---

## Task 2 — 跨足球赛事合并 + 3 天窗口

**文件:** `backend/app/sports/football/adapters/_shared.py`、`backend/tests/test_adapter_shared.py`

### Step 1 — 写失败测试（RED）

新增 `TestMergedScheduleDensity`：

- [x] epl 一场 + ucl 一场（同队、7 天内）→ `matches_merged_7d_home == 2`，同时 `matches_last_7d_home` 仍为 1
- [x] 两赛事队名写法不同（`Manchester City` / `Man City`）→ 仍为 2
- [x] `CEL`（laliga）与 `CEL`（ucl）→ 不互相计入
- [x] nba fixture 在 7 天内 → 不进入合并计数
- [x] 别名表外的队名（`Obscure Town FC`）→ 退回字符串匹配，仍能数上
- [x] 5 天前的比赛 → 计入 `merged_7d` 但不计入 `merged_3d`
- [x] 当前这场比赛自身 → 不计入（`exclude_match_id`）
- [x] 现有 `TestScheduleDensityEnrich` 全部原样通过（验收 8）

### Step 2 — 实现（GREEN）

- [x] 加 `_merged_fixture_history(before)`：查 `_FOOTBALL_COMPETITIONS`（从 `app.kernel.factor_registry` 复用，不另建集合）内的 fixture，每行用**该行自己的 competition** 调 `comparison_key` 解析双方队名后写入 `home_team` / `away_team`
- [x] enrich 里查询名用**当前比赛的 competition** 解析，再调 `matches_in_window_as_of`（`rest_form.py` 不改）
- [x] 写 `custom["matches_merged_7d_home"|"_away"]` 与 `custom["matches_merged_3d_home"|"_away"]`
- [x] 现有 `matches_last_7d_*` / `schedule_congested_*` 代码路径一行不动

### Step 3 — 验证

- [x] `pytest tests/test_adapter_shared.py tests/test_rest_form.py -q`
- [x] 确认 `test_rest_form.py` 未被修改（`git diff --stat` 不含该文件）

---

## Task 3 — 引擎分档 + flag

**文件:** `engines/football_multi_factor_engine.py`、`core/config.py`、`.env.example`、`tests/test_football_multi_factor_engine.py`

### Step 1 — 写失败测试（RED）

- [x] flag OFF 且带 merged 键 → 输出与不带 merged 键时逐位相同
- [x] flag ON 且 `matches_merged_7d_home >= 2` → congest 惩罚生效
- [x] flag ON 且 `matches_merged_3d_home >= 1` → 按 b2b 档（0.03）而非 congest 档
- [x] flag ON 但 merged 键缺失 → 退回现有 `schedule_congested_*`
- [x] （追加）flag ON 且两侧同样拥堵 → 无净效应

### Step 2 — 实现（GREEN）

- [x] `config.py` 加 `FOOTBALL_SCHEDULE_MERGE_ENABLED: bool = False`
- [x] `.env.example` 同步该键（P1-F7 的教训：配置键必须同时进 `.env.example`）
- [x] rest 因子的分档链插入 3 天档，与 b2b / congest 互斥

### Step 3 — 验证

- [x] 相关测试文件全绿（engine 33 passed）
- [x] 后端全量套件对齐基线（3644 + 新增）
- [x] `ruff check app/`
- [x] `python -m scripts.generate_types --check`（未改 `domain.py`，输出 up to date）

---

## Task 4 — 文档与回写

- [x] `OPPORTUNITY_BACKLOG_2026-07-17.md` 的 P1-F2 行补 `✅ 2026-08-06` 与残项收敛说明（§14 维护约定要求）
- [x] `CHANGELOG.md` 记一条
- [x] 本计划勾选完成

---

## 规范扫描

- [x] 无占位符 / TODO / `pass  # implement later`
- [x] 新配置键同时出现在 `config.py` 与 `.env.example`
- [x] 新 flag 默认 OFF
- [x] 无新表、无 `domain.py` 契约变更
- [x] `rest_form.py` 与 `tests/test_rest_form.py` 零改动
- [x] 注释语言与所在文件一致（后端代码英文、backlog 中文）
