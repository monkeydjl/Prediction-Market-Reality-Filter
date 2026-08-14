"""Match-level market liquidity for prediction FeatureSet (P1-E4 feed).

Reads verified sport-market links + latest snapshots (same stores as
EdgeDetectorService) and produces a factor in (0, 1] for
``FeatureSet.custom["liquidity_factor"]``.

Semantics mirror EdgeDetectorService._compute_liquidity_factor:
- max liquidity across verified links' latest snapshots
- factor = min(max_liq / floor, 1.0)
- no measured liquidity → omit (callers leave custom untouched / no penalty)
"""
from __future__ import annotations

import logging
from typing import Any

from app.core import config

logger = logging.getLogger(__name__)


def _liquidity_floor() -> float:
    """USD-ish floor for full trust; default 10_000 if config unset."""
    raw = getattr(config.settings, "DIAGNOSIS_LIQUIDITY_FLOOR", None)
    if raw is None:
        raw = getattr(config.settings, "EDGE_LIQUIDITY_FLOOR", 10_000.0)
    if raw is None:
        # Both settings can exist and be None, which float() would only reject
        # via the TypeError below - name the case instead of catching it.
        return 10_000.0
    try:
        floor = float(raw)
    except (TypeError, ValueError):
        floor = 10_000.0
    return floor if floor > 0 else 10_000.0


def liquidity_factor_from_amount(max_liquidity: float, *, floor: float | None = None) -> float:
    """Scale raw liquidity dollars into [0, 1]."""
    thr = floor if floor is not None else _liquidity_floor()
    if thr <= 0:
        return 1.0
    if max_liquidity is None or max_liquidity <= 0:
        return 1.0
    return min(float(max_liquidity) / thr, 1.0)


def compute_match_liquidity_factor(match_id: str) -> float | None:
    """Return liquidity_factor for a match, or None if unmeasured.

    None means: no verified links or all snapshots lack liquidity —
    engines should not penalize (same as Edge detector returning 1.0
    only when *used* for edge; for FeatureSet we omit the key so
    odds_quality does not apply a zero-liquidity penalty).
    """
    if not match_id:
        return None
    try:
        from app.kernel.market_snapshot_store import MarketSnapshotStore
        from app.kernel.sport_market_link_store import SportMarketLinkStore

        links = SportMarketLinkStore().get_verified_links(match_id=match_id)
        if not links:
            return None

        snap_store = MarketSnapshotStore()
        liquidities: list[float] = []
        for link in links:
            snap = snap_store.get_latest_snapshot(link_id=link["id"])
            if not snap:
                continue
            liq = snap.get("liquidity")
            if liq is not None and float(liq) > 0:
                liquidities.append(float(liq))

        if not liquidities:
            return None

        factor = liquidity_factor_from_amount(max(liquidities))
        return round(factor, 4)
    except Exception:  # noqa: BLE001
        logger.debug(
            "liquidity_factor lookup failed for match_id=%s",
            match_id,
            exc_info=True,
        )
        return None


def inject_liquidity_into_custom(
    custom: dict[str, Any] | None,
    match_id: str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Return a new custom dict with liquidity_factor when available.

    Does not overwrite an explicit caller-provided liquidity_factor unless
    ``overwrite`` is True.
    """
    out: dict[str, Any] = dict(custom or {})
    if not overwrite and out.get("liquidity_factor") is not None:
        return out
    factor = compute_match_liquidity_factor(match_id)
    if factor is not None:
        out["liquidity_factor"] = factor
        out.setdefault("liquidity_source", "sport_market_snapshots")
    return out


def inject_odds_dispersion_from_store(
    custom: dict[str, Any] | None,
    match_id: str,
) -> dict[str, Any]:
    """Attach odds_dispersion from traditional multi-book snapshots (P1-O2)."""
    out: dict[str, Any] = dict(custom or {})
    if out.get("odds_dispersion") is not None or not match_id:
        return out
    try:
        from app.kernel.engines.odds_quality import (
            inject_odds_dispersion,
            odds_dispersion_from_books,
        )
        from app.kernel.traditional_odds_store import TraditionalOddsStore

        snaps = TraditionalOddsStore().get_snapshots(match_id=match_id)
        if not snaps:
            return out
        # Prefer latest capture per bookmaker for home (or first outcome)
        by_book: dict[str, dict[str, Any]] = {}
        # Use newest first
        ordered = sorted(
            snaps,
            key=lambda s: s.get("captured_at") or "",
            reverse=True,
        )
        for s in ordered:
            book = s.get("bookmaker") or "unknown"
            outcome = (s.get("mapped_outcome") or "").lower()
            if outcome not in {"home", "home_win", "1"}:
                # If only one outcome type stored, still use home-ish rows
                if outcome and outcome not in {"home", "home_win", "1", "h"}:
                    continue
            if book in by_book:
                continue
            odds = s.get("decimal_odds")
            if odds is None and s.get("implied_prob"):
                try:
                    odds = 1.0 / float(s["implied_prob"])
                except (TypeError, ValueError, ZeroDivisionError):
                    continue
            by_book[book] = {"odds_home": odds, "bookmaker": book}
        books = list(by_book.values())
        if len(books) < 2:
            # Fallback: multiple captures of same outcome across time as weak signal
            homes = [
                s for s in ordered
                if (s.get("mapped_outcome") or "").lower() in {"home", "home_win", "1", "h", ""}
            ][:8]
            books = [
                {"odds_home": s.get("decimal_odds")}
                for s in homes
                if s.get("decimal_odds")
            ]
        out = inject_odds_dispersion(out, books)
        # Also store book count for UI/debug
        if books:
            out.setdefault("odds_books_count", len(books))
            _ = odds_dispersion_from_books  # keep import used when inject no-ops
    except Exception:  # noqa: BLE001
        logger.debug(
            "odds_dispersion lookup failed for match_id=%s",
            match_id,
            exc_info=True,
        )
    return out


def enrich_feature_set_liquidity(features: Any) -> Any:
    """Return FeatureSet with custom.liquidity_factor + odds_dispersion when missing.

    FeatureSet is frozen — builds a shallow replacement via dataclasses.replace.
    """
    from dataclasses import replace

    from app.kernel.domain import FeatureSet

    if not isinstance(features, FeatureSet):
        return features
    match_id = features.match.match_id
    new_custom = inject_liquidity_into_custom(features.custom, match_id)
    new_custom = inject_odds_dispersion_from_store(new_custom, match_id)
    if new_custom == features.custom:
        return features
    return replace(features, custom=new_custom)
