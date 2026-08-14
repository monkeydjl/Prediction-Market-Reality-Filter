"""Football home-city weather (P1-F7 residual).

Two layers:
- ``climate_for_home`` — soft multi-year climate priors by month (not live
  forecasts). Missing / empty / bad month → None. Final fallback in the
  adapter, tagged ``static_climate``.
- ``live_weather_for_match`` — optional live forecast fill (provider behind
  ``FOOTBALL_LIVE_WEATHER_URL``; disabled until configured). Returns
  ``{"weather_temp_c", "weather_condition"}`` normalized to the adapter field
  names, or None on any failure. Never raises into the adapter.

MultiFactor does not consume weather this round.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from app.core.config import settings

if TYPE_CHECKING:
    from app.kernel.domain import MatchIdentity

logger = logging.getLogger(__name__)

_CONDITIONS = frozenset({"clear", "mild", "rain", "cold", "hot"})

# Seasonal templates: (DJF, MAM, JJA, SON) each (temp_c, condition)
_TEMPLATES: dict[str, tuple[tuple[float, str], tuple[float, str], tuple[float, str], tuple[float, str]]] = {
    "london": ((5.0, "rain"), (11.0, "mild"), (18.0, "mild"), (12.0, "rain")),
    "manchester": ((4.0, "rain"), (10.0, "mild"), (17.0, "mild"), (11.0, "rain")),
    "liverpool": ((5.0, "rain"), (10.0, "mild"), (17.0, "mild"), (11.0, "rain")),
    "birmingham": ((4.5, "rain"), (10.5, "mild"), (17.5, "mild"), (11.5, "rain")),
    "newcastle": ((4.0, "cold"), (9.0, "mild"), (16.0, "mild"), (10.0, "rain")),
    "madrid": ((7.0, "mild"), (14.0, "clear"), (28.0, "hot"), (16.0, "clear")),
    "barcelona": ((10.0, "mild"), (15.0, "mild"), (26.0, "hot"), (18.0, "clear")),
    "seville": ((12.0, "mild"), (17.0, "clear"), (30.0, "hot"), (20.0, "clear")),
    "bilbao": ((9.0, "rain"), (13.0, "mild"), (21.0, "mild"), (15.0, "rain")),
    "milan": ((4.0, "cold"), (13.0, "mild"), (25.0, "hot"), (14.0, "mild")),
    "rome": ((8.0, "mild"), (14.0, "mild"), (27.0, "hot"), (17.0, "clear")),
    "naples": ((10.0, "mild"), (15.0, "mild"), (27.0, "hot"), (18.0, "clear")),
    "turin": ((3.0, "cold"), (12.0, "mild"), (24.0, "hot"), (13.0, "mild")),
    "munich": ((0.0, "cold"), (10.0, "mild"), (19.0, "mild"), (10.0, "rain")),
    "dortmund": ((2.0, "cold"), (10.0, "mild"), (19.0, "mild"), (11.0, "rain")),
    "leipzig": ((0.5, "cold"), (10.0, "mild"), (20.0, "mild"), (10.5, "rain")),
    "paris": ((5.0, "rain"), (12.0, "mild"), (21.0, "mild"), (13.0, "mild")),
    "marseille": ((9.0, "mild"), (14.0, "clear"), (26.0, "hot"), (17.0, "clear")),
    "lyon": ((4.0, "cold"), (12.0, "mild"), (23.0, "hot"), (13.0, "mild")),
    "amsterdam": ((4.0, "rain"), (10.0, "mild"), (18.0, "mild"), (11.0, "rain")),
    "lisbon": ((12.0, "mild"), (15.0, "mild"), (24.0, "hot"), (18.0, "clear")),
    "porto": ((10.0, "rain"), (14.0, "mild"), (21.0, "mild"), (16.0, "rain")),
    "glasgow": ((3.0, "cold"), (8.0, "rain"), (15.0, "mild"), (9.0, "rain")),
    "istanbul": ((6.0, "cold"), (12.0, "mild"), (24.0, "hot"), (15.0, "mild")),
}


def _months_from_template(
    tpl: tuple[tuple[float, str], tuple[float, str], tuple[float, str], tuple[float, str]],
) -> list[tuple[float, str]]:
    """Expand DJF/MAM/JJA/SON into 12 (temp, condition) rows (Jan=1 index 0)."""
    djf, mam, jja, son = tpl
    out: list[tuple[float, str]] = []
    for m in range(1, 13):
        if m in (12, 1, 2):
            out.append(djf)
        elif m in (3, 4, 5):
            out.append(mam)
        elif m in (6, 7, 8):
            out.append(jja)
        else:
            out.append(son)
    return out


# club normalize key → template name
_CLUB_TEMPLATE: dict[str, str] = {
    # EPL / England
    "arsenal": "london",
    "chelsea": "london",
    "tottenham": "london",
    "tottenham hotspur": "london",
    "spurs": "london",
    "west ham": "london",
    "west ham united": "london",
    "crystal palace": "london",
    "fulham": "london",
    "brentford": "london",
    "manchester city": "manchester",
    "man city": "manchester",
    "manchester united": "manchester",
    "man united": "manchester",
    "man utd": "manchester",
    "liverpool": "liverpool",
    "everton": "liverpool",
    "aston villa": "birmingham",
    "newcastle": "newcastle",
    "newcastle united": "newcastle",
    "brighton": "london",
    "brighton and hove albion": "london",
    "wolves": "birmingham",
    "wolverhampton": "birmingham",
    "wolverhampton wanderers": "birmingham",
    "nottingham forest": "birmingham",
    # Spain
    "real madrid": "madrid",
    "real madrid cf": "madrid",
    "atletico madrid": "madrid",
    "atlético madrid": "madrid",
    "atletico de madrid": "madrid",
    "barcelona": "barcelona",
    "fc barcelona": "barcelona",
    "sevilla": "seville",
    "real betis": "seville",
    "athletic bilbao": "bilbao",
    "athletic club": "bilbao",
    "real sociedad": "bilbao",
    "villarreal": "barcelona",
    "girona": "barcelona",
    # Italy
    "inter": "milan",
    "inter milan": "milan",
    "internazionale": "milan",
    "ac milan": "milan",
    "milan": "milan",
    "juventus": "turin",
    "napoli": "naples",
    "roma": "rome",
    "as roma": "rome",
    "lazio": "rome",
    "atalanta": "milan",
    "fiorentina": "rome",
    # Germany
    "bayern munich": "munich",
    "fc bayern munich": "munich",
    "bayern münchen": "munich",
    "fc bayern münchen": "munich",
    "borussia dortmund": "dortmund",
    "dortmund": "dortmund",
    "bvb": "dortmund",
    "rb leipzig": "leipzig",
    "leipzig": "leipzig",
    "bayer leverkusen": "dortmund",
    "leverkusen": "dortmund",
    "eintracht frankfurt": "munich",
    # France
    "psg": "paris",
    "paris saint-germain": "paris",
    "paris saint germain": "paris",
    "marseille": "marseille",
    "olympique marseille": "marseille",
    "lyon": "lyon",
    "olympique lyonnais": "lyon",
    "monaco": "marseille",
    "as monaco": "marseille",
    "lille": "paris",
    "lens": "paris",
    "nice": "marseille",
    # Europe
    "ajax": "amsterdam",
    "psv": "amsterdam",
    "psv eindhoven": "amsterdam",
    "feyenoord": "amsterdam",
    "porto": "porto",
    "fc porto": "porto",
    "benfica": "lisbon",
    "sporting": "lisbon",
    "sporting cp": "lisbon",
    "sporting lisbon": "lisbon",
    "celtic": "glasgow",
    "rangers": "glasgow",
    "galatasaray": "istanbul",
    "fenerbahce": "istanbul",
}

_MONTHLY: dict[str, list[tuple[float, str]]] = {
    k: _months_from_template(v) for k, v in _TEMPLATES.items()
}


def _normalize(name: str) -> str:
    return " ".join((name or "").lower().split())


def climate_for_home(team_name: str, month: int) -> dict[str, float | str] | None:
    """Soft home-city climate for a fixture month, or None if unknown/empty/bad month."""
    key = _normalize(team_name)
    if not key:
        return None
    try:
        m = int(month)
    except (TypeError, ValueError):
        return None
    if m < 1 or m > 12:
        return None
    tpl_name = _CLUB_TEMPLATE.get(key)
    if tpl_name is None:
        return None
    months = _MONTHLY.get(tpl_name)
    if not months:
        return None
    temp, cond = months[m - 1]
    try:
        t = float(temp)
    except (TypeError, ValueError):
        return None
    if t < -15.0:
        t = -15.0
    elif t > 45.0:
        t = 45.0
    c = str(cond).strip().lower()
    if c not in _CONDITIONS:
        c = "mild"
    return {"temp_c": round(t, 1), "condition": c}


# --- Live forecast fill (P1-F7) -------------------------------------------
#
# Optional, provider-backed current-weather fetch. Disabled until
# FOOTBALL_LIVE_WEATHER_URL is configured. The default URL template targets
# Open-Meteo (keyless); the response is normalized to the adapter's existing
# field names so the provider can be swapped without touching the adapter.

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _condition_from_code(code: int, temp_c: float) -> str:
    """Map a WMO weathercode (+ temperature) to the shared condition vocab."""
    if code in (0, 1):
        cond = "clear"
    elif code in (2, 3, 45, 48):
        cond = "mild"
    elif code in range(51, 68) or code in range(80, 83):
        cond = "rain"  # drizzle / rain / showers
    elif code in range(71, 78) or code in (85, 86):
        cond = "cold"  # snow / freezing
    else:
        cond = "mild"  # thunderstorm and anything unknown
    if temp_c <= 3.0:
        cond = "cold"
    elif temp_c >= 26.0 and cond == "clear":
        cond = "hot"
    return cond


# In-memory TTL cache: key -> (expires_at_monotonic, value). Keyed by
# (rounded lat, rounded lon, match local date) so repeated enrich calls for
# the same fixture within the TTL avoid extra HTTP. No persistent storage.
_LIVE_CACHE: dict[tuple[float, float, str], tuple[float, dict | None]] = {}


def _clear_live_weather_cache() -> None:
    """Empty the in-memory live-weather cache (test isolation)."""
    _LIVE_CACHE.clear()


def live_weather_for_match(match: MatchIdentity) -> dict[str, float | str | None] | None:
    """Live current-weather for a match's home city, or None on any failure.

    Selection gates (any miss → None, so the adapter falls through to static
    climate): provider URL configured; kickoff within the configurable horizon;
    home city resolves to coordinates; HTTP 200 with a parseable payload.
    Never raises into the caller.
    """
    try:
        url = str(getattr(settings, "FOOTBALL_LIVE_WEATHER_URL", "") or "").strip()
        if not url:
            return None  # not configured → caller uses static climate

        kickoff = getattr(match, "kickoff_utc", None)
        if kickoff is None:
            return None
        now = _utcnow()
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        horizon_h = float(getattr(settings, "FOOTBALL_LIVE_WEATHER_HORIZON_HOURS", 72.0))
        hours_to_kickoff = (kickoff - now).total_seconds() / 3600.0
        if hours_to_kickoff > horizon_h:
            return None  # too far out for a useful current forecast

        home = getattr(match, "home", None)
        home_name = getattr(home, "name", "") if home is not None else ""
        from app.sports._shared.team_geo import resolve_city

        geo = resolve_city(home_name, "football")
        if geo is None:
            return None
        lat, lon, _tz = geo

        cache_key = (round(float(lat), 2), round(float(lon), 2), kickoff.date().isoformat())
        ttl = float(getattr(settings, "FOOTBALL_LIVE_WEATHER_CACHE_TTL_HOURS", 6.0)) * 3600.0
        now_mono = time.monotonic()
        cached = _LIVE_CACHE.get(cache_key)
        if cached is not None and cached[0] > now_mono:
            return cached[1]

        params: dict[str, float | str] = {"latitude": lat, "longitude": lon}
        api_key = str(getattr(settings, "FOOTBALL_LIVE_WEATHER_API_KEY", "") or "").strip()
        if api_key:
            params["apikey"] = api_key
        timeout = float(getattr(settings, "FOOTBALL_LIVE_WEATHER_TIMEOUT_S", 5.0))
        resp = httpx.get(url, params=params, timeout=timeout)
        if getattr(resp, "status_code", 0) != 200:
            _LIVE_CACHE[cache_key] = (now_mono + ttl, None)
            return None
        payload = resp.json()
        current = payload["current_weather"]
        temp = float(current["temperature"])
        if temp < -15.0:
            temp = -15.0
        elif temp > 45.0:
            temp = 45.0
        try:
            code = int(current.get("weathercode", 0))
        except (TypeError, ValueError):
            code = 0
        result: dict[str, float | str | None] = {
            "weather_temp_c": round(temp, 1),
            "weather_condition": _condition_from_code(code, temp),
        }
        _LIVE_CACHE[cache_key] = (now_mono + ttl, result)
        return result
    except Exception:  # noqa: BLE001 — never raise into the adapter
        logger.debug("live weather fetch skipped", exc_info=True)
        return None
