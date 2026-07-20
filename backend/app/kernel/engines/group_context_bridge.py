"""Map world_cup group_context dict → FeatureSet.custom situational keys.

Keeps SituationalEngine independent of MatchFixture / SQLAlchemy while
letting WorldCupAdapter (and tests) feed the same signals as the legacy
pipeline's build_group_context().
"""
from __future__ import annotations

from typing import Any


def group_context_to_custom(group_context: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten build_group_context() output into situational custom fields."""
    if not group_context:
        return {}

    out: dict[str, Any] = {"group_context_present": True}
    group = group_context.get("group")
    if group is not None:
        out["group"] = group

    home = group_context.get("home") or group_context.get("home_team_standing") or {}
    away = group_context.get("away") or group_context.get("away_team_standing") or {}

    if home:
        out["must_win_home"] = bool(home.get("must_win"))
        if home.get("pressure"):
            out["home_pressure"] = str(home["pressure"])
        if home.get("status"):
            out["home_group_status"] = str(home["status"])
        if home.get("rank") is not None:
            out["home_group_rank"] = home["rank"]
        if home.get("points") is not None:
            out["home_group_points"] = home["points"]
    if away:
        out["must_win_away"] = bool(away.get("must_win"))
        if away.get("pressure"):
            out["away_pressure"] = str(away["pressure"])
        if away.get("status"):
            out["away_group_status"] = str(away["status"])
        if away.get("rank") is not None:
            out["away_group_rank"] = away["rank"]
        if away.get("points") is not None:
            out["away_group_points"] = away["points"]

    if group_context.get("has_must_win_team"):
        out["stakes"] = "high"

    return out


def merge_custom(base: dict[str, Any] | None, extra: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge; existing keys in base win (caller intent preserved)."""
    out = dict(extra)
    out.update(base or {})
    return out
