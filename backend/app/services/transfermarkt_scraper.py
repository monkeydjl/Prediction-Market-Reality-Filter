"""Transfermarkt team market value scraper.

Scrapes team squad market values from Transfermarkt to enhance team strength prediction.
Market value is a strong proxy for team quality.
"""

import logging
import re
import time
from typing import Any
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from app.utils.prediction_db import get_prediction_session, close_prediction_session

logger = logging.getLogger(__name__)


# Team URL mapping for World Cup 2026 teams
TRANSFERMARKT_TEAM_URLS = {
    "Brazil": "https://www.transfermarkt.com/brasilien/startseite/verein/3439",
    "Argentina": "https://www.transfermarkt.com/argentinien/startseite/verein/3437",
    "France": "https://www.transfermarkt.com/frankreich/startseite/verein/3377",
    "Germany": "https://www.transfermarkt.com/deutschland/startseite/verein/3262",
    "Spain": "https://www.transfermarkt.com/spanien/startseite/verein/3375",
    "England": "https://www.transfermarkt.com/england/startseite/verein/3299",
    "Portugal": "https://www.transfermarkt.com/portugal/startseite/verein/3300",
    "Netherlands": "https://www.transfermarkt.com/niederlande/startseite/verein/3379",
    "Italy": "https://www.transfermarkt.com/italien/startseite/verein/3376",
    "Belgium": "https://www.transfermarkt.com/belgien/startseite/verein/3382",
    "Uruguay": "https://www.transfermarkt.com/uruguay/startseite/verein/3449",
    "Croatia": "https://www.transfermarkt.com/kroatien/startseite/verein/3556",
    "Denmark": "https://www.transfermarkt.com/danemark/startseite/verein/3436",
    "Switzerland": "https://www.transfermarkt.com/schweiz/startseite/verein/3384",
    "Mexico": "https://www.transfermarkt.com/mexiko/startseite/verein/6303",
    "USA": "https://www.transfermarkt.com/vereinigte-staaten/startseite/verein/3505",
    "Senegal": "https://www.transfermarkt.com/senegal/startseite/verein/3499",
    "Morocco": "https://www.transfermarkt.com/marokko/startseite/verein/3575",
    "Japan": "https://www.transfermarkt.com/japan/startseite/verein/3435",
    "South Korea": "https://www.transfermarkt.com/sudkorea/startseite/verein/3589",
    "Australia": "https://www.transfermarkt.com/australien/startseite/verein/3433",
    "Canada": "https://www.transfermarkt.com/kanada/startseite/verein/3436",
    "Ecuador": "https://www.transfermarkt.com/ecuador/startseite/verein/3359",
    "Colombia": "https://www.transfermarkt.com/kolumbien/startseite/verein/3816",
}


def parse_market_value(value_str: str) -> float | None:
    """Parse Transfermarkt market value string to millions of euros.

    Examples:
        "€1.05bn" -> 1050.0
        "€850.00m" -> 850.0
        "€45.50m" -> 45.5
        "€2.30k" -> 0.0023

    Args:
        value_str: Market value string from Transfermarkt

    Returns:
        Market value in millions of euros, or None if parsing fails
    """
    if not value_str:
        return None

    # Remove currency symbol and spaces
    value_str = value_str.replace("€", "").replace("£", "").replace("$", "").strip()

    # Extract number and unit
    match = re.match(r"([\d,.]+)\s*([kmb])?", value_str, re.IGNORECASE)
    if not match:
        return None

    number_str = match.group(1).replace(",", "")
    unit = match.group(2).lower() if match.group(2) else ""

    try:
        number = float(number_str)
    except ValueError:
        return None

    # Convert to millions
    if unit == "b":
        return number * 1000  # billions to millions
    elif unit == "m":
        return number
    elif unit == "k":
        return number / 1000  # thousands to millions
    else:
        # Assume millions if no unit
        return number


