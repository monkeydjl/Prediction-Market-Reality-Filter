"""
market_utils.py
===============
跨层共享的工具函数。
避免路由层 (api/routes) 被 services 或 scheduler 反向依赖。
"""
import math
from typing import Any


def safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def safe_int(value: Any, fallback: int = 0) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return int(number) if math.isfinite(number) else fallback


def clamp(value: float, lo: float, hi: float) -> float:
    number = safe_float(value, lo)
    return max(lo, min(hi, number))
