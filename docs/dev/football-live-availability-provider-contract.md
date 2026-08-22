# Football live availability-impact provider contract

## Purpose

`backend/app/services/football_live_availability_service.py` optionally supplies reportable football absences with player minutes and market-value shares. It is a read-only, process-cached data source that enriches the existing injury-impact fields; it does not write to the kernel database, create predictions, or change MultiFactor formulas or weights.

## Configuration

Set every required value in `backend/.env`. If the feature is disabled or either URL/key is blank, it makes no outbound request:

```dotenv
FOOTBALL_LIVE_AVAILABILITY_ENABLED=true
FOOTBALL_LIVE_AVAILABILITY_URL=https://provider.example/availability
FOOTBALL_LIVE_AVAILABILITY_API_KEY=...
FOOTBALL_LIVE_AVAILABILITY_SEASON_PARAM=season
```

Requests replace any existing `competition` and configured season query parameters, use `Authorization: Bearer <key>`, and are bounded by the configured timeout and response-size limit. Credentials and raw responses are never logged.

## Required response envelope

```json
{
  "teams": [
    {
      "team": "Arsenal",
      "absences": [
        {
          "player": "Example Player",
          "status": "out",
          "role": "starter",
          "minutes_share": 0.11,
          "market_value_share": 0.14
        }
      ]
    }
  ]
}
```

Rules:

- Every normalized team name must be unique.
- Every absence needs a non-empty, unique player name, `status: "out"`, and one of `star`, `starter`, `rotation`, or `bench` roles.
- `minutes_share` and `market_value_share` must both be finite values in `[0, 1]`.
- Provider error envelopes, malformed JSON, invalid rows, duplicate teams/players, or an oversized response invalidate the entire snapshot.
- A valid snapshot with no matching team or no absences is available data with no reportable impact, not a healthy `0.0` assertion.

## Impact and fallback behavior

The existing role contribution remains the baseline. For a live row with both valid shares, the per-player contribution is:

```text
max(role_weight, min(0.35, 2 * minutes_share + market_value_share))
```

Team contributions are summed and capped at `1.0`. Rows without both valid shares retain the historical role-only calculation, preserving API-Football, static-table, and World Cup fact-store behavior.

For club matches, enrichment order is:

1. valid contextual availability impact;
2. API-Football injury snapshot;
3. static injury table;
4. World Cup fact-store fallback.

The contextual source writes `injury_source_{home,away}=live_availability_provider`; it must have a reportable impact to take precedence. World Cup matches do not call configured club providers.

## Operational readiness

Keep the feature disabled until a licensed source can provide the complete contract for the intended competitions and seasons. The source must publish actual player minutes and player market-value shares; team value, ratings, goals, or invented proxies are not valid substitutes.
