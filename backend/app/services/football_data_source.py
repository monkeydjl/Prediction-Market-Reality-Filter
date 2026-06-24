"""Football-Data.org integration for World Cup fixtures.

Provides free access to World Cup data with 10 requests/minute limit.
Documentation: https://www.football-data.org/documentation/quickstart
"""

import os
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

FOOTBALL_DATA_API_KEY = os.getenv('FOOTBALL_DATA_API_KEY', '')
FOOTBALL_DATA_BASE_URL = os.getenv('FOOTBALL_DATA_BASE_URL', 'https://api.football-data.org/v4')


class FootballDataAPIError(Exception):
    """Football-Data.org API error."""
    pass


def fetch_world_cup_fixtures(season: int = 2026) -> list[dict[str, Any]]:
    """Fetch World Cup fixtures from Football-Data.org.

    Args:
        season: Tournament year (default: 2026)

    Returns:
        List of fixture dictionaries

    Raises:
        FootballDataAPIError: If API request fails
    """

    if not FOOTBALL_DATA_API_KEY:
        raise FootballDataAPIError("FOOTBALL_DATA_API_KEY not configured")

    try:
        response = httpx.get(
            f'{FOOTBALL_DATA_BASE_URL}/competitions/WC/matches',
            headers={'X-Auth-Token': FOOTBALL_DATA_API_KEY},
            params={'season': season},
            timeout=30.0
        )

        if response.status_code == 403:
            raise FootballDataAPIError("API key invalid or access forbidden")
        elif response.status_code == 429:
            raise FootballDataAPIError("Rate limit exceeded (10 requests/minute)")
        elif response.status_code != 200:
            raise FootballDataAPIError(f"API error: {response.status_code} - {response.text[:200]}")

        data = response.json()
        matches = data.get('matches', [])

        return matches

    except httpx.TimeoutException:
        raise FootballDataAPIError("Request timeout")
    except httpx.RequestError as e:
        raise FootballDataAPIError(f"Request failed: {e}")


def parse_fixture(match_data: dict[str, Any]) -> dict[str, Any] | None:
    """Parse Football-Data.org match into our format.

    Args:
        match_data: Raw match data from API

    Returns:
        Parsed fixture dict or None if invalid
    """

    match_id = match_data.get('id')
    if not match_id:
        return None

    home_team = match_data.get('homeTeam', {}).get('name', '')
    away_team = match_data.get('awayTeam', {}).get('name', '')

    if not home_team or not away_team:
        return None

    # Parse kickoff time
    utc_date = match_data.get('utcDate', '')
    try:
        kickoff_utc = datetime.fromisoformat(utc_date.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None

    # Map stage
    stage_raw = match_data.get('stage', '').upper()
    stage_mapping = {
        'GROUP_STAGE': 'GROUP_STAGE',
        'LAST_32': 'ROUND_OF_32',
        'LAST_16': 'ROUND_OF_16',
        'ROUND_OF_16': 'ROUND_OF_16',
        'QUARTER_FINALS': 'QUARTERFINAL',
        'SEMI_FINALS': 'SEMIFINAL',
        'THIRD_PLACE': 'THIRD_PLACE',
        'FINAL': 'FINAL'
    }
    stage = stage_mapping.get(stage_raw, 'UNKNOWN')

    # Extract group from matchday (for group stage)
    group = match_data.get('group')

    # Map status
    status_raw = match_data.get('status', '')
    status_mapping = {
        'TIMED': 'scheduled',
        'SCHEDULED': 'scheduled',
        'IN_PLAY': 'in_play',
        'LIVE': 'in_play',
        'PAUSED': 'in_play',
        'FINISHED': 'finished',
        'AWARDED': 'finished',
        'POSTPONED': 'postponed',
        'CANCELLED': 'cancelled',
        'SUSPENDED': 'suspended'
    }
    match_status = status_mapping.get(status_raw, 'scheduled')

    # Venue info
    venue_name = match_data.get('venue', '')

    # Extract scores (if match has started)
    score = match_data.get('score', {})
    fulltime = score.get('fullTime', {})
    home_score = fulltime.get('home')
    away_score = fulltime.get('away')

    return {
        'match_id': f'fd-{match_id}',
        'fixture_id': str(match_id),
        'home_team': home_team,
        'away_team': away_team,
        'kickoff_utc': kickoff_utc,
        'venue': venue_name or 'Unknown',
        'stage': stage,
        'group': group,
        'status': match_status,
        'home_score': home_score,
        'away_score': away_score
    }


def get_fixture_count_by_status() -> dict[str, int]:
    """Get count of fixtures by status for monitoring.

    Returns:
        Dict with status counts
    """

    try:
        fixtures = fetch_world_cup_fixtures()

        from collections import Counter
        statuses = Counter(f.get('status', '') for f in fixtures)

        return {
            'total': len(fixtures),
            'scheduled': statuses.get('TIMED', 0) + statuses.get('SCHEDULED', 0),
            'live': statuses.get('LIVE', 0) + statuses.get('IN_PLAY', 0),
            'finished': statuses.get('FINISHED', 0),
            'other': sum(v for k, v in statuses.items() if k not in ['TIMED', 'SCHEDULED', 'LIVE', 'IN_PLAY', 'FINISHED'])
        }

    except FootballDataAPIError:
        return {'error': True}
