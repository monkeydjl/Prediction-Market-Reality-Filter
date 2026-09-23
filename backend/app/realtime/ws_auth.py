"""Handshake authentication for the realtime WebSocket.

`/api/ws/matches/{id}/prices` used to accept every socket that arrived while
`PHASE10_REALTIME_PUSH_ENABLED` was true. What it serves is live bookmaker odds,
implied probabilities and Kalshi prices, pushed by the scheduler, so "anyone who
can reach the port" was the whole access-control policy.

Two transports, because a browser has exactly one lever. `new WebSocket(url,
protocols)` can influence `Sec-WebSocket-Protocol` and nothing else -- there is no
way to set a custom header -- so a browser buys a short-lived, single-use ticket
from `POST /api/ws/tickets` (write key in an `X-API-Key` header) and offers it as a
subprotocol. Programmatic callers send `X-API-Key` on the handshake directly.

Deliberately **not** supported: the key in a query string. It is the only other
thing a browser can do unaided, which is exactly why it needs refusing rather than
merely omitting -- a URL lands in proxy access logs, browser history and
`Referer`. A ticket is safe there by construction (single use, seconds long) and is
still not put there.

Nothing here logs a credential. A refusal logs the *shape* of what was presented
("no credential", "invalid ticket"), never the value, because these lines are the
ones an operator pastes into an issue.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass

from app.utils import sqlite_db

from starlette.websockets import WebSocket

from app.api.security import is_write_key_valid
from app.core.config import settings
from app.core.rate_limit import _client_host

logger = logging.getLogger(__name__)

#: A browser offers `pmrf.ticket.<ticket>`; the server echoes the same value back,
#: which RFC 6455 requires when the client offered any subprotocol at all.
TICKET_SUBPROTOCOL_PREFIX = "pmrf.ticket."

# RFC 6455 close codes. Both are in the registered range a browser will actually
# deliver -- unlike an HTTP status, which the browser discards as 1006 (see
# `REALTIME_DISABLED_CLOSE_CODE` in the route for the same trap).
WS_UNAUTHORIZED_CLOSE_CODE = 1008  # policy violation
WS_AT_CAPACITY_CLOSE_CODE = 1013  # try again later

#: The per-client cap keys on the same identity the HTTP rate limiter uses. Bound
#: to the shared implementation rather than reimplemented: it resolves
#: X-Forwarded-For *from the right* under `TRUSTED_PROXY_HEADER`, which is what
#: stops a caller rotating a spoofed header into an unlimited number of buckets.
#: `tests/test_realtime_ws_hardening.py` asserts this is that function.
client_identity = _client_host


@dataclass(frozen=True)
class WsAuthResult:
    """Whether to accept, and what to echo when accepting."""

    authorized: bool
    #: Passed to `websocket.accept(subprotocol=...)`. `None` when the client
    #: offered nothing, which is the header path.
    subprotocol: str | None = None
    #: Safe for a close frame and a log line: names the shape, never the value.
    reason: str = ""


class TicketStore:
    """Short-lived single-use handshake tickets in the shared loop database.

    The API may be served by more than one worker. Ticket issuance and
    redemption therefore use the same SQLite file as the other durable stores,
    with redemption expressed as one conditional DELETE transaction. A
    process-local dictionary would let a ticket bought by worker A fail on
    worker B and would not make consume-once atomic across workers.
    """

    _TABLE = "realtime_ws_tickets"

    @staticmethod
    def _ticket_digest(ticket: str) -> str:
        """Return a non-reversible lookup key for a bearer ticket."""
        return hashlib.sha256(ticket.encode("utf-8")).hexdigest()

    @staticmethod
    def _ensure_schema(conn) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS realtime_ws_tickets (
                ticket TEXT PRIMARY KEY,
                expires_at REAL NOT NULL,
                issued_at REAL NOT NULL
            )
            """
        )

    def issue(self) -> tuple[str, int]:
        """Return ``(ticket, ttl_seconds)`` and persist it for every worker."""
        ttl = max(1, int(settings.WEBSOCKET_TICKET_TTL_SECONDS))
        now = time.time()
        ticket = secrets.token_urlsafe(32)
        ticket_digest = self._ticket_digest(ticket)
        with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
            self._ensure_schema(conn)
            conn.execute(
                f"DELETE FROM {self._TABLE} WHERE expires_at <= ?", (now,)
            )
            cap = max(1, int(settings.WEBSOCKET_MAX_ACTIVE_TICKETS))
            count = int(
                conn.execute(f"SELECT COUNT(*) FROM {self._TABLE}").fetchone()[0]
            )
            excess = count - cap + 1
            if excess > 0:
                conn.execute(
                    f"""
                    DELETE FROM {self._TABLE}
                    WHERE ticket IN (
                        SELECT ticket FROM {self._TABLE}
                        ORDER BY issued_at ASC
                        LIMIT ?
                    )
                    """,
                    (excess,),
                )
            conn.execute(
                f"INSERT INTO {self._TABLE}(ticket, expires_at, issued_at) VALUES (?, ?, ?)",
                (ticket_digest, now + ttl, now),
            )
        return ticket, ttl

    def redeem(self, ticket: str) -> bool:
        """Consume ``ticket`` atomically; false when unknown or expired."""
        now = time.time()
        ticket_digest = self._ticket_digest(ticket)
        with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
            self._ensure_schema(conn)
            conn.execute(
                f"DELETE FROM {self._TABLE} WHERE expires_at <= ?", (now,)
            )
            deleted = conn.execute(
                f"DELETE FROM {self._TABLE} WHERE ticket = ? AND expires_at > ?",
                (ticket_digest, now),
            )
            return deleted.rowcount == 1

    def active_count(self) -> int:
        """Return the number of currently redeemable tickets."""
        now = time.time()
        with sqlite_db.writing(sqlite_db.loop_db_path()) as conn:
            self._ensure_schema(conn)
            conn.execute(
                f"DELETE FROM {self._TABLE} WHERE expires_at <= ?", (now,)
            )
            return int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {self._TABLE} WHERE expires_at > ?",
                    (now,),
                ).fetchone()[0]
            )


