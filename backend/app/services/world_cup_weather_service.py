"""Weather service for World Cup match predictions.

Fetches weather data from Open-Meteo API (free, no API key required)
for match venues, providing temperature, conditions, wind, and humidity
that feed into the prediction factors.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Cache weather data for 6 hours (weather doesn't change that fast)
_weather_cache: dict[str, dict[str, Any]] = {}

# Open-Meteo geocoding + forecast API (free, no key needed)
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# World Cup 2026 host city coordinates (lat, lon)
# Used as fallback if geocoding fails
WORLD_CUP_2026_VENUES: dict[str, tuple[float, float]] = {
    # USA
    "New York": (40.7128, -74.0060),
    "New Jersey": (40.7128, -74.0060),
    "Los Angeles": (34.0522, -118.2437),
    "Dallas": (32.7767, -96.7970),
    "Atlanta": (33.7490, -84.3880),
    "Houston": (29.7604, -95.3698),
    "Seattle": (47.6062, -122.3321),
    "San Francisco": (37.7749, -122.4194),
    "Boston": (42.3601, -71.0589),
    "Philadelphia": (39.9526, -75.1652),
    "Miami": (25.7617, -80.1918),
    "Kansas City": (39.0997, -94.5786),
    # Canada
    "Toronto": (43.6532, -79.3832),
    "Vancouver": (49.2827, -123.1207),
    # Mexico
    "Mexico City": (19.4326, -99.1332),
    "Guadalajara": (20.6597, -103.3496),
    "Monterrey": (25.6866, -100.3161),
}


def _get_venue_coordinates(venue: str | None, city: str | None) -> tuple[float, float] | None:
    """Get coordinates for a venue/city, using cache or geocoding API."""
    if not venue and not city:
        return None

    location = city or venue or ""

    # Check known venues first
    for name, coords in WORLD_CUP_2026_VENUES.items():
        if name.lower() in location.lower() or location.lower() in name.lower():
            return coords

    # Try geocoding API
    try:
        resp = httpx.get(
            GEOCODING_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                r = results[0]
                return (r["latitude"], r["longitude"])
    except Exception as e:
        logger.debug("Geocoding failed for %s: %s", location, e)

    return None


def get_match_weather(
    venue: str | None = None,
    city: str | None = None,
    match_date: str | None = None,
) -> dict[str, Any]:
    """Get weather forecast for a match venue.

    Uses Open-Meteo API (free, no key required). Caches results for 6 hours.

    Args:
        venue: Venue name (e.g., "MetLife Stadium")
        city: City name (e.g., "New York")
        match_date: ISO date string for the match (used for daily forecast)

    Returns:
        Weather dict with condition, temperature, wind_speed, humidity
    """
    cache_key = f"{venue or ''}_{city or ''}_{match_date or ''}"

    # Check cache
    if cache_key in _weather_cache:
        return _weather_cache[cache_key]

    # Default weather (clear, 20°C)
    default_weather = {
        "condition": "clear",
        "temperature": 20.0,
        "wind_speed": 10.0,
        "humidity": 50.0,
        "source": "default",
    }

    coords = _get_venue_coordinates(venue, city)
    if not coords:
        default_weather["source"] = "default_no_location"
        return default_weather

    lat, lon = coords

    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,wind_speed_10m,relative_humidity_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,wind_speed_10m_max",
            "timezone": "auto",
            "forecast_days": 7,
        }

        resp = httpx.get(FORECAST_URL, params=params, timeout=10.0)
        if resp.status_code != 200:
            default_weather["source"] = f"api_error_{resp.status_code}"
            return default_weather

        data = resp.json()

        # Use current weather if available
        current = data.get("current", {})
        if current:
            weather_code = current.get("weather_code", 0)
            condition = _weather_code_to_condition(weather_code)
            temperature = round(current.get("temperature_2m", 20.0), 1)
            wind_speed = round(current.get("wind_speed_10m", 10.0), 1)
            humidity = round(current.get("relative_humidity_2m", 50.0), 1)

            weather = {
                "condition": condition,
                "temperature": temperature,
                "wind_speed": wind_speed,
                "humidity": humidity,
                "source": "open_meteo",
                "location": {"lat": lat, "lon": lon},
            }
            _weather_cache[cache_key] = weather
            return weather

    except Exception as e:
        logger.warning("Weather fetch failed for %s: %s", cache_key, e)
        default_weather["source"] = f"error: {type(e).__name__}"

    return default_weather


def _weather_code_to_condition(code: int) -> str:
    """Convert Open-Meteo weather code to simple condition string."""
    if code == 0:
        return "clear"
    elif code in (1, 2, 3):
        return "partly_cloudy"
    elif code in (45, 48):
        return "fog"
    elif code in (51, 53, 55, 56, 57):
        return "drizzle"
    elif code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    elif code in (71, 73, 75, 77, 85, 86):
        return "snow"
    elif code in (95, 96, 99):
        return "thunderstorm"
    else:
        return "unknown"
