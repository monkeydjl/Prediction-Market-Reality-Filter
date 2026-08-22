"""Optional, configured true referee-stat provider for football competitions.

The provider must expose a normalized season snapshot:
``{"referees": [{"referee": "Michael Oliver", "home_win_rate": 0.54,
"matches": 24}]}``.
"""
from __future__ import annotations

import json
import math
import time
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.core.config import settings


@dataclass(frozen=True)
class LiveRefereeResult:
    """A lookup result distinguishing provider availability from missing rows."""

    available: bool
    home_win_rate: float | None = None
    matches: int | None = None


@dataclass(frozen=True)
class _CachedSnapshot:
    fetched_at: float
    referees: dict[str, tuple[float, int]]


_SNAPSHOT_CACHE: dict[tuple[str, str], _CachedSnapshot] = {}


def get_live_referee(
    competition: str | None,
    season: str | None,
    referee_name: str,
) -> LiveRefereeResult:
    """Return a season referee statistic or report provider unavailability."""
    competition_code = str(competition or "").strip().lower()
    season_year = _season_year(season)
    referee_key = _referee_key(referee_name)
    if not competition_code or not season_year or not referee_key or not _is_configured():
        return LiveRefereeResult(available=False)

    snapshot = _snapshot(competition_code, season_year)
    if snapshot is None:
        return LiveRefereeResult(available=False)
    row = snapshot.get(referee_key)
    if row is None:
        return LiveRefereeResult(available=True)
    rate, matches = row
    return LiveRefereeResult(available=True, home_win_rate=rate, matches=matches)


def clear_live_referee_cache() -> None:
    """Clear cached provider snapshots for deterministic tests."""
    _SNAPSHOT_CACHE.clear()


def _is_configured() -> bool:
    return bool(
        settings.FOOTBALL_LIVE_REFEREE_ENABLED
        and str(settings.FOOTBALL_LIVE_REFEREE_URL or "").strip()
        and str(settings.FOOTBALL_LIVE_REFEREE_API_KEY or "").strip()
    )


def _season_year(season: str | None) -> str | None:
    value = str(season or "").strip()
    year = value[:4]
    return year if len(year) == 4 and year.isdigit() else None


def _snapshot(competition: str, season_year: str) -> dict[str, tuple[float, int]] | None:
    key = (competition, season_year)
    now = time.monotonic()
    try:
        ttl_seconds = max(0.0, float(settings.FOOTBALL_LIVE_REFEREE_CACHE_TTL_HOURS)) * 3600
    except (TypeError, ValueError):
        return None
    cached = _SNAPSHOT_CACHE.get(key)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.referees

    url = _request_url(competition, season_year)
    if url is None:
        return None
    try:
        timeout = max(0.1, float(settings.FOOTBALL_LIVE_REFEREE_TIMEOUT_S))
        max_bytes = int(settings.FOOTBALL_LIVE_REFEREE_MAX_BYTES)
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    api_key = str(settings.FOOTBALL_LIVE_REFEREE_API_KEY or "").strip()
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
    referees = _parse_snapshot(payload)
    if referees is None:
        return None
    _SNAPSHOT_CACHE[key] = _CachedSnapshot(fetched_at=now, referees=referees)
    return referees


def _request_url(competition: str, season_year: str) -> str | None:
    raw_url = str(settings.FOOTBALL_LIVE_REFEREE_URL or "").strip()
    season_param = str(settings.FOOTBALL_LIVE_REFEREE_SEASON_PARAM or "").strip()
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


def _parse_snapshot(payload: Any) -> dict[str, tuple[float, int]] | None:
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    rows = payload.get("referees")
    if not isinstance(rows, list):
        return None
    referees: dict[str, tuple[float, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        referee_key = _referee_key(row.get("referee"))
        if not referee_key or referee_key in referees:
            return None
        rate = _number(row.get("home_win_rate"), 0.0, 1.0)
        matches = _integer(row.get("matches"), 1, 100)
        if rate is None or matches is None:
            return None
        referees[referee_key] = (round(rate, 4), matches)
    return referees


def _number(value: Any, lower: float, upper: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or not lower <= numeric <= upper:
        return None
    return numeric


def _integer(value: Any, lower: int, upper: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric != int(numeric):
        return None
    integer = int(numeric)
    return integer if lower <= integer <= upper else None


def _referee_key(name: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(name or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = "".join(char.lower() if char.isalnum() else " " for char in normalized).split()
    return " ".join(tokens)
