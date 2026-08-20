"""Optional, cached real market over/under totals lines (P1-O1).

Read-only and default-off. The soft totals diagnostic quotes an over/under
against a line, and with no provider configured that line is the sport's league
average — the same number the engine derives the expected total from. Because
those two are identical by construction, ``p_over`` is a per-sport constant that
carries no information about the fixture. A real book line breaks that tie.

The provider must publish the line together with **both** decimal prices. The
line on its own is a number with nothing behind it; a two-sided quote is what
makes it a market. The prices are de-vigged here and the implied over
probability must sit near even, because a book's posted total is by definition
the level it has balanced — a heavily skewed price means the number is not that
level and the row cannot be trusted.

The line is a market datum while the expected total remains model-derived, so
this pair is deliberately mixed-source. That is the point: the divergence is the
signal. See docs/dev/market-totals-provider-contract.md for the caveat that the
expected total is still a league-average baseline for basketball, baseball, and
hockey.
"""
from __future__ import annotations

import json
import math
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.core.config import settings

# A posted total must be in the right units for its sport. The band is relative
# to the baseline the line replaces, wide enough for any real book line and
# narrow enough to reject a cross-sport unit error: a basketball total of 5.5
# (a spread mistaken for a total) or a football total of 220 both fall outside.
_BAND_FLOOR_RATIO = 0.5
_BAND_CEILING_RATIO = 2.0

# A real two-sided quote carries an overround. No overround means the pair is
# not a book's prices, and an implausibly large one means it is not a market.
_MIN_OVERROUND = 1.0
_MAX_OVERROUND = 1.30


@dataclass(frozen=True)
class MarketTotal:
    """Result of looking one fixture up in a market totals snapshot."""

    available: bool
    total: dict[str, float] | None = None


@dataclass(frozen=True)
class _CachedSnapshot:
    fetched_at: float
    games: dict[tuple[str, str], dict[str, float]]


_SNAPSHOT_CACHE: dict[str, _CachedSnapshot] = {}


def get_market_total(
    sport: str,
    match_date: str,
    home_name: str,
    away_name: str,
) -> MarketTotal:
    """Return a real market total for one fixture, or ``available=False``.

    ``available=True, total=None`` means the provider was reached but carried no
    usable quote for this fixture — either it did not list the fixture or the
    market was published unpriced. The league-average baseline stays
    authoritative in that case, which is not the same as a known-even market.
    """
    baseline = _baseline_total(sport)
    day = _iso_date(match_date)
    home_key = _team_key(home_name)
    away_key = _team_key(away_name)
    if baseline is None or day is None or not home_key or not away_key:
        return MarketTotal(available=False)
    if home_key == away_key:
        return MarketTotal(available=False)

    url = _request_url(sport, day)
    if url is None:
        return MarketTotal(available=False)

    snapshot = _snapshot(url, baseline)
    if snapshot is None:
        return MarketTotal(available=False)

    row = snapshot.get((home_key, away_key))
    return MarketTotal(available=True, total=dict(row) if row else None)


def clear_market_totals_cache() -> None:
    """Clear process-local snapshots; used by tests and explicit diagnostics."""
    _SNAPSHOT_CACHE.clear()


def inject_market_total_into_custom(
    custom: dict[str, Any] | None,
    *,
    sport: str,
    kickoff_utc: Any,
    home_name: str,
    away_name: str,
) -> dict[str, Any]:
    """Return a new custom dict carrying a real market total when one is read.

    Never raises and never overwrites a line the caller already set. An
    unconfigured, unreachable, or unpriced provider leaves ``custom`` untouched,
    so the engine keeps its league-average placeholder and nothing about the
    default-off behaviour changes.
    """
    out: dict[str, Any] = dict(custom or {})
    if out.get("market_total_line") is not None:
        return out
    try:
        match_date = kickoff_utc.date().isoformat()
    except (AttributeError, TypeError, ValueError):
        return out
    try:
        result = get_market_total(sport, match_date, home_name, away_name)
    except Exception:  # noqa: BLE001 — a market feed must never break a prediction
        return out
    total = result.total if result.available else None
    if not total or total.get("total_line") is None:
        return out
    out["market_total_line"] = float(total["total_line"])
    p_over = total.get("market_p_over")
    if p_over is not None:
        out["market_total_p_over"] = float(p_over)
    return out


