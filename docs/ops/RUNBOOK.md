# Operations Runbook

## Required Production Settings

- Set `API_WRITE_KEY` and send it as `X-API-Key` for all protected writes.
  This includes discovery, analysis, manual resolution, auto-resolution,
  tracking, link verification, analytics repair/backfill/reconcile endpoints,
  World Cup prediction writes, and the write-like
  `/api/world-cup/predictions/batch-switch-engine-stream` GET stream.
- Set `CORS_ALLOWED_ORIGINS` to the deployed dashboard origins. Do not use `*`
  when credentials are enabled.
- If overriding `CORS_ALLOWED_HEADERS`, include `X-API-Key`,
  `X-Client-Source`, and `X-Operator` so protected browser writes can pass
  preflight.
- Keep `RATE_LIMIT_ENABLED=true` unless a trusted reverse proxy provides an
  equivalent limit.
- Keep `LOG_FILE` on persistent storage.

## Operator Audit Headers

Protected analytics writes accept optional provenance headers:

- `X-Client-Source`: stable caller name, such as `world-cup-dashboard`,
  `ops-script`, or `scheduler`.
- `X-Operator`: human or automation operator identifier.

These headers are stored in audit run metadata for result fact backfill,
consistency repair, and post-match backfill. `X-API-Key` is only validated and
must not be copied into audit metadata, logs, URLs, or committed examples.

## Dashboard operator key (browser)

The static dashboard stores a **session-only** copy of `API_WRITE_KEY` so
operators can call protected write APIs from the UI (resolve, optimize apply,
edge detect, etc.).

| Rule | Detail |
|------|--------|
| Storage | `sessionStorage` keys `pmrf.operatorApiKey` / `pmrf.operatorId` only |
| Not used | `localStorage` (must not persist across browser restarts) |
| Headers | `X-API-Key`, optional `X-Operator` |
| UI | Nav **授权** / **清除**; hint shows masked key only |
| Scope | Current browser tab session; closing the tab clears the key |
| XSS residual risk | Any XSS can still read `sessionStorage` while the tab is open |

**Long-term upgrade (not implemented):** BFF session cookie holding the secret
server-side so the raw write key never enters JavaScript. Tracked as backlog
P2-FE9 residual / architecture work.

Never paste `API_WRITE_KEY` into chat logs, screenshots of DevTools Application
storage, or committed `.env` examples with real production values.

## Health Check

Use `GET /api/health`. A response status of `degraded` means at least one
recorded scheduler job has failed and should be inspected before trusting new
calibration output.

## Backups

Run the backup script daily from cron or a systemd timer:

```bash
cd /opt/prediction-market-reality-filter/backend
/opt/prediction-market-reality-filter/.venv/bin/python scripts/backup_stores.py
```

The archive includes the JSON event store, audit log, cache, SQLite loop DB,
and SQLite WAL/SHM files when present.

By default the script keeps the latest 30 `pmrf-backup-*.zip` archives in the
backup directory. Override this with `--keep N` if the host has a different
retention policy.

### Encryption at rest

Set `BACKUP_ENCRYPTION_KEY` in `backend/.env` (or pass `--encryption-key`) to
write each archive as a pyzipper AES-256 encrypted zip. Without the key the
archive cannot be restored, so store the passphrase alongside your other
secrets (e.g. in the same secrets manager that holds `API_WRITE_KEY`). Leave
`BACKUP_ENCRYPTION_KEY` empty only when the backup volume is already encrypted
at rest (e.g. an encrypted LVM / EBS volume).

To restore an encrypted archive:

```bash
/opt/prediction-market-reality-filter/.venv/bin/python -c "
import pyzipper
with pyzipper.AESZipFile('pmrf-backup-YYYYMMDD-HHMMSSZ.zip') as zf:
    zf.setpassword(b'YOUR_PASSPHRASE')
    zf.extractall('/path/to/restore/dir')
"
```

Enable the daily backup timer (runs backup_stores.py via systemd):

```bash
sudo systemctl enable prediction-market-reality-filter-backup.timer
sudo systemctl start prediction-market-reality-filter-backup.timer
```

After restoring from an archive, run one auto-resolve pass before resuming
normal unattended operation. The pass calls `reconcile_predictions()` and heals
any temporary JSON/SQLite mismatch captured while a backup was being written.

