"""Elo ratings service - fetch and cache real team Elo ratings.

Data sources:
1. World Football Elo Ratings (eloratings.net) - scraping
2. FIFA World Rankings (fifa.com) - convert to Elo estimate
3. Cached database for performance
"""

import httpx
from datetime import datetime, timedelta
from typing import Any
from bs4 import BeautifulSoup

from app.utils.prediction_db import get_prediction_session
from app.models.world_cup_prediction import Base
from sqlalchemy import Column, Integer, String, Float, DateTime, create_engine
from sqlalchemy.orm import declarative_base


# Elo ratings cache table
class EloRating(Base):
    """Team Elo ratings cache."""
    __tablename__ = "elo_ratings"

    team_name = Column(String, primary_key=True)
    elo_rating = Column(Float, nullable=False)
    fifa_rank = Column(Integer, nullable=True)
    confederation = Column(String, nullable=True)
    last_updated = Column(DateTime, nullable=False)
    source = Column(String, nullable=False)  # 'eloratings.net', 'fifa', 'estimated'


async def fetch_elo_from_web(team_name: str) -> dict[str, Any] | None:
    """Fetch Elo rating from eloratings.net.

    Args:
        team_name: Team name to look up

    Returns:
        {
            "team_name": "Brazil",
            "elo_rating": 2100.5,
            "fifa_rank": 3,
            "confederation": "CONMEBOL",
            "last_updated": "2026-06-24T10:00:00Z",
            "source": "eloratings.net"
        }
        None if not found or error
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Fetch the Elo ratings page
            response = await client.get("https://www.eloratings.net/")

            if response.status_code != 200:
                return None

            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')

            # Find the team's row in the rankings table
            # (Note: This is a simplified parser - actual implementation
            # would need to handle the specific HTML structure of eloratings.net)

            # For now, return None to indicate web scraping not implemented
            return None

    except Exception:
        return None


def estimate_elo_from_fifa_rank(fifa_rank: int) -> float:
    """Estimate Elo rating from FIFA ranking.

    Formula: Elo = 2200 - (fifa_rank × 6)

    This is the same formula used in enhanced_factors.py

    Args:
        fifa_rank: FIFA world ranking position (1-211)

    Returns:
        Estimated Elo rating (1000-2200)
    """
    return max(1000, 2200 - (fifa_rank * 6))


async def get_elo_rating(
    team_name: str,
    fifa_rank: int | None = None,
    force_refresh: bool = False
) -> dict[str, Any]:
    """Get Elo rating for a team (cached or fresh).

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
            "source": "cached" | "eloratings.net" | "estimated"
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
                age = (datetime.utcnow() - cached.last_updated).days
                if age < 7:
                    return {
                        "team_name": cached.team_name,
                        "elo_rating": cached.elo_rating,
                        "fifa_rank": cached.fifa_rank,
                        "confederation": cached.confederation,
                        "last_updated": cached.last_updated.isoformat(),
                        "source": f"cached_{cached.source}"
                    }

        # Try to fetch from web
        web_data = await fetch_elo_from_web(team_name)

        if web_data:
            # Save to cache
            cached = session.query(EloRating).filter_by(
                team_name=team_name
            ).first()

            if cached:
                cached.elo_rating = web_data["elo_rating"]
                cached.fifa_rank = web_data.get("fifa_rank")
                cached.confederation = web_data.get("confederation")
                cached.last_updated = datetime.utcnow()
                cached.source = web_data["source"]
            else:
                new_rating = EloRating(
                    team_name=team_name,
                    elo_rating=web_data["elo_rating"],
                    fifa_rank=web_data.get("fifa_rank"),
                    confederation=web_data.get("confederation"),
                    last_updated=datetime.utcnow(),
                    source=web_data["source"]
                )
                session.add(new_rating)

            session.commit()
            return web_data

        # Fallback: Estimate from FIFA rank
        if fifa_rank:
            estimated_elo = estimate_elo_from_fifa_rank(fifa_rank)

            # Save estimate to cache
            cached = session.query(EloRating).filter_by(
                team_name=team_name
            ).first()

            if cached:
                cached.elo_rating = estimated_elo
                cached.fifa_rank = fifa_rank
                cached.last_updated = datetime.utcnow()
                cached.source = "estimated"
            else:
                new_rating = EloRating(
                    team_name=team_name,
                    elo_rating=estimated_elo,
                    fifa_rank=fifa_rank,
                    confederation=None,
                    last_updated=datetime.utcnow(),
                    source="estimated"
                )
                session.add(new_rating)

            session.commit()

            return {
                "team_name": team_name,
                "elo_rating": estimated_elo,
                "fifa_rank": fifa_rank,
                "confederation": None,
                "last_updated": datetime.utcnow().isoformat(),
                "source": "estimated"
            }

        # Last resort: Default neutral Elo
        return {
            "team_name": team_name,
            "elo_rating": 1500.0,
            "fifa_rank": None,
            "confederation": None,
            "last_updated": datetime.utcnow().isoformat(),
            "source": "default"
        }

    finally:
        session.close()


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
                cached.last_updated = datetime.utcnow()
                cached.source = "manual_import"
            else:
                new_rating = EloRating(
                    team_name=team_name,
                    elo_rating=elo_rating,
                    fifa_rank=fifa_rank,
                    confederation=rating.get("confederation"),
                    last_updated=datetime.utcnow(),
                    source="manual_import"
                )
                session.add(new_rating)

            count += 1

        session.commit()
        return count

    finally:
        session.close()


