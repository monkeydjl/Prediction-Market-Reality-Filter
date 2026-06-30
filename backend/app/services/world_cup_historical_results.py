"""Historical international results fallback for World Cup prediction factors."""

from __future__ import annotations

import csv
import os
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_RESULTS_PATH = Path(__file__).resolve().parents[2] / "data" / "international_results.csv"
DATA_SOURCE = "github_martj42_international_results"

_ALIASES = {
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Cape Verde Islands": "Cape Verde",
    "Congo DR": "DR Congo",
    "Czechia": "Czech Republic",
    "Ivory Coast": "Cote d'Ivoire",
    "Korea Republic": "South Korea",
    "United States": "USA",
}


def get_historical_team_stats(
    team_name: str,
    *,
    before_date: datetime | date | str | None = None,
    max_matches: int = 10,
) -> dict[str, Any] | None:
    """Build recent form/team stats from the martj42 international results CSV."""

    rows = _recent_team_rows(team_name, before_date=before_date, max_matches=max_matches)
    if not rows:
        return None

    goals_for = 0
    goals_against = 0
    wins = draws = losses = 0
    recent_results: list[str] = []
    team_key = _team_key(team_name)

    for row in rows:
        if _team_key(row["home_team"]) == team_key:
            gf = row["home_score"]
            ga = row["away_score"]
        else:
            gf = row["away_score"]
            ga = row["home_score"]

        goals_for += gf
        goals_against += ga
        if gf > ga:
            wins += 1
            recent_results.append("W")
        elif gf < ga:
            losses += 1
            recent_results.append("L")
        else:
            draws += 1
            recent_results.append("D")

    played = len(rows)
    return {
        "goals_per_game": round(goals_for / played, 2),
        "goals_conceded_per_game": round(goals_against / played, 2),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "played": played,
        "recent_results": recent_results,
        "last_match_date": rows[0]["date"].isoformat(),
        "updated_at": _source_updated_at(),
        "data_source": DATA_SOURCE,
    }


def get_historical_h2h(
    home_team: str,
    away_team: str,
    *,
    before_date: datetime | date | str | None = None,
    max_matches: int = 20,
) -> dict[str, Any] | None:
    """Build H2H factors from historical international match results."""

    before = _coerce_date(before_date)
    home_key = _team_key(home_team)
    away_key = _team_key(away_team)
    matches: list[dict[str, Any]] = []

    for row in reversed(_load_results()):
        if before and row["date"] >= before:
            continue
        row_home = _team_key(row["home_team"])
        row_away = _team_key(row["away_team"])
        if {row_home, row_away} != {home_key, away_key}:
            continue
        matches.append(row)
        if len(matches) >= max_matches:
            break

    if not matches:
        return None

    home_wins = away_wins = draws = 0
    home_goals = away_goals = 0
    for row in matches:
        if _team_key(row["home_team"]) == home_key:
            h_goals = row["home_score"]
            a_goals = row["away_score"]
        else:
            h_goals = row["away_score"]
            a_goals = row["home_score"]
        home_goals += h_goals
        away_goals += a_goals
        if h_goals > a_goals:
            home_wins += 1
        elif h_goals < a_goals:
            away_wins += 1
        else:
            draws += 1

    played = len(matches)
    return {
        "matches_played": played,
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "avg_goals_home": round(home_goals / played, 2),
        "avg_goals_away": round(away_goals / played, 2),
        "updated_at": _source_updated_at(),
        "data_source": DATA_SOURCE,
    }


def _recent_team_rows(
    team_name: str,
    *,
    before_date: datetime | date | str | None,
    max_matches: int,
) -> list[dict[str, Any]]:
    before = _coerce_date(before_date)
    team_key = _team_key(team_name)
    rows: list[dict[str, Any]] = []
    for row in reversed(_load_results()):
        if before and row["date"] >= before:
            continue
        if _team_key(row["home_team"]) == team_key or _team_key(row["away_team"]) == team_key:
            rows.append(row)
            if len(rows) >= max_matches:
                break
    return rows


@lru_cache(maxsize=1)
def _load_results() -> tuple[dict[str, Any], ...]:
    path = Path(os.getenv("WORLD_CUP_HISTORICAL_RESULTS_FILE", str(DEFAULT_RESULTS_PATH)))
    if not path.exists():
        return ()

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                match_date = date.fromisoformat(str(row.get("date") or ""))
                home_score = int(row.get("home_score") or 0)
                away_score = int(row.get("away_score") or 0)
            except (TypeError, ValueError):
                continue
            rows.append({
                "date": match_date,
                "home_team": str(row.get("home_team") or ""),
                "away_team": str(row.get("away_team") or ""),
                "home_score": home_score,
                "away_score": away_score,
                "tournament": str(row.get("tournament") or ""),
                "neutral": str(row.get("neutral") or "").upper() == "TRUE",
            })
    rows.sort(key=lambda item: item["date"])
    return tuple(rows)


def _team_key(team_name: str) -> str:
    canonical = _ALIASES.get(team_name, team_name)
    return "".join(ch for ch in canonical.lower() if ch.isalnum())


def _coerce_date(value: datetime | date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _source_updated_at() -> str | None:
    path = Path(os.getenv("WORLD_CUP_HISTORICAL_RESULTS_FILE", str(DEFAULT_RESULTS_PATH)))
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None
