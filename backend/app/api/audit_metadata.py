"""FastAPI dependency for safe audit-run provenance metadata."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, Request

from app.services.audit_metadata import normalize_audit_metadata


def get_audit_metadata(
    request: Request,
    x_client_source: Annotated[str | None, Header(alias="X-Client-Source")] = None,
    x_operator: Annotated[str | None, Header(alias="X-Operator")] = None,
) -> dict[str, str]:
    return normalize_audit_metadata(
        {
            "trigger_source": x_client_source or "api",
            "operator": x_operator,
            "request_path": request.url.path,
        },
        default_source="api",
    )
