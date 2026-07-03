// Adapter layer for frontend type consumption.
//
// generated-types.ts is the source of truth (generated from Pydantic models
// by `python -m scripts.generate_types`). This file re-exports those types
// with frontend naming aliases and adds UI-only types that have no backend
// model.
//
// DO NOT hand-write fields that exist in the Pydantic models. If a field is
// missing, add it to the backend model and re-run the generator.

// Re-export all generated types
export type * from "./generated-types";

// Naming aliases: preserve existing frontend names that differ from backend
import type {
  EventRecord as BackendEventRecord,
  EvidenceProfile as BackendEvidenceProfile,
  EventSemantics as BackendEventSemantics,
} from "./generated-types";

export type EventRecord = BackendEventRecord;
export type EvidenceAggregate = BackendEvidenceProfile;
export type Semantics = BackendEventSemantics;

// TrackedEntry is NOT a pure alias for EventStoreEntry because the generated
// EventStoreEntry.record is { [k: string]: unknown } (backend dict[str, Any]).
// We keep record typed as EventRecord for frontend type safety. The backend's
// EventStoreEntry always includes a record (defaults to empty dict), so
// required is correct.
export interface TrackedEntry {
  event_id: string;
  first_seen?: string;
  last_updated?: string;
  record: EventRecord;
  [k: string]: unknown;
}

// UI-only types: no backend Pydantic model exists for these. They are
// derived from API responses or computed in the frontend.

export interface Trend {
  observations?: number;
  direction?: string;
  pattern?: string;
  net_change?: number;
  recent_change?: number;
  min_probability?: number;
  max_probability?: number;
  volatility?: number;
  latest_probability?: number;
  span_hours?: number | null;
}

export interface Mover {
  event_id: string;
  event_title?: string;
  event_title_zh?: string;
  trend?: Trend;
}

export interface HistorySnapshot {
  timestamp?: string;
  baseline?: number;
  estimated?: number;
  change?: number;
  direction?: string;
}

export interface SimilarEvent {
  event_id: string;
  event_title: string;
  event_title_zh?: string;
  similarity?: number;
  estimated_probability?: number;
  change?: number;
}
