# NBA dynamic-season efficiency provider contract

## Purpose

`backend/app/services/nba_live_ratings_service.py` optionally replaces the static 30-team ORtg/DRtg table with the current season's actual efficiency. It is a read-only, process-cached data source that fills the existing `custom.ortg_{home,away}` / `custom.drtg_{home,away}` fields; it does not write to the kernel database, create predictions, or change `BasketballEngine`'s `net_rating` formula or weight.

The static table in `backend/app/sports/basketball/nba_team_ratings.py` is a soft multi-year level and remains the fallback.

## Configuration

Set every required value in `backend/.env`. If the feature is disabled or either URL/key is blank, it makes no outbound request:

```dotenv
NBA_LIVE_RATINGS_ENABLED=true
NBA_LIVE_RATINGS_URL=https://provider.example/nba/efficiency
NBA_LIVE_RATINGS_API_KEY=...
NBA_LIVE_RATINGS_SEASON_PARAM=season
```

The season start year is appended as the configured query parameter, replacing any value already present in the URL — season key `2024-25` becomes `season=2024`. Requests use `Authorization: Bearer <key>` and are bounded by `NBA_LIVE_RATINGS_TIMEOUT_S` and `NBA_LIVE_RATINGS_MAX_BYTES`. Snapshots are cached per resolved URL for `NBA_LIVE_RATINGS_CACHE_TTL_HOURS`, so each season and any configuration change gets its own entry. Credentials and raw responses are never logged, returned by diagnostics, or exposed via an API. Only `http` and `https` URLs are accepted.

## Required response envelope

```json
{
  "teams": [
    {
      "team": "Boston Celtics",
      "possessions": 8000.0,
      "points": 9600.0,
      "points_allowed": 8720.0
    }
  ]
}
```

Rules:

- The payload must be a JSON object with a `teams` list and no `errors` value.
- Every entry must be an object with a non-empty `team`. Team names are matched case- and punctuation-insensitively, and every normalized name must be unique — duplicate blocks make the snapshot ambiguous and reject it.
- **`possessions`, `points`, and `points_allowed` are all required.** `possessions` must be finite and positive; the point totals must be finite and non-negative. Numeric strings are accepted.
- **ORtg and DRtg are computed here** as `100 × points / possessions` and `100 × points_allowed / possessions`. A payload that carries only pre-computed `ortg`/`drtg` is rejected: without a possession sample it cannot be shown to be possession-derived, which is exactly the gap this provider exists to close. Points per game, raw totals, and estimated possessions are not valid substitutes.
- A computed rating outside `[80, 140]` points per 100 rejects the whole snapshot — a value beyond that band means the payload is not in points-per-100 at all, so the entire feed's units are untrustworthy.
- Malformed JSON, a non-UTF-8 body, an oversized response, a transport error, a timeout, or an unreadable configuration value invalidates the entire snapshot.

### Sample size versus malformed data

These are treated differently on purpose:

- A **structurally broken** row (non-object, missing/duplicate team, non-numeric or non-finite field, non-positive possessions, negative points, out-of-band rating) rejects the **whole snapshot**. The contract is either honoured or it is not.
- A well-formed row with **fewer than `NBA_LIVE_RATINGS_MIN_POSSESSIONS`** possessions is real data with an unusable sample. Only **that team** is dropped, and the static table covers it. The default of 500 possessions is roughly five games.

A valid snapshot that omits the requested team, or reports it with too small a sample, is *available data with no usable rating* — not an assertion that the team is league-average.

## Fallback behavior

`backend/app/sports/basketball/nba_adapter.py` writes ratings only when **both** sides resolve, and both must come from the **same source**:

1. both sides have a live rating → all four values live, `custom.ratings_source=live_provider`;
2. otherwise both sides come from the static table → `custom.ratings_source=static_table`.

Mixing is deliberately impossible. `BasketballEngine` consumes the ORtg−DRtg differential, so pairing a live current-season level against a static multi-year level would manufacture a spurious edge from nothing but the source difference. This is why one live side is not enough, and why the provenance key is a single `ratings_source` rather than a per-side pair.

A disabled or unconfigured provider, a transport failure, a rejected snapshot, an exception inside the service, or a provider that lacks a usable rating for either side all degrade to the static table. When neither source covers both teams, no rating key is written at all and the engine's `net_rating` factor reports itself unavailable, exactly as before this provider existed.

## Operational readiness

Keep the feature disabled until a licensed source can publish actual points, points allowed, and true possession counts for the intended seasons. Enabling this provider does not enable learning, scheduling, market writes, or prediction writes.
