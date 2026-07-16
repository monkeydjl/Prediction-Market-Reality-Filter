"""In-process WebSocket connection manager for real-time price push."""
from __future__ import annotations

from fastapi import WebSocket


class ConnectionManager:
    """Manages WebSocket connections grouped by match_id.

    All methods are safe to call from the asyncio event loop.
    broadcast_to_match is best-effort: dead connections are silently dropped.
    """

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

        Silently drops disconnected clients.
        """
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


# Module-level singleton
_connection_manager: ConnectionManager | None = None


def get_connection_manager() -> ConnectionManager:
    """Return the singleton ConnectionManager instance."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager
