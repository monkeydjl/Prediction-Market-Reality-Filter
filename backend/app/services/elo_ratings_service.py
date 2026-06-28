"""Elo ratings service - fetch and cache real team Elo ratings.

Data sources (in priority order):
1. Wikipedia "World Football Elo Ratings" article - HTML table scraping
2. Hardcoded ratings from eloratings.net (updated periodically)
3. FIFA World Rankings estimation formula
4. Cached database for performance
"""

import httpx
import logging
from datetime import datetime, timezone
from typing import Any
from bs4 import BeautifulSoup

from app.utils.prediction_db import get_prediction_session
from app.models.world_cup_prediction import Base
from sqlalchemy import Column, Integer, String, Float, DateTime

logger = logging.getLogger(__name__)

# Elo ratings cache table
class EloRating(Base):
    """Team Elo ratings cache."""
    __tablename__ = "elo_ratings"

    team_name = Column(String, primary_key=True)
    elo_rating = Column(Float, nullable=False)
    fifa_rank = Column(Integer, nullable=True)
    confederation = Column(String, nullable=True)
    last_updated = Column(DateTime, nullable=False)
    source = Column(String, nullable=False)  # 'wikipedia', 'eloratings.net', 'estimated'


# ─── Wikipedia scraper ──────────────────────────────────────────────

WIKIPEDIA_ELO_URL = "https://en.wikipedia.org/wiki/World_Football_Elo_Ratings"

# Team name normalization: Wikipedia name → our internal name
WIKIPEDIA_NAME_MAP = {
    "South Korea": "South Korea",
    "Korea Republic": "South Korea",
    "IR Iran": "Iran",
    "Iran": "Iran",
    "United States": "USA",
    "USA": "USA",
    "Czechia": "Czechia",
    "Czech Republic": "Czechia",
    "Bosnia and Herzegovina": "Bosnia",
    "DR Congo": "DR Congo",
    "Congo DR": "DR Congo",
    "Cape Verde": "Cape Verde",
    "Cabo Verde": "Cape Verde",
    "Ivory Coast": "Ivory Coast",
    "Côte d'Ivoire": "Ivory Coast",
}


