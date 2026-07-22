"""Competition code aliases and MultiAdapter prefix maps.

Shared by predictions routes, MultiAdapter schedule filtering, and the
betting catalog so FE aliases (pl, serie-a) match Kernel competition codes
stored on fixtures (epl, seriea).
"""
from __future__ import annotations

# Request / FE aliases → preferred display canonical (betting catalog ids).
# Fixture rows may still use historical codes (seriea, ligue1); normalize()
# maps both sides into a single compare key.
COMPETITION_ALIASES: dict[str, str] = {
    "wc": "world_cup",
    "worldcup": "world_cup",
    "world_cup": "world_cup",
    "ucl": "ucl",
    "epl": "epl",
    "pl": "epl",
    "premier_league": "epl",
    "laliga": "laliga",
    "la_liga": "laliga",
    "bundesliga": "bundesliga",
    "seriea": "serie_a",
    "serie_a": "serie_a",
    "ligue1": "ligue_1",
    "ligue_1": "ligue_1",
    "nba": "nba",
    "mlb": "mlb",
    "nhl": "nhl",
}

# MultiAdapter match_id prefix → competition code as stored on fixtures.
PREFIX_TO_COMPETITION: dict[str, str] = {
    "wc-": "world_cup",
    "ucl-": "ucl",
    "epl-": "epl",
    "laliga-": "laliga",
    "bundesliga-": "bundesliga",
    "seriea-": "seriea",
    "ligue1-": "ligue1",
    "nba-": "nba",
    "mlb-": "mlb",
    "nhl-": "nhl",
    "lol-": "lol",
}

PREFIX_TO_SPORT: dict[str, str] = {
    "wc-": "football",
    "ucl-": "football",
    "epl-": "football",
    "laliga-": "football",
    "bundesliga-": "football",
    "seriea-": "football",
    "ligue1-": "football",
    "nba-": "basketball",
    "mlb-": "baseball",
    "nhl-": "hockey",
    "lol-": "lol",
}

COMPETITION_SPORT: dict[str, str] = {
    "wc": "football",
    "world_cup": "football",
    "ucl": "football",
    "epl": "football",
    "laliga": "football",
    "bundesliga": "football",
    "seriea": "football",
    "serie_a": "football",
    "ligue1": "football",
    "ligue_1": "football",
    "nba": "basketball",
    "mlb": "baseball",
    "nhl": "hockey",
    "lol": "lol",
    "lol_lck": "lol",
    "lol_lpl": "lol",
    "lol_lec": "lol",
    "lol_worlds": "lol",
}


def normalize_competition_code(raw: str | None) -> str | None:
    """Map FE/query aliases to a comparable canonical code; None if empty."""
    if raw is None:
        return None
    key = raw.strip().lower().replace("-", "_")
    if not key:
        return None
    return COMPETITION_ALIASES.get(key, key)


def competitions_equivalent(a: str | None, b: str | None) -> bool:
    """True if both sides normalize to the same competition."""
    na = normalize_competition_code(a)
    nb = normalize_competition_code(b)
    if na is None or nb is None:
        return False
    return na == nb


def competition_code_for_prefix(prefix: str) -> str | None:
    return PREFIX_TO_COMPETITION.get(prefix)


def sport_for_prefix(prefix: str) -> str | None:
    return PREFIX_TO_SPORT.get(prefix)
