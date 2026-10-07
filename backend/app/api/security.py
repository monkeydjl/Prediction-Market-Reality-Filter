import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def is_write_key_valid(x_api_key: str | None) -> bool:
    # compare_digest() rejects `str` operands that hold non-ASCII characters
    # with a TypeError, and both operands here are controlled from outside this
    # module (the request header, and API_WRITE_KEY): a single byte >= 0x80 in
    # either one used to escape the auth dependency instead of answering -- see
    # WriteKeyNonAsciiTests. The UTF-8 bytes are equally constant-time and carry
    # no such restriction. (A lone surrogate would raise UnicodeEncodeError, but
    # HTTP header values decode as latin-1, so it cannot arrive from a request.)
    return bool(settings.API_WRITE_KEY) and hmac.compare_digest(
        (x_api_key or "").encode("utf-8"),
        settings.API_WRITE_KEY.encode("utf-8"),
    )


async def require_write_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.API_WRITE_KEY:
        # No key configured. Only allow writes if the operator explicitly opted
        # into public writes via ALLOW_OPEN_WRITES=true. This is a runtime
        # second-check that complements the startup guard in app/main.py —
        # if someone flips the env var and reloads mid-run without restarting,
        # this still refuses to open the write surface.
        if settings.ALLOW_OPEN_WRITES:
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Write API key not configured (set API_WRITE_KEY or ALLOW_OPEN_WRITES=true)",
        )
    # Constant-time comparison to avoid leaking the key via response timing.
    if not is_write_key_valid(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
        )


async def optional_write_key(x_api_key: str | None = Header(default=None)) -> bool:
    """Return True if the write key is valid, False otherwise.

    Unlike ``require_write_key`` this never raises — callers decide whether to
    enforce auth based on additional context (e.g. read-only operations).
    """
    if not settings.API_WRITE_KEY:
        return settings.ALLOW_OPEN_WRITES
    return is_write_key_valid(x_api_key)
