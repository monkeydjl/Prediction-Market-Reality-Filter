"""WebSocket route for real-time price push."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status

from app.api.security import require_write_key
from app.core.config import settings
from app.realtime import ws_auth
from app.realtime.connection_manager import AtCapacity, get_connection_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["Realtime"])

# Close code for "push is switched off". NOT 503: RFC 6455 reserves everything
# below 1000, so that frame is invalid on the wire — a browser discards the code
# and reports 1006, which the client cannot tell apart from a dropped network.
# 4503 is in the 4000-4999 private-use range and keeps the 503 mnemonic.
REALTIME_DISABLED_CLOSE_CODE = 4503

#: No such broadcastable match. Private-use range, keeping the 404 mnemonic, for
#: the same reason as above. Distinct from the codes below so a client can tell
#: "this id will never work" from "come back later" -- retrying the first forever is
#: what the frontend's reconnect loop would otherwise do.
WS_UNKNOWN_MATCH_CLOSE_CODE = 4404

#: The match lookup itself failed, so whether the match exists is unknown and the
#: socket is refused. Deliberately not folded into `WS_UNKNOWN_MATCH_CLOSE_CODE`: a
#: broken links table is an operator's problem and a client retrying an invented id
#: is not, and one code for both would make them indistinguishable in exactly the
#: situation where the difference matters. 1011 is the registered "internal error".
WS_LOOKUP_UNAVAILABLE_CLOSE_CODE = 1011


class MatchLookupUnavailable(Exception):
    """The authoritative store could not answer whether a match is broadcastable.

    Separate from "not broadcastable" on purpose. `get_verified_links` raises on a
    broken table rather than returning `[]` -- that swallow was removed deliberately,
    because "no verified links" is also the normal answer -- so collapsing the raise
    back into a False here would re-create the same defect one layer up.

    Carries no message outward: the route turns this into a fixed close reason, and
    the exception text would be a SQL statement.
    """


def match_is_broadcastable(match_id: str) -> bool:
    """True when the scheduler can actually push snapshots for `match_id`.

    The predicate is "has at least one verified link", read from
    `SportMarketLinkStore` -- the authoritative store, and the same one both
    broadcast sites use: `scheduler.py` enumerates
    `get_matches_with_verified_links()` and pushes per match from
    `get_verified_links(match_id=...)`. So a socket is admitted exactly for the
    matches that can produce traffic.

    Deliberately not a format, length or regex check on the id. Those accept ids
    that will never be broadcast to (and reject ones that would be, whenever a new
    competition prefix appears), which makes them a guess about the data rather than
    a question to it.

    A match whose links are all unverified is *not* broadcastable: it is in the table
    and the scheduler still never pushes for it, so a socket there is a slot held
    open for traffic that cannot arrive.

    Raises `MatchLookupUnavailable` when the store cannot answer.
    """
    from app.kernel.sport_market_link_store import SportMarketLinkStore

    try:
        links = SportMarketLinkStore().get_verified_links(match_id=match_id)
    except Exception as exc:
        # Type name only. The exception string is a SQL statement and a file path,
        # and this line is one an operator pastes into an issue.
        logger.error(
            "Realtime WebSocket refused: the sport-market link store could not be "
            "read (%s), so whether this match is broadcastable is unknown.",
            type(exc).__name__,
        )
        raise MatchLookupUnavailable from exc
    return bool(links)


@router.post(
    "/tickets",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_write_key)],
    summary="Buy a short-lived ticket for a realtime WebSocket handshake",
)
async def issue_ws_ticket() -> dict[str, Any]:
    """Exchange the write key for a single-use handshake ticket.

    This exists because a browser cannot set a header on a WebSocket handshake:
    `new WebSocket(url, protocols)` influences `Sec-WebSocket-Protocol` and
    nothing else. So the key is spent here, over ordinary authenticated HTTP, and
    what reaches the socket is a ticket that is useless a minute later and useless
    twice.

    `subprotocol` is returned pre-assembled so a client never has to hardcode the
    prefix -- a mismatch would fail the handshake with nothing to explain why.
    """
    ticket, ttl = ws_auth.get_ticket_store().issue()
    return {
        "ticket": ticket,
        "expires_in": ttl,
        "subprotocol": f"{ws_auth.TICKET_SUBPROTOCOL_PREFIX}{ticket}",
    }


@router.websocket("/matches/{match_id}/prices")
async def price_stream(websocket: WebSocket, match_id: str) -> None:
    """WebSocket endpoint for real-time price updates.

    Server-push only (no client messages). Five refusals, in this order, each with a
    close code a browser will actually deliver:

    * `PHASE10_REALTIME_PUSH_ENABLED` false -> ``REALTIME_DISABLED_CLOSE_CODE``.
      Checked first, so a valid credential cannot switch the feature on.
    * no valid credential -> ``WS_UNAUTHORIZED_CLOSE_CODE``. An `X-API-Key`
      header, or a `pmrf.ticket.*` subprotocol bought from POST /ws/tickets.
    * the match cannot be broadcast to -> ``WS_UNKNOWN_MATCH_CLOSE_CODE``.
    * the match lookup failed -> ``WS_LOOKUP_UNAVAILABLE_CLOSE_CODE`` (fail closed).
    * a connection cap reached -> ``WS_AT_CAPACITY_CLOSE_CODE``.

    Nothing is accepted until all five pass: what this streams is live bookmaker
    odds and Kalshi prices, and an accepted-then-closed socket has already spent
    the budget the caps defend.

    Authentication runs **before** the match lookup, and that order is pinned by a
    test. The reverse would make the close code an existence oracle on the links
    table for a caller with no credential at all -- and it would also spend a
    database read per anonymous connection attempt.
    """
    if not settings.PHASE10_REALTIME_PUSH_ENABLED:
        await websocket.close(
            code=REALTIME_DISABLED_CLOSE_CODE, reason="Realtime push disabled"
        )
        return

    # Before `accept()`, always. A socket accepted and then closed has already
    # cost the slot the caps exist to protect, and has already been counted.
    auth = ws_auth.authenticate_websocket(websocket)
    if not auth.authorized:
        await websocket.close(
            code=ws_auth.WS_UNAUTHORIZED_CLOSE_CODE,
            # `auth.reason` names the shape of what was presented ("no
            # credential", "invalid ticket"), never the value.
            reason=f"Unauthorized: {auth.reason}",
        )
        return

    try:
        known = match_is_broadcastable(match_id)
    except MatchLookupUnavailable:
        # Fail closed. The alternative -- admitting the socket because the check
        # could not run -- turns a broken table into an open endpoint, and "could
        # not run" is not "passed".
        await websocket.close(
            code=WS_LOOKUP_UNAVAILABLE_CLOSE_CODE,
            reason="Match lookup unavailable",
        )
        return
    if not known:
        # Fixed text. The match id is the caller's own input so echoing it leaks
        # nothing, but there is no reason for a close frame to carry it either.
        await websocket.close(
            code=WS_UNKNOWN_MATCH_CLOSE_CODE, reason="Unknown match"
        )
        return

    manager = get_connection_manager()
    try:
        await manager.connect(match_id, websocket, subprotocol=auth.subprotocol)
    except AtCapacity:
        # No cap name and no counts on the wire: which limit a deployment runs
        # and how close it is are not the refused caller's business. The name is
        # in the server log.
        await websocket.close(
            code=ws_auth.WS_AT_CAPACITY_CLOSE_CODE,
            reason="Too many connections",
        )
        return
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
        # The ordinary ending: the client went away.
        pass
    except asyncio.CancelledError:
        # A server shutdown or a cancelled task. Re-raised, never swallowed:
        # absorbing it would report a cancelled handshake as a live connection and
        # would stop the shutdown it belongs to. `finally` below still runs, so the
        # slot is released either way.
        raise
    except Exception:
        # A dead socket raises on send, which is a normal ending too. The slot is
        # released in `finally`; the failure itself carries the peer's connection
        # state and is not worth a line per departed client.
        pass
    finally:
        # The one cleanup path. Reached from every ending above -- and
        # `broadcast_to_match` routes its own send failures through the same
        # `disconnect`, which is idempotent, so the two cannot double-release.
        manager.disconnect(match_id, websocket)
