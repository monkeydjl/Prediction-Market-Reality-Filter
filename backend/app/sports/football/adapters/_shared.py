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

    # Situational factors (form / h2h / rest / xG proxy) — best-effort only.
    # Enables multi-factor / Dixon-Coles engines without requiring a second
    # adapter pass. Failures leave fields empty (engines redistribute weights).
    enrich_situational_features(raw, match)
    enrich_referee_features(raw, match)

    # Soft possession/shots proxy (P1-F6) when true stats unavailable:
    # map form share → possession share so multi-factor soft path is non-null.
    try:
        custom = raw.setdefault("custom", {})
        if custom.get("possession_home") is None and custom.get("shots_home") is None:
            fh = raw.get("team", {}).get("form_home")
            fa = raw.get("team", {}).get("form_away")
            if fh is not None and fa is not None:
                fh_f, fa_f = float(fh), float(fa)
                total = fh_f + fa_f
                if total > 0:
                    share = fh_f / total
                    custom["possession_home"] = round(100.0 * share, 1)
                    custom["possession_away"] = round(100.0 * (1.0 - share), 1)
                    custom["possession_proxy"] = "form_share"
    except Exception:  # noqa: BLE001
        logger.debug("possession proxy skipped", exc_info=True)

    enrich_style_features(raw, match)
    enrich_altitude_features(raw, match)

    # P1-F7: coarse national-team travel when both sides resolve (clubs stay empty)
    try:
        from app.sports._shared.team_geo import travel_between_teams

        travel = travel_between_teams(
            match.home.name,
            match.away.name,
            match.season.competition.code or "football",
        )
        if travel.get("travel_known"):
            raw.setdefault("general", {})
            raw["general"]["travel_distance_km"] = travel.get("travel_km_away")
            raw.setdefault("custom", {})
            raw["custom"].update(travel)
    except Exception:  # noqa: BLE001
        logger.debug("football travel enrich skipped", exc_info=True)

    # Prediction-market liquidity + multi-book dispersion (P1-E4 / P1-O2).
    try:
        from app.kernel.market_liquidity import (
            inject_liquidity_into_custom,
            inject_odds_dispersion_from_store,
        )

        custom = inject_liquidity_into_custom(
            raw.get("custom") or {},
            match.match_id,
        )
        raw["custom"] = inject_odds_dispersion_from_store(custom, match.match_id)
    except Exception:  # noqa: BLE001
        logger.debug("liquidity/dispersion enrich skipped", exc_info=True)

    # World Cup group motivation when match_id is a WC fixture (best-effort).
    if match.match_id.startswith("wc-") or match.season.competition.code in {
        "wc", "world_cup",
    }:
        try:
            from app.kernel.engines.group_context_bridge import (
                group_context_to_custom,
                merge_custom,
            )
            from app.models.world_cup_prediction import MatchFixture
            from app.services.world_cup_group_context import build_group_context
            from app.utils.prediction_db import get_prediction_session

            session = None
            try:
                session = get_prediction_session()
                fixture = session.get(MatchFixture, match.match_id)
                if fixture is not None:
                    gc = build_group_context(fixture, session)
                    raw["custom"] = merge_custom(
                        raw.get("custom") or {},
                        group_context_to_custom(gc),
                    )
            finally:
                if session is not None:
                    session.close()
        except Exception:  # noqa: BLE001
            logger.debug("group_context enrich skipped", exc_info=True)

    return raw




def enrich_referee_features(raw: dict, match: MatchIdentity) -> None:
    """Pass-through / soft-fill referee custom fields for multi-factor (P1-F8).

    Sources (first wins for rate/bias):
    1. Already-set ``custom.referee_home_win_rate`` / ``referee_home_bias``
    2. ``environment.referee`` / ``custom.referee_name`` + ``bias_for_referee`` static table

    Never invents rates without a name or explicit numeric field.
    """
    custom = raw.setdefault("custom", {})
    env = raw.get("environment") or {}
    if env.get("referee") and not custom.get("referee_name"):
        custom["referee_name"] = str(env["referee"]).strip()

    if (
        custom.get("referee_home_win_rate") is not None
        or custom.get("referee_home_bias") is not None
    ):
        return

    name = custom.get("referee_name") or env.get("referee")
    if not name:
        return
    custom["referee_name"] = str(name).strip()
    try:
        from app.sports.football.football_referee import bias_for_referee

        b = bias_for_referee(str(name))
    except Exception:  # noqa: BLE001
        logger.debug("referee static bias lookup skipped", exc_info=True)
        return
    if b is None:
        return
    custom["referee_home_bias"] = float(b)
    custom["referee_source"] = "static_map"