# World Cup 2026 qualified teams - Initial Elo estimates
# Source: Based on FIFA rankings as of June 2024
WORLD_CUP_2026_ELO_ESTIMATES = [
    {"team_name": "Argentina", "elo_rating": 2100, "fifa_rank": 1},
    {"team_name": "France", "elo_rating": 2090, "fifa_rank": 2},
    {"team_name": "Brazil", "elo_rating": 2080, "fifa_rank": 3},
    {"team_name": "England", "elo_rating": 2050, "fifa_rank": 4},
    {"team_name": "Belgium", "elo_rating": 2040, "fifa_rank": 5},
    {"team_name": "Netherlands", "elo_rating": 2030, "fifa_rank": 6},
    {"team_name": "Portugal", "elo_rating": 2020, "fifa_rank": 7},
    {"team_name": "Spain", "elo_rating": 2010, "fifa_rank": 8},
    {"team_name": "Italy", "elo_rating": 2000, "fifa_rank": 9},
    {"team_name": "Croatia", "elo_rating": 1990, "fifa_rank": 10},
    {"team_name": "Germany", "elo_rating": 2070, "fifa_rank": 11},
    {"team_name": "Uruguay", "elo_rating": 1960, "fifa_rank": 12},
    {"team_name": "Mexico", "elo_rating": 1900, "fifa_rank": 15},
    {"team_name": "USA", "elo_rating": 1850, "fifa_rank": 20},
    {"team_name": "Colombia", "elo_rating": 1940, "fifa_rank": 13},
    {"team_name": "Senegal", "elo_rating": 1880, "fifa_rank": 18},
    {"team_name": "Denmark", "elo_rating": 1930, "fifa_rank": 14},
    {"team_name": "Switzerland", "elo_rating": 1920, "fifa_rank": 16},
    {"team_name": "Morocco", "elo_rating": 1890, "fifa_rank": 17},
    {"team_name": "Japan", "elo_rating": 1870, "fifa_rank": 19},
    {"team_name": "South Korea", "elo_rating": 1830, "fifa_rank": 23},
    {"team_name": "Iran", "elo_rating": 1800, "fifa_rank": 25},
    {"team_name": "Australia", "elo_rating": 1780, "fifa_rank": 27},
    {"team_name": "Canada", "elo_rating": 1820, "fifa_rank": 24},
    # Add more teams as they qualify...
]


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
