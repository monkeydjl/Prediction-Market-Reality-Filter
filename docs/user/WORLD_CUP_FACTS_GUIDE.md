# World Cup Facts Guide

This guide shows how to feed structured 2026 FIFA World Cup facts into PMRF so
the sports-event pipeline can produce better probability context and safely
auto-resolve deterministic events.

The sports layer is intentionally conservative:

- Facts come from operators, official data, or trusted evidence. AI output is
  not a fact source.
- Probability impact is still judged by the analysis model; facts only provide
  structured context and deterministic settlement rules.
- Auto-resolution is fail-closed. If the facts are not decisive, the event stays
  pending.

## Endpoints

All paths below assume the backend is serving the API under `/api`.

Read status:

```text
GET /api/events/sports/world-cup/status
```

Read configured data-source status:

```text
GET /api/events/sports/world-cup/data/sources/status
Header: X-API-Key: <API_WRITE_KEY>
```

List facts:

```text
GET /api/events/sports/world-cup/facts
GET /api/events/sports/world-cup/facts?kind=injury
GET /api/events/sports/world-cup/facts?team=Brazil
```

Operational consistency checks:

```text
GET /api/analytics/result-consistency?limit=25
GET /api/analytics/consistency-repair-plan?limit=25
GET /api/analytics/consistency-repair-preview?history_ids=239&history_ids=240
GET /api/analytics/result-fact-backfill/runs?limit=5
GET /api/analytics/post-match-backfill/runs?limit=5
```

These analytics reads are safe inspection endpoints. Analytics writes require
`X-API-Key`; use optional `X-Client-Source` and `X-Operator` headers to identify
the caller in audit runs:

```text
POST /api/analytics/result-fact-backfill?limit=100&dry_run=true&confirm=false
Header: X-API-Key: <API_WRITE_KEY>
Header: X-Client-Source: ops-script
Header: X-Operator: <operator-id>

POST /api/analytics/consistency-repair?history_ids=239&history_ids=240&dry_run=true&confirm=false
Header: X-API-Key: <API_WRITE_KEY>
Header: X-Client-Source: ops-script
Header: X-Operator: <operator-id>

POST /api/analytics/post-match-backfill?dry_run=true
Header: X-API-Key: <API_WRITE_KEY>
Header: X-Client-Source: ops-script
Header: X-Operator: <operator-id>

POST /api/analytics/reconcile-scoring
Header: X-API-Key: <API_WRITE_KEY>
```

`result-fact-backfill` and `consistency-repair` default to dry-run behavior; a
real write requires `dry_run=false&confirm=true`. `post-match-backfill` also
requires `X-API-Key`; run `dry_run=true` first and only use `dry_run=false`
after reviewing the candidates.

World Cup prediction write operations also require `X-API-Key`, including:

```text
POST /api/world-cup/predictions/init-db
POST /api/world-cup/predictions/sync-fixtures
POST /api/world-cup/predictions/matches/{match_id}/predict
POST /api/world-cup/predictions/matches/{match_id}/analyze
POST /api/world-cup/predictions/batch-predict
POST /api/world-cup/predictions/batch-switch-engine
GET  /api/world-cup/predictions/batch-switch-engine-stream
POST /api/world-cup/predictions/auto-tune/{engine_name}
POST /api/world-cup/predictions/batch-optimize
POST /api/world-cup/predictions/matches/{match_id}/optimize
```

Import facts:

```text
POST /api/events/sports/world-cup/facts/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"facts": [...]}
```

Import trusted match-data snapshots:

```text
POST /api/events/sports/world-cup/data/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"matches": [...], "discipline": [...], "qualifications": [...], "player_awards": [...], "player_statuses": [...], "team_stats": [...], "player_stats": [...]}

POST /api/events/sports/world-cup/data/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"matches": [...], "discipline": [...], "qualifications": [...], "player_awards": [...], "player_statuses": [...], "team_stats": [...], "player_stats": [...]}
```

Import strict official fixed-column CSV exports:

```text
POST /api/events/sports/world-cup/official-csv/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"csv": {"matches": "...", "discipline": "...", "qualifications": "...", "player_awards": "...", "player_statuses": "...", "team_stats": "...", "player_stats": "..."}}

POST /api/events/sports/world-cup/official-csv/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"csv": {"matches": "...", "discipline": "...", "qualifications": "...", "player_awards": "...", "player_statuses": "...", "team_stats": "...", "player_stats": "..."}}
```

Import multiple data-source payloads as one bundle:

```text
POST /api/events/sports/world-cup/data/bundle/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"sources": [{"kind": "matches", "payload": {...}}, {"kind": "standings", "payload": {...}}]}

POST /api/events/sports/world-cup/data/bundle/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"sources": [{"kind": "matches", "payload": {...}}, {"kind": "player_status", "payload": {...}}]}
```

The bundle endpoint is an operator convenience: it runs the same conservative
adapters used by the individual endpoints and imports the combined facts in one
write. Supported `kind` values are `data`, `matches`, `standings`,
`match_events`, `lineups`, `official_csv`, `player_awards`, `player_status`,
and `statistics`.

Bundle preview/import responses include operational metadata for inspection:
`run` reports total conversion duration, source count, converted fact count,
skipped source count, and fetch count when remote feeds were used. Each
`sources[]` item includes its conversion `duration_ms` and status. Configured
feed and provider routes also include sanitized `source_fetches` entries with
request kind, URL without query string, status, and duration. Provider responses
include `skipped_sources` when empty feeds or call-budget limits skip a source.

Import the configured multi-source bundle file:

```text
POST /api/events/sports/world-cup/data/bundle/source/preview
Header: X-API-Key: <API_WRITE_KEY>

POST /api/events/sports/world-cup/data/bundle/source/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
```

Import the configured remote multi-source bundle URL:

```text
POST /api/events/sports/world-cup/data/bundle/url/preview
Header: X-API-Key: <API_WRITE_KEY>

POST /api/events/sports/world-cup/data/bundle/url/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
```

Import configured raw source feed URLs as one bundle:

```text
POST /api/events/sports/world-cup/data/bundle/feeds/preview
Header: X-API-Key: <API_WRITE_KEY>

POST /api/events/sports/world-cup/data/bundle/feeds/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
```

Import configured API-Football World Cup feeds as one bundle:

```text
POST /api/events/sports/world-cup/data/bundle/api-football/preview
Header: X-API-Key: <API_WRITE_KEY>

POST /api/events/sports/world-cup/data/bundle/api-football/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
```

Import configured Sportmonks-style World Cup feeds as one bundle:

```text
POST /api/events/sports/world-cup/data/bundle/sportmonks/preview
Header: X-API-Key: <API_WRITE_KEY>

POST /api/events/sports/world-cup/data/bundle/sportmonks/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
```

Import raw fixture/result exports through the match-source adapter:

```text
POST /api/events/sports/world-cup/matches/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"fixture": {...}, "teams": {...}, "goals": {...}}]}

POST /api/events/sports/world-cup/matches/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"fixture": {...}, "teams": {...}, "goals": {...}}]}
```

Import raw match event/card exports through the match-events adapter:

```text
POST /api/events/sports/world-cup/match-events/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"fixture": {"id": 1001}, "response": [{"type": "Card", "detail": "Red Card"}]}

POST /api/events/sports/world-cup/match-events/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"fixture": {"id": 1001}, "response": [{"type": "Card", "detail": "Red Card"}]}
```

Import raw starting-XI/bench exports through the lineups adapter:

```text
POST /api/events/sports/world-cup/lineups/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"fixture": {"id": 1001}, "response": [{"team": {"name": "Team A"}, "startXI": [...]}]}

POST /api/events/sports/world-cup/lineups/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"fixture": {"id": 1001}, "response": [{"team": {"name": "Team A"}, "startXI": [...]}]}
```

Import raw team/player statistics through the statistics adapter:

