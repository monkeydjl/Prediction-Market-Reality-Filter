# backend/app/sports/_shared/team_geo.py
"""Coarse team home-city geo for soft travel / timezone signals.

Used by NBA / NHL (and optionally MLB) adapters. Distances are great-circle
approximations in km; timezone offsets are integer hours from UTC (winter-ish
US/Canada defaults — soft signal only, not DST-accurate).
"""
from __future__ import annotations

import math
import re
from typing import Any

# (lat, lon, utc_offset_hours)
_NBA_CITIES: dict[str, tuple[float, float, int]] = {
    "Atlanta Hawks": (33.757, -84.401, -5),
    "Boston Celtics": (42.366, -71.062, -5),
    "Brooklyn Nets": (40.683, -73.975, -5),
    "Charlotte Hornets": (35.225, -80.839, -5),
    "Chicago Bulls": (41.881, -87.674, -6),
    "Cleveland Cavaliers": (41.496, -81.688, -5),
    "Dallas Mavericks": (32.790, -96.810, -6),
    "Denver Nuggets": (39.749, -105.008, -7),
    "Detroit Pistons": (42.341, -83.055, -5),
    "Golden State Warriors": (37.768, -122.388, -8),
    "Houston Rockets": (29.751, -95.362, -6),
    "Indiana Pacers": (39.764, -86.155, -5),
    "LA Clippers": (34.043, -118.267, -8),
    "Los Angeles Clippers": (34.043, -118.267, -8),
    "Los Angeles Lakers": (34.043, -118.267, -8),
    "Memphis Grizzlies": (35.138, -90.051, -6),
    "Miami Heat": (25.781, -80.188, -5),
    "Milwaukee Bucks": (43.045, -87.917, -6),
    "Minnesota Timberwolves": (44.980, -93.276, -6),
    "New Orleans Pelicans": (29.949, -90.082, -6),
    "New York Knicks": (40.751, -73.993, -5),
    "Oklahoma City Thunder": (35.463, -97.515, -6),
    "Orlando Magic": (28.539, -81.384, -5),
    "Philadelphia 76ers": (39.901, -75.172, -5),
    "Phoenix Suns": (33.446, -112.071, -7),
    "Portland Trail Blazers": (45.532, -122.667, -8),
    "Sacramento Kings": (38.580, -121.500, -8),
    "San Antonio Spurs": (29.427, -98.437, -6),
    "Toronto Raptors": (43.644, -79.379, -5),
    "Utah Jazz": (40.768, -111.901, -7),
    "Washington Wizards": (38.898, -77.021, -5),
}

_NHL_CITIES: dict[str, tuple[float, float, int]] = {
    "Anaheim Ducks": (33.808, -117.876, -8),
    "Arizona Coyotes": (33.532, -112.261, -7),
    "Utah Hockey Club": (40.768, -111.901, -7),
    "Utah Utah Hockey Club": (40.768, -111.901, -7),
    "Utah Mammoth": (40.768, -111.901, -7),
    "Boston Bruins": (42.366, -71.062, -5),
    "Buffalo Sabres": (42.875, -78.876, -5),
    "Calgary Flames": (51.037, -114.052, -7),
    "Carolina Hurricanes": (35.803, -78.722, -5),
    "Chicago Blackhawks": (41.881, -87.674, -6),
    "Colorado Avalanche": (39.749, -105.008, -7),
    "Columbus Blue Jackets": (39.969, -83.006, -5),
    "Dallas Stars": (32.790, -96.810, -6),
    "Detroit Red Wings": (42.341, -83.055, -5),
    "Edmonton Oilers": (53.547, -113.498, -7),
    "Florida Panthers": (26.158, -80.325, -5),
    "Los Angeles Kings": (34.043, -118.267, -8),
    "Minnesota Wild": (44.945, -93.101, -6),
    "Montreal Canadiens": (45.496, -73.569, -5),
    "Nashville Predators": (36.159, -86.778, -6),
    "New Jersey Devils": (40.734, -74.171, -5),
    "New York Islanders": (40.723, -73.591, -5),
    "New York Rangers": (40.751, -73.993, -5),
    "Ottawa Senators": (45.297, -75.927, -5),
    "Philadelphia Flyers": (39.901, -75.172, -5),
    "Pittsburgh Penguins": (40.439, -79.989, -5),
    "San Jose Sharks": (37.333, -121.901, -8),
    "Seattle Kraken": (47.622, -122.354, -8),
    "St. Louis Blues": (38.627, -90.203, -6),
    "Tampa Bay Lightning": (27.943, -82.452, -5),
    "Toronto Maple Leafs": (43.643, -79.379, -5),
    "Vancouver Canucks": (49.278, -123.109, -8),
    "Vegas Golden Knights": (36.103, -115.178, -8),
    "Washington Capitals": (38.898, -77.021, -5),
    "Winnipeg Jets": (49.893, -97.144, -6),
}

