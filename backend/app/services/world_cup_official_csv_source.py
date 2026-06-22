"""Strict official CSV profile for World Cup source imports."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO
from typing import Any

from app.services.sports_fact_service import WORLD_CUP_TOURNAMENT
from app.services.world_cup_data_source_service import (
    import_world_cup_data,
    world_cup_data_to_facts,
)

_PROFILE_HEADERS: dict[str, tuple[str, ...]] = {
    "matches": (
        "match_id",
        "stage",
        "kickoff_at",
        "venue",
        "referee",
        "home_team",
        "away_team",
        "status",
        "home_score",
        "away_score",
        "winner",
        "extra_time",
        "penalty_shootout",
        "home_red_cards",
        "away_red_cards",
        "home_yellow_cards",
        "away_yellow_cards",
    ),
    "discipline": (
        "event_id",
        "match_id",
        "stage",
        "team",
        "player",
        "minute",
        "status",
        "red_cards",
        "yellow_cards",
        "reason",
    ),
    "qualifications": (
        "team",
        "stage",
        "status",
        "already_qualified",
        "already_eliminated",
    ),
    "player_awards": (
        "award",
        "player",
        "team",
        "goals",
        "rank",
        "status",
    ),
    "player_statuses": (
        "kind",
        "team",
        "player",
        "status",
        "severity",
        "match_id",
        "stage",
        "position",
        "formation",
        "jersey_number",
        "reason",
        "applies_to",
    ),
    "team_stats": (
        "team",
        "match_id",
        "stage",
        "stat_name",
        "stat_value",
        "stat_unit",
    ),
    "player_stats": (
        "team",
        "player",
        "match_id",
        "stage",
        "position",
        "jersey_number",
        "stat_name",
        "stat_value",
        "stat_unit",
    ),
}


def world_cup_official_csv_source_to_data(payload: Any) -> dict[str, Any]:
    """Convert a strict JSON-wrapped official CSV profile into normalized data."""

    if not isinstance(payload, dict):
        raise ValueError("official CSV payload must be an object")
    csv_payload = payload.get("csv") or payload.get("official_csv")
    if not isinstance(csv_payload, dict):
        raise ValueError("official CSV payload must include a csv object")

    unknown = [
        key for key, value in csv_payload.items()
        if key not in _PROFILE_HEADERS and value not in (None, "")
    ]
    if unknown:
        raise ValueError(f"official CSV profile does not support csv.{unknown[0]}")

    data: dict[str, Any] = {
        "tournament": _clean(payload.get("tournament")) or WORLD_CUP_TOURNAMENT,
        "source": _clean(payload.get("source")) or "official_csv",
        "source_url": _clean(payload.get("source_url") or payload.get("url")),
        "observed_at": _clean(payload.get("observed_at")) or _utc_now(),
    }
    counts: dict[str, int] = {}
    for section, headers in _PROFILE_HEADERS.items():
        csv_text = csv_payload.get(section)
        if csv_text in (None, ""):
            continue
        rows = _strict_csv_rows(csv_text, section, headers)
        if section == "player_statuses":
            rows = [_normalize_player_status_row(row) for row in rows]
        data[section] = rows
        counts[section] = len(rows)
    if not counts:
        raise ValueError("official CSV payload did not contain profile sections")
    data["profile"] = "official_csv_v1"
    data["profile_counts"] = counts
    return data


def preview_world_cup_official_csv_source(payload: Any) -> dict[str, Any]:
    """Preview facts produced from the strict official CSV profile."""

    data = world_cup_official_csv_source_to_data(payload)
    facts = world_cup_data_to_facts(data)
    return {
        "profile": data["profile"],
        "profile_counts": data["profile_counts"],
        "normalized_data": data,
        "converted_fact_count": len(facts),
        "facts": facts,
    }


def import_world_cup_official_csv_source(
    payload: Any,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import facts produced from the strict official CSV profile."""

    data = world_cup_official_csv_source_to_data(payload)
    result = import_world_cup_data(data, replace=replace)
    result["profile"] = data["profile"]
    result["profile_counts"] = data["profile_counts"]
    result["normalized_data"] = data
    return result


def _strict_csv_rows(
    csv_text: Any,
    section: str,
    expected_headers: tuple[str, ...],
) -> list[dict[str, str]]:
    if not isinstance(csv_text, str):
        raise ValueError(f"csv.{section} must be a string")
    reader = csv.DictReader(StringIO(csv_text.strip()))
    headers = _normalized_headers(reader.fieldnames)
    if headers != list(expected_headers):
        expected = ",".join(expected_headers)
        found = ",".join(headers)
        raise ValueError(
            f"csv.{section} headers must exactly match {expected}; found {found}"
        )
    rows: list[dict[str, str]] = []
    for raw in reader:
        if None in raw:
            raise ValueError(f"csv.{section} contains extra unnamed columns")
        row = {
            str(key).strip(): _clean(value)
            for key, value in raw.items()
            if key is not None
        }
        if not any(row.values()):
            continue
        rows.append(row)
    if not rows:
        raise ValueError(f"csv.{section} must include at least one data row")
    return rows


def _normalized_headers(fieldnames: list[str] | None) -> list[str]:
    if not fieldnames:
        return []
    headers: list[str] = []
    for index, field in enumerate(fieldnames):
        value = str(field or "").strip()
        if index == 0:
            value = value.lstrip("\ufeff")
        headers.append(value)
    return headers


def _normalize_player_status_row(row: dict[str, str]) -> dict[str, Any]:
    normalized: dict[str, Any] = dict(row)
    applies_to = _split_list(row.get("applies_to"))
    if applies_to:
        normalized["applies_to"] = applies_to
    else:
        normalized.pop("applies_to", None)
    return normalized


def _split_list(value: Any) -> list[str]:
    text = _clean(value)
    if not text:
        return []
    return [_clean(item) for item in text.replace("|", ";").split(";") if _clean(item)]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())
