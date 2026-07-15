# backend/app/sports/football/adapters/_shared.py
"""Shared utility functions for football adapters.

Pure functions — no class, no module-level mutable state.
Each adapter calls these freely (composition over inheritance).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.kernel.domain import (
    CompetitionIdentity, SeasonIdentity, TeamIdentity,
    MatchIdentity, MatchOutcome,
)

# Imported at module level (rather than lazily inside fetch_team_elo) so that
# unit tests can patch it via
#   @patch("app.sports.football.adapters._shared.get_club_elo")
# and so fetch_team_elo calls the patched name. get_elo_rating / get_cached_odds
# stay as lazy in-function imports (the former is never patched in tests; the
# latter is patched at its source module, which a lazy import still honors).
from app.services.club_elo_service import get_club_elo

logger = logging.getLogger(__name__)


async def fetch_team_elo(
    team_name: str,
    scope: str = "national",
    alias: str | None = None,
) -> dict[str, Any] | None:
    """Fetch Elo rating for a team.

    scope="national": delegates to elo_ratings_service.get_elo_rating() (async)
    scope="club": delegates to club_elo_service.get_club_elo() (sync)

    alias: if provided, used as the lookup name instead of team_name.

    Returns {"elo_rating": float, "source": str} or None on failure.
    """
    lookup_name = alias or team_name
    if scope == "club":
        return get_club_elo(lookup_name)  # sync function, OK in async context
    else:
        from app.services.elo_ratings_service import get_elo_rating
        return await get_elo_rating(lookup_name)  # async function, needs await


async def fetch_match_odds(home: str, away: str, competition: str = "wc") -> dict[str, Any] | None:
    """Fetch cached odds for a match.

    Delegates to odds_cache_service.get_cached_odds() (async). Forwards the
    competition so the cache key is namespaced per league and the correct
    The Odds API sport_key is used on a cache miss.
    Returns the odds dict or None on failure.
    """
    from app.services.odds_cache_service import get_cached_odds
    return await get_cached_odds(home, away, competition=competition)


def fetch_elo_and_odds(
    match: MatchIdentity,
    elo_scope: str = "national",
    team_aliases: dict[str, str] | None = None,
) -> dict:
    """Fetch Elo ratings + odds for a match in a single asyncio.run() call.

    Consolidates three async calls (elo_home, elo_away, odds) into one
    event loop via asyncio.gather(return_exceptions=True).

    team_aliases: {team_name: clubelo_name} for name mapping.

    Returns dict with keys: team, market, player, environment, general.
    """
    aliases = team_aliases or {}
    home_alias = aliases.get(match.home.name)
    away_alias = aliases.get(match.away.name)

    raw: dict = {
        "team": {}, "market": {},
        "player": {}, "environment": {}, "general": {},
    }

    # Define an inner coroutine so asyncio.gather() is constructed INSIDE the
    # running event loop that asyncio.run() provides. The brief's
    # `asyncio.run(asyncio.gather(...))` form eagerly calls ensure_future() /
    # get_event_loop() at call time (before the loop exists), which raises
    # "There is no current event loop" under pytest-asyncio and is fragile in
    # general. Wrapping makes gather bind to the runner's loop.
    async def _gather_all():
        return await asyncio.gather(
            fetch_team_elo(match.home.name, scope=elo_scope, alias=home_alias),
            fetch_team_elo(match.away.name, scope=elo_scope, alias=away_alias),
            fetch_match_odds(match.home.name, match.away.name),
            return_exceptions=True,
        )

    try:
        results = asyncio.run(_gather_all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch raw match data: %s", exc)
        return raw

    elo_home_raw, elo_away_raw, odds = results

    if isinstance(elo_home_raw, dict):
        raw["team"]["elo_home"] = elo_home_raw.get("elo_rating")
    elif isinstance(elo_home_raw, BaseException):
        logger.warning("Elo fetch failed for %s: %s", match.home.name, elo_home_raw)

    if isinstance(elo_away_raw, dict):
        raw["team"]["elo_away"] = elo_away_raw.get("elo_rating")
    elif isinstance(elo_away_raw, BaseException):
        logger.warning("Elo fetch failed for %s: %s", match.away.name, elo_away_raw)

    if isinstance(odds, dict) and odds:
        raw["market"]["odds_home"] = odds.get("home")
        raw["market"]["odds_draw"] = odds.get("draw")
        raw["market"]["odds_away"] = odds.get("away")
        raw["market"]["odds_source"] = odds.get("source")
        raw["market"]["odds_fresh"] = not odds.get("stale", True)
    elif isinstance(odds, BaseException):
        logger.warning("Odds fetch failed: %s", odds)

    return raw


def query_fixture(match_id: str, model_cls) -> Any | None:
    """Query a fixture by match_id from the kernel DB.

    model_cls: KernelMatchFixture (for UCL/EPL).

    Returns the fixture object or None.
    """
    from app.kernel.kernel_db import get_kernel_session
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query fixture %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def query_result(match_id: str, model_cls) -> Any | None:
    """Query a match result by match_id from the kernel DB."""
    from app.kernel.kernel_db import get_kernel_session
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query result %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def build_match_identity(
    fixture: Any,
    competition: CompetitionIdentity,
    season_key: str,
    default_stage: str = "group_stage",
) -> MatchIdentity:
    """Build MatchIdentity from a KernelMatchFixture row."""
    home = TeamIdentity(
        code=(fixture.home_team or "HOME")[:3].upper(),
        name=fixture.home_team or "Home",
        competition=competition,
    )
    away = TeamIdentity(
        code=(fixture.away_team or "AWAY")[:3].upper(),
        name=fixture.away_team or "Away",
        competition=competition,
    )
    return MatchIdentity(
        match_id=fixture.match_id,
        season=SeasonIdentity(competition=competition, season_key=season_key),
        stage=fixture.stage or default_stage,
        round=None,
        home=home,
        away=away,
        kickoff_utc=fixture.kickoff_utc or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def build_match_outcome(result: Any) -> MatchOutcome | None:
    """Build MatchOutcome from a KernelMatchResult row."""
    if result is None:
        return None
    return MatchOutcome(
        match_id=result.match_id,
        home_score=result.home_score,
        away_score=result.away_score,
        outcome=result.outcome,
        finished_at=result.finished_at or datetime.now(timezone.utc),
    )


def save_fixture(parsed: dict, competition: str, season: str) -> None:
    """Upsert a parsed fixture into kernel_match_fixtures.

    parsed: dict from football_data_client.parse_fixture()
    """
    from app.kernel.kernel_db import get_kernel_session, KernelMatchFixture
    session = get_kernel_session()
    try:
        now = datetime.now(timezone.utc)
        existing = session.get(KernelMatchFixture, parsed["match_id"])
        if existing:
            existing.home_team = parsed["home_team"]
            existing.away_team = parsed["away_team"]
            existing.kickoff_utc = parsed["kickoff_utc"]
            existing.stage = parsed["stage"]
            existing.status = parsed["status"]
            existing.venue = parsed["venue"]
            if parsed.get("home_score") is not None:
                existing.home_score = parsed["home_score"]
            if parsed.get("away_score") is not None:
                existing.away_score = parsed["away_score"]
            existing.updated_at = now
        else:
            fixture = KernelMatchFixture(
                match_id=parsed["match_id"],
                competition=competition,
                season=season,
                home_team=parsed["home_team"],
                away_team=parsed["away_team"],
                kickoff_utc=parsed["kickoff_utc"],
                stage=parsed["stage"],
                status=parsed["status"],
                home_score=parsed.get("home_score"),
                away_score=parsed.get("away_score"),
                venue=parsed["venue"],
                created_at=now,
                updated_at=now,
            )
            session.add(fixture)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("Failed to save fixture %s: %s", parsed.get("match_id"), exc)
    finally:
        session.close()
