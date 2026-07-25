# Football Club H2H from Kernel (P1-F4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When international historical H2H is empty, load club pairwise meetings from kernel fixtures+results and write `h2h_home_win_rate` / `h2h_draw_rate` so MultiFactor soft `h2h` can become available for league matches without changing engine math.

**Architecture:** Add `h2h_from_kernel` next to club form in `club_form.py` (reuse `_normalize` + kernel session patterns). `enrich_situational_features` keeps `get_historical_h2h` first; only on None/falsy calls kernel and writes the same rate fields. FeatureBuilder and MultiFactor stay untouched.

**Tech Stack:** Python 3.12+, pytest, SQLAlchemy kernel session. No new dependencies. No network.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-football-club-h2h-design.md`
- Priority: **historical CSV first**; kernel only when historical returns None/falsy; **never overwrite** historical
- Perspective: **current match home** wins/draws (align with `get_historical_h2h`); venue of historical fixture does not redefine “home”
- Return shape (kernel): `matches_played`, `home_wins`, `draws`, `away_wins`, `data_source="kernel_match_results"`
- Default `max_matches=20` (match historical H2H)
- Optional `competition` filter when provided (same as `team_form_from_kernel`)
- As-of: only meetings with kickoff/finished **strictly before** `before`
- Empty / same team / no scores → `None`; omit h2h keys (fail-closed)
- Do **not** change MultiFactor h2h formula/weight or FeatureBuilder
- Do **not** merge CSV + kernel sources
- Do **not** push to origin unless user explicitly asks
- TDD: RED → GREEN → COMMIT per task
- Python runner: `C:\Python314\python.exe` with `$env:PYTHONPATH="E:\Github\Prediction Market Reality Filter\backend"`

## File Structure

### Modified files
1. `backend/app/sports/football/club_form.py` — add `h2h_from_kernel`
2. `backend/tests/test_club_form.py` — unit tests for `h2h_from_kernel` (extend seed as needed)
3. `backend/app/sports/football/adapters/_shared.py` — H2H block: historical then kernel fallback + shared write
4. `backend/tests/test_adapter_shared.py` — enrich tests: kernel fallback + historical wins
5. `CHANGELOG.md` — Unreleased P1-F4 note
6. `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md` — P1-F4 status line

### Unchanged (verify only)
1. `backend/app/sports/football/feature_builder.py` — h2h passthrough
2. `backend/app/sports/football/engines/football_multi_factor_engine.py` — h2h soft path
3. `backend/app/services/world_cup_historical_results.py` — CSV H2H unchanged

---

### Task 1: `h2h_from_kernel` unit tests (RED)

**Files:**
- Modify: `backend/tests/test_club_form.py`
- (No production implementation yet)

**Interfaces:**
- Consumes: (not yet) `app.sports.football.club_form.h2h_from_kernel`
- Produces: failing tests defining the query API for Task 2

- [ ] **Step 1: Extend seed data and write failing tests**

Update import line to:

```python
from app.sports.football.club_form import (
    h2h_from_kernel,
    points_form_rate,
    team_form_from_kernel,
)
```

Keep existing `_seed_matches` (Arsenal vs Chelsea W, Liverpool vs Arsenal D). Add a second seeder for H2H-specific cases (or extend `_seed_matches` carefully without breaking form tests).

Append:

```python
def _seed_h2h_matches(tmp_path):
    """Arsenal vs Chelsea twice: Arsenal home win; Chelsea home (Arsenal away) draw."""
    close_kernel_session()
    init_kernel_db(str(tmp_path / "kernel_h2h.db"))
    session = get_kernel_session()
    try:
        fixtures = [
            KernelMatchFixture(
                match_id="h2h-1",
                competition="epl",
                season="2025",
                stage="regular",
                home_team="Arsenal",
                away_team="Chelsea",
                kickoff_utc=datetime(2025, 8, 20, tzinfo=timezone.utc),
            ),
            KernelMatchFixture(
                match_id="h2h-2",
                competition="epl",
                season="2025",
                stage="regular",
                home_team="Chelsea",
                away_team="Arsenal",
                kickoff_utc=datetime(2025, 9, 5, tzinfo=timezone.utc),
            ),
            KernelMatchFixture(
                match_id="h2h-other",
                competition="epl",
                season="2025",
                stage="regular",
                home_team="Arsenal",
                away_team="Liverpool",
                kickoff_utc=datetime(2025, 9, 12, tzinfo=timezone.utc),
            ),
            KernelMatchFixture(
                match_id="h2h-future",
                competition="epl",
                season="2025",
                stage="regular",
                home_team="Arsenal",
                away_team="Chelsea",
                kickoff_utc=datetime(2025, 12, 1, tzinfo=timezone.utc),
            ),
            KernelMatchFixture(
                match_id="h2h-ucl",
                competition="ucl",
                season="2025",
                stage="group",
                home_team="Arsenal",
                away_team="Chelsea",
                kickoff_utc=datetime(2025, 9, 1, tzinfo=timezone.utc),
            ),
        ]
        results = [
            KernelMatchResult(
                match_id="h2h-1",
                home_score=2,
                away_score=0,
                finished_at=datetime(2025, 8, 20, 22, tzinfo=timezone.utc),
            ),
            KernelMatchResult(
                match_id="h2h-2",
                home_score=1,
                away_score=1,
                finished_at=datetime(2025, 9, 5, 22, tzinfo=timezone.utc),
            ),
            KernelMatchResult(
                match_id="h2h-other",
                home_score=3,
                away_score=1,
                finished_at=datetime(2025, 9, 12, 22, tzinfo=timezone.utc),
            ),
            # h2h-future: no result yet
            KernelMatchResult(
                match_id="h2h-ucl",
                home_score=1,
                away_score=0,
                finished_at=datetime(2025, 9, 1, 22, tzinfo=timezone.utc),
            ),
        ]
        for row in fixtures + results:
            session.add(row)
        session.commit()
    finally:
        session.close()


