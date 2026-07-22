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
   EPL_DATA_ENABLED=true              # English Premier League adapter
   PHASE2_LEAGUES_ENABLED=true        # La Liga / Bundesliga / Serie A / Ligue 1 / UCL
   # FOOTBALL_DATA_API_KEY=...        # Football-Data.org for EU leagues
   # BALLDONTLIE_API_KEY=...          # NBA (optional)
   ```
2. Restart the API so `_get_kernel()` rebuilds MultiAdapter registrations.
3. Sync schedules (operator/write path or adapter-level `sync_schedule` via
   existing scheduler / scripts — see Phase 2 plans). Empty `match` count on
   landings usually means flags OFF or no ingest yet, not a broken filter.
4. Operator schedule sync (write key required — never commit real keys):
   ```bash
   curl -s -X POST \
     -H "X-API-Key: $API_WRITE_KEY" \
     "$BASE/api/predictions/schedule/sync?competition=epl"
   ```
   Optional: `sport=football`. Empty `synced` usually means flag OFF, missing
   Football-Data key, or upstream empty — not a broken short-circuit.
5. Smoke:
   ```bash
   curl -s "$BASE/api/betting/catalog" | jq '.flags,.competitions[]|{id,adapter_likely}'
   curl -s "$BASE/api/betting/status" | jq '{kernel_ready,registered_prefixes,flags}'
   curl -s "$BASE/api/predictions/matches?competition=epl"
   ```
   `GET /api/betting/status` is read-only (no write key). It lists MultiAdapter
   prefixes currently registered when Kernel is ON.

World Cup remains on `/api/world-cup/*` (not MultiAdapter football prefixes for
the thematic UI). Esports stays `coming_soon` — see `docs/dev/ESPORTS_BOUNDARY.md`
and ADR-004.

UI: competition landings show a **同步赛程** button when the browser session has
an operator write key (`sessionStorage` via operator credentials UI).

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
