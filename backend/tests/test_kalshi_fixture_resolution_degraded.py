"""What an unreadable kernel_match_fixtures table does to Kalshi linking.

`_resolve_match_id` wrapped its fixture query in
`except Exception: logger.warning(...); return None`. `None` is also its normal
answer — "no such pairing, or ambiguous" — and the caller renders that as
`reason="no_matching_fixture"`, a stated fact about a table the query never
reached.

Measured over one seeded fixture: renaming `home_team`, dropping the table and
deleting every row gave **identical** answers at all four doors —
`linked=False reason="no_matching_fixture"`, zero links stored, and a ledger row
reading `success kalshi_unresolved=1 kalshi_errors=0`. Unlike the liquidity feed,
the cold-start answer is *not* the live state here: `kernel_match_fixtures` holds
18,717 rows, so any of those runs would have been a real outage reported as a
quiet slate.

After the fix the read escapes and the scheduler's per-candidate handler counts
it into `kalshi_errors` — which is the operator's only view of the run.
"""
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.kernel.kernel_db import (
    KernelMatchFixture,
    close_kernel_db,
    get_kernel_session,
    init_kernel_db,
)
from app.kernel.sport_market_bridge_service import SportMarketBridgeService
from app.kernel.sport_market_link_store import SportMarketLinkStore

MATCH_ID = "nba-20250101-LAL-BOS"
#: The rule layer resolves these tokens out of MATCH_ID, so it answers 0.95 and
#: no LLM call is reachable from any test here.
TEAMS = ["los_angeles_lakers", "boston_celtics"]
DATE = "2025-01-01"


@pytest.fixture
def kernel_db(tmp_path):
    close_kernel_db()
    path = str(tmp_path / "fixture_degraded.db")
    with patch.object(settings, "KERNEL_DB_FILE", path):
        init_kernel_db(path)
        try:
            yield
        finally:
            close_kernel_db()


@pytest.fixture
def bridge():
    return SportMarketBridgeService()


def _sql(stmt: str) -> None:
    """Real DDL through the live ORM session, visible to later ORM reads."""
    session = get_kernel_session()
    try:
        session.execute(text(stmt))
        session.commit()
    finally:
        session.close()


def _seed_fixture() -> None:
    session = get_kernel_session()
    try:
        session.add(
            KernelMatchFixture(
                match_id=MATCH_ID,
                competition="nba",
                season="2024-25",
                home_team="Los Angeles Lakers",
                away_team="Boston Celtics",
                kickoff_utc=datetime(2025, 1, 1),
                status="scheduled",
            )
        )
        session.commit()
    finally:
        session.close()


def _candidate() -> dict:
    """Exactly the keys fetch_kalshi_sport_markets emits — no match_id."""
    return {
        "contract_id": "KXNBAGAME-25JAN01-LAL-BOS",
        "question": "Will the Lakers beat the Celtics?",
        "price": 0.65,
        "no_price": 0.35,
        "liquidity": 5000.0,
        "volume": 12000.0,
        "source": "kalshi",
        "detected_sport": "basketball",
        "detected_competition": "nba",
        "detected_teams": TEAMS,
        "detected_date": DATE,
    }


def _resolve(bridge):
    return bridge._resolve_match_id(
        competition="nba", detected_teams=TEAMS, detected_date=DATE
    )


#: Two ways to make the query fail while every other table stays readable.
_DAMAGE = {
    "renamed_column": (
        "ALTER TABLE kernel_match_fixtures RENAME COLUMN home_team TO home_team_x"
    ),
    "dropped_table": "DROP TABLE kernel_match_fixtures",
}