class TestH2hFromKernel:
    def test_current_home_perspective_two_meetings(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            before = datetime(2025, 10, 1, tzinfo=timezone.utc)
            h2h = h2h_from_kernel(
                "Arsenal", "Chelsea", competition="epl", before=before,
            )
            assert h2h is not None
            # h2h-1: Arsenal (current home) won; h2h-2: draw at Chelsea
            # future excluded; Liverpool match excluded; ucl filtered out by competition
            assert h2h["matches_played"] == 2
            assert h2h["home_wins"] == 1
            assert h2h["draws"] == 1
            assert h2h["away_wins"] == 0
            assert h2h["data_source"] == "kernel_match_results"
        finally:
            close_kernel_session()

    def test_venue_swap_still_current_home(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            before = datetime(2025, 10, 1, tzinfo=timezone.utc)
            # Swap current home/away: Chelsea is current home
            h2h = h2h_from_kernel(
                "Chelsea", "Arsenal", competition="epl", before=before,
            )
            assert h2h is not None
            assert h2h["matches_played"] == 2
            # From Chelsea perspective: loss at Arsenal, draw at home
            assert h2h["home_wins"] == 0
            assert h2h["draws"] == 1
            assert h2h["away_wins"] == 1
        finally:
            close_kernel_session()

    def test_unknown_pair_returns_none(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            h2h = h2h_from_kernel(
                "Arsenal", "NotATeam", competition="epl",
                before=datetime(2025, 10, 1, tzinfo=timezone.utc),
            )
            assert h2h is None
        finally:
            close_kernel_session()

    def test_same_team_returns_none(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            assert h2h_from_kernel("Arsenal", "Arsenal", competition="epl") is None
        finally:
            close_kernel_session()

    def test_before_excludes_future(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            # Only h2h-1 finished before Aug 25
            h2h = h2h_from_kernel(
                "Arsenal", "Chelsea", competition="epl",
                before=datetime(2025, 8, 25, tzinfo=timezone.utc),
            )
            assert h2h is not None
            assert h2h["matches_played"] == 1
            assert h2h["home_wins"] == 1
        finally:
            close_kernel_session()

    def test_competition_filter(self, tmp_path):
        _seed_h2h_matches(tmp_path)
        try:
            h2h = h2h_from_kernel(
                "Arsenal", "Chelsea", competition="ucl",
                before=datetime(2025, 10, 1, tzinfo=timezone.utc),
            )
            assert h2h is not None
            assert h2h["matches_played"] == 1
            assert h2h["home_wins"] == 1
        finally:
            close_kernel_session()
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_club_form.py::TestH2hFromKernel -v
```

Expected: FAIL with `ImportError` / `AttributeError: ... h2h_from_kernel`.

- [ ] **Step 3: Commit failing tests**

```powershell
git add backend/tests/test_club_form.py
git commit -m "test(football): failing P1-F4 h2h_from_kernel unit tests"
```

---

### Task 2: Implement `h2h_from_kernel` (GREEN)

**Files:**
- Modify: `backend/app/sports/football/club_form.py`
- Test: `backend/tests/test_club_form.py`

**Interfaces:**
- Consumes: Task 1 test contract; existing `_normalize`, kernel DB helpers
- Produces:

```python
def h2h_from_kernel(
    home_team: str,
    away_team: str,
    *,
    competition: str | None = None,
    before: datetime | None = None,
    max_matches: int = 20,
) -> dict[str, Any] | None:
    """Pairwise H2H from kernel; rates perspective = current home_team."""
```

- [ ] **Step 1: Implement helper**

Add to `club_form.py` after `team_form_from_kernel` (or before it; keep module cohesive):

```python
def h2h_from_kernel(
    home_team: str,
    away_team: str,
    *,
    competition: str | None = None,
    before: datetime | None = None,
    max_matches: int = 20,
) -> dict[str, Any] | None:
    """Pairwise H2H from kernel fixtures+results.

    Counts wins/draws/losses from the perspective of ``home_team`` (current
    match home), regardless of which side hosted historically.
    Shape compatible with get_historical_h2h enrich write path.
    """
    from app.kernel.kernel_db import (
        KernelMatchFixture,
        KernelMatchResult,
        get_kernel_session,
    )

    if not home_team or not away_team:
        return None
    home_key = _normalize(home_team)
    away_key = _normalize(away_team)
    if not home_key or not away_key or home_key == away_key:
        return None

    before = before or datetime.now(timezone.utc)
    if before.tzinfo is None:
        before = before.replace(tzinfo=timezone.utc)

    session = get_kernel_session()
    try:
        q = (
            session.query(KernelMatchFixture, KernelMatchResult)
            .join(
                KernelMatchResult,
                KernelMatchFixture.match_id == KernelMatchResult.match_id,
            )
        )
        if competition:
            q = q.filter(KernelMatchFixture.competition == competition)
        rows = q.all()

        pair = {home_key, away_key}
        meetings: list[tuple[datetime | None, int, int]] = []
        for fixture, result in rows:
            if result.home_score is None or result.away_score is None:
                continue
            kickoff = fixture.kickoff_utc or result.finished_at
            if kickoff is not None:
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                if kickoff >= before:
                    continue
            fh = _normalize(fixture.home_team or "")
            fa = _normalize(fixture.away_team or "")
            if {fh, fa} != pair:
                continue
            hs = int(result.home_score)
            aws = int(result.away_score)
            # Map to current-home perspective scores
            if fh == home_key:
                cur_home_gf, cur_home_ga = hs, aws
            else:
                cur_home_gf, cur_home_ga = aws, hs
            meetings.append((kickoff, cur_home_gf, cur_home_ga))

        if not meetings:
            return None

        meetings.sort(
            key=lambda r: r[0].isoformat() if r[0] else "",
            reverse=True,
        )
        meetings = meetings[: max(1, max_matches)]

        home_wins = draws = away_wins = 0
        for _, gf, ga in meetings:
            if gf > ga:
                home_wins += 1
            elif gf < ga:
                away_wins += 1
            else:
                draws += 1

        return {
            "matches_played": len(meetings),
            "home_wins": home_wins,
            "draws": draws,
            "away_wins": away_wins,
            "data_source": "kernel_match_results",
        }
    finally:
        session.close()
```

Do **not** change `team_form_from_kernel` or `points_form_rate` in this task.

- [ ] **Step 2: Run unit tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_club_form.py -v --tb=short
```

Expected: all PASS (form + points_form_rate + H2H).

- [ ] **Step 3: Commit**

```powershell
git add backend/app/sports/football/club_form.py backend/tests/test_club_form.py
git commit -m "feat(football): h2h_from_kernel current-home perspective (P1-F4)"
```

---

### Task 3: Wire enrich fallback + adapter tests (RED → GREEN)

**Files:**
- Modify: `backend/app/sports/football/adapters/_shared.py` (H2H block ~356–369)
- Modify: `backend/tests/test_adapter_shared.py`

**Interfaces:**
- Consumes: `h2h_from_kernel(home, away, *, competition, before, max_matches=20) -> dict | None`
- Produces: `raw["team"]["h2h_home_win_rate"]` / `h2h_draw_rate` from historical **or** kernel

- [ ] **Step 1: Add adapter tests first**

In `backend/tests/test_adapter_shared.py`, add a new class (near other enrich tests). Reuse `_make_match` helper already in file.

```python
class TestH2hKernelFallback:
    def test_kernel_fills_when_historical_none(self):
        match = _make_match("ucl-h2h-kernel")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        kernel_h2h = {
            "matches_played": 2,
            "home_wins": 1,
            "draws": 1,
            "away_wins": 0,
            "data_source": "kernel_match_results",
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=kernel_h2h,
        ) as mock_kh:
            enrich_situational_features(raw, match)

        mock_kh.assert_called()
        assert raw["team"]["h2h_home_win_rate"] == pytest.approx(0.5)
        assert raw["team"]["h2h_draw_rate"] == pytest.approx(0.5)

    def test_historical_not_overwritten_by_kernel(self):
        match = _make_match("ucl-h2h-hist")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        hist = {
            "matches_played": 4,
            "home_wins": 2,
            "draws": 1,
            "away_wins": 1,
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=hist,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value={
                "matches_played": 2,
                "home_wins": 2,
                "draws": 0,
                "away_wins": 0,
                "data_source": "kernel_match_results",
            },
        ) as mock_kh:
            enrich_situational_features(raw, match)

        mock_kh.assert_not_called()
        assert raw["team"]["h2h_home_win_rate"] == pytest.approx(0.5)
        assert raw["team"]["h2h_draw_rate"] == pytest.approx(0.25)

    def test_both_empty_omits_h2h(self):
        match = _make_match("ucl-h2h-empty")
        raw = {
            "team": {},
            "general": {},
            "market": {},
            "player": {},
            "environment": {},
            "custom": {},
        }
        with patch(
            "app.services.world_cup_historical_results.get_historical_team_stats",
            return_value=None,
        ), patch(
            "app.services.world_cup_historical_results.get_historical_h2h",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.team_form_from_kernel",
            return_value=None,
        ), patch(
            "app.sports.football.club_form.h2h_from_kernel",
            return_value=None,
        ):
            enrich_situational_features(raw, match)

        assert "h2h_home_win_rate" not in raw["team"]
        assert "h2h_draw_rate" not in raw["team"]
```

Ensure `enrich_situational_features` is already imported (it is). Ensure `pytest` imported.

- [ ] **Step 2: Run adapter H2H tests — expect FAIL**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_adapter_shared.py::TestH2hKernelFallback -v
```

Expected: FAIL (kernel never called / keys missing) until enrich wired.

- [ ] **Step 3: Implement enrich H2H block**

Replace the H2H section in `enrich_situational_features` (`_shared.py` ~356–369) with:

```python
    h2h = None
    if get_historical_h2h is not None:
        try:
            h2h = get_historical_h2h(home_name, away_name, before_date=before)
        except Exception:  # noqa: BLE001
            h2h = None
            logger.debug("H2H enrichment failed", exc_info=True)

    if not h2h:
        try:
            from app.sports.football.club_form import h2h_from_kernel

            h2h = h2h_from_kernel(
                home_name,
                away_name,
                competition=competition if not is_world_cup else None,
                before=before,
            )
        except Exception:  # noqa: BLE001
            h2h = None
            logger.debug("Club H2H enrichment failed", exc_info=True)

    if h2h:
        played = max(int(h2h.get("matches_played") or 0), 1)
        raw["team"]["h2h_home_win_rate"] = round(
            int(h2h.get("home_wins") or 0) / played, 4,
        )
        raw["team"]["h2h_draw_rate"] = round(
            int(h2h.get("draws") or 0) / played, 4,
        )
```

Notes:
- For World Cup, pass `competition=None` on kernel fallback so a sparse WC kernel DB is not over-filtered (CSV remains primary for WC).
- Club path passes `competition` like form.
- Existing `test_enrich_form_and_h2h` still mocks historical H2H with data → kernel must not be required; if code calls kernel only when `not h2h`, that test stays green without new patches.

- [ ] **Step 4: Run focused tests**

```powershell
cd "E:\Github\Prediction Market Reality Filter\backend"
$env:PYTHONPATH = "E:\Github\Prediction Market Reality Filter\backend"
C:\Python314\python.exe -m pytest tests/test_club_form.py tests/test_adapter_shared.py -v --tb=short
```

Expected: PASS (ignore Windows tmp cleanup PermissionError noise if assertions all green).

- [ ] **Step 5: Smoke multi-factor h2h (no engine edits)**

```powershell
C:\Python314\python.exe -m pytest tests/test_football_multi_factor_engine.py -k h2h -v --tb=short
```

Expected: h2h-related tests PASS. Do not fix unrelated pre-existing multi_factor failures in this task.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/sports/football/adapters/_shared.py backend/tests/test_adapter_shared.py
git commit -m "feat(football): kernel H2H fallback in enrich (P1-F4)"
```

---

### Task 4: Docs + backlog

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md`

**Interfaces:**
- Consumes: implemented behavior from Tasks 2–3
- Produces: documentation only

- [ ] **Step 1: CHANGELOG**

Under `## Unreleased`, add near other football entries:

```markdown
### Football club H2H from kernel (P1-F4)
- `h2h_from_kernel`: pairwise meetings, current-home perspective, as-of + competition filter
- Enrich: historical CSV first; kernel only when historical empty; same `h2h_*` rate fields
- MultiFactor h2h formula/weight unchanged
```

- [ ] **Step 2: Backlog P1-F4 row**

Replace P1-F4 status cell with equivalent of:

```markdown
| P1-F4 | h2h | ✅ 部分 2026-07-25：historical 优先 + kernel 俱乐部交锋回退（当前主队视角）；主客场分拆/别名/合并源仍待 | 小权重已在 multi-factor |
```

- [ ] **Step 3: Commit**

```powershell
git add CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
git commit -m "docs(football): P1-F4 club H2H changelog + backlog"
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| `h2h_from_kernel` helper | Task 1–2 |
| Current-home perspective | Task 1–2 tests + impl |
| as-of / competition / max_matches=20 | Task 2 |
| Historical first, no overwrite | Task 3 |
| Write same rate fields | Task 3 |
| Fail-closed empty | Task 1–3 |
| MultiFactor / FeatureBuilder unchanged | Task 3 smoke |
| CHANGELOG + backlog | Task 4 |

Placeholder scan: none.  
Type consistency: `h2h_from_kernel(...) -> dict[str, Any] | None` with keys `matches_played/home_wins/draws/away_wins/data_source` used in Tasks 1–3.
