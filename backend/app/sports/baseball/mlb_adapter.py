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
from app.sports.baseball.mlb_stats_client import (
    extract_probable_pitchers,
    extract_probable_pitchers_from_schedule_game,
    fetch_mlb_game_feed,
    fetch_mlb_pitcher,
    fetch_mlb_schedule,
    fetch_mlb_team_hitting_platoon_splits,
    fetch_mlb_team_pitcher_stats,
    fetch_mlb_team_pitching_totals,
    parse_mlb_weather,
    parse_pitcher_person,
    platoon_advantage_home,
    platoon_ops_vs_hand,
    summarize_bullpen_era,
    summarize_team_era,
    MLBStatsClientError,
)

logger = logging.getLogger(__name__)

_BASEBALL = SportIdentity(code="baseball", name="Baseball")
_MLB = CompetitionIdentity(code="mlb", name="MLB", sport=_BASEBALL)
_DEFAULT_SEASON = "2026"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2026, 7, 4, tzinfo=timezone.utc)
_LEAGUE_AVG_ERA = 4.10

# Official MLB Stats API team IDs (sportId=1).
_MLB_TEAM_IDS: dict[str, int] = {
    "Arizona Diamondbacks": 109,
    "Athletics": 133,
    "Atlanta Braves": 144,
    "Baltimore Orioles": 110,
    "Boston Red Sox": 111,
    "Chicago Cubs": 112,
    "Chicago White Sox": 145,
    "Cincinnati Reds": 113,
    "Cleveland Guardians": 114,
    "Colorado Rockies": 115,
    "Detroit Tigers": 116,
    "Houston Astros": 117,
    "Kansas City Royals": 118,
    "Los Angeles Angels": 108,
    "Los Angeles Dodgers": 119,
    "Miami Marlins": 146,
    "Milwaukee Brewers": 158,
    "Minnesota Twins": 142,
    "New York Mets": 121,
    "New York Yankees": 147,
    "Oakland Athletics": 133,
    "Philadelphia Phillies": 143,
    "Pittsburgh Pirates": 134,
    "San Diego Padres": 135,
    "San Francisco Giants": 137,
    "Seattle Mariners": 136,
    "St. Louis Cardinals": 138,
    "Tampa Bay Rays": 139,
    "Texas Rangers": 140,
    "Toronto Blue Jays": 141,
    "Washington Nationals": 120,
}

# Static multi-year-ish park run factors (1.0 = league average). Soft signal only.
# Expanded to all 30 franchises (P1-M2). Alias keys mirror _MLB_TEAM_IDS dual names.
_PARK_FACTORS: dict[str, float] = {
    "Arizona Diamondbacks": 1.02,
    "Athletics": 0.97,
    "Atlanta Braves": 1.01,
    "Baltimore Orioles": 1.01,
    "Boston Red Sox": 1.06,
    "Chicago Cubs": 1.02,
    "Chicago White Sox": 1.00,
    "Cincinnati Reds": 1.05,
    "Cleveland Guardians": 0.99,
    "Colorado Rockies": 1.15,
    "Detroit Tigers": 0.98,
    "Houston Astros": 0.99,
    "Kansas City Royals": 1.01,
    "Los Angeles Angels": 1.00,
    "Los Angeles Dodgers": 0.98,
    "Miami Marlins": 0.93,
    "Milwaukee Brewers": 1.01,
    "Minnesota Twins": 1.01,
    "New York Mets": 0.97,
    "New York Yankees": 1.01,
    "Oakland Athletics": 0.97,
    "Philadelphia Phillies": 1.03,
    "Pittsburgh Pirates": 0.98,
    "San Diego Padres": 0.96,
    "San Francisco Giants": 0.94,
    "Seattle Mariners": 0.94,
    "St. Louis Cardinals": 0.97,
    "Tampa Bay Rays": 0.96,
    "Texas Rangers": 1.04,
    "Toronto Blue Jays": 1.02,
    "Washington Nationals": 1.00,
}


