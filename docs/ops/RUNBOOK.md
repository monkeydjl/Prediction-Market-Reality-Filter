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
