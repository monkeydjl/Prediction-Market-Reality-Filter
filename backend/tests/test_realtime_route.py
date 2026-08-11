"""Tests for realtime WebSocket route — TDD RED phase."""
import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from app.api.routes.realtime import router
from app.realtime.connection_manager import get_connection_manager
from app.core.config import settings


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_manager():
    """Reset the singleton connection manager before each test."""
    import app.realtime.connection_manager as cm_module
    cm_module._connection_manager = None
    yield
    cm_module._connection_manager = None


def test_websocket_close_code_when_disabled_is_wire_legal(client, monkeypatch):
    """The disabled-push close code must be one a browser can actually deliver.

    RFC 6455 reserves everything below 1000, so closing with an HTTP status
    (this used to send 503) produces a frame the browser discards: it surfaces
    1006 with an empty reason, indistinguishable from a dropped connection, and
    the client reconnects forever against an endpoint that will never accept.
    """
    monkeypatch.setattr(settings, "PHASE10_REALTIME_PUSH_ENABLED", False)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/matches/match-1/prices"):
            pass
    assert 1000 <= exc_info.value.code <= 4999
    assert exc_info.value.code == 4503


def test_websocket_accepts_when_enabled(client, monkeypatch):
    """When enabled, the WS is accepted and the manager registers 1 subscriber."""
    monkeypatch.setattr(settings, "PHASE10_REALTIME_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS", 999)
    with client.websocket_connect("/ws/matches/match-1/prices") as ws:
        manager = get_connection_manager()
        assert manager.subscriber_count("match-1") == 1


def test_websocket_receives_broadcast(client, monkeypatch):
    """A broadcast sent via the manager is received by the connected client."""
    monkeypatch.setattr(settings, "PHASE10_REALTIME_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS", 999)
    with client.websocket_connect("/ws/matches/match-1/prices") as ws:
        manager = get_connection_manager()
        # Run the broadcast on the ASGI app's event loop (same loop as the WS)
        ws.portal.call(
            manager.broadcast_to_match, "match-1", {"type": "test", "data": 42}
        )
        msg = ws.receive_json()
        assert msg["type"] == "test"
        assert msg["data"] == 42


def test_websocket_disconnect_cleans_up(client, monkeypatch):
    """After the client disconnects, the subscriber count drops to 0."""
    monkeypatch.setattr(settings, "PHASE10_REALTIME_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS", 999)
    with client.websocket_connect("/ws/matches/match-1/prices"):
        manager = get_connection_manager()
        assert manager.subscriber_count("match-1") == 1
    # After the context exits, the route's finally block should have disconnected
    assert manager.subscriber_count("match-1") == 0
