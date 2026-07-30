# Football Enrichment Contract Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Remove false football geo matches, preserve explicit zero-valued enrichment inputs, reject empty referee names, and align tests/documentation with the repository's existing pytest CI gate.

**Architecture:** Keep static data tables, adapter boundaries, and prediction engine code unchanged. Add a strict normalized lookup only for football city tables, use local non-None source selection in the adapter, and make three small serial commits. The existing .github/workflows/ci.yml already runs pytest tests/, so no CI job is added.

**Tech Stack:** Python 3.14-compatible backend, pytest, existing football adapter and shared geo modules, Markdown documentation, Git.

## Global Constraints

- Do not change prediction formulas, factor weights, feature flags, schemas, public API responses, or static datasets.
- Do not change generic _lookup behavior for NBA, NHL, MLB, or non-football callers.
- Do not add runtime network calls, configuration keys, or a new cross-module value-selection abstraction.
- Preserve source tags exactly: static_table, static_climate, and static_map.
- Work only in the files listed by the active task.
- Do not modify, stage, delete, or clean existing untracked pytest/SDD artifacts.
- Use E:\Github\Prediction Market Reality Filter\backend as the working directory for backend commands and set PYTHONPATH=.
- Each task must provide the actual diff and raw test output before it can be reviewed.

---

### Task 1: Make Football City Lookup Strict

**Files:**
- Modify: backend/app/sports/_shared/team_geo.py:326-416
- Test: backend/tests/test_team_geo.py

**Interfaces:**
- Consumes: _FOOTBALL_CLUBS, _FOOTBALL_NATIONAL, _normalize, resolve_city, and travel_between_teams.
- Produces: private _lookup_exact(name, table) helper used only by the football branch of resolve_city; public return shapes remain unchanged.

**Implementation prompt for another AI:**

~~~text
You are implementing Task 1 of the approved football enrichment hardening plan.

Ownership is limited to:
- backend/app/sports/_shared/team_geo.py
- backend/tests/test_team_geo.py

The generic _lookup() uses a last-token heuristic. In the football branch this
causes false positives: resolve_city("Leeds United", "epl") returns Manchester
United coordinates, and resolve_city("Unknown City", "epl") returns Leicester
City coordinates.

Leave _lookup() unchanged for NBA/NHL/MLB. Add a private football-only exact
normalized lookup that compares normalized complete keys only. It must not use
substring or last-token matching. In the football branch of resolve_city, use
the strict lookup for _FOOTBALL_CLUBS, then the existing national fallback.
Keep explicit aliases, case/whitespace normalization, travel_between_teams,
and all non-football branches unchanged.

Add failing tests for:
- resolve_city("Leeds United", "epl") is None
- resolve_city("Unknown City", "epl") is None
- travel_between_teams("Arsenal", "Leeds United", "epl")["travel_known"] is False

Keep existing Arsenal, Man City alias, Real Madrid CF, Bayern, Brazil, and
Arsenal-v-Real-Madrid tests green.

From backend with PYTHONPATH=. run:
python -m pytest tests/test_team_geo.py -q -k "unknown_club or one_unknown_football_side"
python -m pytest tests/test_team_geo.py -q

The first command must fail before implementation and pass after it. Report
RED output, final output, and the exact diff. Modify and commit only the two
owned files:
git add app/sports/_shared/team_geo.py tests/test_team_geo.py
git commit -m "fix(football): make club geo lookup fail closed"
~~~

- [ ] Add the three negative regression tests to test_team_geo.py.
- [ ] Run the focused tests and verify RED.
- [ ] Add a helper beside _lookup:

~~~python
def _lookup_exact(
    name: str,
    table: dict[str, tuple[float, float, int]],
) -> tuple[float, float, int] | None:
    key = _normalize(name)
    if not key:
        return None
    for table_key, value in table.items():
        if _normalize(table_key) == key:
            return value
    return None
~~~

- [ ] Change only the football branch to call _lookup_exact for clubs and then nationals.
- [ ] Run python -m pytest tests/test_team_geo.py -q and git diff --check on the two owned files.
- [ ] Commit only the two owned files.

**Task 1 acceptance:** Unknown football names return None; explicit aliases and Brazil still resolve; one unknown side produces travel_known=False; non-football tests and _lookup are unchanged.

---

### Task 2: Preserve Fill-only Values And Reject Empty Referees

**Files:**
- Modify: backend/app/sports/football/adapters/_shared.py:222-325
- Test: backend/tests/test_adapter_shared.py, in the existing referee/altitude/weather classes