```text
POST /api/events/sports/world-cup/statistics/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"fixture": {"id": 1001}, "response": [{"team": {"name": "Team A"}, "statistics": [...]}]}

POST /api/events/sports/world-cup/statistics/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"fixture": {"id": 1001}, "response": [{"team": {"name": "Team A"}, "statistics": [...]}]}
```

Import raw standings/group-table exports through the standings adapter:

```text
POST /api/events/sports/world-cup/standings/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"league": {"standings": [[...] ]}}]}

POST /api/events/sports/world-cup/standings/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"league": {"standings": [[...] ]}}]}
```

The standings adapter is conservative: it maps explicit source statuses such as
`qualified`, `advanced`, `knockout`, or `eliminated` into qualification facts. It
does not infer qualification from points, rank, or goal difference by itself.

Import raw top-scorers/player-awards exports through the player-awards adapter:

```text
POST /api/events/sports/world-cup/player-awards/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"player": {...}, "statistics": [{"goals": {...}}]}]}

POST /api/events/sports/world-cup/player-awards/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"player": {...}, "statistics": [{"goals": {...}}]}]}
```

Import raw injury/availability/suspension/lineup exports through the
player-status adapter:

```text
POST /api/events/sports/world-cup/player-status/preview
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"player": {...}, "team": {...}, "status": "out"}]}

POST /api/events/sports/world-cup/player-status/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
Body: {"response": [{"player": {...}, "team": {...}, "status": "out"}]}
```

The player-status adapter maps raw injury, availability, suspension, and lineup
exports into `injury`, `availability`, `suspension`, or `lineup` facts. It
requires player and team names, and preserves status, severity, fixture/match
id, stage, notes/reason, and applies-to hints when present. API-Football style
injury rows with `player.reason` or `player.type=Missing Fixture` are treated
as injury statuses.

Import the configured trusted data-source file:

```text
POST /api/events/sports/world-cup/data/source/preview
Header: X-API-Key: <API_WRITE_KEY>

POST /api/events/sports/world-cup/data/source/import?replace=false
Header: X-API-Key: <API_WRITE_KEY>
```

Configured source files and remote bundle responses must include `source` and a
timezone-aware `observed_at` timestamp. For `WORLD_CUP_SOURCE_BUNDLE_FILE` or
`WORLD_CUP_SOURCE_BUNDLE_URL`, each `sources[]` entry must provide that metadata
either at the entry level or inside its `payload`. PMRF rejects stale snapshots older than
`WORLD_CUP_DATA_MAX_AGE_HOURS` (default: 168). Set the value to `0` only if an
operator intentionally wants to disable the age check.

Preview deterministic resolution:

```text
POST /api/events/sports/world-cup/resolve?dry_run=true
Header: X-API-Key: <API_WRITE_KEY>
```

Apply deterministic resolution:

```text
POST /api/events/sports/world-cup/resolve?dry_run=false
Header: X-API-Key: <API_WRITE_KEY>
```

## Minimal Operator Flow

1. Put structured facts in a JSON file using the sample at
   `docs/examples/world-cup-facts.sample.json`.
2. Or put trusted feed-shaped match data in a JSON file using the sample at
   `docs/examples/world-cup-data.sample.json`; PMRF converts it into facts.
   If your source exports CSV, use the JSON-wrapped CSV sample at
   `docs/examples/world-cup-data-csv.sample.json`.
   If your official CSV export must be schema-locked, use
   `docs/examples/world-cup-official-csv-source.sample.json` with the
   `official-csv/*` endpoints.
   If you want to preview or import several raw exports in one request, use
   `docs/examples/world-cup-source-bundle.sample.json` with the
   `data/bundle/*` endpoints.
   If your source exports raw fixture/result records, use
   `docs/examples/world-cup-match-source.sample.json` with the `matches/*`
   endpoints; PMRF normalizes it first, then converts it into facts.
   If your source exports raw match event/card rows, use
   `docs/examples/world-cup-match-events-source.sample.json` with the
   `match-events/*` endpoints; PMRF normalizes card rows into discipline facts.
   If your source exports raw starting-XI or bench rows, use
   `docs/examples/world-cup-lineups-source.sample.json` with the `lineups/*`
   endpoints.
   If your source exports raw standings/group tables, use
   `docs/examples/world-cup-standings-source.sample.json` with the
   `standings/*` endpoints.
   If your source exports raw top-scorers/player-awards records, use
   `docs/examples/world-cup-player-awards-source.sample.json` with the
   `player-awards/*` endpoints.
   If your source exports raw player injury, availability, suspension, or lineup
   records, use `docs/examples/world-cup-player-status-source.sample.json` with
   the `player-status/*` endpoints.
   If your source exports raw team or player statistics, use
   `docs/examples/world-cup-statistics-source.sample.json` with the
   `statistics/*` endpoints.
