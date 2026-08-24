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
- **If a reverse proxy is in front (the documented deploy), set
  `TRUSTED_PROXY_HEADER=true` and `RATE_LIMIT_TRUSTED_PROXY_HOPS`.** Leaving them
  unset is not the cautious half of the choice: the socket peer is then the proxy
  on every request, so all callers share one bucket and `RATE_LIMIT_MAX_REQUESTS`
  becomes a global cap — one busy client 429s everybody. The backend logs
  `every caller shares one bucket` at WARNING once per process when it sees proxy
  headers while the setting says there is no proxy, so grep startup logs for that
  line after a config change. Detail:
  [Reverse-proxy client IP (E3)](#reverse-proxy-client-ip--the-rate-limit-identity-e3).
- Set `LLM_DAILY_COST_CAP_USD` to a positive number. **`0` means unlimited, not
  disabled** — the guard short-circuits and today's spend is never counted. The
  overlay templates ship `5` (staging) and `25` (production); the backend logs
  `daily LLM spend is UNLIMITED` at WARNING on every boot where the cap is off,
  so grep startup logs for that line after a config change.
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

## Review queue (human review)

Detectors and orchestrators enqueue review candidates into
`review_queue_items` (SQLite, `v2_loop.db`); reviewer actions append to the
INSERT-only `review_queue_audit` table. Two operator paths:

| Path | Use |
|------|-----|
| UI | Nav **人工复核** (`/review-queue`) — filter by status/trigger, read the trigger context, see how long each item has waited, submit an action |
| CLI | `python -m scripts.review_queue_cli list \| sla \| action \| audit` |

| Endpoint | Auth |
|----------|------|
| `GET /api/review-queue?status=pending&trigger=…&limit=…` | open read |
| `GET /api/review-queue/sla` | open read |
| `GET /api/review-queue/{item_id}` | open read |
| `GET /api/review-queue/{item_id}/audit` | open read |
| `POST /api/review-queue/{item_id}/action` | `X-API-Key` (write key) |

The action vocabulary is locked to `confirm` / `override` /
`request_more_evidence` / `mark_bad_source` / `mark_bad_resolution`; anything
else is a 422. Reviewer notes are vocabulary-checked (no
long/short/buy/sell/position/kelly/order) and rejected with 400. Audit rows are
never updated or deleted — a wrong action is corrected by enqueueing a
re-review, not by editing history.

### Review SLA (how fast the queue is being drained)

```bash
python -m scripts.review_queue_cli sla
```

Prints pending depth, the oldest wait, and per-severity / per-trigger counts;
**exits 1 when anything has breached**, so it can be run as a check rather than
read by eye. `--error-hours` / `--warn-hours` override the budgets for one run.

Budgets are reporting-only — nothing escalates, retries, or auto-resolves:

| Setting | Default | Meaning |
|---------|---------|---------|
| `REVIEW_QUEUE_SLA_ERROR_HOURS` | `24` | hours an `ERROR` item may wait |
| `REVIEW_QUEUE_SLA_WARN_HOURS` | `72` | hours a `WARN` item may wait |

`GET /api/review-queue/sla` returns the same aggregate (counts and ages only —
no reasons, no context, no event text), and `/api/health` carries
`counts.pending_reviews`, `counts.breached_reviews` and a `review_queue` block.

On the **人工复核** page the same reading is at the top of the board (depth,
oldest wait, breach count, budgets in force), each pending row shows
`等待 <时长>` instead of a raw timestamp — the timestamp is the tooltip — and a
row past its budget is highlighted. If the SLA line is missing, `/sla` failed;
the list itself is unaffected and still loads.

Four things to know before reading the numbers:

- **`counts.pending_links` is a different store.** It counts
  `event_market_link_store`; the review backlog is `pending_reviews`. Before this
  existed, a review queue of any depth was invisible from `/api/health` while a
  "pending" figure was already on screen.
- **The list is oldest-first, and that is what makes `limit` safe.** The route
  truncates with `items[:limit]`, so whatever sorts last is what gets dropped.
  Urgency is a field (`severity_rank`, ERROR above WARN) for a client to sort on
  — sorting by it server-side would put a fresh ERROR above a week-old WARN and
  hide the WARN again.
- **`created_at` is the first-enqueue time and is never rewritten.** Detectors
  re-run on every overlay build and refresh a pending row in place; refreshing
  the timestamp too would reset the clock on every scan and no item could ever
  age.
- **A severity with no budget can never breach**, so it is reported under
  `unknown_severity` instead of counted as healthy. New rows cannot get there
  (`enqueue_item` rejects anything outside `WARN`/`ERROR`), but rows written
  before that gate existed can.

An empty `outcome_prediction_mismatch` history predates 2026-08-24 rather than
meaning nothing ever mismatched: that detector — the queue's only `ERROR`-severity
trigger — read a field the event record has never carried and compared a dict
against a string, so it could not fire. Depth measured before then is a WARN-only
reading.

## Event title translation (repair path)

Titles are normally translated during analysis (`AUTO_TRANSLATE_TITLES=true`).
When the LLM was unavailable an event keeps its English title, and the detail
page shows **标题翻译** with 翻译标题 / 重新翻译.

| Endpoint | Auth | Use |
|----------|------|-----|
| `POST /api/events/{event_id}/translate?force=false` | `X-API-Key` | one event; skips work if a Chinese title exists |
| `POST /api/events/{event_id}/translate?force=true` | `X-API-Key` | re-translate, overwriting the current Chinese title |
| `POST /api/events/translate-all?force=…` | `X-API-Key` | backfill sweep (no UI entry — curl only) |

A failed translation is reported as `Translation unavailable, kept original`
rather than writing the English title in as if it were Chinese.

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
| `pmrf_event_store_bytes` | Size of `event_store.json` on disk (E1) |
| `pmrf_event_store_records` | Record count in `event_store.json` (E1) |

JSON companions (same data, operator-friendly):

- `GET /api/quality-metrics/summary`
- `GET /api/quality-metrics/drift` — always computes; dispatch is separate
- `GET /api/quality-metrics/alerts`
- `GET /api/quality-metrics/report`

### Event store size — when to stop using JSON (E1)

`event_store.json` is a single JSON file that **every mutating call rewrites in
full**, under an exclusive cross-process lock. So its size *is* the write cost.
Watch it in two places:

- Prometheus: `pmrf_event_store_bytes` and `pmrf_event_store_records`
- JSON: `storage.event_store_bytes` / `storage.event_store_records` on
  `GET /api/health` and `GET /api/events/loop/status`

Reference measurement, 2026-08-24, on a 3.455 MB / 235-record store:

| Operation | Whole-file passes | Store I/O |
|-----------|-------------------|-----------|
| one full read (`read` + parse + normalize) | 1 read | ~64 ms |
| one atomic rewrite (serialize + `os.replace`) | 1 write | ~237 ms |
| one read-modify-write (`set_tracking`, `resolve_event`) | 1 + 1 | ~301 ms |
| `GET /events/` (page + total) | 1 read | ~64 ms |
| `POST /events/resolve-expired` (any batch size) | 1 + 1 | ~301 ms |

Cost scales linearly with the file, so at ~10 MB expect a rewrite near 700 ms
**with every event read blocked behind it**. Two things to know before reacting:

- **A TTL / archive policy will not help much.** By lifecycle, ~31% of the file
  is resolved calibration samples (they must stay — they are the Brier
  aggregate), ~31% is active, and only **~3%** is archived-and-never-resolved,
  i.e. the only slice a TTL may evict.
- **The size is concentrated in one field.** `evidence_items` is ~20% of the
  file on its own, then `legacy_analysis` ~12%, `news_filter` ~9%,
  `sentiment_profile` ~6%. Trimming or externalising `evidence_items` buys far
  more than archiving, and buys it without touching calibration.

Re-run the census against a live store with:

```bash
python -c "import json,os,collections; from app.core.config import settings; p=os.path.abspath(settings.EVENT_STORE_FILE); s=json.load(open(p,encoding='utf-8')); b=collections.Counter(); [b.update({k: len(json.dumps(v,ensure_ascii=False))}) for e in s.values() for k,v in (e.get('record') or {}).items()]; print(os.path.getsize(p), len(s)); print(b.most_common(8))"
```

### Dangling event references — the missing foreign key (E2)

Events live in `event_store.json`; the rows about them live in SQLite. **No
foreign key can span that boundary**, so nothing stops a row from outliving the
event it names. `DELETE /events/{event_id}` removes only the JSON record, and
`loop_db_maintenance` is WAL truncation plus an integrity check — it prunes
nothing — so stranded rows accumulate for the life of the database.

Read the count in one place:

- JSON: `counts.dangling_refs` (total) and `counts.dangling_by_table` (where) on
  `GET /api/health` and `GET /api/events/loop/status`

`counts.dangling_predictions` / `counts.dangling_links` are still published for
older consumers, but they cover **two** of the five watched tables. Before
2026-08-24 they were the *only* reading, and the single dangling reference in the
live store sat in `simulated_trades`, so the dashboard badge read 0. **Do not
build an alert on those two keys.**

Watched: `predictions`, `event_market_links`, `simulated_trades`,
`review_queue_items`, `decision_timeline`. Exempt with a written reason:
`domain_reliability_ledger` — its `event_id` is the dedup key of a credit already
earned (`PRIMARY KEY (event_id, domain, category)`), read by domain and never by
event, so it is not a pointer that has to resolve. The list is declared in
`app/memory/event_ref_census.py` and `tests/test_event_ref_census.py` asserts it
exactly partitions every table in `app/` declaring an `event_id` column — adding
a table with that column and no entry in either list turns the suite red.

**Before deleting an event**, note what the delete will strand. The response
says so:

```bash
curl -s -X DELETE -H "X-API-Key: $API_WRITE_KEY" \
  "$BASE/api/events/<event_id>" | python -m json.tool
# {"event_id": "...", "message": "Deleted",
#  "stranded_refs": {"predictions": 1, "simulated_trades": 1},
#  "stranded_total": 2}
```

The rows are **deliberately kept**. A scored prediction is a calibration sample
and cascading the delete would silently shrink the only measurement of whether
the engine works. Purging is an operator decision — and note the consequence of
keeping: `calibration_summary` does not check that the event still exists, so a
stranded scored prediction stays in the Brier aggregate with nothing to trace it
to. If you decide to purge, do it explicitly and record which event ids.

Re-run the census against a live store with:

```bash
python -c "import json,os; from app.core.config import settings; from app.memory.event_ref_census import dangling_counts; s=json.load(open(os.path.abspath(settings.EVENT_STORE_FILE),encoding='utf-8')); print(dangling_counts(set(s)))"
```

### Reverse-proxy client IP — the rate-limit identity (E3)

The in-process rate limiter buckets by `client:method:route`. What `client`
resolves to is a deployment question, and **both settings of
`TRUSTED_PROXY_HEADER` were wrong** before this was fixed. Measured against the
middleware at `RATE_LIMIT_MAX_REQUESTS=2`:

| Setting | Traffic | Result |
| --- | --- | --- |
| `false` (default), proxy in front | 4 different real clients | clients 3 and 4 got **429** — one shared bucket |
| `true`, nginx from `deploy/` | 1 attacker rotating `X-Forwarded-For` | **8/8 requests allowed** — no limiting at all |

The second one is why the client is now read from the **right** of
`X-Forwarded-For`. The header grows left to right — each proxy appends the
address it accepted the connection from — so the trailing entries are the ones
our infrastructure wrote and the leading entry is whatever the caller sent.
`deploy/nginx.conf.example` forwards `$proxy_add_x_forwarded_for`, which
**appends** rather than replaces, so a caller sending `X-Forwarded-For: 10.0.0.1`
makes the app see `10.0.0.1, <real peer>`. Trusting the leftmost entry handed the
rate-limit key to the caller.

`RATE_LIMIT_TRUSTED_PROXY_HOPS` declares how many trailing entries are ours:

| Topology | Header the app sees | Hops |
| --- | --- | --- |
| nginx only (`$proxy_add_x_forwarded_for`, appends) | `[spoof, ]<client>` | `1` |
| Caddy only (`header_up`, replaces) | `<client>` | `1` |
| CDN (Cloudflare) in front of nginx | `<client>, <cdn edge>` | `2` |

Set it wrong and you do **not** get a spoofable key — a chain shorter than the
declared hop count falls back to `X-Real-IP` (both shipped proxies *replace* that
header, so it is not caller-controlled) and then to the socket peer. It never
falls back to the leftmost entry, because a short chain is exactly the shape a
spoofing caller produces.

Two misconfigurations are logged at WARNING, **once per process** (the setting
only changes on restart, and a per-request log would be its own denial of
service):

- `every caller shares one bucket` — proxy headers arrived while
  `TRUSTED_PROXY_HEADER=false`. You are in row 1 of the first table.
- `not behind the trusted proxy` — `TRUSTED_PROXY_HEADER=true` but no proxy
  headers arrived, so every caller is keyed to the socket peer anyway.

Note what this does *not* fix: the counter is still per-process. That is sound
only because the app is deliberately single-process (`uvicorn` with no
`--workers` in both `deploy/Dockerfile` and `backend/run.py`, which
`optimization_task_store.fail_interrupted_tasks` and the in-process scheduler
already depend on). Adding `--workers N` multiplies every limit by `N` with no
warning, and would break more than rate limiting.

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

### Model eval routine — pinned set + release gate (Q1)

`scripts/model_eval_lab` grades resolved events. Two flags make it a *routine*
rather than a one-off, and both are opt-in:

```bash
cd backend
# 1. Mint a pinned set ONCE. Commit the manifest; it is the baseline every
#    later report is measured against.
PYTHONPATH=. python -m scripts.model_eval_lab \
  --write-eval-set data/eval/model_eval_core.json --size 50

# 2. Grade exactly that set, and block on the gate.
PYTHONPATH=. python -m scripts.model_eval_lab \
  --eval-set data/eval/model_eval_core.json --gate
```

Exit codes: `0` pass, `1` gate failed, `2` bad arguments or an unusable
manifest. Thresholds come from `MODEL_EVAL_GATE_*` in `.env` (see
`.env.example`); `MODEL_EVAL_GATE_REQUIRE_EVAL_SET` defaults to `false`, so an
unpinned run is still gradeable — just not certifiable as a fixed set.

Three things that bite operators:

- **A missing measurement FAILS the gate.** This is deliberately the opposite of
  the `QUALITY_ALERT_*` rules above, where a `None` metric does not page anyone.
  You do not wake someone because a slice has no data; you also do not certify a
  model because there is no evidence against it.
- **Each metric is held to `min_samples` on its own denominator**, not on the
  slice size, so a check can fail while the value printed beside it looks fine —
  read the `metric_n` column. On the live store a 30-event set carried 20
  gradeable Brier/ECE events but only 15 directional ones.
- **A re-graded event is reported, never dropped.** `drifted_event_ids` means the
  underlying record changed after it was pinned, so the score stopped being
  comparable; bump `--set-revision` and re-mint deliberately rather than
  ignoring it. Note that `digest` is a tamper seal covering `created_at`, **not**
  a membership identity — two mints of the same events differ. Membership
  identity is `name` + `revision`.

### Replay harness routine — provenance + stable sample (Q2)

`scripts/replay_decision_pipeline` re-runs the Phase 1–5 overlays over frozen
event records and writes a four-file report. It reads the event store and
`prediction_store`; it writes **nothing** back, so it is safe to run against
production data.

```bash
cd backend
# Whole store, all_off -> current, with the per-phase marginal loop.
PYTHONPATH=. python -m scripts.replay_decision_pipeline \
  --output-dir docs/reports/replay/2026-w34

# A repeatable weekly subset. Same seed + same ids = same events, whatever
# the store's size or order. Change the seed only when you mean to.
PYTHONPATH=. python -m scripts.replay_decision_pipeline \
  --sample-size 50 --sample-seed 2026-w34 --skip-marginal \
  --output-dir docs/reports/replay/2026-w34-sample
```

Output: `report.md`, `report.html`, `metrics.json`, `cases.jsonl`. Exit codes:
`0` report written, `1` no records to replay, `2` bad arguments.

**Before comparing two reports, diff their `run` blocks.** Every report carries
one, and it is what makes two runs comparable at all:

| Field | Why it decides whether a comparison is valid |
|---|---|
| `compare.a` / `compare.b` | Two reports built from different pairs measure different things. |
| `population` vs `records_replayed` | `records_replayed: 50` alone reads as "the store holds 50". |
| `sample.seed` / `sample.strategy` | Different seed = different events. `null` means the whole population — the key is always present, so its absence means the file predates Q2. |
| `marginal` | `false` means `--skip-marginal`; the Per-Phase section is empty by choice, not because no phase contributed. |
| `missing_event_ids` | `--event-ids` you asked for that the store does not have. Replaying 48 of 50 silently is a report about a different population. |
| `duplicate_event_ids` | Kept once each. Left in, a repeated id would be counted twice by `add_pair` and inflate every rate. |
| `generated_at` | One instant for all three files. Two archived reports with the same stamp are the same run. |

Four things that bite operators:

- **`--sample-size` selects by hash rank, not by position.** Before Q2 it was
  `random.seed(42)` + `random.sample`, which picks *positions*: measured on the
  live 235-event store at size 8, merely reversing the store's order left an
  overlap of **0/8** — and `event_store.json` is rewritten whole. Adding events
  now displaces an incumbent at most one-for-one, and a widened `--sample-size`
  is a superset of the narrower run.
- **Read the two resolved counts as the different numbers they are.** Summary's
  "Resolved (with outcome)" is Brier's denominator; Direction Accuracy uses
  "Direction-callable samples", which excludes WAIT/AVOID because an abstention
  has no direction to be right about. A live 8-event run showed 2 resolved and 0
  direction-callable, and `direction_correct_delta` was `null` rather than a
  fake `0.0`.
- **The per-phase loop runs by default.** There is no `--marginal` flag; passing
  one gets `unrecognized arguments`. `--skip-marginal` is the fast path — the
  loop replays every record once per phase.
- **`--compare` takes exactly `current`, `all_off`, `llm_degraded`.** A typo now
  exits `2` with the valid names, not `1` with a traceback.

`scripts/analyze_feature_flag_impact` samples the same way and takes the same
`--sample-seed`; every JSON it writes carries a `sample` block. Use the same
seed in both when you intend the two reports to describe the same events.

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

   Local verification (2026-07-23): after nested-team parse fix,
   `synced=2814` (~8s). Same day list non-empty; `days_ahead=7` ~100+;
   `days_ahead=45` ~600+. No vendor API key required.

### NHL (Phase 5 / api-web.nhle.com)

1. `PHASE5_NHL_ENABLED=true` in `backend/.env` (no API key — official free web API).
2. Restart API so `nhl-` registers (`GET /api/betting/status` → prefixes).
3. Sync (write key; one request per club for preferred season, ~30–40s polite 1 req/s):
   ```bash
   curl -s -X POST \
     -H "X-API-Key: $API_WRITE_KEY" \
     "$BASE/api/predictions/schedule/sync?competition=nhl"
   ```
4. List:
   ```bash
   curl -s "$BASE/api/predictions/matches?competition=nhl&days_ahead=45"
   curl -s "$BASE/api/predictions/matches?competition=nhl&days_ahead=60"
   ```

   Season key is `YYYYYYYY` (e.g. `20262027` for 2026-27). Prefer current
   calendar preferred key, then fall back one season if club schedules are
   empty. Bulk path is `/v1/club-schedule-season/{abbrev}/{season}` (not
   `/v1/schedule/{season}` — that path 404s). Team names come from
   `placeName` + `commonName`; scores from `homeTeam.score` /
   `awayTeam.score`; finished states include `OFF` / `FINAL`.

   Local verification (2026-07-23): `synced=1409` (~2.5 min with SSL
   retries) for **20262027**. Preseason openers from 2026-09-19 through
   regular season Apr 2027. Mid-July: `days_ahead=45` correctly **0**
   (openers ~58d out); `days_ahead=60` ~19 preseason rows.

### Phase 9 historical ingest + Elo seed (NBA / MLB / NHL)

Prerequisite: `PHASE9_ACCURACY_SPRINT_ENABLED=true` and write key. Schedule
sync alone fills `kernel_match_fixtures` (often without results/Elo);
historical ingest + backfill/seed fill `kernel_match_results` and
`kernel_elo_ratings` for backtests.

1. **Ingest finished seasons** (API or Python):
   ```bash
   # NHL example — two completed seasons (label "YYYY-YY"; client expands to YYYYYYYY)
   curl -s -X POST -H "Content-Type: application/json" \
     -H "X-API-Key: $API_WRITE_KEY" \
     -d '{"sport":"nhl","seasons":["2023-24","2024-25"]}' \
     "$BASE/api/sport-optimization/ingest"
   ```
   ```bash
   # From backend/
   python -c "
   import asyncio
   from app.kernel.kernel_db import init_kernel_db
   from app.services.historical_data_ingestor import HistoricalDataIngestor
   init_kernel_db()
   ing = HistoricalDataIngestor()
   async def main():
       for season in ['2023-24', '2024-25']:
           print(await ing.ingest_season('nhl', season))
   asyncio.run(main())
   "
   ```
   Season labels: NBA/NHL `"2023-24"`; MLB calendar year `"2024"`. NHL API
   season key is eight digits (`20232024`); ingestor converts from `"2023-24"`.

2. **Backfill results + seed Elo** (idempotent; also covers adapter-only scores):
   ```bash
   curl -s -X POST -H "Content-Type: application/json" \
     -H "X-API-Key: $API_WRITE_KEY" \
     -d '{"sport":"nhl","backfill":true,"seed_elo":true}' \
     "$BASE/api/sport-optimization/backfill-seed"
   ```
   ```bash
   python scripts/seed_sport_elo.py --sport nhl
   # or: --sport all | --backfill-only | --seed-only
   ```

3. **Verify counts** (local SQLite / kernel DB, 2026-07-24):

   | Sport | Historical seasons | Scored/results | Elo teams |
   |-------|--------------------|----------------|-----------|
   | NBA | 2023-24, 2024-25 (+ 2025-26 sync) | **3962** | **30** |
   | MLB | 2024, 2025 (+ 2026 sync) competitive only | **6803** | **30** |
   | NHL | 2023-24, 2024-25 (+ 20262027 sync) | **3014** | **34** |

   Example MLB seasons labels are calendar years (`"2024"`); NBA/NHL use
   `"YYYY-YY"`. Top Elo after multi-season replay should look sensible.

Notes:
- Upcoming-only seasons (e.g. NHL `20262027` mid-summer) contribute
  fixtures with **no scores** — Elo seed uses finished games only.
- Re-run ingest is upsert/idempotent; seed replaces Elo rows per competition.
- MLB ingest/sync **drops** spring training / All-Star / exhibition
  (`gameType` not in R/D/L/F/W/P). `Oakland Athletics` is stored as
  `Athletics` so Elo spans the franchise rename.

### Phase 9 Optuna parameter optimization

Prerequisite: historical fixtures+results loaded (see section above).
`PHASE9_ACCURACY_SPRINT_ENABLED=true` for HTTP API; CLI works without it.

1. **Offline CLI** (from `backend/`):
   ```bash
   python scripts/run_phase9_optimize.py --sport nba --n-trials 80
   python scripts/run_phase9_optimize.py --sport all --n-trials 80
   ```
   Chronological 80/20 split; Optuna TPE maximizes
   `0.5*accuracy + 0.3*(1-brier) + 0.2*(1-mae)`. Best set is upserted as
   `status=candidate` in `kernel_optimized_params` (same sport/competition
   re-run updates the existing candidate row in place).
   Loader fills **as-of rest/form** from fixtures (not flat 2.0/0.5).
   Unknown rest is `None` (factor unavailable). Re-run Optuna after this
   change before trusting new weights; do not auto-apply.

2. **HTTP** (write key):
   ```bash
   curl -s -X POST -H "Content-Type: application/json" \
     -H "X-API-Key: $API_WRITE_KEY" \
     -d '{"sport":"nba","n_trials":80}' \
     "$BASE/api/sport-optimization/run"
   # poll: GET /api/sport-optimization/status/{task_id}
   # list: GET /api/sport-optimization/params
   ```

3. **Local result snapshot**:

   Flat rest/form (first apply 2026-07-24): NBA id=4 / NHL id=3 / MLB id=2.

   **As-of rest/form re-tune (80 trials, applied 2026-07-24)**:

   | Sport | Train/Test | Best acc | Best score | Params id | Status |
   |-------|------------|----------|------------|-----------|--------|
   | NBA | 3172 / 793 | **0.702** | 0.695 | **5** | **applied** |
   | MLB | 5442 / 1361 | **0.542** | 0.598 | **6** | **applied** |
   | NHL | 2411 / 603 | **0.624** | 0.645 | **7** | **applied** |

4. **Apply** (manual; does not auto-deploy):
   ```bash
   # CLI (same process as store.apply)
   # HTTP (write key + Phase 9 flag):
   curl -s -X POST -H "X-API-Key: $API_WRITE_KEY" \
     "$BASE/api/sport-optimization/apply/5"
   ```
   - Marks row `status=applied`, archives previous applied for that sport
   - Updates `KernelFactor` via `FactorRegistry.update_weight(..., source="optimized")`
   - **Also** drives runtime Elo HFA/K: `resolve_elo_params(sport)` overlays
     applied `elo_params` onto settings; engines use applied HFA; seed uses
     applied K/carry/initial
   - Apply path calls `seed_elo_ratings(sport=...)` and `reset_kernel_singleton()`
     so weights + Elo take effect without a manual restart (restart still fine)
   - NBA playoff HFA: without applied → `NBA_ELO_HFA_PLAYOFF`; with applied →
     single applied `hfa` for regular and playoff (Optuna parity)
   - Manual re-seed (if ratings lag apply):  
     `python scripts/seed_sport_elo.py --sport all --seed-only`
   - Applied set (as-of rest/form, 2026-07-24): NBA **5**, MLB **6**, NHL **7**
   - Verified: 14 `KernelFactor` rows `source=optimized`; resolve HFA/K matches
     applied 5/6/7; Elo re-seeded with applied K/carry
   - Holdout re-eval (chronological 80/20, same weights; applied Elo vs settings Elo):

     | Sport | Applied acc | Settings Elo acc | Δacc | Applied score |
     |-------|-------------|------------------|------|---------------|
     | NBA | **0.702** | 0.672 | **+3.0pp** | 0.695 |
     | MLB | **0.542** | 0.531 | **+1.2pp** | 0.598 |
     | NHL | **0.624** | 0.590 | **+3.3pp** | 0.645 |

   - CLI: `python scripts/eval_applied_params.py`
   - Those deltas are **historical holdout only**. Online readiness is a
     separate question — a large `kernel_match_results` count says nothing
     about how many *live* predictions have settled.
   - Live evidence (read-only, never flips a flag or fits calibration):
     ```bash
     python scripts/report_phase9_live_evidence.py
     ```
     Same data behind `GET /api/sport-optimization/live-evidence` (needs
     `PHASE9_ACCURACY_SPRINT_ENABLED=true`; no write key, it only reads), and
     rendered as the "在线证据" panel on `/sports/optimization` under the
     candidate metrics. Groups by sport/competition/engine because calibration
     is group-scoped. The panel never claims readiness on a load failure.
   - P1-A5 learning loop still **OFF** (as-of 2026-08-17): 11 kernel predictions
     across 10 groups, 1 settled (`football/world_cup` / `elo_odds`, 9 short).
     Need ≥ `MIN_SAMPLES_FOR_CALIBRATION=10` joined samples **per group**, so
     `learning_ready=false` until one group fills up.

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
