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
from collections.abc import Iterable
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


def group_liquidity_factor(
    liquidities: Iterable[float | None], *, floor: float
) -> float | None:
    """One venue-group's liquidity factor, or None meaning "do not penalize".

    THE single rule for "how deep is this group of venues", shared by
    ``EdgeDetectorService._compute_liquidity_factor`` and
    ``compute_match_liquidity_factor``. Those two had drifted three ways while
    the second one's docstring claimed their semantics mirrored each other:

    - the mixed case. Both took ``max`` over the *measured* subset, so one
      unmeasured venue beside a $100 market scored as though the group were a
      $100 market. The edge path returned 0.02 where an unmeasured venue alone
      returned 1.0, and this path returned 0.01 where an unmeasured venue alone
      returned None. Learning that some *other* venue is thin cut the factor 50x
      and 100x respectively, having learned nothing about the first venue.
    - a link with **no snapshot at all**. The edge path read that as unmeasured
      (``snap`` is None -> liquidity None -> no penalty); this path ``continue``d
      and dropped the link, letting a measured venue decide alone.
    - and after the edge path was fixed, the two disagreed outright: the same
      ``[unmeasured, $100]`` group scored 1.0 there and 0.01 here.

    Returns None when **any** venue publishes no usable depth — including the
    all-unmeasured case both functions already treated as "no penalty". A venue
    whose depth is not published has an *unknowable* factor, and arithmetic
    cannot supply one; declining to penalize it is the policy stated at every
    other liquidity site in this repo (``diagnosis_service.liquidity_factor``,
    and ``market_quality_service`` excluding a missing sub-score from its
    average). Callers differ only in how they render None: the edge detector
    multiplies, so it uses 1.0; the FeatureSet feed omits the key so
    ``odds_quality`` applies no penalty either.

    ``floor`` is a parameter, not read from config here, because the edge
    detector deliberately keeps its own (5000) decoupled from
    DIAGNOSIS_LIQUIDITY_FLOOR (10000) — coupling them once let a config change in
    the diagnosis pipeline silently flatten every edge's liquidity factor. The
    *rule* is shared; the *scale* is not.
    """
    measured: list[float] = []
    for raw in liquidities:
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        measured.append(value)

    if not measured:
        # No venues at all — nothing to penalize.
        return None

    if floor <= 0:
        return 1.0
    return min(max(measured) / floor, 1.0)


def compute_match_liquidity_factor(match_id: str) -> float | None:
    """Return liquidity_factor for a match, or None if unmeasured.

    None means: no verified links, or **any** verified link whose venue publishes
    no usable depth — engines should not penalize what cannot be measured, so the
    caller omits the key and odds_quality applies no penalty. Delegates to
    ``group_liquidity_factor`` so this and the Edge detector cannot drift; a link
    with no snapshot counts as unmeasured here rather than being dropped, which
    is what the Edge detector already did.

    A failed *read* is not one of those cases and escapes. ``None`` is this
    function's cold-start answer — both live tables hold zero rows — so
    swallowing a query failure into it asserted "this market's depth is
    unmeasurable" on the strength of a query that never reached the table. The
    consequence is an inverted alarm rather than a missing number: an omitted
    key means ``market_quality_damp`` applies no damp at all, so on a temp
    kernel DB holding one verified link over a thin $100 market
    (DIAGNOSIS_LIQUIDITY_FLOOR 5,000 -> factor 0.02) breaking the links table
    moved ``compute_confidence`` **up**, 0.5174 -> 0.5405, above the value the
    real thin market earns.

    ``SportMarketLinkStore.get_verified_links`` already raises on an unreadable
    table; this handler caught that and converted it straight back, so the
    inner fix was unobservable through every door here.
    """
    if not match_id:
        return None
    from app.kernel.market_snapshot_store import MarketSnapshotStore
    from app.kernel.sport_market_link_store import SportMarketLinkStore

    links = SportMarketLinkStore().get_verified_links(match_id=match_id)
    if not links:
        return None

    snap_store = MarketSnapshotStore()
    liquidities: list[float | None] = []
    for link in links:
        snap = snap_store.get_latest_snapshot(link_id=link["id"])
        liquidities.append(snap.get("liquidity") if snap else None)

    factor = group_liquidity_factor(liquidities, floor=_liquidity_floor())
    if factor is None:
        return None
    return round(factor, 4)


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