## Monitoring

Enable the built-in health check timer. It runs `backend/scripts/healthcheck.py`
every 5 min, verifies local `/api/health`, and only then pings the optional
external dead-man URL:

```bash
sudo systemctl enable prediction-market-reality-filter-healthcheck.timer
sudo systemctl start prediction-market-reality-filter-healthcheck.timer
```

Configure the target in `/etc/prediction-market-reality-filter.env`:

```bash
PMRF_HEALTHCHECK_URL=http://localhost:8000/api/health
PMRF_HEALTHCHECK_TIMEOUT_SECONDS=5
PMRF_DEADMAN_URL=https://<uptime-provider>/<dead-man-token>
```

Leave `PMRF_DEADMAN_URL` empty for local-only health checking. With it set,
missed pings indicate that the service is down, degraded, or unable to reach
the external monitor.

### Prometheus metrics

Scrape `GET /metrics` (Prometheus text format). Core series include:

| Metric | Meaning |
|--------|---------|
| `pmrf_scheduler_failed_runs_total{job_name}` | Counter of failed scheduler jobs |
| `pmrf_scheduler_last_success_timestamp{job_name}` | Unix time of last successful run |
| `pmrf_calibration_brier_score` | Current Brier (lower better; ~0.33 ≈ random) |
| `pmrf_calibration_drift_score` | Relative drift vs baseline (positive = worse) |
| `pmrf_overlay_latency_ms_*` | Overlay build latency histogram |
| `pmrf_llm_token_cost_total` / `pmrf_llm_token_usage_total` | LLM cost / tokens |
| `pmrf_decision_quality_downgrade_total{reason}` | Decision quality demotions |

JSON companions (same data, operator-friendly):

- `GET /api/quality-metrics/summary`
- `GET /api/quality-metrics/drift` — always computes; dispatch is separate
- `GET /api/quality-metrics/alerts`
- `GET /api/quality-metrics/report`

### Grafana dashboard (E8)

Import the provisioned JSON:

```text
docs/ops/grafana/pmrf-overview.json
```

Point the dashboard datasource variable `DS_PROMETHEUS` at the scrape target
that collects `/metrics`. Panels cover scheduler failure rate, seconds since
last success, Brier/drift, overlay p95, LLM cost, and decision quality
downgrades.

### Calibration drift alerts (Q6)

Drift **computation** is always available via `/api/quality-metrics/drift`.
**Dispatch** (webhook + Sentry breadcrumb + structured log) is opt-in:

```bash
DRIFT_ALERTS_ENABLED=false          # set true only after webhook/Sentry ready
DRIFT_ALERT_WEBHOOK_URL=""          # empty → Sentry + log only
DRIFT_ALERT_COOLDOWN_SECONDS=3600   # per alert-code cooldown
DRIFT_BRIER_RELATIVE_THRESHOLD=0.30
DRIFT_BUCKET_DEVIATION_PP=20.0
DRIFT_BUCKET_MIN_SAMPLES=2
DRIFT_RECENT_WINDOW_N=50
DRIFT_SCHEDULER_ZERO_RESOLVED_RUNS=3
```

Leave `DRIFT_ALERTS_ENABLED=false` until you have enough settled samples and a
trusted webhook endpoint; otherwise you only get noise.

### Scheduler failure alerts (E8)

Every failed job already:

1. Writes the loop-run ledger
2. Increments `pmrf_scheduler_failed_runs_total`
3. Forwards the exception to Sentry via `_finish_run`

An **additional** best-effort dispatcher can also POST a webhook and emit a
Sentry breadcrumb, with per-`job_name` cooldown:

```bash
SCHEDULER_FAILURE_ALERT_ENABLED=false
SCHEDULER_FAILURE_ALERT_WEBHOOK_URL=""
SCHEDULER_FAILURE_ALERT_COOLDOWN_SECONDS=1800
```

Default OFF keeps installs byte-identical to pre-E8. Enable only when you want
operator-facing webhook spam control on top of existing Sentry + Prometheus.

### Sport market matching eval (P1-SB1)

Offline precision/recall for the three-layer matcher (rule / LLM / manual):

```bash
cd backend
PYTHONPATH=. python -m scripts.eval_sport_market_matching \
  --dataset data/eval/sport_market_link_eval.sample.jsonl \
  --matcher rule \
  --report text
```

