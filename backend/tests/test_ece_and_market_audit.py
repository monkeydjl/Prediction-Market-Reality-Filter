"""P1-X1 ECE + P1-V1 market audit unit tests."""
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