def enrich_altitude_features(raw: dict, match: MatchIdentity) -> None:
    """Pass-through altitude, then static fill for home venue when still missing (P1-F7)."""
    try:
        env = raw.setdefault("environment", {})
        custom = raw.setdefault("custom", {})
        alt = (
            custom.get("venue_altitude_m")
            or custom.get("altitude_m")
            or env.get("altitude_m")
            or env.get("venue_altitude_m")
        )
        if alt is not None:
            custom["venue_altitude_m"] = float(alt)
            return
        from app.sports._shared.team_geo import altitude_m_for_team

        home_name = match.home.name if match.home else ""
        static_alt = altitude_m_for_team(home_name)
        if static_alt is not None:
            custom["venue_altitude_m"] = float(static_alt)
            custom["altitude_source"] = "static_table"
    except Exception:  # noqa: BLE001
        logger.debug("altitude enrich skipped", exc_info=True)


def enrich_weather_features(raw: dict, match: MatchIdentity) -> None:
    """Pass-through weather, then static climate fill when still missing (P1-F7)."""
    try:
        env = raw.setdefault("environment", {})
        custom = raw.setdefault("custom", {})
        temp = (
            env.get("weather_temp_c")
            or custom.get("weather_temp_c")
            or env.get("temp_c")
            or custom.get("temp_c")
        )
        cond = (
            env.get("weather_condition")
            or custom.get("weather_condition")
            or env.get("condition")
        )
        if temp is not None or cond is not None:
            if temp is not None:
                env["weather_temp_c"] = float(temp)
                custom.setdefault("weather_temp_c", float(temp))
            if cond is not None:
                env["weather_condition"] = str(cond).strip()
                custom.setdefault("weather_condition", str(cond).strip())
            return

        kickoff = getattr(match, "kickoff_utc", None)
        if kickoff is None:
            return
        month = int(kickoff.month)
        home_name = match.home.name if match.home else ""
        from app.sports.football.football_weather import climate_for_home

        climate = climate_for_home(home_name, month)
        if climate is None:
            return
        env["weather_temp_c"] = float(climate["temp_c"])
        env["weather_condition"] = str(climate["condition"])
        custom["weather_temp_c"] = float(climate["temp_c"])
        custom["weather_condition"] = str(climate["condition"])
        custom["weather_source"] = "static_climate"
    except Exception:  # noqa: BLE001
        logger.debug("weather enrich skipped", exc_info=True)


def enrich_style_features(raw: dict, match: MatchIdentity) -> None:
    """Static possession/shots/PPDA (P1-F6): overwrite form proxy only when both sides resolve."""
    try:
        from app.sports.football.football_style import stats_for_team

        home_name = match.home.name if match.home else ""
        away_name = match.away.name if match.away else ""
        sh = stats_for_team(home_name)
        sa = stats_for_team(away_name)
        if sh is None or sa is None:
            return
        custom = raw.setdefault("custom", {})
        custom["possession_home"] = float(sh["possession_pct"])
        custom["possession_away"] = float(sa["possession_pct"])
        custom["shots_home"] = float(sh["shots_per90"])
        custom["shots_away"] = float(sa["shots_per90"])
        custom["ppda_home"] = float(sh["ppda"])
        custom["ppda_away"] = float(sa["ppda"])
        custom["style_source"] = "static_table"
        custom.pop("possession_proxy", None)
    except Exception:  # noqa: BLE001
        logger.debug("Static style enrichment failed", exc_info=True)