def _park_factor_for_team(home_team_name: str) -> float:
    """Return park run factor for home team; default 1.0 (neutral)."""
    if not home_team_name:
        return 1.0
    if home_team_name in _PARK_FACTORS:
        return _PARK_FACTORS[home_team_name]
    # Fuzzy tail match (e.g. "Rockies")
    lower = home_team_name.lower()
    for name, factor in _PARK_FACTORS.items():
        if name.lower() in lower or lower in name.lower():
            return factor
    return 1.0


# Competitive MLB game types for Elo / backtest (exclude spring/ASG/exhibition).
_MLB_REGULAR_TYPES = {"R"}
_MLB_PLAYOFF_TYPES = {"D", "L", "F", "W", "P"}  # division / LCS / WS / playoff-ish
_MLB_COMPETITIVE_TYPES = _MLB_REGULAR_TYPES | _MLB_PLAYOFF_TYPES

# Franchise renames that must collapse to one Elo key across seasons.
_MLB_TEAM_CANONICAL: dict[str, str] = {
    "Oakland Athletics": "Athletics",
}


def _canonical_mlb_team(name: str) -> str:
    return _MLB_TEAM_CANONICAL.get(name, name)


def _team_id_for_name(team_name: str) -> int | None:
    """Map franchise name to MLB Stats API team id."""
    if not team_name:
        return None
    canon = _canonical_mlb_team(team_name)
    if canon in _MLB_TEAM_IDS:
        return _MLB_TEAM_IDS[canon]
    if team_name in _MLB_TEAM_IDS:
        return _MLB_TEAM_IDS[team_name]
    lower = canon.lower()
    for name, tid in _MLB_TEAM_IDS.items():
        if name.lower() in lower or lower in name.lower():
            return tid
    return None


def _game_pk_from_match_id(match_id: str) -> int | None:
    """Parse ``mlb-{gamePk}`` into int gamePk."""
    if not match_id:
        return None
    text = match_id.strip()
    if text.lower().startswith("mlb-"):
        text = text[4:]
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _season_year_from_match(match: MatchIdentity) -> int:
    """Best-effort season year for stats endpoints."""
    key = getattr(getattr(match, "season", None), "season_key", None) or _DEFAULT_SEASON
    try:
        return int(str(key)[:4])
    except (TypeError, ValueError):
        return int(_DEFAULT_SEASON)


