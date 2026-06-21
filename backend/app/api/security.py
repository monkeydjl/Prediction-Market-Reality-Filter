import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def is_write_key_valid(x_api_key: str | None) -> bool:
    return bool(settings.API_WRITE_KEY) and hmac.compare_digest(
        x_api_key or "",
        settings.API_WRITE_KEY,
    )


async def require_write_key(x_api_key: str | None = Header(default=None)):
    if not settings.API_WRITE_KEY:
        # No key configured -> pass through. This is only reachable in a running
        # app because the startup guard (app.main.lifespan) refuses to boot with
        # an empty key UNLESS ALLOW_OPEN_WRITES was explicitly set, so an empty
        # key here means the operator opted into public writes. The guard is the
        # enforcement point; see app/main.py.
        return
    # Constant-time comparison to avoid leaking the key via response timing.
    if not is_write_key_valid(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
        )
