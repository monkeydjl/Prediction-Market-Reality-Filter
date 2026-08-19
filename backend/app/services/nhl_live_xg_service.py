"""Optional, cached true 5v5 NHL shot-quality snapshots (P1-H1).

Read-only and default-off. The official club-stats feed carries no team-level xG
or corsi, so the adapter currently scales shots-on-goal (``SF x 0.09``) as a soft
xG stand-in and uses the shots-on-goal share as a corsi-like proxy. This provider
replaces both with actual 5v5 measurements when one is configured and reachable.

The provider must publish 5v5 time on ice plus **actual expected goals** and/or
**actual corsi event counts**. xGF/60 and CF% are computed here from those inputs
rather than read from the payload, so a pre-computed rate carrying no sample
cannot be presented as a measured 5v5 value. Goals, shots on goal, scoring
chances, and estimated rates are not valid substitutes for expected goals.
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

# Unit-sanity band for 5v5 expected goals per 60 minutes. No NHL team lands
# outside it over a meaningful sample, so a value beyond the band means the
# payload is not in xG-per-60 at all and the whole feed is untrustworthy.
_XG60_FLOOR = 1.0
_XG60_CEILING = 4.5

# Plausibility band for 5v5 corsi share. A team beyond it is not being measured
# in shot attempts for and against, so the feed is counting something else.
_CORSI_FLOOR = 0.30
_CORSI_CEILING = 0.70


@dataclass(frozen=True)
class LiveNhl5v5:
    """Result of looking up one team in a live 5v5 snapshot."""

    available: bool
    metrics: dict[str, float] | None = None


@dataclass(frozen=True)
class _CachedSnapshot:
    fetched_at: float
    teams: dict[str, dict[str, float]]


_SNAPSHOT_CACHE: dict[str, _CachedSnapshot] = {}


def get_live_5v5_metrics(season: str | None, team_name: str) -> LiveNhl5v5:
    """Return live 5v5 metrics, or ``available=False`` if they cannot be read.

    ``available=True, metrics=None`` means the provider was reached but carried no
    usable row for the requested team — either it omitted the team or the team's
    5v5 sample was too small to be meaningful. The club-stats proxies stay
    authoritative in that case.
    """
    season_year = _season_year(season)
    team_key = _team_key(team_name)
    if not season_year or not team_key:
        return LiveNhl5v5(available=False)

    url = _request_url(season_year)
    if url is None:
        return LiveNhl5v5(available=False)

    snapshot = _snapshot(url)
    if snapshot is None:
        return LiveNhl5v5(available=False)

    row = snapshot.get(team_key)
    return LiveNhl5v5(available=True, metrics=dict(row) if row else None)


def clear_live_5v5_cache() -> None:
    """Clear process-local snapshots; used by tests and explicit diagnostics."""
    _SNAPSHOT_CACHE.clear()


def _season_year(season: str | None) -> str | None:
    """Leading start year of an NHL season key such as ``20262027``."""
    year = str(season or "").strip()[:4]
    return year if len(year) == 4 and year.isdigit() else None


def _request_url(season_year: str) -> str | None:
    """Configured endpoint for one season, or None when disabled or unusable."""
    if not settings.NHL_LIVE_XG_ENABLED:
        return None
    raw = str(settings.NHL_LIVE_XG_URL or "").strip()
    key = str(settings.NHL_LIVE_XG_API_KEY or "").strip()
    season_param = str(settings.NHL_LIVE_XG_SEASON_PARAM or "").strip()
    if not raw or not key or not season_param:
        return None
    split = urlsplit(raw)
    if split.scheme not in {"http", "https"} or not split.netloc:
        return None
    query_items = [
        (name, value)
        for name, value in parse_qsl(split.query, keep_blank_values=True)
        if name != season_param
    ]
    query_items.append((season_param, season_year))
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query_items), ""))


def _snapshot(url: str) -> dict[str, dict[str, float]] | None:
    now = time.monotonic()
    try:
        ttl_seconds = max(0.0, float(settings.NHL_LIVE_XG_CACHE_TTL_HOURS)) * 3600
        max_bytes = int(settings.NHL_LIVE_XG_MAX_BYTES)
        timeout = max(0.1, float(settings.NHL_LIVE_XG_TIMEOUT_S))
        min_toi = max(0.0, float(settings.NHL_LIVE_XG_MIN_TOI_MINUTES))
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    # Keyed by the resolved URL, so the season and any configuration change get
    # their own entry instead of reusing a snapshot from a different endpoint.
    cached = _SNAPSHOT_CACHE.get(url)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.teams

    api_key = str(settings.NHL_LIVE_XG_API_KEY or "").strip()
    request = Request(
        url,
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            # One byte past the cap distinguishes "at the limit" from "too large".
            body = response.read(max_bytes + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if len(body) > max_bytes:
        return None

    teams = _parse_snapshot(body, min_toi)
    if teams is None:
        return None
    # Only valid snapshots are cached; a transient fault must not pin an
    # unavailable answer for the whole TTL.
    _SNAPSHOT_CACHE[url] = _CachedSnapshot(fetched_at=now, teams=teams)
    return teams


def _parse_snapshot(body: bytes, min_toi: float) -> dict[str, dict[str, float]] | None:
    """Validate the documented envelope into computed 5v5 metrics per team key.

    A structurally broken row rejects the whole snapshot: the contract is either
    honoured or it is not. A row that is well-formed but backed by too little 5v5
    ice time is different — that is real data with an unusable sample, so it is
    dropped and the club-stats proxies cover that team.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    rows = payload.get("teams")
    if not isinstance(rows, list):
        return None

    teams: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        team_key = _team_key(row.get("team"))
        if not team_key or team_key in teams:
            return None  # missing or duplicate team makes the snapshot ambiguous
        toi = _numeric(row.get("toi_minutes"))
        if toi is None or toi <= 0:
            return None
        metrics = _row_metrics(row, toi)
        if metrics is None:
            return None
        if toi < min_toi:
            teams[team_key] = {}  # reached, sample too small to use
            continue
        metrics["toi_minutes"] = round(toi, 1)
        teams[team_key] = metrics
    return teams


