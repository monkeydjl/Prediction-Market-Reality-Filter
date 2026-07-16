# Phase 11: Kalshi Sports Market Integration — Design Spec

**Date:** 2026-07-16
**Status:** Approved (autonomous design per standing authorization)
**Predecessor:** Phase 10 (WebSocket Real-Time Price Push)

## 1. Goal

Integrate Kalshi as a new sports market data source alongside the existing Polymarket integration. Kalshi markets are discovered, linked to matches via the existing three-layer matching engine, and their price snapshots feed into the existing EdgeDetector → Recommendation → Settlement pipeline. The integration is zero-invasion: no changes to EdgeDetector, Settlement, Recommendation, engines, kernel, or domain.

## 2. Background

- Phase 7 built a sport market bridge with Polymarket as the primary source and The Odds API as a traditional odds source.
- The `KernelSportMarketLink` table has a `source` field (`"polymarket"` | `"odds_api"`) that makes the system source-agnostic — `EdgeDetectorService` does liquidity-weighted aggregation across all sources.
- A generic `kalshi_event_source.py` already exists for non-sports Kalshi events, using `https://api.elections.kalshi.com/trade-api/v2/events` (no auth needed for read-only).
- Kalshi sports markets use series tickers like `KXNBAGAME-*`, `KXMLB-*`, `KXNHL-*`, `KXSOCCER-*` and have team/date info in the ticker itself.
- Kalshi prices are already 0-1 dollar denominated (directly usable as `implied_prob`).

## 3. Non-Goals

- Trading or order placement on Kalshi — this is a read-only data source.
- Kalshi user authentication (RSA keypair) — not needed for public market data.
- Replacing Polymarket — Kalshi runs alongside Polymarket as an additional source.
- Modifying the existing `kalshi_event_source.py` (generic events) — a new sports-specific source is created in parallel.
- Multi-leg markets (championships, futures) — those are Phase 12 (D). Phase 11 handles single-leg binary markets only.

## 4. Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Scheduler (_job_discover_sport_markets)                  │
│  ├─ Polymarket source (existing)         ──┐             │
│  └─ Kalshi sports source (NEW)           ──┤             │
│                                            ▼             │
│  SportMarketBridgeService.link_kalshi_market              │
│  ├─ Layer 1: Rule match (≥0.9) → auto-verify             │
│  ├─ Layer 2: LLM match (≥0.85) → auto-verify             │
│  └─ Layer 3: Manual gate (0.6-0.85) → pending            │
│                                            │             │
│  KernelSportMarketLink (source="kalshi")  │             │
│                                            ▼             │
│  MarketSnapshotStore ← _job_capture_market_snapshots     │
│  (captures Kalshi prices every 1 min, same as Polymarket) │
│                                            │             │
│  EdgeDetectorService (source-agnostic)    │             │
│  → liquidity-weighted aggregation across all sources     │
│  → adjusted_edge computation (unchanged)                  │
│  → SportRecommendationService (unchanged)                │
│  → MarketSettlementService (unchanged)                   │
└──────────────────────────────────────────────────────────┘
```

**Data flow:**
1. Scheduler job fetches Kalshi sports markets via `kalshi_sports_source.fetch_kalshi_sport_markets()`.
2. Each candidate is passed to `SportMarketBridgeService.link_kalshi_market()` which runs the three-layer matching engine.
3. Matched links are stored in `KernelSportMarketLink` with `source="kalshi"`.
4. The existing `_job_capture_market_snapshots` job captures prices for all verified links (including Kalshi) every 1 minute.
5. `EdgeDetectorService` aggregates across all sources (Polymarket + Kalshi + Odds API) using liquidity-weighted averaging — no changes needed.

## 5. Components

### 5.1 Kalshi Sports Source (`backend/app/services/kalshi_sports_source.py`)

New file, parallel to `polymarket_sports_source.py`. Fetches sports markets from Kalshi and filters them using the existing `sport_market_detector`.

```python
KALSHI_SPORTS_SERIES_PREFIXES = {
    "nba": "KXNBAGAME",
    "mlb": "KXMLBGAME",
    "nhl": "KXNHLGAME",
    # Football leagues
    "epl": "KXSOCCEREPL",
    "ucl": "KXSOCCERUCL",
    # Add more as Kalshi expands coverage
}

async def fetch_kalshi_sport_markets(limit: int = 100) -> list[dict]:
    """Fetch sports markets from Kalshi, filtered to single-leg binary events.

    Returns list of dicts with structure compatible with SportMarketBridgeService:
    {
        "contract_id": str,        # Kalshi market ticker
        "question": str,           # Market title
        "price": float,            # YES price (0-1)
        "no_price": float,         # NO price (0-1)
        "liquidity": float,        # liquidity_dollars
        "volume": float,           # volume_fp
        "source": "kalshi",
        "detected_sport": str,     # from sport_market_detector
        "detected_competition": str,
        "detected_teams": list[str],
        "detected_date": str | None,
    }
    """
