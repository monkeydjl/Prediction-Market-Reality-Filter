"""P1-X1 ECE + P1-V1 market audit unit tests."""
from app.kernel.market_snapshot_store import MarketSnapshotStore


def test_audit_summary_method_exists():
    assert hasattr(MarketSnapshotStore, "audit_summary")


def test_reliability_source_has_ece():
    import inspect
    from app.kernel.kernel_db import compute_reliability_bins

    src = inspect.getsource(compute_reliability_bins)
    assert "ece" in src
    assert "max_calibration_error" in src
    assert "sample_count" in src


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
