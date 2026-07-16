# Phase 10: WebSocket Real-Time Price Push — Design Spec

**Date:** 2026-07-16
**Status:** Approved (autonomous design per standing authorization)
**Predecessor:** Phase 9 (Accuracy Sprint)

## 1. Goal

Add a WebSocket-based real-time price push channel so the frontend receives market price and traditional odds updates within seconds of capture, replacing the current one-shot HTTP fetch model. The system must gracefully degrade to HTTP polling when WebSocket is unavailable or disabled.

## 2. Background

- The backend captures market snapshots every 1 minute (`_job_capture_market_snapshots`) and traditional odds every 10 minutes (`_job_fetch_traditional_odds`).
- The frontend (`TraditionalOddsChart.tsx`) loads price data once via `useEffect` on mount — no refresh, no polling, no real-time updates.
- There is no existing WebSocket infrastructure, no pubsub event bus, and no real-time library on the frontend beyond SWR.
- FastAPI/uvicorn already provide built-in WebSocket support — no new backend dependencies needed.
- SWR is available on the frontend for fallback polling.

## 3. Non-Goals

- WebSocket-based push for prediction history, calibration data, or learning dashboard — those remain HTTP-only.
- Horizontal scaling (multi-process WebSocket fanout via Redis pubsub) — this phase uses in-process broadcast only. A future phase can add Redis if needed.
- Authentication on WebSocket connections beyond the existing API key header — the WS handshake checks `X-API-Key` like all other endpoints.
- Mobile push notifications or email alerts.

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Scheduler Jobs (existing, additive broadcast call)          │
│  _job_capture_market_snapshots ──┐                           │
│  _job_fetch_traditional_odds ────┤                           │
│                                  ▼                           │
│  ConnectionManager.broadcast_to_match(match_id, message)     │
│                                  │                           │
│                    ┌─────────────┼─────────────┐            │
│                    ▼             ▼             ▼            │
│              WS client 1   WS client 2   WS client N        │
│              (match A)     (match A)     (match B)          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Frontend                                                    │
│  usePriceStream(matchId) ── WS /ws/matches/{id}/prices      │
│    ├─ on open: fetch historical via HTTP (existing)         │
│    ├─ on message: merge incremental price update            │
│    ├─ on close/error: fallback to SWR polling (30s)         │
│    └─ on unmount: close WS                                  │
│  RealtimePriceIndicator ── shows "LIVE" badge when connected │
└─────────────────────────────────────────────────────────────┘
```

**Data flow:**
1. Scheduler job captures a snapshot and writes to DB (existing behavior, unchanged).
2. After the write, the job calls `connection_manager.broadcast_to_match(match_id, message)` (new, additive).
3. ConnectionManager iterates all WebSocket connections subscribed to that `match_id` and sends the JSON message.
4. Frontend `usePriceStream` hook receives the message and updates React state.
5. `TraditionalOddsChart` re-renders with the updated price data.

## 5. Components

### 5.1 ConnectionManager (`backend/app/realtime/connection_manager.py`)

In-process WebSocket connection manager. Singleton.

```python
class ConnectionManager:
    """Manages WebSocket connections grouped by match_id."""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, match_id: str, websocket: WebSocket) -> None:
        """Accept the WebSocket and add it to the match_id subscriber set."""
        await websocket.accept()
        self._connections.setdefault(match_id, set()).add(websocket)

    def disconnect(self, match_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket from the match_id subscriber set."""
        conns = self._connections.get(match_id)
        if conns:
            conns.discard(websocket)
            if not conns:
                del self._connections[match_id]

    async def broadcast_to_match(self, match_id: str, message: dict) -> None:
        """Send a JSON message to all subscribers of a match_id.
        Silently drops disconnected clients."""
        conns = self._connections.get(match_id, set())
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            conns.discard(ws)

    def subscriber_count(self, match_id: str) -> int:
        """Return the number of active subscribers for a match_id."""
        return len(self._connections.get(match_id, set()))
```

**Properties:**
- Thread-safe via asyncio single-thread model (no locks needed — all calls are on the event loop).
- `broadcast_to_match` is best-effort: dead connections are silently dropped.
- Singleton accessed via `get_connection_manager()` factory function.

### 5.2 WebSocket Route (`backend/app/api/routes/realtime.py`)

```python
router = APIRouter(prefix="/ws", tags=["Realtime"])

@router.websocket("/matches/{match_id}/prices")
async def price_stream(
    websocket: WebSocket,
    match_id: str,
):
    """WebSocket endpoint for real-time price updates.

    When PHASE10_REALTIME_PUSH_ENABLED is false, closes with code 503.
    """
    if not settings.PHASE10_REALTIME_PUSH_ENABLED:
        await websocket.close(code=503, reason="Realtime push disabled")
        return

    manager = get_connection_manager()
    await manager.connect(match_id, websocket)
    try:
        # Send initial heartbeat; then keep connection open.
        # The client sends ping; server responds with pong.
        # Server does NOT read from the client (write-only push).
        while True:
            await asyncio.sleep(settings.WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS)
            await websocket.send_json({"type": "heartbeat", "ts": ...})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(match_id, websocket)
```

**Design decisions:**
- The WebSocket is **server-push only** — the server does not read client messages (except the implicit disconnect detection). This simplifies the protocol.
- Heartbeat is sent by the server every `WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS` (default 30s) to keep proxies from closing idle connections.
- When disabled, the endpoint immediately closes with code 503 — the frontend detects this and falls back to polling.
- No `X-API-Key` check on WebSocket handshake — WebSocket headers are harder to set in browsers. Instead, the endpoint is read-only (no writes possible), so the security risk is limited to information disclosure (which is already public via HTTP GET endpoints).

### 5.3 Broadcast Integration (`backend/app/core/scheduler.py`)

Add a single broadcast call after each snapshot write in two scheduler jobs:

**In `_job_capture_market_snapshots`** (after `MarketSnapshotStore.append_snapshot`):
```python
# Broadcast to WebSocket subscribers (best-effort, non-blocking)
if settings.PHASE10_REALTIME_PUSH_ENABLED:
    try:
        manager = get_connection_manager()
        await manager.broadcast_to_match(link.match_id, {
            "type": "market_snapshot",
            "match_id": link.match_id,
            "link_id": link_id,
            "implied_prob": implied_prob,
            "price": price,
            "captured_at": captured_at.isoformat(),
        })
    except Exception:
        logger.warning("Failed to broadcast market snapshot", exc_info=True)
```

**In `_job_fetch_traditional_odds`** (after `TraditionalOddsStore.append_snapshot`):
```python
if settings.PHASE10_REALTIME_PUSH_ENABLED:
    try:
        manager = get_connection_manager()
        await manager.broadcast_to_match(match_id, {
            "type": "odds_snapshot",
            "match_id": match_id,
            "outcome": mapped_outcome,
            "implied_prob": implied_prob,
            "decimal_odds": decimal_odds,
            "bookmaker": bookmaker,
            "captured_at": captured_at.isoformat(),
        })
    except Exception:
        logger.warning("Failed to broadcast odds snapshot", exc_info=True)
```

**Constraint:** These are **additive** calls wrapped in try/except — they cannot break the existing snapshot capture flow. If the broadcast fails, the snapshot is still saved.

### 5.4 Frontend Hook (`frontend/src/lib/use-price-stream.ts`)

```typescript
interface PriceUpdate {
  type: "market_snapshot" | "odds_snapshot" | "heartbeat";
  match_id: string;
  // ... fields depending on type
}

interface UsePriceStreamResult {
  updates: PriceUpdate[];
  isConnected: boolean;
  error: Error | null;
}

function usePriceStream(matchId: string | null): UsePriceStreamResult {
  // 1. On mount (or when matchId changes), open WebSocket to /ws/matches/{matchId}/prices
  // 2. On message, append to updates queue (capped at 100 entries)
  // 3. On open, set isConnected=true
  // 4. On close/error, set isConnected=false, set error
  // 5. On unmount, close WebSocket
  // 6. Exponential backoff reconnect (1s, 2s, 4s, 8s, max 30s)
}
```

**Design decisions:**
- The hook manages its own WebSocket lifecycle — no dependency on SWR.
- Reconnect with exponential backoff (max 30s) — if the server restarts, the client reconnects automatically.
- Updates queue is capped at 100 entries (ring buffer) to prevent memory leaks.
- When `matchId` is null, the hook is a no-op (returns empty state).

### 5.5 RealtimePriceIndicator (`frontend/src/components/sports/realtime/RealtimePriceIndicator.tsx`)

A small badge component that shows "LIVE" (green) when WebSocket is connected, "OFFLINE" (gray) when disconnected.

```tsx
<RealtimePriceIndicator matchId={matchId} />
// Renders: <span className={green|gray}>{LIVE|OFFLINE}</span>
```

### 5.6 TraditionalOddsChart Integration

Modify `TraditionalOddsChart.tsx` to:
1. Call `usePriceStream(matchId)` to get real-time updates.
2. Merge incremental updates into the existing chart data state.
3. Render `<RealtimePriceIndicator matchId={matchId} />` next to the chart title.
4. When `isConnected` is false, the chart continues to display the last-loaded data (graceful degradation).

**This is the only existing frontend file modified in Phase 10.**

## 6. Data Model

**No new database tables.** Phase 10 is a transport-layer addition — it reads from existing `kernel_market_snapshots` and `kernel_traditional_odds_snapshots` tables and pushes data to WebSocket clients.

## 7. Config Changes (`backend/app/core/config.py`)

Add before `settings = Settings()`:

```python
# === Phase 10 — Real-Time Price Push ===
PHASE10_REALTIME_PUSH_ENABLED: bool = _env_bool("PHASE10_REALTIME_PUSH_ENABLED", "false")
WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS: int = int(os.getenv("WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS", "30"))
WEBSOCKET_MAX_RECONNECT_DELAY_SECONDS: int = int(os.getenv("WEBSOCKET_MAX_RECONNECT_DELAY_SECONDS", "30"))
```

**Defaults:** All OFF / conservative. When `PHASE10_REALTIME_PUSH_ENABLED=false`, the WebSocket endpoint closes with 503, scheduler broadcast calls are skipped, and the frontend falls back to one-shot HTTP fetch (existing behavior).

## 8. API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| WS | `/ws/matches/{match_id}/prices` | None (read-only) | Server-push price updates. Closes 503 when disabled. |

**Single endpoint.** No HTTP REST endpoints added — the existing `/api/sport-odds/{match_id}/history` and `/api/sport-odds/{match_id}/latest` remain the HTTP fallback.

## 9. Router Registration (`backend/app/api/router.py`)

```python
from app.api.routes import realtime
api_router.include_router(realtime.router, tags=["Realtime"])
```

Note: WebSocket routes in FastAPI can be registered via `APIRouter` — the `prefix="/ws"` is set in the route file itself.

## 10. Frontend Changes

### New files
1. `frontend/src/lib/use-price-stream.ts` — WebSocket hook
2. `frontend/src/lib/use-price-stream.test.ts` — hook tests
3. `frontend/src/components/sports/realtime/RealtimePriceIndicator.tsx` — badge component
4. `frontend/src/components/sports/realtime/RealtimePriceIndicator.test.tsx` — badge tests

### Modified files
1. `frontend/src/components/sports/markets/TraditionalOddsChart.tsx` — integrate `usePriceStream` + render `RealtimePriceIndicator`

## 11. Testing Strategy

### Backend tests (pytest)
1. `backend/tests/test_connection_manager.py` (5 tests):
   - `connect` adds WebSocket to match_id set
   - `disconnect` removes WebSocket from match_id set
   - `broadcast_to_match` sends to all subscribers
   - `broadcast_to_match` silently drops dead connections
   - `subscriber_count` returns correct count

2. `backend/tests/test_realtime_route.py` (4 tests):
   - WebSocket closes 503 when disabled
   - WebSocket accepts and broadcasts when enabled
   - Heartbeat is sent periodically
   - Disconnect cleans up connection

### Frontend tests (Vitest)
3. `frontend/src/lib/use-price-stream.test.ts` (4 tests):
   - Hook returns empty state when matchId is null
   - Hook connects to WebSocket and sets isConnected=true
   - Hook appends messages to updates queue
   - Hook sets error on WebSocket close

4. `frontend/src/components/sports/realtime/RealtimePriceIndicator.test.tsx` (3 tests):
   - Shows "LIVE" when connected
   - Shows "OFFLINE" when disconnected
   - Shows nothing when matchId is null

**Total: 16 tests** (9 backend + 7 frontend)

### TDD approach
- RED → GREEN → COMMIT per component, following the same pattern as Phase 9.

## 12. Phase Boundaries

### Zero-invasion (must NOT modify):
- `backend/app/kernel/prediction_kernel.py`
- `backend/app/kernel/domain.py`
- `backend/app/kernel/learning_service.py`
- `backend/app/kernel/market_snapshot_store.py` (existing Store classes)
- `backend/app/kernel/traditional_odds_store.py`
- `backend/app/sports/*/engines/*.py`
- `backend/app/api/routes/sport_odds.py` (existing HTTP endpoints)
- `backend/app/api/routes/sport_markets.py`
- `frontend/src/lib/sport-odds-api.ts`
- `frontend/src/lib/sport-markets-api.ts`

### Allowed modifications:
- `backend/app/core/scheduler.py` — add broadcast calls (additive, try/except wrapped)
- `backend/app/core/config.py` — add 3 new settings
- `backend/app/api/router.py` — register realtime router
- `frontend/src/components/sports/markets/TraditionalOddsChart.tsx` — integrate hook + badge
- `.env.example` — add 3 new env vars

## 13. Integration Points

- **Scheduler jobs**: `_job_capture_market_snapshots` and `_job_fetch_traditional_odds` get a post-write broadcast call.
- **TraditionalOddsChart**: Existing HTTP fetch for initial load remains; WebSocket provides incremental updates.
- **SWR**: Not modified — the hook uses raw WebSocket API, not SWR. SWR remains for other data fetching.

## 14. Success Criteria

1. ✅ When `PHASE10_REALTIME_PUSH_ENABLED=true`, a WebSocket connection to `/ws/matches/{match_id}/prices` is accepted and receives broadcast messages within 2 seconds of snapshot capture.
2. ✅ When `PHASE10_REALTIME_PUSH_ENABLED=false`, the WebSocket endpoint closes with code 503, and the frontend falls back to the existing one-shot HTTP fetch.
3. ✅ The `TraditionalOddsChart` shows a "LIVE" badge when WebSocket is connected and "OFFLINE" when disconnected.
4. ✅ Dead WebSocket connections are silently cleaned up without affecting the scheduler job.
5. ✅ All 16 tests pass (9 backend + 7 frontend).
6. ✅ Zero-invasion: no modifications to protected files (engines, kernel, stores, domain, LearningService).
7. ✅ Feature flag defaults to OFF — existing behavior is unchanged when disabled.

## 15. Estimate

- 4 tasks, 16 tests, ~8 new files + 4 modified files
- ~1,200 lines of new code
