from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_utc(value: datetime | None) -> datetime | None:
    """Return ``value`` as an aware UTC datetime, or ``None`` unchanged.

    SQLite hands ``DateTime`` columns back naive -- including the ones declared
    ``timezone=True`` -- so a value read out of a row carries no offset even
    though every producer in this codebase writes
    ``datetime.now(timezone.utc)``. A naive value is therefore **assumed** to be
    UTC, which is the assumption the whole read path already rests on.

    Aware values are **converted**, not relabelled. ``replace(tzinfo=...)`` keeps
    the wall clock and moves the instant, so a driver that does return an offset
    (Postgres, MySQL) would be mislabelled by the offset's worth and report
    nothing -- a `+08:00` 21:00 would become a `+00:00` 21:00, eight hours late.

    Returns ``None`` unchanged: "no timestamp" and "midnight UTC" are different
    facts, and this function exists to stop them being confused.

    **Single owner** (audit 53.183). ``utc_isoformat`` serialises this function's
    result, and the row->dict converters in ``app/kernel`` return it directly, so
    a dict read out of a row is already aware. That placement is the point: a
    value that reaches the wire through pydantic or ``jsonable_encoder`` has no
    ``.isoformat()`` call site to audit, so the offset cannot be enforced by
    looking for call sites -- only by making the value aware before it leaves
    the store.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_isoformat(value: datetime | None) -> str | None:
    """Serialize a stored datetime as ISO 8601 **with an explicit offset**.

    A bare ``.isoformat()`` on a naive value produces ``2026-06-20T12:00:00``
    with no marker, and a browser's ``new Date(...)`` reads that as *local* time.
    In UTC+8 that is an eight-hour error with no symptom other than a wrong clock
    (audit 53.171 found the first site, 53.172 the rest).

    The naive/aware decision lives in :func:`as_utc` rather than here, so a store
    that has to keep the value a ``datetime`` and one that has to serialise it do
    not each grow their own copy of the rule.
    """
    aware = as_utc(value)
    return None if aware is None else aware.isoformat()


def clamp01(value: Any) -> float:
    """Clamp a numeric value to [0.0, 1.0]. Non-numeric input returns 0.0."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