```

**Design decisions:**
- Uses `KALSHI_API_URL` from config (already exists, defaults to `https://api.elections.kalshi.com/trade-api/v2/events`).
- Filters by series ticker prefixes to get only sports markets.
- Only includes single-leg binary events (`len(markets) == 1` per event) — multi-leg is Phase 12.
- Price priority: `last_price_dollars` > midpoint of `(yes_bid + yes_ask) / 2` > 0.5 default.
- 1 req/s polite rate limit (matching Phase 5 MLB/NHL convention).
- Uses `httpx.AsyncClient` with 30s timeout, same as existing Kalshi event source.
- Uses `sport_market_detector.detect_sport_market(source="kalshi")` to filter and extract sport/competition/teams/date.

### 5.2 Bridge Service Extension (`backend/app/kernel/sport_market_bridge_service.py`)

Add `link_kalshi_market` method, parallel to `link_polymarket_market`:

```python
async def link_kalshi_market(self, candidate: dict) -> dict:
    """Link a Kalshi sports market to a match via the three-layer matching engine.

    Same flow as link_polymarket_market, but:
    - source = "kalshi"
    - contract_id = candidate["contract_id"] (Kalshi ticker)
    - price parsing uses Kalshi's last_price_dollars
    """
```

**Implementation:**
- Reuses the existing `_rule_match` and `_llm_match` methods (no changes to matching logic).
- The only difference from `link_polymarket_market` is the `source` field value and the price field names.
- Stores the link via `SportMarketLinkStore.upsert_link` with `source="kalshi"`.

### 5.3 Scheduler Extension (`backend/app/core/scheduler.py`)

Extend `_job_discover_sport_markets` to also fetch from Kalshi when enabled:

```python
async def _job_discover_sport_markets():
    # ... existing Polymarket discovery ...

    # Kalshi discovery (NEW, additive)
    if settings.PHASE11_KALSHI_SPORTS_ENABLED:
        try:
            from app.services.kalshi_sports_source import fetch_kalshi_sport_markets
            candidates = await fetch_kalshi_sport_markets(limit=100)
            for candidate in candidates:
                try:
                    await bridge.link_kalshi_market(candidate)
                except Exception:
                    logger.warning("Failed to link Kalshi market", exc_info=True)
        except Exception:
            logger.warning("Kalshi sports discovery failed", exc_info=True)
```

**Constraint:** The Kalshi discovery is wrapped in `if settings.PHASE11_KALSHI_SPORTS_ENABLED:` + `try/except`. It cannot break the existing Polymarket discovery.

### 5.4 Price Capture Extension (`backend/app/kernel/sport_market_bridge_service.py`)

The existing `fetch_current_price` method in `SportMarketBridgeService` handles Polymarket prices. Add a Kalshi price fetcher:

```python
async def _fetch_kalshi_price(self, contract_id: str) -> dict:
    """Fetch current price for a Kalshi market by ticker.

    Returns: {"implied_prob": float, "price": float, "liquidity": float, "volume": float}
    """
    # GET https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}
    # Parse last_price_dollars, yes_bid, yes_ask, liquidity_dollars, volume_fp
```

The existing `capture_snapshots` method needs to dispatch to the right price fetcher based on `link.source`:
- `source == "polymarket"` → existing `_fetch_latest_price`
- `source == "kalshi"` → new `_fetch_kalshi_price`
- `source == "odds_api"` → existing odds handling

**This is a modification to `capture_snapshots`** — but it's additive (a new `if` branch for `source == "kalshi"`), not a change to existing behavior.

## 6. Data Model

**No new database tables.** Phase 11 reuses the existing `kernel_sport_market_links` table with `source = "kalshi"`. The `source` field is a string column that already supports arbitrary values.

**No schema modifications.** The existing `KernelSportMarketLink.source` field accepts `"kalshi"` without any changes.

## 7. Config Changes (`backend/app/core/config.py`)

Add before `settings = Settings()`:

```python
# === Phase 11 — Kalshi Sports Market Integration ===
PHASE11_KALSHI_SPORTS_ENABLED: bool = _env_bool("PHASE11_KALSHI_SPORTS_ENABLED", "false")
KALSHI_SPORTS_FETCH_INTERVAL_SECONDS: int = int(os.getenv("KALSHI_SPORTS_FETCH_INTERVAL_SECONDS", "600"))
KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS: float = float(os.getenv("KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS", "1.0"))
```

**Note:** `KALSHI_API_URL` already exists in config (line 373-376, defaults to `https://api.elections.kalshi.com/trade-api/v2/events`). Phase 11 reuses this setting.

