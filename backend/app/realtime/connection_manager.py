"""In-process WebSocket connection manager for real-time price push."""
from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

from app.core.config import settings
from app.realtime import ws_auth

logger = logging.getLogger(__name__)


class AtCapacity(Exception):
    """Raised by `connect` instead of accepting a socket over a cap.

    Carries no numbers outward: the route turns this into a close frame, and how
    many sockets other callers hold is not the refused caller's business.
    """


class ConnectionManager:
    """Manages WebSocket connections grouped by match_id.

    All methods are safe to call from the asyncio event loop.
    broadcast_to_match is best-effort: dead connections are silently dropped.

    Three caps, because each one alone is escapable. `_connections` used to be an
    unbounded dict of unbounded sets: a per-match cap is bypassed by asking for
    another match id, a global cap alone lets one host fill the budget and lock
    every other operator out, and the HTTP rate limiter cannot help at all --
    `InMemoryRateLimitMiddleware` is a `BaseHTTPMiddleware` and never sees a
    WebSocket scope.
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        # Live per-caller counts, and the identity each socket was counted under.
        # The identity is recorded rather than re-derived on disconnect: a header
        # is re-readable but the peer address is not guaranteed to be, and a
        # decrement keyed on a different value than the increment leaks the slot
        # forever.
        self._per_client: dict[str, int] = {}
        self._client_of: dict[WebSocket, str] = {}
        # Slots claimed by a handshake that has started but not finished. Counted
        # alongside the live ones, because the live counts alone cannot express
        # "in flight": see `connect` for why that gap admitted one socket per
        # concurrent handshake over the cap.
        self._reserved_total = 0
        self._reserved_per_match: dict[str, int] = {}
        self._reserved_per_client: dict[str, int] = {}

    def total_connections(self) -> int:
        return sum(len(conns) for conns in self._connections.values())

    def client_connection_count(self) -> dict[str, int]:
        """Live count per caller identity. Empty when nothing is connected."""
        return dict(self._per_client)

    def match_ids(self) -> list[str]:
        """Match ids holding at least one live subscriber.

        Public because "no bucket was created" is a property worth asserting, and a
        test reaching into `_connections` to assert it would pass just as well
        against a private dict that had stopped being the one `broadcast_to_match`
        reads.
        """
        return sorted(self._connections)

    def _capacity_failure(self, match_id: str, client: str) -> str | None:
        """Which cap this connection would exceed, for the log. None when it fits.

        Counts live **and reserved** slots. A reservation is a handshake that has
        passed this check and not yet registered; ignoring them is what let a cap
        of 1 admit 2 (`tests/test_realtime_connection_lifecycle.py` measures it).

        A cap of zero or less is treated as "full", not as "unlimited". The
        opposite reading is the trap `preflight` guards: an operator setting 0 to
        mean "no limit" would silently restore the unbounded endpoint, so the
        posture reports `connection_limited=False` for a non-positive cap and
        production refuses to start.
        """
        if (
            self.total_connections() + self._reserved_total
            >= max(0, settings.WEBSOCKET_MAX_CONNECTIONS_TOTAL)
        ):
            return "WEBSOCKET_MAX_CONNECTIONS_TOTAL"
        if (
            len(self._connections.get(match_id, ()))
            + self._reserved_per_match.get(match_id, 0)
            >= max(0, settings.WEBSOCKET_MAX_CONNECTIONS_PER_MATCH)
        ):
            return "WEBSOCKET_MAX_CONNECTIONS_PER_MATCH"
        if (
            self._per_client.get(client, 0)
            + self._reserved_per_client.get(client, 0)
            >= max(0, settings.WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT)
        ):
            return "WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT"
        return None

    def _reserve(self, match_id: str, client: str) -> None:
        """Claim one slot in all three books. Caller must have checked capacity.

        Runs with no `await` between the check and these increments, which is the
        whole mechanism: the event loop cannot interleave another handshake into
        that window, so the next check already sees this claim.
        """
        self._reserved_total += 1
        self._reserved_per_match[match_id] = (
            self._reserved_per_match.get(match_id, 0) + 1
        )
        self._reserved_per_client[client] = (
            self._reserved_per_client.get(client, 0) + 1
        )

    def _release(self, match_id: str, client: str) -> None:
        """Drop one claim from all three books. Zero rows are removed, not kept.

        Keeping them would grow both dicts by one entry per match id and per caller
        address for the life of the process, which is the unbounded-growth shape the
        caps exist to close.
        """
        self._reserved_total = max(0, self._reserved_total - 1)
        for book, key in (
            (self._reserved_per_match, match_id),
            (self._reserved_per_client, client),
        ):
            remaining = book.get(key, 0) - 1
            if remaining > 0:
                book[key] = remaining
            else:
                book.pop(key, None)

    async def connect(
        self,
        match_id: str,
        websocket: WebSocket,
        subprotocol: str | None = None,
    ) -> None:
        """Accept the WebSocket and add it to the match_id subscriber set.

        Raises `AtCapacity` **without accepting** when a cap is reached. Accepting
        first and closing after would already have cost the slot the cap exists to
        protect.

        The slot is *reserved* before the first await, not merely checked. This used
        to read the live counts and then `await websocket.accept()`; that await
        yields the event loop, so two handshakes arriving in the same iteration both
        measured "0 of 1 used" and both went on to register. A cap of 1 admitted 2,
        and N concurrent handshakes admitted N. The reservation closes the window
        because the check and the claim are not separated by an await, so the second
        handshake sees the first one's claim and is refused.

        Every exit from here either converts the reservation into a live connection
        or releases it. `BaseException` rather than `Exception` on purpose: a
        cancelled task -- a shutdown or a timeout -- must not leave a claim behind,
        and must still be seen by the caller, so it is re-raised untouched.
        """
        client = ws_auth.client_identity(websocket)
        exceeded = self._capacity_failure(match_id, client)
        if exceeded is not None:
            # The cap name and the identity are operational facts, and the
            # identity is an address the deployment already logs per request.
            logger.warning(
                "Realtime WebSocket refused at capacity (%s) for match %s.",
                exceeded, match_id,
            )
            raise AtCapacity(exceeded)
        self._reserve(match_id, client)

        try:
            # `subprotocol` is passed in rather than re-read off the scope here: the
            # route already resolved it while authenticating, and re-deriving it
            # would be a second place that has to agree about the handshake.
            if subprotocol is None:
                await websocket.accept()
            else:
                await websocket.accept(subprotocol=subprotocol)
        except BaseException:
            self._release(match_id, client)
            raise

        # Reservation -> live connection. No await in here, so no observer can see
        # the socket counted in both books at once.
        try:
            self._connections.setdefault(match_id, set()).add(websocket)
            self._client_of[websocket] = client
            self._per_client[client] = self._per_client.get(client, 0) + 1
        except BaseException:
            # Registration is plain dict/set work and is not expected to raise, but
            # a half-registered socket would be a permanent phantom count, so the
            # partial state is undone rather than trusted.
            self.disconnect(match_id, websocket)
            raise
        finally:
            self._release(match_id, client)

    def disconnect(self, match_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket from the match_id subscriber set. Idempotent.

        Idempotence is a requirement, not a nicety: three paths clean up the same
        socket -- the route's `finally`, a failed broadcast, and a cap refusal that
        raised before the socket was ever registered -- and they can run in any
        order. `_client_of` is the authority on whether this socket is counted, so a
        repeat call finds nothing to decrement and returns without touching a
        sibling's count. Keying the decrement on anything else (a re-derived
        identity, or the match set alone) is what would let one socket's cleanup
        release another socket's slot.
        """
        conns = self._connections.get(match_id)
        if conns:
            conns.discard(websocket)
            if not conns:
                del self._connections[match_id]
        client = self._client_of.pop(websocket, None)
        if client is not None:
            remaining = self._per_client.get(client, 0) - 1
            if remaining > 0:
                self._per_client[client] = remaining
            else:
                # Dropped rather than left at 0: the dict is keyed by caller
                # address, so keeping zero rows would grow it for the life of the
                # process -- the same unbounded-growth shape the caps close.
                self._per_client.pop(client, None)

    async def broadcast_to_match(self, match_id: str, message: dict) -> None:
        """Send a JSON message to all subscribers of a match_id.

        Silently drops disconnected clients -- through `disconnect`, so all three
        books are cleaned. This used to `conns.discard(ws)` and nothing else, which
        left the socket in `_client_of` and its slot counted in `_per_client`
        forever. A send failure is the *normal* way a client goes away, so that
        leaked one slot per departed client against
        `WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT`: the cap counted down over an uptime,
        never back up, and the endpoint eventually refused the very caller it had
        just dropped. Nothing logs it, so the first symptom is a client that cannot
        reconnect.

        **Cancellation.** A broadcast task cancelled mid-`send_json` reaps that one
        socket through the same `disconnect`, then re-raises `CancelledError`.
        `connect` already treats a cancelled handshake this way (its failure paths
        catch `BaseException`); the broadcast path used to catch `Exception` only,
        so a cancellation skipped the reaping loop *and* the `except Exception` both
        scheduler call sites wrap the broadcast in (scheduler.py:1005, :1183), and
        the departed socket kept its per-client slot for the life of the process.

        The cancelled socket is reaped rather than kept because a cancellation
        during a send means the *task's* continuation is gone, but the manager
        lives in the app's process, which the scheduler's jobs and every other
        subscriber share: a socket whose send was abandoned mid-frame is not
        known to be deliverable to, and leaving it counted is what turns a
        one-task cancellation into a permanent phantom slot. Whether the process
        is shutting down or only one job was cancelled, the state left behind is
        the same books-empty state, and both scheduler sites' own `finally`
        paths and the route's `finally` remain safe to call again over it. The
        `CancelledError` is always re-raised after cleanup -- swallowing it would
        convert a cancelled broadcast into a normal completion and would eat the
        shutdown the cancellation belongs to.
        """
        # Snapshot the set before iterating: await below yields the event loop,
        # and another coroutine calling disconnect() could mutate the live set
        # mid-iteration, raising RuntimeError: Set changed size during iteration.
        conns = self._connections.get(match_id)
        if not conns:
            return
        snapshot = list(conns)
        dead: list[WebSocket] = []
        cancelled: WebSocket | None = None
        try:
            for ws in snapshot:
                try:
                    await ws.send_json(message)
                except asyncio.CancelledError:
                    # The socket whose send was abandoned. Not appended to `dead`
                    # yet: cleanup happens in the `finally` below so it cannot be
                    # skipped by a *second* cancellation arriving during the
                    # reap loop for an earlier socket.
                    cancelled = ws
                    raise
                except Exception:
                    dead.append(ws)
        finally:
            # One cleanup path for both endings. Reached on the cancel branch as
            # well as on normal completion, and idempotent, so a re-raise through
            # a caller's `finally` (the route's, the scheduler sites') cannot
            # double-release.
            if cancelled is not None:
                self.disconnect(match_id, cancelled)
            for ws in dead:
                self.disconnect(match_id, ws)

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
