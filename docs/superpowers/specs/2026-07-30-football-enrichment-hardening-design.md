# Football Enrichment Contract Hardening - Design

**Date:** 2026-07-30
**Status:** Approved for planning
**Review baseline:** `monkeydjl/main...527a06a`

## Problem

The P1-F7/P1-F8 football enrichment work added club geo/altitude, static
referee bias, and static climate weather. The intended behavior is soft,
fill-only enrichment: explicit fixture data wins, unknown data stays unknown,
and static priors are used only when no stronger value exists.

Review found that the current implementation violates that contract in three
runtime paths and leaves the repository verification/documentation contract
incomplete:

1. Football clubs reuse the generic `_lookup` last-token heuristic. Unknown
   names can therefore resolve to unrelated clubs, for example `Leeds United`
   to Manchester United and `Unknown City` to Leicester City.
2. Altitude and weather selectors use boolean `or`, so valid numeric `0.0`
   values are treated as absent and can be replaced by static data.
3. A whitespace-only referee name creates `custom.referee_name = ""`.
4. The documented `unittest discover` command does not execute the newly added
   pytest-style tests.
5. CHANGELOG/backlog wording does not distinguish delivered static climate
   fill from the still-pending live weather source.

These problems can silently create false travel/environment features or allow
the same regressions to escape the repository's documented verification path.

## Goals

1. Make football club lookup fail closed for unknown names while preserving
   exact normalized names and deliberately listed aliases.
2. Preserve explicit zero altitude and zero temperature as valid source data.
3. Treat empty or whitespace-only referee names as absent.
4. Establish pytest as the documented backend test entry point used for these
   tests, including the required dev dependency installation step.
5. Synchronize CHANGELOG/backlog wording with the implemented static climate
   capability and remove the review's seven Markdown whitespace findings.
6. Deliver the work as three serial, independently reviewable tasks.

## Non-goals

- Adding or changing football prediction factors, formulas, weights, or flags
- Expanding the club, altitude, referee, or climate static datasets
- Adding live geo, referee, or weather APIs
- Refactoring all sport-name lookup behavior
- Introducing a new generic value-selection framework
- Converting the whole test suite to a different test framework
- Cleaning unrelated warnings, tests, files, or untracked artifacts

## Approved Approach

Use one hardening design implemented through three serial tasks. Each task must
complete a RED-GREEN test cycle, receive review, and pass its own acceptance
criteria before the next task starts.

```text
Task 1: football geo lookup correctness
    -> Task 2: fill-only and referee input contracts
        -> Task 3: pytest/documentation contract
            -> complete backend regression and final review
```

This ordering fixes potentially fabricated model inputs first, then closes the
remaining runtime input edge cases, and only then changes the project-wide
verification/documentation contract.

## Task 1 Boundary: Football Geo Lookup Correctness

### Required behavior

- Football lookup must continue to resolve:
  - exact table keys;
  - case/whitespace-normalized equivalents;
  - aliases explicitly present in `_FOOTBALL_CLUBS`;
  - national teams through the existing football national fallback.
- Football club lookup must not use the generic last-token heuristic.
- Unknown football club names must return `None`.
- `travel_between_teams` must return `travel_known=False` if either football
  side is unknown.
- NBA, NHL, MLB, and generic non-football lookup behavior must remain unchanged.

### Design

Keep `_lookup` unchanged for existing consumers. Add a football-specific strict
lookup path in `team_geo.py` that performs only exact normalized key matching.
`resolve_city` uses the strict path for `_FOOTBALL_CLUBS`, then the appropriate
football national fallback. Do not infer aliases at runtime; aliases must be
explicit table entries so false positives are auditable.

### Required regression cases

- `Leeds United` does not resolve to Manchester United.
- `Unknown City` does not resolve to Leicester City.
- `travel_between_teams("Arsenal", "Leeds United", "epl")` is unknown.
- Existing Arsenal, Man City, Real Madrid CF, Bayern, and Brazil cases remain
  green.

## Task 2 Boundary: Fill-only And Referee Input Contracts

### Required behavior

- Altitude source priority remains:
  `custom.venue_altitude_m`, `custom.altitude_m`, `environment.altitude_m`,
  `environment.venue_altitude_m`, then static lookup.
- Any present non-`None` value, including `0`, `0.0`, or `"0"`, wins over the
  static altitude lookup and is normalized to float.
