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
  EventAuditSnapshot,
  EventMoverItem,
  TrendSummary,
} from "./generated-types";

// conclusion_challenge is written onto the record dict by
// conclusion_challenge_event_adapter.apply_event_challenge_result, but the
// Pydantic EventRecord does not declare it (extra="allow"), so the generator
// cannot emit it. Declared here as an intersection until the backend model
// gains the field.
export interface ConclusionChallengeResult {
  verdict?: string | null;
  required_action?: string | null;
  failed_checks?: Array<Record<string, unknown>>;
  warnings?: Array<Record<string, unknown>>;
  challenge_summary?: string | null;
  critic_notes?: Record<string, unknown> | null;
  confidence_adjustment?: Record<string, unknown> | null;
  attempt_count?: number | null;
}

export type EventRecord = BackendEventRecord & {
  conclusion_challenge?: ConclusionChallengeResult | null;
};
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
  category?: string;
  record: EventRecord;
  [k: string]: unknown;
}

// UI-only types: no backend Pydantic model exists for these. They are
// derived from API responses or computed in the frontend.

// HistorySnapshot and Trend used to live here as hand-written subsets of the
// /events/{id}/history payload. Audit 53.53 gave that payload named response
// models (EventAuditSnapshot / TrendSummary / EdgeTrajectorySummary), so both
// now read the generated contract instead of a 4-field and a 10-field copy of
// it. The frontend names are kept because the consumers and their fixtures --
// buildSeries, sparkSeries, TrendEdgeSummary -- spell them this way.
//
// Note the generated versions are *wider*, not narrower: 11 and 14 fields
// against 4 and 10, and the shape arrives through `res.json() as T`, so a
// divergent copy would have compiled cleanly either way.
export type HistorySnapshot = EventAuditSnapshot;
export type Trend = TrendSummary;

// Mover used to live here as a hand-written copy of the /events/movers element.
// It was *looser* than the producer -- `event_title` and `trend` were optional
// against a service that writes both unconditionally -- so it is retired to the
// generated element (audit 53.55). The frontend name is kept because
// adaptMover() and its call sites spell it this way.
export type Mover = EventMoverItem;

export interface SimilarEvent {
  event_id: string;
  event_title: string;
  event_title_zh?: string;
  similarity?: number;
  estimated_probability?: number;
  change?: number;
}
