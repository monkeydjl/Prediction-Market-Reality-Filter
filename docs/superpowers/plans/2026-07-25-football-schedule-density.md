# Football Schedule Density (P1-F2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject true 7-day prior match counts into football `custom` and drive `schedule_congested_*` from those counts (with rest ≤ 2 only as fallback) so MultiFactor rest soft path reflects real schedule density.

**Architecture:** Pure `matches_in_window_as_of` on `rest_form` counts prior fixtures in a calendar-day window. Football `enrich_situational_features` loads kernel fixtures (no score join required), writes `matches_last_7d_*`, sets congestion from count ≥ 2 when known, keeps `b2b_*` rest-based. Engine weights/coefficients unchanged.

**Tech Stack:** Python 3.12+, pytest, existing SQLAlchemy kernel DB. No new dependencies. No network.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-football-schedule-density-design.md`
- Window: **`window_days = 7`**
- Congested when count known: **`matches_last_7d >= 2`**
- When count known and **&lt; 2**: `schedule_congested_* = False` even if `rest_days <= 2`
- When count is **None**: fallback `schedule_congested_* = rest_days <= 2` if rest known
- **b2b_* unchanged**: `rest_days <= 1` only
- Count **includes unfinished fixtures** (scores may be null)
- Helper team match: **exact** `home_team` / `away_team` equality (same as `rest_days_as_of`)
- Do **not** change MultiFactor weight table or rest edge coefficients (±0.03 b2b / ±0.015 congest)
- Do **not** add live API, env vars, or DB schema
- Do **not** push to origin (standing instruction)
- TDD: RED → GREEN → COMMIT per task
- Python runner: `C:\Python314\python.exe` with `$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"`

## File Structure

### Created files
None required (extend existing modules/tests).

### Modified files
1. `backend/app/sports/_shared/rest_form.py` — add `matches_in_window_as_of`
2. `backend/tests/test_rest_form.py` — window unit tests
3. `backend/app/sports/football/adapters/_shared.py` — density inject in `enrich_situational_features`
4. `backend/tests/test_adapter_shared.py` — enrich density / congest override tests
5. `backend/tests/test_football_multi_factor_engine.py` — custom congest with long rest
6. `CHANGELOG.md` — Unreleased note
7. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-F2 row

### Unchanged (verify only)
1. `backend/app/sports/football/engines/football_multi_factor_engine.py` — already reads `custom.schedule_congested_*`
2. `backend/app/kernel/prediction_kernel.py` — no changes

---

### Task 1: `matches_in_window_as_of` unit tests (RED)

**Files:**
- Modify: `backend/tests/test_rest_form.py`
- (No production API yet — import will fail until Task 2)

**Interfaces:**
- Consumes: (not yet) `app.sports._shared.rest_form.matches_in_window_as_of`
- Produces: failing tests defining window contract for Task 2

- [ ] **Step 1: Extend imports and append tests**

Update the import block at top of `backend/tests/test_rest_form.py`:

```python
from app.sports._shared.rest_form import (
    enrich_matches_rest_form,
    form_as_of,
    matches_in_window_as_of,
    rest_days_as_of,
)
```

Append at end of file:

```python
def test_matches_in_window_none_when_kickoff_or_team_missing():
    history = [_m("g1", "A", "B", 1, 0, 1)]
    assert matches_in_window_as_of("", datetime(2024, 1, 10, tzinfo=UTC), history) is None
    assert matches_in_window_as_of("A", None, history) is None


def test_matches_in_window_counts_two_within_seven_days():
    history = [
        _m("g1", "A", "B", 1, 0, 1),
        _m("g2", "C", "A", None, None, 4),  # unfinished still counts
        _m("g3", "A", "D", 2, 1, 10),
    ]
    kickoff = datetime(2024, 1, 10, 19, 0, tzinfo=UTC)
    # g1: day 1 → 9 days before → outside 7
    # g2: day 4 → 6 days before → inside
    # g3 excluded as self
    assert matches_in_window_as_of(
        "A", kickoff, history, window_days=7, exclude_match_id="g3",
    ) == 1


def test_matches_in_window_counts_two_when_two_inside():
    history = [
        _m("g1", "A", "B", 1, 0, 5),
        _m("g2", "A", "C", 0, 0, 7),
        _m("g3", "A", "D", 1, 0, 10),
    ]
    kickoff = datetime(2024, 1, 10, 19, 0, tzinfo=UTC)
    assert matches_in_window_as_of(
        "A", kickoff, history, window_days=7, exclude_match_id="g3",
    ) == 2


def test_matches_in_window_zero_when_only_outside():
    history = [_m("g1", "A", "B", 1, 0, 1)]
    kickoff = datetime(2024, 1, 20, 19, 0, tzinfo=UTC)
    assert matches_in_window_as_of("A", kickoff, history, window_days=7) == 0


def test_matches_in_window_excludes_future():
    history = [
        _m("g1", "A", "B", 1, 0, 5),
        _m("g2", "A", "C", 1, 0, 15),
    ]
    kickoff = datetime(2024, 1, 10, 19, 0, tzinfo=UTC)
    assert matches_in_window_as_of("A", kickoff, history, window_days=7) == 1


def test_matches_in_window_boundary_day_included():
    """Prior match with (as_of - k).days == window_days is included."""
    history = [_m("g1", "A", "B", 1, 0, 3)]
    kickoff = datetime(2024, 1, 10, 19, 0, tzinfo=UTC)
    # days gap = 7
    assert matches_in_window_as_of("A", kickoff, history, window_days=7) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_rest_form.py" -q --tb=short
```

