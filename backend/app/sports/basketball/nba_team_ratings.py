# backend/app/sports/basketball/nba_team_ratings.py
"""Static NBA team ORtg/DRtg for soft net_rating (P1-B4).

Soft multi-year-ish points-per-100 levels (not live season scrape).
Missing team → None. Engine formula lives in BasketballEngine (unchanged).
"""
from __future__ import annotations

PRIMARY_FRANCHISES: tuple[str, ...] = (
    "Atlanta Hawks",
    "Boston Celtics",
    "Brooklyn Nets",
    "Charlotte Hornets",
    "Chicago Bulls",
    "Cleveland Cavaliers",
    "Dallas Mavericks",
    "Denver Nuggets",
    "Detroit Pistons",
    "Golden State Warriors",
    "Houston Rockets",
    "Indiana Pacers",
    "Los Angeles Clippers",
    "Los Angeles Lakers",
    "Memphis Grizzlies",
    "Miami Heat",
    "Milwaukee Bucks",
    "Minnesota Timberwolves",
    "New Orleans Pelicans",
    "New York Knicks",
    "Oklahoma City Thunder",
    "Orlando Magic",
    "Philadelphia 76ers",
    "Phoenix Suns",
    "Portland Trail Blazers",
    "Sacramento Kings",
    "San Antonio Spurs",
    "Toronto Raptors",
    "Utah Jazz",
    "Washington Wizards",
)

# Soft static ORtg/DRtg (1.0-possession points per 100). Soft signal only.
# Alias keys mirror team_geo dual Clippers names.
_TEAM_RATINGS: dict[str, dict[str, float]] = {
    "Atlanta Hawks": {"ortg": 115.0, "drtg": 116.0},
    "Boston Celtics": {"ortg": 118.0, "drtg": 109.0},
    "Brooklyn Nets": {"ortg": 110.0, "drtg": 115.0},
    "Charlotte Hornets": {"ortg": 108.0, "drtg": 117.0},
    "Chicago Bulls": {"ortg": 112.0, "drtg": 114.0},
    "Cleveland Cavaliers": {"ortg": 116.0, "drtg": 110.0},
    "Dallas Mavericks": {"ortg": 114.0, "drtg": 113.0},
    "Denver Nuggets": {"ortg": 117.0, "drtg": 112.0},
    "Detroit Pistons": {"ortg": 109.0, "drtg": 116.0},
    "Golden State Warriors": {"ortg": 115.0, "drtg": 112.0},
    "Houston Rockets": {"ortg": 113.0, "drtg": 110.0},
    "Indiana Pacers": {"ortg": 116.0, "drtg": 114.0},
    "LA Clippers": {"ortg": 114.0, "drtg": 113.0},
    "Los Angeles Clippers": {"ortg": 114.0, "drtg": 113.0},
    "Los Angeles Lakers": {"ortg": 115.0, "drtg": 113.0},
    "Memphis Grizzlies": {"ortg": 111.0, "drtg": 116.0},
    "Miami Heat": {"ortg": 112.0, "drtg": 111.0},
    "Milwaukee Bucks": {"ortg": 114.0, "drtg": 112.0},
    "Minnesota Timberwolves": {"ortg": 114.0, "drtg": 109.0},
    "New Orleans Pelicans": {"ortg": 112.0, "drtg": 113.0},
    "New York Knicks": {"ortg": 117.0, "drtg": 111.0},
    "Oklahoma City Thunder": {"ortg": 118.0, "drtg": 106.0},
    "Orlando Magic": {"ortg": 111.0, "drtg": 108.0},
    "Philadelphia 76ers": {"ortg": 112.0, "drtg": 115.0},
    "Phoenix Suns": {"ortg": 115.0, "drtg": 114.0},
    "Portland Trail Blazers": {"ortg": 109.0, "drtg": 116.0},
    "Sacramento Kings": {"ortg": 114.0, "drtg": 115.0},
    "San Antonio Spurs": {"ortg": 111.0, "drtg": 114.0},
    "Toronto Raptors": {"ortg": 112.0, "drtg": 116.0},
    "Utah Jazz": {"ortg": 110.0, "drtg": 118.0},
    "Washington Wizards": {"ortg": 107.0, "drtg": 119.0},
}


def ratings_for_team(team_name: str) -> dict[str, float] | None:
    """Exact full-name lookup. Returns a shallow copy of {ortg, drtg} or None."""
    name = (team_name or "").strip()
    if not name:
        return None
    row = _TEAM_RATINGS.get(name)
    if row is None:
        return None
    return {"ortg": float(row["ortg"]), "drtg": float(row["drtg"])}