def enrich_situational_features(raw: dict, match: MatchIdentity) -> None:
    """Mutate ``raw`` with form, h2h, rest days, and custom xG proxies.

    Uses international historical CSV when available (best for national teams /
    World Cup). Club competitions may still get partial data when team names
    match the dataset; otherwise fields stay unset.
    """
    before = match.kickoff_utc
    home_name = match.home.name
    away_name = match.away.name
    competition = (match.season.competition.code or "").lower()
    is_world_cup = competition in {"wc", "world_cup"}

    try:
        from app.services.world_cup_historical_results import (
            get_historical_h2h,
            get_historical_team_stats,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Historical results module unavailable", exc_info=True)
        get_historical_team_stats = None  # type: ignore[assignment]
        get_historical_h2h = None  # type: ignore[assignment]

    home_stats = None
    away_stats = None
    if get_historical_team_stats is not None:
        try:
            home_stats = get_historical_team_stats(home_name, before_date=before)
            away_stats = get_historical_team_stats(away_name, before_date=before)
        except Exception:  # noqa: BLE001
            logger.debug("Team stats enrichment failed", exc_info=True)

    # Club competitions: fall back to kernel fixtures when CSV has no team row
    if not is_world_cup and (home_stats is None or away_stats is None):
        try:
            from app.sports.football.club_form import team_form_from_kernel
            if home_stats is None:
                home_stats = team_form_from_kernel(
                    home_name, competition=competition, before=before,
                )
            if away_stats is None:
                away_stats = team_form_from_kernel(
                    away_name, competition=competition, before=before,
                )
        except Exception:  # noqa: BLE001
            logger.debug("Club form enrichment failed", exc_info=True)

    from app.sports.football.club_form import points_form_rate

    if home_stats:
        played = int(home_stats.get("played") or 0)
        wins = int(home_stats.get("wins") or 0)
        draws = int(home_stats.get("draws") or 0)
        form_h = points_form_rate(wins, draws, played)
        if form_h is not None:
            raw["team"]["form_home"] = form_h
        last = home_stats.get("last_match_date")
        rest = _days_since(last, before)
        if rest is not None:
            raw["general"]["rest_days_home"] = rest
            raw["general"]["days_since_last_match"] = rest
        gpg = home_stats.get("goals_per_game")
        if gpg is not None:
            raw.setdefault("custom", {})["xg_home"] = float(gpg)

    if away_stats:
        played = int(away_stats.get("played") or 0)
        wins = int(away_stats.get("wins") or 0)
        draws = int(away_stats.get("draws") or 0)
        form_a = points_form_rate(wins, draws, played)
        if form_a is not None:
            raw["team"]["form_away"] = form_a
        last = away_stats.get("last_match_date")
        rest = _days_since(last, before)
        if rest is not None:
            raw["general"]["rest_days_away"] = rest
        gpg = away_stats.get("goals_per_game")
        if gpg is not None:
            raw.setdefault("custom", {})["xg_away"] = float(gpg)

    # Static xG/90 (P1-F5): overwrite goals proxy only when both sides resolve
    try:
        from app.sports.football.football_xg import xg_for_team

        xh = xg_for_team(home_name)
        xa = xg_for_team(away_name)
        if xh is not None and xa is not None:
            custom = raw.setdefault("custom", {})
            custom["xg_home"] = float(xh)
            custom["xg_away"] = float(xa)
            custom["xg_source"] = "static_table"
    except Exception:  # noqa: BLE001
        logger.debug("Static xG enrichment failed", exc_info=True)

    h2h = None
    if get_historical_h2h is not None:
        try:
            h2h = get_historical_h2h(home_name, away_name, before_date=before)
        except Exception:  # noqa: BLE001
            h2h = None
            logger.debug("H2H enrichment failed", exc_info=True)

    if not h2h:
        try:
            from app.sports.football.club_form import h2h_from_kernel

            h2h = h2h_from_kernel(
                home_name,
                away_name,
                competition=competition if not is_world_cup else None,
                before=before,
            )
        except Exception:  # noqa: BLE001
            h2h = None
            logger.debug("Club H2H enrichment failed", exc_info=True)

    if h2h:
        played = max(int(h2h.get("matches_played") or 0), 1)
        raw["team"]["h2h_home_win_rate"] = round(
            int(h2h.get("home_wins") or 0) / played, 4,
        )
        raw["team"]["h2h_draw_rate"] = round(
            int(h2h.get("draws") or 0) / played, 4,
        )

    # Market value: cache-only Transfermarkt (no scrape on predict path)
    try:
        from app.services.transfermarkt_scraper import get_cached_market_value

        for side, name in (("home", home_name), ("away", away_name)):
            cached = get_cached_market_value(name, ttl_days=14)
            if not cached:
                continue
            total = cached.get("total_market_value")
            if total is None:
                continue
            # Store millions EUR on team layer; multi-factor engine reads it.
            raw["team"][f"market_value_{side}"] = float(total)
            raw.setdefault("custom", {})[f"market_value_{side}"] = float(total)
    except Exception:  # noqa: BLE001
        logger.debug("market value enrich skipped", exc_info=True)

    # P1-F2: schedule density — window counts + congest flags
    try:
        custom = raw.setdefault("custom", {})
        rh = raw.get("general", {}).get("rest_days_home")
        ra = raw.get("general", {}).get("rest_days_away")
        if rh is not None:
            custom["b2b_home"] = float(rh) <= 1.0
        if ra is not None:
            custom["b2b_away"] = float(ra) <= 1.0

        history = _fixture_history_for_density(competition)
        from app.sports._shared.rest_form import matches_in_window_as_of

        if history is not None:
            mh = matches_in_window_as_of(
                home_name,
                before,
                history,
                window_days=7,
                exclude_match_id=match.match_id,
            )
            ma = matches_in_window_as_of(
                away_name,
                before,
                history,
                window_days=7,
                exclude_match_id=match.match_id,
            )
            if mh is not None:
                custom["matches_last_7d_home"] = int(mh)
                custom["schedule_congested_home"] = mh >= 2
            elif rh is not None:
                custom["schedule_congested_home"] = float(rh) <= 2.0
            if ma is not None:
                custom["matches_last_7d_away"] = int(ma)
                custom["schedule_congested_away"] = ma >= 2
            elif ra is not None:
                custom["schedule_congested_away"] = float(ra) <= 2.0
        else:
            if rh is not None:
                custom["schedule_congested_home"] = float(rh) <= 2.0
            if ra is not None:
                custom["schedule_congested_away"] = float(ra) <= 2.0
    except Exception:  # noqa: BLE001
        logger.debug("schedule density flags skipped", exc_info=True)

    # P1-F3: injury impact — static role-weighted Out list, WC fallback
    try:
        from app.sports.football.football_injury import injury_impact_for_team

        inj_h = injury_impact_for_team(home_name)
        inj_a = injury_impact_for_team(away_name)

        wc_lookup = None
        if inj_h is None or inj_a is None:
            try:
                from app.services.world_cup_player_status_source import (
                    get_team_injury_impact,
                )
                wc_lookup = get_team_injury_impact
            except Exception:  # noqa: BLE001
                wc_lookup = None

        if inj_h is None and wc_lookup is not None:
            try:
                inj_h = wc_lookup(home_name)
            except Exception:  # noqa: BLE001
                inj_h = None
        if inj_a is None and wc_lookup is not None:
            try:
                inj_a = wc_lookup(away_name)
            except Exception:  # noqa: BLE001
                inj_a = None

        if inj_h is not None:
            raw["player"]["injury_impact_home"] = float(inj_h)
            raw.setdefault("custom", {})["injury_impact_home"] = float(inj_h)
        if inj_a is not None:
            raw["player"]["injury_impact_away"] = float(inj_a)
            raw.setdefault("custom", {})["injury_impact_away"] = float(inj_a)
    except Exception:  # noqa: BLE001
        logger.debug("injury impact enrich skipped", exc_info=True)

    raw["environment"]["is_home_advantage"] = not is_world_cup
    if is_world_cup:
        raw["environment"]["venue"] = raw["environment"].get("venue") or "neutral"


def _fixture_history_for_density(
    competition: str | None,
) -> list[dict] | None:
    """Load kickoff+teams from kernel fixtures for density counts. None on failure."""
    try:
        from app.kernel.kernel_db import KernelMatchFixture, get_kernel_session

        session = get_kernel_session()
        try:
            q = session.query(KernelMatchFixture)
            if competition:
                q = q.filter(KernelMatchFixture.competition == competition)
            rows = q.all()
            out: list[dict] = []
            for f in rows:
                out.append(
                    {
                        "match_id": f.match_id,
                        "home_team": f.home_team or "",
                        "away_team": f.away_team or "",
                        "kickoff_utc": f.kickoff_utc,
                    }
                )
            return out
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        logger.debug("fixture history for density failed", exc_info=True)
        return None


def _days_since(
    last_match_date: Any,
    kickoff: datetime | None,
) -> float | None:
    if not last_match_date or kickoff is None:
        return None
    try:
        if isinstance(last_match_date, str):
            last = datetime.fromisoformat(last_match_date).date()
        elif hasattr(last_match_date, "date"):
            last = last_match_date.date()  # type: ignore[union-attr]
        else:
            last = last_match_date  # type: ignore[assignment]
        kd = kickoff.date() if isinstance(kickoff, datetime) else kickoff
        delta = (kd - last).days  # type: ignore[operator]
        return float(max(0, min(delta, 60)))
    except Exception:  # noqa: BLE001
        return None


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
