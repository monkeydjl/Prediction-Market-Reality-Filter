# backend/app/sports/football/adapters/_shared.py
"""Shared utility functions for football adapters.

Pure functions — no class, no module-level mutable state.
Each adapter calls these freely (composition over inheritance).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from app.kernel.domain import (
    CompetitionIdentity, SeasonIdentity, TeamIdentity,
    MatchIdentity, MatchOutcome,
)
from app.kernel.feature_provenance import XG_SOURCE_GOALS_PROXY

# Imported at module level (rather than lazily inside fetch_team_elo) so that
# unit tests can patch it via
#   @patch("app.sports.football.adapters._shared.get_club_elo")
# and so fetch_team_elo calls the patched name. get_elo_rating / get_cached_odds
# stay as lazy in-function imports (the former is never patched in tests; the
# latter is patched at its source module, which a lazy import still honors).
from app.services.club_elo_service import get_club_elo

logger = logging.getLogger(__name__)

# Widest schedule-density window, so one international lookup serves every
# window the enrichment reports.
_INTL_DENSITY_WINDOW_DAYS = 7


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
    async def _gather_all() -> list[Any]:
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

    # Provenance, in the "<home>/<away>" form all_sources_look_real() splits on.
    # Without it the kernel cannot tell a measured rating from the 1500.0 that
    # get_elo_rating returns for an unknown team; the odds two lines below have
    # carried odds_source since P1-E4. "unknown" is a non-real token, so a failed
    # fetch on either side correctly invalidates the pair.
    raw["team"]["elo_source"] = "{}/{}".format(
        elo_home_raw.get("source", "unknown") if isinstance(elo_home_raw, dict) else "unknown",
        elo_away_raw.get("source", "unknown") if isinstance(elo_away_raw, dict) else "unknown",
    )

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

    # No possession proxy here (P1-F6). Form share used to be written under the
    # possession keys so the multi-factor soft path would be non-null, but the
    # engine's form factor reads the same two numbers (feature_builder passes
    # team_raw["form_home"] straight through). Filling possession from form made
    # one piece of evidence vote twice under two names: it took form's intended
    # influence from 0.145 to 0.197 of the available weight (1.357x), counted as
    # an extra available factor in ``data_completeness``, and cast a vote that
    # agreed with form by construction in ``factor_agreement`` -- together worth
    # +1.22pp to +2.03pp of confidence depending on how many other factors the
    # local database resolves. The affected tracks are the callers of this
    # function -- epl, ucl, laliga, bundesliga, seriea, ligue1 -- not the World
    # Cup, which builds its own raw dict in WorldCupAdapter.fetch_all_data and
    # never reaches any enricher here. Absent real possession the engine already
    # marks the factor unavailable and redistributes its weight, which is the
    # honest answer and the documented path for a missing factor.
    enrich_style_features(raw, match)
    enrich_altitude_features(raw, match)
    enrich_weather_features(raw, match)

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

    # Real market over/under line (P1-O1). Default-off; absent it the engine
    # keeps quoting its soft O/U against the hardcoded 2.5 placeholder.
    from app.services.market_totals_service import inject_market_total_into_custom

    raw["custom"] = inject_market_total_into_custom(
        raw.get("custom") or {},
        sport="football",
        kickoff_utc=match.kickoff_utc,
        home_name=match.home.name,
        away_name=match.away.name,
    )

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
    """Pass through explicit referee values, then use live or static statistics."""
    custom = raw.setdefault("custom", {})
    env = raw.get("environment") or {}
    env_referee = str(env.get("referee") or "").strip()
    if env_referee and not custom.get("referee_name"):
        custom["referee_name"] = env_referee

    if (
        custom.get("referee_home_win_rate") is not None
        or custom.get("referee_home_bias") is not None
    ):
        return

    name = (custom.get("referee_name") or "").strip() or env_referee
    if not name:
        return
    custom["referee_name"] = name
    competition = (match.season.competition.code or "").lower()
    if competition not in {"wc", "world_cup"}:
        try:
            from app.services.football_live_referee_service import get_live_referee

            live = get_live_referee(competition, match.season.season_key, str(name))
            rate = live.home_win_rate
            if live.available and rate is not None:
                custom["referee_home_win_rate"] = float(rate)
                custom["referee_home_bias"] = round(2.0 * float(rate) - 1.0, 4)
                custom["referee_source"] = "live_provider"
                return
        except Exception:  # noqa: BLE001
            logger.debug("Live referee enrichment unavailable", exc_info=True)

    try:
        from app.sports.football.football_referee import bias_for_referee

        bias = bias_for_referee(str(name))
    except Exception:  # noqa: BLE001
        logger.debug("referee static bias lookup skipped", exc_info=True)
        return
    if bias is None:
        return
    custom["referee_home_bias"] = float(bias)
    custom["referee_source"] = "static_map"


def enrich_altitude_features(raw: dict, match: MatchIdentity) -> None:
    """Pass-through altitude, then static fill for home venue when still missing (P1-F7)."""
    try:
        env = raw.setdefault("environment", {})
        custom = raw.setdefault("custom", {})
        alt = None
        for _src in (
            custom.get("venue_altitude_m"),
            custom.get("altitude_m"),
            env.get("altitude_m"),
            env.get("venue_altitude_m"),
        ):
            if _src is not None:
                alt = _src
                break
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
    """Weather fill order: env explicit (zero-safe) → live forecast → static climate (P1-F7)."""
    try:
        env = raw.setdefault("environment", {})
        custom = raw.setdefault("custom", {})
        temp = None
        for _src in (
            env.get("weather_temp_c"),
            custom.get("weather_temp_c"),
            env.get("temp_c"),
            custom.get("temp_c"),
        ):
            if _src is not None:
                temp = _src
                break
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

        # Live forecast fill — only when configured and it returns data.
        # Returns None (no HTTP) when unconfigured, beyond the kickoff horizon,
        # city unresolved, or on any network/payload failure.
        from app.sports.football.football_weather import (
            climate_for_home,
            live_weather_for_match,
        )

        # `or {}` + a single read: `live.get(...) is not None` narrows the call,
        # not a later `live["weather_temp_c"]`, which stays Optional.
        live = live_weather_for_match(match) or {}
        live_temp = live.get("weather_temp_c")
        if live_temp is not None:
            env["weather_temp_c"] = float(live_temp)
            env["weather_condition"] = str(live.get("weather_condition") or "mild")
            custom["weather_temp_c"] = float(live_temp)
            custom["weather_condition"] = str(live.get("weather_condition") or "mild")
            custom["weather_source"] = "live_forecast"
            # Multi-source provenance (diagnostics only; the feature contract
            # stays weather_temp_c / weather_condition).
            source_count = live.get("weather_source_count")
            if source_count is not None:
                custom["weather_source_count"] = float(source_count)
            agreement = live.get("weather_agreement")
            if agreement is not None:
                custom["weather_agreement"] = str(agreement)
            return

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
    """Live/static possession, shots, PPDA; writes nothing without a full pair.

    ``raw`` always arrives fresh from :func:`fetch_elo_and_odds`, and nothing
    upstream of the call writes the possession keys, so this function is the
    only producer they ever have.

    A half-resolved pair leaves ``custom`` untouched on purpose. The engine's
    possession factor is a share between the two sides, so one side alone cannot
    produce one, and inventing the missing side would put a guess where a
    measurement belongs. With neither key set the factor reports itself
    unavailable and its weight is redistributed across the factors that do have
    data.
    """
    try:
        home_name = match.home.name if match.home else ""
        away_name = match.away.name if match.away else ""
        competition = (match.season.competition.code or "").lower()
        # Defensive only: the callers of fetch_elo_and_odds are the epl, ucl and
        # league adapters, whose competition codes are epl/ucl/laliga/
        # bundesliga/seriea/ligue1. WorldCupAdapter builds its own raw dict and
        # calls no enricher, so no fixture reaching here has a World Cup code
        # today. The guard stays because a national team has no club style row
        # and the provider would spend a request to learn that.
        is_world_cup = competition in {"wc", "world_cup"}
        live_values: tuple[dict[str, float], dict[str, float]] | None = None
        if not is_world_cup:
            try:
                from app.services.football_live_style_service import get_live_style

                live_home = get_live_style(competition, match.season.season_key, home_name)
                live_away = get_live_style(competition, match.season.season_key, away_name)
                home_live_style = live_home.style
                away_live_style = live_away.style
                if (
                    live_home.available
                    and live_away.available
                    and home_live_style is not None
                    and away_live_style is not None
                ):
                    live_values = (home_live_style, away_live_style)
            except Exception:  # noqa: BLE001
                logger.debug("Live style enrichment unavailable", exc_info=True)

        home_style: dict[str, float] | None
        away_style: dict[str, float] | None
        if live_values is not None:
            home_style, away_style = live_values
            source = "live_provider"
        else:
            from app.sports.football.football_style import stats_for_team

            home_style = stats_for_team(home_name)
            away_style = stats_for_team(away_name)
            source = "static_table"
        if home_style is None or away_style is None:
            return
        custom = raw.setdefault("custom", {})
        custom["possession_home"] = float(home_style["possession_pct"])
        custom["possession_away"] = float(away_style["possession_pct"])
        custom["shots_home"] = float(home_style["shots_per90"])
        custom["shots_away"] = float(away_style["shots_per90"])
        custom["ppda_home"] = float(home_style["ppda"])
        custom["ppda_away"] = float(away_style["ppda"])
        custom["style_source"] = source
    except Exception:  # noqa: BLE001
        logger.debug("Style enrichment failed", exc_info=True)


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
            historical_h2h_meetings,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Historical results module unavailable", exc_info=True)
        get_historical_team_stats = None  # type: ignore[assignment]
        get_historical_h2h = None  # type: ignore[assignment]
        historical_h2h_meetings = None  # type: ignore[assignment]

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

    def _form_rate(stats: dict) -> float | None:
        """Recency-weighted rate when the source carried a per-match sequence.

        Only the kernel club path produces one; the world-cup CSV path returns
        aggregate counts only, so it falls back to the flat rate.
        """
        weighted = stats.get("form_rate_weighted")
        if weighted is not None:
            try:
                return float(weighted)
            except (TypeError, ValueError):
                pass
        return points_form_rate(
            int(stats.get("wins") or 0),
            int(stats.get("draws") or 0),
            int(stats.get("played") or 0),
        )

    if home_stats:
        form_h = _form_rate(home_stats)
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
            # Goals scored, not expected goals. Labelled here so the engine can
            # say so; the static/live block below overwrites both the value and
            # this token when it has something better.
            raw["custom"]["xg_source"] = XG_SOURCE_GOALS_PROXY

    if away_stats:
        form_a = _form_rate(away_stats)
        if form_a is not None:
            raw["team"]["form_away"] = form_a
        last = away_stats.get("last_match_date")
        rest = _days_since(last, before)
        if rest is not None:
            raw["general"]["rest_days_away"] = rest
        gpg = away_stats.get("goals_per_game")
        if gpg is not None:
            raw.setdefault("custom", {})["xg_away"] = float(gpg)
            raw["custom"]["xg_source"] = XG_SOURCE_GOALS_PROXY

    # xG: live configured pair, then static pair; otherwise preserve GPG proxy.
    # Every branch that writes xg_home/xg_away also writes xg_source, so a value
    # with no stated origin cannot leave this function. The engine reads the token
    # rather than assuming the pair is measured xG.
    try:
        live_values: tuple[float, float] | None = None
        if not is_world_cup:
            try:
                from app.services.football_live_xg_service import get_live_xg

                live_h = get_live_xg(competition, match.season.season_key, home_name)
                live_a = get_live_xg(competition, match.season.season_key, away_name)
                home_xg = live_h.xg_per90
                away_xg = live_a.xg_per90
                if (
                    live_h.available
                    and live_a.available
                    and home_xg is not None
                    and away_xg is not None
                ):
                    live_values = (home_xg, away_xg)
            except Exception:  # noqa: BLE001
                logger.debug("Live xG enrichment unavailable", exc_info=True)

        from app.sports.football.football_xg import xg_for_team

        if live_values is not None:
            custom = raw.setdefault("custom", {})
            custom["xg_home"], custom["xg_away"] = live_values
            custom["xg_source"] = "live_provider"
        else:
            xh = xg_for_team(home_name)
            xa = xg_for_team(away_name)
            if xh is not None and xa is not None:
                custom = raw.setdefault("custom", {})
                custom["xg_home"] = float(xh)
                custom["xg_away"] = float(xa)
                custom["xg_source"] = "static_table"
    except Exception:  # noqa: BLE001
        logger.debug("xG enrichment failed", exc_info=True)

    h2h = None
    h2h_source = None
    historical_meetings = []
    kernel_meetings = []
    if historical_h2h_meetings is not None:
        try:
            historical_meetings = historical_h2h_meetings(
                home_name,
                away_name,
                before_date=before,
            )
        except Exception:  # noqa: BLE001
            logger.debug("Historical H2H meetings failed", exc_info=True)

    try:
        from app.sports.football.club_form import h2h_meetings_from_kernel

        kernel_meetings = h2h_meetings_from_kernel(
            home_name,
            away_name,
            competition=competition if not is_world_cup else None,
            before=before,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Club H2H meetings failed", exc_info=True)

    if historical_meetings or kernel_meetings:
        try:
            from app.sports.football.h2h import (
                aggregate_h2h_meetings,
                merge_h2h_meetings,
            )

            merged_meetings = merge_h2h_meetings(
                historical_meetings,
                kernel_meetings,
            )
            if historical_meetings and kernel_meetings:
                h2h_source = "historical+kernel"
            elif historical_meetings:
                h2h_source = "historical"
            else:
                h2h_source = "kernel"
            h2h = aggregate_h2h_meetings(
                merged_meetings,
                max_matches=20,
                data_source=h2h_source,
            )
        except Exception:  # noqa: BLE001
            logger.debug("Combined H2H aggregation failed", exc_info=True)
    elif get_historical_h2h is not None:
        # Compatibility fallback for deployments that expose only the legacy
        # historical aggregate function, not raw meeting extraction.
        try:
            h2h = get_historical_h2h(home_name, away_name, before_date=before)
            h2h_source = "historical"
        except Exception:  # noqa: BLE001
            logger.debug("Legacy H2H enrichment failed", exc_info=True)

    if h2h:
        played = max(int(h2h.get("matches_played") or 0), 1)
        raw["team"]["h2h_home_win_rate"] = round(
            int(h2h.get("home_wins") or 0) / played, 4,
        )
        raw["team"]["h2h_draw_rate"] = round(
            int(h2h.get("draws") or 0) / played, 4,
        )
        h2h_data_source = h2h.get("data_source")
        if h2h_data_source is not None:
            raw.setdefault("custom", {})["h2h_source"] = str(h2h_data_source)
        # Sample size behind the two rates above (P1-F4). Every producer runs
        # through aggregate_h2h_meetings and so returns matches_played, but it
        # was dropped here -- leaving the engine unable to tell a 2-match club
        # record from a 20-match national one, and voting a 1.0 arm off two
        # matches on 168 of 512 live fixtures. Written to custom for the same
        # reason as h2h_home_venue_matches below: the TeamFeatures contract and
        # its type-sync CI stay untouched.
        h2h_played = h2h.get("matches_played")
        if h2h_played is not None:
            raw.setdefault("custom", {})["h2h_matches"] = float(int(h2h_played))
        # Same rates over the subset the current home team hosted (P1-F4).
        # Written to custom rather than TeamFeatures to keep the frozen
        # domain contract - and its type-sync CI - untouched. Unconditional:
        # extra custom keys are inert until the engine flag is turned on.
        venue_matches = h2h.get("home_venue_matches")
        if venue_matches is not None:
            venue_played = int(venue_matches)
            custom = raw.setdefault("custom", {})
            custom["h2h_home_venue_matches"] = float(venue_played)
            if venue_played > 0:
                custom["h2h_home_venue_win_rate"] = round(
                    int(h2h.get("home_venue_home_wins") or 0) / venue_played, 4,
                )
                custom["h2h_home_venue_draw_rate"] = round(
                    int(h2h.get("home_venue_draws") or 0) / venue_played, 4,
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

        history = _fixture_history_for_density(
            competition, match.season.season_key, before,
        )
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

    # P1-F2 residual: cross-competition merge + 3-day window.
    # Written unconditionally; the engine reads these behind a default-OFF flag.
    try:
        from app.sports._shared.rest_form import matches_in_window_as_of
        from app.sports._shared.team_aliases import comparison_key

        merged = _merged_fixture_history(match.season.season_key, before)
        sides = [
            (side, name, comparison_key(name, competition))
            for side, name in (("home", home_name), ("away", away_name))
        ]

        # Real international match days (qualifiers / friendlies / continental)
        # for national teams; the kernel carries tournament fixtures only.
        intl_rows: list[dict] = []
        if is_world_cup:
            try:
                intl_rows = _international_density_rows(
                    [(name, key) for _side, name, key in sides],
                    before,
                    merged or [],
                )
            except Exception:  # noqa: BLE001 — keep the kernel counts usable
                logger.debug("international schedule density skipped", exc_info=True)

        if merged is not None or intl_rows:
            custom = raw.setdefault("custom", {})
            history = list(merged or []) + intl_rows
            for side, _name, key in sides:
                for days in (7, 3):
                    n = matches_in_window_as_of(
                        key,
                        before,
                        history,
                        window_days=days,
                        exclude_match_id=match.match_id,
                    )
                    if n is not None:
                        custom[f"matches_merged_{days}d_{side}"] = int(n)
                if intl_rows:
                    intl = matches_in_window_as_of(
                        key, before, intl_rows, window_days=_INTL_DENSITY_WINDOW_DAYS,
                    )
                    if intl is not None:
                        custom[f"matches_intl_{_INTL_DENSITY_WINDOW_DAYS}d_{side}"] = int(intl)
            if intl_rows:
                custom["schedule_intl_source"] = "international_results"
    except Exception:  # noqa: BLE001
        logger.debug("merged schedule density skipped", exc_info=True)

    # P1-F3: contextual availability provider, then API-Football/static/WC fallbacks.
    try:
        from app.sports.football.football_injury import injury_impact_for_team

        availability_results = {}
        live_results = {}
        if not is_world_cup:
            try:
                from app.services.football_live_availability_service import (
                    get_live_availability_impact,
                )

                season_key = match.season.season_key
                availability_results = {
                    side: get_live_availability_impact(competition, season_key, name)
                    for side, name in (("home", home_name), ("away", away_name))
                }
            except Exception:  # noqa: BLE001
                logger.debug("Live availability enrichment unavailable", exc_info=True)
            try:
                from app.services.football_live_injury_service import (
                    get_live_injury_impact,
                )

                season_key = match.season.season_key
                live_results = {
                    side: get_live_injury_impact(competition, season_key, name)
                    for side, name in (("home", home_name), ("away", away_name))
                    if not (
                        availability_results.get(side) is not None
                        and availability_results[side].available
                        and availability_results[side].impact is not None
                    )
                }
            except Exception:  # noqa: BLE001
                logger.debug("Live injury enrichment unavailable", exc_info=True)

        wc_lookup: Callable[[str], float | None] | None = None
        for side, name in (("home", home_name), ("away", away_name)):
            impact: float | None
            availability = availability_results.get(side)
            live = live_results.get(side)
            if availability is not None and availability.available and availability.impact is not None:
                impact = availability.impact
                source: str | None = "live_availability_provider"
            elif live is not None and live.available:
                impact = live.impact
                source = "api_football"
            else:
                impact = injury_impact_for_team(name)
                source = "static_table" if impact is not None else None
                if impact is None:
                    if wc_lookup is None:
                        try:
                            from app.services.world_cup_player_status_source import (
                                get_team_injury_impact,
                            )
                            wc_lookup = get_team_injury_impact
                        except Exception:  # noqa: BLE001
                            wc_lookup = None
                    if wc_lookup is not None:
                        try:
                            impact = wc_lookup(name)
                            source = "world_cup_facts" if impact is not None else None
                        except Exception:  # noqa: BLE001
                            impact = None

            if impact is not None:
                raw["player"][f"injury_impact_{side}"] = float(impact)
                custom = raw.setdefault("custom", {})
                custom[f"injury_impact_{side}"] = float(impact)
                custom[f"injury_source_{side}"] = source
    except Exception:  # noqa: BLE001
        logger.debug("Injury impact enrich skipped", exc_info=True)

    raw["environment"]["is_home_advantage"] = not is_world_cup
    if is_world_cup:
        raw["environment"]["venue"] = raw["environment"].get("venue") or "neutral"


def _fixture_history_for_density(
    competition: str | None,
    season: str | None = None,
    before: datetime | None = None,
) -> list[dict] | None:
    """Load kernel fixture history, then use configured live fallback if empty."""
    try:
        from app.kernel.kernel_db import KernelMatchFixture, get_kernel_session

        session = get_kernel_session()
        try:
            query = session.query(KernelMatchFixture)
            if competition:
                query = query.filter(KernelMatchFixture.competition == competition)
            rows = query.all()
            if rows:
                return [
                    {
                        "match_id": fixture.match_id,
                        "home_team": fixture.home_team or "",
                        "away_team": fixture.away_team or "",
                        "kickoff_utc": fixture.kickoff_utc,
                    }
                    for fixture in rows
                ]
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        logger.debug("fixture history for density failed", exc_info=True)

    return _live_fixture_history_for_density(competition, season, before)


def _live_fixture_history_for_density(
    competition: str | None,
    season: str | None,
    before: datetime | None,
) -> list[dict] | None:
    """Read configured fixture history without writing it to the kernel."""
    try:
        from app.services.football_live_schedule_service import get_live_schedule

        live = get_live_schedule(competition, season, before)
        return live.fixtures if live.available else None
    except Exception:  # noqa: BLE001
        logger.debug("live fixture history for density failed", exc_info=True)
        return None


def _merged_history_rows(rows: list[dict]) -> list[dict]:
    """Keep football rows only, with team names resolved to comparison keys.

    Each row is resolved against **its own** competition: the alias tables are
    per-competition and a few abbreviations collide across them (``CEL`` is
    celta_vigo in laliga but celtic in ucl), so flattening them would merge
    unrelated clubs. Resolving per row makes those cases fall out correctly
    without special-casing.
    """
    from app.kernel.factor_registry import FactorRegistry
    from app.sports._shared.team_aliases import comparison_key

    football = FactorRegistry._FOOTBALL_COMPETITIONS
    out: list[dict] = []
    for r in rows:
        comp = (r.get("competition") or "").lower()
        if comp not in football:
            continue
        out.append(
            {
                "match_id": r.get("match_id"),
                "home_team": comparison_key(r.get("home_team") or "", comp),
                "away_team": comparison_key(r.get("away_team") or "", comp),
                "kickoff_utc": r.get("kickoff_utc"),
            }
        )
    return out


def _merged_fixture_rows() -> list[dict] | None:
    """Raw fixture rows across all football competitions. None on failure."""
    try:
        from app.kernel.factor_registry import FactorRegistry
        from app.kernel.kernel_db import KernelMatchFixture, get_kernel_session

        session = get_kernel_session()
        try:
            rows = (
                session.query(KernelMatchFixture)
                .filter(
                    KernelMatchFixture.competition.in_(
                        sorted(FactorRegistry._FOOTBALL_COMPETITIONS)
                    )
                )
                .all()
            )
            return [
                {
                    "match_id": f.match_id,
                    "home_team": f.home_team or "",
                    "away_team": f.away_team or "",
                    "kickoff_utc": f.kickoff_utc,
                    "competition": f.competition,
                }
                for f in rows
            ]
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        logger.debug("merged fixture rows failed", exc_info=True)
        return None


def _merged_fixture_history(
    season: str | None = None,
    before: datetime | None = None,
) -> list[dict] | None:
    """Fixtures across football competitions, name-resolved, with live fallback."""
    rows = _merged_fixture_rows()
    if rows:
        return _merged_history_rows(rows)

    try:
        from app.kernel.factor_registry import FactorRegistry
        from app.services.football_live_schedule_service import get_live_schedule

        live_rows: list[dict] = []
        for competition in sorted(FactorRegistry._FOOTBALL_COMPETITIONS):
            result = get_live_schedule(competition, season, before)
            if not result.available:
                continue
            for fixture in result.fixtures or []:
                live_rows.append({**fixture, "competition": competition})
        return _merged_history_rows(live_rows) if live_rows else None
    except Exception:  # noqa: BLE001
        logger.debug("live merged fixture history failed", exc_info=True)
        return None


def _international_density_rows(
    sides: Sequence[tuple[str, str]],
    before: datetime | None,
    history: Sequence[Mapping[str, Any]],
) -> list[dict]:
    """Pseudo-fixture rows for real international match days.

    National-team schedule density otherwise sees kernel tournament fixtures
    only: qualifiers, friendlies, and continental matches never reach the
    kernel, so a side arriving on three days' rest looks fully rested. The
    shipped international results CSV records those match days, so they are
    folded in here.

    Deduplicated by calendar date per side -- a national team plays at most one
    match per day, so a date already present in ``history`` for that side is the
    same match and is skipped. No fixture-ID compatibility is assumed between
    the two sources.
    """
    from app.services.world_cup_historical_results import international_match_dates

    rows: list[dict] = []
    for name, key in sides:
        seen = {
            day
            for row in history
            if key in {row.get("home_team"), row.get("away_team")}
            and (day := _calendar_date(row.get("kickoff_utc"))) is not None
        }
        for played in international_match_dates(
            name, before_date=before, window_days=_INTL_DENSITY_WINDOW_DAYS,
        ):
            if played in seen:
                continue
            seen.add(played)
            rows.append({
                "match_id": None,
                "home_team": key,
                "away_team": "",
                # Midnight UTC: the CSV records calendar dates only, and the
                # window counter measures whole days elapsed.
                "kickoff_utc": datetime(
                    played.year, played.month, played.day, tzinfo=timezone.utc,
                ),
            })
    return rows


def _calendar_date(value: Any) -> date | None:
    """UTC calendar date of a fixture timestamp, or None when unusable."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
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
            last = last_match_date.date()
        else:
            last = last_match_date
        kd = kickoff.date() if isinstance(kickoff, datetime) else kickoff
        delta = (kd - last).days
        return float(max(0, min(delta, 60)))
    except Exception:  # noqa: BLE001
        return None


