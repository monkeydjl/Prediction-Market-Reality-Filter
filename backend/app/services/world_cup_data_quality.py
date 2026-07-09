"""Data-quality scoring helpers for World Cup predictions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SCORE_VERSION = 2

_NON_REAL_SOURCE_TOKENS = (
    "mock",
    "fallback",
    "default",
    "unknown",
    "none",
    "estimated",
    "unavailable",
)


def source_looks_real(source: Any) -> bool:
    """Return true for curated/API/cache sources, false for mock/default sources."""

    if not source:
        return False
    parts = str(source).lower().replace("\\", "/").split("/")
    return any(
        part and not any(token in part for token in _NON_REAL_SOURCE_TOKENS)
        for part in parts
    )


def all_sources_look_real(source: Any) -> bool:
    """Return true only when every source segment is a real data source."""

    if not source:
        return False
    parts = [part for part in str(source).lower().replace("\\", "/").split("/") if part]
    return bool(parts) and all(
        not any(token in part for token in _NON_REAL_SOURCE_TOKENS)
        for part in parts
    )


def normalize_prediction_data_quality(
    quality: Any,
    notes: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, list[str]]:
    """Normalize persisted prediction quality before exposing it to clients.

    Old rows may contain ``mock``/``fallback`` labels from earlier fixture sets.
    New trusted outputs should only expose ``real`` or ``partial``; non-real or
    missing historical labels are downgraded to ``partial`` with an audit note.
    """

    normalized_notes = list(notes or [])
    value = str(quality or "").strip().lower()
    if value == "real":
        return "real", normalized_notes
    if value == "partial":
        return "partial", normalized_notes

    note = "data_quality_missing" if not value else "historical_non_real_quality_normalized"
    if note not in normalized_notes:
        normalized_notes.append(note)
    return "partial", normalized_notes


def parse_source_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_days_from_source(payload: dict[str, Any] | None, *keys: str) -> float | None:
    if not payload:
        return None
    for key in keys:
        parsed = parse_source_time(payload.get(key))
        if parsed:
            return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400
    return None


def age_minutes_from_source(payload: dict[str, Any] | None, *keys: str) -> float | None:
    if not payload:
        return None
    if payload.get("cache_age_seconds") is not None:
        try:
            return float(payload["cache_age_seconds"]) / 60
        except (TypeError, ValueError):
            pass
    for key in keys:
        parsed = parse_source_time(payload.get(key))
        if parsed:
            return (datetime.now(timezone.utc) - parsed).total_seconds() / 60
    return None


def enrich_data_quality_metrics(
    metrics: dict[str, Any] | None,
    factors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fill missing v2 quality flags for old persisted predictions."""

    enriched = dict(metrics or {})
    factor_payload = factors if isinstance(factors, dict) else {}

    if "has_stats" not in enriched:
        enriched["has_stats"] = all_sources_look_real(enriched.get("stats_source"))
    if "has_schedule_context" not in enriched:
        home_factor = factor_payload.get("home_team") or {}
        away_factor = factor_payload.get("away_team") or {}
        enriched["has_schedule_context"] = bool(
            factor_payload.get("schedule_context")
            or home_factor.get("schedule_density")
            or away_factor.get("schedule_density")
        )
    if "has_group_context" not in enriched:
        context = factor_payload.get("context") or {}
        enriched["has_group_context"] = bool(
            factor_payload.get("group_context")
            or context.get("home_team_standing")
            or context.get("away_team_standing")
        )

    enriched["score_version"] = SCORE_VERSION
    enriched["quality_score"] = calculate_data_quality_score(enriched)
    return enriched


def calculate_data_quality_score(metrics: dict[str, Any] | None) -> float:
    """Calculate a 0-100 prediction data-quality score.

    The score measures usable predictive inputs. Missing odds or H2H should be
    visible, but real Elo plus match context should not collapse to 20/100.
    """

    data = metrics or {}

    coverage_score = 0.0
    if data.get("has_elo"):
        coverage_score += 15
    if data.get("has_odds"):
        coverage_score += 10 if data.get("odds_stale") else 15
    if data.get("has_h2h"):
        coverage_score += 10
    if data.get("has_stats"):
        coverage_score += 10
    if data.get("has_weather"):
        coverage_score += 5

    context_score = 0.0
    if data.get("has_schedule_context"):
        context_score += 5
    if data.get("has_group_context"):
        context_score += 10
    if data.get("has_openfootball_context"):
        context_score += 5

    freshness_score = 0.0
    elo_age_days = _safe_float_or_none(data.get("elo_age_days"))
    if elo_age_days is not None:
        freshness_score += _freshness_points(elo_age_days, fresh=7, stale=30, max_points=10)
    elif data.get("has_elo") and all_sources_look_real(data.get("elo_source")):
        freshness_score += 8

    odds_age_minutes = _safe_float_or_none(data.get("odds_age_minutes"))
    if odds_age_minutes is not None:
        freshness_score += _freshness_points(odds_age_minutes, fresh=30, stale=120, max_points=5)

    stats_age_hours = _safe_float_or_none(data.get("stats_age_hours"))
    if stats_age_hours is not None:
        freshness_score += _freshness_points(stats_age_hours, fresh=24, stale=72, max_points=5)

    quality = str(data.get("quality") or "").lower()
    if quality == "real":
        quality_level_score = 10
    elif quality == "partial":
        quality_level_score = 6
    else:
        quality_level_score = 2

    score = coverage_score + context_score + freshness_score + quality_level_score
    if data.get("odds_stale"):
        score -= 10
    return round(max(0.0, min(100.0, score)), 1)


def _freshness_points(value: float, *, fresh: float, stale: float, max_points: float) -> float:
    if value <= fresh:
        return max_points
    if value >= stale:
        return 0.0
    return max_points * (1 - ((value - fresh) / (stale - fresh)))


def _safe_float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
