# MLB measured park-factor provider contract

## Purpose

`backend/app/services/mlb_live_park_service.py` optionally replaces the code-local static park table with a factor measured from actual game results. It is a read-only, process-cached data source that fills the existing `custom.park_factor` field; it does not write to the kernel database, create predictions, or change `BaseballEngine`'s `park` formula or weight.

`_PARK_FACTORS` in `backend/app/sports/baseball/mlb_adapter.py` is a frozen 30-team multi-year-ish level, and it remains the fallback. A park's run environment moves with fence changes, altitude-independent renovations, and league-wide ball changes, which a table edited by hand cannot track.

## Configuration

Set every required value in `backend/.env`. If the feature is disabled or either URL/key is blank, it makes no outbound request:

```dotenv
MLB_LIVE_PARK_ENABLED=true
MLB_LIVE_PARK_URL=https://provider.example/mlb/park-factors
MLB_LIVE_PARK_API_KEY=...
MLB_LIVE_PARK_SEASON_PARAM=season
```

The season year is appended as the configured query parameter, replacing any value already present in the URL — season key `2026` becomes `season=2026`. Requests use `Authorization: Bearer <key>` and are bounded by `MLB_LIVE_PARK_TIMEOUT_S` and `MLB_LIVE_PARK_MAX_BYTES`. Snapshots are cached per resolved URL for `MLB_LIVE_PARK_CACHE_TTL_HOURS`, so each season and any configuration change gets its own entry. Credentials and raw responses are never logged, returned by diagnostics, or exposed via an API. Only `http` and `https` URLs are accepted.

## Required response envelope

```json
{
  "parks": [
    {
      "team": "Colorado Rockies",
      "home_games": 90,
      "home_runs": 990,
      "road_games": 100,
      "road_runs": 1000
    }
  ]
}
```

`home_runs` and `road_runs` are **combined runs by both teams** in those games — the park's total run environment, not the home team's offense. The window may span more than one season; a multi-year rolling window is preferable and the game counts should say so.

Rules:

- The payload must be a JSON object with a `parks` list and no `errors` value.
- Every entry must be an object with a non-empty `team`. Team names are matched case- and punctuation-insensitively, and every normalized name must be unique — duplicate blocks make the snapshot ambiguous and reject it.
- All four counts are required and must be finite. `home_games` and `road_games` must be positive; `home_runs` must be non-negative and `road_runs` positive, because the road rate is the denominator.
- **The factor is computed here** as `(home_runs / home_games) / (road_runs / road_games)`. A payload that carries only a pre-computed `park_factor` is rejected: without the game counts behind it, it cannot be shown to be measured rather than another hand-maintained level, which is exactly the gap this provider exists to close. Dividing both sides by their own game counts also means unequal home/road windows do not skew the result.
- A computed factor outside `[0.70, 1.40]` rejects the whole snapshot. The most extreme real parks sit near 0.90 and 1.15, so a value beyond that band means the payload is not a league-average-relative ratio at all — runs per game, for instance, would land near 9.0 — and the entire feed's units are untrustworthy.
- Malformed JSON, a non-UTF-8 body, an oversized response, a transport error, a timeout, or an unreadable configuration value invalidates the entire snapshot.

### Sample size versus malformed data

These are treated differently on purpose:

- A **structurally broken** row (non-object, missing/duplicate team, non-numeric or non-finite field, non-positive game count, negative runs, zero road runs, out-of-band factor) rejects the **whole snapshot**. The contract is either honoured or it is not.
- A well-formed row where **either** game count is below `MLB_LIVE_PARK_MIN_GAMES` is real data with an unusable sample. Only **that park** is dropped, and the static table covers it. The default of 81 games is one full home schedule; a park factor measured over a partial season is dominated by which opponents visited and is too noisy to displace the static level. A licensed multi-year window clears it comfortably. The road count is checked too, because it is the baseline the home rate is divided by.

A valid snapshot that omits the requested park, or reports it with too few games, is *available data with no usable measurement* — not an assertion that the park is league-average.

## Fallback behavior

`backend/app/sports/baseball/mlb_adapter.py` writes the measured factor when one is available and keeps `_park_factor_for_team()` otherwise.

Unlike the paired team-strength providers, there is **no pair rule** here. A park factor is a property of one venue that both teams play in, so only the home team is looked up and there is no home-vs-away comparison a mixed source could distort.

`custom.park_source` records provenance for diagnostics only:

- `live_provider` — the factor was measured by the provider;
- `static_table` — the factor came from `_PARK_FACTORS` (or its 1.0 default for an unrecognized name).

A disabled or unconfigured provider, a transport failure, a rejected snapshot, an exception inside the service, or a park with too few games all degrade to the static table.

## Not covered by this provider

The P1-M2 backlog row also names home-run park factors and batter-handedness (left/right) park splits. Neither is delivered here, and neither is blocked by this provider's design:

- A **home-run park factor** would need a new `BaseballEngine` factor and weight. Engine formulas and weights are frozen for this track, and `custom` is not surfaced through the API, so an extra field would be data with no consumer.
- A **batter-handedness park split** needs the handedness of the batters actually in the lineup. The adapter has only the probable starting pitcher's `pitchHand`, which is pitcher handedness and cannot stand in for it.

## Operational readiness

Keep the feature disabled until a licensed source can publish home and road game counts with combined runs for the intended window. Enabling this provider does not enable learning, scheduling, market writes, or prediction writes.