Use `--matcher all` and optional `--manual-overrides path.jsonl` for full
coverage. The sample JSONL is intentionally small; expand it with real labeled
links before treating F1 as production-ready.

### Betting / 联赛赛程（竞猜模块）

The 竞猜 hub (`/sports/betting`) and competition landings merge a static FE
catalog with `GET /api/betting/catalog` (flag-free). Cards show `adapter_likely`
from current data flags — **not** a guarantee of non-empty fixtures.

To populate Kernel league lists (`GET /api/predictions/matches?competition=epl`):

1. In `backend/.env` (defaults stay OFF):
   ```bash
   KERNEL_PREDICTION_ENABLED=true
   PHASE2_LEAGUES_ENABLED=true        # EPL + La Liga / Bundesliga / Serie A / Ligue 1 / UCL
   # (catalog flag epl_data_enabled is an alias of PHASE2 — there is no EPL_DATA_ENABLED env)
   # FOOTBALL_DATA_API_KEY=...        # Football-Data.org for EU leagues
   PHASE4_NBA_ENABLED=true            # optional — requires BALLDONTLIE_API_KEY
   # BALLDONTLIE_API_KEY=...          # balldontlie.io (free tier ~5 req/min)
   ```
2. Restart the API so `_get_kernel()` rebuilds MultiAdapter registrations.
3. Sync schedules (operator/write path or adapter-level `sync_schedule` via
   existing scheduler / scripts — see Phase 2 plans). Empty `match` count on
   landings usually means flags OFF or no ingest yet, not a broken filter.
4. Operator schedule sync (write key required — never commit real keys):
   ```bash
   # One competition at a time (aliases: epl, laliga, bundesliga, seriea, ligue1, ucl)
   curl -s -X POST \
     -H "X-API-Key: $API_WRITE_KEY" \
     "$BASE/api/predictions/schedule/sync?competition=epl"
   ```
   Optional: `sport=football`. Empty `synced` usually means flag OFF, missing
   Football-Data key, or upstream empty — not a broken short-circuit.
5. Smoke:
   ```bash
   curl -s "$BASE/api/betting/catalog" | jq '.flags,.competitions[]|{id,adapter_likely}'
   curl -s "$BASE/api/betting/status" | jq '{kernel_ready,registered_prefixes,flags,lol}'
   # Default list is **today UTC only**. Landings use days_ahead=45.
   curl -s "$BASE/api/predictions/matches?competition=epl"
   curl -s "$BASE/api/predictions/matches?competition=epl&days_ahead=45"
   ```
   Football-Data season year is the autumn start (currently **2026** → 2026-27).
   Empty `matches` with non-zero `synced` usually means no fixtures in the
   selected date window (off-season / mid-break), not a failed sync.
   `GET /api/betting/status` is read-only (no write key). It lists MultiAdapter
   prefixes currently registered when Kernel is ON, plus a non-secret `lol`
   diagnostics object (see LoL section below).

6. Interpreting multi-league sync (local 2026-07 verification):

   | Result | Meaning |
   |--------|---------|
   | `synced>0`, `days_ahead=45` has rows | Preferable: 2026-27 (or next) openers in window |
   | `synced>0`, `d45=0` / `d60=0` | Fixtures in DB but **outside upcoming window** — typically finished prior campaign, or openers >60d out |
   | `synced=0` | Flag/key/upstream failure (see logs); UCL preferred year may 404 then **fall back** one season |

   Vendor publish lag (not a local adapter bug): as of mid-summer 2026,
   **Bundesliga** may still only return finished 2025-26 fixtures for season
   year 2026, and **UCL** season 2026 may 404 (adapter falls back to 2025,
   which is finished). Re-sync after Football-Data publishes the next campaign;
   do not invent openers.

### MLB (Phase 5 / statsapi.mlb.com)

1. `PHASE5_MLB_ENABLED=true` in `backend/.env` (no API key — official free Stats API).
2. Restart API so `mlb-` registers (`GET /api/betting/status` → prefixes).
3. Sync (write key; one request for Mar–Nov of current year):
   ```bash
   curl -s -X POST \
     -H "X-API-Key: $API_WRITE_KEY" \
     "$BASE/api/predictions/schedule/sync?competition=mlb"
   ```
