# backend/app/sports/baseball/mlb_stats_client.py
"""HTTP client for the official MLB Stats API.

Base URL: https://statsapi.mlb.com/api/v1
Game feed: https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live
Authentication: None (official free API).
Rate limit: 1 req/s (polite usage, not API-enforced).

Endpoints used:
    schedule?startDate=...&endDate=...&sportId=1   — list games by date range
    game/{gamePk}/feed/live (v1.1)                 — full game feed (probable pitchers)
    people/{personId}                              — player/pitcher stats
    stats?group=pitching&teamIds=...               — team pitcher season splits
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://statsapi.mlb.com/api/v1"
_FEED_BASE_URL = "https://statsapi.mlb.com/api/v1.1"
_REQUEST_INTERVAL_SECONDS = 1.0  # 1 req/s polite rate limit

# Module-level timestamp of last request for rate limiting
_last_request_time: float = 0.0


class MLBStatsClientError(Exception):
    """MLB Stats API error."""
    pass


def _enforce_rate_limit() -> None:
    """Sleep if needed to maintain >= 1s between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _REQUEST_INTERVAL_SECONDS:
        time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _request(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    base_url: str | None = None,
) -> dict:
    """Issue a GET request to the MLB Stats API.

    Returns the parsed JSON payload (dict). Raises MLBStatsClientError
    on non-200 status, timeout, or network error.
    """
    _enforce_rate_limit()
    root = base_url or _BASE_URL
    url = f"{root}{path}"
    try:
        response = httpx.get(
            url,
            params=params,
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
        )
    except httpx.TimeoutException as exc:
        raise MLBStatsClientError(f"Request timeout: {url}") from exc
    except httpx.RequestError as exc:
        raise MLBStatsClientError(f"Request failed: {exc}") from exc

    if response.status_code != 200:
        raise MLBStatsClientError(
            f"MLB API error: {response.status_code} - {response.text[:200]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise MLBStatsClientError("MLB API returned non-JSON response") from exc


def fetch_mlb_schedule(
    start_date: str,
    end_date: str,
    *,
    hydrate: str | None = None,
) -> list[dict]:
    """Fetch MLB games in a date range.

    Args:
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format (inclusive).
        hydrate: Optional hydrate string (e.g. ``probablePitcher``).

    Returns:
        List of raw game dicts from the schedule response. Each game dict
        contains ``gamePk``, ``status``, ``teams``, ``gameDate``, etc.
    """
    params: dict[str, Any] = {
        "sportId": 1,  # MLB
        "startDate": start_date,
        "endDate": end_date,
    }
    if hydrate:
        params["hydrate"] = hydrate
    data = _request("/schedule", params=params)
    games: list[dict] = []
    for date_entry in data.get("dates", []):
        games.extend(date_entry.get("games", []))
    return games


def fetch_mlb_game_feed(game_pk: int) -> dict:
    """Fetch the full live feed for a single MLB game.

    Official feed lives under ``/api/v1.1/game/{pk}/feed/live`` (v1 404s).

    Args:
        game_pk: MLB gamePk (e.g., 778812).

    Returns:
        Full game feed dict containing ``gameData`` (teams, players, venue,
        probablePitchers) and ``liveData`` (plays, scoring, boxscore).
    """
    return _request(f"/game/{game_pk}/feed/live", base_url=_FEED_BASE_URL)


def fetch_mlb_pitcher(person_id: int) -> dict:
    """Fetch pitcher stats by person ID.

    Args:
        person_id: MLB person ID (e.g., 543037 for Gerrit Cole).

    Returns:
        Pitcher payload dict containing ``people`` array with stats
        (ERA, WHIP) under ``stats[].splits[].stat``.
    """
    return _request(
        f"/people/{person_id}",
        params={"hydrate": "stats(group=[pitching],type=[season])"},
    )


def fetch_mlb_team_pitcher_stats(team_id: int, season: int | str) -> list[dict]:
    """Fetch season pitching splits for all pitchers on a team.

    Args:
        team_id: MLB team ID (e.g., 144 for Atlanta Braves).
        season: Season year (e.g., 2026).

    Returns:
        List of split dicts from ``/stats`` (each has ``player`` + ``stat``).
    """
    data = _request(
        "/stats",
        params={
            "stats": "season",
            "group": "pitching",
            "season": int(season),
            "teamIds": int(team_id),
            "playerPool": "all",
            "limit": 100,
            "sportIds": 1,
        },
    )
    stats = data.get("stats") or []
    if not stats:
        return []
    return list(stats[0].get("splits") or [])


def fetch_mlb_team_pitching_totals(team_id: int, season: int | str) -> dict | None:
    """Fetch team-level season pitching totals (ERA/WHIP).

    Returns the first split ``stat`` dict, or None when unavailable.
    """
    data = _request(
        f"/teams/{int(team_id)}/stats",
        params={
            "stats": "season",
            "group": "pitching",
            "season": int(season),
        },
    )
    stats = data.get("stats") or []
    if not stats:
        return None
    splits = stats[0].get("splits") or []
    if not splits:
        return None
    stat = splits[0].get("stat")
    return stat if isinstance(stat, dict) else None


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_innings_pitched(value: Any) -> float:
    """Parse MLB innings pitched (``45.2`` means 45 + 2/3)."""
    if value is None or value == "":
        return 0.0
    try:
        text = str(value).strip()
        if "." in text:
            whole_s, frac_s = text.split(".", 1)
            whole = int(whole_s or "0")
            outs = int(frac_s[0]) if frac_s else 0
            if outs < 0 or outs > 2:
                return float(text)
            return whole + outs / 3.0
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def parse_pitcher_person(payload: dict | None) -> dict | None:
    """Extract name/ERA/WHIP from a ``/people/{id}`` pitching hydrate payload.

    Returns ``{"name": str, "era": float, "whip": float, "person_id": int}``
    or None when unusable.
    """
    if not isinstance(payload, dict):
        return None
    people = payload.get("people") or []
    if not people or not isinstance(people[0], dict):
        return None
    person = people[0]
    name = (person.get("fullName") or "").strip()
    person_id = person.get("id")
    era: float | None = None
    whip: float | None = None
    for block in person.get("stats") or []:
        if not isinstance(block, dict):
            continue
        group = ((block.get("group") or {}).get("displayName") or "").lower()
        if group and group != "pitching":
            continue
        for split in block.get("splits") or []:
            if not isinstance(split, dict):
                continue
            stat = split.get("stat") or {}
            if not isinstance(stat, dict):
                continue
            era = _safe_float(stat.get("era"))
            whip = _safe_float(stat.get("whip"))
            if era is not None or whip is not None:
                break
        if era is not None or whip is not None:
            break
    if not name and era is None and whip is None:
        return None
    out: dict[str, Any] = {"name": name or None, "era": era, "whip": whip}
    if person_id is not None:
        try:
            out["person_id"] = int(person_id)
        except (TypeError, ValueError):
            pass
    return out


def extract_probable_pitchers(feed: dict | None) -> dict[str, dict]:
    """Pull home/away probable pitchers from a v1.1 game feed.

    Returns ``{"home": {"id": int, "name": str}, "away": {...}}`` with
    missing sides omitted or empty.
    """
    result: dict[str, dict] = {"home": {}, "away": {}}
    if not isinstance(feed, dict):
        return result
    game_data = feed.get("gameData") or {}
    probable = game_data.get("probablePitchers") or {}
    for side in ("home", "away"):
        row = probable.get(side) or {}
        if not isinstance(row, dict):
            continue
        pid = row.get("id")
        name = (row.get("fullName") or "").strip()
        side_out: dict[str, Any] = {}
        if pid is not None:
            try:
                side_out["id"] = int(pid)
            except (TypeError, ValueError):
                pass
        if name:
            side_out["name"] = name
        result[side] = side_out
    return result


def extract_probable_pitchers_from_schedule_game(game: dict | None) -> dict[str, dict]:
    """Pull probable pitchers from a schedule game (hydrate=probablePitcher)."""
    result: dict[str, dict] = {"home": {}, "away": {}}
    if not isinstance(game, dict):
        return result
    teams = game.get("teams") or {}
    for side in ("home", "away"):
        side_team = teams.get(side) or {}
        row = side_team.get("probablePitcher") or {}
        if not isinstance(row, dict):
            continue
        pid = row.get("id")
        name = (row.get("fullName") or "").strip()
        side_out: dict[str, Any] = {}
        if pid is not None:
            try:
                side_out["id"] = int(pid)
            except (TypeError, ValueError):
                pass
        if name:
            side_out["name"] = name
        result[side] = side_out
    return result


def summarize_bullpen_era(pitcher_splits: list[dict] | None) -> float | None:
    """IP-weighted ERA for relief pitchers (gamesStarted == 0).

    Pure starters (GS > 0 and GS == gamesPlayed) are excluded. Swingmen
    with GS > 0 but also relief appearances are excluded when GS > 0 to
    avoid double-counting with the starting-pitcher factor; only true
    relievers (GS == 0) contribute.
    """
    if not pitcher_splits:
        return None
    total_er = 0.0
    total_ip = 0.0
    for split in pitcher_splits:
        if not isinstance(split, dict):
            continue
        stat = split.get("stat") or {}
        if not isinstance(stat, dict):
            continue
        try:
            gs = int(stat.get("gamesStarted") or 0)
        except (TypeError, ValueError):
            gs = 0
        if gs > 0:
            continue
        ip = parse_innings_pitched(stat.get("inningsPitched"))
        if ip <= 0:
            continue
        era = _safe_float(stat.get("era"))
        er = _safe_float(stat.get("earnedRuns"))
        if er is None and era is not None:
            er = era * ip / 9.0
        if er is None:
            continue
        total_er += er
        total_ip += ip
    if total_ip <= 0:
        return None
    return round(total_er * 9.0 / total_ip, 3)


def summarize_team_era(team_stat: dict | None) -> float | None:
    """Extract team ERA from a team pitching totals stat dict."""
    if not isinstance(team_stat, dict):
        return None
    return _safe_float(team_stat.get("era"))


def parse_wind_mph(wind_text: Any) -> float | None:
    """Parse wind speed (mph) from MLB strings like ``6 mph, Out To LF``."""
    if wind_text is None:
        return None
    if isinstance(wind_text, (int, float)):
        return float(wind_text)
    text = str(wind_text).strip()
    if not text:
        return None
    # Leading number: "6 mph, Out To LF" / "15mph In From CF" / "Calm"
    if text.lower() in {"calm", "none", "n/a", "na", "-"}:
        return 0.0
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*mph", text, flags=re.IGNORECASE)
    if match:
        return _safe_float(match.group(1))
    match = re.match(r"(-?\d+(?:\.\d+)?)", text)
    if match:
        return _safe_float(match.group(1))
    return None


def fahrenheit_to_celsius(temp_f: Any) -> float | None:
    """Convert Fahrenheit temperature to Celsius."""
    f = _safe_float(temp_f)
    if f is None:
        return None
    return round((f - 32.0) * 5.0 / 9.0, 2)


def parse_mlb_weather(feed: dict | None) -> dict | None:
    """Extract weather from a v1.1 game feed ``gameData.weather``.

    Returns
    ``{"temp_c": float|None, "temp_f": float|None, "wind_mph": float|None,
       "condition": str|None, "roof_type": str|None, "venue": str|None}``
    or None when no usable weather block exists.
    """
    if not isinstance(feed, dict):
        return None
    game_data = feed.get("gameData") or {}
    weather = game_data.get("weather")
    venue = game_data.get("venue") or {}
    field_info = venue.get("fieldInfo") or {}
    roof_type = field_info.get("roofType")
    venue_name = venue.get("name")

    if not isinstance(weather, dict) or not weather:
        # Still return venue/roof when weather missing (indoor parks).
        if venue_name or roof_type:
            return {
                "temp_c": None,
                "temp_f": None,
                "wind_mph": None,
                "condition": None,
                "roof_type": roof_type,
                "venue": venue_name,
            }
        return None

    temp_f = _safe_float(weather.get("temp"))
    temp_c = fahrenheit_to_celsius(temp_f) if temp_f is not None else None
    wind_mph = parse_wind_mph(weather.get("wind"))
    condition = weather.get("condition")
    if isinstance(condition, str):
        condition = condition.strip() or None
    else:
        condition = None

    if temp_c is None and wind_mph is None and not condition and not venue_name:
        return None

    # Closed domes: keep condition/venue but weather has little outdoor effect.
    # Callers may still store values for display; engine soft-uses temp/wind.
    return {
        "temp_c": temp_c,
        "temp_f": temp_f,
        "wind_mph": wind_mph,
        "condition": condition,
        "roof_type": roof_type if isinstance(roof_type, str) else None,
        "venue": venue_name if isinstance(venue_name, str) else None,
    }
