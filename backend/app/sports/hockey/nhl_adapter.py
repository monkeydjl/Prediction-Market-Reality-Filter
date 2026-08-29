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
from app.sports._shared.team_aliases import resolve_team
from app.sports.hockey.nhl_stats_client import (
    fetch_nhl_schedule,
    fetch_nhl_club_stats,
    pick_primary_goalie,
    summarize_club_rates,
    NHLStatsClientError,
)

logger = logging.getLogger(__name__)

_HOCKEY = SportIdentity(code="hockey", name="Hockey")
_NHL = CompetitionIdentity(code="nhl", name="NHL", sport=_HOCKEY)
_DEFAULT_SEASON = "20262027"
_DEFAULT_STAGE = "regular_season"
_DEFAULT_KICKOFF = datetime(2026, 10, 7, tzinfo=timezone.utc)
_FINISHED_STATES = frozenset({"OFF", "FINAL", "OFF FINAL"})

# Franchise renames / bad API concatenations that must collapse to one Elo key.
# Coyotes relocated to Utah (2024) then rebranded Mammoth (2025-26+).
_NHL_TEAM_CANONICAL: dict[str, str] = {
    "Arizona Coyotes": "Utah Mammoth",
    "Utah Utah Hockey Club": "Utah Mammoth",
    "Utah Hockey Club": "Utah Mammoth",
}


def _canonical_nhl_team(name: str) -> str:
    """Collapse NHL franchise renames / duplicate place-name concatenations."""
    cleaned = " ".join((name or "").split())
    if not cleaned:
        return cleaned
    return _NHL_TEAM_CANONICAL.get(cleaned, cleaned)


# Display / canonical names → NHL web API team abbrev (club-stats / roster).
_NHL_TEAM_ABBREV: dict[str, str] = {
    "Anaheim Ducks": "ANA",
    "Arizona Coyotes": "UTA",
    "Boston Bruins": "BOS",
    "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY",
    "Carolina Hurricanes": "CAR",
    "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL",
    "Columbus Blue Jackets": "CBJ",
    "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET",
    "Edmonton Oilers": "EDM",
    "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK",
    "Minnesota Wild": "MIN",
    "Montreal Canadiens": "MTL",
    "Nashville Predators": "NSH",
    "New Jersey Devils": "NJD",
    "New York Islanders": "NYI",
    "New York Rangers": "NYR",
    "Ottawa Senators": "OTT",
    "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT",
    "San Jose Sharks": "SJS",
    "Seattle Kraken": "SEA",
    "St. Louis Blues": "STL",
    "Tampa Bay Lightning": "TBL",
    "Toronto Maple Leafs": "TOR",
    "Utah Hockey Club": "UTA",
    "Utah Mammoth": "UTA",
    "Utah Utah Hockey Club": "UTA",
    "Vancouver Canucks": "VAN",
    "Vegas Golden Knights": "VGK",
    "Washington Capitals": "WSH",
    "Winnipeg Jets": "WPG",
}


def _nhl_team_abbrev(team_name: str) -> str | None:
    """Map display name / alias to NHL club abbrev for Stats API paths."""
    if not team_name:
        return None
    cleaned = _canonical_nhl_team(team_name)
    if cleaned in _NHL_TEAM_ABBREV:
        return _NHL_TEAM_ABBREV[cleaned]
    lower = cleaned.lower()
    for name, ab in _NHL_TEAM_ABBREV.items():
        if name.lower() == lower:
            return ab
    # Alias table snake_case → reverse lookup on known display names.
    canon = resolve_team(cleaned, "nhl")
    if canon:
        for name, ab in _NHL_TEAM_ABBREV.items():
            if resolve_team(name, "nhl") == canon:
                return ab
    # Last resort: 3-letter code already.
    token = cleaned.strip().upper()
    if len(token) == 3 and token.isalpha():
        return token
    return None


def _localized_default(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("default") or "").strip()
    return str(value).strip()