4. List:
   ```bash
   curl -s "$BASE/api/predictions/matches?competition=mlb&days_ahead=45"
   ```
   Window is calendar year Mar–Nov. Mid-season should show upcoming games;
   off-season may be finished-only until spring schedules publish.

### NBA (Phase 4 / balldontlie)

1. `PHASE4_NBA_ENABLED=true` and non-empty `BALLDONTLIE_API_KEY` in `backend/.env`.
2. Restart API so `nba-` registers (check `GET /api/betting/status` → prefixes).
3. Sync (write key; full-season pagination is slow on free tier — minutes possible):
   ```bash
   curl -s -X POST \
     -H "X-API-Key: $API_WRITE_KEY" \
     "$BASE/api/predictions/schedule/sync?competition=nba"
   ```
4. List (landings use `days_ahead=45`; mid-summer may still be empty until openers):
   ```bash
   curl -s "$BASE/api/predictions/matches?competition=nba&days_ahead=45"
   curl -s "$BASE/api/predictions/matches?competition=nba&days_ahead=60"
   ```
   Preferred season year is **2026** (2026-27); if empty/unavailable the adapter
   falls back to **2025** (2025-26, often finished by mid-summer). Free tier is
   ~5 req/min; full-season pagination can take **several minutes**. Client
   backs off on HTTP 429 and may return a **partial** season rather than zero.
   `synced=0` with key set → wait ~1–2 min and retry, or check 401 in logs.

   Local verification (2026-07-23): `synced=1322` after ~3 min (2026 empty →
   **2025-26** full season). All rows `finished` through 2026-06-13 playoffs;
   `days_ahead=45/60` correctly **0** until 2026-27 openers appear on balldontlie.

World Cup remains on `/api/world-cup/*` (not MultiAdapter football prefixes for
the thematic UI). Esports stays `coming_soon` — see `docs/dev/ESPORTS_BOUNDARY.md`
and ADR-004.

UI: competition landings show a **同步赛程** button when the browser session has
an operator write key (`sessionStorage` via operator credentials UI). LoL landing
(`/sports/betting/lol`) stays `coming_soon` and shows a **dry-run ops** panel
(flags / vendor effective / blocked) without fake markets or auto-poll.

### LoL esports (ADR-004 / ADR-005)

#### Flags and env (defaults safe)

| Env | Default | Purpose |
|-----|---------|---------|
| `PHASE_LOL_ENABLED` | `false` | Register `lol-` adapter + `lol_market_only` engine |
| `LOL_DRY_RUN_IMPORT` | `false` | Load series from local JSON on `sync_schedule` |
| `LOL_DRY_RUN_FIXTURES_PATH` | empty | Optional absolute path; empty → sample fixture if present |
| `LOL_SCHEDULE_VENDOR` | `null` | Config shell: `null` \| `dry_run` \| `grid` \| `pandascore` |
| `LOL_VENDOR_API_BASE` | empty | Non-secret base URL placeholder (not used until PartnerHttp) |
| `LOL_VENDOR_API_KEY` | empty | **Secret store only** — never log, never return from API |
| `LOL_SETTLE_GRACE_HOURS` | `6` | ADR-005 D6 grace shell (used when settle client lands) |

`PHASE_LOL_ENABLED` alone does **not** open production data. Setting
`LOL_SCHEDULE_VENDOR=grid` (or `pandascore`) does **not** enable HTTP.

#### Schedule source resolver guard

`resolve_lol_schedule_source()` (used by default `LolAdapter()`):

| Requested vendor | Effective | `schedule_source_blocked` |
|------------------|-----------|---------------------------|
| `null` | `null` (Null source) | `false` |
| `dry_run` | `dry_run` (still Null HTTP; file import via dry-run flag) | `false` |
| `grid` / `pandascore` | forced `null` | `true` |
| unknown | forced `null` | `true` |

Production partner HTTP ships only after GATES **P2 + P3 + P6** are fully
`[x]`. See `docs/dev/lol/GATES.md` and ADR-005.

#### Operator dry-run path