async def _ledger_row() -> dict:
    """Run the discovery job and return the ledger row an operator would read."""
    from app.core import scheduler

    captured: dict = {}

    def _finish(run_id, status, *, result=None, error=None, exc=None):
        captured.update(status=status, result=result, error=error)

    with patch.object(scheduler, "_start_run", lambda job_name: "run-1"), \
            patch.object(scheduler, "_finish_run", _finish), \
            patch.object(
                scheduler, "fetch_kalshi_sport_markets",
                AsyncMock(return_value=[_candidate()]),
            ), \
            patch.object(settings, "PHASE7_SPORT_MARKET_BRIDGE_ENABLED", True), \
            patch.object(settings, "PHASE11_KALSHI_SPORTS_ENABLED", True), \
            patch.object(settings, "PHASE7_POLYMARKET_SPORTS_SOURCE_ENABLED", False):
        await scheduler._job_discover_sport_markets()
    return captured


def test_a_seeded_fixture_resolves_to_its_match_id(kernel_db, bridge):
    _seed_fixture()

    assert _resolve(bridge) == MATCH_ID


async def test_a_seeded_fixture_links_and_stores_one_link(kernel_db, bridge):
    _seed_fixture()

    result = await bridge.link_kalshi_market(_candidate())

    assert result["linked"] is True
    assert result["match_id"] == MATCH_ID
    assert len(SportMarketLinkStore().list_links()) == 1


async def test_a_readable_but_empty_table_still_answers_no_matching_fixture(
    kernel_db, bridge
):
    """The reverse test: an empty slate is a normal answer and must not raise."""
    _seed_fixture()
    _sql("DELETE FROM kernel_match_fixtures")

    assert _resolve(bridge) is None
    assert await bridge.link_kalshi_market(_candidate()) == {
        "linked": False,
        "verified": False,
        "source": "kalshi",
        "reason": "no_matching_fixture",
    }
    assert SportMarketLinkStore().list_links() == []


@pytest.mark.parametrize("ddl", list(_DAMAGE.values()), ids=list(_DAMAGE))
def test_an_unreadable_fixtures_table_escapes_the_resolver(kernel_db, bridge, ddl):
    _seed_fixture()
    _sql(ddl)

    with pytest.raises(OperationalError):
        _resolve(bridge)


@pytest.mark.parametrize("ddl", list(_DAMAGE.values()), ids=list(_DAMAGE))
async def test_an_unreadable_fixtures_table_escapes_the_link_door(
    kernel_db, bridge, ddl
):
    """The caller must not turn a failed read into reason="no_matching_fixture"."""
    _seed_fixture()
    _sql(ddl)

    with pytest.raises(OperationalError):
        await bridge.link_kalshi_market(_candidate())
    assert SportMarketLinkStore().list_links() == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"competition": None, "detected_teams": TEAMS, "detected_date": DATE},
        {"competition": "nba", "detected_teams": TEAMS[:1], "detected_date": DATE},
    ],
    ids=["no_competition", "one_team"],
)
def test_the_early_returns_answer_none_without_touching_the_table(
    kernel_db, bridge, kwargs
):
    """Both return before any query is built, so a dropped table cannot matter."""
    _seed_fixture()
    _sql("DROP TABLE kernel_match_fixtures")

    assert bridge._resolve_match_id(**kwargs) is None


async def test_the_ledger_separates_an_unreadable_table_from_an_empty_slate(kernel_db):
    """kalshi_unresolved is an expected number; kalshi_errors is an alarm.

    The per-candidate handler in the job still absorbs the raise — correct for
    one bad market among many — so the run stays `success`. What changes is
    which counter moves, and that is the operator's whole view of the run.
    """
    _seed_fixture()
    _sql("DELETE FROM kernel_match_fixtures")
    empty = await _ledger_row()

    _sql(_DAMAGE["renamed_column"])
    unreadable = await _ledger_row()

    assert empty["status"] == "success"
    assert empty["result"]["kalshi_unresolved"] == 1
    assert empty["result"]["kalshi_errors"] == 0

    assert unreadable["status"] == "success"
    assert unreadable["result"]["kalshi_unresolved"] == 0
    assert unreadable["result"]["kalshi_errors"] == 1

    # Before the fix these two rows were byte-identical.
    assert empty["result"] != unreadable["result"]