def _row_metrics(row: dict[str, Any], toi: float) -> dict[str, float] | None:
    """Computed metrics for one validated row, or None if the row is malformed.

    Each metric group is optional but must arrive complete: expected goals needs
    ``xgf``, corsi needs both ``cf`` and ``ca``. A row carrying neither group has
    nothing measurable in it, and a half-supplied group is a contract violation —
    both reject the snapshot rather than silently degrading.
    """
    metrics: dict[str, float] = {}

    if "xgf" in row:
        xgf = _numeric(row.get("xgf"))
        if xgf is None or xgf < 0:
            return None
        xg60 = 60.0 * xgf / toi
        if not _XG60_FLOOR <= xg60 <= _XG60_CEILING:
            return None  # not expected goals per 60 minutes; the units are wrong
        metrics["xgf_per_60"] = round(xg60, 3)

    if "cf" in row or "ca" in row:
        corsi_for = _numeric(row.get("cf"))
        corsi_against = _numeric(row.get("ca"))
        if corsi_for is None or corsi_against is None:
            return None  # a half-supplied corsi pair cannot be turned into a share
        if corsi_for < 0 or corsi_against < 0 or corsi_for + corsi_against <= 0:
            return None
        share = corsi_for / (corsi_for + corsi_against)
        if not _CORSI_FLOOR <= share <= _CORSI_CEILING:
            return None  # not shot attempts for and against; the feed counts something else
        metrics["corsi_pct"] = round(share, 6)

    return metrics or None


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _team_key(name: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(name or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = "".join(char.lower() if char.isalnum() else " " for char in normalized).split()
    return " ".join(tokens)
