"""Read-only LLM gateway diagnostics routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services.llm_gateway_diagnostics_service import build_llm_diagnostics

router = APIRouter()


@router.get("/diagnostics")
async def llm_diagnostics() -> dict[str, Any]:
    """Return redacted LLM gateway route/configuration diagnostics."""
    return build_llm_diagnostics()
