"""A stored timestamp must reach the wire with an offset -- two layers, one rule.

Audit 53.184. The `.isoformat()` rule in ``test_row_timestamp_serialization.py``
reads *one module's source* and looks for *a call*. These sites have neither
property, which is why they survived five sweeps:

* ``app/kernel/kernel_db.py`` formats the stamp itself, but it is a store, not a
  route module -- nothing in that rule reached ``app/kernel/`` at all. (It can
  now be listed there; ``market_settlement_store`` still cannot, see below.)
* ``market_settlement_store._settlement_to_dict`` does not format anything: it
  hands out the ``datetime`` object and ``jsonable_encoder`` renders it. There
  is no call site for a call-site rule to find -- the third step of the ladder
  in 53.183.

Both modules do reach a response: ``/predictions/history``,
``/predictions/history/{match_id}`` and the three ``/sport-settlements`` GETs
return these dicts as their body. Both are behind a feature flag that defaults
to ``false``, which is why 53.183's open list read them as consumer-less: a 503
answering "feature off" looks exactly like "no route exists". This file turns
the flags on and asserts what the wire then carries.

53.188 added a third family to the same fixture: ``/sport-optimization
/live-evidence`` returned ``latest_settled_at`` as a bare stamp, because its
service formatted the value with a private ``_isoformat`` helper of its own --
the only one in ``app/``, which is why grepping for ``utc_isoformat`` would
never have found it. Same flag shape (defaults to false, so 503), same silence.

Two layers, because either alone is defeatable:

* **type level** -- the converter's return value carries an offset. A new key
  added to the dict is not covered here, but a wrong value type is caught at
  the source.
* **wire level** -- every ISO-shaped string anywhere in the body carries one.
  Key-by-key assertions miss the seventh key; a recursive scan does not.

And two anti-vacuity guards, because both layers pass trivially on an empty
payload: the raw column really is naive (so the converter had something to
fix), and the body really contains a stamp (so the scan ran over something).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.kernel.kernel_db import (
    KernelMarketCalibration,
    KernelMarketSettlement,
    KernelMatchOutcome,
    KernelPrediction,
    KernelPredictionHistory,
    close_kernel_db,
    get_kernel_session,
    get_prediction_history,
    get_prediction_history_by_match,
    init_kernel_db,
)
from app.kernel.market_settlement_store import MarketSettlementStore

# 13:00 UTC, the value every assertion below looks for. Chosen away from
# midnight so an 8-hour shift is visible in the date as well as the clock.
AT = datetime(2026, 6, 20, 13, 0, tzinfo=timezone.utc)

# ISO shape without requiring an offset -- this is deliberately loose, so a
# stamp that lost its offset still matches and gets inspected.
_ISO_SHAPED = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


@pytest.fixture
def kernel_db(tmp_path):
    close_kernel_db()
    init_kernel_db(str(tmp_path / "kernel_timestamp_wire.db"))
    yield
    close_kernel_db()


def _seed(kernel_db) -> None:
    """One of everything the five endpoints read.

    Producer-side detail that matters: the values written here are **aware**.
    Everything the wire gets wrong afterwards comes from SQLite dropping the
    offset on the way back out, not from a careless writer.
    """
    session = get_kernel_session()
    session.add(KernelPrediction(
        match_id="nba-1", sport="basketball", competition="nba", season="2025",
        engine="basketball", predicted_scores={"home": 112, "away": 108},
        outcome_probabilities={"home_win": 0.62, "away_win": 0.38},
        confidence=0.6, feature_version="nba-1.0", explanation=[],
        created_at=AT, updated_at=AT,
    ))
    session.add(KernelPredictionHistory(
        match_id="nba-1", engine="basketball",
        predicted_scores={"home": 112, "away": 108},
        outcome_probabilities={"home_win": 0.62}, confidence=0.6,
        feature_version="nba-1.0", trigger="initial", created_at=AT,
    ))
    session.add(KernelMatchOutcome(
        match_id="nba-1", home_score=110, away_score=105, outcome="home_win",
        engine="basketball", score_mae=2.5, outcome_correct=1, brier_score=0.18,
        finished_at=AT,
    ))
    session.add(KernelMarketSettlement(
        match_id="nba-1", mapped_outcome="home_win", engine="basketball",
        competition="nba", settlement_implied_prob=0.71,
        settlement_captured_at=AT, link_id=1, model_prob=0.62,
        market_prob_at_detection=0.66, raw_edge=0.04, adjusted_edge=0.03,
        brier_score=0.09, signed_error=-0.09, direction_correct=1,
        status="processed", skip_reason=None, match_finished_at=AT,
        processed_at=AT,
    ))
    session.add(KernelMarketCalibration(
        engine="basketball", competition="nba", slope=1.0, intercept=0.0,
        sample_count=12, avg_brier=0.2, avg_signed_error=0.0,
        direction_accuracy=0.58, last_updated=AT,
    ))
    session.commit()
    session.close()


@pytest.fixture
def client(kernel_db, monkeypatch):
    """Three families mounted, every feature flag on.

    The flags default to ``false``; turning them on is the whole point -- the
    handler code path is what was never exercised, and it is the same code that
    runs the day an operator flips them.

    ``sport_optimization`` joined in 53.188: its ``/live-evidence`` body carried
    a bare ``latest_settled_at`` because the service formatted it with a private
    ``_isoformat`` helper instead of ``utc_isoformat``. The route's own test
    could not see it -- it monkeypatches ``build_live_evidence_report`` with a
    literal, so the real body never reaches the wire there.
    """
    monkeypatch.setattr(config.settings, "KERNEL_PREDICTION_ENABLED", True)
    monkeypatch.setattr(
        config.settings, "PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED", True
    )
    monkeypatch.setattr(config.settings, "PHASE9_ACCURACY_SPRINT_ENABLED", True)
    from app.api.routes import predictions, sport_optimization, sport_settlements

    app = FastAPI()
    app.include_router(predictions.router, prefix="/api")
    app.include_router(sport_settlements.router, prefix="/api")
    app.include_router(sport_optimization.router, prefix="/api")
    _seed(kernel_db)
    return TestClient(app)


def _stamp_strings(payload: Any) -> list[str]:
    """Every ISO-shaped string anywhere in a decoded JSON body."""
    found: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            found.extend(_stamp_strings(value))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(_stamp_strings(value))
    elif isinstance(payload, str) and _ISO_SHAPED.match(payload):
        found.append(payload)
    return found


def _assert_every_stamp_is_zoned(payload: Any) -> None:
    """Fail unless every stamp parses to an aware datetime.

    Parses instead of suffix-matching on purpose: the two producers disagree on
    spelling -- ``kernel_db`` pre-formats to ``+00:00``, the settlement store
    hands out a ``datetime`` that the encoder renders as ``Z``. Both are
    correct, so the assertion has to be about the offset, not its notation.
    """
    stamps = _stamp_strings(payload)
    assert stamps, f"no timestamp in {payload!r} -- this assertion proved nothing"
    for stamp in stamps:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, (
            f"{stamp!r} has no offset; a browser reads it as local time and "
            "in UTC+8 that is an eight-hour error with no symptom but a wrong clock"
        )


def _raw(column_ref: str) -> Any:
    """Read one column straight from the table, bypassing the converters."""
    session = get_kernel_session()
    try:
        model, column = column_ref.split(".")
        row = session.query(
            {"history": KernelPredictionHistory,
             "outcome": KernelMatchOutcome,
             "settlement": KernelMarketSettlement,
             "calibration": KernelMarketCalibration}[model]
        ).first()
        return getattr(row, column)
    finally:
        session.close()


class TestTheRawColumnsAreNaive:
    """Anti-vacuity: without this, a converter that touched nothing still passes.

    If SQLite ever starts handing back aware datetimes, every assertion in this
    file becomes true for free and stops meaning anything -- these tests are the
    tripwire for that day.
    """

    @pytest.mark.parametrize("column_ref", [
        "history.created_at",
        "outcome.finished_at",
        "settlement.settlement_captured_at",
        "settlement.match_finished_at",
        "settlement.processed_at",
        "calibration.last_updated",
    ])
    def test_column_really_comes_back_without_an_offset(self, kernel_db, column_ref):
        _seed(kernel_db)
        value = _raw(column_ref)
        assert isinstance(value, datetime), value
        assert value.tzinfo is None, (
            f"{column_ref} is aware in this SQLite build; the conversion below is "
            "now untested, not unnecessary"
        )


class TestConvertersReturnZonedValues:
    """Type level: the value that leaves the store already knows its zone."""

    @pytest.mark.parametrize("reader", ["list", "by_match"])
    def test_prediction_history_stamps_carry_an_offset(self, kernel_db, reader):
        _seed(kernel_db)
        if reader == "list":
            items, total = get_prediction_history(limit=5, offset=0)
            assert total == 1
            stamps = [items[0]["created_at"], items[0]["outcome"]["finished_at"]]
        else:
            stamps = [
                row["created_at"]
                for row in get_prediction_history_by_match("nba-1")["items"]
            ]
        assert stamps and all(s is not None for s in stamps)
        for stamp in stamps:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            assert parsed.tzinfo is not None, stamp
            assert parsed.astimezone(timezone.utc) == AT, stamp

    def test_settlement_store_stamps_are_aware_datetimes(self, kernel_db):
        _seed(kernel_db)
        store = MarketSettlementStore()
        settlements = store.get_history(limit=5)
        assert len(settlements) == 1
        for key in ("settlement_captured_at", "match_finished_at", "processed_at"):
            value = settlements[0][key]
            assert isinstance(value, datetime), (key, value)
            assert value.tzinfo is not None, (key, value)
            assert value == AT, (key, value)
        calibrations = store.get_calibrations()
        assert len(calibrations) == 1
        last_updated = calibrations[0]["last_updated"]
        assert isinstance(last_updated, datetime), last_updated
        assert last_updated.tzinfo is not None, last_updated
        # ``get_settlement`` shares the converter with ``get_history``; pinning
        # it too is what tells the two apart if one is ever rewired.
        for key in ("settlement_captured_at", "match_finished_at", "processed_at"):
            assert store.get_settlement("nba-1")[0][key].tzinfo is not None, key


class TestTheWireCarriesTheOffset:
    """Wire level: the only layer that also covers keys added later."""

    @pytest.mark.parametrize("url", [
        "/api/predictions/history?limit=5",
        "/api/predictions/history/nba-1",
        "/api/sport-settlements/history?limit=5",
        "/api/sport-settlements/calibrations",
        "/api/sport-settlements/nba-1",
        "/api/sport-optimization/live-evidence",
    ])
    def test_body_stamps_carry_an_offset(self, client, url):
        response = client.get(url)
        assert response.status_code == 200, response.text
        _assert_every_stamp_is_zoned(response.json())

    def test_live_evidence_stamp_is_the_same_instant(self, client):
        """Zoned is half the claim; the other half is *which* instant.

        ``utc_isoformat`` converts a naive value by assuming UTC, while a
        ``replace(tzinfo=...)`` would keep the wall clock and move the instant.
        Both pass a zone-only assertion, so this reads the value back.
        """
        body = client.get("/api/sport-optimization/live-evidence").json()
        stamp = body["groups"][0]["latest_settled_at"]
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        assert parsed.astimezone(timezone.utc) == AT, stamp

    def test_the_scan_sees_the_stamps_it_claims_to_guard(self, client):
        """Anti-vacuity for the scan itself, per endpoint.

        ``_assert_every_stamp_is_zoned`` passes on a body with no stamps at all,
        so counting first is what separates "guarded" from "nothing to guard".
        The counts are per endpoint because each returns a different subset.
        """
        expected = {
            "/api/predictions/history?limit=5": 2,     # created_at + outcome.finished_at
            "/api/predictions/history/nba-1": 1,
            "/api/sport-settlements/history?limit=5": 3,
            "/api/sport-settlements/calibrations": 1,
            "/api/sport-settlements/nba-1": 3,
            "/api/sport-optimization/live-evidence": 1,  # latest_settled_at
        }
        for url, count in expected.items():
            body = client.get(url).json()
            assert len(_stamp_strings(body)) == count, (url, _stamp_strings(body))


def test_the_zone_assertion_rejects_a_bare_stamp() -> None:
    """Positive control: the guard can fail.

    Every other test here asserts that something is zoned; if
    ``_assert_every_stamp_is_zoned`` were wrong in the permissive direction,
    all of them would pass and none would detect the defect that this section
    exists for. This is the one input that must be rejected.
    """
    with pytest.raises(AssertionError, match="has no offset"):
        _assert_every_stamp_is_zoned({"items": [{"created_at": "2026-06-20T13:00:00"}]})
    # And the two spellings a real producer emits both pass.
    _assert_every_stamp_is_zoned({"a": "2026-06-20T13:00:00Z",
                                  "b": "2026-06-20T13:00:00+00:00"})
