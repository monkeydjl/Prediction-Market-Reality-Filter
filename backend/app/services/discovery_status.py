"""discovery_status.py
=====================
Tracks real-time progress of the event discovery pipeline so the frontend
can surface detailed feedback about what is happening at each step.

Thread-safe: uses asyncio primitives so concurrent polls don't race.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

STATUS: dict[str, Any] = {
    "phase": "idle",
    "started_at": None,
    "finished_at": None,
    "limit": 0,
    "sources": {},          # {source_name: {status, candidates, error}}
    "candidates_total": 0,
    "analyzed": 0,
    "total_to_analyze": 0,
    "results": 0,
    "errors": [],           # [{event, error}]
    "message": "等待开始",
    "elapsed_ms": 0,
}

_lock = asyncio.Lock()


async def reset(limit: int = 0) -> None:
    async with _lock:
        STATUS.update({
            "phase": "collecting",
            "started_at": time.time(),
            "finished_at": None,
            "limit": limit,
            "sources": {},
            "candidates_total": 0,
            "analyzed": 0,
            "total_to_analyze": 0,
            "results": 0,
            "errors": [],
            "message": f"开始发现，目标 {limit} 个事件…",
            "elapsed_ms": 0,
        })


async def set_phase(phase: str, message: str = "") -> None:
    async with _lock:
        STATUS["phase"] = phase
        STATUS["message"] = message
        STATUS["elapsed_ms"] = _elapsed()


async def source_start(source: str) -> None:
    async with _lock:
        STATUS["sources"][source] = {"status": "fetching", "candidates": 0, "error": None}
        STATUS["elapsed_ms"] = _elapsed()


async def source_done(source: str, candidates: int, error: str | None = None) -> None:
    async with _lock:
        if error:
            STATUS["sources"][source] = {"status": "failed", "candidates": 0, "error": error}
        else:
            STATUS["sources"][source] = {"status": "ok", "candidates": candidates, "error": None}
        STATUS["elapsed_ms"] = _elapsed()


async def set_candidates(total: int, to_analyze: int) -> None:
    async with _lock:
        STATUS["candidates_total"] = total
        STATUS["total_to_analyze"] = to_analyze
        STATUS["phase"] = "analyzing"
        STATUS["message"] = f"已收集 {total} 个候选，待分析 {to_analyze} 个…"
        STATUS["elapsed_ms"] = _elapsed()


async def event_analyzed(question: str = "", success: bool = True, error: str = "") -> None:
    async with _lock:
        STATUS["analyzed"] += 1
        if not success:
            STATUS["errors"].append({
                "event": question[:80],
                "error": error[:200],
            })
        done = STATUS["analyzed"]
        total = STATUS["total_to_analyze"]
        STATUS["message"] = f"分析中 {done}/{total}…"
        STATUS["elapsed_ms"] = _elapsed()


async def event_saved(count: int) -> None:
    async with _lock:
        STATUS["results"] = count
        STATUS["elapsed_ms"] = _elapsed()


async def done(results: int, errors: int) -> None:
    async with _lock:
        STATUS["phase"] = "done"
        STATUS["finished_at"] = time.time()
        STATUS["results"] = results
        STATUS["elapsed_ms"] = _elapsed()
        if results == 0 and errors == 0:
            STATUS["message"] = "完成，但未产生事件 — 可能 LLM 不可用或数据源无新事件"
        elif results == 0:
            STATUS["message"] = f"完成，{errors} 个失败，未产生事件"
        else:
            STATUS["message"] = f"完成，产生 {results} 个事件"


async def fail(reason: str) -> None:
    async with _lock:
        STATUS["phase"] = "failed"
        STATUS["finished_at"] = time.time()
        STATUS["message"] = reason
        STATUS["elapsed_ms"] = _elapsed()


def snapshot() -> dict[str, Any]:
    return STATUS.copy()


def _elapsed() -> float:
    s = STATUS.get("started_at")
    return round((time.time() - s) * 1000, 0) if s else 0