Expected: FAIL — `ImportError` / cannot import `matches_in_window_as_of`.

- [ ] **Step 3: Commit tests only**

```powershell
git add backend/tests/test_rest_form.py
git commit -m "test(football): failing P1-F2 matches_in_window unit tests"
```

---

### Task 2: Implement `matches_in_window_as_of` (GREEN)

**Files:**
- Modify: `backend/app/sports/_shared/rest_form.py`
- Test: `backend/tests/test_rest_form.py`

**Interfaces:**
- Consumes: existing `_as_utc`, `_team_in_match`
- Produces:

```python
def matches_in_window_as_of(
    team: str,
    kickoff: datetime | None,
    history: Sequence[Mapping[str, Any]],
    *,
    window_days: int = 7,
    exclude_match_id: str | None = None,
) -> int | None:
    ...
```

- [ ] **Step 1: Add function after `rest_days_as_of` (before `form_as_of`)**

```python
def matches_in_window_as_of(
    team: str,
    kickoff: datetime | None,
    history: Sequence[Mapping[str, Any]],
    *,
    window_days: int = 7,
    exclude_match_id: str | None = None,
) -> int | None:
    """Count prior matches for team within window_days before kickoff.

    Includes unfinished fixtures (scores may be null). Exact team name match.
    Returns None when team empty or kickoff missing; otherwise int >= 0.
    """
    as_of = _as_utc(kickoff)
    if as_of is None or not team:
        return None
    days = max(0, int(window_days))
    count = 0
    for m in history:
        mid = m.get("match_id")
        if exclude_match_id is not None and mid == exclude_match_id:
            continue
        if not _team_in_match(team, m):
            continue
        k = _as_utc(m.get("kickoff_utc"))
        if k is None or k >= as_of:
            continue
        gap = (as_of - k).days
        if 0 <= gap <= days:
            count += 1
    return count
```

- [ ] **Step 2: Run tests to verify they pass**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_rest_form.py" -q --tb=short
```

Expected: PASS (all rest_form tests including new ones).

- [ ] **Step 3: Commit**

```powershell
git add backend/app/sports/_shared/rest_form.py backend/tests/test_rest_form.py
git commit -m "feat(shared): matches_in_window_as_of for schedule density (P1-F2)"
```

---

### Task 3: Enrich inject + adapter tests (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/football/adapters/_shared.py` (`enrich_situational_features` schedule density block)
- Modify: `backend/tests/test_adapter_shared.py`

**Interfaces:**
- Consumes: `matches_in_window_as_of(team, kickoff, history, window_days=7, exclude_match_id=...) -> int | None`
- Produces: `custom.matches_last_7d_{home,away}`; `schedule_congested_*` per density rules; `b2b_*` still rest-only

- [ ] **Step 1: Write failing enrich tests**

Append to `backend/tests/test_adapter_shared.py` (ensure `enrich_situational_features` is imported):

Update import:

```python
from app.sports.football.adapters._shared import (
    fetch_team_elo,
    fetch_elo_and_odds,
    query_fixture,
    query_result,
    build_match_identity,
    build_match_outcome,
    save_fixture,
    enrich_situational_features,
)
```

Append:

