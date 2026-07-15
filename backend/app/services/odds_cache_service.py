"""Odds caching service - reduce API calls and stay within quota.

Cache betting odds with TTL to minimize The Odds API usage.
Free tier: 500 requests/month → need smart caching strategy.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from app.utils.prediction_db import get_prediction_session
from app.models.world_cup_prediction import Base
from sqlalchemy import Column, String, Float, DateTime, Integer
from app.services.odds_api_service import fetch_match_odds


class OddsCache(Base):
    """Cached betting odds."""
    __tablename__ = "odds_cache"

    match_key = Column(String, primary_key=True)  # "home_vs_away"
    home_odds = Column(Float, nullable=False)
    draw_odds = Column(Float, nullable=False)
    away_odds = Column(Float, nullable=False)
    source = Column(String, nullable=False)
    bookmakers_count = Column(Integer, nullable=True)
    cached_at = Column(DateTime, nullable=False)
    last_updated_api = Column(DateTime, nullable=True)  # From API response


def get_match_key(home_team: str, away_team: str, competition: str = "wc") -> str:
    """Generate cache key for a match, namespaced by competition."""
    return f"{competition}_{home_team.lower().replace(' ', '_')}_vs_{away_team.lower().replace(' ', '_')}"


async def get_cached_odds(
    home_team: str,
    away_team: str,
    ttl_seconds: int = 3600,  # 1 hour default
    commence_time: str | None = None,
    allow_stale: bool = True,
    max_stale_hours: int = 168,
    competition: str = "wc",
) -> dict[str, Any] | None:
    """Get cached odds or fetch fresh if expired.

    Args:
        home_team: Home team name
        away_team: Away team name
        ttl_seconds: Cache TTL in seconds (default 1 hour)
        commence_time: Match kickoff time for API fetch
        competition: Competition code (default "wc" = World Cup). Forwarded
            to ``fetch_match_odds`` and used to namespace the cache key.

    Returns:
        Odds dict or None if unavailable
    """
    session = get_prediction_session()
    match_key = get_match_key(home_team, away_team, competition=competition)

    try:
        # Check cache
        cached = session.query(OddsCache).filter_by(match_key=match_key).first()

        if cached:
            # SQLite stores naive datetimes; attach UTC tzinfo before
            # subtracting from an offset-aware "now".
            cached_at = cached.cached_at.replace(tzinfo=timezone.utc) if cached.cached_at else None
            if cached_at:
                age_seconds = (datetime.now(timezone.utc) - cached_at).total_seconds()
            else:
                age_seconds = ttl_seconds  # treat unknown as expired

            if age_seconds < ttl_seconds:
                # Cache hit
                return {
                    "home": cached.home_odds,
                    "draw": cached.draw_odds,
                    "away": cached.away_odds,
                    "source": f"cached_{cached.source}",
                    "last_update": cached.last_updated_api.isoformat() if cached.last_updated_api else cached.cached_at.isoformat(),
                    "bookmakers_count": cached.bookmakers_count,
                    "cache_age_seconds": int(age_seconds),
                    "stale": False,
                }

        # Cache miss or expired - fetch fresh
        fresh_odds = await fetch_match_odds(home_team, away_team, commence_time, competition=competition)

        if fresh_odds:
            # Update cache
            if cached:
                cached.home_odds = fresh_odds["home"]
                cached.draw_odds = fresh_odds["draw"]
                cached.away_odds = fresh_odds["away"]
                cached.source = fresh_odds["source"]
                cached.bookmakers_count = fresh_odds.get("bookmakers_count")
                cached.cached_at = datetime.now(timezone.utc)
                cached.last_updated_api = datetime.fromisoformat(
                    fresh_odds["last_update"].replace('Z', '+00:00')
                ) if fresh_odds.get("last_update") else None
            else:
                new_cache = OddsCache(
                    match_key=match_key,
                    home_odds=fresh_odds["home"],
                    draw_odds=fresh_odds["draw"],
                    away_odds=fresh_odds["away"],
                    source=fresh_odds["source"],
                    bookmakers_count=fresh_odds.get("bookmakers_count"),
                    cached_at=datetime.now(timezone.utc),
                    last_updated_api=datetime.fromisoformat(
                        fresh_odds["last_update"].replace('Z', '+00:00')
                    ) if fresh_odds.get("last_update") else None
                )
                session.add(new_cache)

            session.commit()

            return {
                **fresh_odds,
                "cache_age_seconds": 0,
                "stale": False,
            }

        if cached and allow_stale and age_seconds <= max_stale_hours * 3600:
            return {
                "home": cached.home_odds,
                "draw": cached.draw_odds,
                "away": cached.away_odds,
                "source": f"stale_cached_{cached.source}",
                "last_update": cached.last_updated_api.isoformat() if cached.last_updated_api else cached.cached_at.isoformat(),
                "bookmakers_count": cached.bookmakers_count,
                "cache_age_seconds": int(age_seconds),
                "stale": True,
            }

        return None

    finally:
        session.close()


async def prefetch_matches_odds(
    matches: list[dict[str, str]],
    ttl_seconds: int = 86400  # 24 hours for batch prefetch
) -> dict[str, Any]:
    """Prefetch odds for multiple matches (batch operation).

    Use this for daily batch updates to minimize API calls.

    Args:
        matches: List of {"home_team": str, "away_team": str, "commence_time": str}
        ttl_seconds: Cache TTL (default 24 hours for batch)

    Returns:
        {
            "fetched": 10,
            "cached": 5,
            "failed": 2,
            "api_calls": 10
        }
    """
    fetched = 0
    cached = 0
    failed = 0
    api_calls = 0

    for match in matches:
        odds = await get_cached_odds(
            home_team=match["home_team"],
            away_team=match["away_team"],
            ttl_seconds=ttl_seconds,
            commence_time=match.get("commence_time")
        )

        if odds:
            if odds.get("cache_age_seconds", 999) == 0:
                fetched += 1
                api_calls += 1
            else:
                cached += 1
        else:
            failed += 1

    return {
        "total": len(matches),
        "fetched": fetched,
        "cached": cached,
        "failed": failed,
        "api_calls": api_calls
    }


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics.

    Returns:
        {
            "total_entries": 64,
            "fresh_count": 50,  # < 1 hour old
            "stale_count": 14,  # > 1 hour old
            "oldest_entry_age_hours": 24.5
        }
    """
    session = get_prediction_session()

    try:
        all_entries = session.query(OddsCache).all()
        now = datetime.now(timezone.utc)

        fresh_count = 0
        stale_count = 0
        oldest_age_hours = 0.0

        for entry in all_entries:
            age_seconds = (now - entry.cached_at).total_seconds()
            age_hours = age_seconds / 3600

            if age_seconds < 3600:
                fresh_count += 1
            else:
                stale_count += 1

            oldest_age_hours = max(oldest_age_hours, age_hours)

        return {
            "total_entries": len(all_entries),
            "fresh_count": fresh_count,
            "stale_count": stale_count,
            "oldest_entry_age_hours": round(oldest_age_hours, 1)
        }

    finally:
        session.close()


