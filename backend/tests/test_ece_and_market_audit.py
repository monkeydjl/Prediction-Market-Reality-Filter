"""P1-X1 ECE + P1-V1 market audit unit tests."""
from datetime import datetime, timezone

import pytest

from app.kernel.market_snapshot_store import MarketSnapshotStore


def test_audit_summary_method_exists():
    assert hasattr(MarketSnapshotStore, "audit_summary")


def test_reliability_curve_reports_ece():
    """The reliability contract, asserted on values rather than on source text.

    This used to grep compute_reliability_bins' source for the strings "ece" /
    "max_calibration_error" / "sample_count", which pinned nothing: the ECE
    value itself was never checked, and the assertions broke the moment the
    binning moved into a shared helper without any behavior change. Bin-level
    and endpoint-level coverage lives in tests/test_confidence_reliability.py.
    """
    from app.kernel.kernel_db import _reliability_curve

    # bin 2: avg_p 0.2 vs avg_a 0.5 (2 samples); bin 8: 0.8 vs 1.0 (1 sample)
    curve = _reliability_curve([(0.2, 0.0), (0.2, 1.0), (0.8, 1.0)], 10)
    assert curve["ece"] == pytest.approx((2 * 0.3 + 1 * 0.2) / 3, abs=1e-4)
    assert curve["max_calibration_error"] == pytest.approx(0.3, abs=1e-4)
    assert curve["sample_count"] == 3


def test_audit_summary_empty_shape():
    """Unit-level: method callable shape when no DB rows (best-effort)."""
    store = MarketSnapshotStore()
    # Monkeypatch get_snapshots to avoid DB
    store.get_snapshots = lambda **kw: []  # type: ignore[method-assign]
    out = store.audit_summary(link_id=999999)
    assert out["available"] is False
    assert out["snapshot_count"] == 0
    assert "no_snapshots" in out["flags"]


def test_audit_summary_with_prices():
    store = MarketSnapshotStore()
    store.get_snapshots = lambda **kw: [  # type: ignore[method-assign]
        {"implied_prob": 0.40, "price": 0.40, "captured_at": "2026-01-01T00:00:00Z"},
        {"implied_prob": 0.55, "price": 0.55, "captured_at": "2026-01-02T00:00:00Z"},
        {"implied_prob": 0.50, "price": 0.50, "captured_at": "2026-01-03T00:00:00Z"},
    ]
    out = store.audit_summary(link_id=1)
    assert out["available"] is True
    assert out["snapshot_count"] == 3
    assert out["first_price"] == 0.4
    assert out["last_price"] == 0.5
    assert abs(out["delta_pp"] - 10.0) < 1e-6
    assert out["max_drawdown_pp"] >= 0


def test_audit_summary_untimestamped_row_does_not_become_an_endpoint():
    """A row with no timestamp is not a point on the price path.

    ``kernel_market_snapshots.captured_at`` is a nullable column, so a row
    without one is legal even though the store's own writer always fills it in
    (audit section 53.47). Before the fix the sort key mixed a ``datetime`` with
    the ``""`` fallback, so ``sorted`` raised TypeError -- a 500 on both
    /sport-markets/links/{id}/audit and /sport-markets/matches/{id}/audit, whose
    call sites do not catch. The assertion is on the endpoint *values*, not on
    "no exception": a row that silently took over ``last`` would pass the
    weaker check.
    """
    store = MarketSnapshotStore()
    older = datetime(2020, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2020, 6, 1, tzinfo=timezone.utc)
    store.get_snapshots = lambda **kw: [  # type: ignore[method-assign]
        {"id": 1, "implied_prob": 0.40, "price": 0.40, "captured_at": older},
        {"id": 2, "implied_prob": 0.90, "price": 0.90, "captured_at": None},
        {"id": 3, "implied_prob": 0.50, "price": 0.50, "captured_at": newer},
    ]
    out = store.audit_summary(link_id=1)
    assert out["available"] is True
    assert out["first_captured_at"] == older
    assert out["last_captured_at"] == newer
    assert out["first_price"] == 0.40
    assert out["last_price"] == 0.50
    assert out["snapshot_count"] == 3
    assert out["untimestamped_count"] == 1
    assert "untimestamped_snapshots" in out["flags"]


def test_audit_summary_all_untimestamped_rows_are_not_available():
    """With nothing to place on the axis there is no price path to report."""
    store = MarketSnapshotStore()
    store.get_snapshots = lambda **kw: [  # type: ignore[method-assign]
        {"id": 1, "implied_prob": 0.40, "price": 0.40, "captured_at": None},
        {"id": 2, "implied_prob": 0.50, "price": 0.50, "captured_at": None},
    ]
    out = store.audit_summary(link_id=1)
    assert out["available"] is False
    assert out["snapshot_count"] == 2
    assert out["untimestamped_count"] == 2
    assert out["flags"] == ["untimestamped_snapshots"]


def test_audit_summary_mixed_timestamp_types_still_order_chronologically():
    """One `datetime` series and one ISO-string series both occur here.

    Real reads hand back ``datetime``; older fixtures (including the one in
    ``test_audit_summary_with_prices``) supply ISO strings. A sort key that
    returned whatever it was given therefore had a type that depended on the
    input, and mixing the two raised TypeError.
    """
    store = MarketSnapshotStore()
    store.get_snapshots = lambda **kw: [  # type: ignore[method-assign]
        {"id": 1, "implied_prob": 0.40, "price": 0.40,
         "captured_at": "2020-01-02T00:00:00Z"},
        {"id": 2, "implied_prob": 0.50, "price": 0.50,
         "captured_at": datetime(2020, 1, 1, tzinfo=timezone.utc)},
    ]
    out = store.audit_summary(link_id=1)
    assert out["available"] is True
    # 2020-01-01 is the earlier of the two, whichever type it arrived as.
    assert out["first_price"] == 0.50
    assert out["last_price"] == 0.40