**Defaults:** All OFF. When `PHASE11_KALSHI_SPORTS_ENABLED=false`, the scheduler skips Kalshi discovery, and the system behaves exactly as before.

## 8. API Endpoints

**No new API endpoints.** The existing `/api/sport-markets/links` endpoint already supports filtering by `source` parameter. Clients can query `?source=kalshi` to see Kalshi links.

The existing `/api/sport-markets/links/{match_id}/latest` endpoint already returns the latest snapshot for any source (fail-closed on verified links only).

## 9. Testing Strategy

### Backend tests (pytest)
1. `backend/tests/test_kalshi_sports_source.py` (5 tests):
   - `fetch_kalshi_sport_markets` returns empty list on API failure (fail-closed)
   - `fetch_kalshi_sport_markets` filters to single-leg events only
   - `fetch_kalshi_sport_markets` parses last_price_dollars as implied_prob
   - `fetch_kalshi_sport_markets` falls back to bid/ask midpoint when last_price is 0
   - `fetch_kalshi_sport_markets` includes source="kalshi" in output

2. `backend/tests/test_kalshi_bridge_integration.py` (4 tests):
   - `link_kalshi_market` stores link with source="kalshi"
   - `link_kalshi_market` auto-verifies on rule match (confidence ≥ 0.9)
   - `link_kalshi_market` sends to pending on LLM match (0.6-0.85)
   - `_fetch_kalshi_price` parses Kalshi market response correctly

3. `backend/tests/test_kalshi_scheduler.py` (3 tests):
   - Scheduler discovers Kalshi markets when enabled
   - Scheduler skips Kalshi when disabled
   - Kalshi discovery failure doesn't break Polymarket discovery

**Total: 12 tests**

### TDD approach
- RED → GREEN → COMMIT per component.

## 10. Phase Boundaries

### Zero-invasion (must NOT modify):
- `backend/app/kernel/edge_detector_service.py`
- `backend/app/kernel/sport_recommendation_service.py`
- `backend/app/kernel/market_settlement_service.py`
- `backend/app/kernel/prediction_kernel.py`
- `backend/app/kernel/domain.py`
- `backend/app/kernel/learning_service.py`
- `backend/app/sports/*/engines/*.py`
- `backend/app/services/kalshi_event_source.py` (existing generic Kalshi source)
- `backend/app/services/polymarket_sports_source.py`
- `backend/app/services/polymarket_service.py`
- `backend/app/services/odds_api_service.py`
- `backend/app/services/sport_market_detector.py`
- All frontend files

### Allowed modifications:
- `backend/app/kernel/sport_market_bridge_service.py` — add `link_kalshi_market` + `_fetch_kalshi_price` + dispatch in `capture_snapshots` (additive)
- `backend/app/core/scheduler.py` — add Kalshi discovery in `_job_discover_sport_markets` (additive, try/except)
- `backend/app/core/config.py` — add 3 new settings
- `.env.example` — add 3 new env vars

### New files:
- `backend/app/services/kalshi_sports_source.py`
- `backend/tests/test_kalshi_sports_source.py`
- `backend/tests/test_kalshi_bridge_integration.py`
- `backend/tests/test_kalshi_scheduler.py`

## 11. Integration Points

- **Scheduler**: `_job_discover_sport_markets` gets a Kalshi discovery block (additive).
- **Bridge Service**: `capture_snapshots` dispatches to `_fetch_kalshi_price` for `source="kalshi"` links (additive branch).
- **EdgeDetector**: Unchanged — already aggregates across all sources via liquidity-weighted averaging.
- **Settlement**: Unchanged — already processes all verified links regardless of source.
- **Recommendation**: Unchanged — reads from `kernel_sport_edges` which is source-agnostic.

## 12. Success Criteria

1. ✅ When `PHASE11_KALSHI_SPORTS_ENABLED=true`, the scheduler discovers Kalshi sports markets and links them via the three-layer matching engine.
2. ✅ When `PHASE11_KALSHI_SPORTS_ENABLED=false`, no Kalshi markets are discovered — existing behavior unchanged.
3. ✅ Kalshi links have `source="kalshi"` in `kernel_sport_market_links`.
4. ✅ Price snapshots for Kalshi links are captured by the existing `_job_capture_market_snapshots` job.
5. ✅ `EdgeDetectorService` aggregates Kalshi prices alongside Polymarket prices (liquidity-weighted).
6. ✅ Kalshi discovery failure does not break Polymarket discovery (try/except isolation).
7. ✅ All 12 tests pass.
8. ✅ Zero-invasion: no modifications to EdgeDetector, Settlement, Recommendation, engines, kernel, domain, LearningService, or existing event sources.

## 13. Estimate

- 3 tasks, 12 tests, 4 new files + 4 modified files
- ~800 lines of new code