3. For trusted match data, call `data/preview` and inspect the generated facts.
   For fixed-column official CSV, call `official-csv/preview`.
   For multi-source bundles, call `data/bundle/preview`.
   For raw fixture/result exports, call `matches/preview`; for raw standings,
   call `standings/preview`; for raw match event/card exports, call
   `match-events/preview`; for raw starting-XI/bench exports, call
   `lineups/preview`; for raw top-scorers/player-awards exports, call
   `player-awards/preview`; for raw player status exports, call
   `player-status/preview`; for raw team/player statistics, call
   `statistics/preview`. If the data lives in `WORLD_CUP_DATA_FILE`, call
   `data/source/preview`. If a multi-source bundle lives in
   `WORLD_CUP_SOURCE_BUNDLE_FILE`, call `data/bundle/source/preview`.
   If it lives behind `WORLD_CUP_SOURCE_BUNDLE_URL`, call
   `data/bundle/url/preview`. If raw source feeds are configured with
   `WORLD_CUP_MATCH_SOURCE_URL`, `WORLD_CUP_MATCH_EVENTS_SOURCE_URL`,
   `WORLD_CUP_LINEUPS_SOURCE_URL`, `WORLD_CUP_STANDINGS_SOURCE_URL`,
   `WORLD_CUP_PLAYER_AWARDS_SOURCE_URL`, `WORLD_CUP_PLAYER_STATUS_SOURCE_URL`,
   or `WORLD_CUP_STATISTICS_SOURCE_URL`, call `data/bundle/feeds/preview`.
   If `WORLD_CUP_API_FOOTBALL_API_KEY` is configured, call
   `data/bundle/api-football/preview`.
   If `WORLD_CUP_SPORTMONKS_API_TOKEN` and at least one Sportmonks feed URL are
   configured, call `data/bundle/sportmonks/preview`.
4. Import with `replace=true` when the file is the current full fact snapshot.
   Use `replace=false` for incremental upserts.
5. Call the facts list endpoint and inspect the normalized records.
6. Run `resolve?dry_run=true` and review every `would_resolve` row.
7. Run `resolve?dry_run=false` only when the dry-run output is correct.

## PowerShell Example

```powershell
$key = "<API_WRITE_KEY>"
$headers = @{
  "X-API-Key" = $key
  "X-Client-Source" = "ops-script"
  "X-Operator" = "<operator-id>"
}
$body = Get-Content -Raw docs\examples\world-cup-facts.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/facts/import?replace=true" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/resolve?dry_run=true" `
  -Headers @{ "X-API-Key" = $key }
```

For protected analytics writes, prefer the shared `$headers` object so audit
runs capture the caller:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/analytics/result-fact-backfill?limit=100&dry_run=true&confirm=false" `
  -Headers $headers
```

For a trusted match-data snapshot, switch the import URL and body file:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-data.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For CSV exports, use the same endpoint with the CSV sample:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-data-csv.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For strict fixed-column official CSV exports, use the official CSV adapter:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-official-csv-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/official-csv/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/official-csv/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For a multi-source bundle, use the bundle adapter:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-source-bundle.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw fixture/result exports, use the match-source adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-match-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/matches/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/matches/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw match event/card exports, use the match-events adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-match-events-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/match-events/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/match-events/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw starting-XI/bench exports, use the lineups adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-lineups-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/lineups/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/lineups/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw standings/group-table exports, use the standings adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-standings-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/standings/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/standings/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw top-scorers/player-awards exports, use the player-awards adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-player-awards-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/player-awards/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/player-awards/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw injury/availability/suspension/lineup exports, use the player-status
adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-player-status-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/player-status/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/player-status/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For raw team/player statistics exports, use the statistics adapter first:

```powershell
$body = Get-Content -Raw docs\examples\world-cup-statistics-source.sample.json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/statistics/preview" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/statistics/import?replace=false" `
  -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json" `
  -Body $body
