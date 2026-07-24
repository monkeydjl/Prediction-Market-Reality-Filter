# backend/app/sports/hockey/nhl_stats_client.py
"""HTTP client for the official NHL Stats API.

Base URL: https://api-web.nhle.com
Authentication: None (official free API).
Rate limit: 1 req/s (polite usage, not API-enforced).

Endpoints used:
    /v1/standings/now                         — team abbrevs for season walk
    /v1/club-schedule-season/{abbrev}/{season} — full club schedule
    /v1/schedule/{YYYY-MM-DD}                 — week schedule (fallback walk)
    /v1/game/{id}/feed/live                   — full game feed (scoring, lines)
    /v1/roster/{teamId}/current               — current team roster (goalies + sv%)

Note: /v1/schedule/{seasonKey} (e.g. 20252026) is NOT a valid path on the
public web API — season bulk fetch goes through club-schedule-season.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api-web.nhle.com"
_REQUEST_INTERVAL_SECONDS = 1.0  # 1 req/s polite rate limit
_MAX_WEEK_WALK_STEPS = 50
_MAX_TRANSIENT_RETRIES = 3
_TRANSIENT_BACKOFF_SECONDS = 1.5

# Module-level timestamp of last request for rate limiting
_last_request_time: float = 0.0


class NHLStatsClientError(Exception):
    """NHL Stats API error."""
    pass


def _enforce_rate_limit() -> None:
    """Sleep if needed to maintain >= 1s between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _REQUEST_INTERVAL_SECONDS:
        time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _request(path: str, params: dict[str, Any] | None = None) -> dict:
    """Issue a GET request to the NHL Stats API.

    Returns the parsed JSON payload (dict). Raises NHLStatsClientError
    on non-200 status, timeout, or network error. Transient SSL/timeout
    failures are retried a few times with short backoff.
    """
    url = f"{_BASE_URL}{path}"
    last_exc: Exception | None = None
    for attempt in range(_MAX_TRANSIENT_RETRIES + 1):
        _enforce_rate_limit()
        try:
            response = httpx.get(
                url,
                params=params,
                timeout=30.0,
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt >= _MAX_TRANSIENT_RETRIES:
                raise NHLStatsClientError(f"Request timeout: {url}") from exc
            wait = _TRANSIENT_BACKOFF_SECONDS * (1 + attempt)
            logger.warning(
                "NHL timeout on %s; sleep %.1fs (retry %s/%s)",
                path,
                wait,
                attempt + 1,
                _MAX_TRANSIENT_RETRIES,
            )
            time.sleep(wait)
            continue
        except httpx.RequestError as exc:
            last_exc = exc
            if attempt >= _MAX_TRANSIENT_RETRIES:
                raise NHLStatsClientError(f"Request failed: {exc}") from exc
            wait = _TRANSIENT_BACKOFF_SECONDS * (1 + attempt)
            logger.warning(
                "NHL request error on %s: %s; sleep %.1fs (retry %s/%s)",
                path,
                exc,
                wait,
                attempt + 1,
                _MAX_TRANSIENT_RETRIES,
            )
            time.sleep(wait)
            continue

        if response.status_code != 200:
            raise NHLStatsClientError(
                f"NHL API error: {response.status_code} - {response.text[:200]}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise NHLStatsClientError("NHL API returned non-JSON response") from exc

    raise NHLStatsClientError(f"Request failed: {last_exc}")


def _localized_default(value: Any) -> str:
    """Extract default string from NHL localized dict or plain string."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("default") or "").strip()
    return str(value).strip()


def fetch_nhl_team_abbrevs() -> list[str]:
    """Return current NHL team abbrevs from standings (e.g. TOR, NYR)."""
    data = _request("/v1/standings/now")
    abbrevs: list[str] = []
    seen: set[str] = set()
    for row in data.get("standings") or []:
        raw = row.get("teamAbbrev")
        ab = _localized_default(raw).upper()
        if ab and ab not in seen:
            seen.add(ab)
            abbrevs.append(ab)
    return abbrevs


def fetch_nhl_club_schedule(team_abbrev: str, season: str) -> list[dict]:
    """Fetch one club's full schedule for a season key (e.g. 20252026)."""
    data = _request(f"/v1/club-schedule-season/{team_abbrev}/{season}")
    games = data.get("games") or []
    return [g for g in games if isinstance(g, dict)]


def fetch_nhl_schedule_week(date: str) -> dict:
    """Fetch schedule week starting near YYYY-MM-DD. Returns full payload."""
    return _request(f"/v1/schedule/{date}")


def _games_from_schedule_payload(data: dict) -> list[dict]:
    games: list[dict] = []
    for week in data.get("gameWeek") or []:
        for game in week.get("games") or []:
            if isinstance(game, dict):
                games.append(game)
    return games


def _dedupe_games(games: list[dict]) -> list[dict]:
    seen: set[Any] = set()
    out: list[dict] = []
    for game in games:
        gid = game.get("id")
        if gid is None or gid in seen:
            continue
        seen.add(gid)
        out.append(game)
    return out


def _fetch_nhl_schedule_via_clubs(season: str) -> list[dict]:
    """Pull season games via each club schedule (canonical season bulk path)."""
    abbrevs = fetch_nhl_team_abbrevs()
    if not abbrevs:
        logger.warning("NHL standings returned no team abbrevs for season=%s", season)
        return []
    games: list[dict] = []
    for abbrev in abbrevs:
        try:
            club_games = fetch_nhl_club_schedule(abbrev, season)
        except NHLStatsClientError as exc:
            logger.warning(
                "NHL club schedule failed for %s season=%s: %s",
                abbrev,
                season,
                exc,
            )
            continue
        games.extend(club_games)
    return _dedupe_games(games)


def _fetch_nhl_schedule_via_weeks(start_date: str) -> list[dict]:
    """Walk /v1/schedule/{date} via nextStartDate (fallback when clubs fail)."""
    date = start_date
    games: list[dict] = []
    for _ in range(_MAX_WEEK_WALK_STEPS):
        try:
            payload = fetch_nhl_schedule_week(date)
        except NHLStatsClientError as exc:
            logger.warning("NHL week schedule failed at %s: %s", date, exc)
            break
        games.extend(_games_from_schedule_payload(payload))
        nxt = payload.get("nextStartDate")
        if not nxt or nxt == date:
            break
        date = str(nxt)
    return _dedupe_games(games)


def fetch_nhl_schedule(season: str) -> list[dict]:
    """Fetch NHL games for a season.

    Args:
        season: Season key in NHL format (e.g., "20252026" for 2025-26).

    Returns:
        List of raw game dicts. Each game dict contains ``id``,
        ``gameState``, ``homeTeam``, ``awayTeam``, ``startTimeUTC``, etc.

    Preferred path: club-schedule-season for every team in standings.
    Fallback: week walk starting from schedule/now when clubs yield nothing.
    """
    games = _fetch_nhl_schedule_via_clubs(season)
    if games:
        return games

    logger.warning(
        "NHL club schedules empty for season=%s; falling back to week walk",
        season,
    )
    try:
        now_payload = _request("/v1/schedule/now")
    except NHLStatsClientError:
        return []
    start = (
        now_payload.get("regularSeasonStartDate")
        or now_payload.get("preSeasonStartDate")
        or (now_payload.get("gameWeek") or [{}])[0].get("date")
    )
    if not start:
        return _games_from_schedule_payload(now_payload)
    return _fetch_nhl_schedule_via_weeks(str(start))


def fetch_nhl_game_feed(game_id: int) -> dict:
    """Fetch the full live feed for a single NHL game.

    Args:
        game_id: NHL game ID (e.g., 2023020001).

    Returns:
        Full game feed dict containing ``homeTeam``, ``awayTeam``,
        ``scoringPlays``, ``rosters``, etc.
    """
    return _request(f"/v1/game/{game_id}/feed/live")


def fetch_nhl_team_roster(team_id: int) -> dict:
    """Fetch the current roster for an NHL team.

    Args:
        team_id: NHL team ID (e.g., 1 for New Jersey Devils).

    Returns:
        Roster dict containing ``forwards``, ``defensemen``, and
        ``goalies`` arrays. Each goalie has ``svPct`` (save percentage).
    """
    return _request(f"/v1/roster/{team_id}/current")
