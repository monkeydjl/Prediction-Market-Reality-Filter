# Operations Runbook

## Required Production Settings

- Set `API_WRITE_KEY` and send it as `X-API-Key` for discovery, analysis,
  manual resolution, auto-resolution, tracking, and link verification.
- Set `CORS_ALLOWED_ORIGINS` to the deployed dashboard origins. Do not use `*`
  when credentials are enabled.
- Keep `RATE_LIMIT_ENABLED=true` unless a trusted reverse proxy provides an
  equivalent limit.
- Keep `LOG_FILE` on persistent storage.

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

Enable the daily backup timer (runs backup_stores.py via systemd):

```bash
sudo systemctl enable prediction-market-reality-filter-backup.timer
sudo systemctl start prediction-market-reality-filter-backup.timer
```

After restoring from an archive, run one auto-resolve pass before resuming
normal unattended operation. The pass calls `reconcile_predictions()` and heals
any temporary JSON/SQLite mismatch captured while a backup was being written.

## Monitoring

Enable the built-in health check timer (pings `/api/health` every 5 min):

```bash
sudo systemctl enable prediction-market-reality-filter-healthcheck.timer
sudo systemctl start prediction-market-reality-filter-healthcheck.timer
```

For external dead-man monitoring, point a service like UptimeRobot or cronitor
at `https://<your-host>/api/health`. The endpoint returns `{"status":"degraded",...}`
when any scheduler job has failed.

## Docker Deployment

```bash
# Build frontend first
cd frontend && npm ci && npm run build && cd ..

# Build and start
docker compose -f deploy/docker-compose.yml up -d --build
```

The container includes a healthcheck that pings `/api/health` every 30 seconds.

## Process Supervision

Use `deploy/prediction-market-reality-filter.service` as the systemd template.
The service restarts automatically after crashes and starts after the network
is online.
