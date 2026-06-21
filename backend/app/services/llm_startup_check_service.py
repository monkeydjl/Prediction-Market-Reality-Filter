"""Startup-time primary LLM connectivity check."""

from openai import AsyncOpenAI

from app.core.config import settings


async def validate_primary_llm_startup() -> None:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is empty; primary LLM startup check failed.")

    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.DASHSCOPE_BASE_URL,
        timeout=settings.LLM_STARTUP_CHECK_TIMEOUT_SECONDS,
        max_retries=0,
    )
    try:
        await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Reply with ok."},
                {"role": "user", "content": "ok"},
            ],
            temperature=0,
            max_tokens=1,
        )
    except Exception as exc:
        detail = str(exc).replace(settings.OPENAI_API_KEY, "<redacted>")
        if len(detail) > 300:
            detail = detail[:300] + "..."
        raise RuntimeError(
            f"Primary LLM startup check failed: {type(exc).__name__}: {detail}"
        ) from exc