def clear_expired_cache(max_age_hours: int = 168) -> int:
    """Clear cache entries older than max_age_hours.

    Args:
        max_age_hours: Maximum age in hours (default 7 days)

    Returns:
        Number of entries deleted
    """
    session = get_prediction_session()

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        deleted = session.query(OddsCache).filter(
            OddsCache.cached_at < cutoff
        ).delete()

        session.commit()
        return deleted

    finally:
        session.close()


def clear_all_cache() -> int:
    """Clear all cache entries (for testing/debugging).

    Returns:
        Number of entries deleted
    """
    session = get_prediction_session()

    try:
        deleted = session.query(OddsCache).delete()
        session.commit()
        return deleted

    finally:
        session.close()


# Smart caching strategy for World Cup 2026
# To stay within 500 requests/month free quota

CACHING_STRATEGY = {
    "pre_tournament": {
        "description": "Initial fetch 1 week before tournament",
        "ttl_hours": 24,
        "refresh_frequency": "daily",
        "estimated_calls": 64  # All matches once
    },
    "tournament_upcoming": {
        "description": "Matches starting in 24-48 hours",
        "ttl_hours": 6,
        "refresh_frequency": "every 6 hours",
        "estimated_calls": 4  # ~4 matches per day
    },
    "match_day_morning": {
        "description": "Matches today, refresh morning",
        "ttl_hours": 2,
        "refresh_frequency": "2 hours before kickoff",
        "estimated_calls": 4
    },
    "live_match": {
        "description": "Match in progress - NO UPDATES",
        "ttl_hours": 999,  # Don't refresh during match
        "refresh_frequency": "never",
        "estimated_calls": 0
    },
    "post_match": {
        "description": "Match finished - freeze odds",
        "ttl_hours": 999999,
        "refresh_frequency": "never",
        "estimated_calls": 0
    }
}

# Total estimated API calls for World Cup 2026:
# - Pre-tournament: 64 matches × 1 call = 64
# - Tournament: 4 matches/day × 2 refreshes × 30 days = 240
# - Buffer: 50
# Total: ~354 calls (within 500 free quota)
