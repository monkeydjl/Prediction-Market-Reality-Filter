"""Small helpers for storing safe audit-run provenance metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize_audit_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    default_source: str = "service",
) -> dict[str, str]:
    """Return bounded, non-secret audit metadata for loop run summaries."""

    metadata = metadata or {}
    trigger_source = _clean(metadata.get("trigger_source")) or default_source
    normalized = {"trigger_source": trigger_source}

    operator = _clean(metadata.get("operator"))
    if operator:
        normalized["operator"] = operator

    request_path = _clean(metadata.get("request_path"), max_length=200)
    if request_path:
        normalized["request_path"] = request_path

    return normalized


def _clean(value: Any, *, max_length: int = 80) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    return text[:max_length]
