"""What an unreadable futures links table says.

``FuturesLinkStore``'s three reads swallowed query failures into ``[]``, which
is also the answer before Kalshi discovery has linked anything -- the state the
live tables are actually in. Measured on a temp kernel DB holding a 5-leg
``KXNBACHAMP-25-*`` book: with the links table dropped every route answered
**200** with zero pairs and ``integrity.status="incomplete"``, and the
scheduler's snapshot-capture job wrote ``success captured=0 errors=0`` to the
run ledger, while ``upsert_link`` / ``append_snapshot`` on the same broken
tables raised.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.scheduler import _job_capture_futures_snapshots
from app.kernel.futures_link_store import FuturesLinkStore
from app.kernel.kernel_db import close_kernel_db, get_kernel_session, init_kernel_db
from app.main import app

LINKS = "kernel_futures_links"
SNAPSHOTS = "kernel_futures_snapshots"
COMPETITION = "nba"
SEASON = "2024-25"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)

#: One Kalshi championship book as ``(team, ticker, price, liquidity)``. The
#: five prices sum to 1.02, inside ``multi_leg_integrity``'s [0.85, 1.45] band,
#: so a healthy read reports ``status="ok"`` -- which an empty book cannot
#: coincide with, since zero legs is ``too_few_legs`` -> ``"incomplete"``.
BOOK = (
    ("BOS", "KXNBACHAMP-25-BOS", 0.28, 41000.0),
    ("OKC", "KXNBACHAMP-25-OKC", 0.24, 33000.0),
    ("DEN", "KXNBACHAMP-25-DEN", 0.19, 27000.0),
    ("NYK", "KXNBACHAMP-25-NYK", 0.17, 21000.0),
    ("LAL", "KXNBACHAMP-25-LAL", 0.14, 16000.0),
)


@pytest.fixture
def kernel_db(tmp_path):
    """A temp kernel DB. The route and job call ``init_kernel_db()`` with no
    argument, which returns early because the engine is already set, so they
    read this one."""
    db_path = tmp_path / "futures_links_degraded.db"
    close_kernel_db()
    init_kernel_db(str(db_path))
    yield
    close_kernel_db()


@pytest.fixture
def client(kernel_db, monkeypatch):
    """Phase 12 enabled, server exceptions surfaced as 500 rather than raised.

    ``app.main`` registers no exception handler, so a read error escaping a
    route is a 500 -- which ``FuturesDashboard`` / ``futures-legs-table`` /
    ``futures-series-panel`` each already render through their SWR ``error``
    branch. ``raise_server_exceptions=False`` is what lets the test assert that
    status rather than catching the exception.
    """
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", True)
    return TestClient(app, raise_server_exceptions=False)


def _sql(stmt: str) -> None:
    """Real DDL through the live ORM session, visible to later ORM reads."""
    session = get_kernel_session()
    try:
        session.execute(text(stmt))
        session.commit()
    finally:
        session.close()


def _seed_book() -> list[dict]:
    """The five legs plus two snapshot generations each, via the store's writes.

    Two generations so ``get_latest_snapshots``' max(captured_at) subquery has
    something to choose; the newer one carries the book price.
    """
    store = FuturesLinkStore()
    links = []
    for team, ticker, price, liquidity in BOOK:
        link = store.upsert_link(
            competition=COMPETITION, season=SEASON, team=team,
            contract_id=ticker, source="kalshi",
            market_question=f"NBA Championship - {team}",
            implied_prob=price, verified=True,
        )
        store.append_snapshot(
            link_id=link["id"], implied_prob=0.11, price=0.11,
            liquidity=9000.0, volume=30000.0, captured_at=NOW - timedelta(days=1),
        )
        store.append_snapshot(
            link_id=link["id"], implied_prob=price, price=price,
            liquidity=liquidity, volume=120000.0, captured_at=NOW,
        )
        links.append(link)
    return links


#: Every store read, as ``(name, thunk)``.
READS = (
    ("get_links", lambda: FuturesLinkStore().get_links(COMPETITION, SEASON)),
    ("get_verified_links", lambda: FuturesLinkStore().get_verified_links()),
    ("get_latest_snapshots",
     lambda: FuturesLinkStore().get_latest_snapshots(COMPETITION, SEASON)),
)

#: Every route that reads the links table, most specific path first.
ROUTES = (
    "/api/futures",
    "/api/futures/meta/coverage",
    f"/api/futures/{COMPETITION}/{SEASON}",
    f"/api/futures/{COMPETITION}/{SEASON}/latest",
)


def test_a_readable_empty_table_still_answers_no_pairs(client):
    """Cold start keeps its answer: the fix must not turn "no rows" into an error.

    This is the state the live tables are actually in -- ``kernel_futures_links``
    and ``kernel_futures_snapshots`` both hold zero rows and
    PHASE12_FUTURES_MARKETS_ENABLED is unset. Without this test, "raises on a
    dropped table" would also pass for reads that raised on an empty one, and
    the dashboard would show an error where it should show 暂无期货市场数据.
    """
    for name, read in READS:
        assert read() == [], name
    for route in ROUTES:
        resp = client.get(route)
        assert resp.status_code == 200, route
    assert client.get("/api/futures").json() == {"pairs": []}
    coverage = client.get("/api/futures/meta/coverage").json()
    assert coverage["pair_count"] == 0
    assert coverage["linked_competitions"] == []
    assert coverage["status_counts"] == {}


def test_the_seeded_book_reaches_every_read_and_route(client):
    """The healthy half, which makes the degraded assertions discriminating.

    A five-leg book summing to 1.02 reports ``status="ok"``; zero legs cannot,
    because ``multi_leg_integrity`` maps ``leg_count < 2`` to ``too_few_legs``
    -> ``"incomplete"``. So "ok over five legs" vs "incomplete over none" is a
    real difference rather than a coincidence of thresholds.
    """
    _seed_book()
    store = FuturesLinkStore()
    assert len(store.get_links(COMPETITION, SEASON)) == 5
    assert len(store.get_verified_links()) == 5
    latest = store.get_latest_snapshots(COMPETITION, SEASON)
    assert len(latest) == 5
    # The newer generation wins: the book price, not the 0.11 filler.
    assert {s["team"]: s["implied_prob"] for s in latest} == {
        team: price for team, _, price, _ in BOOK
    }

    pairs = client.get("/api/futures").json()["pairs"]
    assert len(pairs) == 1
    assert pairs[0]["verified_count"] == 5
    assert pairs[0]["integrity"]["status"] == "ok"

    coverage = client.get("/api/futures/meta/coverage").json()
    assert coverage["pair_count"] == 1
    assert coverage["status_counts"] == {"ok": 1}
    assert coverage["linked_competitions"] == [COMPETITION]

    links_body = client.get(f"/api/futures/{COMPETITION}/{SEASON}").json()
    assert len(links_body["links"]) == 5
    assert links_body["integrity"]["status"] == "ok"
    assert links_body["integrity"]["sum_implied_prob"] == 1.02

    latest_body = client.get(f"/api/futures/{COMPETITION}/{SEASON}/latest").json()
    assert len(latest_body["snapshots"]) == 5
    assert latest_body["integrity"]["status"] == "ok"


def test_every_read_raises_when_the_links_table_is_gone(client, subtests):
    """A dropped links table is not "Kalshi has linked nothing yet".

    All three reads swallowed the failure into ``[]``, so every route answered
    200 with zero pairs and ``integrity.status="incomplete"`` -- the exact
    answer for a season nobody has linked -- while ``upsert_link`` on the same
    table raised. That asymmetry is asserted below: pre-fix this store said
    "the table is fine, there is just nothing in it" and "the table is gone"
    about the same table.
    """
    _seed_book()
    _sql(f"DROP TABLE {LINKS}")

    for name, read in READS:
        with subtests.test(read=name):
            with pytest.raises(OperationalError, match="no such table"):
                read()
    for route in ROUTES:
        with subtests.test(route=route):
            assert client.get(route).status_code == 500

    with pytest.raises(OperationalError, match="no such table"):
        FuturesLinkStore().upsert_link(
            competition=COMPETITION, season=SEASON, team="BOS",
            contract_id="KXNBACHAMP-25-BOS", source="kalshi",
            market_question="q", implied_prob=0.28, verified=True,
        )


def test_every_read_raises_when_the_links_column_drifts(client, subtests):
    """One renamed column, and the links reads are unanimous -- by construction.

    Both link reads build the full row dict (``_link_row_to_dict``) and
    ``get_latest_snapshots`` reads the links first, so a rename of
    ``implied_prob`` fails all three rather than leaving a survivor to report a
    healthy leg count over unreadable rows.
    """
    _seed_book()
    _sql(f"ALTER TABLE {LINKS} RENAME COLUMN implied_prob TO implied_prob_old")

    for name, read in READS:
        with subtests.test(read=name):
            with pytest.raises(OperationalError, match="no such column"):
                read()
    for route in ROUTES:
        with subtests.test(route=route):
            assert client.get(route).status_code == 500


@pytest.mark.parametrize("ddl,match", [
    (f"DROP TABLE {SNAPSHOTS}", "no such table"),
    (
        f"ALTER TABLE {SNAPSHOTS} RENAME COLUMN implied_prob TO implied_prob_old",
        "no such column",
    ),
])
def test_an_unreadable_snapshots_table_is_the_asymmetric_case(client, ddl, match):
    """The sharpest shape: the links read survives, only the snapshots read fails.

    Pre-fix the two routes for one pair disagreed while both answered 200:
    ``/futures/nba/2024-25`` reported ``status="ok"`` over five verified legs and
    ``/futures/nba/2024-25/latest`` beside it reported ``snapshots: []`` with
    ``status="incomplete"``, indistinguishable from a pair whose first capture
    has not run. ``append_snapshot`` on the same broken table raised, which is
    how the operator saw anything at all -- as ``capture_snapshots errors=5``.
    """
    _seed_book()
    _sql(ddl)

    store = FuturesLinkStore()
    assert len(store.get_links(COMPETITION, SEASON)) == 5
    assert len(store.get_verified_links()) == 5
    with pytest.raises(OperationalError, match=match):
        store.get_latest_snapshots(COMPETITION, SEASON)

    assert client.get(f"/api/futures/{COMPETITION}/{SEASON}").status_code == 200
    assert client.get(f"/api/futures/{COMPETITION}/{SEASON}/latest").status_code == 500

    with pytest.raises(OperationalError):
        store.append_snapshot(
            link_id=1, implied_prob=0.28, price=0.28,
            liquidity=41000.0, volume=120000.0, captured_at=NOW,
        )


def test_latest_snapshots_returns_empty_for_a_pair_with_no_links(client):
    """The ``if not links`` early return survives the fix, and must.

    No snapshot query runs in that branch, so ``[]`` is a fact about the pair
    rather than a swallowed failure -- and the snapshots table being unreadable
    cannot change it. Asserted with the snapshots table dropped so the test
    fails if the early return is ever replaced by an unconditional query.
    """
    _seed_book()
    _sql(f"DROP TABLE {SNAPSHOTS}")
    assert FuturesLinkStore().get_latest_snapshots("mlb", "2024") == []
    resp = client.get("/api/futures/mlb/2024/latest")
    assert resp.status_code == 200
    assert resp.json()["snapshots"] == []


@pytest.fixture
def ledger(kernel_db, monkeypatch):
    """Phase 12 enabled, capturing what the snapshot job writes to the ledger."""
    monkeypatch.setattr(settings, "PHASE12_FUTURES_MARKETS_ENABLED", True)
    rows = []

    def fake_finish(run_id, status, *, result=None, error=None, exc=None):
        rows.append({"status": status, "result": result, "error": error})

    with patch("app.core.scheduler._start_run", return_value="run-1"), \
         patch("app.core.scheduler._finish_run", side_effect=fake_finish):
        yield rows


def _kalshi_fetch() -> AsyncMock:
    """The Kalshi fetch the capture job re-runs for fresh prices."""
    return AsyncMock(return_value=[{
        "competition": COMPETITION,
        "championship_type": "NBA Championship",
        "source": "kalshi",
        "title": f"Who will win the {SEASON} NBA Championship?",
        "contracts": [
            {"team": team, "ticker": ticker, "price": price,
             "liquidity": liquidity, "volume": 120000.0}
            for team, ticker, price, liquidity in BOOK
        ],
    }])


@pytest.mark.asyncio
async def test_an_unreadable_links_table_fails_the_snapshot_run(ledger):
    """A dropped table must not be reported as "nothing is linked".

    ``capture_snapshots`` gates on ``get_verified_links()`` and returns
    ``{"captured": 0, "errors": 0}`` for an empty list, which
    ``_job_capture_futures_snapshots`` wrote as ``status="success"``. So the
    operator's only view of the run was a green row identical to an idle one.
    """
    _seed_book()
    _sql(f"DROP TABLE {LINKS}")

    with patch(
        "app.kernel.futures_market_service.fetch_kalshi_futures_markets",
        _kalshi_fetch(),
    ):
        await _job_capture_futures_snapshots()

    final = ledger[-1]
    assert final["status"] == "failed"
    assert "no such table" in (final["error"] or "")


@pytest.mark.asyncio
async def test_a_readable_empty_table_still_reports_a_healthy_idle_run(ledger):
    """Cold start keeps its ledger row: nothing linked yet is not an error."""
    with patch(
        "app.kernel.futures_market_service.fetch_kalshi_futures_markets",
        _kalshi_fetch(),
    ):
        await _job_capture_futures_snapshots()

    final = ledger[-1]
    assert final["status"] == "success"
    assert final["result"] == {"captured": 0, "errors": 0}
    assert final["error"] is None





