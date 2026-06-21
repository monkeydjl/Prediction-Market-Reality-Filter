"""trend_analysis_service.py
=========================
Probability-trend analysis over an event's audit snapshots.

event_audit_service records one probability snapshot per scan (a timestamp plus
the estimated probability, written oldest-to-newest). This module summarizes how
an event's probability has moved across those snapshots: the net direction, the
range it covered, how much it bounced (volatility), the most recent move, and a
shape classification.

Pure and deterministic: snapshots in, summary dict out. No I/O, no LLM. Parsing
is defensive - snapshots with a missing or non-numeric `estimated` are skipped.

Event vocabulary only (no trading terms). Net direction is rising / falling /
stable, mirroring event_intelligence_service.probability_direction.
"""

from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

# Moves within +-2 points are "stable" - the same band probability_direction uses.
_STABLE_BAND = 2.0
_EDGE_CLASS_ORDER = {
    "fresh": 0,
    "decaying": 1,
    "stale": 2,
    "closed": 3,
    "no_data": 4,
}


def analyze_trend(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the probability trend across audit snapshots (oldest-to-newest).

    Each snapshot is an event_audit line; only `estimated` and `timestamp` are
    read. Snapshots whose `estimated` is missing or non-numeric are skipped.
    Returns a summary with zero/None fields and pattern "insufficient_data" when
    there are no usable points.
    """
    estimates: list[float] = []
    times: list[Any] = []
    for snap in snapshots:
        try:
            value = float(snap.get("estimated"))
        except (TypeError, ValueError):
            continue
        estimates.append(value)
        times.append(snap.get("timestamp"))

    count = len(estimates)
    if count == 0:
        return _empty_trend()

    first = estimates[0]
    latest = estimates[-1]
    net_change = round(latest - first, 2)
    low = min(estimates)
    high = max(estimates)
    value_range = round(high - low, 2)

    if count >= 2:
        steps = [abs(estimates[i] - estimates[i - 1]) for i in range(1, count)]
        volatility = round(sum(steps) / len(steps), 2)
        recent_change = round(estimates[-1] - estimates[-2], 2)
    else:
        volatility = 0.0
        recent_change = 0.0

    return {
        "observations": count,
        "direction": _direction(net_change),
        "first_probability": round(first, 2),
        "latest_probability": round(latest, 2),
        "net_change": net_change,
        "min_probability": round(low, 2),
        "max_probability": round(high, 2),
        "range": value_range,
        "volatility": volatility,
        "recent_change": recent_change,
        "pattern": _pattern(count, net_change, value_range, recent_change),
        "first_seen": times[0],
        "last_seen": times[-1],
        "span_hours": _span_hours(times[0], times[-1]),
    }


def _empty_trend() -> dict[str, Any]:
    return {
        "observations": 0,
        "direction": "stable",
        "first_probability": None,
        "latest_probability": None,
        "net_change": 0.0,
        "min_probability": None,
        "max_probability": None,
        "range": 0.0,
        "volatility": 0.0,
        "recent_change": 0.0,
        "pattern": "insufficient_data",
        "first_seen": None,
        "last_seen": None,
        "span_hours": None,
    }


def _direction(net_change: float) -> str:
    if net_change >= _STABLE_BAND:
        return "rising"
    if net_change <= -_STABLE_BAND:
        return "falling"
    return "stable"


def _pattern(
    count: int,
    net_change: float,
    value_range: float,
    recent_change: float,
) -> str:
    if count < 2:
        return "insufficient_data"
    if value_range <= _STABLE_BAND:
        return "stable"
    if abs(net_change) >= _STABLE_BAND:
        # Material net move. If the latest step is a material reversal of that
        # net direction, call it reversing; otherwise it is a steady trend.
        if abs(recent_change) >= _STABLE_BAND and (recent_change > 0) != (net_change > 0):
            return "reversing"
        return "trending_up" if net_change > 0 else "trending_down"
    # Large swings but little net movement: the probability is bouncing around.
    return "volatile"


def _span_hours(start: Any, end: Any) -> float | None:
    start_dt = _parse_timestamp(start)
    end_dt = _parse_timestamp(end)
    if start_dt is None or end_dt is None:
        return None
    return round((end_dt - start_dt).total_seconds() / 3600.0, 2)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def rank_movers(
    histories: dict[str, list[dict[str, Any]]],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Rank events by absolute net probability change over their history.

    `histories` maps event_id -> snapshots (oldest-to-newest), as returned by
    event_audit_service.histories_by_event. Each event is summarized with
    analyze_trend; only events with at least two observations (an actual move)
    are ranked. Returns the top `limit` by abs(net_change), descending.
    """
    movers: list[dict[str, Any]] = []
    for event_id, snapshots in histories.items():
        trend = analyze_trend(snapshots)
        if trend["observations"] < 2:
            continue
        movers.append({
            "event_id": event_id,
            "event_title": _latest_title(snapshots),
            "trend": trend,
        })
    movers.sort(key=lambda mover: abs(mover["trend"]["net_change"]), reverse=True)
    return movers[:limit]


def _latest_title(snapshots: list[dict[str, Any]]) -> str:
    for snapshot in reversed(snapshots):
        title = snapshot.get("event_title")
        if title:
            return str(title)
    return ""


def analyze_edge_trajectory(
    snapshots: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize how an event's edge (AI - market) has moved over its snapshots.

    Each audit snapshot carries `baseline` (market price) and `estimated` (AI
    probability); edge = estimated - baseline per snapshot (oldest-to-newest).
    Snapshots missing either value are skipped (outcome markers have estimated
    None, so they drop out). Returns the edge series summary plus a freshness
    classification:

      no_data  - no usable snapshots
      stale    - latest snapshot older than EDGE_STALE_HOURS (info is old)
      closed   - latest edge below the materiality floor (DECISION_WATCH_EDGE)
      fresh    - material edge holding near its peak (|latest| >= 0.7*|peak|)
      decaying - material edge that has shrunk from its peak

    `now` defaults to the current UTC time; pass it for deterministic tests.
    """
    edges: list[float] = []
    times: list[Any] = []
    for snap in snapshots:
        try:
            estimated = float(snap.get("estimated"))
            baseline = float(snap.get("baseline"))
        except (TypeError, ValueError):
            continue
        edges.append(estimated - baseline)
        times.append(snap.get("timestamp"))

    count = len(edges)
    if count == 0:
        return _empty_edge()

    first = edges[0]
    latest = edges[-1]
    peak = max(edges, key=abs)  # signed edge with the largest magnitude
    recent_edge_change = round(edges[-1] - edges[-2], 2) if count >= 2 else 0.0
    age_hours = _age_hours(times[-1], now)

    return {
        "observations": count,
        "latest_edge": round(latest, 2),
        "first_edge": round(first, 2),
        "peak_edge": round(peak, 2),
        "net_edge_change": round(latest - first, 2),
        "recent_edge_change": recent_edge_change,
        "first_seen": times[0],
        "last_seen": times[-1],
        "span_hours": _span_hours(times[0], times[-1]),
        "age_hours": age_hours,
        "freshness_band": _freshness_band(age_hours),
        "classification": _classify_edge(latest, peak, age_hours),
    }


def _empty_edge() -> dict[str, Any]:
    return {
        "observations": 0,
        "latest_edge": None,
        "first_edge": None,
        "peak_edge": None,
        "net_edge_change": 0.0,
        "recent_edge_change": 0.0,
        "first_seen": None,
        "last_seen": None,
        "span_hours": None,
        "age_hours": None,
        "freshness_band": "UNKNOWN",
        "classification": "no_data",
    }


def _age_hours(last_seen: Any, now: datetime | None) -> float | None:
    last_dt = _parse_timestamp(last_seen)
    if last_dt is None:
        return None
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return round((now - last_dt).total_seconds() / 3600.0, 2)


def _freshness_band(age_hours: float | None) -> str:
    if age_hours is None:
        return "UNKNOWN"
    if age_hours <= 24:
        return "FRESH"
    if age_hours <= 72:
        return "WARM"
    if age_hours <= 168:
        return "COOL"
    return "STALE"


def _classify_edge(latest: float, peak: float, age_hours: float | None) -> str:
    if age_hours is not None and age_hours > settings.EDGE_STALE_HOURS:
        return "stale"
    if abs(latest) < settings.DECISION_WATCH_EDGE:
        return "closed"
    if abs(peak) > 0 and abs(latest) >= 0.7 * abs(peak):
        return "fresh"
    return "decaying"


def edge_series(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return timestamped AI-vs-market edge points for compact frontend charts."""
    points: list[dict[str, Any]] = []
    for snap in snapshots:
        try:
            estimated = float(snap.get("estimated"))
            baseline = float(snap.get("baseline"))
        except (TypeError, ValueError):
            continue
        points.append({
            "timestamp": snap.get("timestamp"),
            "estimated": round(estimated, 2),
            "baseline": round(baseline, 2),
            "edge": round(estimated - baseline, 2),
        })
    return points


def list_edge_trajectories(
    histories: dict[str, list[dict[str, Any]]],
    limit: int = 50,
    *,
    classification: str = "all",
    include_series: bool = False,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """List edge trajectories across freshness classes for monitoring views.

    `classification="all"` skips no-data rows and returns fresh, decaying,
    stale, and closed edges grouped by a stable sort order. A concrete
    classification returns only that class.
    """
    items: list[dict[str, Any]] = []
    for event_id, snapshots in histories.items():
        edge = analyze_edge_trajectory(snapshots, now=now)
        edge_class = edge["classification"]
        if classification == "all":
            if edge_class == "no_data":
                continue
        elif edge_class != classification:
            continue
        item = {
            "event_id": event_id,
            "event_title": _latest_title(snapshots),
            "edge": edge,
        }
        if include_series:
            item["series"] = edge_series(snapshots)
        items.append(item)

    items.sort(
        key=lambda item: (
            _EDGE_CLASS_ORDER.get(item["edge"]["classification"], 99),
            -abs(item["edge"]["latest_edge"] or 0.0),
            item["event_id"],
        )
    )
    return items[:limit]


def rank_fresh_edges(
    histories: dict[str, list[dict[str, Any]]],
    limit: int = 10,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Rank events by a live (fresh) edge - the 'catch edges when real' surface.

    `histories` maps event_id -> snapshots (oldest-to-newest). Each event's edge
    trajectory is classified; only those whose edge is currently `fresh` (recent
    and holding near its peak) are kept, ranked by |latest_edge| descending.
    """
    fresh: list[dict[str, Any]] = []
    for event_id, snapshots in histories.items():
        edge = analyze_edge_trajectory(snapshots, now=now)
        if edge["classification"] != "fresh":
            continue
        fresh.append({
            "event_id": event_id,
            "event_title": _latest_title(snapshots),
            "edge": edge,
        })
    fresh.sort(key=lambda item: abs(item["edge"]["latest_edge"]), reverse=True)
    return fresh[:limit]
