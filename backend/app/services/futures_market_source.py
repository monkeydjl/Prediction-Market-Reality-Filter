# backend/app/services/futures_market_source.py
"""Futures/championship market source — fetches multi-leg events from Kalshi.

Parallel to kalshi_sports_source.py (single-leg). Filters Kalshi events to
multi-leg championship series (KXNBACHAMP, KXMLBCHAMP, etc.) and extracts
one contract per team.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Kalshi futures series ticker prefixes — multi-leg championship events.
# Maps series prefix -> (competition, championship_type).
# Matching uses exact series_ticker first, then longest-prefix startswith
# (Kalshi sometimes appends season tokens to series).
_KALSHI_FUTURES_SERIES_PREFIXES: dict[str, tuple[str, str]] = {
    # Core big-4 / soccer cups (Phase 12 baseline)
    "KXNBACHAMP": ("nba", "championship"),
    "KXMLBCHAMP": ("mlb", "world_series"),
    "KXNHLCHAMP": ("nhl", "stanley_cup"),
    "KXSOCCERWCS": ("wc", "world_cup"),
    "KXSOCCERUCL": ("ucl", "champions_league"),
    # Expanded coverage (P2-SB5)
    "KXNFLCHAMP": ("nfl", "super_bowl"),
    "KXSUPERBOWL": ("nfl", "super_bowl"),
    "KXNFLSUPERBOWL": ("nfl", "super_bowl"),
    "KXEPLCHAMP": ("epl", "premier_league"),
    "KXSOCCEREPL": ("epl", "premier_league"),
    "KXNCAAMBCHAMP": ("ncaab", "ncaa_tournament"),
    "KXNCAAMB": ("ncaab", "ncaa_tournament"),
    "KXNBACONF": ("nba", "conference"),
    "KXNHLCONFW": ("nhl", "conference"),
    "KXNHLCONFE": ("nhl", "conference"),
}


def list_known_futures_series() -> list[dict[str, str]]:
    """Return registered series prefixes for coverage dashboards / API."""
    return [
        {
            "series_prefix": prefix,
            "competition": comp,
            "championship_type": ctype,
        }
        for prefix, (comp, ctype) in sorted(_KALSHI_FUTURES_SERIES_PREFIXES.items())
    ]


def match_futures_series(series_ticker: str) -> tuple[str, str] | None:
    """Map a Kalshi series_ticker to (competition, championship_type)."""
    if not series_ticker:
        return None
    series = series_ticker.strip().upper()
    if series in _KALSHI_FUTURES_SERIES_PREFIXES:
        return _KALSHI_FUTURES_SERIES_PREFIXES[series]
    for prefix, mapping in sorted(
        _KALSHI_FUTURES_SERIES_PREFIXES.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        if series.startswith(prefix):
            return mapping
    return None


def multi_leg_integrity(
    contracts: list[dict[str, Any]],
    *,
    min_legs: int = 2,
    max_sum_prob: float = 1.45,
    min_sum_prob: float = 0.85,
) -> dict[str, Any]:
    """Assess multi-leg championship book integrity (coverage / vig / dupes)."""
    teams = [str(c.get("team") or "").strip() for c in contracts if c.get("team")]
    prices: list[float] = []
    missing_price = 0
    for c in contracts:
        p = c.get("price", c.get("implied_prob"))
        if p is None:
            missing_price += 1
            continue
        try:
            prices.append(float(p))
        except (TypeError, ValueError):
            missing_price += 1
    unique_teams = sorted({t for t in teams if t})
    dupe_teams = sorted({t for t in teams if t and teams.count(t) > 1})
    sum_prob = round(sum(prices), 4) if prices else None
    leg_count = len(contracts)

    issues: list[str] = []
    if leg_count < min_legs:
        issues.append("too_few_legs")
    if missing_price:
        issues.append("missing_prices")
    if dupe_teams:
        issues.append("duplicate_teams")
    if sum_prob is not None and sum_prob > max_sum_prob:
        issues.append("overround_high")
    if sum_prob is not None and sum_prob < min_sum_prob and leg_count >= min_legs:
        issues.append("underround_or_incomplete")
    if not unique_teams and leg_count:
        issues.append("no_team_codes")

    if not issues:
        status = "ok"
    elif "too_few_legs" in issues or "no_team_codes" in issues:
        status = "incomplete"
    elif "underround_or_incomplete" in issues or "missing_prices" in issues:
        status = "thin"
    else:
        status = "warn"

    return {
        "status": status,
        "leg_count": leg_count,
        "unique_team_count": len(unique_teams),
        "teams": unique_teams,
        "duplicate_teams": dupe_teams,
        "missing_price_count": missing_price,
        "sum_implied_prob": sum_prob,
        "issues": issues,
    }


def _extract_team_from_ticker(ticker: str) -> str:
    """Extract team code from a futures contract ticker.

    `KXNBACHAMP-LAL` -> "LAL". Returns "" if no dash present. If multiple
    dashes, takes the last segment (e.g., `KXSOCCERWCS-BRA` -> "BRA").
    """
    if not ticker or "-" not in ticker:
        return ""
    return ticker.rsplit("-", 1)[-1].strip()


def _parse_kalshi_price(last_price: float, yes_bid: float, yes_ask: float) -> float | None:
    """Parse Kalshi market price: last_price > midpoint > None.

    Returns None when no price data is available — callers should skip such
    contracts instead of injecting a synthetic 0.5 probability that would
    pollute downstream edge calculations.
    """
    if last_price and last_price > 0:
        return float(last_price)
    if yes_bid > 0 and yes_ask > 0:
        return float((yes_bid + yes_ask) / 2)
    return None


async def fetch_kalshi_futures_markets(limit: int = 200) -> list[dict[str, Any]]:
    """Fetch multi-leg championship events from Kalshi.

    Returns list of dicts with keys: event_ticker, title, competition,
    championship_type, contracts (list of {ticker, team, price, liquidity,
    volume}), source. Fail-closed: returns empty list on any error.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                settings.KALSHI_API_URL,
                params={
                    "status": "open",
                    "with_nested_markets": "true",
                    "limit": limit,
                },
            )
            response.raise_for_status()
            data = response.json()

        events = data.get("events", [])
        candidates: list[dict[str, Any]] = []

        for event in events:
            markets = event.get("markets", [])
            # Filter to multi-leg only (>=2 contracts = futures championship)
            if len(markets) < 2:
                continue

            series = (event.get("series_ticker") or "").upper()
            mapping = match_futures_series(series)
            if mapping is None:
                continue

            competition, championship_type = mapping
            event_ticker = event.get("event_ticker", "")
            title = event.get("title", "") or event_ticker

            contracts: list[dict[str, Any]] = []
            for market in markets:
                ticker = market.get("ticker", "")
                if not ticker:
                    continue
                team = _extract_team_from_ticker(ticker)
                if not team:
                    continue
                price = _parse_kalshi_price(
                    market.get("last_price_dollars", 0) or 0,
                    market.get("yes_bid_dollars", 0) or 0,
                    market.get("yes_ask_dollars", 0) or 0,
                )
                if price is None:
                    continue  # Skip contracts with no price data
                contracts.append({
                    "ticker": ticker,
                    "team": team,
                    "price": price,
                    "liquidity": float(market.get("liquidity_dollars", 0) or 0),
                    "volume": float(market.get("volume_fp", 0) or 0),
                })

            if not contracts:
                continue

            integrity = multi_leg_integrity(contracts)
            candidates.append({
                "event_ticker": event_ticker,
                "title": title,
                "competition": competition,
                "championship_type": championship_type,
                "series_ticker": series,
                "contracts": contracts,
                "integrity": integrity,
                "source": "kalshi",
            })

            # Polite rate limit
            await asyncio.sleep(settings.KALSHI_SPORTS_REQUEST_INTERVAL_SECONDS)

        return candidates

    except Exception:
        logger.warning("Failed to fetch Kalshi futures markets", exc_info=True)
        return []