def _iso_date(match_date: Any) -> str | None:
    """Canonical ``YYYY-MM-DD`` for the request, or None when unusable.

    The shape is required exactly rather than parsed loosely. A provider handed a
    timestamp or a partial date is free to ignore it and return some other day's
    board, and a wrong-day snapshot would look perfectly valid while quoting
    lines for the wrong fixtures.
    """
    text = str(match_date or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return None


def _baseline_total(sport: str) -> float | None:
    """The league-average level this line replaces, used only for the unit band.

    Unknown sports are rejected rather than banded against a guess.
    """
    key = str(sport or "").strip().lower()
    try:
        if key == "basketball":
            baseline = float(settings.NBA_LEAGUE_AVG_TOTAL)
        elif key == "baseball":
            baseline = float(settings.MLB_LEAGUE_AVG_TOTAL)
        elif key == "hockey":
            baseline = float(settings.NHL_LEAGUE_AVG_TOTAL)
        elif key in {"football", "soccer"}:
            # Football has no league-average setting; the engines quote their
            # soft O/U against 2.5, so that is the level being replaced.
            baseline = 2.5
        else:
            return None
    except (TypeError, ValueError):
        return None
    if not math.isfinite(baseline) or baseline <= 0:
        return None
    return baseline


def _request_url(sport: str, match_date: str) -> str | None:
    """Configured endpoint for one sport and date, or None when unusable."""
    if not settings.MARKET_TOTALS_ENABLED:
        return None
    raw = str(settings.MARKET_TOTALS_URL or "").strip()
    key = str(settings.MARKET_TOTALS_API_KEY or "").strip()
    sport_param = str(settings.MARKET_TOTALS_SPORT_PARAM or "").strip()
    date_param = str(settings.MARKET_TOTALS_DATE_PARAM or "").strip()
    if not raw or not key or not sport_param or not date_param:
        return None
    if sport_param == date_param:
        return None  # one parameter cannot carry both values
    split = urlsplit(raw)
    if split.scheme not in {"http", "https"} or not split.netloc:
        return None
    query_items = [
        (name, value)
        for name, value in parse_qsl(split.query, keep_blank_values=True)
        if name not in {sport_param, date_param}
    ]
    query_items.append((sport_param, str(sport).strip().lower()))
    query_items.append((date_param, match_date))
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query_items), ""))


def _snapshot(
    url: str, baseline: float,
) -> dict[tuple[str, str], dict[str, float]] | None:
    now = time.monotonic()
    try:
        ttl_seconds = max(0.0, float(settings.MARKET_TOTALS_CACHE_TTL_MINUTES)) * 60
        max_bytes = int(settings.MARKET_TOTALS_MAX_BYTES)
        timeout = max(0.1, float(settings.MARKET_TOTALS_TIMEOUT_S))
        max_skew = max(0.0, float(settings.MARKET_TOTALS_MAX_PRICE_SKEW))
    except (TypeError, ValueError):
        return None
    if max_bytes <= 0:
        return None

    # Keyed by the resolved URL, so the sport, the date, and any configuration
    # change get their own entry instead of reusing another endpoint's snapshot.
    cached = _SNAPSHOT_CACHE.get(url)
    if cached is not None and now - cached.fetched_at < ttl_seconds:
        return cached.games

    api_key = str(settings.MARKET_TOTALS_API_KEY or "").strip()
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

    games = _parse_snapshot(body, baseline, max_skew)
    if games is None:
        return None
    # Only valid snapshots are cached; a transient fault must not pin an
    # unavailable answer for the whole TTL.
    _SNAPSHOT_CACHE[url] = _CachedSnapshot(fetched_at=now, games=games)
    return games


def _parse_snapshot(
    body: bytes, baseline: float, max_skew: float,
) -> dict[tuple[str, str], dict[str, float]] | None:
    """Validate the documented envelope into a de-vigged line per fixture pair.

    A structurally broken row rejects the whole snapshot: the contract is either
    honoured or it is not. A row that is well-formed but explicitly unpriced on
    both sides is different — that is a real fixture whose market is suspended
    or not yet open, so it is stored empty and the baseline covers that fixture.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    rows = payload.get("games")
    if not isinstance(rows, list):
        return None

    games: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        home_key = _team_key(row.get("home"))
        away_key = _team_key(row.get("away"))
        if not home_key or not away_key or home_key == away_key:
            return None  # an unidentifiable or self-paired fixture is ambiguous
        pair = (home_key, away_key)
        if pair in games:
            return None  # a duplicated fixture makes the snapshot ambiguous

        # Both keys must be present; a half-supplied quote is a broken contract,
        # while both explicitly null is a market that exists but is not priced.
        if "over_odds" not in row or "under_odds" not in row:
            return None
        if row["over_odds"] is None and row["under_odds"] is None:
            games[pair] = {}  # reached, market unpriced
            continue

        line = _numeric(row.get("total_line"))
        over_odds = _numeric(row["over_odds"])
        under_odds = _numeric(row["under_odds"])
        if line is None or over_odds is None or under_odds is None:
            return None
        if not baseline * _BAND_FLOOR_RATIO <= line <= baseline * _BAND_CEILING_RATIO:
            return None  # wrong units for this sport
        # Decimal odds of 1.0 or below cannot be a price.
        if over_odds <= 1.0 or under_odds <= 1.0:
            return None

        overround = 1.0 / over_odds + 1.0 / under_odds
        if not _MIN_OVERROUND < overround <= _MAX_OVERROUND:
            return None  # not a book's two-sided prices
        p_over = (1.0 / over_odds) / overround
        if abs(p_over - 0.5) > max_skew:
            return None  # the posted number is not the level the book balanced

        games[pair] = {
            "total_line": round(line, 3),
            "market_p_over": round(p_over, 4),
        }
    return games


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