1. Set `KERNEL_PREDICTION_ENABLED=true` and `PHASE_LOL_ENABLED=true` (local/staging only).
2. Optionally `LOL_DRY_RUN_IMPORT=true` and a fixtures path (or rely on repo sample).
3. Restart API so MultiAdapter rebuilds (`lol-` in `registered_prefixes`).
4. Sync (write key required):
   ```bash
   curl -s -X POST \
     -H "X-API-Key: $API_WRITE_KEY" \
     "$BASE/api/predictions/schedule/sync?sport=lol"
   ```
5. List: `GET /api/predictions/matches?sport=lol` (or `competition=lol_lck`).

#### Status / catalog diagnostics (no secrets)

```bash
curl -s "$BASE/api/betting/catalog" | jq '.flags | {phase_lol_enabled,lol_dry_run_import,lol_dry_run_path_configured}'
curl -s "$BASE/api/betting/status" | jq '{kernel_ready,registered_prefixes,flags,lol}'
```

`lol` object fields (booleans / ids only — **never** path strings or API keys):

| Field | Meaning |
|-------|---------|
| `schedule_vendor` | Configured / known vendor id (`null` if unknown) |
| `effective_schedule_vendor` | Runtime after resolver (`null` when blocked) |
| `schedule_source_blocked` | `true` when commercial/unknown vendor forced to Null |
| `schedule_source_reason` | Short human reason (no secrets) |
| `vendor_api_base_configured` | Whether base env is non-empty |
| `vendor_api_key_configured` | Whether key env is non-empty (**not** the key) |
| `settle_grace_hours` | From `LOL_SETTLE_GRACE_HOURS` |
| `production_http_client_ready` | Always `false` until PartnerHttp ships |

Catalog/status `flags` also include `phase_lol_enabled`, `lol_dry_run_import`,
`lol_dry_run_path_configured` (path presence only).

#### Product facts

- **Vendor (ADR-005):** preferred production schedule+settle = **GRID-class**
  commercial LoL series feed. GRID Open Access is CS2/Dota only — not LoL.
  PandaScore-class APIs are optional **odds enrichment**, not sole settlement.
- v1 leagues: `lol_lck` / `lol_lpl` / `lol_lec` / `lol_worlds` / `lol_msi`.
- Engine: `lol_market_only` (series winner only).
- Predict: `engine=auto` is **sport-aware** — for `sport=lol` /
  `competition=lol_*` it selects engines with `supported_sports` containing
  `lol` (or `*`), never a higher-accuracy football engine. Explicit
  `engine=lol_market_only` still works.
- Catalog: `GET /api/betting/catalog` includes `lol`; hub shows `LoL=ON/OFF`.
- UI: `/sports/betting/lol` — no placeholder odds; ops panel only.
- Procurement: product/legal own commercial access request; no API keys in git.

## Event ID Migration

New events use 16-hex SHA-1 prefixes. To migrate legacy 12-hex event IDs across
the JSON event store, audit JSONL, and loop SQLite tables, run a dry-run first:

```bash
cd /opt/prediction-market-reality-filter/backend
/opt/prediction-market-reality-filter/.venv/bin/python scripts/migrate_event_ids.py
```

If the report has no conflicts, take a backup and apply:

```bash
/opt/prediction-market-reality-filter/.venv/bin/python scripts/migrate_event_ids.py --apply
```

## Docker Deployment

```bash
# Build frontend first
cd frontend && npm ci && npm run build && cd ..

# Build and start
docker compose -f deploy/docker-compose.yml up -d --build
```

The container includes a healthcheck that pings `/api/health` every 30 seconds.

## Process Supervision

Use the two systemd units in `deploy/`:

```bash
sudo cp deploy/prediction-market-reality-filter.service /etc/systemd/system/
sudo cp deploy/prediction-market-reality-filter-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable prediction-market-reality-filter.service
sudo systemctl enable prediction-market-reality-filter-scheduler.service
sudo systemctl start prediction-market-reality-filter.service
sudo systemctl start prediction-market-reality-filter-scheduler.service
```

The API unit sets `SCHEDULER_ENABLED=false`; the scheduler unit sets
`SCHEDULER_ENABLED=true` and runs `backend/scripts/run_scheduler.py`. This keeps
APScheduler out of the web process while preserving the same job definitions,
SQLite run ledger, and process lock. If both units accidentally try to own the
scheduler, the lock file allows only one process to start jobs.
