# backend/app/sports/basketball/nba_adapter.py
"""NBAAdapter — DataAdapter Protocol implementation for NBA basketball.

Bridges balldontlie.io API to the sport-agnostic DataAdapter Protocol.
The Kernel never sees basketball-specific code — it only sees DataAdapter.

Match ID format: nba-{balldontlie_game_id}
Stage mapping: postseason=False → "regular_season", postseason=True → "playoff"

When BALLDONTLIE_API_KEY is empty, sync_schedule() returns 0 and
fetch_all_data() returns a raw dict with None Elo values (graceful
degradation, no exceptions).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TypeVar

from app.core import config
from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData
from app.kernel.kernel_db import (
    get_kernel_session, KernelBase, KernelMatchFixture, KernelMatchResult,
    KernelEloRating,
)
from app.sports.basketball.balldontlie_client import fetch_nba_games

logger = logging.getLogger(__name__)

_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)
# balldontlie season year = autumn start (2026 → 2026-27 campaign).
_FD_SEASON = 2026
_DEFAULT_SEASON = "2026-27"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2026, 10, 21, tzinfo=timezone.utc)


def _season_key_for_year(year: int) -> str:
    return f"{year}-{str(year + 1)[-2:]}"


def parse_nba_game(game_data: dict) -> dict | None:
    """Parse a raw balldontlie.io game dict into internal fixture format.

    Returns None if game_data is malformed.
    """
    game_id = game_data.get("id")
    if not game_id:
        return None

    home_team = game_data.get("home_team", {}).get("full_name", "")
    away_team = game_data.get("visitor_team", {}).get("full_name", "")
    if not home_team or not away_team:
        return None

    date_str = game_data.get("date", "")
    try:
        kickoff_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        kickoff_utc = _DEFAULT_KICKOFF

    postseason = game_data.get("postseason", False)
    stage = "playoff" if postseason else "regular_season"

    status_raw = game_data.get("status", "")
    status = "finished" if status_raw == "Final" else "scheduled"

    home_score = game_data.get("home_team_score")
    away_score = game_data.get("visitor_team_score")

    return {
        "match_id": f"nba-{game_id}",
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff_utc,
        "stage": stage,
        "status": status,
        "home_score": home_score,
        "away_score": away_score,
    }


# Both query helpers just forward to Session.get, so the row type is whatever
# model class the caller asked for. Returning `object` instead made every
# attribute read on the result unverifiable.
_RowT = TypeVar("_RowT", bound=KernelBase)


def query_fixture(match_id: str, model_cls: type[_RowT]) -> _RowT | None:
    """Query a fixture by match_id from the kernel DB."""
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query fixture %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def query_result(match_id: str, model_cls: type[_RowT]) -> _RowT | None:
    """Query a match result by match_id from the kernel DB."""
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query result %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def build_match_outcome(result: KernelMatchResult | None) -> MatchOutcome | None:
    """Build MatchOutcome from a KernelMatchResult row."""
    if result is None:
        return None
    home_score = result.home_score or 0
    away_score = result.away_score or 0
    if home_score > away_score:
        outcome = "home_win"
    else:
        outcome = "away_win"
    return MatchOutcome(
        match_id=result.match_id,
        home_score=home_score,
        away_score=away_score,
        outcome=outcome,
        finished_at=result.finished_at or datetime.now(timezone.utc),
    )


def save_fixture(parsed: dict, competition: str, season: str) -> None:
    """Upsert a parsed NBA fixture into kernel_match_fixtures (+ result when scored)."""
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
                venue=parsed.get("venue", "Unknown"),
                created_at=now,
                updated_at=now,
            )
            session.add(fixture)
        hs = parsed.get("home_score")
        aws = parsed.get("away_score")
        if hs is not None and aws is not None:
            hs_i: int | None
            aws_i: int | None
            try:
                hs_i, aws_i = int(hs), int(aws)
            except (TypeError, ValueError):
                hs_i = aws_i = None
            if hs_i is not None and aws_i is not None:
                outcome = "home_win" if hs_i > aws_i else "away_win"
                finished_at = parsed.get("kickoff_utc") or now
                existing_result = session.get(KernelMatchResult, parsed["match_id"])
                if existing_result is None:
                    session.add(
                        KernelMatchResult(
                            match_id=parsed["match_id"],
                            home_score=hs_i,
                            away_score=aws_i,
                            outcome=outcome,
                            finished_at=finished_at,
                            created_at=now,
                        )
                    )
                else:
                    existing_result.home_score = hs_i
                    existing_result.away_score = aws_i
                    existing_result.outcome = outcome
                    if existing_result.finished_at is None:
                        existing_result.finished_at = finished_at
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("Failed to save fixture %s: %s", parsed.get("match_id"), exc)
    finally:
        session.close()


class NBAAdapter:
    """DataAdapter Protocol implementation for NBA basketball."""

    def _stub_identity(self, match_id: str) -> MatchIdentity:
        """Return a stub MatchIdentity when fixture data is unavailable."""
        home = TeamIdentity(code="HOME", name="Home", competition=_NBA)
        away = TeamIdentity(code="AWAY", name="Away", competition=_NBA)
        return MatchIdentity(
            match_id=match_id,
            season=SeasonIdentity(competition=_NBA, season_key=_DEFAULT_SEASON),
            stage=_DEFAULT_STAGE,
            round=None,
            home=home,
            away=away,
            kickoff_utc=_DEFAULT_KICKOFF,
            is_stub=True,
        )

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        fixture = query_fixture(match_id, KernelMatchFixture)
        if fixture is None:
            return self._stub_identity(match_id)
        home = TeamIdentity(
            code=(fixture.home_team or "HOME")[:3].upper(),
            name=fixture.home_team or "Home",
            competition=_NBA,
        )
        away = TeamIdentity(
            code=(fixture.away_team or "AWAY")[:3].upper(),
            name=fixture.away_team or "Away",
            competition=_NBA,
        )
        return MatchIdentity(
            match_id=fixture.match_id,
            season=SeasonIdentity(competition=_NBA, season_key=fixture.season or _DEFAULT_SEASON),
            stage=fixture.stage or _DEFAULT_STAGE,
            round=None,
            home=home,
            away=away,
            kickoff_utc=fixture.kickoff_utc or _DEFAULT_KICKOFF,
        )

    def _fetch_elo_ratings(self, home_team: str, away_team: str) -> dict[str, float]:
        """Fetch Elo ratings for both teams from kernel_elo_ratings table."""
        session = get_kernel_session()
        try:
            ratings = {}
            for team_name in [home_team, away_team]:
                row = session.get(KernelEloRating, team_name)
                if row is not None:
                    ratings[team_name] = row.elo_rating
            return ratings
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch Elo ratings: %s", exc)
            return {}
        finally:
            session.close()

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        """Fetch all raw data for an NBA match.

        All data comes from local DB (no API calls). Elo ratings are read
        from kernel_elo_ratings table. Form and rest days are computed
        from recent fixtures.
        """
        home_name = match.home.name
        away_name = match.away.name

        elo_ratings = self._fetch_elo_ratings(home_name, away_name)
        elo_home = elo_ratings.get(home_name)
        elo_away = elo_ratings.get(away_name)

        # Compute form (last-10 win rate) from recent results
        form_home = self._compute_form(home_name)
        form_away = self._compute_form(away_name)

        # Rest days (simplified — 0 if unknown)
        rest_home = self._compute_rest_days(home_name, match.kickoff_utc)
        rest_away = self._compute_rest_days(away_name, match.kickoff_utc)

        raw: dict = {
            "team": {
                "elo_home": elo_home,
                "elo_away": elo_away,
                "form_home": form_home,
                "form_away": form_away,
            },
            "general": {
                "rest_days_home": rest_home,
                "rest_days_away": rest_away,
                "days_since_last_match": rest_home,
            },
            "market": {},
            "player": {},
            "environment": {
                "venue": "Home Arena",
                "is_home_advantage": True,
            },
            "custom": {
                # P1-B2: rest_days <= 1 treated as back-to-back
                "b2b_home": rest_home is not None and float(rest_home) <= 1.0,
                "b2b_away": rest_away is not None and float(rest_away) <= 1.0,
            },
        }
        try:
            from app.sports._shared.team_geo import travel_between_teams

            travel = travel_between_teams(home_name, away_name, "nba")
            raw["custom"].update(travel)
            if travel.get("travel_km_away") is not None:
                raw["general"]["travel_distance_km"] = travel["travel_km_away"]
        except Exception:  # noqa: BLE001
            logger.debug("NBA travel enrich skipped", exc_info=True)
        try:
            from app.kernel.market_liquidity import inject_liquidity_into_custom

            raw["custom"] = inject_liquidity_into_custom(
                raw.get("custom") or {},
                match.match_id,
            )
        except Exception:  # noqa: BLE001
            logger.debug("NBA liquidity enrich skipped", exc_info=True)
        try:
            from app.sports.basketball.nba_injury import injury_impact_for_team

            live = {}
            try:
                from app.services.nba_live_injury_service import get_live_injury_impact

                live = {
                    "home": get_live_injury_impact(home_name),
                    "away": get_live_injury_impact(away_name),
                }
            except Exception:  # noqa: BLE001 — keep the static table usable
                logger.debug("NBA live injury enrich unavailable", exc_info=True)

            for side, name in (("home", home_name), ("away", away_name)):
                result = live.get(side)
                # A reached provider wins; unreachable or silent on this team
                # falls through to the static Out table.
                if result is not None and result.available and result.impact is not None:
                    impact: float | None = float(result.impact)
                    source = "live_provider"
                else:
                    impact = injury_impact_for_team(name)
                    source = "static_table"
                if impact is None:
                    continue
                raw["player"][f"injury_impact_{side}"] = float(impact)
                raw["custom"][f"injury_impact_{side}"] = float(impact)
                raw["custom"][f"injury_source_{side}"] = source
        except Exception:  # noqa: BLE001
            logger.debug("NBA injury enrich skipped", exc_info=True)
        try:
            from app.sports.basketball.nba_team_ratings import ratings_for_team

            live_ratings: dict[str, dict[str, float] | None] = {}
            try:
                from app.services.nba_live_ratings_service import get_live_team_ratings

                for side, name in (("home", home_name), ("away", away_name)):
                    rating = get_live_team_ratings(match.season.season_key, name)
                    live_ratings[side] = rating.ratings if rating.available else None
            except Exception:  # noqa: BLE001 — keep the static table usable
                logger.debug("NBA live ratings enrich unavailable", exc_info=True)
                live_ratings = {}

            # Both sides must come from one source. The engine consumes the
            # ORtg-DRtg differential, so pairing a live season level against a
            # static multi-year level would manufacture a spurious edge.
            if live_ratings.get("home") and live_ratings.get("away"):
                home_r = live_ratings["home"]
                away_r = live_ratings["away"]
                ratings_source = "live_provider"
            else:
                home_r = ratings_for_team(home_name)
                away_r = ratings_for_team(away_name)
                ratings_source = "static_table"
            if home_r is not None and away_r is not None:
                raw["custom"]["ortg_home"] = float(home_r["ortg"])
                raw["custom"]["drtg_home"] = float(home_r["drtg"])
                raw["custom"]["ortg_away"] = float(away_r["ortg"])
                raw["custom"]["drtg_away"] = float(away_r["drtg"])
                # One key, not per side: a mixed-source pair is never written.
                raw["custom"]["ratings_source"] = ratings_source
        except Exception:  # noqa: BLE001
            logger.debug("NBA team ratings enrich skipped", exc_info=True)
        # Real market over/under line (P1-O1). Default-off; absent it the engine
        # keeps quoting against the league average, which equals its own expected
        # total by construction.
        from app.services.market_totals_service import inject_market_total_into_custom

        raw["custom"] = inject_market_total_into_custom(
            raw.get("custom") or {},
            sport="basketball",
            kickoff_utc=match.kickoff_utc,
            home_name=home_name,
            away_name=away_name,
        )
        return raw

    def _compute_form(self, team_name: str, as_of: datetime | None = None) -> float:
        """As-of last-10 win rate from kernel fixtures. Returns 0.5 if none."""
        session = get_kernel_session()
        try:
            from sqlalchemy import or_, select
            from app.sports._shared.rest_form import form_as_of

            query = (
                select(KernelMatchFixture)
                .where(
                    KernelMatchFixture.competition == "nba",
                    or_(
                        KernelMatchFixture.home_team == team_name,
                        KernelMatchFixture.away_team == team_name,
                    ),
                )
            )
            fixtures = session.execute(query).scalars().all()
            history = [
                {
                    "match_id": f.match_id,
                    "home_team": f.home_team,
                    "away_team": f.away_team,
                    "home_score": f.home_score,
                    "away_score": f.away_score,
                    "kickoff_utc": f.kickoff_utc,
                }
                for f in fixtures
            ]
            return form_as_of(team_name, as_of, history)
        except Exception:  # noqa: BLE001
            return 0.5
        finally:
            session.close()

    def _compute_rest_days(self, team_name: str, kickoff_utc: datetime) -> float | None:
        """Days since last match before kickoff. Returns None if unknown."""
        session = get_kernel_session()
        try:
            from sqlalchemy import or_, select
            from app.sports._shared.rest_form import rest_days_as_of

            query = (
                select(KernelMatchFixture)
                .where(
                    KernelMatchFixture.competition == "nba",
                    or_(
                        KernelMatchFixture.home_team == team_name,
                        KernelMatchFixture.away_team == team_name,
                    ),
                )
            )
            fixtures = session.execute(query).scalars().all()
            history = [
                {
                    "match_id": f.match_id,
                    "home_team": f.home_team,
                    "away_team": f.away_team,
                    "home_score": f.home_score,
                    "away_score": f.away_score,
                    "kickoff_utc": f.kickoff_utc,
                }
                for f in fixtures
            ]
            return rest_days_as_of(team_name, kickoff_utc, history)
        except Exception:  # noqa: BLE001
            return None
        finally:
            session.close()

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        result = query_result(match_id, KernelMatchResult)
        return build_match_outcome(result)

    def sync_schedule(self) -> int:
        """Sync NBA schedule from balldontlie.io.

        Tries preferred season year then previous (schedules often lag mid-summer).
        Returns 0 if API key is not configured or all seasons fail.
        """
        if not config.settings.BALLDONTLIE_API_KEY:
            return 0

        seasons_to_try = [_FD_SEASON]
        if _FD_SEASON > 2000:
            seasons_to_try.append(_FD_SEASON - 1)

        last_err: Exception | None = None
        for season_year in seasons_to_try:
            try:
                games_raw = fetch_nba_games(season_year)
                if not games_raw:
                    logger.info(
                        "NBA season %s returned 0 games; trying fallback",
                        season_year,
                    )
                    continue
                season_key = (
                    _DEFAULT_SEASON
                    if season_year == _FD_SEASON
                    else _season_key_for_year(season_year)
                )
                if season_year != _FD_SEASON:
                    logger.warning(
                        "NBA preferred season %s empty/unavailable; "
                        "using %s (%s games)",
                        _FD_SEASON,
                        season_year,
                        len(games_raw),
                    )
                count = 0
                for raw in games_raw:
                    parsed = parse_nba_game(raw)
                    if parsed:
                        save_fixture(parsed, "nba", season_key)
                        count += 1
                return count
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                msg = str(exc).lower()
                # Rate limit / empty preferred season → try previous year.
                if any(
                    tok in msg
                    for tok in (
                        "404",
                        "not found",
                        "does not exist",
                        "empty",
                        "rate limit",
                    )
                ):
                    logger.info(
                        "NBA season %s not usable (%s); trying fallback",
                        season_year,
                        str(exc)[:120],
                    )
                    continue
                logger.error("Failed to sync NBA schedule: %s", exc)
                return 0
        if last_err is not None:
            logger.error("Failed to sync NBA schedule: %s", last_err)
        return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == "nba"
            )
            if filters.status:
                query = query.where(KernelMatchFixture.status == filters.status)
            if filters.stage:
                query = query.where(KernelMatchFixture.stage == filters.stage)
            if filters.limit:
                query = query.limit(filters.limit)
            fixtures = session.execute(query).scalars().all()
            return [
                RawMatchData(
                    match=self.get_match_identity(f.match_id), raw_json={}
                )
                for f in fixtures
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch NBA schedule: %s", exc)
            return []
        finally:
            session.close()

    def fetch_team_data(self, team: TeamIdentity) -> dict:
        return {}

    def fetch_player_data(self, team: TeamIdentity) -> dict:
        return {}

    def fetch_market_data(self, match: MatchIdentity) -> dict:
        return {}
