"""Optional, configured secondary weather provider for football fixtures.

This is the second independent source behind the P1-F7 multi-source weather
gap. The primary source stays in ``app.sports.football.football_weather``
(Open-Meteo shaped, keyless); this service talks to a *different*, licensed
provider that must expose an already-normalized envelope:
``{"weather": {"temp_c": 17.4, "condition": "rain"}}``.

Disabled by default. With ``FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED`` false,
or a missing URL/key, no outbound request is made and the caller keeps the
single-source behaviour byte for byte. Any transport, size, decode, or
validation failure yields an unavailable result rather than an exception, so
weather enrichment degrades to primary-only and then to static climate.

API keys are sent only in the ``Authorization`` header; keys and raw response
bodies are never logged.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.core.config import settings

# Shared condition vocabulary. Kept in sync with football_weather._CONDITIONS;
# duplicated here so the service has no import cycle back into the sport layer.
_CONDITIONS = frozenset({"clear", "mild", "rain", "cold", "hot"})

# Widest physically plausible surface reading accepted from a provider. The
# consumer clamps to the narrower feature band; anything outside this range is
# treated as a malformed payload.
_TEMP_LOWER_C = -60.0
_TEMP_UPPER_C = 60.0


@dataclass(frozen=True)
class LiveWeatherResult:
    """A lookup result distinguishing provider availability from missing data."""

    available: bool
    temp_c: float | None = None
    condition: str | None = None


@dataclass(frozen=True)
class _CachedReading:
    fetched_at: float
    reading: tuple[float, str] | None


_READING_CACHE: dict[tuple[float, float, str], _CachedReading] = {}


def get_secondary_weather(
    latitude: float | None,
    longitude: float | None,
    match_date: str | None,
) -> LiveWeatherResult:
    """Return a secondary-provider reading, or report unavailability.

    ``match_date`` is the fixture's UTC calendar date (``YYYY-MM-DD``); it is
    passed through to the provider and forms part of the cache key.
    """
    point = _coordinates(latitude, longitude)
    day = _date_key(match_date)
    if point is None or day is None or not _is_configured():
        return LiveWeatherResult(available=False)

    reading = _reading(point, day)
    if reading is None:
        return LiveWeatherResult(available=False)
    temp_c, condition = reading
    return LiveWeatherResult(available=True, temp_c=temp_c, condition=condition)


def clear_secondary_weather_cache() -> None:
    """Clear cached provider readings for deterministic tests."""
    _READING_CACHE.clear()


def _is_configured() -> bool:
    return bool(
        settings.FOOTBALL_LIVE_WEATHER_SECONDARY_ENABLED
        and str(settings.FOOTBALL_LIVE_WEATHER_SECONDARY_URL or "").strip()
        and str(settings.FOOTBALL_LIVE_WEATHER_SECONDARY_API_KEY or "").strip()
    )


def _coordinates(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    lat = _number(latitude, -90.0, 90.0)
    lon = _number(longitude, -180.0, 180.0)
    if lat is None or lon is None:
        return None
    return (round(lat, 2), round(lon, 2))


def _date_key(match_date: Any) -> str | None:
    value = str(match_date or "").strip()
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return None
    year, month, day = value[:4], value[5:7], value[8:10]
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return None
    if not 1 <= int(month) <= 12 or not 1 <= int(day) <= 31:
        return None
    return value


def _reading(point: tuple[float, float], day: str) -> tuple[float, str] | None:
    key = (point[0], point[1], day)
    now = time.monotonic()
    try:
        ttl_seconds = (
            max(0.0, float(settings.FOOTBALL_LIVE_WEATHER_SECONDARY_CACHE_TTL_HOURS)) * 3600
        )
    except (TypeError, ValueError):
        return None
    cached = _READING_CACHE.get(key)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.reading

    url = _request_url(point, day)
    if url is None:
        return None
    try:
        timeout = max(0.1, float(settings.FOOTBALL_LIVE_WEATHER_SECONDARY_TIMEOUT_S))
        max_bytes = int(settings.FOOTBALL_LIVE_WEATHER_SECONDARY_MAX_BYTES)
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    api_key = str(settings.FOOTBALL_LIVE_WEATHER_SECONDARY_API_KEY or "").strip()
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
    reading = _parse_reading(payload)
    if reading is None:
        return None
    # Only valid readings are cached; failures stay uncached so a transient
    # provider fault does not pin an unavailable answer for the whole TTL.
    _READING_CACHE[key] = _CachedReading(fetched_at=now, reading=reading)
    return reading


def _request_url(point: tuple[float, float], day: str) -> str | None:
    raw_url = str(settings.FOOTBALL_LIVE_WEATHER_SECONDARY_URL or "").strip()
    if not raw_url:
        return None
    split = urlsplit(raw_url)
    if split.scheme not in {"http", "https"} or not split.netloc:
        return None
    reserved = {"latitude", "longitude", "date"}
    query_items = [
        (name, value)
        for name, value in parse_qsl(split.query, keep_blank_values=True)
        if name not in reserved
    ]
    query_items.extend(
        (
            ("latitude", f"{point[0]:.2f}"),
            ("longitude", f"{point[1]:.2f}"),
            ("date", day),
        )
    )
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query_items), ""))


def _parse_reading(payload: Any) -> tuple[float, str] | None:
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    row = payload.get("weather")
    if not isinstance(row, dict):
        return None
    temp_c = _number(row.get("temp_c"), _TEMP_LOWER_C, _TEMP_UPPER_C)
    condition = _condition(row.get("condition"))
    if temp_c is None or condition is None:
        return None
    return (round(temp_c, 1), condition)


def _condition(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.lower().split())
    return normalized if normalized in _CONDITIONS else None


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
