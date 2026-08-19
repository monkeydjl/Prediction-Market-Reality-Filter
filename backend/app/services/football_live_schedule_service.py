"""Optional, configured live fixture history for football schedule density.

The provider must expose a normalized competition-season snapshot:
``{"fixtures": [{"match_id": "provider-1", "home_team": "Arsenal",
"away_team": "Chelsea", "kickoff_utc": "2026-08-16T15:00:00Z",
"status": "scheduled"}]}``.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.core.config import settings

_ALLOWED_STATUSES = {
    "scheduled", "in_play", "finished", "postponed", "cancelled", "suspended",
}


@dataclass(frozen=True)
class LiveScheduleResult:
    """A lookup result that distinguishes provider availability from empty data."""

    available: bool
    fixtures: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class _CachedSnapshot:
    fetched_at: float
    fixtures: list[dict[str, Any]]


_SNAPSHOT_CACHE: dict[tuple[str, str], _CachedSnapshot] = {}


def get_live_schedule(
    competition: str | None,
    season: str | None,
    before: datetime | None = None,
) -> LiveScheduleResult:
    """Return a bounded preceding-fixture history or provider unavailability."""
    competition_code = str(competition or "").strip().lower()
    season_year = _season_year(season)
    if not competition_code or not season_year or not _is_configured():
        return LiveScheduleResult(available=False)

    snapshot = _snapshot(competition_code, season_year)
    if snapshot is None:
        return LiveScheduleResult(available=False)
    return LiveScheduleResult(
        available=True,
        fixtures=_filter_history(snapshot, before),
    )


def clear_live_schedule_cache() -> None:
    """Clear cached provider snapshots for deterministic tests."""
    _SNAPSHOT_CACHE.clear()


def _is_configured() -> bool:
    return bool(
        settings.FOOTBALL_LIVE_SCHEDULE_ENABLED
        and str(settings.FOOTBALL_LIVE_SCHEDULE_URL or "").strip()
        and str(settings.FOOTBALL_LIVE_SCHEDULE_API_KEY or "").strip()
    )


def _season_year(season: str | None) -> str | None:
    value = str(season or "").strip()
    year = value[:4]
    return year if len(year) == 4 and year.isdigit() else None


def _snapshot(competition: str, season_year: str) -> list[dict[str, Any]] | None:
    key = (competition, season_year)
    now = time.monotonic()
    try:
        ttl_seconds = max(0.0, float(settings.FOOTBALL_LIVE_SCHEDULE_CACHE_TTL_HOURS)) * 3600
    except (TypeError, ValueError):
        return None
    cached = _SNAPSHOT_CACHE.get(key)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.fixtures

    url = _request_url(competition, season_year)
    if url is None:
        return None
    try:
        timeout = max(0.1, float(settings.FOOTBALL_LIVE_SCHEDULE_TIMEOUT_S))
        max_bytes = int(settings.FOOTBALL_LIVE_SCHEDULE_MAX_BYTES)
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    api_key = str(settings.FOOTBALL_LIVE_SCHEDULE_API_KEY or "").strip()
    request = Request(
        url,
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if len(body) > max_bytes:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    fixtures = _parse_snapshot(payload)
    if fixtures is None:
        return None
    _SNAPSHOT_CACHE[key] = _CachedSnapshot(fetched_at=now, fixtures=fixtures)
    return fixtures


def _request_url(competition: str, season_year: str) -> str | None:
    raw_url = str(settings.FOOTBALL_LIVE_SCHEDULE_URL or "").strip()
    season_param = str(settings.FOOTBALL_LIVE_SCHEDULE_SEASON_PARAM or "").strip()
    if not raw_url or not season_param or season_param == "competition":
        return None
    split = urlsplit(raw_url)
    if split.scheme not in {"http", "https"} or not split.netloc:
        return None
    query_items = [
        (name, value)
        for name, value in parse_qsl(split.query, keep_blank_values=True)
        if name not in {"competition", season_param}
    ]
    query_items.extend((("competition", competition), (season_param, season_year)))
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query_items), ""))


def _parse_snapshot(payload: Any) -> list[dict[str, Any]] | None:
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    rows = payload.get("fixtures")
    if not isinstance(rows, list):
        return None
    fixtures: list[dict[str, Any]] = []
    match_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            return None
        match_id = str(row.get("match_id") or "").strip()
        home_team = str(row.get("home_team") or "").strip()
        away_team = str(row.get("away_team") or "").strip()
        kickoff = _parse_kickoff(row.get("kickoff_utc"))
        status = str(row.get("status") or "").strip().lower()
        if (
            not match_id
            or match_id in match_ids
            or not home_team
            or not away_team
            or home_team.casefold() == away_team.casefold()
            or kickoff is None
            or status not in _ALLOWED_STATUSES
        ):
            return None
        match_ids.add(match_id)
        fixtures.append(
            {
                "match_id": match_id,
                "home_team": home_team,
                "away_team": away_team,
                "kickoff_utc": kickoff,
                "status": status,
            }
        )
    return fixtures


def _parse_kickoff(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _filter_history(
    fixtures: list[dict[str, Any]],
    before: datetime | None,
) -> list[dict[str, Any]]:
    if before is None:
        return list(fixtures)
    if before.tzinfo is None:
        return []
    try:
        days = max(1, int(settings.FOOTBALL_LIVE_SCHEDULE_HISTORY_DAYS))
    except (TypeError, ValueError):
        return []
    cutoff = before.astimezone(timezone.utc)
    lower = cutoff - timedelta(days=days)
    return [
        fixture
        for fixture in fixtures
        if lower <= fixture["kickoff_utc"] < cutoff
    ]
