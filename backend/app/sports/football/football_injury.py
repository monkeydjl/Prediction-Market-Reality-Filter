# backend/app/sports/football/football_injury.py
"""Static football injury impact (P1-F3).

Soft signal only: code-local Out list + role-tier weights.
Missing team / no Out rows → None (do not claim known-healthy 0.0).
Engine formula/weights live in FootballMultiFactorEngine and are unchanged.
"""
from __future__ import annotations

from typing import Any

ROLE_WEIGHTS: dict[str, float] = {
    "star": 0.35,
    "starter": 0.18,
    "rotation": 0.08,
    "bench": 0.03,
}

# Soft static snapshot for tests / optional spot checks. Operators update by PR.
# Keys = fixture full names (adapter / kernel). Only status "out" counts.
_STATIC_INJURIES: dict[str, list[dict[str, str]]] = {
    "Real Madrid CF": [
        {"player": "Example Star Out", "role": "star", "status": "out"},
    ],
    "FC Bayern München": [
        {"player": "Example Starter Out", "role": "starter", "status": "out"},
        {"player": "Example Rotation Out", "role": "rotation", "status": "out"},
    ],
}


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _contextual_weight(row: dict[str, Any], role_weight: float) -> float:
    """Use valid minutes/value context without lowering the role baseline.

    Player shares are expected as fractions of the team's season total. The
    contextual score intentionally caps at the existing star contribution, so
    enriched availability cannot introduce a new larger per-player impact.
    """
    minutes = row.get("minutes_share")
    value = row.get("market_value_share")
    if (
        isinstance(minutes, bool)
        or isinstance(value, bool)
        or not isinstance(minutes, (str, int, float))
        or not isinstance(value, (str, int, float))
    ):
        return role_weight
    try:
        minutes_share = float(minutes)
        value_share = float(value)
    except (TypeError, ValueError):
        return role_weight
    if not 0.0 <= minutes_share <= 1.0 or not 0.0 <= value_share <= 1.0:
        return role_weight
    return max(
        role_weight,
        min(ROLE_WEIGHTS["star"], 2.0 * minutes_share + value_share),
    )


def summarize_injury_impact(rows: list[dict[str, Any]] | None) -> float | None:
    """Sum Out impacts; contextual shares supplement valid role rows.

    Rows without both valid shares keep the legacy role-only contribution.
    """
    if not rows:
        return None
    total = 0.0
    saw_out = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        if status != "out":
            continue
        role = str(row.get("role") or "").strip().lower()
        role_weight = ROLE_WEIGHTS.get(role, ROLE_WEIGHTS["bench"])
        total += _contextual_weight(row, float(role_weight))
        saw_out = True
    if not saw_out:
        return None
    return _clamp01(total)


def injury_impact_for_team(team_name: str) -> float | None:
    """Exact full-name lookup into static table; None if missing/empty/no Out."""
    name = (team_name or "").strip()
    if not name:
        return None
    rows = _STATIC_INJURIES.get(name)
    return summarize_injury_impact(rows)
