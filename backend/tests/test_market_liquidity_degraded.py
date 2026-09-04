"""What an unreadable sport-market links table does to the liquidity factor.

``compute_match_liquidity_factor`` wrapped two store reads in one
``except Exception: return None``. ``None`` is also its normal answer — "no
verified links, or a venue that publishes no depth" — and both live tables hold
**zero rows**, so that is the state every caller is in today.

The consequence is an inverted alarm, not a missing number. An omitted
``liquidity_factor`` means ``market_quality_damp`` applies no damp at all, so
breaking the links table moved ``compute_confidence`` **up**, past the value the
real thin market earns:

    healthy, $100 market, floor 5000 -> factor 0.02 -> confidence 0.5174
    links table unreadable           -> key omitted -> confidence 0.5405

``SportMarketLinkStore.get_verified_links`` has raised since the store's own
reads were fixed; this handler caught that and converted it straight back, so
that fix was unobservable through every door here. Measured through all of them
on a temp kernel DB seeded via the stores' own writes.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.kernel.domain import (
    CompetitionIdentity,
    MatchIdentity,
    SeasonIdentity,
    SportIdentity,
    TeamIdentity,
)
from app.kernel.engines.confidence import compute_confidence
from app.kernel.kernel_db import close_kernel_db, get_kernel_session, init_kernel_db
from app.kernel.market_liquidity import (
    compute_match_liquidity_factor,
    inject_liquidity_into_custom,
)
from app.kernel.market_snapshot_store import MarketSnapshotStore
from app.kernel.multi_feature_builder import MultiFeatureBuilder
from app.kernel.sport_market_link_store import SportMarketLinkStore
from app.sports.basketball.feature_builder import BasketballFeatureBuilder

MATCH_ID = "nba-liq-1"
#: $100 of depth against a 5,000 floor. Thin enough that the healthy factor
#: (0.02) damps confidence measurably, so "no penalty" is not a rounding
#: difference away from the real answer.
LIQUIDITY = 100.0
FLOOR = 5_000.0
EXPECTED_FACTOR = 0.02
#: Decisive but not certain, so the damp is the only thing separating the two
#: confidences below.
PROBS = {"home_win": 0.62, "away_win": 0.38}
CAPTURED = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def kernel_db(tmp_path, monkeypatch):
    """A temp kernel DB with the floor pinned, so 0.02 is not an env default."""
    monkeypatch.setattr(settings, "DIAGNOSIS_LIQUIDITY_FLOOR", FLOOR)
    close_kernel_db()
    init_kernel_db(str(tmp_path / "market_liquidity_degraded.db"))
    yield
    close_kernel_db()


def _sql(stmt: str) -> None:
    """Real DDL through the live ORM session, visible to later ORM reads."""
    session = get_kernel_session()
    try:
        session.execute(text(stmt))
        session.commit()
    finally:
        session.close()


def _seed_thin_market() -> int:
    """One verified link whose only snapshot is a thin market. Returns link id."""
    link = SportMarketLinkStore().upsert_link(
        match_id=MATCH_ID,
        contract_id="KXNBA-BOS-LAL-BOS",
        source="polymarket",
        outcome_label="YES",
        mapped_outcome="home_win",
        link_method="rule",
        link_confidence=0.95,
        verified=True,
        market_question="Will Boston beat the Lakers?",
        implied_prob=0.6,
    )
    MarketSnapshotStore().append_snapshot(
        link_id=link["id"],
        implied_prob=0.6,
        price=0.6,
        liquidity=LIQUIDITY,
        volume=250.0,
        captured_at=CAPTURED,
    )
    return int(link["id"])


def _match() -> MatchIdentity:
    sport = SportIdentity(code="basketball", name="Basketball")
    comp = CompetitionIdentity(code="nba", name="NBA", sport=sport)
    season = SeasonIdentity(competition=comp, season_key="2025-26")
    return MatchIdentity(
        match_id=MATCH_ID,
        season=season,
        stage="regular_season",
        round=None,
        home=TeamIdentity(code="BOS", name="Boston", competition=comp),
        away=TeamIdentity(code="LAL", name="Lakers", competition=comp),
        kickoff_utc=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )


def _break_links() -> None:
    _sql(
        "ALTER TABLE kernel_sport_market_links "
        "RENAME COLUMN verified TO verified_x"
    )


def test_a_thin_market_is_measured_and_damps_confidence(kernel_db):
    """The healthy reading, so the degraded ones below are comparable."""
    _seed_thin_market()
    assert compute_match_liquidity_factor(MATCH_ID) == EXPECTED_FACTOR

    custom = inject_liquidity_into_custom({}, MATCH_ID)
    assert custom["liquidity_factor"] == EXPECTED_FACTOR
    assert custom["liquidity_source"] == "sport_market_snapshots"
    assert round(compute_confidence(PROBS, custom=custom), 4) == 0.5174


def test_a_readable_but_empty_links_table_still_returns_none(kernel_db):
    """The reverse case: None is the honest answer for a cold-start table.

    Both live tables hold zero rows, so this is the state every caller is in
    today. Raising here — or letting a caller see a key — would be a worse
    defect than the swallow it replaces.
    """
    assert compute_match_liquidity_factor(MATCH_ID) is None
    assert inject_liquidity_into_custom({}, MATCH_ID) == {}
    assert "liquidity_factor" not in BasketballFeatureBuilder().build(
        _match(), {"team": {"elo_home": 1520, "elo_away": 1480}}
    ).custom


def test_an_unreadable_links_table_escapes_the_liquidity_factor(kernel_db):
    """The read that never reached the table must not answer "unmeasurable"."""
    _seed_thin_market()
    assert compute_match_liquidity_factor(MATCH_ID) == EXPECTED_FACTOR

    _break_links()
    with pytest.raises(OperationalError):
        compute_match_liquidity_factor(MATCH_ID)


def test_an_unreadable_links_table_escapes_every_liquidity_door(kernel_db):
    """Injection, both feature builders. None of these has an outer handler.

    The five sport feature builders inject inline inside the ``FeatureSet``
    constructor and ``MultiFeatureBuilder`` enriches on the way out, so the
    escape reaches the kernel. The sport *adapters* wrap their own call in
    ``except Exception: logger.debug(...)`` and stay silent — measured, and out
    of scope for this change.
    """
    _seed_thin_market()
    _break_links()
    match, raw = _match(), {"team": {"elo_home": 1520, "elo_away": 1480}}

    with pytest.raises(OperationalError):
        inject_liquidity_into_custom({}, MATCH_ID)
    with pytest.raises(OperationalError):
        BasketballFeatureBuilder().build(match, raw)
    with pytest.raises(OperationalError):
        MultiFeatureBuilder({"nba-": BasketballFeatureBuilder()}).build(match, raw)


def test_a_swallowed_read_would_raise_confidence_above_the_thin_market(kernel_db):
    """The direction that makes this a defect rather than a missing number.

    ``market_quality_damp`` applies **no** damp when ``liquidity_factor`` is
    absent, so swallowing the failed read does not lose a penalty — it awards a
    bonus over what the real thin market earns. Pinned as an inequality *and* as
    both values, so a future re-swallow anywhere on this path fails here.
    """
    _seed_thin_market()
    measured = round(compute_confidence(PROBS, custom=inject_liquidity_into_custom({}, MATCH_ID)), 4)
    swallowed = round(compute_confidence(PROBS, custom={}), 4)

    assert measured == 0.5174
    assert swallowed == 0.5405
    assert swallowed > measured

    _break_links()
    with pytest.raises(OperationalError):
        compute_confidence(PROBS, custom=inject_liquidity_into_custom({}, MATCH_ID))
