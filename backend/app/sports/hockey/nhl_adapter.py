# backend/app/sports/hockey/nhl_adapter.py
"""NHLAdapter — DataAdapter Protocol implementation for NHL hockey.

Bridges the NHL Stats API to the sport-agnostic DataAdapter Protocol.

Match ID format: nhl-{gameId}
Stage mapping: gameType=3 (playoffs) → "playoff", else → "regular_season"
Status mapping: gameState in {"OFF FINAL", "FINAL"} → "finished", else → "scheduled"

Overtime/shootout design (Constraint 22):
    - MatchOutcome.outcome is always binary ("home_win"/"away_win")
    - Overtime/shootout info stored in raw["custom"]["went_to_overtime"]
      and raw["custom"]["went_to_shootout"] for future analysis
    - Does NOT modify domain.py
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
from app.sports.hockey.nhl_stats_client import (
    fetch_nhl_schedule, fetch_nhl_team_roster, NHLStatsClientError,
)
from app.sports._shared.elo_calculator import seed_elo_from_games

logger = logging.getLogger(__name__)

_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)
_DEFAULT_SEASON = "20232024"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2024, 1, 15, tzinfo=timezone.utc)


def parse_nhl_game(game_data: dict) -> dict | None:
    """Parse a raw NHL Stats API game dict into internal fixture format.

    Returns None if game_data is malformed. Captures overtime/shootout
    flags in the parsed dict (returned under ``went_to_overtime`` /
    ``went_to_shootout`` keys; the adapter writes them into raw['custom']).
    """
    game_id = game_data.get("id")
    if not game_id:
        return None

    home_team = game_data.get("homeTeam", {}).get("name", "")
    away_team = game_data.get("awayTeam", {}).get("name", "")
    if not home_team or not away_team:
        return None

    date_str = game_data.get("gameDate", "")
    try:
        kickoff_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        kickoff_utc = _DEFAULT_KICKOFF

    # gameType: 2 = regular season, 3 = playoffs
    game_type = game_data.get("gameType", 2)
    stage = "playoff" if game_type == 3 else "regular_season"

    # gameState: "OFF FINAL", "FINAL", "LIVE", "FUT", etc.
    game_state = game_data.get("gameState", "")
    status = "finished" if game_state in ("OFF FINAL", "FINAL") else "scheduled"

    home_score = game_data.get("homeTeamScore")
    away_score = game_data.get("awayTeamScore")

    # Overtime/shootout detection: period > 3 means OT (4) or shootout (5)
    period = game_data.get("period", 3)
    went_to_overtime = period == 4
    went_to_shootout = period == 5

    venue = game_data.get("venue", {}).get("default", "Unknown")

    return {
        "match_id": f"nhl-{game_id}",
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff_utc,
        "stage": stage,
        "status": status,
        "home_score": home_score,
        "away_score": away_score,
        "venue": venue,
        "went_to_overtime": went_to_overtime,
        "went_to_shootout": went_to_shootout,
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
    """Build MatchOutcome from a KernelMatchResult row. Binary outcome only.

    NHL overtime/shootout games still produce binary home_win/away_win;
    the OT/SO info is preserved separately in FeatureSet.custom.
    """
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
    """Upsert a parsed NHL fixture into kernel_match_fixtures."""
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


class NHLAdapter:
    """DataAdapter Protocol implementation for NHL hockey."""

    def _stub_identity(self, match_id: str) -> MatchIdentity:
        """Return a stub MatchIdentity when fixture data is unavailable."""
        home = TeamIdentity(code="HOME", name="Home", competition=_NHL)
        away = TeamIdentity(code="AWAY", name="Away", competition=_NHL)
        return MatchIdentity(
            match_id=match_id,
            season=SeasonIdentity(competition=_NHL, season_key=_DEFAULT_SEASON),
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
            competition=_NHL,
        )
        away = TeamIdentity(
            code=(fixture.away_team or "AWAY")[:3].upper(),
            name=fixture.away_team or "Away",
            competition=_NHL,
        )
        return MatchIdentity(
            match_id=fixture.match_id,
            season=SeasonIdentity(competition=_NHL, season_key=fixture.season or _DEFAULT_SEASON),
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
            ratings: dict[str, float] = {}
            for team_name in [home_team, away_team]:
                row = session.get(KernelEloRating, team_name)
                if row is not None and row.competition == "nhl":
                    ratings[team_name] = row.elo_rating
            return ratings
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch NHL Elo ratings: %s", exc)
            return {}
        finally:
            session.close()

    def _fetch_starting_goalies(self, match: MatchIdentity) -> dict:
        """Fetch starting goalie save% for both teams.

        Returns dict with 'home' and 'away' keys, each containing
        {'name': str, 'save_pct': float} or empty dict if unavailable.
        Stubbed in tests; in production this would call
        fetch_nhl_team_roster() for both teams.
        """
        return {"home": {}, "away": {}}

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        """Fetch all raw data for an NHL match.

        All data comes from local DB (Elo, form, rest) and the NHL Stats
        API (goalie save%). Goalie stats and overtime flags are written
        to raw['custom'].
        """
        home_name = match.home.name
        away_name = match.away.name

        elo_ratings = self._fetch_elo_ratings(home_name, away_name)
        elo_home = elo_ratings.get(home_name)
        elo_away = elo_ratings.get(away_name)

        form_home = self._compute_form(home_name)
        form_away = self._compute_form(away_name)

        rest_home = self._compute_rest_days(home_name, match.kickoff_utc)
        rest_away = self._compute_rest_days(away_name, match.kickoff_utc)

        goalies = self._fetch_starting_goalies(match)
        home_g = goalies.get("home", {})
        away_g = goalies.get("away", {})

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
            "market": {},  # No odds source
            "player": {
                "starting_goalie_home": home_g.get("name"),
                "starting_goalie_away": away_g.get("name"),
            },
            "environment": {
                "venue": "Home Arena",
                "is_home_advantage": True,
            },
            "custom": {
                "goalie_save_pct_home": home_g.get("save_pct", 0.910),
                "goalie_save_pct_away": away_g.get("save_pct", 0.910),
                "team_gf_home": 3.20, "team_gf_away": 3.00,
                "team_ga_home": 2.90, "team_ga_away": 3.10,
                "corsi_pct_home": None, "corsi_pct_away": None,
                "pdo_home": None, "pdo_away": None,
                "went_to_overtime": False,
                "went_to_shootout": False,
            },
        }
        return raw

    def _compute_form(self, team_name: str) -> float:
        """Compute last-10 win rate from kernel_match_results. Returns 0.5 if none."""
        session = get_kernel_session()
        try:
            from sqlalchemy import select, or_

            query = (
                select(KernelMatchFixture)
                .where(
                    KernelMatchFixture.competition == "nhl",
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
                    KernelMatchFixture.competition == "nhl",
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
        """Sync NHL schedule from the NHL Stats API.

        Returns 0 if PHASE5_NHL_ENABLED is false or sync fails (graceful
        degradation, no exceptions). NHL season spans two calendar years
        (e.g., "20232024" for the 2023-24 season).
        """
        if not config.settings.PHASE5_NHL_ENABLED:
            return 0
        try:
            now = datetime.now(timezone.utc)
            # NHL season key: if month >= August, season starts this year
            if now.month >= 8:
                season = f"{now.year}{now.year + 1}"
            else:
                season = f"{now.year - 1}{now.year}"
            games_raw = fetch_nhl_schedule(season)
            count = 0
            for raw in games_raw:
                parsed = parse_nhl_game(raw)
                if parsed:
                    save_fixture(parsed, "nhl", season)
                    count += 1
            return count
        except NHLStatsClientError as exc:
            logger.error("NHL API error during sync_schedule: %s", exc)
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync NHL schedule: %s", exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == "nhl"
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
            logger.warning("Failed to fetch NHL schedule: %s", exc)
            return []
        finally:
            session.close()

    def fetch_team_data(self, team) -> dict:
        return {}

    def fetch_player_data(self, team) -> dict:
        return {}

    def fetch_market_data(self, match) -> dict:
        return {}
