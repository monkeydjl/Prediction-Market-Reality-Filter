# ADR-001: JSON File Store for Event Records

**Status**: Accepted  
**Date**: 2025-Q4 (retroactively documented 2026-06-20)

## Context

The event store needed a persistence backend in v0.1. Options considered: SQLite, PostgreSQL, JSON file.

## Decision

Use a JSON file (`event_store.json`) with atomic writes.

## Rationale

- **Zero operational dependencies**: No database server to manage or migrate.
- **Atomic writes via tempfile**: `write_json_atomic` writes to a temp file then `os.replace` — crash-safe by construction.
- **Single-writer assumption**: The system runs as a single process, so file-level locking (RLock) is sufficient.
- **Human-readable**: Operators can inspect `event_store.json` directly during debugging.
- **Simplicity over scalability**: v0.x is a validation loop; scale concerns are premature.

## Consequences

- ❌ Full-table scans required for queries (e.g. filtering by category).
- ❌ No concurrent writers (acceptable for single-process deployment).
- ❌ File growth unbounded without compaction (mitigated by audit log compaction).

## Alternatives Considered

- **SQLite**: Would require schema migrations. JSON file is simpler for a key-value doc store with one primary access pattern (get by event_id).
- **PostgreSQL**: Overkill for single-node v0.x deployment.

## Migration Path

When multi-instance deployment is needed (v1.0+), migrate event_store to PostgreSQL, using the existing Pydantic models as the schema definition.
