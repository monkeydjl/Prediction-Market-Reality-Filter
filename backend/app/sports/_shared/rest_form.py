"""As-of rest days and form (L10 win rate) for backtest + adapters."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _team_in_match(team: str, m: Mapping[str, Any]) -> bool:
    return m.get("home_team") == team or m.get("away_team") == team


def _team_won(team: str, m: Mapping[str, Any]) -> bool | None:
    hs, aws = m.get("home_score"), m.get("away_score")
    if hs is None or aws is None:
        return None
    if m.get("home_team") == team:
        return int(hs) > int(aws)
    if m.get("away_team") == team:
        return int(aws) > int(hs)
    return None


def rest_days_as_of(
    team: str,
    kickoff: datetime | None,
    history: Sequence[Mapping[str, Any]],
    *,
    exclude_match_id: str | None = None,
) -> float | None:
    as_of = _as_utc(kickoff)
    if as_of is None or not team:
        return None
    prev: datetime | None = None
    for m in history:
        mid = m.get("match_id")
        if exclude_match_id is not None and mid == exclude_match_id:
            continue
        if not _team_in_match(team, m):
            continue
        k = _as_utc(m.get("kickoff_utc"))
        if k is None or k >= as_of:
            continue
        if prev is None or k > prev:
            prev = k
    if prev is None:
        return None
    return float(max(0, (as_of - prev).days))


def form_as_of(
    team: str,
    kickoff: datetime | None,
    history: Sequence[Mapping[str, Any]],
    *,
    max_matches: int = 10,
    exclude_match_id: str | None = None,
    default: float = 0.5,
) -> float:
    as_of = _as_utc(kickoff)
    if not team:
        return default
    candidates: list[tuple[datetime, str, Mapping[str, Any]]] = []
    for m in history:
        mid = str(m.get("match_id") or "")
        if exclude_match_id is not None and mid == exclude_match_id:
            continue
        if not _team_in_match(team, m):
            continue
        if m.get("home_score") is None or m.get("away_score") is None:
            continue
        k = _as_utc(m.get("kickoff_utc"))
        if k is None:
            continue
        if as_of is not None and k >= as_of:
            continue
        candidates.append((k, mid, m))
    if not candidates:
        return default
    candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
    window = candidates[: max(1, max_matches)]
    wins = 0
    for _, _, m in window:
        won = _team_won(team, m)
        if won:
            wins += 1
    return wins / len(window)


def enrich_matches_rest_form(
    matches: list[dict[str, Any]],
    *,
    max_form_matches: int = 10,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in matches:
        copy = dict(m)
        kickoff = m.get("kickoff_utc")
        mid = m.get("match_id")
        home = m.get("home_team") or ""
        away = m.get("away_team") or ""
        copy["rest_days_home"] = rest_days_as_of(
            home, kickoff, matches, exclude_match_id=mid,
        )
        copy["rest_days_away"] = rest_days_as_of(
            away, kickoff, matches, exclude_match_id=mid,
        )
        copy["form_home"] = form_as_of(
            home, kickoff, matches,
            max_matches=max_form_matches, exclude_match_id=mid,
        )
        copy["form_away"] = form_as_of(
            away, kickoff, matches,
            max_matches=max_form_matches, exclude_match_id=mid,
        )
        out.append(copy)
    return out