**Interfaces:**
- Consumes: enrich_referee_features, enrich_altitude_features, enrich_weather_features, MatchIdentity, and static lookup functions.
- Produces: the same in-place mutation contracts and source tags; no public signature changes.

**Implementation prompt for another AI:**

~~~text
You are implementing Task 2 of the approved football enrichment hardening plan.

Ownership is limited to:
- backend/app/sports/football/adapters/_shared.py
- backend/tests/test_adapter_shared.py

Current defects:
1. Boolean or selection treats custom venue_altitude_m=0.0 as missing.
2. Boolean or selection treats environment weather_temp_c=0.0 as missing when
   condition is absent.
3. Whitespace-only environment.referee creates custom.referee_name="".

Preserve altitude priority exactly:
custom.venue_altitude_m, custom.altitude_m, env.altitude_m,
env.venue_altitude_m, then static lookup. A value is present when it is not
None, including 0, 0.0, and "0". Preserve float normalization and exception
handling.

Preserve weather temperature when it is not None, including zero. Keep current
partial pass-through: either temperature or condition prevents static climate.
Strip referee names before the presence decision. Whitespace-only input creates
no referee_name, bias, rate, or source. Existing explicit rate/bias is untouched.
Do not change source tags, static tables, engine code, or public signatures.

Add failing tests for zero altitude, zero weather temperature, and whitespace
referee input. Run from backend with PYTHONPATH=.:
python -m pytest tests/test_adapter_shared.py -q -k "zero_altitude or zero_weather or whitespace_referee"
python -m pytest tests/test_adapter_shared.py -q -k "StaticAltitudeFill or StaticWeatherFill or StaticReferee"

The first command must fail before implementation and pass after it. Report
RED output, final output, and the exact diff. Modify and commit only the two
owned files:
git add app/sports/football/adapters/_shared.py tests/test_adapter_shared.py
git commit -m "fix(football): preserve enrichment zero values"
~~~

- [ ] Add this concrete test helper beside the existing _make_match helper, then add explicit zero-value and whitespace referee tests:

~~~python
def _make_match_for_home(name: str) -> MatchIdentity:
    base = _make_match()
    return MatchIdentity(
        match_id=base.match_id,
        season=base.season,
        stage=base.stage,
        round=base.round,
        home=TeamIdentity(code="HOME", name=name, competition=_UCL),
        away=base.away,
        kickoff_utc=base.kickoff_utc,
    )


def test_zero_altitude_is_not_overwritten(self):
    raw = {"environment": {}, "custom": {"venue_altitude_m": 0.0}}
    enrich_altitude_features(raw, _make_match_for_home("Toluca"))
    assert raw["custom"]["venue_altitude_m"] == pytest.approx(0.0)
    assert raw["custom"].get("altitude_source") != "static_table"


def test_zero_weather_temp_is_not_overwritten(self):
    raw = {"environment": {"weather_temp_c": 0.0}, "custom": {}}
    enrich_weather_features(raw, _make_match_for_home("Arsenal"))
    assert raw["environment"]["weather_temp_c"] == pytest.approx(0.0)
    assert raw["custom"].get("weather_source") != "static_climate"


def test_whitespace_referee_creates_no_fields(self):
    raw = {"environment": {"referee": "   "}, "custom": {}}
    enrich_referee_features(raw, _make_match())
    assert "referee_name" not in raw["custom"]
    assert "referee_home_bias" not in raw["custom"]
    assert "referee_source" not in raw["custom"]
~~~

- [ ] Run the focused tests and verify RED.
- [ ] Implement local non-None selection. This helper is permitted inside _shared.py only:

~~~python
def _first_not_none(*values: Any) -> Any | None:
    for value in values:
        if value is not None:
            return value
    return None
~~~

- [ ] Use it for altitude and temperature, keep condition selection truthy, normalize referee names before checking presence, and remove an empty generated referee_name if needed.
- [ ] Run the focused adapter regression set and git diff --check.
- [ ] Commit only the two owned files.

**Task 2 acceptance:** Explicit zero values survive unchanged, static source tags are absent for those values, whitespace-only referee input produces no fields, existing positive pass-through/static-fill tests pass, and no public contract changes appear in the diff.

---

### Task 3: Align Verification And Documentation

