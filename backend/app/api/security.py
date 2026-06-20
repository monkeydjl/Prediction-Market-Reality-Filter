from fastapi import Header, HTTPException, status

from app.core.config import settings


async def require_write_key(x_api_key: str | None = Header(default=None)):
    if not settings.API_WRITE_KEY:
        return
    if x_api_key != settings.API_WRITE_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
        )
