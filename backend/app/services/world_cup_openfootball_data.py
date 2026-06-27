"""Read-only helpers for the openfootball World Cup 2026 JSON snapshot."""

from __future__ import annotations

import json
import os
import unicodedata
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "openfootball-2026"
DATA_SOURCE = "github_openfootball_worldcup_json"


def build_openfootball_match_context(
    home_team: str,
    away_team: str,
    *,
    venue: str | None = None,
    city: str | None = None,
    match_date: datetime | date | str | None = None,
) -> dict[str, Any] | None:
    """Build a compact local-data context for a World Cup match."""

    home_context = _build_team_context(home_team, reference_date=match_date)
    away_context = _build_team_context(away_team, reference_date=match_date)
    fixture = get_fixture_reference(home_team, away_team, match_date=match_date)
    stadium = get_stadium_metadata(
        venue=venue,
        city=city,
        ground=fixture.get("ground") if fixture else None,
    )

    if not any((home_context, away_context, fixture, stadium)):
        return None

    return {
        "data_source": DATA_SOURCE,
        "home_team": home_context or {},
        "away_team": away_context or {},
        "fixture": fixture,
        "stadium": stadium,
    }


def get_team_metadata(team_name: str) -> dict[str, Any] | None:
    row = _find_team_row(team_name, "worldcup.teams.json")
    if not row:
        return None
    return {
        "name": row.get("name"),
        "name_normalised": row.get("name_normalised"),
        "fifa_code": row.get("fifa_code"),
        "continent": row.get("continent"),
        "confed": row.get("confed"),
        "group": row.get("group"),
        "data_source": DATA_SOURCE,
    }


def get_squad_summary(
    team_name: str,
    *,
    reference_date: datetime | date | str | None = None,
) -> dict[str, Any] | None:
    row = _find_team_row(team_name, "worldcup.squads.json")
    if not row:
        return None
    players = row.get("players")
    if not isinstance(players, list):
        return None

    position_counts: dict[str, int] = {}
    ages: list[float] = []
    ref_date = _coerce_date(reference_date)
    for player in players:
        if not isinstance(player, dict):
            continue
        position = str(player.get("pos") or "UNK")
        position_counts[position] = position_counts.get(position, 0) + 1
        if ref_date:
            birth_date = _coerce_date(player.get("date_of_birth"))
            if birth_date:
                ages.append((ref_date - birth_date).days / 365.2425)

    summary: dict[str, Any] = {
        "team": row.get("name"),
        "fifa_code": row.get("fifa_code"),
        "group": row.get("group"),
        "player_count": len(players),
        "position_counts": position_counts,
        "data_source": DATA_SOURCE,
    }
    if ages:
        summary["average_age"] = round(sum(ages) / len(ages), 1)
    return summary


def get_fixture_reference(
    home_team: str,
    away_team: str,
    *,
    match_date: datetime | date | str | None = None,
) -> dict[str, Any] | None:
    payload = _load_json("worldcup.json")
    matches = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(matches, list):
        return None

    home_key = _team_key(home_team)
    away_key = _team_key(away_team)
    wanted_date = _coerce_date(match_date)
    fallback: dict[str, Any] | None = None

    for match in matches:
        if not isinstance(match, dict):
            continue
        team1_key = _team_key(str(match.get("team1") or ""))
        team2_key = _team_key(str(match.get("team2") or ""))
        if {team1_key, team2_key} != {home_key, away_key}:
            continue

        reference = {
            "round": match.get("round"),
            "date": match.get("date"),
            "time": match.get("time"),
            "team1": match.get("team1"),
            "team2": match.get("team2"),
            "group": match.get("group"),
            "ground": match.get("ground"),
            "data_source": DATA_SOURCE,
        }
        if not wanted_date or _coerce_date(match.get("date")) == wanted_date:
            return reference
        if fallback is None:
            fallback = reference

    return fallback


def get_stadium_metadata(
    *,
    venue: str | None = None,
    city: str | None = None,
    ground: str | None = None,
) -> dict[str, Any] | None:
    payload = _load_json("worldcup.stadiums.json")
    stadiums = payload.get("stadiums") if isinstance(payload, dict) else None
    if not isinstance(stadiums, list):
        return None

    targets = [_normalise_text(value) for value in (venue, city, ground) if value]
    if not targets:
        return None

    for stadium in stadiums:
        if not isinstance(stadium, dict):
            continue
        name_key = _normalise_text(stadium.get("name"))
        city_key = _normalise_text(stadium.get("city"))
        if any(
            target == name_key
            or target == city_key
            or (target and target in city_key)
            or (city_key and city_key in target)
            for target in targets
        ):
            return {
                "name": stadium.get("name"),
                "city": stadium.get("city"),
                "country_code": stadium.get("cc"),
                "timezone": stadium.get("timezone"),
                "capacity": stadium.get("capacity"),
                "data_source": DATA_SOURCE,
            }
    return None


def clear_openfootball_cache() -> None:
    _load_json_file.cache_clear()


def _build_team_context(
    team_name: str,
    *,
    reference_date: datetime | date | str | None,
) -> dict[str, Any] | None:
    metadata = get_team_metadata(team_name)
    squad = get_squad_summary(team_name, reference_date=reference_date)
    if not metadata and not squad:
        return None
    return {
        "metadata": metadata,
        "squad": squad,
    }


def _find_team_row(team_name: str, filename: str) -> dict[str, Any] | None:
    payload = _load_json(filename)
    if not isinstance(payload, list):
        return None

    target = _team_key(team_name)
    for row in payload:
        if not isinstance(row, dict):
            continue
        names = [
            str(row.get("name") or ""),
            str(row.get("name_normalised") or ""),
        ]
        if any(_team_key(name) == target for name in names if name):
            return row
    return None


def _load_json(filename: str) -> Any:
    return _load_json_file(str(_data_dir() / filename))


@lru_cache(maxsize=16)
def _load_json_file(path_str: str) -> Any:
    path = Path(path_str)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _data_dir() -> Path:
    return Path(os.getenv("WORLD_CUP_OPENFOOTBALL_DATA_DIR", str(DEFAULT_DATA_DIR)))


def _team_key(value: str) -> str:
    return _normalise_text(value)


def _normalise_text(value: Any) -> str:
    normalised = unicodedata.normalize("NFKD", str(value or ""))
    asciiish = "".join(ch for ch in normalised if not unicodedata.combining(ch))
    return "".join(ch.lower() for ch in asciiish if ch.isalnum())


def _coerce_date(value: datetime | date | str | Any | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        try:
            return date.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
