# backend/tests/test_multi_adapter.py
"""Tests for MultiAdapter — prefix-dispatch proxy."""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import pytest

from app.kernel.domain import (
    SportIdentity, CompetitionIdentity, SeasonIdentity,
    TeamIdentity, MatchIdentity, MatchOutcome,
)
from app.kernel.protocols import DataAdapter, ScheduleFilter, RawMatchData
from app.sports.football.adapters.multi_adapter import MultiAdapter


def _make_match(match_id="wc-123") -> MatchIdentity:
    football = SportIdentity(code="football", name="Football")
    wc = CompetitionIdentity(code="world_cup", name="FIFA World Cup", sport=football)
    return MatchIdentity(
        match_id=match_id,
        season=SeasonIdentity(competition=wc, season_key="2026"),
        stage="group_stage", round=None,
        home=TeamIdentity(code="BRA", name="Brazil", competition=wc),
        away=TeamIdentity(code="ARG", name="Argentina", competition=wc),
        kickoff_utc=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )


def _mock_adapter():
    """Create a MagicMock that satisfies DataAdapter Protocol."""
    adapter = MagicMock()
    adapter.get_match_identity.return_value = _make_match()
    adapter.fetch_all_data.return_value = {"team": {}, "market": {}}
    adapter.fetch_outcome.return_value = None
    adapter.sync_schedule.return_value = 5
    adapter.fetch_schedule.return_value = []
    adapter.fetch_team_data.return_value = {}
    adapter.fetch_player_data.return_value = {}
    adapter.fetch_market_data.return_value = {}
    return adapter


class TestMultiAdapterProtocol:
    def test_satisfies_data_adapter_protocol(self):
        multi = MultiAdapter({"wc": _mock_adapter()})
        assert isinstance(multi, DataAdapter)


class TestPrefixDispatch:
    def test_wc_prefix_dispatches_to_wc_adapter(self):
        wc = _mock_adapter()
        ucl = _mock_adapter()
        epl = _mock_adapter()
        multi = MultiAdapter({"wc-": wc, "ucl-": ucl, "epl-": epl})

        multi.get_match_identity("wc-123")
        wc.get_match_identity.assert_called_once_with("wc-123")
        ucl.get_match_identity.assert_not_called()

    def test_ucl_prefix_dispatches_to_ucl_adapter(self):
        wc = _mock_adapter()
        ucl = _mock_adapter()
        multi = MultiAdapter({"wc-": wc, "ucl-": ucl})

        multi.get_match_identity("ucl-456")
        ucl.get_match_identity.assert_called_once_with("ucl-456")

    def test_unknown_prefix_falls_back_to_default(self):
        wc = _mock_adapter()
        multi = MultiAdapter({"wc-": wc})

        multi.get_match_identity("unknown-789")
        wc.get_match_identity.assert_called_once_with("unknown-789")

    def test_fetch_all_data_dispatches_by_match_id(self):
        wc = _mock_adapter()
        ucl = _mock_adapter()
        multi = MultiAdapter({"wc-": wc, "ucl-": ucl})

        match = _make_match("ucl-999")
        multi.fetch_all_data(match)
        ucl.fetch_all_data.assert_called_once_with(match)
        wc.fetch_all_data.assert_not_called()

    def test_fetch_outcome_dispatches(self):
        wc = _mock_adapter()
        epl = _mock_adapter()
        multi = MultiAdapter({"wc-": wc, "epl-": epl})

        multi.fetch_outcome("epl-123")
        epl.fetch_outcome.assert_called_once_with("epl-123")


class TestSyncSchedule:
    def test_sync_aggregates_all_adapters(self):
        wc = _mock_adapter()
        wc.sync_schedule.return_value = 10
        ucl = _mock_adapter()
        ucl.sync_schedule.return_value = 5
        epl = _mock_adapter()
        epl.sync_schedule.return_value = 20

        multi = MultiAdapter({"wc-": wc, "ucl-": ucl, "epl-": epl})
        total = multi.sync_schedule()
        assert total == 35

    def test_sync_with_single_adapter(self):
        wc = _mock_adapter()
        wc.sync_schedule.return_value = 10
        multi = MultiAdapter({"wc-": wc})
        assert multi.sync_schedule() == 10


class TestFetchSchedule:
    def test_aggregates_all_adapters(self):
        wc = _mock_adapter()
        wc.fetch_schedule.return_value = [MagicMock()]
        ucl = _mock_adapter()
        ucl.fetch_schedule.return_value = [MagicMock(), MagicMock()]

        multi = MultiAdapter({"wc-": wc, "ucl-": ucl})
        results = multi.fetch_schedule(ScheduleFilter())
        assert len(results) == 3


class TestStubMethods:
    def test_fetch_team_data_delegates_to_default(self):
        wc = _mock_adapter()
        multi = MultiAdapter({"wc-": wc})
        multi.fetch_team_data(MagicMock())
        wc.fetch_team_data.assert_called_once()

    def test_fetch_market_data_dispatches_by_match_id(self):
        wc = _mock_adapter()
        epl = _mock_adapter()
        multi = MultiAdapter({"wc-": wc, "epl-": epl})

        match = _make_match("epl-1")
        multi.fetch_market_data(match)
        epl.fetch_market_data.assert_called_once_with(match)
