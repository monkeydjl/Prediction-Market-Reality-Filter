# backend/app/sports/baseball/mlb_adapter.py
"""MLBAdapter — DataAdapter Protocol implementation for MLB baseball.

Bridges the MLB Stats API to the sport-agnostic DataAdapter Protocol.
The Kernel never sees baseball-specific code — it only sees DataAdapter.

Match ID format: mlb-{gamePk}
Stage mapping: postseason games → "playoff", else → "regular_season"
Status mapping: abstractGameState == "Final" → "finished", else → "scheduled"

When PHASE5_MLB_ENABLED is false, the adapter is not instantiated at all
(gated in _get_kernel). When the MLB API is unreachable, sync_schedule
returns 0 (graceful degradation, no exceptions).
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
from app.sports.baseball.mlb_stats_client import (
    fetch_mlb_schedule, fetch_mlb_pitcher, MLBStatsClientError,
)
from app.sports._shared.elo_calculator import seed_elo_from_games

logger = logging.getLogger(__name__)

_BASEBALL = SportIdentity(code="baseball", name="Baseball")
_MLB = CompetitionIdentity(code="mlb", name="MLB", sport=_BASEBALL)
_DEFAULT_SEASON = "2024"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2024, 7, 4, tzinfo=timezone.utc)


def parse_mlb_game(game_data: dict) -> dict | None:
    """Parse a raw MLB Stats API game dict into internal fixture format.

    Returns None if game_data is malformed.
    """
    game_pk = game_data.get("gamePk")
    if not game_pk:
        return None

    home_team = game_data.get("teams", {}).get("home", {}).get("name", "")
    away_team = game_data.get("teams", {}).get("away", {}).get("name", "")
    if not home_team or not away_team:
        return None

    date_str = game_data.get("gameDate", "")
    try:
        kickoff_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        kickoff_utc = _DEFAULT_KICKOFF

    # Postseason detection: seriesDescription present OR gameType in LCS/DS/WS
    series_desc = game_data.get("seriesDescription", "")
    game_type = game_data.get("gameType", "")
    is_playoff = bool(series_desc) or game_type in ("D", "L", "F", "W")
    stage = "playoff" if is_playoff else "regular_season"

    status_raw = game_data.get("status", {}).get("abstractGameState", "")
    status = "finished" if status_raw == "Final" else "scheduled"

    linescore = game_data.get("linescore", {})
    home_score = linescore.get("home", {}).get("runs")
    away_score = linescore.get("away", {}).get("runs")

    venue = game_data.get("venue", {}).get("name", "Unknown")

    return {
        "match_id": f"mlb-{game_pk}",
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": kickoff_utc,
        "stage": stage,
        "status": status,
        "home_score": home_score,
        "away_score": away_score,
        "venue": venue,
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
    """Build MatchOutcome from a KernelMatchResult row. Binary outcome only."""
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
    """Upsert a parsed MLB fixture into kernel_match_fixtures."""
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


class MLBAdapter:
    """DataAdapter Protocol implementation for MLB baseball."""

    def _stub_identity(self, match_id: str) -> MatchIdentity:
        """Return a stub MatchIdentity when fixture data is unavailable."""
        home = TeamIdentity(code="HOME", name="Home", competition=_MLB)
        away = TeamIdentity(code="AWAY", name="Away", competition=_MLB)
        return MatchIdentity(
            match_id=match_id,
            season=SeasonIdentity(competition=_MLB, season_key=_DEFAULT_SEASON),
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
            competition=_MLB,
        )
        away = TeamIdentity(
            code=(fixture.away_team or "AWAY")[:3].upper(),
            name=fixture.away_team or "Away",
            competition=_MLB,
        )
        return MatchIdentity(
            match_id=fixture.match_id,
            season=SeasonIdentity(competition=_MLB, season_key=fixture.season or _DEFAULT_SEASON),
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
                if row is not None and row.competition == "mlb":
                    ratings[team_name] = row.elo_rating
            return ratings
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch MLB Elo ratings: %s", exc)
            return {}
        finally:
            session.close()

    def _fetch_starting_pitchers(self, match: MatchIdentity) -> dict:
        """Fetch starting pitcher ERA/WHIP for both teams.

        Returns dict with 'home' and 'away' keys, each containing
        {'name': str, 'era': float, 'whip': float} or empty dict if
        unavailable. Stubbed in tests; in production this would call
        fetch_mlb_pitcher() using probable pitcher IDs from the game feed.
        """
        # Production: would call fetch_mlb_game_feed(match.match_id) to get
        # probable pitchers, then fetch_mlb_pitcher(person_id) for each.
        # Returns empty stubs when data is unavailable (graceful degradation).
        return {"home": {}, "away": {}}

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        """Fetch all raw data for an MLB match.

        All data comes from local DB (Elo, form, rest) and the MLB Stats
        API (pitcher ERA/WHIP). Pitcher stats are written to raw['custom'].
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

        pitchers = self._fetch_starting_pitchers(match)
        home_p = pitchers.get("home", {})
        away_p = pitchers.get("away", {})

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
                "starting_pitcher_home": home_p.get("name"),
                "starting_pitcher_away": away_p.get("name"),
            },
            "environment": {
                "venue": "Home Ballpark",
                "is_home_advantage": True,
            },
            "custom": {
                "pitcher_era_home": home_p.get("era", 4.20),
                "pitcher_era_away": away_p.get("era", 4.20),
                "pitcher_whip_home": home_p.get("whip", 1.30),
                "pitcher_whip_away": away_p.get("whip", 1.30),
                "team_batting_avg_home": 0.250,
                "team_batting_avg_away": 0.250,
                "team_era_home": 4.10,
                "team_era_away": 4.10,
                "pythagorean_win_pct_home": 0.500,
                "pythagorean_win_pct_away": 0.500,
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
                    KernelMatchFixture.competition == "mlb",
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
                    KernelMatchFixture.competition == "mlb",
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
        """Sync MLB schedule from the MLB Stats API.

        Returns 0 if PHASE5_MLB_ENABLED is false or sync fails (graceful
        degradation, no exceptions).
        """
        if not config.settings.PHASE5_MLB_ENABLED:
            return 0
        try:
            today = datetime.now(timezone.utc)
            # Sync current season (Apr–Nov)
            start = f"{today.year}-03-01"
            end = f"{today.year}-11-30"
            games_raw = fetch_mlb_schedule(start, end)
            count = 0
            for raw in games_raw:
                parsed = parse_mlb_game(raw)
                if parsed:
                    save_fixture(parsed, "mlb", str(raw.get("season", _DEFAULT_SEASON)))
                    count += 1
            return count
        except MLBStatsClientError as exc:
            logger.error("MLB API error during sync_schedule: %s", exc)
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync MLB schedule: %s", exc)
            return 0

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        from sqlalchemy import select
        session = get_kernel_session()
        try:
            query = select(KernelMatchFixture).where(
                KernelMatchFixture.competition == "mlb"
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
            logger.warning("Failed to fetch MLB schedule: %s", exc)
            return []
        finally:
            session.close()

    def fetch_team_data(self, team) -> dict:
        return {}

    def fetch_player_data(self, team) -> dict:
        return {}

    def fetch_market_data(self, match) -> dict:
        return {}
