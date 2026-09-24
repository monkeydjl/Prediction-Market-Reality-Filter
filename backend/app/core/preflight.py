"""Production configuration preflight.

`PMRF_ENV` had exactly one reader before this module: `_resolve_env_file`, which
picks the dotenv overlay. Nothing checked what the overlay actually set, so every
production-only requirement was enforced by a *template* -- and a template only
binds the operator who copies it. An overlay only overrides the keys it names, so
an omitted key silently inherits its development default. That is how
`OPENAPI_ENABLED=true`, `LLM_DAILY_COST_CAP_USD=0` and `ALLOW_OPEN_WRITES=true`
were all reachable inside a process that believed it was production.

The gate is deliberately production-only. Every value it rejects is the *correct*
value on a development box -- no write key, interactive docs, `--reload`, an
unlimited (and unused) cost cap on a keyless LLM route -- so enforcing these
everywhere would make the repo unusable locally.

Failure strings name the *setting*, never its value: this text lands in a crash
log and gets pasted into an issue tracker.
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

PRODUCTION = "production"

#: The connection caps the realtime socket enforces. Every one must be finite and
#: positive, because each is escapable alone: a per-match cap is bypassed by asking
#: for another match id, and a global cap alone lets one host fill the budget and
#: lock every other operator out. Declared as data so the posture below iterates
#: the real settings instead of naming a subset that silently stops matching.
REALTIME_CONNECTION_CAP_SETTINGS = (
    "WEBSOCKET_MAX_CONNECTIONS_TOTAL",
    "WEBSOCKET_MAX_CONNECTIONS_PER_MATCH",
    "WEBSOCKET_MAX_CONNECTIONS_PER_CLIENT",
)


def _realtime_authenticated() -> bool:
    """Whether the realtime handshake actually refuses an anonymous socket.

    This was a module constant reading `False`, with a comment saying to flip it
    to `True` once auth existed. That is a claim about the code, and a claim can
    be edited without the code changing -- flipping it would have opened the
    production gate over an endpoint that still accepted everyone.

    So it delegates to the enforcing module. `ws_auth.auth_is_enforced()` reads
    the same `API_WRITE_KEY` the handshake reads, which means an operator cannot
    reach a state where the posture says "authenticated" and the socket disagrees.
    `tests/test_realtime_ws_hardening.py` additionally pins that the route calls
    the authenticator at all.
    """
    from app.realtime import ws_auth

    return ws_auth.auth_is_enforced()


def _realtime_connection_limited() -> bool:
    """Whether every connection cap is a real, finite bound.

    A non-positive cap is treated as unset rather than as "unlimited": the
    manager refuses at `>= max(0, cap)`, so 0 means "accept nothing", and an
    operator who wrote 0 meaning "no limit" gets a refusal to start rather than
    the unbounded endpoint they were asking for by accident.
    """
    for name in REALTIME_CONNECTION_CAP_SETTINGS:
        try:
            value = int(getattr(settings, name))
        except (TypeError, ValueError):
            return False
        if value <= 0:
            return False
    return True


def is_production() -> bool:
    """True when this process is configured as production.

    Case- and whitespace-insensitive because the value reaches us from a dotenv
    file or a compose `environment:` block, where `Production` and a trailing
    space are both ordinary typos.
    """
    return str(settings.PMRF_ENV or "").strip().lower() == PRODUCTION


def realtime_push_posture() -> dict[str, bool]:
    """What the realtime WebSocket surface does and does not enforce."""
    return {
        "enabled": bool(settings.PHASE10_REALTIME_PUSH_ENABLED),
        "authenticated": _realtime_authenticated(),
        "connection_limited": _realtime_connection_limited(),
    }


def log_realtime_push_posture() -> None:
    """State the realtime posture out loud at startup.

    "Do not silently default it on" also means: when it *is* on, say what it does
    not enforce. Logged rather than raised outside production so a dev box can
    exercise the socket.
    """
    posture = realtime_push_posture()
    if not posture["enabled"]:
        logger.info(
            "Realtime WebSocket push is disabled (PHASE10_REALTIME_PUSH_ENABLED"
            "=false); /ws/matches/{id}/prices closes every connection."
        )
        return
    unmet = [
        name
        for name, ok in (
            ("handshake auth (API_WRITE_KEY is unset, so the socket has no "
             "credential to check and accepts anonymous callers)",
             posture["authenticated"]),
            ("a finite connection cap (one of "
             + ", ".join(REALTIME_CONNECTION_CAP_SETTINGS)
             + " is not a positive number)",
             posture["connection_limited"]),
        )
        if not ok
    ]
    if unmet:
        logger.warning(
            "PHASE10_REALTIME_PUSH_ENABLED=true and the realtime WebSocket does "
            "NOT satisfy: %s. Anyone who can reach the port can open unbounded "
            "streams of live odds. Refused outright when PMRF_ENV=production.",
            "; ".join(unmet),
        )
    else:
        logger.info(
            "PHASE10_REALTIME_PUSH_ENABLED=true; the handshake requires a key or "
            "a ticket and all three connection caps are finite."
        )


def _cost_cap_failure() -> str | None:
    """LLM_DAILY_COST_CAP_USD must be a number strictly above zero.

    `_cost_cap_exceeded` treats any non-positive cap as "no ceiling", which is the
    shipped default and correct for a keyless dev box. On a paid key in production
    it means unattended, unbounded spend.
    """
    raw = settings.LLM_DAILY_COST_CAP_USD
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        return (
            "LLM_DAILY_COST_CAP_USD is not a number. Set a positive USD amount; "
            "production must run with a daily LLM spend ceiling."
        )
    if cap <= 0:
        return (
            "LLM_DAILY_COST_CAP_USD must be greater than 0 in production "
            "(0 or negative disables the ceiling and lets daily LLM spend run "
            "unbounded)."
        )
    return None


def _cors_failure() -> str | None:
    origins = [str(item).strip() for item in (settings.CORS_ALLOWED_ORIGINS or [])]
    origins = [item for item in origins if item]
    if not origins:
        return (
            "CORS_ALLOWED_ORIGINS is empty. Set the browser origin(s) this API "
            "serves, comma-separated; an empty list rejects every cross-origin "
            "request, so the dashboard cannot reach the API at all."
        )
    if "*" in origins:
        return (
            "CORS_ALLOWED_ORIGINS contains '*'. List explicit production "
            "origins; a wildcard lets any site read authenticated responses."
        )
    return None


def production_config_failures() -> list[str]:
    """Every unmet production requirement, or an empty list.

    All rules are evaluated -- the caller reports them together, because failing
    on the first would make an operator restart once per misconfigured key.
    Returns an empty list unconditionally outside production.
    """
    if not is_production():
        return []

    failures: list[str] = []

    if not str(settings.API_WRITE_KEY or "").strip():
        failures.append(
            "API_WRITE_KEY is not configured. Every write endpoint (manual "
            "resolve, auto-resolve, discovery that spends LLM budget) would be "
            "unauthenticated."
        )
    if settings.ALLOW_OPEN_WRITES:
        failures.append(
            "ALLOW_OPEN_WRITES must be false in production. It is the explicit "
            "opt-in to keyless write endpoints and exists for local development; "
            "set it false and configure API_WRITE_KEY."
        )
    cost_cap = _cost_cap_failure()
    if cost_cap:
        failures.append(cost_cap)
    if settings.OPENAPI_ENABLED:
        failures.append(
            "OPENAPI_ENABLED must be false in production. /openapi.json, /docs "
            "and /redoc publish the full schema, including every write operation "
            "and the header names it expects."
        )
    if settings.SERVER_RELOAD:
        failures.append(
            "SERVER_RELOAD must be false in production. Reload re-executes the "
            "app on file changes and runs an extra supervising process."
        )
    if settings.BACKUP_SCHEDULE_ENABLED and not str(
        settings.BACKUP_ENCRYPTION_KEY or ""
    ).strip():
        failures.append(
            "BACKUP_SCHEDULE_ENABLED=true requires a non-empty "
            "BACKUP_ENCRYPTION_KEY in production. Without it the scheduled job "
            "writes a plaintext ZIP of every state store (events, predictions, "
            "kernel history) into the backup volume."
        )
    cors = _cors_failure()
    if cors:
        failures.append(cors)
    posture = realtime_push_posture()
    if posture["enabled"] and not (
        posture["authenticated"] and posture["connection_limited"]
    ):
        missing = []
        if not posture["authenticated"]:
            missing.append(
                "handshake authentication (API_WRITE_KEY is what the socket "
                "checks, and it is not configured)"
            )
        if not posture["connection_limited"]:
            missing.append(
                "a finite connection cap (every one of "
                + ", ".join(REALTIME_CONNECTION_CAP_SETTINGS)
                + " must be greater than 0; 0 means 'accept nothing', never "
                "'unlimited')"
            )
        failures.append(
            "PHASE10_REALTIME_PUSH_ENABLED=true, but the realtime WebSocket is "
            "missing " + "; ".join(missing) + ". /api/ws/matches/{id}/prices "
            "streams live bookmaker odds and Kalshi prices, so it cannot be "
            "enabled in production without both."
        )
    return failures


def validate_production_config() -> None:
    """Raise when this production process is misconfigured. No-op elsewhere.

    Called from every entrypoint that reads this configuration in production: the
    API lifespan, `scripts/run_scheduler.py` (the process that spends LLM budget
    unattended and writes the backups) and the backup script. A gate on the API
    alone would leave the other two ungated.
    """
    failures = production_config_failures()
    if not failures:
        if is_production():
            logger.info(
                "Production preflight passed (%d checks).",
                len(_CHECK_NAMES),
            )
        return
    numbered = "\n".join(f"  {i}. {item}" for i, item in enumerate(failures, 1))
    raise RuntimeError(
        f"PMRF_ENV=production but {len(failures)} configuration requirement(s) "
        f"are not met:\n{numbered}\n"
        "Fix these in the .env.production overlay (or the process environment) "
        "and restart. See docs/ops/RUNBOOK.md 'Production preflight'."
    )


# Only used for the count in the pass log; kept next to the rules so it moves
# with them.
_CHECK_NAMES = (
    "API_WRITE_KEY",
    "ALLOW_OPEN_WRITES",
    "LLM_DAILY_COST_CAP_USD",
    "OPENAPI_ENABLED",
    "SERVER_RELOAD",
    "BACKUP_ENCRYPTION_KEY",
    "CORS_ALLOWED_ORIGINS",
    "PHASE10_REALTIME_PUSH_ENABLED",
)
