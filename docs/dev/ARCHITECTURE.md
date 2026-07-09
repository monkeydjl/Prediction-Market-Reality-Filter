# Architecture Overview — Prediction Market Reality Filter v0.3.0

## System Context (C4 Level 1)

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│  Dashboard   │     │  Operator   │     │  UptimeRobot  │
│  (Browser)   │     │  (API Key)   │     │  (Monitor)    │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                     │
       ▼                    ▼                     ▼
┌──────────────────────────────────────────────────────┐
│              PMRF API Server (:8000)                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐ │
│  │ Frontend  │ │ /api/*   │ │ /api/health          │ │
│  │ (Static)  │ │ (JSON)   │ │ /api/events/loop/... │ │
│  └──────────┘ └──────────┘ └──────────────────────┘ │
│  ┌──────────────────────────────────────────────────┐│
│  │              APScheduler (in-process)            ││
│  │  event_discover @07:15 UTC                     ││
│  │  event_auto_resolve @22:30 UTC                 ││
│  └──────────────────────────────────────────────────┘│
└──────────┬───────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────┐
│  External APIs                                       │
│  ┌──────────┐ ┌──────────┐ ┌──────┐ ┌──────────────┐│
│  │DeepSeek  │ │Polymarket│ │GNews │ │Fed/SEC/BLS   ││
│  │(LLM)     │ │Kalshi    │ │(News)│ │(Official)    ││
│  │          │ │Kalshi    │ │      │ │              ││
│  └──────────┘ └──────────┘ └──────┘ └──────────────┘│
└──────────────────────────────────────────────────────┘
```

## Container Diagram (C4 Level 2)

```
┌─────────────────────────────────────────────────────────┐
│  FastAPI Application                                    │
│                                                         │
│  ┌─────────────────────────────────────────────────────┐│
│  │  API Layer (api/)                                   ││
│  │  router.py → /api prefix                            ││
│  │  routes/events.py → 18 endpoints                    ││
│  │  security.py → require_write_key dependency         ││
│  └───────────────────────┬─────────────────────────────┘│
│                          │                               │
│  ┌───────────────────────┴─────────────────────────────┐│
│  │  Service Layer (services/)  36 modules              ││
│  │  ┌─────────────────┐  ┌────────────────────────┐   ││
│  │  │ Event Discovery │  │ Reality Feedback Loop  │   ││
│  │  │ - 2 market src  │  │ - auto_resolve         │   ││
│  │  │ - 5 news src    │  │ - calibration          │   ││
│  │  │ - dedup/cache   │  │ - diagnosis (M2)       │   ││
│  │  └─────────────────┘  │ - trend analysis (M3)  │   ││
│  │                       └────────────────────────┘   ││
│  │  ┌──────────────────────────────────────────────┐  ││
│  │  │ AI Analysis Pipeline                        │  ││
│  │  │ ai_analysis → probability_engine → evidence │  ││
│  │  │ cross_validation (opt-in 2nd model)         │  ││
│  │  └──────────────────────────────────────────────┘  ││
│  └───────────────────────┬─────────────────────────────┘│
│                          │                               │
│  ┌───────────────────────┴─────────────────────────────┐│
│  │  Memory Layer (memory/)                             ││
│  │  event_store (JSON)  │  event_market_link (SQLite)  ││
│  │  event_audit (JSONL) │  prediction_store (SQLite)   ││
│  │  event_cache (JSON)  │  loop_run_store (SQLite)     ││
│  └─────────────────────────────────────────────────────┘│
│                                                         │
│  ┌─────────────────────────────────────────────────────┐│
│  │  Infrastructure (core/ + utils/)                    ││
│  │  config (Settings)  │  scheduler (APScheduler)      ││
│  │  logging (Rotating) │  rate_limit (InMemory)        ││
│  │  file_store (RLock) │  sqlite_db (WAL mode)         ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

## Data Flow: Event Discovery Pipeline

Prediction-market discovery currently includes Polymarket, Kalshi, and the public Limitless adapter by default. Opinion and Predict.fun are live adapter-capable but require `OPINION_API_KEY` and `PREDICT_FUN_API_KEY`; without keys they fail closed and contribute no events. Probable remains planned-only until an official API, indexer, or contract-event interface is verified. The new on-chain adapters do not participate in auto-resolution yet.

Config:

- `LIMITLESS_SOURCE_ENABLED` / `LIMITLESS_API_URL`: public Limitless market discovery.
- `OPINION_API_KEY`: enables Opinion Open API market discovery.
- `PREDICT_FUN_API_KEY`: enables Predict.fun beta API market discovery.

```
News Sources (RSS/GNews/SEC/Fed/BLS)
              │
              ▼
     collect_shared_articles()
              │
              ▼
     Market Sources (Poly/Kalshi) ──┐
     Open Web Extraction (opt-in)           ──┤
              │                                │
              ▼                                │
     _collect_candidate_events() ◄─────────────┘
              │
              ▼
     dedupe_candidates() ──► cap = limit × 3
              │
              ▼
     process_event() × N (Semaphore(LLM_CONCURRENCY))
     ┌─────────────────────────────────────┐
     │ cache lookup (TTL 1h)              │
     │ → _build_filtered_news()            │
     │ → analyze_event() (LLM)             │
     │ → build_event_record()              │
     └─────────────────────────────────────┘
              │
              ▼
     _persist_events()
     ├─ save_events()      → event_store.json
     ├─ record_event()     → event_audit.jsonl
     └─ freeze_prediction() → prediction_store (SQLite)
```

## Data Flow: Reality Feedback Loop

```
Daily 22:30 UTC: auto_resolve_events()
├─ reconcile_predictions()    ← heal crash orphans
├─ fetch_resolved_markets()   ← Poly + Kalshi (parallel)
├─ contract_id match          ← primary: verified link
├─ text_match fallback        ← secondary: fuzzy match
└─ resolve_with_calibration()
   ├─ score_prediction()      ← SQLite first
   ├─ resolve_event()          ← JSON second
   └─ record_outcome()         ← audit log

Opt-in: CALIBRATION_FEEDBACK_ENABLED=true
├─ component_weights()        ← Brier-based fusion
├─ category_shrinkage()       ← base-rate regression
└─ adjust_probability()       ← weighted fusion + shrinkage
```

## Key Design Decisions

See `docs/dev/adr/` for Architecture Decision Records.

## Deployment

- **Single-binary**: FastAPI serves both API (/api/*) and static frontend (/*)
- **Process guard**: `deploy/prediction-market-reality-filter.service` (systemd, Restart=always)
- **Container**: `deploy/Dockerfile` + `deploy/docker-compose.yml`
- **Backup**: `backend/scripts/backup_stores.py` + systemd timer
- **Monitoring**: `/api/health` endpoint + healthcheck systemd timer

## Storage Strategy

| Store | Backend | Purpose |
|-------|---------|---------|
| event_store.json | JSON file | Event records (durable, keyed by event_id) |
| event_audit.jsonl | JSONL file | Append-only probability trajectory |
| event_cache.json | JSON file | TTL compute cache (1h) |
| v2_loop.db | SQLite | Links, predictions, run ledger |