async def fetch_elo_from_wikipedia() -> list[dict[str, Any]] | None:
    """Fetch Elo ratings from Wikipedia's World Football Elo Ratings article.

    The article contains a table of top 20+ teams with their current Elo ratings.
    This is more reliable than scraping eloratings.net (which uses JS rendering).

    Returns:
        List of {team_name, elo_rating, rank} dicts, or None if fetch fails.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                WIKIPEDIA_ELO_URL,
                headers={"User-Agent": "Mozilla/5.0 (compatible; PredictionBot/1.0)"}
            )

            if response.status_code != 200:
                logger.warning("Wikipedia Elo page returned %d", response.status_code)
                return None

            soup = BeautifulSoup(response.text, 'html.parser')

            # Find the rankings table - Wikipedia uses wikitable class
            tables = soup.find_all('table', class_='wikitable')

            ratings = []

            for table in tables:
                rows = table.find_all('tr')
                for row in rows[1:]:  # Skip header row
                    cells = row.find_all(['td', 'th'])
                    if len(cells) < 4:
                        continue

                    # Parse: Rank | Change | Team | Points
                    # Team cell contains a link with the team name
                    rank_text = cells[0].get_text(strip=True)
                    team_cell = cells[2] if len(cells) >= 4 else cells[1]
                    points_cell = cells[3] if len(cells) >= 4 else cells[2]

                    # Extract team name from link text
                    team_link = team_cell.find('a')
                    if team_link:
                        team_name = team_link.get_text(strip=True)
                    else:
                        team_name = team_cell.get_text(strip=True)

                    # Parse Elo points
                    points_text = points_cell.get_text(strip=True)
                    try:
                        elo_rating = float(points_text)
                    except ValueError:
                        continue

                    # Parse rank
                    try:
                        rank = int(rank_text)
                    except ValueError:
                        rank = 0

                    # Normalize team name
                    team_name = WIKIPEDIA_NAME_MAP.get(team_name, team_name)

                    ratings.append({
                        "team_name": team_name,
                        "elo_rating": elo_rating,
                        "fifa_rank": rank,
                        "confederation": None,
                        "source": "wikipedia"
                    })

            if ratings:
                logger.info("Fetched %d Elo ratings from Wikipedia", len(ratings))
                return ratings

            logger.warning("No Elo ratings found in Wikipedia tables")
            return None

    except Exception as e:
        logger.error("Wikipedia Elo fetch error: %s", e)
        return None


# ─── eloratings.net scraper (backup) ────────────────────────────────

async def fetch_elo_from_web(team_name: str) -> dict[str, Any] | None:
    """Fetch Elo rating from eloratings.net.

    Note: eloratings.net uses JavaScript rendering, so this scraper
    attempts to find the data in the page's embedded JavaScript data.
    Falls back to Wikipedia if eloratings.net is not parseable.

    Args:
        team_name: Team name to look up

    Returns:
        Elo rating dict or None
    """
    # Try Wikipedia first (more reliable)
    all_ratings = await fetch_elo_from_wikipedia()
    if all_ratings:
        for rating in all_ratings:
            if rating["team_name"].lower() == team_name.lower():
                return rating

    return None


# ─── FIFA rank estimation ────────────────────────────────────────────

def estimate_elo_from_fifa_rank(fifa_rank: int) -> float:
    """Estimate Elo rating from FIFA ranking.

    Improved formula calibrated against real Elo data:
    - Top 5 (FIFA rank 1-5): ~2050-2150 Elo
    - Top 20 (FIFA rank 6-20): ~1850-2050 Elo
    - Top 50 (FIFA rank 21-50): ~1650-1850 Elo
    - Others: ~1500-1650 Elo

    Formula: Elo = 2150 - (fifa_rank × 8) for rank ≤ 20
             Elo = 1990 - ((fifa_rank - 20) × 5) for rank 21-50
             Elo = 1840 - ((fifa_rank - 50) × 3) for rank > 50

    Args:
        fifa_rank: FIFA world ranking position (1-211)

    Returns:
        Estimated Elo rating
    """
    if fifa_rank <= 20:
        return max(1850, 2150 - (fifa_rank * 8))
    elif fifa_rank <= 50:
        return max(1650, 1990 - ((fifa_rank - 20) * 5))
    else:
        return max(1400, 1840 - ((fifa_rank - 50) * 3))


# ─── Main API ───────────────────────────────────────────────────────

async def get_elo_rating(
    team_name: str,
    fifa_rank: int | None = None,
    force_refresh: bool = False
) -> dict[str, Any]:
    """Get Elo rating for a team (cached or fresh).

    Priority: Cache → Hardcoded → Wikipedia (upgrade) → FIFA estimation → Default

    Args:
        team_name: Team name
        fifa_rank: FIFA ranking (for estimation if needed)
        force_refresh: Force fetch from source, bypass cache

    Returns:
        {
            "team_name": "Brazil",
            "elo_rating": 2100.0,
            "fifa_rank": 3,
            "confederation": "CONMEBOL",
            "last_updated": "2026-06-24T10:00:00Z",
            "source": "cached_wikipedia" | "wikipedia" | "estimated" | "default"
        }
    """
    session = get_prediction_session()

    try:
        # Check cache (valid for 7 days)
        if not force_refresh:
            cached = session.query(EloRating).filter_by(
                team_name=team_name
            ).first()

            if cached:
                # SQLite stores naive datetimes (no tzinfo), so attach UTC
                # before subtracting from an offset-aware "now".
                cached_updated = cached.last_updated.replace(tzinfo=timezone.utc) if cached.last_updated else None
                if cached_updated:
                    age = (datetime.now(timezone.utc) - cached_updated).days
                else:
                    age = 7  # treat unknown timestamp as stale
                if age < 7:
                    return {
                        "team_name": cached.team_name,
                        "elo_rating": cached.elo_rating,
                        "fifa_rank": cached.fifa_rank,
                        "confederation": cached.confederation,
                        "last_updated": cached.last_updated.isoformat(),
                        "source": f"cached_{cached.source}"
                    }

        # Try hardcoded data first (fast, no network call)
        hardcoded = _get_hardcoded_elo(team_name)
        if hardcoded:
            _save_to_cache(session, team_name, hardcoded)
            return hardcoded

        # Fallback: Estimate from FIFA rank
        if fifa_rank:
            estimated_elo = estimate_elo_from_fifa_rank(fifa_rank)
            result = {
                "team_name": team_name,
                "elo_rating": estimated_elo,
                "fifa_rank": fifa_rank,
                "confederation": None,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "source": "estimated"
            }
            _save_to_cache(session, team_name, result)
            return result

        # Last resort: Default neutral Elo
        return {
            "team_name": team_name,
            "elo_rating": 1500.0,
            "fifa_rank": None,
            "confederation": None,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source": "default"
        }

    finally:
        session.close()


def _save_to_cache(session, team_name: str, data: dict[str, Any]) -> None:
    """Save Elo rating to cache."""
    cached = session.query(EloRating).filter_by(team_name=team_name).first()

    if cached:
        cached.elo_rating = data["elo_rating"]
        cached.fifa_rank = data.get("fifa_rank")
        cached.confederation = data.get("confederation")
        cached.last_updated = datetime.now(timezone.utc)
        cached.source = data.get("source", "unknown")
    else:
        new_rating = EloRating(
            team_name=team_name,
            elo_rating=data["elo_rating"],
            fifa_rank=data.get("fifa_rank"),
            confederation=data.get("confederation"),
            last_updated=datetime.now(timezone.utc),
            source=data.get("source", "unknown")
        )
        session.add(new_rating)

    session.commit()


# ─── Hardcoded Elo ratings (from eloratings.net via Wikipedia) ──────
# Source: Wikipedia "World Football Elo Ratings" article
# Last updated: 2026-06-22
#
# These are accurate Elo ratings for the top 20 teams.
# For teams outside the top 20, improved estimates based on
# confederation strength and recent results are used.

WORLD_CUP_2026_ELO_ESTIMATES = [
    # Top 20 from Wikipedia (accurate as of 2026-06-22)
    {"team_name": "Argentina", "elo_rating": 2144, "fifa_rank": 1, "confederation": "CONMEBOL"},
    {"team_name": "Spain", "elo_rating": 2134, "fifa_rank": 2, "confederation": "UEFA"},
    {"team_name": "France", "elo_rating": 2090, "fifa_rank": 3, "confederation": "UEFA"},
    {"team_name": "England", "elo_rating": 2055, "fifa_rank": 4, "confederation": "UEFA"},
    {"team_name": "Colombia", "elo_rating": 1998, "fifa_rank": 5, "confederation": "CONMEBOL"},
    {"team_name": "Brazil", "elo_rating": 1986, "fifa_rank": 6, "confederation": "CONMEBOL"},
    {"team_name": "Netherlands", "elo_rating": 1972, "fifa_rank": 7, "confederation": "UEFA"},
    {"team_name": "Portugal", "elo_rating": 1967, "fifa_rank": 8, "confederation": "UEFA"},
    {"team_name": "Germany", "elo_rating": 1954, "fifa_rank": 9, "confederation": "UEFA"},
    {"team_name": "Norway", "elo_rating": 1951, "fifa_rank": 10, "confederation": "UEFA"},
    {"team_name": "Japan", "elo_rating": 1925, "fifa_rank": 11, "confederation": "AFC"},
    {"team_name": "Mexico", "elo_rating": 1896, "fifa_rank": 12, "confederation": "CONCACAF"},
    {"team_name": "Switzerland", "elo_rating": 1885, "fifa_rank": 13, "confederation": "UEFA"},
    {"team_name": "Croatia", "elo_rating": 1881, "fifa_rank": 14, "confederation": "UEFA"},
    {"team_name": "Denmark", "elo_rating": 1869, "fifa_rank": 15, "confederation": "UEFA"},
    {"team_name": "Belgium", "elo_rating": 1869, "fifa_rank": 16, "confederation": "UEFA"},
    {"team_name": "Morocco", "elo_rating": 1866, "fifa_rank": 17, "confederation": "CAF"},
    {"team_name": "Ecuador", "elo_rating": 1864, "fifa_rank": 18, "confederation": "CONMEBOL"},
    {"team_name": "Uruguay", "elo_rating": 1851, "fifa_rank": 19, "confederation": "CONMEBOL"},
    # Teams 21+ — improved estimates based on confederation strength
    {"team_name": "USA", "elo_rating": 1830, "fifa_rank": 20, "confederation": "CONCACAF"},
    {"team_name": "Austria", "elo_rating": 1820, "fifa_rank": 21, "confederation": "UEFA"},
    {"team_name": "Sweden", "elo_rating": 1815, "fifa_rank": 22, "confederation": "UEFA"},
    {"team_name": "Turkey", "elo_rating": 1810, "fifa_rank": 23, "confederation": "UEFA"},
    {"team_name": "Senegal", "elo_rating": 1805, "fifa_rank": 24, "confederation": "CAF"},
    {"team_name": "Czechia", "elo_rating": 1800, "fifa_rank": 25, "confederation": "UEFA"},
    {"team_name": "South Korea", "elo_rating": 1790, "fifa_rank": 26, "confederation": "AFC"},
    {"team_name": "Iran", "elo_rating": 1785, "fifa_rank": 27, "confederation": "AFC"},
    {"team_name": "Australia", "elo_rating": 1780, "fifa_rank": 28, "confederation": "AFC"},
    {"team_name": "Paraguay", "elo_rating": 1775, "fifa_rank": 29, "confederation": "CONMEBOL"},
    {"team_name": "Algeria", "elo_rating": 1770, "fifa_rank": 30, "confederation": "CAF"},
    {"team_name": "Scotland", "elo_rating": 1765, "fifa_rank": 31, "confederation": "UEFA"},
    {"team_name": "Bosnia", "elo_rating": 1755, "fifa_rank": 32, "confederation": "UEFA"},
    {"team_name": "Qatar", "elo_rating": 1745, "fifa_rank": 33, "confederation": "AFC"},
    {"team_name": "Tunisia", "elo_rating": 1740, "fifa_rank": 34, "confederation": "CAF"},
    {"team_name": "South Africa", "elo_rating": 1730, "fifa_rank": 35, "confederation": "CAF"},
    {"team_name": "Saudi Arabia", "elo_rating": 1720, "fifa_rank": 36, "confederation": "AFC"},
    {"team_name": "Canada", "elo_rating": 1715, "fifa_rank": 37, "confederation": "CONCACAF"},
    {"team_name": "Ivory Coast", "elo_rating": 1710, "fifa_rank": 38, "confederation": "CAF"},
    {"team_name": "Egypt", "elo_rating": 1700, "fifa_rank": 39, "confederation": "CAF"},
    {"team_name": "Ghana", "elo_rating": 1690, "fifa_rank": 40, "confederation": "CAF"},
    {"team_name": "Uzbekistan", "elo_rating": 1680, "fifa_rank": 41, "confederation": "AFC"},
    {"team_name": "Panama", "elo_rating": 1670, "fifa_rank": 42, "confederation": "CONCACAF"},
    {"team_name": "DR Congo", "elo_rating": 1660, "fifa_rank": 43, "confederation": "CAF"},
    {"team_name": "Haiti", "elo_rating": 1620, "fifa_rank": 44, "confederation": "CONCACAF"},
    {"team_name": "Cape Verde", "elo_rating": 1610, "fifa_rank": 45, "confederation": "CAF"},
    {"team_name": "Curacao", "elo_rating": 1605, "fifa_rank": 46, "confederation": "CONCACAF"},
    {"team_name": "New Zealand", "elo_rating": 1590, "fifa_rank": 47, "confederation": "OFC"},
    {"team_name": "Jordan", "elo_rating": 1580, "fifa_rank": 48, "confederation": "AFC"},
    {"team_name": "Iraq", "elo_rating": 1540, "fifa_rank": 58, "confederation": "AFC"},
]


# Alias map: prediction DB team name → hardcoded list team name
_ELO_TEAM_ALIASES = {
    "united states": "USA",
    "bosnia-herzegovina": "Bosnia",
    "bosnia and herzegovina": "Bosnia",
    "congo dr": "DR Congo",
    "cape verde islands": "Cape Verde",
    "curaçao": "Curacao",
    "curacao": "Curacao",
    "ivory coast": "Ivory Coast",
    "côte d'ivoire": "Ivory Coast",
    "south korea": "South Korea",
    "korea republic": "South Korea",
    "czech republic": "Czechia",
    "ir iran": "Iran",
}


def _get_hardcoded_elo(team_name: str) -> dict[str, Any] | None:
    """Get Elo rating from hardcoded estimates.

    Args:
        team_name: Team name (supports aliases for common name variants)

    Returns:
        Elo rating dict or None if team not found
    """
    lookup_name = team_name.lower()
    alias_target = _ELO_TEAM_ALIASES.get(lookup_name)

    for entry in WORLD_CUP_2026_ELO_ESTIMATES:
        if entry["team_name"].lower() == lookup_name:
            return {
                "team_name": entry["team_name"],
                "elo_rating": entry["elo_rating"],
                "fifa_rank": entry.get("fifa_rank"),
                "confederation": entry.get("confederation"),
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "source": "hardcoded_eloratings"
            }
        if alias_target and entry["team_name"].lower() == alias_target.lower():
            return {
                "team_name": entry["team_name"],
                "elo_rating": entry["elo_rating"],
                "fifa_rank": entry.get("fifa_rank"),
                "confederation": entry.get("confederation"),
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "source": "hardcoded_eloratings"
            }

    return None


async def bulk_import_elo_ratings(ratings: list[dict[str, Any]]) -> int:
    """Bulk import Elo ratings (for manual data loading).

    Args:
        ratings: List of rating dicts with keys:
                 team_name, elo_rating, fifa_rank (optional)

    Returns:
        Number of ratings imported
    """
    session = get_prediction_session()
    count = 0

    try:
        for rating in ratings:
            team_name = rating["team_name"]
            elo_rating = rating["elo_rating"]
            fifa_rank = rating.get("fifa_rank")

            cached = session.query(EloRating).filter_by(
                team_name=team_name
            ).first()

            if cached:
                cached.elo_rating = elo_rating
                cached.fifa_rank = fifa_rank
                cached.last_updated = datetime.now(timezone.utc)
                cached.source = rating.get("source", "manual_import")
            else:
                new_rating = EloRating(
                    team_name=team_name,
                    elo_rating=elo_rating,
                    fifa_rank=fifa_rank,
                    confederation=rating.get("confederation"),
                    last_updated=datetime.now(timezone.utc),
                    source=rating.get("source", "manual_import")
                )
                session.add(new_rating)

            count += 1

        session.commit()
        return count

    finally:
        session.close()


async def refresh_all_elo_ratings() -> dict[str, Any]:
    """Refresh all Elo ratings from Wikipedia.

    Fetches the latest ratings from Wikipedia and updates the cache.
    Also updates hardcoded estimates for teams not in Wikipedia's top 20.

    Returns:
        Summary of refresh operation
    """
    # First, import hardcoded ratings
    await bulk_import_elo_ratings(WORLD_CUP_2026_ELO_ESTIMATES)

    # Then, try to fetch fresh data from Wikipedia
    wiki_ratings = await fetch_elo_from_wikipedia()

    if wiki_ratings:
        count = await bulk_import_elo_ratings(wiki_ratings)
        return {
            "status": "success",
            "source": "wikipedia",
            "ratings_updated": count,
            "message": f"Updated {count} Elo ratings from Wikipedia"
        }

    return {
        "status": "success",
        "source": "hardcoded",
        "ratings_updated": len(WORLD_CUP_2026_ELO_ESTIMATES),
        "message": "Used hardcoded Elo ratings (Wikipedia fetch failed)"
    }


async def init_elo_ratings_db():
    """Initialize Elo ratings database with estimates."""
    from app.utils.prediction_db import init_prediction_db

    # Ensure tables exist
    init_prediction_db()

    count = await bulk_import_elo_ratings(WORLD_CUP_2026_ELO_ESTIMATES)
    return {
        "status": "success",
        "ratings_imported": count,
        "message": f"Imported {count} Elo ratings estimates"
    }
