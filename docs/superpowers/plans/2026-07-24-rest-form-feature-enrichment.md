# Rest / Form Feature Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace flat rest/form defaults in Phase 9 backtest loading with leakage-safe as-of features, and align NBA/MLB/NHL adapters so unknown rest is `None` (not `0`).

**Architecture:** Pure in-memory helpers in `sports/_shared/rest_form.py` compute form (L10 win rate) and rest days strictly before kickoff. `match_loader` batch-enriches scored fixtures. Adapters keep their DB queries but call the same helpers for semantics. `BacktestRunner` and engines are unchanged (already handle `None`).

**Tech Stack:** Python 3.12+, SQLAlchemy kernel DB, pytest. No new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-rest-form-feature-enrichment-design.md`
- Do **not** modify `PredictionKernel`, `domain.py`, or engine fusion formulas
- Do **not** wire pitcher/goalie, B2B BacktestRunner parity, or feature-table migration
- Do **not** auto-apply Optuna candidates after smoke runs
- Do **not** push to origin (standing instruction)
- TDD: RED → GREEN → COMMIT per task
- Team name matching: exact string equality on `home_team` / `away_team`
- Rest unknown: **`None`**; form empty history: **`0.5`**
- After enrich, strip `kickoff_utc` from match_loader return dicts (existing contract)
- Python runner for this machine: `C:\Python314\python.exe` when default `python` lacks project deps

## File Structure

### New files
1. `backend/app/sports/_shared/rest_form.py` — pure helpers + batch enrich
2. `backend/tests/test_rest_form.py` — unit tests for helpers

### Modified files
1. `backend/app/kernel/backtest/match_loader.py` — call enrich; drop flat 2.0/0.5
2. `backend/tests/test_match_loader.py` — integration tests with tmp kernel DB
3. `backend/app/sports/basketball/nba_adapter.py` — rest `None` + helpers
4. `backend/app/sports/baseball/mlb_adapter.py` — same
5. `backend/app/sports/hockey/nhl_adapter.py` — same
6. `docs/ops/RUNBOOK.md` — loader note
7. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — rest/form follow-up done
8. `CHANGELOG.md` — short entry

---

### Task 1: Shared rest/form helpers + unit tests

**Files:**
- Create: `backend/app/sports/_shared/rest_form.py`
- Create: `backend/tests/test_rest_form.py`

**Interfaces:**
- Consumes: none (pure helpers; no DB)
- Produces:
  - `rest_days_as_of(team, kickoff, history, *, exclude_match_id=None) -> float | None`
  - `form_as_of(team, kickoff, history, *, max_matches=10, exclude_match_id=None, default=0.5) -> float`
  - `enrich_matches_rest_form(matches, *, max_form_matches=10) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_rest_form.py
"""Unit tests for as-of rest/form helpers."""
from datetime import datetime, timedelta, timezone

from app.sports._shared.rest_form import (
    enrich_matches_rest_form,
    form_as_of,
    rest_days_as_of,
)

UTC = timezone.utc


def _m(
    mid: str,
    home: str,
    away: str,
    hs: int | None,
    aws: int | None,
    day: int,
) -> dict:
    return {
        "match_id": mid,
        "home_team": home,
        "away_team": away,
        "home_score": hs,
        "away_score": aws,
        "kickoff_utc": datetime(2024, 1, day, 19, 0, tzinfo=UTC),
    }


def test_form_empty_history_returns_default():
    kickoff = datetime(2024, 1, 10, tzinfo=UTC)
    assert form_as_of("A", kickoff, []) == 0.5


def test_rest_empty_history_returns_none():
    kickoff = datetime(2024, 1, 10, tzinfo=UTC)
    assert rest_days_as_of("A", kickoff, []) is None


def test_rest_days_calendar_gap():
    history = [_m("g1", "A", "B", 1, 0, 1)]
    kickoff = datetime(2024, 1, 4, 19, 0, tzinfo=UTC)
    assert rest_days_as_of("A", kickoff, history) == 3.0


