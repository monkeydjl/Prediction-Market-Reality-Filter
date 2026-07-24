# backend/app/sports/basketball/nba_injury.py
"""Static NBA injury impact (P1-B1).

Soft signal only: code-local Out list + role-tier weights.
Missing team / no Out rows → None (do not claim known-healthy 0.0).
Engine formula/weights live in BasketballEngine and are unchanged.
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
# Keys = fixture full names (balldontlie full_name). Only status "out" counts.
_STATIC_INJURIES: dict[str, list[dict[str, str]]] = {
    "Boston Celtics": [
        {"player": "Example Star Out", "role": "star", "status": "out"},
    ],
    "Los Angeles Lakers": [
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


def summarize_injury_impact(rows: list[dict[str, Any]] | None) -> float | None:
    """Sum role weights for Out rows; clamp to [0, 1]. None if no Out contribution."""
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
        weight = ROLE_WEIGHTS.get(role, ROLE_WEIGHTS["bench"])
        total += float(weight)
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