_ticket_store: TicketStore | None = None


def get_ticket_store() -> TicketStore:
    global _ticket_store
    if _ticket_store is None:
        _ticket_store = TicketStore()
    return _ticket_store


def auth_is_enforced() -> bool:
    """True when this process refuses an anonymous socket.

    The boundary is the one the rest of the codebase already uses -- a configured
    `API_WRITE_KEY` -- not a new toggle and not a test-only switch. Production is
    covered because the preflight independently requires that key to be non-empty,
    so the two rules compose: production always enforces. A dev box with no key
    keeps an open socket, the same way `_resolve_encryption_key` keeps plaintext
    backups legal off production.
    """
    return bool(str(settings.API_WRITE_KEY or "").strip())


def _offered_ticket(websocket: WebSocket) -> str | None:
    """The first `pmrf.ticket.*` subprotocol the client offered, if any."""
    for offer in websocket.scope.get("subprotocols") or []:
        text = str(offer)
        if text.startswith(TICKET_SUBPROTOCOL_PREFIX):
            return text
    return None


def authenticate_websocket(websocket: WebSocket) -> WsAuthResult:
    """Decide the handshake **before** anything calls `accept()`.

    Accepting first and closing after would already have cost a connection slot
    and would already have been counted, which is precisely what an unbounded
    fan-out needs. The route therefore closes on a negative result without ever
    accepting.
    """
    offered = _offered_ticket(websocket)

    if not auth_is_enforced():
        # No key exists to check. Reported by the startup posture as
        # unauthenticated, and refused outright in production by the preflight.
        logger.info(
            "Realtime WebSocket accepted without authentication: API_WRITE_KEY "
            "is not configured. Production refuses this at startup."
        )
        return WsAuthResult(True, offered, "unauthenticated (no key configured)")

    if offered is not None:
        ticket = offered[len(TICKET_SUBPROTOCOL_PREFIX):]
        if get_ticket_store().redeem(ticket):
            # Echo the offer: RFC 6455 lets a server that ignores a client's
            # subprotocol offer fail the handshake in some browsers.
            return WsAuthResult(True, offered, "ticket")
        logger.warning(
            "Realtime WebSocket handshake refused: the offered ticket was "
            "unknown, already used, or expired."
        )
        return WsAuthResult(False, None, "invalid ticket")

    presented = websocket.headers.get("x-api-key")
    if presented is not None:
        if is_write_key_valid(presented):
            return WsAuthResult(True, None, "header key")
        logger.warning(
            "Realtime WebSocket handshake refused: the X-API-Key header did not "
            "match."
        )
        return WsAuthResult(False, None, "invalid key")

    logger.warning(
        "Realtime WebSocket handshake refused: no credential presented. A "
        "browser must buy a ticket from POST /api/ws/tickets and offer it as a "
        "%s* subprotocol; a script may send an X-API-Key header.",
        TICKET_SUBPROTOCOL_PREFIX,
    )
    return WsAuthResult(False, None, "no credential")