```

For a configured source file, set `WORLD_CUP_DATA_FILE` and call the source
endpoints. The file must include fresh source metadata:

```json
{
  "source": "official_feed",
  "source_url": "https://example.com/world-cup-feed",
  "observed_at": "2026-07-20T00:00:00Z",
  "matches": []
}
```

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/source/preview" `
  -Headers @{ "X-API-Key" = $key }

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/source/import?replace=false" `
  -Headers @{ "X-API-Key" = $key }
```

For a configured multi-source bundle file, set `WORLD_CUP_SOURCE_BUNDLE_FILE`
and call the bundle source endpoints. Each bundle source must include fresh
metadata:

```json
{
  "sources": [
    {
      "kind": "matches",
      "source": "api_football",
      "source_url": "https://example.com/fixtures",
      "observed_at": "2026-07-20T00:00:00Z",
      "payload": {
        "response": [
          {
            "fixture": {"id": 1001, "status": {"short": "FT"}},
            "teams": {
              "home": {"name": "Team A", "winner": true},
              "away": {"name": "Team B", "winner": false}
            },
            "goals": {"home": 2, "away": 0}
          }
        ]
      }
    }
  ]
}
```

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/source/preview" `
  -Headers @{ "X-API-Key" = $key }

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/source/import?replace=false" `
  -Headers @{ "X-API-Key" = $key }
```

For a configured remote multi-source bundle, set `WORLD_CUP_SOURCE_BUNDLE_URL`
and call the bundle URL endpoints. The URL must return the same JSON shape as
`docs/examples/world-cup-source-bundle.sample.json`. If the upstream requires a
key, configure `WORLD_CUP_SOURCE_BUNDLE_AUTH_HEADER` and
`WORLD_CUP_SOURCE_BUNDLE_AUTH_VALUE`; avoid putting real keys in URL query
strings.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/url/preview" `
  -Headers @{ "X-API-Key" = $key }

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/url/import?replace=false" `
  -Headers @{ "X-API-Key" = $key }
```

For configured raw source feeds, set one or more of
`WORLD_CUP_MATCH_SOURCE_URL`, `WORLD_CUP_MATCH_EVENTS_SOURCE_URL`,
`WORLD_CUP_LINEUPS_SOURCE_URL`, `WORLD_CUP_STANDINGS_SOURCE_URL`,
`WORLD_CUP_PLAYER_AWARDS_SOURCE_URL`, `WORLD_CUP_PLAYER_STATUS_SOURCE_URL`, and
`WORLD_CUP_STATISTICS_SOURCE_URL`. PMRF fetches each configured URL, strips
query strings from returned `source_url` metadata, adds a fetch timestamp when
the payload lacks `observed_at`, and then runs the existing bundle adapters.
Use the shared `WORLD_CUP_SOURCE_BUNDLE_AUTH_HEADER` /
`WORLD_CUP_SOURCE_BUNDLE_AUTH_VALUE` settings when every configured feed uses
the same upstream auth header.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/feeds/preview" `
  -Headers @{ "X-API-Key" = $key }

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/feeds/import?replace=false" `
  -Headers @{ "X-API-Key" = $key }