async def scrape_team_market_value(team_name: str, use_cache: bool = True) -> dict[str, Any] | None:
    """Scrape team squad market value from Transfermarkt.

    Args:
        team_name: Team name (must be in TRANSFERMARKT_TEAM_URLS)
        use_cache: Whether to use cached data (TTL: 7 days)

    Returns:
        Dict with market value data, or None if scraping fails
        {
            "team_name": str,
            "total_market_value": float,  # millions of euros
            "avg_player_value": float,     # millions of euros
            "num_players": int,
            "source": "transfermarkt",
            "scraped_at": str,             # ISO timestamp
            "url": str
        }
    """
    url = TRANSFERMARKT_TEAM_URLS.get(team_name)
    if not url:
        return None

    # Check cache first
    if use_cache:
        cached = get_cached_market_value(team_name)
        if cached:
            return cached

    # Headers to mimic browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Find total market value (usually in a box with class "data-header__market-value")
            market_value_elem = soup.find("a", {"class": "data-header__market-value-wrapper"})
            if not market_value_elem:
                # Try alternative selector.
                # bs4 supports name + string together (Tag.find dispatches it to
                # find_all) but publishes no overload for the combination - see
                # the "way too much code for a rarely used feature" TODO in
                # bs4/element.py. Stub gap, not a call error.
                market_value_elem = soup.find(  # type: ignore[call-overload]
                    "div", string=re.compile(r"Total market value", re.IGNORECASE)
                )

            total_value = None
            if market_value_elem:
                value_text = market_value_elem.get_text(strip=True)
                total_value = parse_market_value(value_text)

            # Count squad size
            player_rows = soup.find_all("tr", {"class": re.compile(r"even|odd")})
            num_players = len(player_rows) if player_rows else 26  # Default squad size

            if total_value is None:
                return None

            result = {
                "team_name": team_name,
                "total_market_value": total_value,
                "avg_player_value": total_value / num_players if num_players > 0 else 0,
                "num_players": num_players,
                "source": "transfermarkt",
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "url": url
            }

            # Cache result
            cache_market_value(result)

            return result

    except Exception as e:
        logger.error("Error scraping Transfermarkt for %s: %s", team_name, e, exc_info=True)
        return None


def get_cached_market_value(team_name: str, ttl_days: int = 7) -> dict[str, Any] | None:
    """Get cached market value from database.

    Args:
        team_name: Team name
        ttl_days: Cache TTL in days (default: 7)

    Returns:
        Cached market value data, or None if not found or expired
    """
    session = get_prediction_session()
    try:
        from app.models.world_cup_prediction import TeamMarketValue

        cached = session.query(TeamMarketValue).filter_by(team_name=team_name).first()

        if not cached:
            return None

        # Check if expired
        # SQLite stores naive datetimes; attach UTC tzinfo before subtracting.
        scraped_at = cached.scraped_at.replace(tzinfo=timezone.utc) if cached.scraped_at else None
        if not scraped_at:
            return None
        age = datetime.now(timezone.utc) - scraped_at
        if age > timedelta(days=ttl_days):
            return None

        return {
            "team_name": cached.team_name,
            "total_market_value": cached.total_market_value,
            "avg_player_value": cached.avg_player_value,
            "num_players": cached.num_players,
            "source": "transfermarkt_cached",
            "scraped_at": cached.scraped_at.isoformat(),
            "url": cached.url,
            "cache_age_hours": age.total_seconds() / 3600
        }

    finally:
        close_prediction_session(session)


def cache_market_value(data: dict[str, Any]) -> None:
    """Cache market value data to database.

    Args:
        data: Market value data dict from scrape_team_market_value()
    """
    session = get_prediction_session()
    try:
        from app.models.world_cup_prediction import TeamMarketValue

        existing = session.query(TeamMarketValue).filter_by(team_name=data["team_name"]).first()

        if existing:
            # Update existing
            existing.total_market_value = data["total_market_value"]
            existing.avg_player_value = data["avg_player_value"]
            existing.num_players = data["num_players"]
            existing.scraped_at = datetime.now(timezone.utc)
            existing.url = data["url"]
        else:
            # Insert new
            new_entry = TeamMarketValue(
                team_name=data["team_name"],
                total_market_value=data["total_market_value"],
                avg_player_value=data["avg_player_value"],
                num_players=data["num_players"],
                scraped_at=datetime.now(timezone.utc),
                url=data["url"]
            )
            session.add(new_entry)

        session.commit()

    except Exception as e:
        session.rollback()
        logger.error("Error caching market value: %s", e, exc_info=True)

    finally:
        close_prediction_session(session)


async def batch_scrape_world_cup_teams(delay_seconds: float = 2.0) -> dict[str, Any]:
    """Scrape market values for all World Cup 2026 teams.

    Args:
        delay_seconds: Delay between requests to avoid rate limiting

    Returns:
        Summary with success/failure counts
    """
    results: dict[str, Any] = {
        "status": "ok",
        "total": len(TRANSFERMARKT_TEAM_URLS),
        "succeeded": 0,
        "failed": 0,
        "teams": []
    }

    for i, team_name in enumerate(TRANSFERMARKT_TEAM_URLS.keys()):
        logger.info("Scraping %s (%s/%s)...", team_name, i + 1, len(TRANSFERMARKT_TEAM_URLS))

        data = await scrape_team_market_value(team_name, use_cache=False)

        if data:
            results["succeeded"] += 1
            results["teams"].append({
                "team": team_name,
                "value": data["total_market_value"],
                "status": "ok"
            })
            logger.info("  ✓ %s: €%.1fm", team_name, data['total_market_value'])
        else:
            results["failed"] += 1
            results["teams"].append({
                "team": team_name,
                "status": "failed"
            })
            logger.warning("  ✗ %s: Failed", team_name)

        # Rate limiting
        if i < len(TRANSFERMARKT_TEAM_URLS) - 1:
            time.sleep(delay_seconds)

    return results
