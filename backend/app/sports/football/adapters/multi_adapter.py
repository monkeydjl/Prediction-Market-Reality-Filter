# backend/app/sports/football/adapters/multi_adapter.py
"""MultiAdapter — DataAdapter Protocol proxy with prefix-based dispatch.

Implements DataAdapter Protocol transparently. The PredictionKernel
sees a single adapter; internally, calls are routed to the correct
league adapter based on the match_id prefix.

Prefix mapping:
    "wc-"  → WorldCupAdapter
    "ucl-" → UCLAdapter
    "epl-" → EPLAdapter
    "laliga-" / "bundesliga-" / "seriea-" / "ligue1-" → LeagueAdapter
    "nba-" / "mlb-" / "nhl-" → sport adapters

Unknown prefixes fall back to the default adapter (first registered,
or "wc-" if present) for backward compatibility.

``fetch_schedule`` optionally short-circuits by ``filters.competition``
and ``filters.sport`` so a 竞猜 hub request for epl does not hit every
league adapter (and its DB session).
"""
from __future__ import annotations

import logging

from app.kernel.competition_codes import (
    competitions_equivalent,
    competition_code_for_prefix,
    sport_for_prefix,
)
from app.kernel.domain import (
    MatchIdentity, MatchOutcome, TeamIdentity,
)
from app.kernel.protocols import ScheduleFilter, RawMatchData

logger = logging.getLogger(__name__)


class MultiAdapter:
    """DataAdapter Protocol proxy — dispatches by match_id prefix."""

    def __init__(self, adapters: dict[str, object]) -> None:
        """Initialize with prefix-to-adapter mapping.

        Args:
            adapters: {prefix: adapter} where prefix is a string like
                "wc-", "ucl-", "epl-". The first adapter is used as
                the default for unknown prefixes.
        """
        self._adapters = adapters
        # Default to first adapter for unknown prefixes
        self._default = next(iter(adapters.values()))

    def _select(self, match_id: str) -> object:
        """Select the adapter for a given match_id by prefix."""
        for prefix, adapter in self._adapters.items():
            if match_id.startswith(prefix):
                return adapter
        return self._default

    def _adapter_competition_code(self, prefix: str, adapter: object) -> str | None:
        # Prefer real instance attrs with str codes (ignore auto MagicMock children).
        cfg = getattr(adapter, "_config", None)
        cfg_code = getattr(cfg, "code", None) if cfg is not None else None
        if isinstance(cfg_code, str) and cfg_code:
            return cfg_code
        competition = getattr(adapter, "_competition", None)
        comp_code = getattr(competition, "code", None) if competition is not None else None
        if isinstance(comp_code, str) and comp_code:
            return comp_code
        return competition_code_for_prefix(prefix)

    def _adapter_sport_code(self, prefix: str, adapter: object) -> str | None:
        competition = getattr(adapter, "_competition", None)
        if competition is not None:
            sport = getattr(competition, "sport", None)
            sport_code = getattr(sport, "code", None) if sport is not None else None
            if isinstance(sport_code, str) and sport_code:
                return sport_code
        cfg = getattr(adapter, "_config", None)
        cfg_code = getattr(cfg, "code", None) if cfg is not None else None
        if isinstance(cfg_code, str) and cfg_code:
            # LeagueConfig is football-only today.
            return "football"
        return sport_for_prefix(prefix)

    def _iter_schedule_adapters(
        self, filters: ScheduleFilter,
    ) -> list[tuple[str, object]]:
        """Adapters that should participate in fetch_schedule for *filters*."""
        wanted_comp = filters.competition
        wanted_sport = filters.sport
        selected: list[tuple[str, object]] = []
        for prefix, adapter in self._adapters.items():
            if wanted_sport:
                sport = self._adapter_sport_code(prefix, adapter)
                if sport is not None and sport != wanted_sport:
                    continue
            if wanted_comp:
                code = self._adapter_competition_code(prefix, adapter)
                if code is None or not competitions_equivalent(code, wanted_comp):
                    continue
            selected.append((prefix, adapter))
        return selected

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        return self._select(match_id).get_match_identity(match_id)

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        return self._select(match.match_id).fetch_all_data(match)

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        return self._select(match_id).fetch_outcome(match_id)

    def sync_schedule(
        self,
        filters: ScheduleFilter | None = None,
    ) -> int:
        """Sync schedules; optionally only adapters matching *filters*.

        When *filters* is None or empty, every registered adapter is synced
        (legacy MultiAdapter behaviour). With competition/sport set, only
        matching adapters run — same selection as ``fetch_schedule``.
        """
        if filters is None or not (filters.competition or filters.sport):
            return sum(
                int(adapter.sync_schedule()) for adapter in self._adapters.values()
            )
        targets = self._iter_schedule_adapters(filters)
        if not targets:
            return 0
        total = 0
        for prefix, adapter in targets:
            try:
                total += int(adapter.sync_schedule())
            except Exception:  # pragma: no cover - defensive
                logger.warning(
                    "sync_schedule failed for prefix=%s", prefix, exc_info=True,
                )
        return total

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        results: list[RawMatchData] = []
        targets = self._iter_schedule_adapters(filters)
        if not targets and (filters.competition or filters.sport):
            # No registered adapter matches the filter — empty, not fallback-all.
            return []
        if not targets:
            targets = list(self._adapters.items())
        for prefix, adapter in targets:
            try:
                results.extend(adapter.fetch_schedule(filters))
            except Exception:  # pragma: no cover - defensive
                logger.warning(
                    "fetch_schedule failed for prefix=%s", prefix, exc_info=True,
                )
        return results

    def fetch_team_data(self, team: TeamIdentity) -> dict:
        return self._default.fetch_team_data(team)

    def fetch_player_data(self, team: TeamIdentity) -> dict:
        return self._default.fetch_player_data(team)

    def fetch_market_data(self, match: MatchIdentity) -> dict:
        return self._select(match.match_id).fetch_market_data(match)

    def registered_prefixes(self) -> list[str]:
        """Prefixes currently wired into this MultiAdapter (for diagnostics)."""
        return list(self._adapters.keys())