- Weather source priority remains the existing environment/custom order.
- Any present non-`None` temperature, including `0`, `0.0`, or `"0"`, counts
  as fixture data. Static climate may run only when both usable temperature and
  condition are absent.
- Existing partial pass-through behavior remains: either supplied temperature
  or supplied condition prevents static climate from filling the other field.
- Referee names are stripped before the presence decision. Empty/whitespace
  names create no referee fields and trigger no static lookup.
- Existing explicit referee rate/bias values remain untouched.

### Design

Use a small local first-non-`None` selection expression or helper only where it
removes duplication inside `_shared.py`. Do not add a cross-module abstraction.
Preserve current logging/fail-closed behavior. Normalize the referee name once,
then use that normalized value for both storage and lookup.

### Required regression cases

- Toluca with explicit altitude `0.0` remains `0.0` and has no
  `altitude_source=static_table`.
- Arsenal with only explicit `weather_temp_c=0.0` remains `0.0` and receives no
  `weather_source=static_climate`.
- A whitespace-only environment referee creates no `referee_name`, bias, rate,
  or source.
- Existing positive-value pass-through and static-fill tests remain green.

## Task 3 Boundary: Verification And Documentation Contract

### Required behavior

- `requirements-dev.txt` remains the source of backend test tooling.
- Root and backend README instructions must install runtime and dev
  requirements before verification and use pytest for the backend suite.
- The selected canonical command is:

  ```text
  python -m pytest tests
  ```

- Existing `compileall` verification remains documented.
- CI must run the same pytest suite, or the task must demonstrate that an
  existing CI job already does so. Do not add a duplicate job.
- CHANGELOG must record static climate fill as delivered while explicitly
  leaving live forecast weather pending.
- The P1-F7 backlog row must make the same distinction.
- Remove only the seven trailing-whitespace findings in the three reviewed
  design documents; do not reformat unrelated content.

### Required regression and evidence

- `git diff --check` has no findings for files touched by this hardening batch.
- The complete backend pytest suite exits zero with all locked dependencies
  installed.
- README commands are runnable from their documented working directories.
- CHANGELOG and backlog use consistent static-versus-live-weather terminology.

## Error Handling And Compatibility

- Unknown football names fail closed to `None`; no new exception path.
- Invalid numeric altitude/weather inputs keep the existing best-effort logging
  behavior and must not crash adapter execution.
- No persisted schema, API response, feature flag, factor registry, engine
  weight, or public Python signature changes.
- Static source tags remain exactly `static_table`, `static_climate`, and
  `static_map`.

## Test Strategy

1. Add focused failing regression tests before implementation in Tasks 1 and 2.
2. Run each exact failing test and capture the expected pre-fix failure.
3. Implement the smallest local change that makes the focused test pass.
4. Run the containing test modules after each task.
5. After Task 3, install locked runtime/dev requirements and run the complete
   backend pytest suite.
6. Run `compileall` and `git diff --check` as independent final gates.

Tests must assert returned values and source tags, not internal helper call
counts, unless a call itself is the public contract being protected.

## Task Ownership And Review Gates

- Only one implementation task is active at a time.
- Each task owns only the files listed in its implementation plan.
- The implementing AI must not modify, stage, or delete existing untracked
  pytest/SDD artifacts.
- The implementing AI provides the diff, commands run, and raw pass/fail counts.
- Architect review checks the actual diff and reruns the relevant commands; an
  implementer's success statement is not acceptance evidence.
- Review result is `PASS`, `NEEDS FIX`, or `REJECT`.
- A task marked `NEEDS FIX` returns to the same scope; it does not expand into
  adjacent cleanup.

## Acceptance Criteria

1. Unknown football club names cannot resolve through last-token coincidence.
2. Covered explicit aliases and national-team fallback still work.
3. Explicit zero altitude and temperature survive enrichment unchanged and do
   not receive static source tags.
4. Whitespace-only referee input creates no referee fields.
5. No prediction engine formula, weight, flag, dataset, or API contract changes.
6. Backend verification documentation and CI consistently execute pytest.
7. CHANGELOG/backlog accurately distinguish static climate from live weather.
8. All focused tests, full backend pytest, compileall, and diff-check gates pass.

## Deferred Work

- Live weather forecast source for football fixtures
- True referee statistics API or database
- Expansion and quality audit of static club/referee/climate datasets
- Broader normalization redesign across all sports
