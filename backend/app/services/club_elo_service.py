# backend/app/services/club_elo_service.py
"""ClubElo.com CSV fetcher + cache.

Fetches club Elo ratings from ClubElo.com's free CSV API.
No API key required. Uses kernel_club_elo_cache table for caching.
Does NOT depend on football_data_source.py.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone, timedelta
from io import StringIO
from typing import Any

import httpx
from app.core.config import settings

# Module-level import so that _check_cache / _save_cache can be unit-tested
# via patch("app.services.club_elo_service.get_kernel_session").  The local
# import inside the function body would bypass such patches.
try:
    from app.kernel.kernel_db import get_kernel_session, KernelClubEloCache
except ImportError:  # pragma: no cover - kernel_db optional in some envs
    get_kernel_session = None  # type: ignore[assignment]
    # Also [misc]: rebinding a *class* name to None is "Cannot assign to a
    # type", a separate error from the assignment above. Both call sites are
    # behind `if get_kernel_session is None: return`, so the None never reaches
    # session.get(KernelClubEloCache, ...).
    KernelClubEloCache = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_CLUB_ELO_API = "http://api.clubelo.com"

# Common suffixes/prefixes to strip when normalizing team names.
# NOTE: spaces are removed before matching, so tokens carry no leading space
# (the brief's original " fc" form could never match after .replace(" ", "")).
# IMPORTANT: tokens MUST be ordered longest-first. The normalization loop
# `break`s on the first match, so a shorter token listed before a longer one
# would win incorrectly — e.g. "fc" before "afc" would turn "sunderlandafc"
# into "sunderlanda" instead of "sunderland". Keep 3-char tokens ahead of
# 2-char tokens.
_SUFFIXES = ("afc", "fc.", "cf.", "ac.", "fc", "cf", "ac", "sc")
_PREFIXES = ("afc", "fc.", "cf.", "ac.", "fc", "cf", "ac", "sc")


def _normalize_team_name(name: str) -> str:
    """Normalize team name for matching: lowercase, remove spaces, strip affixes.

    Strips common club suffixes (FC, CF, AC, AFC, SC) and prefixes (FC, CF, ...)
    so that "Arsenal FC", "FC Bayern", and "Arsenal" normalize compatibly.
    """
    if not name:
        return ""
    normalized = name.strip().lower().replace(" ", "")
    for suffix in _SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    for prefix in _PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    return normalized


def fetch_club_elo_snapshot(date: str | None = None) -> list[dict[str, str]]:
    """Fetch full ranking CSV for a given date (default: today).

    Returns list of dicts with keys: Rank, Club, Country, Level, Elo, From, To.
    Returns empty list on network failure.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    url = f"{_CLUB_ELO_API}/{date}"
    try:
        response = httpx.get(url, timeout=15.0)
        if response.status_code != 200:
            logger.warning("ClubElo snapshot fetch failed: %s", response.status_code)
            return []
        reader = csv.DictReader(StringIO(response.text))
        return list(reader)
    except httpx.RequestError as exc:
        logger.warning("ClubElo snapshot network error: %s", exc)
        return []


def get_club_elo_by_country(country: str, level: int = 1) -> dict[str, float]:
    """Fetch snapshot and filter by country + level.

    Returns {team_name: elo_rating} for all clubs in the specified
    country's specified league level.
    """
    snapshot = fetch_club_elo_snapshot()
    result = {}
    for row in snapshot:
        if row.get("Country") == country:
            try:
                row_level = int(row.get("Level", 0))
                if row_level == level:
                    result[row["Club"]] = float(row["Elo"])
            except (ValueError, TypeError):
                continue
    return result


def get_club_elo(team_name: str) -> dict[str, Any] | None:
    """Get current club Elo rating.

    1. Check KernelClubEloCache table (TTL: CLUB_ELO_CACHE_TTL_DAYS).
    2. On cache miss/expire, fetch from ClubElo.com.
    3. Parse CSV, find matching club (case-insensitive, space-normalized).
    4. Cache result in KernelClubEloCache.
    5. Return {"elo_rating": float, "source": "clubelo"} or None on failure.
    """
    cached = _check_cache(team_name)
    if cached is not None:
        return cached

    # Fetch today's snapshot
    snapshot = fetch_club_elo_snapshot()
    if not snapshot:
        return None

    normalized_target = _normalize_team_name(team_name)
    for row in snapshot:
        normalized_club = _normalize_team_name(row.get("Club", ""))
        if normalized_club == normalized_target:
            try:
                elo = float(row["Elo"])
                result = {"elo_rating": elo, "source": "clubelo"}
                _save_cache(
                    team_name, elo,
                    country=row.get("Country", ""),
                    level=int(row.get("Level", 0)),
                )
                return result
            except (ValueError, TypeError):
                continue

    logger.debug("ClubElo: no match for '%s'", team_name)
    return None


def _check_cache(team_name: str) -> dict[str, Any] | None:
    """Check KernelClubEloCache for a valid cached entry.

    Returns {"elo_rating": float, "source": "clubelo"} if cache is fresh,
    or None if cache is missing/expired.
    """
    if get_kernel_session is None:
        return None

    normalized = _normalize_team_name(team_name)
    session = get_kernel_session()
    try:
        entry = session.get(KernelClubEloCache, normalized)
        if entry is None:
            return None
        ttl_days = getattr(settings, "CLUB_ELO_CACHE_TTL_DAYS", 7)
        max_age = timedelta(days=ttl_days)
        fetched = entry.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - fetched > max_age:
            return None
        return {"elo_rating": entry.elo_rating, "source": "clubelo"}
    except Exception as exc:  # noqa: BLE001
        logger.debug("ClubElo cache check failed: %s", exc)
        return None
    finally:
        session.close()


def _save_cache(
    team_name: str, elo: float, country: str = "", level: int = 0,
) -> None:
    """Save club Elo to KernelClubEloCache."""
    if get_kernel_session is None:
        return

    normalized = _normalize_team_name(team_name)
    session = get_kernel_session()
    try:
        existing = session.get(KernelClubEloCache, normalized)
        now = datetime.now(timezone.utc)
        if existing:
            existing.elo_rating = elo
            existing.fetched_at = now
            existing.country = country
            existing.level = level
        else:
            entry = KernelClubEloCache(
                team_name=normalized,
                elo_rating=elo,
                source="clubelo",
                fetched_at=now,
                country=country,
                level=level,
            )
            session.add(entry)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.debug("ClubElo cache save failed: %s", exc)
    finally:
        session.close()
