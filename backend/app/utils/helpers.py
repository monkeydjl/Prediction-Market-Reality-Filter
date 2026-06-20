from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp01(value: Any) -> float:
    """Clamp a numeric value to [0.0, 1.0]. Non-numeric input returns 0.0."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