# Coarse national-team home venues (capitals) for football travel soft signal
_FOOTBALL_NATIONAL: dict[str, tuple[float, float, int]] = {
    "Argentina": (-34.603, -58.381, -3),
    "Brazil": (-15.794, -47.882, -3),
    "France": (48.857, 2.352, 1),
    "Germany": (52.520, 13.405, 1),
    "Spain": (40.417, -3.704, 1),
    "England": (51.507, -0.128, 0),
    "Portugal": (38.722, -9.139, 0),
    "Netherlands": (52.368, 4.904, 1),
    "Italy": (41.903, 12.496, 1),
    "Belgium": (50.850, 4.351, 1),
    "Croatia": (45.815, 15.982, 1),
    "Uruguay": (-34.901, -56.164, -3),
    "Mexico": (19.432, -99.133, -6),
    "USA": (38.907, -77.037, -5),
    "United States": (38.907, -77.037, -5),
    "Canada": (45.421, -75.697, -5),
    "Japan": (35.676, 139.650, 9),
    "South Korea": (37.567, 126.978, 9),
    "Korea Republic": (37.567, 126.978, 9),
    "Australia": (-35.280, 149.130, 10),
    "Morocco": (34.021, -6.842, 1),
    "Senegal": (14.693, -17.447, 0),
    "Nigeria": (9.076, 7.399, 1),
    "Ghana": (5.560, -0.205, 0),
    "Cameroon": (3.848, 11.502, 1),
    "Egypt": (30.044, 31.236, 2),
    "Saudi Arabia": (24.713, 46.675, 3),
    "Iran": (35.689, 51.389, 3),
    "Qatar": (25.286, 51.532, 3),
    "Poland": (52.230, 21.012, 1),
    "Switzerland": (46.948, 7.447, 1),
    "Denmark": (55.676, 12.568, 1),
    "Sweden": (59.329, 18.069, 1),
    "Serbia": (44.787, 20.448, 1),
    "Austria": (48.208, 16.373, 1),
    "Ukraine": (50.450, 30.523, 2),
    "Turkey": (39.933, 32.860, 3),
    "Ecuador": (-0.180, -78.468, -5),
    "Colombia": (4.711, -74.072, -5),
    "Chile": (-33.449, -70.669, -4),
    "Peru": (-12.047, -77.043, -5),
    "Paraguay": (-25.264, -57.576, -4),
    "Venezuela": (10.481, -66.903, -4),
    "Wales": (51.482, -3.179, 0),
    "Scotland": (55.953, -3.189, 0),
    "Ireland": (53.350, -6.260, 0),
    "Republic of Ireland": (53.350, -6.260, 0),
}

