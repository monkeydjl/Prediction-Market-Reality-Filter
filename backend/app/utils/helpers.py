from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_isoformat(value: datetime | None) -> str | None:
    """Serialize a stored datetime as ISO 8601 **with an explicit offset**.

    SQLite hands ``DateTime`` columns back naive -- including the ones declared
    ``timezone=True`` -- so a bare ``.isoformat()`` produces
    ``2026-06-20T12:00:00`` with no marker, and a browser's ``new Date(...)``
    reads that as *local* time. In UTC+8 that is an eight-hour error with no
    symptom other than a wrong clock (audit 53.171 found the first site, 53.172
    the rest).

    Naive values are assumed to be UTC, which is what every producer in this
    codebase writes (``datetime.now(timezone.utc)``). Aware values are
    **converted** rather than relabelled, so a driver that does return an offset
    (Postgres, MySQL) cannot be mislabelled the way ``.replace(tzinfo=...)``
    would mislabel it.

    Returns ``None`` unchanged: "no timestamp" and "midnight UTC" are different
    facts, and this function exists to stop them being confused.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def clamp01(value: Any) -> float:
    """Clamp a numeric value to [0.0, 1.0]. Non-numeric input returns 0.0."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
