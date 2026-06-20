# ADR-002: Next.js Static Export (not SSR)

**Status**: Accepted  
**Date**: 2025-Q4 (retroactively documented 2026-06-20)

## Context

The frontend is a dashboard for event intelligence. Options: SSR with Next.js API routes, or static export served by FastAPI.

## Decision

Use Next.js `output: "export"` — static HTML/JS/CSS built at deploy time, served by FastAPI's `StaticFiles` mount.

## Rationale

- **Single origin**: No CORS issues between frontend and API — everything on `:8000`.
- **No Node.js in production**: FastAPI serves everything. No need for a Node server or Vercel.
- **Build-time data is sufficient**: The dashboard is read-only; all dynamic data comes from `/api/*` fetch calls at runtime.
- **Simpler deployment**: One process, one port.

## Consequences

- ❌ No SSR/ISR — the initial HTML payload is empty until JS hydrates.
- ❌ Frontend rebuild required for any static content change.
- ✅ No frontend server to maintain.

## Alternatives Considered

- **Next.js SSR with Node server**: Added operational complexity (two processes, two ports) without benefit for a read-only dashboard.
- **Vite SPA**: Would achieve the same result but Next.js has better ecosystem support.