def test_form_as_of_excludes_future_and_self():
    # A beat B on day 1; A loses to B on day 5; query as-of day 5 self excluded
    history = [
        _m("g1", "A", "B", 2, 1, 1),
        _m("g2", "B", "A", 3, 1, 5),
    ]
    kickoff = history[1]["kickoff_utc"]
    # only g1 counts → A won 1/1
    assert form_as_of("A", kickoff, history, exclude_match_id="g2") == 1.0
    assert form_as_of("B", kickoff, history, exclude_match_id="g2") == 0.0


def test_form_draw_is_not_win():
    history = [_m("g1", "A", "B", 1, 1, 1)]
    kickoff = datetime(2024, 1, 10, tzinfo=UTC)
    assert form_as_of("A", kickoff, history) == 0.0


def test_form_max_matches_uses_most_recent():
    # 12 games: first 2 losses then 10 wins for A as home
    history = []
    for i in range(1, 13):
        # day i: A home; days 1-2 lose, 3-12 win
        hs, aws = (0, 1) if i <= 2 else (1, 0)
        history.append(_m(f"g{i}", "A", "B", hs, aws, i))
    kickoff = datetime(2024, 1, 20, tzinfo=UTC)
    # last 10 are wins only
    assert form_as_of("A", kickoff, history, max_matches=10) == 1.0


def test_rest_ignores_missing_kickoff_records():
    history = [
        {
            "match_id": "bad",
            "home_team": "A",
            "away_team": "B",
            "home_score": 1,
            "away_score": 0,
            "kickoff_utc": None,
        },
        _m("g1", "A", "B", 1, 0, 1),
    ]
    kickoff = datetime(2024, 1, 3, 19, 0, tzinfo=UTC)
    assert rest_days_as_of("A", kickoff, history) == 2.0


def test_rest_none_when_kickoff_missing():
    history = [_m("g1", "A", "B", 1, 0, 1)]
    assert rest_days_as_of("A", None, history) is None


def test_enrich_preserves_order_and_sets_fields():
    matches = [
        _m("g1", "A", "B", 1, 0, 1),
        _m("g2", "A", "C", 1, 0, 3),
    ]
    out = enrich_matches_rest_form(matches)
    assert [m["match_id"] for m in out] == ["g1", "g2"]
    assert out[0]["rest_days_home"] is None
    assert out[0]["form_home"] == 0.5
    assert out[1]["rest_days_home"] == 2.0
    assert out[1]["form_home"] == 1.0
    # input not mutated
    assert "rest_days_home" not in matches[0]


def test_naive_kickoff_treated_as_utc():
    history = [
        {
            "match_id": "g1",
            "home_team": "A",
            "away_team": "B",
            "home_score": 1,
            "away_score": 0,
            "kickoff_utc": datetime(2024, 1, 1, 12, 0),  # naive
        }
    ]
    kickoff = datetime(2024, 1, 3, 12, 0)  # naive
    assert rest_days_as_of("A", kickoff, history) == 2.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `backend/`):

```bash
C:\Python314\python.exe -m pytest tests/test_rest_form.py -v
```

