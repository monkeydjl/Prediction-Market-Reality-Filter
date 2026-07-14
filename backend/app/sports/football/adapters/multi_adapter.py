# backend/app/sports/football/adapters/multi_adapter.py
"""MultiAdapter — DataAdapter Protocol proxy with prefix-based dispatch.

Implements DataAdapter Protocol transparently. The PredictionKernel
sees a single adapter; internally, calls are routed to the correct
league adapter based on the match_id prefix.

Prefix mapping:
    "wc-"  → WorldCupAdapter
    "ucl-" → UCLAdapter
    "epl-" → EPLAdapter

Unknown prefixes fall back to the default adapter (first registered,
or "wc-" if present) for backward compatibility.
"""
from __future__ import annotations

import logging

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

    def get_match_identity(self, match_id: str) -> MatchIdentity:
        return self._select(match_id).get_match_identity(match_id)

    def fetch_all_data(self, match: MatchIdentity) -> dict:
        return self._select(match.match_id).fetch_all_data(match)

    def fetch_outcome(self, match_id: str) -> MatchOutcome | None:
        return self._select(match_id).fetch_outcome(match_id)

    def sync_schedule(self) -> int:
        return sum(adapter.sync_schedule() for adapter in self._adapters.values())

    def fetch_schedule(self, filters: ScheduleFilter) -> list[RawMatchData]:
        results = []
        for adapter in self._adapters.values():
            results.extend(adapter.fetch_schedule(filters))
        return results

    def fetch_team_data(self, team: TeamIdentity) -> dict:
        return self._default.fetch_team_data(team)

    def fetch_player_data(self, team: TeamIdentity) -> dict:
        return self._default.fetch_player_data(team)

    def fetch_market_data(self, match: MatchIdentity) -> dict:
        return self._select(match.match_id).fetch_market_data(match)
