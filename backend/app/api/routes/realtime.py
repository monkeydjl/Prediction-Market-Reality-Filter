"""WebSocket route for real-time price push."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.realtime.connection_manager import get_connection_manager

router = APIRouter(prefix="/ws", tags=["Realtime"])


@router.websocket("/matches/{match_id}/prices")
async def price_stream(websocket: WebSocket, match_id: str) -> None:
    """WebSocket endpoint for real-time price updates.

    When PHASE10_REALTIME_PUSH_ENABLED is false, closes with code 503.
    Otherwise, accepts the connection and pushes price updates as they
    are captured by the scheduler. Server-push only (no client messages).
    """
    if not settings.PHASE10_REALTIME_PUSH_ENABLED:
        await websocket.close(code=503, reason="Realtime push disabled")
        return

    manager = get_connection_manager()
    await manager.connect(match_id, websocket)
    try:
        while True:
            await asyncio.sleep(settings.WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS)
            await websocket.send_json(
                {
                    "type": "heartbeat",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        manager.disconnect(match_id, websocket)
