"""Kalshi candidate -> match_id resolution (regression for the KeyError).

``fetch_kalshi_sport_markets`` never emitted a ``match_id`` key, but
``link_kalshi_market`` read ``candidate["match_id"]`` directly. Every real
candidate therefore raised KeyError before any matching work; the scheduler
caught it as a warning and still recorded the run as ``success`` with a
non-zero candidate count, so Kalshi coverage was permanently zero while the
run ledger read healthy.
"""
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.kernel.sport_market_bridge_service import MatchResult, SportMarketBridgeService


@pytest.fixture
def bridge():
    return SportMarketBridgeService()


def _producer_candidate(**overrides):
    """Exactly the keys fetch_kalshi_sport_markets emits — no match_id."""
    defaults = dict(
        contract_id="KXNBAGAME-25JAN01-LAL-BOS",
        question="Will the Lakers beat the Celtics?",
        price=0.65,
        no_price=0.35,
        liquidity=5000.0,
        volume=12000.0,
        source="kalshi",
        detected_sport="basketball",
        detected_competition="nba",
        detected_teams=["los_angeles_lakers", "boston_celtics"],
        detected_date="2025-01-01",
    )
    defaults.update(overrides)
    return defaults


def _fixture_row(match_id, home, away, kickoff="2025-01-01"):
    return SimpleNamespace(
        match_id=match_id,
        home_team=home,
        away_team=away,
        kickoff_utc=datetime.strptime(kickoff, "%Y-%m-%d"),
    )


def _patch_fixtures(rows):
    """Patch the kernel session so _resolve_match_id sees `rows`."""
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = rows
    return patch(
        "app.kernel.kernel_db.get_kernel_session", return_value=session
    )


@pytest.mark.asyncio
async def test_producer_candidate_does_not_raise_key_error(bridge):
    """The real candidate shape must be handled, not crash."""
    with _patch_fixtures([]):
        result = await bridge.link_kalshi_market(_producer_candidate())

    assert result["linked"] is False
    assert result["reason"] == "no_matching_fixture"


@pytest.mark.asyncio
async def test_resolves_fixture_and_links(bridge):
    rows = [_fixture_row("nba-20250101-LAL-BOS", "Lakers", "Celtics")]

    with _patch_fixtures(rows):
        with patch.object(bridge, "_rule_match", return_value=MatchResult(
            confidence=0.95, mapped_outcome="home_win", reasoning="rule high"
        )):
            with patch.object(bridge, "_links") as mock_store:
                mock_store.upsert_link = MagicMock(return_value={"id": 1})
                result = await bridge.link_kalshi_market(_producer_candidate())

    assert result["linked"] is True
    assert result["match_id"] == "nba-20250101-LAL-BOS"
    assert mock_store.upsert_link.call_args.kwargs["match_id"] == "nba-20250101-LAL-BOS"


def test_date_disambiguates_repeated_pairing(bridge):
    """The same pairing recurs across a season; the date picks one."""
    rows = [
        _fixture_row("nba-20250101-LAL-BOS", "Lakers", "Celtics", "2025-01-01"),
        _fixture_row("nba-20250320-LAL-BOS", "Lakers", "Celtics", "2025-03-20"),
    ]

    with _patch_fixtures(rows):
        resolved = bridge._resolve_match_id(
            competition="nba",
            detected_teams=["los_angeles_lakers", "boston_celtics"],
            detected_date="2025-03-20",
        )

    assert resolved == "nba-20250320-LAL-BOS"


def test_ambiguous_pairing_without_date_returns_none(bridge):
    rows = [
        _fixture_row("nba-20250101-LAL-BOS", "Lakers", "Celtics", "2025-01-01"),
        _fixture_row("nba-20250320-LAL-BOS", "Lakers", "Celtics", "2025-03-20"),
    ]

    with _patch_fixtures(rows):
        assert bridge._resolve_match_id(
            competition="nba",
            detected_teams=["los_angeles_lakers", "boston_celtics"],
            detected_date=None,
        ) is None


@pytest.mark.asyncio
async def test_explicit_match_id_still_wins(bridge):
    """Callers that already know the match (and the existing tests) keep working."""
    candidate = _producer_candidate(match_id="nba-20250101-LAL-BOS")

    with patch.object(bridge, "_resolve_match_id") as resolver:
        with patch.object(bridge, "_rule_match", return_value=MatchResult(
            confidence=0.95, mapped_outcome="home_win", reasoning="rule high"
        )):
            with patch.object(bridge, "_links") as mock_store:
                mock_store.upsert_link = MagicMock(return_value={"id": 1})
                result = await bridge.link_kalshi_market(candidate)

    resolver.assert_not_called()
    assert result["match_id"] == "nba-20250101-LAL-BOS"


def test_insufficient_detection_returns_none(bridge):
    assert bridge._resolve_match_id(
        competition=None, detected_teams=["los_angeles_lakers", "boston_celtics"], detected_date=None
    ) is None
    assert bridge._resolve_match_id(
        competition="nba", detected_teams=["los_angeles_lakers"], detected_date=None
    ) is None
