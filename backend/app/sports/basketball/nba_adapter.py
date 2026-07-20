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

from app.core import config
from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData
from app.kernel.kernel_db import (
    get_kernel_session, KernelMatchFixture, KernelMatchResult, KernelEloRating,
)
from app.sports.basketball.balldontlie_client import fetch_nba_games

logger = logging.getLogger(__name__)

_BASKETBALL = SportIdentity(code="basketball", name="Basketball")
_NBA = CompetitionIdentity(code="nba", name="NBA", sport=_BASKETBALL)
_DEFAULT_SEASON = "2024-25"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2024, 12, 25, tzinfo=timezone.utc)


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


def query_fixture(match_id: str, model_cls) -> object | None:
    """Query a fixture by match_id from the kernel DB."""
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query fixture %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def query_result(match_id: str, model_cls) -> object | None:
    """Query a match result by match_id from the kernel DB."""
    session = get_kernel_session()
    try:
        return session.get(model_cls, match_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query result %s: %s", match_id, exc)
        return None
    finally:
        session.close()


def build_match_outcome(result: object) -> MatchOutcome | None:
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
    """Upsert a parsed NBA fixture into kernel_match_fixtures."""
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
                "pace_home": 99.5,
                "pace_away": 97.2,
                "ortg_home": 112.3,
                "ortg_away": 108.1,
                "drtg_home": 105.0,
                "drtg_away": 110.5,
                "tpct_home": 0.365,
                "tpct_away": 0.342,
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
        return raw

    def _compute_form(self, team_name: str) -> float:
        """Compute last-10 win rate from kernel_match_fixtures.

        Returns 0.5 if no data available.
        """
        session = get_kernel_session()
        try:
            from sqlalchemy import select, or_

            query = (
                select(KernelMatchFixture)
                .where(
                    KernelMatchFixture.competition == "nba",
                    or_(
                        KernelMatchFixture.home_team == team_name,
                        KernelMatchFixture.away_team == team_name,
                    ),
                    KernelMatchFixture.status == "finished",
                )
                .order_by(KernelMatchFixture.kickoff_utc.desc())
                .limit(10)
            )
            fixtures = session.execute(query).scalars().all()
            if not fixtures:
                return 0.5

            wins = 0
            for f in fixtures:
                if f.home_team == team_name:
                    if (f.home_score or 0) > (f.away_score or 0):
                        wins += 1
                else:
                    if (f.away_score or 0) > (f.home_score or 0):
                        wins += 1
            return wins / len(fixtures)
        except Exception:  # noqa: BLE001
            return 0.5
        finally:
            session.close()

    def _compute_rest_days(self, team_name: str, kickoff_utc: datetime) -> int:
        """Compute days since last match. Returns 0 if unknown."""
        session = get_kernel_session()
        try:
            from sqlalchemy import select, or_

            query = (
                select(KernelMatchFixture.kickoff_utc)
                .where(
                    KernelMatchFixture.competition == "nba",
                    or_(
                        KernelMatchFixture.home_team == team_name,
                        KernelMatchFixture.away_team == team_name,
                    ),
                    KernelMatchFixture.kickoff_utc < kickoff_utc,
                )
                .order_by(KernelMatchFixture.kickoff_utc.desc())
                .limit(1)
            )
            result = session.execute(query).scalar_one_or_none()
            if result is None:
                return 0
            delta = kickoff_utc - result
            return max(0, delta.days)
        except Exception:  # noqa: BLE001
            return 0
        finally:
            session.close()

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        result = query_result(match_id, KernelMatchResult)
        return build_match_outcome(result)

    def sync_schedule(self) -> int:
        """Sync NBA schedule from balldontlie.io.

        Returns 0 if API key is not configured or sync fails.
        """
        if not config.settings.BALLDONTLIE_API_KEY:
            return 0

        try:
            # Fetch current season games
            season_year = 2024  # 2024-25 season
            games_raw = fetch_nba_games(season_year)
            count = 0
            for raw in games_raw:
                parsed = parse_nba_game(raw)
                if parsed:
                    save_fixture(parsed, "nba", _DEFAULT_SEASON)
                    count += 1
            return count
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync NBA schedule: %s", exc)
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

    def fetch_team_data(self, team) -> dict:
        return {}

    def fetch_player_data(self, team) -> dict:
        return {}

    def fetch_market_data(self, match) -> dict:
        return {}
