"""Structured sports facts for the World Cup vertical.

The first version is intentionally file-backed. It lets operators import
manual/JSON facts now, while keeping the normalized shape stable for later
official match-data adapters.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.utils.file_store import locked_file, read_json, read_json_strict, write_json_atomic


WORLD_CUP_TOURNAMENT = "2026 FIFA World Cup"

_KNOWN_KINDS = {
    "injury",
    "availability",
    "suspension",
    "discipline",
    "qualification",
    "match_state",
    "match_result",
    "lineup",
    "player_award",
    "tournament_status",
}


def load_sports_facts(
    *,
    tournament: str | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Read normalized sports facts from the configured JSON file."""

    data = read_json(_fact_path(), _empty_store())
    facts = _facts_from_store(data)
    normalized = []
    for raw in facts:
        try:
            fact = normalize_sports_fact(raw, default_tournament=tournament)
        except ValueError:
            continue
        if tournament and fact.get("tournament") != tournament:
            continue
        if kind and fact.get("kind") != kind:
            continue
        normalized.append(fact)
    return sorted(normalized, key=_sort_key)


def import_sports_facts(
    payload: Any,
    *,
    replace: bool = False,
    default_tournament: str = WORLD_CUP_TOURNAMENT,
) -> dict[str, Any]:
    """Import facts from a list or ``{"facts": [...]}`` payload.

    Facts are upserted by ``fact_id``. When ``replace`` is true the existing file
    is replaced with the valid facts in the payload.
    """

    raw_facts = _extract_payload_facts(payload)
    normalized: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_facts):
        try:
            normalized.append(
                normalize_sports_fact(raw, default_tournament=default_tournament)
            )
        except ValueError as exc:
            errors.append({"index": index, "error": str(exc)})

    if errors and not normalized:
        return {
            "imported": 0,
            "error_count": len(errors),
            "errors": errors,
            "total": len(load_sports_facts(tournament=default_tournament)),
            "replace": replace,
        }

    path = _fact_path()
    with locked_file(path):
        current = _empty_store() if replace else _coerce_store(
            read_json_strict(path, _empty_store())
        )
        facts_by_id = {
            fact["fact_id"]: fact
            for fact in _facts_from_store(current)
            if isinstance(fact, dict) and fact.get("fact_id")
        }
        for fact in normalized:
            facts_by_id[fact["fact_id"]] = fact
        store = {
            "updated_at": _utc_now(),
            "facts": sorted(facts_by_id.values(), key=_sort_key),
        }
        write_json_atomic(path, store, indent=2)

    return {
        "imported": len(normalized),
        "error_count": len(errors),
        "errors": errors,
        "total": len(store["facts"]),
        "replace": replace,
    }


def sports_fact_status(*, tournament: str = WORLD_CUP_TOURNAMENT) -> dict[str, Any]:
    """Return a small operational status block for the facts file."""

    path = _fact_path()
    data = read_json(path, _empty_store())
    facts = load_sports_facts(tournament=tournament)
    by_kind: dict[str, int] = {}
    for fact in facts:
        by_kind[fact["kind"]] = by_kind.get(fact["kind"], 0) + 1
    mtime = None
    if os.path.exists(path):
        mtime = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat()
    return {
        "tournament": tournament,
        "configured_path": path,
        "exists": os.path.exists(path),
        "updated_at": _coerce_store(data).get("updated_at", ""),
        "file_mtime": mtime,
        "count": len(facts),
        "by_kind": by_kind,
    }


def normalize_sports_fact(
    raw: Any,
    *,
    default_tournament: str | None = WORLD_CUP_TOURNAMENT,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("fact must be an object")

    kind = _clean(raw.get("kind")).lower()
    if not kind:
        raise ValueError("missing kind")
    if kind not in _KNOWN_KINDS:
        raise ValueError(f"unsupported kind '{kind}'")

    tournament = _clean(raw.get("tournament") or default_tournament)
    if not tournament:
        raise ValueError("missing tournament")

    fact = {
        "fact_id": _clean(raw.get("fact_id")),
        "kind": kind,
        "tournament": tournament,
        "team": _clean(raw.get("team")),
        "player": _clean(raw.get("player")),
        "match_id": _clean(raw.get("match_id")),
        "stage": _clean(raw.get("stage")),
        "award": _clean(raw.get("award")).lower(),
        "status": _clean(raw.get("status")).lower(),
        "severity": _clean(raw.get("severity")).lower() or "unknown",
        "source": _clean(raw.get("source")) or "manual",
        "source_url": _clean(raw.get("source_url") or raw.get("url")),
        "confidence": _clamp01(raw.get("confidence", 1.0)),
        "observed_at": _clean(raw.get("observed_at")) or _utc_now(),
        "applies_to": _clean_list(raw.get("applies_to")),
        "notes": _clean(raw.get("notes") or raw.get("summary")),
    }

    for field in (
        "home_team",
        "away_team",
        "winner",
        "kickoff_at",
        "venue",
        "referee",
        "minute",
        "position",
        "formation",
        "jersey_number",
        "qualified",
        "eliminated",
    ):
        if raw.get(field) is not None:
            fact[field] = _clean(raw.get(field))

    for field in ("red_cards", "yellow_cards", "goals_for", "goals_against", "goals", "rank"):
        value = _non_negative_number(raw.get(field))
        if value is not None:
            fact[field] = value

    if isinstance(raw.get("score"), dict):
        fact["score"] = raw["score"]
    if raw.get("extra_time") is not None:
        fact["extra_time"] = bool(raw.get("extra_time"))
    if raw.get("penalty_shootout") is not None:
        fact["penalty_shootout"] = bool(raw.get("penalty_shootout"))
    if raw.get("already_qualified") is not None:
        fact["already_qualified"] = bool(raw.get("already_qualified"))
    if raw.get("already_eliminated") is not None:
        fact["already_eliminated"] = bool(raw.get("already_eliminated"))
    if raw.get("tournament_complete") is not None:
        fact["tournament_complete"] = bool(raw.get("tournament_complete"))

    if not fact["fact_id"]:
        fact["fact_id"] = _make_fact_id(fact)
    return {key: value for key, value in fact.items() if value not in ("", [], None)}


def _fact_path() -> str:
    return settings.SPORTS_FACT_FILE


def _empty_store() -> dict[str, Any]:
    return {"updated_at": "", "facts": []}


def _coerce_store(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        return {"updated_at": "", "facts": data}
    if isinstance(data, dict):
        facts = data.get("facts")
        return {
            "updated_at": _clean(data.get("updated_at")),
            "facts": facts if isinstance(facts, list) else [],
        }
    return _empty_store()


def _facts_from_store(data: Any) -> list[Any]:
    return _coerce_store(data)["facts"]


def _extract_payload_facts(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("facts"), list):
        return payload["facts"]
    raise ValueError("payload must be a list or an object with a facts list")


def _make_fact_id(fact: dict[str, Any]) -> str:
    seed = "|".join(
        str(fact.get(field, ""))
        for field in (
            "tournament",
            "kind",
            "team",
            "player",
            "match_id",
            "status",
            "source",
            "observed_at",
        )
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    kind = fact.get("kind", "fact")
    tournament = re.sub(r"[^a-z0-9]+", "-", fact.get("tournament", "").lower()).strip("-")
    return f"sports:{tournament}:{kind}:{digest}"


def _sort_key(fact: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(fact.get("tournament", "")),
        str(fact.get("kind", "")),
        str(fact.get("fact_id", "")),
    )


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean(item) for item in value if _clean(item)]


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, number)), 3)


def _non_negative_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, number)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