Expected: FAIL with `ModuleNotFoundError` or import error for `app.sports._shared.rest_form`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/sports/_shared/rest_form.py
"""As-of rest days and form (L10 win rate) for backtest + adapters."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _team_in_match(team: str, m: Mapping[str, Any]) -> bool:
    return m.get("home_team") == team or m.get("away_team") == team


def _team_won(team: str, m: Mapping[str, Any]) -> bool | None:
    hs, aws = m.get("home_score"), m.get("away_score")
    if hs is None or aws is None:
        return None
    if m.get("home_team") == team:
        return int(hs) > int(aws)
    if m.get("away_team") == team:
        return int(aws) > int(hs)
    return None


def rest_days_as_of(
    team: str,
    kickoff: datetime | None,
    history: Sequence[Mapping[str, Any]],
    *,
    exclude_match_id: str | None = None,
) -> float | None:
    as_of = _as_utc(kickoff)
    if as_of is None or not team:
        return None
    prev: datetime | None = None
    for m in history:
        mid = m.get("match_id")
        if exclude_match_id is not None and mid == exclude_match_id:
            continue
        if not _team_in_match(team, m):
            continue
        k = _as_utc(m.get("kickoff_utc"))
        if k is None or k >= as_of:
            continue
        if prev is None or k > prev:
            prev = k
    if prev is None:
        return None
    return float(max(0, (as_of - prev).days))


def form_as_of(
    team: str,
    kickoff: datetime | None,
    history: Sequence[Mapping[str, Any]],
    *,
    max_matches: int = 10,
    exclude_match_id: str | None = None,
    default: float = 0.5,
) -> float:
    as_of = _as_utc(kickoff)
    if not team:
        return default
    candidates: list[tuple[datetime, str, Mapping[str, Any]]] = []
    for m in history:
        mid = str(m.get("match_id") or "")
        if exclude_match_id is not None and mid == exclude_match_id:
            continue
        if not _team_in_match(team, m):
            continue
        if m.get("home_score") is None or m.get("away_score") is None:
            continue
        k = _as_utc(m.get("kickoff_utc"))
        if k is None:
            continue
        if as_of is not None and k >= as_of:
            continue
        candidates.append((k, mid, m))
    if not candidates:
        return default
    candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
    window = candidates[: max(1, max_matches)]
    wins = 0
    for _, _, m in window:
        won = _team_won(team, m)
        if won:
            wins += 1
    return wins / len(window)


def enrich_matches_rest_form(
    matches: list[dict[str, Any]],
    *,
    max_form_matches: int = 10,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in matches:
        copy = dict(m)
        kickoff = m.get("kickoff_utc")
        mid = m.get("match_id")
        home = m.get("home_team") or ""
        away = m.get("away_team") or ""
        copy["rest_days_home"] = rest_days_as_of(
            home, kickoff, matches, exclude_match_id=mid,
        )
        copy["rest_days_away"] = rest_days_as_of(
            away, kickoff, matches, exclude_match_id=mid,
        )
        copy["form_home"] = form_as_of(
            home, kickoff, matches,
            max_matches=max_form_matches, exclude_match_id=mid,
        )
        copy["form_away"] = form_as_of(
            away, kickoff, matches,
            max_matches=max_form_matches, exclude_match_id=mid,
        )
        out.append(copy)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
C:\Python314\python.exe -m pytest tests/test_rest_form.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/sports/_shared/rest_form.py backend/tests/test_rest_form.py
git commit -m "feat(sports): add as-of rest/form helpers for backtest"
```

---

### Task 2: Wire match_loader + loader tests

**Files:**
- Modify: `backend/app/kernel/backtest/match_loader.py`
- Modify: `backend/tests/test_match_loader.py`

**Interfaces:**
- Consumes: `enrich_matches_rest_form` from `app.sports._shared.rest_form`
- Produces: `load_sport_matches_for_backtest` returns real rest/form; still strips `kickoff_utc`

- [ ] **Step 1: Extend failing loader tests**

Append to `backend/tests/test_match_loader.py` (keep existing time_series tests):

```python
# backend/tests/test_match_loader.py — append
from datetime import datetime, timezone

import pytest

from app.kernel.backtest.match_loader import (
    load_sport_matches_for_backtest,
    time_series_split,
)


@pytest.fixture
def kernel_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "loader.db")
    monkeypatch.setenv("KERNEL_DB_PATH", db_path)
    from app.kernel import kernel_db
    # reset cached engine/session if any
    if hasattr(kernel_db, "_engine"):
        kernel_db._engine = None  # type: ignore[attr-defined]
    if hasattr(kernel_db, "reset_kernel_db"):
        try:
            kernel_db.reset_kernel_db()
        except Exception:
            pass
    engine = kernel_db._get_engine(db_path)
    kernel_db.KernelBase.metadata.create_all(engine)
    return db_path


def _seed_three_nba_games(kernel_db):
    from app.kernel.kernel_db import (
        KernelMatchFixture,
        KernelMatchResult,
        get_kernel_session,
    )

    UTC = timezone.utc
    session = get_kernel_session()
    try:
        rows = [
            ("nba-1", "Lakers", "Celtics", 100, 90, datetime(2024, 1, 1, 0, 0, tzinfo=UTC)),
            ("nba-2", "Lakers", "Heat", 110, 100, datetime(2024, 1, 3, 0, 0, tzinfo=UTC)),
            ("nba-3", "Celtics", "Lakers", 95, 96, datetime(2024, 1, 6, 0, 0, tzinfo=UTC)),
        ]
        for mid, home, away, hs, aws, ko in rows:
            session.add(
                KernelMatchFixture(
                    match_id=mid,
                    competition="nba",
                    home_team=home,
                    away_team=away,
                    kickoff_utc=ko,
                    season="2023-24",
                    stage="regular",
                    status="finished",
                    home_score=hs,
                    away_score=aws,
                )
            )
            session.add(
                KernelMatchResult(
                    match_id=mid,
                    home_score=hs,
                    away_score=aws,
                    outcome="home_win" if hs > aws else "away_win",
                    finished_at=ko,
                )
            )
        session.commit()
    finally:
        session.close()


def test_load_sport_matches_real_rest_form(kernel_db):
    _seed_three_nba_games(kernel_db)
    matches = load_sport_matches_for_backtest("nba")
    assert len(matches) == 3
    by_id = {m["match_id"]: m for m in matches}
    # first game: no prior rest
    assert by_id["nba-1"]["rest_days_home"] is None
    assert by_id["nba-1"]["form_home"] == 0.5
    # Lakers home again after 2 calendar days
    assert by_id["nba-2"]["rest_days_home"] == 2.0
    assert by_id["nba-2"]["form_home"] == 1.0  # won game 1
    # no flat defaults across board
    rests = [m["rest_days_home"] for m in matches]
    assert not all(r == 2.0 for r in rests)
    forms = [m["form_home"] for m in matches]
    assert not all(f == 0.5 for f in forms)
    # kickoff stripped
    assert all("kickoff_utc" not in m for m in matches)
```

Note: if `get_kernel_session` ignores `KERNEL_DB_PATH` after first import, copy the exact isolation pattern from `tests/test_optimized_params_store.py` (set env before importing/creating engine, call `KernelBase.metadata.create_all`). Prefer that pattern if the first fixture returns 0 matches.

- [ ] **Step 2: Run new loader tests — expect FAIL**

```bash
C:\Python314\python.exe -m pytest tests/test_match_loader.py::test_load_sport_matches_real_rest_form -v
```

Expected: FAIL because loader still hardcodes 2.0 / 0.5 (or fixture isolation issue — fix fixture first if import/DB empty).

- [ ] **Step 3: Implement match_loader enrich**

Replace flat assignment block in `load_sport_matches_for_backtest` with:

```python
from app.sports._shared.rest_form import enrich_matches_rest_form

# ... after building matches list with kickoff_utc, scores, teams ...
matches = enrich_matches_rest_form(matches)
matches.sort(
    key=lambda m: (
        m["season"],
        m["kickoff_utc"].isoformat() if m.get("kickoff_utc") else "",
        m["match_id"],
    ),
)
for m in matches:
    m.pop("kickoff_utc", None)
return matches
```

Remove lines that set `"rest_days_home": 2.0` etc.

Full function body should look like:

```python
def load_sport_matches_for_backtest(sport: str) -> list[dict[str, Any]]:
    from app.kernel.kernel_db import (
        KernelMatchFixture,
        KernelMatchResult,
        get_kernel_session,
    )
    from app.sports._shared.rest_form import enrich_matches_rest_form

    session = get_kernel_session()
    try:
        rows = (
            session.query(KernelMatchFixture, KernelMatchResult)
            .join(
                KernelMatchResult,
                KernelMatchFixture.match_id == KernelMatchResult.match_id,
            )
            .filter(KernelMatchFixture.competition == sport)
            .all()
        )
        matches: list[dict[str, Any]] = []
        for fixture, result in rows:
            if result.home_score is None or result.away_score is None:
                continue
            season_raw = fixture.season or "0"
            try:
                season_key: int | str = int(str(season_raw).split("-")[0])
            except (TypeError, ValueError):
                season_key = str(season_raw)
            matches.append({
                "match_id": fixture.match_id,
                "home_team": fixture.home_team,
                "away_team": fixture.away_team,
                "home_score": int(result.home_score),
                "away_score": int(result.away_score),
                "season": season_key,
                "is_playoff": (fixture.stage or "").lower() in {
                    "playoff", "playoffs", "postseason",
                },
                "kickoff_utc": fixture.kickoff_utc,
            })

        matches = enrich_matches_rest_form(matches)
        matches.sort(
            key=lambda m: (
                m["season"],
                m["kickoff_utc"].isoformat() if m.get("kickoff_utc") else "",
                m["match_id"],
            ),
        )
        for m in matches:
            m.pop("kickoff_utc", None)
        return matches
    finally:
        session.close()
```

- [ ] **Step 4: Run all match_loader tests**

```bash
C:\Python314\python.exe -m pytest tests/test_match_loader.py -v
```

Expected: PASS (including time_series tests).

- [ ] **Step 5: Manual smoke on real DB (optional but recommended)**

```bash
C:\Python314\python.exe -c "from app.kernel.backtest.match_loader import load_sport_matches_for_backtest; m=load_sport_matches_for_backtest('nba'); print(len(m), m[100] if len(m)>100 else m[:2]); rests=set(x.get('rest_days_home') for x in m[:200]); forms=set(round(x.get('form_home',0),2) for x in m[:200]); print('rest sample', list(rests)[:10]); print('form sample', list(forms)[:10])"
```

Expected: `len` ~3962; rest set has multiple values / None; form not only 0.5.

- [ ] **Step 6: Commit**

```bash
git add backend/app/kernel/backtest/match_loader.py backend/tests/test_match_loader.py
git commit -m "feat(backtest): enrich rest/form as-of in match_loader"
```

---

### Task 3: Adapter alignment (NBA / MLB / NHL)

**Files:**
- Modify: `backend/app/sports/basketball/nba_adapter.py` (`_compute_form`, `_compute_rest_days`)
- Modify: `backend/app/sports/baseball/mlb_adapter.py` (same methods)
- Modify: `backend/app/sports/hockey/nhl_adapter.py` (same methods)

**Interfaces:**
- Consumes: `form_as_of`, `rest_days_as_of`
- Produces: rest methods return `float | None`; form still `float` default 0.5

- [ ] **Step 1: Refactor one adapter (NBA) as template**

Replace `_compute_form` / `_compute_rest_days` in `nba_adapter.py` with logic that:

1. Queries fixtures for that team (as today: competition filter + home/away).
2. Maps each row to `{match_id, home_team, away_team, home_score, away_score, kickoff_utc}`.
3. For form: use `form_as_of(team, before=now or kickoff, history)` — for live form without as-of kickoff on form path today, pass `kickoff=datetime.now(timezone.utc)` **or** better: when called from `fetch_all_data`, pass match kickoff into `_compute_form(team, as_of=match.kickoff_utc)`.

**Preferred signature change (cleaner as-of online):**

```python
def _compute_form(self, team_name: str, as_of: datetime | None = None) -> float:
    ...

def _compute_rest_days(self, team_name: str, kickoff_utc: datetime) -> float | None:
    ...
```

Call sites:

```python
form_home = self._compute_form(home_name, as_of=match.kickoff_utc)
form_away = self._compute_form(away_name, as_of=match.kickoff_utc)
rest_home = self._compute_rest_days(home_name, match.kickoff_utc)
rest_away = self._compute_rest_days(away_name, match.kickoff_utc)
```

Example rest implementation body:

```python
def _compute_rest_days(self, team_name: str, kickoff_utc: datetime) -> float | None:
    session = get_kernel_session()
    try:
        from sqlalchemy import or_, select
        from app.sports._shared.rest_form import rest_days_as_of

        query = (
            select(KernelMatchFixture)
            .where(
                KernelMatchFixture.competition == "nba",
                or_(
                    KernelMatchFixture.home_team == team_name,
                    KernelMatchFixture.away_team == team_name,
                ),
            )
        )
        fixtures = session.execute(query).scalars().all()
        history = [
            {
                "match_id": f.match_id,
                "home_team": f.home_team,
                "away_team": f.away_team,
                "home_score": f.home_score,
                "away_score": f.away_score,
                "kickoff_utc": f.kickoff_utc,
            }
            for f in fixtures
        ]
        return rest_days_as_of(team_name, kickoff_utc, history)
    except Exception:  # noqa: BLE001
        return None
    finally:
        session.close()
```

Form body analogous with `form_as_of` and `as_of` kickoff; on exception return `0.5`.

Update docstring: rest returns `None` if unknown (not 0).

`b2b_home` already uses `rest_home is not None and float(rest_home) <= 1.0` — remains correct when rest is None (False).

- [ ] **Step 2: Mirror for MLB (`competition == "mlb"`) and NHL (`"nhl"`)**

Same pattern; change competition string only.

- [ ] **Step 3: Run related tests**

```bash
C:\Python314\python.exe -m pytest tests/test_nba_adapter.py tests/test_basketball_engine.py tests/test_baseball_engine.py tests/test_hockey_engine.py tests/test_rest_form.py tests/test_match_loader.py -q --tb=line
```

Expected: PASS. Fix any tests that asserted rest `0` for unknown.

- [ ] **Step 4: Commit**

```bash
git add backend/app/sports/basketball/nba_adapter.py backend/app/sports/baseball/mlb_adapter.py backend/app/sports/hockey/nhl_adapter.py
git commit -m "fix(sports): as-of rest/form helpers in NBA/MLB/NHL adapters"
```

---

### Task 4: Docs + optional Optuna smoke

**Files:**
- Modify: `docs/ops/RUNBOOK.md` (Phase 9 Optuna section)
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` (P1-A3 note)
- Modify: `CHANGELOG.md` (Unreleased)

- [ ] **Step 1: RUNBOOK**

In Phase 9 Optuna section, add after chronological split bullet:

```markdown
Loader fills **as-of rest/form** from fixtures (not flat 2.0/0.5).
Unknown rest is `None` (factor unavailable). Re-run Optuna after this
change before trusting new weights; do not auto-apply.
```

- [ ] **Step 2: Backlog**

Update P1-A3 next-step text to note rest/form real features landed 2026-07-24; recommend re-tune + manual apply.

- [ ] **Step 3: CHANGELOG**

```markdown
### Rest/form as-of features (Phase 9 follow-up)
- `sports/_shared/rest_form.py`: leakage-safe form L10 + rest days
- `match_loader` uses enrich (no flat defaults)
- NBA/MLB/NHL adapters: unknown rest → None; form as-of kickoff
```

- [ ] **Step 4: Optional smoke (no apply)**

```bash
C:\Python314\python.exe scripts/run_phase9_optimize.py --sport nba --n-trials 10
```

Expected: completes; candidate may differ from prior 80-trial flat run. Do **not** call `store.apply`.

- [ ] **Step 5: Commit docs**

```bash
git add docs/ops/RUNBOOK.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md CHANGELOG.md
git commit -m "docs: rest/form as-of enrichment for Phase 9 backtest"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| `rest_form.py` helpers | Task 1 |
| as-of kickoff / no leakage | Task 1–2 |
| rest None / form 0.5 defaults | Task 1, 3 |
| match_loader enrich + strip kickoff | Task 2 |
| adapter alignment | Task 3 |
| unit + loader tests | Task 1–2 |
| docs / no auto-apply | Task 4 |
| pitcher/goalie / B2B runner / schema | Out of scope (not in tasks) |

## Self-review notes

- No TBD placeholders in steps.
- Signatures consistent: `rest_days_as_of` → `float | None`; `form_as_of` → `float`.
- Adapter `days_since_last_match: rest_home` may become None — feature builder accepts Optional; engines use rest pair only when both non-None.
- If kernel DB fixture isolation fails in Task 2, copy exact monkeypatch pattern from `tests/test_optimized_params_store.py`.