```python
class TestScheduleDensityEnrich:
    def test_matches_last_7d_and_congest_from_count(self):
        """count>=2 sets congest True even when rest_days > 2."""
        match = _make_match("ucl-dense")
        raw = {
            "team": {},
            "general": {"rest_days_home": 4.0, "rest_days_away": 4.0},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        history = [
            {
                "match_id": "ucl-1",
                "home_team": "Real Madrid CF",
                "away_team": "X",
                "kickoff_utc": datetime(2025, 9, 10, 20, 0, tzinfo=timezone.utc),
            },
            {
                "match_id": "ucl-2",
                "home_team": "Y",
                "away_team": "Real Madrid CF",
                "kickoff_utc": datetime(2025, 9, 13, 20, 0, tzinfo=timezone.utc),
            },
            {
                "match_id": "ucl-dense",
                "home_team": "Real Madrid CF",
                "away_team": "FC Bayern München",
                "kickoff_utc": match.kickoff_utc,
            },
        ]
        with patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=history,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ):
            # Avoid club_form DB; rest already set on raw
            with patch(
                "app.sports.football.club_form.team_form_from_kernel",
                return_value=None,
            ):
                enrich_situational_features(raw, match)

        assert raw["custom"]["matches_last_7d_home"] == 2
        assert raw["custom"]["schedule_congested_home"] is True
        assert raw["custom"]["b2b_home"] is False  # rest 4

    def test_count_one_overrides_rest_proxy_congest(self):
        """Known count < 2 → congest False even if rest_days <= 2."""
        match = _make_match("ucl-sparse")
        raw = {
            "team": {},
            "general": {"rest_days_home": 2.0, "rest_days_away": 5.0},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        history = [
            {
                "match_id": "ucl-1",
                "home_team": "Real Madrid CF",
                "away_team": "X",
                "kickoff_utc": datetime(2025, 9, 14, 20, 0, tzinfo=timezone.utc),
            },
            {
                "match_id": "ucl-sparse",
                "home_team": "Real Madrid CF",
                "away_team": "FC Bayern München",
                "kickoff_utc": match.kickoff_utc,
            },
        ]
        with patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=history,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert raw["custom"]["matches_last_7d_home"] == 1
        assert raw["custom"]["schedule_congested_home"] is False
        assert raw["custom"]["b2b_home"] is False

    def test_no_history_falls_back_to_rest_congest(self):
        match = _make_match("ucl-fallback")
        raw = {
            "team": {},
            "general": {"rest_days_home": 1.0, "rest_days_away": 5.0},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.sports.football.adapters._shared._fixture_history_for_density",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert "matches_last_7d_home" not in raw["custom"]
        assert raw["custom"]["schedule_congested_home"] is True
        assert raw["custom"]["b2b_home"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest "E:\Github\Prediction Market Reality Filter\backend\tests\test_adapter_shared.py::TestScheduleDensityEnrich" -q --tb=short
```

Expected: FAIL — missing `_fixture_history_for_density` and/or still rest-only congest.

- [ ] **Step 3: Implement helper + density block in `_shared.py`**

Add near other private helpers in `backend/app/sports/football/adapters/_shared.py` (module level):

```python
def _fixture_history_for_density(
    competition: str | None,
) -> list[dict] | None:
    """Load kickoff+teams from kernel fixtures for density counts. None on failure."""
    try:
        from app.kernel.kernel_db import KernelMatchFixture, get_kernel_session

        session = get_kernel_session()
        try:
            q = session.query(KernelMatchFixture)
            if competition:
                q = q.filter(KernelMatchFixture.competition == competition)
            rows = q.all()
            out: list[dict] = []
            for f in rows:
                out.append(
                    {
                        "match_id": f.match_id,
                        "home_team": f.home_team or "",
                        "away_team": f.away_team or "",
                        "kickoff_utc": f.kickoff_utc,
                    }
                )
            return out
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        logger.debug("fixture history for density failed", exc_info=True)
        return None
```

Replace the existing P1-F2 block (rest-only flags) with:

```python
    # P1-F2: schedule density — window counts + congest flags
    try:
        custom = raw.setdefault("custom", {})
        rh = raw.get("general", {}).get("rest_days_home")
        ra = raw.get("general", {}).get("rest_days_away")
        if rh is not None:
            custom["b2b_home"] = float(rh) <= 1.0
        if ra is not None:
            custom["b2b_away"] = float(ra) <= 1.0

        history = _fixture_history_for_density(competition)
        from app.sports._shared.rest_form import matches_in_window_as_of

        if history is not None:
            mh = matches_in_window_as_of(
                home_name,
                before,
                history,
                window_days=7,
                exclude_match_id=match.match_id,
            )
            ma = matches_in_window_as_of(
                away_name,
                before,
                history,
                window_days=7,
                exclude_match_id=match.match_id,
            )
            if mh is not None:
                custom["matches_last_7d_home"] = int(mh)
                custom["schedule_congested_home"] = mh >= 2
            elif rh is not None:
                custom["schedule_congested_home"] = float(rh) <= 2.0
            if ma is not None:
                custom["matches_last_7d_away"] = int(ma)
                custom["schedule_congested_away"] = ma >= 2
            elif ra is not None:
                custom["schedule_congested_away"] = float(ra) <= 2.0
        else:
            if rh is not None:
                custom["schedule_congested_home"] = float(rh) <= 2.0
            if ra is not None:
                custom["schedule_congested_away"] = float(ra) <= 2.0
    except Exception:  # noqa: BLE001
        logger.debug("schedule density flags skipped", exc_info=True)
```