**Files:**
- Modify: README.md:128-136
- Modify: backend/README.md:7-12,43-48
- Modify: CHANGELOG.md:11-15
- Modify: docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md:178
- Modify: docs/superpowers/specs/2026-07-26-football-club-geo-altitude-design.md:3-4
- Modify: docs/superpowers/specs/2026-07-26-football-static-referee-design.md:3-4,99
- Modify: docs/superpowers/specs/2026-07-26-football-static-weather-design.md:3-4

**Interfaces:**
- Consumes: backend/requirements.txt, backend/requirements-dev.txt, and the existing .github/workflows/ci.yml backend job.
- Produces: one canonical documented backend verification command, accurate weather wording, and clean diff-check output. No CI behavior change is expected because CI already runs pytest tests/.

**Implementation prompt for another AI:**

~~~text
You are implementing Task 3 of the approved football enrichment hardening plan.

Ownership is limited to:
- README.md
- backend/README.md
- CHANGELOG.md
- docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md
- docs/superpowers/specs/2026-07-26-football-club-geo-altitude-design.md
- docs/superpowers/specs/2026-07-26-football-static-referee-design.md
- docs/superpowers/specs/2026-07-26-football-static-weather-design.md

Facts:
- backend/requirements-dev.txt already declares pytest.
- .github/workflows/ci.yml already installs pytest and runs pytest tests/.
  Do not modify or duplicate that CI job.
- README files document unittest discover, which does not execute the new
  pytest-style tests.
- CHANGELOG/backlog do not distinguish delivered static climate from pending
  live forecast weather.
- git diff --check reports seven trailing-whitespace lines in the three
  reviewed design documents.

Required changes:
1. In root and backend README verification instructions, document installation
   of requirements.txt and requirements-dev.txt before tests.
2. Make python -m pytest tests the canonical backend test command and retain
   python -m compileall app tests.
3. Update CHANGELOG and the P1-F7 backlog row: static climate is delivered;
   live forecast weather remains pending.
4. Remove only the seven reported trailing-whitespace lines. Do not reformat.
5. Do not change CI, runtime code, test files, or unrelated docs.

Run from backend:
python -m compileall app tests
python -m pytest tests

Run from repository root:
git diff --check -- README.md backend/README.md CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md docs/superpowers/specs/2026-07-26-football-club-geo-altitude-design.md docs/superpowers/specs/2026-07-26-football-static-referee-design.md docs/superpowers/specs/2026-07-26-football-static-weather-design.md
git diff --name-only

Report the exact diff and raw command results. Stage only the owned files:
git add README.md backend/README.md CHANGELOG.md docs/dev/OPPORTUNITY_BACKLOG_2026-07-17.md docs/superpowers/specs/2026-07-26-football-club-geo-altitude-design.md docs/superpowers/specs/2026-07-26-football-static-referee-design.md docs/superpowers/specs/2026-07-26-football-static-weather-design.md
git commit -m "docs: align football enrichment verification"
~~~

- [ ] Confirm the existing CI pytest command before editing; do not add a CI file.
- [ ] Update both README verification sections to install runtime and dev requirements, compile, and run pytest tests.
- [ ] Add a CHANGELOG static climate subsection and correct the P1-F7 backlog wording.
- [ ] Remove only the seven targeted trailing spaces from the three reviewed design documents.
- [ ] Run compileall, full pytest, targeted diff-check, and owned-file name check.
- [ ] Commit only the seven owned documentation files.

**Task 3 acceptance:** README and CI use the same pytest command, static-vs-live weather wording is consistent, the seven whitespace findings are gone, the full backend pytest suite passes, and no CI/runtime/test files changed.

---

## Final Architect Acceptance

After all three task commits exist, run from backend:

~~~text
python -m compileall app tests
python -m pytest tests
~~~

Then run from repository root:

~~~text
git diff --check monkeydjl/main...HEAD
git diff --name-only monkeydjl/main...HEAD
~~~

The final diff must contain only the three task scopes plus this plan/spec documentation. Independently verify:

1. resolve_city("Leeds United", "epl") is None and resolve_city("Unknown City", "epl") is None.
2. Explicit 0.0 altitude and temperature remain unchanged without static tags.
3. Whitespace-only referee input creates no fields.
4. Existing football aliases, national fallback, and non-football travel tests remain green.
5. No engine, feature flag, API, schema, or static data changes were introduced.

Final result is PASS only when all commands exit zero and every acceptance
criterion is backed by the actual diff and test output. Otherwise report
NEEDS FIX with file/line evidence and return the failing task to its owner.
