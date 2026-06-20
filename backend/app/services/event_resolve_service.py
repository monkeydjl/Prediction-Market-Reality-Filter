"""event_resolve_service.py
=========================
Event-layer resolution: the shared resolve-with-calibration helper plus the
auto-resolve workflow.

`resolve_with_calibration` is the single resolution path used by both the
manual resolve endpoint and auto-resolve. It computes the calibration snapshot
from the event's probability trajectory, persists outcome + calibration onto
the record, and appends an outcome snapshot to the audit log. Centralizing it
here keeps the calibration computation in one place (no duplication between
manual and auto paths).

`auto_resolve_events` fetches resolved prediction markets from all sources
(Polymarket, Manifold, Kalshi), matches each
unresolved local event by question similarity (shared text_match utilities),
and resolves each match with a calibration snapshot. It mirrors the
market-layer auto_resolve_service but writes to the event store / event audit
instead of agent_memory / analysis_audit.

Event vocabulary only - no trading terms.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.memory.event_market_link_store import get_verified_link, upsert_link
from app.memory.event_store import get_event, list_all_events, resolve_event
from app.memory.prediction_store import get_prediction, score_prediction, void_prediction
from app.services.calibration_service_event import score_event
from app.services.event_audit_service import histories_by_event, history_for_event, record_outcome
from app.services.trend_analysis_service import analyze_trend
from app.utils.text_match import build_index, find_match, normalize

logger = logging.getLogger(__name__)


async def resolve_with_calibration(
    event_id: str,
    actual_outcome: float,
    confidence: float = 1.0,
    source: str = "manual",
    notes: str = "",
    snapshots: list[dict[str, Any]] | None = None,
    status: str = "resolved",
) -> dict[str, Any] | None:
    """Resolve an event with a settled outcome and a calibration snapshot.

    Scores the event's latest probability estimate against the outcome
    (trajectory context included), persists outcome + calibration onto the
    record, and appends an outcome snapshot to the audit log. Returns the
    updated entry, or None when event_id is not stored (callers raise 404).

    This is the single resolution path: the manual resolve endpoint and
    auto-resolve both call it, so the calibration logic lives in one place.

    `snapshots` optionally supplies this event's audit snapshots (any kind); the
    caller may pass them when resolving many events to avoid an N-times full
    audit-log read (auto-resolve passes one slice per event from a single
    histories_by_event() read). When None, the snapshots are read here.

    `status` is the outcome state. "resolved" is the normal case and gets a
    calibration score. A non-resolved status (e.g. "invalid", written when a
    verified link diverges - see auto_resolve_events) records the outcome marker
    but is NOT scored, so it never enters the calibration aggregate.

    A manual resolution is itself a human verification of identity, so it records
    a verified manual link as provenance (a machine match is the only thing the
    fail-closed gate distrusts).
    """
    entry = get_event(event_id)
    if entry is None:
        return None

    record = entry.get("record") or {}

    if source == "manual":
        upsert_link(
            event_id,
            market_name="manual",
            market_question=record.get("event_title", ""),
            # Persist the event's own resolution criteria (as the analysis
            # engine understood it) so a later audit can confirm a resolution
            # means what we predicted. M0 exit criteria: every prediction traces
            # to a verified contract AND a resolution-criteria string.
            resolution_criteria=(record.get("semantics") or {}).get("resolution_criteria", ""),
            link_method="manual",
            link_confidence=1.0,
            verified=True,
        )

    # Outcome snapshots are filtered out so "latest" is the latest probability
    # estimate, not a settlement marker. analyze_trend already skips
    # non-numeric estimates, but filtering here keeps the trajectory_observations
    # count honest (it counts probability snapshots, not outcome markers).
    raw_snapshots = snapshots if snapshots is not None else history_for_event(event_id)
    probability_snapshots = [
        snap for snap in raw_snapshots if snap.get("kind") != "outcome"
    ]
    trend = analyze_trend(probability_snapshots)
    if trend["latest_probability"] is not None:
        estimated = trend["latest_probability"]
    else:
        # No probability history yet: fall back to the record's baseline so
        # the event still gets a calibration score rather than None.
        estimated = (record.get("probability") or {}).get("baseline", 50.0)

    # Only a genuine resolution is scored. An invalid/void outcome records the
    # marker but carries no calibration, so it is excluded from Brier.
    if status == "resolved":
        calibration = score_event(
            estimated=estimated,
            actual_outcome=actual_outcome,
            trajectory_observations=trend["observations"],
            trajectory_span_hours=trend["span_hours"],
        )
    else:
        calibration = None

    outcome = {
        "status": status,
        "actual_outcome": actual_outcome,
        "confidence": confidence,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "notes": notes,
    }

    # Score the prediction (SQLite) BEFORE writing the event outcome (JSON).
    # These two stores have no shared transaction, so ordering decides what a
    # mid-resolve crash leaves behind. event_store.outcome is the "already
    # resolved -> skip next run" gate (auto_resolve_events), so if we wrote it
    # first and then crashed before scoring, the prediction would stay `open`
    # forever (the event is skipped on every future run): a silent, permanent
    # orphan. By scoring first, a crash before resolve_event leaves the event
    # UNresolved -> the next run retries the whole resolution. score_prediction /
    # void_prediction are idempotent (WHERE status='open'), so the retry is safe.
    #
    # A genuine resolution scores it (act -> scored, watch/skip -> observed); a
    # non-genuine outcome (invalid identity conflict / void) closes the open
    # prediction as `voided` - no Brier, but it leaves the opportunity surface so
    # an invalidated event stops showing as actionable. A None return means the
    # event has no open prediction (e.g. a news event that was never frozen, or
    # already resolved) - that is expected, not a failure, so we proceed to write
    # the outcome. A raised exception (DB failure) aborts BEFORE the outcome is
    # written, so the next run can retry.
    if status == "resolved":
        score_prediction(event_id, actual_outcome)
    else:
        void_prediction(event_id)

    updated = resolve_event(event_id, outcome, calibration=calibration)
    record = (updated or {}).get("record") or {}
    record_outcome(event_id, record.get("event_title", ""), outcome)
    return updated


def reconcile_predictions() -> int:
    """Heal orphaned predictions left by a crash mid-resolve.

    The resolve path writes the prediction (SQLite) and the event outcome (JSON)
    without a shared transaction. If the process died after the event outcome was
    written but before the prediction was scored (the pre-fix ordering, or any
    future regression), the event is "resolved" yet its prediction is still
    `open` - and auto_resolve skips resolved events, so that prediction would
    never be scored. This startup/per-run scan finds those orphans (event has an
    outcome, prediction still open) and applies the stored outcome: a genuine
    resolution scores it (act->scored, watch/skip->observed), a non-genuine one
    voids it. Idempotent (score/void are WHERE status='open'); returns how many
    it healed. Best-effort: a single failure is logged and skipped, never raises.
    """
    healed = 0
    for entry in list_all_events():
        record = entry.get("record") or {}
        outcome = record.get("outcome")
        if outcome is None:
            continue
        event_id = entry.get("event_id")
        if not event_id:
            continue
        pred = get_prediction(event_id)
        if pred is None or pred.get("status") != "open":
            continue  # never predicted, or already terminal - nothing to heal
        try:
            if outcome.get("status") == "resolved":
                actual = float(outcome.get("actual_outcome"))
                score_prediction(event_id, actual)
            else:
                void_prediction(event_id)
            healed += 1
            logger.warning(
                "reconcile: healed orphan prediction for resolved event %s "
                "(outcome status=%s)",
                event_id, outcome.get("status"),
            )
        except Exception as exc:
            logger.warning(
                "reconcile: failed to heal orphan prediction %s: %s",
                event_id, exc,
            )
    return healed


async def auto_resolve_events(resolved_limit: int = 200) -> dict[str, Any]:
    """Auto-resolve events whose questions match resolved prediction markets
    (Polymarket, Manifold, Kalshi).

    Fetches resolved markets from all sources concurrently, builds a question
    index, and for each unresolved local event whose question matches (exact
    normalized key or fuzzy overlap >= FUZZY_THRESHOLD), resolves it with a
    calibration snapshot (source = "auto_market"). Already-resolved events are
    skipped. Returns a summary; never raises (network / match failures
    degrade to fewer
    resolutions).
    """
    # Heal any orphans left by a prior mid-resolve crash before doing new work.
    reconciled = reconcile_predictions()
    from app.services.polymarket_history_service import (
        fetch_resolved_markets as fetch_polymarket_resolved,
    )
    from app.services.manifold_event_source import (
        fetch_resolved_markets as fetch_manifold_resolved,
    )
    from app.services.kalshi_event_source import (
        fetch_resolved_markets as fetch_kalshi_resolved,
    )

    # Pull resolved markets from every prediction-market source concurrently and
    # isolate a failing source, so the same real event can be auto-resolved by
    # whichever platform it came from (Polymarket-only missed Manifold/Kalshi
    # events entirely).
    sources = (
        ("Polymarket", fetch_polymarket_resolved),
        ("Manifold", fetch_manifold_resolved),
        ("Kalshi", fetch_kalshi_resolved),
    )
    fetched = await asyncio.gather(
        *(fetch(limit=resolved_limit) for _, fetch in sources),
        return_exceptions=True,
    )
    resolved_markets: list[dict[str, Any]] = []
    by_source: dict[str, int] = {}
    for (name, _), result in zip(sources, fetched):
        if isinstance(result, Exception):
            logger.warning("auto_resolve: %s resolved fetch failed: %s", name, result)
            continue
        by_source[name] = len(result)
        for market in result:
            market["_source_platform"] = name  # tag for link provenance
        resolved_markets.extend(result)

    if not resolved_markets:
        return {"status": "no_resolved_markets", "resolved_count": 0,
                "pending_count": 0, "invalid_count": 0,
                "checked_count": 0, "unresolved_events": _count_unresolved(),
                "matches": [], "by_source": by_source}

    index = build_index(resolved_markets)
    # Map a matched question back to its market record so we can persist the
    # contract identity on the link. Keyed by the same normalized question as
    # the index (later wins, matching build_index).
    market_by_key = {
        normalize(m.get("question", "")): m
        for m in resolved_markets if m.get("question")
    }
    # Contract-id index: the PRIMARY settlement path. An event already bound to a
    # verified contract is resolved the moment that contract id appears in the
    # resolved set - no dependence on question-text staying byte-identical between
    # freeze and settle. Text matching (market_by_key) is only the fallback for
    # events not yet bound to a contract.
    market_by_contract = {
        str(m.get("id") or m.get("contract_id")): m
        for m in resolved_markets
        if (m.get("id") or m.get("contract_id"))
    }
    resolved_count = 0
    pending_count = 0
    invalid_count = 0
    match_log: list[dict[str, Any]] = []

    # Scan EVERY stored event (unranked, unbounded), not just the top-200 by
    # value_score - otherwise low-value events would never be resolved and the
    # calibration aggregate (which reads all resolved events) would be silently
    # biased toward high-value events.
    entries = list_all_events()
    # Read the audit log once and group by event_id, instead of calling
    # history_for_event per event (which would re-read the whole file N times).
    histories = histories_by_event()
    for entry in entries:
        record = entry.get("record") or {}
        if record.get("outcome") is not None:
            continue  # already resolved
        event_id = entry.get("event_id")
        if not event_id:
            continue

        # PRIMARY path: if the event is already bound to a verified contract and
        # that contract is in the resolved set, settle directly by id - no text
        # match needed. This is what keeps a linked event from going stale just
        # because the market's question wording drifted since freeze.
        linked = get_verified_link(event_id)
        if linked and linked.get("contract_id"):
            settled = market_by_contract.get(str(linked["contract_id"]))
            if settled is not None:
                try:
                    await resolve_with_calibration(
                        event_id=event_id,
                        actual_outcome=settled.get("actual_outcome"),
                        confidence=1.0,
                        source="auto_market",
                        notes=f"contract match: {linked['contract_id']}",
                        snapshots=histories.get(event_id, []),
                    )
                except Exception as exc:
                    logger.warning(
                        "auto_resolve: failed to resolve linked event %s: %s",
                        event_id, exc,
                    )
                    continue
                resolved_count += 1
                match_log.append({
                    "event_id": event_id,
                    "event_title": (record.get("event_title") or "")[:80],
                    "matched_to": linked["contract_id"],
                    "result": "resolved_by_contract",
                })
                continue
            # Linked but its contract has not settled yet: do NOT fall through to
            # text matching (that could match a DIFFERENT market and trigger a
            # false divergence). Wait for the bound contract to settle.
            continue

        question = record.get("event_title") or ""
        if not question:
            # No title means nothing to match on; do NOT fall back to
            # event_summary (narrative text would produce garbage matches).
            continue
        match = find_match(question, index)
        if match is None:
            continue
        matched_question, actual_outcome, score = match
        market = market_by_key.get(normalize(matched_question)) or {}
        contract_id = str(market.get("id") or market.get("contract_id") or "")
        market_name = str(market.get("_source_platform", ""))
        verified = score >= settings.AUTO_VERIFY_THRESHOLD

        # This is the fallback path for an event NOT yet bound to a verified
        # contract (linked events settle by contract id above and never reach
        # here). The match binds the event to its contract for the first time;
        # there is no prior link to diverge from, so the old identity-conflict
        # check is unreachable here and has been removed - contract-first
        # settlement now provides the no-wrong-contract guarantee.
        upsert_link(
            event_id,
            market_name=market_name,
            contract_id=contract_id,
            market_question=matched_question,
            # Event-side resolution criteria (as our analysis understood it).
            # The matched market's OWN criteria is not in fetch_resolved_markets
            # yet (a source-adapter change); record what we have so the column
            # is meaningful rather than empty. See V2_ROADMAP M0 exit criteria.
            resolution_criteria=(record.get("semantics") or {}).get("resolution_criteria", ""),
            link_method="auto",
            link_confidence=score,
            verified=verified,
        )

        # Fail-closed gate: an unverified (fuzzy) link is recorded for human
        # review but never scored, so a fuzzy match cannot silently resolve an
        # event against the wrong outcome.
        if not verified:
            pending_count += 1
            match_log.append({
                "event_id": event_id,
                "event_title": question[:80],
                "matched_to": matched_question[:80],
                "match_score": round(score, 3),
                "result": "pending",
            })
            continue

        try:
            await resolve_with_calibration(
                event_id=event_id,
                actual_outcome=actual_outcome,
                confidence=1.0,
                source="auto_market",
                notes=f"matched: {matched_question[:100]}",
                snapshots=histories.get(event_id, []),
            )
        except Exception as exc:
            logger.warning(
                "auto_resolve: failed to resolve event %s: %s", event_id, exc
            )
            continue
        resolved_count += 1
        match_log.append({
            "event_id": event_id,
            "event_title": question[:80],
            "matched_to": matched_question[:80],
            "actual_outcome": actual_outcome,
            "match_score": round(score, 3),
            "result": "resolved",
        })

    unresolved_events = sum(
        1 for entry in entries
        if (entry.get("record") or {}).get("outcome") is None
    )
    return {
        "status": "ok",
        "resolved_count": resolved_count,
        "pending_count": pending_count,
        "invalid_count": invalid_count,
        "checked_count": len(resolved_markets),
        "unresolved_events": unresolved_events,
        "reconciled_count": reconciled,
        "matches": match_log,
        "by_source": by_source,
    }


def _count_unresolved() -> int:
    """Count stored events without an outcome (best-effort, for reporting).

    Unranked and unbounded so the count is accurate, not capped at the top-200
    by value_score like list_events.
    """
    return sum(
        1 for entry in list_all_events()
        if (entry.get("record") or {}).get("outcome") is None
    )
