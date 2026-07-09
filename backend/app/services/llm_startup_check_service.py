"""Startup-time primary LLM connectivity check."""

from app.core.config import settings
from app.services.llm_gateway_service import complete_chat


async def validate_primary_llm_startup() -> None:
    try:
        result = await complete_chat(
            task="startup_check",
            messages=[
                {"role": "system", "content": "Reply with ok."},
                {"role": "user", "content": "ok"},
            ],
            temperature=0,
            max_tokens=1,
        )
    except Exception as exc:
        detail = str(exc)
        if settings.OPENAI_API_KEY:
            detail = detail.replace(settings.OPENAI_API_KEY, "<redacted>")
        if len(detail) > 300:
            detail = detail[:300] + "..."
        raise RuntimeError(
            f"Primary LLM startup check failed: {type(exc).__name__}: {detail}"
        ) from exc
    if not result.ok:
        errors = [
            attempt.error_type
            for attempt in (result.attempts or [])
            if attempt.status != "success" and attempt.error_type
        ]
        detail = result.degraded_reason or "LLM unavailable"
        if errors:
            detail = f"{detail}: {', '.join(errors[:5])}"
        raise RuntimeError(f"Primary LLM startup check failed: {detail}")
