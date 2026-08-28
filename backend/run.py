"""Local development entrypoint.

Production does not use this file: `deploy/Dockerfile` (CMD) and
`deploy/prediction-market-reality-filter.service` (ExecStart) both invoke
`uvicorn` directly with an explicit `--host`. Only `start.bat`,
`backend/start.bat` and the two READMEs run it.

That distinction is why the bind guard lives here rather than in `app/main.py`.
Under the uvicorn CLI the application cannot observe the address it was bound to,
so a check inside `main.py` would be comparing `settings.SERVER_HOST` against a
value that had no influence on the actual socket — a guard whose input is not the
thing it claims to measure. Here, the setting *is* the bind address.
"""
import ipaddress
import sys

import uvicorn

from app.core.config import settings


def _is_loopback(host: str) -> bool:
    """Whether `host` reaches only this machine.

    The empty string and `*` are uvicorn spellings of "all interfaces". Anything
    unparseable is treated as non-loopback: an unknown value must not be assumed
    safe.
    """
    normalized = host.strip()
    if normalized in ("", "*", "0.0.0.0", "::"):
        return False
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _check_bind_is_safe() -> None:
    """Refuse to expose unauthenticated write endpoints beyond this machine.

    `app/main.py` already fail-closes when neither `API_WRITE_KEY` nor
    `ALLOW_OPEN_WRITES` is set. But `ALLOW_OPEN_WRITES=true` is documented as
    "local dev only", and nothing enforced the "local" half — so the intended
    dev configuration published keyless writes, including LLM-spending endpoints
    (`/events/discover`, analyze), to the whole network.
    """
    if settings.API_WRITE_KEY or not settings.ALLOW_OPEN_WRITES:
        return
    if _is_loopback(settings.SERVER_HOST):
        return
    sys.exit(
        "\n".join(
            [
                "Refusing to start: unauthenticated writes on a non-local address.",
                "",
                f"  SERVER_HOST       = {settings.SERVER_HOST!r} (reachable from other machines)",
                "  ALLOW_OPEN_WRITES = true (no X-API-Key required)",
                "",
                "That combination publishes every mutating endpoint, including the",
                "ones that spend LLM budget, to anyone who can reach this host.",
                "",
                "Pick one:",
                "  - SERVER_HOST=127.0.0.1   keep writes keyless, local only (dev default)",
                "  - API_WRITE_KEY=<secret>  require a key, then any bind address is fine",
                "",
                "Generate a key:  python -c \"import secrets;print(secrets.token_urlsafe(32))\"",
            ]
        )
    )


if __name__ == "__main__":
    _check_bind_is_safe()
    uvicorn.run(
        "app.main:app",
        host=settings.SERVER_HOST,
        port=8000,
        reload=settings.SERVER_RELOAD,
    )