def _team_display_name(side: dict | None) -> str:
    """Build full club name from official nested fields.

    Live api-web.nhle.com uses placeName + commonName (localized dicts).
    Unit fixtures and older payloads may use a flat ``name`` string.
    """
    if not side:
        return ""
    flat = _localized_default(side.get("name"))
    if flat:
        return _canonical_nhl_team(flat)
    place = _localized_default(side.get("placeName"))
    common = _localized_default(side.get("commonName"))
    if place and common:
        # Normalize French accented place names (Montréal → Montreal) so
        # geo/alias tables keyed on ASCII full names still resolve.
        place = place.replace("é", "e").replace("É", "E")
        # Avoid "Utah Utah Hockey Club" when commonName already includes place.
        if common.lower().startswith(place.lower()):
            return _canonical_nhl_team(common)
        return _canonical_nhl_team(f"{place} {common}")
    return _canonical_nhl_team(
        place or common or _localized_default(side.get("abbrev"))
    )


def parse_nhl_game(game_data: dict) -> dict | None:
    """Parse a raw NHL Stats API game dict into internal fixture format.

    Returns None if game_data is malformed. Captures overtime/shootout
    flags in the parsed dict (returned under ``went_to_overtime`` /
    ``went_to_shootout`` keys; the adapter writes them into raw['custom']).
    """
    game_id = game_data.get("id")
    if not game_id:
        return None

    home_side = game_data.get("homeTeam") or {}
    away_side = game_data.get("awayTeam") or {}
    home_team = _team_display_name(home_side if isinstance(home_side, dict) else None)
    away_team = _team_display_name(away_side if isinstance(away_side, dict) else None)
    if not home_team or not away_team:
        return None

    date_str = (
        game_data.get("startTimeUTC")
        or game_data.get("gameDate")
        or ""
    )
    try:
        kickoff_utc = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        kickoff_utc = _DEFAULT_KICKOFF

    # gameType: 1 = preseason, 2 = regular season, 3 = playoffs
    game_type = game_data.get("gameType", 2)
    stage = "playoff" if game_type == 3 else "regular_season"

    # gameState: "OFF", "FINAL", "OFF FINAL", "LIVE", "FUT", etc.
    game_state = str(game_data.get("gameState") or "").upper()
    status = "finished" if game_state in _FINISHED_STATES else "scheduled"

    home_score = None
    away_score = None
    if isinstance(home_side, dict):
        home_score = home_side.get("score")
    if isinstance(away_side, dict):
        away_score = away_side.get("score")
    if home_score is None:
        home_score = game_data.get("homeTeamScore")
    if away_score is None:
        away_score = game_data.get("awayTeamScore")

    period_desc = game_data.get("periodDescriptor") or {}
    period_type = str(
        period_desc.get("periodType")
        or (game_data.get("gameOutcome") or {}).get("lastPeriodType")
        or ""
    ).upper()
    period = period_desc.get("number")
    if period is None:
        period = game_data.get("period", 3)
    try:
        period_n = int(period)
    except (TypeError, ValueError):
        period_n = 3
    went_to_overtime = period_type == "OT" or period_n == 4
    went_to_shootout = period_type == "SO" or period_n == 5

    venue_raw = game_data.get("venue") or {}
    if isinstance(venue_raw, dict):
        venue = _localized_default(venue_raw) or venue_raw.get("default") or "Unknown"
    else:
        venue = str(venue_raw or "Unknown")

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
    """Upsert a parsed NHL fixture into kernel_match_fixtures (+ result when scored)."""
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
            is_stub=True,
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

    def _fetch_club_side(self, team_name: str) -> dict:
        """One club-stats fetch → primary goalie + per-game rates (graceful empty)."""
        abbrev = _nhl_team_abbrev(team_name)
        if not abbrev:
            logger.debug("NHL club-stats: no abbrev for team=%r", team_name)
            return {}
        try:
            stats = fetch_nhl_club_stats(abbrev)
        except NHLStatsClientError as exc:
            logger.warning("NHL club-stats failed for %s: %s", abbrev, exc)
            return {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("NHL club-stats error for %s: %s", abbrev, exc)
            return {}
        out: dict = {}
        picked = pick_primary_goalie(stats)
        if picked:
            out["name"] = picked["name"]
            out["save_pct"] = picked["save_pct"]
        rates = summarize_club_rates(stats)
        if rates:
            out["rates"] = rates
        return out

    def _goalie_for_team(self, team_name: str) -> dict:
        """Resolve primary starter save% via club-stats (graceful empty)."""
        side = self._fetch_club_side(team_name)
        if not side:
            return {}
        out: dict = {}
        if side.get("name") is not None:
            out["name"] = side["name"]
        if side.get("save_pct") is not None:
            out["save_pct"] = side["save_pct"]
        return out

    def _fetch_starting_goalies(self, match: MatchIdentity) -> dict:
        """Fetch primary goalie save% for both teams from NHL club-stats.

        Returns dict with 'home' and 'away' keys, each containing
        {'name': str, 'save_pct': float} or empty dict if unavailable.
        Uses season workhorse (most gamesStarted), not confirmed lineup.
        """
        return {
            "home": self._goalie_for_team(match.home.name),
            "away": self._goalie_for_team(match.away.name),
        }

    @staticmethod
    def _attack_from_side(
        side: dict,
        form: float | None,
    ) -> tuple[float, float, float | None, float | None]:
        """Return (gf, ga, xg_for, shot_share) preferring club-stats rates.

        Falls back to form-shaped soft GF/GA when rates missing (P1-H1).
        ``shot_share`` is 0-1 corsi-like proxy or None.
        """
        rates = side.get("rates") if isinstance(side, dict) else None
        if isinstance(rates, dict) and rates.get("gf_per_game") is not None:
            gf = float(rates["gf_per_game"])
            ga = float(rates.get("ga_per_game") or 0.0)
            # Shots-for as soft xG scale (not true xG; better than form GF alone).
            sf = rates.get("sf_per_game")
            xg = float(sf) * 0.09 if sf is not None else gf
            share = rates.get("shot_share")
            shot_share = float(share) if share is not None else None
            return gf, ga, xg, shot_share
        form_v = form if form is not None else 0.5
        gf = round(2.9 + (float(form_v) - 0.5) * 1.2, 3)
        ga = round(3.1 - (float(form_v) - 0.5) * 0.8, 3)
        return gf, ga, gf, None

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        """Fetch all raw data for an NHL match.

        All data comes from local DB (Elo, form, rest) and the NHL Stats
        API (goalie save% + club rates). Goalie/attack stats and overtime
        flags are written to raw['custom'].
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

        home_side = self._fetch_club_side(home_name)
        away_side = self._fetch_club_side(away_name)
        home_g = {
            k: home_side[k]
            for k in ("name", "save_pct")
            if k in home_side
        }
        away_g = {
            k: away_side[k]
            for k in ("name", "save_pct")
            if k in away_side
        }

        gf_home, ga_home, xg_home, share_home = self._attack_from_side(
            home_side, form_home
        )
        gf_away, ga_away, xg_away, share_away = self._attack_from_side(
            away_side, form_away
        )

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
                "goalie_save_pct_home": home_g.get("save_pct"),
                "goalie_save_pct_away": away_g.get("save_pct"),
                "team_gf_home": gf_home,
                "team_gf_away": gf_away,
                "team_ga_home": ga_home,
                "team_ga_away": ga_away,
                "xg_for_home": xg_home,
                "xg_for_away": xg_away,
                "corsi_pct_home": share_home,
                "corsi_pct_away": share_away,
                "pdo_home": None,
                "pdo_away": None,
                "went_to_overtime": False,
                "went_to_shootout": False,
                "b2b_home": rest_home is not None and float(rest_home) <= 1.0,
                "b2b_away": rest_away is not None and float(rest_away) <= 1.0,
            },
        }
        # True 5v5 shot quality (P1-H1) — measured xG/corsi ahead of the
        # club-stats proxies. Each metric pair must come from one source: the
        # engine consumes a home-vs-away share, so pairing a measured 5v5 rate
        # against a shots-on-goal proxy would manufacture a spurious edge.
        skating_source = (
            "club_stats_proxy"
            if home_side.get("rates") and away_side.get("rates")
            else "soft_form"
        )
        try:
            from app.services.nhl_live_xg_service import get_live_5v5_metrics

            season_key = match.season.season_key
            live_home = get_live_5v5_metrics(season_key, home_name)
            live_away = get_live_5v5_metrics(season_key, away_name)
            metrics_home = live_home.metrics if live_home.available else None
            metrics_away = live_away.metrics if live_away.available else None
        except Exception:  # noqa: BLE001 — keep the club-stats proxies usable
            logger.debug("NHL live 5v5 enrich unavailable", exc_info=True)
            metrics_home = metrics_away = None
        if metrics_home and metrics_away:
            live_corsi = (
                metrics_home.get("corsi_pct") is not None
                and metrics_away.get("corsi_pct") is not None
            )
            live_xg = (
                metrics_home.get("xgf_per_60") is not None
                and metrics_away.get("xgf_per_60") is not None
            )
            if live_corsi:
                raw["custom"]["corsi_pct_home"] = float(metrics_home["corsi_pct"])
                raw["custom"]["corsi_pct_away"] = float(metrics_away["corsi_pct"])
            if live_xg:
                raw["custom"]["xg_for_home"] = float(metrics_home["xgf_per_60"])
                raw["custom"]["xg_for_away"] = float(metrics_away["xgf_per_60"])
                if not live_corsi:
                    # HockeyEngine prefers corsi over xG, so the shots-on-goal
                    # proxy would shadow the measured xG. Drop it rather than let
                    # a proxy outrank real data.
                    raw["custom"]["corsi_pct_home"] = None
                    raw["custom"]["corsi_pct_away"] = None
            if live_corsi or live_xg:
                skating_source = "live_provider"
        raw["custom"]["skating_source"] = skating_source
        try:
            from app.sports._shared.team_geo import travel_between_teams

            travel = travel_between_teams(home_name, away_name, "nhl")
            raw["custom"].update(travel)
            if travel.get("travel_km_away") is not None:
                raw["general"]["travel_distance_km"] = travel["travel_km_away"]
        except Exception:  # noqa: BLE001
            logger.debug("NHL travel enrich skipped", exc_info=True)
        try:
            from app.kernel.market_liquidity import inject_liquidity_into_custom

            raw["custom"] = inject_liquidity_into_custom(
                raw.get("custom") or {},
                match.match_id,
            )
        except Exception:  # noqa: BLE001
            logger.debug("NHL liquidity enrich skipped", exc_info=True)
        # Real market over/under line (P1-O1). Default-off; absent it the engine
        # keeps quoting against the league average, which equals its own expected
        # total by construction.
        from app.services.market_totals_service import inject_market_total_into_custom

        raw["custom"] = inject_market_total_into_custom(
            raw.get("custom") or {},
            sport="hockey",
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
                    KernelMatchFixture.competition == "nhl",
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
                    KernelMatchFixture.competition == "nhl",
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

    def _season_candidates(self) -> list[str]:
        """Preferred then fallback season keys (YYYYYYYY).

        Prefers the campaign that opens this calendar year (e.g. 20262027
        in mid-2026), then falls back one season if empty.
        """
        now = datetime.now(timezone.utc)
        y = now.year
        preferred = f"{y}{y + 1}"
        fallback = f"{y - 1}{y}"
        if preferred == fallback:
            return [preferred]
        return [preferred, fallback]

    def sync_schedule(self) -> int:
        """Sync NHL schedule from the NHL Stats API.

        Returns 0 if PHASE5_NHL_ENABLED is false or sync fails (graceful
        degradation, no exceptions). NHL season spans two calendar years
        (e.g., "20252026" for the 2025-26 season). Prefers the upcoming
        campaign when published; falls back one season if empty.
        """
        if not config.settings.PHASE5_NHL_ENABLED:
            return 0
        try:
            count = 0
            used_season: str | None = None
            for season in self._season_candidates():
                games_raw = fetch_nhl_schedule(season)
                if not games_raw:
                    logger.info("NHL schedule empty for season=%s; trying next", season)
                    continue
                used_season = season
                for raw in games_raw:
                    parsed = parse_nhl_game(raw)
                    if parsed:
                        game_season = raw.get("season")
                        season_key = (
                            str(game_season) if game_season is not None else season
                        )
                        save_fixture(parsed, "nhl", season_key)
                        count += 1
                break
            if used_season is not None:
                logger.info(
                    "NHL sync_schedule season=%s fixtures=%s",
                    used_season,
                    count,
                )
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

    def fetch_team_data(self, team: TeamIdentity) -> dict:
        return {}

    def fetch_player_data(self, team: TeamIdentity) -> dict:
        return {}

    def fetch_market_data(self, match: MatchIdentity) -> dict:
        return {}
