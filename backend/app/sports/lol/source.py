from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Config may name commercial vendors (ADR-005 D4); only null/dry_run run today.
KNOWN_LOL_SCHEDULE_VENDORS = frozenset({"null", "dry_run", "grid", "pandascore"})
RUNTIME_LOL_SCHEDULE_VENDORS = frozenset({"null", "dry_run"})


@dataclass(frozen=True)
class LolSeriesRecord:
    external_id: str
    competition: str
    home_name: str
    away_name: str
    home_code: str
    away_code: str
    kickoff_utc: datetime
    best_of: int
    stage: str
    status: str


class LolScheduleSource(Protocol):
    def list_upcoming(self) -> list[LolSeriesRecord]: ...

    def get_result(self, external_id: str) -> dict | None: ...


class NullLolScheduleSource:
    def list_upcoming(self) -> list[LolSeriesRecord]:
        return []

    def get_result(self, external_id: str) -> dict | None:
        return None


@dataclass(frozen=True)
class LolSourceResolution:
    """Outcome of vendor → schedule source resolution (no secrets)."""

    requested_vendor: str
    effective_vendor: str
    source: LolScheduleSource
    blocked: bool
    reason: str | None


def normalize_lol_schedule_vendor(raw: str | None) -> str:
    vendor = (raw or "null").strip().lower() or "null"
    if vendor not in KNOWN_LOL_SCHEDULE_VENDORS:
        return "null"
    return vendor


def resolve_lol_schedule_source(
    vendor: str | None = None,
    *,
    settings: Any | None = None,
) -> LolSourceResolution:
    """Map LOL_SCHEDULE_VENDOR to a runtime source.

    Production HTTP (grid/pandascore) is **not** implemented. Those ids force
    NullLolScheduleSource + blocked=True so misconfiguration cannot pretend a
    live partner feed exists (GATES P2/P3/P6).
    """
    if settings is None:
        from app.core.config import settings as default_settings

        settings = default_settings

    if vendor is None:
        raw = getattr(settings, "LOL_SCHEDULE_VENDOR", "null")
    else:
        raw = vendor
    requested = (str(raw or "null")).strip().lower() or "null"

    if requested not in KNOWN_LOL_SCHEDULE_VENDORS:
        logger.warning(
            "LOL_SCHEDULE_VENDOR=%r is unknown; using NullLolScheduleSource",
            requested,
        )
        return LolSourceResolution(
            requested_vendor=requested,
            effective_vendor="null",
            source=NullLolScheduleSource(),
            blocked=True,
            reason=f"unknown vendor {requested!r}; forced null",
        )

    if requested in RUNTIME_LOL_SCHEDULE_VENDORS:
        # dry_run still uses Null for partner HTTP; file import is LOL_DRY_RUN_IMPORT.
        reason = (
            None
            if requested == "null"
            else "dry_run uses Null schedule source; series load via LOL_DRY_RUN_IMPORT"
        )
        return LolSourceResolution(
            requested_vendor=requested,
            effective_vendor=requested,
            source=NullLolScheduleSource(),
            blocked=False,
            reason=reason,
        )

    logger.warning(
        "LOL_SCHEDULE_VENDOR=%s has no production HTTP client; "
        "using NullLolScheduleSource until GATES P2/P3/P6 (ADR-005)",
        requested,
    )
    return LolSourceResolution(
        requested_vendor=requested,
        effective_vendor="null",
        source=NullLolScheduleSource(),
        blocked=True,
        reason=(
            "production HTTP client not shipped; "
            "GATES P2/P3/P6 required for PartnerHttp"
        ),
    )