```

For API-Football, set `WORLD_CUP_API_FOOTBALL_API_KEY`. The defaults use
`WORLD_CUP_API_FOOTBALL_BASE_URL=https://v3.football.api-sports.io`,
`WORLD_CUP_API_FOOTBALL_LEAGUE_ID=1`, and
`WORLD_CUP_API_FOOTBALL_SEASON=2026`. PMRF fetches fixtures, standings, top
scorers, and injuries; empty `response: []` feeds are skipped, while
API-Football `errors` fail closed. Set
`WORLD_CUP_API_FOOTBALL_FETCH_EVENTS=true` only when you want PMRF to make
additional `fixtures/events?fixture=...` calls and convert card rows into
`discipline` facts. Set `WORLD_CUP_API_FOOTBALL_FETCH_LINEUPS=true` only when
you want PMRF to make additional `fixtures/lineups?fixture=...` calls and
convert starting-XI/bench rows into `lineup` facts. Set
`WORLD_CUP_API_FOOTBALL_FETCH_STATISTICS=true` only when you want PMRF to make
additional `fixtures/statistics?fixture=...` and `fixtures/players?fixture=...`
calls and convert team/player rows into `team_stat` / `player_stat` facts.
Use `WORLD_CUP_API_FOOTBALL_MAX_DETAIL_CALLS` to cap those fixture-level detail
requests across events, lineups, and statistics. If an optional detail source
would exceed the remaining budget, PMRF skips that source and reports it in
`skipped_sources` with `reason: "call budget exceeded"` plus `required_calls`
and `remaining_calls`. API-Football responses include a `call_budget` block with
fixture count, enabled detail feeds, used calls, skipped calls, and remaining
calls.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/api-football/preview" `
  -Headers @{ "X-API-Key" = $key }

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/api-football/import?replace=false" `
  -Headers @{ "X-API-Key" = $key }
```

For Sportmonks-style provider feeds, set `WORLD_CUP_SPORTMONKS_API_TOKEN` and
one or more of `WORLD_CUP_SPORTMONKS_FIXTURES_URL`,
`WORLD_CUP_SPORTMONKS_STANDINGS_URL`, or
`WORLD_CUP_SPORTMONKS_TOP_SCORERS_URL`. PMRF appends `api_token` to the request
URL when it is not already present, strips query strings from returned metadata,
and converts explicit provider facts only: fixtures to `match_result`,
standings rows with explicit qualified/eliminated descriptions to
`qualification`, and top-scorer rows to `player_award`. Empty configured feeds
are skipped.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/sportmonks/preview" `
  -Headers @{ "X-API-Key" = $key }

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/api/events/sports/world-cup/data/bundle/sportmonks/import?replace=false" `
  -Headers @{ "X-API-Key" = $key }
```

To import a configured bundle on a schedule, set
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_ENABLED=true`. Use
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE=url` for `WORLD_CUP_SOURCE_BUNDLE_URL`, or
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE=file` for `WORLD_CUP_SOURCE_BUNDLE_FILE`,
or `WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE=feeds` for the configured raw source
feed URLs, or `WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE=api_football` for the
configured API-Football provider, or `WORLD_CUP_SOURCE_BUNDLE_IMPORT_MODE=sportmonks`
for the configured Sportmonks-style provider.
The default run time is 05:20 UTC and can be changed with
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_HOUR_UTC` and
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_MINUTE_UTC`. Keep
`WORLD_CUP_SOURCE_BUNDLE_IMPORT_REPLACE=false` for incremental upserts; set it
to `true` only when the configured bundle is the full current fact snapshot.

Do not put real keys in committed files or docs.

## Fact Shape

Common fields:

```json
{
  "fact_id": "optional stable id",
  "kind": "injury",
  "tournament": "2026 FIFA World Cup",
  "team": "Brazil",
  "player": "Player Name",
  "status": "out",
  "severity": "high",
  "source": "manual",
  "source_url": "https://...",
  "confidence": 1.0,
  "observed_at": "2026-06-22T00:00:00Z",
  "applies_to": ["world-cup-2026:brazil-semifinal"],
  "notes": "Short operator note."
}
```

Supported `kind` values:

```text
injury
availability
suspension
discipline
qualification
match_state
match_result
lineup
player_award
team_stat
player_stat
tournament_status
```

## Current Resolution Rules

Team progression:

- YES when a `qualification` fact for the team says it reached the required
  stage.
