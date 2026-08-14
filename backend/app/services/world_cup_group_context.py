"""Group-stage context and motivation factors for World Cup matches."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.world_cup_prediction import MatchFixture


def _is_group_stage(stage: str | None) -> bool:
    normalized = (stage or "").lower().replace(" ", "_")
    return normalized in {"group_stage", "group"}


def _ensure_team(table: dict[str, dict[str, int]], team: str) -> dict[str, int]:
    if team not in table:
        table[team] = {
            "played": 0,
            "points": 0,
            "goals_for": 0,
            "goals_against": 0,
            "goal_difference": 0,
        }
    return table[team]


def _apply_result(table: dict[str, dict[str, int]], match: MatchFixture) -> None:
    if match.home_score is None or match.away_score is None:
        return

    home = _ensure_team(table, match.home_team)
    away = _ensure_team(table, match.away_team)
    home_score = int(match.home_score)
    away_score = int(match.away_score)

    home["played"] += 1
    away["played"] += 1
    home["goals_for"] += home_score
    home["goals_against"] += away_score
    away["goals_for"] += away_score
    away["goals_against"] += home_score

    if home_score > away_score:
        home["points"] += 3
    elif away_score > home_score:
        away["points"] += 3
    else:
        home["points"] += 1
        away["points"] += 1

    home["goal_difference"] = home["goals_for"] - home["goals_against"]
    away["goal_difference"] = away["goals_for"] - away["goals_against"]


def _ranked_table(group_matches: list[MatchFixture]) -> list[dict[str, Any]]:
    table: dict[str, dict[str, int]] = {}
    for match in group_matches:
        _ensure_team(table, match.home_team)
        _ensure_team(table, match.away_team)
        if match.status == "finished":
            _apply_result(table, match)

    # Annotated: `{"team": str, **dict[str, int]}` infers dict[str, object],
    # and the sort key below cannot negate an object.
    rows: list[dict[str, Any]] = [
        {"team": team, **stats}
        for team, stats in table.items()
    ]
    rows.sort(
        key=lambda row: (
            -row["points"],
            -row["goal_difference"],
            -row["goals_for"],
            row["team"],
        )
    )
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def _team_context(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {
            "rank": None,
            "played": 0,
            "points": 0,
            "goal_difference": 0,
            "status": "unknown",
            "pressure": "normal",
            "must_win": False,
        }

    played = int(row["played"])
    points = int(row["points"])
    status = "live"
    pressure = "normal"
    must_win = False

    if played >= 2 and points >= 6:
        status = "qualified"
        pressure = "rotation_risk"
    elif played >= 2 and points == 0:
        status = "eliminated"
        pressure = "low_motivation"
    elif played >= 2 and points <= 3:
        pressure = "must_win"
        must_win = True
    elif played >= 2:
        pressure = "protect_position"

    return {
        "rank": row["rank"],
        "played": played,
        "points": points,
        "goals_for": row["goals_for"],
        "goals_against": row["goals_against"],
        "goal_difference": row["goal_difference"],
        "status": status,
        "pressure": pressure,
        "must_win": must_win,
    }


def build_group_context(match: MatchFixture, session: Session) -> dict[str, Any] | None:
    """Build group-stage standings and motivation context for a match."""
    if not _is_group_stage(match.stage) or not match.group:
        return None

    group_matches = session.query(MatchFixture).filter(
        MatchFixture.group == match.group
    ).all()
    if not group_matches:
        return None

    table = _ranked_table(group_matches)
    by_team = {row["team"]: row for row in table}
    home = _team_context(by_team.get(match.home_team))
    away = _team_context(by_team.get(match.away_team))

    return {
        "group": match.group,
        "table": table,
        "home": home,
        "away": away,
        "home_team_standing": home,
        "away_team_standing": away,
        "has_must_win_team": bool(home["must_win"] or away["must_win"]),
    }
