# ADR-003: FastAPI over Flask

**Status**: Accepted  
**Date**: 2025-Q4 (retroactively documented 2026-06-20)

## Context

Need a Python web framework for the API server. Primary requirements: async I/O (for concurrent LLM/market/news API calls), OpenAPI documentation, type safety.

## Decision

FastAPI.

## Rationale

- **Native async/await**: `asyncio.gather()` for parallel external API calls (LLM + 3 markets + 5 news sources).
- **Auto OpenAPI**: `/docs` Swagger UI generated from Pydantic models — zero maintenance.
- **Pydantic integration**: Request/response validation shares models with the service layer.
- **Lifespan events**: Scheduler start/stop hooks cleanly via `@asynccontextmanager`.

## Consequences

- ✅ Async I/O throughput for the discovery pipeline.
- ✅ Free API documentation.
- ⚠️ Pydantic v2 migration required (from v1 in earlier versions).

## Alternatives Considered

- **Flask**: Sync-only, would need threads or `asyncio.run()` wrappers for external API calls. Less ergonomic.
- **Sanic**: Similar async capabilities but smaller ecosystem than FastAPI.
- **Litestar**: Emerging alternative but less mature at decision time.