- NO when a `qualification` fact says the team is eliminated before the stage.

Red-card threshold:

- YES when summed `red_cards` reaches the threshold in the event title.
- NO only when the tournament is explicitly complete and the total is below the
  threshold.

Penalty shootout:

- YES when any `match_result` / `match_state` fact has
  `penalty_shootout=true`.
- NO only when the tournament is explicitly complete with no shootout fact.

Final extra time:

- YES when a final `match_result` has `extra_time=true`.
- NO when a final `match_result` is finished and has `extra_time=false`.

Golden Boot / scorer threshold:

- YES when a top-scorer / Golden Boot `player_award` fact has `goals` at or
  above the threshold in the event title.
- NO when final top-scorer facts or a complete tournament show the top scorer
  finished below the threshold.

## Field Notes

- `confidence` is clamped to 0..1 and copied to the outcome confidence when a
  decisive fact resolves an event.
- `fact_id` may be omitted; PMRF generates a stable id from fact content. For
  repeat imports, provide your own stable `fact_id` when possible.
- `applies_to` can include a specific event source id such as
  `world-cup-2026:brazil-semifinal`, but entity matching also works for team
  facts.
- Use `tournament_status` with `tournament_complete=true` only after the whole
  tournament is complete.

## Trusted Data Import Shape

`data/preview` and `data/import` accept a compact source-normalized snapshot and
convert it to the same facts used by analysis and auto-resolution. Preview
returns generated facts without writing them:

- `matches`: creates `match_result` facts with score, red/yellow cards,
  `extra_time`, `penalty_shootout`, and match context such as `kickoff_at`,
  `venue`, and `referee` when present.
- `discipline`: creates `discipline` facts for match card events or aggregate
  red/yellow card counts.
- `qualifications`: creates `qualification` facts for team progression.
- `player_awards`: creates `player_award` facts for Golden Boot / top scorer
  events.
- `player_statuses`: creates `injury`, `availability`, `suspension`, or
  `lineup` facts for player status and availability context, preserving
  `position`, `formation`, and `jersey_number` when present.
- `team_stats`: creates `team_stat` facts with `stat_name`, `stat_value`, and
  optional `stat_unit`.
- `player_stats`: creates `player_stat` facts with player/team context,
  `position`, `jersey_number`, `stat_name`, `stat_value`, and optional
  `stat_unit`.
- `tournament_status`: creates one `tournament_status` fact.

CSV exports can be wrapped under `csv.matches`, `csv.discipline`,
`csv.qualifications`, `csv.player_awards`, `csv.player_statuses`,
`csv.team_stats`, and `csv.player_stats`. The first row must be headers whose
names match the JSON fields, such as `match_id`,
`stage`, `home_team`, `away_team`, `status`, `kickoff_at`, `venue`, `referee`,
`minute`, `red_cards`, `yellow_cards`, `penalty_shootout`, `team`,
`already_qualified`, `award`, `player`, `goals`, `severity`, `position`,
`formation`, `jersey_number`, `stat_name`, `stat_value`, `stat_unit`, and
`reason`.

The `official-csv/*` adapter is stricter than generic `data/preview`: it accepts
the same `csv.*` sections but rejects missing columns, extra columns, or column
order changes. The fixed headers are:

```text
csv.matches: match_id,stage,kickoff_at,venue,referee,home_team,away_team,status,home_score,away_score,winner,extra_time,penalty_shootout,home_red_cards,away_red_cards,home_yellow_cards,away_yellow_cards
csv.discipline: event_id,match_id,stage,team,player,minute,status,red_cards,yellow_cards,reason
csv.qualifications: team,stage,status,already_qualified,already_eliminated
csv.player_awards: award,player,team,goals,rank,status
csv.player_statuses: kind,team,player,status,severity,match_id,stage,position,formation,jersey_number,reason,applies_to
csv.team_stats: team,match_id,stage,stat_name,stat_value,stat_unit
csv.player_stats: team,player,match_id,stage,position,jersey_number,stat_name,stat_value,stat_unit
```