def query_fixture(match_id: str, model_cls: type[Any]) -> Any | None:
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


def query_result(match_id: str, model_cls: type[Any]) -> Any | None:
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
    """Upsert a parsed fixture into kernel_match_fixtures, plus its result row.

    parsed: dict from football_data_client.parse_fixture()

    The result row is what P1-E9 added. Football wrote scores onto the fixture
    row only, while ``fetch_outcome``, ``team_form_from_kernel`` and
    ``h2h_meetings_from_kernel`` all read ``kernel_match_results`` -- so on the
    live kernel DB 1181 finished club fixtures held real scores against zero
    result rows, and settlement, club form, rest, the xG goals proxy and h2h
    were all unreachable. The three binary-sport adapters already write both
    tables in their own save paths; this is the same write, with the draw token
    football needs.
    """
    from app.kernel.kernel_db import (
        get_kernel_session,
        KernelMatchFixture,
        KernelMatchResult,
    )
    from app.sports._shared.match_outcome import outcome_from_scores
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

        # Only a finished fixture becomes a result. parse_fixture reads
        # score.fullTime, which Football-Data.org also fills during IN_PLAY, so
        # gating on the score alone would publish a partial score as final and
        # let a prediction settle against it.
        hs = parsed.get("home_score")
        aws = parsed.get("away_score")
        if parsed.get("status") == "finished" and hs is not None and aws is not None:
            hs_i = int(hs)
            aws_i = int(aws)
            outcome = outcome_from_scores(hs_i, aws_i, allow_draw=True)
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