_MLB_CITIES: dict[str, tuple[float, float, int]] = {
    "Arizona Diamondbacks": (33.445, -112.067, -7),
    "Atlanta Braves": (33.890, -84.468, -5),
    "Baltimore Orioles": (39.284, -76.622, -5),
    "Boston Red Sox": (42.346, -71.097, -5),
    "Chicago Cubs": (41.948, -87.655, -6),
    "Chicago White Sox": (41.830, -87.634, -6),
    "Cincinnati Reds": (39.097, -84.508, -5),
    "Cleveland Guardians": (41.496, -81.685, -5),
    "Colorado Rockies": (39.756, -104.994, -7),
    "Detroit Tigers": (42.339, -83.049, -5),
    "Houston Astros": (29.757, -95.355, -6),
    "Kansas City Royals": (39.051, -94.480, -6),
    "Los Angeles Angels": (33.800, -117.883, -8),
    "Los Angeles Dodgers": (34.074, -118.240, -8),
    "Miami Marlins": (25.778, -80.220, -5),
    "Milwaukee Brewers": (43.028, -87.971, -6),
    "Minnesota Twins": (44.982, -93.278, -6),
    "New York Mets": (40.757, -73.846, -5),
    "New York Yankees": (40.829, -73.926, -5),
    "Oakland Athletics": (37.752, -122.201, -8),
    "Athletics": (37.752, -122.201, -8),
    "Philadelphia Phillies": (39.906, -75.167, -5),
    "Pittsburgh Pirates": (40.447, -80.006, -5),
    "San Diego Padres": (32.707, -117.157, -8),
    "San Francisco Giants": (37.778, -122.389, -8),
    "Seattle Mariners": (47.591, -122.333, -8),
    "St. Louis Cardinals": (38.623, -90.193, -6),
    "Tampa Bay Rays": (27.768, -82.653, -5),
    "Texas Rangers": (32.747, -97.084, -6),
    "Toronto Blue Jays": (43.641, -79.389, -5),
    "Washington Nationals": (38.873, -77.007, -5),
}


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _lookup(
    name: str,
    table: dict[str, tuple[float, float, int]],
) -> tuple[float, float, int] | None:
    if not name:
        return None
    if name in table:
        return table[name]
    n = _normalize(name)
    for key, val in table.items():
        kn = _normalize(key)
        if n == kn or n in kn or kn in n:
            return val
        # last token match e.g. "Lakers"
        if n.split()[-1] == kn.split()[-1] and len(n.split()[-1]) > 3:
            return val
    return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def resolve_city(
    team_name: str,
    sport: str,
) -> tuple[float, float, int] | None:
    code = (sport or "").lower()
    if code in ("basketball", "nba"):
        return _lookup(team_name, _NBA_CITIES)
    if code in ("hockey", "nhl"):
        return _lookup(team_name, _NHL_CITIES)
    if code in ("baseball", "mlb"):
        return _lookup(team_name, _MLB_CITIES)
    if code in ("football", "soccer", "wc", "world_cup", "epl", "laliga", "ucl"):
        return _lookup(team_name, _FOOTBALL_NATIONAL)
    return (
        _lookup(team_name, _NBA_CITIES)
        or _lookup(team_name, _NHL_CITIES)
        or _lookup(team_name, _MLB_CITIES)
        or _lookup(team_name, _FOOTBALL_NATIONAL)
    )


def travel_between_teams(
    home_team: str,
    away_team: str,
    sport: str,
) -> dict[str, Any]:
    """Return travel metrics for away team visiting home.

    Keys:
      travel_km_away — distance away home-city → home venue city
      travel_km_home — 0 (home stays put) when both known
      timezone_offset_hours_away — away TZ − home TZ (positive = away from west)
      travel_known — bool
    """
    home = resolve_city(home_team, sport)
    away = resolve_city(away_team, sport)
    if home is None or away is None:
        return {
            "travel_km_away": None,
            "travel_km_home": None,
            "timezone_offset_hours_away": None,
            "travel_known": False,
        }
    h_lat, h_lon, h_tz = home
    a_lat, a_lon, a_tz = away
    km = round(haversine_km(a_lat, a_lon, h_lat, h_lon), 1)
    return {
        "travel_km_away": km,
        "travel_km_home": 0.0,
        "timezone_offset_hours_away": int(a_tz - h_tz),
        "travel_known": True,
    }


def travel_prob_home(
    travel_km_away: float | None,
    timezone_offset_hours_away: float | None = None,
) -> tuple[float, bool]:
    """Soft P(home_win) from away travel fatigue. Returns (p, available)."""
    if travel_km_away is None:
        return 0.5, False
    try:
        km = float(travel_km_away)
    except (TypeError, ValueError):
        return 0.5, False
    # ~0.8pp per 1000 km for home, cap 4pp; extra for multi-zone hops
    boost = min(0.04, (max(0.0, km) / 1000.0) * 0.008)
    if timezone_offset_hours_away is not None:
        try:
            tz = abs(float(timezone_offset_hours_away))
            if tz >= 2:
                boost += min(0.015, (tz - 1) * 0.005)
        except (TypeError, ValueError):
            pass
    p = 0.5 + min(0.05, boost)
    return max(0.40, min(0.60, p)), True