Note: `competition`, `before`, `home_name`, `away_name`, `match` already exist in `enrich_situational_features`.

- [ ] **Step 4: Run adapter density tests + existing shared tests**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_adapter_shared.py::TestScheduleDensityEnrich" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_adapter_shared.py::TestFetchEloAndOdds" `
  -q --tb=short
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/sports/football/adapters/_shared.py backend/tests/test_adapter_shared.py
git commit -m "feat(football): inject 7d match density into schedule_congested (P1-F2)"
```

---

### Task 4: Engine regression + docs

**Files:**
- Modify: `backend/tests/test_football_multi_factor_engine.py`
- Modify: `CHANGELOG.md`
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`

**Interfaces:**
- Consumes: completed Tasks 1–3; engine already honors `custom.schedule_congested_*`
- Produces: proof that long rest + custom congest still soft-penalizes; docs updated

- [ ] **Step 1: Add engine test for custom congest with long rest**

Append to `TestFootballMultiFactorEngine` class area (or new methods on existing test class that has `test_rest_congestion_penalty`):

```python
    def test_custom_schedule_congested_with_long_rest(self):
        """True density flag should penalize even when rest_days are equal and long."""
        engine = FootballMultiFactorEngine()
        base = _make_features(rest_home=5, rest_away=5, custom={})
        congest = _make_features(
            rest_home=5,
            rest_away=5,
            custom={"schedule_congested_home": True, "schedule_congested_away": False},
        )
        r0 = engine.predict(base, base.match)
        r1 = engine.predict(congest, congest.match)
        assert (
            r1.outcome_probabilities["home_win"]
            < r0.outcome_probabilities["home_win"]
        )
```

- [ ] **Step 2: Run related suite**

```powershell
$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_rest_form.py" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_adapter_shared.py::TestScheduleDensityEnrich" `
  "E:\Github\Prediction Market Reality Filter\backend\tests\test_football_multi_factor_engine.py" `
  -q --tb=short
```

Expected: PASS.

- [ ] **Step 3: Update CHANGELOG**

At top of `## Unreleased` in `CHANGELOG.md`:

```markdown
### Football schedule density window counts (P1-F2)
- `matches_in_window_as_of`: prior fixtures in 7-day window (includes unfinished)
- Football enrich injects `matches_last_7d_*`; `schedule_congested_*` from count≥2 when known
- Rest ≤ 2 remains fallback only when count unknown; b2b still rest ≤ 1; MultiFactor weights unchanged
```

- [ ] **Step 4: Update backlog P1-F2 row**

Replace P1-F2 line with:

```markdown
| P1-F2 | rest / 赛程密度 | ✅ 部分 2026-07-25：`matches_last_7d_*` + congest 由 7 日场次≥2 驱动（rest≤2 仅 fallback）；b2b 仍 rest≤1 | 跨联赛合并赛程 / 更细窗口仍待 |
```

Keep table column structure consistent with neighboring rows (if the row is three-column in that section, match that shape).

- [ ] **Step 5: Commit**

```powershell
git add backend/tests/test_football_multi_factor_engine.py CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(football): P1-F2 schedule density changelog + engine test"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| `matches_in_window_as_of` pure helper | Task 1–2 |
| Unfinished fixtures count | Task 1–2 |
| Exclude self / future / empty → None | Task 1–2 |
| Inject `matches_last_7d_*` | Task 3 |
| congest from count ≥ 2 | Task 3 |
| count known &lt; 2 overrides rest ≤ 2 | Task 3 |
| count None → rest ≤ 2 fallback | Task 3 |
| b2b rest-only | Task 3 |
| Engine weights/coefficients unchanged | Task 4 (no engine file edit) |
| CHANGELOG + backlog | Task 4 |

## Placeholder / consistency self-review

- No TBD steps; full test and production code inlined.
- API names consistent: `matches_in_window_as_of`, `_fixture_history_for_density`, `matches_last_7d_*`, `schedule_congested_*`.
- Window 7 / threshold 2 fixed in helper default and enrich call.
- Patch path for history is `_shared._fixture_history_for_density` so tests do not need a live kernel DB.
