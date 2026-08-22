# NBA live availability provider contract

## Purpose

`backend/app/services/nba_live_injury_service.py` optionally supplies real NBA absences in place of the code-local static Out table. It is a read-only, process-cached data source that fills the existing `injury_impact_{home,away}` fields; it does not write to the kernel database, create predictions, or change the NBA engine's formulas or weights.

Role tiers and the impact formula stay in `backend/app/sports/basketball/nba_injury.py`. This service only fetches, validates, and normalizes rows before handing them to that shared summarizer, so live and static values are produced by the same arithmetic.

## Configuration

Set every required value in `backend/.env`. If the feature is disabled or either URL/key is blank, it makes no outbound request:

```dotenv
NBA_LIVE_INJURIES_ENABLED=true
NBA_LIVE_INJURIES_URL=https://provider.example/nba/availability
NBA_LIVE_INJURIES_API_KEY=...
```

Requests use `Authorization: Bearer <key>` and are bounded by `NBA_LIVE_INJURIES_TIMEOUT_S` and `NBA_LIVE_INJURIES_MAX_BYTES`. Snapshots are cached per URL for `NBA_LIVE_INJURIES_CACHE_TTL_HOURS`, so changing the endpoint never serves a stale snapshot. Credentials and raw responses are never logged, returned by diagnostics, or exposed via an API. Only `http` and `https` URLs are accepted.

## Required response envelope

```json
{
  "teams": [
    {
      "team": "Boston Celtics",
      "absences": [
        {"player": "Example Star", "status": "out", "role": "star"},
        {"player": "Example Starter", "status": "out", "role": "starter"}
      ]
    },
    {"team": "Miami Heat", "absences": []}
  ]
}
```

Rules:

- The payload must be a JSON object with a `teams` list and no `errors` value.
- Every entry must be an object with a non-empty `team`. Team names are matched case- and punctuation-insensitively, and every normalized name must be unique — duplicate blocks make the snapshot ambiguous and reject it.
- `absences` may be omitted (treated as empty) but must be a list when present.
- Only `out`, `inactive`, and `suspended` count as absent. `questionable`, `probable`, and `day-to-day` describe a player expected to feature and are ignored, because counting them would overstate the absence a role weight represents.
- `role` is one of `star`, `starter`, `rotation`, or `bench`. An unrecognized tier is dropped so the shared summarizer applies its own documented bench default rather than this service inventing a weight.
- Malformed JSON, a non-UTF-8 body, an oversized response, a transport error, a timeout, or an unreadable configuration value invalidates the entire snapshot.
- A valid snapshot that omits the requested team, or reports it with no qualifying absence, is *available data with no reportable impact* — not a healthy `0.0` assertion.

## Impact and fallback behavior

Per-player weights and the cap are unchanged: `star 0.35`, `starter 0.18`, `rotation 0.08`, `bench 0.03`, summed and clamped to `[0, 1]`.

`backend/app/sports/basketball/nba_adapter.py` resolves each side in this order:

1. a reached provider reporting an impact for that team → `injury_source_{side}=live_provider`;
2. otherwise the static Out table → `injury_source_{side}=static_table`.

A disabled or unconfigured provider, a transport failure, a rejected snapshot, an exception inside the service, or a reached provider that is silent on that team all degrade to the static table. When neither source has a value, no injury key is written at all — nothing is invented.

## Operational readiness

Keep the feature disabled until a licensed source can provide the complete contract for the intended teams and seasons. The source must publish actual roster availability; ratings, minutes projections, or invented proxies are not valid substitutes. Enabling this provider does not enable learning, scheduling, market writes, or prediction writes.
