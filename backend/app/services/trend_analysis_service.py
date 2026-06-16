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

from datetime import datetime
from typing import Any

# Moves within +-2 points are "stable" - the same band probability_direction uses.
_STABLE_BAND = 2.0


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
