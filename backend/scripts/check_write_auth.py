"""Report whether write endpoints are authorised, without starting the server.

`app/main.py` refuses to boot when neither `API_WRITE_KEY` nor
`ALLOW_OPEN_WRITES` is configured. That refusal is correct, but the launcher
spawns the backend into its own window, so an operator sees a window flash and
close — indistinguishable from a crash. `start.bat` needs to say the same thing
*before* spawning anything.

It cannot re-implement the condition: a second copy of "configured" drifts from
the first. Nor can it inline the check as a `python -c` one-liner inside a
`for /f` loop — that was tried, and cmd's quote handling turned the import into
`此时不应有 from`. So the check lives here, as a file, with an exit code.

Exit codes
    0  a write key is set, or open writes were explicitly enabled
    1  neither is set: the backend would fail closed at startup
    2  the settings could not be read at all (dependencies missing, most likely)

The key itself is never printed, only whether one is present.
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        from app.core.config import settings
    except Exception as exc:  # noqa: BLE001 - any import failure means "unknown"
        print(f"UNKNOWN: could not read settings ({type(exc).__name__}: {exc})")
        return 2

    if settings.API_WRITE_KEY:
        print("KEY: API_WRITE_KEY is configured; writes require X-API-Key.")
        return 0
    if settings.ALLOW_OPEN_WRITES:
        host = settings.SERVER_HOST
        print(
            "OPEN: ALLOW_OPEN_WRITES=true - write endpoints are PUBLIC "
            f"(no key). run.py binds {host}."
        )
        return 0
    print(
        "MISSING: neither API_WRITE_KEY nor ALLOW_OPEN_WRITES is set, so the "
        "backend will refuse to start."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
