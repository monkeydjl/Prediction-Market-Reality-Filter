"""
Daily intelligence digest builder: one payload that answers "what actually
moved today?" from the event audit log and the event store.

Design notes:
- The day's move is close-vs-previous-close (the last estimate before the
  day opens the baseline), NOT the all-time trajectory. A quiet day on a
  long-running event must not rank above a real intraday move.
- Ranking is by |day_net| descending with a deterministic tiebreak
  (event_id ascending) so the digest is reproducible.
- The all-time net change rides along as context only.
- Outcome snapshots (kind="outcome", estimated=None) never enter the math.
- MATERIALITY is inclusive on both sides: a move of exactly +/-0.5 is a mover
  *and* is labelled rising/falling, so the mover table can never show a row
  whose direction badge reads "stable".
- The durable event store is read only when there is at least one event to
  title, so a day with no in-window snapshots skips that parse entirely.
- Everything is injectable (store / history loader / now) so callers and
  tests stay deterministic; defaults read the real stores.
"""

from __future__ import annotations

from datetime import date as _date, datetime, timedelta, timezone
from typing import Any, Callable

from app.services import event_audit_service
from app.memory.event_store import list_all_events

MATERIALITY = 0.5  # moves at or above this are movers; below it they are quiet
DEFAULT_LIMIT = 8

_ZONE = timezone.utc


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO timestamp; None when missing/unusable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_ZONE)
    return ts.astimezone(_ZONE)


def _day_window(
    date: str | _date | None, now: datetime
) -> tuple[datetime, datetime]:
    """UTC ``[start, end)`` window for a day (default: today in UTC).

    Accepts a ``datetime.date`` (what the API layer validates to), an ISO
    ``YYYY-MM-DD`` string (kept for direct callers), or ``None``. An
    unparseable string still raises ``ValueError``; the API never lets user
    input reach that branch because the route declares ``date: datetime.date``.
    """
    if isinstance(date, datetime):
        day = date if date.tzinfo else date.replace(tzinfo=_ZONE)
        day = day.astimezone(_ZONE)
    elif isinstance(date, _date):
        day = datetime(date.year, date.month, date.day, tzinfo=_ZONE)
    elif date:
        day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=_ZONE)
    else:
        day = now.astimezone(_ZONE)
    day = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day + timedelta(days=1)


def _day_stats(
    histories: dict[str, list[dict[str, Any]]],
    window: tuple[datetime, datetime],
) -> list[dict[str, Any]]:
    """Per-event day stats: open, close, day_net, day_observations."""
    start, end = window
    stats = []
    for event_id, snapshots in histories.items():
        usable = []  # (ts, snap) sorted, probability snapshots only
        for snap in snapshots or []:
            if snap.get("kind") == "outcome":
                continue
            ts = _parse_ts(snap.get("timestamp"))
            if ts is None:
                continue
            usable.append((ts, snap))
        if not usable:
            continue
        usable.sort(key=lambda pair: pair[0])
        in_day = [(ts, snap) for ts, snap in usable if start <= ts < end]
        if not in_day:
            continue
        pre_day = [(ts, snap) for ts, snap in usable if ts < start]
        if pre_day:
            open_ts, open_snap = pre_day[-1]
            open_source = "previous_close"
        else:
            open_ts, open_snap = in_day[0]
            open_source = "first_in_day"
        close_ts, close_snap = in_day[-1]
        open_est = open_snap.get("estimated")
        close_est = close_snap.get("estimated")
        if not isinstance(open_est, (int, float)) or not isinstance(close_est, (int, float)):
            continue
        stats.append({
            "event_id": event_id,
            "open": float(open_est),
            "close": float(close_est),
            "day_net": round(float(close_est) - float(open_est), 2),
            "day_observations": len(in_day),
            "close_ts": close_ts,
            "open_ts": open_ts,
            "open_source": open_source,
            "close_title": close_snap.get("event_title"),
        })
    return stats


