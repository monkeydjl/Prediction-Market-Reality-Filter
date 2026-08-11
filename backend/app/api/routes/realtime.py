"""WebSocket route for real-time price push."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.realtime.connection_manager import get_connection_manager

router = APIRouter(prefix="/ws", tags=["Realtime"])

# Close code for "push is switched off". NOT 503: RFC 6455 reserves everything
# below 1000, so that frame is invalid on the wire — a browser discards the code
# and reports 1006, which the client cannot tell apart from a dropped network.
# 4503 is in the 4000-4999 private-use range and keeps the 503 mnemonic.
REALTIME_DISABLED_CLOSE_CODE = 4503


@router.websocket("/matches/{match_id}/prices")
async def price_stream(websocket: WebSocket, match_id: str) -> None:
    """WebSocket endpoint for real-time price updates.

    When PHASE10_REALTIME_PUSH_ENABLED is false, closes with
    ``REALTIME_DISABLED_CLOSE_CODE``. Otherwise, accepts the connection and
    pushes price updates as they are captured by the scheduler. Server-push
    only (no client messages).
    """
    if not settings.PHASE10_REALTIME_PUSH_ENABLED:
        await websocket.close(
            code=REALTIME_DISABLED_CLOSE_CODE, reason="Realtime push disabled"
        )
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