def parse_mlb_game(game_data: dict) -> dict | None:
    """Parse a raw MLB Stats API game dict into internal fixture format.

    Returns None if game_data is malformed or non-competitive (spring training,
    All-Star, exhibition, international friendlies, etc.).
    """
    game_pk = game_data.get("gamePk")
    if not game_pk:
        return None

    game_type = (game_data.get("gameType") or "").strip().upper()
    # When gameType is present, only keep regular season + postseason.
    # Missing gameType keeps prior behavior for unit fixtures / partial feeds.
    if game_type and game_type not in _MLB_COMPETITIVE_TYPES:
        return None

    # Official schedule payload nests name under teams.{home,away}.team.name
    # (not teams.home.name). Fall back to flat shape for older fixtures/tests.
    home_side = game_data.get("teams", {}).get("home") or {}
    away_side = game_data.get("teams", {}).get("away") or {}
    home_team = _canonical_mlb_team(
        (home_side.get("team") or {}).get("name")
        or home_side.get("name")
        or ""
    )
    away_team = _canonical_mlb_team(
        (away_side.get("team") or {}).get("name")
        or away_side.get("name")
        or ""
    )
    if not home_team or not away_team:
        return None

    date_str = game_data.get("gameDate", "")
    try:
        kickoff_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        kickoff_utc = _DEFAULT_KICKOFF

    # Postseason: seriesDescription and/or gameType codes (D/L/F/W).
    # Do not treat any non-empty seriesDescription alone as playoff — regular
    # season also has series numbers; rely on gameType first.
    series_desc = (game_data.get("seriesDescription") or "").lower()
    is_playoff = game_type in _MLB_PLAYOFF_TYPES or any(
        tok in series_desc
        for tok in ("wild card", "division series", "championship", "world series")
    )
    stage = "playoff" if is_playoff else "regular_season"

    status_raw = (game_data.get("status") or {}).get("abstractGameState", "")
    status = "finished" if status_raw == "Final" else "scheduled"

    # Scores: schedule feed uses teams.{side}.score; live feed uses linescore.
    linescore = game_data.get("linescore") or {}
    home_score = home_side.get("score")
    if home_score is None:
        home_score = (linescore.get("teams") or {}).get("home", {}).get("runs")
        if home_score is None:
            home_score = (linescore.get("home") or {}).get("runs")
    away_score = away_side.get("score")
    if away_score is None:
        away_score = (linescore.get("teams") or {}).get("away", {}).get("runs")
        if away_score is None:
            away_score = (linescore.get("away") or {}).get("runs")

    venue = (game_data.get("venue") or {}).get("name", "Unknown")

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
    """Upsert a parsed MLB fixture into kernel_match_fixtures (+ result when scored)."""
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

    def _fetch_game_context(self, match: MatchIdentity) -> dict:
        """Fetch game feed once for probable pitchers + weather + venue.

        Returns
        ``{"probable": {home,away}, "weather": dict|None, "venue": str|None}``.
        On total failure returns empty probable and None weather.
        """
        empty_probable: dict[str, dict] = {"home": {}, "away": {}}
        game_pk = _game_pk_from_match_id(match.match_id)
        if game_pk is None:
            return {"probable": empty_probable, "weather": None, "venue": None}

        feed: dict | None = None
        try:
            feed = fetch_mlb_game_feed(game_pk)
        except MLBStatsClientError as exc:
            logger.debug("MLB game feed failed for %s: %s", match.match_id, exc)
        except Exception:  # noqa: BLE001
            logger.debug("MLB game feed skipped for %s", match.match_id, exc_info=True)

        probable = extract_probable_pitchers(feed) if feed else empty_probable
        weather = parse_mlb_weather(feed) if feed else None
        venue = (weather or {}).get("venue") if weather else None

        if not (probable.get("home") or probable.get("away")):
            try:
                kickoff = match.kickoff_utc or _DEFAULT_KICKOFF
                day = kickoff.date().isoformat()
                games = fetch_mlb_schedule(day, day, hydrate="probablePitcher")
                for game in games:
                    if int(game.get("gamePk") or 0) != game_pk:
                        continue
                    probable = extract_probable_pitchers_from_schedule_game(game)
                    break
            except MLBStatsClientError as exc:
                logger.debug("MLB schedule probable pitchers failed: %s", exc)
            except Exception:  # noqa: BLE001
                logger.debug("MLB schedule probable pitchers skipped", exc_info=True)

        return {
            "probable": probable or empty_probable,
            "weather": weather,
            "venue": venue,
        }

    def _resolve_probable_pitcher_ids(self, match: MatchIdentity) -> dict[str, dict]:
        """Resolve home/away probable pitchers via feed, then schedule hydrate."""
        ctx = self._fetch_game_context(match)
        return ctx.get("probable") or {"home": {}, "away": {}}

    def _pitcher_stats_for_person(
        self,
        person_id: int | None,
        fallback_name: str | None = None,
    ) -> dict:
        """Fetch season ERA/WHIP for one pitcher; empty dict on failure."""
        if person_id is None:
            if fallback_name:
                return {"name": fallback_name}
            return {}
        try:
            payload = fetch_mlb_pitcher(int(person_id))
            parsed = parse_pitcher_person(payload)
            if not parsed:
                return {"name": fallback_name} if fallback_name else {}
            if not parsed.get("name") and fallback_name:
                parsed["name"] = fallback_name
            return {
                "name": parsed.get("name"),
                "era": parsed.get("era"),
                "whip": parsed.get("whip"),
                "pitch_hand": parsed.get("pitch_hand"),
                "person_id": parsed.get("person_id") or int(person_id),
            }
        except MLBStatsClientError as exc:
            logger.debug("MLB pitcher %s fetch failed: %s", person_id, exc)
        except Exception:  # noqa: BLE001
            logger.debug("MLB pitcher %s fetch skipped", person_id, exc_info=True)
        return {"name": fallback_name} if fallback_name else {}

    def _fetch_starting_pitchers(
        self,
        match: MatchIdentity,
        probable: dict | None = None,
    ) -> dict:
        """Fetch starting pitcher ERA/WHIP for both teams.

        Uses probable pitcher IDs from the v1.1 game feed (schedule hydrate
        fallback), then ``/people/{id}`` season pitching stats.

        Returns dict with 'home' and 'away' keys, each containing
        {'name': str, 'era': float, 'whip': float, 'pitch_hand': str} or empty
        dict if unavailable (graceful degradation).
        """
        if probable is None:
            probable = self._resolve_probable_pitcher_ids(match)
        out: dict[str, dict] = {"home": {}, "away": {}}
        for side in ("home", "away"):
            row = (probable or {}).get(side) or {}
            pid = row.get("id")
            name = row.get("name")
            out[side] = self._pitcher_stats_for_person(pid, fallback_name=name)
        return out

    def _fetch_platoon_ops(
        self,
        team_name: str,
        season: int,
        opposing_pitch_hand: str | None,
    ) -> float | None:
        """Team season OPS vs the opposing starter's hand (L/R)."""
        team_id = _team_id_for_name(team_name)
        if team_id is None or not opposing_pitch_hand:
            return None
        try:
            splits = fetch_mlb_team_hitting_platoon_splits(team_id, season)
            return platoon_ops_vs_hand(
                splits.get("ops_vs_l"),
                splits.get("ops_vs_r"),
                opposing_pitch_hand,
            )
        except MLBStatsClientError as exc:
            logger.debug("MLB platoon splits %s failed: %s", team_name, exc)
        except Exception:  # noqa: BLE001
            logger.debug("MLB platoon splits %s skipped", team_name, exc_info=True)
        return None

    def _fetch_team_pitching_side(self, team_name: str, season: int) -> dict:
        """Fetch team ERA + bullpen ERA for one franchise.

        Returns ``{"team_era": float|None, "bullpen_era": float|None}``.
        """
        team_id = _team_id_for_name(team_name)
        if team_id is None:
            return {"team_era": None, "bullpen_era": None}

        team_era: float | None = None
        bullpen_era: float | None = None
        try:
            totals = fetch_mlb_team_pitching_totals(team_id, season)
            team_era = summarize_team_era(totals)
        except MLBStatsClientError as exc:
            logger.debug("MLB team pitching totals %s failed: %s", team_name, exc)
        except Exception:  # noqa: BLE001
            logger.debug("MLB team pitching totals %s skipped", team_name, exc_info=True)

        try:
            splits = fetch_mlb_team_pitcher_stats(team_id, season)
            bullpen_era = summarize_bullpen_era(splits)
        except MLBStatsClientError as exc:
            logger.debug("MLB bullpen splits %s failed: %s", team_name, exc)
        except Exception:  # noqa: BLE001
            logger.debug("MLB bullpen splits %s skipped", team_name, exc_info=True)

        if bullpen_era is None and team_era is not None:
            bullpen_era = team_era
        return {"team_era": team_era, "bullpen_era": bullpen_era}

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        """Fetch all raw data for an MLB match.

        Local DB supplies Elo/form/rest; MLB Stats API supplies probable
        starter ERA/WHIP, team ERA, relief-only bullpen ERA, and outdoor weather.
        """
        home_name = match.home.name
        away_name = match.away.name
        season = _season_year_from_match(match)

        elo_ratings = self._fetch_elo_ratings(home_name, away_name)
        elo_home = elo_ratings.get(home_name)
        elo_away = elo_ratings.get(away_name)

        form_home = self._compute_form(home_name)
        form_away = self._compute_form(away_name)

        rest_home = self._compute_rest_days(home_name, match.kickoff_utc)
        rest_away = self._compute_rest_days(away_name, match.kickoff_utc)

        game_ctx = self._fetch_game_context(match)
        pitchers = self._fetch_starting_pitchers(
            match,
            probable=game_ctx.get("probable"),
        )
        home_p = pitchers.get("home", {})
        away_p = pitchers.get("away", {})
        weather = game_ctx.get("weather") or {}
        venue_name = game_ctx.get("venue") or weather.get("venue") or "Home Ballpark"
        roof_type = weather.get("roof_type")
        outdoor = True
        if isinstance(roof_type, str):
            rt = roof_type.strip().lower()
            outdoor = rt in {"", "open", "none"} or "open" in rt
        weather_temp_c = weather.get("temp_c") if outdoor else None
        weather_temp_f = weather.get("temp_f") if outdoor else None
        weather_wind_mph = weather.get("wind_mph") if outdoor else None
        weather_condition = weather.get("condition")

        home_side = self._fetch_team_pitching_side(home_name, season)
        away_side = self._fetch_team_pitching_side(away_name, season)
        team_era_home = home_side.get("team_era")
        team_era_away = away_side.get("team_era")
        bullpen_home = home_side.get("bullpen_era")
        bullpen_away = away_side.get("bullpen_era")
        if bullpen_home is None:
            bullpen_home = team_era_home if team_era_home is not None else _LEAGUE_AVG_ERA
        if bullpen_away is None:
            bullpen_away = team_era_away if team_era_away is not None else _LEAGUE_AVG_ERA

        # P1-M4: offense OPS vs opposing starter hand (team season splits).
        # Home hitters face away SP; away hitters face home SP.
        platoon_ops_home = self._fetch_platoon_ops(
            home_name, season, away_p.get("pitch_hand"),
        )
        platoon_ops_away = self._fetch_platoon_ops(
            away_name, season, home_p.get("pitch_hand"),
        )
        platoon_adv = platoon_advantage_home(platoon_ops_home, platoon_ops_away)

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
                "venue": venue_name,
                "is_home_advantage": True,
                "weather_temp_c": weather_temp_c,
                "weather_condition": weather_condition,
            },
            "custom": {
                "pitcher_era_home": home_p.get("era"),
                "pitcher_era_away": away_p.get("era"),
                "pitcher_whip_home": home_p.get("whip"),
                "pitcher_whip_away": away_p.get("whip"),
                "pitcher_hand_home": home_p.get("pitch_hand"),
                "pitcher_hand_away": away_p.get("pitch_hand"),
                "team_batting_avg_home": 0.250,
                "team_batting_avg_away": 0.250,
                "team_era_home": team_era_home if team_era_home is not None else _LEAGUE_AVG_ERA,
                "team_era_away": team_era_away if team_era_away is not None else _LEAGUE_AVG_ERA,
                "pythagorean_win_pct_home": 0.500,
                "pythagorean_win_pct_away": 0.500,
                # P1-M2: coarse park factors (1.0 = league average runs)
                "park_factor": _park_factor_for_team(home_name),
                # P1-M1: relief-only IP-weighted ERA (team ERA fallback)
                "bullpen_era_home": float(bullpen_home),
                "bullpen_era_away": float(bullpen_away),
                # P1-M3: outdoor weather from game feed (F→C, wind mph)
                "weather_temp_c": weather_temp_c,
                "weather_temp_f": weather_temp_f,
                "weather_wind_mph": weather_wind_mph,
                "weather_condition": weather_condition,
                "roof_type": roof_type,
                # P1-M4: team OPS vs opposing starter hand
                "platoon_ops_home": platoon_ops_home,
                "platoon_ops_away": platoon_ops_away,
                "platoon_advantage_home": platoon_adv,
            },
        }
        try:
            from app.sports._shared.team_geo import travel_between_teams

            travel = travel_between_teams(home_name, away_name, "mlb")
            raw["custom"].update(travel)
            if travel.get("travel_km_away") is not None:
                raw["general"]["travel_distance_km"] = travel["travel_km_away"]
        except Exception:  # noqa: BLE001
            logger.debug("MLB travel enrich skipped", exc_info=True)
        try:
            from app.kernel.market_liquidity import inject_liquidity_into_custom

            raw["custom"] = inject_liquidity_into_custom(
                raw.get("custom") or {},
                match.match_id,
            )
        except Exception:  # noqa: BLE001
            logger.debug("MLB liquidity enrich skipped", exc_info=True)
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
                    KernelMatchFixture.competition == "mlb",
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
                    KernelMatchFixture.competition == "mlb",
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