def _display_title(entry: dict | None, fallback: str | None) -> str | None:
    """Chinese title first, then the record title, then the audit snapshot's."""
    if entry:
        record = entry.get("record") or {}
        for key in ("event_title_zh", "event_title"):
            value = record.get(key)
            if value:
                return value
    return fallback


def _movement(day_net: float) -> str:
    """Direction label.

    Inclusive at the threshold so it agrees with the mover cut: a move of
    exactly ``MATERIALITY`` is a mover *and* is directional, never a mover
    row whose badge reads "stable".
    """
    if day_net >= MATERIALITY:
        return "rising"
    if day_net <= -MATERIALITY:
        return "falling"
    return "stable"


def build_daily_digest(
    limit: int = DEFAULT_LIMIT,
    date: str | _date | None = None,
    now: datetime | None = None,
    store: list[dict[str, Any]] | None = None,
    history_loader: Callable[[], dict[str, list[dict[str, Any]]]] | None = None,
) -> dict[str, Any]:
    """Build the daily digest payload (see module docstring)."""
    now = now or datetime.now(tz=_ZONE)
    window = _day_window(date, now)
    histories = (history_loader or event_audit_service.histories_by_event)()
    stats = _day_stats(histories, window)

    # Titles come from the durable store, but a day with nothing in-window has
    # nothing to title: skip that read rather than pay for it on every poll.
    if stats:
        entries = list_all_events() if store is None else store
        by_id = {e.get("event_id"): e for e in entries}
    else:
        by_id = {}

    movers: list[dict[str, Any]] = []
    new_event_ids: list[str] = []
    quiet_event_ids: list[str] = []
    for stat in stats:
        event_id = stat["event_id"]
        title = _display_title(by_id.get(event_id), stat.get("close_title"))
        all_time = histories.get(event_id) or []
        numeric: list[Any] = [
            s.get("estimated") for s in all_time
            if s.get("kind") != "outcome" and isinstance(s.get("estimated"), (int, float))
        ]
        if len(numeric) >= 2:
            all_time_net = round(float(numeric[-1]) - float(numeric[0]), 2)
        else:
            all_time_net = 0.0
        item = {
            "event_id": event_id,
            "event_title": title,
            "movement": _movement(stat["day_net"]),
            "day_net": stat["day_net"],
            "open": stat["open"],
            "close": stat["close"],
            "open_source": stat["open_source"],
            "day_observations": stat["day_observations"],
            "all_time_net_change": all_time_net,
            "close_ts": stat["close_ts"].isoformat(),
        }
        has_previous_close = stat["open_source"] == "previous_close"
        if abs(stat["day_net"]) >= MATERIALITY and (
            has_previous_close or stat["day_observations"] >= 2
        ):
            # A real move: either measured against the previous digest close,
            # or an intraday move on an event first seen today.
            movers.append(item)
        elif not has_previous_close:
            new_event_ids.append(event_id)
        else:
            quiet_event_ids.append(event_id)

    movers.sort(key=lambda m: (-abs(m["day_net"]), m["event_id"]))
    total_movers = len(movers)
    movers = movers[: max(0, limit)]

    headline = None
    if movers:
        top = movers[0]
        headline = {
            "event_id": top["event_id"],
            "event_title": top["event_title"],
            "movement": top["movement"],
            "day_net": top["day_net"],
            "close": top["close"],
        }

    return {
        "date": window[0].strftime("%Y-%m-%d"),
        "generated_at": now.astimezone(_ZONE).isoformat(),
        "count": len(movers),
        "headline": headline,
        "movers": movers,
        # "empty" means the day produced nothing digestable at all. A day whose
        # events were merely quiet is NOT empty — it carries quiet_event_ids
        # summarising it, so the UI renders that instead of an empty-state
        # banner sitting next to a populated summary.
        "unchanged": {
            "quiet_event_ids": quiet_event_ids,
            "new_event_ids": new_event_ids,
        },
        "empty": total_movers == 0 and not new_event_ids and not quiet_event_ids,
    }
